"""One Cloud Run service, three doors onto the same events.

    /webhook                         Messenger (GET verify, POST messages)
    /mcp/                            MCP, streamable HTTP: the serve/store.py query tools
    /a2a/                            A2A JSON-RPC (ADK to_a2a)
    /a2a/.well-known/agent-card.json the A2A agent card (built by ADK from the agent and its tools)
    /, /api/*                        the public page and its JSON (serve/web.py)
    /tasks/refresh                   Cloud Scheduler: capture, compile new posts, reconcile (serve/refresh.py)
    /health                          liveness, plus how many events are loaded
                                     (not /healthz: Cloud Run's front end answers 404 for paths ending in z)

    uv run uvicorn serve.app:app --port 8080

ABISO_PUBLIC_URL (e.g. https://abiso-xxxx.a.run.app) sets the URL the agent card
advertises; it defaults to http://localhost:8080.
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import LlmAgent

from serve import store
from serve.agent.agent import MODEL, Asker, build_agent
from serve.agent.usage import Meter
from serve.mcp_server import build_server, http_app
from serve.messenger import Messenger, make_router
from serve import web

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def create_app(agent: Optional[LlmAgent] = None, messenger: Optional[Messenger] = None) -> FastAPI:
    if agent is None:
        # Every Gemini call is logged with its cost; past ABISO_MAX_USD per instance, calls stop.
        meter = Meter(model=MODEL, max_usd=float(os.environ.get("ABISO_MAX_USD", "2.00")))
        agent = build_agent(**meter.callbacks())
    asker = Asker(agent)
    messenger = messenger or Messenger(answer=lambda q, psid: asker.ask(q, user_id=psid))

    mcp = build_server()
    mcp_app = http_app(mcp)

    public = urlparse(os.environ.get("ABISO_PUBLIC_URL", "http://localhost:8080"))
    a2a_app = to_a2a(agent, host=public.hostname, protocol=public.scheme,
                     port=public.port or (443 if public.scheme == "https" else 80),
                     rpc_path="a2a")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # Mounted sub-apps do not get lifespan events, so run theirs here.
        async with mcp.session_manager.run():
            async with a2a_app.router.lifespan_context(a2a_app):
                yield

    app = FastAPI(title="Abiso", lifespan=lifespan)
    app.include_router(make_router(messenger))

    firestore = os.environ.get("ABISO_STORE") == "firestore"

    def refresh() -> dict:
        from serve import refresh as job
        return job.run(max_usd=float(os.environ.get("ABISO_REFRESH_RUN_USD", "0.15")))

    def log_q(q, answer, ok, ms):
        from serve import fs
        fs.log_question(q, answer, ok, ms)

    app.include_router(web.make_router(ask=lambda q, sid: asker.ask(q, user_id=sid),
                                       refresh=refresh if firestore else None,
                                       log_question=log_q if firestore else None))

    @app.get("/health")
    def health():
        return {"ok": True, "events": len(store.load_events()), "events_file": str(store.events_path())}

    app.mount("/static", StaticFiles(directory=web.WEB), name="static")
    app.mount("/mcp", mcp_app)
    app.mount("/", a2a_app)        # last: serves /a2a/ and its agent card; everything above wins
    return app


app = create_app()
