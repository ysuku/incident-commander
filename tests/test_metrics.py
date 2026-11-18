"""Checkpoint 6.1 metrics: escalation codes, handover quality, the summary
section and the offline rescore. Hand-built briefs; no data, model or keys."""
import csv
import json
from pathlib import Path

import pytest

from incident_commander import evaluate
from incident_commander.loaders import IncidentCase


def case(cid="checkoutservice_mem/1"):
    svc_fault, _, run = cid.partition("/")
    svc, _, fault = svc_fault.rpartition("_")
    return IncidentCase(cid, "online-boutique", run, 0, Path("."), svc, fault)


def diagnosed(service, fault, **extra):
    return {"status": "diagnosed", "root_cause": {"service": service, "fault_type": fault},
            "usage": {"wall_seconds": 10.0, "cost_usd": 0.05, "model_seconds": 8.0, "tool_seconds": 1.5},
            **extra}


def escalated(reason, candidates=(), **extra):
    return {"status": "escalated", "reason": reason, "candidates": list(candidates),
            "usage": {"wall_seconds": 30.0, "cost_usd": 0.1}, **extra}


def branch(service, fault, status="open"):
    return {"service": service, "fault_type": fault, "status": status}


def tot(status, ranked, tied=(), selected=None):
    return {"status": status, "ranked": list(ranked), "tied": list(tied), "selected": selected}


TIE = "tie: checkoutservice: mem or checkoutservice: disk, indistinguishable in this data"


# --- escalation_code ----------------------------------------------------------
@pytest.mark.parametrize("reason, code", [
    ("step budget (8) exhausted", "budget_exhausted"),
    ("model output unparseable", "unparseable"),
    (TIE, "tot_tie"),
    ("tot: no branch passed the two-kinds gate", "tot_no_branch"),
    ("tot selected checkoutservice: mem but the gate refused: one kind", "tot_selected_gate_refused"),
    ("unfamiliar: no known fault signature fits", "unfamiliar"),
    ("something new", "other"),
])
def test_escalation_code_from_old_reason_strings(reason, code):
    assert evaluate.escalation_code(escalated(reason)) == code
    assert code in evaluate.ESCALATION_CODES


def test_escalation_code_only_for_escalations():
    assert evaluate.escalation_code(diagnosed("checkoutservice", "mem")) == ""
    # a crash carries its message where a reason would be; it is not an escalation
    assert evaluate.escalation_code({"status": "error", "error": "step budget lookalike"}) == ""


def test_structured_block_wins_over_reason_text():
    brief = escalated("step budget (8) exhausted", escalation={"code": "unfamiliar"})
    assert evaluate.escalation_code(brief) == "unfamiliar"
    assert evaluate.escalation_code(escalated("x", escalation={"code": "not-a-code"})) == "other"


def test_all_branches_pruned_reads_as_unfamiliar():
    reason = "tot: no branch passed the two-kinds gate"   # same text either way
    pruned = tot("escalated", [branch("checkoutservice", "mem", "pruned"), branch("redis", "cpu", "pruned")])
    still_open = tot("escalated", [branch("checkoutservice", "mem"), branch("redis", "cpu", "pruned")])
    assert evaluate.escalation_code(escalated(reason, tot=pruned)) == "unfamiliar"
    assert evaluate.escalation_code(escalated(reason, tot=still_open)) == "tot_no_branch"


# --- handover_truth -----------------------------------------------------------
def test_handover_tie_uses_the_tied_branches():
    c = case()
    mem, disk, cpu = (branch("checkoutservice", f, "tied") for f in ("mem", "disk", "cpu"))
    assert evaluate.handover_truth(c, escalated(TIE, tot=tot("tied", [mem, disk], [mem, disk]))) == "tie_with_truth"
    # the truth sits in ranked (pruned) but was not one of the tied pair
    lost = tot("tied", [disk, cpu, branch("checkoutservice", "mem", "pruned")], [disk, cpu])
    assert evaluate.handover_truth(c, escalated(TIE, tot=lost)) == "tie_without_truth"


def test_handover_tot_escalation_ignores_pruned_branches():
    c = case()
    reason = "tot: no branch passed the two-kinds gate"
    top = tot("escalated", [branch("checkoutservice", "mem"), branch("checkoutservice", "disk")])
    second = tot("escalated", [branch("checkoutservice", "disk"), branch("checkoutservice", "mem")])
    pruned = tot("escalated", [branch("redis", "cpu"), branch("checkoutservice", "mem", "pruned")])
    assert evaluate.handover_truth(c, escalated(reason, tot=top)) == "top_live_is_truth"
    assert evaluate.handover_truth(c, escalated(reason, tot=second)) == "service_only"
    assert evaluate.handover_truth(c, escalated(reason, tot=pruned)) == "none"


def test_handover_free_text_candidates():
    c = case()
    budget = "step budget (8) exhausted"
    right = ["checkoutservice: memory pressure (E1 mem z=231)", "redis: cpu"]
    # the evidence note mentions mem, the hypothesis does not: not a fault match
    wrong_fault = ["checkoutservice: CPU saturation (E1 cpu z=231; mem z=40)", "redis: cpu"]
    listed = ["redis: memory leak", "checkoutservice: disk"]     # true service second
    assert evaluate.handover_truth(c, escalated(budget, right)) == "top_live_is_truth"
    assert evaluate.handover_truth(c, escalated(budget, wrong_fault)) == "service_only"
    assert evaluate.handover_truth(c, escalated(budget, listed)) == "service_only"
    assert evaluate.handover_truth(c, escalated(budget, ["redis: mem"])) == "none"
    assert evaluate.handover_truth(c, escalated(budget)) == "none"
    assert evaluate.handover_truth(c, diagnosed("checkoutservice", "mem")) == ""


# --- tot_outcome: a selection the gate refused is not a selection --------------
def test_selected_gate_refused():
    c = case()
    sel = branch("checkoutservice", "mem")
    result = tot("selected", [sel], selected=sel)
    assert evaluate.tot_outcome(c, diagnosed("checkoutservice", "mem", tot=result)) == "selected"
    refused = escalated("tot selected checkoutservice: mem but the gate refused: one kind", tot=result)
    assert evaluate.tot_outcome(c, refused) == "selected_gate_refused"
    assert evaluate.escalation_code(refused) == "tot_selected_gate_refused"
    assert evaluate.handover_truth(c, refused) == "top_live_is_truth"


# --- row and summary ------------------------------------------------------------
def rows_for(briefs: dict) -> list[dict]:
    out = []
    for cid, brief in briefs.items():
        c = case(cid)
        out.append(evaluate._row(c, brief, evaluate.score(c, brief)))
    return out


def sample_briefs() -> dict:
    mem, disk = branch("svcb", "mem", "tied"), branch("svcb", "disk", "tied")
    return {
        "svca_cpu/1": diagnosed("svca", "cpu"),
        "svca_mem/1": diagnosed("svca", "disk"),                       # wrong fault
        "svca_disk/1": diagnosed("redis", "disk"),                     # wrong service
        "svcb_mem/1": escalated("tie: svcb: mem or svcb: disk", tot=tot("tied", [mem, disk], [mem, disk])),
        "svcb_cpu/1": escalated("step budget (8) exhausted", ["svcb: cpu saturation"]),
        "svcb_loss/1": {"status": "error", "error": "RuntimeError: API down", "usage": {"wall_seconds": 1.0}},
    }


def test_row_appends_the_new_columns():
    row = rows_for({"svca_cpu/1": diagnosed("svca", "cpu")})[0]
    assert list(row)[-7:] == ["fix_flags", "escalation_code", "handover_truth", "fix_advised",
                              "fix_dropped", "model_seconds", "tool_seconds"]
    assert (row["fix_advised"], row["fix_dropped"], row["model_seconds"], row["tool_seconds"]) == (0, 0, 8.0, 1.5)
    old = rows_for({"svca_cpu/1": {"status": "escalated", "reason": "step budget (8) exhausted"}})[0]
    assert old["model_seconds"] == "" and old["escalation_code"] == "budget_exhausted"


def test_metrics_section_text(tmp_path):
    md = evaluate.write_summary(rows_for(sample_briefs()), tmp_path)
    assert "| correct | 1 | 20% |" in md                # the existing table is still there
    section = md.split("## 6.1 metrics")[1]
    for line in ("- correct rate: 1 of 5 (20%)",
                 "- wrong-service (diagnosed, wrong service): 1 of 5 (20%)",
                 "- wrong-fault (right service, wrong fault): 1 of 5 (20%)",
                 "- escalation rate: 2 of 5 (40%)",
                 "- escalations by code: tot_tie 1, budget_exhausted 1",
                 "- escalation quality (ToT branches, exact; 1 of 2 escalations): tie_with_truth 1",
                 "- escalation quality (model's candidates, fault match approximate; 1 of 2 escalations): "
                 "top_live_is_truth 1",
                 "- groundedness: no Fix Advisor rows in this run",
                 "- cost per case: $0.058",
                 "- latency per case: mean 18.0s, median 10.0s, p95 30.0s (nearest rank)"):
        assert line in section, line


def test_groundedness_only_counts_advised_briefs(tmp_path):
    advised = diagnosed("svca", "cpu", fix_advisor={"dropped": ["runbook_refs 'R9': not retrieved", "x"]},
                        proposed_fix={"contradictions": [{"runbook": "R1"}]})
    md = evaluate.write_summary(rows_for({"svca_cpu/1": advised, "svca_cpu/2": diagnosed("svca", "cpu")}), tmp_path)
    assert ("- groundedness: 1 briefs advised; dropped references 2 (2.00 per brief); "
            "runbook contradiction flags 1 (1.00 per brief)") in md


def test_p95_is_nearest_rank():
    assert evaluate._p95(list(range(1, 101))) == 95
    assert evaluate._p95([5.0]) == 5.0
    assert evaluate._p95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 10


def test_summary_of_no_rows(tmp_path):
    assert "- correct rate: 0 of 0" in evaluate.write_summary([], tmp_path)


# --- rescore ----------------------------------------------------------------------
def test_rescore_reads_saved_briefs_only(tmp_path, monkeypatch):
    def no_model(*a, **kw):
        raise AssertionError("rescore must not run the agent")
    monkeypatch.setattr(evaluate, "diagnose", no_model)

    src, out = tmp_path / "run", tmp_path / "run_rescored"
    src.mkdir()
    briefs = sample_briefs()
    for cid, brief in briefs.items():
        (src / f"{cid.replace('/', '_')}.json").write_text(json.dumps(brief))
    (src / "results.csv").write_text("stale\n")
    cases = [case(cid) for cid in briefs] + [case("svcz_cpu/1")]       # svcz has no saved brief

    rows = evaluate.rescore(cases, src, out)
    assert [r["case_id"] for r in rows] == list(briefs)
    assert (src / "results.csv").read_text() == "stale\n"             # the source is untouched
    assert not (src / "summary.md").exists()
    saved = list(csv.DictReader(open(out / "results.csv", encoding="utf-8")))
    assert [r["outcome"] for r in saved] == ["correct", "service_only", "wrong", "escalated", "escalated", "error"]
    assert [r["escalation_code"] for r in saved] == ["", "", "", "tot_tie", "budget_exhausted", ""]
    assert "## 6.1 metrics" in (out / "summary.md").read_text()

    with pytest.raises(ValueError):
        evaluate.rescore(cases, src, src)


def test_rescore_cli_defaults_to_a_sibling_folder(tmp_path, monkeypatch, capsys):
    from incident_commander import cli, loaders

    def no_model(name):
        raise AssertionError("rescore must not build a model")
    monkeypatch.setattr(cli, "make_model", no_model)
    monkeypatch.setattr(loaders, "list_cases", lambda system: [case("svca_cpu/1")])
    src = tmp_path / "run"
    src.mkdir()
    (src / "svca_cpu_1.json").write_text(json.dumps(diagnosed("svca", "cpu")))
    monkeypatch.setattr("sys.argv", ["incident_commander", "rescore", str(src)])
    cli.main()
    assert (tmp_path / "run_rescored" / "results.csv").exists()
    assert sorted(p.name for p in src.iterdir()) == ["svca_cpu_1.json"]
    assert "1 saved briefs re-scored" in capsys.readouterr().out
