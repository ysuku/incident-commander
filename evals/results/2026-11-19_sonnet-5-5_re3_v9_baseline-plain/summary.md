# Eval summary: 2026-11-19_sonnet-5-5_re3_v9_baseline-plain

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 0 | 0% |
| correct_unfamiliar | 2 | 7% |
| service_only | 0 | 0% |
| wrong | 28 | 93% |
| escalated | 0 | 0% |
| error | 0 | - |

Total cost $0.19 ($0.006/case); wall time 2 min (4s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated | correct_unfamiliar |
|---|---|---|---|---|---|---|
| f1 | 9 | 0 | 0 | 9 | 0 | 0 |
| f2 | 3 | 0 | 0 | 3 | 0 | 0 |
| f3 | 6 | 0 | 0 | 6 | 0 | 0 |
| f4 | 6 | 0 | 0 | 4 | 0 | 2 |
| f5 | 6 | 0 | 0 | 6 | 0 | 0 |

## 6.1 metrics

- correct rate: 0 of 30 (0%)
- wrong-service (diagnosed, wrong service): 28 of 30 (93%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 0 of 30 (0%)
- unfamiliar-fault cases (labeled fault outside the six known types): 30 of 30 (100%); the rates above do not apply to them
- unfamiliar handled correctly (handed over with the true service as the lead hypothesis): 2 of 30 (7%)
- escalated with the code 'unfamiliar': 0 of 30; about the true service: 0
- unfamiliar, other escalation: 0 of 30 (0%)
- unfamiliar diagnosed as a known fault type (confident and wrong): 28 of 30 (93%)
- escalations by code: other 2
- escalation quality (model's candidates, fault match approximate; 2 of 2 escalations): service_only 2
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.006
- latency per case: mean 4.1s, median 3.7s, p95 6.6s (nearest rank)
