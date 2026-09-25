"""/ask never passes on a sentence the saved facts do not back up, and never calls a
news site official. Cases are real model outputs from testing.

Run: .venv/bin/python -m tests.test_ask_grounding
"""

from __future__ import annotations

from app.ask import grounded
from app.safety import is_official, island_of

FACTS = [
    "Saved shelter research: Puʻuʻeo Community Center — 145 Wainaku St., Hilo [1] [4]",
    "Hilo / East Hawaii (96720) is in phase BEFORE: storm conditions have NOT arrived.",
    "AA batteries: in stock for pickup in 96720 (Walmart, $14.97), checked 28 min ago.",
]


def test_invented_phone_number_is_dropped() -> None:
    text = "The nearest shelter is Puʻuʻeo Community Center at 145 Wainaku St., Hilo. Call (808) 961-5555."
    assert grounded(text, FACTS, []) == "The nearest shelter is Puʻuʻeo Community Center at 145 Wainaku St., Hilo."


def test_invented_price_is_dropped() -> None:
    assert grounded("AA batteries cost $19.99 at Walmart.", FACTS, []) == ""


def test_flipped_phase_is_dropped() -> None:
    assert grounded("Storm conditions have arrived in Hilo.", FACTS, []) == ""
    assert grounded("Storm conditions have NOT arrived in Hilo.", FACTS, []) == "Storm conditions have NOT arrived in Hilo."


def test_echoed_prompt_tags_are_removed() -> None:
    assert "[data]" not in grounded("Go to 145 Wainaku St. [data]Hilo is safe today.", FACTS, [])


def test_only_government_and_red_cross_are_official() -> None:
    assert is_official("https://hawaiicounty.gov/Home/News/4695")
    assert is_official("https://www.weather.gov/hfo/")
    assert is_official("https://www.redcross.org/local/hawaii")
    assert not is_official("https://mauinow.com/2026/09/24/county-to-open-emergency-shelters")
    assert not is_official("https://khon2.com/resources-for-severe-weather/")


def test_links_are_sorted_by_island() -> None:
    assert island_of("shelters source", "https://mauinow.com/2026/09/24/county-to-open-emergency-shelters-friday-on-maui") == "Maui"
    assert island_of("", "https://bigislandnow.com/2026/09/23/hawaii-county-to-open-several-shelters") == "Hawaii"
    assert island_of("", "https://www.weather.gov/hfo/") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
