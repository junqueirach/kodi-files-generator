"""Kodi Files Generator — writers (no Tk).

Episode NFO (Kodi, multi-episode), episode XML (MediaBrowser / Media Center Master <Item>), tvshow.nfo, series.xml,
season.nfo, the legacy template filler, read-back validation and atomic writes. Only fields with a value are
written (B11, F18); text is cleaned of characters XML does not allow (B12).
"""
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime

from kfg_core import clean_text

NFO_ENCODING = "utf-8-sig"      # Kodi tools write NFO with a BOM (as R4 did)
XML_ENCODING = "utf-8"


# ═══════════════════════════════════════════════════════════════════════
# SMALL XML HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return clean_text(v)


def add(parent, tag, value=None, attrs=None, always=False):
    """Append <tag>value</tag> when value is not empty (or always=True). Returns the element or None."""
    text = _txt(value)
    if not text and not always and not attrs:
        return None
    el = ET.SubElement(parent, tag, {k: _txt(v) for k, v in (attrs or {}).items() if _txt(v)})
    if text:
        el.text = text
    return el


def add_many(parent, tag, values):
    for v in values or []:
        add(parent, tag, v)


def to_text(root, declaration=True) -> str:
    ET.indent(root, space="\t")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' if declaration else "") + body + "\n"


def first_sentence(text: str, max_len=200) -> str:
    t = clean_text(text).split("\n")[0]
    m = re.match(r"^(.{10,}?[.!?])(\s|$)", t)
    s = m.group(1) if m else t
    return s if len(s) <= max_len else s[:max_len - 1].rstrip() + "…"


def _mpaa_kodi(value: str, country: str = "") -> str:
    """'16' + country 'Brazil' → 'Brazil:16' (Kodi shows the text as it is); a full rating like 'TV-MA' unchanged."""
    v = _txt(value)
    if v and country and re.fullmatch(r"\d{1,2}|L|ER|AL", v):
        return f"{country}:{v}"
    return v


def _people(value) -> list:
    out = []
    for p in value or []:
        if isinstance(p, (list, tuple)):
            p = p[0]
        p = _txt(p)
        if p and p not in out:
            out.append(p)
    return out


def _actors(value) -> list:
    """[(name, role, thumb)] from tuples or names, no duplicates by name."""
    out, seen = [], set()
    for a in value or []:
        if isinstance(a, (list, tuple)):
            name, role, thumb = (list(a) + ["", "", ""])[:3]
        else:
            name, role, thumb = a, "", ""
        name = _txt(name)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append((name, _txt(role), _txt(thumb)))
    return out


# ═══════════════════════════════════════════════════════════════════════
# EPISODE NFO (Kodi)
# ═══════════════════════════════════════════════════════════════════════

def episode_nfo(episodes, series, video=None, opts=None, rules=None) -> str:
    """One <episodedetails> per episode (more than one = Kodi multi-episode file)."""
    opts = opts or {}
    parts = []
    for i, ep in enumerate(episodes):
        root = _episode_details(ep, series, video if i == 0 else video, opts, rules)
        parts.append(to_text(root, declaration=(i == 0)))
    return "".join(parts)


def _episode_details(ep, series, video, opts, rules):
    g = lambda f: ep.get(f, rules)
    r = ET.Element("episodedetails")
    title = g("title") or f"Episode {ep.episode}"
    add(r, "title", title)
    eng = ep.get("title_en", rules)
    if eng and _txt(eng) != _txt(title) and opts.get("originaltitle", True):
        add(r, "originaltitle", eng)
    add(r, "showtitle", series.get("title", rules) if series else "")
    if opts.get("ratings", True):
        rat = ET.Element("ratings")
        first = True
        for src in ("omdb", "tvdb", "tmdb", "tvmaze"):
            if src in ep.ratings and ep.ratings[src][0]:
                val, votes = ep.ratings[src]
                rr = ET.SubElement(rat, "rating", {"name": {"omdb": "imdb", "tvdb": "tvdb"}.get(src, src), "max": "10",
                                                     "default": "true" if first else "false"})
                add(rr, "value", f"{float(val):.1f}")
                add(rr, "votes", votes)
                first = False
        if len(rat):
            r.append(rat)
    add(r, "season", ep.season, always=True)
    add(r, "episode", ep.episode, always=True)
    if ep.season == 0 and ep.display:
        ds, de = ep.display
        add(r, "displayseason", ds)
        add(r, "displayepisode", de if de is not None else 4096)
    plot = g("plot")
    add(r, "plot", plot)
    if plot and opts.get("outline", True):
        add(r, "outline", first_sentence(plot))
    rt = g("runtime")
    add(r, "runtime", rt)
    aired = g("aired")
    add(r, "aired", aired)
    add(r, "premiered", aired)
    if aired and re.match(r"^\d{4}", aired):
        add(r, "year", aired[:4])
    add(r, "mpaa", _mpaa_kodi(g("mpaa"), opts.get("rating_country", "")))
    ids = [("tvdb", g("tvdb_id")), ("imdb", g("imdb_id")), ("tvmaze", g("tvmaze_id")), ("tmdb", g("tmdb_id"))]
    first = True
    for kind, val in ids:
        if _txt(val):
            add(r, "uniqueid", val, {"type": kind, "default": "true" if first else "false"})
            first = False
    if opts.get("crew", True):
        add_many(r, "credits", _people(g("writers")))
        add_many(r, "director", _people(g("directors")))
    add(r, "studio", g("network") or (series.get("network", rules) if series else ""))
    if opts.get("cast", True):
        for n, (name, role, thumb) in enumerate(_actors(g("guests"))):
            a = ET.SubElement(r, "actor")
            add(a, "name", name)
            add(a, "role", role)
            add(a, "order", n)
            if opts.get("image_urls", True):
                add(a, "thumb", thumb)
    if opts.get("image_urls", True):
        url = next((u for u, _ in ep.images if str(u).startswith("http")), "")
        add(r, "thumb", url, {"aspect": "thumb"} if url else None)
    add(r, "dateadded", opts.get("dateadded") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if video and video.get("streams") and opts.get("techinfo", True):
        r.append(_streamdetails(video))
    return r


def _streamdetails(video: dict):
    fi = ET.Element("fileinfo")
    sd = ET.SubElement(fi, "streamdetails")
    v = video.get("video") or {}
    if v:
        ve = ET.SubElement(sd, "video")
        add(ve, "codec", v.get("codec"))
        add(ve, "aspect", v.get("aspect"))
        add(ve, "width", v.get("width"))
        add(ve, "height", v.get("height"))
        add(ve, "durationinseconds", video.get("duration_s"))
        add(ve, "scantype", v.get("scantype"))
        add(ve, "hdrtype", v.get("hdr"))
    for a in video.get("audio") or []:
        ae = ET.SubElement(sd, "audio")
        add(ae, "codec", a.get("codec"))
        add(ae, "language", a.get("language"))
        add(ae, "channels", a.get("channels"))
    for s in video.get("subtitles") or []:
        se = ET.SubElement(sd, "subtitle")
        add(se, "language", s.get("language"))
    return fi


# ═══════════════════════════════════════════════════════════════════════
# EPISODE XML (MediaBrowser / Media Center Master <Item>, field order of the real files)
# ═══════════════════════════════════════════════════════════════════════

def episode_xml(episodes, series, video=None, image_name="", opts=None, rules=None) -> str:
    opts = opts or {}
    ep = episodes[0]
    g = lambda f: ep.get(f, rules)
    r = ET.Element("Item")
    add(r, "ID", ep.absolute or ep.episode)
    add(r, "EpisodeID", g("tvdb_id"))
    add(r, "EpisodeNumber", ep.episode, always=True)
    if len(episodes) > 1:
        add(r, "EpisodeNumberEnd", episodes[-1].episode)
    add(r, "SeasonNumber", ep.season, always=True)
    add(r, "seriesid", series.get("tvdb_id", rules) if series else "")
    add(r, "Added", opts.get("added") or datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    add(r, "FirstAired", g("aired"))
    if opts.get("crew", True):
        add(r, "Director", "|".join(_people(g("directors"))))
    if opts.get("cast", True):
        guests = _actors(g("guests"))
        add(r, "GuestStars", "|".join(f"{n} ({ro})" if ro else n for n, ro, _ in guests))
    add(r, "IMDB_ID", g("imdb_id"))
    add(r, "Language", opts.get("language", ""))
    if opts.get("ratings", True):
        for src in ("omdb", "tvdb", "tmdb", "tvmaze"):
            if src in ep.ratings and ep.ratings[src][0]:
                add(r, "Rating", f"{float(ep.ratings[src][0]):.1f}")
                break
    if opts.get("crew", True):
        add(r, "Writer", "|".join(_people(g("writers"))))
    add(r, "absolute_number", ep.absolute)
    if ep.season == 0 and ep.display:
        ds, de = ep.display
        if de is None:
            add(r, "airsafter_season", ds)
        else:
            add(r, "airsbefore_episode", de)
            add(r, "airsbefore_season", ds)
    add(r, "EpisodeName", " / ".join(_txt(e.get("title", rules)) for e in episodes if e.get("title", rules))
        or f"Episode {ep.episode}")
    add(r, "Overview", "\n\n".join(_txt(e.get("plot", rules)) for e in episodes if e.get("plot", rules)))
    add(r, "Runtime", g("runtime"))
    if image_name:
        add(r, "filename", "/" + image_name.lstrip("/\\"))
    if video and video.get("streams") and opts.get("techinfo", True):
        _xml_media(r, video)
    return to_text(r).replace(' standalone="yes" ', "")


def _xml_media(r, video: dict):
    v = video.get("video") or {}
    a = (video.get("audio") or [{}])[0]
    add(r, "VideoAspect", v.get("aspect_ratio"))
    add(r, "VideoBitrate", v.get("bitrate"))
    add(r, "VideoCodec", v.get("codec_name_upper"))
    add(r, "VideoFileSize", video.get("size"))
    add(r, "VideoHeight", v.get("height"))
    if video.get("duration_s"):
        add(r, "VideoLength", round(video["duration_s"] / 60))
        add(r, "VideoLengthSeconds", int(video["duration_s"]))
    add(r, "VideoQuality", v.get("quality"))
    add(r, "VideoWidth", v.get("width"))
    add(r, "AudioBitrate", a.get("bitrate"))
    add(r, "AudioChannels", a.get("channels_label"))
    add(r, "AudioCodec", a.get("codec_name_upper"))
    add(r, "AudioFrequency", a.get("sample_rate"))
    add(r, "AspectRatio", v.get("aspect_ratio"))
    mi = ET.SubElement(r, "MediaInfo")
    if v:
        ve = ET.SubElement(mi, "Video")
        add(ve, "Codec", v.get("codec_name_upper"))
        add(ve, "BitRate", v.get("bitrate"))
        add(ve, "Height", v.get("height"))
        add(ve, "Width", v.get("width"))
        add(ve, "FrameRate", v.get("fps"))
        if video.get("duration_s"):
            add(ve, "Duration", round(video["duration_s"] / 60))
            add(ve, "DurationSeconds", int(video["duration_s"]))
        add(ve, "ScanType", v.get("scantype"))
    for au in video.get("audio") or []:
        ae = ET.SubElement(mi, "Audio")
        add(ae, "Codec", au.get("codec_name_upper"))
        add(ae, "Channels", au.get("channels"))
        add(ae, "BitRate", au.get("bitrate"))
        add(ae, "Language", au.get("language"))
    add(r, "VideoHasSubtitles", "True" if video.get("subtitles") else "False")


# ═══════════════════════════════════════════════════════════════════════
# SERIES AND SEASON FILES (F6, F18)
# ═══════════════════════════════════════════════════════════════════════

def tvshow_nfo(series, seasons=None, opts=None, rules=None) -> str:
    opts = opts or {}
    g = lambda f: series.get(f, rules)
    r = ET.Element("tvshow")
    title = g("title") or ""
    add(r, "title", title)
    add(r, "originaltitle", g("originaltitle"))
    add(r, "showtitle", title)
    add(r, "sorttitle", g("sorttitle"))
    if opts.get("ratings", True) and g("rating"):
        rat = ET.SubElement(r, "ratings")
        rr = ET.SubElement(rat, "rating", {"name": series.source("rating", rules) or "default", "max": "10",
                                             "default": "true"})
        add(rr, "value", f"{float(g('rating')):.1f}")
        add(rr, "votes", g("votes"))
    add(r, "year", g("year"))
    plot = g("plot")
    add(r, "plot", plot)
    if plot:
        add(r, "outline", first_sentence(plot))
    add(r, "tagline", g("tagline"))
    add(r, "runtime", g("runtime"))
    add(r, "mpaa", _mpaa_kodi(g("mpaa"), opts.get("rating_country", "")))
    first = True
    for kind, f in (("tvdb", "tvdb_id"), ("imdb", "imdb_id"), ("tvmaze", "tvmaze_id"), ("tmdb", "tmdb_id")):
        if _txt(g(f)):
            add(r, "uniqueid", g(f), {"type": kind, "default": "true" if first else "false"})
            first = False
    ids = {k: _txt(g(f)) for k, f in (("tvdb", "tvdb_id"), ("imdb", "imdb_id"), ("tmdb", "tmdb_id"))
           if _txt(g(f))}
    if ids:
        add(r, "episodeguide", json.dumps(ids, separators=(",", ":")))
    add_many(r, "genre", g("genres"))
    add_many(r, "tag", g("tags"))
    add_many(r, "country", g("countries") or ([g("country")] if g("country") else []))
    add(r, "premiered", g("premiered"))
    add(r, "enddate", g("enddate"))
    add(r, "status", g("status"))
    add_many(r, "studio", g("studios") or ([g("network")] if g("network") else []))
    if opts.get("crew", True):
        add_many(r, "credits", _people(g("creators")))
    for s in sorted((seasons or {}).values(), key=lambda s: s.number):
        if s.get("name", rules):
            add(r, "namedseason", s.get("name", rules), {"number": s.number})
    if opts.get("image_urls", True):
        for kind, aspect in (("poster", "poster"), ("banner", "banner"), ("clearlogo", "clearlogo"),
                             ("clearart", "clearart"), ("landscape", "landscape")):
            url = _first_url(series.artwork.get(kind))
            add(r, "thumb", url, {"aspect": aspect} if url else None)
        fan = [u for u, _ in series.artwork.get("fanart", []) if str(u).startswith("http")][:5]
        if fan:
            fa = ET.SubElement(r, "fanart")
            for u in fan:
                add(fa, "thumb", u)
    if opts.get("cast", True):
        for n, (name, role, thumb) in enumerate(_actors(g("actors"))):
            a = ET.SubElement(r, "actor")
            add(a, "name", name)
            add(a, "role", role)
            add(a, "order", n)
            if opts.get("image_urls", True):
                add(a, "thumb", thumb)
    add(r, "dateadded", opts.get("dateadded") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return to_text(r)


def _first_url(items):
    for u, _src in items or []:
        if str(u).startswith("http"):
            return u
    return ""


def series_xml(series, opts=None, rules=None) -> str:
    opts = opts or {}
    g = lambda f: series.get(f, rules)
    r = ET.Element("Series")
    title = g("title") or ""
    year = g("year")
    local = f"{title} ({year})" if year and f"({year})" not in title else title
    add(r, "id", g("tvdb_id"))
    add(r, "SeriesID", g("tvdb_id"))
    add(r, "LocalTitle", local)
    add(r, "SeriesName", local)
    add(r, "Added", opts.get("added") or datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    add(r, "Airs_DayOfWeek", g("airs_day"))
    add(r, "Airs_Time", g("airs_time"))
    if opts.get("cast", True):
        add(r, "Actors", "|".join(n for n, _, _ in _actors(g("actors"))))
    add(r, "ContentRating", g("mpaa"))
    add(r, "FirstAired", g("premiered"))
    add(r, "Genre", "|".join(_txt(x) for x in g("genres") or []))
    add(r, "IMDb", g("imdb_id"))
    add(r, "IMDbId", g("imdb_id"))
    add(r, "IMDB_ID", g("imdb_id"))
    add(r, "Overview", g("plot"))
    add(r, "Network", g("network"))
    if opts.get("ratings", True) and g("rating"):
        add(r, "Rating", f"{float(g('rating')):.1f}")
    add(r, "Runtime", g("runtime"))
    add(r, "Status", {"Ended": "Ended", "Running": "Continuing", "Continuing": "Continuing"}.get(_txt(g("status")),
                                                                                                    g("status")))
    add(r, "ProductionYear", year)
    add(r, "SortTitle", g("sorttitle") or local)
    add(r, "TMDbId", g("tmdb_id"))
    add(r, "OriginalTitle", g("originaltitle"))
    if opts.get("cast", True):
        actors = _actors(g("actors"))
        if actors:
            p = ET.SubElement(r, "Persons")
            for name, role, _thumb in actors:
                pe = ET.SubElement(p, "Person")
                add(pe, "Name", name)
                add(pe, "Type", "Actor")
                add(pe, "Role", role)
    return to_text(r).replace(' standalone="yes" ', "")


def season_nfo(season, series=None, opts=None, rules=None) -> str:
    opts = opts or {}
    r = ET.Element("season")
    name = season.get("name", rules) or ("Specials" if season.number == 0 else f"Season {season.number}")
    add(r, "title", name)
    add(r, "plot", season.get("plot", rules))
    add(r, "seasonnumber", season.number, always=True)
    add(r, "premiered", season.get("premiered", rules))
    add(r, "enddate", season.get("enddate", rules))
    add(r, "uniqueid", season.get("tvdb_id", rules), {"type": "tvdb", "default": "true"}
        if season.get("tvdb_id", rules) else None)
    if opts.get("image_urls", True):
        url = _first_url(season.posters)
        add(r, "thumb", url, {"aspect": "poster"} if url else None)
    return to_text(r)


# ═══════════════════════════════════════════════════════════════════════
# LEGACY TEMPLATES (F5.3) — R4 behaviour, fixed (B8, B9, B11)
# ═══════════════════════════════════════════════════════════════════════

TECH_TAGS = ("VideoCodec", "VideoBitrate", "VideoFileSize", "VideoWidth", "VideoHeight", "VideoAspect", "AudioCodec",
             "AudioBitrate", "AudioChannels", "AudioFrequency", "AspectRatio", "VideoQuality", "Added", "DateAdded",
             "MediaInfo", "fileinfo")


def stale_template_tags(template: str) -> list:
    """Technical tags with a value inside a template — they would be copied into every episode (B9)."""
    out = []
    for t in TECH_TAGS:
        m = re.search(rf"<{t}>(.+?)</{t}>", template, re.S)
        if m and m.group(1).strip():
            out.append(t)
    return out


def fill_template(template: str, values: dict) -> str:
    """{{field}} placeholders, then tag replacement: <tag>…</tag>, <tag/>, <tag attr=…>…</tag> (B8)."""
    from xml.sax.saxutils import escape
    out = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: escape(_txt(values.get(m.group(1), ""))), template)
    for tag, value in values.items():
        if not re.match(r"^[A-Za-z_][\w.-]*$", tag):
            continue
        v = escape(_txt(value))
        pattern = rf"<{tag}(\s[^>]*)?>(.*?)</{tag}>"
        if re.search(pattern, out, re.S):
            out = re.sub(pattern, lambda m: f"<{tag}{m.group(1) or ''}>{v}</{tag}>", out, count=1, flags=re.S)
        else:
            out = re.sub(rf"<{tag}(\s[^>]*)?/>", lambda m: f"<{tag}{m.group(1) or ''}>{v}</{tag}>", out, count=1)
    out = re.sub(r'<uniqueid\b[^>]*>\s*</uniqueid>\s*', "", out)        # B11: no empty IDs
    return out


# ═══════════════════════════════════════════════════════════════════════
# VALIDATION AND WRITING
# ═══════════════════════════════════════════════════════════════════════

def parse_check(text: str) -> str:
    """'' when the text parses as XML (a multi-episode NFO is checked block by block), else the error."""
    body = re.sub(r"^﻿", "", text)
    body = re.sub(r"<\?xml[^>]*\?>", "", body)
    try:
        ET.fromstring(f"<kfgwrap>{body}</kfgwrap>")
        return ""
    except ET.ParseError as e:
        return str(e)


def atomic_write(path: str, data, encoding: str = None):
    """Write to a temp file in the same folder, then replace (never a half-written file)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kfg_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode(encoding or "utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_existing_ids(path: str) -> dict:
    """IDs and title from an existing tvshow.nfo / series.xml, tolerant to broken XML (MCM's '</role>' error)."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return {}
    out = {}
    for key, pats in (("tvdb_id", [r'<uniqueid[^>]*type="tvdb"[^>]*>(\d+)<', r"<id>(\d+)</id>", r"<SeriesID>(\d+)<",
                                   r"<episodeguide>[^<]*?(\d{4,})"]),
                      ("imdb_id", [r'<uniqueid[^>]*type="imdb"[^>]*>(tt\d+)<', r"<IMDB_ID>(tt\d+)<",
                                   r"<imdb_id>(tt\d+)<", r"(tt\d{6,10})"]),
                      ("tmdb_id", [r'<uniqueid[^>]*type="tmdb"[^>]*>(\d+)<', r"<TMDbId>(\d+)<"]),
                      ("title", [r"<title>([^<]+)</title>", r"<SeriesName>([^<]+)<"])):
        for p in pats:
            m = re.search(p, text)
            if m:
                out[key] = m.group(1).strip()
                break
    return out
