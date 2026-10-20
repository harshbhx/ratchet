"""Task definitions: how to compare two answers for the same task, and how an answer is cleaned.

A task tells Ratchet which parts of an answer must match for the student to count
as agreeing with the teacher. Structured fields (labels) are compared exactly;
free-text fields are ignored by default (the judge can score them separately).

Every answer is cleaned the same way before it is compared, stored or served (`clean_answer`): reasoning is removed
(`strip_reasoning`), and a structured task's answer loses a code fence wrapped around the whole of it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    name: str
    compare_keys: list[str] = field(default_factory=list)  # empty = compare whole normalized text
    # Pinned tasks always go to the teacher and are never stored as training examples:
    # open-ended work (planning, writing) is where the big model earns its price.
    pinned: bool = False

    @property
    def structured(self) -> bool:
        """Answers are JSON compared on label fields, so an answer that does not parse is invalid."""
        return bool(self.compare_keys)

    def canonical(self, text: str) -> str:
        """The part of an answer that must match, or `INVALID`: an empty answer (after `clean_answer`) for any task, and
        for a structured task one that is not a JSON object."""
        clean = clean_answer(self, text)
        if not clean:
            return INVALID
        if self.compare_keys:
            obj = parse_json(clean)
            if obj is None:
                return INVALID
            return json.dumps({k: _norm(obj.get(k)) for k in self.compare_keys}, sort_keys=True)
        return " ".join(clean.lower().split())

    def agree(self, a: str, b: str) -> bool:
        ca, cb = self.canonical(a), self.canonical(b)
        return ca != INVALID and ca == cb


# What `Task.canonical` returns for an answer that cannot count: it never agrees, and gets no vote in `confidence.score`.
INVALID = "<invalid>"


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


_TAG = re.compile(r"<(/?)think>", re.IGNORECASE)  # group 1 is "/" for a closing tag
_CLOSE = re.compile(r"</think>", re.IGNORECASE)
_OPEN = re.compile(r"<think>", re.IGNORECASE)
_INFO_STRING = re.compile(r"[A-Za-z0-9_+.-]*")  # the language tag after an opening fence: ```json, ```JSON, ```


def strip_reasoning(text: str | None) -> str:
    """The answer without the model's reasoning. Reasoning models write `<think>...</think>` before the answer; some chat
    templates put the opening tag in the prompt, so the output starts inside the reasoning and has only the closing tag;
    and an answer cut off by the token limit can end inside an unclosed `<think>`. So, in this order: complete blocks
    are removed (tags in any case), everything up to the last remaining `</think>` is dropped, and an opening `<think>`
    still left is dropped with everything after it. What remains is stripped of surrounding whitespace; it is empty
    when the model never got past its reasoning. Linear in the length of the text: one pass over the tags, never a
    regex that rescans from every opening tag (thousands of unclosed tags would take seconds)."""
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
    # 3. An opening tag left over has no close: the answer was cut off inside it.
    m = _OPEN.search(t)
    if m:
        t = t[: m.start()]
    return t.strip()


def _unfence(text: str) -> str | None:
    """The body of a code fence that is the whole of `text` (```json ... ```, any language tag or none), when that body
    is JSON; otherwise None. Two fences, or prose around one, are not unwrapped."""
    if len(text) < 6 or not (text.startswith("```") and text.endswith("```")):
        return None
    inner = text[3:-3]
    first, newline, rest = inner.partition("\n")
    body = rest if newline and _INFO_STRING.fullmatch(first.strip()) else inner
    body = body.strip()
    try:
        json.loads(body)
    except (ValueError, RecursionError):  # RecursionError: nested too deeply for the parser
        return None
    return body


def clean_answer(task: Task, text: str | None) -> str:
    """An answer as Ratchet compares, stores and serves it: `strip_reasoning`, then, for a structured task, the body of a
    single code fence that wraps the whole answer when that body parses as JSON. Free-text tasks keep their fences,
    because a code block is part of the content there."""
    t = strip_reasoning(text)
    if task.structured:
        body = _unfence(t)
        if body is not None:
            return body
    return t


def parse_json(text: str) -> dict[str, Any] | None:
    """The JSON object in an answer, tolerating reasoning, code fences and text around it; None when there is none.
    After the whole answer, it tries the span from the first `{` to the last `}`, which is the object inside a fence or
    after a sentence. Linear in the length of the answer (a regex for fences or that span rescanned long runs of spaces
    or braces from every position, which took seconds), and it never raises."""
    t = strip_reasoning(text)
    try:
        obj = json.loads(t)
    except (ValueError, TypeError, RecursionError):  # RecursionError: nested too deeply for the parser
        first, last = t.find("{"), t.rfind("}")
        if first == -1 or last < first:
            return None
        try:
            obj = json.loads(t[first : last + 1])
        except (ValueError, RecursionError):
            return None
    return obj if isinstance(obj, dict) else None


REGISTRY: dict[str, Task] = {
    "default": Task("default"),
    "ticket-triage": Task("ticket-triage", compare_keys=["category", "priority"]),
    # Scout, the multi-agent research demo (docs/SCOUT.md).
    "source-triage": Task("source-triage", compare_keys=["relevant", "source_type"]),
    "page-read": Task("page-read", compare_keys=["answers_question", "fact_type"]),
    "research-plan": Task("research-plan", pinned=True),
    "research-write": Task("research-write", pinned=True),
}


_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def valid_name(name: str) -> bool:
    """1 to 64 letters, digits, '_', '.' or '-', matched in full (a trailing newline is not a valid name)."""
    return _NAME.fullmatch(name) is not None


def get(name: str | None) -> Task:
    """Known tasks come from REGISTRY. Unknown names get a whole-text comparison task that is
    NOT stored, so arbitrary client headers cannot grow the registry. Invalid names fall back
    to 'default'."""
    if not name or not valid_name(name):
        return REGISTRY["default"]
    return REGISTRY.get(name) or Task(name)
