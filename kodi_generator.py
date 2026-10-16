#!/usr/bin/env python3
"""
Kodi Files Generator v0.5.0 — series metadata for Kodi, Jellyfin, Emby and the XML format (MediaBrowser /
Media Center Master), without TheTVDB's paid API.

v0.5.0 changes (one large revision of R4):
  • Open a series folder: every episode, its video and its files in one table
  • Episode data from a CSV / Excel file (any column names, any encoding, ; or ,), a paste from TheTVDB's
    "All seasons" page, or a link (TheTVDB, TVmaze, IMDb, TMDb) — the app can build the CSV
  • Several databases: values compared per field, rules decide, your choice always wins
  • Built-in writers: episode NFO and XML, tvshow.nfo, series.xml, season.nfo — with cast, crew, guest
    stars, ratings, all IDs and the real codec / duration from ffprobe (templates still possible)
  • Episode images for every server (-thumb.jpg and metadata\\<name>.jpg) from the databases or the best
    frame of the video; series and season artwork
  • Preview of every change, backups, Undo; health check; optional renamer that moves subtitles too
  • Fixed: the CSV crash with accents after 8 KB, ';' files, Excel numbers/dates, mixed '\\' and '/',
    stale template data, silent overwrites, empty IDs, invalid XML characters and more (B1–B20)
  • Dark window (dark title bar), About, Check for updates, saved settings, drag and drop

Author: Luiz Junqueira & Claude AI
"""
import difflib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

APP_NAME = "Kodi Files Generator"
APP_VERSION = "v0.5.0"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"
APP_ID = "KodiFilesGenerator.App"
APP_AUTHORS = "Luiz Junqueira & Claude AI"
APP_CONTACT = "junqueira.ch@gmail.com"
APP_REPO_URL = "https://github.com/junqueirach/kodi-files-generator"
APP_LICENCE = "MIT"
APP_TAGLINE = "Put a series online fast: every NFO, XML and image Kodi, Jellyfin and Emby need, from free data."
APP_FEATURES = [
    "Reads your CSV / Excel list, a TheTVDB paste or a link (TheTVDB, TVmaze, IMDb, TMDb)",
    "Compares the databases and lets you choose per field",
    "Writes episode NFO + XML, tvshow.nfo, series.xml and season.nfo with cast, crew and ratings",
    "Makes the episode images for every server (from the databases or the best video frame)",
    "Series and season artwork, a health check, a preview, backups and Undo",
]
IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).parent if IS_FROZEN else Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

CAT = {"base": "#1e1e2e", "mantle": "#181825", "crust": "#11111b", "surface0": "#313244", "surface1": "#45475a",
       "surface2": "#585b70", "overlay": "#6c7086", "subtext": "#a6adc8", "text": "#cdd6f4", "green": "#a6e3a1",
       "red": "#f38ba8", "yellow": "#f9e2af", "blue": "#89b4fa", "mauve": "#cba6f7", "peach": "#fab387",
       "teal": "#94e2d5", "sky": "#89dceb"}
FONT = "Segoe UI"
F = {"title": (FONT, 15, "bold"), "head": (FONT, 11, "bold"), "body": (FONT, 10), "small": (FONT, 9),
     "tiny": (FONT, 8), "badge": (FONT, 8, "bold"), "mono": ("Consolas", 9)}

# ═══════════════════════════════════════════════════════════════════════
# SETTINGS (~/.kodi_files_generator.json, written atomically)
# ═══════════════════════════════════════════════════════════════════════

CONFIG_PATH = Path.home() / ".kodi_files_generator.json"
CACHE_DIR = Path.home() / ".kodi_files_generator_cache"
DEFAULT_SETTINGS = {
    "last_series_dir": "", "last_csv": "", "recent_series": [], "csv_mappings": {}, "date_order": "auto",
    "profiles": ["kodi", "jellyfin", "emby", "xml"], "episode_images": True, "artwork": True, "series_files": True,
    "replace_images": False, "replace_text": True, "thumb_width": 1280, "thumb_pad_16_9": False,
    "image_order": ["existing", "csv", "tvdb", "tvmaze", "tmdb", "video"], "rename_pattern": "{show} - {code} - {title}",
    "max_title_len": 80, "out_dir": "", "tvmaze_cache_hours": 24, "tmdb_key": "", "omdb_key": "", "language": "pt-BR",
    "rating_country": "Brazil", "tvdb_episode_details": True, "tvmaze_people": True, "source_rules": {},
    "csv_export_delimiter": ";", "ffmpeg_path": "", "dark_title_bar": True, "check_updates": False,
    "last_update_check": "", "skipped_version": "", "cast": True, "crew": True, "ratings": True, "techinfo": True,
    "image_urls": True, "nfo_template": "", "xml_template": "", "window": {},
}
_CFG_LOCK = threading.Lock()


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        try:
            CONFIG_PATH.replace(CONFIG_PATH.with_name(CONFIG_PATH.name + f".corrupt-{datetime.now():%Y%m%d-%H%M%S}"))
        except OSError:
            pass
    return s


def save_settings(s: dict):
    from kfg_writers import atomic_write
    with _CFG_LOCK:
        try:
            atomic_write(str(CONFIG_PATH), json.dumps(s, indent=1, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# DEPENDENCIES — asked once, installed only with the user's OK (never in the exe)
# ═══════════════════════════════════════════════════════════════════════

PACKAGES = [("PIL", "pillow", "images (thumbnails, artwork)", True),
            ("certifi", "certifi", "secure connections to TVmaze, TheTVDB, GitHub", True),
            ("truststore", "truststore", "uses the Windows certificate check (recommended)", False),
            ("tkinterdnd2", "tkinterdnd2", "drag and drop", False),
            ("openpyxl", "openpyxl", "Excel .xlsx files", False)]


def _has(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def pip_install(names) -> tuple:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + list(names)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    except Exception as e:
        return False, str(e)


def ensure_dependencies(root) -> list:
    missing = [p for p in PACKAGES if not _has(p[0])]
    if not missing or IS_FROZEN:
        return [p[1] for p in missing]
    text = "\n".join(f"  •  {pip}  — {why}{'' if req else '  (optional)'}" for _, pip, why, req in missing)
    if messagebox.askyesno("Install packages", f"{APP_NAME} uses these Python packages:\n\n{text}\n\n"
                           "Install them now with pip?", parent=root):
        ok, out = pip_install([p[1] for p in missing])
        if not ok:
            messagebox.showerror("Install failed", out, parent=root)
    return [p[1] for p in PACKAGES if not _has(p[0])]


# ═══════════════════════════════════════════════════════════════════════
# WINDOWS LOOK: dark title bar, title-bar icon (same helpers as SRT Studio v0.9.1)
# ═══════════════════════════════════════════════════════════════════════

def asset_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) if IS_FROZEN and getattr(sys, "_MEIPASS", None) else APP_DIR
    return base / "assets" / name


_HICONS = {}


def _win_frame(win) -> int:
    try:
        return int(win.wm_frame(), 16)
    except Exception:
        return 0


def _colorref(hex_colour: str) -> int:
    h = hex_colour.lstrip("#")
    return (int(h[4:6], 16) << 16) | (int(h[2:4], 16) << 8) | int(h[0:2], 16)


def set_window_icons(win) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        small, big, hwnd = asset_path("icon-small.ico"), asset_path("icon.ico"), _win_frame(win)
        if not hwnd or not small.exists() or not big.exists():
            return False
        u = ctypes.windll.user32
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        def load(p, s):
            if (str(p), s) not in _HICONS:
                _HICONS[(str(p), s)] = u.LoadImageW(None, str(p), 1, s, s, 0x10)
            return _HICONS[(str(p), s)]
        hs, hb = load(small, u.GetSystemMetrics(49)), load(big, u.GetSystemMetrics(11))
        if hs and hb:
            u.SendMessageW(hwnd, 0x80, 0, hs)
            u.SendMessageW(hwnd, 0x80, 1, hb)
            return True
    except Exception:
        pass
    return False


def set_dark_title_bar(win, on=True) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        hwnd = _win_frame(win)
        if not hwnd:
            return False
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1 if on else 0)
        ok = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4) == 0 or \
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), 4) == 0
        if sys.getwindowsversion().build >= 22000:
            cap = ctypes.c_uint(_colorref(CAT["mantle"]) if on else 0xFFFFFFFF)
            txt = ctypes.c_uint(_colorref(CAT["text"]) if on else 0xFFFFFFFF)
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), 4)
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), 4)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        return ok
    except Exception:
        return False


def set_dpi_awareness():
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def app_version_info(tk_patchlevel="") -> str:
    osname = sys.platform
    if os.name == "nt":
        try:
            osname = f"Windows (build {sys.getwindowsversion().build})"
        except Exception:
            osname = "Windows"
    return " · ".join(p for p in (f"{APP_NAME} {APP_VERSION.lstrip('v')}", "Windows exe" if IS_FROZEN else
                                  "running from source", osname, f"Python {sys.version.split()[0]}",
                                  f"Tk {tk_patchlevel}" if tk_patchlevel else "") if p)


# ═══════════════════════════════════════════════════════════════════════
# CHECK FOR UPDATES (GitHub releases; nothing else is sent)
# ═══════════════════════════════════════════════════════════════════════

RELEASES_API = "https://api.github.com/repos/junqueirach/kodi-files-generator/releases/latest"


def parse_version(tag) -> tuple:
    m = re.match(r"^\s*v?(\d+(?:\.\d+){1,3})\s*$", str(tag or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(tag, current=APP_VERSION) -> bool:
    a, b = parse_version(tag), parse_version(current)
    return bool(a and b and a > b)


def check_latest_release(opener=None, timeout=5) -> dict:
    import urllib.error
    import urllib.request
    try:
        if opener:
            status, body = opener(RELEASES_API)
        else:
            req = urllib.request.Request(RELEASES_API, headers={"User-Agent": f"KodiFilesGenerator/{APP_VERSION}",
                                                                "Accept": "application/vnd.github+json"})
            try:
                from kfg_sources import ssl_context
                with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
                    status, body = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, body = e.code, b""
        if status == 404:
            return {"status": "none", "version": "", "url": APP_REPO_URL + "/releases", "error": ""}
        if status != 200:
            return {"status": "error", "version": "", "url": "", "error": f"HTTP {status}"}
        d = json.loads(body.decode("utf-8"))
        tag, url = d.get("tag_name", ""), d.get("html_url") or APP_REPO_URL + "/releases"
        if not parse_version(tag):
            return {"status": "error", "version": "", "url": "", "error": f"unexpected version tag {tag!r}"}
        ver = ".".join(map(str, parse_version(tag)))
        return {"status": "newer" if is_newer(tag) else "latest", "version": ver, "url": url, "error": ""}
    except Exception as e:
        return {"status": "error", "version": "", "url": "", "error": str(e) or type(e).__name__}


# ═══════════════════════════════════════════════════════════════════════
# SMALL UI HELPERS
# ═══════════════════════════════════════════════════════════════════════

class Tooltip:
    def __init__(self, widget, text, delay=550):
        self.widget, self.text, self.delay, self._after, self._tip = widget, text, delay, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        if not self.text:
            return
        x, y = self.widget.winfo_rootx() + 12, self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, font=F["small"], fg=CAT["text"], bg=CAT["surface0"], padx=8, pady=5,
                 justify="left", wraplength=380, highlightbackground=CAT["blue"], highlightthickness=1).pack()
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _e=None):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def middle_ellipsis(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n // 2 - 1] + "…" + text[-(n - n // 2):]


SYMBOL = {True: "✓", False: "–", None: ""}


# ═══════════════════════════════════════════════════════════════════════
# THE APP
# ═══════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        import kfg_core
        import kfg_engine
        import kfg_media
        import kfg_sources
        self.core, self.engine, self.media, self.sources = kfg_core, kfg_engine, kfg_media, kfg_sources
        self.s = load_settings()
        self.title(APP_TITLE)
        win = self.s.get("window") or {}
        self.geometry(win.get("geometry") or "1360x820")
        self.minsize(1100, 680)
        self.configure(bg=CAT["base"])
        self.project = kfg_engine.Project()
        self.project.rules = dict(self.s.get("source_rules") or {})
        self.http = kfg_sources.Http(str(CACHE_DIR), self.s.get("tvmaze_cache_hours", 24))
        self.ffmpeg, self.ffprobe = kfg_media.find_ffmpeg(self.s.get("ffmpeg_path", ""))
        self.changes = []
        self.busy = False
        self.cancel_flag = False
        self._q = queue.Queue()
        self._icons = {}
        self._poster_img = None
        self._source_counts = {}
        self._set_icon()
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._pump)
        self._decorate(self)
        self.bind_class("Toplevel", "<Map>", self._on_toplevel_map, add="+")
        self._bind_keys()
        self._dnd()
        self.after(300, self._startup)

    # ── threading: workers report through a queue drained on the Tk thread ──
    def post(self, fn):
        self._q.put(fn)

    def _pump(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception as e:                      # never kill the pump
                    self.log(f"UI error: {e}", "error")
        except queue.Empty:
            pass
        try:
            self.after(40, self._pump)
        except tk.TclError:
            pass

    def run_bg(self, title, work, done=None):
        """work(progress, cancel) runs in a thread; done(result) on the Tk thread; errors shown honestly."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait — something is still running.", parent=self)
            return
        self.busy, self.cancel_flag = True, False
        self.status(title + "…")
        self._progress(0, 1)
        self.cancel_btn.configure(state="normal")

        def progress(i, n, text=""):
            self.post(lambda: (self._progress(i, n), self.status(f"{title}: {text}" if text else title)))

        def cancel():
            return self.cancel_flag

        def runner():
            try:
                res, err = work(progress, cancel), None
            except Exception as e:
                res, err = None, e
            self.post(lambda: self._bg_done(title, res, err, done))
        threading.Thread(target=runner, daemon=True).start()

    def _bg_done(self, title, res, err, done):
        self.busy = False
        self.cancel_btn.configure(state="disabled")
        self._progress(0, 1)
        if err is not None:
            self.status(f"{title} failed")
            self.log(f"✗ {title} failed: {err}", "error")
            self.message("Something went wrong", f"✗  {title} failed",
                         [("What happened", str(err) or type(err).__name__, CAT["red"]),
                          ("What to do", self._advice(err), CAT["text"])], [("Show log", lambda: self.nb.select(5),
                                                                              False), ("OK", None, True)])
            return
        if done:
            done(res)

    def _advice(self, err) -> str:
        S = self.sources
        if isinstance(err, S.Blocked):
            return ("The site refused the request. Open the page in your browser, select the episode list and use "
                    "Import ▾ › Paste from clipboard.")
        if isinstance(err, S.NotFound):
            return "Nothing was found for this ID or link. Check it and try again."
        if isinstance(err, S.SourceError):
            return "Check the internet connection and try again in a minute."
        return "See the Log tab for details."

    # ── look ───────────────────────────────────────────────────────────
    def _set_icon(self):
        try:
            ico = asset_path("icon.ico")
            if os.name == "nt" and ico.exists():
                self.iconbitmap(default=str(ico))
            for s in (20, 64):
                p = asset_path(f"icon-{s}.png")
                if p.exists():
                    self._icons[s] = tk.PhotoImage(file=str(p))
        except Exception:
            pass

    def _decorate(self, win):
        try:
            win.update_idletasks()
        except tk.TclError:
            return
        set_window_icons(win)
        if self.s.get("dark_title_bar", True):
            set_dark_title_bar(win, True)

    def _on_toplevel_map(self, e):
        w = e.widget
        if isinstance(w, tk.Toplevel) and not getattr(w, "_kfg_decorated", False):
            try:
                if w.wm_overrideredirect():
                    return
            except tk.TclError:
                return
            w._kfg_decorated = True
            w.after(10, lambda: w.winfo_exists() and self._decorate(w))

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        C = CAT
        st.configure(".", background=C["base"], foreground=C["text"], fieldbackground=C["surface0"],
                     selectbackground=C["surface1"], selectforeground=C["text"], bordercolor=C["surface1"],
                     darkcolor=C["surface0"], lightcolor=C["surface0"], troughcolor=C["mantle"],
                     arrowcolor=C["subtext"], font=F["body"])
        # tabs stand out from the background (user, 2026-10-16): grey tabs with bright text, the open tab in mauve
        st.configure("TNotebook", background=C["base"], borderwidth=0, tabmargins=(10, 6, 0, 0))
        st.configure("TNotebook.Tab", background=C["surface1"], foreground=C["text"], padding=(16, 6),
                     borderwidth=0, font=F["body"])
        st.map("TNotebook.Tab",
               background=[("selected", C["mauve"]), ("active", C["surface2"])],
               foreground=[("selected", C["crust"]), ("active", C["text"])],
               font=[("selected", (FONT, 10, "bold"))])
        st.configure("Treeview", background=C["mantle"], fieldbackground=C["mantle"], foreground=C["text"],
                     rowheight=24, borderwidth=0, font=F["small"])
        st.map("Treeview", background=[("selected", C["surface1"])], foreground=[("selected", C["text"])])
        st.configure("Treeview.Heading", background=C["surface0"], foreground=C["subtext"], relief="flat",
                     font=F["tiny"], padding=(6, 4))
        st.map("Treeview.Heading", background=[("active", C["surface1"])])
        st.configure("TCombobox", fieldbackground=C["surface0"], background=C["surface0"], foreground=C["text"],
                     arrowcolor=C["subtext"], padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", C["surface0"])], foreground=[("readonly", C["text"])])
        self.option_add("*TCombobox*Listbox.background", C["surface0"])
        self.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["surface1"])
        st.configure("Horizontal.TProgressbar", troughcolor=C["surface0"], background=C["mauve"], borderwidth=0,
                     thickness=8)
        st.configure("Vertical.TScrollbar", background=C["surface0"], troughcolor=C["mantle"], arrowcolor=C["subtext"],
                     bordercolor=C["mantle"])

    def btn(self, parent, text, cmd, kind="", **kw):
        C = CAT
        bg = {"primary": C["green"], "accent": C["mauve"], "danger": C["red"]}.get(kind, C["surface0"])
        fg = C["crust"] if kind else C["text"]
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=C["surface1"] if not kind else
                      C["blue"], activeforeground=C["crust"] if kind else C["text"], relief="flat", bd=0,
                      cursor="hand2", font=kw.pop("font", F["head"] if kind == "primary" else F["body"]),
                      padx=kw.pop("padx", 12), pady=kw.pop("pady", 4), highlightthickness=0, **kw)
        return b

    def link(self, parent, text, cmd, bg=None, font=None):
        lbl = tk.Label(parent, text=text, fg=CAT["blue"], bg=bg or CAT["mantle"], cursor="hand2", font=font or F["small"])
        lbl.bind("<Button-1>", lambda e: cmd())
        return lbl

    def heading(self, parent, text, bg):
        return tk.Label(parent, text=text, font=F["badge"], fg=CAT["mauve"], bg=bg, anchor="w")

    # ── build ──────────────────────────────────────────────────────────
    def _build(self):
        C = CAT
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_toolbar()
        left = tk.Frame(self, bg=C["mantle"], width=340, cursor="arrow")
        left.grid(row=1, column=0, sticky="nsw")
        left.grid_propagate(False)
        self._build_left(left)
        right = tk.Frame(self, bg=C["base"], cursor="arrow")
        self._drop_frames = (left, right)          # drag-and-drop targets (the root Tk window cannot be one)
        right.grid(row=1, column=1, sticky="nsew")
        self._build_right(right)
        self._build_statusbar()

    def _build_toolbar(self):
        C = CAT
        bar = tk.Frame(self, bg=C["mantle"], height=48)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)
        tk.Frame(self, bg=C["surface0"], height=1).grid(row=0, column=0, columnspan=2, sticky="sew")
        brand = tk.Frame(bar, bg=C["mantle"])
        brand.pack(side="left", padx=(12, 16))
        parts = []
        if 20 in self._icons:
            parts.append(tk.Label(brand, image=self._icons[20], bg=C["mantle"]))
        parts.append(tk.Label(brand, text=APP_NAME, font=F["head"], fg=C["text"], bg=C["mantle"]))
        parts.append(tk.Label(brand, text=APP_VERSION.lstrip("v"), font=F["tiny"], fg=C["overlay"], bg=C["mantle"]))
        for i, w in enumerate(parts):
            w.pack(side="left", padx=(0, 6), pady=(3 if i == 2 else 0, 0))
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self.show_about())

        def menubutton(text, entries, tip=""):
            mb = tk.Menubutton(bar, text=text, font=F["body"], bg=C["mantle"], fg=C["text"], relief="flat",
                               activebackground=C["surface0"], activeforeground=C["text"], cursor="hand2", padx=10,
                               pady=4, bd=0)
            m = tk.Menu(mb, tearoff=False, bg=C["surface0"], fg=C["text"], activebackground=C["surface1"],
                        activeforeground=C["text"])
            for e in entries:
                if e is None:
                    m.add_separator()
                else:
                    m.add_command(label=e[0], command=e[1], accelerator=e[2] if len(e) > 2 else "")
            mb.configure(menu=m)
            if tip:
                Tooltip(mb, tip)
            return mb, m

        b = self.btn(bar, "📂  Open series…", self.open_series)
        b.pack(side="left", padx=(0, 6), pady=8)
        Tooltip(b, "The show folder (with its season folders and videos)  (Ctrl+O)")
        mb, self.import_menu = menubutton("Import ▾", [
            ("CSV / Excel file…", self.import_file, "Ctrl+I"), ("Paste from clipboard", self.import_paste, "Ctrl+V"),
            ("New CSV from a link…", self.import_link, "Ctrl+L"), None,
            ("Save the episode list as CSV…", self.export_csv)],
            "Episode data from a file, a TheTVDB paste or a link")
        mb.pack(side="left")
        mb, self.fetch_menu = menubutton("Fetch ▾", [
            ("TVmaze (free, no key)", self.fetch_tvmaze), ("TheTVDB (link or ID)…", self.import_link),
            ("TMDb (key in Settings)", self.fetch_tmdb), ("OMDb / IMDb (key in Settings)", self.fetch_omdb)],
            "Fill and compare the episode data from the free databases")
        mb.pack(side="left")
        b = self.btn(bar, "✓  Check", self.run_check)
        b.pack(side="left", padx=(8, 0), pady=8)
        Tooltip(b, "Health check: missing videos, gaps, conflicts between sources, invalid files")
        mb, self.help_menu = menubutton("Help ▾", [
            ("Help", self.show_help, "F1"), ("Files per server", self.show_formats),
            ("Check for updates…", lambda: self.check_updates(True)), None, (f"About {APP_NAME}", self.show_about)])
        mb.pack(side="right", padx=(0, 10))
        b = self.btn(bar, "⚙  Settings", self.show_settings)
        b.pack(side="right", padx=6, pady=8)

    def _build_left(self, p):
        C = CAT
        bg = C["mantle"]
        wrap = tk.Frame(p, bg=bg)
        wrap.pack(fill="both", expand=True, padx=14, pady=10)
        self.heading(wrap, "SERIES", bg).pack(fill="x")
        top = tk.Frame(wrap, bg=bg)
        top.pack(fill="x", pady=(6, 0))
        self.poster_lbl = tk.Label(top, bg=C["surface0"], width=12, height=8, text="no\nposter", fg=C["overlay"],
                                   font=F["tiny"])
        self.poster_lbl.pack(side="left", anchor="n")
        info = tk.Frame(top, bg=bg)
        info.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.var_title = tk.StringVar()
        self.var_year = tk.StringVar()
        self.var_tvdb = tk.StringVar()
        self.var_imdb = tk.StringVar()
        for label, var, w in (("Title", self.var_title, 22), ("Year", self.var_year, 6), ("TheTVDB", self.var_tvdb, 10),
                              ("IMDb", self.var_imdb, 11)):
            r = tk.Frame(info, bg=bg)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label, width=7, anchor="w", font=F["tiny"], fg=C["subtext"], bg=bg).pack(side="left")
            e = tk.Entry(r, textvariable=var, width=w, font=F["small"], bg=C["surface0"], fg=C["text"],
                         insertbackground=C["text"], relief="flat")
            e.pack(side="left", fill="x", expand=True, ipady=2)
            e.bind("<FocusOut>", lambda ev: self._series_fields_to_project())
            e.bind("<Return>", lambda ev: self._series_fields_to_project())
        self.series_info = tk.Label(wrap, text="", font=F["tiny"], fg=C["subtext"], bg=bg, anchor="w", justify="left",
                                    wraplength=300)
        self.series_info.pack(fill="x", pady=(6, 0))

        self.heading(wrap, "WHERE", bg).pack(fill="x", pady=(12, 2))
        self.folder_lbl = tk.Label(wrap, text="No series folder — files go to the output folder", font=F["small"],
                                   fg=C["text"], bg=bg, anchor="w", justify="left", wraplength=300)
        self.folder_lbl.pack(fill="x")
        r = tk.Frame(wrap, bg=bg)
        r.pack(fill="x", pady=(4, 0))
        tk.Label(r, text="Output folder", font=F["tiny"], fg=C["subtext"], bg=bg).pack(side="left")
        self.var_out = tk.StringVar(value=self.s.get("out_dir", ""))
        tk.Entry(r, textvariable=self.var_out, font=F["tiny"], bg=C["surface0"], fg=C["text"], relief="flat",
                 insertbackground=C["text"]).pack(side="left", fill="x", expand=True, padx=6, ipady=2)
        self.link(r, "Browse…", self._browse_out).pack(side="left")

        self.heading(wrap, "SOURCES", bg).pack(fill="x", pady=(12, 2))
        self.sources_lbl = tk.Label(wrap, text="none yet — Import ▾ or Fetch ▾", font=F["small"], fg=C["text"], bg=bg,
                                    anchor="w", justify="left")
        self.sources_lbl.pack(fill="x")

        self.heading(wrap, "SERVERS", bg).pack(fill="x", pady=(12, 2))
        self.var_prof = {}
        grid = tk.Frame(wrap, bg=bg)
        grid.pack(fill="x")
        for i, prof in enumerate(self.engine.PROFILES):
            v = tk.BooleanVar(value=prof in self.s.get("profiles", []))
            self.var_prof[prof] = v
            c = tk.Checkbutton(grid, text=self.engine.PROFILE_LABELS[prof].split(" (")[0], variable=v, font=F["small"],
                               fg=C["text"], bg=bg, selectcolor=C["surface0"], activebackground=bg,
                               activeforeground=C["text"], anchor="w", command=self._options_changed)
            c.grid(row=i // 2, column=i % 2, sticky="w")
            Tooltip(c, {"xml": "metadata\\<episode>.xml + metadata\\<episode>.jpg and series.xml "
                               "(MediaBrowser / Media Center Master; Emby with the XML plugin)",
                        "plex": "Plex reads no NFO: only the images (<episode>.jpg, poster.jpg, SeasonNN.jpg)"}.get(
                prof, "episode .nfo + <episode>-thumb.jpg, tvshow.nfo, artwork"))

        self.heading(wrap, "IMAGES", bg).pack(fill="x", pady=(12, 2))
        self.var_epimg = tk.BooleanVar(value=self.s.get("episode_images", True))
        self.var_art = tk.BooleanVar(value=self.s.get("artwork", True))
        self.var_replace_img = tk.BooleanVar(value=self.s.get("replace_images", False))
        for text, var, tip in (("Episode images (thumbs)", self.var_epimg,
                                "From an existing image, the CSV, TheTVDB, TVmaze, TMDb or the best frame of the video"),
                               ("Series and season artwork", self.var_art, "Poster, fanart, banner, logo, season posters"),
                               ("Replace existing images", self.var_replace_img,
                                "Off: images already in the folder are kept")):
            c = tk.Checkbutton(wrap, text=text, variable=var, font=F["small"], fg=C["text"], bg=bg,
                               selectcolor=C["surface0"], activebackground=bg, activeforeground=C["text"], anchor="w",
                               command=self._options_changed)
            c.pack(fill="x")
            Tooltip(c, tip)
        self.ffmpeg_lbl = tk.Label(wrap, text="", font=F["tiny"], fg=C["overlay"], bg=bg, anchor="w")
        self.ffmpeg_lbl.pack(fill="x", pady=(2, 0))

        self.gen_btn = self.btn(p, "▶  Generate all", self.generate_all, "primary", pady=8)
        self.gen_btn.pack(side="bottom", fill="x", padx=14, pady=(4, 14))
        Tooltip(self.gen_btn, "Shows every file first (Changes tab); nothing is written before you confirm  (Ctrl+G)")
        row = tk.Frame(p, bg=bg)
        row.pack(side="bottom", fill="x", padx=14)
        self.link(row, "Undo last run", self.undo_last).pack(side="left")
        self.link(row, "Rename videos…", self.rename_videos).pack(side="right")

    def _build_right(self, p):
        self.nb = ttk.Notebook(p)
        self.nb.pack(fill="both", expand=True, padx=(8, 8), pady=(4, 0))
        tabs = {}
        for name in ("Episodes", "Sources", "Artwork", "Changes", "Check", "Log"):
            f = tk.Frame(self.nb, bg=CAT["base"])
            self.nb.add(f, text=f"  {name}  ")
            tabs[name] = f
        self._build_episodes(tabs["Episodes"])
        self._build_sources(tabs["Sources"])
        self._build_artwork(tabs["Artwork"])
        self._build_changes(tabs["Changes"])
        self._build_check(tabs["Check"])
        self._build_log(tabs["Log"])

    def _tree(self, parent, cols, widths, heads, height=None, stretch=()):
        fr = tk.Frame(parent, bg=CAT["base"])
        t = ttk.Treeview(fr, columns=cols, show="headings", selectmode="extended", height=height or 10)
        for c in cols:
            t.heading(c, text=heads.get(c, c), anchor="w")
            t.column(c, width=widths.get(c, 80), anchor="w", stretch=c in stretch)
        sb = ttk.Scrollbar(fr, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        t.tag_configure("bad", foreground=CAT["red"])
        t.tag_configure("warn", foreground=CAT["yellow"])
        t.tag_configure("ok", foreground=CAT["green"])
        t.tag_configure("dim", foreground=CAT["overlay"])
        return fr, t

    def _build_episodes(self, p):
        C = CAT
        bar = tk.Frame(p, bg=C["base"])
        bar.pack(fill="x", pady=(8, 6), padx=4)
        self.var_filter = tk.StringVar(value="all")
        for key, text in (("all", "All"), ("problems", "Problems"), ("novideo", "No video"), ("noimage", "No image")):
            tk.Radiobutton(bar, text=text, value=key, variable=self.var_filter, indicatoron=False, font=F["small"],
                           bg=C["surface0"], fg=C["text"], selectcolor=C["surface2"], activebackground=C["surface1"],
                           relief="flat", padx=10, pady=2, command=self.refresh_episodes).pack(side="left", padx=(0, 4))
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *a: self.refresh_episodes())
        tk.Entry(bar, textvariable=self.var_search, font=F["small"], bg=C["surface0"], fg=C["text"], relief="flat",
                 insertbackground=C["text"], width=26).pack(side="right", ipady=2)
        tk.Label(bar, text="Search", font=F["small"], fg=C["subtext"], bg=C["base"]).pack(side="right", padx=6)
        self.ep_count_lbl = tk.Label(bar, text="", font=F["small"], fg=C["subtext"], bg=C["base"])
        self.ep_count_lbl.pack(side="left", padx=10)
        detail = tk.Frame(p, bg=C["mantle"], height=150)
        detail.pack(side="bottom", fill="x")
        detail.pack_propagate(False)
        self.detail = tk.Text(detail, font=F["small"], bg=C["mantle"], fg=C["text"], relief="flat", wrap="word",
                              padx=12, pady=8, highlightthickness=0)
        self.detail.pack(fill="both", expand=True)
        self.detail.tag_configure("h", foreground=C["mauve"], font=F["badge"])
        self.detail.tag_configure("dim", foreground=C["overlay"])
        self.detail.configure(state="disabled")
        cols = ("code", "title", "aired", "rt", "video", "nfo", "xml", "thumb", "src", "flag")
        heads = {"code": "#", "title": "Title", "aired": "Aired", "rt": "Min", "video": "Video", "nfo": "NFO",
                 "xml": "XML", "thumb": "Image", "src": "Sources", "flag": "⚑"}
        widths = {"code": 70, "title": 300, "aired": 90, "rt": 45, "video": 260, "nfo": 40, "xml": 40, "thumb": 50,
                  "src": 130, "flag": 30}
        fr, self.ep_tree = self._tree(p, cols, widths, heads, stretch=("title", "video"))
        fr.pack(fill="both", expand=True, padx=4)
        self.ep_tree.bind("<<TreeviewSelect>>", lambda e: (self.show_detail(), self.refresh_sources_tab()))
        self.ep_tree.bind("<Double-1>", lambda e: self.edit_episode())

    def _build_sources(self, p):
        C = CAT
        top = tk.Frame(p, bg=C["base"])
        top.pack(fill="x", padx=6, pady=(8, 4))
        self.src_head = tk.Label(top, text="Select an episode in the Episodes tab.", font=F["body"], fg=C["text"],
                                 bg=C["base"], anchor="w")
        self.src_head.pack(side="left")
        self.link(top, "Rules per field…", lambda: self.show_settings(tab=1), bg=C["base"]).pack(side="right")
        self.link(top, "Use the rules for this episode", self._clear_user_choices, bg=C["base"]).pack(side="right",
                                                                                                     padx=12)
        tk.Label(p, text="Each column is a database. Click a value to use it for this episode; the green value is the "
                         "one written. 'Rules per field' sets which database the app believes first.",
                 font=F["tiny"], fg=C["subtext"], bg=C["base"], anchor="w", justify="left", wraplength=900).pack(
            fill="x", padx=6)
        self.src_fields = ["title", "plot", "aired", "runtime", "tvdb_id", "imdb_id", "mpaa", "directors", "writers",
                           "guests", "network", "title_en"]
        cols = ["field", "chosen"] + self.core.SOURCES[1:]
        heads = {"field": "Field", "chosen": "Used"} | {s: self.core.SOURCE_LABELS[s] for s in self.core.SOURCES}
        fr, self.src_tree = self._tree(p, cols, {c: (90 if c == "field" else 70 if c == "chosen" else 150)
                                                  for c in cols}, heads, height=12)
        fr.pack(fill="both", expand=True, padx=6, pady=6)
        self.src_tree.bind("<ButtonRelease-1>", self._source_click)
        tk.Label(p, text="Conflicts in the whole series", font=F["badge"], fg=C["mauve"], bg=C["base"],
                 anchor="w").pack(fill="x", padx=6)
        fr2, self.conf_tree = self._tree(p, ("code", "field", "values"), {"code": 80, "field": 80, "values": 700},
                                         {"code": "#", "field": "Field", "values": "What the sources say"}, height=6,
                                         stretch=("values",))
        fr2.pack(fill="x", padx=6, pady=(2, 8))
        self.conf_tree.bind("<Double-1>", self._conflict_goto)

    def _build_artwork(self, p):
        C = CAT
        top = tk.Frame(p, bg=C["base"])
        top.pack(fill="x", padx=6, pady=(8, 4))
        tk.Label(top, text="Series and season artwork — the first image of each kind is used; pick another or add your "
                           "own (drop image files on the window or 'Add images…').", font=F["small"], fg=C["subtext"],
                 bg=C["base"]).pack(side="left")
        self.btn(top, "Add images…", self.add_images).pack(side="right")
        body = tk.Frame(p, bg=C["base"])
        body.pack(fill="both", expand=True, padx=6, pady=4)
        fr, self.art_tree = self._tree(body, ("kind", "count", "source", "file"),
                                       {"kind": 120, "count": 60, "source": 90, "file": 520},
                                       {"kind": "Kind", "count": "Found", "source": "Source", "file": "Image used"},
                                       stretch=("file",))
        fr.pack(side="left", fill="both", expand=True)
        side = tk.Frame(body, bg=C["base"], width=320)
        side.pack(side="right", fill="y", padx=(8, 0))
        side.pack_propagate(False)
        self.art_preview = tk.Label(side, bg=C["mantle"], text="select a row", fg=C["overlay"], font=F["small"])
        self.art_preview.pack(fill="both", expand=True)
        r = tk.Frame(side, bg=C["base"])
        r.pack(fill="x", pady=6)
        self.btn(r, "◀", lambda: self._art_cycle(-1)).pack(side="left")
        self.btn(r, "▶", lambda: self._art_cycle(1)).pack(side="left", padx=4)
        self.btn(r, "Choose file…", self._art_choose_file).pack(side="left")
        self.art_tree.bind("<<TreeviewSelect>>", lambda e: self._art_selected())
        self._art_img = None

    def _build_changes(self, p):
        C = CAT
        top = tk.Frame(p, bg=C["base"])
        top.pack(fill="x", padx=6, pady=(8, 4))
        self.changes_lbl = tk.Label(top, text="Press ▶ Generate all to see every file before anything is written.",
                                    font=F["small"], fg=C["subtext"], bg=C["base"])
        self.changes_lbl.pack(side="left")
        self.write_btn = self.btn(top, "Write selected", self.write_changes, "primary", font=F["body"])
        self.write_btn.pack(side="right")
        self.write_btn.configure(state="disabled")
        self.link(top, "untick all", lambda: self._tick_all(False), bg=C["base"]).pack(side="right", padx=8)
        self.link(top, "tick all", lambda: self._tick_all(True), bg=C["base"]).pack(side="right")
        pw = tk.PanedWindow(p, orient="vertical", bg=C["base"], sashwidth=5, bd=0)
        pw.pack(fill="both", expand=True, padx=6, pady=4)
        fr, self.ch_tree = self._tree(pw, ("sel", "action", "kind", "path", "source"),
                                      {"sel": 30, "action": 70, "kind": 90, "path": 520, "source": 260},
                                      {"sel": "✓", "action": "Action", "kind": "File", "path": "Path",
                                       "source": "Content from"}, stretch=("path",))
        pw.add(fr, stretch="always")
        self.diff = tk.Text(pw, font=F["mono"], bg=C["mantle"], fg=C["text"], relief="flat", height=10, wrap="none")
        self.diff.tag_configure("add", foreground=C["green"])
        self.diff.tag_configure("del", foreground=C["red"])
        self.diff.tag_configure("dim", foreground=C["overlay"])
        pw.add(self.diff, height=180)
        self.ch_tree.bind("<ButtonRelease-1>", self._change_click)
        self.ch_tree.bind("<<TreeviewSelect>>", lambda e: self._show_diff())

    def _build_check(self, p):
        C = CAT
        top = tk.Frame(p, bg=C["base"])
        top.pack(fill="x", padx=6, pady=(8, 4))
        self.check_lbl = tk.Label(top, text="Press ✓ Check.", font=F["small"], fg=C["subtext"], bg=C["base"])
        self.check_lbl.pack(side="left")
        fr, self.check_tree = self._tree(p, ("level", "where", "text"), {"level": 70, "where": 260, "text": 700},
                                         {"level": "Level", "where": "Where", "text": "What"}, stretch=("text",))
        fr.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_log(self, p):
        C = CAT
        top = tk.Frame(p, bg=C["base"])
        top.pack(fill="x", padx=6, pady=(8, 4))
        self.link(top, "Save log…", self.save_log, bg=C["base"]).pack(side="right")
        self.link(top, "Clear", lambda: (self.log_text.configure(state="normal"), self.log_text.delete("1.0", "end"),
                                         self.log_text.configure(state="disabled")), bg=C["base"]).pack(side="right",
                                                                                                        padx=10)
        self.log_text = scrolledtext.ScrolledText(p, font=F["mono"], bg=C["mantle"], fg=C["text"], relief="flat",
                                                  wrap="word", state="disabled", padx=10, pady=6)
        self.log_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        for tag, col in (("ok", C["green"]), ("warn", C["yellow"]), ("error", C["red"]), ("info", C["blue"]),
                         ("accent", C["mauve"])):
            self.log_text.tag_configure(tag, foreground=col)

    def _build_statusbar(self):
        C = CAT
        bar = tk.Frame(self, bg=C["crust"], height=30)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Open a series folder, or import an episode list.")
        tk.Label(bar, textvariable=self.status_var, font=F["small"], fg=C["text"], bg=C["crust"], anchor="w",
                 padx=12).pack(side="left", fill="x", expand=True)
        self.cancel_btn = tk.Button(bar, text="✕ Cancel", command=self._cancel, font=F["tiny"], bg=C["crust"],
                                    fg=C["red"], relief="flat", bd=0, cursor="hand2", state="disabled",
                                    disabledforeground=C["surface1"], activebackground=C["surface0"])
        self.cancel_btn.pack(side="right", padx=8)
        self.pbar = ttk.Progressbar(bar, length=220, maximum=1)
        self.pbar.pack(side="right", padx=6)
        self.update_link = tk.Label(bar, text="", font=F["small"], fg=C["green"], bg=C["crust"], cursor="hand2")
        self.update_link.bind("<Button-1>", lambda e: webbrowser.open(self._update_url))
        self._update_url = ""
        self.open_link = tk.Label(bar, text="Open folder", font=F["small"], fg=C["blue"], bg=C["crust"], cursor="hand2")
        self.open_link.bind("<Button-1>", lambda e: self._open_folder(self.project.show_dir or self.var_out.get()))

    # ── status / log ───────────────────────────────────────────────────
    def status(self, text):
        self.status_var.set(text)

    def _progress(self, i, n):
        self.pbar.configure(maximum=max(1, n), value=i)

    def _cancel(self):
        self.cancel_flag = True
        self.status("Cancelling…")

    def log(self, msg, tag="info"):
        def ins():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n", tag)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        if threading.current_thread() is threading.main_thread():
            ins()
        else:
            self.post(ins)

    def save_log(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", initialfile="kodi-files-generator-log.txt",
                                         filetypes=[("Text", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.log_text.get("1.0", "end"))

    # ── keys, drag and drop, startup, close ────────────────────────────
    def _bind_keys(self):
        self.bind_all("<Control-o>", lambda e: self.open_series())
        self.bind_all("<Control-i>", lambda e: self.import_file())
        self.bind_all("<Control-l>", lambda e: self.import_link())
        self.bind_all("<Control-g>", lambda e: self.generate_all())
        self.bind_all("<F1>", lambda e: self.show_help())
        self.bind("<Control-v>", lambda e: self.import_paste() if not isinstance(self.focus_get(), (tk.Entry, tk.Text))
                  else None)

    def _dnd(self):
        try:
            import tkinterdnd2 as dnd
            dnd.TkinterDnD.require(self)
            for w in self._drop_frames:            # tkinterdnd2 registers widgets, not the root Tk window
                w.drop_target_register(dnd.DND_FILES)
                w.dnd_bind("<<Drop>>", lambda e: self._on_drop(self.tk.splitlist(e.data)))
            self._dnd_ok = True
        except Exception:
            self._dnd_ok = False

    def _on_drop(self, paths):
        paths = [os.path.normpath(p) for p in paths]
        imgs = [p for p in paths if os.path.splitext(p)[1].lower() in self.core.IMAGE_EXTS]
        if imgs:
            self.add_images(imgs)
            return
        for p in paths:
            if os.path.isdir(p):
                self.open_series(p)
                return
            if os.path.splitext(p)[1].lower() in (".csv", ".txt", ".tsv", ".xlsx"):
                self.import_file(p)
                return

    def _startup(self):
        self.ffmpeg_lbl.configure(text=("FFmpeg: " + os.path.dirname(self.ffmpeg)) if self.ffmpeg else
                                  "FFmpeg not found — no frames from videos (Settings → Files)")
        if self.s.get("check_updates") and self.s.get("last_update_check") != datetime.now().strftime("%Y-%m-%d"):
            self.check_updates(False)
        self.log(f"{APP_TITLE} — settings: {CONFIG_PATH}", "accent")
        if not _has("PIL"):
            self.log("Pillow is missing: images cannot be made (python -m pip install pillow).", "warn")

    def _on_close(self):
        self.s["window"] = {"geometry": self.winfo_geometry()}
        self._save_options()
        self.destroy()

    def _save_options(self):
        self.s["profiles"] = [p for p, v in self.var_prof.items() if v.get()]
        self.s["episode_images"] = self.var_epimg.get()
        self.s["artwork"] = self.var_art.get()
        self.s["replace_images"] = self.var_replace_img.get()
        self.s["out_dir"] = self.var_out.get().strip()
        self.s["source_rules"] = self.project.rules
        save_settings(self.s)

    def _options_changed(self):
        self._save_options()
        self.changes = []
        self._fill_changes()

    def _browse_out(self):
        d = filedialog.askdirectory(parent=self, title="Output folder (when no series folder is open)")
        if d:
            self.var_out.set(os.path.normpath(d))
            self._save_options()

    def _open_folder(self, path):
        if path and os.path.isdir(path):
            os.startfile(path) if os.name == "nt" else webbrowser.open(Path(path).as_uri())

    # ═══════════════════════════════════════════════════════════════════
    # OPEN A SERIES FOLDER (F3)
    # ═══════════════════════════════════════════════════════════════════
    def open_series(self, folder=None):
        folder = folder or filedialog.askdirectory(parent=self, title="Series folder (the show, with its season folders)",
                                                   initialdir=self.s.get("last_series_dir") or None)
        if not folder:
            return
        folder = os.path.normpath(folder)
        self.s["last_series_dir"] = os.path.dirname(folder)
        rec = [folder] + [r for r in self.s.get("recent_series", []) if r != folder]
        self.s["recent_series"] = rec[:8]
        save_settings(self.s)
        keep = self.project if self.project.episodes and not self.project.show_dir else None
        proj = self.engine.Project()
        proj.rules = dict(self.s.get("source_rules") or {})
        if keep is not None and messagebox.askyesno(APP_NAME, "Keep the episode data already imported for this "
                                                    "series folder?", parent=self):
            proj.series, proj.seasons, proj.episodes = keep.series, keep.seasons, keep.episodes

        def work(progress, cancel):
            proj.scan(folder, self.ffprobe, progress, cancel)
            return proj

        def done(p):
            self.project = p
            n = len(p.videos)
            self.log(f"Opened {folder} — {n} video(s), {len(p.episodes)} episode(s) from the file names", "accent")
            for v, t in p.match_notes:
                self.log(f"   {os.path.basename(v.path)}: {t}", "warn")
            self._project_to_ui()
            self.status(f"{p.series.get('title') or ''}: {n} video(s). Next: Import ▾ or Fetch ▾ for the episode data.")
        self.run_bg("Reading the series folder", work, done)

    # ═══════════════════════════════════════════════════════════════════
    # IMPORT: FILE, PASTE, LINK (F1, F16)
    # ═══════════════════════════════════════════════════════════════════
    def import_file(self, path=None):
        path = path or filedialog.askopenfilename(parent=self, title="Episode list (CSV, TSV, TXT or Excel)",
                                                  initialdir=os.path.dirname(self.s.get("last_csv") or "") or None,
                                                  filetypes=[("Episode lists", "*.csv *.tsv *.txt *.xlsx"),
                                                             ("All files", "*.*")])
        if not path:
            return
        if path.lower().endswith(".xlsx") and not _has("openpyxl"):
            if not IS_FROZEN and messagebox.askyesno(APP_NAME, "Excel files need the 'openpyxl' package. Install it "
                                                     "now with pip?", parent=self):
                ok, out = pip_install(["openpyxl"])
                if not ok:
                    messagebox.showerror(APP_NAME, out, parent=self)
                    return
            else:
                return
        self.s["last_csv"] = path
        save_settings(self.s)
        try:
            table = self.core.read_table_file(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not read the file:\n{path}\n\n{e}", parent=self)
            return
        self.log(f"Read {os.path.basename(path)} — {len(table.rows)} rows, encoding {table.encoding or 'xlsx'}, "
                 f"separator {repr(table.delimiter) if table.delimiter else '—'}", "info")
        self._import_table(table, "csv", os.path.basename(path))

    def import_paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if not text.strip():
            messagebox.showinfo(APP_NAME, "The clipboard is empty. Copy the episode list first (from TheTVDB's "
                                "'All seasons' page or from a spreadsheet).", parent=self)
            return
        if re.search(r"(?m)^(S\d+E\d+|SPECIAL \d+x\d+)\s+\S", text):
            eps, unknown = self.core.parse_tvdb_paste(text)
            report = [{"line": n, "kind": "unrecognised", "message": f"line not understood: {t[:80]}", "cells": []}
                      for n, t in unknown]
            self.log(f"TheTVDB paste: {len(eps)} episode(s)", "info")
            self._finish_import(eps, report, "paste", "the paste")
        else:
            self._import_table(self.core.read_table_text(text), "paste", "the clipboard")

    def _import_table(self, table, source, name):
        sig = self.core.header_signature(table.headers)
        remembered = (self.s.get("csv_mappings") or {}).get(sig)
        mapping = self.core.suggest_mapping(table.headers, remembered)
        if remembered is None or self.core.missing_required(mapping):
            mapping = self.mapping_dialog(table, mapping, name)
            if mapping is None:
                return
        self.s.setdefault("csv_mappings", {})[sig] = {k: v for k, v in mapping.items() if v}
        save_settings(self.s)
        eps, report = self.core.rows_to_episodes(table, mapping, self.s.get("date_order", "auto"), source)
        self._finish_import(eps, report, source, name)

    def _finish_import(self, eps, report, source, name):
        if report:
            if not self.report_dialog(report, len(eps), name):
                return
        added, notes = self.project.merge_episodes(eps, source)
        self.project.match()
        self.log(f"Imported {len(eps)} episode(s) from {name} ({added} new)", "ok")
        for n in notes:
            self.log("   " + n, "info")
        self._source_counts[source] = len(eps)
        self._project_to_ui()

    def export_csv(self):
        if not self.project.episodes:
            messagebox.showinfo(APP_NAME, "There are no episodes yet.", parent=self)
            return
        t, y = self.project.series.get("title") or "Series", self.project.series.get("year")
        init = self.core.sanitize_filename(f"{t} ({y})" if y else t) + ".episodes.csv"
        p = filedialog.asksaveasfilename(parent=self, title="Save the episode list", initialfile=init,
                                         initialdir=self.project.show_dir or None, defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv")])
        if p:
            n = self.engine.export_csv(self.project, p, self.s.get("csv_export_delimiter", ";"))
            self.log(f"Saved {n} episode(s) to {p}", "ok")
            self.status(f"Episode list saved: {os.path.basename(p)}")

    def import_link(self):
        r = self.link_dialog()
        if not r:
            return
        site, val, details = r
        lang, country = self.s.get("language", "pt-BR"), self.s.get("rating_country", "Brazil")
        proj = self.project

        def work(progress, cancel):
            S = self.sources
            if site in ("tvdb", "tvdb_id"):
                res = S.fetch_tvdb(self.http, proj, val, lang, details, country, progress, cancel)
                return ("TheTVDB", res)
            if site == "tvmaze":
                show = self.http.json(f"{S.TVMAZE}/shows/{val}")
                return ("TVmaze", S.fetch_tvmaze(self.http, proj, show, self.s.get("tvmaze_people", True), progress,
                                                 cancel))
            if site == "imdb":
                show = S.tvmaze_find(self.http, imdb_id=val)
                if show is None:
                    raise S.NotFound("TVmaze does not know this IMDb ID")
                res = S.fetch_tvmaze(self.http, proj, show, self.s.get("tvmaze_people", True), progress, cancel)
                if self.s.get("omdb_key"):
                    S.fetch_omdb(self.http, proj, self.s["omdb_key"], val, progress, cancel)
                return ("TVmaze + IMDb", res)
            if site == "tmdb":
                if not self.s.get("tmdb_key"):
                    raise S.SourceError("a TMDb link needs your TMDb key (Settings → Sources)")
                return ("TMDb", S.fetch_tmdb(self.http, proj, self.s["tmdb_key"], lang, tmdb_id=val, progress=progress,
                                             cancel=cancel))
            raise S.SourceError("this link is not from TheTVDB, TVmaze, IMDb or TMDb")

        def done(res):
            label, r2 = res
            self._after_fetch(label, r2)
            if messagebox.askyesno(APP_NAME, f"{label}: {r2['episodes']} episode(s).\n\nSave them as a CSV file too "
                                   "(to open in Excel and complete by hand)?", parent=self):
                self.export_csv()
        self.run_bg(f"Reading {site.replace('_id', '')}", work, done)

    def _after_fetch(self, label, res):
        self.status(f"{label}: {res['episodes']} episode(s) read — check the Episodes and Sources tabs, then "
                    "▶ Generate all.")
        self.project.match()
        self._source_counts[label] = res["episodes"]
        self.log(f"{label}: '{res['series']}' — {res['episodes']} episode(s), {res['added']} new", "ok")
        for n in res.get("notes") or []:
            self.log("   " + n, "info")
        self._project_to_ui()
        self._load_poster()

    # ── fetch (F4, F14, F17) ───────────────────────────────────────────
    def _series_ids(self):
        self._series_fields_to_project()
        s = self.project.series
        return s.get("tvdb_id") or "", s.get("imdb_id") or "", s.get("title") or ""

    def fetch_tvmaze(self):
        tvdb, imdb, title = self._series_ids()
        if not (tvdb or imdb or title):
            messagebox.showinfo(APP_NAME, "Enter the TheTVDB ID, the IMDb ID or the title first (left panel).",
                                parent=self)
            return
        S, proj = self.sources, self.project

        def find(progress, cancel):
            show = S.tvmaze_find(self.http, tvdb_id=tvdb, imdb_id=imdb)
            if show is not None:
                return [show]
            return S.tvmaze_search(self.http, title) if title else []

        def found(shows):
            if not shows:
                self.message("TVmaze", "Not found on TVmaze", [("Searched", f"TheTVDB {tvdb or '–'} · IMDb {imdb or '–'}"
                                                                 f" · '{title}'", CAT["text"])], [("OK", None, True)])
                return
            show = shows[0] if len(shows) == 1 else self.pick_show(shows)
            if not show:
                return
            self.run_bg("Reading TVmaze", lambda pr, ca: S.fetch_tvmaze(self.http, proj, show,
                                                                         self.s.get("tvmaze_people", True), pr, ca),
                        lambda r: self._after_fetch("TVmaze", r))
        self.run_bg("Looking up TVmaze", find, found)

    def fetch_tmdb(self):
        key = self.s.get("tmdb_key")
        if not key:
            self.message("TMDb", "TMDb needs your own free key", [("Where", "Settings → Sources → TMDb key "
                                                                  "(themoviedb.org → Settings → API)", CAT["text"])],
                         [("Open Settings", lambda: self.show_settings(1), False), ("OK", None, True)])
            return
        tvdb, imdb, _ = self._series_ids()
        lang, proj = self.s.get("language", "pt-BR"), self.project
        self.run_bg("Reading TMDb", lambda pr, ca: self.sources.fetch_tmdb(self.http, proj, key, lang, tvdb, imdb,
                                                                           progress=pr, cancel=ca),
                    lambda r: self._after_fetch("TMDb", r))

    def fetch_omdb(self):
        key = self.s.get("omdb_key")
        if not key:
            self.message("OMDb", "OMDb needs your own free key", [("Where", "Settings → Sources → OMDb key "
                                                                  "(omdbapi.com/apikey.aspx)", CAT["text"])],
                         [("Open Settings", lambda: self.show_settings(1), False), ("OK", None, True)])
            return
        _, imdb, _ = self._series_ids()
        if not imdb:
            messagebox.showinfo(APP_NAME, "Enter the series' IMDb ID (tt…) first.", parent=self)
            return
        proj = self.project
        self.run_bg("Reading OMDb", lambda pr, ca: self.sources.fetch_omdb(self.http, proj, key, imdb, pr, ca),
                    lambda r: self._after_fetch("OMDb", r))

    # ═══════════════════════════════════════════════════════════════════
    # PROJECT → UI
    # ═══════════════════════════════════════════════════════════════════
    def _series_fields_to_project(self):
        s = self.project.series
        for f, var in (("title", self.var_title), ("tvdb_id", self.var_tvdb), ("imdb_id", self.var_imdb)):
            v = var.get().strip()
            cur = s.get(f) or ""
            if v != str(cur):
                s.set_user(f, v)
        y = self.var_year.get().strip()
        if y != str(s.get("year") or ""):
            s.set_user("year", int(y) if y.isdigit() else "")

    def _project_to_ui(self):
        p, s = self.project, self.project.series
        self.var_title.set(s.get("title") or "")
        self.var_year.set(str(s.get("year") or ""))
        self.var_tvdb.set(s.get("tvdb_id") or "")
        self.var_imdb.set(s.get("imdb_id") or "")
        bits = [x for x in (s.get("network"), s.get("status"), ", ".join(s.get("genres") or [])) if x]
        if s.get("tvmaze_id"):
            bits.append(f"TVmaze {s.get('tvmaze_id')}")
        self.series_info.configure(text=" · ".join(bits))
        self.folder_lbl.configure(text=(p.show_dir + f"\n{len(p.videos)} video(s)") if p.show_dir else
                                  "No series folder — files go to the output folder")
        lines = [f"{k}: {v} episode(s)" for k, v in self._source_counts.items()]
        self.sources_lbl.configure(text="\n".join(lines) or "none yet — Import ▾ or Fetch ▾")
        self.refresh_episodes()
        self.refresh_artwork()
        self.refresh_conflicts()
        if p.show_dir:
            self.open_link.pack(side="right", padx=8)

    def _ep_status(self, ep):
        vids = self.project.videos_for(ep.key)
        if not vids:
            return "", None, None, None
        v = vids[0]
        stem = os.path.join(v.folder, v.stem)
        nfo = os.path.isfile(stem + ".nfo")
        xml = os.path.isfile(os.path.join(v.folder, "metadata", v.stem + ".xml"))
        img = bool(self.engine.existing_episode_image(v.path))
        name = os.path.basename(v.path) + (f"  (+{len(vids) - 1})" if len(vids) > 1 else "")
        return name, nfo, xml, img

    def refresh_episodes(self):
        t = self.ep_tree
        sel = t.selection()
        t.delete(*t.get_children())
        flt, q = self.var_filter.get(), self.var_search.get().strip().lower()
        rules = self.project.rules
        n = 0
        folder_mode = bool(self.project.show_dir)
        for ep in self.project.sorted_episodes():
            video, nfo, xml, img = self._ep_status(ep)
            conflict = any(ep.conflicts(f) and "user" not in ep.all(f) for f in ("title", "aired"))
            problem = (folder_mode and not video and ep.season != 0) or not ep.get("title", rules) or conflict
            if flt == "problems" and not problem:
                continue
            if flt == "novideo" and video:
                continue
            if flt == "noimage" and (img or not video):
                continue
            title = ep.get("title", rules) or ""
            if q and q not in f"{ep.code} {title} {video}".lower():
                continue
            srcs = sorted({src for by in ep.values.values() for src in by} - {"user"})
            vals = (ep.code, title, ep.get("aired", rules) or "", ep.get("runtime", rules) or "",
                    video or ("— no video —" if folder_mode and ep.season != 0 else ""), SYMBOL[nfo], SYMBOL[xml],
                    SYMBOL[img], ", ".join(self.core.SOURCE_LABELS.get(x, x) for x in srcs), "⚑" if problem else "")
            t.insert("", "end", iid=f"{ep.season}:{ep.episode}", values=vals, tags=("warn",) if problem else ())
            n += 1
        orphans = [v for v in self.project.videos if not v.keys]
        for v in orphans:
            t.insert("", "end", values=("?", "(no episode found)", "", "", os.path.basename(v.path), "", "", "", "",
                                        "⚑"), tags=("bad",))
        self.ep_count_lbl.configure(text=f"{n} of {len(self.project.episodes)} episode(s)"
                                    + (f" · {len(orphans)} video(s) without an episode" if orphans else ""))
        for s in sel:
            if t.exists(s):
                t.selection_add(s)

    def _selected_episode(self):
        sel = self.ep_tree.selection()
        if not sel or ":" not in sel[0]:
            return None
        s, e = sel[0].split(":")
        return self.project.episodes.get((int(s), int(e)))

    def show_detail(self):
        ep = self._selected_episode()
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        if ep is not None:
            rules = self.project.rules
            g = lambda f: ep.get(f, rules)
            src = lambda f: self.core.SOURCE_LABELS.get(ep.source(f, rules) or "", "")
            d.insert("end", f"{ep.code}  {g('title') or ''}", "h")
            d.insert("end", f"   ({src('title')})\n", "dim")
            people = []
            if g("directors"):
                people.append("Director: " + ", ".join(g("directors")))
            if g("writers"):
                people.append("Writer: " + ", ".join(g("writers")))
            if g("guests"):
                people.append("Guests: " + ", ".join(f"{x[0]} ({x[1]})" if x[1] else x[0] for x in g("guests")[:8]))
            ids = " · ".join(f"{k} {g(f)}" for k, f in (("TheTVDB", "tvdb_id"), ("IMDb", "imdb_id"),
                                                        ("TVmaze", "tvmaze_id")) if g(f))
            d.insert("end", " · ".join(x for x in (g("aired"), f"{g('runtime')} min" if g("runtime") else "",
                                                    g("mpaa"), g("network"), ids) if x) + "\n", "dim")
            if people:
                d.insert("end", " | ".join(people) + "\n", "dim")
            d.insert("end", (g("plot") or "") + "\n")
            if ep.images:
                d.insert("end", "Images: " + ", ".join(self.core.SOURCE_LABELS.get(s, s) for _, s in ep.images[:6]),
                         "dim")
        d.configure(state="disabled")

    # ── Sources tab (F17) ──────────────────────────────────────────────
    def _fmt(self, v):
        if isinstance(v, (list, tuple)):
            parts = []
            for x in v:
                if isinstance(x, (list, tuple)):
                    parts.append(f"{x[0]} ({x[1]})" if len(x) > 1 and x[1] else str(x[0]))
                else:
                    parts.append(str(x))
            return "; ".join(parts)
        return str(v).replace("\n", " ")

    def bg_light(self, work, done):
        """Small background jobs (image previews): no busy state, failures only in the status bar."""
        def runner():
            try:
                res = work()
            except Exception as e:
                msg = str(e) or type(e).__name__
                self.post(lambda: self.status(f"Could not load the image: {msg}"))
                return
            self.post(lambda: done(res))
        threading.Thread(target=runner, daemon=True).start()

    def refresh_sources_tab(self):
        t = self.src_tree
        t.delete(*t.get_children())
        ep = self._selected_episode()
        if ep is None:
            self.src_head.configure(text="Select an episode in the Episodes tab.")
            return
        rules = self.project.rules
        self.src_head.configure(text=f"{ep.code} — {ep.get('title', rules) or ''}")
        for f in self.src_fields:
            allv = ep.all(f)
            if not allv:
                continue
            used = ep.source(f, rules)
            row = [f, self.core.SOURCE_LABELS.get(used, used)]
            for s in self.core.SOURCES[1:]:
                v = ep.values.get(f, {}).get(s)
                row.append(self._fmt(v)[:120] if v not in (None, "", []) else "")
            t.insert("", "end", iid=f, values=row, tags=("warn",) if ep.conflicts(f) else ())

    def _source_click(self, e):
        ep = self._selected_episode()
        row, col = self.src_tree.identify_row(e.y), self.src_tree.identify_column(e.x)
        if ep is None or not row or not col:
            return
        idx = int(col[1:]) - 1
        cols = ["field", "chosen"] + self.core.SOURCES[1:]
        if idx < 2:
            return
        src = cols[idx]
        v = ep.values.get(row, {}).get(src)
        if v in (None, "", []):
            return
        ep.set_user(row, v)
        self.log(f"{ep.code}: {row} ← {self.core.SOURCE_LABELS.get(src, src)}", "info")
        self.refresh_sources_tab()
        self.refresh_episodes()
        self.refresh_conflicts()

    def _clear_user_choices(self):
        ep = self._selected_episode()
        if ep is not None:
            ep.user.clear()
            self.refresh_sources_tab()
            self.refresh_episodes()

    def refresh_conflicts(self):
        t = self.conf_tree
        t.delete(*t.get_children())
        for ep in self.project.sorted_episodes():
            for f in ("title", "aired", "runtime"):
                if ep.conflicts(f) and "user" not in ep.all(f):
                    vals = "   |   ".join(f"{self.core.SOURCE_LABELS.get(s, s)}: {self._fmt(v)}"
                                         for s, v in ep.all(f).items())
                    t.insert("", "end", values=(ep.code, f, vals), tags=(f"{ep.season}:{ep.episode}",))

    def _conflict_goto(self, e):
        sel = self.conf_tree.selection()
        if sel:
            tags = self.conf_tree.item(sel[0], "tags")
            if tags and self.ep_tree.exists(tags[0]):
                self.ep_tree.selection_set(tags[0])
                self.ep_tree.see(tags[0])
                self.refresh_sources_tab()

    # ── Artwork tab (F8) ───────────────────────────────────────────────
    ART_LABELS = {"poster": "Poster", "fanart": "Fanart / backdrop", "banner": "Banner", "clearlogo": "Logo",
                  "clearart": "Clear art", "landscape": "Landscape / thumb"}

    def _art_items(self, key):
        if key.startswith("season:"):
            return self.project.season(int(key.split(":")[1])).posters
        return self.project.series.artwork.get(key, [])

    def _art_current(self, key):
        items = self._art_items(key)
        if key.startswith("season:"):
            return items[0] if items else (None, None)
        chosen = self.project.series.chosen_art.get(key)
        if chosen:
            return next(((u, s) for u, s in items if u == chosen), (chosen, "user"))
        for src in ("user", "local", "tvdb", "tmdb", "tvmaze"):
            for u, s in items:
                if s == src:
                    return u, s
        return items[0] if items else (None, None)

    def refresh_artwork(self):
        t = self.art_tree
        t.delete(*t.get_children())
        for k in self.engine.ART_KINDS:
            u, s = self._art_current(k)
            t.insert("", "end", iid=k, values=(self.ART_LABELS[k], len(self._art_items(k)),
                                               self.core.SOURCE_LABELS.get(s, s or ""), u or "—"),
                     tags=() if u else ("dim",))
        for n in sorted(self.project.seasons):
            se = self.project.seasons[n]
            u, s = (se.posters[0] if se.posters else (None, None))
            t.insert("", "end", iid=f"season:{n}", values=("Specials poster" if n == 0 else f"Season {n} poster",
                                                           len(se.posters), self.core.SOURCE_LABELS.get(s, s or ""),
                                                           u or "—"), tags=() if u else ("dim",))

    def _art_selected(self):
        sel = self.art_tree.selection()
        if not sel:
            return
        u, _ = self._art_current(sel[0])
        if not u:
            self.art_preview.configure(image="", text="no image")
            return
        self.art_preview.configure(image="", text="loading…")

        def work():
            data = open(u, "rb").read() if os.path.isfile(u) else self.sources.download(self.http, u)
            img = self.media.image_from_bytes(data)
            return img

        def done(img):
            from PIL import ImageTk
            im = img.copy()
            im.thumbnail((300, 420))
            self._art_img = ImageTk.PhotoImage(im)
            self.art_preview.configure(image=self._art_img, text="")
            self.status(f"{img.size[0]}×{img.size[1]}"
                        + ("  — smaller than recommended" if self.media.too_small(
                            sel[0] if not sel[0].startswith("season:") else "season_poster", img.size) else ""))
        self.bg_light(work, done)

    def _art_cycle(self, step):
        sel = self.art_tree.selection()
        if not sel:
            return
        key = sel[0]
        items = self._art_items(key)
        if len(items) < 2:
            return
        cur, _ = self._art_current(key)
        i = next((n for n, (u, _) in enumerate(items) if u == cur), 0)
        nxt = items[(i + step) % len(items)]
        if key.startswith("season:"):
            items.remove(nxt)
            items.insert(0, nxt)
        else:
            self.project.series.chosen_art[key] = nxt[0]
        self.refresh_artwork()
        self.art_tree.selection_set(key)

    def _art_choose_file(self):
        sel = self.art_tree.selection()
        if not sel:
            return
        p = filedialog.askopenfilename(parent=self, title="Choose an image",
                                       filetypes=[("Images", "*.jpg *.jpeg *.png *.webp"), ("All", "*.*")])
        if p:
            self._art_set_local(sel[0], os.path.normpath(p))

    def _art_set_local(self, key, path):
        items = self._art_items(key) if key.startswith("season:") else \
            self.project.series.artwork.setdefault(key, [])
        items.insert(0, (path, "user"))
        if not key.startswith("season:"):
            self.project.series.chosen_art[key] = path
        self.refresh_artwork()

    def add_images(self, paths=None):
        paths = paths or filedialog.askopenfilenames(parent=self, title="Images for the series (posters, fanart…)",
                                                     filetypes=[("Images", "*.jpg *.jpeg *.png *.webp")])
        if not paths:
            return
        from PIL import Image
        rows = []
        for p in paths:
            try:
                with Image.open(p) as im:
                    im.load()
                    kind = self.media.classify_artwork(im)
                    size = im.size
            except Exception:
                kind, size = "", (0, 0)
            rows.append([os.path.normpath(p), kind, size])
        res = self.artwork_inbox_dialog(rows)
        for p, kind in res or []:
            if kind.startswith("season:"):
                self.project.season(int(kind.split(":")[1])).posters.insert(0, (p, "user"))
            elif kind:
                self._art_set_local(kind, p)
        self.refresh_artwork()
        self.nb.select(2)

    # ═══════════════════════════════════════════════════════════════════
    # GENERATE: PLAN → CHANGES → WRITE (F10)
    # ═══════════════════════════════════════════════════════════════════
    def _opts(self):
        self._series_fields_to_project()
        o = {"profiles": [p for p, v in self.var_prof.items() if v.get()], "episode_images": self.var_epimg.get(),
             "artwork": self.var_art.get(), "replace_images": self.var_replace_img.get(),
             "out_dir": self.var_out.get().strip()}
        for k in ("replace_text", "series_files", "thumb_width", "image_order", "language", "rating_country", "cast",
                  "crew", "ratings", "techinfo", "image_urls", "max_title_len"):
            o[k] = self.s.get(k, DEFAULT_SETTINGS.get(k))
        o["pad_16_9"] = self.s.get("thumb_pad_16_9", False)
        o["name_pattern"] = self.s.get("rename_pattern", "{show} - {code} - {title}")
        for k in ("nfo_template", "xml_template"):
            path = self.s.get(k)
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    o[k + "_text"] = self.core.decode_bytes(f.read())[0]
        return o

    def generate_all(self):
        if not self.project.episodes:
            messagebox.showinfo(APP_NAME, "There are no episodes yet: open a series folder and/or import the episode "
                                "list.", parent=self)
            return
        o = self._opts()
        if not o["profiles"]:
            messagebox.showinfo(APP_NAME, "Tick at least one server (left panel).", parent=self)
            return
        if not self.project.show_dir and not o["out_dir"]:
            messagebox.showinfo(APP_NAME, "Open a series folder, or choose an output folder (left panel).", parent=self)
            return
        proj, http, ff = self.project, self.http, self.ffmpeg

        def work(progress, cancel):
            return self.engine.plan(proj, o, http, ff)

        def done(changes):
            self.changes = changes
            self._fill_changes()
            self.nb.select(3)
            todo = [c for c in changes if c.selected]
            self.status(f"{len(todo)} file(s) to write — check the list, then press Write selected.")
            issues = [h for h in self.engine.health(proj, changes) if h[0] == "error"]
            if issues:
                self.log(f"Check found {len(issues)} error(s) — see the Check tab", "warn")
                self._fill_check(self.engine.health(proj, changes))
        self.run_bg("Planning the files", work, done)

    def _fill_changes(self):
        t = self.ch_tree
        t.delete(*t.get_children())
        root = self.project.show_dir or self.var_out.get().strip()
        counts = {}
        for i, c in enumerate(self.changes):
            counts[c.action] = counts.get(c.action, 0) + 1
            rel = os.path.relpath(c.path, root) if root and c.path.lower().startswith(root.lower()) else c.path
            tag = {"create": "ok", "replace": "warn", "skip": "bad"}.get(c.action, "dim")
            t.insert("", "end", iid=str(i), values=("☑" if c.selected else "☐", c.action, c.kind, rel,
                                                     c.note or c.source), tags=(tag,))
        sel = sum(1 for c in self.changes if c.selected)
        self.changes_lbl.configure(text=(f"{sel} selected · " + " · ".join(f"{v} {k}" for k, v in counts.items()))
                                   if self.changes else "Press ▶ Generate all to see every file before anything is "
                                                        "written.")
        self.write_btn.configure(state="normal" if sel else "disabled")

    def _change_click(self, e):
        if self.ch_tree.identify_column(e.x) != "#1":
            return
        row = self.ch_tree.identify_row(e.y)
        if not row:
            return
        c = self.changes[int(row)]
        if c.action in ("create", "replace", "keep", "same"):
            c.selected = not c.selected
            if c.selected and c.action in ("keep", "same"):
                c.action = "replace"
            self._fill_changes()
            self.ch_tree.selection_set(row)

    def _tick_all(self, on):
        for c in self.changes:
            if c.action in ("create", "replace"):
                c.selected = on
        self._fill_changes()

    def _show_diff(self):
        sel = self.ch_tree.selection()
        d = self.diff
        d.configure(state="normal")
        d.delete("1.0", "end")
        if sel:
            c = self.changes[int(sel[0])]
            if c.data is not None:
                if c.old_text is not None:
                    for line in difflib.unified_diff(c.old_text.splitlines(), c.data.splitlines(), "on disk", "new",
                                                     lineterm="", n=2):
                        d.insert("end", line + "\n", "add" if line.startswith("+") else "del" if line.startswith("-")
                                 else "dim")
                    if c.action == "same":
                        d.insert("end", "(no change except the date added)\n", "dim")
                else:
                    d.insert("end", c.data)
            else:
                d.insert("end", f"Image: {c.path}\nFrom: {c.source}\n" + ("Already there — kept (tick to replace).\n"
                                                                         if c.action == "keep" else ""), "dim")
        d.configure(state="disabled")

    def write_changes(self):
        todo = [c for c in self.changes if c.selected and c.action in ("create", "replace")]
        if not todo:
            return
        rep = sum(1 for c in todo if c.action == "replace")
        root = self.project.show_dir or self.var_out.get().strip()
        if not messagebox.askyesno(APP_NAME, f"Write {len(todo)} file(s)?\n\n{rep} existing file(s) will be replaced "
                                   f"— a backup copy of each goes to\n{os.path.join(root, '.kfg_backup')}\n\n"
                                   "Undo last run restores them.", parent=self):
            return
        self.nb.select(5)
        changes = self.changes

        def work(progress, cancel):
            return self.engine.apply(changes, root, self.log, progress, cancel)

        def done(st):
            self.changes = []
            self._fill_changes()
            self.refresh_episodes()
            ok = st["failed"] == 0
            rows = [("Written", f"{st['written']} file(s)", CAT["green"] if ok else CAT["text"]),
                    ("Folder", root, CAT["subtext"])]
            if st["failed"]:
                rows.append(("Failed", f"{st['failed']}: " + "; ".join(st["errors"][:4]), CAT["red"]))
            if st["manifest"]:
                rows.append(("Backup", os.path.dirname(st["manifest"]), CAT["subtext"]))
            self.message("Files written" if ok else "Written with errors",
                         "✓  Files written" if ok else "⚠  Some files could not be written", rows,
                         [("Open folder", lambda: self._open_folder(root), False), ("OK", None, True)])
            self.status(f"Done — {st['written']} file(s) written" + (f", {st['failed']} failed" if st["failed"] else ""))
        self.run_bg("Writing the files", work, done)

    def undo_last(self):
        root = self.project.show_dir or self.var_out.get().strip()
        m = self.engine.last_manifest(root) if root else ""
        if not m:
            messagebox.showinfo(APP_NAME, "There is no run to undo in this folder.", parent=self)
            return
        with open(m, encoding="utf-8") as f:
            n = len(json.load(f)["entries"])
        if not messagebox.askyesno(APP_NAME, f"Undo the last run ({n} file(s))?\n\nReplaced files come back from the "
                                   "backup; files the run created are moved into the backup folder (not deleted).",
                                   parent=self):
            return
        st = self.engine.undo(m, self.log)
        self.log(f"Undo: {st['restored']} restored, {st['skipped']} left as they are", "ok" if not st["skipped"]
                 else "warn")
        for msg in st["messages"]:
            self.log("   " + msg, "warn")
        self.refresh_episodes()

    def rename_videos(self):
        if not self.project.videos:
            messagebox.showinfo(APP_NAME, "Open a series folder first.", parent=self)
            return
        pairs = self.engine.plan_renames(self.project, self.s.get("rename_pattern", "{show} - {code} - {title}"),
                                         self.s.get("max_title_len", 80))
        if not pairs:
            messagebox.showinfo(APP_NAME, "Every video already has the name of the pattern.", parent=self)
            return
        conflicts = self.engine.rename_conflicts(pairs)
        if not self.rename_dialog(pairs, conflicts):
            return
        st = self.engine.apply_renames(pairs, self.project.show_dir, self.log)
        if st["error"]:
            self.message("Rename", "✗  Nothing was renamed", [("Why", st["error"], CAT["red"])], [("OK", None, True)])
            return
        self.log(f"Renamed {st['renamed']} file(s) — Undo last run reverses it", "ok")
        self.open_series(self.project.show_dir)

    # ── Check (F9) ─────────────────────────────────────────────────────
    def run_check(self):
        self._fill_check(self.engine.health(self.project, self.changes))
        self.nb.select(4)

    def _fill_check(self, items):
        t = self.check_tree
        t.delete(*t.get_children())
        for level, where, text in items:
            t.insert("", "end", values=(level, where, text), tags=({"error": "bad", "warn": "warn"}.get(level, "dim"),))
        n = {lv: sum(1 for x in items if x[0] == lv) for lv in ("error", "warn", "info")}
        self.check_lbl.configure(text=f"{n['error']} error(s) · {n['warn']} warning(s) · {n['info']} note(s)"
                                 if items else "No problems found.")

    # ── poster in the left panel ───────────────────────────────────────
    def _load_poster(self):
        u, _ = self._art_current("poster")
        if not u:
            return

        def work():
            data = open(u, "rb").read() if os.path.isfile(u) else self.sources.download(self.http, u)
            return self.media.image_from_bytes(data)

        def done(img):
            from PIL import ImageTk
            im = img.copy()
            im.thumbnail((90, 135))
            self._poster_img = ImageTk.PhotoImage(im)
            self.poster_lbl.configure(image=self._poster_img, text="", width=90, height=135)
        self.bg_light(work, done)

    # ═══════════════════════════════════════════════════════════════════
    # DIALOGS
    # ═══════════════════════════════════════════════════════════════════
    def dialog(self, title, w, h, modal=True):
        d = tk.Toplevel(self)
        d.title(title)
        d.configure(bg=CAT["base"])
        d.transient(self)
        self.update_idletasks()
        d.geometry(f"{w}x{h}+{self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)}"
                   f"+{self.winfo_rooty() + max(0, (self.winfo_height() - h) // 3)}")
        d.bind("<Escape>", lambda e: d.destroy())
        if modal:
            d.grab_set()
        return d

    def message(self, title, headline, rows, buttons, w=620):
        """App-drawn message (dark): rows [(label, text, colour)], buttons [(text, command or None, primary)]."""
        d = self.dialog(title, w, 140 + 26 * len(rows))
        d.resizable(False, False)
        tk.Label(d, text=headline, font=F["head"], fg=CAT["text"], bg=CAT["base"], anchor="w").pack(
            fill="x", padx=20, pady=(16, 8))
        g = tk.Frame(d, bg=CAT["base"])
        g.pack(fill="x", padx=20)
        g.columnconfigure(1, weight=1)
        for r, (label, text, col) in enumerate(rows):
            tk.Label(g, text=label, font=F["small"], fg=CAT["subtext"], bg=CAT["base"], anchor="nw").grid(
                row=r, column=0, sticky="nw", padx=(0, 14), pady=2)
            tk.Label(g, text=text, font=F["body"], fg=col, bg=CAT["base"], anchor="w", justify="left",
                     wraplength=w - 200).grid(row=r, column=1, sticky="w", pady=2)
        br = tk.Frame(d, bg=CAT["base"])
        br.pack(side="bottom", fill="x", padx=20, pady=14)
        for text, cmd, primary in reversed(buttons):
            self.btn(br, text, lambda c=cmd: (d.destroy(), c() if c else None),
                     "primary" if primary else "", font=F["body"]).pack(side="right", padx=(6, 0))
        d.update_idletasks()
        d.geometry(f"{w}x{max(d.winfo_reqheight(), 150)}")
        return d

    def mapping_dialog(self, table, mapping, name):
        """Column matching (user note): each field the app knows, the file's columns to pick from, a sample value."""
        C = CAT
        d = self.dialog("Match the columns", 760, 600)
        res = {"ok": False}
        tk.Label(d, text=f"Which column of {name} holds each field?", font=F["head"], fg=C["text"], bg=C["base"],
                 anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(d, text="Suggestions are pre-selected (names seen in real files, also Portuguese). Season, Episode and "
                         "Title are needed. Your choice is remembered for files with the same columns.",
                 font=F["small"], fg=C["subtext"], bg=C["base"], anchor="w", justify="left", wraplength=720).pack(
            fill="x", padx=18)
        g = tk.Frame(d, bg=C["base"])
        g.pack(fill="both", expand=True, padx=18, pady=10)
        headers = [h for h in table.headers if str(h).strip()]
        options = ["— not in this file —"] + headers
        first = next((r for _, r in table.data if any(c.strip() for c in r)), [])
        boxes = {}
        for i, (k, label, req, _) in enumerate(self.core.FIELDS):
            tk.Label(g, text=label + (" *" if req else ""), font=F["small"], fg=C["text"] if req else C["subtext"],
                     bg=C["base"], anchor="w", width=20).grid(row=i, column=0, sticky="w", pady=1)
            cb = ttk.Combobox(g, values=options, state="readonly", width=30, font=F["small"])
            cb.set(mapping.get(k) or options[0])
            cb.grid(row=i, column=1, sticky="w", padx=6)
            sample = tk.Label(g, text="", font=F["tiny"], fg=C["overlay"], bg=C["base"], anchor="w", width=40)
            sample.grid(row=i, column=2, sticky="w")

            def upd(e=None, cb=cb, sample=sample):
                h = cb.get()
                v = first[table.headers.index(h)] if h in table.headers and table.headers.index(h) < len(first) else ""
                sample.configure(text=middle_ellipsis(str(v), 50))
            cb.bind("<<ComboboxSelected>>", upd)
            upd()
            boxes[k] = cb
        warn = tk.Label(d, text="", font=F["small"], fg=C["red"], bg=C["base"])
        warn.pack(fill="x", padx=18)

        def ok():
            m = {k: (cb.get() if cb.get() in table.headers else None) for k, cb in boxes.items()}
            miss = self.core.missing_required(m)
            if miss:
                warn.configure(text="Choose a column for: " + ", ".join(self.core.FIELD_LABELS[x] for x in miss))
                return
            res.update(ok=True, m=m)
            d.destroy()
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=18, pady=12)
        self.btn(br, "Import", ok, "primary", font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)
        tk.Label(br, text=f"Columns found: {len(headers)} · rows: {len(table.data)}", font=F["tiny"], fg=C["overlay"],
                 bg=C["base"]).pack(side="left")
        self.wait_window(d)
        return res.get("m") if res["ok"] else None

    def report_dialog(self, report, n_ok, name) -> bool:
        """Import report (user note): every ignored or doubtful line with its number — nothing dropped silently."""
        C = CAT
        d = self.dialog("Import report", 860, 480)
        res = {"ok": False}
        tk.Label(d, text=f"{n_ok} episode(s) read from {name}. These lines need a look:", font=F["head"],
                 fg=C["text"], bg=C["base"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
        fr, t = self._tree(d, ("line", "kind", "message"), {"line": 60, "kind": 130, "message": 600},
                           {"line": "Line", "kind": "Kind", "message": "What"}, stretch=("message",))
        fr.pack(fill="both", expand=True, padx=18)
        for r in report:
            t.insert("", "end", values=(r["line"], r["kind"].replace("_", " "), r["message"]),
                     tags=("dim",) if r["kind"] in ("blank", "separators") else ("warn",))
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=18, pady=12)
        self.btn(br, f"Import the {n_ok} episode(s)", lambda: (res.update(ok=True), d.destroy()), "primary",
                 font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)
        tk.Label(br, text="Fix the lines in the file and import again to include them.", font=F["tiny"],
                 fg=C["overlay"], bg=C["base"]).pack(side="left")
        self.wait_window(d)
        return res["ok"]

    def link_dialog(self):
        C = CAT
        d = self.dialog("New CSV from a link", 640, 300)
        res = {}
        tk.Label(d, text="Paste a link or an ID", font=F["head"], fg=C["text"], bg=C["base"], anchor="w").pack(
            fill="x", padx=18, pady=(16, 2))
        tk.Label(d, text="TheTVDB (any page of the series, or its ID), TVmaze, IMDb (tt…) or TMDb. The app reads the "
                         "episode list and can save it as a CSV.", font=F["small"], fg=C["subtext"], bg=C["base"],
                 anchor="w", justify="left", wraplength=600).pack(fill="x", padx=18)
        var = tk.StringVar()
        try:
            clip = self.clipboard_get().strip()
            if self.sources.parse_link(clip)[0]:
                var.set(clip)
        except tk.TclError:
            pass
        e = tk.Entry(d, textvariable=var, font=F["body"], bg=C["surface0"], fg=C["text"], relief="flat",
                     insertbackground=C["text"])
        e.pack(fill="x", padx=18, pady=10, ipady=5)
        e.focus_set()
        det = tk.BooleanVar(value=self.s.get("tvdb_episode_details", True))
        tk.Checkbutton(d, text="TheTVDB: also read each episode page (runtime, rating, IMDb, writers, directors, guest "
                               "stars, titles in your language — about 1 s per episode)", variable=det,
                       font=F["small"], fg=C["text"], bg=C["base"], selectcolor=C["surface0"],
                       activebackground=C["base"], wraplength=590, justify="left", anchor="w").pack(fill="x", padx=14)
        msg = tk.Label(d, text="", font=F["small"], fg=C["red"], bg=C["base"])
        msg.pack(fill="x", padx=18)

        def ok(_e=None):
            site, val = self.sources.parse_link(var.get())
            if not site:
                msg.configure(text="Not a TheTVDB, TVmaze, IMDb or TMDb link.")
                return
            self.s["tvdb_episode_details"] = det.get()
            save_settings(self.s)
            res["r"] = (site, val, det.get())
            d.destroy()
        e.bind("<Return>", ok)
        br = tk.Frame(d, bg=C["base"])
        br.pack(side="bottom", fill="x", padx=18, pady=12)
        self.btn(br, "Read", ok, "primary", font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)
        self.wait_window(d)
        return res.get("r")

    def pick_show(self, shows):
        C = CAT
        d = self.dialog("Which series?", 640, 380)
        res = {}
        tk.Label(d, text="TVmaze found several series — pick one:", font=F["head"], fg=C["text"], bg=C["base"],
                 anchor="w").pack(fill="x", padx=18, pady=(14, 6))
        fr, t = self._tree(d, ("name", "year", "net", "ids"), {"name": 260, "year": 60, "net": 140, "ids": 140},
                           {"name": "Name", "year": "Year", "net": "Network", "ids": "TheTVDB / IMDb"})
        fr.pack(fill="both", expand=True, padx=18)
        for i, s in enumerate(shows[:20]):
            net = (s.get("network") or s.get("webChannel") or {}).get("name", "")
            ext = s.get("externals") or {}
            t.insert("", "end", iid=str(i), values=(s.get("name"), (s.get("premiered") or "")[:4], net,
                                                     f"{ext.get('thetvdb') or '–'} / {ext.get('imdb') or '–'}"))

        def ok(_e=None):
            sel = t.selection()
            if sel:
                res["s"] = shows[int(sel[0])]
                d.destroy()
        t.bind("<Double-1>", ok)
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=18, pady=12)
        self.btn(br, "Use this series", ok, "primary", font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)
        self.wait_window(d)
        return res.get("s")

    def artwork_inbox_dialog(self, rows):
        C = CAT
        d = self.dialog("Add images", 820, 420)
        res = {}
        tk.Label(d, text="Each image was sorted by its shape — change the kind where needed.", font=F["head"],
                 fg=C["text"], bg=C["base"], anchor="w").pack(fill="x", padx=18, pady=(14, 6))
        g = tk.Frame(d, bg=C["base"])
        g.pack(fill="both", expand=True, padx=18)
        kinds = ["(skip)"] + [self.ART_LABELS[k] for k in self.engine.ART_KINDS] + \
            [f"Season {n} poster" for n in sorted(self.project.seasons) or [1]]
        keys = [""] + list(self.engine.ART_KINDS) + [f"season:{n}" for n in sorted(self.project.seasons) or [1]]
        boxes = []
        for i, (p, kind, size) in enumerate(rows[:14]):
            tk.Label(g, text=middle_ellipsis(os.path.basename(p), 50), font=F["small"], fg=C["text"], bg=C["base"],
                     anchor="w", width=48).grid(row=i, column=0, sticky="w")
            tk.Label(g, text=f"{size[0]}×{size[1]}", font=F["tiny"], fg=C["overlay"], bg=C["base"], width=11).grid(
                row=i, column=1)
            cb = ttk.Combobox(g, values=kinds, state="readonly", width=22, font=F["small"])
            cb.set(kinds[keys.index(kind)] if kind in keys else kinds[0])
            cb.grid(row=i, column=2, padx=6, pady=1)
            boxes.append((p, cb))

        def ok():
            res["r"] = [(p, keys[kinds.index(cb.get())]) for p, cb in boxes if cb.get() != kinds[0]]
            d.destroy()
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=18, pady=12)
        self.btn(br, "Use these", ok, "primary", font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)
        self.wait_window(d)
        return res.get("r")

    def rename_dialog(self, pairs, conflicts) -> bool:
        C = CAT
        d = self.dialog("Rename videos", 980, 520)
        res = {"ok": False}
        tk.Label(d, text=f"{len(pairs)} file(s) will be renamed (videos with their subtitles, NFO, images and "
                         "metadata files).", font=F["head"], fg=C["text"], bg=C["base"], anchor="w").pack(
            fill="x", padx=18, pady=(14, 6))
        fr, t = self._tree(d, ("old", "new"), {"old": 450, "new": 450}, {"old": "Now", "new": "New name"},
                           stretch=("old", "new"))
        fr.pack(fill="both", expand=True, padx=18)
        for a, b in pairs:
            t.insert("", "end", values=(os.path.basename(a), os.path.basename(b)))
        if conflicts:
            tk.Label(d, text="✗  " + "; ".join(conflicts[:4]), font=F["small"], fg=C["red"], bg=C["base"],
                     anchor="w", wraplength=940, justify="left").pack(fill="x", padx=18, pady=4)
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=18, pady=12)
        b = self.btn(br, "Rename", lambda: (res.update(ok=True), d.destroy()), "primary", font=F["body"])
        b.pack(side="right")
        if conflicts:
            b.configure(state="disabled")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)
        tk.Label(br, text=f"Pattern: {self.s.get('rename_pattern')} (Settings → Files) · Undo last run reverses it",
                 font=F["tiny"], fg=C["overlay"], bg=C["base"]).pack(side="left")
        self.wait_window(d)
        return res["ok"]

    def edit_episode(self):
        ep = self._selected_episode()
        if ep is None:
            return
        C = CAT
        rules = self.project.rules
        d = self.dialog(f"Episode {ep.code}", 780, 660)
        tk.Label(d, text=f"{ep.code} — your values win over every database", font=F["head"], fg=C["text"],
                 bg=C["base"], anchor="w").pack(fill="x", padx=18, pady=(14, 8))
        g = tk.Frame(d, bg=C["base"])
        g.pack(fill="both", expand=True, padx=18)
        g.columnconfigure(1, weight=1)
        fields = [("title", "Title"), ("aired", "Aired (YYYY-MM-DD)"), ("runtime", "Runtime (min)"),
                  ("mpaa", "Content rating"), ("tvdb_id", "TheTVDB ID"), ("imdb_id", "IMDb ID"),
                  ("directors", "Directors (, separated)"), ("writers", "Writers (, separated)"),
                  ("network", "Network")]
        vars_ = {}
        for i, (f, label) in enumerate(fields):
            tk.Label(g, text=label, font=F["small"], fg=C["subtext"], bg=C["base"], anchor="w").grid(
                row=i, column=0, sticky="w", pady=2)
            v = ep.get(f, rules)
            var = tk.StringVar(value=", ".join(v) if isinstance(v, list) else str(v or ""))
            tk.Entry(g, textvariable=var, font=F["small"], bg=C["surface0"], fg=C["text"], relief="flat",
                     insertbackground=C["text"]).grid(row=i, column=1, sticky="ew", padx=6, ipady=2)
            vars_[f] = (var, var.get())
        n = len(fields)
        tk.Label(g, text="Plot", font=F["small"], fg=C["subtext"], bg=C["base"], anchor="nw").grid(
            row=n, column=0, sticky="nw", pady=2)
        plot = tk.Text(g, height=6, font=F["small"], bg=C["surface0"], fg=C["text"], relief="flat", wrap="word",
                       insertbackground=C["text"])
        plot.grid(row=n, column=1, sticky="nsew", padx=6, pady=2)
        plot.insert("1.0", ep.get("plot", rules) or "")
        old_plot = plot.get("1.0", "end").strip()
        tk.Label(g, text="Guest stars (one per line: Name as Role)", font=F["small"], fg=C["subtext"], bg=C["base"],
                 anchor="nw", wraplength=150, justify="left").grid(row=n + 1, column=0, sticky="nw", pady=2)
        guests = tk.Text(g, height=5, font=F["small"], bg=C["surface0"], fg=C["text"], relief="flat",
                         insertbackground=C["text"])
        guests.grid(row=n + 1, column=1, sticky="nsew", padx=6, pady=2)
        guests.insert("1.0", "\n".join(f"{x[0]} as {x[1]}" if x[1] else x[0] for x in ep.get("guests", rules) or []))
        old_guests = guests.get("1.0", "end").strip()
        img = tk.Frame(d, bg=C["base"])
        img.pack(fill="x", padx=18, pady=(6, 0))
        img_lbl = tk.Label(img, text="Image: " + (", ".join(self.core.SOURCE_LABELS.get(s, s) for _, s in ep.images[:4])
                                                  or "from the video frame"), font=F["small"], fg=C["subtext"],
                           bg=C["base"])
        img_lbl.pack(side="left")

        def choose_img():
            p = filedialog.askopenfilename(parent=d, filetypes=[("Images", "*.jpg *.jpeg *.png")])
            if p:
                ep.images.insert(0, (os.path.normpath(p), "user"))
                img_lbl.configure(text="Image: your file " + os.path.basename(p))
        self.link(img, "Choose image…", choose_img, bg=C["base"]).pack(side="right")
        if self.ffmpeg and self.project.videos_for(ep.key):
            self.link(img, "Pick a frame…", lambda: self.pick_frame(ep, img_lbl, d), bg=C["base"]).pack(side="right",
                                                                                                    padx=12)

        def save():
            for f, (var, old) in vars_.items():
                v = var.get().strip()
                if v != old:
                    if f in ("directors", "writers"):
                        v = [x.strip() for x in v.split(",") if x.strip()]
                    elif f == "runtime":
                        v = self.core.parse_int(v)
                    ep.set_user(f, v)
            p = plot.get("1.0", "end").strip()
            if p != old_plot:
                ep.set_user("plot", p)
            gs = guests.get("1.0", "end").strip()
            if gs != old_guests:
                out = []
                for line in gs.splitlines():
                    m = re.match(r"^(.*?)\s+as\s+(.*)$", line.strip())
                    out.append((m.group(1), m.group(2), "") if m else (line.strip(), "", ""))
                ep.set_user("guests", [x for x in out if x[0]])
            d.destroy()
            self.refresh_episodes()
            self.show_detail()
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=18, pady=12)
        self.btn(br, "Save", save, "primary", font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)

    def pick_frame(self, ep, label, parent):
        v = self.project.videos_for(ep.key)[0]
        dur = (v.probe or {}).get("duration_s") or 0

        def work(progress, cancel):
            return self.media.best_frame(self.ffmpeg, v.path, dur, cancel, keep_all=True)[1]

        def done(cands):
            from PIL import ImageTk
            C = CAT
            d = self.dialog("Pick a frame", 1000, 360)
            refs = []
            tk.Label(d, text="Click the frame to use (the app chose the one with the highest score):", font=F["head"],
                     fg=C["text"], bg=C["base"]).pack(anchor="w", padx=14, pady=8)
            row = tk.Frame(d, bg=C["base"])
            row.pack(padx=10)
            best = max(c[1] for c in cands)
            for t, score, im in cands:
                th = im.copy()
                th.thumbnail((180, 120))
                ph = ImageTk.PhotoImage(th)
                refs.append(ph)
                col = tk.Frame(row, bg=C["green"] if score == best else C["base"], padx=2, pady=2)
                col.pack(side="left", padx=4)
                b = tk.Label(col, image=ph, cursor="hand2", bg=C["base"])
                b.pack()
                tk.Label(col, text=f"{int(t // 60)}:{int(t % 60):02d} · score {score:.0f}", font=F["tiny"],
                         fg=C["subtext"], bg=C["base"]).pack()

                def use(e, im=im, d=d):
                    out = os.path.join(str(CACHE_DIR), "frames", f"{ep.season}x{ep.episode}_{int(t)}.jpg")
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "wb") as f:
                        f.write(self.media.jpeg_bytes(im))
                    ep.images.insert(0, (out, "user"))
                    label.configure(text="Image: your frame")
                    d.destroy()
                b.bind("<Button-1>", use)
            d._refs = refs
        self.run_bg("Reading frames", work, done)

    # ── settings ───────────────────────────────────────────────────────
    def show_settings(self, tab=0):
        C = CAT
        d = self.dialog("Settings", 760, 600)
        nb = ttk.Notebook(d)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        vars_ = {}

        def page(name):
            f = tk.Frame(nb, bg=C["base"])
            nb.add(f, text=f"  {name}  ")
            return f

        def row(p, label, key, width=40, tip="", show=None):
            r = tk.Frame(p, bg=C["base"])
            r.pack(fill="x", padx=16, pady=3)
            tk.Label(r, text=label, width=26, anchor="w", font=F["small"], fg=C["subtext"], bg=C["base"]).pack(
                side="left")
            v = tk.StringVar(value=str(self.s.get(key, "") if not isinstance(self.s.get(key), list) else
                                       ", ".join(self.s.get(key))))
            e = tk.Entry(r, textvariable=v, width=width, font=F["small"], bg=C["surface0"], fg=C["text"],
                         relief="flat", insertbackground=C["text"], show=show or "")
            e.pack(side="left", fill="x", expand=True, ipady=2)
            if tip:
                Tooltip(e, tip)
            vars_[key] = v
            return r

        def check(p, label, key, tip=""):
            v = tk.BooleanVar(value=bool(self.s.get(key)))
            c = tk.Checkbutton(p, text=label, variable=v, font=F["small"], fg=C["text"], bg=C["base"],
                               selectcolor=C["surface0"], activebackground=C["base"], anchor="w")
            c.pack(fill="x", padx=12)
            if tip:
                Tooltip(c, tip)
            vars_[key] = v

        g = page("General")
        tk.Label(g, text="LANGUAGE AND RATINGS", font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(
            fill="x", padx=16, pady=(12, 2))
        row(g, "Language for titles and plots", "language", 10, "pt-BR, en, es, fr, de… — TheTVDB episode pages and "
                                                                "TMDb give titles and plots in this language")
        row(g, "Content rating country", "rating_country", 16, "TheTVDB shows ratings per country, e.g. Brazil → 16")
        tk.Label(g, text="WHAT GOES INTO THE FILES", font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(
            fill="x", padx=16, pady=(12, 2))
        check(g, "Cast and guest stars (with roles)", "cast")
        check(g, "Crew: directors, writers, creators", "crew")
        check(g, "Ratings (IMDb, TheTVDB, TMDb, TVmaze)", "ratings")
        check(g, "Technical info from the video (codec, size, audio, subtitles)", "techinfo")
        check(g, "Online image links inside NFO (Kodi fallback)", "image_urls")
        check(g, "Replace existing NFO / XML when the content changed", "replace_text")
        tk.Label(g, text="WINDOW AND UPDATES", font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(
            fill="x", padx=16, pady=(12, 2))
        check(g, "Dark title bar (Windows 10 / 11)", "dark_title_bar")
        check(g, "Check for updates at start (at most once a day; only GitHub is asked)", "check_updates")

        s2 = page("Sources")
        tk.Label(s2, text="KEYS (optional, free)", font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(
            fill="x", padx=16, pady=(12, 2))
        row(s2, "TMDb key", "tmdb_key", 40, "themoviedb.org → Settings → API (free)", show="•")
        row(s2, "OMDb key (IMDb data)", "omdb_key", 40, "omdbapi.com/apikey.aspx (free, 1,000 calls a day)", show="•")
        check(s2, "TVmaze: also read directors, writers and guest stars per episode", "tvmaze_people")
        check(s2, "TheTVDB link: also read each episode page", "tvdb_episode_details")
        tk.Label(s2, text="WHICH DATABASE THE APP BELIEVES FIRST (per field; your own edits always win)",
                 font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(fill="x", padx=16, pady=(12, 2))
        tk.Label(s2, text="Codes: csv, paste, video, tvdb, tmdb, tvmaze, omdb, file — first one with a value wins.",
                 font=F["tiny"], fg=C["overlay"], bg=C["base"], anchor="w").pack(fill="x", padx=16)
        rules_vars = {}
        rg = tk.Frame(s2, bg=C["base"])
        rg.pack(fill="x", padx=16, pady=4)
        for i, (f, order) in enumerate(self.core.DEFAULT_RULES.items()):
            tk.Label(rg, text=f, font=F["small"], fg=C["subtext"], bg=C["base"], width=10, anchor="w").grid(
                row=i // 2, column=(i % 2) * 2, sticky="w")
            v = tk.StringVar(value=", ".join(self.project.rules.get(f) or order))
            tk.Entry(rg, textvariable=v, font=F["tiny"], bg=C["surface0"], fg=C["text"], relief="flat", width=36,
                     insertbackground=C["text"]).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 12),
                                                      pady=1)
            rules_vars[f] = v

        s3 = page("Files")
        tk.Label(s3, text="NAMES AND IMAGES", font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(
            fill="x", padx=16, pady=(12, 2))
        row(s3, "Name pattern (output / renamer)", "rename_pattern", 40, "{show} {year} {code} {title} {segment}")
        row(s3, "Longest title in a file name", "max_title_len", 6)
        row(s3, "Episode image width (px)", "thumb_width", 6)
        check(s3, "Pad episode images to 16:9 (black bars)", "thumb_pad_16_9")
        row(s3, "Image sources order", "image_order", 40, "existing, csv, tvdb, tvmaze, tmdb, video")
        row(s3, "CSV export separator", "csv_export_delimiter", 4)
        tk.Label(s3, text="FFMPEG AND TEMPLATES", font=F["badge"], fg=C["mauve"], bg=C["base"], anchor="w").pack(
            fill="x", padx=16, pady=(12, 2))
        row(s3, "FFmpeg folder or ffmpeg.exe", "ffmpeg_path", 40, "Empty: PATH and C:\\FFmpeg\\bin are searched")
        row(s3, "Own NFO template (optional)", "nfo_template", 40, "R4-style template; empty = built-in writer")
        row(s3, "Own XML template (optional)", "xml_template", 40, "R4-style template; empty = built-in writer")
        tk.Label(s3, text=f"Settings file: {CONFIG_PATH}\nDownload cache: {CACHE_DIR}", font=F["tiny"],
                 fg=C["overlay"], bg=C["base"], anchor="w", justify="left").pack(fill="x", padx=16, pady=(10, 0))
        nb.select(tab)

        def save():
            for k, v in vars_.items():
                val = v.get()
                if isinstance(DEFAULT_SETTINGS.get(k), list):
                    val = [x.strip() for x in str(val).split(",") if x.strip()]
                elif isinstance(DEFAULT_SETTINGS.get(k), int) and not isinstance(DEFAULT_SETTINGS.get(k), bool):
                    val = self.core.parse_int(val) or DEFAULT_SETTINGS[k]
                elif isinstance(val, str):
                    val = val.strip()
                self.s[k] = val
            rules = {}
            for f, v in rules_vars.items():
                lst = [x.strip() for x in v.get().split(",") if x.strip() in self.core.SOURCES]
                if lst and lst != self.core.DEFAULT_RULES[f]:
                    rules[f] = lst
            self.project.rules = rules
            self.s["source_rules"] = rules
            save_settings(self.s)
            self.ffmpeg, self.ffprobe = self.media.find_ffmpeg(self.s.get("ffmpeg_path", ""))
            self._startup_labels()
            for w in [self] + [c for c in self.winfo_children() if isinstance(c, tk.Toplevel)]:
                set_dark_title_bar(w, self.s.get("dark_title_bar", True))
            d.destroy()
            self.refresh_episodes()
        br = tk.Frame(d, bg=C["base"])
        br.pack(fill="x", padx=12, pady=(0, 12))
        self.btn(br, "Save", save, "primary", font=F["body"]).pack(side="right")
        self.btn(br, "Cancel", d.destroy).pack(side="right", padx=6)

    def _startup_labels(self):
        self.ffmpeg_lbl.configure(text=("FFmpeg: " + os.path.dirname(self.ffmpeg)) if self.ffmpeg else
                                  "FFmpeg not found — no frames from videos (Settings → Files)")

    # ── help, formats, about, updates ──────────────────────────────────
    def text_dialog(self, title, text, w=760, h=640):
        d = self.dialog(title, w, h, modal=False)
        t = scrolledtext.ScrolledText(d, font=F["mono"], bg=CAT["mantle"], fg=CAT["text"], relief="flat", wrap="word",
                                      padx=16, pady=12)
        t.pack(fill="both", expand=True)
        t.insert("1.0", text)
        t.tag_configure("h", foreground=CAT["mauve"], font=("Consolas", 9, "bold"))
        for m in re.finditer(r"^([A-Z][A-Z /()0-9.&,'-]{3,})\n-{3,}", text, re.M):
            ln = text[:m.start()].count("\n") + 1
            t.tag_add("h", f"{ln}.0", f"{ln}.end")
        t.configure(state="disabled")
        self.btn(d, "Close", d.destroy, "primary", font=F["body"]).pack(pady=10)

    def show_help(self):
        self.text_dialog(f"Help — {APP_TITLE}", HELP_TEXT)

    def show_formats(self):
        self.text_dialog("Files per server", FORMATS_TEXT, 820, 560)

    def show_about(self):
        C = CAT
        d = self.dialog(f"About {APP_NAME}", 600, 560, modal=False)
        d.resizable(False, False)
        body = tk.Frame(d, bg=C["base"])
        body.pack(fill="both", expand=True, padx=26, pady=(20, 0))
        head = tk.Frame(body, bg=C["base"])
        head.pack(fill="x")
        if 64 in self._icons:
            tk.Label(head, image=self._icons[64], bg=C["base"]).pack(side="left", padx=(0, 16))
        nm = tk.Frame(head, bg=C["base"])
        nm.pack(side="left")
        tk.Label(nm, text=APP_NAME, font=F["title"], fg=C["text"], bg=C["base"]).pack(anchor="w")
        tk.Label(nm, text=f"Version {APP_VERSION.lstrip('v')}", font=F["body"], fg=C["subtext"], bg=C["base"]).pack(
            anchor="w")
        g = tk.Frame(body, bg=C["base"])
        g.pack(fill="x", pady=(16, 0))
        note = tk.Label(body, text="", font=F["tiny"], fg=C["yellow"], bg=C["base"], anchor="w")

        def ulink(parent, text, action):
            lb = tk.Label(parent, text=text, font=F["body"], fg=C["blue"], bg=C["base"], cursor="hand2", anchor="w")
            lb.bind("<Button-1>", lambda e: action())
            lb.bind("<Enter>", lambda e: lb.configure(font=F["body"] + ("underline",)))
            lb.bind("<Leave>", lambda e: lb.configure(font=F["body"]))
            return lb

        def mail():
            try:
                ok = webbrowser.open("mailto:" + APP_CONTACT)
            except Exception:
                ok = False
            if not ok:
                self.clipboard_clear()
                self.clipboard_append(APP_CONTACT)
                note.configure(text=f"No mail program found — {APP_CONTACT} was copied to the clipboard.")
        rows = [("Created by", tk.Label(g, text=APP_AUTHORS, font=F["body"], fg=C["text"], bg=C["base"])),
                ("Contact", ulink(g, APP_CONTACT, mail)),
                ("GitHub", ulink(g, APP_REPO_URL.replace("https://", ""), lambda: webbrowser.open(APP_REPO_URL))),
                ("Licence", tk.Label(g, text=APP_LICENCE, font=F["body"], fg=C["text"], bg=C["base"]))]
        for r, (label, w) in enumerate(rows):
            tk.Label(g, text=label, font=F["body"], fg=C["subtext"], bg=C["base"]).grid(row=r, column=0, sticky="w",
                                                                                      padx=(0, 18), pady=1)
            w.grid(row=r, column=1, sticky="w")
        self._about_rows = [r[0] for r in rows]
        tk.Frame(body, bg=C["surface0"], height=1).pack(fill="x", pady=12)
        tk.Label(body, text=APP_TAGLINE, font=F["body"], fg=C["text"], bg=C["base"], wraplength=540, justify="left",
                 anchor="w").pack(fill="x")
        tk.Frame(body, bg=C["surface0"], height=1).pack(fill="x", pady=12)
        for f in APP_FEATURES:
            tk.Label(body, text="•  " + f, font=F["small"], fg=C["text"], bg=C["base"], anchor="w", justify="left",
                     wraplength=540).pack(fill="x", pady=1)
        tk.Label(body, text="Data: TVmaze (CC BY-SA 4.0), TheTVDB website (personal use), TMDb and OMDb with your own "
                            "keys. This product uses the TMDb API but is not endorsed or certified by TMDb.",
                 font=F["tiny"], fg=C["overlay"], bg=C["base"], anchor="w", justify="left", wraplength=540).pack(
            fill="x", pady=(10, 0))
        note.pack(fill="x", pady=(4, 0))
        bottom = tk.Frame(d, bg=C["base"])
        bottom.pack(side="bottom", fill="x", padx=26, pady=(6, 18))
        self.btn(bottom, "  OK  ", d.destroy, "primary", font=F["body"]).pack(side="right")
        self.btn(bottom, "Check for updates", lambda: self.check_updates(True)).pack(side="right", padx=8)
        info = app_version_info(str(self.tk.call("info", "patchlevel")))
        vi = tk.Frame(d, bg=C["base"])
        vi.pack(side="bottom", fill="x", padx=26)
        tk.Label(vi, text=info.split(" · ", 1)[1], font=F["tiny"], fg=C["overlay"], bg=C["base"]).pack(side="left")
        self.link(vi, "Copy", lambda: (self.clipboard_clear(), self.clipboard_append(info),
                                       self.status("Version info copied")), bg=C["base"], font=F["tiny"]).pack(
            side="left", padx=8)
        d.update_idletasks()
        d.geometry(f"600x{max(d.winfo_reqheight(), 480)}")
        self._about_dlg = d

    def check_updates(self, manual=True):
        if manual:
            self.status("Checking for updates…")

        def runner():
            res = check_latest_release()
            self.post(lambda: self._update_result(res, manual))
        threading.Thread(target=runner, daemon=True).start()

    def _update_result(self, res, manual):
        if res["status"] != "error":
            self.s["last_update_check"] = datetime.now().strftime("%Y-%m-%d")
            save_settings(self.s)
        cur = APP_VERSION.lstrip("v")
        if res["status"] == "newer" and (manual or res["version"] != self.s.get("skipped_version")):
            self._update_url = res["url"]
            self.update_link.configure(text=f"⬆ Update available: v{res['version']}")
            self.update_link.pack(side="right", padx=10)
        if not manual:
            return
        if res["status"] == "newer":
            self.status(f"Version {res['version']} is available")

            def skip():
                self.s["skipped_version"] = res["version"]
                save_settings(self.s)
                self.update_link.pack_forget()
            self.message("Check for updates", "A new version is available",
                         [("Your version", cur, CAT["text"]), ("New version", res["version"], CAT["green"]),
                          ("Download", res["url"], CAT["subtext"])],
                         [("Skip this version", skip, False), ("Close", None, False),
                          ("Open download page", lambda: webbrowser.open(res["url"]), True)])
        elif res["status"] == "latest":
            self.status(f"You have the latest version ({cur})")
            self.message("Check for updates", "You have the latest version",
                         [("Your version", cur, CAT["text"]), ("Latest release", res["version"], CAT["subtext"])],
                         [("OK", None, True)])
        elif res["status"] == "none":
            self.status("No release published on GitHub yet")
            self.message("Check for updates", "No release published yet",
                         [("Your version", cur, CAT["text"]),
                          ("GitHub", "No release has been published on GitHub yet.", CAT["subtext"])],
                         [("Open releases page", lambda: webbrowser.open(res["url"]), False), ("OK", None, True)])
        else:
            self.status("Could not check for updates")
            self.message("Check for updates", "Could not check for updates",
                         [("Reason", res["error"] or "no answer", CAT["yellow"]),
                          ("What to do", "Check the internet connection, or open the Releases page.", CAT["text"])],
                         [("Open releases page", lambda: webbrowser.open(APP_REPO_URL + "/releases"), False),
                          ("OK", None, True)])


# ═══════════════════════════════════════════════════════════════════════
# HELP TEXTS
# ═══════════════════════════════════════════════════════════════════════

HELP_TEXT = f"""\
{APP_TITLE}
Every NFO, XML and image a series needs for Kodi, Jellyfin, Emby and the XML
format (MediaBrowser / Media Center Master), from your own list or free data.

THE FAST WAY
------------
1. 📂 Open series…  the show folder (Season folders or videos directly in it).
   Every video is read: its episode number (S01E01, 1x01, S01E01E02, pt1/pt2…),
   its length and codec (ffprobe), and what files it already has.
2. Get the episode data — any of these, or several:
   • Import ▾ › CSV / Excel file…  your list (any column names: the app suggests
     the match and remembers it; ';' or ',' files; Excel; UTF-8 or ANSI).
   • Import ▾ › Paste from clipboard  — on TheTVDB open the series, Seasons ›
     All Seasons › Official, select the list, copy, and paste here. Specials keep
     the place where they aired.
   • Import ▾ › New CSV from a link…  — TheTVDB, TVmaze, IMDb or TMDb link (or a
     TheTVDB ID). With "read each episode page" TheTVDB also gives runtime,
     rating per country, IMDb ID, writers, directors, guest stars and the titles
     and plots in your language. The list can be saved as a CSV.
   • Fetch ▾ › TVmaze (free) fills empty fields, adds directors, writers, guest
     stars, cast, images; TMDb and OMDb with your own free keys.
3. Check the Episodes tab (⚑ = something to look at) and the Sources tab.
4. ▶ Generate all — the Changes tab lists every file first; Write selected.

TABS
----
Episodes   One row per episode: video, NFO / XML / image already there (✓),
           the sources that gave data. Double-click to edit an episode (your
           values win over every database), choose its image or pick a frame.
Sources    The selected episode, one column per database. Click a value to use
           it. Below: every conflict in the series (double-click to jump).
Artwork    Poster, fanart, banner, logo, clear art, landscape and the season
           posters. ◀ ▶ picks another image; Choose file… or drop images on the
           window (they are sorted by shape: poster, fanart, banner, logo…).
Changes    Every file that will be written, created or replaced, with a diff for
           text files. ☑/☐ in the first column. Nothing is written before
           "Write selected". Replaced files get a backup in .kfg_backup.
Check      Missing videos, gaps in the numbering, runtime that does not match
           the video, sources that disagree, invalid existing files.
Log        Everything that happened (Save log…).

WHEN DATABASES DISAGREE
-----------------------
Each value keeps its source. Settings › Sources sets, per field, which database
the app believes first (e.g. runtime: the video file first; title: your CSV
first, then TheTVDB in your language). Your own edits always win. Episodes from
different databases are matched by number, then by air date and title, so a
different numbering (DVD order, segments) does not mix them up.

IMAGES
------
Episode images: an image already in the folder, the CSV, TheTVDB, TVmaze, TMDb,
or the best frame of the video (5 points are tried; black, white and flat frames
are skipped; black bars are cut). Written as <episode>-thumb.jpg (Kodi,
Jellyfin, Emby) and metadata\\<episode>.jpg (XML format; the XML names it).
Existing images are kept unless "Replace existing images" is ticked.

SAFETY
------
Text files are checked (they must be valid XML) before they are written; every
write goes through a temporary file. Replaced files are copied to
<folder>\\.kfg_backup\\<date-time>\\ first. Undo last run restores them and moves
the files the run created into that backup folder (nothing is deleted).
Rename videos… (optional, left panel) moves the subtitles, NFO, images and
metadata files with each video, and can be undone too. By default the app
keeps your video names and writes the files under them.

SETTINGS
--------
General   Language for titles/plots, rating country, what goes into the files
          (cast, crew, ratings, technical info, online image links), replace
          NFO/XML, dark title bar, check for updates.
Sources   TMDb and OMDb keys, TVmaze people, TheTVDB episode pages, the order
          of the databases per field.
Files     Name pattern, longest title, image width, 16:9 padding, image
          sources order, CSV separator, FFmpeg, your own NFO/XML templates.

KEYBOARD
--------
Ctrl+O open series · Ctrl+I import file · Ctrl+V paste · Ctrl+L link ·
Ctrl+G generate all · F1 help.

ABOUT
-----
{APP_NAME} by {APP_AUTHORS}. Licence {APP_LICENCE}. {APP_REPO_URL.replace("https://", "")}
Data from TVmaze is licensed CC BY-SA 4.0 (tvmaze.com).
"""

FORMATS_TEXT = """\
FILES PER SERVER
----------------
Episode text     Kodi, Jellyfin, Emby: <episode>.nfo next to the video
                 XML format: metadata\\<episode>.xml (<Item>)
Episode image    Kodi, Jellyfin, Emby: <episode>-thumb.jpg next to the video
                 XML format / Jellyfin: metadata\\<episode>.jpg (named in the XML)
                 Plex: <episode>.jpg next to the video
Series text      Kodi, Jellyfin, Emby: tvshow.nfo · XML format: series.xml
Season text      Jellyfin, Emby: season.nfo in the season folder
Series art       poster.jpg + folder.jpg, fanart.jpg + backdrop.jpg (+ backdrop1..9),
                 banner.jpg, clearlogo.png (Kodi) / logo.png, clearart.png,
                 landscape.jpg / thumb.jpg
Season art       season01-poster.jpg (show folder; season-specials-poster.jpg),
                 folder.jpg in the season folder (XML / Emby), Season01.jpg (Plex)

Kodi takes the season and episode numbers from the file name, so the videos
must contain S01E01 (or 1x01). Specials are written with displayseason /
displayepisode (Kodi) and airsbefore_season / airsbefore_episode (XML) when
their position is known.
"""


# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def main():
    set_dpi_awareness()
    probe = tk.Tk()
    probe.withdraw()
    probe.title(APP_NAME)
    try:
        probe.attributes("-topmost", True)
        if os.name == "nt" and asset_path("icon.ico").exists():
            probe.iconbitmap(default=str(asset_path("icon.ico")))
    except tk.TclError:
        pass
    missing = ensure_dependencies(probe)
    probe.destroy()
    app = App()
    if missing:
        app.log("Missing packages: " + ", ".join(missing), "warn")
    app.mainloop()


if __name__ == "__main__":
    main()
