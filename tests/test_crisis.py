"""Crisis-now search, then crisis-specific ZIP supply scans.

Run: .venv/bin/python -m tests.test_crisis
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import crisis, llm, nimble_client
from app.crisis import CrisisInput, CrisisScanRequest
from app.llm import _mock_crisis_plan
from app.main import app

client = TestClient(app)
llm.MOCK_LLM = True
nimble_client.MOCK_NIMBLE = True
crisis.MOCK_NIMBLE = True


def test_fire_requires_water() -> None:
    needed = crisis.required_resources({"crisis_type": "wildfire", "title": "Canyon fire", "summary": ""})
    assert any(row["search_terms"] == "bottled water" for row in needed)


def test_hail_requires_shelter() -> None:
    needed = crisis.required_resources({"crisis_type": "hail", "title": "Hailstorm", "summary": ""})
    assert any(row["kind"] == "shelter" for row in needed)


def test_merge_adds_water_when_the_model_forgets() -> None:
    planned = [{"name": "N95 mask", "kind": "retail", "search_terms": "N95 mask", "why": "Smoke"}]
    merged = crisis.merge_resources(planned, {"crisis_type": "fire", "title": "Fire", "summary": ""})
    assert merged[0]["search_terms"] == "bottled water"
    assert [row["search_terms"] for row in merged] == ["bottled water", "N95 mask"]


def test_local_model_mock_picks_water_for_fire_and_shelter_for_hail() -> None:
    fire = _mock_crisis_plan('<crisis>{"crisis_type":"wildfire","title":"Fire"}</crisis>')
    assert any(row.search_terms == "bottled water" for row in fire.resources)
    hail = _mock_crisis_plan('<crisis>{"crisis_type":"hail","title":"Hailstorm"}</crisis>')
    assert any(row.kind == "shelter" for row in hail.resources)


def test_discover_returns_current_crises() -> None:
    body = asyncio.run(crisis.discover("United States"))
    types = {row["crisis_type"] for row in body["crises"]}
    assert {"hurricane", "wildfire", "hail"} <= types
    assert all(row["sources"] for row in body["crises"])


def test_scan_fire_looks_up_water() -> None:
    scan = asyncio.run(
        crisis.start_scan(
            CrisisScanRequest(
                crisis=CrisisInput(
                    title="Canyon wildfire",
                    crisis_type="wildfire",
                    location="California",
                    summary="Flames near homes.",
                ),
                zip_code="96720",
            )
        )
    )
    terms = {row["search_terms"] for row in scan["resources"]}
    assert "bottled water" in terms
    assert all(row["status"] in {"complete", "pending", "error"} for row in scan["resources"])


def test_scan_hail_looks_up_shelter() -> None:
    scan = asyncio.run(
        crisis.start_scan(
            CrisisScanRequest(
                crisis=CrisisInput(
                    title="Severe hailstorm",
                    crisis_type="hail",
                    location="Texas",
                    summary="Hail damaging roofs.",
                ),
                zip_code="96720",
            )
        )
    )
    assert any(row["kind"] == "shelter" for row in scan["resources"])


def test_bad_zip_is_rejected() -> None:
    try:
        asyncio.run(
            crisis.start_scan(
                CrisisScanRequest(
                    crisis=CrisisInput(title="Fire", crisis_type="fire", location="X", summary=""),
                    zip_code="96",
                )
            )
        )
    except ValueError as exc:
        assert "5 digits" in str(exc)
    else:
        raise AssertionError("expected invalid ZIP to fail")


def test_pages_and_api_shapes() -> None:
    page = client.get("/crisis")
    assert page.status_code == 200
    assert "Find crises happening now" in page.text
    assert "/api/crises/discover" in page.text
    found = client.post("/api/crises/discover", json={"scope": "United States"}).json()
    assert found["crises"]
    wildfire = next(row for row in found["crises"] if row["crisis_type"] == "wildfire")
    scan = client.post("/api/crises/scans", json={"crisis": wildfire, "zip_code": "96720"}).json()
    assert scan["zip_code"] == "96720"
    assert any(row["search_terms"] == "bottled water" for row in scan["resources"])
    polled = client.get(f"/api/crises/scans/{scan['id']}").json()
    assert polled["id"] == scan["id"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
