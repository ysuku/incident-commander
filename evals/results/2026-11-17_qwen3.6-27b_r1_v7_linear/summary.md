# Eval summary: 2026-11-17_qwen3.6-27b_r1_v7_linear

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 12 | 40% |
| service_only | 10 | 33% |
| wrong | 1 | 3% |
| escalated | 7 | 23% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 365 min (731s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 2 | 1 | 0 | 2 |
| delay | 5 | 4 | 1 | 0 | 0 |
| disk | 5 | 2 | 1 | 0 | 2 |
| loss | 5 | 0 | 4 | 1 | 0 |
| mem | 5 | 2 | 3 | 0 | 0 |
| socket | 5 | 2 | 0 | 0 | 3 |

## 6.1 metrics

- correct rate: 12 of 30 (40%)
- wrong-service (diagnosed, wrong service): 1 of 30 (3%)
- wrong-fault (right service, wrong fault): 10 of 30 (33%)
- escalation rate: 7 of 30 (23%)
- escalations by code: budget_exhausted 7
- escalation quality (model's candidates, fault match approximate; 7 of 7 escalations): service_only 4, top_live_is_truth 3
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.000
- latency per case: mean 730.7s, median 630.9s, p95 1443.8s (nearest rank)
