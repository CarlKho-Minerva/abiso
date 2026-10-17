"""The same three tools, as an MCP server any agent can call.

WHY THE RAW TOOLS AND NOT THE CHAT AGENT: another agent already speaks its user's
language. What it lacks is the facts. Exposing disruptions / brownouts /
classes_suspended directly means an MCP client gets structured, sourced results
with no Gemini call in between: free per query, deterministic, and the same
functions the Messenger agent uses (serve/store.py).

    uv run python -m serve.mcp_server            # stdio, for Claude Code, Codex, IDEs
    (HTTP: mounted at /mcp by serve/app.py)
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from serve import store

INSTRUCTIONS = (
    "Official disruption notices for Lapu-Lapu City, Philippines, compiled from the city's "
    "own posts. Every result has source_links. found=false means no official notice was "
    "found: report that, do not guess. Dates are Philippine time (UTC+8)."
)


def build_server() -> MCPServer:
    server = MCPServer(name="abiso", instructions=INSTRUCTIONS)
    for fn in store.TOOLS:
        server.add_tool(fn)
    return server


def http_app(server: MCPServer):
    """Streamable-HTTP app; the caller must run server.session_manager.run() in its lifespan.

    DNS-rebinding protection is for servers on localhost; behind Cloud Run the Host
    header is the service URL, so it is off here.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    build_server().run(transport="stdio")
