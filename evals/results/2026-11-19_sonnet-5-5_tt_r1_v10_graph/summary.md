# Eval summary: 2026-11-19_sonnet-5-5_tt_r1_v10_graph

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 0 | 0% |
| service_only | 6 | 20% |
| wrong | 0 | 0% |
| escalated | 24 | 80% |
| error | 0 | - |

Total cost $1.17 ($0.039/case); wall time 14 min (29s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 0 | 0 | 0 | 5 |
| delay | 5 | 0 | 4 | 0 | 1 |
| disk | 5 | 0 | 0 | 0 | 5 |
| loss | 5 | 0 | 0 | 0 | 5 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 0 | 2 | 0 | 3 |

## Tree-of-Thought

ToT ran in 27 of 30 cases.

| ToT result | n |
|---|---|
| escalated | 13 |
| escalated_truth_first | 10 |
| selected | 4 |

## 6.1 metrics

- correct rate: 0 of 30 (0%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 6 of 30 (20%)
- escalation rate: 24 of 30 (80%)
- false unfamiliar (a known fault type handed over as 'unfamiliar'): 0 of 30 (0%)
- escalations by code: tot_no_branch 23, budget_exhausted 1
- escalation quality (ToT branches, exact; 23 of 24 escalations): top_live_is_truth 10, service_only 7, none 6
- escalation quality (model's candidates, fault match approximate; 1 of 24 escalations): top_live_is_truth 1
- groundedness: 6 briefs advised; dropped references 0 (0.00 per brief); runbook contradiction flags 4 (0.67 per brief)
- cost per case: $0.039
- latency per case: mean 28.9s, median 27.9s, p95 38.0s (nearest rank)
