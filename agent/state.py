"""Checkpoint in data/state.json. Written atomically so a crash never corrupts it."""

from __future__ import annotations

import json
import os

from app.config import DATA_DIR
from agent.util import now_iso

STATE_FILE = DATA_DIR / "state.json"


def _fresh() -> dict:
    return {
        "step": 0,
        "started_utc": now_iso(),
        "last_storm_utc": None,
        "last_store_scan_utc": {},
        "last_shelter_research_utc": None,
        # Nimble template tasks take minutes: {task_id: {kind, meta, started_utc}}
        "pending_tasks": {},
        "pending_research": [],
    }


def load_state() -> dict:
    state = _fresh()
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[state] could not read state.json ({type(exc).__name__}); starting fresh")
    return state


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, STATE_FILE)
