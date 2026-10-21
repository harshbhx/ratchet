"""Risk-aware routing (FR-2.50, ADR 0049): a task's `routing` block, parsed once when the task is defined.

    "routing": {"student_max_input_tokens": 1800,
                "escalate_when": [{"field": "flagged", "equals": true, "action": "teacher"},
                                  {"field": "severity", "in": ["high"], "min_confidence": 0.95}],
                "critical": [{"field": "flagged", "equals": true, "max_miss_rate": 0.1}]}

- Eligibility, before the student is called: a request whose messages hold more than `student_max_input_tokens`
  (characters / 4, the estimate `providers.approx_tokens` makes), or a content part the student does not read
  (`student_content_types`; the student reads text only), goes to the teacher with route `ineligible:input_tokens` or
  `ineligible:content_type`. An over-length request's answer is stored only with `store_ineligible` (train.py cuts
  every example at `--max-seq`); one with a part the student cannot read is never stored (ADR 0012).
- `escalate_when`, once the student is confident enough to serve: the first rule whose condition holds for the
  student's majority answer sends the request to the teacher (`action: teacher`), or does so unless the confidence
  reaches the rule's `min_confidence`. Rules catch costly positive predictions; costly misses are the critical gate's
  and the audits' job, because a rule never sees the answer the student should have given.
- `critical`: classes whose misses are costly. The golden eval counts, on the held-out half, the rows whose teacher
  reference is in the class but whose served answer is not (`Routing.misses`), and the release gate keeps the task on
  the student only when that miss rate is shown to be at most `max_miss_rate` (`ReleaseGate.critical_failure`).

A condition names a declared scalar field and holds when the field's comparator matches one of its values, so `equals:
"Billing"` holds for `billing` and a number condition keeps the field's tolerance. Values are normalised when the task
is defined, and a value the field refuses (a label outside its vocabulary) is a spec error, so the request path only
compares normalised keys the cascade computed already. Standard library only, no I/O.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NamedTuple

from .compare import INVALID, SCALAR_TYPES, FieldSpec
from .stats import wilson

KEYS = ("student_max_input_tokens", "student_content_types", "store_ineligible", "escalate_when", "critical")
# OpenAI's content part types. The student reads text only; the others are reserved for a student that reads them, so
# today a request with such a part (which the gateway admits only when RATCHET_TEACHER_MODALITIES lists it) always goes
# to the teacher and is never stored.
CONTENT_TYPES = ("text", "image_url", "input_audio", "file")
STUDENT_CONTENT_TYPES = frozenset({"text"})
_TEXT_PARTS = frozenset({"text", "refusal"})  # parts that are text, for counting characters
INELIGIBLE_REASONS = ("content_type", "input_tokens")
CHARS_PER_TOKEN = 4  # as `providers.approx_tokens` counts
MAX_INPUT_TOKENS = 10_000_000
MAX_RULES = 32  # escalate_when rules, and critical classes, per task
MAX_CONDITION_VALUES = 256  # values of one `in`


@dataclass(frozen=True)
class Condition:
    """`field` equals one of `values` (normalised by the field, compared with its comparator). `written` is the
    condition as the spec wrote it ({field, equals} or {field, in}), for eval records and messages."""

    field: FieldSpec
    values: tuple[Any, ...]
    written: Mapping[str, Any]

    def holds(self, key: Any) -> bool:
        """Whether an answer with this key (`Task.key`: normalised values by path) is in the class. An invalid answer
        never is."""
        if key is INVALID or not isinstance(key, Mapping):
            return False
        value = key.get(self.field.path, INVALID)
        return any(self.field.match(value, w) for w in self.values)

    def as_json(self) -> dict[str, Any]:
        """The condition as the spec wrote it, as plain JSON."""
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.written.items()}

    def label(self) -> str:
        """`flagged = true`, `category in ["billing", "refund"]`: for events and gate reasons. Spec values only, never an
        answer (INV-12)."""
        if "in" in self.written:
            return f"{self.field.path} in {json.dumps(list(self.written['in']), ensure_ascii=False)}"
        return f"{self.field.path} = {json.dumps(self.written.get('equals'), ensure_ascii=False)}"


@dataclass(frozen=True)
class Rule:
    """An `escalate_when` rule: an answer in its class goes to the teacher (`min_confidence` None, `action: teacher`),
    or does unless the student's confidence is at least `min_confidence`."""

    condition: Condition
    min_confidence: float | None = None

    def escalates(self, key: Any, confidence: float) -> bool:
        return self.condition.holds(key) and (self.min_confidence is None or confidence < self.min_confidence)


@dataclass(frozen=True)
class Critical:
    """A `critical` class: the student may miss at most `max_miss_rate` of the requests whose right answer is in it."""

    condition: Condition
    max_miss_rate: float


class CriticalRow(NamedTuple):
    """One held-out golden row, as `Routing.misses` needs it: the student's confidence, the confidence the rules ask of
    its answer (`Routing.floor`), and per critical class whether the teacher's reference and the student's answer are
    in it."""

    confidence: float
    floor: float
    reference: tuple[bool, ...]
    answer: tuple[bool, ...]


def _text_part(kind: Any, role: Any) -> bool:
    """Whether a content part is text the student reads: a `text` part, or an assistant's `refusal` part. The gateway
    joins such parts into one string (`openai_compat.normalize_messages`) and keeps a content list only when a part of
    another type is in it, so every list it passes on has a part this refuses."""
    return kind == "text" or (kind == "refusal" and role == "assistant")


def input_chars(messages: Iterable[Any]) -> int:
    """Characters of every string a request forwards in its messages (content and text parts, `name`, `refusal`,
    `tool_call_id`, tool call ids, names and arguments), as the gateway's input limit counts them. Never raises: an
    internal caller's messages may be any shape."""
    n = 0
    for m in messages:
        if not isinstance(m, Mapping):
            continue
        content = m.get("content")
        if isinstance(content, str):
            n += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, Mapping) and part.get("type") in _TEXT_PARTS:
                    text = part.get(part["type"])
                    n += len(text) if isinstance(text, str) else 0
        for k in ("name", "refusal", "tool_call_id"):
            v = m.get(k)
            n += len(v) if isinstance(v, str) else 0
        calls = m.get("tool_calls")
        for call in calls if isinstance(calls, list) else ():
            if isinstance(call, Mapping):
                n += len(call["id"]) if isinstance(call.get("id"), str) else 0
                n += _function_chars(call.get("function"))
        n += _function_chars(m.get("function_call"))
    return n


def _function_chars(fn: Any) -> int:
    if not isinstance(fn, Mapping):
        return 0
    return sum(len(fn[k]) for k in ("name", "arguments") if isinstance(fn.get(k), str))


def input_tokens(messages: Iterable[Any]) -> int:
    """`input_chars` / 4, at least 1: the estimate `student_max_input_tokens` is compared with."""
    return max(1, input_chars(messages) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Routing:
    """A task's parsed `routing` block (`parse`). The default routes like a task without one: every text request is
    eligible, there are no rules and no critical classes."""

    max_input_tokens: int | None = None
    content_types: frozenset[str] = STUDENT_CONTENT_TYPES
    store_ineligible: bool = False
    rules: tuple[Rule, ...] = ()
    critical: tuple[Critical, ...] = ()

    def ineligible(self, messages: Sequence[Any]) -> str | None:
        """Why the student must not see this request (one of INELIGIBLE_REASONS), or None: a content part it does not
        read (`content_type`, checked first, because such a request is never stored), or more input than
        `max_input_tokens` (`input_tokens`). The content parts are read in one pass over the messages; the characters
        are counted only when the task sets a limit."""
        for m in messages:
            content = m.get("content") if isinstance(m, Mapping) else None
            if isinstance(content, list):
                for part in content:
                    kind = part.get("type") if isinstance(part, Mapping) else None
                    if not _text_part(kind, m.get("role")) and kind not in self.content_types:
                        return "content_type"
        if self.max_input_tokens is not None and input_tokens(messages) > self.max_input_tokens:
            return "input_tokens"
        return None

    def stores(self, reason: str) -> bool:
        """Whether a teacher answer to a request ineligible for `reason` may become a training example: an over-length
        one with `store_ineligible`, never one with a part the student cannot read."""
        return reason == "input_tokens" and self.store_ineligible

    def escalation(self, key: Any, confidence: float) -> int | None:
        """The index of the first `escalate_when` rule that sends an answer with this key and confidence to the teacher,
        or None. Comparisons on a normalised key only."""
        for i, rule in enumerate(self.rules):
            if rule.escalates(key, confidence):
                return i
        return None

    def floor(self, key: Any) -> float:
        """The confidence the rules ask of an answer with this key before it is served: infinite when an `action:
        teacher` rule holds, else the highest `min_confidence` of the rules that hold, else 0. `confidence >= floor` is
        exactly `escalation(key, confidence) is None`."""
        out = 0.0
        for rule in self.rules:
            if rule.condition.holds(key):
                out = math.inf if rule.min_confidence is None else max(out, rule.min_confidence)
                if out == math.inf:
                    break
        return out

    def critical_row(self, reference_key: Any, answer_key: Any, confidence: float) -> CriticalRow:
        """One held-out row for `misses`. An invalid answer (or none: the endpoint refused the row) is never served."""
        return CriticalRow(
            confidence,
            math.inf if answer_key is INVALID else self.floor(answer_key),
            tuple(c.condition.holds(reference_key) for c in self.critical),
            tuple(c.condition.holds(answer_key) for c in self.critical),
        )

    def misses(self, rows: Iterable[CriticalRow], threshold: float) -> list[dict[str, Any]]:
        """Per critical class, on the held-out rows: how many have a teacher reference in the class (`n`), how many of
        those the student would serve with an answer outside it (`misses`: confident at `threshold`, and no rule sends
        it to the teacher), the miss rate (None without such rows) and its 95% Wilson upper bound (1.0 without)."""
        rows = list(rows)
        out = []
        for j, c in enumerate(self.critical):
            n = misses = 0
            for r in rows:
                if r.reference[j]:
                    n += 1
                    misses += int(r.confidence >= threshold and r.confidence >= r.floor and not r.answer[j])
            out.append(
                {
                    **c.condition.as_json(),
                    "max_miss_rate": c.max_miss_rate,
                    "n": n,
                    "misses": misses,
                    "miss_rate": misses / n if n else None,
                    "upper_bound": wilson(misses, n)[1],
                }
            )
        return out


DEFAULT = Routing()


# ---------------------------------------------------------------------------------------------------------------------
# Parsing


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, bool)) or _number(v)


def _field_names(fields: Sequence[FieldSpec]) -> str:
    return ", ".join(f.path for f in fields) or "none"


def _condition(
    raw: Any, at: str, fields: Sequence[FieldSpec], check_fields: bool, extra: tuple[str, ...], errs: list[str]
) -> Condition | None:
    """{field, equals | in, ...extra} as a Condition, or None after adding its errors to `errs`."""
    keys = ("field", "equals", "in", *extra)
    if not isinstance(raw, Mapping):
        errs.append(f"{at}: must be an object with a field and equals or in")
        return None
    before = len(errs)
    for k in raw:
        if k not in keys:
            errs.append(f"{at}.{k}: unknown key; use {', '.join(keys)}")
    name = raw.get("field")
    spec: FieldSpec | None = None
    if "field" not in raw:
        errs.append(f"{at}.field: missing; name one of the task's fields ({_field_names(fields)})")
    elif not isinstance(name, str):
        errs.append(f"{at}.field: must be the path of one of the task's fields")
    elif check_fields:
        spec = next((f for f in fields if f.path == name), None)
        if spec is None:
            errs.append(f"{at}.field: {name!r} is not a field of this task; its fields: {_field_names(fields)}")
        elif spec.type not in SCALAR_TYPES:
            types = ", ".join(sorted(SCALAR_TYPES))
            errs.append(f"{at}.field: {name!r} is a {spec.type} field; a condition compares a field of type {types}")
            spec = None
    has_eq, has_in = "equals" in raw, "in" in raw
    values: list[tuple[str, Any]] = []
    if has_eq and has_in:
        errs.append(f"{at}: give equals or in, not both")
    elif not has_eq and not has_in:
        errs.append(f"{at}: missing equals or in")
    elif has_eq:
        if not _scalar(raw["equals"]):
            errs.append(f"{at}.equals: must be a string, number, boolean or null")
        else:
            values.append((f"{at}.equals", raw["equals"]))
    else:
        items = raw["in"]
        if not isinstance(items, list) or not items:
            errs.append(f"{at}.in: must be a non-empty list of values")
        elif len(items) > MAX_CONDITION_VALUES:
            errs.append(f"{at}.in: at most {MAX_CONDITION_VALUES} values")
        elif not all(_scalar(v) for v in items):
            errs.append(f"{at}.in: must hold strings, numbers, booleans or null")
        else:
            values.extend((f"{at}.in[{i}]", v) for i, v in enumerate(items))
    normal = []
    if spec is not None:
        for where, v in values:
            nv = spec.normalize(v)
            if nv is INVALID:
                errs.append(f"{where}: {v!r} is not a value of field {spec.path!r} ({spec.type})")
            else:
                normal.append(nv)
    if len(errs) > before or spec is None:
        return None
    written = {"field": name, "in": tuple(raw["in"])} if has_in else {"field": name, "equals": raw["equals"]}
    return Condition(spec, tuple(normal), MappingProxyType(written))


def _rule(raw: Any, at: str, fields: Sequence[FieldSpec], check_fields: bool, errs: list[str]) -> Rule | None:
    before = len(errs)
    cond = _condition(raw, at, fields, check_fields, ("action", "min_confidence"), errs)
    if not isinstance(raw, Mapping):
        return None
    has_action, has_min = "action" in raw, "min_confidence" in raw
    if has_action and has_min:
        errs.append(f"{at}: give action or min_confidence, not both")
    elif not has_action and not has_min:
        errs.append(f"{at}: missing action ('teacher') or min_confidence")
    elif has_action and raw["action"] != "teacher":
        errs.append(f"{at}.action: must be 'teacher'")
    elif has_min and (not _number(raw["min_confidence"]) or not 0 <= raw["min_confidence"] <= 1):
        errs.append(f"{at}.min_confidence: must be a number from 0 to 1")
    if cond is None or len(errs) > before:
        return None
    return Rule(cond, float(raw["min_confidence"]) if has_min else None)


def _critical(raw: Any, at: str, fields: Sequence[FieldSpec], check_fields: bool, errs: list[str]) -> Critical | None:
    before = len(errs)
    cond = _condition(raw, at, fields, check_fields, ("max_miss_rate",), errs)
    if not isinstance(raw, Mapping):
        return None
    rate = raw.get("max_miss_rate")
    if "max_miss_rate" not in raw:
        errs.append(f"{at}.max_miss_rate: missing; the share of the class the student may miss, more than 0 and below 1")
    elif not _number(rate) or not 0 < rate < 1:
        errs.append(f"{at}.max_miss_rate: must be a number more than 0 and below 1")
    if cond is None or len(errs) > before:
        return None
    return Critical(cond, float(rate))


def _items(raw: Any, at: str, errs: list[str]) -> list[Any]:
    if not isinstance(raw, list):
        errs.append(f"{at}: must be a list")
        return []
    if len(raw) > MAX_RULES:
        errs.append(f"{at}: at most {MAX_RULES} entries")
        return []
    return raw


def parse(
    raw: Mapping[str, Any] | None, fields: Sequence[FieldSpec] = (), check_fields: bool = True
) -> tuple[Routing, list[str]]:
    """A `routing` block as a Routing, and every problem with it as `<path>: <message>` (paths relative to the block;
    `taskspec` and `Task` prefix them). `fields` are the task's fields, which conditions must name; with `check_fields`
    False (the fields themselves have errors) the names and values are not checked against them. With problems, the
    Routing returned is the default."""
    errs: list[str] = []
    if not raw:
        return DEFAULT, errs
    if not isinstance(raw, Mapping):
        return DEFAULT, ["must be an object"]
    for key in raw:
        if key not in KEYS:
            hint = " (a task's audit rate is policy.audit_rate)" if key == "audit_rate" else ""
            errs.append(f"{key}: unknown key; use {', '.join(KEYS)}{hint}")
    limit = raw.get("student_max_input_tokens")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_INPUT_TOKENS):
        errs.append(f"student_max_input_tokens: must be a whole number from 1 to {MAX_INPUT_TOKENS}")
    kinds = raw.get("student_content_types", ["text"])
    if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
        errs.append("student_content_types: must be a list of content part types")
    else:
        for i, kind in enumerate(kinds):
            if kind not in CONTENT_TYPES:
                errs.append(f"student_content_types[{i}]: {kind!r} is not a content part type; use {', '.join(CONTENT_TYPES)}")
            elif kind not in STUDENT_CONTENT_TYPES:
                errs.append(f"student_content_types[{i}]: {kind!r} is reserved for a later release; the student reads text only")
        if "text" not in kinds:
            errs.append("student_content_types: must include 'text'")
        if len(set(kinds)) != len(kinds):
            errs.append("student_content_types: names a type twice")
    store = raw.get("store_ineligible", False)
    if not isinstance(store, bool):
        errs.append("store_ineligible: must be true or false")
    rules = [
        _rule(r, f"escalate_when[{i}]", fields, check_fields, errs)
        for i, r in enumerate(_items(raw.get("escalate_when", []), "escalate_when", errs))
    ]
    critical = [
        _critical(c, f"critical[{i}]", fields, check_fields, errs)
        for i, c in enumerate(_items(raw.get("critical", []), "critical", errs))
    ]
    if errs or not check_fields:
        return DEFAULT, errs
    return (
        Routing(
            max_input_tokens=limit,
            content_types=frozenset(kinds),
            store_ineligible=store,
            rules=tuple(r for r in rules if r is not None),
            critical=tuple(c for c in critical if c is not None),
        ),
        errs,
    )
