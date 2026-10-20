"""Golden-set evaluation and per-version threshold calibration.

For each held-out golden example the student is sampled exactly as in production
(n samples with `confidence.STUDENT_SAMPLING`, under the conditions stored with the example and the
same `GenPolicy` as the router, + confidence score). From the (confidence,
correct) pairs we pick the lowest threshold whose served answers reach the target
precision, then report:
  precision = agreement with the teacher among answers it would serve
  coverage  = share of golden requests it would serve (= expected savings)
Structured fields are compared exactly against the teacher's answer, so there is no judge
bias on labels. Free-text tasks can add a reference-based judge score.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .. import tasks
from ..confidence import STUDENT_SAMPLING, calibrate_threshold, score
from ..providers import Completion, GenParams, GenPolicy, Provider

# sample(prompt, n, params): n student samples for a golden row's prompt, under the conditions stored with it (the row's
# `params`, a dict of ledger.STORED_PARAM_KEYS, or None).
SampleFn = Callable[[list[dict[str, Any]], int, "dict[str, Any] | None"], list[Completion]]

JUDGE_PROMPT = (
    "You compare a CANDIDATE answer with a REFERENCE answer for the same request. "
    "Score 1 if the candidate is as correct and useful as the reference, else 0. "
    'Reply with JSON only: {"score": 0 or 1, "reason": "<short>"}'
)


def cascade_eval(
    sample: SampleFn, golden_rows: list[dict[str, Any]], samples: int = 3, target_precision: float = 0.97, floor: float = 0.5
) -> dict[str, Any]:
    """Calibrate the threshold on one half of the golden set and measure on the other half,
    so the reported precision is not graded on the data the threshold was tuned on."""
    calib: list[tuple[float, bool]] = []
    test: list[tuple[float, bool]] = []
    for i, row in enumerate(golden_rows):
        task = tasks.get(row["task"])
        prompt, ref = row["messages"][:-1], row["messages"][-1]["content"]
        sc = score(task, sample(prompt, samples, row.get("params")))
        (calib if i % 2 == 0 else test).append((sc.confidence, task.agree(sc.answer.text, ref)))
    thr = min(1.01, max(floor, calibrate_threshold(calib, target_precision)))  # 1.01 = serve nothing
    served = [ok for c, ok in test if c >= thr]
    allpts = calib + test
    return {
        "golden_n": len(allpts),
        "golden_agreement": sum(ok for _, ok in allpts) / len(allpts) if allpts else 0.0,
        "threshold": round(thr, 4),
        "coverage": len(served) / len(test) if test else 0.0,
        "precision": sum(served) / len(served) if served else 0.0,
        "test_n": len(test),
    }


class LiveEvaluator:
    """Evaluates an adapter served by vLLM (adapter name = model name). `policy` is the gateway's `GenPolicy`
    (`Settings.gen_policy()`), so each golden row's student call gets what the router would send for it."""

    def __init__(
        self,
        student: Provider,
        judge: Provider | None = None,
        judge_model: str = "",
        samples: int = 3,
        target_precision: float = 0.97,
        policy: GenPolicy | None = None,
    ):
        self.student = student
        self.judge = judge
        self.judge_model = judge_model
        self.samples = samples
        self.target_precision = target_precision
        self.policy = policy or GenPolicy()

    def judge_score(self, request: list[dict[str, Any]], reference: str, candidate: str) -> int:
        assert self.judge is not None
        msgs = [
            {"role": "system", "content": JUDGE_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"request": request, "reference": reference, "candidate": candidate}, ensure_ascii=False),
            },
        ]
        out = self.judge.complete(msgs, self.judge_model, response_format={"type": "json_object"})[0]
        obj = tasks.parse_json(out.text) or {}
        return int(obj.get("score", 0) == 1)

    def evaluate(self, adapter_uri: str, golden_rows: list[dict[str, Any]]) -> dict[str, Any]:
        def sample(prompt, n, params=None):
            gen = self.policy.student(GenParams.from_stored(params))
            return self.student.complete(prompt, adapter_uri, n=n, params=gen, **STUDENT_SAMPLING)

        res = cascade_eval(sample, golden_rows, self.samples, self.target_precision)
        free_text = [r for r in golden_rows if not tasks.get(r["task"]).compare_keys]
        if self.judge and free_text:
            ok = 0
            for row in free_text:
                prompt, ref = row["messages"][:-1], row["messages"][-1]["content"]
                gen = self.policy.student(GenParams.from_stored(row.get("params")))
                cand = self.student.complete(prompt, adapter_uri, n=1, temperature=0.0, params=gen)[0].text
                ok += self.judge_score(prompt, ref, cand)
            res["judge_pass_rate"] = ok / len(free_text)
        return res
