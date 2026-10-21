"""Risk-aware routing (FR-2.50, ADR 0049): a task's `routing` block.

Eligibility before the student is called (`student_max_input_tokens`, content parts the student does not read, and
`store_ineligible`), `escalate_when` rules on the confident student's answer (`action: teacher`, `min_confidence`), the
per-task audit rate (`policy.audit_rate`), critical classes measured by the golden eval and enforced by the release
gate at admit and at a night's re-score, the routes these add (`ineligible:<reason>`, `rule:<i>`) in the ledger and in
`stats_by_task`, and the spec checks that name every problem with its path.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, RecordingEvaluator, RecordingProvider, RecordingTrainer, _examples, _version
from fastapi.testclient import TestClient

from ratchet import routing, tasks, taskspec
from ratchet.confidence import SERVE_NOTHING
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.nightschool.orchestrate import rescore, run_night
from ratchet.providers import Completion
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.resilience import UpstreamError
from ratchet.router import INELIGIBLE_ROUTE, ROUTES, RULE_ROUTE, Ratchet, known_route
from ratchet.stats import wilson
from ratchet.taskspec import SpecError

MOD = "moderation"
SPEC: dict[str, Any] = {
    "name": MOD,
    "fields": {"flagged": {"type": "bool"}, "severity": {"type": "label", "values": ["low", "high"]}},
}
FLAG_RULE = {"field": "flagged", "equals": True, "action": "teacher"}
CRITICAL = {"field": "flagged", "equals": True, "max_miss_rate": 0.1}
CONFIDENT = math.log(0.95)  # unanimous samples at token probability 0.95: confidence 0.4 x 0.95 + 0.6 = 0.98


def _answer(flagged: bool, severity: str = "low") -> str:
    return json.dumps({"flagged": flagged, "severity": severity})


def _register(routing_block: dict[str, Any] | None = None, **spec: Any) -> tasks.Task:
    """Register the moderation task (as a tasks file would) with this routing block."""
    task = taskspec.parse_spec({**SPEC, **spec, "routing": routing_block or {}}, "file")
    tasks.register(task)
    return task


def _ask(tag: object, size: int = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"post {tag}" + "x" * size}]


def _router(
    led: Ledger, student_text: str, teacher_text: str | None = None, logprob: float | None = CONFIDENT, **settings: Any
) -> tuple[Ratchet, RecordingProvider, RecordingProvider]:
    student = RecordingProvider(student_text, logprob=logprob)
    teacher = RecordingProvider(teacher_text if teacher_text is not None else student_text)
    s = Settings(**{"teacher_model": OPEN_TEACHER, "samples": 3, "audit_rate": 0.0, "redact_pii": True, **settings})
    return Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri)), student, teacher


def _last(led: Ledger) -> dict[str, Any]:
    return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


# ---------- escalate_when ----------
@pytest.mark.req("FR-2.50", "FR-2.4")
def test_escalate_when_flagged_forces_teacher():
    """A confident student answer that a rule holds for goes to the teacher (route `rule:0`); the teacher's answer is
    stored like any escalation's. An answer the rule does not hold for is served by the student."""
    _register({"escalate_when": [FLAG_RULE]})
    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": 0.8})
    r, student, teacher = _router(led, _answer(True, "high"))
    out = r.handle(_ask(1), MOD)
    assert (out.served_by, out.route, out.escalated, out.text) == ("teacher", "rule:0", True, _answer(True, "high"))
    assert out.confidence == pytest.approx(0.98) and len(student.calls) == 1 and len(teacher.calls) == 1
    assert out.student_cost_usd > 0 and out.teacher_cost_usd > 0  # the student was sampled, then the teacher answered
    (ex,) = led.examples()
    assert (ex["task"], ex["completion"], ex["teacher_model"]) == (MOD, _answer(True, "high"), OPEN_TEACHER)
    row = _last(led)
    assert (row["route"], row["served_by"], row["escalated"], row["stored"], row["student_version"]) == (
        "rule:0",
        "teacher",
        1,
        1,
        vid,
    )
    assert row["confidence"] == pytest.approx(0.98)

    student.text = _answer(False, "high")  # not flagged: the rule does not hold
    out = r.handle(_ask(2), MOD)
    assert (out.served_by, out.route, out.version) == ("student", "student", vid) and len(teacher.calls) == 1

    # Below the threshold the request escalates for its confidence; the rules are checked only once the student is sure.
    student.text, student.logprob = _answer(True, "high"), math.log(0.2)  # confidence 0.68 < 0.8
    assert r.handle(_ask(3), MOD).route == "confidence"


@pytest.mark.req("FR-2.50")
def test_min_confidence_rule():
    """A `min_confidence` rule lets the student serve an answer in its class only at that confidence or above; rules
    are checked in order and the first that holds names the route."""
    _register(
        {
            "escalate_when": [
                {"field": "severity", "equals": "high", "min_confidence": 0.99},
                {"field": "flagged", "in": [True], "min_confidence": 0.9999},
            ]
        }
    )
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.8})
    r, student, _ = _router(led, _answer(False, "high"))
    assert r.handle(_ask(1), MOD).route == "rule:0"  # 0.98 < 0.99
    student.logprob = math.log(0.999)  # confidence 0.9996
    out = r.handle(_ask(2), MOD)
    assert (out.route, out.served_by) == ("student", "student") and out.confidence == pytest.approx(0.9996)
    student.text, student.logprob = _answer(True, "low"), CONFIDENT
    assert r.handle(_ask(3), MOD).route == "rule:1"  # rule 0 does not hold; rule 1 does, at 0.98 < 0.9999
    student.text = _answer(True, "high")  # both hold: the first names the route
    assert r.handle(_ask(4), MOD).route == "rule:0"
    student.logprob = math.log(0.999)  # 0.9996: enough for rule 0, not for rule 1
    assert r.handle(_ask(5), MOD).route == "rule:1"
    # The label comparator applies: 'HIGH' is 'high'.
    student.text, student.logprob = json.dumps({"flagged": False, "severity": "HIGH"}), CONFIDENT
    assert r.handle(_ask(6), MOD).route == "rule:0"


@pytest.mark.req("FR-2.50", "FR-2.12")
def test_rule_answer_never_stands_in_for_the_teacher():
    """With RATCHET_ON_TEACHER_FAILURE=serve_student, an answer a rule holds for is never served in degraded mode, on
    the rule's own route or after a low confidence: the teacher's error reaches the client. Other answers degrade as
    before."""
    _register({"escalate_when": [FLAG_RULE]})
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.8})
    r, student, teacher = _router(led, _answer(True), on_teacher_failure="serve_student")
    teacher.raise_on.add(OPEN_TEACHER)
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask(1), MOD)
    student.logprob = math.log(0.2)  # unsure, and flagged
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask(2), MOD)
    student.text = _answer(False)  # unsure, not flagged: degraded as before
    out = r.handle(_ask(3), MOD)
    assert (out.route, out.served_by, out.degraded) == ("degraded", "student", "teacher_unavailable")
    assert led.examples() == []


@pytest.mark.req("FR-2.50", "FR-6.3")
def test_shadow_runs_the_rules_too():
    """The candidate in shadow runs the same cascade: an answer a rule sends to the teacher is not one it would serve."""
    _register({"escalate_when": [FLAG_RULE]})
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.8})
    shadow = _version(led, "shadow", {"threshold": 0.8}, adapter_uri="student-v2")
    live_student, shadow_student = RecordingProvider(_answer(False), CONFIDENT), RecordingProvider(_answer(True), CONFIDENT)
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0)
    r = Ratchet(
        s,
        led,
        RecordingProvider(_answer(False)),
        lambda v: (shadow_student if v.state == "shadow" else live_student, v.adapter_uri),
    )
    r.handle(_ask(1), MOD)
    assert (_last(led)["route"], _last(led)["shadow_version"], _last(led)["shadow_serve"]) == ("student", shadow, 0)
    shadow_student.text = _answer(False)
    r.handle(_ask(2), MOD)
    assert (_last(led)["shadow_serve"], _last(led)["shadow_agree"]) == (1, 1)


# ---------- eligibility ----------
@pytest.mark.req("FR-2.50", "INV-12")
def test_long_input_skips_student_and_is_not_stored():
    """A request with more input than `student_max_input_tokens` (characters / 4) goes to the teacher without a version
    being picked or the student called (route `ineligible:input_tokens`), and without `store_ineligible` its answer is
    not stored: train.py cuts every example at --max-seq."""
    _register({"student_max_input_tokens": 50})
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.8})
    shadow = _version(led, "shadow", {"threshold": 0.8}, adapter_uri="student-v2")
    r, student, teacher = _router(led, _answer(False), teacher_text=_answer(True))
    long = _ask(1, size=400)  # 407 characters: 101 tokens
    out = r.handle(long, MOD)
    assert (out.served_by, out.route, out.escalated, out.version, out.confidence) == (
        "teacher",
        "ineligible:input_tokens",
        False,
        None,
        None,
    )
    assert student.calls == [] and len(teacher.calls) == 1 and teacher.calls[0].messages == long
    assert led.examples() == [] and out.student_cost_usd == 0 and out.teacher_cost_usd == out.cost_usd > 0
    row = _last(led)
    assert (row["route"], row["student_version"], row["escalated"], row["stored"], row["shadow_version"]) == (
        "ineligible:input_tokens",
        None,
        0,
        0,
        None,
    )
    assert row["cost_usd"] == row["baseline_cost_usd"] == row["teacher_cost_usd"]
    assert [e for e in led.events() if e["kind"] == "skip_example"] == []  # not storable is not a skip
    led.set_version(shadow, "retired", 0)
    # At the limit the request is eligible: 50 tokens is 200 characters.
    at_limit = [{"role": "user", "content": "y" * 203}]
    assert routing.input_tokens(at_limit) == 50 and r.handle(at_limit, MOD).route == "student"
    assert len(student.calls) == 1

    # With store_ineligible the teacher's answer is stored, under the usual rules (INV-16).
    _register({"student_max_input_tokens": 50, "store_ineligible": True})
    out = r.handle(long, MOD)
    assert out.route == "ineligible:input_tokens" and _last(led)["stored"] == 1 and len(student.calls) == 1
    (ex,) = led.examples()
    assert json.loads(ex["messages"]) == long and ex["completion"] == _answer(True)
    teacher.text = "not json"  # invalid under the spec: served, not stored
    assert r.handle(_ask(2, size=400), MOD).text == "not json" and len(led.examples()) == 1

    # n choices, and a teacher that fails: the error reaches the client (the student never answered).
    teacher.text = _answer(True)
    assert len(r.handle(_ask(3, size=400), MOD, n=2).choices) == 2
    teacher.raise_on.add(OPEN_TEACHER)
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask(4, size=400), MOD)
    assert len(student.calls) == 1


@pytest.mark.req("FR-2.50", "FR-2.9", "FR-14.1")
def test_eligibility_runs_after_pinned_and_before_the_version():
    """Pinned tasks go to the teacher first (their routing block is not read); eligibility comes before a version is
    picked, so a task disabled on the serving version still reports why the student could not take the request."""
    _register({"student_max_input_tokens": 10}, pinned=True)
    led = Ledger(":memory:")
    r, student, _ = _router(led, _answer(False))
    assert r.handle(_ask(1, size=100), MOD).route == "pinned"
    task = _register({"student_max_input_tokens": 10})
    record = {"threshold": SERVE_NOTHING, "disabled": "regressed", "spec_hash": task.spec_hash, "test_n": 40}
    _version(led, "live", {"threshold": 0.8, "tasks": {MOD: record}})
    assert r.handle(_ask(2, size=100), MOD).route == "ineligible:input_tokens"
    assert r.handle(_ask(3), MOD).route == "task_disabled" and student.calls == []
    # No version is picked: with a canary in flight, an ineligible request takes no draw from the canary split.
    _version(led, "canary", {"threshold": 0.8}, adapter_uri="student-v2")
    state = r.rng.getstate()
    assert r.handle(_ask(4, size=100), MOD).route == "ineligible:input_tokens" and r.rng.getstate() == state
    r.handle(_ask(5), MOD)
    assert r.rng.getstate() != state


@pytest.mark.req("FR-2.50", "FR-1.10", "INV-12")
def test_image_part_skips_student(gw_env):
    """With RATCHET_TEACHER_MODALITIES=text,image_url an image request is ineligible for the student (it reads text
    only): the teacher answers with route `ineligible:content_type`, nothing is stored even with `store_ineligible`, and
    the route shows in /ratchet/stats."""
    gw = gw_env(
        RATCHET_TEACHER_MODALITIES="text,image_url",
        RATCHET_API_KEYS="",
        RATCHET_API_KEY_DAILY_REQUESTS="0",
        RATCHET_TEACHER_MODEL=OPEN_TEACHER,
        RATCHET_TASKS_FILE="",
        RATCHET_UNKNOWN_TASKS="distill",
    )
    _register({"store_ineligible": True})
    led = gw.rt.ledger
    _version(led, "live", {"threshold": 0.0})
    student, teacher = RecordingProvider(_answer(False)), RecordingProvider(_answer(True))
    gw.rt.ratchet.teacher = teacher
    gw.rt.ratchet.resolve_student = lambda v: (student, v.adapter_uri)
    c = TestClient(gw.app)
    image = {
        "role": "user",
        "content": [{"type": "text", "text": "Is this post allowed?"}, {"type": "image_url", "image_url": {"url": "x"}}],
    }
    r = c.post("/v1/chat/completions", json={"messages": [image]}, headers={"X-Ratchet-Task": MOD})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert student.calls == [] and teacher.calls[0].messages == [image] and led.examples() == []
    assert (_last(led)["route"], _last(led)["stored"], _last(led)["student_version"]) == ("ineligible:content_type", 0, None)
    text = c.post("/v1/chat/completions", json={"messages": _ask(1)}, headers={"X-Ratchet-Task": MOD})
    assert text.json()["ratchet"]["served_by"] == "student" and len(student.calls) == 1
    (mod,) = [t for t in c.get("/ratchet/stats").json()["tasks"] if t["task"] == MOD]
    assert mod["routes"] == {"ineligible:content_type": 1, "student": 1}


@pytest.mark.req("FR-2.50")
def test_ineligible_reads_every_message_shape():
    """`Routing.ineligible` and `input_chars` read what the gateway forwards (content strings and text parts, names,
    refusals, tool call ids, names and arguments) and never raise on another shape."""
    msgs = [
        {"role": "system", "content": "abcd", "name": "ops"},
        {"role": "user", "content": [{"type": "text", "text": "12345678"}]},
        {
            "role": "assistant",
            "content": [{"type": "refusal", "refusal": "no"}],
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "look", "arguments": "{}"}}],
            "function_call": {"name": "f", "arguments": "[]"},
        },
        {"role": "tool", "content": "done", "tool_call_id": "c1"},
        "not a message",
        {"role": "user", "content": None},
    ]
    assert routing.input_chars(msgs) == 4 + 3 + 8 + 2 + 2 + 4 + 2 + 1 + 2 + 4 + 2
    assert routing.input_tokens([]) == 1 and routing.input_tokens(msgs) == 34 // 4
    default = routing.DEFAULT
    assert default.ineligible(msgs) is None  # text parts and an assistant's refusal are text
    image = [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "u"}}]}]
    assert default.ineligible(image) == "content_type"
    # A refusal part is text only in an assistant message, as normalize_messages reads it.
    assert default.ineligible([{"role": "user", "content": [{"type": "refusal", "refusal": "x"}]}]) == "content_type"
    assert default.ineligible([{"role": "user", "content": [{"kind": "odd"}, "part"]}]) == "content_type"
    limited = _register({"student_max_input_tokens": 7}).routing_rules  # 34 characters are 8 tokens
    assert limited.ineligible(msgs) == "input_tokens" and limited.ineligible(msgs[:2]) is None
    assert limited.ineligible(image + msgs) == "content_type"  # checked first: such a request is never stored
    assert limited.stores("input_tokens") is False
    assert _register({"student_max_input_tokens": 7, "store_ineligible": True}).routing_rules.stores("input_tokens")
    assert not _register({"store_ineligible": True}).routing_rules.stores("content_type")


# ---------- audits ----------
@pytest.mark.req("FR-2.50", "FR-2.5")
def test_task_audit_rate_override():
    """A task's `policy.audit_rate` replaces RATCHET_AUDIT_RATE for its served student answers, with one draw of the
    router's random generator either way, so other tasks are audited exactly as before."""
    _register(policy={"audit_rate": 1.0})
    tasks.register(taskspec.parse_spec({**SPEC, "name": "never", "policy": {"audit_rate": 0}}, "file"))
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.8})
    r, _, teacher = _router(led, _answer(False), audit_rate=0.5)
    r.rng = random.Random(7)
    for i in range(5):
        r.handle(_ask(i), MOD)
        r.handle(_ask(f"n{i}"), "never")
    audited = dict(led.db.execute("SELECT task, SUM(audited) FROM requests GROUP BY task").fetchall())
    assert audited == {MOD: 5, "never": 0} and len(teacher.calls) == 5

    # Same draws: a task without its own rate is audited on the same requests whether or not another task has one.
    tasks.register(tasks.Task("plain", source="file"))

    def audits(policy: dict[str, Any]) -> list[int]:
        tasks.register(taskspec.parse_spec({**SPEC, "name": "other", "policy": policy}, "file"))
        led2 = Ledger(":memory:")
        _version(led2, "live", {"threshold": 0.8})
        r2, _, _ = _router(led2, _answer(False), audit_rate=0.3)
        r2.rng = random.Random(3)
        for i in range(30):
            r2.handle(_ask(i), "other" if i % 2 else "plain")
        return [x[0] for x in led2.db.execute("SELECT audited FROM requests WHERE task='plain' ORDER BY id")]

    base = audits({})
    assert audits({"audit_rate": 1.0}) == base == audits({"audit_rate": 0.0}) and 0 < sum(base) < 15


# ---------- routes ----------
@pytest.mark.req("FR-2.50", "FR-2.11")
def test_route_logged_and_in_stats_by_task():
    _register({"student_max_input_tokens": 50, "escalate_when": [{"field": "severity", "equals": "high", "action": "teacher"}]})
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.8})
    r, student, _ = _router(led, _answer(False, "high"))
    r.handle(_ask(1), MOD)
    r.handle(_ask(2), MOD)
    r.handle(_ask(3, size=400), MOD)
    student.text = _answer(False, "low")
    r.handle(_ask(4), MOD)
    (mod,) = [t for t in led.stats_by_task() if t["task"] == MOD]
    assert mod["routes"] == {"rule:0": 2, "ineligible:input_tokens": 1, "student": 1}
    assert mod["requests"] == 4 and mod["escalation_rate"] == 0.5  # an ineligible request is not an escalation
    assert all(known_route(route) for route in mod["routes"])
    assert all(known_route(route) for route in ROUTES)
    assert known_route(f"{RULE_ROUTE}31") and known_route(f"{INELIGIBLE_ROUTE}content_type")
    for bad in ("rule:", "rule:x", "rule:-1", "rule:\u0663", "ineligible:", "ineligible:size", "rules:0", ""):
        assert not known_route(bad), bad


# ---------- critical classes ----------
def _golden(task: str, tag: str, conf: float, answer: str, reference: str) -> dict[str, Any]:
    """A golden row whose student answer and confidence are scripted in its prompt (unanimous samples)."""
    prompt = json.dumps({"tag": tag, "conf": conf, "answer": answer})
    return {"task": task, "messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": reference}]}


def _scripted(prompt: list[dict[str, Any]], n: int, params: Any = None) -> list[Completion]:
    spec = json.loads(prompt[-1]["content"])
    lp = math.log((spec["conf"] - 0.6) / 0.4)
    return [Completion(spec["answer"], "student-v1", 10, 3, 1.0, [lp] * 3) for _ in range(n)]


def _critical_rows(
    class_rows: int, misses: int, others: int = 80, miss_conf: float = 0.98, calib_conf: float = 0.98, chat_conf: float = 0.98
) -> list[dict[str, Any]]:
    """Golden rows for the moderation task and a plain `chat` task, in blocks of (moderation calibration row,
    moderation held-out row, chat calibration row, chat held-out row), so each task's held-out rows are its odd rows.
    Every moderation calibration row is right at confidence `calib_conf`, every chat one at 0.98. Of the moderation
    task's held-out rows, `class_rows` have a flagged reference, and the first `misses` of those the student answers
    unflagged at `miss_conf`; the rest, and `others` unflagged ones, are right at 0.98. Chat's held-out rows are right
    at `chat_conf`."""
    flagged, clean = _answer(True, "high"), _answer(False)
    held_out = [
        (flagged, _answer(False, "high") if j < misses else flagged, miss_conf if j < misses else 0.98) for j in range(class_rows)
    ]
    held_out += [(clean, clean, 0.98)] * others
    rows = []
    for j, (ref, got, conf) in enumerate(held_out):
        calib = flagged if j % 3 == 0 else clean
        rows += [
            _golden(MOD, f"c{j}", calib_conf, calib, calib),
            _golden(MOD, f"t{j}", conf, got, ref),
            _golden("chat", f"cc{j}", 0.98, "fine", "fine"),
            _golden("chat", f"ct{j}", chat_conf, "fine", "fine"),
        ]
    return rows


def _measured(n: int, misses: int, max_miss_rate: float = 0.1) -> dict[str, Any]:
    return {
        "field": "flagged",
        "equals": True,
        "max_miss_rate": max_miss_rate,
        "n": n,
        "misses": misses,
        "miss_rate": misses / n if n else None,
        "upper_bound": wilson(misses, n)[1],
    }


@pytest.mark.req("FR-2.50", "FR-5.2", "INV-6")
def test_cascade_eval_measures_critical_misses():
    """On the held-out half, a miss is a row whose teacher reference is in the class but whose answer, served at the
    task's threshold and past its rules, is not. Rows the student would not serve (unsure, refused by the endpoint, or
    sent to the teacher by a rule) are not misses."""
    _register({"critical": [CRITICAL, {"field": "severity", "in": ["high"], "max_miss_rate": 0.5}]})
    tasks.register(tasks.Task("chat", source="file"))
    res = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2))
    rec = res["tasks"][MOD]
    assert rec["threshold"] == pytest.approx(0.98) and rec["test_n"] == 120
    flagged, severe = rec["critical"]
    assert flagged == _measured(40, 2)
    # The misses answered severity 'high', so the second class has none.
    without_equals = {k: v for k, v in _measured(40, 0, 0.5).items() if k != "equals"}
    assert severe == {**without_equals, "field": "severity", "in": ["high"]}
    assert "critical" not in res["tasks"]["chat"]

    # Below the task's threshold the teacher answers: not a miss.
    low = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2, miss_conf=0.7))["tasks"][MOD]
    assert low["critical"][0]["misses"] == 0 and low["threshold"] == pytest.approx(0.98)
    # A rule that sends unflagged answers below 0.99 to the teacher keeps the misses from being served.
    _register({"escalate_when": [{"field": "flagged", "equals": False, "min_confidence": 0.99}], "critical": [CRITICAL]})
    ruled = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2))["tasks"][MOD]
    assert ruled["critical"] == [_measured(40, 0)]

    # Misses are counted at the threshold the record stores, which the router applies: a miss just under the exact
    # calibrated threshold (0.98004) but at or over the stored one (0.98) is served in production, so it counts.
    _register({"critical": [CRITICAL]})
    edge = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2, miss_conf=0.98002, calib_conf=0.98004))
    assert edge["tasks"][MOD]["threshold"] == 0.98 and edge["tasks"][MOD]["critical"] == [_measured(40, 2)]

    # A held-out row the endpoint refuses escalates in production (INV-7): never a miss.
    _register({"critical": [CRITICAL]})

    def refusing(prompt: list[dict[str, Any]], n: int, params: Any = None) -> list[Completion]:
        if json.loads(prompt[-1]["content"])["tag"] in ("t0", "t1"):
            raise UpstreamError(400, "the prompt is too long for this endpoint")
        return _scripted(prompt, n)

    refused = cascade_eval(refusing, _critical_rows(class_rows=40, misses=2))
    assert refused["refused_n"] == 2 and refused["tasks"][MOD]["critical"] == [_measured(40, 0)]

    # The pieces: an invalid answer is never served; with no row in the class nothing is shown.
    rules = tasks.get(MOD).routing_rules
    row = rules.critical_row(tasks.get(MOD).key(_answer(True)), routing.INVALID, 0.99)
    assert row.floor == math.inf and row.reference == (True,) and row.answer == (False,)
    assert rules.misses([row], 0.5) == [_measured(1, 0)]
    assert rules.misses([], 0.5) == [{**_measured(0, 0), "upper_bound": 1.0}]
    assert routing.DEFAULT.misses([row], 0.5) == []


@pytest.mark.req("FR-2.50", "FR-6.1", "FR-14.1")
def test_critical_miss_rate_disables_task_at_admit():
    """With at least `min_task_test_n` held-out rows in the class, the gate disables the task (or rejects the version
    under RATCHET_TASK_MODE=reject) when the miss rate's 95% Wilson upper bound exceeds `max_miss_rate`; the version
    ships for its other tasks, and the router sends the task to the teacher without a student call."""
    _register({"critical": [CRITICAL]})
    tasks.register(tasks.Task("chat", source="file"))
    led = Ledger(":memory:")
    res = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2))
    assert res["precision"] > 0.98 and res["coverage"] == 1.0  # the pooled numbers pass
    cand = _version(led, "candidate", res)
    hi = wilson(2, 40)[1]
    why = f"critical flagged = true: missed 2 of 40 held-out rows (5.0%, upper bound {hi:.1%} > 10.0%)"
    assert ReleaseGate(led).task_failures(led.version(cand), None) == [(MOD, why)]
    assert ReleaseGate(led).admit(cand)
    v = led.version(cand)
    assert v.state == "canary" and v.eval["tasks"][MOD]["disabled"] == why
    assert v.eval["tasks"][MOD]["threshold"] == SERVE_NOTHING and "disabled" not in v.eval["tasks"]["chat"]
    assert (led.events()[0]["kind"], led.events()[0]["detail"]) == (
        "task_disabled",
        f"v{cand} {MOD}: {why}; the teacher answers it, v{cand} ships for its other tasks",
    )
    led.set_version(cand, "live", 100)
    r, student, _ = _router(led, _answer(False))
    assert r.handle(_ask(1), MOD).route == "task_disabled" and student.calls == []
    assert r.handle(_ask(2), "chat").route == "student"

    rejected = _version(led, "candidate", res)
    assert not ReleaseGate(led, ReleasePolicy(task_mode="reject")).admit(rejected)
    assert led.events()[-1]["detail"] == f"v{rejected}: task {MOD}: {why}"


@pytest.mark.req("FR-2.50", "FR-6.1")
def test_critical_gate_needs_enough_rows_in_the_class():
    """With no misses, n rows show a miss rate of at most 3.84 / (n + 3.84): 35 rows pass a 10% limit, 34 do not.
    Fewer than `min_task_test_n` rows in the class never pass: the student keeps a critical task only on evidence. A
    record that does not measure the task's current classes is not evidence either."""
    task = _register({"critical": [CRITICAL]})
    gate = ReleaseGate(Ledger(":memory:"))
    base = {"spec_hash": task.spec_hash, "threshold": 0.9, "test_n": 200}
    assert gate.critical_failure(task, {**base, "critical": [_measured(35, 0)]}) is None
    assert gate.critical_failure(task, {**base, "critical": [_measured(34, 0)]}) == (
        f"critical flagged = true: missed 0 of 34 held-out rows (0.0%, upper bound {wilson(0, 34)[1]:.1%} > 10.0%)"
    )
    lax = _register({"critical": [{**CRITICAL, "max_miss_rate": 0.5}]})  # the limit is the spec's, not the record's
    lax_base = {**base, "spec_hash": lax.spec_hash}
    assert gate.critical_failure(lax, {**lax_base, "critical": [_measured(9, 0)]}) == (
        "critical flagged = true: 9 held-out rows in the class, fewer than 10, so its miss rate is not shown to be at most 50.0%"
    )
    assert gate.critical_failure(lax, {**lax_base, "critical": [_measured(10, 0)]}) is None  # 27.7% <= 50%
    assert (
        ReleaseGate(Ledger(":memory:"), ReleasePolicy(min_task_test_n=40))
        .critical_failure(task, {**base, "critical": [_measured(35, 0)]})
        .startswith("critical flagged = true: 35 held-out rows in the class, fewer than 40")
    )
    unmeasured = "critical classes not measured under the current spec"
    for rec in (
        base,
        {**base, "critical": []},
        {**base, "critical": "broken"},
        {**base, "critical": [_measured(35, 0)], "spec_hash": "0" * 16},
    ):
        assert gate.critical_failure(task, rec) == unmeasured, rec
    for bad in ({**_measured(35, 0), "n": "35"}, {**_measured(35, 0), "misses": 36}, {**_measured(35, 0), "field": "x"}, "x"):
        assert gate.critical_failure(task, {**base, "critical": [bad]}) == "critical flagged = true: not measured", bad
    assert gate.critical_failure(tasks.get("ticket-triage"), base) is None  # no critical classes


@pytest.mark.req("FR-2.50", "FR-14.1")
def test_critical_task_without_measurements_stays_on_teacher():
    """The router serves a task with critical classes from the student only on a version whose record for it measured
    them: without one (no golden rows of it yet, a record written before, or the hosted untuned fallback model), the
    teacher answers. A shadow without such a record would not serve it either."""
    task = _register({"critical": [CRITICAL]})
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.8})
    r, student, _ = _router(led, _answer(False))
    out = r.handle(_ask(1), MOD)
    assert (out.route, out.served_by, out.escalated) == ("task_disabled", "teacher", True) and student.calls == []
    assert r.threshold(led.version(live), "student-v1", MOD) == SERVE_NOTHING
    assert r.handle(_ask(2), "ticket-triage").route == "confidence"  # other tasks: the global threshold as before
    # /ratchet/stats shows the threshold the router applies, also on a live eval without a global threshold.
    led.update_eval(live, {})
    by_task = {t["task"]: t["threshold"] for t in led.stats_by_task()}
    assert by_task == {MOD: SERVE_NOTHING, "ticket-triage": None}
    led.update_eval(live, {"threshold": 0.8})

    record = {"threshold": 0.9, "test_n": 120, "coverage": 1.0, "precision": 1.0, "spec_hash": task.spec_hash}
    unmeasured = _version(led, "live", {"threshold": 0.8, "tasks": {MOD: record}})  # a record without `critical`
    calls = len(student.calls)
    assert r.handle(_ask(3), MOD).route == "task_disabled" and len(student.calls) == calls
    assert r.threshold(led.version(unmeasured), "student-v2", MOD) == SERVE_NOTHING
    measured = _version(led, "live", {"threshold": 0.8, "tasks": {MOD: {**record, "critical": [_measured(40, 0)]}}})
    out = r.handle(_ask(4), MOD)
    assert (out.route, out.version) == ("student", measured) and r.threshold(led.version(measured), "x", MOD) == 0.9

    # The hosted untuned model never answers it, measured or not, so it is not sampled for it either.
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0)
    hosted = Ratchet(s, led, RecordingProvider(_answer(False)), lambda v: (student, s.fallback_student_model))
    calls = len(student.calls)
    out = hosted.handle(_ask(5), MOD)
    assert (out.route, out.served_by, out.escalated) == ("task_disabled", "teacher", True) and len(student.calls) == calls
    assert hosted.handle(_ask(51), "ticket-triage").route == "confidence" and len(student.calls) == calls + 1

    def broken(v: Any) -> Any:
        raise RuntimeError("resolver down")

    assert Ratchet(s, led, RecordingProvider(_answer(False)), broken).handle(_ask(52), MOD).route == "student_error"
    assert hosted.threshold(led.version(measured), s.fallback_student_model, MOD) == SERVE_NOTHING
    assert hosted.threshold(led.version(measured), s.fallback_student_model, "ticket-triage") == s.confidence_threshold
    led.update_eval(measured, {"threshold": 0.8})  # no record: held back, also for the hosted model
    calls = len(student.calls)
    assert hosted.handle(_ask(6), MOD).route == "task_disabled" and len(student.calls) == calls
    led.update_eval(measured, {"threshold": 0.8, "tasks": {MOD: {**record, "critical": [_measured(40, 0)]}}})

    shadow_student = RecordingProvider(_answer(False), CONFIDENT)
    _version(led, "shadow", {"threshold": 0.8}, adapter_uri="student-v9")
    r = Ratchet(
        s, led, RecordingProvider(_answer(False)), lambda v: (shadow_student if v.state == "shadow" else student, v.adapter_uri)
    )
    r.handle(_ask(7), MOD)
    assert (_last(led)["route"], _last(led)["shadow_serve"]) == ("student", 0)


@pytest.mark.req("FR-2.50", "FR-14.1")
def test_per_task_off_still_records_critical_tasks():
    """With RATCHET_PER_TASK_CALIBRATION=0 the eval writes no per-task records, except for a task with critical
    classes, which the critical gate needs."""
    _register({"critical": [CRITICAL]})
    tasks.register(tasks.Task("chat", source="file"))
    rows = _critical_rows(class_rows=40, misses=0)
    pooled = cascade_eval(_scripted, rows, per_task=False)
    assert set(pooled["tasks"]) == {MOD} and pooled["tasks"][MOD]["critical"] == [_measured(40, 0)]
    plain = cascade_eval(_scripted, [r for r in rows if r["task"] == "chat"], per_task=False)
    assert "tasks" not in plain


@pytest.mark.req("FR-2.50", "FR-14.1", "FR-6.1")
def test_per_task_off_critical_gate_disables_only_that_task():
    """With RATCHET_PER_TASK_CALIBRATION=0 only the critical task has a record, so the eval also gives the other tasks'
    held-out rows and how many the global threshold serves (`other_tasks`). The coverage the gate checks once it
    disables the critical task counts them: the version ships for its other tasks, as with per-task calibration on,
    and a critical task that passes with a low coverage of its own does not reject the version. A version whose other
    tasks serve too little is still rejected."""
    _register({"critical": [CRITICAL]})
    tasks.register(tasks.Task("chat", source="file"))
    hi = wilson(2, 40)[1]
    why = f"critical flagged = true: missed 2 of 40 held-out rows (5.0%, upper bound {hi:.1%} > 10.0%)"

    led = Ledger(":memory:")
    res = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2), per_task=False)
    assert set(res["tasks"]) == {MOD} and res["other_tasks"] == {"test_n": 120, "served": 120}
    cand = _version(led, "candidate", res)
    assert ReleaseGate(led).admit(cand)
    v = led.version(cand)
    assert v.state == "canary" and v.eval["tasks"][MOD]["disabled"] == why
    assert led.events()[0]["detail"] == f"v{cand} {MOD}: {why}; the teacher answers it, v{cand} ships for its other tasks"
    rejected = _version(led, "candidate", res)
    assert not ReleaseGate(led, ReleasePolicy(task_mode="reject")).admit(rejected)
    assert led.events()[-1]["detail"] == f"v{rejected}: task {MOD}: {why}"

    # The critical task passes, but its own threshold serves only 40 of its 200 held-out rows; chat's are all served.
    flagged, clean = _answer(True, "high"), _answer(False)
    rows = []
    for j in range(200):
        calib = flagged if j % 3 == 0 else clean
        held = _golden(MOD, f"t{j}", 0.98, flagged, flagged) if j < 40 else _golden(MOD, f"t{j}", 0.7, clean, clean)
        rows += [
            _golden(MOD, f"c{j}", 0.98, calib, calib),
            held,
            _golden("chat", f"cc{j}", 0.98, "fine", "fine"),
            _golden("chat", f"ct{j}", 0.98, "fine", "fine"),
        ]
    led = Ledger(":memory:")
    res = cascade_eval(_scripted, rows, per_task=False)
    assert res["tasks"][MOD]["coverage"] == pytest.approx(0.2) and res["other_tasks"] == {"test_n": 200, "served": 200}
    cand = _version(led, "candidate", res)
    assert ReleaseGate(led).task_failures(led.version(cand), None) == []
    assert ReleaseGate(led).admit(cand) and led.version(cand).state == "canary"

    # Chat's held-out rows are unsure: once the critical task is disabled the version would serve nothing.
    led = Ledger(":memory:")
    res = cascade_eval(_scripted, _critical_rows(class_rows=40, misses=2, chat_conf=0.7), per_task=False)
    assert res["coverage"] == pytest.approx(0.5) and res["other_tasks"] == {"test_n": 120, "served": 0}
    cand = _version(led, "candidate", res)
    assert not ReleaseGate(led).admit(cand)
    assert led.events()[-1]["detail"] == f"v{cand}: golden coverage 0.0% < 25% once {MOD} disabled"


@pytest.mark.req("FR-2.50", "FR-14.1", "FR-6.1")
def test_rules_apply_to_task_precision_and_coverage():
    """An answer an `escalate_when` rule sends to the teacher is never served, so the golden eval neither calibrates
    the task's threshold on it nor counts it in the precision and coverage the gate checks (in the task's record and
    in the pooled numbers). Here every flagged answer goes to the teacher: what the student serves is the unflagged
    answers, 45 of 50 right, so the task fails a 93% floor at coverage 0.5."""
    _register({"escalate_when": [FLAG_RULE]}, policy={"min_precision": 0.93})
    flagged, clean, wrong = _answer(True, "high"), _answer(False), _answer(False, "high")
    rows = []
    for j in range(100):
        calib = flagged if j % 2 else clean
        rows.append(_golden(MOD, f"c{j}", 0.98, calib, calib))
        if j < 50:  # flagged and right: a rule sends every one to the teacher
            rows.append(_golden(MOD, f"t{j}", 0.98, flagged, flagged))
        else:  # unflagged: wrong on 5 of 50
            rows.append(_golden(MOD, f"t{j}", 0.98, wrong if j < 55 else clean, clean))
    res = cascade_eval(_scripted, rows)
    rec = res["tasks"][MOD]
    assert (rec["threshold"], rec["precision"], rec["coverage"], rec["test_n"]) == (0.98, 0.9, 0.5, 100)
    assert (res["precision"], res["coverage"]) == (0.9, 0.5)  # one task: the pooled numbers are its own
    led = Ledger(":memory:")
    v = _version(led, "candidate", res)
    assert ReleaseGate(led).task_failures(led.version(v), None) == [(MOD, "golden precision 90.0% on 50 answers < 93.0%")]

    # The threshold is calibrated on the answers the rules let through: flagged answers that are wrong at 0.98 would
    # keep any threshold from reaching 97%, but the rule sends them to the teacher, so 0.9 serves the rest.
    rows = []
    for j in range(100):
        if j % 2:
            pair = [_golden(MOD, f"c{j}", 0.98, flagged, clean), _golden(MOD, f"t{j}", 0.98, flagged, clean)]
        else:
            pair = [_golden(MOD, f"c{j}", 0.9, clean, clean), _golden(MOD, f"t{j}", 0.9, clean, clean)]
        rows += pair
    rec = cascade_eval(_scripted, rows)["tasks"][MOD]
    assert (rec["threshold"], rec["precision"], rec["coverage"]) == (0.9, 1.0, 0.5)
    _register()  # without the rule nothing reaches 97%: the task serves nothing
    assert cascade_eval(_scripted, rows)["tasks"][MOD]["threshold"] == SERVE_NOTHING


@pytest.mark.req("FR-2.50", "FR-2.15", "INV-4")
def test_rescore_disables_failing_critical_task():
    """A night's re-score of the live version (the champion re-check, or a skip night's after a spec change) is held
    to the task's critical classes in the same write: a class added to a live task, or more misses on the growing
    golden set, takes the task off the version with a `task_disabled` event."""
    task = _register({"critical": [CRITICAL]})
    led = Ledger(":memory:", golden_fraction=0.3)
    record = {"threshold": 0.9, "test_n": 120, "coverage": 1.0, "precision": 1.0, "spec_hash": task.spec_hash}
    live = _version(
        led,
        "live",
        {"threshold": 0.8, "precision": 0.97, "coverage": 0.9, "tasks": {MOD: {**record, "critical": [_measured(40, 0)]}}},
        train_examples=0,
    )
    _examples(led, train=60, golden=10)
    recheck = {"threshold": 0.8, "precision": 0.97, "coverage": 0.9, "tasks": {MOD: {**record, "critical": [_measured(40, 3)]}}}
    rep = run_night(led, RecordingTrainer(), RecordingEvaluator(recheck), ReleaseGate(led), "base")
    assert rep.trained
    got = led.version(live).eval["tasks"][MOD]
    hi = wilson(3, 40)[1]
    why = f"critical flagged = true: missed 3 of 40 held-out rows (7.5%, upper bound {hi:.1%} > 10.0%)"
    assert (got["threshold"], got["disabled"], got["critical"]) == (SERVE_NOTHING, why, [_measured(40, 3)])
    assert led.has_event("task_disabled", f"v{live} {MOD}: {why}; the teacher answers it (re-scored)")

    # Without a gate the re-score keeps its old behaviour; a passing or pinned record is left alone.
    led2 = Ledger(":memory:")
    v = _version(led2, "live", {"threshold": 0.8})
    rescore(led2, led2.version(v), recheck, {})
    assert "disabled" not in led2.version(v).eval["tasks"][MOD]
    passing = {"tasks": {MOD: {**record, "critical": [_measured(40, 0)]}, "chat": {"threshold": 0.9}}}
    assert ReleaseGate(led2).critical_holds(passing) == [] and ReleaseGate(led2).critical_holds(None) == []
    _register({"critical": [CRITICAL]}, pinned=True)
    assert ReleaseGate(led2).critical_holds(recheck) == []


@pytest.mark.req("FR-2.50", "FR-5.2")
def test_cascade_eval_skips_ineligible_rows():
    """A golden row the router would keep from the student (stored with `store_ineligible`) is not sampled: it would
    calibrate a threshold for answers the student never gives. The task keeps its record."""
    _register({"student_max_input_tokens": 20, "store_ineligible": True})
    flagged = _answer(True)
    rows = [_golden(MOD, f"r{i}", 0.98, flagged, flagged) for i in range(10)]
    rows += [
        {
            **_golden(MOD, "long", 0.98, flagged, flagged),
            "messages": [{"role": "user", "content": "z" * 400}, {"role": "assistant", "content": flagged}],
        }
    ]
    seen: list[str] = []

    def sample(prompt: list[dict[str, Any]], n: int, params: Any = None) -> list[Completion]:
        seen.append(prompt[-1]["content"])
        return _scripted(prompt, n)

    res = cascade_eval(sample, rows)
    assert res["excluded_ineligible"] == 1 and res["golden_n"] == 10 and len(seen) == 10
    assert res["tasks"][MOD]["golden_n"] == 10
    assert "excluded_ineligible" not in cascade_eval(_scripted, rows[:10])


# ---------- the spec ----------
@pytest.mark.req("FR-2.50", "FR-2.15")
def test_rule_must_reference_declared_field():
    with pytest.raises(SpecError) as caught:
        taskspec.parse_spec({**SPEC, "routing": {"escalate_when": [{"field": "flaged", "equals": True, "action": "teacher"}]}})
    assert caught.value.errors == [
        "routing.escalate_when[0].field: 'flaged' is not a field of this task; its fields: flagged, severity"
    ]
    with pytest.raises(SpecError, match=r"critical\[0\]\.field: 'x' is not a field of this task; its fields: none"):
        taskspec.parse_spec({"name": "free", "routing": {"critical": [{"field": "x", "equals": 1, "max_miss_rate": 0.1}]}})
    with pytest.raises(ValueError, match=r"task 'moderation': routing\.escalate_when\[0\]\.field: 'nope' is not a field"):
        tasks.Task(
            MOD,
            fields=tasks.get("ticket-triage").fields,
            routing={"escalate_when": [{"field": "nope", "equals": 1, "action": "teacher"}]},
        )
    # A field whose own spec is bad is reported once, without a second error for the rule that names it.
    with pytest.raises(SpecError) as caught:
        taskspec.parse_spec(
            {**SPEC, "fields": {"flagged": {"type": "bool", "bogus": 1}}, "routing": {"escalate_when": [FLAG_RULE]}}
        )
    assert [e.split(":")[0] for e in caught.value.errors] == ["fields.flagged.bogus"]


ERRORS = [
    (
        {"rules": []},
        "routing.rules: unknown key; use student_max_input_tokens, student_content_types, store_ineligible, escalate_when, "
        "critical",
    ),
    ({"audit_rate": 0.2}, "(a task's audit rate is policy.audit_rate)"),
    ({"student_max_input_tokens": 0}, "routing.student_max_input_tokens: must be a whole number from 1 to 10000000"),
    ({"student_max_input_tokens": 1.5}, "routing.student_max_input_tokens: must be a whole number"),
    ({"student_max_input_tokens": True}, "routing.student_max_input_tokens: must be a whole number"),
    ({"student_content_types": "text"}, "routing.student_content_types: must be a list of content part types"),
    ({"student_content_types": ["text", "video"]}, "routing.student_content_types[1]: 'video' is not a content part type"),
    (
        {"student_content_types": ["text", "image_url"]},
        "'image_url' is reserved for a later release; the student reads text only",
    ),
    ({"student_content_types": []}, "routing.student_content_types: must include 'text'"),
    ({"student_content_types": ["text", "text"]}, "routing.student_content_types: names a type twice"),
    ({"store_ineligible": "yes"}, "routing.store_ineligible: must be true or false"),
    ({"escalate_when": {}}, "routing.escalate_when: must be a list"),
    ({"escalate_when": [FLAG_RULE] * 33}, "routing.escalate_when: at most 32 entries"),
    ({"escalate_when": ["flagged"]}, "routing.escalate_when[0]: must be an object with a field and equals or in"),
    ({"escalate_when": [{"equals": True, "action": "teacher"}]}, "routing.escalate_when[0].field: missing; name one of"),
    ({"escalate_when": [{"field": 3, "equals": True, "action": "teacher"}]}, "routing.escalate_when[0].field: must be the path"),
    ({"escalate_when": [{"field": "flagged", "action": "teacher"}]}, "routing.escalate_when[0]: missing equals or in"),
    ({"escalate_when": [{**FLAG_RULE, "in": [True]}]}, "routing.escalate_when[0]: give equals or in, not both"),
    ({"escalate_when": [{**FLAG_RULE, "equals": [True]}]}, "routing.escalate_when[0].equals: must be a string, number"),
    (
        {"escalate_when": [{"field": "flagged", "in": [], "action": "teacher"}]},
        "routing.escalate_when[0].in: must be a non-empty list",
    ),
    (
        {"escalate_when": [{"field": "flagged", "in": [{}], "action": "teacher"}]},
        "routing.escalate_when[0].in: must hold strings",
    ),
    (
        {"escalate_when": [{"field": "flagged", "in": [True] * 257, "action": "teacher"}]},
        "routing.escalate_when[0].in: at most 256",
    ),
    (
        {"escalate_when": [{"field": "severity", "in": ["low", "urgent"], "action": "teacher"}]},
        "routing.escalate_when[0].in[1]: 'urgent' is not a value of field 'severity' (label)",
    ),
    (
        {"escalate_when": [{"field": "flagged", "equals": True}]},
        "routing.escalate_when[0]: missing action ('teacher') or min_confidence",
    ),
    (
        {"escalate_when": [{**FLAG_RULE, "min_confidence": 0.9}]},
        "routing.escalate_when[0]: give action or min_confidence, not both",
    ),
    ({"escalate_when": [{**FLAG_RULE, "action": "student"}]}, "routing.escalate_when[0].action: must be 'teacher'"),
    (
        {"escalate_when": [{"field": "flagged", "equals": True, "min_confidence": 1.5}]},
        "routing.escalate_when[0].min_confidence: must be a number from 0 to 1",
    ),
    (
        {"escalate_when": [{"field": "flagged", "equals": True, "min_confidence": float("nan")}]},
        "routing: must hold finite numbers",
    ),
    (
        {"escalate_when": [{**FLAG_RULE, "max_miss_rate": 0.1}]},
        "routing.escalate_when[0].max_miss_rate: unknown key; use field, equals, in, action, min_confidence",
    ),
    ({"critical": [{"field": "flagged", "equals": True}]}, "routing.critical[0].max_miss_rate: missing"),
    (
        {"critical": [{**CRITICAL, "max_miss_rate": 0}]},
        "routing.critical[0].max_miss_rate: must be a number more than 0 and below 1",
    ),
    (
        {"critical": [{**CRITICAL, "max_miss_rate": 1}]},
        "routing.critical[0].max_miss_rate: must be a number more than 0 and below 1",
    ),
    (
        {"critical": [{**CRITICAL, "action": "teacher"}]},
        "routing.critical[0].action: unknown key; use field, equals, in, max_miss_rate",
    ),
]


@pytest.mark.req("FR-2.50", "FR-2.15")
@pytest.mark.parametrize(("block", "message"), ERRORS)
def test_routing_spec_errors_name_their_path(block, message):
    with pytest.raises(SpecError) as caught:
        taskspec.parse_spec({**SPEC, "routing": block})
    assert any(message in e for e in caught.value.errors), caught.value.errors
    with pytest.raises(ValueError, match=r"^task 'moderation': routing\."):  # the Task checks the block again
        tasks.Task(MOD, fields=taskspec.parse_spec(SPEC).fields, routing=block)


@pytest.mark.req("FR-2.50", "FR-2.15")
def test_routing_block_round_trips_and_extends_merges_it():
    """A valid block keeps its spelling in the stored spec (and its hash), its values normalised once for the request
    path; `extends` merges a routing block by key, null removing the base's entry. A condition may only compare a
    scalar field."""
    block = {
        "student_max_input_tokens": 1800,
        "escalate_when": [{"field": "severity", "in": ["HIGH"], "action": "teacher"}],
        "critical": [CRITICAL],
    }
    task = _register(block)
    assert taskspec.document(task)["routing"] == block
    assert taskspec.parse_spec(taskspec.document(task)).spec_hash == task.spec_hash != tasks.get("ticket-triage").spec_hash
    rules = task.routing_rules
    assert (rules.max_input_tokens, rules.store_ineligible, len(rules.rules), len(rules.critical)) == (1800, False, 1, 1)
    assert rules.rules[0].condition.values == ("high",) and rules.rules[0].condition.label() == 'severity in ["HIGH"]'
    assert rules.critical[0].condition.label() == "flagged = true" and rules.critical[0].max_miss_rate == 0.1
    child = taskspec.parse_spec(
        {"name": "strict-mod", "extends": MOD, "routing": {"student_max_input_tokens": None, "store_ineligible": True}}
    )
    assert dict(child.routing) == {k: v for k, v in block.items() if k != "student_max_input_tokens"} | {"store_ineligible": True}
    assert child.routing_rules.max_input_tokens is None and child.routing_rules.critical == rules.critical
    # A child that drops the field its inherited rule names is refused.
    with pytest.raises(SpecError, match=r"routing\.critical\[0\]\.field: 'flagged' is not a field of this task"):
        taskspec.parse_spec({"name": "x", "extends": MOD, "fields": {"flagged": None}})
    listy = {"name": "tags", "fields": {"tags": {"type": "set", "item": "label"}}}
    with pytest.raises(SpecError, match=r"'tags' is a set field; a condition compares a field of type bool, date"):
        taskspec.parse_spec({**listy, "routing": {"critical": [{"field": "tags", "equals": "a", "max_miss_rate": 0.1}]}})
    assert tasks.get("ticket-triage").routing_rules is routing.DEFAULT
    # The parser itself refuses what JSON cannot hold, for a Task built in code.
    nan = {"escalate_when": [{"field": "flagged", "equals": True, "min_confidence": float("nan")}]}
    assert routing.parse(nan, task.fields) == (routing.DEFAULT, ["escalate_when[0].min_confidence: must be a number from 0 to 1"])
    assert routing.parse([], task.fields) == (routing.DEFAULT, [])  # empty: no block
    assert routing.parse(["x"], task.fields) == (routing.DEFAULT, ["must be an object"])  # type: ignore[arg-type]
    # With the fields themselves in error, names and values are not checked against them, and no Routing is built.
    assert routing.parse({"critical": [{"field": "nope", "equals": 1, "max_miss_rate": 0.1}]}, (), False) == (routing.DEFAULT, [])
