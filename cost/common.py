"""Shared workload definition for the cost study (subproblem 8).

The workload is 20 SYNTHETIC posts in cost/samples/ that mirror the compile
task: the system prompt in samples/system_prompt.txt plus one post (text in
Cebuano, Taglish or English; 6 of 20 carry one 1080x1350 JPEG notice image).
Four posts per issuer: city, power utility (MECO), water district (MCWD),
DepEd division, PAGASA. Equal weights are an assumption: real posting volume
per issuer has not been counted yet.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

HERE = Path(__file__).parent
SAMPLES = HERE / "samples"
RUNS = HERE / "runs"
ENV_FILE = Path.home() / ".config/carl-life-os/.env"

VLLM_BASE = os.environ.get("ABISO_VLLM_URL", "http://localhost:8000/v1").removesuffix("/v1")
VLLM_MODEL = "gemma-4-31b-it"
MAX_OUTPUT_TOKENS = 1024


def system_prompt() -> str:
    return (SAMPLES / "system_prompt.txt").read_text()


def posts() -> list[dict]:
    rows = [json.loads(l) for l in (SAMPLES / "posts.jsonl").read_text().splitlines() if l.strip()]
    assert all(r["synthetic"] for r in rows), "cost workload must be SYNTHETIC only"
    return rows


def user_text(p: dict) -> str:
    return f"Page: {p['page']}\nPost time: {p['posted_at']}\nPost text:\n{p['text']}"


def image_b64(p: dict) -> str | None:
    if not p["image"]:
        return None
    return base64.b64encode((SAMPLES / p["image"]).read_bytes()).decode()


def gemini_key() -> str:
    """Read GEMINI_API_KEY at runtime from Carl's env file; never copied anywhere."""
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"GEMINI_API_KEY not found in {ENV_FILE}")


def gemini_contents(p: dict) -> list[dict]:
    parts: list[dict] = [{"text": user_text(p)}]
    b64 = image_b64(p)
    if b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    return [{"role": "user", "parts": parts}]


def openai_messages(p: dict) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": user_text(p)}]
    b64 = image_b64(p)
    if b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": content}]
