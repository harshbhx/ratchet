"""Regression tests for issues found while documenting v0.1."""

import sqlite3

import httpx
import pytest

from ratchet import tasks
from ratchet.config import Settings
from ratchet.ledger import DAY_SECONDS, Ledger
from ratchet.providers import Completion
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet


class FakeTeacher:
    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None):
        if self.fail:
            raise httpx.ConnectError("teacher down")
        return [
            Completion(
                text='{"category": "billing", "priority": "low"}', model=model, in_tokens=50, out_tokens=20, latency_ms=1000
            )
        ]


class BrokenStudent:
    def complete(self, *a, **k):
        raise httpx.ConnectError("endpoint down")


def test_old_database_file_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT);
        CREATE TABLE examples (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE);
        CREATE TABLE versions (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT);
    """)
    con.commit()
    con.close()
    led = Ledger(str(db))
    cols = {r["name"] for r in led.db.execute("PRAGMA table_info(versions)")}
    assert "stage_since" in cols
    assert "seen" in {r["name"] for r in led.db.execute("PRAGMA table_info(examples)")}


def test_auto_day_counts_days_from_first_request():
    led = Ledger(":memory:", auto_day=True)
    t0 = 1_800_000_000.0
    led.log_request(ts=t0, task="t", served_by="teacher", cost_usd=0, baseline_cost_usd=0)
    led.log_request(ts=t0 + DAY_SECONDS + 5, task="t", served_by="teacher", cost_usd=0, baseline_cost_usd=0)
    assert [d["day"] for d in led.stats_by_day()] == [1, 2]


def test_student_failure_falls_back_to_teacher():
    led = Ledger(":memory:")
    vid = led.create_version("b", "student-v1", 10, "h", {"threshold": 0.5}, state="live")
    r = Ratchet(Settings(), led, FakeTeacher(), lambda v: (BrokenStudent(), v.adapter_uri), seed=1)
    out = r.handle([{"role": "user", "content": "x"}], "ticket-triage")
    assert out.served_by == "teacher" and out.escalated
    assert any(e["kind"] == "student_error" for e in led.events())
    assert vid == 1


def test_fallback_model_uses_default_threshold():
    s = Settings()
    led = Ledger(":memory:")
    led.create_version("b", "student-v1", 10, "h", {"threshold": 0.2}, state="live")
    r = Ratchet(s, led, FakeTeacher(), lambda v: (None, s.fallback_student_model))
    v = led.live()
    assert r.threshold(v, s.fallback_student_model) == s.confidence_threshold
    assert r.threshold(v, "student-v1") == 0.2


def test_teacher_failure_returns_502(monkeypatch):
    from fastapi.testclient import TestClient

    import ratchet.gateway as gw

    gw.rt.ratchet.teacher = FakeTeacher(fail=True)
    gw.rt.ratchet.resolve_student = lambda v: (BrokenStudent(), "x")
    c = TestClient(gw.app)
    resp = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 502
    gw.rt.reset()


def test_unknown_tasks_do_not_grow_registry():
    before = len(tasks.REGISTRY)
    for i in range(50):
        tasks.get(f"client-task-{i}")
    tasks.get("bad name with spaces!")
    assert len(tasks.REGISTRY) == before
    assert tasks.get("bad name with spaces!").name == "default"


def test_live_alert_fires_once_per_version():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=5))
    vid = led.create_version("b", "a", 1, "h", {}, state="live")
    for _ in range(10):
        led.log_request(
            task="t", served_by="student", student_version=vid, audited=1, audit_agree=0, cost_usd=0, baseline_cost_usd=0
        )
    for _ in range(5):
        gate.tick()
    assert sum(e["kind"] == "alert" for e in led.events()) == 1


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("RATCHET_MIN_AUDITS", "42")
    monkeypatch.setenv("RATCHET_LIVE_FLOOR", "0.9")
    p = ReleasePolicy.from_env()
    assert p.min_audits == 42 and p.live_floor == pytest.approx(0.9)
