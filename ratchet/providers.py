"""Model providers. Everything speaks the OpenAI chat-completions format.

- Teacher and judge: Nebius Token Factory (OpenAI-compatible).
- Student: a Nebius Serverless Endpoint running vLLM with LoRA adapters, where each
  adapter is addressed by its name (e.g. "student-v3") in the `model` field.

Failures raise `UpstreamError` (defined in `resilience.py`, re-exported here), never a bare httpx error.

A client's generation parameters travel as `GenParams`; `GenPolicy` derives what each role may send from them (the
allowlists `RATCHET_TEACHER_PARAMS` and `RATCHET_STUDENT_PARAMS`, the token caps, the reasoning headroom), so the router,
the live golden eval and the mock one apply the same rules (ADR 0017).
"""

from __future__ import annotations

import difflib
import math
import random
import re
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import httpx

from .obs import upstream_headers
from .resilience import BAD_RESPONSE, NO_RETRY, RetryPolicy, UpstreamError, as_upstream, retry_call

__all__ = [
    "COMPLETE",
    "Completion",
    "GenParams",
    "GenPlan",
    "GenPolicy",
    "OpenAICompatProvider",
    "Provider",
    "UpstreamError",
    "approx_tokens",
    "parse_param_allowlist",
    "served_logprobs",
]

# `Completion.finish_reason` values for an answer the model ended itself (`stop`) or with a tool call (`tool_calls`).
# Only such a teacher answer can be stored (INV-16), and only such a student sample gets a vote (`confidence.score`);
# any other value (`length`, `content_filter`, vLLM's `abort`, `unknown`) means the answer may be cut off.
COMPLETE = ("stop", "tool_calls")


@dataclass
class Completion:
    """One choice of a chat completion. `text` is the message's `content` only: reasoning that a server returns apart
    (`reasoning_content`, `reasoning`) is never read. `finish_reason` is why the model stopped (`stop` when the server
    does not say). `reasoning_tokens` is this choice's share of `usage.completion_tokens_details.reasoning_tokens`;
    `out_tokens` already includes them. `cached_tokens` is `usage.prompt_tokens_details.cached_tokens`, the part of
    `in_tokens` the server read from its prompt cache (0 when it does not say); like `in_tokens`, every choice of one
    call carries the whole count, because the choices share the prompt.

    `token_logprobs` are the finite token logprobs the confidence score uses. `logprobs_content` is the choice's
    `logprobs.content` as the server sent it (OpenAI's `[{token, logprob, bytes, top_logprobs}]`; None without one). It
    is kept as it came, not copied or checked, so a request that does not return logprobs pays nothing for it;
    `served_logprobs` checks it and puts it in OpenAI's shape for a client that asked. Never mutate it."""

    text: str
    model: str
    in_tokens: int = 0
    out_tokens: int = 0
    latency_ms: float = 0.0
    token_logprobs: list[float] = field(default_factory=list)
    finish_reason: str = "stop"
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    logprobs_content: list[Any] | None = None

    @property
    def mean_prob(self) -> float | None:
        if not self.token_logprobs:
            return None
        return math.exp(sum(self.token_logprobs) / len(self.token_logprobs))


# ---------- generation parameters ----------
# The request fields a provider can forward, by their OpenAI names: GenParams' fields, `extra` aside. A client's
# `max_completion_tokens` arrives as `max_tokens` (openai_compat.ChatRequest) and goes upstream under that name.
PARAM_FIELDS = (
    "max_tokens",
    "temperature",
    "top_p",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "response_format",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "logprobs",
    "top_logprobs",
    "reasoning_effort",
)
# Fields Ratchet sets itself (model, messages, n, stream) or never sends to a model (the end user's identity, the
# storage, metadata and billing-tier choices, the legacy function fields): an allowlist that names one is a configuration
# mistake.
NEVER_FORWARDED = frozenset(
    {
        "model",
        "messages",
        "n",
        "stream",
        "stream_options",
        "user",
        "safety_identifier",
        "store",
        "metadata",
        "service_tier",
        "functions",
        "function_call",
    }
)
# Standard fields no default allowlist forwards, because the gateway cannot use what comes back yet: a tool call (a
# response has no `message.tool_calls` until the tool-call item, and the student's vLLM runs without a tool parser, so
# it refuses `tools`). An operator can still name them. The teacher's `logprobs` and `top_logprobs` are forwarded since
# responses carry logprobs (FR-1.14): only when the client asked, and never to an audit or arbitration (CHECK_DROPS).
NOT_YET_FORWARDED = frozenset({"tools", "tool_choice", "parallel_tool_calls"})
# RATCHET_TEACHER_PARAMS and RATCHET_STUDENT_PARAMS when unset. The student gets what shapes the answer it must give
# (length, stops, format), not the client's sampling: its own sampling is fixed (confidence.STUDENT_SAMPLING).
DEFAULT_TEACHER_PARAMS = frozenset(PARAM_FIELDS) - NOT_YET_FORWARDED
DEFAULT_STUDENT_PARAMS = frozenset({"max_tokens", "stop", "seed", "response_format", "logit_bias", "top_logprobs"})
DEFAULT_TEACHER_MAX_TOKENS = 8192  # RATCHET_TEACHER_MAX_TOKENS
DEFAULT_STUDENT_MAX_TOKENS = 1024  # RATCHET_STUDENT_MAX_TOKENS
_PARAM_NAME = re.compile(r"[a-z_][a-z0-9_]{0,63}")
# How close an allowlist name may come to a standard field before it is taken for a typo of it (`max_token`, `stops`,
# `temprature`) rather than a vendor field: difflib's ratio. Vendor fields such as `top_k` (0.8 to `top_p`) and
# `min_tokens` (0.8 to `max_tokens`) stay below it.
_TYPO_RATIO = 0.88
# The most a client's temperature may be for a teacher answer written under it to be stored: above 1 the teacher samples
# from a flatter distribution than its own, and at 2 it writes noise.
STORED_MAX_TEMPERATURE = 1.0


@dataclass(frozen=True)
class GenParams:
    """Generation parameters for one model call, by OpenAI's names; None means not set. Built from a validated request
    (`from_request`) or a stored example (`from_stored`); `GenPolicy` turns a client's into what each role sends. `stop` is
    a tuple. `extra` holds top-level fields Ratchet does not know (vLLM's `top_k`, for example), which are forwarded only
    when an allowlist names them. Never mutate one: the router shares them between calls and threads."""

    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: tuple[str, ...] | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, int] | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    reasoning_effort: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, req: Any) -> GenParams:
        """The parameters of an `openai_compat.ChatRequest` (already validated). Empty `stop`, `tools` and `logit_bias`
        count as not sent, so they are never forwarded or stored as a condition."""
        return cls(
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            stop=tuple(req.stop) if req.stop else None,
            seed=req.seed,
            presence_penalty=req.presence_penalty,
            frequency_penalty=req.frequency_penalty,
            logit_bias=req.logit_bias or None,
            response_format=req.response_format,
            tools=req.tools or None,
            tool_choice=req.tool_choice,
            parallel_tool_calls=req.parallel_tool_calls,
            logprobs=req.logprobs,
            top_logprobs=req.top_logprobs,
            reasoning_effort=req.reasoning_effort,
            extra=dict(req.extra),
        )

    @classmethod
    def from_stored(cls, stored: Mapping[str, Any] | None) -> GenParams | None:
        """The conditions stored with an example (`examples.params`, `ledger.STORED_PARAM_KEYS`), so the golden eval
        samples the student under them (FR-5.1). None when there are none; a value of the wrong shape is left out."""
        if not stored:
            return None
        stop = stored.get("stop")
        tools = stored.get("tools")
        response_format = stored.get("response_format")
        tool_choice = stored.get("tool_choice")
        return cls(
            stop=tuple(stop) if isinstance(stop, list) and stop and all(isinstance(s, str) for s in stop) else None,
            response_format=response_format if isinstance(response_format, dict) else None,
            tools=tools if isinstance(tools, list) and tools else None,
            tool_choice=tool_choice if isinstance(tool_choice, (str, dict)) else None,
        )

    def unstorable(self) -> str | None:
        """Why a teacher answer written under these parameters cannot be a training target or a golden reference, or None
        when it can. A client's `logit_bias`, a penalty, a temperature above 1 or a vendor field changes what the teacher
        writes, not only how it samples: a bias of +100 forces a token, so whoever holds an API key could dictate the
        label. Such an answer is served; it is just not stored (threat model T2). A vendor field counts because Ratchet
        cannot tell one that only samples (`top_k`) from one that constrains the answer (vLLM's `guided_choice`)."""
        if self.logit_bias:
            return "client logit_bias"
        if self.presence_penalty or self.frequency_penalty:
            return "client penalty"
        if self.temperature is not None and self.temperature > STORED_MAX_TEMPERATURE:
            return "client temperature above 1"
        if self.extra:
            return "client vendor field"
        return None

    def only(self, allow: Collection[str]) -> GenParams:
        """These parameters without the fields and extra keys `allow` does not name (the same object when none is left
        out, so the common case allocates nothing)."""
        dropped = {name: None for name in PARAM_FIELDS if name not in allow and getattr(self, name) is not None}
        extra = {k: v for k, v in self.extra.items() if k in allow}
        if not dropped and len(extra) == len(self.extra):
            return self
        return replace(self, **dropped, extra=extra)

    def to_body(self, allow: Collection[str] | None = None) -> dict[str, Any]:
        """The fields that are set, as chat-completions body keys (`stop` as a list), then the extra keys; with `allow`,
        only the names it lists. Extra keys never replace a standard field."""
        body: dict[str, Any] = {}
        for name in PARAM_FIELDS:
            value = getattr(self, name)
            if value is not None and (allow is None or name in allow):
                body[name] = list(value) if name == "stop" else value
        for key, value in self.extra.items():
            if key not in body and key not in NEVER_FORWARDED and (allow is None or key in allow):
                body[key] = value
        return body


def parse_param_allowlist(spec: str, default: frozenset[str], name: str = "the parameter allowlist") -> frozenset[str]:
    """RATCHET_TEACHER_PARAMS / RATCHET_STUDENT_PARAMS: comma-separated request fields to forward, in any case. Empty
    means `default`; `none` forwards nothing; `all` stands for every standard field (`all,top_k` adds a vendor field);
    `max_completion_tokens` means `max_tokens`. Raises ValueError, so startup stops, for a field Ratchet sets itself or
    never forwards (`model`, `n`, `user`, `metadata`, `service_tier`, ...), a name that is not an identifier, and a name
    so close to a standard field that it is a typo of it (`max_token`, `stops`). Any other name is taken as a vendor
    field (vLLM's `top_k`) and forwarded when a client sends it."""
    text = spec.strip()
    if not text:
        return default
    if text.lower() == "none":
        return frozenset()
    out: set[str] = set()
    for item in (part.strip().lower() for part in text.split(",")):
        if not item:
            continue
        if item == "all":
            out.update(PARAM_FIELDS)
            continue
        if item == "max_completion_tokens":
            item = "max_tokens"
        if item in NEVER_FORWARDED or item == "none" or not _PARAM_NAME.fullmatch(item):
            raise ValueError(
                f"{name}: {item!r} cannot be forwarded; list request fields such as max_tokens, stop or top_k, "
                "or use 'all' or 'none'"
            )
        if item not in PARAM_FIELDS:
            near = difflib.get_close_matches(item, (*PARAM_FIELDS, "max_completion_tokens"), n=1, cutoff=_TYPO_RATIO)
            if near:
                raise ValueError(f"{name}: {item!r} is not a request field; did you mean {near[0]!r}?")
        out.add(item)
    return frozenset(out)


def _capped(requested: int | None, cap: int) -> int | None:
    """`requested`, at most `cap` (0 = no cap); the cap itself when nothing was requested."""
    if cap <= 0:
        return requested
    return cap if requested is None else min(requested, cap)


# What a teacher audit or arbitration leaves out of the client's parameters: what makes the answer random or tilts it
# token by token. The check is the teacher's deterministic answer (temperature 0) under the same conditions as the
# answer it is compared with: the same length limit, stops, format and tools.
CHECK_DROPS = ("top_p", "seed", "presence_penalty", "frequency_penalty", "logit_bias", "logprobs", "top_logprobs")
TEACHER_ROLES = ("serve", "audit", "arbitrate")
# The serving teacher's temperature for a request of n > 1 choices that sets none: at its default of 0 every choice
# would be the same answer. The student's own sampling temperature (confidence.STUDENT_SAMPLING) is the same.
CHOICES_TEMPERATURE = 0.7


@dataclass(frozen=True)
class GenPlan:
    """The parameters of one request's model calls: the student's samples (and the shadow's), the teacher when its
    answer is served (escalation, cold start, pinned, teacher-only), and the teacher's audit or arbitration (`check`).
    `visible_max` is the client's `max_tokens` when the teacher was allowed more (the reasoning headroom), else None:
    a teacher answer whose visible part is longer than that is marked cut off (`Ratchet._ask_teacher`). `n` is how many
    choices the client asked for (FR-1.14): the serving teacher writes that many, the student draws at least that many
    samples, and audits, arbitrations and shadows stay at one answer or `Settings.samples`."""

    student: GenParams
    serve: GenParams
    check: GenParams
    visible_max: int | None = None
    n: int = 1


@dataclass(frozen=True)
class GenPolicy:
    """Which of a client's parameters each role sends, and the token caps (`Settings.gen_policy()`; ADR 0017).

    - Student: the fields `student_allow` names, `max_tokens` at most `student_max_tokens`; its temperature, logprobs
      and `n` are fixed (`confidence.STUDENT_SAMPLING`, `Settings.samples`), so the client's never apply.
    - Teacher, serving: the fields `teacher_allow` names, temperature 0 unless the client set one (`CHOICES_TEMPERATURE`
      for n > 1 choices, so they differ), `max_tokens` = the client's plus `reasoning_headroom`, at most
      `teacher_max_tokens` (the cap alone when the client sent none).
    - Teacher, auditing or arbitrating: the same, at temperature 0 and without `CHECK_DROPS`, always one answer.

    A cap of 0 means no cap. A field outside an allowlist is never sent; the caps apply either way."""

    teacher_allow: frozenset[str] = DEFAULT_TEACHER_PARAMS
    student_allow: frozenset[str] = DEFAULT_STUDENT_PARAMS
    teacher_max_tokens: int = DEFAULT_TEACHER_MAX_TOKENS
    student_max_tokens: int = DEFAULT_STUDENT_MAX_TOKENS
    reasoning_headroom: int = 0

    def __post_init__(self) -> None:
        for name in ("teacher_max_tokens", "student_max_tokens", "reasoning_headroom"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number of tokens, 0 or more, not {value!r}")

    def student(self, params: GenParams | None) -> GenParams:
        p = (params or _NONE).only(self.student_allow)
        return replace(p, max_tokens=_capped(p.max_tokens, self.student_max_tokens), temperature=None, logprobs=None)

    def _teacher_max(self, requested: int | None) -> int | None:
        if requested is None:
            return self.teacher_max_tokens or None
        return _capped(requested + self.reasoning_headroom, self.teacher_max_tokens)

    def teacher(self, params: GenParams | None, role: str = "serve", n: int = 1) -> GenParams:
        """The teacher's parameters in `role`. With `n` > 1 choices to serve and no client temperature that reaches it
        (not sent, or not in `teacher_allow`), it samples at `CHOICES_TEMPERATURE` instead of 0."""
        if role not in TEACHER_ROLES:
            raise ValueError(f"role must be one of {TEACHER_ROLES}, not {role!r}")
        p = (params or _NONE).only(self.teacher_allow)
        max_tokens = self._teacher_max(p.max_tokens)
        if role == "serve":
            default = CHOICES_TEMPERATURE if n > 1 else 0.0
            return replace(p, max_tokens=max_tokens, temperature=default if p.temperature is None else p.temperature)
        return replace(p, max_tokens=max_tokens, temperature=0.0, **dict.fromkeys(CHECK_DROPS))

    def visible_limit(self, params: GenParams | None) -> int | None:
        """The client's `max_tokens` when the teacher may write more than it (headroom), else None."""
        requested = params.max_tokens if params is not None and "max_tokens" in self.teacher_allow else None
        if requested is None:
            return None
        sent = self._teacher_max(requested)
        return requested if sent is not None and sent > requested else None

    def plan(self, params: GenParams | None, n: int = 1) -> GenPlan:
        """Every call's parameters for a request with these client parameters and `n` choices. ValueError for an `n`
        that is not a whole number of at least 1 (the gateway's validation refuses one first, RATCHET_MAX_N)."""
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"n must be a whole number of choices, 1 or more, not {n!r}")
        return GenPlan(
            self.student(params),
            self.teacher(params, "serve", n),
            self.teacher(params, "audit"),
            self.visible_limit(params),
            n,
        )


_NONE = GenParams()


class Provider(Protocol):
    """An OpenAI-compatible chat model. `params` carries the request's other generation parameters; the explicit `n`,
    `temperature`, `logprobs` and `response_format` win over the same fields in it. Accept unknown keyword arguments
    (`**_`), so a later argument does not break an older provider."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        n: int = 1,
        temperature: float = 0.0,
        logprobs: bool = False,
        response_format: dict[str, Any] | None = None,
        params: GenParams | None = None,
        **_: Any,
    ) -> list[Completion]: ...


class OpenAICompatProvider:
    """Minimal OpenAI-compatible client (Token Factory, vLLM).

    `timeout` is an `httpx.Timeout` (`resilience.timeouts(read_s, connect_s)`) or one number for every phase. `retry`
    retries failed calls (`resilience.retry_call`; None = one attempt, as for the student, whose retry is the teacher).
    Every failure raises `UpstreamError` (ADR 0014). `transport` replaces the network (tests pass `httpx.MockTransport`)
    and `sleep`, `clock` and `rng` the waits between attempts; with none passed, it sends real requests and really
    waits. `allow` narrows the `GenParams` fields a call may send (None: every field it is given; the router has
    already applied the role's allowlist)."""

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
        allow: frozenset[str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.allow = allow
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

    def longest_call_s(self) -> float | None:
        """How long one `complete` call can run before its own timeouts end it: the pool, connect, write and read
        timeouts for every attempt, plus the longest wait between attempts (`cap_s`). None when a phase has no timeout. A
        student breaker waits this long before it takes a probe for lost (ADR 0015). A body that trickles in can take
        longer, because httpx's read timeout applies to each read."""
        t = self.timeout
        phases = (t.pool, t.connect, t.write, t.read)
        if any(p is None for p in phases):
            return None
        attempts = self.retry.max_attempts
        return sum(phases) * attempts + self.retry.cap_s * (attempts - 1)

    def complete(
        self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, deadline_s=None, **_
    ):
        """One chat completion, `n` choices. `params` (a `GenParams`) adds the request's other fields; the explicit
        arguments win: `logprobs=False` also drops `top_logprobs`, and a `response_format` replaces the params' one. Inside
        a request that came with a W3C `traceparent`, each attempt forwards it with a new span id (`obs.upstream_headers`).
        `deadline_s` shortens this call's retry deadline (never lengthens it): an off-path arbitration gives up sooner
        than a served answer would (ADR 0072)."""
        body: dict[str, Any] = params.to_body(self.allow) if params is not None else {}
        body.update(model=model, messages=messages, n=n, temperature=temperature)
        if logprobs:
            body["logprobs"] = True
        else:
            body.pop("logprobs", None)
            body.pop("top_logprobs", None)
        if response_format:
            body["response_format"] = response_format
        url = f"{self.base_url}/chat/completions"

        def attempt(remaining: float) -> httpx.Response:
            # The request's W3C trace context with a new span id per attempt (ADR 0062); nothing outside a traced request.
            resp = self.client.post(url, json=body, timeout=self._within(remaining), headers=upstream_headers() or None)
            if not resp.is_success:
                raise UpstreamError.from_response(resp)
            return resp

        t0 = time.perf_counter()
        retry = self.retry
        if deadline_s is not None and deadline_s < retry.deadline_s:
            retry = replace(retry, deadline_s=deadline_s)
        try:
            resp = retry_call(attempt, retry, sleep=self.sleep, clock=self.clock, rng=self.rng)
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
    """Why the model stopped, lower-cased: `stop` when the server leaves it out (null, or an empty string), `unknown`
    when it is not a short word. Never longer than 40 characters, so it is safe to put in an event. An empty value is
    a missing one, not an unknown one: read as `unknown`, every answer from such a server would go unstored."""
    value = choice.get("finish_reason")
    if value is None or (isinstance(value, str) and not value.strip()):
        return "stop"
    if isinstance(value, str) and _FINISH.fullmatch(value.strip().lower()):
        return value.strip().lower()
    return "unknown"


def _logprob_list(choice: dict[str, Any]) -> list[Any] | None:
    """A choice's `logprobs.content` (one entry per generated token) as the server sent it, or None without that list."""
    logprobs = choice.get("logprobs")
    content = logprobs.get("content") if isinstance(logprobs, dict) else None
    return content if isinstance(content, list) else None


def _logprob_count(choice: dict[str, Any]) -> int | None:
    """How many tokens a choice's `logprobs.content` lists, or None without that list."""
    content = _logprob_list(choice)
    return None if content is None else len(content)


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


def _number(value: Any) -> float | None:
    """`value` as a float when it is a JSON number (not a boolean, and not an integer too large for a float), else None.
    NaN and the infinities come back as they are, for the caller to judge."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value)
    except OverflowError:  # an integer of hundreds of digits, which json.loads accepts
        return None


def _token_logprobs(choice: dict[str, Any]) -> list[float]:
    """The finite token logprobs of a choice (empty when the endpoint sent none)."""
    content = _logprob_list(choice)
    if content is None:
        return []
    out = []
    for tok in content:
        lp = _number(tok.get("logprob")) if isinstance(tok, dict) else None
        if lp is not None and math.isfinite(lp):
            out.append(lp)
    return out


def _completions(data: Any, model: str, latency: float) -> list[Completion]:
    """Parse a chat-completions body. A body that is not an object, or has no choices, is a 502 `bad_upstream_response`
    (before: an IndexError, so a 500); malformed optional fields (usage, logprobs, finish_reason) count as absent."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not all(isinstance(ch, dict) for ch in choices):
        raise UpstreamError(502, "the upstream model returned no choices", code=BAD_RESPONSE)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    in_tokens = _count(usage.get("prompt_tokens"))
    cached = _count(prompt_details.get("cached_tokens"))  # one prompt for every choice, like in_tokens
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
            cached_tokens=cached,
            logprobs_content=_logprob_list(ch),
        )
        for ch, out, thought in zip(choices, out_tokens, reasoning, strict=True)
    ]


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------- token logprobs in responses (FR-1.14) ----------
# The lowest logprob a response carries: JSON has no -Infinity, so vLLM itself reports a token it could not have
# sampled as -9999.0, and so does Ratchet.
MIN_LOGPROB = -9999.0
_SURROGATE = re.compile("[\ud800-\udfff]")


def _logprob(value: Any) -> float | None:
    """A logprob as a response carries it: a finite number (-Infinity becomes MIN_LOGPROB), or None for anything else."""
    f = _number(value)
    if f is None or math.isnan(f) or f == math.inf:
        return None
    return max(f, MIN_LOGPROB)


def _token_text(value: Any) -> str | None:
    """A token's text, or None when it is not a string or holds an unpaired surrogate (which UTF-8 cannot carry, so the
    response could not be written)."""
    if not isinstance(value, str) or (not value.isascii() and _SURROGATE.search(value)):
        return None
    return value


def _token_bytes(entry: Mapping[str, Any], token: str) -> list[int]:
    """The token's UTF-8 bytes as the server sent them (a list of 0 to 255), else those of its text."""
    sent = entry.get("bytes")
    if isinstance(sent, list) and all(isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255 for b in sent):
        return list(sent)
    return list(token.encode("utf-8"))


def _alternative(entry: Any) -> dict[str, Any] | None:
    """One of a token's `top_logprobs` in OpenAI's shape, or None when it is malformed."""
    if not isinstance(entry, dict):
        return None
    token, lp = _token_text(entry.get("token")), _logprob(entry.get("logprob"))
    if token is None or lp is None:
        return None
    return {"token": token, "logprob": lp, "bytes": _token_bytes(entry, token)}


def _entry(entry: Any, top: int) -> dict[str, Any] | None:
    """One `logprobs.content` entry in OpenAI's shape ({token, logprob, bytes, top_logprobs}), with at most `top`
    alternatives (malformed ones left out), or None when the entry itself is malformed."""
    if not isinstance(entry, dict):
        return None
    token, lp = _token_text(entry.get("token")), _logprob(entry.get("logprob"))
    if token is None or lp is None:
        return None
    listed = entry.get("top_logprobs") if top > 0 else None
    alternatives: list[dict[str, Any]] = []
    for alt in listed if isinstance(listed, list) else ():
        a = _alternative(alt)
        if a is not None:
            alternatives.append(a)
            if len(alternatives) == top:
                break
    return {"token": token, "logprob": lp, "bytes": _token_bytes(entry, token), "top_logprobs": alternatives}


def _trim(entry: dict[str, Any], start: int, end: int) -> None:
    """Cut a token's text to `[start:end]` (whitespace `clean_answer` stripped from the answer's edges), with its bytes."""
    entry["token"] = entry["token"][start:end]
    entry["bytes"] = list(entry["token"].encode("utf-8"))


def served_logprobs(c: Completion, text: str, top: int | None = None) -> list[dict[str, Any]] | None:
    """The token logprobs of an answer as it is served, in OpenAI's `logprobs.content` shape, or None when they cannot
    be given. `text` is what the client gets of `c.text` (`tasks.clean_answer`): the reasoning and, for a structured
    task, a fence around the answer may be gone, and so is whitespace at its edges. `top` is the client's
    `top_logprobs` (each token keeps at most that many alternatives; none for 0 or None).

    The tokens returned always spell `text` exactly. When the model's tokens are `text` as they stand, they are all
    returned. Otherwise the tokens of the last place `text` occurs in them are kept, and the ones before and after it
    dropped (`<think>` reasoning, a fence, or reasoning tokens a server lists although its `content` leaves them out:
    reasoning never reaches a client). A token that `text` only partly covers is cut only when the part left out is
    whitespace; any other overlap means the answer cannot be told apart from what was dropped, and so does an answer
    that is not one stretch of the tokens (reasoning removed from its middle) or tokens that do not spell it (a
    character split over tokens that do not decode alone). Then, and when the model sent no logprobs or a malformed
    entry, the result is None. Linear in the number of tokens; run only for a client that asked for logprobs."""
    if c.logprobs_content is None:
        return None
    tokens: list[dict[str, Any]] = []
    for raw in c.logprobs_content:
        e = _entry(raw, top or 0)
        if e is None:
            return None
        tokens.append(e)
    joined = "".join(e["token"] for e in tokens)
    if joined == text:
        return tokens
    if not text:
        return []
    start = joined.rfind(text)
    if start < 0:
        return None
    end = start + len(text)
    first = last = -1
    first_at = last_end = pos = 0
    for i, e in enumerate(tokens):
        nxt = pos + len(e["token"])
        if first < 0 and nxt > start:
            first, first_at = i, pos
        if nxt >= end:
            last, last_end = i, nxt
            break
        pos = nxt
    if joined[first_at:start].strip() or joined[end:last_end].strip():
        return None  # a token holds both the answer and something that is not whitespace
    out = tokens[first : last + 1]
    if end < last_end:
        _trim(out[-1], 0, len(out[-1]["token"]) - (last_end - end))
    if first_at < start:
        _trim(out[0], start - first_at, len(out[0]["token"]))
    return out
