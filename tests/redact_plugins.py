"""Detector plugins for the redaction tests (RATCHET_REDACT_PLUGIN='redact_plugins:<name>', ADR 0082).

Each plays one kind of plugin: a working one, one that fails on real text but passes the startup probe, one that
returns spans the redactor must refuse, and attributes that are not detectors at all. Nothing here imports Presidio.
"""

from __future__ import annotations

import re

NAMES = re.compile(r"\b(?:Alice Smith|Bob Jones|Priya Patel)\b")
CALLS: list[str] = []  # every text `names` was given, so a test can see what the plugin saw


def names(text: str) -> list[tuple[int, int, str]]:
    """Tags a few full names as NAME, the way an NER model would."""
    CALLS.append(text)
    return [(m.start(), m.end(), "NAME") for m in NAMES.finditer(text)]


def lower_label(text: str) -> list[tuple[int, int, str]]:
    """Returns its label in lower case: the redactor upper-cases it."""
    return [(m.start(), m.end(), "person") for m in NAMES.finditer(text)]


def overlapping(text: str) -> list[tuple[int, int, str]]:
    """Two spans for one name, plus one that overlaps the email after it: the earlier span wins and covers them all."""
    out = []
    for m in NAMES.finditer(text):
        out += [(m.start(), m.end(), "NAME"), (m.start() + 2, m.end() + 8, "OTHER")]
    return out


def flaky(text: str) -> list[tuple[int, int, str]]:
    """Passes the startup probe (an empty text), then fails on every real text, with PII in its message."""
    if text:
        raise RuntimeError(f"model crashed on {text!r}")
    return []


def out_of_range(text: str) -> list[tuple[int, int, str]]:
    return [(0, len(text) + 5, "NAME")] if text else []


def bad_label(text: str) -> list[tuple[int, int, str]]:
    return [(0, 1, "not a label!")] if text else []


def not_triples(text: str) -> list[object]:
    return [(0, 1)] if text else []


def bool_offsets(text: str) -> list[tuple[object, object, str]]:
    return [(False, True, "NAME")] if text else []


def float_offsets(text: str) -> list[tuple[object, object, str]]:
    return [(0.0, 5.0, "NAME")] if text else []


class Offset:
    """An integer type that is not `int`, as numpy's are: usable through `__index__`."""

    def __init__(self, n: int) -> None:
        self.n = n

    def __index__(self) -> int:
        return self.n


def index_offsets(text: str) -> list[tuple[object, object, str]]:
    """Tags names with offsets of an integer type that is not `int`."""
    return [(Offset(m.start()), Offset(m.end()), "NAME") for m in NAMES.finditer(text)]


def empty_span(text: str) -> list[tuple[int, int, str]]:
    return [(1, 1, "NAME")] if text else []


def broken_probe(text: str) -> list[tuple[int, int, str]]:
    """Fails even on the startup probe: startup must stop."""
    raise ValueError("no model loaded")


def returns_none(text: str) -> None:
    return None


class Holder:
    """A plugin reached by a dotted path after the colon: 'redact_plugins:Holder.detect'."""

    @staticmethod
    def detect(text: str) -> list[tuple[int, int, str]]:
        return names(text)


NOT_CALLABLE = 42
