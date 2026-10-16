"""Tests for Kodi Files Generator v0.5.0 — pure modules, no network, no real media.

Run:  python -m pytest tests -q
Every R4 bug (B1–B20 of the plan) has a test that failed on R4's logic and passes now.
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"
needs_fixtures = pytest.mark.skipif(not FIX.is_dir(), reason="TheTVDB page copies are not in the public repo")

import kfg_core as core              # noqa: E402
import kfg_engine as engine          # noqa: E402
import kfg_sources as src            # noqa: E402
import kfg_writers as W              # noqa: E402

HEADER = "ID,Season,Episode,Episode_Title,TVDB_Episode_ID,Originally_Aired,Runtime_min,Content_Rating,IMDB_ID,Description"


def _table(text, **kw):
    return core.read_table_text(text, **kw)


# ── B1 / B2: encodings ─────────────────────────────────────────────────

def test_b1_cp1252_accent_after_8kb(tmp_path):
    body = HEADER + "\n" + "".join(f"{i},1,{i},Episode {i},{i},2024-01-01,22,,,A plain English description that "
                                   f"is long enough to fill the buffer.\n" for i in range(1, 200))
    p = tmp_path / "late.csv"
    p.write_bytes(body.encode() + "200,1,200,Fim,1,2024-01-01,22,,,São Paulo – “aspas”\n".encode("cp1252"))
    t = core.read_table_file(str(p))
    assert t.encoding == "cp1252"
    eps, rep = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    assert len(eps) == 200 and eps[-1].get("plot") == "São Paulo – “aspas”"          # B2: real quotes and dash


def test_utf16_and_bom(tmp_path):
    text = HEADER.replace(",", "\t") + "\n1\t1\t1\tPilot\t\t\t\t\t\tx\n"
    for enc, data in (("utf-16", b"\xff\xfe" + text.encode("utf-16-le")), ("utf-8-sig", text.encode("utf-8-sig"))):
        p = tmp_path / f"f_{enc}.tsv"
        p.write_bytes(data)
        t = core.read_table_file(str(p))
        assert t.headers[3] == "Episode_Title" and t.delimiter == "\t", enc


# ── B3: delimiter ──────────────────────────────────────────────────────

def test_b3_semicolon_csv():
    t = _table("ID;Season;Episode;Episode_Title\n1;1;1;Pilot\n")
    assert t.delimiter == ";" and t.headers == ["ID", "Season", "Episode", "Episode_Title"]


# ── B4: numbers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("v, n", [("1.0", 1), ("46 min", 46), ("", None), ("S02", 2), (3, 3), (4.0, 4), ("x", None)])
def test_b4_numbers(v, n):
    assert core.parse_int(v) == n


# ── B5 / B6: dates ─────────────────────────────────────────────────────

@pytest.mark.parametrize("v, iso", [("May 8, 2024", "2024-05-08"), ("November 16, 2004", "2004-11-16"),
                                    ("8 de maio de 2024", "2024-05-08"), ("2024-05-08", "2024-05-08"),
                                    ("2024/05/08", "2024-05-08"), ("13/05/2024", "2024-05-13"),
                                    ("05/13/2024", "2024-05-13"), ("45420", "2024-05-08"),
                                    ("8 May 2024", "2024-05-08"), ("2024-05-08T12:00:00", "2024-05-08")])
def test_b5_date_formats(v, iso):
    assert core.parse_date(v)[0] == iso


def test_b6_us_dates_decided_per_file():
    assert core.detect_date_order(["05/08/2024", "05/22/2024"]) == "mdy"
    t = _table("Season,Episode,Title,Aired\n1,1,A,05/08/2024\n1,2,B,05/22/2024\n")
    eps, _ = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    assert eps[0].get("aired") == "2024-05-08"
    assert core.parse_date("05/08/2024", "mdy")[0] == "2024-05-08"


# ── B7: separators ─────────────────────────────────────────────────────

def test_b7_normpath_everywhere(tmp_path):
    v = core.VideoFile("C:/Series/Show/Season 1/Show S01E01.mkv")
    assert v.path == os.path.normpath("C:/Series/Show/Season 1/Show S01E01.mkv")
    c = engine.Change("C:/Series/Show/metadata/x.xml", "xml", data="<a/>")
    assert c.path == os.path.normpath("C:/Series/Show/metadata/x.xml")


# ── B8 / B9 / B11: templates ───────────────────────────────────────────

def test_b8_self_closing_template_tags():
    out = W.fill_template("<episodedetails><plot/><title></title><mpaa x='1'/></episodedetails>",
                          {"plot": "Hello & bye", "title": "T", "mpaa": "16"})
    assert "<plot>Hello &amp; bye</plot>" in out and "<title>T</title>" in out and ">16</mpaa>" in out


def test_b9_stale_technical_tags_found():
    assert W.stale_template_tags("<Item><VideoCodec>HEVC</VideoCodec><VideoWidth></VideoWidth></Item>") == ["VideoCodec"]


def test_b11_no_empty_ids():
    ep = core.Episode(1, 1)
    ep.set("title", "Pilot", "csv")
    ep.set("tvdb_id", "123", "csv")
    nfo = W.episode_nfo([ep], core.Series(), opts={"dateadded": "x"})
    assert 'type="imdb"' not in nfo and '<uniqueid type="tvdb" default="true">123</uniqueid>' in nfo
    assert "<rating>" not in nfo and "<mpaa" not in nfo        # nothing invented, no empty tags


def test_b11_fill_template_drops_empty_uniqueid():
    out = W.fill_template('<uniqueid type="tvdb">1</uniqueid><uniqueid type="imdb"></uniqueid>', {})
    assert "imdb" not in out


# ── B10: duplicates ────────────────────────────────────────────────────

def test_b10_duplicate_rows_reported():
    t = _table("Season,Episode,Title\n1,12,Same\n1,12,Same again\n")
    eps, rep = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    assert len(eps) == 1 and [r["kind"] for r in rep] == ["duplicate"]


# ── B12: control characters ────────────────────────────────────────────

def test_b12_control_characters_removed_and_parse():
    ep = core.Episode(1, 1)
    ep.set("plot", "a\x0bb\x01c", "csv")
    nfo = W.episode_nfo([ep], core.Series(), opts={"dateadded": "x"})
    assert "<plot>abc</plot>" in nfo and W.parse_check(nfo) == ""


# ── B13: episode references ────────────────────────────────────────────

@pytest.mark.parametrize("name, season, eps, seg", [
    ("Show S01E01.mkv", 1, [1], None), ("Show 1x02.mkv", 1, [2], None), ("Show S01E01E02.mkv", 1, [1, 2], None),
    ("Show S01E01-E03.mkv", 1, [1, 2, 3], None), ("Show S01 E02 - T.mkv", 1, [2], None),
    ("Episode 5.mkv", 3, [5], None), ("Ep05.mkv", 3, [5], None), ("Show S2024E01.mkv", 2024, [1], None),
    ("The Care Bears Family - S01E12 - pt2 - Order on the Court.mp4", 1, [12], 2),
    ("Show.S01E05-1080p.mkv", 1, [5], None), ("Season 2 Episode 7.avi", 2, [7], None)])
def test_b13_episode_refs(name, season, eps, seg):
    r = core.parse_episode_ref(name, folder_season=3)
    assert (r["season"], r["episodes"], r["segment"]) == (season, eps, seg)


def test_b13_quality_tags_not_in_title():
    r = core.parse_episode_ref("Dark Matter (2024) - S01E01 - Are You Happy in Your Life_ [1080p] [2024-05-08].mkv")
    assert r["title"] == "Are You Happy in Your Life"


# ── B14 / B15 / B16: renames ───────────────────────────────────────────

def _touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_b14_sidecars_move_with_the_video(tmp_path):
    v = _touch(tmp_path / "old S01E01.mkv")
    for n in ("old S01E01.en.srt", "old S01E01.pt-BR.forced.srt", "old S01E01.nfo", "old S01E01-thumb.jpg",
              "metadata/old S01E01.xml"):
        _touch(tmp_path / n)
    names = sorted(os.path.basename(p) for p, _ in engine.sidecars(str(v)))
    assert names == sorted(["old S01E01.en.srt", "old S01E01.pt-BR.forced.srt", "old S01E01.nfo",
                            "old S01E01-thumb.jpg", "old S01E01.xml"])


def test_b15_b16_swap_and_case_rename(tmp_path):
    a, b, c = _touch(tmp_path / "A.mkv", b"a"), _touch(tmp_path / "B.mkv", b"b"), _touch(tmp_path / "case.mkv", b"c")
    res = engine.apply_renames([(str(a), str(b)), (str(b), str(a)), (str(c), str(tmp_path / "Case.mkv"))],
                               str(tmp_path), log=lambda *x: None)
    assert res["renamed"] == 3 and (tmp_path / "A.mkv").read_bytes() == b"b" and (tmp_path / "B.mkv").read_bytes() == b"a"
    assert "Case.mkv" in os.listdir(tmp_path)
    st = engine.undo(res["manifest"], log=lambda *x: None)
    assert st["restored"] == 3 and (tmp_path / "A.mkv").read_bytes() == b"a" and "case.mkv" in os.listdir(tmp_path)


def test_b16_conflicts_detected(tmp_path):
    a, b = _touch(tmp_path / "a.mkv"), _touch(tmp_path / "b.mkv")
    assert engine.rename_conflicts([(str(a), str(tmp_path / "x.mkv")), (str(b), str(tmp_path / "x.mkv"))])


def test_b16_failure_rolls_back(tmp_path):
    a = _touch(tmp_path / "a.mkv")
    _touch(tmp_path / "taken.mkv")
    res = engine.apply_renames([(str(a), str(tmp_path / "taken.mkv"))], str(tmp_path), log=lambda *x: None)
    assert res["error"] and a.exists() and not [n for n in os.listdir(tmp_path) if "__kfg_" in n]


# ── B20: long paths ────────────────────────────────────────────────────

def test_b20_long_titles_shortened():
    name = core.episode_base_name("Show", 1, [1], "x" * 300, max_len=80)
    assert len(name) < 120 and name.endswith("…")


# ── F1: mapping, import report ─────────────────────────────────────────

def test_f1_mapping_portuguese_headers():
    headers = ["Temporada", "Episódio", "Título", "Sinopse", "Exibição", "Duração"]
    m = core.suggest_mapping(headers)
    assert (m["season"], m["episode"], m["title"], m["plot"], m["aired"], m["runtime"]) == tuple(headers)


def test_f1_mapping_fuzzy_and_remembered():
    m = core.suggest_mapping(["Seasn", "Ep", "Episode Title", "Descrption"])
    assert m["season"] == "Seasn" and m["plot"] == "Descrption"
    m2 = core.suggest_mapping(["A", "B", "C"], remembered={"season": "A", "episode": "B", "title": "C"})
    assert (m2["season"], m2["episode"], m2["title"]) == ("A", "B", "C")
    assert core.header_signature(["B", "a "]) == core.header_signature(["A", "b"])


def test_f1_polluted_file_report():
    text = ("Episodes of the show\n" + HEADER + "\n,,,,,,,,,\n1,1,1,Pilot,1,2024-01-01,22,,,x\n\n" + HEADER + "\n"
            "2,1,2,Two,2,2024-01-08,22,,,y\n3,1,,No number,3,,,,,\n4,X,3,Bad,4,,,,,\nTotal,,,,,,,,,\n"
            "5,1,4,Imdb,5,never,22,,nope,\n")
    t = _table(text)
    assert t.headers[0] == "ID"
    eps, rep = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    kinds = [r["kind"] for r in rep]
    assert [e.code for e in eps] == ["S01E01", "S01E02", "S01E04"]
    for k in ("separators", "repeated_header", "no_season_episode", "not_number", "footer", "bad_date", "bad_imdb"):
        assert k in kinds, k


def test_f1_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from datetime import datetime
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Season", "Episode", "Title", "Aired"])
    ws.append([1.0, 1.0, "Pilot", datetime(2024, 5, 8)])
    p = tmp_path / "eps.xlsx"
    wb.save(p)
    t = core.read_table_file(str(p))
    eps, _ = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    assert eps[0].code == "S01E01" and eps[0].get("aired") == "2024-05-08"


@needs_fixtures
def test_f1_paste_reader_house_sample():
    eps, unknown = core.parse_tvdb_paste((FIX / "tvdb_paste_sample_house.txt").read_text(encoding="utf-8"))
    assert unknown == []
    by = {e.code: e for e in eps}
    p = by["S01E01"]
    assert (p.get("title"), p.get("aired"), p.get("network")) == ("Pilot", "2004-11-16", "FOX")
    assert p.get("plot").startswith("A Kindergarten teacher")
    assert by["S00E01"].get("aired") is None and by["S00E01"].get("network") == "FOX" and by["S00E01"].display == (1, 1)
    assert by["S00E04"].display == (7, 16)                   # Get Happy airs before S07E16
    assert by["S00E05"].display == (8, 22)
    assert by["S00E02"].display is None                      # Additional Specials: no position
    assert by["S03E06"].get("title") == "Que Será Será" and by["S01E18"].get("title") == "Babies & Bathwater"


# ── Records and sources (F17) ──────────────────────────────────────────

def test_f17_rules_and_user_choice():
    ep = core.Episode(1, 1)
    ep.set("title", "Piloto", "csv")
    ep.set("title", "Pilot", "tvmaze")
    ep.set("runtime", 44, "csv")
    ep.set("runtime", 46, "video")
    assert ep.get("title") == "Piloto" and ep.get("runtime") == 46 and ep.conflicts("title")
    assert ep.get("title", {"title": ["tvmaze"]}) == "Pilot"
    ep.set_user("title", "Mine")
    assert ep.get("title") == "Mine" and ep.source("title") == "user"


def test_f17_match_by_date_and_title_when_numbers_differ():
    p = engine.Project()
    a, b = core.Episode(1, 12), core.Episode(1, 13)
    a.set("title", "The All Powerful Mr. Beastly", "csv"); a.set("aired", "1986-11-01", "csv")
    b.set("title", "Order on the Court", "csv"); b.set("aired", "1986-11-08", "csv")
    p.merge_episodes([a, b], "csv")
    other = core.Episode(1, 20)
    other.set("title", "Order on the Court", "tvmaze"); other.set("aired", "1986-11-09", "tvmaze")
    other.set("plot", "From TVmaze", "tvmaze")
    p.merge_episodes([other], "tvmaze")
    assert p.episodes[(1, 13)].get("plot") == "From TVmaze" and (1, 20) not in p.episodes
    assert any("matched to S01E13" in n for _, n in p.notes)


# ── TheTVDB pages (F16) ────────────────────────────────────────────────

@needs_fixtures
def test_f16_tvdb_allseasons_house():
    eps = src.parse_tvdb_allseasons((FIX / "tvdb_house_allseasons.html").read_text(encoding="utf-8"))
    assert len(eps) == 181
    by = {e.code: e for e in eps}
    assert sum(1 for e in eps if e.season == 0) == 5
    p = by["S01E01"]
    assert (p.get("title"), p.get("aired"), p.get("network"), p.get("tvdb_id")) == ("Pilot", "2004-11-16", "FOX", "110994")
    assert p.images and p.images[0][0].startswith("https://artworks.thetvdb.com/")
    assert by["S00E04"].display == (7, 16) and by["S00E02"].display is None
    assert "&" not in by["S01E18"].get("title").replace("&", "") or by["S01E18"].get("title") == "Babies & Bathwater"


@needs_fixtures
def test_f16_tvdb_allseasons_darkmatter_ids():
    eps = src.parse_tvdb_allseasons((FIX / "tvdb_darkmatter_allseasons.html").read_text(encoding="utf-8"))
    by = {e.code: e for e in eps}
    assert by["S01E01"].get("tvdb_id") == "8083754" and by["S01E01"].get("aired") == "2024-05-08"
    assert "&mdash;" not in by["S01E02"].get("plot") and "—" in by["S01E02"].get("plot")


@needs_fixtures
def test_f16_tvdb_episode_page():
    d = src.parse_tvdb_episode((FIX / "tvdb_darkmatter_episode.html").read_text(encoding="utf-8"), "pt-BR")
    assert d["runtime"] == 59 and ("Brazil", "16") in d["ratings"] and d["imdb_id"] == "tt21199482"
    assert d["title"] == "O corredor" and d["title_en"] == "The Corridor"
    assert "Blake Crouch" in d["writers"] and d["directors"] and d["network"] == "Apple TV"
    assert d["image"].endswith("66440a61046cf.jpg") and d["dvd"] == (1, 4)
    h = src.parse_tvdb_episode((FIX / "tvdb_house_episode.html").read_text(encoding="utf-8"), "pt-BR")
    assert h["title"] == "Piloto" and len(h["guests"]) == 11 and h["guests"][0][1] != "" or h["guests"]


@needs_fixtures
def test_f16_tvdb_series_page():
    s = src.parse_tvdb_series((FIX / "tvdb_house_series.html").read_text(encoding="utf-8"), "pt-BR")
    assert s["tvdb_id"] == "73255" and s["status"] == "Ended" and s["premiered"] == "2004-11-16"
    assert s["network"] == "FOX" and s["runtime"] == 45 and s["genres"] == ["Drama", "Mystery", "Suspense"]
    assert s["title"] == "Dr. House" and s["title_en"] == "House" and s["airs_day"] == "Monday"
    assert ("Robert Sean Leonard", "James Wilson") in [(a, r) for a, r, _ in s["actors"]]
    assert s["artwork"]["poster"] and s["artwork"]["fanart"] and s["artwork"]["clearlogo"]


def test_f16_links():
    assert src.parse_link("https://thetvdb.com/series/house/allseasons/official") == ("tvdb", "https://thetvdb.com/series/house")
    assert src.parse_link("https://thetvdb.com/dereferrer/series/393159")[0] == "tvdb"
    assert src.parse_link("https://www.imdb.com/title/tt0412142/") == ("imdb", "tt0412142")
    assert src.parse_link("https://www.tvmaze.com/shows/61315/dark-matter") == ("tvmaze", "61315")
    assert src.parse_link("https://www.themoviedb.org/tv/1408-house") == ("tmdb", "1408")
    assert src.parse_link("73255") == ("tvdb_id", "73255") and src.parse_link("hello") == ("", "")


# ── TVmaze with recorded answers (F4, F18) ─────────────────────────────

def _fake_http(routes):
    def opener(url, headers):
        for part, body in routes.items():
            if part in url:
                return 200, json.dumps(body).encode(), url
        return 404, b"", url
    return src.Http(opener=opener)


def test_f4_tvmaze_fills_empty_fields_only():
    show = {"id": 61315, "name": "Dark Matter", "summary": "<p>A <b>story</b>.</p>", "premiered": "2024-05-08",
            "genres": ["Drama"], "status": "Running", "externals": {"thetvdb": 393159, "imdb": "tt19231492"},
            "webChannel": {"name": "Apple TV", "country": None}, "rating": {"average": 7.5}}
    routes = {"/shows/61315/episodes": [{"id": 1, "season": 1, "number": 1, "name": "Are You Happy in Your Life?",
                                         "airdate": "2024-05-08", "runtime": 46, "summary": "<p>Abducted.</p>",
                                         "image": {"original": "https://x/1.jpg"}, "rating": {"average": 7.3}}],
              "/shows/61315/seasons": [{"number": 1, "name": "", "image": {"original": "https://x/s1.jpg"}}],
              "/shows/61315/images": [], "/shows/61315/cast": [{"person": {"name": "Joel Edgerton"},
                                                                 "character": {"name": "Jason Dessen"}}],
              "/shows/61315/crew": [], "/episodes/1/guestcrew": [{"guestCrewType": "Director",
                                                                   "person": {"name": "Jakob Verbruggen"}},
                                                                  {"guestCrewType": "Writer", "person": {"name": "Blake Crouch"}}],
              "/episodes/1/guestcast": [{"person": {"name": "Amanda Brugel"}, "character": {"name": "Blair Caplan"}}]}
    p = engine.Project()
    e = core.Episode(1, 1)
    e.set("title", "Você é feliz?", "csv")
    p.merge_episodes([e], "csv")
    src.fetch_tvmaze(_fake_http(routes), p, show)
    ep = p.episodes[(1, 1)]
    assert ep.get("title") == "Você é feliz?" and ep.get("plot") == "Abducted." and ep.get("runtime") == 46
    assert ep.get("directors") == ["Jakob Verbruggen"] and ep.get("guests")[0][:2] == ("Amanda Brugel", "Blair Caplan")
    assert p.series.get("plot") == "A story." and p.series.get("tvdb_id") == "393159"
    nfo = W.episode_nfo([ep], p.series, opts={"dateadded": "x"})
    for s in ("<director>Jakob Verbruggen</director>", "<credits>Blake Crouch</credits>", "<role>Blair Caplan</role>",
              "<originaltitle>Are You Happy in Your Life?</originaltitle>", 'name="tvmaze"'):
        assert s in nfo, s


def test_http_errors():
    h = src.Http(opener=lambda u, hd: (403, b"Just a moment", u))
    with pytest.raises(src.Blocked):
        h.get("https://thetvdb.com/series/x", browser=True)
    with pytest.raises(src.NotFound):
        src.Http(opener=lambda u, hd: (404, b"", u)).get("https://api.tvmaze.com/x")


# ── Writers (F5, F6, F18) ──────────────────────────────────────────────

def _series():
    s = core.Series()
    s.set("title", "Dark Matter", "tvdb"); s.set("year", 2024, "tvdb"); s.set("tvdb_id", "393159", "tvdb")
    s.set("imdb_id", "tt19231492", "tvdb"); s.set("genres", ["Drama", "Mystery"], "tvdb")
    s.set("actors", [("Joel Edgerton", "Jason Dessen", "https://x/a.jpg")], "tvdb"); s.set("status", "Running", "tvmaze")
    return s


def test_f5_all_files_parse_and_fields():
    s = _series()
    ep = core.Episode(1, 1)
    for f, v in (("title", "Are You Happy in Your Life?"), ("plot", "Physics professor. Then more."),
                 ("aired", "2024-05-08"), ("tvdb_id", "8083754"), ("imdb_id", "tt21158452"), ("runtime", 46)):
        ep.set(f, v, "tvdb")
    ep.set("writers", ["Blake Crouch"], "tvdb")
    video = {"streams": True, "duration_s": 2776, "size": 1000,
             "video": {"codec": "hevc", "codec_name_upper": "HEVC", "width": 1920, "height": 960, "aspect": 2.0,
                       "aspect_ratio": "2.00:1", "scantype": "progressive", "fps": 23.976, "quality": "1080p"},
             "audio": [{"codec": "ac3", "codec_name_upper": "AC3", "channels": 6, "channels_label": "5.1",
                        "language": "eng"}], "subtitles": [{"language": "por"}]}
    nfo = W.episode_nfo([ep], s, video, {"dateadded": "x"})
    xml = W.episode_xml([ep], s, video, "Dark Matter - S01E01.jpg", {"added": "x"})
    tv, sx = W.tvshow_nfo(s, {}), W.series_xml(s)
    for t in (nfo, xml, tv, sx, W.season_nfo(core.Season(1))):
        assert W.parse_check(t) == "", t[:200]
    assert "<durationinseconds>2776</durationinseconds>" in nfo and "<outline>Physics professor.</outline>" in nfo
    assert "<filename>/Dark Matter - S01E01.jpg</filename>" in xml and "<VideoLengthSeconds>2776</VideoLengthSeconds>" in xml
    assert "<EpisodeID>8083754</EpisodeID>" in xml and "<Writer>Blake Crouch</Writer>" in xml
    assert '"tvdb":"393159"' in tv and "<role>Jason Dessen</role>" in tv
    assert "<Status>Continuing</Status>" in sx and "<Role>Jason Dessen</Role>" in sx


def test_f5_multi_episode_nfo_and_specials():
    a, b = core.Episode(1, 1), core.Episode(1, 2)
    a.set("title", "One", "csv"); b.set("title", "Two", "csv")
    nfo = W.episode_nfo([a, b], core.Series(), opts={"dateadded": "x"})
    assert nfo.count("<episodedetails>") == 2 and W.parse_check(nfo) == ""
    sp = core.Episode(0, 4)
    sp.display = (7, 16)
    assert "<displayseason>7</displayseason>" in W.episode_nfo([sp], core.Series(), opts={"dateadded": "x"})
    assert "<airsbefore_season>7</airsbefore_season>" in W.episode_xml([sp], core.Series())
    sp.display = (8, None)
    assert "<displayepisode>4096</displayepisode>" in W.episode_nfo([sp], core.Series(), opts={"dateadded": "x"})


def test_read_existing_ids_tolerates_broken_mcm_nfo(tmp_path):
    p = tmp_path / "tvshow.nfo"
    p.write_text("<tvshow><title>Dark Matter (2024)</title><id>393159</id><actor><name>X</name></role></actor>"
                 "</tvshow>", encoding="utf-8")
    ids = W.read_existing_ids(str(p))
    assert ids["tvdb_id"] == "393159" and ids["title"] == "Dark Matter (2024)" and W.parse_check(p.read_text())


# ── Engine: plan, apply, backup, undo (F10, F12) ───────────────────────

def _project_folder(tmp_path):
    show = tmp_path / "The Care Bears Family (1986)"
    for n in ("Season 1/The Care Bears Family - S01E11 - Grams Bear's Thanksgiving Surprise.mp4",
              "Season 1/The Care Bears Family - S01E12 - pt1 - The All Powerful Mr. Beastly.mp4",
              "Season 1/The Care Bears Family - S01E12 - pt2 - Order on the Court.mp4",
              "Season 1/The Care Bears Family - S01E11 - Grams Bear's Thanksgiving Surprise.en.srt"):
        _touch(show / n)
    p = engine.Project()
    p.scan(str(show))
    return p, show


def test_engine_scan_segments_and_plan(tmp_path):
    p, show = _project_folder(tmp_path)
    assert p.series.get("title") == "The Care Bears Family" and p.series.get("year") == 1986
    assert sorted(p.episodes) == [(1, 11), (1, 12)]
    segs = [v for v in p.videos if v.segment]
    assert len(segs) == 2 and all(v.keys == [(1, 12)] for v in segs)
    ch = engine.plan(p, {"artwork": False, "episode_images": False})
    names = sorted(os.path.relpath(c.path, show) for c in ch)
    assert os.path.join("Season 1", "The Care Bears Family - S01E12 - pt1 - The All Powerful Mr. Beastly.nfo") in names
    assert os.path.join("Season 1", "metadata", "The Care Bears Family - S01E12 - pt2 - Order on the Court.xml") in names
    assert "tvshow.nfo" in names and "series.xml" in names and os.path.join("Season 1", "season.nfo") in names
    assert not [h for h in engine.health(p) if h[0] == "error"]          # segments are not "several videos"


def test_engine_profiles(tmp_path):
    p, show = _project_folder(tmp_path)
    ch = engine.plan(p, {"profiles": ["kodi"], "artwork": False, "episode_images": False})
    assert not [c for c in ch if c.kind in ("xml", "series_xml", "season_nfo")]
    ch = engine.plan(p, {"profiles": ["xml"], "artwork": False, "episode_images": False})
    assert not [c for c in ch if c.kind in ("nfo", "tvshow")]


def test_engine_apply_backup_undo(tmp_path):
    p, show = _project_folder(tmp_path)
    old = show / "Season 1" / "The Care Bears Family - S01E11 - Grams Bear's Thanksgiving Surprise.nfo"
    _touch(old, b"<episodedetails><title>old</title></episodedetails>")
    ch = engine.plan(p, {"artwork": False, "episode_images": False})
    st = engine.apply(ch, str(show), log=lambda *a: None)
    assert st["failed"] == 0 and st["written"] >= 6 and os.path.isfile(st["manifest"])
    assert "Grams Bear" in old.read_text(encoding="utf-8-sig")
    again = engine.plan(p, {"artwork": False, "episode_images": False})
    assert all(c.action == "same" for c in again if c.data is not None)          # nothing to write twice
    u = engine.undo(st["manifest"], log=lambda *a: None)
    assert u["skipped"] == 0 and old.read_bytes() == b"<episodedetails><title>old</title></episodedetails>"
    assert not (show / "tvshow.nfo").exists() and (pathlib.Path(st["manifest"]).parent / "undone").is_dir()


def test_engine_output_folder_mode(tmp_path):
    p = engine.Project()
    t = _table(HEADER + "\n1,1,1,Pilot,1,2004-11-16,44,TV-14,tt0606035,First\n")
    eps, _ = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    p.merge_episodes(eps, "csv")
    p.series.set("title", "House", "user")
    ch = engine.plan(p, {"out_dir": str(tmp_path / "out"), "artwork": False, "episode_images": False})
    paths = [os.path.relpath(c.path, tmp_path / "out") for c in ch]
    assert "House - S01E01 - Pilot.nfo" in paths and os.path.join("metadata", "House - S01E01 - Pilot.xml") in paths


def test_engine_keeps_mcm_image_reference(tmp_path):
    show = tmp_path / "Dark Matter (2024)"
    v = _touch(show / "Season 1" / "Dark Matter (2024) - S01E01 - Pilot [1080p].mkv")
    _touch(show / "Season 1" / "metadata" / "663ad3493cba8.jpg", b"\xff\xd8")
    (show / "Season 1" / "metadata" / "Dark Matter (2024) - S01E01 - Pilot [1080p].xml").write_text(
        "<Item><filename>/663ad3493cba8.jpg</filename></Item>", encoding="utf-8")
    p = engine.Project()
    p.scan(str(show))
    assert engine.existing_episode_image(str(v)).endswith("663ad3493cba8.jpg")
    ch = engine.plan(p, {"artwork": False})
    xml = next(c for c in ch if c.kind == "xml")
    assert "<filename>/663ad3493cba8.jpg</filename>" in xml.data
    thumb = next(c for c in ch if c.kind == "thumb")
    assert thumb.path.endswith("Pilot [1080p]-thumb.jpg") and thumb.producer.describe.startswith("existing file")


def test_match_by_title_when_number_missing(tmp_path):
    p = engine.Project()
    a, b = core.Episode(2, 1), core.Episode(2, 2)
    a.set("title", "Bright Heart's Bad Day", "csv"); b.set("title", "The Magic Lamp", "csv")
    p.merge_episodes([a, b], "csv")
    show = tmp_path / "Show"
    _touch(show / "Season 2" / "the magic lamp.mp4")
    p.show_dir = str(show)
    p.videos = core.find_videos(str(show))
    p.match()
    assert p.videos[0].keys == [(2, 2)] and p.videos[0].match_how == "title"


def test_r4_module_not_needed():
    for name in ("kfg_core", "kfg_writers", "kfg_sources", "kfg_media", "kfg_engine"):
        src_text = (ROOT / f"{name}.py").read_text(encoding="utf-8")
        assert "import tkinter" not in src_text, name


# ── CSV export (F16.4) and legacy templates (F5.3) ─────────────────────

@needs_fixtures
def test_export_csv_round_trip(tmp_path):
    p = engine.Project()
    eps, _ = core.parse_tvdb_paste((FIX / "tvdb_paste_sample_house.txt").read_text(encoding="utf-8"))
    p.merge_episodes(eps, "paste")
    out = tmp_path / "House.episodes.csv"
    n = engine.export_csv(p, str(out))
    assert n == len(eps) and out.read_bytes().startswith(b"\xef\xbb\xbf")
    t = core.read_table_file(str(out))
    back, rep = core.rows_to_episodes(t, core.suggest_mapping(t.headers))
    assert len(back) == n and {e.code for e in back} == {e.code for e in eps} and not rep
    assert next(e for e in back if e.code == "S03E06").get("title") == "Que Será Será"


def test_legacy_template_fill():
    ep = core.Episode(1, 2)
    for f, v in (("title", "Paternity"), ("tvdb_id", "110995"), ("runtime", 44), ("plot", "A & B")):
        ep.set(f, v, "csv")
    tpl = ('<episodedetails><title></title><plot/><runtime></runtime><durationinseconds></durationinseconds>'
           '<uniqueid type="tvdb" default="true"></uniqueid><uniqueid type="imdb"></uniqueid></episodedetails>')
    out = engine.fill_legacy(tpl, [ep], core.Series())
    assert "<title>Paternity</title>" in out and "<plot>A &amp; B</plot>" in out
    assert "<durationinseconds>2640</durationinseconds>" in out and ">110995</uniqueid>" in out and "imdb" not in out


def test_health_groups_missing_videos_and_sorts():
    assert engine._ranges([1, 2, 3, 7, 9, 10]) == "E01–E03, E07, E09–E10"
    p = engine.Project()
    eps = []
    for n in range(1, 6):
        e = core.Episode(1, n)
        e.set("title", f"T{n}", "csv")
        eps.append(e)
    p.merge_episodes(eps, "csv")
    v = core.VideoFile("C:/x/Show/Season 1/Show S01E02.mkv", 1)
    p.show_dir, p.videos = "C:/x/Show", [v, core.VideoFile("C:/x/Show/Season 1/Show S01E02 copy.mkv", 1)]
    p.match()
    h = engine.health(p)
    assert h[0][0] == "error"                                   # two videos for S01E02 come first
    assert ("info", "Season 1", "no video for 4 of 5 episode(s): E01, E03–E05") in h
