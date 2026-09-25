"""Shareable status cards: a FLUX background (no text, made once per area) with the
verified facts drawn on top by Pillow. FLUX never sees or draws a fact.

Backgrounds once:  .venv/bin/python -m agent.visuals --backgrounds
Render a card:     .venv/bin/python -m agent.visuals 96720   (or GET /api/cards/96720)
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from app import db
from app.config import ACTIVE_AREAS, DATA_DIR, ESSENTIALS, HST, STORM_NAME, area_for_zip
from app.safety import SAFETY_MESSAGE, caution_message, get_zip_phase, is_official, shopping_allowed
from app.tips import image_path, tips_for
from agent.util import minutes_since

W, H = 1200, 630
IMAGE_DIR = DATA_DIR / "images"
CARD_DIR = IMAGE_DIR / "cards"
_NAMES = {item["key"]: item["name"] for item in ESSENTIALS}
_STATUS = {"in_stock": "In stock", "low": "Low", "out": "Out"}
_STATUS_COLOR = {"in_stock": (74, 222, 128), "low": (251, 191, 36), "out": (248, 113, 113)}
_SCENE = {
    "Hilo / East Hawaii": "lush green windward tropical coastline with palm trees under heavy grey rain clouds",
    "Kona / West Hawaii": "dry leeward tropical coastline of black rocks and palm trees under grey storm clouds, no fire, no lava, no red",
    "Central / South Maui": "tropical island coastline with a green mountain under grey storm clouds",
}
_FONTS = ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf")
_BOLD = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc")


def _slug(area: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", area.lower()).strip("_")


def background_path(area: str) -> Path:
    return IMAGE_DIR / f"bg_{_slug(area)}.png"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD if bold else ()) + _FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=1 if path.endswith(".ttc") and bold else 0)
    return ImageFont.load_default(size=size)


def _clock(iso: str | None) -> str:
    if not iso:
        return "--:--"
    return datetime.fromisoformat(iso).astimezone(HST).strftime("%H:%M")


def card_facts(zip_code: str) -> dict:
    """Everything the card will say, from SQLite, through the safety gate."""
    area = area_for_zip(zip_code) or zip_code
    phase = get_zip_phase(zip_code)
    facts: dict = {"zip": zip_code, "area": area, "phase": phase, "lines": [], "tips": [], "caution": None}
    facts["title"] = f"{area.split(' / ')[0]} ({zip_code}) — Hurricane {STORM_NAME} supplies update"
    if not shopping_allowed(zip_code):
        facts["title"] = f"{area.split(' / ')[0]} ({zip_code}) — Hurricane {STORM_NAME}"
        facts["message"] = SAFETY_MESSAGE
        facts["tips"] = tips_for("DURING")[:4]
    else:
        facts["caution"] = caution_message(zip_code)
        rows = db.query(
            """
            SELECT c.essential_key, c.status, c.price, c.ts_utc, s.name AS store_name FROM stock_checks c
            LEFT JOIN stores s ON s.id = c.store_id
            WHERE c.zip = ? AND c.id IN (
              SELECT MAX(id) FROM stock_checks WHERE zip = ? AND status != 'unknown' GROUP BY essential_key)
            """,
            (zip_code, zip_code),
        )
        # Low and out first: that is what people need to know before they drive.
        rows.sort(key=lambda row: (row["status"] == "in_stock", row["essential_key"]))
        for row in rows[:6]:
            price = f" · ${row['price']:.2f}" if row["price"] else ""
            facts["lines"].append(
                {
                    "key": row["essential_key"],
                    "status": row["status"],
                    "text": f"{_NAMES.get(row['essential_key'], row['essential_key'])}: {_STATUS[row['status']]}",
                    "detail": f"{row['store_name'] or 'store'} pickup{price} · checked {_clock(row['ts_utc'])} HST",
                }
            )
    # In DURING the useful link is the shelter source; otherwise the latest advisory.
    links = db.query(
        "SELECT url FROM official_links ORDER BY kind = 'shelter' DESC, found_ts DESC"
        if facts.get("message")
        else "SELECT url FROM official_links ORDER BY found_ts DESC"
    )
    official = next((row["url"] for row in links if is_official(row["url"])), "https://www.weather.gov/hfo/")
    newest = max((row["ts_utc"] for row in db.query("SELECT MAX(ts_utc) AS ts_utc FROM stock_checks") if row["ts_utc"]), default=None)
    age = minutes_since(newest)
    facts["footer"] = (
        f"Data from {_clock(newest)} HST ({int(age)} min before this card) · Not official guidance · "
        f"{urlparse(official).netloc.removeprefix('www.')}"
        if newest
        else f"No checks yet · Not official guidance · {urlparse(official).netloc.removeprefix('www.')}"
    )
    return facts


def _base(area: str) -> Image.Image:
    path = background_path(area)
    if path.exists():
        with Image.open(path) as image:
            src = image.convert("RGB")
        scale = max(W / src.width, H / src.height)
        src = src.resize((int(src.width * scale), int(src.height * scale)), Image.LANCZOS)
        left, top = (src.width - W) // 2, (src.height - H) // 2
        return src.crop((left, top, left + W, top + H))
    # No background yet (or FLUX down): a plain gradient, the card still works.
    base = Image.new("RGB", (W, H), (30, 41, 59))
    draw = ImageDraw.Draw(base)
    for y in range(H):
        shade = int(30 + 40 * y / H)
        draw.line([(0, y), (W, y)], fill=(shade, shade + 12, shade + 30))
    return base


def _icon(path: Path, size: int) -> Image.Image | None:
    if not path.exists():
        return None
    with Image.open(path) as image:
        icon = image.convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=255)
    icon.putalpha(mask)
    return icon


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def render_card(zip_code: str) -> Path:
    """Pure Pillow, no API calls. Writes data/images/cards/{zip}_latest.jpg (JPEG: ~5x smaller
    than PNG, which matters when the card is texted over a weak connection)."""
    facts = card_facts(zip_code)
    card = _base(facts["area"]).convert("RGBA")
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rectangle((0, 0, int(W * 0.66), H), fill=(10, 15, 25, 215))
    card = Image.alpha_composite(card, panel)
    draw = ImageDraw.Draw(card)
    x, y, text_w = 44, 36, int(W * 0.66) - 88

    for line in _wrap(draw, facts["title"], _font(34, bold=True), text_w):
        draw.text((x, y), line, font=_font(34, bold=True), fill="white")
        y += 42
    phase_color = {"BEFORE": (74, 222, 128), "DURING": (248, 113, 113), "AFTER": (147, 197, 253)}[facts["phase"]]
    draw.rounded_rectangle((x, y + 6, x + 150, y + 40), radius=10, fill=phase_color)
    draw.text((x + 14, y + 10), facts["phase"], font=_font(22, bold=True), fill=(10, 15, 25))
    y += 58

    if facts.get("caution"):
        draw.rounded_rectangle((x, y, x + text_w, y + 44), radius=8, fill=(120, 72, 8))
        draw.text((x + 12, y + 11), facts["caution"], font=_font(19, bold=True), fill=(254, 243, 199))
        y += 58

    if facts.get("message"):
        for line in _wrap(draw, facts["message"], _font(26, bold=True), text_w):
            draw.text((x, y), line, font=_font(26, bold=True), fill=(254, 202, 202))
            y += 34
        y += 12
        for tip in facts["tips"]:
            icon = _icon(image_path(tip["key"]), 56)
            if icon:
                card.alpha_composite(icon, (x, y))
            for i, line in enumerate(_wrap(draw, tip["text"], _font(21), text_w - 72)[:2]):
                draw.text((x + 72, y + 4 + i * 26), line, font=_font(21), fill="white")
            y += 70
    elif facts["lines"]:
        for line in facts["lines"]:
            icon = _icon(IMAGE_DIR / f"{line['key']}.png", 52)
            if icon:
                card.alpha_composite(icon, (x, y))
            draw.text((x + 68, y + 2), line["text"], font=_font(24, bold=True), fill=_STATUS_COLOR[line["status"]])
            draw.text((x + 68, y + 30), line["detail"], font=_font(17), fill=(203, 213, 225))
            y += 64
    else:
        draw.text((x, y), "No confirmed stock checks for this ZIP yet.", font=_font(24), fill="white")

    draw.text((x, H - 44), facts["footer"], font=_font(17), fill=(148, 163, 184))
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    out = CARD_DIR / f"{zip_code}_latest.jpg"
    card.convert("RGB").save(out, "JPEG", quality=82, optimize=True)
    return out


async def make_backgrounds() -> None:
    """One FLUX call per active area, ever. Delete the file to make a new one."""
    from app.flux_client import generate

    for area in ACTIVE_AREAS:
        path = background_path(area)
        if path.exists():
            print(f"{area}: background exists, kept")
            continue
        prompt = (
            f"calm minimal illustration of a {_SCENE.get(area, 'tropical island coastline under storm clouds')}, "
            "soft muted colors, wide landscape, large calm empty area on the left, "
            "no text, no letters, no numbers, no logos, no people"
        )
        made = await generate(prompt, str(path))
        print(f"{area}: {'saved ' + str(path) if made else 'FAILED, cards use a plain gradient'}")


if __name__ == "__main__":
    if "--backgrounds" in sys.argv:
        asyncio.run(make_backgrounds())
    else:
        for zip_code in sys.argv[1:] or [z for info in ACTIVE_AREAS.values() for z in info["zips"]]:
            print(render_card(zip_code))
