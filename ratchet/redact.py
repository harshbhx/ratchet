"""PII redaction before anything is stored as training data.

Escalated and audited requests become training examples, and a LoRA adapter can memorise
what it is trained on. So emails, phone numbers, card numbers and similar identifiers are
replaced with placeholders like `[EMAIL]` before `Ledger.add_example` sees them.

This is a pattern-based redactor with checksums where the format has one (Luhn for cards,
mod-97 for IBANs, Verhoeff for Aadhaar), so it adds no dependency and runs in microseconds.
It does not find names or street addresses; that needs an NER model such as Presidio, which
a `Detector` plugin can add (RATCHET_REDACT_PLUGIN). The teacher still sees the original
request: it needs the details to answer.

Placeholders come in three styles (ADR 0082). `label` writes `[EMAIL]` for every email, as v0.4
did. `numbered` writes `[EMAIL_1]`, `[EMAIL_2]` in order of first appearance, and `surrogate` a
reserved value of the same shape (`person1@example.com`). A `Pseudonymizer` holds one example's
mapping, so a value gets the same placeholder in the prompt, the stored conditions and the
answer, and an extraction target can still say which of two emails it means. It lives for one
example: no original value is kept, and nothing links two examples.
"""

from __future__ import annotations

import importlib
import operator
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

# Placeholder styles (RATCHET_REDACT_STYLE, a task spec's `privacy.redact_style`). `label` is v0.4's and the default.
STYLES = ("label", "numbered", "surrogate")
# A plugin's detector (RATCHET_REDACT_PLUGIN): the spans of `text` it finds, each (start, end, label) with
# 0 <= start < end <= len(text) and a label of letters, digits and '_' (upper-cased). It must be thread-safe: Scout
# calls the cascade from several threads.
Detector = Callable[[str], Iterable[tuple[int, int, str]]]
_DETECTOR_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")


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


# Handles after '@' that are package versions or branches, not payment providers: `react@latest`, `feature@main`.
_NOT_UPI = frozenset("latest next beta alpha canary stable nightly dev rc edge lts experimental main master head".split())


def _upi_ok(s: str) -> bool:
    return s.rpartition("@")[2].lower() not in _NOT_UPI


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
    # A UPI id (Indian payments): `name@okicici`, `9876543210@ybl`: a provider handle of letters after the '@', with no
    # domain after it (an email has already been replaced). Before PHONE, which would otherwise take the digits of a
    # mobile-number id.
    (
        "UPI",
        re.compile(r"(?<![\w.%+-])[A-Za-z0-9][A-Za-z0-9._-]{1,255}@[A-Za-z]{2,64}(?![\w@-]|\.[A-Za-z0-9])"),
        _upi_ok,
    ),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), _iban_ok),
    ("CARD", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"), _luhn_ok),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)"), _aadhaar_ok),
    ("SSN", re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)"), None),
    # An Indian GSTIN: a 2-digit state code, the holder's PAN, an entity number, 'Z' today, a check character. Format only,
    # like PAN: the PAN inside is an identifier whether or not the check character is right.
    ("GSTIN", re.compile(r"\b\d{2}[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z][1-9A-Z][0-9A-Z]{2}\b"), None),
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


class RedactionError(ValueError):
    """A detector raised, or returned something that is not a list of (start, end, label) spans of the text. The message
    names the problem and never quotes the text or the detector's own message, which may hold what it was looking for."""


def _detected(detector: Detector, text: str) -> list[tuple[int, int, str]]:
    """The detector's spans of `text`, checked: (start, end, LABEL) with 0 <= start < end <= len(text). RedactionError
    otherwise, so a broken plugin stops an example from being stored instead of letting it through unredacted."""
    try:
        raw = detector(text)
        items = list(raw)
    except Exception as e:  # the plugin's own failure: named by class only
        raise RedactionError(f"the redaction plugin raised {type(e).__name__}") from None
    out = []
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RedactionError("the redaction plugin must return (start, end, label) triples")
        start, end, label = item
        try:  # any integer type (an NER model may return numpy integers), never a bool or a float
            if isinstance(start, bool) or isinstance(end, bool):
                raise TypeError
            start, end = operator.index(start), operator.index(end)
        except TypeError:
            raise RedactionError("the redaction plugin returned a span whose start or end is not an integer") from None
        if not 0 <= start < end <= len(text):
            raise RedactionError("the redaction plugin returned a span outside the text, or an empty one")
        if not isinstance(label, str) or _DETECTOR_LABEL.fullmatch(label) is None:
            raise RedactionError("the redaction plugin returned a label that is not 1 to 32 letters, digits and '_'")
        out.append((start, end, label.upper()))
    return out


# Stands in for a span found by an earlier pattern while the later ones run: a private-use character that no pattern
# matches and that, like the `[`, `]` of v0.4's placeholders, is neither a word character, a digit nor punctuation a
# pattern looks at. Same length as the span, so offsets stay those of the original text.
_MASK = "\ue000"


# Labels whose check is a checksum (Luhn, mod-97, Verhoeff), searched again in what the patterns left (`_pattern_spans`):
# a part of a refused match that passes the checksum is an identifier. PHONE only counts digits, so searching it again
# would cut order and account numbers into "phones".
_RETRY = ("IBAN", "CARD", "AADHAAR")


def _matches(pattern: re.Pattern[str], check: Callable[[str], bool] | None, text: str, retry: bool) -> list[tuple[int, int]]:
    """The (start, end) of each match of `pattern` in `text` that passes `check`, left to right, not overlapping.

    With `retry`, a match the check refuses is tried again before the search moves on: shorter, ending at each
    whitespace inside it (shortest first, so two card numbers in a row are two), then from each word inside it that
    follows whitespace. A shorter match ends where whitespace follows, which every checked pattern's closing assertion
    accepts, so it is a match the pattern could have made on its own. The retries stay inside the refused match, a few
    dozen characters."""
    if check is None:
        return [m.span() for m in pattern.finditer(text)]
    if not retry:
        return [m.span() for m in pattern.finditer(text) if check(m.group(0))]
    out: list[tuple[int, int]] = []
    pos = 0
    while (m := pattern.search(text, pos)) is not None:
        start, end = m.span()
        if check(m.group(0)):
            out.append((start, end))
            pos = end
            continue
        cuts = [i for i in range(start + 1, end) if text[i].isspace() and not text[i - 1].isspace()]
        shorter = None
        for cut in cuts:
            t = pattern.match(text, start, cut)
            if t is not None and check(t.group(0)):
                shorter = t.end()
                break
        if shorter is not None:
            out.append((start, shorter))
            pos = shorter
            continue
        # Nothing valid starts here: search again from the next word inside the refused match, or after it.
        pos = next((i for i in range(start + 1, end) if text[i - 1].isspace() and not text[i].isspace()), end)
    return out


def _masked(text: str, found: list[tuple[int, int, str]]) -> str:
    """`text` with each found span replaced by as many `_MASK` characters."""
    parts, last = [], 0
    for start, end, _ in found:
        parts += [text[last:start], _MASK * (end - start)]
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _pattern_spans(text: str) -> list[tuple[int, int, str]]:
    """Every pattern's matches in `text` as (start, end, label), sorted.

    First pass: the patterns run in PATTERNS order and each sees the earlier ones' matches masked, which finds exactly
    what v0.4's replacement pattern by pattern found. A card number followed by its CVV or expiry ('4111 1111 1111 1111
    123') is one digit run that fails Luhn, and v0.4 left the card in the text. So a second pass, in what is left, runs
    the checksum patterns (`_RETRY`) with the refused matches retried (`_matches`), and when that finds something, a
    third runs every pattern once more on what is then left: a phone number just before the card is found now that the
    card no longer joins it into one long digit run. Only text no pattern took is searched again, so every v0.4 span
    stays as it was and a chance checksum match cannot cut into a phone number or an Aadhaar."""
    spans: list[tuple[int, int, str]] = []
    masked = text

    def run(retry: bool) -> bool:
        nonlocal masked
        added = False
        for label, pattern, check in PATTERNS:
            if retry and label not in _RETRY:
                continue
            found = [(start, end, label) for start, end in _matches(pattern, check, masked, retry)]
            if found:
                spans.extend(found)
                masked = _masked(masked, found)
                added = True
        return added

    run(False)
    if run(True):
        run(False)
    spans.sort()
    return spans


def _merge(patterns: list[tuple[int, int, str]], detected: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Pattern and detector spans as one sorted list without overlaps. Of overlapping spans the one that starts first
    wins, then the longer, then a pattern's; the winner's placeholder covers all of them, so no part of a span it
    overlaps is left in the text."""
    ordered = sorted([(s, -e, 0, label) for s, e, label in patterns] + [(s, -e, 1, label) for s, e, label in detected])
    out: list[tuple[int, int, str]] = []
    for start, neg_end, _, label in ordered:
        end = -neg_end
        if out and start < out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end, out[-1][2])
            continue
        out.append((start, end, label))
    return out


def spans(text: str, detector: Detector | None = None) -> list[tuple[int, int, str]]:
    """What the redactor replaces in `text`: (start, end, label) spans, sorted and not overlapping. The patterns'
    matches, merged with the detector's spans when there is one (RedactionError if it fails)."""
    if not text:
        return []
    found = _pattern_spans(text)
    if detector is None:
        return found
    extra = _detected(detector, text)
    return _merge(found, extra) if extra else found


# ---------------------------------------------------------------------------------------------------------------------
# Placeholders


def _key(label: str, value: str) -> str:
    """When two spellings are the same value: case for addresses, separators for numbers, spacing and case for names. A
    phone number is its last 10 digits, so one written with and without its country code is one value (and two that
    differ only in the country code are one too: a lookup, never a scan, however many numbers a text holds)."""
    if label in ("EMAIL", "UPI"):
        return value.casefold()
    if label == "PHONE":
        return _digits(value)[-10:]
    if label in ("CARD", "AADHAAR", "SSN"):
        return _digits(value)
    if label in ("IBAN", "GSTIN", "PAN"):
        return re.sub(r"[^0-9A-Za-z]", "", value).upper()
    if label in ("IP", "SECRET"):
        return value
    return " ".join(value.split()).casefold()


# Surrogates are written without spaces, as one token each (E.164 for phones, the electronic format for IBANs): a value
# with spaces could join the digit groups around it into something a second pass reads differently.
_CARD_PREFIX = "400000000000"  # its 12 digits fail Verhoeff, so a surrogate never starts with an Aadhaar


def _card(n: int) -> str | None:
    """`4000000000000NNNc`: c is one more than the Luhn check digit, so the number is never a real card and a second pass
    leaves it alone; 16 digits, more than a phone number has."""
    if n > 999:
        return None
    body = f"{_CARD_PREFIX}{n:03d}"
    total = 0
    for i, ch in enumerate(reversed(body)):  # the check digit's neighbours are doubled
        x = int(ch) * (2 if i % 2 == 0 else 1)
        total += x - 9 if x > 9 else x
    check = (10 - total % 10) % 10
    return body + str((check + 1) % 10)


def _iban(n: int) -> str:
    """`ZZcc00000000000000n`: ZZ is an ISO 3166 code reserved for user assignment, never a country, so this is never
    anyone's account; the check digits are valid, so it reads as an IBAN (and a second pass finds it again). 15 digits
    after the check digits, so the IBAN pattern ends on a short group and cannot take a 4-character word after it."""
    bban = f"{n:015d}"
    moved = int("".join(str(int(c, 36)) for c in bban + "ZZ00"))
    return f"ZZ{98 - moved % 97:02d}{bban}"


def _ip(n: int) -> str | None:
    """The documentation networks of RFC 5737 (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24), hosts 1 to 254."""
    for net in ("192.0.2", "198.51.100", "203.0.113"):
        if n <= 254:
            return f"{net}.{n}"
        n -= 254
    return None


def _phone(n: int) -> str | None:
    """`+12025550100`: 555-0100 to 555-0199 are set aside for fiction in every North American area code (100 per area
    code, from 202 on)."""
    area, line = divmod(n - 1, 100)
    return f"+1{202 + area}55501{line:02d}" if area < 798 else None


def _surrogate(label: str, n: int) -> str | None:
    """The n-th reserved value shaped like a `label`, or None when the label has none (or they ran out)."""
    if label == "EMAIL":
        return f"person{n}@example.com"  # RFC 2606
    if label == "PHONE":
        return _phone(n)
    if label == "CARD":
        return _card(n)
    if label == "IBAN":
        return _iban(n)
    if label == "IP":
        return _ip(n)
    return None


class Pseudonymizer:
    """One example's placeholders (ADR 0082): made for one example, passed to `redact_messages`, `redact_json` and
    `redact` for its prompt, conditions and answer (in that order, so the request's placeholders never depend on the
    answer), then dropped. `label` style writes `[LABEL]` (v0.4); `numbered` the same value's `[LABEL_n]` everywhere,
    n in order of first appearance per label; `surrogate` a reserved value of the same shape (`_surrogate`: emails,
    phones, cards, IBANs, IPs), else the numbered placeholder. A value is the same when its spellings normalise alike
    (`_key`). `detector` adds a plugin's spans. Not thread-safe: one per example, on one thread."""

    def __init__(self, style: str = "label", detector: Detector | None = None) -> None:
        if style not in STYLES:
            raise ValueError(f"redaction style must be one of {', '.join(STYLES)}, not {style!r}")
        self.style = style
        self.detector = detector
        self._tokens: dict[tuple[str, str], str] = {}
        self._count: Counter[str] = Counter()

    def placeholder(self, label: str, value: str) -> str:
        """The placeholder for `value`, found as a `label`: the one it got before in this example, or the next one."""
        if self.style == "label":
            return f"[{label}]"
        key = (label, _key(label, value))
        token = self._tokens.get(key)
        if token is None:
            self._count[label] += 1
            n = self._count[label]
            token = (_surrogate(label, n) if self.style == "surrogate" else None) or f"[{label}_{n}]"
            self._tokens[key] = token
        return token


def load_detector(spec: str) -> Detector:
    """RATCHET_REDACT_PLUGIN: import `module:function` (a dotted attribute path after the colon) and call it once on an
    empty text, which must find nothing. ValueError names what is wrong, so a typo or a missing package stops startup
    instead of storing what the plugin was meant to find."""
    module_name, sep, attr = spec.strip().partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"RATCHET_REDACT_PLUGIN must be 'module:function', not {spec!r}")
    try:
        target: Any = importlib.import_module(module_name)
    except Exception as e:
        raise ValueError(f"RATCHET_REDACT_PLUGIN: cannot import {module_name!r} ({type(e).__name__}: {e})") from None
    for part in attr.split("."):
        if not hasattr(target, part):
            raise ValueError(f"RATCHET_REDACT_PLUGIN: {module_name!r} has no {attr!r}")
        target = getattr(target, part)
    if not callable(target):
        raise ValueError(f"RATCHET_REDACT_PLUGIN: {spec.strip()!r} is not callable")
    try:
        _detected(target, "")  # any span of an empty text is refused, so this also checks it finds nothing
    except RedactionError as e:
        raise ValueError(f"RATCHET_REDACT_PLUGIN: {spec.strip()!r} failed on an empty text: {e}") from None
    return target


# ---------------------------------------------------------------------------------------------------------------------
# Redacting text, messages and JSON


def redact(text: str, counts: Counter[str] | None = None, pseudo: Pseudonymizer | None = None) -> str:
    """Replace identifiers in `text` with placeholders: `[LABEL]`, or `pseudo`'s style (with its detector's spans too).
    Adds what it found to `counts`. RedactionError when `pseudo`'s detector fails."""
    if not text:
        return text
    found = spans(text, pseudo.detector if pseudo is not None else None)
    if not found:
        return text
    parts, last = [], 0
    for start, end, label in found:
        parts += [text[last:start], pseudo.placeholder(label, text[start:end]) if pseudo is not None else f"[{label}]"]
        last = end
        if counts is not None:
            counts[label] += 1
    parts.append(text[last:])
    return "".join(parts)


def _redact_function(fn: Any, counts: Counter[str] | None, pseudo: Pseudonymizer | None = None) -> Any:
    if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
        return {**fn, "arguments": redact(fn["arguments"], counts, pseudo)}
    return fn


def redact_messages(
    messages: list[dict[str, Any]], counts: Counter[str] | None = None, pseudo: Pseudonymizer | None = None
) -> list[dict[str, Any]]:
    """Redact chat messages: content (a string or OpenAI content parts, `[{"type": "text", "text": ...}]`), `name`, an
    assistant's `refusal`, and the arguments of its tool and function calls, which can carry what the user typed. The
    rest of a message is a role, an id or a part that is not text. `pseudo` is the example's (`redact`)."""
    out = []
    for m in messages:
        m = dict(m)
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = redact(content, counts, pseudo)
        elif isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    p = {**p, "text": redact(p["text"], counts, pseudo)}
                parts.append(p)
            m["content"] = parts
        for key in ("name", "refusal"):
            if isinstance(m.get(key), str):
                m[key] = redact(m[key], counts, pseudo)
        if isinstance(m.get("tool_calls"), list):
            m["tool_calls"] = [
                {**c, "function": _redact_function(c["function"], counts, pseudo)}
                if isinstance(c, dict) and "function" in c
                else c
                for c in m["tool_calls"]
            ]
        if "function_call" in m:
            m["function_call"] = _redact_function(m["function_call"], counts, pseudo)
        out.append(m)
    return out


def redact_json(value: Any, counts: Counter[str] | None = None, pseudo: Pseudonymizer | None = None) -> Any:
    """A copy of a JSON value with every string value redacted: the generation conditions stored with an example (a
    tool's description, a schema's enum, a stop string can carry what a user typed). Object keys are kept: they are
    field names a schema needs. Iterative, so any nesting the request validation let through is fine. The values are
    visited in the same order every time, so `pseudo` numbers the same conditions alike."""
    if isinstance(value, str):
        return redact(value, counts, pseudo)
    if not isinstance(value, (dict, list, tuple)):
        return value
    root: Any = {} if isinstance(value, dict) else []
    stack: list[tuple[Any, Any]] = [(value, root)]
    while stack:
        src, dst = stack.pop()
        for key, item in src.items() if isinstance(src, dict) else enumerate(src):
            if isinstance(item, str):
                out: Any = redact(item, counts, pseudo)
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
