"""Task definitions: how to compare two answers for the same task.

A task tells Ratchet which parts of an answer must match for the student to count
as agreeing with the teacher. Structured fields (labels) are compared exactly;
free-text fields are ignored by default (the judge can score them separately).
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

    def canonical(self, text: str) -> str:
        if self.compare_keys:
            obj = parse_json(text)
            if obj is None:
                return "<invalid>"
            return json.dumps({k: _norm(obj.get(k)) for k in self.compare_keys}, sort_keys=True)
        return " ".join(strip_reasoning(text).lower().split())

    def agree(self, a: str, b: str) -> bool:
        ca, cb = self.canonical(a), self.canonical(b)
        return ca != "<invalid>" and ca == cb


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def strip_reasoning(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def parse_json(text: str) -> dict[str, Any] | None:
    t = _FENCE.sub("", strip_reasoning(text)).strip()
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
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


_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def get(name: str | None) -> Task:
    """Known tasks come from REGISTRY. Unknown names get a whole-text comparison task that is
    NOT stored, so arbitrary client headers cannot grow the registry. Invalid names fall back
    to 'default'."""
    if not name or not _NAME.match(name):
        return REGISTRY["default"]
    return REGISTRY.get(name) or Task(name)
