"""Confidence scoring for student answers.

Small models are poorly calibrated, so Ratchet never trusts a single signal. It combines:
  1. token probability: exp(mean token logprob) of the best sample, from vLLM
  2. self-consistency: how many of n samples agree with the majority answer, compared pairwise with the task's field
     comparators (`Task.match`), so a tolerance can make 0, 0.6 and 1.2 one group around 0.6
  3. validity: an answer that is not valid under the task (`Task.key`: not parseable, a required field missing, a label
     outside its vocabulary), is empty, or did not end by itself (a `finish_reason` other than `stop` or `tool_calls`,
     the rule a teacher answer must pass to be stored) gets no vote, and when no sample is valid the score is 0 and
     nothing is served
The threshold that turns the score into "serve" vs "escalate" is set on a labelled
dev set (see `calibrate_threshold`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .compare import INVALID
from .providers import COMPLETE, Completion
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

    @property
    def valid(self) -> bool:
        """At least one sample was a valid answer. When none was, `answer` is only a placeholder and must never be
        served, whatever the threshold (even 0)."""
        return self.agreement > 0


def _key(task: Task, sample: Completion) -> Any:
    """A sample's key (`Task.key`); `INVALID` when it did not end by itself (`length`, `content_filter`, vLLM's `abort`,
    ...), because a cut-off answer is never a vote. The same `COMPLETE` rule as for teacher answers (`answer_problem`)."""
    return task.key(sample.text) if sample.finish_reason in COMPLETE else INVALID


def _prob(s: Completion) -> float:
    return s.mean_prob if s.mean_prob is not None else 0.0


def score(task: Task, samples: list[Completion], w_prob: float = 0.4, w_agree: float = 0.6) -> Scored:
    """Each valid sample's votes are the valid samples it matches, itself included (`Task.match`, keys computed once per
    sample, at most n^2 matches and no I/O). The majority is the first sample with the most votes, its members the
    samples it matches, and agreement its votes over all n samples. The answer is the member with the highest token
    probability among the members that have as many votes as the majority (the first on ties). For comparators that are
    transitive (every built-in task's) the members are one group of equal answers, so this is exactly a count of equal
    canonical answers with `Counter.most_common`'s tie-break, as before field comparators. With a tolerance (0, 0.6 and
    1.2 under abs_tol 1) the answer is the sample that agrees with every member (0.6), never one at the edge."""
    if not samples:
        raise ValueError("no samples")
    keys = [_key(task, s) for s in samples]
    valid = [i for i, k in enumerate(keys) if k is not INVALID]
    if not valid:
        return Scored(samples[0], 0.0, 0.0, samples[0].mean_prob)
    votes = {i: sum(1 for j in valid if task.match(keys[i], keys[j])) for i in valid}
    top = max(votes.values())
    m = next(i for i in valid if votes[i] == top)
    members = [j for j in valid if task.match(keys[m], keys[j])]
    agreement = top / len(samples)
    best = max((samples[j] for j in members if votes[j] == top), key=_prob)  # max keeps the first on ties
    p = best.mean_prob
    conf = agreement if p is None else w_prob * p + w_agree * agreement
    return Scored(best, conf, agreement, p)


def majority(task: Task, scored: Scored, samples: list[Completion], n: int) -> list[Completion] | None:
    """The `n` samples a client gets as its choices when the student serves a request for `n` choices (FR-1.14), or
    None when fewer than `n` of `samples` share the scored majority answer (or no sample was valid). `scored` is the
    score of a prefix of `samples` (`Settings.samples` of them, as calibrated), so choice 0 is `scored.answer`, the
    sample a one-choice request serves; the others are the samples that agree with it (`Task.match`), by token
    probability, highest first (in sample order on ties). Every choice is a valid answer that ended by itself."""
    if not scored.valid:
        return None
    if n == 1:
        return [scored.answer]
    best = next(i for i, s in enumerate(samples) if s is scored.answer)
    key = _key(task, scored.answer)
    rest = [s for i, s in enumerate(samples) if i != best and task.match(key, _key(task, s))]
    if len(rest) + 1 < n:
        return None
    rest.sort(key=lambda s: -_prob(s))
    return [scored.answer, *rest[: n - 1]]


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
