# Offline ToT check (heuristic generator, suspect = truth)

30 cases, no model calls. Outcome counts:

| Outcome | n |
|---|---|
| correct_unfamiliar | 3 |
| wrong | 6 |
| escalated | 21 |

Truth branch pruned: 0; mean expansions 1.0

False unfamiliar (a known fault type called 'unfamiliar'): 0 of 0 known-fault cases

## By fault type

| Fault | correct | correct_unfamiliar | service_only | wrong | tie_with_truth | tie_without_truth | escalated_truth_first | escalated |
|---|---|---|---|---|---|---|---|---|
| f1 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 7 |
| f2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| f3 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 4 |
| f4 | 0 | 3 | 0 | 2 | 0 | 0 | 0 | 1 |
| f5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 |

## Cases

| Case | Suspect | Outcome | Branches (score, status) |
|---|---|---|---|
| adservice_f3/1 | adservice | wrong | cpu=5(s); mem=4(o); disk=4(o) |
| adservice_f3/2 | adservice | escalated | socket=1(o); mem=1(o); disk=1(o) |
| adservice_f3/3 | adservice | wrong | loss=6(s); mem=4(p); socket=0(p) |
| adservice_f4/1 | adservice | correct_unfamiliar | disk=-1(o) |
| adservice_f4/2 | adservice | correct_unfamiliar | disk=-1(o) |
| adservice_f4/3 | adservice | correct_unfamiliar | disk=-1(o) |
| adservice_f5/1 | adservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| adservice_f5/2 | adservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| adservice_f5/3 | adservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| cartservice_f1/1 | cartservice | wrong | socket=1(s); disk=-1(p) |
| cartservice_f1/2 | cartservice | wrong | cpu=5(s); disk=4(o); socket=0(p) |
| cartservice_f1/3 | cartservice | escalated | socket=1(o); mem=1(o); disk=1(o) |
| currencyservice_f1/1 | currencyservice | escalated | socket=1(o); mem=1(o); disk=1(o) |
| currencyservice_f1/2 | currencyservice | escalated | socket=1(o); mem=1(o); disk=1(o) |
| currencyservice_f1/3 | currencyservice | escalated | socket=1(o); mem=1(o); disk=1(o) |
| emailservice_f1/1 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f1/2 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f1/3 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f2/1 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f2/2 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f2/3 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f3/1 | emailservice | escalated | mem=1(o); disk=1(o) |
| emailservice_f3/2 | emailservice | escalated | mem=1(o); disk=1(o) |
| emailservice_f3/3 | emailservice | escalated | mem=1(o); disk=1(o) |
| emailservice_f4/1 | emailservice | wrong | cpu=5(s); disk=2(p) |
| emailservice_f4/2 | emailservice | escalated | disk=-1(o) |
| emailservice_f4/3 | emailservice | wrong | cpu=5(s); disk=2(p) |
| emailservice_f5/1 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f5/2 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
| emailservice_f5/3 | emailservice | escalated | loss=3(o); delay=2(o); disk=-3(p) |
