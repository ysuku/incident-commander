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
> the dev-set eval runner, and Tree-of-Thought hypothesis testing on ambiguity (4.1, `--tot`).
> See [DESIGN_LOG.md](DESIGN_LOG.md).

## Project structure
```text
src/incident_commander/   the capstone system
  config.py               paths; dev system vs held-out test system
  loaders.py              RCAEval case loading (the only code touching raw files)
  tools.py                the five read-only diagnostic tools
  mcp_server.py           FastMCP server wrapping them (one server per incident) + sync client
  loop.py                 ReAct loop, citation gate, escalation, ToT triggers
  tot.py                  Tree-of-Thought: beam controller, generators, branch state (4.1)
  signatures.py           fault signature table + deterministic critic (4.1)
  scratchpad.py           evidence board (short-term memory)
  model.py                OpenRouter/OpenAI-compatible client + keyless MockModel
  retrieval.py            runbook chunking, Chroma index, MMR
  baselines.py            plain-prompt and chain-of-thought baselines
  cli.py                  list / baseline / diagnose entry points
src/research_agent/       Module 1 course starter, kept as the design-evolution baseline
scripts/download_data.py  fetches RCAEval (Zenodo) and the Prometheus runbooks
scripts/tot_offline.py    ToT over all dev cases with no model calls ($0)
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
python -m incident_commander.cli eval --model claude-opus-5-5 --tot model --max-usd 20
python scripts/tot_offline.py --suspect truth                       # ToT critic/controller, $0
python -m pytest -q
```

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
