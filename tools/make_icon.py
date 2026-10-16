"""Build the Kodi Files Generator icons (needs Pillow; build only).

    python tools/make_icon.py   → assets/icon.ico (16–256, dark tile), assets/icon-*.png,
                                  assets/icon-small.ico (16–32, transparent, for the title bar)

Motif: a TV with a document (the generated files) on its screen. Catppuccin Mocha colours, like the app.
"""
from pathlib import Path

from PIL import Image, ImageDraw

BASE, BLUE, MAUVE, TEXT, YELLOW = (30, 30, 46, 255), (137, 180, 250, 255), (203, 166, 247, 255), \
    (205, 214, 244, 255), (249, 226, 175, 255)
EDGE = (64, 96, 170, 255)
OUT = Path(__file__).resolve().parents[1] / "assets"


def _tv(d, u, x0, y0, small):
    """TV body, screen, stand and a document with lines; u = size of one unit of a 64-unit grid."""
    lw = max(1, round((5 if small else 4) * u))
    d.rounded_rectangle([x0 + 6 * u, y0 + 14 * u, x0 + 58 * u, y0 + 50 * u], radius=6 * u, fill=BLUE)
    d.rounded_rectangle([x0 + 6 * u + lw, y0 + 14 * u + lw, x0 + 58 * u - lw, y0 + 50 * u - lw],
                        radius=max(1, 6 * u - lw), fill=BASE)
    d.line([x0 + 22 * u, y0 + 4 * u, x0 + 32 * u, y0 + 14 * u], fill=MAUVE, width=max(1, round(3 * u)))
    d.line([x0 + 42 * u, y0 + 4 * u, x0 + 32 * u, y0 + 14 * u], fill=MAUVE, width=max(1, round(3 * u)))
    d.rectangle([x0 + 24 * u, y0 + 50 * u, x0 + 40 * u, y0 + 55 * u], fill=BLUE)
    # document on the screen
    d.rectangle([x0 + 22 * u, y0 + 20 * u, x0 + 42 * u, y0 + 44 * u], fill=TEXT)
    bar = max(1, round(3 * u))
    for i, (w, col) in enumerate(((16, YELLOW), (12, BASE), (14, BASE))):
        y = y0 + (25 + i * 6) * u
        d.rectangle([x0 + 24 * u, y, x0 + (24 + w) * u, y + bar - 1], fill=col)


def draw(size: int) -> Image.Image:
    k = 1 if size <= 32 else 4
    S = size * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = S / 64
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=round(12 * u), fill=(24, 24, 37, 255))
    _tv(d, u, 0, 2 * u, size <= 32)
    return img if k == 1 else img.resize((size, size), Image.LANCZOS)


def draw_small(size: int) -> Image.Image:
    """Transparent: the TV alone with a darker edge, so it shows on light and dark title bars."""
    k = 4
    S = size * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = S / 64
    d.rounded_rectangle([3 * u, 11 * u, 61 * u, 53 * u], radius=8 * u, fill=EDGE)
    _tv(d, u * 1.08, -2.5 * u, -1.5 * u, True)
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    images = {s: draw(s) for s in sizes}
    images[256].save(OUT / "icon.ico", sizes=[(s, s) for s in sizes],
                     append_images=[images[s] for s in sizes if s != 256])
    for s in (16, 20, 32, 48, 64, 256):
        images[s].save(OUT / f"icon-{s}.png")
    small = {s: draw_small(s) for s in (16, 20, 24, 32)}
    small[32].save(OUT / "icon-small.ico", sizes=[(s, s) for s in small], append_images=[small[s] for s in (16, 20, 24)])
    print("written:", OUT)


if __name__ == "__main__":
    main()
