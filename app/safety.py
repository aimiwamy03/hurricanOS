"""The single safety gate. Every stock list or recommendation for a ZIP goes through
shopping_allowed. In DURING, people get the safety message and official links, never stock."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from app import db
from app.tips import tips_for

SAFETY_MESSAGE = (
    "Storm conditions are expected or occurring here. Do not travel to stores. "
    "Follow official instructions and shelter guidance."
)


def _override() -> dict[str, str]:
    # Read at call time, not import time, so the demo override and tests take effect.
    phases = {}
    for part in os.getenv("PHASE_OVERRIDE", "").split(","):
        if ":" in part:
            zip_code, phase = part.split(":", 1)
            phases[zip_code.strip()] = phase.strip().upper()
    return phases


CAUTION_MESSAGE = "Storm conditions may be approaching. Check official guidance before traveling."


def caution_message(zip_code: str) -> str | None:
    """Amber note while DURING is being confirmed (1-2 proposals so far, not yet locked)."""
    if zip_code in _override():
        return None
    rows = db.query("SELECT phase, during_votes FROM zips WHERE zip = ?", (zip_code,))
    if rows and rows[0]["phase"] != "DURING" and (rows[0]["during_votes"] or 0) > 0:
        return CAUTION_MESSAGE
    return None


def get_zip_phase(zip_code: str) -> str:
    override = _override()
    if zip_code in override:
        return override[zip_code]
    rows = db.query("SELECT phase FROM zips WHERE zip = ?", (zip_code,))
    # Unknown ZIP or no phase yet: be conservative.
    return rows[0]["phase"] if rows and rows[0]["phase"] else "DURING"


def shopping_allowed(zip_code: str) -> bool:
    return get_zip_phase(zip_code) in ("BEFORE", "AFTER")


def safety_message(zip_code: str) -> str:
    return SAFETY_MESSAGE


_OFFICIAL_HOSTS = ("redcross.org",)
_ISLAND_WORDS = {
    "Maui": ("maui", "molokai", "moloka", "lanai", "lahaina", "kihei", "wailuku", "kahului"),
    "Hawaii": ("hawaii-county", "hawaiicounty", "hawaii county", "big-island", "big island", "bigisland", "hilo", "kona", "hawaii-news"),
}


def is_official(url: str | None) -> bool:
    """Government or Red Cross. News sites are useful, but they are not official."""
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    return host.endswith(".gov") or host.endswith(".mil") or any(host.endswith(h) for h in _OFFICIAL_HOSTS)


def island_of(title: str | None, url: str | None) -> str | None:
    """Which island a link is about, from its URL and title. None = could be either."""
    text = f"{url or ''} {title or ''}".lower()
    hits = [island for island, words in _ISLAND_WORDS.items() if any(word in text for word in words)]
    return hits[0] if len(hits) == 1 else None


# Readable names for the hosts the agent keeps finding.
_HOST_NAMES = {
    "nhc.noaa.gov": "National Hurricane Center",
    "woc.noaa.gov": "National Hurricane Center",
    "weather.gov": "National Weather Service",
    "hawaiicounty.gov": "Hawaii County",
    "mauicounty.gov": "Maui County",
    "hawaii.gov": "State of Hawaii",
    "redcross.org": "American Red Cross",
}


def _host_name(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    for suffix, name in _HOST_NAMES.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return host


def _usable_title(text: str) -> bool:
    """Search titles often arrive cut off ("NATIONAL HURRICANE CENTER and", "...nolo Moving")."""
    if not text or text.endswith(" source") or text.startswith("..."):
        return False
    if text.split()[-1].lower() in ("and", "or", "of", "the", "for", "to", "in"):
        return False
    letters = [c for c in text if c.isalpha()]
    return not (letters and sum(c.isupper() for c in letters) / len(letters) > 0.7)


def link_label(title: str | None, url: str | None) -> str:
    """A clean title, else a readable name for the site (research links are titled "shelters source")."""
    text = (title or "").strip()
    if _usable_title(text):
        return text
    return _host_name(url) or text or "source"


def safety_payload(zip_code: str) -> dict:
    links = db.query(
        "SELECT title, url, kind FROM official_links ORDER BY kind = 'news', kind = 'shelter' DESC, found_ts DESC LIMIT 12"
    )
    for row in links:
        row["label"] = link_label(row["title"], row["url"])
    return {
        "zip": zip_code,
        "phase": get_zip_phase(zip_code),
        "shopping_allowed": False,
        "message": safety_message(zip_code),
        "official_links": links,
        "tips": tips_for("DURING"),
    }
