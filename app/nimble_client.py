"""Nimble search, extract, templates, and background research.

Sync SDK calls run in a thread. Template stock checks start on one cycle
and are collected on the next: run_template_start, then run_template_result.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from nimble_python import Nimble
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from app.budget import BudgetExceeded, spend
from app.config import MOCK_NIMBLE, NIMBLE_API_KEY
from app import db

_MOCKS = Path(__file__).resolve().parent / "mocks"
_client: Nimble | None = None
_RUNNING = {"pending", "queued", "in_progress"}


def _nimble() -> Nimble:
    global _client
    if _client is None:
        if not NIMBLE_API_KEY:
            raise RuntimeError("NIMBLE_API_KEY is not set")
        _client = Nimble(api_key=NIMBLE_API_KEY, timeout=30.0)
    return _client


def _load(name: str):
    return json.loads((_MOCKS / name).read_text(encoding="utf-8"))


def _dump(model) -> dict:
    if isinstance(model, dict):
        return model
    return model.model_dump(mode="json")


def _needs_localization(params: dict) -> bool:
    """Docs require localization when params include zip_code or store_id.

    Home Depot's published input uses zipcode for that same location slot.
    """
    keys = {str(key).lower() for key in params}
    return bool(keys & {"zip_code", "zipcode", "store_id"})


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_not_exception_type(BudgetExceeded),
    reraise=True,
)
def _call(fn, paid: bool):
    if paid:
        spend("nimble")
    return fn()


async def _run(fn):
    """A billable call: costs one unit of the Nimble budget."""
    return await asyncio.to_thread(_call, fn, True)


async def _poll(fn):
    """Checking on an already-started task is free, so it does not spend budget."""
    return await asyncio.to_thread(_call, fn, False)


def trim_markdown(md: str, keywords: list[str], max_chars: int = 2000) -> str:
    """Keep paragraphs that mention the keywords, then fill up to max_chars."""
    paragraphs = [part.strip() for part in md.split("\n\n") if part.strip()]
    needles = [word.lower() for word in keywords if word]
    hits = [part for part in paragraphs if any(word in part.lower() for word in needles)]
    rest = [part for part in paragraphs if part not in hits]
    chosen: list[str] = []
    used = 0
    for part in hits + rest:
        if used >= max_chars:
            break
        piece = part[: max_chars - used]
        chosen.append(piece)
        used += len(piece) + 2
    return "\n\n".join(chosen)[:max_chars]


async def search(
    query: str,
    *,
    focus: str | None = None,
    depth: str = "lite",
    max_results: int = 8,
    time_range: str | None = None,
) -> list[dict]:
    if MOCK_NIMBLE:
        return list(_load("nimble_search.json"))
    # Focus modes require search_depth="lite".
    search_depth = "lite" if focus else depth

    def _go():
        kwargs = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
        }
        if focus:
            kwargs["focus"] = focus
        if time_range:
            kwargs["time_range"] = time_range
        return _nimble().search(**kwargs)

    response = await _run(_go)
    return [
        {"title": item.title, "url": item.url, "description": item.description}
        for item in response.results
    ]


async def extract(url: str, *, render_js: bool = False) -> str:
    if MOCK_NIMBLE:
        return str(_load("nimble_extract.json")["markdown"])

    def _go():
        kwargs = {"url": url, "formats": ["markdown"]}
        if render_js:
            kwargs["render"] = True
        return _nimble().extract.run(**kwargs)

    response = await _run(_go)
    return response.data.markdown or ""


async def extract_batch(urls: list[str]) -> dict[str, str]:
    if MOCK_NIMBLE:
        sample = str(_load("nimble_extract.json")["markdown"])
        return {url: sample for url in urls}
    if not urls:
        return {}

    def _submit():
        return _nimble().extract.batch(
            inputs=[{"url": url} for url in urls],
            shared_inputs={"formats": ["markdown"], "render": True},
        )

    created = await _run(_submit)
    pending: dict[str, str] = {}
    for index, task in enumerate(created.tasks):
        url = urls[index] if index < len(urls) else ""
        raw = task.input if isinstance(task.input, dict) else {}
        if isinstance(raw, dict) and raw.get("url"):
            url = str(raw["url"])
        pending[task.id] = url
    found: dict[str, str] = {url: "" for url in urls}
    deadline = time.monotonic() + 25
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(2)
        for task_id, url in list(pending.items()):
            state = await _task_state(task_id)
            if state is None:
                continue
            if state.get("state") == "error":
                found[url] = ""
                del pending[task_id]
                continue
            payload = await _poll(lambda task_id=task_id: _nimble().tasks.results(task_id))
            found[url] = _markdown(payload)
            del pending[task_id]
    return found


async def run_template(name: str, params: dict) -> dict:
    if MOCK_NIMBLE:
        payload = dict(_load("nimble_template.json"))
        payload["template"] = name
        return payload

    def _go():
        kwargs = {"template": name, "params": params}
        if _needs_localization(params):
            kwargs["localization"] = True
        return _nimble().extract.templates.run(**kwargs)

    return _dump(await _run(_go))


async def run_template_start(name: str, params: dict) -> str:
    """POST /v2/extract/templates/async. Returns the task id. Does not wait."""
    if MOCK_NIMBLE:
        return f"mock-task-{name}"

    def _go():
        kwargs = {"template": name, "params": params}
        if _needs_localization(params):
            kwargs["localization"] = True
        return _nimble().extract.templates.async_(**kwargs)

    response = await _run(_go)
    task = response.task or {}
    task_id = task.get("id") or task.get("task_id")
    if not task_id:
        raise RuntimeError(f"template async response had no task id; keys={sorted(task)}")
    return str(task_id)


async def run_template_result(task_id: str) -> dict | None:
    """None while the task is pending, queued, or in progress."""
    if MOCK_NIMBLE:
        return dict(_load("nimble_template.json"))
    state = await _task_state(task_id)
    if state is None:
        return None
    if state.get("state") == "error":
        return {"error": state.get("error") or "template task failed", "state": "error"}
    return await _poll(lambda: _nimble().tasks.results(task_id))


async def _task_state(task_id: str) -> dict | None:
    response = await _poll(lambda: _nimble().tasks.get(task_id))
    task = response.task
    if task.state in _RUNNING:
        return None
    return {"state": task.state, "error": task.error}


async def research_start(task: str) -> str:
    if MOCK_NIMBLE:
        return "mock-run-hilo"

    def _go():
        return _nimble().agents.run(input=task, effort="low")

    response = await _run(_go)
    db.set_kv(f"nimble_run:{response.id}", response.web_search_agent_id)
    return response.id


async def research_result(run_id: str) -> dict | None:
    if MOCK_NIMBLE:
        return dict(_load("nimble_research.json"))
    agent_id = db.get_kv(f"nimble_run:{run_id}")
    if not agent_id:
        return {"error": "unknown run"}

    def _status():
        return _nimble().agents.runs.get(run_id, agent_id=agent_id)

    status = await _poll(_status)
    if status.is_active:
        return None
    if status.status in {"failed", "cancelled"}:
        message = status.error.message if status.error else status.status
        return {"error": message}

    def _result():
        return _nimble().agents.runs.result(run_id, agent_id=agent_id)

    try:
        return _dump(await _poll(_result))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _markdown(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and isinstance(data.get("markdown"), str):
        return data["markdown"]
    if isinstance(payload, dict) and isinstance(payload.get("markdown"), str):
        return str(payload["markdown"])
    return ""
