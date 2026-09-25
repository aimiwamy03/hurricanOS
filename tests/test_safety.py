"""With 96720 in DURING, /api/stock returns only the safety payload, never stock.

Run: .venv/bin/python -m tests.test_safety   (or pytest, if installed)
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.main import app
from app.safety import SAFETY_MESSAGE, shopping_allowed

client = TestClient(app)  # no `with`: skips startup, so no network checks


def test_during_blocks_stock() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:DURING"
    try:
        assert shopping_allowed("96720") is False
        body = client.get("/api/stock", params={"zip": "96720"}).json()
        assert body["shopping_allowed"] is False
        assert body["message"] == SAFETY_MESSAGE
        assert "items" not in body and "forecasts" not in body
        assert "official_links" in body
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_before_shows_stock() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:BEFORE"
    try:
        body = client.get("/api/stock", params={"zip": "96720"}).json()
        assert body["shopping_allowed"] is True
        assert "items" in body
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_unknown_zip_is_blocked() -> None:
    assert shopping_allowed("00000") is False


def test_stock_prefers_a_real_reading_and_returns_products() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:BEFORE"
    try:
        body = client.get("/api/stock", params={"zip": "96720"}).json()
        by_key = {item["essential_key"]: item for item in body["items"]}
        flash = by_key["flashlight"]
        assert flash["status"] == "in_stock"
        assert flash["result_state"] == "matched"
        assert flash["products"]
        assert flash["products"][0]["name"]
        water = by_key.get("bottled_water")
        if water:
            assert water["status"] == "in_stock"
        gen = by_key.get("generator")
        if gen and gen["result_state"] == "no_match":
            assert "Home Depot" in (gen.get("result_note") or "")
        assert "Lantern" in body["unverified"] or "lantern" in by_key
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_check_rank_prefers_stock_over_listings_over_failures() -> None:
    from app.main import _check_rank

    stocked = _check_rank({"status": "in_stock", "check_id": 1})
    listed = _check_rank({"status": "unknown", "matched": 4, "check_id": 9})
    missing = _check_rank({"status": "unknown", "results": 12, "matched": 0, "check_id": 8})
    failed = _check_rank({"status": "unknown", "results": None, "matched": 0, "check_id": 20})
    assert stocked < listed < missing < failed


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
