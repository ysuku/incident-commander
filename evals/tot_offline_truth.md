# Offline ToT check (heuristic generator, suspect = truth)

90 cases, no model calls. Outcome counts:

| Outcome | n |
|---|---|
| correct | 36 |
| service_only | 2 |
| tie_with_truth | 22 |
| tie_without_truth | 2 |
| escalated_truth_first | 21 |
| escalated | 7 |

Truth branch pruned: 3; mean expansions 1.0

## By fault type

| Fault | correct | service_only | wrong | tie_with_truth | tie_without_truth | escalated_truth_first | escalated |
|---|---|---|---|---|---|---|---|
| cpu | 7 | 0 | 0 | 0 | 0 | 8 | 0 |
| delay | 15 | 0 | 0 | 0 | 0 | 0 | 0 |
| disk | 0 | 0 | 0 | 8 | 1 | 1 | 5 |
| loss | 9 | 2 | 0 | 0 | 1 | 1 | 2 |
| mem | 0 | 0 | 0 | 14 | 0 | 1 | 0 |
| socket | 5 | 0 | 0 | 0 | 0 | 10 | 0 |

## Cases

| Case | Suspect | Outcome | Branches (score, status) |
|---|---|---|---|
| checkoutservice_cpu/1 | checkoutservice | escalated_truth_first | cpu=3(o); disk=2(o); socket=0(p) |
| checkoutservice_cpu/2 | checkoutservice | escalated_truth_first | cpu=3(o); mem=2(o); disk=2(o) |
| checkoutservice_cpu/3 | checkoutservice | escalated_truth_first | cpu=3(o); mem=2(o); disk=2(o) |
| checkoutservice_delay/1 | checkoutservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| checkoutservice_delay/2 | checkoutservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| checkoutservice_delay/3 | checkoutservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| checkoutservice_disk/1 | checkoutservice | escalated_truth_first | disk=3(o); cpu=0(p); mem=0(p) |
| checkoutservice_disk/2 | checkoutservice | escalated | mem=3(o); disk=3(o); cpu=0(p) |
| checkoutservice_disk/3 | checkoutservice | escalated | mem=3(o); disk=3(o); cpu=0(p) |
| checkoutservice_loss/1 | checkoutservice | correct | loss=6(s); mem=4(p); socket=0(p) |
| checkoutservice_loss/2 | checkoutservice | escalated | delay=2(o); loss=0(p); disk=-3(p) |
| checkoutservice_loss/3 | checkoutservice | escalated_truth_first | loss=3(o); mem=2(o); socket=0(p) |
| checkoutservice_mem/1 | checkoutservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| checkoutservice_mem/2 | checkoutservice | escalated_truth_first | mem=3(o); disk=3(o); cpu=0(p) |
| checkoutservice_mem/3 | checkoutservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| checkoutservice_socket/1 | checkoutservice | escalated_truth_first | socket=1(o); mem=0(o); disk=0(o) |
| checkoutservice_socket/2 | checkoutservice | escalated_truth_first | socket=1(o); cpu=0(o); disk=-1(p) |
| checkoutservice_socket/3 | checkoutservice | escalated_truth_first | socket=1(o); cpu=0(o); loss=0(o) |
| currencyservice_cpu/1 | currencyservice | correct | cpu=5(s); disk=2(p); socket=0(p) |
| currencyservice_cpu/2 | currencyservice | correct | cpu=5(s); disk=2(p); socket=0(p) |
| currencyservice_cpu/3 | currencyservice | correct | cpu=5(s); disk=2(p); socket=0(p) |
| currencyservice_delay/1 | currencyservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| currencyservice_delay/2 | currencyservice | correct | delay=5(s); mem=4(o); loss=2(p) |
| currencyservice_delay/3 | currencyservice | correct | delay=5(s); mem=4(o); loss=2(p) |
| currencyservice_disk/1 | currencyservice | tie_without_truth | mem=5(t); loss=5(t); disk=2(p) |
| currencyservice_disk/2 | currencyservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| currencyservice_disk/3 | currencyservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| currencyservice_loss/1 | currencyservice | correct | loss=6(s); socket=3(p); mem=2(p) |
| currencyservice_loss/2 | currencyservice | service_only | cpu=5(s); disk=2(p); socket=0(p) |
| currencyservice_loss/3 | currencyservice | service_only | delay=5(s); loss=2(p); disk=-3(p) |
| currencyservice_mem/1 | currencyservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| currencyservice_mem/2 | currencyservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| currencyservice_mem/3 | currencyservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| currencyservice_socket/1 | currencyservice | escalated_truth_first | socket=1(o); disk=-6(p) |
| currencyservice_socket/2 | currencyservice | correct | socket=3(s); cpu=2(o); loss=2(o) |
| currencyservice_socket/3 | currencyservice | correct | socket=3(s); cpu=2(o); loss=2(o) |
| emailservice_cpu/1 | emailservice | correct | cpu=5(s); disk=2(p); loss=-3(p) |
| emailservice_cpu/2 | emailservice | escalated_truth_first | cpu=3(o); disk=0(p); loss=-3(p) |
| emailservice_cpu/3 | emailservice | escalated_truth_first | cpu=3(o); disk=0(p); loss=-3(p) |
| emailservice_delay/1 | emailservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| emailservice_delay/2 | emailservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| emailservice_delay/3 | emailservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| emailservice_disk/1 | emailservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| emailservice_disk/2 | emailservice | escalated | mem=3(o); disk=3(o); cpu=0(p) |
| emailservice_disk/3 | emailservice | escalated | mem=3(o); disk=3(o); cpu=0(p) |
| emailservice_loss/1 | emailservice | correct | loss=6(s); mem=5(o); disk=2(p) |
| emailservice_loss/2 | emailservice | tie_without_truth | mem=5(t); disk=5(t); cpu=2(p) |
| emailservice_loss/3 | emailservice | escalated | mem=3(o); disk=3(o); cpu=0(p) |
| emailservice_mem/1 | emailservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| emailservice_mem/2 | emailservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| emailservice_mem/3 | emailservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| emailservice_socket/1 | emailservice | escalated_truth_first | socket=1(o); cpu=0(o); loss=0(o) |
| emailservice_socket/2 | emailservice | escalated_truth_first | socket=1(o); cpu=0(o); loss=0(o) |
| emailservice_socket/3 | emailservice | escalated_truth_first | socket=1(o); cpu=0(o); loss=0(o) |
| productcatalogservice_cpu/1 | productcatalogservice | escalated_truth_first | cpu=3(o); mem=2(o); disk=2(o) |
| productcatalogservice_cpu/2 | productcatalogservice | escalated_truth_first | cpu=3(o); mem=2(o); disk=2(o) |
| productcatalogservice_cpu/3 | productcatalogservice | escalated_truth_first | cpu=3(o); mem=2(o); disk=2(o) |
| productcatalogservice_delay/1 | productcatalogservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| productcatalogservice_delay/2 | productcatalogservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| productcatalogservice_delay/3 | productcatalogservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| productcatalogservice_disk/1 | productcatalogservice | escalated | mem=3(o); disk=3(o); cpu=0(p) |
| productcatalogservice_disk/2 | productcatalogservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| productcatalogservice_disk/3 | productcatalogservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| productcatalogservice_loss/1 | productcatalogservice | correct | loss=5(s); mem=4(o); socket=0(p) |
| productcatalogservice_loss/2 | productcatalogservice | correct | loss=5(s); mem=4(o); socket=0(p) |
| productcatalogservice_loss/3 | productcatalogservice | correct | loss=5(s); mem=4(o); socket=0(p) |
| productcatalogservice_mem/1 | productcatalogservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| productcatalogservice_mem/2 | productcatalogservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| productcatalogservice_mem/3 | productcatalogservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| productcatalogservice_socket/1 | productcatalogservice | correct | socket=3(s); mem=2(o); disk=2(o) |
| productcatalogservice_socket/2 | productcatalogservice | escalated_truth_first | socket=1(o); mem=0(o); disk=0(o) |
| productcatalogservice_socket/3 | productcatalogservice | escalated_truth_first | socket=1(o); cpu=0(o); loss=0(o) |
| recommendationservice_cpu/1 | recommendationservice | correct | cpu=5(s); disk=2(p); loss=-3(p) |
| recommendationservice_cpu/2 | recommendationservice | correct | cpu=5(s); disk=2(p); loss=-3(p) |
| recommendationservice_cpu/3 | recommendationservice | correct | cpu=5(s); disk=2(p); loss=-3(p) |
| recommendationservice_delay/1 | recommendationservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| recommendationservice_delay/2 | recommendationservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| recommendationservice_delay/3 | recommendationservice | correct | delay=5(s); loss=2(p); disk=-3(p) |
| recommendationservice_disk/1 | recommendationservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| recommendationservice_disk/2 | recommendationservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| recommendationservice_disk/3 | recommendationservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| recommendationservice_loss/1 | recommendationservice | correct | loss=6(s); socket=3(p); mem=2(p) |
| recommendationservice_loss/2 | recommendationservice | correct | loss=6(s); socket=3(p); mem=2(p) |
| recommendationservice_loss/3 | recommendationservice | correct | loss=6(s); socket=3(p); mem=2(p) |
| recommendationservice_mem/1 | recommendationservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| recommendationservice_mem/2 | recommendationservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| recommendationservice_mem/3 | recommendationservice | tie_with_truth | mem=5(t); disk=5(t); cpu=2(p) |
| recommendationservice_socket/1 | recommendationservice | correct | socket=3(s); cpu=2(o); loss=2(o) |
| recommendationservice_socket/2 | recommendationservice | correct | socket=3(s); cpu=2(o); loss=2(o) |
| recommendationservice_socket/3 | recommendationservice | escalated_truth_first | socket=1(o); cpu=0(o); loss=0(o) |
