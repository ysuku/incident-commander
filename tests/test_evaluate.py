"""Eval runner tests: scoring against labels, resume, spend cap. No data or keys needed."""
import json
from pathlib import Path

from incident_commander import evaluate, loop
from incident_commander.loaders import IncidentCase


def case(cid="checkoutservice_cpu/1"):
    svc_fault, _, run = cid.partition("/")
    svc, _, fault = svc_fault.rpartition("_")
    return IncidentCase(cid, "online-boutique", run, 0, Path("."), svc, fault)


def diagnosed(service, fault):
    return {"status": "diagnosed", "root_cause": {"service": service, "fault_type": fault}}


def test_score_outcomes():
    c = case()
    assert evaluate.score(c, diagnosed("checkoutservice", "cpu")) == "correct"
    assert evaluate.score(c, diagnosed("checkout", "CPU")) == "correct"   # normalized
    assert evaluate.score(c, diagnosed("checkoutservice", "mem")) == "service_only"
    assert evaluate.score(c, diagnosed("redis", "cpu")) == "wrong"
    assert evaluate.score(c, {"status": "escalated"}) == "escalated"
    assert evaluate.score(c, {"status": "error"}) == "error"


class Priced:
    """Fake model whose every run costs $1, so the cap is easy to hit."""
    cost_usd = 1.0


def test_resume_and_spend_cap(tmp_path, monkeypatch):
    calls = []

    def fake_diagnose(c, model):
        calls.append(c.case_id)
        return {**diagnosed(c.root_cause_service, c.fault_type), "investigation": [], "usage": {}}

    monkeypatch.setattr(loop, "diagnose", fake_diagnose)
    cases = [case(f"svc{i}_cpu/1") for i in range(4)]
    # case 0 already saved: must be reused, not rerun
    (tmp_path / "svc0_cpu_1.json").write_text(json.dumps(
        {**diagnosed("svc0", "cpu"), "usage": {"cost_usd": 0.0}}))

    rows = evaluate.run_eval(cases, Priced, tmp_path, max_usd=2.0)
    assert calls == ["svc1_cpu/1", "svc2_cpu/1"]          # stopped once $2 was spent
    assert [r["outcome"] for r in rows] == ["correct"] * 3
    assert "3 cases run" in (tmp_path / "summary.md").read_text()

    evaluate.run_eval(cases, Priced, tmp_path, max_usd=None)  # resume finishes the rest
    assert calls[-1] == "svc3_cpu/1" and len(calls) == 3


def test_crash_is_recorded_not_raised(tmp_path, monkeypatch):
    def boom(c, model):
        raise RuntimeError("API down")
    monkeypatch.setattr(loop, "diagnose", boom)
    rows = evaluate.run_eval([case()], Priced, tmp_path)
    assert rows[0]["outcome"] == "error" and "API down" in rows[0]["reason"]


# --- per-service trace signal (trace_lookup(service=...)) ---------------------
def _spans(after_server_us, after_client_us):
    """frontend client span -> currencyservice server span, 4 calls before, 4 after."""
    import pandas as pd
    rows = []
    for i in range(8):
        late = i >= 4
        t = (2000 if late else 0) * 1000 + i
        srv = after_server_us if late else 100
        cli = after_client_us if late else 300
        rows += [{"traceID": f"t{i}", "spanID": f"c{i}", "serviceName": "frontendservice",
                  "operationName": "hipstershop.CurrencyService/Convert", "startTimeMillis": t,
                  "duration": cli, "statusCode": 0, "parentSpanID": None},
                 {"traceID": f"t{i}", "spanID": f"s{i}", "serviceName": "currencyservice",
                  "operationName": "grpc.hipstershop.CurrencyService/Convert", "startTimeMillis": t,
                  "duration": srv, "statusCode": 0, "parentSpanID": f"c{i}"}]
    return pd.DataFrame(rows)


def test_service_spans_inbound_catches_network_delay():
    from incident_commander import tools
    c = IncidentCase("currencyservice_delay/1", "online-boutique", "1", 1000, Path("."),
                     "currencyservice", "delay")
    # server work unchanged, but callers wait 10x longer: delay is on the wire
    r = tools._service_spans(c, _spans(after_server_us=100, after_client_us=2100), "currency", 5)
    assert r["service"] == "currencyservice"
    assert r["overall"]["p50_ratio"] == 1.0
    assert r["inbound"]["p50_ratio"] == 10.0          # client self time 0.2ms -> 2.0ms
    kind, summary, _, implicated = loop._summarize("trace_lookup", r, {"service": "currency"})
    assert kind == "traces" and "SIGNIFICANT" in summary and implicated == ["currencyservice"]


def test_service_spans_no_change_is_said_plainly():
    from incident_commander import tools
    c = IncidentCase("x_cpu/1", "online-boutique", "1", 1000, Path("."), "x", "cpu")
    r = tools._service_spans(c, _spans(after_server_us=110, after_client_us=320), "currencyservice", 5)
    _, summary, _, implicated = loop._summarize("trace_lookup", r, {"service": "currencyservice"})
    assert "NO significant change" in summary and implicated == []


def test_service_spans_unknown_service_lists_names():
    import pytest
    from incident_commander import tools
    c = IncidentCase("x_cpu/1", "online-boutique", "1", 1000, Path("."), "x", "cpu")
    with pytest.raises(ValueError, match="trace services are"):
        tools._service_spans(c, _spans(100, 300), "redis", 5)
