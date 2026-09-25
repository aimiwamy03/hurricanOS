"""Logging and time helpers shared by the agent modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import DATA_DIR

LOG_FILE = DATA_DIR / "agent.log"


def log(prefix: str, message: str) -> None:
    """Print and append to data/agent.log, e.g. log("stock", "96720 tarp: low")."""
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"{stamp} [{prefix}] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def tb_ts(value: datetime | None = None) -> str:
    """Tinybird DateTime64(3) format."""
    return (value or now_utc()).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def iso_ago(hours: float) -> str:
    """ISO timestamp N hours ago, comparable with the ts_utc strings we store."""
    return (now_utc() - timedelta(hours=hours)).isoformat()


def minutes_since(iso: str | None) -> float | None:
    if not iso:
        return None
    stamp = datetime.fromisoformat(iso)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now_utc() - stamp).total_seconds() / 60
