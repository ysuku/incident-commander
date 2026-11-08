"""Dev-set eval runner: run the agent over many labeled cases and score it.

Scoring (per case, against the folder-name label; the agent never sees it):
  correct        diagnosed, right service AND right fault type
  service_only   diagnosed, right service, wrong fault type
  wrong          diagnosed, wrong service (the dangerous outcome: confident and wrong)
  escalated      handed to a human with findings (honest, but not a diagnosis)
  error          the run crashed (API error etc.); not counted as an agent outcome

With ToT (--tot), an escalation can still carry a useful answer: a tie that
includes the true fault ("mem or disk"), or ranked branches with the true fault
first. Those are tracked in the `tot` column, not counted as correct.

Each case's brief is saved as it finishes, so an interrupted run resumes where it
stopped (same --out dir) and a spend cap (--max-usd) can stop it safely.
"""
from __future__ import annotations

import csv
import json
import logging
import time
import traceback
from collections import Counter
from pathlib import Path

from . import loaders, loop
from .loop import _norm

log = logging.getLogger("incident_commander")
OUTCOMES = ("correct", "service_only", "wrong", "escalated", "error")


def score(case: loaders.IncidentCase, brief: dict) -> str:
    if brief.get("status") == "error":
        return "error"
    if brief.get("status") != "diagnosed":
        return "escalated"
    rc = brief["root_cause"]
    if _norm(rc.get("service")) != _norm(case.root_cause_service):
        return "wrong"
    return "correct" if str(rc.get("fault_type", "")).lower() == case.fault_type else "service_only"


def _row(case, brief: dict, outcome: str) -> dict:
    rc = brief.get("root_cause") or {}
    u = brief.get("usage") or {}
    return {
        "case_id": case.case_id, "truth_service": case.root_cause_service,
        "truth_fault": case.fault_type, "status": brief.get("status"),
        "pred_service": rc.get("service", ""), "pred_fault": rc.get("fault_type", ""),
        "outcome": outcome, "steps": len(brief.get("investigation") or []),
        "model_calls": u.get("model_calls", 0), "wall_seconds": u.get("wall_seconds", 0),
        "prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
        "cost_usd": round(u.get("cost_usd", 0.0), 4),
        "reason": brief.get("reason", brief.get("error", "")),
        "tot": tot_outcome(case, brief),
        "arch": brief.get("architecture", "linear"),
        "fix_flags": len((brief.get("proposed_fix") or {}).get("contradictions") or []),
    }


def tot_outcome(case, brief: dict) -> str:
    """'' (ToT not run) | selected | tie_with_truth | tie_without_truth |
    escalated_truth_first | escalated (service compared too)."""
    t = brief.get("tot")
    if not t:
        return ""
    if t["status"] == "selected":
        return "selected"
    svc = lambda b: _norm(b["service"]) == _norm(case.root_cause_service)
    if t["status"] == "tied":
        hit = any(svc(b) and b["fault_type"] == case.fault_type for b in t["tied"])
        return "tie_with_truth" if hit else "tie_without_truth"
    live = [b for b in t["ranked"] if b["status"] != "pruned"]
    first = live and svc(live[0]) and live[0]["fault_type"] == case.fault_type
    return "escalated_truth_first" if first else "escalated"


def diagnose(case, model, tot: str | None = None, arch: str = "linear") -> dict:
    """Dispatch on architecture: the linear 4.1 agent, or the 5.1 graph."""
    if arch == "graph":
        from . import graph
        return graph.diagnose(case, model, tot=tot or "model")
    return loop.diagnose(case, model, tot=tot)


def run_eval(cases: list, make_model, out_dir: Path, max_usd: float | None = None,
             tot: str | None = None, arch: str = "linear") -> list[dict]:
    """Run each case (skipping ones already saved in out_dir); returns all rows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, spent = [], 0.0
    for i, case in enumerate(cases, 1):
        path = out_dir / f"{case.case_id.replace('/', '_')}.json"
        if path.exists():
            brief = json.loads(path.read_text())
        else:
            if max_usd is not None and spent >= max_usd:
                log.info("STOP: spend cap $%.2f reached ($%.2f); %d cases left", max_usd, spent,
                         len(cases) - i + 1)
                break
            log.info("=== [%d/%d] %s", i, len(cases), case.case_id)
            model = make_model()
            t0 = time.monotonic()
            try:
                brief = diagnose(case, model, tot=tot, arch=arch)
            except Exception as e:  # keep going; the row records it
                brief = {"status": "error", "error": f"{type(e).__name__}: {e}",
                         "traceback": traceback.format_exc(),
                         "usage": {"wall_seconds": round(time.monotonic() - t0, 1)}}
            brief.setdefault("usage", {})["cost_usd"] = getattr(model, "cost_usd", 0.0)
            path.write_text(json.dumps(brief, indent=2, default=str), encoding="utf-8")
        outcome = score(case, brief)
        row = _row(case, brief, outcome)
        spent += row["cost_usd"]
        rows.append(row)
        log.info("    %s -> %s (pred %s/%s) $%.3f | total $%.2f", case.case_id, outcome,
                 row["pred_service"] or "-", row["pred_fault"] or "-", row["cost_usd"], spent)
    write_summary(rows, out_dir)
    return rows


def write_summary(rows: list[dict], out_dir: Path) -> str:
    if rows:
        with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    n = len(rows)
    c = Counter(r["outcome"] for r in rows)
    scored = n - c["error"]
    pct = lambda k: f"{100 * c[k] / scored:.0f}%" if scored else "-"
    lines = [f"# Eval summary: {out_dir.name}", "",
             f"{n} cases run, {scored} scored ({c['error']} errors).", "",
             "| Outcome | Count | Share of scored |", "|---|---|---|"]
    lines += [f"| {k} | {c[k]} | {pct(k) if k != 'error' else '-'} |" for k in OUTCOMES]
    cost = sum(r["cost_usd"] for r in rows)
    secs = sum(r["wall_seconds"] or 0 for r in rows)
    lines += ["", f"Total cost ${cost:.2f} (${cost / max(n, 1):.3f}/case); "
                  f"wall time {secs / 60:.0f} min ({secs / max(n, 1):.0f}s/case).", "",
              "## By fault type", "", "| Fault | n | correct | service_only | wrong | escalated |",
              "|---|---|---|---|---|---|"]
    for fault in sorted({r["truth_fault"] for r in rows}):
        fc = Counter(r["outcome"] for r in rows if r["truth_fault"] == fault)
        lines.append(f"| {fault} | {sum(fc.values())} | {fc['correct']} | {fc['service_only']} | "
                     f"{fc['wrong']} | {fc['escalated']} |")
    tc = Counter(r.get("tot") for r in rows if r.get("tot"))
    if tc:
        lines += ["", "## Tree-of-Thought", "",
                  f"ToT ran in {sum(tc.values())} of {n} cases.", "", "| ToT result | n |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in tc.most_common()]
    md = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    return md
