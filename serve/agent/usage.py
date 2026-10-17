"""Count Gemini calls, tokens and dollars; refuse calls past a budget.

WHY: paid runs need a number, not a feeling (Carl's rule since 2026-10-01). The
meter is attached to every agent (coordinator and specialists), so a fan-out to
five specialists is counted in full. Each call is logged as one JSON line, which
on Cloud Run lands in Cloud Logging and can be summed afterwards.

Prices: Gemini API paid tier, USD per 1M tokens, from ai.google.dev/gemini-api/docs/pricing
(read 2026-10-03). Thinking tokens bill as output. Unknown models count at the
highest listed price so the estimate errs high.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from google.adk.models import LlmResponse
from google.genai import types

log = logging.getLogger("abiso.usage")

PRICES = {  # model: (input, output) USD per 1M tokens
    "gemini-3.8-flash": (0.75, 3.75),     # through 2026-12-31; doubles on 2027-01-01
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-2.5-flash": (0.30, 2.50),
}
WORST = max(PRICES.values(), key=lambda p: p[1])


@dataclass
class Meter:
    model: str
    max_calls: Optional[int] = None
    max_usd: Optional[float] = None
    calls: int = 0
    blocked: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    history: list = field(default_factory=list)

    def price(self) -> tuple[float, float]:
        return PRICES.get(self.model, WORST)

    def before(self, callback_context, llm_request):
        over_calls = self.max_calls is not None and self.calls >= self.max_calls
        over_usd = self.max_usd is not None and self.usd >= self.max_usd
        if over_calls or over_usd:
            self.blocked += 1
            why = f"{self.max_calls} calls" if over_calls else f"${self.max_usd:.2f}"
            log.error("Gemini call blocked: budget of %s reached", why)
            return LlmResponse(content=types.Content(role="model", parts=[
                types.Part(text=f"[budget of {why} reached; Gemini not called]")]))
        self.calls += 1
        return None

    def after(self, callback_context, llm_response: LlmResponse):
        u = llm_response.usage_metadata
        if u is None:
            return None
        i = u.prompt_token_count or 0
        o = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        pin, pout = self.price()
        cost = i / 1e6 * pin + o / 1e6 * pout
        self.input_tokens += i
        self.output_tokens += o
        self.usd += cost
        rec = {"event": "gemini_call", "agent": callback_context.agent_name, "model": self.model,
               "input_tokens": i, "output_tokens": o, "usd": round(cost, 6),
               "session_usd": round(self.usd, 6)}
        self.history.append(rec)
        log.info(json.dumps(rec))
        return None

    def callbacks(self) -> dict:
        return {"before_model_callback": self.before, "after_model_callback": self.after}

    def summary(self) -> str:
        return (f"{self.calls} Gemini calls, {self.input_tokens} input + {self.output_tokens} output "
                f"tokens, ${self.usd:.4f} ({self.model}); blocked by budget: {self.blocked}")
