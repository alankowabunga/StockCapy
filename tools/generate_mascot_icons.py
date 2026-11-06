"""Builds the web app's PWA icons and favicon from assets/app-icon-source.jpg.

Run with Pillow installed: `uv pip install pillow && python tools/generate_mascot_icons.py`
The source is a 2048x2048 artwork of the mascot inside a gold rounded frame. Each output is a
different crop of it, so replacing the source file and re-running regenerates everything.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SOURCE = Path("assets/app-icon-source.jpg")
STATIC = Path("src/portfolio/web/static")
PWA_ICONS = STATIC / "icons"
ASSETS = SOURCE.parent

# The gold frame of the artwork, as a square (left, top, right, bottom) in source pixels.
FRAME = (160, 165, 1890, 1895)
# Corner radius of that frame, as a fraction of its width.
FRAME_RADIUS = 0.2
# The mascot's face, for the favicon: only the head stays readable at 16-48px.
FACE = (380, 360, 1380, 1360)
# Android crops maskable icons to a circle (safe zone: radius 40% of the icon). The artwork's own
# background is a smooth gradient, so shrinking it slightly and letting the full-size copy show
# around it extends the background without a seam. The coin stack on top is the tightest element.
MASKABLE_CONTENT_RATIO = 0.92


def _rounded(image: Image.Image) -> Image.Image:
    """Cuts the corners to the frame's shape so the icon has no background slivers."""
    scale = 4
    size = image.width
    mask = Image.new("L", (size * scale, size * scale), 0)
    radius = int(size * scale * FRAME_RADIUS)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * scale - 1, size * scale - 1), radius=radius, fill=255
    )
    rgba = image.convert("RGBA")
    rgba.putalpha(mask.resize((size, size), Image.LANCZOS))
    return rgba


def _framed(source: Image.Image, size: int) -> Image.Image:
    return _rounded(source.crop(FRAME).resize((size, size), Image.LANCZOS))


def _opaque(source: Image.Image, size: int) -> Image.Image:
    """apple-touch-icon must be opaque; iOS applies its own corner mask."""
    return source.crop(FRAME).resize((size, size), Image.LANCZOS).convert("RGB")


def _maskable(source: Image.Image, size: int) -> Image.Image:
    backdrop = source.resize((size, size), Image.LANCZOS)
    content = int(size * MASKABLE_CONTENT_RATIO)
    offset = (size - content) // 2
    backdrop.paste(source.resize((content, content), Image.LANCZOS), (offset, offset))
    return backdrop.convert("RGB")


def main(output_dir: Path = PWA_ICONS) -> None:
    source = Image.open(SOURCE).convert("RGB")
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {
        "icon-192.png": _framed(source, 192),
        "icon-512.png": _framed(source, 512),
        "icon-maskable-512.png": _maskable(source, 512),
        "apple-touch-icon.png": _opaque(source, 180),
        "favicon-48.png": source.crop(FACE).resize((48, 48), Image.LANCZOS),
    }
    for name, image in written.items():
        image.save(output_dir / name)
        print(f"wrote {output_dir / name} ({image.size[0]}x{image.size[1]})")
    # Brand images for the README and docs: the framed artwork, and a small face-only mark.
    brand = {
        "capy-logo.png": _framed(source, 512),
        "capy-mark.png": source.crop(FACE).resize((128, 128), Image.LANCZOS),
    }
    for name, image in brand.items():
        image.save(ASSETS / name)
        print(f"wrote {ASSETS / name} ({image.size[0]}x{image.size[1]})")
    favicon = source.crop(FACE).resize((64, 64), Image.LANCZOS)
    favicon.save(output_dir.parent / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote {output_dir.parent / 'favicon.ico'}")


if __name__ == "__main__":
    main()
