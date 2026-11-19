"""Dev-set eval runner: run the agent over many labeled cases and score it.

Scoring (per case, against the folder-name label; the agent never sees it):
  correct        diagnosed, right service AND right fault type
  service_only   diagnosed, right service, wrong fault type
  wrong          diagnosed, wrong service (the dangerous outcome: confident and wrong)
  escalated      handed to a human with findings (honest, but not a diagnosis)
  error          the run crashed (API error etc.); not counted as an agent outcome

Cases whose labeled fault is outside the six known types (the RE3 code-level
faults) are scored differently, because no diagnosis the agent can write is right:
  correct_unfamiliar  escalated with the true service as the lead hypothesis
                      (coded "unfamiliar" or not)
  escalated           any other escalation, "unfamiliar" on the wrong service included
  wrong               diagnosed: a confident claim of one of the six known types

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
import math
import re
import statistics
import time
import traceback
from collections import Counter
from pathlib import Path

from . import loaders, loop
from .config import HELDOUT_SYSTEM
from .loop import _norm

log = logging.getLogger("incident_commander")
OUTCOMES = ("correct", "service_only", "wrong", "escalated", "error")
# Same six as signatures.FAULT_TYPES (a test pins it); spelled out here so that
# scoring does not import the critic.
KNOWN_FAULTS = ("cpu", "mem", "disk", "socket", "delay", "loss")
UNFAMILIAR_OK = "correct_unfamiliar"
HELDOUT_STAMP = "heldout_complete.json"


def score(case: loaders.IncidentCase, brief: dict) -> str:
    if brief.get("status") == "error":
        return "error"
    if case.fault_type not in KNOWN_FAULTS:
        return _score_unfamiliar(case, brief)
    if brief.get("status") != "diagnosed":
        return "escalated"
    rc = brief["root_cause"]
    if _norm(rc.get("service")) != _norm(case.root_cause_service):
        return "wrong"
    return "correct" if str(rc.get("fault_type", "")).lower() == case.fault_type else "service_only"


def _score_unfamiliar(case, brief: dict) -> str:
    """A case whose fault is none of the six known types. The right answer is a
    handover, not a diagnosis: any diagnosis names a known fault type, so it is
    wrong whichever service it names (pred_service still records that). The
    handover must name the true service first. The "unfamiliar" code alone earns
    nothing: a wrong suspect has flat metrics too, and "no known type fits the
    wrong service" is not a correct handling of the incident."""
    if brief.get("status") == "diagnosed":
        return "wrong"
    return UNFAMILIAR_OK if _lead_service_is_truth(case, brief) else "escalated"


def _lead_service_is_truth(case, brief: dict) -> bool:
    """Does the escalation's FIRST hypothesis name the true service? Later ones do
    not count: a list of five services that happens to include it is not naming it."""
    hyps = (brief.get("escalation") or {}).get("hypotheses") or []
    if hyps:
        # an unfamiliar lead may name two services ("a or b"): the first one counts
        lead = str(hyps[0].get("service") or "").split(" or ")[0]
    else:  # briefs saved before the block existed: the first "svc: hypothesis" line
        m = re.match(r"\s*([A-Za-z][\w-]*)", str((brief.get("candidates") or [""])[0]))
        lead = m.group(1) if m else ""
    return bool(lead) and _norm(lead) == _norm(case.root_cause_service)


def _row(case, brief: dict, outcome: str) -> dict:
    rc = brief.get("root_cause") or {}
    u = brief.get("usage") or {}
    row = {
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
        # 6.1 metrics. Appended, so older CSVs keep their column order.
        "escalation_code": escalation_code(brief),
        "handover_truth": handover_truth(case, brief),
        # fix_flags is 0 both for "advisor found nothing" and "advisor never ran";
        # fix_advised tells them apart.
        "fix_advised": int("fix_advisor" in brief),
        "fix_dropped": len((brief.get("fix_advisor") or {}).get("dropped") or []),
        "model_seconds": u.get("model_seconds", ""),   # '' = not recorded in this brief
        "tool_seconds": u.get("tool_seconds", ""),
    }
    if brief.get("telemetry_hidden"):  # only under --hide: ordinary CSVs keep their columns
        row["hidden"] = ",".join(brief["telemetry_hidden"])
    return row


ESCALATION_CODES = ("budget_exhausted", "unparseable", "tot_tie", "tot_no_branch",
                    "tot_selected_gate_refused", "unfamiliar", "other")
# Prefixes of the free-text reasons written by loop.py / graph.py, so result
# folders saved before the structured block existed still aggregate.
_REASON_CODES = (("step budget", "budget_exhausted"), ("model output unparseable", "unparseable"),
                 ("tie:", "tot_tie"), ("tot selected", "tot_selected_gate_refused"),
                 ("unfamiliar", "unfamiliar"), ("tot: no branch", "tot_no_branch"))


def escalation_code(brief: dict) -> str:
    """Why the run was handed to a human, as one of ESCALATION_CODES; '' when it
    was not (diagnosed, or crashed: a crash is an error, not an escalation)."""
    if brief.get("status") in ("diagnosed", "error"):
        return ""
    code = (brief.get("escalation") or {}).get("code")
    if code:
        return code if code in ESCALATION_CODES else "other"
    t = brief.get("tot") or {}
    if t.get("unfamiliar") and not t.get("selected"):  # set by tot.close; same test as the block
        return "unfamiliar"
    # "Every branch pruned" has no reason string of its own (it shares the
    # no-branch text), so read it off the branches: nothing known fits.
    ranked = t.get("ranked") or []
    if t.get("status") == "escalated" and ranked and all(b["status"] == "pruned" for b in ranked):
        return "unfamiliar"
    reason = str(brief.get("reason", "")).lower()
    for prefix, code in _REASON_CODES:
        if reason.startswith(prefix):
            return code
    by_tot = {"tied": "tot_tie", "selected": "tot_selected_gate_refused", "escalated": "tot_no_branch"}
    return by_tot.get(t.get("status"), "other")


# Words a free-text candidate line may use for each fault label.
_FAULT_WORDS = {"cpu": "cpu", "mem": "mem|memory|oom", "disk": "disk|i/?o", "delay": "delay|latency",
                "loss": "loss|drop(?:s|ped)?", "socket": "sockets?|connections?"}


def handover_truth(case, brief: dict) -> str:
    """For an escalation: is the truth in what the human was handed? ''
    (not escalated) | tie_with_truth | tie_without_truth | top_live_is_truth |
    service_only (true service handed over, true fault not on top) | none.

    ToT escalations are scored on the tied or still-live branches, exactly. The
    full ranked list keeps pruned branches and holds the truth nearly always, so
    it would say nothing. Escalations without ToT only have the model's
    free-text candidates ("svc: hypothesis ..."): the service comes from the same
    regex loop.py uses, and the fault is a word match on the hypothesis, so
    top_live_is_truth is APPROXIMATE there."""
    if brief.get("status") in ("diagnosed", "error"):
        return ""
    svc = lambda name: _norm(name) == _norm(case.root_cause_service)
    t = brief.get("tot")
    if t:
        hit = lambda b: svc(b["service"]) and b["fault_type"] == case.fault_type
        if t["status"] == "tied":
            return "tie_with_truth" if any(hit(b) for b in t["tied"]) else "tie_without_truth"
        live = [b for b in t["ranked"] if b["status"] != "pruned"]
        if live and hit(live[0]):
            return "top_live_is_truth"
        return "service_only" if any(svc(b["service"]) for b in live) else "none"
    lines = [(m.group(1), c) for c in brief.get("candidates") or []
             if (m := re.match(r"\s*([A-Za-z][\w-]*)", str(c)))]
    if not any(svc(name) for name, _ in lines):
        return "none"
    name, line = lines[0]
    # the hypothesis is the text before the "(E1 cpu z=...)" evidence note,
    # which names other signals and would match almost any fault
    hypothesis = line.split("(")[0]
    words = _FAULT_WORDS.get(case.fault_type, re.escape(case.fault_type))
    named = re.search(rf"\b(?:{words})\b", hypothesis, re.I)
    return "top_live_is_truth" if svc(name) and named else "service_only"


def tot_outcome(case, brief: dict) -> str:
    """'' (ToT not run) | selected | selected_gate_refused | tie_with_truth |
    tie_without_truth | escalated_truth_first | escalated (service compared too)."""
    t = brief.get("tot")
    if not t:
        return ""
    if t["status"] == "selected":
        # ToT picked a branch but the citation gate refused it: that brief is an
        # escalation and must not be counted with the diagnosed selections.
        return "selected" if brief.get("status") == "diagnosed" else "selected_gate_refused"
    svc = lambda b: _norm(b["service"]) == _norm(case.root_cause_service)
    if t["status"] == "tied":
        hit = any(svc(b) and b["fault_type"] == case.fault_type for b in t["tied"])
        return "tie_with_truth" if hit else "tie_without_truth"
    live = [b for b in t["ranked"] if b["status"] != "pruned"]
    first = live and svc(live[0]) and live[0]["fault_type"] == case.fault_type
    return "escalated_truth_first" if first else "escalated"


def diagnose(case, model, tot: str | None = None, arch: str = "linear", hide=()) -> dict:
    """Dispatch on architecture: the linear 4.1 agent, or the 5.1 graph.
    hide: telemetry kinds to withhold (the 6.1 robustness ablation); empty = the
    ordinary run, on the ordinary code path. arch "plain" / "rag" are the non-agent
    baselines (baselines.py): one prompt, no tools, no gate."""
    from . import baselines
    if arch in baselines.KINDS:
        if tot or hide:
            raise ValueError(f"the {arch!r} baseline takes no --tot and no --hide")
        return baselines.diagnose(case, model, arch)
    if hide:
        return _diagnose_hidden(case, model, tot, arch, hide)
    if arch == "graph":
        from . import graph
        return graph.diagnose(case, model, tot=tot or "model")
    return loop.diagnose(case, model, tot=tot)


def _diagnose_hidden(case, model, tot, arch, hide) -> dict:
    """The same run on a view of the incident without the hidden kinds' tools."""
    from .mcp_server import KIND_TOOLS, ToolClient, build_server, without_kinds
    with ToolClient(build_server(case)) as client:
        view = without_kinds(client, hide)
        if arch == "graph":
            from . import graph
            brief = graph.diagnose(case, model, toolbox=view, tot=tot or "model")
        else:
            brief = loop.diagnose(case, model, toolbox=view, tot=tot)
    brief["telemetry_hidden"] = sorted(hide)
    if brief.get("confidence") == "high":  # a diagnosis made with a kind missing is not "high"
        brief["confidence"] = "medium"
    if "metrics" in hide and brief.get("status") == "diagnosed":
        # only the loop without ToT gets here (ToT never selects with metrics hidden):
        # the service passed the gate on logs + traces, the fault type is the model's word
        brief["fault_type_unchecked"] = ("metrics were hidden: no fault type's must-have signal "
                                         "could be checked, so the fault type named is a guess")
    gone = {t for k in hide for t in KIND_TOOLS[k]}
    if "roles" in brief:  # graph: list what each role could really call in this run
        brief["roles"] = {r: [t for t in ts if t not in gone] for r, ts in brief["roles"].items()}
    return brief


def _case_path(out_dir: Path, case) -> Path:
    return out_dir / f"{case.case_id.replace('/', '_')}.json"


def rescore(cases: list, results_dir: Path, out_dir: Path) -> list[dict]:
    """Rebuild results.csv and summary.md from the briefs saved in results_dir,
    with the current scoring. No model is built or called. Cases without a saved
    brief are skipped (a rep-1 folder holds 30 of the 90). Writes to out_dir,
    which must be another folder so recorded results are never overwritten."""
    if out_dir.resolve() == results_dir.resolve():
        raise ValueError("rescore writes to a separate folder; pick another --out")
    rows = []
    for case in cases:
        path = _case_path(results_dir, case)
        if path.exists():
            brief = json.loads(path.read_text())
            rows.append(_row(case, brief, score(case, brief)))
    out_dir.mkdir(parents=True, exist_ok=True)
    write_summary(rows, out_dir)
    return rows


def _heldout_check(cases: list, out_dir: Path) -> None:
    """The one-shot rule for the held-out system: once a run over it is complete,
    no second run may look at it. Keyed on the stamp, which is written only when
    every case of the whole run has a saved brief; summary.md is no use for this,
    because it is also written after a spend-cap stop, and that run must be able
    to resume."""
    for stamp in out_dir.parent.glob(f"*/{HELDOUT_STAMP}"):
        if stamp.parent.resolve() != out_dir.resolve():
            raise RuntimeError(f"{HELDOUT_SYSTEM!r} was already evaluated once, in {stamp.parent} "
                               f"({HELDOUT_STAMP}). The held-out run is one-shot: a second run "
                               "would be tuned on the first one's result.")
    missing = [c.case_id for c in cases if not _case_path(out_dir, c).exists()]
    if (out_dir / HELDOUT_STAMP).exists() and missing:
        raise RuntimeError(f"{out_dir} is stamped complete ({HELDOUT_STAMP}) but {len(missing)} of the "
                           f"requested cases were not part of it (first: {missing[0]}). The held-out "
                           "run is one-shot: it cannot be extended after its result was seen.")


def _heldout_stamp(whole_run: list, out_dir: Path) -> None:
    """Stamp the folder once every case of the WHOLE run has a saved brief. The
    whole run, not the cases of this call: scripts/local_eval.sh runs one case
    per call, and a --cases/--limit smoke run is a part too. Stamping on a part
    would close the run after its first case and refuse the rest."""
    stamp = out_dir / HELDOUT_STAMP
    if stamp.exists():
        return
    done = sum(_case_path(out_dir, c).exists() for c in whole_run)
    if done < len(whole_run):
        log.info("HELD-OUT run not complete: %d of %d cases saved; not stamped yet (the one-shot "
                 "rule applies once it is)", done, len(whole_run))
        return
    stamp.write_text(json.dumps({"system": HELDOUT_SYSTEM, "cases": [c.case_id for c in whole_run],
                                 "completed": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2),
                     encoding="utf-8")
    log.info("HELD-OUT run complete: stamped %s (one-shot; no second run)", stamp.name)


def run_eval(cases: list, make_model, out_dir: Path, max_usd: float | None = None,
             tot: str | None = None, arch: str = "linear", hide=(),
             whole_run: list | None = None) -> list[dict]:
    """Run each case (skipping ones already saved in out_dir); returns all rows.
    whole_run: every case the run consists of, when `cases` is only a part of it
    (one case per call, --cases, --limit). Read only for the held-out stamp."""
    out_dir.mkdir(parents=True, exist_ok=True)
    heldout = any(c.system == HELDOUT_SYSTEM for c in cases)
    if heldout:
        _heldout_check(cases, out_dir)
    rows, spent = [], 0.0
    for i, case in enumerate(cases, 1):
        path = _case_path(out_dir, case)
        if path.exists():
            brief = json.loads(path.read_text())
            if sorted(brief.get("telemetry_hidden") or []) != sorted(hide):
                # resuming with a different --hide would mix two experiments in one folder
                raise ValueError(f"{path.name} was saved with hidden telemetry "
                                 f"{brief.get('telemetry_hidden') or 'none'}, this run hides "
                                 f"{sorted(hide) or 'none'}; use another --out")
        else:
            if max_usd is not None and spent >= max_usd:
                log.info("STOP: spend cap $%.2f reached ($%.2f); %d cases left", max_usd, spent,
                         len(cases) - i + 1)
                break
            log.info("=== [%d/%d] %s", i, len(cases), case.case_id)
            model = make_model()
            t0 = time.monotonic()
            try:
                # hide is passed only when set, so stand-ins for diagnose keep working
                brief = diagnose(case, model, tot=tot, arch=arch, **({"hide": hide} if hide else {}))
            except Exception as e:  # keep going; the row records it
                brief = {"status": "error", "error": f"{type(e).__name__}: {e}",
                         "traceback": traceback.format_exc(),
                         "usage": {"wall_seconds": round(time.monotonic() - t0, 1)}}
                if hide:
                    brief["telemetry_hidden"] = sorted(hide)
            brief.setdefault("usage", {})["cost_usd"] = getattr(model, "cost_usd", 0.0)
            path.write_text(json.dumps(brief, indent=2, default=str), encoding="utf-8")
        outcome = score(case, brief)
        row = _row(case, brief, outcome)
        spent += row["cost_usd"]
        rows.append(row)
        log.info("    %s -> %s (pred %s/%s) $%.3f | total $%.2f", case.case_id, outcome,
                 row["pred_service"] or "-", row["pred_fault"] or "-", row["cost_usd"], spent)
    write_summary(rows, out_dir)
    if heldout:
        _heldout_stamp(whole_run or cases, out_dir)
    return rows


def _said_unfamiliar(row: dict) -> bool:
    return row.get("escalation_code") == "unfamiliar"


def _p95(values: list[float]) -> float:
    """95th percentile by nearest rank: the smallest value with at least 95% of
    the sample at or below it (no interpolation, so it is always a real run)."""
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def metrics_section(rows: list[dict]) -> list[str]:
    """The Checkpoint 6.1 section 3 metrics by name, from the per-case rows."""
    scored = [r for r in rows if r["outcome"] != "error"]
    n = len(scored)
    c = Counter(r["outcome"] for r in scored)
    of = lambda k, total=n: f"{k} of {total} ({100 * k / total:.0f}%)" if total else f"{k} of 0"
    esc = [r for r in scored if r["outcome"] in ("escalated", UNFAMILIAR_OK)]
    lines = ["", "## 6.1 metrics", "",
             f"- correct rate: {of(c['correct'])}",
             f"- wrong-service (diagnosed, wrong service): {of(c['wrong'])}",
             f"- wrong-fault (right service, wrong fault): {of(c['service_only'])}",
             f"- escalation rate: {of(c['escalated'])}"]
    odd = [r for r in scored if r["truth_fault"] not in KNOWN_FAULTS]
    if odd:  # RE3 code-level faults: the right answer is a handover
        oc = Counter(r["outcome"] for r in odd)
        lines += [f"- unfamiliar-fault cases (labeled fault outside the six known types): {of(len(odd))}; "
                  "the rates above do not apply to them",
                  f"- unfamiliar handled correctly (handed over with the true service "
                  f"as the lead hypothesis): {of(oc[UNFAMILIAR_OK], len(odd))}",
                  f"- escalated with the code 'unfamiliar': {sum(_said_unfamiliar(r) for r in odd)} of "
                  f"{len(odd)}; about the true service: "
                  f"{sum(_said_unfamiliar(r) and r['outcome'] == UNFAMILIAR_OK for r in odd)}",
                  f"- unfamiliar, other escalation: {of(oc['escalated'], len(odd))}",
                  f"- unfamiliar diagnosed as a known fault type (confident and wrong): "
                  f"{of(oc['wrong'], len(odd))}"]
    known = [r for r in scored if r["truth_fault"] in KNOWN_FAULTS]
    false_odd = sum(_said_unfamiliar(r) for r in known)
    if false_odd or any(r.get("tot") for r in known):
        # only ToT makes the call, so the line appears on runs where it ran: the
        # number to read next to the unfamiliar-fault result on the code-level set
        lines.append("- false unfamiliar (a known fault type handed over as 'unfamiliar'): "
                     + of(false_odd, len(known)))
    hidden = Counter(r["hidden"] for r in rows if r.get("hidden"))
    if hidden:
        lines.append("- telemetry hidden (robustness ablation): "
                     + ", ".join(f"{k} in {v} of {len(rows)} cases" for k, v in hidden.most_common()))
    if "metrics" in hidden:
        with_tot = sum(bool(r.get("tot")) or r.get("arch") == "graph" for r in rows if r.get("hidden") == "metrics")
        lines.append("- metrics hidden: no fault type's must-have signal can be checked. With ToT "
                     "(--tot or --arch graph) the fault type is never accepted unchecked, so every case "
                     "is an escalation by design and a 0% correct rate says nothing more; without ToT the "
                     "loop still names a fault type, unchecked (such briefs carry fault_type_unchecked), "
                     "so read that run on the service named, not on the fault type. "
                     f"ToT or graph ran in {with_tot} of {hidden['metrics']} such cases")
    codes = Counter(r.get("escalation_code") or "other" for r in esc)
    if codes:
        lines.append("- escalations by code: " + ", ".join(f"{k} {v}" for k, v in codes.most_common()))
    # ToT branches are compared exactly; free-text candidates only by word match.
    for label, part in (("ToT branches, exact", [r for r in esc if r.get("tot")]),
                        ("model's candidates, fault match approximate", [r for r in esc if not r.get("tot")])):
        hc = Counter(r.get("handover_truth") or "none" for r in part)
        if part:
            lines.append(f"- escalation quality ({label}; {len(part)} of {len(esc)} escalations): "
                         + ", ".join(f"{k} {v}" for k, v in hc.most_common()))
    advised = [r for r in rows if r.get("fix_advised")]
    if advised:
        dropped = sum(r["fix_dropped"] for r in advised)
        flags = sum(r["fix_flags"] for r in advised)
        lines.append(f"- groundedness: {len(advised)} briefs advised; dropped references {dropped} "
                     f"({dropped / len(advised):.2f} per brief); runbook contradiction flags {flags} "
                     f"({flags / len(advised):.2f} per brief)")
    else:
        lines.append("- groundedness: no Fix Advisor rows in this run")
    lines.append(f"- cost per case: ${sum(r['cost_usd'] for r in rows) / max(len(rows), 1):.3f}")
    secs = [r["wall_seconds"] for r in scored if r["wall_seconds"]]
    if secs:
        lines.append(f"- latency per case: mean {statistics.mean(secs):.1f}s, median "
                     f"{statistics.median(secs):.1f}s, p95 {_p95(secs):.1f}s (nearest rank)")
    return lines


def write_summary(rows: list[dict], out_dir: Path) -> str:
    if rows:
        with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
            # the union, in first-seen order: a row may carry a column others lack ("hidden")
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), restval="")
            w.writeheader()
            w.writerows(rows)
    n = len(rows)
    c = Counter(r["outcome"] for r in rows)
    scored = n - c["error"]
    pct = lambda k: f"{100 * c[k] / scored:.0f}%" if scored else "-"
    lines = [f"# Eval summary: {out_dir.name}", "",
             f"{n} cases run, {scored} scored ({c['error']} errors).", "",
             "| Outcome | Count | Share of scored |", "|---|---|---|"]
    # correct_unfamiliar is listed only when a case has it, so ordinary summaries do not change
    shown = OUTCOMES[:1] + ((UNFAMILIAR_OK,) if c[UNFAMILIAR_OK] else ()) + OUTCOMES[1:]
    lines += [f"| {k} | {c[k]} | {pct(k) if k != 'error' else '-'} |" for k in shown]
    cost = sum(r["cost_usd"] for r in rows)
    secs = sum(r["wall_seconds"] or 0 for r in rows)
    lines += ["", f"Total cost ${cost:.2f} (${cost / max(n, 1):.3f}/case); "
                  f"wall time {secs / 60:.0f} min ({secs / max(n, 1):.0f}s/case).", "",
              "## By fault type", ""]
    cols = [k for k in shown if k != "error"]
    cols.sort(key=("correct", "service_only", "wrong", "escalated", UNFAMILIAR_OK).index)
    lines += ["| Fault | n | " + " | ".join(cols) + " |", "|---|---|" + "---|" * len(cols)]
    for fault in sorted({r["truth_fault"] for r in rows}):
        fc = Counter(r["outcome"] for r in rows if r["truth_fault"] == fault)
        lines.append(f"| {fault} | {sum(fc.values())} | " + " | ".join(str(fc[k]) for k in cols) + " |")
    tc = Counter(r.get("tot") for r in rows if r.get("tot"))
    if tc:
        lines += ["", "## Tree-of-Thought", "",
                  f"ToT ran in {sum(tc.values())} of {n} cases.", "", "| ToT result | n |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in tc.most_common()]
    lines += metrics_section(rows)
    md = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    return md
