"""The answer behind /api/ask.

Every fact comes out of SQLite, so this keeps working with the wifi off. Code picks the
facts; the local model only turns them into a sentence. If the model is down, the
code-built answer goes out instead, so /ask never returns nothing.
"""

from __future__ import annotations

import asyncio
import json
import re

from app import confidence, db, shelters
from app import reports as shelf_reports
from app.config import ACTIVE_AREAS, ESSENTIALS, REGION_ZIPS, STORM_NAME
from app.connectivity import is_online
from app.llm import chat, chat_json
from app.models import AskQuery
from app.prompts import ASK_SYSTEM, QUERY_EXAMPLE, QUERY_SYSTEM
from app.safety import SAFETY_MESSAGE, is_official, island_of, link_label, shopping_allowed
from app.times import hst, hst_clock, minutes_ago
from app.zips import lookup

MAX_QUESTION_CHARS = 300
MAX_SHELTER_CHARS = 1300
NEAREST_SHELTERS = 3
# Reading the question is a short job. Past this, the keyword reading is good enough.
QUERY_TIMEOUT_S = 20
INTENTS = ("shelter", "running_out", "item", "safety", "supplies")
# LFM2.5 answered with silence when it got all 2,000 characters of facts, so the model
# sees only the top of the list. The page still shows every fact under "facts used".
MODEL_CONTEXT_CHARS = 1100
_NAMES = {item["key"]: item["name"] for item in ESSENTIALS}
_ZIP_IN_TEXT = re.compile(r"\b9\d{4}\b")
_SHELTER_WORDS = ("shelter", "evacuat", "closure", "closed", "where do i go", "where do we go", "where should we go", "where should i go")
_RUNNING_OUT_WORDS = ("running out", "run out", "running low", "sold out", "at risk", "disappear", "short on")
_SAFETY_WORDS = ("safe", "drive", "driving", "travel", "should i go", "still time", "time to")
# What people call each essential. Naming the item keeps the model's facts list tiny.
_ITEM_WORDS = {
    "generator": ("generator",),
    "d_batteries": ("d batteries", "d-cell", "batteries"),
    "aa_batteries": ("aa batteries", "aa battery", "batteries"),
    "flashlight": ("flashlight", "torch"),
    "bottled_water": ("water",),
    "tarp": ("tarp", "tarpaulin"),
    "sandbags": ("sandbag",),
    "first_aid_kit": ("first aid", "bandage"),
    "power_bank": ("power bank", "battery pack", "charger"),
    "gas_can": ("gas can", "gasoline can", "fuel can", "jerry can"),
    "aaa_batteries": ("aaa batteries", "aaa battery"),
    "lantern": ("lantern",),
    "weather_radio": ("weather radio", "emergency radio", "noaa radio", "hand crank radio"),
    "canned_food": ("canned food", "nonperishable food", "non-perishable food"),
    "can_opener": ("can opener",),
    "water_jug": ("water container", "water jug", "water storage"),
    "propane": ("propane",),
    "camp_stove": ("camp stove", "camping stove"),
    "cooler": ("cooler", "ice chest"),
    "duct_tape": ("duct tape",),
}
# "low" only means Walmart search showed few matching products, not a shelf count.
# Walmart online pickup search, not a shelf count: the model must not say "in stock on the shelf".
_STATUS_WORDS = {"in_stock": "available online", "low": "few online", "out": "none online"}
# Item-name words too common to prove an item was meant ("can my family..." is not a gas can).
_VAGUE = {"can", "kit", "first", "aid", "bank", "power", "bottled"}
# Sentence ends, but not after a month ("Opening Friday, Sept. 25 at 6 a.m.").
_SENTENCE_END = re.compile(
    r"(?<=[.!?])(?<!\bJan\.)(?<!\bFeb\.)(?<!\bMar\.)(?<!\bApr\.)(?<!\bAug\.)(?<!\bSep\.)"
    r"(?<!\bSept\.)(?<!\bOct\.)(?<!\bNov\.)(?<!\bDec\.)\s+"
)
_HEDGE = re.compile(r"^(i cannot|i can't|i do not|i don't|unfortunately|please refer|please check with)", re.I)
_URL_IN_TEXT = re.compile(r"https?://\S+")
# What is left of "Official link: https://..." once the URL is cut: drop it.
_BARE_LABEL = re.compile(r"^(official links?|news reports?( \(not official\))?|sources?|link)( \(\w+\))?[\s:—-]*\.?$", re.I)
# Words too general to point at one area.
_STOPWORDS = {"hawaii", "island", "east", "west", "central", "south", "north", "mixed"}
# Towns people actually type, mapped to the ZIP that covers them.
_TOWNS = {
    "kailua": "96740",
    "keauhou": "96740",
    "captain cook": "96740",
    "keaau": "96749",
    "puna": "96778",
    "pahoa": "96778",
    "kahului": "96732",
    "wailuku": "96793",
    "kihei": "96753",
}


def _area_tokens() -> dict[str, set[str]]:
    """{zip: {words that point at it}} — "Hilo / East Hawaii" gives {"hilo"}."""
    tokens: dict[str, set[str]] = {}
    for name, area in ACTIVE_AREAS.items():
        words = {word for word in re.split(r"[^a-z]+", name.lower()) if word and word not in _STOPWORDS}
        for zip_code in area["zips"]:
            tokens.setdefault(zip_code, set()).update(words)
            tokens[zip_code].add(zip_code)
    return tokens


def _ranked_zips() -> list[str]:
    ranked = [row["zip"] for row in db.query("SELECT zip FROM zips ORDER BY priority DESC, zip")]
    return [z for z in ranked if z in REGION_ZIPS] or list(REGION_ZIPS)


def _watched_near(zip_code: str) -> str | None:
    """Any Hawaiʻi ZIP -> itself if watched, else the nearest watched ZIP on the same island."""
    if zip_code in REGION_ZIPS:
        return zip_code
    info = lookup(zip_code) or {}
    same_island = [row["zip"] for row in info.get("nearest", []) if row["same_island"]]
    return same_island[0] if same_island else None


def _named_zips(text: str) -> list[str]:
    """Watched ZIPs the text points at, in the order found. Empty if none."""
    text = shelters.plain(text)  # "Kailua-Kona", "Hāmākua" and "hilo" all match
    found: list[str] = []
    for zip_code, words in _area_tokens().items():
        if any(word in text for word in words):
            found.append(zip_code)
    named = [zip_code for town, zip_code in _TOWNS.items() if town in text]
    named += [zip_code for town, zip_code in shelters.TOWN_ZIPS.items() if re.search(rf"\b{re.escape(town)}\b", text)]
    named += _ZIP_IN_TEXT.findall(text)
    for zip_code in named:
        near = _watched_near(zip_code)
        if near and near not in found:
            found.append(near)
    return found


def zips_for(question: str) -> list[str]:
    """ZIPs the question is about. Nothing recognised means all of them, busiest first."""
    found = _named_zips(question)
    ranked = _ranked_zips()
    return sorted(found, key=ranked.index) if found else ranked


def snapshot_iso() -> str | None:
    """Newest timestamp anywhere in the snapshot."""
    stamps = []
    for table in ("stock_checks", "storm_updates", "agent_steps"):
        rows = db.query(f"SELECT MAX(ts_utc) AS ts FROM {table}")
        if rows and rows[0]["ts"]:
            stamps.append(str(rows[0]["ts"]))
    return max(stamps) if stamps else None


def _storm_lines(sources: list[dict]) -> list[str]:
    rows = db.query("SELECT ts_utc, summary, source_url FROM storm_updates ORDER BY id DESC LIMIT 1")
    if not rows:
        return []
    row = rows[0]
    if row["source_url"]:
        sources.append({"title": f"Latest advisory read at {hst_clock(row['ts_utc'])}", "url": row["source_url"]})
    lines = []
    official = db.query(
        "SELECT official_text FROM storm_updates WHERE official_text IS NOT NULL ORDER BY id DESC LIMIT 1"
    )
    if official:
        lines.append(f"Official NHC watches and warnings: {official[0]['official_text']}")
    lines.append(f"Storm {STORM_NAME}, read at {hst_clock(row['ts_utc'])}: {(row['summary'] or '').strip()[:250]}")
    return lines


def _phase_lines(zips: list[str]) -> list[str]:
    """Blunt on purpose. Given a hedged sentence, the small model answered safety
    questions backwards, so the phase is spelled out as an instruction."""
    lines = []
    read = db.query("SELECT ts_utc FROM storm_updates ORDER BY id DESC LIMIT 1")
    as_of = f" as of the advisory read at {hst_clock(read[0]['ts_utc'])}" if read else ""
    for zip_code in zips:
        rows = db.query("SELECT zip, area, phase FROM zips WHERE zip = ?", (zip_code,))
        area = rows[0]["area"] if rows else zip_code
        if shopping_allowed(zip_code):
            # Code cannot see roads or wind, so it never says "safe to drive".
            lines.append(
                f"{area} ({zip_code}) is in phase BEFORE: storm conditions have NOT arrived{as_of}. "
                "Shop early today, before conditions worsen, and check official guidance and road closures before driving."
            )
        else:
            lines.append(
                f"{area} ({zip_code}) is in phase DURING: storm conditions are expected or happening. "
                "It is NOT safe to drive. Nobody should go to a store. Shelter instead."
            )
    return lines


def items_in(question: str) -> list[str]:
    text = question.lower()
    return [key for key, words in _ITEM_WORDS.items() if any(word in text for word in words)]


def _report_lines(zip_code: str, keys: list[str] | None = None) -> list[str]:
    """Shelf reports from people in the store, last hour. They go before the online lines."""
    stores = {row["id"]: row["name"] for row in db.query("SELECT id, name FROM stores WHERE zip = ?", (zip_code,))}
    found = []
    for store_id, items in shelf_reports.summaries(list(stores)).items():
        for key, summary in items.items():
            if not keys or key in keys:
                found.append((store_id, key, summary))
    # Where people saw it on the shelf comes first: that is the answer someone in a rush needs.
    found.sort(key=lambda row: ({"has": 0, "mixed": 1, "empty": 2}[row[2]["verdict"]], row[2]["newest_min"]))
    return ["Shelf report from people in the store: " +
            shelf_reports.sentence(_NAMES.get(key, key), stores[store_id], summary)
            for store_id, key, summary in found]


def _stock_lines(zip_code: str, keys: list[str] | None = None, low_first: bool = False) -> list[str]:
    rows = db.query(
        f"""
        SELECT c.essential_key, c.status, c.price, c.level, c.ts_utc, c.confidence,
               c.confidence_why, c.results, c.matched, c.available, s.name AS store_name
        FROM stock_checks c
        LEFT JOIN stores s ON s.id = c.store_id
        WHERE c.zip = ? AND c.id IN (
          SELECT MAX(id) FROM stock_checks WHERE zip = ? GROUP BY essential_key
        )
        ORDER BY c.status = 'in_stock' {'ASC' if low_first else 'DESC'}, c.essential_key
        LIMIT 20
        """,
        (zip_code, zip_code),
    )
    if keys:
        rows = [row for row in rows if row["essential_key"] in keys]
    lines = []
    for row in rows:
        name = _NAMES.get(row["essential_key"], row["essential_key"])
        where = row["store_name"] or "a store in the ZIP"
        price = f", ${row['price']:.2f}" if row["price"] else ""
        scope = "at that store" if row["level"] == "store" else f"for pickup in {zip_code}"
        if row["status"] == "unknown" and row["results"] is not None:
            status = f"no matching pickup product among {row['results']} search results"
        elif row["status"] == "unknown":
            status = "availability not verified because the latest search failed"
        else:
            status = _STATUS_WORDS.get(row["status"], row["status"].replace("_", " "))
        age = minutes_ago(row["ts_utc"])
        trust = confidence.current(row["confidence"], row["confidence_why"], age)["label"]
        lines.append(f"{name}: {status} {scope} ({where}{price}), checked {age} min ago, {trust} confidence.")
    return lines


def _risk_lines(zips: list[str], keys: list[str] | None = None) -> list[str]:
    """keys: only forecasts for the items asked about (a batteries question gets no gas-can line)."""
    lines = []
    for zip_code in zips:
        rows = db.query(
            """
            SELECT essential_key, risk, reason FROM forecasts
            WHERE zip = ? AND ts_utc >= ? AND id IN (SELECT MAX(id) FROM forecasts WHERE zip = ? GROUP BY essential_key)
            ORDER BY risk DESC LIMIT 4
            """,
            (zip_code, db.forecast_run_utc(), zip_code),
        )
        lines.extend(str(row["reason"]) for row in rows if row["reason"] and (not keys or row["essential_key"] in keys))
    return lines


def _store_lines(zip_code: str) -> list[str]:
    """Each line says whether that store has been checked. Without it the model read a
    store list next to one item's stock line and claimed the item was at every store."""
    rows = db.query(
        """
        SELECT s.name, s.address_norm,
               (SELECT COUNT(*) FROM stock_checks c WHERE c.store_id = s.id AND c.status != 'unknown') AS checks
        FROM stores s WHERE s.zip = ? ORDER BY s.chain LIMIT 6
        """,
        (zip_code,),
    )
    lines = []
    for row in rows:
        note = "stock checked, see the item lines" if row["checks"] else "no stock check for this store yet"
        lines.append(f"Store in {zip_code}: {row['name']} — {row['address_norm']} ({note})")
    return lines


def _research_text(payload: object, depth: int = 0) -> str:
    """The Web Search Agent answer, wherever the SDK put it in the payload."""
    if isinstance(payload, str):
        return payload
    if depth > 4:
        return ""
    if isinstance(payload, dict):
        for key in ("content", "answer", "text", "output", "result", "data", "message"):
            if key in payload:
                found = _research_text(payload[key], depth + 1)
                if found:
                    return found
    if isinstance(payload, list):
        for entry in payload[:5]:
            found = _research_text(entry, depth + 1)
            if found:
                return found
    return ""


def _towns_for(zips: list[str]) -> set[str]:
    words = set()
    for zip_code, tokens in _area_tokens().items():
        if zip_code in zips:
            words |= tokens
    words |= {town for town, zip_code in _TOWNS.items() if zip_code in zips}
    return words


def _shelter_lines(zips: list[str]) -> list[str]:
    """The saved shelter research, flattened. This is why /ask can name a shelter offline.
    Still untrusted web text: it goes in the <data> block like everything else."""
    raw = db.get_kv("research:shelters")
    if not raw:
        return []
    try:
        content = _research_text(json.loads(raw))
    except json.JSONDecodeError:
        content = raw
    flat: list[str] = []
    for line in (content or raw).splitlines():
        line = line.strip()
        if not line or set(line) <= set("|-: "):
            continue
        if line.startswith("|"):  # markdown table row -> "Shelter — Town"
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            cells = [cell for cell in cells if cell and not re.fullmatch(r"(\[\d+\][,\s]*)+", cell)]
            if not cells or cells[0].lower() in ("shelter", "name", "location"):
                continue
            line = " — ".join(cells)
        line = re.sub(r"[#*`]+", "", line).strip()
        if len(line) < 8 or line.startswith("Source index"):
            continue
        if line not in flat:
            flat.append(line)
    towns = _towns_for(zips)
    near = [line for line in flat if any(town in line.lower() for town in towns)]
    rest = [line for line in flat if line not in near]
    lines, used = [], 0
    for line in near + rest:
        if used + len(line) > MAX_SHELTER_CHARS:
            break
        lines.append(line)
        used += len(line)
    return [f"Saved shelter research: {line}" for line in lines]


def _origin(question: str, places: list[str], zips: list[str]) -> tuple[str, tuple[float, float]] | None:
    """(label, point) to measure shelter distance from: the town asked about, else a
    ZIP in the question, else the first area. The town's own point, not the watched area's."""
    town, zip_code = shelters.town_zip(question, *places)
    if town and (point := shelters.point_for(town, zip_code)):
        return town.title(), point
    for zip_code in [*_ZIP_IN_TEXT.findall(question), *zips[:1]]:
        if point := shelters.point_for(None, zip_code):
            rows = db.query("SELECT area FROM zips WHERE zip = ?", (zip_code,))
            return (rows[0]["area"] if rows and zip_code not in question else zip_code), point
    return None


def _nearest_shelter_lines(origin: tuple[str, str] | None, sources: list[dict]) -> list[str]:
    """Closest saved shelters, measured by code. Before this the model got a list of
    every shelter that named the town and had to guess which was nearest."""
    if not origin:
        return []
    label, point = origin
    lines = []
    for row in shelters.nearest(*point, limit=NEAREST_SHELTERS):
        # Town centers are rounded, so under a mile reads as "in town".
        distance = "in town" if row["miles"] < 1 else f"about {row['miles']:g} miles away"
        when = f", {row['when'][0].lower()}{row['when'][1:]}" if row["when"] else ""
        rank = "Nearest reported shelter" if not lines else "Next nearest reported shelter"
        lines.append(f"{rank} to {label}: {row['name']} — {row['address']} ({distance}{when}).")
        for source in row["sources"][:1]:
            if all(source["url"] != seen["url"] for seen in sources):
                official = is_official(source["url"])
                sources.append({"title": source["title"], "label": link_label(source["title"], source["url"]),
                                "url": source["url"], "official": official})
    if lines:
        lines.append("Shelter list is from saved news and web research, not confirmed open right now.")
    return lines


def _islands_for(zips: list[str]) -> set[str]:
    return {info["island"] for info in ACTIVE_AREAS.values() if any(z in info["zips"] for z in zips)}


def _official_lines(sources: list[dict], shelters_first: bool, zips: list[str]) -> list[str]:
    """Official (.gov, Red Cross) links first, then news. Links about another island are left out."""
    order = "kind = 'shelter' DESC, kind = 'news' DESC, found_ts DESC" if shelters_first else "found_ts DESC"
    rows = db.query(f"SELECT title, url, kind FROM official_links ORDER BY {order}")
    islands = _islands_for(zips)
    rows = [row for row in rows if island_of(row["title"], row["url"]) in (None, *islands)]
    rows = sorted(rows, key=lambda row: not is_official(row["url"]))[: 8 if shelters_first else 5]
    lines = []
    for row in rows:
        label = link_label(row["title"], row["url"])
        official = is_official(row["url"])
        sources.append({"title": row["title"], "label": label, "url": row["url"], "official": official})
        kind = "Official link" if official else "News report (not official)"
        lines.append(f"{kind} ({row['kind']}): {label} — {row['url']}")
    return lines


def intent_of(question: str) -> str:
    text = question.lower()
    if any(word in text for word in _SHELTER_WORDS):
        return "shelter"
    if any(word in text for word in _RUNNING_OUT_WORDS):
        return "running_out"
    if items_in(text):
        return "item"
    if any(word in text for word in _SAFETY_WORDS):
        return "safety"
    return "supplies"


def read_question(question: str) -> dict:
    """The keyword reading. Always works, even with the model down."""
    return {
        "intent": intent_of(question),
        "zips": zips_for(question),
        "items": items_in(question),
        "places": [],
        "read_by": "keywords",
    }


async def understand(question: str) -> dict:
    """The model reads the question; code checks what it says. The model catches what
    keyword lists miss ("gas for the generator", "can my family still get to Kona"),
    but every town and item it names must map onto ours or it is dropped, and a
    shelter question worded as one always stays a shelter question."""
    words = read_question(question)
    system = QUERY_SYSTEM.format(items=", ".join(_NAMES))
    try:
        parsed = await asyncio.wait_for(
            chat_json(system, f"Question: {question}", AskQuery, example=QUERY_EXAMPLE, max_tokens=80, attempts=2),
            QUERY_TIMEOUT_S,
        )
    except Exception as exc:
        print(f"[ask] model could not read the question ({type(exc).__name__}), using keywords")
        return words

    # A place the question never mentions is the model copying or guessing: drop it.
    asked = shelters.plain(question)
    places = [place.strip() for place in parsed.places
              if place.strip() and not place.startswith("<") and shelters.plain(place.strip()) in asked][:3]
    found = _named_zips(question)
    for place in places:
        found += [zip_code for zip_code in _named_zips(place) if zip_code not in found]
    ranked = _ranked_zips()

    items = list(words["items"])
    for item in parsed.items:
        key = item.strip().lower().replace(" ", "_")
        for match in ([key] if key in _NAMES else items_in(item)):
            # Keep it only if the question has a word for it ("gas" -> gas_can).
            hint = set(re.findall(r"[a-z]{3,}", f"{match.replace('_', ' ')} {_NAMES[match].lower()}")) - _VAGUE
            if match not in items and any(re.search(rf"\b{word}", asked) for word in hint):
                items.append(match)

    # A keyword hit decides; the model only fills in when no keyword matched. In testing
    # it read "Do I still have time to buy supplies?" as shopping, not as a safety question.
    intent = parsed.intent.strip().lower().replace(" ", "_")
    # Nor may it make a question about shelters: "is Costco open" is not one.
    if intent not in INTENTS or intent == "shelter" or words["intent"] != "supplies":
        intent = words["intent"]
    if intent == "item" and not items:
        intent = "supplies"
    return {
        "intent": intent,
        "zips": sorted(found, key=ranked.index) if found else ranked,
        "items": items,
        "places": places,
        "read_by": "model",
    }


def build_facts(question: str, query: dict | None = None) -> dict:
    """The facts the model may use, most relevant first, plus what the page displays.
    Code decides which facts matter; the model only writes the sentence.
    query: what the question is about (understand()); keywords when not given."""
    query = query or read_question(question)
    zips, intent, keys = query["zips"], query["intent"], query["items"]
    blocked = [zip_code for zip_code in zips if not shopping_allowed(zip_code)]
    open_zips = [zip_code for zip_code in zips[:2] if zip_code not in blocked]
    sources: list[dict] = []
    head = _storm_lines(sources) + _phase_lines(zips)

    # In DURING we never list stock or stores for that area, whatever was asked.
    stock: list[str] = []
    for zip_code in open_zips:
        stock += _report_lines(zip_code, keys or None)
        stock += _stock_lines(zip_code, keys or None, low_first=intent == "running_out")
        if intent == "supplies":  # A question about one item never gets the whole store list.
            stock += _store_lines(zip_code)
    risks = _risk_lines(open_zips, keys or None)
    if intent == "running_out":
        for zip_code in open_zips:
            if not _risk_lines([zip_code], keys or None):
                # Without this line the model filled the gap with an in-stock item.
                risks.append(
                    f"Nothing is running out in {zip_code}: no item came back with few or no options "
                    "twice in a row in the last 3 hours."
                )
    shelter_lines: list[str] = []
    if intent == "shelter" or blocked:
        shelter_lines = _nearest_shelter_lines(_origin(question, query["places"], zips), sources)
        shelter_lines = shelter_lines or _shelter_lines(zips)
    official = _official_lines(sources, intent == "shelter" or bool(blocked), zips)

    if shelter_lines:
        body = shelter_lines + official + stock
    elif intent == "running_out":
        body = risks + stock + official
    elif intent == "safety":
        body = risks + stock + official  # The phase line already answers it; links come last.
    elif intent == "item":
        body = stock + risks  # The page lists the links; the model only needs the item facts.
    else:
        body = stock + risks + official
    snapshot = snapshot_iso()
    if intent in ("item", "running_out"):
        # Answer facts right after the safety line, so a shorter retry drops the storm summary, not them.
        storm, phase = head[: len(head) - len(zips)], head[-len(zips):]
        lines = phase + body + storm
    else:
        lines = head + body
    return {
        "lines": lines,
        "zips": zips,
        "intent": intent,
        "items": keys,
        "blocked": blocked,
        "sources": sources[:8],
        "snapshot_iso": snapshot,
        "snapshot_hst": hst(snapshot),
        "snapshot_clock_hst": hst_clock(snapshot),
    }


def _fit(lines: list[str], budget: int) -> list[str]:
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return kept


def _fallback(facts: dict) -> str:
    """Code-built answer when Liquid is down. It must stay useful during an outage."""
    lines = facts["lines"]
    if not lines:
        return "I have no saved data yet. Check the official sources on this page."
    phase = next((line for line in lines if " is in phase " in line), None)
    if facts["blocked"]:
        return f"{phase or SAFETY_MESSAGE} Follow the official shelter links on this page."

    intent = facts["intent"]
    if intent == "shelter":
        nearest = next((line for line in lines if line.startswith("Nearest reported shelter to ")), None)
        if nearest:
            label, _sep, shelter = nearest.removeprefix("Nearest reported shelter to ").partition(": ")
            return (f"The nearest reported shelter to {label} is {shelter} "
                    "It comes from saved news reports, so confirm it with the official source below.")
        shelter = next((line for line in lines if line.startswith("Saved shelter research:") and " — " in line), None)
        if shelter:
            shelter = shelter.removeprefix("Saved shelter research: ").strip()
            shelter = shelter.removeprefix("- ").strip()
            shelter = re.sub(r"\s*\[\d+\](?:\s*\[\d+\])*\s*$", "", shelter)
            return f"The saved shelter list for this area includes {shelter}. Confirm it with the official source below."
    if intent == "running_out":
        risk = next((line for line in lines if "few or none found" in line or "Nothing is running out" in line), None)
        if risk:
            return risk
    if intent == "item":
        # A person in the store beats the website: lead with their reports (seen-on-shelf
        # first, then empty shelves), then the online check.
        reports = [line.removeprefix("Shelf report from people in the store: ")
                   for line in lines if line.startswith("Shelf report")][:2]
        stock = next((line for line in lines if "checked " in line and ": " in line), None)
        if reports or stock:
            said = [f"People in the store report: {' '.join(reports)}"] if reports else []
            online = [f"Online (Walmart website, not the shelf): {stock}"] if stock and reports else ([stock] if stock else [])
            return " ".join(said + online + (["Call before you drive."] if reports else []))
    if intent == "safety" and phase:
        return phase

    stock = [line for line in lines if "checked " in line and ": " in line][:3]
    return " ".join(stock or ([phase] if phase else lines[:2]))


def _same_thing(one: str, two: str) -> bool:
    words_one = set(re.findall(r"[a-z0-9]+", one.lower()))
    words_two = set(re.findall(r"[a-z0-9]+", two.lower()))
    if not words_one or not words_two:
        return False
    overlap = len(words_one & words_two) / min(len(words_one), len(words_two))
    return overlap > 0.6


def tidy(text: str, question: str) -> str:
    """LFM2.5 keeps writing after it has answered: it repeats the question, says the same
    fact twice, then tacks on a contradicting "I cannot determine". Keep the real answer."""
    # The page lists the sources itself, so pasted URLs only clutter the sentence.
    plain = _URL_IN_TEXT.sub("", text.replace("\n", " "))
    sentences = [part.strip() for part in _SENTENCE_END.split(plain) if part.strip()]
    asked = question.strip().lower().rstrip("?")
    kept: list[str] = []
    for raw in sentences:
        sentence = re.sub(r"\s+", " ", raw).strip(" ,;:.") + "."
        if len(sentence) < 14 or sentence.lower().rstrip("?.") == asked or _BARE_LABEL.match(sentence):
            continue
        if kept and (_HEDGE.match(sentence) or any(_same_thing(sentence, earlier) for earlier in kept)):
            continue
        kept.append(sentence)
        if len(kept) == 3:
            break
    return " ".join(kept) or text.strip()


_NUMBER = re.compile(r"\(?\d[\d().\-/ ]*\d|\d")
_ARRIVED = re.compile(r"conditions (have|are|were) (arrived|arriving|occurring|happening)|storm has arrived", re.I)


def grounded(text: str, lines: list[str], blocked: list[str]) -> str:
    """Drop any sentence the saved facts do not back up. In testing the model invented a
    shelter phone number and turned "have NOT arrived" into "have arrived"."""
    fact_numbers = set(re.sub(r"\D", " ", " ".join(lines)).split())
    text = re.sub(r"</?data>|\[/?data\]", " ", text)  # the model sometimes echoes the prompt's tags
    kept = []
    for sentence in _SENTENCE_END.split(text.strip()):
        # Every digit group must appear in the facts ("(808) 961-5555" -> 808, 961, 5555).
        groups = {g for n in _NUMBER.findall(sentence) for g in re.sub(r"\D", " ", n).split()}
        if groups - fact_numbers:
            continue
        if not blocked and _ARRIVED.search(sentence) and " not " not in f" {sentence.lower()} ":
            continue
        kept.append(sentence)
    return " ".join(kept).strip()


async def _model_answer(question: str, lines: list[str]) -> str:
    """One try on the fitted facts, one shorter try if the model goes quiet."""
    for budget in (MODEL_CONTEXT_CHARS, MODEL_CONTEXT_CHARS // 2):
        context = "\n".join(_fit(lines, budget))
        prompt = f"Question: {question}\n\n<data>\n{context}\n</data>"
        text = (await chat(ASK_SYSTEM, prompt, max_tokens=220)).strip()
        if text:
            return tidy(text, question)
        print("[ask] model returned nothing, retrying with fewer facts")
    return ""


async def answer(question: str) -> dict:
    question = " ".join(question.split())[:MAX_QUESTION_CHARS]
    query = await understand(question)
    facts = build_facts(question, query)
    facts["read_by"] = query["read_by"]
    used_model = True
    if facts["intent"] == "item" and any(line.startswith("Shelf report") for line in facts["lines"]):
        # Shelf reports send people to a store; the small model has swapped whose report is
        # whose, so this answer is built in code from the facts, word for word.
        return _response(question, facts, _fallback(facts), "saved facts, word for word (shelf reports)")
    try:
        text = await _model_answer(question, facts["lines"])
    except Exception as exc:
        print(f"[ask] local model unavailable: {type(exc).__name__}")
        text = ""
    text = grounded(text, facts["lines"], facts["blocked"]) if text else ""
    answerable = any(("checked " in line and ": " in line) or "few or none found" in line
                     or line.startswith("Shelf report") for line in facts["lines"])
    if not text or (answerable and (_HEDGE.match(text) or "cannot verify" in text.lower())):
        # The model went quiet or hedged although the saved facts answer it: use the code answer.
        text, used_model = _fallback(facts), False
    return _response(question, facts, text,
                     "Liquid LFM2.5 (local)" if used_model else "saved facts only (model unreachable)")


def _response(question: str, facts: dict, text: str, answered_by: str) -> dict:
    return {
        "question": question,
        "answer": text,
        "snapshot_hst": facts["snapshot_hst"],
        "snapshot_clock_hst": facts["snapshot_clock_hst"],
        "online": is_online(),
        "zips": facts["zips"],
        "intent": facts["intent"],
        "safety": SAFETY_MESSAGE if facts["blocked"] else None,
        "sources": facts["sources"],
        "facts_used": facts["lines"],
        "answered_by": answered_by,
        "question_read_by": facts.get("read_by", "keywords"),
    }
