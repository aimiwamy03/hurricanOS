"""Small objects the model is allowed to return."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AreaPhase(BaseModel):
    area: str
    proposed_phase: Literal["BEFORE", "DURING", "AFTER"]
    confidence: float = 0.5
    # Hours until tropical-storm or hurricane conditions reach the area (0 = now),
    # and the source URL that says so. None when the sources do not say.
    onset_hours: float | None = None
    onset_source: str | None = None


class StormAssessment(BaseModel):
    summary: str
    track_shift: bool
    expected_onset_hst: str | None = None
    areas: list[AreaPhase]
    official_links: list[dict] = []


class StockResult(BaseModel):
    status: Literal["in_stock", "low", "out", "unknown"]
    price: float | None = None
    confidence: float
    level: Literal["store", "zip", "agent"]
    source_url: str | None = None


class Explanation(BaseModel):
    text: str


class AskAnswer(BaseModel):
    answer: str
    cited_ids: list[int] = []


class AskQuery(BaseModel):
    """What a resident's question is about. Plain strings: code checks every value
    against its own lists, so a made-up town or item is dropped, not trusted."""

    intent: str
    places: list[str] = []
    items: list[str] = []


class CrisisOption(BaseModel):
    """One active crisis consolidated from numbered Nimble news results."""

    title: str
    crisis_type: str
    location: str
    summary: str
    source_ids: list[int] = []


class CrisisDiscovery(BaseModel):
    crises: list[CrisisOption] = []


class CrisisResource(BaseModel):
    """A resource the local model recommends looking up for a selected crisis."""

    name: str
    kind: Literal["retail", "shelter"]
    search_terms: str
    why: str


class CrisisResourcePlan(BaseModel):
    safety_note: str
    resources: list[CrisisResource] = []
