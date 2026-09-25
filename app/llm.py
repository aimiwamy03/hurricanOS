"""Local Liquid chat. The model returns text. Code decides what to do with it."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL, MOCK_LLM
from app.prompts import GOAL_ANCHOR

client = AsyncOpenAI(base_url=LOCAL_LLM_BASE_URL, api_key="local", timeout=90)
_gate = asyncio.Semaphore(1)
_MOCKS = Path(__file__).resolve().parent / "mocks"
_ping_at = 0.0
_ping_ok = False


class LLMJSONError(RuntimeError):
    """The model did not return JSON that matches the schema."""


def _system(system: str, extra: str = "") -> str:
    parts = [GOAL_ANCHOR, system.strip(), extra.strip()]
    return "\n\n".join(part for part in parts if part)


def _parse_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")].strip()
    start = stripped.find("{")
    if start == -1:
        raise json.JSONDecodeError("no JSON object", stripped, 0)
    # First complete object only; the model sometimes keeps writing after it.
    obj, _end = json.JSONDecoder().raw_decode(stripped[start:])
    return obj


def _mock_text() -> str:
    path = _MOCKS / "llm_chat.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("text"):
            return str(payload["text"])
    return "Check the National Weather Service before traveling for supplies."


def _mock_crisis_plan(user: str) -> BaseModel:
    """Mock Liquid: fire → water, hail → shelter, otherwise a small storm kit."""
    from app.models import CrisisResource, CrisisResourcePlan

    text = user.lower()
    resources: list[CrisisResource] = []
    if re.search(r"\b(wild)?fires?\b|\bsmoke\b", text):
        resources.append(
            CrisisResource(
                name="Drinking water",
                kind="retail",
                search_terms="bottled water",
                why="Fires disrupt drinking water and force extra hydration.",
            )
        )
        resources.append(
            CrisisResource(
                name="N95 mask",
                kind="retail",
                search_terms="N95 mask",
                why="Smoke and ash make outdoor air unsafe.",
            )
        )
    if re.search(r"\bhail", text):
        resources.append(
            CrisisResource(
                name="Emergency shelter",
                kind="shelter",
                search_terms="emergency shelter",
                why="Hail can damage roofs and make staying in place unsafe.",
            )
        )
        resources.append(
            CrisisResource(
                name="Tarp",
                kind="retail",
                search_terms="tarp",
                why="Cover broken windows or a damaged roof.",
            )
        )
    if not resources:
        resources = [
            CrisisResource(name="Drinking water", kind="retail", search_terms="bottled water", why="Emergency hydration"),
            CrisisResource(name="Flashlight", kind="retail", search_terms="flashlight", why="Light during outages"),
            CrisisResource(
                name="Emergency shelter",
                kind="shelter",
                search_terms="emergency shelter",
                why="A safer place if ordered to evacuate",
            ),
        ]
    return CrisisResourcePlan(
        safety_note="Follow official instructions and do not travel into dangerous conditions.",
        resources=resources,
    )


def _mock_model(schema: type[BaseModel], user: str = "") -> BaseModel:
    if schema.__name__ == "CrisisResourcePlan":
        return _mock_crisis_plan(user)
    path = _MOCKS / f"llm_{schema.__name__}.json"
    if path.exists():
        return schema.model_validate_json(path.read_text(encoding="utf-8"))
    sample = {
        "AreaPhase": {"area": "Hilo", "proposed_phase": "BEFORE", "confidence": 0.4},
        "StormAssessment": {
            "summary": "Hurricane Nolo is being watched for Hawaii. Confirm with the National Weather Service.",
            "track_shift": False,
            "expected_onset_hst": None,
            "areas": [{"area": "Hilo", "proposed_phase": "BEFORE", "confidence": 0.4}],
            "official_links": [],
        },
        "StockResult": {
            "status": "unknown",
            "price": None,
            "confidence": 0.0,
            "level": "zip",
            "source_url": None,
        },
        "Explanation": {"text": "No live stock check has confirmed this item yet."},
        "AskAnswer": {"answer": "I only have the last saved snapshot. Check official sources.", "cited_ids": []},
        "CrisisDiscovery": {
            "crises": [
                {
                    "title": "Hurricane Nolo near Hawaii",
                    "crisis_type": "hurricane",
                    "location": "Hawaii",
                    "summary": "A hurricane is being monitored near Hawaii.",
                    "source_ids": [1, 2],
                },
                {
                    "title": "Wildfire spreading near populated areas",
                    "crisis_type": "wildfire",
                    "location": "California",
                    "summary": "An active wildfire is threatening homes and air quality.",
                    "source_ids": [3],
                },
                {
                    "title": "Severe hailstorm damaging buildings",
                    "crisis_type": "hail",
                    "location": "Texas",
                    "summary": "Large hail is breaking windows and damaging roofs.",
                    "source_ids": [4],
                },
            ]
        },
    }
    if schema.__name__ in sample:
        return schema.model_validate(sample[schema.__name__])
    raise LLMJSONError(f"no mock fixture for {schema.__name__}")


async def _complete(system: str, user: str, max_tokens: int, prefill: str = "") -> str:
    """LFM2.5 is a reasoning model and the chat endpoint cannot turn thinking off, so it
    can spend the whole token budget thinking. The raw completions endpoint lets us write
    the chat template ourselves with an empty <think></think>, so it answers directly."""
    prompt = (
        f"<|startoftext|><|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n<think></think>\n{prefill}"
    )
    started = time.perf_counter()
    async with _gate:
        response = await client.completions.create(
            model=LOCAL_LLM_MODEL,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.2,
            stop=["<|im_end|>"],
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    usage = response.usage
    tokens = 0
    if usage is not None:
        tokens = usage.completion_tokens or usage.total_tokens or 0
    print(f"[llm] {tokens} tok in {elapsed_ms} ms")
    return (prefill + (response.choices[0].text or "")).strip()


async def chat(system: str, user: str, max_tokens: int = 400) -> str:
    if MOCK_LLM:
        return _mock_text()
    return await _complete(_system(system), user, max_tokens)


async def chat_json(
    system: str,
    user: str,
    schema: type[BaseModel],
    example: str | None = None,
    max_tokens: int = 600,
    attempts: int = 3,
) -> BaseModel:
    """example: a short JSON sample of the expected shape. Small models follow it
    better than a full JSON Schema. attempts=1 when a caller has its own fallback."""
    if MOCK_LLM:
        return _mock_model(schema, user)
    shape = example or json.dumps(schema.model_json_schema())
    instruction = f"Reply with ONLY one JSON object shaped like this. No prose, no code fences.\n{shape}"
    last_error = ""
    for attempt in range(attempts):
        prompt = user if not last_error else f"{user}\n\nYour last output failed: {last_error}. Fix it."
        raw = await _complete(_system(system, instruction), prompt, max_tokens, prefill="{")
        try:
            return schema.model_validate(_parse_object(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            if attempt == attempts - 1:
                raise LLMJSONError(last_error) from exc
    raise LLMJSONError(last_error)


async def llm_ok() -> bool:
    """One-token ping, cached for 60 seconds."""
    global _ping_at, _ping_ok
    now = time.monotonic()
    if _ping_at and now - _ping_at < 60:
        return _ping_ok
    if MOCK_LLM:
        _ping_ok = True
    else:
        try:
            await chat("Reply with ok.", "ping", max_tokens=1)
            _ping_ok = True
        except Exception as exc:
            print(f"[llm] ping failed: {type(exc).__name__}")
            _ping_ok = False
    _ping_at = now
    return _ping_ok
