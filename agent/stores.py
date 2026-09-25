"""Store discovery with the google_maps_search template.

The template runs as a Nimble async task, like the stock checks: start_scan submits
the queries now, and record_maps_result saves the stores when a later cycle collects them.
"""

from __future__ import annotations

import math
import re

from app import db, nimble_client
from app.config import ACTIVE_AREAS, area_for_zip
from agent.util import log, now_iso

TEMPLATE = "google_maps_search"
QUERIES = ("Walmart near {zip} Hawaii", "Home Depot near {zip} Hawaii", "Target near {zip} Hawaii", "hardware store near {zip} Hawaii")
MAX_KM = 25
_CHAIN_NAMES = {"walmart": "walmart", "target": "target", "home depot": "home_depot"}


def normalize_address(address: str) -> str:
    text = address.lower()
    text = re.sub(r"\b(ste|suite|unit|apt|#)\s*[\w-]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def chain_for(name: str) -> str:
    lowered = name.lower()
    for needle, chain in _CHAIN_NAMES.items():
        if needle in lowered:
            return chain
    return "other"


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def start_scan(zip_code: str) -> dict[str, dict]:
    """Submit one maps query per chain. Returns {task_id: task_info} for the pending list."""
    tasks = {}
    for query in QUERIES:
        text = query.format(zip=zip_code)
        try:
            task_id = await nimble_client.run_template_start(TEMPLATE, {"query": text})
        except Exception as exc:
            log("stores", f"could not start '{text}': {type(exc).__name__}: {exc}")
            continue
        tasks[task_id] = {"kind": "maps", "meta": {"zip": zip_code, "query": text}, "started_utc": now_iso()}
    log("stores", f"{zip_code}: started {len(tasks)} map searches")
    return tasks


def record_maps_result(meta: dict, payload: dict) -> int:
    """Upsert stores from a finished maps task. Returns how many were new."""
    zip_code = meta["zip"]
    area = ACTIVE_AREAS.get(area_for_zip(zip_code) or "") or {}
    center = area.get("center")
    # Live shape: data.parsing.entities.SearchResult = [place, ...]
    places = (payload.get("data") or {}).get("parsing") or []
    if isinstance(places, dict):
        places = (places.get("entities") or {}).get("SearchResult") or []
    new = 0
    for place in places:
        if not isinstance(place, dict):
            continue
        name = str(place.get("title") or "").strip()
        address = str(place.get("address") or place.get("street_address") or "").strip()
        if not name or not address:
            continue
        lat, lng = _float(place.get("latitude")), _float(place.get("longitude"))
        place_zip = str(place.get("zip_code") or "").strip() or zip_code
        in_region = place_zip in area.get("zips", [])
        near = center is not None and lat is not None and lng is not None and _km(center, (lat, lng)) <= MAX_KM
        if not (in_region or near):
            continue
        address_norm = normalize_address(address)
        chain = chain_for(name)
        existed = db.query("SELECT 1 FROM stores WHERE chain = ? AND address_norm = ?", (chain, address_norm))
        db.upsert_store(
            {
                "name": name,
                "chain": chain,
                "address_norm": address_norm,
                "zip": place_zip,
                "lat": lat,
                "lng": lng,
                "hours": str(place.get("business_status") or ""),
                "source_url": str(place.get("place_url") or ""),
                # Comes back in the same Google Maps result, so no extra call.
                "phone": str(place.get("phone_number") or "").strip() or None,
                "found_ts": now_iso(),
            }
        )
        if not existed:
            new += 1
    log("stores", f"{zip_code} '{meta.get('query')}': {len(places)} places, {new} new stores")
    return new
