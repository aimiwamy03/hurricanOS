"""Any Hawaiʻi ZIP → where it is, and the nearest area the agent actually watches.

ZIP points come from app/hi_zips.json (US Census ZCTA internal points), saved in the
repo so the lookup works with the wifi off. A ZIP point is the middle of the ZIP area,
so distances are "about", never exact.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from app import db
from app.config import AREAS, REGION_ZIPS, area_for_zip

_ZIPS: dict[str, list[float]] = json.loads((Path(__file__).parent / "hi_zips.json").read_text())["zips"]

# Rough boxes, west to east. Enough to never send someone to a store across the ocean.
_ISLANDS = [
    ("Kauaʻi", 21.75, 22.35, -160.30, -159.20),
    ("Oʻahu", 21.20, 21.75, -158.35, -157.60),
    ("Molokaʻi", 21.00, 21.25, -157.35, -156.70),
    ("Lānaʻi", 20.70, 20.95, -157.10, -156.80),
    ("Maui", 20.55, 21.05, -156.70, -155.95),
    ("Hawaiʻi Island", 18.85, 20.30, -156.10, -154.75),
]


def island_at(lat: float, lng: float) -> str | None:
    for name, lat_lo, lat_hi, lng_lo, lng_hi in _ISLANDS:
        if lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi:
            return name
    return None


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def _watched_points() -> dict[str, list[tuple[float, float]]]:
    """Where each watched ZIP's stores are; the area's town center if none are found yet."""
    points: dict[str, list[tuple[float, float]]] = {zip_code: [] for zip_code in REGION_ZIPS}
    for row in db.query("SELECT zip, lat, lng FROM stores WHERE lat IS NOT NULL AND lng IS NOT NULL"):
        if row["zip"] in points:
            points[row["zip"]].append((row["lat"], row["lng"]))
    for zip_code, found in points.items():
        area = AREAS.get(area_for_zip(zip_code) or "")
        if not found and area:
            found.append(area["center"])
    return points


def locate(lat: float, lng: float, zip_code: str | None = None) -> dict:
    """Nearest watched ZIPs to a point, same island first. Other islands are marked, not hidden."""
    island = island_at(lat, lng)
    watched = []
    for code, points in _watched_points().items():
        if not points:
            continue
        nearest = min(points, key=lambda p: miles((lat, lng), p))
        watched.append({
            "zip": code,
            "area": area_for_zip(code) or code,
            "miles": round(miles((lat, lng), nearest), 1),
            "same_island": island is not None and island_at(*nearest) == island,
        })
    watched.sort(key=lambda row: (not row["same_island"], row["miles"]))
    return {
        "zip": zip_code,
        "lat": lat,
        "lng": lng,
        "island": island,
        "watched": zip_code in REGION_ZIPS if zip_code else False,
        "nearest": watched,
    }


def lookup(zip_code: str) -> dict | None:
    point = _ZIPS.get(zip_code)
    if not point:
        return None
    return locate(point[0], point[1], zip_code)

