# Eval summary: 2026-11-19_qwen3.6-27b_r1_baseline-plain

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 14 | 47% |
| service_only | 15 | 50% |
| wrong | 1 | 3% |
| escalated | 0 | 0% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 111 min (221s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 4 | 1 | 0 | 0 |
| delay | 5 | 4 | 0 | 1 | 0 |
| disk | 5 | 1 | 4 | 0 | 0 |
| loss | 5 | 0 | 5 | 0 | 0 |
| mem | 5 | 3 | 2 | 0 | 0 |
| socket | 5 | 2 | 3 | 0 | 0 |

## 6.1 metrics

- correct rate: 14 of 30 (47%)
- wrong-service (diagnosed, wrong service): 1 of 30 (3%)
- wrong-fault (right service, wrong fault): 15 of 30 (50%)
- escalation rate: 0 of 30 (0%)
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.000
- latency per case: mean 221.4s, median 209.4s, p95 314.1s (nearest rank)
