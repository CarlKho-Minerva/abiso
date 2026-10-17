"""The Abiso agent: a coordinator and one specialist per disruption type.

WHY ONE SPECIALIST PER TYPE (spec section 2a): the disruptions one household in
Basak lives with come from different issuers (MECO for power, MCWD for water,
DepEd and the city for classes, the traffic office and DPWH for roads, PAGASA for
weather), each with its own vocabulary and its own way of posting. Each
specialist carries that type knowledge and only its own tool. The coordinator
decides which specialists to ask and merges their answers into one reply for
the person's barangay, in the person's language.

WHY AgentTool AND NOT sub_agents TRANSFER: transfer hands the whole conversation
to one sub-agent, which then answers alone. "Anything in Basak tomorrow?" needs
power AND water AND classes in one reply, so the coordinator calls the
specialists like tools and merges what they return.

The facts come from deterministic tools (serve/store.py), never from a model.
The model is Gemini (contest rule). The key is read at runtime from the process
environment (Cloud Run secret) or ~/.config/carl-life-os/.env, never written.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping, Optional, Union

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import BaseLlm, Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from serve import store

APP_NAME = "abiso"
MODEL = os.environ.get("ABISO_AGENT_MODEL", "gemini-3.8-flash")
ENV_FILE = Path(os.environ.get("ABISO_ENV_FILE", Path.home() / ".config" / "carl-life-os" / ".env"))

# name -> (tools, what this specialist knows about its type)
SPECIALISTS: dict[str, tuple[list[Callable], str]] = {
    "power": ([store.brownouts],
              "Power interruptions (brownouts). Issuers: MECO (Mactan Electric Company) for "
              "Lapu-Lapu, NGCP for grid alerts, relayed by the city. Notices name feeders and "
              "'portions of' a barangay; scheduled maintenance is posted ahead, faults after."),
    "water": ([store.water_interruptions],
              "Water interruptions and low pressure. Issuer: MCWD (Metropolitan Cebu Water "
              "District) or the local water provider, relayed by the city. Cuts often follow "
              "pipe repairs or power cuts at pumping stations."),
    "classes": ([store.classes_suspended, store.work_suspensions],
                "Class and work suspensions ('walang pasok'). Issuers: the city government and "
                "DepEd Lapu-Lapu City Division. Note which levels (preschool to grade 12, "
                "college) and public or private."),
    "roads": ([store.road_closures],
              "Road closures and rerouting. Issuers: the city traffic office and DPWH. Note "
              "the road, the hours and any alternate route the post gives."),
    "weather": ([store.weather_advisories],
                "Weather advisories relayed by the city: PAGASA typhoon wind signals, rainfall "
                "warnings. Only report what the city's notice says; do not forecast."),
}

SPECIALIST_INSTRUCTION = """\
You check one kind of disruption for Lapu-Lapu City, Cebu. Today is {today} (Philippine time).
{knowledge}

Always call your tool; never answer from memory. Reply in English, for the coordinator:
- one line per notice: the summary, the status (write CANCELLED in capitals if cancelled),
  then the source link(s) exactly as given in source_links;
- if found=false: "NONE: no official {name} notice" plus the area and date you checked.
Add nothing the tool did not return.
"""

COORDINATOR_INSTRUCTION = """\
You are Abiso. You tell a household in Lapu-Lapu City, Cebu, about official disruption
notices that affect their barangay. Today is {today} (Philippine time).

You have specialists: power, water, classes, roads, weather. Call them; never answer from
memory. In each request give the place and an explicit YYYY-MM-DD date (turn "tomorrow /
bukas / ugma" into a date from today).
- A question about one kind (brownout, tubig, pasok, road, bagyo): ask only that specialist.
- "Anything happening?", "What should we prepare?", "Unsa'y balita sa Basak?": ask all five
  and merge them into one answer for that place, most disruptive first.

How to answer:
1. In the language the person wrote in: English, Filipino or Taglish, or Cebuano/Bisaya.
2. Short and plain: at most 4 short lines, no headings, no markdown.
3. Every fact keeps its official post link from the specialist.
4. If nothing was found, say there is no official notice and do not guess.
   English: "No official notice found." Filipino: "Walang opisyal na abiso na nakita."
   Cebuano: "Walay opisyal nga abiso nga nakit-an." Then suggest checking the official pages.
5. If a notice was cancelled, say clearly that it was cancelled.
6. Abiso only covers Lapu-Lapu City. For other places, say so.
"""

ModelSpec = Union[str, BaseLlm, Mapping[str, BaseLlm], None]


def _today() -> str:
    return store._now().strftime("%A, %Y-%m-%d")


def instruction(ctx: Optional[ReadonlyContext]) -> str:
    return COORDINATOR_INSTRUCTION.format(today=_today())


def specialist_instruction(name: str) -> Callable[[ReadonlyContext], str]:
    knowledge = SPECIALISTS[name][1]
    return lambda ctx: SPECIALIST_INSTRUCTION.format(today=_today(), knowledge=knowledge, name=name)


def load_key() -> Optional[str]:
    """GEMINI_API_KEY from the environment, else from Carl's .env. Never copied."""
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def vertex() -> bool:
    """Gemini on Vertex AI (billed to the GCP project, no API key) when GOOGLE_GENAI_USE_VERTEXAI is set."""
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true", "yes"}


def gemini(model: str = MODEL) -> Gemini:
    if vertex():
        # The genai client reads GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION and uses the
        # runtime's own credentials (Cloud Run service account, or local ADC).
        return Gemini(model=model)
    key = load_key()
    # Without a key the genai client falls back to its own env/ADC lookup and fails
    # loudly on the first call, which is the right place to fail.
    return Gemini(model=model, client_kwargs={"api_key": key} if key else None)


def build_agent(model: ModelSpec = None, **callbacks) -> LlmAgent:
    """The coordinator with its five specialists.

    model: one model for every agent, or a mapping {"coordinator": m, "power": m, ...}
    (tests give each agent its own scripted model). callbacks (e.g.
    before_model_callback) are applied to every agent, so a call cap counts all calls.
    """
    shared = gemini() if model is None else model

    def pick(name: str):
        return shared[name] if isinstance(shared, Mapping) else shared

    # Low thinking: the facts come from tools, so the model only routes and phrases (measured
    # 2026-10-17 on Vertex: a classes question took 38 s at the default level).
    gen = types.GenerateContentConfig(
        temperature=0.1,
        thinking_config=types.ThinkingConfig(thinking_level=os.environ.get("ABISO_THINKING", "low")))
    specialists = [
        LlmAgent(name=name, model=pick(name), instruction=specialist_instruction(name),
                 description=f"Checks official {name} notices for a place and date in Lapu-Lapu City. "
                             f"Send the place and a YYYY-MM-DD date.",
                 tools=list(tools), generate_content_config=gen, **callbacks)
        for name, (tools, _) in SPECIALISTS.items()
    ]
    return LlmAgent(
        name="abiso",
        description=("Official disruption notices for Lapu-Lapu City, Philippines, across issuers: "
                     "power (MECO), water (MCWD), class suspensions, road closures, weather. Every "
                     "answer links to the official post; says 'no official notice found' rather than guess."),
        model=pick("coordinator"),
        instruction=instruction,
        tools=[AgentTool(agent=s) for s in specialists],
        generate_content_config=gen,
        **callbacks,
    )


root_agent = build_agent()   # the name `adk web` / `adk deploy` look for


class Asker:
    """One Runner, one session per person, so follow-ups ("how about Pusok?") work."""

    def __init__(self, agent: Optional[LlmAgent] = None):
        self.agent = agent or root_agent
        self.runner = Runner(app_name=APP_NAME, agent=self.agent,
                             session_service=InMemorySessionService())
        self.sessions: dict[str, str] = {}

    async def ask(self, question: str, user_id: str = "anon") -> str:
        sid = self.sessions.get(user_id)
        if sid is None:
            s = await self.runner.session_service.create_session(app_name=APP_NAME, user_id=user_id)
            sid = self.sessions[user_id] = s.id
        msg = types.Content(role="user", parts=[types.Part(text=question)])
        answer = ""
        async for ev in self.runner.run_async(user_id=user_id, session_id=sid, new_message=msg):
            if ev.author == self.agent.name and ev.is_final_response() and ev.content and ev.content.parts:
                answer = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
        if not answer.strip():
            raise RuntimeError("The agent returned no answer text.")
        return answer.strip()
