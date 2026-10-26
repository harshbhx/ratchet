"""Request ids, the failures table, structured logs, Prometheus metrics and trace forwarding (ADR 0062).

Every response carries the gateway's `req_<hex>` id (`x-request-id`), errors and streams included, beside
`openai-processing-ms`; a client's own id is kept when it is well formed and ignored otherwise; the completion id and
`ratchet.request_id` carry the same digits, and the ledger row is found by either. What failed lands in `failures` with
what it spent (a teacher failure after the student's samples), which `/ratchet/stats` counts against the savings. Log
lines and metrics carry ids, names, numbers and statuses, never request text, answers, URLs or keys (invariants 12 and
13); `/metrics` parses as the Prometheus text format and is closed in live mode without a key or the metrics token.
Every version transition writes an event. Models are `RecordingProvider` or `FakeOpenAIServer`; nothing leaves the
process.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from fakes import OPEN_TEACHER, FakeClock, FakeOpenAIServer, RecordingProvider, _audit, _shadow, _version
from fastapi.testclient import TestClient

from ratchet import config, metrics, obs
from ratchet.config import Settings
from ratchet.ledger import FAILURE_STAGES, Ledger
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.resilience import UpstreamError
from ratchet.router import Ratchet, RequestMeta, task_label

CHAT = "/v1/chat/completions"
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
B = json.dumps({"category": "refund", "priority": "low", "reply": "Your refund is on its way."})
ACME_KEY = "acme-api-key-0123456789abcdef"
GLOBEX_KEY = "globex-api-key-0123456789abcd"
ADMIN_KEY = "ops-admin-key-0123456789abcdef"
DASH_KEY = "screen-dash-key-0123456789abcd"
METRICS_TOKEN = "prometheus-scrape-token-0123456789"
ACME, GLOBEX = {"Authorization": f"Bearer {ACME_KEY}"}, {"Authorization": f"Bearer {GLOBEX_KEY}"}
REQUEST_ID = re.compile(r"req_[0-9a-f]{32}")
MARKER = "ZEBRA-MARKER-31337"  # a unique string in a request: it must never reach a log line
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


# ---------- helpers ----------
def _settings(**overrides: Any) -> Settings:
    """Every value the cascade tests depend on, set explicitly."""
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "teacher_reasoning_tokens": 0,
        "student_breaker_failures": 5,
        "teacher_breaker_failures": 5,
    }
    return Settings(**{**base, **overrides})


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice this month, order {tag}"}]


def _cascade(student: RecordingProvider, teacher: RecordingProvider, threshold: float = 0.5, **settings: Any):
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": threshold})
    s = _settings(**settings)
    return led, Ratchet(s, led, teacher, lambda v: (student, v.adapter_uri), seed=1, clock=FakeClock())


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    """A live gateway's settings: keys of every kind, a private dashboard, the teacher on a fake endpoint."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://tokenfactory.test/v1",
        "teacher_api_key": "tf-teacher-key-0123456789",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "teacher_retries": 2,
        "api_keys": f"acme:{ACME_KEY},globex:{GLOBEX_KEY}",
        "admin_keys": f"ops:{ADMIN_KEY}",
        "dashboard_keys": f"screen:{DASH_KEY}",
        "metrics_token": "",
        "api_key_daily_requests": 0,
        "tasks_file": "",
        "unknown_tasks": "distill",
        "bom_signing_key": "",
        "tavily_api_key": "",
        "mock_warmup_days": 0,
        "public_dashboard": None,
        "public_bom": None,
        "scrub_stats": None,
        "openapi": None,
        "allow_unauthenticated": None,
        "csp": "",
        "log_format": "",
        "log_level": "",
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
    }
    return Settings(**{**base, **overrides})


def _live_gateway(gw_env: Any, monkeypatch: pytest.MonkeyPatch, settings: Settings, **transports: Any) -> tuple[Any, Any]:
    gw = gw_env()
    rt = gw.Runtime(settings, transports=transports or {"teacher": FakeOpenAIServer("ok").transport}, sleep=lambda s: None)
    monkeypatch.setattr(gw, "rt", rt)
    return gw, rt


def _rows(led: Ledger, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute(sql, args)]


def _demo(gw: Any) -> list[dict[str, Any]]:
    return gw.rt.stream.sample(1).messages()


_SAMPLE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (\S+)")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"(,|$)')


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), value)


def parse_metrics(text: str) -> tuple[dict[str, str], dict[tuple[str, tuple[tuple[str, str], ...]], float]]:
    """A small parser for the Prometheus text format 0.0.4: ({family: type}, {(sample name, sorted labels): value}).
    Fails on any line that is not a comment, a blank or a well-formed sample."""
    types: dict[str, str] = {}
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(" ", 3)
            assert kind in ("counter", "gauge", "histogram", "summary", "untyped"), line
            types[name] = kind
            continue
        if line.startswith("# HELP "):
            continue
        assert not line.startswith("#"), line
        m = _SAMPLE.fullmatch(line)
        assert m is not None, line
        name, raw, value = m.groups()
        labels: dict[str, str] = {}
        if raw:
            consumed = 0
            for lm in _LABEL.finditer(raw):
                assert lm.start() == consumed, line
                labels[lm.group(1)] = _unescape(lm.group(2))
                consumed = lm.end()
            assert consumed == len(raw), line
        key = (name, tuple(sorted(labels.items())))
        assert key not in samples, f"duplicate series {line}"
        samples[key] = float(value)
    return types, samples


def _value(samples: dict[Any, float], metric: str, /, **labels: str) -> float:
    return samples.get((metric, tuple(sorted(labels.items()))), 0.0)


def _family(sample: str, types: dict[str, str]) -> str:
    for suffix in ("_bucket", "_sum", "_count"):
        if sample.endswith(suffix) and types.get(sample[: -len(suffix)]) == "histogram":
            return sample[: -len(suffix)]
    return sample


@pytest.fixture()
def ratchet_log(caplog: pytest.LogCaptureFixture):
    """caplog wired to the `ratchet` logger, which does not propagate once the gateway configured it."""
    logger = logging.getLogger("ratchet")
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


def _json_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    fmt = obs.JsonFormatter()
    return [json.loads(fmt.format(r)) for r in caplog.records if r.name == "ratchet"]


# ---------- request ids ----------
@pytest.mark.req("FR-1.60", "NFR-6")
def test_request_id_echoed_and_client_id_stored(gw):
    """Every response gets the gateway's own id; a client's well-formed X-Request-Id (or X-Client-Request-Id) is stored as
    `client_request_id`, and anything else is ignored without an error. The completion id and `ratchet.request_id` carry
    the same digits, and the ledger row holds both ids."""
    c = TestClient(gw.app)
    r = c.post(CHAT, json={"messages": _demo(gw)}, headers={"X-Ratchet-Task": TRIAGE, "X-Request-Id": "order-77.retry_1"})
    assert r.status_code == 200
    rid = r.headers["x-request-id"]
    assert REQUEST_ID.fullmatch(rid) and r.headers["openai-processing-ms"].isdigit()
    body = r.json()
    assert body["id"] == "chatcmpl-" + rid[4:] and body["ratchet"]["request_id"] == rid
    row = _rows(gw.rt.ledger, "SELECT request_id, client_request_id FROM requests")[-1]
    assert row == {"request_id": rid, "client_request_id": "order-77.retry_1"}

    r = c.post(CHAT, json={"messages": _demo(gw)}, headers={"X-Client-Request-Id": "abc-123"})
    assert _rows(gw.rt.ledger, "SELECT client_request_id FROM requests")[-1]["client_request_id"] == "abc-123"

    for bad in ("has spaces", "x" * 129, "semi;colon", "üml"):
        r = c.post(CHAT, json={"messages": _demo(gw)}, headers={"X-Request-Id": bad.encode()})  # raw bytes, as sent
        assert r.status_code == 200 and REQUEST_ID.fullmatch(r.headers["x-request-id"])
        assert r.headers["x-request-id"] != bad
        assert _rows(gw.rt.ledger, "SELECT client_request_id FROM requests")[-1]["client_request_id"] is None

    # A server id on every response, each its own, also on pages and unknown routes; the client's never replaces it.
    ids = {c.get(path, headers={"X-Request-Id": "mine"}).headers["x-request-id"] for path in ("/healthz", "/", "/nope")}
    assert len(ids) == 3 and all(REQUEST_ID.fullmatch(i) for i in ids)


@pytest.mark.req("FR-1.60")
def test_streamed_answer_carries_the_request_id(gw):
    c = TestClient(gw.app)
    with c.stream("POST", CHAT, json={"messages": _demo(gw), "stream": True}) as r:
        rid = r.headers["x-request-id"]
        chunks = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: {")]
    assert REQUEST_ID.fullmatch(rid)
    assert {ch["id"] for ch in chunks} == {"chatcmpl-" + rid[4:]}
    assert chunks[-1]["ratchet"]["request_id"] == rid


@pytest.mark.req("FR-1.60", "FR-12.4")
def test_error_responses_carry_x_request_id_and_envelope_request_id(gw_env):
    gw = gw_env(RATCHET_API_KEYS=f"acme:{ACME_KEY}", RATCHET_MAX_BODY_BYTES="1000")
    c = TestClient(gw.app, raise_server_exceptions=False)
    led = gw.rt.ledger

    def check(r: Any, status: int) -> str:
        assert r.status_code == status
        rid = r.headers["x-request-id"]
        assert REQUEST_ID.fullmatch(rid) and r.json()["request_id"] == rid, r.json()
        assert set(r.json()["error"]) == {"message", "type", "param", "code"}  # OpenAI's error object, untouched
        return rid

    check(c.post(CHAT, json={"messages": "x"}, headers=ACME), 400)  # validation: counted in metrics only, no row
    assert _rows(led, "SELECT * FROM failures") == []
    rid = check(c.post(CHAT, json={"messages": _demo(gw)}, headers={"Authorization": "Bearer wrong-key-0123456789"}), 401)
    rid413 = check(c.post(CHAT, content=b"x" * 2000, headers={**ACME, "Content-Type": "application/json"}), 413)
    rows = {r["request_id"]: r for r in _rows(led, "SELECT * FROM failures")}
    assert rows[rid]["stage"] == "auth" and rows[rid]["status"] == 401 and rows[rid]["client"] is None
    assert rows[rid]["error"] == "HTTPException 401 invalid_api_key"
    assert rows[rid413]["stage"] == "gateway" and rows[rid413]["status"] == 413

    # An unhandled error: the 500 handler runs outside the middleware and adds the id itself; the row names the class only.
    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"bug while handling {MARKER}")

    gw.rt.ratchet.handle = crash
    rid500 = check(c.post(CHAT, json={"messages": _demo(gw)}, headers=ACME), 500)
    row = {r["request_id"]: r for r in _rows(led, "SELECT * FROM failures")}[rid500]
    assert (row["stage"], row["status"], row["error"], row["client"]) == ("gateway", 500, "RuntimeError", "acme")
    assert MARKER not in json.dumps(_rows(led, "SELECT * FROM failures"))

    # Routes outside /v1 keep {"detail": ...}, with the id in the header.
    r = c.get("/ratchet/nope")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"} and REQUEST_ID.fullmatch(r.headers["x-request-id"])


@pytest.mark.req("FR-1.60")
def test_request_context_parsing():
    ctx = obs.new_context({"x-request-id": " a.b_c-1 ", "traceparent": TRACEPARENT, "tracestate": "vendor=1"})
    assert (ctx.client_request_id, ctx.trace_id, ctx.trace_flags, ctx.tracestate) == ("a.b_c-1", TRACE_ID, "01", "vendor=1")
    assert ctx.request_id == "req_" + ctx.hex and ctx.completion_id == "chatcmpl-" + ctx.hex
    assert obs.new_context({"x-request-id": "bad id", "x-client-request-id": "good"}).client_request_id == "good"
    for bad in (
        "00-" + TRACE_ID.upper() + "-b7ad6b7169203331-01",  # upper case
        "00-" + "0" * 32 + "-b7ad6b7169203331-01",  # all-zero trace id
        f"00-{TRACE_ID}-" + "0" * 16 + "-01",  # all-zero parent id
        f"ff-{TRACE_ID}-b7ad6b7169203331-01",  # version ff
        f"00-{TRACE_ID}-b7ad6b7169203331-01-extra",  # version 00 has exactly four fields
        f"00-{TRACE_ID}-b7ad6b716920333-01",  # short parent id
        "garbage",
    ):
        assert obs.parse_traceparent(bad) is None, bad
    assert obs.parse_traceparent(f"01-{TRACE_ID}-b7ad6b7169203331-00-future") == (TRACE_ID, "00")  # a later version
    assert obs.new_context({"traceparent": TRACEPARENT, "tracestate": "x" * 600}).tracestate is None
    assert obs.lookup_hex("chatcmpl-" + "a" * 32) == "a" * 32 and obs.lookup_hex("req_" + "A" * 32) is None
    assert obs.upstream_headers() == {}  # outside a request


@pytest.mark.req("FR-1.60")
def test_traceparent_forwarded_to_the_teacher_with_a_new_span(gw_env, tmp_path, monkeypatch):
    """A client's W3C traceparent reaches every upstream attempt with the same trace id and flags and a new span id;
    a malformed one, or none, sends nothing."""
    calls: list[httpx.Request] = []
    server = FakeOpenAIServer("ok")

    def teacher(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:  # the first attempt fails, so the retry shows a span of its own
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return server(request)

    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path), teacher=httpx.MockTransport(teacher))
    c = TestClient(gw.app)
    r = c.post(CHAT, json={"messages": _ask()}, headers={**ACME, "traceparent": TRACEPARENT, "tracestate": "v=1"})
    assert r.status_code == 200 and len(calls) == 2
    spans = []
    for call in calls:
        version, trace, span, flags = call.headers["traceparent"].split("-")
        assert (version, trace, flags) == ("00", TRACE_ID, "01") and call.headers["tracestate"] == "v=1"
        spans.append(span)
    assert len(set(spans)) == 2 and "b7ad6b7169203331" not in spans
    for headers in ({}, {"traceparent": "00-" + TRACE_ID.upper() + "-b7ad6b7169203331-01"}):
        calls.clear()
        assert c.post(CHAT, json={"messages": _ask(1)}, headers={**ACME, **headers}).status_code == 200
        assert calls and all("traceparent" not in call.headers for call in calls)


# ---------- request lookups ----------
@pytest.mark.req("FR-1.60", "INV-13")
def test_request_lookup_own_request_and_another_clients(gw_env):
    gw = gw_env(RATCHET_API_KEYS=f"acme:{ACME_KEY},globex:{GLOBEX_KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    c = TestClient(gw.app)
    r = c.post(CHAT, json={"messages": _demo(gw)}, headers={**ACME, "X-Request-Id": "acme-1"})
    rid, cid = r.headers["x-request-id"], r.json()["id"]
    for ref in (rid, cid):  # either id form
        own = c.get(f"/v1/ratchet/requests/{ref}", headers=ACME)
        assert own.status_code == 200, own.text
        assert own.json()["request_id"] == rid and own.json()["failures"] == []
        row = own.json()["request"]
        assert (row["client"], row["client_request_id"], row["request_id"]) == ("acme", "acme-1", rid)
        assert not {"messages", "completion", "content"} & set(row)  # metadata only
    other = c.get(f"/v1/ratchet/requests/{rid}", headers=GLOBEX)
    assert other.status_code == 404 and other.json()["error"]["code"] == "request_not_found"
    missing = c.get(f"/v1/ratchet/requests/req_{'0' * 32}", headers=GLOBEX)
    assert missing.status_code == 404 and missing.json()["error"] == other.json()["error"]  # nothing to tell apart
    for ref in ("req_xyz", "1", "chatcmpl-" + "0" * 24):
        assert c.get(f"/v1/ratchet/requests/{ref}", headers=ACME).status_code == 404
    assert c.get(f"/v1/ratchet/requests/{rid}").status_code == 401

    # A failed request has no requests row, only its failures row: the lookup still finds it, for its own client.
    over = c.post(CHAT, json={"messages": _demo(gw)}, headers=ACME)
    assert over.status_code == 429
    found = c.get(f"/v1/ratchet/requests/{over.headers['x-request-id']}", headers=ACME).json()
    assert found["request"] is None and [f["stage"] for f in found["failures"]] == ["quota"]

    # The dashboard API reads any request (public in mock mode).
    dash = c.get(f"/ratchet/requests/{rid}")
    assert dash.status_code == 200 and dash.json()["request"]["client"] == "acme"
    assert c.get(f"/ratchet/requests/req_{'0' * 32}").status_code == 404


# ---------- failures ----------
@pytest.mark.req("FR-12.4", "NFR-3")
def test_teacher_failure_row_carries_the_student_cost():
    """An escalation whose teacher call fails leaves no requests row; its failures row holds what the student's samples
    cost, and the totals count it against the savings."""
    student = RecordingProvider(A, logprob=math.log(0.3))  # unsure: escalates
    teacher = RecordingProvider(A, raise_on={OPEN_TEACHER}, exc=lambda m: UpstreamError(502, m, code="teacher_down"))
    led, r = _cascade(student, teacher, threshold=0.99)
    meta = RequestMeta(request_id="req_" + "1" * 32, client_request_id="mine")
    with pytest.raises(UpstreamError):
        r.handle(_ask(), TRIAGE, client="acme", meta=meta)
    assert _rows(led, "SELECT * FROM requests") == []
    (row,) = _rows(led, "SELECT * FROM failures")
    spent = r.s.price("student").cost(10, 15)  # one call: the prompt once, three samples of 5 tokens
    assert row["cost_usd"] == pytest.approx(spent) and spent > 0
    assert (row["stage"], row["status"], row["client"], row["task"]) == ("teacher", 502, "acme", TRIAGE)
    assert (row["request_id"], row["error"]) == (meta.request_id, "UpstreamError 502 teacher_down")
    assert row["latency_ms"] >= 0

    teacher.raise_on.clear()  # the teacher is back: an answered request with a baseline
    r.handle(_ask(1), TRIAGE)
    totals = led.totals()
    assert totals["failed_cost_usd"] == pytest.approx(spent)
    assert totals["saved_usd"] == pytest.approx(totals["baseline_usd"] - totals["cost_usd"] - spent)
    assert totals["savings_pct"] == pytest.approx(1 - (totals["cost_usd"] + spent) / totals["baseline_usd"])


@pytest.mark.req("FR-12.4", "NFR-3", "INV-7")
def test_teacher_502_through_the_gateway_writes_one_row_with_the_student_cost(gw_env, tmp_path, monkeypatch):
    student = FakeOpenAIServer(A, logprob=math.log(0.3))  # unsure, so the request escalates

    def teacher(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": {"message": f"down at https://tokenfactory.test/v1 for {MARKER}"}})

    settings = _live(tmp_path, student_base="https://student.test/v1", student_api_key="student-endpoint-token")
    gw, rt = _live_gateway(gw_env, monkeypatch, settings, teacher=httpx.MockTransport(teacher), student=student.transport)
    _version(rt.ledger, "live", {"threshold": 0.9})
    before = metrics.COST.value("acme")
    failed_before, teacher_failures = metrics.FAILED_COST.value("acme"), metrics.FAILURES.value("teacher")
    r = TestClient(gw.app).post(CHAT, json={"messages": _ask()}, headers={**ACME, "X-Ratchet-Task": TRIAGE})
    assert r.status_code == 502 and r.json()["request_id"] == r.headers["x-request-id"]
    (row,) = _rows(rt.ledger, "SELECT * FROM failures")  # the router's row; the gateway did not add a second
    assert (row["request_id"], row["stage"], row["status"], row["client"]) == (r.headers["x-request-id"], "teacher", 502, "acme")
    assert row["cost_usd"] == pytest.approx(rt.settings.price("student").cost(10, 15))
    assert MARKER not in row["error"] and "http" not in row["error"]
    assert metrics.COST.value("acme") - before == pytest.approx(row["cost_usd"])
    assert metrics.FAILED_COST.value("acme") - failed_before == pytest.approx(row["cost_usd"])
    assert metrics.FAILURES.value("teacher") - teacher_failures == 1
    assert gw.rt.ledger.totals()["failed_cost_usd"] == pytest.approx(row["cost_usd"])


@pytest.mark.req("FR-12.4")
def test_quota_429_writes_a_quota_row_and_counts_a_rejection(gw_env):
    gw = gw_env(RATCHET_API_KEYS=f"acme:{ACME_KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    c = TestClient(gw.app)
    assert c.post(CHAT, json={"messages": _demo(gw)}, headers=ACME).status_code == 200
    before = metrics.REJECTIONS.value("quota")
    r = c.post(CHAT, json={"messages": _demo(gw)}, headers={**ACME, "X-Ratchet-Task": TRIAGE})
    assert r.status_code == 429
    (row,) = _rows(gw.rt.ledger, "SELECT * FROM failures")
    assert (row["stage"], row["status"], row["client"], row["task"]) == ("quota", 429, "acme", TRIAGE)
    assert (row["request_id"], row["error"], row["cost_usd"]) == (
        r.headers["x-request-id"],
        "APIError 429 daily_quota_exceeded",
        0.0,
    )
    assert metrics.REJECTIONS.value("quota") - before == 1
    assert gw.rt.ledger.totals()["requests"] == 1  # still no requests row for a refusal


@pytest.mark.req("FR-12.4", "INV-7")
def test_teacher_429_is_a_teacher_failure_not_a_refusal(gw_env, tmp_path, monkeypatch):
    """A teacher 429 reaches the client as a 429, but the request was admitted: its row is the router's `teacher` row
    (the gateway adds no `rate` row), and it is not counted as a refusal before the cascade."""

    def teacher(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "2"})

    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path), teacher=httpx.MockTransport(teacher))
    before = {reason: metrics.REJECTIONS.value(reason) for reason in ("quota", "invalid_request", "other")}
    counted = metrics.REQUESTS.value(TRIAGE, "none", "none", "429")
    r = TestClient(gw.app).post(CHAT, json={"messages": _ask()}, headers={**ACME, "X-Ratchet-Task": TRIAGE})
    assert r.status_code == 429 and r.json()["request_id"] == r.headers["x-request-id"]
    (row,) = _rows(rt.ledger, "SELECT stage, status, client, task, request_id FROM failures")
    assert row == {"stage": "teacher", "status": 429, "client": "acme", "task": TRIAGE, "request_id": r.headers["x-request-id"]}
    assert {reason: metrics.REJECTIONS.value(reason) for reason in before} == before
    assert metrics.REQUESTS.value(TRIAGE, "none", "none", "429") - counted == 1


@pytest.mark.req("FR-12.4", "NFR-3", "INV-7")
def test_a_bug_on_the_teacher_path_is_a_gateway_failure_not_the_teachers(gw_env, tmp_path, monkeypatch):
    """Only an HTTP failure is the teacher's. A bug after the student was sampled (here in `_store_example`, after the
    teacher answered) is a 500 filed under `gateway` with what the samples cost, so the teacher-failure alert does not
    send the operator to a teacher outage. Outside a gateway request the router files it under `gateway` itself."""
    student = FakeOpenAIServer(A, logprob=math.log(0.3))  # unsure, so the request escalates
    settings = _live(tmp_path, student_base="https://student.test/v1", student_api_key="student-endpoint-token")
    gw, rt = _live_gateway(gw_env, monkeypatch, settings, teacher=FakeOpenAIServer(A).transport, student=student.transport)
    _version(rt.ledger, "live", {"threshold": 0.9})

    def bug(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"bug while storing {MARKER}")

    monkeypatch.setattr(rt.ratchet, "_store_example", bug)
    teacher_failures, gateway_failures = metrics.FAILURES.value("teacher"), metrics.FAILURES.value("gateway")
    failed_before = metrics.FAILED_COST.value("acme")
    c = TestClient(gw.app, raise_server_exceptions=False)
    r = c.post(CHAT, json={"messages": _ask()}, headers={**ACME, "X-Ratchet-Task": TRIAGE})
    assert r.status_code == 500
    spent = rt.settings.price("student").cost(10, 15)  # the three samples; the teacher's own spend is past the bug
    (row,) = _rows(rt.ledger, "SELECT request_id, stage, status, error, client, task, cost_usd FROM failures")
    assert row == {
        "request_id": r.headers["x-request-id"],
        "stage": "gateway",
        "status": 500,
        "error": "RuntimeError",
        "client": "acme",
        "task": TRIAGE,
        "cost_usd": pytest.approx(spent),
    }
    assert spent > 0 and metrics.FAILURES.value("teacher") == teacher_failures
    assert metrics.FAILURES.value("gateway") - gateway_failures == 1
    assert metrics.FAILED_COST.value("acme") - failed_before == pytest.approx(spent)
    assert rt.ledger.totals()["failed_cost_usd"] == pytest.approx(spent)

    # A row that carries spend is never throttled away.
    rt.failure_rows = gw.FailureThrottle(per_minute=0)
    assert c.post(CHAT, json={"messages": _ask(1)}, headers={**ACME, "X-Ratchet-Task": TRIAGE}).status_code == 500
    assert len(_rows(rt.ledger, "SELECT * FROM failures WHERE stage='gateway' AND cost_usd > 0")) == 2

    # In process, on the escalation and the teacher-only paths: the router's own `gateway` row, no status.
    teacher = RecordingProvider(A, raise_on={OPEN_TEACHER}, exc=lambda m: RuntimeError(m))
    led, cascade = _cascade(RecordingProvider(A, logprob=math.log(0.3)), teacher, threshold=0.99)
    with pytest.raises(RuntimeError):
        cascade.handle(_ask(), TRIAGE, client="acme")
    with pytest.raises(RuntimeError):
        cascade.teacher_only(_ask(1), TRIAGE, client="acme")
    rows = _rows(led, "SELECT stage, status, error, cost_usd FROM failures")
    assert [(x["stage"], x["status"], x["error"]) for x in rows] == [("gateway", None, "RuntimeError")] * 2
    assert rows[0]["cost_usd"] == pytest.approx(cascade.s.price("student").cost(10, 15)) and rows[1]["cost_usd"] == 0.0


@pytest.mark.req("FR-12.4", "FR-1.60")
def test_gateway_failure_row_exists_before_the_response_starts(gw_env):
    """A client may look its refused request up as soon as the error arrives (API.md, RUNBOOK): under a real server the
    response is out before the app returns, so the gateway writes the row before the response starts."""
    gw = gw_env(RATCHET_API_KEYS=f"acme:{ACME_KEY}")
    seen: list[tuple[int, str, list[dict[str, Any]]]] = []
    body = json.dumps({"messages": _demo(gw)}).encode()

    async def call() -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": CHAT,
            "raw_path": CHAT.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"authorization", b"Bearer nope-0123456789abcdef"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        messages = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                rid = dict(message["headers"])[b"x-request-id"].decode()
                seen.append((message["status"], rid, gw.rt.ledger.failures_for(rid)))

        await gw.app(scope, receive, send)

    anyio.run(call)
    ((status, rid, rows),) = seen
    assert status == 401 and REQUEST_ID.fullmatch(rid)
    assert [(x["stage"], x["status"]) for x in rows] == [("auth", 401)]


@pytest.mark.req("FR-12.4", "INV-15")
def test_swallowed_failures_get_rows_and_breaker_refusals_are_sampled():
    """A failed student call the teacher covered gets a row each time; once the breaker is open, its refusals get a row
    on the 1st, 11th, 21st ... time only. A failed audit gets a row too. Every request is still answered."""
    student = RecordingProvider(A, raise_on={"student-v1"})
    led, r = _cascade(student, RecordingProvider(A))
    meta = RequestMeta(request_id="req_" + "2" * 32)
    counted = metrics.FAILURES.value("student")
    for i in range(30):
        assert r.handle(_ask(i), TRIAGE, meta=meta).served_by == "teacher"
    rows = _rows(led, "SELECT stage, status, error FROM failures")
    errors = [x for x in rows if x["error"] == "ConnectError"]
    refusals = [x for x in rows if x["error"].startswith("CircuitOpen")]
    assert len(errors) == 5 and {(x["stage"], x["status"]) for x in errors} == {("student", 502)}
    assert len(refusals) == 3 and {(x["stage"], x["status"]) for x in refusals} == {("student", 503)}  # 25 refusals
    assert metrics.FAILURES.value("student") - counted == 30  # the counter is not sampled

    teacher = RecordingProvider(B, raise_on={OPEN_TEACHER}, exc=httpx.ReadTimeout)
    led, r = _cascade(RecordingProvider(A), teacher, audit_rate=1.0)
    res = r.handle(_ask(), TRIAGE, client="acme")
    assert res.served_by == "student" and not res.audited
    (row,) = _rows(led, "SELECT stage, status, error, client, cost_usd FROM failures")
    assert row == {"stage": "audit", "status": 504, "error": "ReadTimeout", "client": "acme", "cost_usd": 0.0}


@pytest.mark.req("FR-12.4")
def test_stats_errors_last_hour_and_failed_cost(gw):
    led = gw.rt.ledger
    led.log_failure(stage="teacher", error="UpstreamError 502", status=502, cost_usd=0.25)
    led.log_failure(stage="student", error="ConnectError", status=502)
    led.log_failure(stage="student", error="ConnectError", status=502)
    led.log_failure(stage="auth", error="HTTPException 401", status=401, ts=time.time() - 7200)  # two hours ago
    stats = TestClient(gw.app).get("/ratchet/stats").json()
    assert stats["errors"] == {"last_hour": {"student": 2, "teacher": 1}}
    assert stats["totals"]["failed_cost_usd"] == pytest.approx(0.25)
    with pytest.raises(ValueError, match="stage"):
        led.log_failure(stage="elsewhere", error="x", status=500)
    with pytest.raises(ValueError, match="columns"):
        led.log_failure(stage="auth", message="never text")


@pytest.mark.req("FR-12.4")
def test_gateway_failure_rows_are_throttled_per_stage(gw_env):
    clock = FakeClock()
    throttle = __import__("ratchet.gateway", fromlist=["FailureThrottle"]).FailureThrottle(per_minute=2, clock=clock)
    assert [throttle.allow("auth") for _ in range(3)] == [True, True, False]
    assert throttle.allow("quota")  # each stage has its own window
    clock.advance(60)
    assert throttle.allow("auth")

    gw = gw_env(RATCHET_API_KEYS=f"acme:{ACME_KEY}")
    gw.rt.failure_rows = gw.FailureThrottle(per_minute=2)
    c = TestClient(gw.app)
    before, failures = metrics.REJECTIONS.value("auth"), metrics.FAILURES.value("auth")
    for _ in range(5):
        assert c.post(CHAT, json={"messages": []}, headers={"Authorization": "Bearer nope-0123456789abcdef"}).status_code == 401
    assert len(_rows(gw.rt.ledger, "SELECT * FROM failures WHERE stage='auth'")) == 2
    assert metrics.REJECTIONS.value("auth") - before == 5  # every one is still counted
    assert metrics.FAILURES.value("auth") - failures == 5


# ---------- logs ----------
@pytest.mark.req("NFR-6", "INV-12", "INV-13", "FR-12.4")
def test_json_log_lines_carry_no_content_and_no_keys(gw_env, tmp_path, monkeypatch, ratchet_log):
    attempts = {"n": 0}
    server = FakeOpenAIServer(f"answer about {MARKER}")

    def teacher(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if b"fail-now" in request.content:
            return httpx.Response(500, json={"error": {"message": f"boom at https://tokenfactory.test/v1 {MARKER}"}})
        return server(request)

    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path), teacher=httpx.MockTransport(teacher))
    c = TestClient(gw.app)
    msgs = [{"role": "user", "content": f"my card is 4242 4242 4242 4242 and {MARKER}"}]
    ok = c.post(CHAT, json={"messages": msgs}, headers={**ACME, "X-Request-Id": "acme-7", "traceparent": TRACEPARENT})
    assert ok.status_code == 200
    bad = c.post(CHAT, json={"messages": [{"role": "user", "content": f"fail-now {MARKER}"}]}, headers=ACME)
    assert bad.status_code == 502
    rt.ledger.event("note", "probe failed: ConnectError: [Errno 111] at https://student.internal:8000/v1")

    lines = _json_lines(ratchet_log)
    text = "\n".join(json.dumps(line) for line in lines)
    for secret in (MARKER, ACME_KEY, "4242", "tokenfactory.test", "student.internal", "tf-teacher-key"):
        assert secret not in text, secret
    chats = [line for line in lines if line["event"] == "chat"]
    assert [ch["status"] for ch in chats] == [200, 502]
    expected = {
        "ts", "level", "logger", "event", "request_id", "client", "task", "served_by", "route", "version", "confidence",
        "escalated", "audited", "latency_ms", "wall_ms", "cost_usd", "status",
    }  # fmt: skip
    first = chats[0]
    assert expected <= set(first), set(first)
    assert (first["request_id"], first["client"], first["client_request_id"], first["trace_id"]) == (
        ok.headers["x-request-id"],
        "acme",
        "acme-7",
        TRACE_ID,
    )
    assert (first["served_by"], first["route"], first["level"], first["logger"]) == ("teacher", "cold_start", "info", "ratchet")
    assert chats[1]["request_id"] == bad.headers["x-request-id"] and chats[1]["served_by"] is None
    (warning,) = [line for line in lines if line["event"] == "upstream_error"]
    assert {k: warning[k] for k in ("role", "error", "status", "level")} == {
        "role": "teacher",
        "error": "UpstreamError",
        "status": 500,
        "level": "warning",
    }
    (mirrored,) = [line for line in lines if line["event"] == "ledger_event" and line["kind"] == "note"]
    assert mirrored["detail"] == "probe failed: ConnectError"  # scrubbed like a public dashboard's events


@pytest.mark.req("NFR-6")
def test_log_settings_formats_and_handlers(monkeypatch):
    assert (config.LOG_FORMATS, config.LOG_LEVELS) == (obs.LOG_FORMATS, obs.LOG_LEVELS)  # config cannot import obs
    assert Settings(mode="mock", log_format="", log_level="").log_settings() == ("text", "WARNING")
    assert Settings(mode="live", log_format="", log_level="").log_settings() == ("json", "INFO")
    assert Settings(mode="live", log_format=" TEXT ", log_level="debug").log_settings() == ("text", "DEBUG")
    with pytest.raises(ValueError, match="RATCHET_LOG_FORMAT"):
        Settings(log_format="xml").log_settings()
    with pytest.raises(ValueError, match="RATCHET_LOG_LEVEL"):
        Settings(log_level="loud").log_settings()

    logger = logging.getLogger("ratchet")
    saved = (logger.level, logger.propagate, list(logger.handlers))
    try:
        stream = io.StringIO()
        obs.configure("text", "INFO", stream)
        obs.configure("text", "INFO", stream)  # reconfigured: still one handler
        assert sum(1 for h in logger.handlers if getattr(h, "_ratchet_handler", False)) == 1
        obs.log(logging.INFO, "chat", client="acme", note='has "quotes" and spaces', ms=1.5, ok=True, none=None)
        obs.log(logging.DEBUG, "hidden")
        line = stream.getvalue().strip()
        assert re.fullmatch(r"\S+Z INFO chat client=acme note=\"has \\\"quotes\\\" and spaces\" ms=1.5 ok=true none=-", line)
        stream2 = io.StringIO()
        obs.configure("json", "WARNING", stream2)
        obs.log(logging.INFO, "quiet")
        obs.upstream_failure("student", httpx.ConnectError("refused by https://student.internal"))
        rec = json.loads(stream2.getvalue())
        assert {k: rec[k] for k in ("event", "role", "error", "status")} == {
            "event": "upstream_error",
            "role": "student",
            "error": "ConnectError",
            "status": 502,
        }
        with pytest.raises(ValueError):
            obs.configure("yaml", "INFO")
    finally:
        for h in list(logger.handlers):
            if getattr(h, "_ratchet_handler", False) and h not in saved[2]:
                logger.removeHandler(h)
        for h in saved[2]:
            if h not in logger.handlers:
                logger.addHandler(h)
        logger.setLevel(saved[0])
        logger.propagate = saved[1]


@pytest.mark.req("NFR-6")
def test_event_listeners_see_every_event_and_cannot_fail_it():
    led = Ledger(":memory:")
    seen: list[dict[str, Any]] = []

    def broken(row: dict[str, Any]) -> None:
        raise RuntimeError("listener bug")

    led.add_event_listener(seen.append)
    led.add_event_listener(seen.append)  # once
    led.add_event_listener(broken)
    led.event("canary", "v1 canary 10%")
    assert [(e["kind"], e["detail"]) for e in seen] == [("canary", "v1 canary 10%")]
    assert seen[0]["id"] == led.events()[-1]["id"] and led.events()[-1]["kind"] == "canary"


# ---------- version transitions ----------
@pytest.mark.req("NFR-6", "FR-12.9")
@pytest.mark.parametrize("sequential", [0, 1])
def test_every_version_transition_writes_an_event(monkeypatch, sequential):
    """Cold start -> canary -> live, then shadow -> canary -> live over the old live version, then a candidate replaced
    by a newer one, a rolled-back canary and a rejected candidate: every move the gate makes goes through the
    compare-and-set writers (`transition`, `promote_live`; never `set_version`, ADR 0064) and writes, in the same call,
    an event naming that version, the old live version's retirement included (`live_retired`). The same under the
    anytime-valid canary rule (`sequential=1`, ADR 0025), which once moved versions with `set_version`."""
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=5, min_shadow=5, sequential=sequential))
    log: list[tuple[str, int | str, str, str]] = []  # ("set", vid, from, to) and ("event", kind, detail, "")
    real_transition, real_promote, real_event = led.transition, led.promote_live, led.event

    def set_version(*args: object, **kwargs: object) -> None:
        raise AssertionError("the release gate moved a version with set_version, which does not compare and set")

    def transition(vid, from_state, from_pct, to_state, to_pct, event=None):
        moved = real_transition(vid, from_state, from_pct, to_state, to_pct, event)
        if moved:  # its event was written inside the call, so it is logged after the move it names
            log.insert(len(log) - (event is not None), ("set", vid, from_state, to_state))
        return moved

    def promote_live(vid, from_state, from_pct, event=None, *, old_live, retired_event=None):
        moved = real_promote(vid, from_state, from_pct, event, old_live=old_live, retired_event=retired_event)
        if moved:  # the retirement's event, then the promotion's, each after its move
            at = len(log) - (event is not None) - (retired_event is not None)
            if old_live is not None:
                log.insert(at, ("set", old_live, "live", "retired"))
                at += 1 + (retired_event is not None)
            log.insert(at, ("set", vid, from_state, "live"))
        return moved

    def event(kind: str, detail: str) -> None:
        real_event(kind, detail)
        log.append(("event", kind, detail, ""))

    monkeypatch.setattr(led, "set_version", set_version)
    monkeypatch.setattr(led, "transition", transition)
    monkeypatch.setattr(led, "promote_live", promote_live)
    monkeypatch.setattr(led, "event", event)
    good = {"precision": 0.99, "coverage": 0.9, "threshold": 0.5}
    v1 = led.create_version("base", "student-v1", 100, "h1", good)
    assert gate.admit(v1)
    for _ in range(2):  # canary 10 -> 50 -> live
        _audit(led, v1, 5)
        gate.tick()
    assert led.live() is not None and led.live().id == v1
    v2 = led.create_version("base", "student-v2", 120, "h2", good)
    assert gate.admit(v2) and led.by_state("shadow").id == v2
    _shadow(led, v2, 5, live_vid=v1)
    gate.tick()  # shadow -> canary 10
    for _ in range(2):  # canary 50, then live over v1
        _audit(led, v2, 5)
        gate.tick()
    assert led.live().id == v2 and led.version(v1).state == "retired"

    v3 = led.create_version("base", "student-v3", 130, "h3", good)
    assert gate.admit(v3)
    v4 = led.create_version("base", "student-v4", 140, "h4", good)
    assert gate.admit(v4) and led.version(v3).state == "retired"  # replaced by a newer candidate
    _shadow(led, v4, 5, live_vid=v2)
    gate.tick()  # shadow -> canary 10
    _audit(led, v4, 20, 2)  # 10 of 20 agree: the upper bound is below the floor
    gate.tick()
    assert led.version(v4).state == "rolled_back"
    v5 = led.create_version("base", "student-v5", 150, "h5", {"precision": 0.5, "coverage": 0.9, "threshold": 0.5})
    assert not gate.admit(v5) and led.version(v5).state == "rejected"

    sets = [(i, entry) for i, entry in enumerate(log) if entry[0] == "set"]
    assert {(entry[2], entry[3]) for _, entry in sets} == {
        ("candidate", "canary"), ("canary", "canary"), ("canary", "live"), ("candidate", "shadow"), ("shadow", "canary"),
        ("live", "retired"), ("shadow", "retired"), ("canary", "rolled_back"), ("candidate", "rejected"),
    }  # fmt: skip
    assert len(sets) == 14
    for i, (_, vid, before, after) in sets:
        nxt = next((entry for entry in log[i + 1 :] if entry[0] == "event"), None)
        assert nxt is not None and re.search(rf"\bv{vid}\b", nxt[2]), (vid, before, after, nxt)
    retired = [entry for entry in log if entry[:2] == ("event", "live_retired")]
    assert len(retired) == 1 and retired[0][2].startswith(f"v{v1} retired: v{v2} replaces it")
    assert len(led.events()) == sum(1 for entry in log if entry[0] == "event")


# ---------- metrics ----------
@pytest.mark.req("FR-12.4")
def test_metrics_parse_count_and_bound_their_labels(gw):
    c = TestClient(gw.app)
    types, before = parse_metrics(c.get("/metrics").text)
    r = c.post(CHAT, json={"messages": _demo(gw)}, headers={"X-Ratchet-Task": TRIAGE})
    assert (
        r.status_code == 200
        and c.post(CHAT, json={"messages": _demo(gw)}, headers={"X-Ratchet-Task": "my-adhoc"}).status_code == 200
    )
    assert c.post(CHAT, json={"messages": "x"}).status_code == 400
    resp = c.get("/metrics")
    assert resp.headers["content-type"] == metrics.CONTENT_TYPE
    types, after = parse_metrics(resp.text)

    for name in ("ratchet_requests_total", "ratchet_request_seconds", "ratchet_upstream_requests_total", "ratchet_circuit_open"):
        assert name in types
    assert types["ratchet_request_seconds"] == "histogram" and types["ratchet_requests_total"] == "counter"
    assert {_family(name, types) for name, _ in after} <= set(types)  # every sample's family has a # TYPE line

    def delta(name: str, **labels: str) -> float:
        return _value(after, name, **labels) - _value(before, name, **labels)

    assert delta("ratchet_requests_total", task=TRIAGE, served_by="teacher", route="cold_start", status="200") == 1
    assert delta("ratchet_requests_total", task="other", served_by="teacher", route="cold_start", status="200") == 1
    assert delta("ratchet_requests_total", task="none", served_by="none", route="none", status="400") == 1
    assert delta("ratchet_rejections_total", reason="invalid_request") == 1
    assert (
        delta("ratchet_examples_stored_total", task="other", split="train")
        + delta("ratchet_examples_stored_total", task="other", split="golden")
        == 1
    )
    assert not any(dict(labels).get("task") == "my-adhoc" for _, labels in after)  # an ad-hoc name never becomes a label
    assert delta("ratchet_cost_usd_total", client="none") > 0 and delta("ratchet_baseline_usd_total", client="none") > 0
    assert _value(after, "ratchet_info", version=gw.__version__, mode="mock") == 1

    # Histogram buckets are cumulative and end at +Inf, which equals the count.
    buckets = sorted(
        (
            (float(dict(lb)["le"]), v)
            for (name, lb), v in after.items()
            if name == "ratchet_request_seconds_bucket" and dict(lb)["served_by"] == "teacher"
        ),
    )
    counts = [v for _, v in buckets]
    assert counts == sorted(counts) and math.isinf(buckets[-1][0])
    assert buckets[-1][1] == _value(after, "ratchet_request_seconds_count", served_by="teacher") >= 2


@pytest.mark.req("FR-12.4")
def test_cascade_metrics_for_upstreams_audits_and_examples():
    student = RecordingProvider(A)
    led, r = _cascade(student, RecordingProvider(A), audit_rate=1.0)

    def snap() -> dict[str, float]:
        return {
            "student_ok": metrics.UPSTREAM.value("student", "ok"),
            "audit_ok": metrics.UPSTREAM.value("audit", "ok"),
            "agree": metrics.AUDITS.value("agree"),
            "student_timeout": metrics.UPSTREAM.value("student", "timeout"),
            "student_open": metrics.UPSTREAM.value("student", "circuit_open"),
            "escalations": metrics.ESCALATIONS.value(TRIAGE),
            "stored": metrics.EXAMPLES.value(TRIAGE, "train") + metrics.EXAMPLES.value(TRIAGE, "golden"),
            "seconds": metrics.UPSTREAM_SECONDS.count("student"),
        }

    before = snap()
    assert r.handle(_ask(), TRIAGE).audited
    student.raise_on.add("student-v1")
    student.exc = httpx.ReadTimeout
    for i in range(7):  # 5 timeouts open the breaker, then 2 refusals
        assert r.handle(_ask(f"t{i}"), TRIAGE).route in ("student_error", "student_breaker")
    after = snap()
    d = {k: after[k] - before[k] for k in before}
    assert (d["student_ok"], d["audit_ok"], d["agree"]) == (1, 1, 1)
    assert (d["student_timeout"], d["student_open"], d["escalations"], d["seconds"]) == (5, 2, 7, 6)
    assert d["stored"] == 8  # the audit's example and the seven escalations' (new prompts each)
    assert task_label("my-adhoc") == "other" and task_label(TRIAGE) == TRIAGE and task_label(None) == "none"


@pytest.mark.req("FR-12.4", "NFR-5", "INV-13")
def test_metrics_401_in_live_mode_without_a_key_or_token(gw_env, tmp_path, monkeypatch):
    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path, metrics_token=METRICS_TOKEN))
    c = TestClient(gw.app)
    assert c.post(CHAT, json={"messages": _ask()}, headers=ACME).status_code == 200
    r = c.get("/metrics")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert c.get("/metrics", headers={"Authorization": "Bearer not-the-token-0123456789"}).status_code == 401
    assert c.get("/metrics", headers=ACME).status_code == 403  # an API key reads nothing here
    for headers in (
        {"Authorization": f"Bearer {METRICS_TOKEN}"},
        {"X-API-Key": DASH_KEY},
        {"Authorization": f"Bearer {ADMIN_KEY}"},
    ):
        ok = c.get("/metrics", headers=headers)
        assert ok.status_code == 200 and ok.headers["content-type"] == metrics.CONTENT_TYPE, headers
        _, samples = parse_metrics(ok.text)
        assert _value(samples, "ratchet_cost_usd_total", client="acme") > 0  # a keyed reader sees key names
    # The token reads /metrics and nothing else.
    assert c.get("/ratchet/stats", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).status_code == 401
    assert METRICS_TOKEN not in json.dumps(_rows(rt.ledger, "SELECT * FROM failures"))

    # Without reader keys, the token alone opens it; without the token too, nobody can.
    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path, admin_keys="", dashboard_keys="", metrics_token=METRICS_TOKEN))
    c = TestClient(gw.app)
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).status_code == 200
    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path, admin_keys="", dashboard_keys=""))
    assert TestClient(gw.app).get("/metrics").status_code == 403


@pytest.mark.req("FR-12.4", "INV-13")
def test_metrics_token_is_checked_at_startup(gw_env, tmp_path):
    gw = gw_env()
    with pytest.raises(ValueError, match="RATCHET_METRICS_TOKEN is shorter"):
        gw.Runtime(_live(tmp_path, metrics_token="short"))
    for key in (ACME_KEY, ADMIN_KEY, DASH_KEY):
        with pytest.raises(ValueError, match="RATCHET_METRICS_TOKEN is also the key"):
            gw.Runtime(_live(tmp_path, metrics_token=key))


@pytest.mark.req("FR-12.4", "INV-13")
def test_public_live_metrics_scrub_client_labels(gw_env, tmp_path, monkeypatch):
    gw, rt = _live_gateway(gw_env, monkeypatch, _live(tmp_path, public_dashboard=True))
    c = TestClient(gw.app)
    assert c.post(CHAT, json={"messages": _ask()}, headers=GLOBEX).status_code == 200
    anonymous = c.get("/metrics").text
    # The client label is what is scrubbed. The metrics registry is process-wide, so another test's task named
    # "globex-only" can still be in the text under a `task` label (a task name is public): look at client labels only.
    _, samples = parse_metrics(anonymous)
    clients = {v for _, labels in samples for k, v in labels if k == "client"}
    assert not clients & {"globex", "acme"}, clients
    assert 'client="globex"' not in anonymous and 'client="acme"' not in anonymous
    assert _value(samples, "ratchet_cost_usd_total", client="client-2") > 0  # globex sorts after acme
    keyed = c.get("/metrics", headers={"X-API-Key": DASH_KEY}).text
    assert 'client="globex"' in keyed

    # The request lookup scrubs the same way: the anonymous reader sees the pseudonym, a keyed reader the name.
    over = c.post(CHAT, json={"messages": "x"}, headers=GLOBEX)  # a 400: no requests row, no failures row
    assert over.status_code == 400
    rid = c.post(CHAT, json={"messages": _ask(1)}, headers=GLOBEX).headers["x-request-id"]
    found = c.get(f"/ratchet/requests/{rid}").json()
    assert found["request"]["client"] == "client-2" and "globex" not in json.dumps(found)
    assert c.get(f"/ratchet/requests/{rid}", headers={"X-API-Key": DASH_KEY}).json()["request"]["client"] == "globex"
    assert c.get(f"/ratchet/requests/{over.headers['x-request-id']}").status_code == 404


@pytest.mark.req("FR-12.4")
def test_scrape_time_gauges(gw):
    led = gw.rt.ledger
    live = _version(led, "live", {"threshold": 0.5})
    canary = _version(led, "canary", {"threshold": 0.5})
    gw.rt.ratchet.handle(_demo(gw), TRIAGE)
    c = TestClient(gw.app)
    assert c.post("/ratchet/demo/student-outage", params={"seconds": 30}).status_code == 200
    for _ in range(6):  # the student fails, then its breaker opens
        gw.rt.ratchet.handle(_demo(gw), TRIAGE)
    _, samples = parse_metrics(c.get("/metrics").text)
    assert _value(samples, "ratchet_version_traffic_pct", version=f"v{live}", state="live") == 100
    assert _value(samples, "ratchet_version_traffic_pct", version=f"v{canary}", state="canary") == 10
    assert _value(samples, "ratchet_circuit_open", breaker="student") == 1
    assert _value(samples, "ratchet_circuit_open", breaker="teacher") == 0
    assert _value(samples, "ratchet_spend_today_usd", client="none") > 0


@pytest.mark.req("FR-12.4")
def test_metrics_module_format():
    reg = metrics.Registry()
    counter = reg.counter("t_total", "A help\\text\nwith a newline.", ("name",))
    gauge = reg.gauge("t_gauge", "g", ("k",))
    hist = reg.histogram("t_seconds", "h", ("k",), buckets=(0.1, 1.0))
    counter.inc('quote" back\\slash\nnewline')
    counter.inc("a", by=2.5)
    with pytest.raises(ValueError):
        counter.inc("a", by=-1)
    with pytest.raises(ValueError):
        counter.inc()  # one label value is required
    with pytest.raises(ValueError):
        reg.counter("t_total", "again")
    with pytest.raises(ValueError):
        metrics.Histogram("bad", "b", (), buckets=(1.0, 0.5))
    gauge.set(3, "x")
    gauge.replace([(("y",), 1.0)])  # x is dropped
    for v in (0.05, 0.5, 5.0, float("nan")):
        hist.observe(v, "k1")
    text = reg.render()
    assert "# HELP t_total A help\\\\text\\nwith a newline." in text
    types, samples = parse_metrics(text)
    assert types == {"t_total": "counter", "t_gauge": "gauge", "t_seconds": "histogram"}
    assert _value(samples, "t_total", name='quote" back\\slash\nnewline') == 1 and _value(samples, "t_total", name="a") == 2.5
    assert ("t_gauge", (("k", "x"),)) not in samples and _value(samples, "t_gauge", k="y") == 1
    assert [_value(samples, "t_seconds_bucket", k="k1", le=le) for le in ("0.1", "1.0", "+Inf")] == [1, 2, 3]
    assert _value(samples, "t_seconds_count", k="k1") == 3 and _value(samples, "t_seconds_sum", k="k1") == pytest.approx(5.55)
    assert [metrics.number(x) for x in (3.0, 0.25, float("inf"), float("-inf"), float("nan"))] == [
        "3",
        "0.25",
        "+Inf",
        "-Inf",
        "NaN",
    ]
    merged = reg.render({"name": lambda v: "same"})  # relabelled series that collide are added together
    assert parse_metrics(merged)[1][("t_total", (("name", "same"),))] == 3.5
    metrics.set_clients(["acme"])
    try:
        assert [metrics.client_label(c) for c in ("acme", None, "", "stranger")] == ["acme", "none", "none", "other"]
    finally:
        metrics.set_clients([])


# ---------- the ledger ----------
@pytest.mark.req("FR-12.4")
def test_a_failure_never_starts_the_live_ledgers_day_one(tmp_path):
    """A scanner's 401 (or a teacher down during bring-up) before the first request must not become day 1's origin:
    `Ledger.__init__` rebuilds the origin from `requests` alone, so a restart would move it back and day numbers would go
    backwards. Once requests have started the clock, a failure is filed under the day they count."""
    path, t, hour = str(tmp_path / "live.db"), 1_000_000.0, 3600.0

    def request(led: Ledger, ts: float) -> None:
        led.log_request(ts=ts, task=TRIAGE, served_by="teacher", cost_usd=0.0, baseline_cost_usd=0.0)

    led = Ledger(path, auto_day=True)
    led.log_failure(ts=t, stage="auth", error="HTTPException 401", status=401)
    assert led._t0 is None and led.day == 0
    request(led, t + 20 * hour)  # day 1 starts here, not at the 401
    request(led, t + 30 * hour)
    led.log_failure(ts=t + 50 * hour, stage="teacher", error="UpstreamError 502", status=502)
    assert led.day_start(1) == t + 20 * hour
    led.close()

    again = Ledger(path, auto_day=True)
    assert again.day_start(1) == t + 20 * hour  # the same origin after a restart
    request(again, t + 31 * hour)
    assert [r["day"] for r in _rows(again, "SELECT day FROM requests ORDER BY id")] == [1, 1, 1]
    assert [r["day"] for r in _rows(again, "SELECT day FROM failures ORDER BY id")] == [0, 2]
    assert again.day == 1


@pytest.mark.req("FR-1.60", "FR-12.4")
def test_old_ledger_gains_request_ids_and_the_failures_table(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT);")
    con.close()
    led = Ledger(str(db))
    cols = {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
    assert {"request_id", "client_request_id"} <= cols
    assert {r["name"] for r in led.db.execute("PRAGMA table_info(failures)")} >= {"stage", "error", "status", "cost_usd"}
    plan = " ".join(r[3] for r in led.db.execute("EXPLAIN QUERY PLAN SELECT * FROM requests WHERE request_id='x'"))
    assert "requests_request_id" in plan
    assert set(FAILURE_STAGES) == {
        "teacher",
        "student",
        "audit",
        "shadow",
        "quota",
        "auth",
        "budget",
        "rate",
        "upstream",
        "gateway",
    }

    led = Ledger(":memory:")  # today's spend per client: answered and failed requests together
    led.log_request(ts=1.0, task="t", served_by="teacher", cost_usd=0.5, baseline_cost_usd=1.0, client="acme")
    led.log_failure(ts=2.0, stage="teacher", error="x", status=502, cost_usd=0.25, client="acme")
    led.log_failure(ts=2.0, stage="auth", error="x", status=401)
    assert led.spend_since(0.0, ["acme", None, "acme"]) == {"acme": 0.75, None: 0.0}
    assert led.spend_since(1.5, ["acme"]) == {"acme": 0.25}
