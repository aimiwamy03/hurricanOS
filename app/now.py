"""The "What to do now" card: one instruction, the nearest shelters, and who to call.

Everything here is decided in code from saved data, never by the model, so it reads the
same with the wifi off. When Shelfwatch does not track the storm phase for someone's
island, it says so and hands them to their county instead of guessing.
"""

from __future__ import annotations

from app import db, shelters
from app.config import AREAS, REGION_ZIPS, area_for_zip
from app.safety import caution_message, get_zip_phase
from app.times import hst_clock
from app import zips as zip_lookup

EMERGENCY = {"name": "Life-threatening emergency", "phone": "911"}
WEATHER_LINKS = [
    {"label": "National Weather Service Honolulu", "url": "https://www.weather.gov/hfo/"},
    {"label": "Central Pacific Hurricane Center", "url": "https://www.nhc.noaa.gov/?cpac"},
]


def _official() -> dict | None:
    rows = db.query(
        "SELECT ts_utc, official_text, official_url FROM storm_updates "
        "WHERE official_text IS NOT NULL ORDER BY id DESC LIMIT 1"
    )
    if not rows:
        return None
    return {"text": rows[0]["official_text"], "url": rows[0]["official_url"], "read_hst": hst_clock(rows[0]["ts_utc"])}


def _instruction(phase: str | None, area: str | None, caution: str | None) -> tuple[str, str]:
    """(level, sentence). level drives the colour: go / careful / stop / unknown."""
    if phase is None:
        return "unknown", ("Shelfwatch doesn't track storm conditions for your island. "
                           "Follow your county's emergency alerts and call before you drive anywhere.")
    if phase == "DURING":
        return "stop", (f"{area}: storm conditions now. Stay where you are and don't drive for supplies. "
                        "If you have to leave, go to a shelter.")
    if phase == "AFTER":
        return "careful", (f"{area}: the storm has passed. Watch for downed lines and flooded roads, "
                           "and check official all-clear notices before driving.")
    if caution:
        return "careful", (f"{area}: storm conditions may be close. If you need supplies, go now and "
                           "get home quickly. Otherwise stay put.")
    return "go", (f"{area}: storm conditions haven't arrived. Get supplies now and be home before "
                  "they do. Know your nearest shelter.")


def build(lat: float | None = None, lng: float | None = None, zip_code: str | None = None) -> dict:
    located = None
    if zip_code:
        located = zip_lookup.lookup(zip_code)
    elif lat is not None and lng is not None:
        located = zip_lookup.locate(lat, lng)

    if located:
        island = located["island"]
        same = [row for row in located["nearest"] if row["same_island"]]
        home = located["zip"] if located["watched"] else (same[0]["zip"] if same else None)
        point = (located["lat"], located["lng"])
    else:
        # No ZIP yet: speak for the top-ranked watched area.
        ranked = [row["zip"] for row in db.query("SELECT zip FROM zips ORDER BY priority DESC, zip")]
        home = next((code for code in ranked if code in REGION_ZIPS), None)
        area = AREAS.get(area_for_zip(home) or "") if home else None
        point = area["center"] if area else None
        island = zip_lookup.island_at(*point) if point else None

    phase = get_zip_phase(home) if home else None
    area_name = area_for_zip(home) if home else None
    caution = caution_message(home) if home else None
    level, sentence = _instruction(phase, area_name, caution)
    county = shelters.COUNTY_CONTACTS.get(island or "")
    return {
        "located": located is not None,
        "zip": located["zip"] if located else None,
        "island": island,
        "home_zip": home,
        "area": area_name,
        "phase": phase,
        "level": level,
        "instruction": sentence,
        "caution": caution,
        "official": _official(),
        "shelters": shelters.nearest(*point) if point else [],
        "contacts": [EMERGENCY] + ([county] if county else []),
        "links": ([{"label": county["name"], "url": county["url"]}] if county else []) + WEATHER_LINKS,
        "alerts_signup": "Text HAWAIIALERTS to 888777" if island == "Hawaiʻi Island" else None,
    }
