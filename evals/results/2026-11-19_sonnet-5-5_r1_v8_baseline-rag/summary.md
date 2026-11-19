# Eval summary: 2026-11-19_sonnet-5-5_r1_v8_baseline-rag

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 15 | 50% |
| service_only | 13 | 43% |
| wrong | 1 | 3% |
| escalated | 1 | 3% |
| error | 0 | - |

Total cost $0.21 ($0.007/case); wall time 2 min (4s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 4 | 1 | 0 | 0 |
| delay | 5 | 4 | 0 | 0 | 1 |
| disk | 5 | 2 | 3 | 0 | 0 |
| loss | 5 | 0 | 4 | 1 | 0 |
| mem | 5 | 3 | 2 | 0 | 0 |
| socket | 5 | 2 | 3 | 0 | 0 |

## 6.1 metrics

- correct rate: 15 of 30 (50%)
- wrong-service (diagnosed, wrong service): 1 of 30 (3%)
- wrong-fault (right service, wrong fault): 13 of 30 (43%)
- escalation rate: 1 of 30 (3%)
- escalations by code: other 1
- escalation quality (model's candidates, fault match approximate; 1 of 1 escalations): service_only 1
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.007
- latency per case: mean 4.4s, median 4.3s, p95 5.6s (nearest rank)
