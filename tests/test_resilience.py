"""Upstream failures on the request path (NFR-2, FR-1.8, FR-1.11, FR-2.10, INV-7, INV-15).

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
from fakes import OPEN_TEACHER, FakeClock, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.live import AdapterReloader, adapter_reloader
from ratchet.providers import OpenAICompatProvider, UpstreamError
from ratchet.resilience import (
    NO_RETRY,
    AdapterMissing,
    Breaker,
    CircuitOpen,
    RetryPolicy,
    as_upstream,
    endpoint_down,
    parse_retry_after,
    timeouts,
)
from ratchet.router import ROUTES, Ratchet
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
NO_ERRORS = {"student_errors": 0, "audit_errors": 0, "shadow_errors": 0}
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
    return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


def _events(led: Ledger) -> list[tuple[str, str]]:
    return [(e["kind"], e["detail"]) for e in led.events()]


def _counters(r: Ratchet) -> dict[str, int]:
    """`Ratchet.health()` without the breakers: the failure counters only."""
    return {k: v for k, v in r.health().items() if k != "breakers"}


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
    assert _counters(r) == {**NO_ERRORS, "audit_errors": 1}


@pytest.mark.req("FR-2.10", "INV-15", "INV-7", "FR-1.8")
def test_audit_errors_are_counted_and_throttled_and_escalation_still_fails():
    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=_timeout)
    led, live, s, r = _cascade(teacher, audit_rate=1.0)
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
    led, live, s, r = _cascade(teacher, audit_rate=1.0)
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

    # Audited, and the shadow disagrees: the audit fails, and arbitration is not tried against the same outage.
    r = c.post(CHAT, json={"messages": _ask(1)})
    assert r.status_code == 200, r.text
    assert (r.json()["ratchet"]["served_by"], r.json()["choices"][0]["message"]["content"]) == ("student", A)
    assert len(teacher_calls) == 2 and len(waits) == 1
    row = _last_request(rt.ledger)
    assert (row["audited"], row["shadow_version"], row["shadow_serve"], row["shadow_agree"]) == (0, cand, 1, None)

    r = c.post(CHAT, json={"messages": _ask(2), "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.text.rstrip().endswith("data: [DONE]") and len(teacher_calls) == 4

    # Not audited: the shadow's disagreement goes to the teacher, which fails, and the request is still a 200.
    rt.settings.audit_rate = 0.0
    r = c.post(CHAT, json={"messages": _ask(3)})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "student" and len(teacher_calls) == 6
    assert _counters(rt.ratchet) == {"student_errors": 0, "audit_errors": 2, "shadow_errors": 1}
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

    # A connection that never opened reached no model: retried up to max_attempts, then 502 upstream_unreachable.
    for exc in (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError):
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
    assert _transitions(changes) == ["closed->open"] + ["open->half_open", "half_open->open"] * 6 + [
        "open->half_open",
        "half_open->closed",
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
    assert r.health()["breakers"] == {"student": {"state": "open", "failures": 5, "open_until": clock.now + 30, "open_s": 30.0}}
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
    assert sorted(r.health()["breakers"]) == ["shadow", "student", "student-2", "student-3"]


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


@pytest.mark.req("FR-2.11", "FR-2.9")
def test_every_request_row_has_a_route():
    clock = FakeClock()
    led = Ledger(":memory:")
    student = RecordingProvider(A)
    teacher = RecordingProvider(B)
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, student_breaker_failures=2, student_breaker_cooldown_s=30)
    r = Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri), seed=1, clock=clock)

    seen = [r.handle(_ask("cold"), TRIAGE).route]  # no version yet
    seen.append(r.handle([{"role": "user", "content": "Question: what is LoRA?"}], "research-plan").route)
    seen.append(r.teacher_only(_ask("image"), TRIAGE).route)  # the pinned path
    live = _version(led, "live", {"threshold": 0.5})
    seen.append(r.handle(_ask("sure"), TRIAGE).route)
    led.update_eval(live, {"threshold": 0.99})
    seen.append(r.handle(_ask("unsure"), TRIAGE).route)
    student.raise_on.add("student-v1")
    seen += [r.handle(_ask(f"down {i}"), TRIAGE).route for i in range(3)]
    assert seen == [
        "cold_start",
        "pinned",
        "pinned",
        "student",
        "confidence",
        "student_error",
        "student_error",
        "student_breaker",
    ]
    assert set(seen) == set(ROUTES)
    assert led.db.execute("SELECT COUNT(*) FROM requests WHERE route IS NULL").fetchone()[0] == 0
    by_task = {t["task"]: t["routes"] for t in led.stats_by_task()}
    assert by_task == {
        TRIAGE: {"student_error": 2, "pinned": 1, "cold_start": 1, "student": 1, "confidence": 1, "student_breaker": 1},
        "research-plan": {"pinned": 1},
    }
    assert list(by_task[TRIAGE])[0] == "student_error"  # most frequent first
