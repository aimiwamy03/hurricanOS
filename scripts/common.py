"""Shared helpers for the Phase 0 check scripts."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def mock_on(flag: str, *required: str) -> tuple[bool, str]:
    """Return (use_mock, reason). A missing key is a mock, not a crash."""
    if env(flag, "0") in {"1", "true", "yes", "on"}:
        return True, f"{flag}=1"
    missing = [name for name in required if not env(name)]
    if missing:
        return True, "missing " + ", ".join(missing)
    return False, ""


def clip(text: str, limit: int = 800) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
