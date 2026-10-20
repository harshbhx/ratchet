"""Model providers. Everything speaks the OpenAI chat-completions format.

- Teacher and judge: Nebius Token Factory (OpenAI-compatible).
- Student: a Nebius Serverless Endpoint running vLLM with LoRA adapters, where each
  adapter is addressed by its name (e.g. "student-v3") in the `model` field.

Failures raise `UpstreamError` (defined in `resilience.py`, re-exported here), never a bare httpx error.
"""

from __future__ import annotations

import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .resilience import BAD_RESPONSE, NO_RETRY, RetryPolicy, UpstreamError, as_upstream, retry_call

__all__ = ["COMPLETE", "CUT_OFF", "Completion", "OpenAICompatProvider", "Provider", "UpstreamError", "approx_tokens"]

# `Completion.finish_reason` values. The model ended its answer itself (`stop`) or with a tool call (`tool_calls`): only
# such a teacher answer can be stored (INV-16). The answer was cut off, by the token limit or a content filter: a
# student sample like that gets no vote (`confidence.score`).
COMPLETE = ("stop", "tool_calls")
CUT_OFF = ("length", "content_filter")


@dataclass
class Completion:
    """One choice of a chat completion. `text` is the message's `content` only: reasoning that a server returns apart
    (`reasoning_content`, `reasoning`) is never read. `finish_reason` is why the model stopped (`stop` when the server
    does not say). `reasoning_tokens` is this choice's share of `usage.completion_tokens_details.reasoning_tokens`;
    `out_tokens` already includes them."""

    text: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    latency_ms: float = 0.0
    token_logprobs: list[float] = field(default_factory=list)
    finish_reason: str = "stop"
    reasoning_tokens: int = 0

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

    `timeout` is an `httpx.Timeout` (`resilience.timeouts(read_s, connect_s)`) or one number for every phase. `retry`
    retries failed calls (`resilience.retry_call`; None = one attempt, as for the student, whose retry is the teacher).
    Every failure raises `UpstreamError` (ADR 0014). `transport` replaces the network (tests pass `httpx.MockTransport`)
    and `sleep`, `clock` and `rng` the waits between attempts; with none passed, it sends real requests and really
    waits."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: httpx.Timeout | float = 120.0,
        retry: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout)
        self.retry = retry or NO_RETRY
        self.sleep = sleep
        self.clock = clock
        self.rng = rng
        self.client = httpx.Client(
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            transport=transport,
        )

    def _within(self, remaining: float) -> httpx.Timeout:
        """This attempt's timeouts: the configured ones, none longer than the time left before the retry deadline."""
        if math.isinf(remaining):
            return self.timeout
        t = self.timeout
        return httpx.Timeout(
            connect=_at_most(t.connect, remaining),
            read=_at_most(t.read, remaining),
            write=_at_most(t.write, remaining),
            pool=_at_most(t.pool, remaining),
        )

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        body: dict[str, Any] = {"model": model, "messages": messages, "n": n, "temperature": temperature}
        if logprobs:
            body["logprobs"] = True
        if response_format:
            body["response_format"] = response_format
        url = f"{self.base_url}/chat/completions"

        def attempt(remaining: float) -> httpx.Response:
            resp = self.client.post(url, json=body, timeout=self._within(remaining))
            if not resp.is_success:
                raise UpstreamError.from_response(resp)
            return resp

        t0 = time.perf_counter()
        try:
            resp = retry_call(attempt, self.retry, sleep=self.sleep, clock=self.clock, rng=self.rng)
        except httpx.HTTPError as e:
            if isinstance(e, UpstreamError):
                raise
            raise as_upstream(e) from e  # a timeout is 504, an unreachable endpoint 502; never httpx's message
        latency = (time.perf_counter() - t0) * 1000  # what the caller waited, retries included
        # A 2xx body is read once and never retried: the model answered, and the tokens are billed.
        try:
            data = resp.json()
        except (ValueError, RecursionError) as e:  # RecursionError: JSON nested too deeply for the parser
            raise UpstreamError(502, "the upstream model's response is not JSON", code=BAD_RESPONSE) from e
        return _completions(data, model, latency)


def _at_most(value: float | None, limit: float) -> float:
    return limit if value is None else min(value, limit)


def _count(value: Any) -> int:
    """A token count from `usage`, or 0 when it is missing or not a non-negative number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return 0
    return int(value)


def _content(message: Any) -> str:
    """The text of a choice's message: a string, or the text parts of a list; anything else is no text. Only `content`
    is read: vLLM's and Token Factory's reasoning parsers return the reasoning in `reasoning_content` or `reasoning`,
    which must never be served or stored as the answer."""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


_FINISH = re.compile(r"[a-z0-9_.-]{1,40}")


def _finish_reason(choice: dict[str, Any]) -> str:
    """Why the model stopped, lower-cased: `stop` when the server leaves it out (or null), `unknown` when it is not a
    short word. Never longer than 40 characters, so it is safe to put in an event."""
    value = choice.get("finish_reason")
    if value is None:
        return "stop"
    if isinstance(value, str) and _FINISH.fullmatch(value.strip().lower()):
        return value.strip().lower()
    return "unknown"


def _logprob_count(choice: dict[str, Any]) -> int | None:
    """How many tokens a choice's `logprobs.content` lists (one entry per generated token), or None without that list."""
    logprobs = choice.get("logprobs")
    content = logprobs.get("content") if isinstance(logprobs, dict) else None
    return len(content) if isinstance(content, list) else None


def _split(total: int, n: int) -> list[int]:
    """`total` split evenly over `n` choices, the remainder going to the first ones, so the parts add up to it."""
    q, r = divmod(total, n)
    return [q + (1 if i < r else 0) for i in range(n)]


def _out_tokens(total: int, choices: list[dict[str, Any]]) -> list[int]:
    """Each choice's output tokens. `usage.completion_tokens` (`total`) is what all choices together cost, so the parts
    always add up to it. When every choice lists its tokens in `logprobs.content` (the student, sampled with logprobs),
    those counts say how it splits: exactly them when they add up to the total, as on vLLM, and in proportion when they
    do not (reasoning tokens a server leaves out of the logprobs). Otherwise the split is even, an approximation. With no
    usage, the logprob counts are taken as they are."""
    counts = [_logprob_count(ch) for ch in choices]
    if any(c is None for c in counts):
        return _split(total, len(choices))
    known = [c or 0 for c in counts]
    listed = sum(known)
    if total == 0 or listed == total:
        return known
    if listed == 0:
        return _split(total, len(choices))
    # Largest remainder in integers: floor each proportional share, then give the leftover tokens to the largest
    # fractions (earlier choices first on ties), so the parts add up to `total` exactly.
    parts = [total * c // listed for c in known]
    order = sorted(range(len(known)), key=lambda i: (-(total * known[i] % listed), i))
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts


def _token_logprobs(choice: dict[str, Any]) -> list[float]:
    """The finite token logprobs of a choice (empty when the endpoint sent none)."""
    logprobs = choice.get("logprobs")
    content = logprobs.get("content") if isinstance(logprobs, dict) else None
    if not isinstance(content, list):
        return []
    out = []
    for tok in content:
        lp = tok.get("logprob") if isinstance(tok, dict) else None
        if isinstance(lp, (int, float)) and not isinstance(lp, bool) and math.isfinite(lp):
            out.append(float(lp))
    return out


def _completions(data: Any, model: str, latency: float) -> list[Completion]:
    """Parse a chat-completions body. A body that is not an object, or has no choices, is a 502 `bad_upstream_response`
    (before: an IndexError, so a 500); malformed optional fields (usage, logprobs, finish_reason) count as absent."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not all(isinstance(ch, dict) for ch in choices):
        raise UpstreamError(502, "the upstream model returned no choices", code=BAD_RESPONSE)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    in_tokens = _count(usage.get("prompt_tokens"))
    out_tokens = _out_tokens(_count(usage.get("completion_tokens")), choices)
    reasoning = _split(_count(details.get("reasoning_tokens")), len(choices))  # no per-choice figure exists
    served = data.get("model") if isinstance(data.get("model"), str) and data.get("model") else model
    return [
        Completion(
            text=_content(ch.get("message")),
            model=served,
            in_tokens=in_tokens,
            out_tokens=out,
            latency_ms=latency,
            token_logprobs=_token_logprobs(ch),
            finish_reason=_finish_reason(ch),
            reasoning_tokens=thought,
        )
        for ch, out, thought in zip(choices, out_tokens, reasoning, strict=True)
    ]


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
