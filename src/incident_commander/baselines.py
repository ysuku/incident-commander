"""Non-agent baselines the agent is measured against (the eval plan's "systems compared").

  plain  single prompt: one fixed telemetry digest, one model call. No tools, no
         follow-up queries, no citation gate, no critic.
  rag    RAG-only: the same digest plus runbook chunks retrieved (MMR) with a query
         built in code from the digest. Still one model call and no gate.

The digest is built in code by the same tool functions the agent calls (so the
baseline is not handicapped by raw number series), with fixed arguments: the
first look an on-call engineer would take. What the baselines lack is everything
the agent adds: choosing what to look at next, the two-kinds gate, the fault
critic, and the rule that runbooks never steer a diagnosis.

Both return a brief in the agent's schema, so `evaluate.score` and the summary
treat them like any other run. A baseline may decline ({"escalate": true}); that
is scored as an escalation.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from . import tools
from .loaders import IncidentCase
from .loop import Z_SIGNIFICANT, _model_line, _parse

log = logging.getLogger("incident_commander")
KINDS = ("plain", "rag")
FAULTS = ("cpu", "mem", "disk", "socket", "delay", "loss")
TOP_METRICS = 15
LOG_SAMPLES = 8
LOG_TEXT_SWEEP = ("error", "exception", "failed")  # searched in the message text when the logs record no severity
WORST_TRACES = 5
RUNBOOK_K = 4
RUNBOOK_CHARS = 700      # per retrieved chunk in the prompt
MAX_CALLS = 2            # the one prompt, plus one retry if the reply is not JSON

SYSTEM_PROMPT = """You are diagnosing one recorded microservice incident from a telemetry digest.
You get one look: there are no tools and no follow-up questions. Work ONLY from the digest.
Respond with ONE JSON object and nothing else:
  {"root_cause_service": "...", "fault_type": "cpu|mem|disk|socket|delay|loss",
   "confidence": "high|medium|low", "rationale": "<two sentences citing the digest>",
   "proposed_fix": {"description": "...", "destructive": true|false}}
or, if the digest does not support a diagnosis:
  {"escalate": true, "reason": "...", "candidates": ["svc: hypothesis", ...]}"""


def digest(case: IncidentCase) -> tuple[str, dict]:
    """(text for the prompt, the raw tool results). Fixed calls, fixed arguments."""
    logs = {lvl: tools.log_search(case, level=lvl, limit=LOG_SAMPLES) for lvl in ("error", "warning")}
    log_title = "## Logs: warning and error lines"
    if not any(r.get("severity_recorded", True) for r in logs.values()):
        # no severity in these logs (RE3): a level sweep is empty by construction and
        # would read as "0 errors". Take the first look by message text instead.
        logs = {f"text '{q}'": tools.log_search(case, query=q, limit=LOG_SAMPLES) for q in LOG_TEXT_SWEEP}
        log_title = ("## Logs: these logs record no severity; lines whose message contains "
                     + ", ".join(f"'{q}'" for q in LOG_TEXT_SWEEP))
    raw = {"service_map": tools.service_map(case),
           "metrics": tools.metric_query(case, top=TOP_METRICS),
           "logs": logs,
           "traces": tools.trace_lookup(case, failing_only=True, limit=WORST_TRACES)}
    lines = ["## Service map (caller -> callees, from traces)"]
    lines += [f"{a} -> {', '.join(b)}" for a, b in raw["service_map"]["edges"].items()]
    m = raw["metrics"]
    lines += ["", f"## Metrics: top {TOP_METRICS} of {m.get('n_series_scanned', '?')} series by |z| "
                  "(mean after the alert vs the baseline before it)"]
    lines += [f"{d['series']}: before {d['before_mean']:g}, after {d['after_mean']:g}, "
              f"change {_pct(d)}, z={d['z']:+.2f}" for d in m["top_deviations"]]
    lines += ["", log_title]
    for lvl, r in raw["logs"].items():
        lines.append(f"{lvl}: {r['total_matches']} lines of {r['scope_lines']} searched "
                     f"(levels present: {', '.join(r.get('levels_present') or []) or 'none'}); "
                     f"by service and level: {json.dumps(r.get('matches_by_service_level') or {})}")
        lines += [f"  {json.dumps(s, default=str)[:300]}" for s in r.get("sample_after_alert") or []]
    t = raw["traces"]
    lines += ["", f"## Traces: the {WORST_TRACES} worst of {t.get('n_traces_after_alert', '?')} after the alert "
                  "(errored first, then slowest)"]
    for tr in t.get("traces") or []:
        errs = ", ".join(sorted({e["service"] for e in tr.get("error_spans") or []})) or "none"
        lines.append(f"{tr['path']} | {tr['duration_ms']} ms | {tr['n_spans']} spans | error spans in: {errs}")
    if not t.get("traces"):
        lines.append("no failing traces after the alert")
    return "\n".join(lines), raw


def _pct(d: dict) -> str:
    """'+38.5%'; the tool gives no percentage when the baseline mean is 0."""
    return "n/a (baseline 0)" if d.get("pct_change") is None else f"{d['pct_change']:+.1f}%"


def retrieval_query(raw: dict) -> str:
    """The runbook query, written in code from observed telemetry: the significant
    metric deviations, or the bare alert when nothing deviates."""
    big = [d for d in raw["metrics"]["top_deviations"] if abs(d["z"]) >= Z_SIGNIFICANT][:3]
    if not big:
        return "latency or availability alert, no significant metric deviation"
    return "; ".join(f"{d['service']} {d['metric']} {'up' if d['z'] > 0 else 'down'}"
                     + ("" if d.get("pct_change") is None else f" {abs(d['pct_change']):.0f}%") for d in big)


def _runbooks(raw: dict) -> tuple[str, list[dict]]:
    query = retrieval_query(raw)
    hits = tools.runbook_lookup(query, k=RUNBOOK_K)
    lines = ["", f"## Runbook excerpts retrieved for: {query}"]
    lines += [f"[{h['source_ref']}] {h.get('alert', '')}: {str(h['text'])[:RUNBOOK_CHARS]}" for h in hits]
    if not hits:
        lines.append("no runbook was close enough")
    return "\n".join(lines), [{"source_ref": h["source_ref"], "alert": h.get("alert"),
                               "distance": h.get("distance")} for h in hits]


def _reply(parsed: dict) -> dict:
    """The model's answer in one shape; a loop-style {"action": {"finish": ...}} is accepted too."""
    finish = (parsed.get("action") or {}).get("finish") if isinstance(parsed.get("action"), dict) else None
    return finish if isinstance(finish, dict) else parsed


def diagnose(case: IncidentCase, model, kind: str = "plain") -> dict:
    """Run one baseline on one case; returns a brief in the agent's schema."""
    if kind not in KINDS:
        raise ValueError(f"unknown baseline {kind!r}; choose one of {list(KINDS)}")
    t_run = time.monotonic()
    text, raw = digest(case)
    retrieved = None
    if kind == "rag":
        extra, retrieved = _runbooks(raw)
        text += "\n" + extra
    tool_seconds = time.monotonic() - t_run
    alert_utc = datetime.fromtimestamp(case.inject_time, tz=timezone.utc)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{case.system}: latency/availability alert at "
                                            f"{alert_utc:%Y-%m-%d %H:%M}Z. Find root-cause service and "
                                            f"fault type.\n\n{text}"}]
    totals = {"model_calls": 0, "model_seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
    answer, problem = None, ""
    for _ in range(MAX_CALLS):
        reply = model.complete(messages)
        call = getattr(model, "last_call", None) or {}
        totals["model_calls"] += 1
        totals["model_seconds"] += call.get("seconds") or 0
        totals["prompt_tokens"] += call.get("prompt_tokens") or 0
        totals["completion_tokens"] += call.get("completion_tokens") or 0
        log.info("  baseline %s: %s", kind, _model_line(call))
        try:
            answer = _reply(_parse(reply))
            break
        except ValueError as e:  # json.JSONDecodeError is a ValueError
            problem = f"model output unparseable: {e}"
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": "That was not one JSON object. Reply with the JSON only."}]

    brief: dict = {"architecture": f"baseline-{kind}", "investigation": [],
                   "guardrails": "none: no citation gate, no critic, fix not forced to need approval",
                   "digest_chars": len(text)}
    if retrieved is not None:
        brief["retrieved"] = retrieved
    service = str((answer or {}).get("root_cause_service") or "").strip()
    if answer is None or answer.get("escalate") or not service:
        code = "unparseable" if answer is None else "model_declined"
        reason = problem if answer is None else str(answer.get("reason") or "the model named no service")
        candidates = [str(c) for c in (answer or {}).get("candidates") or []]
        brief.update(status="escalated", reason=reason, candidates=candidates,
                     escalation={"code": code, "reason": reason, "hypotheses": [],
                                 "note": "baseline: the candidates are the model's own words, unchecked"})
    else:
        brief.update(status="diagnosed",
                     root_cause={"service": service, "fault_type": str(answer.get("fault_type") or "").lower()},
                     confidence=answer.get("confidence"), rationale=answer.get("rationale"),
                     proposed_fix=answer.get("proposed_fix"))
    totals["model_seconds"] = round(totals["model_seconds"], 1)
    brief["usage"] = {**totals, "tool_seconds": round(tool_seconds, 1),
                      "wall_seconds": round(time.monotonic() - t_run, 1)}
    log.info("  baseline %s: %s%s", kind, brief["status"],
             f" {service}/{brief['root_cause']['fault_type']}" if brief["status"] == "diagnosed" else "")
    return brief
