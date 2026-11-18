"""The ReAct loop (Checkpoint 2.1): orient -> reason -> act -> observe -> stop/escalate.

Design rules, enforced in CODE, not left to the model:
- finish is REJECTED unless the cited evidence spans >= 2 independent telemetry
  kinds (metrics/logs/traces) that each implicate the claimed service, every
  citation id exists and the fault type is a known one (the citation gate).
- every proposed fix carries requires_approval=True, whatever the model labels it.
- the Commander's tools are an allowlist (roles.COMMANDER): no runbooks on any path.
- when the step budget runs out, the agent escalates with findings instead of
  guessing (status: "escalated").
- the model sees only the scratchpad rendering; raw telemetry never enters context.
- tools are reached only through the MCP server (mcp_server.py); the tool list in
  the prompt is rendered from the server's schemas, so the two cannot drift.

With tot= set (Checkpoint 4.1), Tree-of-Thought takes over the reason/stop steps
only when the first pass is ambiguous (tot.py):
- T1: a finish passes the gate, but the critic does not rank its fault type
  clearly first for that service (another type within MARGIN, or contradicted);
- T2: after TOT_AFTER_STEPS steps no service has two telemetry kinds implicating it.
Cases that are already clear (most delay faults) pay one extra tool call, no model call.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone

from .loaders import IncidentCase
from .mcp_server import BRANCH_TOOLS, ToolClient, build_server
from .roles import COMMANDER
from .scratchpad import TELEMETRY_KINDS, Scratchpad

MAX_STEPS = 8
Z_SIGNIFICANT = 3.0  # "3-sigma": a deviation smaller than this is treated as noise
TRACE_RATIO_SIGNIFICANT = 2.0  # per-service p50 self time; calibrated on RE2-OB rep 1
ERR_RISE_SIGNIFICANT = 0.05    # error-rate rise (absolute) on a service's spans
log = logging.getLogger("incident_commander")
MAX_PARSE_FAILURES = 2
TOT_AFTER_STEPS = 3
BAD_LOG_LEVELS = {"warn", "warning", "error", "err", "fatal", "critical"}
FIX_KEYS = ("description",)  # what the model may put in a fix; approval fields are set in code

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
        # an empty or null filter is no filter: never print it as a scope
        scope = ", ".join(f"{k}={v}" for k, v in args.items()
                          if k in ("service", "metric") and v) or "all services"
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
        if not n:
            return "logs", _no_log_lines(result, args), ref, []
        # Ordinary info lines are a mention, not a symptom: only warning/error lines
        # implicate a service. A query does not change that: "request" matches every
        # line an idle service writes, so a matched query alone implicates nobody.
        svcs = sorted({s for k, c in by.items() if c > 0
                       for s, _, lvl in [k.rpartition("/")] if lvl.lower() in BAD_LOG_LEVELS})
        return "logs", f"{n} matching lines; by svc/level {by}; {lines or 'no sample'}", ref, svcs
    if name == "trace_lookup" and "service" in result:
        svc = result["service"]
        moved = lambda s: bool(s and s["n_after"]) and (
            (s["p50_ratio"] or 0) >= TRACE_RATIO_SIGNIFICANT
            or (s["err_rate_after"] or 0) - (s["err_rate_before"] or 0) >= ERR_RISE_SIGNIFICANT)
        slow_to = lambda callees: [c["callee"] for c in callees
                                   if (c["p50_ratio"] or 0) >= TRACE_RATIO_SIGNIFICANT]
        parts, implicated = [], []
        own, inbound = result["overall"], result.get("inbound")
        for label, side in (("own spans", own), ("inbound calls", inbound)):
            if side and side["n_after"]:
                parts.append(f"{label} self-time p50 {side['self_p50_ms_before']}->{side['self_p50_ms_after']}ms"
                             f" ({side['p50_ratio']}x), errors {side['err_rate_before']}->{side['err_rate_after']}")
        if moved(own):
            implicated = [svc]
        if moved(inbound):
            # a slow caller that is also slow to >=2 other callees: the delay is on its side
            slow = [c for c in result.get("inbound_by_caller", []) if moved(c)]
            upstream = {c["caller"]: slow_to(c["caller_other_callees"]) for c in slow}
            upstream = {k: v for k, v in upstream.items() if len(v) >= 2}
            if slow and len(upstream) == len(slow):
                parts += [f"BUT the inbound slowdown is only on calls from {k}, which is also slow to "
                          f"{', '.join(v)}: likely {k}'s own network, not {svc}" for k, v in upstream.items()]
                implicated += [k for k in upstream if k not in implicated]
            elif svc not in implicated:
                implicated.append(svc)
        out_slow = slow_to(result.get("outbound", []))
        if len(out_slow) >= 2:
            parts.append(f"outbound calls slowed to {len(out_slow)} callees ({', '.join(out_slow)}): "
                         f"delay on {svc}'s own network")
            if svc not in implicated:
                implicated.append(svc)
        verdict = ("SIGNIFICANT change" if implicated else
                   f"NO significant change (<{TRACE_RATIO_SIGNIFICANT:g}x latency, "
                   f"<{ERR_RISE_SIGNIFICANT:g} error rise)")
        return ("traces", f"traces for {svc}: {verdict}; " + "; ".join(parts or ["no spans after alert"]),
                result["source_ref"], implicated)
    if name == "trace_lookup":
        ts = result["traces"]
        if not ts:
            return "traces", result.get("note", "no traces"), "traces.csv", []
        shown = ts[:3]
        lines = "; ".join(f"{t['path']} {t['duration_ms']}ms errors={len(t['error_spans'])}" for t in shown)
        if args.get("failing_only") is not False and not result.get("failing_only", True):
            # asked for failing traces, none errored: the tool fell back to the slowest
            return ("traces", f"no failing traces among {result['n_traces_after_alert']} after the alert; "
                    f"showing the slowest instead (being on a slow path implicates nobody): {lines}",
                    ts[0]["source_ref"], [])
        # A path-level lookup is navigation: it implicates nobody. Being ON a bad path
        # is a mention, and the error span belongs to the CALLER that recorded the
        # failed call (frontendservice in 30 of 30 rep-1 cases, the faulty service in
        # none). Only the per-service lookup above is a calibrated trace signal.
        return ("traces", f"{result['n_traces_after_alert']} traces after alert; worst: {lines}",
                ts[0]["source_ref"], [])
    if name == "service_map":
        if not result["edges"]:
            return ("map", "no call edges found in the traces (the map is empty; this does not "
                    "mean the services have no dependencies)", result["source_ref"], [])
        edges = "; ".join(f"{k}->{','.join(v)}" for k, v in result["edges"].items())
        return "map", f"dependencies: {edges}", result["source_ref"], []
    if name == "runbook_lookup":
        if not result:
            return "runbook", "no runbook match (abstained)", "runbook index", []
        h = result[0]
        return ("runbook", f"{len(result)} hits; top {h['alert']} ({h['kind']}, d={h['distance']}): "
                f"{h['text'][:120]}", h["source_ref"], [])
    return "map", str(result)[:200], name, []


def _no_log_lines(result: dict, args: dict) -> str:
    """Wording for a log search with zero lines. A query that missed is a filter
    that matched nothing; a valid level with no lines is a checked absence. Neither
    says a service is healthy, and neither implicates anyone."""
    filt = ", ".join(f"{k}={args[k]!r}" for k in ("service", "level", "query") if args.get(k))
    scope, levels = result.get("scope_lines"), result.get("levels_present")
    searched = f"{scope} lines searched, levels present {levels}" if scope is not None else "0 lines"
    if args.get("query"):
        return (f"filter matched nothing [{filt}]: no line contains that literal text ({searched}); "
                "a filter that misses is not a finding about any service")
    if args.get("level"):
        return f"checked [{filt}]: no log lines at that level ({searched}); implicates nobody"
    return f"no log lines [{filt or 'all services'}]"


def _call_key(toolbox, name: str, args: dict) -> tuple[str, str]:
    """Identity of a tool call for the repeat check. Arguments left at the schema
    default are dropped, so metric_query({}) and metric_query({"top": 10}) are one call."""
    schema = next((t.inputSchema or {} for t in getattr(toolbox, "tools", []) if t.name == name), {})
    props = schema.get("properties", {})
    unset = lambda k, v: v is None or (k in props and "default" in props[k]
                                        and type(v) is type(props[k]["default"]) and v == props[k]["default"])
    return name, json.dumps({k: v for k, v in args.items() if not unset(k, v)}, sort_keys=True)


def _norm(service) -> str:
    """'checkoutservice', 'checkout', 'Checkout' -> 'checkout'; logs say 'frontend', traces 'frontendservice'."""
    s = str(service or "").lower().strip()
    # rstrip: 'ts-order-service' and 'ts-order' are one service too (Train Ticket
    # naming); no Online Boutique name contains '-', so nothing changes there
    return s[: -len("service")].rstrip("-") if s.endswith("service") else s


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
             toolbox: ToolClient | None = None, tot: str | None = None) -> dict:
    """Run the loop on one case. Returns the incident brief (dict).

    Every step is recorded in brief["investigation"] (including failed steps) with
    the model call's time/token stats and an `outcome`, and is logged in plain
    language to the "incident_commander" logger. brief["usage"] holds run totals.
    Tools go through `toolbox`; by default a fresh MCP server for this case.
    tot: None (linear only), "model" (LLM thought generator) or "heuristic"
    (code picks each test: a zero-cost ablation).
    """
    if toolbox is not None:
        return _investigate(case, model, max_steps, toolbox, tot)
    with ToolClient(build_server(case)) as toolbox:
        return _investigate(case, model, max_steps, toolbox, tot)


def make_observer(pad: Scratchpad, toolbox: ToolClient, seen_calls: dict, observations: dict,
                  totals: dict | None = None):
    """observe(name, args, why) -> eid | None: run one tool for ToT through the
    loop's own dedup/summarize/record path (a repeated call returns its old eid).
    observe.last["tool_seconds"] is the time the last call spent in the tool (None
    when it was answered from the board or failed), for the caller's trace entry."""
    last: dict = {"tool_seconds": None}

    def observe(name: str, args: dict, why: str) -> str | None:
        last["tool_seconds"] = None
        key = _call_key(toolbox, name, args)
        if key in seen_calls:
            return seen_calls[key]
        t_tool = time.monotonic()
        try:
            result = toolbox.call(name, args)
        except (TypeError, ValueError) as e:
            pad.notes.append(f"{why}: {name}({_short(args)}) failed: {e}")
            log.info("   -> %s(%s) FAILED (%s): %s", name, _short(args), why, str(e)[:140])
            return None
        took = time.monotonic() - t_tool
        last["tool_seconds"] = round(took, 2)
        if totals is not None:
            totals["tool_seconds"] += took
        kind, summary, ref, implicated = _summarize(name, result, args)
        ev = pad.add(kind, summary, ref, implicated)
        seen_calls[key], observations[ev.eid] = ev.eid, (name, args, result)
        log.info("   -> %s(%s) -> %s [%s] %s (%s, tool %.1fs)", name, _short(args), ev.eid, kind,
                 summary[:100], why, took)
        return ev.eid
    observe.last = last
    return observe


class Run:
    """One investigation's shared state plus the Commander's step primitives.

    The linear loop below (`_investigate`) and the LangGraph nodes (graph.py,
    Checkpoint 5.1) are both built from these methods, so the two architectures
    cannot drift: a step means the same thing, is logged the same way and lands
    in the same trace entry in both. The linear loop is the reference behaviour.
    """

    def __init__(self, case: IncidentCase, model, toolbox: ToolClient,
                 max_steps: int = MAX_STEPS, tot: str | None = None):
        alert_utc = datetime.fromtimestamp(case.inject_time, tz=timezone.utc)
        self.case, self.model, self.toolbox = case, model, toolbox
        self.max_steps, self.tot = max_steps, tot
        self.hidden = getattr(toolbox, "hidden", frozenset())  # telemetry kinds withheld (--hide)
        self.pad = Scratchpad(alert=f"{case.system}: latency/availability alert at "
                                    f"{alert_utc:%Y-%m-%d %H:%M}Z. Find root-cause service and fault type.")
        self.system = SYSTEM_PROMPT.replace("{tools}", toolbox.describe())
        self.trace: list[dict] = []
        self.parse_failures, self.seen_calls = 0, {}
        self.failed_calls: dict = {}  # call key -> error: an identical bad call is refused, not re-run
        self.observations: dict = {}  # eid -> (tool, args, raw result): what the ToT critic reads
        self.totals = {"model_calls": 0, "model_seconds": 0.0, "tool_seconds": 0.0,
                       "prompt_tokens": 0, "completion_tokens": 0, "replies_hit_token_limit": 0}
        self.t_run = time.monotonic()
        self.step = 0
        self.entry: dict = {}  # the current step's trace entry
        self.observe = make_observer(self.pad, toolbox, self.seen_calls, self.observations, self.totals)
        log.info("START case=%s | step budget=%d", case.case_id, max_steps)

    # ------------------------------------------------------------ accounting
    def count(self, call: dict) -> bool:
        """Add one model call to the run totals; True if the reply was cut off."""
        self.totals["model_calls"] += 1
        self.totals["model_seconds"] += call.get("seconds") or 0
        self.totals["prompt_tokens"] += call.get("prompt_tokens") or 0
        self.totals["completion_tokens"] += call.get("completion_tokens") or 0
        cut_off = call.get("finish_reason") == "length"
        self.totals["replies_hit_token_limit"] += int(cut_off)
        return cut_off

    def done(self, brief: dict) -> dict:
        t = self.totals
        t["wall_seconds"] = round(time.monotonic() - self.t_run, 1)
        t["model_seconds"] = round(t["model_seconds"], 1)
        t["tool_seconds"] = round(t["tool_seconds"], 1)
        brief["usage"] = t
        log.info("END status=%s | %d model calls | %.0fs total (model %.0fs, tools %.1fs) | "
                 "tokens in %d / out %d | %d replies hit the token limit",
                 brief["status"], t["model_calls"], t["wall_seconds"], t["model_seconds"],
                 t["tool_seconds"], t["prompt_tokens"], t["completion_tokens"],
                 t["replies_hit_token_limit"])
        return brief

    # ------------------------------------------------------------ the Commander's turn
    def think(self) -> tuple[str, object]:
        """One Commander turn: ("tool", (name, args)) | ("finish", finish) |
        ("retry", why) when the reply was unusable and the board now says so |
        ("abort", reason) after too many unparseable replies."""
        self.step += 1
        step, pad = self.step, self.pad
        raw = self.model.complete([{"role": "system", "content": self.system},
                                   {"role": "user", "content": pad.render()}])
        call = dict(getattr(self.model, "last_call", {}) or {})
        cut_off = self.count(call)
        self.entry = entry = {"step": step, "model_call": call}
        self.trace.append(entry)
        log.info("step %d/%d | %s", step, self.max_steps, _model_line(call))

        try:
            msg = _parse(raw)
            action = msg["action"]
            # a string/list where an object belongs is as unusable as broken JSON
            if not isinstance(action, dict):
                raise ValueError("'action' must be a JSON object")
            if "finish" in action and not isinstance(action["finish"], dict):
                raise ValueError("'finish' must be a JSON object")
        except Exception as e:
            self.parse_failures += 1
            snippet = " ".join(raw.split())[:160] or "(empty response)"
            hint = (" Your reply was cut off at the length limit: think less, answer sooner."
                    if cut_off else "")
            pad.notes.append(f"step {step}: unparseable response ({e}); raw started: "
                             f"{snippet!r}; reply with ONE JSON object.{hint}")
            entry.update(outcome="unparseable", detail=f"{e}; raw started: {snippet}")
            log.warning("   -> UNPARSEABLE (%s)%s raw=%r", e,
                        " because the reply hit the token limit" if cut_off else "", snippet[:80])
            if self.parse_failures > MAX_PARSE_FAILURES:
                return "abort", "model output unparseable"
            return "retry", "unparseable"

        # "rationale" since Oct 28 (Claude's classifier refused a per-step "thought"
        # as reasoning extraction); older runs and the mock still send "thought"
        entry["thought"] = str(msg.get("rationale") or msg.get("thought") or "")
        entry["action"] = action
        if isinstance(msg.get("candidates"), list):
            pad.candidates = [str(c) for c in msg["candidates"]][:4]
        log.info("   thought: %s", " ".join(entry["thought"].split())[:160])

        if "finish" in action:
            return "finish", action["finish"]
        name = action.get("tool")
        args = action.get("args") or {}
        if name not in self.toolbox.diagnostic:
            pad.notes.append(f"step {step}: unknown tool {name!r}; tools are {self.toolbox.diagnostic}")
            entry.update(outcome="unknown_tool", detail=str(name))
            log.info("   -> UNKNOWN TOOL %r", name)
            return "retry", "unknown_tool"
        if not isinstance(args, dict):
            pad.notes.append(f"step {step}: bad args for {name}: args must be a JSON object")
            entry.update(outcome="bad_args", detail=f"args was {type(args).__name__}")
            log.info("   -> %s BAD ARGS: not an object (%r)", name, args)
            return "retry", "bad_args"
        key = _call_key(self.toolbox, name, args)
        if key in self.failed_calls:
            pad.notes.append(f"step {step}: {name}({args}) was already called and failed "
                             f"({self.failed_calls[key][:120]}). Choose a different query.")
            entry.update(outcome="repeated_call", detail="same as an earlier failed call")
            log.info("   -> REFUSED repeat of failed %s(%s)", name, _short(args))
            return "retry", "repeated_call"
        if key in self.seen_calls:
            pad.notes.append(f"step {step}: {name}({args}) was already called; its result is "
                             f"{self.seen_calls[key]}. Choose a different query.")
            entry.update(outcome="repeated_call", detail=f"same as {self.seen_calls[key]}")
            log.info("   -> REFUSED repeat of %s(%s), already answered by %s",
                     name, _short(args), self.seen_calls[key])
            return "retry", "repeated_call"
        return "tool", (name, args)

    def act(self, name: str, args: dict) -> str | None:
        """Run the tool the Commander chose; the new evidence id, or None on bad args."""
        step, entry = self.step, self.entry
        t_tool = time.monotonic()
        try:
            result = self.toolbox.call(name, args)
        except (TypeError, ValueError) as e:
            self.pad.notes.append(f"step {step}: bad args for {name}: {e}")
            self.failed_calls[_call_key(self.toolbox, name, args)] = str(e)
            entry.update(outcome="bad_args", detail=str(e))
            log.info("   -> %s(%s) BAD ARGS: %s", name, _short(args), str(e)[:140])
            return None
        tool_s = round(time.monotonic() - t_tool, 2)
        self.totals["tool_seconds"] += tool_s
        kind, summary, ref, implicated = _summarize(name, result, args)
        ev = self.pad.add(kind, summary, ref, implicated)
        self.seen_calls[_call_key(self.toolbox, name, args)] = ev.eid
        self.observations[ev.eid] = (name, args, result)
        entry.update(outcome="tool_call", observed=ev.eid, tool_seconds=tool_s)
        log.info("   -> %s(%s) -> %s [%s] %s (tool %.1fs)", name, _short(args), ev.eid,
                 kind, summary[:110], tool_s)
        return ev.eid

    # ------------------------------------------------------------ validation points
    def gate(self, finish: dict) -> tuple[dict, str | None]:
        """The citation gate on a finish proposal: (brief, None) when accepted, else
        ({}, problem) with the reason posted to the board for the Commander."""
        brief, problem = _gate(self.pad, finish)
        if problem:
            self.pad.notes.append(f"step {self.step}: finish rejected - {problem}")
            self.entry.update(outcome="finish_rejected", detail=problem)
            log.info("   -> FINISH REJECTED by citation gate: %s", problem)
        return brief, problem

    def ambiguity(self, finish: dict, brief: dict) -> tuple[str, str, str | None] | None:
        """T1: (trigger, suspect, rival) when a gated finish is ambiguous, else None."""
        ambiguous = _fault_ambiguity(self.pad, self.observe, self.observations, finish, self.hidden)
        if not ambiguous:
            return None
        self.entry.update(outcome="finish_ambiguous", detail=ambiguous)
        log.info("   -> FINISH passed the gate but is AMBIGUOUS: %s", ambiguous)
        service = brief["root_cause"]["service"]
        rivals = [s for s in _candidate_services(self.pad) if _norm(s) != _norm(service)]
        return f"T1 {ambiguous}", service, rivals[0] if rivals else None

    def two_kinds_unmet(self) -> tuple[str, str, str | None] | None:
        """T2: (trigger, suspect, rival) when, after TOT_AFTER_STEPS steps, no
        service has two telemetry kinds implicating it, else None."""
        if self.step < TOT_AFTER_STEPS or _any_two_kinds(self.pad):
            return None
        suspects = _candidate_services(self.pad)
        if not suspects:
            return None
        return (f"T2 two-kinds rule unmet after {self.step} steps", suspects[0],
                suspects[1] if len(suspects) > 1 else None)

    # ------------------------------------------------------------ outcomes
    def diagnosed(self, brief: dict, tot: dict | None = None) -> dict:
        """Finalize an accepted brief (status diagnosed)."""
        if tot is None:
            self.entry["outcome"] = "diagnosed"
        rc = brief["root_cause"]
        log.info("   -> DIAGNOSED %s / %s, citing %s", rc["service"], rc["fault_type"],
                 ", ".join(e["eid"] for e in brief["supporting_evidence"]))
        brief.update(status="diagnosed", steps=self.step, investigation=self.trace,
                     evidence_board=[vars(e) for e in self.pad.evidence])
        if tot is not None:
            brief.update(decided_by="tot", steps=len(self.trace), tot=tot)
        return brief

    def escalate(self, reason: str, escalation: dict | None = None) -> dict:
        return _escalate(self.pad, self.trace, reason, escalation, self.observations)

    # ------------------------------------------------------------ Tree-of-Thought
    def tot_context(self, toolbox: ToolClient | None = None):
        from . import tot as tot_mod
        return tot_mod.TotContext(self.pad, toolbox or self.toolbox, self.observe, self.observations,
                                  lambda n, a: _call_key(self.toolbox, n, a) in self.seen_calls)

    def generator(self):
        from . import tot as tot_mod
        return tot_mod.ModelGenerator(self.model) if self.tot == "model" else tot_mod.HeuristicGenerator()

    def count_generator(self, gen) -> None:
        for call in getattr(gen, "last_calls", []):
            self.count(call)

    def tot_brief(self, trigger: str, suspect: str, rival: str | None) -> dict:
        """Linear path: run the whole ToT controller and turn its result into a brief."""
        from . import tot as tot_mod
        gen, ctx = self.generator(), self.tot_context()
        result = tot_mod.run_tot(ctx, suspect, rival, gen, trigger, self.trace)
        self.count_generator(gen)
        return _tot_brief(self, tot_mod.with_flags(result), ctx)


def _investigate(case: IncidentCase, model, max_steps: int, toolbox: ToolClient,
                 tot: str | None = None) -> dict:
    """The linear ReAct loop, written over Run's primitives."""
    # the Commander's allowlist (no runbooks), as on the graph; the ToT controller
    # this Run may start also needs the branch board
    run = Run(case, model, toolbox.scoped(COMMANDER.tools + BRANCH_TOOLS), max_steps, tot)
    while run.step < run.max_steps:
        kind, payload = run.think()
        if kind == "abort":
            return run.done(run.escalate(payload))
        if kind == "retry":
            continue
        if kind == "finish":
            brief, problem = run.gate(payload)
            if problem:
                continue
            if tot and (req := run.ambiguity(payload, brief)):
                return run.done(run.tot_brief(*req))
            return run.done(run.diagnosed(brief))
        name, args = payload
        if run.act(name, args) and tot and (req := run.two_kinds_unmet()):
            return run.done(run.tot_brief(*req))
    return run.done(run.escalate(f"step budget ({max_steps}) exhausted"))


def _gate(pad: Scratchpad, finish: dict) -> tuple[dict, str | None]:
    """The citation gate, in code (2.1's 'overconfidence' check; tightened for 6.1).

    Passes only if every cited id exists AND at least two independent telemetry
    kinds among the citations actually implicate the claimed service. "Implicates"
    is computed by code when the evidence is created (_summarize): a significant
    metric deviation, a significant change in the service's own or inbound spans
    (the per-service trace lookup), or warning/error log lines. Being on a failing
    or slow path, or having ordinary log lines (matched by a query or not), is a
    mention and does not count, so citing unrelated evidence gets nowhere.
    Also refused: a fault type outside the known six, and a service whose own
    trace lookup, cited or not, put the slowdown on its caller (_shared_cause).
    Evidence ids in alternatives_rejected that are not on the board do not refuse
    the finish; they are listed in the brief as untraceable_citations.
    """
    from .signatures import FAULT_TYPES  # not at top: signatures imports this module
    if not isinstance(finish, dict):
        return {}, "finish must be a JSON object"
    cited = finish.get("supporting_evidence") or []
    if not isinstance(cited, list):
        return {}, "supporting_evidence must be a list of evidence ids, e.g. [\"E1\", \"E2\"]"
    missing = [i for i in cited if not isinstance(i, str) or pad.get(i) is None]
    if missing:
        return {}, f"citations {missing} do not exist on the evidence board"
    service = finish.get("root_cause_service")
    if service is not None and not isinstance(service, str):
        return {}, "root_cause_service must be one service name"
    fault = str(finish.get("fault_type") or "").strip().lower()
    if fault not in FAULT_TYPES:
        return {}, (f"fault_type {finish.get('fault_type')!r} is not a known fault type; "
                    f"choose one of {list(FAULT_TYPES)}")
    if shared := _shared_cause(pad, service):
        return {}, shared
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
    alternatives, unknown = _alternatives(pad, finish.get("alternatives_rejected", []))
    brief = {
        "root_cause": {"service": service, "fault_type": fault},
        "confidence": "high" if not pad.notes else "medium",
        "supporting_evidence": [vars(pad.get(i)) for i in cited],
        "telemetry_kinds": sorted(supporting),
        "alternatives_rejected": alternatives,
        "proposed_fix": _fix(finish.get("proposed_fix")),
    }
    if unknown:  # only present when there is something to say: ordinary briefs keep their shape
        brief["untraceable_citations"] = unknown
    return brief, None


def _fix(given) -> dict:
    """The fix as the brief carries it. v1 policy: EVERY proposed fix waits for a
    human. The model's own destructive label is kept for the record but cannot
    switch approval off, and no other key the model adds (an "approval" of its
    own, say) survives. What was removed is listed in dropped_keys, so a fix
    written under another key is not lost without a trace; the key is absent when
    nothing was removed. A bare string is taken as the description."""
    dropped = []
    if isinstance(given, str):
        given = {"description": given}
    elif not isinstance(given, dict):
        dropped = [] if given is None else [f"(whole fix: {type(given).__name__}, not an object)"]
        given = {}
    # a null description is no description: str(None) would print "None" as the fix
    fix = {k: given[k] if isinstance(given[k], str) else str(given[k])
           for k in FIX_KEYS if given.get(k) is not None}
    fix["model_labeled_destructive"] = bool(given.get("destructive", True))
    fix["requires_approval"] = True
    dropped += sorted(str(k) for k in given if k not in (*FIX_KEYS, "destructive"))
    if dropped:
        fix["dropped_keys"] = dropped
    return fix


def _alternatives(pad: Scratchpad, given) -> tuple[list, list[str]]:
    """(rejected alternatives as a list, evidence ids they cite that are not on
    the board). The text is the model's reasoning and is kept as written; an id
    that does not exist is recorded next to it, not deleted, so the reader can
    see which part of the reasoning traces to nothing."""
    alternatives = given if isinstance(given, list) else [] if given is None else [given]
    cited = set(re.findall(r"\bE\d+\b", json.dumps(alternatives, default=str)))
    return alternatives, sorted((i for i in cited if pad.get(i) is None), key=_eid_order)


def _shared_cause(pad: Scratchpad, service) -> str | None:
    """The caller-side pattern as a gate rule. If the claimed service's own trace
    lookup found the slowdown only on calls from a caller that is slow to its other
    callees too, then this service's latency metrics and its place on slow paths
    are the same fault seen twice, not two independent sources. The WHOLE board is
    scanned: leaving that entry uncited must not make it go away."""
    for e in pad.evidence:
        tag, _, name = e.source_ref.partition("serviceName=")
        if (e.kind == "traces" and tag == "traces.csv:" and _norm(name) == _norm(service)
                and e.implicates and _norm(service) not in {_norm(s) for s in e.implicates}):
            callers = ", ".join(e.implicates)
            return (f"{e.eid} puts {service}'s slowdown on its caller {callers} (shared cause): other "
                    f"evidence that {service} is slow follows from the same fault and is not "
                    f"independent; investigate {callers}")
    return None


def _escalate(pad: Scratchpad, trace: list, reason: str, escalation: dict | None = None,
              observations: dict | None = None) -> dict:
    """2.1: 'hand the findings to the human instead of guessing'. Every escalation
    carries a structured block (6.1): {code, reason, hypotheses, note}. `reason`
    and `candidates` stay as they were for readers of older briefs."""
    return {"status": "escalated", "reason": reason,
            "escalation": escalation or _escalation(pad, trace, reason, observations or {}),
            "candidates": pad.candidates, "notes": pad.notes,
            "evidence_board": [vars(e) for e in pad.evidence],
            "investigation": trace, "proposed_fix": None}


def _escalation(pad: Scratchpad, trace: list, reason: str, observations: dict) -> dict:
    """The block for an escalation that did not come out of ToT (step budget, or
    unparseable replies). The hypotheses are ranked in code from the board."""
    if reason.startswith("model output unparseable"):
        code, note = "unparseable", "the model's reply could not be read three times"
    elif not pad.evidence:
        # nothing was ever gathered, so the budget did not run out on investigation:
        # the replies themselves were the problem. Not a plain budget escalation.
        seen = sorted({e.get("outcome", "?") for e in trace})
        code, note = "unparseable", (f"no usable evidence gathered: the step budget ran out with no "
                                     f"successful tool call (step outcomes: {', '.join(seen) or 'none'}), "
                                     "so this is coded as unusable model output, not as a budget problem")
    else:
        code, note = "budget_exhausted", "no diagnosis passed the citation gate within the step budget"
    hyps = _ranked_hypotheses(pad, observations)
    if not pad.evidence and "no usable evidence gathered" not in note:
        note += "; no usable evidence gathered"
    elif pad.evidence and not hyps:
        note += "; the evidence gathered implicates no service"
    elif hyps:
        note += ("; hypotheses are ranked in code from the evidence already on the board (no new "
                 "tool calls): a fault type is listed only where one of its signals is confirmed")
    return {"code": code, "reason": reason, "hypotheses": hyps, "note": note}


MAX_HYPOTHESES = 6


def _verdict_summary(verdicts: list[dict]) -> str:
    """'confirmed cpu_up (cpu z=12); contradicted ...' - untested signals are left out."""
    by: dict[str, list[str]] = {}
    for v in verdicts:
        if v["verdict"] != "untested":
            by.setdefault(v["verdict"], []).append(
                v["signal"] + (f" ({v['observed']})" if v["observed"] else ""))
    return "; ".join(f"{k} {', '.join(v)}" for k, v in by.items()) or "no signal measured yet"


def _ranked_hypotheses(pad: Scratchpad, observations: dict) -> list[dict]:
    """Ranked (service, fault type) hypotheses for a non-ToT handover, built from
    the board alone: the services the model named or the evidence implicates, each
    with the fault types the critic has not ruled out. [] when the board is empty."""
    from dataclasses import asdict
    from . import signatures as sig  # not at top: signatures imports this module
    if not pad.evidence:
        return []
    implicates = {e.eid: e.implicates for e in pad.evidence}
    kinds = _implicated_kinds(pad)
    names = {_norm(s): s for e in pad.evidence for s in e.implicates}
    services, out = [], []
    for s in _candidate_services(pad) + [names[k] for k in sorted(kinds, key=lambda k: -len(kinds[k]))]:
        if _norm(s) not in {_norm(x) for x in services}:
            services.append(s)
    for order, svc in enumerate(services):
        pointing = [e.eid for e in pad.evidence if e.kind in TELEMETRY_KINDS
                    and _norm(svc) in {_norm(x) for x in e.implicates}]
        base = {"service": svc, "kinds": sorted(kinds.get(_norm(svc), ())), "order": order}
        rows = [(f, sc, [asdict(v) for v in vs])
                for f, sc, vs in sig.rank_faults(sig.features(svc, observations, implicates))
                if not sig.must_contradicted(vs) and any(v.verdict == sig.CONFIRMED for v in vs)]
        if not rows:
            out.append({**base, "fault_type": None, "score": None, "evidence": pointing,
                        "summary": ("implicated by the evidence cited" if pointing else
                                    "named by the model; nothing on the board implicates it")
                                   + "; no fault type has a confirmed signal"})
        for fault, sc, vs in rows[:2]:
            eids = sorted({v["eid"] for v in vs if v["eid"]} | set(pointing), key=_eid_order)
            out.append({**base, "fault_type": fault, "score": sc, "evidence": eids,
                        "summary": _verdict_summary(vs)})
    # best score first; a service with no fault type tested goes last, in the model's order
    out.sort(key=lambda h: (h["score"] is None, -(h["score"] or 0), -len(h["kinds"]), h["order"]))
    return [{k: v for k, v in h.items() if k != "order"} for h in out[:MAX_HYPOTHESES]]


def _eid_order(eid: str) -> tuple:
    return (int(eid[1:]) if eid[1:].isdigit() else 0, eid)


def _implicated_kinds(pad: Scratchpad) -> dict[str, set[str]]:
    """normalized service -> telemetry kinds on the board that implicate it."""
    out: dict[str, set[str]] = {}
    for e in pad.evidence:
        if e.kind in TELEMETRY_KINDS:
            for s in e.implicates:
                out.setdefault(_norm(s), set()).add(e.kind)
    return out


def _any_two_kinds(pad: Scratchpad) -> bool:
    return any(len(k) >= 2 for k in _implicated_kinds(pad).values())


def _candidate_services(pad: Scratchpad) -> list[str]:
    """Services in the model's own ranking ("svc: hypothesis"), in order; if it gave
    none, services by how many telemetry kinds implicate them. In the 90-case run
    the true service was the model's first candidate in all 28 escalations, while
    the raw top metric deviation was a caller or the frontend in ~1 case in 5."""
    out = []
    for c in pad.candidates:
        m = re.match(r"\s*([A-Za-z][\w-]*)", c)
        if m and _norm(m.group(1)) not in {_norm(s) for s in out}:
            out.append(m.group(1))
    if not out:
        kinds = _implicated_kinds(pad)
        names = {_norm(s): s for e in pad.evidence for s in e.implicates}
        out = [names[k] for k in sorted(kinds, key=lambda k: -len(kinds[k]))]
    return out


def _fault_ambiguity(pad: Scratchpad, observe, observations: dict, finish: dict,
                     hidden: frozenset = frozenset()) -> str | None:
    """T1: does the critic rank the claimed fault type clearly first for that service?
    Costs one tool call (the service's own metric signature), no model call.
    Returns None when clear, else a one-line reason."""
    from . import signatures as sig
    from .tot import MARGIN
    service, fault = finish.get("root_cause_service"), str(finish.get("fault_type", "")).lower()
    if "metrics" in hidden:
        # --hide metrics: metric_query is not there, so the check cannot run. That
        # must not read as "clear" (the service-has-no-metrics case below): every
        # fault type's must-have signal is a metric, so the claim is unchecked.
        return f"{fault} for {service} cannot be checked: metrics are hidden in this run"
    if observe("metric_query", {"service": service}, "tot check: metric signature") is None:
        return None  # not a metric service: nothing to check against, keep the linear answer
    implicates = {e.eid: e.implicates for e in pad.evidence}
    ranked = sig.rank_faults(sig.features(service, observations, implicates, hidden))
    alive = [(f, s) for f, s, vs in ranked if not sig.must_contradicted(vs)]
    scores = dict(alive)
    if fault not in scores:
        return f"{fault} has a contradicted must-have signal for {service}"
    rivals = [(f, s) for f, s in alive if f != fault and s > scores[fault] - MARGIN]
    if rivals:
        return (f"{fault} (score {scores[fault]}) vs " +
                ", ".join(f"{f} ({s})" for f, s in rivals) + f" for {service}")
    return None


def tot_finish(result: dict, ctx) -> dict | None:
    """The finish proposal for ToT's selected branch (None if nothing was selected).
    It still goes through the citation gate like any Commander finish."""
    from . import signatures as sig
    from . import tot as tot_mod
    sel = result["selected"]
    if not sel:
        return None
    cited = [eid for eids in ctx.implicating_kinds(sel["service"]).values() for eid in eids]
    return {"root_cause_service": sel["service"], "fault_type": sel["fault_type"],
            "supporting_evidence": cited,
            "alternatives_rejected": tot_mod.alternatives(result),
            "proposed_fix": {"description": sig.REMEDIATION[sel["fault_type"]].format(svc=sel["service"]),
                             "destructive": True}}


def tot_code(result: dict) -> str:
    """The escalation code for a ToT result that did not end in a diagnosis."""
    if result["selected"]:
        return "tot_selected_gate_refused"
    if result["status"] == "tied":
        return "tot_tie"
    return "unfamiliar" if result.get("unfamiliar") else "tot_no_branch"


def _labels(branches: list[dict]) -> str:
    return " or ".join(f"{b['service']}: {b['fault_type']}" for b in branches)


def tot_reason(result: dict, gate_problem: str | None = None) -> str:
    """Why a ToT result ends in an escalation."""
    sel = result["selected"]
    if sel:
        return f"tot selected {sel['service']}: {sel['fault_type']} but the gate refused: {gate_problem}"
    if result["status"] == "tied":
        return "tie: " + _labels(result["tied"]) + ", indistinguishable in this data"
    if result.get("unfamiliar"):
        left = _unobservable_only(result)
        return (f"unfamiliar fault: no known fault type fits {_services(result)} - every fault type "
                "whose must-have signal can be observed is contradicted"
                + (f"; {_labels(left)} is not ruled out only because its must-have signal cannot be "
                   "observed in this data, which is not support for it" if left else ""))
    level = result.get("co_leaders") or []
    # --hide metrics: say why nothing could be selected, or the fault types listed read as leads
    blind = ("; metrics are hidden in this run, so no fault type's must-have signal could be "
             "checked: the fault types listed are untested guesses" if "metrics" in result.get("hidden", ()) else "")
    if level:  # not status "tied": no leader passed the gate, but the human should see both
        return ("tot: no branch passed the two-kinds gate; leaders level: " + _labels(level)
                + f" (score {level[0]['score']}, {len(level[0]['kinds'])} telemetry kind(s) each)" + blind)
    return "tot: no branch passed the two-kinds gate" + blind


def _services(result: dict) -> str:
    """The services an unfamiliar call names: those shown to be affected (the
    ones tot.ruled_out_types kept), not every service ToT happened to look at."""
    names = list(dict.fromkeys(v["service"] for v in result.get("ruled_out") or [])
                 or dict.fromkeys(b["service"] for b in result["ranked"])) or [result["suspect"]]
    return " or ".join(names)


def _unobservable_only(result: dict) -> list[dict]:
    return [b for b in result["ranked"] if b["status"] != "pruned"]


UNFAMILIAR_TAG = "not ruled out only because its must-have signal is unobservable; not a lead"


def tot_hypotheses(result: dict) -> list[dict]:
    """ToT's live branches as the handover's ranked hypotheses: tie members first,
    then by (score, telemetry kinds) as the controller compares them. For an
    unfamiliar fault the first entry says so, and the leftover branch (disk) is
    listed after it and marked, so it is not read as the lead candidate."""
    from . import tot as tot_mod
    live = sorted(tot_mod.live_branches(result["ranked"]), key=lambda b: b["status"] != "tied")
    out = [{"service": b["service"], "fault_type": b["fault_type"], "score": b["score"],
            "status": b["status"], "kinds": b["kinds"], "summary": _verdict_summary(b["verdicts"]),
            "evidence": sorted({v["eid"] for v in b["verdicts"] if v["eid"]}
                               | {t["eid"] for t in b["tests"] if t.get("eid")}, key=_eid_order)}
           for b in live]
    if not result.get("unfamiliar"):
        return out
    for h in out:
        h["summary"] = f"{UNFAMILIAR_TAG}; {h['summary']}"
    contra = result.get("ruled_out") or []
    lead = {"service": _services(result), "fault_type": "unfamiliar", "score": None,
            "status": "unfamiliar", "kinds": [],
            "summary": "no known fault type fits; ruled out: " + "; ".join(
                f"{v['service']} {v['fault_type']} by {v['signal']} ({v['observed']})" for v in contra),
            "evidence": sorted({v["eid"] for v in contra if v["eid"]}, key=_eid_order)}
    return [lead] + out


def tot_escalation(run: "Run", result: dict, reason: str) -> dict:
    """Escalate with ToT's ranked branches as the candidates and the hypotheses."""
    from . import tot as tot_mod
    code = tot_code(result)
    note = {"tot_tie": "the tied branches passed the gate and cannot be told apart in this data",
            "tot_selected_gate_refused": "ToT's best branch failed the citation gate",
            "unfamiliar": "treat as a fault outside the six known types; needs a human diagnosis",
            "tot_no_branch": "no branch gathered two telemetry kinds that implicate its service"}[code]
    brief = run.escalate(reason, {"code": code, "reason": reason, "hypotheses": tot_hypotheses(result),
                                  "note": note + "; hypotheses are ToT's live branches, best first"})
    lines = [tot_mod.summary_line(b) for b in result["ranked"]]
    if code == "unfamiliar":  # the surviving branch must not read as the lead candidate
        lines = ([f"{_services(result)}: unfamiliar fault (no known fault type fits)"]
                 + [f"{l} [{UNFAMILIAR_TAG}]" if b["status"] != "pruned" else l
                    for l, b in zip(lines, result["ranked"])])
    brief["candidates"] = lines
    brief["tot"] = result
    return brief


def _tot_brief(run: "Run", result: dict, ctx) -> dict:
    """Linear path. The brief from a ToT result: selected -> diagnosed (re-checked
    by the gate); tied -> escalated naming the indistinguishable hypotheses; else
    escalated with the ranked branches."""
    finish = tot_finish(result, ctx)
    if finish:
        brief, problem = _gate(run.pad, finish)
        if not problem:
            return run.diagnosed(brief, tot=result)
        return tot_escalation(run, result, tot_reason(result, problem))
    return tot_escalation(run, result, tot_reason(result))
