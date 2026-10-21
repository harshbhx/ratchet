"""OpenAI compatibility of `/v1/chat/completions`: strict request validation, message normalisation, the error envelope
OpenAI SDKs read, the body and input limits, and the rule that a client mistake is never a 500 (ratchet/openai_compat.py,
the gateway's error handlers and `BodyLimitMiddleware`; docs/API.md#errors, ADR 0012); and the response's parity with
OpenAI's: `usage`, `system_fingerprint`, the full choice and `stream_options.include_usage` (FR-1.12, ADR 0018).

Gateway tests reload the mock gateway with `PINNED_ENV`, so variables in the shell cannot move a result. The SDK tests
drive the real `openai` package through `tests/sdk_bridge.py` and are skipped when it is not installed.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from typing import Any

import anyio
import httpx
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, ToolCallingServer, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

from ratchet import tasks
from ratchet.confidence import score
from ratchet.config import Settings
from ratchet.ledger import example_hash
from ratchet.openai_compat import (
    APIError,
    ChatRequest,
    Limits,
    from_validation_errors,
    http_error,
    normalize_messages,
    parse_body,
    parse_modalities,
    validate_chat_request,
)
from ratchet.providers import DEFAULT_TEACHER_PARAMS, OpenAICompatProvider, approx_tokens
from ratchet.router import fingerprint
from ratchet.scout.prompts import WRITE_SYSTEM, plan_messages
from sim import research, tickets

CHAT = "/v1/chat/completions"
KEY = "compat-test-key-0123456789abcdef"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
HI = [{"role": "user", "content": "hi"}]
LIMIT = 2 * 1024 * 1024  # the default RATCHET_MAX_BODY_BYTES
PINNED_ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_REDACT_PII": "1",
    "RATCHET_BOM_SIGNING_KEY": "",
    "RATCHET_MAX_BODY_BYTES": str(LIMIT),
    "RATCHET_MAX_MESSAGES": "256",
    "RATCHET_MAX_INPUT_CHARS": "120000",
    "RATCHET_MAX_N": "4",
    "RATCHET_MERGE_SYSTEM": "0",
    "RATCHET_TEACHER_MODALITIES": "text",
    "RATCHET_TEACHER_PARAMS": "",
    "RATCHET_STUDENT_PARAMS": "",
    "RATCHET_TEACHER_MAX_TOKENS": "8192",
    "RATCHET_STUDENT_MAX_TOKENS": "1024",
    "RATCHET_TEACHER_REASONING_HEADROOM": "0",
}


def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**PINNED_ENV, **env})


def _nest(depth: int) -> dict[str, Any]:
    """An object nested `depth` levels deep: {"x": {"x": ... {}}}."""
    value: dict[str, Any] = {}
    for _ in range(depth - 1):
        value = {"x": value}
    return value


def _error(resp: Any) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == {"error", "detail", "request_id"}, body
    assert body["request_id"] == resp.headers["x-request-id"]  # the request's id beside OpenAI's error (ADR 0062)
    assert set(body["error"]) == {"message", "type", "param", "code"}
    assert body["detail"] == body["error"]["message"]  # kept for one release, for {"detail"} clients
    return body["error"]


# ---------- the gateway refuses malformed requests with a 400 or 413 envelope ----------
BAD_BODIES = [
    pytest.param({"messages": ["hi"]}, 400, "invalid_type", "messages[0]", id="message-not-an-object"),
    pytest.param({"messages": HI, "metadata": "x"}, 400, "invalid_type", "metadata", id="metadata-not-an-object"),
    pytest.param({"messages": [{"content": "hi"}]}, 400, "missing_required_parameter", "messages[0].role", id="missing-role"),
    pytest.param({"messages": [{"role": "robot", "content": "hi"}]}, 400, "invalid_value", "messages[0].role", id="role-robot"),
    pytest.param({"messages": HI, "temperature": 3}, 400, "invalid_value", "temperature", id="temperature-3"),
    pytest.param({"messages": HI, "n": 0}, 400, "invalid_value", "n", id="n-0"),
    pytest.param({"messages": HI, "n": 5}, 400, "unsupported_value", "n", id="n-5"),  # RATCHET_MAX_N is 4
    pytest.param({"messages": HI, "stop": ["a", "b", "c", "d", "e"]}, 400, "invalid_value", "stop", id="five-stops"),
    pytest.param({"messages": HI, "top_logprobs": 2}, 400, "invalid_value", "top_logprobs", id="top-logprobs-alone"),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}}},
        400,
        "missing_required_parameter",
        "response_format.json_schema.name",
        id="json-schema-without-name",
    ),
    pytest.param(b'{"messages": [', 400, "invalid_json", None, id="invalid-json"),
    pytest.param(b'{"messages": [{"role": "user", "content": "hi"}], "temperature": NaN}', 400, "invalid_json", None, id="nan"),
    pytest.param(b"[" * 100_000, 400, "invalid_json", None, id="nested-too-deep"),
    pytest.param(b"\xff\xfe\xfd", 400, "invalid_json", None, id="not-utf8"),
    pytest.param(
        b'{"messages": [{"role": "user", "content": "hi \\udc00"}]}',
        400,
        "invalid_json",
        "messages[0].content",
        id="unpaired-surrogate-escape",
    ),
    pytest.param(
        b'{"messages": [{"role": "user", "content": "hi \xed\xa0\x80"}]}',
        400,
        "invalid_json",
        "messages[0].content",
        id="surrogate-as-utf8-bytes",
    ),
    pytest.param(b"", 400, "invalid_json", None, id="empty-body"),
    pytest.param(b"[]", 400, "invalid_type", None, id="body-not-an-object"),
    pytest.param(b"{" + b" " * LIMIT + b"}", 413, "request_too_large", None, id="over-the-size-limit"),
    pytest.param(
        {"messages": [{"role": "user", "content": "x" * 120_001}]},
        400,
        "context_length_exceeded",
        "messages",
        id="120001-chars",
    ),
    pytest.param(
        {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "x"}}]}
            ]
        },
        400,
        "unsupported_content_type",
        "messages[0].content[1].type",
        id="image-part",
    ),
    pytest.param(
        {"messages": HI, "stream": True, "temperature": 3}, 400, "invalid_value", "temperature", id="stream-refused-as-json"
    ),
]


@pytest.mark.req("FR-1.5", "FR-1.10")
@pytest.mark.parametrize(("body", "status", "code", "param"), BAD_BODIES)
def test_bad_request_gets_an_openai_error(gw_env, body, status, code, param):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    if isinstance(body, bytes):
        r = c.post(CHAT, content=body, headers={"content-type": "application/json"})
    else:
        r = c.post(CHAT, json=body)
    assert r.status_code == status, r.text[:300]
    assert r.headers["content-type"].startswith("application/json")  # never a stream, even with stream: true
    err = _error(r)
    assert (err["type"], err["code"], err["param"]) == ("invalid_request_error", code, param)
    assert gw.rt.ledger.totals()["requests"] == 0 and gw.rt.gateway_errors == 0  # refused before the cascade


def _mutations(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], Any]]:
    """Every (path, replacement) that swaps one value in `value` for junk, plus every deleted key (replacement DELETE)."""
    junk = [None, True, -1, 1.5, "x", [], {}, [None]]
    out = [(path, j) for j in junk]
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(((*path, k), DELETE))
            out += _mutations(v, (*path, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _mutations(v, (*path, i))
    return out


DELETE = object()


def _apply(body: Any, path: tuple[Any, ...], new: Any) -> Any:
    if not path:
        return new
    body = json.loads(json.dumps(body))
    *parent, last = path
    node = body
    for p in parent:
        node = node[p]
    if new is DELETE:
        del node[last]
    else:
        node[last] = new
    return body


# A request that uses every field Ratchet validates; the sweep below swaps each of its values for junk.
FULL_REQUEST: dict[str, Any] = {
    "model": "ratchet",
    "messages": [
        {"role": "developer", "content": "be brief"},
        {"role": "user", "content": [{"type": "text", "text": "hi"}], "name": "u"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "42"},
    ],
    "temperature": 0.5,
    "top_p": 1,
    "n": 1,
    "max_tokens": 10,
    "stop": ["x"],
    "seed": 1,
    "logit_bias": {"1": 2},
    "logprobs": True,
    "top_logprobs": 1,
    "metadata": {"ratchet_task": "ticket-triage"},
    "response_format": {"type": "json_schema", "json_schema": {"name": "t", "schema": {"type": "object"}}},
    "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
    "tool_choice": {"type": "function", "function": {"name": "f"}},
    "stream": False,
    "stream_options": {"include_usage": True},
    "user": "u-1",
}


@pytest.mark.req("FR-1.10")
def test_no_client_mistake_gives_500(gw_env, tmp_path, monkeypatch):
    """Swap every value of a request that uses every field for junk, or delete it, and let validation judge it first.
    One it refuses gets exactly that 400 (code and param) from the gateway; one it accepts is answered with a 200 in
    live mode (the teacher behind MockTransport) and in mock mode (the generic mock world answers any request, FR-9.7).
    Never a 500, and a refused request is never logged."""
    gw = _gateway(gw_env)
    mock = gw.rt
    live = gw.Runtime(
        Settings(
            mode="live",
            db_path=str(tmp_path / "live.db"),
            student_base="",
            teacher_api_key="tf-key",  # live mode needs a teacher key to start (ADR 0070)
            audit_rate=0.0,
            allow_unauthenticated=True,
            api_guard="guarded",
            admin_token="0123456789abcdef0123456789abcdef",
            ip_rpm=0,  # every mutation comes from one address: no per-IP limit (ADR 0063)
        ),
        transports={"teacher": FakeOpenAIServer(A).transport},
    )
    c = TestClient(gw.app, raise_server_exceptions=False)
    mutations = _mutations(FULL_REQUEST)
    accepted = 0
    for path, new in mutations:
        raw = json.dumps(_apply(FULL_REQUEST, path, new))
        try:
            normalize_messages(validate_chat_request(parse_body(raw)).messages)
            expected = None
        except APIError as e:
            expected = (e.code, e.param)
        monkeypatch.setattr(gw, "rt", live)
        r = c.post(CHAT, content=raw, headers={"content-type": "application/json"})
        if expected is not None:  # the same refusal in either mode, since validation runs before the cascade
            assert r.status_code == 400, (path, new, r.status_code, r.text[:200])
            err = _error(r)
            assert (err["type"], err["code"], err["param"]) == ("invalid_request_error", *expected), (path, new)
            continue
        accepted += 1
        assert r.status_code == 200, (path, new, r.status_code, r.text[:200])
        monkeypatch.setattr(gw, "rt", mock)
        r = c.post(CHAT, content=raw, headers={"content-type": "application/json"})
        assert r.status_code == 200, (path, new, r.status_code, r.text[:200])
    assert 0 < accepted < len(mutations) // 2  # both branches run; most junk is refused
    assert (mock.gateway_errors, live.gateway_errors) == (0, 0)
    assert mock.ledger.totals()["requests"] == live.ledger.totals()["requests"] == accepted


@pytest.mark.req("FR-1.10")
def test_unpaired_surrogate_is_a_400_in_both_modes(gw_env, tmp_path, monkeypatch):
    """A string Python's JSON decoder accepts but UTF-8 cannot hold (an emoji cut in half, as JavaScript's slice() can
    leave it) is refused before anything is sent or stored. A valid escaped pair is an ordinary character."""
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    system, user = gw.rt.stream.sample(1).messages()  # a demo ticket: the mock would answer and store it

    def post(messages: Any) -> Any:  # json.dumps escapes a surrogate as \udXXX, as JSON.stringify does
        return c.post(CHAT, content=json.dumps({"messages": messages}), headers={"content-type": "application/json"})

    cases = [
        ([{**system, "content": system["content"] + "\ud800"}, user], "messages[0].content"),
        ([system, {**user, "name": "\ud83d"}], "messages[1].name"),
        ([system, user, {"role": "user", "content": [{"type": "text", "text": "a\udfff"}]}], "messages[2].content[0].text"),
        ([system, {**user, "x\udc00": 1}], "messages[1]"),  # in a key: the object's path
    ]
    for messages, param in cases:
        r = post(messages)
        assert r.status_code == 400, r.text[:200]
        err = _error(r)
        assert (err["code"], err["param"]) == ("invalid_json", param) and "unpaired UTF-16 surrogate" in err["message"]
    assert gw.rt.gateway_errors == 0 and gw.rt.ledger.totals()["requests"] == 0

    # Live mode: the teacher is never called (before the fix, encoding its request body raised: a 500).
    teacher = FakeOpenAIServer(A)
    live = gw.Runtime(
        Settings(
            mode="live",
            db_path=str(tmp_path / "live.db"),
            student_base="",
            teacher_api_key="tf-key",  # live mode needs a teacher key to start (ADR 0070)
            audit_rate=0.0,
            allow_unauthenticated=True,
            api_guard="guarded",
            admin_token="0123456789abcdef0123456789abcdef",
        ),
        transports={"teacher": teacher.transport},
    )
    monkeypatch.setattr(gw, "rt", live)
    r = post([{"role": "user", "content": "hi \udc00"}])
    assert r.status_code == 400 and _error(r)["param"] == "messages[0].content" and teacher.calls == []
    r = post([{"role": "user", "content": "hi \ud83d\ude00"}])  # a pair is one character, U+1F600
    assert r.status_code == 200, r.text[:200]
    assert teacher.calls[0].json["messages"] == [{"role": "user", "content": "hi \U0001f600"}]
    assert live.gateway_errors == 0 and live.ledger.totals()["requests"] == 1


# ---------- the envelope on other errors ----------
@pytest.mark.req("FR-1.10")
def test_unknown_v1_path_and_wrong_method(gw_env):
    c = TestClient(_gateway(gw_env).app)
    for method, path in (("POST", "/v1/foo"), ("GET", "/v1/chat/completions/x"), ("GET", "/v1"), ("POST", "/openai/v1/x")):
        r = c.request(method, path)
        err = _error(r)
        assert r.status_code == 404
        assert (err["type"], err["code"], err["message"]) == (
            "not_found_error",
            "unknown_url",
            f"Unknown request URL: {method} {path}.",
        )
    r = c.get(CHAT)
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert _error(r)["type"] == "invalid_request_error"
    # Every other route keeps FastAPI's {"detail": ...}.
    assert c.get("/nope").json() == {"detail": "Not Found"}


@pytest.mark.req("FR-1.10", "INV-7")
def test_unhandled_error_is_a_500_envelope_and_an_event(gw_env, monkeypatch):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret request text")

    monkeypatch.setattr(gw.rt.ratchet, "handle", broken)
    for _ in range(10):
        r = c.post(CHAT, json={"messages": HI})
        assert r.status_code == 500
        err = _error(r)
        assert (err["type"], err["code"], err["param"]) == ("api_error", None, None)
        assert "secret" not in r.text  # the message stays on the server
    events = [e for e in gw.rt.ledger.events() if e["kind"] == "gateway_error"]
    assert [e["detail"] for e in events] == ["RuntimeError (unhandled error 1)", "RuntimeError (unhandled error 10)"]

    # Outside /v1 a crash keeps the server's plain-text 500; it counts (11th), but only powers of ten are written.
    monkeypatch.setattr(gw.rt.ledger, "stats_by_day", broken)
    r = c.get("/ratchet/stats")
    assert (r.status_code, r.text) == (500, "Internal Server Error")
    assert gw.rt.gateway_errors == 11
    assert len([e for e in gw.rt.ledger.events() if e["kind"] == "gateway_error"]) == 2

    # The 100th crash would write an event; if the ledger itself fails, the 500 still goes out.
    gw.rt.gateway_errors = 99
    monkeypatch.setattr(gw.rt.ledger, "event", broken)
    r = c.post(CHAT, json={"messages": HI})
    assert r.status_code == 500 and _error(r)["type"] == "api_error" and gw.rt.gateway_errors == 100


@pytest.mark.req("FR-1.8", "FR-1.10", "FR-9.7")
def test_mock_keyerror_is_400_and_live_keyerror_is_500(gw_env, monkeypatch, tmp_path):
    """The strict mock (RATCHET_MOCK_STRICT=1) knows only demo requests, as v0.4 did: anything else is a 400. Without it
    the generic mock world answers the same requests with a 200."""
    unknown = [
        HI,  # not a demo ticket
        [{"role": "system", "content": "only a system message"}],  # no user message at all
        [{"role": "system", "content": WRITE_SYSTEM}, {"role": "user", "content": "not JSON"}],  # Scout's writer
        [{"role": "system", "content": WRITE_SYSTEM}, {"role": "user", "content": "[" * 100_000}],  # too deep to parse
    ]
    gw = _gateway(gw_env, RATCHET_MOCK_STRICT="1")
    c = TestClient(gw.app, raise_server_exceptions=False)
    for messages in unknown:
        r = c.post(CHAT, json={"messages": messages})
        assert r.status_code == 400 and "mock mode only answers demo requests" in r.json()["detail"], r.text
        assert _error(r)["type"] == "invalid_request_error"
    assert gw.rt.gateway_errors == 0 and gw.rt.ledger.totals()["requests"] == 0
    gw = _gateway(gw_env, RATCHET_MOCK_STRICT="0")
    c = TestClient(gw.app, raise_server_exceptions=False)
    for messages in unknown:
        r = c.post(CHAT, json={"messages": messages})
        assert r.status_code == 200, r.text[:200]
        assert r.json()["choices"][0]["message"]["content"].startswith("[mock teacher] ")
    assert gw.rt.gateway_errors == 0 and gw.rt.ledger.totals()["requests"] == len(unknown)

    # In live mode a KeyError is a bug in the gateway, not a client mistake.
    live = _gateway(
        gw_env,
        RATCHET_MODE="live",
        RATCHET_DB=str(tmp_path / "live.db"),
        RATCHET_STUDENT_BASE="",
        NEBIUS_API_KEY="tf-key",  # live mode needs a teacher key to start (ADR 0070)
        RATCHET_ALLOW_UNAUTHENTICATED="1",
        RATCHET_API_GUARD="guarded",
        RATCHET_ADMIN_TOKEN="0123456789abcdef0123456789abcdef",
    )

    def lookup_bug(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("threshold")

    monkeypatch.setattr(live.rt.ratchet, "handle", lookup_bug)
    r = TestClient(live.app, raise_server_exceptions=False).post(CHAT, json={"messages": HI})
    assert r.status_code == 500 and _error(r)["type"] == "api_error"


@pytest.mark.req("FR-1.10")
def test_framework_validation_errors_become_400_on_v1_only(gw_env):
    gw = _gateway(gw_env)

    @gw.app.get("/v1/_probe")  # the module is reloaded after the test, so the route goes away
    def probe_v1(limit: int) -> dict[str, int]:
        return {"limit": limit}

    @gw.app.get("/ratchet/_probe")
    def probe_other(limit: int) -> dict[str, int]:
        return {"limit": limit}

    c = TestClient(gw.app)
    r = c.get("/v1/_probe", params={"limit": "many"})
    err = _error(r)
    assert r.status_code == 400 and (err["code"], err["param"]) == ("invalid_value", "limit")
    err = _error(c.get("/v1/_probe"))
    assert (err["code"], err["param"]) == ("missing_required_parameter", "limit")
    r = c.get("/ratchet/_probe", params={"limit": "many"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)


@pytest.mark.req("FR-1.10")
def test_ratchet_routes_keep_detail(gw_env):
    c = TestClient(_gateway(gw_env, RATCHET_MAX_BODY_BYTES="1000").app)
    for resp, status in (
        (c.post("/ratchet/redact", json={}), 400),
        (c.post("/ratchet/redact", json={"text": "x" * 900}), 200),
        (c.post("/ratchet/redact", json={"text": "x" * 1001}), 413),
        (c.get("/ratchet/versions/999/bom"), 404),
        (c.post("/ratchet/demo/day?workload=nope"), 400),
        (c.post("/scout/run", content=b"{", headers={"content-type": "application/json"}), 422),
    ):
        assert resp.status_code == status, (resp.request.url, resp.text)
        if status != 200:
            assert "error" not in resp.json() and resp.json()["detail"]


# ---------- order of checks, limits, quota ----------
@pytest.mark.req("FR-1.7", "FR-1.10", "INV-13")
def test_checks_run_in_order_and_refusals_cost_nothing(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    c = TestClient(gw.app)
    key = {"Authorization": f"Bearer {KEY}"}
    msgs = gw.rt.stream.sample(1).messages()
    bad = {"messages": msgs, "temperature": 3}

    r = c.post(CHAT, json=bad)  # no key: 401 before the body is looked at
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert (_error(r)["type"], _error(r)["code"]) == ("invalid_request_error", "invalid_api_key")
    r = c.post(CHAT, json=bad, headers=key)  # a key and a bad body: 400, and the quota is untouched
    assert r.status_code == 400 and _error(r)["param"] == "temperature"
    assert c.post(CHAT, json={"messages": msgs}, headers=key).status_code == 200
    r = c.post(CHAT, json={"messages": msgs}, headers=key)  # quota used up
    err = _error(r)
    assert r.status_code == 429 and (err["type"], err["code"]) == ("insufficient_quota", "daily_quota_exceeded")
    assert r.headers["x-ratelimit-remaining-requests"] == "0" and r.headers["x-should-retry"] == "false"
    assert 0 < int(r.headers["retry-after"]) <= 86400
    assert c.post(CHAT, json=bad, headers=key).status_code == 400  # validation comes before the quota
    assert gw.rt.ledger.totals()["requests"] == 1 and gw.rt.ledger.client_requests_since("acme", 0) == 1


async def _send_chunks(app: Any, path: str, chunks: list[bytes], complete: bool = True) -> tuple[int, bytes, int]:
    """POST `chunks` as a chunked body (no Content-Length) straight into the ASGI app; (status, body, chunks unread).
    With `complete=False` the client disconnects after the last chunk instead of ending the body."""
    pending = [{"type": "http.request", "body": ch, "more_body": True} for ch in chunks]
    if complete:
        pending.append({"type": "http.request", "body": b"", "more_body": False})
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body, len(pending)


@pytest.mark.req("FR-1.10")
def test_body_limit_declared_and_streamed(gw_env):
    gw = _gateway(gw_env, RATCHET_MAX_BODY_BYTES="1000")
    c = TestClient(gw.app)
    r = c.post(CHAT, content=b" " * 1001, headers={"content-type": "application/json"})  # Content-Length says too big
    assert r.status_code == 413 and _error(r)["code"] == "request_too_large"

    # A chunked body is counted as it arrives and refused at the chunk that passes the limit; the rest is never read.
    status, body, unread = anyio.run(_send_chunks, gw.app, CHAT, [b" " * 400] * 10)
    assert status == 413 and json.loads(body)["error"]["code"] == "request_too_large"
    assert unread == 8  # chunks 1-3 read (1,200 bytes > 1,000), 7 chunks and the end marker left
    status, body, _ = anyio.run(_send_chunks, gw.app, "/ratchet/redact", [b" " * 600] * 2)
    assert status == 413 and set(json.loads(body)) == {"detail"}
    # Under the limit, chunked or not, a request goes through.
    small = json.dumps({"messages": gw.rt.stream.sample(1).messages()}).encode()
    assert len(small) < 1000
    status, body, _ = anyio.run(_send_chunks, gw.app, CHAT, [small[:100], small[100:]])
    assert status == 200 and json.loads(body)["object"] == "chat.completion"
    assert gw.rt.ledger.totals()["requests"] == 1

    # A client that disconnects halfway through its body gets a 400 (if it still listens), not a 500.
    status, body, _ = anyio.run(_send_chunks, gw.app, CHAT, [small[:100]], False)
    assert status == 400 and json.loads(body)["error"]["type"] == "invalid_request_error"
    assert gw.rt.gateway_errors == 0 and gw.rt.ledger.totals()["requests"] == 1
    assert gw._content_length([(b"content-length", b"12")]) == 12 and gw._content_length([(b"content-length", b"x")]) is None

    # 0 turns the limit off.
    off = TestClient(_gateway(gw_env, RATCHET_MAX_BODY_BYTES="0").app)
    assert off.post("/ratchet/redact", json={"text": "x" * 4000, "pad": "y" * 3_000_000}).status_code == 200


@pytest.mark.req("FR-1.10")
def test_message_and_input_limits_come_from_settings(gw_env):
    gw = _gateway(gw_env, RATCHET_MAX_MESSAGES="3", RATCHET_MAX_INPUT_CHARS="50")
    c = TestClient(gw.app)
    r = c.post(CHAT, json={"messages": HI * 4})
    assert r.status_code == 400 and (_error(r)["code"], _error(r)["param"]) == ("invalid_value", "messages")
    parts = [{"type": "text", "text": "x" * 26}, {"type": "text", "text": "y" * 25}]  # 51 characters in parts
    r = c.post(CHAT, json={"messages": [{"role": "user", "content": parts}]})
    assert r.status_code == 400 and _error(r)["code"] == "context_length_exceeded"
    # 0 means no limit.
    unlimited = TestClient(_gateway(gw_env, RATCHET_MAX_MESSAGES="0", RATCHET_MAX_INPUT_CHARS="0").app)
    r = unlimited.post(CHAT, json={"messages": HI * 300 + [{"role": "user", "content": "x" * 200_000}]})
    assert r.status_code == 200  # validated, then answered by the generic mock world


# ---------- normalisation through the gateway ----------
@pytest.mark.req("FR-1.10", "US-1")
def test_developer_role_reaches_the_teacher_as_system(gw_env):
    gw = _gateway(gw_env)
    teacher = gw.rt.ratchet.teacher = RecordingProvider(A)
    c = TestClient(gw.app)
    msgs = [{"role": "developer", "content": "Reply with JSON."}, {"role": "system", "content": "Be brief."}, *HI]
    assert c.post(CHAT, json={"messages": msgs}).status_code == 200
    plain = [{"role": "system", "content": "Reply with JSON."}, {"role": "system", "content": "Be brief."}, *HI]
    assert teacher.calls[0].messages == plain
    (example,) = gw.rt.ledger.examples()
    assert example["hash"] == example_hash("default", plain)

    # RATCHET_MERGE_SYSTEM=1: consecutive leading system messages become one, joined by a blank line.
    gw = _gateway(gw_env, RATCHET_MERGE_SYSTEM="1")
    teacher = gw.rt.ratchet.teacher = RecordingProvider(A)
    assert TestClient(gw.app).post(CHAT, json={"messages": msgs}).status_code == 200
    assert teacher.calls[0].messages == [{"role": "system", "content": "Reply with JSON.\n\nBe brief."}, *HI]


@pytest.mark.req("FR-1.10", "INV-12", "FR-1.41")
def test_dropped_keys_reach_neither_the_teacher_nor_the_ledger(gw_env):
    """Keys a message's role does not use reach neither the teacher nor the ledger. A conversation that carries tool
    calls and results is a tool request (FR-1.41): its tool call's arguments reach the teacher as sent, and nothing of
    it is stored (redact_messages covers stored tool calls: tests/test_tools.py)."""
    gw = _gateway(gw_env)
    teacher = gw.rt.ratchet.teacher = RecordingProvider(A)
    call = {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": '{"email": "bob@example.com"}'}}
    msgs = [
        {"role": "user", "content": "Find my order.", "vendor_note": "call me on +44 20 7946 0958"},
        {"role": "assistant", "content": None, "tool_calls": [{**call, "index": 0}], "annotations": []},
        {"role": "tool", "tool_call_id": "c1", "content": "Order 7 ships today."},
    ]
    r = TestClient(gw.app).post(CHAT, json={"messages": msgs})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    sent = [{"role": "user", "content": "Find my order."}, {"role": "assistant", "content": None, "tool_calls": [call]}, msgs[2]]
    assert teacher.calls[0].messages == sent
    assert gw.rt.ledger.examples() == []
    (row,) = [dict(x) for x in gw.rt.ledger.db.execute("SELECT route, stored, uses_tools FROM requests")]
    assert row == {"route": "tools", "stored": 0, "uses_tools": 1}


@pytest.mark.req("FR-1.1", "FR-1.10", "US-1")
def test_text_parts_ticket_matches_the_string_form(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    plain = c.post(CHAT, json={"messages": msgs, "metadata": {"ratchet_task": "ticket-triage"}})
    system, user = msgs
    parts = [
        {**system, "content": [{"type": "text", "text": system["content"]}]},
        {**user, "content": [{"type": "text", "text": user["content"]}]},
    ]
    r = c.post(CHAT, json={"messages": parts, "metadata": {"ratchet_task": "ticket-triage"}})
    assert plain.status_code == r.status_code == 200
    assert r.json()["choices"][0]["message"] == plain.json()["choices"][0]["message"]
    (example,) = gw.rt.ledger.examples()  # one example, seen twice, under the hash of the string form
    assert example["seen"] == 2 and example["hash"] == example_hash("ticket-triage", msgs)
    assert gw.rt.ledger.totals()["requests"] == 2


@pytest.mark.req("FR-1.10")
def test_multimodal_parts_go_to_the_teacher_only(gw_env):
    """With RATCHET_TEACHER_MODALITIES=text,image_url an image request skips the student and is never stored."""
    gw = _gateway(gw_env, RATCHET_TEACHER_MODALITIES="text,image_url")
    led = gw.rt.ledger
    _version(led, "live", {"threshold": 0.0})
    student, teacher = RecordingProvider(A), RecordingProvider(A)
    gw.rt.ratchet.teacher = teacher
    gw.rt.ratchet.resolve_student = lambda v: (student, v.adapter_uri)
    c = TestClient(gw.app)
    image = {
        "role": "user",
        "content": [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": "x"}}],
    }
    r = c.post(CHAT, json={"messages": [image]}, headers={"X-Ratchet-Task": "ticket-triage"})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert student.calls == [] and teacher.calls[0].messages == [image]  # the parts reach the teacher as sent
    assert led.examples() == []  # no image bytes in the ledger
    (row,) = [dict(x) for x in led.db.execute("SELECT task, served_by, escalated, student_version, route FROM requests")]
    assert row == {
        "task": "ticket-triage",
        "served_by": "teacher",
        "escalated": 0,
        "student_version": None,
        "route": "ineligible:content_type",  # the student reads text only (FR-2.50)
    }
    # The same gateway sends a text request through the cascade as usual: the student answers.
    assert c.post(CHAT, json={"messages": HI}).json()["ratchet"]["served_by"] == "student" and len(student.calls) == 1
    # Audio is still refused. The mock teacher answers the image request from its text part, and still nothing is stored.
    audio = {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "", "format": "wav"}}]}
    assert _error(c.post(CHAT, json={"messages": [audio]}))["code"] == "unsupported_content_type"
    gw.rt.reset()
    r = c.post(CHAT, json={"messages": [image]})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert '"What is this?"' in r.json()["choices"][0]["message"]["content"] and gw.rt.ledger.examples() == []


# ---------- generation parameters reach each model (FR-1.13, ADR 0017) ----------
def _gen_runtime(gw: Any, tmp_path: Any, teacher: FakeOpenAIServer, **overrides: Any) -> tuple[Any, FakeOpenAIServer]:
    """A live Runtime with every generation setting pinned; the teacher and the student behind MockTransport."""
    student = FakeOpenAIServer(A)
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / f"gen{len(list(tmp_path.iterdir()))}.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "tf-teacher-api-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "https://student.test/v1",
        "student_api_key": "sk-student-endpoint-token",
        "samples": 3,
        "audit_rate": 0.0,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "teacher_max_tokens": 8192,
        "student_max_tokens": 1024,
        "teacher_reasoning_headroom": 0,
        "api_keys": "",
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",  # no API keys in live mode (ADR 0061)
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
    }
    rt = gw.Runtime(Settings(**{**base, **overrides}), transports={"teacher": teacher.transport, "student": student.transport})
    return rt, student


def _bodies(server: FakeOpenAIServer) -> list[dict[str, Any]]:
    return [c.json for c in server.calls_to("/chat/completions")]


@pytest.mark.req("FR-1.13", "FR-2.1", "FR-2.5", "INV-1")
def test_generation_parameters_reach_teacher_student_and_audit(gw_env, tmp_path, monkeypatch):
    """The client's max_completion_tokens, stop, seed and temperature, as each call sends them over HTTP: the teacher
    serving an answer gets them all; the student gets the shaping ones under its own sampling; an audit runs at
    temperature 0 under the same limit and stops. A vendor field goes nowhere unless an allowlist names it."""
    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer(A)
    rt, student = _gen_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    vid = _version(rt.ledger, "live", {"threshold": 0.99})  # unsure of everything: the teacher answers
    c = TestClient(gw.app)
    sent = {"model": "gpt-4o", "max_completion_tokens": 50, "stop": ["\n\n"], "seed": 7, "temperature": 0.9, "top_k": 40}
    r = c.post(CHAT, json={"messages": HI, **sent})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    (sampled,) = _bodies(student)
    assert sampled == {
        "model": "student-v1",
        "messages": HI,
        "n": 3,
        "temperature": 0.7,
        "logprobs": True,
        "max_tokens": 50,
        "stop": ["\n\n"],
        "seed": 7,
        "chat_template_kwargs": {"enable_thinking": False},  # ADR 0130
    }
    # The model is always the configured teacher (INV-1); max_completion_tokens goes up as max_tokens.
    assert _bodies(teacher) == [
        {"model": OPEN_TEACHER, "messages": HI, "n": 1, "temperature": 0.9, "max_tokens": 50, "stop": ["\n\n"], "seed": 7}
    ]

    # A forced audit of a confident student: temperature 0, the same stop and limit, no seed.
    rt.ledger.update_eval(vid, {"threshold": 0.5})
    rt.settings.audit_rate = 1.0
    hello = [{"role": "user", "content": "hello"}]
    r = c.post(CHAT, json={"messages": hello, **sent})
    assert r.json()["ratchet"]["served_by"] == "student" and r.json()["choices"][0]["finish_reason"] == "stop"
    assert rt.ratchet.sidecar.drain(10)  # live mode audits after the response (ADR 0072)
    assert _bodies(teacher)[-1] == {
        "model": OPEN_TEACHER,
        "messages": hello,
        "n": 1,
        "temperature": 0.0,
        "max_tokens": 50,
        "stop": ["\n\n"],
    }

    # An operator who allows the vendor field gets it forwarded to the teacher (and only there).
    teacher = FakeOpenAIServer(A)
    rt, student = _gen_runtime(gw, tmp_path, teacher, teacher_params="all,top_k")
    monkeypatch.setattr(gw, "rt", rt)
    assert c.post(CHAT, json={"messages": HI, **sent}).status_code == 200
    assert _bodies(teacher)[0]["top_k"] == 40 and student.calls == []  # cold start: no student yet


@pytest.mark.req("FR-1.13", "NFR-3")
def test_allowlists_and_token_caps_through_the_gateway(gw_env, tmp_path, monkeypatch):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    sent = {"max_tokens": 10**6, "logit_bias": {"50256": -100}, "presence_penalty": 0.5}

    # Defaults: every standard field to the teacher, capped at 8,192 output tokens; the student at 1,024.
    teacher = FakeOpenAIServer(A)
    rt, student = _gen_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.99})
    assert c.post(CHAT, json={"messages": HI, **sent}).status_code == 200
    (tbody,) = _bodies(teacher)
    (sbody,) = _bodies(student)
    assert (tbody["max_tokens"], tbody["logit_bias"], tbody["presence_penalty"]) == (8192, {"50256": -100}, 0.5)
    assert (sbody["max_tokens"], sbody["logit_bias"]) == (1024, {"50256": -100}) and "presence_penalty" not in sbody

    # RATCHET_TEACHER_PARAMS without logit_bias: not forwarded; RATCHET_STUDENT_PARAMS=none: the cap alone.
    allowed = ",".join(f for f in sorted(DEFAULT_TEACHER_PARAMS) if f != "logit_bias")
    teacher = FakeOpenAIServer(A)
    rt, student = _gen_runtime(gw, tmp_path, teacher, teacher_params=allowed, student_params="none", teacher_max_tokens=0)
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.99})
    assert c.post(CHAT, json={"messages": HI, **sent}).status_code == 200
    (tbody,) = _bodies(teacher)
    (sbody,) = _bodies(student)
    assert "logit_bias" not in tbody and tbody["presence_penalty"] == 0.5
    assert tbody["max_tokens"] == 10**6  # RATCHET_TEACHER_MAX_TOKENS=0: no cap
    assert sbody["max_tokens"] == 1024 and "logit_bias" not in sbody

    # A request without parameters still carries both caps.
    teacher = FakeOpenAIServer(A)
    rt, student = _gen_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.99})
    assert c.post(CHAT, json={"messages": HI}).status_code == 200
    assert (_bodies(teacher)[0]["max_tokens"], _bodies(student)[0]["max_tokens"]) == (8192, 1024)
    assert "stop" not in _bodies(teacher)[0] and "seed" not in _bodies(student)[0]

    # A bad setting stops the gateway at startup.
    with pytest.raises(ValueError, match="RATCHET_TEACHER_PARAMS"):
        _gen_runtime(gw, tmp_path, FakeOpenAIServer(A), teacher_params="metadata")
    with pytest.raises(ValueError, match="RATCHET_STUDENT_MAX_TOKENS"):
        _gen_runtime(gw, tmp_path, FakeOpenAIServer(A), student_max_tokens=-1)


@pytest.mark.req("FR-1.13", "FR-2.13", "FR-1.6")
def test_a_cut_off_teacher_answer_reaches_the_client_as_length(gw_env, tmp_path, monkeypatch):
    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer(A, finish_reason="length")
    rt, _ = _gen_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    r = c.post(CHAT, json={"messages": HI, "max_tokens": 5})
    assert r.json()["choices"][0]["finish_reason"] == "length" and rt.ledger.examples() == []
    with c.stream("POST", CHAT, json={"messages": HI, "max_tokens": 5, "stream": True}) as resp:
        chunks = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: {")]
    assert [ch["choices"][0]["finish_reason"] for ch in chunks][-1] == "length"
    assert all(ch["choices"][0]["finish_reason"] is None for ch in chunks[:-1])
    teacher.finish_reason = "eos"  # a value OpenAI clients do not know is reported as stop
    assert c.post(CHAT, json={"messages": HI}).json()["choices"][0]["finish_reason"] == "stop"


WEATHER = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]


@pytest.mark.req("FR-1.13", "FR-1.1", "FR-1.6", "FR-1.41")
def test_finish_reason_is_tool_calls_only_with_tool_calls(gw_env, tmp_path, monkeypatch):
    """A response says `finish_reason: "tool_calls"` only when its message has `tool_calls`: the OpenAI SDK reads that
    finish as "the calls are in the message", and the usual agent loop over `message.tool_calls` (None) raises. A
    request with tools goes to the teacher alone, tools included (FR-1.41); a teacher that answers it in prose, or says
    `tool_calls` but sends none, is reported as `stop`, streamed or not. A request without tools whose teacher answers
    with a tool call anyway is a 502 `tool_calls_unsupported`, with nothing stored."""
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    ask = {"messages": [{"role": "user", "content": "Weather in Paris?"}], "tools": WEATHER, "tool_choice": "auto"}

    def claims_calls_it_lacks(body: dict[str, Any]) -> bool:
        return any(ch["finish_reason"] == "tool_calls" and not ch["message"].get("tool_calls") for ch in body["choices"])

    teacher = FakeOpenAIServer("I cannot look up the weather.")
    rt, student = _gen_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.0})  # the student would serve anything it is asked
    r = c.post(CHAT, json=ask)
    assert r.status_code == 200 and r.json()["choices"][0]["finish_reason"] == "stop" and not claims_calls_it_lacks(r.json())
    assert r.json()["ratchet"]["served_by"] == "teacher" and _bodies(student) == [] and rt.ledger.examples() == []
    assert (_bodies(teacher)[0]["tools"], _bodies(teacher)[0]["tool_choice"]) == (WEATHER, "auto")

    teacher = FakeOpenAIServer("", finish_reason="tool_calls")  # says tool_calls, sends none
    rt, _ = _gen_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    r = c.post(CHAT, json=ask)
    assert r.status_code == 200 and r.json()["choices"][0]["finish_reason"] == "stop" and not claims_calls_it_lacks(r.json())
    finishes = [ch["finish_reason"] for e in _stream(c, ask) for ch in e["choices"] if ch["finish_reason"]]
    assert finishes == ["stop"]

    call = {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}
    for teacher_params in ("", "all"):
        teacher = ToolCallingServer([call])
        rt, _ = _gen_runtime(gw, tmp_path, teacher, teacher_params=teacher_params)
        monkeypatch.setattr(gw, "rt", rt)
        r = c.post(CHAT, json={"messages": [{"role": "user", "content": "Weather in Paris?"}]})  # no tools: the cascade
        assert r.status_code == 502 and _error(r)["code"] == "tool_calls_unsupported"
        assert r.headers["x-should-retry"] == "false" and rt.ledger.examples() == []
        with c.stream("POST", CHAT, json={"messages": HI, "stream": True}) as resp:
            assert resp.status_code == 502
        r = c.post(CHAT, json=ask)  # the same answer to a request with tools is the answer
        assert r.json()["choices"][0]["message"]["tool_calls"] == [call]
        assert r.json()["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.req("FR-1.13", "FR-2.13", "FR-9.2")
def test_mock_models_honour_max_tokens_and_stop(gw_env):
    """In mock mode the mock teacher stops at a stop string and cuts at max_tokens (about 4 characters a token): a cut
    answer is `length` and is not stored; an answer that ended at a stop is stored with that stop as its condition."""
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    full = c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "default"}).json()
    answer = full["choices"][0]["message"]["content"]
    assert full["choices"][0]["finish_reason"] == "stop" and len(gw.rt.ledger.examples()) == 1
    gw.rt.reset()

    r = c.post(CHAT, json={"messages": msgs, "max_completion_tokens": 3}, headers={"X-Ratchet-Task": "default"}).json()
    assert (r["choices"][0]["message"]["content"], r["choices"][0]["finish_reason"]) == (answer[:12], "length")
    assert gw.rt.ledger.examples() == []

    r = c.post(CHAT, json={"messages": msgs, "stop": ', "priority"'}, headers={"X-Ratchet-Task": "default"}).json()
    assert (r["choices"][0]["message"]["content"], r["choices"][0]["finish_reason"]) == (answer.split(', "priority"')[0], "stop")
    (ex,) = gw.rt.ledger.examples()
    assert json.loads(ex["params"]) == {"stop": [', "priority"']} and ex["completion"] == r["choices"][0]["message"]["content"]

    # The real OpenAI SDK reads the cut-off answer's finish_reason.
    sdk()
    client = make_openai_client(gw.app)
    resp = client.chat.completions.create(model="ratchet", messages=msgs, max_tokens=3)
    assert resp.choices[0].finish_reason == "length"


# ---------- the OpenAI SDK ----------
@pytest.mark.req("FR-1.1", "FR-1.10", "US-1")
def test_openai_sdk_round_trip_and_errors(gw_env):
    openai, _ = sdk()
    gw = _gateway(gw_env)
    client = make_openai_client(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    resp = client.chat.completions.create(model="ratchet", messages=msgs, extra_headers={"X-Ratchet-Task": "ticket-triage"})
    assert resp.object == "chat.completion" and json.loads(resp.choices[0].message.content)["category"]
    assert resp.model_extra["ratchet"]["served_by"] in ("student", "teacher")
    # Text parts and the developer role, as newer SDK code sends them, give the same answer.
    parts = [
        {"role": "developer", "content": msgs[0]["content"]},
        {"role": "user", "content": [{"type": "text", "text": msgs[1]["content"]}]},
    ]
    again = client.chat.completions.create(model="ratchet", messages=parts, extra_headers={"X-Ratchet-Task": "ticket-triage"})
    assert again.choices[0].message.content == resp.choices[0].message.content
    # Errors arrive as typed exceptions with code, param and type.
    with pytest.raises(openai.BadRequestError) as bad:
        client.chat.completions.create(model="ratchet", messages=msgs, temperature=3)
    assert (bad.value.status_code, bad.value.code, bad.value.param, bad.value.type) == (
        400,
        "invalid_value",
        "temperature",
        "invalid_request_error",
    )
    with pytest.raises(openai.BadRequestError) as bad:
        client.chat.completions.create(model="ratchet", messages=[{"role": "user", "content": "x" * 120_001}])
    assert (bad.value.code, bad.value.param) == ("context_length_exceeded", "messages")


@pytest.mark.req("FR-1.7", "FR-1.10")
def test_openai_sdk_auth_and_quota_errors(gw_env):
    openai, _ = sdk()
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}", RATCHET_API_KEY_DAILY_REQUESTS="1")
    msgs = gw.rt.stream.sample(1).messages()
    with pytest.raises(openai.AuthenticationError) as denied:
        make_openai_client(gw.app, api_key="wrong-key-0123456789").chat.completions.create(model="ratchet", messages=msgs)
    assert (denied.value.code, denied.value.type) == ("invalid_api_key", "invalid_request_error")
    calls: list[Any] = []
    client = make_openai_client(gw.app, api_key=KEY, calls=calls, max_retries=2)  # the SDK's default retry budget
    client.chat.completions.create(model="ratchet", messages=msgs)
    with pytest.raises(openai.RateLimitError) as limited:
        client.chat.completions.create(model="ratchet", messages=msgs)
    assert (limited.value.type, limited.value.code) == ("insufficient_quota", "daily_quota_exceeded")
    assert len(calls) == 2  # X-Should-Retry: false, so the daily quota is not retried


# ---------- response parity: usage, system_fingerprint, the full choice, include_usage (FR-1.12) ----------
FINGERPRINT = re.compile(r"fp_[0-9a-f]{12}")
BODY_KEYS = {"id", "object", "created", "model", "system_fingerprint", "service_tier", "choices", "usage", "ratchet"}
NO_CALL = {"audit": None, "shadow": None, "arbitration": None, "repair": None}


def _usage(prompt: int, completion: int, reasoning: int = 0, cached: int = 0) -> dict[str, Any]:
    """OpenAI's `usage` object, written out in full."""
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": reasoning,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    }


def _stream(c: TestClient, body: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    """The data chunks of a streamed response, parsed; checks that `data: [DONE]` ends it."""
    with c.stream("POST", CHAT, json={**body, "stream": True}, **kwargs) as resp:
        assert resp.status_code == 200, resp.read()[:300]
        lines = [line[6:] for line in resp.iter_lines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]" and "[DONE]" not in lines[:-1]
    return [json.loads(line) for line in lines[:-1]]


@pytest.mark.req("FR-1.1", "FR-1.12", "US-1")
def test_responses_are_openai_chat_completions_with_usage(gw_env, tmp_path, monkeypatch):
    """Student- and teacher-served responses pass the OpenAI SDK's own `ChatCompletion` model with every field an
    OpenAI response has, and `usage` is the served answer's as if its model had answered alone: the student's chosen
    sample, or the teacher's call with its reasoning and cached tokens. Ratchet's other calls are in the breakdown."""
    sdk()
    from openai.types.chat import ChatCompletion

    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer(A, in_tokens=40, out_tokens=320, reasoning_tokens=300, cached_tokens=16)
    rt, _ = _gen_runtime(gw, tmp_path, teacher)  # the student: 10 prompt tokens and 5 per sample, 3 samples
    monkeypatch.setattr(gw, "rt", rt)
    vid = _version(rt.ledger, "live", {"threshold": 0.5})
    c = TestClient(gw.app)

    body = c.post(CHAT, json={"messages": HI}).json()
    ChatCompletion.model_validate(body)
    assert set(body) == BODY_KEYS and (body["object"], body["service_tier"]) == ("chat.completion", "default")
    assert body["model"] == "student-v1" and FINGERPRINT.fullmatch(body["system_fingerprint"])
    message = {"role": "assistant", "content": A, "refusal": None, "annotations": []}
    assert body["choices"] == [{"index": 0, "message": message, "logprobs": None, "finish_reason": "stop"}]
    assert body["usage"] == _usage(10, 5)  # one sample, not the three the cascade drew
    assert body["ratchet"]["served_by"] == "student"
    breakdown = {"student_samples": {"in": 10, "out": 15}, "served": {"in": 10, "out": 5}, **NO_CALL}
    assert body["ratchet"]["usage_breakdown"] == breakdown
    student_fp = body["system_fingerprint"]

    rt.ledger.update_eval(vid, {"threshold": 0.99})  # unsure of everything: the teacher answers
    body = c.post(CHAT, json={"messages": HI}).json()
    ChatCompletion.model_validate(body)
    assert (body["model"], body["ratchet"]["served_by"]) == (OPEN_TEACHER, "teacher")
    assert body["usage"] == _usage(40, 320, reasoning=300, cached=16)  # reasoning counted in completion_tokens
    breakdown = {"student_samples": {"in": 10, "out": 15}, "served": {"in": 40, "out": 320}, **NO_CALL}
    assert body["ratchet"]["usage_breakdown"] == breakdown
    assert FINGERPRINT.fullmatch(body["system_fingerprint"]) and body["system_fingerprint"] != student_fp

    # A teacher answer cut off by its token limit is `length`, and still a valid ChatCompletion.
    teacher.finish_reason = "length"
    cut = c.post(CHAT, json={"messages": HI, "max_tokens": 5}).json()
    assert ChatCompletion.model_validate(cut).choices[0].finish_reason == "length"

    # The real SDK reads the same fields.
    teacher.finish_reason = "stop"
    resp = make_openai_client(gw.app).chat.completions.create(model="ratchet", messages=HI)
    assert (resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens) == (40, 320, 360)
    assert (resp.usage.completion_tokens_details.reasoning_tokens, resp.usage.prompt_tokens_details.cached_tokens) == (300, 16)
    assert (resp.system_fingerprint, resp.service_tier) == (body["system_fingerprint"], "default")
    assert resp.choices[0].message.refusal is None and resp.choices[0].logprobs is None


@pytest.mark.req("FR-1.12", "FR-9.2", "FR-2.9")
def test_mock_teacher_reports_its_reasoning_tokens(gw_env):
    """The mock teacher reports the reasoning tokens it bills (`RATCHET_TEACHER_REASONING_TOKENS`) the way Token
    Factory does, so a mock response's usage has them; a pinned task's response has usage too, from its one call."""
    sdk()
    from openai.types.chat import ChatCompletion

    gw = _gateway(gw_env, RATCHET_TEACHER_REASONING_TOKENS="120")
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    body = c.post(CHAT, json={"messages": msgs}, headers={"X-Ratchet-Task": "ticket-triage"}).json()  # cold start
    ChatCompletion.model_validate(body)
    usage = body["usage"]
    assert usage["completion_tokens_details"]["reasoning_tokens"] == gw.rt.settings.teacher_reasoning_tokens == 120
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"] > 0
    assert usage["completion_tokens"] > 120  # the answer's own tokens, plus the reasoning
    teacher_fp = body["system_fingerprint"]
    assert teacher_fp == fingerprint("teacher", OPEN_TEACHER)

    plan = c.post(
        CHAT, json={"messages": plan_messages("What is LoRA fine-tuning?")}, headers={"X-Ratchet-Task": "research-plan"}
    )
    body = plan.json()
    ChatCompletion.model_validate(body)
    served = {"in": body["usage"]["prompt_tokens"], "out": body["usage"]["completion_tokens"]}
    assert body["ratchet"]["usage_breakdown"] == {"student_samples": None, "served": served, **NO_CALL}
    assert body["usage"]["completion_tokens_details"]["reasoning_tokens"] == 120 and body["system_fingerprint"] == teacher_fp


@pytest.mark.req("FR-1.12")
def test_system_fingerprint_follows_the_serving_configuration(gw_env, tmp_path, monkeypatch):
    """Equal for answers of one version; another when a promotion puts another version live, back to the old one after
    a rollback, another again when the version's threshold changes; the teacher's is its own and the same each time."""
    gw = _gateway(gw_env)
    rt, _ = _gen_runtime(gw, tmp_path, FakeOpenAIServer(A))
    monkeypatch.setattr(gw, "rt", rt)
    led = rt.ledger
    c = TestClient(gw.app)

    def served() -> tuple[str, str]:
        body = c.post(CHAT, json={"messages": HI}).json()
        return body["ratchet"]["served_by"], body["system_fingerprint"]

    v1 = _version(led, "live", {"threshold": 0.5})
    first = served()
    assert first == served() == ("student", fingerprint("student", "student-v1", v1, "dataset-student-v1", 0.5))
    v2 = _version(led, "live", {"threshold": 0.5})  # promoted
    led.set_version(v1, "retired", 0)
    second = served()
    assert second == served() and second[0] == "student" and second[1] != first[1]
    led.set_version(v2, "rolled_back", 0)
    led.set_version(v1, "live", 100)
    assert served() == first
    led.update_eval(v1, {"threshold": 0.6})  # recalibrated: the same adapter serves other answers
    third = served()
    assert third[0] == "student" and third[1] not in (first[1], second[1])
    led.update_eval(v1, {"threshold": 0.99})
    assert served() == served() == ("teacher", fingerprint("teacher", OPEN_TEACHER))
    # Every chunk of a streamed answer carries it too.
    assert {ch["system_fingerprint"] for ch in _stream(c, {"messages": HI})} == {fingerprint("teacher", OPEN_TEACHER)}


@pytest.mark.req("FR-1.6", "FR-1.12", "US-1")
def test_stream_chunks_validate_and_include_usage_ends_with_the_usage(gw_env, tmp_path, monkeypatch):
    """Every chunk passes the SDK's `ChatCompletionChunk` model and carries the fingerprint, the service tier and null
    logprobs. With `stream_options.include_usage`, every chunk has `usage: null` and one more chunk before `[DONE]` has
    no choices, the non-streamed response's usage and the ratchet block; without it the block stays on the finish
    chunk and no chunk has `usage`. Student- and teacher-served."""
    sdk()
    from openai.types.chat import ChatCompletionChunk

    gw = _gateway(gw_env)
    rt, _ = _gen_runtime(gw, tmp_path, FakeOpenAIServer(A, in_tokens=40, out_tokens=320, reasoning_tokens=300))
    monkeypatch.setattr(gw, "rt", rt)
    vid = _version(rt.ledger, "live", {"threshold": 0.5})
    c = TestClient(gw.app)
    for threshold, served_by in ((0.5, "student"), (0.99, "teacher")):
        rt.ledger.update_eval(vid, {"threshold": threshold})
        plain = c.post(CHAT, json={"messages": HI}).json()
        assert plain["ratchet"]["served_by"] == served_by

        chunks = _stream(c, {"messages": HI, "stream_options": {"include_usage": True}})
        for ch in chunks:
            ChatCompletionChunk.model_validate(ch)
        *parts, finish, last = chunks
        assert last["choices"] == [] and last["usage"] == plain["usage"]
        assert last["ratchet"]["served_by"] == served_by
        assert last["ratchet"]["usage_breakdown"] == plain["ratchet"]["usage_breakdown"]
        assert all(ch["usage"] is None and "ratchet" not in ch for ch in (*parts, finish))
        assert (finish["choices"][0]["delta"], finish["choices"][0]["finish_reason"]) == ({}, "stop")
        assert all(ch["choices"][0]["finish_reason"] is None for ch in parts)
        assert all(ch["choices"][0]["logprobs"] is None for ch in (*parts, finish))
        assert parts[0]["choices"][0]["delta"] == {"role": "assistant", "content": "", "refusal": None}
        text = "".join(ch["choices"][0]["delta"].get("content", "") for ch in parts)
        assert text == plain["choices"][0]["message"]["content"]
        envelope = (chunks[0]["id"], chunks[0]["created"], plain["model"], plain["system_fingerprint"], "default")
        assert {(ch["id"], ch["created"], ch["model"], ch["system_fingerprint"], ch["service_tier"]) for ch in chunks} == {
            envelope
        }

        # Without include_usage (absent, false or an empty object): no usage, and the block on the finish chunk.
        for options in ({}, {"stream_options": {"include_usage": False}}, {"stream_options": {}}):
            chunks = _stream(c, {"messages": HI, **options})
            for ch in chunks:
                ChatCompletionChunk.model_validate(ch)
            assert all("usage" not in ch for ch in chunks) and all("ratchet" not in ch for ch in chunks[:-1])
            assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and chunks[-1]["ratchet"]["served_by"] == served_by

    # stream_options without a stream changes nothing.
    again = c.post(CHAT, json={"messages": HI, "stream_options": {"include_usage": True}}).json()
    assert again["object"] == "chat.completion" and again["usage"] == plain["usage"] == _usage(40, 320, reasoning=300)

    # The real SDK: the last chunk has no choices and the usage; the others have none.
    got = list(
        make_openai_client(gw.app).chat.completions.create(
            model="ratchet", messages=HI, stream=True, stream_options={"include_usage": True}
        )
    )
    assert got[-1].choices == [] and got[-1].usage.total_tokens == 360
    assert got[-1].usage.completion_tokens_details.reasoning_tokens == 300
    assert all(ch.usage is None for ch in got[:-1])
    assert "".join(ch.choices[0].delta.content or "" for ch in got[:-1]) == A


@pytest.mark.req("FR-1.12", "FR-1.11")
def test_provider_reads_cached_prompt_tokens():
    """`usage.prompt_tokens_details.cached_tokens` reaches every choice of a call, like the prompt it is part of; a
    malformed value or details object counts as 0."""
    server = FakeOpenAIServer(A, in_tokens=90, out_tokens=4, cached_tokens=64)
    p = OpenAICompatProvider("https://student.test/v1", "k", transport=server.transport)
    assert [(x.in_tokens, x.cached_tokens) for x in p.complete(HI, "m", n=2)] == [(90, 64), (90, 64)]
    for details in ({"cached_tokens": "64"}, {"cached_tokens": -1}, {"cached_tokens": True}, [64], None):
        usage = {"prompt_tokens": 90, "completion_tokens": 4, "prompt_tokens_details": details}
        body = {"choices": [{"message": {"content": "a"}}], "usage": usage}
        bad = OpenAICompatProvider(
            "https://m.test/v1", "k", transport=httpx.MockTransport(lambda r, b=body: httpx.Response(200, json=b))
        )
        assert bad.complete(HI, "m")[0].cached_tokens == 0, details


# ---------- n choices and token logprobs (FR-1.14, ADR 0020) ----------
def _trained_mock(gw: Any) -> tuple[Any, list[Any]]:
    """The mock gateway with a live student trained on 40 demo tickets (the mock fleet's own trainer), and those tickets.
    The mock student knows the exact texts it trained on, so it serves most of them with every sample agreeing."""
    rt = gw.rt
    items = [rt.stream.sample(1) for _ in range(40)]
    rows = [{"messages": [*it.messages(), {"role": "assistant", "content": it.answer(it.labels)}]} for it in items]
    rt.fleet.train(rows, rt.settings.student_base_model, "student-v1")
    _version(rt.ledger, "live", {"threshold": 0.5})
    return rt, items


def _labels(content: str) -> tuple[Any, Any]:
    obj = json.loads(content)
    return obj["category"], obj["priority"]


def _spelled(logprobs: dict[str, Any]) -> str:
    return "".join(t["token"] for t in logprobs["content"])


@pytest.mark.req("FR-1.14", "FR-1.12", "FR-1.6", "US-1")
def test_n_choices_from_a_trained_mock_student(gw_env):
    """n=2 from a trained mock student: two choices with the same labels, `usage.completion_tokens` their sum, a valid
    `ChatCompletion`; with logprobs, each choice's tokens spell its content and their mean is the token probability the
    confidence score saw; streamed, each choice's chunks with its own finish chunk; and the real SDK reads it all."""
    sdk()
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    gw = _gateway(gw_env)
    rt, items = _trained_mock(gw)
    c = TestClient(gw.app)
    task = {"X-Ratchet-Task": "ticket-triage"}
    served = []
    for it in items:
        body = c.post(CHAT, json={"messages": it.messages(), "n": 2, "logprobs": True, "top_logprobs": 1}, headers=task).json()
        if body["ratchet"]["served_by"] == "student":
            served.append((it, body))
    assert len(served) >= 20  # most trained tickets are served
    for it, body in served:
        ChatCompletion.model_validate(body)
        first, second = body["choices"]
        assert (first["index"], second["index"]) == (0, 1)
        assert _labels(first["message"]["content"]) == _labels(second["message"]["content"])
        assert {first["finish_reason"], second["finish_reason"]} == {"stop"}
        # The usage adds up both choices (the mock student bills about 4 characters a token), the prompt once.
        tokens = [approx_tokens(ch["message"]["content"]) for ch in (first, second)]
        assert body["usage"]["completion_tokens"] == sum(tokens) == body["ratchet"]["usage_breakdown"]["served"]["out"]
        assert body["ratchet"]["usage_breakdown"]["student_samples"]["out"] >= sum(tokens)  # 3 samples drawn
        for ch in (first, second):
            assert ch["logprobs"]["refusal"] is None and _spelled(ch["logprobs"]) == ch["message"]["content"]
            assert all(len(t["top_logprobs"]) == 1 for t in ch["logprobs"]["content"])
        # Choice 0's mean token probability is what the confidence score used (the mock's samples are seeded).
        student, model = rt.fleet.resolve(rt.ledger.live())
        samples = student.complete(it.messages(), model, n=3, temperature=0.7, logprobs=True)
        sc = score(tasks.get("ticket-triage"), samples)
        content = first["logprobs"]["content"]
        assert math.exp(sum(t["logprob"] for t in content) / len(content)) == pytest.approx(sc.token_prob, rel=1e-9)
        assert first["message"]["content"] == sc.answer.text

    # Streamed: choice 0's role, content and finish chunks, then choice 1's; the ratchet block on the last finish chunk.
    it, plain = served[0]
    msgs = {"messages": it.messages(), "n": 2, "logprobs": True, "top_logprobs": 1}
    chunks = _stream(c, msgs, headers=task)
    for ch in chunks:
        ChatCompletionChunk.model_validate(ch)
    order = [(ch["choices"][0]["index"], "role" in ch["choices"][0]["delta"], ch["choices"][0]["finish_reason"]) for ch in chunks]
    starts = [i for i, (_, role, _) in enumerate(order) if role]
    ends = [i for i, (_, _, finish) in enumerate(order) if finish]
    assert [order[i][0] for i in starts] == [order[i][0] for i in ends] == [0, 1] and starts[1] == ends[0] + 1
    assert "ratchet" in chunks[-1] and all("ratchet" not in ch for ch in chunks[:-1])
    for index in (0, 1):
        mine = [ch["choices"][0] for ch in chunks if ch["choices"][0]["index"] == index]
        content = [x for x in mine if x["delta"].get("content")]
        assert "".join(x["delta"]["content"] for x in content) == plain["choices"][index]["message"]["content"]
        streamed = [t for x in content for t in x["logprobs"]["content"]]
        assert streamed == plain["choices"][index]["logprobs"]["content"]
        assert all("".join(t["token"] for t in x["logprobs"]["content"]) == x["delta"]["content"] for x in content)
        assert mine[0]["logprobs"] is None and mine[-1]["logprobs"] is None  # the role and finish chunks carry none
    usage_chunks = _stream(c, {**msgs, "stream_options": {"include_usage": True}}, headers=task)
    assert usage_chunks[-1]["choices"] == [] and usage_chunks[-1]["usage"] == plain["usage"]

    # Without logprobs: null, as before; the choices are the same.
    again = c.post(CHAT, json={"messages": it.messages(), "n": 2}, headers=task).json()
    assert [ch["logprobs"] for ch in again["choices"]] == [None, None]
    assert [ch["message"] for ch in again["choices"]] == [ch["message"] for ch in plain["choices"]]

    # The real SDK.
    resp = make_openai_client(gw.app).chat.completions.create(
        model="ratchet", messages=it.messages(), n=2, logprobs=True, top_logprobs=1, extra_headers=task
    )
    assert len(resp.choices) == 2 and resp.usage.completion_tokens == plain["usage"]["completion_tokens"]
    assert "".join(t.token for t in resp.choices[1].logprobs.content) == resp.choices[1].message.content


@pytest.mark.req("FR-1.14", "FR-1.13", "INV-2")
def test_n_choices_escalated_over_http(gw_env, tmp_path, monkeypatch):
    """Escalated with n=2: the teacher behind MockTransport receives n=2 (at 0.7, since the client set no temperature),
    the student draws its usual 3 samples, the response has both choices, and exactly one example is stored. With
    logprobs the teacher is asked for them and each choice carries its own; with n=4 the student draws 4."""
    sdk()
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer(A, out_tokens=6)
    # Without the seen window (live mode's 300 s would count this client's repeats once, ADR 0071), each request's one
    # bump shows in `seen`: n choices never bump more than once.
    rt, student = _gen_runtime(gw, tmp_path, teacher, teacher_modalities="text,image_url", seen_dedup_s=0)
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.99})  # unsure of everything: the teacher answers
    c = TestClient(gw.app)
    body = c.post(CHAT, json={"messages": HI, "n": 2}).json()
    ChatCompletion.model_validate(body)
    assert body["ratchet"]["served_by"] == "teacher" and [ch["index"] for ch in body["choices"]] == [0, 1]
    assert [ch["message"]["content"] for ch in body["choices"]] == [A, A] and body["usage"]["completion_tokens"] == 12
    (tbody,) = _bodies(teacher)
    assert (tbody["n"], tbody["temperature"]) == (2, 0.7) and "logprobs" not in tbody
    assert _bodies(student)[0]["n"] == 3
    assert [e["seen"] for e in rt.ledger.examples()] == [1]

    asked = {"messages": HI, "n": 2, "logprobs": True, "top_logprobs": 2, "temperature": 0.3}
    body = c.post(CHAT, json=asked).json()
    ChatCompletion.model_validate(body)
    tbody = _bodies(teacher)[-1]
    assert (tbody["n"], tbody["temperature"], tbody["logprobs"], tbody["top_logprobs"]) == (2, 0.3, True, 2)
    for ch in body["choices"]:
        assert _spelled(ch["logprobs"]) == A and all(len(t["top_logprobs"]) == 2 for t in ch["logprobs"]["content"])
    assert [e["seen"] for e in rt.ledger.examples()] == [2]  # the same example, bumped once per request

    c.post(CHAT, json={"messages": HI, "n": 4})
    assert (_bodies(student)[-1]["n"], _bodies(teacher)[-1]["n"]) == (4, 4)
    image = {
        "role": "user",
        "content": [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": "x"}}],
    }
    assert len(c.post(CHAT, json={"messages": [image], "n": 3}).json()["choices"]) == 3  # teacher-only: n too
    assert _bodies(teacher)[-1]["n"] == 3

    # A reasoning teacher whose tokens include its reasoning: the client gets the answer's tokens, never the reasoning.
    def reasoning(request: httpx.Request) -> httpx.Response:
        tokens = [
            {"token": t, "logprob": -0.5, "bytes": None, "top_logprobs": []} for t in ("<think>", "SECRET plan", "</think>", A)
        ]
        choice = {
            "message": {"content": "<think>SECRET plan</think>" + A},
            "logprobs": {"content": tokens},
            "finish_reason": "stop",
        }
        return httpx.Response(200, json={"choices": [choice], "usage": {"prompt_tokens": 9, "completion_tokens": 4}})

    cold = replace(rt.settings, db_path=str(tmp_path / "reasoning.db"))  # a new ledger: cold start, the teacher answers
    rt2 = gw.Runtime(cold, transports={"teacher": httpx.MockTransport(reasoning), "student": FakeOpenAIServer(A).transport})
    monkeypatch.setattr(gw, "rt", rt2)
    raw = c.post(CHAT, json={"messages": HI, "logprobs": True}).text
    body = json.loads(raw)
    assert body["choices"][0]["message"]["content"] == A and _spelled(body["choices"][0]["logprobs"]) == A
    assert "SECRET" not in raw and "think" not in raw

    # A teacher that returns no logprobs: every choice says so with content null, streamed too, and still validates.
    rt3, _ = _gen_runtime(gw, tmp_path, FakeOpenAIServer(A, logprob=None))
    monkeypatch.setattr(gw, "rt", rt3)
    body = c.post(CHAT, json={"messages": HI, "n": 2, "logprobs": True}).json()
    ChatCompletion.model_validate(body)
    assert [ch["logprobs"] for ch in body["choices"]] == [{"content": None, "refusal": None}] * 2
    chunks = _stream(c, {"messages": HI, "n": 2, "logprobs": True})
    for ch in chunks:
        ChatCompletionChunk.model_validate(ch)
    content = [ch["choices"][0] for ch in chunks if ch["choices"][0]["delta"].get("content")]
    assert {x["index"] for x in content} == {0, 1}
    assert all(x["logprobs"] == {"content": None, "refusal": None} for x in content)


@pytest.mark.req("FR-1.14", "FR-1.10")
def test_max_n_comes_from_settings(gw_env):
    """RATCHET_MAX_N (4) bounds n; 1 restores one choice per request; a value outside 1 to 128 stops startup."""
    gw = _gateway(gw_env, RATCHET_MAX_N="1")
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    r = c.post(CHAT, json={"messages": msgs, "n": 2})
    err = _error(r)
    assert r.status_code == 400 and (err["code"], err["param"]) == ("unsupported_value", "n")
    assert "at most 1 choice(s) per request, but got 2 (RATCHET_MAX_N)" in err["message"]
    assert gw.rt.ledger.totals()["requests"] == 0
    gw = _gateway(gw_env, RATCHET_MAX_N="6")
    body = TestClient(gw.app).post(CHAT, json={"messages": msgs, "n": 5}).json()
    assert len(body["choices"]) == 5  # cold start: the mock teacher writes all five
    assert Limits().max_n == 4 and validate_chat_request({"messages": HI, "n": 4}).n == 4
    for bad in (0, -1, 129, 2.5, True):
        with pytest.raises(ValueError, match="RATCHET_MAX_N"):
            Limits(max_n=bad)
        with pytest.raises(ValueError, match="RATCHET_MAX_N"):
            gw.Runtime(Settings(mode="mock", mock_warmup_days=0, max_n=bad))


# ---------- validate_chat_request ----------
@pytest.mark.req("FR-1.10")
def test_chat_request_fields():
    req = validate_chat_request(
        {
            "messages": HI,
            "model": "ratchet",
            "max_tokens": 50,
            "max_completion_tokens": 20,
            "stop": "END",
            "temperature": None,
            "metadata": {"ratchet_task": "ticket-triage", "user_id": "u-1"},
            "service_tier": "auto",
            "x_vendor_field": {"anything": [1, 2]},
        }
    )
    assert isinstance(req, ChatRequest) and req.messages is HI
    assert (req.max_tokens, req.stop, req.temperature, req.stream, req.n) == (20, ["END"], None, False, 1)
    assert req.task == "ticket-triage"
    assert req.extra == {"service_tier": "auto", "x_vendor_field": {"anything": [1, 2]}}  # kept and ignored
    assert validate_chat_request({"messages": HI, "max_tokens": 7}).max_tokens == 7
    assert validate_chat_request({"messages": HI, "stop": []}).stop is None
    assert validate_chat_request({"messages": HI}).task is None

    # Shapes SDKs send back: an assistant turn with tool calls and null content, the tool's answer, and an assistant
    # message dumped from a response with its unused fields as null.
    convo = [
        *HI,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "42"}]},
        {"role": "assistant", "content": "It is 42.", "refusal": None, "audio": None, "function_call": None, "tool_calls": None},
        {"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": "{}"}},
        {"role": "function", "name": "f", "content": "42"},
    ]
    assert validate_chat_request({"messages": convo}).messages == convo

    # Valid forms of the optional fields pass and come back as sent.
    fields = {
        "response_format": {"type": "json_object"},
        "tools": [{"type": "function", "function": {"name": "lookup_order", "parameters": {"type": "object"}}}],
        "tool_choice": {"type": "allowed_tools", "allowed_tools": {"mode": "auto", "tools": []}},
        "functions": [{"name": "lookup_order", "parameters": {"type": "object"}}],
        "function_call": {"name": "lookup_order"},
        "logit_bias": {"50256": -100},
        "logprobs": True,
        "top_logprobs": 20,
        "stream": True,
        "stream_options": {"include_usage": True},
        "seed": 7,
        "presence_penalty": -2,
        "frequency_penalty": 2.0,
        "user": "u-1",
        "store": False,
    }
    req = validate_chat_request({"messages": HI, **fields})
    assert {k: getattr(req, k) for k in fields} == fields and req.extra == {}
    # stream_options.include_usage counts only in a stream, as on OpenAI's API; other keys in it are kept.
    assert req.include_usage is True
    assert validate_chat_request({"messages": HI, "stream_options": {"include_usage": True}}).include_usage is False
    newer = {"include_usage": None, "include_obfuscation": False}
    assert validate_chat_request({"messages": HI, "stream": True, "stream_options": newer}).stream_options == newer
    assert validate_chat_request({"messages": HI, "stream": True, "stream_options": newer}).include_usage is False
    with pytest.raises(APIError) as e:  # JSON keys are strings; a Python caller can pass anything
        validate_chat_request({"messages": HI, "metadata": {1: "x"}})
    assert (e.value.code, e.value.param) == ("invalid_type", "metadata")


VALIDATION_CASES = [
    pytest.param([], "invalid_type", None, id="body-list"),
    pytest.param({}, "missing_required_parameter", "messages", id="messages-missing"),
    pytest.param({"messages": None}, "missing_required_parameter", "messages", id="messages-null"),
    pytest.param({"messages": "hi"}, "invalid_type", "messages", id="messages-string"),
    pytest.param({"messages": []}, "invalid_value", "messages", id="messages-empty"),
    pytest.param({"messages": HI * 257}, "invalid_value", "messages", id="257-messages"),
    pytest.param({"messages": [{"role": 5, "content": "x"}]}, "invalid_type", "messages[0].role", id="role-number"),
    pytest.param(
        {"messages": [{"role": "user", "content": "x", "name": 5}]}, "invalid_type", "messages[0].name", id="name-number"
    ),
    pytest.param({"messages": [{"role": "user"}]}, "missing_required_parameter", "messages[0].content", id="content-missing"),
    pytest.param(
        {"messages": [{"role": "assistant", "content": None}]}, "invalid_type", "messages[0].content", id="null-without-calls"
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "content": None, "tool_calls": []}]},
        "invalid_type",
        "messages[0].content",
        id="null-with-empty-calls",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": "x"}]}, "invalid_type", "messages[0].tool_calls", id="calls-string"
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [1]}]}, "invalid_type", "messages[0].tool_calls[0]", id="call-number"
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"type": "function", "function": {"name": "f", "arguments": ""}}]}]},
        "missing_required_parameter",
        "messages[0].tool_calls[0].id",
        id="call-id-missing",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"id": 5, "type": "function"}]}]},
        "invalid_type",
        "messages[0].tool_calls[0].id",
        id="call-id-number",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": ""}}]}]},
        "missing_required_parameter",
        "messages[0].tool_calls[0].type",
        id="call-type-missing",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"id": "c1", "type": "custom", "custom": {"name": "f"}}]}]},
        "unsupported_value",
        "messages[0].tool_calls[0].type",
        id="call-type-custom",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"id": "c1", "type": "function"}]}]},
        "missing_required_parameter",
        "messages[0].tool_calls[0].function",
        id="call-function-missing",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": "f"}]}]},
        "invalid_type",
        "messages[0].tool_calls[0].function",
        id="call-function-string",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"arguments": ""}}]}]},
        "missing_required_parameter",
        "messages[0].tool_calls[0].function.name",
        id="call-name-missing",
    ),
    pytest.param(
        {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": {}}}]}
            ]
        },
        "invalid_type",
        "messages[0].tool_calls[0].function.arguments",
        id="call-arguments-object",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "function_call": {"name": "f"}}]},
        "missing_required_parameter",
        "messages[0].function_call.arguments",
        id="function-call-arguments",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "content": "x", "refusal": 5}]}, "invalid_type", "messages[0].refusal", id="refusal"
    ),
    pytest.param(
        {"messages": [{"role": "tool", "content": "x"}]}, "missing_required_parameter", "messages[0].tool_call_id", id="tool-id"
    ),
    pytest.param(
        {"messages": [{"role": "tool", "content": "x", "tool_call_id": 1}]},
        "invalid_type",
        "messages[0].tool_call_id",
        id="tool-id-int",
    ),
    pytest.param(
        {"messages": [{"role": "user", "content": []}]}, "invalid_value", "messages[0].content", id="content-empty-list"
    ),
    pytest.param(
        {"messages": [{"role": "user", "content": {"text": "x"}}]}, "invalid_type", "messages[0].content", id="content-object"
    ),
    pytest.param({"messages": [{"role": "user", "content": ["x"]}]}, "invalid_type", "messages[0].content[0]", id="part-string"),
    pytest.param(
        {"messages": [{"role": "user", "content": [{}]}]},
        "missing_required_parameter",
        "messages[0].content[0].type",
        id="part-type",
    ),
    pytest.param(
        {"messages": [{"role": "user", "content": [{"type": 1}]}]},
        "invalid_type",
        "messages[0].content[0].type",
        id="part-type-int",
    ),
    pytest.param(
        {"messages": [{"role": "user", "content": [{"type": "text"}]}]},
        "missing_required_parameter",
        "messages[0].content[0].text",
        id="text-missing",
    ),
    pytest.param(
        {"messages": [{"role": "user", "content": [{"type": "text", "text": 5}]}]},
        "invalid_type",
        "messages[0].content[0].text",
        id="text-number",
    ),
    pytest.param(
        {"messages": [{"role": "assistant", "content": [{"type": "refusal"}]}]},
        "missing_required_parameter",
        "messages[0].content[0].refusal",
        id="refusal-missing",
    ),
    pytest.param({"messages": HI, "temperature": True}, "invalid_type", "temperature", id="temperature-bool"),
    pytest.param({"messages": HI, "temperature": "1"}, "invalid_type", "temperature", id="temperature-string"),
    pytest.param({"messages": HI, "temperature": -0.1}, "invalid_value", "temperature", id="temperature-negative"),
    pytest.param({"messages": HI, "top_p": 1.5}, "invalid_value", "top_p", id="top-p"),
    pytest.param({"messages": HI, "max_tokens": 0}, "invalid_value", "max_tokens", id="max-tokens-0"),
    pytest.param(
        {"messages": HI, "max_completion_tokens": 1.5}, "invalid_type", "max_completion_tokens", id="max-completion-float"
    ),
    pytest.param({"messages": HI, "n": "1"}, "invalid_type", "n", id="n-string"),
    pytest.param({"messages": HI, "presence_penalty": 3}, "invalid_value", "presence_penalty", id="presence-penalty"),
    pytest.param({"messages": HI, "frequency_penalty": -2.5}, "invalid_value", "frequency_penalty", id="frequency-penalty"),
    pytest.param({"messages": HI, "seed": "1"}, "invalid_type", "seed", id="seed-string"),
    pytest.param({"messages": HI, "stop": 5}, "invalid_type", "stop", id="stop-number"),
    pytest.param({"messages": HI, "stop": ["a", 1]}, "invalid_type", "stop[1]", id="stop-item"),
    pytest.param({"messages": HI, "logit_bias": [1]}, "invalid_type", "logit_bias", id="logit-bias-list"),
    pytest.param({"messages": HI, "logit_bias": {"50256": 101}}, "invalid_value", "logit_bias", id="logit-bias-range"),
    pytest.param({"messages": HI, "logit_bias": {"50256": 1.5}}, "invalid_type", "logit_bias", id="logit-bias-float"),
    pytest.param({"messages": HI, "logprobs": "yes"}, "invalid_type", "logprobs", id="logprobs-string"),
    pytest.param({"messages": HI, "logprobs": True, "top_logprobs": 21}, "invalid_value", "top_logprobs", id="top-logprobs-21"),
    pytest.param(
        {"messages": HI, "logprobs": False, "top_logprobs": 1}, "invalid_value", "top_logprobs", id="top-logprobs-false"
    ),
    pytest.param({"messages": HI, "stream": "true"}, "invalid_type", "stream", id="stream-string"),
    pytest.param({"messages": HI, "stream_options": "x"}, "invalid_type", "stream_options", id="stream-options"),
    pytest.param(
        {"messages": HI, "stream": True, "stream_options": {"include_usage": "yes"}},
        "invalid_type",
        "stream_options.include_usage",
        id="include-usage-string",
    ),
    pytest.param({"messages": HI, "metadata": ["ratchet_task"]}, "invalid_type", "metadata", id="metadata-list"),
    pytest.param(
        {"messages": HI, "metadata": {str(i): "v" for i in range(17)}}, "invalid_value", "metadata", id="metadata-17-keys"
    ),
    pytest.param({"messages": HI, "metadata": {"k" * 65: "v"}}, "invalid_value", "metadata", id="metadata-long-key"),
    pytest.param({"messages": HI, "metadata": {"note": "v" * 513}}, "invalid_value", "metadata.note", id="metadata-long-value"),
    pytest.param({"messages": HI, "metadata": {"ratchet_task": 5}}, "invalid_type", "metadata.ratchet_task", id="task-number"),
    pytest.param(
        {"messages": HI, "metadata": {"ratchet_task": "no spaces"}}, "invalid_value", "metadata.ratchet_task", id="task-space"
    ),
    pytest.param(
        {"messages": HI, "metadata": {"ratchet_task": "ticket-triage\n"}},
        "invalid_value",
        "metadata.ratchet_task",
        id="task-newline",
    ),
    pytest.param({"messages": HI, "response_format": "json"}, "invalid_type", "response_format", id="format-string"),
    pytest.param({"messages": HI, "response_format": {}}, "missing_required_parameter", "response_format.type", id="format-type"),
    pytest.param({"messages": HI, "response_format": {"type": "xml"}}, "invalid_value", "response_format.type", id="format-xml"),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema"}},
        "missing_required_parameter",
        "response_format.json_schema",
        id="json-schema-missing",
    ),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema", "json_schema": {"name": "has space"}}},
        "invalid_value",
        "response_format.json_schema.name",
        id="json-schema-name",
    ),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema", "json_schema": {"name": "t", "schema": []}}},
        "invalid_type",
        "response_format.json_schema.schema",
        id="json-schema-list",
    ),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema", "json_schema": {"name": "t", "schema": _nest(17)}}},
        "invalid_value",
        "response_format.json_schema.schema",
        id="json-schema-depth-17",
    ),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema", "json_schema": {"name": "t", "schema": {"d": "x" * 65_536}}}},
        "invalid_value",
        "response_format.json_schema.schema",
        id="json-schema-over-64kb",
    ),
    pytest.param(
        {"messages": HI, "response_format": {"type": "json_schema", "json_schema": {"name": "t", "strict": "yes"}}},
        "invalid_type",
        "response_format.json_schema.strict",
        id="json-schema-strict",
    ),
    pytest.param({"messages": HI, "tools": {"type": "function"}}, "invalid_type", "tools", id="tools-object"),
    pytest.param(
        {"messages": HI, "tools": [{"type": "function", "function": {"name": f"f{i}"}} for i in range(129)]},
        "invalid_value",
        "tools",
        id="129-tools",
    ),
    pytest.param(
        {"messages": HI, "tools": [{"type": "custom", "custom": {}}]}, "unsupported_value", "tools[0].type", id="tool-custom"
    ),
    pytest.param(
        {"messages": HI, "tools": [{"type": "function"}]}, "missing_required_parameter", "tools[0].function", id="tool-function"
    ),
    pytest.param(
        {"messages": HI, "tools": [{"type": "function", "function": {"name": "a b"}}]},
        "invalid_value",
        "tools[0].function.name",
        id="tool-name",
    ),
    pytest.param(
        {"messages": HI, "tools": [{"type": "function", "function": {"name": "f", "parameters": "x"}}]},
        "invalid_type",
        "tools[0].function.parameters",
        id="tool-parameters",
    ),
    pytest.param(
        {"messages": HI, "tools": [{"type": "function", "function": {"name": "f", "description": "d" * 65_536}}]},
        "invalid_value",
        "tools",
        id="tools-over-64kb",
    ),
    pytest.param({"messages": HI, "tool_choice": "sometimes"}, "invalid_value", "tool_choice", id="tool-choice-string"),
    pytest.param(
        {"messages": HI, "tool_choice": {"type": "function"}}, "invalid_type", "tool_choice.function", id="tool-choice-fn"
    ),
    pytest.param({"messages": HI, "functions": [{"name": 5}]}, "invalid_type", "functions[0].name", id="function-name"),
    pytest.param(
        {"messages": HI, "function_call": {"x": 1}}, "missing_required_parameter", "function_call.name", id="function-call"
    ),
    pytest.param({"messages": HI, "parallel_tool_calls": "x"}, "invalid_type", "parallel_tool_calls", id="parallel-tool-calls"),
    pytest.param({"messages": HI, "user": 5}, "invalid_type", "user", id="user-number"),
    pytest.param({"messages": HI, "model": 5}, "invalid_type", "model", id="model-number"),
    pytest.param({"messages": HI, "store": 1}, "invalid_type", "store", id="store-number"),
    pytest.param({"messages": HI, "reasoning_effort": 1}, "invalid_type", "reasoning_effort", id="reasoning-effort"),
    pytest.param(
        {"messages": [{"role": "assistant", "function_call": "f"}]},
        "invalid_type",
        "messages[0].function_call",
        id="function-call-msg",
    ),
]


@pytest.mark.req("FR-1.10")
@pytest.mark.parametrize(("body", "code", "param"), VALIDATION_CASES)
def test_validation_rules(body, code, param):
    with pytest.raises(APIError) as e:
        validate_chat_request(body)
    err = e.value.body()["error"]
    assert (e.value.status, err["type"], err["code"], err["param"]) == (400, "invalid_request_error", code, param), err
    assert err["message"]


CALL_6 = {"name": "f", "arguments": "12345"}  # a call to f(12345): the name and arguments count, 6 characters


@pytest.mark.req("FR-1.10")
def test_limits_and_their_off_switch():
    small = Limits(max_messages=2, max_input_chars=5)
    for body, code in (
        ({"messages": HI * 3}, "invalid_value"),
        ({"messages": [{"role": "user", "content": "123456"}]}, "context_length_exceeded"),
        (
            {"messages": [{"role": "user", "content": [{"type": "text", "text": "123"}, {"type": "text", "text": "456"}]}]},
            "context_length_exceeded",
        ),
        ({"messages": [{"role": "assistant", "content": [{"type": "refusal", "refusal": "123456"}]}]}, "context_length_exceeded"),
        ({"messages": [{"role": "user", "content": "123", "name": "456"}]}, "context_length_exceeded"),
        ({"messages": [{"role": "assistant", "content": "123", "refusal": "456"}]}, "context_length_exceeded"),
        ({"messages": [{"role": "tool", "content": "123", "tool_call_id": "456"}]}, "context_length_exceeded"),
        (
            {"messages": [{"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": CALL_6}]}]},
            "context_length_exceeded",
        ),
        ({"messages": [{"role": "assistant", "content": None, "function_call": CALL_6}]}, "context_length_exceeded"),
    ):
        with pytest.raises(APIError) as e:
            validate_chat_request(body, small)
        assert (e.value.code, e.value.param) == (code, "messages")
    assert validate_chat_request({"messages": [{"role": "user", "content": "12345"}]}, small).messages  # at the limit
    # Keys a message's role does not use are dropped by normalize_messages, so they are not counted either.
    junk = {"role": "user", "content": "12345", "tool_call_id": "x" * 100, "vendor": "y" * 100}
    assert validate_chat_request({"messages": [junk]}, small).messages == [junk]
    validate_chat_request({"messages": [{"role": "user", "content": "x" * 10}] * 500}, Limits(max_messages=0, max_input_chars=0))
    assert validate_chat_request({"messages": HI, "n": 3}, Limits(max_n=4)).n == 3


@pytest.mark.req("FR-1.10")
def test_parse_body_and_error_helpers():
    assert parse_body(b'{"a": [1, 2.5, "\\u00e9"]}') == {"a": [1, 2.5, "é"]}
    for raw in (b"{", b'{"a": Infinity}', b'{"a": -NaN}', b"\xff"):
        with pytest.raises(APIError) as e:
            parse_body(raw)
        assert (e.value.status, e.value.code) == (400, "invalid_json")
    # Surrogates: a valid escaped pair is one character, in bytes, text or UTF-16; an unpaired one, escaped or as bytes
    # (also both halves of a pair as separate UTF-8 sequences, CESU-8), is a 400 naming where it is.
    assert parse_body(b'{"a": "\\ud83d\\ude00"}') == parse_body('{"a": "\\ud83d\\ude00"}') == {"a": "\U0001f600"}
    assert parse_body('{"a": "\U0001f600 \\\\ud800"}'.encode("utf-16")) == {"a": "\U0001f600 \\ud800"}  # an escaped backslash
    for raw, param in (
        (b'"\\ud800"', None),
        (b'{"a": [1, {"b": "x\\udc00y"}]}', "a[1].b"),
        (b'{"a": {"\\ude00": 1}}', "a"),
        (b'{"a": "\\ude00\\ud83d"}', "a"),  # the wrong way round
        (b'{"a": ["\xed\xa0\xbd\xed\xb8\x80"]}', "a[0]"),
        ('{"a": "\ud800"}', "a"),
    ):
        with pytest.raises(APIError) as e:
            parse_body(raw)
        assert (e.value.status, e.value.code, e.value.param) == (400, "invalid_json", param), raw
        assert json.dumps(e.value.body(), ensure_ascii=False).encode()  # the error itself is valid UTF-8
    assert http_error(404, "Not Found", method="GET", path="/v1/x").body()["error"]["code"] == "unknown_url"
    assert http_error(404, "no such model").body()["error"] == {
        "message": "no such model",
        "type": "not_found_error",
        "param": None,
        "code": None,
    }
    assert [(http_error(s, "m").type, http_error(s, "m").code) for s in (401, 403, 409, 413, 418, 429, 502, 503)] == [
        ("invalid_request_error", "invalid_api_key"),
        ("permission_error", None),
        ("invalid_request_error", None),
        ("invalid_request_error", "request_too_large"),
        ("invalid_request_error", None),
        ("rate_limit_error", None),
        ("api_error", None),
        ("api_error", None),
    ]
    assert http_error(401, "m", headers={"WWW-Authenticate": "Bearer"}).headers == {"WWW-Authenticate": "Bearer"}
    err = from_validation_errors([{"loc": ("body", "messages", 2, "content"), "msg": "Field required", "type": "missing"}])
    assert (err.status, err.code, err.param) == (400, "missing_required_parameter", "messages[2].content")
    assert from_validation_errors([]).param is None
    with pytest.raises(APIError) as e:  # a value json.dumps cannot write (Python callers only) counts as too big
        validate_chat_request({"messages": HI, "tools": [{"type": "function", "function": {"name": "f", "x": object()}}]})
    assert (e.value.code, e.value.param) == ("invalid_value", "tools")


# ---------- normalize_messages ----------
@pytest.mark.req("FR-1.10")
def test_normalize_rewrites_only_what_it_must():
    system = {"role": "system", "content": "Be brief."}
    user = {"role": "user", "content": "hi"}
    developer = {"role": "developer", "content": "Reply with JSON.", "name": "ops"}
    parts = {"role": "user", "content": [{"type": "text", "text": "line 1"}, {"type": "text", "text": "line 2"}]}
    refused = {"role": "assistant", "content": [{"type": "refusal", "refusal": "I can't help with that."}]}
    spoken = {"role": "assistant", "content": "Done.", "audio": {"id": "audio_1"}}
    msgs = [developer, system, parts, refused, spoken, user]
    snapshot = json.loads(json.dumps(msgs))

    out, flags = normalize_messages(msgs)
    assert out == [
        {"role": "system", "content": "Reply with JSON.", "name": "ops"},
        system,
        {"role": "user", "content": "line 1\nline 2"},
        {"role": "assistant", "content": "I can't help with that."},
        {"role": "assistant", "content": "Done."},
        user,
    ]
    assert out[1] is system and out[5] is user and not flags.multimodal  # plain messages are the same objects
    assert msgs == snapshot  # the caller's messages are never changed in place

    merged, _ = normalize_messages(msgs, merge_system=True)
    assert merged[0] == {"role": "system", "content": "Reply with JSON.\n\nBe brief."} and merged[1:] == out[2:]
    plain = [system, user]
    for merge in (False, True):  # nothing to change: the same list back
        same, _ = normalize_messages(plain, merge_system=merge)
        assert same is plain

    # Keys a role does not use are dropped, like unknown top-level keys, so neither the teacher nor the ledger gets them.
    call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    asked = {"role": "assistant", "content": None, "refusal": None, "tool_calls": [call], "annotations": [], "audio": None}
    answered = {"role": "tool", "tool_call_id": "c1", "content": "42", "name": "f"}
    msgs = [
        {"role": "user", "content": "hi", "tool_call_id": "c0", "tool_calls": "junk", "vendor": {"x": [1]}},
        asked,
        answered,
        {"role": "assistant", "content": "ok", "tool_calls": [{**call, "index": 0, "function": {**call["function"], "x": 1}}]},
        {"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": "{}", "x": 1}},
    ]
    out, _ = normalize_messages(msgs)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "refusal": None, "tool_calls": [call]},
        answered,
        {"role": "assistant", "content": "ok", "tool_calls": [call]},
        {"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": "{}"}},
    ]
    assert out[1]["tool_calls"][0] is call and out[2] is answered


@pytest.mark.req("FR-1.10")
def test_non_text_parts_need_a_teacher_modality():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    audio = {"type": "input_audio", "input_audio": {"data": "", "format": "wav"}}
    pdf = {"type": "file", "file": {"file_id": "f1"}}
    for i, part in enumerate((image, audio, pdf)):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": [{"type": "text", "text": "look"}, part]}]
        with pytest.raises(APIError) as e:
            normalize_messages(msgs)
        assert (e.value.status, e.value.code, e.value.param) == (
            400,
            "unsupported_content_type",
            "messages[1].content[1].type",
        ), i
    with pytest.raises(APIError) as e:  # a refusal part is the assistant's own; from the user it is not text
        normalize_messages([{"role": "user", "content": [{"type": "refusal", "refusal": "no"}]}])
    assert e.value.param == "messages[0].content[0].type"

    modalities = parse_modalities(" image_url , ,file")
    assert modalities == {"text", "image_url", "file"}
    msg = {"role": "user", "content": [{"type": "text", "text": "look"}, image]}
    out, flags = normalize_messages([msg], modalities)
    assert flags.multimodal and out[0] is msg  # the teacher gets the parts as sent
    with pytest.raises(APIError):
        normalize_messages([{"role": "user", "content": [audio]}], modalities)
    assert parse_modalities("") == {"text"}


@pytest.mark.req("FR-1.10")
def test_normalize_is_identity_on_the_mock_world():
    """Every mock request (support tickets and Scout's calls) comes back as the same objects, so example hashes,
    golden splits, mock lookups and the reference simulation cannot move."""
    stream = tickets.TicketStream(seed=7, drift_day=5)  # tickets register as they are generated: every template, drift too
    sampled = {stream.sample(day).text for day in range(1, 11) for _ in range(300)}
    items = [*tickets.REGISTRY.values(), *research.REGISTRY.values()]
    assert sampled <= set(tickets.REGISTRY) and len(research.REGISTRY) > 1000
    for item in items:
        msgs = item.messages()
        for merge in (False, True):
            out, flags = normalize_messages(msgs, merge_system=merge)
            assert out is msgs and not flags.multimodal
        req = validate_chat_request({"messages": msgs, "metadata": {"ratchet_task": item.task}})
        assert req.messages is msgs and req.task == item.task
