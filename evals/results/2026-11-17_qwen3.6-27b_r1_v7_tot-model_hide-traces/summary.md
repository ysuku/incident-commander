# Eval summary: 2026-11-17_qwen3.6-27b_r1_v7_tot-model_hide-traces

30 cases run, 30 scored (0 errors).

| Outcome | Count | Share of scored |
|---|---|---|
| correct | 0 | 0% |
| service_only | 0 | 0% |
| wrong | 0 | 0% |
| escalated | 30 | 100% |
| error | 0 | - |

Total cost $0.00 ($0.000/case); wall time 1088 min (2176s/case).

## By fault type

| Fault | n | correct | service_only | wrong | escalated |
|---|---|---|---|---|---|
| cpu | 5 | 0 | 0 | 0 | 5 |
| delay | 5 | 0 | 0 | 0 | 5 |
| disk | 5 | 0 | 0 | 0 | 5 |
| loss | 5 | 0 | 0 | 0 | 5 |
| mem | 5 | 0 | 0 | 0 | 5 |
| socket | 5 | 0 | 0 | 0 | 5 |

## Tree-of-Thought

ToT ran in 30 of 30 cases.

| ToT result | n |
|---|---|
| escalated_truth_first | 21 |
| escalated | 9 |

## 6.1 metrics

- correct rate: 0 of 30 (0%)
- wrong-service (diagnosed, wrong service): 0 of 30 (0%)
- wrong-fault (right service, wrong fault): 0 of 30 (0%)
- escalation rate: 30 of 30 (100%)
- false unfamiliar (a known fault type handed over as 'unfamiliar'): 0 of 30 (0%)
- telemetry hidden (robustness ablation): traces in 30 of 30 cases
- escalations by code: tot_no_branch 30
- escalation quality (ToT branches, exact; 30 of 30 escalations): top_live_is_truth 21, service_only 8, none 1
- groundedness: no Fix Advisor rows in this run
- cost per case: $0.000
- latency per case: mean 2176.1s, median 2218.4s, p95 2834.5s (nearest rank)
