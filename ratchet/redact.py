"""PII redaction before anything is stored as training data.

Escalated and audited requests become training examples, and a LoRA adapter can memorise
what it is trained on. So emails, phone numbers, card numbers and similar identifiers are
replaced with placeholders like `[EMAIL]` before `Ledger.add_example` sees them.

This is a pattern-based redactor with checksums where the format has one (Luhn for cards,
mod-97 for IBANs, Verhoeff for Aadhaar), so it adds no dependency and runs in microseconds.
It does not find names or street addresses; that needs an NER model such as Presidio.
The teacher still sees the original request: it needs the details to answer.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Any


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _luhn_ok(s: str) -> bool:
    d = [int(c) for c in _digits(s)]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x *= 2
            if x > 9:
                x -= 9
        total += x
    return total % 10 == 0


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _aadhaar_ok(s: str) -> bool:
    d = _digits(s)
    if len(d) != 12 or d[0] in "01":
        return False
    c = 0
    for i, x in enumerate(reversed(d)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(x)]]
    return c == 0


def _phone_ok(s: str) -> bool:
    return 10 <= len(_digits(s)) <= 15


# (label, pattern, extra check). Order matters: earlier patterns win, so the specific
# formats with checksums run before the broad phone pattern.
PATTERNS: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = [
    (
        "SECRET",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}"
            r"|xox[abprs]-[A-Za-z0-9-]{10,})"
            r"|(?<=Bearer )[A-Za-z0-9._~+/-]{20,}=*"
        ),
        None,
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), None),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), _iban_ok),
    ("CARD", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"), _luhn_ok),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)"), _aadhaar_ok),
    ("SSN", re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)"), None),
    ("PAN", re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b"), None),
    (
        "IP",
        re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\.?\d)"),
        None,
    ),
    (
        "PHONE",
        re.compile(r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?)?\d{2,5}(?:[ .-]?\d{2,5}){1,4}(?![\w-])"),
        _phone_ok,
    ),
]


def redact(text: str, counts: Counter[str] | None = None) -> str:
    """Replace identifiers in `text` with `[LABEL]` placeholders. Adds what it found to `counts`."""
    if not text:
        return text
    for label, pattern, check in PATTERNS:

        def sub(m: re.Match[str], label: str = label, check: Callable[[str], bool] | None = check) -> str:
            if check is not None and not check(m.group(0)):
                return m.group(0)
            if counts is not None:
                counts[label] += 1
            return f"[{label}]"

        text = pattern.sub(sub, text)
    return text


def _redact_function(fn: Any, counts: Counter[str] | None) -> Any:
    if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
        return {**fn, "arguments": redact(fn["arguments"], counts)}
    return fn


def redact_messages(messages: list[dict[str, Any]], counts: Counter[str] | None = None) -> list[dict[str, Any]]:
    """Redact chat messages: content (a string or OpenAI content parts, `[{"type": "text", "text": ...}]`), `name`, an
    assistant's `refusal`, and the arguments of its tool and function calls, which can carry what the user typed. The
    rest of a message is a role, an id or a part that is not text."""
    out = []
    for m in messages:
        m = dict(m)
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = redact(content, counts)
        elif isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    p = {**p, "text": redact(p["text"], counts)}
                parts.append(p)
            m["content"] = parts
        for key in ("name", "refusal"):
            if isinstance(m.get(key), str):
                m[key] = redact(m[key], counts)
        if isinstance(m.get("tool_calls"), list):
            m["tool_calls"] = [
                {**c, "function": _redact_function(c["function"], counts)} if isinstance(c, dict) and "function" in c else c
                for c in m["tool_calls"]
            ]
        if "function_call" in m:
            m["function_call"] = _redact_function(m["function_call"], counts)
        out.append(m)
    return out


def redact_json(value: Any, counts: Counter[str] | None = None) -> Any:
    """A copy of a JSON value with every string value redacted: the generation conditions stored with an example (a
    tool's description, a schema's enum, a stop string can carry what a user typed). Object keys are kept: they are
    field names a schema needs. Iterative, so any nesting the request validation let through is fine."""
    if isinstance(value, str):
        return redact(value, counts)
    if not isinstance(value, (dict, list, tuple)):
        return value
    root: Any = {} if isinstance(value, dict) else []
    stack: list[tuple[Any, Any]] = [(value, root)]
    while stack:
        src, dst = stack.pop()
        for key, item in src.items() if isinstance(src, dict) else enumerate(src):
            if isinstance(item, str):
                out: Any = redact(item, counts)
            elif isinstance(item, dict):
                out = {}
                stack.append((item, out))
            elif isinstance(item, (list, tuple)):
                out = []
                stack.append((item, out))
            else:
                out = item
            if isinstance(dst, dict):
                dst[key] = out
            else:
                dst.append(out)
    return root
