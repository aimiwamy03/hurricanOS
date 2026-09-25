"""Storm tracking: live sources -> model proposes phases -> code applies them and ranks ZIPs.

Run alone with: python -m agent.storm --once
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from urllib.parse import urlparse

from app import db, llm, nimble_client
from app.config import ACTIVE_AREAS, PHASE_OVERRIDE, STORM_NAME
from app.models import AreaPhase, StormAssessment
from app.prompts import STORM_EXAMPLE, STORM_SYSTEM
from app.tinybird_client import log_event
from agent.util import iso_ago, log, now_iso, tb_ts

_KEYWORDS = ["warning", "watch", "track", "rain", "Hilo", "Kona", "Maui", "landfall", "closest approach"]
_PHASE_WEIGHT = {"BEFORE": 50, "DURING": 10, "AFTER": 35}
DURING_VOTES = 3  # consecutive DURING proposals before locking
FAST_TRACK_HOURS = 3


def _is_gov(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith(".gov") or host.endswith(".mil")


_WATCH_HEAD = re.compile(r"A (Hurricane|Tropical Storm) (Warning|Watch) is in effect for", re.I)
_WATCH_END = re.compile(r"A (Hurricane|Tropical Storm) (Warning|Watch) means|Interests elsewhere|DISCUSSION AND OUTLOOK", re.I)


def watches_and_warnings(text: str) -> list[str]:
    """The NHC "SUMMARY OF WATCHES AND WARNINGS" block, word for word, as one line per product:
    "A Tropical Storm Warning is in effect for Hawaii County". The model is never asked for
    these: in testing it swapped which county had the warning."""
    text = re.sub(r"[*_`#\\]+", " ", text)
    end = _WATCH_END.search(text)
    heads = list(_WATCH_HEAD.finditer(text[: end.start()] if end else text))
    lines = []
    for index, head in enumerate(heads):
        stop = heads[index + 1].start() if index + 1 < len(heads) else (end.start() if end else head.end() + 300)
        body = text[head.end() : stop]
        places = [p.strip(" .,;:-") for p in re.split(r"\n|\.\.\.|;", body)]
        places = [p for p in places if p and len(p) < 80 and not re.search(r"\b(means|within|hours)\b", p, re.I)]
        if places:
            line = f"A {head.group(1).title()} {head.group(2).title()} is in effect for {', '.join(dict.fromkeys(places))}"
            if line not in lines:
                lines.append(line)
    return lines


async def fetch_storm_sources() -> list[dict]:
    news = await nimble_client.search(
        f"Hurricane {STORM_NAME} Hawaii", focus="news", depth="lite", time_range="day", max_results=8
    )
    official = await nimble_client.search(
        f"Central Pacific Hurricane Center {STORM_NAME} advisory", depth="lite", max_results=5
    )
    seen: set[str] = set()
    candidates: list[dict] = []
    for item in official + news:
        url = item.get("url") or ""
        if url and url not in seen:
            seen.add(url)
            candidates.append(item)
    candidates.sort(key=lambda item: not _is_gov(item["url"]))  # .gov first, order kept otherwise

    sources = []
    for item in candidates[:2]:
        try:
            md = await nimble_client.extract(item["url"])
        except Exception as exc:
            log("storm", f"extract failed {item['url']}: {type(exc).__name__}")
            md = ""
        text = nimble_client.trim_markdown(md, _KEYWORDS, 2000) if md else ""
        # Parsed from the full page, before trimming, and only from .gov pages.
        warnings = watches_and_warnings(md) if md and _is_gov(item["url"]) else []
        sources.append(
            {"url": item["url"], "title": item.get("title") or "", "text": text or item.get("description") or "", "warnings": warnings}
        )
    # Search snippets for the rest: free context, no extra calls.
    for item in candidates[2:6]:
        sources.append({"url": item["url"], "title": item.get("title") or "", "text": item.get("description") or ""})
    return sources


async def assess_storm(sources: list[dict]) -> StormAssessment:
    areas = "\n".join(f"- {name} ({area['island']}, {area['side']} side)" for name, area in ACTIVE_AREAS.items())
    blocks = []
    used = 0
    for source in sources:
        block = f'<source url="{source["url"]}">\n{source["title"]}\n{source["text"]}\n</source>'
        if used + len(block) > 6000:
            block = block[: max(0, 6000 - used)]
        if not block:
            break
        blocks.append(block)
        used += len(block)
    user = f"Areas:\n{areas}\n\nSources:\n" + "\n".join(blocks)
    return await llm.chat_json(STORM_SYSTEM, user, StormAssessment, example=STORM_EXAMPLE)


def _override() -> dict[str, str]:
    phases = {}
    for part in PHASE_OVERRIDE.split(","):
        if ":" in part:
            zip_code, phase = part.split(":", 1)
            if phase.strip().upper() in _PHASE_WEIGHT:
                phases[zip_code.strip()] = phase.strip().upper()
    return phases


def ensure_zips() -> None:
    """Every active ZIP gets a row, starting in BEFORE."""
    for area, info in ACTIVE_AREAS.items():
        for zip_code in info["zips"]:
            db.execute(
                "INSERT OR IGNORE INTO zips (zip, area, priority, reason, phase, phase_locked, updated_ts) "
                "VALUES (?, ?, 0, 'not ranked yet', 'BEFORE', 0, ?)",
                (zip_code, area, now_iso()),
            )


def _proposed_for(area: str, assessment: StormAssessment | None) -> AreaPhase | None:
    if assessment is None:
        return None
    first_word = area.split()[0].lower()
    for item in assessment.areas:
        name = item.area.lower()
        if name == area.lower() or first_word in name:
            return item
    return None


def decide_phase(
    current: str, locked: bool, votes: int, proposal: AreaPhase | None, source_urls: set[str]
) -> tuple[str, bool, int, str]:
    """Code decides, not the model. Returns (phase, locked, during_votes, why).

    - Locked DURING stays DURING until AFTER is proposed and a .gov source was read.
    - DURING needs DURING_VOTES proposals in a row, except the fast track: a .gov
      source read this cycle puts onset within FAST_TRACK_HOURS -> lock now.
    - A DURING proposal with onset more than FAST_TRACK_HOURS away never counts.
    - Any other proposal resets the count. No assessment (model down) changes nothing.
    """
    has_gov = any(_is_gov(url) for url in source_urls)
    if proposal is None:
        return current, locked, votes, "no assessment"
    phase = proposal.proposed_phase
    if locked and current == "DURING":
        if phase == "AFTER" and has_gov:
            return "AFTER", False, 0, "official all-clear"
        return current, locked, votes, "locked until official all-clear"
    if phase != "DURING":
        return phase, False, 0, f"proposed {phase}"
    onset = proposal.onset_hours
    if onset is not None and onset > FAST_TRACK_HOURS:
        return current, locked, 0, f"DURING proposed but onset {onset:g} h away; not counted"
    official = bool(proposal.onset_source) and proposal.onset_source in source_urls and _is_gov(proposal.onset_source)
    if onset is not None and official:
        return "DURING", True, DURING_VOTES, f"official source puts onset within {onset:g} h"
    votes += 1
    if votes >= DURING_VOTES:
        return "DURING", True, votes, f"DURING proposed {votes} cycles in a row"
    return current, locked, votes, f"DURING proposed ({votes} of {DURING_VOTES}); caution shown"


def apply_phases(assessment: StormAssessment | None, sources: list[dict]) -> list[str]:
    """Returns change lines like '96720 BEFORE → DURING'."""
    ensure_zips()
    source_urls = {source["url"] for source in sources}
    override = _override()
    changes = []
    for row in db.query("SELECT zip, area, phase, phase_locked, during_votes FROM zips"):
        zip_code, current, locked = row["zip"], row["phase"], bool(row["phase_locked"])
        votes = row["during_votes"] or 0
        if zip_code in override:
            new = override[zip_code]
            new_locked, new_votes, why = new == "DURING", 0, "PHASE_OVERRIDE"
        else:
            proposal = _proposed_for(row["area"], assessment)
            new, new_locked, new_votes, why = decide_phase(current, locked, votes, proposal, source_urls)
        if (new, new_locked, new_votes) != (current, locked, votes):
            db.execute(
                "UPDATE zips SET phase = ?, phase_locked = ?, during_votes = ?, updated_ts = ? WHERE zip = ?",
                (new, int(new_locked), new_votes, now_iso(), zip_code),
            )
        if new != current:
            changes.append(f"{zip_code} {current} → {new} ({why})")
            log("storm", f"{zip_code} {current} → {new}: {why}")
        elif new_votes != votes:
            log("storm", f"{zip_code} stays {current}: {why}")

    if assessment is not None:
        top_url = sources[0]["url"] if sources else ""
        proposed_all = ",".join(f"{a.area}:{a.proposed_phase}" for a in assessment.areas)
        advisory = assessment.model_dump_json()
        official = next((s for s in sources if s.get("warnings")), None)
        db.insert(
            "storm_updates",
            {
                "ts_utc": now_iso(),
                "source_url": top_url,
                "summary": assessment.summary,
                "phase_proposed": proposed_all,
                "advisory_json": advisory,
                "official_text": ". ".join(official["warnings"]) + "." if official else None,
                "official_url": official["url"] if official else None,
            },
        )
        log_event(
            "storm_updates",
            {
                "ts_utc": tb_ts(),
                "source_url": top_url,
                "summary": assessment.summary,
                "phase_proposed": proposed_all,
                "advisory_json": advisory,
            },
        )
    return changes


def rank_zips(assessment: StormAssessment | None, sources: list[dict]) -> None:
    newest = (sources[0]["title"] + " " + sources[0]["text"]).lower() if sources else ""
    for row in db.query("SELECT zip, area, phase FROM zips"):
        area = ACTIVE_AREAS.get(row["area"]) or {}
        score = _PHASE_WEIGHT.get(row["phase"], 50)
        reasons = [f"{row['phase']} phase"]
        area_word = row["area"].split()[0].lower()
        named_in_warning = any(
            area_word in (s["text"] + s["title"]).lower() and "warning" in (s["text"] + s["title"]).lower()
            for s in sources
        )
        if area.get("side") == "windward" or named_in_warning:
            score += 20
            reasons.append("windward side" if area.get("side") == "windward" else "named in a warning")
        pressure = db.query(
            "SELECT COUNT(*) AS n FROM stock_checks WHERE zip = ? AND status IN ('low', 'out') "
            "AND ts_utc >= ?",
            (row["zip"], iso_ago(2)),
        )[0]["n"]
        if pressure:
            score += 15
            reasons.append(f"{pressure} recent low/out checks")
        if assessment is not None and assessment.track_shift and area_word in newest:
            score += 10
            reasons.append("track shift toward this area")
        db.execute(
            "UPDATE zips SET priority = ?, reason = ?, updated_ts = ? WHERE zip = ?",
            (min(score, 100), ", ".join(reasons), now_iso(), row["zip"]),
        )


def _link_kind(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if "weather.gov" in host or "noaa.gov" in host:
        return "cphc" if "nhc" in host or "cphc" in url.lower() else "nws"
    if "hawaiicounty.gov" in host or "mauicounty.gov" in host or "hawaii.gov" in host:
        return "county"
    if "redcross.org" in host:
        return "shelter"
    return None


def save_official_links(assessment: StormAssessment | None, sources: list[dict]) -> int:
    found = [(s["title"], s["url"]) for s in sources]
    if assessment is not None:
        # Only links that appear in a source. The model has been seen inventing URLs.
        source_text = " ".join(s["url"] + " " + s["text"] for s in sources)
        for link in assessment.official_links:
            url = str(link.get("url") or "")
            if url and url in source_text:
                found.append((str(link.get("title") or ""), url))
    added = 0
    for title, url in found:
        kind = _link_kind(url) if url else None
        if not kind:
            continue
        if db.query("SELECT 1 FROM official_links WHERE url = ?", (url,)):
            continue
        db.insert("official_links", {"title": title or urlparse(url).netloc, "url": url, "kind": kind, "found_ts": now_iso()})
        added += 1
    return added


async def run_once() -> tuple[StormAssessment | None, list[str]]:
    sources = await fetch_storm_sources()
    log("storm", f"{len(sources)} sources, {sum(_is_gov(s['url']) for s in sources)} official")
    try:
        assessment = await assess_storm(sources)
        log("storm", assessment.summary)
    except Exception as exc:
        # Model down or bad JSON: keep the current phases, still rank.
        log("llm", f"storm assessment failed: {type(exc).__name__}: {exc}")
        assessment = None
    changes = apply_phases(assessment, sources)
    rank_zips(assessment, sources)
    save_official_links(assessment, sources)
    return assessment, changes


async def _main() -> None:
    assessment, _changes = await run_once()
    if assessment is not None:
        print(json.dumps(assessment.model_dump(), indent=2))
    for row in db.query("SELECT zip, area, phase, phase_locked, priority, reason FROM zips ORDER BY priority DESC"):
        print(f"{row['zip']} {row['phase']:<6} locked={row['phase_locked']} priority={row['priority']}  {row['reason']}")


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print("usage: python -m agent.storm --once")
    else:
        asyncio.run(_main())
