"""Confidence scoring for student answers.

Small models are poorly calibrated, so Ratchet never trusts a single signal. It combines:
  1. token probability: exp(mean token logprob) of the best sample, from vLLM
  2. self-consistency: how many of n samples agree with the majority answer
  3. validity: an answer that fails the task's format, is empty, or did not end by itself (a `finish_reason` other
     than `stop` or `tool_calls`, the rule a teacher answer must pass to be stored) gets no vote, and when no sample is
     valid the score is 0 and nothing is served
The threshold that turns the score into "serve" vs "escalate" is set on a labelled
dev set (see `calibrate_threshold`).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .providers import COMPLETE, Completion
from .tasks import INVALID, Task

# How the student is sampled for a confidence score: in production (`Ratchet._sample`), in the live golden eval
# (`LiveEvaluator.evaluate`) and in the mock one (`MockFleet.evaluate`). One constant, so the eval cannot drift from
# production (FR-5.1). `n` is added by each caller (`Settings.samples`). Read-only.
STUDENT_SAMPLING: Mapping[str, Any] = MappingProxyType({"temperature": 0.7, "logprobs": True})


@dataclass
class Scored:
    answer: Completion
    confidence: float
    agreement: float
    token_prob: float | None

    @property
    def valid(self) -> bool:
        """At least one sample was a valid answer. When none was, `answer` is only a placeholder and must never be
        served, whatever the threshold (even 0)."""
        return self.agreement > 0


def _key(task: Task, sample: Completion) -> str:
    """A sample's canonical answer; `INVALID` when it did not end by itself (`length`, `content_filter`, vLLM's `abort`,
    ...), because a cut-off answer is never a vote. The same `COMPLETE` rule as for teacher answers (`answer_problem`)."""
    return task.canonical(sample.text) if sample.finish_reason in COMPLETE else INVALID


def score(task: Task, samples: list[Completion], w_prob: float = 0.4, w_agree: float = 0.6) -> Scored:
    if not samples:
        raise ValueError("no samples")
    canon = [_key(task, s) for s in samples]
    counts = Counter(c for c in canon if c != INVALID)
    if not counts:
        return Scored(samples[0], 0.0, 0.0, samples[0].mean_prob)
    majority, votes = counts.most_common(1)[0]
    agreement = votes / len(samples)
    # Best sample = the majority answer with the highest token probability.
    best = max(
        (s for s, c in zip(samples, canon, strict=True) if c == majority),
        key=lambda s: s.mean_prob if s.mean_prob is not None else 0.0,
    )
    p = best.mean_prob
    conf = agreement if p is None else w_prob * p + w_agree * agreement
    return Scored(best, conf, agreement, p)


def calibrate_threshold(points: list[tuple[float, bool]], target_precision: float = 0.95) -> float:
    """Lowest threshold whose served answers are correct at least `target_precision` of the time.

    `points` = (confidence, student_was_correct) pairs from a labelled dev set.
    """
    if not points:
        return 1.0
    pts = sorted(points, key=lambda x: -x[0])
    best = math.inf  # serve nothing unless some threshold reaches the target
    correct = served = 0
    i = 0
    while i < len(pts):
        c = pts[i][0]
        # A threshold serves every point with confidence >= c, so include all ties before checking.
        while i < len(pts) and pts[i][0] == c:
            correct += pts[i][1]
            served += 1
            i += 1
        if correct / served >= target_precision:
            best = c
    return best
