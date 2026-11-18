"""Offline re-gate: what the CURRENT code would do with the tool calls and the
finish that SAVED runs made. No model calls, so it costs $0.

For every saved brief in a results folder:
  1. rebuild the list of tool calls behind its evidence board (the Commander's
     tool_call steps, the ToT tests, and the metric signatures ToT's root and the
     T1 check fetch without a trace step: those are read off the saved summary);
  2. re-execute the same calls, in order, against the on-disk data through the
     current tools and the Commander's scoped client, then through the current
     loop._summarize, keeping the saved evidence ids so citations still line up;
  3. re-run the current loop._gate on the saved finish (service, fault type,
     cited ids), and on every finish the old gate passed on to ToT (T1).

It reports, per run: tool calls that worked then and are refused now (by rule),
evidence whose implicated services or wording changed, diagnosed briefs the
gate would now refuse (by rule), the outcome counts if a refused brief became
an escalation, and, for briefs the gate still accepts, which fields of the
brief it now writes differently (root cause, telemetry kinds, rejected
alternatives, proposed fix, confidence).

What this is NOT: a re-run. The model's actions are held fixed, so a refused
call or finish is counted once and the steps a model would take next are not
simulated. "refused => escalated" is therefore the pessimistic bound; a real run
has steps left to try again.

  python scripts/regate_offline.py                       # the four reference runs
  python scripts/regate_offline.py 2026-11-06_qwen3.6-27b_r1_v4_linear --details
  python scripts/regate_offline.py --out evals/regate_offline.md

--src replays against another checkout of the package instead (for example
`git archive <commit> src | tar -x -C /tmp/old` plus a `data` symlink next to
`src`): on the code a run was made with, the table should show nothing refused.
"""
import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS = ("2026-11-05_opus-5-5_all_v4_linear", "2026-11-05_opus-5-5_all_v4_tot-model",
                "2026-11-06_qwen3.6-27b_r1_v4_linear", "2026-11-08_qwen3.6-27b_r1_v6_tot-model")
OUTCOMES = ("correct", "service_only", "wrong", "escalated")
CALL_RULES = ("service has no log lines", "unknown log service", "'|' in query", "short fragment", "unknown argument",
              "unknown name", "unknown log level", "not on Commander allowlist", "other")
GATE_RULES = ("cited id gone", "unknown fault type", "shared cause", "two kinds unmet", "other")
# what the gate writes into a diagnosed brief; the last two exist only in the model's own finish
GATE_FIELDS = ("root_cause", "telemetry_kinds", "alternatives_rejected", "proposed_fix")


def _num(eid: str) -> int:
    return int(eid[1:]) if eid[1:].isdigit() else 0


def saved_calls(brief: dict) -> tuple[list[dict], list[str]]:
    """The tool call behind each entry of the saved evidence board, in board order:
    ([{eid, tool, args, by}], eids that could not be rebuilt)."""
    by_eid = {}
    for e in brief.get("investigation", []):
        action, eid = e.get("action") or {}, e.get("observed")
        if eid and eid not in by_eid and e.get("outcome") in ("tool_call", "tot_test"):
            by_eid[eid] = {"eid": eid, "tool": action.get("tool"), "args": action.get("args") or {},
                           "by": "tot" if e.get("phase") == "tot" else "commander"}
    calls, lost = [], []
    for ev in brief.get("evidence_board", []):
        call = by_eid.get(ev["eid"])
        if call is None:
            # no trace step: a metric signature fetched in code (ToT root, T1 check)
            m = re.search(r"in scope \[service=([\w-]+)\]", ev["summary"])
            if ev["kind"] != "metrics" or not m:
                lost.append(ev["eid"])
                continue
            call = {"eid": ev["eid"], "tool": "metric_query", "args": {"service": m.group(1)}, "by": "code"}
        calls.append({**call, "saved": ev})
    return calls, lost


def call_rule(tool: str, args: dict, error: str) -> str:
    """Which input rule refused a call that used to succeed."""
    if "unknown argument" in error:
        return "unknown argument"
    if "not available to this role" in error:
        return "not on Commander allowlist"
    if "literal substring" in error:
        return "'|' in query"
    if "unknown level" in error:
        return "unknown log level"
    if tool == "log_search" and "no log lines from" in error:
        return "service has no log lines"
    if tool == "log_search" and "unknown service" in error:
        return "unknown log service"
    if tool == "metric_query" and ("unknown service" in error or "unknown metric" in error):
        given = str(args.get("metric" if "unknown metric" in error else "service") or "").strip()
        return "short fragment" if len(given) < 3 else "unknown name"
    return "other"


def gate_rule(problem: str) -> str:
    if "do not exist" in problem:
        return "cited id gone"
    if "not a known fault type" in problem:
        return "unknown fault type"
    if "shared cause" in problem:
        return "shared cause"
    if "two independent kinds" in problem:
        return "two kinds unmet"
    return "other"


def change_kind(call: dict, saved: dict) -> str:
    """Where an evidence entry's implicated services changed: a short label."""
    tool, args = call["tool"], call["args"]
    if tool == "log_search":
        if not saved["implicates"]:
            return "logs: now implicates (name spelling matched)"
        return "logs: info lines (no warning/error level)" if not args.get("level") else "logs: other"
    if tool == "trace_lookup" and not args.get("service"):
        return "traces: path-level lookup (implicates nobody now)"
    return f"{tool}: other"


def replay(case, brief: dict, ic) -> dict:
    """Re-execute one saved brief's tool calls and re-gate its finish. `ic` holds
    the package modules (so --src can swap the code under test)."""
    loop, mcp, roles = ic["loop"], ic["mcp_server"], ic["roles"]
    Evidence, Scratchpad = ic["scratchpad"].Evidence, ic["scratchpad"].Scratchpad
    calls, lost = saved_calls(brief)
    pad = Scratchpad(alert=case.case_id)
    out = {"calls": len(calls), "lost": lost, "rejected": [], "repeats": [], "implicates_changed": [],
           "summary_changed": Counter(), "gate": None, "t1_refused": [], "brief_changed": [],
           "pad": pad, "observations": {}}  # the rebuilt board, for further offline probes
    call_key = getattr(loop, "_call_key", lambda tb, n, a: (n, json.dumps(a, sort_keys=True)))
    seen = {}
    with mcp.ToolClient(mcp.build_server(case)) as client:
        box = client.scoped(roles.COMMANDER.tools + mcp.BRANCH_TOOLS)  # as loop._investigate builds it
        for c in calls:
            saved = c["saved"]
            try:
                key = call_key(box, c["tool"], c["args"])
                if key in seen and c["by"] == "commander":
                    # the Commander would be refused this as a repeat; the evidence is
                    # already on the board under the earlier id, so keep it citable
                    out["repeats"].append((c["eid"], seen[key], c["tool"], c["args"]))
                result = box.call(c["tool"], c["args"])
            except (TypeError, ValueError) as e:
                out["rejected"].append((c["eid"], c["tool"], c["args"], call_rule(c["tool"], c["args"], str(e)),
                                        c["by"], str(e)[:160]))
                continue
            seen.setdefault(key, c["eid"])
            kind, summary, ref, implicated = loop._summarize(c["tool"], result, c["args"])
            pad.evidence.append(Evidence(c["eid"], kind, summary, ref, list(implicated)))
            out["observations"][c["eid"]] = (c["tool"], c["args"], result)
            if set(implicated) != set(saved["implicates"]):
                out["implicates_changed"].append((c["eid"], change_kind(c, saved), saved["implicates"],
                                                  list(implicated)))
            if summary != saved["summary"]:
                zero = c["tool"] == "log_search" and saved["summary"].startswith("0 matching")
                out["summary_changed"]["logs: zero-match wording" if zero else f"{c['tool']}: other"] += 1

    def gate(finish: dict, upto: int | None = None):
        view = pad if upto is None else Scratchpad(
            alert=pad.alert, evidence=[e for e in pad.evidence if _num(e.eid) <= upto])
        try:
            return loop._gate(view, finish)
        except Exception as e:  # the old gate crashed on some malformed finishes
            return {}, f"gate raised {type(e).__name__}: {e}"

    # finishes the old gate accepted and handed to ToT (T1): would they still get there?
    high = 0
    for e in brief.get("investigation", []):
        if e.get("outcome") == "finish_ambiguous" and isinstance((e.get("action") or {}).get("finish"), dict):
            if problem := gate(e["action"]["finish"], high)[1]:
                out["t1_refused"].append((e["step"], gate_rule(problem), problem[:200]))
        high = max(high, _num(e.get("observed") or "E0"))
    if brief.get("status") == "diagnosed":
        last = (brief["investigation"][-1].get("action") or {}) if brief.get("investigation") else {}
        rc = brief["root_cause"]
        # the model's own finish when the Commander decided; ToT's choice otherwise
        own = brief.get("decided_by") != "tot" and isinstance(last.get("finish"), dict)
        finish = last["finish"] if own else {
            "root_cause_service": rc["service"], "fault_type": rc["fault_type"],
            "supporting_evidence": [e["eid"] for e in brief["supporting_evidence"]]}
        regated, problem = gate(finish)
        out["gate"] = (gate_rule(problem), problem) if problem else None
        if not problem:
            # still accepted: does the gate write the same brief? (ToT's finish is
            # rebuilt here without its fix, so only the first two fields compare)
            out["brief_changed"] = [(k, brief.get(k), regated.get(k))
                                    for k in (GATE_FIELDS if own else GATE_FIELDS[:2])
                                    if regated.get(k) != brief.get(k)]
            # confidence is "high" only while the board has no notes; a refused
            # call or a refused repeat leaves one (inferred: notes are not replayed)
            if brief.get("confidence") == "high" and (out["rejected"] or out["repeats"]):
                out["brief_changed"].append(("confidence", "high", "medium"))
    return out


def run_table(name: str, folder: Path, cases: dict, ic, details: bool) -> tuple[dict, list[str]]:
    """Replay one results folder: (summary row, detail lines)."""
    score = ic["evaluate"].score
    row = {"run": name, "briefs": 0, "diagnosed": 0, "calls": 0, "lost": 0, "old": Counter(), "new": Counter(),
           "rejected": Counter(), "refused": Counter(), "repeats": 0, "impl": Counter(), "summ": Counter(),
           "t1": 0, "t1_total": 0, "rejected_briefs": 0, "fields": Counter(), "accepted": 0, "accepted_changed": 0}
    lines = []
    for path in sorted(folder.glob("*.json")):
        brief = json.loads(path.read_text())
        case = cases.get(path.stem)
        if case is None or "status" not in brief or brief["status"] == "error":
            continue
        r = replay(case, brief, ic)
        old = score(case, brief)
        new = "escalated" if r["gate"] else old
        row["briefs"] += 1
        row["diagnosed"] += brief["status"] == "diagnosed"
        row["calls"] += r["calls"]
        row["lost"] += len(r["lost"])
        row["old"][old] += 1
        row["new"][new] += 1
        row["repeats"] += len(r["repeats"])
        row["rejected"].update(x[3] for x in r["rejected"])
        row["rejected_briefs"] += bool(r["rejected"])
        row["impl"].update(x[1] for x in r["implicates_changed"])
        row["summ"].update(r["summary_changed"])
        row["t1"] += bool(r["t1_refused"])
        row["t1_total"] += any(e.get("outcome") == "finish_ambiguous" for e in brief.get("investigation", []))
        if r["gate"]:
            row["refused"][r["gate"][0]] += 1
            lines.append(f"- REFUSED `{case.case_id}` (was {old}; {r['gate'][0]}): {r['gate'][1]}")
        elif brief["status"] == "diagnosed":
            row["accepted"] += 1
            row["accepted_changed"] += bool(r["brief_changed"])
            row["fields"].update(k for k, _, _ in r["brief_changed"])
            for k, was, now in r["brief_changed"]:
                lines.append(f"- brief field `{case.case_id}` (still accepted) {k}: "
                             f"{json.dumps(was)} -> {json.dumps(now)}")
        for step, rule, problem in r["t1_refused"]:
            lines.append(f"- T1 finish refused `{case.case_id}` step {step} ({rule}; brief was "
                         f"{brief['status']}): {problem}")
        for eid, tool, args, rule, by, err in r["rejected"]:
            lines.append(f"- call refused `{case.case_id}` {eid} {tool}({json.dumps(args)}) by {by}: "
                         f"{rule}: {err}")
        if details:
            for eid, first, tool, args in r["repeats"]:
                lines.append(f"- repeat `{case.case_id}` {eid} {tool}({json.dumps(args)}) = {first}")
            for eid, why, was, now in r["implicates_changed"]:
                lines.append(f"- implicates `{case.case_id}` {eid} ({why}): {was} -> {now}")
    return row, lines


def _counts(c: Counter, order=()) -> str:
    keys = [k for k in order if c[k]] + sorted(k for k in c if k not in order and c[k])
    # only used inside table cells: a bare '|' (the query rule's name) would split the cell
    return (", ".join(f"{k} {c[k]}" for k in keys) or "0").replace("|", "\\|")


def report(rows: list[dict], detail: dict[str, list[str]]) -> str:
    md = ["# Offline re-gate of saved runs on the current code", "",
          "Saved tool calls re-executed and saved finishes re-gated; no model calls. "
          "\"After\" assumes every refused brief becomes an escalation (the pessimistic bound).", "",
          "| Run | Briefs | Diagnosed | Now refused by the gate (rule) | Tool calls replayed | "
          "Calls now refused (rule) | Briefs with a refused call | correct | service_only | wrong | escalated |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        n = sum(r["refused"].values())
        shift = [f"{r['old'][k]} -> {r['new'][k]}" for k in OUTCOMES]
        md.append(f"| {r['run']} | {r['briefs']} | {r['diagnosed']} | {n}"
                  + (f" ({_counts(r['refused'], GATE_RULES)})" if n else "")
                  + f" | {r['calls']} | {sum(r['rejected'].values())}"
                  + (f" ({_counts(r['rejected'], CALL_RULES)})" if r["rejected"] else "")
                  + f" | {r['rejected_briefs']} | " + " | ".join(shift) + " |")
    md += ["", "## What else moved on the board", "",
           "| Run | Evidence whose implicated services changed | Summaries whose wording changed | "
           "Commander calls that are now repeats | T1 finishes now refused before ToT | "
           "Still-accepted briefs the gate writes differently (field) | "
           "Board entries not rebuilt |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['run']} | {sum(r['impl'].values())} ({_counts(r['impl'])}) | "
                  f"{sum(r['summ'].values())} ({_counts(r['summ'])}) | {r['repeats']} | "
                  f"{r['t1']} of {r['t1_total']} | {r['accepted_changed']} of {r['accepted']}"
                  + (f" ({_counts(r['fields'])})" if r["fields"] else "") + f" | {r['lost']} |")
    for r in rows:
        md += ["", f"## {r['run']}", ""] + (detail[r["run"]] or ["nothing refused"])
    return "\n".join(md) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("runs", nargs="*", help="results folders (names under evals/results/ or paths); "
                                           "default: the four reference runs")
    p.add_argument("--system", default="online-boutique")
    p.add_argument("--details", action="store_true",
                   help="also list every repeat and every evidence entry whose implicated services changed")
    p.add_argument("--out", help="also write the report to this file")
    p.add_argument("--src", help="replay against the package in this folder instead (see module docstring)")
    args = p.parse_args()
    sys.path.insert(0, args.src or str(ROOT / "src"))
    import importlib
    ic = {m: importlib.import_module(f"incident_commander.{m}")
          for m in ("loop", "mcp_server", "roles", "scratchpad", "evaluate", "loaders", "config")}
    if args.system == getattr(ic["config"], "HELDOUT_SYSTEM", "train-ticket"):
        p.error(f"{args.system} is held out; this dev check must not open it")
    logging.getLogger("incident_commander").setLevel(logging.WARNING)
    try:
        cases = {c.case_id.replace("/", "_"): c for c in ic["loaders"].list_cases(args.system)}
    except FileNotFoundError as e:
        raise SystemExit(f"the recorded data is needed for a replay: {e}")
    rows, detail = [], {}
    for name in args.runs or DEFAULT_RUNS:
        folder = Path(name) if Path(name).is_dir() else ROOT / "evals" / "results" / name
        if not folder.is_dir():
            raise SystemExit(f"no results folder {name!r}")
        row, lines = run_table(folder.name, folder, cases, ic, args.details)
        rows.append(row)
        detail[folder.name] = lines
        print(f"... replayed {folder.name}: {row['briefs']} briefs, {row['calls']} tool calls", file=sys.stderr)
    md = report(rows, detail)
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"... wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
