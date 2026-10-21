"""Golden-set evaluation and per-version threshold calibration.

For each held-out golden example the student is sampled exactly as in production
(n samples with `confidence.STUDENT_SAMPLING`, under the conditions stored with the example and the
same `GenPolicy` as the router, + confidence score). From the (confidence,
correct) pairs we pick the lowest threshold whose served answers reach the target
precision, then report:
  precision = agreement with the teacher among answers it would serve
  coverage  = share of golden requests it would serve (= expected savings)
Structured fields are compared with the task's field comparators against the teacher's answer
(`Task.agree`, FR-2.14), so there is no judge bias on labels. Free-text tasks can add a
reference-based judge score.

A golden row whose reference (the stored teacher answer) is not valid under the task's current
spec is skipped and counted (`golden_invalid_refs`): the spec may have tightened since the
answer was stored, and a reference the task refuses cannot grade the student. A row of a task
that is pinned now is skipped and counted too (`excluded_pinned`, INV-10): the student never
answers a pinned task, so its rows would calibrate a threshold for answers it never serves.

A row the student endpoint refuses (`REFUSED`: a stored condition it cannot serve, such as
`tools` on a vLLM without a tool parser or a schema its guided decoding cannot compile, or a
prompt too long for it) escalates in production (INV-7), so here it scores as a request the
student would not serve: confidence 0, wrong. It never stops the night.

Several tasks can share one adapter (FR-14.1, ADR 0040), so each task also gets its own record
(`eval['tasks'][name]`, read through `ratchet.calibration`): its threshold, calibrated to its
spec's `policy.target_precision` on its own even rows and measured on its own odd rows, with the
same global parity, so a single-task eval gives the global numbers again.

A task's routing block (FR-2.50, ADR 0049) adds three things. A row the router would keep from the
student (`routing.Routing.ineligible`: an over-length prompt stored with `store_ineligible`) is
skipped and counted (`excluded_ineligible`), as a pinned task's row is. The eligibility check reads
the stored prompt, which was redacted (INV-12): a placeholder such as [EMAIL] is shorter than what it
replaced, so a stored prompt just over the limit in production can come back just under it here.
A task with `escalate_when` rules is calibrated and measured on the answers the rules let the
student serve: an answer a rule sends to the teacher is never served, so it is neither in the
threshold's calibration nor in the precision and coverage the gate checks (the pooled numbers
too), though it still counts in `test_n` and coverage's denominator. And a task with critical
classes gets `critical` in its record: per class, on its held-out rows, how often the answer served
at the task's stored threshold misses a class the teacher's reference is in (`routing.Routing.misses`),
which the release gate reads.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, NamedTuple

from .. import calibration, tasks
from ..compare import INVALID
from ..confidence import SERVE_NOTHING, STUDENT_SAMPLING, calibrate_threshold, score
from ..providers import Completion, GenParams, GenPolicy, Provider, UpstreamError
from ..routing import CriticalRow

# sample(prompt, n, params): n student samples for a golden row's prompt, under the conditions stored with it (the row's
# `params`, a dict of ledger.STORED_PARAM_KEYS, or None).
SampleFn = Callable[[list[dict[str, Any]], int, "dict[str, Any] | None"], list[Completion]]

# Statuses with which a student endpoint refuses one request for what it asks, not every request: vLLM answers 400 for
# an unsupported parameter or a prompt over its context, 413 and 422 are the same refusal from other servers. An auth
# error or a missing adapter (401, 403, 404) would refuse every row, so it still stops the night, as does an outage.
REFUSED = frozenset({400, 413, 422})


def refused(e: BaseException) -> bool:
    """Whether a student call's failure is the endpoint refusing that one request (`REFUSED`)."""
    return isinstance(e, UpstreamError) and e.status in REFUSED


JUDGE_PROMPT = (
    "You compare a CANDIDATE answer with a REFERENCE answer for the same request. "
    "Score 1 if the candidate is as correct and useful as the reference, else 0. "
    'Reply with JSON only: {"score": 0 or 1, "reason": "<short>"}'
)


Point = tuple[float, bool]  # (confidence, agreed with the teacher's answer)


class _Points(NamedTuple):
    """One task's scored golden rows. `calib_served` and `test_served` are those whose answer the task's `escalate_when`
    rules let the student serve (`_servable`): for a task without rules, every row, in the same lists."""

    task: tasks.Task
    calib: list[Point]
    test: list[Point]
    calib_served: list[Point]
    test_served: list[Point]

    @classmethod
    def new(cls, task: tasks.Task) -> _Points:
        calib: list[Point] = []
        test: list[Point] = []
        if task.routing_rules.rules:
            return cls(task, calib, test, [], [])
        return cls(task, calib, test, calib, test)


def _servable(task: tasks.Task, key: Any, confidence: float) -> bool:
    """Whether the task's `escalate_when` rules let the student serve an answer with this key at this confidence
    (FR-2.50): no rule holds, or each that holds asks for no more confidence (`routing.Routing.floor`). An invalid answer
    (or none: the endpoint refused the row) never is, as in `Routing.critical_row`. A task without rules: always, so its
    rows are calibrated and measured exactly as before."""
    rules = task.routing_rules
    if not rules.rules:
        return True
    return key is not INVALID and confidence >= rules.floor(key)


def _task_record(
    task: tasks.Task,
    calib: list[Point],
    test: list[Point],
    global_thr: float,
    target_precision: float,
    floor: float,
    min_task_n: int,
    critical: list[CriticalRow] | None = None,
    calib_served: list[Point] | None = None,
    test_served: list[Point] | None = None,
) -> dict[str, Any]:
    """One task's calibration record (FR-14.1). Its threshold is calibrated on its own calibration rows to its
    `policy.target_precision` (else the eval's target), by the same rule as the global one. With fewer than `min_task_n`
    calibration rows it is `calibrated: false` and gets the higher of its own threshold and the global one, so a few
    lucky rows cannot lower it; with none it gets the global one. A task with no held-out row gets SERVE_NOTHING: the
    student never answers it without evidence measured on rows the threshold was not tuned on (INV-6). A task with
    critical classes (FR-2.50) also gets `critical`, their misses on its held-out rows (`critical`) at the threshold the
    record stores, which is the one the router applies.

    `calib_served` and `test_served` (default: `calib` and `test`) are the rows whose answer the task's `escalate_when`
    rules let the student serve (`_servable`): the threshold is calibrated, and precision and coverage measured, on
    those only, as production serves no other, while `test_n`, `agreement` and coverage's denominator count every
    row."""
    calib_served = calib if calib_served is None else calib_served
    test_served = test if test_served is None else test_served
    target = calibration.target_precision(task, target_precision)
    own = min(SERVE_NOTHING, max(floor, calibrate_threshold(calib_served, target))) if calib_served else None
    calibrated = own is not None and len(calib_served) >= min_task_n
    if not test:
        thr = SERVE_NOTHING
    elif calibrated:
        thr = own
    else:
        thr = global_thr if own is None else max(global_thr, own)
    stored = round(thr, 4)
    served = [ok for c, ok in test_served if c >= thr]
    points = calib + test
    record = {
        "golden_n": len(points),
        "test_n": len(test),
        "agreement": sum(ok for _, ok in points) / len(points) if points else 0.0,
        "threshold": stored,
        "precision": sum(served) / len(served) if served else 0.0,
        "coverage": len(served) / len(test) if test else 0.0,
        "spec_hash": task.spec_hash,
        "target_precision": target,
        "calibrated": calibrated,
    }
    if task.routing_rules.critical:  # only then, so every other record stays exactly as it was
        record["critical"] = task.routing_rules.misses(critical or [], stored)
    return record


def cascade_eval(
    sample: SampleFn,
    golden_rows: list[dict[str, Any]],
    samples: int = 3,
    target_precision: float = calibration.DEFAULT_TARGET_PRECISION,
    floor: float = 0.5,
    min_task_n: int = calibration.MIN_TASK_GOLDEN,
    per_task: bool = True,
) -> dict[str, Any]:
    """Calibrate the threshold on one half of the golden set and measure on the other half,
    so the reported precision is not graded on the data the threshold was tuned on. A row the
    student endpoint refuses (`refused`) counts as confidence 0 and wrong, as production would
    escalate it; `refused_n` says how many there were. A row whose reference is not valid under
    its task's spec is skipped before the student is sampled (`golden_invalid_refs`), and so is a
    row of a task that is pinned now (`excluded_pinned`, only when there is one); the halves
    alternate over the rows that are scored, so they stay the same size.

    `tasks` holds one record per task with golden rows that is not pinned (`_task_record`), split by
    the same global parity, so the global numbers above are exactly what they were before per-task
    calibration. A task whose every golden row was skipped as invalid has a record with no rows, and
    so SERVE_NOTHING. With `per_task` False (RATCHET_PER_TASK_CALIBRATION=0) there is no `tasks`, so
    the version keeps one pooled threshold and the gate checks only the pooled numbers, as in v0.4;
    only a task with critical classes (FR-2.50) still gets its record, which the critical gate needs.
    Those records then cover only some tasks, so the eval also says what the others come to
    (`other_tasks`: their held-out rows and how many the global threshold serves), which the release
    gate adds to the records' rows when it checks the coverage left after disabling a task.

    A row the task's routing block keeps from the student (FR-2.50) is skipped before it is sampled
    and counted (`excluded_ineligible`, only when there is one): production never shows it to the
    student, so it would calibrate a threshold for answers it never serves. Its task still gets a
    record. A row whose answer an `escalate_when` rule sends to the teacher is scored and counted, but
    neither calibrates a threshold nor counts as served (`_servable`), in the task's record and in the
    pooled numbers. For a task with critical classes, each held-out row also records whether the
    teacher's reference and the student's answer are in each class and what the task's rules ask of
    that answer (`routing.Routing.critical_row`), for `critical` in the task's record."""
    calib: list[Point] = []
    test: list[Point] = []
    calib_served: list[Point] = []  # the rows whose answer no escalate_when rule sends to the teacher
    test_served: list[Point] = []
    per_task_points: dict[str, _Points] = {}  # by the task's name, not its spelling
    critical_rows: dict[str, list[CriticalRow]] = {}  # held-out rows of the tasks with critical classes
    n_refused = n_invalid = n_pinned = n_ineligible = 0
    specs: dict[str, tasks.Task] = {}
    i = 0  # index among the scored rows: even calibrates, odd measures
    for row in golden_rows:
        task = specs.get(row["task"]) or specs.setdefault(row["task"], tasks.get(row["task"]))
        if task.pinned:  # INV-10, read time: pinned after its rows were stored, so the student never answers it now
            n_pinned += 1
            continue
        own = per_task_points.get(task.name) or per_task_points.setdefault(task.name, _Points.new(task))
        prompt, ref = row["messages"][:-1], row["messages"][-1]["content"]
        if task.routing_rules.ineligible(prompt) is not None:  # FR-2.50: the student never sees such a request
            n_ineligible += 1
            continue
        if not task.valid(ref):
            n_invalid += 1
            continue
        answer_key: Any = INVALID
        try:
            got = sample(prompt, samples, row.get("params"))
        except UpstreamError as e:
            if not refused(e):
                raise
            n_refused += 1
            point = (0.0, False)
        else:
            sc = score(task, got)
            point = (sc.confidence, task.agree(sc.answer.text, ref))
            answer_key = sc.key
        even = i % 2 == 0
        (calib if even else test).append(point)
        (own.calib if even else own.test).append(point)
        if _servable(task, answer_key, point[0]):
            (calib_served if even else test_served).append(point)
            if own.calib_served is not own.calib:
                (own.calib_served if even else own.test_served).append(point)
        if not even and task.routing_rules.critical:
            row_ = task.routing_rules.critical_row(task.key(ref), answer_key, point[0])
            critical_rows.setdefault(task.name, []).append(row_)
        i += 1
    thr = min(SERVE_NOTHING, max(floor, calibrate_threshold(calib_served, target_precision)))
    served = [ok for c, ok in test_served if c >= thr]
    allpts = calib + test
    extra: dict[str, Any] = {"excluded_pinned": n_pinned} if n_pinned else {}
    if n_ineligible:
        extra["excluded_ineligible"] = n_ineligible
    # With per-task calibration off, a task with critical classes still gets its record: the critical gate reads it, and
    # without one the router keeps the task on the teacher (`calibration.critical_measured`).
    records = {
        name: _task_record(
            pts.task,
            pts.calib,
            pts.test,
            thr,
            target_precision,
            floor,
            min_task_n,
            critical_rows.get(name),
            pts.calib_served,
            pts.test_served,
        )
        for name, pts in sorted(per_task_points.items())
        if per_task or pts.task.routing_rules.critical
    }
    if per_task or records:
        extra["tasks"] = records
    if not per_task and records:
        # The records cover only the critical tasks: the others are served at the global threshold, and the release gate
        # needs their rows to check the coverage left once it disables a critical task.
        rest = [pts for name, pts in per_task_points.items() if name not in records]
        extra["other_tasks"] = {
            "test_n": sum(len(pts.test) for pts in rest),
            "served": sum(1 for pts in rest for c, _ in pts.test_served if c >= thr),
        }
    return {
        "golden_n": len(allpts),
        "golden_agreement": sum(ok for _, ok in allpts) / len(allpts) if allpts else 0.0,
        "threshold": round(thr, 4),
        "coverage": len(served) / len(test) if test else 0.0,
        "precision": sum(served) / len(served) if served else 0.0,
        "test_n": len(test),
        "refused_n": n_refused,
        "golden_invalid_refs": n_invalid,
        **extra,
    }


class LiveEvaluator:
    """Evaluates an adapter served by vLLM (adapter name = model name). `policy` is the gateway's `GenPolicy`
    (`Settings.gen_policy()`), so each golden row's student call gets what the router would send for it. `min_task_n`
    is RATCHET_MIN_TASK_GOLDEN (`Settings.min_task_golden_rows()`), the calibration rows a task needs for its own
    threshold (FR-14.1), and `per_task` RATCHET_PER_TASK_CALIBRATION (False: no per-task records)."""

    def __init__(
        self,
        student: Provider,
        judge: Provider | None = None,
        judge_model: str = "",
        samples: int = 3,
        target_precision: float = calibration.DEFAULT_TARGET_PRECISION,
        policy: GenPolicy | None = None,
        min_task_n: int = calibration.MIN_TASK_GOLDEN,
        per_task: bool = True,
    ):
        self.student = student
        self.judge = judge
        self.judge_model = judge_model
        self.samples = samples
        self.target_precision = target_precision
        self.policy = policy or GenPolicy()
        self.min_task_n = min_task_n
        self.per_task = per_task

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

        res = cascade_eval(
            sample, golden_rows, self.samples, self.target_precision, min_task_n=self.min_task_n, per_task=self.per_task
        )
        free_text = [r for r in golden_rows if not tasks.get(r["task"]).fields and not tasks.get(r["task"]).pinned]
        if self.judge and free_text:
            ok = 0
            for row in free_text:
                prompt, ref = row["messages"][:-1], row["messages"][-1]["content"]
                gen = self.policy.student(GenParams.from_stored(row.get("params")))
                try:
                    cand = self.student.complete(prompt, adapter_uri, n=1, temperature=0.0, params=gen)[0].text
                except UpstreamError as e:
                    if not refused(e):
                        raise
                    continue  # refused: the row does not pass
                ok += self.judge_score(prompt, ref, cand)
            res["judge_pass_rate"] = ok / len(free_text)
        return res
