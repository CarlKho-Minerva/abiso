"""Live smoke test of the agent against Gemini. PAID: every model call costs money.

Budget is enforced in code by serve/agent/usage.Meter on every agent (coordinator
and specialists): at most ABISO_SMOKE_MAX_CALLS calls (default 30) and
ABISO_SMOKE_BUDGET_USD dollars (default 0.25). Past either, calls are blocked and
the run exits 1. Runs against the SYNTHETIC fixture unless ABISO_EVENTS is set.

    uv run python -m serve.smoke "Naay brownout sa Basak ugma?" "May pasok ba sa Lunes?"
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"
os.environ.setdefault("ABISO_EVENTS", str(FIX / "events.jsonl"))
os.environ.setdefault("ABISO_POSTS", str(FIX / "posts.jsonl"))

from serve.agent.agent import MODEL, Asker, build_agent  # noqa: E402
from serve.agent.usage import Meter  # noqa: E402


async def main(questions: list[str]) -> int:
    meter = Meter(model=MODEL, max_calls=int(os.environ.get("ABISO_SMOKE_MAX_CALLS", "30")),
                  max_usd=float(os.environ.get("ABISO_SMOKE_BUDGET_USD", "0.25")))
    asker = Asker(build_agent(**meter.callbacks()))
    print(f"model: {MODEL}   cap: {meter.max_calls} calls / ${meter.max_usd}   "
          f"events: {os.environ['ABISO_EVENTS']}\n")
    for q in questions:
        c0, u0 = meter.calls, meter.usd
        answer = await asker.ask(q, user_id="smoke")
        agents = sorted({h["agent"] for h in meter.history[c0:]})
        print(f"Q: {q}\nA: {answer}\n   ({meter.calls - c0} calls via {', '.join(agents)}; "
              f"${meter.usd - u0:.4f})\n")
    print("total:", meter.summary())
    return 1 if meter.blocked else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or ["Naay brownout sa Basak ugma?"])))
