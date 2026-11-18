# Opus 5.5 vs local Qwen3.6-27B, linear vs Tree-of-Thought (repetition 1)

Same 30 cases (RE2-OB repetition 1: 6 faults x 5 services), same tools, gate and critic.
The only differences are the model, and whether ToT (`--tot model`) is on. Scored with
`evaluate.score`. The headline runs are **v6**: ToT with the ceiling rule (below), all on one
frozen code version (fingerprint `f6d377c126c7`, snapshot `evals/archive/src_f6d377c126c7.tgz`).
Linear runs are from v4; the ToT changes since v4 do not touch the linear path.

## Headline (v6)

| Outcome (30 cases) | Opus linear | Opus ToT | Qwen linear | Qwen ToT |
|---|---|---|---|---|
| **correct** (service + fault) | 13 | 15 | 11 | **16** |
| service_only (wrong fault) | 11 | **0** | 16 | **0** |
| wrong service | 0 | 0 | 0 | 0 |
| escalated | 6 | 15 | 3 | 14 |
| of which: tie that includes the truth | - | 8 | - | 9 |
| ToT ran in | - | 14 cases | - | 22 cases |
| model calls / case | 4.8 | 7.0 | 4.9 | 7.5 |
| wall time / case | 0.4 min | 0.6 min | 10.6 min* | 22.7 min |
| cost (30 cases) | $1.79 | $2.73 | $0 | $0 |

\* The Qwen linear run had client timeouts and retries (see Operations); its wall time
includes duplicated generation, so it overstates the per-case time somewhat.

With ToT, **neither model ships a wrong answer**: every case is either correct or escalated, and
most escalations name the truth (a tie containing it, or the truth ranked first).

## Correct by fault type (correct / tie that includes the truth)

| Fault | Opus linear | Opus ToT | Qwen linear | Qwen ToT |
|---|---|---|---|---|
| cpu | 2 | 4 | 3 | 4 |
| delay | 5 | 5 | 4 | 4 |
| disk | 1 | 0 / 2 | 1 | 1 / 2 |
| loss | 3 | 4 / 1 | 0 | 3 / 2 |
| mem | 1 | 0 / 5 | 1 | 0 / 5 |
| socket | 1 | 2 | 2 | 4 |

## Linear -> ToT (v6), case by case

| Transition | Opus | Qwen |
|---|---|---|
| service_only -> correct | 3 | 7 |
| service_only -> escalated (mostly mem/disk ties) | 8 | 9 |
| escalated -> correct | 1 | 0 |
| correct -> escalated (mem/disk ties) | 2 | 2 |
| unchanged | 16 | 12 |

## Findings

1. **ToT removes every wrong-fault answer for both models, and helps the weaker linear model
   more.** Qwen linear gave the wrong fault in 16 of 30 cases; with ToT, none, and 7 of those
   became correct. Qwen ToT (16) and Opus ToT (15) are level: on 30 cases a one-case gap is
   noise. Both are correct on 12 of the same cases. Much of the fault-type decision is made by
   the deterministic critic, which is the same for both models.
2. **mem and disk remain unsolved for both models.** With the disk metric unusable, ToT reports
   "mem or disk" instead of guessing.
3. **Ceiling rule (v5/v6) fixed a wrong-service answer.** In v4, Qwen's
   `productcatalogservice_mem/1` selected the rival `recommendationservice: loss`. The rival's
   traces had already been read during the ReAct phase, so all its signals were tested and it
   scored 5. The true suspect's trace signal was still untested, so it scored 3: it was pruned on
   the score gap and out-ranked, though one more test could have tied it. Fix: a branch with
   untested signals is pruned and compared on its *ceiling* (untested signals counted as
   confirmed), so it must be tested, or be out of reach, before another branch can win. In v6
   this case is a tie that includes the truth. Cost: one correct answer per model became a tie
   (Opus `productcatalogservice_loss/1`, Qwen `currencyservice_loss/1`), where the rival could
   not be ruled out. The trace rule itself was correct: the rival's own spans did slow 2.33x,
   above the 2x threshold.
4. **Proposed tests.** Qwen proposed 76 ToT tests, 2 of which failed (e.g. a metric that does
   not exist, such as `swap`); Opus proposed 74, 1 failed. A failed test costs a step but does
   not corrupt the result.
5. **Cost vs time.** Qwen is free but about 40x slower per case on this Mac (Apple Silicon,
   64 GB, 4-bit 27B via MLX), and ToT doubles its per-case time.

## History of the ToT runs (30 cases)

| | Opus ToT v4 | Opus ToT v5 | Opus ToT v6 | Qwen ToT v4 | Qwen ToT v6 |
|---|---|---|---|---|---|
| correct | 16 | 15 | 15 | 17 | 16 |
| wrong service | 0 | 0 | 0 | 1 | 0 |
| escalated (ties w/ truth) | 14 (8) | 15 (8) | 15 (8) | 12 (8) | 14 (9) |

v4: original ToT. v5: ceiling rule, pre-5.1 code (Opus only; the Qwen v5 run was discarded
because the code was edited mid-run). v6: ceiling rule on the finished 5.1 code. v5 and v6
Opus agree except for two escalated cases, which escalate differently (run-to-run variation).

## Operations (local model)

The MLX server crashed twice during the first Qwen ToT run (`[metal::malloc] Resource limit
(499000) exceeded`). The cause was the client, not the model: replies longer than the OpenAI
SDK's 600 s default timed out and were retried while the server was still generating, so
duplicate requests piled up. Fixed by `IC_REQUEST_TIMEOUT=3600` and `IC_MAX_RETRIES=0` (read in
`model.py`, set in `scripts/qwen_ab.sh`), plus a fresh server per case. The v6 run had 0 retries
and 0 crashes. `scripts/qwen_ab.sh` also fingerprints `src/` and stops if the code changes
mid-run.

## Files

- Opus: `evals/results/2026-11-05_opus-5-5_all_v4_linear/`, `..._all_v4_tot-model/` (90 cases;
  read-only; backup `evals/archive/2026-11-05_opus-5-5_ab_v4.tgz`),
  `2026-11-07_opus-5-5_r1_v5_tot-model/`, `2026-11-08_opus-5-5_r1_v6_tot-model/`
- Qwen: `evals/results/2026-11-06_qwen3.6-27b_r1_v4_linear/`, `..._r1_v4_tot-model/`,
  `2026-11-08_qwen3.6-27b_r1_v6_tot-model/`
- `scripts/qwen_ab.sh` (re-runs or resumes a Qwen pair; `LINEAR=skip TOT=<folder>` for ToT only)
