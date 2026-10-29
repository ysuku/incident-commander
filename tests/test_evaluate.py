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

