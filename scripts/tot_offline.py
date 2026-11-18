"""Offline ToT check: the full ToT path (controller, MCP branch tools, critic,
heuristic generator) over every dev case, with no model calls, so it costs $0.

The suspect service is given, which isolates the question ToT is for (fault
type, given the service). Two modes:
  --suspect truth   the labeled service: an upper bound for the fault question
  --suspect metric  the top non-diskio deviation of an unfiltered metric_query,
                    a label-free suspect (often a caller or the frontend)
Writes evals/tot_offline_<mode>.md (or into --out).

  python scripts/tot_offline.py --suspect truth
  python scripts/tot_offline.py --hide traces --out /tmp/ablation   # 6.1: one kind withheld

--hide withholds a telemetry kind exactly as `cli eval --hide` does (the tools
are absent from the client). The heuristic generator never searches logs, so
with traces hidden no branch can reach two kinds: this run shows what the critic
does with the gap, not what a model would recover from logs.
Cases labeled with a fault outside the six known types (RE3) are scored as
correct_unfamiliar (ToT said "unfamiliar" about the true service), escalated, or
wrong (a known type selected). On known-fault cases the report counts the
"unfamiliar" calls ToT made anyway (false unfamiliar): read the two together.
"""
import argparse
import logging
from collections import Counter

from pathlib import Path

from incident_commander import loaders, loop
from incident_commander import tot as tot_mod
from incident_commander.config import HELDOUT_SYSTEM, REPO_ROOT
from incident_commander.evaluate import KNOWN_FAULTS
from incident_commander.mcp_server import ToolClient, build_server, without_kinds
from incident_commander.scratchpad import Scratchpad


def run_case(case, mode: str, hide=()) -> dict:
    pad, seen, obs = Scratchpad(alert=case.case_id), {}, {}
    with ToolClient(build_server(case)) as client:
        tb = without_kinds(client, hide) if hide else client
        if mode == "truth":
            suspect = case.root_cause_service
        else:  # through the client, so a hidden kind is hidden here too
            top = tb.call("metric_query", {"top": 10})["top_deviations"]
            suspect = next(d["service"] for d in top if d["metric"] != "diskio")
        observe = loop.make_observer(pad, tb, seen, obs)
        ctx = tot_mod.TotContext(pad, tb, observe, obs, lambda n, a: False)
        r = tot_mod.run_tot(ctx, suspect, None, tot_mod.HeuristicGenerator(), f"offline/{mode}")
    truth = case.fault_type
    right_svc = loop._norm(suspect) == loop._norm(case.root_cause_service)
    if truth not in KNOWN_FAULTS:
        out = ("wrong" if r["status"] == "selected" else
               "correct_unfamiliar" if r.get("unfamiliar") and right_svc else "escalated")
    elif r["status"] == "selected":
        out = "correct" if right_svc and r["selected"]["fault_type"] == truth else (
            "service_only" if right_svc else "wrong")
    elif r["status"] == "tied":
        out = "tie_with_truth" if right_svc and truth in {b["fault_type"] for b in r["tied"]} else "tie_without_truth"
    else:
        live = [b for b in r["ranked"] if b["status"] != "pruned"]
        out = "escalated_truth_first" if right_svc and live and live[0]["fault_type"] == truth else "escalated"
    truth_pruned = any(b["fault_type"] == truth and b["status"] == "pruned" for b in r["ranked"])
    return {"case": case.case_id, "fault": truth, "suspect": suspect, "outcome": out,
            "unfamiliar": bool(r.get("unfamiliar")),
            "expansions": r["expansions"], "truth_pruned": right_svc and truth_pruned,
            "ranked": "; ".join(f"{b['fault_type']}={b['score']}({b['status'][0]})" for b in r["ranked"])}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--suspect", choices=["truth", "metric"], default="truth")
    p.add_argument("--system", default="online-boutique")
    p.add_argument("--hide", choices=["metrics", "logs", "traces"],
                   help="withhold one telemetry kind (6.1 robustness ablation)")
    p.add_argument("--runs", help="comma list of repetitions, e.g. '1' (default: all)")
    p.add_argument("--out", help="folder for the report (default: evals/)")
    args = p.parse_args()
    if args.system == HELDOUT_SYSTEM:
        # free, but it opens the held-out data: that would be the one look at it
        p.error(f"{HELDOUT_SYSTEM} is held out; this dev check must not open it")
    if args.hide == "metrics" and args.suspect == "metric":
        p.error("--suspect metric needs metrics; use --suspect truth with --hide metrics")
    logging.getLogger("incident_commander").setLevel(logging.WARNING)
    hide = (args.hide,) if args.hide else ()
    rows = [run_case(c, args.suspect, hide) for c in loaders.list_cases(args.system)
            if not args.runs or c.run in args.runs.split(",")]
    order = ["correct", "correct_unfamiliar", "service_only", "wrong", "tie_with_truth",
             "tie_without_truth", "escalated_truth_first", "escalated"]
    order = [k for k in order if k != "correct_unfamiliar" or any(r["outcome"] == k for r in rows)]
    c = Counter(r["outcome"] for r in rows)
    lines = [f"# Offline ToT check (heuristic generator, suspect = {args.suspect}"
             + (f", {args.hide} hidden" if args.hide else "") + ")", "",
             f"{len(rows)} cases, no model calls. Outcome counts:", "",
             "| Outcome | n |", "|---|---|"] + [f"| {k} | {c[k]} |" for k in order if c[k]]
    known = [r for r in rows if r["fault"] in KNOWN_FAULTS]
    lines += ["", f"Truth branch pruned: {sum(r['truth_pruned'] for r in rows)}; "
              f"mean expansions {sum(r['expansions'] for r in rows) / len(rows):.1f}", "",
              f"False unfamiliar (a known fault type called 'unfamiliar'): "
              f"{sum(r['unfamiliar'] for r in known)} of {len(known)} known-fault cases", "",
              "## By fault type", "", "| Fault | " + " | ".join(order) + " |",
              "|---|" + "---|" * len(order)]
    for f in sorted({r["fault"] for r in rows}):
        fc = Counter(r["outcome"] for r in rows if r["fault"] == f)
        lines.append(f"| {f} | " + " | ".join(str(fc[k]) for k in order) + " |")
    lines += ["", "## Cases", "", "| Case | Suspect | Outcome | Branches (score, status) |", "|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['suspect']} | {r['outcome']} | {r['ranked']} |" for r in rows]
    md = "\n".join(lines) + "\n"
    folder = Path(args.out) if args.out else REPO_ROOT / "evals"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / (f"tot_offline_{args.suspect}" + (f"_hide-{args.hide}" if args.hide else "") + ".md")
    out.write_text(md, encoding="utf-8")
    print("\n".join(lines[:30]))
    print(f"... wrote {out}")


if __name__ == "__main__":
    main()
