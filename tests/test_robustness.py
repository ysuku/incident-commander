"""Robustness harness tests (Checkpoint 6.1 section 3, answering the 5.1 feedback:
incomplete data, unfamiliar issues, another system). Everything here is default
off, so each group also pins that an ordinary run is unchanged.

  1. --hide: a telemetry kind withheld on the linear, ToT and graph paths
  2. scoring of cases whose fault is outside the six known types (RE3)
  3. dataset plumbing: folder names, the held-out one-shot stamp, callee lookup
"""
import csv
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from incident_commander import cli, config, evaluate, loaders, loop
from incident_commander import signatures as sig
from incident_commander import tot as tot_mod
from incident_commander.mcp_server import (BRANCH_TOOLS, DESCRIPTIONS, KIND_TOOLS, ToolClient,
                                           ToolError, build_server, without_kinds)
from incident_commander.roles import COMMANDER, FIX_ADVISOR, INVESTIGATOR

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")
needs_re3 = pytest.mark.skipif(not (config.RCAEVAL_DIR / "RE3-OB").exists(),
                               reason="RE3-OB not downloaded/extracted")
FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=0, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")
CPU = dict(cpu=10, mem=1, socket=1, latency_50=0.5)  # a clean cpu signature
KINDS = sorted(KIND_TOOLS)


class Scripted:
    """Replays replies (the last one repeats) and keeps every system prompt it saw."""

    def __init__(self, *replies):
        self.replies, self.calls, self.prompts, self.last_call = replies, 0, [], {}

    def complete(self, messages):
        self.prompts.append(messages[0]["content"])
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return r if isinstance(r, str) else json.dumps(r)


def act(tool, cands=(), **args):
    return {"rationale": "x", "candidates": list(cands), "action": {"tool": tool, "args": args}}


def finish(service, fault, *eids):
    return {"rationale": "x", "action": {"finish": {
        "root_cause_service": service, "fault_type": fault, "supporting_evidence": list(eids),
        "proposed_fix": {"description": "x", "destructive": False}}}}


def hidden_run(*hide, model=None):
    """(client, Run) on a case with no data, with `hide` withheld: only the
    branch board is real, evidence is put on the board by `put`."""
    tb = ToolClient(build_server(FAKE))
    tb.__enter__()
    view = without_kinds(tb, hide) if hide else tb
    return tb, loop.Run(FAKE, model or Scripted({}), view.scoped(COMMANDER.tools + BRANCH_TOOLS),
                        tot="heuristic")


def put(run, tool, kind, svc, implicated=True, result=None):
    """One piece of evidence about `svc`, as if the tool had run."""
    ref = f"traces.csv:serviceName={svc}" if kind == "traces" else f"{kind}.csv"
    ev = run.pad.add(kind, f"stub {tool} for {svc}", ref, [svc] if implicated else [])
    run.observations[ev.eid] = (tool, {"service": svc}, result or {})
    run.seen_calls[loop._call_key(run.toolbox, tool, {"service": svc})] = ev.eid
    return ev.eid


def put_metrics(run, svc, **z):
    devs = [{"service": svc, "metric": k.replace("_", "-"), "z": v} for k, v in z.items()]
    return put(run, "metric_query", "metrics", svc, any(v >= 3 for v in z.values()),
               {"top_deviations": devs})


def put_traces(run, svc, implicated=True):
    side = {"n_after": 50, "err_rate_before": 0.0, "err_rate_after": 0.0}
    return put(run, "trace_lookup", "traces", svc, implicated,
               {"service": svc, "overall": side, "inbound": side})


def verdicts(branch) -> dict:
    return {v["signal"]: v["verdict"] for v in branch["verdicts"]}


# ------------------------------------------------------------ 1. --hide: the tools are gone
@pytest.mark.parametrize("kind", KINDS)
def test_hidden_kind_is_absent_from_every_roles_view_and_refused_in_code(kind):
    with ToolClient(build_server(FAKE)) as tc:
        view = without_kinds(tc, (kind,))
        gone = set(KIND_TOOLS[kind])
        assert set(view.names) == set(tc.names) - gone             # runbook and branch tools stay
        assert "runbook_lookup" in view.names and set(BRANCH_TOOLS) <= set(view.names)
        for role_view in (COMMANDER.view(view), view.scoped(INVESTIGATOR.tools + BRANCH_TOOLS),
                          FIX_ADVISOR.view(view)):
            assert not gone & set(role_view.names) and role_view.hidden == {kind}
            assert not any(f"{t}(" in role_view.describe() for t in gone)
            with pytest.raises(ToolError, match="not available"):
                role_view.call(KIND_TOOLS[kind][0], {})
        assert COMMANDER.view(view).names == [t for t in COMMANDER.tools if t not in gone]


def test_service_map_goes_with_traces_and_unknown_kinds_are_refused():
    assert KIND_TOOLS["traces"] == ("trace_lookup", "service_map")   # the map is built from traces.csv
    with ToolClient(build_server(FAKE)) as tc:
        with pytest.raises(ValueError, match="cannot hide"):
            without_kinds(tc, ("runbooks",))


@pytest.mark.parametrize("arch", ["linear", "graph"])
def test_commander_never_sees_or_reaches_a_hidden_tool(arch):
    """The model keeps asking for the hidden tool: it is not in the prompt, the
    call is refused as an unknown tool, and the brief records what was hidden."""
    model = Scripted(act("trace_lookup", service="x"))
    brief = evaluate.diagnose(FAKE, model, arch=arch, tot="heuristic", hide=("traces",))
    assert model.prompts and not any("trace_lookup(" in p or "service_map(" in p for p in model.prompts)
    assert all("metric_query(" in p and "log_search(" in p for p in model.prompts)
    assert {e["outcome"] for e in brief["investigation"]} == {"unknown_tool"}
    assert brief["status"] == "escalated" and brief["telemetry_hidden"] == ["traces"]
    assert brief["evidence_board"] == []
    if arch == "graph":
        assert brief["roles"]["Commander"] == ["metric_query", "log_search"]
        assert brief["roles"]["Fix Advisor"] == ["runbook_lookup"]


def test_tot_investigator_never_sees_or_reaches_a_hidden_tool():
    model = Scripted({"rationale": "x", "tool": "trace_lookup", "args": {"service": "svc"}})
    tb, run = hidden_run("traces")
    with tb:
        put_metrics(run, "svc", **CPU)
        ctx = run.tot_context()
        branch = tb.call("open_branch", {"service": "svc", "fault_type": "cpu"})
        thought = tot_mod.ModelGenerator(model).propose(branch, None, ctx)
    assert "trace_lookup(" not in model.prompts[0] and "metric_query(" in model.prompts[0]
    assert thought is None  # the reply is unusable, and the heuristic has no trace test to fall back on


# ------------------------------------------------------------ 1. unobservable, not untested
def test_signals_of_a_hidden_kind_are_unobservable_not_untested():
    seen = sig.features("svc", {}, {})
    blind = sig.features("svc", {}, {}, hidden=frozenset({"traces"}))
    of = lambda f, fault: {v.signal: v.verdict for v in sig.judge(fault, f)}
    assert of(seen, "delay")["trace_slow"] == of(seen, "delay")["no_errors"] == sig.UNTESTED
    assert of(blind, "delay")["trace_slow"] == of(blind, "delay")["no_errors"] == sig.UNOBSERVABLE
    assert of(blind, "cpu")["cpu_up"] == sig.UNTESTED               # metrics can still be tested
    no_metrics = sig.features("svc", {}, {}, hidden=frozenset({"metrics"}))
    for fault in sig.FAULT_TYPES:
        vs = sig.judge(fault, no_metrics)
        assert {v.verdict for v in vs if v.kind == "metrics"} == {sig.UNOBSERVABLE}
        assert {v.verdict for v in vs if v.kind == "traces"} == {sig.UNTESTED}
        assert not sig.must_contradicted(vs)                         # "no data" never prunes


def test_hidden_metrics_is_not_an_unfamiliar_fault():
    """With metrics hidden every must-have is unobservable and scores are <= 0:
    the unfamiliar test would fire for every service. Nothing was ruled out."""
    f = sig.features("svc", {}, {}, hidden=frozenset({"metrics"}))
    assert all(s <= 0 for _, s, _ in sig.rank_faults(f)) and not sig.unfamiliar(f)


def test_heuristic_proposes_no_test_for_a_hidden_kind():
    tb, run = hidden_run("traces")
    with tb:
        put_metrics(run, "svc", **CPU)
        ctx = run.tot_context()
        branch = tb.call("open_branch", {"service": "svc", "fault_type": "cpu"})
        assert tot_mod.HeuristicGenerator().propose(branch, None, ctx) is None
    tb, run = hidden_run()
    with tb:
        put_metrics(run, "svc", **CPU)
        branch = tb.call("open_branch", {"service": "svc", "fault_type": "cpu"})
        assert tot_mod.HeuristicGenerator().propose(branch, None, run.tot_context()).tool == "trace_lookup"


# ------------------------------------------------------------ 1. the gate is never relaxed
def test_gate_still_needs_two_kinds_when_only_two_remain():
    tb, run = hidden_run("traces")
    with tb:
        e1 = put_metrics(run, "svc", **CPU)
        brief, problem = loop._gate(run.pad, finish("svc", "cpu", e1)["action"]["finish"])
        assert not brief and "only ['metrics'] implicates" in problem and "two independent kinds" in problem
        e2 = put(run, "log_search", "logs", "svc")
        brief, problem = loop._gate(run.pad, finish("svc", "cpu", e1, e2)["action"]["finish"])
        assert problem is None and brief["telemetry_kinds"] == ["logs", "metrics"]


def test_tot_with_traces_hidden_escalates_on_one_kind_and_selects_on_two():
    """A full cpu signature in the metrics, traces hidden. One kind is not enough,
    however clean the branch is; a second kind (logs) makes the same branch pass."""
    tb, run = hidden_run("traces")
    with tb:
        put_metrics(run, "svc", **CPU)
        brief = run.tot_brief("T2 test", "svc", None)
    t = brief["tot"]
    assert brief["status"] == "escalated" and brief["escalation"]["code"] == "tot_no_branch"
    assert t["hidden"] == ["traces"] and t["expansions"] == 0 and not t["unfamiliar"]
    cpu = next(b for b in t["ranked"] if b["fault_type"] == "cpu")
    assert verdicts(cpu)["trace_slow"] == sig.UNOBSERVABLE and cpu["status"] != "pruned"
    assert "trace_slow: traces hidden in this run" in t["flags"][cpu["branch_id"]]
    assert not any("failed" in n for n in brief["notes"])

    tb, run = hidden_run("traces")
    with tb:
        put_metrics(run, "svc", **CPU)
        put(run, "log_search", "logs", "svc")
        brief = run.tot_brief("T2 test", "svc", None)
    assert brief["status"] == "diagnosed" and brief["root_cause"] == {"service": "svc", "fault_type": "cpu"}
    assert brief["telemetry_kinds"] == ["logs", "metrics"]


def test_metrics_hidden_never_accepts_an_unchecked_fault_type():
    """Logs and traces both implicate the service, so the gate passes. Without
    metrics no fault type's must-have signal can be checked: T1 must say so (not
    'clear'), and ToT must hand over, not select the best-scoring guess."""
    tb, run = hidden_run("metrics", model=Scripted(finish("svc", "cpu", "E1", "E2")))
    with tb:
        put(run, "log_search", "logs", "svc")
        put_traces(run, "svc")
        kind, payload = run.think()
        brief, problem = run.gate(payload)
        assert kind == "finish" and problem is None                 # two kinds: the gate is satisfied
        trigger, suspect, rival = run.ambiguity(payload, brief)
        assert trigger.startswith("T1") and "metrics are hidden" in trigger and suspect == "svc"
        out = run.tot_brief(trigger, suspect, rival)
    t = out["tot"]
    assert out["status"] == "escalated" and t["status"] == "escalated" and not t["selected"]
    assert t["hidden"] == ["metrics"] and t["unfamiliar"] is False
    assert out["escalation"]["code"] == "tot_no_branch"
    assert "metrics are hidden in this run" in out["reason"] and "untested guesses" in out["reason"]
    assert out["escalation"]["hypotheses"][0]["service"] == "svc"    # the service is still named
    live = [b for b in t["ranked"] if b["status"] != "pruned"]
    assert all(v["verdict"] == sig.UNOBSERVABLE for b in live for v in b["verdicts"] if v["kind"] == "metrics")
    assert not any("metric_query" in n for n in out["notes"])       # no failed call left on the board


def test_t1_check_is_unchanged_when_nothing_is_hidden():
    """A service the metric tool does not know still keeps the linear answer
    (None), as before; only a hidden metric_query turns that into a reason."""
    failing = lambda name, args, why: None
    fin = {"root_cause_service": "svc", "fault_type": "cpu"}
    pad = loop.Scratchpad(alert="a")
    assert loop._fault_ambiguity(pad, failing, {}, fin) is None
    assert "cannot be checked" in loop._fault_ambiguity(pad, failing, {}, fin, frozenset({"metrics"}))
    assert loop._fault_ambiguity(pad, failing, {}, fin, frozenset({"traces", "logs"})) is None


@needs_data
@pytest.mark.parametrize("arch", ["linear", "graph"])
def test_real_case_with_traces_hidden_escalates_without_touching_traces(arch):
    c = loaders.load_case("checkoutservice_cpu/1")
    cand = ["checkoutservice: cpu saturation"]
    model = Scripted(act("metric_query", service="checkoutservice"),
                     act("log_search", cand, level="error"),
                     act("log_search", cand, service="checkoutservice", level="warn"))
    brief = evaluate.diagnose(c, model, tot="heuristic", arch=arch, hide=("traces",))
    assert brief["status"] == "escalated" and brief["telemetry_hidden"] == ["traces"]
    assert brief["tot"]["trigger"].startswith("T2") and brief["tot"]["hidden"] == ["traces"]
    assert {e["kind"] for e in brief["evidence_board"]} <= {"metrics", "logs"}
    assert not any(v["verdict"] == sig.UNTESTED for b in brief["tot"]["ranked"] for v in b["verdicts"]
                   if v["kind"] == "traces")
    assert evaluate.score(c, brief) == "escalated" and model.calls == 3


@needs_data
def test_offline_script_honours_hide():
    spec = importlib.util.spec_from_file_location("tot_offline", config.REPO_ROOT / "scripts" / "tot_offline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = loaders.load_case("emailservice_delay/1")
    assert mod.run_case(c, "truth")["outcome"] == "correct"
    for kind in ("traces", "metrics"):
        row = mod.run_case(c, "truth", (kind,))
        assert row["outcome"].startswith("escalated"), (kind, row)
    assert mod.run_case(c, "truth", ("logs",)) == mod.run_case(c, "truth")   # the control


# ------------------------------------------------------------ 1. default off: nothing changes
ROW_COLUMNS = ["case_id", "truth_service", "truth_fault", "status", "pred_service", "pred_fault",
               "outcome", "steps", "model_calls", "wall_seconds", "prompt_tokens", "completion_tokens",
               "cost_usd", "reason", "tot", "arch", "fix_flags", "escalation_code", "handover_truth",
               "fix_advised", "fix_dropped", "model_seconds", "tool_seconds"]


def test_default_row_tool_list_and_dispatch_are_unchanged(monkeypatch, tmp_path):
    brief = {"status": "diagnosed", "root_cause": {"service": "x", "fault_type": "cpu"}}
    row = evaluate._row(FAKE, brief, evaluate.score(FAKE, brief))
    assert list(row) == ROW_COLUMNS and row["outcome"] == "correct"
    hid = evaluate._row(FAKE, {**brief, "telemetry_hidden": ["traces"]}, "correct")
    assert list(hid) == ROW_COLUMNS + ["hidden"] and hid["hidden"] == "traces"

    with ToolClient(build_server(FAKE)) as tc:
        assert tc.names == list(DESCRIPTIONS) and tc.hidden == frozenset()
        assert COMMANDER.view(tc).hidden == frozenset()
        run = loop.Run(FAKE, Scripted({}), COMMANDER.view(tc))
        assert run.hidden == frozenset() and run.tot_context().hidden == frozenset()
        assert run.system == loop.SYSTEM_PROMPT.replace("{tools}", COMMANDER.view(tc).describe())

    seen = []
    monkeypatch.setattr(loop, "diagnose", lambda *a, **kw: seen.append((a[2:], kw)) or dict(brief))
    out = evaluate.diagnose(FAKE, None)
    assert seen == [((), {"tot": None})]               # the ordinary call: no toolbox, no hide
    assert "telemetry_hidden" not in out
    evaluate.run_eval([FAKE], lambda: None, tmp_path)
    assert seen[-1] == ((), {"tot": None})
    header = next(csv.reader(open(tmp_path / "results.csv")))
    assert header == ROW_COLUMNS
    summary = (tmp_path / "summary.md").read_text()
    assert "hidden" not in summary and "unfamiliar" not in summary
    assert "| Fault | n | correct | service_only | wrong | escalated |\n|---|---|---|---|---|---|\n" in summary


def test_default_tot_result_has_no_hidden_key():
    tb, run = hidden_run()
    with tb:
        put_metrics(run, "svc", **CPU)
        brief = run.tot_brief("T2 test", "svc", None)
    assert "hidden" not in brief["tot"] and "hidden" not in brief["reason"]
    cpu = next(b for b in brief["tot"]["ranked"] if b["fault_type"] == "cpu")
    assert verdicts(cpu)["trace_slow"] == sig.UNTESTED  # the trace test was tried, and failed for want of data


def test_hidden_column_summary_line_and_no_mixing_on_resume(tmp_path, monkeypatch):
    calls = []

    def fake(case, model, toolbox=None, tot=None):
        calls.append(sorted(toolbox.hidden))
        return {"status": "diagnosed", "confidence": "high",
                "root_cause": {"service": "x", "fault_type": "cpu"}, "usage": {}}

    monkeypatch.setattr(loop, "diagnose", fake)
    rows = evaluate.run_eval([FAKE], lambda: None, tmp_path, hide=("logs",))
    assert calls == [["logs"]] and rows[0]["hidden"] == "logs"
    saved = json.loads((tmp_path / "x_cpu_1.json").read_text())
    assert saved["telemetry_hidden"] == ["logs"] and saved["confidence"] == "medium"
    assert next(csv.reader(open(tmp_path / "results.csv"))) == ROW_COLUMNS + ["hidden"]
    assert "- telemetry hidden (robustness ablation): logs in 1 of 1 cases" in (tmp_path / "summary.md").read_text()
    evaluate.run_eval([FAKE], lambda: None, tmp_path, hide=("logs",))   # same experiment: resumes
    assert len(calls) == 1
    for other in ((), ("traces",)):
        with pytest.raises(ValueError, match="use another --out"):
            evaluate.run_eval([FAKE], lambda: None, tmp_path, hide=other)


# ------------------------------------------------------------ 2. unfamiliar-fault scoring
def odd_case(system="online-boutique-re3"):
    return loaders.IncidentCase("emailservice_f3/1", system, "1", 0, Path("."), "emailservice", "f3")


def esc(code, *services, **extra):
    hyps = [{"service": s, "fault_type": None, "score": None, "summary": "", "evidence": []} for s in services]
    return {"status": "escalated", "reason": "r", "candidates": [],
            "escalation": {"code": code, "reason": "r", "hypotheses": hyps, "note": "n"}, **extra}


def test_known_faults_are_the_critics_six():
    assert evaluate.KNOWN_FAULTS == sig.FAULT_TYPES


def test_unfamiliar_fault_scoring():
    c = odd_case()
    assert evaluate.score(c, esc("unfamiliar", "emailservice")) == "correct_unfamiliar"
    assert evaluate.score(c, esc("unfamiliar", "emailservice or cartservice")) == "correct_unfamiliar"
    # "unfamiliar" about the wrong service is what a wrong suspect looks like: not correct
    assert evaluate.score(c, esc("unfamiliar", "cartservice")) == "escalated"
    assert evaluate.score(c, esc("unfamiliar", "cartservice or emailservice")) == "escalated"
    assert evaluate.score(c, esc("tot_tie", "emailservice", "cartservice")) == "correct_unfamiliar"
    assert evaluate.score(c, esc("budget_exhausted", "email")) == "correct_unfamiliar"   # normalized
    assert evaluate.score(c, esc("tot_no_branch", "cartservice", "emailservice")) == "escalated"  # not the lead
    assert evaluate.score(c, esc("budget_exhausted")) == "escalated"
    # any diagnosis claims one of the six known types: wrong, even on the right service
    for svc in ("emailservice", "cartservice"):
        assert evaluate.score(c, {"status": "diagnosed",
                                  "root_cause": {"service": svc, "fault_type": "cpu"}}) == "wrong"
    assert evaluate.score(c, {"status": "error"}) == "error"
    # briefs saved before the block existed: the first candidate line
    old = {"status": "escalated", "reason": "step budget (8) exhausted"}
    assert evaluate.score(c, {**old, "candidates": ["emailservice: something odd", "cartservice: x"]}) == "correct_unfamiliar"
    assert evaluate.score(c, {**old, "candidates": ["cartservice: x", "emailservice: y"]}) == "escalated"
    odd = {"unfamiliar": True, "selected": None, "status": "escalated", "ranked": [], "tied": []}
    assert evaluate.escalation_code({**old, "tot": odd}) == "unfamiliar"
    assert evaluate.score(c, {**old, "tot": odd}) == "escalated"          # names nobody
    assert evaluate.score(c, {**old, "tot": odd, "candidates": ["emailservice: unfamiliar fault"]}) == "correct_unfamiliar"


def test_six_type_scoring_is_unchanged():
    c = loaders.IncidentCase("emailservice_mem/1", "online-boutique", "1", 0, Path("."), "emailservice", "mem")
    d = lambda s, f: {"status": "diagnosed", "root_cause": {"service": s, "fault_type": f}}
    assert [evaluate.score(c, b) for b in (d("emailservice", "mem"), d("email", "disk"), d("cart", "mem"),
                                           esc("unfamiliar", "emailservice"), esc("tot_tie", "emailservice"),
                                           {"status": "error"})] == [
        "correct", "service_only", "wrong", "escalated", "escalated", "error"]


def test_summary_shows_unfamiliar_outcomes_only_when_present(tmp_path):
    c = odd_case()
    briefs = [esc("unfamiliar", "emailservice"), esc("budget_exhausted", "cartservice"),
              {"status": "diagnosed", "root_cause": {"service": "emailservice", "fault_type": "cpu"}}]
    rows = [evaluate._row(c, b, evaluate.score(c, b)) for b in briefs]
    md = evaluate.write_summary(rows, tmp_path)
    assert "| correct_unfamiliar | 1 | 33% |" in md
    assert "| Fault | n | correct | service_only | wrong | escalated | correct_unfamiliar |" in md
    assert "| f3 | 3 | 0 | 0 | 1 | 1 | 1 |" in md
    assert "- unfamiliar-fault cases (labeled fault outside the six known types): 3 of 3 (100%)" in md
    assert "as the lead hypothesis): 1 of 3 (33%)" in md
    assert "- escalated with the code 'unfamiliar': 1 of 3; about the true service: 1" in md
    assert "- unfamiliar, other escalation: 1 of 3 (33%)" in md
    assert "(confident and wrong): 1 of 3 (33%)" in md
    assert "- escalations by code: unfamiliar 1, budget_exhausted 1" in md   # both handovers are escalations


# ------------------------------------------------------------ 3. dataset plumbing
def test_default_out_dir_names():
    name = lambda *a, **kw: cli.default_out_name(*a, day="2026-11-17", **kw)
    assert name("opus", None, "linear") == "2026-11-17_opus"                     # as before
    assert name("opus", "model", "linear") == "2026-11-17_opus_tot-model"
    assert name("opus", "model", "graph") == "2026-11-17_opus_tot-model_graph"
    assert name("opus", None, "linear", config.DEV_SYSTEM) == "2026-11-17_opus"
    assert name("opus", "model", "linear", hide="traces") == "2026-11-17_opus_tot-model_hide-traces"
    assert name("q", None, "linear", "online-boutique-re3") == "2026-11-17_q_online-boutique-re3"
    assert name("q", "model", "graph", "train-ticket", "logs") == "2026-11-17_q_train-ticket_tot-model_graph_hide-logs"
    assert cli.default_out_name("m", None, "linear")[:4].isdigit()               # today's date by default


class Priced:
    cost_usd = 1.0


def heldout_cases(n=3):
    return [loaders.IncidentCase(f"ts-order-service_cpu/{i}", config.HELDOUT_SYSTEM, str(i), 0, Path("."),
                                 "ts-order-service", "cpu") for i in range(1, n + 1)]


def test_heldout_stamp_is_written_on_completion_and_resume_still_works(tmp_path, monkeypatch):
    calls = []

    def fake(case, model, **kw):
        calls.append(case.case_id)
        return {"status": "diagnosed", "root_cause": {"service": "ts-order", "fault_type": "cpu"}, "usage": {}}

    monkeypatch.setattr(loop, "diagnose", fake)
    cases, out = heldout_cases(), tmp_path / "tt_run"
    stamp = out / evaluate.HELDOUT_STAMP
    rows = evaluate.run_eval(cases, Priced, out, max_usd=2.0)      # the cap stops it after two cases
    assert len(rows) == 2 and (out / "summary.md").exists() and not stamp.exists()
    rows = evaluate.run_eval(cases, Priced, out)                   # resume: summary.md does not block it
    assert len(calls) == 3 and [r["outcome"] for r in rows] == ["correct"] * 3
    done = json.loads(stamp.read_text())
    assert done["system"] == config.HELDOUT_SYSTEM and done["cases"] == [c.case_id for c in cases]
    evaluate.run_eval(cases, Priced, out)                          # complete: re-summarising is free
    assert len(calls) == 3 and json.loads(stamp.read_text()) == done
    with pytest.raises(RuntimeError, match="one-shot"):            # a second run elsewhere is refused
        evaluate.run_eval(cases, Priced, tmp_path / "tt_again")
    with pytest.raises(RuntimeError, match="cannot be extended"):  # and so is growing the first
        evaluate.run_eval(heldout_cases(4), Priced, out)
    assert len(calls) == 3


def test_dev_system_runs_are_never_stamped_or_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "diagnose", lambda c, m, **kw: {"status": "escalated", "usage": {}})
    (tmp_path / "tt").mkdir()
    (tmp_path / "tt" / evaluate.HELDOUT_STAMP).write_text("{}")    # a finished held-out run next door
    evaluate.run_eval([FAKE], Priced, tmp_path / "a")
    evaluate.run_eval([FAKE], Priced, tmp_path / "b")
    assert not list(tmp_path.glob(f"[ab]/{evaluate.HELDOUT_STAMP}"))


def test_heldout_guard_still_needs_the_env_flag(monkeypatch):
    monkeypatch.delenv("IC_ALLOW_HELDOUT", raising=False)
    with pytest.raises(RuntimeError, match="held-out"):
        loaders.list_cases(config.HELDOUT_SYSTEM)


def test_norm_joins_train_ticket_style_names_and_leaves_online_boutique_alone():
    assert loop._norm("ts-order-service") == loop._norm("ts-order") == "ts-order"
    ob = ["adservice", "cartservice", "checkoutservice", "currencyservice", "emailservice", "frontend",
          "paymentservice", "productcatalogservice", "recommendationservice", "redis", "shippingservice"]
    strip = lambda s: s[: -len("service")] if s.endswith("service") else s       # the rule before
    assert [loop._norm(s) for s in ob] == [strip(s) for s in ob]


def spans(rows):
    return pd.DataFrame(rows, columns=["spanID", "serviceName", "operationName", "parentSpanID"])


def old_callees(df):
    """The expression tools._service_spans used before span_callees existed."""
    return (df["operationName"].str.extract(r"hipstershop\.(\w+?)Service/", expand=False)
            .str.lower() + "service")


def test_span_callees_uses_the_operation_name_when_any_span_has_one():
    df = spans([("a", "frontend", "hipstershop.CurrencyService/Convert", None),
                ("b", "currencyservice", "hipstershop.CurrencyService/Convert", "a"),
                ("c", "frontend", "/cart", None),
                ("d", "redis", "GET", "c")])       # a cross-service child, but the regex already answered
    got = loaders.span_callees(df)
    pd.testing.assert_series_equal(got, old_callees(df))
    assert got.tolist()[:2] == ["currencyservice", "currencyservice"] and got[2:].isna().all()


def test_span_callees_falls_back_to_the_span_graph_on_another_system():
    df = spans([("a", "ts-ui", "POST /order", None),
                ("b", "ts-order-service", "create", "a"),          # a calls ts-order-service
                ("c", "ts-order-service", "db.query", "b"),        # same service: not a call
                ("d", "ts-ui", "fan-out", None),
                ("e", "ts-user-service", "get", "d"),
                ("f", "ts-price-service", "get", "d"),             # d has two callees: ambiguous
                ("g", "ts-user-service", "orphan", "missing")])    # parent not in the table
    assert old_callees(df).isna().all()
    got = loaders.span_callees(df)
    assert got.iloc[0] == "ts-order-service" and got.iloc[1:].isna().all()


@needs_data
@pytest.mark.parametrize("case_id", ["checkoutservice_delay/1", "emailservice_cpu/1", "currencyservice_loss/2"])
def test_span_callees_is_identical_on_online_boutique(case_id):
    df = loaders.load_traces(loaders.load_case(case_id),
                             columns=["spanID", "serviceName", "operationName", "parentSpanID"])
    pd.testing.assert_series_equal(loaders.span_callees(df), old_callees(df))


def test_code_level_fault_labels_load_from_the_folder_name(tmp_path, monkeypatch):
    """RE3 folders are <service>_f<n>: the loader needs no change, and the label
    lands outside the six known types, which is what switches the scoring."""
    run = tmp_path / "RE3-OB" / "emailservice_f1" / "1"
    run.mkdir(parents=True)
    (run / "inject_time.txt").write_text("1700000000\n")
    monkeypatch.setattr(loaders, "RCAEVAL_DIR", tmp_path)
    (c,) = loaders.list_cases("online-boutique-re3")
    assert (c.root_cause_service, c.fault_type, c.system) == ("emailservice", "f1", "online-boutique-re3")
    assert c.fault_type not in evaluate.KNOWN_FAULTS
    assert evaluate.score(c, esc("unfamiliar", "emailservice")) == "correct_unfamiliar"
    with pytest.raises(FileNotFoundError, match="download_data.py --match RE2-OB"):
        loaders.list_cases("online-boutique")        # absent data names the command to fetch it


@needs_re3
def test_re3_cases_have_the_files_the_tools_read():
    cases = loaders.list_cases("online-boutique-re3")
    assert cases and all(c.fault_type not in evaluate.KNOWN_FAULTS for c in cases)
    c = cases[0]
    for kind in ("metrics_simple", "logs", "traces"):
        assert c.path(kind).exists()
    cols = set(pd.read_csv(c.path("traces"), nrows=0).columns)
    assert {"traceID", "spanID", "serviceName", "operationName", "startTimeMillis", "duration",
            "statusCode", "parentSpanID"} <= cols
