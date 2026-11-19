"""Loaders for RCAEval failure cases (RE2 layout, verified against RE2-OB).

Layout on disk:  data/rcaeval/RE2-OB/<service>_<fault>/<run>/
  inject_time.txt     unix timestamp of fault injection
  logs.csv            time, timestamp(ns), container_name, message, level, req_path, error, cluster_id, log_template
                      (RE3-OB: time, timestamp, container_name, message, pod_name, node_name; no level)
  logts.csv           log-template counts per 15s bucket; ids map via cluster_info.json
  simple_metrics.csv  wide: time + <service>_<metric> columns (cpu, mem, diskio, socket, ...)
  metrics.csv         full metrics (large)
  traces.csv          spans: traceID, spanID, serviceName, operationName, duration, statusCode, parentSpanID (large)
  tracets_lat.csv / tracets_err.csv   per-operation latency / error-rate time series

GROUND TRUTH is the folder name `<service>_<fault>`. It lives on IncidentCase
for scoring only — nothing that builds agent context may read those two fields.

This module is the only code that touches raw files; everything else sees
IncidentCase and the load_* helpers. Big files (logs, traces) load lazily and
only through helpers, so tools can summarize without dumping.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from .config import HELDOUT_SYSTEM, RCAEVAL_DIR

DATASET_BY_SYSTEM = {
    "online-boutique": "RE2-OB",
    "train-ticket": "RE2-TT",       # held-out: guarded below
    "online-boutique-re3": "RE3-OB",  # harder code-level faults (eval week)
}

NO_LEVEL = "unknown"   # log level for a dataset whose logs carry no severity (RE3)

CASE_FILES = {
    "inject_time": "inject_time.txt",
    "logs": "logs.csv",
    "log_timeseries": "logts.csv",
    "log_templates": "cluster_info.json",
    "metrics_simple": "simple_metrics.csv",
    "metrics_full": "metrics.csv",
    "traces": "traces.csv",
    "trace_latency": "tracets_lat.csv",
    "trace_errors": "tracets_err.csv",
}


@dataclass(frozen=True)
class IncidentCase:
    case_id: str          # "<service>_<fault>/<run>", e.g. "checkoutservice_cpu/1"
    system: str
    run: str
    inject_time: int      # unix seconds; the agent may know when the alert fired
    dir: Path
    # --- ground truth: scoring only, never into agent context ---
    root_cause_service: str
    fault_type: str

    def path(self, kind: str) -> Path:
        p = self.dir / CASE_FILES[kind]
        if not p.exists():
            raise FileNotFoundError(f"{self.case_id}: no {kind} file at {p}")
        return p


def _dataset_root(system: str) -> Path:
    if system == HELDOUT_SYSTEM and not os.environ.get("IC_ALLOW_HELDOUT"):
        raise RuntimeError(
            f"{system!r} is the held-out test system. It must not be opened while "
            "building; set IC_ALLOW_HELDOUT=1 only during the final evaluation run."
        )
    try:
        dataset = DATASET_BY_SYSTEM[system]
    except KeyError:
        raise ValueError(f"Unknown system {system!r}; one of {sorted(DATASET_BY_SYSTEM)}") from None
    root = RCAEVAL_DIR / dataset
    if not root.exists():
        raise FileNotFoundError(
            f"No data at {root}. Run: python scripts/download_data.py --match {dataset} "
            f"and unzip into data/rcaeval/"
        )
    return root


def _read_case(system: str, fault_dir: Path, run_dir: Path) -> IncidentCase:
    service, _, fault = fault_dir.name.rpartition("_")
    inject = int((run_dir / CASE_FILES["inject_time"]).read_text().strip())
    return IncidentCase(
        case_id=f"{fault_dir.name}/{run_dir.name}",
        system=system,
        run=run_dir.name,
        inject_time=inject,
        dir=run_dir,
        root_cause_service=service,
        fault_type=fault,
    )


def list_cases(system: str = "online-boutique") -> list[IncidentCase]:
    """All downloaded cases for one system, sorted by case_id."""
    root = _dataset_root(system)
    cases = [
        _read_case(system, fault_dir, run_dir)
        for fault_dir in sorted(root.iterdir()) if fault_dir.is_dir() and "_" in fault_dir.name
        for run_dir in sorted(fault_dir.iterdir(), key=lambda p: p.name)
        if run_dir.is_dir() and (run_dir / CASE_FILES["inject_time"]).exists()
    ]
    if not cases:
        raise FileNotFoundError(f"{root} exists but holds no cases — is the zip extracted?")
    return cases


def load_case(case_id: str, system: str = "online-boutique") -> IncidentCase:
    root = _dataset_root(system)
    fault_name, _, run = case_id.partition("/")
    run_dir = root / fault_name / (run or "1")
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No case {case_id!r} under {root}")
    return _read_case(system, root / fault_name, run_dir)


# --- lazy data helpers (pandas imported on use, so listing works without it) ---

def load_logs(case: IncidentCase, columns: list[str] | None = None):
    """Full log table — can be tens of MB; tools must filter/summarize, never dump.

    RE3 logs have no `level` column (time, timestamp, container_name, message,
    pod_name, node_name) and no severity in the message text. When `level` is
    asked for and the file has none, every line gets the level "unknown": the
    line counts stay true, a search for level="error" finds nothing and says the
    only level present is "unknown", and no line counts as a warning or error."""
    import pandas as pd
    path = case.path("logs")
    if columns and "level" in columns and "level" not in pd.read_csv(path, nrows=0).columns:
        df = pd.read_csv(path, usecols=[c for c in columns if c != "level"])
        df["level"] = NO_LEVEL
        return df[columns]
    return pd.read_csv(path, usecols=columns)


def load_simple_metrics(case: IncidentCase):
    """Wide per-service metrics (time + <service>_<metric> columns)."""
    import pandas as pd
    return pd.read_csv(case.path("metrics_simple"))


def load_traces(case: IncidentCase, columns: list[str] | None = None):
    """Full span table — the largest file; prefer load_trace_timeseries first."""
    import pandas as pd
    return pd.read_csv(case.path("traces"), usecols=columns)


def span_callees(df):
    """For each span, the service it calls (NaN when it calls no other service).

    Online Boutique client spans name their target in the operation
    ("hipstershop.EmailService/Send..." -> emailservice); when any span in the
    case does, that is the whole answer and nothing else is read, so Online
    Boutique results cannot change. A system whose operations name nobody (Train
    Ticket) falls back to the span graph: a span's callee is the service of its
    child spans in another service, when they all agree. A service that exports
    no spans of its own cannot be found that way. Needs columns spanID,
    serviceName, operationName, parentSpanID."""
    callee = (df["operationName"].str.extract(r"hipstershop\.(\w+?)Service/", expand=False)
              .str.lower() + "service")
    if callee.notna().any():
        return callee
    child = df[df["parentSpanID"].notna()]
    parent_svc = child["parentSpanID"].map(dict(zip(df["spanID"], df["serviceName"])))
    cross = child[parent_svc.notna() & (parent_svc != child["serviceName"])]
    by_parent = cross.groupby("parentSpanID")["serviceName"]
    return df["spanID"].map(by_parent.first()[by_parent.nunique() == 1])


def load_trace_timeseries(case: IncidentCase):
    """(latency_df, errors_df): per-operation time series — small and alert-like."""
    import pandas as pd
    return pd.read_csv(case.path("trace_latency")), pd.read_csv(case.path("trace_errors"))
