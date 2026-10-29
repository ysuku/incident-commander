"""The one MCP server the five read-only tools sit behind (Checkpoint 2.1 design).

One server per incident: build_server(case) binds the tools to that recorded case,
so a client can only ever see the incident it was given. The server, not
tools.py, owns what the model reads about each tool: its name, input schema
(generated from the typed signature) and the one-line DESCRIPTIONS below. The
agent's prompt is rendered from list_tools, so prompt and tools cannot drift.

Two ways in:
- in-process: ToolClient(build_server(case)) -- what loop.diagnose uses; a real
  MCP client session over in-memory streams, wrapped in a sync API.
- stdio, for any MCP host (MCP Inspector, Claude Desktop, Claude Code):
    python -m incident_commander.mcp_server --case checkoutservice_cpu/1
"""
import argparse
import json
import logging
from contextlib import ExitStack

from anyio.from_thread import start_blocking_portal
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from . import loaders, tools
from .loaders import IncidentCase

logging.getLogger("mcp").setLevel(logging.WARNING)  # one INFO line per request otherwise

# What the model reads about each tool. Kept short: these go into every prompt.
DESCRIPTIONS = {
    "metric_query": ("before/after-alert deviations, computed in code; "
                     "call it with NO filters first: it ranks every service and metric"),
    "log_search": "counts + cited lines",
    "trace_lookup": ("worst request paths; with service=<name>, that service's own and "
                     "inbound (caller-observed) latency and errors, before vs after the alert"),
    "service_map": "who calls whom",
    "runbook_lookup": "reference docs; hits suggest fixes but are NOT root-cause evidence",
}


def build_server(case: IncidentCase) -> FastMCP:
    """FastMCP server exposing the five tools, bound to one case. Tool errors
    (unknown service, bad args) come back to the client as isError results."""
    server = FastMCP("incident-commander",
                     instructions=f"Read-only diagnostic tools for recorded incident {case.case_id}.")

    @server.tool(description=DESCRIPTIONS["metric_query"])
    def metric_query(service: str | None = None, metric: str | None = None, top: int = 10) -> dict:
        return tools.metric_query(case, service=service, metric=metric, top=top)

    @server.tool(description=DESCRIPTIONS["log_search"])
    def log_search(query: str = "", service: str | None = None,
                   level: str | None = None, limit: int = 20) -> dict:
        return tools.log_search(case, query=query, service=service, level=level, limit=limit)

    @server.tool(description=DESCRIPTIONS["trace_lookup"])
    def trace_lookup(failing_only: bool = True, limit: int = 10, service: str | None = None) -> dict:
        return tools.trace_lookup(case, failing_only=failing_only, limit=limit, service=service)

    @server.tool(description=DESCRIPTIONS["service_map"])
    def service_map() -> dict:
        return tools.service_map(case)

    @server.tool(description=DESCRIPTIONS["runbook_lookup"])
    def runbook_lookup(symptom: str, k: int = 4) -> list[dict]:
        return tools.runbook_lookup(symptom, k=k)

    return server


class ToolError(ValueError):
    """The server answered isError: bad args, unknown name, or the tool raised."""


class ToolClient:
    """Sync MCP client for the (sync) ReAct loop: one session, kept open on a
    background event loop until close(). Use as a context manager."""

    def __init__(self, server: FastMCP):
        self._stack = ExitStack()
        self._portal = self._stack.enter_context(start_blocking_portal())
        self._session = self._stack.enter_context(self._portal.wrap_async_context_manager(
            create_connected_server_and_client_session(server._mcp_server)))
        self.tools = self._portal.call(self._session.list_tools).tools
        self.names = [t.name for t in self.tools]

    def call(self, name: str, args: dict):
        """Call one tool; returns its result as plain Python (dict or list)."""
        r = self._portal.call(self._session.call_tool, name, args)
        text = " ".join(c.text for c in r.content if getattr(c, "text", None))
        if r.isError:
            msg = " ".join(text.removeprefix(f"Error executing tool {name}: ").split())
            raise ToolError(msg[:300])  # pydantic errors are multi-line; notes are one line
        if r.structuredContent is not None:
            # FastMCP wraps non-object returns (list[dict]) as {"result": ...}
            sc = r.structuredContent
            return sc["result"] if set(sc) == {"result"} else sc
        return json.loads(text)

    def describe(self) -> str:
        """Tool lines for the system prompt, from the live schemas:
        'name(required, optional?) - description'."""
        lines = []
        for t in self.tools:
            schema = t.inputSchema or {}
            required = set(schema.get("required", []))
            params = ", ".join(p if p in required else f"{p}?" for p in schema.get("properties", {}))
            lines.append(f"{t.name}({params}) - {t.description}")
        return ";\n".join(lines) + "."

    def close(self) -> None:
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="incident_commander.mcp_server",
                                description="Serve one incident's tools over MCP stdio.")
    p.add_argument("--case", required=True)
    p.add_argument("--system", default="online-boutique")
    args = p.parse_args()
    build_server(loaders.load_case(args.case, args.system)).run()  # stdio


if __name__ == "__main__":
    main()
