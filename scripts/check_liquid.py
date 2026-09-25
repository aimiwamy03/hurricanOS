"""One chat and one JSON chat against the local Liquid server.

Prints latency and tokens/sec. If the server is down, prints why and
uses a mock reply so the rest of the project can keep moving.
"""

from __future__ import annotations

import json
import time

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from common import env, mock_on

SYSTEM = (
    "Help people in Hurricane Nolo's path on Hawaii's Big Island and Maui "
    "get essential supplies safely, using live verified data. Never send "
    "people into danger. Always point to official sources."
)


class StockHint(BaseModel):
    essential: str
    zip: str
    advice: str


def tokens_per_sec(completion_tokens: int | None, seconds: float) -> str:
    if not completion_tokens or seconds <= 0:
        return "n/a (server did not report completion tokens)"
    return f"{completion_tokens / seconds:.1f}"


def reply_text(message) -> str:
    """LFM2.5 thinks first. The final answer is in content; if that is empty, use reasoning."""
    content = (message.content or "").strip()
    if content:
        return content
    extra = getattr(message, "model_extra", None) or {}
    reasoning = getattr(message, "reasoning", None) or extra.get("reasoning") or ""
    return reasoning.strip()


def parse_json_object(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object", text, 0)
    return json.loads(text[start : end + 1])


def chat(client: OpenAI, model: str, user: str) -> tuple[str, int | None, float]:
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=200,
        temperature=0.2,
    )
    seconds = time.perf_counter() - started
    text = reply_text(response.choices[0].message)
    usage = response.usage
    completion_tokens = usage.completion_tokens if usage else None
    return text, completion_tokens, seconds


def chat_json(client: OpenAI, model: str) -> tuple[StockHint, int | None, float]:
    user = (
        "Return only a JSON object with keys essential, zip, advice. "
        'essential must be "flashlight", zip must be "96720", '
        "advice must say to check official sources before traveling. "
        "No markdown."
    )
    total_tokens = 0
    saw_usage = False
    started = time.perf_counter()
    last_error = ""
    for _attempt in range(2):
        prompt = user if not last_error else f"{user}\n\nYour last reply failed validation: {last_error}"
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM + " Reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.2,
        )
        if response.usage and response.usage.completion_tokens:
            total_tokens += response.usage.completion_tokens
            saw_usage = True
        raw = reply_text(response.choices[0].message)
        try:
            parsed = StockHint.model_validate(parse_json_object(raw))
            seconds = time.perf_counter() - started
            return parsed, total_tokens if saw_usage else None, seconds
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = f"{exc}; raw={raw[:240]!r}"
    raise RuntimeError(f"chat_json failed after one retry: {last_error}")


def run_mock(reason: str) -> None:
    print(f"MOCK LLM — {reason}")
    print("chat: Check the National Weather Service before any store trip. Flashlights are a priority in 96720.")
    print('chat_json: {"essential":"flashlight","zip":"96720","advice":"Check official sources before traveling."}')
    print("latency: n/a (mock)")
    print("tokens/sec: n/a (mock)")


def main() -> None:
    use_mock, reason = mock_on("MOCK_LLM")
    if use_mock:
        run_mock(reason)
        return

    base_url = env("LOCAL_LLM_BASE_URL", "http://localhost:8080/v1")
    model = env("LOCAL_LLM_MODEL", "LiquidAI/LFM2.5-8B-A1B-MLX-4bit")
    client = OpenAI(base_url=base_url, api_key="local", timeout=120.0)
    try:
        text, chat_tokens, chat_seconds = chat(
            client,
            model,
            "In one sentence, where should someone in Hilo confirm hurricane instructions?",
        )
    except Exception as exc:
        run_mock(f"could not reach Liquid at {base_url}: {exc}")
        print("Start the server after the model download finishes:")
        print(
            ".venv/bin/mlx_lm.server "
            "--model LiquidAI/LFM2.5-8B-A1B-MLX-4bit --host 127.0.0.1 --port 8080"
        )
        return

    print(f"chat: {text.strip()}")
    print(f"chat latency: {chat_seconds:.2f}s")
    print(f"chat tokens/sec: {tokens_per_sec(chat_tokens, chat_seconds)}")
    try:
        parsed, json_tokens, json_seconds = chat_json(client, model)
    except Exception as exc:
        print(f"chat_json failed: {exc}")
        return

    print(f"chat_json: {parsed.model_dump()}")
    print(f"chat_json latency: {json_seconds:.2f}s")
    print(f"chat_json tokens/sec: {tokens_per_sec(json_tokens, json_seconds)}")


if __name__ == "__main__":
    main()
