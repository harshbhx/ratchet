"""OpenAI compatibility of `/v1/chat/completions`: strict request validation, message normalisation, the error envelope
OpenAI SDKs read, the body and input limits, and the rule that a client mistake is never a 500 (ratchet/openai_compat.py,
the gateway's error handlers and `BodyLimitMiddleware`; docs/API.md#errors, ADR 0012).

Gateway tests reload the mock gateway with `PINNED_ENV`, so variables in the shell cannot move a result. The SDK tests
drive the real `openai` package through `tests/sdk_bridge.py` and are skipped when it is not installed.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

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
from ratchet.scout.prompts import WRITE_SYSTEM
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
    "RATCHET_MERGE_SYSTEM": "0",
    "RATCHET_TEACHER_MODALITIES": "text",
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
    assert set(body) == {"error", "detail"}, body
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
    pytest.param({"messages": HI, "n": 2}, 400, "unsupported_value", "n", id="n-2"),
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
    "user": "u-1",
}


@pytest.mark.req("FR-1.10")
def test_no_client_mistake_gives_500(gw_env, tmp_path, monkeypatch):
    """Swap every value of a request that uses every field for junk, or delete it, and let validation judge it first.
    One it refuses gets exactly that 400 (code and param) from the gateway; one it accepts is answered: 200 in live mode
    (the teacher behind MockTransport), and in mock mode the mock's own 400, which only knows demo requests. Never a
    500, and a refused request is never logged."""
    gw = _gateway(gw_env)
    mock = gw.rt
    live = gw.Runtime(
        Settings(mode="live", db_path=str(tmp_path / "live.db"), student_base="", teacher_api_key="", audit_rate=0.0),
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
        assert r.status_code == 400 and "mock mode only answers demo requests" in r.json()["detail"], (path, new)
    assert 0 < accepted < len(mutations) // 2  # both branches run; most junk is refused
    assert (mock.gateway_errors, live.gateway_errors) == (0, 0)
    assert mock.ledger.totals()["requests"] == 0 and live.ledger.totals()["requests"] == accepted


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
        Settings(mode="live", db_path=str(tmp_path / "live.db"), student_base="", teacher_api_key="", audit_rate=0.0),
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


@pytest.mark.req("FR-1.8", "FR-1.10")
def test_mock_keyerror_is_400_and_live_keyerror_is_500(gw_env, monkeypatch, tmp_path):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    unknown = [
        HI,  # not a demo ticket
        [{"role": "system", "content": "only a system message"}],  # no user message at all
        [{"role": "system", "content": WRITE_SYSTEM}, {"role": "user", "content": "not JSON"}],  # Scout's writer
        [{"role": "system", "content": WRITE_SYSTEM}, {"role": "user", "content": "[" * 100_000}],  # too deep to parse
    ]
    for messages in unknown:
        r = c.post(CHAT, json={"messages": messages})
        assert r.status_code == 400 and "mock mode only answers demo requests" in r.json()["detail"], r.text
        assert _error(r)["type"] == "invalid_request_error"
    assert gw.rt.gateway_errors == 0

    # In live mode a KeyError is a bug in the gateway, not a client mistake.
    live = _gateway(gw_env, RATCHET_MODE="live", RATCHET_DB=str(tmp_path / "live.db"), RATCHET_STUDENT_BASE="", NEBIUS_API_KEY="")

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
    assert r.status_code == 400 and "mock mode only answers" in r.json()["detail"]  # validated, then unknown to the mock


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


@pytest.mark.req("FR-1.10", "INV-12")
def test_dropped_keys_and_redacted_tool_calls_end_to_end(gw_env):
    """Keys a message's role does not use reach neither the teacher nor the ledger. A tool call's arguments reach the
    teacher as sent and are stored redacted, like content."""
    gw = _gateway(gw_env)
    teacher = gw.rt.ratchet.teacher = RecordingProvider(A)
    call = {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": '{"email": "bob@example.com"}'}}
    msgs = [
        {"role": "user", "content": "Find my order.", "vendor_note": "call me on +44 20 7946 0958"},
        {"role": "assistant", "content": None, "tool_calls": [{**call, "index": 0}], "annotations": []},
        {"role": "tool", "tool_call_id": "c1", "content": "Order 7 ships today."},
    ]
    assert TestClient(gw.app).post(CHAT, json={"messages": msgs}).status_code == 200
    sent = [{"role": "user", "content": "Find my order."}, {"role": "assistant", "content": None, "tool_calls": [call]}, msgs[2]]
    assert teacher.calls[0].messages == sent
    (example,) = gw.rt.ledger.examples()
    stored = json.loads(example["messages"])
    assert stored[1]["tool_calls"][0]["function"]["arguments"] == '{"email": "[EMAIL]"}'
    assert "vendor_note" not in example["messages"] and "7946" not in example["messages"] and example["redactions"] == 1


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
    (row,) = [dict(x) for x in led.db.execute("SELECT task, served_by, escalated, student_version FROM requests")]
    assert row == {"task": "ticket-triage", "served_by": "teacher", "escalated": 0, "student_version": None}
    # The same gateway sends a text request through the cascade as usual: the student answers.
    assert c.post(CHAT, json={"messages": HI}).json()["ratchet"]["served_by"] == "student" and len(student.calls) == 1
    # Audio is still refused, and in mock mode the mock teacher does not know the image request (400, not 500).
    audio = {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "", "format": "wav"}}]}
    assert _error(c.post(CHAT, json={"messages": [audio]}))["code"] == "unsupported_content_type"
    gw.rt.reset()
    r = c.post(CHAT, json={"messages": [image]})
    assert r.status_code == 400 and "mock mode only answers demo requests" in r.json()["detail"]


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
