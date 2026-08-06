#!/usr/bin/env python3
"""Generate assets/argus.ico (multi-resolution Windows icon) from the mascot.

Source is the official Clawd mascot sprite assets/img/happy.png. The image is
trimmed to its non-transparent bounding box, padded square, and written as a
single .ico containing the standard icon sizes. Commit the resulting .ico so
CI doesn't need Pillow.

Usage:
    python tools/make_ico.py
    python tools/make_ico.py --src assets/img/looking.png --out assets/argus.ico

Requires Pillow (pip install pillow).
"""
import argparse
from pathlib import Path

from PIL import Image

SIZES = [16, 24, 32, 48, 64, 128, 256]


def trim_to_content(img: Image.Image, alpha_threshold: int = 16) -> Image.Image:
    """Crop to the *dense* mascot, so it fills the icon.

    The source sprite has faint near-transparent stray pixels near the edges
    (alpha 1-8), so a plain getbbox() (any non-zero alpha) barely crops and
    leaves the mascot tiny and centered. Threshold the alpha first to find the
    bounding box of the actually-visible mascot.
    """
    img = img.convert("RGBA")
    mask = img.getchannel("A").point(lambda a: 255 if a > alpha_threshold else 0)
    bbox = mask.getbbox()
    return img.crop(bbox) if bbox else img


def pad_square(img: Image.Image, margin: float = 0.06) -> Image.Image:
    """Center the (already trimmed) image on a transparent square canvas with
    a small margin so it doesn't touch the icon edges."""
    w, h = img.size
    side = int(max(w, h) * (1 + margin * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return canvas


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Build assets/argus.ico from a PNG.")
    ap.add_argument("--src", default=str(repo / "assets" / "img" / "happy.png"))
    ap.add_argument("--out", default=str(repo / "assets" / "argus.ico"))
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        raise SystemExit(f"source image not found: {src}")

    img = pad_square(trim_to_content(Image.open(src)))
    # Render the largest size once with high-quality resampling; Pillow derives
    # the smaller frames from the sizes list.
    base = img.resize((256, 256), Image.LANCZOS)
    base.save(args.out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {args.out} ({', '.join(f'{s}x{s}' for s in SIZES)})")


if __name__ == "__main__":
    main()
