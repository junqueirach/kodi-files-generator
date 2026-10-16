"""Kodi Files Generator — engine (no Tk).

Project (series + seasons + episodes with per-source values + the videos of a series folder), the plan of every
file to write for the chosen server profiles (F5–F8, F12), apply with backups and a manifest (F10), undo, the health
check (F9) and the renamer plan (F11).
"""
import json
import os
import re
import shutil
import time
from datetime import datetime

import kfg_core as core
import kfg_writers as W
from kfg_sources import match_episode

PROFILES = ["kodi", "jellyfin", "emby", "xml", "plex"]
PROFILE_LABELS = {"kodi": "Kodi", "jellyfin": "Jellyfin", "emby": "Emby",
                  "xml": "XML (MediaBrowser / Media Center Master)", "plex": "Plex"}
DEFAULT_PROFILES = ["kodi", "jellyfin", "emby", "xml"]

# Series artwork: kind → file names per profile (research table 5.1)
ART_NAMES = {
    "poster":    {"kodi": ["poster.jpg"], "jellyfin": ["poster.jpg", "folder.jpg"], "emby": ["poster.jpg", "folder.jpg"],
                  "xml": ["folder.jpg"], "plex": ["poster.jpg"]},
    "fanart":    {"kodi": ["fanart.jpg"], "jellyfin": ["fanart.jpg", "backdrop.jpg"], "emby": ["fanart.jpg", "backdrop.jpg"],
                  "xml": ["backdrop.jpg"], "plex": ["fanart.jpg"]},
    "banner":    {p: ["banner.jpg"] for p in PROFILES},
    "clearlogo": {"kodi": ["clearlogo.png"], "jellyfin": ["logo.png"], "emby": ["logo.png"], "xml": ["logo.png"]},
    "clearart":  {"kodi": ["clearart.png"], "jellyfin": ["clearart.png"], "emby": ["clearart.png"], "xml": ["clearart.png"]},
    "landscape": {"kodi": ["landscape.jpg"], "jellyfin": ["landscape.jpg"], "emby": ["landscape.jpg"], "xml": ["thumb.jpg"]},
}
ART_KINDS = list(ART_NAMES)
VOLATILE = re.compile(r"<(dateadded|Added|DateAdded)>[^<]*</\1>\s*")


def art_names(kind, profiles) -> list:
    out = []
    for p in profiles:
        for n in ART_NAMES.get(kind, {}).get(p, []):
            if n not in out:
                out.append(n)
    return out


def season_poster_targets(show_dir, season_dir, number, profiles) -> list:
    out = []
    if any(p in profiles for p in ("kodi", "jellyfin", "emby")):
        out.append(os.path.join(show_dir, "season-specials-poster.jpg" if number == 0 else f"season{number:02d}-poster.jpg"))
    if season_dir and os.path.normcase(season_dir) != os.path.normcase(show_dir):
        if "xml" in profiles or "emby" in profiles:
            out.append(os.path.join(season_dir, "folder.jpg"))
        if "plex" in profiles:
            out.append(os.path.join(season_dir, f"Season{number:02d}.jpg"))
    return out


# ═══════════════════════════════════════════════════════════════════════
# PROJECT
# ═══════════════════════════════════════════════════════════════════════

class Project:
    BASE_SOURCES = ("csv", "paste", "tvdb", "file")

    def __init__(self):
        self.series = core.Series()
        self.seasons = {}
        self.episodes = {}
        self.videos = []
        self.show_dir = ""
        self.notes = []          # [(level, text)]
        self.rules = {}          # user changes of DEFAULT_RULES {field: [sources]}
        self.match_notes = []    # [(video, text)]

    # records
    def season(self, n) -> core.Season:
        if n not in self.seasons:
            self.seasons[n] = core.Season(n)
        return self.seasons[n]

    def sorted_episodes(self) -> list:
        return [self.episodes[k] for k in sorted(self.episodes)]

    def merge_episodes(self, eps, source, create=None) -> tuple:
        """Match each source episode to a project episode (number, then date + title — F17.2). Returns (added, notes).
        Base sources (CSV, paste, TheTVDB, files) add new episodes; the others only fill the existing ones, unless the
        project has no episodes yet."""
        if create is None:
            create = source in self.BASE_SOURCES or not self.episodes
        added, notes, missing = 0, [], []
        for ep in eps:
            # match only against episodes that came from OTHER sources: the episodes of one import never merge
            others = {k: e for k, e in self.episodes.items()
                      if any(source not in by_src for by_src in e.values.values())}
            if not others:
                key, how = (ep.key, "number") if ep.key in self.episodes else (None, "")
            else:
                key, conf, how = match_episode(ep, others)
                if key is None and ep.key in self.episodes:
                    key, how = ep.key, "number"
            if key is None:
                if create:
                    self.episodes[ep.key] = ep
                    added += 1
                else:
                    missing.append(ep.code)
                continue
            if key not in self.episodes:
                self.episodes[key] = ep
                added += 1
                continue
            target = self.episodes[key]
            if key != ep.key:
                notes.append(f"{core.SOURCE_LABELS.get(source, source)}: {ep.code} "
                             f"'{ep.get('title')}' matched to {target.code} by {how}")
            if how == "number (title differs)":
                notes.append(f"{core.SOURCE_LABELS.get(source, source)}: {ep.code} title differs — "
                             f"'{ep.get('title')}' vs '{target.get('title')}'")
            target.merge(ep)
        if missing:
            notes.append(f"{core.SOURCE_LABELS.get(source, source)} knows {len(missing)} episode(s) that are not in the "
                         f"list: {', '.join(missing[:15])}{' …' if len(missing) > 15 else ''}")
        for n in notes:
            self.notes.append(("info", n))
        for ep in self.episodes.values():
            self.season(ep.season)
        return added, notes

    # folder
    def scan(self, show_dir: str, ffprobe: str = "", progress=None, cancel=None):
        import kfg_media
        self.show_dir = os.path.normpath(show_dir)
        name, year = core.show_from_folder(os.path.basename(self.show_dir))
        self.series.set("title", name, "file")
        self.series.set("year", year, "file")
        for fn in ("tvshow.nfo", "series.xml"):
            ids = W.read_existing_ids(os.path.join(self.show_dir, fn))
            for k, v in ids.items():
                if k != "title":
                    self.series.set(k, v, "file")
        self.videos = core.find_videos(self.show_dir)
        file_eps = []
        for v in self.videos:
            if not v.ref:
                continue
            for i, e in enumerate(v.ref["episodes"]):
                ep = core.Episode(v.ref["season"], e)
                nfo = os.path.splitext(v.path)[0] + ".nfo"
                ids = W.read_existing_ids(nfo) if os.path.isfile(nfo) else {}
                ep.set("title", ids.get("title") or (v.ref["title"] if i == 0 else ""), "file")
                ep.set("tvdb_id", ids.get("tvdb_id"), "file")
                ep.set("imdb_id", ids.get("imdb_id"), "file")
                file_eps.append(ep)
        existing = {e.key for e in file_eps}
        if file_eps:
            self.merge_episodes([e for e in file_eps if e.key in existing], "file")
        self.match()
        if ffprobe:
            for i, v in enumerate(self.videos, 1):
                if cancel and cancel():
                    break
                if progress:
                    progress(i, len(self.videos), os.path.basename(v.path))
                try:
                    v.probe = kfg_media.probe(ffprobe, v.path)
                except Exception as e:
                    self.notes.append(("warn", f"ffprobe could not read {os.path.basename(v.path)}: {e}"))
                    continue
                minutes = round((v.probe.get("duration_s") or 0) / 60)
                if minutes >= 1 and len(v.keys) == 1 and not v.segment:
                    ep = self.episodes.get(v.keys[0])
                    if ep is not None:
                        ep.set("runtime", minutes, "video")

    def match(self):
        self.match_notes = core.match_videos(self.videos, self.episodes)
        return self.match_notes

    def videos_for(self, key) -> list:
        return [v for v in self.videos if key in v.keys]


# ═══════════════════════════════════════════════════════════════════════
# CHANGES (the preview list, F10)
# ═══════════════════════════════════════════════════════════════════════

class Change:
    def __init__(self, path, kind, data=None, producer=None, keys=(), source="", note=""):
        self.path = os.path.normpath(path)
        self.kind = kind                 # nfo xml thumb meta_image tvshow series_xml season_nfo artwork season_poster
        self.data = data                 # str (text files) or bytes
        self.producer = producer         # callable(log) -> bytes (images), run at apply time
        self.keys = list(keys)
        self.source = source             # where the content comes from (shown in the preview)
        self.note = note
        self.exists = os.path.exists(self.path)
        self.action = "replace" if self.exists else "create"
        self.selected = True
        self.old_text = None

    def __repr__(self):
        return f"Change({self.action} {self.kind} {os.path.basename(self.path)})"


DEFAULT_OPTS = {
    "profiles": DEFAULT_PROFILES, "replace_text": True, "replace_images": False, "series_files": True,
    "artwork": True, "episode_images": True, "thumb_width": 1280, "pad_16_9": False,
    "image_order": ["existing", "csv", "tvdb", "tvmaze", "tmdb", "video"], "language": "pt-BR",
    "rating_country": "Brazil", "cast": True, "crew": True, "ratings": True, "techinfo": True, "image_urls": True,
    "outline": True, "name_pattern": "{show} - {code} - {title}", "max_title_len": 80, "out_dir": "",
    "text_only_missing": False,
}


def _same_text(a: str, b: str) -> bool:
    norm = lambda t: VOLATILE.sub("", (t or "").replace("﻿", "")).strip()
    return norm(a) == norm(b)


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8-sig", "replace")
    except OSError:
        return None


def existing_episode_image(video_path: str) -> str:
    """An image that already belongs to this video: -thumb, metadata/<stem>.jpg, <stem>.jpg, MCM's metadata/<hash>.jpg."""
    folder, stem = os.path.dirname(video_path), os.path.splitext(os.path.basename(video_path))[0]
    for ext in (".jpg", ".png", ".jpeg"):
        for p in (os.path.join(folder, stem + "-thumb" + ext), os.path.join(folder, "metadata", stem + ext),
                  os.path.join(folder, stem + ext)):
            if os.path.isfile(p):
                return p
    xml = _read(os.path.join(folder, "metadata", stem + ".xml"))
    if xml:
        m = re.search(r"<filename>/?([^<]+\.(?:jpg|png|jpeg))</filename>", xml, re.I)
        if m and os.path.isfile(os.path.join(folder, "metadata", m.group(1))):
            return os.path.join(folder, "metadata", m.group(1))
    return ""


def existing_meta_image(video_path: str) -> str:
    """The image of the XML format for this video: the one named in the existing metadata XML (Media Center Master
    uses TheTVDB's image name, e.g. 663ad3493cba8.jpg) or metadata/<stem>.jpg."""
    folder, stem = os.path.dirname(video_path), os.path.splitext(os.path.basename(video_path))[0]
    meta = os.path.join(folder, "metadata")
    xml = _read(os.path.join(meta, stem + ".xml"))
    if xml:
        m = re.search(r"<filename>/?([^<]+\.(?:jpg|png|jpeg))</filename>", xml, re.I)
        if m and os.path.isfile(os.path.join(meta, m.group(1))):
            return os.path.join(meta, m.group(1))
    for ext in (".jpg", ".png", ".jpeg"):
        if os.path.isfile(os.path.join(meta, stem + ext)):
            return os.path.join(meta, stem + ext)
    return ""


def plan(project: Project, opts=None, http=None, ffmpeg="") -> list:
    """Every file to write. Text is built now (cheap; compared with the file on disk); images get a producer that
    runs at apply time. Series-folder mode when the project has a folder and videos, else output-folder mode."""
    o = dict(DEFAULT_OPTS)
    o.update(opts or {})
    prof = o["profiles"]
    rules = project.rules
    wopts = {k: o[k] for k in ("cast", "crew", "ratings", "techinfo", "image_urls", "outline", "rating_country")}
    wopts["language"] = (o.get("language") or "").split("-")[0]
    changes = []
    nfo_on = any(p in prof for p in ("kodi", "jellyfin", "emby"))
    xml_on = "xml" in prof
    thumb_names = []
    if nfo_on:
        thumb_names.append("{stem}-thumb.jpg")
    if "plex" in prof:
        thumb_names.append("{stem}.jpg")

    def text_change(path, kind, text, keys=(), source=""):
        c = Change(path, kind, data=text, keys=keys, source=source)
        if c.exists:
            c.old_text = _read(path)
            if _same_text(c.old_text, text):
                c.action, c.selected = "same", False
            elif not o["replace_text"]:
                c.action, c.selected = "keep", False
        changes.append(c)

    folder_mode = bool(project.show_dir and project.videos)
    units = []          # (folder, stem, [episodes], video or None, season_dir)
    if folder_mode:
        for v in project.videos:
            eps = [project.episodes[k] for k in v.keys if k in project.episodes]
            if eps:
                units.append((v.folder, v.stem, eps, v, v.folder))
    else:
        out = o["out_dir"] or project.show_dir
        if not out:
            raise ValueError("choose an output folder or open a series folder")
        show = project.series.get("title", rules) or "Show"
        for ep in project.sorted_episodes():
            stem = core.episode_base_name(show, ep.season, [ep.episode], ep.get("title", rules) or "", None,
                                          project.series.get("year", rules), o["name_pattern"], o["max_title_len"])
            units.append((os.path.normpath(out), stem, [ep], None, os.path.normpath(out)))

    for folder, stem, eps, video, _sd in units:
        probe = video.probe if video else None
        keys = [e.key for e in eps]
        meta_dir = os.path.join(folder, "metadata")
        image_name = ""
        if o["episode_images"]:
            existing = existing_episode_image(video.path) if video else ""
            targets = [os.path.join(folder, n.format(stem=stem)) for n in thumb_names]
            if xml_on:
                mcm = existing_meta_image(video.path) if video else ""
                if mcm and not o["replace_images"]:
                    image_name = os.path.basename(mcm)          # keep the existing XML image and its <filename>
                else:
                    image_name = stem + ".jpg"
                    targets.append(os.path.join(meta_dir, image_name))
            prod = _episode_image_producer(project, eps[0], video, existing, o, http, ffmpeg)
            user_img = any(s == "user" for _, s in eps[0].images)
            for t in targets:
                c = Change(t, "meta_image" if os.path.dirname(t) == meta_dir else "thumb", producer=prod, keys=keys,
                           source=prod.describe)
                if c.exists and not o["replace_images"] and not user_img:
                    c.action, c.selected = "keep", False
                if prod.describe == "none":
                    c.action, c.selected, c.note = "skip", False, "no image source (no video frame, no URL)"
                changes.append(c)
        if nfo_on:
            text = (fill_legacy(o["nfo_template_text"], eps, project.series, probe, image_name, rules)
                    if o.get("nfo_template_text") else W.episode_nfo(eps, project.series, probe, wopts, rules))
            text_change(os.path.join(folder, stem + ".nfo"), "nfo", text, keys,
                        "your template" if o.get("nfo_template_text") else "")
        if xml_on:
            text = (fill_legacy(o["xml_template_text"], eps, project.series, probe, image_name, rules, xml=True)
                    if o.get("xml_template_text") else
                    W.episode_xml(eps, project.series, probe, image_name, wopts, rules))
            text_change(os.path.join(meta_dir, stem + ".xml"), "xml", text, keys,
                        "your template" if o.get("xml_template_text") else "")

    root = project.show_dir if folder_mode else (o["out_dir"] or project.show_dir)
    if o["series_files"] and root:
        if nfo_on:
            text_change(os.path.join(root, "tvshow.nfo"), "tvshow",
                        W.tvshow_nfo(project.series, project.seasons, wopts, rules))
        if xml_on:
            text_change(os.path.join(root, "series.xml"), "series_xml", W.series_xml(project.series, wopts, rules))
        if folder_mode and ("jellyfin" in prof or "emby" in prof):
            season_dirs = {}
            for v in project.videos:
                for k in v.keys:
                    if os.path.normcase(v.folder) != os.path.normcase(project.show_dir):
                        season_dirs.setdefault(k[0], v.folder)
            for n, sdir in sorted(season_dirs.items()):
                text_change(os.path.join(sdir, "season.nfo"), "season_nfo",
                            W.season_nfo(project.season(n), project.series, wopts, rules))
    if o["artwork"] and root:
        changes += _artwork_changes(project, root, prof, o, http)
    return changes


class _Producer:
    def __init__(self, fn, describe):
        self.fn, self.describe = fn, describe
        self._cache = None

    def __call__(self, log=None):
        if self._cache is None:
            self._cache = self.fn(log)
        return self._cache


def _episode_image_producer(project, ep, video, existing, o, http, ffmpeg) -> _Producer:
    """Sources in the configured order: existing file → CSV image → TheTVDB → TVmaze → TMDb → frame from the video."""
    import kfg_media
    steps = []
    for url, s in ep.images:                       # an image the user chose always comes first
        if s == "user" and os.path.isfile(url):
            steps.append(("your image", lambda p=url: open(p, "rb").read()))
    for src in o["image_order"]:
        if src == "existing" and existing and not o["replace_images"]:
            steps.append(("existing file", lambda p=existing: open(p, "rb").read()))
        elif src in ("csv", "tvdb", "tvmaze", "tmdb"):
            for url, s in ep.images:
                if s == src or (src == "csv" and s in ("csv", "paste")):
                    if url.lower().startswith("http") and http is not None:
                        steps.append((f"{core.SOURCE_LABELS.get(s, s)} image",
                                      lambda u=url: __import__("kfg_sources").download(http, u)))
                    elif os.path.isfile(url):
                        steps.append(("image file from the CSV", lambda p=url: open(p, "rb").read()))
        elif src == "video" and video is not None and ffmpeg:
            dur = (video.probe or {}).get("duration_s") or 0

            def grab(v=video, d=dur):
                img, _ = kfg_media.best_frame(ffmpeg, v.path, d)
                return img
            steps.append(("frame from the video", grab))
    if not steps:
        return _Producer(lambda log=None: None, "none")

    def run(log=None):
        last = None
        for desc, fn in steps:
            try:
                res = fn()
                img = res if hasattr(res, "size") else kfg_media.image_from_bytes(res)
                img = kfg_media.make_thumb(img, o["thumb_width"], o["pad_16_9"])
                run.used = desc
                return kfg_media.jpeg_bytes(img)
            except Exception as e:
                last = e
                if log:
                    log(f"   image source '{desc}' failed for {ep.code}: {e}", "warn")
        raise RuntimeError(f"no image could be made for {ep.code}: {last}")
    return _Producer(run, steps[0][0] + (f" (then {len(steps) - 1} fallback(s))" if len(steps) > 1 else ""))


def _artwork_changes(project, root, prof, o, http) -> list:
    import kfg_media
    import kfg_sources
    out = []
    order = ["user", "local", "tvdb", "tmdb", "tvmaze"]

    def pick(items, chosen=None):
        if chosen:
            return chosen
        for src in order:
            for u, s in items or []:
                if s == src:
                    return u
        return (items or [(None, None)])[0][0]

    def producer(src, png):
        def run(log=None):
            data = open(src, "rb").read() if os.path.isfile(src) else kfg_sources.download(http, src)
            img = kfg_media.image_from_bytes(data)
            if png:
                import io
                buf = io.BytesIO()
                img.save(buf, "PNG")
                return buf.getvalue()
            return kfg_media.jpeg_bytes(img, 92)
        return _Producer(run, ("local file " + os.path.basename(src)) if os.path.isfile(src) else src)

    for kind in ART_KINDS:
        src = pick(project.series.artwork.get(kind), project.series.chosen_art.get(kind))
        if not src or (not os.path.isfile(src) and http is None):
            continue
        for name in art_names(kind, prof):
            c = Change(os.path.join(root, name), "artwork", producer=producer(src, name.endswith(".png")),
                       source=f"{kind}: " + (os.path.basename(src) if os.path.isfile(src) else src))
            if c.exists and not o["replace_images"]:
                c.action, c.selected = "keep", False
            out.append(c)
    extra = [u for u, s in project.series.artwork.get("fanart", [])][1:20]
    if extra and any(p in prof for p in ("jellyfin", "emby", "xml")) and o.get("extra_fanart", True):
        for i, u in enumerate(extra[:9], 1):
            c = Change(os.path.join(root, f"backdrop{i}.jpg"), "artwork", producer=producer(u, False),
                       source=f"extra fanart {i}")
            if c.exists and not o["replace_images"]:
                c.action, c.selected = "keep", False
            out.append(c)
    season_dirs = {}
    for v in project.videos:
        for k in v.keys:
            season_dirs.setdefault(k[0], v.folder)
    for n, season in sorted(project.seasons.items()):
        src = pick(season.posters)
        if not src or (not os.path.isfile(src) and http is None):
            continue
        for t in season_poster_targets(root, season_dirs.get(n), n, prof):
            c = Change(t, "season_poster", producer=producer(src, False), source=f"season {n} poster")
            if c.exists and not o["replace_images"]:
                c.action, c.selected = "keep", False
            out.append(c)
    return out


# ═══════════════════════════════════════════════════════════════════════
# APPLY, BACKUP, UNDO (F10)
# ═══════════════════════════════════════════════════════════════════════

def backup_root(project_dir: str) -> str:
    return os.path.join(project_dir, ".kfg_backup")


def apply(changes, root_dir: str, log=print, progress=None, cancel=None) -> dict:
    """Write the selected changes. Replaced files are copied to <root>/.kfg_backup/<time>/ first; a manifest lists every
    action (for Undo). Text is parsed back before it is written (F9). Returns the summary."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(backup_root(root_dir), stamp)
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "root": root_dir, "entries": []}
    todo = [c for c in changes if c.selected and c.action in ("create", "replace")]
    stats = {"written": 0, "failed": 0, "skipped": len(changes) - len(todo), "errors": [], "manifest": ""}
    for i, c in enumerate(todo, 1):
        if cancel and cancel():
            log("Cancelled.", "warn")
            break
        if progress:
            progress(i, len(todo), os.path.basename(c.path))
        try:
            if c.data is not None:
                data = c.data
                err = W.parse_check(data)
                if err:
                    raise ValueError(f"the generated XML does not parse: {err}")
                enc = W.NFO_ENCODING if c.path.lower().endswith(".nfo") else W.XML_ENCODING
                payload = data.encode(enc)
            else:
                payload = c.producer(log)
                if payload is None:
                    raise RuntimeError("no content")
            entry = {"action": "create", "path": c.path}
            if os.path.exists(c.path):
                rel = os.path.relpath(c.path, root_dir) if c.path.lower().startswith(root_dir.lower()) else \
                    re.sub(r"[:\\/]+", "_", c.path)
                bpath = os.path.join(bdir, rel)
                os.makedirs(os.path.dirname(bpath), exist_ok=True)
                shutil.copy2(c.path, bpath)
                entry = {"action": "replace", "path": c.path, "backup": bpath}
            W.atomic_write(c.path, payload)
            st = os.stat(c.path)
            entry.update(size=st.st_size, mtime=st.st_mtime)
            manifest["entries"].append(entry)
            stats["written"] += 1
            log(f"✓ {c.action:7s} {c.path}", "ok")
        except Exception as e:
            stats["failed"] += 1
            stats["errors"].append(f"{os.path.basename(c.path)}: {e}")
            log(f"✗ {os.path.basename(c.path)}: {e}", "error")
    if manifest["entries"]:
        os.makedirs(bdir, exist_ok=True)
        mp = os.path.join(bdir, "manifest.json")
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
        stats["manifest"] = mp
    return stats


def last_manifest(root_dir: str) -> str:
    b = backup_root(root_dir)
    if not os.path.isdir(b):
        return ""
    runs = sorted(d for d in os.listdir(b) if os.path.isfile(os.path.join(b, d, "manifest.json"))
                  and not os.path.isfile(os.path.join(b, d, "undone.txt")))
    return os.path.join(b, runs[-1], "manifest.json") if runs else ""


def undo(manifest_path: str, log=print) -> dict:
    """Restore a run: replaced files come back from the backup; created files are moved into the backup folder
    ('undone'), never deleted; renames are reversed. A file changed since the run is left alone and reported."""
    with open(manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    bdir = os.path.dirname(manifest_path)
    stats = {"restored": 0, "skipped": 0, "messages": []}
    renames = [(e["new"], e["path"]) for e in m["entries"] if e["action"] == "rename"]
    if renames:
        gone = [os.path.basename(a) for a, _ in renames if not os.path.exists(a)]
        if gone:
            stats["skipped"] += len(renames)
            stats["messages"].append("renamed files are not where the run left them: " + ", ".join(gone[:5]))
        else:
            err = _two_phase(renames, log)
            if err:
                stats["skipped"] += len(renames)
                stats["messages"].append(err)
            else:
                stats["restored"] += len(renames)
    for e in reversed(m["entries"]):
        p = e["path"]
        if e["action"] == "rename":
            continue
        try:
            if not os.path.exists(p):
                raise RuntimeError("the file is gone")
            st = os.stat(p)
            if st.st_size != e.get("size") or abs(st.st_mtime - e.get("mtime", 0)) > 2:
                raise RuntimeError("changed since the run — left as it is")
            if e["action"] == "replace":
                shutil.copy2(e["backup"], p)
            else:
                dest = os.path.join(bdir, "undone", os.path.relpath(p, m["root"]) if p.lower().startswith(
                    m["root"].lower()) else os.path.basename(p))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(p, dest)
            stats["restored"] += 1
        except Exception as ex:
            stats["skipped"] += 1
            stats["messages"].append(f"{os.path.basename(p)}: {ex}")
            log(f"⚠ {os.path.basename(p)}: {ex}", "warn")
    with open(os.path.join(bdir, "undone.txt"), "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat(timespec="seconds"))
    return stats


# ═══════════════════════════════════════════════════════════════════════
# HEALTH CHECK (F9)
# ═══════════════════════════════════════════════════════════════════════

def health(project: Project, changes=None, max_path=240) -> list:
    """[(level, where, text)] level: error | warn | info."""
    out = []
    rules = project.rules
    folder_mode = bool(project.show_dir and project.videos)
    by_key = {}
    for v in project.videos:
        if not v.keys:
            out.append(("warn", os.path.basename(v.path), "video without an episode (no number or title match)"))
        for k in v.keys:
            by_key.setdefault(k, []).append(v)
    for k, vs in by_key.items():
        if len(vs) > 1 and not all(v.segment for v in vs):
            out.append(("error", core.episode_code(*k), "several videos for one episode: "
                        + ", ".join(os.path.basename(v.path) for v in vs)))
    seasons, no_video = {}, {}
    for ep in project.sorted_episodes():
        seasons.setdefault(ep.season, []).append(ep)
        if folder_mode and ep.key not in by_key and ep.season != 0:
            no_video.setdefault(ep.season, []).append(ep.episode)
        if not ep.get("title", rules):
            out.append(("warn", ep.code, "no title"))
        rt, probe_rt = ep.values.get("runtime", {}).get("csv") or ep.values.get("runtime", {}).get("tvdb"), \
            ep.values.get("runtime", {}).get("video")
        if rt and probe_rt and abs(rt - probe_rt) > max(3, 0.2 * probe_rt):
            out.append(("warn", ep.code, f"runtime {rt} min in the data, the video lasts {probe_rt} min"))
        for f in ("title", "aired"):
            if ep.conflicts(f) and "user" not in ep.all(f):
                vals = "; ".join(f"{core.SOURCE_LABELS.get(s, s)}: {v}" for s, v in ep.all(f).items())
                out.append(("info", ep.code, f"sources disagree on {f} — {vals}"))
    for n, eps in seasons.items():
        if n == 0:
            continue
        nums = sorted(e.episode for e in eps)
        gaps = sorted(set(range(1, nums[-1] + 1)) - set(nums))
        if gaps:
            out.append(("warn", f"Season {n}", "missing episode numbers: " + ", ".join(str(g) for g in gaps[:20])))
        dates = [(e.episode, e.get("aired", rules)) for e in sorted(eps, key=lambda e: e.episode) if e.get("aired", rules)]
        for (e1, d1), (e2, d2) in zip(dates, dates[1:]):
            if d2 < d1:
                out.append(("info", f"S{n:02d}E{e2:02d}", f"aired {d2}, before episode {e1} ({d1})"))
    for n, nums in sorted(no_video.items()):        # one line per season, not one per episode
        total = len(seasons.get(n, []))
        what = "all episodes" if len(nums) == total else _ranges(nums)
        out.append(("info", f"Season {n}", f"no video for {len(nums)} of {total} episode(s): {what}"))
    for v, text in project.match_notes:
        out.append(("warn", os.path.basename(v.path), text))
    for c in changes or []:
        if len(c.path) > max_path:
            out.append(("error", os.path.basename(c.path), f"path is {len(c.path)} characters (Windows limit 260)"))
    if project.show_dir:
        for v in project.videos:
            for p in (os.path.splitext(v.path)[0] + ".nfo", os.path.join(v.folder, "metadata", v.stem + ".xml")):
                t = _read(p)
                if t and W.parse_check(t):
                    out.append(("warn", os.path.basename(p), "existing file is not valid XML: " + W.parse_check(t)))
        for fn in ("tvshow.nfo", "series.xml"):
            t = _read(os.path.join(project.show_dir, fn))
            if t and W.parse_check(t):
                out.append(("warn", fn, "existing file is not valid XML: " + W.parse_check(t)))
    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda x: order.get(x[0], 3))


def _ranges(nums) -> str:
    """[1, 2, 3, 7, 9, 10] → 'E01–E03, E07, E09–E10'."""
    nums, out, start = sorted(nums), [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            out.append(f"E{start:02d}" + (f"–E{n:02d}" if n != start else ""))
            start = None
    return ", ".join(out)


# ═══════════════════════════════════════════════════════════════════════
# CSV EXPORT (F16.4) AND LEGACY TEMPLATES (F5.3)
# ═══════════════════════════════════════════════════════════════════════

CSV_COLUMNS = ["ID", "Season", "Episode", "Episode_Title", "TVDB_Episode_ID", "Originally_Aired", "Runtime_min",
               "Content_Rating", "IMDB_ID", "Description", "Directors", "Writers", "Guest_Stars", "Network", "Image",
               "Notes"]


def export_csv(project: Project, path: str, delimiter=";") -> int:
    """The episode list with the standard R4 columns (+ people, image and a Notes column listing the conflicts),
    UTF-8 with BOM so Excel shows accents. Returns the number of rows."""
    import csv
    rules = project.rules
    rows = []
    for ep in project.sorted_episodes():
        g = lambda f: ep.get(f, rules)
        notes = []
        for f in ("title", "aired", "runtime"):
            if ep.conflicts(f):
                notes.append(f"{f}: " + "; ".join(f"{core.SOURCE_LABELS.get(s, s)}={v}" for s, v in ep.all(f).items()))
        guests = [x[0] if isinstance(x, (list, tuple)) else x for x in g("guests") or []]
        rows.append([ep.absolute or "", ep.season, ep.episode, g("title") or "", g("tvdb_id") or "", g("aired") or "",
                     g("runtime") or "", g("mpaa") or "", g("imdb_id") or "", g("plot") or "",
                     " | ".join(g("directors") or []), " | ".join(g("writers") or []), " | ".join(guests),
                     g("network") or "", next((u for u, _ in ep.images), ""), " / ".join(notes)])
    buf = __import__("io").StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    w.writerows(rows)
    W.atomic_write(path, buf.getvalue().encode("utf-8-sig"))
    return len(rows)


def template_values(eps, series, probe=None, image_name="", rules=None) -> dict:
    """Values for the R4-style templates: NFO and XML tag names and {{placeholders}}."""
    ep = eps[0]
    g = lambda f: ep.get(f, rules) or ""
    rt = g("runtime")
    secs = (probe or {}).get("duration_s") or (int(rt) * 60 if str(rt).isdigit() else "")
    return {"title": g("title"), "season": ep.season, "episode": ep.episode, "aired": g("aired"), "runtime": rt,
            "durationinseconds": secs, "mpaa": g("mpaa"), "plot": g("plot"), "ID": ep.absolute or ep.episode,
            "EpisodeID": g("tvdb_id"), "SeasonNumber": ep.season, "EpisodeNumber": ep.episode,
            "EpisodeName": g("title"), "FirstAired": g("aired"), "IMDB_ID": g("imdb_id"), "Overview": g("plot"),
            "VideoLength": rt, "VideoLengthSeconds": secs, "Duration": rt, "DurationSeconds": secs,
            "filename": ("/" + image_name) if image_name else "", "showtitle": series.get("title", rules) or "",
            "tvdb_id": g("tvdb_id"), "imdb_id": g("imdb_id")}


def fill_legacy(template: str, eps, series, probe=None, image_name="", rules=None, xml=False) -> str:
    vals = template_values(eps, series, probe, image_name, rules)
    out = template
    for kind, val in (("tvdb", vals["tvdb_id"]), ("imdb", vals["imdb_id"])):
        out = re.sub(rf'(<uniqueid\b[^>]*type="{kind}"[^>]*>)[^<]*(</uniqueid>)',
                     lambda m: m.group(1) + W._txt(val) + m.group(2), out)
    out = W.fill_template(out, vals)          # also drops the IDs that stayed empty (B11)
    if xml and not out.lstrip().startswith("<?xml"):
        out = '<?xml version="1.0" encoding="UTF-8"?>\n' + out
    return out


# ═══════════════════════════════════════════════════════════════════════
# RENAMER (F11) — optional; the default keeps the video names
# ═══════════════════════════════════════════════════════════════════════

def sidecars(video_path: str) -> list:
    """(old path, suffix) of every file that belongs to the video: subtitles with language tags, .nfo, -thumb, .jpg,
    metadata/<stem>.xml/.jpg, .edl/.bif."""
    folder, stem = os.path.dirname(video_path), os.path.splitext(os.path.basename(video_path))[0]
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        p = os.path.join(folder, n)
        if p == video_path or not os.path.isfile(p):
            continue
        if n.startswith(stem + ".") or n.startswith(stem + "-thumb."):
            out.append((p, n[len(stem):]))
    meta = os.path.join(folder, "metadata")
    if os.path.isdir(meta):
        for n in os.listdir(meta):
            if os.path.splitext(n)[0] == stem:
                out.append((os.path.join(meta, n), "|meta|" + os.path.splitext(n)[1]))
    return out


def plan_renames(project: Project, pattern="{show} - {code} - {title}", max_len=80) -> list:
    """[(old, new)] for every matched video and its sidecars; collisions within the batch are reported."""
    rules = project.rules
    show = project.series.get("title", rules) or "Show"
    year = project.series.get("year", rules)
    out = []
    for v in project.videos:
        if not v.keys:
            continue
        eps = [project.episodes[k] for k in v.keys if k in project.episodes]
        if not eps:
            continue
        title = " / ".join(e.get("title", rules) or "" for e in eps) if len(eps) > 1 else eps[0].get("title", rules)
        new_stem = core.episode_base_name(show, eps[0].season, [e.episode for e in eps], title or "", v.segment, year,
                                          pattern, max_len)
        if new_stem == v.stem:
            continue
        ext = os.path.splitext(v.path)[1]
        out.append((v.path, os.path.join(v.folder, new_stem + ext)))
        for p, suffix in sidecars(v.path):
            if suffix.startswith("|meta|"):
                out.append((p, os.path.join(v.folder, "metadata", new_stem + suffix[6:])))
            else:
                out.append((p, os.path.join(v.folder, new_stem + suffix)))
    return out


def rename_conflicts(pairs) -> list:
    olds = {os.path.normcase(a) for a, _ in pairs}
    news, out = {}, []
    for a, b in pairs:
        nb = os.path.normcase(b)
        if nb in news:
            out.append(f"two files would get the name {os.path.basename(b)}")
        news[nb] = a
        if os.path.exists(b) and nb not in olds and nb != os.path.normcase(a):
            out.append(f"{os.path.basename(b)} already exists")
    return out


def _two_phase(pairs, log=print) -> str:
    """Rename every file to a temporary name first, then to its new name. '' on success; on a failure everything
    done so far is rolled back and the error is returned."""
    tmp_pairs, done = [], []
    try:
        for i, (a, b) in enumerate(pairs):
            t = f"{a}.__kfg_{i}_{int(time.time())}__"
            os.rename(a, t)
            done.append((a, t))
            tmp_pairs.append((t, b))
        for t, b in tmp_pairs:
            if os.path.exists(b):
                raise FileExistsError(f"{os.path.basename(b)} already exists")
            os.makedirs(os.path.dirname(b), exist_ok=True)
            os.rename(t, b)
            done.append((t, b))
    except Exception as e:
        log(f"✗ rename failed ({e}) — rolling back", "error")
        for x, y in reversed(done):
            try:
                os.rename(y, x)
            except OSError as e2:
                log(f"✗ could not roll back {os.path.basename(y)}: {e2}", "error")
        return str(e)
    return ""


def apply_renames(pairs, root_dir: str, log=print) -> dict:
    """Two phases through temporary names (handles swaps, cycles and case-only renames — B15, B16); a failure rolls
    back what was done. The manifest makes it undoable."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(backup_root(root_dir), stamp)
    err = _two_phase(pairs, log)
    if err:
        return {"renamed": 0, "error": err, "manifest": ""}
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "root": root_dir,
                "entries": [{"action": "rename", "path": a, "new": b} for a, b in pairs]}
    os.makedirs(bdir, exist_ok=True)
    mp = os.path.join(bdir, "manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    for a, b in pairs:
        log(f"✓ {os.path.basename(a)}  →  {os.path.basename(b)}", "ok")
    return {"renamed": len(pairs), "error": "", "manifest": mp}
