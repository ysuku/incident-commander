"""Model clients: OpenRouter/OpenAI-compatible for real runs, a rule-based mock
for tests and keyless demos (the eval plan's 'mock backend keeps tests free').
"""
import json
import os
import re
import time


def _load_env() -> None:
    """load_dotenv() for the settings, minus the Claude key (see _capstone_anthropic_key):
    it stays in the file and is never placed in os.environ."""
    from dotenv import dotenv_values, find_dotenv
    for k, v in dotenv_values(find_dotenv(usecwd=True)).items():
        if k != ANTHROPIC_KEY_VAR and v is not None:
            os.environ.setdefault(k, v)


class OpenRouterClient:
    """Chat-completions client; works with OpenRouter, OpenAI, or any local
    OpenAI-compatible server (e.g. LM Studio / Ollama serving Qwen)."""

    def __init__(self, model: str | None = None, temperature: float = 0.0):
        from openai import OpenAI
        _load_env()
        self.model = model or os.environ.get("OPENAI_MODEL")
        if not self.model:
            raise RuntimeError("Set OPENAI_MODEL (and OPENAI_API_KEY / OPENAI_BASE_URL) in .env")
        self.temperature = temperature
        # Reads OPENAI_API_KEY / OPENAI_BASE_URL from env. A slow local model can take
        # more than the SDK's 600s default for one reply; the timeout then retries while
        # the server is still generating, piling up concurrent requests until MLX hits
        # Metal's buffer limit. For local runs set IC_REQUEST_TIMEOUT high and IC_MAX_RETRIES=0.
        self.client = OpenAI(timeout=float(os.environ.get("IC_REQUEST_TIMEOUT", "600")),
                             max_retries=int(os.environ.get("IC_MAX_RETRIES", "2")))
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


# USD per 1M tokens (input, output); Anthropic first-party rates, Oct 2026.
CLAUDE_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


ANTHROPIC_KEY_VAR = "IC_ANTHROPIC_API_KEY"


def _capstone_anthropic_key() -> str:
    """The capstone's own Claude key, read from the repo's .env file only.

    Deliberately NOT the standard ANTHROPIC_API_KEY: a key under that name in a
    shell is picked up by other tools (Claude Code bills it). The value is read
    straight from .env and passed to the client, never put into os.environ, and
    an ambient ANTHROPIC_API_KEY is ignored.
    """
    from dotenv import dotenv_values

    from .config import REPO_ROOT
    key = (dotenv_values(REPO_ROOT / ".env").get(ANTHROPIC_KEY_VAR) or "").strip()
    if not key:
        raise RuntimeError(f"Set {ANTHROPIC_KEY_VAR} in {REPO_ROOT / '.env'} to use --model claude-*. "
                           "The shell's ANTHROPIC_API_KEY is ignored on purpose.")
    return key


class AnthropicClient:
    """Native Anthropic Messages API client with the same complete()/last_call
    interface as OpenRouterClient, so the loop does not care which one it has.

    Thinking is adaptive and always on for Opus 5.5 (it cannot be disabled), so
    depth is set with effort (IC_EFFORT, default medium). No temperature: current
    models reject sampling params. The loop sends a fresh system+user pair each
    step, so no thinking blocks are ever replayed.
    """

    def __init__(self, model: str | None = None):
        import anthropic
        _load_env()
        self.model = model or "claude-opus-5-5"
        self.effort = os.environ.get("IC_EFFORT", "medium")
        self.client = anthropic.Anthropic(api_key=_capstone_anthropic_key())
        self.calls = 0
        self.cost_usd = 0.0
        self.last_call: dict = {}

    def complete(self, messages: list[dict]) -> str:
        self.calls += 1
        # own setting: IC_MAX_TOKENS in .env is tuned for the local Qwen runs
        max_tokens = int(os.environ.get("IC_CLAUDE_MAX_TOKENS", "16000"))
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        t0 = time.monotonic()
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=max_tokens, system=system, messages=turns,
            thinking={"type": "adaptive"}, output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        text = "".join(b.text for b in r.content if b.type == "text")
        u = r.usage
        # price by the model that actually served (a refusal fallback may differ)
        p_in, p_out = CLAUDE_PRICES.get(r.model, CLAUDE_PRICES.get(self.model, (0.0, 0.0)))
        cost = (u.input_tokens * p_in + u.output_tokens * p_out) / 1e6
        self.cost_usd += cost
        self.last_call = {
            "seconds": round(time.monotonic() - t0, 1),
            "prompt_tokens": u.input_tokens,
            "completion_tokens": u.output_tokens,  # includes thinking
            "max_tokens": max_tokens,
            "answer_chars": len(text),
            "effort": self.effort,
            "served_by": r.model,
            "cost_usd": round(cost, 5),
            # map to the loop's vocabulary: "length" = cut off at max_tokens
            "finish_reason": {"max_tokens": "length", "end_turn": "stop"}.get(r.stop_reason, r.stop_reason),
        }
        if r.stop_reason == "refusal" and r.stop_details:
            # a safety-classifier decline (seen once on step 1); keep why, for the log
            self.last_call["refusal"] = {"category": r.stop_details.category,
                                         "explanation": r.stop_details.explanation}
        return text


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
