"""Regenerate the Android launcher icons and the pocket-book images from branding/juniper-neon.png.

Run from the repo root: python mobile/scripts/gen-icons.py (needs Pillow).
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "mobile/branding/juniper-neon.png"
RES = ROOT / "mobile/android/app/src/main/res"
ASSETS = ROOT / "docs/html/assets"
DASHBOARD = ROOT / "brain/dashboard/static/assets"
APP_LOGO = ROOT / "mobile/branding/logo.png"
SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def mask(size: int, circle: bool) -> Image.Image:
    scale = 4
    big = Image.new("L", (size * scale, size * scale), 0)
    draw = ImageDraw.Draw(big)
    box = (0, 0, size * scale - 1, size * scale - 1)
    if circle:
        draw.ellipse(box, fill=255)
    else:
        draw.rounded_rectangle(box, radius=size * scale // 5, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def shaped(image: Image.Image, size: int, circle: bool) -> Image.Image:
    icon = image.resize((size, size), Image.LANCZOS).convert("RGBA")
    icon.putalpha(mask(size, circle))
    return icon


def transparent(image: Image.Image, size: int) -> Image.Image:
    """The mark on a transparent background: the brightness of the neon becomes the alpha."""
    small = image.resize((size, size), Image.LANCZOS)
    alpha = small.convert("L").point(lambda v: min(255, v * 255 // 200))
    neon = Image.new("RGBA", small.size, (*image.getpixel((image.width // 2 - 60, 140)), 255))
    neon.putalpha(alpha)
    return neon


def main() -> None:
    image = Image.open(SOURCE).convert("RGB")
    for path, size in ((DASHBOARD / "logo.png", 256), (DASHBOARD / "favicon.png", 64),
                       (ASSETS / "logo.png", 256), (APP_LOGO, 256)):
        transparent(image, size).save(path, optimize=True)
    for density, size in SIZES.items():
        folder = RES / f"mipmap-{density}"
        shaped(image, size, circle=False).save(folder / "ic_launcher.png")
        shaped(image, size, circle=True).save(folder / "ic_launcher_round.png")
    image.resize((512, 512), Image.LANCZOS).save(ASSETS / "juniper-neon.png", optimize=True)
    image.resize((64, 64), Image.LANCZOS).save(ASSETS / "favicon-neon.png", optimize=True)


if __name__ == "__main__":
    main()
