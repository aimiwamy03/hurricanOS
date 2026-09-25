"""Print retailer template schemas, then run async Hilo stock checks.

Uses Nimble's documented template get, plus run_template_start / run_template_result.
Polls for up to 5 minutes. Does not print API keys.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nimble_python import Nimble

from app.config import NIMBLE_API_KEY
from app.nimble_client import run_template_result, run_template_start

TEMPLATES = ("homedepot_serp", "walmart_serp", "target_serp")
TERMS = ("flashlight", "D batteries", "tarp")
ZIP = "96720"
INTEREST = ("avail", "stock", "pickup", "inventory", "quantity", "fulfill", "store")
KEYWORD_NAMES = ("keyword", "query", "q", "search_query", "search")


def _short(value, limit: int = 400) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _find_key(node, name: str):
    if isinstance(node, dict):
        if name in node:
            return node[name]
        for value in node.values():
            found = _find_key(value, name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_key(value, name)
            if found is not None:
                return found
    return None


def _zip_keys(properties: dict) -> list[str]:
    keys = []
    for name in properties:
        lowered = name.lower()
        if "zip" in lowered or lowered in {"postal_code", "store_id"}:
            keys.append(name)
    return keys


def _schema_mentions_availability(schema: dict) -> bool:
    text = json.dumps(schema).lower()
    return any(word in text for word in ("avail", "stock", "pickup", "inventory", "fulfill"))


def inspect_template(client: Nimble, name: str) -> dict:
    detail = client.extract.templates.get(name)
    full = detail.model_dump(mode="json")
    version = full.get("published_version") or {}
    properties = (version.get("input_schema") or {}).get("properties") or {}
    output_schema = version.get("output_schema") or {}
    flags = _find_key(full, "feature_flags")
    zip_keys = _zip_keys(properties)
    print(f"\n=== {name} ===")
    print("input properties:")
    print(json.dumps(properties, indent=2, default=str)[:6000])
    print("output schema:")
    print(json.dumps(output_schema, indent=2, default=str)[:8000])
    print("feature_flags:")
    print(json.dumps(flags, indent=2, default=str) if flags is not None else "(none on the template payload)")
    print(f"accepts a ZIP: {bool(zip_keys)} {zip_keys}")
    print(f"output mentions store availability or pickup: {_schema_mentions_availability(output_schema)}")
    keyword = next((key for key in KEYWORD_NAMES if key in properties), None)
    return {
        "name": name,
        "properties": properties,
        "zip_keys": zip_keys,
        "keyword": keyword,
        "schema_has_availability": _schema_mentions_availability(output_schema),
    }


def _print_availability(payload) -> None:
    if not isinstance(payload, dict):
        print(_short(payload))
        return
    if payload.get("error"):
        print(f"  error: {_short(payload.get('error'))}")
    found = False

    def walk(node, path: str, depth: int) -> None:
        nonlocal found
        if depth > 6:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else str(key)
                if any(word in str(key).lower() for word in INTEREST):
                    found = True
                    print(f"  {here}: {_short(value)}")
                if isinstance(value, (dict, list)):
                    walk(value, here, depth + 1)
        elif isinstance(node, list) and node:
            walk(node[0], path + "[0]", depth + 1)

    walk(payload, "", 0)
    if not found:
        print("  (no availability-related fields)")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        parsing = data.get("parsing") if isinstance(data, dict) else None
        if parsing is not None:
            print(f"  parsing: {_short(parsing, 800)}")


async def start_all(inspected: list[dict], extra: dict[str, dict]) -> dict[str, dict]:
    """Submit every template and term up front so they queue in parallel."""
    pending: dict[str, dict] = dict(extra)
    for info in inspected:
        name = info["name"]
        keyword = info["keyword"]
        zip_key = info["zip_keys"][0] if info["zip_keys"] else None
        if not keyword or not zip_key:
            print(f"\n{name}: skipped live runs (keyword={keyword}, zip={zip_key})")
            continue
        for term in TERMS:
            if any(meta["template"] == name and meta["term"] == term for meta in pending.values()):
                continue
            task_id = await run_template_start(name, {keyword: term, zip_key: ZIP})
            pending[task_id] = {"template": name, "term": term, "began": time.monotonic()}
            print(f"started {name} {term!r} task {task_id}")
    _save_tasks(pending)
    return pending


def _save_tasks(pending: dict[str, dict]) -> None:
    """Keep task ids on disk so a restart collects them instead of resubmitting."""
    path = ROOT / "data" / "probe_tasks.json"
    wall = time.time()
    now = time.monotonic()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                task_id: {
                    "template": meta["template"],
                    "term": meta["term"],
                    "submitted_at": wall - (now - meta["began"]),
                }
                for task_id, meta in pending.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )


async def collect(pending: dict[str, dict], budget_seconds: int) -> list[dict]:
    reports: list[dict] = []
    deadline = time.monotonic() + budget_seconds
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(10)
        for task_id in list(pending.keys()):
            result = await run_template_result(task_id)
            if result is None:
                continue
            meta = pending.pop(task_id)
            seconds = round(time.monotonic() - meta["began"], 1)
            print(f"\n{meta['template']} {meta['term']!r} finished in {seconds}s")
            _print_availability(result)
            reports.append(
                {
                    "template": meta["template"],
                    "term": meta["term"],
                    "seconds": seconds,
                    "error": bool(isinstance(result, dict) and result.get("error")),
                }
            )
    for task_id, meta in pending.items():
        seconds = round(time.monotonic() - meta["began"], 1)
        print(f"\n{meta['template']} {meta['term']!r} still pending after {seconds}s (task {task_id})")
        reports.append(
            {"template": meta["template"], "term": meta["term"], "seconds": seconds, "pending": True}
        )
    return reports


async def main() -> None:
    if not NIMBLE_API_KEY:
        raise SystemExit("NIMBLE_API_KEY is not set")
    client = Nimble(api_key=NIMBLE_API_KEY, timeout=30.0)
    inspected = [inspect_template(client, name) for name in TEMPLATES]
    resume = _resume_tasks()
    if resume:
        print(f"\nresuming {len(resume)} task(s) already submitted")
    pending = await start_all(inspected, resume)
    reports = await collect(pending, 300)
    print("\n=== run times ===")
    print(json.dumps(reports, indent=2))


def _resume_tasks() -> dict[str, dict]:
    """Pick up task ids from an earlier run so we do not pay for them twice."""
    path = ROOT / "data" / "probe_tasks.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    now = time.monotonic()
    wall = time.time()
    resumed = {}
    for task_id, meta in raw.items():
        waited = max(0.0, wall - float(meta.get("submitted_at") or wall))
        resumed[str(task_id)] = {
            "template": meta["template"],
            "term": meta["term"],
            "began": now - waited,
        }
    return resumed


if __name__ == "__main__":
    asyncio.run(main())
