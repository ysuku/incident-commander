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

## Data layout findings + loaders (Oct 25)
RE2-OB verified on disk: `RE2-OB/<service>_<fault>/<run>/` — **ground truth is the folder
name**, so loaders carry it on `IncidentCase` for scoring only; nothing building agent
context may read it. Per case: `inject_time.txt`, `logs.csv` (~23MB), `traces.csv` (~70MB),
`simple_metrics.csv` (wide, per-service), `logts.csv` + `cluster_info.json` (log-template
time series), `tracets_lat/err.csv` (per-operation latency/error series). The log/trace
sizes confirm the core design point: no context window holds a case, so tools summarize.
- `loaders.py` implemented and tested against real data; big files load lazily via helpers.
- **Held-out guard in code:** `train-ticket` raises unless `IC_ALLOW_HELDOUT=1`, so the
  unseen-system result can't be contaminated by accident.
- CLI `list` works. Next: `log_search` + `metric_query` on the extracted case, then the
  plain/CoT baselines, then the ReAct loop.

## Module 3 — Retrieval layer for runbook_lookup (Oct 25)
**Decision:** retrieval IS required, for the long-term memory only. Telemetry (logs, metrics,
traces) stays on structured tools; runbooks/postmortems are text and need semantic lookup.

**Added:** `retrieval.py` (chunking, Chroma index, MMR, distance threshold), wired into
`tools.runbook_lookup`; `tests/test_retrieval.py` (logic tests with a hashing stand-in embedder);
`scripts/retrieval_demo.py` (worked example + 6-query smoke check with the real MiniLM embedder).
- Chunking: per-runbook "symptom" (title+Meaning+Impact) and "action" (title+Diagnosis+Mitigation)
  chunks, split on the runbooks' own headings, title repeated in every chunk. Scoped corpus:
  59 runbooks -> 130 chunks (median 337 chars, max 1231).
- Scope: kubernetes + node runbooks only (59 of 105 runbooks); monitoring-stack runbooks
  (alertmanager, etcd, prometheus...) are distractors for workload faults.
- K=4 after fetching 12; cosine-distance cut-off 0.65 (STARTING value, calibrate on dev cases);
  MMR lambda 0.6. Empty result = "no runbook match", never a guess.
- Runbook hits are reference, not evidence: a root cause still needs two telemetry types.
**Caveat:** the sandbox used for development could not download MiniLM, so retrieval quality
numbers come only from running `scripts/retrieval_demo.py` locally -> `evals/retrieval_demo_output.txt`.
**Not yet:** postmortem summaries and confirmed past incidents are not indexed (data/postmortems empty).
