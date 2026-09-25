"""Confidence must move with the evidence. Run: .venv/bin/python -m tests.test_confidence"""

from app.confidence import current, evidence_score


def _score(**overrides) -> float:
    args = {"status": "in_stock", "level": "zip", "matched": 6, "previous": "in_stock", "price_flag": False}
    return evidence_score(**{**args, **overrides})[0]


def test_more_matching_products_means_more_confidence() -> None:
    assert _score(matched=8) > _score(matched=3) > _score(matched=1)


def test_agreeing_with_last_check_beats_a_flip() -> None:
    assert _score(previous="in_stock") > _score(previous=None) > _score(previous="out")


def test_store_level_beats_zip_level() -> None:
    assert _score(level="store") > _score(level="zip")


def test_price_jump_lowers_confidence() -> None:
    assert _score(price_flag=True) < _score()


def test_unknown_is_zero() -> None:
    assert _score(status="unknown", matched=0) == 0.0


def test_old_checks_lose_trust() -> None:
    score, reasons = evidence_score(status="in_stock", level="zip", matched=6, previous="in_stock", price_flag=False)
    why = " · ".join(reasons)
    fresh, hour, stale = (current(score, why, age) for age in (5, 60, 240))
    assert fresh["score"] > hour["score"] > stale["score"]
    assert fresh["label"] == "high" and stale["label"] == "low"
    assert any("min ago" in reason for reason in stale["reasons"])


def test_reasons_are_saved_and_returned() -> None:
    _score_value, reasons = evidence_score(status="low", level="zip", matched=2, previous="in_stock", price_flag=False)
    shown = current(0.4, " · ".join(reasons), 5)["reasons"]
    assert "only 2 matching products" in shown
    assert any("was in stock" in reason for reason in shown)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
