# serve/: deliver and let agents ask (subproblems 5 and 6)

Reads `data/events.jsonl` (one `abiso.schema.Event` per line) and `data/raw/posts.jsonl`
(for source links). Every door gives the same answer, because every door calls the same
functions in `store.py`:

| Function | Asks |
|---|---|
| `disruptions(area, date, type)` | anything: power, water, class/work suspension, road, weather |
| `brownouts(area, date)` | power only |
| `water_interruptions(area, date)`, `road_closures(...)`, `weather_advisories(...)`, `work_suspensions(...)` | one type each |
| `classes_suspended(city, date)` | "walang pasok?"; Lapu-Lapu City only, says so for other cities |

Each returns `found`, the matching events (summary, status, areas, times, `source_links`)
and a `note`. `found: false` means **no official notice found**, with the time the records
were last updated. Nothing in `store.py` calls a model.

| File | Door |
|---|---|
| `calendar.py` | Google Calendars: the city, and one per barangay (Basak first), upsert keyed by `event_id` |
| `agent/agent.py` | ADK coordinator + one specialist per type (power, water, classes, roads, weather) |
| `agent/usage.py` | counts every Gemini call, tokens and dollars; stops calls past a budget |
| `messenger.py` | Messenger webhook: questions go to the agent, shared posts go to the compiler |
| `mcp_server.py` | the store functions as MCP tools (stdio, or HTTP at `/mcp/`) |
| `app.py` | one Cloud Run service: `/webhook`, `/mcp/`, `/a2a/`, `/health` |
| `fixtures/` | **SYNTHETIC** events and posts (`synthetic-` ids, `example.invalid` links) |

## The agent

The disruptions one household lives with come from different issuers (MECO, MCWD, DepEd and
the city, the traffic office and DPWH, PAGASA). Each specialist knows its issuer and has only
its own tool. The coordinator (`abiso`) calls specialists through ADK `AgentTool`, so it
can ask one ("brownout?") or all five ("anything happening in Basak tomorrow?") and merge
the answers into one reply for that barangay, in the asker's language, with every link.

## Run and test

```sh
uv run pytest serve/tests                     # no network, no Gemini (model is scripted)
ABISO_EVENTS=serve/fixtures/events.jsonl ABISO_POSTS=serve/fixtures/posts.jsonl \
  uv run uvicorn serve.app:app --port 8080    # local server on the fixture
uv run python -m serve.smoke "Naay brownout sa Basak ugma?"   # PAID: live Gemini, capped at 30 calls / $0.25
```

## Calendars

Both exist and are **private** :

| Calendar | Carries | Id |
|---|---|---|
| Abiso: Lapu-Lapu City | every disruption | `c_60e2a40f1f1cc55c1536056803a691eec9fda116b1d675f7fe15f64e5192fa96@group.calendar.google.com` |
| Abiso: Basak | only events naming Basak, plus city-wide ones; each with a "Prep tonight" event | `c_9f0f009efe0638fffb9d11544bf4e75e311e5b2d852c91c625fb6beded38670b@group.calendar.google.com` |

```sh
uv run python -m serve.calendar upsert --calendar <city id>                     # data/events.jsonl
uv run python -m serve.calendar upsert --calendar <basak id> --barangay Basak   # resident view
uv run python -m serve.calendar create-calendar --barangay Pusok                # next barangay
uv run python -m serve.calendar link --calendar <id>                            # subscribe link
```

Every event's description starts with what to do the evening before ("Water cut tomorrow
10 PM: Fill containers and the drum, store drinking water, do laundry and dishes early.")
and has a popup at 20:00 PH the evening before. That popup only fires for the account that
wrote it; Google does not pass event popups to people who subscribe or are shared the
calendar. So the barangay calendar also gets a 15-minute "Prep tonight: ..." event at 20:00
the evening before, which shows in their day view and fires their own default notification.

Upsert runs on a workstation with the calendar owner's Google token (`ABISO_LIFEOS_DIR`), not on Cloud Run. Cancelled events stay, titled `CANCELLED: ...`, with
no popup. Events with no date are skipped and reported, never placed on a guessed date.

**Making a calendar public or sharing it is Carl's decision.** Nothing in this code
changes sharing. To share the Basak one with the household: Google Calendar, settings for
"Abiso: Basak", "Share with specific people", add their address, "See all event details".

## Messenger: not testable live yet

Tests use payloads shaped from Meta's webhook reference (`tests/payloads/`, marked
SYNTHETIC). There is no Page or Meta app yet, so nothing has been received from Meta.
What Carl needs to do, in order:

1. Create a Facebook Page for the bot (e.g. "Abiso Lapu-Lapu").
2. At developers.facebook.com, create an app (type: Business), add the **Messenger** product.
3. Messenger settings: connect the Page and generate a **Page access token**.
4. App settings > Basic: copy the **App secret**.
5. Choose any **verify token** (a random string).
6. Put the three in Secret Manager (see Secrets below) and deploy.
7. Messenger settings > Webhooks: callback URL `https://<service>/webhook`, the verify
   token from step 5, subscribe the Page to the `messages` field (add `message_echoes`
   only if wanted; echoes are ignored).
8. While the app is in Development mode only people with a role on the app can message
   it. Public use needs App Review for `pages_messaging`.
9. The service is public, so Meta can reach `/webhook`; each request is still checked against
   the X-Hub-Signature-256 header.

Shares are appended to `data/raw/messenger_shares.jsonl` and passed to
`abiso.compile.compile_post(post) -> Event | list[Event] | None` (imported lazily; if it
is missing the share is still queued and the person is told so). On Cloud Run that file
is lost on restart: fine for the demo, needs Cloud Storage after it.

## Deploy

`serve/deploy.sh` prints the plan and exits unless `ABISO_DEPLOY_APPROVED=yes` (deploying costs money). It
deploys to `asia-southeast1` on project `dev-antler-508423-m1`, refuses unless the project bills to the
expected account, and:

- serves the page and API **publicly** (`--no-invoker-iam-check`; the organization's domain-restricted
  sharing refuses an `allUsers` binding), min 1 instance during judging, max 3;
- reads the feed from **Firestore** (`ABISO_STORE=firestore`, seeded with `python -m serve.fs push`);
- calls **Gemini on Vertex AI** (`GOOGLE_GENAI_USE_VERTEXAI=true`, `global` endpoint) as the Cloud Run
  service account: no API key;
- creates the **Cloud Scheduler** job `abiso-refresh` (every 3 hours, Asia/Manila) that POSTs to
  `/tasks/refresh` with the `X-Abiso-Task` token from Secret Manager;
- checks that `/` answers 200 and `/tasks/refresh` without the token answers 403.

Live: https://abiso-462688966054.asia-southeast1.run.app (A2A JSON-RPC at `/a2a`, method `SendMessage`,
header `A2A-Version: 1.0`).

### Secrets

`abiso-task-token` (made by `deploy.sh`, never printed) authenticates Cloud Scheduler to `/tasks/refresh`.
Gemini needs no key: it runs on Vertex AI as the service account.
The Messenger ones are optional for the deploy; create them when the Meta app exists:

```sh
export CLOUDSDK_CONFIG=$HOME/.config/carl-life-os/gcloud-tmuc P=dev-antler-508423-m1
printf %s '<verify token>' | gcloud secrets create abiso-messenger-verify-token --data-file=- --project $P
printf %s '<app secret>'   | gcloud secrets create abiso-messenger-app-secret   --data-file=- --project $P
printf %s '<page token>'   | gcloud secrets create abiso-messenger-page-token   --data-file=- --project $P
```

`deploy.sh` grants the Cloud Run runtime service account read access to each secret it uses.

## Agents

- **MCP (local):** `uv run python -m serve.mcp_server` (stdio). **MCP (remote):** `<url>/mcp/`
  with an identity token. Tools: every function in the first table. No Gemini call.
- **A2A:** agent card at `<url>/a2a/.well-known/agent-card.json`, built by ADK's `to_a2a`;
  its skills are the five specialists. ADK marks A2A as experimental.
