"""The cascade: student first, teacher when the student is unsure, every teacher answer becomes training data."""

from __future__ import annotations

import random
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from . import licenses, tasks
from .confidence import STUDENT_SAMPLING, score
from .config import Settings
from .ledger import Ledger, Version
from .providers import Completion, Provider, UpstreamError, approx_tokens
from .redact import redact, redact_messages

# Given a version, return (provider, model name to send). For vLLM + LoRA the model name is the adapter name.
StudentResolver = Callable[[Version], tuple[Provider, str]]

# A failing upstream call is logged as an event on these occurrences only, so an outage cannot flood the release log.
EVENT_ON_FAILURE = (1, 10, 100, 1000)


def _failure(e: BaseException) -> str:
    """What an event may say about a failed call: the exception's class and, for an HTTP error, the status (and an
    upstream error's code, such as `upstream_timeout`). Never the message: an httpx message carries the endpoint URL,
    and other messages can carry request text."""
    if isinstance(e, UpstreamError):
        return f"{type(e).__name__} {e.status} {e.code}" if e.code else f"{type(e).__name__} {e.status}"
    if isinstance(e, httpx.HTTPStatusError):
        return f"{type(e).__name__} {e.response.status_code}"
    return type(e).__name__


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


class Ratchet:
    def __init__(
        self, settings: Settings, ledger: Ledger, teacher: Provider, student_resolver: StudentResolver, seed: int | None = None
    ):
        self.s = settings
        self.ledger = ledger
        self.teacher = teacher
        self.resolve_student = student_resolver
        self.rng = random.Random(seed)
        self.teacher_license = licenses.require_distillable(settings.teacher_model)
        # Failed upstream calls since start (health()). Scout calls handle from several threads, hence the lock.
        self.student_errors = 0  # student calls that raised; the teacher answered instead
        self.audit_errors = 0  # teacher audits that raised; the student's answer was served anyway (INV-15)
        self.shadow_errors = 0  # shadow samples or arbitrations that raised; the request never noticed
        self._errors_lock = threading.Lock()

    def health(self) -> dict[str, int]:
        """Failed upstream calls since start, by kind (for /readyz and /metrics)."""
        with self._errors_lock:
            return {"student_errors": self.student_errors, "audit_errors": self.audit_errors, "shadow_errors": self.shadow_errors}

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

    def _call_teacher(self, task: tasks.Task, messages: list[dict[str, Any]]) -> Completion:
        c = self.teacher.complete(messages, self.s.teacher_model)[0]
        clean = tasks.strip_reasoning(c.text)
        stored, found = messages, Counter()
        if self.s.redact_pii:  # the teacher saw the original; the training set gets placeholders
            stored, clean = redact_messages(messages, found), redact(clean, found)
        self.ledger.add_example(
            task.name,
            stored,
            clean,
            self.s.teacher_model,
            self.teacher_license.name,
            self.teacher_license.url,
            redactions=sum(found.values()),
        )
        return c

    def _arbitrate(self, task: tasks.Task, messages: list[dict[str, Any]], shadow: Version) -> Completion | None:
        """The teacher settles a disagreement between the shadow and the served answer. A failed call returns None and
        leaves the disagreement unarbitrated; the request it belongs to is already answered (INV-15)."""
        try:
            return self._call_teacher(task, messages)
        except Exception as e:  # optional call: count it, never fail the request
            self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} arbitration: {_failure(e)}")
            return None

    def _cost(self, model: str, c: Completion, n_out: int = 1) -> float:
        return self.s.price(model).cost(c.in_tokens, c.out_tokens * n_out)

    def threshold(self, version: Version, model: str | None = None) -> float:
        """Each version carries its own threshold, calibrated on the golden set at training time.
        The hosted untuned fallback model was never calibrated, so it uses the global default."""
        if model is not None and model == self.s.fallback_student_model:
            return self.s.confidence_threshold
        return float(version.eval.get("threshold", self.s.confidence_threshold))

    def _sample(self, version: Version, messages: list[dict[str, Any]]) -> tuple[list[Completion], float, str]:
        provider, model = self.resolve_student(version)
        samples = provider.complete(messages, model, n=self.s.samples, **STUDENT_SAMPLING)
        if not samples:
            raise RuntimeError("student returned no choices")
        cost = self.s.price("student").cost(samples[0].in_tokens, sum(s.out_tokens for s in samples))
        return samples, cost, model

    def _pinned(self, task: tasks.Task, messages: list[dict[str, Any]], client: str | None = None) -> Result:
        """Pinned tasks always go to the teacher. They are logged but never stored as training
        examples, so they cannot leak into a student that was never meant to do them."""
        c = self.teacher.complete(messages, self.s.teacher_model)[0]
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
        )
        return Result(tasks.strip_reasoning(c.text), "teacher", None, None, False, cost, cost, c.latency_ms, c.model)

    def teacher_only(self, messages: list[dict[str, Any]], task_name: str | None = None, client: str | None = None) -> Result:
        """A request the student cannot take, such as one with an image part the teacher reads
        (RATCHET_TEACHER_MODALITIES): the pinned path, so the teacher answers, the request is logged, and nothing is
        stored as a training example. No student, audit or shadow call."""
        return self._pinned(tasks.get(task_name), messages, client)

    # ------------------------------------------------------------------
    def handle(self, messages: list[dict[str, Any]], task_name: str | None = None, client: str | None = None) -> Result:
        """`client` is the API key name that sent the request (None for internal calls and open gateways)."""
        task = tasks.get(task_name)
        if task.pinned:
            return self._pinned(task, messages, client)
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

        sc = None
        if version is not None:
            try:
                samples, c, model_used = self._sample(version, messages)
            except Exception as e:  # student endpoint down or erroring: the teacher still answers
                self._count_failure("student_errors", "student_error", f"v{version.id}: {type(e).__name__}: {e}"[:300])
                escalated = True
            else:
                latency += max(s.latency_ms for s in samples)
                cost += c
                sc = score(task, samples)
                confidence = sc.confidence
                student_c = sc.answer
        if version is not None and sc is not None:
            if sc.confidence >= self.threshold(version, model_used):
                served, served_by = sc.answer, "student"
                if self.rng.random() < self.s.audit_rate:  # audit = quality signal + free training data
                    try:
                        teacher_c = self._call_teacher(task, messages)
                    except Exception as e:  # optional call: the student's answer is served (INV-15)
                        teacher_failed = True
                        self._count_failure("audit_errors", "audit_error", f"{task.name}: {_failure(e)}")
                    else:
                        cost += self._cost(self.s.teacher_model, teacher_c)
                        audited, audit_agree = 1, int(task.agree(sc.answer.text, teacher_c.text))
            else:
                escalated = True
        if served_by == "teacher":
            teacher_c = self._call_teacher(task, messages)
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
        # leaves no shadow record; a failed arbitration leaves shadow_agree NULL, which the release gate
        # counts as a disagreement (Ledger.shadow_quality).
        shadow = self.ledger.by_state("shadow")
        shadow_vid = shadow_serve = shadow_agree = None
        if shadow is not None:
            try:
                s_samples, c, s_model = self._sample(shadow, messages)
            except Exception as e:  # optional call: count it, never fail the request
                s_samples = None
                self._count_failure("shadow_errors", "shadow_error", f"v{shadow.id} sampling: {_failure(e)}")
        if shadow is not None and s_samples is not None:
            cost += c
            s_sc = score(task, s_samples)
            shadow_vid = shadow.id
            shadow_serve = int(s_sc.confidence >= self.threshold(shadow, s_model))
            reference: Completion | None = teacher_c
            arbitrated = True
            if reference is None and shadow_serve and not task.agree(s_sc.answer.text, served.text):
                reference = None if teacher_failed else self._arbitrate(task, messages, shadow)
                if reference is None:
                    arbitrated = False
                else:
                    cost += self._cost(self.s.teacher_model, reference)
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
        )
        return Result(
            text=tasks.strip_reasoning(served.text),
            served_by=served_by,
            version=version.id if (version and served_by == "student") else None,
            confidence=confidence,
            escalated=escalated,
            cost_usd=cost,
            baseline_usd=baseline,
            latency_ms=latency,
            model=served.model,
        )
