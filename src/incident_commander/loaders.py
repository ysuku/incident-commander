"""Loaders for RCAEval failure cases.

An RCAEval case bundles telemetry recorded around one injected fault:
logs, metrics, traces, plus the labeled root cause (service + fault type).
These loaders are the only code that touches raw files; everything else
(tools, agents) goes through `load_case`.
"""
from dataclasses import dataclass, field
from pathlib import Path

from .config import RCAEVAL_DIR


@dataclass
class IncidentCase:
    case_id: str
    system: str                 # e.g. "online-boutique"
    fault_type: str             # labeled ground truth (hidden from the agent)
    root_cause_service: str     # labeled ground truth (hidden from the agent)
    paths: dict = field(default_factory=dict)  # kind -> Path (logs/metrics/traces)


def list_cases(system: str) -> list[IncidentCase]:
    """Enumerate downloaded cases for one system. TODO(M2): implement against
    the actual RCAEval layout once scripts/download_data.py has run."""
    root = RCAEVAL_DIR / system
    if not root.exists():
        raise FileNotFoundError(
            f"No data at {root}. Run: python scripts/download_data.py --dataset re2 --system {system}"
        )
    raise NotImplementedError("Implement after inspecting the downloaded RCAEval layout.")


def load_case(case_id: str, system: str) -> IncidentCase:
    """Load one case's telemetry paths. TODO(M2)."""
    raise NotImplementedError
