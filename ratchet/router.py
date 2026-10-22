"""The cascade: student first, teacher when the student is unsure, every teacher answer becomes training data."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import random
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple, NoReturn

import httpx

from . import calibration, licenses, metrics, obs, schema_tasks, tasks, taskspec
from .confidence import SERVE_NOTHING, STUDENT_SAMPLING, Scored, majority, score
from .config import Settings
from .drift import boosted_rate
from .ledger import STORED_PARAM_KEYS, TEACHER_MODEL, Ledger, RuntimeFlags, Version, example_hash, target_params
from .openai_compat import tool_turns
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
from .redact import Detector, Pseudonymizer, RedactionError, redact, redact_json, redact_messages
from .resilience import (
    BAD_RESPONSE,
    TEACHER_CIRCUIT_OPEN,
    TOOL_CALLS_UNSUPPORTED,
    UPSTREAM_TIMEOUT,
    AdapterMissing,
    Breaker,
    CircuitOpen,
    adapter_missing,
    describe_failure,
    endpoint_down,
    teacher_unavailable,
)
from .routing import INELIGIBLE_REASONS
from .sidecar import SideCar, Slot, job_bytes
from .spend import BudgetExceeded

# Given a version, return (provider, model name to send). For vLLM + LoRA the model name is the adapter name.
StudentResolver = Callable[[Version], tuple[Provider, str]]

# A failing upstream call is logged as an event on these occurrences only, so an outage cannot flood the release log.
EVENT_ON_FAILURE = (1, 10, 100, 1000)
# While a breaker is open, a refusal that spent nothing gets a failures row only for the 1st, 11th, 21st ... time per
# stage (ADR 0062): an outage cannot turn every request into a write, and the rows still show it.
OPEN_SAMPLE = 10
# Different schemas a teacher endpoint must refuse (`refuses_json_schema`) before the router asks it whether it takes
# json_schema at all (`Ratchet._probe_json_schema`, ADR 0042). A refused schema may be the client's (a keyword the
# endpoint's grammar engine cannot compile), so only that schema is sent as json_object with the schema in the system
# message; every schema is sent that way only when the endpoint refuses the probe's smallest schema too, so no client
# can turn json_schema off for the others by sending schemas the endpoint refuses.
JSON_SCHEMA_STRIKES = 3
MAX_REFUSED_SCHEMAS = 1024  # refused schema digests remembered per endpoint; past it, a new one is refused each time

# How a request was served (`requests.route`): the student; the teacher because the student was unsure (`confidence`),
# because no version serves yet (`cold_start`), because the task is pinned (`pinned`), because the student call raised
# (`student_error`), or because the student's breaker was open (`student_breaker`); the student's own below-threshold
# answer because the teacher was unavailable (`degraded`, RATCHET_ON_TEACHER_FAILURE=serve_student); the fallback
# teacher because the primary was unavailable (`fallback_teacher`, RATCHET_TEACHER_FALLBACK_MODEL; ADR 0019); or the
# teacher because the student was confident but fewer than the client's n of its samples gave that answer, so it could
# not fill n choices (`n_choices`, FR-1.14, ADR 0020); or the teacher alone because the task is not registered and
# RATCHET_UNKNOWN_TASKS is `teacher` (`unknown_task`, FR-2.15, ADR 0022); or the teacher because an operator turned the
# student off (`student_disabled`, the admin API's kill switch, ADR 0064);
# or the teacher without a student call because
# the serving version's per-task record lets the student answer nothing (`task_disabled`: the release gate disabled the
# task on that version, or its eval had no held-out row of it; FR-14.1, ADR 0040); or the teacher alone because the
# request offers tools or carries tool calls or results (`tools`, FR-1.41, ADR 0043); `multimodal` is the route a
# caller of `Ratchet.teacher_only` may give for a request with a content part only the teacher reads (the gateway
# sends such a request through `handle`, which routes it `ineligible:content_type`). The task's routing block adds two
# families of routes (FR-2.50, ADR 0049; `known_route`): `rule:<i>`, the teacher because rule i of `escalate_when`
# held for the confident student's answer, and `ineligible:<reason>`, the teacher without a student call because the
# request is one the student must not see (`routing.INELIGIBLE_REASONS`: `input_tokens`, `content_type`).
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
    "student_disabled",
    "task_disabled",
    "tools",
    "multimodal",
)
RULE_ROUTE = "rule:"  # + the index of the escalate_when rule (FR-2.50)
INELIGIBLE_ROUTE = "ineligible:"  # + one of routing.INELIGIBLE_REASONS (FR-2.50)
# The route of a request the gateway sends straight to an upstream model without the cascade: an embeddings request
# (served_by `upstream`, FR-1.51, ADR 0050). The router never writes it.
PASSTHROUGH_ROUTE = "passthrough"


def known_route(route: str) -> bool:
    """Whether `route` is one the ledger may hold: one of ROUTES, `rule:<i>` or `ineligible:<reason>` (the router's), or
    the gateway's PASSTHROUGH_ROUTE."""
    if route in ROUTES or route == PASSTHROUGH_ROUTE:
        return True
    if route.startswith(RULE_ROUTE):
        index = route[len(RULE_ROUTE) :]
        return index.isascii() and index.isdigit()
    return route.startswith(INELIGIBLE_ROUTE) and route[len(INELIGIBLE_ROUTE) :] in INELIGIBLE_REASONS


# Routes whose answer is not the primary teacher's or a confident student's: such a request runs no shadow, so release
# statistics never rest on a stand-in's answer (ADR 0019).
STAND_IN_ROUTES = frozenset({"degraded", "fallback_teacher"})
# `Result.degraded` (the response's `ratchet.degraded` and `X-Ratchet-Degraded`) for a degraded answer.
TEACHER_UNAVAILABLE = "teacher_unavailable"

# finish_reason values a `skip_example` event names as they are; any other is `other`, so the reasons stay a short list.
_FINISH_NAMED = frozenset({"length", "content_filter", "function_call", "abort", "error", "unknown"})
# The finish_reason values a client gets (`Result.finish_reason`); any other upstream value is reported as `stop`.
# `tool_calls` only for a choice that has tool calls (`Choice.tool_calls`, FR-1.41): OpenAI clients read that finish as
# "the calls are in the message", so a model that claims it without any is reported as `stop` (`_client_finish`).
CLIENT_FINISH = ("stop", "length", "content_filter", "tool_calls")
# Finishes that say an answer was cut short: kept for a tool call too, whose arguments may then be incomplete.
_CUT_SHORT = frozenset({"length", "content_filter"})


def refuses_json_schema(e: BaseException, gen: GenParams | None) -> bool:
    """Whether a failed call with `response_format` json_schema is the endpoint refusing that format (a 400 whose
    `param` or message names `response_format` or `json_schema`), so it should be asked again with json_object and the
    schema in its system message (`schema_tasks.downgrade`, ADR 0042)."""
    if not isinstance(e, UpstreamError) or e.status != 400 or not schema_tasks.wants_json_schema(gen):
        return False
    said = f"{e.param or ''} {e.message or ''}".lower()
    return "response_format" in said or "json_schema" in said


def answer_problem(task: tasks.Task, c: Completion) -> str | None:
    """Why a teacher answer can be neither a training example nor a reference for the student (INV-16), or None when it
    can: it did not end by itself (`finish_reason length`, ...), nothing is left once reasoning is stripped (`empty
    answer`), a JSON task's answer does not parse (`invalid answer`), it breaks the request's `response_format`
    (`schema mismatch at $.priority`, for a task bound to one; FR-1.40), or a field of the task's spec is missing or
    does not normalise, such as a label outside its vocabulary (`invalid field category`; `Task.check`, FR-2.14). String
    operations and the schema check only."""
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


def comparable_label(gen: GenParams | None) -> bool:
    """Whether a teacher answer written under `gen` may be compared with another label of the same request by the
    teacher watch (FR-16.7): drawn at temperature 0 (or unset, which the provider sends as 0) without a client's
    reasoning effort. A sampled answer may differ from another one by design, so it is stored as
    `ledger.UNCHECKABLE`."""
    return gen is None or ((gen.temperature or 0.0) == 0.0 and gen.reasoning_effort is None)


# Added to a student call's longest time before its probe is presumed lost: the call starts a moment after allow().
PROBE_MARGIN_S = 5.0
# How long an off-path arbitration may take, retries included (ADR 0072): a teacher that hangs holds a sidecar worker
# this long, not the serving deadline (RATCHET_TEACHER_DEADLINE_S, 180 s).
ARBITRATION_DEADLINE_S = 60.0


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
    bumped); the request is still answered, audited and logged, with `store_opt_out` (one of `privacy.OPT_OUTS`, `request`
    when not given) as the reason. `end_user` is the end user's HMAC (`privacy.EndUsers.hash`), never the raw value: it is
    logged with the request and the example, and an end user registered as no-train stores nothing (FR-13.2).
    `end_user_aliases` are the other hashes that end user may be registered as no-train under (`privacy.EndUsers.aliases`:
    the request's `user` when `safety_identifier` names the end user, and the every-client scope): looked up, never
    logged or stored. `end_user_secondary` is the hash of that second identifier in the same scope, logged with the
    request (never stored with an example) so a correction of it must name both identifiers (FR-13.30). `retry_count` is
    the client SDK's attempt number (`x-stainless-retry-count`, 0 for a first attempt; ADR 0071): above 0 the request is
    a retry, so a teacher answer it stores never bumps a duplicate's `seen`, and its ledger row records the number.
    `idempotency_key` is the client's Idempotency-Key, which the gateway acts on before the cascade; it is never stored.
    `offpath` lets the request's audit and shadow run after its answer is returned, on the router's sidecar when it has
    workers (ADR 0072); a caller that sums `Result.cost_usd` (Scout's budget guard) passes no meta, so its checks stay
    on the request path and every cent is in the Result. `student_only=True` (the gateway's daily budget is spent under
    RATCHET_BUDGET_MODE=student_only, ADR 0063) serves only what the student answers alone: no audit, no shadow, and a
    request that needs the teacher raises `spend.BudgetExceeded` instead of calling it."""

    request_id: str | None = None
    client_request_id: str | None = None
    retry_count: int = 0
    storable: bool = True
    end_user: str | None = None
    idempotency_key: str | None = None
    store_opt_out: str | None = None
    end_user_aliases: tuple[str, ...] = ()
    end_user_secondary: str | None = None
    offpath: bool = True
    student_only: bool = False


# The model calls of one request, as `Result.usage_breakdown` names them: the student's samples (one call, n choices),
# the served answer (one of those samples, or the teacher's call), the teacher's audit, the shadow's samples, the
# teacher's arbitration, and the teacher answers a repair left unused (`repair`, FR-1.40: the first answer, which broke
# the request's response_format, or a repair answer that was cut off or empty; the answer used is the served, audit or
# arbitration call). A call that was not made, or that failed, is None.
USAGE_PARTS = ("student_samples", "served", "audit", "shadow", "arbitration", "repair")


@dataclass(frozen=True)
class Choice:
    """One of a response's choices (FR-1.14): the answer as served (`tasks.clean_answer`), why it ended (one of
    CLIENT_FINISH), and its token logprobs in OpenAI's `logprobs.content` shape (`providers.served_logprobs`, whose
    tokens spell `text` exactly). `logprobs` is None when the client did not ask for them, and when it did but they
    cannot be given (the model returned none, or they cannot be told apart from stripped reasoning). `tool_calls` are
    the teacher's tool calls for a tool request, as the provider read them (`Completion.tool_calls`, upstream ids kept),
    or None (FR-1.41); `text` is then the content the model wrote beside them, usually ''."""

    text: str
    finish_reason: str = "stop"
    logprobs: list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None


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
    or a replay names the request by. It is not `RequestMeta.request_id`, the gateway's `req_...` id for the request,
    which the row records in `requests.request_id` (what feedback names the request by, FR-13.30).
    `audited` is whether a teacher audit of the served answer counted (`requests.audited`), and `wall_ms` the whole
    cascade on the router's timer (`requests.wall_ms`); the gateway's log line carries both (ADR 0062).

    `overhead_pending` is True when the request's audit or shadow was queued to run after it returns (ADR 0072): then
    `cost_usd` and its split are what serving the answer cost, `baseline_usd` is the token formula's estimate, `audited`
    is False and the breakdown has no audit, shadow or arbitration yet; the ledger row gets the rest when the job ends.

    `task` is the name of the task the request was resolved to (a registered task, an auto task from its schema, or
    an unregistered name). `schema_valid` says whether every choice meets the request's `response_format` (FR-1.40):
    None without one."""

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
    stored: bool = False  # a teacher answer of this request was stored as an example, or bumped one (`requests.stored`)
    store_opt_out: str | None = None  # why it could not be stored (`privacy.OPT_OUTS`), or None (FR-13.1)
    audited: bool = False
    wall_ms: float = 0.0
    overhead_pending: bool = False  # the audit or shadow runs after this returns (ADR 0072)
    task: str = ""  # the resolved task's name
    schema_valid: bool | None = None  # every choice meets the response_format; None without one (FR-1.40)

    def __post_init__(self) -> None:
        if not self.choices:
            self.choices = [Choice(self.text, self.finish_reason)]

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def task_label(task: tasks.Task | str | None) -> str:
    """A metric's task label: a registered task's name, `other` for a name the registry does not hold (so a client cannot
    grow the label set), `none` before a request named one."""
    if task is None:
        return "none"
    if isinstance(task, str):
        found = tasks.lookup(task)
        return found.name if found is not None else "other"
    return task.name if task.source != "adhoc" else "other"


def _outcome(e: BaseException) -> str:
    """`ratchet_upstream_requests_total`'s outcome for a failed call: `timeout` (the call or the endpoint timed out) or
    `error`."""
    if isinstance(e, httpx.TimeoutException) or (
        isinstance(e, UpstreamError) and (e.code == UPSTREAM_TIMEOUT or e.status == 504)
    ):
        return "timeout"
    return "error"


def _spend(counter: metrics.Counter, label: str, usd: float) -> None:
    """Add a positive amount of money to a counter; a zero, negative or NaN cost (a misconfigured price) adds nothing
    and never raises on the request path."""
    if usd > 0:
        counter.inc(label, by=usd)


def _client_finish(c: Completion) -> str:
    """The finish a client gets: `tool_calls` for a choice with tool calls (unless it was cut short: `length` and
    `content_filter` stay, since the arguments may be incomplete), never `tool_calls` for one without; any value
    outside CLIENT_FINISH is `stop`."""
    if c.tool_calls and c.finish_reason not in _CUT_SHORT:
        return "tool_calls"
    if c.finish_reason == "tool_calls" or c.finish_reason not in CLIENT_FINISH:
        return "stop"
    return c.finish_reason


def _choices(task: tasks.Task, served: list[Completion], params: GenParams | None) -> list[Choice]:
    """The response's choices for the served completions: each cleaned as it is served, with its token logprobs when the
    client asked for them (`params.logprobs`; at most `params.top_logprobs` alternatives per token) and its tool calls.
    String work only, and the logprobs only for a client that asked."""
    asked = params is not None and bool(params.logprobs)
    top = params.top_logprobs if params is not None else None
    out = []
    for c in served:
        text = tasks.clean_answer(task, c.text)
        out.append(Choice(text, _client_finish(c), served_logprobs(c, text, top) if asked else None, c.tool_calls or None))
    return out


def uses_tools(messages: list[dict[str, Any]], params: GenParams | None) -> bool:
    """Whether a request is a tool request (FR-1.41): it offers tools (`params.tools`, where the gateway maps the legacy
    `functions` too) or its messages carry tool calls or results (`openai_compat.tool_turns`). A field lookup, then one
    pass over the messages."""
    return (params is not None and bool(params.tools)) or tool_turns(messages)


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
        written = choice.text + (json.dumps(choice.tool_calls, ensure_ascii=False) if choice.tool_calls else "")
        out = c.out_tokens or (approx_tokens(written) if written else 0)
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
    repaired: list[Completion] | None = None,
) -> dict[str, dict[str, int] | None]:
    """`Result.usage_breakdown`: every model call of the request by `USAGE_PARTS`. When the student served, `served` is
    the choices taken from `student_samples`; when the teacher did, it is its own call, every choice of it. `repair`
    adds up the teacher answers a repair left unused (one call each)."""
    return {
        "student_samples": _sampled(samples),
        "served": _sampled(served),
        "audit": _tokens(audit),
        "shadow": _sampled(shadow),
        "arbitration": _tokens(arbitration),
        "repair": None
        if not repaired
        else {"in": sum(c.in_tokens for c in repaired), "out": sum(c.out_tokens for c in repaired)},
    }


def _asked(served: list[Completion], first: Completion | None) -> list[Completion]:
    """The served choices as the request's own prompt cost them. After a repair the served answer may come from the
    repair call, whose prompt also held the rejected answer and the repair note; the client's usage and the
    teacher-alone baseline count the prompt of the first call (`TeacherAnswer.first`), which was asked the request
    itself. `cost_usd` and `usage_breakdown` still pay for both calls as made."""
    if first is None or first is served[0]:
        return served
    return [replace(served[0], in_tokens=first.in_tokens, cached_tokens=first.cached_tokens), *served[1:]]


def _schema_valid(task: tasks.Task, choices: list[Choice]) -> bool | None:
    """`Result.schema_valid`: whether every choice, as served, meets the task's bound `response_format`; None without
    one. A tool call is not held to the format (it applies to the content of the final answer), so choices with tool
    calls are left out, and when every choice is one there is nothing to check: None."""
    answers = [ch for ch in choices if not ch.tool_calls]
    if task.contract is None or not answers:
        return None
    return all(task.contract.problem(ch.text) is None for ch in answers)


def _task(task: str | tasks.Task | None) -> tasks.Task:
    """The task a name (or a Task) means now, with an operator's runtime pin applied (`tasks.effective`, ADR 0064)."""
    return tasks.effective(task) if isinstance(task, tasks.Task) else tasks.get(task)


class TeacherAnswer(NamedTuple):
    """A teacher call's answer (`Ratchet._call_teacher`): the completion (choice 0, the only one that may be stored),
    whether it was stored as a training example, the configured model that wrote it (RATCHET_TEACHER_MODEL, or
    RATCHET_TEACHER_FALLBACK_MODEL), whether that was the fallback teacher, every choice of the call (one, or the
    client's n when the answer is served), and, when the fallback teacher answered, the primary's error (for the
    request's failures row). The model is the one Ratchet asked for and licensed at startup, never the response's own
    `model` string, which an upstream may spell differently. After a repair (the first answer broke the
    request's `response_format`, FR-1.40), `rejected` is the answer of the two that is not used (the first one, or a
    repair answer that was cut off or empty), whose call is paid for too, and `first` is the first call's answer: the
    one asked the request's own messages, whose prompt counts the client's usage and the baseline read (`_asked`)."""

    completion: Completion
    stored: bool
    model: str
    fallback: bool
    choices: tuple[Completion, ...] = ()
    primary_error: BaseException | None = None
    rejected: Completion | None = None
    first: Completion | None = None


class AuditOutcome(NamedTuple):
    """A teacher audit of a served student answer (`Ratchet._audit`): the teacher's answer when the call succeeded (it
    prices the baseline even when it measures nothing), whether it was stored, whether it counts as an audit (`audited`,
    INV-16) and agrees, whether the call failed (then no arbitration follows for the request, INV-15), and its price
    (both calls after a repair, FR-1.40); after a repair, the answer it did not use and its first answer
    (`TeacherAnswer.rejected`, `.first`)."""

    completion: Completion | None
    stored: bool
    audited: int
    agree: int
    failed: bool
    cost: float
    rejected: Completion | None = None
    first: Completion | None = None


class ShadowOutcome(NamedTuple):
    """The shadow candidate's run on one request (`Ratchet._shadow`): its samples (None when they failed: no shadow
    record), their price, the teacher's arbitration and its price (None and 0 when none ran), whether the arbitration
    stored an example, and the row's verdict (`shadow_version`, `shadow_serve`, `shadow_agree`)."""

    samples: list[Completion] | None
    sample_cost: float
    arbitration: Completion | None
    arbitration_cost: float
    stored: bool
    version: int | None
    serve: int | None
    agree: int | None
    rejected: Completion | None = None  # the arbitration's unused answer after a repair (FR-1.40), paid for in its price


_NO_SHADOW = ShadowOutcome(None, 0.0, None, 0.0, False, None, None, None)


@dataclass(frozen=True)
class OffPath:
    """What a request's off-path job needs (ADR 0072): its ledger row, the request, the answer served (choice 0) and how
    many choices the client got, the student answer an audit judges, the reference the request already has (the
    escalation's teacher answer, or the answer of an audit that ran on the request path because the queue was full),
    the baseline the row was logged with, the student version whose answer is audited (None: no audit), the shadow
    candidate (None: no shadow) and whether a teacher call of the request already failed (an inline audit's), which
    stops the arbitration as it would on the request path."""

    row: int
    task: tasks.Task
    messages: list[dict[str, Any]]
    plan: GenPlan
    meta: RequestMeta | None
    client: str | None
    t0: float
    served: Completion
    choices: int
    student: Completion | None
    reference: Completion | None
    baseline: float
    audit_version: int | None
    shadow: Version | None
    teacher_failed: bool = False

    @property
    def tag(self) -> tuple[int | None, int | None]:
        """(audited version, shadow version): what a dropped or abandoned job is counted under."""
        return self.audit_version, self.shadow.id if self.shadow is not None else None

    rejected: Completion | None = None
    first: Completion | None = None


class Ratchet:
    """The cascade. `clock` drives the breakers (tests pass a fake one). `on_adapter_missing(version)` is called when
    vLLM answers 404 for a version's adapter; live mode wires `AdapterReloader.request` to it. `fallback_teacher` is the
    provider for RATCHET_TEACHER_FALLBACK_MODEL (default: `teacher`, the same endpoint); it is used only when that model
    is set. `timer` measures each request's wall-clock time (`requests.wall_ms`, ADR 0060; `time.perf_counter`).
    `sidecar` runs requests' audits and shadows after they return (ADR 0072); by default one is built from
    `Settings.sidecar()` (4 workers in live mode, none in mock mode, where every check stays on the request path)."""

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
        timer: Callable[[], float] = time.perf_counter,
        sidecar: SideCar | None = None,
        tasks_lock: threading.Lock | None = None,
    ):
        self.s = settings
        settings.check_reserved()  # a setting reserved for a feature not built yet stops startup here (ADR 0024)
        self.ledger = ledger
        self.teacher = teacher
        self.resolve_student = student_resolver
        self.rng = random.Random(seed)
        # The license registry for these settings: the built-in teachers plus RATCHET_TEACHER_LICENSES' (ADR 0090); a bad
        # file stops startup here, every error listed. Night School and the BOM read the same registry.
        licenses.install(settings.teacher_licenses_file)
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
        # RATCHET_STORE_EXAMPLES and RATCHET_NO_STORE_TASKS (FR-13.1): a Settings built in code with anything but a bool,
        # or a name that is not a task name, stops startup here.
        if not isinstance(settings.store_examples, bool):
            raise ValueError(f"RATCHET_STORE_EXAMPLES must be true or false, not {settings.store_examples!r}")
        self.no_store_tasks = settings.no_store_task_names()
        # Structured outputs (ADR 0042): RATCHET_MAX_AUTO_TASKS (a bad value stops startup here) and the lock that keeps
        # auto-task writes and the admin API's (the gateway passes its `tasks_lock`) from interleaving.
        self.max_auto_tasks = settings.auto_task_cap()
        self.tasks_lock = tasks_lock if tasks_lock is not None else threading.Lock()
        self._auto_cap_noted = False  # the `auto_task_cap` event was written (once per ledger, `Ledger.event_once`)
        self._json_schema_refused: dict[str, set[str]] = {}  # teacher endpoint -> digests of the schemas it refused
        # teacher endpoint -> the probe's verdict: True refused (json_schema off), False answered, None being asked
        self._json_schema_probed: dict[str, bool | None] = {}
        # Redaction (ADR 0082): the placeholder style (RATCHET_REDACT_STYLE) and the detector plugin
        # (RATCHET_REDACT_PLUGIN), imported here, so a bad style or a missing plugin stops startup.
        self.redact_style = settings.redaction_style()
        self.detector: Detector | None = settings.redact_detector()
        self.breaker_policy = settings.student_breaker()  # a bad RATCHET_STUDENT_BREAKER_* value stops startup here
        # What each role sends of a client's parameters (ADR 0017); a bad RATCHET_*_PARAMS or cap stops startup here.
        self.gen = settings.gen_policy()
        self._plain = self.gen.plan(None)  # the plan of a request without parameters (Scout, the simulator)
        self.clock = clock
        self.timer = timer
        self.on_adapter_missing = on_adapter_missing
        # {version id: multiplier} of the audit rate after a drift alarm (FR-6.40). Empty unless RATCHET_DRIFT_AUDIT_BOOST
        # is above 1; the release gate fills it and the gateway and simulator share its dict here (`ReleaseGate
        # .audit_boost`). Only read, with `.get`, so the gate may change it from another thread.
        self.audit_boost: dict[int, float] = {}
        # Failed upstream calls since start (health()). Scout calls handle from several threads, hence the lock.
        self.student_errors = 0  # student calls that raised; the teacher answered instead
        self.audit_errors = 0  # teacher audits that raised; the student's answer was served anyway (INV-15)
        self.shadow_errors = 0  # shadow samples or arbitrations that raised; the request never noticed
        self.degraded_answers = 0  # escalations answered by the student because the teacher was unavailable
        self.fallback_answers = 0  # answers the fallback teacher wrote because the primary was unavailable
        self.repair_errors = 0  # repair calls that raised; the first teacher answer was served (FR-1.40)
        self.auto_task_collisions = 0  # requests whose schema derives other fields than the auto task of their name
        self._errors_lock = threading.Lock()
        self._skipped: Counter[str] = Counter()  # teacher answers not stored, by reason (throttles `skip_example`)
        self._open_refusals: Counter[str] = Counter()  # breaker refusals by failure stage (samples their failures rows)
        # One breaker per (role, endpoint), made on first use: live and canary on one endpoint share 'student'; shadow
        # calls have their own ('shadow'), so a bad staging adapter cannot open the breaker live traffic depends on.
        self._breakers: dict[tuple[str, str], Breaker] = {}
        self._breakers_lock = threading.Lock()
        # The teachers' breakers (ADR 0019), made here so health() shows them from the start; a bad
        # RATCHET_TEACHER_BREAKER_* value stops startup. Every teacher call goes through one (`_teacher_complete`).
        self.teacher_policy = settings.teacher_breaker()
        self.teacher_breaker = self._teacher_breaker("teacher", teacher)
        settings.teacher_watch_limits()  # a bad RATCHET_TEACHER_PROBES or RATCHET_ADJUDICATE_MAX stops startup here
        # The model id the primary teacher's responses last reported (the teacher watch, FR-16.7): a cache of the ledger's
        # `meta`, so only a change touches the ledger (`_watch_model`).
        self._teacher_seen: str | None = ledger.meta_value(TEACHER_MODEL)
        self._watch_lock = threading.Lock()
        self.fallback_breaker = (
            self._teacher_breaker("fallback_teacher", self.fallback_teacher) if self.fallback_teacher is not None else None
        )
        # Off-path checks (ADR 0072); a bad RATCHET_SIDECAR_* value stops startup here. What could not go through the
        # sidecar, by version: audits that ran on the request path and shadows skipped because the queue was full, and
        # jobs given up at shutdown.
        if sidecar is None:
            workers, queue, _ = settings.sidecar()
            sidecar = SideCar(workers, queue)
        self.sidecar = sidecar
        self._audits_inline: Counter[int] = Counter()
        self._shadows_dropped: Counter[int] = Counter()
        self._abandoned_audits: Counter[int] = Counter()
        self._abandoned_shadows: Counter[int] = Counter()
        self._full: Counter[str] = Counter()  # queue-full refusals by kind (throttles their events)
        # Called with (client, US dollars) when an off-path audit or shadow spends after its request answered (ADR 0072),
        # so the gateway's spend meter (ADR 0063) counts what the response's `cost_usd` could not include yet.
        self.on_offpath_spend: Callable[[str | None, float], object] | None = None

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
        """Failed upstream calls since start, by kind (`repair_errors`: schema repairs that raised); answers a stand-in
        wrote because the teacher was unavailable (`degraded_answers`, `fallback_answers`); and each breaker's
        `snapshot()` by name, the teachers' included (for /readyz and /metrics)."""
        with self._errors_lock:
            out: dict[str, Any] = {
                "student_errors": self.student_errors,
                "audit_errors": self.audit_errors,
                "shadow_errors": self.shadow_errors,
                "degraded_answers": self.degraded_answers,
                "fallback_answers": self.fallback_answers,
                "repair_errors": self.repair_errors,
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

    @staticmethod
    def _own_context(meta: RequestMeta | None) -> obs.RequestContext | None:
        """The gateway's context of the request `meta` describes, or None for a call outside it (Scout's agents run
        inside /scout/run's context but are not that request)."""
        ctx = obs.current()
        if ctx is None or meta is None or meta.request_id is None or meta.request_id != ctx.request_id:
            return None
        return ctx

    @classmethod
    def _sender(cls, meta: RequestMeta | None) -> str | None:
        """Who sent the request, for the ledger's seen window (`Ledger.add_example`, ADR 0071): the API key's name ('' with
        keys off) of the request the gateway is serving, or None for a call outside it (Scout's agents, the simulator,
        the mock warm-up), whose repeats are traffic, never a client's retries."""
        ctx = cls._own_context(meta)
        return None if ctx is None else (ctx.client or "")

    def _record_failure(
        self,
        stage: str,
        e: BaseException,
        task: tasks.Task,
        client: str | None,
        meta: RequestMeta | None,
        t0: float,
        cost: float = 0.0,
        *,
        call: str = "",
        final: bool = False,
        status: int | None = None,
    ) -> None:
        """A `failures` row (ADR 0062) for `e` at `stage` (`ledger.FAILURE_STAGES`): the exception's class, status and
        code only (`describe_failure`, prefixed with `call` when the stage holds several calls), the status it stands for
        (`status`, else `obs.failure_status`), the time since `t0` on `self.timer`, and `cost`: what the request spent
        with no `requests` row to hold it (only a request that fails, `final`). A refusal by an open breaker that spent
        nothing is written for the 1st, 11th, 21st ... time per stage only (OPEN_SAMPLE). A `final` failure tells the
        gateway's context its row is decided and where the request failed, so the gateway writes no second row and does
        not count the mapped 4xx (a teacher's 400 or 429) as a refusal before the cascade. Never raises: a failures row
        must not fail the request it describes (INV-7, INV-15)."""
        if final:
            ctx = self._own_context(meta)
            if ctx is not None:
                ctx.failure_handled = True
                ctx.stage = stage
        metrics.FAILURES.inc(stage)  # every failure, the sampled-out refusals included
        if isinstance(e, CircuitOpen) and not cost > 0:
            with self._errors_lock:
                self._open_refusals[stage] += 1
                n = self._open_refusals[stage]
            if n % OPEN_SAMPLE != 1:
                return
        try:
            self.ledger.log_failure(
                request_id=meta.request_id if meta is not None else None,
                client=client,
                task=task.name,
                stage=stage,
                error=f"{call}: {describe_failure(e)}" if call else describe_failure(e),
                status=status if status is not None else obs.failure_status(e),
                latency_ms=(self.timer() - t0) * 1000,
                cost_usd=cost if cost > 0 else 0.0,
            )
        except Exception:  # noqa: BLE001 - the failure is still in the log and the metrics; the request goes on
            pass

    def _count_request(
        self,
        task: tasks.Task,
        served_by: str,
        route: str,
        wall_ms: float,
        cost: float,
        baseline: float,
        client: str | None,
        escalated: bool,
        meta: RequestMeta | None,
    ) -> None:
        """The metrics of an answered request (ADR 0062): counter increments and one histogram observation, nothing
        else, so `handle` stays fast. The gateway counts the requests that end in an error."""
        tl, cl = task_label(task), metrics.client_label(client)
        metrics.REQUESTS.inc(tl, served_by, route, "200")
        metrics.REQUEST_SECONDS.observe(wall_ms / 1000, served_by)
        _spend(metrics.COST, cl, cost)
        _spend(metrics.BASELINE, cl, baseline)
        if escalated:
            metrics.ESCALATIONS.inc(tl)
        ctx = self._own_context(meta)
        if ctx is not None:
            ctx.counted = True

    def _teacher_failed(
        self, e: BaseException, task: tasks.Task, client: str | None, meta: RequestMeta | None, t0: float, cost: float
    ) -> None:
        """A request whose teacher answer could not be had, about to raise for the gateway to map (INV-7): its failures
        row carries what it already spent (the student's samples, NFR-3), which also counts in `ratchet_cost_usd_total`
        and `ratchet_failed_cost_usd_total`. Only an HTTP failure (`obs.failure_status` is not None) is the teacher's: an
        error of another kind is a bug in Ratchet (or the mock world not knowing a request), so it gets no `teacher` row,
        which would send the operator to a teacher outage. The gateway writes that request's row by the status it answers
        (a 500 is `gateway`), with the spend handed over in `RequestContext.failed_cost_usd`; outside a gateway request
        the router writes the `gateway` row itself."""
        label = metrics.client_label(client)
        _spend(metrics.COST, label, cost)
        _spend(metrics.FAILED_COST, label, cost)
        ctx = self._own_context(meta)
        if ctx is not None:
            ctx.spent_usd += cost  # the gateway counts it against the key's budget (ADR 0063)
        if obs.failure_status(e) is not None:
            self._record_failure("teacher", e, task, client, meta, t0, cost, final=True)
            return
        if ctx is not None:
            ctx.failed_cost_usd += cost
        else:
            self._record_failure("gateway", e, task, client, meta, t0, cost)

    def _budget_refused(
        self, need: str, task: tasks.Task, client: str | None, meta: RequestMeta | None, t0: float, cost: float = 0.0
    ) -> NoReturn:
        """Refuse a request that needs the teacher while the gateway's budget is spent (`RequestMeta.student_only`, ADR
        0063): raise `BudgetExceeded`, which the gateway answers with a 429, without calling the teacher. What the request
        already spent (the student's samples) is counted like a failed request's (NFR-3): in the metrics, on the request's
        context for the key's budget, and in a `budget` failures row written here, never throttled. A refusal that spent
        nothing gets its row from the gateway, throttled like its other refusals."""
        e = BudgetExceeded(need)
        ctx = self._own_context(meta)
        if ctx is not None:
            ctx.stage = "budget"
            ctx.spent_usd += cost
        if cost > 0:
            label = metrics.client_label(client)
            _spend(metrics.COST, label, cost)
            _spend(metrics.FAILED_COST, label, cost)
            self._record_failure("budget", e, task, client, meta, t0, cost, final=True, status=429)
        raise e

    # ------------------------------------------------------------------
    def _pick_version(self, flags: RuntimeFlags | None = None) -> Version | None:
        """The version that answers this request: the canary for its `traffic_pct`% of requests, otherwise live; None on
        cold start, or while an operator has turned the student off (`student_disabled`, ADR 0064), so the teacher
        answers and its answers are stored as usual."""
        if (flags or self.ledger.flags()).student_disabled:
            return None
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
        self,
        messages: list[dict[str, Any]],
        gen: GenParams,
        fallback: bool = False,
        n: int = 1,
        upstream: str = "teacher",
        deadline_s: float | None = None,
    ) -> list[Completion]:
        """One call to the primary teacher (or, with `fallback`, the fallback teacher) through its breaker, the way
        `_sample` calls the student: its choices, at least one and at most `n` (a server that returns fewer than `n` gives
        fewer). Raises `CircuitOpen` (503 `teacher_circuit_open`, with the time until the next probe as `retry_after`)
        without calling while the breaker is open, so a request that needs the teacher's answer fails at once instead of
        waiting out the deadline, and an optional call (audit, arbitration) is skipped. The provider's retries come
        first: one failed call is one failure here, counted as `resilience.endpoint_down` says (a timeout, no connection,
        a 5xx, 408, 429; another 4xx is the teacher answering; a non-HTTP error counts neither way). `upstream` names the
        call in the metrics and the log (teacher, fallback_teacher, audit, arbitration; ADR 0062). `deadline_s` shortens
        the provider's retry deadline for this call (an off-path arbitration, ADR 0072); None keeps it.

        A teacher that refuses `response_format` json_schema (a 400 about it, `refuses_json_schema`) is asked once more
        with json_object and the schema in its system message (`schema_tasks.downgrade`), within the same breaker slot.
        That schema is then sent that way directly; once the endpoint refused JSON_SCHEMA_STRIKES different schemas it is
        asked with the smallest strict schema (`_probe_json_schema`), and only when it refuses that too is every schema
        sent that way (the provider's `supports_json_schema` is set False). Each step writes one `json_schema_fallback`
        event per ledger. The answer is checked against the schema either way (ADR 0042)."""
        if fallback:
            assert self.fallback_teacher is not None and self.fallback_model is not None and self.fallback_breaker is not None
            provider, model, breaker = self.fallback_teacher, self.fallback_model, self.fallback_breaker
        else:
            provider, model, breaker = self.teacher, self.s.teacher_model, self.teacher_breaker
        if not breaker.allow():
            metrics.UPSTREAM.inc(upstream, "circuit_open")
            raise CircuitOpen(breaker.name, retry_after=breaker.retry_in(), code=TEACHER_CIRCUIT_OPEN)
        start = time.perf_counter()
        extra: dict[str, Any] = {} if deadline_s is None else {"deadline_s": deadline_s}
        msgs, call = messages, gen
        if schema_tasks.wants_json_schema(gen) and not self._takes_json_schema(provider, gen):
            msgs, call = schema_tasks.downgrade(messages, gen)
        try:
            try:
                out = provider.complete(
                    msgs, model, n=n, temperature=call.temperature or 0.0, logprobs=bool(call.logprobs), params=call, **extra
                )
            except UpstreamError as e:
                if call is not gen or not refuses_json_schema(e, gen):
                    raise
                self._refused_json_schema(provider, model, breaker.name, e, gen)
                msgs, call = schema_tasks.downgrade(messages, gen)
                out = provider.complete(
                    msgs, model, n=n, temperature=call.temperature or 0.0, logprobs=bool(call.logprobs), params=call, **extra
                )
            if not out:
                raise UpstreamError(502, "the teacher returned no choices", code=BAD_RESPONSE)
        except BaseException as e:
            down = endpoint_down(e)
            if down is None:
                breaker.release()
            else:
                breaker.record(not down, describe_failure(e))
            if isinstance(e, Exception):
                metrics.UPSTREAM.inc(upstream, _outcome(e))
                metrics.UPSTREAM_SECONDS.observe(time.perf_counter() - start, upstream)
                obs.upstream_failure(upstream, e)
            raise
        breaker.record(True)
        metrics.UPSTREAM.inc(upstream, "ok")
        metrics.UPSTREAM_SECONDS.observe(time.perf_counter() - start, upstream)
        return out[:n]

    def _takes_json_schema(self, provider: Provider, gen: GenParams) -> bool:
        """Whether a teacher provider is sent this call's `response_format` json_schema as it is: not when the provider
        says it does not take json_schema, when its endpoint refused the probe (`_probe_json_schema`), or when it refused
        this schema. Hashes the schema only for an endpoint that refused one."""
        if getattr(provider, "supports_json_schema", True) is False:
            return False
        endpoint = _endpoint(provider)
        with self._errors_lock:
            refused = self._json_schema_refused.get(endpoint)
            if not refused:
                return True
            if self._json_schema_probed.get(endpoint) is True:
                return False
        digest = schema_tasks.schema_digest(gen)
        with self._errors_lock:
            return digest not in self._json_schema_refused.get(endpoint, ())

    def _refused_json_schema(self, provider: Provider, model: str, name: str, e: BaseException, gen: GenParams) -> None:
        """Remember that a teacher endpoint refused this json_schema (at most MAX_REFUSED_SCHEMAS of them), and after
        JSON_SCHEMA_STRIKES different ones ask it whether it takes json_schema at all (`_probe_json_schema`, once per
        endpoint). Only when it refuses the probe too is json_schema off for it (`supports_json_schema = False` on the
        provider, as the day-1 check reports it; also kept here, for a provider that cannot take the attribute). One
        event per ledger for the first refusal and one for turning json_schema off, naming the error's class and status,
        never its message (INV-12)."""
        digest = schema_tasks.schema_digest(gen)
        endpoint = _endpoint(provider)
        with self._errors_lock:
            refused = self._json_schema_refused.setdefault(endpoint, set())
            first = not refused
            if len(refused) < MAX_REFUSED_SCHEMAS:
                refused.add(digest)
            probe = len(refused) >= JSON_SCHEMA_STRIKES and endpoint not in self._json_schema_probed
            if probe:
                self._json_schema_probed[endpoint] = None  # being asked: no second probe meanwhile
        why = describe_failure(e)
        if first:
            detail = f"{name}: schema refused ({why}); that schema is sent as json_object with the schema in the system message"
            self.ledger.event_once("json_schema_fallback", f"{name}: schema refused", detail)
        if not probe:
            return
        verdict = self._probe_json_schema(provider, model)
        with self._errors_lock:
            if verdict is None:  # the probe failed otherwise: asked again after the next refused schema
                self._json_schema_probed.pop(endpoint, None)
            else:
                self._json_schema_probed[endpoint] = verdict
        if not verdict:
            return
        try:
            provider.supports_json_schema = False  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass
        detail = (
            f"{name}: json_schema off: {JSON_SCHEMA_STRIKES} schemas and the probe refused ({why}); every schema is sent "
            "as json_object with the schema in the system message"
        )
        self.ledger.event_once("json_schema_fallback", f"{name}: json_schema off", detail)

    def _probe_json_schema(self, provider: Provider, model: str) -> bool | None:
        """Whether a teacher endpoint refuses json_schema itself: one call with the smallest strict schema
        (`schema_tasks.PROBE_FORMAT`). True when it refuses that too (`refuses_json_schema`), False when it answers, None
        when the call failed otherwise. Outside the breaker and not priced into any request: a few tokens, asked once
        per endpoint (again only after an inconclusive probe)."""
        gen = GenParams(response_format=schema_tasks.PROBE_FORMAT, max_tokens=schema_tasks.PROBE_MAX_TOKENS)
        try:
            provider.complete(schema_tasks.PROBE_MESSAGES, model, n=1, temperature=0.0, logprobs=False, params=gen)
        except Exception as e:
            return True if refuses_json_schema(e, gen) else None
        return False

    def _ask_teacher(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        gen: GenParams,
        visible_max: int | None,
        fallback: bool = False,
        n: int = 1,
        upstream: str = "teacher",
        deadline_s: float | None = None,
        tool_calls: bool = False,
    ) -> list[Completion]:
        """The teacher's answer under `gen` (the fallback teacher's with `fallback`): one choice, or up to `n` for a served
        answer. When the reasoning headroom let the teacher write more than the client's `max_tokens` (`visible_max`), a
        choice whose visible part (`tasks.clean_answer`) is longer than that, by `approx_tokens`, is marked `length`: over
        the client's budget, so it is not stored (INV-16) and the client is told. The text is not cut, because the count
        is an estimate.

        A tool call is an answer only where `tool_calls` says so: the teacher-only path, which returns it to the client
        and never stores it (FR-1.41). Anywhere else (the cascade, whose requests offer no tools) a choice with tool calls,
        or one that ends in `tool_calls`, raises `UpstreamError` 502 `tool_calls_unsupported`: it could be neither
        compared, stored nor served as the answer to that request. An audit or arbitration counts it as a failed optional
        call. The teacher answered, so its breaker counts the call as a success."""
        cs = self._teacher_complete(messages, gen, fallback, n, upstream, deadline_s)
        if not fallback:
            self._watch_model(cs[0].model)
        if not tool_calls and any(c.tool_calls or c.finish_reason == "tool_calls" for c in cs):
            refused = UpstreamError(
                502, "the teacher answered with a tool call to a request that offers no tools", code=TOOL_CALLS_UNSUPPORTED
            )
            obs.upstream_failure(upstream, refused)
            raise refused
        if visible_max is not None:
            cs = [
                replace(c, finish_reason="length")
                if c.finish_reason in COMPLETE and approx_tokens(tasks.clean_answer(task, c.text)) > visible_max
                else c
                for c in cs
            ]
        return cs

    def _watch_model(self, model: str) -> None:
        """The teacher watch's model-id check (FR-16.7, ADR 0033): when a primary-teacher response reports another model
        id than the last one, `Ledger.teacher_model_seen` writes `teacher_model_changed` and starts a new check epoch,
        once across threads and processes. A string comparison on every teacher call; only a change reaches the ledger.
        The fallback teacher's responses are not watched: they name another model by design. The watch never fails the
        call it rides on: when the ledger refuses the write, the id stays unseen and the next response tries again."""
        if not model or model == self._teacher_seen:
            return
        with self._watch_lock:
            if model == self._teacher_seen:
                return
            try:
                self.ledger.teacher_model_seen(model)
            except Exception:  # noqa: BLE001 - evidence about the teacher, never a reason to fail its answer
                return
            self._teacher_seen = model

    def _serve_teacher(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        gen: GenParams,
        visible_max: int | None,
        n: int = 1,
        tool_calls: bool = False,
    ) -> tuple[list[Completion], BaseException | None]:
        """The teacher answer a request needs (escalation, cold start, pinned, teacher-only), up to `n` choices, and the
        primary's error when the fallback teacher wrote it (None when the primary did). `tool_calls` lets the answer be
        tool calls (the teacher-only path, `_ask_teacher`). The fallback teacher (RATCHET_TEACHER_FALLBACK_MODEL) is
        asked only when the primary is unavailable (`resilience.teacher_unavailable`: its retries failed, or its breaker
        is open); never for an answer about the request itself (a 400 such as `context_length_exceeded`) or a refused
        tool call. When the fallback fails as an upstream call too, the primary's error is raised: it is the configured
        teacher, and its breaker says when to come back. Anything else the fallback raises (a bug) propagates as it is."""
        try:
            return self._ask_teacher(task, messages, gen, visible_max, n=n, tool_calls=tool_calls), None
        except Exception as e:
            if self.fallback_teacher is None or not teacher_unavailable(e):
                raise
            primary = e
        try:
            cs = self._ask_teacher(
                task, messages, gen, visible_max, fallback=True, n=n, upstream="fallback_teacher", tool_calls=tool_calls
            )
        except httpx.HTTPError:
            raise primary  # noqa: B904 - the primary's error is the one the client gets; the fallback's is its context
        self._count_failure("fallback_answers", "fallback_teacher", f"{task.name}: {describe_failure(primary)}")
        return cs, primary

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
        client: str | None = None,
        deadline_s: float | None = None,
    ) -> TeacherAnswer:
        """The teacher's answer, under the plan's `serve` parameters (with the client's `plan.n` choices) or, to audit or
        arbitrate, its `check` ones (one answer), and whether it was stored as a training example (`_store_example`).
        `deadline_s` shortens an audit's or arbitration's retry deadline (an off-path arbitration, ADR 0072).
        Only choice 0 is ever stored, so one request adds or bumps at most one example and `seen` keeps counting
        requests (FR-1.14). Only a served answer may come from the fallback teacher (`_serve_teacher`); an audit or
        arbitration is a release signal and a label, so it is the primary's or nothing."""
        plan = plan or self._plain
        primary: BaseException | None = None
        if role == "serve":
            gen = plan.serve
            cs, primary = self._serve_teacher(task, messages, gen, plan.visible_max, plan.n)
        else:
            gen = plan.check
            upstream = "audit" if role == "audit" else "arbitration"
            cs = self._ask_teacher(task, messages, gen, plan.visible_max, upstream=upstream, deadline_s=deadline_s)
        fallback = primary is not None
        cs, rejected, first = self._repair(task, messages, gen, plan.visible_max, fallback, cs)
        stored = self._store_example(task, messages, cs[0], gen, meta, fallback=fallback, client=client)
        return TeacherAnswer(cs[0], stored, self._teacher_model(fallback), fallback, tuple(cs), primary, rejected, first)

    def store_opt_out(self, task: tasks.Task, meta: RequestMeta | None = None) -> str | None:
        """Why this request's teacher answers may not be stored (`privacy.OPT_OUTS`, broadest first), or None when they
        may (FR-13.1): zero retention (RATCHET_STORE_EXAMPLES=0), a no-store task (its spec's `store`, or
        RATCHET_NO_STORE_TASKS), the caller's verdict (`meta.storable` False, from the API key or the request), or an end
        user registered as no-train under any of its hashes (`end_user` and `end_user_aliases`). Attribute checks, plus
        one primary-key lookup when the request names an end user and nothing else opted it out. Nothing here can turn
        an opt-out back into storage."""
        if not self.s.store_examples:
            return "global"
        if not task.store or task.name in self.no_store_tasks:
            return "task"
        if meta is None:
            return None
        if not meta.storable:
            return meta.store_opt_out or "request"
        if (meta.end_user is not None or meta.end_user_aliases) and self.ledger.is_no_train(
            meta.end_user, *meta.end_user_aliases
        ):
            return "end_user"
        return None

    def _opted_out(self, task: tasks.Task, meta: RequestMeta | None) -> tuple[RequestMeta, str | None]:
        """The request's own copy of its meta with the opt-out settled once (`store_opt_out`), so the request is logged
        with its reason and every later store check stops at `storable` without another lookup. A copy, never the
        caller's object: `_store_example` marks it when an opt-out arrives while the request runs."""
        reason = self.store_opt_out(task, meta)
        # No meta (Scout, the simulator, the mock warm-up): its checks stay on the request path (ADR 0072).
        mine = replace(meta) if meta is not None else RequestMeta(offpath=False)
        if reason is None:
            mine.storable, mine.store_opt_out = True, None
            return mine, None
        mine.storable, mine.store_opt_out = False, reason
        return mine, reason

    def _repair(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        gen: GenParams,
        visible_max: int | None,
        fallback: bool,
        cs: list[Completion],
    ) -> tuple[list[Completion], Completion | None, Completion | None]:
        """One repair of a teacher answer that breaks the request's `response_format` (FR-1.40, ADR 0042), with
        RATCHET_SCHEMA_REPAIR on: the same teacher, under the same parameters, is shown its answer and the schema errors
        (`schema_tasks.repair_messages`) and answers once more. Returns the choices to use, the answer of the two calls
        that is not used, and the first call's answer (`TeacherAnswer.rejected` and `.first`); without a repair, the
        choices as they were and None twice: when there is no contract, the answer meets it, it was cut off (a repair
        would be cut off too), the request asked for several choices, or the repair call failed (an optional call:
        counted in `repair_errors` with a throttled `schema_repair_error` event, and the first answer is served). The
        repair's answer replaces the first unless it was cut off or is empty (the first, which ended by itself, is then
        kept). The repair conversation is never stored: an example keeps the request's own messages and the answer that
        met the contract, which is still the teacher's (INV-2). An answer that still breaks the contract is served with
        `schema_valid` false and not stored (INV-16). A tool call is not held to the format (it applies to the final
        answer's content), so it is never repaired."""
        if task.contract is None or not self.s.schema_repair or len(cs) != 1 or cs[0].finish_reason not in COMPLETE:
            return cs, None, None
        if cs[0].tool_calls:
            return cs, None, None
        first = tasks.clean_answer(task, cs[0].text)
        if task.contract.problem(first) is None:  # the check the router has already made (memoised): no second one
            return cs, None, None
        errors = task.contract.errors(first)
        again = schema_tasks.repair_messages(messages, first, errors)
        try:
            repaired = self._ask_teacher(task, again, gen, visible_max, fallback=fallback)[0]
        except CircuitOpen:  # the teacher failed too often lately: not called, not counted again
            return cs, None, None
        except Exception as e:  # optional call: the first answer is served
            self._count_failure("repair_errors", "schema_repair_error", f"{task.name}: {describe_failure(e)}")
            return cs, None, None
        if repaired.finish_reason not in COMPLETE or not tasks.clean_answer(task, repaired.text):
            return cs, repaired, cs[0]  # cut off or empty: worse than the first answer, which is kept
        return [repaired], cs[0], cs[0]

    def _store_example(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        c: Completion,
        gen: GenParams | None = None,
        meta: RequestMeta | None = None,
        *,
        fallback: bool = False,
        client: str | None = None,
    ) -> bool:
        """Store a teacher answer as a training example, the only place that does (INV-2). Only a complete, valid answer
        is stored (INV-16, `answer_problem`): cleaned the way it is served (`tasks.clean_answer`), then, with
        `Settings.redact_pii`, with identifiers in the messages, the stored conditions and the answer replaced (INV-12),
        in the task's style (its spec's `privacy.redact_style`, else RATCHET_REDACT_STYLE), one value one placeholder
        across the three; when the detector plugin fails, nothing is stored (`redaction failed`; FR-13.7).
        The conditions are the `STORED_PARAM_KEYS` of `gen`, the parameters the teacher answered under. An answer written
        under a client's label-altering sampling (`GenParams.unstorable`: `logit_bias`, a penalty, a temperature above 1,
        a vendor field) is not stored either: the client may choose the answer it gets, not the label everyone's student
        learns (threat model T2). Returns True when an example was inserted or a duplicate's `seen` bumped; False, with a
        throttled `skip_example` event, when not. A request under a storage opt-out (`store_opt_out`: zero retention, a
        no-store task, an opted-out key, request or end user) stores nothing and is not a skip (FR-13.1). A `fallback`
        teacher's answer is stored only with RATCHET_STORE_FALLBACK_TEACHER=1 (so the golden labels stay the primary
        teacher's), and then with its own model and license (INV-1). An answer that is not valid under the task's spec
        (`Task.check`: a required field missing, a label outside its vocabulary) is served but skipped, with the field's
        path as the reason (FR-2.14). The example records `client` (the API key name) and the end user's hash. An example
        an erasure tombstoned is refused by the ledger and skipped as `erased` (FR-13.3), also when it was erased under
        another redaction style or plugin setting (its key under the default redaction, `canon_hash`). An opt-out that
        arrived while the request ran (an end user registered as no-train meanwhile) is written onto `meta`, the
        request's own copy (`_opted_out`), so the row and the Result report it like one found at the start."""
        late = self.store_opt_out(task, meta)
        if late is not None:
            if meta is not None and meta.storable:
                meta.storable, meta.store_opt_out = False, late
            return False
        if fallback and not self.s.store_fallback_teacher:
            return False
        problem = (gen.unstorable() if gen is not None else None) or answer_problem(task, c)
        if problem is not None:
            self._skip_example(task, problem)
            return False
        clean = tasks.clean_answer(task, c.text)
        conditions = target_params({k: getattr(gen, k) for k in STORED_PARAM_KEYS}) if gen is not None else None
        stored, found, canon = messages, Counter(), None
        if self.s.redact_pii:  # the teacher saw the original; the training set gets placeholders
            # One Pseudonymizer per example: a value gets the same placeholder in the prompt, the conditions and the
            # answer, and nothing of it outlives this call (ADR 0082). The request is numbered before the answer, so
            # the example's hash (messages and conditions) never depends on which answer the teacher gave.
            pseudo = Pseudonymizer(task.redact_style or self.redact_style, self.detector)
            raw_conditions = conditions
            try:
                stored = redact_messages(messages, found, pseudo)
                if conditions:
                    conditions = redact_json(conditions, found, pseudo)
                clean = redact(clean, found, pseudo)
            except RedactionError:  # the plugin failed: never store what it was meant to find
                self._skip_example(task, "redaction failed")
                return False
            if pseudo.style != "label" or pseudo.detector is not None:
                # The key this request has under the default redaction, which an erasure tombstones too: an example
                # erased under one style or plugin setting stays erased under any other (ADR 0081, ADR 0082).
                canon = example_hash(
                    task.name, redact_messages(messages), redact_json(raw_conditions) if raw_conditions else None
                )
            # A placeholder can break a structured answer (a card-like number outside quotes, an identifier inside a
            # field the spec checks): never store that.
            if found and task.check(clean) is not None:
                self._skip_example(task, "invalid after redaction")
                return False
        model = self._teacher_model(fallback)
        lic = self.licenses.get(model) or licenses.require_distillable(model)  # licensed at startup; never unlicensed
        added = inserted = self.ledger.add_example(
            task.name,
            stored,
            clean,
            model,
            lic.name,
            lic.url,
            redactions=sum(found.values()),
            params=conditions,
            client=client,
            end_user=meta.end_user if meta is not None else None,
            # A client's retry is not a repeat: never counted when its SDK says so, and at most once per seen window
            # for the same sender, so retries cannot move a golden example into training (FR-1.70, INV-3).
            count_seen=meta is None or meta.retry_count <= 0,
            sender=self._sender(meta),
            canon_hash=canon,
            comparable=comparable_label(gen),
        )
        if added is None:  # an erasure tombstoned this example (FR-13.3): it is never stored again
            self._skip_example(task, "erased")
            return False
        if inserted:  # a new row, not a duplicate's `seen` bumped
            metrics.EXAMPLES.inc(task_label(task), self.ledger.split_of(task.name, stored))
        return True

    def _skip_example(self, task: tasks.Task, reason: str, kind: str = "skip_example") -> None:
        """Count a teacher answer that was not stored, and write a `skip_example` event on the 1st, 10th, 100th and
        1000th time for that reason. The detail is the task and the reason, never the answer (INV-12). `kind` names
        another throttled event of the same shape (`auto_task_skipped`). Schema mismatches are counted as one reason
        whatever the path, because a path comes from a client's schema and the counter must not grow with them; the
        event still names the path."""
        key = "schema mismatch" if reason.startswith("schema mismatch") else reason
        with self._errors_lock:
            self._skipped[key] += 1
            n = self._skipped[key]
        if n in EVENT_ON_FAILURE:
            self.ledger.event(kind, f"{task.name}: {reason}")

    def _arbitrate(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        shadow: Version,
        plan: GenPlan | None = None,
        meta: RequestMeta | None = None,
        client: str | None = None,
        t0: float | None = None,
        deadline_s: float | None = None,
    ) -> TeacherAnswer | None:
        """The teacher settles a disagreement between the shadow and the served answer: its answer, whether it was
        stored and the answer a repair left unused. A failed call returns None and leaves the disagreement unarbitrated;
        the request it belongs to is already answered (INV-15). An open teacher breaker skips the call, as a failure
        would, without counting it. Either
        way the request gets a `shadow` failures row (sampled while the breaker is open). `deadline_s` bounds the call,
        retries included (ARBITRATION_DEADLINE_S off the request path, ADR 0072)."""
        start = self.timer() if t0 is None else t0
        try:
            return self._call_teacher(task, messages, plan, "arbitrate", meta, client, deadline_s)
        except CircuitOpen as e:  # the teacher failed too often lately: not called, not counted again
            self._record_failure("shadow", e, task, client, meta, start, call="arbitration")
            return None
        except Exception as e:  # optional call: count it, never fail the request
            self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} arbitration: {describe_failure(e)}")
            self._record_failure("shadow", e, task, client, meta, start, call="arbitration")
            return None

    def _audit(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        plan: GenPlan,
        meta: RequestMeta | None,
        client: str | None,
        t0: float,
        student: Completion,
    ) -> AuditOutcome:
        """The teacher's audit of a served student answer, on the request path or off it (ADR 0072). A failed call, or
        one an open breaker refused, is counted and gets an `audit` failures row; it is no audit, and no arbitration
        follows for the request (INV-15). A cut-off, empty or invalid teacher answer is priced but measures nothing: not
        an audit (INV-16). Never raises for the teacher's sake."""
        try:
            audit = self._call_teacher(task, messages, plan, "audit", meta, client)
        except CircuitOpen as e:  # the teacher failed too often lately: not called, not counted again
            metrics.AUDITS.inc("circuit_open")
            self._record_failure("audit", e, task, client, meta, t0)
            return AuditOutcome(None, False, 0, 0, True, 0.0)
        except Exception as e:  # optional call: the student's answer is served (INV-15)
            self._count_failure("audit_errors", "audit_error", f"{task.name}: {describe_failure(e)}")
            metrics.AUDITS.inc("error")
            self._record_failure("audit", e, task, client, meta, t0)
            return AuditOutcome(None, False, 0, 0, True, 0.0)
        c = audit.completion
        price = self._cost(self.s.teacher_model, c)
        if audit.rejected is not None:  # a repair: both calls are paid for (FR-1.40)
            price += self._cost(self.s.teacher_model, audit.rejected)
        if answer_problem(task, c) is not None:
            metrics.AUDITS.inc("invalid")
            return AuditOutcome(c, audit.stored, 0, 0, False, price, audit.rejected, audit.first)
        agree = int(task.agree(student.text, c.text))
        metrics.AUDITS.inc("agree" if agree else "disagree")
        return AuditOutcome(c, audit.stored, 1, agree, False, price, audit.rejected, audit.first)

    def _shadow(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        plan: GenPlan,
        meta: RequestMeta | None,
        client: str | None,
        t0: float,
        shadow: Version,
        served: Completion,
        reference: Completion | None,
        teacher_failed: bool,
        deadline_s: float | None = None,
    ) -> ShadowOutcome:
        """The shadow candidate runs the same cascade silently, on the request path or off it (ADR 0072). It records
        whether the candidate would have served, and if so whether it agrees with the best reference the request has:
        the teacher's answer (the escalation's, or a counted audit's), else the served answer. When it disagrees with
        an answer the live student served and no teacher answer exists, the teacher arbitrates (and that becomes
        training data), unless a teacher call of this request already failed (`teacher_failed`): it would only wait out
        the same outage. Neither the samples nor the arbitration can fail the request (INV-15): a failed sample leaves
        no shadow record (`_NO_SHADOW`); a failed arbitration, or a reference the teacher cut off or got wrong (INV-16),
        leaves `shadow_agree` None, which the release gate counts as a disagreement (`Ledger.shadow_quality`).
        `deadline_s` bounds the arbitration, retries included."""
        try:
            # The task's own sample count, as the live student's (FR-2.55).
            n = task.sample_count(self.s.samples)
            s_samples, c, s_model = self._sample(shadow, messages, role="shadow", gen=plan.student, n=n)
        except CircuitOpen as e:  # the shadow's breaker is open: no shadow record, nothing counted again
            self._record_failure("shadow", e, task, client, meta, t0, call="sampling")
            return _NO_SHADOW
        except Exception as e:  # optional call: count it, never fail the request
            if isinstance(e, AdapterMissing):
                self._request_reload(shadow)
            self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} sampling: {describe_failure(e)}")
            self._record_failure("shadow", e, task, client, meta, t0, call="sampling")
            return _NO_SHADOW
        s_sc = score(task, s_samples)
        # The candidate runs the same cascade: an answer a rule sends to the teacher is not one it would serve (FR-2.50).
        serve = int(s_sc.valid and s_sc.confidence >= self.threshold(shadow, s_model, task) and self._rule(task, s_sc) is None)
        arbitration: Completion | None = None
        rejected: Completion | None = None
        arbitration_cost = 0.0
        stored = False
        arbitrated = True
        if reference is None and serve and not task.agree(s_sc.answer.text, served.text):
            ruling = None if teacher_failed else self._arbitrate(task, messages, shadow, plan, meta, client, t0, deadline_s)
            if ruling is None:
                arbitrated = False
            else:
                reference, stored, rejected = ruling.completion, ruling.stored, ruling.rejected
                arbitration = reference
                arbitration_cost = self._cost(self.s.teacher_model, reference)
                if rejected is not None:  # a repair: both calls are paid for (FR-1.40)
                    arbitration_cost += self._cost(self.s.teacher_model, rejected)
        if reference is not None and answer_problem(task, reference) is not None:
            arbitrated = False  # a cut-off, empty or invalid teacher answer settles nothing (INV-16)
        agree = None
        if arbitrated:
            ref_text = reference.text if reference is not None else served.text
            agree = int(task.agree(s_sc.answer.text, ref_text))
        return ShadowOutcome(s_samples, c, arbitration, arbitration_cost, stored, shadow.id, serve, agree, rejected)

    def _offpath(self, meta: RequestMeta | None) -> bool:
        """Whether this request's audit and shadow may run after it returns (ADR 0072): only for a request the gateway
        serves (`meta.offpath`), only while the sidecar has workers and is not closed (after shutdown began, every check
        runs on the request path again). Scout's agents, the simulator and the mock warm-up pass no meta, so their checks
        stay on the request path and every cent is in their Result."""
        return meta is not None and meta.offpath and self.sidecar.accepting

    def _sidecar_full(self, kind: str, version: int) -> None:
        """A request whose off-path work found no place in the queue (its places or its byte budget, `SideCar.reserve`):
        its drawn audit runs on the request path instead (`kind` audit; an audit is never dropped, since a sample that
        depended on load would bias the release statistics), or its shadow does not run (`kind` shadow). Counted per
        version (`sidecar_report`) and in `ratchet_sidecar_full_total`; a `sidecar_inline` or `sidecar_dropped` event on
        the 1st, 10th, 100th and 1000th time of each kind."""
        with self._errors_lock:
            (self._audits_inline if kind == "audit" else self._shadows_dropped)[version] += 1
            self._full[kind] += 1
            n = self._full[kind]
        metrics.SIDECAR_FULL.inc("audit_inline" if kind == "audit" else "shadow_dropped")
        if n in EVENT_ON_FAILURE:
            if kind == "audit":
                self.ledger.event("sidecar_inline", f"v{version}: audit ran on the request path, the off-path queue is full")
            else:
                self.ledger.event("sidecar_dropped", f"v{version}: shadow skipped, the off-path queue is full")

    def _finish_offpath(self, op: OffPath) -> None:
        """A request's audit and shadow after its answer was returned (ADR 0072): the same calls, in the same order, with
        the same rules as on the request path (`_audit`, then `_shadow` with the audit as its reference), then one
        `Ledger.update_request` with the verdicts, the cost and token columns they add to (summed call by call in the
        request path's order, so the row ends with the floats an inline run writes), whether an example was stored and,
        after a teacher audit, the baseline the audit prices. The arbitration gives up after ARBITRATION_DEADLINE_S
        (an unarbitrated disagreement counts against the shadow, so a shorter wait cannot flatter it). The audit keeps
        the teacher's own deadline: a failed audit leaves the sample, and slow answers (often the hard requests, where
        the student errs) leaving it more often would bias the audited precision upward. Runs on a sidecar worker in a
        copy of the request's context, or inline when the sidecar refused the job."""
        reference, teacher_failed = op.reference, op.teacher_failed
        stored = False
        baseline: float | None = None
        cost: list[float] = []
        student_cost: list[float] = []
        teacher_cost: list[float] = []
        student_in = student_out = 0
        cols: dict[str, Any] = {}
        audit: AuditOutcome | None = None
        if op.audit_version is not None:
            assert op.student is not None
            audit = self._audit(op.task, op.messages, op.plan, op.meta, op.client, op.t0, op.student)
            teacher_failed = audit.failed
            cols["audited"] = audit.audited
            if audit.audited:
                cols["audit_agree"] = audit.agree
                cols["student_agree"] = audit.agree  # the replay's label of the student's answer (FR-16.5, ADR 0028)
                reference = audit.completion
            if audit.completion is not None:
                stored = audit.stored
                cost.append(audit.cost)
                teacher_cost.append(audit.cost)
                # The teacher alone would have written one answer per choice the client got.
                baseline = self._cost(self.s.teacher_model, _asked([audit.completion], audit.first)[0], op.choices)
        shadow: ShadowOutcome | None = None
        if op.shadow is not None:
            shadow = self._shadow(
                op.task,
                op.messages,
                op.plan,
                op.meta,
                op.client,
                op.t0,
                op.shadow,
                op.served,
                reference,
                teacher_failed,
                ARBITRATION_DEADLINE_S,
            )
            cols.update(shadow_version=shadow.version, shadow_serve=shadow.serve, shadow_agree=shadow.agree)
            if shadow.samples is not None:
                cost.append(shadow.sample_cost)
                student_cost.append(shadow.sample_cost)
                student_in += shadow.samples[0].in_tokens
                student_out += sum(s.out_tokens for s in shadow.samples)
            if shadow.arbitration is not None:
                stored = shadow.stored
                cost.append(shadow.arbitration_cost)
                teacher_cost.append(shadow.arbitration_cost)
        if stored:
            cols["stored"] = 1
        if baseline is not None:
            cols["baseline_cost_usd"] = baseline
        for column, terms in (("cost_usd", cost), ("student_cost_usd", student_cost), ("teacher_cost_usd", teacher_cost)):
            if terms:
                cols[f"add_{column}"] = terms
        if student_in or student_out:
            cols.update(add_student_in_tokens=student_in, add_student_out_tokens=student_out)
        self.ledger.update_request(op.row, **cols)
        label = metrics.client_label(op.client)
        overhead = sum(cost)
        _spend(metrics.COST, label, overhead)
        if overhead > 0 and self.on_offpath_spend is not None:  # against the key's and the gateway's budgets (ADR 0063)
            self.on_offpath_spend(op.client, overhead)
        if op.audit_version is not None:  # `handle` left an off-path audit's baseline out of the metric; a shadow's it counted
            _spend(metrics.BASELINE, label, baseline if baseline is not None else op.baseline)
        obs.log(
            logging.INFO,
            "offpath",
            row=op.row,
            audited=cols.get("audited"),
            audit_agree=cols.get("audit_agree"),
            shadow_version=cols.get("shadow_version"),
            shadow_serve=cols.get("shadow_serve"),
            shadow_agree=cols.get("shadow_agree"),
            stored=stored,
            cost_usd=overhead,
            pending_ms=round((self.timer() - op.t0) * 1000, 1),
        )

    def close(self, drain_s: float) -> dict[str, Any]:
        """Stop the sidecar (the gateway's shutdown, ADR 0072): wait at most `drain_s` seconds for the queued jobs, then
        give up on the rest. Their rows stay pending (`audited` 1 with `audit_agree` NULL; `shadow_version` with
        `shadow_serve` NULL), which the release gate ignores; what was given up is counted per version
        (`sidecar_report`) and written as one `sidecar_dropped` event. Returns `sidecar_report()`."""
        left = self.sidecar.close(drain_s)
        audits: Counter[int] = Counter()
        shadows: Counter[int] = Counter()
        for (audit_vid, shadow_vid), n in left.items():
            if audit_vid is not None:
                audits[audit_vid] += n
            if shadow_vid is not None:
                shadows[shadow_vid] += n
        if left:
            with self._errors_lock:
                self._abandoned_audits.update(audits)
                self._abandoned_shadows.update(shadows)
            parts = [f"audits v{v}: {n}" for v, n in sorted(audits.items())]
            parts += [f"shadows v{v}: {n}" for v, n in sorted(shadows.items())]
            try:
                self.ledger.event(
                    "sidecar_dropped",
                    f"shutdown: {sum(left.values())} off-path jobs not run after {drain_s:g} s ({'; '.join(parts)}); "
                    "their rows stay pending and the release gate ignores them",
                )
            except Exception:  # noqa: BLE001 - a closed ledger: the counts are still in the report and the log
                pass
            obs.log(logging.WARNING, "sidecar_abandoned", jobs=sum(left.values()), audits=dict(audits), shadows=dict(shadows))
        return self.sidecar_report()

    def sidecar_report(self) -> dict[str, Any]:
        """The sidecar's state and counts (`SideCar.snapshot`) and, per version, what could not go through it since
        start: audits that ran on the request path because the queue was full (`audits_inline`), shadows skipped for the
        same reason (`shadows_dropped`), and jobs given up at shutdown (`audits_abandoned`, `shadows_abandoned`). Every
        routed version (live, canary, shadow) is listed, with zeros when nothing was lost, and so is any other version
        that lost something. For /ratchet/stats."""
        snap = self.sidecar.snapshot()
        with self._errors_lock:
            counters = {
                "audits_inline": Counter(self._audits_inline),
                "shadows_dropped": Counter(self._shadows_dropped),
                "audits_abandoned": Counter(self._abandoned_audits),
                "shadows_abandoned": Counter(self._abandoned_shadows),
            }
        routed = [self.ledger.live(), *(self.ledger.by_state(state) for state in ("canary", "shadow"))]
        versions = sorted(set().union(*counters.values(), (v.id for v in routed if v is not None)))
        by_version = [{"version": v, **{k: c[v] for k, c in counters.items()}} for v in versions]
        return {**snap, "by_version": by_version}

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
        """Each version carries its own threshold, calibrated on the golden set at training time, and since FR-14.1 one
        per task (`ratchet.calibration.threshold`): the task's own when the version's eval has a record for it under its
        current spec (1.01, SERVE_NOTHING, when the release gate disabled the task on this version or it had no held-out
        rows), SERVE_NOTHING when the version was scored under another spec of the task (the teacher answers until a
        night re-scores it, FR-2.15), else the version's global threshold. A version scored before per-task calibration
        has only the global one, and one that is not a number serves nothing. The hosted untuned fallback model was never
        calibrated, so it uses the global default, except on a task with critical classes, which it never answers: nothing
        measured how often it misses them (FR-2.50). Dict lookups only."""
        if model is not None and model == self.s.fallback_student_model:
            critical = task is not None and bool(_task(task).routing_rules.critical)
            return SERVE_NOTHING if critical else self.s.confidence_threshold
        raw = version.eval.get("threshold")
        default = self.s.confidence_threshold if raw is None else calibration.number(raw)
        if default is None:  # a hand-edited threshold that is not a number: never guess, never fail the request (INV-7)
            default = SERVE_NOTHING
        if task is None:
            return default
        return calibration.threshold(version.eval, _task(task), default)

    def _held_back(self, version: Version, task: tasks.Task) -> bool:
        """Whether `version` holds `task` at SERVE_NOTHING by its own per-task record (`calibration.held_back`), so the
        student need not be called. A version served by the hosted untuned fallback model ignores its records
        (`threshold`), so it is never held back, except on a task with critical classes, which that model never answers
        (FR-2.50): such a task is held back whenever the resolver picks that model, whatever the record says, so the
        model is not sampled (and paid for) for answers `threshold` would never serve. The resolver is asked only when
        the answer depends on it; one that fails here leaves a held-back task held back and a critical task with a
        serving record to the student path, which fails over to the teacher as before."""
        critical = bool(task.routing_rules.critical)
        if not calibration.held_back(version.eval, task):
            if not (critical and self.s.fallback_student_model):
                return False
            try:
                return self.resolve_student(version)[1] == self.s.fallback_student_model
            except Exception:
                return False
        if not self.s.fallback_student_model or critical:
            return True
        try:
            return self.resolve_student(version)[1] != self.s.fallback_student_model
        except Exception:
            return True

    @staticmethod
    def spec_changed(version: Version, task: tasks.Task | str) -> bool:
        """Whether `version` was scored under another spec of `task` than the current one: its per-task record (or the
        older `eval['spec_hashes']`, which `run_night` still writes) names another `spec_hash`. A version without a
        record for the task (scored before hashes were recorded, or with no golden rows of it) has nothing to
        compare."""
        return calibration.spec_changed(version.eval, _task(task))

    def _sample(
        self,
        version: Version,
        messages: list[dict[str, Any]],
        role: str = "student",
        gen: GenParams | None = None,
        n: int | None = None,
    ) -> tuple[list[Completion], float, str]:
        """The student's samples under `gen` (the plan's `student` parameters), `n` of them (`Settings.samples` when
        None; `handle` and the shadow pass the task's own count, and more only to fill a client's n choices), through the
        `role` breaker of its endpoint (`role`, student or shadow, also names the call in the metrics and the log). Raises
        `CircuitOpen` without calling when that breaker is open, and `AdapterMissing` when vLLM answers 404 naming the
        version's adapter (an endpoint restart forgets runtime-loaded adapters; that 404 is the endpoint answering, so it
        does not count against it)."""
        provider, model = self.resolve_student(version)
        breaker = self._breaker(role, provider)
        if not breaker.allow():
            metrics.UPSTREAM.inc(role, "circuit_open")
            raise CircuitOpen(breaker.name)
        start = time.perf_counter()
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
            if isinstance(e, Exception):
                metrics.UPSTREAM.inc(role, _outcome(e))
                metrics.UPSTREAM_SECONDS.observe(time.perf_counter() - start, role)
                obs.upstream_failure(role, e)
            if model == version.adapter_uri and adapter_missing(e, model):
                raise AdapterMissing(model, e.message) from e
            raise
        breaker.record(True)
        metrics.UPSTREAM.inc(role, "ok")
        metrics.UPSTREAM_SECONDS.observe(time.perf_counter() - start, role)
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
        meta: RequestMeta | None = None,
        opt_out: str | None = None,
        t0: float | None = None,
    ) -> Result:
        """The teacher alone: a pinned task, a request the student cannot take (`teacher_only`), an unregistered task
        under RATCHET_UNKNOWN_TASKS=teacher (`route` `unknown_task`), or a tool request (`tools`, FR-1.41). The request
        is logged, but the answer is never stored as a training example, so it cannot leak into a student that was never
        meant to do it (INV-10), and the student is never sampled. When the primary teacher is unavailable, the fallback
        teacher may answer (route `fallback_teacher`); the student never does, so this path never degrades. The baseline
        is the primary teacher's price for the same tokens. `params` are the client's, for the logprobs it asked to get
        back. For a tool request the answer may be tool calls, returned as the teacher wrote them (`Choice.tool_calls`);
        for any other request (a pinned task offering no tools, say) a tool call is a 502 `tool_calls_unsupported`, as
        in the cascade. The row records whether the request used tools (`requests.uses_tools`). An answer that breaks
        the request's `response_format` is repaired once (`_repair`): both calls are paid for in `cost_usd` and their
        latencies added, while the baseline and the client's usage count the request's own prompt (`_asked`). The row
        records the end user's hash and the request's storage opt-out (`opt_out`), as every request does. `t0` is when
        the caller started the request on `self.timer` (now, when not given), for `wall_ms`. A teacher failure gets a
        `teacher` failures row (nothing was spent) before it is raised; `meta` names the request in it and in the
        request's row."""
        t0 = self.timer() if t0 is None else t0
        plan = plan or self._plain
        tool_request = uses_tools(messages, params)  # only then may the answer be tool calls (`_ask_teacher`)
        if meta is not None and meta.student_only:  # the gateway's budget is spent: no teacher call (ADR 0063)
            self._budget_refused(route, task, client, meta, t0)
        try:
            served, primary = self._serve_teacher(task, messages, plan.serve, plan.visible_max, plan.n, tool_calls=tool_request)
        except Exception as e:
            self._teacher_failed(e, task, client, meta, t0, 0.0)
            raise
        if primary is not None:  # the fallback teacher answered: the primary's failure is this request's
            self._record_failure("teacher", primary, task, client, meta, t0)
        fallback = primary is not None
        served, rejected, first = self._repair(task, messages, plan.serve, plan.visible_max, fallback, served)
        c = served[0]
        asked = _asked(served, first)
        model = self._teacher_model(fallback)
        route = "fallback_teacher" if fallback else route
        cost = self._call_cost(model, served)
        baseline = self._call_cost(self.s.teacher_model, asked) if fallback or first is not None else cost
        wall_ms = (self.timer() - t0) * 1000
        latency = c.latency_ms
        if rejected is not None:  # a repair: both calls are paid for, and they ran one after the other
            cost += self._cost(model, rejected)
            latency += rejected.latency_ms
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
            latency_ms=latency,
            cost_usd=cost,
            baseline_cost_usd=baseline,
            student_cost_usd=0.0,  # the student is never sampled here: every cent is the teacher's (FR-15.1)
            teacher_cost_usd=cost,
            student_in_tokens=0,
            student_out_tokens=0,
            client=client,
            route=route,
            stored=0,
            end_user=meta.end_user if meta is not None else None,
            store_opt_out=opt_out,
            request_id=meta.request_id if meta is not None else None,
            end_user_secondary=meta.end_user_secondary if meta is not None else None,
            wall_ms=wall_ms,
            client_request_id=meta.client_request_id if meta is not None else None,
            retry_count=meta.retry_count if meta is not None else 0,
            uses_tools=int(tool_request),
        )
        self._count_request(task, "teacher", route, wall_ms, cost, baseline, client, False, meta)
        choices = _choices(task, served, params)
        return Result(
            text=choices[0].text,
            served_by="teacher",
            version=None,
            confidence=None,
            escalated=False,
            cost_usd=cost,
            baseline_usd=baseline,
            latency_ms=latency,
            model=c.model,
            route=route,
            finish_reason=choices[0].finish_reason,
            **_usage(messages, asked, choices),
            fingerprint=fingerprint("teacher", model),
            usage_breakdown=_breakdown(served, repaired=[rejected] if rejected is not None else None),
            choices=choices,
            teacher_cost_usd=cost,
            request_id=request_id,
            store_opt_out=opt_out,
            wall_ms=wall_ms,
            task=task.name,
            schema_valid=_schema_valid(task, choices),
        )

    def teacher_only(
        self,
        messages: list[dict[str, Any]],
        task: str | tasks.Task | None = None,
        client: str | None = None,
        params: GenParams | None = None,
        meta: RequestMeta | None = None,
        n: int = 1,
        route: str | None = None,
    ) -> Result:
        """A request the student cannot take, such as one with an image part the teacher reads
        (RATCHET_TEACHER_MODALITIES; the gateway passes `route` `multimodal`): the pinned path, so the teacher answers (or
        the fallback teacher, when the primary is unavailable), the request is logged, and nothing is stored as a
        training example. No student, audit or shadow call, so it never degrades. `n` choices, as for `handle`. An
        unregistered task under RATCHET_UNKNOWN_TASKS=reject raises `tasks.UnknownTask`. The row's route says why the
        teacher answered alone, the operator's reason first: `unknown_task` (an unregistered task under `teacher`),
        `pinned` (a pinned task), then `route`, then `tools` for a tool request (FR-1.41), else `pinned`. A
        `response_format` binds the task as in `handle`."""
        t0 = self.timer()
        self.ledger.flags()  # publishes the runtime pins before the task is resolved (ADR 0064)
        task = self.resolve(task, params)
        params = schema_tasks.forwardable(params)
        self._admit(task, client)
        if task.source == "adhoc" and self.unknown_tasks == "teacher":
            route = "unknown_task"
        elif task.pinned:
            route = "pinned"
        elif route is None:
            route = "tools" if uses_tools(messages, params) else "pinned"
        meta, opt_out = self._opted_out(task, meta)
        plan = self.plan(params, n)
        return self._teacher_only(task, messages, client, plan, params, route=route, meta=meta, opt_out=opt_out, t0=t0)

    def audit_rate(self, task: tasks.Task) -> float:
        """The share of the task's served student answers the teacher audits: its spec's `policy.audit_rate` when it
        sets one (FR-2.50), else RATCHET_AUDIT_RATE. One draw of the router's random generator either way, so a task
        without its own rate is audited exactly as before."""
        rate = calibration.number(task.policy.get("audit_rate"))
        return self.s.audit_rate if rate is None else rate

    @staticmethod
    def _rule(task: tasks.Task, sc: Scored) -> int | None:
        """The index of the task's first `escalate_when` rule that holds for the scored answer (`routing.Routing.
        escalation`), or None. A task without rules returns at once; one with rules compares the answer's key, which
        `confidence.score` computed already."""
        rules = task.routing_rules
        if not rules.rules:
            return None
        key = sc.key if sc.key is not None else task.key(sc.answer.text)
        return rules.escalation(key, sc.confidence)

    def _ineligible(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        reason: str,
        client: str | None = None,
        plan: GenPlan | None = None,
        params: GenParams | None = None,
        meta: RequestMeta | None = None,
        opt_out: str | None = None,
        t0: float | None = None,
    ) -> Result:
        """A request the task's routing block keeps from the student (FR-2.50, route `ineligible:<reason>`): more input
        than `student_max_input_tokens`, or a content part the student does not read. The teacher answers it (or the
        fallback teacher, route `fallback_teacher`, when the primary is unavailable); no version is picked, the student
        is not called, and there is no audit or shadow, since a candidate student could not take it either. The answer
        is stored only when `routing.Routing.stores` allows (an over-length request with `store_ineligible`), through
        `_store_example` and its rules (INV-2, INV-12, INV-16); otherwise the teacher call's meta is not storable, so
        nothing of it reaches the training set, image bytes included. The row records the request's own storage opt-out
        (`opt_out`) and end user, as every row does; an answer that breaks the `response_format` is repaired once and
        priced as in the cascade; under `meta.student_only` (ADR 0063) the request is refused before any teacher call."""
        t0 = self.timer() if t0 is None else t0
        plan = plan or self._plain
        route = f"{INELIGIBLE_ROUTE}{reason}"
        if meta is not None and meta.student_only:  # the gateway's budget is spent: no teacher call (ADR 0063)
            self._budget_refused(route, task, client, meta, t0)
        call_meta = meta
        if not task.routing_rules.stores(reason):
            call_meta = replace(meta, storable=False) if meta is not None else RequestMeta(storable=False)
        try:
            answer = self._call_teacher(task, messages, plan, "serve", call_meta, client)
        except Exception as e:
            self._teacher_failed(e, task, client, meta, t0, 0.0)
            raise
        if answer.primary_error is not None:  # the fallback teacher answered for the primary
            self._record_failure("teacher", answer.primary_error, task, client, meta, t0)
        served = list(answer.choices)
        c = answer.completion
        if answer.fallback:
            route = "fallback_teacher"
        cost = self._call_cost(answer.model, served)
        latency = c.latency_ms
        repaired: list[Completion] = []
        first = None
        if answer.rejected is not None:  # a repair: both calls are paid for, and they ran one after the other
            cost += self._cost(answer.model, answer.rejected)
            latency += answer.rejected.latency_ms
            repaired.append(answer.rejected)
            first = answer.first
        asked = _asked(served, first)
        baseline = self._call_cost(self.s.teacher_model, asked)
        if meta is not None and call_meta is meta and not meta.storable:  # an opt-out made while the request ran
            opt_out = meta.store_opt_out
        wall_ms = (self.timer() - t0) * 1000
        request_id = self.ledger.log_request(
            task=task.name,
            served_by="teacher",
            student_version=None,
            confidence=None,
            escalated=0,  # the student never had the request: not an escalation, as for a pinned task
            audited=0,
            audit_agree=None,
            shadow_version=None,
            shadow_serve=None,
            shadow_agree=None,
            latency_ms=latency,
            cost_usd=cost,
            baseline_cost_usd=baseline,
            student_cost_usd=0.0,
            teacher_cost_usd=cost,
            student_in_tokens=0,
            student_out_tokens=0,
            client=client,
            route=route,
            stored=int(answer.stored),
            end_user=meta.end_user if meta is not None else None,
            store_opt_out=opt_out,
            request_id=meta.request_id if meta is not None else None,
            end_user_secondary=meta.end_user_secondary if meta is not None else None,
            wall_ms=wall_ms,
            client_request_id=meta.client_request_id if meta is not None else None,
            retry_count=meta.retry_count if meta is not None else 0,
            uses_tools=0,  # a tool request goes to the teacher alone before this check (route `tools`)
        )
        self._count_request(task, "teacher", route, wall_ms, cost, baseline, client, False, meta)
        choices = _choices(task, served, params)
        return Result(
            text=choices[0].text,
            served_by="teacher",
            version=None,
            confidence=None,
            escalated=False,
            cost_usd=cost,
            baseline_usd=baseline,
            latency_ms=latency,
            model=c.model,
            route=route,
            finish_reason=choices[0].finish_reason,
            **_usage(messages, asked, choices),
            fingerprint=fingerprint("teacher", answer.model),
            usage_breakdown=_breakdown(served, repaired=repaired or None),
            choices=choices,
            teacher_cost_usd=cost,
            request_id=request_id,
            stored=answer.stored,
            store_opt_out=opt_out,
            wall_ms=wall_ms,
            task=task.name,
            schema_valid=_schema_valid(task, choices),
        )

    def task_for(self, task: str | tasks.Task | None, client: str | None = None, params: GenParams | None = None) -> tasks.Task:
        """The task a request names, as the gateway admits it before any quota or model call: a registered task, or for
        an unregistered name a task without fields made up for it (`tasks.get`), which RATCHET_UNKNOWN_TASKS decides
        about (`reject` raises `tasks.UnknownTask`, listing the tasks this client may use). A name that is not a task
        name (an X-Ratchet-Task header; metadata is checked earlier) means 'default' under `distill`, as in v0.4, and
        raises `tasks.UnknownTask` under `teacher` and `reject`, so it cannot become 'default' training data there. A
        task with `clients` raises `tasks.TaskForbidden` for any other API key, and for a request without one. With
        `params`, a `response_format` binds the task and may name or register an auto task (`resolve`); otherwise
        lookups only."""
        if isinstance(task, str) and task and not tasks.valid_name(task) and self.unknown_tasks != "distill":
            raise tasks.UnknownTask(task, tasks.names_for(client), invalid=True)
        self.ledger.flags()  # publishes the runtime pins before the task is resolved (ADR 0064)
        t = self.resolve(task, params)
        self._admit(t, client)
        if not t.allows(client):
            raise tasks.TaskForbidden(t.name)
        return t

    def resolve(self, task: str | tasks.Task | None, params: GenParams | None = None) -> tasks.Task:
        """The task of a request with these parameters (FR-1.40, FR-14.2, ADR 0042). Without a `response_format` it is
        `tasks.get(task)` (a `tasks.Task` is kept), exactly as before. With one, the task is bound to its contract
        (`schema_tasks.contract_for`, `Task.bound`), and for json_schema:

        - a name (header or metadata) of an operator's task (built-in, tasks file, admin API) wins; the schema is only
          forwarded and checked;
        - an unregistered name becomes an auto task of that name under RATCHET_UNKNOWN_TASKS=distill (`teacher` and
          `reject` keep it unknown: they govern the names clients make up), except a name starting with `auto-`: those
          are derived from schemas, so a client's own is treated as no name;
        - no name: the auto task `schema_tasks.auto_name(...)` (`auto-<schema name>-<hash>`), registered on first use.

        An auto task stands for the fields its schema derives, so a request uses one only when its own schema derives
        the same rules (`_derives`, the same `spec_hash`); otherwise one client could choose the fields another client's
        answers are compared on. A name an auto task of another schema holds is a collision (a throttled
        `auto_task_collision` event): a client's name then falls back to the request's own `auto_name`, and an auto name
        gets an unregistered task that stores nothing, which RATCHET_UNKNOWN_TASKS decides about.

        An auto task comes from `schema_tasks.auto_task` (fields derived from the schema, source `auto`, never pinned)
        and is registered and stored (`_auto_task`); a schema with no comparable field, one past RATCHET_MAX_AUTO_TASKS,
        or one whose derived spec holds personal data gets an unregistered task of that name instead, which
        RATCHET_UNKNOWN_TASKS decides about. With RATCHET_MAX_AUTO_TASKS=0 (auto tasks off), and for json_object (or a
        json_schema without a schema), the request keeps the task it names, or `default` (canonical JSON compared), held
        to its contract."""
        contract = schema_tasks.contract_for(params.response_format if params is not None else None)
        if isinstance(task, tasks.Task):  # Scout passes the Task itself: an operator's runtime pin holds all the same
            task = tasks.effective(task)
            return task if contract is None or task.contract is not None else task.bound(contract)
        t = tasks.get(task)
        if contract is None:
            return t
        if contract.kind != "json_schema" or contract.schema is None:
            return t.bound(contract)
        named = isinstance(task, str) and bool(task) and tasks.valid_name(task)
        if named and t.source not in ("adhoc", "auto"):
            return t.bound(contract)  # an operator's task wins
        rf = params.response_format if params is not None else None
        spec = rf.get("json_schema") if isinstance(rf, Mapping) else None
        spec = spec if isinstance(spec, Mapping) else {}
        schema = spec.get("schema")
        if named and t.source == "auto":
            if self._derives(t, schema, contract.name):
                return t.bound(contract)
            self._auto_collision(t.name)
            named = False  # another schema's auto task: this request gets its own schema's
        if self.max_auto_tasks == 0:  # auto tasks off: the named task (RATCHET_UNKNOWN_TASKS decides), or `default`
            return (t if named else tasks.get(None)).bound(contract)
        if named and str(task).startswith(schema_tasks.AUTO_PREFIX):
            named = False  # auto names are derived from schemas: a client's own never names a new task
        if named and self.unknown_tasks != "distill":
            return t.bound(contract)  # an unknown name stays unknown under `teacher` and `reject`
        name = task if named else schema_tasks.auto_name(spec)
        assert isinstance(name, str)
        found = tasks.lookup(name) or self._auto_task(name, schema, contract.name)
        if found is None:  # no comparable field, past the cap, or personal data in the derived spec
            return tasks.Task(name, source="adhoc").bound(contract)
        if found.source != "auto" or self._derives(found, schema, contract.name):
            return found.bound(contract)
        self._auto_collision(name)
        if named:  # registered meanwhile by another schema's request
            return self.resolve(None, params)
        # Two schemas with one auto name (a digest collision): nothing is stored under a name whose fields are not this
        # schema's.
        return tasks.Task(name, source="adhoc", store=False).bound(contract)

    def _derives(self, task: tasks.Task, schema: Any, schema_name: str | None) -> bool:
        """Whether `schema` derives the rules of the auto task `task` (`schema_tasks.auto_task`, the same `spec_hash`;
        the description may differ). Cached per schema."""
        mine = schema_tasks.auto_task(task.name, schema, schema_name)
        return mine is not None and mine.spec_hash == task.spec_hash

    def _auto_collision(self, name: str) -> None:
        """Count a request whose schema derives other fields than the auto task under `name`, with an
        `auto_task_collision` event on the 1st, 10th, 100th and 1000th. Names the task, never the schema."""
        detail = f"{name}: registered from another schema; this request's schema derives other fields"
        self._count_failure("auto_task_collisions", "auto_task_collision", detail)

    def _auto_task(self, name: str, schema: Any, schema_name: str | None) -> tasks.Task | None:
        """Register and store the auto task `name` derived from `schema`, or return the task registered under that name
        meanwhile (by another request or the admin API, which wins). None when the schema has no comparable field, the
        registry already holds RATCHET_MAX_AUTO_TASKS auto tasks (an `auto_task_cap` event, once per ledger), the
        derived spec holds something the redactor would replace (an enum value that looks like an email: the `tasks`
        table stores rules, never personal data, INV-12; an `auto_task_skipped` event), or the registry refuses it.
        Under `tasks_lock`, the lock the gateway's admin writes take, so the registry and the `tasks` table never
        disagree. Runs once per new schema; a known one is a dict lookup in `resolve`."""
        task = schema_tasks.auto_task(name, schema, schema_name)
        if task is None:
            return None
        with self.tasks_lock:
            current = tasks.lookup(name)
            if current is not None:
                return current
            if sum(1 for t in tasks.registered() if t.source == "auto") >= self.max_auto_tasks:
                if not self._auto_cap_noted:
                    self._auto_cap_noted = True
                    detail = (
                        f"RATCHET_MAX_AUTO_TASKS ({self.max_auto_tasks}) reached: requests with a new response_format "
                        "schema get the unknown-task path (RATCHET_UNKNOWN_TASKS) until an auto task is deleted"
                    )
                    self.ledger.event_once("auto_task_cap", "RATCHET_MAX_AUTO_TASKS", detail)
                return None
            doc = taskspec.document(task)
            if redact_json(doc) != doc:
                self._skip_example(task, "auto task not stored: its schema holds personal data", kind="auto_task_skipped")
                return None
            try:
                tasks.register(task)
            except (tasks.TaskConflict, tasks.RegistryFull, ValueError):
                return tasks.lookup(name)
            try:
                self.ledger.save_task(name, doc, task.spec_hash, "auto", None)
            except BaseException:
                tasks.restore(name, None)  # the registry and the table never disagree
                raise
        self.ledger.event("task_registered", f"{name} (spec {task.spec_hash}) derived from a response_format schema")
        return task

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

        The student draws the task's `policy.samples`, else `Settings.samples` (FR-2.55; the shadow too), and for `n` > 1
        at least `n`. Its confidence is scored on the first of them, as many as the golden eval scored for the task
        (exactly as calibrated), and it serves only when that is at or above the threshold and at least `n` samples share
        the majority answer (`confidence.majority`); choice 0 is then the sample a one-choice request would serve. A
        confident student that cannot fill `n` choices escalates (route `n_choices`). The teacher writes `n` choices (at
        `CHOICES_TEMPERATURE` when the client set no temperature), and only choice 0 can become a training example.
        Audits, arbitrations and the shadow judge choice 0, so release statistics do not depend on `n`.

        When the teacher answer an escalation needs cannot be had (ADR 0019), the fallback teacher answers if one is set
        (route `fallback_teacher`), else, with RATCHET_ON_TEACHER_FAILURE=serve_student, the student's own valid answer
        (route `degraded`; for `n` > 1 only when `n` of its samples agree), else the teacher's error is raised for the
        gateway to map (INV-7).

        A pinned task, and an unregistered one under RATCHET_UNKNOWN_TASKS=teacher (route `unknown_task`), go to the
        teacher alone (`_teacher_only`); under `reject` an unregistered task raises `tasks.UnknownTask`. So does a tool
        request (`uses_tools`: tools offered, or tool calls or results in its messages; route `tools`, FR-1.41): its
        tools reach the teacher, its tool calls come back to the client, and nothing is stored, whatever the task's
        `tools` says until tool-call distillation is built (the student is never sent tools, so its answer could not be
        the call the client expects, and a stored tool call needs comparators of its own; ADR 0043). The client
        allow-list (`Task.clients`) is the gateway's check (`task_for`): internal callers pass no key. A task whose
        record on the serving version lets the student answer nothing (the release gate disabled it, or the version had
        no held-out row of it) is escalated without a student call (route `task_disabled`, FR-14.1), so it costs no
        student samples and is never answered in degraded mode; it still runs the shadow and can be stored.

        The task's routing block (FR-2.50, ADR 0049; `routing.Routing`) runs in two places. Before a version is picked,
        a request the student must not see (more input than `student_max_input_tokens`, or a content part it does not
        read) goes to the teacher alone (`_ineligible`, route `ineligible:<reason>`). After the threshold, the first
        `escalate_when` rule that holds for a confident student's answer sends it to the teacher (route `rule:<i>`); the
        teacher's answer is stored as for any escalation, and such an answer never stands in for a teacher that is down.
        A task's `policy.audit_rate` replaces RATCHET_AUDIT_RATE for its served answers (`audit_rate`).

        A `response_format` binds the task to its contract (`resolve`; json_schema may name an auto task): a student
        sample that breaks it gets no vote, a teacher answer that breaks it is repaired once (`_repair`) and is never
        stored, served text is the JSON an answer holds, and `Result.schema_valid` says whether every choice meets it.
        Every model is sent the schema without its `x-ratchet` annotations (`schema_tasks.forwardable`) (FR-1.40).

        Whether the request's teacher answers may be stored is settled once, up front (`store_opt_out`, FR-13.1): under an
        opt-out the cascade runs exactly as it would otherwise (audits, shadows and arbitrations included), nothing is
        inserted or bumped, and the row records the reason (`requests.store_opt_out`) and the end user's hash. An opt-out
        made while the request runs is caught by the store check and reported the same way.

        With `meta.student_only` (the gateway's daily budget is spent, RATCHET_BUDGET_MODE=student_only; ADR 0063) only
        a confident student answer is served, with no audit (the draw is still made, so the random stream stays the same)
        and no shadow (its arbitration would need the teacher, and an unarbitrated disagreement would count against the
        candidate). A request that needs the teacher (an escalation, a cold start, a pinned or unregistered task, `tools`)
        raises `spend.BudgetExceeded` before any teacher call, its student spend recorded as a failed request's.

        An operator's runtime switches (`Ledger.flags`, ADR 0064) are read once, first: a task pinned at runtime takes
        the pinned path, `student_disabled` sends the request to the teacher with no student or shadow call (route
        `student_disabled`; the teacher's answer is stored as on a cold start), and a runtime `audit_rate` replaces
        `Settings.audit_rate`.

        `requests.wall_ms` is the time from here to the ledger write on `self.timer`, every model call included;
        `latency_ms` stays the providers' own time, so `wall_ms - latency_ms` is Ratchet's overhead (ADR 0060)."""
        t0 = self.timer()
        flags = self.ledger.flags()  # the operator's switches and task pins, one snapshot for the request (ADR 0064)
        task = self.resolve(task, params)
        params = schema_tasks.forwardable(params)
        plan = self.plan(params, n)
        self._admit(task, client)
        # Settled once: whether this request's teacher answers may be stored, and if not why (FR-13.1).
        meta, opt_out = self._opted_out(task, meta)
        if task.source == "adhoc" and self.unknown_tasks == "teacher":  # not registered: never distilled (ADR 0022)
            return self._teacher_only(
                task, messages, client, plan, params, route="unknown_task", meta=meta, opt_out=opt_out, t0=t0
            )
        if task.pinned:
            return self._teacher_only(task, messages, client, plan, params, meta=meta, opt_out=opt_out, t0=t0)
        student_only = meta is not None and meta.student_only  # the gateway's budget is spent (ADR 0063)
        if student_only and params is not None and params.tools:  # the student does not see tools: only the teacher could
            self._budget_refused("tools", task, client, meta, t0)
        if uses_tools(messages, params):  # tool calls pass through, teacher only, never stored (FR-1.41, ADR 0043)
            return self._teacher_only(task, messages, client, plan, params, route="tools", meta=meta, opt_out=opt_out, t0=t0)
        reason = task.routing_rules.ineligible(messages)  # FR-2.50: before a version is picked or the student called
        if reason is not None:
            return self._ineligible(task, messages, reason, client, plan, params, meta, opt_out, t0)
        version = self._pick_version(flags)
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
        route = "student_disabled" if flags.student_disabled else "cold_start"
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
        # Off the request path (ADR 0072): the queue place the request's checks hold, the version whose drawn audit
        # waits there, and the shadow candidate that waits there. The draws themselves stay here, in the same order.
        offpath = self._offpath(meta)
        slot: Slot | None = None
        pending_audit: int | None = None
        pending_shadow: Version | None = None
        repaired: list[Completion] = []  # teacher answers a repair left unused (FR-1.40), paid for and in the breakdown
        served_first: Completion | None = None  # the served teacher call's first answer, after a repair (`_asked`)
        audit_first: Completion | None = None  # the same for the audit

        sc = None
        # The samples one confidence score of this task rests on: its policy's, else RATCHET_SAMPLES (FR-2.55), as the
        # golden eval calibrated it.
        k = task.sample_count(self.s.samples)
        if version is not None and self._held_back(version, task):
            # The version's record for the task serves nothing: its samples could never be served, so the student is not
            # called, and with no student answer nothing below the task's floor can stand in for the teacher (FR-14.1).
            escalated, route = True, "task_disabled"
        elif version is not None:
            try:
                samples, c, model_used = self._sample(version, messages, gen=plan.student, n=max(k, plan.n))
            except CircuitOpen as e:  # the endpoint failed too often lately: not called, not counted again
                escalated, route = True, "student_breaker"
                self._record_failure("student", e, task, client, meta, t0)
            except Exception as e:  # student endpoint down or erroring: the teacher still answers
                if isinstance(e, AdapterMissing):
                    self._request_reload(version)
                # The class, status and code only: an endpoint's error message can quote the request (INV-12).
                self._count_failure("student_errors", "student_error", f"v{version.id}: {describe_failure(e)}")
                self._record_failure("student", e, task, client, meta, t0)
                escalated, route = True, "student_error"
            else:
                latency += max(s.latency_ms for s in samples)
                cost += c
                student_cost += c
                student_in += samples[0].in_tokens
                student_out += sum(s.out_tokens for s in samples)
                # Scored on the calibrated number of samples; the rest are drawn only to fill n choices (ADR 0020).
                sc = score(task, samples[: max(1, k)])
                picked = majority(task, sc, samples, plan.n)
                confidence = sc.confidence
                student_c = sc.answer
        if version is not None and sc is not None:
            threshold = threshold_used = self.threshold(version, model_used, task)
            # A sample set with no valid answer is never served, whatever the threshold (confidence.Scored.valid).
            confident = sc.valid and sc.confidence >= threshold
            # The task's escalate_when rules, on the confident answer's key (FR-2.50): a costly positive goes to the teacher.
            rule = self._rule(task, sc) if confident else None
            if rule is not None:
                escalated, route = True, f"{RULE_ROUTE}{rule}"
            elif confident and picked is not None:
                served, served_by, route, served_choices = sc.answer, "student", "student", picked
                fp = fingerprint("student", model_used, version.id, version.dataset_hash, threshold)
                # The task's own audit rate (`policy.audit_rate`, FR-2.50) or RATCHET_AUDIT_RATE; audit focusing (ADR
                # 0025): a canary may be audited at its own rate; an operator's runtime rate (`audit_rate`, ADR 0064)
                # replaces them all. Still one draw per served answer, drawn also under student_only, which skips the
                # call (and so observes no label: `label_p` 0).
                rate = self.audit_rate(task) if flags.audit_rate is None else flags.audit_rate
                if flags.audit_rate is None and version.state == "canary" and self.s.canary_audit_rate is not None:
                    rate = self.s.canary_audit_rate
                boost = self.audit_boost.get(version.id)  # an open drift alarm on this version (FR-6.40)
                if boost:
                    rate = boosted_rate(rate, boost)
                label_p = 0.0 if student_only else rate
                if self.rng.random() < rate and not student_only:  # audit = quality signal + free training data
                    if offpath:  # a reserved place: shadow work alone can never push an audit onto this path
                        slot = self.sidecar.reserve(priority=True, size=job_bytes(messages))
                        if slot is None:
                            self._sidecar_full("audit", version.id)
                    if slot is not None:
                        pending_audit = version.id  # runs after the answer is returned (`_finish_offpath`)
                    else:
                        audit = self._audit(task, messages, plan, meta, client, t0, sc.answer)
                        teacher_failed = audit.failed
                        if audit.completion is not None:
                            teacher_c, stored = audit.completion, audit.stored
                            audit_c = teacher_c
                            cost += audit.cost  # a repair's second call included (FR-1.40)
                            teacher_cost += audit.cost
                            if audit.rejected is not None:
                                repaired.append(audit.rejected)
                                audit_first = audit.first
                            audited, audit_agree = audit.audited, audit.agree
                            if audited:  # a counted audit labels the student's answer (FR-16.5)
                                student_agree = audit_agree
            elif confident:  # confident, but fewer than n samples agree
                escalated, route, label_p = True, "n_choices", 1.0
            else:
                escalated, route, label_p = True, "confidence", 1.0
        if served_by == "teacher":
            if student_only:  # the student could not answer alone, and the teacher is out of budget
                self._budget_refused("escalation", task, client, meta, t0, cost)
            try:
                answer = self._call_teacher(task, messages, plan, "serve", meta, client)
            except Exception as e:  # no teacher answer: degraded if allowed, else the gateway maps the error (INV-7)
                # An answer an escalate_when rule holds for never stands in, whatever the route: the rule says such an
                # answer must come from the teacher (FR-2.50).
                if not self._may_degrade(sc, params, e, picked) or (sc is not None and self._rule(task, sc) is not None):
                    self._teacher_failed(e, task, client, meta, t0, cost)  # what the student's samples cost (NFR-3)
                    raise
                # The student's own valid answer stands in. Not audited, so it never counts toward release statistics.
                assert version is not None and sc is not None and picked is not None
                teacher_failed = True
                served, served_by, route, degraded = sc.answer, "student", "degraded", TEACHER_UNAVAILABLE
                served_choices = picked
                threshold = self.threshold(version, model_used, task)
                fp = fingerprint("student", model_used, version.id, version.dataset_hash, threshold)
                self._count_failure("degraded_answers", "degraded", f"{task.name}: {describe_failure(e)}")
                self._record_failure("teacher", e, task, client, meta, t0)
            else:
                if answer.primary_error is not None:  # the fallback teacher answered for the primary
                    self._record_failure("teacher", answer.primary_error, task, client, meta, t0)
                teacher_c, stored = answer.completion, answer.stored
                served_choices = list(answer.choices)
                t = self._call_cost(answer.model, served_choices)
                if answer.rejected is not None:
                    t += self._cost(answer.model, answer.rejected)
                    repaired.append(answer.rejected)
                    served_first = answer.first
                    latency += answer.rejected.latency_ms  # the repair ran after the first call
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

        # A place reserved for this request's checks (the audit's, above, or the shadow's, below) is given back if anything
        # raises before the row is logged, so a drain never waits for a job that cannot come.
        try:
            # The all-teacher cost by the token formula (requests.baseline_est_usd, FR-16.5), for every student answer,
            # also when the teacher was called: on audited rows the replay compares it with what the teacher really cost
            # (ADR 0028). Over the choices the student serves, or would have served (its answer once per choice when
            # fewer agree).
            baseline_est: float | None = None
            if student_c is not None:
                baseline_est = self._formula_baseline(messages, student_c, picked if picked is not None else [student_c] * plan.n)
            # Baseline = what this request would have cost with the teacher alone, writing every choice the client got.
            # After a repair, the prompt counted is the request's own (`_asked`), not the repair conversation's.
            if teacher_c is not None and served_by == "teacher":  # the teacher's call (or the fallback teacher's) itself
                baseline = self._call_cost(self.s.teacher_model, _asked(served_choices, served_first))
            elif teacher_c is not None:  # an audit: one answer; the teacher alone would have written one per choice
                baseline = self._cost(self.s.teacher_model, _asked([teacher_c], audit_first)[0], len(served_choices))
            else:  # the student served (also degraded, or with an audit still pending): the formula above, to the bit
                assert baseline_est is not None
                baseline = baseline_est

            # Shadow (`_shadow`): the candidate runs the same cascade silently, compared with the teacher's answer when
            # this request has one (the escalation's, or a counted audit's), else with the served answer. A request a
            # stand-in answered (degraded, or the fallback teacher) runs no shadow: its only reference would not be the
            # primary teacher's (ADR 0019). Off the request path it rides with the request's audit, or takes a place of its
            # own; with no place left it does not run for this request (counted per version), and nothing else changes.
            # The student turned off (ADR 0064) stops the shadow's samples too: the switch stops every student call.
            stopped = student_only or flags.student_disabled
            shadow = None if route in STAND_IN_ROUTES or stopped else self.ledger.by_state("shadow")
            shadow_vid = shadow_serve = shadow_agree = None
            if shadow is not None and offpath:
                if slot is None:
                    slot = self.sidecar.reserve(size=job_bytes(messages))
                    if slot is None:
                        self._sidecar_full("shadow", shadow.id)
                if slot is not None:
                    pending_shadow, shadow_vid = shadow, shadow.id  # shadow_serve NULL: pending (`_finish_offpath`)
                shadow = None
            if shadow is not None:
                reference = teacher_c if served_by == "teacher" or audited else None
                so = self._shadow(task, messages, plan, meta, client, t0, shadow, served, reference, teacher_failed)
                if so.samples is not None:
                    s_samples, arbitration_c = so.samples, so.arbitration
                    cost += so.sample_cost
                    student_cost += so.sample_cost
                    student_in += s_samples[0].in_tokens
                    student_out += sum(s.out_tokens for s in s_samples)
                    if arbitration_c is not None:
                        stored = so.stored
                        cost += so.arbitration_cost  # a repair's second call included (FR-1.40)
                        teacher_cost += so.arbitration_cost
                        if so.rejected is not None:
                            repaired.append(so.rejected)
                    shadow_vid, shadow_serve, shadow_agree = so.version, so.serve, so.agree

            # Settled at the start, or by a store check that found an opt-out made while the request ran
            # (`_store_example`).
            opt_out = meta.store_opt_out if not meta.storable else None
            wall_ms = (self.timer() - t0) * 1000
            request_id = self.ledger.log_request(
                task=task.name,
                served_by=served_by,
                student_version=version.id if version else None,
                confidence=confidence,
                escalated=int(escalated),
                audited=1 if pending_audit is not None else audited,  # a pending audit: audit_agree NULL until it ends
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
                student_agree=student_agree,  # a pending audit's: written when its job ends (`_finish_offpath`)
                baseline_est_usd=baseline_est,
                end_user=meta.end_user,
                store_opt_out=opt_out,
                request_id=meta.request_id,
                end_user_secondary=meta.end_user_secondary,
                wall_ms=wall_ms,
                client_request_id=meta.client_request_id,
                retry_count=meta.retry_count,
                uses_tools=0,  # a tool request never reaches the cascade (FR-1.41)
            )
        except BaseException:
            if slot is not None:
                slot.cancel()
            raise
        if slot is not None:
            op = OffPath(
                request_id,
                task,
                messages,
                plan,
                meta,
                client,
                t0,
                served,
                len(served_choices),
                student_c if pending_audit is not None else None,
                teacher_c if served_by == "teacher" or audited else None,  # the shadow's reference, as inline
                baseline,
                pending_audit,
                pending_shadow,
                teacher_failed,
            )
            if not slot.submit(functools.partial(self._finish_offpath, op), op.tag):
                self._finish_offpath(op)  # the sidecar stopped meanwhile: the request still runs its own checks
        # An off-path audit's baseline is counted when its job prices it (`_finish_offpath`).
        counted_baseline = 0.0 if pending_audit is not None else baseline
        self._count_request(task, served_by, route, wall_ms, cost, counted_baseline, client, escalated, meta)
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
            **_usage(messages, _asked(served_choices, served_first), choices),
            fingerprint=fp,
            usage_breakdown=_breakdown(served_choices, samples, audit_c, s_samples, arbitration_c, repaired),
            degraded=degraded,
            choices=choices,
            student_cost_usd=student_cost,
            teacher_cost_usd=teacher_cost,
            request_id=request_id,
            stored=stored,
            store_opt_out=opt_out,
            audited=bool(audited),
            wall_ms=wall_ms,
            overhead_pending=slot is not None,
            task=task.name,
            schema_valid=_schema_valid(task, choices),
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
