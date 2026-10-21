"""The cascade: student first, teacher when the student is unsure, every teacher answer becomes training data."""

from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple

import httpx

from . import licenses, tasks
from .confidence import SERVE_NOTHING, STUDENT_SAMPLING, Scored, majority, score
from .config import Settings
from .ledger import STORED_PARAM_KEYS, Ledger, Version, target_params
from .providers import (
    COMPLETE,
    Completion,
    GenParams,
    GenPlan,
    Provider,
    UpstreamError,
    approx_tokens,
    served_logprobs,
)
from .redact import redact, redact_json, redact_messages
from .resilience import (
    BAD_RESPONSE,
    TEACHER_CIRCUIT_OPEN,
    TOOL_CALLS_UNSUPPORTED,
    AdapterMissing,
    Breaker,
    CircuitOpen,
    describe_failure,
    endpoint_down,
    names_model,
    teacher_unavailable,
)

# Given a version, return (provider, model name to send). For vLLM + LoRA the model name is the adapter name.
StudentResolver = Callable[[Version], tuple[Provider, str]]

# A failing upstream call is logged as an event on these occurrences only, so an outage cannot flood the release log.
EVENT_ON_FAILURE = (1, 10, 100, 1000)

# How a request was served (`requests.route`): the student; the teacher because the student was unsure (`confidence`),
# because no version serves yet (`cold_start`), because the task is pinned (`pinned`), because the student call raised
# (`student_error`), or because the student's breaker was open (`student_breaker`); the student's own below-threshold
# answer because the teacher was unavailable (`degraded`, RATCHET_ON_TEACHER_FAILURE=serve_student); the fallback
# teacher because the primary was unavailable (`fallback_teacher`, RATCHET_TEACHER_FALLBACK_MODEL; ADR 0019); or the
# teacher because the student was confident but fewer than the client's n of its samples gave that answer, so it could
# not fill n choices (`n_choices`, FR-1.14, ADR 0020); or the teacher alone because the task is not registered and
# RATCHET_UNKNOWN_TASKS is `teacher` (`unknown_task`, FR-2.15, ADR 0022).
ROUTES = (
    "student",
    "confidence",
    "cold_start",
    "pinned",
    "student_error",
    "student_breaker",
    "degraded",
    "fallback_teacher",
    "n_choices",
    "unknown_task",
)
# Routes whose answer is not the primary teacher's or a confident student's: such a request runs no shadow, so release
# statistics never rest on a stand-in's answer (ADR 0019).
STAND_IN_ROUTES = frozenset({"degraded", "fallback_teacher"})
# `Result.degraded` (the response's `ratchet.degraded` and `X-Ratchet-Degraded`) for a degraded answer.
TEACHER_UNAVAILABLE = "teacher_unavailable"

# finish_reason values a `skip_example` event names as they are; any other is `other`, so the reasons stay a short list.
_FINISH_NAMED = frozenset({"length", "content_filter", "function_call", "abort", "error", "unknown"})
# The finish_reason values a client gets (`Result.finish_reason`); any other upstream value is reported as `stop`.
# `tool_calls` is not one yet: a response carries no `message.tool_calls` until the tool-call item, and OpenAI clients
# read that finish as "the calls are in the message". A teacher answer that is a tool call is refused (`_ask_teacher`).
CLIENT_FINISH = ("stop", "length", "content_filter")


def answer_problem(task: tasks.Task, c: Completion) -> str | None:
    """Why a teacher answer can be neither a training example nor a reference for the student (INV-16), or None when it
    can: it did not end by itself (`finish_reason length`, ...), nothing is left once reasoning is stripped (`empty
    answer`), a JSON task's answer does not parse (`invalid answer`), or a field of the task's spec is missing or does
    not normalise, such as a label outside its vocabulary (`invalid field category`; `Task.check`, FR-2.14). String
    operations only."""
    if c.finish_reason not in COMPLETE:
        return f"finish_reason {c.finish_reason if c.finish_reason in _FINISH_NAMED else 'other'}"
    return task.check(c.text)


# The serving teacher's parameters that only report on its answer (token logprobs a client asked for) and never change
# what it writes, so a serving call that sends them still writes the answer an audit would (FR-16.5, ADR 0028).
_REPORT_ONLY = {"logprobs": None, "top_logprobs": None}


def _labels_like_audit(plan: GenPlan) -> bool:
    """Whether an escalation's teacher answer is a label of the same kind as an audit's: one choice, written under the
    audit's parameters (temperature 0 and nothing from `CHECK_DROPS`, logprobs aside). Otherwise the teacher sampled
    (a client temperature, or n > 1 choices at `CHOICES_TEMPERATURE`) or a client steered it (`logit_bias`, a penalty,
    a seed), and the replay would weigh labels from two different teachers in one mean (ADR 0028). Such an escalation
    leaves `student_agree` NULL: a designed label that was not obtained, like a failed teacher call."""
    return plan.n == 1 and replace(plan.serve, **_REPORT_ONLY) == plan.check


# Added to a student call's longest time before its probe is presumed lost: the call starts a moment after allow().
PROBE_MARGIN_S = 5.0


def _probe_timeout(provider: Provider) -> float:
    """How long a breaker on `provider`'s endpoint waits before it takes an unanswered probe for lost: the provider's
    `longest_call_s()` plus PROBE_MARGIN_S, or 0 (one wait) when the provider cannot say. A probe still inside its own
    timeouts is never replaced, so a slow failing endpoint is probed once per wait and the wait doubles (ADR 0015)."""
    longest = getattr(provider, "longest_call_s", None)
    seconds = longest() if callable(longest) else None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return float(seconds) + PROBE_MARGIN_S


def _endpoint(provider: Provider) -> str:
    """The failure domain a provider talks to: its `base_url` (every OpenAICompatProvider, the mock fleet's students),
    else its class. Never the object's id: a resolver may build a new provider per call (the mock fleet does)."""
    base = getattr(provider, "base_url", None)
    return base if isinstance(base, str) and base else f"{type(provider).__module__}.{type(provider).__qualname__}"


@dataclass
class RequestMeta:
    """What a caller knows about a request besides its messages and parameters; later items fill more of it.
    `storable=False` keeps every teacher answer of the request out of the training set (no example is inserted or
    bumped); the request is still answered, audited and logged."""

    request_id: str | None = None
    client_request_id: str | None = None
    retry_count: int = 0
    storable: bool = True
    end_user: str | None = None
    idempotency_key: str | None = None


# The model calls of one request, as `Result.usage_breakdown` names them: the student's samples (one call, n choices),
# the served answer (one of those samples, or the teacher's call), the teacher's audit, the shadow's samples and the
# teacher's arbitration. A call that was not made, or that failed, is None.
USAGE_PARTS = ("student_samples", "served", "audit", "shadow", "arbitration")


@dataclass(frozen=True)
class Choice:
    """One of a response's choices (FR-1.14): the answer as served (`tasks.clean_answer`), why it ended (one of
    CLIENT_FINISH), and its token logprobs in OpenAI's `logprobs.content` shape (`providers.served_logprobs`, whose
    tokens spell `text` exactly). `logprobs` is None when the client did not ask for them, and when it did but they
    cannot be given (the model returned none, or they cannot be told apart from stripped reasoning)."""

    text: str
    finish_reason: str = "stop"
    logprobs: list[dict[str, Any]] | None = None


@dataclass
class Result:
    """One answered request. The token fields are what the response's `usage` reports (FR-1.12): the served answer as
    if its model had answered alone, so a client's own accounting matches what one model call would have cost it.
    Ratchet's own overhead (the other samples, audits, shadows, arbitrations) is in `usage_breakdown`, by call, and in
    `cost_usd`. `fingerprint` is the response's `system_fingerprint` (`fingerprint()`).

    `choices` are the response's choices, as many as the client's `n` (FR-1.14; fewer only when a teacher returned
    fewer); `text` and `finish_reason` are choice 0's, and the token fields add up every choice's completion (the prompt
    once), as OpenAI counts them. A Result built without choices gets one from `text` and `finish_reason`.

    `student_cost_usd` and `teacher_cost_usd` split `cost_usd` (FR-15.1): the student's samples (live and shadow) and the
    teacher's calls (escalation, audit, arbitration, pinned). `cost_usd` is still summed call by call as before, so it
    equals their sum up to float rounding.

    `request_id` is the id of the request's row in the ledger (`Ledger.log_request`'s return value): what a receipt
    or a replay names the request by. It is not `RequestMeta.request_id`, the caller's own id for the request."""

    text: str
    served_by: str
    version: int | None
    confidence: float | None
    escalated: bool
    cost_usd: float
    baseline_usd: float
    latency_ms: float
    model: str
    route: str = ""  # one of ROUTES
    finish_reason: str = "stop"  # the served answer's, one of CLIENT_FINISH ("length": cut off, or over max_tokens)
    prompt_tokens: int = 0
    completion_tokens: int = 0  # reasoning tokens included, as OpenAI counts them for its reasoning models
    reasoning_tokens: int = 0
    cached_tokens: int = 0  # the part of prompt_tokens the served model read from its prompt cache
    fingerprint: str = ""
    usage_breakdown: dict[str, dict[str, int] | None] = field(default_factory=dict)  # USAGE_PARTS -> {"in", "out"}
    degraded: str | None = None  # TEACHER_UNAVAILABLE when the student's below-threshold answer stood in for the teacher
    choices: list[Choice] = field(default_factory=list)
    student_cost_usd: float = 0.0  # cost_usd's student part (FR-15.1)
    teacher_cost_usd: float = 0.0  # cost_usd's teacher part
    request_id: int | None = None  # the ledger row (`requests.id`) of this request; not RequestMeta.request_id

    def __post_init__(self) -> None:
        if not self.choices:
            self.choices = [Choice(self.text, self.finish_reason)]

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _client_finish(c: Completion) -> str:
    return c.finish_reason if c.finish_reason in CLIENT_FINISH else "stop"


def _choices(task: tasks.Task, served: list[Completion], params: GenParams | None) -> list[Choice]:
    """The response's choices for the served completions: each cleaned as it is served, with its token logprobs when the
    client asked for them (`params.logprobs`; at most `params.top_logprobs` alternatives per token). String work only,
    and the logprobs only for a client that asked."""
    asked = params is not None and bool(params.logprobs)
    top = params.top_logprobs if params is not None else None
    out = []
    for c in served:
        text = tasks.clean_answer(task, c.text)
        out.append(Choice(text, _client_finish(c), served_logprobs(c, text, top) if asked else None))
    return out


def fingerprint(*parts: object) -> str:
    """A `system_fingerprint`: 'fp_' and the first 12 hex digits of SHA-256 over the parts joined by '|'. The router
    passes what decides an answer: for the student its role, model (adapter), version, training-set hash and threshold;
    for the teacher its role and model. So it stays the same within a version and changes when another version serves
    (a promotion, a rollback) or a version's threshold changes, as OpenAI's changes with its backend configuration."""
    return "fp_" + hashlib.sha256("|".join(map(str, parts)).encode("utf-8", "replace")).hexdigest()[:12]


def _usage(messages: list[dict[str, Any]], served: list[Completion], choices: list[Choice]) -> dict[str, int]:
    """`Result`'s token fields for the served answer (FR-1.12): the prompt once, and every served choice's completion
    (FR-1.14). The counts are the served model's own; a count it did not report (0) is estimated at about 4 characters a
    token, from the messages as JSON and from each answer as served. Reasoning tokens are part of a completion and
    cached tokens part of the prompt, so neither exceeds its total. No I/O; the JSON is built only when the prompt count
    is missing."""
    first = served[0]
    prompt = first.in_tokens or approx_tokens(json.dumps(messages, ensure_ascii=False, default=str))
    completion = reasoning = 0
    for c, choice in zip(served, choices, strict=True):
        out = c.out_tokens or (approx_tokens(choice.text) if choice.text else 0)
        completion += out
        reasoning += min(c.reasoning_tokens, out)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "cached_tokens": min(first.cached_tokens, prompt),
    }


def _tokens(c: Completion | None) -> dict[str, int] | None:
    """One call's tokens as its model reported them."""
    return None if c is None else {"in": c.in_tokens, "out": c.out_tokens}


def _sampled(samples: list[Completion] | None) -> dict[str, int] | None:
    """One sampling call's tokens: the prompt once (the choices share it) and the output of every choice."""
    return None if not samples else {"in": samples[0].in_tokens, "out": sum(s.out_tokens for s in samples)}


def _breakdown(
    served: list[Completion],
    samples: list[Completion] | None = None,
    audit: Completion | None = None,
    shadow: list[Completion] | None = None,
    arbitration: Completion | None = None,
) -> dict[str, dict[str, int] | None]:
    """`Result.usage_breakdown`: every model call of the request by `USAGE_PARTS`. When the student served, `served` is
    the choices taken from `student_samples`; when the teacher did, it is its own call, every choice of it."""
    return {
        "student_samples": _sampled(samples),
        "served": _sampled(served),
        "audit": _tokens(audit),
        "shadow": _sampled(shadow),
        "arbitration": _tokens(arbitration),
    }


def _task(task: str | tasks.Task | None) -> tasks.Task:
    return task if isinstance(task, tasks.Task) else tasks.get(task)


class TeacherAnswer(NamedTuple):
    """A teacher call's answer (`Ratchet._call_teacher`): the completion (choice 0, the only one that may be stored),
    whether it was stored as a training example, the configured model that wrote it (RATCHET_TEACHER_MODEL, or
    RATCHET_TEACHER_FALLBACK_MODEL), whether that was the fallback teacher, and every choice of the call (one, or the
    client's n when the answer is served). The model is the one Ratchet asked for and licensed at startup, never the
    response's own `model` string, which an upstream may spell differently."""

    completion: Completion
    stored: bool
    model: str
    fallback: bool
    choices: tuple[Completion, ...] = ()


class Ratchet:
    """The cascade. `clock` drives the breakers (tests pass a fake one). `on_adapter_missing(version)` is called when
    vLLM answers 404 for a version's adapter; live mode wires `AdapterReloader.request` to it. `fallback_teacher` is the
    provider for RATCHET_TEACHER_FALLBACK_MODEL (default: `teacher`, the same endpoint); it is used only when that model
    is set."""

    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        teacher: Provider,
        student_resolver: StudentResolver,
        seed: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_adapter_missing: Callable[[Version], object] | None = None,
        fallback_teacher: Provider | None = None,
    ):
        self.s = settings
        settings.check_reserved()  # a setting reserved for a feature not built yet stops startup here (ADR 0024)
        self.ledger = ledger
        self.teacher = teacher
        self.resolve_student = student_resolver
        self.rng = random.Random(seed)
        # INV-1: every teacher that may write an answer is licensed here, so a closed or unknown model stops startup. An
        # example records the license of the teacher that wrote it (`_store_example`), looked up by configured model.
        self.licenses: dict[str, licenses.TeacherLicense] = {
            settings.teacher_model: licenses.require_distillable(settings.teacher_model)
        }
        self.teacher_license = self.licenses[settings.teacher_model]
        self.fallback_model: str | None = settings.teacher_fallback_model or None
        self.fallback_teacher: Provider | None = None
        if self.fallback_model is not None:
            self.licenses[self.fallback_model] = licenses.require_distillable(self.fallback_model)
            self.fallback_teacher = fallback_teacher if fallback_teacher is not None else teacher
        # RATCHET_ON_TEACHER_FAILURE; a bad value stops startup here.
        self.serve_student_on_failure = settings.serve_student_on_teacher_failure()
        settings.checked_canary_audit_rate()  # RATCHET_CANARY_AUDIT_RATE outside (0, 1] stops startup here (ADR 0025)
        # RATCHET_UNKNOWN_TASKS: distill, teacher or reject (ADR 0022); a bad value stops startup here.
        self.unknown_tasks = settings.unknown_task_policy()
        self.breaker_policy = settings.student_breaker()  # a bad RATCHET_STUDENT_BREAKER_* value stops startup here
        # What each role sends of a client's parameters (ADR 0017); a bad RATCHET_*_PARAMS or cap stops startup here.
        self.gen = settings.gen_policy()
        self._plain = self.gen.plan(None)  # the plan of a request without parameters (Scout, the simulator)
        self.clock = clock
        self.on_adapter_missing = on_adapter_missing
        # Failed upstream calls since start (health()). Scout calls handle from several threads, hence the lock.
        self.student_errors = 0  # student calls that raised; the teacher answered instead
        self.audit_errors = 0  # teacher audits that raised; the student's answer was served anyway (INV-15)
        self.shadow_errors = 0  # shadow samples or arbitrations that raised; the request never noticed
        self.degraded_answers = 0  # escalations answered by the student because the teacher was unavailable
        self.fallback_answers = 0  # answers the fallback teacher wrote because the primary was unavailable
        self._errors_lock = threading.Lock()
        self._skipped: Counter[str] = Counter()  # teacher answers not stored, by reason (throttles `skip_example`)
        # One breaker per (role, endpoint), made on first use: live and canary on one endpoint share 'student'; shadow
        # calls have their own ('shadow'), so a bad staging adapter cannot open the breaker live traffic depends on.
        self._breakers: dict[tuple[str, str], Breaker] = {}
        self._breakers_lock = threading.Lock()
        # The teachers' breakers (ADR 0019), made here so health() shows them from the start; a bad
        # RATCHET_TEACHER_BREAKER_* value stops startup. Every teacher call goes through one (`_teacher_complete`).
        self.teacher_policy = settings.teacher_breaker()
        self.teacher_breaker = self._teacher_breaker("teacher", teacher)
        self.fallback_breaker = (
            self._teacher_breaker("fallback_teacher", self.fallback_teacher) if self.fallback_teacher is not None else None
        )

    def _teacher_breaker(self, name: str, provider: Provider) -> Breaker:
        """A teacher's breaker ('teacher' or 'fallback_teacher') with RATCHET_TEACHER_BREAKER_*, listed in health()."""
        p = self.teacher_policy
        breaker = Breaker(
            name,
            p.failure_threshold,
            p.open_s,
            p.max_open_s,
            clock=self.clock,
            on_change=self._circuit_event,
            probe_timeout_s=_probe_timeout(provider),
        )
        self._breakers[(name, _endpoint(provider))] = breaker
        return breaker

    def health(self) -> dict[str, Any]:
        """Failed upstream calls since start, by kind; answers a stand-in wrote because the teacher was unavailable
        (`degraded_answers`, `fallback_answers`); and each breaker's `snapshot()` by name, the teachers' included (for
        /readyz and /metrics)."""
        with self._errors_lock:
            out: dict[str, Any] = {
                "student_errors": self.student_errors,
                "audit_errors": self.audit_errors,
                "shadow_errors": self.shadow_errors,
                "degraded_answers": self.degraded_answers,
                "fallback_answers": self.fallback_answers,
            }
        with self._breakers_lock:
            breakers = list(self._breakers.values())
        out["breakers"] = {b.name: b.snapshot() for b in breakers}
        return out

    def _breaker(self, role: str, provider: Provider) -> Breaker:
        """The breaker for `role` ('student' or 'shadow') on `provider`'s endpoint. The first endpoint of a role names
        its breaker after the role ('student'); another endpoint in the same role gets 'student-2', and so on."""
        key = (role, _endpoint(provider))
        breaker = self._breakers.get(key)
        if breaker is None:
            with self._breakers_lock:
                breaker = self._breakers.get(key)
                if breaker is None:
                    n = sum(1 for r, _ in self._breakers if r == role)
                    p = self.breaker_policy
                    breaker = Breaker(
                        role if n == 0 else f"{role}-{n + 1}",
                        p.failure_threshold,
                        p.open_s,
                        p.max_open_s,
                        clock=self.clock,
                        on_change=self._circuit_event,
                        probe_timeout_s=_probe_timeout(provider),
                    )
                    self._breakers[key] = breaker
        return breaker

    def _circuit_event(self, breaker: Breaker, old: str, new: str, reason: str) -> None:
        self.ledger.event("circuit", f"{breaker.name}: {old}->{new} {reason}")

    def _request_reload(self, version: Version) -> None:
        """Ask for a lost adapter to be loaded again. The request that hit the 404 escalates either way, so a failing
        callback is ignored (INV-7)."""
        if self.on_adapter_missing is None:
            return
        try:
            self.on_adapter_missing(version)
        except Exception:  # noqa: BLE001 - never fail the request over a reload request
            pass

    def _count_failure(self, counter: str, kind: str, detail: str) -> None:
        """Add one to `counter` and write a `kind` event on its 1st, 10th, 100th and 1000th failure."""
        with self._errors_lock:
            n = getattr(self, counter) + 1
            setattr(self, counter, n)
        if n in EVENT_ON_FAILURE:
            self.ledger.event(kind, detail)

    # ------------------------------------------------------------------
    def _pick_version(self) -> Version | None:
        live = self.ledger.live()
        canary = self.ledger.by_state("canary")
        if canary and self.rng.random() * 100 < canary.traffic_pct:
            return canary
        return live  # None on cold start -> teacher

    def plan(self, params: GenParams | None, n: int = 1) -> GenPlan:
        """The parameters of each model call for a request with these client parameters and `n` choices
        (`GenPolicy.plan`)."""
        return self._plain if params is None and n == 1 else self.gen.plan(params, n)

    def _teacher_complete(
        self, messages: list[dict[str, Any]], gen: GenParams, fallback: bool = False, n: int = 1
    ) -> list[Completion]:
        """One call to the primary teacher (or, with `fallback`, the fallback teacher) through its breaker, the way
        `_sample` calls the student: its choices, at least one and at most `n` (a server that returns fewer than `n` gives
        fewer). Raises `CircuitOpen` (503 `teacher_circuit_open`, with the time until the next probe as `retry_after`)
        without calling while the breaker is open, so a request that needs the teacher's answer fails at once instead of
        waiting out the deadline, and an optional call (audit, arbitration) is skipped. The provider's retries come
        first: one failed call is one failure here, counted as `resilience.endpoint_down` says (a timeout, no connection,
        a 5xx, 408, 429; another 4xx is the teacher answering; a non-HTTP error counts neither way)."""
        if fallback:
            assert self.fallback_teacher is not None and self.fallback_model is not None and self.fallback_breaker is not None
            provider, model, breaker = self.fallback_teacher, self.fallback_model, self.fallback_breaker
        else:
            provider, model, breaker = self.teacher, self.s.teacher_model, self.teacher_breaker
        if not breaker.allow():
            raise CircuitOpen(breaker.name, retry_after=breaker.retry_in(), code=TEACHER_CIRCUIT_OPEN)
        try:
            out = provider.complete(
                messages, model, n=n, temperature=gen.temperature or 0.0, logprobs=bool(gen.logprobs), params=gen
            )
            if not out:
                raise UpstreamError(502, "the teacher returned no choices", code=BAD_RESPONSE)
        except BaseException as e:
            down = endpoint_down(e)
            if down is None:
                breaker.release()
            else:
                breaker.record(not down, describe_failure(e))
            raise
        breaker.record(True)
        return out[:n]

    def _ask_teacher(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        gen: GenParams,
        visible_max: int | None,
        fallback: bool = False,
        n: int = 1,
    ) -> list[Completion]:
        """The teacher's answer under `gen` (the fallback teacher's with `fallback`): one choice, or up to `n` for a served
        answer. When the reasoning headroom let the teacher write more than the client's `max_tokens` (`visible_max`), a
        choice whose visible part (`tasks.clean_answer`) is longer than that, by `approx_tokens`, is marked `length`: over
        the client's budget, so it is not stored (INV-16) and the client is told. The text is not cut, because the count
        is an estimate. A choice that ends in a tool call (only when an operator allows `tools` for the teacher) raises
        `UpstreamError` 502 `tool_calls_unsupported`: the provider does not read tool calls yet, so its text is not the
        whole answer, and a response claiming `tool_calls` without them breaks OpenAI clients. Nothing is stored; an audit
        or arbitration counts it as a failed optional call. The teacher answered, so its breaker counts the call as a
        success."""
        cs = self._teacher_complete(messages, gen, fallback, n)
        if any(c.finish_reason == "tool_calls" for c in cs):
            raise UpstreamError(
                502, "the teacher answered with a tool call, which the gateway cannot return yet", code=TOOL_CALLS_UNSUPPORTED
            )
        if visible_max is not None:
            cs = [
                replace(c, finish_reason="length")
                if c.finish_reason in COMPLETE and approx_tokens(tasks.clean_answer(task, c.text)) > visible_max
                else c
                for c in cs
            ]
        return cs

    def _serve_teacher(
        self, task: tasks.Task, messages: list[dict[str, Any]], gen: GenParams, visible_max: int | None, n: int = 1
    ) -> tuple[list[Completion], bool]:
        """The teacher answer a request needs (escalation, cold start, pinned, teacher-only), up to `n` choices, and
        whether the fallback teacher wrote it. The fallback teacher (RATCHET_TEACHER_FALLBACK_MODEL) is asked only when
        the primary is unavailable (`resilience.teacher_unavailable`: its retries failed, or its breaker is open); never
        for an answer about the request itself (a 400 such as `context_length_exceeded`) or a tool call. When the fallback
        fails as an upstream call too, the primary's error is raised: it is the configured teacher, and its breaker says
        when to come back. Anything else the fallback raises (a bug) propagates as it is."""
        try:
            return self._ask_teacher(task, messages, gen, visible_max, n=n), False
        except Exception as e:
            if self.fallback_teacher is None or not teacher_unavailable(e):
                raise
            primary = e
        try:
            cs = self._ask_teacher(task, messages, gen, visible_max, fallback=True, n=n)
        except httpx.HTTPError:
            raise primary  # noqa: B904 - the primary's error is the one the client gets; the fallback's is its context
        self._count_failure("fallback_answers", "fallback_teacher", f"{task.name}: {describe_failure(primary)}")
        return cs, True

    def _teacher_model(self, fallback: bool) -> str:
        """The configured model of the teacher that answered."""
        return self.fallback_model if fallback and self.fallback_model is not None else self.s.teacher_model

    def _call_teacher(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        plan: GenPlan | None = None,
        role: str = "serve",
        meta: RequestMeta | None = None,
    ) -> TeacherAnswer:
        """The teacher's answer, under the plan's `serve` parameters (with the client's `plan.n` choices) or, to audit or
        arbitrate, its `check` ones (one answer), and whether it was stored as a training example (`_store_example`).
        Only choice 0 is ever stored, so one request adds or bumps at most one example and `seen` keeps counting
        requests (FR-1.14). Only a served answer may come from the fallback teacher (`_serve_teacher`); an audit or
        arbitration is a release signal and a label, so it is the primary's or nothing."""
        plan = plan or self._plain
        if role == "serve":
            gen = plan.serve
            cs, fallback = self._serve_teacher(task, messages, gen, plan.visible_max, plan.n)
        else:
            gen, fallback = plan.check, False
            cs = self._ask_teacher(task, messages, gen, plan.visible_max)
        stored = self._store_example(task, messages, cs[0], gen, meta, fallback=fallback)
        return TeacherAnswer(cs[0], stored, self._teacher_model(fallback), fallback, tuple(cs))

    def _store_example(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        c: Completion,
        gen: GenParams | None = None,
        meta: RequestMeta | None = None,
        *,
        fallback: bool = False,
    ) -> bool:
        """Store a teacher answer as a training example, the only place that does (INV-2). Only a complete, valid answer
        is stored (INV-16, `answer_problem`): cleaned the way it is served (`tasks.clean_answer`), then, with
        `Settings.redact_pii`, with identifiers in the messages, the answer and the stored conditions replaced (INV-12).
        The conditions are the `STORED_PARAM_KEYS` of `gen`, the parameters the teacher answered under. An answer written
        under a client's label-altering sampling (`GenParams.unstorable`: `logit_bias`, a penalty, a temperature above 1,
        a vendor field) is not stored either: the client may choose the answer it gets, not the label everyone's student
        learns (threat model T2). Returns True when an example was inserted or a duplicate's `seen` bumped; False, with a
        throttled `skip_example` event, when not. A request whose `meta.storable` is False stores nothing and is not a
        skip. A `fallback` teacher's answer is stored only with RATCHET_STORE_FALLBACK_TEACHER=1 (so the golden labels
        stay the primary teacher's), and then with its own model and license (INV-1). A task with `store=False` stores
        nothing either, and that is not a skip. An answer that is not valid under the task's spec (`Task.check`: a
        required field missing, a label outside its vocabulary) is served but skipped, with the field's path as the
        reason (FR-2.14)."""
        if (meta is not None and not meta.storable) or not task.store:
            return False
        if fallback and not self.s.store_fallback_teacher:
            return False
        problem = (gen.unstorable() if gen is not None else None) or answer_problem(task, c)
        if problem is not None:
            self._skip_example(task, problem)
            return False
        clean = tasks.clean_answer(task, c.text)
        conditions = target_params({k: getattr(gen, k) for k in STORED_PARAM_KEYS}) if gen is not None else None
        stored, found = messages, Counter()
        if self.s.redact_pii:  # the teacher saw the original; the training set gets placeholders
            stored, clean = redact_messages(messages, found), redact(clean, found)
            if conditions:
                conditions = redact_json(conditions, found)
            # A placeholder can break a structured answer (a card-like number outside quotes, an identifier inside a
            # field the spec checks): never store that.
            if found and task.check(clean) is not None:
                self._skip_example(task, "invalid after redaction")
                return False
        model = self._teacher_model(fallback)
        lic = self.licenses.get(model) or licenses.require_distillable(model)  # licensed at startup; never unlicensed
        self.ledger.add_example(
            task.name,
            stored,
            clean,
            model,
            lic.name,
            lic.url,
            redactions=sum(found.values()),
            params=conditions,
        )
        return True

    def _skip_example(self, task: tasks.Task, reason: str) -> None:
        """Count a teacher answer that was not stored, and write a `skip_example` event on the 1st, 10th, 100th and
        1000th time for that reason. The detail is the task and the reason, never the answer (INV-12)."""
        with self._errors_lock:
            self._skipped[reason] += 1
            n = self._skipped[reason]
        if n in EVENT_ON_FAILURE:
            self.ledger.event("skip_example", f"{task.name}: {reason}")

    def _arbitrate(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        shadow: Version,
        plan: GenPlan | None = None,
        meta: RequestMeta | None = None,
    ) -> tuple[Completion, bool] | None:
        """The teacher settles a disagreement between the shadow and the served answer: its answer and whether it was
        stored. A failed call returns None and leaves the disagreement unarbitrated; the request it belongs to is
        already answered (INV-15). An open teacher breaker skips the call, as a failure would, without counting it."""
        try:
            ruling = self._call_teacher(task, messages, plan, "arbitrate", meta)
        except CircuitOpen:  # the teacher failed too often lately: not called, not counted again
            return None
        except Exception as e:  # optional call: count it, never fail the request
            self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} arbitration: {describe_failure(e)}")
            return None
        return ruling.completion, ruling.stored

    def _cost(self, model: str, c: Completion, n_out: int = 1) -> float:
        return self.s.price(model).cost(c.in_tokens, c.out_tokens * n_out)

    def _call_cost(self, model: str, choices: list[Completion]) -> float:
        """The price of one call that returned these choices: the prompt once, every choice's output."""
        return self.s.price(model).cost(choices[0].in_tokens, sum(c.out_tokens for c in choices))

    def _formula_baseline(self, messages: list[dict[str, Any]], student_c: Completion, choices: list[Completion]) -> float:
        """What the teacher alone would have cost for a student answer, by the token formula: the student's prompt
        tokens (estimated from the messages when it reported none), and each choice's output tokens plus the assumed
        RATCHET_TEACHER_REASONING_TOKENS, at the teacher's price. The baseline of a request the student served; the
        replay calibrates it against audited rows (`requests.baseline_est_usd`, ADR 0028)."""
        in_t = student_c.in_tokens or approx_tokens(str(messages))
        out_t = sum(s.out_tokens for s in choices) + len(choices) * self.s.teacher_reasoning_tokens
        return self.s.price(self.s.teacher_model).cost(in_t, out_t)

    def threshold(self, version: Version, model: str | None = None, task: tasks.Task | str | None = None) -> float:
        """Each version carries its own threshold, calibrated on the golden set at training time.
        The hosted untuned fallback model was never calibrated, so it uses the global default. For a `task` whose spec
        changed since the version was scored (`spec_changed`) it is SERVE_NOTHING: the threshold was calibrated under
        other rules, so the teacher answers until a night re-scores the version under the new spec (FR-2.15)."""
        if model is not None and model == self.s.fallback_student_model:
            return self.s.confidence_threshold
        if task is not None and self.spec_changed(version, task):
            return SERVE_NOTHING
        return float(version.eval.get("threshold", self.s.confidence_threshold))

    @staticmethod
    def spec_changed(version: Version, task: tasks.Task | str) -> bool:
        """Whether `version` was scored under another spec of `task` than the current one: its eval records the task's
        `spec_hash` (`eval['spec_hashes']`, written by `run_night`) and it differs. A version without a record for the
        task (scored before hashes were recorded, or with no golden rows of it) has nothing to compare."""
        recorded = version.eval.get("spec_hashes")
        if not isinstance(recorded, dict):
            return False
        t = _task(task)
        h = recorded.get(t.name)
        return h is not None and h != t.spec_hash

    def _sample(
        self,
        version: Version,
        messages: list[dict[str, Any]],
        role: str = "student",
        gen: GenParams | None = None,
        n: int | None = None,
    ) -> tuple[list[Completion], float, str]:
        """The student's samples under `gen` (the plan's `student` parameters), `n` of them (`Settings.samples` when
        None; more only to fill a client's n choices), through the `role` breaker of its endpoint. Raises `CircuitOpen`
        without calling when that breaker is open, and `AdapterMissing` when vLLM answers 404 naming the version's adapter
        (an endpoint restart forgets runtime-loaded adapters; that 404 is the endpoint answering, so it does not count
        against it)."""
        provider, model = self.resolve_student(version)
        breaker = self._breaker(role, provider)
        if not breaker.allow():
            raise CircuitOpen(breaker.name)
        try:
            samples = provider.complete(
                messages,
                model,
                n=self.s.samples if n is None else n,
                params=self._plain.student if gen is None else gen,
                **STUDENT_SAMPLING,
            )
            if not samples:
                raise UpstreamError(502, "the student returned no choices", code=BAD_RESPONSE)
        except BaseException as e:
            down = endpoint_down(e)
            if down is None:
                breaker.release()
            else:
                breaker.record(not down, describe_failure(e))
            if (
                isinstance(e, UpstreamError)
                and e.status == 404
                and model == version.adapter_uri
                and names_model(e.message, model)
            ):
                raise AdapterMissing(model, e.message) from e
            raise
        breaker.record(True)
        cost = self.s.price("student").cost(samples[0].in_tokens, sum(s.out_tokens for s in samples))
        return samples, cost, model

    def _teacher_only(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        client: str | None = None,
        plan: GenPlan | None = None,
        params: GenParams | None = None,
        route: str = "pinned",
    ) -> Result:
        """The teacher alone: a pinned task, a request the student cannot take (`teacher_only`), or an unregistered
        task under RATCHET_UNKNOWN_TASKS=teacher (`route` `unknown_task`). The request is logged, but the answer is never
        stored as a training example, so it cannot leak into a student that was never meant to do it (INV-10), and the
        student is never sampled. When the primary teacher is unavailable, the fallback teacher may answer (route
        `fallback_teacher`); the student never does, so this path never degrades. The baseline is the primary teacher's
        price for the same tokens. `params` are the client's, for the logprobs it asked to get back."""
        plan = plan or self._plain
        served, fallback = self._serve_teacher(task, messages, plan.serve, plan.visible_max, plan.n)
        c = served[0]
        model = self._teacher_model(fallback)
        route = "fallback_teacher" if fallback else route
        cost = self._call_cost(model, served)
        baseline = self._call_cost(self.s.teacher_model, served) if fallback else cost
        request_id = self.ledger.log_request(
            task=task.name,
            served_by="teacher",
            student_version=None,
            confidence=None,
            escalated=0,
            audited=0,
            audit_agree=None,
            shadow_version=None,
            shadow_serve=None,
            shadow_agree=None,
            latency_ms=c.latency_ms,
            cost_usd=cost,
            baseline_cost_usd=baseline,
            student_cost_usd=0.0,  # the student is never sampled here: every cent is the teacher's (FR-15.1)
            teacher_cost_usd=cost,
            student_in_tokens=0,
            student_out_tokens=0,
            client=client,
            route=route,
            stored=0,
        )
        choices = _choices(task, served, params)
        return Result(
            text=choices[0].text,
            served_by="teacher",
            version=None,
            confidence=None,
            escalated=False,
            cost_usd=cost,
            baseline_usd=baseline,
            latency_ms=c.latency_ms,
            model=c.model,
            route=route,
            finish_reason=choices[0].finish_reason,
            **_usage(messages, served, choices),
            fingerprint=fingerprint("teacher", model),
            usage_breakdown=_breakdown(served),
            choices=choices,
            teacher_cost_usd=cost,
            request_id=request_id,
        )

    def teacher_only(
        self,
        messages: list[dict[str, Any]],
        task: str | tasks.Task | None = None,
        client: str | None = None,
        params: GenParams | None = None,
        meta: RequestMeta | None = None,
        n: int = 1,
    ) -> Result:
        """A request the student cannot take, such as one with an image part the teacher reads
        (RATCHET_TEACHER_MODALITIES): the pinned path, so the teacher answers (or the fallback teacher, when the primary
        is unavailable), the request is logged, and nothing is stored as a training example. No student, audit or shadow
        call, so it never degrades. `n` choices, as for `handle`. An unregistered task under RATCHET_UNKNOWN_TASKS=reject
        raises `tasks.UnknownTask`; under `teacher` it is logged with route `unknown_task`, as in `handle`."""
        task = _task(task)
        self._admit(task, client)
        route = "unknown_task" if task.source == "adhoc" and self.unknown_tasks == "teacher" else "pinned"
        return self._teacher_only(task, messages, client, self.plan(params, n), params, route=route)

    def task_for(self, task: str | tasks.Task | None, client: str | None = None) -> tasks.Task:
        """The task a request names, as the gateway admits it before any quota or model call: a registered task, or for
        an unregistered name a task without fields made up for it (`tasks.get`), which RATCHET_UNKNOWN_TASKS decides
        about (`reject` raises `tasks.UnknownTask`, listing the tasks this client may use). A name that is not a task
        name (an X-Ratchet-Task header; metadata is checked earlier) means 'default' under `distill`, as in v0.4, and
        raises `tasks.UnknownTask` under `teacher` and `reject`, so it cannot become 'default' training data there. A
        task with `clients` raises `tasks.TaskForbidden` for any other API key, and for a request without one. Lookups
        only."""
        if isinstance(task, str) and task and not tasks.valid_name(task) and self.unknown_tasks != "distill":
            raise tasks.UnknownTask(task, tasks.names_for(client), invalid=True)
        t = _task(task)
        self._admit(t, client)
        if not t.allows(client):
            raise tasks.TaskForbidden(t.name)
        return t

    def _admit(self, task: tasks.Task, client: str | None = None) -> None:
        """Raise `tasks.UnknownTask` for an unregistered task under RATCHET_UNKNOWN_TASKS=reject."""
        if task.source == "adhoc" and self.unknown_tasks == "reject":
            raise tasks.UnknownTask(task.name, tasks.names_for(client))

    # ------------------------------------------------------------------
    def handle(
        self,
        messages: list[dict[str, Any]],
        task: str | tasks.Task | None = None,
        client: str | None = None,
        params: GenParams | None = None,
        meta: RequestMeta | None = None,
        n: int = 1,
    ) -> Result:
        """Answer one request. `task` is a task name (or a `tasks.Task`); `client` is the API key name that sent it (None
        for internal calls and open gateways); `params` are the client's generation parameters, which each call gets as
        its role allows (`GenPolicy`, ADR 0017); `meta` is what else the caller knows (`RequestMeta`); `n` is how many
        choices the client wants (FR-1.14, ADR 0020). Scout and the simulator pass only messages and a task name.

        For `n` > 1 the student draws `max(Settings.samples, n)` samples. Its confidence is scored on the first
        `Settings.samples` of them, exactly as calibrated, and it serves only when that is at or above the threshold and
        at least `n` samples share the majority answer (`confidence.majority`); choice 0 is then the sample a one-choice
        request would serve. A confident student that cannot fill `n` choices escalates (route `n_choices`). The teacher
        writes `n` choices (at `CHOICES_TEMPERATURE` when the client set no temperature), and only choice 0 can become a
        training example. Audits, arbitrations and the shadow judge choice 0, so release statistics do not depend on `n`.

        When the teacher answer an escalation needs cannot be had (ADR 0019), the fallback teacher answers if one is set
        (route `fallback_teacher`), else, with RATCHET_ON_TEACHER_FAILURE=serve_student, the student's own valid answer
        (route `degraded`; for `n` > 1 only when `n` of its samples agree), else the teacher's error is raised for the
        gateway to map (INV-7).

        A pinned task, and an unregistered one under RATCHET_UNKNOWN_TASKS=teacher (route `unknown_task`), go to the
        teacher alone (`_teacher_only`); under `reject` an unregistered task raises `tasks.UnknownTask`. The client
        allow-list (`Task.clients`) is the gateway's check (`task_for`): internal callers pass no key."""
        task = _task(task)
        plan = self.plan(params, n)
        self._admit(task, client)
        if task.source == "adhoc" and self.unknown_tasks == "teacher":  # not registered: never distilled (ADR 0022)
            return self._teacher_only(task, messages, client, plan, params, route="unknown_task")
        if task.pinned:
            return self._teacher_only(task, messages, client, plan, params)
        version = self._pick_version()
        cost = 0.0
        # cost's two parts (FR-15.1), added beside it call by call so `cost` itself is summed exactly as before, and the
        # student calls' tokens, which price the hosted fallback student in the cost report (ratchet/cost.py).
        student_cost = teacher_cost = 0.0
        student_in = student_out = 0
        latency = 0.0
        confidence = None
        escalated = False
        audited = audit_agree = 0
        served: Completion
        served_by = "teacher"
        teacher_c: Completion | None = None
        student_c: Completion | None = None
        teacher_failed = False  # an audit already failed: arbitration would only wait out the same outage
        route = "cold_start"
        stored = False  # a teacher answer became (or bumped) a training example in this request
        samples: list[Completion] | None = None  # the student's, when it answered
        audit_c: Completion | None = None
        s_samples: list[Completion] | None = None  # the shadow's, when it answered
        arbitration_c: Completion | None = None
        fp = ""
        degraded: str | None = None
        # The designed probability that a teacher label of the student's answer is observed (requests.label_p, FR-16.5):
        # the audit rate this request was drawn at when the student served, 1.0 when it escalated after answering.
        label_p: float | None = None
        # What the ledger replay reads besides it (FR-16.5, ADR 0028): the threshold the student's answer was held to,
        # and whether that answer agreed with the primary teacher's valid answer from this request's escalation or random
        # audit. Never from a shadow arbitration (not a random draw) or a stand-in teacher. Logging only: no call, no draw.
        threshold_used: float | None = None
        student_agree: int | None = None
        picked: list[Completion] | None = None  # the student's n choices, when n of its samples agree
        served_choices: list[Completion] = []  # every choice the client gets; choice 0 is `served`

        sc = None
        if version is not None:
            try:
                samples, c, model_used = self._sample(version, messages, gen=plan.student, n=max(self.s.samples, plan.n))
            except CircuitOpen:  # the endpoint failed too often lately: not called, not counted again
                escalated, route = True, "student_breaker"
            except Exception as e:  # student endpoint down or erroring: the teacher still answers
                if isinstance(e, AdapterMissing):
                    self._request_reload(version)
                # The class, status and code only: an endpoint's error message can quote the request (INV-12).
                self._count_failure("student_errors", "student_error", f"v{version.id}: {describe_failure(e)}")
                escalated, route = True, "student_error"
            else:
                latency += max(s.latency_ms for s in samples)
                cost += c
                student_cost += c
                student_in += samples[0].in_tokens
                student_out += sum(s.out_tokens for s in samples)
                # Scored on the calibrated number of samples; the rest are drawn only to fill n choices (ADR 0020).
                sc = score(task, samples[: max(1, self.s.samples)])
                picked = majority(task, sc, samples, plan.n)
                confidence = sc.confidence
                student_c = sc.answer
        if version is not None and sc is not None:
            threshold = threshold_used = self.threshold(version, model_used, task)
            # A sample set with no valid answer is never served, whatever the threshold (confidence.Scored.valid).
            if sc.valid and sc.confidence >= threshold and picked is not None:
                served, served_by, route, served_choices = sc.answer, "student", "student", picked
                fp = fingerprint("student", model_used, version.id, version.dataset_hash, threshold)
                # Audit focusing (ADR 0025): a canary may be audited at its own rate. Still one draw per served answer.
                rate = self.s.audit_rate
                if version.state == "canary" and self.s.canary_audit_rate is not None:
                    rate = self.s.canary_audit_rate
                label_p = rate
                if self.rng.random() < rate:  # audit = quality signal + free training data
                    try:
                        audit = self._call_teacher(task, messages, plan, "audit", meta)
                    except CircuitOpen:  # the teacher failed too often lately: not called, not counted again
                        teacher_failed = True
                    except Exception as e:  # optional call: the student's answer is served (INV-15)
                        teacher_failed = True
                        self._count_failure("audit_errors", "audit_error", f"{task.name}: {describe_failure(e)}")
                    else:
                        teacher_c, stored = audit.completion, audit.stored
                        audit_c = teacher_c
                        t = self._cost(self.s.teacher_model, teacher_c)
                        cost += t
                        teacher_cost += t
                        # A cut-off, empty or invalid teacher answer measures nothing: not an audit (INV-16).
                        if answer_problem(task, teacher_c) is None:
                            audited, audit_agree = 1, int(task.agree(sc.answer.text, teacher_c.text))
                            student_agree = audit_agree
            elif sc.valid and sc.confidence >= threshold:  # confident, but fewer than n samples agree
                escalated, route, label_p = True, "n_choices", 1.0
            else:
                escalated, route, label_p = True, "confidence", 1.0
        if served_by == "teacher":
            try:
                answer = self._call_teacher(task, messages, plan, "serve", meta)
            except Exception as e:  # no teacher answer: degraded if allowed, else the gateway maps the error (INV-7)
                if not self._may_degrade(sc, params, e, picked):
                    raise
                # The student's own valid answer stands in. Not audited, so it never counts toward release statistics.
                assert version is not None and sc is not None and picked is not None
                teacher_failed = True
                served, served_by, route, degraded = sc.answer, "student", "degraded", TEACHER_UNAVAILABLE
                served_choices = picked
                threshold = self.threshold(version, model_used, task)
                fp = fingerprint("student", model_used, version.id, version.dataset_hash, threshold)
                self._count_failure("degraded_answers", "degraded", f"{task.name}: {describe_failure(e)}")
            else:
                teacher_c, stored = answer.completion, answer.stored
                served_choices = list(answer.choices)
                t = self._call_cost(answer.model, served_choices)
                cost += t
                teacher_cost += t
                latency += teacher_c.latency_ms
                served = teacher_c
                fp = fingerprint("teacher", answer.model)
                if answer.fallback:
                    route = "fallback_teacher"
                # The escalation labels the student's own answer (label_p 1.0): one comparison, string work only. Only a
                # valid student answer and the primary teacher's valid answer count, as for an audit (INV-16), and only
                # when the teacher wrote it under the audit's conditions (`_labels_like_audit`, ADR 0028).
                elif sc is not None and sc.valid and answer_problem(task, teacher_c) is None and _labels_like_audit(plan):
                    student_agree = int(task.agree(sc.answer.text, teacher_c.text))

        # The all-teacher cost by the token formula (requests.baseline_est_usd, FR-16.5), for every student answer, also
        # when the teacher was called: on audited rows the replay compares it with what the teacher really cost (ADR
        # 0028). Over the choices the student serves, or would have served (its answer once per choice when fewer agree).
        baseline_est: float | None = None
        if student_c is not None:
            baseline_est = self._formula_baseline(messages, student_c, picked if picked is not None else [student_c] * plan.n)
        # Baseline = what this request would have cost with the teacher alone, writing every choice the client got.
        if teacher_c is not None and served_by == "teacher":  # the teacher's call (or the fallback teacher's) itself
            baseline = self._call_cost(self.s.teacher_model, served_choices)
        elif teacher_c is not None:  # an audit: one answer; the teacher alone would have written one per choice
            baseline = self._cost(self.s.teacher_model, teacher_c, len(served_choices))
        else:  # the student served (also degraded): its choices are `picked`, so this is the formula above, to the bit
            assert baseline_est is not None
            baseline = baseline_est

        # Shadow: the candidate runs the same cascade silently. We record whether it would have
        # served, and if so whether it agrees with the best reference we have. When it disagrees
        # with an answer the live student served, the teacher arbitrates (and that becomes training data).
        # Neither the shadow's samples nor the arbitration can fail the request (INV-15). A failed sample
        # leaves no shadow record; a failed arbitration, or an escalation or arbitration answer the teacher
        # cut off or got wrong (INV-16), leaves shadow_agree NULL, which the release gate counts as a
        # disagreement (Ledger.shadow_quality). An audit answer like that is no reference at all: the shadow
        # is compared with the served answer, as when no audit ran. A request a stand-in answered (degraded, or
        # the fallback teacher) runs no shadow: its only reference would not be the primary teacher's (ADR 0019).
        shadow = None if route in STAND_IN_ROUTES else self.ledger.by_state("shadow")
        shadow_vid = shadow_serve = shadow_agree = None
        if shadow is not None:
            try:
                s_samples, c, s_model = self._sample(shadow, messages, role="shadow", gen=plan.student)
            except CircuitOpen:  # the shadow's breaker is open: no shadow record, nothing counted again
                pass
            except Exception as e:  # optional call: count it, never fail the request
                if isinstance(e, AdapterMissing):
                    self._request_reload(shadow)
                self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} sampling: {describe_failure(e)}")
        if shadow is not None and s_samples is not None:
            cost += c
            student_cost += c
            student_in += s_samples[0].in_tokens
            student_out += sum(s.out_tokens for s in s_samples)
            s_sc = score(task, s_samples)
            shadow_vid = shadow.id
            shadow_serve = int(s_sc.valid and s_sc.confidence >= self.threshold(shadow, s_model, task))
            reference: Completion | None = teacher_c if served_by == "teacher" or audited else None
            arbitrated = True
            if reference is None and shadow_serve and not task.agree(s_sc.answer.text, served.text):
                ruling = None if teacher_failed else self._arbitrate(task, messages, shadow, plan, meta)
                if ruling is None:
                    arbitrated = False
                else:
                    reference, stored = ruling
                    arbitration_c = reference
                    t = self._cost(self.s.teacher_model, reference)
                    cost += t
                    teacher_cost += t
            if reference is not None and answer_problem(task, reference) is not None:
                arbitrated = False  # a cut-off, empty or invalid teacher answer settles nothing (INV-16)
            if arbitrated:
                ref_text = reference.text if reference is not None else served.text
                shadow_agree = int(task.agree(s_sc.answer.text, ref_text))

        request_id = self.ledger.log_request(
            task=task.name,
            served_by=served_by,
            student_version=version.id if version else None,
            confidence=confidence,
            escalated=int(escalated),
            audited=audited,
            audit_agree=audit_agree if audited else None,
            shadow_version=shadow_vid,
            shadow_serve=shadow_serve,
            shadow_agree=shadow_agree,
            latency_ms=latency,
            cost_usd=cost,
            baseline_cost_usd=baseline,
            student_cost_usd=student_cost,
            teacher_cost_usd=teacher_cost,
            student_in_tokens=student_in,
            student_out_tokens=student_out,
            client=client,
            route=route,
            stored=int(stored),
            label_p=label_p,
            threshold=threshold_used,
            student_agree=student_agree,
            baseline_est_usd=baseline_est,
        )
        choices = _choices(task, served_choices, params)
        return Result(
            text=choices[0].text,
            served_by=served_by,
            version=version.id if (version and served_by == "student") else None,
            confidence=confidence,
            escalated=escalated,
            cost_usd=cost,
            baseline_usd=baseline,
            latency_ms=latency,
            model=served.model,
            route=route,
            finish_reason=choices[0].finish_reason,
            **_usage(messages, served_choices, choices),
            fingerprint=fp,
            usage_breakdown=_breakdown(served_choices, samples, audit_c, s_samples, arbitration_c),
            degraded=degraded,
            choices=choices,
            student_cost_usd=student_cost,
            teacher_cost_usd=teacher_cost,
            request_id=request_id,
        )

    def _may_degrade(
        self, sc: Scored | None, params: GenParams | None, e: BaseException, picked: list[Completion] | None = None
    ) -> bool:
        """Whether a failed escalation may be answered with the student's own answer (ADR 0019): only with
        RATCHET_ON_TEACHER_FAILURE=serve_student, only when the student answered this request with at least one valid
        sample (`Scored.valid`: never an empty, invalid or cut-off answer) and, for n > 1 choices, with n samples that
        agree (`picked`, `confidence.majority`), never for a request with `tools` (the student did not see them, so its
        answer cannot be the call the client expects), and only when the teacher is unavailable
        (`resilience.teacher_unavailable`), not when it answered about the request itself (a 400 the client must fix).
        Pinned and teacher-only requests never get here: they have no student answer."""
        return (
            self.serve_student_on_failure
            and picked is not None
            and sc is not None
            and sc.valid
            and not (params is not None and params.tools)
            and teacher_unavailable(e)
        )
