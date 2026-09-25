"""The DURING rule: 3 in a row, .gov fast track within 3 h, never when onset is > 3 h away.

Run: .venv/bin/python -m tests.test_phase_rules
"""

from __future__ import annotations

from agent.storm import decide_phase
from app.models import AreaPhase

GOV = "https://www.weather.gov/hfo/"
NEWS = "https://www.npr.org/nolo"
SOURCES = {GOV, NEWS}


def during(onset=None, source=None) -> AreaPhase:
    return AreaPhase(area="Hilo", proposed_phase="DURING", onset_hours=onset, onset_source=source)


def test_three_in_a_row_locks() -> None:
    phase, locked, votes = "BEFORE", False, 0
    for expected in (1, 2):
        phase, locked, votes, _ = decide_phase(phase, locked, votes, during(), SOURCES)
        assert (phase, locked, votes) == ("BEFORE", False, expected)
    phase, locked, votes, _ = decide_phase(phase, locked, votes, during(), SOURCES)
    assert (phase, locked) == ("DURING", True)


def test_other_proposal_resets() -> None:
    before = AreaPhase(area="Hilo", proposed_phase="BEFORE")
    assert decide_phase("BEFORE", False, 2, before, SOURCES)[:3] == ("BEFORE", False, 0)


def test_far_onset_never_counts() -> None:
    assert decide_phase("BEFORE", False, 2, during(onset=20, source=GOV), SOURCES)[:3] == ("BEFORE", False, 0)


def test_fast_track_needs_gov_source_that_was_read() -> None:
    assert decide_phase("BEFORE", False, 0, during(onset=2, source=GOV), SOURCES)[:2] == ("DURING", True)
    # News source, or a .gov URL we never read: counts as one vote only.
    assert decide_phase("BEFORE", False, 0, during(onset=2, source=NEWS), SOURCES)[:3] == ("BEFORE", False, 1)
    assert decide_phase("BEFORE", False, 0, during(onset=2, source="https://www.nhc.noaa.gov/made-up"), SOURCES)[:3] == (
        "BEFORE",
        False,
        1,
    )


def test_locked_needs_official_all_clear() -> None:
    after = AreaPhase(area="Hilo", proposed_phase="AFTER")
    assert decide_phase("DURING", True, 3, after, {NEWS})[:2] == ("DURING", True)
    assert decide_phase("DURING", True, 3, after, SOURCES)[:2] == ("AFTER", False)


def test_model_down_changes_nothing() -> None:
    assert decide_phase("BEFORE", False, 2, None, SOURCES)[:3] == ("BEFORE", False, 2)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
