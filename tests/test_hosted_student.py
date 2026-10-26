"""The hosted student (FR-12.33, ADR 0146): the untuned Nano 30B on Token Factory answering the tasks it was calibrated
for while no version is live. Offline: `RecordingProvider` and `FakeOpenAIServer`, never a real call."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet import hosted, tasks
from ratchet.confidence import SERVE_NOTHING
from ratchet.config import ERROR, WARNING, Settings
from ratchet.ledger import HOSTED_STATE, Ledger
from ratchet.router import Ratchet
from ratchet.scout.prompts import read_messages, triage_messages

ROOT = Path(__file__).resolve().parents[1]
CHAT = "/v1/chat/completions"
TF = "https://teacher.test/v1"  # Token Factory
ENDPOINT = "https://student.test/v1"  # a vLLM endpoint
TRIAGE_ANSWER = json.dumps({"relevant": True, "source_type": "docs", "reason": "It defines the term."})
READ_ANSWER = json.dumps({"answers_question": True, "fact_type": "definition", "key_fact": "LoRA trains low-rank matrices."})
TRIAGE_MSGS = triage_messages("What is LoRA?", "LoRA", "https://example.org/lora", "LoRA freezes the weights.")
READ_MSGS = read_messages("What is LoRA?", "LoRA", "https://example.org/lora", "LoRA freezes the weights.")


def _script():
    spec = importlib.util.spec_from_file_location("hosted_nano_calibration", ROOT / "scripts" / "hosted_nano_calibration.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hosted_nano_calibration"] = mod
    spec.loader.exec_module(mod)
    return mod


def _serving_tasks(threshold: float = 0.5) -> MappingProxyType:
    """The shipped records with source-triage serving at `threshold` (as if it had met the bar); page-read as shipped."""
    triage = {k: v for k, v in hosted.TASKS["source-triage"].items() if k not in ("disabled", "measured_threshold")}
    return MappingProxyType({**hosted.TASKS, "source-triage": MappingProxyType({**triage, "threshold": threshold})})


def _answer(messages: list[dict[str, Any]], model: str) -> str:
    return READ_ANSWER if "source excerpt" in messages[0]["content"] else TRIAGE_ANSWER


def _router(audit_rate: float = 0.0, **settings: Any) -> tuple[Ledger, Ratchet, RecordingProvider, RecordingProvider]:
    led = Ledger(":memory:")
    s = Settings(
        teacher_model=OPEN_TEACHER,
        fallback_student_model=hosted.MODEL,
        hosted_student=True,
        samples=3,
        audit_rate=audit_rate,
        teacher_reasoning_tokens=0,
        **settings,
    )
    teacher, nano, endpoint = RecordingProvider(_answer), RecordingProvider(_answer), RecordingProvider(_answer)

    def resolve(v):
        return (nano, hosted.MODEL) if v.state == HOSTED_STATE else (endpoint, v.adapter_uri)

    return led, Ratchet(s, led, teacher, resolve, seed=1), teacher, nano


def _hosted_eval(threshold: float = 0.5) -> dict[str, Any]:
    ev = hosted.eval_record()
    ev["tasks"]["source-triage"] = dict(_serving_tasks(threshold)["source-triage"])
    return ev


def _row(led: Ledger) -> dict[str, Any]:
    with led.locked():
        return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


# ---------- the records ----------
@pytest.mark.req("FR-12.33")
def test_hosted_records_follow_from_the_evidence():
    """`ratchet/hosted.py` holds exactly what the golden eval computes from evidence/hosted-nano (no network): the
    thresholds come from real data, never by hand. Today neither task met the bar."""
    mod = _script()
    assert mod.check() == 0
    rows = mod.load()
    assert {r["task"] for r in rows} == {"source-triage", "page-read"}
    assert all(r["student"]["model"] == hosted.MODEL and len(r["student"]["samples"]) == hosted.SAMPLES for r in rows)
    assert hosted.served_tasks(hosted.eval_record()) == {}
    for name, rec in hosted.TASKS.items():
        assert rec["spec_hash"] == tasks.get(name).spec_hash  # measured under the built-in specs served now
        assert rec["threshold"] == SERVE_NOTHING and rec["disabled"]


@pytest.mark.req("FR-12.33")
def test_hosted_gate_needs_precision_on_enough_served_rows():
    """A task serves only at MIN_PRECISION held-out precision on MIN_SERVED served rows; otherwise it is disabled and
    keeps its measured threshold for the record."""
    good = {"threshold": 0.9, "precision": 0.95, "coverage": 0.5, "test_n": 40}
    assert hosted.gate(good) == good  # 20 served rows at 0.95
    for bad in ({**good, "precision": 0.949}, {**good, "test_n": 38}, {**good, "threshold": SERVE_NOTHING}):
        rec = hosted.gate(bad)
        assert rec["threshold"] == SERVE_NOTHING and rec["measured_threshold"] == bad["threshold"] and rec["disabled"]
    assert hosted.served_tasks({"tasks": {"a": good, "b": hosted.gate({**good, "test_n": 2})}}) == {"a": 0.9}


# ---------- the router ----------
@pytest.mark.req("FR-12.33", "FR-12.32")
def test_hosted_student_answers_only_its_tasks():
    """The hosted row answers a task its record serves, at that task's threshold, and is never sampled for any other
    (route task_disabled: the teacher answers and its answer is stored as on a cold start). Its answers are no
    version's (ADR 0073): the result names the model and no version, and no unattributed event is written."""
    led, r, teacher, nano = _router()
    v, change = led.ensure_hosted(hosted.MODEL, _hosted_eval(0.5))
    assert (v.state, v.adapter_uri, v.train_examples, change) == (HOSTED_STATE, "", 0, "created")

    res = r.handle(TRIAGE_MSGS, "source-triage")
    assert (res.served_by, res.route, res.version, res.model) == ("student", "student", None, hosted.MODEL)
    row = _row(led)
    assert (row["student_version"], row["student_model"], row["threshold"], row["stored"]) == (v.id, hosted.MODEL, 0.5, 0)
    (call,) = nano.calls
    assert call.kwargs["n"] == hosted.SAMPLES and call.kwargs["params"].template_kwargs == {"enable_thinking": False}

    for msgs, task in ((READ_MSGS, "page-read"), ([{"role": "user", "content": "In one sentence, what is LoRA?"}], "default")):
        res = r.handle(msgs, task)
        assert (res.served_by, res.route) == ("teacher", "task_disabled")
    assert len(nano.calls) == 1  # never sampled for a task it does not answer
    assert [e["task"] for e in led.examples()] == ["page-read", "default"]  # the teacher's answers, as on a cold start
    assert not [e for e in led.events() if e["kind"] == "unattributed"]
    assert led.version_live_quality(v.id) == (0, None)


@pytest.mark.req("FR-12.33", "INV-2")
def test_hosted_answers_are_audited_and_never_stored():
    """Audits run as drawn: the teacher's answer is stored and judges the hosted answer, the hosted answer itself is
    never training data (INV-2), and no release statistic counts the audit (it is not an adapter's answer)."""
    led, r, teacher, nano = _router(audit_rate=1.0)
    v, _ = led.ensure_hosted(hosted.MODEL, _hosted_eval(0.5))
    res = r.handle(TRIAGE_MSGS, "source-triage")
    assert res.served_by == "student" and res.audited
    row = _row(led)
    assert (row["audited"], row["audit_agree"], row["stored"]) == (1, 1, 1)
    (ex,) = led.examples()
    assert (ex["teacher_model"], ex["task"]) == (OPEN_TEACHER, "source-triage")
    assert json.loads(ex["completion"]) == json.loads(TRIAGE_ANSWER)  # the teacher's answer, never the student's
    assert led.version_live_quality(v.id) == (0, None)


@pytest.mark.req("FR-12.33")
def test_hosted_student_stands_in_only_while_nothing_is_live():
    """A live version answers before the hosted row; with the switch off, or the student turned off, the teacher
    answers as on a cold start."""
    led, r, teacher, nano = _router()
    led.ensure_hosted(hosted.MODEL, _hosted_eval(0.5))
    assert r._pick_version().state == HOSTED_STATE
    r.s.hosted_student = False
    assert r._pick_version() is None
    assert r.handle(TRIAGE_MSGS, "source-triage").route == "cold_start"
    r.s.hosted_student = True
    led.set_flag("student_disabled", True, "cli:test")
    assert r._pick_version() is None
    led.set_flag("student_disabled", None, "cli:test")
    live = _version(led, "live", {"threshold": 0.5})
    assert r._pick_version().id == live
    assert nano.calls == []


@pytest.mark.req("FR-12.33")
def test_hosted_row_follows_the_records():
    """One row per ledger, brought up to date when the records change, never moved by a release."""
    led = Ledger(":memory:")
    first, change = led.ensure_hosted(hosted.MODEL, _hosted_eval(0.5))
    assert change == "created"
    assert led.ensure_hosted(hosted.MODEL, _hosted_eval(0.5)) == (first, None)
    again, change = led.ensure_hosted(hosted.MODEL, _hosted_eval(0.6))
    assert (again.id, change, again.eval["tasks"]["source-triage"]["threshold"]) == (first.id, "updated", 0.6)
    assert [v.state for v in led.versions()] == [HOSTED_STATE] and led.live() is None


# ---------- the gateway ----------
def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": TF,
        "teacher_api_key": "tf-key",
        "teacher_model": OPEN_TEACHER,
        "fallback_student_model": hosted.MODEL,
        "hosted_student": True,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "teacher_reasoning_tokens": 0,
        "tavily_api_key": "",
        "api_keys": "",
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,
        "stats_ttl_s": 0.0,
        "mock_warmup_days": 0,
    }
    return Settings(**{**base, **overrides})


def _tf(body: dict[str, Any]) -> str:
    return _answer(body["messages"], body["model"])


@pytest.mark.req("FR-12.33", "FR-7.40")
def test_live_gateway_with_the_shipped_records_answers_with_the_teacher(gw_env, tmp_path, monkeypatch):
    """With the records as measured (no task met the bar) the switch makes the row and says why, the pages name no
    student, and every request is the teacher's: the hosted model is never called."""
    gw = gw_env()
    tf = FakeOpenAIServer(_tf)
    rt = gw.Runtime(_settings(tmp_path), transports={"teacher": tf.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    (event,) = [e for e in rt.ledger.events() if e["kind"] == "hosted"]
    assert "answers no task" in event["detail"] and "source-triage: held-out precision 1.000 on 7" in event["detail"]
    stats = c.get("/ratchet/stats").json()
    assert stats["student_serving"]["kind"] == "none" and stats["versions"] == []
    body = c.post(CHAT, json={"messages": TRIAGE_MSGS}, headers={"X-Ratchet-Task": "source-triage"}).json()
    assert body["ratchet"]["served_by"] == "teacher" and _row(rt.ledger)["route"] == "task_disabled"
    assert {call.json["model"] for call in tf.calls} == {OPEN_TEACHER}


@pytest.mark.req("FR-12.33", "FR-7.40", "FR-12.32")
def test_live_gateway_serves_a_task_that_met_the_bar(gw_env, tmp_path, monkeypatch):
    """A task whose record serves goes to the hosted Nano on Token Factory first, also with a student endpoint set (the
    row has no adapter to load there); the response names the model and no version, and `student_serving` says the
    hosted model answers, with each task's threshold."""
    monkeypatch.setattr(hosted, "TASKS", _serving_tasks(0.5))
    gw = gw_env()
    tf, endpoint = FakeOpenAIServer(_tf), FakeOpenAIServer(_tf)
    settings = _settings(tmp_path, student_base=ENDPOINT, student_api_key="sk-endpoint")
    rt = gw.Runtime(settings, transports={"teacher": tf.transport, "student": endpoint.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    serving = c.get("/ratchet/stats").json()["student_serving"]
    assert (serving["kind"], serving["model"], serving["threshold"], serving["certified"]) == (
        "hosted_fallback",
        hosted.MODEL,
        0.5,
        False,
    )
    assert serving["tasks"] == {"source-triage": 0.5}
    body = c.post(CHAT, json={"messages": TRIAGE_MSGS}, headers={"X-Ratchet-Task": "source-triage"}).json()
    assert (body["ratchet"]["served_by"], body["model"], body["ratchet"]["student_version"]) == ("student", hosted.MODEL, None)
    assert [call.json["model"] for call in tf.calls] == [hosted.MODEL] and endpoint.calls == []
    assert c.get("/ratchet/stats").json()["versions"] == []  # no trained version to list


# ---------- configuration ----------
@pytest.mark.req("FR-12.33", "FR-12.30")
def test_hosted_student_config_checks(tmp_path):
    """The records hold for one model, sample count and thinking mode, so any other stops startup; in mock mode the
    switch is a warning and does nothing."""

    def issues(**overrides: Any) -> set[tuple[str, str]]:
        return {(i.level, i.var) for i in _settings(tmp_path, **overrides).validate({}) if "HOSTED" in i.message}

    assert issues() == set()
    assert (ERROR, "RATCHET_FALLBACK_STUDENT") in issues(fallback_student_model="other/model")
    assert (ERROR, "RATCHET_SAMPLES") in issues(samples=5)
    assert (ERROR, "RATCHET_FALLBACK_STUDENT_REASONING") in issues(fallback_student_reasoning="default")
    assert issues(mode="mock") == {(WARNING, "RATCHET_HOSTED_STUDENT")}
