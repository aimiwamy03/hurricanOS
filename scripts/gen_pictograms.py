"""One FLUX pictogram per essential (data/images/{key}.png) and per safety tip
(data/images/tip_{key}.png, see app/tips.py). Generated once and cached.

The web server serves data/images locally at /images, so the icons work offline.
Run: .venv/bin/python -m scripts.gen_pictograms            (skips files that exist)
     .venv/bin/python -m scripts.gen_pictograms --redo tarp  (regenerate one)
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

from app import db
from app.config import DATA_DIR, ESSENTIALS
from app.flux_client import generate
from app.tips import TIPS, image_path

IMAGE_DIR = DATA_DIR / "images"
SIZE = 256  # FLUX returns 1024 px; the UI shows ~56 px, so keep pages light
CONCURRENCY = 3

# Plain descriptions: no brands, and the object is named so FLUX draws the right thing.
_OBJECT = {
    "generator": "portable power generator on a frame",
    "d_batteries": "two large D-size batteries",
    "aa_batteries": "four small AA batteries",
    "flashlight": "flashlight",
    "bottled_water": "plastic bottle of drinking water",
    "tarp": "folded waterproof tarp with grommets",
    "sandbags": "stack of sandbags",
    "first_aid_kit": "first aid kit box with a cross",
    "power_bank": "portable phone power bank with a cable",
    "gas_can": "red portable fuel can",
    "aaa_batteries": "four slim AAA batteries",
    "lantern": "battery camping lantern",
    "weather_radio": "hand-crank emergency weather radio with antenna",
    "canned_food": "three sealed cans of food",
    "can_opener": "manual handheld can opener",
    "water_jug": "large portable drinking-water storage container with handle",
    "propane": "small green camping propane cylinder",
    "camp_stove": "compact two-burner camping stove",
    "cooler": "hard-sided portable ice cooler with handle",
    "duct_tape": "roll of silver duct tape",
}


def prompt_for(key: str, name: str) -> str:
    return (
        f"flat pictogram icon of a {_OBJECT.get(key, name.lower())}, bold simple shapes, thick outlines, "
        "high contrast, single object centered, plain white background, "
        "no text, no letters, no numbers, no logos"
    )


def _normalize(path: Path) -> None:
    """FLUX sends JPEG bytes at 1024 px; store a real PNG at SIZE px."""
    with Image.open(path) as image:
        if image.format == "PNG" and image.size == (SIZE, SIZE):
            return
        small = image.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    small.save(path, "PNG", optimize=True)


def _record(key: str, name: str, path: Path | None) -> None:
    db.execute(
        "INSERT INTO essentials (key, name, pictogram_path) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET name = excluded.name, pictogram_path = excluded.pictogram_path",
        (key, name, str(path) if path else None),
    )


def tip_prompt(drawing: str) -> str:
    return (
        f"flat pictogram safety sign of a {drawing}, bold simple shapes, thick outlines, high contrast, "
        "plain white background, universal public information symbol style, "
        "no text, no letters, no numbers, no logos"
    )


def _jobs() -> list[dict]:
    """Everything to draw: {key, path, prompt, essential (None for tips)}."""
    jobs = [
        {"key": item["key"], "path": IMAGE_DIR / f"{item['key']}.png", "prompt": prompt_for(item["key"], item["name"]), "essential": item}
        for item in ESSENTIALS
    ]
    jobs += [
        {"key": f"tip_{tip['key']}", "path": image_path(tip["key"]), "prompt": tip_prompt(tip["drawing"]), "essential": None}
        for tip in TIPS
        if not tip.get("reuse")
    ]
    return jobs


async def _one(job: dict, gate: asyncio.Semaphore) -> tuple[str, str]:
    path, item = job["path"], job["essential"]
    async with gate:
        made = await generate(job["prompt"], str(path))
    if not made:
        if item:
            _record(item["key"], item["name"], None)
        return job["key"], "FAILED (see [flux] line above)"
    _normalize(path)
    if item:
        _record(item["key"], item["name"], path)
    return job["key"], f"saved {path}"


async def main() -> None:
    redo = sys.argv[sys.argv.index("--redo") + 1] if "--redo" in sys.argv else None
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    todo = []
    for job in _jobs():
        if redo and job["key"] != redo:
            continue
        if job["path"].exists() and not redo:
            _normalize(job["path"])
            if job["essential"]:
                _record(job["key"], job["essential"]["name"], job["path"])
            print(f"{job['key']}: exists, kept")
            continue
        todo.append(job)
    for tip in TIPS:  # tips that reuse an essentials icon instead of their own drawing
        source = IMAGE_DIR / f"{tip['reuse']}.png" if tip.get("reuse") else None
        if source and source.exists() and not image_path(tip["key"]).exists():
            shutil.copyfile(source, image_path(tip["key"]))
    gate = asyncio.Semaphore(CONCURRENCY)
    for key, result in await asyncio.gather(*(_one(job, gate) for job in todo)):
        print(f"{key}: {result}")


if __name__ == "__main__":
    asyncio.run(main())
