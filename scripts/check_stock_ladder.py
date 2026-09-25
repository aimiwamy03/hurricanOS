"""Try each stock-ladder rung for a flashlight in Hilo 96720.

Rungs:
  1. Retailer extract template, with ZIP if the template schema allows it.
  2. Extract a store page with JavaScript rendering, markdown output.
  3. ZIP-level shopping search (snippets only).
  4. Web Search Agent, started and checked once. This script does not wait
     for the agent to finish.
"""

from __future__ import annotations

import json

from nimble_python import Nimble

from common import clip, env, mock_on

QUERY_KEYS = ("keyword", "query", "search_query", "q", "search")
ZIP_KEYS = ("zip_code", "zip", "postal_code")
RETAILER_BITS = ("walmart", "target", "home depot", "homedepot", "home_depot")


def dump(value, limit: int = 900) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    try:
        text = json.dumps(value, default=str)
    except TypeError:
        text = str(value)
    return clip(text, limit)


def choose_template(names: list[str]) -> str | None:
    def rank(name: str) -> tuple[int, str]:
        lowered = name.lower()
        retailer = any(bit in lowered.replace("_", " ") or bit in lowered for bit in RETAILER_BITS)
        search = "search" in lowered or "serp" in lowered
        if retailer and search:
            return (0, name)
        if retailer:
            return (1, name)
        return (2, name)

    candidates = [name for name in names if rank(name)[0] < 2]
    if not candidates:
        return None
    return sorted(candidates, key=rank)[0]


def list_names(client: Nimble) -> list[str]:
    names: list[str] = []
    offset = 0
    while True:
        page = client.extract.templates.list(limit=100, offset=offset)
        names.extend(item.name for item in page.items)
        offset += len(page.items)
        if offset >= page.total or not page.items:
            break
    return names


def params_for(schema: dict) -> tuple[dict, list[str], bool]:
    properties = schema.get("properties") or {}
    params: dict = {}
    for key in properties:
        lowered = key.lower()
        if lowered in QUERY_KEYS:
            params[key] = "flashlight"
        elif lowered in ZIP_KEYS:
            params[key] = "96720"
    required = [key for key in (schema.get("required") or []) if isinstance(key, str)]
    missing = [key for key in required if key not in params]
    return params, missing, any(key.lower() in ZIP_KEYS for key in params)


def first_url(value) -> str | None:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, str) and value.startswith("http"):
        return value
    if isinstance(value, dict):
        for key in ("url", "link", "product_url"):
            found = value.get(key)
            if isinstance(found, str) and found.startswith("http"):
                return found
        for child in value.values():
            found = first_url(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = first_url(child)
            if found:
                return found
    return None


def rung_template(client: Nimble) -> str | None:
    print("\n== rung 1: retailer template ==")
    names = list_names(client)
    template = choose_template(names)
    if not template:
        print("No Walmart, Target, or Home Depot template in this account.")
        return None
    detail = client.extract.templates.get(template)
    schema = {}
    if detail.published_version:
        schema = detail.published_version.input_schema or {}
    print(f"template: {template}")
    print(f"input fields: {', '.join((schema.get('properties') or {}).keys()) or '(none published)'}")
    params, missing, has_zip = params_for(schema)
    if missing:
        print(f"skipped run: required fields we will not invent: {', '.join(missing)}")
        return None
    if not params:
        print("skipped run: schema has no query or ZIP field we recognize")
        return None
    result = client.extract.templates.run(
        template=template,
        params=params,
        localization=True if has_zip else False,
    )
    print(f"params: {params} localization={has_zip}")
    print(dump(result))
    return first_url(result)


def rung_extract(client: Nimble, url: str | None) -> None:
    print("\n== rung 2: extract store page (JS render, markdown) ==")
    if not url:
        search = client.search(
            query="flashlight Home Depot Hilo Hawaii",
            focus="shopping",
            search_depth="lite",
            max_results=5,
        )
        for item in search.results:
            if item.url:
                url = item.url
                break
    if not url:
        print("no store URL to extract")
        return
    print(f"url: {url}")
    result = client.extract.run(url=url, render=True, formats=["markdown"])
    markdown = ""
    data = getattr(result, "data", None)
    if data is not None:
        markdown = getattr(data, "markdown", None) or ""
    print(clip(markdown or dump(result), 800))


def rung_zip(client: Nimble) -> None:
    print("\n== rung 3: ZIP-level shopping search ==")
    result = client.search(
        query="flashlight in stock 96720 Hilo Hawaii",
        focus="shopping",
        search_depth="lite",
        max_results=5,
    )
    print(f"{len(result.results)} results")
    for item in result.results:
        print(f"- {item.title} | {item.url}")
        if item.description:
            print(f"  {clip(item.description, 180)}")


def rung_agent(client: Nimble) -> None:
    print("\n== rung 4: web search agent (background, one status check) ==")
    run = client.agents.run(
        input=(
            "Is a flashlight in stock today at a hardware or big-box store "
            "in Hilo, Hawaii ZIP 96720? Cite the store page. Do not guess."
        ),
        effort="low",
        agent_name="shelfwatch-flashlight-96720",
    )
    print(f"started run {run.id} status={run.status} agent={run.web_search_agent_id}")
    polled = client.agents.runs.get(run.id, agent_id=run.web_search_agent_id)
    print(f"after one check: status={polled.status} is_active={polled.is_active}")
    if not polled.is_active and polled.status == "completed":
        result = client.agents.runs.result(run.id, agent_id=run.web_search_agent_id)
        print(dump(result))
    else:
        print("still running — the real agent will pick this up on a later cycle")


def run_mock(reason: str) -> None:
    print(f"MOCK STOCK LADDER — {reason}")
    print("\n== rung 1: retailer template ==")
    print("template: (unknown until NIMBLE_API_KEY is set)")
    print("fake parsing: flashlight, Hilo Home Depot, status unknown, level=store")
    print("\n== rung 2: extract store page ==")
    print("fake markdown: product page mentioned a flashlight; stock widget did not load")
    print("\n== rung 3: ZIP-level shopping search ==")
    print("- Flashlights in Hilo | https://example.com/hilo-flashlight")
    print("\n== rung 4: web search agent ==")
    print("fake run id mock-run-96720 status=running (not sent)")
    print("\nThis does not tell us which rung to build on. Add NIMBLE_API_KEY and re-run.")


def main() -> None:
    use_mock, reason = mock_on("MOCK_NIMBLE", "NIMBLE_API_KEY")
    if use_mock:
        run_mock(reason)
        return
    client = Nimble(api_key=env("NIMBLE_API_KEY"), timeout=90.0)
    try:
        url = rung_template(client)
    except Exception as exc:
        print(f"rung 1 failed: {exc}")
        url = None
    try:
        rung_extract(client, url)
    except Exception as exc:
        print(f"rung 2 failed: {exc}")
    try:
        rung_zip(client)
    except Exception as exc:
        print(f"rung 3 failed: {exc}")
    try:
        rung_agent(client)
    except Exception as exc:
        print(f"rung 4 failed: {exc}")


if __name__ == "__main__":
    main()
