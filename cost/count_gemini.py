"""Count Gemini input tokens for the 20-post workload with the free countTokens endpoint.

No generation, no charge. Writes cost/runs/gemini_counttokens.json.

    uv run --no-project --with httpx python cost/count_gemini.py [model]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import httpx

import common

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemini-3.8-flash"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:countTokens"


def main() -> None:
    key = common.gemini_key()
    rows = []
    with httpx.Client(timeout=60, headers={"x-goog-api-key": key}) as c:
        for p in common.posts():
            body = {"generateContentRequest": {
                "model": f"models/{MODEL}",
                "systemInstruction": {"parts": [{"text": common.system_prompt()}]},
                "contents": common.gemini_contents(p),
            }}
            r = c.post(URL, json=body)
            if r.status_code != 200:
                raise SystemExit(f"countTokens failed for {p['sample_id']}: {r.status_code} {r.text[:300]}")
            j = r.json()
            rows.append({"sample_id": p["sample_id"], "has_image": bool(p["image"]),
                         "total_tokens": j.get("totalTokens"),
                         "prompt_tokens_details": j.get("promptTokensDetails")})
            print(p["sample_id"], j.get("totalTokens"), j.get("promptTokensDetails"))
    common.RUNS.mkdir(exist_ok=True)
    out = common.RUNS / "gemini_counttokens.json"
    out.write_text(json.dumps({"model": MODEL, "endpoint": URL,
                               "run_at": datetime.now(timezone.utc).isoformat(), "rows": rows}, indent=1))
    print(out)


if __name__ == "__main__":
    main()
