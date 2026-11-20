# Eval summary: 2026-11-19_sonnet-5-5_re3_v10_graph

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 0 | 0% |
| correct_unfamiliar | 24 | 80% |
| service_only | 0 | 0% |
| wrong | 3 | 10% |
| escalated | 3 | 10% |
| error | 0 | - |

Total cost $1.19 ($0.040/case); wall time 12 min (23s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated | correct_unfamiliar |
|---|---|---|---|---|---|---|
| f1 | 9 | 0 | 0 | 2 | 2 | 5 |
| f2 | 3 | 0 | 0 | 0 | 0 | 3 |
| f3 | 6 | 0 | 0 | 1 | 0 | 5 |
| f4 | 6 | 0 | 0 | 0 | 1 | 5 |
| f5 | 6 | 0 | 0 | 0 | 0 | 6 |

## Tree-of-Thought

ToT ran in 15 of 30 cases.

| ToT result | n |
|---|---|
| escalated | 14 |
| selected | 1 |

## 6.1 metrics

- correct rate: 0 of 30 (0%)
- wrong-service (diagnosed, wrong service): 3 of 30 (10%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 3 of 30 (10%)
- unfamiliar-fault cases (labeled fault outside the six known types): 30 of 30 (100%); the rates above do not apply to them
- unfamiliar handled correctly (handed over with the true service as the lead hypothesis): 24 of 30 (80%)
- escalated with the code 'unfamiliar': 1 of 30; about the true service: 0
- unfamiliar, other escalation: 3 of 30 (10%)
- unfamiliar diagnosed as a known fault type (confident and wrong): 3 of 30 (10%)
- escalations by code: budget_exhausted 13, tot_no_branch 13, unfamiliar 1
- escalation quality (ToT branches, exact; 14 of 27 escalations): service_only 11, none 3
- escalation quality (model's candidates, fault match approximate; 13 of 27 escalations): service_only 13
- groundedness: 3 briefs advised; dropped references 0 (0.00 per brief); runbook contradiction flags 1 (0.33 per brief)
- cost per case: $0.040
- latency per case: mean 23.1s, median 19.7s, p95 34.7s (nearest rank)
