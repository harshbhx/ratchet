"""Upstream failures on the request path (NFR-2, FR-1.8, FR-1.11, FR-2.10, FR-2.12, INV-7, INV-15).

A failing student falls back to the teacher (`tests/test_hardening.py`). Here:

- The optional calls around a request the student already answered, the teacher's audit and its shadow arbitration,
  and the shadow's own samples. Each failure is counted (`Ratchet.health()`), logged as a throttled `audit_error` or
  `shadow_error` event that names the exception class (and HTTP status) but never its message, and the student's
  answer is served.
- The provider's retries (`ratchet/resilience.py`, ADR 0014): full-jitter backoff under the cap, the server's
  Retry-After, the deadline, a read timeout retried once, no retry for other 4xx or for a bad 2xx body, and
  `UpstreamError` parsed from the error body without the URL. Waits go to an injected `sleep`, so nothing really waits.
- What the client gets when the teacher's answer was needed and the teacher failed: 400 for a cause it can fix, 429
  for a rate limit, 504 for a timeout, 502 otherwise, with `X-Should-Retry: false` where a retry cannot help; and the
  daily quota's 429 headers.
- The student circuit breaker (FR-2.11, ADR 0015): `Breaker` on a fake clock and under threads; the router skipping an
  endpoint that keeps failing (route `student_breaker`), probing it again and backing off; a shadow breaker of its own;
  vLLM's 404 for a lost adapter queuing one reload a minute through the live gateway; the mock outage demo; and the
  `route` recorded on every request.
- When the teacher is down (FR-2.12, ADR 0019): the teacher's breaker (a 503 `teacher_circuit_open` with `Retry-After`
  at once, audits and arbitrations skipped), `RATCHET_ON_TEACHER_FAILURE=serve_student` (the student's valid answer,
  marked degraded, never stored, audited or shadowed), and the licensed fallback teacher.
- Night School's golden eval when the student endpoint refuses a row for a condition stored with it (vLLM's 400 to
  `tools` without a tool parser): the row scores as not served and the night goes on (FR-5.1, INV-7).
"""

from __future__ import annotations

import calendar
import email.utils
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import FALLBACK_TEACHER, OPEN_TEACHER, FakeClock, FakeOpenAIServer, RecordingProvider, RecordingTrainer, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

from ratchet import licenses, tasks
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.live import AdapterReloader, adapter_reloader
from ratchet.nightschool import dataset
from ratchet.nightschool.evaluate import LiveEvaluator
from ratchet.nightschool.orchestrate import run_night
from ratchet.providers import PARAM_FIELDS, GenParams, GenPolicy, OpenAICompatProvider, UpstreamError
from ratchet.release import ReleaseGate
from ratchet.resilience import (
    NO_RETRY,
    AdapterMissing,
    Breaker,
    BreakerPolicy,
    CircuitOpen,
    RetryPolicy,
    adapter_missing,
    as_upstream,
    endpoint_down,
    parse_retry_after,
    timeouts,
)
from ratchet.router import ROUTES, Ratchet, RequestMeta, fingerprint
from ratchet.scout.guard import Guard
from ratchet.scout.prompts import EXAMPLE_QUESTIONS

A = json.dumps({"category": "billing", "priority": "high"})
B = json.dumps({"category": "refund", "priority": "high"})
TRIAGE = "ticket-triage"
CHAT = "/v1/chat/completions"
# httpx puts the URL in its messages; an event must never repeat it (it can hold a host, a path or a query token).
TEACHER_URL = "https://teacher.test/v1/chat/completions?token=do-not-log"
TEACHER_BASE = "https://teacher.test/v1"
TEACHER_KEY = "tf-teacher-api-key"
NO_ERRORS = {
    "student_errors": 0,
    "audit_errors": 0,
    "shadow_errors": 0,
    "degraded_answers": 0,
    "fallback_answers": 0,
    "repair_errors": 0,
}
HI = [{"role": "user", "content": "hi"}]
KEY = "resilience-test-key-0123456789abcdef"
OK_BODY = {"model": "m", "choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
TOO_LONG = "This model's maximum context length is 8192 tokens."
CONTEXT_TOO_LONG = {
    "error": {"message": TOO_LONG, "type": "invalid_request_error", "param": "messages", "code": "context_length_exceeded"}
}


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
    # Under the ledger's lock: a live gateway's sidecar writes through the same connection, and an unsynchronised read
    # against it can deadlock (ADR 0072; `LedgerGuard` in tests/conftest.py). A test that needs the row final drains first.
    with led.locked():
        return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


def _events(led: Ledger) -> list[tuple[str, str]]:
    return [(e["kind"], e["detail"]) for e in led.events()]


def _counters(r: Ratchet) -> dict[str, int]:
    """`Ratchet.health()` without the breakers: the failure counters only."""
    return {k: v for k, v in r.health().items() if k != "breakers"}


def _cascade(teacher: RecordingProvider, audit_rate: float, **settings: Any) -> tuple[Ledger, int, Settings, Ratchet]:
    """A live version with a calibrated threshold of 0.5, which its unanimous student (confidence 0.98) clears."""
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    student = RecordingProvider(A)
    base: dict[str, Any] = {"on_teacher_failure": "error", "teacher_fallback_model": "", "teacher_breaker_failures": 5}
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=audit_rate, teacher_reasoning_tokens=0, **{**base, **settings})
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
    assert _counters(r) == {**NO_ERRORS, "audit_errors": 1}


@pytest.mark.req("FR-2.10", "INV-15", "INV-7", "FR-1.8")
def test_audit_errors_are_counted_and_throttled_and_escalation_still_fails():
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    # No teacher breaker, so every request tries its audit (with one, the 6th on skip it: test_open_teacher_breaker_*).
    led, live, s, r = _cascade(teacher, audit_rate=1.0, teacher_breaker_failures=0)
    for i in range(12):
        assert r.handle(_ask(i), TRIAGE).served_by == "student"
    assert _counters(r) == {**NO_ERRORS, "audit_errors": 12}
    assert _events(led) == [("audit_error", f"{TRIAGE}: ReadTimeout")] * 2  # the 1st and the 10th, not all twelve
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE audited=1").fetchone()[0] == 0

    # The teacher is back: audits count again, and their answers are training data again.
    teacher.raise_on.clear()
    r.handle(_ask("recovered"), TRIAGE)
    row = _last_request(led)
    assert (row["audited"], row["audit_agree"]) == (1, 0) and len(led.examples()) == 1
    assert _counters(r)["audit_errors"] == 12

    # INV-7: an unsure student needs the teacher's answer, so a teacher failure there is still an error (502).
    teacher.raise_on.add(OPEN_TEACHER)
    led.update_eval(live, {"threshold": 0.99})
    with pytest.raises(httpx.ReadTimeout):
        r.handle(_ask("unsure"), TRIAGE)
    assert _counters(r) == {**NO_ERRORS, "audit_errors": 12}  # an escalation is not an optional call


@pytest.mark.req("FR-2.10")
def test_failure_counters_are_exact_under_threads():
    """Scout calls handle from several threads; every failure is counted once and the events stay throttled."""
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, live, s, r = _cascade(teacher, audit_rate=1.0, teacher_breaker_failures=0)  # every audit tried, as above
    with ThreadPoolExecutor(8) as pool:
        served = list(pool.map(lambda i: r.handle(_ask(i), TRIAGE).served_by, range(200)))
    assert served == ["student"] * 200
    assert _counters(r) == {**NO_ERRORS, "audit_errors": 200}
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
    assert _counters(r) == {**NO_ERRORS, "shadow_errors": 1}
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
    # No breaker, so every request tries the shadow (with one, the 6th on skip it: test_shadow_breaker_is_its_own).
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, student_breaker_failures=0)
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
    assert _counters(r) == {**NO_ERRORS, "shadow_errors": 10}


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
        "teacher_timeout_s": 120.0,
        "teacher_retries": 2,
        "teacher_deadline_s": 180.0,
        "student_timeout_s": 15.0,
        "student_connect_timeout_s": 2.0,
        "samples": 3,
        "audit_rate": 1.0,
        "redact_pii": True,
        "api_keys": "",
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
        # No API keys and a public dashboard (ADR 0061): these tests call /v1/* and read /ratchet/stats without a key.
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,
    }
    return Settings(**{**base, **overrides})


@pytest.mark.req("FR-1.8", "FR-2.10", "INV-7", "INV-15", "NFR-2")
def test_optional_teacher_failures_through_the_gateway(gw_env, tmp_path, monkeypatch):
    """A teacher that times out on Token Factory, behind the real HTTP provider with its retry policy: confident answers
    are still 200 (also streamed); an escalation is a 504 `teacher_timeout`. Each teacher call is two HTTP requests,
    because a read timeout is retried once (ADR 0014)."""
    gw = gw_env()
    for name in [n for n in os.environ if n.startswith("RATCHET_")]:  # e.g. RATCHET_MIN_SHADOW would move the gate
        monkeypatch.delenv(name)
    teacher_calls: list[httpx.Request] = []

    def teacher(request: httpx.Request) -> httpx.Response:
        teacher_calls.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    student = FakeOpenAIServer(lambda body: A if body["model"] == "student-v1" else B)
    waits: list[float] = []
    transports = {"teacher": httpx.MockTransport(teacher), "student": student.transport}
    rt = gw.Runtime(_live_settings(tmp_path), transports=transports, sleep=waits.append)
    monkeypatch.setattr(gw, "rt", rt)
    live = _version(rt.ledger, "live", {"threshold": 0.5})
    cand = _version(rt.ledger, "shadow", {"threshold": 0.5})
    c = TestClient(gw.app)

    # Audited, and the shadow disagrees: the audit fails, and arbitration is not tried against the same outage. Live
    # mode runs both after the response (ADR 0072), so each check waits for the sidecar first.
    r = c.post(CHAT, json={"messages": _ask(1)})
    assert r.status_code == 200, r.text
    assert (r.json()["ratchet"]["served_by"], r.json()["choices"][0]["message"]["content"]) == ("student", A)
    assert r.json()["ratchet"]["overhead_pending"] and rt.ratchet.sidecar.drain(10)
    assert len(teacher_calls) == 2 and len(waits) == 1
    row = _last_request(rt.ledger)
    assert (row["audited"], row["shadow_version"], row["shadow_serve"], row["shadow_agree"]) == (0, cand, 1, None)

    r = c.post(CHAT, json={"messages": _ask(2), "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.text.rstrip().endswith("data: [DONE]") and rt.ratchet.sidecar.drain(10) and len(teacher_calls) == 4

    # Not audited: the shadow's disagreement goes to the teacher, which fails, and the request is still a 200.
    rt.settings.audit_rate = 0.0
    r = c.post(CHAT, json={"messages": _ask(3)})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "student"
    assert rt.ratchet.sidecar.drain(10) and len(teacher_calls) == 6
    assert _counters(rt.ratchet) == {**NO_ERRORS, "audit_errors": 2, "shadow_errors": 1}
    events = [(e["kind"], e["detail"]) for e in rt.ledger.events()]
    assert events == [
        ("audit_error", "default: UpstreamError 504 upstream_timeout"),
        ("shadow_error", f"v{cand} arbitration: UpstreamError 504 upstream_timeout"),
    ]
    assert rt.ledger.examples() == []

    # INV-7: an unsure student needs the teacher's answer; with the teacher timing out that is a 504 the SDKs retry.
    rt.ledger.update_eval(live, {"threshold": 0.99})
    r = c.post(CHAT, json={"messages": _ask(4)})
    assert r.status_code == 504 and r.json()["detail"] == "teacher timeout: upstream 504 upstream_timeout"
    assert (r.json()["error"]["type"], r.json()["error"]["code"]) == ("api_error", "teacher_timeout")
    assert "teacher.test" not in r.text and len(teacher_calls) == 8 and rt.gateway_errors == 0


@pytest.mark.req("INV-12", "INV-7", "FR-1.8", "NFR-2")
def test_student_error_event_never_quotes_the_endpoint(gw_env, tmp_path, monkeypatch):
    """vLLM's 400 can quote the request (it returns `str(exc)` of validation and template errors). The `student_error`
    event, which /ratchet/stats serves without an API key, names the class, status and code only: no request text and
    no URL (INV-12, ADR 0014). The teacher still answers."""
    gw = gw_env()
    for name in [n for n in os.environ if n.startswith("RATCHET_")]:
        monkeypatch.delenv(name)
    private = "My SSN is 123-45-6789, email jane@corp.com"

    def student(request: httpx.Request) -> httpx.Response:
        content = json.loads(request.content)["messages"][-1]["content"]
        error = {"message": f"Invalid content: {content!r} ({request.url})", "type": "BadRequestError", "code": 400}
        return httpx.Response(400, json={"error": error})

    teacher = FakeOpenAIServer(B)
    transports = {"teacher": teacher.transport, "student": httpx.MockTransport(student)}
    rt = gw.Runtime(_live_settings(tmp_path, audit_rate=0.0), transports=transports)
    monkeypatch.setattr(gw, "rt", rt)
    live = _version(rt.ledger, "live", {"threshold": 0.5})
    c = TestClient(gw.app)

    r = c.post(CHAT, json={"messages": [{"role": "user", "content": private}]})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert _last_request(rt.ledger)["route"] == "student_error" and _counters(rt.ratchet)["student_errors"] == 1
    stats = c.get("/ratchet/stats")
    assert [(e["kind"], e["detail"]) for e in stats.json()["events"]] == [("student_error", f"v{live}: UpstreamError 400")]
    for leak in ("123-45-6789", "jane@corp.com", "Invalid content", "student.test"):
        assert leak not in stats.text and all(leak not in e["detail"] for e in rt.ledger.events()), leak


# ---------- the provider's retries (ADR 0014) ----------
class Upstream:
    """An OpenAI-compatible endpoint that plays `steps` in order, repeating the last one. A step is a status (with a
    default body), `(status, headers, body)` (a str body is sent as is), or an httpx exception class to raise. Each
    attempt takes `takes` seconds on `clock`. Records every request, with the timeouts it was sent with."""

    def __init__(self, *steps: Any, clock: FakeClock | None = None, takes: float = 0.0) -> None:
        self.steps = steps
        self.clock = clock
        self.takes = takes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.clock is not None:
            self.clock.advance(self.takes)
        step = self.steps[min(len(self.requests), len(self.steps)) - 1]
        if isinstance(step, type) and issubclass(step, Exception):
            raise step(f"failed talking to {request.url}", request=request)
        status, headers, body = (step, {}, None) if isinstance(step, int) else step
        if body is None:
            body = OK_BODY if status == 200 else {"error": {"message": f"status {status}", "type": "server_error"}}
        if isinstance(body, str):
            return httpx.Response(status, headers=headers, text=body)
        return httpx.Response(status, headers=headers, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def timeouts(self, phase: str = "read") -> list[float]:
        return [r.extensions["timeout"][phase] for r in self.requests]


def _provider(
    upstream: Upstream,
    policy: RetryPolicy | None = RetryPolicy(),
    *,
    clock: FakeClock | None = None,
    rng: float = 0.999,
    read_s: float = 120.0,
) -> tuple[OpenAICompatProvider, list[float]]:
    """The real provider on `upstream`, with its waits recorded (and moving `clock`, when given) instead of slept."""
    waits: list[float] = []
    clock = clock or FakeClock(0.0)

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock.advance(seconds)

    provider = OpenAICompatProvider(
        TEACHER_BASE,
        TEACHER_KEY,
        timeout=timeouts(read_s),
        retry=policy,
        transport=upstream.transport,
        sleep=sleep,
        clock=clock,
        rng=lambda: rng,
    )
    return provider, waits


def _no_secrets(e: UpstreamError) -> None:
    """An UpstreamError never carries the URL, the host or the API key, in its message or any attribute."""
    seen = f"{e!s} {e!r} {vars(e)!r}"
    assert "teacher.test" not in seen and TEACHER_KEY not in seen and "do-not-log" not in seen, seen


@pytest.mark.req("NFR-2", "FR-1.11")
def test_retry_backs_off_with_full_jitter_under_the_cap():
    up = Upstream(503, 503, 200)
    provider, waits = _provider(up)
    out = provider.complete(HI, OPEN_TEACHER)
    assert [c.text for c in out] == ["hi"] and len(up.requests) == 3
    # Full jitter: uniform(0, min(cap, base * 2**i)), here with the random draw at 0.999 of its range.
    assert waits == pytest.approx([0.999 * 0.5, 0.999 * 1.0]) and waits[0] < waits[1] <= RetryPolicy().cap_s
    assert out[0].latency_ms >= 0 and out[0].in_tokens == 3

    # A long outage: the waits double up to the cap and stay there; then the last error goes back to the caller.
    up = Upstream(503)
    provider, waits = _provider(up, RetryPolicy(max_attempts=8, base_s=0.5, cap_s=2.0, deadline_s=math.inf))
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    assert (e.value.status, e.value.message, e.value.type, len(up.requests)) == (503, "status 503", "server_error", 8)
    assert waits == pytest.approx([0.4995, 0.999, 1.998, 1.998, 1.998, 1.998, 1.998]) and max(waits) <= 2.0
    assert isinstance(e.value, httpx.HTTPError)  # existing `except httpx.HTTPError` clauses still catch it
    _no_secrets(e.value)

    # The low end of the jitter is no wait at all.
    provider, waits = _provider(Upstream(500, 502, 200), rng=0.0)
    assert provider.complete(HI, OPEN_TEACHER)[0].text == "hi" and waits == [0.0, 0.0]


@pytest.mark.req("NFR-2", "FR-1.11")
def test_retry_after_is_honoured():
    up = Upstream((429, {"Retry-After": "2"}, None), 200)
    provider, waits = _provider(up)
    assert provider.complete(HI, OPEN_TEACHER)[0].text == "hi" and waits == [2.0] and len(up.requests) == 2

    # retry-after-ms (OpenAI's) wins over Retry-After, and works on a 503 too.
    up = Upstream((503, {"retry-after-ms": "1500", "Retry-After": "7"}, None), 200)
    provider, waits = _provider(up)
    assert provider.complete(HI, OPEN_TEACHER)[0].text == "hi" and waits == [1.5]

    # A wait longer than cap_s (8 s) is not worth holding the request for: the 429 goes back at once, with the wait.
    up = Upstream((429, {"Retry-After": "30"}, {"error": {"message": "slow down", "code": "rate_limit_exceeded"}}))
    provider, waits = _provider(up)
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    assert (e.value.status, e.value.code, e.value.retry_after, len(up.requests), waits) == (
        429,
        "rate_limit_exceeded",
        30.0,
        1,
        [],
    )


@pytest.mark.req("FR-1.11")
def test_parse_retry_after_forms():
    now = 1_800_000_000.0
    assert parse_retry_after({"Retry-After": "2"}) == 2.0
    assert parse_retry_after({"retry-after": "1.5"}) == 1.5
    assert parse_retry_after({"Retry-After-Ms": "250"}) == 0.25
    assert parse_retry_after({"Retry-After": email.utils.formatdate(now + 7, usegmt=True)}, now=now) == 7.0
    assert parse_retry_after({"retry-after-ms": "soon", "retry-after": "4"}) == 4.0  # a bad ms value falls back
    assert parse_retry_after(httpx.Headers({"RETRY-AFTER": "3"})) == 3.0
    assert parse_retry_after({}) is None
    past = email.utils.formatdate(now - 60, usegmt=True)
    for junk in ("", "soon", "0", "-3", "inf", "nan", "1e400", past, "Mon, 99 Foo 2026 25:61:00 GMT"):
        assert parse_retry_after({"Retry-After": junk}, now=now) is None, junk


@pytest.mark.req("NFR-2", "FR-1.11")
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_client_errors_are_not_retried(status):
    up = Upstream(status)
    provider, waits = _provider(up, RetryPolicy(max_attempts=5))
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    assert (e.value.status, len(up.requests), waits) == (status, 1, [])


@pytest.mark.req("NFR-2", "FR-1.11")
def test_deadline_stops_retries():
    """The deadline bounds attempts and waits together: each attempt's timeouts shrink to the time that is left, and a
    wait that would end past it is not started."""
    clock = FakeClock(0.0)
    up = Upstream(503, clock=clock, takes=1.0)
    provider, waits = _provider(up, RetryPolicy(max_attempts=10, deadline_s=5.0), clock=clock, rng=1.0)
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    # t=0 try (1 s), wait 0.5; t=1.5 try, wait 1.0; t=3.5 try; t=4.5: a 2 s wait would end at 6.5, past 5. Stop.
    assert e.value.status == 503 and waits == [0.5, 1.0] and len(up.requests) == 3
    assert up.timeouts("read") == [5.0, 3.5, 1.5] and up.timeouts("connect") == [5.0, 3.5, 1.5]
    assert clock.now == 4.5

    # The server's Retry-After counts against the deadline too.
    clock = FakeClock(0.0)
    up = Upstream((503, {"Retry-After": "4"}, None), clock=clock, takes=2.0)
    provider, waits = _provider(up, RetryPolicy(deadline_s=5.0), clock=clock)
    with pytest.raises(UpstreamError):
        provider.complete(HI, OPEN_TEACHER)
    assert (len(up.requests), waits) == (1, [])

    # No retry policy: one attempt with the configured timeouts, unchanged.
    up = Upstream(503)
    provider, waits = _provider(up, None, read_s=15.0)
    with pytest.raises(UpstreamError):
        provider.complete(HI, OPEN_TEACHER)
    assert (len(up.requests), waits, up.timeouts()) == (1, [], [15.0]) and provider.retry == NO_RETRY


@pytest.mark.req("NFR-2", "FR-1.11")
def test_read_timeout_is_retried_exactly_once():
    up = Upstream(httpx.ReadTimeout)
    provider, waits = _provider(up, RetryPolicy(max_attempts=5))
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    assert (e.value.status, e.value.code, len(up.requests), len(waits)) == (504, "upstream_timeout", 2, 1)
    assert isinstance(e.value.__cause__, httpx.ReadTimeout) and "ReadTimeout" in e.value.message
    _no_secrets(e.value)

    # A 503 first does not use up the one read-timeout retry; a second read timeout ends it.
    up = Upstream(503, httpx.ReadTimeout, httpx.ReadTimeout, 200)
    provider, _ = _provider(up, RetryPolicy(max_attempts=5))
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    assert (e.value.code, len(up.requests)) == ("upstream_timeout", 3)
    up = Upstream(503, httpx.ReadTimeout, 200)
    provider, _ = _provider(up, RetryPolicy(max_attempts=5))
    assert provider.complete(HI, OPEN_TEACHER)[0].text == "hi" and len(up.requests) == 3

    # A connection dropped mid-response and a 504 may come after the model started generating: like a read timeout, one
    # retry at most, and one in all, whichever came first (each could bill what the model generated).
    for steps, attempts in (
        ((httpx.RemoteProtocolError,), 2),
        ((504,), 2),
        ((httpx.ReadTimeout, httpx.RemoteProtocolError), 2),
        ((504, httpx.ReadTimeout), 2),
        ((httpx.RemoteProtocolError, 503, 504), 3),
        ((504, 200), 2),
    ):
        up = Upstream(*steps)
        provider, waits = _provider(up, RetryPolicy(max_attempts=5))
        if steps[-1] == 200:
            assert provider.complete(HI, OPEN_TEACHER)[0].text == "hi"
        else:
            with pytest.raises(UpstreamError):
                provider.complete(HI, OPEN_TEACHER)
        assert (len(up.requests), len(waits)) == (attempts, attempts - 1), steps

    # A connection that never opened reached no model: retried up to max_attempts, then 502 upstream_unreachable.
    for exc in (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        up = Upstream(exc)
        provider, waits = _provider(up, RetryPolicy(max_attempts=4))
        with pytest.raises(UpstreamError) as e:
            provider.complete(HI, OPEN_TEACHER)
        assert len(up.requests) == 4 and len(waits) == 3, exc
        timeout = issubclass(exc, httpx.TimeoutException)
        assert (e.value.status, e.value.code) == ((504, "upstream_timeout") if timeout else (502, "upstream_unreachable"))
        _no_secrets(e.value)

    # Failures after the request may have reached the model are not retried.
    for exc, status in ((httpx.WriteTimeout, 504), (httpx.ReadError, 502), (httpx.WriteError, 502)):
        up = Upstream(exc)
        provider, waits = _provider(up, RetryPolicy(max_attempts=4))
        with pytest.raises(UpstreamError) as e:
            provider.complete(HI, OPEN_TEACHER)
        assert (e.value.status, len(up.requests), waits) == (status, 1, []), exc


@pytest.mark.req("NFR-2", "FR-1.11")
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"choices": []}, id="empty-choices"),
        pytest.param({"object": "chat.completion"}, id="no-choices"),
        pytest.param({"choices": None}, id="null-choices"),
        pytest.param({"choices": ["hi"]}, id="choice-not-an-object"),
        pytest.param([OK_BODY], id="not-an-object"),
        pytest.param("<html>maintenance</html>", id="not-json"),
        pytest.param("[" * 100_000 + "]" * 100_000, id="nested-too-deeply"),
    ],
)
def test_bad_upstream_bodies_are_502_and_not_retried(body):
    """A 2xx the provider cannot read is a 502 `bad_upstream_response` (before: an IndexError, so the gateway's 500).
    It is not retried: the model answered, and the tokens are billed."""
    up = Upstream((200, {}, body))
    provider, waits = _provider(up)
    with pytest.raises(UpstreamError) as e:
        provider.complete(HI, OPEN_TEACHER)
    assert (e.value.status, e.value.code, len(up.requests), waits) == (502, "bad_upstream_response", 1, [])


@pytest.mark.req("FR-1.11")
def test_malformed_optional_fields_count_as_absent():
    choice = {
        "message": {"content": [{"type": "text", "text": "a"}, {"type": "image_url"}, {"type": "text", "text": "b"}]},
        "logprobs": {"content": [{"logprob": "x"}, {"logprob": -0.5}, "junk", {"logprob": True}, {"logprob": None}]},
    }
    body = {"model": None, "choices": [choice, {"message": None}], "usage": {"prompt_tokens": "12", "completion_tokens": -4}}
    provider, _ = _provider(Upstream((200, {}, body)))
    out = provider.complete(HI, OPEN_TEACHER, n=2, logprobs=True)
    assert [(c.text, c.model, c.in_tokens, c.out_tokens, c.token_logprobs) for c in out] == [
        ("ab", OPEN_TEACHER, 0, 0, [-0.5]),
        ("", OPEN_TEACHER, 0, 0, []),
    ]
    # NaN is accepted by Python's JSON parser; it is not a probability.
    raw = '{"choices": [{"message": {"content": "a"}, "logprobs": {"content": [{"logprob": NaN}, {"logprob": -1}]}}]}'
    provider, _ = _provider(Upstream((200, {"content-type": "application/json"}, raw)))
    assert provider.complete(HI, OPEN_TEACHER, logprobs=True)[0].token_logprobs == [-1.0]


@pytest.mark.req("FR-1.11")
@pytest.mark.parametrize(
    ("status", "body", "parsed"),
    [
        pytest.param(
            400,
            CONTEXT_TOO_LONG,
            (TOO_LONG, "invalid_request_error", "context_length_exceeded", "messages"),
            id="openai",
        ),
        pytest.param(
            400,
            {"object": "error", "message": "bad n", "type": "BadRequestError", "param": None, "code": 400},
            ("bad n", "BadRequestError", None, None),
            id="vllm-legacy-integer-code",
        ),
        pytest.param(503, {"error": "overloaded"}, ("overloaded", None, None, None), id="error-string"),
        pytest.param(404, {"detail": "Not Found"}, ("Not Found", None, None, None), id="fastapi-detail"),
        pytest.param(
            422,
            {"detail": [{"loc": ["body", "messages", 0, "content"], "msg": "Field required", "type": "missing"}]},
            ("Field required", None, None, "messages.0.content"),
            id="fastapi-validation",
        ),
        pytest.param(502, "<html>Bad Gateway from https://teacher.test</html>", ("Bad Gateway", None, None, None), id="html"),
        pytest.param(
            400,
            # JSON text: a control character and a lone surrogate (escaped, as a JSON encoder writes them), too long.
            '{"error": {"message": "line\\nbreak \\ud800' + "x" * 400 + '", "code": "has spaces", "param": ["messages"]}}',
            ("line break ?" + "x" * 287 + "…", None, None, None),
            id="clipped-and-cleaned",
        ),
    ],
)
def test_upstream_error_bodies_are_parsed_without_the_url(status, body, parsed):
    request = httpx.Request("POST", TEACHER_URL, headers={"Authorization": f"Bearer {TEACHER_KEY}"})
    if isinstance(body, str):
        response = httpx.Response(status, text=body, request=request)
    else:
        response = httpx.Response(status, json=body, request=request)
    e = UpstreamError.from_response(response)
    assert (e.status, (e.message, e.type, e.code, e.param)) == (status, parsed)
    assert len(e.message) <= 300 and e.retry_after is None
    assert json.dumps(vars(e), ensure_ascii=False).encode()  # always valid UTF-8
    _no_secrets(e)
    # httpx's own status error, as Tavily's raise_for_status raises it, reads the same way.
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as err:
        assert (as_upstream(err).status, as_upstream(err).message) == (status, e.message)


# ---------- what the client gets (ADR 0014) ----------
def _cold_start(gw_env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: Upstream) -> tuple[Any, Any, list]:
    """A live gateway with no student version yet, so the teacher (behind `upstream`) answers every request."""
    gw = gw_env()
    waits: list[float] = []
    rt = gw.Runtime(_live_settings(tmp_path, audit_rate=0.0), transports={"teacher": upstream.transport}, sleep=waits.append)
    monkeypatch.setattr(gw, "rt", rt)
    return gw, rt, waits


@pytest.mark.req("FR-1.8", "FR-1.11", "INV-7", "NFR-2")
@pytest.mark.parametrize(
    ("step", "status", "kind", "code", "no_retry", "retry_after", "attempts"),
    [
        pytest.param(
            (400, {}, CONTEXT_TOO_LONG),
            400,
            "invalid_request_error",
            "context_length_exceeded",
            True,
            None,
            1,
            id="context-too-long",
        ),
        pytest.param((413, {}, None), 400, "invalid_request_error", "request_too_large", True, None, 1, id="too-large"),
        pytest.param(
            (429, {"Retry-After": "7"}, None), 429, "rate_limit_error", "upstream_rate_limited", False, "7", 3, id="rate"
        ),
        pytest.param((429, {}, None), 429, "rate_limit_error", "upstream_rate_limited", False, "1", 3, id="rate-no-wait"),
        pytest.param(
            (429, {"Retry-After": "600"}, None), 429, "rate_limit_error", "upstream_rate_limited", False, "60", 1, id="rate-long"
        ),
        pytest.param(500, 502, "api_error", "teacher_unavailable", False, None, 3, id="server-error"),
        pytest.param((503, {"Retry-After": "2"}, None), 502, "api_error", "teacher_unavailable", False, "2", 3, id="overloaded"),
        pytest.param(
            (503, {"Retry-After": "30"}, None), 502, "api_error", "teacher_unavailable", False, "30", 1, id="overloaded-long"
        ),
        pytest.param(
            (503, {"Retry-After": "600"}, None), 502, "api_error", "teacher_unavailable", False, "60", 1, id="overloaded-longer"
        ),
        pytest.param(
            (504, {"Retry-After": "20"}, None), 504, "api_error", "teacher_timeout", False, "20", 1, id="gateway-timeout-wait"
        ),
        pytest.param(httpx.ConnectError, 502, "api_error", "teacher_unavailable", False, None, 3, id="connect-error"),
        pytest.param(httpx.ReadTimeout, 504, "api_error", "teacher_timeout", False, None, 2, id="read-timeout"),
        pytest.param(504, 504, "api_error", "teacher_timeout", False, None, 2, id="gateway-timeout"),
        pytest.param(httpx.RemoteProtocolError, 502, "api_error", "teacher_unavailable", False, None, 2, id="dropped"),
        pytest.param(401, 502, "api_error", "upstream_auth_error", True, None, 1, id="auth"),
        pytest.param(403, 502, "api_error", "upstream_auth_error", True, None, 1, id="forbidden"),
        pytest.param(404, 502, "api_error", "upstream_not_found", True, None, 1, id="model-not-found"),
        pytest.param(402, 502, "api_error", "teacher_unavailable", True, None, 1, id="payment-required"),
        pytest.param(
            (402, {"Retry-After": "30"}, None), 502, "api_error", "teacher_unavailable", True, None, 1, id="final-with-wait"
        ),
        pytest.param(301, 502, "api_error", "teacher_unavailable", True, None, 1, id="redirect"),
        pytest.param((200, {}, {"choices": []}), 502, "api_error", "teacher_unavailable", False, None, 1, id="empty-choices"),
        pytest.param(
            (200, {}, "[" * 100_000 + "]" * 100_000), 502, "api_error", "teacher_unavailable", False, None, 1, id="deep-json"
        ),
    ],
)
def test_upstream_errors_map_to_client_statuses(
    gw_env, tmp_path, monkeypatch, step, status, kind, code, no_retry, retry_after, attempts
):
    """Live Runtime, the real provider and its retry policy, the teacher behind MockTransport. A cause the client can
    fix is a 400 with the teacher's message, param and code; a rate limit a 429 with the teacher's wait (1 to 60 s); a
    timeout a 504; anything else a 502. `X-Should-Retry: false` where no retry can help, so SDKs do not resend it. A 502
    or 504 SDKs may retry carries the teacher's wait (1 to 60 s) when it sent one, so they do not come back sooner; a
    wait over the provider's cap (8 s) is not slept on the request path at all."""
    upstream = Upstream(step)
    gw, rt, waits = _cold_start(gw_env, tmp_path, monkeypatch, upstream)
    r = TestClient(gw.app, raise_server_exceptions=False).post(CHAT, json={"messages": HI})
    err = r.json()["error"]
    assert (r.status_code, err["type"], err["code"]) == (status, kind, code), r.text
    assert r.headers.get("x-should-retry") == ("false" if no_retry else None)
    assert r.headers.get("retry-after") == retry_after
    assert len(upstream.requests) == attempts and len(waits) == attempts - 1
    assert r.json()["detail"] == err["message"] and "teacher.test" not in r.text and TEACHER_KEY not in r.text
    assert rt.gateway_errors == 0 and rt.ledger.totals()["requests"] == 0  # not a crash, and nothing logged
    if status == 400 and code == "context_length_exceeded":  # the teacher's own words reach the client
        assert (err["message"], err["param"]) == (TOO_LONG, "messages")
    if retry_after == "7":
        assert waits == [7.0, 7.0]  # the teacher's own wait before each retry
    else:
        assert all(0 <= w <= RetryPolicy().cap_s for w in waits)  # full-jitter backoff


@pytest.mark.req("FR-1.11", "FR-8.1", "NFR-2")
def test_live_runtime_passes_timeouts_and_retries(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    teacher, student = Upstream(200), Upstream(200)
    tuned = _live_settings(
        tmp_path,
        teacher_timeout_s=30.0,
        teacher_retries=1,
        teacher_deadline_s=50.0,
        student_timeout_s=7.0,
        student_connect_timeout_s=1.5,
        audit_rate=0.0,
    )
    waits: list[float] = []
    rt = gw.Runtime(tuned, transports={"teacher": teacher.transport, "student": student.transport}, sleep=waits.append)
    t = rt.ratchet.teacher
    assert (t.timeout, t.retry, t.sleep) == (timeouts(30.0), RetryPolicy(max_attempts=2, deadline_s=50.0), waits.append)
    vid = _version(rt.ledger, "live", {"threshold": 1.01})  # serves nothing: every request escalates
    s, model = rt.ratchet.resolve_student(rt.ledger.version(vid))
    assert (s.timeout, s.retry, model) == (timeouts(7.0, connect_s=1.5), NO_RETRY, "student-v1")

    # Each request carries its role's timeouts: the student's own; the teacher's, within its deadline.
    monkeypatch.setattr(gw, "rt", rt)
    r = TestClient(gw.app).post(CHAT, json={"messages": HI})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert student.requests[0].extensions["timeout"] == {"connect": 1.5, "read": 7.0, "write": 10.0, "pool": 5.0}
    assert teacher.requests[0].extensions["timeout"] == {"connect": 5.0, "read": 30.0, "write": 10.0, "pool": 5.0}

    # No student endpoint: the hosted fallback on Token Factory is still a student, with its timeouts and no retries.
    fb = gw.Runtime(_live_settings(tmp_path, db_path=str(tmp_path / "fb.db"), student_base="", student_timeout_s=9.0))
    s, model = fb.ratchet.resolve_student(fb.ledger.version(_version(fb.ledger, "live")))
    assert s is not fb.ratchet.teacher and (s.base_url, s.timeout.read, s.retry) == (TEACHER_BASE, 9.0, NO_RETRY)
    assert fb.ratchet.teacher.retry == RetryPolicy(max_attempts=3, deadline_s=180.0)

    # The variables reach Settings; a bad value stops live mode at startup instead of failing every request.
    monkeypatch.setenv("RATCHET_TEACHER_RETRIES", "5")
    monkeypatch.setenv("RATCHET_TEACHER_DEADLINE_S", "30")
    assert Settings().teacher_retry() == RetryPolicy(max_attempts=6, deadline_s=30.0)
    bad_values = [
        {"teacher_timeout_s": 0.0},
        {"teacher_retries": -1},
        {"teacher_deadline_s": 0.0},
        {"student_timeout_s": -1.0},
        {"student_connect_timeout_s": float("nan")},
    ]
    for i, bad in enumerate(bad_values):
        with pytest.raises(ValueError):
            gw.Runtime(_live_settings(tmp_path, db_path=str(tmp_path / f"bad{i}.db"), **bad))


@pytest.mark.req("FR-1.7", "FR-1.11")
def test_quota_429_says_when_it_resets(gw_env, monkeypatch):
    gw = gw_env(RATCHET_API_KEYS=f"acme:{KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    eleven_pm = float(calendar.timegm((2026, 10, 20, 23, 0, 0)))
    monkeypatch.setattr(time, "time", lambda: eleven_pm)  # the gateway and the ledger read the same clock
    c = TestClient(gw.app)
    key = {"Authorization": f"Bearer {KEY}"}
    msgs = gw.rt.stream.sample(1).messages()

    ok = c.post(CHAT, json={"messages": msgs}, headers=key)
    assert ok.status_code == 200 and ok.headers["x-ratelimit-reset-requests"] == "1h0m0s"
    assert (ok.headers["x-ratelimit-limit-requests"], ok.headers["x-ratelimit-remaining-requests"]) == ("1", "0")
    r = c.post(CHAT, json={"messages": msgs}, headers=key)
    err = r.json()["error"]
    assert r.status_code == 429 and (err["type"], err["code"]) == ("insufficient_quota", "daily_quota_exceeded")
    assert (r.headers["retry-after"], r.headers["x-ratelimit-reset-requests"]) == ("3600", "1h0m0s")
    assert (r.headers["x-should-retry"], r.headers["x-ratelimit-remaining-requests"]) == ("false", "0")

    monkeypatch.setattr(time, "time", lambda: eleven_pm + 3600 - 90.5)  # 90.5 s before midnight: rounded up
    r = c.post(CHAT, json={"messages": msgs}, headers=key)
    assert (r.status_code, r.headers["retry-after"], r.headers["x-ratelimit-reset-requests"]) == (429, "91", "1m31s")
    assert gw.rt.ledger.client_requests_since("acme", 0) == 1  # refusals are not counted

    assert [gw._duration(s) for s in (0, 0.2, 59, 60, 11525, 86400)] == ["0s", "1s", "59s", "1m0s", "3h12m5s", "24h0m0s"]


@pytest.mark.req("FR-1.7", "FR-1.11", "US-1")
def test_sdk_sends_an_exhausted_quota_once(gw_env):
    openai, _ = sdk()
    gw = gw_env(RATCHET_API_KEYS=f"acme:{KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    msgs = gw.rt.stream.sample(1).messages()
    assert TestClient(gw.app).post(CHAT, json={"messages": msgs}, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
    calls: list[Any] = []
    client = make_openai_client(gw.app, api_key=KEY, calls=calls, max_retries=2)  # the SDK's default retry budget
    with pytest.raises(openai.RateLimitError) as e:
        client.chat.completions.create(model="ratchet", messages=msgs)
    assert len(calls) == 1  # X-Should-Retry: false (and a Retry-After past the SDK's 60 s limit)
    assert (e.value.status_code, e.value.type, e.value.code) == (429, "insufficient_quota", "daily_quota_exceeded")
    assert int(e.value.response.headers["retry-after"]) > 0 and e.value.response.headers["x-ratelimit-reset-requests"]


@pytest.mark.req("FR-1.8", "FR-1.11", "US-1")
def test_sdk_sends_an_upstream_client_error_once(gw_env, tmp_path, monkeypatch):
    """What the OpenAI SDK sees: a typed exception with the teacher's code, and no retry of a request that cannot work."""
    openai, _ = sdk()
    upstream = Upstream((400, {}, CONTEXT_TOO_LONG))
    gw, rt, waits = _cold_start(gw_env, tmp_path, monkeypatch, upstream)
    calls: list[Any] = []
    client = make_openai_client(gw.app, calls=calls, max_retries=2)
    with pytest.raises(openai.BadRequestError) as e:
        client.chat.completions.create(model="ratchet", messages=HI)
    assert (e.value.code, e.value.param, len(calls), len(upstream.requests)) == ("context_length_exceeded", "messages", 1, 1)

    upstream.steps = (401,)  # the gateway's own Token Factory key is wrong: a 502 the SDK does not retry either
    with pytest.raises(openai.InternalServerError) as e:
        client.chat.completions.create(model="ratchet", messages=HI)
    assert (e.value.status_code, e.value.code, len(calls), len(upstream.requests)) == (502, "upstream_auth_error", 2, 2)


@pytest.mark.req("FR-10.5", "FR-1.11")
def test_scout_error_event_names_the_upstream_status(gw, monkeypatch):
    c = TestClient(gw.app)
    gw.rt.guard = Guard(5.0, 10)
    failures = [
        (UpstreamError(503, "overloaded", code="service_unavailable"), "upstream 503 service_unavailable"),
        (httpx.ConnectError(f"[Errno 111] Connection refused: {TEACHER_URL}"), "upstream 502 upstream_unreachable"),
        (httpx.ReadTimeout("timed out"), "upstream 504 upstream_timeout"),
    ]
    for exc, label in failures:

        def failing(messages: Any, task: str | None = None, exc: Exception = exc) -> Any:
            raise exc

        monkeypatch.setattr(gw.rt.ratchet, "handle", failing)
        r = c.post("/scout/run", json={"question": EXAMPLE_QUESTIONS[0]})
        assert r.status_code == 502 and r.json()["detail"] == f"a model or search call failed: {label}"
        assert "teacher.test" not in r.text


# ---------- the student circuit breaker (FR-2.11, ADR 0015) ----------
def _transitions(changes: list[tuple[str, str, str]]) -> list[str]:
    return [f"{old}->{new}" for old, new, _ in changes]


def _circuits(led: Ledger) -> list[str]:
    return [e["detail"] for e in led.events() if e["kind"] == "circuit"]


@pytest.mark.req("FR-2.11", "NFR-2")
def test_breaker_opens_probes_backs_off_and_closes():
    clock = FakeClock(1000.0)
    changes: list[tuple[str, str, str]] = []
    b = Breaker("student", 5, 30, 300, clock=clock, on_change=lambda br, old, new, why: changes.append((old, new, why)))

    for _ in range(4):
        b.record(False, "ReadTimeout")
    assert b.allow() and b.state == "closed"
    b.record(True)  # a success resets the count
    for _ in range(4):
        b.record(False, "ReadTimeout")
    assert b.state == "closed" and changes == []
    b.record(False, "ReadTimeout")  # five in a row
    assert changes == [("closed", "open", "after 5 failures (ReadTimeout)")]
    assert b.snapshot() == {"state": "open", "failures": 5, "open_until": 1030.0, "open_s": 30.0}
    assert not b.allow()
    clock.advance(29.9)
    assert not b.allow()

    # After the wait, exactly one probe; each failed probe doubles the wait, up to max_open_s.
    for wait in (30, 60, 120, 240, 300, 300):
        clock.now = b.snapshot()["open_until"]
        assert b.allow() and b.state == "half_open"
        assert not b.allow()  # everyone else sees it open while the probe is out
        b.record(False, "ReadTimeout")
        assert b.snapshot()["open_s"] == min(2 * wait, 300) and b.state == "open"
    assert changes[1:3] == [
        ("open", "half_open", "after 30 s; one probe"),
        ("half_open", "open", "for 60 s after a failed probe (ReadTimeout)"),
    ]

    # A successful probe closes it and resets the wait.
    clock.now = b.snapshot()["open_until"]
    assert b.allow()
    b.record(True)
    assert b.snapshot() == {"state": "closed", "failures": 0, "open_until": None, "open_s": 30.0}
    assert changes[-1] == ("half_open", "closed", "after a successful probe")
    # At the longest wait only the 1st, 10th, 100th and 1000th probe are reported (the 2nd and the 3rd, which closed it,
    # are not); closing always is.
    assert _transitions(changes) == ["closed->open"] + ["open->half_open", "half_open->open"] * 5 + ["half_open->closed"]
    logged = "only the 1st, 10th, 100th and 1000th are logged"
    assert changes[9:11] == [
        ("open", "half_open", f"after 300 s; one probe (the 1st at the longest wait; {logged})"),
        ("half_open", "open", "for 300 s after a failed probe (ReadTimeout), the 1st at the longest wait"),
    ]

    # A probe whose error says nothing about the endpoint is handed back; a lost probe expires after open_s.
    for _ in range(5):
        b.record(False)
    clock.advance(30)
    assert b.allow()
    b.release()
    assert b.state == "half_open" and b.allow()  # the next caller probes
    assert not b.allow()
    clock.advance(30)  # that probe never reported back
    assert b.allow()

    # In half_open only the probe's own thread decides: a late result from another thread is ignored.
    late = threading.Thread(target=b.record, args=(True,))
    late.start()
    late.join()
    assert b.state == "half_open"
    b.record(True)
    assert b.state == "closed"

    # Results that arrive while open (calls that began before it opened) leave the wait alone.
    for _ in range(5):
        b.record(False)
    until = b.snapshot()["open_until"]
    b.record(True)
    b.record(False)
    assert b.snapshot()["open_until"] == until and b.state == "open"

    # failure_threshold 0 never opens; a failing listener never reaches the caller.
    off = Breaker("off", 0, 30, clock=clock)
    for _ in range(100):
        off.record(False)
    assert off.allow() and off.snapshot()["failures"] == 0

    def boom(*args: Any) -> None:
        raise RuntimeError("the ledger is gone")

    noisy = Breaker("noisy", 1, 30, clock=clock, on_change=boom)
    noisy.record(False)
    assert noisy.state == "open"

    for bad in ({"failure_threshold": -1}, {"failure_threshold": True}, {"open_s": 0}, {"open_s": math.nan}, {"max_open_s": 10}):
        with pytest.raises(ValueError):
            Breaker("bad", **{"failure_threshold": 5, "open_s": 30, "max_open_s": 300, **bad})
    # Settings: RATCHET_STUDENT_BREAKER_FAILURES / _COOLDOWN_S are checked when the cascade is built.
    assert Settings(student_breaker_failures=5, student_breaker_cooldown_s=30).student_breaker().max_open_s == 300
    assert Settings(student_breaker_failures=0, student_breaker_cooldown_s=600).student_breaker().max_open_s == 600
    for bad_settings in (
        {"student_breaker_failures": -1},
        {"student_breaker_cooldown_s": 0},
        {"student_breaker_cooldown_s": math.inf},
    ):
        with pytest.raises(ValueError, match="RATCHET_STUDENT_BREAKER"):
            Ratchet(
                Settings(teacher_model=OPEN_TEACHER, **bad_settings),
                Ledger(":memory:"),
                RecordingProvider(B),
                lambda v: (None, ""),
            )


def _race_for_the_probe(callers: int) -> list[bool]:
    """Open a breaker, wait out its cooldown, and have `callers` threads call allow() at the same moment."""
    clock = FakeClock(0.0)
    b = Breaker("student", 1, 30, clock=clock)
    b.record(False)
    clock.advance(30)
    start = threading.Barrier(callers)
    allowed: list[bool] = []
    lock = threading.Lock()

    def caller() -> None:
        start.wait()
        ok = b.allow()
        with lock:
            allowed.append(ok)

    threads = [threading.Thread(target=caller) for _ in range(callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return allowed


@pytest.mark.req("FR-2.11")
def test_half_open_lets_exactly_one_probe_through():
    for _ in range(25):
        assert sorted(_race_for_the_probe(16)) == [False] * 15 + [True]


class _HeldCall:
    """One call through a breaker on its own thread (a breaker tells probes apart by thread): `allow()` at once, and the
    outcome when `finish(ok)` is called (`None` hands the probe back with `release()`)."""

    def __init__(self, b: Breaker) -> None:
        self._go = threading.Event()
        self._asked = threading.Event()
        self._ok: bool | None = None
        self.allowed = False
        self._thread = threading.Thread(target=self._run, args=(b,))
        self._thread.start()
        assert self._asked.wait(10)

    def _run(self, b: Breaker) -> None:
        self.allowed = b.allow()
        self._asked.set()
        if self.allowed:
            assert self._go.wait(10)
            if self._ok is None:
                b.release()
            else:
                b.record(self._ok, "" if self._ok else "ReadTimeout")

    def finish(self, ok: bool | None) -> None:
        self._ok = ok
        self._go.set()
        self._thread.join(10)


def _hung_endpoint(cooldown: float, call_s: int, probe_timeout_s: float, seconds: int = 900) -> tuple[int, list[str], int]:
    """A dead endpoint on which every call hangs for `call_s` seconds before it times out, one request a second for
    `seconds` seconds. Returns the probes made (calls allowed while the breaker was not closed), the breaker's
    transitions, and the most probes ever in flight at once."""
    clock = FakeClock(0.0)
    changes: list[str] = []
    b = Breaker(
        "student",
        5,
        cooldown,
        300,
        clock=clock,
        on_change=lambda br, old, new, why: changes.append(f"{old}->{new} {why}"),
        probe_timeout_s=probe_timeout_s,
    )
    in_flight: list[tuple[int, _HeldCall, bool]] = []  # (ends at, call, is a probe)
    probes = most_probes = 0
    for t in range(seconds):
        clock.now = float(t)
        for item in [x for x in in_flight if x[0] <= t]:
            item[1].finish(False)
            in_flight.remove(item)
        call = _HeldCall(b)
        if call.allowed:
            is_probe = b.state != "closed"
            probes += is_probe
            in_flight.append((t + call_s, call, is_probe))
            most_probes = max(most_probes, sum(1 for _, _, probe in in_flight if probe))
    for _, call, _ in in_flight:
        call.finish(False)
    return probes, changes, most_probes


@pytest.mark.req("FR-2.11", "NFR-2")
def test_a_probe_slower_than_the_cooldown_still_counts_and_backs_the_breaker_off():
    """Regression: a probe that outlived the cooldown was replaced, its failure was dropped, and the breaker stayed
    half-open for the whole outage, putting a request on the dead endpoint every cooldown without ever backing off."""
    # A 5 s cooldown, below the student's 15 s read timeout. Without probe_timeout_s a probe is presumed lost after one
    # wait and the next caller probes, but the lost probe's failure still reopens the breaker and doubles the wait.
    clock = FakeClock(0.0)
    b = Breaker("student", 1, 5, 300, clock=clock)
    b.record(False, "ReadTimeout")
    clock.advance(5)
    first = _HeldCall(b)
    assert first.allowed and b.state == "half_open"
    clock.advance(5)
    second = _HeldCall(b)  # the first is still waiting on its read timeout
    assert second.allowed
    clock.advance(5)
    first.finish(False)
    assert b.snapshot() == {"state": "open", "failures": 2, "open_until": 25.0, "open_s": 10.0}
    second.finish(False)  # arrives while open: the wait stands
    assert b.snapshot()["open_until"] == 25.0
    # A lost probe's success closes it too; the replacing probe's later result then counts like any other call.
    clock.now = 25.0
    first = _HeldCall(b)
    clock.advance(10)
    second = _HeldCall(b)
    assert first.allowed and second.allowed
    first.finish(True)
    assert b.snapshot() == {"state": "closed", "failures": 0, "open_until": None, "open_s": 5.0}
    second.finish(None)
    assert b.state == "closed"

    # With probe_timeout_s (the router sets the call's own longest time) nobody else probes while the probe can still
    # be running: exactly one probe, and its failure doubles the wait.
    clock = FakeClock(0.0)
    b = Breaker("student", 1, 5, 300, clock=clock, probe_timeout_s=37)
    b.record(False, "ReadTimeout")
    clock.advance(5)
    probe = _HeldCall(b)
    assert probe.allowed
    for wait in (5, 10, 31):
        clock.now = 5.0 + wait
        assert not _HeldCall(b).allowed
    probe.finish(False)
    assert b.snapshot() == {"state": "open", "failures": 2, "open_until": 46.0, "open_s": 10.0}
    clock.now = 46.0
    probe = _HeldCall(b)
    clock.advance(37)  # never reported back within its own longest time: lost, so the next caller probes
    replacement = _HeldCall(b)
    assert probe.allowed and replacement.allowed
    replacement.finish(None)
    probe.finish(False)  # the lost probe reports at last, still half-open: it counts
    assert b.snapshot()["state"] == "open" and b.snapshot()["open_s"] == 20.0
    for bad in (-1, math.inf, math.nan, True):
        with pytest.raises(ValueError, match="probe_timeout_s"):
            Breaker("bad", 5, 30, 300, probe_timeout_s=bad)

    # The reviewer's outage: 900 s of a hung endpoint, a request every second, every call held for its timeout. The
    # waits double to 300 s however long a call takes, and with probe_timeout_s only one probe is ever out at once.
    backing_off = [
        "closed->open after 5 failures (ReadTimeout)",
        "open->half_open after 30 s; one probe",
        "half_open->open for 60 s after a failed probe (ReadTimeout)",
        "open->half_open after 60 s; one probe",
        "half_open->open for 120 s after a failed probe (ReadTimeout)",
    ]
    for cooldown, call_s, probe_timeout_s in ((30, 17, 0), (30, 17, 39), (30, 60, 0), (30, 60, 82), (5, 15, 0), (5, 15, 37)):
        probes, changes, most_probes = _hung_endpoint(cooldown, call_s, probe_timeout_s)
        assert changes[-1].startswith("half_open->open for 300 s"), (cooldown, call_s, changes)
        if cooldown == 30:
            assert changes[:5] == backing_off
        spells = sum(1 for c in changes if c.startswith("open->half_open"))
        assert 4 <= spells <= 8 and spells <= probes <= 2 * spells, (cooldown, call_s, probes, changes)
        if probe_timeout_s:
            assert most_probes == 1 and probes == spells


@pytest.mark.req("FR-2.11", "NFR-2")
def test_router_never_replaces_a_probe_that_can_still_be_running():
    """The router gives each breaker the student call's longest time (connect, write, read and pool timeouts, plus 5 s),
    so a request that arrives while the probe waits on a hung endpoint skips the student instead of probing again."""
    clock = FakeClock()
    entered, release = threading.Event(), threading.Event()
    hang = threading.Event()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        if hang.is_set():
            entered.set()
            assert release.wait(10)
        raise httpx.ReadTimeout("timed out", request=request)

    s = Settings(student_timeout_s=15.0, student_connect_timeout_s=2.0)
    transport = httpx.MockTransport(handler)
    student = OpenAICompatProvider("https://student.test/v1", "", timeout=s.student_timeout(), transport=transport)
    assert student.longest_call_s() == 5 + 2 + 10 + 15  # pool, connect, write, read
    led, _, teacher, r = _breaker_cascade(student, clock, student_breaker_cooldown_s=5.0)
    for i in range(5):
        assert r.handle(_ask(i), TRIAGE).route == "student_error"
    timeout = "UpstreamError 504 upstream_timeout"  # the provider's ReadTimeout, as an event names it
    assert _circuits(led) == [f"student: closed->open after 5 failures ({timeout})"]

    clock.advance(5)
    hang.set()
    started = clock.now
    with ThreadPoolExecutor(1) as pool:
        probe = pool.submit(r.handle, _ask("probe"), TRIAGE)
        assert entered.wait(10)
        for wait in (5, 10, 36.9):  # past the cooldown, but the probe's own timeouts (32 s + 5 s) have not run out
            clock.now = started + wait
            assert r.handle(_ask(f"during {wait}"), TRIAGE).route == "student_breaker"
        assert len(seen) == 6
        release.set()
        assert probe.result(10).route == "student_error"
    assert _circuits(led)[-2:] == [
        "student: open->half_open after 5 s; one probe",
        f"student: half_open->open for 10 s after a failed probe ({timeout})",
    ]
    assert r.health()["breakers"]["student"]["open_s"] == 10.0 and len(teacher.calls) == 9


@pytest.mark.req("FR-2.11")
def test_circuit_events_at_the_longest_wait_are_throttled():
    """A long outage repeats the same probe every 300 s; only the 1st, 10th, 100th and 1000th of those are reported, so
    the dashboard's release log (the last 40 events) keeps the release history. Closing is always reported."""
    clock = FakeClock(0.0)
    changes: list[str] = []
    b = Breaker("student", 5, 30, 300, clock=clock, on_change=lambda br, old, new, why: changes.append(f"{old}->{new} {why}"))

    def outage(failed_probes: int) -> None:
        for _ in range(5):
            b.record(False, "ConnectError")
        for _ in range(failed_probes):
            clock.now = b.snapshot()["open_until"]
            assert b.allow()
            b.record(False, "ConnectError")

    outage(4 + 120)  # 30 -> 60 -> 120 -> 240 -> 300 s, then 120 probes at 300 s (10 hours)
    assert len(changes) == 1 + 2 * 4 + 2 * 3
    assert changes[9:] == [
        "open->half_open after 300 s; one probe (the 1st at the longest wait; only the 1st, 10th, 100th and 1000th are logged)",
        "half_open->open for 300 s after a failed probe (ConnectError), the 1st at the longest wait",
        "open->half_open after 300 s; one probe (the 10th at the longest wait; only the 1st, 10th, 100th and 1000th are logged)",
        "half_open->open for 300 s after a failed probe (ConnectError), the 10th at the longest wait",
        "open->half_open after 300 s; one probe (the 100th at the longest wait; only the 1st, 10th, 100th and 1000th are logged)",
        "half_open->open for 300 s after a failed probe (ConnectError), the 100th at the longest wait",
    ]
    assert b.snapshot()["failures"] == 5 + 124  # every probe still counts
    clock.now = b.snapshot()["open_until"]
    assert b.allow()  # the 121st probe at 300 s: not reported
    b.record(True)
    assert changes[-1] == "half_open->closed after a successful probe" and len(changes) == 16

    # Closing starts the count again: the next outage's first probe at the longest wait is reported.
    changes.clear()
    outage(5)
    assert len(changes) == 1 + 2 * 5 and "(the 1st at the longest wait;" in changes[-2]


@pytest.mark.req("FR-2.11", "NFR-2")
def test_endpoint_failures_are_told_from_request_errors():
    request = httpx.Request("POST", TEACHER_URL)
    down = [
        httpx.ReadTimeout("t"),
        httpx.ConnectError("c"),
        httpx.RemoteProtocolError("p"),
        UpstreamError(504, code="upstream_timeout"),
        UpstreamError(502, code="bad_upstream_response"),
        UpstreamError(500),
        UpstreamError(408),
        UpstreamError(429),
        httpx.HTTPStatusError("x", request=request, response=httpx.Response(503, request=request)),
    ]
    answered = [
        UpstreamError(400, code="context_length_exceeded"),
        UpstreamError(404),
        AdapterMissing("student-v3"),
        UpstreamError(401),
    ]
    assert [endpoint_down(e) for e in down] == [True] * len(down)
    assert [endpoint_down(e) for e in answered] == [False] * len(answered)
    assert [endpoint_down(e) for e in (KeyError("unknown request"), ValueError("bad"))] == [None, None]
    e = CircuitOpen("student")
    assert (e.status, e.code, e.breaker, str(e)) == (
        503,
        "circuit_open",
        "student",
        "upstream 503 circuit_open: the student circuit is open",
    )


def _breaker_cascade(
    student: RecordingProvider, clock: FakeClock, **settings: Any
) -> tuple[Ledger, int, RecordingProvider, Ratchet]:
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    teacher = RecordingProvider(B)
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "teacher_reasoning_tokens": 0,
        "student_breaker_failures": 5,
        "student_breaker_cooldown_s": 30.0,
    }
    s = Settings(**{**base, **settings})
    return led, live, teacher, Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri), seed=1, clock=clock)


@pytest.mark.req("FR-2.11", "FR-1.8", "INV-7", "NFR-2")
def test_open_student_breaker_sends_requests_straight_to_the_teacher():
    clock = FakeClock()
    student = RecordingProvider(A, raise_on={"student-v1"}, exc=httpx.ReadTimeout)
    led, live, teacher, r = _breaker_cascade(student, clock)
    teacher_cost = Settings().price(OPEN_TEACHER).cost(10, 5)

    for i in range(5):
        out = r.handle(_ask(i), TRIAGE)
        assert (out.served_by, out.route, out.escalated) == ("teacher", "student_error", True)
    assert len(student.calls) == 5 and _circuits(led) == ["student: closed->open after 5 failures (ReadTimeout)"]

    # Open: the 6th request never reaches the student, is answered by the teacher and is not another student error.
    out = r.handle(_ask(5), TRIAGE)
    assert len(student.calls) == 5 and len(teacher.calls) == 6
    assert (out.served_by, out.route, out.escalated, out.confidence, out.text) == ("teacher", "student_breaker", True, None, B)
    row = _last_request(led)
    assert (row["route"], row["served_by"], row["escalated"], row["confidence"], row["student_version"]) == (
        "student_breaker",
        "teacher",
        1,
        None,
        live,
    )
    assert row["cost_usd"] == pytest.approx(teacher_cost) == row["baseline_cost_usd"]  # no student samples billed
    assert _counters(r)["student_errors"] == 5
    assert r.health()["breakers"] == {
        "student": {"state": "open", "failures": 5, "open_until": clock.now + 30, "open_s": 30.0},
        "teacher": {"state": "closed", "failures": 0, "open_until": None, "open_s": 30.0},  # the teacher's own (ADR 0019)
    }
    assert len(led.examples()) == 6  # the teacher's answers still become training data

    clock.advance(29)
    assert r.handle(_ask(6), TRIAGE).route == "student_breaker" and len(student.calls) == 5

    # After the cooldown one probe goes through; it fails, so the breaker waits twice as long.
    clock.advance(1)
    assert r.handle(_ask(7), TRIAGE).route == "student_error" and len(student.calls) == 6
    clock.advance(30)
    assert r.handle(_ask(8), TRIAGE).route == "student_breaker" and len(student.calls) == 6

    # The endpoint is back: the next probe is served by the student and closes the breaker.
    student.raise_on.clear()
    clock.advance(30)
    out = r.handle(_ask(9), TRIAGE)
    assert (out.served_by, out.route, out.version, out.text) == ("student", "student", live, A) and len(student.calls) == 7
    assert r.handle(_ask(10), TRIAGE).route == "student" and len(student.calls) == 8
    assert _circuits(led) == [
        "student: closed->open after 5 failures (ReadTimeout)",
        "student: open->half_open after 30 s; one probe",
        "student: half_open->open for 60 s after a failed probe (ReadTimeout)",
        "student: open->half_open after 60 s; one probe",
        "student: half_open->closed after a successful probe",
    ]
    assert r.health()["breakers"]["student"] == {"state": "closed", "failures": 0, "open_until": None, "open_s": 30.0}
    assert _counters(r)["student_errors"] == 6
    assert [e["kind"] for e in led.events() if e["kind"] == "student_error"] == ["student_error"]  # the 1st only


@pytest.mark.req("FR-2.11", "FR-2.10", "INV-15", "NFR-2")
def test_shadow_breaker_is_its_own():
    """A bad staging adapter opens the shadow's breaker, never the one live traffic depends on."""
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    cand = _version(led, "shadow", {"threshold": 0.5})
    live_student = RecordingProvider(A)
    shadow_student = RecordingProvider(B, raise_on={"student-v2"}, exc=_timeout)
    students = {live: live_student, cand: shadow_student}
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, student_breaker_failures=5, student_breaker_cooldown_s=30)
    r = Ratchet(s, led, RecordingProvider(B), lambda v: (students[v.id], v.adapter_uri), seed=1, clock=FakeClock())

    for i in range(10):
        out = r.handle(_ask(i), TRIAGE)
        assert (out.served_by, out.route) == ("student", "student")
    assert len(live_student.calls) == 10 and len(shadow_student.calls) == 5
    assert _counters(r) == {**NO_ERRORS, "shadow_errors": 5}  # skipped samples are not more errors
    assert _events(led) == [
        ("shadow_error", f"v{cand} sampling: ReadTimeout"),
        ("circuit", "shadow: closed->open after 5 failures (ReadTimeout)"),
    ]
    breakers = r.health()["breakers"]
    assert (breakers["student"]["state"], breakers["shadow"]["state"]) == ("closed", "open")
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE shadow_version IS NOT NULL").fetchone()[0] == 0


@pytest.mark.req("FR-2.11")
def test_breakers_are_per_endpoint_and_role():
    clock = FakeClock()
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    canary = _version(led, "canary", {"threshold": 0.5}, pct=50)
    student = RecordingProvider(A, raise_on={"student-v1", "student-v2"}, exc=httpx.ConnectError)
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, student_breaker_failures=5, student_breaker_cooldown_s=30)
    r = Ratchet(s, led, RecordingProvider(B), lambda v: (student, v.adapter_uri), seed=3, clock=clock)

    # Live and canary on one endpoint are one failure domain: five failures between them open it for both.
    routes = [r.handle(_ask(i), TRIAGE).route for i in range(8)]
    assert routes == ["student_error"] * 5 + ["student_breaker"] * 3
    assert {c.model for c in student.calls} == {"student-v1", "student-v2"} and len(student.calls) == 5
    versions = [row[0] for row in led.db.execute("SELECT student_version FROM requests ORDER BY id")]
    assert set(versions) == {live, canary}

    # Keyed by base URL (so two clients of one endpoint share it), by class without one, and by role.
    a1, a2 = OpenAICompatProvider("https://a.test/v1", "k"), OpenAICompatProvider("https://a.test/v1/", "k")
    other = OpenAICompatProvider("https://b.test/v1", "k")
    assert r._breaker("student", a1) is r._breaker("student", a2) and r._breaker("student", a1).name == "student-2"
    assert r._breaker("student", other).name == "student-3"
    assert r._breaker("student", RecordingProvider(A)) is r._breaker("student", student)  # a new object per call: one breaker
    assert r._breaker("shadow", a1).name == "shadow" and r._breaker("shadow", a1) is not r._breaker("student", a1)
    assert sorted(r.health()["breakers"]) == ["shadow", "student", "student-2", "student-3", "teacher"]


@pytest.mark.req("FR-2.11", "INV-7", "NFR-2")
def test_lost_adapter_is_reported_and_does_not_open_the_breaker():
    def missing(model: str) -> Any:
        return lambda message: UpstreamError(404, f"The model `{model}` does not exist.", type="NotFoundError")

    student = RecordingProvider(A, raise_on={"student-v1"}, exc=missing("student-v1"))
    lost: list[Any] = []
    led, live, teacher, r = _breaker_cascade(student, FakeClock())
    r.on_adapter_missing = lost.append
    for i in range(10):
        out = r.handle(_ask(i), TRIAGE)
        assert (out.served_by, out.route) == ("teacher", "student_error")
    assert lost == [led.version(live)] * 10  # every time: AdapterReloader keeps it to one reload a minute
    assert len(student.calls) == 10 and r.health()["breakers"]["student"]["state"] == "closed"
    errors = [e["detail"] for e in led.events() if e["kind"] == "student_error"]
    assert errors == ["v1: AdapterMissing 404 adapter_missing"] * 2  # never the message (INV-12, ADR 0014)

    # A 404 that does not name the adapter (a wrong base URL) is not a lost adapter.
    student.exc = lambda message: UpstreamError(404, "Not Found")
    r.handle(_ask("wrong path"), TRIAGE)
    student.exc = missing("student-v10")  # nor is one that names another adapter
    r.handle(_ask("other"), TRIAGE)
    assert len(lost) == 10

    # The hosted fallback's model is not an adapter: its 404 is a setting to fix, not something to reload.
    fallback = RecordingProvider(A, raise_on={"nano"}, exc=missing("nano"))
    r_fb = Ratchet(r.s, led, teacher, lambda v: (fallback, "nano"), clock=FakeClock(), on_adapter_missing=lost.append)
    assert r_fb.handle(_ask("fallback"), TRIAGE).route == "student_error" and len(lost) == 10

    # A failing callback never fails the request.
    def broken(version: Any) -> None:
        raise RuntimeError("queue is gone")

    student.exc = missing("student-v1")
    r.on_adapter_missing = broken
    assert r.handle(_ask("broken"), TRIAGE).served_by == "teacher"
    r.on_adapter_missing = None  # nothing wired (mock mode, the hosted fallback): it still escalates
    assert r.handle(_ask("unwired"), TRIAGE).route == "student_error"

    # A shadow whose adapter was lost asks for its reload too, and still never fails the request.
    cand = _version(led, "shadow", {"threshold": 0.5})
    shadow_student = RecordingProvider(B, raise_on={f"student-v{cand}"}, exc=missing(f"student-v{cand}"))
    healthy = RecordingProvider(A)
    students = {live: healthy, cand: shadow_student}
    r_sh = Ratchet(
        r.s, led, teacher, lambda v: (students[v.id], v.adapter_uri), clock=FakeClock(), on_adapter_missing=lost.append
    )
    assert r_sh.handle(_ask("shadow"), TRIAGE).route == "student"
    assert lost[-1] == led.version(cand) and r_sh.health()["breakers"]["shadow"]["state"] == "closed"


class _NoChoices:
    """An endpoint that answers 200 with an empty list of choices."""

    def complete(self, messages: Any, model: str, *args: Any, **kwargs: Any) -> list[Any]:
        return []


@pytest.mark.req("FR-2.11", "INV-7")
def test_errors_without_an_http_status_say_nothing_to_the_breaker():
    """A provider bug (or the mock world not knowing a request) is not the endpoint being down: it never opens the
    breaker, and a probe that ends that way is handed back for the next request. An empty answer is the endpoint failing."""
    clock = FakeClock()
    student = RecordingProvider(A, raise_on={"student-v1"}, exc=KeyError)
    led, live, teacher, r = _breaker_cascade(student, clock)
    for i in range(10):
        assert r.handle(_ask(i), TRIAGE).route == "student_error"
    assert r.health()["breakers"]["student"] == {"state": "closed", "failures": 0, "open_until": None, "open_s": 30.0}

    # Open it with real failures, then let the probe end in a KeyError: the next request probes again.
    student.exc = httpx.ConnectError
    for i in range(5):
        r.handle(_ask(f"down {i}"), TRIAGE)
    clock.advance(30)
    student.exc = KeyError
    assert r.handle(_ask("probe 1"), TRIAGE).route == "student_error" and len(student.calls) == 16
    assert r.health()["breakers"]["student"]["state"] == "half_open"
    student.raise_on.clear()
    assert r.handle(_ask("probe 2"), TRIAGE).route == "student" and len(student.calls) == 17
    assert r.health()["breakers"]["student"]["state"] == "closed"

    # 200 with no choices is a bad answer from the endpoint: 502 bad_upstream_response, counted.
    led2, _, _, r2 = _breaker_cascade(_NoChoices(), FakeClock())  # type: ignore[arg-type]
    assert [r2.handle(_ask(i), TRIAGE).route for i in range(6)] == ["student_error"] * 5 + ["student_breaker"]
    assert [e["detail"] for e in led2.events()][:1] == ["v1: UpstreamError 502 bad_upstream_response"]


@pytest.mark.req("FR-2.11", "NFR-2")
def test_adapter_reloader_rate_limits_and_reports():
    clock = FakeClock()
    led = Ledger(":memory:")
    v3 = led.version(_version(led, "live", adapter_uri="student-v3"))
    v4 = led.version(_version(led, "canary", adapter_uri="student-v4"))
    loads: list[str] = []
    failing = {"student-v4"}

    def load(name: str) -> None:
        loads.append(name)
        if name in failing:
            raise _unavailable()

    reloader = AdapterReloader(load, led, interval_s=60, clock=clock)
    assert reloader.request(v3) and reloader.request(v4)
    assert not reloader.request(v3)  # queued less than 60 s ago
    assert reloader.join(5)
    clock.advance(59)
    assert not reloader.request(v3) and not reloader.request(v4)
    clock.advance(1)
    failing.clear()
    assert reloader.request(v4) and reloader.join(5)
    assert loads == ["student-v3", "student-v4", "student-v4"]
    assert _events(led) == [
        ("adapter_reloaded", f"v{v3.id} student-v3: loaded again after the student endpoint answered 404"),
        ("adapter_reload_failed", f"v{v4.id} student-v4: HTTPStatusError 503"),  # never the URL
        ("adapter_reloaded", f"v{v4.id} student-v4: loaded again after the student endpoint answered 404"),
    ]

    # The thread outlives a failing ledger write; after close() nothing more is queued.
    def no_events(kind: str, detail: str) -> None:
        raise OSError("disk full")

    led.event = no_events  # type: ignore[method-assign]
    clock.advance(60)
    assert reloader.request(v3) and reloader.join(5)
    reloader.close()
    clock.advance(60)
    assert not reloader.request(v3) and loads[-1] == "student-v3" and len(loads) == 4
    with pytest.raises(RuntimeError, match="RATCHET_STUDENT_BASE"):  # no endpoint, nothing to load into
        adapter_reloader(Settings(student_base=""), led)


@pytest.mark.req("FR-2.11")
def test_adapter_missing_is_the_routers_predicate(gw_env, tmp_path, monkeypatch):
    """The router's lost-adapter test, shared with scripts/day1_check.py: a 404 whose message names the adapter as a
    whole name, in vLLM v0.18.0's OpenAI-shaped body and in the older top-level one."""

    def error(status: int, body: Any) -> UpstreamError:
        return UpstreamError.from_response(httpx.Response(status, json=body))

    v018 = {
        "error": {"message": "The model `student-v3` does not exist.", "type": "NotFoundError", "param": "model", "code": 404}
    }
    legacy = {"object": "error", "message": "The model `student-v3` does not exist.", "type": "NotFoundError", "code": 404}
    assert adapter_missing(error(404, v018), "student-v3") and adapter_missing(error(404, legacy), "student-v3")
    assert not adapter_missing(error(404, v018), "student-v30") and not adapter_missing(error(404, v018), "student-v3.1")
    for other in ("student-v30", "student-v3.1"):
        assert not adapter_missing(error(404, {"error": {"message": f"The model `{other}` does not exist."}}), "student-v3")
    assert not adapter_missing(error(400, v018), "student-v3")  # the right text, the wrong status
    assert not adapter_missing(httpx.ConnectError("refused"), "student-v3") and not adapter_missing(error(404, v018), "")

    # Through a live Runtime on v0.18.0's bodies: a request for the live adapter still queues exactly one reload.
    gw = gw_env()
    for name in [n for n in os.environ if n.startswith("RATCHET_")]:
        monkeypatch.delenv(name)
    student = FakeOpenAIServer(A, loaded=set(), errors="v0.18.0")
    teacher = FakeOpenAIServer(B)
    rt = gw.Runtime(
        _live_settings(tmp_path, audit_rate=0.0),
        transports={"teacher": teacher.transport, "student": student.transport},
        clock=FakeClock(),
    )
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.5}, adapter_uri="student-v3")
    r = TestClient(gw.app).post(CHAT, json={"messages": _ask(0)})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert rt.reloader.join(5)
    (load,) = student.calls_to("/load_lora_adapter")
    assert load.json == {"lora_name": "student-v3", "lora_path": "/data/adapters/student-v3"}


@pytest.mark.req("FR-2.11", "FR-8.2", "NFR-2", "INV-7")
def test_live_gateway_reloads_a_lost_adapter_once_a_minute(gw_env, tmp_path, monkeypatch):
    """vLLM after a restart: every request for the adapter is a 404. The request escalates, the breaker stays closed (the
    endpoint answered), and one reload is queued per minute however many requests hit it."""
    gw = gw_env()
    for name in [n for n in os.environ if n.startswith("RATCHET_")]:
        monkeypatch.delenv(name)
    loads_fail = [True]
    student = FakeOpenAIServer(A, loaded=set(), load=lambda body: (500, "boom") if loads_fail[0] else (200, "Success"))
    teacher = FakeOpenAIServer(B)
    clock = FakeClock()
    rt = gw.Runtime(
        _live_settings(tmp_path, audit_rate=0.0),
        transports={"teacher": teacher.transport, "student": student.transport},
        clock=clock,
    )
    monkeypatch.setattr(gw, "rt", rt)
    live = _version(rt.ledger, "live", {"threshold": 0.5}, adapter_uri="student-v3")
    c = TestClient(gw.app)

    for i in range(10):
        r = c.post(CHAT, json={"messages": _ask(i)})
        assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
        assert rt.reloader.join(5)
        clock.advance(5)
    (load,) = student.calls_to("/load_lora_adapter")  # 10 requests within 60 s: one reload
    assert load.json == {"lora_name": "student-v3", "lora_path": "/data/adapters/student-v3"}
    assert load.headers["authorization"] == "Bearer sk-student-endpoint-token"
    assert rt.ratchet.health()["breakers"]["student"]["state"] == "closed"
    rows = [tuple(row) for row in rt.ledger.db.execute("SELECT served_by, route FROM requests")]
    assert rows == [("teacher", "student_error")] * 10
    events = [(e["kind"], e["detail"]) for e in rt.ledger.events()]
    assert ("adapter_reload_failed", f"v{live} student-v3: HTTPStatusError 500") in events
    assert all("student.test" not in detail for _, detail in events)

    # A minute later the next 404 tries again; this time vLLM loads it, and the student serves again.
    loads_fail[0] = False
    clock.advance(20)
    assert c.post(CHAT, json={"messages": _ask(10)}).json()["ratchet"]["served_by"] == "teacher"
    assert rt.reloader.join(5) and len(student.calls_to("/load_lora_adapter")) == 2
    r = c.post(CHAT, json={"messages": _ask(11)})
    assert (r.json()["ratchet"]["served_by"], r.json()["model"]) == ("student", "student-v3")
    assert [k for k, _ in events if k.startswith("adapter_")] + [rt.ledger.events()[-1]["kind"]] == [
        "adapter_reload_failed",
        "adapter_reloaded",
    ]
    rt.reloader.close()

    # Mock mode, and live mode on the hosted fallback (no endpoint to load into), have no reloader.
    fallback = gw.Runtime(_live_settings(tmp_path, db_path=str(tmp_path / "fb.db"), student_base=""))
    assert fallback.reloader is None and fallback.ratchet.on_adapter_missing is None
    assert gw.Runtime(Settings(mode="mock", mock_warmup_days=0)).reloader is None


@pytest.mark.req("FR-2.11", "INV-8")
def test_demo_student_outage_opens_and_closes_the_breaker(gw_env, monkeypatch):
    gw = gw_env()
    clock = FakeClock()
    rt = gw.Runtime(
        Settings(mode="mock", mock_warmup_days=0, student_breaker_failures=5, student_breaker_cooldown_s=30), clock=clock
    )
    monkeypatch.setattr(gw, "rt", rt)
    rt.fleet.train([], "base", "student-v1")  # an untrained adapter; with threshold 0 it serves whatever it says
    _version(rt.ledger, "live", {"threshold": 0.0})
    c = TestClient(gw.app)

    def ask() -> str:
        r = c.post(CHAT, json={"messages": rt.stream.sample(rt.ledger.day).messages()})
        assert r.status_code == 200, r.text
        return rt.ledger.db.execute("SELECT route FROM requests ORDER BY id DESC LIMIT 1").fetchone()[0]

    assert ask() == "student"
    assert c.post("/ratchet/demo/student-outage?seconds=1").json()["seconds"] == 5  # clamped to 5..300
    assert c.post("/ratchet/demo/student-outage?seconds=100000").json()["seconds"] == 300
    r = c.post("/ratchet/demo/student-outage")  # 60 s by default
    assert r.status_code == 200 and r.json()["seconds"] == 60 and r.json()["until"] > time.time() + 59

    assert [ask() for _ in range(6)] == ["student_error"] * 5 + ["student_breaker"]  # opens; the teacher answers
    clock.advance(30)
    assert ask() == "student_error"  # the probe: still down, so it waits 60 s
    clock.advance(30)
    assert ask() == "student_breaker"
    clock.advance(31)  # the outage ended at 60 s
    assert ask() == "student"  # the probe succeeds and closes the breaker
    assert _circuits(rt.ledger) == [
        "student: closed->open after 5 failures (ConnectError)",
        "student: open->half_open after 30 s; one probe",
        "student: half_open->open for 60 s after a failed probe (ConnectError)",
        "student: open->half_open after 60 s; one probe",
        "student: half_open->closed after a successful probe",
    ]
    faults = [e["detail"] for e in rt.ledger.events() if e["kind"] == "fault"]
    assert faults[-1] == "student endpoint down for 60 s (demo fault injection)"
    stats = c.get("/ratchet/stats").json()
    (task,) = stats["tasks"]
    assert task["routes"] == {"student_error": 6, "student": 2, "student_breaker": 2}
    assert sum(task["routes"].values()) == task["requests"] == 10
    assert any(e["kind"] == "circuit" for e in stats["events"])


@pytest.mark.req("FR-2.11", "FR-2.9", "FR-2.12", "FR-2.15", "FR-1.41")
def test_every_request_row_has_a_route():
    clock = FakeClock()
    led = Ledger(":memory:")
    student = RecordingProvider(A)
    teacher = RecordingProvider(B)
    fallback = RecordingProvider(B)
    s = Settings(
        teacher_model=OPEN_TEACHER,
        samples=3,
        audit_rate=0.0,
        student_breaker_failures=2,
        student_breaker_cooldown_s=30,
        teacher_fallback_model=FALLBACK_TEACHER,
        on_teacher_failure="serve_student",
        unknown_tasks="teacher",
    )
    r = Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri), seed=1, clock=clock, fallback_teacher=fallback)

    seen = [r.handle(_ask("cold"), TRIAGE).route]  # no version yet
    seen.append(r.handle([{"role": "user", "content": "Question: what is LoRA?"}], "research-plan").route)
    seen.append(r.teacher_only(_ask("image"), TRIAGE).route)  # the pinned path
    seen.append(r.handle(_ask("not registered"), "unregistered-task").route)  # RATCHET_UNKNOWN_TASKS=teacher
    live = _version(led, "live", {"threshold": 0.5})
    seen.append(r.handle(_ask("sure"), TRIAGE).route)
    student.text = [A, A, A, B]  # confident on its 3 scored samples, but only 3 of the 4 drawn agree
    seen.append(r.handle(_ask("four choices"), TRIAGE, n=4).route)
    student.text = A
    led.update_eval(live, {"threshold": 0.99})
    seen.append(r.handle(_ask("unsure"), TRIAGE).route)
    # The release gate disabled the task on this version (FR-14.1): the student is not called.
    disabled = {"spec_hash": tasks.get(TRIAGE).spec_hash, "threshold": 1.01, "disabled": "regressed"}
    led.update_eval(live, {"threshold": 0.99, "tasks": {TRIAGE: disabled}})
    seen.append(r.handle(_ask("disabled"), TRIAGE).route)
    led.update_eval(live, {"threshold": 0.99})
    teacher.raise_on.add(OPEN_TEACHER)  # the primary teacher is down: the fallback answers
    seen.append(r.handle(_ask("unsure, primary down"), TRIAGE).route)
    fallback.raise_on.add(FALLBACK_TEACHER)  # both are down: the student's own answer, degraded
    seen.append(r.handle(_ask("unsure, both down"), TRIAGE).route)
    teacher.raise_on.clear()
    student.raise_on.add("student-v1")
    seen += [r.handle(_ask(f"down {i}"), TRIAGE).route for i in range(3)]
    led.set_flag("student_disabled", True, "cli:ops")  # an operator's kill switch (ADR 0064): not even a breaker probe
    seen.append(r.handle(_ask("student off"), TRIAGE).route)
    # A tool request and a part only the teacher reads go to the teacher alone, each with its own route (FR-1.41).
    weather = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]
    seen.append(r.handle(_ask("weather?"), TRIAGE, params=GenParams(tools=weather)).route)
    seen.append(r.teacher_only(_ask("image"), TRIAGE, route="multimodal").route)
    assert seen == [
        "cold_start",
        "pinned",
        "pinned",
        "unknown_task",
        "student",
        "n_choices",
        "confidence",
        "task_disabled",
        "fallback_teacher",
        "degraded",
        "student_error",
        "student_error",
        "student_breaker",
        "student_disabled",
        "tools",
        "multimodal",
    ]
    # `demo_split` needs the hosted student's row and RATCHET_DEMO_SPLIT: tests/test_demo_split.py serves it.
    assert set(seen) == set(ROUTES) - {"demo_split"}
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE route IS NULL").fetchone()[0] == 0
    by_task = {t["task"]: t["routes"] for t in led.stats_by_task()}
    assert by_task == {
        TRIAGE: {
            "student_error": 2,
            "cold_start": 1,
            "confidence": 1,
            "degraded": 1,
            "fallback_teacher": 1,
            "pinned": 1,
            "student": 1,
            "student_breaker": 1,
            "n_choices": 1,
            "student_disabled": 1,
            "task_disabled": 1,
            "tools": 1,
            "multimodal": 1,
        },
        "research-plan": {"pinned": 1},
        "unregistered-task": {"unknown_task": 1},
    }
    assert list(by_task[TRIAGE])[0] == "student_error"  # most frequent first
    assert [(d["degraded"], d["fallback_teacher"]) for d in led.stats_by_day()] == [(1, 1)]


# ---------- when the teacher is down: its breaker, degraded mode, the fallback teacher (FR-2.12, ADR 0019) ----------
def _teacher_down(
    teacher: RecordingProvider,
    clock: FakeClock,
    *,
    students: dict[str, RecordingProvider] | None = None,
    fallback: Any = None,
    **settings: Any,
) -> tuple[Ledger, int, Ratchet]:
    """A live version whose unanimous student (confidence 0.98) is below its threshold (0.99), so every request needs
    the teacher. `students` maps adapter names to their providers (default: student-v1 answers A)."""
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.99})
    students = students or {"student-v1": RecordingProvider(A)}
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "teacher_reasoning_tokens": 0,
        "teacher_breaker_failures": 5,
        "teacher_breaker_cooldown_s": 30.0,
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "store_fallback_teacher": False,
    }
    s = Settings(**{**base, **settings})
    r = Ratchet(
        s, led, teacher, lambda v: (students[v.adapter_uri], v.adapter_uri), seed=1, clock=clock, fallback_teacher=fallback
    )
    return led, live, r


def _requests(led: Ledger) -> int:
    return int(led.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0])


class TokenFactory:
    """Token Factory behind MockTransport, switched by the test: a model or a host in `down` answers 503 (no
    `Retry-After`, so the provider retries it twice without waiting), anything else answers `text` under the model it
    was asked for. Records (host, model, Authorization) for every request. Thread-safe."""

    def __init__(self, text: str = B, down: Any = ()) -> None:
        self.text = text
        self.down = set(down)
        self.requests: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        with self._lock:
            self.requests.append((request.url.host, model, request.headers.get("authorization", "")))
        if model in self.down or request.url.host in self.down:
            return httpx.Response(503, json={"error": {"message": f"overloaded ({request.url})", "type": "server_error"}})
        choice = {"message": {"role": "assistant", "content": self.text}, "finish_reason": "stop"}
        return httpx.Response(
            200, json={"model": model, "choices": [choice], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _live_teacher_down(
    gw_env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, teacher: TokenFactory, clock: FakeClock, **overrides: Any
) -> tuple[Any, Any, FakeOpenAIServer]:
    """A live gateway on `teacher`, with a student endpoint that answers A as student-v1 and B as any other adapter.
    The teacher's waits between retries are not slept."""
    gw = gw_env()
    for name in [n for n in os.environ if n.startswith("RATCHET_")]:  # e.g. RATCHET_ON_TEACHER_FAILURE in the shell
        monkeypatch.delenv(name)
    student = FakeOpenAIServer(lambda body: A if body["model"] == "student-v1" else B)
    transports = {"teacher": teacher.transport, "student": student.transport}
    settings = _live_settings(tmp_path, audit_rate=0.0, **overrides)
    rt = gw.Runtime(settings, transports=transports, sleep=lambda seconds: None, clock=clock)
    monkeypatch.setattr(gw, "rt", rt)
    return gw, rt, student


def _stream_meta(text: str) -> dict[str, Any]:
    """The `ratchet` block of a streamed response (it rides on the finish chunk)."""
    chunks = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
    return next(c["ratchet"] for c in chunks if "ratchet" in c)


@pytest.mark.req("FR-2.12", "NFR-2", "FR-1.8", "INV-7")
def test_open_teacher_breaker_answers_503_at_once_with_retry_after(gw_env, tmp_path, monkeypatch):
    """Spec: 5 failed escalations, then the 6th is a 503 with Retry-After and makes no teacher call. Live Runtime at cold
    start (every request needs the teacher), the real provider and its retries: each failed request is a 502 after
    three attempts; once the breaker is open a request is a 503 `teacher_circuit_open` at once, with the time until its
    next probe as `Retry-After`; a probe after the cooldown that finds the teacher back closes it."""
    openai, _ = sdk()
    clock = FakeClock()
    teacher = TokenFactory(down={OPEN_TEACHER})
    gw, rt, _ = _live_teacher_down(gw_env, tmp_path, monkeypatch, teacher, clock)
    c = TestClient(gw.app)

    for i in range(5):
        r = c.post(CHAT, json={"messages": _ask(i)})
        assert (r.status_code, r.json()["error"]["code"]) == (502, "teacher_unavailable")
    assert len(teacher.requests) == 15  # each call is retried twice before it counts once
    assert _circuits(rt.ledger) == ["teacher: closed->open after 5 failures (UpstreamError 503)"]

    r = c.post(CHAT, json={"messages": _ask(5)})
    err = r.json()["error"]
    assert (r.status_code, err["type"], err["code"], err["param"]) == (503, "api_error", "teacher_circuit_open", None)
    assert r.headers["retry-after"] == "30" and "x-should-retry" not in r.headers
    assert r.json()["detail"] == err["message"] and "retry after 30 s" in err["message"] and "teacher.test" not in r.text
    assert len(teacher.requests) == 15  # no teacher call

    clock.advance(12.5)
    calls: list[Any] = []
    client = make_openai_client(gw.app, calls=calls, max_retries=0)
    with pytest.raises(openai.InternalServerError) as e:
        client.chat.completions.create(model="ratchet", messages=_ask(6))
    assert (e.value.status_code, e.value.code, e.value.response.headers["retry-after"]) == (503, "teacher_circuit_open", "18")
    assert len(teacher.requests) == 15 and len(calls) == 1
    assert rt.ratchet.health()["breakers"]["teacher"]["state"] == "open"
    assert rt.ledger.totals()["requests"] == 0 and rt.gateway_errors == 0  # refused, not logged, not a crash

    # After the cooldown one probe goes through; the teacher is back, so it is answered and the breaker closes.
    teacher.down.clear()
    clock.advance(17.5)
    r = c.post(CHAT, json={"messages": _ask(7)})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher" and len(teacher.requests) == 16
    assert _circuits(rt.ledger)[1:] == [
        "teacher: open->half_open after 30 s; one probe",
        "teacher: half_open->closed after a successful probe",
    ]

    # The mapping on its own: the wait rounded up and clamped to 1 to 60 s, as for the teacher's other waits; a
    # student's breaker never reaches the client, and an upstream that says `teacher_circuit_open` is just a 502.
    for wait, header in ((12.2, "13"), (None, "1"), (240.0, "60")):
        mapped = gw._map_upstream(CircuitOpen("teacher", retry_after=wait, code="teacher_circuit_open"))
        assert (mapped.status, mapped.code, mapped.headers) == (503, "teacher_circuit_open", {"Retry-After": header})
    assert gw._map_upstream(CircuitOpen("student")).code == "teacher_unavailable"
    assert gw._map_upstream(UpstreamError(503, "busy", code="teacher_circuit_open")).status == 502


@pytest.mark.req("FR-2.12", "NFR-2", "FR-2.10", "INV-15")
def test_open_teacher_breaker_skips_audits_and_arbitration_without_counting_them():
    """Every teacher call goes through the breaker: escalations open it, and while it is open an audit or an
    arbitration is skipped without a call (the student's answer is served, as when they fail) and is not another
    `audit_error` or `shadow_error`: the breaker already counted. A probe after the cooldown closes it."""
    clock = FakeClock()
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    live_student, shadow_student = RecordingProvider(A), RecordingProvider(B)
    led, live, r = _teacher_down(teacher, clock, students={"student-v1": live_student, "student-v2": shadow_student})
    for i in range(5):
        with pytest.raises(httpx.ReadTimeout):
            r.handle(_ask(i), TRIAGE)
    assert len(teacher.calls) == 5 and _circuits(led) == ["teacher: closed->open after 5 failures (ReadTimeout)"]
    with pytest.raises(CircuitOpen) as refused:
        r.handle(_ask(5), TRIAGE)
    assert (refused.value.status, refused.value.code, refused.value.retry_after) == (503, "teacher_circuit_open", 30.0)
    assert len(teacher.calls) == 5 and _requests(led) == 0
    assert r.health()["breakers"]["teacher"] == {"state": "open", "failures": 5, "open_until": clock.now + 30, "open_s": 30.0}

    # A confident student, audited every time, and a shadow that disagrees: both teacher calls are skipped.
    led.update_eval(live, {"threshold": 0.5})
    cand = _version(led, "shadow", {"threshold": 0.5})
    r.s.audit_rate = 1.0
    out = r.handle(_ask("audited"), TRIAGE)
    assert (out.served_by, out.text, out.route) == ("student", A, "student") and len(teacher.calls) == 5
    r.s.audit_rate = 0.0
    out = r.handle(_ask("arbitrated"), TRIAGE)
    assert (out.served_by, out.text) == ("student", A) and len(teacher.calls) == 5 and len(shadow_student.calls) == 2
    rows = led.db.execute("SELECT audited, shadow_version, shadow_serve, shadow_agree FROM requests ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [(0, cand, 1, None), (0, cand, 1, None)]  # unarbitrated: against the shadow
    assert _counters(r) == NO_ERRORS and [e["kind"] for e in led.events()] == ["circuit"]

    # The teacher is back: after the cooldown, the next teacher call is the probe, and audits resume.
    teacher.raise_on.clear()
    clock.advance(30)
    r.s.audit_rate = 1.0
    r.handle(_ask("probe"), TRIAGE)
    assert len(teacher.calls) == 6 and _last_request(led)["audited"] == 1
    assert r.health()["breakers"]["teacher"]["state"] == "closed"


@pytest.mark.req("FR-2.12", "NFR-2")
def test_teacher_breaker_counts_only_teacher_outages():
    """`resilience.endpoint_down` decides, as for the student: a 400 about the request, a tool-call answer (the teacher
    answered; the gateway cannot return it) and a non-HTTP error never open the breaker; a success resets the count.
    0 turns it off; bad settings stop startup. `Breaker.retry_in()` is the time left while open, else None."""
    clock = FakeClock()
    too_long = RecordingProvider(
        B, raise_on={OPEN_TEACHER}, exc=lambda m: UpstreamError(400, TOO_LONG, code="context_length_exceeded")
    )
    led, _, r = _teacher_down(too_long, clock)
    for i in range(7):
        with pytest.raises(UpstreamError) as e:
            r.handle(_ask(i), TRIAGE)
        assert (e.value.status, e.value.code) == (400, "context_length_exceeded")
    assert len(too_long.calls) == 7 and r.teacher_breaker.state == "closed"

    flaky = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, _, r = _teacher_down(flaky, clock)
    for i in range(4):
        with pytest.raises(httpx.ReadTimeout):
            r.handle(_ask(i), TRIAGE)
    flaky.raise_on.clear()
    flaky.finish_reason = "tool_calls"  # a tool call: refused for the client, a healthy answer for the breaker
    with pytest.raises(UpstreamError, match="tool call"):
        r.handle(_ask("tool"), TRIAGE)
    flaky.text, flaky.finish_reason = _unreadable, "stop"  # not an HTTP failure: says nothing either way
    with pytest.raises(ValueError):
        r.handle(_ask("bug"), TRIAGE)
    flaky.text = B
    flaky.raise_on.add(OPEN_TEACHER)
    for i in range(4):
        with pytest.raises(httpx.ReadTimeout):
            r.handle(_ask(f"again {i}"), TRIAGE)
    assert len(flaky.calls) == 10 and r.teacher_breaker.snapshot()["failures"] == 4 and r.teacher_breaker.state == "closed"

    off = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, _, r = _teacher_down(off, clock, teacher_breaker_failures=0)
    for i in range(10):
        with pytest.raises(httpx.ReadTimeout):
            r.handle(_ask(i), TRIAGE)
    assert len(off.calls) == 10 and r.teacher_breaker.state == "closed" and _circuits(led) == []

    for bad, var in (
        ({"teacher_breaker_failures": -1}, "RATCHET_TEACHER_BREAKER_FAILURES"),
        ({"teacher_breaker_failures": True}, "RATCHET_TEACHER_BREAKER_FAILURES"),
        ({"teacher_breaker_cooldown_s": 0.0}, "RATCHET_TEACHER_BREAKER_COOLDOWN_S"),
        ({"teacher_breaker_cooldown_s": math.inf}, "RATCHET_TEACHER_BREAKER_COOLDOWN_S"),
    ):
        with pytest.raises(ValueError, match=var):
            _teacher_down(RecordingProvider(B), clock, **bad)

    b = Breaker("teacher", 1, 30.0, clock=clock)
    assert b.retry_in() is None
    b.record(False)
    assert b.retry_in() == 30.0
    clock.advance(31)
    assert b.retry_in() == 0.0 and b.allow() and b.state == "half_open" and b.retry_in() is None


@pytest.mark.req("FR-2.12", "NFR-2", "INV-7", "INV-2", "FR-6.3")
def test_serve_student_mode_answers_degraded_with_a_flag_and_a_header(gw_env, tmp_path, monkeypatch):
    """Spec: a confident-but-below-threshold student and a failing teacher give a 200 with `ratchet.degraded` and
    `X-Ratchet-Degraded: teacher_unavailable`, and the row has audited 0. Live Runtime with
    RATCHET_ON_TEACHER_FAILURE=serve_student: the student's own answer is served (route `degraded`), nothing is stored,
    no shadow runs (its only reference would be that answer), and no release statistic moves. Once the teacher's breaker
    opens, degraded answers come without a teacher call."""
    clock = FakeClock()
    teacher = TokenFactory(down={OPEN_TEACHER})
    gw, rt, student = _live_teacher_down(gw_env, tmp_path, monkeypatch, teacher, clock, on_teacher_failure="serve_student")
    live = _version(rt.ledger, "live", {"threshold": 0.99})
    cand = _version(rt.ledger, "shadow", {"threshold": 0.5})
    c = TestClient(gw.app)

    r = c.post(CHAT, json={"messages": _ask(1)})
    assert r.status_code == 200, r.text
    body = r.json()
    meta = body["ratchet"]
    assert (meta["served_by"], meta["degraded"], meta["escalated"], meta["student_version"]) == (
        "student",
        "teacher_unavailable",
        True,
        live,
    )
    assert 0.9 < meta["confidence"] < 0.99 and body["choices"][0]["message"]["content"] == A and body["model"] == "student-v1"
    assert (r.headers["x-ratchet-degraded"], r.headers["x-ratchet-served-by"]) == ("teacher_unavailable", "student")
    row = _last_request(rt.ledger)
    assert (row["route"], row["served_by"], row["audited"], row["audit_agree"], row["escalated"], row["stored"]) == (
        "degraded",
        "student",
        0,
        None,
        1,
        0,
    )
    assert row["shadow_version"] is None and row["cost_usd"] == pytest.approx(Settings().price("student").cost(10, 15))
    assert {call.json["model"] for call in student.calls_to("/chat/completions")} == {"student-v1"}  # no shadow call
    assert len(teacher.requests) == 3 and rt.ledger.examples() == []
    assert [(e["kind"], e["detail"]) for e in rt.ledger.events()] == [("degraded", "default: UpstreamError 503")]

    r = c.post(CHAT, json={"messages": _ask(2), "stream": True})
    assert r.status_code == 200 and r.headers["x-ratchet-degraded"] == "teacher_unavailable"
    assert _stream_meta(r.text)["degraded"] == "teacher_unavailable"

    for i in range(3, 6):  # the 5th failed teacher call opens its breaker; the 6th request is degraded without one
        assert c.post(CHAT, json={"messages": _ask(i)}).json()["ratchet"]["degraded"] == "teacher_unavailable"
    assert len(teacher.requests) == 15 and rt.ratchet.health()["breakers"]["teacher"]["state"] == "open"
    r = c.post(CHAT, json={"messages": _ask(6)})
    assert r.json()["ratchet"]["degraded"] == "teacher_unavailable" and len(teacher.requests) == 15
    assert rt.ratchet.health()["degraded_answers"] == 6

    # None of it counts toward a release: no audits for the live version, no evidence for the shadow.
    assert rt.ledger.version_live_quality(live) == (0, None) and rt.ledger.shadow_quality(cand)["n"] == 0
    stats = c.get("/ratchet/stats").json()
    assert stats["days"][0]["degraded"] == 6 and stats["days"][0]["fallback_teacher"] == 0
    assert stats["tasks"][0]["routes"] == {"degraded": 6}

    # A confident answer is not degraded: no flag (null), no header.
    rt.ledger.update_eval(live, {"threshold": 0.5})
    r = c.post(CHAT, json={"messages": _ask(7)})
    assert r.json()["ratchet"]["degraded"] is None and "x-ratchet-degraded" not in r.headers
    assert r.json()["ratchet"]["served_by"] == "student" and _last_request(rt.ledger)["route"] == "student"


@pytest.mark.req("FR-2.12", "INV-7", "FR-1.8", "FR-2.9", "INV-10")
def test_error_mode_is_the_default_and_pinned_requests_never_degrade(gw_env, tmp_path, monkeypatch):
    """Spec: error mode (the default) still returns 502; a pinned task in serve_student mode is still a 502, then a 503
    once the breaker is open. The student is never asked for a pinned task."""
    clock = FakeClock()
    teacher = TokenFactory(down={OPEN_TEACHER})
    gw, rt, student = _live_teacher_down(gw_env, tmp_path, monkeypatch, teacher, clock)
    assert rt.settings.on_teacher_failure == "error" and not rt.ratchet.serve_student_on_failure
    _version(rt.ledger, "live", {"threshold": 0.99})
    r = TestClient(gw.app).post(CHAT, json={"messages": _ask(1)})
    assert (r.status_code, r.json()["error"]["code"]) == (502, "teacher_unavailable") and "x-ratchet-degraded" not in r.headers
    assert rt.ledger.totals()["requests"] == 0

    gw, rt, student = _live_teacher_down(
        gw_env, tmp_path, monkeypatch, teacher, clock, on_teacher_failure="serve_student", db_path=str(tmp_path / "serve.db")
    )
    _version(rt.ledger, "live", {"threshold": 0.99})
    c = TestClient(gw.app)
    plan = {"messages": [{"role": "user", "content": "Question: what is LoRA?"}]}
    statuses = [c.post(CHAT, json=plan, headers={"X-Ratchet-Task": "research-plan"}).status_code for _ in range(6)]
    assert statuses == [502] * 5 + [503]
    assert student.calls == [] and rt.ledger.totals()["requests"] == 0 and rt.ratchet.health()["degraded_answers"] == 0


@pytest.mark.req("FR-2.12", "INV-7", "FR-1.8")
def test_degraded_mode_never_hides_a_client_error_or_serves_an_invalid_answer():
    """serve_student stands in only for an unavailable teacher and only with a valid student answer: a 400 about the
    request, a tool-call answer and a non-HTTP error still raise, and so does a request with `tools`, a student whose
    samples are all invalid, a student that failed or was skipped, a cold start, and the teacher-only path. A bad
    RATCHET_ON_TEACHER_FAILURE stops startup."""
    clock = FakeClock()

    def cascade(
        exc: Any = httpx.ConnectError, student: RecordingProvider | None = None, **settings: Any
    ) -> tuple[Ledger, Ratchet]:
        teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=exc)
        students = {"student-v1": student or RecordingProvider(A)}
        led, _, r = _teacher_down(teacher, clock, students=students, on_teacher_failure="serve_student", **settings)
        return led, r

    led, r = cascade()
    assert r.handle(_ask("down"), TRIAGE).route == "degraded"  # the baseline: an unavailable teacher degrades
    assert r.handle(_ask("down"), TRIAGE, params=GenParams(max_tokens=50)).route == "degraded"
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask("tools"), TRIAGE, params=GenParams(tools=TOOLS))  # a tool request is teacher-only (FR-1.41)
    with pytest.raises(httpx.ConnectError):
        r.teacher_only(_ask("image"), TRIAGE)

    led, r = cascade(exc=lambda m: UpstreamError(400, TOO_LONG, code="context_length_exceeded"))
    with pytest.raises(UpstreamError, match="maximum context length"):
        r.handle(_ask("long"), TRIAGE)
    led, r = cascade(exc=lambda m: ValueError("a bug in a provider"))
    with pytest.raises(ValueError):
        r.handle(_ask("bug"), TRIAGE)
    led, r = cascade(student=RecordingProvider("not json"))  # no valid sample: never served, whatever the mode
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask("invalid"), TRIAGE)
    led, r = cascade(student=RecordingProvider(A, raise_on={"student-v1"}), student_breaker_failures=1)
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask("student down"), TRIAGE)  # route student_error: no student answer
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask("student skipped"), TRIAGE)  # route student_breaker
    tool_call = RecordingProvider(B, finish_reason="tool_calls")
    led, _, r = _teacher_down(tool_call, clock, on_teacher_failure="serve_student")
    with pytest.raises(UpstreamError, match="tool call"):
        r.handle(_ask("tool call"), TRIAGE)

    cold = Ledger(":memory:")
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, on_teacher_failure="serve_student")
    r = Ratchet(s, cold, RecordingProvider(B, raise_on={OPEN_TEACHER}), lambda v: (RecordingProvider(A), v.adapter_uri))
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask("cold"), TRIAGE)

    assert _teacher_down(RecordingProvider(B), clock, on_teacher_failure=" Serve_Student ")[2].serve_student_on_failure
    assert not _teacher_down(RecordingProvider(B), clock, on_teacher_failure="")[2].serve_student_on_failure  # unset
    for bad in ("serve-student", "student", "fallback", "1"):
        with pytest.raises(ValueError, match="RATCHET_ON_TEACHER_FAILURE"):
            _teacher_down(RecordingProvider(B), clock, on_teacher_failure=bad)


@pytest.mark.req("FR-2.12")
def test_degraded_answers_are_counted_exactly_under_threads():
    """Scout calls handle from several threads: every degraded answer is logged, counted once, and its event throttled."""
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, _, r = _teacher_down(teacher, FakeClock(), on_teacher_failure="serve_student", teacher_breaker_failures=0)
    with ThreadPoolExecutor(8) as pool:
        routes = list(pool.map(lambda i: r.handle(_ask(i), TRIAGE).route, range(200)))
    assert routes == ["degraded"] * 200 and len(teacher.calls) == 200
    assert r.health()["degraded_answers"] == 200
    assert _events(led) == [("degraded", f"{TRIAGE}: ReadTimeout")] * 3  # the 1st, 10th and 100th
    assert led.db.execute("SELECT COUNT(*), SUM(audited), SUM(stored) FROM requests WHERE route='degraded'").fetchone()[:] == (
        200,
        0,
        0,
    )


@pytest.mark.req("FR-2.12", "FR-1.14")
def test_n_choices_when_the_teacher_is_down():
    """With n > 1 choices, a degraded answer needs n student samples that agree (never fewer choices than asked from the
    student); otherwise the teacher's error stands. The fallback teacher writes n choices like the teacher would."""
    clock = FakeClock()
    a2 = json.dumps({"category": "billing", "priority": "high", "reply": "on it"})
    down = RecordingProvider(B, raise_on={OPEN_TEACHER})
    students = {"student-v1": RecordingProvider([A, a2, A])}
    led, _, r = _teacher_down(down, clock, students=students, on_teacher_failure="serve_student", teacher_breaker_failures=0)
    out = r.handle(_ask("two"), TRIAGE, n=2)
    assert (out.route, out.degraded, [ch.text for ch in out.choices]) == ("degraded", "teacher_unavailable", [A, a2])
    assert (out.completion_tokens, out.usage_breakdown["served"]) == (10, {"in": 10, "out": 10})
    students["student-v1"].text = [A, A, B, B]  # 2 of 3 agree: not enough for 3 choices
    with pytest.raises(httpx.ConnectError):
        r.handle(_ask("three"), TRIAGE, n=3)
    assert r.handle(_ask("one"), TRIAGE).route == "degraded"  # one choice: the majority answer stands in as before
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE route='degraded'").fetchone()[0] == 2

    fallback = RecordingProvider([B, A])
    led, _, r = _teacher_down(
        RecordingProvider(B, raise_on={OPEN_TEACHER}), clock, fallback=fallback, teacher_fallback_model=FALLBACK_TEACHER
    )
    out = r.handle(_ask("fallback"), TRIAGE, n=2)
    assert (out.route, [ch.text for ch in out.choices]) == ("fallback_teacher", [B, A])
    assert (fallback.calls[0].kwargs["n"], fallback.calls[0].kwargs["temperature"]) == (2, 0.7)
    assert led.examples() == []  # a fallback teacher's answer is not stored by default


@pytest.mark.req("FR-2.12", "NFR-2", "INV-2", "INV-10", "FR-2.9")
def test_fallback_teacher_answers_when_the_primary_is_unavailable():
    """Spec: a fallback answer is served (route `fallback_teacher`, the fallback's model id, its own price) and not
    stored by default. It is asked only after the primary failed its retries or while its breaker is open, never for a
    400 about the request, never for an audit; when it fails too the primary's error is raised. A pinned task may get
    it (and stores nothing), and a request it answered runs no shadow."""
    clock = FakeClock()
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER})
    fallback = RecordingProvider(B, in_tokens=40, out_tokens=20)
    live_student, shadow_student = RecordingProvider(A), RecordingProvider(B)
    students = {"student-v1": live_student, "student-v2": shadow_student}
    led, live, r = _teacher_down(teacher, clock, students=students, fallback=fallback, teacher_fallback_model=FALLBACK_TEACHER)
    cand = _version(led, "shadow", {"threshold": 0.5})
    s = r.s

    out = r.handle(_ask(1), TRIAGE)
    assert (out.route, out.served_by, out.model, out.text, out.escalated, out.version) == (
        "fallback_teacher",
        "teacher",
        FALLBACK_TEACHER,
        B,
        True,
        None,
    )
    samples = s.price("student").cost(10, 15)
    assert out.cost_usd == pytest.approx(samples + s.price(FALLBACK_TEACHER).cost(40, 20))
    assert out.baseline_usd == pytest.approx(s.price(OPEN_TEACHER).cost(40, 20))  # the primary's price, same tokens
    assert out.fingerprint == fingerprint("teacher", FALLBACK_TEACHER) and out.usage_breakdown["served"] == {"in": 40, "out": 20}
    assert [call.model for call in fallback.calls] == [FALLBACK_TEACHER] and fallback.calls[0].messages == _ask(1)
    row = _last_request(led)
    assert (row["route"], row["served_by"], row["stored"], row["shadow_version"]) == ("fallback_teacher", "teacher", 0, None)
    assert led.examples() == [] and len(shadow_student.calls) == 0  # not stored, and no shadow
    assert _events(led) == [("fallback_teacher", f"{TRIAGE}: ConnectError")]

    # A 400 about the request is the client's to fix: the fallback is not asked. (The primary answered, so its count of
    # failures starts again.)
    teacher.exc = lambda m: UpstreamError(400, TOO_LONG, code="context_length_exceeded")
    with pytest.raises(UpstreamError, match="maximum context length"):
        r.handle(_ask("long"), TRIAGE)
    assert len(fallback.calls) == 1
    teacher.exc = httpx.ConnectError

    # Five failures in a row open the primary's breaker; then the fallback answers without a primary call.
    for i in range(5):
        assert r.handle(_ask(f"down {i}"), TRIAGE).route == "fallback_teacher"
    assert len(teacher.calls) == 7 and r.teacher_breaker.state == "open"
    assert r.handle(_ask("open"), TRIAGE).route == "fallback_teacher" and len(teacher.calls) == 7

    # Pinned: the fallback answers, nothing is stored; the baseline is the primary's price.
    out = r.handle([{"role": "user", "content": "Question: what is LoRA?"}], "research-plan")
    assert (out.route, out.model, out.served_by) == ("fallback_teacher", FALLBACK_TEACHER, "teacher")
    assert out.cost_usd == pytest.approx(s.price(FALLBACK_TEACHER).cost(40, 20)) and out.baseline_usd > out.cost_usd

    # An audit is the primary's or nothing: skipped while its breaker is open, never sent to the fallback. (This request
    # was served by the live student, so the shadow runs on it.)
    led.update_eval(live, {"threshold": 0.5})
    s.audit_rate = 1.0
    calls = len(fallback.calls)
    assert r.handle(_ask("audited"), TRIAGE).route == "student" and len(fallback.calls) == calls
    assert _last_request(led)["audited"] == 0 and len(shadow_student.calls) == 1
    s.audit_rate = 0.0
    led.update_eval(live, {"threshold": 0.99})

    # Both down: the primary's error, with its breaker's wait.
    fallback.raise_on.add(FALLBACK_TEACHER)
    with pytest.raises(CircuitOpen) as both:
        r.handle(_ask("both down"), TRIAGE)
    assert (both.value.code, both.value.retry_after) == ("teacher_circuit_open", 30.0)
    assert led.examples() == [] and r.health()["fallback_answers"] == 8
    rows = led.db.execute("SELECT COUNT(*), COUNT(shadow_version) FROM requests WHERE route='fallback_teacher'").fetchone()
    assert tuple(rows) == (8, 0) and len(shadow_student.calls) == 1  # no fallback-served request ran the shadow
    assert led.shadow_quality(cand)["n"] == 1  # only the student-served one


@pytest.mark.req("FR-2.12", "INV-1", "INV-2", "FR-3.3", "FR-3.5")
def test_stored_fallback_answers_carry_the_fallback_license():
    """Spec: with RATCHET_STORE_FALLBACK_TEACHER=1 a fallback answer is stored with the fallback model's own license
    (looked up by the configured model that answered, never the primary's), and the BOM lists both teachers."""
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER})
    fallback = RecordingProvider(B)
    led, _, r = _teacher_down(
        teacher, FakeClock(), fallback=fallback, teacher_fallback_model=FALLBACK_TEACHER, store_fallback_teacher=True
    )
    assert r.handle(_ask("fallback"), TRIAGE).route == "fallback_teacher" and _last_request(led)["stored"] == 1
    teacher.raise_on.clear()
    assert r.handle(_ask("primary"), TRIAGE).route == "confidence"
    by_model = {(ex["teacher_model"], ex["license"], ex["license_url"]) for ex in led.examples()}
    assert len(led.examples()) == 2 and by_model == {
        (FALLBACK_TEACHER, licenses.NEMOTRON_OPEN.name, licenses.NEMOTRON_OPEN.url),
        (OPEN_TEACHER, licenses.OPEN_MDW.name, licenses.OPEN_MDW.url),
    }
    sources = {(src["teacher_model"], src["license"]) for src in led.bill_of_materials()["sources"]}
    assert sources == {(FALLBACK_TEACHER, licenses.NEMOTRON_OPEN.name), (OPEN_TEACHER, licenses.OPEN_MDW.name)}

    # Not stored by default, and never when the request may not be stored at all.
    led, _, r = _teacher_down(RecordingProvider(B, raise_on={OPEN_TEACHER}), FakeClock(), teacher_fallback_model=FALLBACK_TEACHER)
    assert r.fallback_teacher is r.teacher  # without its own provider, the fallback model is asked on the same endpoint
    assert r.handle(_ask("default"), TRIAGE).route == "fallback_teacher" and led.examples() == []
    led, _, r = _teacher_down(
        RecordingProvider(B, raise_on={OPEN_TEACHER}),
        FakeClock(),
        teacher_fallback_model=FALLBACK_TEACHER,
        store_fallback_teacher=True,
    )
    assert r.handle(_ask("private"), TRIAGE, meta=RequestMeta(storable=False)).route == "fallback_teacher"
    assert led.examples() == [] and _events(led) == [("fallback_teacher", f"{TRIAGE}: ConnectError")]  # no skip event


@pytest.mark.req("FR-2.12", "FR-8.1", "NFR-2", "INV-1")
def test_live_runtime_wires_the_fallback_teacher(gw_env, tmp_path, monkeypatch):
    """RATCHET_TEACHER_FALLBACK_BASE puts the fallback on its own client (same key, the teacher's timeouts and retries);
    empty, it shares the teacher's endpoint and client. The response names the fallback's model. The variables reach
    Settings."""
    clock = FakeClock()
    teacher = TokenFactory(down={"teacher.test"})
    gw, rt, _ = _live_teacher_down(
        gw_env,
        tmp_path,
        monkeypatch,
        teacher,
        clock,
        teacher_fallback_model=FALLBACK_TEACHER,
        teacher_fallback_base="https://fallback.test/v1/",
    )
    fb = rt.ratchet.fallback_teacher
    assert fb is not rt.ratchet.teacher and (fb.base_url, fb.retry, fb.timeout.read) == (
        "https://fallback.test/v1",
        rt.ratchet.teacher.retry,
        120.0,
    )
    r = TestClient(gw.app).post(CHAT, json={"messages": _ask(1)})  # cold start
    assert r.status_code == 200 and r.json()["model"] == FALLBACK_TEACHER and r.json()["ratchet"]["served_by"] == "teacher"
    assert r.headers["x-ratchet-served-by"] == "teacher" and "x-ratchet-degraded" not in r.headers
    assert teacher.requests == [("teacher.test", OPEN_TEACHER, f"Bearer {TEACHER_KEY}")] * 3 + [
        ("fallback.test", FALLBACK_TEACHER, f"Bearer {TEACHER_KEY}")
    ]
    assert sorted(rt.ratchet.health()["breakers"]) == ["fallback_teacher", "teacher"]  # cold start: no student call yet

    teacher = TokenFactory(down={OPEN_TEACHER})
    gw, rt, _ = _live_teacher_down(
        gw_env, tmp_path, monkeypatch, teacher, clock, teacher_fallback_model=FALLBACK_TEACHER, db_path=str(tmp_path / "b.db")
    )
    assert rt.ratchet.fallback_teacher is rt.ratchet.teacher
    r = TestClient(gw.app).post(CHAT, json={"messages": _ask(2)})
    assert r.status_code == 200 and r.json()["model"] == FALLBACK_TEACHER
    assert teacher.requests[-1] == ("teacher.test", FALLBACK_TEACHER, f"Bearer {TEACHER_KEY}")

    env = {
        "RATCHET_ON_TEACHER_FAILURE": "serve_student",
        "RATCHET_TEACHER_FALLBACK_MODEL": FALLBACK_TEACHER,
        "RATCHET_TEACHER_FALLBACK_BASE": "https://fallback.test/v1",
        "RATCHET_STORE_FALLBACK_TEACHER": "1",
        "RATCHET_TEACHER_BREAKER_FAILURES": "3",
        "RATCHET_TEACHER_BREAKER_COOLDOWN_S": "12.5",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    s = Settings()
    got = (
        s.on_teacher_failure,
        s.teacher_fallback_model,
        s.teacher_fallback_base,
        s.store_fallback_teacher,
        s.teacher_breaker(),
    )
    assert got == ("serve_student", FALLBACK_TEACHER, "https://fallback.test/v1", True, BreakerPolicy(3, 12.5, 300.0))
    monkeypatch.setenv("RATCHET_TEACHER_FALLBACK_MODEL", "gpt-4o")  # a closed model stops the gateway (INV-1)
    with pytest.raises(licenses.LicenseError, match="gpt-4o"):
        gw.Runtime(_live_settings(tmp_path, db_path=str(tmp_path / "c.db")))


# ---------- Night School: a golden row the student endpoint refuses (FR-5.1, INV-7) ----------
TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
NO_TOOL_PARSER = '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'


@pytest.mark.req("FR-5.1", "INV-7", "FR-4.4")
def test_a_golden_row_the_student_refuses_never_stops_night_school():
    """vLLM started without a tool parser (deploy/student_endpoint.sh) answers 400 to a request with `tools`. A golden
    row stored with tools, sampled under an allowlist that forwards them, would escalate in production (INV-7), so the
    live eval scores it as not served and the night produces a report; the judge does not pass it. Before, the 400
    stopped every night, because the golden split never changes. The default allowlist does not send tools at all, and
    an error that refuses every row (401) still stops the night."""
    inner = FakeOpenAIServer(A)

    def vllm(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("tools") and body.get("tool_choice", "auto") == "auto":
            refusal = {"object": "error", "message": NO_TOOL_PARSER, "type": "BadRequestError", "code": 400}
            return httpx.Response(400, json=refusal)
        return inner(request)

    led = Ledger(":memory:", golden_fraction=0.3)
    for i in range(60):
        led.add_example(TRIAGE, _ask(i), A, OPEN_TEACHER, "l", "u", params={"tools": TOOLS} if i % 2 == 0 else None)
    golden = dataset.rows(led, "golden")
    with_tools = sum(1 for r in golden if r["params"])
    assert with_tools >= 2 and len(golden) > with_tools

    student = OpenAICompatProvider("https://student.test/v1", "k", transport=httpx.MockTransport(vllm))
    forwards_tools = GenPolicy(student_allow=frozenset(PARAM_FIELDS))
    rep = run_night(led, RecordingTrainer(), LiveEvaluator(student, None, policy=forwards_tools), ReleaseGate(led), "base", 1)
    assert rep.trained and rep.eval is not None
    assert (rep.eval["refused_n"], rep.eval["golden_n"]) == (with_tools, len(golden))

    inner.calls.clear()
    res = LiveEvaluator(student, None, policy=GenPolicy()).evaluate("student-v1", golden)
    assert res["refused_n"] == 0 and not any("tools" in c.json for c in inner.calls_to("/chat/completions"))

    # A free-text row refused the same way escalates in production, so nobody gets a student answer to judge: it is
    # neither judged nor passed, and the night goes on (the bare row calibrates, the refused one is held out).
    free = Ledger(":memory:", golden_fraction=1.0)
    free.add_example("default", _ask("bare"), "an answer", OPEN_TEACHER, "l", "u")
    free.add_example("default", _ask("tools"), "an answer", OPEN_TEACHER, "l", "u", params={"tools": TOOLS})
    judge = RecordingProvider('{"score": 1, "reason": "same"}')
    free_rows = dataset.rows(free, "golden")
    assert [bool(r["params"]) for r in free_rows] == [False, True]
    res = LiveEvaluator(student, judge, "judge", policy=forwards_tools).evaluate("student-v1", free_rows)
    assert (res["refused_n"], res["judge_n"], res["judge_pass_rate"], len(judge.calls)) == (1, 0, None, 0)

    denied = OpenAICompatProvider("https://student.test/v1", "k", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(UpstreamError) as stopped:
        LiveEvaluator(denied, None).evaluate("student-v1", golden)
    assert stopped.value.status == 401
