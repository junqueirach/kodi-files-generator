import csv
import os
import re
import threading
import tkinter as tk
from collections import namedtuple
from datetime import datetime
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from xml.sax.saxutils import escape

# ── Catppuccin Mocha palette ──────────────────────────────────────────────────
CAT = {
    "base":    "#1e1e2e", "mantle":   "#181825", "surface0": "#313244",
    "surface1":"#45475a", "overlay":  "#6c7086", "subtext":  "#a6adc8",
    "text":    "#cdd6f4", "green":    "#a6e3a1", "red":      "#f38ba8",
    "yellow":  "#f9e2af", "blue":     "#89b4fa", "mauve":    "#cba6f7",
    "peach":   "#fab387", "teal":     "#94e2d5",
}

# ── Required CSV columns ──────────────────────────────────────────────────────
REQUIRED_CSV_COLS = [
    "ID", "Season", "Episode", "Episode_Title", "TVDB_Episode_ID",
    "Originally_Aired", "Runtime_min", "Content_Rating", "IMDB_ID", "Description",
]

# ── Tags expected in each template  (search_string, friendly_label) ───────────
REQUIRED_NFO_TAGS = [
    ("<title>",             "title"),
    ("<season>",            "season"),
    ("<episode>",           "episode"),
    ("<aired>",             "aired"),
    ("<runtime>",           "runtime"),
    ("<durationinseconds>", "durationinseconds"),
    ("<mpaa>",              "mpaa"),
    ("<plot>",              "plot"),
    ('<uniqueid ',          "uniqueid (tvdb / imdb)"),
]
REQUIRED_XML_TAGS = [
    ("<ID>",                 "ID"),
    ("<SeasonNumber>",       "SeasonNumber"),
    ("<EpisodeNumber>",      "EpisodeNumber"),
    ("<EpisodeName>",        "EpisodeName"),
    ("<FirstAired>",         "FirstAired"),
    ("<Duration>",           "Duration"),
    ("<DurationSeconds>",    "DurationSeconds"),
    ("<VideoLength>",        "VideoLength"),
    ("<VideoLengthSeconds>", "VideoLengthSeconds"),
    ("<EpisodeID>",          "EpisodeID"),
    ("<IMDB_ID>",            "IMDB_ID"),
    ("<Overview>",           "Overview"),
    # <mpaa> and <id type="tvdb"> intentionally omitted — optional in XML template.
]

# ── Rename-checker constants ──────────────────────────────────────────────────
VIDEO_EXTS  = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"})
IMAGE_EXTS  = frozenset({".jpg", ".jpeg", ".png"})
SXXEXX_RE   = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')   # case-insensitive match

RenameAction = namedtuple("RenameAction", ["src", "dst", "kind"])

# =============================================================================
# ENCODING HELPERS
# =============================================================================

def read_text_auto(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="ignore"), "utf-8(ignore)"


def open_csv_auto(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        fh = open(path, "r", encoding=enc, newline="")
        try:
            fh.read(1024)
            fh.seek(0)
            return fh, enc
        except UnicodeDecodeError:
            fh.close()
            continue
    return open(path, "r", encoding="utf-8", errors="ignore", newline=""), "utf-8(ignore)"

# =============================================================================
# DATA HELPERS
# =============================================================================

def convert_date(date_str):
    s = (date_str or "").strip()
    if not s:
        return "", None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d"), None
        except ValueError:
            continue
    return s, f"unrecognised date format '{s}' — written as-is to Kodi"


def sanitize_filename(text):
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r'[\x00-\x1f]', "", text)
    return text.strip(". ")


def safe_int(value, default=0):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default

# =============================================================================
# TEMPLATE REPLACEMENTS
# =============================================================================

def replace_simple_tag(doc, tag, value):
    if value is None:
        value = ""
    value = escape(str(value))
    return re.sub(
        rf"(<{tag}>)(.*?)(</{tag}>)",
        lambda m: m.group(1) + value + m.group(3),
        doc, count=1, flags=re.DOTALL,
    )


def replace_attr_tag(doc, tag, attr_pattern, value):
    if value is None:
        value = ""
    value = escape(str(value))
    return re.sub(
        rf"(<{tag}\s[^>]*{attr_pattern}[^>]*>)(.*?)(</{tag}>)",
        lambda m: m.group(1) + value + m.group(3),
        doc, count=1, flags=re.DOTALL,
    )


def apply_uniqueids(doc, tvdb_id, imdb_id):
    tvdb_val = escape(str(tvdb_id or ""))
    imdb_val = escape(str(imdb_id or ""))

    def _set_default(tag_open, value):
        if 'default=' in tag_open:
            return re.sub(r'default="[^"]*"', f'default="{value}"', tag_open)
        return tag_open[:-1] + f' default="{value}">'

    if imdb_val:
        doc = re.sub(
            r'(<uniqueid\b[^>]*type="tvdb"[^>]*>)(.*?)(</uniqueid>)',
            lambda m: m.group(1) + tvdb_val + m.group(3),
            doc, count=1, flags=re.DOTALL,
        )
        doc = re.sub(
            r'(<uniqueid\b[^>]*type="imdb"[^>]*>)(.*?)(</uniqueid>)',
            lambda m: m.group(1) + imdb_val + m.group(3),
            doc, count=1, flags=re.DOTALL,
        )
    else:
        doc = re.sub(
            r'(<uniqueid\b[^>]*type="tvdb"[^>]*>)(.*?)(</uniqueid>)',
            lambda m: _set_default(m.group(1), "true") + tvdb_val + m.group(3),
            doc, count=1, flags=re.DOTALL,
        )
        doc = re.sub(
            r'(<uniqueid\b[^>]*type="imdb"[^>]*>)(.*?)(</uniqueid>)',
            lambda m: _set_default(m.group(1), "false") + m.group(3),
            doc, count=1, flags=re.DOTALL,
        )
    return doc


def ensure_xml_declaration(content):
    decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if not content.lstrip().startswith("<?xml"):
        return decl + content
    return content

# =============================================================================
# VALIDATION
# =============================================================================

def validate_template(content, required_tags):
    return [(label, pattern in content) for pattern, label in required_tags]


def validate_csv_columns(csv_path):
    fh, _ = open_csv_auto(csv_path)
    with fh:
        all_actual_cols = csv.DictReader(fh).fieldnames or []
    found   = [c for c in REQUIRED_CSV_COLS if c in all_actual_cols]
    missing = [c for c in REQUIRED_CSV_COLS if c not in all_actual_cols]
    return found, missing, all_actual_cols

# =============================================================================
# CORE FILE BUILDER
# =============================================================================

def build_files(row, nfo_template, xml_template, show_name):
    warnings = []
    season   = safe_int(row.get("Season",  0))
    episode  = safe_int(row.get("Episode", 0))
    title    = (row.get("Episode_Title") or "").strip()

    base_name = (f"{sanitize_filename(show_name)}"
                 f" - S{season:02d}E{episode:02d}"
                 f" - {sanitize_filename(title)}")

    runtime_min     = safe_int(row.get("Runtime_min"), 0)
    runtime_min_str = str(runtime_min) if runtime_min else ""
    runtime_sec_str = str(runtime_min * 60) if runtime_min else ""

    aired, date_warn = convert_date(row.get("Originally_Aired", ""))
    if date_warn:
        warnings.append(date_warn)

    tvdb_id     = (row.get("TVDB_Episode_ID") or "").strip()
    imdb_id     = (row.get("IMDB_ID")         or "").strip()
    content_rat = (row.get("Content_Rating")  or "").strip()
    description = (row.get("Description")     or "").strip()

    nfo = nfo_template
    nfo = apply_uniqueids(nfo, tvdb_id, imdb_id)
    nfo = replace_attr_tag(nfo, "id", r'type="tvdb"', tvdb_id)
    nfo = replace_simple_tag(nfo, "title",             title)
    nfo = replace_simple_tag(nfo, "season",            str(season))
    nfo = replace_simple_tag(nfo, "episode",           str(episode))
    nfo = replace_simple_tag(nfo, "aired",             aired)
    nfo = replace_simple_tag(nfo, "runtime",           runtime_min_str)
    nfo = replace_simple_tag(nfo, "durationinseconds", runtime_sec_str)
    nfo = replace_simple_tag(nfo, "mpaa",              content_rat)
    nfo = replace_simple_tag(nfo, "plot",              description)

    xml = xml_template
    xml = replace_attr_tag(xml, "id", r'type="tvdb"', tvdb_id)
    xml = replace_simple_tag(xml, "ID",                 row.get("ID", ""))
    xml = replace_simple_tag(xml, "EpisodeID",          tvdb_id)
    xml = replace_simple_tag(xml, "SeasonNumber",       str(season))
    xml = replace_simple_tag(xml, "EpisodeNumber",      str(episode))
    xml = replace_simple_tag(xml, "EpisodeName",        title)
    xml = replace_simple_tag(xml, "FirstAired",         aired)
    xml = replace_simple_tag(xml, "IMDB_ID",            imdb_id)
    xml = replace_simple_tag(xml, "Overview",           description)
    xml = replace_simple_tag(xml, "VideoLength",        runtime_min_str)
    xml = replace_simple_tag(xml, "VideoLengthSeconds", runtime_sec_str)
    xml = replace_simple_tag(xml, "Duration",           runtime_min_str)
    xml = replace_simple_tag(xml, "DurationSeconds",    runtime_sec_str)
    xml = replace_simple_tag(xml, "mpaa",               content_rat)
    xml = ensure_xml_declaration(xml)

    return nfo, xml, base_name, warnings

# =============================================================================
# GENERATION THREAD
# =============================================================================

def generate_files(csv_path, nfo_tpl_path, xml_tpl_path,
                   nfo_out_dir, xml_out_dir, show_name,
                   log_fn, progress_fn, done_fn):
    stats = {"ok": 0, "skip": 0, "errors": []}
    try:
        os.makedirs(nfo_out_dir, exist_ok=True)
        os.makedirs(xml_out_dir, exist_ok=True)
        nfo_template, nfo_enc = read_text_auto(nfo_tpl_path)
        xml_template, xml_enc = read_text_auto(xml_tpl_path)
        log_fn(f"NFO template loaded ({nfo_enc})", "info")
        log_fn(f"XML template loaded ({xml_enc})", "info")

        fh, csv_enc = open_csv_auto(csv_path)
        with fh:
            rows = list(csv.DictReader(fh))
        log_fn(f"CSV loaded ({csv_enc}) — {len(rows)} rows\n", "info")
        progress_fn(0, len(rows))

        for i, row in enumerate(rows, 1):
            title = (row.get("Episode_Title") or "").strip()
            if not title:
                log_fn(f"⚠  Row {i}: empty Episode_Title — skipped", "warn")
                stats["skip"] += 1
                progress_fn(i, len(rows))
                continue
            try:
                nfo, xml, base_name, build_warns = build_files(
                    row, nfo_template, xml_template, show_name
                )
                for w in build_warns:
                    log_fn(f"   ⚠  Row {i}: {w}", "warn")

                with open(os.path.join(nfo_out_dir, base_name + ".nfo"), "w",
                          encoding="utf-8-sig") as fout:
                    fout.write(nfo)
                with open(os.path.join(xml_out_dir, base_name + ".xml"), "w",
                          encoding="utf-8") as fout:
                    fout.write(xml)

                log_fn(f"✓  {base_name}", "ok")
                stats["ok"] += 1
            except Exception as exc:
                msg = f"✗  Row {i} ({title}): {exc}"
                log_fn(msg, "error")
                stats["errors"].append(msg)
            progress_fn(i, len(rows))

        summary = (f"Done — ✓ {stats['ok']} generated  "
                   f"⚠ {stats['skip']} skipped  "
                   f"✗ {len(stats['errors'])} errors")
        if stats["ok"] == 0:
            status = "error"
        elif stats["errors"] or stats["skip"]:
            status = "warn"
        else:
            status = "ok"
        done_fn(status, summary)
    except Exception as exc:
        done_fn("error", f"Fatal error: {exc}")

# =============================================================================
# RENAME CHECKER — LOGIC
# =============================================================================

def _parse_sxxexx(name):
    """Return (season:int, episode:int) from filename, case-insensitive, or None."""
    m = SXXEXX_RE.search(os.path.basename(name))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _files_by_sxxexx(folder, season, episode, extensions):
    """
    Return sorted list of filenames (not full paths) in *folder* whose
    SxxExx matches (season, episode) and whose extension is in *extensions*.
    """
    target = (season, episode)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(
        n for n in names
        if os.path.splitext(n)[1].lower() in extensions
        and _parse_sxxexx(n) == target
    )


def scan_for_renames(folder, log_fn, progress_fn):
    """
    Scan *folder* for NFO files and determine all required renames.

    For each .nfo:
      • The NFO base name is the source of truth.
      • Any video file whose SxxExx matches is found and renamed if needed.
      • The matching XML inside metadata/ subfolder is renamed if needed.
      • Any matching thumb image (optional) is renamed if needed.

    Returns (actions: list[RenameAction], error_count: int).
    Logging is done via log_fn(msg, tag).
    """
    actions     = []
    error_count = 0

    nfo_files = sorted(
        f for f in os.listdir(folder) if f.lower().endswith(".nfo")
    )
    if not nfo_files:
        log_fn("No NFO files found in the selected folder.", "warn")
        progress_fn(0, 1)
        return actions, 0

    progress_fn(0, len(nfo_files))
    meta_dir = os.path.join(folder, "metadata")

    for i, nfo_file in enumerate(nfo_files, 1):
        base_name = os.path.splitext(nfo_file)[0]
        se = _parse_sxxexx(base_name)

        if se is None:
            log_fn(f"⚠  {nfo_file}: no SxxExx pattern — skipped", "warn")
            progress_fn(i, len(nfo_files))
            continue

        season, episode = se
        log_fn(f"\n── {base_name}", "info")

        # ── 1. Video file ──────────────────────────────────────────────────────
        video_matches = _files_by_sxxexx(folder, season, episode, VIDEO_EXTS)

        if len(video_matches) == 0:
            log_fn(f"   ✗  No video file found for S{season:02d}E{episode:02d} "
                   f"— episode cannot be left without a video", "error")
            error_count += 1

        elif len(video_matches) > 1:
            log_fn(f"   ✗  Multiple video files for S{season:02d}E{episode:02d} "
                   f"— remove extras and re-scan:", "error")
            for v in video_matches:
                log_fn(f"      • {v}", "error")
            error_count += 1

        else:
            video_file = video_matches[0]
            ext        = os.path.splitext(video_file)[1]
            expected   = base_name + ext
            if video_file == expected:
                log_fn(f"   ✓  Video : {video_file}", "ok")
            else:
                src = os.path.join(folder, video_file)
                dst = os.path.join(folder, expected)
                actions.append(RenameAction(src, dst, "video"))
                log_fn(f"   →  Video : {video_file}", "warn")
                log_fn(f"         ↳  {expected}", "warn")

        # ── 2. XML in metadata/ ────────────────────────────────────────────────
        if not os.path.isdir(meta_dir):
            log_fn(f"   ⚠  metadata/ subfolder not found — XML check skipped", "warn")
        else:
            xml_matches = _files_by_sxxexx(meta_dir, season, episode, {".xml"})

            if len(xml_matches) == 0:
                log_fn(f"   ⚠  No XML found in metadata/ for "
                       f"S{season:02d}E{episode:02d}", "warn")

            elif len(xml_matches) > 1:
                log_fn(f"   ✗  Multiple XML files in metadata/ for "
                       f"S{season:02d}E{episode:02d} — resolve manually:", "error")
                for x in xml_matches:
                    log_fn(f"      • {x}", "error")
                error_count += 1

            else:
                xml_file = xml_matches[0]
                expected = base_name + ".xml"
                if xml_file == expected:
                    log_fn(f"   ✓  XML   : {xml_file}", "ok")
                else:
                    src = os.path.join(meta_dir, xml_file)
                    dst = os.path.join(meta_dir, expected)
                    actions.append(RenameAction(src, dst, "xml"))
                    log_fn(f"   →  XML   : {xml_file}", "warn")
                    log_fn(f"         ↳  {expected}", "warn")

        # ── 3. Thumb image (optional) ──────────────────────────────────────────
        img_matches = _files_by_sxxexx(folder, season, episode, IMAGE_EXTS)

        if len(img_matches) == 0:
            log_fn(f"   ℹ  Thumb : (none — optional)", "")

        elif len(img_matches) > 1:
            log_fn(f"   ✗  Multiple image files for S{season:02d}E{episode:02d} "
                   f"— cannot determine which is the thumb:", "error")
            for img in img_matches:
                log_fn(f"      • {img}", "error")
            error_count += 1

        else:
            img_file = img_matches[0]
            img_ext  = os.path.splitext(img_file)[1]
            expected = base_name + "-thumb" + img_ext
            if img_file == expected:
                log_fn(f"   ✓  Thumb : {img_file}", "ok")
            else:
                src = os.path.join(folder, img_file)
                dst = os.path.join(folder, expected)
                actions.append(RenameAction(src, dst, "thumb"))
                log_fn(f"   →  Thumb : {img_file}", "warn")
                log_fn(f"         ↳  {expected}", "warn")

        progress_fn(i, len(nfo_files))

    # ── Summary at the bottom of the scan log ─────────────────────────────────
    log_fn("", "")
    log_fn("── Scan summary ─────────────────────────────────────────", "info")
    if actions:
        log_fn(f"  ⚠  {len(actions)} file(s) pending rename", "warn")
    else:
        log_fn("  ✓  All files are already correctly named", "ok")
    if error_count > 0:
        log_fn(f"  ✗  {error_count} error(s) found — review log above", "error")

    return actions, error_count


def _is_case_only_rename(src, dst):
    """
    True when src and dst refer to the same filesystem entry but differ only
    in case — e.g. 'show.mkv' → 'Show.mkv'.
    os.path.normcase() lowercases on Windows (no-op on Linux/macOS case-
    sensitive FS), so this check is correct on all platforms.
    """
    src_abs = os.path.abspath(src)
    dst_abs = os.path.abspath(dst)
    # Use .lower() explicitly — os.path.normcase is a no-op on Linux/macOS
    # but we need the check to work correctly on all platforms.
    return src_abs.lower() == dst_abs.lower() and src_abs != dst_abs


def apply_rename_actions(actions, log_fn, progress_fn, done_fn):
    """
    Physically rename the files described in *actions*.
    Skips any rename whose destination already exists (to avoid overwriting).
    Reports results via done_fn(status, summary).
    """
    renamed = 0
    skipped = 0
    errors  = []

    progress_fn(0, len(actions))

    for i, action in enumerate(actions, 1):
        src_name = os.path.basename(action.src)
        dst_name = os.path.basename(action.dst)

        if _is_case_only_rename(action.src, action.dst):
            # Case-only rename (e.g. lowercase → Title Case).
            # On case-insensitive filesystems (Windows, macOS) os.path.exists()
            # returns True for both names, so a direct rename would be blocked
            # by the collision guard below.  Two-step via a temp name solves it.
            try:
                tmp_path = action.src + ".__kodi_tmp__"
                os.rename(action.src, tmp_path)
                os.rename(tmp_path, action.dst)
                log_fn(f"✓  [{action.kind}] {src_name}  →  {dst_name}", "ok")
                renamed += 1
            except OSError as exc:
                msg = f"✗  [{action.kind}] {src_name}: {exc}"
                log_fn(msg, "error")
                errors.append(msg)
                skipped += 1
        elif os.path.exists(action.dst):
            msg = (f"✗  [{action.kind}] Cannot rename — destination already exists:\n"
                   f"   {dst_name}")
            log_fn(msg, "error")
            errors.append(msg)
            skipped += 1
        else:
            try:
                os.rename(action.src, action.dst)
                log_fn(f"✓  [{action.kind}] {src_name}  →  {dst_name}", "ok")
                renamed += 1
            except OSError as exc:
                msg = f"✗  [{action.kind}] {src_name}: {exc}"
                log_fn(msg, "error")
                errors.append(msg)
                skipped += 1

        progress_fn(i, len(actions))

    summary = (f"Rename complete — ✓ {renamed} renamed  "
               f"✗ {skipped} skipped")
    if skipped == 0:
        status = "ok"
    elif renamed == 0:
        status = "error"
    else:
        status = "warn"
    done_fn(status, summary)

# =============================================================================
# CATPPUCCIN STYLE
# =============================================================================

def apply_catppuccin(root):
    style = ttk.Style(root)
    style.theme_use("clam")

    bg, surf, text = CAT["base"], CAT["surface0"], CAT["text"]
    sub, hl, bdr   = CAT["subtext"], CAT["mauve"], CAT["surface1"]

    style.configure(".",
        background=bg, foreground=text,
        fieldbackground=surf, selectbackground=hl,
        selectforeground=bg, bordercolor=bdr,
        darkcolor=surf, lightcolor=surf,
        troughcolor=surf, arrowcolor=sub,
        font=("Segoe UI", 10),
    )
    for w in ("TFrame", "TLabelframe", "TLabelframe.Label"):
        style.configure(w, background=bg)
    style.configure("TLabel",  background=bg,   foreground=text)
    style.configure("TEntry",  fieldbackground=surf, foreground=text)
    style.configure("TButton",
        background=surf, foreground=text, bordercolor=bdr,
        focuscolor=hl, padding=6,
    )
    style.map("TButton",
        background=[("active", CAT["surface1"]), ("pressed", CAT["overlay"])],
        foreground=[("disabled", sub)],
    )
    style.configure("Accent.TButton",
        background=hl, foreground=bg,
        font=("Segoe UI", 10, "bold"), padding=6,
    )
    style.map("Accent.TButton",
        background=[("active", CAT["blue"]), ("pressed", CAT["overlay"])],
    )
    style.configure("TProgressbar", troughcolor=surf, background=hl, bordercolor=bdr)
    style.configure("TNotebook",    background=bg,    bordercolor=bdr, tabmargins=[2,4,0,0])
    style.configure("TNotebook.Tab",
        background=surf, foreground=sub,
        padding=(14, 5), focuscolor=bg,
    )
    style.map("TNotebook.Tab",
        background=[("selected", bg),   ("active", CAT["surface1"])],
        foreground=[("selected", text)],
    )
    root.configure(bg=bg)

# =============================================================================
# GUI APPLICATION
# =============================================================================

def _make_log(parent):
    """Create and return a themed ScrolledText log widget."""
    t = ScrolledText(
        parent, height=16, state="disabled",
        bg=CAT["surface0"], fg=CAT["text"],
        insertbackground=CAT["text"],
        font=("Consolas", 9), relief="flat", bd=0,
    )
    t.tag_config("ok",    foreground=CAT["green"])
    t.tag_config("warn",  foreground=CAT["yellow"])
    t.tag_config("error", foreground=CAT["red"])
    t.tag_config("info",  foreground=CAT["blue"])
    return t


def _log_write(widget, msg, tag=""):
    widget.configure(state="normal")
    widget.insert("end", msg + "\n", tag)
    widget.see("end")
    widget.configure(state="disabled")


def _log_clear(widget):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.configure(state="disabled")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kodi NFO + XML Generator")
        self.geometry("960x740")
        self.minsize(780, 600)
        apply_catppuccin(self)

        # ── Generator state ────────────────────────────────────────────────────
        self.csv_path  = tk.StringVar()
        self.nfo_path  = tk.StringVar()
        self.xml_path  = tk.StringVar()
        self.nfo_out_dir = tk.StringVar(value=os.path.join(os.getcwd(), "output"))
        self.xml_out_dir = tk.StringVar(
            value=os.path.join(os.getcwd(), "output", "metadata"))
        self.show_name   = tk.StringVar(value="Turma da Mônica")
        self._summary  = tk.StringVar()

        # ── Rename-checker state ───────────────────────────────────────────────
        self.rename_folder   = tk.StringVar()
        self._rename_summary = tk.StringVar()
        self._rename_actions = []          # filled by scan, consumed by apply

        # ── Notebook (two tabs) ────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        gen_tab    = ttk.Frame(nb, padding=14)
        rename_tab = ttk.Frame(nb, padding=14)
        nb.add(gen_tab,    text="  NFO / XML Generator  ")
        nb.add(rename_tab, text="  Rename Checker  ")

        self._build_generator_tab(gen_tab)
        self._build_rename_tab(rename_tab)

    # =========================================================================
    # GENERATOR TAB
    # =========================================================================

    def _build_generator_tab(self, parent):
        self._build_file_rows(parent)
        self._build_gen_buttons(parent)
        self._build_gen_progress(parent)
        self._build_gen_log(parent)

    def _build_file_rows(self, parent):
        def file_row(label, var, filetypes):
            r = ttk.Frame(parent); r.pack(fill="x", pady=5)
            ttk.Label(r, text=label, width=18).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(r, text="Browse…",
                command=lambda: var.set(
                    filedialog.askopenfilename(filetypes=filetypes) or var.get())
            ).pack(side="left")

        file_row("CSV file",     self.csv_path,
                 [("CSV files", "*.csv")])
        file_row("NFO template", self.nfo_path,
                 [("NFO files", "*.nfo"), ("Text files", "*.txt"), ("All", "*.*")])
        file_row("XML template", self.xml_path,
                 [("XML files", "*.xml"), ("All", "*.*")])

        # NFO output folder
        r = ttk.Frame(parent); r.pack(fill="x", pady=5)
        ttk.Label(r, text="NFO output folder", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.nfo_out_dir).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(r, text="Browse…",
            command=self._browse_nfo_out_dir).pack(side="left")

        # XML output folder
        r = ttk.Frame(parent); r.pack(fill="x", pady=5)
        ttk.Label(r, text="XML output folder", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.xml_out_dir).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(r, text="Browse…",
            command=lambda: self.xml_out_dir.set(
                filedialog.askdirectory() or self.xml_out_dir.get())
        ).pack(side="left")

        r = ttk.Frame(parent); r.pack(fill="x", pady=5)
        ttk.Label(r, text="Show name", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.show_name).pack(
            side="left", fill="x", expand=True, padx=6)

    def _build_gen_buttons(self, parent):
        row = ttk.Frame(parent); row.pack(fill="x", pady=(10, 2))
        self.btn_validate = ttk.Button(row, text="① Validate inputs",
                                       command=self.run_validate)
        self.btn_preview  = ttk.Button(row, text="② Preview first row",
                                       command=self.run_preview)
        self.btn_generate = ttk.Button(row, text="③ Generate files",
                                       command=self.run_generate,
                                       style="Accent.TButton")
        self.btn_validate.pack(side="left", padx=(0, 6))
        self.btn_preview .pack(side="left", padx=(0, 6))
        self.btn_generate.pack(side="left")

        self.summary_lbl = ttk.Label(parent, textvariable=self._summary,
                                     font=("Segoe UI", 10, "bold"))
        self.summary_lbl.pack(anchor="w", pady=(4, 0))

    def _build_gen_progress(self, parent):
        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 4))

    def _build_gen_log(self, parent):
        ttk.Label(parent, text="Log").pack(anchor="w")
        self.log = _make_log(parent)
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    # ── Generator log helpers ──────────────────────────────────────────────────
    def write_log(self, msg, tag=""):
        _log_write(self.log, msg, tag)

    def clear_log(self):
        _log_clear(self.log)

    def _set_summary(self, text, colour):
        self._summary.set(text)
        self.summary_lbl.configure(foreground=colour)

    # ── Thread-safe generator callbacks ───────────────────────────────────────
    def _safe_log(self, msg, tag=""):
        self.after(0, lambda: self.write_log(msg, tag))

    def _safe_progress(self, val, maxval):
        def _update(v=val, m=maxval):
            self.progress["maximum"] = m
            self.progress["value"]   = v
        self.after(0, _update)

    def _safe_done(self, status, summary):
        self.after(0, lambda: self._on_generation_done(status, summary))

    def _on_generation_done(self, status, summary):
        colour_map = {"ok": CAT["green"], "warn": CAT["yellow"], "error": CAT["red"]}
        self._set_summary(summary, colour_map.get(status, CAT["text"]))
        for btn in (self.btn_generate, self.btn_validate, self.btn_preview):
            btn.configure(state="normal")
        if status == "error":
            messagebox.showerror("Generation failed", summary)

    def _browse_nfo_out_dir(self):
        """Browse for NFO output folder; auto-suggest metadata/ for XML if appropriate."""
        path = filedialog.askdirectory()
        if not path:
            return
        self.nfo_out_dir.set(path)
        # Auto-suggest metadata/ subfolder for XML when:
        #   - XML field is blank, OR
        #   - XML field currently ends with /metadata or \\metadata (was auto-set before)
        xml_val = self.xml_out_dir.get()
        if not xml_val or xml_val.rstrip("/\\").endswith("metadata"):
            self.xml_out_dir.set(os.path.join(path, "metadata"))

    # ── Input guard ────────────────────────────────────────────────────────────
    def _check_paths(self, need_templates=False):
        missing, invalid = [], []

        def _req_file(label, path):
            if not path:
                missing.append(label)
            elif not os.path.isfile(path):
                invalid.append(f"{label}:\n  {path}")

        _req_file("CSV file", self.csv_path.get())
        if need_templates:
            _req_file("NFO template", self.nfo_path.get())
            _req_file("XML template", self.xml_path.get())
        if not self.nfo_out_dir.get():
            missing.append("NFO output folder")
        if not self.xml_out_dir.get():
            missing.append("XML output folder")

        if missing:
            messagebox.showwarning("Missing inputs",
                                   "Please select:\n• " + "\n• ".join(missing))
            return False
        if invalid:
            messagebox.showerror("File not found",
                                 "These files were not found on disk:\n\n"
                                 + "\n\n".join(invalid))
            return False
        return True

    # ── Button: Validate ───────────────────────────────────────────────────────
    def run_validate(self):
        if not self._check_paths(need_templates=True):
            return
        self.clear_log()
        self._set_summary("Validating…", CAT["yellow"])
        all_ok = True

        try:
            _, missing, actual_cols = validate_csv_columns(self.csv_path.get())
            self.write_log("CSV — columns detected in file:", "info")
            if actual_cols:
                for col in actual_cols:
                    self.write_log(f"   • {col}", "")
            else:
                self.write_log("   (none detected — is this a valid CSV?)", "warn")

            if missing:
                self.write_log(
                    "\nCSV — required columns NOT found "
                    "(compare names above with the list below):", "warn")
                for col in missing:
                    self.write_log(f"   ✗  {col}", "error")
                all_ok = False
            else:
                self.write_log("\nCSV — all required columns matched ✓", "ok")
        except Exception as exc:
            self.write_log(f"CSV error: {exc}", "error")
            all_ok = False

        for tpl_label, tpl_path, req_tags in (
            ("NFO template", self.nfo_path.get(), REQUIRED_NFO_TAGS),
            ("XML template", self.xml_path.get(), REQUIRED_XML_TAGS),
        ):
            try:
                content, enc = read_text_auto(tpl_path)
                self.write_log(f"\n{tpl_label}  ({enc}):", "info")
                for label, found in validate_template(content, req_tags):
                    icon = "✓" if found else "✗"
                    self.write_log(f"   {icon}  {label}", "ok" if found else "error")
                    if not found:
                        all_ok = False
            except Exception as exc:
                self.write_log(f"{tpl_label} error: {exc}", "error")
                all_ok = False

        if all_ok:
            self._set_summary("✓  All inputs validated — ready to generate.", CAT["green"])
        else:
            self._set_summary("⚠  Validation found issues — review the log.", CAT["red"])

    # ── Button: Preview ────────────────────────────────────────────────────────
    def run_preview(self):
        if not self._check_paths(need_templates=True):
            return
        try:
            nfo_tpl, _ = read_text_auto(self.nfo_path.get())
            xml_tpl, _ = read_text_auto(self.xml_path.get())
            fh, _      = open_csv_auto(self.csv_path.get())
            with fh:
                rows = list(csv.DictReader(fh))
            if not rows:
                messagebox.showwarning("Preview", "CSV has no data rows.")
                return
            nfo, xml, base_name, preview_warns = build_files(
                rows[0], nfo_tpl, xml_tpl, self.show_name.get()
            )
        except Exception as exc:
            messagebox.showerror("Preview error", str(exc))
            return

        win = tk.Toplevel(self)
        win.title(f"Preview — {base_name}")
        win.geometry("860x660")
        win.configure(bg=CAT["base"])

        pnb = ttk.Notebook(win)
        pnb.pack(fill="both", expand=True, padx=10, pady=10)
        for label, content in (("NFO", nfo), ("XML", xml)):
            frame = ttk.Frame(pnb); pnb.add(frame, text=label)
            t = ScrolledText(frame, font=("Consolas", 9),
                             bg=CAT["surface0"], fg=CAT["text"],
                             insertbackground=CAT["text"], relief="flat", bd=0)
            t.pack(fill="both", expand=True)
            t.insert("1.0", content)
            t.configure(state="disabled")

        warn_text = ("  ⚠ " + "  ⚠ ".join(preview_warns)) if preview_warns else ""
        ttk.Label(win, text=f"Filename base:  {base_name}{warn_text}",
                  font=("Segoe UI", 9, "italic"), background=CAT["base"],
                  foreground=CAT["yellow"] if preview_warns else CAT["subtext"],
                  ).pack(anchor="w", padx=12, pady=(0, 8))

    # ── Button: Generate ───────────────────────────────────────────────────────
    def run_generate(self):
        if not self._check_paths(need_templates=True):
            return
        self.clear_log()
        self._set_summary("Generating…", CAT["yellow"])
        self.progress["maximum"] = 1
        self.progress["value"]   = 0
        for btn in (self.btn_generate, self.btn_validate, self.btn_preview):
            btn.configure(state="disabled")

        threading.Thread(
            target=generate_files,
            args=(self.csv_path.get(), self.nfo_path.get(), self.xml_path.get(),
                  self.nfo_out_dir.get(), self.xml_out_dir.get(),
                  self.show_name.get(),
                  self._safe_log, self._safe_progress, self._safe_done),
            daemon=True,
        ).start()

    # =========================================================================
    # RENAME CHECKER TAB
    # =========================================================================

    def _build_rename_tab(self, parent):
        # ── Folder picker ──────────────────────────────────────────────────────
        r = ttk.Frame(parent); r.pack(fill="x", pady=5)
        ttk.Label(r, text="Episodes folder", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.rename_folder).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(r, text="Browse…",
            command=lambda: self.rename_folder.set(
                filedialog.askdirectory() or self.rename_folder.get())
        ).pack(side="left")

        # ── Info label ────────────────────────────────────────────────────────
        info = ("Select the folder that contains the .nfo and video files.  "
                "The .xml files must be inside a  metadata/  subfolder.  "
                "Thumb images are optional.")
        ttk.Label(parent, text=info, foreground=CAT["subtext"],
                  font=("Segoe UI", 9, "italic"),
                  wraplength=820, justify="left",
                  ).pack(anchor="w", pady=(0, 8))

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = ttk.Frame(parent); btn_row.pack(fill="x", pady=(4, 2))
        self.btn_scan  = ttk.Button(btn_row, text="① Scan folder",
                                    command=self.run_scan)
        self.btn_apply = ttk.Button(btn_row, text="② Apply renames",
                                    command=self.run_apply_renames,
                                    style="Accent.TButton",
                                    state="disabled")
        self.btn_scan .pack(side="left", padx=(0, 6))
        self.btn_apply.pack(side="left")

        self.rename_summary_lbl = ttk.Label(
            parent, textvariable=self._rename_summary,
            font=("Segoe UI", 10, "bold"))
        self.rename_summary_lbl.pack(anchor="w", pady=(4, 0))

        # ── Progress ──────────────────────────────────────────────────────────
        self.rename_progress = ttk.Progressbar(parent, mode="determinate")
        self.rename_progress.pack(fill="x", pady=(6, 4))

        # ── Log ───────────────────────────────────────────────────────────────
        ttk.Label(parent, text="Log").pack(anchor="w")
        self.rename_log = _make_log(parent)
        self.rename_log.pack(fill="both", expand=True, pady=(4, 0))

    # ── Rename log helpers ─────────────────────────────────────────────────────
    def _rlog(self, msg, tag=""):
        _log_write(self.rename_log, msg, tag)

    def _rclear(self):
        _log_clear(self.rename_log)

    def _rset_summary(self, text, colour):
        self._rename_summary.set(text)
        self.rename_summary_lbl.configure(foreground=colour)

    # ── Thread-safe rename callbacks ───────────────────────────────────────────
    def _rsafe_log(self, msg, tag=""):
        self.after(0, lambda: self._rlog(msg, tag))

    def _rsafe_progress(self, val, maxval):
        def _update(v=val, m=maxval):
            self.rename_progress["maximum"] = m
            self.rename_progress["value"]   = v
        self.after(0, _update)

    def _rsafe_scan_done(self, actions, error_count, nfo_count):
        """Called on the main thread after scan completes."""
        self._rename_actions = actions
        self.btn_scan.configure(state="normal")

        pending = len(actions)
        colour_map = {"ok": CAT["green"], "warn": CAT["yellow"], "error": CAT["red"]}

        if error_count > 0 and pending == 0:
            status = "error"
        elif error_count > 0 or pending > 0:
            status = "warn"
        else:
            status = "ok"

        summary = (f"Scan complete — {nfo_count} NFO(s) checked  "
                   f"| {pending} rename(s) pending  "
                   f"| {error_count} error(s)")
        self._rset_summary(summary, colour_map[status])

        if pending > 0:
            self.btn_apply.configure(state="normal")
            self._rlog("  Click ② Apply renames to proceed.", "warn")
        else:
            self.btn_apply.configure(state="disabled")

    def _rsafe_apply_done(self, status, summary):
        self.after(0, lambda: self._on_apply_done(status, summary))

    def _on_apply_done(self, status, summary):
        colour_map = {"ok": CAT["green"], "warn": CAT["yellow"], "error": CAT["red"]}
        self._rset_summary(summary, colour_map.get(status, CAT["text"]))
        self.btn_scan .configure(state="normal")
        self.btn_apply.configure(state="disabled")
        self._rename_actions = []

    # ── Button: Scan ───────────────────────────────────────────────────────────
    def run_scan(self):
        folder = self.rename_folder.get().strip()
        if not folder:
            messagebox.showwarning("No folder selected",
                                   "Please select the episodes folder first.")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Folder not found",
                                 f"Folder not found on disk:\n{folder}")
            return

        self._rclear()
        self._rename_actions = []
        self.btn_apply.configure(state="disabled")
        self.btn_scan .configure(state="disabled")
        self.rename_progress["maximum"] = 1
        self.rename_progress["value"]   = 0
        self._rset_summary("Scanning…", CAT["yellow"])

        def _worker():
            try:
                nfo_count = len([
                    f for f in os.listdir(folder) if f.lower().endswith(".nfo")
                ])
                actions, error_count = scan_for_renames(
                    folder, self._rsafe_log, self._rsafe_progress
                )
                self.after(0, lambda: self._rsafe_scan_done(
                    actions, error_count, nfo_count))
            except Exception as exc:
                self._rsafe_log(f"Fatal scan error: {exc}", "error")
                self.after(0, lambda: self._rset_summary(
                    f"Fatal error: {exc}", CAT["red"]))
                self.after(0, lambda: self.btn_scan.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Button: Apply renames ─────────────────────────────────────────────────
    def run_apply_renames(self):
        if not self._rename_actions:
            messagebox.showinfo("Nothing to rename",
                                "Run Scan first to identify files that need renaming.")
            return

        n = len(self._rename_actions)
        if not messagebox.askyesno(
            "Confirm renames",
            f"This will rename {n} file(s).\n\n"
            "Files are renamed in-place — this cannot be undone automatically.\n\n"
            "Proceed?",
        ):
            return

        self.btn_apply.configure(state="disabled")
        self.btn_scan .configure(state="disabled")
        self.rename_progress["maximum"] = 1
        self.rename_progress["value"]   = 0
        self._rlog("\n── Applying renames ──", "info")

        actions = list(self._rename_actions)   # snapshot before thread starts

        threading.Thread(
            target=apply_rename_actions,
            args=(actions, self._rsafe_log, self._rsafe_progress,
                  self._rsafe_apply_done),
            daemon=True,
        ).start()


# =============================================================================
if __name__ == "__main__":
    App().mainloop()
