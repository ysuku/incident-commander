"""Checkpoint 3.1: controlled with/without-retrieval comparison.

Same model, same evidence, same question; the ONLY difference is whether the
runbook chunks returned by runbook_lookup are included. Temperature is 0 (see
model.py), so the two answers differ because of the retrieved text.

Run from capstone/ with your local Qwen server up (.env as for `diagnose`):
    python scripts/retrieval_ab.py
Output is printed and saved verbatim to evals/retrieval_ab.md.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from incident_commander import tools  # noqa: E402
from incident_commander.model import OpenRouterClient  # noqa: E402

# Evidence the agent actually collected on checkoutservice_cpu/1 (E1 and E2 from a
# saved run in evals/runs/). Diagnosis is given; the question is only the FIX.
EVIDENCE = (
    "Alert: online-boutique: latency/availability alert. Root cause already established by the agent:\n"
    "service=checkoutservice, fault_type=cpu.\n"
    "E1 [metrics] significant deviations in scope [all services]: checkoutservice_cpu z=226.43 (4164.9%); "
    "checkoutservice_mem z=7.7 (38.5%); checkoutservice_socket z=4.36 (21.8%)\n"
    "E2 [traces] 11945 traces after alert; worst: frontendservice -> currencyservice -> productcatalogservice "
    "2396.4ms errors=1; frontendservice -> checkoutservice -> productcatalogservice -> paymentservice -> "
    "emailservice -> recommendationservice -> currencyservice 1795.9ms errors=1"
)
QUESTION = ('Propose a fix for the on-call engineer. Reply with ONE JSON object and nothing else: '
            '{"description": "...", "conditions": "when this fix is or is not appropriate", '
            '"destructive": true|false}')
SYSTEM = "You are Incident Commander, an SRE assistant. Work only from the evidence given."
SYMPTOM_QUERY = "service container CPU usage pinned at its limit, request latency rising, timeouts upstream"


def ask(model, user: str) -> str:
    raw = model.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return raw or "(empty reply: model hit the token limit; raise IC_MAX_TOKENS and rerun)"


def main():
    model = OpenRouterClient()
    hits = tools.runbook_lookup(SYMPTOM_QUERY)
    refs = [f"{h['source_ref']} (d={h['distance']})" for h in hits]
    runbook_block = "\n\n".join(f"[{h['source_ref']}]\n{h['text']}" for h in hits) or "(no runbook match)"

    a_prompt = f"{EVIDENCE}\n\n{QUESTION}"
    b_prompt = (f"{EVIDENCE}\n\nReference runbook excerpts (reference only, not evidence):\n{runbook_block}\n\n{QUESTION}")

    print("Running A (no retrieval)...", file=sys.stderr)
    a = ask(model, a_prompt)
    print("Running B (with retrieval)...", file=sys.stderr)
    b = ask(model, b_prompt)

    out = (f"# Retrieval A/B: checkoutservice_cpu/1 ({datetime.now():%Y-%m-%d %H:%M})\n\n"
           f"Model: {model.model}, temperature 0. Same evidence and question; only difference is the runbook text.\n\n"
           f"Retrieved for B: {', '.join(refs) or 'nothing'}\n\n"
           f"## A. Without retrieval\n\n{a}\n\n## B. With retrieval\n\n{b}\n")
    (ROOT / "evals" / "retrieval_ab.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
