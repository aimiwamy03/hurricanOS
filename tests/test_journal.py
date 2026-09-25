"""The journal UI draws steps from these parsed facts.

Run: .venv/bin/python -m tests.test_journal   (or pytest, if installed)
"""

from app.journal import step_facts


def test_results_step_old_wording():
    facts = step_facts("cycle", "Recorded 20 stock checks (14 in stock, 1 low, 5 unknown). Running low: Power bank. "
                                "Started 0 new checks; 0 still running at Nimble.")
    assert facts["kind"] == "results"
    assert facts["recorded"] == 20
    assert facts["statuses"] == {"in_stock": 14, "low": 1, "unknown": 5}
    assert facts["low_items"] == ["Power bank"]
    assert facts["running"] == 0


def test_results_step_new_wording():
    facts = step_facts("cycle", "Recorded 3 stock checks (1 few options, 2 none found). Few options found: Gas can, Tarp. "
                                "Found 4 new stores. Started 2 new checks; 5 still running at Nimble.")
    assert facts["statuses"] == {"low": 1, "out": 2}
    assert facts["low_items"] == ["Gas can", "Tarp"]
    assert facts["new_stores"] == 4
    assert (facts["started"], facts["running"]) == (2, 5)


def test_dispatch_quiet_decision_offline():
    assert step_facts("cycle", "Started 16 new checks; 16 still running at Nimble.")["kind"] == "dispatch"
    assert step_facts("cycle", "Started 0 new checks; 16 still running at Nimble.")["kind"] == "quiet"
    phase = step_facts("cycle", "Phase change: 96720 BEFORE → DURING; 96740 BEFORE → DURING. Started 0 new checks; 0 still running at Nimble.")
    assert phase["kind"] == "decision"
    assert phase["phase_changes"][1] == {"zip": "96740", "from": "BEFORE", "to": "DURING"}
    assert step_facts("phase_rule", "False alarm corrected: ...")["kind"] == "decision"
    assert step_facts("offline", "Offline — pausing web work.")["kind"] == "offline"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
