"""Night School's rules: training targets come from the teacher only, golden rows stay held out, closed teachers are
dropped, nights skip for the documented reasons, every night retrains from the base model on cumulative data, and
evaluation is held out and compares the champion on the same golden set (INV-1 to INV-6)."""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest
from fakes import (
    BASE_MODEL,
    CLOSED_TEACHER,
    OPEN_TEACHER,
    REJECTED_EVAL,
    RecordingEvaluator,
    RecordingProvider,
    RecordingTrainer,
    _audit,
    _examples,
    _repeat,
    _shadow,
    _version,
)

from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.dataset import FREQUENT
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import CANARY_STEPS, ReleaseGate
from ratchet.router import Ratchet

ROOT = Path(__file__).resolve().parents[1]
STUDENT_MARKER = "STUDENT-ONLY"
SHADOW_MARKER = "SHADOW-ONLY"


def _hashes(rows):
    return {r["provenance"]["hash"] for r in rows}


# ---------- INV-2: never train on student outputs ----------
@pytest.mark.req("INV-2", "FR-4.1")
def test_training_targets_are_teacher_answers_only():
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    shadow = _version(led, "shadow", {"threshold": 0.5})
    teacher = RecordingProvider(lambda msgs, model: f"teacher answer to {msgs[-1]['content']}")
    student = RecordingProvider(STUDENT_MARKER)  # unanimous at p≈0.95: confident enough to serve
    settings = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=1.0, confidence_threshold=0.8, redact_pii=True)
    r = Ratchet(settings, led, teacher, lambda v: (student, v.adapter_uri), seed=1)

    # 1) Audits: the student serves every request and the teacher double-checks every one.
    for i in range(200):
        out = r.handle([{"role": "user", "content": f"audited {i}"}], "default")
        assert out.served_by == "student" and out.text == STUDENT_MARKER  # the marker really reached users
    counts = led.db.execute(
        "SELECT SUM(audited), SUM(shadow_version=?), SUM(audit_agree) FROM requests WHERE student_version=?", (shadow, live)
    ).fetchone()
    assert tuple(counts) == (200, 200, 0)

    # 2) Arbitration and escalation, without audits. A shadow that disagrees with the served answer makes the teacher
    # arbitrate; an answer the task cannot parse ('ticket-triage' wants JSON) escalates to the teacher.
    shadow_student = RecordingProvider(SHADOW_MARKER)
    no_audit = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, confidence_threshold=0.8, redact_pii=True)
    resolve = {live: student, shadow: shadow_student}
    r2 = Ratchet(no_audit, led, teacher, lambda v: (resolve[v.id], v.adapter_uri), seed=2)
    served = {"student": 0, "teacher": 0}
    for i in range(200):
        out = r2.handle([{"role": "user", "content": f"unaudited {i}"}], "default" if i % 2 else "ticket-triage")
        served[out.served_by] += 1
    assert served == {"student": 100, "teacher": 100}
    assert len(teacher.calls) == 400  # one teacher answer per request: audit, arbitration or escalation

    examples = led.examples()
    assert len(examples) == 400
    for ex in examples:
        blob = ex["messages"] + ex["completion"]
        assert STUDENT_MARKER not in blob and SHADOW_MARKER not in blob
        assert ex["completion"] == teacher.answer(json.loads(ex["messages"]), OPEN_TEACHER)
        assert ex["teacher_model"] == OPEN_TEACHER

    rows = dataset.rows(led, "train") + dataset.rows(led, "golden")
    assert len(rows) == 400
    for row in rows:
        prompt, target = row["messages"][:-1], row["messages"][-1]
        assert target["role"] == "assistant" and target["content"] == teacher.answer(prompt, OPEN_TEACHER)
        assert STUDENT_MARKER not in json.dumps(row) and SHADOW_MARKER not in json.dumps(row)


# Every place under ratchet/ that may store a training example. Change this set only in a reviewed edit: a new caller
# must store teacher answers only (INV-2) and never a pinned task (INV-10). The set must match the code exactly, so
# moving a call site means replacing its old entry, not adding the new one beside it. For example, when the
# answer-hygiene item moves storing into Ratchet._store_example (called from _call_teacher), the entry
# "ratchet.router:Ratchet._call_teacher" becomes "ratchet.router:Ratchet._store_example".
ALLOWED_ADD_EXAMPLE_CALLERS = {"ratchet.router:Ratchet._call_teacher"}


class _AddExampleSites(ast.NodeVisitor):
    """Every use of `add_example` in a module: a call, a bound method stored or passed on, or the name as a string
    (`getattr(ledger, "add_example")`). Prose that merely mentions it is not a use."""

    def __init__(self, module: str):
        self.module = module
        self.scope: list[str] = []
        self.found: list[tuple[str, int]] = []

    def _use(self, node: ast.expr) -> None:
        self.found.append((f"{self.module}:{'.'.join(self.scope) or '<module>'}", node.lineno))

    def _enter(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter  # a lambda counts as its enclosing function

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "add_example":
            self._use(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "add_example":
            self._use(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value == "add_example":
            self._use(node)


def _add_example_sites(package: Path) -> list[tuple[str, int]]:
    """(module:qualified function, line) for every use of `add_example` in a package directory."""
    found = []
    for path in sorted(package.rglob("*.py")):
        parts = path.relative_to(package.parent).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        visitor = _AddExampleSites(module)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        found += visitor.found
    return found


@pytest.mark.req("INV-2", "INV-10")
def test_add_example_called_only_from_allowed_callers():
    sites = _add_example_sites(ROOT / "ratchet")
    stray = sorted(f"{caller} (line {line})" for caller, line in sites if caller not in ALLOWED_ADD_EXAMPLE_CALLERS)
    assert not stray, (
        f"add_example is used outside {sorted(ALLOWED_ADD_EXAMPLE_CALLERS)}: {stray}. Training examples are teacher "
        "answers only (INV-2) and never pinned tasks (INV-10). If the new caller keeps both rules, add it to "
        "ALLOWED_ADD_EXAMPLE_CALLERS in a reviewed change."
    )
    stale = sorted(ALLOWED_ADD_EXAMPLE_CALLERS - {caller for caller, _ in sites})
    assert not stale, f"{stale} no longer use add_example: if the call moved, replace the entry instead of adding beside it"


def test_add_example_guard_sees_indirect_uses(tmp_path):
    """Checks the scanner behind the INV-2 guard, not the invariant itself, so it carries no requirement marker."""
    pkg = tmp_path / "ratchet"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "sub" / "store.py").write_text(
        textwrap.dedent(
            '''
            class Store:
                def direct(self):
                    self.ledger.add_example("t", [], "a", "m", "l", "u")

                def nested(self):
                    def inner():
                        return getattr(self.ledger, "add_example")

                    return inner


            save = lambda led: led.add_example


            def prose():
                """Mentions add_example and Ledger.add_example in a docstring, which is not a use."""
            '''
        )
    )
    callers = sorted(caller for caller, _ in _add_example_sites(pkg))
    assert callers == ["ratchet.sub.store:<module>", "ratchet.sub.store:Store.direct", "ratchet.sub.store:Store.nested.inner"]


# ---------- INV-3: golden rows are held out, except frequent requests ----------
@pytest.mark.req("INV-3", "FR-4.3")
def test_golden_rows_never_reach_the_trainer_except_frequent():
    led = Ledger(":memory:", golden_fraction=0.3)
    stored = _examples(led, train=60, golden=12)
    golden = [e for e in stored if e.split == "golden"]
    _repeat(led, golden[0], FREQUENT - 1)  # seen exactly FREQUENT times: moves to training
    _repeat(led, golden[1], FREQUENT - 2)  # seen FREQUENT - 1 times: stays held out
    _repeat(led, golden[2], FREQUENT + 4)
    frequent = {golden[0].hash, golden[2].hash}

    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL)
    assert rep.trained and len(trainer.calls) == 1 and len(evaluator.calls) == 1

    trained, scored = _hashes(trainer.calls[0].rows), _hashes(evaluator.calls[0].golden_rows)
    assert trained == {e.hash for e in stored if e.split == "train"} | frequent
    assert scored == {e.hash for e in golden} - frequent
    assert not trained & scored
    assert golden[1].hash in scored
    # The move happens when the dataset is built; the stored split never changes.
    assert {r["hash"] for r in led.examples(split="golden")} == {e.hash for e in golden}
    assert led.version(rep.version).train_examples == 62


# ---------- INV-1: closed teachers never reach training ----------
@pytest.mark.req("INV-1")
def test_closed_teacher_rows_are_dropped_at_dataset_build(tmp_path):
    led = Ledger(":memory:", golden_fraction=0.3)
    open_rows = _examples(led, train=60, golden=8)
    # Rows from a closed or unregistered teacher can only come from an old or hand-edited database (the router refuses
    # such teachers at startup); the dataset build is the second line of defence.
    _examples(led, train=20, golden=5, teacher=CLOSED_TEACHER, prefix="closed")
    _examples(led, train=20, golden=5, teacher="someone/unknown-model", prefix="unknown")
    assert len(led.examples()) == 118

    rows = dataset.rows(led, "train") + dataset.rows(led, "golden")
    assert {r["provenance"]["teacher"] for r in rows} == {OPEN_TEACHER}
    assert _hashes(rows) == {e.hash for e in open_rows}

    manifest = dataset.build(led, tmp_path)
    assert (manifest["train_examples"], manifest["golden_examples"]) == (60, 8)
    for name in ("train.jsonl", "golden.jsonl"):
        lines = (tmp_path / name).read_text(encoding="utf-8").splitlines()
        assert lines and all(json.loads(line)["provenance"]["teacher"] == OPEN_TEACHER for line in lines)

    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL)
    assert rep.trained
    assert {r["provenance"]["teacher"] for r in trainer.calls[0].rows} == {OPEN_TEACHER}
    assert {r["provenance"]["teacher"] for r in evaluator.calls[0].golden_rows} == {OPEN_TEACHER}
    sources = led.version_bom(rep.version)["manifest"]["sources"]
    assert [(s["teacher"], s["distill_ok"], s["examples"]) for s in sources] == [(OPEN_TEACHER, True, 60)]


# ---------- FR-4.4, INV-5: when a night skips ----------
@pytest.mark.req("FR-4.4", "INV-5")
@pytest.mark.parametrize(
    ("case", "reason", "detail"),
    [
        ("fewer than 50 new examples", "not enough new data", "only 49 new examples (< 50)"),
        ("fewer than 50 since the last version", "not enough new data", "only 40 new examples (< 50)"),
        ("no golden set", "no golden set", "no golden examples yet"),
        ("shadow in flight", "release in flight", "v2 still in shadow"),
        ("canary in flight", "release in flight", "v1 still in canary"),
    ],
)
def test_night_skips_with_reason(case, reason, detail):
    led = Ledger(":memory:", golden_fraction=0.0 if case == "no golden set" else 0.3)
    if case == "fewer than 50 new examples":
        _examples(led, train=49, golden=10)
    elif case == "fewer than 50 since the last version":
        _version(led, "live", {"precision": 0.97, "coverage": 0.8}, train_examples=40)
        _examples(led, train=80, golden=10)
    elif case == "no golden set":
        _examples(led, train=80)
    elif case == "shadow in flight":
        _version(led, "live", {"precision": 0.97, "coverage": 0.8})
        _version(led, "shadow", {"precision": 0.97, "coverage": 0.8})
        _examples(led, train=200, golden=20)  # plenty of data: the release alone stops training
    else:
        _version(led, "canary", {"precision": 0.97, "coverage": 0.8})  # cold start: the first student is in canary
        _examples(led, train=200, golden=20)
    before = [(v.id, v.state, v.eval) for v in led.versions()]

    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL)

    assert (rep.trained, rep.version, rep.admitted, rep.reason) == (False, None, False, reason)
    assert trainer.calls == [] and evaluator.calls == []  # no GPU time, not even to re-score the champion
    assert [(v.id, v.state, v.eval) for v in led.versions()] == before
    events = led.events()
    assert [e["kind"] for e in events] == ["skip"] and detail in events[0]["detail"]


# ---------- FR-4.2: retrain from the base model on cumulative data ----------
def _release(led: Ledger, gate: ReleaseGate, vid: int) -> None:
    """Take an admitted candidate all the way to live through the gate, on clean shadow and canary evidence."""
    p = gate.policy
    if led.version(vid).state == "shadow":
        _shadow(led, vid, p.min_shadow, live_vid=led.live().id)
        gate.tick()
    for _ in CANARY_STEPS:  # 10% -> 50% -> live, each stage on its own audits
        assert led.version(vid).state == "canary"
        _audit(led, vid, p.min_audits)
        gate.tick()
    assert led.live().id == vid


@pytest.mark.req("FR-4.2")
def test_retrain_from_base_on_cumulative_data():
    led = Ledger(":memory:", golden_fraction=0.3)
    # Every candidate passes and goes live, so nights 2 and 3 train while the previous night's adapter is the champion:
    # the moment a trainer could stack the new adapter on it instead of starting from the base model.
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator({"precision": 0.97, "coverage": 0.60, "threshold": 0.5})
    gate = ReleaseGate(led)
    live_when_trained = []
    for night, new in enumerate((50, 50, 75), start=1):  # night 1 has exactly min_new_examples
        led.day = night
        _examples(led, train=new, golden=5, prefix=f"night {night}")
        live = led.live()
        live_when_trained.append(live.adapter_uri if live else None)
        rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
        assert rep.trained and rep.admitted, rep.reason
        _release(led, gate, rep.version)

    assert live_when_trained == [None, "student-v1", "student-v2"]
    assert [c.adapter_uri for c in evaluator.calls] == ["student-v1", "student-v1", "student-v2", "student-v2", "student-v3"]
    assert [c.base_model for c in trainer.calls] == [BASE_MODEL] * 3  # never the champion's adapter
    assert [v.base_model for v in led.versions()] == [BASE_MODEL] * 3
    assert [c.name for c in trainer.calls] == ["student-v1", "student-v2", "student-v3"]
    assert [(v.adapter_uri, v.state) for v in led.versions()] == [
        ("student-v1", "retired"),
        ("student-v2", "retired"),
        ("student-v3", "live"),
    ]
    sizes = [len(c.rows) for c in trainer.calls]
    assert sizes == [50, 100, 175] and sizes == sorted(sizes)
    assert sizes == [v.train_examples for v in led.versions()]
    nights = [_hashes(c.rows) for c in trainer.calls]
    assert nights[0] < nights[1] < nights[2]  # every night replays everything earlier nights saw


# ---------- INV-6: precision is measured on held-out rows ----------
RIGHT = json.dumps({"category": "billing", "priority": "high"})
WRONG = json.dumps({"category": "refund", "priority": "low"})


def _golden(n):
    return [
        {
            "task": "ticket-triage",
            "messages": [{"role": "user", "content": f"ticket {i}"}, {"role": "assistant", "content": RIGHT}],
        }
        for i in range(n)
    ]


@pytest.mark.req("INV-6", "FR-5.2", "FR-5.3")
def test_cascade_eval_measures_on_held_out_half():
    n = 20
    # Every answer is unanimous at the same token probability, so all have the same confidence: no threshold can
    # separate right from wrong, and only the choice of rows decides the reported precision.
    right_on_even = RecordingProvider(lambda msgs, model: RIGHT if int(msgs[-1]["content"].split()[-1]) % 2 == 0 else WRONG)

    def sample(prompt, k):
        return right_on_even.complete(prompt, "student-v1", n=k, temperature=0.7, logprobs=True)

    res = cascade_eval(sample, _golden(n), samples=3, target_precision=0.97)
    assert {"golden_n", "golden_agreement", "threshold", "coverage", "precision", "test_n"} <= set(res)
    assert (res["golden_n"], res["test_n"]) == (n, n // 2)
    assert res["golden_agreement"] == 0.5
    assert 0.5 <= res["threshold"] <= 1.0  # calibrated on the even rows, which are all right: it serves
    assert (res["precision"], res["coverage"]) == (0.0, 1.0)  # graded on the odd rows only, which are all wrong
    assert [c.kwargs["n"] for c in right_on_even.calls] == [3] * n

    # Mirror image: calibrated on wrong answers, it serves nothing (threshold 1.01) instead of guessing.
    right_on_odd = RecordingProvider(lambda msgs, model: WRONG if int(msgs[-1]["content"].split()[-1]) % 2 == 0 else RIGHT)
    res = cascade_eval(lambda p, k: right_on_odd.complete(p, "m", n=k, logprobs=True), _golden(n), samples=3)
    assert (res["threshold"], res["coverage"], res["precision"], res["golden_agreement"]) == (1.01, 0.0, 0.0, 0.5)


# ---------- INV-4: champion vs challenger on the same golden set ----------
@pytest.mark.req("INV-4", "FR-5.4", "FR-4.2")
def test_champion_rescored_on_same_golden_set():
    led = Ledger(":memory:", golden_fraction=0.3)
    # The live version's stored eval is from an older, easier golden set. Against it the candidate below would be
    # rejected as a regression (0.97 < 0.99 - 0.02).
    live = _version(led, "live", {"precision": 0.99, "coverage": 0.99, "threshold": 0.9}, train_examples=0)
    _examples(led, train=60, golden=10)
    led.day = 7
    today = {"student-v1": {"precision": 0.96, "coverage": 0.50}, "student-v2": {"precision": 0.97, "coverage": 0.60}}
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(lambda uri, rows: today[uri])

    rep = run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL)

    assert [c.adapter_uri for c in evaluator.calls] == ["student-v1", "student-v2"]  # the champion is scored first
    champion_rows, challenger_rows = evaluator.calls[0].golden_rows, evaluator.calls[1].golden_rows
    assert champion_rows == challenger_rows == dataset.rows(led, "golden") and len(champion_rows) == 10
    rechecked = led.version(live).eval
    assert {k: rechecked[k] for k in ("precision", "coverage", "rechecked_day")} == {
        "precision": 0.96,
        "coverage": 0.50,
        "rechecked_day": led.day,
    }
    assert led.day == 7
    assert rep.admitted and led.version(rep.version).state == "shadow"
    assert led.live().id == live
    # The challenger starts from the base model, not from the champion it is compared with (FR-4.2).
    assert [c.base_model for c in trainer.calls] == [BASE_MODEL]
    assert led.version(rep.version).base_model == BASE_MODEL
