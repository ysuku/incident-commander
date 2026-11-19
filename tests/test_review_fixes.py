"""Regression tests for the review of the Checkpoint 6.1 guardrail work.

The 5.1 reviewer asked whether "two supporting sources can always be trusted,
especially if both give misleading information". Groups 1 and 2 are the two
cheapest pairs of misleading sources the review found still passing the gate;
the rest pin the other findings (unfamiliar-fault call, caller-side traces on
another system, wording, what the gate keeps of a finish, the held-out stamp).
Tests on recorded incidents skip when RE2-OB is absent."""
import importlib.util
import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from incident_commander import cli, config, evaluate, fix_advisor, loaders, loop, tools
from incident_commander import signatures as sig
from incident_commander.model import MockModel
from incident_commander.scratchpad import Scratchpad
from test_escalation import FLAT, ODD, seed, seeded

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")

FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=100, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")
PATHS = [("linear", None), ("linear", "heuristic"), ("graph", "heuristic")]


class Scripted:
    """Replays a list of actions (the last one repeats)."""

    def __init__(self, *actions):
        self.actions, self.i = actions, 0

    def complete(self, messages):
        a = self.actions[min(self.i, len(self.actions) - 1)]
        self.i += 1
        return json.dumps({"rationale": "scripted", "action": a})


def finish(service, fault, *eids, **over):
    return {"finish": {"root_cause_service": service, "fault_type": fault, "supporting_evidence": list(eids),
                       "proposed_fix": {"description": "x", "destructive": False}, **over}}


def two_kinds() -> Scratchpad:
    pad = Scratchpad(alert="x")
    pad.add("metrics", "m", "s", implicates=["a"]); pad.add("traces", "t", "s", implicates=["aservice"])
    return pad


# ------------------------------------------------------------ 1. caller's metrics + a failing path
@needs_data
@pytest.mark.parametrize("arch,tot", PATHS)
def test_frontend_is_not_diagnosed_from_two_views_of_a_downstream_fault(arch, tot):
    """currencyservice has the cpu fault. The unfiltered metric query shows the
    frontend's latency up, and the failing paths carry the frontend's error
    spans: one downstream fault seen twice from the caller, not two sources."""
    c = loaders.load_case("currencyservice_cpu/1")
    model = Scripted({"tool": "metric_query", "args": {}}, {"tool": "trace_lookup", "args": {}},
                     finish("frontend", "delay", "E1", "E2"))
    brief = evaluate.diagnose(c, model, tot=tot, arch=arch)
    board = {e["eid"]: e for e in brief["evidence_board"]}
    assert "frontend" in board["E1"]["implicates"]          # the misleading half is really there
    assert board["E2"]["kind"] == "traces" and board["E2"]["implicates"] == []
    assert brief["status"] == "escalated" and evaluate.score(c, brief) == "escalated"
    assert any("finish rejected" in n and "E2 [traces] does not implicate frontend" in n for n in brief["notes"])


# ------------------------------------------------------------ 2. caller-induced metric + info log lines
@needs_data
@pytest.mark.parametrize("arch,tot", PATHS)
def test_info_lines_matched_by_a_query_do_not_make_a_second_source(arch, tot):
    """checkoutservice has the delay. emailservice's latency rises because its
    caller is slow, and 'request' matches hundreds of its ordinary INFO lines."""
    c = loaders.load_case("checkoutservice_delay/1")
    model = Scripted({"tool": "metric_query", "args": {"service": "emailservice"}},
                     {"tool": "log_search", "args": {"service": "emailservice", "query": "request"}},
                     finish("emailservice", "delay", "E1", "E2"))
    brief = evaluate.diagnose(c, model, tot=tot, arch=arch)
    board = {e["eid"]: e for e in brief["evidence_board"]}
    assert board["E1"]["implicates"] == ["emailservice"]
    assert "matching lines" in board["E2"]["summary"] and board["E2"]["implicates"] == []
    assert brief["status"] == "escalated"
    assert (brief.get("root_cause") or {}).get("service") != "emailservice"


# ------------------------------------------------------------ 3. the keyless demo model
def mock_reply(board: str) -> dict:
    return json.loads(MockModel().complete([{"role": "system", "content": loop.SYSTEM_PROMPT},
                                            {"role": "user", "content": board}]))["action"]


def test_mock_model_checks_the_top_services_own_spans_and_names_a_known_fault():
    e1 = "E1 [metrics] significant deviations in scope [all services]: emailservice_error z=9 (50%)"
    assert mock_reply("ALERT") == {"tool": "metric_query", "args": {"top": 5}}
    assert mock_reply(e1) == {"tool": "trace_lookup", "args": {"service": "emailservice"}}
    done = mock_reply(e1 + "\nE2 [traces] traces for emailservice: SIGNIFICANT change")["finish"]
    assert done["root_cause_service"] == "emailservice" and done["supporting_evidence"] == ["E1", "E2"]
    assert done["fault_type"] == "loss" and set(MockModel.FAULT_BY_METRIC.values()) <= set(sig.FAULT_TYPES)


@needs_data
def test_mock_model_reaches_a_diagnosis_on_a_recorded_case():
    brief = loop.diagnose(loaders.load_case("emailservice_cpu/1"), MockModel())
    assert brief["status"] == "diagnosed" and brief["telemetry_kinds"] == ["metrics", "traces"]
    assert brief["root_cause"] == {"service": "emailservice", "fault_type": "cpu"}


# ------------------------------------------------------------ 4. "unfamiliar" needs an affected service
def feats(**z) -> sig.Features:
    f = sig.Features("svc", metrics_scoped="E1")
    f.z = {k.replace("_", "-"): (v, "E1") for k, v in z.items()}
    return f


def test_a_service_without_resource_series_is_not_unfamiliar():
    """frontend-external has latency, error and workload only: cpu, mem, socket
    and disk survive for want of data, so nothing was ruled out for them."""
    assert not sig.unfamiliar(feats(latency_50=0, latency_90=0, error=50, workload=-1))
    assert sig.unfamiliar(feats(**FLAT))          # what the critic says; the caller adds the rest


@pytest.mark.parametrize("z,traces", [(FLAT, False),    # nothing implicates it: a wrong suspect
                                      (ODD, False),     # its error series rose, like every caller's
                                      (FLAT, True)])    # one kind is not the gate's standard either
def test_flat_metrics_alone_are_a_wrong_suspect_not_an_unfamiliar_fault(z, traces):
    tb, run = seeded()
    with tb:
        seed(run, "svc", traces=traces, **z)
        brief = run.tot_brief("T2 test", "svc", None)
    assert brief["tot"]["unfamiliar"] is False and brief["tot"]["ruled_out"] == []
    assert brief["escalation"]["code"] == "tot_no_branch" and "unfamiliar" not in brief["reason"]


def test_unfamiliar_names_the_affected_service_and_is_scored_on_it():
    tb, run = seeded()
    with tb:
        seed(run, "svc", traces=True, **ODD)        # two kinds implicate it; no type fits
        seed(run, "other", traces=False, **FLAT)    # the rival is merely flat
        brief = run.tot_brief("T2 test", "svc", "other")
    assert brief["tot"]["unfamiliar"] is True and brief["escalation"]["code"] == "unfamiliar"
    assert {v["service"] for v in brief["tot"]["ruled_out"]} == {"svc"}
    lead = brief["escalation"]["hypotheses"][0]
    assert lead["service"] == "svc" and lead["fault_type"] == "unfamiliar"
    assert "fits svc -" in brief["reason"] and brief["candidates"][0].startswith("svc: unfamiliar")
    odd = lambda truth: loaders.IncidentCase(f"{truth}_f3/1", "online-boutique-re3", "1", 0, Path("."), truth, "f3")
    assert evaluate.score(odd("svc"), brief) == "correct_unfamiliar"
    assert evaluate.score(odd("other"), brief) == "escalated"       # unfamiliar, about the wrong service


@needs_data
@pytest.mark.parametrize("case_id,suspect", [("checkoutservice_loss/1", "frontend"),
                                             ("currencyservice_disk/2", "frontend-external")])
def test_known_faults_with_a_wrong_suspect_are_not_called_unfamiliar(case_id, suspect):
    """Two of the 11 (of 90) dev cases the review found: the label-free suspect
    is a caller whose resources are flat. Now 0 of 90."""
    spec = importlib.util.spec_from_file_location("tot_offline", config.REPO_ROOT / "scripts" / "tot_offline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    row = mod.run_case(loaders.load_case(case_id), "metric")
    assert row["suspect"] == suspect and row["unfamiliar"] is False and row["outcome"] == "escalated"


def test_summary_reports_false_unfamiliar_on_runs_where_tot_ran(tmp_path):
    known = loaders.IncidentCase("emailservice_mem/1", "online-boutique", "1", 0, Path("."), "emailservice", "mem")
    esc = lambda code: {"status": "escalated", "reason": "r", "candidates": [],
                        "tot": {"status": "escalated", "ranked": [], "tied": []},
                        "escalation": {"code": code, "reason": "r", "hypotheses": [], "note": "n"}}
    rows = [evaluate._row(known, b, evaluate.score(known, b)) for b in (esc("unfamiliar"), esc("tot_no_branch"))]
    md = evaluate.write_summary(rows, tmp_path)
    assert "- false unfamiliar (a known fault type handed over as 'unfamiliar'): 1 of 2 (50%)" in md
    linear = {"status": "escalated", "reason": "step budget (8) exhausted", "candidates": []}
    md = evaluate.write_summary([evaluate._row(known, linear, "escalated")], tmp_path)
    assert "unfamiliar" not in md                                   # no ToT: the line does not appear


# ------------------------------------------------------------ 5. caller-side traces on another system
def other_system_spans(n=6):
    """ts-ui -> ts-order-service -> {station, price, user}. After the alert (t=100s)
    every call ts-order-service makes takes 100 ms longer on ITS side: the callees'
    own spans stay at 8 ms. No operation names a target service."""
    rows = []
    for i in range(2 * n):
        t = (50_000 if i < n else 150_000) + i
        slow = 0 if i < n else 100_000
        rows += [(f"t{i}", f"u{i}", "ts-ui", "POST /order", t, 40_000 + 3 * slow, 0, None),
                 (f"t{i}", f"o{i}", "ts-order-service", "create", t, 36_000 + 3 * slow, 0, f"u{i}")]
        for k, callee in enumerate(("ts-station-service", "ts-price-service", "ts-user-service")):
            rows += [(f"t{i}", f"c{i}{k}", "ts-order-service", "GET", t, 10_000 + slow, 0, f"o{i}"),
                     (f"t{i}", f"s{i}{k}", callee, "query", t, 8_000, 0, f"c{i}{k}")]
    return pd.DataFrame(rows, columns=["traceID", "spanID", "serviceName", "operationName",
                                       "startTimeMillis", "duration", "statusCode", "parentSpanID"])


def test_trace_lookup_has_inbound_and_outbound_views_without_hipstershop_names(monkeypatch):
    monkeypatch.setattr(tools, "load_traces", lambda case, columns=None: other_system_spans())
    r = tools.trace_lookup(FAKE, service="ts-station-service")
    assert r["overall"]["p50_ratio"] == 1.0                              # its own spans did not move
    assert r["inbound"]["n_after"] == 6 and r["inbound"]["p50_ratio"] >= 50
    (caller,) = r["inbound_by_caller"]
    assert caller["caller"] == "ts-order-service"
    assert {c["callee"] for c in caller["caller_other_callees"]} == {"ts-price-service", "ts-user-service"}
    out = tools.trace_lookup(FAKE, service="ts-order-service")["outbound"]
    assert {c["callee"] for c in out} == {"ts-station-service", "ts-price-service", "ts-user-service"}

    # so the caller-side rule, and the gate's shared-cause rule, can fire there too
    _, summary, ref, implicated = loop._summarize("trace_lookup", r, {"service": "ts-station-service"})
    assert implicated == ["ts-order-service"] and "likely ts-order-service's own network" in summary
    pad = Scratchpad(alert="x")
    pad.add("metrics", "latency up", "simple_metrics.csv", ["ts-station-service"])
    pad.add("logs", "errors", "logs.csv#L2", ["ts-station-service"])
    pad.add("traces", summary, ref, implicated)
    _, problem = loop._gate(pad, finish("ts-station-service", "delay", "E1", "E2")["finish"])
    assert problem and "shared cause" in problem and "ts-order-service" in problem


def test_trace_lookup_survives_a_case_where_no_span_calls_another_service(monkeypatch):
    df = other_system_spans()
    monkeypatch.setattr(tools, "load_traces", lambda case, columns=None: df[df["serviceName"] == "ts-ui"])
    r = tools.trace_lookup(FAKE, service="ts-ui")
    assert r["inbound"] is None and r["inbound_by_caller"] == [] and r["outbound"] == []
    assert r["overall"]["n_after"] == 6


@needs_data
def test_service_spans_are_unchanged_on_online_boutique(monkeypatch):
    """The tool's output with span_callees against the inline regex it replaced
    (all 90 cases x every service were compared offline; this pins one case)."""
    c = loaders.load_case("checkoutservice_delay/1")
    df = loaders.load_traces(c, columns=["traceID", "spanID", "serviceName", "operationName",
                                         "startTimeMillis", "duration", "statusCode", "parentSpanID"])
    services = ("emailservice", "checkoutservice", "shippingservice", "frontendservice")
    # as text: shippingservice exports no spans, and its NaN medians never compare equal
    spans = lambda: [json.dumps(tools._service_spans(c, df, s, 10), default=str, sort_keys=True) for s in services]
    new = spans()
    assert json.loads(new[0])["inbound_by_caller"] and json.loads(new[1])["outbound"]
    monkeypatch.setattr(tools, "span_callees", lambda d: (
        d["operationName"].str.extract(r"hipstershop\.(\w+?)Service/", expand=False).str.lower() + "service"))
    assert spans() == new


# ------------------------------------------------------------ 6. a real service with no log lines
def test_log_search_says_a_known_service_has_no_lines_and_keeps_unknown_for_unknown_names(monkeypatch):
    logs = pd.DataFrame({"timestamp": [150 * 10**9], "container_name": ["frontend"],
                         "message": ["request complete"], "level": ["info"]})
    monkeypatch.setattr(tools, "load_logs", lambda case, columns=None: logs)
    monkeypatch.setattr(tools, "load_simple_metrics",
                        lambda case: pd.DataFrame({"time": [1], "currencyservice_cpu": [0.1]}))
    monkeypatch.setattr(tools, "load_traces",
                        lambda case, columns=None: pd.DataFrame({"serviceName": ["shippingservice"]}))
    for known in ("currencyservice", "currency", "shippingservice"):     # in the metrics / only in the traces
        with pytest.raises(ValueError, match=rf"no log lines from '{known}' in this case \(it is a known "
                                             r"service.*services with logs in this case are \['frontend'\]") as e:
            tools.log_search(FAKE, service=known)
        assert "unknown service" not in str(e.value)
    with pytest.raises(ValueError, match=r"unknown service 'nosuchservice'; services with logs .* \['frontend'\]"):
        tools.log_search(FAKE, service="nosuchservice")


@needs_data
def test_the_faulty_service_without_logs_is_not_called_unknown():
    c = loaders.load_case("currencyservice_delay/2")
    with pytest.raises(ValueError, match="no log lines from 'currencyservice' in this case"):
        tools.log_search(c, service="currencyservice")
    assert tools.metric_query(c, service="currencyservice")["top_deviations"]   # the other tools know it


# ------------------------------------------------------------ 7. what the gate keeps of a fix
def test_well_formed_fix_and_alternatives_keep_the_brief_as_it_was():
    alts = [{"candidate": "redis", "why": "flat in E1"}]
    brief, problem = loop._gate(two_kinds(), finish("a", "cpu", "E1", "E2", alternatives_rejected=alts)["finish"])
    assert problem is None and set(brief) == {"root_cause", "confidence", "supporting_evidence",
                                              "telemetry_kinds", "alternatives_rejected", "proposed_fix"}
    assert brief["alternatives_rejected"] == alts
    assert brief["proposed_fix"] == {"description": "x", "model_labeled_destructive": False,
                                     "requires_approval": True}


def test_a_null_description_is_skipped_and_other_keys_are_listed_not_lost():
    gate = lambda fix: loop._gate(two_kinds(), finish("a", "cpu", "E1", "E2", proposed_fix=fix)["finish"])[0]
    assert gate({"description": None, "destructive": False})["proposed_fix"] == {
        "model_labeled_destructive": False, "requires_approval": True}          # not the string "None"
    assert gate({"steps": ["drain", "restart"]})["proposed_fix"] == {
        "model_labeled_destructive": True, "requires_approval": True, "dropped_keys": ["steps"]}
    assert gate("restart it")["proposed_fix"]["description"] == "restart it"


def test_fix_advisor_keeps_the_record_of_what_the_gate_removed():
    brief = {"proposed_fix": {"description": "draft", "requires_approval": True, "dropped_keys": ["run"]}}
    advice = {"fix": {"summary": "s", "steps": [], "preconditions": [], "rollback": ""}, "runbooks": [],
              "runbook_refs": [], "contradictions": [], "dropped": [], "by": "model"}
    out = fix_advisor.apply(brief, advice)
    assert out["fix_advisor"]["commander_dropped_keys"] == ["run"] and out["proposed_fix"]["requires_approval"]
    plain = fix_advisor.apply({"proposed_fix": {"description": "draft"}}, advice)
    assert "commander_dropped_keys" not in plain["fix_advisor"]


# ------------------------------------------------------------ 8. citations in rejected alternatives
def test_evidence_ids_in_rejected_alternatives_that_are_not_on_the_board_are_recorded():
    alts = [{"candidate": "redis", "why": "ruled out by E9 and E42"}, {"candidate": "cart", "why": "flat (E1)"}]
    brief, problem = loop._gate(two_kinds(), finish("a", "cpu", "E1", "E2", alternatives_rejected=alts)["finish"])
    assert problem is None and brief["alternatives_rejected"] == alts       # the reasoning is kept as written
    assert brief["untraceable_citations"] == ["E9", "E42"]


@pytest.mark.parametrize("given,kept", [(None, []), ("redis: see E7", ["redis: see E7"]),
                                        ({"candidate": "redis"}, [{"candidate": "redis"}])])
def test_rejected_alternatives_are_always_a_list(given, kept):
    brief, problem = loop._gate(two_kinds(), finish("a", "cpu", "E1", "E2", alternatives_rejected=given)["finish"])
    assert problem is None and brief["alternatives_rejected"] == kept
    assert brief.get("untraceable_citations", []) == (["E7"] if given == "redis: see E7" else [])


# ------------------------------------------------------------ 9. the held-out stamp, one case per call
def heldout_cases(n=3):
    return [loaders.IncidentCase(f"ts-order-service_cpu/{i}", config.HELDOUT_SYSTEM, str(i), 0, Path("."),
                                 "ts-order-service", "cpu") for i in range(1, n + 1)]


def test_heldout_run_made_one_case_per_call_is_stamped_only_when_whole(tmp_path, monkeypatch):
    """scripts/local_eval.sh calls `cli eval --cases <one case>` per case. The
    stamp used to be written after the first call, which refused the second."""
    calls = []
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: calls.append(case.case_id) or {
        "status": "escalated", "reason": "r", "usage": {}})
    cases, out = heldout_cases(), tmp_path / "tt_run"
    stamp = out / evaluate.HELDOUT_STAMP
    for i, case in enumerate(cases, 1):
        evaluate.run_eval([case], lambda: None, out, whole_run=cases)
        assert stamp.exists() == (i == len(cases))
    assert calls == [c.case_id for c in cases]
    assert json.loads(stamp.read_text())["cases"] == [c.case_id for c in cases]
    rows = evaluate.run_eval(cases, lambda: None, out, whole_run=cases)    # the final summary pass
    assert len(rows) == 3 and len(calls) == 3
    with pytest.raises(RuntimeError, match="one-shot"):                    # the rule itself still holds
        evaluate.run_eval(cases[:1], lambda: None, tmp_path / "tt_again", whole_run=cases)


def test_a_smoke_run_on_part_of_the_heldout_set_does_not_burn_the_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: {"status": "escalated", "usage": {}})
    cases = heldout_cases()
    evaluate.run_eval(cases[:2], lambda: None, tmp_path / "smoke", whole_run=cases)     # --limit 2
    assert not (tmp_path / "smoke" / evaluate.HELDOUT_STAMP).exists()
    evaluate.run_eval(cases, lambda: None, tmp_path / "real", whole_run=cases)          # not blocked
    assert (tmp_path / "real" / evaluate.HELDOUT_STAMP).exists()


# ------------------------------------------------------------ 10. cli eval: --out anywhere, whole run passed
def test_cli_eval_accepts_an_out_folder_outside_the_repo_and_passes_the_whole_run(tmp_path, monkeypatch):
    cases = [loaders.IncidentCase(f"{s}_cpu/{r}", "online-boutique", r, 0, Path("."), s, "cpu")
             for s in ("aservice", "bservice") for r in ("1", "2")]
    seen = {}

    def fake_run_eval(part, make_model, out, max_usd, **kw):
        seen.update(part=part, out=out, **kw)
        (out / "summary.md").write_text("ok\n")

    monkeypatch.setattr(loaders, "list_cases", lambda system: cases)
    monkeypatch.setattr(evaluate, "run_eval", fake_run_eval)
    out = tmp_path / "scratch" / "lin"
    assert not out.is_relative_to(config.REPO_ROOT)
    args = ["cli", "eval", "--model", "mock", "--runs", "1", "--cases", "a*", "--out", str(out)]
    monkeypatch.setattr("sys.argv", args)
    logger = logging.getLogger("incident_commander")
    before = list(logger.handlers)
    try:
        cli.main()                                   # used to raise ValueError from relative_to
    finally:
        for h in [h for h in logger.handlers if h not in before]:
            logger.removeHandler(h)
            h.close()
    assert seen["out"] == out and (out / "eval.log").read_text().count(f"out={out}") == 1
    assert [c.case_id for c in seen["part"]] == ["aservice_cpu/1"]
    assert [c.case_id for c in seen["whole_run"]] == ["aservice_cpu/1", "bservice_cpu/1"]   # --runs, not --cases
    assert cli._shown(config.REPO_ROOT / "evals" / "results" / "x", config.REPO_ROOT) == "evals/results/x"


# ------------------------------------------------------------ 11. --hide metrics without ToT
def test_linear_diagnosis_with_metrics_hidden_is_marked_as_an_unchecked_fault_type(tmp_path, monkeypatch):
    """Without ToT nothing checks the fault type, and with metrics hidden nothing
    could: the brief and the summary say so, and the ToT/graph rows of the same
    ablation are explained as escalations by design."""
    monkeypatch.setattr(loop, "diagnose", lambda case, model, toolbox=None, tot=None: {
        "status": "diagnosed", "confidence": "high", "root_cause": {"service": "x", "fault_type": "cpu"},
        "usage": {}})
    evaluate.run_eval([FAKE], lambda: None, tmp_path / "m", hide=("metrics",))
    saved = json.loads((tmp_path / "m" / "x_cpu_1.json").read_text())
    assert "metrics were hidden" in saved["fault_type_unchecked"] and saved["confidence"] == "medium"
    md = (tmp_path / "m" / "summary.md").read_text()
    assert "- metrics hidden: no fault type's must-have signal can be checked." in md
    assert "every case is an escalation by design" in md and "ToT or graph ran in 0 of 1 such cases" in md
    evaluate.run_eval([FAKE], lambda: None, tmp_path / "l", hide=("logs",))
    assert "fault_type_unchecked" not in json.loads((tmp_path / "l" / "x_cpu_1.json").read_text())
    assert "- metrics hidden" not in (tmp_path / "l" / "summary.md").read_text()


# --- logs without a level column (the RE3 code-level fault set) ---

def _case_with_logs(tmp_path, header, rows):
    (tmp_path / "logs.csv").write_text("\n".join([header, *rows]) + "\n")
    return loaders.IncidentCase("emailservice_f1/1", "online-boutique-re3", "1", 1000, tmp_path, "emailservice", "f1")


def test_logs_without_a_level_column_are_searchable_and_implicate_nobody(tmp_path):
    ns = 1000 * 1_000_000_000
    case = _case_with_logs(tmp_path, "time,timestamp,container_name,message,pod_name,node_name", [
        f"999,{ns - 5},frontend,request complete,p1,n1",
        f"1001,{ns + 5},emailservice,Traceback (most recent call last),p2,n1",
        f"1002,{ns + 9},emailservice,sent,p2,n1"])
    df = loaders.load_logs(case, columns=["timestamp", "container_name", "message", "level"])
    assert list(df.columns) == ["timestamp", "container_name", "message", "level"]
    assert set(df["level"]) == {loaders.NO_LEVEL}

    hit = tools.log_search(case, query="Traceback")
    assert hit["total_matches"] == 1 and hit["levels_present"] == ["unknown"]
    assert hit["matches_by_service_level"] == {"emailservice/unknown": 1}
    assert hit["sample_after_alert"][0]["source_ref"] == "logs.csv#L3"
    json.dumps(hit)  # the tool result must serialize
    # a matched line with no severity is a mention, not a symptom: nobody is implicated
    assert loop._summarize("log_search", hit, {"query": "Traceback"})[3] == []

    errors = tools.log_search(case, level="error")
    assert errors["total_matches"] == 0 and errors["scope_lines"] == 3
    assert errors["levels_present"] == ["unknown"]
    kind, text, _, implicated = loop._summarize("log_search", errors, {"level": "error"})
    assert kind == "logs" and implicated == [] and "unknown" in text


def test_logs_with_a_level_column_load_as_before(tmp_path):
    case = _case_with_logs(tmp_path, "time,timestamp,container_name,message,level", [
        "1001,1000000000005,emailservice,boom,error"])
    df = loaders.load_logs(case, columns=["timestamp", "container_name", "message", "level"])
    assert list(df["level"]) == ["error"]
    assert tools.log_search(case, level="error")["matches_by_service_level"] == {"emailservice/error": 1}
