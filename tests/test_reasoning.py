"""Nemotron reasoning control by role (FR-1.90, ADR 0130): the settings, what each role sends, where the kwargs go in
the body, and that a client never sets them. Offline: `httpx.MockTransport` and the fakes."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, _version
from fastapi.testclient import TestClient

from ratchet import reasoning
from ratchet.config import Settings
from ratchet.nightschool import dataset
from ratchet.providers import GenParams, GenPolicy, OpenAICompatProvider, parse_param_allowlist

CHAT = "/v1/chat/completions"
FALLBACK = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
OFF = {"enable_thinking": False}
LABELS = json.dumps({"category": "billing", "priority": "high"})


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"Where is my refund for order {tag}?"}]


def _live(tmp_path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "r.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "k",
        "teacher_model": OPEN_TEACHER,
        "fallback_student_model": FALLBACK,
        "student_base": "",
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.5,
        "tavily_api_key": "",
        "api_keys": "",
        "mock_warmup_days": 0,
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,
        "stats_ttl_s": 0.0,
    }
    return Settings(**{**base, **overrides})


@pytest.mark.req("FR-1.90")
def test_reasoning_modes_and_settings():
    assert reasoning.parse(" OFF ", "X") == "off" and reasoning.parse("", "X") == "default"
    assert reasoning.parse("", "X", default="off") == "off"
    for raw, env in (("loud", "RATCHET_TEACHER_REASONING"), ("", "RATCHET_FOO")):
        if raw:
            with pytest.raises(ValueError, match=env):
                reasoning.parse(raw, env)
    for bad in ("on", "medium"):
        with pytest.raises(ValueError, match="trained without reasoning"):
            reasoning.parse(bad, "RATCHET_STUDENT_REASONING", reasoning.STUDENT_MODES, "off")
    with pytest.raises(TypeError):
        reasoning.KWARGS["off"]["enable_thinking"] = True  # type: ignore[index]
    assert reasoning.template_kwargs("default") is None
    assert reasoning.TRAIN_TEMPLATE_KWARGS == dataset.TRAIN_TEMPLATE_KWARGS == OFF
    for field in reasoning.FIELDS:  # plain dicts: json.dumps refuses a mappingproxy
        json.dumps(reasoning.body_fields(reasoning.KWARGS["medium"], field))
    assert reasoning.body_fields(OFF) == {"chat_template_kwargs": OFF}
    assert reasoning.body_fields(OFF, "extra_body") == {"extra_body": {"chat_template_kwargs": OFF}}
    assert reasoning.body_fields(None) == {}

    p = Settings(student_reasoning="", teacher_reasoning="Medium", check_reasoning="").reasoning_policy()
    assert (p.student, p.fallback_student, p.serve, p.check, p.field) == (
        "off",
        "off",
        "medium",
        "medium",
        "chat_template_kwargs",
    )
    assert Settings(teacher_reasoning="medium", check_reasoning="off").reasoning_policy().check == "off"
    for name, value in (
        ("student_reasoning", "on"),
        ("fallback_student_reasoning", "medium"),
        ("teacher_reasoning", "x"),
        ("check_reasoning", "x"),
        ("teacher_reasoning_field", "body"),
    ):
        env = "RATCHET_" + name.upper()
        with pytest.raises(ValueError, match=env):
            Settings(**{name: value}).reasoning_policy()
        with pytest.raises(ValueError, match=env):  # startup (the router and the gateway) stops on it
            Settings(**{name: value}).gen_policy()


@pytest.mark.req("FR-1.90", "FR-1.13")
def test_genpolicy_sets_template_kwargs_by_role():
    plan = GenPolicy().plan(None)
    assert plan.student.template_kwargs == OFF and plan.serve.template_kwargs is None and plan.check.template_kwargs is None
    gen = GenPolicy(serve_reasoning="medium", check_reasoning="off")
    assert gen.teacher(None, "serve").template_kwargs == reasoning.KWARGS["medium"]
    assert gen.teacher(None, "audit").template_kwargs == OFF and gen.teacher(None, "arbitrate").template_kwargs == OFF
    assert GenPolicy(student_reasoning="default").student(None).template_kwargs is None
    with pytest.raises(ValueError):
        GenPolicy(student_reasoning="on")
    with pytest.raises(ValueError):
        GenPolicy(serve_reasoning="loud")
    p = GenParams(template_kwargs=OFF, max_tokens=5)
    assert p.only(frozenset({"max_tokens"})).template_kwargs == OFF and p.unstorable() is None
    assert "template_kwargs" not in p.to_body() and "chat_template_kwargs" not in p.to_body()


@pytest.mark.req("FR-1.90", "FR-1.13")
def test_provider_places_template_kwargs_by_field():
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})

    params = GenParams(template_kwargs=OFF, temperature=0.9)
    for field, key in (("chat_template_kwargs", None), ("extra_body", "extra_body")):
        provider = OpenAICompatProvider("https://x.test/v1", "k", transport=httpx.MockTransport(handler), reasoning_field=field)
        provider.complete(_ask(), "m", n=2, temperature=0.0, params=params)
        provider.complete(_ask(), "m", params=GenParams())
        provider.complete(_ask(), "m")
        with_kwargs, without, bare = bodies[-3:]
        if key is None:
            assert with_kwargs["chat_template_kwargs"] == OFF and "extra_body" not in with_kwargs
        else:
            assert with_kwargs["extra_body"] == {"chat_template_kwargs": OFF} and "chat_template_kwargs" not in with_kwargs
        assert (with_kwargs["n"], with_kwargs["temperature"]) == (2, 0.0)  # the explicit arguments still win
        for body in (without, bare):
            assert "chat_template_kwargs" not in body and "extra_body" not in body
    with pytest.raises(ValueError, match="reasoning_field"):
        OpenAICompatProvider("https://x.test/v1", "k", reasoning_field="body")


@pytest.mark.req("FR-1.90", "FR-1.13")
def test_client_template_kwargs_never_reach_a_model(gw_env, tmp_path, monkeypatch):
    for bad in ("chat_template_kwargs", "extra_body"):
        with pytest.raises(ValueError, match=bad):
            parse_param_allowlist(f"all,{bad}", frozenset(), "RATCHET_TEACHER_PARAMS")
    gw = gw_env()
    teacher, student = FakeOpenAIServer(LABELS), FakeOpenAIServer(LABELS)
    rt = gw.Runtime(
        _live(tmp_path, student_base="https://student.test/v1", confidence_threshold=0.99),
        transports={"teacher": teacher.transport, "student": student.transport},
    )
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.99})
    r = TestClient(gw.app).post(
        CHAT, json={"messages": _ask(1), "chat_template_kwargs": {"enable_thinking": True}, "extra_body": {"x": 1}}
    )
    assert r.status_code == 200, r.text
    assert student.calls and all(c.json["chat_template_kwargs"] == OFF for c in student.calls)
    assert teacher.calls and all("chat_template_kwargs" not in c.json and "extra_body" not in c.json for c in teacher.calls)
    assert not [e for e in rt.ledger.events() if e["kind"] == "skip_example"]


@pytest.mark.req("FR-1.90", "FR-8.3", "INV-7")
def test_hosted_fallback_student_reasoning(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    client = TestClient(gw.app)

    def run(name: str, server: FakeOpenAIServer, **settings: Any):
        rt = gw.Runtime(
            _live(tmp_path, db_path=str(tmp_path / f"{name}.db"), **settings), transports={"teacher": server.transport}
        )
        monkeypatch.setattr(gw, "rt", rt)
        _version(rt.ledger, "live", {"threshold": 0.99})  # the calibrated value belongs to an adapter, not the fallback
        return rt, client.post(CHAT, json={"messages": _ask(name)})

    def student_bodies(server: FakeOpenAIServer):
        return [c.json for c in server.calls if c.json["model"] == FALLBACK]

    hosted = FakeOpenAIServer(LABELS)
    _, r = run("top", hosted)
    assert r.status_code == 200 and student_bodies(hosted)
    assert all(b["chat_template_kwargs"] == OFF for b in student_bodies(hosted))
    teacher_calls = [c.json for c in hosted.calls if c.json["model"] == OPEN_TEACHER]
    assert all("chat_template_kwargs" not in b for b in teacher_calls)

    hosted = FakeOpenAIServer(LABELS)
    run("nested", hosted, teacher_reasoning_field="extra_body")
    assert all(
        b["extra_body"] == {"chat_template_kwargs": OFF} and "chat_template_kwargs" not in b for b in student_bodies(hosted)
    )

    hosted = FakeOpenAIServer(LABELS)
    run("default", hosted, fallback_student_reasoning="default")
    assert student_bodies(hosted) and all("chat_template_kwargs" not in b for b in student_bodies(hosted))

    # A Token Factory that refuses the field: the student fails fast and the request escalates (INV-7).
    def refuse(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field", "type": "invalid_request_error"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": LABELS}, "finish_reason": "stop"}]})

    class Refusing(FakeOpenAIServer):
        @property
        def transport(self):
            return httpx.MockTransport(refuse)

    rt, r = run("refused", Refusing())
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert rt.settings.confidence_threshold == 0.5  # the threshold used is still the global one


@pytest.mark.req("FR-1.90", "FR-1.13")
def test_teacher_roles_send_nothing_by_default_and_follow_settings(gw_env, tmp_path, monkeypatch):
    gw = gw_env()

    def bodies(tag: str, **settings: Any) -> tuple[list[dict], list[dict]]:
        """(escalation bodies, audit bodies): a student that doubts itself escalates; a confident one is audited."""
        out = []
        for threshold, audit_rate in ((0.99999, 0.0), (0.0, 1.0)):
            teacher = FakeOpenAIServer(LABELS)
            db = str(tmp_path / f"{tag}{threshold}.db")
            student = FakeOpenAIServer(LABELS, logprob=-0.5)
            rt = gw.Runtime(
                _live(tmp_path, db_path=db, student_base="https://student.test/v1", audit_rate=audit_rate, **settings),
                transports={"teacher": teacher.transport, "student": student.transport},
            )
            monkeypatch.setattr(gw, "rt", rt)
            _version(rt.ledger, "live", {"threshold": threshold})
            assert TestClient(gw.app).post(CHAT, json={"messages": _ask(tag)}).status_code == 200
            out.append([c.json for c in teacher.calls])
        return out[0], out[1]

    served, audited = bodies("plain")
    assert served and audited and all("chat_template_kwargs" not in b for b in served + audited)
    served, audited = bodies("medium", teacher_reasoning="medium")
    assert all(b["chat_template_kwargs"] == reasoning.KWARGS["medium"] for b in served + audited)
    served, audited = bodies("check", check_reasoning="off")
    assert all("chat_template_kwargs" not in b for b in served) and all(b["chat_template_kwargs"] == OFF for b in audited)


@pytest.mark.req("FR-1.90")
def test_stats_reasoning_block(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    rt = gw.Runtime(_live(tmp_path, teacher_reasoning="off"), transports={"teacher": FakeOpenAIServer(LABELS).transport})
    monkeypatch.setattr(gw, "rt", rt)
    block = TestClient(gw.app).get("/ratchet/stats").json()["reasoning"]
    assert block == {"student": "off", "fallback_student": "off", "serve": "off", "check": "off", "field": "chat_template_kwargs"}
