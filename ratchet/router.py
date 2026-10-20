"""The cascade: student first, teacher when the student is unsure, every teacher answer becomes training data."""

from __future__ import annotations

import math
import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from . import licenses, tasks
from .confidence import STUDENT_SAMPLING, score
from .config import Settings
from .ledger import STORED_PARAM_KEYS, Ledger, Version, target_params
from .providers import COMPLETE, Completion, GenParams, GenPlan, Provider, UpstreamError, approx_tokens
from .redact import redact, redact_json, redact_messages
from .resilience import BAD_RESPONSE, AdapterMissing, Breaker, CircuitOpen, describe_failure, endpoint_down, names_model

# Given a version, return (provider, model name to send). For vLLM + LoRA the model name is the adapter name.
StudentResolver = Callable[[Version], tuple[Provider, str]]

# A failing upstream call is logged as an event on these occurrences only, so an outage cannot flood the release log.
EVENT_ON_FAILURE = (1, 10, 100, 1000)

# How a request was served (`requests.route`): the student; the teacher because the student was unsure (`confidence`),
# because no version serves yet (`cold_start`), because the task is pinned (`pinned`), because the student call raised
# (`student_error`), or because the student's breaker was open (`student_breaker`).
ROUTES = ("student", "confidence", "cold_start", "pinned", "student_error", "student_breaker")

# finish_reason values a `skip_example` event names as they are; any other is `other`, so the reasons stay a short list.
_FINISH_NAMED = frozenset({"length", "content_filter", "function_call", "abort", "error", "unknown"})
# The finish_reason values a client gets (`Result.finish_reason`); any other upstream value is reported as `stop`.
CLIENT_FINISH = ("stop", "length", "tool_calls", "content_filter")


def answer_problem(task: tasks.Task, c: Completion) -> str | None:
    """Why a teacher answer can be neither a training example nor a reference for the student (INV-16), or None when it
    can: it did not end by itself (`finish_reason length`, ...), nothing is left once reasoning is stripped (`empty
    answer`), or a structured task's answer is not valid JSON (`invalid answer`). String operations only."""
    if c.finish_reason not in COMPLETE:
        return f"finish_reason {c.finish_reason if c.finish_reason in _FINISH_NAMED else 'other'}"
    clean = tasks.clean_answer(task, c.text)
    if not clean:
        return "empty answer"
    if task.structured and task.canonical(clean) == tasks.INVALID:
        return "invalid answer"
    return None


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


@dataclass
class Result:
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


def _client_finish(c: Completion) -> str:
    return c.finish_reason if c.finish_reason in CLIENT_FINISH else "stop"


def _task(task: str | tasks.Task | None) -> tasks.Task:
    return task if isinstance(task, tasks.Task) else tasks.get(task)


class Ratchet:
    """The cascade. `clock` drives the student breakers (tests pass a fake one). `on_adapter_missing(version)` is
    called when vLLM answers 404 for a version's adapter; live mode wires `AdapterReloader.request` to it."""

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
    ):
        self.s = settings
        self.ledger = ledger
        self.teacher = teacher
        self.resolve_student = student_resolver
        self.rng = random.Random(seed)
        self.teacher_license = licenses.require_distillable(settings.teacher_model)
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
        self._errors_lock = threading.Lock()
        self._skipped: Counter[str] = Counter()  # teacher answers not stored, by reason (throttles `skip_example`)
        # One breaker per (role, endpoint), made on first use: live and canary on one endpoint share 'student'; shadow
        # calls have their own ('shadow'), so a bad staging adapter cannot open the breaker live traffic depends on.
        self._breakers: dict[tuple[str, str], Breaker] = {}
        self._breakers_lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        """Failed upstream calls since start, by kind, and each student breaker's `snapshot()` by name (for /readyz and
        /metrics)."""
        with self._errors_lock:
            out: dict[str, Any] = {
                "student_errors": self.student_errors,
                "audit_errors": self.audit_errors,
                "shadow_errors": self.shadow_errors,
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

    def plan(self, params: GenParams | None) -> GenPlan:
        """The parameters of each model call for a request with these client parameters (`GenPolicy.plan`)."""
        return self._plain if params is None else self.gen.plan(params)

    def _ask_teacher(
        self, task: tasks.Task, messages: list[dict[str, Any]], gen: GenParams, visible_max: int | None
    ) -> Completion:
        """One teacher answer under `gen`. When the reasoning headroom let the teacher write more than the client's
        `max_tokens` (`visible_max`), an answer whose visible part (`tasks.clean_answer`) is longer than that, by
        `approx_tokens`, is marked `length`: over the client's budget, so it is not stored (INV-16) and the client is told.
        The text is not cut, because the count is an estimate."""
        c = self.teacher.complete(
            messages, self.s.teacher_model, temperature=gen.temperature or 0.0, logprobs=bool(gen.logprobs), params=gen
        )[0]
        if visible_max is not None and c.finish_reason in COMPLETE:
            if approx_tokens(tasks.clean_answer(task, c.text)) > visible_max:
                c = replace(c, finish_reason="length")
        return c

    def _call_teacher(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        plan: GenPlan | None = None,
        role: str = "serve",
        meta: RequestMeta | None = None,
    ) -> tuple[Completion, bool]:
        """The teacher's answer, under the plan's `serve` parameters or, to audit or arbitrate, its `check` ones, and
        whether it was stored as a training example (`_store_example`)."""
        plan = plan or self._plain
        gen = plan.serve if role == "serve" else plan.check
        c = self._ask_teacher(task, messages, gen, plan.visible_max)
        return c, self._store_example(task, messages, c, gen, meta)

    def _store_example(
        self,
        task: tasks.Task,
        messages: list[dict[str, Any]],
        c: Completion,
        gen: GenParams | None = None,
        meta: RequestMeta | None = None,
    ) -> bool:
        """Store a teacher answer as a training example, the only place that does (INV-2). Only a complete, valid answer
        is stored (INV-16, `answer_problem`): cleaned the way it is served (`tasks.clean_answer`), then, with
        `Settings.redact_pii`, with identifiers in the messages, the answer and the stored conditions replaced (INV-12).
        The conditions are the `STORED_PARAM_KEYS` of `gen`, the parameters the teacher answered under. Returns True when
        an example was inserted or a duplicate's `seen` bumped; False, with a throttled `skip_example` event, when not.
        A request whose `meta.storable` is False stores nothing and is not a skip."""
        if meta is not None and not meta.storable:
            return False
        problem = answer_problem(task, c)
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
            # A placeholder can break a structured answer (a card-like number outside quotes): never store that.
            if found and task.structured and task.canonical(clean) == tasks.INVALID:
                self._skip_example(task, "invalid after redaction")
                return False
        self.ledger.add_example(
            task.name,
            stored,
            clean,
            self.s.teacher_model,
            self.teacher_license.name,
            self.teacher_license.url,
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
        already answered (INV-15)."""
        try:
            return self._call_teacher(task, messages, plan, "arbitrate", meta)
        except Exception as e:  # optional call: count it, never fail the request
            self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} arbitration: {describe_failure(e)}")
            return None

    def _cost(self, model: str, c: Completion, n_out: int = 1) -> float:
        return self.s.price(model).cost(c.in_tokens, c.out_tokens * n_out)

    def threshold(self, version: Version, model: str | None = None) -> float:
        """Each version carries its own threshold, calibrated on the golden set at training time.
        The hosted untuned fallback model was never calibrated, so it uses the global default."""
        if model is not None and model == self.s.fallback_student_model:
            return self.s.confidence_threshold
        return float(version.eval.get("threshold", self.s.confidence_threshold))

    def _sample(
        self, version: Version, messages: list[dict[str, Any]], role: str = "student", gen: GenParams | None = None
    ) -> tuple[list[Completion], float, str]:
        """The student's samples under `gen` (the plan's `student` parameters), through the `role` breaker of its
        endpoint. Raises `CircuitOpen` without calling when that breaker is open, and `AdapterMissing` when vLLM answers
        404 naming the version's adapter (an endpoint restart forgets runtime-loaded adapters; that 404 is the endpoint
        answering, so it does not count against it)."""
        provider, model = self.resolve_student(version)
        breaker = self._breaker(role, provider)
        if not breaker.allow():
            raise CircuitOpen(breaker.name)
        try:
            samples = provider.complete(
                messages, model, n=self.s.samples, params=self._plain.student if gen is None else gen, **STUDENT_SAMPLING
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

    def _pinned(
        self, task: tasks.Task, messages: list[dict[str, Any]], client: str | None = None, plan: GenPlan | None = None
    ) -> Result:
        """Pinned tasks always go to the teacher. They are logged but never stored as training
        examples, so they cannot leak into a student that was never meant to do them."""
        plan = plan or self._plain
        c = self._ask_teacher(task, messages, plan.serve, plan.visible_max)
        cost = self._cost(self.s.teacher_model, c)
        self.ledger.log_request(
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
            baseline_cost_usd=cost,
            client=client,
            route="pinned",
            stored=0,
        )
        text = tasks.clean_answer(task, c.text)
        return Result(text, "teacher", None, None, False, cost, cost, c.latency_ms, c.model, "pinned", _client_finish(c))

    def teacher_only(
        self,
        messages: list[dict[str, Any]],
        task: str | tasks.Task | None = None,
        client: str | None = None,
        params: GenParams | None = None,
        meta: RequestMeta | None = None,
    ) -> Result:
        """A request the student cannot take, such as one with an image part the teacher reads
        (RATCHET_TEACHER_MODALITIES): the pinned path, so the teacher answers, the request is logged, and nothing is
        stored as a training example. No student, audit or shadow call."""
        return self._pinned(_task(task), messages, client, self.plan(params))

    # ------------------------------------------------------------------
    def handle(
        self,
        messages: list[dict[str, Any]],
        task: str | tasks.Task | None = None,
        client: str | None = None,
        params: GenParams | None = None,
        meta: RequestMeta | None = None,
    ) -> Result:
        """Answer one request. `task` is a task name (or a `tasks.Task`); `client` is the API key name that sent it (None
        for internal calls and open gateways); `params` are the client's generation parameters, which each call gets as
        its role allows (`GenPolicy`, ADR 0017); `meta` is what else the caller knows (`RequestMeta`). Scout and the
        simulator pass only messages and a task name."""
        task = _task(task)
        plan = self.plan(params)
        if task.pinned:
            return self._pinned(task, messages, client, plan)
        version = self._pick_version()
        cost = 0.0
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

        sc = None
        if version is not None:
            try:
                samples, c, model_used = self._sample(version, messages, gen=plan.student)
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
                sc = score(task, samples)
                confidence = sc.confidence
                student_c = sc.answer
        if version is not None and sc is not None:
            # A sample set with no valid answer is never served, whatever the threshold (confidence.Scored.valid).
            if sc.valid and sc.confidence >= self.threshold(version, model_used):
                served, served_by, route = sc.answer, "student", "student"
                if self.rng.random() < self.s.audit_rate:  # audit = quality signal + free training data
                    try:
                        teacher_c, stored = self._call_teacher(task, messages, plan, "audit", meta)
                    except Exception as e:  # optional call: the student's answer is served (INV-15)
                        teacher_failed = True
                        self._count_failure("audit_errors", "audit_error", f"{task.name}: {describe_failure(e)}")
                    else:
                        cost += self._cost(self.s.teacher_model, teacher_c)
                        # A cut-off, empty or invalid teacher answer measures nothing: not an audit (INV-16).
                        if answer_problem(task, teacher_c) is None:
                            audited, audit_agree = 1, int(task.agree(sc.answer.text, teacher_c.text))
            else:
                escalated, route = True, "confidence"
        if served_by == "teacher":
            teacher_c, stored = self._call_teacher(task, messages, plan, "serve", meta)
            cost += self._cost(self.s.teacher_model, teacher_c)
            latency += teacher_c.latency_ms
            served = teacher_c

        # Baseline = what this request would have cost with the teacher alone.
        if teacher_c is not None:
            baseline = self._cost(self.s.teacher_model, teacher_c)
        else:
            assert student_c is not None
            in_t = student_c.in_tokens or approx_tokens(str(messages))
            out_t = student_c.out_tokens + self.s.teacher_reasoning_tokens
            baseline = self.s.price(self.s.teacher_model).cost(in_t, out_t)

        # Shadow: the candidate runs the same cascade silently. We record whether it would have
        # served, and if so whether it agrees with the best reference we have. When it disagrees
        # with an answer the live student served, the teacher arbitrates (and that becomes training data).
        # Neither the shadow's samples nor the arbitration can fail the request (INV-15). A failed sample
        # leaves no shadow record; a failed arbitration, or an escalation or arbitration answer the teacher
        # cut off or got wrong (INV-16), leaves shadow_agree NULL, which the release gate counts as a
        # disagreement (Ledger.shadow_quality). An audit answer like that is no reference at all: the shadow
        # is compared with the served answer, as when no audit ran.
        shadow = self.ledger.by_state("shadow")
        shadow_vid = shadow_serve = shadow_agree = None
        if shadow is not None:
            try:
                s_samples, c, s_model = self._sample(shadow, messages, role="shadow", gen=plan.student)
            except CircuitOpen:  # the shadow's breaker is open: no shadow record, nothing counted again
                s_samples = None
            except Exception as e:  # optional call: count it, never fail the request
                s_samples = None
                if isinstance(e, AdapterMissing):
                    self._request_reload(shadow)
                self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} sampling: {describe_failure(e)}")
        if shadow is not None and s_samples is not None:
            cost += c
            s_sc = score(task, s_samples)
            shadow_vid = shadow.id
            shadow_serve = int(s_sc.valid and s_sc.confidence >= self.threshold(shadow, s_model))
            reference: Completion | None = teacher_c if served_by == "teacher" or audited else None
            arbitrated = True
            if reference is None and shadow_serve and not task.agree(s_sc.answer.text, served.text):
                ruling = None if teacher_failed else self._arbitrate(task, messages, shadow, plan, meta)
                if ruling is None:
                    arbitrated = False
                else:
                    reference, stored = ruling
                    cost += self._cost(self.s.teacher_model, reference)
            if reference is not None and answer_problem(task, reference) is not None:
                arbitrated = False  # a cut-off, empty or invalid teacher answer settles nothing (INV-16)
            if arbitrated:
                ref_text = reference.text if reference is not None else served.text
                shadow_agree = int(task.agree(s_sc.answer.text, ref_text))

        self.ledger.log_request(
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
            client=client,
            route=route,
            stored=int(stored),
        )
        return Result(
            text=tasks.clean_answer(task, served.text),
            served_by=served_by,
            version=version.id if (version and served_by == "student") else None,
            confidence=confidence,
            escalated=escalated,
            cost_usd=cost,
            baseline_usd=baseline,
            latency_ms=latency,
            model=served.model,
            route=route,
            finish_reason=_client_finish(served),
        )
