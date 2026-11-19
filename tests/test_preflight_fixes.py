"""Defects found by the pre-flight audit before the held-out run, one section each.

All cases here are synthetic folders written to tmp_path with service names in
the Train Ticket style (ts-*-service): no held-out data is read.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from incident_commander import baselines, config, evaluate, loaders, loop, tools
from incident_commander.mcp_server import ToolClient, ToolCrash, ToolError, build_server
from incident_commander.model import MockModel

T0 = 1_700_000_300          # alert time, unix seconds
NS = 1_000_000_000
TRACE_COLS = ["traceID", "spanID", "serviceName", "operationName", "startTimeMillis",
              "duration", "statusCode", "parentSpanID"]


def make_case(folder: Path, metrics=None, logs=None, traces=None, system="online-boutique"):
    folder.mkdir(parents=True, exist_ok=True)
    if metrics is not None:
        pd.DataFrame(metrics).to_csv(folder / "simple_metrics.csv", index=False)
    if logs is not None:
        pd.DataFrame(logs).to_csv(folder / "logs.csv", index=False)
    if traces is not None:
        pd.DataFrame(traces, columns=TRACE_COLS).to_csv(folder / "traces.csv", index=False)
    return loaders.IncidentCase("ts-order-service_cpu/1", system, "1", T0, folder, "ts-order-service", "cpu")


def log_rows(*rows):
    """rows of (seconds from the alert, service, level, message) -> a logs.csv table."""
    return {"time": [T0 + s for s, *_ in rows], "timestamp": [(T0 + s) * NS for s, *_ in rows],
            "container_name": [r[1] for r in rows], "message": [r[3] for r in rows],
            "level": [r[2] for r in rows]}


# ------------------------------------------------------------ 1. metric ranking

def metric_table():
    """20 samples, 10 on each side of the alert. Column order puts the NaN-baseline
    series first, where it used to scramble the sort."""
    t = [T0 - 100 + 10 * i for i in range(20)]
    rng = np.random.default_rng(7)
    cols = {"time": t, "ts-new-service_error": [np.nan] * 10 + [1.0] * 10}
    for i in range(14):   # more than `top` quiet series
        cols[f"ts-quiet{i}-service_mem"] = list(100 + rng.normal(0, 1, 20))
    cols["ts-order-service_cpu"] = list(1 + rng.normal(0, 0.01, 10)) + list(3 + rng.normal(0, 0.01, 10))
    cols["ts-pay-service_error"] = [0.0] * 10 + [0.5] * 10          # flat 0 before the alert
    return cols


def test_a_series_with_no_baseline_no_longer_scrambles_the_ranking(tmp_path):
    r = tools.metric_query(make_case(tmp_path, metrics=metric_table()))
    top = r["top_deviations"]
    assert top[0]["series"] == "ts-order-service_cpu"
    assert all(math.isfinite(d["z"]) for d in top)
    # the series with no data before the alert is read as starting from 0: bounded, like the flat-0 one
    assert {d["series"] for d in top[:3]} == {"ts-order-service_cpu", "ts-new-service_error", "ts-pay-service_error"}
    new = next(d for d in top if d["series"] == "ts-new-service_error")
    assert new["z"] == 20.0 and new["baseline"] == "absent"
    text = loop._summarize("metric_query", r, {})[1]
    assert "no data before the alert" in text and "nan" not in text


def test_a_series_that_ends_at_the_alert_is_not_comparable_and_ranks_last(tmp_path):
    cols = metric_table()
    cols["ts-gone-service_cpu"] = [1.0] * 10 + [np.nan] * 10          # data before, none after: z stays NaN
    case = make_case(tmp_path, metrics=cols)
    assert all(math.isfinite(d["z"]) for d in tools.metric_query(case)["top_deviations"])
    r = tools.metric_query(case, service="ts-gone-service")
    kind, text, _, implicated = loop._summarize("metric_query", r, {"service": "ts-gone-service"})
    assert text.startswith("NOT comparable") and "nan" not in text and implicated == []


def test_the_critic_does_not_read_a_bounded_z_as_a_measured_size(tmp_path):
    """z = 20 on a zero-baseline series is a flag. Before the bound it was 1e9 and
    the signatures called it unusable; they still do."""
    from incident_commander import signatures as sig
    cols = metric_table()
    cols["ts-pay-service_socket"] = [0.0] * 10 + [1.0] * 10
    cols["ts-pay-service_cpu"] = cols.pop("ts-quiet0-service_mem")
    r = tools.metric_query(make_case(tmp_path, metrics=cols), service="ts-pay-service")
    rows = {d["metric"]: d for d in r["top_deviations"]}
    assert rows["socket"]["z"] == 20.0 and rows["socket"]["baseline"] == "zero" and rows["cpu"]["baseline"] == "recorded"
    f = sig.features("ts-pay-service", {"E1": ("metric_query", {"service": "ts-pay-service"}, r)}, {})
    assert math.isnan(f.z["socket"][0]) and math.isfinite(f.z["cpu"][0])
    assert sig._metric(f, "socket")[0] is None


def test_a_zero_baseline_series_is_significant_but_bounded(tmp_path):
    r = tools.metric_query(make_case(tmp_path, metrics=metric_table()), service="ts-pay-service")
    d = r["top_deviations"][0]
    assert d["z"] == 20.0 and d["pct_change"] is None               # was 0.5 / 1e-9 = 5e8
    assert abs(d["z"]) >= loop.Z_SIGNIFICANT                        # still counts as a deviation
    _, _, _, implicated = loop._summarize("metric_query", r, {"service": "ts-pay-service"})
    assert implicated == ["ts-pay-service"]


# ------------------------------------------------------------ 2. which spans are errors

def test_missing_and_2xx_status_codes_are_not_errors():
    s = pd.Series([0, np.nan, 200, 204, 302, 2, 13, 400, 404, 500, 503])
    assert list(tools._is_error(s)) == [False] * 5 + [True] * 6


def spans(status_after):
    """Two services over HTTP; one trace per second, 30 before and 30 after the alert."""
    rows = []
    for i in range(-30, 30):
        ms, tid = (T0 + i) * 1000, f"t{i}"
        status = 200 if i < 0 else status_after(i)
        rows += [(tid, f"{tid}-a", "ts-ui-service", "GET /order", ms, 9000, 200 if status < 400 else status, None),
                 (tid, f"{tid}-b", "ts-order-service", "POST /api/order", ms + 1, 5000, status, f"{tid}-a")]
    return rows


def test_http_spans_with_no_failures_are_not_reported_as_failing(tmp_path):
    case = make_case(tmp_path, traces=spans(lambda i: 200))
    r = tools.trace_lookup(case)
    assert r["failing_only"] is False and all(t["error_spans"] == [] for t in r["traces"])
    with ToolClient(build_server(case)) as tb:                       # and the flag survives MCP as a bool
        over_mcp = tb.call("trace_lookup", {})
    assert over_mcp["failing_only"] is False
    kind, text, _, implicated = loop._summarize("trace_lookup", over_mcp, {})
    assert kind == "traces" and text.startswith("no failing traces") and implicated == []
    own = tools.trace_lookup(case, service="ts-order-service")["overall"]
    assert own["err_rate_before"] == 0.0 and own["err_rate_after"] == 0.0   # was 1.0 and 1.0


def test_a_real_rise_in_http_500s_is_seen(tmp_path):
    case = make_case(tmp_path, traces=spans(lambda i: 500 if i % 3 == 0 else 200))
    own = tools.trace_lookup(case, service="ts-order-service")["overall"]
    assert own["err_rate_before"] == 0.0 and own["err_rate_after"] == pytest.approx(1 / 3, abs=0.01)
    worst = tools.trace_lookup(case)
    assert worst["failing_only"] is True
    assert {e["status"] for t in worst["traces"] for e in t["error_spans"]} == {500.0}


# ------------------------------------------------------------ 3. logs: line numbers, levels, rise

def test_cited_log_lines_are_file_lines_when_a_message_spans_several(tmp_path):
    case = make_case(tmp_path, logs=log_rows(
        (-5, "ts-ui-service", "INFO", "request complete"),
        (3, "ts-order-service", "ERROR", "Traceback (most recent call last):\n  File \"a.py\", line 1\nBoom"),
        (4, "ts-order-service", "ERROR", "second failure")))
    hit = tools.log_search(case, query="second failure")["sample_after_alert"][0]
    line_no = int(hit["source_ref"].rpartition("#L")[2])
    file_lines = (tmp_path / "logs.csv").read_text().split("\n")
    assert "second failure" in file_lines[line_no - 1]              # was two lines early
    first = tools.log_search(case, query="Traceback")["sample_after_alert"][0]
    assert "Traceback" in file_lines[int(first["source_ref"].rpartition("#L")[2]) - 1]


def test_level_filter_accepts_either_spelling_and_any_case(tmp_path):
    case = make_case(tmp_path, logs=log_rows((1, "ts-order-service", "WARN", "slow"),
                                             (2, "ts-order-service", "ERROR", "boom"),
                                             (3, "ts-ui-service", "INFO", "ok")))
    assert tools.log_search(case, level="warning")["total_matches"] == 1
    assert tools.log_search(case, level="warn")["total_matches"] == 1
    assert tools.log_search(case, level="err")["total_matches"] == 1
    with pytest.raises(tools.Refusal, match="unknown level"):
        tools.log_search(case, level="loud")


def test_an_empty_level_column_is_handled_like_a_missing_one(tmp_path):
    case = make_case(tmp_path, logs=log_rows((1, "ts-order-service", None, "java.lang.Exception: boom"),
                                             (2, "ts-ui-service", None, "ok")))
    r = tools.log_search(case, level="error")                       # was AttributeError on .str
    assert r["total_matches"] == 0 and r["severity_recorded"] is False and r["levels_present"] == ["unknown"]
    kind, text, _, implicated = loop._summarize("log_search", r, {"level": "error"})
    assert text.startswith("NOT checked") and "no severity" in text and implicated == []
    both = tools.log_search(case, level="error", query="Exception")  # level + query: still not a checked zero
    assert loop._summarize("log_search", both, {"level": "error", "query": "Exception"})[1].startswith("NOT checked")
    found = tools.log_search(case, query="Exception")
    kind, text, _, implicated = loop._summarize("log_search", found, {"query": "Exception"})
    assert found["total_matches"] == 1 and "no severity" in text and implicated == []


def routine_and_new():
    """Equal 100 s windows on each side. ts-chatty warns at the same rate before
    and after; ts-order errors only after; ts-pay warns once before, ten times after."""
    rows = [(-100, "ts-ui-service", "INFO", "start"), (100, "ts-ui-service", "INFO", "end")]
    rows += [(s, "ts-chatty-service", "WARN", "retrying cache") for s in (-90, -70, -50, -30, -10, 10, 30, 50, 70, 90)]
    rows += [(s, "ts-order-service", "ERROR", "db timeout") for s in (5, 15, 25)]
    rows += [(-40, "ts-pay-service", "WARN", "slow call")] + [(s, "ts-pay-service", "WARN", "slow call") for s in range(1, 11)]
    return log_rows(*sorted(rows))


def test_only_a_rise_in_warning_or_error_lines_implicates_a_service(tmp_path):
    case = make_case(tmp_path, logs=routine_and_new())
    r = tools.log_search(case)
    assert r["matches_by_service_level"]["ts-chatty-service/warn"] == 10   # keys are lower-case levels
    assert r["after_alert_by_service_level"]["ts-chatty-service/warn"] == 5
    assert r["window_seconds"] == {"before": 100.0, "after": 100.0}
    kind, text, _, implicated = loop._summarize("log_search", r, {})
    assert implicated == ["ts-order-service", "ts-pay-service"]      # ts-chatty-service used to be here too
    assert "ts-chatty-service/warn" in text and "routine" in text
    only_before = tools.log_search(case, service="ts-chatty-service", query="retrying cache", limit=3)
    assert only_before["sample_from"] == "after_alert"
    quiet_after = make_case(tmp_path / "b", logs=log_rows((-50, "ts-old-service", "ERROR", "old trouble"),
                                                          (-100, "ts-ui-service", "INFO", "a"), (100, "ts-ui-service", "INFO", "b")))
    r2 = tools.log_search(quiet_after, level="error")
    kind, text, _, implicated = loop._summarize("log_search", r2, {"level": "error"})
    assert r2["sample_from"] == "before_alert" and implicated == [] and "BEFORE" in text


def test_a_saved_log_result_without_the_split_reads_as_before():
    old = {"total_matches": 3, "scope_lines": 9, "levels_present": ["error", "info"],
           "matches_by_service_level": {"frontend/error": 3}, "sample_after_alert": []}
    assert loop._summarize("log_search", old, {})[3] == ["frontend"]  # offline replays of saved runs


def test_log_search_reads_a_numeric_level_column(tmp_path):
    case = make_case(tmp_path, logs=log_rows((-5, "a-service", 30, "m1"), (1, "b-service", 50, "m2"), (2, "a-service", 50, "m3")))
    r = tools.log_search(case)                                       # .str on an int column used to raise
    assert r["total_matches"] == 3 and r["levels_present"] == ["30", "50"]
    assert tools.log_search(case, level="50")["total_matches"] == 2


def test_a_service_whose_lines_carry_no_level_is_not_a_checked_zero(tmp_path):
    case = make_case(tmp_path, logs=log_rows((1, "cart-service", None, "System.Exception: redis down"),
                                             (2, "cart-service", None, "retry"), (3, "ui-service", "info", "ok")))
    r = tools.log_search(case, service="cart-service", level="error")
    assert r["severity_recorded"] is False and r["unlevelled_lines"] == 2
    assert loop._summarize("log_search", r, {"service": "cart-service", "level": "error"})[1].startswith("NOT checked")
    whole = tools.log_search(case, level="error")                    # the file as a whole does record severity
    text = loop._summarize("log_search", whole, {"level": "error"})[1]
    assert whole["severity_recorded"] is True and text.startswith("checked") and "2 of them carry no level" in text


def test_cited_lines_are_right_when_another_column_spans_lines(tmp_path):
    logs = log_rows((1, "ui-service", "error", "first"), (2, "ui-service", "error", "second"), (3, "ui-service", "error", "third"))
    logs["error"] = ["rpc error:\n\tstatus = UNAVAILABLE\n\tdetails = x", "", ""]   # the RE2 layout: multi-line `error`
    case = make_case(tmp_path, logs=logs)
    refs = {s["message"]: int(s["source_ref"].rpartition("#L")[2]) for s in tools.log_search(case)["sample_after_alert"]}
    file_lines = (tmp_path / "logs.csv").read_text().split("\n")
    assert all(msg in file_lines[n - 1] for msg, n in refs.items()) and refs["second"] == 5


def test_log_timestamps_that_are_not_nanoseconds_do_not_switch_logs_off(tmp_path):
    rows = log_rows((5, "pay-service", "error", "boom"), (6, "pay-service", "error", "boom"), (-9, "ui-service", "info", "ok"))
    rows["timestamp"] = [t // 1_000_000 for t in rows["timestamp"]]   # milliseconds: every line reads as "before"
    r = tools.log_search(make_case(tmp_path, logs=rows))
    assert r["after_alert_by_service_level"] is None and r["sample_from"] == "no_split"
    kind, text, _, implicated = loop._summarize("log_search", r, {})
    assert implicated == ["pay-service"] and "could not be told apart" in text and "routine" not in text


def test_one_level_under_two_spellings_is_counted_together(tmp_path):
    rows = [(-100, "ui-service", "info", "a"), (100, "ui-service", "info", "b")]
    rows += [(s, "a-service", "WARN", "retry") for s in range(-90, 0, 9)] + [(s, "a-service", "WARN", "retry") for s in range(1, 91, 9)]
    rows += [(50, "a-service", "warn", "retry")]
    r = tools.log_search(make_case(tmp_path, logs=log_rows(*sorted(rows))), service="a-service")
    assert r["matches_by_service_level"] == {"a-service/warn": 21} and r["after_alert_by_service_level"] == {"a-service/warn": 11}
    assert loop._summarize("log_search", r, {"service": "a-service"})[3] == []   # 10 before, 11 after: no rise


def test_text_status_codes_do_not_crash_the_trace_tools(tmp_path):
    rows = [(t, s, svc, op, ms, d, "OK" if st == 200 else "ERROR", par) for t, s, svc, op, ms, d, st, par in spans(lambda i: 200)]
    case = make_case(tmp_path, traces=rows)
    assert tools.trace_lookup(case)["failing_only"] is False          # a name is not a code: not recorded
    assert tools.trace_lookup(case, service="ts-order-service")["overall"]["err_rate_after"] == 0.0
    mixed = [(t, s, svc, op, ms, d, st if i % 2 else "OK", par)
             for i, (t, s, svc, op, ms, d, st, par) in enumerate(spans(lambda i: 500))]
    worst = tools.trace_lookup(make_case(tmp_path / "m", traces=mixed))
    assert worst["failing_only"] is True


# ------------------------------------------------------------ 4. a tool crash is an error row

def broken_metrics(tmp_path):
    cols = metric_table()
    cols["timestamp"] = cols.pop("time")                             # the column the tool needs is missing
    return make_case(tmp_path, metrics=cols, logs=routine_and_new(), traces=spans(lambda i: 200))


def test_a_refusal_is_the_models_to_fix_and_a_crash_is_not(tmp_path):
    with ToolClient(build_server(broken_metrics(tmp_path))) as tb:
        with pytest.raises(ToolError, match="unknown service"):      # a refusal: the loop posts a note
            tb.call("log_search", {"service": "nope"})
        with pytest.raises(ToolCrash, match="metric_query: KeyError"):
            tb.call("metric_query", {})
    assert not issubclass(ToolCrash, ValueError)                     # the loop catches ValueError as bad args


@pytest.mark.parametrize("arch", ["linear", "graph"])
def test_a_case_whose_data_cannot_be_read_is_recorded_as_an_error(tmp_path, arch):
    case = broken_metrics(tmp_path / "case")
    rows = evaluate.run_eval([case], MockModel, tmp_path / "out", arch=arch)
    assert rows[0]["outcome"] == "error" and "ToolCrash" in rows[0]["reason"]   # was an ordinary escalation
    assert "1 errors" in (tmp_path / "out" / "summary.md").read_text()


# ------------------------------------------------------------ 5. error briefs are retried, not stamped

def heldout_cases(n=3):
    return [loaders.IncidentCase(f"ts-order-service_cpu/{i}", config.HELDOUT_SYSTEM, str(i), 0, Path("."),
                                 "ts-order-service", "cpu") for i in range(1, n + 1)]


class Paid:
    cost_usd = 0.25


def test_a_run_with_error_rows_is_not_stamped_and_the_same_command_finishes_it(tmp_path, monkeypatch):
    cases, out, calls = heldout_cases(), tmp_path / "tt", []
    api_up = {"ok": False}

    def diagnose(case, model, **kw):
        calls.append(case.case_id)
        if not api_up["ok"]:
            raise ConnectionError("api down")
        return {"status": "escalated", "reason": "r", "usage": {}}

    monkeypatch.setattr(loop, "diagnose", diagnose)
    rows = evaluate.run_eval(cases, Paid, out, whole_run=cases)
    assert [r["outcome"] for r in rows] == ["error"] * 3
    # every case was attempted, so the held-out data is closed to other folders;
    # the error rows are retried in this folder (they used to be final)
    assert (out / evaluate.HELDOUT_STAMP).exists()
    with pytest.raises(RuntimeError, match="one-shot"):
        evaluate.run_eval(cases, Paid, tmp_path / "second_try", whole_run=cases)
    api_up["ok"] = True
    rows = evaluate.run_eval(cases, Paid, out, whole_run=cases)       # the same command again
    assert [r["outcome"] for r in rows] == ["escalated"] * 3 and len(calls) == 6
    assert (out / evaluate.HELDOUT_STAMP).exists()
    assert all(r["cost_usd"] == 0.5 for r in rows)                   # the failed try is still counted
    saved = json.loads((out / "ts-order-service_cpu_1.json").read_text())
    assert saved["usage"]["failed_attempts_usd"] == 0.25
    evaluate.run_eval(cases, Paid, out, whole_run=cases)              # finished: nothing runs again
    assert len(calls) == 6


def test_a_brief_cut_short_by_a_kill_is_run_again(tmp_path, monkeypatch):
    case, out = heldout_cases(1)[0], tmp_path / "tt"
    out.mkdir()
    (out / "ts-order-service_cpu_1.json").write_text('{"status": "diagnosed", "root_cause": {"servi')
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: {"status": "escalated", "usage": {}})
    rows = evaluate.run_eval([case], lambda: None, out, whole_run=[case])   # was JSONDecodeError
    assert rows[0]["outcome"] == "escalated" and not list(out.glob("*.tmp"))


def test_the_spend_cap_counts_what_failed_attempts_cost(tmp_path, monkeypatch):
    cases, out = heldout_cases(2), tmp_path / "tt"
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: (_ for _ in ()).throw(ConnectionError("down")))
    evaluate.run_eval(cases, Paid, out, whole_run=cases)             # two error rows, $0.25 each
    ran = []
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: ran.append(case.case_id) or {"status": "escalated", "usage": {}})
    rows = evaluate.run_eval(cases, Paid, out, max_usd=0.4, whole_run=cases)
    assert ran == []                                                 # the folder already cost $0.50: cap reached
    assert [r["outcome"] for r in rows] == ["error", "error"]        # and the saved rows are still reported
    rows = evaluate.run_eval(cases, Paid, out, max_usd=0.6, whole_run=cases)
    assert ran == ["ts-order-service_cpu/1"]                         # $0.50 < $0.60: one retry, then $0.75 stops it
    assert [r["outcome"] for r in rows] == ["escalated", "error"] and rows[0]["cost_usd"] == 0.5


def test_saved_only_scores_what_is_saved_and_runs_nothing(tmp_path, monkeypatch):
    cases, out, ran = heldout_cases(3), tmp_path / "tt", []
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: (_ for _ in ()).throw(ConnectionError("down")))
    evaluate.run_eval(cases[:1], Paid, out, whole_run=cases)         # case 1: a crash record
    monkeypatch.setattr(loop, "diagnose", lambda case, model, **kw: ran.append(1) or {"status": "escalated", "usage": {}})
    evaluate.run_eval(cases[1:2], Paid, out, whole_run=cases)        # case 2: a result; case 3 never ran
    ran.clear()
    built = []
    rows = evaluate.run_eval(cases, lambda: built.append(1), out, whole_run=cases, saved_only=True)
    assert ran == [] and built == []                                 # the local runner's last pass, server stopped
    assert [r["outcome"] for r in rows] == ["error", "escalated"]
    assert "ConnectionError" in rows[0]["reason"]                    # the crash record is kept, not overwritten


# ------------------------------------------------------------ 6. baseline digest on logs with no severity

def test_baseline_digest_sweeps_message_text_when_the_logs_record_no_severity(tmp_path):
    logs = log_rows((2, "ts-order-service", None, "java.lang.IllegalStateException: boom"),
                    (3, "ts-order-service", None, "request failed"), (4, "ts-ui-service", None, "ok"))
    case = make_case(tmp_path, metrics=metric_table(), logs=logs, traces=spans(lambda i: 200))
    text, raw = baselines.digest(case)
    assert list(raw["logs"]) == ["text 'error'", "text 'exception'", "text 'failed'"]
    assert "record no severity" in text and "IllegalStateException" in text
    assert "## Logs: warning and error lines" not in text


def test_baseline_digest_is_unchanged_when_levels_exist(tmp_path):
    case = make_case(tmp_path, metrics=metric_table(), logs=routine_and_new(), traces=spans(lambda i: 200))
    text, raw = baselines.digest(case)
    assert list(raw["logs"]) == ["error", "warning"] and "## Logs: warning and error lines" in text


# ------------------------------------------------------------ 7. loader odds and ends

def test_inject_time_written_as_a_float_is_read(tmp_path):
    run = tmp_path / "ts-order-service_cpu" / "1"
    run.mkdir(parents=True)
    (run / "inject_time.txt").write_text("1700000300.0\n")
    case = loaders._read_case("online-boutique", run.parent, run)
    assert case.inject_time == 1700000300 and case.root_cause_service == "ts-order-service"
