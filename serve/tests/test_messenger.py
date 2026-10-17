"""Messenger webhook against Meta-shaped sample payloads (serve/tests/payloads/, SYNTHETIC)."""
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from abiso.schema import Event, Post
from serve.messenger import Messenger, make_router, share_to_post

PAYLOADS = Path(__file__).parent / "payloads"
SECRET = "test-app-secret"
PSID = "2000000000000002"


def payload(name):
    return (PAYLOADS / name).read_bytes()


def sign(raw):
    return "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MESSENGER_APP_SECRET", SECRET)
    monkeypatch.setenv("MESSENGER_VERIFY_TOKEN", "abiso-verify")
    monkeypatch.setenv("ABISO_CALENDAR_ID", "abc@group.calendar.google.com")


class Harness:
    def __init__(self, compile_post=None, compiler=None):
        self.sent, self.asked = [], []

        async def answer(q, psid):
            self.asked.append((q, psid))
            return "Oo, naay brownout sa Basak ugma, 8 AM hangtod 5 PM. https://example.invalid/x"

        async def send(psid, text):
            self.sent.append((psid, text))

        self.m = Messenger(answer=answer, send=send,
                           compiler=compiler or (lambda: compile_post))
        app = FastAPI()
        app.include_router(make_router(self.m))
        self.client = TestClient(app)

    def post(self, raw, signature=None):
        return self.client.post("/webhook", content=raw,
                                headers={"content-type": "application/json",
                                         "x-hub-signature-256": signature or sign(raw)})


def basak_event(**kw):
    base = dict(event_id="e1", type="power", start="2026-10-04T08:00:00+08:00",
                end="2026-10-04T17:00:00+08:00", summary="No power, Pajo, Sun 4 Oct, 8 AM to 5 PM",
                source_post_ids=["p"], confidence=0.9, model="test")
    return Event(**{**base, **kw})


def test_verification_echoes_challenge(env):
    h = Harness()
    r = h.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "abiso-verify",
                                         "hub.challenge": "1158201444"})
    assert r.status_code == 200 and r.text == "1158201444"


def test_verification_rejects_wrong_token(env):
    r = Harness().client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                 "hub.challenge": "1"})
    assert r.status_code == 403


def test_text_question_goes_to_agent_and_reply_is_sent(env):
    h = Harness()
    r = h.post(payload("text_question.json"))
    assert r.status_code == 200 and r.text == "EVENT_RECEIVED"
    assert h.asked == [("Naay brownout sa Basak ugma?", PSID)]
    assert h.sent and h.sent[0][0] == PSID and "https://" in h.sent[0][1]


def test_bad_signature_is_refused_and_nothing_runs(env):
    h = Harness()
    assert h.post(payload("text_question.json"), signature="sha256=deadbeef").status_code == 403
    assert h.asked == [] and h.sent == []


def test_missing_app_secret_refuses_loudly(monkeypatch):
    monkeypatch.delenv("MESSENGER_APP_SECRET", raising=False)
    assert Harness().post(payload("text_question.json")).status_code == 503


def test_echo_of_our_own_message_is_ignored(env):
    h = Harness()
    assert h.post(payload("echo.json")).status_code == 200
    assert h.sent == [] and h.asked == []


def test_image_share_is_queued_compiled_and_answered_with_calendar_link(env, tmp_path):
    seen = []

    def compile_post(post: Post):
        seen.append(post)
        return [basak_event()]

    h = Harness(compile_post=compile_post)
    h.post(payload("image_share.json"))
    assert seen and seen[0].media[0].kind == "image"
    assert seen[0].capture_method == "messenger"
    queued = [json.loads(l) for l in h.m.queue_path.read_text().splitlines()]
    assert queued[0]["post_id"] == seen[0].post_id
    reply = h.sent[0][1]
    assert "No power, Pajo, Sun 4 Oct, 8 AM to 5 PM" in reply
    assert "https://calendar.google.com/calendar/u/0?cid=" in reply
    assert h.asked == []                                   # a share is not a question


def test_link_share_keeps_url_and_title(env):
    seen = []
    h = Harness(compile_post=lambda p: seen.append(p) or basak_event(status="cancelled"))
    h.post(payload("link_share.json"))
    assert seen[0].url == "https://example.invalid/SYNTHETIC/llcpio/posts/2001"
    assert "ABISO: Power interruption, Pajo" in seen[0].text
    assert "CANCELLED: No power" in h.sent[0][1]


def test_async_compiler_is_awaited(env):
    async def compile_post(post):
        return basak_event()
    h = Harness(compile_post=compile_post)
    h.post(payload("image_share.json"))
    assert "No power, Pajo" in h.sent[0][1]


def test_compiler_not_written_yet_still_queues_and_says_so(env):
    def missing():
        raise ImportError("No module named 'abiso.compile'")
    h = Harness(compiler=missing)
    h.post(payload("image_share.json"))
    assert h.m.queue_path.exists()
    assert "queued" in h.sent[0][1]


def test_not_a_disruption_is_said_plainly(env):
    h = Harness(compile_post=lambda p: [basak_event(type="not_disruption")])
    h.post(payload("link_share.json"))
    assert "doesn't look like a disruption notice" in h.sent[0][1]


def test_agent_failure_becomes_an_apology_not_silence(env):
    h = Harness()

    async def broken(q, psid):
        raise RuntimeError("Gemini down")
    h.m.answer = broken
    h.post(payload("text_question.json"))
    assert "couldn't check that right now" in h.sent[0][1]


def test_share_post_id_is_stable_for_same_link():
    msg = json.loads(payload("link_share.json"))["entry"][0]["messaging"][0]["message"]
    assert share_to_post(msg).post_id == share_to_post(dict(msg, mid="other")).post_id
