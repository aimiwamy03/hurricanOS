"""Small objects the model is allowed to return."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AreaPhase(BaseModel):
    area: str
    proposed_phase: Literal["BEFORE", "DURING", "AFTER"]
    confidence: float


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
