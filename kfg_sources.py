"""Kodi Files Generator — online sources (no Tk).

TheTVDB website (pages the user asks for: series, "All seasons → Official", optional episode pages), TVmaze (free,
no key, CC BY-SA), OMDb (IMDb data, free key) and TMDb (free key). Every value is stored with its source in the
project's records (F17); episodes from a second source are matched by number, then by air date and title (F17.2).
"""
import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from kfg_core import Episode, parse_date, parse_int, title_similarity

APP_UA = "KodiFilesGenerator/0.5.0 (+https://github.com/junqueirach/kodi-files-generator)"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


_SSL = None


def ssl_context():
    """TLS context that works on real PCs: truststore (the Windows certificate check, like curl) → certifi bundle →
    Python's default. On this PC Python's default rejected api.tvmaze.com ('certificate has expired') while both
    truststore and certifi accepted it (checked 2026-10-16)."""
    global _SSL
    if _SSL is None:
        import ssl
        try:
            import truststore
            _SSL = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:
            try:
                import certifi
                _SSL = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                _SSL = ssl.create_default_context()
    return _SSL


class SourceError(Exception):
    pass


class NotFound(SourceError):
    pass


class Blocked(SourceError):
    pass


# ═══════════════════════════════════════════════════════════════════════
# HTTP: cache, polite rate limit, retries (section 5.3)
# ═══════════════════════════════════════════════════════════════════════

class Http:
    MIN_INTERVAL = {"thetvdb.com": 1.0, "api.tvmaze.com": 0.25, "www.omdbapi.com": 0.2, "api.themoviedb.org": 0.1}

    def __init__(self, cache_dir="", ttl_hours=24, opener=None):
        self.cache_dir, self.ttl = cache_dir, ttl_hours * 3600
        self.opener = opener                    # tests: callable(url, headers) -> (status, bytes, final_url)
        self._last = {}
        self._lock = threading.Lock()
        self.mem = {}

    def _cache_path(self, url):
        return os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest() + ".bin") if self.cache_dir else ""

    def _wait(self, host):
        gap = self.MIN_INTERVAL.get(host, 0.1)
        with self._lock:
            now = time.time()
            t = self._last.get(host, 0) + gap - now
            self._last[host] = max(now, self._last.get(host, 0) + gap)
        if t > 0:
            time.sleep(t)

    def get(self, url, browser=False, cache=True, retries=2, timeout=20) -> tuple:
        """(bytes, final_url). Raises NotFound (404), Blocked (403 / challenge), SourceError (other)."""
        if cache and url in self.mem:
            return self.mem[url]
        cp = self._cache_path(url)
        if cache and cp and os.path.isfile(cp) and time.time() - os.path.getmtime(cp) < self.ttl:
            with open(cp, "rb") as f:
                raw = f.read()
            final, _, body = raw.partition(b"\n")
            self.mem[url] = (body, final.decode())
            return self.mem[url]
        headers = {"User-Agent": BROWSER_UA if browser else APP_UA, "Accept-Language": "en,pt;q=0.8"}
        host = urllib.parse.urlparse(url).netloc
        last = None
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                if self.opener:
                    status, body, final = self.opener(url, headers)
                else:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
                        status, body, final = r.status, r.read(), r.geturl()
            except urllib.error.HTTPError as e:
                status, body, final = e.code, e.read() if hasattr(e, "read") else b"", url
            except Exception as e:                       # network errors
                last = SourceError(f"could not reach {host}: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if status == 404:
                raise NotFound(f"{host}: not found (HTTP 404)")
            if status in (401, 403) or b"cf-chl" in body[:20000] or b"Just a moment" in body[:5000]:
                raise Blocked(f"{host} refused the request (HTTP {status})")
            if status == 429 or status >= 500:
                last = SourceError(f"{host}: HTTP {status}")
                time.sleep(3 * (attempt + 1))
                continue
            if status != 200:
                raise SourceError(f"{host}: HTTP {status}")
            self.mem[url] = (body, final)
            if cache and cp:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(cp, "wb") as f:
                    f.write(final.encode() + b"\n" + body)
            return body, final
        raise last or SourceError(f"{host}: no answer")

    def json(self, url, **kw):
        body, _ = self.get(url, **kw)
        return json.loads(body.decode("utf-8"))

    def text(self, url, **kw):
        body, final = self.get(url, **kw)
        return body.decode("utf-8", "replace"), final


def strip_html(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</p>\s*<p>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


# ═══════════════════════════════════════════════════════════════════════
# LINKS (F16.1)
# ═══════════════════════════════════════════════════════════════════════

def parse_link(text: str) -> tuple:
    """('tvdb', url) | ('tvdb_id', '73255') | ('imdb', 'tt…') | ('tvmaze', '61315') | ('tmdb', '1408') | ('', '')."""
    t = (text or "").strip()
    m = re.search(r"thetvdb\.com/(?:[a-z]{2,3}/)?(series/[^/?#\s]+|dereferrer/series/\d+)", t, re.I)
    if m:
        return "tvdb", "https://thetvdb.com/" + m.group(1)
    m = re.search(r"(tt\d{6,10})", t)
    if m:
        return "imdb", m.group(1)
    m = re.search(r"tvmaze\.com/shows/(\d+)", t, re.I)
    if m:
        return "tvmaze", m.group(1)
    m = re.search(r"themoviedb\.org/tv/(\d+)", t, re.I)
    if m:
        return "tmdb", m.group(1)
    if re.fullmatch(r"\d{2,8}", t):
        return "tvdb_id", t
    return "", ""


TVDB_LANGS = {"pt-BR": ["pt", "por"], "pt": ["pt", "por"], "pt-PT": ["por", "pt"], "en": ["eng"], "es": ["spa"],
              "fr": ["fra"], "de": ["deu"], "it": ["ita"], "nl": ["nld"], "pl": ["pol"], "ja": ["jpn"]}


def tvdb_lang_codes(language: str) -> list:
    return TVDB_LANGS.get(language) or TVDB_LANGS.get((language or "en").split("-")[0]) or ["eng"]


# ═══════════════════════════════════════════════════════════════════════
# THETVDB WEBSITE PARSERS (pure functions on the HTML; tested with saved pages)
# ═══════════════════════════════════════════════════════════════════════

# season headings and the start of each episode block; a block runs to the next mark
_TVDB_MARK = re.compile(r'<h3[^>]*>(?:<a[^>]*>)?\s*(Season\s+\d+|Additional Specials|Specials)\s*(?:</a>)?</h3>|'
                        r'<li class="list-group-item(?: list-group-item-special)?">', re.S)
_TVDB_LABEL = re.compile(r'episode-label">\s*(S(\d+)E(\d+)|SPECIAL\s+(\d+)x(\d+))\s*<', re.S)


class LayoutChanged(SourceError):
    pass


def parse_tvdb_allseasons(page: str) -> list:
    """Episodes of the 'All seasons → Official' page: Episode records (source 'tvdb') with S/E, title, aired, network,
    plot, TheTVDB episode ID, image URL and the specials' position. Raises LayoutChanged when nothing is found."""
    eps, season_ctx, pending = [], None, []
    marks = list(_TVDB_MARK.finditer(page))
    for i, m in enumerate(marks):
        if m.group(1):
            for sp in pending:
                sp.display = (season_ctx, None)
            pending = []
            sm = re.match(r"Season\s+(\d+)", m.group(1))
            season_ctx = int(sm.group(1)) if sm else None
            continue
        block = page[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        lab = _TVDB_LABEL.search(block)
        if not lab:
            continue
        if lab.group(2):
            ep = Episode(int(lab.group(2)), int(lab.group(3)))
        else:
            ep = Episode(int(lab.group(4)), int(lab.group(5)))
        idm = re.search(r'/episodes/(\d+)"\s*>\s*(.*?)\s*</a>', block, re.S)
        if idm:
            ep.set("tvdb_id", idm.group(1), "tvdb")
            ep.set("title", strip_html(idm.group(2)), "tvdb")
            ep.set("title_en", strip_html(idm.group(2)), "tvdb")
            ep.tvdb_url_path = re.search(r'href="(/series/[^"]+/episodes/\d+)"', block).group(1)
        infos = [strip_html(x) for x in re.findall(r'<ul class="list-inline text-muted">(.*?)</ul>', block, re.S)
                 for x in re.findall(r"<li>(.*?)</li>", x, re.S)]
        for info in infos:
            iso, warn = parse_date(info)
            if not warn and re.match(r"^\d{4}-\d\d-\d\d$", iso):
                ep.set("aired", iso, "tvdb")
            elif info:
                ep.set("network", info, "tvdb")
        pm = re.search(r"<p>(.*?)</p>", block, re.S)
        if pm:
            ep.set("plot", strip_html(pm.group(1)), "tvdb")
        im = re.search(r'data-src="(https://artworks\.thetvdb\.com/[^"]+)"', block)
        if im:
            ep.images.append((im.group(1), "tvdb"))
        if ep.season == 0:
            if season_ctx is not None:
                pending.append(ep)
        else:
            for sp in pending:
                sp.display = (ep.season, ep.episode)
            pending = []
        eps.append(ep)
    for sp in pending:
        sp.display = (season_ctx, None) if season_ctx is not None else None
    if not eps:
        raise LayoutChanged("TheTVDB page layout not recognised — use Paste instead")
    return eps


def _info_items(page: str) -> dict:
    """<strong>Label</strong> <span>…</span>… → {label: [texts]}."""
    out = {}
    for m in re.finditer(r"<strong>([^<]{2,40})</strong>(.*?)</li>", page, re.S):
        spans = re.findall(r"<span[^>]*>(.*?)</span>", m.group(2), re.S)
        out.setdefault(m.group(1).strip(), []).extend(strip_html(s) for s in spans)
    return out


def _translations(page: str) -> dict:
    out = {}
    for m in re.finditer(r'data-language="([a-z]{2,3})"\s+data-title="([^"]*)"[^>]*>\s*(?:<p>(.*?)</p>)?', page, re.S):
        out.setdefault(m.group(1), (html.unescape(m.group(2)).strip(), strip_html(m.group(3) or "")))
    return out


def parse_tvdb_episode(page: str, language="pt-BR") -> dict:
    """Runtime, content ratings by country, IMDb ID, writers, directors, guest stars (name, role), network, production
    companies, the title/plot in the chosen language, the English title, the screencap image, DVD/absolute numbers."""
    info = _info_items(page)
    res = {"runtime": None, "ratings": [], "imdb_id": "", "writers": [], "directors": [], "guests": [],
           "network": "", "studios": [], "title": "", "plot": "", "title_en": "", "image": "", "dvd": None,
           "absolute": None}
    for v in info.get("Runtime", []):
        res["runtime"] = parse_int(v)
    for m in re.finditer(r'<strong>Content Rating</strong>(.*?)</li>', page, re.S):
        for sm in re.finditer(r'<small class="text-muted">([^<]+)</small>\s*([^<\s][^<]*)', m.group(1)):
            res["ratings"].append((sm.group(1).strip(), sm.group(2).strip()))
    mi = re.search(r"imdb\.com/title/(tt\d+)", page)
    res["imdb_id"] = mi.group(1) if mi else ""
    res["network"] = (info.get("Network") or [""])[0]
    res["studios"] = info.get("Production Company") or []
    for name, kind, role in re.findall(r'<tr>\s*<td><a href="/people/[^"]*">([^<]+)</a></td>\s*<td>([^<]*)</td>\s*'
                                       r'<td>([^<]*)</td>', page):
        name, kind, role = html.unescape(name).strip(), kind.strip().lower(), html.unescape(role).strip()
        if kind == "writer":
            res["writers"].append(name)
        elif kind == "director":
            res["directors"].append(name)
        elif kind in ("guest star", "actor", "guest"):
            res["guests"].append((name, role, ""))
    tr = _translations(page)
    for code in tvdb_lang_codes(language):
        if code in tr and tr[code][0]:
            res["title"], res["plot"] = tr[code]
            break
    if "eng" in tr:
        res["title_en"] = tr["eng"][0]
        if not res["plot"] and not res["title"]:
            res["title"], res["plot"] = tr["eng"]
    im = re.search(r'(https://artworks\.thetvdb\.com/banners/[^"\s]+/(?:screencap|episodes)/[^"\s]+\.jpg)', page)
    res["image"] = im.group(1) if im else ""
    m = re.search(r"DVD Order /\s*<a[^>]*>Season (\d+)</a> /\s*Episode (\d+)", page)
    if m:
        res["dvd"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r"Absolute Order /\s*<a[^>]*>Season \d+</a> /\s*Episode (\d+)", page)
    if m:
        res["absolute"] = int(m.group(1))
    return res


def parse_tvdb_series(page: str, language="pt-BR") -> dict:
    info = _info_items(page)
    res = {"tvdb_id": (info.get("TheTVDB.com Series ID") or [""])[0], "status": (info.get("Status") or [""])[0],
           "premiered": parse_date((info.get("First Aired") or [""])[0])[0],
           "network": (info.get("Network") or [""])[0], "runtime": parse_int((info.get("Average Runtime") or [""])[0]),
           "genres": info.get("Genres") or [], "country": (info.get("Original Country") or [""])[0],
           "language": (info.get("Original Language") or [""])[0], "airs_day": "", "airs_time": "",
           "imdb_id": "", "title": "", "plot": "", "title_en": "", "actors": [], "artwork": {}, "official_site": ""}
    airs = " ".join(info.get("Airs") or [])
    m = re.search(r"([A-Z][a-z]+day)", airs)
    res["airs_day"] = m.group(1) if m else ""
    m = re.search(r"at\s+([\d:]+\s*[ap]m)", page[page.find("<strong>Airs</strong>"):][:400], re.I) \
        if "<strong>Airs</strong>" in page else None
    res["airs_time"] = m.group(1) if m else ""
    mi = re.search(r"imdb\.com/title/(tt\d+)", page)
    res["imdb_id"] = mi.group(1) if mi else ""
    tr = _translations(page)
    for code in tvdb_lang_codes(language):
        if code in tr and tr[code][0]:
            res["title"], res["plot"] = tr[code]
            break
    if "eng" in tr:
        res["title_en"] = tr["eng"][0]
        if not res["title"]:
            res["title"], res["plot"] = tr["eng"]
    for name, role, img in re.findall(r'<h3>\s*([^<]+?)<br>\s*<small>\s*as\s+([^<]*?)\s*</small>\s*</h3>\s*'
                                      r'<img[^>]*data-src="([^"]*)"', page, re.S):
        thumb = img if "/actors/" in img or "/person/" in img else ""
        res["actors"].append((html.unescape(name).strip(), html.unescape(role).strip(), thumb))
    tabs = {"posters": "poster", "backgrounds": "fanart", "banners": "banner", "clearlogo": "clearlogo",
            "clearart": "clearart"}
    for tab, kind in tabs.items():
        m = re.search(rf'id="artwork-{tab}"(.*?)(?=<div role="tabpanel"|$)', page, re.S)
        if m:
            urls = re.findall(r'<a href="(https://artworks\.thetvdb\.com/banners/[^"]+)" class="lightbox"', m.group(1))
            res["artwork"][kind] = list(dict.fromkeys(urls))
    return res


def tvdb_slug_url(url: str) -> str:
    m = re.search(r"(https://thetvdb\.com/series/[^/?#]+)", url)
    return m.group(1) if m else url


def fetch_tvdb(http: Http, project, link: str, language="pt-BR", details=False, rating_country="Brazil",
               progress=None, cancel=None) -> dict:
    """Series page + official episode list (+ each episode page when details). Returns a summary dict."""
    if re.fullmatch(r"\d+", link.strip()):
        link = f"https://thetvdb.com/dereferrer/series/{link.strip()}"
    page, final = http.text(link, browser=True)
    base = tvdb_slug_url(final)
    if not base.startswith("https://thetvdb.com/series/"):
        raise SourceError("this TheTVDB link does not lead to a series page")
    sp = page if final.rstrip("/") == base else http.text(base, browser=True)[0]
    s = parse_tvdb_series(sp, language)
    ser = project.series
    for f in ("tvdb_id", "status", "premiered", "network", "runtime", "genres", "country", "airs_day", "airs_time",
              "imdb_id", "actors"):
        ser.set(f, s[f], "tvdb")
    ser.set("title", s["title"], "tvdb")
    ser.set("originaltitle", s["title_en"] if s["title_en"] != s["title"] else "", "tvdb")
    ser.set("plot", s["plot"], "tvdb")
    if s["premiered"][:4].isdigit():
        ser.set("year", int(s["premiered"][:4]), "tvdb")
    for kind, urls in s["artwork"].items():
        for u in urls:
            if (u, "tvdb") not in ser.artwork.setdefault(kind, []):
                ser.artwork[kind].append((u, "tvdb"))
    allp, _ = http.text(base + "/allseasons/official", browser=True)
    eps = parse_tvdb_allseasons(allp)
    if details:
        for i, ep in enumerate(eps, 1):
            if cancel and cancel():
                break
            if progress:
                progress(i, len(eps), f"TheTVDB episode {ep.code}")
            path = getattr(ep, "tvdb_url_path", "")
            if not path:
                continue
            try:
                d = parse_tvdb_episode(http.text("https://thetvdb.com" + path, browser=True)[0], language)
            except SourceError:
                continue
            ep.set("runtime", d["runtime"], "tvdb")
            ep.set("imdb_id", d["imdb_id"], "tvdb")
            ep.set("writers", d["writers"], "tvdb")
            ep.set("directors", d["directors"], "tvdb")
            ep.set("guests", d["guests"], "tvdb")
            if d["title"]:
                ep.set("title", d["title"], "tvdb")
            if d["plot"]:
                ep.set("plot", d["plot"], "tvdb")
            ep.set("title_en", d["title_en"], "tvdb")
            rating = next((v for c, v in d["ratings"] if c.lower() == (rating_country or "").lower()), "")
            ep.set("mpaa", rating, "tvdb")
            if d["image"] and (d["image"], "tvdb") not in ep.images:
                ep.images.insert(0, (d["image"], "tvdb"))
            if d["absolute"]:
                ep.absolute = ep.absolute or d["absolute"]
    added, notes = project.merge_episodes(eps, "tvdb")
    return {"series": s["title"] or s["title_en"], "episodes": len(eps), "added": added, "notes": notes}


# ═══════════════════════════════════════════════════════════════════════
# TVMAZE (F4) — free, no key, CC BY-SA 4.0
# ═══════════════════════════════════════════════════════════════════════

TVMAZE = "https://api.tvmaze.com"


def tvmaze_find(http: Http, tvdb_id="", imdb_id="", name=""):
    if tvdb_id:
        try:
            return http.json(f"{TVMAZE}/lookup/shows?thetvdb={tvdb_id}")
        except NotFound:
            pass
    if imdb_id:
        try:
            return http.json(f"{TVMAZE}/lookup/shows?imdb={imdb_id}")
        except NotFound:
            pass
    if name:
        res = http.json(f"{TVMAZE}/search/shows?q={urllib.parse.quote(name)}")
        return res[0]["show"] if res else None
    return None


def tvmaze_search(http: Http, name: str) -> list:
    return [r["show"] for r in http.json(f"{TVMAZE}/search/shows?q={urllib.parse.quote(name)}")]


def fetch_tvmaze(http: Http, project, show: dict, people=True, progress=None, cancel=None) -> dict:
    sid = show["id"]
    ser = project.series
    ser.set("tvmaze_id", str(sid), "tvmaze")
    ser.set("title", show.get("name"), "tvmaze")
    ser.set("plot", strip_html(show.get("summary") or ""), "tvmaze")
    ser.set("premiered", show.get("premiered"), "tvmaze")
    ser.set("enddate", show.get("ended"), "tvmaze")
    if (show.get("premiered") or "")[:4].isdigit():
        ser.set("year", int(show["premiered"][:4]), "tvmaze")
    ser.set("status", show.get("status"), "tvmaze")
    ser.set("genres", show.get("genres"), "tvmaze")
    ser.set("runtime", show.get("runtime") or show.get("averageRuntime"), "tvmaze")
    net = show.get("network") or show.get("webChannel") or {}
    ser.set("network", net.get("name"), "tvmaze")
    ser.set("country", ((net.get("country") or {}).get("name")), "tvmaze")
    ser.set("official_site", show.get("officialSite"), "tvmaze")
    ext = show.get("externals") or {}
    ser.set("tvdb_id", str(ext["thetvdb"]) if ext.get("thetvdb") else "", "tvmaze")
    ser.set("imdb_id", ext.get("imdb"), "tvmaze")
    rating = (show.get("rating") or {}).get("average")
    ser.set("rating", rating, "tvmaze")
    sched = show.get("schedule") or {}
    ser.set("airs_day", (sched.get("days") or [""])[0], "tvmaze")
    ser.set("airs_time", sched.get("time"), "tvmaze")
    eps_raw = http.json(f"{TVMAZE}/shows/{sid}/episodes?specials=1")
    eps = []
    for e in eps_raw:
        if e.get("season") is None or e.get("number") is None:
            continue
        ep = Episode(e["season"], e["number"])
        ep.set("title", e.get("name"), "tvmaze")
        ep.set("title_en", e.get("name"), "tvmaze")
        ep.set("plot", strip_html(e.get("summary") or ""), "tvmaze")
        ep.set("aired", e.get("airdate"), "tvmaze")
        ep.set("runtime", e.get("runtime"), "tvmaze")
        ep.set("tvmaze_id", str(e["id"]), "tvmaze")
        img = (e.get("image") or {}).get("original")
        if img:
            ep.images.append((img, "tvmaze"))
        r = (e.get("rating") or {}).get("average")
        if r:
            ep.ratings["tvmaze"] = (r, None)
        ep._tvmaze_id = e["id"]
        eps.append(ep)
    try:
        for s in http.json(f"{TVMAZE}/shows/{sid}/seasons"):
            se = project.season(s["number"])
            se.set("name", s.get("name"), "tvmaze")
            se.set("plot", strip_html(s.get("summary") or ""), "tvmaze")
            se.set("premiered", s.get("premiereDate"), "tvmaze")
            se.set("enddate", s.get("endDate"), "tvmaze")
            img = (s.get("image") or {}).get("original")
            if img and (img, "tvmaze") not in se.posters:
                se.posters.append((img, "tvmaze"))
    except SourceError:
        pass
    try:
        imgs = http.json(f"{TVMAZE}/shows/{sid}/images")
        kinds = {"poster": "poster", "background": "fanart", "banner": "banner"}
        for im in sorted(imgs, key=lambda x: not x.get("main")):
            k = kinds.get(im.get("type"))
            url = ((im.get("resolutions") or {}).get("original") or {}).get("url")
            if k and url and (url, "tvmaze") not in ser.artwork.setdefault(k, []):
                ser.artwork[k].append((url, "tvmaze"))
    except SourceError:
        pass
    if people:
        try:
            cast = http.json(f"{TVMAZE}/shows/{sid}/cast")
            ser.set("actors", [((c.get("person") or {}).get("name"), (c.get("character") or {}).get("name"),
                                (((c.get("character") or {}).get("image") or (c.get("person") or {}).get("image")
                                  or {}).get("original") or "")) for c in cast], "tvmaze")
            crew = http.json(f"{TVMAZE}/shows/{sid}/crew")
            ser.set("creators", [(c.get("person") or {}).get("name") for c in crew if c.get("type") == "Creator"],
                    "tvmaze")
        except SourceError:
            pass
        for i, ep in enumerate(eps, 1):
            if cancel and cancel():
                break
            if progress:
                progress(i, len(eps), f"TVmaze people {ep.code}")
            try:
                crew = http.json(f"{TVMAZE}/episodes/{ep._tvmaze_id}/guestcrew")
                ep.set("directors", [c["person"]["name"] for c in crew if c.get("guestCrewType") == "Director"],
                       "tvmaze")
                ep.set("writers", [c["person"]["name"] for c in crew if c.get("guestCrewType") == "Writer"], "tvmaze")
                cast = http.json(f"{TVMAZE}/episodes/{ep._tvmaze_id}/guestcast")
                ep.set("guests", [(c["person"]["name"], (c.get("character") or {}).get("name") or "",
                                   ((c["person"].get("image") or {}).get("original") or "")) for c in cast], "tvmaze")
            except (SourceError, KeyError, TypeError):
                continue
    added, notes = project.merge_episodes(eps, "tvmaze")
    return {"series": show.get("name"), "episodes": len(eps), "added": added, "notes": notes}


# ═══════════════════════════════════════════════════════════════════════
# OMDb (IMDb data, free key) and TMDb (free key) — optional (F14, F17)
# ═══════════════════════════════════════════════════════════════════════

def fetch_omdb(http: Http, project, key: str, imdb_id: str, progress=None, cancel=None) -> dict:
    base = "https://www.omdbapi.com/?apikey=" + urllib.parse.quote(key)
    s = http.json(f"{base}&i={imdb_id}")
    if s.get("Response") == "False":
        raise SourceError("OMDb: " + (s.get("Error") or "no answer"))
    ser = project.series
    ser.set("imdb_id", imdb_id, "omdb")
    ser.set("mpaa", s.get("Rated") if s.get("Rated") not in ("N/A", None) else "", "omdb")
    ser.set("genres", [g.strip() for g in (s.get("Genre") or "").split(",") if g.strip() and g.strip() != "N/A"], "omdb")
    ser.set("creators", [w.strip() for w in (s.get("Writer") or "").split(",") if w.strip() not in ("", "N/A")], "omdb")
    try:
        ser.set("rating", float(s.get("imdbRating")), "omdb")
        ser.set("votes", int((s.get("imdbVotes") or "0").replace(",", "")), "omdb")
    except (TypeError, ValueError):
        pass
    total = parse_int(s.get("totalSeasons")) or 0
    eps = []
    for n in range(1, total + 1):
        if cancel and cancel():
            break
        if progress:
            progress(n, total, f"OMDb season {n}")
        d = http.json(f"{base}&i={imdb_id}&Season={n}")
        for e in d.get("Episodes") or []:
            num = parse_int(e.get("Episode"))
            if not num:
                continue
            ep = Episode(n, num)
            ep.set("title", e.get("Title"), "omdb")
            ep.set("title_en", e.get("Title"), "omdb")
            iso, warn = parse_date(e.get("Released"))
            ep.set("aired", "" if warn else iso, "omdb")
            ep.set("imdb_id", e.get("imdbID"), "omdb")
            try:
                ep.ratings["omdb"] = (float(e.get("imdbRating")), None)
            except (TypeError, ValueError):
                pass
            eps.append(ep)
    added, notes = project.merge_episodes(eps, "omdb")
    return {"series": s.get("Title"), "episodes": len(eps), "added": added, "notes": notes}


TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/original"


def fetch_tmdb(http: Http, project, key: str, language="pt-BR", tvdb_id="", imdb_id="", tmdb_id="",
               progress=None, cancel=None) -> dict:
    q = f"api_key={urllib.parse.quote(key)}&language={language}"
    if not tmdb_id:
        for ext, val in (("tvdb_id", tvdb_id), ("imdb_id", imdb_id)):
            if val:
                f = http.json(f"{TMDB}/find/{val}?api_key={urllib.parse.quote(key)}&external_source={ext}")
                if f.get("tv_results"):
                    tmdb_id = str(f["tv_results"][0]["id"])
                    break
    if not tmdb_id:
        raise NotFound("TMDb: series not found")
    t = http.json(f"{TMDB}/tv/{tmdb_id}?{q}&append_to_response=external_ids,keywords,content_ratings,credits")
    ser = project.series
    ser.set("tmdb_id", tmdb_id, "tmdb")
    ser.set("title", t.get("name"), "tmdb")
    ser.set("originaltitle", t.get("original_name") if t.get("original_name") != t.get("name") else "", "tmdb")
    ser.set("plot", t.get("overview"), "tmdb")
    ser.set("tagline", t.get("tagline"), "tmdb")
    ser.set("genres", [g["name"] for g in t.get("genres") or []], "tmdb")
    ser.set("tags", [k["name"] for k in (t.get("keywords") or {}).get("results") or []], "tmdb")
    ser.set("studios", [c["name"] for c in t.get("production_companies") or []], "tmdb")
    ser.set("countries", t.get("origin_country"), "tmdb")
    ser.set("premiered", t.get("first_air_date"), "tmdb")
    ser.set("creators", [c["name"] for c in t.get("created_by") or []], "tmdb")
    ser.set("actors", [(c["name"], c.get("character"), (TMDB_IMG + c["profile_path"]) if c.get("profile_path") else "")
                       for c in (t.get("credits") or {}).get("cast") or []], "tmdb")
    ext = t.get("external_ids") or {}
    ser.set("imdb_id", ext.get("imdb_id"), "tmdb")
    ser.set("tvdb_id", str(ext["tvdb_id"]) if ext.get("tvdb_id") else "", "tmdb")
    if t.get("poster_path"):
        ser.artwork.setdefault("poster", []).append((TMDB_IMG + t["poster_path"], "tmdb"))
    if t.get("backdrop_path"):
        ser.artwork.setdefault("fanart", []).append((TMDB_IMG + t["backdrop_path"], "tmdb"))
    eps = []
    seasons = t.get("seasons") or []
    for i, s in enumerate(seasons, 1):
        if cancel and cancel():
            break
        if progress:
            progress(i, len(seasons), f"TMDb season {s.get('season_number')}")
        d = http.json(f"{TMDB}/tv/{tmdb_id}/season/{s['season_number']}?{q}")
        se = project.season(s["season_number"])
        se.set("name", d.get("name"), "tmdb")
        se.set("plot", d.get("overview"), "tmdb")
        if d.get("poster_path"):
            se.posters.append((TMDB_IMG + d["poster_path"], "tmdb"))
        for e in d.get("episodes") or []:
            ep = Episode(e["season_number"], e["episode_number"])
            ep.set("title", e.get("name"), "tmdb")
            ep.set("plot", e.get("overview"), "tmdb")
            ep.set("aired", e.get("air_date"), "tmdb")
            ep.set("runtime", e.get("runtime"), "tmdb")
            ep.set("tmdb_id", str(e["id"]), "tmdb")
            ep.set("directors", [c["name"] for c in e.get("crew") or [] if c.get("job") == "Director"], "tmdb")
            ep.set("writers", [c["name"] for c in e.get("crew") or [] if c.get("department") == "Writing"], "tmdb")
            ep.set("guests", [(g["name"], g.get("character") or "",
                               (TMDB_IMG + g["profile_path"]) if g.get("profile_path") else "")
                              for g in e.get("guest_stars") or []], "tmdb")
            if e.get("still_path"):
                ep.images.append((TMDB_IMG + e["still_path"], "tmdb"))
            if e.get("vote_average"):
                ep.ratings["tmdb"] = (e["vote_average"], e.get("vote_count"))
            eps.append(ep)
    added, notes = project.merge_episodes(eps, "tmdb")
    return {"series": t.get("name"), "episodes": len(eps), "added": added, "notes": notes}


def download(http: Http, url: str) -> bytes:
    body, _ = http.get(url, browser="thetvdb.com" in url, cache=True)
    return body


# ═══════════════════════════════════════════════════════════════════════
# MATCHING EPISODES ACROSS SOURCES (F17.2)
# ═══════════════════════════════════════════════════════════════════════

def _days(a: str, b: str):
    from datetime import date
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def match_episode(ep: Episode, existing: dict):
    """(key, confidence, how) of the project episode this source episode belongs to, or (None, 0, '')."""
    t, a = ep.get("title_en") or ep.get("title") or "", ep.get("aired") or ""
    cur = existing.get(ep.key)
    if cur is not None:
        ct = cur.get("title_en") or cur.get("title") or ""
        d = _days(a, cur.get("aired") or "")
        if not t or not ct or title_similarity(t, ct) >= 0.6 or (d is not None and d <= 2):
            return ep.key, 1.0, "number"
    best, best_score = None, 0.0
    for key, other in existing.items():
        ot = other.get("title_en") or other.get("title") or ""
        sim = title_similarity(t, ot) if t and ot else 0.0
        d = _days(a, other.get("aired") or "")
        score = sim * 0.7 + (0.3 if d is not None and d <= 2 else 0.0)
        if d is not None and d <= 2 and sim >= 0.45:
            score += 0.2
        if score > best_score:
            best, best_score = key, score
    if best is not None and best_score >= 0.75:
        return best, min(1.0, best_score), "date+title"
    if cur is not None:
        return ep.key, 0.5, "number (title differs)"
    return None, 0.0, ""
