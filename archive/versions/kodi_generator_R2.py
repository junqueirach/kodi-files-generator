import csv
import os
import re
import threading
import tkinter as tk
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
# Simple tags: exact "<tag>" match.  Attribute tags: "<tag " (trailing space).
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
    # NOTE: <mpaa> and <id type="tvdb"> are intentionally NOT required here.
    # They are still processed in build_files — if present in your template
    # they will be filled; if absent the replacement is a silent no-op.
]

# =============================================================================
# ENCODING HELPERS
# =============================================================================

def read_text_auto(path):
    """Try common encodings; return (text, encoding_used)."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="ignore"), "utf-8(ignore)"


def open_csv_auto(path):
    """
    Open CSV with auto-detected encoding; return (file_handle, encoding).

    FIX WARN-5: each failed attempt now explicitly closes its handle before
    trying the next encoding, preventing file-handle leaks.
    """
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        fh = open(path, "r", encoding=enc, newline="")
        try:
            fh.read(1024)
            fh.seek(0)
            return fh, enc
        except UnicodeDecodeError:
            fh.close()          # ← close before moving to next encoding
            continue
    return open(path, "r", encoding="utf-8", errors="ignore", newline=""), "utf-8(ignore)"

# =============================================================================
# DATA HELPERS
# =============================================================================

def convert_date(date_str):
    """
    Convert DD/MM/YYYY → YYYY-MM-DD (Kodi standard).
    Also accepts YYYY-MM-DD unchanged and MM/DD/YYYY as a last resort.
    Returns (converted_string, warning_or_None).

    FIX MINOR-8: returns a warning string when the format was not recognised
    so callers can surface it in the log instead of silently writing bad data.
    """
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
    """Strip characters illegal in Windows filenames."""
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r'[\x00-\x1f]', "", text)
    return text.strip(". ")


def safe_int(value, default=0):
    """Convert value to int safely, returning default on failure."""
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default

# =============================================================================
# TEMPLATE REPLACEMENTS
# =============================================================================

def replace_simple_tag(doc, tag, value):
    """Replace content of <tag>…</tag> (first occurrence)."""
    if value is None:
        value = ""
    value = escape(str(value))
    return re.sub(
        rf"(<{tag}>)(.*?)(</{tag}>)",
        lambda m: m.group(1) + value + m.group(3),
        doc, count=1, flags=re.DOTALL,
    )


def replace_attr_tag(doc, tag, attr_pattern, value):
    """
    Replace content of <tag attr…>…</tag> where the opening tag contains
    attr_pattern anywhere in its attributes (first occurrence).

    FIX BUG-1: pattern now uses [^>]* on both sides of attr_pattern so that
    additional attributes before or after the matched one are tolerated.
    e.g. <id type="tvdb" foo="bar"> now matches, previously it would not.
    """
    if value is None:
        value = ""
    value = escape(str(value))
    return re.sub(
        rf"(<{tag}\s[^>]*{attr_pattern}[^>]*>)(.*?)(</{tag}>)",
        lambda m: m.group(1) + value + m.group(3),
        doc, count=1, flags=re.DOTALL,
    )


def apply_uniqueids(doc, tvdb_id, imdb_id):
    """
    Fill <uniqueid type="tvdb"> and <uniqueid type="imdb"> in the NFO.

    IMDB present  → fill both, keep existing default= attributes.
    IMDB absent   → ensure tvdb gets default="true", imdb gets default="false".

    FIX BUG-3: default= is now INSERTED into the tag when the template tag
    does not already carry that attribute, instead of leaving it unchanged.
    """
    tvdb_val = escape(str(tvdb_id or ""))
    imdb_val = escape(str(imdb_id or ""))

    def _set_default(tag_open, value):
        """Set or insert default="value" in an opening tag string."""
        if 'default=' in tag_open:
            return re.sub(r'default="[^"]*"', f'default="{value}"', tag_open)
        # Attribute absent → insert before closing >
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
    """Prepend a UTF-8 XML declaration if one is not already present."""
    decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if not content.lstrip().startswith("<?xml"):
        return decl + content
    return content

# =============================================================================
# VALIDATION
# =============================================================================

def validate_template(content, required_tags):
    """
    Check each (search_string, label) pair against template content.
    Returns list of (label, found: bool).
    """
    return [(label, pattern in content) for pattern, label in required_tags]


def validate_csv_columns(csv_path):
    """
    Return (found_required, missing_required, all_actual_cols).

    found_required   – REQUIRED_CSV_COLS entries present in the file.
    missing_required – REQUIRED_CSV_COLS entries absent from the file.
    all_actual_cols  – every column exactly as it appears in the CSV header
                       (shown in the log so name-mismatch errors are obvious).
    """
    fh, _ = open_csv_auto(csv_path)
    with fh:
        all_actual_cols = csv.DictReader(fh).fieldnames or []
    found   = [c for c in REQUIRED_CSV_COLS if c in all_actual_cols]
    missing = [c for c in REQUIRED_CSV_COLS if c not in all_actual_cols]
    return found, missing, all_actual_cols

# =============================================================================
# CORE FILE BUILDER  (pure function — easy to unit-test)
# =============================================================================

def build_files(row, nfo_template, xml_template, show_name):
    """
    Process one CSV row dictionary.
    Returns (nfo_text, xml_text, base_filename, warnings: list[str]).

    FIX BUG-2:    show_name sanitized before use in filename.
    FIX MINOR-8:  date warnings collected and returned to caller.
    """
    warnings = []

    season  = safe_int(row.get("Season",  0))
    episode = safe_int(row.get("Episode", 0))
    title   = (row.get("Episode_Title") or "").strip()

    # FIX BUG-2: sanitize show_name so illegal chars never reach the filename
    base_name = (f"{sanitize_filename(show_name)}"
                 f" - S{season:02d}E{episode:02d}"
                 f" - {sanitize_filename(title)}")

    runtime_min     = safe_int(row.get("Runtime_min"), 0)
    runtime_min_str = str(runtime_min) if runtime_min else ""
    runtime_sec_str = str(runtime_min * 60) if runtime_min else ""

    # FIX MINOR-8: capture any date conversion warning
    aired, date_warn = convert_date(row.get("Originally_Aired", ""))
    if date_warn:
        warnings.append(date_warn)

    tvdb_id     = (row.get("TVDB_Episode_ID") or "").strip()
    imdb_id     = (row.get("IMDB_ID")         or "").strip()
    content_rat = (row.get("Content_Rating")  or "").strip()
    description = (row.get("Description")     or "").strip()

    # ── NFO ────────────────────────────────────────────────────────────────────
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

    # ── XML ────────────────────────────────────────────────────────────────────
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
                   out_dir, show_name, log_fn, progress_fn, done_fn):
    """
    Worker function executed in a background thread.

    All callbacks are thread-safe wrappers (App._safe_*) that schedule
    actual tkinter updates on the main thread via after(0, …).

    FIX WARN-4: done_fn now receives status ∈ {"ok","warn","error"} instead
    of a bool, so the UI shows the correct colour for partial success.

    Callbacks:
        log_fn(msg, tag)           – append coloured line to log
        progress_fn(val, maxval)   – update progress bar
        done_fn(status, summary)   – status ∈ {"ok", "warn", "error"}
    """
    stats = {"ok": 0, "skip": 0, "errors": []}

    try:
        os.makedirs(out_dir, exist_ok=True)

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

                # NFO → UTF-8-sig (BOM) for maximum Kodi scraper compatibility
                with open(os.path.join(out_dir, base_name + ".nfo"), "w",
                          encoding="utf-8-sig") as fout:
                    fout.write(nfo)

                # XML → plain UTF-8 (declaration already injected)
                with open(os.path.join(out_dir, base_name + ".xml"), "w",
                          encoding="utf-8") as fout:
                    fout.write(xml)

                log_fn(f"✓  {base_name}", "ok")
                stats["ok"] += 1

            except Exception as exc:
                msg = f"✗  Row {i} ({title}): {exc}"
                log_fn(msg, "error")
                stats["errors"].append(msg)

            progress_fn(i, len(rows))

        summary = (
            f"Done — ✓ {stats['ok']} generated  "
            f"⚠ {stats['skip']} skipped  "
            f"✗ {len(stats['errors'])} errors"
        )

        # FIX WARN-4: derive colour from actual outcome, not a hardcoded True
        if stats["ok"] == 0:
            status = "error"   # nothing written → red
        elif stats["errors"] or stats["skip"]:
            status = "warn"    # partial success → yellow
        else:
            status = "ok"      # everything clean → green

        done_fn(status, summary)

    except Exception as exc:
        done_fn("error", f"Fatal error: {exc}")

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
    for widget in ("TFrame", "TLabelframe", "TLabelframe.Label"):
        style.configure(widget, background=bg)
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
    style.configure("TProgressbar",
        troughcolor=surf, background=hl, bordercolor=bdr,
    )
    root.configure(bg=bg)

# =============================================================================
# GUI APPLICATION
# =============================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kodi NFO + XML Generator")
        self.geometry("940x720")
        self.minsize(760, 580)
        apply_catppuccin(self)

        self.csv_path  = tk.StringVar()
        self.nfo_path  = tk.StringVar()
        self.xml_path  = tk.StringVar()
        self.out_dir   = tk.StringVar(value=os.path.join(os.getcwd(), "output"))
        self.show_name = tk.StringVar(value="Turma da Mônica")
        self._summary  = tk.StringVar()

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        self._build_file_rows(frm)
        self._build_buttons(frm)
        self._build_progress(frm)
        self._build_log(frm)

    # ── File-picker rows ───────────────────────────────────────────────────────
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

        r = ttk.Frame(parent); r.pack(fill="x", pady=5)
        ttk.Label(r, text="Output folder", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r, text="Browse…",
            command=lambda: self.out_dir.set(
                filedialog.askdirectory() or self.out_dir.get())
        ).pack(side="left")

        r = ttk.Frame(parent); r.pack(fill="x", pady=5)
        ttk.Label(r, text="Show name", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.show_name).pack(side="left", fill="x", expand=True, padx=6)

    # ── Buttons + summary ─────────────────────────────────────────────────────
    def _build_buttons(self, parent):
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

    # ── Progress bar ───────────────────────────────────────────────────────────
    def _build_progress(self, parent):
        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 4))

    # ── Log panel ──────────────────────────────────────────────────────────────
    def _build_log(self, parent):
        ttk.Label(parent, text="Log").pack(anchor="w")
        self.log = ScrolledText(
            parent, height=16, state="disabled",
            bg=CAT["surface0"], fg=CAT["text"],
            insertbackground=CAT["text"],
            font=("Consolas", 9), relief="flat", bd=0,
        )
        self.log.tag_config("ok",    foreground=CAT["green"])
        self.log.tag_config("warn",  foreground=CAT["yellow"])
        self.log.tag_config("error", foreground=CAT["red"])
        self.log.tag_config("info",  foreground=CAT["blue"])
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    # ── Log helpers (main-thread only) ─────────────────────────────────────────
    def write_log(self, msg, tag=""):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_summary(self, text, colour):
        self._summary.set(text)
        self.summary_lbl.configure(foreground=colour)

    # ── Thread-safe callback wrappers (FIX BUG-9) ─────────────────────────────
    # tkinter is NOT thread-safe.  Calling widget methods from a background
    # thread causes random crashes on Windows/macOS.  after(0, …) posts work
    # to the main thread's event queue — the only safe approach.

    def _safe_log(self, msg, tag=""):
        self.after(0, lambda: self.write_log(msg, tag))

    def _safe_progress(self, val, maxval):
        # Use default-arg capture to avoid late-binding closure bugs
        def _update(v=val, m=maxval):
            self.progress["maximum"] = m
            self.progress["value"]   = v
        self.after(0, _update)

    def _safe_done(self, status, summary):
        self.after(0, lambda: self._on_generation_done(status, summary))

    def _on_generation_done(self, status, summary):
        """Called on the main thread when generation finishes."""
        colour_map = {"ok": CAT["green"], "warn": CAT["yellow"], "error": CAT["red"]}
        self._set_summary(summary, colour_map.get(status, CAT["text"]))
        for btn in (self.btn_generate, self.btn_validate, self.btn_preview):
            btn.configure(state="normal")
        if status == "error":
            messagebox.showerror("Generation failed", summary)

    # ── Input guard (FIX WARN-6) ───────────────────────────────────────────────
    def _check_paths(self, need_templates=False):
        """
        Verify required paths are non-empty AND exist on disk.
        FIX WARN-6: previously only checked for non-empty strings.
        """
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
        if not self.out_dir.get():
            missing.append("Output folder")
        # Output folder need not exist yet — generate_files creates it

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

            # Always show what the CSV actually contains — essential for
            # diagnosing name-mismatch errors (wrong underscore, extra space,
            # capitalisation differences, BOM-corrupted first column, etc.)
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

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        for label, content in (("NFO", nfo), ("XML", xml)):
            frame = ttk.Frame(nb); nb.add(frame, text=label)
            t = ScrolledText(
                frame, font=("Consolas", 9),
                bg=CAT["surface0"], fg=CAT["text"],
                insertbackground=CAT["text"], relief="flat", bd=0,
            )
            t.pack(fill="both", expand=True)
            t.insert("1.0", content)
            t.configure(state="disabled")

        warn_text = ("  ⚠ " + "  ⚠ ".join(preview_warns)) if preview_warns else ""
        ttk.Label(
            win,
            text=f"Filename base:  {base_name}{warn_text}",
            font=("Segoe UI", 9, "italic"),
            background=CAT["base"],
            foreground=CAT["yellow"] if preview_warns else CAT["subtext"],
        ).pack(anchor="w", padx=12, pady=(0, 8))

    # ── Button: Generate ───────────────────────────────────────────────────────
    def run_generate(self):
        if not self._check_paths(need_templates=True):
            return
        self.clear_log()
        self._set_summary("Generating…", CAT["yellow"])

        # FIX MINOR-7: fully reset the progress bar before each run
        self.progress["maximum"] = 1
        self.progress["value"]   = 0

        for btn in (self.btn_generate, self.btn_validate, self.btn_preview):
            btn.configure(state="disabled")

        # FIX BUG-9: pass thread-safe wrappers — worker never touches widgets directly
        threading.Thread(
            target=generate_files,
            args=(
                self.csv_path.get(),
                self.nfo_path.get(),
                self.xml_path.get(),
                self.out_dir.get(),
                self.show_name.get(),
                self._safe_log,
                self._safe_progress,
                self._safe_done,
            ),
            daemon=True,
        ).start()


# =============================================================================
if __name__ == "__main__":
    App().mainloop()
