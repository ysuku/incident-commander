"""Offline ToT check: the full ToT path (controller, MCP branch tools, critic,
heuristic generator) over every dev case, with no model calls, so it costs $0.

The suspect service is given, which isolates the question ToT is for (fault
type, given the service). Two modes:
  --suspect truth   the labeled service: an upper bound for the fault question
  --suspect metric  the top non-diskio deviation of an unfiltered metric_query,
                    a label-free suspect (often a caller or the frontend)
Writes evals/tot_offline_<mode>.md.

  python scripts/tot_offline.py --suspect truth
"""
import argparse
import logging
from collections import Counter

from incident_commander import loaders, loop, tools
from incident_commander import tot as tot_mod
from incident_commander.config import REPO_ROOT
from incident_commander.mcp_server import ToolClient, build_server
from incident_commander.scratchpad import Scratchpad


def run_case(case, mode: str) -> dict:
    if mode == "truth":
        suspect = case.root_cause_service
    else:
        top = tools.metric_query(case, top=10)["top_deviations"]
        suspect = next(d["service"] for d in top if d["metric"] != "diskio")
    pad, seen, obs = Scratchpad(alert=case.case_id), {}, {}
    with ToolClient(build_server(case)) as tb:
        observe = loop.make_observer(pad, tb, seen, obs)
        ctx = tot_mod.TotContext(pad, tb, observe, obs, lambda n, a: False)
        r = tot_mod.run_tot(ctx, suspect, None, tot_mod.HeuristicGenerator(), f"offline/{mode}")
    truth = case.fault_type
    right_svc = loop._norm(suspect) == loop._norm(case.root_cause_service)
    if r["status"] == "selected":
        out = "correct" if right_svc and r["selected"]["fault_type"] == truth else (
            "service_only" if right_svc else "wrong")
    elif r["status"] == "tied":
        out = "tie_with_truth" if right_svc and truth in {b["fault_type"] for b in r["tied"]} else "tie_without_truth"
    else:
        live = [b for b in r["ranked"] if b["status"] != "pruned"]
        out = "escalated_truth_first" if right_svc and live and live[0]["fault_type"] == truth else "escalated"
    truth_pruned = any(b["fault_type"] == truth and b["status"] == "pruned" for b in r["ranked"])
    return {"case": case.case_id, "fault": truth, "suspect": suspect, "outcome": out,
            "expansions": r["expansions"], "truth_pruned": right_svc and truth_pruned,
            "ranked": "; ".join(f"{b['fault_type']}={b['score']}({b['status'][0]})" for b in r["ranked"])}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--suspect", choices=["truth", "metric"], default="truth")
    p.add_argument("--system", default="online-boutique")
    args = p.parse_args()
    logging.getLogger("incident_commander").setLevel(logging.WARNING)
    rows = [run_case(c, args.suspect) for c in loaders.list_cases(args.system)]
    order = ["correct", "service_only", "wrong", "tie_with_truth", "tie_without_truth",
             "escalated_truth_first", "escalated"]
    c = Counter(r["outcome"] for r in rows)
    lines = [f"# Offline ToT check (heuristic generator, suspect = {args.suspect})", "",
             f"{len(rows)} cases, no model calls. Outcome counts:", "",
             "| Outcome | n |", "|---|---|"] + [f"| {k} | {c[k]} |" for k in order if c[k]]
    lines += ["", f"Truth branch pruned: {sum(r['truth_pruned'] for r in rows)}; "
              f"mean expansions {sum(r['expansions'] for r in rows) / len(rows):.1f}", "",
              "## By fault type", "", "| Fault | " + " | ".join(order) + " |",
              "|---|" + "---|" * len(order)]
    for f in sorted({r["fault"] for r in rows}):
        fc = Counter(r["outcome"] for r in rows if r["fault"] == f)
        lines.append(f"| {f} | " + " | ".join(str(fc[k]) for k in order) + " |")
    lines += ["", "## Cases", "", "| Case | Suspect | Outcome | Branches (score, status) |", "|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['suspect']} | {r['outcome']} | {r['ranked']} |" for r in rows]
    md = "\n".join(lines) + "\n"
    out = REPO_ROOT / "evals" / f"tot_offline_{args.suspect}.md"
    out.write_text(md, encoding="utf-8")
    print("\n".join(lines[:30]))
    print(f"... wrote {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
