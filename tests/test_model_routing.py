"""Task selection without a header (FR-1.50, ADR 0048): the `model` field (`ratchet/<task>`, model aliases, the lenient
and strict policies), the tasks' match rules, auto tasks per system prompt, `/v1/models` and `GET /v1/models/{id}` in
the OpenAI SDK's shape, the Azure-style route and its `api-key` header, and the rule that the `model` field never selects
an upstream model: the teacher always gets RATCHET_TEACHER_MODEL and the student its adapter name."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import CLOSED_TEACHER, OPEN_TEACHER, FakeOpenAIServer, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

import ratchet.ledger as ledger_module
from ratchet import tasks, taskspec
from ratchet.auth import bearer
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.openai_compat import (
    APIError,
    ModelRouting,
    check_model,
    classify_model,
    header_value,
    parse_aliases,
    resolve_task,
    system_prompt_task,
    validate_chat_request,
)

CHAT = "/v1/chat/completions"
ACME_KEY = "acme-api-key-0123456789abcdef"
GLOBEX_KEY = "globex-api-key-0123456789abcdef"
KEYS = f"acme:{ACME_KEY},globex:{GLOBEX_KEY}"
ACME = {"Authorization": f"Bearer {ACME_KEY}"}
GLOBEX = {"Authorization": f"Bearer {GLOBEX_KEY}"}
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "Checking the charge now."})
HI = [{"role": "user", "content": "hi"}]
# Everything these tests depend on, pinned so the shell's variables cannot move a result.
ROUTING_ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_ADMIN_KEYS": "",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_MODEL_POLICY": "lenient",
    "RATCHET_MODEL_ALIASES": "",
    "RATCHET_AUTO_TASK": "off",
    "RATCHET_MAX_AUTO_TASKS": "200",
    "RATCHET_UNKNOWN_TASKS": "distill",
    "RATCHET_TASKS_FILE": "",
    "RATCHET_MOCK_STRICT": "0",
    "RATCHET_MERGE_SYSTEM": "0",
}


def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**ROUTING_ENV, **env})


def _rows(gw: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in gw.rt.ledger.db.execute("SELECT * FROM requests ORDER BY id")]


def _tasks_file(tmp_path: Path, *specs: dict[str, Any]) -> str:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": list(specs)}), encoding="utf-8")
    return str(path)


def _system(prompt: str, user: str = "Order 4821 never arrived.") -> list[dict[str, Any]]:
    return [{"role": "system", "content": prompt}, {"role": "user", "content": user}]


def _req(**body: Any) -> Any:
    return validate_chat_request({"messages": HI, **body})


def _auto(prompt: str) -> str:
    return "auto-sys-" + hashlib.sha256(prompt.encode()).hexdigest()[:10]


# ---------- the pure rules ----------
@pytest.mark.req("FR-1.50", "FR-1.3")
def test_resolution_order_is_header_metadata_model_alias_then_default():
    """The rules in their order (docs/API.md#task-resolution): each case adds one more source, and the first wins."""
    routing = ModelRouting(aliases={"gpt-4o-mini": "page-read"})
    meta = {"ratchet_task": "source-triage"}
    cases = [  # (header, body fields, (task, source))
        ("research-plan", {"metadata": meta, "model": f"ratchet/{TRIAGE}"}, ("research-plan", "header")),
        (None, {"metadata": meta, "model": f"ratchet/{TRIAGE}"}, ("source-triage", "metadata")),
        (None, {"model": f"ratchet/{TRIAGE}"}, (TRIAGE, "model")),
        (None, {"model": f"ratchet:{TRIAGE}"}, (TRIAGE, "model")),
        (None, {"model": "gpt-4o-mini"}, ("page-read", "alias")),
        (None, {"model": "ratchet"}, ("default", "default")),
        (None, {"model": "gpt-4o"}, ("default", "default")),
        (None, {}, ("default", "default")),
        ("", {"model": f"ratchet/{TRIAGE}"}, (TRIAGE, "model")),  # an empty header counts as none, as before
    ]
    for header, body, want in cases:
        assert resolve_task(_req(**body), header, routing) == want, (header, body)
    # On the Azure-style route the deployment takes the model's place, and the body's model is not read.
    req = _req(model="gpt-4o-mini")
    assert resolve_task(req, None, routing, deployment=TRIAGE) == (TRIAGE, "deployment")
    assert resolve_task(req, None, routing, deployment="gpt-4o-mini") == ("page-read", "alias")
    assert resolve_task(req, None, routing, deployment=f"ratchet:{TRIAGE}") == (TRIAGE, "deployment")
    assert resolve_task(req, None, routing, deployment="ratchet") == ("default", "default")
    assert resolve_task(_req(metadata=meta), None, routing, deployment=TRIAGE) == ("source-triage", "metadata")
    with pytest.raises(APIError) as caught:
        resolve_task(req, None, routing, deployment="has space")
    assert (caught.value.status, caught.value.code) == (404, "model_not_found")


@pytest.mark.req("FR-1.50")
def test_classify_and_check_model_under_both_policies():
    lenient, strict = ModelRouting(aliases={"gpt-4o-mini": TRIAGE}), ModelRouting("strict", {"gpt-4o-mini": TRIAGE})
    kinds = {
        None: ("ratchet", None),
        "": ("ratchet", None),
        "ratchet": ("ratchet", None),
        "ratchet/ticket-triage": ("task", TRIAGE),
        "ratchet:a.b_c-9": ("task", "a.b_c-9"),
        "gpt-4o-mini": ("alias", TRIAGE),
        "gpt-4o": ("unknown", None),
        "Ratchet": ("unknown", None),  # model ids are case-sensitive, as OpenAI's are
        "ratchet/": ("invalid", None),
        "ratchet/has space": ("invalid", None),
        "ratchet/" + "x" * 65: ("invalid", None),
        "ratchet/a/b": ("invalid", None),
    }
    for model, want in kinds.items():
        assert tuple(classify_model(model, lenient)) == want, model
    for model in (None, "ratchet", "ratchet/ticket-triage", "gpt-4o-mini"):
        assert check_model(model, lenient) is None and check_model(model, strict) is None
    assert check_model("gpt-4o", lenient) == "gpt-4o"  # served as ratchet; the gateway echoes it in a header
    with pytest.raises(APIError) as caught:
        check_model("gpt-4o", strict)
    assert caught.value.body()["error"] == {
        "message": "The model 'gpt-4o' does not exist.",
        "type": "invalid_request_error",
        "param": "model",
        "code": "model_not_found",
    }
    for policy in (lenient, strict):  # a mistake inside Ratchet's namespace is a 404 under either policy
        with pytest.raises(APIError, match="a Ratchet model is 'ratchet' or 'ratchet/<task>'") as caught:
            check_model("ratchet/has space", policy)
        assert (caught.value.status, caught.value.code) == (404, "model_not_found")
    with pytest.raises(APIError) as caught:  # a long id is quoted in part only
        check_model("m" * 5000, strict)
    assert len(caught.value.message) < 150


@pytest.mark.req("FR-1.50")
def test_alias_and_routing_settings_are_checked_at_startup():
    assert parse_aliases(" gpt-4o-mini = ticket-triage , openai/gpt-4o=default,") == {
        "gpt-4o-mini": TRIAGE,
        "openai/gpt-4o": "default",
    }
    assert parse_aliases("") == {}
    bad = {
        "gpt-4o": "is not model=task",
        "=ticket-triage": "is not a model id",
        "gpt 4o=x": "is not a model id",
        "ratchet=default": "is a Ratchet model id already",
        "ratchet/x=default": "is a Ratchet model id already",
        "gpt-4o=has space": "which is not a task name",
        "gpt-4o=": "which is not a task name",
        "a=x,a=y": "'a' appears twice",
    }
    for spec, message in bad.items():
        with pytest.raises(ValueError, match="RATCHET_MODEL_ALIASES") as caught:
            parse_aliases(spec)
        assert message in str(caught.value), spec
    with pytest.raises(ValueError, match="at most 256 aliases"):
        parse_aliases(",".join(f"m{i}=default" for i in range(257)))

    s = Settings(model_policy=" STRICT ", model_aliases="gpt-4o=default", auto_task="System_Prompt")
    assert s.model_routing() == ModelRouting("strict", {"gpt-4o": "default"}, "system_prompt")
    assert Settings(model_policy="", auto_task="").model_routing() == ModelRouting()  # set but empty: the defaults
    for field, value, env in (
        ("model_policy", "loose", "RATCHET_MODEL_POLICY"),
        ("auto_task", "yes", "RATCHET_AUTO_TASK"),
        ("model_aliases", "gpt-4o", "RATCHET_MODEL_ALIASES"),
    ):
        with pytest.raises(ValueError, match=env):
            Settings(**{field: value}).model_routing()
    for cap in (-1, True, 2.5):
        with pytest.raises(ValueError, match="RATCHET_MAX_AUTO_TASKS"):
            Settings(max_auto_tasks=cap).auto_task_cap()
    assert Settings(max_auto_tasks=0).auto_task_cap() == 0
    with pytest.raises(ValueError, match="RATCHET_MODEL_POLICY"):
        ModelRouting("loose")


@pytest.mark.req("FR-1.50")
def test_bad_routing_setting_stops_the_gateway(gw_env):
    for env in ({"RATCHET_MODEL_POLICY": "loose"}, {"RATCHET_MODEL_ALIASES": "gpt-4o"}, {"RATCHET_MAX_AUTO_TASKS": "-1"}):
        with pytest.raises(ValueError):
            _gateway(gw_env, **env)


@pytest.mark.req("FR-1.50")
def test_api_key_header_and_its_precedence():
    assert bearer("Bearer one", "two", "three") == "one"
    assert bearer(None, "two", "three") == "two"
    assert bearer(None, None, " three ") == "three"
    assert bearer("Basic xyz", "  ", "three") == "three"
    assert bearer(None, None, "   ") is None and bearer(None) is None


@pytest.mark.req("FR-1.50")
def test_header_value_never_breaks_a_response():
    assert header_value("gpt-4o") == "gpt-4o"
    assert header_value("模型-4") == "%E6%A8%A1%E5%9E%8B-4"
    assert header_value("a\r\nX-Evil: 1") == "a%0D%0AX-Evil:%201"
    assert len(header_value("m" * 5000)) == 256


# ---------- the model field through the gateway ----------
@pytest.mark.req("FR-1.50", "FR-1.3")
def test_model_names_the_task_and_header_and_metadata_win(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    cases = [  # (body fields, headers, task logged, task_source)
        ({"model": f"ratchet/{TRIAGE}"}, {}, TRIAGE, "model"),
        ({"model": f"ratchet:{TRIAGE}"}, {}, TRIAGE, "model"),
        ({"model": f"ratchet/{TRIAGE}"}, {"X-Ratchet-Task": "page-read"}, "page-read", "header"),
        ({"model": f"ratchet/{TRIAGE}", "metadata": {"ratchet_task": "page-read"}}, {}, "page-read", "metadata"),
        ({"model": "ratchet"}, {}, "default", "default"),
        ({}, {}, "default", "default"),
    ]
    for body, headers, task, source in cases:
        r = c.post(CHAT, json={"messages": msgs, **body}, headers=headers)
        assert r.status_code == 200, (body, r.text)
        meta = r.json()["ratchet"]
        assert (meta["task"], meta["task_source"], meta["requested_model"]) == (task, source, body.get("model"))
        assert r.headers["X-Ratchet-Task"] == task and "X-Ratchet-Model-Alias" not in r.headers
    assert [row["task"] for row in _rows(gw)] == [case[2] for case in cases]


@pytest.mark.req("FR-1.50")
def test_unknown_model_lenient_serves_default_strict_refuses(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app, raise_server_exceptions=False)
    r = c.post(CHAT, json={"model": "gpt-4o", "messages": HI})
    assert r.status_code == 200
    assert r.headers["X-Ratchet-Model-Alias"] == "gpt-4o" and r.headers["X-Ratchet-Task"] == "default"
    assert r.json()["ratchet"]["requested_model"] == "gpt-4o"
    assert r.json()["model"] != "gpt-4o"  # `model` is the model that served
    r = c.post(CHAT, json={"model": "模型", "messages": HI})  # any script: percent-encoded in the header, never a 500
    assert r.status_code == 200 and r.headers["X-Ratchet-Model-Alias"] == "%E6%A8%A1%E5%9E%8B"
    r = c.post(CHAT, json={"model": "ratchet/no such task!", "messages": HI})  # Ratchet's namespace: 404 also here
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    assert len(_rows(gw)) == 2

    gw = _gateway(gw_env, RATCHET_MODEL_POLICY="strict", RATCHET_API_KEYS=KEYS, RATCHET_API_KEY_DAILY_REQUESTS="5")
    c = TestClient(gw.app, raise_server_exceptions=False)
    r = c.post(CHAT, json={"model": "gpt-4o", "messages": HI}, headers=ACME)
    assert r.status_code == 404
    assert r.json()["error"] == {
        "message": "The model 'gpt-4o' does not exist.",
        "type": "invalid_request_error",
        "param": "model",
        "code": "model_not_found",
    }
    r = c.post(CHAT, json={"model": "gpt-4o", "messages": HI}, headers={**ACME, "X-Ratchet-Task": TRIAGE})
    assert r.status_code == 404  # strict checks the model whatever names the task
    assert _rows(gw) == []  # refused before the cascade: nothing logged, no quota used
    for model in ("ratchet", f"ratchet/{TRIAGE}", None):
        body = {"messages": HI} if model is None else {"model": model, "messages": HI}
        r = c.post(CHAT, json=body, headers=ACME)
        assert r.status_code == 200 and r.headers["X-RateLimit-Remaining-Requests"] == str(4 - len(_rows(gw)) + 1)
    sdk_client = make_openai_client(gw.app, api_key=ACME_KEY)
    openai, _ = sdk()
    with pytest.raises(openai.NotFoundError) as caught:
        sdk_client.chat.completions.create(model="gpt-4o", messages=HI)
    assert (caught.value.code, caught.value.param) == ("model_not_found", "model")


@pytest.mark.req("FR-1.50")
def test_model_alias_maps_to_its_task(gw_env):
    gw = _gateway(gw_env, RATCHET_MODEL_ALIASES=f"gpt-4o-mini={TRIAGE},openai/gpt-4o=page-read,legacy=nope")
    c = TestClient(gw.app, raise_server_exceptions=False)
    msgs = gw.rt.stream.sample(1).messages()
    for model, task in (("gpt-4o-mini", TRIAGE), ("openai/gpt-4o", "page-read"), ("legacy", "nope")):
        r = c.post(CHAT, json={"model": model, "messages": msgs})
        assert r.status_code == 200 and r.json()["ratchet"]["task_source"] == "alias"
        assert r.headers["X-Ratchet-Task"] == task and "X-Ratchet-Model-Alias" not in r.headers
    assert [row["task"] for row in _rows(gw)] == [TRIAGE, "page-read", "nope"]

    # An alias or a ratchet/<task> naming an unregistered task follows RATCHET_UNKNOWN_TASKS; the error names `model`.
    gw = _gateway(gw_env, RATCHET_MODEL_ALIASES="legacy=nope", RATCHET_UNKNOWN_TASKS="reject")
    c = TestClient(gw.app, raise_server_exceptions=False)
    for model in ("legacy", "ratchet/nope"):
        r = c.post(CHAT, json={"model": model, "messages": HI})
        assert r.status_code == 400
        assert (r.json()["error"]["code"], r.json()["error"]["param"]) == ("unknown_task", "model")
    r = c.post(CHAT, json={"messages": HI}, headers={"X-Ratchet-Task": "nope"})
    assert r.json()["error"]["param"] is None
    assert _rows(gw) == []


@pytest.mark.req("FR-1.50")
def test_streamed_response_names_the_task(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    r = c.post(CHAT, json={"model": f"ratchet/{TRIAGE}", "messages": msgs, "stream": True})
    assert r.status_code == 200 and r.headers["X-Ratchet-Task"] == TRIAGE
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    meta = events[-1]["ratchet"]
    assert (meta["task"], meta["task_source"], meta["requested_model"]) == (TRIAGE, "model", f"ratchet/{TRIAGE}")


# ---------- match rules ----------
@pytest.mark.req("FR-1.50")
def test_match_rules_route_requests_that_name_no_task(gw_env, tmp_path):
    specs = [
        {"name": "invoice-extract", "match": {"system_prefix": "You extract invoice fields"}},
        {"name": "triage-schema", "match": {"schema_name": "ticket_triage"}},
        {"name": "order-tools", "match": {"tools": ["lookup_order", "refund"]}},
        {"name": "both", "match": {"system_prefix": "Both:", "schema_name": "both_schema"}},
        {"name": "invoice-later", "match": {"system_prefix": "You extract"}},  # registered after invoice-extract
        {"name": "globex-only", "clients": ["globex"], "match": {"system_prefix": "Globex:"}},
    ]
    gw = _gateway(gw_env, RATCHET_TASKS_FILE=_tasks_file(tmp_path, *specs), RATCHET_API_KEYS=KEYS)
    c = TestClient(gw.app)

    def schema(name: str) -> dict[str, Any]:
        return {"type": "json_schema", "json_schema": {"name": name, "schema": {"type": "object"}}}

    def tool(name: str) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}

    cases = [  # (body, headers, task, source)
        ({"messages": _system("You extract invoice fields as JSON.")}, ACME, "invoice-extract", "match"),
        (
            {"messages": [{"role": "developer", "content": [{"type": "text", "text": "You extract invoice fields"}]}, *HI]},
            ACME,
            "invoice-extract",
            "match",
        ),
        ({"messages": _system("You extract dates.")}, ACME, "invoice-later", "match"),  # the first match in order wins
        ({"messages": _system("You extract invoice fields.")}, {**ACME, "X-Ratchet-Task": TRIAGE}, TRIAGE, "header"),
        ({"messages": HI, "response_format": schema("ticket_triage")}, ACME, "triage-schema", "match"),
        ({"messages": HI, "response_format": schema("other")}, ACME, "default", "default"),
        ({"messages": HI, "tools": [tool("refund"), tool("lookup_order")]}, ACME, "order-tools", "match"),
        ({"messages": HI, "tools": [tool("refund"), tool("lookup_order"), tool("cancel")]}, ACME, "default", "default"),
        ({"messages": HI, "functions": [{"name": "refund"}, {"name": "lookup_order"}]}, ACME, "order-tools", "match"),
        ({"messages": _system("Both: go"), "response_format": schema("both_schema")}, ACME, "both", "match"),
        ({"messages": _system("Both: go")}, ACME, "default", "default"),  # every rule of a task must hold
        ({"messages": _system("Globex: hi")}, ACME, "default", "default"),  # not this key's task: no match, no 403
        ({"messages": _system("Globex: hi")}, GLOBEX, "globex-only", "match"),
        (
            {"messages": [*HI, {"role": "system", "content": "You extract invoice fields"}]},
            ACME,
            "invoice-extract",
            "match",
        ),  # the first system message, wherever it is
    ]
    for body, headers, task, source in cases:
        r = c.post(CHAT, json=body, headers=headers)
        assert r.status_code == 200, (body, r.text)
        assert (r.json()["ratchet"]["task"], r.json()["ratchet"]["task_source"]) == (task, source), body
    assert [row["task"] for row in _rows(gw)] == [case[2] for case in cases]


@pytest.mark.req("FR-1.50")
def test_match_rules_are_plain_comparisons():
    """A prefix that looks like a pattern matches only itself, and a long system prompt costs one startswith."""
    tasks.register(tasks.Task("dots", source="file", match_rules={"system_prefix": ".*"}))
    routing = ModelRouting()
    assert resolve_task(_req(messages=_system("anything")), None, routing) == ("default", "default")
    assert resolve_task(_req(messages=_system(".* literally")), None, routing) == ("dots", "match")
    huge = "(a+)+" * 20000
    assert resolve_task(_req(messages=_system(huge)), None, routing) == ("default", "default")


# ---------- auto tasks per system prompt ----------
@pytest.mark.req("FR-1.50")
def test_auto_system_prompt_task_is_stable_persisted_and_capped(gw_env):
    gw = _gateway(gw_env, RATCHET_AUTO_TASK="system_prompt", RATCHET_MAX_AUTO_TASKS="2")
    c = TestClient(gw.app)
    one, two, three = "You summarise legal letters.", "You write haiku about cats.", "You are a pirate."

    def ask(messages: list[dict[str, Any]], **headers: str) -> dict[str, Any]:
        r = c.post(CHAT, json={"messages": messages}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.headers["X-Ratchet-Task"] == r.json()["ratchet"]["task"]
        return r.json()["ratchet"]

    first = ask(_system(one, "Letter 1"))
    assert (first["task"], first["task_source"]) == (_auto(one), "auto_system")
    assert system_prompt_task(_system(one)) == _auto(one)
    assert ask(_system(one, "Letter 2"))["task"] == _auto(one)  # stable: the same prompt, the same task
    developer = [{"role": "developer", "content": [{"type": "text", "text": one}]}, {"role": "user", "content": "3"}]
    assert ask(developer)["task"] == _auto(one)
    assert ask(HI)["task"] == "default" and ask(_system("   "))["task"] == "default"
    assert ask(_system(one), **{"X-Ratchet-Task": TRIAGE})["task"] == TRIAGE  # a header still wins

    registered = tasks.lookup(_auto(one))
    assert registered is not None and registered.source == "auto" and not registered.pinned and not registered.fields
    saved = gw.rt.ledger.saved_task(_auto(one))
    assert saved is not None and (saved["source"], saved["updated_by"]) == ("auto", "auto")
    assert one not in json.dumps(saved)  # the name holds a digest; the prompt's text is never stored (INV-12)
    created = [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == "task_registered"]
    assert created == [f"{_auto(one)} (spec {registered.spec_hash}) auto: a new system prompt"]

    assert ask(_system(two))["task"] == _auto(two)
    for _ in range(3):  # past RATCHET_MAX_AUTO_TASKS: default, logged once
        meta = ask(_system(three))
        assert (meta["task"], meta["task_source"]) == ("default", "default")
    assert tasks.lookup(_auto(three)) is None
    caps = [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == "auto_task_cap"]
    assert len(caps) == 1 and caps[0].startswith("RATCHET_MAX_AUTO_TASKS=2:")
    assert ask(_system(one))["task"] == _auto(one)  # tasks made before the cap keep working

    # Stored like an API task, so a restart or a Night School run registers it again.
    tasks.reset_builtin()
    assert tasks.lookup(_auto(one)) is None
    assert taskspec.install(gw.rt.ledger) == []
    assert tasks.lookup(_auto(one)) is not None and tasks.lookup(_auto(two)) is not None
    assert {r["task"] for r in _rows(gw)} >= {_auto(one), _auto(two), "default", TRIAGE}


@pytest.mark.req("FR-1.50")
def test_auto_task_made_only_for_an_admitted_request(gw_env):
    gw = _gateway(gw_env, RATCHET_AUTO_TASK="system_prompt", RATCHET_API_KEYS=KEYS, RATCHET_API_KEY_DAILY_REQUESTS="1")
    c = TestClient(gw.app, raise_server_exceptions=False)
    assert c.post(CHAT, json={"messages": _system("first")}, headers=ACME).status_code == 200
    r = c.post(CHAT, json={"messages": _system("second")}, headers=ACME)
    assert r.status_code == 429  # over the quota: no task made, nothing stored
    assert tasks.lookup(_auto("second")) is None and gw.rt.ledger.saved_task(_auto("second")) is None

    gw = _gateway(gw_env, RATCHET_AUTO_TASK="system_prompt", RATCHET_MAX_AUTO_TASKS="0")
    meta = TestClient(gw.app).post(CHAT, json={"messages": _system("first")}).json()["ratchet"]
    assert (meta["task"], meta["task_source"]) == ("default", "default")


@pytest.mark.req("FR-1.50")
def test_auto_task_registered_once_under_threads(gw_env):
    gw = _gateway(gw_env, RATCHET_AUTO_TASK="system_prompt")
    name = _auto("You sort mail.")
    got: list[tasks.Task] = []
    barrier = threading.Barrier(8)

    def make() -> None:
        new = gw.rt.auto_task(name)
        assert new is not None
        barrier.wait()
        got.append(gw.rt.keep_auto_task(new))

    threads = [threading.Thread(target=make) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(t) for t in got}) == 1 and got[0] is tasks.lookup(name)
    assert [e["kind"] for e in gw.rt.ledger.events()].count("task_registered") == 1
    assert [t["name"] for t in gw.rt.ledger.saved_tasks()] == [name]


# ---------- /v1/models ----------
@pytest.mark.req("FR-1.2", "FR-1.50")
def test_every_model_validates_with_the_openai_sdk(gw_env, tmp_path):
    openai, _ = sdk()
    from openai.types import Model

    specs = [{"name": "invoice-extract", "fields": {"total": {"type": "number"}}}, {"name": "globex-only", "clients": ["globex"]}]
    gw = _gateway(
        gw_env,
        RATCHET_TASKS_FILE=_tasks_file(tmp_path, *specs),
        RATCHET_MODEL_ALIASES=f"gpt-4o-mini={TRIAGE},hidden=globex-only",
        RATCHET_API_KEYS=KEYS,
    )
    c = TestClient(gw.app)
    assert c.get("/v1/models").status_code == 401
    assert c.post(CHAT, json={"messages": HI}, headers={**ACME, "X-Ratchet-Task": "adhoc-name"}).status_code == 200
    assert c.post(CHAT, json={"messages": HI}, headers={**GLOBEX, "X-Ratchet-Task": "globex-adhoc"}).status_code == 200
    data = c.get("/v1/models", headers=ACME).json()["data"]
    for item in data:
        assert Model.model_validate(item).id == item["id"]
        assert item["owned_by"] == "ratchet" and isinstance(item["created"], int)
    ids = [m["id"] for m in data]
    assert ids[0] == "ratchet"
    assert ids[1 : len(tasks.BUILTIN) + 1] == [f"ratchet/{n}" for n in tasks.BUILTIN]  # registry order
    assert "ratchet/invoice-extract" in ids and "ratchet/adhoc-name" in ids and "gpt-4o-mini" in ids
    # A task, an ad hoc name or an alias another key's allow-list or requests own is not listed for this key.
    assert not {"ratchet/globex-only", "ratchet/globex-adhoc", "hidden"} & set(ids)
    by_id = {m["id"]: m for m in data}
    assert by_id["ratchet/invoice-extract"]["ratchet"] == {
        "task": "invoice-extract",
        "pinned": False,
        "format": "json",
        "compare_keys": ["total"],
        "live_version": None,
    }
    assert by_id["ratchet/research-plan"]["ratchet"]["pinned"] is True
    assert by_id["gpt-4o-mini"]["ratchet"]["alias_of"] == f"ratchet/{TRIAGE}"
    assert by_id["ratchet/adhoc-name"]["created"] == int(_rows(gw)[0]["ts"])  # first seen
    assert "ratchet/globex-only" in {m["id"] for m in c.get("/v1/models", headers=GLOBEX).json()["data"]}
    r = c.post(CHAT, json={"model": "ratchet/globex-only", "messages": HI}, headers=ACME)
    assert r.status_code == 403 and r.json()["error"]["code"] == "task_not_allowed"  # the model field is no way around it

    # The SDK's own list and retrieve, the slash sent as %2F.
    client = make_openai_client(gw.app, api_key=ACME_KEY)
    assert [m.id for m in client.models.list()] == ids
    got = client.models.retrieve(f"ratchet/{TRIAGE}")
    assert got.id == f"ratchet/{TRIAGE}" and got.model_extra["ratchet"]["compare_keys"] == ["category", "priority"]
    assert client.models.retrieve(f"ratchet:{TRIAGE}").id == f"ratchet/{TRIAGE}"
    assert client.models.retrieve("gpt-4o-mini").model_extra["ratchet"]["task"] == TRIAGE
    for missing in ("gpt-4o", "ratchet/globex-only", "ratchet/never-seen"):
        with pytest.raises(openai.NotFoundError) as caught:
            client.models.retrieve(missing)
        assert caught.value.code == "model_not_found" and caught.value.body["message"] == f"The model '{missing}' does not exist."
    r = c.get("/v1/models/gpt-4o", headers=ACME)
    assert r.status_code == 404 and r.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.req("FR-1.2", "FR-1.50")
def test_models_show_the_live_version_and_the_unknown_task_policy(gw_env):
    gw = _gateway(gw_env, RATCHET_UNKNOWN_TASKS="teacher")
    c = TestClient(gw.app)
    vid = _version(gw.rt.ledger, "live", {"threshold": 0.8})
    assert c.post(CHAT, json={"messages": HI}, headers={"X-Ratchet-Task": "seen-once"}).status_code == 200
    by_id = {m["id"]: m["ratchet"] for m in c.get("/v1/models").json()["data"]}
    assert by_id["ratchet"]["live_version"] == vid and by_id[f"ratchet/{TRIAGE}"]["live_version"] == vid
    assert by_id["ratchet/research-plan"]["live_version"] is None  # pinned: the student never serves it
    assert by_id["ratchet/seen-once"]["live_version"] is None  # unregistered under `teacher`: neither

    gw = _gateway(gw_env, RATCHET_UNKNOWN_TASKS="reject")
    gw.rt.ledger.log_request(task="seen-once", served_by="teacher", client=None)
    ids = {m["id"] for m in TestClient(gw.app).get("/v1/models").json()["data"]}
    assert "ratchet/seen-once" not in ids  # a request for it would be refused, so it is not offered


@pytest.mark.req("FR-1.50")
def test_recent_tasks_window_client_and_limit(monkeypatch):
    led = Ledger(":memory:")
    now, day = 1_800_000_000.0, 86400.0
    for task, ts, client in (
        ("old", now - 40 * day, None),
        ("both", now - 35 * day, "acme"),
        ("both", now - 1 * day, "globex"),
        ("fresh", now - 2 * day, "acme"),
        ("fresh", now - 3 * day, "acme"),
    ):
        led.log_request(task=task, served_by="teacher", client=client, ts=ts)
    monkeypatch.setattr(ledger_module.time, "time", lambda: now)
    assert led.recent_tasks(30) == [
        {"task": "both", "first_ts": now - 35 * day, "last_ts": now - day},
        {"task": "fresh", "first_ts": now - 3 * day, "last_ts": now - 2 * day},
    ]
    assert [r["task"] for r in led.recent_tasks(30, client="acme")] == ["fresh"]
    assert [r["task"] for r in led.recent_tasks(50, limit=2)] == ["both", "fresh"]
    assert [r["task"] for r in led.recent_tasks(50)] == ["both", "fresh", "old"]
    monkeypatch.undo()
    for bad in (0, -1, float("inf"), True):
        with pytest.raises(ValueError, match="days"):
            led.recent_tasks(bad)
    with pytest.raises(ValueError, match="limit"):
        led.recent_tasks(30, limit=0)


# ---------- Azure-style route ----------
@pytest.mark.req("FR-1.50")
def test_azure_route_with_api_key_header(gw_env):
    gw = _gateway(gw_env, RATCHET_API_KEYS=KEYS, RATCHET_MODEL_ALIASES=f"gpt-4o-mini={TRIAGE}", RATCHET_MODEL_POLICY="strict")
    c = TestClient(gw.app, raise_server_exceptions=False)
    msgs = gw.rt.stream.sample(1).messages()
    url = "/openai/deployments/{}/chat/completions?api-version=2024-10-21"
    r = c.post(url.format(TRIAGE), json={"messages": msgs, "model": "gpt-4o"}, headers={"api-key": ACME_KEY})
    assert r.status_code == 200, r.text  # the body's model is not read on this route, so strict does not refuse it
    meta = r.json()["ratchet"]
    assert (meta["task"], meta["task_source"]) == (TRIAGE, "deployment")
    assert r.json()["object"] == "chat.completion"
    r = c.post(url.format("gpt-4o-mini"), json={"messages": msgs}, headers={"api-key": ACME_KEY})
    assert (r.status_code, r.json()["ratchet"]["task_source"]) == (200, "alias")
    r = c.post(url.format("ratchet"), json={"messages": msgs}, headers={"api-key": GLOBEX_KEY})
    assert (r.status_code, r.json()["ratchet"]["task"]) == (200, "default")
    assert [(row["task"], row["client"]) for row in _rows(gw)] == [(TRIAGE, "acme"), (TRIAGE, "acme"), ("default", "globex")]

    r = c.post(url.format(TRIAGE), json={"messages": msgs})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    r = c.post(url.format("bad%20name"), json={"messages": msgs}, headers={"api-key": ACME_KEY})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    r = c.post(url.format(TRIAGE), json={"messages": []}, headers={"api-key": ACME_KEY})
    assert r.status_code == 400 and r.json()["error"]["param"] == "messages"  # the same validation as /v1
    assert len(_rows(gw)) == 3
    # The api-key header works on /v1 too.
    assert c.get("/v1/models", headers={"api-key": ACME_KEY}).status_code == 200


@pytest.mark.req("FR-1.50")
def test_azure_openai_sdk_end_to_end(gw_env):
    openai, hx = sdk()
    gw = _gateway(gw_env, RATCHET_API_KEYS=KEYS)
    app_client = TestClient(gw.app, raise_server_exceptions=False)
    seen: list[Any] = []

    def forward(request: Any) -> Any:
        seen.append(request)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        r = app_client.request(request.method, str(request.url), headers=headers, content=request.read())
        keep = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-length", "content-encoding")]
        return hx.Response(r.status_code, headers=keep, content=r.content, request=request)

    client = openai.AzureOpenAI(
        azure_endpoint="http://testserver",
        api_key=GLOBEX_KEY,
        api_version="2024-10-21",
        http_client=hx.Client(transport=hx.MockTransport(forward)),
        max_retries=0,
    )
    msgs = gw.rt.stream.sample(1).messages()
    resp = client.chat.completions.create(model=TRIAGE, messages=msgs)
    assert resp.model_extra["ratchet"]["task"] == TRIAGE and resp.choices[0].message.content
    (request,) = seen
    assert request.url.path == f"/openai/deployments/{TRIAGE}/chat/completions"
    assert request.headers["api-key"] == GLOBEX_KEY and "authorization" not in request.headers
    assert _rows(gw)[0]["client"] == "globex"


# ---------- the model field never selects an upstream model ----------
@pytest.mark.req("FR-1.50", "INV-1")
def test_teacher_always_gets_the_configured_model(gw_env, tmp_path, monkeypatch):
    """Whatever `model` a client sends (a closed model's id among them), the teacher gets RATCHET_TEACHER_MODEL and the
    student its adapter name; `model` in the response is the one that served (proposed invariant: c2-invariants.md)."""
    gw = _gateway(gw_env)
    teacher, student = FakeOpenAIServer(A), FakeOpenAIServer(A)
    settings = Settings(
        mode="live",
        db_path=str(tmp_path / "live.db"),
        teacher_base="https://teacher.test/v1",
        teacher_api_key="tf-teacher-api-key",
        teacher_model=OPEN_TEACHER,
        student_base="https://student.test/v1",
        student_api_key="sk-student",
        samples=3,
        audit_rate=1.0,
        api_keys="",
        api_key_daily_requests=0,
        model_aliases=f"gpt-4o-mini={TRIAGE}",
        model_policy="lenient",
        auto_task="off",
        mock_warmup_days=0,
    )
    rt = gw.Runtime(settings, transports={"teacher": teacher.transport, "student": student.transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    models = [None, "ratchet", f"ratchet/{TRIAGE}", "gpt-4o-mini", "gpt-4o", CLOSED_TEACHER, "o1-preview", "student-v1"]
    for model in models:  # cold start: the teacher answers
        body = {"messages": HI} if model is None else {"model": model, "messages": HI}
        r = c.post(CHAT, json=body)
        assert r.status_code == 200 and r.json()["model"] == OPEN_TEACHER, model
    _version(rt.ledger, "live", {"threshold": 0.5})  # confident: the student answers and every answer is audited
    for model in models:
        body = {"messages": HI} if model is None else {"model": model, "messages": HI}
        r = c.post(CHAT, json=body)
        assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "student", model
        assert r.json()["model"] == "student-v1"
    r = c.post("/openai/deployments/gpt-4o/chat/completions", json={"model": CLOSED_TEACHER, "messages": HI})
    assert r.status_code == 200
    teacher_models = {call.json["model"] for call in teacher.calls_to("/chat/completions")}
    student_models = {call.json["model"] for call in student.calls_to("/chat/completions")}
    assert teacher_models == {OPEN_TEACHER} and student_models == {"student-v1"}
    assert len(teacher.calls_to("/chat/completions")) == 2 * len(models) + 1  # served, then audits


@pytest.mark.req("FR-1.50")
def test_routing_keeps_plain_requests_unchanged(gw_env):
    """With the default settings, a request that names no task is the `default` task as before, whatever its system
    prompt: match rules belong to operator specs (the built-in tasks have none) and auto tasks are off."""
    gw = _gateway(gw_env)
    assert gw.rt.routing == ModelRouting() and not any(t.match_rules for t in tasks.registered())
    c = TestClient(gw.app)
    for body in ({"messages": HI}, {"messages": _system("You extract invoice fields.")}, {"model": "gpt-4o", "messages": HI}):
        r = c.post(CHAT, json=body)
        assert (r.json()["ratchet"]["task"], r.headers["X-Ratchet-Task"]) == ("default", "default")
    assert gw.rt.ledger.saved_tasks() == []
