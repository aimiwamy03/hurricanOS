"""Numbers for the pitch and README, from the live SQLite snapshot -> data/demo_stats.json.

Run: .venv/bin/python -m scripts.demo_stats
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.budget import remaining
from app.config import HST, MAX_NIMBLE_CALLS, PT


def _count(sql: str, params: tuple = ()) -> int:
    return int(db.query(sql, params)[0]["n"])


def _by(table: str, column: str) -> dict[str, int]:
    rows = db.query(f"SELECT {column} AS k, COUNT(*) AS n FROM {table} GROUP BY {column} ORDER BY n DESC")
    return {str(row["k"]): int(row["n"]) for row in rows}


def main() -> None:
    state_file = ROOT / "data" / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(state["started_utc"]) if state.get("started_utc") else None
    uptime_min = int((now - started).total_seconds() // 60) if started else None

    stats = {
        "generated_hst": now.astimezone(HST).strftime("%Y-%m-%d %H:%M HST"),
        "generated_pt": now.astimezone(PT).strftime("%H:%M PT"),
        "agent_started_pt": started.astimezone(PT).strftime("%H:%M PT") if started else None,
        "agent_uptime_minutes": uptime_min,
        "agent_steps": state.get("step"),
        "stock_checks": _count("SELECT COUNT(*) AS n FROM stock_checks"),
        "stock_checks_by_status": _by("stock_checks", "status"),
        "stores": _count("SELECT COUNT(*) AS n FROM stores"),
        "stores_by_chain": _by("stores", "chain"),
        "zips": [row["zip"] for row in db.query("SELECT zip FROM zips ORDER BY zip")],
        "storm_updates": _count("SELECT COUNT(*) AS n FROM storm_updates"),
        "phase_changes": _count("SELECT COUNT(*) AS n FROM agent_steps WHERE summary LIKE '%Phase change%'"),
        "false_alarms_corrected": _count("SELECT COUNT(*) AS n FROM agent_steps WHERE action = 'phase_rule'"),
        "forecasts": _count("SELECT COUNT(*) AS n FROM forecasts"),
        "items_at_risk_now": [
            {"zip": row["zip"], "item": row["essential_key"], "risk": float(row["risk"])}
            for row in db.query(
                "SELECT zip, essential_key, risk FROM forecasts WHERE ts_utc >= ? AND id IN "
                "(SELECT MAX(id) FROM forecasts GROUP BY zip, essential_key) ORDER BY CAST(risk AS REAL) DESC",
                (db.forecast_run_utc(),),
            )
        ],
        "official_links_by_kind": _by("official_links", "kind"),
        "price_jumps_flagged": _count("SELECT COUNT(*) AS n FROM stock_checks WHERE price_flag = 1"),
        "nimble_calls_used": MAX_NIMBLE_CALLS - remaining("nimble"),
    }
    out = ROOT / "data" / "demo_stats.json"
    out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))
    print(f"\nsaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
