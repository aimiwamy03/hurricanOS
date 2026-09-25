"""Send 3 stock-check events through the app client and read them back."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tinybird_client import flush_pending, log_event, query_endpoint
from scripts.common import mock_on


def sample_rows() -> list[dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    rows = []
    for index, status in enumerate(("in_stock", "low", "out")):
        rows.append(
            {
                "ts_utc": now,
                "store_id": f"phase0-store-{index}",
                "zip": "96720",
                "essential_key": "flashlight",
                "status": status,
                "price": 12.99,
                "confidence": 0.4,
                "level": "store",
                "source_url": "https://example.com/phase0",
            }
        )
    return rows


def main() -> None:
    use_mock, reason = mock_on("MOCK_TINYBIRD", "TINYBIRD_TOKEN", "TINYBIRD_HOST")
    rows = sample_rows()
    for row in rows:
        log_event("stock_checks", row)
    print(f"queued {len(rows)} stock_checks events")

    if use_mock:
        print(f"MOCK TINYBIRD — {reason}")
        print("Events stayed in data/event_queue.jsonl.")
        print("Add TINYBIRD_TOKEN and TINYBIRD_HOST to .env, then:")
        print("  .venv/bin/python scripts/deploy_tinybird.py")
        print("  .venv/bin/python scripts/check_tinybird.py")
        return

    sent = flush_pending()
    print(f"flush_pending: {sent}")
    if not sent:
        print("Ingest did not land. Deploy first: .venv/bin/python scripts/deploy_tinybird.py")
        return

    async def read_back():
        found_rows = None
        for _ in range(5):
            found_rows = await query_endpoint(
                "stock_timeline",
                {"essential_key": "flashlight", "zip": "96720", "limit": 3},
            )
            if found_rows:
                return found_rows
            await asyncio.sleep(1)
        return found_rows

    found = asyncio.run(read_back())
    if found is None:
        print("stock_timeline returned nothing. The endpoint may still be deploying.")
        return
    print(f"read back {len(found)} rows")
    for row in found:
        print(f"- {row.get('status')} {row.get('essential_key')} @ {row.get('zip')} store {row.get('store_id')}")


if __name__ == "__main__":
    main()
