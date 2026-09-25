"""HTTP skeleton. Pages arrive later. This process only serves health and status."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.budget import remaining
from app.config import (
    HST,
    MOCK_FLUX,
    MOCK_LLM,
    MOCK_NIMBLE,
    MOCK_TINYBIRD,
)
from app.connectivity import check_once, is_online, monitor_forever
from app import db
from app.llm import llm_ok
from app.tinybird_client import flush_forever

_TABLES = (
    "storm_updates",
    "zips",
    "stores",
    "essentials",
    "products",
    "stock_checks",
    "forecasts",
    "official_links",
    "agent_steps",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    await check_once()
    flush_task = asyncio.create_task(flush_forever())
    monitor_task = asyncio.create_task(monitor_forever())
    yield
    flush_task.cancel()
    monitor_task.cancel()


app = FastAPI(title="Storm Supply Scout", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web"), name="static")


def _hst(value: str | None) -> str | None:
    if not value:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(HST).strftime("%Y-%m-%d %H:%M HST")


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "llm": await llm_ok(),
        "online": is_online(),
        "mocks": {
            "nimble": MOCK_NIMBLE,
            "tinybird": MOCK_TINYBIRD,
            "flux": MOCK_FLUX,
            "llm": MOCK_LLM,
        },
    }


@app.get("/api/status")
async def status() -> dict:
    counts = {}
    for table in _TABLES:
        rows = db.query(f"SELECT COUNT(*) AS n FROM {table}")
        counts[table] = rows[0]["n"]
    steps = db.query("SELECT * FROM agent_steps ORDER BY step DESC LIMIT 1")
    phases = db.query("SELECT zip, area, phase, phase_locked FROM zips ORDER BY priority DESC, zip")
    return {
        "counts": counts,
        "budgets": {"nimble": remaining("nimble"), "flux": remaining("flux")},
        "last_agent_step": steps[0] if steps else None,
        "phases": phases,
        "last_sync_hst": _hst(db.get_kv("last_sync_utc")),
    }
