"""Phase 6: the pages serve, the API shapes hold, and DURING still hides everything.

Run: .venv/bin/python -m tests.test_ui   (or pytest, if installed)
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app import ask, llm
from app.config import REGION_ZIPS
from app.main import app

client = TestClient(app)  # no `with`: skips startup, so no network checks
llm.MOCK_LLM = True  # the model server is not part of these tests


def test_pages_and_assets_serve() -> None:
    for path in ("/", "/ask", "/offline", "/agent", "/crisis", "/static/style.css", "/static/app.js", "/static/ask.js",
                 "/static/vendor/leaflet/leaflet.js", "/static/vendor/leaflet/leaflet.css"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content, path


def test_no_page_loads_anything_from_a_cdn() -> None:
    for page in ("/", "/ask", "/offline", "/agent", "/crisis"):
        body = client.get(page).text
        for tag in ("<script src=", "<link rel=\"stylesheet\" href="):
            for chunk in body.split(tag)[1:]:
                assert chunk.startswith('"/static/'), f"{page} loads {chunk[:60]} from outside the repo"


def test_overview_has_what_the_header_needs() -> None:
    body = client.get("/api/overview").json()
    assert {"storm_name", "online", "snapshot_hst", "agent", "areas", "budgets"} <= set(body)
    assert {"step", "uptime_minutes", "last_summary", "pending_checks"} <= set(body["agent"])


def test_journal_is_in_hawaii_time() -> None:
    body = client.get("/api/journal?limit=5").json()
    assert body["mode"] == "manual"
    assert body["running"] is False
    steps = body["steps"]
    assert all(str(step["ts_hst"]).endswith("HST") for step in steps)


def test_crisis_page_is_search_then_pick_then_scan() -> None:
    body = client.get("/crisis").text
    assert "Find crises happening now" in body
    assert "Pick a crisis" in body
    assert "/api/crises/discover" in body
    assert "/api/crises/scans" in body


def test_agent_page_has_manual_run_control() -> None:
    body = client.get("/agent").text
    assert 'id="run-search"' in body
    assert 'postJSON("/api/agent/run"' in body
    assert "Next cycle" not in body


def test_manual_run_endpoint_executes_one_cycle() -> None:
    import asyncio

    called = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", None

    async def fake_subprocess(*args, **kwargs):
        called.append((args, kwargs))
        return Process()

    real = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_subprocess
    try:
        response = client.post("/api/agent/run")
    finally:
        asyncio.create_subprocess_exec = real

    assert response.status_code == 200
    assert called[0][0][1:] == ("-m", "agent.agent", "--once")


def test_during_hides_stores_and_risk() -> None:
    os.environ["PHASE_OVERRIDE"] = ",".join(f"{zip_code}:DURING" for zip_code in REGION_ZIPS)
    try:
        stores = client.get("/api/stores").json()
        assert stores["stores"] == []
        assert set(stores["blocked_zips"]) == set(REGION_ZIPS)
        assert client.get("/api/risk").json()["items"] == []
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_during_answer_carries_the_warning_and_no_stock() -> None:
    os.environ["PHASE_OVERRIDE"] = "96720:DURING"
    try:
        facts = ask.build_facts("Where can I buy a tarp in Hilo?")
        assert facts["blocked"] == ["96720"]
        assert not any("in stock" in line for line in facts["lines"])
        body = client.post("/api/ask", json={"question": "Where can I buy a tarp in Hilo?"}).json()
        assert body["safety"]
        assert body["snapshot_hst"] is None or body["snapshot_hst"].endswith("HST")
    finally:
        os.environ.pop("PHASE_OVERRIDE", None)


def test_questions_route_to_the_right_area_and_facts() -> None:
    assert ask.zips_for("is the shelter near Kailua-Kona open") == ["96740"]
    assert ask.intent_of("Where's the nearest open shelter?") == "shelter"
    assert ask.intent_of("what is running out in Hilo") == "running_out"
    assert "tarp" in ask.items_in("do you have tarps and D batteries")


def test_tidy_keeps_the_answer_and_drops_the_rambling() -> None:
    messy = (
        "The nearest shelter is Puu Community Center. Shelter: Puu Community Center. "
        "I cannot determine the location. See https://example.com/x"
    )
    assert ask.tidy(messy, "Where is the shelter?") == "The nearest shelter is Puu Community Center."


def test_fallback_still_names_the_saved_shelter() -> None:
    facts = ask.build_facts("Where is the nearest open shelter to Hilo?")
    answer = ask._fallback(facts)
    assert "Puʻuʻeo Community Center" in answer
    assert "145 Wainaku St." in answer


def test_nearest_shelter_is_measured_from_the_town() -> None:
    # 96704's ZIP middle is 30 miles south of Captain Cook; by ZIP middles Waimea won.
    facts = ask.build_facts("where do i go in kona")
    assert "Yano Hall" in ask._fallback(facts)
    facts = ask.build_facts("Where's the nearest open shelter to Hilo?")
    nearest = [line for line in facts["lines"] if "reported shelter to Hilo" in line]
    assert nearest[0].startswith("Nearest reported shelter to Hilo: Puʻuʻeo Community Center")
    assert "Keaʻau Armory" in nearest[1]


def test_month_abbreviation_does_not_cut_the_answer() -> None:
    text = "Puʻuʻeo Community Center opens Friday, Sept. 25 at 6 a.m. Keaʻau Armory is 6.7 miles away."
    assert ask.tidy(text, "Where is the shelter?") == text


def test_model_reading_is_checked_against_the_question() -> None:
    import asyncio

    from app.models import AskQuery

    async def copied_example(*_args, **_kwargs):  # what LFM2.5 did with a real-valued example
        return AskQuery(intent="shelter", places=["Kona"], items=["generator", "gas_can"])

    real = ask.chat_json
    ask.chat_json = copied_example
    try:
        read = asyncio.run(ask.understand("can my family still get to Costco in Hilo"))
    finally:
        ask.chat_json = real
    assert read["zips"] == ["96720"]  # Kona was never asked about
    assert read["items"] == []  # "can" is not a gas can
    assert read["intent"] == "supplies"  # the model may not make a shelter question


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
