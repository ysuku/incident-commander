"""Multi-agent graph tests (Checkpoint 5.1): role-scoped tools, the spine, the
Fix Advisor, and the ToT subgraph checked against the one-function controller."""
import json
from pathlib import Path

import pytest

from incident_commander import config, fix_advisor, graph, loaders, loop
from incident_commander import tot as tot_mod
from incident_commander.mcp_server import BRANCH_TOOLS, ToolClient, ToolError, build_server
from incident_commander.model import MockModel
from incident_commander.roles import COMMANDER, FIX_ADVISOR, INVESTIGATOR

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")
FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=0, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")


class Scripted:
    """Plays a fixed list of Commander replies, then repeats the last; answers the
    Fix Advisor with `fix_reply` (a dict, or a raw string to simulate garbage)."""

    def __init__(self, replies, fix_reply=None):
        self.replies, self.calls, self.fix_reply = replies, 0, fix_reply
        self.prompts = []

    def complete(self, messages):
        self.calls += 1
        self.prompts.append(messages)
        if messages[0]["content"].startswith("You are the Fix Advisor"):
            r = self.fix_reply if self.fix_reply is not None else {"fix": {"summary": "scripted fix"}}
            return r if isinstance(r, str) else json.dumps(r)
        r = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        return json.dumps(r)


def act(tool, cands=(), **args):
    return {"rationale": "x", "candidates": list(cands), "action": {"tool": tool, "args": args}}


def finish(service, fault, *eids):
    return {"rationale": "x", "action": {"finish": {
        "root_cause_service": service, "fault_type": fault, "supporting_evidence": list(eids),
        "proposed_fix": {"description": "commander's draft", "destructive": False}}}}


# ---------------------------------------------------------------- roles and tool scoping
def test_roles_see_only_their_tools_and_the_server_enforces_it():
    with ToolClient(build_server(FAKE)) as tc:
        cmd, inv, fix = COMMANDER.view(tc), INVESTIGATOR.view(tc), FIX_ADVISOR.view(tc)
        assert "runbook_lookup" not in cmd.names and "runbook_lookup" not in cmd.describe()
        assert set(cmd.diagnostic) == set(inv.diagnostic) == set(COMMANDER.tools)
        assert fix.names == ["runbook_lookup"] and fix.diagnostic == ["runbook_lookup"]
        with pytest.raises(ToolError, match="not available to this role"):
            cmd.call("runbook_lookup", {"symptom": "x"})
        with pytest.raises(ToolError, match="not available to this role"):
            fix.call("metric_query", {})
        ctrl = tc.scoped(INVESTIGATOR.tools + BRANCH_TOOLS)
        assert set(BRANCH_TOOLS) <= set(ctrl.names) and "runbook_lookup" not in ctrl.names
        assert not any(n in ctrl.describe() for n in BRANCH_TOOLS)


@needs_data
def test_commander_cannot_reach_runbooks_even_if_it_asks():
    """3.1's lesson in code: a runbook can never enter the diagnosis context."""
    c = loaders.load_case("checkoutservice_cpu/1")
    model = Scripted([act("runbook_lookup", symptom="cpu"), act("metric_query", top=3)])
    brief = graph.diagnose(c, model, max_steps=2, tot="heuristic")
    outcomes = [e["outcome"] for e in brief["investigation"]]
    assert outcomes[0] == "unknown_tool" and "runbook" not in json.dumps(brief["evidence_board"])


# ---------------------------------------------------------------- the spine
@needs_data
def test_graph_diagnoses_like_the_linear_agent_then_advises_and_waits_for_a_human():
    # emailservice, not checkoutservice: a cpu fault leaves checkout's own spans flat,
    # so metrics + its own traces cannot pass the 6.1 gate there
    c = loaders.load_case("emailservice_cpu/1")
    linear = loop.diagnose(c, MockModel())
    brief = graph.diagnose(c, MockModel(), tot="heuristic")
    assert brief["status"] == "diagnosed" and brief["architecture"] == "graph"
    assert brief["root_cause"] == linear["root_cause"] == {"service": "emailservice", "fault_type": "cpu"}
    assert brief["path"] == ["commander", "act", "commander", "act", "commander", "gate",
                             "fix_advisor", "human_approval"]
    fix = brief["proposed_fix"]
    assert fix["requires_approval"] is True and fix["advised_by"] == "model"
    assert fix["steps"] and fix["runbook_refs"]          # the mock cites R1 when a runbook matched
    assert brief["approval"]["state"] == "pending"
    assert brief["fix_advisor"]["commander_draft"].startswith("Raise cpu limit")
    assert brief["roles"]["Fix Advisor"] == ["runbook_lookup"]
    # the Fix Advisor's one tool call and one model call are in the run totals
    assert brief["usage"]["model_calls"] == linear["usage"]["model_calls"] + 1


@needs_data
def test_gate_rejection_loops_back_to_the_commander_then_escalates_on_budget():
    c = loaders.load_case("checkoutservice_cpu/1")
    model = Scripted([act("metric_query", top=3), finish("checkoutservice", "cpu", "E1")])
    brief = graph.diagnose(c, model, max_steps=3, tot="heuristic")
    assert brief["status"] == "escalated" and "budget" in brief["reason"]
    assert brief["path"] == ["commander", "act", "commander", "gate", "commander", "gate",
                             "commander", "escalate"]
    assert brief["proposed_fix"] is None and "fix_advisor" not in brief
    assert any("finish rejected" in n for n in brief["notes"])


# ---------------------------------------------------------------- the Fix Advisor
@needs_data
def test_fix_advisor_sees_only_the_accepted_diagnosis_and_its_cited_evidence():
    c = loaders.load_case("emailservice_cpu/1")
    model = Scripted([act("metric_query", top=3), act("service_map", cands=["emailservice: cpu", "redis: cache"]),
                      act("trace_lookup", service="emailservice"),
                      finish("emailservice", "cpu", "E1", "E3")],
                     fix_reply={"rationale": "r", "fix": {"summary": "s", "steps": ["a"]},
                                "runbook_refs": ["R1", "R9"],
                                "contradictions": [{"runbook": "R1", "step": "restart", "evidence": "E2", "why": "x"},
                                                   {"runbook": "R1", "step": "scale", "evidence": "E1", "why": "cpu"}]})
    brief = graph.diagnose(c, model, tot="heuristic")
    assert brief["status"] == "diagnosed"
    advisor_prompt = next(m for m in model.prompts if m[0]["content"].startswith("You are the Fix Advisor"))
    shown = advisor_prompt[1]["content"]
    assert "E1 " in shown and "E3 " in shown and "E2 " not in shown      # only the cited evidence
    assert "CANDIDATES" not in shown and "redis" not in shown             # no Commander context
    assert "runbook_lookup" in shown or "RUNBOOKS RETRIEVED" in shown
    fa = brief["fix_advisor"]
    # 6.1: a flagged step that is not in R1's text is kept, with a note saying so
    assert [{k: v for k, v in f.items() if k != "note"} for f in fa["contradictions"]] == [
        {"runbook": "R1", "step": "scale", "evidence": "E1", "why": "cpu"}]
    assert any("E2" in d for d in fa["dropped"]) and any("R9" in d for d in fa["dropped"])
    assert brief["proposed_fix"]["contradictions"] == fa["contradictions"]
    assert brief["approval"]["flags_for_reviewer"] == 1


@needs_data
def test_unusable_fix_advice_falls_back_to_the_template_and_still_needs_approval():
    c = loaders.load_case("emailservice_cpu/1")
    model = Scripted([act("metric_query", top=3), act("trace_lookup", service="emailservice"),
                      finish("emailservice", "cpu", "E1", "E2")], fix_reply="not json")
    brief = graph.diagnose(c, model, tot="heuristic")
    fix = brief["proposed_fix"]
    assert fix["advised_by"].startswith("template") and fix["requires_approval"] is True
    assert fix["description"].startswith("Raise the CPU limit")
    assert fix["contradictions"] == [] and fix["model_labeled_destructive"] is False


def test_fix_advice_validation_without_data():
    """Pure code: references the model invents are dropped, never shown as traced."""
    class Tools:
        def call(self, name, args):
            assert name == "runbook_lookup"
            return [{"alert": "A", "kind": "action", "distance": 0.5, "source_ref": "r/A.md#action", "text": "do x"}]

    class M:
        last_call = {"seconds": 0.1}
        def complete(self, messages):
            return json.dumps({"fix": {"summary": "s"}, "runbook_refs": ["R1", "R2"],
                               "contradictions": [{"runbook": "R2", "step": "a", "evidence": "E1", "why": "w"}]})

    brief = {"root_cause": {"service": "svc", "fault_type": "mem"},
             "supporting_evidence": [{"eid": "E1", "kind": "metrics", "summary": "m", "source_ref": "s"}],
             "proposed_fix": {"model_labeled_destructive": True, "requires_approval": True}}
    advice = fix_advisor.advise(brief, Tools(), M())
    assert advice["runbook_refs"] == ["R1"] and advice["contradictions"] == [] and len(advice["dropped"]) == 2
    assert "memory" in advice["query"]
    out = fix_advisor.apply(brief, advice)
    assert out["proposed_fix"]["runbook_refs"] == ["r/A.md#action"]
    assert out["proposed_fix"]["requires_approval"] is True


# ---------------------------------------------------------------- the ToT subgraph
@needs_data
def test_t1_ambiguous_finish_goes_through_tot_then_the_gate_then_the_advisor():
    """Same scenario as test_tot's T1 case, on the graph: wrong fault corrected."""
    c = loaders.load_case("emailservice_delay/1")
    model = Scripted([act("metric_query", service="emailservice"), act("trace_lookup", service="emailservice"),
                      finish("emailservice", "cpu", "E1", "E2")])
    brief = graph.diagnose(c, model, tot="heuristic")
    assert brief["status"] == "diagnosed" and brief["decided_by"] == "tot"
    assert brief["root_cause"] == {"service": "emailservice", "fault_type": "delay"}
    assert brief["path"][-5:] == ["gate", "tot", "gate", "fix_advisor", "human_approval"]
    assert brief["tot"]["trigger"].startswith("T1")
    assert brief["proposed_fix"]["requires_approval"] is True


@needs_data
def test_t2_without_a_second_kind_escalates_with_ranked_branches():
    c = loaders.load_case("checkoutservice_cpu/1")
    model = Scripted([act("metric_query", top=3), act("service_map", cands=["checkoutservice: cpu saturation"]),
                      act("log_search", cands=["checkoutservice: cpu saturation"], level="error")])
    brief = graph.diagnose(c, model, tot="heuristic")
    assert brief["status"] == "escalated" and brief["tot"]["trigger"].startswith("T2")
    assert brief["path"][-2:] == ["tot", "escalate"]
    assert brief["candidates"][0].startswith("checkoutservice: cpu")
    assert "fix_advisor" not in brief     # the advisor never runs without an accepted diagnosis


@needs_data
@pytest.mark.parametrize("case_id,suspect,rival", [
    ("emailservice_delay/1", "emailservice", None),
    ("emailservice_mem/1", "emailservice", None),
    ("productcatalogservice_mem/1", "productcatalogservice", "recommendationservice"),
])
def test_tot_subgraph_matches_the_one_function_controller(case_id, suspect, rival):
    """The LangGraph port must make the same decisions as tot.run_tot."""
    c = loaders.load_case(case_id)

    def setup(tc):
        pad, seen, obs = loop.Scratchpad(alert=c.case_id), {}, {}
        observe = loop.make_observer(pad, tc, seen, obs)
        return tot_mod.TotContext(pad, tc, observe, obs, lambda n, a: False)

    with ToolClient(build_server(c)) as tc:
        ref = tot_mod.run_tot(setup(tc), suspect, rival, tot_mod.HeuristicGenerator(), "t")
    with ToolClient(build_server(c)) as tc:
        app, box = graph.build_tot_graph()
        box.update(ctx=setup(tc), generator=tot_mod.HeuristicGenerator(), trace=[])
        got = app.invoke({"trigger": "t", "suspect": suspect, "rival": rival,
                          "depth": 0, "expansions": 0, "progressed": True})["result"]
    key = lambda r: [(b["service"], b["fault_type"], b["score"], b["status"]) for b in r["ranked"]]
    assert got["status"] == ref["status"] and got["expansions"] == ref["expansions"]
    assert key(got) == key(ref)
    assert (got["selected"] or {}).get("branch_id") == (ref["selected"] or {}).get("branch_id")


def test_graph_draws():
    m = graph.mermaid()
    for node in ("commander", "gate", "tot", "fix_advisor", "human_approval", "escalate",
                 "investigators", "critic"):
        assert node in m
