# Eval summary: 2026-11-19_sonnet-5-5_re3_v9_graph

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 0 | 0% |
| correct_unfamiliar | 22 | 73% |
| service_only | 0 | 0% |
| wrong | 4 | 13% |
| escalated | 4 | 13% |
| error | 0 | - |

Total cost $1.27 ($0.042/case); wall time 21 min (43s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated | correct_unfamiliar |
|---|---|---|---|---|---|---|
| f1 | 9 | 0 | 0 | 1 | 1 | 7 |
| f2 | 3 | 0 | 0 | 0 | 0 | 3 |
| f3 | 6 | 0 | 0 | 2 | 0 | 4 |
| f4 | 6 | 0 | 0 | 1 | 3 | 2 |
| f5 | 6 | 0 | 0 | 0 | 0 | 6 |

## Tree-of-Thought

ToT ran in 17 of 30 cases.

| ToT result | n |
|---|---|
| escalated | 16 |
| selected | 1 |

## 6.1 metrics

- correct rate: 0 of 30 (0%)
- wrong-service (diagnosed, wrong service): 4 of 30 (13%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 4 of 30 (13%)
- unfamiliar-fault cases (labeled fault outside the six known types): 30 of 30 (100%); the rates above do not apply to them
- unfamiliar handled correctly (handed over with the true service as the lead hypothesis): 22 of 30 (73%)
- escalated with the code 'unfamiliar': 1 of 30; about the true service: 0
- unfamiliar, other escalation: 4 of 30 (13%)
- unfamiliar diagnosed as a known fault type (confident and wrong): 4 of 30 (13%)
- escalations by code: tot_no_branch 15, budget_exhausted 10, unfamiliar 1
- escalation quality (ToT branches, exact; 16 of 26 escalations): service_only 14, none 2
- escalation quality (model's candidates, fault match approximate; 10 of 26 escalations): service_only 10
- groundedness: 4 briefs advised; dropped references 0 (0.00 per brief); runbook contradiction flags 1 (0.25 per brief)
- cost per case: $0.042
- latency per case: mean 42.7s, median 21.1s, p95 37.2s (nearest rank)
