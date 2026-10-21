"""A small JSON Schema validator for the subset OpenAI's structured outputs use (FR-1.40, ADR 0042).

Ratchet checks every answer to a `response_format` json_schema request against the schema: a student sample that does
not match gets no vote, and a teacher answer that does not match is repaired once and never stored (INV-16). The
`jsonschema` package is not a dependency, and the request path needs only the strict subset, so this module covers:

- `type` (one name or a list, `null` included), `enum` and `const` (JSON equality: `true` is not `1`, `1` is `1.0`);
- objects: `properties`, `required`, `additionalProperties` (`false` or a schema; not enforced beside
  `patternProperties`, whose regular expressions are never run, so a key they would allow is never refused);
- arrays: `items` (one schema), `prefixItems` (a tuple, with `items` for the rest), `minItems`, `maxItems`;
- `anyOf`, `oneOf`, `allOf`;
- `$ref` to the schema itself or into it (`#`, `#/$defs/x`, `#/definitions/x`, any local JSON pointer), with a guard
  against a reference that loops back without reaching a value;
- `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `minLength`, `maxLength`.

Every other keyword (`format`, `pattern`, `multipleOf`, `title`, `description`, ...) is ignored, as a validator ignores
annotations. A `pattern` is a client's regular expression: running it on the request path would let one request make
the gateway slow. A reference that does not resolve, or a schema too large to check within `MAX_STEPS`, is an error:
an answer that cannot be checked is not taken as valid.

Errors name a path built from the schema's own property names and array indices, and the schema's expectation, never
a value or a key from the answer, so they can go into an event (INV-12) or a repair message. Standard library only;
recursion is bounded by `MAX_DEPTH` levels of the answer and `MAX_STEPS` schema nodes per check.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

MAX_ERRORS = 20  # errors one check reports; the first one decides validity
MAX_STEPS = 100_000  # schema nodes one check may visit (anyOf inside arrays multiplies them); about 0.3 s at worst
MAX_DEPTH = 64  # levels of nesting in an answer that are checked
TYPES = ("null", "boolean", "integer", "number", "string", "array", "object")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class _TooComplex(Exception):
    """The step budget ran out."""


class _Full(Exception):
    """`max_errors` errors were found: nothing more is needed."""


def validate(instance: Any, schema: Any, *, max_errors: int = MAX_ERRORS) -> list[str]:
    """Why `instance` does not match `schema`, as `<path>: <problem>` strings (at most `max_errors` of them), or an empty
    list when it matches. Never raises for any JSON value and any schema a request can carry."""
    return [f"{path}: {problem}" for path, problem in check(instance, schema, max_errors=max_errors)]


def check(instance: Any, schema: Any, *, max_errors: int = MAX_ERRORS) -> list[tuple[str, str]]:
    """`validate` as (path, problem) pairs, so a caller can name the path alone."""
    v = _Validator(schema, max(1, max_errors))
    try:
        v.node(instance, schema, "$", 0, frozenset())
    except _Full:
        pass
    except _TooComplex:
        return [("$", f"the schema is too large to check (more than {MAX_STEPS} steps)")]
    except RecursionError:
        return [("$", "the schema is nested too deeply to check")]
    return v.errors


def json_equal(a: Any, b: Any) -> bool:
    """Equality as JSON Schema defines it: `true` is not `1` (Python says it is), `1` equals `1.0`, arrays by position,
    objects by key. Iterative, so a deep value cannot exhaust the stack."""
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if isinstance(x, bool) or isinstance(y, bool):
            if not (isinstance(x, bool) and isinstance(y, bool) and x == y):
                return False
        elif _is_number(x) or _is_number(y):
            if not (_is_number(x) and _is_number(y) and x == y):
                return False
        elif isinstance(x, str) or isinstance(y, str) or x is None or y is None:
            if type(x) is not type(y) or x != y:
                return False
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                return False
            stack.extend(zip(x, y, strict=True))
        elif isinstance(x, dict) and isinstance(y, dict):
            if x.keys() != y.keys():
                return False
            stack.extend((x[k], y[k]) for k in x)
        else:
            return False
    return True


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    return isinstance(v, int) or (isinstance(v, float) and math.isfinite(v))


def type_of(value: Any) -> str | None:
    """The JSON type of a parsed value (`integer` for a whole number, also written `1.0`), or None for a value JSON
    cannot hold (NaN and the infinities, which `json.loads` accepts)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return "integer" if value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


def _has_type(value: Any, name: Any) -> bool:
    t = type_of(value)
    return t == name or (name == "number" and t == "integer")


def _child(path: str, key: str) -> str:
    """`path.key`, or `path["key"]` for a key that is not an identifier."""
    if _IDENT.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _bound(value: Any) -> int | float | None:
    """A numeric keyword's value, or None when it is not a finite number (then the keyword is ignored). Never converted
    to a float: Python compares an int and a float exactly, and a huge int does not fit in one."""
    return value if _is_number(value) and _finite(value) else None


def _count(value: Any) -> int | None:
    """A count keyword's value (minItems, maxLength, ...), or None when it is not a whole number >= 0."""
    if isinstance(value, bool) or not _is_number(value) or not _finite(value) or value < 0:
        return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    return value


class _Validator:
    def __init__(self, root: Any, max_errors: int) -> None:
        self.root = root
        self.max_errors = max_errors
        self.errors: list[tuple[str, str]] = []
        self.steps = 0

    def add(self, path: str, problem: str) -> None:
        self.errors.append((path, problem))
        if len(self.errors) >= self.max_errors:
            raise _Full

    def resolve(self, ref: Any) -> tuple[bool, Any]:
        """(found, target) for a local `$ref`."""
        if not isinstance(ref, str) or not ref.startswith("#"):
            return False, None
        pointer = ref[1:]
        if pointer == "":
            return True, self.root
        if not pointer.startswith("/"):
            return False, None
        cur = self.root
        for token in pointer[1:].split("/"):
            key = _unescape(token)
            if isinstance(cur, Mapping) and key in cur:
                cur = cur[key]
            elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
                cur = cur[int(key)]
            else:
                return False, None
        return True, cur

    def passes(self, value: Any, schema: Any, path: str, depth: int, refs: frozenset[str]) -> bool:
        """Whether `value` matches `schema`, without recording errors (anyOf, oneOf). Shares the step budget."""
        sub = _Validator(self.root, 1)
        sub.steps = self.steps
        try:
            sub.node(value, schema, path, depth, refs)
        except _Full:
            pass
        finally:
            self.steps = sub.steps
        return not sub.errors

    def node(self, value: Any, schema: Any, path: str, depth: int, refs: frozenset[str]) -> None:
        """Check `value` (at `path`, `depth` levels into the answer) against `schema`. `refs` are the references
        followed at this value so far: one followed twice loops without reaching a value."""
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise _TooComplex
        if schema is True or schema is None:  # true (or a missing subschema) allows anything
            return
        if schema is False:
            self.add(path, "no value is allowed here")
            return
        if not isinstance(schema, Mapping):
            return  # not a schema: nothing to check (the request validation lets only objects through at the top)
        if "$ref" in schema:
            ref = schema["$ref"]
            if isinstance(ref, str) and ref in refs:
                self.add(path, f"$ref {ref} loops back to itself without reaching a value")
                return
            found, target = self.resolve(ref)
            if not found:
                self.add(path, "a $ref the schema does not define")
                return
            self.node(value, target, path, depth, refs | {ref})
        self.keywords(value, schema, path, depth, refs)

    def keywords(self, value: Any, schema: Mapping[str, Any], path: str, depth: int, refs: frozenset[str]) -> None:
        if "type" in schema:
            wanted = schema["type"]
            names = wanted if isinstance(wanted, list) else [wanted]
            names = [n for n in names if n in TYPES]
            if names and not any(_has_type(value, n) for n in names):
                self.add(path, f"expected {' or '.join(names)}")
                return  # the other keywords would only repeat the mismatch
        if type_of(value) is None:
            self.add(path, "not a JSON value (NaN or infinity)")
            return
        enum = schema.get("enum")
        if isinstance(enum, list):
            # A string or null equals only an option of its own type, so `in` is exact for them (and fast on big enums);
            # Python's `in` would take `true` for `1`, so anything else goes through json_equal.
            found = value in enum if value is None or isinstance(value, str) else any(json_equal(value, o) for o in enum)
            if not found:
                self.add(path, "not one of the values in 'enum'")
        if "const" in schema and not json_equal(value, schema["const"]):
            self.add(path, "not the value in 'const'")
        for key in ("allOf", "anyOf", "oneOf"):
            options = schema.get(key)
            if isinstance(options, list) and options:
                self.combine(key, options, value, path, depth, refs)
        if isinstance(value, dict):
            self.object(value, schema, path, depth, refs)
        elif isinstance(value, list):
            self.array(value, schema, path, depth, refs)
        elif isinstance(value, str):
            self.string(value, schema, path)
        elif _is_number(value):
            self.number(value, schema, path)

    def combine(self, key: str, options: list[Any], value: Any, path: str, depth: int, refs: frozenset[str]) -> None:
        if key == "allOf":
            for option in options:
                self.node(value, option, path, depth, refs)
            return
        if key == "anyOf":
            if not any(self.passes(value, option, path, depth, refs) for option in options):
                self.add(path, "matches none of the schemas in 'anyOf'")
            return
        matched = 0
        for option in options:
            matched += self.passes(value, option, path, depth, refs)
            if matched > 1:
                break
        if matched != 1:
            self.add(path, f"matches {'none' if matched == 0 else 'more than one'} of the schemas in 'oneOf'")

    def object(self, value: dict[str, Any], schema: Mapping[str, Any], path: str, depth: int, refs: frozenset[str]) -> None:
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in value:
                    self.add(_child(path, name), "missing (required)")
        props = schema.get("properties")
        props = props if isinstance(props, Mapping) else {}
        extra = schema.get("additionalProperties", True)
        patterns = schema.get("patternProperties")
        if isinstance(patterns, Mapping) and patterns:
            extra = True  # a key a pattern allows cannot be told apart without running the client's regex: never refused
        if depth >= MAX_DEPTH:
            if value:
                self.add(path, f"nested more than {MAX_DEPTH} levels deep")
            return
        unlisted = False
        for key, item in value.items():
            if key in props:
                self.node(item, props[key], _child(path, key), depth + 1, frozenset())
            elif extra is False:
                unlisted = True
            elif isinstance(extra, Mapping):
                self.node(item, extra, f"{path}.*", depth + 1, frozenset())
        if unlisted:  # named once and without the key: it comes from the answer, not the schema
            self.add(path, "has a property the schema does not list (additionalProperties is false)")

    def array(self, value: list[Any], schema: Mapping[str, Any], path: str, depth: int, refs: frozenset[str]) -> None:
        low, high = _count(schema.get("minItems")), _count(schema.get("maxItems"))
        if low is not None and len(value) < low:
            self.add(path, f"fewer than {low} items (minItems)")
        if high is not None and len(value) > high:
            self.add(path, f"more than {high} items (maxItems)")
        prefix = schema.get("prefixItems")
        prefix = prefix if isinstance(prefix, list) else []
        items = schema.get("items")
        if not isinstance(items, (Mapping, bool)):  # missing, or the old list form of a tuple: nothing more to check
            items = True
        if (items is True and not prefix) or not value:
            return
        if depth >= MAX_DEPTH:
            self.add(path, f"nested more than {MAX_DEPTH} levels deep")
            return
        for i, item in enumerate(value):  # a tuple's positions first (2020-12), then `items` for the rest
            sub = prefix[i] if i < len(prefix) else items
            if sub is not True:
                self.node(item, sub, f"{path}[{i}]", depth + 1, frozenset())

    def string(self, value: str, schema: Mapping[str, Any], path: str) -> None:
        low, high = _count(schema.get("minLength")), _count(schema.get("maxLength"))
        if low is not None and len(value) < low:  # a Python string counts code points, as JSON Schema does
            self.add(path, f"shorter than {low} characters (minLength)")
        if high is not None and len(value) > high:
            self.add(path, f"longer than {high} characters (maxLength)")

    def number(self, value: int | float, schema: Mapping[str, Any], path: str) -> None:
        checks = (
            ("minimum", lambda x, b: x >= b, "less than"),
            ("maximum", lambda x, b: x <= b, "more than"),
            ("exclusiveMinimum", lambda x, b: x > b, "not more than"),
            ("exclusiveMaximum", lambda x, b: x < b, "not less than"),
        )
        for key, ok, words in checks:
            bound = _bound(schema.get(key))
            if bound is not None and not ok(value, bound):  # int and float compare exactly in Python
                self.add(path, f"{words} {bound} ({key})")
