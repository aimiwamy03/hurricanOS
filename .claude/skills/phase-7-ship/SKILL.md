---
name: phase-7-ship
description: Phase 7 of Shelfwatch. Feature freeze and ship: README with pitch, architecture and sponsor tools, a secrets audit before making the repo public, demo rehearsal including the wifi-off moment, demo video recording checklist, and the hackathon submission checklist. Use whenever the user says "phase 7", "ship", "README", "submit", "demo video", "make the repo public", or it is after 3:30 PT.
---

# Phase 7 — Ship

**Timeline (PT):** 3:30 feature freeze → 3:30–3:50 README + secrets audit → 3:50–4:10 record the demo video → 4:10–4:20 submit. **4:30 is the hard deadline; aim for 4:20.**

## Part A — Freeze
- No new features. Bug fixes only if something visibly breaks the demo.
- Leave the agent running. Note its uptime and step count for the README and demo.
- Pull quick stats for the pitch: `SELECT count(*) FROM stock_checks`, the number of stores, ZIPs, phase changes, and forecasts. Put them in `data/demo_stats.json` via a tiny script `scripts/demo_stats.py`.

## Part B — README.md (keep it scannable)
1. **One-liner:** "An autonomous agent that tracks Hurricane Nolo and live stock of emergency supplies for Hawaii's Big Island and Maui — and keeps helping offline when the internet goes down."
2. **Why it matters:** the storm, outages expected, supplies vanishing store by store. 3–4 sentences.
3. **What the agent does on its own:** the 8 steps from SPEC §1, plus the BEFORE/DURING/AFTER phases.
4. **Architecture:** a Mermaid diagram:
   ```mermaid
   flowchart LR
     Web[Live web] -->|Nimble| Agent
     Agent -->|events| TB[Tinybird Cloud]
     TB -->|depletion SQL| Agent
     Agent <-->|local| Liquid[Liquid LFM2.5 on laptop]
     Agent --> DB[(SQLite snapshot)]
     DB --> UI[Web UI + /ask]
     Agent -->|pictograms| FLUX[BFL FLUX]
   ```
5. **Sponsor tools and exactly what each does:** Nimble (list the features used), Liquid AI (local brain, offline), Tinybird (event memory + depletion endpoints), BFL FLUX (language-free pictograms, cards with verified overlays).
6. **Numbers from today's run:** from `demo_stats.json`.
7. **Safety design:** official sources everywhere, freshness labels, no store trips during storm conditions, social posts treated as hints, no generated text in images.
8. **Run it:** prerequisites, `.env.example`, the Liquid server command, `uvicorn`, `python -m agent.agent`.
9. **Limitations, honestly stated:** online stock can lag shelves; which data level was available (store / ZIP / research); estimates aren't guarantees.
10. **Team + contact.**

## Part C — Secrets audit (before making the repo public)
Run and show the output to the user:
```bash
git status                                   # .env and .cursor/mcp.json must NOT appear
git check-ignore .env .cursor/mcp.json data/ # all three must be printed
git grep -nI -E "(api[_-]?key|token|secret|Bearer )" -- . ':!*.md' ':!.env.example'
git log --all --oneline -- .env .cursor/mcp.json   # must print nothing
```
- If a key was **ever** committed: don't just delete it. Tell the user to rotate that key in the provider dashboard now, then rewrite history or start a fresh repo.
- `.env.example` must contain only empty values.

## Part D — Demo rehearsal (3 minutes, run it twice)
| Time | Beat | On screen |
|---|---|---|
| 0:00–0:30 | "Right now, Hurricane Nolo is approaching Hawaii. Since this morning, our agent has been working for the people in its path." | Main screen, uptime visible |
| 0:30–1:30 | Stores, stock, freshness; "this item it predicted would run out — and it did" | Map + Running out next |
| 1:30–2:15 | The journal: the track shift, re-ranking, an area switching to "stay put" | Journal + amber safety panel |
| 2:15–2:45 | **Turn off the wifi.** Banner appears. Ask "Where's the nearest open shelter to Hilo?" | `/ask` answering offline |
| 2:45–3:00 | "The storm takes the internet down. The help shouldn't go down with it." | Main screen |
Tips: pre-open all tabs; increase browser zoom; have the `/ask` question ready to paste; if live data is thin, point to the journal and timeline.

## Part E — Demo video (required for submission)
- Screen-record the rehearsal at 1080p with narration (QuickTime / OBS / Loom). Keep it under 3 minutes.
- Upload somewhere with a shareable link (unlisted YouTube, Loom, or Drive with "anyone with the link"). **Open the link in a private window to confirm it works.**

## Part F — Submission checklist
- [ ] Public GitHub repo URL (opens logged-out)
- [ ] Demo video link (opens logged-out)
- [ ] Description: what you built + tools used (paste README sections 1, 3, 5)
- [ ] Team names + contact emails
- [ ] Optional: screenshot of the main screen
- [ ] Submitted before 4:20 PT; confirmation seen

## After submitting
- Keep the agent running for the 5:00 finalist demos (data keeps accumulating).
- Laptop plugged in; wifi toggle ready; the Liquid server still running.

## Don't
- Don't add features after 3:30. Don't make the repo public before the secrets audit. Don't leave the video link private.
