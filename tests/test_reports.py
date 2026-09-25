"""Shelf reports: one vote per phone, disagreement shown as mixed, an hour's life, rate limits.

Run: .venv/bin/python -m tests.test_reports   (or pytest, if installed)
Uses the real database, inside a store row it deletes afterwards.
"""

import os
from datetime import datetime, timedelta, timezone

from app import db, reports

NOW = datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc)


def _row(verdict: str, device: str, minutes_ago: int) -> dict:
    return {"verdict": verdict, "device": device, "ts_utc": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_one_vote_per_device_newest_wins() -> None:
    summary = reports.summarize([_row("has", "a", 30), _row("empty", "a", 5)], NOW)
    assert (summary["has"], summary["empty"], summary["verdict"]) == (0, 1, "empty")


def test_close_split_is_mixed_clear_majority_wins() -> None:
    assert reports.summarize([_row("has", "a", 3), _row("empty", "b", 4)], NOW)["verdict"] == "mixed"
    clear = reports.summarize([_row("empty", "a", 3), _row("empty", "b", 4), _row("has", "c", 50)], NOW)
    assert clear["verdict"] == "empty" and clear["newest_min"] == 3


def _temp_store() -> int:
    db.init_db()
    return db.upsert_store({"name": "Test Store", "chain": "test", "address_norm": "test only 96720", "zip": "96720",
                            "lat": 19.7, "lng": -155.1, "hours": "", "source_url": "", "found_ts": NOW.isoformat()})


def _cleanup(store_id: int) -> None:
    db.execute("DELETE FROM shelf_reports WHERE store_id = ?", (store_id,))
    db.execute("DELETE FROM stores WHERE id = ?", (store_id,))


def test_submit_limits_and_expiry() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:BEFORE"
    store_id = _temp_store()
    try:
        first = reports.submit(store_id, "tarp", "empty", "device-one-1234", "1.2.3.4", NOW)
        assert first["verdict"] == "empty"
        try:
            reports.submit(store_id, "tarp", "has", "device-one-1234", "1.2.3.4", NOW + timedelta(minutes=2))
            raise AssertionError("same item within the cooldown was accepted")
        except reports.ReportRejected as exc:
            assert exc.status == 429
        reports.submit(store_id, "tarp", "empty", "device-two-1234", "1.2.3.4", NOW + timedelta(minutes=3))
        live = reports.summaries([store_id], NOW + timedelta(minutes=5))[store_id]["tarp"]
        assert (live["empty"], live["verdict"]) == (2, "empty")
        assert reports.summaries([store_id], NOW + timedelta(minutes=70)) == {}  # expired after an hour
        for bad in (("chainsaw", "has"), ("tarp", "maybe")):
            try:
                reports.submit(store_id, bad[0], bad[1], "device-three-12", "1.2.3.4", NOW)
                raise AssertionError(f"{bad} was accepted")
            except reports.ReportRejected as exc:
                assert exc.status == 400
    finally:
        _cleanup(store_id)
        os.environ.pop("PHASE_OVERRIDE", None)


def test_no_reports_during_storm_conditions() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:DURING"
    store_id = _temp_store()
    try:
        reports.submit(store_id, "tarp", "has", "device-four-123", "1.2.3.4", NOW)
        raise AssertionError("a DURING report was accepted")
    except reports.ReportRejected as exc:
        assert "storm conditions" in str(exc)
    finally:
        _cleanup(store_id)
        os.environ.pop("PHASE_OVERRIDE", None)


def test_sentence_says_who_and_when() -> None:
    text = reports.sentence("Tarp", "Walmart", {"verdict": "empty", "has": 0, "empty": 2, "newest_min": 12, "oldest_min": 30})
    assert text == "Tarp at Walmart: 2 people found the shelf empty (latest 12 min ago)."


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
