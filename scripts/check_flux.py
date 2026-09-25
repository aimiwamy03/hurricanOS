"""Generate one flashlight pictogram, or a placeholder if FLUX is unavailable.

Documented flow: POST /v1/<model>, poll the returned polling_url with the
x-key header, download result.sample immediately (the URL expires).
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from common import ROOT, env, mock_on

PROMPT = (
    "flat pictogram icon of a flashlight, bold simple shapes, high contrast, "
    "white background, no text, no letters"
)


def placeholder(path: Path, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((230, 80, 282, 250), radius=16, fill="#F5C542", outline="#111111", width=8)
    draw.rounded_rectangle((214, 250, 298, 430), radius=18, fill="#4A4A4A", outline="#111111", width=8)
    draw.ellipse((246, 110, 266, 130), fill="#FFF6CC")
    image.save(path, "PNG")
    print(f"PLACEHOLDER — {reason}")
    print(f"saved {path}")


def main() -> None:
    out = ROOT / "data" / "images" / "flashlight.png"
    use_mock, reason = mock_on("MOCK_FLUX", "BFL_API_KEY")
    if use_mock:
        placeholder(out, reason)
        return

    base = env("BFL_BASE_URL", "https://api.bfl.ai/v1").rstrip("/")
    endpoint = env("BFL_IMAGE_ENDPOINT", "flux-2-pro")
    headers = {"x-key": env("BFL_API_KEY"), "Content-Type": "application/json"}
    try:
        submitted = httpx.post(
            f"{base}/{endpoint}",
            headers=headers,
            json={"prompt": PROMPT, "width": 1024, "height": 1024},
            timeout=30.0,
        )
        print(f"submit HTTP {submitted.status_code}")
        submitted.raise_for_status()
        payload = submitted.json()
        polling_url = payload["polling_url"]
        deadline = time.time() + 90
        status = "Pending"
        result = {}
        while time.time() < deadline:
            polled = httpx.get(polling_url, headers={"x-key": env("BFL_API_KEY")}, timeout=30.0)
            polled.raise_for_status()
            body = polled.json()
            status = body.get("status", "")
            print(f"status: {status}")
            if status == "Ready":
                result = body.get("result") or {}
                break
            if status in {"Error", "Failed"}:
                raise RuntimeError(body.get("error") or body)
            time.sleep(1.5)
        else:
            raise TimeoutError("FLUX still pending after 90s")
        sample = result.get("sample")
        if not sample:
            raise RuntimeError("Ready response had no sample URL")
        image = httpx.get(sample, timeout=60.0)
        image.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(image.content)
        print(f"saved {out} ({len(image.content)} bytes)")
    except Exception as exc:
        placeholder(out, f"FLUX failed: {exc}")


if __name__ == "__main__":
    main()
