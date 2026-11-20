# Eval summary: 2026-11-19_sonnet-5-5_re3_v10_baseline-plain

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 0 | 0% |
| correct_unfamiliar | 1 | 3% |
| service_only | 0 | 0% |
| wrong | 29 | 97% |
| escalated | 0 | 0% |
| error | 0 | - |

Total cost $0.28 ($0.009/case); wall time 2 min (4s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated | correct_unfamiliar |
|---|---|---|---|---|---|---|
| f1 | 9 | 0 | 0 | 9 | 0 | 0 |
| f2 | 3 | 0 | 0 | 3 | 0 | 0 |
| f3 | 6 | 0 | 0 | 6 | 0 | 0 |
| f4 | 6 | 0 | 0 | 5 | 0 | 1 |
| f5 | 6 | 0 | 0 | 6 | 0 | 0 |

## 6.1 metrics

- correct rate: 0 of 30 (0%)
- wrong-service (diagnosed, wrong service): 29 of 30 (97%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 0 of 30 (0%)
- unfamiliar-fault cases (labeled fault outside the six known types): 30 of 30 (100%); the rates above do not apply to them
- unfamiliar handled correctly (handed over with the true service as the lead hypothesis): 1 of 30 (3%)
- escalated with the code 'unfamiliar': 0 of 30; about the true service: 0
- unfamiliar, other escalation: 0 of 30 (0%)
- unfamiliar diagnosed as a known fault type (confident and wrong): 29 of 30 (97%)
- escalations by code: other 1
- escalation quality (model's candidates, fault match approximate; 1 of 1 escalations): service_only 1
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.009
- latency per case: mean 4.2s, median 4.0s, p95 6.0s (nearest rank)
