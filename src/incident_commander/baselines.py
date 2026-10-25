"""Non-agent baselines the full agent is measured against (eval plan):
1. plain:  one prompt, raw(ish) telemetry pasted in, "name the root cause".
2. cot:    same input, step-by-step prompt.
A retrieval-only variant is added in Module 3.
Both share the same model client and the same output schema as the agent so
scoring is identical. TODO(M2): implement once loaders work.
"""


def run_baseline(kind: str, case_id: str, system: str) -> dict:
    """Returns {case_id, hypothesis: {service, fault_type}, raw_response}."""
    raise NotImplementedError
