"""Media tests with tiny synthetic videos made by FFmpeg at test time (skipped when FFmpeg is missing)."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kfg_core as core              # noqa: E402
import kfg_engine as engine          # noqa: E402
import kfg_media as media            # noqa: E402

FFMPEG, FFPROBE = media.find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="FFmpeg not found")
PIL = pytest.importorskip("PIL.Image")


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("FFmpeg not found")
    d = tmp_path_factory.mktemp("vids")
    show = d / "Test Show (2020)" / "Season 1"
    show.mkdir(parents=True)
    wide = media.make_test_video(FFMPEG, str(show / "Test Show - S01E01 - Wide.mkv"), 20, "640x360", 3)
    old = media.make_test_video(FFMPEG, str(show / "Test Show - S01E02 - Old.mkv"), 20, "480x360", 3)
    return d / "Test Show (2020)", wide, old


@needs_ffmpeg
def test_probe_summary(videos):
    _, wide, _ = videos
    p = media.probe(FFPROBE, wide)
    assert 19 <= p["duration_s"] <= 21 and p["video"]["width"] == 640 and p["video"]["codec"] == "h264"
    assert p["audio"] and p["audio"][0]["codec_name_upper"] == "AAC" and p["video"]["quality"] == "SD"


@needs_ffmpeg
def test_best_frame_is_never_black(videos):
    _, wide, _ = videos
    img, cands = media.best_frame(FFMPEG, wide, 20, keep_all=True)
    assert len(cands) == 5 and media.score_frame(img) > 0
    from PIL import ImageStat
    assert ImageStat.Stat(img.convert("L")).mean[0] > 30


def test_score_rejects_black_and_flat():
    from PIL import Image
    assert media.score_frame(Image.new("RGB", (640, 360), (0, 0, 0))) == 0
    assert media.score_frame(Image.new("RGB", (640, 360), (128, 128, 128))) == 0


def test_letterbox_cropped():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (640, 360), (0, 0, 0))
    ImageDraw.Draw(im).rectangle([0, 60, 639, 299], fill=(200, 120, 40))
    out = media.crop_letterbox(im)
    assert out.size[1] < 300 and out.size[0] == 640


def test_make_thumb_keeps_aspect_or_pads():
    from PIL import Image
    im = Image.new("RGB", (1920, 1440), (90, 90, 90))
    assert media.make_thumb(im).size == (1280, 960)
    assert media.make_thumb(im, pad_16_9=True).size == (1280, 720)
    assert media.make_thumb(Image.new("RGB", (640, 360))).size == (640, 360)        # never upscaled


@pytest.mark.parametrize("size, mode, kind", [((1000, 1500), "RGB", "poster"), ((1920, 1080), "RGB", "fanart"),
                                              ((758, 140), "RGB", "banner"), ((800, 310), "RGBA", "clearlogo"),
                                              ((1000, 562), "RGBA", "clearart"), ((800, 450), "RGB", "landscape")])
def test_classify_artwork(size, mode, kind):
    from PIL import Image
    im = Image.new(mode, size, (10, 20, 30, 0) if mode == "RGBA" else (10, 20, 30))
    assert media.classify_artwork(im) == kind


@needs_ffmpeg
def test_generate_images_for_every_server(videos):
    show, wide, old = videos
    p = engine.Project()
    p.scan(str(show), FFPROBE)
    assert p.episodes[(1, 1)].get("runtime") is None          # a 20-second clip: no runtime of "0 min"
    assert all(v.probe and v.probe["duration_s"] for v in p.videos)
    ch = engine.plan(p, {"artwork": False}, ffmpeg=FFMPEG)
    st = engine.apply(ch, str(show), log=lambda *a: None)
    assert st["failed"] == 0, st["errors"]
    s1 = show / "Season 1"
    for stem, size in (("Test Show - S01E01 - Wide", (640, 360)), ("Test Show - S01E02 - Old", (480, 360))):
        thumb, meta = s1 / f"{stem}-thumb.jpg", s1 / "metadata" / f"{stem}.jpg"
        assert thumb.is_file() and meta.is_file()
        from PIL import Image
        assert Image.open(thumb).size == size                                    # 4:3 stays 4:3
        xml = (s1 / "metadata" / f"{stem}.xml").read_text(encoding="utf-8")
        assert f"<filename>/{stem}.jpg</filename>" in xml and "<VideoLengthSeconds>" in xml
        nfo = (s1 / f"{stem}.nfo").read_text(encoding="utf-8-sig")
        assert "<durationinseconds>" in nfo and "<codec>h264</codec>" in nfo
    before = (s1 / "Test Show - S01E01 - Wide-thumb.jpg").read_bytes()
    again = engine.plan(p, {"artwork": False}, ffmpeg=FFMPEG)
    assert all(c.action in ("same", "keep") for c in again)                       # images kept, text unchanged
    assert (s1 / "Test Show - S01E01 - Wide-thumb.jpg").read_bytes() == before


def test_find_ffmpeg_never_raises():
    assert isinstance(media.find_ffmpeg("Z:/nowhere/ffmpeg.exe"), tuple)
    assert core.VIDEO_EXTS and engine.PROFILES
