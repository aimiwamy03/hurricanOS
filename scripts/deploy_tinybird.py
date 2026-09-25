"""Deploy tinybird/resources.py to Tinybird Cloud with the Python SDK."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from tinybird_sdk.api.build import BuildConfig
from tinybird_sdk.api.deploy import deploy_to_main
from tinybird_sdk.generator.index import build_from_include

from scripts.common import env


def main() -> None:
    token = env("TINYBIRD_TOKEN")
    host = (env("TINYBIRD_HOST") or env("TINYBIRD_API_URL") or env("TINYBIRD_URL")).rstrip("/")
    if not token or not host:
        print("Set TINYBIRD_TOKEN and TINYBIRD_HOST in .env, then re-run.")
        sys.exit(1)

    built = build_from_include({"include_paths": ["tinybird/resources.py"], "cwd": str(ROOT)})
    print(
        f"deploying {built.stats['datasource_count']} datasources "
        f"and {built.stats['pipe_count']} endpoints to {host}"
    )
    result = deploy_to_main(BuildConfig(base_url=host, token=token), built.resources)
    print(f"result: {result.get('result')} success={result.get('success')}")
    if result.get("error"):
        print(result["error"])
        sys.exit(1)
    created = (result.get("datasources") or {}).get("created") or []
    if created:
        print("datasources created: " + ", ".join(created))
    created_pipes = (result.get("pipes") or {}).get("created") or []
    if created_pipes:
        print("endpoints created: " + ", ".join(created_pipes))


if __name__ == "__main__":
    main()
