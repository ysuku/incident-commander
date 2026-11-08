# Eval summary: 2026-11-06_qwen3.6-27b_r1_v4_tot-model

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 17 | 57% |
| service_only | 0 | 0% |
| wrong | 1 | 3% |
| escalated | 12 | 40% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 668 min (1336s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 4 | 0 | 0 | 1 |
| delay | 5 | 4 | 0 | 0 | 1 |
| disk | 5 | 1 | 0 | 0 | 4 |
| loss | 5 | 4 | 0 | 0 | 1 |
| mem | 5 | 0 | 0 | 1 | 4 |
| socket | 5 | 4 | 0 | 0 | 1 |

## Tree-of-Thought

ToT ran in 22 of 30 cases.

| ToT result | n |
|---|---|
| selected | 10 |
| tie_with_truth | 8 |
| escalated_truth_first | 2 |
| escalated | 1 |
| tie_without_truth | 1 |
