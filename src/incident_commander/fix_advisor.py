"""The Fix Advisor (Checkpoint 5.1): runbooks only AFTER the diagnosis is settled.

In 3.1 a retrieved runbook visibly changed the model's root-cause answer. That
is wanted for the fix and never for the diagnosis, so runbook access now lives
in a separate role that runs only once the citation gate has accepted a
diagnosis. It receives only the accepted diagnosis and its cited evidence (not
the Commander's candidates, notes or rejected alternatives), looks up runbooks
for the diagnosed symptom (one tool call, query built in code), and makes one
model call to draft a conditional fix and to flag runbook steps the evidence
contradicts. Code then validates every reference the model makes (runbook ids,
evidence ids) and drops what it cannot trace. Every fix still waits for a human.
"""
from __future__ import annotations

import logging
import time

from . import signatures as sig
from .loop import _parse

log = logging.getLogger("incident_commander")

PROMPT = """You are the Fix Advisor for one diagnosed microservice incident.
The diagnosis is settled and is not yours to change: {service} / {fault_type}.
You see only that diagnosis, the telemetry evidence that supports it, and runbook excerpts retrieved for it.
Draft a conditional fix for the on-call engineer. Every step must be reversible or gated; a human approves before anything runs.
Flag any runbook step that the evidence contradicts (cite the evidence id) and leave it out of the plan.
Respond with ONE JSON object and nothing else:
  {{"rationale": "<one sentence>",
   "fix": {{"summary": "<one sentence>", "steps": ["<step>", ...],
           "preconditions": ["<what to verify before acting>", ...], "rollback": "<how to undo>"}},
   "runbook_refs": ["R1", ...],
   "contradictions": [{{"runbook": "R1", "step": "<the runbook step, quoted>", "evidence": "E2", "why": "<one sentence>"}}]}}
If no runbook applies, say so in rationale, leave runbook_refs empty, and base the fix on the evidence alone."""

# runbook_lookup wants a symptom written from observed telemetry, not the raw alert
SYMPTOMS = {
    "cpu": "{svc} CPU at its limit and throttled, latency rising",
    "mem": "{svc} memory usage rising towards its limit, possible leak, latency rising",
    "disk": "{svc} disk I/O saturated, slow writes, latency rising",
    "socket": "{svc} sockets or connections exhausted, connection errors and timeouts",
    "delay": "{svc} network latency injected on its pod, every call slow, resources normal",
    "loss": "{svc} packet loss on its pod network, retries and connections held open",
}


def advise(brief: dict, toolbox, model, k: int = 4) -> dict:
    """Runbook lookup + one model call for an accepted brief. Returns the advice
    record (what was retrieved, what the model said, what was dropped and why).
    `toolbox` must be the Fix Advisor's scoped client (runbook_lookup only)."""
    rc = brief["root_cause"]
    service, fault = rc["service"], str(rc.get("fault_type", "")).lower()
    query = SYMPTOMS.get(fault, "{svc} {fault} fault").format(svc=service, fault=fault)
    cited = brief.get("supporting_evidence") or []
    advice = {"query": query, "runbooks": [], "tool_seconds": 0.0, "model_call": {},
              "contradictions": [], "dropped": [], "by": "model"}

    t0 = time.monotonic()
    try:
        hits = toolbox.call("runbook_lookup", {"symptom": query, "k": k}) or []
    except (TypeError, ValueError) as e:
        hits, advice["lookup_error"] = [], str(e)[:200]
    advice["tool_seconds"] = round(time.monotonic() - t0, 2)
    refs = {f"R{i + 1}": h for i, h in enumerate(hits)}
    advice["runbooks"] = [{"id": rid, "alert": h["alert"], "kind": h["kind"], "distance": h["distance"],
                           "source_ref": h["source_ref"]} for rid, h in refs.items()]
    log.info("fix advisor: runbook_lookup(%r) -> %s", query,
             ", ".join(f"{rid}={h['alert']}/{h['kind']}" for rid, h in refs.items()) or "no match")

    system = PROMPT.format(service=service, fault_type=fault)
    raw = model.complete([{"role": "system", "content": system},
                          {"role": "user", "content": render(rc, cited, refs)}])
    advice["model_call"] = dict(getattr(model, "last_call", {}) or {})
    try:
        msg = _parse(raw)
        fix = dict(msg.get("fix") or {})
        if not str(fix.get("summary", "")).strip():
            raise ValueError("no fix.summary")
        advice["fix"] = {"summary": str(fix["summary"])[:400],
                         "steps": [str(s)[:300] for s in (fix.get("steps") or [])][:8],
                         "preconditions": [str(s)[:300] for s in (fix.get("preconditions") or [])][:6],
                         "rollback": str(fix.get("rollback", ""))[:300]}
        advice["rationale"] = str(msg.get("rationale", ""))[:300]
        advice["runbook_refs"] = [r for r in (msg.get("runbook_refs") or []) if r in refs]
        for r in (msg.get("runbook_refs") or []):
            if r not in refs:
                advice["dropped"].append(f"runbook_refs {r!r}: not among the retrieved runbooks")
        eids = {e["eid"] for e in cited}
        for c in (msg.get("contradictions") or []):
            c = dict(c) if isinstance(c, dict) else {}
            if c.get("runbook") not in refs:
                advice["dropped"].append(f"contradiction cites runbook {c.get('runbook')!r}: not retrieved")
            elif c.get("evidence") not in eids:
                advice["dropped"].append(f"contradiction cites {c.get('evidence')!r}: not among the cited evidence")
            else:
                advice["contradictions"].append({k: str(c.get(k, ""))[:300]
                                                 for k in ("runbook", "step", "evidence", "why")})
    except Exception as e:  # an unusable reply never blocks the brief: template fix, no flags
        advice["by"] = f"template (model fallback: {str(e)[:80]})"
        advice["fix"] = {"summary": sig.REMEDIATION.get(fault, "Investigate {svc}").format(svc=service),
                         "steps": [], "preconditions": [], "rollback": ""}
        advice["runbook_refs"] = []
    log.info("fix advisor: %s | %d runbook refs | %d contradictions flagged | %d dropped",
             advice["by"], len(advice["runbook_refs"]), len(advice["contradictions"]), len(advice["dropped"]))
    return advice


def render(rc: dict, cited: list[dict], refs: dict) -> str:
    """The Fix Advisor's whole view: diagnosis, cited evidence, retrieved runbooks."""
    lines = [f"DIAGNOSIS (accepted by the citation gate): {rc['service']} / {rc.get('fault_type')}", "",
             "CITED EVIDENCE:"]
    lines += [f"  {e['eid']} [{e['kind']}] {e['summary']} (source: {e['source_ref']})" for e in cited]
    lines += ["", "RUNBOOKS RETRIEVED (reference, not evidence):"]
    if not refs:
        lines.append("  (no runbook matched the symptom)")
    for rid, h in refs.items():
        lines.append(f"  {rid} {h['alert']} ({h['kind']}, d={h['distance']}, {h['source_ref']}): "
                     + " ".join(str(h["text"]).split())[:600])
    return "\n".join(lines)


def apply(brief: dict, advice: dict) -> dict:
    """Fold the advice into the brief's proposed_fix. Approval stays forced in code."""
    old = dict(brief.get("proposed_fix") or {})
    refs = {r["id"]: r for r in advice["runbooks"]}
    brief["proposed_fix"] = {
        "description": advice["fix"]["summary"],
        "steps": advice["fix"]["steps"],
        "preconditions": advice["fix"]["preconditions"],
        "rollback": advice["fix"]["rollback"],
        "runbook_refs": [refs[r]["source_ref"] for r in advice["runbook_refs"]],
        "contradictions": advice["contradictions"],
        "model_labeled_destructive": old.get("model_labeled_destructive", True),
        "requires_approval": True,
        "advised_by": advice["by"],
    }
    brief["fix_advisor"] = {**advice, "commander_draft": old.get("description")}
    return brief
