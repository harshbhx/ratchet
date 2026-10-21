"""Spend and rate limits (FR-12.2, FR-12.3, ADR 0063).

Per-key US dollar budgets (daily and monthly) and request quotas counted by an in-memory spend meter that reads the
ledger once at startup, so the quota path runs no query and a restart forgets nothing; the gateway's own daily budget
with its two modes (`block`, `student_only`) and budget alerts written once per period; per-key requests a minute (a
token bucket on a fake clock), requests in flight, task scopes and the store switch; the per-IP rate of an open gateway
and the global in-flight cap. Scout's spend stays under its own guard (invariant 11) and limits are keyed by name only
(invariant 13).
"""

from __future__ import annotations

import calendar
import json
import math
import re
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeClock, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet import metrics
from ratchet.auth import KeyLimits, parse_key_limits
from ratchet.config import Settings, parse_alert_pct
from ratchet.ledger import Ledger
from ratchet.ratelimit import Concurrency, Limiter, RateLimiter, Refused, TokenBucket, burst
from ratchet.router import Ratchet, RequestMeta
from ratchet.spend import BudgetExceeded, Caps, SpendMeter, money, month_bounds

CHAT = "/v1/chat/completions"
ACME_KEY = "acme-api-key-0123456789abcdef"
GLOBEX_KEY = "globex-api-key-0123456789abcd"
KEYS = f"acme:{ACME_KEY},globex:{GLOBEX_KEY}"
ACME, GLOBEX = {"Authorization": f"Bearer {ACME_KEY}"}, {"Authorization": f"Bearer {GLOBEX_KEY}"}
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
TEACHER_COST = Settings(mode="mock").price(OPEN_TEACHER).cost(10, 5)  # FakeOpenAIServer's usage at the teacher's price
NOON = float(calendar.timegm((2026, 10, 21, 12, 0, 0)))
LIMIT_ENV = (
    "RATCHET_API_KEY_DAILY_REQUESTS",
    "RATCHET_API_KEY_DAILY_USD",
    "RATCHET_API_KEY_MONTHLY_USD",
    "RATCHET_API_KEY_RPM",
    "RATCHET_API_KEY_CONCURRENCY",
    "RATCHET_DAILY_BUDGET_USD",
    "RATCHET_IP_RPM",
    "RATCHET_MAX_INFLIGHT",
)


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    """A live gateway's settings with every value these tests depend on set: two API keys, a public dashboard, the
    teacher on a fake endpoint, no student endpoint (cold start: the teacher answers), and no limits unless asked."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://tokenfactory.test/v1",
        "teacher_api_key": "tf-teacher-key-0123456789",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "teacher_retries": 0,
        "api_keys": KEYS,
        "admin_keys": "",
        "dashboard_keys": "",
        "metrics_token": "",
        "api_key_daily_requests": 0,
        "key_limits": "",
        "api_key_daily_usd": 0.0,
        "api_key_monthly_usd": 0.0,
        "api_key_rpm": 0,
        "api_key_concurrency": 0,
        "daily_budget_usd": 0.0,
        "budget_mode": "block",
        "budget_alert_pct": "50,80,100",
        "ip_rpm": 0,
        "max_inflight": 32,
        "tasks_file": "",
        "unknown_tasks": "distill",
        "bom_signing_key": "",
        "tavily_api_key": "",
        "mock_warmup_days": 0,
        "public_dashboard": True,
        "allow_unauthenticated": None,
        "log_format": "",
        "log_level": "",
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "teacher_reasoning_tokens": 0,
        "stats_ttl_s": 0.0,
    }
    return Settings(**{**base, **overrides})


def _gateway(gw_env: Any, monkeypatch: pytest.MonkeyPatch, settings: Settings, clock: Any = None, **transports: Any):
    gw = gw_env()
    rt = gw.Runtime(
        settings,
        transports=transports or {"teacher": FakeOpenAIServer(A).transport},
        sleep=lambda s: None,
        **({"clock": clock} if clock is not None else {}),
    )
    monkeypatch.setattr(gw, "rt", rt)
    return gw, rt


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice this month, order {tag}"}]


def _post(c: TestClient, headers: dict[str, str] | None = None, tag: object = 0, **body: Any) -> httpx.Response:
    return c.post(CHAT, json={"messages": _ask(tag), **body}, headers={"X-Ratchet-Task": TRIAGE, **(headers or {})})


def _error(r: httpx.Response) -> tuple[str, str]:
    err = r.json()["error"]
    return err["type"], err["code"]


def _rows(led: Ledger, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute(sql, args)]


def _budget_events(led: Ledger) -> list[str]:
    return [e["detail"] for e in led.events() if e["kind"] == "budget"]


def _logged(led: Ledger, client: str | None, usd: float, ts: float, request_id: str | None = "req_x") -> None:
    """A gateway API request's row, as the router writes it (only what the spend meter reads)."""
    led.log_request(task=TRIAGE, served_by="teacher", client=client, cost_usd=usd, ts=ts, request_id=request_id)


# ---------- per-key US dollar budgets ----------
@pytest.mark.req("FR-12.2", "FR-1.7", "NFR-3")
def test_key_daily_usd_cap_refuses_once_spent_and_other_keys_go_on(gw_env, tmp_path, monkeypatch):
    """acme's cap is 2.5 requests' worth: three requests pass (the third overshoots by its own cost, since a check reads
    what was spent before the request), the fourth is a 429 `budget_exceeded` that tells SDKs not to retry and says when
    the cap resets; globex, without a cap, goes on and gets no spend headers."""
    cap = 2.5 * TEACHER_COST
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, key_limits=f"acme:daily_usd={cap:.10f}"))
    c = TestClient(gw.app)
    for i in range(1, 4):
        r = _post(c, ACME, tag=i)
        assert r.status_code == 200 and r.json()["ratchet"]["cost_usd"] == pytest.approx(TEACHER_COST)
        assert r.headers["x-ratchet-spend-today-usd"] == f"{i * TEACHER_COST:.6f}"
        assert r.headers["x-ratchet-budget-remaining-usd"] == f"{max(0.0, cap - i * TEACHER_COST):.6f}"
    refusals = metrics.REJECTIONS.value("budget")
    r = _post(c, ACME, tag=4)
    assert r.status_code == 429 and _error(r) == ("insufficient_quota", "budget_exceeded")
    assert "daily budget" in r.json()["error"]["message"] and "'acme'" in r.json()["error"]["message"]
    assert r.headers["x-ratchet-budget-remaining-usd"] == "0.000000" and r.headers["x-should-retry"] == "false"
    assert int(r.headers["retry-after"]) >= 1 and re.fullmatch(r"(\d+h)?(\d+m)?\d+s", r.headers["x-ratchet-budget-reset"])
    assert r.headers["x-ratchet-spend-today-usd"] == f"{3 * TEACHER_COST:.6f}"
    assert metrics.REJECTIONS.value("budget") - refusals == 1
    (row,) = _rows(rt.ledger, "SELECT stage, status, client, error, cost_usd FROM failures")
    assert row == {"stage": "budget", "status": 429, "client": "acme", "error": "APIError 429 budget_exceeded", "cost_usd": 0.0}

    ok = _post(c, GLOBEX, tag=5)
    assert ok.status_code == 200 and "x-ratchet-spend-today-usd" not in ok.headers
    assert rt.ledger.totals()["requests"] == 4  # the refusal left no requests row and spent nothing
    assert rt.meter.usage("acme").day_usd == pytest.approx(3 * TEACHER_COST)


@pytest.mark.req("FR-12.2")
def test_monthly_cap_resets_on_the_first_and_names_the_later_reset(tmp_path, gw_env, monkeypatch):
    """A monthly cap spent on the 30th refuses until the 1st, even though the daily cap resets at midnight: the 429 says
    the later reset."""
    thirtieth = float(calendar.timegm((2026, 10, 30, 12, 0, 0)))
    monkeypatch.setattr("time.time", lambda: thirtieth)
    limits = f"acme:daily_usd={1.5 * TEACHER_COST:.10f},monthly_usd={1.5 * TEACHER_COST:.10f}"
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, key_limits=limits))
    c = TestClient(gw.app)
    assert _post(c, ACME, tag=1).status_code == 200
    assert _post(c, ACME, tag=2).status_code == 200
    r = _post(c, ACME, tag=3)
    assert r.status_code == 429 and "monthly budget" in r.json()["error"]["message"]
    first = float(calendar.timegm((2026, 11, 1, 0, 0, 0)))
    assert int(r.headers["retry-after"]) == math.ceil(first - thirtieth)
    monkeypatch.setattr("time.time", lambda: thirtieth + 86400)  # the 31st: a new day, the same month
    assert _post(c, ACME, tag=4).status_code == 429
    monkeypatch.setattr("time.time", lambda: first + 60)  # the 1st
    assert _post(c, ACME, tag=5).status_code == 200


@pytest.mark.req("FR-12.2", "NFR-3")
def test_spend_survives_a_restart_on_the_same_ledger(gw_env, tmp_path, monkeypatch):
    """The meter reads today's gateway requests and failed spend from the ledger when it starts, so a new Runtime on the
    same file refuses what the old one would have, and counts what failed requests spent."""
    settings = _live(tmp_path, key_limits=f"acme:daily_usd={1.5 * TEACHER_COST:.10f};globex:daily_requests=2")
    gw, rt = _gateway(gw_env, monkeypatch, settings)
    c = TestClient(gw.app)
    assert [_post(c, ACME, tag=i).status_code for i in range(3)] == [200, 200, 429]
    assert [_post(c, GLOBEX, tag=i).status_code for i in range(3)] == [200, 200, 429]
    rt.ledger.log_failure(request_id="req_failed", client="acme", stage="teacher", cost_usd=0.5)  # a failed request's spend

    gw2, rt2 = _gateway(gw_env, monkeypatch, settings)
    use = rt2.meter.usage("acme")
    assert (use.day_requests, use.day_usd) == (2, pytest.approx(2 * TEACHER_COST + 0.5))
    assert rt2.meter.usage("globex").day_requests == 2
    assert rt2.meter.usage(None).gateway_day_usd == pytest.approx(4 * TEACHER_COST + 0.5)
    c2 = TestClient(gw2.app)
    assert _post(c2, ACME).status_code == 429 and _post(c2, GLOBEX).status_code == 429


@pytest.mark.req("FR-12.2", "NFR-3")
def test_failed_requests_spend_counts_against_the_key(gw_env, tmp_path, monkeypatch):
    """An unsure student, then a teacher 502: the student's samples were paid for, so they count against acme's budget
    at once and after a restart (from the failures row), but not as an answered request."""
    student = FakeOpenAIServer(A, logprob=math.log(0.3))

    def teacher(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": {"message": "down"}})

    settings = _live(tmp_path, student_base="https://student.test/v1", key_limits="acme:daily_usd=1")
    gw, rt = _gateway(gw_env, monkeypatch, settings, teacher=httpx.MockTransport(teacher), student=student.transport)
    _version(rt.ledger, "live", {"threshold": 0.9})
    assert _post(TestClient(gw.app), ACME).status_code == 502
    sampled = rt.settings.price("student").cost(10, 15)
    use = rt.meter.usage("acme")
    assert (use.day_requests, use.day_usd) == (0, pytest.approx(sampled))
    _, rt2 = _gateway(gw_env, monkeypatch, settings, teacher=httpx.MockTransport(teacher), student=student.transport)
    assert rt2.meter.usage("acme").day_usd == pytest.approx(sampled)


# ---------- the spend meter ----------
@pytest.mark.req("FR-12.2")
def test_utc_rollover_resets_the_counters_and_a_clock_moved_back_reads_the_ledger():
    led = Ledger(":memory:")
    clock = FakeClock(float(calendar.timegm((2026, 10, 31, 23, 59, 0))))
    meter = SpendMeter(led, Caps(monthly_usd={"acme": 100.0}), clock=clock)
    meter.add("acme", 1.0)
    meter.add(None, 0.5)
    assert meter.usage("acme")[:4] == (1, 1.0, 1.0, 1.5)
    clock.advance(120)  # 00:01 on Nov 1: a new day and a new month
    assert meter.usage("acme")[:4] == (0, 0.0, 0.0, 0.0)
    meter.add("acme", 2.0)
    clock.advance(86400)  # Nov 2: a new day, the same month
    assert meter.usage("acme")[:4] == (0, 0.0, 2.0, 0.0)
    assert meter.snapshot()["gateway_usd"] == 0.0

    # The ledger holds Oct 31's gateway rows; a clock that moves back there reads them, since memory has nothing.
    oct31 = float(calendar.timegm((2026, 10, 31, 10, 0, 0)))
    _logged(led, "acme", 0.25, oct31)
    _logged(led, "acme", 0.75, oct31 + 1, request_id=None)  # Scout or the simulator: no gateway request id, not counted
    clock.now = oct31 + 3600
    assert meter.usage("acme")[:3] == (1, 0.25, 0.25)
    assert month_bounds(oct31) == (float(calendar.timegm((2026, 10, 1, 0, 0, 0))), float(calendar.timegm((2026, 11, 1, 0, 0, 0))))


@pytest.mark.req("FR-12.2")
def test_meter_ignores_bad_costs_and_counts_spend_without_a_request():
    led = Ledger(":memory:")
    meter = SpendMeter(led, clock=FakeClock(NOON))
    for bad in (-1.0, math.nan, math.inf):
        meter.add("acme", bad)
    meter.add("acme", 0.2, requests=0)  # an off-path job's cost for a request already counted
    assert meter.usage("acme")[:3] == (3, 0.2, 0.2)


@pytest.mark.req("FR-12.2")
def test_budget_alerts_fire_once_per_share_also_after_a_restart():
    """50, 80 and 100% of acme's $5.00 daily cap and of the gateway's $10.00 budget, each written once; a meter started
    again on the same ledger (with the spend in it) writes none of them again; one that finds the spend past a share no
    event records writes it at startup; the next UTC day starts over."""
    led = Ledger(":memory:")
    clock = FakeClock(NOON)
    caps = Caps(daily_usd={"acme": 5.0}, gateway_daily_usd=10.0, alert_pct=(50, 80, 100))

    def spend(meter: SpendMeter, usd: float) -> None:
        _logged(led, "acme", usd, clock())
        meter.add("acme", usd)

    meter = SpendMeter(led, caps, clock=clock)
    spend(meter, 2.0)
    assert _budget_events(led) == []
    spend(meter, 0.5)
    assert _budget_events(led) == ["acme reached 50% of $5.00 daily budget"]
    spend(meter, 2.5)  # 5.0: two shares at once, and half the gateway's budget
    assert _budget_events(led)[1:] == [
        "the gateway reached 50% of $10.00 daily budget",
        "acme reached 80% of $5.00 daily budget",
        "acme reached 100% of $5.00 daily budget",
    ]
    spend(meter, 1.0)
    assert len(_budget_events(led)) == 4

    SpendMeter(led, caps, clock=clock)  # a restart: the spend is read back, the alerts are not repeated
    assert len(_budget_events(led)) == 4
    SpendMeter(led, Caps(daily_usd={"acme": 5.0}, gateway_daily_usd=6.0, alert_pct=(50, 80, 100)), clock=clock)
    assert _budget_events(led)[4:] == [  # a lower gateway budget: the shares 6.0 already passed, written at startup
        "the gateway reached 50% of $6.00 daily budget",
        "the gateway reached 80% of $6.00 daily budget",
        "the gateway reached 100% of $6.00 daily budget",
    ]

    clock.advance(86400)
    meter.add("acme", 3.0)
    assert _budget_events(led)[-1] == "acme reached 50% of $5.00 daily budget"
    assert money(0.005) == "0.005" and money(5) == "5.00"


@pytest.mark.req("FR-12.2")
def test_an_alert_that_cannot_be_written_is_tried_again(monkeypatch):
    led = Ledger(":memory:")
    meter = SpendMeter(led, Caps(daily_usd={"acme": 1.0}, alert_pct=(50,)), clock=FakeClock(NOON))
    real = led.event
    calls = []

    def flaky(kind: str, detail: str) -> None:
        calls.append(detail)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        real(kind, detail)

    monkeypatch.setattr(led, "event", flaky)
    meter.add("acme", 0.6)  # the write fails; the request is not affected
    assert _budget_events(led) == []
    meter.add("acme", 0.1)
    assert _budget_events(led) == ["acme reached 50% of $1.00 daily budget"]


@pytest.mark.req("FR-12.2", "NFR-5")
def test_quota_path_issues_no_count_query(gw_env):
    gw = gw_env(RATCHET_API_KEYS=KEYS, RATCHET_API_KEY_DAILY_REQUESTS="5", RATCHET_API_KEY_DAILY_USD="1")
    statements: list[str] = []
    gw.rt.ledger.db.set_trace_callback(statements.append)
    try:
        gw.rt.meter.usage("acme")
        assert statements == []  # the checks read memory only
        r = TestClient(gw.app).post(CHAT, json={"messages": gw.rt.stream.sample(1).messages()}, headers=ACME)
    finally:
        gw.rt.ledger.db.set_trace_callback(None)
    assert r.status_code == 200 and r.headers["x-ratelimit-remaining-requests"] == "4"
    assert statements and not [s for s in statements if re.search(r"COUNT\(\*\) FROM requests WHERE client", s)]


# ---------- the gateway's budget ----------
@pytest.mark.req("FR-12.2", "NFR-3", "INV-7")
def test_student_only_serves_a_confident_student_and_refuses_what_needs_the_teacher(gw_env, tmp_path, monkeypatch):
    """Once the gateway's budget is spent (here by the first request's audit), a confident student answer is still
    served, with no audit and no shadow; an escalation, a pinned task and a request with tools get 429
    `gateway_budget_exceeded` without a teacher call, and what the escalation's samples cost is recorded."""
    student = FakeOpenAIServer(lambda body: "" if "hard" in json.dumps(body["messages"]) else A)  # "" is never valid
    teacher = FakeOpenAIServer(A)
    settings = _live(
        tmp_path,
        student_base="https://student.test/v1",
        audit_rate=1.0,
        daily_budget_usd=TEACHER_COST / 2,
        budget_mode="student_only",
    )
    gw, rt = _gateway(gw_env, monkeypatch, settings, teacher=teacher.transport, student=student.transport)
    _version(rt.ledger, "live", {"threshold": 0.5})
    _version(rt.ledger, "shadow", {"threshold": 0.5})
    c = TestClient(gw.app)
    first = _post(c, ACME, tag=1)
    assert first.status_code == 200 and first.json()["ratchet"]["served_by"] == "student"
    assert len(teacher.calls) == 1 and rt.meter.usage(None).gateway_day_usd >= TEACHER_COST / 2  # the audit spent it

    student_calls = len(student.calls)
    ok = _post(c, ACME, tag=2)
    assert ok.status_code == 200 and ok.json()["ratchet"]["served_by"] == "student"
    assert len(teacher.calls) == 1 and len(student.calls) == student_calls + 1  # no audit, no shadow sample
    row = _rows(rt.ledger, "SELECT route, audited, shadow_version FROM requests ORDER BY id DESC LIMIT 1")[0]
    assert row == {"route": "student", "audited": 0, "shadow_version": None}

    hard = _post(c, ACME, tag="hard")
    assert hard.status_code == 429 and _error(hard) == ("insufficient_quota", "gateway_budget_exceeded")
    assert hard.headers["x-should-retry"] == "false" and int(hard.headers["retry-after"]) >= 1
    assert "needs the teacher" in hard.json()["error"]["message"] and str(TEACHER_COST / 2) not in hard.text
    sampled = rt.settings.price("student").cost(10, 15)
    (failure,) = _rows(rt.ledger, "SELECT stage, status, client, cost_usd, request_id FROM failures")
    assert failure == {
        "stage": "budget",
        "status": 429,
        "client": "acme",
        "cost_usd": pytest.approx(sampled),
        "request_id": hard.headers["x-request-id"],
    }
    assert len(teacher.calls) == 1

    student_calls = len(student.calls)
    pinned = c.post(CHAT, json={"messages": _ask()}, headers={**ACME, "X-Ratchet-Task": "research-plan"})
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    with_tools = _post(c, ACME, tag=3, tools=tools)
    for r in (pinned, with_tools):
        assert r.status_code == 429 and _error(r) == ("insufficient_quota", "gateway_budget_exceeded")
    assert len(teacher.calls) == 1 and len(student.calls) == student_calls  # neither model was called
    assert any("the gateway reached 100%" in e for e in _budget_events(rt.ledger))


@pytest.mark.req("FR-12.2")
def test_block_mode_refuses_every_chat_request_until_midnight(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, daily_budget_usd=1.5 * TEACHER_COST))
    c = TestClient(gw.app)
    assert [_post(c, ACME, tag=i).status_code for i in range(2)] == [200, 200]  # the second overshoots by its own cost
    for headers in (ACME, GLOBEX):
        r = _post(c, headers)
        assert r.status_code == 429 and _error(r) == ("insufficient_quota", "gateway_budget_exceeded")
    assert c.get("/v1/models", headers=ACME).status_code == 200  # costs nothing, so it is not refused
    budget = c.get("/ratchet/stats").json()["budget"]
    assert budget == {
        "daily_usd": pytest.approx(1.5 * TEACHER_COST),
        "mode": "block",
        "spent_today_usd": pytest.approx(2 * TEACHER_COST),
        "remaining_usd": 0.0,
        "exceeded": True,
        "alert_pct": [50, 80, 100],
    }


@pytest.mark.req("INV-11", "FR-12.2")
def test_scout_spend_stays_under_its_own_guard(gw_env):
    """Scout's agents call the cascade without a gateway request id: their spend counts toward neither the gateway's
    budget nor any key's, and a spent /v1 budget does not stop Scout, which its own guard governs."""
    gw = gw_env(RATCHET_DAILY_BUDGET_USD="0.000000001")  # any /v1 spend spends it
    c = TestClient(gw.app)
    question = c.get("/scout/examples").json()["examples"][0]
    assert c.post("/scout/run", json={"question": question}).status_code == 200
    assert gw.rt.ledger.totals()["cost_usd"] > 0 and gw.rt.meter.usage(None).gateway_day_usd == 0.0
    assert c.post(CHAT, json={"messages": gw.rt.stream.sample(1).messages()}).status_code == 200
    r = c.post(CHAT, json={"messages": gw.rt.stream.sample(1).messages()})
    assert r.status_code == 429 and _error(r) == ("insufficient_quota", "gateway_budget_exceeded")
    assert c.post("/scout/run", json={"question": question}).status_code == 200
    assert SpendMeter(gw.rt.ledger).usage(None).gateway_day_usd == pytest.approx(gw.rt.meter.usage(None).gateway_day_usd)


# ---------- per-key quotas, scopes and storage ----------
@pytest.mark.req("FR-12.2", "FR-1.7", "INV-13")
def test_key_limits_override_the_defaults_and_bad_specs_stop_startup(gw_env, tmp_path):
    """RATCHET_KEY_LIMITS replaces the RATCHET_API_KEY_* defaults per key name; a name that is not a key, an unknown
    field, a field or a key twice, or a bad value stops startup, and the message never echoes an unknown entry (it may
    be a key pasted by mistake)."""
    gw = gw_env(RATCHET_API_KEYS=KEYS, RATCHET_API_KEY_DAILY_REQUESTS="3", RATCHET_KEY_LIMITS="acme:daily_requests=1")
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    assert [c.post(CHAT, json={"messages": msgs}, headers=ACME).status_code for _ in range(2)] == [200, 429]
    assert [c.post(CHAT, json={"messages": msgs}, headers=GLOBEX).status_code for _ in range(4)] == [200, 200, 200, 429]

    defaults = KeyLimits(daily_requests=3, rpm=60)
    got = parse_key_limits(
        " acme : daily_usd=5, monthly_usd=100 ,rpm=0,concurrency=4,tasks=ticket-triage|invoice-extract,store=0 ;",
        ["acme", "globex"],
        defaults,
    )
    assert got["globex"] == defaults
    assert got["acme"] == KeyLimits(3, 5.0, 100.0, 0, 4, frozenset({TRIAGE, "invoice-extract"}), False)
    assert got["acme"].usd_capped and not got["globex"].usd_capped and not got["acme"].allows_task("default")
    for spec, words in (
        (f"{ACME_KEY}:rpm=5", "does not start with the name of a key"),
        ("acme", "must read acme:field=value"),
        ("acme:rpm=5;acme:rpm=6", "appears twice"),
        ("acme:rpm=5,rpm=6", "acme.rpm appears twice"),
        ("acme:burst=5", "unknown field 'burst'"),
        ("acme:rpm", "must read field=value"),
        ("acme:rpm=-1", "acme.rpm must be a whole number"),
        ("acme:rpm=1e3", "acme.rpm must be a whole number"),
        ("acme:concurrency=10001", "acme.concurrency must be a whole number"),
        ("acme:daily_usd=nan", "acme.daily_usd must be US dollars"),
        ("acme:monthly_usd=-5", "acme.monthly_usd must be US dollars"),
        ("acme:store=yes", "acme.store must be 0 or 1"),
        ("acme:tasks=", "acme.tasks must be task names"),
        ("acme:tasks=a b", "acme.tasks must be task names"),
    ):
        with pytest.raises(ValueError, match=re.escape(words)) as e:
            parse_key_limits(spec, ["acme", "globex"])
        assert ACME_KEY not in str(e.value) and "RATCHET_KEY_LIMITS" in str(e.value)
    with pytest.raises(ValueError, match="none: RATCHET_API_KEYS is empty"):
        parse_key_limits("acme:rpm=5", [])
    with pytest.raises(ValueError, match="RATCHET_KEY_LIMITS: entry 1"):
        gw.Runtime(_live(tmp_path, key_limits="nobody:rpm=5"))


@pytest.mark.req("FR-12.3", "FR-1.7")
def test_task_scope_is_403_and_lists_only_the_scoped_tasks(gw_env, tmp_path, monkeypatch):
    settings = _live(tmp_path, key_limits=f"acme:tasks={TRIAGE}|page-read,daily_requests=1", unknown_tasks="reject")
    gw, rt = _gateway(gw_env, monkeypatch, settings)
    c = TestClient(gw.app)
    for task in ("research-plan", "default"):
        r = c.post(CHAT, json={"messages": _ask()}, headers={**ACME, "X-Ratchet-Task": task})
        assert r.status_code == 403 and _error(r) == ("permission_error", "task_not_allowed")
        assert r.json()["error"]["message"] == "task not allowed for this key"
    unknown = c.post(CHAT, json={"messages": _ask()}, headers={**ACME, "X-Ratchet-Task": "nonexistent"})
    assert unknown.status_code == 400 and unknown.json()["error"]["message"].endswith(f"registered: page-read, {TRIAGE}")
    assert _post(c, ACME).status_code == 200  # the refusals used none of acme's one request
    assert c.post(CHAT, json={"messages": _ask()}, headers={**GLOBEX, "X-Ratchet-Task": "research-plan"}).status_code == 200


@pytest.mark.req("FR-12.3", "INV-2")
def test_store_zero_keeps_a_keys_requests_out_of_the_training_set(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, key_limits="acme:store=0"))
    c = TestClient(gw.app)
    assert _post(c, ACME, tag=1).status_code == 200 and _post(c, GLOBEX, tag=2).status_code == 200
    assert _rows(rt.ledger, "SELECT client, stored FROM requests ORDER BY id") == [
        {"client": "acme", "stored": 0},
        {"client": "globex", "stored": 1},
    ]
    assert len(rt.ledger.examples()) == 1


# ---------- rate limits ----------
@pytest.mark.req("FR-12.3", "NFR-5")
def test_rpm_bucket_refuses_past_the_burst_and_refills_on_the_clock(gw_env, tmp_path, monkeypatch):
    """60 a minute is a burst of 10 and one more each second: the 11th request at the same instant is a 429
    `rate_limit_exceeded` with `Retry-After: 1` (which OpenAI SDKs honour), a second later one more passes, and a minute
    later the burst is back. globex has its own bucket."""
    clock = FakeClock()
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, key_limits="acme:rpm=60"), clock=clock)
    c = TestClient(gw.app)
    for i in range(10):
        r = _post(c, ACME, tag=i)
        assert r.status_code == 200 and r.headers["x-ratelimit-limit-requests"] == "60"
        assert r.headers["x-ratelimit-remaining-requests"] == str(9 - i)
    rate = metrics.REJECTIONS.value("rate")
    over = _post(c, ACME, tag=10)
    assert over.status_code == 429 and _error(over) == ("rate_limit_error", "rate_limit_exceeded")
    assert (over.headers["retry-after"], over.headers["x-ratelimit-remaining-requests"]) == ("1", "0")
    assert over.headers["x-ratelimit-reset-requests"] == "10s" and "x-should-retry" not in over.headers
    assert metrics.REJECTIONS.value("rate") - rate == 1
    assert _rows(rt.ledger, "SELECT stage, status, client FROM failures") == [{"stage": "rate", "status": 429, "client": "acme"}]
    assert _post(c, GLOBEX).status_code == 200 and "x-ratelimit-limit-requests" not in _post(c, GLOBEX).headers
    clock.advance(1.0)
    assert _post(c, ACME, tag=11).status_code == 200
    assert _post(c, ACME, tag=12).status_code == 429
    clock.advance(60.0)
    assert [_post(c, ACME, tag=20 + i).status_code for i in range(11)] == [200] * 10 + [429]


@pytest.mark.req("FR-12.3")
def test_daily_quota_headers_win_over_the_rate_headers(gw_env, tmp_path, monkeypatch):
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, key_limits="acme:rpm=60,daily_requests=5"), clock=FakeClock())
    r = _post(TestClient(gw.app), ACME)
    assert (r.headers["x-ratelimit-limit-requests"], r.headers["x-ratelimit-remaining-requests"]) == ("5", "4")


def _blocking_teacher() -> tuple[FakeOpenAIServer, threading.Event, threading.Event]:
    """A teacher whose answer to a message containing 'hold' waits until `release` is set (`entered` says it got there)."""
    entered, release = threading.Event(), threading.Event()

    def text(body: dict[str, Any]) -> str:
        if "hold" in json.dumps(body["messages"]):
            entered.set()
            release.wait(10)
        return A

    return FakeOpenAIServer(text), entered, release


def _in_background(c: TestClient, headers: dict[str, str]) -> tuple[threading.Thread, list[httpx.Response]]:
    out: list[httpx.Response] = []
    worker = threading.Thread(target=lambda: out.append(_post(c, headers, tag="hold")))
    worker.start()
    return worker, out


@pytest.mark.req("FR-12.3")
def test_concurrency_one_refuses_a_second_request_while_the_first_runs(gw_env, tmp_path, monkeypatch):
    teacher, entered, release = _blocking_teacher()
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, key_limits="acme:concurrency=1"), teacher=teacher.transport)
    c = TestClient(gw.app)
    worker, out = _in_background(c, ACME)
    try:
        assert entered.wait(10)
        second = _post(c, ACME, tag=2)
        assert second.status_code == 429 and _error(second) == ("rate_limit_error", "rate_limit_exceeded")
        assert second.headers["retry-after"] == "1" and "in flight" in second.json()["error"]["message"]
        assert _post(c, GLOBEX, tag=3).status_code == 200  # another key's slots are its own
    finally:
        release.set()
        worker.join(10)
    assert out[0].status_code == 200
    assert _post(c, ACME, tag=4).status_code == 200  # the slot was given back


@pytest.mark.req("FR-12.3", "NFR-5")
def test_max_inflight_answers_503_instead_of_queueing(gw_env, tmp_path, monkeypatch):
    teacher, entered, release = _blocking_teacher()
    gw, rt = _gateway(gw_env, monkeypatch, _live(tmp_path, max_inflight=1), teacher=teacher.transport)
    c = TestClient(gw.app)
    overloaded = metrics.REJECTIONS.value("overloaded")
    worker, out = _in_background(c, ACME)
    try:
        assert entered.wait(10)
        assert rt.limiter.inflight == 1
        r = _post(c, GLOBEX, tag=2)
        assert r.status_code == 503 and _error(r) == ("api_error", "overloaded") and r.headers["retry-after"] == "1"
    finally:
        release.set()
        worker.join(10)
    assert out[0].status_code == 200 and rt.limiter.inflight == 0
    assert metrics.REJECTIONS.value("overloaded") - overloaded == 1
    assert _rows(rt.ledger, "SELECT stage, status FROM failures") == [{"stage": "rate", "status": 503}]
    assert _post(c, GLOBEX, tag=3).status_code == 200


@pytest.mark.req("FR-12.3", "NFR-5")
def test_per_ip_rate_on_a_gateway_without_keys(gw_env, tmp_path, monkeypatch):
    """Without API keys the client IP is the only thing that tells callers apart (behind the proxy, the first
    X-Forwarded-For hop). 120 a minute is live mode's default, off in mock mode and whenever keys are set."""
    live, mock = (Settings(mode=mode, ip_rpm=None).limit_policy().ip_rpm for mode in ("live", "mock"))
    assert (live, mock) == (120, 0)
    clock = FakeClock()
    settings = _live(tmp_path, api_keys="", allow_unauthenticated=True, ip_rpm=6, trust_proxy=True)
    gw, rt = _gateway(gw_env, monkeypatch, settings, clock=clock)
    c = TestClient(gw.app)
    one = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    assert _post(c, one).status_code == 200
    r = _post(c, one, tag=2)
    assert r.status_code == 429 and _error(r) == ("rate_limit_error", "rate_limit_exceeded")
    assert r.headers["retry-after"] == "10" and "this client address" in r.json()["error"]["message"]
    assert "203.0.113.7" not in r.text
    assert _post(c, {"X-Forwarded-For": "198.51.100.2"}, tag=3).status_code == 200
    clock.advance(10)
    assert _post(c, one, tag=4).status_code == 200
    keyed, _ = _gateway(gw_env, monkeypatch, _live(tmp_path, db_path=str(tmp_path / "k.db"), ip_rpm=6), clock=clock)
    assert keyed.rt.limiter.ip_rpm == 0


@pytest.mark.req("FR-12.3")
def test_token_bucket_concurrency_and_limiter_units():
    bucket = TokenBucket(60, 2, now=0.0)
    assert [bucket.take(0.0).allowed for _ in range(3)] == [True, True, False]
    assert bucket.take(0.5).retry_after_s == pytest.approx(0.5)
    assert bucket.take(1.0).allowed and not bucket.take(0.0).allowed  # a clock moved back adds nothing
    assert [burst(r) for r in (1, 5, 6, 60, 120)] == [1, 1, 1, 10, 20]
    with pytest.raises(ValueError):
        TokenBucket(0, 1, now=0.0)

    clock = FakeClock(0.0)
    ips = RateLimiter(clock, max_buckets=3)
    for ip in ("a", "b", "c", "d"):
        assert ips.take(ip, 6).allowed
    assert len(ips) == 3 and ips.take("a", 6).allowed  # "a" was dropped (least recently used), so it starts full
    assert not ips.take("d", 6).allowed and ips.take("d", 12).allowed  # a new rate starts a new, full bucket
    assert ips.take("x", 0) is None

    slots = Concurrency()
    assert slots.acquire("k", 1) and not slots.acquire("k", 1)
    slots.release("k")
    assert slots.acquire("k", 1) and slots.count("k") == 1

    limiter = Limiter(max_inflight=1, clock=FakeClock(0.0))
    with limiter.admit("acme", rpm=6, concurrency=1) as slot:
        assert limiter.inflight == 1 and slot.decision is not None and slot.decision.remaining == 0
        with pytest.raises(Refused) as e:
            limiter.admit("globex")
        assert e.value.kind == "overloaded"
    assert limiter.inflight == 0
    with pytest.raises(Refused) as e:  # the bucket is empty: refused, and it holds no slot afterwards
        limiter.admit("acme", rpm=6, concurrency=1)
    assert e.value.kind == "rate" and limiter.inflight == 0 and limiter.concurrency.count("key:acme") == 0


@pytest.mark.req("FR-12.2", "FR-12.3")
def test_limit_settings_are_checked_at_startup(monkeypatch):
    for name in LIMIT_ENV:  # the defaults below are the code's, whatever the shell sets
        monkeypatch.delenv(name, raising=False)
    policy = Settings(mode="mock", budget_mode=" STUDENT_ONLY ", budget_alert_pct="100, 50,50,").limit_policy()
    assert (policy.budget_mode, policy.alert_pct, policy.max_inflight) == ("student_only", (50, 100), 32)
    assert Settings(budget_mode="").limit_policy().budget_mode == "block"
    assert parse_alert_pct("") == ()
    for bad, words in (
        ({"api_key_daily_usd": -1.0}, "RATCHET_API_KEY_*: daily_usd"),
        ({"api_key_monthly_usd": math.inf}, "RATCHET_API_KEY_*: monthly_usd"),
        ({"api_key_rpm": -5}, "RATCHET_API_KEY_*: rpm"),
        ({"api_key_concurrency": 10**6}, "RATCHET_API_KEY_*: concurrency"),
        ({"api_key_daily_requests": -1}, "RATCHET_API_KEY_*: daily_requests"),
        ({"daily_budget_usd": -0.5}, "RATCHET_DAILY_BUDGET_USD"),
        ({"budget_mode": "soft"}, "RATCHET_BUDGET_MODE"),
        ({"budget_alert_pct": "50,150"}, "RATCHET_BUDGET_ALERT_PCT"),
        ({"budget_alert_pct": "half"}, "RATCHET_BUDGET_ALERT_PCT"),
        ({"ip_rpm": -1}, "RATCHET_IP_RPM"),
        ({"max_inflight": -1}, "RATCHET_MAX_INFLIGHT"),
    ):
        with pytest.raises(ValueError, match=re.escape(words)):
            Settings(mode="mock", **bad).limit_policy()


@pytest.mark.req("FR-12.2")
def test_stats_report_each_keys_spend_and_cap_and_the_security_switches(gw_env, tmp_path, monkeypatch):
    limits = "acme:daily_usd=2,rpm=60;globex:concurrency=2"
    settings = _live(tmp_path, key_limits=limits, max_inflight=0, scrub_stats=False)  # names, not pseudonyms
    gw, rt = _gateway(gw_env, monkeypatch, settings, clock=FakeClock())
    c = TestClient(gw.app)
    assert _post(c, ACME).status_code == 200 and _post(c, GLOBEX).status_code == 200
    stats = c.get("/ratchet/stats").json()
    clients = {x["client"]: x for x in stats["clients"]}
    assert (clients["acme"]["spent_today_usd"], clients["acme"]["daily_cap_usd"]) == (pytest.approx(TEACHER_COST), 2.0)
    assert (clients["globex"]["spent_today_usd"], clients["globex"]["daily_cap_usd"]) == (pytest.approx(TEACHER_COST), None)
    assert {k: stats["security"][k] for k in ("key_limits", "ip_rpm", "max_inflight")} == {
        "key_limits": True,
        "ip_rpm": None,
        "max_inflight": None,
    }
    assert stats["budget"]["daily_usd"] is None and stats["budget"]["spent_today_usd"] == pytest.approx(2 * TEACHER_COST)
    assert ACME_KEY not in json.dumps(stats) and GLOBEX_KEY not in json.dumps(stats)  # names only (invariant 13)


@pytest.mark.req("FR-12.2", "NFR-3")
def test_router_refuses_teacher_work_under_student_only_without_a_gateway():
    """`RequestMeta(student_only=True)` straight on the router: a pinned task raises before any call; an unsure student
    raises after its samples, whose cost gets a `budget` failures row (no gateway context to write it later)."""
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.9})
    student = RecordingProvider(A, logprob=math.log(0.3))
    teacher = RecordingProvider(A)
    r = Ratchet(Settings(mode="mock", audit_rate=0.0, samples=3), led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    meta = RequestMeta(request_id="req_1", student_only=True)
    with pytest.raises(BudgetExceeded) as e:
        r.handle(_ask(), "research-plan", client="acme", meta=meta)
    assert e.value.need == "pinned" and not student.calls and not teacher.calls
    assert _rows(led, "SELECT * FROM failures") == []  # nothing spent, no gateway: nothing to record
    with pytest.raises(BudgetExceeded) as e:
        r.handle(_ask(), TRIAGE, client="acme", meta=meta)
    assert e.value.need == "escalation" and len(student.calls) == 1 and not teacher.calls
    (row,) = _rows(led, "SELECT stage, status, error, cost_usd, request_id FROM failures")
    assert row == {
        "stage": "budget",
        "status": 429,
        "error": "BudgetExceeded",
        "cost_usd": pytest.approx(Settings(mode="mock").price("student").cost(10, 15)),
        "request_id": "req_1",
    }
    assert led.totals()["requests"] == 0
