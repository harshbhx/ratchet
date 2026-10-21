"""Night School's rules: training targets come from the teacher only, golden rows stay held out, closed teachers are
dropped, nights skip for the documented reasons, every night retrains from the base model on cumulative data, and
evaluation is held out and compares the champion on the same golden set (INV-1 to INV-6)."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import platform
import re
import shlex
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

from ratchet import bom
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset, train
from ratchet.nightschool.dataset import FREQUENT
from ratchet.nightschool.evaluate import LiveEvaluator, cascade_eval
from ratchet.nightschool.orchestrate import run_night
from ratchet.providers import GenParams, UpstreamError
from ratchet.release import CANARY_STEPS, ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet
from sim import mock_models
from sim.world import make_stream

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
    # Valid for both tasks used below ('ticket-triage' wants JSON with a known category and priority), so every teacher
    # answer can be stored (INV-16).
    teacher = RecordingProvider(
        lambda msgs, model: json.dumps({"category": "billing", "priority": "high", "reply": f"to {msgs[-1]['content']}"})
    )
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
# moving a call site means replacing its old entry, not adding the new one beside it (as when storing moved from
# Ratchet._call_teacher into Ratchet._store_example, which _call_teacher calls and which also enforces INV-16).
ALLOWED_ADD_EXAMPLE_CALLERS = {"ratchet.router:Ratchet._store_example", "ratchet.feedback:store_label"}


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


@pytest.mark.req("INV-3", "INV-6", "FR-4.3", "FR-1.13")
def test_every_variant_of_a_prompt_shares_its_split_and_count():
    """The trainer sees only the messages, so a prompt stored under other conditions (a stop, JSON mode) must land in the
    same split as the bare one: otherwise a golden prompt and its answer would be trained on (INV-3) and inflate the
    held-out precision (INV-6). The split comes from the hash without conditions, so a bare example keeps its v0.4 split,
    and the frequent rule counts every variant of one request together."""
    conditions = ({"stop": ["\n\n"]}, {"response_format": {"type": "json_object"}}, {"stop": ["END"], "tool_choice": "none"})
    led = Ledger(":memory:", golden_fraction=0.3)
    for i in range(200):
        msgs = [{"role": "user", "content": f"What is the capital of France? #{i}"}]
        led.add_example("default", msgs, "Paris.", OPEN_TEACHER, "l", "u")
        for params in conditions:
            led.add_example("default", msgs, "Paris.", OPEN_TEACHER, "l", "u", params=params)
    by_prompt: dict[str, set[str]] = {}
    for r in led.examples():
        by_prompt.setdefault(r["messages"], set()).add(r["split"])
    assert len(by_prompt) == 200 and len(led.examples()) == 800  # four examples per prompt
    assert all(len(splits) == 1 for splits in by_prompt.values())
    assert {next(iter(s)) for s in by_prompt.values()} == {"golden", "train"}
    bare = Ledger(":memory:", golden_fraction=0.3)  # the bare example's split is the one it had alone (v0.4)
    for i in range(200):
        bare.add_example(
            "default", [{"role": "user", "content": f"What is the capital of France? #{i}"}], "Paris.", OPEN_TEACHER, "l", "u"
        )
    assert {r["hash"]: r["split"] for r in bare.examples()} == {r["hash"]: r["split"] for r in led.examples() if not r["params"]}

    # The same request seen once bare and twice under a format is seen FREQUENT times: every variant moves to training.
    led = Ledger(":memory:", golden_fraction=1.0)
    msgs = [{"role": "user", "content": "Is my order late?"}]
    led.add_example("default", msgs, "No.", OPEN_TEACHER, "l", "u")
    json_mode = {"response_format": {"type": "json_object"}}
    led.add_example("default", msgs, '{"late": false}', OPEN_TEACHER, "l", "u", params=json_mode)
    assert len(dataset.rows(led, "golden")) == 2 and dataset.rows(led, "train") == []
    led.add_example("default", msgs, '{"late": false}', OPEN_TEACHER, "l", "u", params=json_mode)
    assert dataset.rows(led, "golden") == [] and len(dataset.rows(led, "train")) == 2


@pytest.mark.req("INV-3", "FR-4.1", "FR-5.1")
def test_examples_cut_at_a_stop_are_held_out_of_training():
    """train.py renders the messages only, so an answer the client's stop ended early would teach the student to stop
    there on the bare prompt: such a row is left out of training. As a golden row it still serves the eval, which
    samples the student under the same stop."""
    led = Ledger(":memory:", golden_fraction=0.0)
    for i in range(3):
        msgs = [{"role": "user", "content": f"List three colours #{i}"}]
        led.add_example("default", msgs, "Red, green, blue.", OPEN_TEACHER, "l", "u")
        led.add_example("default", msgs, "Red", OPEN_TEACHER, "l", "u", params={"stop": [","]})
        led.add_example("default", msgs, '{"c": 1}', OPEN_TEACHER, "l", "u", params={"response_format": {"type": "json_object"}})
    train = dataset.rows(led, "train")
    assert len(train) == 6 and all(not (r["params"] or {}).get("stop") for r in train)
    golden = Ledger(":memory:", golden_fraction=1.0)
    golden.add_example(
        "default", [{"role": "user", "content": "List three colours"}], "Red", OPEN_TEACHER, "l", "u", params={"stop": [","]}
    )
    assert [r["params"] for r in dataset.rows(golden, "golden")] == [{"stop": [","]}]


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

    def sample(prompt, k, params=None):
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
    res = cascade_eval(lambda p, k, params: right_on_odd.complete(p, "m", n=k, logprobs=True), _golden(n), samples=3)
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


# ---------- FR-5.1: the golden eval samples under the conditions stored with each example ----------
@pytest.mark.req("FR-5.1", "FR-1.13", "INV-6")
def test_cascade_eval_passes_row_params_to_sample():
    rows = _golden(4)
    rows[1]["params"] = {"stop": ["\n\n"]}
    rows[2]["params"] = {"response_format": {"type": "json_object"}}
    seen = []

    def sample(prompt, k, params):
        seen.append((prompt[-1]["content"], k, params))
        return RecordingProvider(RIGHT).complete(prompt, "m", n=k, logprobs=True)

    cascade_eval(sample, rows, samples=3)
    assert seen == [
        ("ticket 0", 3, None),  # a row from before v0.5, or stored without conditions
        ("ticket 1", 3, {"stop": ["\n\n"]}),
        ("ticket 2", 3, {"response_format": {"type": "json_object"}}),
        ("ticket 3", 3, None),
    ]


@pytest.mark.req("FR-5.1", "INV-7", "INV-6")
def test_cascade_eval_scores_a_refused_row_as_not_served():
    """A row the student endpoint refuses for what it asks (400, 413, 422: a stored condition it cannot serve, a prompt
    over its context) escalates in production, so the eval scores it as confidence 0 and wrong and goes on. Any other
    failure (an auth error, a missing adapter, an outage) refuses every row and still stops the eval."""
    rows = _golden(6)
    for i in (1, 2, 4):
        rows[i]["params"] = {"tools": [{"type": "function", "function": {"name": "lookup"}}]}
    student = RecordingProvider(RIGHT)

    def sample(prompt, k, params):
        if params:
            raise UpstreamError(400, '"auto" tool choice requires --enable-auto-tool-choice')
        return student.complete(prompt, "m", n=k, logprobs=True)

    res = cascade_eval(sample, rows, samples=3)
    assert (res["golden_n"], res["refused_n"], res["golden_agreement"]) == (6, 3, 0.5)
    assert (res["test_n"], res["coverage"], res["precision"]) == (3, 2 / 3, 1.0)  # odd rows 1, 3, 5: row 1 is refused
    assert cascade_eval(lambda p, k, params: student.complete(p, "m", n=k, logprobs=True), rows)["refused_n"] == 0
    for status in (401, 404, 429, 503):

        def failing(prompt, k, params, status=status):
            raise UpstreamError(status, "no")

        with pytest.raises(UpstreamError):
            cascade_eval(failing, rows, samples=3)


@pytest.mark.req("FR-5.1", "FR-1.13", "FR-4.1")
def test_golden_eval_runs_under_production_conditions(monkeypatch):
    """An example stored with conditions comes back from `dataset.rows` with them, and both evaluators send the student
    exactly what the router sends for that request: the conditions, under the same GenPolicy and its cap."""
    led = Ledger(":memory:", golden_fraction=1.0)  # every example golden
    conditions = {"stop": ["\n\n"], "response_format": {"type": "json_object"}}
    item = make_stream("tickets", seed=7).sample(1)  # a request the mock world knows
    led.add_example(item.task, item.messages(), RIGHT, OPEN_TEACHER, "l", "u", params=conditions)
    led.add_example(item.task, [{"role": "user", "content": "ticket 2"}], RIGHT, OPEN_TEACHER, "l", "u")
    rows = dataset.rows(led, "golden")
    assert [r["params"] for r in rows] == [conditions, None]

    settings = Settings(
        teacher_model=OPEN_TEACHER,
        samples=3,
        audit_rate=0.0,
        student_params="",
        teacher_params="",
        student_max_tokens=256,
        teacher_max_tokens=8192,
        teacher_reasoning_headroom=0,
    )
    served = Ledger(":memory:")
    _version(served, "live", {"threshold": 0.5})
    router_student = RecordingProvider(RIGHT)
    router = Ratchet(settings, served, RecordingProvider(RIGHT), lambda v: (router_student, v.adapter_uri))
    for row in rows:  # the same requests in production, the client sending those conditions
        router.handle(row["messages"][:-1], row["task"], params=GenParams.from_stored(row["params"]))
    production = [c.kwargs for c in router_student.calls]
    assert [k["params"] for k in production] == [
        GenParams(max_tokens=256, stop=("\n\n",), response_format={"type": "json_object"}),
        GenParams(max_tokens=256),
    ]

    live_student = RecordingProvider(RIGHT)
    LiveEvaluator(live_student, None, samples=3, policy=settings.gen_policy()).evaluate("student-v1", rows)
    assert [c.kwargs for c in live_student.calls] == production

    seen = []
    real = mock_models.MockStudent.complete

    def spy(self, messages, model, *args, **kwargs):
        seen.append(kwargs)
        return real(self, messages, model, *args, **kwargs)

    monkeypatch.setattr(mock_models.MockStudent, "complete", spy)
    fleet = mock_models.MockFleet(policy=settings.gen_policy())
    fleet.train(rows[:1], BASE_MODEL, "student-v1")
    fleet.evaluate("student-v1", rows[:1])
    assert seen == production[:1]


# ---------- certified precision (FR-16.3, ADR 0026) ----------
import math  # noqa: E402
import random  # noqa: E402

from ratchet import certificate, tasks  # noqa: E402
from ratchet.confidence import calibrate_threshold, score  # noqa: E402
from ratchet.stats import CERT_GRID, SERVE_NOTHING, cp_lower  # noqa: E402

DEFAULT_RIGHT, DEFAULT_WRONG = "the answer is 4", "the answer is 5"


def _mixed_golden(n: int) -> list[dict]:
    """`n` golden rows, in blocks of four alternating between two tasks, so both halves hold both."""
    rows = []
    for i in range(n):
        task = "ticket-triage" if (i // 4) % 2 == 0 else "default"
        ref = RIGHT if task == "ticket-triage" else DEFAULT_RIGHT
        rows.append(
            {"task": task, "messages": [{"role": "user", "content": f"ticket {i}"}, {"role": "assistant", "content": ref}]}
        )
    return rows


def _scripted(plan: dict[int, tuple[float, bool]]):
    """A sampler whose three samples for row i are unanimous at token probability plan[i][0] (confidence 0.4 p + 0.6),
    right or wrong as plan[i][1] says."""

    def sample(prompt, k, params=None):
        i = int(prompt[-1]["content"].split()[-1])
        p, right = plan[i]
        triage = (i // 4) % 2 == 0
        text = (RIGHT if right else WRONG) if triage else (DEFAULT_RIGHT if right else DEFAULT_WRONG)
        return RecordingProvider(text, logprob=math.log(p)).complete(prompt, "m", n=k, temperature=0.7, logprobs=True)

    return sample


@pytest.mark.req("FR-16.3", "INV-6", "FR-5.2")
def test_cascade_eval_certifies_on_the_held_out_half():
    rows = _mixed_golden(80)
    rng = random.Random(5)
    plan = {}
    for i in range(80):
        p = rng.uniform(0.3, 1.0)
        plan[i] = (p, rng.random() < p)
    sample = _scripted(plan)
    res = cascade_eval(sample, rows, samples=3, target_precision=0.97)

    # The points, as the eval sees them: even rows calibrate, odd rows measure.
    points = []
    for row in rows:
        task = tasks.get(row["task"])
        sc = score(task, sample(row["messages"][:-1], 3))
        points.append((sc.confidence, task.agree(sc.answer.text, row["messages"][-1]["content"])))
    calib, test = points[0::2], points[1::2]
    before = min(SERVE_NOTHING, max(0.5, calibrate_threshold(calib, 0.97)))  # the pre-change rule, unchanged
    assert res["threshold"] == before
    served = [ok for c, ok in test if c >= before]
    assert (res["coverage"], res["precision"]) == (len(served) / len(test), sum(served) / len(served))
    assert (res["certificate"]["precision"], res["certificate"]["served"]) == (res["precision"], len(served))

    cert = res["certificate"]
    thr = res["threshold"]
    want_served = sum(c >= thr for c, _ in test)
    want_errors = sum(c >= thr and not ok for c, ok in test)
    assert (cert["served"], cert["errors"], cert["threshold"]) == (want_served, want_errors, thr)
    assert (cert["method"], cert["calibration"], cert["target"], cert["delta"]) == ("clopper-pearson", "point", 0.95, 0.05)
    assert cert["lcb"] == cp_lower(want_served - want_errors, want_served, 0.05) <= cert["precision"]
    assert cert["certified"] is (cert["lcb"] >= 0.95) and "ltt_trace" not in cert
    assert len(res["test_vector"]) == res["test_n"] == 40
    assert res["test_vector"] == certificate.outcome_vector(test, thr)
    by_threshold = sorted(res["frontier"], key=lambda r: r["threshold"])
    assert [r["threshold"] for r in by_threshold] == sorted(CERT_GRID)
    assert all(a["coverage"] >= b["coverage"] for a, b in zip(by_threshold, by_threshold[1:], strict=False))
    assert all(r["lcb"] <= r["precision"] and r["delta"] == 0.005 for r in res["frontier"])
    per = res["per_task"]
    assert set(per) == {"ticket-triage", "default"}
    assert sum(t["n_test"] for t in per.values()) == 40
    assert (sum(t["served"] for t in per.values()), sum(t["errors"] for t in per.values())) == (want_served, want_errors)
    assert certificate.recheck(json.loads(json.dumps(res)))[1] == []

    # Other certificate settings change the certificate only, never the threshold or the v0.4 keys.
    strict = cascade_eval(sample, rows, samples=3, cert_target=0.99, cert_delta=0.01)
    v04 = ("golden_n", "golden_agreement", "threshold", "coverage", "precision", "test_n", "refused_n", "golden_invalid_refs")
    assert {k: strict[k] for k in v04} == {k: res[k] for k in v04}
    assert (strict["certificate"]["target"], strict["certificate"]["delta"]) == (0.99, 0.01)
    assert strict["certificate"]["lcb"] <= cert["lcb"]


@pytest.mark.req("FR-16.3", "INV-6")
def test_cascade_eval_ltt_picks_a_grid_threshold():
    sure = {i: (0.95, True) for i in range(240)}  # confidence 0.98, always right
    res = cascade_eval(_scripted(sure), _mixed_golden(240), samples=3, calibration="ltt")
    cert = res["certificate"]
    assert res["threshold"] == 0.5 and cert["calibration"] == "ltt"  # every grid threshold is certified on 120 rows
    assert [t["threshold"] for t in cert["ltt_trace"]] == sorted(CERT_GRID, reverse=True)
    assert all(t["n"] == t["k"] == 120 and t["p_value"] <= 0.05 for t in cert["ltt_trace"])
    assert (cert["served"], cert["errors"], cert["certified"]) == (120, 0, True)
    assert cert["lcb"] == pytest.approx(0.05 ** (1 / 120))

    # Ten calibration rows cannot certify 95% even when all are right (0.95^10 = 0.60 > 0.05): nothing is served.
    few = cascade_eval(_scripted(sure), _mixed_golden(20), samples=3, calibration="ltt")
    assert (few["threshold"], few["coverage"], few["certificate"]["served"], few["certificate"]["certified"]) == (
        SERVE_NOTHING,
        0.0,
        0,
        False,
    )
    assert len(few["certificate"]["ltt_trace"]) == 1

    # Confident rows right, unsure rows wrong: Learn-then-Test stops at the first threshold it cannot certify.
    split = {i: ((0.95, True) if i % 3 else (0.5, False)) for i in range(240)}  # confidence 0.98 or 0.80
    mixed = cascade_eval(_scripted(split), _mixed_golden(240), samples=3, calibration="ltt", cert_target=0.9)
    assert mixed["threshold"] in CERT_GRID and mixed["threshold"] > 0.80
    assert mixed["certificate"]["errors"] == 0 and mixed["certificate"]["certified"]
    assert certificate.recheck(json.loads(json.dumps(mixed)))[1] == []


def test_cascade_eval_refuses_bad_certification_settings():
    rows, sample = _mixed_golden(4), _scripted({i: (0.9, True) for i in range(4)})
    for kwargs, says in (
        ({"calibration": "magic"}, "calibration must be point or ltt"),
        ({"cert_target": 1.0}, "cert_target must be"),
        ({"cert_delta": 0}, "cert_delta must be"),
        ({"cert_delta": True}, "cert_delta must be"),
    ):
        with pytest.raises(ValueError, match=says):
            cascade_eval(sample, rows, **kwargs)


def _vector_eval(vec: str | None, **extra) -> dict:
    """A golden result the gate admits, with `vec` as its test vector (None: an evaluator that writes none)."""
    ev = {"precision": 0.97, "coverage": 0.9, "threshold": 0.8, "golden_agreement": 0.9, "golden_n": 20, **extra}
    return ev if vec is None else {**ev, "test_n": len(vec), "test_vector": vec}


@pytest.mark.req("FR-16.3", "FR-6.30", "INV-4", "INV-14")
def test_run_night_pairs_the_candidate_with_the_rescored_live_version():
    led = Ledger(":memory:", golden_fraction=0.3)
    live = _version(led, "live", {"precision": 0.97, "coverage": 0.9, "threshold": 0.8}, train_examples=0)
    _examples(led, train=60, golden=10)
    vectors = {"student-v1": "SSSSWEEESS", "student-v2": "SSSEESWESS"}
    evaluator = RecordingEvaluator(lambda uri, rows: _vector_eval(vectors[uri]))
    rep = run_night(led, RecordingTrainer(), evaluator, ReleaseGate(led), BASE_MODEL)
    want = {**certificate.paired(vectors["student-v1"], vectors["student-v2"]), "against": live}
    assert rep.eval["paired"] == want and led.version(rep.version).eval["paired"] == want
    assert led.version_bom(rep.version)["manifest"]["eval"]["paired"] == want  # signed with the BOM
    assert "paired" not in led.version(live).eval  # the champion's re-score carries no comparison

    # No comparison when the vectors differ in length, when an eval has none, or with no live version.
    for live_vec, cand_vec in (("SSSS", "SSS"), (None, "SSS"), ("", "")):
        led2 = Ledger(":memory:", golden_fraction=0.3)
        _version(led2, "live", {"precision": 0.97, "coverage": 0.9}, train_examples=0)
        _examples(led2, train=60, golden=10)
        evs = {"student-v1": live_vec, "student-v2": cand_vec}
        ev = RecordingEvaluator(lambda uri, rows, evs=evs: _vector_eval(evs[uri]))
        rep2 = run_night(led2, RecordingTrainer(), ev, ReleaseGate(led2), BASE_MODEL)
        assert rep2.trained and "paired" not in rep2.eval
    led3 = Ledger(":memory:", golden_fraction=0.3)
    _examples(led3, train=60, golden=10)
    rep3 = run_night(
        led3, RecordingTrainer(), RecordingEvaluator(lambda uri, rows: _vector_eval("SSS")), ReleaseGate(led3), BASE_MODEL
    )
    assert "paired" not in rep3.eval and led3.version(rep3.version).state == "canary"


@pytest.mark.req("FR-6.30", "FR-16.3", "INV-4")
def test_paired_gate_admits_what_the_unpaired_rule_rejects_on_noise():
    """The reference simulation's v2 (simulation, mock models): it serves 70 of 74 held-out rows that live v1 serves all
    of, every served answer right. The unpaired rule rejects it (coverage 94.6% vs 100%, beyond the 2-point tolerance);
    on the same rows that is 4 discordant pairs, McNemar p = 0.0625, not significant at 5%, so the paired gate admits."""
    from ratchet.release import ReleasePolicy

    outcomes = []
    for paired_gate in (0, 1):
        led = Ledger(":memory:", golden_fraction=0.3)
        _version(led, "live", {"precision": 1.0, "coverage": 1.0}, train_examples=0)
        _examples(led, train=60, golden=10)
        vectors = {"student-v1": "S" * 74, "student-v2": "S" * 70 + "E" * 4}
        evals = {
            "student-v1": _vector_eval(vectors["student-v1"], precision=1.0, coverage=1.0),
            "student-v2": _vector_eval(vectors["student-v2"], precision=1.0, coverage=70 / 74),
        }
        gate = ReleaseGate(led, ReleasePolicy(paired_gate=paired_gate))
        rep = run_night(led, RecordingTrainer(), RecordingEvaluator(lambda uri, rows, evals=evals: evals[uri]), gate, BASE_MODEL)
        outcomes.append((rep.admitted, led.events()[-1]["detail"]))
    (unpaired_ok, unpaired_why), (paired_ok, paired_why) = outcomes
    assert not unpaired_ok and "regresses vs live v1" in unpaired_why
    assert paired_ok and paired_why.endswith(
        "shadowing live v1; serves -4 of 74 vs live v1 (McNemar p=0.06); 0 negative flips, 0 positive flips"
    )


# ---------- FR-8.42: the student trains on the prompt it is served ----------
TEMPLATE = ROOT / "tests" / "fixtures" / "nano4b_chat_template.jinja"
# chat_template.jinja of nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 at commit dfaf35d (read Oct 21, 2026); NOTICE says so.
TEMPLATE_SHA256 = "ab7813c3abdd9cb655905a410728b26c7884eca45ddfab8d9f931553485a7862"
SERVED_PROMPT_END = "<|im_start|>assistant\n<think></think>"  # the generation prompt with thinking off
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)")


def _endpoint_template_kwargs() -> dict:
    """The chat-template arguments the student endpoint serves with unless a request says otherwise: the JSON that
    deploy/student_endpoint.sh passes to vLLM's --default-chat-template-kwargs."""
    text = (ROOT / "deploy" / "student_endpoint.sh").read_text(encoding="utf-8")
    m = re.search(r"^DEFAULT_TEMPLATE_KWARGS='(\{.*\})'$", text, re.M)
    assert m, "student_endpoint.sh no longer sets DEFAULT_TEMPLATE_KWARGS"
    return json.loads(m.group(1))


def _renderer():
    """The vendored Nemotron 3 Nano template compiled as transformers' apply_chat_template compiles it (vLLM renders a
    chat request through the same call): render(messages, add_generation_prompt, chat_template_kwargs) -> text."""
    pytest.importorskip("jinja2")
    jinja_ext = pytest.importorskip("jinja2.ext")
    from jinja2.exceptions import TemplateError
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    source = TEMPLATE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == TEMPLATE_SHA256

    def tojson(x, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
        return json.dumps(x, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys)

    def raise_exception(message):
        raise TemplateError(message)

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=[jinja_ext.loopcontrols])
    env.filters["tojson"] = tojson
    env.globals["raise_exception"] = raise_exception
    template = env.from_string(source.decode("utf-8"))

    def render(messages, add_generation_prompt, kwargs):
        return template.render(
            messages=messages, tools=None, documents=None, add_generation_prompt=add_generation_prompt, **kwargs
        )

    return render


def _pins(path: Path) -> dict[str, str]:
    """`name==version` lines of a requirements or constraints file, names in lower case."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            m = PIN.fullmatch(line)
            assert m, f"{path.name}: {line!r} is not name==version"
            out[m.group(1).lower()] = m.group(2)
    return out


def _train_split(led: Ledger, task: str, text: str, answer: str) -> None:
    """Store `answer` to the first request '<text> <i>' that the ledger's split puts in train."""
    for i in range(200):
        msgs = [{"role": "user", "content": f"{text} {i}"}]
        probe = Ledger(":memory:", golden_fraction=led.golden_fraction)
        probe.add_example(task, msgs, answer, OPEN_TEACHER, "l", "u")
        if probe.examples()[-1]["split"] == "train":
            assert led.add_example(task, msgs, answer, OPEN_TEACHER, "l", "u")
            return
    raise AssertionError("no request landed in train")


@pytest.mark.req("FR-8.42", "INV-2", "FR-4.1")
def test_training_rows_carry_reasoning_off(tmp_path):
    led = Ledger(":memory:", golden_fraction=0.0)  # every example a training row
    _examples(led, train=5)
    convo = [
        {"role": "system", "content": "You triage support tickets."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello. What happened?"},
        {"role": "user", "content": "I was charged twice."},
    ]
    json_mode = {"response_format": {"type": "json_object"}}
    led.add_example("default", convo, '{"reply": "A refund is on its way."}', OPEN_TEACHER, "l", "u", params=json_mode)
    # An answer that quotes the tags: the template would render it without the empty think block (see the next test).
    led.add_example(
        "default", [{"role": "user", "content": "How do I hide reasoning?"}], "Wrap it in <think>.", OPEN_TEACHER, "l", "u"
    )
    led.add_example(
        "default", [{"role": "user", "content": "And close it?"}], "End it with </think> first.", OPEN_TEACHER, "l", "u"
    )

    manifest = dataset.build(led, tmp_path)
    assert (manifest["train_examples"], manifest["skipped_think_tag"]) == (6, 2)
    lines = (tmp_path / "train.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    stored = {r["hash"]: r for r in led.examples()}
    assert len(rows) == 6
    for line, row in zip(lines, rows, strict=True):
        assert "<think>" not in line and "</think>" not in line
        assert "messages" not in row and row["chat_template_kwargs"] == {"enable_thinking": False}
        ex = stored[row["provenance"]["hash"]]
        # INV-2: the target is the teacher's stored answer, the one assistant message of the completion.
        assert row["prompt"] == json.loads(ex["messages"])
        assert row["completion"] == [{"role": "assistant", "content": ex["completion"]}]
    assert next(r for r in rows if len(r["prompt"]) == 4)["params"] == json_mode  # kept, never given to TRL
    train.check_rows(rows)  # the GPU job accepts what Night School writes

    # The live trainer writes the same file (dataset.write_training), and the three copies of the arguments agree: the
    # rows, train.py's check and the endpoint's default (vLLM's --default-chat-template-kwargs).
    written = dataset.write_training(dataset.rows(led, "train"), tmp_path / "night" / "train.jsonl")
    assert train.load_rows(str(written)) == rows
    assert dict(dataset.TRAIN_TEMPLATE_KWARGS) == dict(train.TEMPLATE_KWARGS) == _endpoint_template_kwargs()
    assert dataset.TRAIN_TEMPLATE_KWARGS == {"enable_thinking": False} and dataset.THINK_TAGS == train.THINK_TAGS
    with pytest.raises(TypeError):
        dataset.TRAIN_TEMPLATE_KWARGS["enable_thinking"] = True  # type: ignore[index]

    # A row with a tag stays a golden row (the eval compares answers, it renders no template) and the night says how many
    # it left out of training, because manifest.json is a temporary file.
    golden = Ledger(":memory:", golden_fraction=1.0)
    golden.add_example("default", [{"role": "user", "content": "q"}], "Wrap it in <think>.", OPEN_TEACHER, "l", "u")
    assert [r["messages"][-1]["content"] for r in dataset.rows(golden, "golden")] == ["Wrap it in <think>."]
    night = Ledger(":memory:", golden_fraction=0.3)
    _examples(night, train=3, golden=1)
    _train_split(night, "default", "tagged", "Close it with </think>.")
    run_night(night, RecordingTrainer(), RecordingEvaluator(), ReleaseGate(night), BASE_MODEL, min_new_examples=50)
    assert night.events()[-1]["detail"] == (
        "only 3 new examples (< 50) (1 answers with a literal think tag left out of training); no training tonight"
    )


@pytest.mark.req("FR-8.42", "FR-5.1")
def test_training_prompt_is_a_prefix_and_matches_serving():
    render = _renderer()
    led = Ledger(":memory:", golden_fraction=0.0)
    _version(led, "live", {"threshold": 0.5})
    conversations = [
        [{"role": "user", "content": "Where is my refund for order 7?"}],
        [{"role": "system", "content": "You triage support tickets."}, {"role": "user", "content": "The app crashes on login."}],
        [
            {"role": "system", "content": "Answer in one sentence."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello. What can I do for you?"},
            {"role": "user", "content": "Cancel my subscription, please."},
        ],
    ]
    teacher = RecordingProvider(lambda msgs, model: f"Teacher answer: {msgs[-1]['content']}")
    student = RecordingProvider(["a", "b", "c"], logprob=-3.0)  # three answers that disagree: every request escalates
    r = Ratchet(Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0), led, teacher, lambda v: (student, v.adapter_uri))
    for convo in conversations:
        assert r.handle(convo, "default").served_by == "teacher"
    rows = [dataset.training_record(row) for row in dataset.rows(led, "train")]
    assert len(rows) == len(student.calls) == 3
    train.check_rows(rows)
    train.check_prefix(rows, render)  # the job's own check, here with the vendored template

    endpoint = _endpoint_template_kwargs()
    for row in rows:
        kwargs = row["chat_template_kwargs"]
        prompt = render(row["prompt"], True, kwargs)
        full = render(row["prompt"] + row["completion"], False, kwargs)
        # The prompt is a prefix: TRL's completion is exactly the teacher's answer and its end-of-turn token.
        assert prompt.endswith(SERVED_PROMPT_END)
        assert full == prompt + row["completion"][0]["content"] + "<|im_end|>\n"
        # And it is the prompt vLLM renders for the router's student call: the same messages, the endpoint's default
        # arguments, merged under any the request carried itself (request values win in vLLM).
        (call,) = [c for c in student.calls if c.messages == row["prompt"]]
        own = getattr(call.kwargs.get("params"), "template_kwargs", None) or {}
        assert prompt == render(call.messages, True, {**endpoint, **own})

    # Without the argument (the template's default: thinking on) the prompt ends in "<think>\n" and is not a prefix of
    # prompt + answer, which TRL only warns about: the bug this item fixes. The job's check stops on it.
    for row in rows:
        assert render(row["prompt"], True, {}).endswith("<|im_start|>assistant\n<think>\n")
        assert not render(row["prompt"] + row["completion"], False, {}).startswith(render(row["prompt"], True, {}))
    with pytest.raises(train.TemplateMismatch, match=r"^row 0: .*not a prefix"):
        train.check_prefix([{**row, "chat_template_kwargs": {}} for row in rows], render)
    # An answer that holds a tag is rendered without the empty think block, so it breaks the prefix too (dataset.rows and
    # check_rows leave such rows out).
    for tagged in ("Wrap it in <think>.", "End it with </think> first."):
        with pytest.raises(train.TemplateMismatch):
            train.check_prefix([{**rows[0], "completion": [{"role": "assistant", "content": tagged}]}], render)

    # The datasets library gives every message the keys any message has, as None; the template renders the same text.
    keys = {"name", "tool_calls", "tool_call_id", "reasoning_content"} | {k for row in rows for m in row["prompt"] for k in m}
    filled = [
        {
            **row,
            "prompt": [{**dict.fromkeys(keys), **m} for m in row["prompt"]],
            "completion": [{**dict.fromkeys(keys), **row["completion"][0]}],
        }
        for row in rows
    ]
    train.check_prefix(filled, render, served=rows)
    edited = [rows[0], {**rows[1], "prompt": [{"role": "user", "content": "something else"}]}, rows[2]]
    with pytest.raises(train.TemplateMismatch, match=r"^row 1: .*student endpoint"):
        train.check_prefix(rows, render, served=edited)
    with pytest.raises(train.TemplateMismatch, match="3 rows to train against 2 written"):
        train.check_prefix(rows, render, served=rows[:2])


@pytest.mark.req("FR-8.42", "FR-4.5")
def test_train_args_have_no_assistant_only_loss():
    source = (ROOT / "ratchet" / "nightschool" / "train.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def calls(name):
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == name]

    (config,) = calls("SFTConfig")
    kw = {k.arg: k.value for k in config.keywords}
    # The template has no {% generation %} markers, and TRL's loss on prompt-completion rows covers the completion only.
    off = kw.get("assistant_only_loss", ast.Constant(False))
    assert isinstance(off, ast.Constant) and off.value is False
    assert isinstance(kw.get("completion_only_loss"), ast.Constant) and kw["completion_only_loss"].value is True
    # TRL is given the three columns only (no stored condition reaches the template), after both checks.
    (main,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    called = [n.func.id for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert {"check_rows", "check_prefix", "columns", "SFTTrainer"} <= set(called)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import) and "unsloth" in ast.dump(n)]
    row = dataset.training_record(
        {"task": "t", "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}], "params": {"x": 1}}
    )
    assert train.columns([row]) == [
        {
            "prompt": [{"role": "user", "content": "q"}],
            "completion": [{"role": "assistant", "content": "a"}],
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ]


@pytest.mark.req("FR-8.42")
def test_dockerfile_is_pinned():
    docker = (ROOT / "deploy" / "Dockerfile.train").read_text(encoding="utf-8")
    pins = _pins(ROOT / "deploy" / "train-versions.txt")
    assert "|| true" not in docker and "trl==1.16.0" in docker and pins["trl"] == "1.16.0"
    runs = [line.removeprefix("RUN ").strip() for line in docker.replace("\\\n", " ").splitlines() if line.startswith("RUN ")]
    installed: dict[str, str] = {}
    for run in runs:
        for command in run.split("&&"):
            argv = shlex.split(command)
            if argv[:2] != ["pip", "install"]:
                continue
            specs, skip = [], False
            for arg in argv[2:]:
                if skip:
                    skip = False
                elif arg in ("-c", "--constraint", "--index-url", "--extra-index-url", "-f", "--find-links"):
                    skip = True
                elif not arg.startswith("-"):
                    specs.append(arg)
            assert "-r" not in argv and specs, run
            for spec in specs:  # every package carries ==, and the version is the list's
                m = PIN.fullmatch(spec)
                assert m, f"unpinned: {spec}"
                installed[m.group(1).lower()] = m.group(2)
            if "torch" not in specs[0]:  # every step after torch reads the list as constraints
                assert "-c /tmp/train-versions.txt" in command, run
    assert installed == pins and "unsloth" not in installed
    copy = docker.index("COPY deploy/train-versions.txt /tmp/train-versions.txt")
    assert copy < docker.index("-c /tmp/train-versions.txt")  # on disk before the first step reads it
    # The kernels' import is checked after they are installed, so a failed build fails the image.
    mamba = next(i for i, run in enumerate(runs) if "mamba-ssm==" in run)
    check = next(i for i, run in enumerate(runs) if run.startswith('python -c "import torch, mamba_ssm, causal_conv1d'))
    assert check > mamba
    # The job runs `python -m ratchet.nightschool.train`; Ubuntu has no `python` without python-is-python3.
    assert "python-is-python3" in runs[0]
    # versions.json records exactly what the image pins, and the `train` extra agrees with the image.
    assert {name.lower() for name in train.LIBRARIES} == set(pins)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    extra = re.search(r"^train = \[(.*)\]$", pyproject, re.M)
    assert extra
    train_extra = dict(m.groups() for m in map(PIN.fullmatch, re.findall(r'"([^"]+)"', extra.group(1))) if m)
    assert train_extra and all(pins[name.lower()] == version for name, version in train_extra.items())


@pytest.mark.req("FR-8.42", "FR-4.5", "INV-14")
def test_train_records_library_versions():
    pins = _pins(ROOT / "deploy" / "train-versions.txt")

    def lookup(name):
        if name == "mamba-ssm":
            raise importlib.metadata.PackageNotFoundError(name)
        return pins[name]

    doc = train.versions(lookup, cuda="12.8", python="3.12.3")
    assert doc == {"python": "3.12.3", "cuda": "12.8", "libraries": {**pins, "mamba-ssm": None}}
    assert list(doc["libraries"]) == list(train.LIBRARIES)
    assert json.loads(json.dumps(doc)) == doc
    assert train.versions(lambda name: "1.0")["python"] == platform.python_version()
    # The signed BOM has no section for them, so versions.json stays beside the adapter: a stored BOM is never rewritten.
    assert not {"versions", "libraries", "training_image"} & set(bom.EXTRA_KEYS)
    assert train.manifest("base", [])["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.req("FR-8.42")
def test_train_refuses_rows_that_would_train_another_prompt():
    led = Ledger(":memory:", golden_fraction=0.0)
    _examples(led, train=2)
    good = [dataset.training_record(r) for r in dataset.rows(led, "train")]
    train.check_rows(good)
    first = good[0]
    secret = "SECRET-TEXT"
    answer = [{"role": "assistant", "content": secret}]
    cases = [
        ({"messages": first["prompt"] + answer, "provenance": first["provenance"]}, "has `messages` instead"),
        ({k: v for k, v in first.items() if k != "chat_template_kwargs"}, "`chat_template_kwargs` is not"),
        ({**first, "chat_template_kwargs": {"enable_thinking": True}}, "`chat_template_kwargs` is not"),
        ({**first, "chat_template_kwargs": {"enable_thinking": False, "x": 1}}, "`chat_template_kwargs` is not"),
        ({**first, "completion": answer * 2}, "`completion` is not one message"),
        ({**first, "completion": secret}, "`completion` is not one message"),
        ({**first, "completion": [{"role": "user", "content": secret}]}, "not an assistant message with text"),
        ({**first, "completion": [{"role": "assistant", "content": "   "}]}, "not an assistant message with text"),
        ({**first, "completion": [{"role": "assistant", "content": f"{secret} </think>"}]}, "literal think tag"),
        ({**first, "prompt": []}, "`prompt` is not"),
        ({**first, "prompt": secret}, "`prompt` is not"),
        ({**first, "prompt": [{"content": secret}]}, "`prompt` is not"),
        ({**first, "provenance": {"teacher": OPEN_TEACHER}}, "`provenance` lacks"),
        (secret, "not an object"),
    ]
    for bad, reason in cases:
        with pytest.raises(ValueError, match=rf"^row 1: .*{re.escape(reason)}") as err:
            train.check_rows([first, bad])
        assert secret not in str(err.value)  # the job's logs never get the text
    with pytest.raises(ValueError, match="no training rows"):
        train.check_rows([])


@pytest.mark.req("FR-4.4", "INV-14", "INV-17")
def test_the_version_is_recorded_on_the_rows_the_adapter_was_trained_on(monkeypatch):
    """An example the gateway stores while the dataset files are written (live mode: another process) is in the next
    night, not half in this one: the trainer's rows, the version's example count, the signed dataset hash and the
    commitment all come from one read of the ledger."""
    led = Ledger(":memory:", golden_fraction=0.3)
    _examples(led, train=60, golden=10)
    real = dataset.write_jsonl
    fired: list[object] = []

    def write_jsonl(items, path):
        if not fired:  # one more teacher answer lands now
            fired.append(_examples(led, train=1, prefix="late"))
        return real(items, path)

    monkeypatch.setattr(dataset, "write_jsonl", write_jsonl)
    ev = RecordingEvaluator({"precision": 0.99, "coverage": 0.9, "threshold": 0.8, "golden_agreement": 0.9, "golden_n": 10})
    trainer = RecordingTrainer()
    rep = run_night(led, trainer, ev, ReleaseGate(led, ReleasePolicy()), BASE_MODEL, min_new_examples=1)
    assert fired and rep.trained
    rows = trainer.calls[0].rows
    bom_doc = led.version_bom(rep.version)["manifest"]["training"]
    assert led.version(rep.version).train_examples == len(rows) == bom_doc["examples"] == bom_doc["commitment"]["size"] == 60
    assert bom_doc["dataset_sha256"] == dataset.dataset_hash(rows)
    assert len(led.version_leaves(rep.version, "train")) == 60
    assert len(dataset.rows(led, "train")) == 61  # the late example waits for tomorrow


@pytest.mark.req("INV-3", "INV-6", "FR-4.2")
def test_a_prompt_held_out_in_one_task_is_never_trained_on_through_another():
    """One adapter serves every task, so the split is by prompt across tasks (ADR 0131): a prompt that is golden under one
    task is golden under all of them, and its occurrences add up across tasks for the frequent rule."""
    from ratchet import licenses

    lic = licenses.lookup(OPEN_TEACHER)
    led = Ledger(":memory:", golden_fraction=0.3)

    def pick(first: str, second: str, want_first: str, want_second: str, tag: str) -> list[dict[str, str]]:
        for i in range(2000):
            msgs = [{"role": "user", "content": f"shared prompt {tag} {i}"}]
            if led.split_of(first, msgs) == want_first and led.split_of(second, msgs) == want_second:
                return msgs
        raise AssertionError("no prompt found")

    def store(task: str, msgs: list[dict[str, str]]) -> None:
        assert led.add_example(task, msgs, f"answer in {task}", OPEN_TEACHER, lic.name, lic.url)

    split_apart = pick("alpha", "beta", "golden", "train", "apart")  # golden under alpha, train under beta
    both_train = pick("alpha", "beta", "train", "train", "both-train")
    both_golden = pick("alpha", "beta", "golden", "golden", "both-golden")
    frequent = pick("alpha", "beta", "golden", "golden", "frequent")
    for msgs in (split_apart, both_train, both_golden, frequent):
        store("alpha", msgs)
        store("beta", msgs)
    assert not led.add_example("alpha", frequent, "again", OPEN_TEACHER, lic.name, lic.url)  # a duplicate: seen 2 in alpha
    # A prompt only beta holds (alpha has no copy) trains as before.
    only_beta = pick("beta", "gamma", "train", "train", "only-beta")
    store("beta", only_beta)

    def contents(split: str, task: str | None = None) -> set[tuple[str, str]]:
        return {(r["task"], r["messages"][0]["content"]) for r in dataset.rows(led, split, task)}

    def key(task: str, msgs: list[dict[str, str]]) -> tuple[str, str]:
        return task, msgs[0]["content"]

    train, golden = contents("train"), contents("golden")
    assert key("beta", split_apart) not in train and key("beta", split_apart) in golden  # held out through alpha
    assert key("alpha", split_apart) in golden
    assert {key("alpha", both_train), key("beta", both_train)} <= train  # trained in both, as before
    assert {key("alpha", both_golden), key("beta", both_golden)} <= golden
    assert {key("alpha", frequent), key("beta", frequent)} <= train and not {key("alpha", frequent)} & golden  # 2 + 1 = FREQUENT
    assert key("beta", only_beta) in train
    assert key("beta", split_apart) in contents("golden", "beta") and key("beta", split_apart) not in contents("train", "beta")
    # Every row is in one split only, and the two sets never share a prompt.
    assert not {c for _, c in train} & {c for _, c in golden}
