"""UTC goes in the database, Hawaii time goes on the screen (UTC-10, no DST)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import HST


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def hst(value: str | None) -> str | None:
    stamp = parse(value)
    return None if stamp is None else stamp.astimezone(HST).strftime("%Y-%m-%d %H:%M HST")


def hst_clock(value: str | None) -> str | None:
    """Short form for banners and the journal: 14:05 HST."""
    stamp = parse(value)
    return None if stamp is None else stamp.astimezone(HST).strftime("%H:%M HST")


def minutes_ago(value: str | None) -> int | None:
    stamp = parse(value)
    if stamp is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - stamp).total_seconds() // 60))
