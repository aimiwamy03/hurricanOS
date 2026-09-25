"""Local queue, then the Tinybird SDK. Callers keep working when Tinybird is down."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone

from tinybird_sdk import create_tinybird_api

from app.config import DATA_DIR, MOCK_TINYBIRD, TINYBIRD_HOST, TINYBIRD_TOKEN
from app import db

QUEUE = DATA_DIR / "event_queue.jsonl"
_lock = threading.Lock()


def log_event(datasource: str, row: dict) -> None:
    """Append one event locally. This never calls the network and never raises."""
    try:
        line = json.dumps({"ds": datasource, "row": row}, default=str)
        with _lock:
            QUEUE.parent.mkdir(parents=True, exist_ok=True)
            with QUEUE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:
        print(f"[tb] log_event failed: {type(exc).__name__}")


def _read_lines() -> list[str]:
    if not QUEUE.exists():
        return []
    return [line for line in QUEUE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_lines(lines: list[str]) -> None:
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(line + "\n" for line in lines)
    QUEUE.write_text(text, encoding="utf-8")


def _datasource(item: dict) -> str:
    return str(item.get("ds") or item.get("datasource") or "")


def flush_once() -> tuple[bool, int]:
    """Send up to 500 queued lines. Failed lines stay in the file."""
    if MOCK_TINYBIRD:
        return True, 0
    if not TINYBIRD_TOKEN or not TINYBIRD_HOST:
        with _lock:
            waiting = len(_read_lines())
        return False, waiting
    with _lock:
        queued = _read_lines()
        batch = queued[:500]
    if not batch:
        return True, 0

    groups: dict[str, list[tuple[str, dict]]] = {}
    failed: list[str] = []
    for line in batch:
        try:
            item = json.loads(line)
            name = _datasource(item)
            row = item["row"]
            if not name or not isinstance(row, dict):
                raise ValueError("queue line missing ds or row")
            groups.setdefault(name, []).append((line, row))
        except Exception:
            failed.append(line)

    api = create_tinybird_api({"base_url": TINYBIRD_HOST, "token": TINYBIRD_TOKEN})
    sent_lines: list[str] = []
    for name, pairs in groups.items():
        try:
            api.ingest_batch(name, [row for _, row in pairs])
            sent_lines.extend(line for line, _ in pairs)
        except Exception as exc:
            print(f"[tb] ingest {name} failed: {type(exc).__name__}: {exc}")
            failed.extend(line for line, _ in pairs)

    with _lock:
        current = _read_lines()
        for line in sent_lines:
            if line in current:
                current.remove(line)
        _write_lines(current)

    if failed:
        return False, len(failed)
    if sent_lines:
        db.set_kv("last_sync_utc", datetime.now(timezone.utc).isoformat())
    return True, len(sent_lines)


def flush_pending() -> bool:
    ok, _count = flush_once()
    return ok


async def flush_forever() -> None:
    delay = 3
    while not (DATA_DIR / "STOP").exists():
        try:
            ok, count = await asyncio.to_thread(flush_once)
        except Exception as exc:
            print(f"[tb] flush failed: {type(exc).__name__}")
            ok, count = False, 0
        sleep_for = delay
        if ok:
            if count:
                print(f"[tb] flushed {count}")
            delay = 3
        else:
            print(f"[tb] offline, queued {count}")
            delay = min(delay * 2, 30)
        await asyncio.sleep(sleep_for)


async def query_endpoint(name: str, params: dict) -> list[dict] | None:
    if MOCK_TINYBIRD:
        return None
    if not TINYBIRD_TOKEN or not TINYBIRD_HOST:
        return None
    try:
        api = create_tinybird_api({"base_url": TINYBIRD_HOST, "token": TINYBIRD_TOKEN})
        result = await asyncio.to_thread(api.query, name, params)
    except Exception as exc:
        print(f"[tb] query {name} failed: {type(exc).__name__}")
        return None
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, list) else None
