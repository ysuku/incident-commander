"""Baselines: one prompt over a fixed digest, scored like the agent. No data or keys needed."""
import json
from pathlib import Path

import pytest

from incident_commander import baselines, evaluate, tools
from incident_commander.loaders import IncidentCase

CASE = IncidentCase("checkoutservice_cpu/1", "online-boutique", "1", 1705354566, Path("."),
                    "checkoutservice", "cpu")


class Canned:
    """Fake model: replies in order, records what it was sent."""

    def __init__(self, *replies):
        self.replies, self.seen, self.last_call = list(replies), [], {"mock": True}

    def complete(self, messages):
        self.seen.append(messages)
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch):
    calls = []
    dev = lambda svc, metric, z: {"series": f"{svc}_{metric}", "service": svc, "metric": metric,
                                  "before_mean": 1.0, "after_mean": 9.0, "pct_change": 800.0, "z": z}
    monkeypatch.setattr(tools, "service_map", lambda c: {"edges": {"frontendservice": ["checkoutservice"]}})
    monkeypatch.setattr(tools, "metric_query", lambda c, top=10: {
        "n_series_scanned": 72, "top_deviations": [dev("checkoutservice", "cpu", 226.4),
                                                   dev("frontend", "latency-50", 0.4)]})
    monkeypatch.setattr(tools, "log_search", lambda c, level=None, limit=20: {
        "total_matches": 0, "scope_lines": 100, "levels_present": ["info"],
        "matches_by_service_level": {}, "sample_after_alert": []})
    monkeypatch.setattr(tools, "trace_lookup", lambda c, failing_only=True, limit=10: {
        "n_traces_after_alert": 50, "traces": [{"path": "frontendservice -> checkoutservice",
                                                "duration_ms": 2396.4, "n_spans": 25,
                                                "error_spans": [{"service": "frontendservice"}]}]})

    def runbook_lookup(symptom, k=4):
        calls.append(symptom)
        return [{"source_ref": "runbooks/CPUThrottlingHigh.md#meaning", "alert": "CPUThrottlingHigh",
                 "distance": 0.31, "text": "Processes experience elevated CPU throttling."}]
    monkeypatch.setattr(tools, "runbook_lookup", runbook_lookup)
    return calls


DIAGNOSIS = json.dumps({"root_cause_service": "checkoutservice", "fault_type": "CPU", "confidence": "high",
                        "rationale": "cpu z=226", "proposed_fix": {"description": "raise the limit",
                                                                   "destructive": False}})


def test_plain_is_one_call_over_the_digest_and_scores_like_the_agent(fake_tools):
    model = Canned(DIAGNOSIS)
    brief = baselines.diagnose(CASE, model, "plain")
    assert brief["status"] == "diagnosed" and brief["architecture"] == "baseline-plain"
    assert brief["root_cause"] == {"service": "checkoutservice", "fault_type": "cpu"}
    assert brief["usage"]["model_calls"] == 1 and brief["investigation"] == []
    assert evaluate.score(CASE, brief) == "correct"
    prompt = model.seen[0][1]["content"]
    assert "checkoutservice_cpu: before 1, after 9" in prompt and "z=+226.40" in prompt
    assert "frontendservice -> checkoutservice | 2396.4 ms" in prompt
    assert "Runbook" not in prompt and fake_tools == [] and "retrieved" not in brief


def test_rag_adds_runbooks_retrieved_with_a_query_built_from_the_digest(fake_tools):
    model = Canned(DIAGNOSIS)
    brief = baselines.diagnose(CASE, model, "rag")
    # only the significant deviation goes into the query; latency z=0.4 is noise
    assert fake_tools == ["checkoutservice cpu up 800%"]
    assert "CPUThrottlingHigh" in model.seen[0][1]["content"]
    assert brief["retrieved"][0]["source_ref"] == "runbooks/CPUThrottlingHigh.md#meaning"
    assert brief["architecture"] == "baseline-rag" and brief["usage"]["model_calls"] == 1


def test_a_series_with_a_zero_baseline_has_no_percentage(monkeypatch):
    # real data: the tool returns pct_change None when the mean before the alert is 0
    monkeypatch.setattr(tools, "metric_query", lambda c, top=10: {"n_series_scanned": 1, "top_deviations": [
        {"series": "emailservice_socket", "service": "emailservice", "metric": "socket",
         "before_mean": 0.0, "after_mean": 4.0, "pct_change": None, "z": 12.0}]})
    text, raw = baselines.digest(CASE)
    assert "change n/a (baseline 0), z=+12.00" in text
    assert baselines.retrieval_query(raw) == "emailservice socket up"


def test_a_baseline_may_decline_and_that_is_an_escalation():
    brief = baselines.diagnose(CASE, Canned(json.dumps(
        {"escalate": True, "reason": "nothing deviates", "candidates": ["checkoutservice: cpu?"]})))
    assert brief["status"] == "escalated" and brief["escalation"]["code"] == "model_declined"
    assert evaluate.score(CASE, brief) == "escalated"
    assert evaluate.escalation_code(brief) == "other"          # not one of the agent's codes
    assert evaluate.handover_truth(CASE, brief) == "top_live_is_truth"


def test_unparseable_reply_gets_one_retry_then_escalates():
    model = Canned("I think it is checkout.", DIAGNOSIS)
    assert baselines.diagnose(CASE, model)["usage"]["model_calls"] == 2
    brief = baselines.diagnose(CASE, Canned("no json", "still no json"))
    assert brief["status"] == "escalated" and brief["escalation"]["code"] == "unparseable"
    assert brief["usage"]["model_calls"] == baselines.MAX_CALLS


def test_wrong_service_is_scored_wrong_there_is_no_gate():
    brief = baselines.diagnose(CASE, Canned(json.dumps(
        {"root_cause_service": "frontend", "fault_type": "delay"})))
    assert brief["status"] == "diagnosed" and evaluate.score(CASE, brief) == "wrong"


def test_eval_dispatch_and_folder_name(tmp_path):
    from incident_commander.cli import default_out_name
    assert default_out_name("m", None, "plain", day="d") == "d_m_baseline-plain"
    assert default_out_name("m", None, "linear", day="d") == "d_m"          # ordinary name unchanged
    rows = evaluate.run_eval([CASE], lambda: Canned(DIAGNOSIS), tmp_path, arch="rag")
    assert rows[0]["outcome"] == "correct" and rows[0]["arch"] == "baseline-rag"
    with pytest.raises(ValueError):
        evaluate.diagnose(CASE, Canned(DIAGNOSIS), tot="model", arch="plain")
    with pytest.raises(ValueError):
        baselines.diagnose(CASE, Canned(DIAGNOSIS), "cot")
    assert baselines.FAULTS == evaluate.KNOWN_FAULTS
