# Retrieval A/B: checkoutservice_cpu/1 (2026-10-25)

Same evidence and question in every run; the only difference between A and B is the runbook text. Temperature 0.
Retrieved for B (both models): runbooks/kubernetes/CPUThrottlingHigh.md#action (d=0.435), runbooks/node/NodeHighNumberConntrackEntriesUsed.md#symptom (d=0.555), runbooks/kubernetes/KubeContainerWaiting.md#symptom (d=0.488), runbooks/node/NodeNetworkTransmitErrs.md#action (d=0.545)

## Model 1: Qwen/Qwen3-4B (run 21:31)

### A. Without retrieval

{"description": "Scale the checkoutservice to handle increased CPU load.", "conditions": "when the root cause is CPU usage and the service is experiencing high latency/availability issues.", "destructive": false}

### B. With retrieval

{"description": "Increase CPU limits for checkoutservice container to alleviate CPU throttling.", "conditions": "when the application is not performing normally and the kernel version is up to date", "destructive": true}

## Model 2: mlx-community/Qwen3.6-27B-4bit (run 21:38)

### A. Without retrieval

{"description": "Scale out checkoutservice replicas or increase CPU resource limits to alleviate CPU saturation. If scaling is constrained, restart checkoutservice pods to clear potential runaway processes, then profile for inefficient code or retry loops.", "conditions": "Appropriate when CPU saturation stems from traffic spikes, insufficient resource quotas, or transient load. Not appropriate when cluster node capacity is exhausted, when the spike is caused by a known application bug requiring a code fix/rollback, or when scaling would trigger cascading resource exhaustion in dependent services.", "destructive": false}

### B. With retrieval

{"description": "Increase the CPU limit for the checkoutservice container in its Kubernetes deployment while keeping the CPU request unchanged, then trigger a rolling restart to apply the change. Monitor latency and error rates to confirm recovery.", "conditions": "Appropriate when checkoutservice is confirmed to be CPU-bound with extreme usage deviations (z-score > 200) causing trace latency spikes and errors, and when the application is exhibiting erratic behavior or triggering concurrent alerts. Not appropriate if the CPU spike stems from an application-level bug, infinite loop, or unoptimized code that requires a software fix rather than resource scaling.", "destructive": false}
