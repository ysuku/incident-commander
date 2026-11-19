# Eval summary: 2026-11-17_qwen3.6-27b_r1_v7_graph

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 13 | 43% |
| service_only | 0 | 0% |
| wrong | 0 | 0% |
| escalated | 17 | 57% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 755 min (1509s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 3 | 0 | 0 | 2 |
| delay | 5 | 5 | 0 | 0 | 0 |
| disk | 5 | 0 | 0 | 0 | 5 |
| loss | 5 | 3 | 0 | 0 | 2 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 2 | 0 | 0 | 3 |

## Tree-of-Thought

ToT ran in 22 of 30 cases.

| ToT result | n |
|---|---|
| tie_with_truth | 9 |
| escalated_truth_first | 6 |
| selected | 5 |
| tie_without_truth | 1 |
| escalated | 1 |

## 6.1 metrics

- correct rate: 13 of 30 (43%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 17 of 30 (57%)
- false unfamiliar (a known fault type handed over as 'unfamiliar'): 0 of 30 (0%)
- escalations by code: tot_tie 10, tot_no_branch 7
- escalation quality (ToT branches, exact; 17 of 17 escalations): tie_with_truth 9, top_live_is_truth 6, tie_without_truth 1, service_only 1
- groundedness: 13 briefs advised; dropped references 4 (0.31 per brief); runbook contradiction flags 29 (2.23 per brief)
- cost per case: $0.000
- latency per case: mean 1509.5s, median 1262.8s, p95 2962.4s (nearest rank)
