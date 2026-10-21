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
reference-based judge score. A row stored with a response_format is graded under it
(`tasks.for_row`, FR-1.40), as the router graded the request: a sample that breaks the schema
gets no vote, and a reference that breaks it is skipped as invalid.

A golden row whose reference (the stored teacher answer) is not valid under the task's current
spec is skipped and counted (`golden_invalid_refs`): the spec may have tightened since the
answer was stored, and a reference the task refuses cannot grade the student. A row of a task
that is pinned now is skipped and counted too (`excluded_pinned`, INV-10): the student never
answers a pinned task, so its rows would calibrate a threshold for answers it never serves.

A row the student endpoint refuses (`REFUSED`: a stored condition it cannot serve, such as
`tools` on a vLLM without a tool parser or a schema its guided decoding cannot compile, or a
prompt too long for it) escalates in production (INV-7), so here it scores as a request the
student would not serve: confidence 0, wrong. It never stops the night.

Certified precision (FR-16.3, ADR 0026): the held-out half played no part in choosing the
threshold, so its counts at that threshold are one exact test. Every eval carries a
Clopper-Pearson certificate, a Bonferroni-valid frontier over `stats.CERT_GRID`, a per-task
breakdown and the per-row outcomes (`test_vector`) that `paired` compares row by row
(`ratchet/certificate.py`). `calibration='ltt'` picks the threshold by Learn-then-Test over the
fixed grid instead of the point rule.

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

Free text (FR-2.55, FR-14.40, ADR 0054). Each row is sampled as many times as its task's confidence score
rests on in production (`Task.sample_count`: the task's `policy.samples`, else the eval's
`samples`). For a task with a `similarity` field, agreeing with the teacher means close enough by
a score, so its precision is a proxy (`precision_kind: proxy` in its record, and globally when such
a task was scored). With a `judge`, the answers the student would serve on the held-out rows of
free-text tasks (`Task.free_text`: no fields, or a similarity field) are graded against the
teacher's: `judge_pass_rate` and `judge_n`, per task and overall, which the release gate can hold
to RATCHET_MIN_JUDGE_PASS. The judge runs here, at night, never on the request path (ADR 0008).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, NamedTuple

from .. import calibration, certificate, tasks
from ..certificate import CALIBRATIONS, paired  # noqa: F401  (`evaluate.paired`: the paired comparison, ADR 0026)
from ..compare import INVALID
from ..confidence import SERVE_NOTHING, STUDENT_SAMPLING, calibrate_threshold, score
from ..providers import Completion, GenParams, GenPolicy, Provider, UpstreamError
from ..resilience import adapter_missing
from ..routing import CriticalRow
from ..stats import CERT_GRID, ltt_fixed_sequence

# sample(prompt, n, params): n student samples for a golden row's prompt, under the conditions stored with it (the row's
# `params`, a dict of ledger.STORED_PARAM_KEYS, or None).
SampleFn = Callable[[list[dict[str, Any]], int, "dict[str, Any] | None"], list[Completion]]
# judge(prompt, reference, candidate, params): whether `candidate`, the answer the student would serve (as served, by
# `tasks.clean_answer`), is as good as `reference`, the stored teacher answer, for the request `prompt` stored with
# conditions `params`.
JudgeFn = Callable[[list[dict[str, Any]], str, str, "dict[str, Any] | None"], bool]

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


def _level(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
        raise ValueError(f"{name} must be a number strictly between 0 and 1, not {value!r}")
    return float(value)


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


def _task_threshold(
    task: tasks.Task,
    calib: list[Point],
    test: list[Point],
    global_thr: float,
    target_precision: float,
    floor: float,
    min_task_n: int,
    calib_served: list[Point] | None = None,
) -> tuple[float, bool, float]:
    """A task's threshold (FR-14.1), whether it is its own (`calibrated`), and the target it was calibrated to. Its
    threshold is calibrated on its own calibration rows to its `policy.target_precision` (else the eval's target), by
    the same rule as the global one. With fewer than `min_task_n` calibration rows it is not its own and is the higher
    of its own threshold and the global one, so a few lucky rows cannot lower it; with none it is the global one. A task
    with no held-out row gets SERVE_NOTHING: the student never answers it without evidence measured on rows the
    threshold was not tuned on (INV-6). `calib_served` (default: `calib`) are the calibration rows whose answer the
    task's `escalate_when` rules let the student serve (`_servable`): the threshold is calibrated on those only."""
    calib_served = calib if calib_served is None else calib_served
    target = calibration.target_precision(task, target_precision)
    own = min(SERVE_NOTHING, max(floor, calibrate_threshold(calib_served, target))) if calib_served else None
    calibrated = own is not None and len(calib_served) >= min_task_n
    if not test:
        thr = SERVE_NOTHING
    elif calibrated:
        thr = own
    else:
        thr = global_thr if own is None else max(global_thr, own)
    return thr, calibrated, target


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
    """One task's calibration record (FR-14.1), at its threshold (`_task_threshold`), stored exactly as the global one
    is, because the router serves at it (a rounding up would serve less than the eval measured): `calibrated: false`
    when the threshold is not its own. A task with critical classes (FR-2.50) also gets `critical`, their misses on its
    held-out rows (`critical`) at that threshold. `calib_served` and `test_served` (default: `calib` and `test`) are the
    rows whose answer the task's `escalate_when` rules let the student serve (`_servable`): the threshold is calibrated,
    and precision and coverage measured, on those only, as production serves no other, while `test_n`, `agreement` and
    coverage's denominator count every row."""
    test_served = test if test_served is None else test_served
    thr, calibrated, target = _task_threshold(task, calib, test, global_thr, target_precision, floor, min_task_n, calib_served)
    served = [ok for c, ok in test_served if c >= thr]
    points = calib + test
    record = {
        "golden_n": len(points),
        "test_n": len(test),
        "agreement": sum(ok for _, ok in points) / len(points) if points else 0.0,
        "threshold": thr,
        "precision": sum(served) / len(served) if served else 0.0,
        "precision_kind": task.precision_kind,
        "coverage": len(served) / len(test) if test else 0.0,
        "spec_hash": task.spec_hash,
        "target_precision": target,
        "calibrated": calibrated,
    }
    if task.routing_rules.critical:  # only then, so every other record stays exactly as it was
        record["critical"] = task.routing_rules.misses(critical or [], thr)
    return record


def _pass_rate(passed: int, n: int) -> float | None:
    return passed / n if n else None


def cascade_eval(
    sample: SampleFn,
    golden_rows: list[dict[str, Any]],
    samples: int = 3,
    target_precision: float = calibration.DEFAULT_TARGET_PRECISION,
    floor: float = 0.5,
    calibration: str = "point",  # the rule; the module is read only in the defaults
    cert_target: float = 0.95,
    cert_delta: float = 0.05,
    min_task_n: int = calibration.MIN_TASK_GOLDEN,
    per_task: bool = True,
    judge: JudgeFn | None = None,
) -> dict[str, Any]:
    """Calibrate the threshold on one half of the golden set and measure on the other half,
    so the reported precision is not graded on the data the threshold was tuned on. A row the
    student endpoint refuses (`refused`) counts as confidence 0 and wrong, as production would
    escalate it; `refused_n` says how many there were. A row whose reference is not valid under
    its task's spec is skipped before the student is sampled (`golden_invalid_refs`), and so is a
    row of a task that is pinned now (`excluded_pinned`, only when there is one); the halves
    alternate over the rows that are scored, so they stay the same size.

    The threshold is the point rule's (`calibrate_threshold` at `target_precision`), or with
    `calibration='ltt'` Learn-then-Test's over `CERT_GRID` (`cert_target`, `cert_delta`), clamped
    the same way. Certified precision (ADR 0026) adds `certificate` (Clopper-Pearson at `cert_delta`
    against `cert_target`), `frontier`, `per_task` and `test_vector`, all from the held-out half at
    the same threshold as `coverage` and `precision`, the one stored and served at. Nothing here
    draws a random number.

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
    that answer (`routing.Routing.critical_row`), for `critical` in the task's record. The certificate,
    frontier, per-task block and outcome vector count such a row as escalated, as production does.

    Each row is sampled `Task.sample_count(samples)` times, as the router samples its task. With a
    `judge` and a free-text task in the rows (`Task.free_text`), the held-out rows of free-text tasks
    whose answer the student would serve, at the threshold the eval measures coverage at (the task's
    own with per-task records, else the global one), are judged after calibration: `judge_n` and
    `judge_pass_rate` (None when nothing was served to judge) globally and in each free-text task's
    record. A row the student refused, or whose samples were all invalid, serves nothing, so it is
    not judged. `precision_kind` is `proxy` when a task with a similarity field was scored."""
    if calibration not in CALIBRATIONS:
        raise ValueError(f"calibration must be {' or '.join(CALIBRATIONS)}, not {calibration!r}")
    cert_target, cert_delta = _level(cert_target, "cert_target"), _level(cert_delta, "cert_delta")
    calib: list[Point] = []
    test: list[Point] = []
    test_cert: list[Point] = []  # the held-out rows as the certificate sees them (`_servable`)
    test_tasks: list[str] = []  # the task of each held-out row, for the per-task breakdown
    calib_served: list[Point] = []  # the rows whose answer no escalate_when rule sends to the teacher
    test_served: list[Point] = []
    per_task_points: dict[str, _Points] = {}  # by the task's name, not its spelling
    critical_rows: dict[str, list[CriticalRow]] = {}  # held-out rows of the tasks with critical classes
    n_refused = n_invalid = n_pinned = n_ineligible = 0
    specs: dict[str, tasks.Task] = {}
    kinds: set[str] = set()  # the precision kinds of the tasks scored
    free_text: set[str] = set()  # free-text tasks with golden rows (the judge's tasks)
    # Held-out rows of free-text tasks: (task, confidence, the answer it would serve or None, prompt, reference, params).
    candidates: list[tuple[str, float, str | None, list[dict[str, Any]], str, Any]] = []
    i = 0  # index among the scored rows: even calibrates, odd measures
    for row in golden_rows:
        task = specs.get(row["task"]) or specs.setdefault(row["task"], tasks.get(row["task"]))
        if task.pinned:  # INV-10, read time: pinned after its rows were stored, so the student never answers it now
            n_pinned += 1
            continue
        own = per_task_points.get(task.name) or per_task_points.setdefault(task.name, _Points.new(task))
        task = tasks.for_row(row, task)  # held to the row's stored response_format, as the router held the request
        if task.free_text:
            free_text.add(task.name)
        prompt, ref = row["messages"][:-1], row["messages"][-1]["content"]
        if task.routing_rules.ineligible(prompt) is not None:  # FR-2.50: the student never sees such a request
            n_ineligible += 1
            continue
        if not task.valid(ref):
            n_invalid += 1
            continue
        answer_key: Any = INVALID
        answer: str | None = None
        try:
            got = sample(prompt, task.sample_count(samples), row.get("params"))
        except UpstreamError as e:
            if not refused(e):
                raise
            n_refused += 1
            point = (0.0, False)
        else:
            sc = score(task, got)
            point = (sc.confidence, task.agree(sc.answer.text, ref))
            answer_key = sc.key
            answer = tasks.clean_answer(task, sc.answer.text) if sc.valid else None
        even = i % 2 == 0
        (calib if even else test).append(point)
        (own.calib if even else own.test).append(point)
        servable = _servable(task, answer_key, point[0])
        if servable:
            (calib_served if even else test_served).append(point)
            if own.calib_served is not own.calib:
                (own.calib_served if even else own.test_served).append(point)
        if not even:
            test_tasks.append(task.name)
            # The certificate and the outcome vector see a row a rule sends to the teacher as escalated, as production
            # would escalate it (confidence 0: never served at any threshold).
            test_cert.append(point if servable else (0.0, point[1]))
        if not even and task.routing_rules.critical:
            row_ = task.routing_rules.critical_row(task.key(ref), answer_key, point[0])
            critical_rows.setdefault(task.name, []).append(row_)
        kinds.add(task.precision_kind)
        if judge is not None and not even and task.free_text and servable:  # a rule's escalation serves nothing to judge
            candidates.append((task.name, point[0], answer, prompt, ref, row.get("params")))
        i += 1
    trace = None
    if calibration == "ltt":
        lam, trace = ltt_fixed_sequence(calib_served, CERT_GRID, cert_target, cert_delta)
    else:
        lam = calibrate_threshold(calib_served, target_precision)
    thr = min(SERVE_NOTHING, max(floor, lam))
    served = [ok for c, ok in test_served if c >= thr]
    allpts = calib + test
    extra: dict[str, Any] = {"excluded_pinned": n_pinned} if n_pinned else {}
    if n_ineligible:
        extra["excluded_ineligible"] = n_ineligible
    # With per-task calibration off, a task with critical classes still gets its record: the critical gate reads it, and
    # without one the router keeps the task on the teacher (`calibration.critical_measured`).
    records: dict[str, dict[str, Any]] = {
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
    if judge is not None and free_text:
        judged: dict[str, list[int]] = {name: [0, 0] for name in free_text}  # task -> [judged, passed]
        # Each task's threshold as its coverage and precision were measured at: its own with per-task records, else the
        # global one.
        at: dict[str, float] = {
            name: _task_threshold(pts.task, pts.calib, pts.test, thr, target_precision, floor, min_task_n, pts.calib_served)[0]
            if name in records
            else thr
            for name, pts in per_task_points.items()
            if name in free_text
        }
        for name, confidence, answer, prompt, ref, params in candidates:
            if answer is None or confidence < at[name]:
                continue  # escalated in production: nobody gets this answer, so there is nothing to judge
            judged[name][0] += 1
            judged[name][1] += int(bool(judge(prompt, ref, answer, params)))
        n_judged, n_passed = sum(n for n, _ in judged.values()), sum(p for _, p in judged.values())
        extra["judge_n"] = n_judged
        extra["judge_pass_rate"] = _pass_rate(n_passed, n_judged)
        for name, (n, passed) in judged.items():
            if name in records:
                records[name]["judge_n"] = n
                records[name]["judge_pass_rate"] = _pass_rate(passed, n)
    return {
        "golden_n": len(allpts),
        "golden_agreement": sum(ok for _, ok in allpts) / len(allpts) if allpts else 0.0,
        # Exactly the threshold measured below: the router serves at it (`Ratchet.threshold`). Rounded to 4 decimals, as
        # before v0.5, it could round up past the confidence it was calibrated at, so the version served less than its
        # eval measured (nothing at all when every row has one confidence).
        "threshold": thr,
        "coverage": len(served) / len(test) if test else 0.0,
        "precision": sum(served) / len(served) if served else 0.0,
        "precision_kind": "proxy" if "proxy" in kinds else "exact",
        "test_n": len(test),
        "refused_n": n_refused,
        "golden_invalid_refs": n_invalid,
        **extra,
        "certificate": certificate.certificate(test_cert, thr, cert_target, cert_delta, calibration, trace),
        "frontier": certificate.frontier(test_cert, cert_delta),
        "per_task": certificate.per_task(test_cert, test_tasks, thr, cert_delta),
        "test_vector": certificate.outcome_vector(test_cert, thr),
    }


class LiveEvaluator:
    """Evaluates an adapter served by vLLM (adapter name = model name). `policy` is the gateway's `GenPolicy`
    (`Settings.gen_policy()`), so each golden row's student call gets what the router would send for it. `min_task_n`
    is RATCHET_MIN_TASK_GOLDEN (`Settings.min_task_golden_rows()`), the calibration rows a task needs for its own
    threshold (FR-14.1), and `per_task` RATCHET_PER_TASK_CALIBRATION (False: no per-task records). With a `judge`
    (RATCHET_JUDGE_MODEL on Token Factory), the answers the student would serve on free-text tasks' held-out rows are
    graded against the teacher's (`cascade_eval`'s `judge`, FR-5.5, FR-14.40): the candidate is the answer chosen from
    the samples drawn with `STUDENT_SAMPLING`, so no extra student call is made and the judge grades what production
    would serve. `on_adapter_missing(adapter_uri)` loads an adapter the endpoint forgot (it restarted since the adapter was
    loaded): when a call answers 404 for the adapter, the evaluator calls it once per `evaluate` and asks again, so one
    restart does not end a night."""

    def __init__(
        self,
        student: Provider,
        judge: Provider | None = None,
        judge_model: str = "",
        samples: int = 3,
        target_precision: float = calibration.DEFAULT_TARGET_PRECISION,
        policy: GenPolicy | None = None,
        calibration: str = "point",
        cert_target: float = 0.95,
        cert_delta: float = 0.05,
        min_task_n: int = calibration.MIN_TASK_GOLDEN,
        per_task: bool = True,
        on_adapter_missing: Callable[[str], object] | None = None,
    ):
        self.on_adapter_missing = on_adapter_missing
        self.student = student
        self.judge = judge
        self.judge_model = judge_model
        self.samples = samples
        self.target_precision = target_precision
        self.policy = policy or GenPolicy()
        # Certified precision (ADR 0026): `Settings.cert_policy()` in live mode.
        self.calibration, self.cert_target, self.cert_delta = calibration, cert_target, cert_delta
        self.min_task_n = min_task_n
        self.per_task = per_task

    def judge_score(self, request: list[dict[str, Any]], reference: str, candidate: str) -> int:
        """1 when the judge answers exactly `{"score": 1}` (in JSON mode; reasoning or a fence around it is fine), else
        0, so an answer the judge does not clearly pass never counts as passing. A failed call raises: a night without
        its judge's verdict stops, rather than ship unjudged."""
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
        reloaded: list[bool] = []

        def sample(prompt, n, params=None):
            gen = self.policy.student(GenParams.from_stored(params))
            try:
                return self.student.complete(prompt, adapter_uri, n=n, params=gen, **STUDENT_SAMPLING)
            except Exception as e:
                if self.on_adapter_missing is None or reloaded or not adapter_missing(e, adapter_uri):
                    raise
            reloaded.append(True)  # the endpoint forgot the adapter: load it again, once, and ask again
            self.on_adapter_missing(adapter_uri)
            return self.student.complete(prompt, adapter_uri, n=n, params=gen, **STUDENT_SAMPLING)

        def judge(prompt, reference, candidate, params=None):
            return self.judge_score(prompt, reference, candidate) == 1

        return cascade_eval(
            sample,
            golden_rows,
            self.samples,
            self.target_precision,
            calibration=self.calibration,
            cert_target=self.cert_target,
            cert_delta=self.cert_delta,
            min_task_n=self.min_task_n,
            per_task=self.per_task,
            judge=judge if self.judge is not None else None,
        )
