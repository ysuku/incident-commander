# Opus 5.5 vs local Qwen3.6-27B, linear vs Tree-of-Thought (repetition 1)

Same 30 cases (RE2-OB repetition 1: 6 faults x 5 services), same code (v4), same tools, gate
and critic. The only differences are the model, and whether ToT (`--tot model`) is on.
Scored with `evaluate.score`. Opus figures are the repetition-1 subset of the 90-case runs.

| Outcome (30 cases) | Opus linear | Opus ToT | Qwen linear | Qwen ToT |
|---|---|---|---|---|
| **correct** (service + fault) | 13 | 16 | 11 | **17** |
| service_only (wrong fault) | 11 | **0** | 16 | **0** |
| wrong service | 0 | 0 | 0 | 1 |
| escalated | 6 | 14 | 3 | 12 |
| of which: tie that includes the truth | - | 8 | - | 8 |
| model calls / case | 4.8 | 6.4 | 4.9 | 6.8 |
| wall time / case | 0.4 min | 0.6 min | 10.6 min* | 22.3 min |
| cost (30 cases) | $1.79 | $2.49 | $0 | $0 |

\* The Qwen linear run had client timeouts and retries (see Operations); its wall time
includes duplicated generation, so it overstates the per-case time somewhat.

## Correct by fault type (correct / tie that includes the truth)

| Fault | Opus linear | Opus ToT | Qwen linear | Qwen ToT |
|---|---|---|---|---|
| cpu | 2 | 4 | 3 | 4 |
| delay | 5 | 5 | 4 | 4 |
| disk | 1 | 0 / 3 | 1 | 1 / 3 |
| loss | 3 | 5 | 0 | 4 / 1 |
| mem | 1 | 0 / 5 | 1 | 0 / 4 |
| socket | 1 | 2 | 2 | 4 |

## Linear -> ToT, case by case

| Transition | Opus | Qwen |
|---|---|---|
| service_only -> correct | 4 | 8 |
| service_only -> escalated (mostly mem/disk ties) | 7 | 7 |
| escalated -> correct | 1 | 0 |
| correct -> escalated (mem/disk ties) | 2 | 2 |
| service_only -> wrong service | 0 | 1 |
| unchanged | 16 | 12 |

## Findings

1. **ToT helps both models, and helps the weaker linear model more.** Qwen linear gave the
   wrong fault in 16 of 30 cases; with ToT, none. Qwen's ToT result (17) is level with
   Opus's (16): on 30 cases a one-case gap is noise, not a ranking. The two agree on 13 of
   the correct cases. Much of the fault-type decision is made by the deterministic critic,
   which is the same for both models, so the model's own fault-type judgement matters less
   once ToT runs.
2. **mem and disk remain unsolved for both models.** With the disk metric unusable, ToT
   reports "mem or disk" instead of guessing (8 ties containing the truth for each model).
3. **One new failure mode (Qwen, `productcatalogservice_mem/1`).** ToT selected the rival
   branch `recommendationservice: loss` over the true suspect. Two causes:
   - the rival's trace check was scored *confirmed* although the trace summary itself says
     the slowdown is "likely frontendservice's own network, not recommendationservice";
   - the suspect's mem/disk branches were pruned on the score gap while their trace signal
     was still *untested*: the rival was tested first, so it led by 2.
   Both are critic/controller rules, not model behaviour; fixing them would need a re-run of
   both models to keep the comparison fair.
4. **Proposed tests.** Qwen proposed 60 ToT tests, 2 of which failed (a metric that does not
   exist, e.g. `swap`); Opus proposed 53, none failed. A failed test costs a step but does
   not corrupt the result.
5. **Cost vs time.** Qwen is free but about 40x slower per case on this Mac (Apple Silicon,
   64 GB, 4-bit 27B via MLX), and ToT doubles its per-case time.

## Operations (local model)

The MLX server crashed twice during the Qwen ToT run (`[metal::malloc] Resource limit
(499000) exceeded`), producing 11 and then 9 error cases. The cause was the client, not the
model: replies longer than the OpenAI SDK's 600 s default timed out and were retried while
the server was still generating, so duplicate requests piled up. Fixed by
`IC_REQUEST_TIMEOUT=3600` and `IC_MAX_RETRIES=0` (read in `model.py`, set in
`scripts/qwen_ab.sh`), plus a fresh server per case. After the fix: 0 retries, 0 crashes.
Error cases were set aside in `evals/archive/qwen_tot_errors*/` and re-run; every scored
case above is a clean run.

## Files

- `evals/results/2026-11-05_opus-5-5_all_v4_linear/`, `..._tot-model/` (90 cases each; read-only;
  backup in `evals/archive/2026-11-05_opus-5-5_ab_v4.tgz`)
- `evals/results/2026-11-06_qwen3.6-27b_r1_v4_linear/`, `..._tot-model/` (30 cases each)
- `scripts/qwen_ab.sh` (re-runs or resumes the Qwen pair)
