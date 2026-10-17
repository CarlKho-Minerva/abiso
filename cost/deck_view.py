"""Add the deck-facing view (`run_date`, `cost`) to cost/results.json.

The cost study writes rich, sourced numbers; the deck (deck/data.js) reads a small
fixed shape. This copies values across, never recomputes or converts them.
Run after any cost re-run: `uv run python cost/deck_view.py`.
"""
import json
from pathlib import Path

P = Path(__file__).with_name("results.json")
d = json.loads(P.read_text())
g, pc = d["gemini"], d["pc"]
head = pc["cost_per_1000_headline"]
rates = d["rates"]


def rate_label(key: str) -> str:
    r = rates.get(key, {})
    if isinstance(r, dict):
        bits = [str(r.get(k)) for k in ("value", "unit", "period", "as_of") if r.get(k)]
        return " ".join(bits) or key
    return str(r)


d["run_date"] = pc["reps"][0]["run_at"][:10]
d["cost"] = {
    "bars": [
        {"id": "gemini", "label": "Gemini API",
         "usd_per_1k": g["usd_per_1000"], "php_per_1k": g["php_per_1000"],
         "rate": "list price, default thinking"},
        {"id": "gemma_meco", "label": "Gemma, my PC, MECO rate (Lapu-Lapu)",
         "usd_per_1k": head["meco"]["usd"], "php_per_1k": head["meco"]["php"],
         "rate": rate_label("meco_residential")},
        {"id": "gemma_meralco", "label": "Gemma, my PC, Meralco rate",
         "usd_per_1k": head["meralco"]["usd"], "php_per_1k": head["meralco"]["php"],
         "rate": rate_label("meralco_residential")},
    ],
    "watts": round(pc["gpu_w_batch"]["mean"]),
    "watts_method": pc["method"],
}
P.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
print(json.dumps(d["cost"], indent=1, ensure_ascii=False)[:900])
