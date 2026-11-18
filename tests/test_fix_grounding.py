"""Fix Advisor grounding (Checkpoint 6.1): stale flags, untraceable references in
free text, and the review flag a runbook contradiction puts on the approval."""
import json

import pytest

from incident_commander import config, fix_advisor, graph, loaders

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")

RUNBOOK = {"alert": "A", "kind": "action", "distance": 0.5, "source_ref": "r/A.md#action",
           "text": "Restart the pod.  Then scale the deployment up."}
GOOD = {"runbook": "R1", "step": "Restart the pod", "evidence": "E1", "why": "memory is flat"}


class Tools:
    def call(self, name, args):
        assert name == "runbook_lookup"
        return [RUNBOOK]


class M:
    last_call = {"seconds": 0.1}

    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages):
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def a_brief():
    return {"root_cause": {"service": "svc", "fault_type": "mem"},
            "supporting_evidence": [{"eid": "E1", "kind": "metrics", "summary": "m", "source_ref": "s"},
                                    {"eid": "E3", "kind": "traces", "summary": "t", "source_ref": "s"}],
            "proposed_fix": {"description": "draft", "model_labeled_destructive": False,
                             "requires_approval": True}}


def advise(reply):
    brief = a_brief()
    advice = fix_advisor.advise(brief, Tools(), M(reply))
    return advice, fix_advisor.apply(brief, advice)


def test_a_reply_that_breaks_halfway_leaves_no_stale_flags():
    # R9 is dropped and GOOD is kept before the unhashable runbook id raises
    advice, brief = advise({"fix": {"summary": "s"}, "runbook_refs": ["R9"],
                            "contradictions": [GOOD, {"runbook": ["R1"], "evidence": "E1"}]})
    assert advice["by"].startswith("template")
    assert advice["contradictions"] == [] and advice["dropped"] == [] and advice["runbook_refs"] == []
    assert brief["proposed_fix"]["contradictions"] == []
    assert brief["proposed_fix"]["requires_approval"] is True


def test_garbage_reply_still_reports_that_the_advisor_ran():
    _, brief = advise("not json")
    assert brief["fix_advisor"]["dropped"] == [] and brief["fix_advisor"]["contradictions"] == []


def test_untraceable_reference_in_free_text_is_recorded_and_the_text_is_kept():
    steps = ["Follow R1 to restart", "Apply R7 section 2, as E9 shows", "per E3, raise the limit"]
    advice, brief = advise({"fix": {"summary": "see R1", "steps": steps, "preconditions": ["E2 still holds"],
                                    "rollback": "undo per R4"}, "runbook_refs": ["R1"]})
    dropped = advice["dropped"]
    assert len(dropped) == 4
    assert any("steps[1]" in d and "'R7'" in d for d in dropped)
    assert any("steps[1]" in d and "'E9'" in d for d in dropped)
    assert any("preconditions[0]" in d and "'E2'" in d for d in dropped)   # on no board the advisor saw
    assert any("rollback" in d and "'R4'" in d for d in dropped)
    assert not any("'R1'" in d or "'E3'" in d for d in dropped)            # traceable ones pass
    assert brief["proposed_fix"]["steps"] == steps                          # nothing cut from the plan
    assert brief["fix_advisor"]["dropped"] == dropped


def test_clean_advice_has_empty_lists_not_missing_keys():
    advice, brief = advise({"fix": {"summary": "s", "steps": ["raise the limit"]}, "runbook_refs": ["R1"]})
    assert advice["dropped"] == [] and advice["contradictions"] == []
    assert brief["fix_advisor"]["dropped"] == [] and brief["fix_advisor"]["contradictions"] == []


def test_flagged_step_is_matched_against_the_runbook_text_and_kept_either_way():
    para = {"runbook": "R1", "step": "bounce the container", "evidence": "E3", "why": "w"}
    spaced = {**GOOD, "step": '"restart the  pod."'}
    advice, _ = advise({"fix": {"summary": "s"}, "contradictions": [spaced, para]})
    quoted, paraphrased = advice["contradictions"]
    assert "note" not in quoted                       # case, spacing and quotes do not matter
    assert "not found" in paraphrased["note"] and paraphrased["step"] == "bounce the container"
    assert advice["dropped"] == []


def test_review_reason_names_the_step_and_the_evidence():
    reason = fix_advisor.review_reason([GOOD])
    assert "R1" in reason and "Restart the pod" in reason and "E1" in reason
    assert "+2 more" in fix_advisor.review_reason([GOOD] * 5)


# ---------------------------------------------------------------- through the graph
def _graph_brief(fix_reply):
    from test_graph import Scripted, act, finish
    c = loaders.load_case("emailservice_cpu/1")
    model = Scripted([act("metric_query", top=3), act("trace_lookup", service="emailservice"),
                      finish("emailservice", "cpu", "E1", "E2")], fix_reply=fix_reply)
    return graph.diagnose(c, model, tot="heuristic")


@needs_data
def test_valid_contradiction_asks_for_review_without_changing_the_outcome():
    brief = _graph_brief({"fix": {"summary": "s", "steps": ["a"]}, "runbook_refs": ["R1"],
                          "contradictions": [{"runbook": "R1", "step": "restart", "evidence": "E1", "why": "cpu"}]})
    ap = brief["approval"]
    assert brief["status"] == "diagnosed" and ap["state"] == "pending"
    assert ap["review_required"] is True and ap["flags_for_reviewer"] == 1
    assert "R1" in ap["review_reason"] and "restart" in ap["review_reason"] and "E1" in ap["review_reason"]
    assert brief["path"][-2:] == ["fix_advisor", "human_approval"]       # no escalation route
    assert brief["proposed_fix"]["requires_approval"] is True


@needs_data
def test_no_contradiction_means_no_review_flag():
    brief = _graph_brief({"fix": {"summary": "s"}, "contradictions": [
        {"runbook": "R8", "step": "x", "evidence": "E1", "why": "invented runbook"}]})
    ap = brief["approval"]
    assert brief["status"] == "diagnosed" and ap["state"] == "pending"
    assert ap["review_required"] is False and "review_reason" not in ap and ap["flags_for_reviewer"] == 0
    assert brief["fix_advisor"]["contradictions"] == [] and len(brief["fix_advisor"]["dropped"]) == 1


@needs_data
def test_unusable_advice_through_the_graph_carries_empty_lists_and_no_review_flag():
    brief = _graph_brief("not json")
    assert brief["fix_advisor"]["dropped"] == [] and brief["fix_advisor"]["contradictions"] == []
    assert brief["approval"]["review_required"] is False and brief["approval"]["state"] == "pending"
