# Eval summary: 2026-11-19_sonnet-5-5_r1_v8_baseline-plain

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 14 | 47% |
| service_only | 14 | 47% |
| wrong | 2 | 7% |
| escalated | 0 | 0% |
| error | 0 | - |

Total cost $0.18 ($0.006/case); wall time 2 min (4s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 4 | 1 | 0 | 0 |
| delay | 5 | 4 | 0 | 1 | 0 |
| disk | 5 | 2 | 3 | 0 | 0 |
| loss | 5 | 0 | 4 | 1 | 0 |
| mem | 5 | 2 | 3 | 0 | 0 |
| socket | 5 | 2 | 3 | 0 | 0 |

## 6.1 metrics

- correct rate: 14 of 30 (47%)
- wrong-service (diagnosed, wrong service): 2 of 30 (7%)
- wrong-fault (right service, wrong fault): 14 of 30 (47%)
- escalation rate: 0 of 30 (0%)
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.006
- latency per case: mean 4.2s, median 4.2s, p95 4.9s (nearest rank)
