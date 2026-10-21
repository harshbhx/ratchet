"""Structured outputs (FR-1.40, FR-14.2, ADR 0042): what a `response_format` asks of an answer, and auto tasks whose
comparison fields come from a json_schema.

- `contract_for(response_format)` is the `Contract` a request's answers must meet: JSON for `json_object` (an object),
  JSON matching the schema for `json_schema` (`jsonschema_lite`). The router binds it to the request's task
  (`tasks.Task.bound`), so a student sample that breaks it gets no vote and a teacher answer that breaks it is repaired
  once and never stored (INV-16); `tasks.for_row` binds the stored conditions the same way in Night School (INV-6).
- `derive(schema)` turns a json_schema into a task's fields: enums become labels (an enum of numbers an exact
  `number`), booleans `bool`, integers and numbers `number`, date strings `date`, identifier strings (email, uuid, uri,
  ipv4, hostname) exact `text`, arrays of those a `set`, arrays of objects `records`, nested objects dot paths (at most 4
  levels and 64 fields); free text is ignored.
  A property's `x-ratchet` annotation (`{"compare": "text" | "ignore" | "ordered", "abs_tol": n}`) includes or
  excludes it. A pure function of the schema, so an auto task's name (`auto_name`, from a hash of the schema) always
  stands for the same fields.
- `forwardable(params)` strips every `x-ratchet...` key from the schema before any model sees it or it is stored.
- `downgrade` (a teacher that refuses json_schema gets json_object and the schema in its system message) and
  `repair_messages` (one more teacher call after an answer that broke the contract) build the conversations the router
  sends; neither is ever stored.

Standard library only, besides the package's own `compare`, `tasks` and `jsonschema_lite`. Nothing here does I/O; the
router registers and stores auto tasks (`Ratchet._auto_task`).
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from . import jsonschema_lite, tasks
from .compare import MAX_FIELDS, MAX_VALUES, FieldSpec
from .providers import GenParams

ANNOTATION = "x-ratchet"  # a property's comparison hint; every key starting with it is stripped before forwarding
COMPARE_HINTS = ("text", "ignore", "ordered")
AUTO_PREFIX = "auto-"
SLUG_CHARS = 40  # characters of the schema name an auto task's name keeps
# Hex digits of the schema's SHA-256 in an auto task's name: 64 bits, so no one can make another schema with the name
# of someone else's (with 8 digits, 2**32 tries would find one); a name still fits a task name's 64 characters.
NAME_DIGEST = 16
MAX_DERIVE_DEPTH = 4  # levels of nested objects (and records) a derived field may sit at
MAX_PATH = 256  # characters of a field path, as a tasks file allows (taskspec.MAX_PATH)
NUMBER_REL_TOL = 1e-6  # a `number` field's relative tolerance: 0.1 + 0.2 agrees with 0.3
ID_FORMATS = frozenset({"email", "uuid", "uri", "ipv4", "hostname"})  # strings compared exactly (case-folded)
REPAIR_ERRORS = 5  # schema errors a repair message lists
REPAIR_NOTE = "Your previous answer did not match the schema: {errors}. Reply with JSON only."
SCHEMA_NOTE = "Reply with JSON only. It must match this JSON schema:\n{schema}"
# The call that asks a teacher endpoint whether it takes json_schema at all (`Ratchet._probe_json_schema`): the smallest
# strict schema, which every endpoint that takes json_schema compiles.
PROBE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "probe",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}
PROBE_MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": 'Reply with {"ok": true}.'}]
PROBE_MAX_TOKENS = 64
CACHE_SIZE = 256  # schemas whose contract and derived task are kept
MEMO_SIZE = 1024  # (contract, answer) checks remembered: one request checks an answer several times (votes, agreement)

_IDENT = re.compile(r"[A-Za-z0-9_]+")  # a property name a field path may hold (taskspec's rule for paths)
_SLUG = re.compile(r"[^a-z0-9_-]+")
_INDEX = re.compile(r"\[\d+\]")
_NOT_JSON = object()
# Keywords whose value maps names to schemas, holds a list of schemas, or is one schema: where `strip_annotations`
# looks for schemas. Everything else (`enum`, `const`, `default`, `required`, ...) is a value, never a schema.
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
_SCHEMA_LISTS = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
_SCHEMA_ONE = frozenset(
    {
        "items",
        "additionalItems",
        "additionalProperties",
        "unevaluatedItems",
        "unevaluatedProperties",
        "not",
        "if",
        "then",
        "else",
        "contains",
        "propertyNames",
    }
)
_MAX_WALK = 64  # schema nesting `strip_annotations` follows (requests are refused past 16 levels)


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return _NOT_JSON


def canonical(value: Any) -> str:
    """`value` as canonical JSON (sorted keys, no spaces): the same text whatever the order of a schema's keys."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------------------------------------------------
# The contract


@dataclass(frozen=True, eq=False)
class Contract:
    """What a request's `response_format` asks of every answer: `kind` `json_object` (a JSON object) or `json_schema`
    (JSON matching `schema`, already stripped of annotations; None when the request sent no schema, then any JSON
    value). `any_value` says whether an answer's JSON may be something other than an object (a schema whose root is
    not an object), which decides where `tasks.json_span` looks for it. Shared between requests and threads: never
    mutate `schema`."""

    kind: str
    schema: Any = None
    name: str | None = None
    any_value: bool = False

    def _value(self, clean: str) -> Any:
        return _loads(clean.strip()) if isinstance(clean, str) else _NOT_JSON

    def problem(self, clean: str) -> str | None:
        """Why a cleaned answer (`tasks.clean_answer`) breaks the contract, or None: `invalid answer` when it is not JSON
        (or, for json_object, not an object), `schema mismatch at <path>` (array indices as `[]`) when it does not match
        the schema. Names a path of the schema, never the answer, so it may go into an event (INV-12). A schema check is
        remembered by the answer's SHA-256 (`MEMO_SIZE` of them), because the router asks about one answer several times
        (its vote, its agreement, the served choice) and a check may take up to `jsonschema_lite.MAX_STEPS` steps."""
        if self.schema is None or not isinstance(clean, str):
            return self._problem(clean)
        key = (self, hashlib.sha256(clean.encode("utf-8", "surrogatepass")).digest())
        with _MEMO_LOCK:
            if key in _MEMO:
                _MEMO.move_to_end(key)
                return _MEMO[key]
        found = self._problem(clean)
        with _MEMO_LOCK:
            _MEMO[key] = found
            while len(_MEMO) > MEMO_SIZE:
                _MEMO.popitem(last=False)
        return found

    def _problem(self, clean: str) -> str | None:
        value = self._value(clean)
        if value is _NOT_JSON or (self.kind == "json_object" and not isinstance(value, dict)):
            return "invalid answer"
        if self.schema is None:
            return None
        found = jsonschema_lite.check(value, self.schema, max_errors=1)
        return f"schema mismatch at {_INDEX.sub('[]', found[0][0])}" if found else None

    def errors(self, clean: str) -> list[str]:
        """Every way a cleaned answer breaks the contract (at most REPAIR_ERRORS), for a repair message; empty when it
        meets it."""
        value = self._value(clean)
        if value is _NOT_JSON:
            return ["$: the answer is not valid JSON"]
        if self.kind == "json_object" and not isinstance(value, dict):
            return ["$: the answer must be one JSON object"]
        if self.schema is None:
            return []
        return jsonschema_lite.validate(value, self.schema, max_errors=REPAIR_ERRORS)


JSON_OBJECT = Contract("json_object")
# `Contract.problem`'s memo, shared by every thread (Scout calls the router from several): the contract is the object
# itself (contracts are cached per schema), the answer its digest, so the memo holds no answer text.
_MEMO: OrderedDict[tuple[Contract, bytes], str | None] = OrderedDict()
_MEMO_LOCK = threading.Lock()


def _object_root(schema: Any) -> bool:
    """Whether a schema's root is plainly an object (`type: object`, or `properties` without a type)."""
    if not isinstance(schema, Mapping):
        return False
    kind = schema.get("type")
    return kind == "object" or (kind is None and isinstance(schema.get("properties"), Mapping))


@functools.lru_cache(maxsize=CACHE_SIZE)
def _schema_contract(text: str, name: str | None) -> Contract:
    schema = strip_annotations(json.loads(text))
    return Contract("json_schema", schema, name, any_value=not _object_root(schema))


def contract_for(response_format: Any) -> Contract | None:
    """The contract a `response_format` sets, or None for none (absent, `text`, or a type Ratchet does not know). The
    request validation has checked the shape of a request's; a stored one is read leniently: a json_schema without a
    usable schema asks for JSON of any kind. Contracts are cached by the schema's canonical JSON."""
    if not isinstance(response_format, Mapping):
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        return JSON_OBJECT
    if kind != "json_schema":
        return None
    spec = response_format.get("json_schema")
    spec = spec if isinstance(spec, Mapping) else {}
    name = spec.get("name") if isinstance(spec.get("name"), str) else None
    schema = spec.get("schema")
    if not isinstance(schema, Mapping):
        return Contract("json_schema", None, name, any_value=True)
    try:
        return _schema_contract(canonical(schema), name)
    except (TypeError, ValueError, RecursionError):  # not JSON a request could carry: check JSON-ness only
        return Contract("json_schema", None, name, any_value=True)


# ---------------------------------------------------------------------------------------------------------------------
# Annotations


def _strip(node: Any, depth: int) -> Any:
    if not isinstance(node, Mapping) or depth > _MAX_WALK:
        return node
    out: dict[str, Any] = {}
    changed = False
    for key, value in node.items():
        if isinstance(key, str) and key.startswith(ANNOTATION):
            changed = True
            continue
        new = value
        if key in _SCHEMA_MAPS and isinstance(value, Mapping):
            subs = {name: _strip(sub, depth + 1) for name, sub in value.items()}
            if any(subs[name] is not value[name] for name in value):
                new = subs
        elif (key in _SCHEMA_LISTS or key in _SCHEMA_ONE) and isinstance(value, list):
            items = [_strip(sub, depth + 1) for sub in value]
            if any(a is not b for a, b in zip(items, value, strict=True)):
                new = items
        elif key in _SCHEMA_ONE:
            new = _strip(value, depth + 1)
        changed = changed or new is not value
        out[key] = new
    return out if changed else node


def strip_annotations(schema: Any) -> Any:
    """`schema` without any key starting with `x-ratchet`, at any depth where a schema can sit (a property's schema,
    `items`, `$defs`, `anyOf`, ...), never touching a property *named* that way. The same object when there is none,
    so the common case copies nothing."""
    return _strip(schema, 0)


def forwardable(params: GenParams | None) -> GenParams | None:
    """`params` with the annotations stripped from a json_schema `response_format` (`strip_annotations`): what every
    model is sent and what an example stores. The same object when there is nothing to strip."""
    rf = params.response_format if params is not None else None
    if not isinstance(rf, Mapping) or rf.get("type") != "json_schema":
        return params
    spec = rf.get("json_schema")
    schema = spec.get("schema") if isinstance(spec, Mapping) else None
    if not isinstance(schema, Mapping):
        return params
    stripped = strip_annotations(schema)
    if stripped is schema:
        return params
    assert params is not None and isinstance(spec, Mapping)
    return replace(params, response_format={**rf, "json_schema": {**spec, "schema": stripped}})


# ---------------------------------------------------------------------------------------------------------------------
# Deriving fields


@dataclass(frozen=True)
class _Shape:
    node: Mapping[str, Any]
    type: Any  # the JSON type name, or None when the schema names none
    nullable: bool
    values: tuple[Any, ...] | None  # the enum without null, or None without an enum


def _is_null(schema: Any) -> bool:
    if not isinstance(schema, Mapping):
        return False
    if schema.get("type") == "null":
        return True
    if "const" in schema and schema["const"] is None:
        return True
    return isinstance(schema.get("enum"), list) and schema["enum"] == [None]


def _note(schema: Any) -> Mapping[str, Any]:
    note = schema.get(ANNOTATION) if isinstance(schema, Mapping) else None
    return note if isinstance(note, Mapping) else {}


class _Deriver:
    def __init__(self, root: Any) -> None:
        self.root = root
        self.left = MAX_FIELDS

    def resolve(self, ref: Any) -> Any:
        if not isinstance(ref, str) or not ref.startswith("#"):
            return None
        if ref == "#":
            return self.root
        if not ref.startswith("#/"):
            return None
        cur = self.root
        for token in ref[2:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(cur, Mapping) or key not in cur:
                return None
            cur = cur[key]
        return cur

    def shape(self, node: Any) -> _Shape | None:
        """What a property's value is: its schema with local `$ref`s, a one-schema `allOf` and a null option of
        `anyOf`/`oneOf` resolved, its type and whether null is allowed. None when it cannot be told: several non-null
        options, several types, a `$ref` that does not resolve or loops."""
        nullable = False
        seen: set[str] = set()
        for _ in range(64):
            if not isinstance(node, Mapping):
                return None
            if "$ref" in node:
                ref = node["$ref"]
                if not isinstance(ref, str) or ref in seen:
                    return None
                seen.add(ref)
                node = self.resolve(ref)
                continue
            options = node.get("anyOf", node.get("oneOf"))
            if isinstance(options, list) and options and "type" not in node and "enum" not in node:
                rest = [o for o in options if not _is_null(o)]
                nullable = nullable or len(rest) < len(options)
                if len(rest) != 1:
                    return None
                node = rest[0]
                continue
            parts = node.get("allOf")
            if isinstance(parts, list) and len(parts) == 1 and "type" not in node:
                node = parts[0]
                continue
            break
        else:
            return None
        kind = node.get("type")
        if isinstance(kind, list):
            names = [k for k in kind if k != "null"]
            nullable = nullable or len(names) < len(kind)
            if len(names) > 1:
                return None
            kind = names[0] if names else None
        values = None
        if isinstance(node.get("enum"), list):
            enum = node["enum"]
            values = tuple(v for v in enum if v is not None)
            nullable = nullable or len(values) < len(enum)
        return _Shape(node, kind, nullable, values)

    def object_fields(self, node: Mapping[str, Any], prefix: str, required: bool, depth: int) -> list[FieldSpec]:
        """The fields of an object's properties, in name order, with paths under `prefix`. `required` is False under an
        optional or nullable ancestor: a field the answer may lack entirely is never required."""
        props = node.get("properties")
        if not isinstance(props, Mapping) or depth > MAX_DERIVE_DEPTH:
            return []
        listed = node.get("required")
        needed = {k for k in listed if isinstance(k, str)} if isinstance(listed, list) else set()
        out: list[FieldSpec] = []
        for name in sorted(k for k in props if isinstance(k, str)):
            path = prefix + name
            if not _IDENT.fullmatch(name) or len(path) > MAX_PATH:
                continue  # a path a tasks file could not write back (taskspec), so the task could not be stored
            out.extend(self.field(path, props[name], required and name in needed, depth))
            if self.left <= 0:
                break
        return out

    def take(self, spec: FieldSpec) -> list[FieldSpec]:
        if spec.size > self.left:
            return []
        self.left -= spec.size
        return [spec]

    def field(self, path: str, raw: Any, required: bool, depth: int) -> list[FieldSpec]:
        """The field(s) one property gives: none, one, or a nested object's fields. `required` means the schema requires
        the key here and above; a null the schema allows is `nullable`."""
        note = _note(raw)
        shape = self.shape(raw)
        if shape is None:
            return []
        if not note:
            note = _note(shape.node)
        hint = note.get("compare") if note.get("compare") in COMPARE_HINTS else None
        if hint == "ignore" or "const" in shape.node:
            return []
        tol = note.get("abs_tol")
        abs_tol = float(tol) if isinstance(tol, (int, float)) and not isinstance(tol, bool) and 0 <= tol < 1e300 else 0.0
        base: dict[str, Any] = {"required": required, "nullable": shape.nullable}
        kind = shape.type
        if kind == "object" or (kind is None and isinstance(shape.node.get("properties"), Mapping)):
            if depth >= MAX_DERIVE_DEPTH:
                return []
            # Under a null (or absent) parent every child path is missing, so no child may be required.
            return self.object_fields(shape.node, path + ".", required and not shape.nullable, depth + 1)
        if kind == "array":
            return self.array(path, shape, base, hint, depth)
        scalar = self.scalar(shape, hint, abs_tol)
        if scalar is None:
            return []
        t, options = scalar
        try:
            return self.take(FieldSpec(path, t, **base, **options))
        except ValueError:
            if t == "label":  # two enum values that are one label once normalised ('in-progress', 'in_progress')
                return self.take(FieldSpec(path, "exact", **base))
            return []

    def scalar(self, shape: _Shape, hint: str | None, abs_tol: float) -> tuple[str, dict[str, Any]] | None:
        """(type, options) for a scalar value, or None for free text and anything else that is not compared."""
        kind, node = shape.type, shape.node
        if hint == "text":
            return ("text", {}) if kind in ("string", None) else None
        if shape.values is not None:
            values = shape.values
            if not values:
                return None
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                # A number, compared exactly: the schema takes 2.0 for an enum value 2, which a label '2' would refuse.
                # Which values are allowed is the schema's check (the contract), as for any bound task.
                return "number", {}
            if len(values) <= MAX_VALUES and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in values):
                return "label", {"values": tuple(str(v) for v in values)}
            return "exact", {}
        if kind == "boolean":
            return "bool", {}
        if kind == "integer":
            return "number", {"abs_tol": abs_tol}
        if kind == "number":
            return "number", {"abs_tol": abs_tol, "rel_tol": NUMBER_REL_TOL}
        if kind == "string":
            fmt = node.get("format")
            if fmt == "date":
                return "date", {}
            if fmt == "date-time":
                return "date", {"granularity": "datetime"}
            if fmt in ID_FORMATS:
                return "text", {"mode": "exact"}
        return None

    def array(self, path: str, shape: _Shape, base: dict[str, Any], hint: str | None, depth: int) -> list[FieldSpec]:
        item = self.shape(shape.node.get("items"))
        if item is None or item.nullable:
            return []
        ordered = hint == "ordered"
        if item.type == "object" or (item.type is None and isinstance(item.node.get("properties"), Mapping)):
            if depth >= MAX_DERIVE_DEPTH:
                return []
            left = self.left
            self.left -= 1  # the records field itself
            subs = self.object_fields(item.node, "", True, depth + 1)
            self.left = left
            if not subs:
                return []
            try:
                return self.take(FieldSpec(path, "records", **base, fields=tuple(subs), ordered=ordered))
            except ValueError:
                return []
        scalar = self.scalar(item, None, 0.0)
        if scalar is None:
            return []
        t, options = scalar
        if t not in ("label", "bool", "date", "text") and not (t == "number" and item.values is not None):
            return []  # numbers (but an enum's) and free text in a list: no comparison the schema implies
        kind = "ranking" if ordered else "set"
        try:
            if t == "label":
                spec = FieldSpec(path, kind, **base, values=options["values"])
            else:
                spec = FieldSpec(path, kind, **base, item=FieldSpec("$", t, **options))
        except ValueError:
            return []
        return self.take(spec)


def derive(schema: Any) -> tuple[FieldSpec, ...]:
    """The fields a json_schema implies (module docstring), in property-name order: empty when the root is not an
    object with properties or nothing in it is comparable. A field is required when the schema requires it and every
    object above it, and nullable when the schema allows null. Never raises."""
    d = _Deriver(schema)
    root = d.shape(schema)
    if root is None or root.nullable:
        return ()
    if root.type not in ("object", None) or not isinstance(root.node.get("properties"), Mapping):
        return ()
    try:
        return tuple(d.object_fields(root.node, "", True, 1))
    except RecursionError:
        return ()


# ---------------------------------------------------------------------------------------------------------------------
# Auto tasks


def auto_name(json_schema: Mapping[str, Any]) -> str:
    """The name of the auto task for a json_schema `response_format`'s `json_schema` object: `auto-`, its `name` as a
    slug (lower case; at most SLUG_CHARS characters), `-` and the first NAME_DIGEST (16) hex digits of SHA-256 over the
    canonical JSON of its schema (annotations included, since they change the fields). Key order does not change it.
    The router uses the auto task under a name only for a schema that derives its rules (`Ratchet._derives`)."""
    raw = json_schema.get("name")
    slug = _SLUG.sub("-", raw.lower() if isinstance(raw, str) else "").strip("-")[:SLUG_CHARS].strip("-") or "schema"
    digest = hashlib.sha256(canonical(json_schema.get("schema")).encode("utf-8", "surrogatepass")).hexdigest()[:NAME_DIGEST]
    return f"{AUTO_PREFIX}{slug}-{digest}"


@functools.lru_cache(maxsize=CACHE_SIZE)
def _auto_task(name: str, text: str, schema_name: str | None) -> tasks.Task | None:
    fields = derive(json.loads(text))
    if not fields:
        return None
    described = f"Derived from the response_format schema {schema_name!r}" if schema_name else "Derived from a schema"
    try:
        return tasks.Task(name, fields=fields, format="json", source="auto", description=described[: tasks.MAX_DESCRIPTION])
    except ValueError:
        return None


def auto_task(name: str, schema: Any, schema_name: str | None = None) -> tasks.Task | None:
    """The auto task `name` for `schema` (source `auto`, never pinned, format `json`, fields from `derive`), or None
    when the schema has no comparable field: such a request is an unknown task and follows RATCHET_UNKNOWN_TASKS, so a
    free-text schema is not silently distilled. Cached by the schema's canonical JSON; not registered here."""
    try:
        text = canonical(schema)
    except (TypeError, ValueError, RecursionError):
        return None
    return _auto_task(name, text, schema_name)


# ---------------------------------------------------------------------------------------------------------------------
# Conversations the router sends (never stored)


def _with_system_note(messages: list[dict[str, Any]], note: str) -> list[dict[str, Any]]:
    """`messages` with `note` appended to the leading system message (a new list and message; the caller's are
    unchanged), or a system message holding it put first when there is none with text."""
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        first = dict(messages[0])
        first["content"] = f"{first['content']}\n\n{note}" if first["content"] else note
        return [first, *messages[1:]]
    return [{"role": "system", "content": note}, *messages]


def downgrade(messages: list[dict[str, Any]], gen: GenParams) -> tuple[list[dict[str, Any]], GenParams]:
    """The call for a teacher that refuses `response_format` json_schema: `{"type": "json_object"}` instead, and the
    schema in its system message (`SCHEMA_NOTE`). The answer is still checked against the schema."""
    rf = gen.response_format if isinstance(gen.response_format, Mapping) else {}
    spec = rf.get("json_schema") if isinstance(rf.get("json_schema"), Mapping) else {}
    schema = spec.get("schema", {})
    note = SCHEMA_NOTE.format(schema=json.dumps(schema, ensure_ascii=False))
    return _with_system_note(messages, note), replace(gen, response_format={"type": "json_object"})


def schema_digest(gen: GenParams | None) -> str:
    """The first 16 hex digits of SHA-256 over the canonical JSON of a call's json_schema schema (`canonical`): what the
    router remembers a schema a teacher refused by. '' when the call has none."""
    rf = gen.response_format if gen is not None else None
    spec = rf.get("json_schema") if isinstance(rf, Mapping) else None
    schema = spec.get("schema") if isinstance(spec, Mapping) else None
    try:
        text = canonical(schema)
    except (TypeError, ValueError, RecursionError):
        return ""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def wants_json_schema(gen: GenParams | None) -> bool:
    """Whether a call asks for `response_format` json_schema."""
    rf = gen.response_format if gen is not None else None
    return isinstance(rf, Mapping) and rf.get("type") == "json_schema"


def repair_messages(messages: list[dict[str, Any]], previous: str, errors: list[str]) -> list[dict[str, Any]]:
    """The conversation for one repair: the request's messages, the teacher's previous answer as cleaned (when there is
    any) as an assistant turn, then `REPAIR_NOTE` with the first REPAIR_ERRORS errors as a user turn. A user turn, not a
    system message: many chat templates refuse a system message after the first turn, and user and assistant turns
    alternate in every template. The errors name schema paths, never the answer's values. Never stored: the example
    keeps the original messages."""
    note = REPAIR_NOTE.format(errors="; ".join(errors[:REPAIR_ERRORS]) or "it is not valid JSON")
    out = list(messages)
    if previous:
        out.append({"role": "assistant", "content": previous})
    out.append({"role": "user", "content": note})
    return out
