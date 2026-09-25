"""News search for Hurricane Nolo, plus retailer and maps templates.

The installed SDK's templates.list() has no search argument, so this
script pages through the catalog and filters names locally.
"""

from __future__ import annotations

from nimble_python import Nimble

from common import clip, env, mock_on

NEEDLES = ("maps", "walmart", "target", "home depot", "home_depot", "homedepot")


def template_names(client: Nimble) -> list[str]:
    names: list[str] = []
    offset = 0
    while True:
        page = client.extract.templates.list(limit=100, offset=offset)
        names.extend(item.name for item in page.items)
        offset += len(page.items)
        if offset >= page.total or not page.items:
            break
    return names


def matching(names: list[str]) -> dict[str, list[str]]:
    grouped = {needle: [] for needle in ("maps", "walmart", "target", "home depot")}
    for name in names:
        lowered = name.lower().replace("_", " ")
        if "map" in lowered:
            grouped["maps"].append(name)
        if "walmart" in lowered:
            grouped["walmart"].append(name)
        if "target" in lowered:
            grouped["target"].append(name)
        if "home depot" in lowered or "homedepot" in lowered:
            grouped["home depot"].append(name)
    return grouped


def run_mock(reason: str) -> None:
    print(f"MOCK NIMBLE — {reason}")
    print("news search (fake, not live):")
    print("- Hurricane Nolo nears Hawaii | https://example.com/nolo")
    print("  Official watches and warnings are the source of truth.")
    print("templates documented in the public gallery (not your account):")
    print("- maps: google_maps_search")
    print("- walmart: walmart_pdp, walmart_search")
    print("- target: target_pdp")
    print("- home depot: not listed in the gallery table we read")
    print("Put NIMBLE_API_KEY in .env and re-run to see the live catalog.")


def main() -> None:
    use_mock, reason = mock_on("MOCK_NIMBLE", "NIMBLE_API_KEY")
    if use_mock:
        run_mock(reason)
        return

    client = Nimble(api_key=env("NIMBLE_API_KEY"), timeout=60.0)
    try:
        result = client.search(
            query="Hurricane Nolo Hawaii",
            focus="news",
            search_depth="lite",
            max_results=8,
            time_range="week",
        )
        print(f"news search: {len(result.results)} results")
        for item in result.results:
            print(f"- {item.title}")
            print(f"  {item.url}")
            if item.description:
                print(f"  {clip(item.description, 220)}")
        names = template_names(client)
        print(f"templates listed: {len(names)}")
        grouped = matching(names)
        for needle, hits in grouped.items():
            shown = ", ".join(hits) if hits else "(none)"
            print(f"- {needle}: {shown}")
    except Exception as exc:
        run_mock(f"Nimble call failed: {exc}")


if __name__ == "__main__":
    main()
