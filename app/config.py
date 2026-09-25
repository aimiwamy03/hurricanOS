"""Settings from the environment. No extra settings library."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

HST = timezone(timedelta(hours=-10))
PT = ZoneInfo("America/Los_Angeles")
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _raw(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


LOCAL_LLM_BASE_URL = _raw("LOCAL_LLM_BASE_URL", "http://localhost:8080/v1")
LOCAL_LLM_MODEL = _raw("LOCAL_LLM_MODEL", "LiquidAI/LFM2.5-8B-A1B-MLX-4bit")
NIMBLE_API_KEY = _raw("NIMBLE_API_KEY")
TINYBIRD_TOKEN = _raw("TINYBIRD_TOKEN")
TINYBIRD_HOST = _raw("TINYBIRD_HOST") or _raw("TINYBIRD_API_URL") or _raw("TINYBIRD_URL")
BFL_API_KEY = _raw("BFL_API_KEY")
BFL_BASE_URL = _raw("BFL_BASE_URL", "https://api.bfl.ai/v1").rstrip("/")
BFL_IMAGE_ENDPOINT = _raw("BFL_IMAGE_ENDPOINT", "flux-2-pro")
STORM_NAME = _raw("STORM_NAME", "Nolo")
REGION_ZIPS = _list("REGION_ZIPS") or ["96720", "96740"]
MOCK_NIMBLE = _bool("MOCK_NIMBLE")
MOCK_TINYBIRD = _bool("MOCK_TINYBIRD")
MOCK_FLUX = _bool("MOCK_FLUX")
MOCK_LLM = _bool("MOCK_LLM")
CYCLE_SECONDS = _int("CYCLE_SECONDS", 540)
MAX_NIMBLE_CALLS = _int("MAX_NIMBLE_CALLS", 800)
# Stock checks run until this Pacific time (HH:MM), with the Nimble budget paced to last until then.
STOCK_CHECKS_UNTIL_PT = tuple(int(part) for part in _raw("STOCK_CHECKS_UNTIL_PT", "20:00").split(":"))
MAX_IMAGE_CALLS = _int("MAX_IMAGE_CALLS", 30)
PHASE_OVERRIDE = _raw("PHASE_OVERRIDE")

ESSENTIALS = [
    {"key": "generator", "name": "Generator", "search_terms": "portable generator"},
    {"key": "d_batteries", "name": "D batteries", "search_terms": "D batteries"},
    {"key": "aa_batteries", "name": "AA batteries", "search_terms": "AA batteries"},
    {"key": "flashlight", "name": "Flashlight", "search_terms": "flashlight"},
    {"key": "bottled_water", "name": "Bottled water", "search_terms": "bottled water"},
    {"key": "tarp", "name": "Tarp", "search_terms": "tarp"},
    {"key": "sandbags", "name": "Sandbags", "search_terms": "sandbags"},
    {"key": "first_aid_kit", "name": "First aid kit", "search_terms": "first aid kit"},
    {"key": "power_bank", "name": "Power bank", "search_terms": "power bank"},
    {"key": "gas_can", "name": "Gas can", "search_terms": "gas can"},
    # Second ten: the rest of the FEMA/Hawaiʻi Emergency Management "build a kit" list
    # that a Walmart pickup search can actually answer.
    {"key": "aaa_batteries", "name": "AAA batteries", "search_terms": "AAA batteries"},
    {"key": "lantern", "name": "Lantern", "search_terms": "battery lantern"},
    {"key": "weather_radio", "name": "Emergency radio", "search_terms": "emergency weather radio"},
    {"key": "canned_food", "name": "Canned food", "search_terms": "canned food"},
    {"key": "can_opener", "name": "Can opener", "search_terms": "manual can opener"},
    {"key": "water_jug", "name": "Water container", "search_terms": "water storage container"},
    {"key": "propane", "name": "Propane", "search_terms": "propane cylinder"},
    {"key": "camp_stove", "name": "Camp stove", "search_terms": "camping stove"},
    {"key": "cooler", "name": "Cooler", "search_terms": "cooler"},
    {"key": "duct_tape", "name": "Duct tape", "search_terms": "duct tape"},
]

CHAINS = ["walmart", "target", "home_depot"]

# center = rough town center (lat, lng), used to drop map results that are far away.
AREAS = {
    "Hilo / East Hawaii": {
        "island": "Hawaii",
        "zips": ["96720", "96749", "96778"],
        "side": "windward",
        "center": (19.707, -155.089),
    },
    "Kona / West Hawaii": {
        "island": "Hawaii",
        "zips": ["96740"],
        "side": "leeward",
        "center": (19.640, -155.997),
    },
    "Central / South Maui": {
        "island": "Maui",
        "zips": ["96732", "96793", "96753"],
        "side": "mixed",
        "center": (20.830, -156.460),
    },
}


def area_for_zip(zip_code: str) -> str | None:
    for name, area in AREAS.items():
        if zip_code in area["zips"]:
            return name
    return None


# REGION_ZIPS narrows AREAS to the ZIPs the agent actually works on.
ACTIVE_AREAS = {
    name: {**area, "zips": [z for z in area["zips"] if z in REGION_ZIPS]}
    for name, area in AREAS.items()
    if any(z in REGION_ZIPS for z in area["zips"])
}
