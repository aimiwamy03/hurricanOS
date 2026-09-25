"""The autonomous loop: python -m agent.agent   (test one cycle: --once)

Stop cleanly with: touch data/STOP. Restart resumes from data/state.json.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time

from app import connectivity, db, nimble_client, tinybird_client
from app.budget import remaining
from app.config import CYCLE_SECONDS, DATA_DIR, ESSENTIALS, REGION_ZIPS, STORM_NAME
from app.prompts import SHELTER_TASK
from app.safety import is_official
from agent import forecast, stock, storm, stores
from agent.state import load_state, save_state
from agent.util import log, minutes_since, now_iso

STOP = DATA_DIR / "STOP"
STORE_RESCAN_MINUTES = 120
SHELTER_RESEARCH_MINUTES = 180
TASK_TIMEOUT_MINUTES = 30
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def log_step(state: dict, phase: str, action: str, summary: str, duration_ms: int) -> None:
    db.execute(
        "INSERT OR REPLACE INTO agent_steps (step, ts_utc, phase, action, summary, duration_ms) VALUES (?, ?, ?, ?, ?, ?)",
        (state["step"], now_iso(), phase, action, summary, duration_ms),
    )
    log("agent", f"step {state['step']}: {summary}")


def _overall_phase() -> str:
    phases = {row["phase"] for row in db.query("SELECT phase FROM zips")}
    for phase in ("DURING", "AFTER", "BEFORE"):
        if phase in phases:
            return phase
    return "BEFORE"


async def collect_tasks(state: dict) -> dict:
    """Pick up finished Nimble template tasks (polling is free). Never waits."""
    counts = {"stock": 0, "maps": 0, "new_stores": 0, "statuses": {}}
    for task_id, task in list(state["pending_tasks"].items()):
        age = minutes_since(task["started_utc"]) or 0
        try:
            payload = await nimble_client.run_template_result(task_id)
        except Exception as exc:
            log("tasks", f"poll {task_id[:8]} failed: {type(exc).__name__}")
            payload = None
        if payload is None:
            if age > TASK_TIMEOUT_MINUTES:
                log("tasks", f"dropping {task['kind']} task {task_id[:8]} after {age:.0f} min")
                del state["pending_tasks"][task_id]
            continue
        try:
            if task["kind"] == "stock":
                status = stock.record_result(task["meta"], payload)
                counts["stock"] += 1
                key = task["meta"]["essential_key"]
                counts["statuses"].setdefault(status, []).append(key)
            elif task["kind"] == "maps":
                counts["new_stores"] += stores.record_maps_result(task["meta"], payload)
                counts["maps"] += 1
        except Exception as exc:
            log("tasks", f"could not record {task['kind']} {task_id[:8]}: {type(exc).__name__}: {exc}")
        del state["pending_tasks"][task_id]
        save_state(state)
    return counts


async def collect_research(state: dict) -> int:
    """Finished Web Search Agent runs -> official_links(kind='shelter')."""
    saved = 0
    for run in list(state["pending_research"]):
        result = await nimble_client.research_result(run["run_id"])
        if result is None:
            if (minutes_since(run["started_utc"]) or 0) > 60:
                state["pending_research"].remove(run)
            continue
        state["pending_research"].remove(run)
        if result.get("error"):
            log("research", f"{run['kind']} run failed: {result['error']}")
            continue
        text = json.dumps(result, default=str)
        db.set_kv(f"research:{run['kind']}", text[:20000])
        for url in dict.fromkeys(_URL.findall(text)):
            url = url.rstrip(".,;\\")
            if db.query("SELECT 1 FROM official_links WHERE url = ?", (url,)):
                continue
            kind = "shelter" if is_official(url) else "news"
            db.insert("official_links", {"title": f"{run['kind']} source", "url": url, "kind": kind, "found_ts": now_iso()})
            saved += 1
        log("research", f"{run['kind']} run finished, {saved} new links")
    save_state(state)
    return saved


async def maybe_start_shelter_research(state: dict) -> None:
    age = minutes_since(state.get("last_shelter_research_utc"))
    if age is not None and age < SHELTER_RESEARCH_MINUTES:
        return
    if any(run["kind"] == "shelters" for run in state["pending_research"]):
        return
    run_id = await nimble_client.research_start(SHELTER_TASK.format(storm=STORM_NAME))
    state["pending_research"].append({"run_id": run_id, "kind": "shelters", "meta": {}, "started_utc": now_iso()})
    state["last_shelter_research_utc"] = now_iso()
    save_state(state)
    log("research", f"started shelter research {run_id[:8]}")


def zips_needing_store_scan(state: dict) -> list[str]:
    due = []
    for zip_code in REGION_ZIPS:
        age = minutes_since(state["last_store_scan_utc"].get(zip_code))
        if age is None or age > STORE_RESCAN_MINUTES:
            due.append(zip_code)
    return due


def summary_sentence(changes: list[str], started: int, counts: dict, pending: int) -> str:
    names = {item["key"]: item["name"] for item in ESSENTIALS}
    parts = []
    if changes:
        parts.append("Phase change: " + "; ".join(changes) + ".")
    if counts["stock"]:
        words = {"in_stock": "in stock", "low": "few options", "out": "none found", "unknown": "not matched"}
        detail = ", ".join(
            f"{len(keys)} {words.get(status, status)}" for status, keys in sorted(counts["statuses"].items())
        )
        parts.append(f"Recorded {counts['stock']} stock checks ({detail}).")
        low_or_out = counts["statuses"].get("low", []) + counts["statuses"].get("out", [])
        if low_or_out:
            parts.append("Few options found: " + ", ".join(sorted({names.get(k, k) for k in low_or_out})) + ".")
    if counts["new_stores"]:
        parts.append(f"Found {counts['new_stores']} new stores.")
    parts.append(f"Started {started} new checks; {pending} still running at Nimble.")
    return " ".join(parts)


async def cycle(state: dict) -> None:
    if STOP.exists():
        raise SystemExit("data/STOP found")
    began = time.perf_counter()
    state["step"] += 1
    save_state(state)
    if not await connectivity.check_once():
        log_step(state, _overall_phase(), "offline", "Offline — pausing web work.", 0)
        return

    changes: list[str] = []
    try:
        _assessment, changes = await storm.run_once()
        state["last_storm_utc"] = now_iso()
    except Exception as exc:
        log("storm", f"stage failed: {type(exc).__name__}: {exc}")
        storm.ensure_zips()
    save_state(state)

    counts = {"stock": 0, "maps": 0, "new_stores": 0, "statuses": {}}
    try:
        counts = await collect_tasks(state)
    except Exception as exc:
        log("tasks", f"stage failed: {type(exc).__name__}: {exc}")

    for zip_code in zips_needing_store_scan(state):
        try:
            state["pending_tasks"].update(await stores.start_scan(zip_code))
            state["last_store_scan_utc"][zip_code] = now_iso()
        except Exception as exc:
            log("stores", f"stage failed for {zip_code}: {type(exc).__name__}: {exc}")
        save_state(state)

    try:
        await collect_research(state)
        await maybe_start_shelter_research(state)
    except Exception as exc:
        log("research", f"stage failed: {type(exc).__name__}: {exc}")

    started = 0
    try:
        budget = stock.budget_this_cycle(remaining("nimble"), CYCLE_SECONDS)
        pending_pairs = {
            (task["meta"]["zip"], task["meta"]["essential_key"])
            for task in state["pending_tasks"].values()
            if task["kind"] == "stock"
        }
        for store, essential in stock.pick_checks(budget, pending_pairs):
            submitted = await stock.start_check(store, essential)
            if submitted:
                task_id, task = submitted
                state["pending_tasks"][task_id] = task
                started += 1
                save_state(state)  # save each id right away so a restart never pays twice
            await asyncio.sleep(0.5)
        log("stock", f"started {started} checks (budget {budget}, {remaining('nimble')} Nimble calls left)")
    except Exception as exc:
        log("stock", f"stage failed: {type(exc).__name__}: {exc}")

    try:
        await forecast.run_forecast()
    except Exception as exc:
        log("forecast", f"stage failed: {type(exc).__name__}: {exc}")

    # Phase 5 inserts visuals here.
    duration_ms = int((time.perf_counter() - began) * 1000)
    summary = summary_sentence(changes, started, counts, len(state["pending_tasks"]))
    log_step(state, _overall_phase(), "cycle", summary, duration_ms)
    save_state(state)


async def main(once: bool) -> None:
    db.init_db()
    state = load_state()
    log("agent", f"starting at step {state['step'] + 1}, {len(state['pending_tasks'])} tasks pending from before")
    # The agent is the only process that sends the event queue to Tinybird.
    flusher = asyncio.create_task(tinybird_client.flush_forever())
    try:
        while True:
            try:
                await cycle(state)
            except SystemExit:
                raise
            except Exception as exc:
                log("agent", f"cycle crashed, continuing: {type(exc).__name__}: {exc}")
            if once:
                break
            wait = CYCLE_SECONDS + random.uniform(-30, 30)
            log("agent", f"sleeping {wait:.0f}s")
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if STOP.exists():
                    raise SystemExit("data/STOP found")
                await asyncio.sleep(5)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError) as exc:
        log("agent", f"stopping ({type(exc).__name__}: {exc}); state saved at step {state['step']}")
    finally:
        flusher.cancel()
        await asyncio.to_thread(tinybird_client.flush_once)
        save_state(state)


if __name__ == "__main__":
    try:
        asyncio.run(main("--once" in sys.argv))
    except KeyboardInterrupt:
        pass
