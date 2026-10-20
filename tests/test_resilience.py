"""Upstream failures on the request path (NFR-2, FR-2.10, INV-7, INV-15).

A failing student falls back to the teacher (`tests/test_hardening.py`). Here: the optional calls around a request the
student already answered, the teacher's audit and its shadow arbitration, and the shadow's own samples. Each failure is
counted (`Ratchet.health()`), logged as a throttled `audit_error` or `shadow_error` event that names the exception
class (and HTTP status) but never its message, and the student's answer is served. A teacher failure on the
escalation path is still an error: there is no answer to serve, so the gateway returns 502.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.router import Ratchet

A = json.dumps({"category": "billing", "priority": "high"})
B = json.dumps({"category": "refund", "priority": "high"})
TRIAGE = "ticket-triage"
CHAT = "/v1/chat/completions"
# httpx puts the URL in its messages; an event must never repeat it (it can hold a host, a path or a query token).
TEACHER_URL = "https://teacher.test/v1/chat/completions?token=do-not-log"
NO_ERRORS = {"student_errors": 0, "audit_errors": 0, "shadow_errors": 0}


def _ask(tag: object) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice ({tag})"}]


def _timeout(message: str = "") -> httpx.ReadTimeout:
    return httpx.ReadTimeout(f"timed out reading {TEACHER_URL}")


def _unavailable(message: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", TEACHER_URL)
    response = httpx.Response(503, request=request)
    return httpx.HTTPStatusError(
        f"Server error '503 Service Unavailable' for url '{TEACHER_URL}'", request=request, response=response
    )


def _unreadable(messages: Any, model: str) -> str:
    raise ValueError(f"Expecting value: the body from {TEACHER_URL} is not JSON")


def _last_request(led: Ledger) -> dict[str, Any]:
    return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


def _events(led: Ledger) -> list[tuple[str, str]]:
    return [(e["kind"], e["detail"]) for e in led.events()]


def _cascade(teacher: RecordingProvider, audit_rate: float) -> tuple[Ledger, int, Settings, Ratchet]:
    """A live version with a calibrated threshold of 0.5, which its unanimous student (confidence 0.98) clears."""
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    student = RecordingProvider(A)
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=audit_rate, teacher_reasoning_tokens=0)
    return led, live, s, Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri), seed=1)


# ---------- the audit ----------
@pytest.mark.req("FR-2.10", "INV-15", "NFR-2")
@pytest.mark.parametrize(
    ("failure", "label"),
    [("timeout", "ReadTimeout"), ("http_503", "HTTPStatusError 503"), ("bad_body", "ValueError")],
)
def test_audit_failure_still_serves_the_student(failure, label):
    teacher = {
        "timeout": lambda: RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout),
        "http_503": lambda: RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_unavailable),
        "bad_body": lambda: RecordingProvider(_unreadable),  # not an httpx error at all: still only an audit
    }[failure]()
    led, live, s, r = _cascade(teacher, audit_rate=1.0)

    out = r.handle(_ask(1), TRIAGE)
    assert (out.served_by, out.version, out.text, out.escalated) == ("student", live, A, False)
    assert len(teacher.calls) == 1  # the audit was tried, once
    row = _last_request(led)
    assert (row["served_by"], row["audited"], row["audit_agree"], row["shadow_version"]) == ("student", 0, None, None)
    # Nothing is billed for the failed call; the all-teacher baseline is the estimate used when no audit ran.
    samples = s.price("student").cost(10, 5 * 3)
    assert out.cost_usd == pytest.approx(samples) and row["cost_usd"] == pytest.approx(samples)
    assert out.baseline_usd == pytest.approx(s.price(OPEN_TEACHER).cost(10, 5)) == row["baseline_cost_usd"]
    assert led.examples() == []  # no teacher answer, so nothing to train on
    assert _events(led) == [("audit_error", f"{TRIAGE}: {label}")]  # the task and the class, never the message
    assert "teacher.test" not in led.events()[0]["detail"] and "do-not-log" not in led.events()[0]["detail"]
    assert r.health() == {**NO_ERRORS, "audit_errors": 1}


@pytest.mark.req("FR-2.10", "INV-15", "INV-7", "FR-1.8")
def test_audit_errors_are_counted_and_throttled_and_escalation_still_fails():
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, live, s, r = _cascade(teacher, audit_rate=1.0)
    for i in range(12):
        assert r.handle(_ask(i), TRIAGE).served_by == "student"
    assert r.health() == {**NO_ERRORS, "audit_errors": 12}
    assert _events(led) == [("audit_error", f"{TRIAGE}: ReadTimeout")] * 2  # the 1st and the 10th, not all twelve
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE audited=1").fetchone()[0] == 0

    # The teacher is back: audits count again, and their answers are training data again.
    teacher.raise_on.clear()
    r.handle(_ask("recovered"), TRIAGE)
    row = _last_request(led)
    assert (row["audited"], row["audit_agree"]) == (1, 0) and len(led.examples()) == 1
    assert r.health()["audit_errors"] == 12

    # INV-7: an unsure student needs the teacher's answer, so a teacher failure there is still an error (502).
    teacher.raise_on.add(OPEN_TEACHER)
    led.update_eval(live, {"threshold": 0.99})
    with pytest.raises(httpx.ReadTimeout):
        r.handle(_ask("unsure"), TRIAGE)
    assert r.health() == {**NO_ERRORS, "audit_errors": 12}  # an escalation is not an optional call


@pytest.mark.req("FR-2.10")
def test_failure_counters_are_exact_under_threads():
    """Scout calls handle from several threads; every failure is counted once and the events stay throttled."""
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, live, s, r = _cascade(teacher, audit_rate=1.0)
    with ThreadPoolExecutor(8) as pool:
        served = list(pool.map(lambda i: r.handle(_ask(i), TRIAGE).served_by, range(200)))
    assert served == ["student"] * 200
    assert r.health() == {**NO_ERRORS, "audit_errors": 200}
    assert _events(led) == [("audit_error", f"{TRIAGE}: ReadTimeout")] * 3  # the 1st, 10th and 100th
    assert led.db.execute("SELECT COUNT(*), SUM(audited) FROM requests").fetchone()[:] == (200, 0)


# ---------- the shadow ----------
@pytest.mark.req("FR-2.10", "INV-15", "FR-6.3")
def test_shadow_arbitration_failure_is_silent():
    def teacher_says(messages: Any, model: str) -> str:
        if "the teacher times out" in messages[-1]["content"]:
            raise _timeout()
        return B

    teacher = RecordingProvider(teacher_says, in_tokens=50, out_tokens=20)
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    cand = _version(led, "shadow", {"threshold": 0.5})
    students = {live: RecordingProvider(A), cand: RecordingProvider(B)}  # the shadow would serve B where live serves A
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0)
    r = Ratchet(s, led, teacher, lambda v: (students[v.id], v.adapter_uri), seed=1)
    samples = s.price("student").cost(10, 5 * 3)

    r.handle(_ask("arbitrated"), TRIAGE)  # the teacher sides with the shadow
    assert led.shadow_quality(cand) == {
        "n": 1,
        "served": 1,
        "precision": 1.0,
        "coverage": 1.0,
        "live_coverage": 1.0,
        "unarbitrated": 0,
    }

    out = r.handle([{"role": "user", "content": "the teacher times out"}], TRIAGE)
    assert (out.served_by, out.version, out.text) == ("student", live, A)  # the user never notices
    assert len(teacher.calls) == 2
    row = _last_request(led)
    # The shadow's verdict is known (it would serve); whether it was right is not.
    assert (row["shadow_version"], row["shadow_serve"], row["shadow_agree"], row["audited"]) == (cand, 1, None, 0)
    assert out.cost_usd == pytest.approx(2 * samples) == row["cost_usd"]  # live and shadow samples; no teacher bill
    assert len(led.examples()) == 1  # only the first arbitration's answer
    assert _events(led) == [("shadow_error", f"v{cand} arbitration: ReadTimeout")]
    assert r.health() == {**NO_ERRORS, "shadow_errors": 1}
    # It counts in n and coverage, and as a disagreement in precision, so a failing teacher cannot flatter the shadow.
    s_q = led.shadow_quality(cand)
    assert (s_q["n"], s_q["served"], s_q["unarbitrated"], s_q["precision"], s_q["coverage"]) == (2, 2, 1, 0.5, 1.0)


@pytest.mark.req("FR-2.10", "INV-15", "FR-6.3", "NFR-2")
def test_shadow_sampling_error_counted():
    teacher = RecordingProvider(B)
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    cand = _version(led, "shadow", {"threshold": 0.5})
    down = RecordingProvider(B, raise_on={"student-v2"}, exc=_timeout)
    students = {live: RecordingProvider(A), cand: down}
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0)
    r = Ratchet(s, led, teacher, lambda v: (students[v.id], v.adapter_uri), seed=1)
    samples = s.price("student").cost(10, 5 * 3)

    for i in range(10):
        out = r.handle(_ask(i), TRIAGE)
        assert (out.served_by, out.text) == ("student", A) and out.cost_usd == pytest.approx(samples)
    assert len(down.calls) == 10 and teacher.calls == []
    # No verdict, so no shadow record: the shadow's statistics are untouched.
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE shadow_version IS NOT NULL").fetchone()[0] == 0
    assert led.shadow_quality(cand)["n"] == 0
    assert _events(led) == [("shadow_error", f"v{cand} sampling: ReadTimeout")] * 2  # the 1st and the 10th
    assert r.health() == {**NO_ERRORS, "shadow_errors": 10}


# ---------- through the gateway, in live mode ----------
def _live_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "tf-teacher-api-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "https://student.test/v1",
        "student_api_key": "sk-student-endpoint-token",
        "samples": 3,
        "audit_rate": 1.0,
        "redact_pii": True,
        "api_keys": "",
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
    }
    return Settings(**{**base, **overrides})


@pytest.mark.req("FR-1.8", "FR-2.10", "INV-7", "INV-15", "NFR-2")
def test_optional_teacher_failures_through_the_gateway(gw_env, tmp_path, monkeypatch):
    """A teacher that times out on Token Factory, behind the real HTTP provider: confident answers are still 200 (also
    streamed); an escalation is a 502."""
    gw = gw_env()
    for name in [n for n in os.environ if n.startswith("RATCHET_")]:  # e.g. RATCHET_MIN_SHADOW would move the gate
        monkeypatch.delenv(name)
    teacher_calls: list[httpx.Request] = []

    def teacher(request: httpx.Request) -> httpx.Response:
        teacher_calls.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    student = FakeOpenAIServer(lambda body: A if body["model"] == "student-v1" else B)
    rt = gw.Runtime(_live_settings(tmp_path), transports={"teacher": httpx.MockTransport(teacher), "student": student.transport})
    monkeypatch.setattr(gw, "rt", rt)
    live = _version(rt.ledger, "live", {"threshold": 0.5})
    cand = _version(rt.ledger, "shadow", {"threshold": 0.5})
    c = TestClient(gw.app)

    # Audited, and the shadow disagrees: the audit fails, and arbitration is not tried against the same outage.
    r = c.post(CHAT, json={"messages": _ask(1)})
    assert r.status_code == 200, r.text
    assert (r.json()["ratchet"]["served_by"], r.json()["choices"][0]["message"]["content"]) == ("student", A)
    assert len(teacher_calls) == 1
    row = _last_request(rt.ledger)
    assert (row["audited"], row["shadow_version"], row["shadow_serve"], row["shadow_agree"]) == (0, cand, 1, None)

    r = c.post(CHAT, json={"messages": _ask(2), "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.text.rstrip().endswith("data: [DONE]") and len(teacher_calls) == 2

    # Not audited: the shadow's disagreement goes to the teacher, which fails, and the request is still a 200.
    rt.settings.audit_rate = 0.0
    r = c.post(CHAT, json={"messages": _ask(3)})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "student" and len(teacher_calls) == 3
    assert rt.ratchet.health() == {"student_errors": 0, "audit_errors": 2, "shadow_errors": 1}
    assert [e["kind"] for e in rt.ledger.events()] == ["audit_error", "shadow_error"]
    assert rt.ledger.examples() == []

    # INV-7: an unsure student needs the teacher's answer; with the teacher down that is a 502, as before.
    rt.ledger.update_eval(live, {"threshold": 0.99})
    r = c.post(CHAT, json={"messages": _ask(4)})
    assert r.status_code == 502 and r.json()["detail"] == "teacher unavailable: ReadTimeout"
