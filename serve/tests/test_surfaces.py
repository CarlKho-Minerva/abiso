"""MCP tools and the A2A agent card, served by the real app (scripted model)."""
import json

from fastapi.testclient import TestClient

from serve import store
from serve.app import create_app
from serve.messenger import Messenger
from serve.tests.conftest import ScriptedLlm
from serve.agent.agent import build_agent


async def _never(q, psid):
    raise AssertionError("not used")


def client():
    app = create_app(agent=build_agent(model=ScriptedLlm()), messenger=Messenger(answer=_never))
    return TestClient(app)


MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def rpc(c, method, params, id_=1):
    r = c.post("/mcp/", headers=MCP_HEADERS,
               json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_mcp_lists_and_calls_the_tools_over_http():
    with client() as c:
        init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "0"}})
        assert init["result"]["serverInfo"]["name"] == "abiso"
        tools = rpc(c, "tools/list", {}, 2)["result"]["tools"]
        assert sorted(t["name"] for t in tools) == sorted(f.__name__ for f in store.TOOLS)
        assert {"disruptions", "brownouts", "classes_suspended"} <= {t["name"] for t in tools}
        res = rpc(c, "tools/call", {"name": "classes_suspended",
                                    "arguments": {"city": "Lapu-Lapu", "date": "2026-10-06"}}, 3)
        payload = json.loads(res["result"]["content"][0]["text"])
        assert payload["found"] and payload["events"][0]["source_links"]


def test_a2a_agent_card_lists_the_specialists_as_skills():
    with client() as c:
        card = c.get("/a2a/.well-known/agent-card.json")
        assert card.status_code == 200, card.text
        body = card.json()
        assert body["name"] == "abiso"
        assert "Lapu-Lapu" in body["description"]
        text = json.dumps(body)
        skills = {s["id"] for s in body["skills"]}
        for name in ("power", "water", "classes", "roads", "weather"):
            assert f"abiso-{name}" in skills, text


def test_health_counts_events():
    with client() as c:
        assert c.get("/health").json()["events"] == 7
