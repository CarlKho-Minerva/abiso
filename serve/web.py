"""The public front door: a page anyone can open, its JSON API, and the scheduler's refresh hook.

    GET  /                 the page (serve/web/index.html)
    GET  /api/meta         counts, last update, barangays, sources
    GET  /api/events       ?area=Basak&type=water&when=upcoming|past|all&limit=50
    POST /api/ask          {"q": "...", "session": "..."} -> the ADK agent's answer (Gemini)
    POST /tasks/refresh    Cloud Scheduler only: header X-Abiso-Task must equal ABISO_TASK_TOKEN

The facts on the page come from serve/store.py, the same functions the agent, MCP and A2A call.
/api/ask is the only route that spends money: per caller at most ASK_PER_10MIN questions per
10 minutes, and the agent's Meter stops Gemini past ABISO_MAX_USD per instance.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from abiso.places import barangays
from abiso.schema import DisruptionType
from serve import store

log = logging.getLogger("abiso.web")
WEB = Path(__file__).resolve().parent / "web"
ASK_PER_10MIN = int(os.environ.get("ABISO_ASK_PER_10MIN", "8"))
ASK_TIMEOUT_S = 75
SOURCES = [
    {"issuer": "MECO (power)", "url": "https://mecomactan.com/"},
    {"issuer": "MCWD (water)", "url": "https://www.mcwd.gov.ph/"},
    {"issuer": "DepEd Lapu-Lapu City Division", "url": "https://depedlapulapu.net.ph/"},
    {"issuer": "PAGASA Visayas", "url": "https://www.pagasa.dost.gov.ph/regional-forecast/visprsd"},
    {"issuer": "Lapu-Lapu City government", "url": "https://lapulapucity.gov.ph/"},
]


class AskIn(BaseModel):
    q: str = Field(min_length=2, max_length=400)
    session: str = Field(default="web", max_length=64)


def _caller(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]   # never stored, only counted in memory


def make_router(ask: Callable[[str, str], Awaitable[str]],
                refresh: Optional[Callable[[], dict]] = None,
                log_question: Optional[Callable[..., None]] = None) -> APIRouter:
    r = APIRouter()
    recent: dict[str, deque] = defaultdict(deque)

    @r.get("/", include_in_schema=False)
    def page():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @r.get("/demo", include_in_schema=False)
    def demo():
        return FileResponse(WEB / "demo.html", headers={"Cache-Control": "no-cache"})

    @r.get("/api/meta")
    def meta():
        events = store.load_events()
        ts = store.updated_at()
        now = store._now()
        live = [e for e in events if e.type != DisruptionType.not_disruption]
        upcoming = [e for e in live if (w := store.window(e)) and w[1] >= now]
        return {
            "updated_at": ts.isoformat() if ts else None,
            "now": now.isoformat(),
            "n_events": len(live),
            "n_upcoming": len(upcoming),
            "n_posts": len(store.load_posts()),
            "by_type": Counter(e.type.value for e in live),
            "barangays": barangays(),
            "sources": SOURCES,
        }

    @r.get("/api/events")
    def events(area: str = "", type: str = "", when: str = "upcoming", limit: int = 50):
        if when not in {"upcoming", "past", "all"}:
            raise HTTPException(400, "when must be upcoming, past or all")
        try:
            want = store.resolve_type(type)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        posts = store.load_posts()
        now = store._now()
        rows = []
        for e in store.load_events():
            if e.type == DisruptionType.not_disruption or (want and e.type != want):
                continue
            if area.strip() and not store.area_matches(e, area):
                continue
            w = store.window(e)
            is_up = bool(w and w[1] >= now)
            if (when == "upcoming" and not is_up) or (when == "past" and (is_up or not w)):
                continue
            row = store._as_answer(e, posts)
            row.update(model=e.model, confidence=e.confidence, upcoming=is_up,
                       ongoing=bool(w and w[0] <= now < w[1]))
            rows.append((w[0].timestamp() if w else float("inf"), row))
        rows.sort(key=lambda t: t[0], reverse=(when == "past"))
        return {"count": len(rows), "events": [r for _, r in rows[:max(1, min(limit, 200))]]}

    @r.post("/api/ask")
    async def ask_route(body: AskIn, request: Request):
        who = _caller(request)
        q = recent[who]
        now = time.monotonic()
        while q and now - q[0] > 600:
            q.popleft()
        if len(q) >= ASK_PER_10MIN:
            return JSONResponse({"error": "Too many questions from here; try again in a few minutes."}, 429)
        q.append(now)
        t0 = time.monotonic()
        ok, answer = True, ""
        try:
            answer = await asyncio.wait_for(ask(body.q.strip(), f"web-{body.session}"), ASK_TIMEOUT_S)
        except asyncio.TimeoutError:
            ok, answer = False, "The agent took too long. Try again, or browse the list below."
        except Exception as e:  # noqa: BLE001  shown to the person and logged, never swallowed
            log.exception("ask failed")
            ok = False
            answer = ("The agent could not answer right now (budget reached or Gemini unavailable). "
                      "The list below is still current." if "budget" in str(e).lower() or "429" in str(e)
                      else f"The agent failed: {type(e).__name__}. The list below is still current.")
        ms = int((time.monotonic() - t0) * 1000)
        if log_question:
            try:
                log_question(body.q, answer, ok, ms)
            except Exception:  # noqa: BLE001
                log.exception("could not log the question to Firestore")
        return JSONResponse({"answer": answer, "ok": ok, "ms": ms}, 200 if ok else 503)

    @r.post("/tasks/refresh", include_in_schema=False)
    def refresh_route(x_abiso_task: str = Header(default="")):
        token = os.environ.get("ABISO_TASK_TOKEN", "")
        if not token or not hmac.compare_digest(x_abiso_task, token):
            raise HTTPException(403, "forbidden")
        if refresh is None:
            raise HTTPException(503, "refresh not configured")
        report = refresh()
        log.info("refresh %s", report)
        return JSONResponse(report, 200 if report.get("ok") else 500)

    return r
