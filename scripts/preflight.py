#!/usr/bin/env python3
"""Free pre-flight for an eval run: can every tool read every case?

Calls the four telemetry tools on each case with the arguments an investigation
starts with, plus the per-service views for one service taken from the case's
own metric names. No model is called, so it costs $0. It prints pass/fail and
the exception only: no telemetry values, no service ranking, nothing that could
steer a design decision. That makes it safe to run on the held-out system right
before its one-shot run (IC_ALLOW_HELDOUT=1 is still required): a layout the
loaders cannot read is found before any money is spent, instead of turning 90
paid cases into error rows.

  python scripts/preflight.py --system online-boutique --runs 1
  IC_ALLOW_HELDOUT=1 python scripts/preflight.py --system train-ticket --runs 1

Exit code 0 when every call on every case succeeded, 1 otherwise.
"""
import argparse
import json
import sys
import time
from collections import Counter

from incident_commander import loaders, tools


def checks(case):
    """(name, call) pairs. The service is the first one the metrics name, in
    file order: no ground truth and no ranking is read."""
    cols = [c for c in loaders.load_simple_metrics(case).columns if c != "time"]
    svc = cols[0].rpartition("_")[0] if cols else ""
    yield "service_map", lambda: tools.service_map(case)
    yield "metric_query", lambda: tools.metric_query(case)
    yield "metric_query(service)", lambda: tools.metric_query(case, service=svc)
    yield "log_search", lambda: tools.log_search(case)
    yield "log_search(level=error)", lambda: tools.log_search(case, level="error")
    yield "trace_lookup", lambda: tools.trace_lookup(case)
    yield "trace_lookup(service)", lambda: _service_traces(case)


def _service_traces(case):
    """Per-service trace view for the first service the traces name."""
    names = loaders.load_traces(case, columns=["serviceName"])["serviceName"].dropna()
    if names.empty:
        raise ValueError("traces.csv names no service")
    return tools.trace_lookup(case, service=str(names.iloc[0]))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--system", default="online-boutique")
    p.add_argument("--runs", default="1", help="comma list of repetitions, e.g. '1' or '1,2,3'")
    args = p.parse_args()
    runs = set(args.runs.split(","))
    cases = [c for c in loaders.list_cases(args.system) if c.run in runs]
    print(f"{args.system}: {len(cases)} cases at runs {sorted(runs)}")
    failures, t0 = Counter(), time.time()
    for case in cases:
        bad = []
        try:
            for name, call in checks(case):
                try:
                    json.dumps(call(), default=str)      # a result the MCP layer can serialize
                except tools.Refusal as e:               # a refusal is an answer, not a crash
                    bad.append(f"{name}: refused: {str(e)[:120]}")
                except Exception as e:
                    bad.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
        except Exception as e:                            # the metrics file itself cannot be read
            bad.append(f"metrics file: {type(e).__name__}: {str(e)[:160]}")
        for b in bad:
            failures[b.split(":")[0]] += 1
        print(f"  {case.case_id}: " + ("ok" if not bad else "FAIL | " + " | ".join(bad)), flush=True)
    print(f"\n{len(cases)} cases checked in {time.time() - t0:.0f}s; "
          + ("every tool read every case" if not failures else f"failures by tool: {dict(failures)}"))
    return 1 if failures or not cases else 0


if __name__ == "__main__":
    sys.exit(main())
