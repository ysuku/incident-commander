# Eval summary: 2026-11-19_sonnet-5-5_r1_v8_graph

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 10 | 33% |
| service_only | 0 | 0% |
| wrong | 0 | 0% |
| escalated | 20 | 67% |
| error | 0 | - |

Total cost $1.25 ($0.042/case); wall time 12 min (23s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 3 | 0 | 0 | 2 |
| delay | 5 | 3 | 0 | 0 | 2 |
| disk | 5 | 0 | 0 | 0 | 5 |
| loss | 5 | 2 | 0 | 0 | 3 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 2 | 0 | 0 | 3 |

## Tree-of-Thought

ToT ran in 18 of 30 cases.

| ToT result | n |
|---|---|
| escalated_truth_first | 7 |
| tie_with_truth | 6 |
| selected | 3 |
| tie_without_truth | 1 |
| escalated | 1 |

## 6.1 metrics

- correct rate: 10 of 30 (33%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 20 of 30 (67%)
- false unfamiliar (a known fault type handed over as 'unfamiliar'): 0 of 30 (0%)
- escalations by code: tot_no_branch 8, tot_tie 7, budget_exhausted 5
- escalation quality (ToT branches, exact; 15 of 20 escalations): top_live_is_truth 7, tie_with_truth 6, tie_without_truth 1, service_only 1
- escalation quality (model's candidates, fault match approximate; 5 of 20 escalations): service_only 3, top_live_is_truth 2
- groundedness: 10 briefs advised; dropped references 0 (0.00 per brief); runbook contradiction flags 13 (1.30 per brief)
- cost per case: $0.042
- latency per case: mean 23.3s, median 22.3s, p95 35.0s (nearest rank)
