"""Local Liquid chat. The model returns text. Code decides what to do with it."""

from __future__ import annotations

import asyncio
import json
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


def _reply_text(message) -> str:
    content = (message.content or "").strip()
    if content:
        return content
    extra = getattr(message, "model_extra", None) or {}
    reasoning = getattr(message, "reasoning", None) or extra.get("reasoning") or ""
    return str(reasoning).strip()


def _parse_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object", stripped, 0)
    return json.loads(stripped[start : end + 1])


def _mock_text() -> str:
    path = _MOCKS / "llm_chat.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("text"):
            return str(payload["text"])
    return "Check the National Weather Service before traveling for supplies."


def _mock_model(schema: type[BaseModel]) -> BaseModel:
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
    }
    if schema.__name__ in sample:
        return schema.model_validate(sample[schema.__name__])
    raise LLMJSONError(f"no mock fixture for {schema.__name__}")


async def _complete(system: str, user: str, max_tokens: int) -> str:
    started = time.perf_counter()
    async with _gate:
        response = await client.chat.completions.create(
            model=LOCAL_LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    usage = response.usage
    tokens = 0
    if usage is not None:
        tokens = usage.completion_tokens or usage.total_tokens or 0
    print(f"[llm] {tokens} tok in {elapsed_ms} ms")
    return _reply_text(response.choices[0].message)


async def chat(system: str, user: str, max_tokens: int = 400) -> str:
    if MOCK_LLM:
        return _mock_text()
    return await _complete(_system(system), user, max_tokens)


async def chat_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if MOCK_LLM:
        return _mock_model(schema)
    instruction = (
        "Return ONLY a JSON object matching this JSON Schema. No prose, no code fences.\n"
        + json.dumps(schema.model_json_schema())
    )
    last_error = ""
    for attempt in range(2):
        prompt = user if not last_error else f"{user}\n\nYour last output failed: {last_error}. Fix it."
        raw = await _complete(_system(system, instruction), prompt, 400)
        try:
            return schema.model_validate(_parse_object(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            if attempt == 1:
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
