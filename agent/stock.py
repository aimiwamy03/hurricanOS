"""Stock checks on the one rung Phase 0 proved: walmart_serp with the ZIP.

Walmart reports pickup availability for the ZIP's store, so results are level="zip".
A check starts on one cycle (start_check) and is recorded when a later cycle
collects the finished task (record_walmart_result). Runs take 5-11 minutes.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import datetime

from app import db, nimble_client
from app.confidence import evidence_score
from app.config import ESSENTIALS, PT, STOCK_CHECKS_UNTIL_PT
from app.safety import shopping_allowed
from app.tinybird_client import log_event
from agent.forecast import latest_risk
from agent.util import iso_ago, log, minutes_since, now_iso, tb_ts

TEMPLATE = "walmart_serp"
HD_TEMPLATE = "homedepot_serp"
# Hardware Walmart pickup in Hawaiʻi does not actually sell. Home Depot localizes to
# the island store, but its template has no in-stock field, so those checks are listings.
HD_ITEMS = {"generator", "sandbags", "tarp", "gas_can", "propane"}
# No minimum age: a check is re-queued in the same cycle its result is collected.
# A 15-minute gate left every other cycle idle, since results land ~9 min after start.
# Items Walmart never matches (generator, sandbags) only get a retry once an hour.
UNKNOWN_RETRY_MINUTES = 60
_ESSENTIAL = {item["key"]: item for item in ESSENTIALS}

# A product counts toward an essential only if its name matches _MATCH and not _NOT.
# Walmart search returns plenty of near misses (battery chargers, reusable water bottles,
# canopy weights for "sandbags"), so every rule was written against real result pages.
_MATCH = {
    "generator": r"\bgenerator\b",
    "d_batteries": r"\bD\b.*(batter|cell)|batter.*\bD\b",
    "aa_batteries": r"\bAA\b.*(batter|cell)|batter.*\bAA\b",
    "flashlight": r"flashlight|headlamp|work light",
    # Require drinking-water language. A broad ``water`` match also returned filters,
    # toys and reusable bottles; rejecting every word "bottle" then hid gallon jugs.
    "bottled_water": (
        r"(drinking|purified|spring|distilled|mineral|alkaline|bottled).*\bwater\b|"
        r"\bwater\b.*(drinking|purified|spring|distilled|mineral|alkaline|case|pack|gallon|fl oz|\boz\b)"
    ),
    "tarp": r"\btarps?\b",
    "sandbags": r"sand ?bags?",
    "first_aid_kit": r"first aid",
    "power_bank": r"power ?bank|portable charger",
    "gas_can": r"gas can|fuel can|gas container|fuel container|jerry can",
    "aaa_batteries": r"\bAAA\b.*(batter|cell)|batter.*\bAAA\b",
    "lantern": r"\blanterns?\b",
    "weather_radio": r"(weather|emergency|crank|noaa|solar).*\bradio\b|\bradio\b.*(weather|emergency|crank|noaa)",
    "canned_food": r"\bcann?ed\b|\bcans?\b",
    "can_opener": r"can opener",
    "water_jug": r"water.*(container|jug|carrier|storage|jerry)|(container|jug|carrier|jerry).*water",
    "propane": r"\bpropane\b",
    "camp_stove": r"\bstove\b",
    "cooler": r"\bcoolers?\b",
    "duct_tape": r"duct tape",
}
_NOT = {
    "d_batteries": r"charger|\bAA\b|\bAAA\b|\bC\b and|flashlight",
    "aa_batteries": r"charger|flashlight|\bAAA only",
    "aaa_batteries": r"charger|flashlight",
    "flashlight": r"\bapp\b|holster|replacement bulb",
    "bottled_water": r"filter|opener|heater|\bgun\b|balloon|shoe|flosser|squirt|hose|resistant|proof|"
                     r"stainless|insulated|reusable|tumbler|jug|container|beads|toy|enhancer",
    "tarp": r"strap|clip|bungee|tie[- ]?down|grommet kit",
    "first_aid_kit": r"refill|sign|cabinet only|book",
    "power_bank": r"case|cable only|holder",
    "gas_can": r"chafing|sterno|wick|canned heat|fondue|heater",
    "generator": r"parts|cover|cord|oil|\bspark\b|wheel kit|bubble",
    "lantern": r"string lights|decor|wedding|paper|hanging hook",
    "canned_food": r"opener|trash|watering|gas can|spray|garbage|storage|organizer|dog|cat|pet|koozie|crusher|air|paint",
    "can_opener": r"electric|replacement|battery",
    "water_jug": r"filter|bottle(?!d)|pitcher|dispenser pump|pet|dog|cat|bird|chicken|plant|squirt|heater|tumbler",
    "propane": r"torch|adapter|hose|regulator|cover|refill kit|gauge|heater|fire pit|grill(?!.*cylinder)|lantern mantle",
    "camp_stove": r"stove ?top|oven|kettle|pot\b|cover|mat\b|protector|wood stove|toy",
    "cooler": r"air cooler|evaporative|wine|fan\b|coolant|cooler bag clip|swamp|laptop|cpu|shock|lock",
    "duct_tape": r"dispenser only|remover",
}
_MATCH_RE = {key: re.compile(pattern, re.I) for key, pattern in _MATCH.items()}
_NOT_RE = {key: re.compile(pattern, re.I) for key, pattern in _NOT.items()}
# Store pickup is only sold by Walmart itself; marketplace sellers ship, so their listings
# say nothing about the store shelf even when the search is filtered to pickup.
_PICKUP_SELLERS = {"", "walmart.com", "walmart"}
_LOW = re.compile(r"only (\d+) left|limited", re.I)
PRODUCTS_KEPT = 8  # per check, for the "which products" list on the page


def walmart_store(zip_code: str) -> dict:
    """The Walmart store row for this ZIP. Creates a placeholder until maps finds the real one."""
    rows = db.query("SELECT * FROM stores WHERE chain = 'walmart' AND zip = ? ORDER BY address_norm LIKE 'zip %', id LIMIT 1", (zip_code,))
    if rows:
        return rows[0]
    store_id = db.upsert_store(
        {
            "name": f"Walmart (pickup for {zip_code})",
            "chain": "walmart",
            "address_norm": f"zip {zip_code}",
            "zip": zip_code,
            "lat": None,
            "lng": None,
            "hours": "",
            "source_url": "https://www.walmart.com/",
            "found_ts": now_iso(),
        }
    )
    return db.query("SELECT * FROM stores WHERE id = ?", (store_id,))[0]


def home_depot_store(zip_code: str) -> dict | None:
    """The Home Depot the maps scan found for this ZIP, if any. No placeholder: no store, no check."""
    rows = db.query(
        "SELECT * FROM stores WHERE chain = 'home_depot' AND zip = ? ORDER BY id LIMIT 1",
        (zip_code,),
    )
    return rows[0] if rows else None


# Storm searches, store rescans and shelter research also spend Nimble calls: about 4 per
# cycle in data/agent.log (475 -> 456 left after starting 15 checks). Held back first.
OTHER_CALLS_PER_CYCLE = 4


def cycles_left(cycle_seconds: int, now: datetime | None = None) -> int:
    """Cycles until STOCK_CHECKS_UNTIL_PT today; 0 once it has passed."""
    now = now or datetime.now(PT)
    end = now.replace(hour=STOCK_CHECKS_UNTIL_PT[0], minute=STOCK_CHECKS_UNTIL_PT[1], second=0, microsecond=0)
    if end <= now:
        return 0
    return max(1, math.ceil((end - now).total_seconds() / cycle_seconds))


def budget_this_cycle(remaining: int, cycle_seconds: int, now: datetime | None = None) -> int:
    """Even share of the calls left until the end time, after what the other stages need."""
    cycles = cycles_left(cycle_seconds, now)
    if cycles == 0:
        return 0
    spare = remaining - OTHER_CALLS_PER_CYCLE * cycles
    return max(0, min(40, spare // cycles))


def pick_checks(budget: int, pending_pairs: set[tuple[str, str]]) -> list[tuple[dict, dict]]:
    """Score every (Walmart store, essential) pair in shopping ZIPs and take the top N."""
    scored = []
    for zip_row in db.query("SELECT zip, priority, phase FROM zips"):
        if not shopping_allowed(zip_row["zip"]):
            continue  # no shopping advice while storm conditions are on
        store = walmart_store(zip_row["zip"])
        for key, essential in _ESSENTIAL.items():
            if (zip_row["zip"], key) in pending_pairs:
                continue
            last = db.query(
                "SELECT ts_utc, status, results FROM stock_checks WHERE zip = ? AND essential_key = ? "
                "ORDER BY ts_utc DESC LIMIT 2",
                (zip_row["zip"], key),
            )
            age = minutes_since(last[0]["ts_utc"]) if last else None
            never_matches = len(last) == 2 and all(row["status"] == "unknown" for row in last)
            no_match = bool(last) and last[0]["status"] == "unknown" and last[0]["results"] is not None
            # One confirmed "Walmart doesn't sell this" is enough; retry hourly. Home Depot
            # picks these up below instead of burning another Walmart search.
            if (never_matches or no_match) and age is not None and age < UNKNOWN_RETRY_MINUTES:
                continue
            score = zip_row["priority"] or 0
            if not last:
                score += 30
            elif age is not None and age > 60:
                score += 20
            if last and last[0]["status"] == "low":
                score += 15
            if latest_risk(zip_row["zip"], key) >= 0.3:
                score += 10
            scored.append((score, store, essential))
        hd = home_depot_store(zip_row["zip"])
        if not hd:
            continue
        for key in HD_ITEMS:
            if (zip_row["zip"], key) in pending_pairs:
                continue
            walmart = db.query(
                "SELECT c.status, c.results FROM stock_checks c JOIN stores s ON s.id = c.store_id "
                "WHERE c.zip = ? AND c.essential_key = ? AND s.chain = 'walmart' ORDER BY c.id DESC LIMIT 1",
                (zip_row["zip"], key),
            )
            if not walmart or walmart[0]["status"] != "unknown" or walmart[0]["results"] is None:
                continue  # Walmart still sells it, or has not finished a real search yet
            last_hd = db.query(
                "SELECT c.ts_utc FROM stock_checks c JOIN stores s ON s.id = c.store_id "
                "WHERE c.zip = ? AND c.essential_key = ? AND s.chain = 'home_depot' ORDER BY c.id DESC LIMIT 1",
                (zip_row["zip"], key),
            )
            age = minutes_since(last_hd[0]["ts_utc"]) if last_hd else None
            if age is not None and age < UNKNOWN_RETRY_MINUTES:
                continue
            scored.append((25 + (zip_row["priority"] or 0), hd, _ESSENTIAL[key]))
    scored.sort(key=lambda item: -item[0])
    picked: list[tuple[dict, dict]] = []
    seen: set[tuple[str, str]] = set()
    for _score, store, essential in scored:
        pair = (store["zip"], essential["key"])
        if pair in seen:
            continue
        seen.add(pair)
        picked.append((store, essential))
        if len(picked) >= budget:
            break
    return picked


async def start_check(store: dict, essential: dict) -> tuple[str, dict] | None:
    template = HD_TEMPLATE if store.get("chain") == "home_depot" else TEMPLATE
    params = {"keyword": essential["search_terms"], "zipcode": store["zip"]}
    try:
        task_id = await nimble_client.run_template_start(template, params)
    except Exception as exc:
        log("stock", f"could not start {store['zip']} {essential['key']}: {type(exc).__name__}: {exc}")
        return None
    meta = {
        "zip": store["zip"],
        "store_id": store["id"],
        "essential_key": essential["key"],
        "template": template,
        "chain": store.get("chain") or "walmart",
    }
    return task_id, {"kind": "stock", "meta": meta, "started_utc": now_iso()}


def matching_products(essential_key: str, products: list[dict], *, pickup_only: bool = True) -> list[dict]:
    """Products whose name is really this essential, sold by Walmart itself, once each.

    pickup_only: Walmart marketplace sellers ship and do not offer store pickup.
    Home Depot listings have no seller field, so those checks pass pickup_only=False.
    """
    pattern = _MATCH_RE.get(essential_key) or re.compile(re.escape(essential_key), re.I)
    exclude = _NOT_RE.get(essential_key)
    seen: set[str] = set()
    found = []
    for product in products:
        if not isinstance(product, dict):
            continue
        name = str(product.get("product_name") or product.get("name") or "")
        if not pattern.search(name) or (exclude and exclude.search(name)):
            continue
        if pickup_only and str(product.get("product_seller") or "").strip().lower() not in _PICKUP_SELLERS:
            continue
        ident = str(product.get("product_id") or product.get("product_item_id") or name.lower())
        if ident in seen:
            continue  # Walmart repeats sponsored items further down the page
        seen.add(ident)
        found.append(product)
    return found


def _available(product: dict) -> bool:
    availability = str(product.get("product_availability") or "")
    if product.get("product_out_of_stock") or re.search(r"out of stock|unavailable", availability, re.I):
        return False
    return bool(re.search(r"in stock|available|only \d+ left|limited", availability, re.I))


def _price(product: dict) -> float | None:
    value = product.get("product_price")
    if isinstance(value, list):
        value = value[0] if value else None
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def product_rows(essential_key: str, products: list[dict], *, pickup_only: bool = True) -> list[dict]:
    """The matched products worth showing: available first, then Walmart's own order."""
    rows = []
    for product in matching_products(essential_key, products, pickup_only=pickup_only):
        rows.append({
            "product_id": str(product.get("product_id") or ""),
            "name": str(product.get("product_name") or "")[:160],
            "price": _price(product),
            "unit_price": str(product.get("product_price_per_unit") or "") or None,
            "in_stock": int(_available(product)),
            "availability": str(product.get("product_availability") or ""),
            # Drop Walmart's tracking query string; the item page link is enough.
            "url": str(product.get("product_url") or "").split("?")[0] or None,
            "image": str(product.get("product_image") or "").split("?")[0] or None,
        })
    rows.sort(key=lambda row: -row["in_stock"])
    return rows


def status_from_products(essential_key: str, products: list[dict]) -> tuple[str, float | None]:
    """Status mapping, in code. Walmart's search is filtered to store pickup for the ZIP."""
    relevant = matching_products(essential_key, products)
    if not relevant:
        return "unknown", None
    in_stock, low = [], []
    for product in relevant:
        if not _available(product):
            continue
        availability = str(product.get("product_availability") or "")
        match = _LOW.search(availability)
        if match and (not match.group(1) or int(match.group(1)) <= 5):
            low.append(product)
        else:
            in_stock.append(product)
    available = in_stock + low
    prices = [price for price in (_price(p) for p in available) if price]
    price = round(statistics.median(prices), 2) if prices else None
    if len(in_stock) >= 3:
        return "in_stock", price
    if available:
        return "low", price
    return "out", None


def _price_jump(zip_code: str, essential_key: str, price: float | None) -> bool:
    """Price above 1.5x the lowest price for the same item and ZIP in the last 24 h."""
    if not price:
        return False
    rows = db.query(
        "SELECT MIN(price) AS low FROM stock_checks WHERE zip = ? AND essential_key = ? AND price > 0 "
        "AND ts_utc > ?",
        (zip_code, essential_key, iso_ago(24)),
    )
    low = rows[0]["low"] if rows else None
    if low and price > 1.5 * low:
        log("stock", f"price jump {zip_code} {essential_key}: ${price} vs ${low} low today")
        return True
    return False


def _parsing_products(payload: dict) -> list[dict] | None:
    """None when the task failed or the payload was not a product list."""
    if payload.get("error"):
        return None
    parsing = (payload.get("data") or {}).get("parsing")
    if isinstance(parsing, dict):
        parsing = parsing.get("products") or parsing.get("items") or parsing.get("results") or []
    if not isinstance(parsing, list):
        return None
    return [product for product in parsing if isinstance(product, dict)]


def _hd_product(product: dict) -> dict:
    """Home Depot's template uses name/url/image, not Walmart's product_* field names."""
    return {
        "product_id": str(product.get("product_id") or ""),
        "product_name": str(product.get("name") or product.get("product_name") or ""),
        "product_seller": "homedepot.com",
        "product_availability": "Listed",
        "product_price": product.get("price") if not isinstance(product.get("price"), list) else (product.get("price") or [None])[0],
        "product_price_per_unit": None,
        "product_url": str(product.get("url") or product.get("product_url") or ""),
        "product_image": str(product.get("image") or product.get("product_image") or ""),
        "store_location": str(product.get("store_location") or ""),
    }


def record_result(meta: dict, payload: dict) -> str:
    """Save one finished retailer search. Home Depot is listings only; Walmart has pickup stock."""
    if meta.get("template") == HD_TEMPLATE or meta.get("chain") == "home_depot":
        return record_homedepot_result(meta, payload)
    return record_walmart_result(meta, payload)


def record_homedepot_result(meta: dict, payload: dict) -> str:
    """Save matching Home Depot listings. Status stays unknown: the template has no stock field."""
    raw = _parsing_products(payload)
    failed = raw is None
    products = [] if failed else [_hd_product(product) for product in raw]
    shown = [] if failed else product_rows(meta["essential_key"], products, pickup_only=False)
    prices = [price for price in (row["price"] for row in shown) if price]
    price = round(statistics.median(prices), 2) if prices else None
    source_url = str(payload.get("url") or "https://www.homedepot.com/")
    store = home_depot_store(meta["zip"]) or walmart_store(meta["zip"])
    place = next((product.get("store_location") for product in products if product.get("store_location")), None)
    if shown:
        confidence, reasons = 0.35, [
            f"listed at Home Depot{f' {place}' if place else ''} (no in-store stock field)"
        ]
    else:
        confidence, reasons = 0.0, ["no matching product in the results"]
    row = {
        "ts_utc": now_iso(),
        "store_id": store["id"],
        "zip": meta["zip"],
        "essential_key": meta["essential_key"],
        "status": "unknown",
        "price": price,
        "confidence": confidence,
        "level": "zip",
        "source_url": source_url,
    }
    check_id = db.insert("stock_checks", {
        **row,
        "price_flag": 0,
        "confidence_why": " · ".join(reasons),
        "results": None if failed else len(products),
        "matched": len(shown),
        "available": 0,
    })
    for product in shown[:PRODUCTS_KEPT]:
        db.insert("check_products", {**product, "check_id": check_id, "zip": meta["zip"], "essential_key": meta["essential_key"]})
    log_event("stock_checks", {**row, "ts_utc": tb_ts(), "store_id": str(row["store_id"]), "price": price or 0.0})
    detail = "check failed" if failed else f"{len(shown)} matching listed, {len(products)} results"
    log("stock", f"{meta['zip']} {meta['essential_key']}: listed at Home Depot, {detail}, confidence {confidence}")
    return "unknown"


def record_walmart_result(meta: dict, payload: dict) -> str:
    """Save one finished check to SQLite and queue it for Tinybird. Returns the status."""
    parsing = (payload.get("data") or {}).get("parsing")
    failed = bool(payload.get("error")) or not isinstance(parsing, list)
    products = parsing if isinstance(parsing, list) else []
    if failed:
        status, price = "unknown", None
    else:
        status, price = status_from_products(meta["essential_key"], products)
    shown = [] if failed else product_rows(meta["essential_key"], products)
    source_url = str(payload.get("url") or "https://www.walmart.com/")
    price_flag = _price_jump(meta["zip"], meta["essential_key"], price)
    previous = db.query(
        "SELECT status FROM stock_checks WHERE zip = ? AND essential_key = ? AND status != 'unknown' "
        "ORDER BY ts_utc DESC LIMIT 1",
        (meta["zip"], meta["essential_key"]),
    )
    confidence, reasons = evidence_score(
        status=status,
        level="zip",
        matched=len(matching_products(meta["essential_key"], products)),
        previous=previous[0]["status"] if previous else None,
        price_flag=price_flag,
    )
    # Resolve at record time: maps may have found the real store since the check started.
    store_id = walmart_store(meta["zip"])["id"]
    row = {
        "ts_utc": now_iso(),
        "store_id": store_id,
        "zip": meta["zip"],
        "essential_key": meta["essential_key"],
        "status": status,
        "price": price,
        "confidence": confidence,
        "level": "zip",
        "source_url": source_url,
    }
    check_id = db.insert("stock_checks", {
        **row, "price_flag": int(price_flag), "confidence_why": " · ".join(reasons),
        # results is NULL when the check failed; 0 matched out of N results means Walmart
        # pickup doesn't sell it here, which is worth saying on the page.
        "results": None if failed else len(products),
        "matched": len(shown),
        "available": sum(product["in_stock"] for product in shown),
    })
    for product in shown[:PRODUCTS_KEPT]:
        db.insert("check_products", {**product, "check_id": check_id, "zip": meta["zip"], "essential_key": meta["essential_key"]})
    log_event("stock_checks", {**row, "ts_utc": tb_ts(), "store_id": str(row["store_id"]), "price": price or 0.0})
    detail = "check failed" if failed else f"{sum(p['in_stock'] for p in shown)} of {len(shown)} matching available, {len(products)} results"
    log(
        "stock",
        f"{meta['zip']} {meta['essential_key']}: {status}" + (f" (${price})" if price else "")
        + f", {detail}, confidence {confidence}",
    )
    return status
