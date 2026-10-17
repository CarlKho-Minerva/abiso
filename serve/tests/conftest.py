"""Shared test setup: SYNTHETIC fixtures, a fixed clock, and a model that never calls Gemini."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Callable

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
os.environ["ABISO_EVENTS"] = str(FIXTURES / "events.jsonl")
os.environ["ABISO_POSTS"] = str(FIXTURES / "posts.jsonl")

from google.adk.models import BaseLlm, LlmRequest, LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

from serve import store  # noqa: E402

NOW = datetime(2026, 10, 3, 10, 0, tzinfo=store.PH)   # Saturday


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_now", lambda: NOW)
    monkeypatch.setenv("ABISO_EVENTS", str(FIXTURES / "events.jsonl"))
    monkeypatch.setenv("ABISO_POSTS", str(FIXTURES / "posts.jsonl"))
    monkeypatch.setenv("ABISO_SHARE_QUEUE", str(tmp_path / "shares.jsonl"))


class ScriptedLlm(BaseLlm):
    """A fake model. Each turn is a function of the request, so tests can check
    that the final text is built from what the tool actually returned."""

    model: str = "scripted"
    turns: list[Callable[[LlmRequest], types.Content]] = []
    requests: list[LlmRequest] = []

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False
                                     ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        turn = self.turns[len(self.requests) - 1]
        yield LlmResponse(content=turn(llm_request), turn_complete=True)


def call(name: str, **args) -> Callable[[LlmRequest], types.Content]:
    return lambda req: types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=name, args=args))])


def last_tool_result(req: LlmRequest) -> dict:
    for c in reversed(req.contents):
        for p in c.parts or []:
            if p.function_response:
                return p.function_response.response
    raise AssertionError("no tool result in the request")


def tool_results(req: LlmRequest) -> dict[str, dict]:
    """Every tool result in the latest turn, by tool name (for a parallel fan-out)."""
    for c in reversed(req.contents):
        got = {p.function_response.name: p.function_response.response
               for p in c.parts or [] if p.function_response}
        if got:
            return got
    raise AssertionError("no tool result in the request")


def calls(*specs: tuple[str, dict]) -> Callable[[LlmRequest], types.Content]:
    """Several function calls in one model turn."""
    return lambda req: types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in specs])


def say(fn: Callable[[dict], str]) -> Callable[[LlmRequest], types.Content]:
    return lambda req: types.Content(role="model", parts=[types.Part(text=fn(last_tool_result(req)))])


def say_all(fn: Callable[[dict[str, dict]], str]) -> Callable[[LlmRequest], types.Content]:
    return lambda req: types.Content(role="model", parts=[types.Part(text=fn(tool_results(req)))])


def models(**turns) -> dict[str, "ScriptedLlm"]:
    """One scripted model per agent; agents not given turns fail if called."""
    names = ["coordinator", "power", "water", "classes", "roads", "weather"]
    return {n: ScriptedLlm(turns=turns.get(n, []), requests=[]) for n in names}


@pytest.fixture(autouse=True)
def _offline_store(monkeypatch):
    """Tests read the fixture files, never Firestore or Vertex, whatever the shell exported."""
    for var in ("ABISO_STORE", "GOOGLE_GENAI_USE_VERTEXAI", "ABISO_TASK_TOKEN"):
        monkeypatch.delenv(var, raising=False)
