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

## Telemetry tools implemented (Oct 25)
`metric_query`, `log_search`, `trace_lookup`, `service_map` implemented in pure pandas and
verified on checkoutservice_cpu/1. All five 2.1 tools now real. Findings worth keeping:
- metric_query (before/after-alert z-scores) ranks the injected fault first on the worked
  case: checkoutservice_cpu z=226 (+4,165%). Lesson: a near-constant baseline makes raw
  z-scores explode (socket series hit z=1.9e9), so the denominator is floored at 5% of the
  baseline mean.
- This case has ZERO error-level logs — a CPU fault makes services slow, not crashy. Logs
  alone cannot diagnose it, which empirically validates the two-independent-telemetry rule.
- Top failing traces route through currency/productcatalog — a misleading clue for a naive
  reader; good demo moment.
Still open from 2.1: evidence scratchpad, ReAct loop, citation check, model client,
baselines, MCP server wrapper.

## ReAct loop, scratchpad, citation gate (Oct 25)
The agent exists. `scratchpad.py` (short-term memory: cited evidence, candidates, notes),
`model.py` (OpenRouter/OpenAI-compatible client + deterministic MockModel for keyless tests),
`loop.py` (orient->reason->act->observe->stop/escalate).
The 2.1 guards are CODE, not prompts:
- citation gate: finish rejected if cited evidence ids don't exist or span <2 independent
  telemetry kinds (metrics/logs/traces; map and runbooks are reference, not evidence);
- destructive fix => requires_approval=True, set in code;
- step budget exhausted => status "escalated" with findings, never a guess.
First full run (MockModel, checkoutservice_cpu/1): correct service+fault in 3 steps, cited
metrics+traces. Second case (emailservice_mem/1): mock names the right service but calls the
fault "delay" — its heuristic trusts the single top metric deviation, and the mem fault
surfaces loudest as latency. Lesson: tool summaries alone don't decide; the model must weigh
deviations. Keep for the baseline-vs-agent story.
Still open from 2.1: real-model runs (Qwen/OpenRouter), plain/CoT baselines, past-incident
store, MCP server wrapper. Next: baselines + a small eval runner over the 30-case dev set.

## First real-model run: Qwen3.6-27B (local MLX), checkoutservice_cpu/1 (Oct 25)
Saved as `evals/runs/2026-10-25_qwen3.6-27b_checkoutservice_cpu_1.json`.
**Result: escalated after 8 steps, no diagnosis. The gate prevented a wrong answer.**
Run 1 had escalated on 3 unparseable replies: the server's default completion budget was
spent on reasoning, leaving empty content. Fixed with max_tokens=4096 (IC_MAX_TOKENS).
Run 2 (this file), what happened:
- Qwen chose sensible tools on its own (service_map -> metrics -> traces -> logs).
- **Tool bug, not model bug:** it asked metric_query for metric="p99_latency", then "latency".
  Exact-match filtering matched nothing, and the tool summarized that as "no metric deviations
  found", a FALSE finding (truth: checkoutservice_cpu z=226). The agent reasonably trusted it.
- With metrics "ruled out", it followed the misleading errored traces through
  currency/productcatalog, the exact trap predicted in the 2.1 write-up, now seen with a real model.
- The citation gate held: currencyservice never got a second telemetry kind, so no wrong cause
  reached a brief. The agent escalated with its candidate list instead of guessing.
- 2 of 8 steps were lost to output problems: one empty reply and one truncated JSON
  (budget again). It also repeated an identical log_search.
**Fixes:** metric_query matches service/metric names tolerantly ("latency" -> latency-50/90) and
raises on unknown names with the valid list, which the loop turns into a note so the model can
correct itself. An empty filter is never reported as a finding. The loop refuses identical
repeated calls and points to the earlier evidence id.
**Lesson for the report:** tool output wording is part of the safety surface. "No deviations
found" and "your filter matched nothing" are different claims, and conflating them misled a
capable model.

## Observability: per-step timing, tokens, and a readable run log (Oct 25)
Prompted by the first Qwen run, where steps took 1 to 21 minutes and two steps vanished from the
trace because failed steps were never recorded.
- The model client records each call's seconds, prompt/completion tokens, reasoning vs answer
  size, and `finish_reason` ("length" = cut off at max_tokens).
- Every loop step is now in `investigation` with an `outcome` (tool_call, unparseable,
  finish_rejected, repeated_call, bad_args, unknown_tool, diagnosed), including failed steps.
- `brief["usage"]` holds run totals: calls, model vs tool seconds, tokens, replies cut off.
  These feed the eval plan's "cost and time per incident" metric.
- A plain-language log goes to the `incident_commander` logger. The CLI streams it to stderr
  and saves `evals/runs/<time>_<model>_<case>.{log,json}` for every run.
- When a reply is cut off, the note sent back to the model says so ("think less, answer
  sooner"). The failure explanation also becomes a correction signal.

## Qwen3-4B run: a confident, well-cited, WRONG diagnosis (Oct 25)
Saved in `evals/runs/` (Qwen3-4B, checkoutservice_cpu/1). 7 calls, 142s total, about 20s per call,
no reply cut off: roughly 15x faster than the 27B. **It diagnosed redis/cpu. The truth is checkoutservice/cpu.**
How it got through:
- The model invented service names ("online-boutique", "database"). The error listing valid names
  worked: it corrected itself by step 3. But it picked "redis" from the list as "the cache" and
  filtered metrics to redis only.
- **Tool wording bug, second time:** within the redis filter, redis_cpu z=0.14 (+2%) was the
  "top deviation". The summary said "top deviation redis_cpu", which made noise read as a finding.
- **Gate bug:** the gate checked that the citations existed and covered two telemetry kinds, not
  that the evidence pointed at the claimed service. E1 (noise) + E3 (traces never mentioning redis)
  passed. The output looked rigorous and was wrong.
- **Approval bug:** the model labeled its fix non-destructive, which switched requires_approval
  off. The 2.1 claim "approval forced in code" was not true until this fix.
**Fixes:**
- Metric summaries classify deviations with a 3-sigma threshold. Below it the summary says
  "NO significant deviation ... largest is only ...", and every summary names its filter scope.
- Each piece of evidence records which services it implicates, computed in code when it is
  created: significant metric deviations, services on the worst failing trace paths, services
  with matching log lines. The gate requires two telemetry kinds that each implicate the
  claimed service. The model cannot cite its way past the evidence.
- Every fix requires approval in v1. The model's destructive label is kept as
  model_labeled_destructive but cannot switch approval off.
- Prompt: "call metric_query with NO filters first", which would have surfaced z=226 in one step.
**Lessons for the report:** (1) "two citations, two telemetry kinds" is a structural check; the
gate must check that the evidence supports the claim. (2) Without ground-truth labels this run
would have been scored a success. Evaluating against labels is what caught it. (3) Model size
traded speed for judgment: 27B escalated honestly, 4B answered fast and wrong.

## After the fixes: first correct real-model diagnosis (Oct 25, evening)
Same case (checkoutservice_cpu/1, truth checkoutservice/cpu), runs saved in `evals/runs/`:

| Model | Code | Outcome | Steps | Time |
|---|---|---|---|---|
| Qwen3.6-27B | pre-fix | escalated (misled by false "no deviations") | 8 | ~60 min |
| Qwen3-4B | pre-fix | WRONG: redis/cpu, passed the old gate | 7 | 2.4 min |
| Qwen3-4B | post-fix (x2) | escalated; identical both runs | 6 | ~17 min |
| Qwen3.6-27B | post-fix | **correct**, cites metrics + traces | 4 | 7.5 min |

- 27B followed the new "unfiltered metric_query first" hint: z=226 on step 1, logs empty
  (expected for CPU), traces for corroboration, finish accepted by the evidence-checked gate.
- 4B also saw the spike but named "online-boutique" (the SYSTEM name, which leads the alert text)
  as the root-cause service. The gate rejected it. Then 3 replies in a row hit the 8192-token
  limit (27,577 output tokens): the small model couldn't recover from the rejection.
- Both 4B reruns produced identical token counts: temperature 0 makes runs deterministic, so
  bugs reproduce exactly. Variance in results has to come from the case set, not from reruns.
Next: (1) the alert text names the system separately and lists valid service names;
(2) try the 4B with reasoning ("thinking") off; (3) eval runner over the 30-case dev set.
At ~7.5 min/case the 27B needs ~4 hours for 30 cases, so it is an overnight run.
