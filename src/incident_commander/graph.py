"""Multi-agent coordination as a LangGraph state graph (Checkpoint 5.1).

A sequential spine, one conditional edge into the Tree-of-Thought subgraph, and
bounded validation loops:

  START -> commander -> act -> commander ...            (the linear loop; T2 may branch to tot)
                     -> gate -> fix_advisor -> human_approval -> END
                        gate -> commander                (rejected, with the reason on the board)
                        gate -> tot                      (T1: passed, but ambiguous)
                     -> escalate -> END                  (budget, unparseable, tie, no branch)
  tot subgraph: root -> critic <-> investigators, critic -> close; its selected
  branch goes back through the same gate before the Fix Advisor sees it.

Coordination is through shared state, not chat: every node reads and writes the
evidence board (loop.Run) and the branch board (on the MCP server); handoffs
are one-way and structured. Tool access differs by role and is enforced by the
scoped MCP client (roles.py): the Commander and Investigators never see
runbook_lookup, the Fix Advisor sees nothing else. Scoring and the gate stay in
code. The brief has the same shape as the linear agent's, so evaluate.py scores
both the same way; it also carries `architecture`, `roles` and `path` (the
nodes visited, in order).

  python -m incident_commander.graph --mermaid      # draw the graph
"""
from __future__ import annotations

import argparse
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from . import fix_advisor as advisor
from . import tot as tot_mod
from .loaders import IncidentCase
from .loop import MAX_STEPS, Run, log, tot_escalation, tot_finish, tot_reason
from .mcp_server import BRANCH_TOOLS, ToolClient, build_server
from .roles import COMMANDER, FIX_ADVISOR, INVESTIGATOR, ROLES


class State(TypedDict, total=False):
    next: str                 # routing signal set by the node that just ran
    action: tuple             # (tool, args) the Commander chose
    finish: dict              # a finish proposal waiting for the gate
    brief: dict               # the accepted brief, then the final brief
    reason: str               # why we are escalating
    tot_request: dict         # {trigger, suspect, rival}
    tot: dict | None          # ToT result, once the subgraph has run
    path: Annotated[list[str], operator.add]


class TotState(TypedDict, total=False):
    trigger: str
    suspect: str
    rival: str | None
    depth: int
    expansions: int
    progressed: bool
    live: list
    outcome: object
    result: dict


def diagnose(case: IncidentCase, model, max_steps: int = MAX_STEPS,
             toolbox: ToolClient | None = None, tot: str = "model") -> dict:
    """Run the graph on one case; returns the incident brief (same shape as loop.diagnose)."""
    if toolbox is not None:
        return _run(case, model, max_steps, toolbox, tot)
    with ToolClient(build_server(case)) as toolbox:
        return _run(case, model, max_steps, toolbox, tot)


def _run(case, model, max_steps, toolbox, tot) -> dict:
    run = Run(case, model, COMMANDER.view(toolbox), max_steps, tot)
    app = build_graph(run, toolbox)
    # each loop step is two supersteps (commander, act); leave room for gate retries and ToT
    out = app.invoke({"path": [], "tot": None},
                     config={"recursion_limit": 6 * max_steps + 40})
    brief = out["brief"]
    brief.update(architecture="graph", path=out["path"],
                 roles={r.name: list(r.tools) for r in ROLES})
    return run.done(brief)


def build_graph(run: Run, toolbox: ToolClient):
    """The compiled graph for one investigation. Nodes close over `run` (the
    evidence board and step primitives) and the incident's MCP client."""
    tot_app, tot_box = build_tot_graph()

    # ------------------------------------------------------------ spine
    def commander(state: State) -> State:
        if run.step >= run.max_steps:
            return {"next": "escalate", "reason": f"step budget ({run.max_steps}) exhausted",
                    "path": ["commander"]}
        kind, payload = run.think()
        if kind == "tool":
            return {"next": "act", "action": payload, "path": ["commander"]}
        if kind == "finish":
            return {"next": "gate", "finish": payload, "tot": None, "path": ["commander"]}
        if kind == "abort":
            return {"next": "escalate", "reason": payload, "path": ["commander"]}
        return {"next": "commander", "path": ["commander"]}  # unusable reply: the board says why

    def act(state: State) -> State:
        name, args = state["action"]
        eid = run.act(name, args)
        if eid and run.tot and (req := run.two_kinds_unmet()):
            return {"next": "tot", "tot_request": _request(req), "path": ["act"]}
        return {"next": "commander", "path": ["act"]}

    def gate(state: State) -> State:
        """Citation gate (code): two telemetry kinds must implicate the service."""
        finish, tot = state["finish"], state.get("tot")
        brief, problem = run.gate(finish)
        if problem:
            if tot:  # ToT's best branch failed the gate: nothing more to try
                return {"next": "escalate", "reason": tot_reason(tot, problem), "path": ["gate"]}
            return {"next": "commander", "path": ["gate"]}
        if run.tot and not tot and (req := run.ambiguity(finish, brief)):
            return {"next": "tot", "tot_request": _request(req), "path": ["gate"]}
        return {"next": "fix_advisor", "brief": run.diagnosed(brief, tot=tot), "path": ["gate"]}

    def tot(state: State) -> State:
        """Conditional ToT subgraph: investigators (LLM, one per branch) + critic (code)."""
        req = state["tot_request"]
        gen = run.generator()
        # investigators propose only diagnostic tools; the controller needs the branch board too
        ctx = run.tot_context(toolbox.scoped(INVESTIGATOR.tools + BRANCH_TOOLS))
        tot_box.update(ctx=ctx, generator=gen, trace=run.trace)
        log.info("ToT START (%s) suspect=%s rival=%s", req["trigger"], req["suspect"], req["rival"] or "-")
        result = tot_app.invoke({**req, "depth": 0, "expansions": 0, "progressed": True},
                                config={"recursion_limit": 4 * tot_mod.DEPTH_LIMIT + 10})["result"]
        run.count_generator(gen)
        result = tot_mod.with_flags(result)
        finish = tot_finish(result, ctx)
        if finish:
            return {"next": "gate", "finish": finish, "tot": result, "path": ["tot"]}
        return {"next": "escalate", "tot": result, "reason": tot_reason(result), "path": ["tot"]}

    def fix_advisor(state: State) -> State:
        """Runs only after the gate; runbook_lookup only; one model call."""
        brief = state["brief"]
        advice = advisor.advise(brief, FIX_ADVISOR.view(toolbox), run.model)
        run.totals["tool_seconds"] += advice["tool_seconds"]
        if advice["model_call"]:
            run.count(advice["model_call"])
        return {"next": "human_approval", "brief": advisor.apply(brief, advice), "path": ["fix_advisor"]}

    def human_approval(state: State) -> State:
        """The human in the loop. Nothing is executed by this system: the brief
        leaves here marked pending, for the on-call engineer to approve or reject."""
        brief = state["brief"]
        brief["approval"] = {"state": "pending", "approver": "human",
                             "policy": "every proposed fix waits for explicit approval (v1)",
                             "flags_for_reviewer": len(brief["proposed_fix"].get("contradictions") or [])}
        return {"brief": brief, "path": ["human_approval"]}

    def escalate(state: State) -> State:
        tot = state.get("tot")
        brief = (tot_escalation(run, tot, state["reason"]) if tot else run.escalate(state["reason"]))
        return {"brief": brief, "path": ["escalate"]}

    g = StateGraph(State)
    for name, fn in (("commander", commander), ("act", act), ("gate", gate), ("tot", tot),
                     ("fix_advisor", fix_advisor), ("human_approval", human_approval),
                     ("escalate", escalate)):
        g.add_node(name, fn)
    g.add_edge(START, "commander")
    route = lambda s: s["next"]
    g.add_conditional_edges("commander", route, ["act", "gate", "escalate", "commander"])
    g.add_conditional_edges("act", route, ["commander", "tot"])
    g.add_conditional_edges("gate", route, ["fix_advisor", "tot", "commander", "escalate"])
    g.add_conditional_edges("tot", route, ["gate", "escalate"])
    g.add_edge("fix_advisor", "human_approval")
    g.add_edge("human_approval", END)
    g.add_edge("escalate", END)
    return g.compile()


def build_tot_graph():
    """The ToT subgraph: root -> critic -> (investigators -> critic)* -> close.
    Same steps as tot.run_tot (open_roots / rescore / decide / expand_beam / close),
    so the two are checked against each other in tests. `box` is filled by the
    parent's tot node with the context, generator and trace for this run."""
    box: dict = {}

    def root(s: TotState) -> TotState:
        tot_mod.open_roots(box["ctx"], s["suspect"], s["rival"])
        return {}

    def critic(s: TotState) -> TotState:
        """Code, never the model: re-judge, prune, rank, and decide."""
        live = tot_mod.rescore(box["ctx"])
        return {"live": live, "outcome": tot_mod.decide(box["ctx"], live)}

    def after_critic(s: TotState) -> str:
        out = s.get("outcome")
        if out and out[0] != "tied":
            return "close"
        if s["depth"] >= 1 and (not s["progressed"] or s["expansions"] >= tot_mod.MAX_EXPANSIONS):
            return "close"
        if s["depth"] >= tot_mod.DEPTH_LIMIT:
            return "close"
        return "investigators"

    def investigators(s: TotState) -> TotState:
        """One Investigator per branch in the beam; each sees one hypothesis."""
        depth = s["depth"] + 1
        expansions, progressed = tot_mod.expand_beam(box["ctx"], box["generator"], s["live"], depth,
                                                     s["expansions"], box["trace"])
        return {"depth": depth, "expansions": expansions, "progressed": progressed}

    def close(s: TotState) -> TotState:
        return {"result": tot_mod.close(box["ctx"], s.get("outcome"), s["trigger"], s["suspect"],
                                        s["rival"], s["expansions"], box["generator"])}

    g = StateGraph(TotState)
    for name, fn in (("root", root), ("critic", critic), ("investigators", investigators), ("close", close)):
        g.add_node(name, fn)
    g.add_edge(START, "root")
    g.add_edge("root", "critic")
    g.add_conditional_edges("critic", after_critic, ["investigators", "close"])
    g.add_edge("investigators", "critic")
    g.add_edge("close", END)
    return g.compile(), box


def _request(req: tuple) -> dict:
    trigger, suspect, rival = req
    return {"trigger": trigger, "suspect": suspect, "rival": rival}


def mermaid() -> str:
    """Both graphs as Mermaid, for the report."""
    class _Stub:  # nodes are never executed when drawing
        step, max_steps, tot = 0, 0, None
    main = build_graph(_Stub(), None).get_graph().draw_mermaid()
    sub = build_tot_graph()[0].get_graph().draw_mermaid()
    return f"%% main graph\n{main}\n%% tot subgraph\n{sub}"


def main() -> None:
    p = argparse.ArgumentParser(prog="incident_commander.graph")
    p.add_argument("--mermaid", action="store_true", help="print the graphs as Mermaid")
    args = p.parse_args()
    if args.mermaid:
        print(mermaid())


if __name__ == "__main__":
    main()
