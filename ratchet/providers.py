"""Model providers. Everything speaks the OpenAI chat-completions format.

- Teacher and judge: Nebius Token Factory (OpenAI-compatible).
- Student: a Nebius Serverless Endpoint running vLLM with LoRA adapters, where each
  adapter is addressed by its name (e.g. "student-v3") in the `model` field.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class Completion:
    text: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    latency_ms: float = 0.0
    token_logprobs: list[float] = field(default_factory=list)

    @property
    def mean_prob(self) -> float | None:
        if not self.token_logprobs:
            return None
        return math.exp(sum(self.token_logprobs) / len(self.token_logprobs))


class Provider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        n: int = 1,
        temperature: float = 0.0,
        logprobs: bool = False,
        response_format: dict[str, Any] | None = None,
    ) -> list[Completion]: ...


class OpenAICompatProvider:
    """Minimal OpenAI-compatible client (Token Factory, vLLM).

    `transport` replaces the network (tests pass `httpx.MockTransport`); the default sends real requests."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            transport=transport,
        )

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        body: dict[str, Any] = {"model": model, "messages": messages, "n": n, "temperature": temperature}
        if logprobs:
            body["logprobs"] = True
        if response_format:
            body["response_format"] = response_format
        t0 = time.perf_counter()
        resp = self.client.post(f"{self.base_url}/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        latency = (time.perf_counter() - t0) * 1000
        usage = data.get("usage") or {}
        choices = data.get("choices") or []
        per_out = int((usage.get("completion_tokens") or 0) / max(1, len(choices)))
        out = []
        for ch in choices:
            lp = []
            content_lp = ((ch.get("logprobs") or {}).get("content")) or []
            for tok in content_lp:
                if tok.get("logprob") is not None:
                    lp.append(float(tok["logprob"]))
            out.append(
                Completion(
                    text=(ch.get("message") or {}).get("content") or "",
                    model=data.get("model", model),
                    in_tokens=int(usage.get("prompt_tokens") or 0),
                    out_tokens=per_out,
                    latency_ms=latency,
                    token_logprobs=lp,
                )
            )
        return out


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
