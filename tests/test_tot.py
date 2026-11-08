"""Tree-of-Thought tests (Checkpoint 4.1): the critic, the MCP branch state, and
both triggers end to end on real cases."""
import json
from pathlib import Path

import pytest

from incident_commander import config, evaluate, loaders, loop
from incident_commander import signatures as sig
from incident_commander import tot as tot_mod
from incident_commander.mcp_server import BRANCH_TOOLS, ToolClient, build_server

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")
FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=0, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")


def feats(**z) -> sig.Features:
    """Features from a scoped metric query: every series not given is absent."""
    f = sig.Features("svc", metrics_scoped="E1")
    f.z = {k.replace("_", "-"): (v, "E1") for k, v in z.items()}
    return f


def verdict(fault, f, name):
    return next(v for v in sig.judge(fault, f) if v.signal == name)


# ---------------------------------------------------------------- critic
def test_delay_and_loss_are_told_apart_by_resource_pressure():
    delay = feats(cpu=0.4, mem=0.1, socket=0.0, latency_50=300)
    loss = feats(cpu=-0.5, mem=41, socket=87, latency_50=770)
    assert sig.rank_faults(delay)[0][0] == "delay"
    assert sig.must_contradicted(sig.judge("delay", loss))  # resources not flat: prune delay
    assert verdict("loss", loss, "conn_pressure").verdict == sig.CONFIRMED


def test_unobservable_never_contradicts():
    """4.1 risk: diskio is missing or unusable, so disk must never be pruned for it."""
    f = feats(cpu=230, mem=231, socket=6.6, diskio=9.8e18)
    v = verdict("disk", f, "diskio_up")
    assert v.verdict == sig.UNOBSERVABLE
    assert not sig.must_contradicted(sig.judge("disk", f))
    assert verdict("delay", feats(cpu=1, mem=1, socket=0), "latency_up").verdict == sig.UNOBSERVABLE


def test_untested_until_a_scoped_query_runs():
    f = sig.Features("svc")  # nothing on the board yet
    assert {v.verdict for v in sig.judge("cpu", f)} == {sig.UNTESTED}


def test_score_rewards_two_kinds_and_punishes_contradiction():
    V = lambda kind, v: sig.Verdict("s", kind, v, "", "E1", False, "")
    assert sig.score([V("metrics", sig.CONFIRMED), V("traces", sig.CONFIRMED)]) == 3
    assert sig.score([V("metrics", sig.CONFIRMED), V("metrics", sig.CONTRADICTED)]) == -1
    assert sig.score([V("metrics", sig.UNOBSERVABLE), V("metrics", sig.INCONCLUSIVE)]) == 0


def test_ceiling_counts_untested_signals_as_confirmed():
    V = lambda kind, v: sig.Verdict("s", kind, v, "", "E1", False, "")
    vs = [V("metrics", sig.CONFIRMED), V("traces", sig.UNTESTED)]
    assert sig.score(vs) == 1 and sig.ceiling(vs) == 3       # +1 signal, +1 second kind
    assert sig.ceiling([V("metrics", sig.CONTRADICTED)]) == -2


@needs_data
def test_a_less_tested_branch_is_not_beaten_by_a_more_tested_rival():
    """Qwen run, productcatalogservice_mem/1: the rival's traces were already on the
    board, the suspect's were not, so the rival led 5-3 and was selected (wrong
    service). Compared on ceilings, the suspect must be tested first; here it ties."""
    c = loaders.load_case("productcatalogservice_mem/1")
    with ToolClient(build_server(c)) as tb:
        pad, seen, obs = loop.Scratchpad(alert=c.case_id), {}, {}
        observe = loop.make_observer(pad, tb, seen, obs)
        observe("metric_query", {"service": "recommendationservice"}, "setup")
        observe("trace_lookup", {"service": "recommendationservice"}, "setup")
        r = tot_mod.run_tot(tot_mod.TotContext(pad, tb, observe, obs, lambda n, a: False),
                            "productcatalogservice", "recommendationservice",
                            tot_mod.HeuristicGenerator(), "test")
    assert r["status"] != "selected" or r["selected"]["service"] == "productcatalogservice"
    assert any(b["service"] == "productcatalogservice" and b["fault_type"] == "mem"
               for b in r["tied"])


# ---------------------------------------------------------------- state manager (MCP)
def test_branch_tools_are_on_the_server_but_not_in_the_react_prompt():
    with ToolClient(build_server(FAKE)) as tc:
        assert set(BRANCH_TOOLS) <= set(tc.names)
        assert len(tc.diagnostic) == 5
        assert not any(n in tc.describe() for n in BRANCH_TOOLS)
        b = tc.call("open_branch", {"service": "a", "fault_type": "cpu"})
        assert tc.call("open_branch", {"service": "aservice", "fault_type": "cpu"})["branch_id"] == b["branch_id"]
        vs = [vars(sig.Verdict("cpu_up", "metrics", sig.CONFIRMED, "cpu z=9", "E1", True, ""))]
        assert tc.call("record_result", {"branch_id": b["branch_id"], "verdicts": vs})["score"] == 1
        tc.call("close_branch", {"branch_id": b["branch_id"], "status": "pruned", "reason": "x"})
        assert tc.call("branch_status", {})["branches"][0]["status"] == "pruned"


# ---------------------------------------------------------------- end to end
class Scripted:
    """Plays a fixed list of replies, then repeats the last."""
    def __init__(self, replies):
        self.replies, self.calls = replies, 0

    def complete(self, messages):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return json.dumps(r)


def act(tool, cands=(), **args):
    return {"rationale": "x", "candidates": list(cands), "action": {"tool": tool, "args": args}}


@needs_data
def test_t1_wrong_fault_that_passes_the_gate_is_corrected_by_tot():
    """The service_only failure: right service, gate passes, wrong fault type."""
    c = loaders.load_case("emailservice_delay/1")
    model = Scripted([act("metric_query", service="emailservice"),
                      act("trace_lookup", service="emailservice"),
                      {"rationale": "x", "action": {"finish": {
                          "root_cause_service": "emailservice", "fault_type": "cpu",
                          "supporting_evidence": ["E1", "E2"],
                          "proposed_fix": {"description": "x", "destructive": False}}}}])
    linear = loop.diagnose(c, Scripted(model.replies))
    assert linear["root_cause"]["fault_type"] == "cpu"          # what the linear loop would ship
    brief = loop.diagnose(c, model, tot="heuristic")
    assert brief["status"] == "diagnosed" and brief["decided_by"] == "tot"
    assert brief["root_cause"] == {"service": "emailservice", "fault_type": "delay"}
    assert brief["proposed_fix"]["requires_approval"] is True
    assert any("cpu" in a["candidate"] for a in brief["alternatives_rejected"]) or brief["tot"]["trigger"].startswith("T1 cpu")


@needs_data
def test_t2_no_second_kind_escalates_with_ranked_branches():
    """cpu on checkout: traces stay flat, so no branch can pass the gate. ToT must
    escalate, not guess, with the true fault ranked first."""
    c = loaders.load_case("checkoutservice_cpu/1")
    model = Scripted([act("metric_query", top=3),
                      act("service_map", cands=["checkoutservice: cpu saturation"]),
                      act("log_search", cands=["checkoutservice: cpu saturation"], level="error")])
    brief = loop.diagnose(c, model, tot="heuristic")
    assert brief["status"] == "escalated"
    assert brief["tot"]["trigger"].startswith("T2")
    assert brief["candidates"][0].startswith("checkoutservice: cpu")
    assert evaluate.tot_outcome(c, brief) == "escalated_truth_first"
    assert model.calls == 3  # heuristic generator: no extra model calls


@needs_data
def test_clear_finish_skips_tot():
    from incident_commander.model import MockModel
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, MockModel(), tot="heuristic")
    assert brief["status"] == "diagnosed" and "tot" not in brief


@needs_data
def test_mem_vs_disk_is_reported_as_a_tie_not_a_guess():
    from incident_commander.model import MockModel
    c = loaders.load_case("emailservice_mem/1")
    brief = loop.diagnose(c, MockModel(), tot="heuristic")
    assert brief["status"] == "escalated" and brief["reason"].startswith("tie:")
    assert evaluate.tot_outcome(c, brief) == "tie_with_truth"


@needs_data
def test_model_generator_falls_back_on_an_unusable_reply():
    c = loaders.load_case("emailservice_delay/1")
    replies = [act("metric_query", service="emailservice"),
               act("log_search", cands=["emailservice: delay"], level="error"),
               act("service_map", cands=["emailservice: delay"]),
               "not json at all"]

    class Model(Scripted):
        def complete(self, messages):
            r = self.replies[min(self.calls, len(self.replies) - 1)]
            self.calls += 1
            return r if isinstance(r, str) else json.dumps(r)

    brief = loop.diagnose(c, Model(replies), tot="model")
    tests = [e for e in brief["investigation"] if e.get("phase") == "tot"]
    assert tests and all("fallback" in e["generator"] for e in tests)
    assert brief["usage"]["model_calls"] == 3 + brief["tot"]["generator_calls"]
