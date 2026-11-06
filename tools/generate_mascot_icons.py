"""Draws the StockCapy mascot as the web app's PWA icons (home-screen, maskable, apple-touch).

Run with Pillow installed: `uv pip install pillow && python tools/generate_mascot_icons.py`
The shapes mirror assets/capy-logo.svg — keep the two in step if either changes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

FUR = (169, 123, 82, 255)
FUR_DARK = (138, 98, 68, 255)
INK = (43, 29, 18, 255)
GOLD = (247, 189, 52, 255)
GOLD_DARK = (214, 144, 22, 255)
ARROW = (120, 74, 10, 255)
CANVAS = 1024
SUPERSAMPLE = 4

PWA_ICONS = Path("src/portfolio/web/static/icons")
# Background gradient built around the app's theme colour (#1462b4) so icon and UI feel like one brand.
BG_TOP = (46, 128, 214)
BG_BOTTOM = (14, 78, 150)
# Android crops maskable icons to a circle; the safe zone is the central 80%.
MASKABLE_CONTENT_RATIO = 0.80


def _scaled(box: tuple[float, float, float, float], factor: float) -> list[float]:
    return [value * factor for value in box]


def _draw_head(draw: ImageDraw.ImageDraw, factor: float, with_coin: bool) -> None:
    """A capybara head is a blunt brick: wide, low, eyes set high and far apart."""
    unit = CANVAS / 64 * factor
    dy = 2.5  # centre the whole mascot (coin included) vertically
    box = lambda x0, y0, x1, y1: _scaled((x0, y0 + dy, x1, y1 + dy), unit)  # noqa: E731
    draw.rounded_rectangle(box(9, 17, 21, 27), radius=5 * unit, fill=FUR_DARK)
    draw.rounded_rectangle(box(43, 17, 55, 27), radius=5 * unit, fill=FUR_DARK)
    draw.rounded_rectangle(box(5, 21, 59, 57), radius=12 * unit, fill=FUR)
    draw.rounded_rectangle(box(17, 37, 47, 57), radius=10 * unit, fill=FUR_DARK)
    eye = 9 if not with_coin else 8  # slightly larger eyes when there is no coin (small sizes)
    draw.ellipse(box(19 - eye / 2, 31 - eye / 2, 19 + eye / 2, 31 + eye / 2), fill=INK)
    draw.ellipse(box(45 - eye / 2, 31 - eye / 2, 45 + eye / 2, 31 + eye / 2), fill=INK)
    if with_coin:
        draw.ellipse(box(26, 45, 31, 49), fill=INK)
        draw.ellipse(box(33, 45, 38, 49), fill=INK)
        # Gold coin with an upward arrow: "price going up" at a glance.
        draw.ellipse(box(23, 1, 41, 19), fill=GOLD_DARK)
        draw.ellipse(box(24.5, 2.5, 39.5, 17.5), fill=GOLD)
        draw.polygon([(x * unit, (y + dy) * unit) for x, y in
                      [(32, 5), (36.5, 10), (33.5, 10), (33.5, 14.5), (30.5, 14.5), (30.5, 10), (27.5, 10)]],
                     fill=ARROW)


def _gradient(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for row in range(size):
        t = row / max(1, size - 1)
        color = tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,)
        draw.line([(0, row), (size, row)], fill=color)
    return image


def render(size: int, background: bool, with_coin: bool = True) -> Image.Image:
    """Supersamples then downscales, because rounded corners alias badly otherwise."""
    big = size * SUPERSAMPLE
    image = _gradient(big) if background else Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_head(draw, big / CANVAS, with_coin)
    return image.resize((size, size), Image.LANCZOS)


def _on_blue(size: int) -> Image.Image:
    return render(size, True).convert("RGB")


def _maskable(size: int) -> Image.Image:
    canvas = _gradient(size)
    content = int(size * MASKABLE_CONTENT_RATIO)
    offset = (size - content) // 2
    canvas.alpha_composite(render(content, False), (offset, offset))
    return canvas.convert("RGB")


def main(output_dir: Path = PWA_ICONS) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {
        "icon-192.png": _on_blue(192),
        "icon-512.png": _on_blue(512),
        "icon-maskable-512.png": _maskable(512),
        "apple-touch-icon.png": _on_blue(180),
    }
    for name, image in written.items():
        image.save(output_dir / name)
        print(f"wrote {output_dir / name} ({image.size[0]}x{image.size[1]})")
    # Tiny sizes drop the coin so the silhouette stays readable.
    favicon = render(64, True, with_coin=False).convert("RGBA")
    favicon.save(output_dir.parent / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote {output_dir.parent / 'favicon.ico'}")


if __name__ == "__main__":
    main()
