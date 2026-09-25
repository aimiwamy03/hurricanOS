"""Pictograms from BFL FLUX. A failure returns None and never stops the agent."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
from PIL import Image

from app.budget import BudgetExceeded, spend
from app.config import BFL_API_KEY, BFL_BASE_URL, BFL_IMAGE_ENDPOINT, MOCK_FLUX


async def generate(prompt: str, out_path: str) -> str | None:
    path = Path(out_path)
    if MOCK_FLUX:
        return _grey_png(path)
    try:
        spend("flux")
        return await asyncio.to_thread(_generate_sync, prompt, path)
    except BudgetExceeded:
        print("[flux] budget exhausted")
        return None
    except Exception as exc:
        print(f"[flux] failed: {type(exc).__name__}")
        return None


def _grey_png(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (256, 256), (180, 180, 180)).save(path, "PNG")
    return str(path)


def _generate_sync(prompt: str, path: Path) -> str:
    headers = {"x-key": BFL_API_KEY, "Content-Type": "application/json"}
    submitted = httpx.post(
        f"{BFL_BASE_URL}/{BFL_IMAGE_ENDPOINT}",
        headers=headers,
        json={"prompt": prompt, "width": 1024, "height": 1024},
        timeout=30.0,
    )
    submitted.raise_for_status()
    polling_url = submitted.json()["polling_url"]
    deadline = time.monotonic() + 120
    result: dict = {}
    while time.monotonic() < deadline:
        polled = httpx.get(polling_url, headers={"x-key": BFL_API_KEY}, timeout=30.0)
        polled.raise_for_status()
        body = polled.json()
        status = body.get("status", "")
        if status == "Ready":
            result = body.get("result") or {}
            break
        if status in {"Error", "Failed"}:
            raise RuntimeError("FLUX reported an error")
        time.sleep(1.5)
    else:
        raise TimeoutError("FLUX still pending after 120s")
    sample = result.get("sample")
    if not sample:
        raise RuntimeError("FLUX Ready response had no sample URL")
    image = httpx.get(sample, timeout=60.0)
    image.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image.content)
    return str(path)
