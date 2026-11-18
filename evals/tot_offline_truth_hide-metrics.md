# Offline ToT check (heuristic generator, suspect = truth, metrics hidden)

90 cases, no model calls. Outcome counts:

| Outcome | n |
|---|---|
| escalated_truth_first | 15 |
| escalated | 75 |

Truth branch pruned: 0; mean expansions 1.0

## By fault type

| Fault | correct | service_only | wrong | tie_with_truth | tie_without_truth | escalated_truth_first | escalated |
|---|---|---|---|---|---|---|---|
| cpu | 0 | 0 | 0 | 0 | 0 | 15 | 0 |
| delay | 0 | 0 | 0 | 0 | 0 | 0 | 15 |
| disk | 0 | 0 | 0 | 0 | 0 | 0 | 15 |
| loss | 0 | 0 | 0 | 0 | 0 | 0 | 15 |
| mem | 0 | 0 | 0 | 0 | 0 | 0 | 15 |
| socket | 0 | 0 | 0 | 0 | 0 | 0 | 15 |

## Cases

| Case | Suspect | Outcome | Branches (score, status) |
|---|---|---|---|
| checkoutservice_cpu/1 | checkoutservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_cpu/2 | checkoutservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_cpu/3 | checkoutservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_delay/1 | checkoutservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| checkoutservice_delay/2 | checkoutservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| checkoutservice_delay/3 | checkoutservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| checkoutservice_disk/1 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_disk/2 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_disk/3 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_loss/1 | checkoutservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| checkoutservice_loss/2 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_loss/3 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_mem/1 | checkoutservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| checkoutservice_mem/2 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_mem/3 | checkoutservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| checkoutservice_socket/1 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_socket/2 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| checkoutservice_socket/3 | checkoutservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| currencyservice_cpu/1 | currencyservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_cpu/2 | currencyservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_cpu/3 | currencyservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_delay/1 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_delay/2 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_delay/3 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_disk/1 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_disk/2 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_disk/3 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_loss/1 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_loss/2 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_loss/3 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_mem/1 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_mem/2 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_mem/3 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_socket/1 | currencyservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| currencyservice_socket/2 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| currencyservice_socket/3 | currencyservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_cpu/1 | emailservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_cpu/2 | emailservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_cpu/3 | emailservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_delay/1 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_delay/2 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_delay/3 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_disk/1 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_disk/2 | emailservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_disk/3 | emailservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_loss/1 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_loss/2 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_loss/3 | emailservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_mem/1 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_mem/2 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_mem/3 | emailservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| emailservice_socket/1 | emailservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_socket/2 | emailservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| emailservice_socket/3 | emailservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| productcatalogservice_cpu/1 | productcatalogservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| productcatalogservice_cpu/2 | productcatalogservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| productcatalogservice_cpu/3 | productcatalogservice | escalated_truth_first | cpu=0(o); mem=0(o); disk=0(o) |
| productcatalogservice_delay/1 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_delay/2 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_delay/3 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_disk/1 | productcatalogservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| productcatalogservice_disk/2 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_disk/3 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_loss/1 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_loss/2 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_loss/3 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_mem/1 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_mem/2 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_mem/3 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_socket/1 | productcatalogservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| productcatalogservice_socket/2 | productcatalogservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| productcatalogservice_socket/3 | productcatalogservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
| recommendationservice_cpu/1 | recommendationservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_cpu/2 | recommendationservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_cpu/3 | recommendationservice | escalated_truth_first | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_delay/1 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_delay/2 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_delay/3 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_disk/1 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_disk/2 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_disk/3 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_loss/1 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_loss/2 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_loss/3 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_mem/1 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_mem/2 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_mem/3 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_socket/1 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_socket/2 | recommendationservice | escalated | cpu=1(o); mem=1(o); disk=1(o) |
| recommendationservice_socket/3 | recommendationservice | escalated | cpu=0(o); mem=0(o); disk=0(o) |
