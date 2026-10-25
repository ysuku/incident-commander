"""ReAct loop tests: happy path on real data, plus the code-enforced guards."""
import json
import pytest

from incident_commander import config, loaders, loop
from incident_commander.model import MockModel
from incident_commander.scratchpad import Scratchpad

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")


class NeverFinishes:
    def complete(self, messages):
        return json.dumps({"thought": "keep looking",
                           "action": {"tool": "service_map", "args": {}}})


class FinishesUncited:
    def complete(self, messages):
        if "E1" not in messages[-1]["content"]:
            return json.dumps({"thought": "one quick look",
                               "action": {"tool": "metric_query", "args": {"top": 3}}})
        return json.dumps({"thought": "good enough",  # one kind only -> must be rejected
                           "action": {"finish": {
                               "root_cause_service": "checkoutservice", "fault_type": "cpu",
                               "supporting_evidence": ["E1"],
                               "proposed_fix": {"description": "x", "destructive": False}}}})


@needs_data
def test_mock_agent_diagnoses_real_case():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, MockModel())
    assert brief["status"] == "diagnosed"
    assert brief["root_cause"] == {"service": c.root_cause_service, "fault_type": c.fault_type}
    assert len(brief["telemetry_kinds"]) >= 2
    assert brief["proposed_fix"]["requires_approval"] is True  # destructive => gated


@needs_data
def test_budget_exhaustion_escalates_instead_of_guessing():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, NeverFinishes(), max_steps=3)
    assert brief["status"] == "escalated"
    assert "budget" in brief["reason"]
    assert brief["proposed_fix"] is None


@needs_data
def test_citation_gate_rejects_single_kind_finish():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, FinishesUncited(), max_steps=3)
    assert brief["status"] == "escalated"  # finish kept getting rejected
    assert any("finish rejected" in n for n in brief["notes"])


def test_gate_rejects_phantom_citations():
    pad = Scratchpad(alert="x")
    pad.add("metrics", "m", "s", implicates=["a"]); pad.add("traces", "t", "s", implicates=["aservice"])
    _, problem = loop._gate(pad, {"supporting_evidence": ["E1", "E9"]})
    assert problem and "E9" in str(problem)
    brief, problem = loop._gate(pad, {"root_cause_service": "a", "fault_type": "cpu",
                                      "supporting_evidence": ["E1", "E2"],
                                      "proposed_fix": {"description": "x", "destructive": True}})
    assert problem is None and brief["proposed_fix"]["requires_approval"] is True


def test_parse_tolerates_think_blocks_and_fences():
    raw = '<think>the fault is\nprobably cpu {not json}</think>\n```json\n{"thought": "x", "action": {"tool": "service_map", "args": {}}}\n```'
    msg = loop._parse(raw)
    assert msg["action"]["tool"] == "service_map"
    with pytest.raises(ValueError):
        loop._parse("<think>no answer yet</think>")


class RepeatsItself:
    def complete(self, messages):
        return json.dumps({"thought": "same again",
                           "action": {"tool": "log_search", "args": {"level": "error"}}})


@needs_data
def test_unknown_metric_is_an_error_not_a_finding():
    """Regression (first Qwen run): metric='p99_latency' matched nothing and was
    summarized as 'no metric deviations found' -- a false finding that misled the agent."""
    from incident_commander import tools
    c = loaders.load_case("checkoutservice_cpu/1")
    with pytest.raises(ValueError, match="available metrics"):
        tools.metric_query(c, metric="p99_latency")
    series = [d["series"] for d in tools.metric_query(c, metric="latency")["top_deviations"]]
    assert series and all("latency" in s for s in series)


@needs_data
def test_repeated_identical_call_is_refused():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, RepeatsItself(), max_steps=3)
    assert len(brief["evidence_board"]) == 1          # second+ calls add no evidence
    assert any("already called" in n for n in brief["notes"])


class CutOffThenGarbage:
    """Simulates the first Qwen failure: replies cut off at the token limit."""
    def __init__(self):
        self.last_call = {}

    def complete(self, messages):
        self.last_call = {"seconds": 290.0, "prompt_tokens": 1800, "completion_tokens": 4096,
                          "max_tokens": 4096, "finish_reason": "length"}
        return '{"thought": "very long reasoning that never clo'


@needs_data
def test_every_step_is_recorded_with_usage():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, MockModel())
    assert [e["outcome"] for e in brief["investigation"]] == ["tool_call", "tool_call", "diagnosed"]
    assert brief["usage"]["model_calls"] == 3
    assert "wall_seconds" in brief["usage"]


@needs_data
def test_cut_off_replies_are_counted_and_explained():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, CutOffThenGarbage())
    assert brief["status"] == "escalated"
    assert brief["usage"]["replies_hit_token_limit"] == 3          # all three failures recorded
    assert all(e["outcome"] == "unparseable" for e in brief["investigation"])
    assert any("cut off at the length limit" in n for n in brief["notes"])  # model is told why



class ReplayQwen4B:
    """Replays Qwen3-4B's real run (Oct 25): redis-filtered noise + unrelated traces."""
    script = [
        {"tool": "metric_query", "args": {"service": "redis", "metric": "cpu_utilization", "top": 1}},
        {"tool": "trace_lookup", "args": {"failing_only": True, "limit": 5}},
        {"finish": {"root_cause_service": "redis", "fault_type": "cpu",
                    "supporting_evidence": ["E1", "E2"],
                    "proposed_fix": {"description": "scale Redis", "destructive": False}}},
    ]

    def __init__(self):
        self.i = 0

    def complete(self, messages):
        a = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return json.dumps({"thought": "replay", "action": a})


@needs_data
def test_gate_rejects_citations_that_do_not_implicate_the_claim():
    """Regression: Qwen3-4B 'diagnosed' redis citing a z=0.14 metric and traces
    that never mention redis. Two kinds were cited; neither supported the claim."""
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, ReplayQwen4B(), max_steps=4)
    assert brief["status"] == "escalated"
    assert any("does not implicate redis" in n for n in brief["notes"])


@needs_data
def test_noise_is_not_summarized_as_a_deviation():
    c = loaders.load_case("checkoutservice_cpu/1")
    from incident_commander import tools
    r = tools.metric_query(c, service="redis", metric="cpu", top=1)
    kind, summary, _, implicated = loop._summarize("metric_query", r, {"service": "redis"})
    assert summary.startswith("NO significant deviation") and implicated == []


def test_every_fix_requires_approval_even_if_model_says_safe():
    pad = Scratchpad(alert="x")
    pad.add("metrics", "m", "s", implicates=["a"]); pad.add("logs", "l", "s", implicates=["a"])
    brief, problem = loop._gate(pad, {"root_cause_service": "a", "fault_type": "cpu",
                                      "supporting_evidence": ["E1", "E2"],
                                      "proposed_fix": {"description": "x", "destructive": False}})
    assert problem is None
    assert brief["proposed_fix"]["requires_approval"] is True
    assert brief["proposed_fix"]["model_labeled_destructive"] is False
