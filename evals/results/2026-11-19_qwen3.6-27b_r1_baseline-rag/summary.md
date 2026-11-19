# Eval summary: 2026-11-19_qwen3.6-27b_r1_baseline-rag

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 7 | 23% |
| service_only | 21 | 70% |
| wrong | 2 | 7% |
| escalated | 0 | 0% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 113 min (226s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 2 | 3 | 0 | 0 |
| delay | 5 | 0 | 4 | 1 | 0 |
| disk | 5 | 0 | 5 | 0 | 0 |
| loss | 5 | 0 | 4 | 1 | 0 |
| mem | 5 | 3 | 2 | 0 | 0 |
| socket | 5 | 2 | 3 | 0 | 0 |

## 6.1 metrics

- correct rate: 7 of 30 (23%)
- wrong-service (diagnosed, wrong service): 2 of 30 (7%)
- wrong-fault (right service, wrong fault): 21 of 30 (70%)
- escalation rate: 0 of 30 (0%)
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.000
- latency per case: mean 226.5s, median 218.8s, p95 294.0s (nearest rank)
