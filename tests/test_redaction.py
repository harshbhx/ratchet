"""Redaction for extraction tasks (FR-13.7, FR-4.7, NFR-4, INV-12, ADR 0082).

- Styles: `label` (`[EMAIL]`, v0.4's and the default), `numbered` (`[EMAIL_1]`, the same value the same number in the
  prompt, the answer and the stored conditions) and `surrogate` (reserved values of the same shape, such as
  `person1@example.com`), chosen by RATCHET_REDACT_STYLE or a task spec's `privacy.redact_style`.
- A detector plugin (RATCHET_REDACT_PLUGIN, 'module:function') adds spans the patterns cannot find (names); it is
  imported at startup, and when it fails at store time the example is not stored.
- Indian identifiers: GSTIN (with the PAN inside it) and UPI ids.
- `label` style finds and writes exactly what v0.4 did: a verbatim copy of v0.4's sequential replacement is the oracle.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
from collections import Counter
from typing import Any

import pytest
import redact_plugins
from fakes import OPEN_TEACHER, RecordingProvider
from fastapi.testclient import TestClient

from ratchet import redact as rd
from ratchet import tasks, taskspec
from ratchet.compare import FieldSpec
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.redact import PATTERNS, Pseudonymizer, RedactionError, load_detector, redact, redact_json, redact_messages
from ratchet.router import Ratchet

CHAT = "/v1/chat/completions"
EXTRACT = "contact-extract"
ACME_KEY = "sk-acme-0123456789abcdef"
ADMIN_KEY = "sk-admin-0123456789abcdef"
# v0.4's nine rules, unchanged: the SHA-256 the BOM's `redactor` section showed before GSTIN and UPI (ADR 0082 cites e85726…).
V04_LABELS = ("SECRET", "EMAIL", "IBAN", "CARD", "AADHAAR", "SSN", "PAN", "IP", "PHONE")
V04_RULES_SHA256 = "e85726e38b1cf2b1878ba4099b7c38c30ea5268a811b270511c54ac3d0d4d27c"
# v0.4's EMAIL rule, kept here as it was: unbounded repeats, which backtrack quadratically on a long run of address
# characters. Today's `PATTERNS` bound every repeat (`test_every_pattern_scans_adversarial_input_in_linear_time`), so the
# EMAIL rule is the one that is not v0.4's byte for byte; it finds the same addresses (`test_label_style_matches_v04` compares
# the two on fuzzed text) and refuses only parts RFC 5321 does not allow (`test_email_pattern_still_finds_addresses`).
V04_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
V04_PATTERNS = [(p[0], V04_EMAIL if p[0] == "EMAIL" else p[1], p[2]) for p in PATTERNS if p[0] in V04_LABELS]
CHECKS = {label: check for label, _, check in PATTERNS}
# One of each identifier the patterns know, as people write them.
IDENTIFIERS = {
    "EMAIL": "jane.doe@acme.co.uk",
    "UPI": "jane.doe@okicici",
    "IBAN": "DE89 3704 0044 0532 0130 00",
    "CARD": "4111 1111 1111 1111",
    "AADHAAR": "2345 6789 0124",
    "SSN": "123-45-6789",
    "GSTIN": "27AAPFU0939F1ZV",
    "PAN": "ABCPE1234F",
    "IP": "192.168.1.20",
    "PHONE": "+91 98765 43210",
    "SECRET": "sk-abcdefghijklmnopqrstuvwxyz123",
}


def _sequential(text: str, patterns: list[Any]) -> str:
    """v0.4's `redact()`, verbatim but for the pattern list: each pattern replaces its matches in turn."""
    if not text:
        return text
    for label, pattern, check in patterns:

        def sub(m: re.Match[str], label: str = label, check: Any = check) -> str:
            if check is not None and not check(m.group(0)):
                return m.group(0)
            return f"[{label}]"

        text = pattern.sub(sub, text)
    return text


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "redact_pii": True,
        "redact_style": "label",
        "redact_plugin": "",
        "store_examples": True,
        "no_store_tasks": "",
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "unknown_tasks": "distill",
    }
    return Settings(**{**base, **overrides})


def _cold(answer: str | Any, **settings: Any) -> tuple[Ledger, Ratchet, RecordingProvider]:
    """A ledger with no version yet: every request goes to the teacher and its answer is stored."""
    led, teacher = Ledger(":memory:"), RecordingProvider(answer)
    return led, Ratchet(_settings(**settings), led, teacher, lambda v: (None, "x"), seed=1), teacher


def _extract_task(style: str | None = None) -> tasks.Task:
    task = tasks.Task(EXTRACT, fields=(FieldSpec("contact", "text"),), source="file", redact_style=style)
    tasks.register(task)
    return task


def _dump(led: Ledger) -> list[str]:
    """Every text value in every table of the ledger."""
    out = []
    for (table,) in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in led.db.execute(f"SELECT * FROM {table}"):  # noqa: S608 - names come from sqlite_master
            out += [v for v in tuple(row) if isinstance(v, str)]
    return out


def _stored(led: Ledger) -> tuple[list[dict[str, Any]], str]:
    (row,) = led.examples()
    return json.loads(row["messages"]), row["completion"]


# ---------- placeholders ----------
@pytest.mark.req("FR-13.7", "FR-4.7", "INV-12")
def test_two_emails_distinct_placeholders():
    text = "From ana@x.io to bo@y.io, cc ANA@X.IO, reply to bo@y.io"
    assert redact(text) == "From [EMAIL] to [EMAIL], cc [EMAIL], reply to [EMAIL]"  # v0.4: which one is lost
    c: Counter[str] = Counter()
    assert redact(text, c, Pseudonymizer("numbered")) == "From [EMAIL_1] to [EMAIL_2], cc [EMAIL_1], reply to [EMAIL_2]"
    assert c == Counter(EMAIL=4)  # every placeholder is counted, as in v0.4
    out = redact(text, None, Pseudonymizer("surrogate"))
    assert out == "From person1@example.com to person2@example.com, cc person1@example.com, reply to person2@example.com"
    # Numbers are per label and per example: a new Pseudonymizer starts again, so nothing links two examples.
    p = Pseudonymizer("numbered")
    assert redact("call +44 20 7946 0958, mail ana@x.io", None, p) == "call [PHONE_1], mail [EMAIL_1]"
    assert redact("mail zed@z.io", None, Pseudonymizer("numbered")) == "mail [EMAIL_1]"
    # A phone with and without its country code is one value; separators never matter.
    p = Pseudonymizer("numbered")
    assert redact("+91 98765 43210 or 98765-43210 or 9876543210, not 98765 43211", None, p) == (
        "[PHONE_1] or [PHONE_1] or [PHONE_1], not [PHONE_2]"
    )
    p = Pseudonymizer("numbered")
    assert redact("4111 1111 1111 1111 = 4111-1111-1111-1111", None, p) == "[CARD_1] = [CARD_1]"
    with pytest.raises(ValueError, match="numbered"):
        Pseudonymizer("hashed")


@pytest.mark.req("FR-13.7", "FR-4.7", "INV-12")
def test_numbered_placeholders_consistent_between_prompt_and_answer():
    """The teacher picks the second of two emails; the stored answer names the second placeholder of the prompt, so the
    student learns which one to copy, and the golden eval can tell a wrong pick from a right one."""
    task = _extract_task()
    answer = json.dumps({"contact": "bo@y.io"})
    led, r, teacher = _cold(answer, redact_style="numbered")
    msgs = [
        {"role": "system", "content": "Return the billing contact."},
        {"role": "user", "content": "Sales: ana@x.io. Billing: bo@y.io (call +44 20 7946 0958)."},
    ]
    out = r.handle(msgs, EXTRACT)
    assert out.stored and out.text == answer  # the client gets the teacher's original answer
    assert teacher.calls[0].messages[1]["content"] == msgs[1]["content"]  # the teacher saw the original
    stored, completion = _stored(led)
    assert stored[1]["content"] == "Sales: [EMAIL_1]. Billing: [EMAIL_2] (call [PHONE_1])."
    assert json.loads(completion) == {"contact": "[EMAIL_2]"}
    assert led.examples()[0]["redactions"] == 4
    # Comparators read placeholders as ordinary strings: copying the wrong placeholder is a disagreement.
    assert task.agree(completion, json.dumps({"contact": "[EMAIL_2]"}))
    assert not task.agree(completion, json.dumps({"contact": "[EMAIL_1]"}))


@pytest.mark.req("FR-13.7", "INV-12")
def test_one_pseudonymizer_covers_messages_answer_and_conditions():
    p, c = Pseudonymizer("numbered"), Counter()
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "I am ana@x.io"}], "name": "bo@y.io"},
        {
            "role": "assistant",
            "content": None,
            "refusal": "not mailing ana@x.io",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "mail", "arguments": '{"to": "bo@y.io"}'}}],
        },
    ]
    out = redact_messages(msgs, c, p)
    assert out[0]["content"][0]["text"] == "I am [EMAIL_1]" and out[0]["name"] == "[EMAIL_2]"
    assert out[1]["refusal"] == "not mailing [EMAIL_1]"
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"to": "[EMAIL_2]"}'
    assert redact("Write to bo@y.io and cy@z.io", c, p) == "Write to [EMAIL_2] and [EMAIL_3]"
    assert redact_json({"stop": ["cy@z.io"], "n": 1}, c, p) == {"stop": ["[EMAIL_3]"], "n": 1}
    assert c == Counter(EMAIL=7) and msgs[0]["name"] == "bo@y.io"  # the caller's messages are untouched


@pytest.mark.req("FR-13.7", "FR-4.7")
def test_surrogate_values_stable_under_second_pass():
    """redact(redact(x)) == redact(x) in every style, with a fresh Pseudonymizer for each pass, as when a stored example
    is redacted again. Surrogates are reserved values: they belong to no one."""
    text = (
        "Mail jane.doe@acme.co.uk or ana@x.io, pay jane.doe@okicici, IBAN DE89 3704 0044 0532 0130 00 or "
        "GB82 WEST 1234 5698 7654 32, card 4111 1111 1111 1111 or 5500 0000 0000 0004, Aadhaar 2345 6789 0124, "
        "SSN 123-45-6789, GSTIN 27AAPFU0939F1ZV, PAN ABCPE1234F, host 192.168.1.20 or 10.0.0.7, call "
        "+91 98765 43210, (415) 555-0132 or 98765 43210; key sk-abcdefghijklmnopqrstuvwxyz123."
    )
    answer = "Reached ana@x.io on (415) 555-0132; refund to GB82 WEST 1234 5698 7654 32."
    for style in rd.STYLES:
        p = Pseudonymizer(style)
        first = [redact(text, None, p), redact(answer, None, p)]
        q = Pseudonymizer(style)
        assert [redact(first[0], None, q), redact(first[1], None, q)] == first, style
    p = Pseudonymizer("surrogate")
    out = redact(text, None, p)
    for original in IDENTIFIERS.values():
        assert original not in out
    assert "person1@example.com" in out and "person2@example.com" in out  # RFC 2606
    # 555-0100 to 0199 are fiction only; one token each (E.164), so no digits around them can join a second pass's match.
    assert "call +12025550100, +12025550101 or +12025550100;" in out  # 98765 43210 is +91 98765 43210 without +91
    assert "192.0.2.1" in out and "192.0.2.2" in out  # RFC 5737 documentation addresses
    assert "[UPI_1]" in out and "[GSTIN_1]" in out and "[SECRET_1]" in out  # no surrogate shape: numbered
    ibans = re.findall(r"\bZZ\d{17}\b", out)
    cards = re.findall(r"\b400000000000\d{4}\b", out)
    assert len(set(ibans)) == 2 and len(set(cards)) == 2
    for iban in ibans:  # valid check digits under a code reserved for user assignment, never a country
        assert rd._iban_ok(iban)
    for card in cards:  # never a valid card number, never an Aadhaar
        assert not rd._luhn_ok(card) and redact(card) == card


@pytest.mark.req("FR-13.7")
def test_surrogates_are_reserved_and_distinct_until_they_run_out():
    emails = [rd._surrogate("EMAIL", n) for n in range(1, 301)]
    assert len(set(emails)) == 300 and all(e.endswith("@example.com") for e in emails)
    phones = [rd._phone(n) for n in range(1, 79_801)]
    assert len(set(phones)) == 79_800 and all(re.fullmatch(r"\+1[2-9]\d\d55501\d\d", p) for p in phones)
    assert phones[0] == "+12025550100" and phones[99] == "+12025550199" and phones[100] == "+12035550100"
    assert rd._phone(79_801) is None
    cards = [rd._card(n) for n in range(1, 1000)]
    assert len(set(cards)) == 999 and not any(rd._luhn_ok(c) for c in cards) and rd._card(1000) is None
    assert not rd._aadhaar_ok(rd._CARD_PREFIX)
    ibans = [rd._iban(n) for n in (1, 2, 99, 10**15)]
    assert len(set(ibans)) == 4 and all(rd._iban_ok(i) and i.startswith("ZZ") for i in ibans)
    ips = [rd._ip(n) for n in range(1, 763)]
    assert len(set(ips)) == 762 and rd._ip(763) is None
    assert ips[253] == "192.0.2.254" and ips[254] == "198.51.100.1" and ips[-1] == "203.0.113.254"
    # Past the last surrogate a label falls back to numbered placeholders, still one per value.
    p = Pseudonymizer("surrogate")
    hosts = " ".join(f"10.0.{i // 250}.{i % 250 + 1}" for i in range(763))
    out = redact(hosts, None, p).split()
    assert out[0] == "192.0.2.1" and out[761] == "203.0.113.254" and out[762] == "[IP_763]"


# ---------- v0.4 compatibility ----------
def _corpus(n: int, seed: int) -> list[str]:
    """Identifiers glued to each other and to punctuation, digits and letters: where a replacement could change what a
    later pattern sees."""
    rng = random.Random(seed)
    pool = [
        *IDENTIFIERS.values(),
        "4111 1111 1111 1112",
        "98765 43210",
        "9876543210@ybl",
        "name@okicici",
        "react@latest",
        "bob@example.com",
        "+1 (415) 555-0132",
        "Bearer abcdefghijklmnopqrstu12345",
        "GB82 WEST 1234 5698 7654 32",
        "2026-10-20",
        "$1,299.00",
        "v0.3.0",
        "12345",
        "AKIAABCDEFGHIJKLMNOP",
        "1.2.3.4",
        "27AAPFU0939F1Z",
        "x",
        "Z",
    ]
    glue = ["", "", " ", "-", ".", "+", "@", ",", "(", ")", "[", "]", "1", "a", "A", "\n", "", "_", "/"]
    return ["".join(rng.choice(pool) + rng.choice(glue) for _ in range(rng.randint(1, 6))) for _ in range(n)]


def _first_pass(text: str, patterns: list[Any]) -> list[tuple[int, int, str]]:
    """v0.4's spans: each pattern's matches that pass its check, later patterns seeing the earlier ones' masked."""
    spans, masked = [], text
    for label, pattern, check in patterns:
        found = [(m.start(), m.end(), label) for m in pattern.finditer(masked) if check is None or check(m.group(0))]
        spans += found
        for start, end, _ in found:
            masked = masked[:start] + "\ue000" * (end - start) + masked[end:]
    return sorted(spans)


def _references(n: int, seed: int) -> list[str]:
    """Long numbers that are no identifier, as people type them: tracking, policy and invoice numbers of 5 to 8 groups
    of four digits (20 to 32 digits), also dashed, unspaced or with a word after them. About one window in ten passes
    Luhn or Verhoeff by chance, so a rule that tried more windows than v0.4 would cut them into pieces."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        groups = [f"{rng.randrange(10000):04d}" for _ in range(rng.randint(5, 8))]
        number = rng.choice([" ", " ", " ", "-", ""]).join(groups)
        out.append(
            rng.choice(["Ref {}.", "Tracking number {} shipped", "Policy {}", "UTR {} on 2026-10-20", "{}"]).format(number)
        )
    return out


def _run_end(text: str, at: int, label: str) -> int:
    """Where the run that continues at `at` ends: digits for a card, digits and capitals for an IBAN, one space or dash
    apart."""
    m = re.compile(r"(?:[ -]?\d)*" if label == "CARD" else r"(?:[ -]?[0-9A-Z])*").match(text, at)
    return m.end() if m else at


@pytest.mark.req("FR-13.7", "FR-4.7", "NFR-4")
def test_label_style_matches_v04():
    """The default style is v0.4's: the same nine rules (their SHA-256 as v0.4's BOMs carried it); on fuzzed text,
    exactly what v0.4's replacement pattern by pattern gives with today's rules, and exactly v0.4's output where there
    is neither a GSTIN nor a UPI id; on 8,000 long reference numbers, exactly v0.4's output. The one other difference,
    a card or IBAN with a short tail, is in `test_a_refused_match_no_longer_hides_a_card`."""
    from ratchet import bom

    rules = [[label, rx.pattern, rx.flags, getattr(check, "__name__", None)] for label, rx, check in V04_PATTERNS]
    assert hashlib.sha256(bom.canonical(rules)).hexdigest() == V04_RULES_SHA256
    texts = [*_corpus(3000, seed=7), *_corpus(1500, seed=8)]
    plain = 0
    for text in texts:
        assert rd._pattern_spans(text) == _first_pass(text, PATTERNS), text
        assert redact(text) == _sequential(text, PATTERNS), text
        if not {label for _, _, label in rd.spans(text)} & {"UPI", "GSTIN"}:
            plain += 1
            assert redact(text) == _sequential(text, V04_PATTERNS), text
        assert redact(text, None, Pseudonymizer("label")) == redact(text)
        # Every style finds the same spans: a numbered placeholder is a label placeholder with a number.
        numbered = redact(text, None, Pseudonymizer("numbered"))
        assert re.sub(r"\[([A-Z]+)_\d+\]", r"[\1]", numbered) == redact(text), text
    assert plain > 2000
    changed = 0
    for text in _references(8000, seed=9):
        out = redact(text)
        assert out == _sequential(text, V04_PATTERNS), text
        changed += out != text
    assert changed > 500  # v0.4's own single windows still find a chance card or Aadhaar now and then, as they did
    c: Counter[str] = Counter()
    assert redact("Mail jane.doe@acme.co.uk, PAN ABCPE1234F, call +1 (415) 555-0132 or 98765 43210.", c) == (
        "Mail [EMAIL], PAN [PAN], call [PHONE] or [PHONE]."
    )
    assert c == Counter(PHONE=2, EMAIL=1, PAN=1)


@pytest.mark.req("FR-4.7", "NFR-4", "INV-12")
def test_a_refused_match_no_longer_hides_a_card():
    """v0.4 tried each rule once per run: a card followed by its CVV or expiry is a longer run that fails Luhn, and the
    card stayed in the stored text. A refused card or IBAN match is now tried again shorter, and kept when 1 to 4
    characters of its run follow it; those stay as typed and no later rule reads them. Nothing else changes."""
    cases = {
        "card 4111 1111 1111 1111 123": "card [CARD] 123",  # the CVV
        "card 4111111111111111 12/27": "card [CARD] 12/27",  # the expiry
        "card 4111 1111 1111 1111 12 27": "card [CARD] 12 27",
        "4111 1111 1111 1111 5500 0000 0000 0004 99": "[CARD] [CARD] 99",  # two cards in a row, a CVV after the second
        "IBAN BE68 5390 0754 7034 2026": "IBAN [IBAN] 2026",  # a 16-character IBAN and the year after it
        "Pay to BE68 5390 0754 7034 FROM my account": "Pay to [IBAN] FROM my account",  # v0.4: BE68 [PHONE]
        "card 4111 1111 1111 1111 123.": "card [CARD] 123.",
        "5500 0000 0000 0004 99, GSTIN 27AAPFU0939F1ZV": "[CARD] 99, GSTIN [GSTIN]",
        "5500 0000 0000 0004 27AAPFU0939F1ZV": "[CARD] [GSTIN]",  # the card rule read the GSTIN's 27 as a group
        "4111 1111 1111 1111 12abc": "[CARD] 12abc",
    }
    for text, want in cases.items():
        assert _sequential(text, PATTERNS) != want  # v0.4 left an identifier in the text
        assert redact(text) == want, text
    # Everything else is v0.4's: a run with digits before the refused match, or more than 4 after the card, and a digit
    # run with no valid checksum part, stay as v0.4 left them (phones have no checksum and are never retried).
    for kept in (
        "4111 1111 1111 1111 1112",  # 20 digits: the card is v0.4's own first window
        "4111 1111 1111 1112",
        "Order 1234 5678 9012 3456 7891",
        "ISBN 978-3-16-148410-0",
        "x 4111 1111 1111 1111 123 4567",  # 7 digits after the card: a longer number
        "Pay 500 4111 1111 1111 1111 now",  # a number before it: v0.4's limit, kept
        "Aadhaar 2345 6789 0124 4111 1111 1111 1111",
        "call +1 (415) 555-0132 4111 1111 1111 1111.",
        "4111 1111 1111 1111 123.45.67.89",  # an IP address v0.4 found, not a CVV
        "4111 1111 1111 1111 123.456.7890",
        "2026 BE68 5390 0754 7034 1 123.456.7890",  # the card rule would start inside the word BE68
        "123.45.67.89 4111 1111 1111 1111",  # ... or inside the IP address before the card
        "+918-5943037390 6749",  # a phone number's country code: no card starts after a '+'
    ):
        assert redact(kept) == _sequential(kept, PATTERNS), kept
    assert redact("+91 98765 43210 2345 6789 0124") == "[PHONE] [AADHAAR]"
    # Fuzzed: wherever the output differs from v0.4's, it is that rule and nothing else.
    rng = random.Random(12)
    valid = ["4111 1111 1111 1111", "5500 0000 0000 0004", "4111111111111111", "4111-1111-1111-1111", "BE68 5390 0754 7034"]
    pieces = [*valid, "123", "12/27", "2026", "99", "1", "12 27", "12345", "98765 43210", "2345 6789 0124", "Order", "x"]
    pieces += ["27AAPFU0939F1ZV", "12abc", "123.456.7890", "123.45.67.89", "IBAN", "FROM", "BE68", "12.", "+91"]
    rescued = 0
    for _ in range(3000):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(1, 5)))
        old, new = set(_first_pass(text, PATTERNS)), set(rd._pattern_spans(text))
        if old == new:
            assert redact(text) == _sequential(text, PATTERNS), text
            continue
        rescued += 1
        runs = []
        for start, end, label in sorted(new - old):
            assert label in ("CARD", "IBAN") and CHECKS[label](text[start:end]), text
            # Nothing joins it to the text before (a span kept before it ends a run), nor its tail to the text after.
            before = text[max(0, start - 2) : start]
            kept_before = any(s <= start - 2 < e for s, e, _ in new)
            assert kept_before or not re.search(r"[\w+]$|\w[.-]$|\d $", before), text
            tail = text[end : _run_end(text, end, label)]
            after = text[end + len(tail) : end + len(tail) + 2]
            if re.fullmatch(r" \w+", tail) and re.match(r"\w", after):  # the start of the next word, read whole later
                tail = ""
            else:  # a short tail of the run that nothing joins to the text after it
                assert 1 <= sum(ch.isalnum() for ch in tail) <= 4 and not re.match(r"\w|[.-]\w", after), text
            runs.append((start, end + len(tail)))
        for start, end, _ in old - new:  # a v0.4 span is replaced only inside a rescued card's run
            assert any(s <= start and end <= e for s, e in runs), text
    assert rescued > 100
    # Numbered and surrogate placeholders find the same.
    assert redact("card 4111 1111 1111 1111 123", None, Pseudonymizer("numbered")) == "card [CARD_1] 123"
    out = redact("card 4111 1111 1111 1111 123", None, Pseudonymizer("surrogate"))
    assert re.fullmatch(r"card 400000000000\d{4} 123", out) and redact(out) == out


def _realistic(n: int, seed: int, pool: list[str]) -> list[str]:
    """Identifiers and ordinary words between the separators people type. Two numbers are never one space apart: the
    rules read them as one digit run, as v0.4's did (a card after an Aadhaar, two phone numbers), and a second pass,
    which sees them apart, can then find what the first could not."""
    rng = random.Random(seed)
    glue = [" ", ", ", ". ", "\n", " (", ") ", ": ", "; ", '"', " / ", " - ", "\t", " and ", ' "', '" ', "<", "> "]
    out = []
    for _ in range(n):
        parts = [rng.choice(pool) for _ in range(rng.randint(1, 8))]
        text = ""
        for i, part in enumerate(parts):
            sep = rng.choice(glue)
            if sep == " " and i + 1 < len(parts) and part[-1].isdigit() and parts[i + 1][0].isdigit():
                sep = ", "
            text += part + sep
        out.append(text)
    return out


@pytest.mark.req("FR-13.7", "FR-4.7")
def test_redaction_is_idempotent_on_realistic_text():
    """A stored example redacted again is unchanged, in every style: no placeholder or surrogate is read as an
    identifier, and nothing the first pass left is found by the second. Two numbers typed one space apart are the known
    exception, left out of the corpus (`_realistic`): the rules read them as one digit run, as v0.4's did, and so miss
    a card after an Aadhaar, or read a surrogate card and the number after it as one phone; a second pass, which sees
    a placeholder or a surrogate between them, can then find more."""
    pool = [
        *IDENTIFIERS.values(),
        "4111 1111 1111 1112",  # 16 digits that fail Luhn
        "5500 0000 0000 0004",
        "98765 43210",
        "9876543210@ybl",
        "name@okicici",
        "react@latest",
        "bob@example.com",
        "+1 (415) 555-0132",
        "(415) 555-0132",
        "Bearer abcdefghijklmnopqrstu12345",
        "GB82 WEST 1234 5698 7654 32",
        "10.0.0.7",
        "1.2.3.4",
        "27AAPFU0939F1Z",
        "AKIAABCDEFGHIJKLMNOP",
        "Rs. 1,23,456.50",
        "2026-10-20",
        "$1,299.00",
        "v0.3.0",
        "12345",
        "Order",
        "the",
        "person1@example.com",  # surrogates already in the text
        "+12025550100",
        "ZZ39000000000000001",
    ]
    for style in rd.STYLES:
        for text in _realistic(3000, 11, pool):
            once = redact(text, None, Pseudonymizer(style))
            assert redact(once, None, Pseudonymizer(style)) == once, (style, text)


@pytest.mark.req("FR-13.7", "FR-4.7")
def test_default_settings_store_v04_examples():
    """RATCHET_REDACT_STYLE unset: the stored example is byte for byte what v0.4 stored."""
    answer = '{"category": "account", "priority": "high", "reply": "Mailed you at bob@example.com"}'
    led, r, _ = _cold(answer)
    assert Settings().redact_style == "label" and r.redact_style == "label" and r.detector is None
    r.handle([{"role": "user", "content": "I'm bob@example.com and ana@x.io, phone +44 20 7946 0958"}], "ticket-triage")
    stored, completion = _stored(led)
    assert stored[0]["content"] == "I'm [EMAIL] and [EMAIL], phone [PHONE]"
    assert json.loads(completion)["reply"] == "Mailed you at [EMAIL]"


# ---------- Indian identifiers ----------
@pytest.mark.req("FR-13.7", "FR-4.7", "NFR-4")
def test_gstin_and_upi_ids_are_redacted():
    c: Counter[str] = Counter()
    text = "GSTIN 27AAPFU0939F1ZV, pay name@okicici or 9876543210@ybl, mail a.b@okicici.com"
    assert redact(text, c) == "GSTIN [GSTIN], pay [UPI] or [UPI], mail [EMAIL]"
    assert c == Counter(GSTIN=1, UPI=2, EMAIL=1)
    assert redact("29ABCPE1234FAZ5 and 07AAACB2894G1Z5.") == "[GSTIN] and [GSTIN]."  # entity A; a company's PAN
    assert redact("Pay to priya.p-1_x@ybl.") == "Pay to [UPI]."  # a sentence's full stop is not a domain
    for kept in (
        "npm i react@latest and lodash@next",  # package tags, not payment handles
        "git checkout feature@main",
        "a+b@ybl",  # '+' is not in a UPI id: no partial match either
        "x@ybl",  # one character before '@'
        "user@ okicici",
        "@okicici",
        "27AAPFU0939F1Z",  # 14 characters: not a GSTIN, and its PAN is glued to digits
    ):
        assert redact(kept) == kept, kept
    assert redact("ABCPE1234F") == "[PAN]"


# ---------- the detector plugin ----------
@pytest.mark.req("FR-13.7", "FR-4.7", "NFR-4", "INV-12")
def test_redact_plugin_detector_applied():
    detector = load_detector("redact_plugins:names")
    redact_plugins.CALLS.clear()
    c: Counter[str] = Counter()
    p = Pseudonymizer("numbered", detector)
    text = "Alice Smith (alice@x.io) asked Bob Jones; Alice Smith again"
    assert redact(text, c, p) == "[NAME_1] ([EMAIL_1]) asked [NAME_2]; [NAME_1] again"
    assert c == Counter(NAME=3, EMAIL=1) and redact_plugins.CALLS == [text]
    assert redact(text, None, Pseudonymizer("label", detector)) == "[NAME] ([EMAIL]) asked [NAME]; [NAME] again"
    assert redact(text, None, Pseudonymizer("surrogate", detector)).startswith("[NAME_1] (person1@example.com)")
    assert redact("", None, Pseudonymizer("label", detector)) == ""  # an empty text is never sent to the plugin
    assert redact_plugins.CALLS == [text, text, text]
    # A dotted path after the colon; a label in lower case is upper-cased.
    assert redact("Hi Priya Patel", None, Pseudonymizer("label", load_detector("redact_plugins:Holder.detect"))) == "Hi [NAME]"
    assert redact("Hi Priya Patel", None, Pseudonymizer("label", load_detector(" redact_plugins:lower_label "))) == (
        "Hi [PERSON]"
    )
    # Offsets of any integer type (numpy's, from an NER model) are read as integers.
    assert redact("Hi Priya Patel", None, Pseudonymizer("numbered", load_detector("redact_plugins:index_offsets"))) == (
        "Hi [NAME_1]"
    )


@pytest.mark.req("FR-13.7")
def test_detector_spans_merge_with_patterns_earlier_then_longer():
    """Of overlapping spans the earlier wins, then the longer, then the pattern's; the winner's placeholder covers every
    span it overlaps, so no part of a name or an email is left behind."""
    det = load_detector("redact_plugins:overlapping")
    text = "Alice Smith ana@x.io tail"
    # NAME [0, 11) and OTHER [2, 19) overlap; OTHER also overlaps EMAIL [12, 20): one NAME placeholder covers all.
    assert rd.spans(text, det) == [(0, 20, "NAME")]
    assert redact(text, None, Pseudonymizer("label", det)) == "[NAME] tail"
    merged = rd._merge([(5, 10, "EMAIL")], [(5, 8, "NAME"), (5, 12, "ORG"), (20, 22, "NAME"), (12, 14, "X")])
    assert merged == [(5, 12, "ORG"), (12, 14, "X"), (20, 22, "NAME")]  # longer wins at the same start; touching stays
    assert rd._merge([(5, 10, "EMAIL")], [(5, 10, "NAME")]) == [(5, 10, "EMAIL")]  # a tie goes to the pattern
    assert rd._merge([(5, 10, "EMAIL")], [(3, 6, "NAME")]) == [(3, 10, "NAME")]  # the earlier start wins


@pytest.mark.req("FR-13.7", "NFR-4")
@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        ("no_such_module_for_ratchet:find", "cannot import 'no_such_module_for_ratchet'"),
        ("redact_plugins:missing", "has no 'missing'"),
        ("redact_plugins:Holder.missing", "has no 'Holder.missing'"),
        ("redact_plugins:NOT_CALLABLE", "is not callable"),
        ("redact_plugins", "must be 'module:function'"),
        (":names", "must be 'module:function'"),
        ("redact_plugins:", "must be 'module:function'"),
        ("redact_plugins:broken_probe", "failed on an empty text: the redaction plugin raised ValueError"),
        ("redact_plugins:returns_none", "failed on an empty text: the redaction plugin raised TypeError"),
    ],
)
def test_missing_plugin_fails_startup(spec, problem, gw_env):
    with pytest.raises(ValueError, match=re.escape(problem)):
        load_detector(spec)
    with pytest.raises(ValueError, match=re.escape(problem)):
        Ratchet(_settings(redact_plugin=spec), Ledger(":memory:"), RecordingProvider("x"), lambda v: (None, "x"))
    gw = gw_env()
    with pytest.raises(ValueError, match="RATCHET_REDACT_PLUGIN"):
        gw.Runtime(Settings(mode="mock", redact_plugin=spec, mock_warmup_days=0))


@pytest.mark.req("FR-13.7", "NFR-4")
def test_bad_style_fails_startup_and_style_is_read_loosely(monkeypatch):
    assert rd.STYLES == ("label", "numbered", "surrogate")  # test_original_values_never_stored lists them literally
    for bad in ("hashed", "labels", 3):
        with pytest.raises(ValueError, match="RATCHET_REDACT_STYLE"):
            Ratchet(_settings(redact_style=bad), Ledger(":memory:"), RecordingProvider("x"), lambda v: (None, "x"))
    with pytest.raises(ValueError, match="RATCHET_REDACT_PLUGIN"):
        Ratchet(_settings(redact_plugin=None), Ledger(":memory:"), RecordingProvider("x"), lambda v: (None, "x"))
    assert _settings(redact_style=" Numbered ").redaction_style() == "numbered"
    assert _settings(redact_style="").redaction_style() == "label"
    monkeypatch.setenv("RATCHET_REDACT_STYLE", "SURROGATE")
    monkeypatch.setenv("RATCHET_REDACT_PLUGIN", "redact_plugins:names")
    s = Settings()
    assert (s.redact_style, s.redact_plugin, s.redaction_style()) == ("SURROGATE", "redact_plugins:names", "surrogate")


@pytest.mark.req("FR-13.7", "INV-12", "INV-16")
@pytest.mark.parametrize(
    "plugin",
    ["flaky", "out_of_range", "bad_label", "not_triples", "bool_offsets", "float_offsets", "empty_span"],
)
def test_a_failing_detector_stores_nothing_and_never_leaks_the_text(plugin):
    led, r, _ = _cold(json.dumps({"contact": "Alice Smith"}), redact_plugin=f"redact_plugins:{plugin}")
    _extract_task()
    msgs = [{"role": "user", "content": "Contact Alice Smith at alice@x.io"}]
    out = r.handle(msgs, EXTRACT)
    assert out.text == json.dumps({"contact": "Alice Smith"}) and not out.stored  # served, not stored
    assert led.examples() == []
    (event,) = [e for e in led.events() if e["kind"] == "skip_example"]
    assert event["detail"] == f"{EXTRACT}: redaction failed"
    assert not any("Alice" in v or "alice@x.io" in v for v in _dump(led))
    with pytest.raises(RedactionError) as caught:
        redact("Alice Smith", None, Pseudonymizer("label", r.detector))
    assert "Alice" not in str(caught.value) and caught.value.__cause__ is None  # the plugin's message is not kept


# ---------- what is stored ----------
@pytest.mark.req("FR-13.7", "NFR-4", "INV-12")
@pytest.mark.parametrize("style", ["label", "numbered", "surrogate"])
def test_original_values_never_stored(style):
    """No original value reaches any table, in any style, from the prompt, the answer or the stored conditions."""
    values = [*IDENTIFIERS.values(), "Alice Smith", "Priya Patel", "bo@y.io"]
    answer = json.dumps({"contact": "Alice Smith", "note": "wrote to bo@y.io, UPI jane.doe@okicici, GSTIN 27AAPFU0939F1ZV"})
    led, r, teacher = _cold(answer, redact_style=style, redact_plugin="redact_plugins:names")
    _extract_task()
    from ratchet.providers import GenParams

    for i, (label, value) in enumerate(IDENTIFIERS.items()):
        msgs = [
            {"role": "system", "content": "Find the contact. Never mention Priya Patel."},
            {"role": "user", "content": f"Ticket {i}: {label} {value}; ask Alice Smith or bo@y.io"},
        ]
        assert r.handle(msgs, EXTRACT, params=GenParams(stop=("bo@y.io",))).stored
    assert len(led.examples()) == len(IDENTIFIERS)
    assert len(teacher.calls) == len(IDENTIFIERS)
    text = "\n".join(_dump(led))
    for value in values:
        assert value not in text, value
    stored, completion = json.loads(led.examples()[0]["messages"]), led.examples()[0]["completion"]
    if style != "label":  # the answer names the prompt's placeholders
        assert json.loads(completion)["contact"] == "[NAME_2]" and "[NAME_2]" in stored[1]["content"]
        assert "[NAME_1]" in stored[0]["content"]  # Priya Patel came first


@pytest.mark.req("FR-13.7", "FR-2.15", "INV-12")
def test_task_spec_privacy_redact_style_overrides_the_default():
    spec = {"name": EXTRACT, "fields": {"contact": {"type": "text"}}, "privacy": {"redact_style": "numbered"}}
    task = taskspec.parse_spec(spec, "api")
    assert task.redact_style == "numbered"
    assert taskspec.document(task)["privacy"] == {"redact_style": "numbered"}
    assert taskspec.parse_spec(taskspec.document(task), "api") == task
    plain = taskspec.parse_spec({**spec, "privacy": {}}, "api")
    assert plain.redact_style is None and taskspec.document(plain)["privacy"] == {}
    assert plain.spec_hash == task.spec_hash  # how examples are stored is not how answers compare: no recalibration
    assert taskspec.parse_spec({**spec, "privacy": {"redact_style": None}}, "api").redact_style is None
    with pytest.raises(taskspec.SpecError) as caught:
        taskspec.parse_spec({**spec, "privacy": {"redact_style": "hashed", "keep": 1}}, "api")
    assert caught.value.errors == [
        "privacy.redact_style: must be one of label, numbered, surrogate or null",
        "privacy.keep: unknown key; use redact_style",
    ]
    with pytest.raises(taskspec.SpecError, match=r"privacy: must be an object"):
        taskspec.parse_spec({**spec, "privacy": "numbered"}, "api")
    with pytest.raises(ValueError, match="privacy.redact_style"):
        tasks.Task("t", redact_style="hashed")
    # `extends` merges `privacy` by key; null removes the base's style.
    tasks.register(task)
    child = taskspec.parse_spec({"name": "child", "extends": EXTRACT}, "api")
    assert child.redact_style == "numbered"
    unset = taskspec.parse_spec({"name": "child", "extends": EXTRACT, "privacy": {"redact_style": None}}, "api")
    assert unset.redact_style is None
    # The cascade stores this task's examples numbered while the deployment's default stays label.
    led, r, _ = _cold(json.dumps({"contact": "bo@y.io"}))
    r.handle([{"role": "user", "content": "ana@x.io or bo@y.io?"}], EXTRACT)
    r.handle([{"role": "user", "content": "ana@x.io or bo@y.io?"}], "other-task")
    rows = {row["task"]: row for row in led.examples()}
    assert json.loads(rows[EXTRACT]["messages"])[0]["content"] == "[EMAIL_1] or [EMAIL_2]?"
    assert rows[EXTRACT]["completion"] == json.dumps({"contact": "[EMAIL_2]"})
    assert json.loads(rows["other-task"]["messages"])[0]["content"] == "[EMAIL] or [EMAIL]?"


@pytest.mark.req("FR-13.7", "FR-2.15")
def test_admin_api_sets_a_tasks_style_without_recalibrating(gw_env):
    """A style is how examples are stored, not how answers compare: changing it through the admin API keeps the spec's
    hash, so the task is not sent back to the teacher, and the event says what kind of change it was."""
    gw = gw_env(RATCHET_ADMIN_KEYS="")
    c = TestClient(gw.app)
    spec = {"fields": {"contact": {"type": "text"}}}
    first = c.put(f"/ratchet/tasks/{EXTRACT}", json=spec).json()
    assert first["created"] and first["task"]["privacy"] == {}
    second = c.put(f"/ratchet/tasks/{EXTRACT}", json={**spec, "privacy": {"redact_style": "numbered"}}).json()
    assert second["changed"] and second["spec_hash"] == first["spec_hash"]
    assert second["task"]["privacy"] == {"redact_style": "numbered"} and not second["task"]["recalibrating"]
    assert tasks.get(EXTRACT).redact_style == "numbered"
    assert c.get("/ratchet/stats").json()["privacy"]["redaction_task_styles"] == {EXTRACT: "numbered"}
    (event,) = [e["detail"] for e in gw.rt.ledger.events() if e["kind"] == "task_changed"]
    assert event == f"{EXTRACT}: spec {first['spec_hash']} kept; description, clients, match or privacy by mock-admin"
    bad = c.put(f"/ratchet/tasks/{EXTRACT}", json={**spec, "privacy": {"redact_style": "hashed"}})
    assert bad.status_code == 422 and bad.json()["errors"] == [
        "privacy.redact_style: must be one of label, numbered, surrogate or null"
    ]


@pytest.mark.req("FR-13.7")
def test_changing_the_style_stores_a_new_example_once():
    """The hash covers the stored text, so the same request stored under another style is a new example: a one-time
    dedup break for new rows, documented in ADR 0082. Under one style the same request is one example seen twice."""
    led = Ledger(":memory:")
    msgs = [{"role": "user", "content": "I'm ana@x.io"}]
    for style in ("label", "label", "numbered", "numbered"):
        Ratchet(_settings(redact_style=style), led, RecordingProvider("ok"), lambda v: (None, "x"), seed=1).handle(msgs, "t")
    rows = led.examples()
    assert [json.loads(r["messages"])[0]["content"] for r in rows] == ["I'm [EMAIL]", "I'm [EMAIL_1]"]
    assert [r["seen"] for r in rows] == [2, 2]


@pytest.mark.req("FR-13.3", "FR-13.7", "INV-12")
def test_an_erasure_holds_under_every_style_and_plugin():
    """Erasure tombstones the example's hash, which covers the redacted text, so another style or a detector plugin
    would hash the same request differently. Each example stored otherwise than in the default (label style, no plugin)
    also records its key under the default redaction (`canon_hash`), and an erasure tombstones both: an example erased
    under one setting is refused under every other, in either direction."""
    from ratchet import privacy
    from ratchet.ledger import example_hash
    from ratchet.providers import GenParams

    answer = json.dumps({"contact": "bo@y.io"})
    msgs = [{"role": "user", "content": "Sales: ana@x.io. Billing: bo@y.io. Alice Smith here."}]
    params = GenParams(stop=("ana@x.io",))
    _extract_task()
    settings = [("label", ""), ("numbered", ""), ("surrogate", ""), ("label", "redact_plugins:names")]
    settings.append(("numbered", "redact_plugins:names"))
    for erased_under in settings:
        led = Ledger(":memory:")

        def store(style: str, plugin: str, led: Ledger = led) -> bool:
            r = Ratchet(
                _settings(redact_style=style, redact_plugin=plugin), led, RecordingProvider(answer), lambda v: (None, "x")
            )
            return r.handle(msgs, EXTRACT, params=params).stored

        assert store(*erased_under)
        (row,) = led.examples()
        canon = example_hash(EXTRACT, redact_messages(msgs), {"stop": ["[EMAIL]"]})
        assert row["canon_hash"] == (None if erased_under == ("label", "") else canon), erased_under
        out = privacy.erase(led, hashes=[row["hash"]], actor="admin", reason="gdpr request", dry_run=False)
        assert out["purged"] == 1
        keys = {r[0] for r in led.db.execute("SELECT key FROM tombstones")}
        assert keys == {row["hash"], canon}, erased_under
        for again in settings:
            assert not store(*again), (erased_under, again)
        assert len(led.examples()) == 1 and led.examples()[0]["purged_ts"] is not None
        assert int(led.db.execute("SELECT SUM(suppressed) FROM tombstones").fetchone()[0]) == len(settings)
    # The key is stored only when it differs, so rows under the default settings are as before.
    led, r, _ = _cold(answer)
    r.handle(msgs, EXTRACT)
    assert led.examples()[0]["canon_hash"] is None


@pytest.mark.req("FR-4.7", "NFR-4", "INV-12", "INV-3")
def test_long_reference_numbers_store_as_in_v04():
    """A tracking number in the prompt and the same digits in the answer are stored as v0.4 stored them: neither is cut
    into chance checksum windows, so the training target never holds digits its prompt lacks, and the example hash, the
    split and the frequent-request count are v0.4's."""
    from ratchet.ledger import example_hash

    rng = random.Random(3)
    for i in range(40):
        groups = [f"{rng.randrange(10000):04d}" for _ in range(rng.randint(5, 8))]
        if i == 0:
            groups = ["1033", "5827", "9625", "0562", "5008", "5954", "9163"]  # the review's example
        prompt = f"Where is parcel {' '.join(groups)}? Return its tracking number."
        answer = json.dumps({"tracking": "".join(groups)})
        led, r, _ = _cold(answer)
        assert r.handle([{"role": "user", "content": prompt}], "track").stored
        (row,) = led.examples()
        want = [{"role": "user", "content": _sequential(prompt, V04_PATTERNS)}]
        assert json.loads(row["messages"]) == want, prompt
        assert row["completion"] == _sequential(answer, V04_PATTERNS)
        assert row["hash"] == example_hash("track", want)
        if i == 0:
            assert want[0]["content"] == prompt and row["completion"] == answer and row["redactions"] == 0


@pytest.mark.req("FR-13.7", "INV-12", "INV-3")
def test_stored_conditions_are_numbered_in_key_order():
    """Two requests whose conditions differ only in the order of their keys are one example: the stored conditions are
    numbered in key order, as the example hash sorts them, and the copy keeps the client's order."""
    from ratchet.ledger import example_hash

    a = {"y": "b@b.io", "x": "a@a.io", "z": [{"d": "d@d.io", "c": "c@c.io"}, "b@b.io"], "n": 1}
    b = {"n": 1, "z": [{"c": "c@c.io", "d": "d@d.io"}, "b@b.io"], "x": "a@a.io", "y": "b@b.io"}
    out_a, out_b = redact_json(a, None, Pseudonymizer("numbered")), redact_json(b, None, Pseudonymizer("numbered"))
    want = {"n": 1, "x": "[EMAIL_1]", "y": "[EMAIL_2]", "z": [{"c": "[EMAIL_3]", "d": "[EMAIL_4]"}, "[EMAIL_2]"]}
    assert out_a == out_b == want
    assert list(out_a) == ["y", "x", "z", "n"] and list(out_b) == ["n", "z", "x", "y"]
    assert list(out_a["z"][0]) == ["d", "c"]
    msgs = [{"role": "user", "content": "mail b@b.io"}]
    hashes = set()
    for schema in (a, b):
        p = Pseudonymizer("numbered")
        stored = redact_messages(msgs, None, p)
        fmt = {"type": "json_schema", "json_schema": {"name": "s", "schema": redact_json(schema, None, p)}}
        hashes.add(example_hash("t", stored, {"response_format": fmt}))
    assert len(hashes) == 1


@pytest.mark.req("FR-13.7", "INV-12", "INV-3")
def test_the_example_hash_never_depends_on_the_answer():
    """The prompt and the stored conditions are numbered before the answer, so the same request is one example however
    the teacher answers (the hash covers the messages and the conditions; INV-3 counts its repeats). Numbering the
    answer first would give the stop string [EMAIL_3] after an answer naming a new address, and [EMAIL_2] after one that
    does not."""
    from ratchet.providers import GenParams

    answers = iter(["Reply to zed@z.io", "Reply to ana@x.io", "Reply to zed@z.io and ana@x.io"])
    led, r, _ = _cold(lambda msgs, model: next(answers), redact_style="numbered")
    msgs = [{"role": "user", "content": "I'm ana@x.io"}]
    for _ in range(3):
        assert r.handle(msgs, "t", params=GenParams(stop=("cy@z.io",))).stored
    (row,) = led.examples()
    assert row["seen"] == 3
    assert json.loads(row["messages"])[0]["content"] == "I'm [EMAIL_1]"
    assert json.loads(row["params"]) == {"stop": ["[EMAIL_2]"]}
    assert row["completion"] == "Reply to [EMAIL_3]"  # the first answer's; a duplicate keeps the stored answer


@pytest.mark.req("FR-13.7", "INV-12")
def test_concurrent_examples_never_share_placeholders():
    """Scout calls the cascade from several threads: each example gets its own Pseudonymizer, so every example starts
    at [EMAIL_1] and none sees another's values."""
    led, r, _ = _cold(lambda msgs, model: json.dumps({"contact": msgs[0]["content"].split()[-1]}), redact_style="numbered")
    _extract_task()
    errors: list[BaseException] = []

    def ask(i: int) -> None:
        try:
            r.handle([{"role": "user", "content": f"Ticket {i}: first{i}@x.io then second{i}@y.io"}], EXTRACT)
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rows = led.examples()
    assert len(rows) == 16
    for row in rows:
        content = json.loads(row["messages"])[0]["content"]
        assert content.endswith(": [EMAIL_1] then [EMAIL_2]") and row["completion"] == json.dumps({"contact": "[EMAIL_2]"})


# ---------- the gateway ----------
@pytest.mark.req("FR-13.7", "FR-4.7")
def test_redact_preview_shows_the_style_and_takes_style(gw_env):
    gw = gw_env(
        RATCHET_REDACT_STYLE="numbered",
        RATCHET_REDACT_PLUGIN="redact_plugins:names",
        RATCHET_API_KEYS=f"acme:{ACME_KEY}",
        RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}",
    )
    c = TestClient(gw.app)
    body = {"text": "Alice Smith: ana@x.io, bo@y.io, ana@x.io"}
    keyed = {"Authorization": f"Bearer {ACME_KEY}"}
    r = c.post("/ratchet/redact", json=body, headers=keyed).json()
    assert r == {
        "text": "[NAME_1]: [EMAIL_1], [EMAIL_2], [EMAIL_1]",
        "found": {"NAME": 1, "EMAIL": 3},
        "enabled": True,
        "style": "numbered",
        "plugin": True,
        "plugin_skipped": False,
    }
    assert c.post("/ratchet/redact", json=body, headers={"X-API-Key": ADMIN_KEY}).json() == r  # an admin key too
    # The endpoint is public: without a valid key the plugin does not run, and the response says so.
    for headers in ({}, {"Authorization": "Bearer not-a-key"}):
        anon = c.post("/ratchet/redact", json=body, headers=headers)
        assert anon.status_code == 200 and anon.json()["text"] == "Alice Smith: [EMAIL_1], [EMAIL_2], [EMAIL_1]"
        assert (anon.json()["plugin"], anon.json()["plugin_skipped"]) == (False, True)
    label = c.post("/ratchet/redact?style=label", json=body, headers=keyed).json()
    assert label["text"] == "[NAME]: [EMAIL], [EMAIL], [EMAIL]"
    out = c.post("/ratchet/redact?style=Surrogate", json=body, headers=keyed).json()
    assert out["text"] == "[NAME_1]: person1@example.com, person2@example.com, person1@example.com"
    assert out["style"] == "surrogate"
    bad = c.post("/ratchet/redact?style=hashed", json=body)
    assert bad.status_code == 400 and bad.json() == {"detail": "style must be one of label, numbered, surrogate"}
    stats = c.get("/ratchet/stats").json()["privacy"]
    assert (stats["redaction_style"], stats["redaction_plugin"], stats["redaction_task_styles"]) == ("numbered", True, {})
    tasks.register(tasks.Task(EXTRACT, source="file", redact_style="surrogate"))
    assert c.get("/ratchet/stats").json()["privacy"]["redaction_task_styles"] == {EXTRACT: "surrogate"}


@pytest.mark.req("FR-13.7")
def test_redact_preview_reports_a_failing_plugin_without_the_text(gw_env):
    gw = gw_env(RATCHET_REDACT_PLUGIN="redact_plugins:flaky", RATCHET_ADMIN_KEYS=f"ops:{ADMIN_KEY}")
    c = TestClient(gw.app, raise_server_exceptions=False)
    r = c.post("/ratchet/redact", json={"text": "Alice Smith"}, headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    assert r.status_code == 500 and r.json() == {"detail": "the redaction plugin raised RuntimeError"}
    # A caller without a key never reaches the plugin, so a failing plugin cannot turn every public preview into a 500.
    anon = c.post("/ratchet/redact", json={"text": "Alice Smith"})
    assert anon.status_code == 200 and (anon.json()["plugin"], anon.json()["plugin_skipped"]) == (False, True)
    plain = TestClient(gw_env(RATCHET_REDACT_PLUGIN="").app).post("/ratchet/redact", json={"text": "Alice Smith"})
    assert plain.json()["text"] == "Alice Smith"  # patterns alone find no names
    assert (plain.json()["plugin"], plain.json()["plugin_skipped"]) == (False, False)


@pytest.mark.req("FR-13.7", "INV-12")
def test_gateway_stores_numbered_examples_end_to_end(gw_env):
    gw = gw_env(RATCHET_REDACT_STYLE="numbered", RATCHET_UNKNOWN_TASKS="distill")
    teacher = RecordingProvider(json.dumps({"contact": "bo@y.io"}))
    gw.rt.ratchet.teacher = teacher
    c = TestClient(gw.app)
    body = {"messages": [{"role": "user", "content": "ana@x.io or bo@y.io?"}]}
    r = c.post(CHAT, json=body, headers={"X-Ratchet-Task": "x-extract"})
    assert r.status_code == 200
    (row,) = [e for e in gw.rt.ledger.examples() if e["task"] == "x-extract"]
    assert json.loads(row["messages"])[0]["content"] == "[EMAIL_1] or [EMAIL_2]?"
    assert row["completion"] == json.dumps({"contact": "[EMAIL_2]"})


# ---------- linear time (the unauthenticated /ratchet/redact and every stored example run these patterns) ----------
_REDOS_N = 120_000
_REDOS_BUDGET_S = 3.0  # a linear scan takes about 0.01 to 0.2 s here; the unbounded EMAIL pattern took 20 to 30 s


def _adversarial() -> list[str]:
    n = _REDOS_N
    return [
        "a" * n,
        "a." * (n // 2),
        "a@" * (n // 2),
        "a@a." * (n // 4),
        "a-" * (n // 2),
        "1 " * (n // 2),
        "1-" * (n // 2),
        "1." * (n // 2),
        "1" * n,
        "A" * n,
        "A1" * (n // 2),
        "AB12" + " ABCD" * (n // 5),
        "+1 " + "1 " * (n // 2),
        "(" + "1" * n,
        "@" * n,
        "x" * n + "@",
        "x@" + "y." * (n // 2),
        "x@" + "y-" * (n // 2),
        "x@y" + ".y" * (n // 2),
        "x@y-y-y-y-" * (n // 10),
        "12AAAA1234A" * (n // 11),
        "sk-" + "a" * n,
        "Bearer " + "a" * n,
        "a._%+-" * (n // 5),
        ("a" * 60 + "@") * (n // 61),
        ("a" * 64 + "@" + "b" * 63 + ".") * (n // 130),
        ("a" * 255 + " ") * (n // 256),
    ]


@pytest.mark.req("NFR-5", "FR-4.7")
def test_every_pattern_scans_adversarial_input_in_linear_time():
    """Each redaction pattern, and `redact` as a whole, finishes 120,000 characters of hostile input (long runs of address,
    digit and separator characters) within a fixed budget: no pattern backtracks quadratically."""
    import time

    for label, pattern, _check in PATTERNS:
        for text in _adversarial():
            start = time.perf_counter()
            for _ in pattern.finditer(text):
                pass
            took = time.perf_counter() - start
            assert took < _REDOS_BUDGET_S, (label, text[:16], round(took, 2))
    for text in _adversarial():
        start = time.perf_counter()
        redact(text)
        took = time.perf_counter() - start
        assert took < _REDOS_BUDGET_S * 3, ("redact", text[:16], round(took, 2))


@pytest.mark.req("FR-4.7")
def test_email_pattern_still_finds_addresses_and_cuts_a_long_local_part():
    found = Counter()
    assert redact("write to bob.smith+tag@mail.example.co.uk now", found) == "write to [EMAIL] now" and found["EMAIL"] == 1
    assert redact("a@b.io, c@d.org", Counter()) == "[EMAIL], [EMAIL]"
    # A local part over 64 characters is not an address (RFC 5321), as it never was for a rule that needs a word boundary first.
    assert redact("x" * 100 + "@host.example", Counter()) == "x" * 100 + "@host.example"
    assert redact("x" * 64 + "@host.example", Counter()) == "[EMAIL]"
