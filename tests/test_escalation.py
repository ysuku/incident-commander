"""Checkpoint 6.1 escalation handover, on all three paths (linear, linear + ToT,
graph): every escalated brief carries brief["escalation"] = {code, reason,
hypotheses, note}; "unfamiliar fault" has a reachable definition; an escalation
whose leaders are level names them; every ToT model call leaves a trace entry.

Most tests need no dataset: the evidence board is seeded with stub tool results
(`seed`), so the critic, the controller and the gate run on known numbers. The
ones on recorded incidents skip when RE2-OB is absent."""
import json
import logging
from pathlib import Path

import pytest

from incident_commander import config, evaluate, graph, loaders, loop
from incident_commander import signatures as sig
from incident_commander import tot as tot_mod
from incident_commander.mcp_server import ToolClient, build_server

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")

FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=100, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")
CODES = {"budget_exhausted", "unparseable", "tot_tie", "tot_no_branch",
         "tot_selected_gate_refused", "unfamiliar"}
CPU = dict(cpu=10, mem=1, socket=1, latency_50=0.5)     # a clean cpu signature
FLAT = dict(cpu=0, mem=0, socket=0, latency_50=0, latency_90=0)  # nothing moved
# no resource or latency series moved, but the service IS affected: its error
# series rose (metrics implicate it) and, with traces=True, so do its own spans
ODD = dict(FLAT, error=50)


class Scripted:
    """Replays replies (the last one repeats). A dict is sent as JSON, a str as is.
    `investigator` answers the ToT generator prompt instead, when given."""

    def __init__(self, *replies, investigator=None):
        self.replies, self.investigator, self.calls = replies, investigator, 0
        self.last_call = {}

    def complete(self, messages):
        self.calls += 1
        self.last_call = {"seconds": 0.1, "prompt_tokens": 10, "completion_tokens": 5,
                          "max_tokens": 100, "finish_reason": "stop"}
        if self.investigator and messages[0]["content"].startswith("You are an Investigator"):
            return json.dumps(self.investigator(self))
        self.i = getattr(self, "i", -1) + 1
        r = self.replies[min(self.i, len(self.replies) - 1)]
        return r if isinstance(r, str) else json.dumps(r)


def act(tool, cands=(), **args):
    return {"rationale": "x", "candidates": list(cands), "action": {"tool": tool, "args": args}}


def seeded(model=None, tot="heuristic"):
    """(toolbox, Run) on a case with no data: only the branch board is real."""
    tb = ToolClient(build_server(FAKE))
    tb.__enter__()
    return tb, loop.Run(FAKE, model or Scripted({}), tb, tot=tot)


def seed(run, svc, traces=None, **z):
    """Put a service's metric signature (and, with traces=True/False, a trace
    lookup that does / does not implicate it) on the board, as if the tools ran."""
    def put(tool, kind, ref, implicated, result):
        ev = run.pad.add(kind, f"stub {tool} for {svc}", ref, implicated)
        run.observations[ev.eid] = (tool, {"service": svc}, result)
        run.seen_calls[loop._call_key(run.toolbox, tool, {"service": svc})] = ev.eid
        return ev.eid
    devs = [{"service": svc, "metric": k.replace("_", "-"), "z": v} for k, v in z.items()]
    eids = [put("metric_query", "metrics", "metrics.csv", [svc] if any(v >= 3 for v in z.values()) else [],
                {"top_deviations": devs})]
    if traces is not None:
        side = {"n_after": 50, "err_rate_before": 0.0, "err_rate_after": 0.0}
        eids.append(put("trace_lookup", "traces", f"traces.csv:serviceName={svc}", [svc] if traces else [],
                        {"service": svc, "overall": side, "inbound": side}))
    return eids


def check_block(brief):
    """What every escalation must carry, whatever the path."""
    esc = brief["escalation"]
    assert set(esc) == {"code", "reason", "hypotheses", "note"} and esc["code"] in CODES
    assert esc["reason"] == brief["reason"] and esc["note"]
    assert isinstance(brief["candidates"], list)                 # old readers still work
    board = {e["eid"] for e in brief["evidence_board"]}
    for h in esc["hypotheses"]:
        assert {"service", "fault_type", "score", "summary", "evidence"} <= set(h)
        assert set(h["evidence"]) <= board                       # nothing cited that is not on the board
    assert evaluate.escalation_code(brief) == esc["code"]
    return esc


# ------------------------------------------------------------ 1. non-ToT escalations
@pytest.mark.parametrize("diagnose", [loop.diagnose, graph.diagnose])
def test_budget_spent_on_unusable_replies_is_not_called_a_budget_problem(diagnose):
    """Every reply asks for a tool that does not exist: the budget runs out with
    an empty board. The handover says so instead of 'budget' with nothing behind it."""
    with ToolClient(build_server(FAKE)) as tb:
        brief = diagnose(FAKE, Scripted(act("no_such_tool")), toolbox=tb)
    esc = check_block(brief)
    assert brief["status"] == "escalated" and brief["reason"] == "step budget (8) exhausted"
    assert esc["code"] == "unparseable" and esc["hypotheses"] == []
    assert "no usable evidence gathered" in esc["note"] and "unknown_tool" in esc["note"]


@pytest.mark.parametrize("diagnose", [loop.diagnose, graph.diagnose])
def test_three_unparseable_replies_escalate_with_the_unparseable_code(diagnose):
    with ToolClient(build_server(FAKE)) as tb:
        brief = diagnose(FAKE, Scripted("not json"), toolbox=tb)
    esc = check_block(brief)
    assert esc["code"] == "unparseable" and esc["hypotheses"] == []
    assert "no usable evidence gathered" in esc["note"]


def test_budget_escalation_ranks_hypotheses_from_the_board_without_new_tool_calls():
    tb, run = seeded()
    with tb:
        a = seed(run, "aservice", traces=False, **CPU)
        seed(run, "bservice", cpu=4, mem=20, socket=1)           # cpu and disk both partly fit
        run.pad.candidates = ["zservice: just a hunch"]
        n = len(run.pad.evidence)
        brief = run.escalate("step budget (8) exhausted")
    esc = check_block(brief)
    assert esc["code"] == "budget_exhausted" and len(run.pad.evidence) == n
    hyps = esc["hypotheses"]
    assert (hyps[0]["service"], hyps[0]["fault_type"]) == ("aservice", "cpu") and hyps[0]["evidence"] == a
    scores = [h["score"] for h in hyps if h["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    # the model's own candidate is kept, last and labelled: nothing on the board supports it
    assert hyps[-1]["service"] == "zservice" and hyps[-1]["fault_type"] is None
    assert "nothing on the board implicates it" in hyps[-1]["summary"]
    assert brief["candidates"] == ["zservice: just a hunch"]      # the free text is untouched


def test_evidence_that_implicates_nobody_gives_no_hypotheses_and_says_so():
    tb, run = seeded()
    with tb:
        run.pad.add("map", "dependencies: a->b", "traces.csv")
        esc = check_block(run.escalate("step budget (8) exhausted"))
    assert esc["code"] == "budget_exhausted" and esc["hypotheses"] == []
    assert "implicates no service" in esc["note"]


@needs_data
def test_linear_and_graph_hand_over_the_same_block_on_a_real_budget_escalation():
    c = loaders.load_case("checkoutservice_cpu/1")
    script = (act("metric_query", service="checkoutservice"), act("service_map"),
              act("metric_query", service="frontend"), act("no_such_tool"))
    lin, gr = loop.diagnose(c, Scripted(*script)), graph.diagnose(c, Scripted(*script), tot=None)
    esc = check_block(lin)
    assert esc["code"] == "budget_exhausted" and check_block(gr) == esc
    assert (esc["hypotheses"][0]["service"], esc["hypotheses"][0]["fault_type"]) == ("checkoutservice", "cpu")


@needs_data
def test_a_diagnosed_brief_has_no_escalation_block():
    c = loaders.load_case("emailservice_cpu/1")
    finish = {"rationale": "x", "action": {"finish": {
        "root_cause_service": "emailservice", "fault_type": "cpu", "supporting_evidence": ["E1", "E2"],
        "proposed_fix": {"description": "x", "destructive": True}}}}
    brief = loop.diagnose(c, Scripted(act("metric_query", service="emailservice"),
                                      act("trace_lookup", service="emailservice"), finish))
    assert brief["status"] == "diagnosed" and "escalation" not in brief
    assert evaluate.escalation_code(brief) == ""


# ------------------------------------------------------------ 2. unfamiliar fault
def feats(**z) -> sig.Features:
    f = sig.Features("svc", metrics_scoped="E1")
    f.z = {k.replace("_", "-"): (v, "E1") for k, v in z.items()}
    return f


def test_unfamiliar_means_only_unobservable_survivors_not_untested_ones():
    flat = feats(**FLAT)
    alive = [(f, s) for f, s, vs in sig.rank_faults(flat) if not sig.must_contradicted(vs)]
    assert [f for f, _ in alive] == ["disk"] and alive[0][1] <= 0   # 6.1's "every branch pruned" cannot happen
    assert sig.unfamiliar(flat)
    assert not sig.unfamiliar(sig.Features("svc"))                 # nothing tested yet
    assert not sig.unfamiliar(feats(**CPU))                        # cpu fits
    assert not sig.unfamiliar(feats(cpu=0, mem=10, socket=0, latency_50=0))   # mem must-have in the grey zone
    assert not sig.unfamiliar(feats(cpu=1, mem=30, socket=1, latency_50=0))   # mem (and disk) fit


def test_flat_service_escalates_as_unfamiliar_and_disk_is_not_the_lead():
    tb, run = seeded()
    with tb:
        seed(run, "svc", traces=True, **ODD)
        brief = run.tot_brief("T2 test", "svc", None)
    esc = check_block(brief)
    assert brief["tot"]["unfamiliar"] is True and brief["tot"]["status"] == "escalated"
    assert esc["code"] == "unfamiliar" and brief["reason"].startswith("unfamiliar fault:")
    assert "svc: disk" in brief["reason"] and "not support" in brief["reason"]
    # disk is still on the board (never contradicted), but is not presented first
    assert [b["fault_type"] for b in brief["tot"]["ranked"] if b["status"] != "pruned"] == ["disk"]
    assert "unfamiliar" in brief["candidates"][0] and "disk" not in brief["candidates"][0]
    lead, rest = esc["hypotheses"][0], esc["hypotheses"][1:]
    assert lead["fault_type"] == "unfamiliar" and lead["evidence"]
    assert [h["fault_type"] for h in rest] == ["disk"] and loop.UNFAMILIAR_TAG in rest[0]["summary"]
    # a brief saved without the block is still coded from the ToT result
    old = {k: v for k, v in brief.items() if k != "escalation"}
    assert evaluate.escalation_code(old) == "unfamiliar"


def test_a_real_candidate_on_the_rival_service_stops_the_unfamiliar_call():
    tb, run = seeded()
    with tb:
        seed(run, "svc", traces=True, **ODD)
        seed(run, "other", traces=False, **CPU)
        brief = run.tot_brief("T2 test", "svc", "other")
    assert brief["tot"]["unfamiliar"] is False and brief["escalation"]["code"] == "tot_no_branch"
    assert brief["escalation"]["hypotheses"][0]["service"] == "other"


def test_graph_tot_subgraph_agrees_on_unfamiliar():
    def result(via_graph):
        tb, run = seeded()
        with tb:
            seed(run, "svc", traces=True, **ODD)
            ctx, gen = run.tot_context(), tot_mod.HeuristicGenerator()
            if not via_graph:
                return tot_mod.run_tot(ctx, "svc", None, gen, "t")
            app, box = graph.build_tot_graph()
            box.update(ctx=ctx, generator=gen, trace=[])
            return app.invoke({"trigger": "t", "suspect": "svc", "rival": None,
                               "depth": 0, "expansions": 0, "progressed": True})["result"]
    ref, got = result(False), result(True)
    assert ref["unfamiliar"] is got["unfamiliar"] is True
    assert loop.tot_code(ref) == loop.tot_code(got) == "unfamiliar"
    assert loop.tot_reason(ref) == loop.tot_reason(got)


# ------------------------------------------------------------ 3 + 6. ties and two services
def test_two_services_with_equal_confirmed_signals_are_a_tie_naming_both():
    """5.1 feedback, multi-service probe. Two different services each have a fully
    confirmed cpu signature on two telemetry kinds (metrics + traces), so both
    would pass the gate. Equal (score, kinds): the system does NOT pick one. It
    escalates as a tie and names both, service and fault.

    When they do not tie (next test) the higher (score, kinds) is selected and
    diagnosed as the single root cause; the other service appears only under
    alternatives_rejected and in brief["tot"]["ranked"]. So a real two-service
    incident with unequal evidence is reported as one root cause: the brief has no
    "second fault" field. That is current behaviour, pinned here, not changed."""
    tb, run = seeded()
    with tb:
        seed(run, "aservice", traces=True, **CPU)
        seed(run, "bservice", traces=True, **CPU)
        brief = run.tot_brief("T1 test", "aservice", "bservice")
    esc = check_block(brief)
    assert brief["status"] == "escalated" and brief["tot"]["status"] == "tied"
    assert esc["code"] == "tot_tie"
    assert brief["reason"].startswith("tie:")
    assert "aservice: cpu" in brief["reason"] and "bservice: cpu" in brief["reason"]
    top = esc["hypotheses"][:2]                                    # tie members first
    assert {(h["service"], h["fault_type"], h["status"]) for h in top} == {
        ("aservice", "cpu", "tied"), ("bservice", "cpu", "tied")}
    assert top[0]["score"] == top[1]["score"] and top[0]["kinds"] == top[1]["kinds"] == ["metrics", "traces"]


def test_two_services_with_unequal_signals_report_one_root_cause():
    """The documented other half: bservice's traces do not implicate it, so it
    scores lower and aservice alone is diagnosed. bservice's (real) cpu signal is
    visible only as a rejected alternative."""
    tb, run = seeded()
    with tb:
        seed(run, "aservice", traces=True, **CPU)
        seed(run, "bservice", traces=False, **CPU)
        brief = run.tot_brief("T1 test", "aservice", "bservice")
    assert brief["status"] == "diagnosed" and "escalation" not in brief
    assert brief["root_cause"] == {"service": "aservice", "fault_type": "cpu"}
    assert any(a["candidate"] == "bservice: cpu" for a in brief["alternatives_rejected"])


def test_level_leaders_that_fail_the_gate_are_named_without_being_called_a_tie():
    """Same fault on two services, metrics only: nobody passes the two-kinds gate,
    so status stays 'escalated' (not 'tied'), but the reason names both leaders."""
    tb, run = seeded()
    with tb:
        seed(run, "aservice", traces=False, **CPU)
        seed(run, "bservice", traces=False, **CPU)
        brief = run.tot_brief("T2 test", "aservice", "bservice")
    esc = check_block(brief)
    t = brief["tot"]
    assert t["status"] == "escalated" and t["tied"] == [] and esc["code"] == "tot_no_branch"
    assert [(b["service"], b["fault_type"]) for b in t["co_leaders"]] == [("aservice", "cpu"), ("bservice", "cpu")]
    assert brief["reason"].startswith("tot: no branch passed the two-kinds gate; leaders level: ")
    assert "aservice: cpu or bservice: cpu" in brief["reason"]
    assert evaluate.tot_outcome(FAKE, brief).startswith("escalated")   # the old reading of status holds


def test_co_leaders_compare_score_and_kinds_like_decide():
    b = lambda bid, score, kinds, status="open": {"branch_id": bid, "score": score, "kinds": kinds, "status": status}
    assert tot_mod.co_leaders([b("B1", 3, ["metrics"]), b("B2", 3, ["metrics", "traces"])]) == []  # kinds differ
    assert tot_mod.co_leaders([b("B1", 3, ["metrics"]), b("B2", 3, ["metrics"], "pruned")]) == []  # pruned is out
    assert len(tot_mod.co_leaders([b("B1", 3, ["metrics"]), b("B2", 3, ["traces"]), b("B3", 1, [])])) == 2


def test_gate_refused_selection_has_its_own_code():
    """ToT selects aservice, but its own trace lookup blamed the caller (shared
    cause), so the gate refuses: the handover says which branch and why."""
    tb, run = seeded()
    with tb:
        seed(run, "aservice", traces=True, **CPU)
        # a second per-service trace entry that puts the slowdown on the caller
        run.pad.add("traces", "stub", "traces.csv:serviceName=aservice", ["frontend"])
        brief = run.tot_brief("T1 test", "aservice", None)
    esc = check_block(brief)
    assert esc["code"] == "tot_selected_gate_refused" and brief["reason"].startswith("tot selected aservice: cpu")
    assert esc["hypotheses"][0]["status"] == "selected"


# ------------------------------------------------------------ 4. per-step logging in ToT
def test_generator_calls_that_give_no_test_are_logged_and_counted(caplog):
    tb, run = seeded(Scripted({}, investigator=lambda m: {"tool": None}), tot="model")
    with tb, caplog.at_level(logging.INFO, logger="incident_commander"):
        seed(run, "aservice", traces=False, **CPU)
        seed(run, "bservice", traces=False, **CPU)
        brief = run.done(run.tot_brief("T2 test", "aservice", "bservice"))
    steps = [e for e in brief["investigation"] if e.get("phase") == "tot"]
    assert steps and all(e["outcome"] == "tot_no_test" and e["model_call"] for e in steps)
    assert all("no test can add information" in e["generator"] for e in steps)
    assert [e["step"] for e in brief["investigation"]] == list(range(1, len(steps) + 1))
    # every model call is in the trace: nothing is paid for and not written down
    assert sum(1 for e in brief["investigation"] if e.get("model_call")) == brief["usage"]["model_calls"]
    assert brief["usage"]["model_calls"] == brief["tot"]["generator_calls"] <= tot_mod.MAX_EXPANSIONS
    assert brief["tot"]["expansions"] == 0                          # a call with no test is not an expansion
    lines = [r.getMessage() for r in caplog.records]
    assert any(l.startswith("tot B1 aservice: cpu") and "model 0.1s" in l and "in 10 tok" in l for l in lines)


def test_observer_reports_tool_seconds_without_changing_what_it_returns():
    class Box:
        tools = []
        def call(self, name, args):
            if name == "boom":
                raise ValueError("bad")
            return {"edges": {"a": ["b"]}, "source_ref": "traces.csv"}
    pad = loop.Scratchpad(alert="x")
    observe = loop.make_observer(pad, Box(), {}, {})
    assert observe("service_map", {}, "t") == "E1" and isinstance(observe.last["tool_seconds"], float)
    assert observe("service_map", {}, "t") == "E1" and observe.last["tool_seconds"] is None   # from the board
    assert observe("boom", {}, "t") is None and observe.last["tool_seconds"] is None


@needs_data
def test_model_fallback_steps_keep_their_model_call_and_tool_time():
    c = loaders.load_case("emailservice_delay/1")
    model = Scripted(act("metric_query", service="emailservice"),
                     act("log_search", cands=["emailservice: delay"], level="error"),
                     act("service_map", cands=["emailservice: delay"]), "not json at all")
    brief = loop.diagnose(c, model, tot="model")
    steps = [e for e in brief["investigation"] if e.get("phase") == "tot"]
    assert steps and all("fallback" in e["generator"] and e["model_call"] for e in steps)
    assert any("tool_seconds" in e for e in steps if e["outcome"] == "tot_test")
    assert sum(1 for e in brief["investigation"] if e.get("model_call")) == brief["usage"]["model_calls"]
    if brief["status"] == "diagnosed":                              # steps now counts the ToT model calls too
        assert brief["steps"] == len(brief["investigation"])


# ------------------------------------------------------------ 5. budget
def _busy_investigator(model):
    """Always proposes a call that is not on the board yet, so every expansion is used."""
    n = model.calls
    return {"rationale": "x", "tool": "metric_query", "predicted": "x",
            "args": {"service": "emailservice", "metric": "cpu", "top": 20 + n}}


@needs_data
def test_model_calls_stay_within_the_commander_and_tot_budgets():
    """6.1 'step budget': the Commander has MAX_STEPS turns, ToT at most
    MAX_EXPANSIONS generator calls, the Fix Advisor one. Worst case scripted here:
    seven Commander turns without a finish, then a ToT whose generator never stops."""
    c = loaders.load_case("emailservice_mem/1")
    turns = [act("metric_query", service="emailservice"), act("trace_lookup", service="emailservice"),
             act("service_map"), act("metric_query", service="frontend"),
             act("metric_query", service="checkoutservice"), act("trace_lookup", service="frontend"),
             act("metric_query", service="cartservice"),
             {"rationale": "x", "action": {"finish": {
                 "root_cause_service": "emailservice", "fault_type": "mem",
                 "supporting_evidence": ["E1", "E2"], "proposed_fix": {"description": "x"}}}}]
    for diagnose in (loop.diagnose, graph.diagnose):
        brief = diagnose(c, Scripted(*turns, investigator=_busy_investigator), tot="model")
        assert "tot" in brief and brief["tot"]["generator_calls"] >= 1
        assert brief["tot"]["generator_calls"] <= tot_mod.MAX_EXPANSIONS
        assert brief["usage"]["model_calls"] <= loop.MAX_STEPS + tot_mod.MAX_EXPANSIONS + 1
        assert sum(1 for e in brief["investigation"] if e.get("model_call")) + int(
            "fix_advisor" in brief) >= brief["usage"]["model_calls"]


def test_generator_calls_are_bounded_even_when_none_yields_a_test():
    """A no-test reply is not an expansion, so the cap on expansions alone would
    not bound it; the beam and depth limits do."""
    model = Scripted({}, investigator=lambda m: {"tool": None})
    tb, run = seeded(model, tot="model")
    with tb:
        for s in ("aservice", "bservice"):
            seed(run, s, traces=False, cpu=10, mem=20, socket=1, latency_50=5)   # several live roots
        run.tot_brief("T2 test", "aservice", "bservice")
    assert 1 <= model.calls <= tot_mod.MAX_EXPANSIONS


def test_signature_docstring_no_longer_claims_only_contradiction_prunes():
    assert "Only \"contradicted\" lowers a score or prunes" not in sig.__doc__
    assert "PRUNE_GAP" in sig.__doc__
