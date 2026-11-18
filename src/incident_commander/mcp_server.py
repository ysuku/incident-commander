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

The same server is the ToT state manager (Checkpoint 4.1): four branch tools over
one BranchBoard per incident. They are not diagnostic tools, so the ReAct prompt
never lists them (ToolClient.diagnostic); the ToT controller calls them.
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
    "open_branch": "ToT: open a hypothesis branch (service, fault_type); returns it",
    "record_result": "ToT: store the critic's verdicts (and the test run) on a branch; rescored here",
    "close_branch": "ToT: set a branch's status (pruned|selected|tied|exhausted) with a reason",
    "branch_status": "ToT: every branch with score, verdicts and tests, best first",
}
BRANCH_TOOLS = ("open_branch", "record_result", "close_branch", "branch_status")
# The tools that read each telemetry kind (the robustness ablation, 6.1 section 3).
# service_map is built from traces.csv, so hiding traces must hide it too.
KIND_TOOLS = {"metrics": ("metric_query",), "logs": ("log_search",),
              "traces": ("trace_lookup", "service_map")}


def build_server(case: IncidentCase) -> FastMCP:
    """FastMCP server exposing the five tools, bound to one case, plus the ToT
    branch tools over a fresh BranchBoard. Tool errors (unknown service, bad
    args) come back to the client as isError results."""
    server = FastMCP("incident-commander",
                     instructions=f"Read-only diagnostic tools for recorded incident {case.case_id}, "
                                  "plus the Tree-of-Thought branch board (in-memory hypothesis "
                                  "state for this session; nothing writes to the incident).")

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

    from .tot import BranchBoard  # not at top: tot -> signatures -> loop -> this module
    board = BranchBoard()

    @server.tool(description=DESCRIPTIONS["open_branch"])
    def open_branch(service: str, fault_type: str, reason: str = "") -> dict:
        return board.open(service, fault_type, reason)

    @server.tool(description=DESCRIPTIONS["record_result"])
    def record_result(branch_id: str, verdicts: list[dict], test: dict | None = None) -> dict:
        return board.record(branch_id, verdicts, test)

    @server.tool(description=DESCRIPTIONS["close_branch"])
    def close_branch(branch_id: str, status: str, reason: str = "") -> dict:
        return board.close(branch_id, status, reason)

    @server.tool(description=DESCRIPTIONS["branch_status"])
    def branch_status() -> dict:
        return board.status()

    return server


class ToolError(ValueError):
    """The server answered isError: bad args, unknown name, or the tool raised."""


class ToolClient:
    """Sync MCP client for the (sync) ReAct loop: one session, kept open on a
    background event loop until close(). Use as a context manager."""

    hidden: frozenset = frozenset()  # telemetry kinds withheld from this view (none)

    def __init__(self, server: FastMCP):
        self._stack = ExitStack()
        self._portal = self._stack.enter_context(start_blocking_portal())
        self._session = self._stack.enter_context(self._portal.wrap_async_context_manager(
            create_connected_server_and_client_session(server._mcp_server)))
        self.tools = self._portal.call(self._session.list_tools).tools
        self.names = [t.name for t in self.tools]
        self.diagnostic = [n for n in self.names if n not in BRANCH_TOOLS]

    def call(self, name: str, args: dict):
        """Call one tool; returns its result as plain Python (dict or list).
        A misspelled argument name is refused here: FastMCP would drop it and run
        the call unfiltered, an answer to a question nobody asked."""
        props = next((list((t.inputSchema or {}).get("properties", {})) for t in self.tools
                      if t.name == name), None)
        unknown = [k for k in args if k not in props] if props is not None else []
        if unknown:
            raise ToolError(f"unknown argument {', '.join(map(repr, unknown))} for {name}; "
                            f"arguments are {props}")
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
        return _describe(self.tools)

    def scoped(self, allowed) -> "ScopedToolClient":
        """This session, restricted to `allowed` tool names (a role's view)."""
        return ScopedToolClient(self, allowed)

    def close(self) -> None:
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _describe(tools) -> str:
    lines = []
    for t in tools:
        if t.name in BRANCH_TOOLS:
            continue
        schema = t.inputSchema or {}
        required = set(schema.get("required", []))
        params = ", ".join(p if p in required else f"{p}?" for p in schema.get("properties", {}))
        lines.append(f"{t.name}({params}) - {t.description}")
    return ";\n".join(lines) + "."


class ScopedToolClient:
    """One role's view of the server (Checkpoint 5.1: tool access differs by role).

    Only the allowed tools are listed, rendered into the role's prompt, or
    callable; anything else is refused here, in code, not by the prompt. The
    Commander and the Investigators get the diagnostic tools without
    runbook_lookup, so no runbook can steer a diagnosis; the Fix Advisor gets
    runbook_lookup only. Shares the underlying session (one server per incident).
    """

    def __init__(self, client, allowed, hidden=frozenset()):
        self._client, self.allowed = client, tuple(allowed)
        self.hidden = frozenset(hidden)  # telemetry kinds withheld; every narrower view inherits it
        self.tools = [t for t in client.tools if t.name in self.allowed]
        self.names = [t.name for t in self.tools]
        self.diagnostic = [n for n in self.names if n not in BRANCH_TOOLS]

    def call(self, name: str, args: dict):
        if name not in self.names:
            raise ToolError(f"tool {name!r} is not available to this role (allowed: {self.names})")
        return self._client.call(name, args)

    def describe(self) -> str:
        return _describe(self.tools)

    def scoped(self, allowed) -> "ScopedToolClient":
        return ScopedToolClient(self._client, [a for a in allowed if a in self.names], self.hidden)


def without_kinds(client, hide) -> ScopedToolClient:
    """The incident with some telemetry kinds missing (--hide): a view of `client`
    without the tools that read them. Every role's view is cut from this one
    (scoped() only narrows), so no role on any path can reach a hidden kind.
    Runbook and branch tools stay."""
    unknown = sorted(set(hide) - set(KIND_TOOLS))
    if unknown:
        raise ValueError(f"cannot hide {unknown}; telemetry kinds are {sorted(KIND_TOOLS)}")
    gone = {t for k in hide for t in KIND_TOOLS[k]}
    return ScopedToolClient(client, [n for n in client.names if n not in gone],
                            frozenset(hide) | client.hidden)


def main() -> None:
    p = argparse.ArgumentParser(prog="incident_commander.mcp_server",
                                description="Serve one incident's tools over MCP stdio.")
    p.add_argument("--case", required=True)
    p.add_argument("--system", default="online-boutique")
    args = p.parse_args()
    build_server(loaders.load_case(args.case, args.system)).run()  # stdio


if __name__ == "__main__":
    main()
