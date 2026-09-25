"""Shelf reports: people standing in a store say whether an item is on the shelf.

This is the only data in Shelfwatch that measures the shelf itself, so fresh reports
rank above Walmart's online pickup data. They expire after REPORT_WINDOW_MIN, and a
store/item where people disagree is marked "mixed" rather than picking a side.

Abuse protection is basic on purpose (no accounts): each browser keeps a random id,
stored here only as a hash, and both that id and the network address are rate limited.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app import db
from app.config import ESSENTIALS
from app.safety import shopping_allowed

REPORT_WINDOW_MIN = 60      # a shelf report older than this is not shown
SAME_ITEM_COOLDOWN_MIN = 10  # one device, one store, one item
DEVICE_PER_HOUR = 30
IP_PER_HOUR = 120            # a shelter or café shares one address, so this is looser
VERDICTS = ("has", "empty")
_KEYS = {item["key"] for item in ESSENTIALS}
_NAMES = {item["key"]: item["name"] for item in ESSENTIALS}


class ReportRejected(ValueError):
    """The message is shown to the person as is. status: 400 bad report, 429 too many."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _since(minutes: int, now: datetime) -> str:
    return (now - timedelta(minutes=minutes)).isoformat()


def _device_hash(device: str) -> str:
    return hashlib.sha256(device.encode("utf-8")).hexdigest()[:16]


def submit(store_id: int, essential_key: str, verdict: str, device: str, ip: str, now: datetime | None = None) -> dict:
    now = now or _now()
    if verdict not in VERDICTS:
        raise ReportRejected("Pick “has it” or “empty”.")
    if essential_key not in _KEYS:
        raise ReportRejected("Shelfwatch doesn't track that item.")
    device = (device or "").strip()
    if not 8 <= len(device) <= 64:
        raise ReportRejected("This browser couldn't be identified. Reload the page and try again.")
    stores = db.query("SELECT id, zip, name FROM stores WHERE id = ?", (store_id,))
    if not stores:
        raise ReportRejected("That store isn't in Shelfwatch.")
    store = stores[0]
    if not shopping_allowed(store["zip"]):
        # Storm conditions: nobody should be in a store to report from it.
        raise ReportRejected("Reports are off in this area while storm conditions are on. Stay safe.")

    who = _device_hash(device)
    recent_same = db.query(
        "SELECT ts_utc FROM shelf_reports WHERE device = ? AND store_id = ? AND essential_key = ? AND ts_utc >= ?",
        (who, store_id, essential_key, _since(SAME_ITEM_COOLDOWN_MIN, now)),
    )
    if recent_same:
        raise ReportRejected(f"You already reported {_NAMES[essential_key].lower()} here in the last {SAME_ITEM_COOLDOWN_MIN} minutes.", 429)
    per_device = db.query("SELECT COUNT(*) AS n FROM shelf_reports WHERE device = ? AND ts_utc >= ?", (who, _since(60, now)))[0]["n"]
    if per_device >= DEVICE_PER_HOUR:
        raise ReportRejected("That's a lot of reports from one phone in an hour. Try again later.", 429)
    per_ip = db.query("SELECT COUNT(*) AS n FROM shelf_reports WHERE ip = ? AND ts_utc >= ?", (ip, _since(60, now)))[0]["n"]
    if per_ip >= IP_PER_HOUR:
        raise ReportRejected("Too many reports from this network in an hour. Try again later.", 429)

    db.insert("shelf_reports", {
        "ts_utc": now.isoformat(), "store_id": store_id, "zip": store["zip"], "essential_key": essential_key,
        "verdict": verdict, "device": who, "ip": ip,
    })
    return summaries([store_id], now).get(store_id, {}).get(essential_key, {})


def summarize(rows: list[dict], now: datetime) -> dict:
    """Reports for one store/item -> what to show. One vote per device (its newest)."""
    newest_by_device: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r["ts_utc"]):
        newest_by_device[row["device"]] = row
    votes = list(newest_by_device.values())
    has = sum(1 for row in votes if row["verdict"] == "has")
    empty = len(votes) - has
    if has and empty and max(has, empty) < 2 * min(has, empty):
        verdict = "mixed"  # people disagree: don't pick a side
    else:
        verdict = "has" if has > empty else "empty" if empty > has else votes[-1]["verdict"]
    ages = [int((now - datetime.fromisoformat(row["ts_utc"])).total_seconds() // 60) for row in votes]
    return {"verdict": verdict, "has": has, "empty": empty, "newest_min": min(ages), "oldest_min": max(ages)}


def summaries(store_ids: list[int], now: datetime | None = None) -> dict[int, dict[str, dict]]:
    """{store_id: {essential_key: summary}} for reports inside the window."""
    now = now or _now()
    if not store_ids:
        return {}
    marks = ", ".join("?" for _ in store_ids)
    rows = db.query(
        f"SELECT store_id, essential_key, verdict, device, ts_utc FROM shelf_reports "
        f"WHERE store_id IN ({marks}) AND ts_utc >= ?",
        (*store_ids, _since(REPORT_WINDOW_MIN, now)),
    )
    grouped: dict[tuple[int, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["store_id"], row["essential_key"]), []).append(row)
    out: dict[int, dict[str, dict]] = {}
    for (store_id, key), group in grouped.items():
        out.setdefault(store_id, {})[key] = summarize(group, now)
    return out


def sentence(item_name: str, store_name: str, summary: dict) -> str:
    """Plain words for the page and for /ask. Always says how many people and how long ago."""
    people = lambda n: f"{n} {'person' if n == 1 else 'people'}"  # noqa: E731
    age = "just now" if summary["newest_min"] < 1 else f"latest {summary['newest_min']} min ago"
    if summary["verdict"] == "mixed":
        return (f"{item_name} at {store_name}: reports disagree, {people(summary['has'])} saw it on the shelf "
                f"and {people(summary['empty'])} found it empty ({age}).")
    if summary["verdict"] == "has":
        return f"{item_name} at {store_name}: {people(summary['has'])} saw it on the shelf ({age})."
    return f"{item_name} at {store_name}: {people(summary['empty'])} found the shelf empty ({age})."
