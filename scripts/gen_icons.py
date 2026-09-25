"""App icons for the installable web app (web/manifest.webmanifest).

The mark is a map pin: a ring split green / amber / red, like the stock rings on the map.
Run: .venv/bin/python -m scripts.gen_icons
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
CANVAS = (10, 10, 10)
OK, WARN, BAD, INK = (74, 222, 128), (255, 176, 32), (248, 113, 113), (255, 255, 255)


def draw(size: int, safe: float) -> Image.Image:
    """safe: share of the icon the ring may fill (maskable icons get cropped to a circle)."""
    scale = 4  # draw big, shrink down, for smooth edges
    big = size * scale
    image = Image.new("RGB", (big, big), CANVAS)
    pen = ImageDraw.Draw(image)
    radius = big * safe / 2
    box = [big / 2 - radius, big / 2 - radius, big / 2 + radius, big / 2 + radius]
    for start, end, colour in ((-90, 150, OK), (150, 225, WARN), (225, 270, BAD)):
        pen.pieslice(box, start, end, fill=colour)
    hole = radius * 0.62
    pen.ellipse([big / 2 - hole, big / 2 - hole, big / 2 + hole, big / 2 + hole], fill=CANVAS)
    dot = radius * 0.2
    pen.ellipse([big / 2 - dot, big / 2 - dot, big / 2 + dot, big / 2 + dot], fill=INK)
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        draw(size, 0.78).save(OUT / f"icon-{size}.png")
    draw(512, 0.6).save(OUT / "icon-maskable-512.png")
    draw(180, 0.72).save(OUT / "apple-touch-icon.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
