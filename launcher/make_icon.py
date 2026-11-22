"""Draws the app icon (the web app's mark: a speech bubble holding the hire bar) and builds icon.icns.

  python3 launcher/make_icon.py
"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 4                       # supersampling
N = 1024 * S
TEAL = (27, 110, 102, 255)  # #1b6e66, the app's one accent
GROUND = (246, 246, 244, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    # macOS grid: an 824 px tile centred on the 1024 canvas, with a soft drop shadow.
    pad, r = 100 * S, 185 * S
    tile = [pad, pad, N - pad, N - pad]
    shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([tile[0], tile[1] + 12 * S, tile[2], tile[3] + 12 * S], r,
                                             fill=(0, 0, 0, 70))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * S)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(tile, r, fill=GROUND, outline=(225, 225, 220, 255), width=3 * S)

    # The mark's 28-unit viewBox, scaled to fill most of the tile.
    u = 23 * S
    ox = N / 2 - 14 * u
    oy = N / 2 - 14.5 * u
    P = lambda x, y: (ox + x * u, oy + y * u)
    # bubble body: x 2..26, y 4..21, radius 4
    d.rounded_rectangle([*P(2, 4), *P(26, 21)], int(4 * u), fill=TEAL)
    # tail: from (13,21) to (8,25) to (8,21)
    d.polygon([P(13, 20.5), P(8, 25), P(8, 20.5)], fill=TEAL)
    # hire bar track (white at 45%) and the marker dot
    bar = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle([*P(7, 11.5), *P(21, 14.5)], int(1.5 * u), fill=(255, 255, 255, 115))
    img.alpha_composite(bar)
    cx, cy = P(17, 13)
    d.ellipse([cx - 2.6 * u, cy - 2.6 * u, cx + 2.6 * u, cy + 2.6 * u], fill=(255, 255, 255, 255))
    return img.resize((1024, 1024), Image.LANCZOS)


def main():
    big = draw()
    big.save(HERE / "icon.png")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            big.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            big.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
    print("wrote", HERE / "icon.png", "and", HERE / "icon.icns")


if __name__ == "__main__":
    main()
