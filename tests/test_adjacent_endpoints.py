"""The other OpenAI endpoints a drop-in base URL receives (FR-1.51, ADR 0050): the `/v1/embeddings` pass-through (the
configured model always answers, nothing is stored, the request is logged and counted), `/v1/completions` and the
stateless subset of `/v1/responses` (both run the chat path, so they answer, log and store exactly as the chat request
they become), and the typed 404s: `unsupported_endpoint` for moderations and stored responses, `unknown_url` for any
other path. Responses are checked with the OpenAI SDK's own models and clients."""

from __future__ import annotations

import base64
import json
import math
import struct
import typing
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

from ratchet import ledger as ledger_module
from ratchet import licenses, tasks, taskspec
from ratchet.config import DEFAULT_EMBEDDINGS_MODEL, DEFAULT_PRICES, EMBEDDING_PRICES, Settings
from ratchet.ledger import Ledger, example_hash
from ratchet.openai_compat import (
    MAX_EMBEDDING_INPUTS,
    MAX_PROMPTS,
    REASONING_EFFORTS,
    APIError,
    ModelRouting,
    check_embeddings_model,
    legacy_logprobs,
    normalize_messages,
    response_object,
    response_output,
    responses_usage,
    validate_completion_request,
    validate_embeddings_request,
    validate_responses_request,
)
from ratchet.providers import UpstreamError, _embeddings
from ratchet.router import PASSTHROUGH_ROUTE, ROUTES, known_route
from sim import tickets

EMBED = "/v1/embeddings"
COMPLETE = "/v1/completions"
RESPOND = "/v1/responses"
CHAT = "/v1/chat/completions"
TRIAGE = "ticket-triage"
TICKET = tickets.TicketStream(seed=11).sample(1).text  # registered, so the mock teacher answers its labels (stored)
KEY = "acme-api-key-0123456789abcdef"
ACME = {"Authorization": f"Bearer {KEY}"}
# Everything these tests depend on, pinned so the shell's variables cannot move a result.
ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_ADMIN_KEYS": "",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_MODEL_POLICY": "lenient",
    "RATCHET_MODEL_ALIASES": "",
    "RATCHET_AUTO_TASK": "off",
    "RATCHET_UNKNOWN_TASKS": "distill",
    "RATCHET_TASKS_FILE": "",
    "RATCHET_MOCK_STRICT": "0",
    "RATCHET_MERGE_SYSTEM": "0",
    "RATCHET_MAX_INPUT_CHARS": "120000",
    "RATCHET_EMBEDDINGS_BASE": "",
    "RATCHET_EMBEDDINGS_MODEL": DEFAULT_EMBEDDINGS_MODEL,
    "RATCHET_EMBEDDINGS_KEY": "",
}


def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**ENV, **env})


def _rows(gw: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in gw.rt.ledger.db.execute("SELECT * FROM requests ORDER BY id")]


def _examples(gw: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in gw.rt.ledger.db.execute("SELECT * FROM examples ORDER BY id")]


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _floats(b64: str) -> list[float]:
    raw = base64.b64decode(b64)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _error(r: Any) -> tuple[int, str | None, str | None]:
    err = r.json()["error"]
    return r.status_code, err["code"], err["param"]


def _live(gw: Any, tmp_path: Path, monkeypatch: Any, teacher: FakeOpenAIServer, **overrides: Any) -> Any:
    """The gateway in live mode against fake upstreams (`transports` may add an "embeddings" one), installed as `gw.rt`."""
    transports = overrides.pop("transports", {})
    fields = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "tf-teacher-api-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "",
        "teacher_retries": 0,
        "api_keys": "",
        "api_key_daily_requests": 0,
        "model_policy": "lenient",
        "model_aliases": "",
        "auto_task": "off",
        "embeddings_base": "",
        "embeddings_model": DEFAULT_EMBEDDINGS_MODEL,
        "embeddings_api_key": "",
        "mock_warmup_days": 0,
        # No API keys and a public dashboard (ADR 0061), no per-IP rate (ADR 0063): these tests call /v1/* and read
        # /ratchet/stats without a key, from one address.
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,
        "ip_rpm": 0,
        **overrides,
    }
    rt = gw.Runtime(Settings(**fields), transports={"teacher": teacher.transport, **transports}, sleep=lambda s: None)
    monkeypatch.setattr(gw, "rt", rt)
    return rt


# ---------- /v1/embeddings ----------
@pytest.mark.req("FR-1.51", "INV-12")
def test_mock_embeddings_validate_with_the_sdk(gw_env):
    """Deterministic unit vectors in OpenAI's shape; the request is logged (task `embeddings`, served_by `upstream`,
    route `passthrough`) and counted, and nothing of it is stored."""
    sdk()
    from openai.types import CreateEmbeddingResponse

    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    body = {"input": ["refund my order", "reset my password", "refund my order"], "model": "text-embedding-3-small"}
    r = c.post(EMBED, json=body)
    assert r.status_code == 200
    got = CreateEmbeddingResponse.model_validate(r.json())
    assert got.model == DEFAULT_EMBEDDINGS_MODEL and [d.index for d in got.data] == [0, 1, 2]
    vectors = [d.embedding for d in got.data]
    assert all(len(v) == 256 and _norm(v) == pytest.approx(1.0) for v in vectors)
    assert vectors[0] == vectors[2] and vectors[0] != vectors[1]
    assert c.post(EMBED, json=body).json()["data"][1]["embedding"] == vectors[1]  # the same input, the same vector
    assert r.headers["X-Ratchet-Served-By"] == "upstream" and r.headers["X-Ratchet-Task"] == "embeddings"
    assert r.headers["X-Ratchet-Model-Alias"] == "text-embedding-3-small"  # another id, answered by the configured model
    tokens = got.usage.prompt_tokens
    assert tokens == got.usage.total_tokens == sum(max(1, len(t) // 4) for t in body["input"])  # as the mock counts
    meta = r.json()["ratchet"]
    assert (meta["served_by"], meta["route"], meta["task"]) == ("upstream", PASSTHROUGH_ROUTE, "embeddings")
    assert meta["cost_usd"] == pytest.approx(EMBEDDING_PRICES[DEFAULT_EMBEDDINGS_MODEL].cost(tokens, 0))

    # Shortened vectors keep the full vector's direction (its first values, scaled back to length 1), as OpenAI's do.
    short = c.post(EMBED, json={"input": "refund my order", "dimensions": 64}).json()["data"][0]["embedding"]
    assert len(short) == 64 and _norm(short) == pytest.approx(1.0)
    prefix = vectors[0][:64]
    assert short == pytest.approx([x / _norm(prefix) for x in prefix])

    # The OpenAI SDK asks for base64 by default and decodes it: the same floats, to float32 precision.
    client = make_openai_client(gw.app)
    sdk_vector = client.embeddings.create(input="refund my order", model="any-model").data[0].embedding
    assert sdk_vector == pytest.approx(vectors[0], abs=1e-6)
    b64 = c.post(EMBED, json={"input": "refund my order", "encoding_format": "base64"}).json()["data"][0]["embedding"]
    assert isinstance(b64, str) and _floats(b64) == pytest.approx(vectors[0], abs=1e-6)
    # A token array is embedded as itself (its JSON seeds the mock vector).
    tok = c.post(EMBED, json={"input": [[11, 22, 33], [44]]}).json()
    assert len(tok["data"]) == 2 and tok["usage"]["prompt_tokens"] == 4

    # The shared mock demo returns at most 2^17 floats per request (about 3 MB of JSON): 512 inputs at 256 dimensions.
    r = c.post(EMBED, json={"input": ["a"] * 17, "dimensions": 8192})
    assert _error(r) == (400, "unsupported_value", "input") and "at most 131072" in r.json()["error"]["message"]
    assert _error(c.post(EMBED, json={"input": ["a"] * 513}))[0] == 400

    rows = _rows(gw)
    assert len(rows) == 6 and gw.rt.ledger.examples() == []
    for row in rows:
        assert (row["task"], row["served_by"], row["route"], row["stored"]) == ("embeddings", "upstream", "passthrough", 0)
        assert row["cost_usd"] == row["baseline_cost_usd"] == row["teacher_cost_usd"] and row["student_cost_usd"] == 0
        assert row["escalated"] == 0 and row["audited"] == 0 and row["student_version"] is None
    assert known_route(PASSTHROUGH_ROUTE) and PASSTHROUGH_ROUTE not in ROUTES  # the gateway's route, not the router's
    by_task = {t["task"]: t for t in c.get("/ratchet/stats").json()["tasks"]}
    assert by_task["embeddings"]["routes"] == {"passthrough": 6} and by_task["embeddings"]["savings_pct"] == 0.0

    # /v1/models offers the embeddings model (the id RATCHET_MODEL_POLICY=strict accepts), never `ratchet/embeddings`.
    listed = {m["id"]: m for m in c.get("/v1/models").json()["data"]}
    assert listed[DEFAULT_EMBEDDINGS_MODEL]["ratchet"]["endpoint"] == "embeddings" and "ratchet/embeddings" not in listed
    assert c.get(f"/v1/models/{DEFAULT_EMBEDDINGS_MODEL}").json() == listed[DEFAULT_EMBEDDINGS_MODEL]
    assert client.models.retrieve(DEFAULT_EMBEDDINGS_MODEL).model_extra["ratchet"]["task"] is None
    assert "embeddings" not in {t["name"] for t in c.get("/ratchet/tasks").json()["unregistered"]}


@pytest.mark.req("FR-1.51", "FR-15.1")
def test_embeddings_stay_out_of_the_headline_figures(gw_env):
    """The per-day figures, the totals and the per-client rows describe the distillation, so embeddings calls (route
    `passthrough`) are left out of them: a client that embeds once per chat request does not halve the student share,
    pull the latency toward the embedder's or the savings toward 0. Their spend still adds up in the `embeddings` task
    row and the cost report, and they still count against the key's quota."""
    led = Ledger(":memory:")

    def log(task: str, served_by: str, cost: float, baseline: float, latency: float, route: str | None) -> None:
        row = {"cost_usd": cost, "baseline_cost_usd": baseline, "latency_ms": latency, "route": route}
        led.log_request(task=task, served_by=served_by, escalated=0, client="acme", **row)

    log("t", "student", 1.0, 4.0, 10.0, "student")
    log("t", "teacher", 4.0, 4.0, 30.0, None)  # a row older than the route column is counted
    alone = (led.stats_by_day(), led.totals(), led.stats_by_client())
    for _ in range(4):
        log("embeddings", "upstream", 2.0, 2.0, 500.0, PASSTHROUGH_ROUTE)
    assert (led.stats_by_day(), led.totals(), led.stats_by_client()) == alone
    (day,) = alone[0]
    assert (day["requests"], day["student_share"], day["avg_latency_ms"], day["savings_pct"]) == (2, 0.5, 20.0, 0.375)
    assert ledger_module.NOT_PASSTHROUGH == f"route IS NOT '{PASSTHROUGH_ROUTE}'"
    by_task = {t["task"]: t for t in led.stats_by_task()}
    assert (by_task["embeddings"]["requests"], by_task["embeddings"]["cost_usd"]) == (4, 8.0)
    (spend,) = led.cost_by_day()
    assert (spend["requests"], spend["cost_usd"], spend["teacher_usd"]) == (6, 13.0, 13.0)
    assert led.client_requests_since("acme", 0) == 6

    # Through the gateway: the same /ratchet/stats headline before and after embeddings calls.
    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}")
    c = TestClient(gw.app)
    assert c.post(CHAT, json={"messages": [{"role": "user", "content": "hi"}]}, headers=ACME).status_code == 200
    before = c.get("/ratchet/stats").json()
    for _ in range(3):
        assert c.post(EMBED, json={"input": "refund my order"}, headers=ACME).status_code == 200
    after = c.get("/ratchet/stats").json()
    assert [after[k] == before[k] for k in ("days", "totals")] == [True] * 2
    # The client's row moves only by what the key spent today (the spend meter, ADR 0063): embeddings count against its
    # budgets, as against its quota.
    (b,), (a,) = before["clients"], after["clients"]
    assert {**a, "spent_today_usd": None} == {**b, "spent_today_usd": None} and a["spent_today_usd"] > b["spent_today_usd"]
    assert {t["task"]: t["requests"] for t in after["tasks"]}["embeddings"] == 3
    assert gw.rt.ledger.client_requests_since("acme", 0) == 4


@pytest.mark.req("FR-1.51")
def test_embeddings_validation_names_the_field(gw_env):
    gw = _gateway(gw_env, RATCHET_MAX_INPUT_CHARS="40")
    c = TestClient(gw.app)
    cases = [
        ({}, 400, "missing_required_parameter", "input"),
        ({"input": ""}, 400, "invalid_value", "input"),
        ({"input": []}, 400, "invalid_value", "input"),
        ({"input": ["a", ""]}, 400, "invalid_value", "input[1]"),
        ({"input": ["a", 3]}, 400, "invalid_type", "input[1]"),
        ({"input": [[1, 2], "a"]}, 400, "invalid_type", "input[1]"),
        ({"input": [[]]}, 400, "invalid_value", "input[0]"),
        ({"input": [1, -2]}, 400, "invalid_value", "input[1]"),
        ({"input": [1, 2.5]}, 400, "invalid_type", "input[1]"),
        ({"input": [True]}, 400, "invalid_type", "input[0]"),
        ({"input": {"text": "a"}}, 400, "invalid_type", "input"),
        ({"input": ["x"] * (MAX_EMBEDDING_INPUTS + 1)}, 400, "invalid_value", "input"),
        ({"input": "x" * 41}, 400, "context_length_exceeded", "input"),
        ({"input": list(range(11))}, 400, "context_length_exceeded", "input"),  # a token counts as 4 characters
        ({"input": "a", "encoding_format": "binary"}, 400, "invalid_value", "encoding_format"),
        ({"input": "a", "dimensions": 0}, 400, "invalid_value", "dimensions"),
        ({"input": "a", "dimensions": 8193}, 400, "invalid_value", "dimensions"),
        ({"input": "a", "dimensions": "8"}, 400, "invalid_type", "dimensions"),
        ({"input": "a", "model": 4}, 400, "invalid_type", "model"),
    ]
    for body, status, code, param in cases:
        r = c.post(EMBED, json=body)
        assert _error(r) == (status, code, param), body
    assert c.post(EMBED, content=b"{not json").json()["error"]["code"] == "invalid_json"
    assert c.post(EMBED, json=["a"]).json()["error"]["code"] == "invalid_type"
    assert c.post(EMBED, json={"input": list(range(10)), "vendor_field": 1}).status_code == 200  # exactly 40; extras kept
    assert len(_rows(gw)) == 1  # a refused request is neither logged nor counted

    # Pure checks: a single token array is one input, however long; a list of strings stays a list.
    assert validate_embeddings_request({"input": list(range(5000))}, None).inputs == [list(range(5000))]
    req = validate_embeddings_request({"input": ["a", "b"], "encoding_format": "base64", "dimensions": 3, "user": "u"})
    assert (req.inputs, req.encoding_format, req.dimensions, req.user) == (["a", "b"], "base64", 3, "u")


@pytest.mark.req("FR-1.51", "FR-1.50")
def test_embeddings_model_policy_off_switch_and_quota(gw_env):
    """Under RATCHET_MODEL_POLICY=strict only `ratchet` and the configured id are served; RATCHET_EMBEDDINGS_BASE=off
    turns the endpoint into a 404 `unsupported_endpoint`; every served call counts against the key's daily quota."""
    routing = ModelRouting("strict")
    assert check_embeddings_model(None, "m", routing) is None and check_embeddings_model("ratchet", "m", routing) is None
    assert check_embeddings_model("m", "m", routing) is None and check_embeddings_model("x", "m", ModelRouting()) == "x"
    with pytest.raises(APIError) as caught:
        check_embeddings_model("text-embedding-3-small", "m", routing)
    assert (caught.value.status, caught.value.code) == (404, "model_not_found")

    gw = _gateway(gw_env, RATCHET_MODEL_POLICY="strict")
    c = TestClient(gw.app)
    r = c.post(EMBED, json={"input": "a", "model": "text-embedding-3-small"})
    assert _error(r) == (404, "model_not_found", "model") and DEFAULT_EMBEDDINGS_MODEL in r.json()["error"]["message"]
    for model in (DEFAULT_EMBEDDINGS_MODEL, "ratchet", None):
        body = {"input": "a"} if model is None else {"input": "a", "model": model}
        r = c.post(EMBED, json=body)
        assert r.status_code == 200 and "X-Ratchet-Model-Alias" not in r.headers, model
    assert len(_rows(gw)) == 3

    gw = _gateway(gw_env, RATCHET_EMBEDDINGS_BASE="off")
    c = TestClient(gw.app)
    r = c.post(EMBED, json={"input": "a"})
    assert _error(r) == (404, "unsupported_endpoint", None) and "RATCHET_EMBEDDINGS_BASE=off" in r.json()["error"]["message"]
    assert DEFAULT_EMBEDDINGS_MODEL not in {m["id"] for m in c.get("/v1/models").json()["data"]}
    assert c.get(f"/v1/models/{DEFAULT_EMBEDDINGS_MODEL}").status_code == 404 and _rows(gw) == []

    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}", RATCHET_API_KEY_DAILY_REQUESTS="2")
    c = TestClient(gw.app)
    assert c.post(EMBED, json={"input": "a"}).status_code == 401
    assert c.post(EMBED, json={"input": "a"}, headers=ACME).headers["X-RateLimit-Remaining-Requests"] == "1"
    assert c.post(CHAT, json={"messages": [{"role": "user", "content": "hi"}]}, headers=ACME).status_code == 200
    r = c.post(EMBED, json={"input": "a"}, headers=ACME)
    assert _error(r) == (429, "daily_quota_exceeded", None)
    assert [row["client"] for row in _rows(gw)] == ["acme", "acme"]


@pytest.mark.req("FR-1.51")
def test_embeddings_settings_are_checked_at_startup(gw_env):
    s = Settings(embeddings_base="", teacher_base="https://tf.test/v1")
    assert s.embeddings_endpoint() == "https://tf.test/v1"
    assert Settings(embeddings_base=" OFF ").embeddings_endpoint() is None
    assert Settings(embeddings_base="http://tei.local:8080/v1").embeddings_endpoint() == "http://tei.local:8080/v1"
    for bad in (
        {"embeddings_base": "tei.local/v1"},
        {"embeddings_model": ""},
        {"embeddings_model": " padded "},
        {"embeddings_model": "ratchet/embeddings"},
        {"embeddings_model": "ratchet"},
    ):
        with pytest.raises(ValueError, match="RATCHET_EMBEDDINGS"):
            Settings(**bad).embeddings_endpoint()
    assert Settings(embeddings_base="off", embeddings_model="").embeddings_endpoint() is None  # off: the model is unused
    with pytest.raises(ValueError, match="RATCHET_EMBEDDINGS_MODEL"):
        _gateway(gw_env, RATCHET_MODEL_ALIASES=f"{DEFAULT_EMBEDDINGS_MODEL}={TRIAGE}")


@pytest.mark.req("FR-1.51", "INV-9")
def test_embeddings_model_is_priced_and_day1_checked(day1_check):
    """The default embeddings model has a price (input tokens only) and the day-1 check confirms it with a real key; it
    is kept out of DEFAULT_PRICES and the license registry, so it can never be mistaken for a teacher."""
    s = Settings(embeddings_model=DEFAULT_EMBEDDINGS_MODEL)
    assert s.embeddings_model in EMBEDDING_PRICES and s.price(s.embeddings_model) == EMBEDDING_PRICES[DEFAULT_EMBEDDINGS_MODEL]
    for model, price in EMBEDDING_PRICES.items():
        assert model in day1_check.WANT_EMBEDDINGS, (
            f"{model} has a price in config.py but scripts/day1_check.py does not check it"
        )
        assert price.output == 0 and model not in DEFAULT_PRICES and licenses.lookup(model) is None
    assert day1_check.EMBEDDINGS == DEFAULT_EMBEDDINGS_MODEL or "RATCHET_EMBEDDINGS_MODEL" in __import__("os").environ


@pytest.mark.req("FR-1.51", "INV-1")
def test_live_embeddings_forward_the_configured_model(gw_env, tmp_path, monkeypatch):
    """Whatever `model` a client sends, the upstream gets RATCHET_EMBEDDINGS_MODEL, with the inputs and `dimensions` as
    sent and floats asked for; usage passes through and prices the call; failures map by cause and log nothing."""
    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer("ok", in_tokens=7, embedding_dims=4)
    rt = _live(gw, tmp_path, monkeypatch, teacher)
    c = TestClient(gw.app)
    body = {"input": ["a", "bb"], "model": "text-embedding-3-large", "dimensions": 3, "encoding_format": "base64", "user": "u1"}
    r = c.post(EMBED, json=body)
    assert r.status_code == 200, r.text
    (call,) = teacher.calls_to("/embeddings")
    assert call.url == "https://teacher.test/v1/embeddings" and call.headers["authorization"] == "Bearer tf-teacher-api-key"
    assert call.json == {"model": DEFAULT_EMBEDDINGS_MODEL, "input": ["a", "bb"], "dimensions": 3}  # no user, no format
    data = r.json()["data"]
    assert [_floats(d["embedding"]) for d in data] == [[1.0, 1.0, 0.5], [2.0, 2.0, 0.5]]
    assert r.json()["usage"] == {"prompt_tokens": 7, "total_tokens": 7} and r.json()["model"] == DEFAULT_EMBEDDINGS_MODEL
    (row,) = [dict(x) for x in rt.ledger.db.execute("SELECT * FROM requests")]
    assert row["cost_usd"] == pytest.approx(EMBEDDING_PRICES[DEFAULT_EMBEDDINGS_MODEL].cost(7, 0))
    assert teacher.calls_to("/chat/completions") == []  # no cascade

    # Another endpoint, its own key and transport: the teacher's endpoint and key are not touched.
    other = FakeOpenAIServer("ok", in_tokens=2)
    rt = _live(
        gw,
        tmp_path,
        monkeypatch,
        teacher,
        db_path=str(tmp_path / "live2.db"),
        embeddings_base="https://embed.test/v1",
        embeddings_api_key="emb-key",
        embeddings_model="BAAI/bge-m3",
        transports={"embeddings": other.transport},
    )
    r = c.post(EMBED, json={"input": "a"})
    assert r.status_code == 200 and r.json()["data"][0]["embedding"] == [1.0, 1.0, 0.5, 0.5]
    (call,) = other.calls_to("/embeddings")
    assert call.url == "https://embed.test/v1/embeddings" and call.headers["authorization"] == "Bearer emb-key"
    assert call.json["model"] == "BAAI/bge-m3" and len(teacher.calls_to("/embeddings")) == 1
    # A model with no price in config.py is priced as the teacher: an overstatement, never a hidden cost.
    (row,) = [dict(x) for x in rt.ledger.db.execute("SELECT * FROM requests")]
    assert row["cost_usd"] == pytest.approx(DEFAULT_PRICES[OPEN_TEACHER].cost(2, 0))
    # Without RATCHET_EMBEDDINGS_KEY another provider gets no key at all, never the Token Factory one.
    keyless = FakeOpenAIServer("ok")
    _live(
        gw,
        tmp_path,
        monkeypatch,
        teacher,
        db_path=str(tmp_path / "live3.db"),
        embeddings_base="http://tei.local:8080/v1",
        transports={"embeddings": keyless.transport},
    )
    assert c.post(EMBED, json={"input": "a"}).status_code == 200
    (call,) = keyless.calls_to("/embeddings")
    assert call.url == "http://tei.local:8080/v1/embeddings" and "authorization" not in call.headers
    # The teacher's own endpoint spelled with a trailing slash still gets NEBIUS_API_KEY.
    same = FakeOpenAIServer("ok")
    _live(
        gw,
        tmp_path,
        monkeypatch,
        teacher,
        db_path=str(tmp_path / "live4.db"),
        embeddings_base="https://teacher.test/v1/",
        transports={"embeddings": same.transport},
    )
    assert c.post(EMBED, json={"input": "a"}).status_code == 200
    assert same.calls_to("/embeddings")[0].headers["authorization"] == "Bearer tf-teacher-api-key"

    def failing(status: int, payload: Any) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=payload)

        return httpx.MockTransport(handler)

    too_long = {"error": {"message": "input is too long", "type": "invalid_request_error", "code": "context_length_exceeded"}}
    cases = [
        (failing(400, too_long), 400, "context_length_exceeded"),
        (failing(401, {"error": {"message": "bad key"}}), 502, "upstream_auth_error"),
        (failing(404, {"error": {"message": "no such model"}}), 502, "upstream_not_found"),
        (failing(429, {"error": {"message": "slow down"}}), 429, "upstream_rate_limited"),
        (failing(503, {"error": {"message": "down"}}), 502, "upstream_unavailable"),
        (failing(504, {"error": {"message": "timeout"}}), 504, "upstream_timeout"),
        (failing(200, {"data": [{"embedding": [1.0]}]}), 502, "upstream_unavailable"),  # one vector for two inputs
        (failing(200, {"data": [{"embedding": [1.0]}, {"embedding": [1.0, 2.0]}]}), 502, "upstream_unavailable"),
    ]
    for i, (transport, status, code) in enumerate(cases):
        rt = _live(gw, tmp_path, monkeypatch, teacher, db_path=str(tmp_path / f"f{i}.db"), transports={"embeddings": transport})
        r = c.post(EMBED, json={"input": ["a", "b"]})
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), (i, r.text)
        assert rt.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


@pytest.mark.req("FR-1.51", "FR-2.15", "FR-1.50")
def test_embeddings_task_name_is_reserved(gw_env):
    """`embeddings` is where /v1/embeddings logs its requests, so no chat request, spec or alias may use it: the task's
    row in the stats is only ever passthrough requests. Every refusal comes before the quota and logs nothing."""
    assert tasks.EMBEDDINGS_TASK == "embeddings" and "embeddings" in tasks.RESERVED_NAMES
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    msgs = [{"role": "user", "content": "hi"}]
    for extra, headers, param in (
        ({}, {"X-Ratchet-Task": "embeddings"}, None),
        ({"metadata": {"ratchet_task": "embeddings"}}, {}, "metadata.ratchet_task"),
        ({"model": "ratchet/embeddings"}, {}, "model"),
        ({"model": "ratchet:embeddings"}, {}, "model"),
    ):
        for path, body in ((CHAT, {"messages": msgs}), (COMPLETE, {"prompt": "hi"}), (RESPOND, {"input": "hi"})):
            r = c.post(path, json={**body, **extra}, headers=headers)
            assert _error(r) == (400, "reserved_task", param), (path, extra, headers)
            assert "/v1/embeddings" in r.json()["error"]["message"]
    r = c.post("/openai/deployments/embeddings/chat/completions", json={"messages": msgs})
    assert _error(r)[:2] == (400, "reserved_task")
    assert _rows(gw) == []
    assert c.get("/v1/models/ratchet/embeddings").status_code == 404
    # No operator can define it either: not through the admin API, a tasks file or a model alias.
    r = c.put("/ratchet/tasks/embeddings", json={"description": "chat about embeddings"})
    assert r.status_code == 422 and "reserved" in r.json()["errors"][0]
    with pytest.raises(taskspec.SpecError, match="reserved"):
        taskspec.parse_document({"tasks": [{"name": "embeddings"}]}, "file")
    with pytest.raises(ValueError, match="reserved"):
        _gateway(gw_env, RATCHET_MODEL_ALIASES="text-embedding-3-small=embeddings")


@pytest.mark.req("FR-1.51")
def test_provider_parses_embeddings_strictly():
    """One vector per input, each found by its index, all of one length, finite and within float32; base64 accepted."""
    b64 = base64.b64encode(struct.pack("<3f", 0.5, -1.0, 2.0)).decode()
    data = {
        "data": [{"index": 1, "embedding": [3.0, 4.0, 5.0]}, {"index": 0, "embedding": b64}],
        "model": "served-name",
        "usage": {"prompt_tokens": 9},
    }
    out = _embeddings(data, "configured", 2, 12.0)
    assert out.vectors == [[0.5, -1.0, 2.0], [3.0, 4.0, 5.0]] and (out.model, out.prompt_tokens) == ("served-name", 9)
    unordered = _embeddings({"data": [{"embedding": [1]}, {"index": 5, "embedding": [2]}]}, "m", 2, 0.0)
    assert unordered.vectors == [[1.0], [2.0]] and unordered.model == "m" and unordered.prompt_tokens == 0
    bad = [
        None,
        {"data": "x"},
        {"data": [{"embedding": [1.0]}]},  # two inputs
        {"data": [{"embedding": [1.0]}, {"embedding": []}]},
        {"data": [{"embedding": [1.0]}, {"embedding": [1.0, 2.0]}]},
        {"data": [{"embedding": [1.0]}, {"embedding": ["1"]}]},
        {"data": [{"embedding": [1.0]}, {"embedding": [True]}]},
        {"data": [{"embedding": [1.0]}, {"embedding": [1e39]}]},
        {"data": [{"embedding": [1.0]}, {"embedding": "not base64!"}]},
        {"data": [{"embedding": [1.0]}, {"embedding": base64.b64encode(b"abc").decode()}]},
        {"data": [{"embedding": [1.0]}, "x"]},
    ]
    for body in bad:
        with pytest.raises(UpstreamError) as caught:
            _embeddings(body, "m", 2, 0.0)
        assert caught.value.status == 502 and caught.value.code == "bad_upstream_response", body


# ---------- /v1/completions ----------
@pytest.mark.req("FR-1.51", "INV-2", "INV-12")
def test_completions_answer_and_store_as_the_chat_request(gw_env):
    """A prompt is one user message through the chat path: the same answer, task, route and stored example as
    /v1/chat/completions with that message (the second request bumps the first one's example)."""
    sdk()
    from openai.types import Completion

    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    r = c.post(COMPLETE, json={"model": f"ratchet/{TRIAGE}", "prompt": TICKET})
    assert r.status_code == 200, r.text
    body = r.json()
    got = Completion.model_validate(body)
    assert got.object == "text_completion" and got.id.startswith("cmpl-") and got.model == OPEN_TEACHER
    assert [(ch.index, ch.logprobs, ch.finish_reason) for ch in got.choices] == [(0, None, "stop")]
    meta = body["ratchet"]
    assert (meta["served_by"], meta["task"], meta["task_source"]) == ("teacher", TRIAGE, "model")
    assert r.headers["X-Ratchet-Task"] == TRIAGE
    msgs = [{"role": "user", "content": TICKET}]
    (ex,) = _examples(gw)
    assert (ex["task"], ex["hash"], json.loads(ex["messages"]), ex["completion"]) == (
        TRIAGE,
        example_hash(TRIAGE, msgs),
        msgs,
        got.choices[0].text,
    )

    chat = c.post(CHAT, json={"model": f"ratchet/{TRIAGE}", "messages": msgs}).json()
    assert chat["choices"][0]["message"]["content"] == got.choices[0].text
    assert chat["usage"] == body["usage"]
    (ex,) = _examples(gw)
    assert ex["seen"] == 2  # the chat request is the same example
    assert [(row["task"], row["route"]) for row in _rows(gw)] == [(TRIAGE, "cold_start")] * 2


@pytest.mark.req("FR-1.51")
def test_completions_refuse_what_would_change_the_answer(gw_env):
    gw = _gateway(gw_env, RATCHET_MAX_INPUT_CHARS="50")
    c = TestClient(gw.app)
    cases = [
        ({"prompt": "hi", "suffix": "end"}, "unsupported_parameter", "suffix"),
        ({"prompt": "hi", "echo": True}, "unsupported_parameter", "echo"),
        ({"prompt": "hi", "best_of": 3}, "unsupported_parameter", "best_of"),
        ({"prompt": "hi", "best_of": 3, "n": 2}, "unsupported_parameter", "best_of"),
        ({"prompt": "hi", "suffix": 3}, "invalid_type", "suffix"),
        ({"prompt": "hi", "echo": "yes"}, "invalid_type", "echo"),
        ({"prompt": [1, 2, 3]}, "unsupported_value", "prompt"),
        ({"prompt": [[1, 2], [3]]}, "unsupported_value", "prompt"),
        ({"prompt": ["a", 2]}, "unsupported_value", "prompt"),
        ({"prompt": ["a", None]}, "invalid_type", "prompt[1]"),
        ({"prompt": []}, "invalid_value", "prompt"),
        ({"prompt": {"text": "hi"}}, "invalid_type", "prompt"),
        ({}, "missing_required_parameter", "prompt"),
        ({"prompt": ["a"] * (MAX_PROMPTS + 1)}, "unsupported_value", "prompt"),
        ({"prompt": "x" * 51}, "context_length_exceeded", "prompt"),
        ({"prompt": ["a", "x" * 51]}, "context_length_exceeded", "prompt[1]"),
        ({"prompt": "hi", "logprobs": 6}, "invalid_value", "logprobs"),
        ({"prompt": "hi", "logprobs": True}, "invalid_type", "logprobs"),
        ({"prompt": "hi", "temperature": 3}, "invalid_value", "temperature"),  # chat fields keep their names
        ({"prompt": "hi", "n": 9}, "unsupported_value", "n"),
        ({"prompt": "hi", "stop": 4}, "invalid_type", "stop"),
    ]
    for body, code, param in cases:
        assert _error(c.post(COMPLETE, json=body)) == (400, code, param), body
    assert _rows(gw) == []
    # What changes nothing is accepted: an empty suffix, echo false, best_of 1 or n.
    for extra in ({"suffix": ""}, {"suffix": None}, {"echo": False}, {"best_of": 1}, {"best_of": 2, "n": 2}):
        assert c.post(COMPLETE, json={"prompt": "hi", **extra}).status_code == 200, extra
    req = validate_completion_request({"prompt": ["a", "b"], "max_tokens": 5, "logprobs": 2, "seed": 3})
    assert [r.messages for r in req.requests()] == [[{"role": "user", "content": "a"}], [{"role": "user", "content": "b"}]]
    assert (req.chat.max_tokens, req.chat.logprobs, req.chat.top_logprobs, req.chat.seed, req.logprobs) == (5, True, 2, 3, 2)


@pytest.mark.req("FR-1.51", "FR-1.6")
def test_completions_batches_and_streams(gw_env):
    """Prompt p's choice j is choice p * n + j; usage adds up; each prompt is a request of its own, and a batch the
    daily quota cannot finish is refused before the first runs. The stream is text_completion chunks that the SDK's
    model accepts and that spell the same text, then the usage chunk and [DONE]."""
    sdk()
    from openai.types import Completion

    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}", RATCHET_API_KEY_DAILY_REQUESTS="3")
    c = TestClient(gw.app)
    body = {"prompt": ["first prompt", "second prompt"], "n": 2}
    r = c.post(COMPLETE, json=body, headers=ACME)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [ch["index"] for ch in data["choices"]] == [0, 1, 2, 3]
    assert "first prompt" in data["choices"][1]["text"] and "second prompt" in data["choices"][2]["text"]
    assert len(data["ratchet"]["prompts"]) == 2 and r.headers["X-RateLimit-Remaining-Requests"] == "1"
    rows = _rows(gw)
    assert len(rows) == 2 and data["usage"]["completion_tokens"] > 0
    assert data["usage"]["total_tokens"] == data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]
    r = c.post(COMPLETE, json=body, headers=ACME)  # 1 left, 2 needed: refused whole
    assert _error(r) == (429, "daily_quota_exceeded", None) and "has 1 left" in r.json()["error"]["message"]
    assert len(_rows(gw)) == 2

    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    plain = c.post(COMPLETE, json=body).json()
    r = c.post(COMPLETE, json={**body, "stream": True, "stream_options": {"include_usage": True}})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    lines = [ln[len("data: ") :] for ln in r.text.split("\n\n") if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(ln) for ln in lines[:-1]]
    # The SDK parses stream chunks as `Completion` without validating them: its model types finish_reason as never null,
    # but OpenAI's own chunks carry null until the last one. The chunks that end a choice, and the usage chunk, pass it.
    for ch in chunks:
        if all(c["finish_reason"] for c in ch["choices"]):
            Completion.model_validate(ch)
    texts: dict[int, str] = {}
    finishes: dict[int, str] = {}
    for ch in chunks[:-1]:
        (choice,) = ch["choices"]
        assert ch["usage"] is None and ch["object"] == "text_completion"
        texts[choice["index"]] = texts.get(choice["index"], "") + choice["text"]
        if choice["finish_reason"]:
            finishes[choice["index"]] = choice["finish_reason"]
    assert texts == {ch["index"]: ch["text"] for ch in plain["choices"]} and finishes == dict.fromkeys(range(4), "stop")
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"] == plain["usage"] and "prompts" in chunks[-1]["ratchet"]
    # Without include_usage the ratchet block rides on the last finish chunk.
    r = c.post(COMPLETE, json={"prompt": "hi", "stream": True})
    last = json.loads([ln for ln in r.text.split("\n\n") if ln.startswith("data: {")][-1][len("data: ") :])
    assert last["choices"][0]["finish_reason"] == "stop" and last["ratchet"]["task"] == "default" and "usage" not in last


@pytest.mark.req("FR-1.51", "INV-3", "INV-7")
def test_completions_batch_that_stops_after_its_first_prompt_is_not_retried(gw_env, tmp_path, monkeypatch):
    """A later prompt that fails leaves the earlier ones answered, logged, billed and stored, so the error says which and
    carries `X-Should-Retry: false` (no `Retry-After`): the OpenAI SDK, which resends a 5xx twice by default, sends the
    batch once, and the first prompt's example is seen once, not three times (seen >= FREQUENT would move a golden row
    into training, INV-3). The same holds for a later prompt refused by the quota a concurrent request used up, for a
    plain HTTP error, and for a batch that runs past the teacher's deadline, which stops before its next prompt. The
    first prompt's own error is the request's, retryable as a chat request's would be."""
    openai, _ = sdk()
    from ratchet.nightschool.dataset import FREQUENT

    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer("ok")
    concurrent: list[Any] = [lambda: None]  # a request a concurrent client sends with the same key while the batch runs

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.dumps(json.loads(request.content).get("messages"))
        if "FAIL" in text:
            return httpx.Response(503, json={"error": {"message": "down"}}, headers={"Retry-After": "2"})
        if "RACE" in text:
            concurrent[0]()
        return teacher(request)

    rt = _live(
        gw,
        tmp_path,
        monkeypatch,
        teacher,
        api_keys=f"acme:{KEY}",
        api_key_daily_requests=4,
        transports={"teacher": httpx.MockTransport(handler)},
    )
    calls: list[Any] = []
    client = make_openai_client(gw.app, calls=calls, max_retries=2, api_key=KEY)
    with pytest.raises(openai.APIStatusError) as caught:
        client.completions.create(model="ratchet", prompt=["good prompt", "FAIL prompt"])
    e = caught.value
    assert len(calls) == 1  # not resent
    assert (e.status_code, e.code) == (502, "teacher_unavailable")
    assert e.response.headers["x-should-retry"] == "false" and "retry-after" not in e.response.headers
    assert "stopped at prompt[1] of 2" in e.message and "prompt[0] was answered, logged and billed" in e.message
    (ex,) = _examples(gw)
    assert ex["seen"] == 1 < FREQUENT
    assert [(row["client"], row["served_by"], row["stored"]) for row in _rows(gw)] == [("acme", "teacher", 1)]
    assert rt.ledger.client_requests_since("acme", 0) == 1

    c = TestClient(gw.app)
    r = c.post(COMPLETE, json={"prompt": ["FAIL prompt", "good prompt"]}, headers=ACME)  # the first prompt: as chat
    assert _error(r) == (502, "teacher_unavailable", None) and "x-should-retry" not in r.headers
    assert r.headers["retry-after"] == "2" and "batch" not in r.json()["error"]["message"] and len(_rows(gw)) == 1

    # The quota had room for the whole batch when it started; a concurrent request used it up before prompt[2] ran
    # (counted by the spend meter, as the quota reads it: ADR 0063).
    concurrent[0] = lambda: rt.meter.add("acme", 0.0)
    r = c.post(COMPLETE, json={"prompt": ["RACE prompt", "second prompt", "third prompt"]}, headers=ACME)
    assert _error(r) == (429, "daily_quota_exceeded", None) and r.headers["x-should-retry"] == "false"
    assert "retry-after" not in r.headers and r.headers["x-ratelimit-limit-requests"] == "4"
    message = r.json()["error"]["message"]
    assert "stopped at prompt[2] of 3" in message and "prompt[0] to prompt[1] were answered" in message
    assert len(_rows(gw)) == 3

    # A batch past the teacher's deadline starts no further prompt.
    rt.key_limits["acme"] = replace(rt.key_limits["acme"], daily_requests=100)  # room again
    ticks = iter([0.0])  # the batch's start, then the deadline (also for the gate's throttled tick, ADR 0060)
    monkeypatch.setattr(rt, "clock", lambda: next(ticks, rt.settings.teacher_deadline_s))
    r = c.post(COMPLETE, json={"prompt": ["slow prompt", "never run"]}, headers=ACME)
    assert _error(r) == (504, "batch_deadline_exceeded", None) and r.headers["x-should-retry"] == "false"
    assert "before prompt[1] of 2" in r.json()["error"]["message"] and len(_rows(gw)) == 4

    # A plain HTTP error from a later prompt (mock mode's 400 for a request it cannot answer) stops the batch the same way.
    gw = _gateway(gw_env, RATCHET_MOCK_STRICT="1")
    r = TestClient(gw.app).post(COMPLETE, json={"model": f"ratchet/{TRIAGE}", "prompt": [TICKET, "not a demo request"]})
    assert r.status_code == 400 and r.headers["x-should-retry"] == "false", r.text
    assert "stopped at prompt[1] of 2" in r.json()["error"]["message"] and len(_rows(gw)) == 1


@pytest.mark.req("FR-1.51", "FR-1.14")
def test_completions_with_the_openai_sdk_and_legacy_logprobs(gw_env, tmp_path, monkeypatch):
    """The SDK's completions client end to end, streamed and not; `logprobs: k` returns the legacy shape, tokens that
    spell the text and offsets that start at the prompt's length."""
    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer("billing, high", logprob=-0.25, out_tokens=3)
    _live(gw, tmp_path, monkeypatch, teacher)
    client = make_openai_client(gw.app)
    got = client.completions.create(model=f"ratchet/{TRIAGE}", prompt="Charged twice?", logprobs=2, max_tokens=20)
    (choice,) = got.choices
    assert choice.text == "billing, high" and choice.finish_reason == "stop"
    lp = choice.logprobs
    assert "".join(lp.tokens) == choice.text and lp.token_logprobs == [-0.25] * 3
    assert lp.text_offset == [14, 14 + len(lp.tokens[0]), 14 + len(lp.tokens[0]) + len(lp.tokens[1])]
    assert all(len(alts) <= 2 for alts in lp.top_logprobs)
    sent = teacher.calls_to("/chat/completions")[-1].json
    assert sent["messages"] == [{"role": "user", "content": "Charged twice?"}] and sent["model"] == OPEN_TEACHER
    assert (sent["logprobs"], sent["top_logprobs"], sent["max_tokens"]) == (True, 2, 20)
    assert "prompt" not in sent and "echo" not in sent

    streamed = client.completions.create(model="ratchet", prompt="Charged twice?", stream=True)
    assert "".join(ch.choices[0].text for ch in streamed if ch.choices) == "billing, high"
    plain = client.completions.create(model="ratchet", prompt="Charged twice?")
    assert plain.choices[0].logprobs is None


@pytest.mark.req("FR-1.51")
def test_legacy_logprobs_shape():
    entries = [
        {"token": "bill", "logprob": -0.1, "bytes": [], "top_logprobs": [{"token": "bill", "logprob": -0.1}]},
        {"token": "ing", "logprob": -0.2, "bytes": [], "top_logprobs": []},
    ]
    assert legacy_logprobs(entries, 5) == {
        "tokens": ["bill", "ing"],
        "token_logprobs": [-0.1, -0.2],
        "top_logprobs": [{"bill": -0.1}, {}],
        "text_offset": [5, 9],
    }
    assert legacy_logprobs(None) is None and legacy_logprobs([]) == dict.fromkeys(
        ("tokens", "token_logprobs", "top_logprobs", "text_offset"), []
    )


# ---------- /v1/responses ----------
@pytest.mark.req("FR-1.51", "INV-12")
def test_responses_string_input_validates_with_the_sdk(gw_env):
    """A string input and instructions answer as the chat request [system, user] would, stored the same way; the
    response passes the SDK's `Response` model, and the SDK's responses client reads its `output_text`."""
    sdk()
    from openai.types.responses import Response

    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    r = c.post(RESPOND, json={"model": "ratchet", "input": "What is a refund window?", "instructions": "Be brief."})
    assert r.status_code == 200, r.text
    body = r.json()
    got = Response.model_validate(body)
    assert got.id.startswith("resp_") and got.status == "completed" and got.output_text.strip()
    assert (got.instructions, got.store, got.previous_response_id, got.tool_choice, got.tools) == (
        "Be brief.",
        False,
        None,
        "auto",
        [],
    )
    (item,) = got.output
    assert (item.type, item.role, item.status, item.content[0].type) == ("message", "assistant", "completed", "output_text")
    msgs = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "What is a refund window?"}]
    chat = c.post(CHAT, json={"messages": msgs}).json()
    assert chat["choices"][0]["message"]["content"] == got.output_text
    u = chat["usage"]
    assert (got.usage.input_tokens, got.usage.output_tokens, got.usage.total_tokens) == (
        u["prompt_tokens"],
        u["completion_tokens"],
        u["total_tokens"],
    )
    (ex,) = _examples(gw)
    assert json.loads(ex["messages"]) == msgs and ex["seen"] == 2 and body["ratchet"]["task"] == "default"

    client = make_openai_client(gw.app)
    resp = client.responses.create(model="ratchet", input=[{"role": "user", "content": "hello"}])
    assert resp.output_text and resp.object == "response"
    # A cut-off answer is `incomplete`, with the reason.
    short = Response.model_validate(c.post(RESPOND, json={"input": "Explain refunds at length", "max_output_tokens": 2}).json())
    assert (short.status, short.incomplete_details.reason, short.output[0].status) == (
        "incomplete",
        "max_output_tokens",
        "incomplete",
    )
    assert short.max_output_tokens == 2


@pytest.mark.req("FR-1.51")
def test_responses_refuse_what_needs_state(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    cases = [
        ({"input": "hi", "previous_response_id": "resp_1"}, "unsupported_parameter", "previous_response_id"),
        ({"input": "hi", "conversation": "conv_1"}, "unsupported_parameter", "conversation"),
        ({"input": "hi", "prompt": {"id": "pmpt_1"}}, "unsupported_parameter", "prompt"),
        ({"input": "hi", "background": True}, "unsupported_parameter", "background"),
        ({"input": "hi", "stream": True}, "unsupported_parameter", "stream"),
        ({"input": "hi", "tools": [{"type": "web_search"}]}, "unsupported_parameter", "tools[0].type"),
        ({"input": "hi", "tool_choice": {"type": "file_search"}}, "unsupported_parameter", "tool_choice"),
        ({"input": [{"type": "item_reference", "id": "msg_1"}]}, "unsupported_value", "input[0].type"),
        ({"input": [{"type": "web_search_call", "id": "ws_1"}]}, "unsupported_value", "input[0].type"),
        (
            {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "f1"}]}]},
            "unsupported_value",
            "input[0].content[0]",
        ),
        (
            {"input": [{"role": "user", "content": [{"type": "input_image", "file_id": "f1"}]}]},
            "unsupported_value",
            "input[0].content[0]",
        ),
        ({"input": [{"role": "robot", "content": "hi"}]}, "invalid_value", "input[0].role"),
        ({"input": [{"type": "reasoning", "id": "rs_1"}]}, "invalid_value", "input"),
        (
            {"input": [{"type": "function_call", "name": "f", "arguments": "{}"}]},
            "missing_required_parameter",
            "input[0].call_id",
        ),
        ({}, "missing_required_parameter", "input"),
        ({"input": 3}, "invalid_type", "input"),
        ({"input": "hi", "max_output_tokens": 0}, "invalid_value", "max_output_tokens"),
        ({"input": "hi", "text": {"format": {"type": "json_schema"}}}, "missing_required_parameter", "text.format.name"),
        ({"input": "hi", "text": {"format": {"type": "xml"}}}, "invalid_value", "text.format.type"),
        ({"input": "hi", "tools": [{"type": "function"}]}, "missing_required_parameter", "tools[0].name"),
        ({"input": "hi", "tool_choice": {"type": "function"}}, "missing_required_parameter", "tool_choice.name"),
        ({"input": "hi", "reasoning": "high"}, "invalid_type", "reasoning"),
        ({"input": "hi", "reasoning": {"effort": 3}}, "invalid_type", "reasoning.effort"),
        ({"input": "hi", "reasoning": {"effort": "extreme"}}, "invalid_value", "reasoning.effort"),  # the SDK refuses it
        ({"input": "hi", "temperature": 5}, "invalid_value", "temperature"),
    ]
    for body, code, param in cases:
        r = c.post(RESPOND, json=body)
        assert _error(r) == (400, code, param), (body, r.json())
    r = c.post(RESPOND, json={"input": "hi", "max_output_tokens": 0})
    assert "'max_output_tokens'" in r.json()["error"]["message"] and "max_tokens" not in r.json()["error"]["message"]
    assert _rows(gw) == []
    # What changes nothing is accepted: false flags, and the fields that have no effect here.
    ok = {"input": "hi", "stream": False, "background": False, "store": True, "include": ["reasoning.encrypted_content"]}
    assert c.post(RESPOND, json={**ok, "truncation": "auto", "service_tier": "auto"}).status_code == 200
    # An accepted effort is echoed, and the installed SDK's model parses the response for every effort it knows.
    sdk()
    from openai.types.responses import Response
    from openai.types.shared.reasoning_effort import ReasoningEffort

    def literals(t: Any) -> set[str]:
        return {a for a in typing.get_args(t) if isinstance(a, str)} | {x for a in typing.get_args(t) for x in literals(a)}

    known = literals(ReasoningEffort) & set(REASONING_EFFORTS)
    assert {"low", "medium", "high"} <= known
    for effort in sorted(known):
        r = c.post(RESPOND, json={"input": "hi", "reasoning": {"effort": effort}})
        assert Response.model_validate(r.json()).reasoning.effort == effort
    # No response is kept to retrieve, delete, cancel or list.
    for method, path in (
        ("GET", "/v1/responses/resp_1"),
        ("DELETE", "/v1/responses/resp_1"),
        ("POST", "/v1/responses/resp_1/cancel"),
        ("GET", "/v1/responses/resp_1/input_items"),
    ):
        r = c.request(method, path)
        assert _error(r) == (404, "unsupported_endpoint", None) and "no response state" in r.json()["error"]["message"]


@pytest.mark.req("FR-1.51")
def test_responses_input_items_become_chat_messages():
    schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    weather = {"type": "function", "name": "get_weather", "description": "Weather now", "parameters": schema, "strict": True}
    body = {
        "model": "ratchet/weather",
        "instructions": "You are terse.",
        "input": [
            {"role": "user", "content": "Weather and time in Paris?"},
            {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"city": "Paris"}'},
            {"type": "function_call", "call_id": "call_2", "name": "get_time", "arguments": "{}", "status": "completed"},
            {"type": "function_call_output", "call_id": "call_1", "output": "18 C"},
            {"type": "function_call_output", "call_id": "call_2", "output": [{"type": "input_text", "text": "14:00"}]},
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "18 C, 14:00.", "annotations": []}],
            },
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Answer in JSON."}]},
            {"role": "user", "content": [{"type": "input_text", "text": "And"}, {"type": "input_text", "text": "tomorrow?"}]},
        ],
        "tools": [weather],
        "tool_choice": {"type": "function", "name": "get_weather"},
        "parallel_tool_calls": False,
        "text": {"format": {"type": "json_schema", "name": "forecast", "schema": schema, "strict": True}, "verbosity": "low"},
        "max_output_tokens": 50,
        "temperature": 0.2,
        "reasoning": {"effort": "low", "summary": "auto"},
        "metadata": {"ratchet_task": "weather"},
        "include": ["message.output_text.logprobs"],
        "truncation": "auto",
        "store": True,
        "n": 3,  # a chat field the Responses API does not have: ignored
        "top_k": 5,  # unknown: kept, like a chat request's vendor field
    }
    req = validate_responses_request(body)
    chat = req.chat
    assert chat.messages == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Weather and time in Paris?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
                {"id": "call_2", "type": "function", "function": {"name": "get_time", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "18 C"},
        {"role": "tool", "tool_call_id": "call_2", "content": "14:00"},
        {"role": "assistant", "content": [{"type": "text", "text": "18 C, 14:00."}]},
        {"role": "developer", "content": [{"type": "text", "text": "Answer in JSON."}]},
        {"role": "user", "content": [{"type": "text", "text": "And"}, {"type": "text", "text": "tomorrow?"}]},
    ]
    normalized, _ = normalize_messages(chat.messages)
    assert normalized[-1] == {"role": "user", "content": "And\ntomorrow?"} and normalized[-2]["role"] == "system"
    assert chat.tools == [
        {
            "type": "function",
            "function": {"name": "get_weather", "description": "Weather now", "parameters": schema, "strict": True},
        }
    ]
    assert chat.tool_choice == {"type": "function", "function": {"name": "get_weather"}}
    assert chat.response_format == {"type": "json_schema", "json_schema": {"name": "forecast", "schema": schema, "strict": True}}
    assert (chat.max_tokens, chat.temperature, chat.reasoning_effort, chat.parallel_tool_calls) == (50, 0.2, "low", False)
    assert (chat.model, chat.task, chat.n, chat.extra) == ("ratchet/weather", "weather", 1, {"top_k": 5})
    # The response echoes the Responses-shaped settings as sent.
    assert (req.tools, req.tool_choice, req.parallel_tool_calls, req.max_output_tokens) == (
        [weather],
        {"type": "function", "name": "get_weather"},
        False,
        50,
    )
    assert req.text["format"]["name"] == "forecast" and req.instructions == "You are terse."
    # A JSON-object format and a plain one; an image URL part (the teacher reads it under RATCHET_TEACHER_MODALITIES).
    assert validate_responses_request({"input": "x", "text": {"format": {"type": "json_object"}}}).chat.response_format == {
        "type": "json_object"
    }
    assert validate_responses_request({"input": "x", "text": {"format": {"type": "text"}}}).chat.response_format is None
    image = {"type": "input_image", "image_url": "https://img.test/a.png", "detail": "low"}
    req = validate_responses_request({"input": [{"role": "user", "content": [image]}]})
    assert req.chat.messages[0]["content"] == [
        {"type": "image_url", "image_url": {"url": "https://img.test/a.png", "detail": "low"}}
    ]


@pytest.mark.req("FR-1.51")
def test_responses_tool_loop_becomes_the_chat_conversation():
    """A response's output sent back as input (its text, then its calls, with reasoning anywhere) is one assistant
    message with those tool calls, as the chat request of the same loop would be, so it is stored as that request."""
    loop = [
        {"role": "user", "content": "Weather in Paris and Rome?"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Checking.", "annotations": []}]},
        {"type": "function_call", "call_id": "c1", "name": "get_weather", "arguments": '{"city": "Paris"}'},
        {"type": "reasoning", "id": "rs_2", "summary": []},
        {"type": "function_call", "call_id": "c2", "name": "get_weather", "arguments": '{"city": "Rome"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "18 C"},
        {"type": "function_call_output", "call_id": "c2", "output": "21 C"},
        {"role": "assistant", "content": "Paris 18 C, Rome 21 C."},
        {"type": "function_call", "call_id": "c3", "name": "get_weather", "arguments": '{"city": "Oslo"}'},
    ]
    calls = [
        {"id": cid, "type": "function", "function": {"name": "get_weather", "arguments": json.dumps({"city": city})}}
        for cid, city in (("c1", "Paris"), ("c2", "Rome"), ("c3", "Oslo"))
    ]
    messages = validate_responses_request({"input": loop}).chat.messages
    assert messages == [
        {"role": "user", "content": "Weather in Paris and Rome?"},
        {"role": "assistant", "content": [{"type": "text", "text": "Checking."}], "tool_calls": calls[:2]},
        {"role": "tool", "tool_call_id": "c1", "content": "18 C"},
        {"role": "tool", "tool_call_id": "c2", "content": "21 C"},
        {"role": "assistant", "content": "Paris 18 C, Rome 21 C.", "tool_calls": calls[2:]},
    ]
    normalized, _ = normalize_messages(messages)
    assert normalized[1] == {"role": "assistant", "content": "Checking.", "tool_calls": calls[:2]}
    # Calls after a tool result, or after a user message, start an assistant message of their own.
    after_tool = validate_responses_request({"input": loop[:8] + loop[-1:]}).chat.messages
    assert after_tool[-1] == {"role": "assistant", "content": None, "tool_calls": calls[2:]}
    after_user = validate_responses_request({"input": loop[:1] + loop[-1:]}).chat.messages
    assert after_user[-1] == {"role": "assistant", "content": None, "tool_calls": calls[2:]}


@pytest.mark.req("FR-1.51")
def test_response_output_carries_function_calls():
    """A choice's tool calls become `function_call` items whose `call_id` a client sends back with the result; the
    response object passes the SDK's model with them."""
    calls = [
        {"id": "call_9", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
        {"id": "", "type": "function", "function": {"name": "get_time", "arguments": {"tz": "CET"}}},
    ]
    items = response_output("", "tool_calls", calls)
    assert [i["type"] for i in items] == ["function_call", "function_call"]
    assert (items[0]["call_id"], items[0]["name"], items[0]["arguments"]) == ("call_9", "get_weather", '{"city": "Paris"}')
    assert items[1]["call_id"].startswith("call_") and items[1]["arguments"] == '{"tz": "CET"}'
    both = response_output("Checking.", "tool_calls", calls[:1])
    assert [i["type"] for i in both] == ["message", "function_call"]
    assert response_output("", "stop")[0]["content"][0]["text"] == ""  # an empty answer is still a message
    sdk()
    from openai.types.responses import Response

    req = validate_responses_request({"input": "x", "tools": [{"type": "function", "name": "get_weather"}]})
    obj = response_object(req, created=1, model="m", output=items, finish_reason="tool_calls", usage=responses_usage(5, 7, 2, 1))
    got = Response.model_validate(obj)
    assert (
        got.status == "completed" and got.output[0].call_id == "call_9" and got.usage.output_tokens_details.reasoning_tokens == 2
    )
    assert got.usage.input_tokens_details.cached_tokens == 1 and got.usage.total_tokens == 12
    assert response_object(req, created=1, model="m", output=items, finish_reason="content_filter", usage={})[
        "incomplete_details"
    ] == {"reason": "content_filter"}


# ---------- typed 404s ----------
@pytest.mark.req("FR-1.51", "FR-1.10")
def test_moderations_and_unknown_paths_get_typed_404s(gw_env):
    """Moderations is a known OpenAI endpoint this gateway does not serve: a 404 `unsupported_endpoint` with the docs
    link, after the key check, which the SDK raises as NotFoundError. Any other path is `unknown_url`."""
    openai, _ = sdk()
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    r = c.post("/v1/moderations", json={"input": "hello"})
    assert _error(r) == (404, "unsupported_endpoint", None)
    assert "https://github.com/harshbhx/ratchet/blob/main/docs/API.md#openai-endpoints" in r.json()["error"]["message"]
    with pytest.raises(openai.NotFoundError) as caught:
        make_openai_client(gw.app).moderations.create(input="hello")
    assert caught.value.code == "unsupported_endpoint"
    for method, path in (("POST", "/v1/images/generations"), ("GET", "/v1/files"), ("POST", "/v1/audio/speech")):
        r = c.request(method, path)
        assert _error(r) == (404, "unknown_url", None) and f"{method} {path}" in r.json()["error"]["message"]
    assert c.get(EMBED).status_code == 405 and c.get(EMBED).json()["error"]["type"] == "invalid_request_error"

    gw = _gateway(gw_env, RATCHET_API_KEYS=f"acme:{KEY}")
    c = TestClient(gw.app)
    for path in ("/v1/moderations", EMBED, COMPLETE, RESPOND, "/v1/responses/resp_1"):
        assert _error(c.post(path, json={})) == (401, "invalid_api_key", None), path
    assert c.post("/v1/moderations", json={}, headers=ACME).status_code == 404


@pytest.mark.req("FR-1.51")
def test_adjacent_endpoints_reach_the_student(gw_env):
    """With a live version, a completion and a response are served by the student exactly as the chat request is: the
    cascade, not a side path."""
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    for _ in range(3):  # cold start: the teacher answers and the example is stored
        assert c.post(COMPLETE, json={"model": f"ratchet/{TRIAGE}", "prompt": TICKET}).status_code == 200
    gw.rt.fleet.knowledge["student-v1"] = {"templates": {tickets.REGISTRY[TICKET].template: 500}, "texts": {TICKET}}
    _version(gw.rt.ledger, "live", {"threshold": 0.0})
    r = c.post(COMPLETE, json={"model": f"ratchet/{TRIAGE}", "prompt": TICKET}).json()
    assert r["ratchet"]["served_by"] == "student" and r["model"] == "student-v1"
    r = c.post(RESPOND, json={"model": f"ratchet/{TRIAGE}", "input": TICKET}).json()
    assert r["ratchet"]["served_by"] == "student" and r["model"] == "student-v1" and r["output"][0]["content"][0]["text"]
