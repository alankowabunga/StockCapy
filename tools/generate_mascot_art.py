"""Builds the mascot artwork used around the web app and docs from the source images in assets/.

Run with Pillow and numpy installed: `uv pip install pillow numpy && python tools/generate_mascot_art.py`

Sources (all with a white background):
  assets/mascot-flat-source.jpg    flat head, for small spots (header, favicon, footer, avatar)
  assets/mascot-banner-source.jpg  wide 3D scene, for the README banner and the larger web art
Outputs go to assets/ (docs, avatar) and src/portfolio/web/static/ (favicon, header logo, art/).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path("assets")
STATIC = Path("src/portfolio/web/static")
ART = STATIC / "art"
FLAT = ASSETS / "mascot-flat-source.jpg"
BANNER = ASSETS / "mascot-banner-source.jpg"

# Crops of the banner (left, top, right, bottom, in source pixels) -> one empty-state picture each.
BANNER_CROPS = {
    "login": ((100, 90, 1720, 1660), 640),  # whole scene, for the sign-in card
    "face": ((470, 180, 1250, 960), 240),  # the mascot's head, for "nothing here yet" states
    "coins": ((640, 1280, 1560, 1700), 460),  # coin stacks, for empty positions
    "chart": ((1200, 220, 1900, 840), 360),  # red arrow over candles, for empty charts
    "coin": ((1320, 1100, 1660, 1400), 200),  # one flying coin, for empty dividends
}


def _background_mask(image: Image.Image, tolerance: int = 14) -> np.ndarray:
    """True for near-white pixels connected to the border (the backdrop, not the white of an eye)."""
    pixels = np.asarray(image.convert("RGB")).astype(np.int16)
    near_white = (255 - pixels).max(axis=2) <= tolerance
    reach = np.zeros_like(near_white)
    reach[0, :] = reach[-1, :] = near_white[0, :] | near_white[-1, :]
    reach[:, 0] = reach[:, -1] = near_white[:, 0] | near_white[:, -1]
    reach &= near_white
    while True:
        before = reach.sum()
        for axis, flip in ((0, False), (0, True), (1, False), (1, True)):
            view = np.flip(reach, axis) if flip else reach
            nw = np.flip(near_white, axis) if flip else near_white
            # Spread along the axis through every run of near-white pixels that already holds a seed.
            seeded = np.maximum.accumulate(view, axis=axis)
            blocked = np.cumsum(~nw, axis=axis)
            start = np.where(view, blocked, -1)
            run_id = np.maximum.accumulate(start, axis=axis)
            result = nw & (blocked == run_id) & (run_id >= 0) & seeded
            view |= result
        if reach.sum() == before:
            return reach


def cutout(image: Image.Image, tolerance: int = 14) -> Image.Image:
    """Makes the white backdrop transparent, with a softened edge so there is no white halo."""
    rgba = image.convert("RGBA")
    backdrop = _background_mask(image, tolerance)
    alpha = Image.fromarray(np.where(backdrop, 0, 255).astype(np.uint8))
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    rgba.putalpha(alpha)
    return rgba


def _square(image: Image.Image, padding: float = 0.06) -> Image.Image:
    box = image.getchannel("A").point(lambda value: 255 if value > 20 else 0).getbbox()
    image = image.crop(box)
    side = int(max(image.size) * (1 + 2 * padding))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def _rounded(image: Image.Image, radius_ratio: float = 0.12) -> Image.Image:
    scale = 4
    width, height = image.size
    mask = Image.new("L", (width * scale, height * scale), 0)
    radius = int(min(width, height) * scale * radius_ratio)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius=radius, fill=255
    )
    rgba = image.convert("RGBA")
    rgba.putalpha(mask.resize((width, height), Image.LANCZOS))
    return rgba


def _save(image: Image.Image, path: Path, **options) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, **options)
    print(f"wrote {path} ({image.size[0]}x{image.size[1]})")


def build_flat(output_assets: Path = ASSETS, output_static: Path = STATIC) -> None:
    head = _square(cutout(Image.open(FLAT)))
    _save(head.resize((96, 96), Image.LANCZOS), output_static / "icons" / "capy-mark.png")
    _save(head.resize((256, 256), Image.LANCZOS), output_assets / "capy-mark.png")
    _save(head.resize((48, 48), Image.LANCZOS), output_static / "icons" / "favicon-48.png")
    head.resize((64, 64), Image.LANCZOS).save(
        output_static / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    print(f"wrote {output_static / 'favicon.ico'}")
    # Chat apps crop avatars to a circle and dislike transparency: put the head on brand blue.
    size = 640
    avatar = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(avatar)
    for row in range(size):
        t = row / (size - 1)
        colour = tuple(round(a + (b - a) * t) for a, b in zip((74, 163, 242), (20, 98, 180)))
        draw.line([(0, row), (size, row)], fill=colour + (255,))
    inner = int(size * 0.8)
    avatar.alpha_composite(head.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2,) * 2)
    _save(avatar.convert("RGB"), output_assets / "capy-avatar.png")


def build_banner(output_assets: Path = ASSETS) -> None:
    source = Image.open(BANNER)
    banner = cutout(source.crop((90, 80, source.width, 1740)))
    # The scene fades to white on the right; fade the alpha too so it also works on dark pages.
    alpha = np.asarray(banner.getchannel("A")).astype(np.float32)
    columns = np.linspace(0.0, 1.0, banner.width, dtype=np.float32)
    ramp = np.clip((1.0 - columns) / 0.28, 0.0, 1.0)  # 1 until 72% of the width, then down to 0
    banner.putalpha(Image.fromarray((alpha * ramp[None, :]).astype(np.uint8)))
    width = 1400
    banner = banner.resize((width, round(banner.height * width / banner.width)), Image.LANCZOS)
    _save(banner, output_assets / "capy-banner.png", optimize=True)


def build_web_art(output_dir: Path = ART) -> None:
    source = Image.open(BANNER).convert("RGB")
    for name, (box, width) in BANNER_CROPS.items():
        piece = source.crop(box)
        piece = piece.resize((width, round(piece.height * width / piece.width)), Image.LANCZOS)
        _save(_rounded(piece), output_dir / f"{name}.webp", quality=86, method=6)


def main() -> None:
    build_flat()
    build_banner()
    build_web_art()


if __name__ == "__main__":
    main()
