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
]

CHAINS = ["walmart", "target", "home_depot"]
