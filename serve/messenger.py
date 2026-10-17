"""Messenger Platform webhook: the door a Lapu-Lapu resident actually uses.

Two things can arrive (Meta's documented `messages` webhook shapes):
  (a) a text question       -> the ADK agent answers, with the source link
  (b) a shared post, link or image -> saved to the compile queue, compiled
      (abiso.compile.compile_post, written elsewhere), and answered with the
      compiled summary and the calendar subscribe link

WHY THE WORK HAPPENS AFTER THE 200: Meta wants a 200 within 5 seconds and retries
(then disables the webhook) otherwise. A Gemini answer can take longer, so the
handler verifies the signature, schedules the work, and returns at once.

WHY THE SIGNATURE CHECK IS NOT OPTIONAL: anyone can POST to a public URL. Without
X-Hub-Signature-256 verification, a stranger could make the Page message people.
If MESSENGER_APP_SECRET is not set, POSTs are refused (503), loudly.

Environment:
    MESSENGER_VERIFY_TOKEN   chosen by Carl, typed into the Meta app's webhook settings
    MESSENGER_APP_SECRET     Meta app > Settings > Basic > App secret
    MESSENGER_PAGE_TOKEN     Page access token, for the Send API
    MESSENGER_GRAPH_VERSION  default v26.0
    ABISO_CALENDAR_ID        the public calendar's id, for the subscribe link
    ABISO_SHARE_QUEUE        default data/raw/messenger_shares.jsonl
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from abiso.schema import DisruptionType, Event, Media, Post, Status
from serve import store
from serve.calendar import subscribe_link

log = logging.getLogger("abiso.messenger")
MAX_TEXT = 2000                     # Send API limit for message.text
URL_RE = re.compile(r"https?://\S+")
SHARE_TYPES = {"image", "fallback", "post", "share", "template", "video", "reel"}
CITY_PAGE = "the official Lapu-Lapu City Facebook page"

Sender = Callable[[str, str], Awaitable[None]]
Answerer = Callable[[str, str], Awaitable[str]]


async def send_text(psid: str, text: str) -> None:
    """Send API: reply to one person. Raises on any failure; never silently drops."""
    token = os.environ.get("MESSENGER_PAGE_TOKEN")
    if not token:
        raise RuntimeError("MESSENGER_PAGE_TOKEN is not set; cannot reply on Messenger")
    ver = os.environ.get("MESSENGER_GRAPH_VERSION", "v26.0")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"https://graph.facebook.com/{ver}/me/messages",
                         params={"access_token": token},
                         json={"recipient": {"id": psid}, "messaging_type": "RESPONSE",
                               "message": {"text": text[:MAX_TEXT]}})
        r.raise_for_status()


def default_compiler():
    """abiso.compile.compile_post, imported only when a share arrives (another agent writes it)."""
    from abiso.compile import compile_post
    return compile_post


@dataclass
class Messenger:
    answer: Answerer                       # (question, psid) -> answer text
    send: Sender = send_text
    compiler: Callable[[], Callable] = default_compiler
    queue_path: Path = field(default_factory=lambda: Path(os.environ.get(
        "ABISO_SHARE_QUEUE", store.REPO / "data" / "raw" / "messenger_shares.jsonl")))

    # ---------- routing ----------

    async def handle_messaging(self, m: dict) -> None:
        psid = (m.get("sender") or {}).get("id")
        msg = m.get("message")
        if not psid or not msg or msg.get("is_echo"):
            return                          # delivery/read receipts, echoes of our own replies
        try:
            if is_share(msg):
                reply = await self.handle_share(psid, msg)
            elif (msg.get("text") or "").strip():
                reply = await self.answer(msg["text"].strip(), psid)
            else:
                reply = ("I can read typed questions and shared posts or photos of official "
                         "notices. Try: \"May brownout ba sa Basak bukas?\"")
        except Exception:
            log.exception("failed to handle message %s from %s", msg.get("mid"), psid)
            reply = ("Sorry, I couldn't check that right now. Please look at "
                     f"{CITY_PAGE} for the latest notices.")
        await self.send(psid, reply)

    # ---------- shares ----------

    async def handle_share(self, psid: str, msg: dict) -> str:
        post = share_to_post(msg)
        self.enqueue(post)
        try:
            compile_post = self.compiler()
        except (ImportError, AttributeError):
            log.warning("abiso.compile.compile_post not available; %s queued only", post.post_id)
            return "Salamat! I saved this post. I can't read it yet; it is queued and will be added once read."
        try:
            out = compile_post(post)
            if inspect.isawaitable(out):
                out = await out
        except NotImplementedError:
            return "Salamat! I saved this post. I can't read it yet; it is queued and will be added once read."
        events = [out] if isinstance(out, Event) else list(out or [])
        return share_reply(events)

    def enqueue(self, post: Post) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with self.queue_path.open("a", encoding="utf-8") as f:
            f.write(post.model_dump_json() + "\n")


def is_share(msg: dict) -> bool:
    if any(a.get("type") in SHARE_TYPES for a in msg.get("attachments") or []):
        return True
    return bool(URL_RE.search(msg.get("text") or ""))


def share_to_post(msg: dict) -> Post:
    """A Messenger share as a Post, so the compiler sees the same shape as a captured post."""
    text_parts, media, url = [msg.get("text") or ""], [], None
    for a in msg.get("attachments") or []:
        p = a.get("payload") or {}
        if a.get("type") == "image":
            media.append(Media(kind="image", url=p.get("url")))
        elif p.get("url"):
            media.append(Media(kind="link", url=p["url"], alt=p.get("title")))
            url = url or p["url"]
        if p.get("title"):
            text_parts.append(p["title"])
    if url is None:
        found = URL_RE.search(msg.get("text") or "")
        url = found.group(0) if found else None
    key = url or msg.get("mid") or json.dumps(msg, sort_keys=True)
    return Post(
        post_id="messenger-" + hashlib.sha256(key.encode()).hexdigest()[:16],
        page="messenger-share",
        url=url or f"messenger:{msg.get('mid', '')}",
        captured_at=datetime.now(store.PH),
        text="\n".join(t for t in text_parts if t).strip(),
        media=media,
        capture_method="messenger",
    )


def calendar_line() -> str:
    cid = os.environ.get("ABISO_CALENDAR_ID")
    return f"Add the city calendar: {subscribe_link(cid)}" if cid else ""


def share_reply(events: list[Event]) -> str:
    real = [e for e in events if e.type != DisruptionType.not_disruption]
    if not events:
        return "Salamat! I saved this post but couldn't find a notice in it."
    if not real:
        return ("Salamat! This post doesn't look like a disruption notice "
                "(no brownout, water, class or road change), so it won't go on the calendar.")
    lines = ["Salamat! Here is what the post says:"]
    for e in real[:3]:
        prefix = "CANCELLED: " if e.status == Status.cancelled else ""
        lines.append(f"- {prefix}{e.summary}")
    if any(e.start is None for e in real):
        lines.append("The post gives no date yet, so it is not on the calendar.")
    cal = calendar_line()
    if cal:
        lines.append(cal)
    return "\n".join(lines)


# ---------- HTTP ----------

def verify_signature(raw: bytes, header: Optional[str], secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.removeprefix("sha256="))


def make_router(messenger: Messenger) -> APIRouter:
    r = APIRouter()

    @r.get("/webhook")
    async def verify(request: Request):
        token = os.environ.get("MESSENGER_VERIFY_TOKEN")
        if not token:
            raise HTTPException(503, "MESSENGER_VERIFY_TOKEN is not set")
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), token):
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403, "verify token mismatch")

    @r.post("/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        secret = os.environ.get("MESSENGER_APP_SECRET")
        if not secret:
            log.error("MESSENGER_APP_SECRET is not set; refusing webhook POST")
            raise HTTPException(503, "MESSENGER_APP_SECRET is not set")
        raw = await request.body()
        if not verify_signature(raw, request.headers.get("x-hub-signature-256"), secret):
            raise HTTPException(403, "bad signature")
        body = json.loads(raw)
        if body.get("object") != "page":
            raise HTTPException(404, "not a page event")
        for entry in body.get("entry", []):
            for m in entry.get("messaging", []):
                background.add_task(messenger.handle_messaging, m)
        return PlainTextResponse("EVENT_RECEIVED")

    return r
