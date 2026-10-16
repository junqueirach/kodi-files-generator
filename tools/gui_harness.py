# GUI harness (v0.5.0): drives the real window with a fake network (saved TheTVDB pages, generated images) and
# a temporary series folder made of FFmpeg test videos. USERPROFILE/HOME must point to an empty temp folder whose
# path contains "emptyhome". Also opens the read-only reference folder (scan + plan only, nothing written).
# Usage: python tools/gui_harness.py <app.py> <work folder> <screenshots folder> [<read-only reference series>]
import ctypes, importlib.util, io, json, os, shutil, sys, time
from ctypes import wintypes
from pathlib import Path

app_path, work, shots = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
reference = Path(sys.argv[4]) if len(sys.argv) > 4 else None
assert "emptyhome" in os.environ["USERPROFILE"], "USERPROFILE must point to the temp home"
home = Path(os.environ["USERPROFILE"])
for p in home.glob(".kodi_files_generator*"):
    shutil.rmtree(p) if p.is_dir() else p.unlink()
shots.mkdir(parents=True, exist_ok=True)
for p in shots.glob("*.png"):
    p.unlink()
sys.path.insert(0, str(app_path.parent))
spec = importlib.util.spec_from_file_location("kfg_app", app_path)
kg = importlib.util.module_from_spec(spec)
sys.modules["kfg_app"] = kg
spec.loader.exec_module(kg)
kg.set_dpi_awareness()
import kfg_media, kfg_sources                       # noqa: E402
from PIL import Image, ImageDraw, ImageGrab          # noqa: E402

FIX = app_path.parent / "tests" / "fixtures"
FF, FP = kfg_media.find_ffmpeg()
assert FF, "FFmpeg is needed for the harness"


def fake_image(url):
    w, h = (1000, 1500) if "poster" in url else (1920, 1080) if "fanart" in url or "background" in url else \
        (758, 140) if "graphical" in url or "banner" in url else (800, 310) if "clearlogo" in url else (640, 360)
    mode = "RGBA" if "clearlogo" in url or "clearart" in url else "RGB"
    im = Image.new(mode, (w, h), (40, 80, 160, 0) if mode == "RGBA" else (40, 80, 160))
    d = ImageDraw.Draw(im)
    d.rectangle([w // 8, h // 8, w * 7 // 8, h * 7 // 8], fill=(230, 180, 60, 255))
    d.text((w // 4, h // 2), url[-28:], fill=(0, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG" if mode == "RGBA" else "JPEG")
    return buf.getvalue()


calls = []


def opener(url, headers):
    calls.append(url)
    if "artworks.thetvdb.com" in url:
        return 200, fake_image(url), url
    if "dereferrer/series/73255" in url or url.rstrip("/") == "https://thetvdb.com/series/house":
        return 200, (FIX / "tvdb_house_series.html").read_bytes(), "https://thetvdb.com/series/house"
    if url.endswith("/series/house/allseasons/official"):
        return 200, (FIX / "tvdb_house_allseasons.html").read_bytes(), url
    if "/series/house/episodes/" in url:
        return 200, (FIX / "tvdb_house_episode.html").read_bytes(), url
    return 404, b"", url


# ── the temporary series folder: House with 3 episodes + 1 special (FFmpeg test videos) ──
if work.exists():
    shutil.rmtree(work)
show = work / "House (2004)"
for rel in ("Season 1/House - S01E01 - Pilot.mkv", "Season 1/House - S01E02 - Paternity.mkv",
            "Season 1/House.S01E03.Occams.Razor.720p.mkv", "Specials/House - S00E04 - Get Happy.mkv"):
    p = show / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    kfg_media.make_test_video(FF, str(p), 12, "640x360", 2)
(show / "Season 1" / "House - S01E01 - Pilot.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n",
                                                                encoding="utf-8")
csv_pt = work / "house_pt.csv"
csv_pt.write_text("Temporada;Episódio;Título;Sinopse;Exibição\n1;1;Piloto;Uma professora desmaia na sala.;16/11/2004\n"
                  "1;2;Paternidade;Um jovem jogador de lacrosse adoece.;23/11/2004\n", encoding="cp1252")

asked = []
kg.messagebox.askyesno = lambda *a, **k: (asked.append(a[1][:60]), True)[1]
kg.messagebox.showinfo = lambda *a, **k: asked.append(("info", a[1][:60]))
kg.messagebox.showerror = lambda *a, **k: asked.append(("error", a[1][:80]))

app = kg.App()
app.http = kfg_sources.Http(opener=opener)
app.mapping_dialog = lambda table, mapping, name: mapping          # accept the suggestions
log, checks = [], []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), str(detail)))


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def idle(timeout=120):
    end = time.time() + timeout
    pump(0.3)
    while app.busy and time.time() < end:
        app.update()
        time.sleep(0.03)
    pump(0.5)


def frame_rect(win):
    r = wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(int(win.wm_frame(), 16)), 9, ctypes.byref(r),
                                               ctypes.sizeof(r))
    return r.left, r.top, r.right, r.bottom


def shot(name, win=None):
    w = win or app
    w.attributes("-topmost", True)
    w.lift()
    pump(0.6)
    ImageGrab.grab(bbox=frame_rect(w), all_screens=True).save(shots / f"{name}.png")
    w.attributes("-topmost", False)
    log.append(name)


def tops():
    return [w for w in app.winfo_children() if isinstance(w, kg.tk.Toplevel) and w.winfo_exists()]


def close_tops():
    for w in tops():
        w.destroy()
    pump(0.2)


def logtext():
    return app.log_text.get("1.0", "end")


app.geometry("1400x860+10+10")
pump(1.0)
check("title", app.title() == "Kodi Files Generator v0.5.0", app.title())
check("drag and drop is on (tkinterdnd2 drop targets registered)", app._dnd_ok)
app._on_drop([str(show)])                      # what a drop of the series folder does
idle()
check("dropping the series folder opens it", len(app.project.videos) == 4, len(app.project.videos))
shot("01_start")

# open the series folder
app.open_series(str(show))
idle()
check("4 videos found", len(app.project.videos) == 4, len(app.project.videos))
check("series name and year from the folder", (app.project.series.get("title"), app.project.series.get("year"))
      == ("House", 2004))
check("ffprobe read every video", all(v.probe for v in app.project.videos))

# paste from TheTVDB (the user's format)
app.clipboard_clear()
app.clipboard_append((FIX / "tvdb_paste_sample_house.txt").read_text(encoding="utf-8"))
app.import_paste()
pump(0.5)
check("paste imported", app.project.episodes[(1, 2)].get("plot", app.project.rules).startswith("When a teenage"))
check("special placed before S07E16", app.project.episodes[(0, 4)].display == (7, 16))

# a Portuguese CSV (';', cp1252) → conflicts with the paste
app.import_file(str(csv_pt))
pump(0.5)
ep1 = app.project.episodes[(1, 1)]
check("CSV title wins (rules)", ep1.get("title", app.project.rules) == "Piloto", ep1.get("title"))
check("conflict listed", ep1.conflicts("title"))

# TheTVDB link (saved pages served by the fake network)
app.link_dialog = lambda: ("tvdb", "https://thetvdb.com/dereferrer/series/73255", False)
kg.messagebox.askyesno = lambda *a, **k: (asked.append(a[1][:60]), False)[1]       # do not export the CSV now
app.import_link()
idle()
kg.messagebox.askyesno = lambda *a, **k: (asked.append(a[1][:60]), True)[1]
check("TheTVDB IDs merged", ep1.get("tvdb_id") == "110994", ep1.get("tvdb_id"))
check("TheTVDB series data", app.project.series.get("network") == "FOX" and app.project.series.get("tvdb_id") == "73255")
check("artwork found", len(app.project.series.artwork.get("poster", [])) > 5)
check("S01E03 matched by number", app.project.videos_for((1, 3)) != [])
app.nb.select(0)
app.ep_tree.selection_set("1:1")
pump(0.6)
shot("02_episodes")
app.nb.select(1)
pump(0.4)
shot("03_sources")
# choose the TheTVDB title for S01E01 in the Sources tab
app.project.episodes[(1, 1)].set_user("title", "Pilot")
app.refresh_sources_tab()
app.nb.select(2)
app.art_tree.selection_set("poster")
pump(1.5)
shot("04_artwork")

# generate all → changes → write
app.generate_all()
idle()
ch = app.changes
check("changes planned", len(ch) > 20, len(ch))
kinds = {c.kind for c in ch}
check("every kind of file planned", {"nfo", "xml", "thumb", "meta_image", "tvshow", "series_xml", "season_nfo",
                                      "artwork"} <= kinds, kinds)
app.ch_tree.selection_set(next(str(i) for i, c in enumerate(ch) if c.kind == "nfo"))
pump(0.4)
shot("05_changes")
app.write_changes()
idle(240)
d = tops()
check("written dialog", d and "written" in d[0].title().lower(), [w.title() for w in d])
if d:
    shot("06_written", d[0])
close_tops()
s1 = show / "Season 1"
for stem in ("House - S01E01 - Pilot", "House - S01E02 - Paternity", "House.S01E03.Occams.Razor.720p"):
    nfo = s1 / f"{stem}.nfo"
    xml = s1 / "metadata" / f"{stem}.xml"
    check(f"{stem}: nfo + xml + both images", nfo.exists() and xml.exists() and (s1 / f"{stem}-thumb.jpg").exists()
          and (s1 / "metadata" / f"{stem}.jpg").exists())
n1 = (s1 / "House - S01E01 - Pilot.nfo").read_text(encoding="utf-8-sig")
check("NFO: user title, TheTVDB ID, streamdetails", "<title>Pilot</title>" in n1 and ">110994</uniqueid>" in n1
      and "<codec>h264</codec>" in n1, n1[:300])
sp = (show / "Specials" / "House - S00E04 - Get Happy.nfo").read_text(encoding="utf-8-sig")
check("special: displayseason/displayepisode", "<displayseason>7</displayseason>" in sp
      and "<displayepisode>16</displayepisode>" in sp)
tv = (show / "tvshow.nfo").read_text(encoding="utf-8-sig")
check("tvshow.nfo: cast with roles, genres, tvdb id", "<role>James Wilson</role>" in tv and "<genre>Drama</genre>" in tv
      and '"tvdb":"73255"' in tv)
for n in ("poster.jpg", "folder.jpg", "fanart.jpg", "backdrop.jpg", "banner.jpg", "clearlogo.png", "logo.png",
          "series.xml", "Season 1/season.nfo", "Season 1/House - S01E01 - Pilot.en.srt"):
    check(f"exists: {n}", (show / n).exists())
import kfg_writers                                   # noqa: E402
bad = [p.name for p in show.rglob("*") if p.suffix in (".nfo", ".xml")
       and kfg_writers.parse_check(p.read_text(encoding="utf-8-sig"))]
check("every written NFO / XML parses", not bad, bad)

# check tab, second plan = nothing to do
app.run_check()
pump(0.4)
shot("07_check")
again = app.engine.plan(app.project, app._opts(), app.http, app.ffmpeg)
check("second plan writes nothing", all(c.action in ("same", "keep") for c in again),
      sorted({c.action for c in again}))

# dialogs
app.show_about()
pump(0.6)
d = tops()
check("about rows", getattr(app, "_about_rows", []) == ["Created by", "Contact", "GitHub", "Licence"])
if d:
    shot("08_about", d[0])
close_tops()
app.show_settings(1)
pump(0.6)
if tops():
    shot("09_settings_sources", tops()[0])
close_tops()
app.show_help()
pump(0.5)
if tops():
    shot("10_help", tops()[0])
close_tops()

# undo
app.undo_last()
pump(0.5)
check("undo removed the files of the run", not (show / "tvshow.nfo").exists()
      and not (s1 / "House - S01E01 - Pilot.nfo").exists() and (s1 / "House - S01E01 - Pilot.en.srt").exists())

# read-only reference: scan + plan only
if reference and reference.exists():
    before = sorted((str(p), p.stat().st_size, p.stat().st_mtime) for p in reference.rglob("*") if p.is_file())
    import kfg_engine                                # noqa: E402
    proj = kfg_engine.Project()
    proj.scan(str(reference), FP)
    plan = kfg_engine.plan(proj, {"artwork": False}, None, "")
    keep_mcm = [c for c in plan if c.kind == "xml" and "<filename>/66" in (c.data or "")]
    check("reference: MCM episode images are kept and referenced", len(keep_mcm) >= 1, len(keep_mcm))
    check("reference: existing thumbs reused, not regenerated",
          all(c.action in ("keep", "skip") for c in plan if c.kind == "thumb"))
    hl = kfg_engine.health(proj, plan)
    check("reference: MCM's broken tvshow.nfo reported", any("tvshow.nfo" in w for _, w, _ in hl))
    after = sorted((str(p), p.stat().st_size, p.stat().st_mtime) for p in reference.rglob("*") if p.is_file())
    check("reference folder unchanged", before == after)

app._on_close()
cfg = json.loads((home / ".kodi_files_generator.json").read_text(encoding="utf-8"))
check("settings saved (mapping remembered)", cfg.get("csv_mappings") and cfg.get("profiles"))
check("no unexpected errors", not [a for a in asked if isinstance(a, tuple) and a[0] == "error"], asked)
for n, r, d in checks:
    print(("PASS " if r else "FAIL ") + n + (f"  [{d[:220]}]" if d and not r else ""))
print("shots:", ", ".join(log))
print("ALL PASS" if all(c[1] for c in checks) else "SOME FAILED")
