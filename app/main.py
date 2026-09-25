"""HTTP: the JSON API plus the three pages in web/."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ask as ask_module
from app.budget import remaining
from app.config import (
    ACTIVE_AREAS,
    DATA_DIR,
    ESSENTIALS,
    MOCK_FLUX,
    MOCK_LLM,
    MOCK_NIMBLE,
    MOCK_TINYBIRD,
    REGION_ZIPS,
    STORM_NAME,
)
from app.connectivity import check_once, is_online, monitor_forever
from app import confidence, db
from app.journal import step_facts
from app import zips as zip_lookup
from app import now as now_card
from app import shelters as shelter_list
from app import reports as shelf_reports
from app import crisis as crisis_search
from app.llm import llm_ok
from app.safety import caution_message, get_zip_phase, is_official, link_label, safety_payload, shopping_allowed
from app.tips import tips_for
from agent.visuals import render_card
from app.times import hst, hst_clock, minutes_ago

_TABLES = (
    "storm_updates",
    "zips",
    "stores",
    "essentials",
    "products",
    "stock_checks",
    "check_products",
    "forecasts",
    "official_links",
    "agent_steps",
)

WEB_DIR = Path("web")
IMAGE_DIR = DATA_DIR / "images"
PROJECT_DIR = Path(__file__).resolve().parents[1]
_agent_run_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    await check_once()
    # The agent process sends the Tinybird queue; two flushers would race on the file.
    monitor_task = asyncio.create_task(monitor_forever())
    yield
    monitor_task.cancel()


app = FastAPI(title="Shelfwatch", lifespan=lifespan)
_NAMES = {item["key"]: item["name"] for item in ESSENTIALS}
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")


class AskRequest(BaseModel):
    question: str


def _page(name: str) -> FileResponse:
    return FileResponse(WEB_DIR / name, headers={"Cache-Control": "no-store"})


def _agent_state() -> dict:
    try:
        return json.loads((DATA_DIR / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@app.get("/")
async def index_page() -> FileResponse:
    return _page("index.html")


@app.get("/ask")
async def ask_page() -> FileResponse:
    return _page("ask.html")


@app.get("/offline")
async def offline_page() -> FileResponse:
    return _page("offline.html")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    """Served from the root so it can keep every page working when a phone loses signal."""
    return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/agent")
async def agent_page() -> FileResponse:
    return _page("agent.html")


@app.get("/crisis")
async def crisis_page() -> FileResponse:
    return _page("crisis.html")


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "llm": await llm_ok(),
        "online": is_online(),
        "mocks": {
            "nimble": MOCK_NIMBLE,
            "tinybird": MOCK_TINYBIRD,
            "flux": MOCK_FLUX,
            "llm": MOCK_LLM,
        },
    }


@app.get("/api/status")
async def status() -> dict:
    counts = {}
    for table in _TABLES:
        rows = db.query(f"SELECT COUNT(*) AS n FROM {table}")
        counts[table] = rows[0]["n"]
    steps = db.query("SELECT * FROM agent_steps ORDER BY step DESC LIMIT 1")
    phases = db.query("SELECT zip, area, phase, phase_locked, during_votes FROM zips ORDER BY priority DESC, zip")
    return {
        "counts": counts,
        "budgets": {"nimble": remaining("nimble"), "flux": remaining("flux")},
        "last_agent_step": steps[0] if steps else None,
        "phases": phases,
        "last_sync_hst": hst(db.get_kv("last_sync_utc")),
    }


@app.get("/api/overview")
async def overview() -> dict:
    """Everything the header and the area tabs need, in one call."""
    state = _agent_state()
    steps = db.query("SELECT step, ts_utc, phase, action, summary FROM agent_steps ORDER BY step DESC LIMIT 1")
    last_step = steps[0] if steps else None
    storm_rows = db.query("SELECT ts_utc, summary, source_url FROM storm_updates ORDER BY id DESC LIMIT 1")
    # Watches and warnings are quoted from the newest .gov advisory, never from the model.
    official_rows = db.query(
        "SELECT ts_utc, official_text, official_url FROM storm_updates "
        "WHERE official_text IS NOT NULL ORDER BY id DESC LIMIT 1"
    )
    areas = []
    zip_rows = db.query(
        "SELECT zip, area, phase, priority, reason, phase_locked, updated_ts FROM zips ORDER BY priority DESC, zip"
    )
    for row in zip_rows:
        zip_code = row["zip"]
        counts = db.query(
            "SELECT COUNT(*) AS checks FROM stock_checks WHERE zip = ? AND status != 'unknown'", (zip_code,)
        )
        store_rows = db.query(
            "SELECT COUNT(*) AS stores, SUM(EXISTS (SELECT 1 FROM stock_checks c WHERE c.store_id = s.id "
            "AND c.status != 'unknown')) AS checked FROM stores s WHERE zip = ?",
            (zip_code,),
        )
        areas.append(
            {
                **row,
                "shopping_allowed": shopping_allowed(zip_code),
                "caution": caution_message(zip_code),
                "checks": counts[0]["checks"],
                "stores": store_rows[0]["stores"],
                "stores_checked": store_rows[0]["checked"] or 0,
                "updated_hst": hst_clock(row["updated_ts"]),
            }
        )
    snapshot = ask_module.snapshot_iso()
    return {
        "storm_name": STORM_NAME,
        "essentials": [{"key": item["key"], "name": item["name"], "pictogram": _pictogram(item["key"])} for item in ESSENTIALS],
        "online": is_online(),
        "snapshot_hst": hst(snapshot),
        "snapshot_clock_hst": hst_clock(snapshot),
        "last_sync_hst": hst_clock(db.get_kv("last_sync_utc")),
        "region_zips": list(REGION_ZIPS),
        "area_names": list(ACTIVE_AREAS),
        "agent": {
            "mode": "manual",
            "running": _agent_run_lock.locked(),
            "step": state.get("step") or (last_step["step"] if last_step else 0),
            "uptime_minutes": minutes_ago(state.get("started_utc")),
            "pending_checks": len(state.get("pending_tasks") or {}),
            "last_step_hst": hst_clock(last_step["ts_utc"]) if last_step else None,
            "last_step_minutes_ago": minutes_ago(last_step["ts_utc"]) if last_step else None,
            "last_summary": last_step["summary"] if last_step else None,
            "phase": last_step["phase"] if last_step else None,
        },
        "storm_update": (
            {
                "summary": storm_rows[0]["summary"],
                "source_url": storm_rows[0]["source_url"],
                "read_hst": hst_clock(storm_rows[0]["ts_utc"]),
                "official_text": official_rows[0]["official_text"] if official_rows else None,
                "official_url": official_rows[0]["official_url"] if official_rows else None,
                "official_read_hst": hst_clock(official_rows[0]["ts_utc"]) if official_rows else None,
            }
            if storm_rows
            else None
        ),
        "areas": areas,
        "budgets": {"nimble": remaining("nimble"), "flux": remaining("flux")},
    }


@app.get("/api/stock")
async def stock(zip: str) -> dict:
    """Latest check per essential for one ZIP. Goes through the safety gate first."""
    if not shopping_allowed(zip):
        return safety_payload(zip)
    items = current_stock_items(zip_code=zip)
    for item in items:
        _decorate_stock_item(item, include_pictogram=True)
    forecasts = db.query(
        """
        SELECT essential_key, risk, reason, ts_utc FROM forecasts
        WHERE zip = ? AND ts_utc >= ? AND id IN (SELECT MAX(id) FROM forecasts WHERE zip = ? GROUP BY essential_key)
        ORDER BY risk DESC
        """,
        (zip, db.forecast_run_utc(), zip),
    )
    caution = caution_message(zip)
    return {
        "zip": zip,
        "shopping_allowed": True,
        "caution": caution,
        # While DURING is being confirmed, show the DURING tips next to the caution note.
        "tips": tips_for("DURING" if caution else get_zip_phase(zip)),
        "items": items,
        "unverified": [item["name"] for item in ESSENTIALS if item["key"] not in {i["essential_key"] for i in items}],
        "forecasts": forecasts,
        "card": f"/api/cards/{zip}",
    }


def _check_rank(row: dict) -> tuple:
    """A real pickup status beats a listing, which beats 'doesn't sell this', which beats a failed search."""
    check_id = -(row.get("check_id") or row.get("id") or 0)
    if row.get("status") and row["status"] != "unknown":
        return (0, check_id)
    if (row.get("matched") or 0) > 0:
        return (1, check_id)
    if row.get("results") is not None:
        return (2, check_id)
    return (3, check_id)


def current_stock_items(*, zip_code: str | None = None, store_id: int | None = None) -> list[dict]:
    """The check the page should show for each essential: not merely the newest row."""
    if zip_code:
        where, params = "c.zip = ?", (zip_code,)
        id_where = "zip = ?"
        id_params = (zip_code,)
    else:
        where, params = "c.store_id = ?", (store_id,)
        id_where = "store_id = ?"
        id_params = (store_id,)
    rows = db.query(
        f"""
        SELECT c.id AS check_id, c.essential_key, c.status, c.price, c.price_flag,
               c.confidence, c.confidence_why, c.level, c.ts_utc, c.source_url,
               c.results, c.matched, c.available,
               s.name AS store_name, s.chain
        FROM stock_checks c
        LEFT JOIN stores s ON s.id = c.store_id
        WHERE {where} AND c.id IN (
          SELECT MAX(id) FROM stock_checks WHERE {id_where} GROUP BY essential_key
          UNION
          SELECT MAX(id) FROM stock_checks WHERE {id_where} AND status != 'unknown' GROUP BY essential_key
          UNION
          SELECT MAX(id) FROM stock_checks WHERE {id_where} AND COALESCE(matched, 0) > 0 GROUP BY essential_key
        )
        """,
        params + id_params + id_params + id_params,
    )
    best: dict[str, dict] = {}
    for row in rows:
        key = row["essential_key"]
        if key not in best or _check_rank(row) < _check_rank(best[key]):
            best[key] = row
    return [best[key] for key in sorted(best)]


def _pictogram(essential_key: str) -> str | None:
    """Phase 5 writes these. Missing file just means no icon on the card."""
    rows = db.query("SELECT pictogram_path FROM essentials WHERE key = ?", (essential_key,))
    path = rows[0]["pictogram_path"] if rows else None
    name = Path(path).name if path else f"{essential_key}.png"
    return f"/images/{name}" if (IMAGE_DIR / name).exists() else None


def _decorate_stock_item(item: dict, *, include_pictogram: bool = False) -> None:
    """Add display fields and the actual products that support one stock check."""
    item["name"] = _NAMES.get(item["essential_key"], item["essential_key"])
    item["checked_min_ago"] = minutes_ago(item["ts_utc"])
    item["checked_hst"] = hst(item["ts_utc"])
    if include_pictogram:
        item["pictogram"] = _pictogram(item["essential_key"])
    # Recorded evidence, aged to now: a 3-hour-old "in stock" is not a fresh one.
    item["confidence"] = confidence.current(
        item.pop("confidence"), item.pop("confidence_why"), item["checked_min_ago"]
    )
    item["products"] = db.query(
        """
        SELECT product_id, name, price, unit_price, in_stock, availability, url, image
        FROM check_products WHERE check_id = ?
        ORDER BY in_stock DESC, id
        """,
        (item["check_id"],),
    )
    chain = item.get("chain") or "walmart"
    store = item.get("store_name") or ("Home Depot" if chain == "home_depot" else "Walmart")
    if item["status"] != "unknown":
        item["result_state"] = "matched"
        item["result_note"] = (
            f"{item['available']} of {item['matched']} matching products available for pickup."
            if item["matched"] is not None
            else None
        )
    elif (item.get("matched") or 0) > 0:
        item["result_state"] = "listed"
        item["result_note"] = (
            f"Listed at {store}. Home Depot's search does not report in-store stock."
            if chain == "home_depot"
            else f"{item['matched']} matching products listed; in-store stock was not reported."
        )
    elif item["results"] is None:
        item["result_state"] = "failed"
        item["result_note"] = f"The latest {store} search failed, so availability could not be verified."
    else:
        item["result_state"] = "no_match"
        hardware = item["essential_key"] in {"generator", "sandbags", "tarp", "gas_can", "propane"}
        extra = " Home Depot is the more likely source." if hardware and chain != "home_depot" else ""
        verb = "returned" if chain == "home_depot" else "pickup returned"
        item["result_note"] = (
            f"{store} {verb} {item['results']} results, but none matched this item.{extra}"
        )


_BUSINESS_STATUS = {
    # Google's business status is "not closed for good", not today's hours or a storm closure.
    "OPERATIONAL": "Listed as operating, not today's hours. Call first.",
    "CLOSED_TEMPORARILY": "Temporarily closed (Google Maps)",
    "CLOSED_PERMANENTLY": "Permanently closed (Google Maps)",
}


def display_address(address_norm: str | None) -> str:
    """address_norm is lower-case with punctuation stripped (it is the dedupe key).
    Put back what people expect: Hawaii's hyphenated street numbers and "HI"."""
    text = str(address_norm or "")
    text = re.sub(r"^(\d{2}) (\d{3,4})\b", r"\1-\2", text)  # "75 1015 henry st" -> "75-1015"
    words = [w.upper() if w == "hi" else (w.capitalize() if w.isalpha() else w) for w in text.split()]
    return " ".join(words)


@app.get("/api/stores")
async def stores(zip: str | None = None) -> dict:
    """Map pins: stores with their latest per-item checks. Areas in DURING are left out."""
    wanted = [zip] if zip else list(REGION_ZIPS)
    allowed = [code for code in wanted if shopping_allowed(code)]
    blocked = [code for code in wanted if code not in allowed]
    rows: list[dict] = []
    if allowed:
        placeholders = ", ".join("?" for _ in allowed)
        rows = db.query(
            f"""
            SELECT id, name, chain, address_norm, zip, lat, lng, hours, source_url, phone
            FROM stores WHERE zip IN ({placeholders}) ORDER BY zip, chain
            """,
            tuple(allowed),
        )
    for row in rows:
        row["address"] = display_address(row["address_norm"])
        row["status_label"] = _BUSINESS_STATUS.get(row["hours"] or "", row["hours"] or "")
        items = current_stock_items(store_id=row["id"])
        for item in items:
            _decorate_stock_item(item)
        row["items"] = items
        row["in_stock"] = sum(1 for item in items if item["status"] == "in_stock")
        row["low_or_out"] = sum(1 for item in items if item["status"] in ("low", "out"))
    # What people in the store saw in the last hour. It outranks the online data on the page.
    fresh = shelf_reports.summaries([row["id"] for row in rows])
    for row in rows:
        row["reports"] = fresh.get(row["id"], {})
    return {"stores": rows, "blocked_zips": blocked, "report_window_min": shelf_reports.REPORT_WINDOW_MIN}


class ShelfReport(BaseModel):
    store_id: int
    essential_key: str
    verdict: str
    device: str


@app.post("/api/reports")
async def report_shelf(report: ShelfReport, request: Request) -> dict:
    """Someone in a store: "shelves have it" or "shelves empty". Rate limited, never an account."""
    ip = request.client.host if request.client else "unknown"
    try:
        summary = shelf_reports.submit(report.store_id, report.essential_key, report.verdict, report.device, ip)
    except shelf_reports.ReportRejected as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"ok": True, "summary": summary}


@app.get("/api/risk")
async def risk(limit: int = 8) -> dict:
    """"Running out next" across the region. DURING areas are left out on purpose."""
    rows = db.query(
        """
        SELECT zip, essential_key, risk, reason, ts_utc FROM forecasts
        WHERE ts_utc >= ? AND id IN (SELECT MAX(id) FROM forecasts GROUP BY zip, essential_key)
        ORDER BY CAST(risk AS REAL) DESC LIMIT ?
        """,
        (db.forecast_run_utc(), max(1, min(limit, 30))),
    )
    items = []
    for row in rows:
        if not shopping_allowed(row["zip"]):
            continue
        row["name"] = _NAMES.get(row["essential_key"], row["essential_key"])
        row["risk"] = float(row["risk"] or 0)
        row["risk_pct"] = int(round(row["risk"] * 100))
        row["seen_min_ago"] = minutes_ago(row["ts_utc"])
        items.append(row)
    return {"items": items}


@app.get("/api/now")
async def now(zip: str | None = None, lat: float | None = None, lng: float | None = None) -> dict:
    """What to do right now, for a ZIP, a location, or (neither) the top-ranked area."""
    return now_card.build(lat, lng, zip.strip()[:5] if zip else None)


@app.get("/api/shelters")
async def shelters_all() -> dict:
    """Every shelter in the saved research, with its sources. Reported, not confirmed open."""
    return {"shelters": shelter_list.all_shelters(), "researched_hst": hst_clock(_agent_state().get("last_shelter_research_utc"))}


@app.get("/api/zip/{zip_code}")
async def zip_info(zip_code: str) -> dict:
    """Someone's own ZIP: which island, and the nearest ZIPs the agent watches."""
    zip_code = zip_code.strip()[:5]
    found = zip_lookup.lookup(zip_code)
    if found is None:
        raise HTTPException(404, "Not a Hawaiʻi ZIP code")
    return found


@app.get("/api/locate")
async def locate(lat: float, lng: float) -> dict:
    """Same as /api/zip, from the browser's location instead of a typed ZIP.

    No ZIP is guessed: a ZIP's census point can sit miles from town, so distances use the real location.
    """
    return zip_lookup.locate(lat, lng)


@app.get("/api/journal")
async def journal(limit: int = 25) -> dict:
    """What the manually triggered agent did, newest first."""
    rows = db.query(
        "SELECT step, ts_utc, phase, action, summary, duration_ms FROM agent_steps ORDER BY step DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    )
    for row in rows:
        row["ts_hst"] = hst_clock(row["ts_utc"])
        row["minutes_ago"] = minutes_ago(row["ts_utc"])
        row["facts"] = step_facts(row["action"], row["summary"])
    state = _agent_state()
    return {
        "steps": rows,
        "mode": "manual",
        "running": _agent_run_lock.locked(),
        "pending_checks": len(state.get("pending_tasks") or {}),
    }


@app.post("/api/agent/run")
async def run_agent_once() -> dict:
    """Run one complete agent cycle after an explicit operator request."""
    if _agent_run_lock.locked():
        raise HTTPException(status_code=409, detail="A search is already running")

    async with _agent_run_lock:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent.agent",
            "--once",
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        if process.returncode:
            lines = output.decode("utf-8", errors="replace").strip().splitlines()
            detail = lines[-1] if lines else "Agent search failed"
            raise HTTPException(status_code=500, detail=detail[:500])
    return {"ok": True}


@app.post("/api/crises/discover")
async def discover_crises(request: crisis_search.CrisisDiscoverRequest) -> dict:
    """Nimble finds recent events; the local model consolidates them for selection."""
    try:
        return await crisis_search.discover(request.scope)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Current-crisis search failed ({type(exc).__name__}). Check Nimble and the local model.",
        ) from exc


@app.post("/api/crises/scans")
async def start_crisis_scan(request: crisis_search.CrisisScanRequest) -> dict:
    """Choose crisis-specific resources locally, then start ZIP-scoped Nimble scans."""
    try:
        return await crisis_search.start_scan(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Crisis supply scan failed ({type(exc).__name__}). Check Nimble and the local model.",
        ) from exc


@app.get("/api/crises/scans/{scan_id}")
async def crisis_scan(scan_id: str) -> dict:
    """Poll finished Nimble tasks without starting or paying for duplicate scans."""
    scan = await crisis_search.get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Unknown crisis scan")
    return scan


@app.get("/api/links")
async def links(limit: int = 12) -> dict:
    """Official (.gov, Red Cross) and news links, apart. Same label twice = one link."""
    rows = db.query(
        "SELECT title, url, kind, found_ts FROM official_links ORDER BY kind = 'shelter' DESC, found_ts DESC"
    )
    official, news, seen = [], [], set()
    for row in rows:
        row["found_hst"] = hst_clock(row["found_ts"])
        row["label"] = link_label(row["title"], row["url"])
        row["official"] = is_official(row["url"])
        key = (row["label"].lower(), row["kind"])
        if key in seen:
            continue
        seen.add(key)
        (official if row["official"] else news).append(row)
    limit = max(1, min(limit, 50))
    return {"links": official[:limit], "news": news[:5]}


@app.get("/api/cards/{zip_code}")
async def card(zip_code: str) -> FileResponse:
    """Shareable status card, rendered now from SQLite (Pillow only, no API calls)."""
    if zip_code not in REGION_ZIPS:
        raise HTTPException(status_code=404, detail="unknown ZIP")
    path = await asyncio.to_thread(render_card, zip_code)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/ask")
async def ask(request: AskRequest) -> dict:
    """Answers from the SQLite snapshot through the local model. Works offline."""
    question = request.question.strip()
    if not question:
        return {"question": "", "answer": "Ask me about supplies, stores, or shelters.", "sources": []}
    return await ask_module.answer(question)
