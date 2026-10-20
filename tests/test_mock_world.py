"""The generic mock world (sim/generic.py, FR-9.7): any request the ticket and research worlds do not know gets a
deterministic answer (free text, an instance of its schema, a tool call); the mock models, the trainer and the golden
eval handle it; the `schema` and `tools` workloads; RATCHET_MOCK_STRICT; and the mock gateway through the OpenAI SDK.
Simulation numbers here are trends from mock models, never measurements."""

from __future__ import annotations

import json
import random
import time
from datetime import date
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sdk_bridge import make_openai_client

from ratchet.config import Settings
from ratchet.ledger import Ledger, Version
from ratchet.providers import GenParams
from sim import generic, research, simulate
from sim.generic import GenericItem, instance_from_schema
from sim.mock_models import STAGING, MockFleet, MockStudent, MockTeacher
from sim.simulate import parser, run
from sim.world import WORKLOADS, lookup, make_stream

HELLO = [{"role": "user", "content": "hello"}]
CATALOGUE = GenParams(tools=generic.TOOLS, tool_choice="auto")
LOOKUP = "Where is my order #48213? It has been a week."
REFUND = "Order #48213 arrived damaged. Please refund the 34.50 USD I paid."


# ---------- a small validator for the schema subset the mock fills (jsonschema is not a dependency) ----------
def _is_type(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}[t])


def _errors(value: Any, schema: Any, root: Any = None, at: str = "$") -> list[str]:
    """What of `value` breaks `schema`: type, enum, const, properties, required, additionalProperties false, items,
    prefixItems, minItems, maxItems, uniqueItems, bounds, multipleOf, minLength, maxLength, local $ref, anyOf, oneOf,
    allOf."""
    root = schema if root is None else root
    if not isinstance(schema, dict):
        return []
    out: list[str] = []
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].split("/")[1:]:
            target = target[part]
        out += _errors(value, target, root, at)
    if "const" in schema and value != schema["const"]:
        out.append(f"{at}: not the const")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{at}: {value!r} not in the enum")
    for key in ("anyOf", "oneOf"):
        if key in schema and all(_errors(value, option, root, at) for option in schema[key]):
            out.append(f"{at}: no {key} option fits")
    for part in schema.get("allOf", []):
        out += _errors(value, part, root, at)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            return [*out, f"{at}: {value!r} is not {schema['type']}"]
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out += [f"{at}: {k} missing" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            out += [f"{at}: {k} not allowed" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                out += _errors(v, props[k], root, f"{at}.{k}")
    if isinstance(value, list):
        prefix = schema.get("prefixItems", [])
        for i, v in enumerate(value):
            out += _errors(v, prefix[i] if i < len(prefix) else schema.get("items", True), root, f"{at}[{i}]")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            out.append(f"{at}: {len(value)} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) < len(value):
            out.append(f"{at}: repeated items")
    if _is_type(value, "number"):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        elo, ehi = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if (lo is not None and (value < lo or (elo is True and value == lo))) or (hi is not None and value > hi):
            out.append(f"{at}: {value} out of bounds")
        if (elo not in (None, True, False) and value <= elo) or (ehi not in (None, True, False) and value >= ehi):
            out.append(f"{at}: {value} out of exclusive bounds")
        if "multipleOf" in schema and value % schema["multipleOf"]:
            out.append(f"{at}: {value} not a multiple")
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
        out.append(f"{at}: length {len(value)}")
    return out


def _at(value: Any, key: str) -> Any:
    """The value at a schema label's path ('$/parts/0/qty')."""
    for part in key.split("/")[1:]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _decision(c: Any) -> tuple[str, Any]:
    if c.tool_calls:
        fn = c.tool_calls[0]["function"]
        return fn["name"], json.loads(fn["arguments"])
    return "reply", c.text


def _ask(text: str, params: GenParams | None = CATALOGUE, extra: tuple[dict[str, Any], ...] = ()) -> Any:
    messages = [{"role": "system", "content": generic.TOOL_SYSTEM}, {"role": "user", "content": text}, *extra]
    (c,) = MockTeacher().complete(messages, "t", params=params)
    return c


# ---------- lookup ----------
@pytest.mark.req("FR-9.7")
def test_lookup_tries_tickets_then_research_then_the_generic_world():
    ticket = make_stream("tickets", seed=3).sample(1)
    schema = GenParams(response_format=generic.SCHEMA_CASES[0].response_format())
    assert lookup(ticket.messages(), schema) is ticket  # a demo text keeps its world, whatever it is sent with
    assert lookup(ticket.messages(), strict=True) is ticket
    triage = research.ResearchStream(seed=3).sample(1)
    assert lookup(triage.messages(), CATALOGUE) is triage
    image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    for messages in (HELLO, [{"role": "system", "content": "only a system message"}], image, []):
        item = lookup(messages)
        assert isinstance(item, GenericItem) and item.task == "default" and item.answer(item.labels)
        with pytest.raises(KeyError):  # RATCHET_MOCK_STRICT=1: only demo requests, as before the generic world
            lookup(messages, strict=True)
    assert lookup(HELLO, schema).world == "schema" and lookup(HELLO, CATALOGUE).world == "tools"


@pytest.mark.req("FR-9.7")
def test_generic_items_are_deterministic_and_keyed_by_what_a_model_sees():
    msgs = [{"role": "system", "content": "Summarise."}, {"role": "user", "content": "The quarterly report is late."}]
    a, b = generic.item(msgs), generic.item(json.loads(json.dumps(msgs)), task="another-task")
    assert (a.template, a.labels, a.answer(a.labels)) == (b.template, b.labels, b.answer(b.labels))  # the task: no part
    assert b.task == "another-task" and a.template.startswith("generic:text:") and a.difficulty == "easy"
    other = generic.item([{**msgs[0], "content": "Translate."}, msgs[1]])
    assert other.template != a.template  # the system prompt stands in for the task a model never sees
    assert generic.item([msgs[1]]).template == "generic:text:-"
    # Labels come from the text's hash: different texts spread over every framing.
    assert {generic.item([{"role": "user", "content": f"question {i}"}]).labels["focus"] for i in range(60)} == set(generic.FOCI)


@pytest.mark.req("FR-9.7")
def test_free_text_world_restates_the_prompt():
    item = generic.item(HELLO)
    assert item.answer(item.labels) == f'[mock teacher] You asked: "hello". {generic.FOCUS[item.labels["focus"]]}'
    wrong = next(f for f in generic.FOCI if f != item.labels["focus"])
    assert item.answer({"focus": wrong}) != item.answer(item.labels)  # a wrong framing disagrees as a whole answer
    long = generic.item([{"role": "user", "content": " ".join(f"word{i}" for i in range(50))}])
    text = long.answer(long.labels)
    assert text.count("word") == generic.SNIPPET_WORDS and '..."' in text
    start = time.perf_counter()
    huge = generic.item([{"role": "user", "content": "[" * 1_000_000}])
    assert '"' + "[" * generic.SNIPPET_CHARS + '..."' in huge.answer(huge.labels) and time.perf_counter() - start < 0.5
    parts = [
        {"type": "text", "text": "What is"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "this?"},
    ]
    item = generic.item([{"role": "user", "content": parts}])
    assert '"What is this?"' in item.answer(item.labels)
    as_json = generic.item(HELLO, GenParams(response_format={"type": "json_object"}))
    assert as_json.world == "json" and json.loads(as_json.answer(as_json.labels))["answer"].startswith(
        '[mock teacher] You asked: "hello".'
    )


# ---------- instances of a schema ----------
POINT = {
    "type": "object",
    "properties": {"x": {"type": "integer"}, "y": {"type": "number", "minimum": -5, "maximum": -1}},
    "required": ["x", "y"],
    "additionalProperties": False,
}
SUBSET: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["a", "b", "c"]},
        "flag": {"type": "boolean"},
        "count": {"type": "integer", "minimum": 3, "maximum": 7},
        "score": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "step": {"type": "integer", "minimum": 1, "maximum": 50, "multipleOf": 5},
        "old_style": {"type": "integer", "minimum": 0, "exclusiveMinimum": True, "maximum": 2},
        "name": {"type": "string"},
        "when": {"type": "string", "format": "date"},
        "at": {"type": "string", "format": "date-time"},
        "id": {"type": "string", "format": "uuid"},
        "code": {"type": "string", "minLength": 12, "maxLength": 14},
        "tiny": {"type": "string", "maxLength": 4},
        "maybe": {"type": ["string", "null"]},
        "either": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/point"}]},
        "fixed": {"const": {"v": 1}},
        "tags": {"type": "array", "items": {"enum": ["x", "y", "z"]}},
        "pair": {"type": "array", "prefixItems": [{"type": "boolean"}, {"const": 9}], "items": False},
        "many": {"type": "array", "items": {"type": "integer"}, "minItems": 4},
        "unique": {"type": "array", "items": {"enum": [1, 2]}, "minItems": 1, "maxItems": 2, "uniqueItems": True},
        "both": {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "boolean"}}, "required": ["a"]},
                {"properties": {"b": {"const": 2}}, "required": ["b"]},
            ]
        },
        "nested": {"$ref": "#/$defs/point"},
    },
    "additionalProperties": False,
    "$defs": {"point": POINT},
}
SUBSET["required"] = list(SUBSET["properties"])


@pytest.mark.req("FR-9.7")
def test_instance_from_schema_fills_the_structured_outputs_subset():
    seen: dict[str, set[str]] = {"kind": set(), "count": set(), "flag": set()}
    for seed in range(200):
        v = instance_from_schema(SUBSET, seed)
        assert _errors(v, SUBSET) == [], (seed, v)
        assert instance_from_schema(SUBSET, seed) == v  # the same seed, the same instance
        assert (v["when"], v["at"], v["fixed"], v["both"]["b"]) == ("2026-10-20", "2026-10-20T09:30:00Z", {"v": 1}, 2)
        assert v["maybe"] == "mock text" and v["either"] is not None and len(v["tags"]) in (1, 2)
        assert v["step"] % 5 == 0 and v["old_style"] in (1, 2) and 0 < v["score"] <= 1
        for key in seen:
            seen[key].add(json.dumps(v[key]))
    assert len(seen["kind"]) == 3 and len(seen["count"]) >= 4 and len(seen["flag"]) == 2  # drawn, not fixed
    assert instance_from_schema({"type": "object", "required": ["a"]}, 1) == {"a": "mock text"}
    assert instance_from_schema({}, 1) == "mock text" and instance_from_schema(True, 1) == "mock text"
    assert instance_from_schema({"type": "array", "items": {"type": "integer"}, "maxItems": 0}, 1) == []


@pytest.mark.req("FR-9.7")
def test_instance_from_schema_ends_and_never_raises_on_hostile_schemas():
    recursive = {"type": "object", "properties": {"child": {"$ref": "#"}}, "required": ["child"]}
    loop = {"$ref": "#/$defs/a", "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}}
    explode = {
        "type": "array",
        "minItems": 50,
        "items": {"type": "array", "minItems": 50, "items": {"type": "array", "minItems": 50}},
    }
    wide = {"type": "number", "minimum": -1.7e308, "maximum": 1.7e308}
    huge_int = {"type": "integer", "minimum": 10**400}
    start = time.perf_counter()
    for schema in (recursive, loop, explode, wide, huge_int, {"type": "integer", "minimum": 5, "maximum": 1}):
        json.dumps(instance_from_schema(schema, 3), allow_nan=False)
    assert time.perf_counter() - start < 1.0
    depth, node = 0, instance_from_schema(recursive, 3)
    while isinstance(node, dict) and node:
        node, depth = node["child"], depth + 1
    assert 0 < depth <= generic.MAX_DEPTH + 1

    rng = random.Random(11)
    atoms = [None, True, False, 0, -1, 1.5, 10**30, "x", "integer", "null", [], {}, [None], {"$ref": "#"}, {"$ref": 5}]
    keys = ["type", "enum", "const", "properties", "items", "prefixItems", "anyOf", "oneOf", "allOf", "minimum",
            "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "minLength",
            "maxLength", "format", "required", "$ref", "$defs", "uniqueItems", "additionalProperties"]  # fmt: skip

    def junk(level: int = 0) -> Any:
        r = rng.random()
        if level > 4 or r < 0.3:
            return rng.choice(atoms)
        if r < 0.45:
            return [junk(level + 1) for _ in range(rng.randint(0, 3))]
        return {rng.choice(keys): junk(level + 1) for _ in range(rng.randint(0, 5))}

    for seed in range(1500):
        schema = junk()
        json.dumps(instance_from_schema(schema, seed), allow_nan=False)
        rf = {"type": "json_schema", "json_schema": {"name": "j", "schema": schema}}
        tools = [{"type": "function", "function": {"name": "f", "parameters": schema}}]
        for params in (GenParams(response_format=rf), GenParams(tools=tools), GenParams(tools=tools, tool_choice="required")):
            item = generic.item(HELLO, params)
            json.dumps([item.answer(item.labels), item.calls(item.labels)], allow_nan=False)
            for _ in range(2):  # every label space has a wrong value to draw (the mock student needs one)
                assert all(any(v != item.labels[k] for v in space) for k, space in item.label_space.items())


@pytest.mark.req("FR-9.7")
def test_instance_from_schema_agrees_with_jsonschema():
    jsonschema = pytest.importorskip("jsonschema")
    modern = {**SUBSET, "properties": {k: v for k, v in SUBSET["properties"].items() if k != "old_style"}}  # draft 4 out
    modern["required"] = list(modern["properties"])
    schemas = [modern, *(c.schema for c in generic.SCHEMA_CASES + generic.DRIFT_SCHEMA_CASES)]
    for schema in schemas:
        validator = jsonschema.Draft202012Validator(schema)
        for seed in range(100):
            assert not list(validator.iter_errors(instance_from_schema(schema, seed)))


@pytest.mark.req("FR-9.7")
def test_schema_answers_stay_valid_for_any_labels_and_wrong_ones_change_enums_first():
    stream = make_stream("schema", seed=1, drift_day=1)
    rng = random.Random(0)
    worlds = set()
    for _ in range(300):
        item = stream.sample(2)
        schema = item.params.response_format["json_schema"]["schema"]
        worlds.add(item.task)
        assert _errors(json.loads(item.answer(item.labels)), schema) == []
        drawn = {k: rng.choice(v) for k, v in item.label_space.items()}  # what a guessing student writes
        assert _errors(json.loads(item.answer(drawn)), schema) == []
        kinds = list(item.data["kinds"].values())
        assert kinds == sorted(kinds) and kinds[0] == 0  # enums first: a slip changes an enum
    assert worlds == {c.task for c in generic.SCHEMA_CASES + generic.DRIFT_SCHEMA_CASES}

    # A corrupted deploy is confidently wrong: its samples agree, on enum values that are not the teacher's.
    item = next(i for i in (stream.sample(1) for _ in range(200)) if i.fragile)
    student = MockStudent("v", {"templates": {}, "texts": set()}, corrupted=True)
    out = student.complete(item.messages(), "v", n=3, params=item.params)
    assert len({c.text for c in out}) == 1
    got, right = json.loads(out[0].text), json.loads(item.answer(item.labels))
    enums = [k for k, kind in item.data["kinds"].items() if kind == 0]
    assert enums and all(_at(got, k) != _at(right, k) for k in enums)
    assert MockTeacher().complete(item.messages(), "t", params=item.params)[0].text == item.answer(item.labels)


# ---------- the tool world ----------
@pytest.mark.req("FR-9.7")
def test_mock_teacher_calls_the_catalogue_with_arguments_read_from_the_prompt():
    c = _ask(LOOKUP)
    assert (c.text, c.finish_reason) == ("", "tool_calls") and c.out_tokens > c.reasoning_tokens
    (call,) = c.tool_calls
    assert call["type"] == "function" and call["id"].startswith("call_") and len(call["id"]) == 29
    assert call["function"]["name"] == "lookup_order" and json.loads(call["function"]["arguments"]) == {"order_id": "48213"}
    assert _ask(LOOKUP).tool_calls == c.tool_calls  # the same request, the same call (id included)
    assert _decision(_ask(REFUND)) == ("refund", {"order_id": "48213", "amount": 34.5, "reason": "damaged"})
    reasons = {
        "I was charged twice for order #51234, please refund 19.99 USD.": "duplicate_charge",
        "Order #51234 never arrived. Please refund my 20.00 USD.": "not_received",
        "You sent the wrong item for order #51234. I want a refund of 12.00 USD.": "wrong_item",
        "Please refund order #51234, $9.50, I changed my mind.": "changed_mind",
    }
    for text, reason in reasons.items():
        name, args = _decision(_ask(text))
        assert name == "refund" and args["reason"] == reason and args["order_id"] == "51234", text
    reply = _ask("Do you ship to Canada?")  # nothing to look up: a plain reply
    assert reply.tool_calls is None and reply.finish_reason == "stop" and reply.text.startswith("[mock teacher] ")
    # After the tool's result, a final text answer that restates it.
    extra = (
        {"role": "assistant", "content": None, "tool_calls": c.tool_calls},
        {"role": "tool", "tool_call_id": call["id"], "content": "Order 48213 shipped on Monday."},
    )
    final = _ask(LOOKUP, extra=extra)
    assert final.tool_calls is None and final.finish_reason == "stop"
    assert final.text.startswith('[mock teacher] The tool returned: "Order 48213 shipped on Monday."')
    as_json = _ask(LOOKUP, GenParams(tools=generic.TOOLS, response_format={"type": "json_object"}), extra)
    assert json.loads(as_json.text)["answer"].startswith("[mock teacher] The tool returned")


@pytest.mark.req("FR-9.7")
def test_tool_choice_and_client_tools():
    forced = GenParams(tools=generic.TOOLS, tool_choice={"type": "function", "function": {"name": "lookup_order"}})
    assert _decision(_ask(REFUND, forced)) == ("lookup_order", {"order_id": "48213"})
    assert _ask(REFUND, GenParams(tools=generic.TOOLS, tool_choice="none")).tool_calls is None  # free text
    required = GenParams(tools=generic.TOOLS, tool_choice="required")
    assert _ask("Do you ship to Canada?", required).tool_calls[0]["function"]["name"] == "lookup_order"
    weather = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"enum": ["c", "f"]},
            "days": {"type": "integer", "minimum": 1, "maximum": 3},
        },
        "required": ["city", "unit", "days"],
        "additionalProperties": False,
    }
    name, args = _decision(
        _ask(
            "Weather in Pune tomorrow?",
            GenParams(tools=[{"type": "function", "function": {"name": "get_weather", "parameters": weather}}]),
        )
    )
    assert name == "get_weather" and _errors(args, weather) == []
    shop = [
        {
            "type": "function",
            "function": {"name": "lookup_order", "parameters": {"properties": {"order_id": {"type": "integer"}}}},
        }
    ]
    assert _decision(_ask("Status of #48213?", GenParams(tools=shop))) == ("lookup_order", {"order_id": 48213})


@pytest.mark.req("FR-9.7", "FR-9.2")
def test_mock_student_tool_calls_improve_with_training():
    stream = make_stream("tools", seed=5)
    items = [stream.sample(1) for _ in range(300)]
    teacher = MockTeacher()

    def accuracy(student: MockStudent) -> float:
        right = 0
        for it in items:
            want = teacher.complete(it.messages(), "t", params=it.params)[0]
            right += _decision(student.complete(it.messages(), "s", n=1, params=it.params)[0]) == _decision(want)
        return right / len(items)

    untrained = MockStudent("s", {"templates": {}, "texts": set()})
    trained = MockStudent("s", {"templates": {items[0].template: 200}, "texts": set()})
    assert accuracy(trained) > 0.75 > 0.6 > accuracy(untrained)
    (sample,) = trained.complete(items[0].messages(), "s", n=1, logprobs=True, params=items[0].params)
    if sample.tool_calls:  # a tool call has no content, so it lists no content tokens
        assert sample.text == "" and sample.logprobs_content is None and sample.finish_reason == "tool_calls"


# ---------- training and the golden eval ----------
def _row(messages: list[dict[str, Any]], answer: str, params: dict[str, Any] | None = None, task: str = "default") -> dict:
    return {"task": task, "messages": [*messages, {"role": "assistant", "content": answer}], "params": params}


@pytest.mark.req("FR-9.7", "FR-9.3")
def test_mock_fleet_trains_and_evaluates_on_generic_and_ticket_rows():
    ticket = make_stream("tickets", seed=2).sample(1)
    case = make_stream("schema", seed=2).sample(1)
    tool = make_stream("tools", seed=2).sample(1)
    teacher = MockTeacher()

    def answer(item: Any) -> str:
        return teacher.complete(item.messages(), "t", params=item.params)[0].text

    rows = [
        _row(ticket.messages(), answer(ticket), task="ticket-triage"),
        _row(HELLO, "hi"),
        _row(case.messages(), answer(case), {"response_format": case.params.response_format}, case.task),
        _row(tool.messages(), "We are looking into it.", {"tools": generic.TOOLS}, tool.task),  # stored tools
        _row([{"role": "system", "content": "only a system message"}], "ok"),
        _row([{"role": "user", "content": [{"type": "text", "text": "parts"}]}], "fine"),
    ]
    for strict in (False, True):  # strict mode never applies to rows already stored
        fleet = MockFleet(strict=strict)
        assert fleet.train(rows, "base", "student-v1") == "student-v1"
        known = fleet.knowledge["student-v1"]
        assert {ticket.template, case.template, tool.template, "generic:text:-"} <= set(known["templates"])
        assert ticket.text in known["texts"] and "hello" in known["texts"]
        report = fleet.evaluate("student-v1", rows)
        assert report["golden_n"] == len(rows) and 0.5 <= report["threshold"] <= 1.01
    # The students it serves are strict when it is: a request the demo did not write is the gateway's 400.
    version = Version(1, 1, "base", "student-v1", len(rows), "h", {}, "live", 100)
    fleet = MockFleet(strict=True)
    fleet.train(rows, "base", "student-v1")
    student, model = fleet.resolve(version)
    assert model == "student-v1" and student.strict
    with pytest.raises(KeyError):
        student.complete(HELLO, model)
    assert student.complete(ticket.messages(), model)[0].text  # demo requests still work
    staging = MockFleet()
    staging.train(rows, "base", "student-v1")
    student, _ = staging.resolve(Version(1, 1, "base", "student-v1", 1, "h", {}, "shadow", 0))
    assert student.base_url == STAGING and student.complete(HELLO, "student-v1")[0].text.startswith("[mock teacher] ")


@pytest.mark.req("FR-9.7")
def test_strict_teacher_and_unreadable_scout_writer_requests():
    from ratchet.scout.prompts import WRITE_SYSTEM

    writer = [{"role": "system", "content": WRITE_SYSTEM}, {"role": "user", "content": "not JSON"}]
    assert MockTeacher().complete(writer, "t")[0].text.startswith("[mock teacher] ")  # the generic world answers
    for messages in (writer, HELLO):
        with pytest.raises(KeyError):
            MockTeacher(strict=True).complete(messages, "t")
    assert not Settings().mock_strict


@pytest.mark.req("FR-9.7")
def test_mock_strict_setting(monkeypatch):
    monkeypatch.setenv("RATCHET_MOCK_STRICT", "1")
    assert Settings().mock_strict
    monkeypatch.setenv("RATCHET_MOCK_STRICT", "0")
    assert not Settings().mock_strict


# ---------- workloads ----------
def _simulate(monkeypatch: Any, workload: str, days: int, requests: int) -> tuple[dict[str, Any], Ledger]:
    """`sim.simulate.run` on an in-memory ledger, and that ledger (caught from `build`)."""
    args = parser().parse_args(
        ["--workload", workload, "--days", str(days), "--requests", str(requests), "--min-audits", "5", "--min-shadow", "40"]
    )
    built: list[Any] = []
    real = simulate.build

    def build(*a: Any, **kw: Any) -> Any:
        built.append(real(*a, **kw))
        return built[-1]

    monkeypatch.setattr("sim.simulate.build", build)
    res = run(args)
    return res, built[0][1]


@pytest.mark.req("FR-9.7", "FR-9.4")
def test_schema_workload_student_takes_over(monkeypatch):
    """Simulation, mock models: structured-output traffic is learned like tickets, and every stored example keeps the
    schema it was answered under."""
    res, led = _simulate(monkeypatch, "schema", 4, 300)
    share = [d["student_share"] for d in res["days"]]
    assert share[0] == 0 and share[-1] > 0.5 and share[-1] > share[1]
    assert [d["audit_agreement"] for d in res["days"]][-1] >= 0.9
    examples = led.examples()
    assert {e["task"] for e in examples} == {c.task for c in generic.SCHEMA_CASES}  # drift day 5 is past the run
    assert all(json.loads(e["params"])["response_format"]["type"] == "json_schema" for e in examples)


@pytest.mark.req("FR-9.7")
def test_tools_workload_runs_with_tools_kept_from_both_models(monkeypatch):
    """Simulation, mock models. The default allowlists forward `tools` to neither model yet (providers.NOT_YET_FORWARDED),
    so both answer the tools workload's prompts as free text, and that prose is what is stored and learned."""
    res, led = _simulate(monkeypatch, "tools", 3, 300)
    share = [d["student_share"] for d in res["days"]]
    assert res["totals"]["requests"] == 900 and share[0] == 0 and share[-1] > share[0]
    examples = led.examples(task="order-support")
    assert examples and all(e["params"] is None and e["completion"].startswith("[mock teacher] ") for e in examples)


@pytest.mark.req("FR-9.7", "INV-8")
def test_demo_day_runs_the_schema_and_tools_workloads(gw):
    assert gw.WORKLOADS == WORKLOADS
    c = TestClient(gw.app)
    for workload in ("schema", "tools"):
        r = c.post(f"/ratchet/demo/day?requests=100&workload={workload}")
        assert r.status_code == 200, r.text
        assert (r.json()["workload"], r.json()["simulation"]) == (workload, True)
    tasks = {t["task"] for t in c.get("/ratchet/stats").json()["tasks"]}
    assert {c.task for c in generic.SCHEMA_CASES} | {"order-support"} <= tasks
    assert c.post("/ratchet/demo/day?workload=nope").status_code == 400


# ---------- the mock gateway through the OpenAI SDK ----------
class Part(BaseModel):
    part: Literal["screen", "battery", "hinge"]
    qty: int = Field(ge=1, le=3)


class Claim(BaseModel):
    covered: bool
    decision: Literal["approve", "deny", "review"]
    months: int
    bought: date
    parts: list[Part]
    note: str | None


@pytest.mark.req("FR-9.7", "FR-1.1", "US-1")
def test_sdk_gets_answers_and_structured_outputs_from_the_mock_gateway(gw):
    calls: list[Any] = []
    client = make_openai_client(gw.app, calls=calls)
    resp = client.chat.completions.create(model="ratchet", messages=HELLO)
    assert resp.choices[0].message.content.startswith('[mock teacher] You asked: "hello".')
    assert resp.choices[0].finish_reason == "stop"
    parsed = client.chat.completions.parse(
        model="ratchet", messages=[{"role": "user", "content": "Is my cracked screen still covered?"}], response_format=Claim
    )
    message = parsed.choices[0].message
    assert isinstance(message.parsed, Claim) and message.parsed.parts and message.parsed.bought == date(2026, 10, 20)
    schema = json.loads(calls[-1].read())["response_format"]["json_schema"]["schema"]  # what the SDK sent
    assert _errors(json.loads(message.content), schema) == []
    stats = TestClient(gw.app).get("/ratchet/stats").json()
    assert stats["totals"]["requests"] == 2


@pytest.mark.req("FR-9.7")
def test_a_mock_tool_call_is_refused_until_tool_calls_pass_through(gw_env):
    """With `tools` forwarded to the teacher, the mock teacher calls one, and the gateway refuses that answer as a real
    one (502 tool_calls_unsupported): a response cannot carry tool calls yet. Nothing is stored."""
    body = {"messages": [{"role": "user", "content": LOOKUP}], "tools": generic.TOOLS}
    gw = gw_env(RATCHET_TEACHER_PARAMS="all")
    r = TestClient(gw.app).post("/v1/chat/completions", json=body)
    assert r.status_code == 502 and r.json()["error"]["code"] == "tool_calls_unsupported"
    assert gw.rt.ledger.examples() == []
    gw = gw_env(RATCHET_TEACHER_PARAMS="")  # the default: no model sees the tools, so the teacher answers in prose
    r = TestClient(gw.app).post("/v1/chat/completions", json=body)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"].startswith("[mock teacher] ")
