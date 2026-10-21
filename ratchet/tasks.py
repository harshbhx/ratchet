"""Task definitions: when two answers to the same task agree, which answers are valid, and how an answer is cleaned.

A task tells Ratchet which parts of an answer must match for the student to count as agreeing with the teacher: its
`fields` (FR-2.14, ADR 0021), each a path and a typed comparator from `compare.py` (a label over a vocabulary, a number
with a tolerance, a date in any common format, a set, records, ...). Free-text fields that are not listed are ignored;
a `similarity` field compares free text by a score instead (FR-2.55), and the golden eval's judge grades it (FR-14.40). A
task with such a field needs `policy.samples` of at least 2 (`samples_problem`). A field is required unless it says
otherwise, so an answer without it is invalid: it gets no vote in the confidence score and is never stored as a training
example (INV-16). A task without fields compares whole answers: canonical JSON when both are JSON, else the text,
lower-cased with whitespace collapsed. A task with format `sql` answers with one SQL query, and two answers agree when
their queries return the same rows on the task's fixture (`sqlcheck.py`, FR-2.56): its `sql` options (`sqlcheck.SqlSpec`)
take the place of fields.

Every answer is cleaned the same way before it is compared, stored or served (`clean_answer`): reasoning is removed
(`strip_reasoning`), and a JSON or SQL task's answer loses a code fence wrapped around the whole of it. A JSON answer that is
already one JSON object is kept as it is, so a reply that mentions a `<think>` tag keeps its text.

The registry (FR-2.15, ADR 0022): `REGISTRY` holds the tasks a request can name, by name. It starts with `BUILTIN` (the
tasks defined here); `taskspec.install` adds the tasks of RATCHET_TASKS_FILE (which may replace a built-in one) and the
ones the admin API stored (which may replace neither), building the whole registry before it publishes it
(`replace_all`). Writers (`register`, `unregister`, `reset_builtin`, `replace_all`) take a lock and publish a new dict,
so `get` on the request path reads without one. Only an operator's spec (built-in, file or API)
can pin a task; nothing in a request can.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import sqlcheck, vocab
from .compare import INVALID as NO_KEY
from .compare import MAX_FIELDS, MAX_RECORDS, FieldSpec, Record, jsonable

# Answer formats. `json`: the answer is JSON and fields are paths into it. `text`: the answer is plain text and its one
# field is '$', the text without surrounding whitespace and trailing `.!,;:`. `sql`: the answer is one SQL query,
# compared by what it returns on the task's fixture (`Task.sql`, FR-2.56). `tool_calls` is reserved for a later release.
FORMATS = ("json", "text", "tool_calls", "sql")
RESERVED_FORMATS = frozenset({"tool_calls"})
# Where a task was defined: in this file, a tasks file, the admin API, derived from a request, or made up for an unknown
# task name (`get`, never stored in REGISTRY).
SOURCES = ("builtin", "file", "api", "auto", "adhoc")
TOOLS = ("teacher", "distill")  # who answers tool calls for this task (distill: opt-in for a later release)
# A task's own release policy: `samples` (the router and the golden eval, FR-2.55), `target_precision` and
# `min_precision` (per-task calibration, FR-14.1); `audit_rate` is for a later release.
POLICY_KEYS = ("samples", "audit_rate", "target_precision", "min_precision")
# What a task's measured precision means (`Task.precision_kind`): `exact` when its comparators decide agreement, `proxy`
# when a `similarity` field makes it closeness to the teacher's answer (FR-14.40).
PRECISION_KINDS = ("exact", "proxy")
# How a request without a task name may be matched to this task (a later release reads them): its system message starts
# with `system_prefix`, its `response_format.json_schema.name` is `schema_name`, or its tools are exactly `tools`. Plain
# comparisons, never a pattern a request could make slow.
MATCH_KEYS = ("system_prefix", "schema_name", "tools")
MAX_DESCRIPTION = 1000  # characters
MAX_PREFIX = 2000  # characters of `match.system_prefix`
MAX_MATCH_TOOLS = 128  # OpenAI's limit on a request's tools
MAX_CLIENTS = 256
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")  # OpenAI's rule for a function name and a json_schema name
_TEXT_TRAILING = frozenset(".!,;:")


@dataclass(frozen=True)
class Task:
    """A task: its name, whether it is pinned to the teacher, and its spec (`format`, `fields` or `sql`, `policy`,
    `routing`, `store`, `tools`). `compare_keys` is the v0.4 shortcut: each key becomes an optional `exact` field, and such a task
    compares, validates and canonicalises answers exactly as v0.4 did. `spec_hash` is derived from the spec (not from
    the name or `source`), so evaluations can tell when a task's rules changed; it cannot be set, so it never drifts
    from the rules (`dataclasses.replace` computes it again). Immutable: `policy`, `routing` and `match_rules` are
    read-only mappings. Invalid specs raise ValueError.

    `description`, `clients` (the API key names that may use the task; empty: every key) and `match_rules` (a spec
    file's `match`) say who uses the task and how a request finds it, not how its answers compare, so they are left
    out of `spec()` and `spec_hash`: editing them never sends the task back to the teacher for recalibration."""

    name: str
    compare_keys: Sequence[str] = ()
    # Pinned tasks always go to the teacher and are never stored as training examples:
    # open-ended work (planning, writing) is where the big model earns its price.
    pinned: bool = False
    format: str | None = None  # None: 'json' when the task has fields, else 'text'
    fields: tuple[FieldSpec, ...] = ()
    policy: Mapping[str, Any] = field(default_factory=dict)
    routing: Mapping[str, Any] = field(default_factory=dict)
    store: bool = True  # False: the task's teacher answers are served but never stored as training examples
    tools: str = "teacher"
    source: str = "builtin"
    description: str = ""
    clients: Sequence[str] = ()  # API key names (RATCHET_API_KEYS) allowed to use this task; empty: every key
    match_rules: Mapping[str, Any] = field(default_factory=dict)  # MATCH_KEYS; a spec file's `match`
    sql: sqlcheck.SqlSpec | None = None  # format 'sql': the fixture its queries run on and how results compare (FR-2.56)
    spec_hash: str = field(init=False, default="")  # sha256 of `spec()`, 16 hex digits
    _root: bool = field(init=False, repr=False, compare=False, default=False)  # a field at '$'

    def __post_init__(self) -> None:
        def fail(message: str) -> None:
            raise ValueError(f"task {self.name!r}: {message}")

        def put(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        if not isinstance(self.pinned, bool) or not isinstance(self.store, bool):
            fail("pinned and store must be true or false")
        if isinstance(self.compare_keys, str):
            fail("compare_keys must be a list of keys")
        keys = tuple(self.compare_keys)
        specs = self.fields
        if isinstance(specs, FieldSpec) or not isinstance(specs, (tuple, list)):
            fail("fields must be a list of FieldSpecs")
        if keys:
            if not all(isinstance(k, str) for k in keys):
                fail("compare_keys must be strings")
            for k in keys:  # v0.4 read each key at the top level; a path would read another value
                if not k or k == "$" or "." in k:
                    fail(f"compare key {k!r} is not a plain top-level key ('.' and '$' are path syntax); use fields")
            derived = tuple(FieldSpec(k, "exact", required=False) for k in dict.fromkeys(keys))
            if specs and tuple(specs) != derived:  # equal ones come from dataclasses.replace, which passes both
                fail("give compare_keys or fields, not both")
            specs = derived
        specs = tuple(specs)
        if not all(isinstance(f, FieldSpec) for f in specs):
            fail("fields must be FieldSpecs")
        paths = [f.path for f in specs]
        if len(set(paths)) != len(paths):
            fail("two fields have the same path")
        if sum(f.size for f in specs) > MAX_FIELDS:
            fail(f"more than {MAX_FIELDS} fields, counting the fields inside records")
        put("compare_keys", keys)
        put("fields", specs)

        if self.format is not None:
            fmt = self.format
        else:
            fmt = "sql" if self.sql is not None else "json" if specs else "text"
        if fmt in RESERVED_FORMATS:
            fail(f"format {fmt!r} is reserved for a later release")
        if fmt not in FORMATS:
            fail(f"format must be one of {', '.join(f for f in FORMATS if f not in RESERVED_FORMATS)}")
        if fmt == "text" and any(p != "$" for p in paths):
            fail("a text task has one field, '$' (the whole answer)")
        problem = sql_problem(fmt, specs, self.sql)
        if problem is not None:
            fail(problem)
        put("format", fmt)

        if self.tools not in TOOLS:
            fail(f"tools must be one of {', '.join(TOOLS)}")
        if self.source not in SOURCES:
            fail(f"source must be one of {', '.join(SOURCES)}")
        if self.pinned and self.source in ("auto", "adhoc"):
            fail("only an operator's spec (built-in, file or API) can pin a task; auto and adhoc tasks are never pinned")
        if not isinstance(self.description, str) or len(self.description) > MAX_DESCRIPTION:
            fail(f"description must be a string of at most {MAX_DESCRIPTION} characters")
        problem = clients_problem(self.clients)
        if problem is not None:
            fail(f"clients {problem}")
        put("clients", tuple(self.clients))
        if not isinstance(self.match_rules, Mapping):
            fail("match must be a mapping")
        for key, value in self.match_rules.items():
            problem = match_problem(key, value)
            if problem is not None:
                fail(f"match.{key} {problem}")
        put("match_rules", MappingProxyType({k: tuple(v) if k == "tools" else v for k, v in self.match_rules.items()}))
        put("policy", MappingProxyType(_policy(self.policy, fail)))
        problem = samples_problem(specs, self.policy)
        if problem is not None:
            fail(problem)
        if not isinstance(self.routing, Mapping) or not all(isinstance(k, str) for k in self.routing):
            fail("routing must be a mapping with string keys")
        put("routing", MappingProxyType(dict(self.routing)))
        try:
            text = json.dumps(self.spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError, RecursionError):
            fail("policy and routing must hold JSON values")
        put("spec_hash", hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
        put("_root", any(p == "$" for p in paths))

    def spec(self) -> dict[str, Any]:
        """The task's rules as a plain dict (everything but its name, `spec_hash` and `source`): what `spec_hash`
        covers. A sql task's also hold its `sql` options and its fixture's sha256; no other task's dict has the key, so
        their hashes are what they were before the format existed."""
        out = {
            "format": self.format,
            "fields": [f.spec() for f in self.fields],
            "pinned": self.pinned,
            "store": self.store,
            "tools": self.tools,
            "policy": dict(self.policy),
            "routing": dict(self.routing),
        }
        if self.sql is not None:
            out["sql"] = self.sql.spec()
        return out

    @property
    def verifiable(self) -> bool:
        """The task names fields that can be checked (labels, numbers, dates, ...), or runs its SQL answers on a fixture.
        A task without fields compares whole answers, which tells little about free text, and a `similarity` field only
        says an answer is close to another."""
        if self.sql is not None:
            return self.sql.executes
        return any(f.type != "similarity" for f in self.fields)

    @property
    def free_text(self) -> bool:
        """Agreement with the teacher's answer does not show the answer is right: the task has no fields (whole answers
        compared; a postgres SQL task compares query texts) or a `similarity` field (close enough, by a score). The
        golden eval asks the judge about such a task's answers too (FR-14.40). A SQL task that runs its answers on a
        fixture is not free text: agreement there means the same rows."""
        if self.sql is not None:
            return not self.sql.executes
        return not self.fields or any(f.type == "similarity" for f in self.fields)

    @property
    def precision_kind(self) -> str:
        """What a precision measured on this task means (`PRECISION_KINDS`): `proxy` with a `similarity` field (the
        share of served answers close enough to the teacher's), else `exact` (the share that agree under comparators
        that decide it)."""
        return "proxy" if any(f.type == "similarity" for f in self.fields) else "exact"

    def sample_count(self, default: int) -> int:
        """How many student samples one confidence score of this task rests on: its policy's `samples`, else `default`
        (RATCHET_SAMPLES). The router and the golden eval both read it, so a task is calibrated on what it is served
        with."""
        n = self.policy.get("samples")
        return n if isinstance(n, int) and not isinstance(n, bool) else default

    def allows(self, client: str | None) -> bool:
        """Whether the API key named `client` may use this task: always without `clients`; with them, only a key they
        name, never a request without a key (the allow-list cannot be checked, so it is refused)."""
        return not self.clients or (client is not None and client in self.clients)

    @property
    def structured(self) -> bool:
        """Answers are JSON (`format` 'json'): `clean_answer` unwraps a code fence around one, and an answer that does not
        parse is invalid."""
        return self.format == "json"

    # -----------------------------------------------------------------------------------------------------------------
    def _key(self, text: str | None) -> tuple[Any, str | None]:
        """`key` and, when it is NO_KEY, why (`check`)."""
        clean = clean_answer(self, text)
        if not clean:
            return NO_KEY, "empty answer"
        if self.sql is not None:
            return self.sql.key(clean)
        if not self.fields:
            return _whole_key(clean, self.format == "json")
        if self.format == "text":
            root: Any = _rstrip(clean, _TEXT_TRAILING)
        elif self._root:  # a '$' field: the answer may be any JSON value, a list or a scalar too
            root = _parse_any(clean)
            if root is _NOT_JSON:
                return NO_KEY, "invalid answer"
        else:
            root = parse_json(clean)
            if root is None:
                return NO_KEY, "invalid answer"
        budget = [MAX_RECORDS]
        key = Record()
        for f in self.fields:
            v = f.normalize(root if f.path == "$" else f.resolve(root), budget)
            if v is NO_KEY:
                return NO_KEY, f"invalid field {f.path}"
            key[f.path] = v
        return key, None

    def key(self, text: str | None) -> dict[str, Any] | Any:
        """What of an answer must agree: each field's normalised value by path (a `sqlcheck.SqlKey` for a sql task), or
        `compare.INVALID` (`tasks.NO_KEY`) for an answer that cannot count: empty after `clean_answer`, not parseable in
        the task's format, or with a required field missing (or null when it is not nullable) or a field that does not
        normalise (a label outside its vocabulary); for a sql task, not one query, or one that fails or hits a limit on
        the fixture. Compute it once per answer and compare keys with `match`."""
        return self._key(text)[0]

    def check(self, text: str | None) -> str | None:
        """Why an answer is not valid under the task (`empty answer`, `invalid answer`, `invalid field <path>`, or for a
        sql task one of sqlcheck's fixed reasons such as `sql error`), or None when it is. The reason names a path of the
        spec or a fixed string, never the answer's text, so it may go into an event (INV-12)."""
        return self._key(text)[1]

    def valid(self, text: str | None) -> bool:
        """The answer parses, every required field is present (null only when nullable), and every field normalises."""
        return self.key(text) is not NO_KEY

    def match(self, ka: Any, kb: Any) -> bool:
        """Whether two keys (`key`) agree: every field matches under its comparator. A task without fields compares
        canonical JSON when both answers are JSON objects or arrays, else their text. NO_KEY never agrees."""
        if ka is NO_KEY or kb is NO_KEY:
            return False
        if self.sql is not None:
            return self.sql.match(ka, kb)
        if not self.fields:
            ja, jb = ka["$json"], kb["$json"]
            return ja == jb if ja is not None and jb is not None else ka["$"] == kb["$"]
        return all(f.match(ka[f.path], kb[f.path]) for f in self.fields)

    def agree(self, a: str | None, b: str | None) -> bool:
        """Two answers agree: both are valid and their keys match."""
        return self.match(self.key(a), self.key(b))

    def canonical(self, text: str | None) -> str:
        """The part of an answer that must match as one string, for logs and tests, or `INVALID` (`<invalid>`): JSON of
        the normalised values by path, or for a task without fields the answer lower-cased with whitespace collapsed. A
        task built from `compare_keys` gives exactly v0.4's string. Compare answers with `key` and `match`: two answers
        within a field's tolerance agree although their canonical strings differ."""
        key = self.key(text)
        if key is NO_KEY:
            return INVALID
        if self.sql is not None:
            return self.sql.canonical(key)
        if not self.fields:
            return key["$"]
        return json.dumps({p: jsonable(v) for p, v in key.items()}, sort_keys=True)


def sql_problem(fmt: Any, specs: Sequence[FieldSpec], sql: Any) -> str | None:
    """Why a task of format `fmt` cannot have these fields and `sql` options, or None: a sql task needs its options (a
    fixture, or dialect postgres) and has no fields (its answers agree when their queries return the same rows), and
    only a sql task takes them."""
    if fmt == "sql":
        if specs:
            return "a sql task has no fields: two answers agree when their queries return the same result"
        if not isinstance(sql, sqlcheck.SqlSpec):
            return "a sql task needs sql options: a fixture in RATCHET_FIXTURES_DIR, or dialect 'postgres'"
    elif sql is not None:
        return "only a task with format 'sql' takes sql options"
    return None


def samples_problem(specs: Sequence[FieldSpec], policy: Mapping[str, Any]) -> str | None:
    """Why a task with these fields cannot have this policy, or None: a task with a `similarity` field needs
    `policy.samples` of at least 2 (FR-2.55), because one sample always agrees with itself, so its agreement is 1 and its
    confidence says nothing; and the task must say so itself, so RATCHET_SAMPLES=1 cannot take that away."""
    if not any(f.type == "similarity" for f in specs):
        return None
    n = policy.get("samples")
    if not isinstance(n, int) or isinstance(n, bool) or n < 2:
        return "a task with a similarity field needs policy.samples of at least 2 (one sample always agrees with itself)"
    return None


def policy_problem(key: Any, value: Any) -> str | None:
    """Why `value` cannot be policy `key` (`samples` a whole number from 1 to 16, the rates in [0, 1]), or None. One key
    at a time, so a spec file can name every bad key (`taskspec`)."""
    if key not in POLICY_KEYS:
        return f"unknown key; use {', '.join(POLICY_KEYS)}"
    if key == "samples":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16:
            return "must be a whole number from 1 to 16"
    elif isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return "must be a number from 0 to 1"
    return None


def _policy(policy: Any, fail: Any) -> dict[str, Any]:
    """A task's policy, checked by `policy_problem`; an unknown key is reported first."""
    if not isinstance(policy, Mapping):
        fail("policy must be a mapping")
    out = dict(policy)
    unknown = sorted(str(k) for k in out if k not in POLICY_KEYS)
    if unknown:
        fail(f"unknown policy key {unknown[0]!r}; use {', '.join(POLICY_KEYS)}")
    for key, value in out.items():
        problem = policy_problem(key, value)
        if problem is not None:
            fail(f"policy.{key} {problem}")
    return out


def match_problem(key: Any, value: Any) -> str | None:
    """Why `value` cannot be `match.<key>`, or None."""
    if key not in MATCH_KEYS:
        return f"unknown key; use {', '.join(MATCH_KEYS)}"
    if key == "system_prefix":
        if not isinstance(value, str) or not value or len(value) > MAX_PREFIX:
            return f"must be a non-empty string of at most {MAX_PREFIX} characters"
    elif key == "schema_name":
        if not isinstance(value, str) or not _TOOL_NAME.fullmatch(value):
            return "must be a JSON schema name: 1 to 64 letters, digits, '_' or '-'"
    else:  # tools
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or not value:
            return "must be a non-empty list of tool names"
        if len(value) > MAX_MATCH_TOOLS:
            return f"must name at most {MAX_MATCH_TOOLS} tools"
        if not all(isinstance(t, str) and _TOOL_NAME.fullmatch(t) for t in value):
            return "must hold tool names: 1 to 64 letters, digits, '_' or '-'"
        if len(set(value)) != len(value):
            return "names a tool twice"
    return None


def clients_problem(value: Any) -> str | None:
    """Why `value` cannot be a task's `clients` (a list of API key names, each at most once), or None."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return "must be a list of API key names"
    if len(value) > MAX_CLIENTS:
        return f"must name at most {MAX_CLIENTS} keys"
    for name in value:
        if not isinstance(name, str) or not valid_name(name):
            return f"must hold API key names (letters, digits, '_', '.', '-'), not {name!r}"
    if len(set(value)) != len(value):
        return "names a key twice"
    return None


# What `Task.canonical` returns for an answer that cannot count: it never agrees, and gets no vote in `confidence.score`.
INVALID = "<invalid>"


def _rstrip(text: str, chars: frozenset[str]) -> str:
    """`text` without a trailing run of `chars` and whitespace. Linear, unlike a regex anchored at the end."""
    i = len(text)
    while i and (text[i - 1] in chars or text[i - 1].isspace()):
        i -= 1
    return text[:i]


def _canon(v: Any) -> Any:
    """A JSON value as a task without fields compares it: strings (keys too) NFKC-normalised, case-folded and with
    whitespace collapsed (as v0.4's text rule did, so "x  y" is "x y"), numbers as floats. Raises ValueError when two
    keys become one, and RecursionError when nested too deeply."""
    if isinstance(v, str):
        return " ".join(unicodedata.normalize("NFKC", v).casefold().split())
    if isinstance(v, bool) or v is None or isinstance(v, float):
        return v
    if isinstance(v, int):
        try:
            return float(v)
        except OverflowError:
            return v
    if isinstance(v, list):
        return [_canon(x) for x in v]
    out = {}
    for k, x in v.items():
        nk = _canon(k)
        if nk in out:
            raise ValueError("two keys are the same once normalised")
        out[nk] = _canon(x)
    return out


def _whole_key(clean: str, need_json: bool) -> tuple[Any, str | None]:
    """The key of a task without fields: the text rule (lower-cased, whitespace collapsed) and, when the answer is one
    JSON object or array (bare or in one code fence that is all of it), its canonical JSON (`_canon`). With
    `need_json` (format 'json') an answer that is not JSON is invalid."""
    t = clean
    if t.startswith("```"):
        t = _fence_body(t) or ""
    js = None
    if t[:1] in ("{", "["):
        v = _loads(t)
        if isinstance(v, (dict, list)):
            try:
                js = json.dumps(_canon(v), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            except (ValueError, RecursionError):
                js = None
    if need_json and js is None:
        return NO_KEY, "invalid answer"
    return {"$": " ".join(clean.lower().split()), "$json": js}, None


_TAG = re.compile(r"<(/?)think>", re.IGNORECASE)  # group 1 is "/" for a closing tag
_CLOSE = re.compile(r"</think>", re.IGNORECASE)
_OPEN = re.compile(r"<think>", re.IGNORECASE)
_INFO_STRING = re.compile(r"[A-Za-z0-9_+.-]*")  # the language tag after an opening fence: ```json, ```JSON, ```


def strip_reasoning(text: str | None) -> str:
    """The answer without the model's reasoning. Reasoning models write `<think>...</think>` before the answer; some chat
    templates put the opening tag in the prompt, so the output starts inside the reasoning and has only the closing tag;
    and an answer cut off by the token limit can end inside an unclosed `<think>`. So, in this order: complete blocks
    are removed (tags in any case), everything up to the last remaining `</think>` is dropped, and an opening `<think>`
    still left at the start of the rest (nothing but whitespace before it) is dropped with everything after it. An
    opening tag after some answer text is content, such as a reply that mentions the tag: a model opens its reasoning
    before it answers, not halfway through. What remains is stripped of surrounding whitespace; it is empty when the
    model never got past its reasoning. A free-text answer that itself mentions a lone `</think>` loses the text before
    it, because no string rule can tell that from reasoning the template opened (`clean_answer` keeps a structured
    answer whole). Linear in the length of the text: one pass over the tags, never a regex that rescans from every
    opening tag (thousands of unclosed tags would take seconds)."""
    t = text or ""
    if not _TAG.search(t):  # the common case: no reasoning tags at all
        return t.strip()
    # 1. Complete blocks: an opening tag runs to the next closing tag (opening tags inside it are part of the block).
    kept, pos, start = [], 0, None
    for m in _TAG.finditer(t):
        closing = m.group(1) == "/"
        if start is None and not closing:
            start = m.start()
        elif start is not None and closing:
            kept.append(t[pos:start])
            pos, start = m.end(), None
    kept.append(t[pos:])
    t = "".join(kept)
    # 2. A closing tag left over: the reasoning began before the output did; keep what follows the last one.
    closes = list(_CLOSE.finditer(t))
    if closes:
        t = t[closes[-1].end() :]
    # 3. An opening tag left over at the start has no close: the model was cut off while reasoning.
    m = _OPEN.search(t)
    if m and not t[: m.start()].strip():
        return ""
    return t.strip()


_NOT_JSON = object()  # what `_loads` returns for text that is not JSON


def _loads(text: str) -> Any:
    """The JSON value of `text`, or `_NOT_JSON`. Never raises: RecursionError means nested too deeply for the parser."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return _NOT_JSON


def _fence_body(text: str) -> str | None:
    """The body of a code fence around the whole of `text` (```json ... ```, any language tag or none), or None. The body
    is not checked: two fences, or prose around one, give a body that is not JSON, which the callers refuse."""
    if len(text) < 6 or not (text.startswith("```") and text.endswith("```")):
        return None
    inner = text[3:-3]
    first, newline, rest = inner.partition("\n")
    body = rest if newline and _INFO_STRING.fullmatch(first.strip()) else inner
    return body.strip()


def _unfence(text: str) -> str | None:
    """The body of a code fence that is the whole of `text`, when that body is JSON; otherwise None."""
    body = _fence_body(text)
    return body if body is not None and _loads(body) is not _NOT_JSON else None


def _one_object(text: str) -> tuple[str, dict[str, Any]] | None:
    """`text` without surrounding whitespace, and its value, when it is exactly one JSON object; or the same for the body
    of one code fence that is all of it. Otherwise None."""
    t = text.strip()
    if t.startswith("```"):
        t = _fence_body(t) or ""
    if not t.startswith("{"):  # cheap: most text is not an object, and is never parsed here
        return None
    obj = _loads(t)
    return (t, obj) if isinstance(obj, dict) else None


def _whole_object(text: str | None, stripped: str) -> tuple[str, dict[str, Any]] | None:
    """The first of these that is exactly one JSON object (or one fence around one): the whole answer, what follows its
    first `</think>`, and the answer without its reasoning (`stripped`, `strip_reasoning`). A tag inside such an object
    sits in a string, so it is content: a JSON answer that mentions `<think>` or `</think>` keeps its text, also after
    reasoning. An object inside unclosed reasoning is never one of these."""
    raw = text or ""
    close = _CLOSE.search(raw)
    for candidate in (raw, raw[close.end() :] if close else None, stripped):
        found = _one_object(candidate) if candidate is not None else None
        if found is not None:
            return found
    return None


def clean_answer(task: Task, text: str | None) -> str:
    """An answer as Ratchet compares, stores and serves it: `strip_reasoning`, then, for a structured task, the body of a
    single code fence that wraps the whole answer when that body parses as JSON. A structured answer that is one JSON
    object, before or after its reasoning, is returned as that object (`_whole_object`), so tags quoted in its strings
    survive. A sql task's answer loses a code fence around the whole of it (```sql ... ```) unless the body holds
    another fence. Free-text tasks keep their fences, because a code block is part of the content there. Cleaning twice
    gives the same text."""
    t = strip_reasoning(text)
    if not task.structured:
        if task.format == "sql":
            body = _fence_body(t)
            return body if body is not None and "```" not in body else t
        return t
    found = _whole_object(text, t)
    if found is not None:
        return found[0]
    body = _unfence(t)
    return t if body is None else body


def parse_json(text: str, allow_any: bool = False) -> Any:
    """The JSON object in an answer, tolerating reasoning, code fences and text around it; None when there is none.
    An answer that is one object (`_whole_object`: also inside one fence, or after its reasoning) is taken whole, so tags
    in its strings stay. Otherwise, after the answer without reasoning, it tries the span from the first `{` to the last
    `}`, which is the object after a sentence. Linear in the length of the answer (a regex for fences or that span
    rescanned long runs of spaces or braces from every position, which took seconds), and it never raises.

    With `allow_any`, any JSON value: a list or a scalar is accepted too when it is the whole answer (or one fence), and
    a list after a sentence is found like an object. None then also stands for a JSON null; `Task.key` tells the two
    apart through `_parse_any`."""
    if allow_any:
        v = _parse_any(text)
        return None if v is _NOT_JSON else v
    t = strip_reasoning(text)
    found = _whole_object(text, t)
    if found is not None:
        return found[1]
    obj = _loads(t)
    if obj is _NOT_JSON:
        first, last = t.find("{"), t.rfind("}")
        if first == -1 or last < first:
            return None
        obj = _loads(t[first : last + 1])
    return obj if isinstance(obj, dict) else None


def _parse_any(text: str | None) -> Any:
    """The JSON value of an answer, or `_NOT_JSON`: one object as `parse_json` finds it, else the whole answer without
    reasoning (or the body of one fence that is all of it) as any JSON value, else the span from the first `[` to the
    last `]` or from the first `{` to the last `}`, whichever starts first."""
    t = strip_reasoning(text)
    found = _whole_object(text, t)
    if found is not None:
        return found[1]
    body = _fence_body(t)
    for candidate in (t, body):
        if candidate:
            v = _loads(candidate)
            if v is not _NOT_JSON:
                return v
    spans = [(t.find(a), t.rfind(b)) for a, b in (("[", "]"), ("{", "}"))]
    for first, last in sorted(s for s in spans if s[0] != -1 and s[1] > s[0]):
        v = _loads(t[first : last + 1])
        if isinstance(v, (dict, list)):
            return v
    return _NOT_JSON


_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def valid_name(name: str) -> bool:
    """1 to 64 letters, digits, '_', '.' or '-', matched in full (a trailing newline is not a valid name)."""
    return _NAME.fullmatch(name) is not None


# The tasks defined in code. `reset_builtin` puts REGISTRY back to exactly these.
BUILTIN: Mapping[str, Task] = MappingProxyType(
    {
        "default": Task("default"),
        "ticket-triage": Task(
            "ticket-triage",
            fields=(
                FieldSpec("category", "label", values=vocab.TICKET_CATEGORIES),
                FieldSpec("priority", "label", values=vocab.TICKET_PRIORITIES),
            ),
        ),
        # Scout, the multi-agent research demo (docs/SCOUT.md).
        "source-triage": Task(
            "source-triage",
            fields=(FieldSpec("relevant", "bool"), FieldSpec("source_type", "label", values=vocab.SOURCE_TYPES)),
        ),
        "page-read": Task(
            "page-read",
            fields=(FieldSpec("answers_question", "bool"), FieldSpec("fact_type", "label", values=vocab.FACT_TYPES)),
        ),
        "research-plan": Task("research-plan", pinned=True),
        "research-write": Task("research-write", pinned=True),
    }
)

# The tasks a request can name. Writers build a new dict under _REGISTRY_LOCK and publish it in one assignment, so a
# reader (`get`, on every request) never takes the lock and never sees a half-made registry. Read it as
# `tasks.REGISTRY`, never `from .tasks import REGISTRY` (that keeps the dict of the moment).
REGISTRY: dict[str, Task] = dict(BUILTIN)
_REGISTRY_LOCK = threading.Lock()
MAX_TASKS = 500  # registered tasks of every source, built-in ones included
# The registered tasks a new one may replace, by the new one's source: the tasks file may replace a built-in task (the
# operator's configuration wins); the admin API replaces only its own tasks and auto tasks (it answers 403 for a
# built-in or file task, which the code or the file defines); an auto task replaces only another auto task.
_REPLACES: Mapping[str, frozenset[str]] = MappingProxyType(
    {"file": frozenset({"builtin", "file"}), "api": frozenset({"api", "auto"}), "auto": frozenset({"auto"})}
)
_OWNER = {"builtin": "built in", "file": "defined in RATCHET_TASKS_FILE", "api": "an API task", "auto": "an auto task"}
_LISTED = 20  # registered names an UnknownTask message lists


class TaskConflict(ValueError):
    """A task cannot replace (or remove) the one registered under its name. `existing` is that task's source."""

    def __init__(self, message: str, name: str, existing: str) -> None:
        super().__init__(message)
        self.name = name
        self.existing = existing


class RegistryFull(ValueError):
    """MAX_TASKS tasks are registered already."""


class UnknownTask(ValueError):
    """A request named a task that is not registered, and RATCHET_UNKNOWN_TASKS is `reject`; or (`invalid`) it sent an
    X-Ratchet-Task header that is not a task name, under `reject` or `teacher`. The message lists the registered tasks
    the caller may use (`known`), at most 20 of them."""

    def __init__(self, name: str, known: Iterable[str] = (), invalid: bool = False) -> None:
        names = sorted(known)
        listed = ", ".join(names[:_LISTED]) or "none"
        more = f" and {len(names) - _LISTED} more" if len(names) > _LISTED else ""
        if invalid:  # a header can be long: show its start only
            shown = repr(name) if len(name) <= 40 else repr(name[:37] + "...")
            head = f"{shown} is not a task name (1 to 64 letters, digits, '_', '.' or '-')"
        else:
            head = f'unknown task "{name}"'
        super().__init__(f"{head}; registered: {listed}{more}")
        self.name = name


class TaskForbidden(PermissionError):
    """The task's `clients` do not name the API key that sent the request (or the request had no key)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"task {name!r} is not allowed for this key")
        self.name = name


def _put(registry: dict[str, Task], task: Task, source: str | None) -> Task | None:
    """`register`'s rules applied to `registry`, a dict that no reader sees yet: puts `task` in it and returns the task
    it replaced, if any."""
    if source is not None and source != task.source:
        task = replace(task, source=source)
    allowed = _REPLACES.get(task.source)
    if allowed is None:
        raise ValueError(f"task {task.name!r}: a {task.source} task cannot be registered; use file, api or auto")
    if not isinstance(task.name, str) or not valid_name(task.name):
        raise ValueError(f"task {task.name!r}: a name is 1 to 64 letters, digits, '_', '.' or '-'")
    current = registry.get(task.name)
    if current is not None and current.source not in allowed:
        article = "an" if task.source[0] in "aeiou" else "a"
        message = f"task {task.name!r} is {_OWNER[current.source]}; {article} {task.source} task cannot replace it"
        raise TaskConflict(message, task.name, current.source)
    if current is None and len(registry) >= MAX_TASKS:
        raise RegistryFull(f"the task registry is full ({MAX_TASKS} tasks); delete a task first")
    registry[task.name] = task
    return current


def register(task: Task, source: str | None = None) -> Task | None:
    """Add `task` to the registry, or replace the task registered under its name; returns the task it replaced, if any.
    `source` (default `task.source`) is `file`, `api` or `auto`: built-in tasks come only from `reset_builtin`, and
    adhoc tasks are never registered. Raises TaskConflict when the registered task may not be replaced by one from this
    source (`_REPLACES`), RegistryFull for a new name past MAX_TASKS, and ValueError for a name a request could not
    send."""
    global REGISTRY
    with _REGISTRY_LOCK:
        new = dict(REGISTRY)
        current = _put(new, task, source)
        REGISTRY = new
    return current


def builtin_registry() -> dict[str, Task]:
    """A new, unpublished registry holding the built-in tasks: `add` tasks to it, then publish it with `replace_all`."""
    return dict(BUILTIN)


def add(registry: dict[str, Task], task: Task, source: str | None = None) -> Task | None:
    """`register` into `registry` (from `builtin_registry`) instead of the published one, with the same rules and
    errors. Readers see none of it until `replace_all`."""
    return _put(registry, task, source)


def replace_all(registry: Mapping[str, Task]) -> None:
    """Publish a whole registry in one assignment (`taskspec.install`), so a request sees the old registry or the new
    one, never one without the tasks file's tasks. It must hold every built-in name (an invalid name gets 'default')."""
    global REGISTRY
    missing = [name for name in BUILTIN if name not in registry]
    if missing:
        raise ValueError(f"a registry must hold every built-in task; missing {', '.join(missing)}")
    new = dict(registry)
    with _REGISTRY_LOCK:
        REGISTRY = new


def unregister(name: str) -> Task | None:
    """Remove an API or auto task and return it; None when no task has that name. A built-in or file task cannot be
    removed (TaskConflict): the code or the tasks file defines it."""
    global REGISTRY
    with _REGISTRY_LOCK:
        current = REGISTRY.get(name)
        if current is None:
            return None
        if current.source not in ("api", "auto"):
            raise TaskConflict(f"task {name!r} is {_OWNER[current.source]}; it cannot be removed", name, current.source)
        new = dict(REGISTRY)
        del new[name]
        REGISTRY = new
    return current


def restore(name: str, previous: Task | None) -> None:
    """Put back what was registered under `name` before a write that could not be completed (None: nothing)."""
    global REGISTRY
    with _REGISTRY_LOCK:
        new = dict(REGISTRY)
        if previous is None:
            new.pop(name, None)
        else:
            new[name] = previous
        REGISTRY = new


def reset_builtin() -> None:
    """Make the registry the built-in tasks again (tests; `taskspec.install` builds its registry from
    `builtin_registry` and publishes it whole instead, so requests never see the built-in tasks alone)."""
    global REGISTRY
    with _REGISTRY_LOCK:
        REGISTRY = dict(BUILTIN)


def lookup(name: Any) -> Task | None:
    """The registered task called `name`, or None. Never makes one up (see `get`)."""
    return REGISTRY.get(name) if isinstance(name, str) else None


def registered() -> list[Task]:
    """Every registered task: the built-in ones first, then the file's, then the API's, as they were registered."""
    return list(REGISTRY.values())


def names_for(client: str | None) -> list[str]:
    """The names of the registered tasks the API key `client` may use (`Task.allows`), sorted."""
    return sorted(name for name, task in REGISTRY.items() if task.allows(client))


def load_file(path: str | Path, known_clients: Iterable[str] | None = None, fixtures_dir: str | Path | None = None) -> list[Task]:
    """The tasks of a tasks file, JSON or (with PyYAML) YAML: `taskspec.load_file`. Raises `taskspec.SpecError` (a
    ValueError) listing every error with its path. Registers nothing (`taskspec.install` does)."""
    from .taskspec import load_file as _load_file  # taskspec imports this module

    return _load_file(path, known_clients, fixtures_dir)


def get(name: str | None) -> Task:
    """The registered task called `name`. An unknown valid name gets a task without fields (whole answers compared) that
    is NOT registered, so arbitrary client headers cannot grow the registry (`source` 'adhoc', never pinned); what a
    request for it gets is RATCHET_UNKNOWN_TASKS's call (`router.Ratchet.task_for`). Invalid names fall back to
    'default'. Night School uses this too, so a stored example always has a task to compare with."""
    if not name or not valid_name(name):
        return REGISTRY["default"]
    return REGISTRY.get(name) or Task(name, source="adhoc")
