"""The coordinator and its specialists, with scripted models: no Gemini call here.

What these pin: each specialist only has its own type's tool; the coordinator
reaches facts only through specialists; a specialist's answer is built from what
its tool returned (link included); a fan-out merges several specialists; and the
instructions carry the rules a resident depends on."""
import asyncio

from serve.agent.agent import SPECIALISTS, Asker, build_agent, instruction, specialist_instruction
from serve.tests.conftest import ScriptedLlm, call, calls, models, say, say_all

BASAK_LINK = "https://example.invalid/SYNTHETIC/llcpio/posts/1001"
PAJO_LINK = "https://example.invalid/SYNTHETIC/llcpio/posts/1002"


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    """An AgentTool result: ADK wraps the specialist's text as {"result": text}."""
    return result["result"] if isinstance(result, dict) and "result" in result else str(result)


def specialist_line(r):
    if not r["found"]:
        return "NONE: no official notice"
    e = r["events"][0]
    return f"{e['summary']} {e['source_links'][0]}"


def test_coordinator_has_one_specialist_per_type_each_with_only_its_tools():
    agent = build_agent(model=ScriptedLlm())
    assert sorted(t.name for t in agent.tools) == ["classes", "power", "roads", "water", "weather"]
    tools = {t.name: sorted(f.__name__ for f in t.agent.tools) for t in agent.tools}
    assert tools == {"power": ["brownouts"], "water": ["water_interruptions"],
                     "classes": ["classes_suspended", "work_suspensions"],
                     "roads": ["road_closures"], "weather": ["weather_advisories"]}


def test_single_type_question_goes_through_the_power_specialist():
    m = models(
        coordinator=[call("power", request="Basak, 2026-10-04"),
                     say(lambda r: f"Oo, naay brownout sa Basak ugma. {text_of(r).split()[-1]}")],
        power=[call("brownouts", area="Basak", date="2026-10-04"), say(specialist_line)],
    )
    answer = run(Asker(build_agent(model=m)).ask("Naay brownout sa Basak ugma?", "psid-1"))
    assert answer.endswith(BASAK_LINK)
    assert len(m["power"].requests) == 2 and m["water"].requests == []


def test_whats_happening_fans_out_and_merges():
    m = models(
        coordinator=[calls(("power", {"request": "Basak, 2026-10-04"}),
                           ("water", {"request": "Pajo, 2026-10-05"})),
                     say_all(lambda rs: " | ".join(text_of(rs[k]) for k in sorted(rs)))],
        power=[call("brownouts", area="Basak", date="2026-10-04"), say(specialist_line)],
        water=[call("water_interruptions", area="Pajo", date="2026-10-05"), say(specialist_line)],
    )
    answer = run(Asker(build_agent(model=m)).ask("Unsa'y balita?"))
    assert BASAK_LINK in answer and PAJO_LINK in answer


def test_not_found_flows_through_to_the_answer():
    m = models(
        coordinator=[call("power", request="Pusok, 2026-10-20"),
                     say(lambda r: "No official notice found." if "NONE" in text_of(r) else "guess")],
        power=[call("brownouts", area="Pusok", date="2026-10-20"), say(specialist_line)],
    )
    assert run(Asker(build_agent(model=m)).ask("Brownout in Pusok on Oct 20?")) == "No official notice found."


def test_follow_up_reuses_the_same_session():
    m = models(
        coordinator=[call("power", request="Basak, 2026-10-04"), say(lambda r: "yes"),
                     call("power", request="Pusok, 2026-10-04"), say(lambda r: "yes too")],
        power=[call("brownouts", area="Basak", date="2026-10-04"), say(specialist_line),
               call("brownouts", area="Pusok", date="2026-10-04"), say(specialist_line)],
    )
    asker = Asker(build_agent(model=m))
    run(asker.ask("Brownout in Basak tomorrow?", "psid-2"))
    run(asker.ask("How about Pusok?", "psid-2"))
    assert "Brownout in Basak tomorrow?" in str(m["coordinator"].requests[2].contents)


def test_instructions_state_the_rules_and_today():
    text = instruction(None)
    assert "Saturday, 2026-10-03" in text
    for must in ("Walay opisyal nga abiso nga nakit-an", "Walang opisyal na abiso na nakita",
                 "No official notice found", "link", "cancelled", "Lapu-Lapu", "ask all five"):
        assert must in text
    for name in SPECIALISTS:
        s = specialist_instruction(name)(None)
        assert "never answer from memory" in s and "NONE" in s and "Saturday, 2026-10-03" in s
    assert "MECO" in specialist_instruction("power")(None)
    assert "MCWD" in specialist_instruction("water")(None)
