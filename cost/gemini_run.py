"""Paid run: real Gemini token counts on every sample (Carl approved paid calls up to $5, 2026-10-03).

Sends each of the 20 posts REPS times (default 3) with the same system prompt
and default generation config (thinking at the model default), and records
usageMetadata. Output and thinking tokens vary run to run, so repeats give the
spread. A running cost guard stops the run before it passes BUDGET_USD.

    uv run --no-project --with httpx python cost/gemini_run.py [reps] [model] [thinking_level]
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import httpx

import common

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
MODEL = sys.argv[2] if len(sys.argv) > 2 else "gemini-3.8-flash"
THINKING = sys.argv[3] if len(sys.argv) > 3 else None  # e.g. "low"; None = model default
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
PRICE_IN, PRICE_OUT = 0.75, 3.75  # USD per 1M tokens, gemini-3.8-flash paid tier to 2026-12-31
BUDGET_USD = 1.00                 # hard stop for this script; Carl's cap is $5 total


def main() -> None:
    key = common.gemini_key()
    rows, spent = [], 0.0
    with httpx.Client(timeout=180, headers={"x-goog-api-key": key}) as c:
        for rep in range(1, REPS + 1):
            for p in common.posts():
                if spent >= BUDGET_USD:
                    raise SystemExit(f"STOP: cost guard hit at ${spent:.4f}")
                t0 = time.time()
                r = c.post(URL, json={
                    "systemInstruction": {"parts": [{"text": common.system_prompt()}]},
                    "contents": common.gemini_contents(p),
                    "generationConfig": {"maxOutputTokens": common.MAX_OUTPUT_TOKENS * 8,
                                         **({"thinkingConfig": {"thinkingLevel": THINKING}} if THINKING else {})},
                })
                dt = time.time() - t0
                if r.status_code != 200:
                    raise SystemExit(f"generateContent failed for {p['sample_id']}: {r.status_code} {r.text[:300]}")
                j = r.json()
                u = j.get("usageMetadata", {})
                out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
                spent += (u.get("promptTokenCount", 0) * PRICE_IN + out * PRICE_OUT) / 1e6
                parts = j["candidates"][0].get("content", {}).get("parts", [])
                rows.append({"rep": rep, "sample_id": p["sample_id"], "issuer": p["issuer"], "has_image": bool(p["image"]),
                             "seconds": round(dt, 2), "usage": u, "finish_reason": j["candidates"][0].get("finishReason"),
                             "output": "".join(pt.get("text", "") for pt in parts if not pt.get("thought"))})
                print(rep, p["sample_id"], f"{dt:.1f}s", u.get("promptTokenCount"), u.get("candidatesTokenCount"),
                      u.get("thoughtsTokenCount"), f"${spent:.4f}")
    common.RUNS.mkdir(exist_ok=True)
    out_path = common.RUNS / (f"gemini_run_thinking-{THINKING}.json" if THINKING else "gemini_run.json")
    out_path.write_text(json.dumps({"model": MODEL, "endpoint": URL, "calls": len(rows), "reps": REPS, "thinking_level": THINKING or "model default",
                                    "spent_usd_at_list_price": spent,
                                    "run_at": datetime.now(timezone.utc).isoformat(), "rows": rows},
                                   indent=1, ensure_ascii=False))
    print(out_path, f"total ${spent:.4f}")


if __name__ == "__main__":
    main()
