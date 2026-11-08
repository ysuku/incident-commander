# Eval summary: 2026-11-05_opus-5-5_all_v4_tot-model

90 cases run, 90 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 38 | 42% |
| service_only | 2 | 2% |
| wrong | 0 | 0% |
| escalated | 50 | 56% |
| error | 0 | - |

Total cost $8.25 ($0.092/case); wall time 56 min (38s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 15 | 10 | 0 | 0 | 5 |
| delay | 15 | 15 | 0 | 0 | 0 |
| disk | 15 | 0 | 0 | 0 | 15 |
| loss | 15 | 8 | 2 | 0 | 5 |
| mem | 15 | 0 | 0 | 0 | 15 |
| socket | 15 | 5 | 0 | 0 | 10 |

## Tree-of-Thought

ToT ran in 44 of 90 cases.

| ToT result | n |
|---|---|
| tie_with_truth | 24 |
| selected | 16 |
| tie_without_truth | 2 |
| escalated | 2 |
