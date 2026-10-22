"""Structured outputs (FR-1.40, FR-14.2, ADR 0042): the JSON Schema check (`ratchet/jsonschema_lite.py`), fields derived
from a schema and auto tasks (`ratchet/schema_tasks.py`), the contract a `response_format` binds to a request's task
(`tasks.Task.bound`, `tasks.for_row`), one repair of a teacher answer that breaks it, the json_object fallback for a
teacher that refuses json_schema, and what clients see (`ratchet.task`, `ratchet.schema_valid`, the OpenAI SDK's
`.parse()`). Models are `RecordingProvider`s or `FakeOpenAIServer`s behind `httpx.MockTransport`; nothing calls a real
API.
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any, Literal

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient
from sdk_bridge import make_openai_client, sdk

from ratchet import jsonschema_lite, schema_tasks, tasks, taskspec
from ratchet.compare import FieldSpec
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion, GenParams, OpenAICompatProvider
from ratchet.router import USAGE_PARTS, Ratchet
from ratchet.schema_tasks import Contract, auto_name, contract_for, derive, forwardable, strip_annotations

CHAT = "/v1/chat/completions"
ASK = [{"role": "system", "content": "Route the ticket."}, {"role": "user", "content": "I was charged twice, order 4711"}]
TICKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["billing", "technical", "account"]},
        "priority": {"type": "string", "enum": ["low", "high"]},
        "reply": {"type": "string"},
    },
    "required": ["category", "priority", "reply"],
    "additionalProperties": False,
}
GOOD = json.dumps({"category": "billing", "priority": "high", "reply": "Checking the charge."})
OTHER = json.dumps({"category": "technical", "priority": "low", "reply": "Restart it."})
BAD = json.dumps({"category": "refund", "priority": "high", "reply": "Refunded."})  # not in the enum
PINNED_ENV = {
    "RATCHET_API_KEYS": "",
    "RATCHET_API_KEY_DAILY_REQUESTS": "0",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
    "RATCHET_REDACT_PII": "1",
    "RATCHET_BOM_SIGNING_KEY": "",
    "RATCHET_ADMIN_KEYS": "",
    "RATCHET_UNKNOWN_TASKS": "distill",
    "RATCHET_MAX_AUTO_TASKS": "200",
    "RATCHET_SCHEMA_REPAIR": "1",
    "RATCHET_MERGE_SYSTEM": "0",
    "RATCHET_TEACHER_PARAMS": "",
    "RATCHET_STUDENT_PARAMS": "",
    "RATCHET_MOCK_STRICT": "0",
}


def _rf(schema: Any = None, name: str = "ticket", strict: bool = True) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": name, "strict": strict, "schema": TICKET_SCHEMA if schema is None else schema}
    return {"type": "json_schema", "json_schema": spec}


def _params(schema: Any = None, name: str = "ticket") -> GenParams:
    return GenParams(response_format=_rf(schema, name))


def _settings(**overrides: Any) -> Settings:
    """Every value these tests depend on, set explicitly, so RATCHET_* variables in the shell cannot move a result."""
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "teacher_reasoning_tokens": 300,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "teacher_max_tokens": 8192,
        "student_max_tokens": 1024,
        "teacher_reasoning_headroom": 0,
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "unknown_tasks": "distill",
        "max_auto_tasks": 200,
        "schema_repair": True,
        "merge_system": False,
    }
    return Settings(**{**base, **overrides})


def _ratchet(
    teacher: Any, student: Any = None, *, led: Ledger | None = None, live: dict[str, Any] | None = None, **settings: Any
) -> tuple[Ratchet, Ledger]:
    """A Ratchet on a fresh ledger (cold start unless `live` gives a live version's eval)."""
    led = led or Ledger(":memory:")
    if live is not None:
        _version(led, "live", live)
    student = student or RecordingProvider(GOOD)
    return Ratchet(_settings(**settings), led, teacher, lambda v: (student, v.adapter_uri), seed=1), led


def _events(led: Ledger, kind: str) -> list[str]:
    return [e["detail"] for e in led.events() if e["kind"] == kind]


def _requests(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute("SELECT * FROM requests ORDER BY id")]


# ---------- jsonschema_lite ----------
@pytest.mark.req("FR-1.40")
def test_validator_checks_required_enum_and_additional_properties():
    """The strict subset's object rules. Errors name the schema's paths and expectations, never a value or key from the
    answer, so they can go into an event or a repair message."""
    assert jsonschema_lite.validate(json.loads(GOOD), TICKET_SCHEMA) == []
    secret = "sk-proj-SECRETSECRETSECRET1234"
    errors = jsonschema_lite.validate({"category": secret, "reply": "x", secret: 1}, TICKET_SCHEMA)
    assert errors == [
        "$.priority: missing (required)",
        "$.category: not one of the values in 'enum'",
        "$: has a property the schema does not list (additionalProperties is false)",
    ]
    assert all(secret not in e for e in errors)
    assert jsonschema_lite.validate([1], TICKET_SCHEMA) == ["$: expected object"]
    # max_errors stops early; check() gives the path apart from the problem.
    assert jsonschema_lite.check({}, TICKET_SCHEMA, max_errors=1) == [("$.category", "missing (required)")]
    # additionalProperties as a schema checks the extra values, under `.*` (never the answer's key).
    typed = {"type": "object", "additionalProperties": {"type": "integer"}}
    assert jsonschema_lite.validate({"a": 1, "b": "x"}, typed) == ["$.*: expected integer"]
    assert jsonschema_lite.validate({"a-b": "x"}, {"properties": {"a-b": {"type": "integer"}}}) == ['$["a-b"]: expected integer']


@pytest.mark.req("FR-1.40")
def test_validator_types_and_nullable():
    """`type` names and lists (null included), JSON's integer (1.0 is one, true is not), NaN refused."""
    nullable = {"type": ["string", "null"]}
    assert jsonschema_lite.validate(None, nullable) == [] and jsonschema_lite.validate("x", nullable) == []
    assert jsonschema_lite.validate(3, nullable) == ["$: expected string or null"]
    assert jsonschema_lite.validate(1.0, {"type": "integer"}) == []
    for value in (1.5, True, "1"):
        assert jsonschema_lite.validate(value, {"type": "integer"}) == ["$: expected integer"], value
    assert jsonschema_lite.validate(True, {"type": "number"}) == ["$: expected number"]
    assert jsonschema_lite.validate(1, {"type": "boolean"}) == ["$: expected boolean"]
    assert jsonschema_lite.validate(float("nan"), {}) == ["$: not a JSON value (NaN or infinity)"]
    assert jsonschema_lite.validate(float("inf"), {"type": "number"}) == ["$: expected number"]
    assert jsonschema_lite.validate("x", {"type": "unheard-of"}) == []  # an unknown type name is ignored
    any_of = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert jsonschema_lite.validate(None, any_of) == [] and jsonschema_lite.validate(2, any_of) == [
        "$: matches none of the schemas in 'anyOf'"
    ]


@pytest.mark.req("FR-1.40")
def test_validator_resolves_refs_and_guards_against_loops():
    tree = {
        "$defs": {"node": {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#/$defs/node"}}}}},
        "$ref": "#/$defs/node",
    }
    assert jsonschema_lite.validate({"kids": [{"kids": []}, {"kids": [{"kids": []}]}]}, tree) == []
    assert jsonschema_lite.validate({"kids": [{"kids": 3}]}, tree) == ["$.kids[0].kids: expected array"]
    loop = {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}
    assert jsonschema_lite.validate(1, loop) == ["$: $ref #/$defs/a loops back to itself without reaching a value"]
    assert jsonschema_lite.validate(1, {"$ref": "#/$defs/missing"}) == ["$: a $ref the schema does not define"]
    assert jsonschema_lite.validate(1, {"$ref": "https://example.com/s.json"}) == ["$: a $ref the schema does not define"]
    escaped = {"definitions": {"a/b": {"type": "string"}}, "properties": {"x": {"$ref": "#/definitions/a~1b"}}}
    assert jsonschema_lite.validate({"x": 1}, escaped) == ["$.x: expected string"]
    root = {"type": "object", "properties": {"self": {"anyOf": [{"$ref": "#"}, {"type": "null"}]}}}
    assert jsonschema_lite.validate({"self": {"self": None}}, root) == []


@pytest.mark.req("FR-1.40")
def test_validator_combinators_const_items_and_bounds():
    one_of = {"oneOf": [{"type": "integer"}, {"type": "number"}]}  # an integer is both
    assert jsonschema_lite.validate(1.5, one_of) == []
    assert jsonschema_lite.validate(1, one_of) == ["$: matches more than one of the schemas in 'oneOf'"]
    assert jsonschema_lite.validate("x", one_of) == ["$: matches none of the schemas in 'oneOf'"]
    all_of = {"allOf": [{"type": "integer"}, {"minimum": 3}]}
    assert jsonschema_lite.validate(2, all_of) == ["$: less than 3 (minimum)"]
    assert jsonschema_lite.validate(True, {"const": 1}) == ["$: not the value in 'const'"]
    assert jsonschema_lite.validate(1.0, {"const": 1}) == [] and jsonschema_lite.validate(1, {"enum": [True]}) != []
    arr = {"type": "array", "items": {"type": "string", "maxLength": 3}, "minItems": 1, "maxItems": 2}
    assert jsonschema_lite.validate(["ab"], arr) == []
    assert jsonschema_lite.validate([], arr) == ["$: fewer than 1 items (minItems)"]
    assert jsonschema_lite.validate(["a", "b", "toolong"], arr) == [
        "$: more than 2 items (maxItems)",
        "$[2]: longer than 3 characters (maxLength)",
    ]
    bounds = {"type": "number", "exclusiveMinimum": 0, "maximum": 10}
    assert jsonschema_lite.validate(0, bounds) == ["$: not more than 0 (exclusiveMinimum)"]
    assert jsonschema_lite.validate(10**400, bounds) == ["$: more than 10 (maximum)"]  # a huge integer, compared exactly
    assert jsonschema_lite.validate("x", {"minLength": 2}) == ["$: shorter than 2 characters (minLength)"]
    # Annotations and keywords outside the subset are ignored: a client's pattern never runs on the request path.
    ignored = {"type": "string", "pattern": "^(a+)+$", "format": "email", "title": "T", "multipleOf": 7}
    assert jsonschema_lite.validate("not-an-email", ignored) == []
    assert jsonschema_lite.validate(1, False) == ["$: no value is allowed here"] and jsonschema_lite.validate(1, True) == []


@pytest.mark.req("FR-1.40")
def test_validator_tuples_and_pattern_properties():
    """`prefixItems` checks a tuple's positions and `items` the rest (Pydantic writes a tuple this way). With
    `patternProperties` the client's regular expressions are never run, so additionalProperties false cannot refuse a
    key one of them might allow: an answer is never refused for what cannot be checked."""
    pair = {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}], "minItems": 2, "maxItems": 2}
    assert jsonschema_lite.validate([1, "a"], pair) == []
    assert jsonschema_lite.validate(["a", 1], pair) == ["$[0]: expected integer", "$[1]: expected string"]
    rest = {"type": "array", "prefixItems": [{"type": "integer"}], "items": {"type": "boolean"}}
    assert jsonschema_lite.validate([1, True, False], rest) == []
    assert jsonschema_lite.validate([1, 2], rest) == ["$[1]: expected boolean"]
    closed = {"type": "array", "prefixItems": [{"type": "integer"}], "items": False}
    assert jsonschema_lite.validate([1, 2], closed) == ["$[1]: no value is allowed here"]
    assert jsonschema_lite.validate([1], {"type": "array", "items": [{"type": "string"}]}) == []  # the old list form
    patterned = {"type": "object", "patternProperties": {"^x_": {"type": "integer"}}, "additionalProperties": False}
    assert jsonschema_lite.validate({"x_a": 1, "other": 2}, patterned) == []
    assert jsonschema_lite.validate({"x": 1}, {"type": "object", "additionalProperties": False}) != []


@pytest.mark.req("FR-1.40")
def test_validator_never_raises_and_is_bounded():
    deep: Any = 1
    for _ in range(80):
        deep = [deep]
    assert jsonschema_lite.validate(deep, {"items": {"items": {}}}) == []  # items only two levels deep
    nest = {"type": "array", "items": {"$ref": "#"}}
    assert jsonschema_lite.validate(deep, nest)[0].endswith(f"nested more than {jsonschema_lite.MAX_DEPTH} levels deep")
    # anyOf of anyOfs over a long array multiplies the work: the step budget ends it quickly, as invalid. "Quickly" is
    # measured against an ordinary check, in the same rounds, not against a wall clock: coverage's tracer and a shared CI
    # runner made this case take 2.3 s twice (the old bound was 2.0 s) where it takes 0.2 s bare, and they slow the ordinary
    # check by the same factor. The ordinary check is 20,000 integers against {"type": "integer"}, a fifth of the budget's
    # 100,000 steps. The exploding case takes about 3 times as long (bare or traced; up to 6 with every core busy) and is
    # allowed 20. Without the budget it would take exponentially longer; a budget 15 times larger fails.
    explode: Any = {"type": "integer"}
    for _ in range(12):
        explode = {"anyOf": [{"type": "string"}, explode, explode]}
    exploded = ordinary = math.inf
    for _ in range(3):
        t0 = time.perf_counter()
        errors = jsonschema_lite.validate([True] * 200, {"type": "array", "items": explode})  # every branch fails
        exploded = min(exploded, time.perf_counter() - t0)
        t0 = time.perf_counter()
        fine = jsonschema_lite.validate([1] * 20_000, {"type": "array", "items": {"type": "integer"}})
        ordinary = min(ordinary, time.perf_counter() - t0)
    assert fine == [] and exploded < 20 * ordinary, f"{exploded:.2f} s against {ordinary:.2f} s for a fifth of the step budget"
    assert errors == [f"$: the schema is too large to check (more than {jsonschema_lite.MAX_STEPS} steps)"]
    for junk in ([1, 2], "x", 3, {"type": 7, "required": "x", "properties": [1], "items": 3, "enum": {}}):
        jsonschema_lite.validate({"a": [1, {"b": None}]}, junk)  # never raises


@pytest.mark.req("FR-1.40")
def test_json_equal_is_json_equality():
    assert jsonschema_lite.json_equal(1, 1.0) and not jsonschema_lite.json_equal(True, 1)
    assert not jsonschema_lite.json_equal(0, False) and not jsonschema_lite.json_equal(None, 0)
    assert jsonschema_lite.json_equal({"a": [1, {"b": None}]}, {"a": [1.0, {"b": None}]})
    assert not jsonschema_lite.json_equal({"a": [1]}, {"a": [1, 2]}) and not jsonschema_lite.json_equal([True], [1])


# ---------- derived fields ----------
@pytest.mark.req("FR-14.2", "FR-2.14")
def test_derive_takes_enums_and_ignores_free_text():
    """The spec's own example: {category: enum, priority: enum, reply: string} compares category and priority; the
    free-text reply is ignored, and required follows the schema."""
    fields = derive(TICKET_SCHEMA)
    assert fields == (
        FieldSpec("category", "label", values=("billing", "technical", "account")),
        FieldSpec("priority", "label", values=("low", "high")),
    )
    optional = {**TICKET_SCHEMA, "required": ["category"]}
    assert [(f.path, f.required) for f in derive(optional)] == [("category", True), ("priority", False)]
    for root in ({"type": "array", "items": TICKET_SCHEMA}, {"type": "string"}, {"type": "object"}, True, "x", None):
        assert derive(root) == ()  # no object with properties at the root: nothing to compare
    assert derive({"type": "object", "properties": {"reply": {"type": "string"}, "kind": {"const": "x"}}}) == ()


PYDANTIC_LIKE: dict[str, Any] = {
    "$defs": {
        "Part": {
            "properties": {
                "part": {"enum": ["screen", "battery"], "title": "Part", "type": "string"},
                "qty": {"title": "Qty", "type": "integer"},
                "note": {"type": "string"},
            },
            "required": ["part", "qty", "note"],
            "title": "Part",
            "type": "object",
            "additionalProperties": False,
        },
        "Address": {
            "type": "object",
            "properties": {"country": {"type": "string", "enum": ["DE", "IN"]}, "zip": {"type": "string"}},
            "required": ["country", "zip"],
        },
    },
    "type": "object",
    "properties": {
        "covered": {"type": "boolean"},
        "due": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        "at": {"type": "string", "format": "date-time"},
        "email": {"type": "string", "format": "email"},
        "amount": {"type": "number"},
        "parts": {"type": "array", "items": {"$ref": "#/$defs/Part"}},
        "tags": {"type": "array", "items": {"type": "string", "enum": ["urgent", "vip"]}},
        "flags": {"type": "array", "items": {"type": "boolean"}},
        "days": {"type": "array", "items": {"type": "string", "format": "date"}},
        "counts": {"type": "array", "items": {"type": "integer"}},
        "ship_to": {"anyOf": [{"$ref": "#/$defs/Address"}, {"type": "null"}]},
        "bill_to": {"$ref": "#/$defs/Address"},
        "level": {"type": ["integer", "null"], "enum": [1, 2, 3, None]},
        "kind": {"const": "claim"},
        "bad-name": {"type": "boolean"},
        "either": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
    },
    "required": ["covered", "due", "at", "email", "amount", "parts", "tags", "flags", "days", "ship_to", "level", "kind"],
    "additionalProperties": False,
}


@pytest.mark.req("FR-14.2", "FR-2.14")
def test_derive_resolves_refs_nullable_nested_records_and_formats():
    got = {f.path: f for f in derive(PYDANTIC_LIKE)}
    assert list(got) == sorted(got)  # property-name order: a pure function of the schema
    assert got["covered"] == FieldSpec("covered", "bool")
    assert got["due"] == FieldSpec("due", "date", nullable=True)  # required, may be null
    assert got["at"] == FieldSpec("at", "date", granularity="datetime")
    assert got["email"] == FieldSpec("email", "text", mode="exact")
    assert got["amount"] == FieldSpec("amount", "number", rel_tol=schema_tasks.NUMBER_REL_TOL)
    part = FieldSpec(
        "parts", "records", fields=(FieldSpec("part", "label", values=("screen", "battery")), FieldSpec("qty", "number"))
    )
    assert got["parts"] == part  # the record's free-text note is ignored too
    assert got["tags"] == FieldSpec("tags", "set", values=("urgent", "vip"))
    assert got["flags"] == FieldSpec("flags", "set", item=FieldSpec("$", "bool"))
    assert got["days"] == FieldSpec("days", "set", item=FieldSpec("$", "date"))
    # A nullable parent: its children may all be missing, so none is required.
    assert got["ship_to.country"] == FieldSpec("ship_to.country", "label", required=False, values=("DE", "IN"))
    assert got["bill_to.country"] == FieldSpec("bill_to.country", "label", required=False, values=("DE", "IN"))
    assert got["level"] == FieldSpec("level", "number", nullable=True)  # an enum of numbers: compared exactly
    # Not compared: a const, integers in a list, a name a path cannot hold, two non-null options, free text.
    for absent in ("kind", "counts", "bad-name", "either", "ship_to.zip", "parts.note"):
        assert absent not in got, absent
    # The fields accept what the schema accepts.
    task = tasks.Task("t", fields=derive(PYDANTIC_LIKE))
    answer = {
        "covered": True,
        "due": None,
        "at": "2026-10-21T10:00:00Z",
        "email": "a@b.co",
        "amount": 12.5,
        "parts": [{"part": "screen", "qty": 1, "note": "cracked"}],
        "tags": ["vip"],
        "flags": [True],
        "days": ["2026-10-21"],
        "ship_to": None,
        "bill_to": {"country": "IN", "zip": "400001"},
        "level": 2,
        "kind": "claim",
    }
    assert task.valid(json.dumps(answer))
    reordered = json.loads(json.dumps(PYDANTIC_LIKE), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
    assert derive(reordered) == derive(PYDANTIC_LIKE)


@pytest.mark.req("FR-14.2", "FR-1.40")
def test_an_enum_of_numbers_takes_what_the_schema_takes():
    """JSON equality makes 2.0 one of the enum values [1, 2, 3], so the derived field compares numbers exactly: a label
    '2' would refuse 2.0, and an answer that meets the client's contract would get no vote and never be stored."""
    schema = {
        "type": "object",
        "properties": {"p": {"type": "integer", "enum": [1, 2, 3]}, "tags": {"type": "array", "items": {"enum": [1, 2]}}},
        "required": ["p", "tags"],
    }
    assert derive(schema) == (FieldSpec("p", "number"), FieldSpec("tags", "set", item=FieldSpec("$", "number")))
    task = schema_tasks.auto_task("auto-x-1", schema)
    assert task is not None
    bound = task.bound(contract_for(_rf(schema, "x")))
    assert bound.check('{"p": 2.0, "tags": [1.0]}') is None
    assert bound.agree('{"p": 2, "tags": [1]}', '{"p": 2.0, "tags": [1.0]}')
    assert not bound.agree('{"p": 2, "tags": [1]}', '{"p": 3, "tags": [1]}')
    assert bound.check('{"p": 4, "tags": [1]}') == "schema mismatch at $.p"  # which values: the schema's check
    assert derive({"type": "object", "properties": {"s": {"enum": ["a", 1]}}})[0].type == "label"  # mixed: labels


@pytest.mark.req("FR-14.2")
def test_derive_stops_at_four_levels_and_sixty_four_fields():
    deep: dict[str, Any] = {"type": "boolean"}
    for name in ("e", "d", "c", "b", "a"):
        deep = {"type": "object", "properties": {name: deep, f"{name}_flag": {"type": "boolean"}}, "required": [name]}
    paths = [f.path for f in derive(deep)]
    assert paths == ["a.b.c.d_flag", "a.b.c_flag", "a.b_flag", "a_flag"]  # four levels; the object at the fourth is not followed
    wide = {"type": "object", "properties": {f"f{i:03}": {"type": "boolean"} for i in range(100)}}
    fields = derive(wide)
    assert len(fields) == 64 and fields[0].path == "f000" and fields[-1].path == "f063"
    records = {"type": "object", "properties": {"r": {"type": "array", "items": wide}}}
    (rec,) = derive(records)
    assert rec.type == "records" and rec.size == 64  # the records field and 63 of its sub-fields
    task = tasks.Task("big", fields=derive({"type": "object", "properties": {**wide["properties"], "z": records}}))
    assert sum(f.size for f in task.fields) <= 64


@pytest.mark.req("FR-14.2")
def test_x_ratchet_annotations_include_exclude_and_are_stripped():
    schema = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["a", "b"], "x-ratchet": {"compare": "ignore"}},
            "vendor": {"type": "string", "x-ratchet": {"compare": "text"}},
            "steps": {"type": "array", "items": {"enum": ["x", "y"]}, "x-ratchet": {"compare": "ordered"}},
            "lines": {
                "type": "array",
                "items": {"type": "object", "properties": {"sku": {"enum": ["s1", "s2"]}}},
                "x-ratchet": {"compare": "ordered"},
            },
            "total": {"type": "number", "x-ratchet": {"abs_tol": 0.01}, "x-ratchet-note": "ours"},
            "x-ratchet-id": {"type": "boolean"},  # a property named that way is a property, not an annotation
            "odd": {"type": "boolean", "x-ratchet": {"compare": "sideways", "abs_tol": -1}},  # unknown hints: ignored
        },
        "x-ratchet-version": 2,
    }
    got = {f.path: f for f in derive(schema)}
    assert "category" not in got
    assert got["vendor"] == FieldSpec("vendor", "text", required=False)
    assert got["steps"] == FieldSpec("steps", "ranking", required=False, values=("x", "y"))
    assert got["lines"].type == "records" and got["lines"].ordered
    assert got["total"] == FieldSpec("total", "number", required=False, abs_tol=0.01, rel_tol=schema_tasks.NUMBER_REL_TOL)
    assert got["odd"] == FieldSpec("odd", "bool", required=False)
    assert "x-ratchet-id" not in got  # a field path holds letters, digits and '_' only
    stripped = strip_annotations(schema)
    text = json.dumps(stripped)
    assert '"x-ratchet"' not in text and "x-ratchet-note" not in text and "x-ratchet-version" not in text
    assert "x-ratchet-id" in stripped["properties"] and stripped["properties"]["total"] == {"type": "number"}
    plain = _params()
    assert strip_annotations(TICKET_SCHEMA) is TICKET_SCHEMA and forwardable(plain) is plain and forwardable(None) is None
    sent = forwardable(_params(schema))
    assert sent is not None and sent.response_format["json_schema"]["schema"] == stripped
    assert sent.response_format["json_schema"]["name"] == "ticket"


@pytest.mark.req("FR-14.2")
def test_derived_tasks_round_trip_through_a_spec():
    """An auto task is stored as `taskspec.document` writes it and registered again from that at startup: every field
    derive makes must read back to the same task."""
    for schema in (TICKET_SCHEMA, PYDANTIC_LIKE):
        task = schema_tasks.auto_task("auto-x-12345678", schema, "x")
        assert task is not None and task.source == "auto" and not task.pinned and task.format == "json"
        assert taskspec.parse_spec(taskspec.document(task), "auto") == task
    assert schema_tasks.auto_task("auto-y", {"type": "object", "properties": {"reply": {"type": "string"}}}) is None
    # Every schema the mock world serves, and the comparator types only annotations give (ranking, ordered records).
    from sim.generic import DRIFT_SCHEMA_CASES, SCHEMA_CASES

    ordered = {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"enum": ["x", "y"]}, "x-ratchet": {"compare": "ordered"}},
            "lines": {
                "type": "array",
                "items": {"type": "object", "properties": {"sku": {"enum": ["s1", "s2"]}, "n": {"type": "integer"}}},
                "x-ratchet": {"compare": "ordered"},
            },
            "vendor": {"type": "string", "x-ratchet": {"compare": "text"}},
            "total": {"type": "number", "x-ratchet": {"abs_tol": 0.01}},
        },
    }
    for i, schema in enumerate([case.schema for case in SCHEMA_CASES + DRIFT_SCHEMA_CASES] + [ordered]):
        task = schema_tasks.auto_task(f"auto-case-{i}", schema, "case")
        assert task is not None and task.fields, i
        assert taskspec.parse_spec(taskspec.document(task), "auto") == task, i
    with pytest.raises(ValueError, match="only an operator's spec"):
        tasks.Task("auto-z", fields=derive(TICKET_SCHEMA), source="auto", pinned=True)


@pytest.mark.req("FR-14.2")
def test_auto_task_name_is_stable_across_key_order():
    spec = {"name": "Ticket Route!", "schema": TICKET_SCHEMA}
    name = auto_name(spec)
    digest = name.rsplit("-", 1)[1]
    assert name.startswith("auto-ticket-route-") and len(digest) == schema_tasks.NAME_DIGEST == 16 and tasks.valid_name(name)
    shuffled = {
        "schema": json.loads(json.dumps(TICKET_SCHEMA), object_pairs_hook=lambda p: dict(reversed(p))),
        "name": "Ticket Route!",
    }
    assert auto_name(shuffled) == name
    other = {"name": "Ticket Route!", "schema": {**TICKET_SCHEMA, "required": ["category"]}}
    assert auto_name(other) != name  # another schema, another task
    long = auto_name({"name": "x" * 64, "schema": TICKET_SCHEMA})
    assert len(long) == len("auto-") + 40 + 1 + 16 <= 64 and tasks.valid_name(long)
    assert auto_name({"schema": TICKET_SCHEMA}).startswith("auto-schema-")


# ---------- the contract ----------
@pytest.mark.req("FR-1.40")
def test_contracts_and_bound_tasks():
    assert contract_for(None) is None and contract_for({"type": "text"}) is None and contract_for("x") is None
    assert contract_for({"type": "json_object"}) is schema_tasks.JSON_OBJECT
    c = contract_for(_rf())
    assert isinstance(c, Contract) and c.kind == "json_schema" and c.name == "ticket" and not c.any_value
    assert contract_for(_rf()) is c  # cached by the schema's canonical JSON
    assert contract_for(_rf(schema={"type": "array"})).any_value
    loose = contract_for({"type": "json_schema", "json_schema": {"name": "n"}})
    assert loose is not None and loose.schema is None and loose.problem("[1]") is None and loose.problem("x") == "invalid answer"
    base = tasks.get("default")
    task = base.bound(c)
    assert task.contract is c and base.contract is None and task.spec_hash == base.spec_hash and task == base
    assert task.bound(c) is task and base.bound(None) is base
    assert task.structured and not base.structured
    assert task.check(GOOD) is None and task.check(BAD) == "schema mismatch at $.category"
    assert task.check("not json") == "invalid answer" and task.check("") == "empty answer"
    obj = base.bound(schema_tasks.JSON_OBJECT)
    assert obj.check("[1, 2]") == "invalid answer" and obj.check('{"a": 1}') is None
    # The served text is the JSON an answer holds, as the model wrote it.
    wrapped = f"<think>let me see</think>Sure! Here it is:\n```json\n{GOOD}\n```\nAnything else?"
    assert tasks.clean_answer(task, wrapped) == GOOD and tasks.clean_answer(task, GOOD) == GOOD
    assert tasks.clean_answer(base, wrapped).startswith("Sure!")  # unbound, free text keeps its fence
    arrays = base.bound(contract_for(_rf(schema={"type": "array", "items": {"type": "integer"}})))
    assert tasks.clean_answer(arrays, "The list: [1, 2, 3].") == "[1, 2, 3]" and arrays.valid("The list: [1, 2, 3].")
    with pytest.raises(ValueError, match="cannot be registered"):
        tasks.register(tasks.Task("bound-x", fields=derive(TICKET_SCHEMA), source="api").bound(c))


@pytest.mark.req("FR-1.40", "INV-6")
def test_for_row_grades_a_stored_row_under_its_stored_format():
    row = {"task": "default", "params": {"response_format": _rf()}}
    assert tasks.for_row(row).contract is contract_for(_rf())
    assert tasks.for_row({"task": "default", "params": json.dumps({"response_format": _rf()})}).contract is not None
    assert tasks.for_row({"task": "default", "params": None}) is tasks.get("default")
    assert tasks.for_row({"task": "default", "params": "{not json"}) is tasks.get("default")
    led = Ledger(":memory:")
    led.add_example("default", ASK, BAD, OPEN_TEACHER, "OpenMDW-1.1", "u", params={"response_format": _rf()})
    (r,) = led.examples()
    assert tasks.for_row(r).check(r["completion"]) == "schema mismatch at $.category"


# ---------- the router ----------
@pytest.mark.req("FR-14.2", "FR-1.40")
def test_auto_task_is_registered_persisted_and_never_pinned():
    teacher = RecordingProvider(GOOD)
    r, led = _ratchet(teacher)
    out = r.handle(ASK, params=_params())
    name = auto_name(_rf()["json_schema"])
    assert out.task == name and out.schema_valid is True and out.text == GOOD and out.route == "cold_start"
    task = tasks.lookup(name)
    assert (
        task is not None
        and task.source == "auto"
        and not task.pinned
        and [f.path for f in task.fields] == ["category", "priority"]
    )
    saved = led.saved_task(name)
    assert saved is not None and saved["source"] == "auto" and saved["updated_by"] is None
    assert saved["spec"] == taskspec.document(task) and saved["spec_hash"] == task.spec_hash
    assert _events(led, "task_registered") == [f"{name} (spec {task.spec_hash}) derived from a response_format schema"]
    (ex,) = led.examples()
    assert ex["task"] == name and json.loads(ex["params"]) == {"response_format": _rf()}
    r.handle(ASK, params=_params())  # known now: a lookup, no second registration
    assert len(_events(led, "task_registered")) == 1 and len(led.saved_tasks()) == 1
    # The teacher was sent the schema as the client gave it.
    assert teacher.calls[0].kwargs["params"].response_format == _rf()


@pytest.mark.req("FR-1.3", "FR-14.2")
def test_a_registered_task_beats_the_schema():
    teacher = RecordingProvider(GOOD)
    r, led = _ratchet(teacher)
    out = r.handle(ASK, "ticket-triage", params=_params())
    assert out.task == "ticket-triage" and out.schema_valid is True
    assert [t.name for t in tasks.registered() if t.source == "auto"] == [] and led.saved_tasks() == []
    assert teacher.calls[0].kwargs["params"].response_format == _rf()  # forwarded all the same
    assert led.examples()[0]["task"] == "ticket-triage"
    # The contract still applies on top of the task's own fields.
    teacher.text = BAD
    out = r.handle([{"role": "user", "content": "another"}], "ticket-triage", params=_params())
    assert out.schema_valid is False and len(led.examples()) == 1


@pytest.mark.req("FR-14.2", "FR-2.15")
def test_a_name_the_client_makes_up_becomes_an_auto_task_under_distill_only():
    r, led = _ratchet(RecordingProvider(GOOD))
    out = r.handle(ASK, "my-extract", params=_params())
    assert out.task == "my-extract" and tasks.lookup("my-extract").source == "auto"  # type: ignore[union-attr]
    # Registered now: a request under that name with another schema never takes the first one's fields; it gets its
    # own schema's auto task (`auto_task_collision`). The same schema keeps the name.
    other = {"type": "object", "properties": {"x": {"type": "boolean"}}, "required": ["x"]}
    mine = r.resolve("my-extract", _params(other, "other"))
    assert mine.name == auto_name(_rf(other, "other")["json_schema"]) and [f.path for f in mine.fields] == ["x"]
    assert _events(led, "auto_task_collision") == [
        "my-extract: registered from another schema; this request's schema derives other fields"
    ]
    assert r.resolve("my-extract", _params()).name == "my-extract"
    tasks.reset_builtin()
    r, led = _ratchet(RecordingProvider(GOOD), unknown_tasks="teacher")
    out = r.handle(ASK, "my-extract", params=_params())
    assert (out.task, out.route) == ("my-extract", "unknown_task") and tasks.lookup("my-extract") is None
    assert led.examples() == []
    # No name: the auto task, whatever the policy (RATCHET_MAX_AUTO_TASKS=0 turns auto tasks off).
    assert r.handle(ASK, params=_params()).route == "cold_start" and led.examples() != []


CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": ["billing", "technical"]}, "refund_usd": {"type": "number"}},
    "required": ["category", "refund_usd"],
    "additionalProperties": False,
}
WEAK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": ["billing", "technical"]}},
    "required": ["category"],
}
REFUND_10 = json.dumps({"category": "billing", "refund_usd": 10})
REFUND_99999 = json.dumps({"category": "billing", "refund_usd": 99999})


@pytest.mark.req("FR-14.2")
def test_a_client_cannot_choose_the_fields_another_clients_answers_are_compared_on():
    """Anyone can compute an auto task's name from a schema. A client that names another client's auto task before it
    exists, with a weaker schema, must not decide what that client's answers are compared on: a client's own `auto-`
    name never names a new task, and an auto task serves only a schema that derives its rules."""
    victim = GenParams(response_format=_rf(CLAIM_SCHEMA, "claim"))
    name = auto_name(_rf(CLAIM_SCHEMA, "claim")["json_schema"])
    r, led = _ratchet(RecordingProvider(REFUND_10))
    squat = r.task_for(name, "client-a", _params(WEAK_SCHEMA, "x"))
    assert squat.name == auto_name(_rf(WEAK_SCHEMA, "x")["json_schema"]) and tasks.lookup(name) is None
    honest = r.task_for(None, "client-b", victim)
    assert honest.name == name and [f.path for f in honest.fields] == ["category", "refund_usd"]
    assert not honest.agree(REFUND_10, REFUND_99999)  # the refund is compared
    # Named once it exists, by a schema that derives other fields: that request gets its own schema's task.
    again = r.task_for(name, "client-a", _params(WEAK_SCHEMA, "x"))
    assert again.name == squat.name and [f.path for f in again.fields] == ["category"]
    assert _events(led, "auto_task_collision") == [
        f"{name}: registered from another schema; this request's schema derives other fields"
    ]
    assert r.task_for(name, "client-c", victim).name == name  # the same schema under that name: the task itself
    # A name a client makes up is held by the first schema's fields, for that schema only.
    r.handle(ASK, "claims", params=_params(WEAK_SCHEMA, "x"))
    later = r.task_for("claims", "client-b", victim)
    assert later.name == name and not later.agree(REFUND_10, REFUND_99999)


@pytest.mark.req("FR-14.2", "INV-16")
def test_an_auto_name_held_by_other_fields_stores_nothing():
    """Two schemas with one auto name (a digest collision, or an auto task derived differently by another release): the
    request is never compared on the registered task's fields and nothing is stored under the name;
    RATCHET_UNKNOWN_TASKS decides about it, as for any unregistered task."""
    victim = GenParams(response_format=_rf(CLAIM_SCHEMA, "claim"))
    name = auto_name(_rf(CLAIM_SCHEMA, "claim")["json_schema"])
    tasks.register(tasks.Task(name, fields=derive(WEAK_SCHEMA), source="auto"))
    r, led = _ratchet(RecordingProvider(REFUND_10))
    out = r.handle(ASK, params=victim)
    assert (out.task, out.schema_valid) == (name, True) and led.examples() == [] and _requests(led)[0]["stored"] == 0
    task = r.resolve(None, victim)
    assert task.source == "adhoc" and not task.store and task.fields == ()
    assert _events(led, "auto_task_collision") == [
        f"{name}: registered from another schema; this request's schema derives other fields"
    ]
    r, _ = _ratchet(RecordingProvider(REFUND_10), unknown_tasks="reject")
    with pytest.raises(tasks.UnknownTask, match=name):
        r.task_for(None, None, victim)


@pytest.mark.req("FR-14.2", "FR-2.15")
def test_a_schema_without_comparable_fields_follows_the_unknown_task_policy():
    free = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    params = _params(free, "summary")
    name = auto_name(_rf(free, "summary")["json_schema"])
    teacher = RecordingProvider('{"summary": "fine"}')
    r, led = _ratchet(teacher)
    out = r.handle(ASK, params=params)
    assert out.task == name and tasks.lookup(name) is None and not tasks.get(name).verifiable
    assert out.route == "cold_start" and len(led.examples()) == 1  # distill: whole answers, compared as canonical JSON
    r, led = _ratchet(teacher, unknown_tasks="teacher")
    assert r.handle(ASK, params=params).route == "unknown_task" and led.examples() == []
    r, _ = _ratchet(teacher, unknown_tasks="reject")
    with pytest.raises(tasks.UnknownTask, match=name):
        r.task_for(None, None, params)


@pytest.mark.req("FR-14.2")
def test_auto_tasks_stop_at_the_cap_and_say_so_once():
    def schema(i: int) -> dict[str, Any]:
        return {"type": "object", "properties": {f"flag{i}": {"type": "boolean"}}, "required": [f"flag{i}"]}

    r, led = _ratchet(RecordingProvider('{"flag0": true, "flag1": true, "flag2": true}'), max_auto_tasks=1)
    first = r.handle(ASK, params=_params(schema(0), "s0"))
    assert tasks.lookup(first.task) is not None
    for i in (1, 2, 1):
        out = r.handle(ASK, params=_params(schema(i), f"s{i}"))
        assert tasks.lookup(out.task) is None and tasks.get(out.task).source == "adhoc"
    assert len(_events(led, "auto_task_cap")) == 1 and "RATCHET_MAX_AUTO_TASKS (1)" in _events(led, "auto_task_cap")[0]
    r2, _ = _ratchet(RecordingProvider("{}"), led=led, max_auto_tasks=1)  # a second process on the same ledger
    r2.handle(ASK, params=_params(schema(3), "s3"))
    assert len(_events(led, "auto_task_cap")) == 1
    # 0 turns auto tasks off: a request keeps the task it names, or `default`, held to its schema, as before auto tasks.
    tasks.reset_builtin()
    off, led = _ratchet(RecordingProvider(GOOD), max_auto_tasks=0)
    out = off.handle(ASK, params=_params(name="off"))
    assert (out.task, out.schema_valid) == ("default", True) and led.saved_tasks() == [] and len(led.examples()) == 1
    assert off.handle(ASK, "my-extract", params=_params(name="off")).task == "my-extract"
    for policy in ("teacher", "reject"):
        off, _ = _ratchet(RecordingProvider(GOOD), max_auto_tasks=0, unknown_tasks=policy)
        assert off.task_for(None, None, _params(name="off")).name == "default"  # never refused for having a schema
    with pytest.raises(ValueError, match="RATCHET_MAX_AUTO_TASKS"):
        _ratchet(RecordingProvider(GOOD), max_auto_tasks=-1)
    with pytest.raises(ValueError, match="RATCHET_MAX_AUTO_TASKS"):
        _ratchet(RecordingProvider(GOOD), max_auto_tasks=tasks.MAX_TASKS + 1)


@pytest.mark.req("FR-14.2", "INV-12")
def test_an_auto_task_whose_schema_holds_personal_data_is_not_stored():
    """The `tasks` table stores rules, never personal data: an enum value the redactor would replace keeps the schema
    from becoming an auto task (its requests follow the unknown-task policy)."""
    pii = {"type": "object", "properties": {"to": {"enum": ["alice@example.com", "bob@example.com"]}}, "required": ["to"]}
    r, led = _ratchet(RecordingProvider('{"to": "alice@example.com"}'))
    out = r.handle(ASK, params=_params(pii, "mail"))
    assert tasks.lookup(out.task) is None and led.saved_tasks() == []
    assert _events(led, "auto_task_skipped") == [f"{out.task}: auto task not stored: its schema holds personal data"]


NO_REPLY = json.dumps({"category": "billing", "priority": "high"})  # the fields are fine; the schema requires `reply`


@pytest.mark.req("FR-14.2", "INV-6", "INV-16")
def test_auto_tasks_come_back_at_startup_and_night_school_grades_under_the_schema(tmp_path):
    """Persisted, so Night School finds them: a new registry built from the ledger (`taskspec.install`, as `python -m
    ratchet.live` and the gateway do) has the auto task. The dataset and the golden eval hold each row to its stored
    schema (`tasks.for_row`), as the router held the request: an answer whose fields are fine but which breaks the
    schema is no training target and no vote (INV-6, INV-16)."""
    led = Ledger(str(tmp_path / "r.db"))
    r, led = _ratchet(RecordingProvider(GOOD), led=led, redact_pii=False)
    for i in range(40):
        r.handle([{"role": "user", "content": f"ticket {i}"}], params=_params())
    name = auto_name(_rf()["json_schema"])
    derived = tasks.lookup(name)
    tasks.reset_builtin()
    assert tasks.lookup(name) is None
    assert taskspec.install(Ledger(str(tmp_path / "r.db"))) == []
    assert tasks.lookup(name) == derived and derived is not None
    golden = dataset.rows(led, "golden")
    rows = dataset.rows(led, "train") + golden
    assert len(rows) == 40 and all(row["task"] == name and row["params"] == {"response_format": _rf()} for row in rows)
    assert derived.valid(NO_REPLY) and not tasks.for_row(rows[0]).valid(NO_REPLY)

    def sampler(text: str) -> Any:
        def sample(prompt: list[dict[str, Any]], n: int, params: dict[str, Any] | None) -> list[Completion]:
            assert params == {"response_format": _rf()}  # the student is sampled under the stored format
            return [Completion(text, "s", token_logprobs=[-0.01]) for _ in range(n)]

        return sample

    good = cascade_eval(sampler(GOOD), rows, samples=3, min_task_n=1)
    assert good["golden_agreement"] == 1.0 and set(good["tasks"]) == {name}
    assert good["tasks"][name]["spec_hash"] == derived.spec_hash
    broken = cascade_eval(sampler(NO_REPLY), rows, samples=3, min_task_n=1)
    assert broken["golden_agreement"] == 0.0 and broken["coverage"] == 0.0  # no vote, though the fields agree
    # A stored row whose answer breaks its schema (stored before a schema check, or by hand) is never a target.
    for i in range(30):
        led.add_example(
            name,
            [{"role": "user", "content": f"old {i}"}],
            NO_REPLY,
            OPEN_TEACHER,
            "OpenMDW-1.1",
            "u",
            params={"response_format": _rf()},
        )
    counts: dict[str, int] = {}
    train = dataset.rows(led, "train", counts=counts)
    old_train = [e for e in led.examples(split="train") if e["completion"] == NO_REPLY]
    assert old_train and counts["skipped_invalid"] == len(old_train)
    assert all(row["messages"][-1]["content"] != NO_REPLY for row in train)


@pytest.mark.req("FR-14.2")
def test_concurrent_requests_register_one_auto_task():
    r, led = _ratchet(RecordingProvider(GOOD))
    barrier = threading.Barrier(8)
    names: list[str] = []

    def go(i: int) -> None:
        barrier.wait()
        names.append(r.handle([{"role": "user", "content": f"thread {i}"}], params=_params()).task)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(names)) == 1 and len(led.saved_tasks()) == 1 and len(_events(led, "task_registered")) == 1


@pytest.mark.req("FR-1.40", "INV-16")
def test_invalid_student_samples_get_no_vote_and_escalate():
    student = RecordingProvider(BAD, logprob=-0.01)  # unanimous and confident, but outside the schema's enum
    teacher = RecordingProvider(GOOD)
    r, led = _ratchet(teacher, student, live={"threshold": 0.5})
    out = r.handle(ASK, params=_params())
    assert (out.served_by, out.route, out.confidence) == ("teacher", "confidence", 0.0)
    assert out.text == GOOD and out.schema_valid is True
    # The same samples with a schema they match are served.
    student.text = GOOD
    out = r.handle([{"role": "user", "content": "x"}], params=_params())
    assert out.served_by == "student" and out.schema_valid is True and out.confidence > 0.5
    # json_object: a sample that is not one JSON object gets no vote either.
    student.text = "billing, high"
    out = r.handle([{"role": "user", "content": "y"}], params=GenParams(response_format={"type": "json_object"}))
    assert (out.task, out.route, out.confidence) == ("default", "confidence", 0.0)


@pytest.mark.req("FR-1.40")
def test_json_object_answers_are_served_as_the_json_they_hold():
    """json_object without a schema: the served content is the JSON object the answer holds, as written (reasoning, a
    sentence and a fence around it dropped), and whole answers are compared as canonical JSON (the default task)."""
    teacher = RecordingProvider('<think>easy</think>Here you go: {"a": 1,  "b": [2]} Anything else?')
    r, led = _ratchet(teacher)
    out = r.handle(ASK, params=GenParams(response_format={"type": "json_object"}))
    assert out.text == '{"a": 1,  "b": [2]}' and out.schema_valid is True and out.task == "default"
    assert led.examples()[0]["completion"] == out.text  # stored as served: the JSON, never re-serialised
    teacher.text = "no JSON here"
    out = r.handle([{"role": "user", "content": "x"}], params=GenParams(response_format={"type": "json_object"}))
    assert out.text == "no JSON here" and out.schema_valid is False  # repaired once, still invalid: served as written
    assert len(led.examples()) == 1


@pytest.mark.req("FR-1.40")
def test_an_answer_is_checked_against_its_schema_once_per_request(monkeypatch):
    """The router asks about one answer several times (its vote, its agreement, the served choice); the schema check
    behind it runs once per distinct answer (`Contract.problem`'s memo), so a large schema costs one check each."""
    calls: list[Any] = []
    real = jsonschema_lite.check

    def counting(instance: Any, schema: Any, **kwargs: Any) -> list[tuple[str, str]]:
        calls.append(instance)
        return real(instance, schema, **kwargs)

    monkeypatch.setattr(jsonschema_lite, "check", counting)
    student = RecordingProvider(GOOD, logprob=-0.01)
    r, _ = _ratchet(RecordingProvider(GOOD), student, live={"threshold": 0.5}, audit_rate=1.0)
    out = r.handle([{"role": "user", "content": "memo"}], params=_params(name="memo"))
    assert out.served_by == "student" and out.schema_valid is True
    assert len(calls) == 1  # three samples, the audit and the served choice: one text, one check


@pytest.mark.req("FR-1.40", "INV-12")
def test_schema_mismatches_share_one_skip_counter():
    """A schema path comes from a client: the throttle counts every schema mismatch as one reason, so its counter does
    not grow with the schemas clients send; each event still names the path, never the answer."""
    r, led = _ratchet(RecordingProvider(BAD), schema_repair=False)
    r.handle(ASK, params=_params())
    other = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    for i in range(9):
        r.handle([{"role": "user", "content": f"n{i}"}], params=_params(other, "other"))
    assert set(r._skipped) == {"schema mismatch"} and r._skipped["schema mismatch"] == 10
    assert _events(led, "skip_example") == [  # the 1st and the 10th skip, each naming its own path
        f"{auto_name(_rf()['json_schema'])}: schema mismatch at $.category",
        f"{auto_name(_rf(other, 'other')['json_schema'])}: schema mismatch at $.ok",
    ]
    assert all("refund" not in e["detail"] for e in led.events())


def _repairing_teacher(first: str, then: str) -> RecordingProvider:
    """A teacher that writes `first`, and `then` when asked again after a repair note."""

    def answer(messages: list[dict[str, Any]], model: str) -> str:
        return then if "did not match the schema" in str(messages[-1].get("content")) else first

    return RecordingProvider(answer, in_tokens=40, out_tokens=7)


@pytest.mark.req("FR-1.40", "INV-2", "INV-16")
def test_a_repaired_teacher_answer_is_served_and_stored_with_the_original_messages():
    teacher = _repairing_teacher(f"<think>hmm</think>{BAD}", f"```json\n{GOOD}\n```")
    r, led = _ratchet(teacher, redact_pii=False)
    out = r.handle(ASK, params=_params())
    assert out.text == GOOD and out.schema_valid is True and out.served_by == "teacher"
    first, again = teacher.calls
    assert first.messages == ASK
    assert again.messages[: len(ASK)] == ASK
    assert again.messages[len(ASK)] == {"role": "assistant", "content": BAD}  # its own answer, without the reasoning
    note = again.messages[-1]  # a user turn: many chat templates refuse a system message after the first turn
    assert note["role"] == "user" and note["content"] == (
        "Your previous answer did not match the schema: $.category: not one of the values in 'enum'. Reply with JSON only."
    )
    assert again.kwargs["params"] == first.kwargs["params"]  # the same parameters, schema and all
    (ex,) = led.examples()  # one example: the request's own messages and the repaired, valid teacher answer (INV-2)
    assert json.loads(ex["messages"]) == ASK and ex["completion"] == GOOD and ex["teacher_model"] == OPEN_TEACHER
    assert _requests(led)[0]["stored"] == 1
    # Both calls are paid for: the repair's first answer is overhead, in cost_usd and the breakdown, not the baseline.
    price = r.s.price(OPEN_TEACHER)
    assert out.usage_breakdown["repair"] == {"in": 40, "out": 7} and set(out.usage_breakdown) == set(USAGE_PARTS)
    assert out.cost_usd == pytest.approx(2 * price.cost(40, 7)) and out.baseline_usd == pytest.approx(price.cost(40, 7))
    assert out.teacher_cost_usd == pytest.approx(out.cost_usd)


@pytest.mark.req("FR-1.40", "INV-16")
def test_a_teacher_answer_invalid_twice_is_served_but_never_stored():
    teacher = _repairing_teacher(BAD, BAD)
    r, led = _ratchet(teacher)
    out = r.handle(ASK, params=_params())
    assert out.text == BAD and out.schema_valid is False and len(teacher.calls) == 2
    assert led.examples() == [] and _requests(led)[0]["stored"] == 0
    assert _events(led, "skip_example") == [f"{out.task}: schema mismatch at $.category"]
    # A cut-off answer is not repaired (a second call would be cut off too), and n > 1 choices are served as written.
    cut = RecordingProvider(BAD, finish_reason="length")
    r, led = _ratchet(cut)
    assert r.handle(ASK, params=_params()).schema_valid is False and len(cut.calls) == 1
    many = RecordingProvider(BAD)
    r, _ = _ratchet(many)
    out = r.handle(ASK, params=_params(), n=2)
    assert len(many.calls) == 1 and len(out.choices) == 2 and out.schema_valid is False


@pytest.mark.req("FR-1.40", "INV-15")
def test_repair_can_be_switched_off_and_a_failed_repair_serves_the_first_answer():
    teacher = _repairing_teacher(BAD, GOOD)
    r, led = _ratchet(teacher, schema_repair=False)
    assert r.handle(ASK, params=_params()).schema_valid is False and len(teacher.calls) == 1
    flaky = _repairing_teacher(BAD, GOOD)
    original = flaky.complete

    def complete(messages: Any, model: str, *args: Any, **kwargs: Any) -> list[Completion]:
        if "did not match" in str(messages[-1].get("content")):
            raise httpx.ConnectError("https://teacher.example/v1?token=SECRET down")
        return original(messages, model, *args, **kwargs)

    flaky.complete = complete  # type: ignore[method-assign]
    r, led = _ratchet(flaky)
    out = r.handle(ASK, params=_params())
    assert out.text == BAD and out.schema_valid is False and r.health()["repair_errors"] == 1
    assert _events(led, "schema_repair_error") == [f"{out.task}: ConnectError"]
    assert all("SECRET" not in e["detail"] for e in led.events())


@pytest.mark.req("FR-1.40")
def test_an_audit_answer_is_repaired_too():
    student = RecordingProvider(GOOD, logprob=-0.01)
    teacher = _repairing_teacher(BAD, GOOD)
    r, led = _ratchet(teacher, student, live={"threshold": 0.5}, audit_rate=1.0)
    out = r.handle(ASK, params=_params())
    assert out.served_by == "student" and len(teacher.calls) == 2
    row = _requests(led)[0]
    assert (row["audited"], row["audit_agree"], row["stored"]) == (1, 1, 1)
    assert out.usage_breakdown["repair"] == {"in": 40, "out": 7} and out.usage_breakdown["audit"] == {"in": 40, "out": 7}


@pytest.mark.req("FR-1.40", "INV-10", "FR-15.1")
def test_arbitration_and_pinned_answers_are_repaired_and_priced():
    """The two other teacher paths: an arbitration (the shadow disagrees with the served answer) is repaired like an
    audit, and its replaced answer is priced and listed; a pinned task's answer is repaired and priced too, and still
    never stored (INV-10)."""
    student = RecordingProvider(lambda messages, model: OTHER if model == "student-v2" else GOOD, logprob=-0.01)
    teacher = _repairing_teacher(BAD, GOOD)
    r, led = _ratchet(teacher, student, live={"threshold": 0.5})
    _version(led, "shadow", {"threshold": 0.5})
    out = r.handle(ASK, params=_params())
    assert out.served_by == "student" and len(teacher.calls) == 2  # the arbitration and its repair
    row = _requests(led)[0]
    assert (row["shadow_serve"], row["shadow_agree"], row["stored"]) == (1, 0, 1)  # the repaired ruling settles it
    assert out.usage_breakdown["arbitration"] == out.usage_breakdown["repair"] == {"in": 40, "out": 7}
    price = r.s.price(OPEN_TEACHER).cost(40, 7)
    assert out.teacher_cost_usd == pytest.approx(2 * price)
    assert led.examples()[0]["completion"] == GOOD

    tasks.register(tasks.Task("pinned-extract", fields=derive(TICKET_SCHEMA), source="api", pinned=True))
    pinned_teacher = _repairing_teacher(BAD, GOOD)
    r, led = _ratchet(pinned_teacher)
    out = r.handle(ASK, "pinned-extract", params=_params())
    assert (out.route, out.text, out.schema_valid, out.task) == ("pinned", GOOD, True, "pinned-extract")
    assert len(pinned_teacher.calls) == 2 and led.examples() == []
    assert out.cost_usd == pytest.approx(2 * price) and out.baseline_usd == pytest.approx(price)
    assert out.usage_breakdown["repair"] == {"in": 40, "out": 7}
    assert _requests(led)[0]["cost_usd"] == pytest.approx(out.cost_usd)


class _PromptTeacher:
    """A teacher whose prompt tokens grow with what it is sent (about 4 characters a token, half of them cached) and
    that takes 1000 ms for a first answer and 500 ms for a repair, as a real endpoint would: a repair's prompt also
    holds the rejected answer and the note. `then_finish` is why the repair answer stopped."""

    def __init__(self, first: str, then: str, then_finish: str = "stop") -> None:
        self.first, self.then, self.then_finish = first, then, then_finish
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]], model: str, n: int = 1, **_: Any) -> list[Completion]:
        self.calls.append(messages)
        repair = "did not match the schema" in str(messages[-1].get("content"))
        tokens = len(json.dumps(messages)) // 4
        c = Completion(
            self.then if repair else self.first,
            model,
            in_tokens=tokens,
            out_tokens=7,
            latency_ms=500.0 if repair else 1000.0,
            finish_reason=self.then_finish if repair else "stop",
            cached_tokens=tokens // 2,
        )
        return [c] * n


LONG_BAD = json.dumps({"category": "refund", "priority": "high", "reply": "x" * 2000})


@pytest.mark.req("FR-1.40", "FR-15.1", "FR-1.12")
def test_after_a_repair_the_baseline_usage_and_latency_count_the_request_itself():
    """The repair call's prompt holds the request, the rejected answer and the note. Both calls are paid for in
    `cost_usd` and listed in the breakdown as made, but what the teacher alone would have cost (the baseline, so the
    savings) and the client's `usage.prompt_tokens` count the request's own prompt, and `latency_ms` both calls."""
    price = _settings().price(OPEN_TEACHER)
    teacher = _PromptTeacher(LONG_BAD, GOOD)
    r, led = _ratchet(teacher, redact_pii=False)
    out = r.handle(ASK, params=_params())
    own, repair_in = (len(json.dumps(m)) // 4 for m in teacher.calls)
    assert repair_in > 10 * own and out.text == GOOD
    assert (out.prompt_tokens, out.cached_tokens) == (own, own // 2)
    assert out.baseline_usd == pytest.approx(price.cost(own, 7))
    assert out.cost_usd == pytest.approx(price.cost(own, 7) + price.cost(repair_in, 7))
    assert out.latency_ms == pytest.approx(1500.0)
    assert out.usage_breakdown["served"] == {"in": repair_in, "out": 7}
    assert out.usage_breakdown["repair"] == {"in": own, "out": 7}
    row = _requests(led)[0]
    assert row["baseline_cost_usd"] == pytest.approx(out.baseline_usd) and row["latency_ms"] == pytest.approx(1500.0)
    # The pinned path counts the same way.
    tasks.register(tasks.Task("pinned-extract", fields=derive(TICKET_SCHEMA), source="api", pinned=True))
    r, led = _ratchet(_PromptTeacher(LONG_BAD, GOOD))
    out = r.handle(ASK, "pinned-extract", params=_params())
    assert (out.route, out.prompt_tokens, out.latency_ms) == ("pinned", own, pytest.approx(1500.0))
    assert out.baseline_usd == pytest.approx(price.cost(own, 7))
    assert out.cost_usd == pytest.approx(price.cost(own, 7) + price.cost(repair_in, 7))
    assert _requests(led)[0]["latency_ms"] == pytest.approx(1500.0)
    # An audit's baseline too; the student served, so the latency is its own.
    student = RecordingProvider(GOOD, logprob=-0.01, latency_ms=20.0)
    r, led = _ratchet(_PromptTeacher(LONG_BAD, GOOD), student, live={"threshold": 0.5}, audit_rate=1.0)
    out = r.handle(ASK, params=_params())
    assert out.served_by == "student" and out.latency_ms == pytest.approx(20.0)
    assert out.baseline_usd == pytest.approx(price.cost(own, 7))
    assert _requests(led)[0]["baseline_cost_usd"] == pytest.approx(price.cost(own, 7))


@pytest.mark.req("FR-1.40", "INV-16")
def test_a_cut_off_or_empty_repair_answer_keeps_the_first_answer():
    """A complete answer that breaks the schema is better than a repair answer that was cut off or is empty: the first is
    served, with its own finish_reason, and neither is stored. The repair call is still paid for."""
    price = _settings().price(OPEN_TEACHER)
    for then, finish in ((GOOD[:20], "length"), ("", "stop"), ("<think>only reasoning</think>", "stop")):
        teacher = _PromptTeacher(BAD, then, then_finish=finish)
        r, led = _ratchet(teacher)
        out = r.handle(ASK, params=_params())
        assert (out.text, out.finish_reason, out.schema_valid) == (BAD, "stop", False), then
        assert len(teacher.calls) == 2 and led.examples() == []
        own, repair_in = (len(json.dumps(m)) // 4 for m in teacher.calls)
        assert out.usage_breakdown["served"] == {"in": own, "out": 7}
        assert out.usage_breakdown["repair"] == {"in": repair_in, "out": 7} and out.prompt_tokens == own
        assert out.cost_usd == pytest.approx(price.cost(own, 7) + price.cost(repair_in, 7))
        assert out.baseline_usd == pytest.approx(price.cost(own, 7)) and out.latency_ms == pytest.approx(1500.0)


def _refusing_teacher() -> tuple[OpenAICompatProvider, list[dict[str, Any]]]:
    """A Token Factory that refuses response_format json_schema with a 400, and answers json_object."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            error = {"message": "response_format.type json_schema is not supported", "param": "response_format"}
            return httpx.Response(400, json={"error": {**error, "type": "invalid_request_error"}})
        message = {"role": "assistant", "content": GOOD}
        usage = {"prompt_tokens": 30, "completion_tokens": 9}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}], "usage": usage})

    return OpenAICompatProvider("https://teacher.test/v1", "tf-key", transport=httpx.MockTransport(handler)), bodies


@pytest.mark.req("FR-1.40", "FR-1.11")
def test_a_teacher_that_refuses_json_schema_gets_json_object_and_the_schema():
    teacher, bodies = _refusing_teacher()
    r, led = _ratchet(teacher)
    out = r.handle(ASK, params=_params())
    assert out.text == GOOD and out.schema_valid is True and len(bodies) == 2
    first, second = bodies
    assert first["response_format"] == _rf() and second["response_format"] == {"type": "json_object"}
    assert second["messages"][0]["role"] == "system" and second["messages"][0]["content"].startswith("Route the ticket.\n\n")
    assert json.dumps(TICKET_SCHEMA, ensure_ascii=False) in second["messages"][0]["content"]
    assert second["messages"][1:] == ASK[1:]
    assert _events(led, "json_schema_fallback") == [
        "teacher: schema refused (UpstreamError 400); that schema is sent as json_object with the schema in the system message"
    ]
    (ex,) = led.examples()  # the client's format and messages are what the example keeps
    assert json.loads(ex["messages"]) == ASK and json.loads(ex["params"])["response_format"] == _rf()
    # Remembered: the same schema goes straight to json_object, and no second event is written.
    r.handle([{"role": "user", "content": "again"}], params=_params())
    assert len(bodies) == 3 and bodies[2]["response_format"] == {"type": "json_object"}
    assert bodies[2]["messages"][0]["content"].startswith("Reply with JSON only.")  # no system message: one is added
    assert len(_events(led, "json_schema_fallback")) == 1
    assert getattr(teacher, "supports_json_schema", True) is True  # one refused schema may be the client's own
    # Another 400 (a prompt too long) is the request's own problem: no retry.
    calls = []

    def other(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "prompt too long", "code": "context_length_exceeded"}})

    plain = OpenAICompatProvider("https://t2.test/v1", "k", transport=httpx.MockTransport(other))
    r, _ = _ratchet(plain)
    with pytest.raises(httpx.HTTPError):
        r.handle(ASK, params=_params())
    assert len(calls) == 1


def _schema_refusing_teacher(refuse_all: bool = False, probe_status: int = 200) -> tuple[OpenAICompatProvider, list[Any]]:
    """A Token Factory whose grammar engine refuses the json_schema of every schema named `s<i>` (or every json_schema,
    with `refuse_all`) with a 400 about response_format, and answers the router's probe with `probe_status`."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        rf = body.get("response_format", {})
        if rf.get("type") == "json_schema":
            name = rf["json_schema"]["name"]
            if name == "probe" and probe_status != 200 and not refuse_all:
                return httpx.Response(probe_status, json={"error": {"message": "overloaded"}})
            if refuse_all or name.startswith("s"):
                error = {"message": "Invalid JSON schema in response_format: unsupported keyword", "param": "response_format"}
                return httpx.Response(400, json={"error": error})
        message = {"role": "assistant", "content": GOOD}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})

    return OpenAICompatProvider("https://teacher.test/v1", "tf-key", transport=httpx.MockTransport(handler)), bodies


def _variant(i: int) -> dict[str, Any]:
    return {**TICKET_SCHEMA, "description": f"variant {i}"}


def _types(bodies: list[dict[str, Any]]) -> list[str]:
    return [b["response_format"]["type"] for b in bodies]


@pytest.mark.req("FR-1.40", "FR-1.11")
def test_refused_client_schemas_never_turn_json_schema_off_for_the_others():
    """A grammar engine may refuse a client's schema (a keyword it cannot compile) and take every other: only that schema
    goes as json_object. After JSON_SCHEMA_STRIKES refused schemas the endpoint is asked once with the smallest strict
    schema (`schema_tasks.PROBE_FORMAT`); it answers, so json_schema stays on for every other schema, and one client
    sending schemas the endpoint refuses cannot take grammar-constrained decoding away from the rest."""
    from ratchet.router import JSON_SCHEMA_STRIKES

    teacher, bodies = _schema_refusing_teacher()
    r, led = _ratchet(teacher)
    r.handle(ASK, params=_params(_variant(0), "s0"))  # refused, retried as json_object
    r.handle([{"role": "user", "content": "b"}], params=_params(_variant(9), "ok"))  # another schema: still json_schema
    assert _types(bodies) == ["json_schema", "json_object", "json_schema"]
    bodies.clear()
    for i in range(1, JSON_SCHEMA_STRIKES):
        r.handle([{"role": "user", "content": f"c{i}"}], params=_params(_variant(i), f"s{i}"))
    probes = [b for b in bodies if b["response_format"] == schema_tasks.PROBE_FORMAT]
    assert len(probes) == 1 and probes[0]["messages"] == schema_tasks.PROBE_MESSAGES
    assert probes[0]["max_tokens"] == schema_tasks.PROBE_MAX_TOKENS
    assert getattr(teacher, "supports_json_schema", True) is True  # the endpoint took the probe: json_schema stays on
    bodies.clear()
    r.handle([{"role": "user", "content": "d"}], params=_params(_variant(9), "ok"))
    r.handle([{"role": "user", "content": "e"}], params=_params(_variant(0), "s0"))  # remembered: straight to json_object
    r.handle([{"role": "user", "content": "f"}], params=_params(_variant(7), "s7"))  # one more refused: no second probe
    assert _types(bodies) == ["json_schema", "json_object", "json_schema", "json_object"]
    assert _events(led, "json_schema_fallback") == [
        "teacher: schema refused (UpstreamError 400); that schema is sent as json_object with the schema in the system message"
    ]
    # A probe that fails for another reason decides nothing: the endpoint is asked again after the next refused schema.
    flaky, bodies = _schema_refusing_teacher(probe_status=503)
    r, _ = _ratchet(flaky)
    for i in range(JSON_SCHEMA_STRIKES + 1):
        r.handle([{"role": "user", "content": f"g{i}"}], params=_params(_variant(i), f"s{i}"))
    assert sum(b["response_format"] == schema_tasks.PROBE_FORMAT for b in bodies) == 2
    assert getattr(flaky, "supports_json_schema", True) is True


@pytest.mark.req("FR-1.40", "FR-1.11")
def test_json_schema_is_turned_off_for_an_endpoint_that_refuses_the_probe_too():
    """An endpoint that takes no json_schema at all refuses the probe as well: then every schema goes as json_object,
    the provider says so (`supports_json_schema`), and each step writes one event per ledger."""
    from ratchet.router import JSON_SCHEMA_STRIKES

    teacher, bodies = _schema_refusing_teacher(refuse_all=True)
    r, led = _ratchet(teacher)
    for i in range(JSON_SCHEMA_STRIKES):
        r.handle([{"role": "user", "content": f"c{i}"}], params=_params(_variant(i), f"s{i}"))
    assert teacher.supports_json_schema is False  # type: ignore[attr-defined]
    bodies.clear()
    out = r.handle([{"role": "user", "content": "d"}], params=_params(_variant(9), "ok"))  # off now: straight to json_object
    assert _types(bodies) == ["json_object"] and out.schema_valid is True
    assert _events(led, "json_schema_fallback") == [
        "teacher: schema refused (UpstreamError 400); that schema is sent as json_object with the schema in the system message",
        f"teacher: json_schema off: {JSON_SCHEMA_STRIKES} schemas and the probe refused (UpstreamError 400); every schema "
        "is sent as json_object with the schema in the system message",
    ]
    # A second process on the same ledger writes neither event again.
    teacher2, _ = _schema_refusing_teacher(refuse_all=True)
    r2, _ = _ratchet(teacher2, led=led)
    for i in range(JSON_SCHEMA_STRIKES):
        r2.handle([{"role": "user", "content": f"e{i}"}], params=_params(_variant(i), f"s{i}"))
    assert teacher2.supports_json_schema is False  # type: ignore[attr-defined]
    assert len(_events(led, "json_schema_fallback")) == 2
    # The fallback teacher's endpoint is its own: a refusal by the primary never changes what it is sent.
    assert r2._takes_json_schema(RecordingProvider(GOOD), _params()) is True


@pytest.mark.req("FR-14.2", "FR-1.13")
def test_annotations_never_reach_a_model_or_the_ledger():
    annotated = json.loads(json.dumps(TICKET_SCHEMA))
    annotated["properties"]["reply"]["x-ratchet"] = {"compare": "text"}
    annotated["properties"]["x-ratchet-ref"] = {"type": "string"}
    teacher = FakeOpenAIServer(json.dumps({"category": "billing", "priority": "low", "reply": "ok", "x-ratchet-ref": "r1"}))
    provider = OpenAICompatProvider("https://teacher.test/v1", "k", transport=teacher.transport)
    r, led = _ratchet(provider)
    out = r.handle(ASK, params=_params(annotated, "annotated"))
    (call,) = teacher.calls_to("/chat/completions")
    sent = call.json["response_format"]["json_schema"]["schema"]
    assert '"x-ratchet"' not in json.dumps(sent) and "x-ratchet-ref" in sent["properties"]
    assert sent == strip_annotations(annotated)
    (ex,) = led.examples()
    assert json.loads(ex["params"])["response_format"]["json_schema"]["schema"] == sent
    task = tasks.lookup(out.task)
    assert task is not None and FieldSpec("reply", "text") in task.fields  # the annotation decided the fields
    assert out.task == auto_name(_rf(annotated, "annotated")["json_schema"])  # named after the annotated schema


# ---------- the gateway ----------
def _gateway(gw_env: Any, **env: str) -> Any:
    return gw_env(**{**PINNED_ENV, **env})


def _live(gw: Any, tmp_path: Any, teacher: FakeOpenAIServer, **overrides: Any) -> Any:
    """A live Runtime with the teacher and the student behind MockTransport."""
    student = FakeOpenAIServer(GOOD)
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / f"s{len(list(tmp_path.iterdir()))}.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "tf-teacher-api-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "https://student.test/v1",
        "student_api_key": "sk-student",
        "samples": 3,
        "audit_rate": 0.0,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "api_keys": "",
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",  # a live gateway without API keys starts only with this (ADR 0061)
        "admin_keys": "",
        "bom_signing_key": "",
        "mock_warmup_days": 0,
        "unknown_tasks": "distill",
        "max_auto_tasks": 200,
        "schema_repair": True,
    }
    return gw.Runtime(Settings(**{**base, **overrides}), transports={"teacher": teacher.transport, "student": student.transport})


@pytest.mark.req("FR-1.40", "FR-1.12")
def test_fenced_teacher_json_is_served_as_bare_json(gw_env, tmp_path, monkeypatch):
    sdk()
    from openai.types.chat import ChatCompletion

    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer(f"<think>The user was charged twice.</think>Here you go:\n```json\n{GOOD}\n```\nDone.")
    monkeypatch.setattr(gw, "rt", _live(gw, tmp_path, teacher))
    body = TestClient(gw.app).post(CHAT, json={"messages": ASK, "response_format": _rf()}).json()
    ChatCompletion.model_validate(body)
    content = body["choices"][0]["message"]["content"]
    assert content == GOOD and json.loads(content)["category"] == "billing"
    assert body["ratchet"]["schema_valid"] is True and body["ratchet"]["task"] == auto_name(_rf()["json_schema"])
    assert body["choices"][0]["finish_reason"] == "stop"
    # Streamed, the chunks spell the same bare JSON.
    stream = TestClient(gw.app).post(CHAT, json={"messages": ASK, "response_format": _rf(), "stream": True})
    chunks = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: {")]
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]) == GOOD
    assert chunks[-1]["ratchet"]["schema_valid"] is True


@pytest.mark.req("FR-1.40", "FR-14.2", "FR-2.15")
def test_responses_name_the_task_and_stats_name_the_compared_keys(gw_env):
    gw = _gateway(gw_env)
    c = TestClient(gw.app)
    plain = c.post(CHAT, json={"messages": ASK}).json()["ratchet"]
    assert (plain["task"], plain["schema_valid"]) == ("default", None)
    named = c.post(CHAT, json={"messages": ASK}, headers={"X-Ratchet-Task": "ticket-triage"}).json()["ratchet"]
    assert (named["task"], named["schema_valid"]) == ("ticket-triage", None)
    body = c.post(CHAT, json={"messages": ASK, "response_format": _rf()}).json()
    name = body["ratchet"]["task"]
    assert name == auto_name(_rf()["json_schema"]) and body["ratchet"]["schema_valid"] is True
    rows = {t["task"]: t for t in c.get("/ratchet/stats").json()["tasks"]}
    assert (rows[name]["compare_keys"], rows[name]["compare_source"]) == (["category", "priority"], "schema")
    assert (rows["ticket-triage"]["compare_keys"], rows["ticket-triage"]["compare_source"]) == (
        ["category", "priority"],
        "registry",
    )
    assert (rows["default"]["compare_keys"], rows["default"]["compare_source"]) == ([], "registry")
    listed = {t["name"]: t for t in c.get("/ratchet/tasks").json()["tasks"]}
    assert listed[name]["source"] == "auto" and listed[name]["verifiable"] and not listed[name]["pinned"]
    assert c.get(f"/ratchet/tasks/{name}").json()["updated_by"] is None
    # An operator can delete an auto task (mock mode: the admin API is open); its examples stay.
    gone = c.delete(f"/ratchet/tasks/{name}")
    assert gone.status_code == 200 and gone.json()["examples_kept"] == 1 and tasks.lookup(name) is None
    assert gw.rt.ledger.saved_task(name) is None
    assert c.delete("/ratchet/tasks/ticket-triage").status_code == 403
    # Under reject, a schema with nothing to compare is a 400 naming response_format as the cause.
    gw = _gateway(gw_env, RATCHET_UNKNOWN_TASKS="reject")
    free = {"type": "object", "properties": {"summary": {"type": "string"}}}
    r = TestClient(gw.app).post(CHAT, json={"messages": ASK, "response_format": _rf(free, "free")})
    assert r.status_code == 400 and (r.json()["error"]["code"], r.json()["error"]["param"]) == ("unknown_task", "response_format")


@pytest.mark.req("FR-1.40", "FR-9.7")
def test_the_sdk_parse_helper_works_end_to_end_in_mock_mode(gw_env):
    """`client.chat.completions.parse(response_format=Model)` sends a strict json_schema; the mock teacher answers an
    instance of it (the generic mock world), and the SDK parses the bare JSON into the model."""
    sdk()
    from pydantic import BaseModel

    class Part(BaseModel):
        part: Literal["screen", "battery", "hinge"]
        qty: int

    class Claim(BaseModel):
        decision: Literal["approve", "deny", "review"]
        covered: bool
        parts: list[Part]
        due: str | None
        note: str

    gw = _gateway(gw_env)
    client = make_openai_client(gw.app)
    messages = [{"role": "user", "content": "My phone screen cracked after two months. Claim 5521."}]
    resp = client.chat.completions.parse(model="ratchet", messages=messages, response_format=Claim)
    claim = resp.choices[0].message.parsed
    assert isinstance(claim, Claim) and claim.decision in ("approve", "deny", "review") and claim.parts
    extra = resp.model_extra["ratchet"]
    assert extra["schema_valid"] is True and extra["task"].startswith("auto-claim-")
    task = tasks.lookup(extra["task"])
    assert task is not None and [f.path for f in task.fields] == ["covered", "decision", "parts"]


@pytest.mark.req("FR-1.40")
def test_a_cut_off_answer_is_length_so_the_sdk_raises_its_length_error(gw_env, tmp_path, monkeypatch):
    openai, _ = sdk()
    from pydantic import BaseModel

    class Ticket(BaseModel):
        category: Literal["billing", "technical", "account"]
        priority: Literal["low", "high"]

    gw = _gateway(gw_env)
    teacher = FakeOpenAIServer('{"category": "bill', finish_reason="length")
    monkeypatch.setattr(gw, "rt", _live(gw, tmp_path, teacher))
    client = make_openai_client(gw.app)
    with pytest.raises(openai.LengthFinishReasonError):
        client.chat.completions.parse(model="ratchet", messages=ASK, response_format=Ticket)
    assert gw.rt.ledger.examples() == []  # cut off: never stored (INV-16)
