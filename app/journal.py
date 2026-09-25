"""Turns the agent's one-line step summaries into facts the journal UI can draw.

The agent writes plain sentences (agent/agent.py summary_sentence). Parsing them here
keeps the agent unchanged and works for every step already in the database.
"""

from __future__ import annotations

import re

# Both the current wording and the wording older steps were written with.
_STATUS_WORDS = {
    "in stock": "in_stock",
    "low": "low",
    "few options": "low",
    "out": "out",
    "none found": "out",
    "unknown": "unknown",
    "not matched": "unknown",
}

_STARTED = re.compile(r"Started (\d+) new checks?; (\d+) still running")
_RECORDED = re.compile(r"Recorded (\d+) stock checks? \(([^)]*)\)")
_STATUS_PART = re.compile(r"(\d+) ([a-z ]+)")
_LOW_NAMES = re.compile(r"(?:Running low|Few options found): ([^.]+)\.")
_NEW_STORES = re.compile(r"Found (\d+) new stores?")
_PHASE_CHANGE = re.compile(r"(\d{5}) ([A-Z]+) → ([A-Z]+)")


def step_facts(action: str, summary: str) -> dict:
    """Structured view of one step. `kind` drives how the UI draws it."""
    summary = summary or ""
    facts: dict = {"started": 0, "running": None, "recorded": 0, "statuses": {}, "low_items": [],
                   "new_stores": 0, "phase_changes": []}

    if match := _STARTED.search(summary):
        facts["started"], facts["running"] = int(match.group(1)), int(match.group(2))
    if match := _RECORDED.search(summary):
        facts["recorded"] = int(match.group(1))
        for count, words in _STATUS_PART.findall(match.group(2)):
            key = _STATUS_WORDS.get(words.strip(), words.strip())
            facts["statuses"][key] = facts["statuses"].get(key, 0) + int(count)
    if match := _LOW_NAMES.search(summary):
        facts["low_items"] = [name.strip() for name in match.group(1).split(",") if name.strip()]
    if match := _NEW_STORES.search(summary):
        facts["new_stores"] = int(match.group(1))
    if summary.startswith("Phase change:"):
        facts["phase_changes"] = [
            {"zip": zip_code, "from": old, "to": new} for zip_code, old, new in _PHASE_CHANGE.findall(summary)
        ]

    if action == "offline":
        kind = "offline"
    elif action != "cycle" or facts["phase_changes"]:
        kind = "decision"
    elif facts["recorded"] or facts["new_stores"]:
        kind = "results"
    elif facts["started"]:
        kind = "dispatch"
    else:
        kind = "quiet"
    facts["kind"] = kind
    return facts
