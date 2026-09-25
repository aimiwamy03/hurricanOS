"""Stockout risk per essential x ZIP. Tinybird endpoint first; the same math in SQLite
when Tinybird is unreachable (offline) or mocked.

Run alone with: python -m agent.forecast
"""

from __future__ import annotations

import asyncio

from app import db, tinybird_client
from app.config import ESSENTIALS
from agent.util import iso_ago, log, now_iso

HOURS = 3
EXPLAIN_MIN_RISK = 0.3
# One "few options" reading can be Walmart search returning different products, so an
# item only counts as running out after this many low/out checks in a row.
CONFIRM_CHECKS = 2
MAX_EXPLANATIONS = 10
_NAMES = {item["key"]: item["name"] for item in ESSENTIALS}


def sqlite_depletion(hours: int = HOURS) -> list[dict]:
    """Same logic as the depletion_by_item_zip endpoint."""
    rows = db.query(
        """
        WITH window AS (
          -- ZIP-level checks (Walmart pickup for the ZIP) count as one store per ZIP.
          SELECT zip, essential_key,
                 CASE WHEN level = 'zip' THEN 'zip:' || zip ELSE CAST(store_id AS TEXT) END AS store_id,
                 status, ts_utc
          FROM stock_checks
          WHERE ts_utc > ? AND level IN ('store', 'zip') AND status != 'unknown'
        ),
        ranked AS (
          SELECT *,
                 ROW_NUMBER() OVER (PARTITION BY zip, essential_key, store_id ORDER BY ts_utc DESC) AS newest,
                 ROW_NUMBER() OVER (PARTITION BY zip, essential_key, store_id ORDER BY ts_utc ASC) AS oldest
          FROM window
        ),
        per_store AS (
          SELECT zip, essential_key, store_id,
                 MAX(CASE WHEN newest = 1 THEN status END) AS latest,
                 MAX(CASE WHEN oldest = 1 THEN status END) AS earliest,
                 MAX(ts_utc) AS last_seen
          FROM ranked
          GROUP BY zip, essential_key, store_id
        )
        SELECT zip, essential_key,
               COUNT(*) AS stores_checked,
               SUM(latest IN ('low', 'out')) AS stores_low_out,
               SUM(earliest = 'in_stock' AND latest IN ('low', 'out')) AS stores_flipped,
               MAX(last_seen) AS last_seen
        FROM per_store
        GROUP BY zip, essential_key
        """,
        (iso_ago(hours),),
    )
    for row in rows:
        checked = row["stores_checked"] or 1
        row["risk"] = round(0.6 * row["stores_low_out"] / checked + 0.4 * row["stores_flipped"] / checked, 3)
    return sorted(rows, key=lambda row: -row["risk"])




def template_reason(row: dict) -> str:
    item = _NAMES.get(row["essential_key"], row["essential_key"])
    checked = row["stores_checked"]
    text = (
        f"{item}: few or none found at {row['stores_low_out']} of {checked} "
        f"source{'s' if checked != 1 else ''} in {row['zip']}, "
        f"{row.get('streak', CONFIRM_CHECKS)} checks in a row (last {HOURS} hours)"
    )
    if row.get("stores_flipped"):
        text += "; it was in stock earlier"
    return text + "."


def low_streak(zip_code: str, essential_key: str) -> int:
    """How many of the newest checks in a row said low or out (unknown results are skipped)."""
    rows = db.query(
        "SELECT status FROM stock_checks WHERE zip = ? AND essential_key = ? AND status != 'unknown' "
        "ORDER BY ts_utc DESC LIMIT 6",
        (zip_code, essential_key),
    )
    streak = 0
    for row in rows:
        if row["status"] not in ("low", "out"):
            break
        streak += 1
    return streak


async def explain(row: dict) -> str:
    """Code-built on purpose. In testing, the local model restated wrong counts
    ("at 2 stores" for 1 of 2) that still passed a digits-only check. Residents act
    on this sentence, so it comes straight from the numbers."""
    return template_reason(row)


def _has_recent_checks() -> bool:
    return bool(db.query("SELECT 1 FROM stock_checks WHERE ts_utc > ? AND status != 'unknown' LIMIT 1", (iso_ago(HOURS),)))


async def run_forecast() -> list[dict]:
    started = now_iso()
    rows = await tinybird_client.query_endpoint("depletion_by_item_zip", {"hours": HOURS})
    source = "tinybird"
    # None = unreachable. No "risk" column = the old endpoint version is still live.
    if rows is None or (rows and "risk" not in rows[0]) or (not rows and _has_recent_checks()):
        rows = sqlite_depletion(HOURS)
        source = "sqlite"
    explained = 0
    for row in rows:
        risk = float(row.get("risk") or 0)
        if risk < EXPLAIN_MIN_RISK or explained >= MAX_EXPLANATIONS:
            continue
        row["streak"] = low_streak(row["zip"], row["essential_key"])
        if row["streak"] < CONFIRM_CHECKS:
            log("forecast", f"{row['zip']} {row['essential_key']} risk {risk:.2f} not confirmed yet ({row['streak']} low check)")
            continue
        reason = await explain(row)
        explained += 1
        db.insert(
            "forecasts",
            {"ts_utc": now_iso(), "essential_key": row["essential_key"], "zip": row["zip"], "risk": risk, "reason": reason},
        )
        log("forecast", f"{row['zip']} {row['essential_key']} risk {risk:.2f}: {reason}")
    db.set_kv("forecast_run_utc", started)  # readers only trust rows from this run on
    log("forecast", f"{len(rows)} item/ZIP pairs from {source}, {explained} at risk")
    return rows


def latest_risk(zip_code: str, essential_key: str) -> float:
    rows = db.query(
        "SELECT risk FROM forecasts WHERE zip = ? AND essential_key = ? AND ts_utc >= ? ORDER BY ts_utc DESC LIMIT 1",
        (zip_code, essential_key, db.forecast_run_utc()),
    )
    return float(rows[0]["risk"]) if rows else 0.0


if __name__ == "__main__":
    for item in asyncio.run(run_forecast()):
        print(item)
