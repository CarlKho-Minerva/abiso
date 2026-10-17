"""The spend meter: correct dollars, and calls refused past the budget."""
import asyncio

from google.adk.models import LlmResponse
from google.genai import types

from serve.agent.agent import Asker, build_agent
from serve.agent.usage import Meter
from serve.tests.conftest import call, models, say


class Ctx:
    agent_name = "power"


def response(i, o, thoughts=0):
    return LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(
        prompt_token_count=i, candidates_token_count=o, thoughts_token_count=thoughts))


def test_cost_counts_thinking_as_output():
    m = Meter(model="gemini-3.8-flash")
    m.after(Ctx(), response(1_000_000, 100_000, thoughts=100_000))
    assert round(m.usd, 4) == round(0.75 + 0.2 * 3.75, 4)


def test_unknown_model_is_priced_high_not_free():
    m = Meter(model="gemini-99-mystery")
    m.after(Ctx(), response(1_000_000, 0))
    assert m.usd >= 0.75


def test_dollar_budget_blocks_further_calls():
    m = Meter(model="gemini-3.8-flash", max_usd=0.01)
    assert m.before(Ctx(), None) is None
    m.after(Ctx(), response(20_000, 0))                # $0.015, over budget
    blocked = m.before(Ctx(), None)
    assert blocked is not None and "budget" in blocked.content.parts[0].text and m.blocked == 1


def test_meter_counts_specialist_calls_too():
    m = models(
        coordinator=[call("power", request="Basak, 2026-10-04"), say(lambda r: "ok")],
        power=[call("brownouts", area="Basak", date="2026-10-04"), say(lambda r: "line")],
    )
    meter = Meter(model="gemini-3.8-flash")
    asyncio.run(Asker(build_agent(model=m, **meter.callbacks())).ask("brownout Basak?"))
    assert meter.calls == 4                               # 2 coordinator + 2 power
