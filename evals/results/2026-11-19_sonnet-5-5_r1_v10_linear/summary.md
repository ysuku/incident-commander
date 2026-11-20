# Eval summary: 2026-11-19_sonnet-5-5_r1_v10_linear

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 6 | 20% |
| service_only | 12 | 40% |
| wrong | 0 | 0% |
| escalated | 12 | 40% |
| error | 0 | - |

Total cost $0.76 ($0.025/case); wall time 8 min (16s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 2 | 1 | 0 | 2 |
| delay | 5 | 2 | 0 | 0 | 3 |
| disk | 5 | 0 | 3 | 0 | 2 |
| loss | 5 | 0 | 5 | 0 | 0 |
| mem | 5 | 1 | 2 | 0 | 2 |
| socket | 5 | 1 | 1 | 0 | 3 |

## 6.1 metrics

- correct rate: 6 of 30 (20%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 12 of 30 (40%)
- escalation rate: 12 of 30 (40%)
- escalations by code: budget_exhausted 12
- escalation quality (model's candidates, fault match approximate; 12 of 12 escalations): top_live_is_truth 8, service_only 4
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.025
- latency per case: mean 16.0s, median 17.0s, p95 20.6s (nearest rank)
