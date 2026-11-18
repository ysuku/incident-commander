"""Checkpoint 6.1 guardrails that hold on EVERY path (linear, ToT, graph): what
counts as implicating a service, the citation gate's extra refusals, unusable
replies, repeat refusal, tool input validation and the Commander's allowlist.
Most need no data; the ones on recorded incidents skip when RE2-OB is absent."""
import json
from pathlib import Path

import pandas as pd
import pytest

from incident_commander import config, loaders, loop, tools
from incident_commander.mcp_server import ToolClient, ToolError, build_server
from incident_commander.scratchpad import Scratchpad

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")

FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=100, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")


class Scripted:
    """Replays a list of actions (the last one repeats); keeps the prompts it saw."""

    def __init__(self, *actions):
        self.actions, self.i, self.systems = actions, 0, []

    def complete(self, messages):
        self.systems.append(messages[0]["content"])
        a = self.actions[min(self.i, len(self.actions) - 1)]
        self.i += 1
        return json.dumps({"rationale": "scripted", "action": a})


def outcomes(brief):
    return [e["outcome"] for e in brief["investigation"]]


def logs_result(by, n=None, **extra):
    return {"total_matches": sum(by.values()) if n is None else n, "matches_by_service_level": by,
            "sample_after_alert": [], **extra}


def trace(path, owners=()):
    return {"path": path, "duration_ms": 9.0, "source_ref": "traces.csv:traceID=t",
            "error_spans": [{"service": s, "op": "op", "status": 2.0} for s in owners]}


# ------------------------------------------------------------ 1. what "implicates" means
def test_ordinary_log_lines_implicate_nobody():
    info = logs_result({"checkoutservice/info": 2007, "frontend/debug": 9})
    assert loop._summarize("log_search", info, {})[3] == []                                # unfiltered
    assert loop._summarize("log_search", info, {"service": "checkoutservice"})[3] == []    # per service
    assert loop._summarize("log_search", info, {"level": "info"})[3] == []                 # not a symptom level


def test_only_warning_and_error_lines_implicate_and_a_matched_query_does_not():
    mixed = logs_result({"cartservice/error": 3, "redis/WARNING": 1, "frontend/info": 50})
    assert loop._summarize("log_search", mixed, {})[3] == ["cartservice", "redis"]
    assert loop._summarize("log_search", mixed, {"query": "deadline exceeded"})[3] == ["cartservice", "redis"]
    hit = logs_result({"emailservice/info": 4})
    for query in ("deadline exceeded", "request", "e"):  # info lines stay a mention, whatever matched
        assert loop._summarize("log_search", hit, {"query": query})[3] == []


def test_path_traces_implicate_nobody_not_even_the_owner_of_an_error_span():
    r = {"failing_only": True, "n_traces_after_alert": 40,
         "traces": [trace("frontendservice -> checkoutservice -> emailservice", ["frontendservice"])]}
    _, summary, _, implicated = loop._summarize("trace_lookup", r, {"limit": 3})
    assert implicated == [] and "40 traces after alert" in summary and "errors=1" in summary


def test_failing_only_fallback_is_said_and_implicates_nobody():
    r = {"failing_only": False, "n_traces_after_alert": 40,
         "traces": [trace("frontendservice -> checkoutservice")]}
    _, summary, _, implicated = loop._summarize("trace_lookup", r, {})  # failing_only left at its default
    assert summary.startswith("no failing traces") and "slowest instead" in summary and implicated == []
    _, asked, _, _ = loop._summarize("trace_lookup", r, {"failing_only": False})
    assert asked.startswith("40 traces after alert")  # slowest was what the caller asked for


# ------------------------------------------------------------ 2. shared cause, at the gate
def shared_cause_board(redirected: bool) -> Scratchpad:
    pad = Scratchpad(alert="x")
    pad.add("metrics", "emailservice latency up", "simple_metrics.csv:emailservice_latency-90", ["emailservice"])
    pad.add("logs", "errors", "logs.csv#L2", ["emailservice"])
    if redirected:  # the per-service lookup blamed the caller; the finish below does not cite it
        pad.add("traces", "BUT ... likely checkoutservice's own network",
                "traces.csv:serviceName=emailservice", ["checkoutservice"])
    return pad


EMAIL = {"root_cause_service": "emailservice", "fault_type": "delay", "supporting_evidence": ["E1", "E2"],
         "proposed_fix": {"description": "restart emailservice", "destructive": True}}


def test_gate_refuses_a_callee_whose_trace_lookup_blamed_its_caller_even_when_uncited():
    brief, problem = loop._gate(shared_cause_board(redirected=False), EMAIL)
    assert problem is None and brief["root_cause"]["service"] == "emailservice"
    brief, problem = loop._gate(shared_cause_board(redirected=True), EMAIL)
    assert brief == {} and "caller checkoutservice" in problem and "E3" in problem


def test_shared_cause_rule_leaves_a_service_its_own_lookup_implicates():
    pad = shared_cause_board(redirected=False)
    pad.add("traces", "own spans 9x", "traces.csv:serviceName=emailservice", ["emailservice", "checkoutservice"])
    pad.add("traces", "other service", "traces.csv:serviceName=cartservice", ["frontendservice"])
    assert loop._gate(pad, EMAIL)[1] is None


@needs_data
def test_blaming_emailservice_for_checkouts_delay_is_refused():
    """The 6.1 risk 'agreeing evidence that shares one cause': checkoutservice's
    network delay makes emailservice look slow in metrics and on every path."""
    c = loaders.load_case("checkoutservice_delay/1")
    model = Scripted({"tool": "metric_query", "args": {"service": "emailservice"}},
                     {"tool": "trace_lookup", "args": {"service": "emailservice"}},
                     {"tool": "trace_lookup", "args": {"limit": 3}},
                     {"tool": "log_search", "args": {"service": "emailservice"}},
                     {"finish": {**EMAIL, "supporting_evidence": ["E1", "E3", "E4"]}})
    brief = loop.diagnose(c, model, max_steps=6)
    board = {e["eid"]: e["implicates"] for e in brief["evidence_board"]}
    assert "emailservice" in board["E1"] and board["E2"] == ["checkoutservice"]
    assert "emailservice" not in board["E3"] and board["E4"] == []  # on a path / plain log lines
    assert brief["status"] == "escalated" and outcomes(brief)[4:] == ["finish_rejected", "finish_rejected"]
    assert any("finish rejected" in n and "caller checkoutservice" in n for n in brief["notes"])


# ------------------------------------------------------------ 3. gate: types and whitelists
def two_kinds() -> Scratchpad:
    pad = Scratchpad(alert="x")
    pad.add("metrics", "m", "s", implicates=["a"]); pad.add("traces", "t", "s", implicates=["aservice"])
    return pad


def finish(**over):
    return {"root_cause_service": "a", "fault_type": "cpu", "supporting_evidence": ["E1", "E2"],
            "proposed_fix": {"description": "x", "destructive": False}, **over}


def test_gate_refuses_an_unknown_fault_type():
    brief, problem = loop._gate(two_kinds(), finish(fault_type="banana"))
    assert brief == {} and "banana" in problem and "cpu" in problem
    assert loop._gate(two_kinds(), finish(fault_type=None))[1]
    brief, problem = loop._gate(two_kinds(), finish(fault_type="CPU "))
    assert problem is None and brief["root_cause"]["fault_type"] == "cpu"


def test_gate_refuses_malformed_fields_instead_of_crashing():
    assert "JSON object" in loop._gate(two_kinds(), "emailservice is down")[1]
    assert "list" in loop._gate(two_kinds(), finish(supporting_evidence="E1"))[1]
    assert "do not exist" in loop._gate(two_kinds(), finish(supporting_evidence=["E1", {"id": "E2"}]))[1]
    assert "service name" in loop._gate(two_kinds(), finish(root_cause_service=["a", "b"]))[1]


def test_fix_keeps_only_whitelisted_keys_and_always_needs_approval():
    fix = {"description": "x" * 900, "destructive": False, "requires_approval": False,
           "approval": {"state": "approved"}, "run": "kubectl delete pod"}
    brief, problem = loop._gate(two_kinds(), finish(proposed_fix=fix))
    assert problem is None
    assert brief["proposed_fix"] == {"description": "x" * 900,  # not truncated
                                     "model_labeled_destructive": False, "requires_approval": True,
                                     # removed, and said so (review fix: nothing is dropped silently)
                                     "dropped_keys": ["approval", "requires_approval", "run"]}


@pytest.mark.parametrize("fix,kept", [("restart it", {"description": "restart it"}),  # a bare string is the fix
                                      (["restart"], {"dropped_keys": ["(whole fix: list, not an object)"]}),
                                      (7, {"dropped_keys": ["(whole fix: int, not an object)"]}),
                                      (None, {})])
def test_a_non_object_fix_still_needs_approval_and_nothing_is_lost_silently(fix, kept):
    brief, problem = loop._gate(two_kinds(), finish(proposed_fix=fix))
    assert problem is None
    assert brief["proposed_fix"] == {**kept, "model_labeled_destructive": True, "requires_approval": True}


def test_gate_keeps_non_supporting_citations_in_the_brief():
    pad = two_kinds()
    pad.add("map", "deps", "traces.csv")
    brief, problem = loop._gate(pad, finish(supporting_evidence=["E1", "E2", "E3"]))
    assert problem is None and [e["eid"] for e in brief["supporting_evidence"]] == ["E1", "E2", "E3"]


# ------------------------------------------------------------ 4. unusable replies escalate
@pytest.mark.parametrize("action", ["finish", ["x"], {"finish": "emailservice, cpu"}, {"finish": ["E1"]}])
def test_malformed_action_or_finish_escalates_instead_of_crashing(action):
    brief = loop.diagnose(FAKE, Scripted(action))
    assert brief["status"] == "escalated" and brief["reason"] == "model output unparseable"
    assert outcomes(brief) == ["unparseable"] * 3
    assert any("must be a JSON object" in n for n in brief["notes"])


# ------------------------------------------------------------ 5. repeat refusal
def test_a_repeated_failing_call_is_refused_the_second_time():
    brief = loop.diagnose(FAKE, Scripted({"tool": "metric_query", "args": {"servce": "cart"}}), max_steps=3)
    assert outcomes(brief) == ["bad_args", "repeated_call", "repeated_call"]
    assert any("already called and failed" in n and "unknown argument" in n for n in brief["notes"])
    assert brief["evidence_board"] == []


def test_default_valued_arguments_do_not_defeat_the_repeat_check():
    with ToolClient(build_server(FAKE)) as tc:
        key = loop._call_key(tc, "metric_query", {})
        assert loop._call_key(tc, "metric_query", {"top": 10, "service": None}) == key
        assert loop._call_key(tc, "metric_query", {"top": 5}) != key
        assert loop._call_key(tc, "trace_lookup", {"failing_only": True}) == loop._call_key(tc, "trace_lookup", {})
        # 1 == True in Python, but limit=1 is not failing_only's default
        assert loop._call_key(tc, "trace_lookup", {"failing_only": 1}) != loop._call_key(tc, "trace_lookup", {})


@needs_data
def test_the_same_query_spelled_with_its_defaults_is_a_repeat():
    c = loaders.load_case("checkoutservice_cpu/1")
    model = Scripted({"tool": "metric_query", "args": {}}, {"tool": "metric_query", "args": {"top": 10}})
    brief = loop.diagnose(c, model, max_steps=2)
    assert outcomes(brief) == ["tool_call", "repeated_call"] and len(brief["evidence_board"]) == 1


# ------------------------------------------------------------ 6. log_search input
@pytest.fixture
def small_logs(monkeypatch):
    df = pd.DataFrame({"timestamp": [50 * 10**9, 150 * 10**9, 160 * 10**9, 170 * 10**9],
                       "container_name": ["frontend", "frontend", "cartservice", "cartservice"],
                       "message": ["request started", "request complete", "GetCart ok", "cache miss"],
                       "level": ["debug", "info", "info", "warning"]})
    monkeypatch.setattr(tools, "load_logs", lambda case, columns=None: df)
    return df


def test_log_search_rejects_a_service_without_logs_and_lists_the_ones_with(small_logs):
    with pytest.raises(ValueError, match=r"unknown service 'productcatalogservice'.*\['cartservice', 'frontend'\]"):
        tools.log_search(FAKE, service="productcatalogservice")
    r = tools.log_search(FAKE, service="frontendservice")  # the trace spelling of "frontend"
    assert r["total_matches"] == 2 and set(r["matches_by_service_level"]) == {"frontend/debug", "frontend/info"}


def test_log_search_rejects_an_unknown_level_and_lists_the_valid_ones(small_logs):
    with pytest.raises(ValueError, match="unknown level 'catastrophic'; valid levels are .*'error'.*'warning'"):
        tools.log_search(FAKE, level="catastrophic")
    assert tools.log_search(FAKE, level="WARNING")["total_matches"] == 1  # case-insensitive, as before


def test_log_search_rejects_regex_alternation(small_logs):
    with pytest.raises(ValueError, match="literal substring, not a regex"):
        tools.log_search(FAKE, query="OOM|out of memory")


def test_no_error_lines_is_a_worded_zero_not_an_error(small_logs):
    args = {"service": "frontend", "level": "error"}
    r = tools.log_search(FAKE, **args)
    assert r["total_matches"] == 0 and r["scope_lines"] == 2 and r["levels_present"] == ["debug", "info"]
    kind, summary, _, implicated = loop._summarize("log_search", r, args)
    assert kind == "logs" and implicated == []
    assert summary.startswith("checked [service='frontend', level='error']: no log lines at that level")
    assert "2 lines searched" in summary


def test_a_query_that_matches_nothing_is_worded_as_a_missed_filter(small_logs):
    args = {"query": "deadline exceeded"}
    _, summary, _, implicated = loop._summarize("log_search", tools.log_search(FAKE, **args), args)
    assert summary.startswith("filter matched nothing [query='deadline exceeded']") and implicated == []
    assert "not a finding about any service" in summary and "matching lines" not in summary


@needs_data
def test_log_search_validation_on_a_recorded_case():
    c = loaders.load_case("checkoutservice_cpu/1")
    with pytest.raises(ValueError, match="services with logs in this case are .*'frontend'"):
        tools.log_search(c, service="nosuchservice")
    assert tools.log_search(c, service="frontendservice", limit=1)["total_matches"] > 0
    with ToolClient(build_server(c)) as tc:  # and through MCP it is a one-line tool error
        with pytest.raises(ToolError, match="not a regex"):
            tc.call("log_search", {"query": "timeout|deadline"})


# ------------------------------------------------------------ 7. metric_query / trace_lookup / map
@needs_data
def test_metric_query_rejects_short_fragments_but_keeps_both_substring_directions():
    c = loaders.load_case("checkoutservice_cpu/1")
    with pytest.raises(ValueError, match="unknown service 'e'; metric services are"):
        tools.metric_query(c, service="e")
    with pytest.raises(ValueError, match="unknown metric 'io'; available metrics are"):
        tools.metric_query(c, metric="io")
    with pytest.raises(ValueError, match="metric services are"):
        tools.metric_query(c, service="nosuchservice")
    top = tools.metric_query(c, service="frontendservice", metric="latency")["top_deviations"]
    assert top and all(d["service"] == "frontend" and d["metric"].startswith("latency") for d in top)
    wide = tools.metric_query(c, service="redis", metric="cpu_utilization", top=1)["top_deviations"]
    assert [d["series"] for d in wide] == ["redis_cpu"]


@needs_data
def test_zero_top_or_limit_is_clamped_not_reported_as_nothing_found():
    c = loaders.load_case("checkoutservice_cpu/1")
    r = tools.metric_query(c, service="", top=0)
    assert len(r["top_deviations"]) == 1
    assert "scope [all services]" in loop._summarize("metric_query", r, {"service": "", "top": 0})[1]
    assert len(tools.trace_lookup(c, limit=0)["traces"]) == 1


def test_empty_or_null_filters_are_not_printed_as_a_scope():
    r = {"top_deviations": [{"series": "a_cpu", "service": "a", "metric": "cpu", "z": 9.0,
                             "pct_change": 50.0, "source_ref": "simple_metrics.csv:a_cpu"}]}
    summary = loop._summarize("metric_query", r, {"service": "", "metric": None})[1]
    assert "scope [all services]" in summary and "service=" not in summary


def test_empty_service_map_and_runbook_miss_are_worded_as_such():
    kind, summary, _, implicated = loop._summarize("service_map", {"edges": {}, "source_ref": "traces.csv"})
    assert kind == "map" and summary.startswith("no call edges found") and implicated == []
    assert loop._summarize("runbook_lookup", [])[1] == "no runbook match (abstained)"


# ------------------------------------------------------------ 8. unknown argument names
def test_unknown_argument_names_are_refused_with_the_valid_ones():
    with ToolClient(build_server(FAKE)) as tc:
        with pytest.raises(ToolError, match=r"unknown argument 'servce' for metric_query; "
                                            r"arguments are \['service', 'metric', 'top'\]") as e:
            tc.call("metric_query", {"servce": "cartservice", "top": 1})
        assert "\n" not in str(e.value)
        with pytest.raises(ToolError, match="unknown argument 'service' for service_map"):
            tc.call("service_map", {"service": "x"})
        with pytest.raises(ToolError, match="unknown argument"):  # the role views share the check
            tc.scoped(["log_search"]).call("log_search", {"lvl": "error"})


# ------------------------------------------------------------ 9. allowlist on the linear path
def test_linear_commander_can_neither_see_nor_call_runbook_lookup():
    model = Scripted({"tool": "runbook_lookup", "args": {"symptom": "cpu at its limit"}})
    brief = loop.diagnose(FAKE, model, max_steps=2)
    assert outcomes(brief) == ["unknown_tool", "unknown_tool"]
    assert brief["evidence_board"] == [] and "runbook" not in model.systems[0]
    assert all(t in model.systems[0] for t in ("metric_query(", "log_search(", "trace_lookup(", "service_map("))
    assert any("tools are ['metric_query', 'log_search', 'trace_lookup', 'service_map']" in n
               for n in brief["notes"])


def test_linear_tot_keeps_the_branch_board_but_not_the_runbooks():
    from incident_commander.mcp_server import BRANCH_TOOLS
    from incident_commander.roles import COMMANDER
    with ToolClient(build_server(FAKE)) as tc:
        run = loop.Run(FAKE, Scripted(), tc.scoped(COMMANDER.tools + BRANCH_TOOLS))
        box = run.tot_context().toolbox
        assert set(BRANCH_TOOLS) <= set(box.names) and "runbook_lookup" not in box.names
        assert box.call("branch_status", {}) is not None
        with pytest.raises(ToolError, match="not available to this role"):
            box.call("runbook_lookup", {"symptom": "x"})


def test_server_instructions_mention_the_branch_board():
    assert "branch board" in build_server(FAKE).instructions


# ------------------------------------------------------------ 10. read-only
@needs_data
def test_telemetry_tools_leave_the_case_directory_untouched():
    c = loaders.load_case("checkoutservice_cpu/1")
    snapshot = lambda: sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                              for p in Path(c.dir).rglob("*") if p.is_file())
    before = snapshot()
    with ToolClient(build_server(c)) as tc:
        for name, args in (("metric_query", {}), ("log_search", {"level": "error"}),
                           ("trace_lookup", {"limit": 3}), ("trace_lookup", {"service": "checkoutservice"}),
                           ("service_map", {})):
            tc.call(name, args)
    assert before and snapshot() == before
