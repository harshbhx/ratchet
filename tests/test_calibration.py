"""Per-task calibration when several tasks share one adapter (FR-14.1, ADR 0040).

The golden eval's per-task records (`cascade_eval`, `eval['tasks']`), the router's threshold per task, the per-task
golden gate (`ReleaseGate.admit`, `task_mode`), disabled tasks that survive a re-score, the per-task live alert,
`stats_by_task`'s calibration keys, the settings that drive them, and one mixed-workload simulation. Simulation numbers
come from mock models.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from fakes import OPEN_TEACHER, RecordingEvaluator, RecordingProvider, RecordingTrainer, _examples, _version

from ratchet import calibration, tasks, taskspec
from ratchet.confidence import SERVE_NOTHING
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.evaluate import LiveEvaluator, cascade_eval
from ratchet.nightschool.orchestrate import run_night, stale_tasks
from ratchet.providers import Completion
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet
from sim.simulate import parser, run

RIGHT, WRONG = "yes", "no"


def _text_task(name: str, **kw: Any) -> tasks.Task:
    """Register a task without fields (whole answers compared), as a tasks file would."""
    task = tasks.Task(name, source="file", **kw)
    tasks.register(task)
    return task


def _conf_to_prob(conf: float) -> float:
    """The token probability that gives a unanimous 3-sample answer this confidence (0.4 p + 0.6, FR-2.2)."""
    return (conf - 0.6) / 0.4


def _row(task: str, tag: str, conf: float, right: bool) -> dict[str, Any]:
    """A golden row whose student answer is scripted in its prompt: unanimous, at confidence `conf`, right or wrong."""
    prompt = json.dumps({"tag": tag, "conf": conf, "right": right})
    return {"task": task, "messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": RIGHT}]}


def _sample(prompt: list[dict[str, Any]], n: int, params: Any = None) -> list[Completion]:
    spec = json.loads(prompt[-1]["content"])
    lp = math.log(_conf_to_prob(spec["conf"]))
    text = RIGHT if spec["right"] else WRONG
    return [Completion(text, "student-v1", 10, 3, 1.0, [lp] * 3) for _ in range(n)]


def _easy_and_noisy(noisy_conf_right: float = 0.95) -> list[dict[str, Any]]:
    """Periods of four easy rows and one noisy row (an odd period, so each task's rows alternate between the halves).
    Every easy answer is right at confidence 0.85; the noisy task is right at `noisy_conf_right` except six rows (three
    in each half), wrong at 0.85."""
    rows = []
    for j in range(40):
        rows += [_row("easy", f"e{j}.{k}", 0.85, True) for k in range(4)]
        rows.append(
            _row("noisy", f"n{j}", 0.85 if j in (0, 1, 20, 21, 38, 39) else noisy_conf_right, j not in (0, 1, 20, 21, 38, 39))
        )
    return rows


# ---------- the golden eval: one record per task ----------
@pytest.mark.req("FR-14.1", "FR-5.2", "INV-6")
def test_cascade_eval_per_task_thresholds():
    easy, noisy = _text_task("easy"), _text_task("noisy")
    res = cascade_eval(_sample, _easy_and_noisy())
    # Pooled, the noisy task's wrong answers are diluted: the global threshold serves them.
    assert res["threshold"] == pytest.approx(0.85) and (res["golden_n"], res["test_n"]) == (200, 100)
    assert res["precision"] == pytest.approx(0.97) and res["coverage"] == 1.0
    e, n = res["tasks"]["easy"], res["tasks"]["noisy"]
    assert set(res["tasks"]) == {"easy", "noisy"}
    assert (e["golden_n"], e["test_n"], n["golden_n"], n["test_n"]) == (160, 80, 40, 20)
    assert e["threshold"] == pytest.approx(0.85) and (e["precision"], e["coverage"]) == (1.0, 1.0)
    # On its own rows the noisy task needs 0.95: what it serves is all right, and it serves less.
    assert n["threshold"] == pytest.approx(0.95) and (n["precision"], n["coverage"]) == (1.0, 0.85)
    assert n["agreement"] == pytest.approx(34 / 40) and e["agreement"] == 1.0
    assert (e["calibrated"], n["calibrated"]) == (True, True)  # 80 and exactly 20 calibration rows
    assert (e["spec_hash"], n["spec_hash"]) == (easy.spec_hash, noisy.spec_hash)
    assert e["target_precision"] == n["target_precision"] == 0.97

    # When no threshold of its own reaches the target, the noisy task serves nothing.
    flat = cascade_eval(_sample, _easy_and_noisy(noisy_conf_right=0.85))
    assert flat["tasks"]["noisy"]["threshold"] == SERVE_NOTHING and flat["tasks"]["noisy"]["coverage"] == 0.0
    assert flat["tasks"]["easy"]["threshold"] == pytest.approx(0.85)

    # Its spec's policy sets its own target: at 80%, 85% right at 0.85 is enough.
    _text_task("noisy", policy={"target_precision": 0.8})
    lenient = cascade_eval(_sample, _easy_and_noisy(noisy_conf_right=0.85))["tasks"]["noisy"]
    assert lenient["threshold"] == pytest.approx(0.85) and lenient["target_precision"] == 0.8
    assert lenient["spec_hash"] == tasks.get("noisy").spec_hash != noisy.spec_hash


@pytest.mark.req("FR-14.1", "FR-5.2", "FR-5.3")
@pytest.mark.parametrize("min_task_n", [1, 20, 1000])
def test_single_task_eval_identical_to_global(min_task_n):
    """With one task, its record is the global numbers again, whether or not it has enough rows to be calibrated."""
    pattern = [(0.95, True), (0.7, True), (0.85, False), (0.9, True), (0.65, False), (0.99, True), (0.8, True)]
    rows = [_row("ticket-triage", f"t{i}", *pattern[i % len(pattern)]) for i in range(60)]
    rows = [{**r, "messages": [r["messages"][0], {"role": "assistant", "content": _TICKET}]} for r in rows]
    res = cascade_eval(_ticket_sample, rows, min_task_n=min_task_n)
    rec = res["tasks"]["ticket-triage"]
    assert set(res["tasks"]) == {"ticket-triage"}
    assert (rec["threshold"], rec["precision"], rec["coverage"]) == (res["threshold"], res["precision"], res["coverage"])
    assert (rec["golden_n"], rec["test_n"], rec["agreement"]) == (res["golden_n"], res["test_n"], res["golden_agreement"])
    assert rec["calibrated"] is (min_task_n <= 30)
    assert 0.5 < res["threshold"] < SERVE_NOTHING and 0 < res["coverage"] < 1


_TICKET = json.dumps({"category": "billing", "priority": "high"})
_TICKET_WRONG = json.dumps({"category": "refund", "priority": "low"})


def _ticket_sample(prompt: list[dict[str, Any]], n: int, params: Any = None) -> list[Completion]:
    spec = json.loads(prompt[-1]["content"])
    lp = math.log(_conf_to_prob(spec["conf"]))
    text = _TICKET if spec["right"] else _TICKET_WRONG
    return [Completion(text, "student-v1", 10, 3, 1.0, [lp] * 3) for _ in range(n)]


@pytest.mark.req("FR-14.1", "FR-5.2")
def test_task_with_few_calibration_rows_is_never_below_global():
    """A task with fewer than `min_task_n` calibration rows takes the higher of its own threshold and the global one, so
    a few lucky rows cannot lower it; with no calibration row at all it takes the global one."""
    _text_task("big"), _text_task("small")
    big = [_row("big", f"b{i}", 0.9 if i % 10 else 0.8, i % 10 != 0) for i in range(60)]  # wrong at 0.8: needs 0.9
    small_low = [_row("small", f"s{i}", 0.7, True) for i in range(4)]  # its own threshold would be 0.7
    res = cascade_eval(_sample, big + small_low)
    assert res["tasks"]["small"]["threshold"] == pytest.approx(res["threshold"]) == pytest.approx(0.9)
    assert res["tasks"]["small"]["calibrated"] is False and res["tasks"]["big"]["calibrated"] is True
    small_high = [_row("small", f"s{i}", 0.95 if i % 2 == 0 else 0.65, i % 2 == 0) for i in range(6)]
    res = cascade_eval(_sample, big + small_high, min_task_n=20)
    assert res["tasks"]["small"]["threshold"] == pytest.approx(0.95) and res["threshold"] == pytest.approx(0.9)
    assert res["tasks"]["small"]["calibrated"] is False
    assert cascade_eval(_sample, big + small_high, min_task_n=3)["tasks"]["small"]["calibrated"] is True
    lonely = cascade_eval(_sample, [*big, _row("small", "s", 0.7, True)])["tasks"]["small"]  # index 60: calibrates
    assert (lonely["golden_n"], lonely["test_n"], lonely["threshold"]) == (1, 0, SERVE_NOTHING)  # nothing held out
    odd = cascade_eval(_sample, [*big[:-1], _row("small", "s", 0.95, True)])["tasks"]["small"]  # index 59: held out
    assert (odd["golden_n"], odd["test_n"], odd["calibrated"]) == (1, 1, False)
    assert odd["threshold"] == pytest.approx(0.9) and (odd["coverage"], odd["precision"]) == (1.0, 1.0)  # the global one


@pytest.mark.req("FR-14.1", "INV-6")
def test_task_without_test_rows_is_teacher_only():
    _text_task("a"), _text_task("b"), _text_task("strict")
    rows = [_row("a", f"a{i}", 0.9, True) for i in range(40)]
    rows.insert(10, _row("b", "only", 0.99, True))  # index 10: even, so its one row calibrates and none is held out
    rows.append({"task": "strict", "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": " "}]})
    res = cascade_eval(_sample, rows)
    b = res["tasks"]["b"]
    assert (b["golden_n"], b["test_n"], b["threshold"], b["coverage"], b["precision"]) == (1, 0, SERVE_NOTHING, 0.0, 0.0)
    # A task whose every golden reference is invalid under its spec has a record with no rows: nothing to serve on.
    assert res["golden_invalid_refs"] == 1
    assert res["tasks"]["strict"]["golden_n"] == 0 and res["tasks"]["strict"]["threshold"] == SERVE_NOTHING

    led = Ledger(":memory:")
    vid = _version(led, "live", res)
    student = RecordingProvider(RIGHT, logprob=math.log(0.99))
    r = Ratchet(Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0), led, RecordingProvider(RIGHT), _student(student))
    assert r.handle(_ask("a"), "a").route == "student"
    out = r.handle(_ask("b"), "b")
    assert (out.served_by, out.route, out.confidence) == ("teacher", "confidence", pytest.approx(0.996))
    assert r.threshold(led.version(vid), "student-v1", "b") == SERVE_NOTHING


def _student(provider: RecordingProvider):
    return lambda v: (provider, v.adapter_uri)


def _ask(tag: object) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"question {tag}"}]


# ---------- the router ----------
def _record(task: str, threshold: float, **kw: Any) -> dict[str, Any]:
    return {
        "golden_n": 80,
        "test_n": 40,
        "agreement": 0.95,
        "threshold": threshold,
        "precision": kw.pop("precision", 1.0),
        "coverage": kw.pop("coverage", 0.9),
        "spec_hash": kw.pop("spec_hash", tasks.get(task).spec_hash),
        "target_precision": 0.97,
        "calibrated": True,
        **kw,
    }


@pytest.mark.req("FR-14.1", "FR-2.3")
def test_router_uses_task_threshold():
    _text_task("A"), _text_task("B"), _text_task("C")
    led = Ledger(":memory:")
    ev = {"threshold": 0.8, "precision": 0.97, "coverage": 0.9, "tasks": {"A": _record("A", 0.7), "B": _record("B", 0.95)}}
    vid = _version(led, "live", ev)
    student = RecordingProvider(RIGHT, logprob=math.log(_conf_to_prob(0.9)))  # unanimous at confidence 0.9
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0)
    r = Ratchet(s, led, RecordingProvider(RIGHT), _student(student))
    a, b, c = r.handle(_ask(1), "A"), r.handle(_ask(2), "B"), r.handle(_ask(3), "C")
    assert a.confidence == b.confidence == c.confidence == pytest.approx(0.9)
    assert (a.served_by, a.route) == ("student", "student")
    assert (b.served_by, b.route) == ("teacher", "confidence")
    assert (c.served_by, c.route) == ("student", "student")  # no record for C: the version's global threshold
    v = led.version(vid)
    assert [r.threshold(v, "student-v1", t) for t in ("A", "B", "C")] == [0.7, 0.95, 0.8]
    assert r.threshold(v, "student-v1") == 0.8  # without a task, as before
    assert r.threshold(v, s.fallback_student_model, "B") == s.confidence_threshold  # the untuned model: never calibrated
    # The fingerprint names the threshold the answer was held to, so each task of one version has its own.
    assert a.fingerprint != c.fingerprint and a.fingerprint == r.handle(_ask(4), "A").fingerprint


@pytest.mark.req("FR-14.1", "FR-2.3", "FR-2.15")
def test_stale_spec_hash_threshold_ignored():
    spec = {"name": "amounts", "fields": {"total": {"type": "number"}}}
    tasks.register(taskspec.parse_spec(spec, "file"))
    led = Ledger(":memory:")
    old = tasks.get("amounts").spec_hash
    vid = _version(led, "live", {"threshold": 0.9, "tasks": {"amounts": _record("amounts", 0.5)}})
    student = RecordingProvider('{"total": 12.5}', logprob=math.log(0.9))
    r = Ratchet(
        Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0),
        led,
        RecordingProvider('{"total": 12.5}'),
        _student(student),
    )
    assert r.handle(_ask(1), "amounts").route == "student" and not r.spec_changed(led.version(vid), "amounts")

    tasks.register(taskspec.parse_spec({**spec, "fields": {"total": {"type": "number", "abs_tol": 0.01}}}, "file"))
    v = led.version(vid)
    assert tasks.get("amounts").spec_hash != old and r.spec_changed(v, "amounts")
    assert r.threshold(v, "student-v1", "amounts") == SERVE_NOTHING  # neither its own 0.5 nor the global 0.9
    assert r.handle(_ask(2), "amounts").route == "confidence"
    assert stale_tasks(v, {"amounts": tasks.get("amounts").spec_hash}) == ["amounts"]

    # The per-task record is read before the older `spec_hashes`: a current record wins over a stale hash there.
    current = _record("amounts", 0.5)
    both = _version(led, "canary", {"threshold": 0.9, "tasks": {"amounts": current}, "spec_hashes": {"amounts": old}})
    assert not r.spec_changed(led.version(both), "amounts") and r.threshold(led.version(both), "x", "amounts") == 0.5
    assert stale_tasks(led.version(both), {"amounts": current["spec_hash"]}) == []


@pytest.mark.req("FR-14.1", "FR-2.3")
def test_versions_without_task_eval_unchanged():
    """A version scored before per-task calibration keeps its one threshold for every task, the older `spec_hashes`
    still send a changed task to the teacher, and the gate checks only the global numbers."""
    _text_task("A")
    led = Ledger(":memory:")
    old = _version(led, "live", {"threshold": 0.9, "precision": 0.97, "coverage": 0.9})
    hashed = _version(led, "retired", {"threshold": 0.9, "spec_hashes": {"A": "0" * 16}})
    r = Ratchet(
        Settings(teacher_model=OPEN_TEACHER, samples=3), led, RecordingProvider(RIGHT), _student(RecordingProvider(RIGHT))
    )
    for task in ("A", "ticket-triage", "never-registered", None):
        assert r.threshold(led.version(old), "student-v1", task) == 0.9
    assert r.threshold(led.version(hashed), "student-v1", "A") == SERVE_NOTHING
    assert r.threshold(led.version(hashed), "student-v1", "ticket-triage") == 0.9
    gate = ReleaseGate(led, ReleasePolicy())
    cand = _version(led, "candidate", {"threshold": 0.9, "precision": 0.97, "coverage": 0.9})
    assert gate.task_failures(led.version(cand), led.live()) == []
    assert gate.admit(cand) and led.version(cand).state == "shadow"
    assert [e["kind"] for e in led.events()] == ["shadow"]
    assert calibration.entries(led.version(cand).eval) == {}


@pytest.mark.req("FR-14.1", "FR-2.3", "INV-7")
def test_threshold_reads_malformed_records_safely():
    task = _text_task("A")
    cases = [
        ({"threshold": 0.8, "tasks": "broken"}, 0.8),  # not a mapping: no records
        ({"threshold": 0.8, "tasks": {"A": "broken"}}, 0.8),  # a record that is not a mapping is left out
        ({"threshold": 0.8, "tasks": {"A": {"spec_hash": task.spec_hash, "threshold": "high"}}}, SERVE_NOTHING),
        ({"threshold": 0.8, "tasks": {"A": {"spec_hash": task.spec_hash, "threshold": float("nan")}}}, SERVE_NOTHING),
        ({"threshold": 0.8, "tasks": {"A": {"threshold": 0.5}}}, SERVE_NOTHING),  # no hash: cannot be current
        ({"threshold": 0.8, "tasks": {"A": {**_record("A", 0.5), "disabled": "by hand"}}}, SERVE_NOTHING),
        ({"threshold": 0.8, "spec_hashes": "broken"}, 0.8),
    ]
    for ev, want in cases:
        assert calibration.threshold(ev, task, 0.8) == want, ev
    assert calibration.entries(None) == {} and calibration.entry({"tasks": []}, "A") is None
    # A hand-edited global threshold that is not a number never fails a request: the student serves nothing (INV-7).
    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": "high"})
    r = Ratchet(
        Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0),
        led,
        RecordingProvider(RIGHT),
        _student(RecordingProvider(RIGHT)),
    )
    assert r.threshold(led.version(vid), "student-v1") == r.threshold(led.version(vid), "student-v1", "A") == SERVE_NOTHING
    assert r.handle(_ask(1), "A").route == "confidence"


# ---------- the release gate ----------
def _gate_setup(b_candidate: dict[str, Any], policy: ReleasePolicy | None = None) -> tuple[Ledger, ReleaseGate, int, int]:
    """A live version and a candidate, each with records for tasks A and B (registered as file tasks unless a test
    registered them first)."""
    for name in ("A", "B"):
        if tasks.lookup(name) is None:
            _text_task(name)
    led = Ledger(":memory:")
    live_tasks = {"A": _record("A", 0.8), "B": _record("B", 0.8, coverage=1.0)}
    live = _version(led, "live", {"threshold": 0.8, "precision": 0.97, "coverage": 0.9, "tasks": live_tasks})
    cand_tasks = {"A": _record("A", 0.75), "B": b_candidate}
    cand = _version(led, "candidate", {"threshold": 0.8, "precision": 0.97, "coverage": 0.9, "tasks": cand_tasks})
    return led, ReleaseGate(led, policy or ReleasePolicy()), live, cand


@pytest.mark.req("FR-14.1", "FR-6.1", "NFR-8")
def test_admit_disables_regressed_task_but_ships_version():
    # B: 36 of 40 answers right (90%): above the floor within its evidence (upper bound 96.0% >= 95%), but strongly behind
    # live's 100% on B (96.0% < 100% - 2 points).
    led, gate, live, cand = _gate_setup(_record("B", 0.85, precision=0.9, coverage=1.0))
    assert gate.admit(cand)
    v = led.version(cand)
    assert v.state == "shadow"
    b = v.eval["tasks"]["B"]
    assert b["threshold"] == SERVE_NOTHING and b["precision"] == 0.9  # the measurement stays; the threshold serves nothing
    assert b["disabled"] == f"regresses vs live v{live} (precision 90.0% on 40 answers, upper bound 96.0%, vs 100.0%)"
    assert "disabled" not in v.eval["tasks"]["A"] and v.eval["tasks"]["A"]["threshold"] == 0.75
    assert [(e["kind"], e["detail"]) for e in led.events()][:1] == [
        ("task_disabled", f"v{cand} B: {b['disabled']}; the teacher answers it, v{cand} ships for its other tasks")
    ]
    r = Ratchet(Settings(teacher_model=OPEN_TEACHER), led, RecordingProvider(RIGHT), _student(RecordingProvider(RIGHT)))
    assert r.threshold(v, "student-v2", "B") == SERVE_NOTHING and r.threshold(v, "student-v2", "A") == 0.75

    # A task far below its floor is disabled even with nothing live to compare with.
    led, gate, _, cand = _gate_setup(_record("B", 0.85, precision=0.7, coverage=1.0))
    led.set_version(led.live().id, "retired", 0)
    assert gate.admit(cand) and led.version(cand).state == "canary"
    assert led.version(cand).eval["tasks"]["B"]["disabled"].startswith("golden precision 70.0% on 40 answers, upper bound")


@pytest.mark.req("FR-14.1", "FR-6.1")
def test_admit_reject_mode():
    led, gate, live, cand = _gate_setup(_record("B", 0.85, precision=0.9, coverage=1.0), ReleasePolicy(task_mode="reject"))
    assert not gate.admit(cand)
    assert led.version(cand).state == "rejected" and "disabled" not in led.version(cand).eval["tasks"]["B"]
    assert [(e["kind"], e["detail"]) for e in led.events()] == [
        ("rejected", f"v{cand}: task B: regresses vs live v{live} (precision 90.0% on 40 answers, upper bound 96.0%, vs 100.0%)")
    ]


@pytest.mark.req("FR-14.1", "FR-6.1", "NFR-8")
def test_per_task_gate_needs_evidence():
    """Few held-out rows, a small dip within the noise, a task the candidate serves nothing of, a live version that
    does not serve the task or was scored under another spec: nothing to disable."""
    quiet = [
        _record("B", 0.85, precision=0.5, test_n=9, coverage=1.0),  # under min_task_test_n (10)
        _record("B", 0.85, precision=0.95, coverage=1.0),  # 38 of 40: upper bound 98.6%, within live's 100% - 2 points
        _record("B", SERVE_NOTHING, precision=0.0, coverage=0.0),  # serves nothing already
    ]
    for rec in quiet:
        led, gate, _, cand = _gate_setup(rec)
        assert gate.task_failures(led.version(cand), led.live()) == [], rec
    led, gate, live, cand = _gate_setup(_record("B", 0.85, precision=0.9, coverage=1.0))
    for live_b in (_record("B", SERVE_NOTHING), _record("B", 0.8, spec_hash="0" * 16), _record("B", 0.8, test_n=5)):
        ev = led.version(live).eval
        led.update_eval(live, {**ev, "tasks": {**ev["tasks"], "B": live_b}})
        assert gate.task_failures(led.version(cand), led.live()) == [], live_b
    # A task's own floor (its spec's policy.min_precision) is checked with the same evidence rule: 194 of 200 (97%)
    # has an upper bound of 98.6%, under a 99% floor.
    _text_task("B", policy={"min_precision": 0.99})
    led, gate, _, cand = _gate_setup(_record("B", 0.85, precision=0.97, test_n=200, coverage=1.0))
    assert [name for name, _ in gate.task_failures(led.version(cand), led.live())] == ["B"]


@pytest.mark.req("FR-14.1", "FR-6.1", "INV-5")
def test_admit_rejects_a_version_that_would_serve_too_little():
    """Disabling a task, or per-task thresholds that serve almost nothing, cannot leave a version in shadow or canary
    forever (no night trains while it is, INV-5): the coverage it keeps is held to min_coverage."""
    led, gate, _, cand = _gate_setup(_record("B", 0.85, precision=0.7, coverage=1.0))
    ev = led.version(cand).eval
    led.update_eval(cand, {**ev, "tasks": {**ev["tasks"], "A": _record("A", 0.75, coverage=0.25)}})
    assert not gate.admit(cand)  # A answers 10 of 80 held-out rows once B is off: 12.5%
    assert [(e["kind"], e["detail"]) for e in led.events()] == [
        ("rejected", f"v{cand}: golden coverage 12.5% < 25% once B disabled")
    ]
    assert "disabled" not in led.version(cand).eval["tasks"]["B"]

    led, gate, _, cand = _gate_setup(_record("B", SERVE_NOTHING, coverage=0.0))
    ev = led.version(cand).eval
    led.update_eval(cand, {**ev, "tasks": {**ev["tasks"], "A": _record("A", SERVE_NOTHING, coverage=0.0)}})
    assert not gate.admit(cand)
    assert led.events()[-1]["detail"] == f"v{cand}: golden coverage 0.0% < 25% at its per-task thresholds"


@pytest.mark.req("FR-14.1", "INV-4")
def test_disabled_task_survives_rescore():
    """A re-score measures a version again but runs no gate: a task the gate disabled on it stays disabled, even when
    the re-score has no record for it."""
    _text_task("A"), _text_task("B"), _text_task("C")
    led = Ledger(":memory:", golden_fraction=0.3)
    ev = {
        "threshold": 0.8,
        "precision": 0.97,
        "coverage": 0.9,
        "tasks": {
            "A": _record("A", 0.8),
            "B": _record("B", SERVE_NOTHING, disabled="regressed"),
            "C": _record("C", 1.01, disabled="low"),
        },
    }
    live = _version(led, "live", ev, train_examples=0)
    _examples(led, train=60, golden=10, task="A")
    recheck = {"threshold": 0.8, "precision": 0.99, "coverage": 0.95, "tasks": {"A": _record("A", 0.7), "B": _record("B", 0.6)}}
    rep = run_night(led, RecordingTrainer(), RecordingEvaluator(recheck), ReleaseGate(led), "base")
    assert rep.trained
    got = led.version(live).eval
    assert got["precision"] == 0.99 and got["tasks"]["A"]["threshold"] == 0.7
    assert (got["tasks"]["B"]["threshold"], got["tasks"]["B"]["disabled"]) == (SERVE_NOTHING, "regressed")
    assert got["tasks"]["C"] == ev["tasks"]["C"]  # no record in the re-score: the old one is kept
    assert calibration.keep_disabled({"tasks": {"A": _record("A", 0.7)}}, {"x": 1}) == {"x": 1}


# ---------- the per-task live alert ----------
def _audits(led: Ledger, vid: int, task: str, n: int, agree: int) -> None:
    for i in range(n):
        led.log_request(task=task, served_by="student", student_version=vid, audited=1, audit_agree=int(i < agree))


@pytest.mark.req("FR-14.1", "FR-6.7", "NFR-8")
def test_per_task_live_alert_once():
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.8})
    _audits(led, live, "bad_task", 20, 12)  # 60%: upper bound 78% < 92%
    _audits(led, live, "badxtask", 200, 200)  # all right, and a name the '_' of the first would match under LIKE
    gate = ReleaseGate(led, ReleasePolicy())
    gate.tick()
    gate.tick()
    alerts = [e["detail"] for e in led.events() if e["kind"] == "alert"]
    assert alerts == [
        f"v{live} task bad_task: audited precision 60.0% (n=20, upper bound 78.1% < 92%) while live; "
        "investigate, or pin the task to the teacher (see docs/RUNBOOK.md)"
    ]
    ReleaseGate(led, ReleasePolicy()).tick()  # a new gate (a new `ratchet.live tick` process) reads the ledger
    assert len([e for e in led.events() if e["kind"] == "alert"]) == 1
    assert led.has_event("alert", f"v{live} task bad_task:") and not led.has_event("alert", f"v{live} task bad?task:")
    assert led.version_live_quality(live, task="bad_task") == (20, 0.6)
    assert led.task_live_quality(live) == {"bad_task": (20, 0.6), "badxtask": (200, 1.0)}

    # The canary gets its own per-task alerts, in its current stage.
    canary = _version(led, "canary", {"threshold": 0.8})
    _audits(led, canary, "bad_task", 20, 10)
    _audits(led, canary, "badxtask", 30, 30)
    gate.tick()
    assert [e["detail"].split(":")[0] for e in led.events() if e["kind"] == "alert"][-1] == f"v{canary} task bad_task"


@pytest.mark.req("FR-14.1", "FR-6.7")
def test_per_task_alert_leaves_single_task_versions_to_the_version_alert():
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.8})
    _audits(led, live, "only", 20, 10)
    ReleaseGate(led, ReleasePolicy()).tick()
    assert [e["detail"].split(" audited")[0] for e in led.events() if e["kind"] == "alert"] == [f"live v{live}"]


# ---------- stats ----------
@pytest.mark.req("FR-14.1", "FR-7.7")
def test_stats_by_task_has_audit_agreement():
    _text_task("t"), _text_task("u"), _text_task("off"), _text_task("plan", pinned=True)
    led = Ledger(":memory:")
    ev = {
        "threshold": 0.8,
        "tasks": {
            "t": _record("t", 0.7, precision=0.98, coverage=0.6),
            "off": _record("off", SERVE_NOTHING, disabled="regressed"),
        },
    }
    live = _version(led, "live", ev)
    _audits(led, live, "t", 20, 18)
    for task in ("u", "off", "plan"):
        led.log_request(task=task, served_by="teacher", escalated=1)
    by = {r["task"]: r for r in led.stats_by_task()}
    t, u, off, plan = by["t"], by["u"], by["off"], by["plan"]
    assert (t["audits"], t["audit_agreement"]) == (20, 0.9) and (u["audits"], u["audit_agreement"]) == (0, None)
    assert (t["threshold"], t["precision"], t["coverage"], t["calibrated"], t["disabled"]) == (0.7, 0.98, 0.6, True, None)
    assert (u["threshold"], u["precision"], u["coverage"], u["calibrated"]) == (0.8, None, None, None)  # global threshold
    assert (off["threshold"], off["disabled"]) == (SERVE_NOTHING, "regressed")
    assert (plan["pinned"], plan["threshold"], plan["precision"]) == (True, None, None)

    _text_task("t", policy={"audit_rate": 0.5})  # the spec changed since the live version was scored
    assert {r["task"]: r for r in led.stats_by_task()}["t"]["threshold"] == SERVE_NOTHING
    led.set_version(live, "retired", 0)
    assert all(r["threshold"] is None for r in led.stats_by_task())


@pytest.mark.req("FR-14.1", "INV-14")
def test_bom_eval_carries_task_records():
    _text_task("A")
    led = Ledger(":memory:", golden_fraction=0.3)
    _examples(led, train=60, golden=12, task="A")
    student = RecordingProvider("teacher answer", logprob=math.log(0.99))

    def evaluate(uri: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return cascade_eval(lambda p, n, params: student.complete(p, uri, n=n, logprobs=True), rows)

    rep = run_night(led, RecordingTrainer(), RecordingEvaluator(evaluate), ReleaseGate(led), "base", min_new_examples=1)
    assert rep.trained and set(rep.eval["tasks"]) == {"A"}
    bom = led.version_bom(rep.version)
    assert bom is not None and bom["manifest"]["eval"]["tasks"] == rep.eval["tasks"]
    assert rep.eval["spec_hashes"] == {"A": tasks.get("A").spec_hash} == {"A": rep.eval["tasks"]["A"]["spec_hash"]}
    assert [r["task"] for r in dataset.rows(led, "golden")] == ["A"] * 12


# ---------- settings ----------
@pytest.mark.req("FR-14.1")
def test_task_calibration_settings(monkeypatch):
    assert Settings().min_task_golden_rows() == 20 and ReleasePolicy().task_mode == "disable"
    assert ReleasePolicy().min_task_test_n == 10
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ValueError, match="RATCHET_MIN_TASK_GOLDEN must be a whole number, 1 or more"):
            Settings(min_task_golden=bad).min_task_golden_rows()
    monkeypatch.setenv("RATCHET_MIN_TASK_GOLDEN", "many")
    with pytest.raises(ValueError, match="RATCHET_MIN_TASK_GOLDEN must be a whole number"):
        Settings()
    monkeypatch.setenv("RATCHET_MIN_TASK_GOLDEN", "5")
    assert LiveEvaluator(RecordingProvider(RIGHT), min_task_n=Settings().min_task_golden_rows()).min_task_n == 5

    monkeypatch.setenv("RATCHET_TASK_MODE", " Reject ")
    monkeypatch.setenv("RATCHET_MIN_TASK_TEST_N", "30")
    p = ReleasePolicy.from_env()
    assert (p.task_mode, p.min_task_test_n) == ("reject", 30)
    monkeypatch.setenv("RATCHET_TASK_MODE", "")  # set but empty, as in an .env file: the default
    assert ReleasePolicy.from_env().task_mode == "disable"
    monkeypatch.setenv("RATCHET_TASK_MODE", "warn")
    with pytest.raises(ValueError, match=r"ReleasePolicy.task_mode \(RATCHET_TASK_MODE\) must be disable or reject"):
        ReleasePolicy.from_env()
    monkeypatch.setenv("RATCHET_TASK_MODE", "disable")
    monkeypatch.setenv("RATCHET_MIN_TASK_TEST_N", "ten")
    with pytest.raises(ValueError, match="RATCHET_MIN_TASK_TEST_N must be a whole number"):
        ReleasePolicy.from_env()
    with pytest.raises(ValueError, match="must be a string"):
        ReleasePolicy(task_mode=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="RATCHET_MIN_TASK_TEST_N"):
        ReleasePolicy(min_task_test_n=-1)


@pytest.mark.req("FR-14.1")
def test_ledger_merges_a_task_record_atomically():
    led = Ledger(":memory:")
    vid = _version(led, "candidate", {"threshold": 0.8})
    led.merge_task_eval(vid, "A", {"disabled": "x"})
    led.merge_task_eval(vid, "A", {"threshold": SERVE_NOTHING})
    assert led.version(vid).eval == {"threshold": 0.8, "tasks": {"A": {"disabled": "x", "threshold": SERVE_NOTHING}}}
    with pytest.raises(KeyError):
        led.merge_task_eval(999, "A", {})
    assert not led.db.in_transaction  # rolled back, so the next write starts clean
    led.merge_task_eval(vid, "B", {"threshold": 0.9})
    assert set(led.version(vid).eval["tasks"]) == {"A", "B"}


# ---------- the loop ----------
@pytest.mark.req("FR-14.1", "FR-9.5", "US-4")
def test_mixed_workload_calibrates_each_task():
    """Simulation (mock models): tickets and Scout's two distillable calls share one adapter. Every trained version
    carries a record per task, the live version serves each at its own threshold, and the student still takes over."""
    args = parser().parse_args(["--workload", "mixed", "--days", "6", "--requests", "1200", "--drift-day", "0"])
    args.drift_day = None
    res = run(args)
    v1 = res["versions"][0]
    assert set(v1["eval"]["tasks"]) == {"ticket-triage", "source-triage", "page-read"}
    for name, rec in v1["eval"]["tasks"].items():
        assert rec["spec_hash"] == tasks.get(name).spec_hash and rec["test_n"] > 0
    days = res["days"]
    assert days[0]["student_share"] == 0 and days[-1]["student_share"] > 0.8
    assert min(d["audit_agreement"] for d in days if d["audit_agreement"] is not None) >= 0.9
