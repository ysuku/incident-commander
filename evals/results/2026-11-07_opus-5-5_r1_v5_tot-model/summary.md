# Eval summary: 2026-11-07_opus-5-5_r1_v5_tot-model

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 15 | 50% |
| service_only | 0 | 0% |
| wrong | 0 | 0% |
| escalated | 15 | 50% |
| error | 0 | - |

Total cost $2.74 ($0.091/case); wall time 18 min (35s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 4 | 0 | 0 | 1 |
| delay | 5 | 5 | 0 | 0 | 0 |
| disk | 5 | 0 | 0 | 0 | 5 |
| loss | 5 | 4 | 0 | 0 | 1 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 2 | 0 | 0 | 3 |

## Tree-of-Thought

ToT ran in 14 of 30 cases.

| ToT result | n |
|---|---|
| tie_with_truth | 8 |
| selected | 4 |
| escalated_truth_first | 1 |
| tie_without_truth | 1 |
