"""Tool and function calling (FR-1.41, ADR 0043): a tool request (tools offered, or tool calls or results in its
messages) goes to the teacher alone with its tools, its tool calls come back exactly as the teacher made them (also in
the legacy `function_call` form, and as stream deltas the OpenAI SDK rebuilds), and nothing of it is stored; a request
with a part only the teacher reads goes to the teacher alone too, through the task's routing block (route
`ineligible:content_type`, FR-2.50), or with tools as a tool request. Models are `RecordingProvider`s, small
providers below, or `FakeOpenAIServer`s behind `httpx.MockTransport`; nothing calls a real API.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import httpx
import pytest
from fakes import FALLBACK_TEACHER, OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, ToolCallingServer, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

from ratchet import tasks
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.openai_compat import (
    ToolParams,
    legacy_history,
    tool_params,
    tool_turns,
    uses_tools,
    validate_chat_request,
)
from ratchet.providers import Completion, GenParams, OpenAICompatProvider, UpstreamError
from ratchet.redact import redact_messages
from ratchet.router import Ratchet
from sim import generic

CHAT = "/v1/chat/completions"
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
WEATHER_FN = {
    "name": "get_weather",
    "description": "The weather in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}
WEATHER = [{"type": "function", "function": WEATHER_FN}]
ASK = [{"role": "user", "content": "Weather in Paris and Rome?"}]
CALL = {"id": "call_paris01", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}
CALL2 = {"id": "call_rome02", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Rome"}'}}
CALL_ID = re.compile(r"call_[0-9a-f]{24}")


def _req(**body: Any) -> Any:
    return validate_chat_request({"messages": ASK, **body})


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "unknown_tasks": "distill",
    }
    return Settings(**{**base, **overrides})


class ToolTeacher:
    """A teacher that answers every call with the tool calls `calls` (no content, finish `tool_calls`), or `text` when
    `calls` is empty; it records what it was sent."""

    def __init__(self, calls: list[dict[str, Any]], text: str = "", out_tokens: int = 7) -> None:
        self.tool_calls = calls
        self.text = text
        self.out_tokens = out_tokens
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, model, n=1, temperature=0.0, logprobs=False, response_format=None, params=None, **_):
        self.calls.append({"messages": messages, "model": model, "n": n, "params": params})
        finish = "tool_calls" if self.tool_calls else "stop"
        c = Completion(self.text, model, 12, self.out_tokens, 3.0, finish_reason=finish, tool_calls=self.tool_calls or None)
        return [c] * n


def _ratchet(teacher: Any, student: Any = None, **settings: Any) -> tuple[Ratchet, Ledger, Any]:
    """A Ratchet with a live version whose student would serve anything (threshold 0), and a spy on `add_example`."""
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.0})
    student = student or RecordingProvider(A)
    r = Ratchet(_settings(**settings), led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    added: list[Any] = []
    real = led.add_example

    def spy(*a: Any, **kw: Any) -> bool:
        added.append(a)
        return real(*a, **kw)

    led.add_example = spy  # type: ignore[method-assign]
    r.added = added  # type: ignore[attr-defined]
    return r, led, student


def _live(gw: Any, tmp_path: Any, teacher: FakeOpenAIServer, **overrides: Any) -> tuple[Any, FakeOpenAIServer]:
    """A live Runtime with the teacher and the student behind MockTransport, and a live version that would serve
    anything; tool requests must never reach that student."""
    student = FakeOpenAIServer(A)
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / f"tools{len(list(tmp_path.iterdir()))}.db"),
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
        "teacher_modalities": "text",
        "api_keys": "",
        "allow_unauthenticated": True,  # a live gateway without API keys starts only with this (ADR 0061)
        "public_dashboard": True,  # and its /ratchet/stats needs a dashboard key without this (ADR 0061)
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
    }
    rt = gw.Runtime(Settings(**{**base, **overrides}), transports={"teacher": teacher.transport, "student": student.transport})
    _version(rt.ledger, "live", {"threshold": 0.0})
    return rt, student


def _bodies(server: FakeOpenAIServer) -> list[dict[str, Any]]:
    return [c.json for c in server.calls_to("/chat/completions")]


def _rows(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute("SELECT task, route, served_by, stored, uses_tools FROM requests ORDER BY id")]


def _stream(c: TestClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    with c.stream("POST", CHAT, json={**body, "stream": True}) as resp:
        assert resp.status_code == 200, resp.read()[:300]
        lines = [line[6:] for line in resp.iter_lines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


# ---------- what a tool request is, and the legacy fields (openai_compat) ----------
@pytest.mark.req("FR-1.41")
def test_uses_tools_names_every_kind_of_tool_request():
    call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    tool_requests = [
        {"tools": WEATHER},
        {"functions": [WEATHER_FN]},
        {"messages": [*ASK, {"role": "assistant", "content": None, "tool_calls": [call]}]},
        {"messages": [*ASK, {"role": "assistant", "content": None, "function_call": call["function"]}]},
        {"messages": [*ASK, {"role": "tool", "tool_call_id": "c1", "content": "18 C"}]},
        {"messages": [*ASK, {"role": "function", "name": "f", "content": "18 C"}]},
        {"tools": WEATHER, "tool_choice": "none"},  # still a tool request: the teacher is told not to call
    ]
    for body in tool_requests:
        req = validate_chat_request({"messages": ASK, **body})
        assert uses_tools(req), body
    plain = [
        {},
        {"tools": []},
        {"functions": []},
        {"tool_choice": "auto"},  # nothing to call
        {"parallel_tool_calls": False},
        {"messages": [*ASK, {"role": "assistant", "content": "18 C", "tool_calls": [], "function_call": None}]},
    ]
    for body in plain:
        req = validate_chat_request({"messages": ASK, **body})
        assert not uses_tools(req) and not tool_turns(req.messages), body


@pytest.mark.req("FR-1.41")
def test_tool_params_map_the_legacy_fields():
    """`functions` become tools and `function_call` the tool choice; the response is legacy only when the request used
    only the legacy fields. Without a tool to call, `tool_choice` and `parallel_tool_calls` are not sent."""
    mapped = [{"type": "function", "function": WEATHER_FN}]
    # A legacy message holds one call, so a legacy request asks for one (parallel_tool_calls false unless it says).
    assert tool_params(_req(functions=[WEATHER_FN])) == ToolParams(mapped, None, False, legacy=True)
    named = tool_params(_req(functions=[WEATHER_FN], function_call={"name": "get_weather"}))
    assert named == ToolParams(mapped, {"type": "function", "function": {"name": "get_weather"}}, False, legacy=True)
    assert tool_params(_req(functions=[WEATHER_FN], parallel_tool_calls=True)).parallel_tool_calls is True
    for word in ("auto", "none"):
        assert tool_params(_req(functions=[WEATHER_FN], function_call=word)).tool_choice == word
    assert tool_params(_req(tools=WEATHER, tool_choice="required", parallel_tool_calls=False)) == ToolParams(
        WEATHER, "required", False, legacy=False
    )
    # Both forms: the request's tools first, then the functions they do not name; the response in the current form.
    other = {"name": "get_time", "parameters": {"type": "object"}}
    both = tool_params(_req(tools=WEATHER, functions=[WEATHER_FN, other], function_call={"name": "get_time"}))
    assert both.tools == [*WEATHER, {"type": "function", "function": other}] and not both.legacy
    assert both.parallel_tool_calls is None  # the current form: the teacher's own default
    assert both.tool_choice == {"type": "function", "function": {"name": "get_time"}}
    assert tool_params(_req(tools=WEATHER, tool_choice="auto", functions=[WEATHER_FN])).tool_choice == "auto"
    # Nothing to call: nothing about calling is sent.
    for body in ({"tool_choice": "auto"}, {"parallel_tool_calls": True}, {"function_call": "auto"}, {"tools": []}):
        assert tool_params(_req(**body)) == ToolParams()
    # GenParams carries the mapped fields; the legacy names never reach a model (providers.NEVER_FORWARDED).
    p = GenParams.from_request(_req(functions=[WEATHER_FN], function_call="auto"))
    assert (p.tools, p.tool_choice, p.parallel_tool_calls) == (mapped, "auto", False)
    assert "functions" not in p.to_body() and "function_call" not in p.to_body()
    assert GenParams.from_request(_req(tool_choice="none")) == GenParams()


@pytest.mark.req("FR-1.41")
def test_legacy_history_pairs_calls_and_results():
    msgs = [
        *ASK,
        {"role": "assistant", "content": None, "function_call": {"name": "get_weather", "arguments": '{"city": "Paris"}'}},
        {"role": "function", "name": "get_weather", "content": "18 C"},
        {"role": "assistant", "content": "18 C in Paris."},
        {"role": "function", "name": "get_weather", "content": "an orphan result"},
    ]
    out = legacy_history(msgs)
    (call,) = out[1]["tool_calls"]
    assert CALL_ID.fullmatch(call["id"]) and call["type"] == "function" and "function_call" not in out[1]
    assert call["function"] == {"name": "get_weather", "arguments": '{"city": "Paris"}'} and out[1]["content"] is None
    assert out[2] == {"role": "tool", "tool_call_id": call["id"], "content": "18 C"}
    assert out[3] is msgs[3] and out[4] is msgs[4]  # an answer with no call before it stays as it is
    assert legacy_history(msgs)[1]["tool_calls"][0]["id"] == call["id"]  # stable: the same history, the same ids
    assert msgs[1]["function_call"] and msgs[2]["role"] == "function"  # the caller's list is untouched
    plain = [*ASK, {"role": "assistant", "content": None, "tool_calls": [CALL]}]
    assert legacy_history(plain) is plain


# ---------- the provider reads tool calls (FR-1.41) ----------
def _parse(message: dict[str, Any], finish: str | None = "tool_calls") -> list[Completion]:
    choice: dict[str, Any] = {"index": 0, "message": message}
    if finish is not None:
        choice["finish_reason"] = finish
    body = json.dumps({"choices": [choice], "usage": {"prompt_tokens": 9, "completion_tokens": 4}})
    p = OpenAICompatProvider(
        "https://m.test/v1", "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body.encode()))
    )
    return p.complete(ASK, OPEN_TEACHER)


@pytest.mark.req("FR-1.41", "FR-1.11")
def test_provider_reads_tool_calls_in_openais_shape():
    vllm = {"index": 0, "id": "chatcmpl-tool-1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}
    (c,) = _parse({"role": "assistant", "content": None, "tool_calls": [vllm, CALL2]})
    assert c.text == "" and c.finish_reason == "tool_calls"
    assert c.tool_calls == [{k: v for k, v in vllm.items() if k != "index"}, CALL2]  # upstream ids kept, `index` dropped
    # An object as arguments is JSON-encoded, missing ones are {}, a missing type is a function, a missing id is made.
    loose = [
        {"function": {"name": "get_weather", "arguments": {"city": "Zürich"}}},
        {"id": "", "type": "function", "function": {"name": "get_weather"}},
    ]
    (c,) = _parse({"role": "assistant", "content": "Looking both up.", "tool_calls": loose})
    first, second = c.tool_calls
    assert first["function"]["arguments"] == '{"city": "Zürich"}' and second["function"]["arguments"] == "{}"
    assert CALL_ID.fullmatch(first["id"]) and CALL_ID.fullmatch(second["id"]) and first["id"] != second["id"]
    assert first["type"] == second["type"] == "function" and c.text == "Looking both up."
    # A server that answers in the legacy form: one call.
    (c,) = _parse(
        {"role": "assistant", "content": None, "function_call": {"name": "get_weather", "arguments": "{}"}}, "function_call"
    )
    (call,) = c.tool_calls
    assert call["function"] == {"name": "get_weather", "arguments": "{}"} and CALL_ID.fullmatch(call["id"])
    # No calls: None, as vLLM's empty list and an explicit null say.
    for message in ({"content": "18 C", "tool_calls": []}, {"content": "18 C", "tool_calls": None, "function_call": None}):
        (c,) = _parse(message, "stop")
        assert c.tool_calls is None and c.text == "18 C"


@pytest.mark.req("FR-1.41", "FR-1.11", "INV-7")
@pytest.mark.parametrize(
    "tool_calls",
    [
        pytest.param({"id": "x"}, id="not-an-array"),
        pytest.param(["call"], id="entry-not-an-object"),
        pytest.param([{"id": "c", "type": "custom", "custom": {"name": "f"}}], id="custom-type"),
        pytest.param([{"id": "c", "type": "function"}], id="no-function"),
        pytest.param([{"id": "c", "type": "function", "function": {"arguments": "{}"}}], id="no-name"),
        pytest.param([{"id": "c", "type": "function", "function": {"name": " ", "arguments": "{}"}}], id="blank-name"),
        pytest.param([CALL, {"id": "c", "function": {"name": 5}}], id="one-bad-of-two"),
    ],
)
def test_a_tool_call_a_client_could_not_use_is_a_bad_upstream_response(tool_calls):
    with pytest.raises(UpstreamError) as e:
        _parse({"role": "assistant", "content": None, "tool_calls": tool_calls})
    assert (e.value.status, e.value.code) == (502, "bad_upstream_response")


@pytest.mark.req("FR-1.41", "FR-1.11")
def test_tool_call_text_that_is_not_valid_unicode_is_refused():
    """An unpaired surrogate (`\\ud800`, which json.loads accepts) cannot be written as UTF-8, so the response would fail
    after the teacher was paid for; it is a 502 at once instead."""
    for fn in ('{"name": "get_\\ud800", "arguments": "{}"}', '{"name": "f", "arguments": "{\\"a\\": \\"\\ud800\\"}"}'):
        body = '{"choices": [{"message": {"content": null, "tool_calls": [{"id": "c", "type": "function", "function": ' + fn
        body += "}]}}]}"
        p = OpenAICompatProvider(
            "https://m.test/v1", "k", transport=httpx.MockTransport(lambda r, b=body: httpx.Response(200, content=b.encode()))
        )
        with pytest.raises(UpstreamError, match="tool call"):
            p.complete(ASK, OPEN_TEACHER)


# ---------- the router: tool requests go to the teacher alone ----------
@pytest.mark.req("FR-1.41", "INV-2", "INV-10")
def test_a_tool_request_is_answered_by_the_teacher_alone_and_never_stored():
    teacher = ToolTeacher([CALL, CALL2])
    r, led, student = _ratchet(teacher)
    params = GenParams(tools=WEATHER, tool_choice="auto", parallel_tool_calls=True)
    res = r.handle(ASK, TRIAGE, params=params)
    assert (res.route, res.served_by, res.version, res.escalated, res.confidence) == ("tools", "teacher", None, False, None)
    (choice,) = res.choices
    assert choice.tool_calls == [CALL, CALL2] and choice.finish_reason == "tool_calls" and choice.text == ""
    assert res.finish_reason == "tool_calls" and res.schema_valid is None
    assert student.calls == [] and r.added == [] and led.examples() == []  # INV-2, INV-10: nothing to learn from
    (sent,) = teacher.calls
    assert sent["model"] == OPEN_TEACHER and sent["params"].tools == WEATHER  # the configured teacher, sent the tools
    assert (sent["params"].tool_choice, sent["params"].parallel_tool_calls) == ("auto", True)
    assert _rows(led) == [{"task": TRIAGE, "route": "tools", "served_by": "teacher", "stored": 0, "uses_tools": 1}]
    # tool_choice "none" takes the same path: the teacher writes text, nothing is stored.
    text = ToolTeacher([], text="Paris: 18 C.")
    r, led, student = _ratchet(text)
    res = r.handle(ASK, TRIAGE, params=GenParams(tools=WEATHER, tool_choice="none"))
    assert (res.route, res.text, res.choices[0].tool_calls, res.finish_reason) == ("tools", "Paris: 18 C.", None, "stop")
    assert text.calls[0]["params"].tool_choice == "none" and student.calls == [] and r.added == []
    # n choices: each is the teacher's.
    r, led, _ = _ratchet(ToolTeacher([CALL]))
    res = r.handle(ASK, TRIAGE, params=GenParams(tools=WEATHER), n=2)
    assert [ch.tool_calls for ch in res.choices] == [[CALL], [CALL]] and led.examples() == []
    # Stats count tool requests by task, whatever their route.
    (task,) = led.stats_by_task()
    assert (task["tool_requests"], task["routes"]) == (1, {"tools": 1})


@pytest.mark.req("FR-1.41", "INV-10")
def test_tool_history_without_tools_is_teacher_only():
    """A conversation that carries tool calls or results continues a tool exchange whether or not it offers the tools
    again: the teacher answers, the student is never asked and nothing is stored (also in the legacy form)."""
    r, led, student = _ratchet(RecordingProvider(A))
    history = [
        *ASK,
        {"role": "assistant", "content": None, "tool_calls": [CALL]},
        {"role": "tool", "tool_call_id": CALL["id"], "content": "18 C and sunny."},
    ]
    legacy = [*ASK, {"role": "assistant", "content": None, "function_call": CALL["function"]}]
    for messages in (history, history[:2], legacy, [*ASK, {"role": "function", "name": "f", "content": "18 C"}]):
        res = r.handle(messages, TRIAGE)
        assert (res.route, res.served_by, res.text) == ("tools", "teacher", A)
    assert student.calls == [] and r.added == [] and led.examples() == []
    assert {row["route"] for row in _rows(led)} == {"tools"} and all(row["uses_tools"] == 1 for row in _rows(led))
    # The same task without tools is distilled as before.
    assert r.handle(ASK, TRIAGE).route == "student" and _rows(led)[-1]["uses_tools"] == 0


@pytest.mark.req("FR-1.41", "INV-10", "FR-2.15")
def test_tool_routes_keep_the_operators_reason_first():
    """A pinned task and an unregistered one under RATCHET_UNKNOWN_TASKS=teacher keep their own route when they offer
    tools, and their tool calls come back. A task that asks for tool distillation (`tools: distill`) is still answered
    by the teacher alone: that path is not built yet, so its student would answer without the tools."""
    tools = GenParams(tools=WEATHER)
    r, led, student = _ratchet(ToolTeacher([CALL]), unknown_tasks="teacher")
    pinned = r.handle([{"role": "user", "content": "Question: plan a trip?"}], "research-plan", params=tools)
    assert (pinned.route, pinned.choices[0].tool_calls) == ("pinned", [CALL])
    assert r.handle(ASK, "brand-new", params=tools).route == "unknown_task"
    assert r.teacher_only(ASK, TRIAGE, params=tools).route == "tools"  # a tool request with no other reason
    assert r.teacher_only(ASK, TRIAGE, params=tools, route="multimodal").route == "multimodal"
    tasks.register(tasks.Task("weather-agent", tools="distill"), source="file")
    assert r.handle(ASK, "weather-agent", params=tools).route == "tools"
    # A teacher-only request that offers no tools never gets a tool call back: as in the cascade, it is refused.
    for refused in (
        lambda: r.handle([{"role": "user", "content": "Question: plan a trip?"}], "research-plan"),
        lambda: r.teacher_only(ASK, TRIAGE),
        lambda: r.teacher_only(ASK, TRIAGE, route="multimodal"),
    ):
        with pytest.raises(UpstreamError) as e:
            refused()
        assert (e.value.status, e.value.code) == (502, "tool_calls_unsupported")
    assert student.calls == [] and r.added == [] and led.examples() == []
    by_task = {t["task"]: t["tool_requests"] for t in led.stats_by_task()}
    assert by_task == {"research-plan": 1, "brand-new": 1, TRIAGE: 2, "weather-agent": 1}
    # The generic teacher-only path, as before: a request without tools gets route `pinned` and a text answer.
    r, led, _ = _ratchet(RecordingProvider(A))
    assert (r.teacher_only(ASK, TRIAGE).route, led.stats_by_task()[0]["tool_requests"]) == ("pinned", 0)


@pytest.mark.req("FR-1.41", "FR-1.40")
def test_a_tool_call_is_not_held_to_the_response_format():
    """A `response_format` applies to the final answer's content, so a tool call is neither repaired nor counted against
    the schema (`schema_valid` is None when every choice is a tool call); a text answer still is."""
    schema = {"type": "object", "properties": {"temp": {"type": "number"}}, "required": ["temp"]}
    rf = {"type": "json_schema", "json_schema": {"name": "weather", "schema": schema}}
    teacher = ToolTeacher([CALL])
    r, led, _ = _ratchet(teacher)
    res = r.handle(ASK, params=GenParams(tools=WEATHER, response_format=rf))
    assert len(teacher.calls) == 1 and res.choices[0].tool_calls == [CALL] and res.schema_valid is None
    assert res.usage_breakdown["repair"] is None and r.health()["repair_errors"] == 0
    prose = ToolTeacher([], text='{"temp": 18}')
    r, _, _ = _ratchet(prose)
    res = r.handle(ASK, params=GenParams(tools=WEATHER, response_format=rf))
    assert res.route == "tools" and res.schema_valid is True and res.text == '{"temp": 18}'


@pytest.mark.req("FR-1.41", "FR-2.12", "INV-1", "INV-7")
def test_tool_requests_use_the_fallback_teacher_and_map_teacher_failures():
    """When the primary teacher is unavailable, the licensed fallback teacher answers the tool request (its calls are
    returned, nothing stored); when no teacher can, the error reaches the gateway to be mapped (INV-7)."""
    down = RecordingProvider(A, raise_on={OPEN_TEACHER})
    fallback = ToolTeacher([CALL])
    led = Ledger(":memory:")
    s = _settings(teacher_fallback_model=FALLBACK_TEACHER)
    r = Ratchet(s, led, down, lambda v: (RecordingProvider(A), v.adapter_uri), seed=1, fallback_teacher=fallback)
    res = r.handle(ASK, TRIAGE, params=GenParams(tools=WEATHER))
    assert (res.route, res.choices[0].tool_calls) == ("fallback_teacher", [CALL])
    assert fallback.calls[0]["model"] == FALLBACK_TEACHER and led.examples() == []
    assert _rows(led)[0]["uses_tools"] == 1
    r = Ratchet(_settings(), Ledger(":memory:"), down, lambda v: (RecordingProvider(A), v.adapter_uri), seed=1)
    with pytest.raises(httpx.ConnectError):
        r.handle(ASK, TRIAGE, params=GenParams(tools=WEATHER))


@pytest.mark.req("FR-1.41", "INV-2", "INV-10")
def test_a_tool_request_gets_no_audit_and_no_shadow():
    """Even at an audit rate of 1 and with a shadow candidate, a tool request makes one call, to the teacher: an audit
    or a shadow would judge the student on a request it never saw, and store or compare what it cannot learn."""
    teacher = ToolTeacher([CALL])
    shadow_student = RecordingProvider(A)
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.0})
    _version(led, "shadow", {"threshold": 0.0})
    students = {"student-v1": RecordingProvider(A), "student-v2": shadow_student}
    r = Ratchet(_settings(audit_rate=1.0), led, teacher, lambda v: (students[v.adapter_uri], v.adapter_uri), seed=1)
    res = r.handle(ASK, TRIAGE, params=GenParams(tools=WEATHER))
    assert (res.route, res.choices[0].tool_calls, len(teacher.calls)) == ("tools", [CALL], 1)
    assert all(s.calls == [] for s in students.values()) and led.examples() == []
    (row,) = [dict(x) for x in led.db.execute("SELECT audited, shadow_version, stored FROM requests")]
    assert row == {"audited": 0, "shadow_version": None, "stored": 0}
    assert r.handle(ASK, TRIAGE).route == "student" and shadow_student.calls  # the same request without tools: both run


@pytest.mark.req("FR-1.41", "FR-1.12")
def test_usage_counts_a_tool_calls_tokens():
    """A teacher that reports no completion tokens gets them estimated from what it wrote: its tool calls too."""
    r, _, _ = _ratchet(ToolTeacher([CALL], out_tokens=0))
    res = r.handle(ASK, TRIAGE, params=GenParams(tools=WEATHER))
    assert res.completion_tokens == len(json.dumps([CALL])) // 4 > 0


# ---------- redaction covers what tool turns carry (INV-12) ----------
@pytest.mark.req("FR-1.41", "FR-4.7", "INV-12")
def test_redact_messages_covers_tool_arguments_and_results():
    """Tool-call distillation will store tool turns; everything in them that can hold what a user typed is redacted
    first: tool and function call arguments and tool results (string or text parts)."""
    msgs = [
        {"role": "user", "content": "Mail my receipt."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "mail", "arguments": '{"to": "bob@example.com"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "sent to bob@example.com"},
        {"role": "tool", "tool_call_id": "c2", "content": [{"type": "text", "text": "cc alice@example.org"}]},
        {"role": "assistant", "content": None, "function_call": {"name": "mail", "arguments": '{"to": "carol@example.net"}'}},
        {"role": "function", "name": "mail", "content": "sent to carol@example.net"},
    ]
    found: Counter[str] = Counter()
    out = redact_messages(msgs, found)
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"to": "[EMAIL]"}'
    assert out[2]["content"] == "sent to [EMAIL]" and out[3]["content"][0]["text"] == "cc [EMAIL]"
    assert out[4]["function_call"]["arguments"] == '{"to": "[EMAIL]"}' and out[5]["content"] == "sent to [EMAIL]"
    assert found == Counter(EMAIL=5) and "example" not in json.dumps(out)
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == '{"to": "bob@example.com"}'  # the caller's copy


# ---------- over HTTP ----------
@pytest.mark.req("FR-1.41", "FR-1.1", "INV-1", "INV-2", "INV-10")
def test_tool_calls_pass_through_the_gateway(gw_env, tmp_path, monkeypatch):
    """A request with tools against a teacher behind MockTransport that calls two tools: the response has those calls
    as made (ids kept), `finish_reason: "tool_calls"` and null content; the teacher got the tools, the choice and the
    parallel flag under the configured model; the student endpoint got nothing and nothing was stored."""
    sdk()
    from openai.types.chat import ChatCompletion

    gw = gw_env()
    teacher = ToolCallingServer([CALL, CALL2])
    rt, student = _live(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    body = {"model": "gpt-4o", "messages": ASK, "tools": WEATHER, "tool_choice": "auto", "parallel_tool_calls": True}
    r = TestClient(gw.app).post(CHAT, json=body)
    assert r.status_code == 200, r.text
    ChatCompletion.model_validate(r.json())
    (choice,) = r.json()["choices"]
    assert choice["message"]["tool_calls"] == [CALL, CALL2] and choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None and "function_call" not in choice["message"]
    assert r.json()["ratchet"]["served_by"] == "teacher" and r.headers["x-ratchet-served-by"] == "teacher"
    (sent,) = _bodies(teacher)
    assert (sent["model"], sent["tools"], sent["tool_choice"], sent["parallel_tool_calls"]) == (
        OPEN_TEACHER,
        WEATHER,
        "auto",
        True,
    )
    assert _bodies(student) == [] and rt.ledger.examples() == []
    assert _rows(rt.ledger) == [{"task": "default", "route": "tools", "served_by": "teacher", "stored": 0, "uses_tools": 1}]
    (task,) = TestClient(gw.app).get("/ratchet/stats").json()["tasks"]
    assert (task["tool_requests"], task["routes"]) == (1, {"tools": 1})
    # A teacher failure on the tool path is mapped like any other (INV-7): its 400 about the request is the client's.
    refusal = {"error": {"message": '"auto" tool choice requires --enable-auto-tool-choice', "type": "BadRequestError"}}
    rt.ratchet.teacher.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(400, json=refusal)))
    r = TestClient(gw.app).post(CHAT, json=body)
    assert r.status_code == 400 and "enable-auto-tool-choice" in r.json()["error"]["message"]


@pytest.mark.req("FR-1.41", "FR-1.11", "INV-7", "INV-16")
def test_a_malformed_teacher_tool_call_is_a_502_and_nothing_is_stored(gw_env, tmp_path, monkeypatch):
    """A tool call the client could not use (here: no function name) is a 502 `teacher_unavailable` naming
    `bad_upstream_response`, not a 500 and not a partial answer; nothing is stored and the student is never asked."""
    gw = gw_env()
    teacher = ToolCallingServer([CALL, {"id": "call_x", "type": "function", "function": {"arguments": "{}"}}])
    rt, student = _live(gw, tmp_path, teacher, teacher_retries=0)
    monkeypatch.setattr(gw, "rt", rt)
    r = TestClient(gw.app).post(CHAT, json={"messages": ASK, "tools": WEATHER})
    assert r.status_code == 502, r.text
    assert r.json()["error"]["code"] == "teacher_unavailable" and "bad_upstream_response" in r.json()["error"]["message"]
    assert _bodies(student) == [] and rt.ledger.examples() == []


@pytest.mark.req("FR-1.41", "FR-1.1")
def test_legacy_function_calling_round_trips(gw_env, tmp_path, monkeypatch):
    """`functions` and `function_call` reach the teacher as `tools` and `tool_choice`, a legacy history as tool turns,
    and the call comes back as `message.function_call` with `finish_reason: "function_call"` (streamed too). A request
    that sends `tools` as well gets the current form, and so does a modern request whose teacher answers the old way."""
    sdk()
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    gw = gw_env()
    teacher = ToolCallingServer([CALL])
    rt, _ = _live(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    history = [
        *ASK,
        {"role": "assistant", "content": None, "function_call": {"name": "get_weather", "arguments": '{"city": "Oslo"}'}},
        {"role": "function", "name": "get_weather", "content": "4 C"},
    ]
    body = {"messages": history, "functions": [WEATHER_FN], "function_call": {"name": "get_weather"}}
    r = c.post(CHAT, json=body)
    assert r.status_code == 200, r.text
    ChatCompletion.model_validate(r.json())
    (choice,) = r.json()["choices"]
    assert choice["message"]["function_call"] == CALL["function"] and "tool_calls" not in choice["message"]
    assert choice["finish_reason"] == "function_call" and choice["message"]["content"] is None
    (sent,) = _bodies(teacher)
    assert sent["tools"] == [{"type": "function", "function": WEATHER_FN}] and "functions" not in sent
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}} and "function_call" not in sent
    assert sent["parallel_tool_calls"] is False  # one call, as a legacy message holds
    call_id = sent["messages"][1]["tool_calls"][0]["id"]
    assert sent["messages"][2] == {"role": "tool", "tool_call_id": call_id, "content": "4 C"}
    chunks = _stream(c, body)
    for chunk in chunks:
        ChatCompletionChunk.model_validate(chunk)
    deltas = [ch["delta"] for e in chunks for ch in e["choices"]]
    assert {"function_call": CALL["function"]} in deltas
    assert [ch["finish_reason"] for e in chunks for ch in e["choices"] if ch["finish_reason"]] == ["function_call"]
    assert rt.ledger.examples() == []

    # A teacher that makes two calls anyway: the legacy form holds one, so the first is returned.
    two = ToolCallingServer([CALL, CALL2])
    rt, _ = _live(gw, tmp_path, two)
    monkeypatch.setattr(gw, "rt", rt)
    first = c.post(CHAT, json=body).json()["choices"][0]
    assert first["message"]["function_call"] == CALL["function"] and first["finish_reason"] == "function_call"
    both = c.post(CHAT, json={**body, "tools": WEATHER}).json()["choices"][0]
    assert both["message"]["tool_calls"] == [CALL, CALL2] and both["finish_reason"] == "tool_calls"
    old = ToolCallingServer([CALL], legacy=True)  # a server that still answers with function_call
    rt, _ = _live(gw, tmp_path, old)
    monkeypatch.setattr(gw, "rt", rt)
    modern = c.post(CHAT, json={"messages": ASK, "tools": WEATHER}).json()["choices"][0]
    (call,) = modern["message"]["tool_calls"]
    assert call["function"] == CALL["function"] and CALL_ID.fullmatch(call["id"]) and modern["finish_reason"] == "tool_calls"


@pytest.mark.req("FR-1.41", "FR-1.6", "FR-1.12")
def test_streamed_tool_calls_rebuild_with_the_sdks_accumulator(gw_env, tmp_path, monkeypatch):
    """Streamed: the role chunk, the text the model wrote beside its calls, one delta per call with the whole
    arguments, then the finish chunk. Every chunk passes `ChatCompletionChunk`, and the SDK's
    `ChatCompletionStreamState` rebuilds the same message as the non-streamed response; with `include_usage` the last
    chunk has the usage."""
    sdk()
    from openai.lib.streaming.chat import ChatCompletionStreamState
    from openai.types.chat import ChatCompletionChunk

    gw = gw_env()
    teacher = ToolCallingServer([CALL, CALL2], content="Checking both cities.")
    rt, _ = _live(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    body = {"messages": ASK, "tools": WEATHER, "stream_options": {"include_usage": True}}
    chunks = _stream(c, body)
    state = ChatCompletionStreamState()
    for chunk in chunks:
        state.handle_chunk(ChatCompletionChunk.model_validate(chunk))
    final = state.get_final_completion()
    (choice,) = final.choices
    assert choice.finish_reason == "tool_calls" and choice.message.content == "Checking both cities."
    rebuilt = [
        {"id": t.id, "type": t.type, "function": {"name": t.function.name, "arguments": t.function.arguments}}
        for t in choice.message.tool_calls
    ]
    assert rebuilt == [CALL, CALL2]
    whole = c.post(CHAT, json={"messages": ASK, "tools": WEATHER}).json()
    assert whole["choices"][0]["message"]["tool_calls"] == rebuilt and chunks[-1]["usage"] == whole["usage"]
    call_deltas = [ch["delta"]["tool_calls"] for e in chunks for ch in e["choices"] if "tool_calls" in ch["delta"]]
    assert call_deltas == [[{"index": 0, **CALL}], [{"index": 1, **CALL2}]]

    # Calls alone: the role chunk's content is null and no content chunk is sent.
    teacher = ToolCallingServer([CALL])
    rt, _ = _live(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    chunks = _stream(c, {"messages": ASK, "tools": WEATHER})
    deltas = [ch["delta"] for e in chunks for ch in e["choices"]]
    assert deltas[0] == {"role": "assistant", "content": None, "refusal": None}
    assert not any("content" in d for d in deltas[1:]) and deltas[1] == {"tool_calls": [{"index": 0, **CALL}]}
    state = ChatCompletionStreamState()
    for chunk in chunks:
        state.handle_chunk(ChatCompletionChunk.model_validate(chunk))
    message = state.get_final_completion().choices[0].message
    assert message.content is None and message.tool_calls[0].id == CALL["id"]


@pytest.mark.req("FR-1.41", "FR-1.10", "INV-12")
def test_multimodal_requests_are_teacher_only_with_their_own_route(gw_env):
    """With RATCHET_TEACHER_MODALITIES=text,image_url an image request skips the student and is never stored, logged
    with route `ineligible:content_type` (the task's routing block, FR-2.50); no image bytes reach the ledger. A pinned
    task keeps its own route, and an image request with tools is a tool request."""
    gw = gw_env(RATCHET_TEACHER_MODALITIES="text,image_url", RATCHET_REDACT_PII="1")
    led = gw.rt.ledger
    _version(led, "live", {"threshold": 0.0})
    student, teacher = RecordingProvider(A), RecordingProvider(A)
    gw.rt.ratchet.teacher = teacher
    gw.rt.ratchet.resolve_student = lambda v: (student, v.adapter_uri)
    url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    image = {
        "role": "user",
        "content": [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": url}}],
    }
    c = TestClient(gw.app)
    r = c.post(CHAT, json={"messages": [image]}, headers={"X-Ratchet-Task": TRIAGE})
    assert r.status_code == 200 and r.json()["ratchet"]["served_by"] == "teacher"
    assert student.calls == [] and teacher.calls[0].messages == [image] and led.examples() == []
    r = c.post(CHAT, json={"messages": [image]}, headers={"X-Ratchet-Task": "research-plan"})
    assert r.status_code == 200
    assert [(row["task"], row["route"], row["stored"]) for row in _rows(led)] == [
        (TRIAGE, "ineligible:content_type", 0),
        ("research-plan", "pinned", 0),
    ]
    assert "iVBORw0KGgo" not in "\n".join(led.db.iterdump())  # no image bytes anywhere in the ledger
    # An image and tools: still the teacher alone, counted as a tool request.
    r = c.post(CHAT, json={"messages": [image], "tools": WEATHER})
    assert r.status_code == 200 and _rows(led)[-1]["route"] == "tools" and _rows(led)[-1]["uses_tools"] == 1
    assert student.calls == [] and led.examples() == []


@pytest.mark.req("FR-1.41", "FR-9.7", "US-1")
def test_the_openai_sdk_runs_a_tool_loop_against_the_mock_gateway(gw):
    """The real OpenAI SDK, end to end on the mock gateway: the call comes back as `message.tool_calls`, the client runs
    the tool and sends the result, and the teacher's final answer restates it; `.stream()` rebuilds the same call, and
    the legacy `functions` argument gets `message.function_call`. Nothing is stored. Simulation: mock models."""
    client = make_openai_client(gw.app)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "Where is my order #48213? It has been a week."}]
    resp = client.chat.completions.create(model="ratchet", messages=messages, tools=generic.TOOLS)
    message = resp.choices[0].message
    assert resp.choices[0].finish_reason == "tool_calls" and message.content is None
    (call,) = message.tool_calls
    assert call.function.name == "lookup_order" and json.loads(call.function.arguments) == {"order_id": "48213"}
    messages += [message.model_dump(exclude_none=True), {"role": "tool", "tool_call_id": call.id, "content": "Shipped Monday."}]
    final = client.chat.completions.create(model="ratchet", messages=messages, tools=generic.TOOLS)
    assert final.choices[0].finish_reason == "stop" and final.choices[0].message.tool_calls is None
    assert final.choices[0].message.content.startswith('[mock teacher] The tool returned: "Shipped Monday."')
    with client.chat.completions.stream(model="ratchet", messages=messages[:1], tools=generic.TOOLS) as stream:
        streamed = stream.get_final_completion()
    assert streamed.choices[0].message.tool_calls[0].id == call.id  # the mock's ids are stable
    legacy = client.chat.completions.create(
        model="ratchet", messages=messages[:1], functions=[t["function"] for t in generic.TOOLS]
    )
    assert legacy.choices[0].finish_reason == "function_call"
    assert legacy.choices[0].message.function_call.name == "lookup_order" and legacy.choices[0].message.tool_calls is None
    assert gw.rt.ledger.examples() == []
    routes = {t["task"]: t["routes"] for t in TestClient(gw.app).get("/ratchet/stats").json()["tasks"]}
    assert routes == {"default": {"tools": 4}}
