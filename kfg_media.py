"""Kodi Files Generator — media (no Tk): FFmpeg / ffprobe, technical info, frame grab with best-frame choice,
episode thumbnails, artwork classification. FFmpeg is optional: without it the app still writes every text file.
"""
import io
import json
import os
import re
import shutil
import subprocess

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0            # CREATE_NO_WINDOW
_LOW_PRIORITY = 0x00004000 if os.name == "nt" else 0         # BELOW_NORMAL_PRIORITY_CLASS
FFMPEG_DIRS = [r"C:\FFmpeg\bin", r"C:\Program Files\ffmpeg\bin", r"C:\ffmpeg\bin"]


def find_ffmpeg(setting_path: str = "") -> tuple:
    """(ffmpeg, ffprobe) full paths or ('', ''). Order: the Settings path (file or folder), PATH, common folders."""
    exe = ".exe" if os.name == "nt" else ""
    cands = []
    if setting_path:
        p = setting_path
        cands.append(os.path.dirname(p) if os.path.isfile(p) else p)
    for name in ("ffmpeg",):
        w = shutil.which(name)
        if w:
            cands.append(os.path.dirname(w))
    cands += FFMPEG_DIRS
    for d in cands:
        f, p = os.path.join(d, "ffmpeg" + exe), os.path.join(d, "ffprobe" + exe)
        if os.path.isfile(f) and os.path.isfile(p):
            return f, p
    return "", ""


def _run(cmd, timeout=60, data=False):
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW | _LOW_PRIORITY)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["ffmpeg failed"])
    return r.stdout if data else r.stdout.decode("utf-8", "replace")


_CODEC_KODI = {"h264": "h264", "hevc": "hevc", "mpeg4": "mpeg4", "mpeg2video": "mpeg2video", "vp9": "vp9", "av1": "av1",
               "msmpeg4v3": "divx", "wmv3": "wmv3", "vc1": "vc1"}
_CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}
SUB_LANG_RE = r"\.([a-z]{2,3}(?:-[A-Za-z]{2})?)(?:\.(?:forced|sdh|cc|default))*\.(?:srt|ass|ssa|sub|vtt|sup)$"


def probe(ffprobe: str, path: str, timeout=60) -> dict:
    """A summary used by both writers (Kodi streamdetails and the XML MediaInfo block)."""
    out = _run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path], timeout)
    j = json.loads(out)
    fmt = j.get("format") or {}
    res = {"streams": True, "duration_s": None, "size": None, "video": {}, "audio": [], "subtitles": []}
    try:
        res["duration_s"] = round(float(fmt.get("duration")))
    except (TypeError, ValueError):
        pass
    try:
        res["size"] = int(fmt.get("size")) if fmt.get("size") else os.path.getsize(path)
    except (OSError, ValueError):
        pass
    for s in j.get("streams") or []:
        kind = s.get("codec_type")
        tags = s.get("tags") or {}
        lang = (tags.get("language") or "").lower()
        if kind == "video" and not res["video"] and (s.get("disposition") or {}).get("attached_pic") != 1:
            w, h = s.get("width") or 0, s.get("height") or 0
            dar = s.get("display_aspect_ratio") or ""
            try:
                aspect = round(int(dar.split(":")[0]) / int(dar.split(":")[1]), 2) if ":" in dar and \
                    dar.split(":")[1] != "0" else (round(w / h, 2) if h else None)
            except (ValueError, ZeroDivisionError):
                aspect = round(w / h, 2) if h else None
            try:
                num, den = (s.get("avg_frame_rate") or "0/1").split("/")
                fps = round(int(num) / int(den), 3) if int(den) else None
            except ValueError:
                fps = None
            field = s.get("field_order") or ""
            transfer = s.get("color_transfer") or ""
            res["video"] = {
                "codec": _CODEC_KODI.get(s.get("codec_name"), s.get("codec_name") or ""),
                "codec_name_upper": (s.get("codec_name") or "").upper(),
                "width": w, "height": h, "aspect": aspect,
                "aspect_ratio": f"{aspect:.2f}:1" if aspect else "",
                "fps": fps, "bitrate": int(s["bit_rate"]) if str(s.get("bit_rate", "")).isdigit() else None,
                "scantype": "interlaced" if field in ("tt", "bb", "tb", "bt") else "progressive",
                "hdr": "hdr10" if transfer == "smpte2084" else ("hlg" if transfer == "arib-std-b67" else ""),
                "quality": "2160p" if h >= 1600 or w >= 3200 else "1080p" if h >= 800 or w >= 1700 else
                           "720p" if h >= 600 or w >= 1200 else "SD",
            }
        elif kind == "audio":
            ch = s.get("channels") or 0
            res["audio"].append({"codec": (s.get("codec_name") or "").replace("eac3", "eac3"),
                                 "codec_name_upper": (s.get("codec_name") or "").upper(), "language": lang,
                                 "channels": ch, "channels_label": _CHANNELS.get(ch, str(ch)),
                                 "bitrate": int(s["bit_rate"]) if str(s.get("bit_rate", "")).isdigit() else None,
                                 "sample_rate": s.get("sample_rate")})
        elif kind == "subtitle":
            res["subtitles"].append({"language": lang})
    # sidecar subtitles: Movie.en.srt, Movie.pt-BR.forced.srt
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    try:
        for f in os.listdir(folder):
            if f.startswith(stem + ".") and f != os.path.basename(path):
                m = re.search(SUB_LANG_RE, f[len(stem):], re.I)
                if m:
                    res["subtitles"].append({"language": m.group(1).lower(), "external": True})
    except OSError:
        pass
    return res


# ═══════════════════════════════════════════════════════════════════════
# FRAME GRAB AND THE BEST FRAME (F7.2)
# ═══════════════════════════════════════════════════════════════════════

FRAME_POINTS = (0.12, 0.25, 0.40, 0.55, 0.70)


def grab_frame(ffmpeg: str, video: str, at_s: float, timeout=30):
    from PIL import Image
    data = _run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, at_s):.2f}", "-i", video, "-frames:v", "1",
                 "-vf", "scale='min(1920,iw)':-2", "-q:v", "2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                timeout, data=True)
    if not data:
        raise RuntimeError("no frame")
    return Image.open(io.BytesIO(data)).convert("RGB")


def frame_points(duration_s: float, points=FRAME_POINTS) -> list:
    """Times to try: skip the first 90 s (intros) and the last 5 % (credits) when the video is long enough."""
    d = float(duration_s or 0)
    if d <= 0:
        return [5.0]
    start = 90.0 if d > 600 else d * 0.05
    end = d * 0.95
    span = max(1.0, end - start)
    return [round(start + p * span, 2) for p in points]


def score_frame(img) -> float:
    """Higher is better. Near-black, near-white and flat frames score 0; sharp, mid-bright frames win."""
    from PIL import ImageFilter, ImageStat
    g = img.convert("L").resize((320, max(1, int(320 * img.height / max(1, img.width)))))
    st = ImageStat.Stat(g)
    mean, sd = st.mean[0], st.stddev[0]
    if mean < 28 or mean > 230 or sd < 18:
        return 0.0
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    brightness = 1.0 - abs(mean - 120) / 160
    return round(edges * (sd / 64) * max(0.2, brightness), 4)


def crop_letterbox(img, threshold=20):
    """Remove black bars (top/bottom, left/right) when they are clearly bars (< 30 % of the frame each side)."""
    g = img.convert("L")
    w, h = g.size
    small = g.resize((160, max(1, int(160 * h / w))))
    sw, sh = small.size
    px = small.load()
    row_dark = [sum(px[x, y] for x in range(sw)) / sw < threshold for y in range(sh)]
    col_dark = [sum(px[x, y] for y in range(sh)) / sh < threshold for x in range(sw)]

    def run(flags):
        n = 0
        for f in flags:
            if not f:
                break
            n += 1
        return n
    top, bottom = run(row_dark), run(reversed(row_dark))
    left, right = run(col_dark), run(reversed(col_dark))
    if top + bottom >= sh * 0.6 or left + right >= sw * 0.6:
        return img
    box = (int(left * w / sw), int(top * h / sh), w - int(right * w / sw), h - int(bottom * h / sh))
    if box == (0, 0, w, h):
        return img
    return img.crop(box)


def best_frame(ffmpeg: str, video: str, duration_s: float, cancel=None, keep_all=False):
    """(best image, [(time, score, image)]) — the candidates are kept for the episode editor when keep_all."""
    cands = []
    for t in frame_points(duration_s):
        if cancel and cancel():
            break
        try:
            img = crop_letterbox(grab_frame(ffmpeg, video, t))
        except Exception:
            continue
        cands.append((t, score_frame(img), img))
    if not cands:
        raise RuntimeError("no frame could be read from the video")
    best = max(cands, key=lambda c: c[1])
    return best[2], (cands if keep_all else [])


def make_thumb(img, width=1280, pad_16_9=False):
    """JPEG-ready RGB image: width 1280 (never upscaled), source aspect kept unless pad_16_9 (F7.3)."""
    from PIL import Image
    img = img.convert("RGB")
    if pad_16_9 and img.width / img.height < 16 / 9 - 0.01:
        nw = int(round(img.height * 16 / 9))
        canvas = Image.new("RGB", (nw, img.height), (0, 0, 0))
        canvas.paste(img, ((nw - img.width) // 2, 0))
        img = canvas
    if img.width > width:
        img = img.resize((width, int(round(img.height * width / img.width))), Image.LANCZOS)
    return img


def jpeg_bytes(img, quality=90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def image_from_bytes(data: bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def has_alpha(img) -> bool:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        a = img.convert("RGBA").getchannel("A")
        return a.getextrema()[0] < 250
    return False


ART_KINDS = ["poster", "fanart", "banner", "clearlogo", "clearart", "landscape", "season_poster"]
MIN_SIZES = {"poster": (680, 1000), "fanart": (1280, 720), "banner": (758, 140), "clearlogo": (400, 155),
             "clearart": (500, 281), "landscape": (500, 281), "season_poster": (400, 578)}


def classify_artwork(img) -> str:
    """poster ≈ 2:3, fanart ≈ 16:9 ≥ 1280, landscape ≈ 16:9 smaller, banner ≈ 5.4:1, transparent wide → clearlogo,
    transparent ≈ 16:9 → clearart; '' when unsure (the user chooses)."""
    w, h = img.size
    if not w or not h:
        return ""
    r = w / h
    if has_alpha(img):
        if r >= 2.3:
            return "clearlogo"
        if 1.3 <= r < 2.3:
            return "clearart"
        return ""
    if 4.3 <= r <= 6.8:
        return "banner"
    if 0.6 <= r <= 0.76:
        return "poster"
    if 1.6 <= r <= 1.95:
        return "fanart" if w >= 1280 else "landscape"
    return ""


def too_small(kind: str, size) -> bool:
    mw, mh = MIN_SIZES.get(kind, (0, 0))
    return size[0] < mw or size[1] < mh


def make_test_video(ffmpeg: str, path: str, seconds=20, size="640x360", black_first=3):
    """Synthetic video for tests: black for the first seconds, then a moving test pattern."""
    vf = f"testsrc=size={size}:rate=25,drawbox=enable='lt(t,{black_first})':x=0:y=0:w=iw:h=ih:color=black:t=fill"
    _run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-f", "lavfi", "-i", "sine=frequency=440",
          "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-shortest", path], timeout=120)
    return path
