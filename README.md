# Abiso

**Live:** https://abiso-462688966054.asia-southeast1.run.app

In Lapu-Lapu City (Cebu, Philippines), the notices a household needs come from different offices, posted in different places: MECO posts brownouts, MCWD posts water cuts, DepEd and the city post class suspensions, PAGASA posts weather warnings. Many are images with no searchable text. People miss them, and AI assistants answer from old news or make something up.

Abiso reads each official notice once and compiles it into a typed event: what kind of disruption, which barangays, from when to when, its status, and a link to the source post. Then anyone can:

- **look** at the notices for their barangay on the web page,
- **ask** in Cebuano, Filipino or English ("Naay brownout sa Basak ugma?") and get an answer from a Gemini agent that only uses the official notices and links every one,
- **subscribe** to a Google Calendar per barangay, with a "prep tonight" event the evening before a cut,
- **call it from another agent** over MCP, A2A or plain REST, at no model cost.

Built for the Google Cloud AI Builder Cup 2026 (Sustainability & Social Impact) by team Pilipins.

## How it works

```
issuers' sites ──> Cloud Scheduler (every 3 h) ──> Cloud Run /tasks/refresh
                                                     │ capture new posts (collect/)
                                                     │ compile each once: Gemini 3.8 Flash on Vertex AI (abiso/compile.py)
                                                     │ reconcile: merge duplicates, extensions, cancellations (abiso/reconcile.py)
                                                     ▼
                                                  Firestore: posts, compiled, events, meta, question log
                                                     │
          ┌──────────────────────┬───────────────────┼─────────────────────┬──────────────────────┐
     web page + /api       ask box: Google ADK     MCP tools (/mcp/)      A2A card (/a2a)     Google Calendar
     (serve/web.py)        coordinator + 5 specialists (serve/agent/)                         per barangay (serve/calendar.py)
```

- **One model call per post, at compile time.** Questions are answered from the stored events by plain, tested code (`serve/store.py`), so every door gives the same answer and the source link travels with every fact.
- **The agent never guesses.** Each specialist (power, water, classes, roads, weather) can only call its own store function. With nothing found it says "no official notice found" and when the feed was last updated.
- **Spend is capped.** Each refresh stops before $0.15 of Gemini, all refreshes before $10; the ask box allows 8 questions per caller per 10 minutes, and each instance stops calling Gemini past $2.

## Google Cloud

| Service | What it does here |
|---|---|
| Gemini 3.8 Flash on Vertex AI | compiles each notice (text and images) into a typed event; runs the ask-box agent |
| Google ADK | coordinator agent with five specialist agents; A2A card via `to_a2a` |
| Cloud Run (asia-southeast1) | one service: web page, `/api`, `/mcp/`, `/a2a`, Messenger `/webhook`, `/tasks/refresh` |
| Firestore (Firebase) | the live feed: posts, compiler results, events, metadata, question log |
| Cloud Scheduler | runs the refresh every 3 hours (Asia/Manila) |
| Google Calendar API | one calendar per barangay, with evening-before prep events |
| Cloud Build, Artifact Registry, Secret Manager | build and store the image; hold the refresh token |

## Measured

On a locked 50-post test split (`eval/SPLIT_LOCK`, SHA-256 `b4d56d8f68c7…`, locked before any model saw the labels), with bootstrap 95% intervals in `eval/results.json`:

| Field | Gemini 3.8 Flash | Gemma 4 31B (local GPU) |
|---|---|---|
| Type | 100% | 84% |
| Barangays, exact set | 72% | 67% |
| Barangays, overlap (Jaccard) | 94% | 70% |
| Start within 1 h | 92% | 67% |
| End within 1 h | 100% | 69% |
| Status | 69% | 67% |

Labels were drafted by Claude Haiku and checked by Claude Sonnet; a person spot-checked 20 of them and agreed with 18 (both differences were the `status` field). Status is the least reliable field for labels and models alike.

Batch run (2026-10-08): 167/167 posts compiled with 0 failures into 366 events, $0.476 of Gemini. First live refresh (2026-10-17): 47 posts captured from four issuers, 33 new ones compiled for $0.08. Cost study per 1,000 posts: `cost/REPORT.md`.

## Run it

```sh
uv sync
uv run pytest                                   # 266 tests, no network, no model calls
uv run uvicorn serve.app:app --port 8080        # reads data/events.jsonl unless ABISO_STORE=firestore
```

Ask from any MCP client: `https://abiso-462688966054.asia-southeast1.run.app/mcp/` (tools: `disruptions`, `brownouts`, `water_interruptions`, `classes_suspended`, `road_closures`, `weather_advisories`). REST: `GET /api/events?area=Basak&type=water`.

## Layout

| Folder | |
|---|---|
| `abiso/` | data contract (`schema.py`), compiler, place matching for Lapu-Lapu's 30 barangays, reconciler |
| `collect/` | capture from the issuers' sites and public Facebook pages, logged out |
| `serve/` | Cloud Run service: store, web page, agent, MCP, A2A, Messenger, calendars, refresh, deploy |
| `eval/` | locked split, predictions, scoring with bootstrap intervals |
| `label/` | label drafting (Claude Haiku), checking (Claude Sonnet) and the review page |
| `cost/` | cost per 1,000 posts: Gemini tiers vs Gemma on owned hardware |
| `finetune/` | dataset and LoRA script for Gemma (built; the fine-tune has not been scored) |
| `data/` | captured posts, compiled events, labels |

Abiso reads public posts from official pages only, logged out, and never comments or personal data. It is not an official government service: always follow the issuer's own post.
