"""What to do now, shelters, and stock pacing to the 8 PM PT end time.

Run: .venv/bin/python -m tests.test_now   (or pytest, if installed)
"""

from datetime import datetime

from agent import stock
from app import now, shelters
from app.config import PT

SAMPLE = """## Hawaii Island (Big Island)

**Opening Friday, Sept. 25 at 6 a.m.**
- **Puʻuʻeo Community Center** — 145 Wainaku St., Hilo [1] [4]

## Maui County — Opening Friday, Sept. 25 at noon

- **Hāna High & Elementary School** — 4111 Hāna Highway [1] [5]

Source index:
  [1] Shelters LIST — https://khon2.com/list  (official)
  [4] Five more shelters — https://hawaiitribune-herald.com/five  (official)
  [5] Maui shelters — https://mauinow.com/maui  (news)
"""


def test_parse_keeps_town_island_time_and_sources() -> None:
    hilo, hana = shelters.parse(SAMPLE)
    assert (hilo["zip"], hilo["island"]) == ("96720", "Hawaiʻi Island")
    assert hilo["when"] == "Opening Friday, Sept. 25 at 6 a.m."
    assert [s["url"] for s in hilo["sources"]] == ["https://khon2.com/list", "https://hawaiitribune-herald.com/five"]
    assert (hana["zip"], hana["island"]) == ("96713", "Maui")  # town found with no comma
    assert hana["when"] == "Opening Friday, Sept. 25 at noon"


def test_every_card_has_911_and_one_instruction() -> None:
    for zip_code in (None, "96720", "96778", "96793", "96815"):
        card = now.build(zip_code=zip_code)
        assert card["contacts"][0]["phone"] == "911"
        assert card["instruction"]
        assert card["level"] in ("go", "careful", "stop", "unknown")


def test_untracked_island_is_told_so_not_guessed() -> None:
    card = now.build(zip_code="96815")  # Oʻahu
    assert card["phase"] is None and card["level"] == "unknown"
    assert "doesn't track" in card["instruction"]


def test_during_phase_says_stay_put() -> None:
    level, sentence = now._instruction("DURING", "Hilo / East Hawaii", None)
    assert level == "stop" and "don't drive" in sentence


def test_stock_pacing_stops_at_8pm_pt_and_holds_back_other_calls() -> None:
    afternoon = datetime(2026, 9, 25, 14, 45, tzinfo=PT)
    assert stock.cycles_left(540, afternoon) == 35
    assert stock.budget_this_cycle(436, 540, afternoon) == (436 - 4 * 35) // 35
    assert stock.budget_this_cycle(100, 540, datetime(2026, 9, 25, 20, 1, tzinfo=PT)) == 0
    assert stock.budget_this_cycle(50, 540, afternoon) == 0  # not enough left after the other stages


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
