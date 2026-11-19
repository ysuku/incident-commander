# Eval summary: 2026-11-19_sonnet-5-5_r1_v8_linear

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 7 | 23% |
| service_only | 10 | 33% |
| wrong | 0 | 0% |
| escalated | 13 | 43% |
| error | 0 | - |

Total cost $0.80 ($0.027/case); wall time 9 min (17s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 2 | 1 | 0 | 2 |
| delay | 5 | 2 | 0 | 0 | 3 |
| disk | 5 | 0 | 2 | 0 | 3 |
| loss | 5 | 0 | 5 | 0 | 0 |
| mem | 5 | 1 | 2 | 0 | 2 |
| socket | 5 | 2 | 0 | 0 | 3 |

## 6.1 metrics

- correct rate: 7 of 30 (23%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 10 of 30 (33%)
- escalation rate: 13 of 30 (43%)
- escalations by code: budget_exhausted 13
- escalation quality (model's candidates, fault match approximate; 13 of 13 escalations): service_only 7, top_live_is_truth 6
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.027
- latency per case: mean 17.4s, median 16.8s, p95 29.7s (nearest rank)
