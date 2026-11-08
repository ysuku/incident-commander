"""Tree-of-Thought hypothesis testing (Checkpoint 4.1).

Triggered by the ReAct loop only when the first pass is ambiguous; replaces its
reason/stop steps from there. Roles (4.1 section 5), framework-free for now so
each maps onto one node / agent when Module 5 wires LangGraph and CrewAI:
  thought generator  ModelGenerator (an LLM proposes the one test that best
                     separates a branch from its rival) or HeuristicGenerator
                     (code picks the test for the branch's first untested signal)
  critic             signatures.judge/score: deterministic, never model self-grading
  controller         run_tot: expand -> score -> prune -> select | tie | escalate (beam search)
  state manager      BranchBoard behind the MCP server (open_branch, record_result,
                     close_branch, branch_status); evidence stays on the shared
                     scratchpad, so a tool call runs once and every branch reuses it

Tree: a node is a hypothesis (service, fault type) with its verdicts, score and
status; depth = tests run on it. Root branches: the top 3 fault types for the
suspect by metric signature (+1 rival-service branch on conflict), beam width 2
after that, depth <= 3, <= 8 expansions (one generator call each).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from . import signatures as sig
from .loop import _norm, _parse

log = logging.getLogger("incident_commander")

ROOT_BRANCHES = 3   # fault types opened for the suspect service
BEAM_WIDTH = 2      # branches expanded per level after the first
DEPTH_LIMIT = 3
MAX_EXPANSIONS = 8  # generator calls; ~ one escalated linear run (8 steps, $0.10 on Opus)
PRUNE_GAP = 2       # drop a branch scoring >= 2 below the best
MARGIN = 1          # best must lead the runner-up by >= 1 to be selected


# ---------------------------------------------------------------- state manager
@dataclass
class Branch:
    branch_id: str
    service: str
    fault_type: str
    reason: str = ""                       # why it was opened
    status: str = "open"                   # open | pruned | selected | tied | exhausted
    close_reason: str = ""
    score: int = 0
    kinds: list[str] = field(default_factory=list)       # telemetry kinds among confirmed signals
    verdicts: list[dict] = field(default_factory=list)   # latest critic verdicts
    tests: list[dict] = field(default_factory=list)      # {eid, tool, args, predicted, rationale, by}

    @property
    def label(self) -> str:
        return f"{self.service}: {self.fault_type}"


class BranchBoard:
    """Branch state for one investigation. Lives with the MCP server (one per
    incident), so any MCP host can inspect the tree with branch_status."""

    def __init__(self):
        self.branches: dict[str, Branch] = {}

    def open(self, service: str, fault_type: str, reason: str = "") -> dict:
        if fault_type not in sig.FAULT_TYPES:
            raise ValueError(f"fault_type must be one of {sig.FAULT_TYPES}")
        for b in self.branches.values():  # one node per hypothesis
            if _norm(b.service) == _norm(service) and b.fault_type == fault_type:
                return asdict(b)
        b = Branch(f"B{len(self.branches) + 1}", service, fault_type, reason)
        self.branches[b.branch_id] = b
        return asdict(b)

    def record(self, branch_id: str, verdicts: list[dict], test: dict | None = None) -> dict:
        """Store the critic's verdicts (and the test that produced them); the score
        is recomputed here from the verdicts, so every reader sees the same number."""
        b = self._get(branch_id)
        vs = [sig.Verdict(**v) for v in verdicts]
        b.verdicts = [asdict(v) for v in vs]
        b.score = sig.score(vs)
        b.kinds = sorted({v.kind for v in vs if v.verdict == sig.CONFIRMED})
        if test:
            b.tests.append(test)
        return asdict(b)

    def close(self, branch_id: str, status: str, reason: str = "") -> dict:
        if status not in ("pruned", "selected", "tied", "exhausted", "open"):
            raise ValueError("status must be pruned|selected|tied|exhausted|open")
        b = self._get(branch_id)
        b.status, b.close_reason = status, reason
        return asdict(b)

    def status(self) -> dict:
        ranked = sorted(self.branches.values(), key=lambda b: (b.status != "pruned", b.score), reverse=True)
        return {"branches": [asdict(b) for b in ranked]}

    def _get(self, branch_id: str) -> Branch:
        if branch_id not in self.branches:
            raise ValueError(f"unknown branch {branch_id!r}; open branches: {sorted(self.branches)}")
        return self.branches[branch_id]


# ---------------------------------------------------------------- generators
@dataclass
class Thought:
    tool: str
    args: dict
    predicted: str
    rationale: str
    by: str  # "model" | "heuristic" | "heuristic (model fallback: ...)"


def _untested_tests(branch: dict, f: sig.Features) -> list[tuple[str, dict, str]]:
    """(tool, args, predicted) for the branch's signals no evidence speaks to yet."""
    svc, out = branch["service"], []
    for s in sig.SIGNATURES[branch["fault_type"]]:
        if s.judge(f)[0] != sig.UNTESTED:
            continue
        test = (("metric_query", {"service": svc}) if s.kind == "metrics"
                else ("trace_lookup", {"service": svc}))
        if all(test != (t, a) for t, a, _ in out):
            out.append((*test, s.expect))
    return out


class HeuristicGenerator:
    """Zero-cost generator: the test for the branch's first untested signal."""
    calls = 0

    def propose(self, branch: dict, rival: dict | None, ctx: "TotContext") -> Thought | None:
        todo = _untested_tests(branch, ctx.features(branch["service"]))
        if not todo:
            return None
        tool, args, predicted = todo[0]
        return Thought(tool, args, predicted, f"first untested signal of {branch['fault_type']}",
                       "heuristic")


GENERATOR_PROMPT = """You are an Investigator testing ONE hypothesis about a recorded microservice incident.
Your hypothesis: {hypothesis}. Its strongest rival: {rival}.
Its signature (what must hold if it is true): {signature}.
Propose the single read-only test that best separates your hypothesis from the rival.
Respond with ONE JSON object and nothing else:
  {{"rationale": "<one sentence>", "tool": "<name>", "args": {{...}},
   "predicted": "<the result you expect if your hypothesis is true>"}}
Tools: {tools}
Do not repeat a call already on the evidence board. If no test can add information, reply {{"tool": null}}."""


class ModelGenerator:
    """An LLM proposes the test (4.1: one model call per expansion). An unusable
    reply falls back to the heuristic test, so a bad reply never costs a branch."""

    def __init__(self, model):
        self.model, self.calls, self.last_calls = model, 0, []
        self.fallback = HeuristicGenerator()

    def propose(self, branch: dict, rival: dict | None, ctx: "TotContext") -> Thought | None:
        sigs = "; ".join(f"{s.name}: {s.expect}" for s in sig.SIGNATURES[branch["fault_type"]])
        system = GENERATOR_PROMPT.format(
            hypothesis=f"{branch['service']}: {branch['fault_type']}",
            rival=f"{rival['service']}: {rival['fault_type']}" if rival else "none",
            signature=sigs, tools=ctx.toolbox.describe())
        raw = self.model.complete([{"role": "system", "content": system},
                                   {"role": "user", "content": ctx.pad.render()}])
        self.calls += 1
        self.last_calls.append(dict(getattr(self.model, "last_call", {}) or {}))
        try:
            msg = _parse(raw)
            tool, args = msg.get("tool"), msg.get("args") or {}
            if tool is None:
                return None
            if tool not in ctx.toolbox.diagnostic or not isinstance(args, dict):
                raise ValueError(f"unusable tool/args {tool!r}")
            if ctx.already_ran(tool, args):
                raise ValueError(f"{tool}({args}) already on the board")
            return Thought(tool, args, str(msg.get("predicted", ""))[:200],
                           str(msg.get("rationale", ""))[:200], "model")
        except Exception as e:
            t = self.fallback.propose(branch, rival, ctx)
            if t:
                t.by = f"heuristic (model fallback: {str(e)[:80]})"
            return t


# ---------------------------------------------------------------- controller
class TotContext:
    """What the controller shares with the ReAct loop: the scratchpad, the MCP
    toolbox, and `observe`, the loop's own dedup + summarize + record path."""

    def __init__(self, pad, toolbox, observe, observations: dict, already_ran):
        self.pad, self.toolbox, self.observe = pad, toolbox, observe
        self.observations, self.already_ran = observations, already_ran

    def features(self, service: str) -> sig.Features:
        implicates = {e.eid: e.implicates for e in self.pad.evidence}
        return sig.features(service, self.observations, implicates)

    def implicating_kinds(self, service: str) -> dict[str, list[str]]:
        """telemetry kind -> eids on the board that implicate `service` (the gate's view)."""
        out: dict[str, list[str]] = {}
        for e in self.pad.evidence:
            if e.kind in ("metrics", "logs", "traces") and _norm(service) in {_norm(s) for s in e.implicates}:
                out.setdefault(e.kind, []).append(e.eid)
        return out


def run_tot(ctx: TotContext, suspect: str, rival: str | None, generator,
            trigger: str, trace: list | None = None) -> dict:
    """Beam search over fault hypotheses. Returns
    {status: selected|tied|escalated, selected, ranked, expansions, ...}."""
    tb, trace = ctx.toolbox, trace if trace is not None else []
    log.info("ToT START (%s) suspect=%s rival=%s", trigger, suspect, rival or "-")

    def rescore() -> list[dict]:
        live = []
        for b in tb.call("branch_status", {})["branches"]:
            if b["status"] != "open":
                continue
            vs = sig.judge(b["fault_type"], ctx.features(b["service"]))
            b = tb.call("record_result", {"branch_id": b["branch_id"],
                                          "verdicts": [asdict(v) for v in vs]})
            dead = sig.must_contradicted(vs)
            if dead:
                why = "; ".join(f"{v.signal} contradicted: {v.observed} ({v.eid})" for v in dead)
                tb.call("close_branch", {"branch_id": b["branch_id"], "status": "pruned", "reason": why})
                log.info("   prune %s %s: %s", b["branch_id"], b["service"] + ":" + b["fault_type"], why)
                continue
            live.append(b)
        if live:
            # prune on the ceiling (untested signals counted as confirmed): a branch that
            # has only been tested less must not lose to one that has been tested more
            best = max(b["score"] for b in live)
            for b in [b for b in live if _ceiling(b) <= best - PRUNE_GAP]:
                why = (f"score {b['score']} (at most {_ceiling(b)} if every untested signal held) "
                       f"is {best - _ceiling(b)}+ below the best ({best})")
                tb.call("close_branch", {"branch_id": b["branch_id"], "status": "pruned", "reason": why})
                log.info("   prune %s %s: %s", b["branch_id"], b["service"] + ":" + b["fault_type"], why)
                live.remove(b)
        return sorted(live, key=lambda b: (b["score"], len(b["kinds"])), reverse=True)

    def decide(live: list[dict]) -> tuple[str, list[dict]] | None:
        """selected when the best is fully tested (no signal left untested), passes
        the two-kinds gate, has no contradicted signal, and leads the runner-up by
        MARGIN (or on telemetry kinds) over every other live branch. A branch with
        untested signals is compared on its ceiling, so it must be tested (or be out
        of reach even if all its signals held) before another can win; None = keep
        testing (and escalate if the budget runs out)."""
        if not live:
            return "escalated", []
        best = live[0]
        gate = ctx.implicating_kinds(best["service"])
        clean = not any(v["verdict"] in (sig.CONTRADICTED, sig.UNTESTED) for v in best["verdicts"])
        if len(gate) < 2 or not clean:
            return None

        def beaten(o: dict) -> bool:
            if _untested(o):
                return best["score"] - _ceiling(o) >= MARGIN
            return best["score"] - o["score"] >= MARGIN or len(best["kinds"]) > len(o["kinds"])

        others = live[1:]
        if all(beaten(o) for o in others):
            return "selected", [best]
        if any(_untested(o) and not beaten(o) for o in others):
            return None
        return "tied", [b for b in live if b["score"] == best["score"]
                        and len(b["kinds"]) == len(best["kinds"])]

    # root: the suspect's metric signature, then its top fault types
    ctx.observe("metric_query", {"service": suspect}, "tot root: metric signature of the suspect")
    roots = [r for r in sig.rank_faults(ctx.features(suspect)) if not sig.must_contradicted(r[2])]
    for fault, s, _ in roots[:ROOT_BRANCHES]:
        tb.call("open_branch", {"service": suspect, "fault_type": fault,
                                "reason": f"top fault types for {suspect} by metric signature (score {s})"})
    if rival and _norm(rival) != _norm(suspect):
        try:
            ctx.observe("metric_query", {"service": rival}, "tot root: metric signature of the rival")
            r = [r for r in sig.rank_faults(ctx.features(rival)) if not sig.must_contradicted(r[2])]
            if r:
                tb.call("open_branch", {"service": rival, "fault_type": r[0][0],
                                        "reason": "rival service from conflicting candidates"})
        except ValueError as e:  # rival not a metric service
            log.info("   rival %s skipped: %s", rival, e)

    expansions, outcome = 0, None
    live = rescore()
    for depth in range(1, DEPTH_LIMIT + 1):
        outcome = decide(live)
        if outcome and outcome[0] != "tied":
            break
        beam = live if depth == 1 else live[:BEAM_WIDTH]
        progressed = False
        for b in beam:
            if expansions >= MAX_EXPANSIONS:
                break
            rival_b = next((o for o in live if o["branch_id"] != b["branch_id"]), None)
            thought = generator.propose(b, rival_b, ctx)
            if thought is None:
                continue
            expansions += 1
            eid = ctx.observe(thought.tool, thought.args,
                              f"tot {b['branch_id']} {b['service']}:{b['fault_type']} depth {depth}")
            test = {"eid": eid, "tool": thought.tool, "args": thought.args, "depth": depth,
                    "predicted": thought.predicted, "rationale": thought.rationale, "by": thought.by}
            tb.call("record_result", {"branch_id": b["branch_id"], "verdicts": b["verdicts"], "test": test})
            trace.append({"step": len(trace) + 1, "phase": "tot", "branch": b["branch_id"],
                          "hypothesis": f"{b['service']}: {b['fault_type']}", "thought": thought.rationale,
                          "action": {"tool": thought.tool, "args": thought.args},
                          "outcome": "tot_test" if eid else "tot_test_failed", "observed": eid,
                          "generator": thought.by,
                          "model_call": (generator.last_calls[-1] if getattr(generator, "last_calls", None)
                                         and thought.by == "model" else {})})
            progressed = progressed or bool(eid)
        live = rescore()
        if not progressed or expansions >= MAX_EXPANSIONS:
            outcome = decide(live)
            break
    else:
        outcome = decide(live)

    status, chosen = outcome or ("escalated", [])
    for b in chosen:
        tb.call("close_branch", {"branch_id": b["branch_id"], "status": status,
                                 "reason": "best branch passed the gate" if status == "selected"
                                 else "indistinguishable from another branch in this data"})
    ranked = tb.call("branch_status", {})["branches"]
    log.info("ToT END %s | %d expansions | ranked: %s", status.upper(), expansions,
             "; ".join(f"{b['service']}:{b['fault_type']}={b['score']}({b['status']})" for b in ranked))
    return {"status": status, "trigger": trigger, "suspect": suspect, "rival": rival,
            "selected": chosen[0] if status == "selected" else None,
            "tied": chosen if status == "tied" else [],
            "ranked": ranked, "expansions": expansions,
            "generator_calls": getattr(generator, "calls", 0)}


def _untested(b: dict) -> bool:
    return any(v["verdict"] == sig.UNTESTED for v in b["verdicts"])


def _ceiling(b: dict) -> int:
    return sig.ceiling([sig.Verdict(**v) for v in b["verdicts"]])


def alternatives(result: dict) -> list[dict]:
    """Pruned and losing branches become the brief's rejected alternatives, with evidence."""
    sel = result["selected"]
    out = []
    for b in result["ranked"]:
        if sel and b["branch_id"] == sel["branch_id"]:
            continue
        contra = [f"{v['signal']} {v['observed']} ({v['eid']})" for v in b["verdicts"]
                  if v["verdict"] == sig.CONTRADICTED]
        why = b["close_reason"] or f"scored {b['score']} vs {sel['score'] if sel else '-'}"
        if contra and "contradicted" not in why:
            why += "; contradicted: " + "; ".join(contra)
        out.append({"candidate": f"{b['service']}: {b['fault_type']}", "why": why})
    return out


def unobservable_flags(branch: dict) -> list[str]:
    return [f"{v['signal']}: {v['observed']}" for v in branch["verdicts"] if v["verdict"] == sig.UNOBSERVABLE]


def summary_line(b: dict) -> str:
    conf = [v["signal"] for v in b["verdicts"] if v["verdict"] == sig.CONFIRMED]
    return (f"{b['service']}: {b['fault_type']} (score {b['score']}, {b['status']}; "
            f"confirmed {', '.join(conf) or 'none'})")
