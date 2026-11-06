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
ORANGE = (232, 136, 58, 255)
STEM = (63, 125, 58, 255)
CANVAS = 1024
SUPERSAMPLE = 4

PWA_ICONS = Path("src/portfolio/web/static/icons")
SKY = (230, 244, 254, 255)
# Android crops maskable icons to a circle; keep the mascot inside the central safe zone.
MASKABLE_CONTENT_RATIO = 0.66


def _scaled(box: tuple[float, float, float, float], factor: float) -> list[float]:
    return [value * factor for value in box]


def _draw_head(draw: ImageDraw.ImageDraw, factor: float, with_orange: bool) -> None:
    """A capybara head is a blunt brick: wide, low, eyes set high and far apart."""
    unit = CANVAS / 64 * factor
    draw.rounded_rectangle(_scaled((9, 17, 21, 27), unit), radius=5 * unit, fill=FUR_DARK)
    draw.rounded_rectangle(_scaled((43, 17, 55, 27), unit), radius=5 * unit, fill=FUR_DARK)
    draw.rounded_rectangle(_scaled((5, 21, 59, 57), unit), radius=12 * unit, fill=FUR)
    draw.rounded_rectangle(_scaled((17, 37, 47, 57), unit), radius=10 * unit, fill=FUR_DARK)
    draw.ellipse(_scaled((15, 27, 23, 35), unit), fill=INK)
    draw.ellipse(_scaled((41, 27, 49, 35), unit), fill=INK)
    draw.ellipse(_scaled((26, 45, 31, 49), unit), fill=INK)
    draw.ellipse(_scaled((33, 45, 38, 49), unit), fill=INK)
    if with_orange:
        draw.ellipse(_scaled((26, 5, 38, 17), unit), fill=ORANGE)
        draw.line(_scaled((32, 6, 32, 2.5), unit), fill=STEM, width=max(1, int(2.5 * unit)))


def render(size: int, background: tuple | None, with_orange: bool = True) -> Image.Image:
    """Supersamples then downscales, because rounded corners alias badly otherwise."""
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_head(draw, big / CANVAS, with_orange)
    return image.resize((size, size), Image.LANCZOS)


def _on_sky(size: int) -> Image.Image:
    return render(size, SKY).convert("RGB")


def _maskable(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), SKY)
    content = int(size * MASKABLE_CONTENT_RATIO)
    offset = (size - content) // 2
    canvas.alpha_composite(render(content, None), (offset, offset))
    return canvas.convert("RGB")


def main(output_dir: Path = PWA_ICONS) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {
        "icon-192.png": _on_sky(192),
        "icon-512.png": _on_sky(512),
        "icon-maskable-512.png": _maskable(512),
        "apple-touch-icon.png": _on_sky(180),
    }
    for name, image in written.items():
        image.save(output_dir / name)
        print(f"wrote {output_dir / name} ({image.size[0]}x{image.size[1]})")


if __name__ == "__main__":
    main()
