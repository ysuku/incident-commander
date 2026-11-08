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
from .mcp_server import ToolClient, build_server
from .scratchpad import TELEMETRY_KINDS, Scratchpad

MAX_STEPS = 8
Z_SIGNIFICANT = 3.0  # "3-sigma": a deviation smaller than this is treated as noise
TRACE_RATIO_SIGNIFICANT = 2.0  # per-service p50 self time; calibrated on RE2-OB rep 1
ERR_RISE_SIGNIFICANT = 0.05    # error-rate rise (absolute) on a service's spans
log = logging.getLogger("incident_commander")
MAX_PARSE_FAILURES = 2
TOT_AFTER_STEPS = 3

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
    loop's own dedup/summarize/record path (a repeated call returns its old eid)."""
    def observe(name: str, args: dict, why: str) -> str | None:
        key = (name, json.dumps(args, sort_keys=True))
        if key in seen_calls:
            return seen_calls[key]
        t_tool = time.monotonic()
        try:
            result = toolbox.call(name, args)
        except (TypeError, ValueError) as e:
            pad.notes.append(f"{why}: {name}({_short(args)}) failed: {e}")
            log.info("   -> %s(%s) FAILED (%s): %s", name, _short(args), why, str(e)[:140])
            return None
        if totals is not None:
            totals["tool_seconds"] += time.monotonic() - t_tool
        kind, summary, ref, implicated = _summarize(name, result, args)
        ev = pad.add(kind, summary, ref, implicated)
        seen_calls[key], observations[ev.eid] = ev.eid, (name, args, result)
        log.info("   -> %s(%s) -> %s [%s] %s (%s)", name, _short(args), ev.eid, kind, summary[:100], why)
        return ev.eid
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
        self.pad = Scratchpad(alert=f"{case.system}: latency/availability alert at "
                                    f"{alert_utc:%Y-%m-%d %H:%M}Z. Find root-cause service and fault type.")
        self.system = SYSTEM_PROMPT.replace("{tools}", toolbox.describe())
        self.trace: list[dict] = []
        self.parse_failures, self.seen_calls = 0, {}
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
            pad.notes.append(f"step {step}: unknown tool {name!r}")
            entry.update(outcome="unknown_tool", detail=str(name))
            log.info("   -> UNKNOWN TOOL %r", name)
            return "retry", "unknown_tool"
        if not isinstance(args, dict):
            pad.notes.append(f"step {step}: bad args for {name}: args must be a JSON object")
            entry.update(outcome="bad_args", detail=f"args was {type(args).__name__}")
            log.info("   -> %s BAD ARGS: not an object (%r)", name, args)
            return "retry", "bad_args"
        key = (name, json.dumps(args, sort_keys=True))
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
            entry.update(outcome="bad_args", detail=str(e))
            log.info("   -> %s(%s) BAD ARGS: %s", name, _short(args), str(e)[:140])
            return None
        tool_s = round(time.monotonic() - t_tool, 2)
        self.totals["tool_seconds"] += tool_s
        kind, summary, ref, implicated = _summarize(name, result, args)
        ev = self.pad.add(kind, summary, ref, implicated)
        self.seen_calls[(name, json.dumps(args, sort_keys=True))] = ev.eid
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
        ambiguous = _fault_ambiguity(self.pad, self.observe, self.observations, finish)
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

    def escalate(self, reason: str) -> dict:
        return _escalate(self.pad, self.trace, reason)

    # ------------------------------------------------------------ Tree-of-Thought
    def tot_context(self, toolbox: ToolClient | None = None):
        from . import tot as tot_mod
        return tot_mod.TotContext(self.pad, toolbox or self.toolbox, self.observe, self.observations,
                                  lambda n, a: (n, json.dumps(a, sort_keys=True)) in self.seen_calls)

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
    run = Run(case, model, toolbox, max_steps, tot)
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


def _fault_ambiguity(pad: Scratchpad, observe, observations: dict, finish: dict) -> str | None:
    """T1: does the critic rank the claimed fault type clearly first for that service?
    Costs one tool call (the service's own metric signature), no model call.
    Returns None when clear, else a one-line reason."""
    from . import signatures as sig
    from .tot import MARGIN
    service, fault = finish.get("root_cause_service"), str(finish.get("fault_type", "")).lower()
    if observe("metric_query", {"service": service}, "tot check: metric signature") is None:
        return None  # not a metric service: nothing to check against, keep the linear answer
    implicates = {e.eid: e.implicates for e in pad.evidence}
    ranked = sig.rank_faults(sig.features(service, observations, implicates))
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


def tot_reason(result: dict, gate_problem: str | None = None) -> str:
    """Why a ToT result ends in an escalation."""
    sel = result["selected"]
    if sel:
        return f"tot selected {sel['service']}: {sel['fault_type']} but the gate refused: {gate_problem}"
    if result["status"] == "tied":
        return ("tie: " + " or ".join(f"{b['service']}: {b['fault_type']}" for b in result["tied"])
                + ", indistinguishable in this data")
    return "tot: no branch passed the two-kinds gate"


def tot_escalation(run: "Run", result: dict, reason: str) -> dict:
    """Escalate with ToT's ranked branches as the candidates."""
    from . import tot as tot_mod
    brief = run.escalate(reason)
    brief["candidates"] = [tot_mod.summary_line(b) for b in result["ranked"]]
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
