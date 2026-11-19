# Design Log

One entry per module. This feeds the final report's "Design evolution across the program" section.

## Module 1 — Baseline
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

## Module 2 — Pivot to Incident Commander
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

## Data layout findings + loaders
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

## Module 3 — Retrieval layer for runbook_lookup
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

## Telemetry tools implemented
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

## ReAct loop, scratchpad, citation gate
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

## First real-model run: Qwen3.6-27B (local MLX), checkoutservice_cpu/1
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

## Observability: per-step timing, tokens, and a readable run log
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

## Qwen3-4B run: a confident, well-cited, WRONG diagnosis
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

## After the fixes: first correct real-model diagnosis
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

## MCP server: the tools now sit behind FastMCP
Closes the last open 2.1 item ("MCP server wrapper"). `mcp_server.py`:
- `build_server(case)` registers the five tools on a FastMCP server bound to one recorded
  case. A client can only reach the incident it was given; the held-out guard in
  `load_case` still applies.
- **The server owns the tool contract.** Input schemas come from the typed signatures;
  each tool has a short model-facing description (`DESCRIPTIONS`). The prompt's tool list
  is rendered from `list_tools`, replacing the hand-written block in `SYSTEM_PROMPT`.
  This was the drift risk raised in Lab 1.2 ("a weak description makes a weak tool"):
  there is now one source. The rendered text keeps the old prompt's signatures and wording
  (only line breaks moved), so earlier runs stay comparable.
- `ToolClient`: the loop is sync, MCP is async. One client session stays open on a
  background event loop (anyio blocking portal) for the whole run. `diagnose()` opens it
  by default; tests can pass their own.
- Errors: unknown names, bad types and missing params come back as MCP `isError`
  results. The client raises them as `ToolError` (a ValueError), so the loop's existing
  bad_args note path is unchanged. Pydantic now rejects bad types (e.g. limit="SIX")
  before the tool runs. Non-object `args` (a JSON list) used to crash the loop and is
  now a bad_args note.
- Same server over stdio (`python -m incident_commander.mcp_server --case <id>`) for
  MCP Inspector / Claude Desktop / Claude Code. (`mcp dev <file>` does not apply: it needs a
  module-level server, and ours is built per case.)
- SDK choice: the official `mcp` package's FastMCP (as used in Lab 1.2), pinned
  `>=1.12,<2` because 2.x renames FastMCP. Verified on mcp 1.30.0.
Tests: `tests/test_mcp_server.py` (schemas and descriptions, rendered prompt, error
mapping, MCP results equal direct calls, non-object args, real stdio subprocess).
37 tests pass.
Not adopted from the lab: resources, prompts, sampling, roots, auth. The tools are
model-controlled, and the only client is local, so none of these are needed yet.

## First dev-set eval on Claude Opus 5.5
New: `AnthropicClient` (`model.py`, native SDK, adaptive thinking, effort `IC_EFFORT=medium`,
server-side refusal fallback, per-call cost) and the eval runner (`evaluate.py`,
`cli eval`): scores each case against the folder label as correct / service_only / wrong /
escalated / error, saves each brief as it finishes (resumable), stops at `--max-usd`.
Repetitions 2 and 3 of RE2-OB are now extracted: 90 dev cases (this run used rep 1 only).

**Run 1, harness bug.** 38 of 203 model calls were refused by a safety classifier
(category `reasoning_extraction`), 27 of them on step 1. The prompt asked for a per-step
`"thought"` field, which reads as asking the model to dump its hidden reasoning. Each
refusal burned a step; 4 cases escalated purely from repeated refusals. Result 5 correct /
7 service_only / 1 wrong / 17 escalated, not a fair number.
**Fix:** the field is now `"rationale"`, one sentence on why this action (the loop still
accepts `"thought"`, so old runs and the mock parse). Run 2: zero refusals.

**Run 2 (`evals/results/2026-10-28_opus-5-5_r1_v2/`), 30 cases, $2.74, 19 min:**

| Outcome | n | share |
|---|---|---|
| correct (service + fault) | 6 | 20% |
| service_only | 10 | 33% |
| wrong service | 1 | 3% |
| escalated (all: step budget) | 13 | 43% |

- Right service in 16/30 (53%), and wrong only once: when it commits, it is almost never
  pointing at the wrong service. Compare Qwen3-4B pre-fix, which was confident and wrong.
- Fault type is the weak part: mem->cpu/delay/loss, disk->mem, loss/socket->delay.
  Data limits explain some of it: simple_metrics has no network metric (loss looks like
  delay) and diskio exists for only 4 of 11 services.
- Opus is stricter than the gate: on checkoutservice_cpu/1 the gate would accept E1+E2,
  but the model judged the traces did not isolate checkoutservice and kept looking.
  `trace_lookup` has no service filter, so CPU/mem faults (no error logs) rarely get a
  clean second kind of evidence. That is the main driver of budget escalations.
- About 37s and $0.09 per case, against ~7.5 min for Qwen3.6-27B: the whole dev set
  (90 cases) costs about $8 per configuration.
Next: (1) `trace_lookup(service=...)`: per-service span latency/error before vs after;
(2) a fault-signal guide (which telemetry each fault type moves) via the runbook layer;
(3) rerun on reps 1-3 (90 cases) after each change.

## Per-service traces: 90-case eval and a caller-side fix
`trace_lookup(service=...)` (commit 9353e69): the service's own self time and its
callers' "inbound" time, before vs after the alert; implicates only on >=2x p50 or
>=0.05 error-rate rise.

**Run 3 (`evals/results/2026-10-28_opus-5-5_all_v3/`), 90 cases (reps 1-3), $5.85, 42 min:**

| Outcome | all 90 | rep 1 | rep 1 in run 2 |
|---|---|---|---|
| correct | 27 (30%) | 12 | 6 |
| service_only | 33 (37%) | 11 | 10 |
| wrong service | 2 (2%) | 0 | 1 |
| escalated (all: step budget) | 28 (31%) | 7 | 13 |

- Rep 1 doubled correct (6 -> 12) and halved escalations (13 -> 7); right service in
  60/90 (67%). The new filter was used in all 90 cases. Reps 2 and 3 score lower than
  rep 1 (7 and 8 correct), so the rep-1-only number from run 2 was optimistic.
- Delay is solved (13/15 correct). The weak faults are cpu (8/15 escalated) and socket
  (10/15 escalated): offline, traces fail to implicate the true service in 28 of 90
  cases, all cpu/disk/socket/some loss on checkout, email and productcatalog. Those must
  be diagnosed from metrics alone, and the gate wants two kinds.
- Fault type is still the main error (33 service_only): loss->delay 7, mem->cpu 4,
  mem->delay 4, disk->mem 4.

**Both wrong answers were the same blind spot:** `checkoutservice_delay/2` and `/3`
were blamed on emailservice. The delay was on checkout's network, and checkout is
emailservice's only caller, so email's inbound time rose 55x. Split by caller, it is
plain: checkout's calls to all six callees slowed ~200ms; frontend's calls to the
same services stayed flat. **Fix:** `trace_lookup(service=...)` now also returns
inbound by caller (with that caller's calls to its other callees) and outbound by
callee. A callee is not implicated when the only slow callers are also slow to >=2
other callees; those callers are implicated instead ("likely checkoutservice's own
network"). A service slow to >=2 callees is implicated ("delay on its own network").
Also: services that export no spans of their own (shipping, cart, ad) used to be
"unknown"; they now get the inbound-only view. Checked offline on all 90 cases: the
true service is implicated in the same 62 as before (no regression), and the
checkout-delay cases now point at checkout. Regression test:
`test_caller_side_delay_is_pinned_on_the_caller`.
Next: the fault-signal guide, then one 90-case rerun for both changes.

## Module 4 — Where Tree-of-Thought helps: the data changed the plan
**Decision:** ToT is appropriate, but only for hypothesis testing and only when the first pass is
ambiguous. Checkpoint 4.1 plan, not yet implemented.

**The earlier 4.1 premise was wrong.** The draft assumed the linear loop's main weakness was
anchoring on the wrong SERVICE (misleading failing traces). Re-reading the 90-case Opus run
(`evals/results/2026-10-28_opus-5-5_all_v3`):
- first unfiltered metric_query implicates the true service in 90/90 cases;
- in all 28 escalations the true service was already ranked FIRST in the final candidates (20 of
  28 also already named the true fault word); the loop stalled on the two-kinds rule;
- 33 service_only: right service, wrong fault (loss->delay 7, mem->cpu/delay 8, disk->mem 4);
  in 15 of 33 the true fault word appears in the brief's alternatives/fix text (text match);
- wrong service only 2/90, both callee-vs-caller delay (since fixed in trace_lookup).
So the error mass is fault-type ambiguity, a competing-hypotheses problem.

**Plan:** branches = fault-type hypotheses for the suspect service (+ a rival-service branch on
conflict); beam search width 2, depth <=3, <=8 expansions; scoring in code from a signature table
(fault type -> telemetry it must move); signals are confirmed / contradicted / unobservable and only
"contradicted" prunes (diskio exists for 4 of 11 services; no network metric); shared evidence
board, per-branch scores; ties escalate. Triggered only on ambiguity, so cases that already
work (delay 13/15) pay nothing.
**To test (Module 6):** same 90 cases with vs without ToT: correct / service_only / escalated,
cost per case. Estimated ceiling ~$0.20/case (Opus), from escalated-run cost of $0.10.

## Module 4 — ToT implemented: 3.1 retrieval loop -> 4.1 hypothesis tree
The 4.1 plan now runs; `diagnose --tot model|heuristic`, `eval --tot ...`. Without `--tot`
the linear 3.1 agent is unchanged (same prompt, same gate, same outputs).

**Where each 4.1 role lives**
| 4.1 role | Code | Module 5 target |
|---|---|---|
| Thought generator | `tot.ModelGenerator` (LLM proposes {tool, args, predicted}; reply field is `rationale`, not `thought`, after the Oct 28 refusals), `tot.HeuristicGenerator` (zero-cost ablation) | CrewAI Investigator per branch |
| Critic | `signatures.py`: signature table + `judge`/`score`, pure code | LangChain Runnable |
| Controller | `tot.run_tot`: root -> expand -> rescore -> prune -> select / tie / escalate | LangGraph state graph |
| State manager | `BranchBoard` behind the MCP server: `open_branch`, `record_result`, `close_branch`, `branch_status` | same server |
The controller is framework-free on purpose: each step is a function over shared state,
so the LangGraph port is mechanical. Branch tools are on the MCP server but hidden from the
ReAct prompt (`ToolClient.diagnostic`). Evidence stays on the loop's scratchpad, and ToT
calls tools through the loop's own dedupe path (`make_observer`), so a call runs once and
every branch reuses it.

**Triggers (4.1 "only when ambiguous").** T1: a finish passes the gate, but the critic
does not rank its fault type first by >= MARGIN, or a must-have signal contradicts it. The check
costs one tool call and no model call, so clear cases like checkoutservice_cpu/1 keep the
linear answer. T2: after 3 steps, no service has two telemetry kinds implicating it.
**The suspect comes from the model's candidate list, not from the raw top metric
deviation.** That top deviation is not the true service in 20 of 90 cases (frontend-external 9,
frontend 5, emailservice 3 for checkout delay, ...). The 4.1 text says "the first metric query named
S". It did implicate S in 90/90, but did not rank it first.

**Calibration findings (data, not the plan)**
- `diskio` is unusable everywhere: z is 0, NaN or 1e12..1e19 in every fault type. So disk's
  must-have signal is always unobservable. Per the 4.1 risk rule that never prunes disk,
  but disk can also never be confirmed, so **mem and disk tie** (mem z 22+ in both).
  ToT reports "mem or disk, indistinguishable in this data" instead of guessing.
- Logs are dropped from the signature table. The root-cause service logged zero
  error lines in all 90 cases, so a logs signal would only contradict true hypotheses.
- Signals are five-valued in code (confirmed / contradicted / inconclusive / unobservable /
  untested). "Inconclusive" is the grey zone (3 <= z < strong threshold). "Untested" drives the
  generator. Only "contradicted" scores negative or prunes.
- Added a rule not in 4.1: a branch cannot be selected while any of its signals is
  untested. Without it, ToT selected on the root ranking with zero tests run.
- Loss vs delay is separated without a network metric by `conn_pressure` (mem or socket
  z >= 5 with no cpu rise; delay never passes 3.4) plus `resources_flat` for delay.

**Offline check, $0** (`scripts/tot_offline.py`, heuristic generator, suspect given):
| suspect | correct | service_only | wrong | tie incl. truth | tie w/o truth | escalated, truth 1st | escalated |
|---|---|---|---|---|---|---|---|
| truth service | 36 | 2 | 0 | 22 | 2 | 21 | 7 |
| top metric deviation | 23 | 0 | 5 | 17 | 2 | 21 | 22 |
With the service right, the wrong-fault failure (33 service_only in the linear Opus run)
nearly disappears (2). Delay is 15/15. The 21 "escalated, truth first" cases are the
cpu/socket faults on checkout, email and productcatalog, where traces stay flat. ToT cannot
create a second telemetry kind, so it escalates with the right answer ranked first.
The 4.1 risk did show up: the 2-point prune gap dropped the true branch in 3 cases.
Mean expansions are 1.0 to 1.2. **The tree is shallow in practice** (5 tools, 2 of
them scoped per service), so the 8-expansion cap and $0.20/case estimate are loose upper bounds.
These are dev-set numbers with thresholds read off the same 90 cases. Train Ticket is the
honest test.

**Next:** paid A/B on the 90 cases (`eval --model claude-opus-5-5 --tot model` vs the v3
run), then `--tot heuristic` as the zero-cost ablation. Module 5: port the controller to
LangGraph and the generator to CrewAI agents.

## Module 4 — ToT A/B on the 90 dev cases
Same code, same model (Opus 5.5, effort medium), all 90 RE2-OB cases; only `--tot model` differs.
The linear baseline was rerun because v3 predates the caller-side trace fix.
`evals/results/2026-11-05_opus-5-5_all_v4_linear/` vs `..._all_v4_tot-model/`.

| Outcome | linear v4 | ToT (model generator) |
|---|---|---|
| correct | 29 (32%) | **38 (42%)** |
| service_only (wrong fault) | 34 | **2** |
| wrong service | 0 | 0 |
| escalated | 27 | 50 |
| of which: tie that includes the truth | - | 24 |
| cost | $5.98 ($0.066/case) | $8.25 ($0.092/case, **1.38x**) |
| wall time | 42 min | 56 min |

- **The 4.1 bar is met:** the correct rate rose at less than 2x cost (1.38x). ToT ran in 44 of 90 cases
  (T1 38, T2 6); the other 46 paid one extra tool call and no model call. Mean 3.6
  expansions per ToT case; all 159 generator replies were usable (no fallbacks).
- **Case transitions:** 12 service_only became correct (ToT selected the right fault), 3 escalated
  became correct, 18 service_only became honest "mem or disk" ties that include the truth.
  Cost: 6 linear-correct cases became ties, all mem/disk, where the linear agent's guess
  happened to be right. 2 ties excluded the truth.
- **By fault:** cpu 3 -> 10 correct, loss 4 -> 8, socket 1 -> 5, delay 15 -> 15. mem and disk went
  to 0 correct, 30 escalated, nearly all ties naming the true fault. That is the diskio data gap made
  explicit instead of guessed: linear got 6/30 of these right and 18 wrong-fault.
- Offline prediction (`tot_offline_truth.md`: 36 correct, 2 service_only, 22 ties) matched
  the paid run closely (38 / 2 / 24 ties).

**Trigger gap found: 22 escalations where ToT never ran.** In every one, some service
(usually the true one) already had two telemetry kinds implicating it, so T2 ("two-kinds
rule unmet") could not fire. But the model never finished: it judged the trace evidence too
weak (the service on a slow path, not isolated) and spent the budget. All 22 are cpu, disk,
socket or loss on checkout, email, currency and recommendation, the same no-trace-signal cases.
Option for Module 5 (a design choice, not made yet): T3 = run ToT when the budget is
nearly spent instead of escalating. ToT's own selection rule requires the scoped trace
test to have run, but the gate would still accept loose path evidence. So T3 should
probably also tighten the gate, accepting only service-scoped traces, or it would trade
honest escalations for weakly evidenced answers.

## Module 4 — local Qwen3.6-27B vs Opus, linear vs ToT, repetition 1
Same 30 cases, same v4 code; full tables in `evals/model_comparison_r1.md`.

| 30 cases | Opus linear | Opus ToT | Qwen linear | Qwen ToT |
|---|---|---|---|---|
| correct | 13 | 16 | 11 | 17 |
| service_only | 11 | 0 | 16 | 0 |
| wrong service | 0 | 0 | 0 | 1 |
| escalated (ties with truth) | 6 | 14 (8) | 3 | 12 (8) |
| min / case | 0.4 | 0.6 | 10.6 | 22.3 |

- ToT removes every wrong-fault answer for both models; Qwen gains more (8 service_only ->
  correct vs 4 for Opus). Qwen ToT and Opus ToT are level: the shared deterministic critic
  makes much of the fault-type call.
- New failure (Qwen, productcatalogservice_mem/1): ToT selected the rival
  `recommendationservice: loss`. (Corrected Nov 8: the rival's trace verdict was right, its own
  spans slowed 2.33x.) The real cause: the rival was fully tested and scored 5 while the true
  suspect's trace signal was still untested (score 3), so the suspect was pruned on the gap and
  out-ranked. Fixed in v5/v6, below.
- Ops: MLX server crashed twice (Metal buffer limit). Cause: SDK 600 s timeout + 2 retries
  re-sent long generations while the server was still working. Fixed with
  `IC_REQUEST_TIMEOUT` / `IC_MAX_RETRIES` (model.py; defaults unchanged) and a fresh server per
  case in `scripts/qwen_ab.sh`. Error cases were set aside and re-run.

## Module 5 — Multi-agent architecture implemented: the 5.1 plan as a LangGraph graph
`diagnose --arch graph` / `eval --arch graph`. The linear agent (`--arch linear`, default) is
unchanged, so every earlier number stays comparable. Not yet evaluated on the dev set.

**Where each 5.1 component lives**
| 5.1 component | Code |
|---|---|
| Commander (LLM) | `graph.commander` + `graph.act` nodes over `loop.Run.think/act`; tools = diagnostic only |
| Investigators (LLM) | `graph.build_tot_graph` `investigators` node over `tot.expand_beam`; `tot.ModelGenerator` per branch |
| Fix Advisor (LLM) | `fix_advisor.advise/apply`; `graph.fix_advisor` node, runs only after the gate; `runbook_lookup` only |
| Critic (code) | `critic` node over `tot.rescore` + `tot.decide` (signatures.py unchanged) |
| Citation gate (code) | `graph.gate` node over `loop.Run.gate`; ToT's selected branch goes through the same node |
| Human | `graph.human_approval`: the brief leaves marked `approval.state = pending`; nothing executes |
| Shared state | evidence board (`loop.Run`) + branch board (MCP server); nodes pass routing signals only |
| Role tool access | `roles.py` allowlists, enforced by `mcp_server.ScopedToolClient` (refuses, lists, describes) |

**How the port was done.** `loop._investigate` was split into `loop.Run` (shared state + step
primitives: think, act, gate, ambiguity, two_kinds_unmet, diagnosed, escalate) and the linear loop
was rewritten over them (59 existing tests unchanged, all pass). `tot.run_tot` was split the same
way (open_roots / rescore / decide / expand_beam / close) and the one-function controller rewritten
over the parts. The graph nodes call exactly those functions, so the two architectures share one
implementation of every step. `test_tot_subgraph_matches_the_one_function_controller` runs both on
three cases (delay, mem tie, suspect-vs-rival) and asserts identical status, expansions, and ranked
branches.

**Decisions made while building, beyond the 5.1 text**
- **CrewAI not adopted.** `pip --dry-run` showed it pins pydantic < 2.13 (the venv has 2.14 for the
  MCP SDK) and brings a large tree. The 5.1 plan allowed dropping it if it added nothing over plain
  role definitions; `roles.Role` (name, goal, tool allowlist) is that, and a CrewAI Agent can be
  generated from it later. LangGraph 1.2 installed cleanly and is the only new dependency.
- **The Fix Advisor sees only the accepted diagnosis and its cited evidence** (`fix_advisor.render`):
  no candidates, notes, or rejected alternatives. The runbook query is built in code from the
  diagnosed fault (`SYMPTOMS`), so the advisor costs one tool call and one model call per diagnosed
  case, as 5.1 budgeted. Every runbook id and evidence id the model cites is checked in code;
  untraceable references go to `fix_advisor.dropped`, never into the brief. An unusable reply falls
  back to the 4.1 `REMEDIATION` template with no contradiction flags. Approval stays forced in code.
- **ToT's selected branch is re-gated by the same `gate` node** as a Commander finish (5.1 figure:
  best branch -> citation gate -> Fix Advisor), with T1 suppressed on that pass.
- **Investigators run sequentially for now.** In the 4.1 controller, branch 2's proposal sees branch
  1's test result and the dedupe path; a parallel fan-out (LangGraph `Send`) would change that and
  the comparison with the 4.1 numbers. Parallelism is a Module 6 experiment, not a free change.
- **The evidence board stays in-process** (`loop.Run`), not on the MCP server as the 5.1 figure draws
  it; the branch board is on the server. Moving it would touch the linear loop; deferred.
- The brief gains `architecture`, `path` (nodes visited in order), `roles`, `fix_advisor`,
  `approval`; `evaluate.py` scores it unchanged and adds `arch` and `fix_flags` columns.
- The keyless mock now answers the Investigator prompt by deferring to the heuristic generator and
  the Fix Advisor prompt with a canned plan, so `--model mock --arch graph` exercises every node.

**Next:** (1) `eval --model claude-opus-5-5 --arch graph` on the 90 cases against the v4 ToT run
(expect the same diagnoses plus a fix section and contradiction flags; cost +1 call per diagnosed
case); (2) read the flagged contradictions by hand on a sample, since no label exists for them;
(3) the T3 trigger question from Nov 5 is now a one-line routing change in `graph.commander`.

## Module 4 — ToT ceiling rule; v6 re-run on frozen 5.1 code
Rule: a branch with untested signals is pruned and compared on its ceiling (untested counted as
confirmed: `signatures.ceiling`), so a less-tested branch cannot lose to a more-tested one.
`decide` returns None (keep testing; escalate at budget) while any rival's ceiling is within
MARGIN. Offline check (90 cases, both suspect modes) is unchanged; the bug needs a ReAct phase
that has already tested one branch, which the offline harness does not create. Regression test
`test_a_less_tested_branch_is_not_beaten_by_a_more_tested_rival`.

| 30 cases | Opus ToT v4 | Opus ToT v6 | Qwen ToT v4 | Qwen ToT v6 |
|---|---|---|---|---|
| correct | 16 | 15 | 17 | 16 |
| wrong service | 0 | 0 | 1 | 0 |
| escalated (ties w/ truth) | 14 (8) | 15 (8) | 12 (8) | 14 (9) |
| cost | $2.49 | $2.73 | $0 | $0 |

The wrong-service case is now a tie containing the truth; each model lost one correct answer to
a tie where the rival could not be ruled out. With ToT neither model ships a wrong answer.
A first v5 Qwen run was discarded: the 5.1 edits landed mid-run and each case is a new process.
Both v6 runs record code fingerprint `f6d377c126c7` (snapshot in `evals/archive/`); the Qwen
script now stops if `src/` changes. Full tables: `evals/model_comparison_r1.md`.

## Module 6 — Safety guardrails and human intervention implemented: the 6.1 plan in code
Checkpoint 6.1 listed guardrails, metrics, and escalation rules. An audit of the code against it
(every 6.1 sentence, each gap re-checked independently) found that most claims were true for
`metric_query` and the graph path but stated for all tools and all paths. This entry closes the
gaps. **The linear path changes on purpose here**: safety fixes apply to every architecture, so
the Module 4-5 numbers describe gate v1 and the numbers below need a re-baseline (local Qwen).
Code fingerprint `493760396fe8`; tests 72 -> 229.

**5.1 reviewer feedback this answers**
| Feedback | Answer in code |
|---|---|
| "can two supporting sources always be trusted" | implicates tightened; shared-cause rule in the gate |
| "unfamiliar issues" | `unfamiliar` escalation code; code-level fault scoring (RE3-OB) |
| "multiple services" | pinned by test: equal evidence -> tie naming both; unequal -> one root cause (limit) |
| "incomplete and conflicting information" | `--hide` ablation; contradiction -> `approval.review_required` |

**Where each 6.1 guardrail lives, and on which path**
| 6.1 row | Code | linear | ToT | graph |
|---|---|---|---|---|
| Tool access: read-only, one server per incident | `mcp_server.build_server`; snapshot test | yes | yes | yes |
| Tool access: diagnosis agents cannot read runbooks | `loop.diagnose` scopes to `COMMANDER.tools + BRANCH_TOOLS` | **new** | **new** | yes |
| Input: unknown names rejected with the valid list | `tools.metric_query/log_search/trace_lookup`; `ToolClient.call` (argument names) | yes | yes | yes |
| Input: empty filter is not a finding | `loop._summarize` zero-match wording; `failing_only` fallback implicates nobody | yes | yes | yes |
| Output: two kinds must each implicate | `loop._summarize` implicates + `loop._gate` | yes | yes | yes |
| Output: shared cause | `loop._shared_cause` (whole board, caller-side trace pattern) | yes | yes | yes |
| Output: fix references checked | `fix_advisor.advise` (`dropped`, incl. free-text R/E tokens) | n/a | n/a | yes |
| Reasoning: critic in code, tie stays a tie | `signatures.py`, `tot.decide/close` (`co_leaders`) | n/a | yes | yes |
| Action: approval pending, nothing executes | `loop._fix` (key whitelist, forced `requires_approval`); `approval.state` | forced flag | forced flag | flag + state |
| Runtime: budget, repeats refused, per-step log | `loop.Run.think/act`; ToT `tot_no_test` entries, `tool_seconds` | yes | yes | yes |
| Escalation: structured handover | `brief["escalation"] = {code, reason, hypotheses, note}` | yes | yes | yes |
| Metrics | `evaluate.metrics_section`, `cli rescore` | yes | yes | yes |

**What "implicates" means now (gate v2).** Before, a log call implicated every service with any
matching line and a path-level trace call implicated every service on the three slowest traces,
so the gate accepted frontend with fault type "banana" in a probe. Now: logs implicate only on
warning/error-level lines; a path-level trace lookup implicates nobody (its error span belongs to
the caller that recorded the failure: frontend in 30 of 30 rep-1 cases, the true service in 0);
only a per-service trace lookup counts. The gate also rejects a fault type outside the six, and
rejects a service when a per-service trace entry anywhere on the board points at its caller.

**Offline re-gate of saved runs** (`scripts/regate_offline.py`: saved tool calls re-run on the
recorded data through the new code, saved finish re-gated; no model calls; "after" is the
pessimistic bound where every refused brief becomes an escalation):
| Run | Diagnosed | Refused now | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|---|
| Opus v4 linear (90) | 63 | 1 | 29 -> 29 | 34 -> 33 | 0 -> 0 | 27 -> 28 |
| Opus v4 ToT (90) | 40 | 3 | 38 -> 35 | 2 -> 2 | 0 -> 0 | 50 -> 53 |
| Qwen v4 linear (30) | 27 | 7 | 11 -> 9 | 16 -> 11 | 0 -> 0 | 3 -> 10 |
| Qwen v6 ToT (30) | 16 | 4 | 16 -> 12 | 0 -> 0 | 0 -> 0 | 14 -> 18 |

All 15 refusals are "two kinds unmet": the second kind was info log lines or a place on a slow
trace. Some of those briefs were correct, so the stricter gate costs correct answers that were
right for a reason the evidence did not support; a real run has steps left to gather a per-service
trace instead. The same script on a HEAD checkout shows 0 changes over 1,333 calls (harness check).
Input rules newly refuse 11 / 45 / 6 / 15 saved calls: a service with no log lines in the case,
`a|b|c` queries (matched literally before, so they could never hit), and 1-2 letter fragments.

**6.1 metrics, now computed by code.** `cli rescore <folder>` rebuilds `results.csv` and a
`## 6.1 metrics` section from saved briefs into a sibling folder. It reproduces every 6.1 number:
| Opus 5.5, gate v1, 90 cases | v4 linear | v4 ToT |
|---|---|---|
| Correct | 29 (32%) | 38 (42%) |
| Wrong service | 0 | 0 |
| Wrong fault | 34 | 2 |
| Escalated | 27 | 50 |
| By code | budget_exhausted 27 | tot_tie 26, budget_exhausted 22, tot_no_branch 2 |
| Cost per case | $0.066 | $0.092 |
| Latency mean / median / p95 | 27.8 / 24.7 / 46.6 s | 37.6 / 40.8 / 72.6 s |

Escalation quality (ToT run): the true service is in all 50 handovers. Of the 28 that ran ToT, 24
are ties containing the truth, 2 ties without it, 2 service-only. Of the 22 budget escalations the
true fault is the top candidate in 8 (free-text match, approximate). Groundedness has columns
(`fix_advised`, `fix_dropped`, `fix_flags`) but no data: the Fix Advisor runs only on the graph.

**Telemetry ablation, free offline run** (heuristic generator, suspect = true service, 90 cases;
reports in `evals/tot_offline_truth_hide-*.md`):
| Hidden | correct | tie with truth | escalated, truth first | escalated | wrong |
|---|---|---|---|---|---|
| nothing | 36 | 22 | 21 | 7 (+2 tie w/o, 2 svc-only) | 0 |
| logs | 36 | 22 | 21 | 7 | 0 |
| traces | 0 | 0 | 68 | 22 | 0 |
| metrics | 0 | 0 | 15 | 75 | 0 |

With a kind missing the system escalates and never guesses. Traces hidden escalates everything
here only because the heuristic never searches logs; a model may recover some through logs.
Metrics hidden escalates by design (every must-have signal is a metric); its "truth first" count
is an ordering artefact and should not be quoted.

**Decisions made while building, beyond the 6.1 text**
- **Where the code differs from the 6.1 wording (code is right, wording is loose):**
  - "Only a contradicted signal prunes": the ceiling rule also closes out-ranked
    branches. Pruning is unchanged; the docstring now says both.
  - "Step budget of 8": per phase. Commander 8 + ToT expansions 8 + 1 Fix Advisor call; a test
    pins the bound.
  - "A runbook step contradicts the evidence" does not escalate. The brief stays `diagnosed` and
    carries `approval.review_required` + `review_reason`: flagged to the approver. Detection is
    proposed by the model and validated in code.
  - "Every branch is pruned -> unfamiliar" was unreachable: the disk signal can never be
    contradicted, so a disk branch always survives. Redefined below.
  - Role allowlists are enforced by the MCP client, not per-role servers (one server per incident).
- **Unfamiliar fault** (`signatures.unfamiliar`, set in `tot.close`): every fault type with an
  observable must-have is contradicted, the survivors survive only because their must-have is
  unobservable, score <= 0, the service has usable cpu/mem/socket series, and two kinds implicate
  it. Then the code is `unfamiliar` and disk is listed as "not a lead". A first version fired for
  11 of 90 dev cases with a label-free suspect; after the last two conditions it fires for 0 of 90
  (and 0 of 75 saved ToT escalations), so it cannot be confirmed on the dev set: RE3-OB is its test.
- **Code-level fault scoring** (`evaluate._score_unfamiliar`): when the label is outside the six
  types, `correct_unfamiliar` = escalated with the true service as lead hypothesis; any diagnosis
  is `wrong`. Six-type scoring is unchanged.
- **Escalation handover.** Non-ToT escalations build ranked hypotheses in code from the evidence
  board (no tool calls); a budget that ran out with an empty board is coded `unparseable`, not
  `budget_exhausted`. `reason` and `candidates` are kept for old readers.
- **Unusable output.** A malformed `action` / `finish` used to crash the run (status `error`,
  excluded from scoring); it now counts toward the three-strikes escalation. Unknown-tool and
  bad-args replies are not merged into that counter. A failing call is remembered, so an identical
  bad call is refused the second time (it ran 8 times in a probe).
- **`log_search` on a service with no log lines** is refused with the list of services that have
  logs, worded "no log lines from X in this case (it is a known service)", not "unknown".
- **`--hide`** is a scoped client view carrying a `hidden` set; hidden signals are unobservable,
  not untested; the gate is never relaxed to one kind. With metrics hidden, ToT and graph escalate
  by design; plain linear still diagnoses and the brief carries `fault_type_unchecked`.
- **Train Ticket.** Callees come from the span parent graph when the `hipstershop.` regex matches
  nothing (`loaders.span_callees`; identical on all 90 OB cases, 900 lookups). The held-out run is
  stamped once every case is saved (`heldout_complete.json`); a second folder is refused; a partial
  run is not stamped. `_diskio` and all thresholds are untouched: no tuning for the held-out system.
- **The mock model** used a path-level trace as its second kind and was refused on every case by
  gate v2; it now uses a per-service lookup. Mock, 90 cases, linear: 20 correct, 26 service-only,
  5 wrong, 39 escalated.

**Open**
- **Frontend residual.** Frontend's own per-service trace lookup still implicates frontend in some
  downstream faults: a probe (unfiltered metrics + `trace_lookup(service=frontend)`) is accepted in
  5 of 30 rep-1 cases, and these are the mock's 5 wrong answers. Closing it means recalibrating the
  per-service signal on the dev set. Metrics have the same shared-cause shape and no check. 6.1
  already states "the shared-cause check covers one known pattern".
- **Disk metric.** "Unusable" is overstated: the true service's diskio z is huge in 10 of 15 disk
  cases and 8 of 75 others; the critic discards those values. A dev-set study, not a quick fix.
- **Multi-service faults**: one root cause per brief; RCAEval has no multi-fault cases.
- **Parallel Investigators** (promised for Module 6 on Nov 7) and the T3 trigger: not done.
- **Runs.** Rep-1 linear, rep-1 graph and `--hide traces` on gate v2 are done (next entry). Not
  run yet: `--hide metrics` on a real model, RE3-OB (after download), Train Ticket once, last.

## Module 6 — Gate v2 on a real model: linear, multi-agent graph, traces hidden

Three runs of the same 30 cases (RE2-OB repetition 1: 6 faults x 5 services) on local
Qwen3.6-27B (MLX, 4-bit), one code version (fingerprint `493760396fe8`), $0, 0 errors. Run with
`scripts/local_eval.sh`; the folders are `evals/results/2026-11-17_qwen3.6-27b_r1_v7_*`.

| 30 cases | Linear | Graph (`--arch graph --tot model`) | ToT, traces hidden (`--tot model --hide traces`) |
|---|---|---|---|
| correct (service + fault) | 12 | 13 | 0 |
| right service, wrong fault | 10 | 0 | 0 |
| wrong service | 1 | 0 | 0 |
| escalated | 7 | 17 | 30 |
| escalation codes | budget_exhausted 7 | tot_tie 10, tot_no_branch 7 | tot_no_branch 30 |
| escalations that name the truth | 3 of 7 ranked first | 15 of 17 (9 in a tie, 6 ranked first) | 21 of 30 ranked first |
| model calls / case | 5.4 | 7.7 | - |
| wall time / case (mean) | 12.2 min | 25.2 min | 36.3 min |
| wall time (30 cases) | 365 min | 755 min | 1088 min |

Graph, correct by fault: cpu 3, delay 5, loss 3, socket 2, mem 0, disk 0 (of 5 each). ToT ran in
22 of 30 graph cases.

**What the runs show**
1. **The graph gives no wrong answer of either kind.** Every case is correct or handed to a
   human, and 15 of the 17 handovers name the true service and fault. Linear is wrong in 11 of
   30 (10 wrong fault, 1 wrong service). Case by case, linear -> graph: 8 correct in both, 5
   wrong-fault -> correct, 5 wrong-fault -> escalated, 1 wrong-service -> escalated, 4 correct ->
   escalated, 7 escalated in both. The price is a 57% escalation rate and twice the time per case.
2. **Gate v2 on plain linear does ship one wrong service.** `productcatalogservice_loss/1` is
   diagnosed as `frontendservice: socket`: the frontend residual listed as open in the previous
   entry, now seen on a real model (the offline re-gate of saved runs showed none). The 6.1
   statement of zero wrong-service answers holds for the ToT and graph paths, not for linear
   without ToT. The graph escalates this case.
3. **mem and disk are still unsolved.** All 10 are escalated by the graph, most as a "mem or
   disk" tie that contains the truth. Same finding as v6; the disk metric is the cause.
4. **First groundedness numbers (graph, 13 advised briefs).** 4 fix references could not be
   traced to a retrieved runbook chunk and were dropped (0.31 per brief); 29 runbook steps were
   flagged as contradicted by the evidence (2.23 per brief) and carried to the approver as
   `review_required`. Every fix has `requires_approval` true.
5. **With traces hidden the system escalates every case and never guesses.** Each case still had
   log and metric evidence, but no branch gathered two kinds that implicate its service: Online
   Boutique logs rarely carry warning or error lines (51 of 71 log searches returned nothing, and
   a log entry implicated a service in 2 of 30 cases), so logs seldom count as a second kind. The
   offline run predicted this (90 of 90 escalated); the hope that a model would recover some
   through logs did not hold. The handover still ranks the truth first in 21 of 30. This is the
   intended fail-safe, and it also says the agent is only useful when traces are available.

**Reading these numbers**
- One repetition, 30 cases: a difference of one or two cases is noise.
- The graph result (13 correct) is not directly comparable with v6 Qwen ToT on the linear
  architecture (16 correct, 14 escalated): the gate changed between v6 and v7 and there is no
  v7 ToT run on the linear architecture without hiding, so gate and architecture are confounded.
- Against the old gate, linear moved from 11 / 16 / 0 / 3 (v4: correct / wrong fault / wrong
  service / escalated) to 12 / 10 / 1 / 7. All 7 escalations are step-budget exhaustion.

**Still open after these runs:** the frontend residual (now with a real-model case), `--hide
metrics` on a real model, RE3-OB, Train Ticket, and the single-prompt / RAG-only baselines.
