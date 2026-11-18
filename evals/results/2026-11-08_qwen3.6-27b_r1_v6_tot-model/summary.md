# Eval summary: 2026-11-08_qwen3.6-27b_r1_v6_tot-model

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 16 | 53% |
| service_only | 0 | 0% |
| wrong | 0 | 0% |
| escalated | 14 | 47% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 681 min (1361s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 4 | 0 | 0 | 1 |
| delay | 5 | 4 | 0 | 0 | 1 |
| disk | 5 | 1 | 0 | 0 | 4 |
| loss | 5 | 3 | 0 | 0 | 2 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 4 | 0 | 0 | 1 |

## Tree-of-Thought

ToT ran in 22 of 30 cases.

| ToT result | n |
|---|---|
| tie_with_truth | 9 |
| selected | 8 |
| escalated | 2 |
| escalated_truth_first | 2 |
| tie_without_truth | 1 |
