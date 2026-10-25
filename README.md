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

> Status: **scaffold.** Design is set through Checkpoint 2.1 (ReAct loop, five
> read-only tools behind one MCP server, evidence scratchpad); implementation
> lands module by module — see [DESIGN_LOG.md](DESIGN_LOG.md).

## Project structure
```text
src/incident_commander/   the capstone system
  config.py               paths; dev system vs held-out test system
  loaders.py              RCAEval case loading (the only code touching raw files)
  tools.py                the five read-only diagnostic tools
  baselines.py            plain-prompt and chain-of-thought baselines
  cli.py                  list / baseline / diagnose entry points
src/research_agent/       Module 1 course starter, kept as the design-evolution baseline
scripts/download_data.py  fetches RCAEval (Zenodo) and the Prometheus runbooks
data/                     public data only, raw downloads gitignored (see data/README.md)
evals/                    evaluation cases and results
tests/                    pytest
DESIGN_LOG.md             module-by-module design decisions
```

## Setup (macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e .
cp .env.example .env            # add OpenRouter key + model id
python scripts/download_data.py --list          # see what's in the RCAEval record
python scripts/download_data.py --match RE2     # then download the dev dataset
python scripts/download_data.py --runbooks
```

## Run
```bash
python -m incident_commander.cli list --system online-boutique
python -m incident_commander.cli baseline --kind plain --case <id>
python -m incident_commander.cli diagnose --case <id>
python -m pytest -q
```

## Data policy
Only public, synthetic, or anonymized data. Nothing proprietary, nothing from
any employer's systems. Train Ticket (the held-out system) is never opened
while building, so the unseen-system result stays honest.
