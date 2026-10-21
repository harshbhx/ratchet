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
from ratchet.nightschool.evaluate import LiveEvaluator, cascade_eval
from ratchet.nightschool.orchestrate import run_night
from ratchet.providers import GenParams, UpstreamError
from ratchet.release import CANARY_STEPS, ReleaseGate
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
