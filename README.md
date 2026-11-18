# Incident Commander

CMU Agentic AI Program capstone (Research Assistant track). An agent that
diagnoses recorded microservice failures — logs, metrics, and traces from the
public [RCAEval](https://arxiv.org/html/2412.17015v1) benchmark — and proposes
fixes that wait for human approval.

**Problem.** When a production alert fires, an on-call engineer faces dozens of
interacting services and far more telemetry than anyone can read under time
pressure, and symptoms often appear far from the cause. **User.** A mid-level
on-call engineer who didn't build every service involved: instead of raw
telemetry they get an incident brief — most likely root cause, the evidence
behind it (every claim cites a log line, metric, trace, or document), the
alternatives considered and rejected, and a proposed fix with any risky action
held for explicit approval.

> Status: **working agent, under evaluation.** Implemented: the ReAct loop, five read-only tools
> behind one MCP server, the evidence scratchpad and citation gate (2.1), runbook retrieval (3.1),
> the dev-set eval runner, Tree-of-Thought hypothesis testing on ambiguity (4.1, `--tot`), and the
> multi-agent LangGraph architecture with role-scoped tools and a Fix Advisor (5.1, `--arch graph`,
> not yet evaluated on the dev set), and the 6.1 safety guardrails, structured escalation handover,
> named safety metrics, and robustness harness (`--hide`, `rescore`). See [DESIGN_LOG.md](DESIGN_LOG.md).

## Project structure
```text
src/incident_commander/   the capstone system
  config.py               paths; dev system vs held-out test system
  loaders.py              RCAEval case loading (the only code touching raw files)
  tools.py                the five read-only diagnostic tools
  mcp_server.py           FastMCP server wrapping them (one server per incident) + sync client
  loop.py                 ReAct loop (Run: the step primitives), citation gate, escalation, ToT triggers
  tot.py                  Tree-of-Thought: beam controller, generators, branch state (4.1)
  signatures.py           fault signature table + deterministic critic (4.1)
  graph.py                LangGraph coordination: spine + conditional ToT subgraph + validation loops (5.1)
  roles.py                Commander / Investigator / Fix Advisor: goals and tool allowlists (5.1)
  fix_advisor.py          runbooks only after the gate; conditional fix + contradiction flags (5.1)
  scratchpad.py           evidence board (short-term memory)
  model.py                OpenRouter/OpenAI-compatible client + keyless MockModel
  retrieval.py            runbook chunking, Chroma index, MMR
  baselines.py            plain-prompt and chain-of-thought baselines
  cli.py                  list / baseline / diagnose entry points
src/research_agent/       Module 1 course starter, kept as the design-evolution baseline
scripts/download_data.py  fetches RCAEval (Zenodo) and the Prometheus runbooks
scripts/tot_offline.py    ToT over all dev cases with no model calls ($0); --hide for the ablation
scripts/regate_offline.py re-run saved tool calls and finishes through the current gate ($0)
scripts/local_eval.sh     resumable local-model eval runner (MLX, never touches a paid API)
data/                     public data only, raw downloads gitignored (see data/README.md)
evals/                    evaluation cases and results
tests/                    pytest
DESIGN_LOG.md             module-by-module design decisions
```

## Setup (macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e .
cp .env.example .env && chmod 600 .env   # OpenRouter key + model id; Claude key as IC_ANTHROPIC_API_KEY (never in your shell)
python scripts/download_data.py --list          # see what's in the RCAEval record
python scripts/download_data.py --match RE2     # then download the dev dataset
python scripts/download_data.py --runbooks
```

## Run
```bash
python -m incident_commander.cli list --system online-boutique
python -m incident_commander.cli baseline --kind plain --case <id>
python -m incident_commander.cli diagnose --case <id>
python -m incident_commander.cli diagnose --case <id> --model mock   # keyless demo
python -m incident_commander.cli diagnose --case <id> --tot model    # + Tree-of-Thought on ambiguity
python -m incident_commander.cli diagnose --case <id> --arch graph   # 5.1 multi-agent graph (ToT on by default)
python -m incident_commander.cli eval --model claude-opus-5-5 --tot model --max-usd 20
python -m incident_commander.cli eval --model claude-opus-5-5 --arch graph --max-usd 20
python scripts/tot_offline.py --suspect truth                       # ToT critic/controller, $0
python scripts/tot_offline.py --suspect truth --hide traces         # same, one telemetry kind withheld
python -m incident_commander.cli eval --model mock --hide metrics   # telemetry ablation on any eval
python -m incident_commander.cli rescore evals/results/<folder>     # 6.1 metrics from saved briefs, no model
python scripts/regate_offline.py                                    # saved runs through the current gate, $0
scripts/local_eval.sh <out_folder> --arch graph --tot model         # local Qwen run, resumable, $0
python -m incident_commander.graph --mermaid                        # draw the 5.1 graphs
python -m pytest -q
```

## Multi-agent architecture (5.1)
`--arch graph` runs the same investigation as a LangGraph state graph: Commander
(LLM) -> citation gate (code) -> Fix Advisor (LLM) -> human approval, with a
conditional edge into the Tree-of-Thought subgraph (Investigators, one LLM call
per branch, and the code critic) when the first pass is ambiguous. Agents never
message each other: they read and write the shared evidence board and the branch
board on the MCP server. Tool access is enforced per role by the MCP client:
the Commander and Investigators cannot call `runbook_lookup`, the Fix Advisor
can call nothing else, so a runbook can steer a fix but never a diagnosis. The
brief carries `path` (nodes visited) and `fix_advisor` (what was retrieved,
what was flagged as contradicted by the evidence, what was dropped).

## Safety guardrails and human intervention (6.1)
All enforced in code, on every architecture unless noted:
- **Tool access.** Read-only tools, one MCP server per incident. Diagnosis agents cannot call
  `runbook_lookup` (linear, ToT, and graph); only the Fix Advisor can.
- **Input.** Tools reject unknown service, metric, level, and argument names and list the valid
  ones. A filter that matched nothing is worded as such, never as a finding.
- **Output.** The citation gate needs two telemetry kinds that each implicate the claimed service:
  warning/error log lines, a metric deviation, or a per-service trace view. A mention is not
  enough. A per-service trace that points at the caller blocks the diagnosis (shared cause). On the
  graph, fix references are checked and untraceable ones recorded as dropped.
- **Action.** Every fix has `requires_approval` forced true whatever the model labelled it; the
  graph also records `approval.state = pending` and `review_required` when a runbook step
  contradicts the evidence. Nothing executes.
- **Escalation.** An escalated brief carries `escalation = {code, reason, hypotheses, note}` with
  code `budget_exhausted`, `unparseable`, `tot_tie`, `tot_no_branch`, `tot_selected_gate_refused`,
  or `unfamiliar`, and ranked hypotheses with their evidence ids.
- **Metrics.** `summary.md` has a `## 6.1 metrics` section: correct, wrong-service, wrong-fault,
  escalations by code, handover quality, groundedness, cost, latency.

## MCP server
The agent reaches its tools only through MCP: `loop.diagnose` opens an in-process
client session to a server bound to the case, and the tool list in its prompt is
rendered from that server's schemas. The same server runs over stdio for any MCP
host, so you can inspect or use the tools outside the agent:
```bash
python -m incident_commander.mcp_server --case checkoutservice_cpu/1      # stdio
npx @modelcontextprotocol/inspector .venv/bin/python -m incident_commander.mcp_server --case checkoutservice_cpu/1  # browse/call tools
claude mcp add incident-commander -- "$PWD/.venv/bin/python" -m incident_commander.mcp_server --case checkoutservice_cpu/1
```
Each server serves one recorded incident, read-only. The held-out guard applies:
Train Ticket cases refuse to load unless `IC_ALLOW_HELDOUT=1`.

## Data policy
Only public, synthetic, or anonymized data. Nothing proprietary, nothing from
any employer's systems. Train Ticket (the held-out system) is never opened
while building, so the unseen-system result stays honest.
