"""User-driven crisis discovery and crisis-specific ZIP supply scans.

Nimble finds current events and retailer/shelter results. The local Liquid model
consolidates the events and chooses which resources fit the selected crisis.
Retailer templates run asynchronously and survive server restarts in SQLite kv.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from app import db, nimble_client
from app.config import MOCK_NIMBLE
from app.llm import LLMJSONError, chat_json
from app.models import CrisisDiscovery, CrisisResourcePlan
from app.prompts import CRISIS_DISCOVERY_SYSTEM, CRISIS_RESOURCES_SYSTEM

SCAN_KEY = "crisis_scan:"
MAX_RESOURCES = 5
_ZIP = re.compile(r"^\d{5}$")
_AVAILABLE = re.compile(r"in stock|available|only \d+ left|limited", re.I)
_UNAVAILABLE = re.compile(r"out of stock|unavailable", re.I)
_PICKUP_SELLERS = {"", "walmart", "walmart.com"}
_FIRE = re.compile(r"\b(wild)?fires?\b|\bsmoke\b", re.I)
_HAIL = re.compile(r"\bhail", re.I)
_WATER = {
    "name": "Drinking water",
    "kind": "retail",
    "search_terms": "bottled water",
    "why": "Fires disrupt drinking water and force extra hydration.",
}
_SHELTER = {
    "name": "Emergency shelter",
    "kind": "shelter",
    "search_terms": "emergency shelter",
    "why": "Hail can damage roofs and make staying in place unsafe.",
}
_GENERIC = [
    {"name": "Drinking water", "kind": "retail", "search_terms": "bottled water", "why": "Emergency hydration"},
    {"name": "Flashlight", "kind": "retail", "search_terms": "flashlight", "why": "Light during outages"},
    {
        "name": "Emergency shelter",
        "kind": "shelter",
        "search_terms": "emergency shelter",
        "why": "A safer place if ordered to evacuate",
    },
]


class CrisisInput(BaseModel):
    title: str
    crisis_type: str
    location: str
    summary: str
    sources: list[dict[str, Any]] = []


class CrisisDiscoverRequest(BaseModel):
    scope: str = "United States"


class CrisisScanRequest(BaseModel):
    crisis: CrisisInput
    zip_code: str


def _clean(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _http_url(value: Any) -> str | None:
    url = str(value or "").strip()
    return url if url.startswith(("https://", "http://")) else None


def _crisis_text(crisis: dict) -> str:
    return " ".join(str(crisis.get(key) or "") for key in ("crisis_type", "title", "summary"))


def required_resources(crisis: dict) -> list[dict]:
    """Code backup for the local model: fire → water, hail → shelter."""
    text = _crisis_text(crisis)
    needed: list[dict] = []
    if _FIRE.search(text):
        needed.append(dict(_WATER))
    if _HAIL.search(text):
        needed.append(dict(_SHELTER))
    return needed


def merge_resources(planned: list[dict], crisis: dict) -> list[dict]:
    """Keep the model's picks, but never drop the required resource for the crisis type."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in required_resources(crisis) + planned:
        name = _clean(item.get("name"), 80)
        terms = _clean(item.get("search_terms"), 80)
        kind = item.get("kind")
        if kind not in {"retail", "shelter"} or not name or not terms:
            continue
        key = (kind, terms.lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            {
                "id": f"resource-{len(merged) + 1}",
                "name": name,
                "kind": kind,
                "search_terms": terms,
                "why": _clean(item.get("why"), 180),
                "status": "starting",
                "results": [],
            }
        )
        if len(merged) == MAX_RESOURCES:
            break
    return merged


def _planned_from_model(plan: CrisisResourcePlan) -> list[dict]:
    return [
        {"name": item.name, "kind": item.kind, "search_terms": item.search_terms, "why": item.why}
        for item in plan.resources
    ]


async def discover(scope: str = "United States") -> dict:
    """Nimble finds crises happening now; Liquid consolidates duplicate news reports."""
    scope = _clean(scope, 80) or "United States"
    query = f"active natural disasters public emergencies happening now {scope}"
    results = await nimble_client.search(
        query,
        focus="news",
        depth="lite",
        max_results=12,
        time_range="day",
        mock_file="nimble_crisis_search.json",
    )
    if not results:
        results = await nimble_client.search(
            query,
            focus="news",
            depth="lite",
            max_results=12,
            time_range="week",
            mock_file="nimble_crisis_search.json",
        )
    if not results:
        return {"scope": scope, "crises": [], "searched_at": _now()}

    numbered = [
        {
            "id": index,
            "title": _clean(row.get("title"), 220),
            "description": _clean(row.get("description"), 500),
            "url": _http_url(row.get("url")),
        }
        for index, row in enumerate(results, 1)
    ]
    prompt = (
        f"Search scope: {scope}\n"
        f"<results>\n{json.dumps(numbered, ensure_ascii=False)}\n</results>"
    )
    try:
        plan = await chat_json(
            CRISIS_DISCOVERY_SYSTEM,
            prompt,
            CrisisDiscovery,
            example=(
                '{"crises":[{"title":"...","crisis_type":"wildfire","location":"...",'
                '"summary":"...","source_ids":[1,2]}]}'
            ),
            max_tokens=700,
        )
    except (LLMJSONError, Exception):
        plan = CrisisDiscovery(crises=[])

    by_id = {row["id"]: row for row in numbered}
    crises = []
    for item in plan.crises[:6]:
        source_ids = list(dict.fromkeys(source_id for source_id in item.source_ids if source_id in by_id))
        if not source_ids:
            continue
        sources = [by_id[source_id] for source_id in source_ids]
        crises.append(
            {
                "id": f"crisis-{len(crises) + 1}",
                "title": _clean(item.title),
                "crisis_type": _clean(item.crisis_type, 60),
                "location": _clean(item.location, 100),
                "summary": _clean(item.summary, 420),
                "sources": sources,
            }
        )
    if not crises:
        crises = [
            {
                "id": f"crisis-{index}",
                "title": row["title"] or f"Event {index}",
                "crisis_type": "unclassified",
                "location": scope,
                "summary": row["description"] or row["title"],
                "sources": [row],
            }
            for index, row in enumerate(numbered[:6], 1)
            if row.get("url")
        ]
    return {"scope": scope, "crises": crises, "searched_at": _now()}


async def start_scan(request: CrisisScanRequest) -> dict:
    """Ask Liquid what this crisis needs, then start ZIP-scoped Nimble lookups."""
    zip_code = request.zip_code.strip()
    if not _ZIP.fullmatch(zip_code):
        raise ValueError("ZIP code must be exactly 5 digits")

    crisis = {
        "title": _clean(request.crisis.title),
        "crisis_type": _clean(request.crisis.crisis_type, 60),
        "location": _clean(request.crisis.location, 100),
        "summary": _clean(request.crisis.summary, 420),
        "sources": [
            {"title": _clean(row.get("title"), 180), "url": _http_url(row.get("url"))}
            for row in request.crisis.sources[:6]
            if isinstance(row, dict)
        ],
    }
    prompt = f"ZIP code: {zip_code}\n<crisis>\n{json.dumps(crisis, ensure_ascii=False)}\n</crisis>"
    try:
        plan = await chat_json(
            CRISIS_RESOURCES_SYSTEM,
            prompt,
            CrisisResourcePlan,
            example=(
                '{"safety_note":"...","resources":[{"name":"Drinking water","kind":"retail",'
                '"search_terms":"bottled water","why":"Emergency hydration"}]}'
            ),
            max_tokens=700,
        )
    except (LLMJSONError, Exception):
        plan = CrisisResourcePlan(
            safety_note="Confirm official guidance before traveling for supplies.",
            resources=[],
        )

    resources = merge_resources(_planned_from_model(plan), crisis)
    if not resources:
        resources = merge_resources(list(_GENERIC), crisis)
    if not resources:
        raise RuntimeError("The local model did not return any usable crisis resources")

    scan = {
        "id": uuid.uuid4().hex,
        "created_at": _now(),
        "updated_at": _now(),
        "status": "starting",
        "zip_code": zip_code,
        "crisis": crisis,
        "safety_note": _clean(plan.safety_note, 220),
        "resources": resources,
    }
    _save(scan)

    await asyncio.gather(*(_start_resource(scan, resource) for resource in resources))
    scan["status"] = _overall_status(resources)
    scan["updated_at"] = _now()
    _save(scan)
    return scan


async def _start_resource(scan: dict, resource: dict) -> None:
    try:
        if resource["kind"] == "shelter":
            rows = await nimble_client.search(
                f"{resource['search_terms']} near ZIP {scan['zip_code']} for {scan['crisis']['title']}",
                focus="location",
                depth="lite",
                max_results=6,
            )
            resource["results"] = [
                {
                    "name": _clean(row.get("title"), 180),
                    "url": _http_url(row.get("url")),
                    "description": _clean(row.get("description"), 300),
                }
                for row in rows
                if _http_url(row.get("url"))
            ]
            resource["status"] = "complete"
            resource["result_status"] = "leads"
            resource["note"] = "Search leads only—confirm the shelter is open with an official source."
            return

        resource["task_id"] = await nimble_client.run_template_start(
            "walmart_serp",
            {"keyword": resource["search_terms"], "zipcode": scan["zip_code"]},
        )
        resource["status"] = "pending"
        resource["note"] = "Nimble is checking Walmart pickup for this ZIP."
    except Exception as exc:
        resource["status"] = "error"
        resource["error"] = f"{type(exc).__name__}: {_clean(exc, 180)}"


async def get_scan(scan_id: str) -> dict | None:
    scan = _load(scan_id)
    if scan is None:
        return None

    changed = False
    for resource in scan["resources"]:
        if resource.get("status") != "pending" or not resource.get("task_id"):
            continue
        try:
            payload = await nimble_client.run_template_result(resource["task_id"])
        except Exception as exc:
            resource["status"] = "error"
            resource["error"] = f"{type(exc).__name__}: {_clean(exc, 180)}"
            changed = True
            continue
        if payload is None:
            continue
        resource.update(_retailer_result(payload, resource))
        changed = True

    status = _overall_status(scan["resources"])
    if changed or scan.get("status") != status:
        scan["status"] = status
        scan["updated_at"] = _now()
        _save(scan)
    return scan


def _retailer_result(payload: dict, resource: dict) -> dict:
    if payload.get("error"):
        return {"status": "error", "error": _clean(payload["error"], 180)}
    parsing = (payload.get("data") or {}).get("parsing")
    if MOCK_NIMBLE and not isinstance(parsing, list):
        return {
            "status": "complete",
            "result_status": "available",
            "note": "Mock result: replace with a live Nimble key for current availability.",
            "results": [
                {
                    "name": f"Mock {resource['name']}",
                    "availability": "In stock",
                    "in_stock": True,
                    "price": 9.99,
                    "url": "https://example.com/mock-product",
                    "image": None,
                }
            ],
        }
    if not isinstance(parsing, list):
        return {"status": "error", "error": "Nimble returned no retailer product list"}

    products = []
    seen: set[str] = set()
    for row in parsing:
        if not isinstance(row, dict):
            continue
        seller = _clean(row.get("product_seller"), 80).lower()
        if seller not in _PICKUP_SELLERS:
            continue
        name = _clean(row.get("product_name"), 180)
        ident = _clean(row.get("product_id") or row.get("product_item_id") or name.lower(), 180)
        if not name or ident in seen:
            continue
        seen.add(ident)
        availability = _clean(row.get("product_availability"), 100)
        unavailable = bool(row.get("product_out_of_stock")) or bool(_UNAVAILABLE.search(availability))
        in_stock = not unavailable and bool(_AVAILABLE.search(availability))
        price = row.get("product_price")
        if isinstance(price, list):
            price = price[0] if price else None
        products.append(
            {
                "name": name,
                "availability": availability or "Availability not stated",
                "in_stock": in_stock,
                "price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
                "url": _http_url(row.get("product_url")),
                "image": _http_url(row.get("product_image")),
            }
        )
        if len(products) == 8:
            break

    available = sum(1 for product in products if product["in_stock"])
    result_status = "available" if available else ("unavailable" if products else "no_results")
    note = (
        f"{available} of {len(products)} returned products show pickup availability."
        if products
        else "No Walmart pickup products matched this resource."
    )
    return {"status": "complete", "result_status": result_status, "note": note, "results": products}


def _overall_status(resources: list[dict]) -> str:
    return "complete" if all(row.get("status") in {"complete", "error"} for row in resources) else "running"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(scan: dict) -> None:
    db.set_kv(f"{SCAN_KEY}{scan['id']}", json.dumps(scan, ensure_ascii=False))


def _load(scan_id: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{32}", scan_id):
        return None
    raw = db.get_kv(f"{SCAN_KEY}{scan_id}")
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
