"""Model clients: OpenRouter/OpenAI-compatible for real runs, a rule-based mock
for tests and keyless demos (the eval plan's 'mock backend keeps tests free').
"""
import json
import os
import re
import time


class OpenRouterClient:
    """Chat-completions client; works with OpenRouter, OpenAI, or any local
    OpenAI-compatible server (e.g. LM Studio / Ollama serving Qwen)."""

    def __init__(self, model: str | None = None, temperature: float = 0.0):
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv()
        self.model = model or os.environ.get("OPENAI_MODEL")
        if not self.model:
            raise RuntimeError("Set OPENAI_MODEL (and OPENAI_API_KEY / OPENAI_BASE_URL) in .env")
        self.temperature = temperature
        self.client = OpenAI()  # reads OPENAI_API_KEY / OPENAI_BASE_URL from env
        self.calls = 0
        self.last_call: dict = {}

    def complete(self, messages: list[dict]) -> str:
        self.calls += 1
        max_tokens = int(os.environ.get("IC_MAX_TOKENS", "4096"))
        t0 = time.monotonic()
        r = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature,
            max_tokens=max_tokens)
        choice = r.choices[0]
        msg = choice.message
        # Local reasoning models return their thinking in a separate field.
        reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None) or ""
        u = r.usage
        self.last_call = {
            "seconds": round(time.monotonic() - t0, 1),
            "prompt_tokens": getattr(u, "prompt_tokens", None),
            "completion_tokens": getattr(u, "completion_tokens", None),  # includes reasoning
            "max_tokens": max_tokens,
            "reasoning_chars": len(reasoning),
            "answer_chars": len(msg.content or ""),
            # "length" means the reply was cut off at max_tokens; "stop" means it ended on its own
            "finish_reason": choice.finish_reason,
        }
        return msg.content or ""


class MockModel:
    """Deterministic stand-in: investigates metrics, then traces, then finishes
    on the top metric deviation. Parses the scratchpad text it is shown, exactly
    like a real model would. Never used for graded numbers — tests and demos only."""

    FAULT_BY_METRIC = {"cpu": "cpu", "mem": "mem", "diskio": "disk",
                       "socket": "socket", "latency-50": "delay", "latency-90": "delay"}

    def __init__(self):
        self.calls = 0

    def complete(self, messages: list[dict]) -> str:
        self.calls += 1
        self.last_call = {"seconds": 0.0, "prompt_tokens": None, "completion_tokens": None,
                          "finish_reason": "stop", "mock": True}
        board = messages[-1]["content"]
        if "E1" not in board:
            return json.dumps({"thought": "Start with cheap, computed metric deviations.",
                               "candidates": [],
                               "action": {"tool": "metric_query", "args": {"top": 5}}})
        if "E2" not in board:
            m = re.search(r"deviations in scope \[[^\]]*\]: ([a-z]+)_([a-z0-9-]+)", board)
            svc = m.group(1) if m else "unknown"
            return json.dumps({"thought": "Corroborate with a second telemetry kind.",
                               "candidates": [f"{svc}: resource fault (from metrics)"],
                               "action": {"tool": "trace_lookup", "args": {"limit": 3}}})
        m = re.search(r"deviations in scope \[[^\]]*\]: ([a-z]+)_([a-z0-9-]+)", board)
        svc, metric = (m.group(1), m.group(2)) if m else ("unknown", "cpu")
        fault = self.FAULT_BY_METRIC.get(metric, metric)
        return json.dumps({"thought": "Metrics and traces both implicate the same service.",
                           "action": {"finish": {
                               "root_cause_service": svc, "fault_type": fault,
                               "supporting_evidence": ["E1", "E2"],
                               "alternatives_rejected": [
                                   {"candidate": "services on errored trace paths",
                                    "why": "errors are downstream symptoms; their own resources are normal (E1)"}],
                               "proposed_fix": {
                                   "description": f"Raise {metric} limit for {svc} after confirming app behavior",
                                   "destructive": True}}}})
