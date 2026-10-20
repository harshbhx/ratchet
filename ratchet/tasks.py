"""Task definitions: when two answers to the same task agree, which answers are valid, and how an answer is cleaned.

A task tells Ratchet which parts of an answer must match for the student to count as agreeing with the teacher: its
`fields` (FR-2.14, ADR 0021), each a path and a typed comparator from `compare.py` (a label over a vocabulary, a number
with a tolerance, a date in any common format, a set, records, ...). Free-text fields that are not listed are ignored
(the judge can score them separately). A field is required unless it says otherwise, so an answer without it is invalid:
it gets no vote in the confidence score and is never stored as a training example (INV-16). A task without fields
compares whole answers: canonical JSON when both are JSON, else the text, lower-cased with whitespace collapsed.

Every answer is cleaned the same way before it is compared, stored or served (`clean_answer`): reasoning is removed
(`strip_reasoning`), and a JSON task's answer loses a code fence wrapped around the whole of it. A JSON answer that is
already one JSON object is kept as it is, so a reply that mentions a `<think>` tag keeps its text.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from . import vocab
from .compare import INVALID as NO_KEY
from .compare import MAX_FIELDS, MAX_RECORDS, FieldSpec, Record, jsonable

# Answer formats. `json`: the answer is JSON and fields are paths into it. `text`: the answer is plain text and its one
# field is '$', the text without surrounding whitespace and trailing `.!,;:`. `tool_calls` and `sql` are reserved for
# later releases.
FORMATS = ("json", "text", "tool_calls", "sql")
RESERVED_FORMATS = frozenset({"tool_calls", "sql"})
# Where a task was defined: in this file, a tasks file, the admin API, derived from a request, or made up for an unknown
# task name (`get`, never stored in REGISTRY).
SOURCES = ("builtin", "file", "api", "auto", "adhoc")
TOOLS = ("teacher", "distill")  # who answers tool calls for this task (distill: opt-in for a later release)
# A task's own release policy; later releases read it (samples, audit rate and precision targets per task).
POLICY_KEYS = ("samples", "audit_rate", "target_precision", "min_precision")
_TEXT_TRAILING = frozenset(".!,;:")


@dataclass(frozen=True)
class Task:
    """A task: its name, whether it is pinned to the teacher, and its spec (`format`, `fields`, `policy`, `routing`,
    `store`, `tools`). `compare_keys` is the v0.4 shortcut: each key becomes an optional `exact` field, and such a task
    compares, validates and canonicalises answers exactly as v0.4 did. `spec_hash` is derived from the spec (not from
    the name or `source`), so evaluations can tell when a task's rules changed; it cannot be set, so it never drifts
    from the rules (`dataclasses.replace` computes it again). Immutable: `policy` and `routing` are read-only mappings.
    Invalid specs raise ValueError."""

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
        if keys and specs:
            fail("give compare_keys or fields, not both")
        if keys:
            if not all(isinstance(k, str) for k in keys):
                fail("compare_keys must be strings")
            specs = tuple(FieldSpec(k, "exact", required=False) for k in dict.fromkeys(keys))
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

        fmt = self.format if self.format is not None else ("json" if specs else "text")
        if fmt in RESERVED_FORMATS:
            fail(f"format {fmt!r} is reserved for a later release")
        if fmt not in FORMATS:
            fail(f"format must be one of {', '.join(f for f in FORMATS if f not in RESERVED_FORMATS)}")
        if fmt == "text" and any(p != "$" for p in paths):
            fail("a text task has one field, '$' (the whole answer)")
        put("format", fmt)

        if self.tools not in TOOLS:
            fail(f"tools must be one of {', '.join(TOOLS)}")
        if self.source not in SOURCES:
            fail(f"source must be one of {', '.join(SOURCES)}")
        put("policy", MappingProxyType(_policy(self.policy, fail)))
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
        covers."""
        return {
            "format": self.format,
            "fields": [f.spec() for f in self.fields],
            "pinned": self.pinned,
            "store": self.store,
            "tools": self.tools,
            "policy": dict(self.policy),
            "routing": dict(self.routing),
        }

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
        """What of an answer must agree: each field's normalised value by path, or `compare.INVALID` (`tasks.NO_KEY`) for
        an answer that cannot count: empty after `clean_answer`, not parseable in the task's format, or with a required
        field missing (or null when it is not nullable) or a field that does not normalise (a label outside its
        vocabulary). Compute it once per answer and compare keys with `match`."""
        return self._key(text)[0]

    def check(self, text: str | None) -> str | None:
        """Why an answer is not valid under the task (`empty answer`, `invalid answer`, `invalid field <path>`), or None
        when it is. The reason names a path of the spec, never the answer's text, so it may go into an event (INV-12)."""
        return self._key(text)[1]

    def valid(self, text: str | None) -> bool:
        """The answer parses, every required field is present (null only when nullable), and every field normalises."""
        return self.key(text) is not NO_KEY

    def match(self, ka: Any, kb: Any) -> bool:
        """Whether two keys (`key`) agree: every field matches under its comparator. A task without fields compares
        canonical JSON when both answers are JSON objects or arrays, else their text. NO_KEY never agrees."""
        if ka is NO_KEY or kb is NO_KEY:
            return False
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
        if not self.fields:
            return key["$"]
        return json.dumps({p: jsonable(v) for p, v in key.items()}, sort_keys=True)


def _policy(policy: Any, fail: Any) -> dict[str, Any]:
    """A task's policy, checked: only `POLICY_KEYS`, `samples` a whole number from 1 to 16, the rates in [0, 1]."""
    if not isinstance(policy, Mapping):
        fail("policy must be a mapping")
    out = dict(policy)
    unknown = sorted(set(out) - set(POLICY_KEYS))
    if unknown:
        fail(f"unknown policy key {unknown[0]!r}; use {', '.join(POLICY_KEYS)}")
    if "samples" in out:
        n = out["samples"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 16:
            fail("policy.samples must be a whole number from 1 to 16")
    for name in ("audit_rate", "target_precision", "min_precision"):
        if name in out:
            v = out[name]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                fail(f"policy.{name} must be a number from 0 to 1")
    return out


# What `Task.canonical` returns for an answer that cannot count: it never agrees, and gets no vote in `confidence.score`.
INVALID = "<invalid>"


def _rstrip(text: str, chars: frozenset[str]) -> str:
    """`text` without a trailing run of `chars` and whitespace. Linear, unlike a regex anchored at the end."""
    i = len(text)
    while i and (text[i - 1] in chars or text[i - 1].isspace()):
        i -= 1
    return text[:i]


def _canon(v: Any) -> Any:
    """A JSON value as a task without fields compares it: strings (keys too) NFKC-normalised, case-folded and stripped,
    numbers as floats. Raises ValueError when two keys become one, and RecursionError when nested too deeply."""
    if isinstance(v, str):
        return unicodedata.normalize("NFKC", v).casefold().strip()
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
    survive. Free-text tasks keep their fences, because a code block is part of the content there. Cleaning twice gives
    the same text."""
    t = strip_reasoning(text)
    if not task.structured:
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


REGISTRY: dict[str, Task] = {
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


_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def valid_name(name: str) -> bool:
    """1 to 64 letters, digits, '_', '.' or '-', matched in full (a trailing newline is not a valid name)."""
    return _NAME.fullmatch(name) is not None


def get(name: str | None) -> Task:
    """Known tasks come from REGISTRY. Unknown names get a task without fields (whole answers compared) that is NOT
    stored, so arbitrary client headers cannot grow the registry (`source` 'adhoc'). Invalid names fall back to
    'default'."""
    if not name or not valid_name(name):
        return REGISTRY["default"]
    return REGISTRY.get(name) or Task(name, source="adhoc")
