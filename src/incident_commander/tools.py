"""The five read-only diagnostic tools (Checkpoint 2.1 design).

Rules that apply to every tool:
- Read-only: tools observe the recorded incident, never change anything.
- Summaries, not dumps: each tool returns top matches / aggregates sized for a
  free model's context window, never raw telemetry.
- Citable: every returned item carries a source reference (file + line/offset
  or metric name + window) so the agent can cite it in the incident brief.

These run behind one MCP server (FastMCP) from Module 2 onward; the functions
here are the implementations the server wraps.
"""
from .loaders import IncidentCase


def log_search(case: IncidentCase, query: str, service: str | None = None, limit: int = 20) -> list[dict]:
    """Search a case's logs; returns [{service, ts, line, source_ref}]. TODO(M2)."""
    raise NotImplementedError


def metric_query(case: IncidentCase, service: str, metric: str | None = None) -> dict:
    """Summary stats + anomaly windows for a service's metrics. TODO(M2)."""
    raise NotImplementedError


def trace_lookup(case: IncidentCase, failing_only: bool = True, limit: int = 10) -> list[dict]:
    """Traces parsed into call graphs; returns summarized failing/slow request
    paths (span: service, duration, status), never raw span dumps. TODO(M2)."""
    raise NotImplementedError


def service_map(case: IncidentCase) -> dict:
    """Service dependency graph {service: [downstream services]}, built from
    traces. TODO(M2)."""
    raise NotImplementedError


def runbook_lookup(symptom: str, k: int = 5) -> list[dict]:
    """Retrieve runbooks / past incidents matching a symptom. Keyword match in
    M2; Chroma + MMR in Module 3. TODO(M2/M3)."""
    raise NotImplementedError
