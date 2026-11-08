"""Agent roles (Checkpoint 5.1): who may do what.

Three LLM roles, each with a goal, a prompt and an allowlist of tools that the
MCP client enforces in code (mcp_server.ScopedToolClient), never the prompt:

  Commander     orients on the alert, picks the suspect, runs the linear loop,
                writes the brief. Diagnostic tools only.
  Investigator  one per live hypothesis branch; proposes one discriminating
                test. Diagnostic tools only, plus read access to its branch.
  Fix Advisor   runs only after the citation gate has accepted a diagnosis;
                retrieves runbooks and drafts a conditional fix. runbook_lookup
                only, so the diagnosis can never have seen a runbook.

The Critic and the Citation gate are code (signatures.py, loop._gate), and the
human approves every fix (graph.human_approval). Roles are plain specs on
purpose: the 5.1 plan allowed dropping CrewAI if it added nothing over this, and
it would also pin an older pydantic than the MCP SDK uses. A CrewAI Agent(role,
goal, tools) can be generated from a Role if that changes.
"""
from __future__ import annotations

from dataclasses import dataclass

DIAGNOSTIC_TOOLS = ("metric_query", "log_search", "trace_lookup", "service_map")
RUNBOOK_TOOLS = ("runbook_lookup",)


@dataclass(frozen=True)
class Role:
    name: str
    goal: str
    tools: tuple[str, ...]  # allowlist; enforced by ScopedToolClient

    def view(self, toolbox):
        """This role's restricted view of the incident's MCP server."""
        return toolbox.scoped(self.tools)


COMMANDER = Role(
    "Commander",
    "Diagnose the incident from telemetry alone: pick the suspect service, gather two "
    "independent telemetry kinds that implicate it, name the fault type, write the brief.",
    DIAGNOSTIC_TOOLS)

INVESTIGATOR = Role(
    "Investigator",
    "Test ONE hypothesis: propose the single read-only check that best separates it from "
    "its rival, and predict the result. Never see the other branches' reasoning.",
    DIAGNOSTIC_TOOLS)

FIX_ADVISOR = Role(
    "Fix Advisor",
    "Given an accepted, cited diagnosis, retrieve the relevant runbooks and draft a "
    "conditional fix for human approval; flag any runbook step the evidence contradicts.",
    RUNBOOK_TOOLS)

ROLES = (COMMANDER, INVESTIGATOR, FIX_ADVISOR)
