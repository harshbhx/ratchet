"""Nemotron's thinking switch, by role (ADR 0130). Standard library only.

Nemotron 3 templates turn reasoning on unless told otherwise (`enable_thinking`). Ratchet sets it per role, from operator
settings, and sends it as the chat template's keyword arguments. `default` sends nothing. The kwargs never come from a
client, are never stored and never join a hash.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

MODES = ("default", "off", "on", "medium")
# `medium` is the Ultra card's `medium_effort`; templates that do not read it ignore it.
KWARGS: dict[str, Mapping[str, Any] | None] = {
    "default": None,
    "off": MappingProxyType({"enable_thinking": False}),
    "on": MappingProxyType({"enable_thinking": True}),
    "medium": MappingProxyType({"enable_thinking": True, "medium_effort": True}),
}
# The student is trained with thinking off (dataset.TRAIN_TEMPLATE_KWARGS, ADR 0145), so it is served with it off.
STUDENT_MODES = ("off", "default")
FALLBACK_STUDENT_MODES = ("off", "on", "default")
# Where a Token Factory-bound call carries the kwargs. UNVERIFIED until day 1 whether Token Factory applies either.
FIELDS = ("chat_template_kwargs", "extra_body")
TRAIN_TEMPLATE_KWARGS = KWARGS["off"]


def parse(raw: str | None, env: str, allowed: tuple[str, ...] = MODES, default: str = "default") -> str:
    """A mode from an environment value: case-insensitive, empty means `default`. ValueError names `env`."""
    text = (raw or "").strip().lower() or default
    if text in allowed:
        return text
    why = ""
    if env.endswith("STUDENT_REASONING") and text in MODES:
        why = (
            "; the student is trained without reasoning (enable_thinking=False), so a student that reasons is not the "
            "model that was trained"
        )
    raise ValueError(f"{env} must be one of {', '.join(allowed)}, not {raw!r}{why}")


def template_kwargs(mode: str) -> Mapping[str, Any] | None:
    """The chat template kwargs for `mode`, or None (send nothing)."""
    try:
        return KWARGS[mode]
    except KeyError:
        raise ValueError(f"reasoning mode must be one of {', '.join(MODES)}, not {mode!r}") from None


def body_fields(kwargs: Mapping[str, Any] | None, field: str = "chat_template_kwargs") -> dict[str, Any]:
    """The request body keys that carry `kwargs`; plain dicts, so `json.dumps` accepts them."""
    if field not in FIELDS:
        raise ValueError(f"the reasoning field must be one of {', '.join(FIELDS)}, not {field!r}")
    if not kwargs:
        return {}
    if field == "extra_body":
        return {"extra_body": {"chat_template_kwargs": dict(kwargs)}}
    return {"chat_template_kwargs": dict(kwargs)}


class ReasoningPolicy:
    """The checked settings: the mode of the student, the hosted fallback student, the serving teacher and the audits."""

    __slots__ = ("student", "fallback_student", "serve", "check", "field")

    def __init__(self, student: str, fallback_student: str, serve: str, check: str, field: str):
        self.student, self.fallback_student, self.serve, self.check, self.field = student, fallback_student, serve, check, field

    def snapshot(self) -> dict[str, str]:
        return {
            "student": self.student,
            "fallback_student": self.fallback_student,
            "serve": self.serve,
            "check": self.check,
            "field": self.field,
        }
