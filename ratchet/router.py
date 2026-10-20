"""The cascade: student first, teacher when the student is unsure, every teacher answer becomes training data."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import licenses, tasks
from .confidence import STUDENT_SAMPLING, score
from .config import Settings
from .ledger import Ledger, Version
from .providers import Completion, Provider, approx_tokens
from .redact import redact, redact_messages

# Given a version, return (provider, model name to send). For vLLM + LoRA the model name is the adapter name.
StudentResolver = Callable[[Version], tuple[Provider, str]]


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
        self.student_errors = 0

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

        sc = None
        if version is not None:
            try:
                samples, c, model_used = self._sample(version, messages)
            except Exception as e:  # student endpoint down or erroring: the teacher still answers
                self.student_errors += 1
                if self.student_errors in (1, 10, 100, 1000):
                    self.ledger.event("student_error", f"v{version.id}: {type(e).__name__}: {e}"[:300])
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
                if self.rng.random() < self.s.audit_rate:
                    teacher_c = self._call_teacher(task, messages)  # audit = quality signal + free training data
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
        shadow = self.ledger.by_state("shadow")
        shadow_vid = shadow_serve = shadow_agree = None
        if shadow is not None:
            try:
                s_samples, c, s_model = self._sample(shadow, messages)
            except Exception:
                s_samples = None
        if shadow is not None and s_samples is not None:
            cost += c
            s_sc = score(task, s_samples)
            shadow_vid = shadow.id
            shadow_serve = int(s_sc.confidence >= self.threshold(shadow, s_model))
            reference = teacher_c
            if reference is None and shadow_serve and not task.agree(s_sc.answer.text, served.text):
                reference = self._call_teacher(task, messages)
                cost += self._cost(self.s.teacher_model, reference)
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
