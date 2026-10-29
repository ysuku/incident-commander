"""The ReAct loop (Checkpoint 2.1): orient -> reason -> act -> observe -> stop/escalate.

Design rules, enforced in CODE, not left to the model:
- finish is REJECTED unless the cited evidence spans >= 2 independent telemetry
  kinds (metrics/logs/traces) and every citation id exists (the citation gate).
- a destructive fix always carries requires_approval=True.
- when the step budget runs out, the agent escalates with findings instead of
  guessing (status: "escalated").
- the model sees only the scratchpad rendering; raw telemetry never enters context.
- tools are reached only through the MCP server (mcp_server.py); the tool list in
  the prompt is rendered from the server's schemas, so the two cannot drift.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone

from .loaders import IncidentCase
from .mcp_server import ToolClient, build_server
from .scratchpad import TELEMETRY_KINDS, Scratchpad

MAX_STEPS = 8
Z_SIGNIFICANT = 3.0  # "3-sigma": a deviation smaller than this is treated as noise
log = logging.getLogger("incident_commander")
MAX_PARSE_FAILURES = 2

SYSTEM_PROMPT = """You are Incident Commander, diagnosing one recorded microservice incident.
Work ONLY from the evidence board you are shown. Never invent log lines, metrics, or services.
Each turn, respond with ONE JSON object and nothing else:
  {"rationale": "<one sentence: why this action>", "candidates": ["svc: hypothesis", ...],
   "action": {"tool": "<name>", "args": {...}}}
or, when the evidence is sufficient:
  {"rationale": "<one sentence>", "action": {"finish": {
     "root_cause_service": "...", "fault_type": "cpu|mem|disk|socket|delay|loss",
     "supporting_evidence": ["E1", ...],
     "alternatives_rejected": [{"candidate": "...", "why": "... (cite Ex)"}],
     "proposed_fix": {"description": "...", "destructive": true|false}}}}
Tools: {tools}
A root cause needs two independent telemetry kinds (metrics/logs/traces) that EACH point at the claimed service. Finish is rejected otherwise."""


def _summarize(name: str, result, args: dict | None = None) -> tuple[str, str, str, list[str]]:
    """(kind, model-facing summary, source_ref, implicated services) for one tool result.

    Wording matters: a summary must never make noise look like a finding. The
    implicated-services list is computed here, in code, and is what the citation
    gate checks, so the model cannot claim support the evidence doesn't give.
    """
    args = args or {}
    if name == "metric_query":
        top = result["top_deviations"]
        scope = ", ".join(f"{k}={v}" for k, v in args.items() if k in ("service", "metric")) or "all services"
        if not top:
            return "metrics", f"no metric series in scope [{scope}]", "simple_metrics.csv", []
        sig = [d for d in top if abs(d["z"]) >= Z_SIGNIFICANT]
        fmt = lambda d: (f"{d['series']} z={d['z']} "
                         f"({d['pct_change']}%)" if d["pct_change"] is not None else f"{d['series']} z={d['z']}")
        if not sig:
            return ("metrics", f"NO significant deviation (|z|>={Z_SIGNIFICANT:g}) in scope [{scope}]; "
                    f"largest is only {fmt(top[0])}", top[0]["source_ref"], [])
        return ("metrics", f"significant deviations in scope [{scope}]: " + "; ".join(fmt(d) for d in sig[:4]),
                sig[0]["source_ref"], sorted({d["service"] for d in sig}))
    if name == "log_search":
        n, by = result["total_matches"], result["matches_by_service_level"]
        first = result["sample_after_alert"][:2]
        lines = "; ".join(f"[{s['service']}/{s['level']}] {s['message'][:80]}" for s in first)
        ref = first[0]["source_ref"] if first else "logs.csv"
        svcs = sorted({k.split("/")[0] for k, c in by.items() if c > 0})
        return "logs", f"{n} matching lines; by svc/level {by}; {lines or 'no sample'}", ref, svcs
    if name == "trace_lookup":
        ts = result["traces"]
        if not ts:
            return "traces", result.get("note", "no traces"), "traces.csv", []
        shown = ts[:3]
        lines = "; ".join(f"{t['path']} {t['duration_ms']}ms errors={len(t['error_spans'])}" for t in shown)
        svcs = {s for t in shown for s in t["path"].split(" -> ")}
        svcs |= {e["service"] for t in shown for e in t["error_spans"]}
        return ("traces", f"{result['n_traces_after_alert']} traces after alert; worst: {lines}",
                ts[0]["source_ref"], sorted(svcs))
    if name == "service_map":
        edges = "; ".join(f"{k}->{','.join(v)}" for k, v in result["edges"].items())
        return "map", f"dependencies: {edges}", result["source_ref"], []
    if name == "runbook_lookup":
        if not result:
            return "runbook", "no runbook match (abstained)", "runbook index", []
        h = result[0]
        return ("runbook", f"{len(result)} hits; top {h['alert']} ({h['kind']}, d={h['distance']}): "
                f"{h['text'][:120]}", h["source_ref"], [])
    return "map", str(result)[:200], name, []


def _norm(service) -> str:
    """'checkoutservice', 'checkout', 'Checkout' -> 'checkout'; logs say 'frontend', traces 'frontendservice'."""
    s = str(service or "").lower().strip()
    return s[: -len("service")] if s.endswith("service") else s


def _parse(raw: str) -> dict:
    """Tolerant JSON extraction: local reasoning models (Qwen3.x) may wrap the
    JSON in <think> blocks, code fences, or prose. We strip thinking, then take
    the outermost {...} span. Anything else is a parse failure the loop reports
    back to the model."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = raw.replace("```json", "```").replace("```", " ").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(raw[start:end + 1])


def _model_line(call: dict) -> str:
    """One readable line of model-call stats, e.g.
    'model 56.2s | in 1843 tok | out 812 tok (reasoning 2400 ch) | finish=stop'."""
    if call.get("mock"):
        return "model: mock (no cost)"
    parts = [f"model {call.get('seconds', '?')}s"]
    if call.get("prompt_tokens") is not None:
        parts.append(f"in {call['prompt_tokens']} tok")
    if call.get("completion_tokens") is not None:
        out = f"out {call['completion_tokens']}/{call.get('max_tokens', '?')} tok"
        if call.get("reasoning_chars"):
            out += f" (reasoning {call['reasoning_chars']} ch, answer {call.get('answer_chars', 0)} ch)"
        parts.append(out)
    fr = call.get("finish_reason")
    parts.append(f"finish={fr}" + ("  << HIT TOKEN LIMIT" if fr == "length" else ""))
    return " | ".join(parts)


def _short(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def diagnose(case: IncidentCase, model, max_steps: int = MAX_STEPS,
             toolbox: ToolClient | None = None) -> dict:
    """Run the loop on one case. Returns the incident brief (dict).

    Every step is recorded in brief["investigation"] (including failed steps) with
    the model call's time/token stats and an `outcome`, and is logged in plain
    language to the "incident_commander" logger. brief["usage"] holds run totals.
    Tools go through `toolbox`; by default a fresh MCP server for this case.
    """
    if toolbox is not None:
        return _investigate(case, model, max_steps, toolbox)
    with ToolClient(build_server(case)) as toolbox:
        return _investigate(case, model, max_steps, toolbox)


def _investigate(case: IncidentCase, model, max_steps: int, toolbox: ToolClient) -> dict:
    alert_utc = datetime.fromtimestamp(case.inject_time, tz=timezone.utc)
    pad = Scratchpad(alert=f"{case.system}: latency/availability alert at "
                           f"{alert_utc:%Y-%m-%d %H:%M}Z. Find root-cause service and fault type.")
    system = SYSTEM_PROMPT.replace("{tools}", toolbox.describe())
    trace, parse_failures, seen_calls = [], 0, {}
    totals = {"model_calls": 0, "model_seconds": 0.0, "tool_seconds": 0.0,
              "prompt_tokens": 0, "completion_tokens": 0, "replies_hit_token_limit": 0}
    t_run = time.monotonic()
    log.info("START case=%s | step budget=%d", case.case_id, max_steps)

    def done(brief: dict) -> dict:
        totals["wall_seconds"] = round(time.monotonic() - t_run, 1)
        totals["model_seconds"] = round(totals["model_seconds"], 1)
        totals["tool_seconds"] = round(totals["tool_seconds"], 1)
        brief["usage"] = totals
        log.info("END status=%s | %d model calls | %.0fs total (model %.0fs, tools %.1fs) | "
                 "tokens in %d / out %d | %d replies hit the token limit",
                 brief["status"], totals["model_calls"], totals["wall_seconds"],
                 totals["model_seconds"], totals["tool_seconds"], totals["prompt_tokens"],
                 totals["completion_tokens"], totals["replies_hit_token_limit"])
        return brief

    for step in range(1, max_steps + 1):
        raw = model.complete([{"role": "system", "content": system},
                              {"role": "user", "content": pad.render()}])
        call = dict(getattr(model, "last_call", {}) or {})
        totals["model_calls"] += 1
        totals["model_seconds"] += call.get("seconds") or 0
        totals["prompt_tokens"] += call.get("prompt_tokens") or 0
        totals["completion_tokens"] += call.get("completion_tokens") or 0
        cut_off = call.get("finish_reason") == "length"
        totals["replies_hit_token_limit"] += int(cut_off)
        entry = {"step": step, "model_call": call}
        trace.append(entry)
        log.info("step %d/%d | %s", step, max_steps, _model_line(call))

        try:
            msg = _parse(raw)
            action = msg["action"]
        except Exception as e:
            parse_failures += 1
            snippet = " ".join(raw.split())[:160] or "(empty response)"
            hint = (" Your reply was cut off at the length limit: think less, answer sooner."
                    if cut_off else "")
            pad.notes.append(f"step {step}: unparseable response ({e}); raw started: "
                             f"{snippet!r}; reply with ONE JSON object.{hint}")
            entry.update(outcome="unparseable", detail=f"{e}; raw started: {snippet}")
            log.warning("   -> UNPARSEABLE (%s)%s raw=%r", e,
                        " because the reply hit the token limit" if cut_off else "", snippet[:80])
            if parse_failures > MAX_PARSE_FAILURES:
                return done(_escalate(pad, trace, "model output unparseable"))
            continue

        # "rationale" since Oct 28 (Claude's classifier refused a per-step "thought"
        # as reasoning extraction); older runs and the mock still send "thought"
        entry["thought"] = str(msg.get("rationale") or msg.get("thought") or "")
        entry["action"] = action
        if isinstance(msg.get("candidates"), list):
            pad.candidates = [str(c) for c in msg["candidates"]][:4]
        log.info("   thought: %s", " ".join(entry["thought"].split())[:160])

        if "finish" in action:
            brief, problem = _gate(pad, action["finish"])
            if problem:
                pad.notes.append(f"step {step}: finish rejected - {problem}")
                entry.update(outcome="finish_rejected", detail=problem)
                log.info("   -> FINISH REJECTED by citation gate: %s", problem)
                continue
            entry["outcome"] = "diagnosed"
            rc = brief["root_cause"]
            log.info("   -> DIAGNOSED %s / %s, citing %s", rc["service"], rc["fault_type"],
                     ", ".join(e["eid"] for e in brief["supporting_evidence"]))
            brief.update(status="diagnosed", steps=step, investigation=trace,
                         evidence_board=[vars(e) for e in pad.evidence])
            return done(brief)

        name = action.get("tool")
        args = action.get("args") or {}
        if name not in toolbox.names:
            pad.notes.append(f"step {step}: unknown tool {name!r}")
            entry.update(outcome="unknown_tool", detail=str(name))
            log.info("   -> UNKNOWN TOOL %r", name)
            continue
        if not isinstance(args, dict):
            pad.notes.append(f"step {step}: bad args for {name}: args must be a JSON object")
            entry.update(outcome="bad_args", detail=f"args was {type(args).__name__}")
            log.info("   -> %s BAD ARGS: not an object (%r)", name, args)
            continue
        key = (name, json.dumps(args, sort_keys=True))
        if key in seen_calls:
            pad.notes.append(f"step {step}: {name}({args}) was already called; its result is "
                             f"{seen_calls[key]}. Choose a different query.")
            entry.update(outcome="repeated_call", detail=f"same as {seen_calls[key]}")
            log.info("   -> REFUSED repeat of %s(%s), already answered by %s",
                     name, _short(args), seen_calls[key])
            continue
        t_tool = time.monotonic()
        try:
            result = toolbox.call(name, args)
        except (TypeError, ValueError) as e:
            pad.notes.append(f"step {step}: bad args for {name}: {e}")
            entry.update(outcome="bad_args", detail=str(e))
            log.info("   -> %s(%s) BAD ARGS: %s", name, _short(args), str(e)[:140])
            continue
        tool_s = round(time.monotonic() - t_tool, 2)
        totals["tool_seconds"] += tool_s
        kind, summary, ref, implicated = _summarize(name, result, args)
        ev = pad.add(kind, summary, ref, implicated)
        seen_calls[key] = ev.eid
        entry.update(outcome="tool_call", observed=ev.eid, tool_seconds=tool_s)
        log.info("   -> %s(%s) -> %s [%s] %s (tool %.1fs)", name, _short(args), ev.eid,
                 kind, summary[:110], tool_s)

    return done(_escalate(pad, trace, f"step budget ({max_steps}) exhausted"))


def _gate(pad: Scratchpad, finish: dict) -> tuple[dict, str | None]:
    """The citation gate, in code (2.1's 'overconfidence' check).

    Passes only if every cited id exists AND at least two independent telemetry
    kinds among the citations actually implicate the claimed service. "Implicates"
    is computed by code when the evidence is created (a significant metric
    deviation, the service on a failing trace path, matching log lines), so citing
    unrelated evidence does not count.
    """
    cited = finish.get("supporting_evidence") or []
    missing = [i for i in cited if pad.get(i) is None]
    if missing:
        return {}, f"citations {missing} do not exist on the evidence board"
    service = finish.get("root_cause_service")
    supporting, problems = set(), []
    for i in cited:
        e = pad.get(i)
        if e.kind not in TELEMETRY_KINDS:
            problems.append(f"{i} is {e.kind} (reference, not evidence)")
        elif _norm(service) in {_norm(s) for s in e.implicates}:
            supporting.add(e.kind)
        else:
            problems.append(f"{i} [{e.kind}] does not implicate {service}")
    if len(supporting) < 2:
        lead = (f"only {sorted(supporting)} implicates {service!r}" if supporting
                else f"no cited telemetry implicates {service!r}")
        return {}, (lead
                    + (f" ({'; '.join(problems)})" if problems else "")
                    + "; a root cause needs two independent kinds that each point at the claimed service")
    fix = dict(finish.get("proposed_fix") or {})
    # v1 policy: EVERY proposed fix waits for a human. The model's own destructive
    # label is kept for the record but cannot switch approval off.
    fix["model_labeled_destructive"] = bool(fix.pop("destructive", True))
    fix["requires_approval"] = True
    brief = {
        "root_cause": {"service": service, "fault_type": finish.get("fault_type")},
        "confidence": "high" if not pad.notes else "medium",
        "supporting_evidence": [vars(pad.get(i)) for i in cited],
        "telemetry_kinds": sorted(supporting),
        "alternatives_rejected": finish.get("alternatives_rejected", []),
        "proposed_fix": fix,
    }
    return brief, None


def _escalate(pad: Scratchpad, trace: list, reason: str) -> dict:
    """2.1: 'hand the findings to the human instead of guessing'."""
    return {"status": "escalated", "reason": reason,
            "candidates": pad.candidates, "notes": pad.notes,
            "evidence_board": [vars(e) for e in pad.evidence],
            "investigation": trace, "proposed_fix": None}
