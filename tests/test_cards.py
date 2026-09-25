"""Status cards go through the safety gate: DURING shows guidance and no stock.

Run: .venv/bin/python -m tests.test_cards
"""

from __future__ import annotations

import os

from PIL import Image

from agent.visuals import H, W, card_facts, render_card
from app.safety import SAFETY_MESSAGE


def test_during_card_has_no_stock() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:DURING"
    try:
        facts = card_facts("96720")
        assert facts["phase"] == "DURING"
        assert facts["lines"] == []
        assert facts["message"] == SAFETY_MESSAGE
        assert any(tip["key"] == "go_to_shelter" for tip in facts["tips"])
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_before_card_lists_checked_items_low_first() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:BEFORE"
    try:
        facts = card_facts("96720")
        assert "message" not in facts
        statuses = [line["status"] for line in facts["lines"]]
        assert statuses == sorted(statuses, key=lambda status: status == "in_stock")
        assert all("checked" in line["detail"] and "HST" in line["detail"] for line in facts["lines"])
        assert "Not official guidance" in facts["footer"]
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_card_renders_at_share_size() -> None:
    with Image.open(render_card("96720")) as image:
        assert image.size == (W, H)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
