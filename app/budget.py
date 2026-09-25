"""Call budgets live in SQLite so a restart does not reset them."""

from __future__ import annotations

from app.config import MAX_IMAGE_CALLS, MAX_NIMBLE_CALLS
from app import db

_LIMITS = {"nimble": MAX_NIMBLE_CALLS, "flux": MAX_IMAGE_CALLS}


class BudgetExceeded(RuntimeError):
    """The agent catches this and skips the call."""


def spend(kind: str) -> None:
    limit = _LIMITS[kind]
    key = f"budget:{kind}"
    with db.write_lock:
        current = int(db.get_kv(key) or "0")
        if current >= limit:
            raise BudgetExceeded(f"{kind} budget exhausted")
        db.set_kv(key, str(current + 1))


def remaining(kind: str) -> int:
    limit = _LIMITS[kind]
    used = int(db.get_kv(f"budget:{kind}") or "0")
    return max(0, limit - used)
