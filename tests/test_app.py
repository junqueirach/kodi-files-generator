"""Tests of the app module's non-window parts: version, update check, settings file, TLS context."""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tkinter")


def _load():
    path = sorted(ROOT.glob("kodi_generator*.py"))[-1]
    spec = importlib.util.spec_from_file_location("kfg_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kg = _load()


def test_version_and_identity():
    assert kg.APP_VERSION == "v0.5.0" and kg.APP_NAME == "Kodi Files Generator"
    assert kg.APP_REPO_URL == "https://github.com/junqueirach/kodi-files-generator"
    assert kg.RELEASES_API.endswith("/junqueirach/kodi-files-generator/releases/latest")


def test_versions_compare_as_numbers():
    assert kg.is_newer("v0.10.0", "v0.5.0") and kg.is_newer("0.5.1", "v0.5.0")
    assert not kg.is_newer("v0.5.0", "v0.5.0") and not kg.is_newer("v0.4.0", "v0.5.0")
    assert not kg.is_newer("latest", "v0.5.0") and kg.parse_version(None) == ()


@pytest.mark.parametrize("status, body, expected", [
    (200, {"tag_name": "v0.6.0", "html_url": "u"}, "newer"), (200, {"tag_name": "v0.4.0", "html_url": "u"}, "latest"),
    (404, {}, "none"), (503, {}, "error"), (200, {"tag_name": "nightly"}, "error")])
def test_update_check_answers(status, body, expected):
    assert kg.check_latest_release(lambda url: (status, json.dumps(body).encode()))["status"] == expected


def test_update_check_never_raises():
    def boom(url):
        raise OSError("offline")
    r = kg.check_latest_release(boom)
    assert r["status"] == "error" and "offline" in r["error"]


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(kg, "CONFIG_PATH", tmp_path / "s.json")
    s = kg.load_settings()
    assert s["profiles"] == ["kodi", "jellyfin", "emby", "xml"] and s["check_updates"] is False
    s["language"] = "en"
    kg.save_settings(s)
    assert kg.load_settings()["language"] == "en"
    (tmp_path / "s.json").write_text("{broken", encoding="utf-8")
    assert kg.load_settings()["language"] == "pt-BR" and list(tmp_path.glob("s.json.corrupt-*"))


def test_tls_context_exists():
    import kfg_sources
    assert kfg_sources.ssl_context() is kfg_sources.ssl_context()


def test_help_names_every_tab_and_setting():
    for t in ("Episodes", "Sources", "Artwork", "Changes", "Check", "Log", "Paste from clipboard", "New CSV from a link",
              "Undo last run", "Rename videos", "TMDb", "OMDb", "TVmaze", "-thumb.jpg", "metadata\\", "Settings",
              "General", "Files", "displayseason"):
        assert t.lower() in (kg.HELP_TEXT + kg.FORMATS_TEXT).lower(), t
