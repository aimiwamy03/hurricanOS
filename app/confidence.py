"""How much to trust one stock check, built from evidence the agent actually saw.

Two parts:
- evidence_score() runs once, when a check is recorded. It looks at where the answer
  came from, how many products backed it, and whether it agrees with the last check.
  The score and its reasons are saved with the check.
- current() runs whenever the check is shown. A check loses trust as it ages, because
  shelves empty fast on the last day before a storm.

Every factor is a plain multiplier with a reason, so the UI can show exactly why.
"""

from __future__ import annotations

# Where the answer came from. A ZIP-level answer is Walmart's pickup availability for
# the ZIP's store, not a shelf count, so it starts lower than a real store-level check.
LEVEL_BASE = {"store": 0.90, "zip": 0.75, "agent": 0.50}
LEVEL_REASON = {
    "store": "store shelf data",
    "zip": "Walmart pickup availability for this ZIP (not a shelf count)",
    "agent": "web research answer",
}
FRESH_MINUTES = 20  # full trust up to here
STALE_MINUTES = 180  # half trust by here
FLOOR = 0.4  # never below this share of the recorded score
HIGH, MEDIUM = 0.65, 0.45
_BAD = ("low", "out")


def _depth(matched: int) -> tuple[float, str]:
    """More matching products = the status rests on more than one listing."""
    if matched >= 5:
        return 1.0, f"{matched} matching products"
    if matched >= 3:
        return 0.9, f"{matched} matching products"
    if matched == 2:
        return 0.75, "only 2 matching products"
    return 0.6, "only 1 matching product"


def _agreement(status: str, previous: str | None) -> tuple[float, str]:
    if previous is None:
        return 0.9, "first check for this item here"
    if previous == status:
        return 1.0, "agrees with the previous check"
    if status in _BAD and previous in _BAD:
        return 0.95, "close to the previous check"
    return 0.8, f"changed since the previous check (was {previous.replace('_', ' ')})"


def evidence_score(
    *, status: str, level: str, matched: int, previous: str | None, price_flag: bool
) -> tuple[float, list[str]]:
    """Recorded trust in one check (0-1) and the reasons behind it."""
    if status == "unknown":
        return 0.0, ["no matching product in the results"]
    score = LEVEL_BASE.get(level, 0.5)
    reasons = [LEVEL_REASON.get(level, level)]
    for factor, reason in (_depth(matched), _agreement(status, previous)):
        score *= factor
        reasons.append(reason)
    if price_flag:
        score *= 0.9
        reasons.append("price jumped compared with earlier today")
    return round(score, 2), reasons


def freshness(minutes: int | None) -> float:
    """1.0 while fresh, sliding to 0.5 at STALE_MINUTES, then FLOOR at the very least."""
    if minutes is None:
        return FLOOR
    if minutes <= FRESH_MINUTES:
        return 1.0
    slide = (minutes - FRESH_MINUTES) / (STALE_MINUTES - FRESH_MINUTES)
    return max(FLOOR, round(1.0 - 0.5 * slide, 2))


def label(score: float) -> str:
    if score >= HIGH:
        return "high"
    if score >= MEDIUM:
        return "medium"
    return "low"


def current(recorded: float | None, why: str | None, minutes: int | None) -> dict:
    """Confidence as of now, for the API: score, label and the reasons in order."""
    recorded = float(recorded or 0)
    reasons = [part for part in (why or "").split(" · ") if part] or ["older check: evidence was not recorded"]
    fresh = freshness(minutes)
    if fresh < 1.0:
        reasons.append(f"checked {minutes} min ago, trust reduced" if minutes is not None else "check time unknown")
    score = round(recorded * fresh, 2)
    return {"score": score, "label": label(score), "reasons": reasons}
