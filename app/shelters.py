"""Shelters from the agent's saved shelter research, as rows the UI can sort by distance.

The research run (agent/agent.py, SHELTER_TASK) comes back as markdown: island headings,
"Opened / Opening ..." lines, then "- **Name** — address, Town [1] [4]" bullets and a
source index. Parsing it here means every shelter shows with its own sources, offline.
It is still web text: the UI says "reported", never "open now".
"""

from __future__ import annotations

import json
import re
import unicodedata

from app import db
from app.zips import _ZIPS, island_at, miles

# Real ZIP for each town a shelter list names (not the nearest watched ZIP).
TOWN_ZIPS = {
    "hilo": "96720", "keaau": "96749", "pahoa": "96778", "volcano": "96785", "mountain view": "96771",
    "kurtistown": "96760", "papaaloa": "96780", "laupahoehoe": "96764", "honokaa": "96727",
    "waimea": "96743", "kamuela": "96743", "kailua-kona": "96740", "kailua kona": "96740", "kona": "96740",
    "holualoa": "96725", "kealakekua": "96750", "captain cook": "96704", "ocean view": "96737",
    "naalehu": "96772", "pahala": "96777", "hawi": "96719", "kapaau": "96755", "waikoloa": "96738",
    "kahului": "96732", "wailuku": "96793", "kihei": "96753", "lahaina": "96761", "pukalani": "96768",
    "makawao": "96768", "paia": "96779", "haiku": "96708", "hana": "96713", "kula": "96790",
    "kaunakakai": "96748", "lanai city": "96763", "lanai": "96763",
}

# Town centers, rounded. Big rural ZIPs put their middle miles from the town: 96704's
# middle is 30 miles south of Captain Cook, which made Waimea look nearer to Kona than
# Yano Hall. Towns not listed fall back to their ZIP middle.
TOWN_POINTS = {
    "hilo": (19.707, -155.089), "keaau": (19.623, -155.039), "pahoa": (19.497, -154.950),
    "volcano": (19.430, -155.234), "papaaloa": (19.986, -155.220), "honokaa": (20.079, -155.466),
    "waimea": (20.023, -155.670), "kamuela": (20.023, -155.670), "kailua-kona": (19.640, -155.997),
    "kailua kona": (19.640, -155.997), "kona": (19.640, -155.997), "captain cook": (19.497, -155.922),
    "ocean view": (19.100, -155.765), "naalehu": (19.065, -155.585), "pahala": (19.203, -155.480),
    "kahului": (20.889, -156.473), "wailuku": (20.891, -156.505), "kihei": (20.764, -156.445),
    "lahaina": (20.878, -156.683), "pukalani": (20.837, -156.337), "hana": (20.758, -155.988),
    "kaunakakai": (21.091, -157.023),
}

COUNTY_CONTACTS = {
    # Checked on hawaiicounty.gov and mauicounty.gov, Sept 25, 2026.
    "Hawaiʻi Island": {"name": "Hawaiʻi County Civil Defense", "phone": "808-935-0031",
                       "url": "https://www.hawaiicounty.gov/departments/civil-defense"},
    "Maui": {"name": "Maui Emergency Management Agency", "phone": "808-270-7285",
             "url": "https://www.mauicounty.gov/2921/Emergency-Management-Agency"},
    "Molokaʻi": {"name": "Maui Emergency Management Agency", "phone": "808-270-7285",
                 "url": "https://www.mauicounty.gov/2921/Emergency-Management-Agency"},
    "Lānaʻi": {"name": "Maui Emergency Management Agency", "phone": "808-270-7285",
               "url": "https://www.mauicounty.gov/2921/Emergency-Management-Agency"},
}

_BULLET = re.compile(r"^\s*[-*]\s+\*\*(?P<name>[^*]+)\*\*(?P<extra>[^—–-]*)[—–-]\s*(?P<rest>.+)$")
_REFS = re.compile(r"\[(\d+)\]")
_SOURCE = re.compile(r"^\s*\[(\d+)\]\s*(?P<title>.*?)\s+[—–-]\s+(?P<url>https?://\S+)")
_WHEN = re.compile(r"(Opened|Opening|Open)\b[^*\n]*", re.I)


def plain(text: str) -> str:
    """'Nāʻālehu' -> 'naalehu', so town names match however they were spelled."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[ʻ‘'`’]", "", text).lower().strip()


def point_for(town: str | None, zip_code: str | None) -> tuple[float, float] | None:
    if town in TOWN_POINTS:
        return TOWN_POINTS[town]
    point = _ZIPS.get(zip_code) if zip_code else None
    return (point[0], point[1]) if point else None


def town_zip(*texts: str) -> tuple[str | None, str | None]:
    """First known town mentioned, longest names first ('lanai city' before 'lanai')."""
    for text in texts:
        text = plain(text)
        for town in sorted(TOWN_ZIPS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(town)}\b", text):
                return town, TOWN_ZIPS[town]
    return None, None


def _research_markdown() -> str:
    raw = db.get_kv("research:shelters")
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    output = data.get("output") if isinstance(data, dict) else None
    if isinstance(output, dict) and isinstance(output.get("content"), str):
        return output["content"]
    return raw


def parse(markdown: str) -> list[dict]:
    sources = {}
    for line in markdown.splitlines():
        if match := _SOURCE.match(line):
            sources[match.group(1)] = {"title": match.group("title").strip(), "url": match.group("url").rstrip(".,;)")}

    shelters, when = [], None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            found = _WHEN.search(stripped)
            when = found.group(0).strip(" *") if found else None
            continue
        if stripped.startswith("**") and _WHEN.search(stripped):
            when = _WHEN.search(stripped).group(0).strip(" *")
            continue
        match = _BULLET.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        rest = match.group("rest")
        refs = _REFS.findall(rest)
        address = _REFS.sub("", rest).strip(" ,.")
        town, zip_code = town_zip(address.split(",")[-1], address, name, match.group("extra"))
        point = point_for(town, zip_code)
        shelters.append({
            "name": name,
            "address": address,
            "zip": zip_code,
            "lat": point[0] if point else None,
            "lng": point[1] if point else None,
            "island": island_at(*point) if point else None,
            "when": when,
            "sources": [sources[ref] for ref in dict.fromkeys(refs) if ref in sources],
        })
    return shelters


def all_shelters() -> list[dict]:
    return parse(_research_markdown())


def nearest(lat: float, lng: float, limit: int = 3) -> list[dict]:
    """Closest shelters on the same island. Distance is from ZIP middles, so "about"."""
    island = island_at(lat, lng)
    rows = []
    for shelter in all_shelters():
        if shelter["lat"] is None or (island and shelter["island"] != island):
            continue
        rows.append({**shelter, "miles": round(miles((lat, lng), (shelter["lat"], shelter["lng"])), 1)})
    rows.sort(key=lambda row: row["miles"])
    return rows[:limit]
