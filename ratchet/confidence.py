"""Confidence scoring for student answers.

Small models are poorly calibrated, so Ratchet never trusts a single signal. It combines:
  1. token probability: exp(mean token logprob) of the best sample, from vLLM
  2. self-consistency: how many of n samples agree with the majority answer
  3. validity: an answer that fails the task's format scores 0
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

from .providers import Completion
from .tasks import Task

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


def score(task: Task, samples: list[Completion], w_prob: float = 0.4, w_agree: float = 0.6) -> Scored:
    if not samples:
        raise ValueError("no samples")
    canon = [task.canonical(s.text) for s in samples]
    counts = Counter(c for c in canon if c != "<invalid>")
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
