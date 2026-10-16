"""Kodi Files Generator — core (no Tk).

Data model with per-source values, table import (CSV / TSV / XLSX / clipboard), column mapping, numbers, dates,
the TheTVDB "All seasons" paste reader, episode references in file names, names and episode <-> video matching.
"""
import csv
import difflib
import io
import os
import re
import unicodedata
from datetime import date, datetime, timedelta

VIDEO_EXTS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".mov", ".wmv", ".mpg", ".mpeg", ".webm",
                        ".flv", ".divx", ".ogm", ".vob", ".iso"})
SUB_EXTS = frozenset({".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi", ".ttml"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tbn", ".webp"})
SIDECAR_EXTS = SUB_EXTS | frozenset({".nfo", ".edl", ".bif", ".txt"})

# ═══════════════════════════════════════════════════════════════════════
# SOURCES AND THE RECORD MODEL (every value remembers where it came from)
# ═══════════════════════════════════════════════════════════════════════

SOURCES = ["user", "csv", "paste", "video", "tvdb", "tmdb", "tvmaze", "omdb", "file"]
SOURCE_LABELS = {"user": "You", "csv": "CSV", "paste": "Paste", "video": "Video file", "tvdb": "TheTVDB",
                 "tmdb": "TMDb", "tvmaze": "TVmaze", "omdb": "OMDb (IMDb)", "file": "Existing files"}

# F17 — which source the app believes first, per field ("user" always wins and is not listed)
DEFAULT_RULES = {
    "title":     ["csv", "paste", "tvdb", "tmdb", "tvmaze", "omdb", "file"],
    "plot":      ["csv", "paste", "tvdb", "tmdb", "tvmaze", "omdb", "file"],
    "aired":     ["csv", "paste", "tvdb", "tvmaze", "tmdb", "omdb", "file"],
    "runtime":   ["video", "csv", "tvdb", "tvmaze", "tmdb", "omdb", "file"],
    "tvdb_id":   ["csv", "paste", "tvdb", "tvmaze", "tmdb", "file"],
    "imdb_id":   ["csv", "omdb", "tvdb", "tmdb", "tvmaze", "file"],
    "mpaa":      ["csv", "tvdb", "tmdb", "omdb", "file"],
    "directors": ["tvdb", "tvmaze", "tmdb", "omdb", "csv", "file"],
    "writers":   ["tvdb", "tvmaze", "tmdb", "omdb", "csv", "file"],
    "guests":    ["tvdb", "tvmaze", "tmdb", "csv", "file"],
    "actors":    ["tvdb", "tvmaze", "tmdb", "file"],
    "network":   ["csv", "paste", "tvdb", "tvmaze", "tmdb", "file"],
    "genres":    ["tvdb", "tvmaze", "tmdb", "omdb", "file"],
    "image":     ["csv", "tvdb", "tvmaze", "tmdb"],
}
FALLBACK_ORDER = ["csv", "paste", "video", "tvdb", "tmdb", "tvmaze", "omdb", "file"]


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {} or v == ()


class Record:
    """A set of fields; each field may have one value per source. user > rules order > anything."""

    def __init__(self):
        self.values = {}      # field -> {source: value}
        self.user = {}        # field -> value typed by the user (always wins)
        self.rules = None     # optional per-record rule override {field: [sources]}

    def set(self, field, value, source):
        if isinstance(value, str):
            value = clean_text(value)
        if _empty(value):
            return
        self.values.setdefault(field, {})[source] = value

    def set_user(self, field, value):
        if _empty(value):
            self.user.pop(field, None)
        else:
            self.user[field] = value

    def order(self, field, rules=None):
        r = (self.rules or {}).get(field) or (rules or {}).get(field) or DEFAULT_RULES.get(field) or FALLBACK_ORDER
        return list(r) + [s for s in FALLBACK_ORDER if s not in r]

    def source(self, field, rules=None):
        if field in self.user:
            return "user"
        vals = self.values.get(field) or {}
        for src in self.order(field, rules):
            if src in vals:
                return src
        return next(iter(vals), None)

    def get(self, field, rules=None, default=None):
        src = self.source(field, rules)
        if src is None:
            return default
        return self.user[field] if src == "user" and field in self.user else self.values[field][src]

    def all(self, field) -> dict:
        out = dict(self.values.get(field) or {})
        if field in self.user:
            out["user"] = self.user[field]
        return out

    def conflicts(self, field) -> bool:
        vals = [v for s, v in self.all(field).items()]
        norm = {_norm_value(v) for v in vals}
        return len(norm) > 1

    def merge(self, other: "Record"):
        for f, by_src in other.values.items():
            for s, v in by_src.items():
                self.set(f, v, s)


def _norm_value(v):
    if isinstance(v, (list, tuple)):
        return tuple(sorted(_norm_value(x) for x in v))
    if isinstance(v, str):
        return re.sub(r"\W+", "", strip_accents(v).lower())
    return v


class Series(Record):
    def __init__(self):
        super().__init__()
        self.artwork = {}     # kind -> [(url_or_path, source)]; kinds: poster fanart banner clearlogo clearart landscape
        self.chosen_art = {}  # kind -> url_or_path


class Season(Record):
    def __init__(self, number):
        super().__init__()
        self.number = number
        self.posters = []     # [(url_or_path, source)]


class Episode(Record):
    def __init__(self, season, episode):
        super().__init__()
        self.season = int(season)
        self.episode = int(episode)
        self.images = []      # [(url, source)] episode images from the databases / CSV
        self.ratings = {}     # source -> (value, votes)
        self.display = None   # specials: (season, episode) it airs before, or (season, None) after the season
        self.absolute = None

    @property
    def key(self):
        return (self.season, self.episode)

    @property
    def code(self):
        return f"S{self.season:02d}E{self.episode:02d}"

    def merge(self, other: "Episode"):
        super().merge(other)
        for img in other.images:
            if img not in self.images:
                self.images.append(img)
        self.ratings.update(other.ratings)
        self.display = other.display or self.display
        self.absolute = other.absolute or self.absolute


# ═══════════════════════════════════════════════════════════════════════
# TEXT, NUMBERS AND DATES
# ═══════════════════════════════════════════════════════════════════════

_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def clean_text(s) -> str:
    """Remove characters XML 1.0 does not allow (B12), normalise line ends and non-breaking spaces."""
    if s is None:
        return ""
    s = str(s).replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    return _XML_BAD.sub("", s).strip()


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def decode_bytes(raw: bytes) -> tuple:
    """(text, encoding). Whole-file decision (B1): BOM → UTF-16 without BOM → strict UTF-8 → cp1252 (B2) → latin-1."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                     (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(enc), enc
    head = raw[:4096]
    if len(head) >= 8:
        if head[1::2].count(0) > len(head) // 4:
            try:
                return raw.decode("utf-16-le"), "utf-16-le"
            except UnicodeDecodeError:
                pass
        if head[0::2].count(0) > len(head) // 4:
            try:
                return raw.decode("utf-16-be"), "utf-16-be"
            except UnicodeDecodeError:
                pass
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_int(value):
    """First number in the text as an int (B4): '1.0' → 1, '46 min' → 46, '' → None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    try:
        return int(round(float(m.group().replace(",", "."))))
    except ValueError:
        return None


MONTHS = {}
for _i, _names in enumerate([
        "january jan janeiro enero janvier januar gennaio", "february feb fevereiro fev febrero février fevrier februar febbraio",
        "march mar março marco marzo mars märz marz", "april apr abril avril aprile",
        "may maio mayo mai maggio", "june jun junho junio juin juni giugno",
        "july jul julho julio juillet juli luglio", "august aug agosto août aout",
        "september sep sept setembro set septiembre septembre settembre",
        "october oct outubro out octubre octobre oktober ottobre",
        "november nov novembro noviembre novembre", "december dec dezembro dez diciembre décembre decembre dezember dicembre"],
        start=1):
    for _n in _names.split():
        MONTHS[strip_accents(_n)] = _i

_ISO_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?:\s.*)?$")
_MDY_WORD_RE = re.compile(r"^([A-Za-zÀ-ÿ]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")
_DMY_WORD_RE = re.compile(r"^(\d{1,2})(?:º|o|st|nd|rd|th)?\.?\s+(?:de\s+)?([A-Za-zÀ-ÿ]+)\.?,?\s+(?:de\s+)?(\d{4})$", re.I)


def _year(y: int) -> int:
    return y if y >= 100 else (2000 + y if y < 50 else 1900 + y)


def _iso(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(value, order: str = "auto") -> tuple:
    """(ISO date or the original text, warning or None). order: auto | dmy | mdy (B5, B6)."""
    if value is None:
        return "", None
    if isinstance(value, datetime):
        return value.date().isoformat(), None
    if isinstance(value, date):
        return value.isoformat(), None
    s = str(value).strip()
    if not s:
        return "", None
    m = _ISO_RE.match(s)
    if m:
        iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso, None
    if re.fullmatch(r"\d{5}(?:\.\d+)?", s) and 20000 < float(s) < 80000:           # Excel serial date
        return (date(1899, 12, 30) + timedelta(days=int(float(s)))).isoformat(), None
    plain = strip_accents(s).lower()
    m = _MDY_WORD_RE.match(plain)
    if m and m.group(1) in MONTHS:
        iso = _iso(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
        if iso:
            return iso, None
    m = _DMY_WORD_RE.match(plain)
    if m and m.group(2) in MONTHS:
        iso = _iso(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
        if iso:
            return iso, None
    m = _NUMERIC_RE.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(int(m.group(3)))
        if order == "dmy" or (order == "auto" and a > 12):
            iso, warn = _iso(y, b, a), None
        elif order == "mdy" or (order == "auto" and b > 12):
            iso, warn = _iso(y, a, b), None
        else:
            iso, warn = _iso(y, b, a), f"ambiguous date '{s}' read as day/month"
        if iso:
            return iso, warn
    return s, f"unrecognised date '{s}' — written as it is"


def detect_date_order(values) -> str:
    """'dmy' or 'mdy' when the numeric dates of a column decide it (a first part > 12 or a second part > 12)."""
    dmy = mdy = 0
    for v in values:
        m = _NUMERIC_RE.match(str(v or "").strip())
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            dmy += 1
        elif b > 12 >= a:
            mdy += 1
    if dmy and not mdy:
        return "dmy"
    if mdy and not dmy:
        return "mdy"
    return ""


# ═══════════════════════════════════════════════════════════════════════
# TABLES: CSV / TSV / XLSX / CLIPBOARD
# ═══════════════════════════════════════════════════════════════════════

class Table:
    def __init__(self, rows, line_numbers=None, encoding="", delimiter="", source=""):
        self.rows = rows                                   # list[list[str]] including the header row
        self.line_numbers = line_numbers or list(range(1, len(rows) + 1))
        self.encoding, self.delimiter, self.source = encoding, delimiter, source
        self.header_index = find_header_row(rows)
        self.headers = [h.strip().lstrip("﻿") for h in rows[self.header_index]] if rows else []

    @property
    def data(self):
        """[(line number, cells)] after the header."""
        return [(self.line_numbers[i], r) for i, r in enumerate(self.rows) if i > self.header_index]


def sniff_delimiter(text: str) -> str:
    lines = [l for l in text.splitlines() if l.strip()][:20]
    best, best_score = ",", -1
    for d in ("\t", ";", ",", "|"):
        counts = [l.count(d) for l in lines]
        if not counts or max(counts) == 0:
            continue
        common = max(set(counts), key=counts.count)
        score = sum(1 for c in counts if c == common and c > 0) * 10 + common
        if score > best_score:
            best, best_score = d, score
    return best


def read_table_text(text: str, source="clipboard", delimiter=None) -> Table:
    d = delimiter or sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=d)
    rows, lines = [], []
    for row in reader:
        rows.append([c.strip() for c in row])
        lines.append(reader.line_num)
    return Table(rows, lines, "", d, source)


def read_table_file(path: str, delimiter=None) -> Table:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    with open(path, "rb") as f:
        raw = f.read()
    text, enc = decode_bytes(raw)
    t = read_table_text(text, path, delimiter)
    t.encoding = enc
    return t


def read_xlsx(path: str, sheet=None) -> Table:
    import openpyxl                           # optional dependency (installed on demand by the GUI)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        cells = []
        for v in r:
            if v is None:
                cells.append("")
            elif isinstance(v, datetime):
                cells.append(v.date().isoformat())
            elif isinstance(v, float) and v.is_integer():
                cells.append(str(int(v)))
            else:
                cells.append(str(v).strip())
        rows.append(cells)
    wb.close()
    return Table(rows, None, "xlsx", "", path)


# The fields the app knows, with the column names seen in real files (F1.3)
FIELDS = [
    ("season",    "Season",            True,  ["season", "temporada", "s", "season number", "seasonnumber", "saison", "staffel", "seas"]),
    ("episode",   "Episode",           True,  ["episode", "episodio", "ep", "e", "number", "episodenumber", "episode number",
                                               "no", "nr", "num", "ep no", "episodio n"]),
    ("title",     "Title",             True,  ["episode title", "episode_title", "title", "name", "episode name", "titulo",
                                               "episodename", "titre", "nome"]),
    ("absolute",  "Absolute number",   False, ["id", "absolute", "abs", "overall", "absolute number", "absolute_number",
                                               "no overall", "numero geral", "#"]),
    ("tvdb_id",   "TheTVDB episode ID", False, ["tvdb_episode_id", "tvdb episode id", "tvdb id", "tvdb", "episodeid",
                                                "thetvdb", "tvdbid"]),
    ("aired",     "Aired",             False, ["originally_aired", "originally aired", "aired", "air date", "airdate",
                                               "first aired", "firstaired", "exibicao", "date", "data", "release date",
                                               "estreia", "premiered"]),
    ("runtime",   "Runtime (min)",     False, ["runtime_min", "runtime", "duration", "duracao", "length", "minutes", "min"]),
    ("mpaa",      "Content rating",    False, ["content_rating", "content rating", "rating", "mpaa", "certification",
                                               "classificacao", "classificacao indicativa"]),
    ("imdb_id",   "IMDb ID",           False, ["imdb_id", "imdb", "imdb id", "imdbid"]),
    ("plot",      "Plot",              False, ["description", "overview", "plot", "synopsis", "summary", "sinopse",
                                               "resumo", "descricao", "storyline"]),
    ("directors", "Directors",         False, ["director", "directors", "diretor", "direcao", "directed by"]),
    ("writers",   "Writers",           False, ["writer", "writers", "credits", "roteiro", "written by", "escritor"]),
    ("guests",    "Guest stars",       False, ["guest stars", "gueststars", "guests", "guest", "convidados"]),
    ("network",   "Network",           False, ["network", "channel", "canal", "emissora", "studio"]),
    ("image",     "Image (URL or file)", False, ["image", "thumb", "thumbnail", "still", "screenshot", "imagem", "filename"]),
]
FIELD_LABELS = {k: label for k, label, _, _ in FIELDS}
REQUIRED_FIELDS = [k for k, _, req, _ in FIELDS if req]


def norm_header(h: str) -> str:
    h = strip_accents(str(h or "")).lower().replace("_", " ")
    return re.sub(r"[^a-z0-9#]+", " ", h).strip()


_ALIAS = {}
for _k, _label, _req, _aliases in FIELDS:
    for _a in _aliases:
        _ALIAS.setdefault(norm_header(_a), _k)


def find_header_row(rows) -> int:
    """The first row (within 30) with at least 3 known column names; else the first non-empty row."""
    first = None
    for i, r in enumerate(rows[:30]):
        cells = [c for c in r if str(c).strip()]
        if not cells:
            continue
        if first is None:
            first = i
        if sum(1 for c in r if norm_header(c) in _ALIAS) >= 3:
            return i
    return first or 0


def header_signature(headers) -> str:
    return "|".join(sorted(norm_header(h) for h in headers if str(h).strip()))


def suggest_mapping(headers, remembered=None) -> dict:
    """{field: header or None}. Remembered mapping first, then exact aliases, then fuzzy (difflib ≥ 0.75)."""
    out = {k: None for k, *_ in FIELDS}
    used = set()
    for k, h in (remembered or {}).items():
        if k in out and h in headers and h not in used:
            out[k], _ = h, used.add(h)
    for h in headers:
        k = _ALIAS.get(norm_header(h))
        if k and out[k] is None and h not in used:
            out[k] = h
            used.add(h)
    for k, _label, _req, aliases in FIELDS:
        if out[k] is not None:
            continue
        best, best_r = None, 0.0
        for h in headers:
            if h in used or not str(h).strip():
                continue
            nh = norm_header(h)
            for a in aliases:
                na = norm_header(a)
                r = difflib.SequenceMatcher(None, nh, na).ratio()
                if len(na) >= 5 and na in nh:
                    r = max(r, 0.8)
                if r > best_r:
                    best, best_r = h, r
        if best is not None and best_r >= 0.75:
            out[k] = best
            used.add(best)
    return out


def missing_required(mapping) -> list:
    return [k for k in REQUIRED_FIELDS if not mapping.get(k)]


_FOOTER_RE = re.compile(r"^(total|totals|sum|soma|count|contagem|subtotal)\b", re.I)
_IMDB_RE = re.compile(r"tt\d{6,10}")


def _split_people(v: str) -> list:
    return [p.strip() for p in re.split(r"\s*[|;/]\s*|\s*,\s*", v or "") if p.strip()]


def rows_to_episodes(table: Table, mapping: dict, date_order="auto", source="csv") -> tuple:
    """(episodes, report). report: [{"line", "kind", "message", "cells"}] — nothing is dropped silently (F1.4)."""
    idx = {k: (table.headers.index(h) if h in table.headers else None) for k, h in mapping.items() if h}
    header_norm = [norm_header(h) for h in table.headers]

    def cell(row, k):
        i = idx.get(k)
        return row[i].strip() if i is not None and i < len(row) else ""

    if date_order == "auto" and "aired" in idx:
        date_order = detect_date_order(cell(r, "aired") for _, r in table.data) or "auto"
    episodes, report, seen = [], [], {}
    for line, row in table.data:
        if not row or (len(row) == 1 and not row[0].strip()):
            report.append({"line": line, "kind": "blank", "message": "blank row — ignored", "cells": row})
            continue
        if not any(re.search(r"\w", c) for c in row):
            report.append({"line": line, "kind": "separators", "message": "row with separators only — ignored",
                           "cells": row})
            continue
        if [norm_header(c) for c in row[:len(header_norm)]] == header_norm:
            report.append({"line": line, "kind": "repeated_header", "message": "repeated header row — ignored",
                           "cells": row})
            continue
        if _FOOTER_RE.match(row[0].strip() if row else ""):
            report.append({"line": line, "kind": "footer", "message": f"total/footer row ('{row[0]}') — ignored",
                           "cells": row})
            continue
        s_raw, e_raw, title = cell(row, "season"), cell(row, "episode"), cell(row, "title")
        s, e, absolute = parse_int(s_raw), parse_int(e_raw), parse_int(cell(row, "absolute"))
        if s is None or e is None:
            kind = "no_season_episode" if not s_raw or not e_raw else "not_number"
            report.append({"line": line, "kind": kind, "cells": row,
                           "message": f"no usable season/episode (season '{s_raw}', episode '{e_raw}')"
                                      + (f" — title '{title}'" if title else "") + " — ignored"})
            continue
        ep = Episode(s, e)
        ep.absolute = absolute
        ep.set("title", title, source)
        ep.set("plot", cell(row, "plot"), source)
        aired_raw = cell(row, "aired")
        aired, warn = parse_date(aired_raw, date_order)
        if warn and aired_raw:
            report.append({"line": line, "kind": "bad_date", "message": warn, "cells": row})
        ep.set("aired", aired if re.match(r"^\d{4}-\d\d-\d\d$", aired or "") else "", source)
        rt = parse_int(cell(row, "runtime"))
        ep.set("runtime", rt if rt and rt > 0 else None, source)
        ep.set("mpaa", cell(row, "mpaa"), source)
        tv = parse_int(cell(row, "tvdb_id"))
        ep.set("tvdb_id", str(tv) if tv else "", source)
        imdb_raw = cell(row, "imdb_id")
        m = _IMDB_RE.search(imdb_raw)
        if imdb_raw and not m:
            report.append({"line": line, "kind": "bad_imdb", "message": f"IMDb ID '{imdb_raw}' is not tt…", "cells": row})
        ep.set("imdb_id", m.group() if m else "", source)
        ep.set("directors", _split_people(cell(row, "directors")), source)
        ep.set("writers", _split_people(cell(row, "writers")), source)
        ep.set("guests", [(g, "", "") for g in _split_people(cell(row, "guests"))], source)
        ep.set("network", cell(row, "network"), source)
        img = cell(row, "image")
        if img:
            ep.images.append((img, source))
        if ep.key in seen:
            report.append({"line": line, "kind": "duplicate", "cells": row,
                           "message": f"{ep.code} already on line {seen[ep.key]} — kept the first one"})
            continue
        seen[ep.key] = line
        episodes.append(ep)
    return episodes, report


# ═══════════════════════════════════════════════════════════════════════
# THE THETVDB "ALL SEASONS" PASTE (F1.6)
# ═══════════════════════════════════════════════════════════════════════

_PASTE_SEASON = re.compile(r"^Season\s+(\d+)\s*$", re.I)
_PASTE_ADDITIONAL = re.compile(r"^Additional\s+Specials\s*$", re.I)
_PASTE_EP = re.compile(r"^S(\d{1,4})E(\d{1,4})\s+(.+?)\s*$")
_PASTE_SP = re.compile(r"^SPECIAL\s+(\d{1,4})x(\d{1,4})\s+(.+?)\s*$", re.I)
_PASTE_DATE_NET = re.compile(r"^([A-Z][a-z]+\.? \d{1,2}, \d{4})\s*(.*)$")


def parse_tvdb_paste(text: str, source="paste") -> tuple:
    """(episodes, unrecognised [(line number, text)]). Specials keep the place where they aired."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    episodes, unknown = [], []
    season_ctx, cur, body = None, None, []
    pending_specials = []          # specials waiting for the next regular episode of the same season

    def finish():
        if cur is None:
            return
        rest = [l for l in body if l.strip()]
        if rest:
            m = _PASTE_DATE_NET.match(rest[0].strip())
            if m:
                aired, _ = parse_date(m.group(1))
                cur.set("aired", aired, source)
                cur.set("network", m.group(2), source)
                rest = rest[1:]
            elif len(rest) > 1 and len(rest[0]) <= 40 and not rest[0].rstrip().endswith("."):
                cur.set("network", rest[0], source)
                rest = rest[1:]
        cur.set("plot", "\n".join(l.strip() for l in rest), source)

    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        m_season, m_add = _PASTE_SEASON.match(line), _PASTE_ADDITIONAL.match(line)
        m_ep, m_sp = _PASTE_EP.match(line), _PASTE_SP.match(line)
        if m_season or m_add or m_ep or m_sp:
            finish()
            cur, body = None, []
            if m_season or m_add:
                for sp in pending_specials:                     # specials at the end of a season air after it
                    sp.display = (season_ctx, None)
                pending_specials = []
                season_ctx = int(m_season.group(1)) if m_season else None
                continue
            if m_ep:
                cur = Episode(int(m_ep.group(1)), int(m_ep.group(2)))
                cur.set("title", m_ep.group(3), source)
                for sp in pending_specials:
                    sp.display = (cur.season, cur.episode)
                pending_specials = []
            else:
                cur = Episode(int(m_sp.group(1)), int(m_sp.group(2)))
                cur.set("title", m_sp.group(3), source)
                if season_ctx is not None:
                    pending_specials.append(cur)
            episodes.append(cur)
            continue
        if cur is not None:
            body.append(raw)
        elif line:
            unknown.append((n, line))
    finish()
    for sp in pending_specials:
        sp.display = (season_ctx, None) if season_ctx is not None else None
    return episodes, unknown


# ═══════════════════════════════════════════════════════════════════════
# EPISODE REFERENCES IN FILE NAMES (B13) AND FOLDERS
# ═══════════════════════════════════════════════════════════════════════

_REF_SE = re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,4})[ ._-]?[Ee](\d{1,4})((?:(?:-?[Ee]|-)\d{1,4}(?!\d))*)")
_REF_X = re.compile(r"(?<![0-9A-Za-z])(\d{1,2})x(\d{2,3})((?:x\d{2,3})*)(?![0-9])")
_REF_WORDS = re.compile(r"(?:season|temporada|staffel|saison)\s*(\d{1,4})\D{0,6}?(?:episode|episodio|epis[oó]dio|folge|ep)\.?\s*(\d{1,4})", re.I)
_REF_EP_ONLY = re.compile(r"(?<![A-Za-z0-9])(?:episode|episodio|epis[oó]dio|ep|e)[ ._-]?(\d{1,4})(?![0-9])", re.I)
_SEGMENT = re.compile(r"(?:^|[ ._\-\[(])(?:pt|part|parte|cd|disc|disk)[ ._-]?(\d{1,2})(?=$|[ ._\-\])])", re.I)
_SEASON_DIR = re.compile(r"^(?:season|temporada|staffel|saison|s)[ ._-]*0*(\d{1,4})$", re.I)
_SPECIALS_DIR = re.compile(r"^(?:specials?|especiais|extras?|season[ ._-]*0+|s0+)$", re.I)


def season_from_folder(name: str):
    n = name.strip()
    if _SPECIALS_DIR.match(n):
        return 0
    m = _SEASON_DIR.match(n)
    return int(m.group(1)) if m else None


def show_from_folder(name: str) -> tuple:
    m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", name.strip())
    return (m.group(1).strip(), int(m.group(2))) if m else (name.strip(), None)


def parse_episode_ref(name: str, folder_season=None):
    """{'season', 'episodes': [..], 'segment', 'title'} or None. Handles S01E01, S01E01E02, S01E01-E02, S01 E02,
    1x02, 'Season 1 Episode 2', 'Episode 5' / 'Ep05' (season from the folder), S2024E01, pt1/part 2/cd1."""
    stem = os.path.splitext(os.path.basename(name))[0] if os.path.splitext(name)[1].lower() in (
        VIDEO_EXTS | SUB_EXTS | IMAGE_EXTS | {".nfo", ".xml"}) else os.path.basename(name)
    season, eps, end = None, [], None
    m = _REF_SE.search(stem)
    if m:
        season, eps, end = int(m.group(1)), [int(m.group(2))], m.end()
        extra = [int(x) for x in re.findall(r"(\d{1,4})", m.group(3) or "")]
        if any(x <= eps[0] or x > eps[0] + 30 for x in extra):      # "S01E05-1080p" is not a range
            extra, end = [], m.start(3)
        if len(extra) == 1 and extra[0] > eps[0] + 1 and "-" in m.group(3):
            eps = list(range(eps[0], extra[0] + 1))          # S01E01-E03 → 1, 2, 3
        else:
            eps += extra
    else:
        m = _REF_X.search(stem)
        if m:
            season, eps, end = int(m.group(1)), [int(m.group(2))], m.end()
            eps += [int(x) for x in re.findall(r"x(\d{2,3})", m.group(3) or "")]
        else:
            m = _REF_WORDS.search(stem)
            if m:
                season, eps, end = int(m.group(1)), [int(m.group(2))], m.end()
            else:
                m = _REF_EP_ONLY.search(stem)
                if m and folder_season is not None:
                    season, eps, end = folder_season, [int(m.group(1))], m.end()
    if season is None:
        return None
    rest = stem[end:]
    seg_m = _SEGMENT.search(rest)
    segment = int(seg_m.group(1)) if seg_m else None
    title = rest[:seg_m.start()] + " " + rest[seg_m.end():] if seg_m else rest
    if segment is None:
        lm = re.match(r"^([a-d])(?=$|[ ._-])", rest)
        if lm:
            segment = "abcd".index(lm.group(1)) + 1
            title = rest[1:]
    title = re.sub(r"\[[^\]]*\]|\([^)]*p\)", " ", title)
    title = re.sub(r"(?i)(?:^|[ ._-])(?:\d{3,4}p|[248]k|x26[45]|h\.?26[45]|hevc|avc|web[ ._-]?(?:dl|rip)|bluray|"
                   r"brrip|bdrip|dvdrip|hdtv|hdr10?|remux|aac\d?(?:\.\d)?|ac3|dts(?:-hd)?|ddp?\d?(?:\.\d)?)(?=$|[ ._-])",
                   " ", title)
    title = re.sub(r"^[ ._-]+|[ ._-]+$", "", re.sub(r"[ ._]+", " ", title)).strip(" -")
    return {"season": season, "episodes": sorted(set(eps)), "segment": segment, "title": title}


# ═══════════════════════════════════════════════════════════════════════
# NAMES
# ═══════════════════════════════════════════════════════════════════════

_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def sanitize_filename(text: str, max_len: int = 0) -> str:
    text = clean_text(text).replace("\n", " ")
    text = re.sub(r'[\\/*?"<>|]', "", text)
    text = re.sub(r"\s*:\s*", " - ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(". ")
    if max_len and len(text) > max_len:
        text = text[:max_len - 1].rstrip(" .-") + "…"
    if text.lower() in _RESERVED:
        text += "_"
    return text


def episode_code(season: int, episodes) -> str:
    eps = list(episodes) if isinstance(episodes, (list, tuple)) else [episodes]
    code = f"S{season:02d}E{eps[0]:02d}"
    if len(eps) > 1:
        code += "-E" + f"{eps[-1]:02d}"
    return code


def episode_base_name(show: str, season: int, episodes, title: str, segment=None, year=None,
                      pattern: str = "{show} - {code} - {title}", max_len: int = 80) -> str:
    seg = f" - pt{segment}" if segment else ""
    name = pattern.format(show=sanitize_filename(show), year=year or "", code=episode_code(season, episodes),
                          title=sanitize_filename(title, max_len), segment=seg)
    if segment and "{segment}" not in pattern:
        name += seg
    return re.sub(r"\s*\(\s*\)", "", name).strip(" -")


def norm_title(t: str) -> str:
    t = strip_accents(str(t or "")).lower()
    t = re.sub(r"\((\d)\)|\bpart\s*\d\b|\bpt\s*\d\b", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(nb) >= 4 and nb in na or len(na) >= 4 and na in nb:
        return 0.95
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ═══════════════════════════════════════════════════════════════════════
# VIDEOS IN A SERIES FOLDER AND THE MATCH TO EPISODES (F3, F11.2)
# ═══════════════════════════════════════════════════════════════════════

class VideoFile:
    def __init__(self, path, folder_season=None):
        self.path = os.path.normpath(path)
        self.folder = os.path.dirname(self.path)
        self.stem = os.path.splitext(os.path.basename(self.path))[0]
        self.ref = parse_episode_ref(self.path, folder_season)
        self.folder_season = folder_season
        self.keys = []            # [(season, episode)] after matching
        self.segment = self.ref["segment"] if self.ref else None
        self.match_how = ""       # number | title | user
        self.confidence = 0.0
        self.probe = None         # ffprobe summary (kfg_media.probe)

    def __repr__(self):
        return f"VideoFile({os.path.basename(self.path)!r}, {self.keys})"


def find_videos(show_dir: str) -> list:
    """Videos in the show folder and its season folders (one level; 'metadata' and hidden folders skipped)."""
    out = []
    for root, dirs, files in os.walk(show_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("metadata", "extrafanart", "extras",
                                                                                   ".kfg_backup")]
        rel = os.path.relpath(root, show_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 2:
            continue
        fs = season_from_folder(os.path.basename(root)) if depth else None
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS and not re.search(r"(?i)[-._ ](sample|trailer)$",
                                                                              os.path.splitext(f)[0]):
                out.append(VideoFile(os.path.join(root, f), fs))
    return out


def match_videos(videos: list, episodes: dict) -> list:
    """Fill video.keys. By the number in the file name; when the file title clearly names another episode, or there
    is no number, by title similarity (user note: 'identify similar episode names'). Returns notes [(video, text)]."""
    notes = []
    by_season = {}
    for ep in episodes.values():
        by_season.setdefault(ep.season, []).append(ep)
    for v in videos:
        v.keys, v.match_how, v.confidence = [], "", 0.0
        ref = v.ref
        cands = by_season.get(ref["season"] if ref else v.folder_season, []) or list(episodes.values())
        if ref and all((ref["season"], e) in episodes for e in ref["episodes"]):
            keys = [(ref["season"], e) for e in ref["episodes"]]
            v.keys, v.match_how, v.confidence = keys, "number", 1.0
            ftitle = ref.get("title") or ""
            if ftitle and len(norm_title(ftitle)) >= 4:
                own = title_similarity(ftitle, episodes[keys[0]].get("title") or "")
                best = max(cands, key=lambda ep: title_similarity(ftitle, ep.get("title") or ""))
                best_r = title_similarity(ftitle, best.get("title") or "")
                if own < 0.5 and best_r >= 0.85 and best.key not in keys:
                    notes.append((v, f"the file name says {episode_code(*keys[0])} but its title matches "
                                     f"{best.code} '{best.get('title')}' ({best_r:.0%})"))
            continue
        ftitle = (ref or {}).get("title") or v.stem
        best, best_r = None, 0.0
        for ep in cands:
            r = title_similarity(ftitle, ep.get("title") or "")
            if r > best_r:
                best, best_r = ep, r
        if best is not None and best_r >= 0.6:
            v.keys, v.match_how, v.confidence = [best.key], "title", best_r
            if ref:
                notes.append((v, f"{episode_code(ref['season'], ref['episodes'])} is not in the episode list; "
                                 f"matched by title to {best.code} ({best_r:.0%})"))
        else:
            notes.append((v, "no episode found for this file"))
    return notes
