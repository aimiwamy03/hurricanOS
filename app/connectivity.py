"""Online means Nimble's API host answered. Any HTTP status counts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.config import DATA_DIR
from app import db

_online = False
_PROBE = "https://sdk.nimbleway.com"


def is_online() -> bool:
    return _online


async def check_once() -> bool:
    global _online
    online = False
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
            await client.head(_PROBE)
        online = True
    except httpx.HTTPError:
        online = False
    _online = online
    db.set_kv("online", "1" if online else "0")
    if online:
        db.set_kv("last_online_utc", datetime.now(timezone.utc).isoformat())
    return online


async def monitor_forever() -> None:
    while not (DATA_DIR / "STOP").exists():
        await asyncio.sleep(30)
        try:
            await check_once()
        except Exception as exc:
            print(f"[net] check failed: {type(exc).__name__}")
