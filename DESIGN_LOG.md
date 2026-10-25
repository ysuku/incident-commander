# Design Log

One entry per module. This feeds the final report's "Design evolution across the program" section.

## Module 1 — Baseline (Sep 2026)
**State:** Course Research Agent starter template, restructured into `src/` layout.

**Changes from the starter**
- Moved package to `src/research_agent`, added `pyproject.toml` (pytest `pythonpath=src`).
- `llm_writer.py`: switched from the OpenAI-only Responses API to Chat Completions so it works with
  OpenRouter; loads `.env` via python-dotenv; no hardcoded default model (LLM mode needs key + model).
- Added `.env.example`, `.gitignore`, data/evals folders.

**Observations (why this is not yet an agent)**
- Fixed 4-step pipeline: same steps every run, no loop, no tool selection, no memory, no stop condition.
- Web search scrapes Bing HTML, which is fragile.

**Next (Module 2):** ReAct loop, tools, memory.

## Module 2 — Pivot to Incident Commander (Oct 2026)
**Decision:** the capstone is **Incident Commander** (Checkpoint 1.1, graded complete):
diagnose recorded RCAEval failures, propose fixes gated on human approval. The generic
research-agent starter stays in `src/research_agent/` as the Module 1 baseline for the
"design evolution" story; new work happens in `src/incident_commander/`.

**Scaffold added**
- `config.py` — dev system (Online Boutique) vs held-out test system (Train Ticket, test-only).
- `loaders.py` — single entry point to raw RCAEval files; everything else sees `IncidentCase`.
- `tools.py` — the five read-only tools from Checkpoint 2.1 (log search, metric query, trace
  lookup, service map, runbook lookup); contract: read-only, summaries not dumps, every result
  carries a citable source ref. To sit behind one FastMCP server.
- `baselines.py` — plain and CoT baselines sharing the agent's output schema, so scoring is identical.
- `scripts/download_data.py` — Zenodo (RCAEval) + runbooks; repo ships the script, never the data.

**Instructor feedback (1.1) folded into the design**
- Conflicting evidence is signal: a hypothesis needs ≥2 independent telemetry types; a conflict
  triggers a tie-breaking query; under ToT each side of a conflict becomes a branch.
- Confidence is evidence-derived (corroboration + no open contradiction + margin over runner-up),
  threshold calibrated on labeled dev failures.
- Evaluate the *brief*, not just the diagnosis: citation coverage, rubric grade, small blinded read test.

**Next:** implement loaders against the real RCAEval layout, then log_search + metric_query,
then the ReAct loop.
