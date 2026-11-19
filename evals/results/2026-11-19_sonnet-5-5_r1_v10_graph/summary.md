# Eval summary: 2026-11-19_sonnet-5-5_r1_v10_graph

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 9 | 30% |
| service_only | 0 | 0% |
| wrong | 0 | 0% |
| escalated | 21 | 70% |
| error | 0 | - |

Total cost $1.29 ($0.043/case); wall time 13 min (25s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 2 | 0 | 0 | 3 |
| delay | 5 | 3 | 0 | 0 | 2 |
| disk | 5 | 0 | 0 | 0 | 5 |
| loss | 5 | 2 | 0 | 0 | 3 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 2 | 0 | 0 | 3 |

## Tree-of-Thought

ToT ran in 17 of 30 cases.

| ToT result | n |
|---|---|
| escalated_truth_first | 6 |
| tie_with_truth | 6 |
| selected | 3 |
| tie_without_truth | 1 |
| escalated | 1 |

## 6.1 metrics

- correct rate: 9 of 30 (30%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 21 of 30 (70%)
- false unfamiliar (a known fault type handed over as 'unfamiliar'): 0 of 30 (0%)
- escalations by code: tot_no_branch 7, budget_exhausted 7, tot_tie 7
- escalation quality (ToT branches, exact; 14 of 21 escalations): top_live_is_truth 6, tie_with_truth 6, tie_without_truth 1, service_only 1
- escalation quality (model's candidates, fault match approximate; 7 of 21 escalations): service_only 4, top_live_is_truth 3
- groundedness: 9 briefs advised; dropped references 0 (0.00 per brief); runbook contradiction flags 9 (1.00 per brief)
- cost per case: $0.043
- latency per case: mean 25.3s, median 26.1s, p95 38.5s (nearest rank)
