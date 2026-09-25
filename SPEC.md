You are helping me build a hackathon project **solo, today**. Hard submission deadline: **4:30 PM Pacific**. I'm a vibe-coder, not a senior engineer: keep code simple, explain what you did in plain English, and never leave the project broken.

First, save this entire message to `SPEC.md` in the repo root so you can re-read it later. Then do **Phase 0 only** (section 10) and stop.

## 1. What we're building: Shelfwatch

**Hurricane Nolo** is approaching Hawaii's Big Island right now (hurricane watch + tropical storm warning; Maui County under a tropical storm watch; Saturday is the main storm day; power and communications outages are possible). Our hack window is the last preparation day in Hawaii (Hawaii = Pacific minus 3 hours).

Shelfwatch is an **autonomous agent** that, on its own and all day:

1. Tracks the storm (official advisories + news); notices track shifts and warning changes.
2. Ranks affected ZIP codes (e.g. Hilo 96720, Kailua-Kona 96740, plus Maui) and re-ranks when the forecast moves.
3. Finds stores in those ZIPs (hardware, big-box, grocery, pharmacy).
4. Checks stock for ~10 essentials per store, repeatedly, recording every check.
5. Forecasts which items/stores run out next from the history of its own checks.
6. Publishes a live "where to get it" view per ZIP; every entry shows "checked N min ago", confidence, and data level (store / ZIP).
7. Switches **phases** on its own: BEFORE (where to get supplies) → DURING (no store trips; shelters, closures, official instructions) → AFTER (recovery supplies, reopened stores).
8. Keeps working **offline**: the brain is a local Liquid AI model; when internet dies, it answers from the last synced snapshot.

**Judging criteria:** Autonomy (acts on the web with real-time data, no manual intervention), Idea (real-world value), Technical implementation, Tool use (≥3 sponsor tools), 3-minute demo.

**Sponsor tools:**

- **Nimble** (core) — all live web data.
- **Liquid AI** (core) — the agent's brain, local and offline-capable. **No OpenAI anywhere.**
- **Tinybird** (core) — time-series memory: every check and storm update; SQL endpoints compute depletion rates for the forecast.
- **BFL FLUX** (add-on, cut first if behind) — language-free pictograms + shareable status-card backgrounds.



## 2. Tech stack


| Concern                       | Choice                                                                                                                                                                                                                                                           |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language                      | Python 3.11+                                                                                                                                                                                                                                                     |
| Server                        | FastAPI + uvicorn (JSON API + static HTML)                                                                                                                                                                                                                       |
| Local LLM                     | Liquid **LFM2.5** served locally by an **OpenAI-compatible server** (`mlx_lm.server` on Apple Silicon, or llama.cpp `llama-server` with a GGUF). Our code uses the `openai` Python *client library* pointed at `LOCAL_LLM_BASE_URL` — the model is Liquid, local |
| Web data                      | Nimble Python SDK `nimble_python`                                                                                                                                                                                                                                |
| Analytics memory              | Tinybird **Cloud, Forward** (CLI: `tb`), Events API for ingestion, published SQL endpoints for queries                                                                                                                                                           |
| Local store                   | SQLite (stdlib, WAL) — **source of truth**, works offline                                                                                                                                                                                                        |
| Images                        | BFL FLUX REST API via `httpx`; text overlays with Pillow                                                                                                                                                                                                         |
| Validation / retries / config | `pydantic`, `tenacity` (3 tries, backoff), `python-dotenv`                                                                                                                                                                                                       |
| Frontend                      | Plain HTML + vanilla JS, no build step. Leaflet **served from the repo** (`web/vendor/`), not a CDN. Offline view = list + chat                                                                                                                                  |


`requirements.txt`: `fastapi uvicorn[standard] httpx openai pydantic python-dotenv tenacity nimble_python pillow`

## 3. Golden rule for third-party APIs

You do **not** reliably know current Nimble, Tinybird, BFL or Liquid details. **Never guess endpoints, methods or parameters.**

1. Read the official docs first (below), or ask me to paste the page.
2. Wrap every service behind the fixed interfaces in section 6.
3. Build a **mock mode** first (`MOCK_NIMBLE`, `MOCK_TINYBIRD`, `MOCK_FLUX`, `MOCK_LLM`) with realistic fake data so work never blocks.
4. If an SDK is sync-only, call it with `asyncio.to_thread`.

**Docs**

- **Nimble** — full index, read first: [https://docs.nimbleway.com/llms.txt](https://docs.nimbleway.com/llms.txt) (Search, Web Search Agents, Extract, Extract Templates + Gallery, Python SDK, Rate Limits). Confirmed facts:
  - `search` supports `search_depth` (`lite`/`standard`), `max_results`, `time_range` or `start_date`/`end_date`, `include_domains`/`exclude_domains`, and `focus` (`news`, `social`, `location`, `shopping`…). **Focus modes only work with** `search_depth="lite"`**.**
  - `nimble.extract.run(url=..., formats=["markdown"])`, plus async and batch variants; JS rendering available.
  - Web Search Agents: `nimble.agents.run(input="...")`, asynchronous, returns a cited answer with a trust report. Poll until complete.
  - Extract Templates exist for Google Maps search and major retailers (Walmart, Target, Home Depot, Best Buy, Amazon); several accept a ZIP code. Inspect with `nimble.extract.templates.list(search=...)` / `.get(name)` before use.
  - Nimble MCP for Cursor — create `.cursor/mcp.json` (gitignored):
    ```json
    { "mcpServers": { "nimble-mcp-server": {
        "url": "https://mcp.nimbleway.com/mcp",
        "headers": { "Authorization": "Bearer NIMBLE_API_KEY" } } } }
    ```
- **Tinybird** — [https://www.tinybird.co/docs/forward](https://www.tinybird.co/docs/forward) . Use **Forward** (the Classic migration period has ended). **Do not use Tinybird Local** (needs 8 GB+ of Docker memory and competes with the local model). Ingest with the Events API (NDJSON, bearer token, region host); query via published endpoints. Verify exact syntax in the docs.
- **BFL** — [https://docs.bfl.ai](https://docs.bfl.ai) . Confirm the current image endpoint, request fields and polling flow. Download results immediately (signed URLs expire).
- **Liquid AI** — Liquid docs and the `LiquidAI` Hugging Face org. Confirm exact model repo names and server flags.



## 4. Repo structure

```
shelfwatch/
├── .env.example   .gitignore (.env, data/, .cursor/mcp.json)   requirements.txt   SPEC.md   README.md
├── app/
│   ├── main.py           # FastAPI routes + static pages
│   ├── config.py         # env vars, typed; STORM_NAME, REGION_ZIPS, ESSENTIALS are config
│   ├── db.py             # SQLite schema + helpers (parameterized SQL only)
│   ├── models.py         # pydantic models
│   ├── prompts.py        # ALL prompts
│   ├── llm.py            # Liquid local client
│   ├── nimble_client.py
│   ├── tinybird_client.py
│   ├── flux_client.py
│   └── connectivity.py   # online/offline detection
├── agent/
│   ├── agent.py          # autonomous loop: python -m agent.agent
│   ├── storm.py          # advisories → phase + ranked ZIPs
│   ├── stores.py         # store discovery + dedupe
│   ├── catalog.py        # one-time: product IDs per essential per chain
│   ├── stock.py          # stock checks via the fallback ladder
│   ├── forecast.py       # stockout risk (Tinybird endpoint, SQLite fallback)
│   ├── visuals.py        # FLUX pictograms + Pillow status cards
│   └── state.py          # atomic checkpoint save/load
├── tinybird/             # Forward datafiles: datasources + endpoints
├── web/                  # index.html, offline.html, ask.html, style.css, vendor/leaflet
├── scripts/              # check_liquid.py check_nimble.py check_stock_ladder.py check_tinybird.py check_flux.py
└── data/                 # gitignored: app.db, state.json, images/, event_queue.jsonl
```



## 5. Data model

SQLite (source of truth):

```sql
storm_updates(id, ts_utc, source_url, summary, phase_proposed, advisory_json)
zips(zip, area, priority, reason, phase, phase_locked, updated_ts)
stores(id, name, chain, address_norm, zip, lat, lng, hours, source_url, found_ts)   -- UNIQUE(chain, address_norm)
essentials(key, name, pictogram_path)
products(id, essential_key, chain, product_id, url, title)                            -- from catalog.py
stock_checks(id, ts_utc, store_id, zip, essential_key, status, price, confidence, level, source_url)
                                                                                       -- status: in_stock|low|out|unknown; level: store|zip|agent
forecasts(id, ts_utc, essential_key, zip, risk, reason)
official_links(id, title, url, kind, found_ts)                                         -- shelters, closures, county, NWS/CPHC
agent_steps(step, ts_utc, phase, action, summary, duration_ms)
```

Tinybird datasources mirror `stock_checks` and `storm_updates`. Endpoints: `depletion_by_item_zip` (share of stores flipping to low/out in the last 2 h, recency-weighted), `stock_timeline` (per item/ZIP over time, for the demo chart).

## 6. Fixed interfaces

```python
# app/llm.py — Liquid, local. NO native tool calling: code drives the loop.
async def chat(system: str, user: str, max_tokens: int = 400) -> str
async def chat_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel   # validate; retry once with the error; raise

# app/nimble_client.py
async def search(query: str, *, focus: str | None = None, depth: str = "lite",
                 max_results: int = 8, time_range: str | None = None) -> list[dict]   # [{title, url, description}]
async def extract(url: str, *, render_js: bool = False) -> str                        # markdown
async def extract_batch(urls: list[str]) -> dict[str, str]
async def run_template(name: str, params: dict) -> dict
async def research_start(task: str) -> str                                            # returns run id immediately
async def research_result(run_id: str) -> dict | None                                 # None while running

# app/tinybird_client.py
def log_event(datasource: str, row: dict) -> None           # append to data/event_queue.jsonl; never blocks
async def flush_forever() -> None                           # background: NDJSON batches every 3 s; keep queue on failure
async def query_endpoint(name: str, params: dict) -> list[dict] | None   # None if unreachable → caller uses SQLite

# app/flux_client.py
async def generate(prompt: str, out_path: str) -> str | None   # None on any failure; never blocks the agent
```



## 7. Agent rules

- **Goal anchor**, at the top of every LLM call: *"Help people in Hurricane Nolo's path on Hawaii's Big Island and Maui get essential supplies safely, using live verified data. Never send people into danger. Always point to official sources."*
- **Code drives, model decides small things.** The model picks from an enumerated action menu and returns JSON (e.g. `{"action": "check_stock", "zip": "96720"}`); it never free-forms tool calls.
- **Short prompts.** Never send whole pages; trim Nimble markdown to the most relevant ~2,000 characters before the model sees it. All math in code.
- **Cycle** (`CYCLE_SECONDS`, default 540): storm check → phase + ZIP ranking → stores for new ZIPs → stock checks (top ZIPs first, within budget) → forecast → visuals if needed → publish to SQLite → `log_event` to Tinybird → checkpoint → one-sentence step summary.
- **Stock fallback ladder** (record which `level` each result came from): (1) retailer template with ZIP → (2) extract the store-availability page with JS rendering → (3) ZIP-level availability → (4) Web Search Agent in the background.
- **Web Search Agents are background jobs**: start, store the run id, pick up results on a later cycle. The cycle never waits.
- **Phase safety in code:** the model proposes a phase per area; code applies it. Once an area is DURING it stays DURING (`phase_locked`) until an official all-clear. In DURING, the API and UI never show "go buy" for that area. `PHASE_OVERRIDE` env var exists for the demo.
- **Untrusted data:** web pages, search results and social posts go inside delimited blocks, never treated as instructions. Social posts are hints that trigger a check, never facts.
- **Resumable + unkillable:** atomic checkpoint every step; resume on restart; catch/log/continue with prefixes (`[storm] [stores] [stock] [tb] [flux] [llm]`).
- **Budgets + kill switch:** `MAX_NIMBLE_CALLS`, `MAX_IMAGE_CALLS`; exit cleanly if `data/STOP` exists.
- **Time:** store UTC; display Hawaii time (UTC−10, no DST) and Pacific.
- **Offline mode:** `connectivity.py` checks every 30 s. Offline → agent pauses web work, UI banner "Offline — showing data from HH:MM HST", `/ask` answers from SQLite via the local model and always states the snapshot time.



## 8. BFL FLUX (add-on)

1. **Pictograms** for each essential, generated once by a script at start and cached: *"flat pictogram icon of a {item}, bold simple shapes, high contrast, white background, no text, no letters"*.
2. **Status cards** per ZIP: FLUX makes a calm illustrated background (no text); **Pillow overlays the verified facts** (item, store, checked time, official link). The image model never renders facts.

Placeholder image on any failure. Budget `MAX_IMAGE_CALLS=30`.

## 9. Environment variables (`.env.example`)

```
LOCAL_LLM_BASE_URL=http://localhost:8080/v1
LOCAL_LLM_MODEL=
NIMBLE_API_KEY=
TINYBIRD_TOKEN=
TINYBIRD_HOST=
BFL_API_KEY=
BFL_BASE_URL=https://api.bfl.ai/v1
BFL_IMAGE_ENDPOINT=
STORM_NAME=Nolo
REGION_ZIPS=96720,96740
MOCK_NIMBLE=0
MOCK_TINYBIRD=0
MOCK_FLUX=0
MOCK_LLM=0
CYCLE_SECONDS=540
MAX_NIMBLE_CALLS=800
MAX_IMAGE_CALLS=30
PHASE_OVERRIDE=
```



## 10. Build phases (in order; stop after each with files changed, exact commands, and a 3-step test)

**Phase 0 — Prove the risky parts (do now, ~45 min)**

1. Ask me my laptop (chip + RAM). Then give exact commands to download and serve Liquid: **LFM2.5-8B-A1B at 4-bit** if RAM ≥ 16 GB, else **LFM2.5-1.2B-Instruct**. Start the download immediately.
2. `.env.example`, `.gitignore`, `requirements.txt`, `.cursor/mcp.json` template.
3. `scripts/check_liquid.py`: one chat + one `chat_json` call; print latency and tokens/sec.
4. `scripts/check_nimble.py`: a `news` search for "Hurricane Nolo Hawaii"; list templates matching "maps", "walmart", "target", "home depot".
5. `scripts/check_stock_ladder.py` **(most important):** for ONE essential (flashlight) in Hilo 96720, try each rung of the stock ladder and print what each returns. This tells us which rung we build on. Report results to me in plain English.
6. `scripts/check_tinybird.py`: send 3 test events and read them back (walk me through `tb login` and creating the workspace).
7. `scripts/check_flux.py`: one flashlight pictogram saved to `data/images/`.

✅ Done when every script either passes or prints a clear reason and falls back to its mock.

*(Next phases — don't start yet:)*
**Phase 1** skeleton + clients + SQLite + `/health` → **Phase 2** storm + ZIP ranking + `catalog.py` → **Phase 3** stores + stock ladder + **start the long-running agent (target ≤ 1:15 PM PT)** → **Phase 4** Tinybird endpoints + forecast + phase safety → **Phase 5** FLUX pictograms + status cards → **Phase 6** UI: map/list, offline page, `/ask` → **Phase 7** README, feature freeze 3:30, demo video by 4:00, submit by 4:20.

**If behind, cut in this order:** status cards → pictograms → map (keep list) → forecast explanations. **Never cut:** live stock checks, phase safety, offline mode, Tinybird logging.

## 11. How to work with me

- One phase at a time; wait for my OK between phases.
- Small files, plain functions, no frameworks beyond FastAPI.
- Unclear doc detail → stop and ask; build the mock meanwhile.
- Secrets only in `.env`. Never print keys. Check `.gitignore` before any commit.



## Phase 0 results

Laptop: Apple M4, 24 GB RAM. Liquid model: `LiquidAI/LFM2.5-8B-A1B-MLX-4bit`, served locally with `mlx_lm.server` on port 8080. Chat check returned valid JSON at about 50 tokens/sec.


| Check                   | Result                                                                                                                                   |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `check_liquid.py`       | Passed. Local chat and JSON both worked.                                                                                                 |
| `check_nimble.py`       | Passed. 8 live Hurricane Nolo news results. Templates include `homedepot_serp`, `walmart_serp`, `target_serp`, and `google_maps_search`. |
| `check_stock_ladder.py` | Ran all four rungs. **No rung returned a confirmed store-level stock answer** for a flashlight in Hilo 96720.                            |
| `check_tinybird.py`     | Passed. 3 flashlight events for 96720 were written and read back through `stock_timeline`.                                               |
| `check_flux.py`         | Passed. Pictogram saved at `data/images/flashlight.png`.                                                                                 |


Stock ladder, in plain terms:

- Store template (`homedepot_serp`) accepts `keyword` and `zipcode`, then timed out at 90 seconds. No stock payload came back.
- Extracting a store page returned a national Walmart product page. The text was site navigation, not Hilo availability.
- ZIP shopping search returned national product links, not stock in 96720.
- The research agent was still queued when the check stopped.



### Stock probe (end of Phase 1, live)

`scripts/probe_stock.py` ran all three retailer templates for 96720 with the async endpoint.


| Template         | ZIP input | Availability in output                          | Live result for Hilo                                                                                     |
| ---------------- | --------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `walmart_serp`   | `zipcode` | `product_availability`, `product_out_of_stock`  | Worked. `store_location: 96720`, `product_availability: In stock` for flashlight, D batteries, and tarp. |
| `homedepot_serp` | `zipcode` | none in the schema                              | Returned products with `store_location: Hilo`, but no stock field at all.                                |
| `target_serp`    | `zipcode` | `availability`, `is_out_of_stock` in the schema | Returned empty `parsing` for all three terms. No rows, so no availability.                               |


Run times through `/v2/extract/templates/async`: Walmart 315–674 seconds, Home Depot 617 seconds, Target 488–510 seconds. These are minutes, not seconds, so the agent must submit checks on one cycle and collect them on a later one. A 5-minute poll window is not always enough.

**Phase 3 builds on** `walmart_serp`**.** It is the only template that returned a real availability value tied to the Hilo ZIP, so those checks record `level="zip"`. Home Depot can supply prices and the resolved store name, not stock. Target needs a different approach before it is worth a call.

## Progress log

**Done:** Phases 0–6. The autonomous agent is running, stock checks and forecasts are
saved in SQLite and mirrored to Tinybird, phase safety is enforced in code, and the
Phase 6 UI is available at `/`, `/offline`, and `/ask`. **Next:** Phase 7 only — feature
freeze, README, secrets audit, demo recording, and submission.

**Phase 7 prep (13:55 PT):** `README.md` written; `scripts/demo_stats.py` → `data/demo_stats.json`;
secrets audit passed (no key value from `.env` in any publishable file or in git history;
`.env`, `.cursor/mcp.json`, `data/`, `.tinyb` are ignored); `mlx-lm` added to `requirements.txt`.
Phase 5 status cards were cut. Still to do: README team/contact, commit, freeze at 3:30,
rehearse and record the demo, make the repo public, submit by 4:20.

**/ask fixes (14:05 PT):**
- Only `.gov`/`.mil`/Red Cross links count as official (`app/safety.py: is_official`). Research links from news sites are saved as `kind='news'` (8 existing rows relabeled); `/ask` says "News report (not official)" and filters links to the island asked about (`island_of`).
- Item questions get only that item's forecasts and no link list in the model facts; answer facts come right after the phase line so a shorter retry drops the storm summary instead.
- "Running out" with nothing at risk now has an explicit "Nothing is low or out in {zip}" fact.
- `grounded()` drops any model sentence with a number not in the facts (the model invented a shelter phone number), or claiming storm conditions arrived in a BEFORE area (it dropped a "NOT"), and strips echoed `<data>` tags. A hedge while facts exist falls back to the code answer. Tests: `tests/test_ask_grounding.py`.
- Depletion counts all ZIP-level checks in a ZIP as one store (Tinybird and SQLite), fixing "2 of 2 stores" for the single Hilo Walmart. Tinybird deployment 3 is live.
- **Phase 5 Part A done at 14:05 PT:** `scripts/gen_pictograms.py` made FLUX pictograms for the 9 missing essentials (flashlight was from Phase 0), saved as 256 px PNGs in `data/images/{key}.png` and recorded in `essentials.pictogram_path`. 9 of 30 FLUX calls used. All 10 reviewed: correct objects, no text. Part B (status cards) not built. Note `data/` is gitignored, so the icons are not in the repo; rerun the script after a fresh clone.
- **FLUX add-ons (14:20 PT):** `app/tips.py` holds 7 safety tips (fixed guidance text + FLUX pictogram `data/images/tip_{key}.png`, priority order) shown by phase: BEFORE get-ready tips in the stock panel; DURING tips in the safety box, the caution box, the offline page, and on cards. `agent/visuals.py`: one FLUX background per area (`bg_{slug}.png`), `card_facts()` + `render_card()` draw a 1200×630 JPEG through the safety gate; `GET /api/cards/{zip}` renders on request (no FLUX calls). Tests: `tests/test_cards.py`. Replaced two misleading images: the flashlight tip (reuses the essentials icon) and the Kona background (had red lava). FLUX budget: 20 of 30 used.
- **Tinybird deploys:** each deploy leaves the previous one in Staging, which blocks the next deploy ("already a deployment in progress"). Run `.venv/bin/tb --cloud deployment discard --wait` first, then `scripts/deploy_tinybird.py`.

**Phase 6 results:**

- `/` is the live dashboard: ranked area tabs, latest advisory, freshness and data-level
  labels, store map, "Running out next", agent journal, official sources, uptime, step
  count, and calls remaining.
- `/offline` is a local text-only supply and safety view with no external assets.
- `/ask` answers from SQLite through the local Liquid model. The demo question
  "Where is the nearest open shelter to Hilo?" answers from saved shelter research even
  with the internet off.
- Leaflet JavaScript and CSS are vendored under `web/vendor/`; only online map tiles use
  the internet. Every other UI dependency is local.
- DURING safety applies to all new endpoints: store pins, risks, and stock facts are
  hidden for affected ZIPs and replaced by shelter guidance and official links.
- Tests: `.venv/bin/python -m tests.test_ui`, `tests.test_safety`, and
  `tests.test_phase_rules` all pass.

**Decisions that differ from the spec:**

- Tinybird uses the official Python SDK (`tinybird-sdk`), not the `tb` CLI and not `.datasource` datafiles. Resources are defined in `tinybird/resources.py` and deployed with `scripts/deploy_tinybird.py`. Cloud only, no Tinybird Local.
- `nimble_client.py` adds `run_template_start` / `run_template_result` beyond SPEC §6, because template runs take minutes. The agent submits stock checks on one cycle and collects them on a later one.
- Stock ladder rung 1 is `walmart_serp`, not Home Depot. Results are recorded as `level="zip"`, not `"store"`, since Walmart reports availability for the ZIP's delivery store rather than a shelf.
- `research_start` returns a run id, but Nimble also needs the `web_search_agent_id` to poll. It is stored in the `kv` table under `nimble_run:<run_id>`.
- `TINYBIRD_API_URL` is accepted as an alias for `TINYBIRD_HOST`.
- Task polls do not spend the Nimble budget; only calls that start work do.

**Known issues:**

- Nimble template runs take 5–11 minutes and sometimes exceed a 5-minute poll window. Checks must survive several cycles.
- `target_serp` returns empty `parsing` for Hilo. Do not spend calls on it yet.
- `homedepot_serp` has no stock field in its output schema. Price and store name only.
- About 153 of 800 Nimble calls were already spent during Phase 1 probing.
- Installing `tinybird-sdk` downgraded `pydantic`, `python-dotenv`, `click`, and `packaging`. Everything still runs, but pin carefully before adding dependencies.
- Tinybird ingest is eventually consistent: a pipe query right after a successful write can return 0 rows. Retry about a second later.

**Commands** (run from the repo root; the venv is Python 3.12 at `.venv`):

```bash
# 1. Liquid model server (needs its own terminal; /health reports llm:false without it)
.venv/bin/mlx_lm.server --model LiquidAI/LFM2.5-8B-A1B-MLX-4bit --host 127.0.0.1 --port 8080

# 2. Web server
.venv/bin/uvicorn app.main:app --reload
# check it:  curl localhost:8000/health   and   curl localhost:8000/api/status

# 3. Autonomous agent
.venv/bin/python -m agent.agent
# kill switch: touch data/STOP
```
