"""Field comparators and task specs (FR-2.14, ADR 0021): one positive and one negative case per field type, the v0.4
`compare_keys` rule kept byte for byte, pairwise confidence, required fields and vocabularies, and what the router,
the golden eval and the dataset do with an answer the task's spec refuses."""

import dataclasses
import json
import math
import random
import time
from collections import Counter
from types import SimpleNamespace

import pytest
from fakes import OPEN_TEACHER, REJECTED_EVAL, RecordingEvaluator, RecordingProvider, RecordingTrainer, _audit, _version

from ratchet import compare, licenses, similarity, tasks, taskspec
from ratchet.compare import INVALID, FieldSpec
from ratchet.confidence import majority, score
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.nightschool.orchestrate import run_night
from ratchet.providers import COMPLETE, Completion
from ratchet.release import ReleaseGate
from ratchet.router import Ratchet
from sim import research, tickets

TRIAGE = tasks.get("ticket-triage")
VALID = json.dumps({"category": "billing", "priority": "high", "reply": "Checking your charges now."})
OUT_OF_VOCAB = json.dumps({"category": "billing inquiry", "priority": "high", "reply": "Checking."})
NO_PRIORITY = json.dumps({"category": "billing", "reply": "Checking."})


def one(f: FieldSpec, *, fmt: str | None = None) -> tasks.Task:
    """A task with the single field `f` (scored on 3 samples when it is a similarity field, which needs 2 or more)."""
    return tasks.Task("t", fields=(f,), format=fmt, policy={"samples": 3} if f.type == "similarity" else {})


def comp(text, p=0.9, finish_reason="stop", n=4):
    return Completion(text, "m", 10, n, 1.0, [math.log(p)] * n if p is not None else [], finish_reason=finish_reason)


def agree(f: FieldSpec, a, b) -> bool:
    """Whether two raw values of the field agree, as answers `{"x": a}` and `{"x": b}` of a task with that field."""
    t = one(f)
    return t.agree(json.dumps({f.path: a}), json.dumps({f.path: b}))


# ---------- one positive and one negative case per field type ----------
@pytest.mark.req("FR-2.14", "FR-2.6")
def test_exact_is_the_v04_rule():
    f = FieldSpec("x", "exact")
    assert agree(f, " Billing ", "billing") and agree(f, [1, 2], [1, 2]) and agree(f, {"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not agree(f, 1, 1.0) and not agree(f, True, 1) and not agree(f, [1, 2], [2, 1]) and not agree(f, "1", 1)


@pytest.mark.req("FR-2.14")
def test_label_normalises_and_checks_its_vocabulary():
    f = FieldSpec("x", "label", values=("very high", "low"))
    assert agree(f, "Very-High.", "very_high") and agree(f, "ＶＥＲＹ  HIGH!", "very high")  # NFKC, case, separators
    assert not agree(f, "very high", "low")
    assert one(f).key('{"x": "medium"}') is INVALID  # outside the vocabulary
    assert one(FieldSpec("x", "label")).valid('{"x": "anything"}')  # no vocabulary: any label
    assert compare.label_text("c#") != compare.label_text("c")  # only sentence punctuation is dropped


@pytest.mark.req("FR-2.14")
def test_label_aliases():
    f = FieldSpec("priority", "label", values=tickets.PRIORITIES, aliases={"P1": "urgent", "sev 2": "high"})
    assert agree(f, "p1", "urgent") and agree(f, "SEV-2", "High")
    assert not agree(f, "p1", "high")
    with pytest.raises(ValueError, match="alias names a label outside the vocabulary"):
        FieldSpec("priority", "label", values=("low",), aliases={"p1": "urgent"})


@pytest.mark.req("FR-2.14")
def test_bool_reads_words_and_json():
    f = FieldSpec("x", "bool")
    assert agree(f, True, "yes") and agree(f, "Y", 1) and agree(f, "false", 0) and agree(f, "No.", False)
    assert not agree(f, True, False)
    assert one(f).key('{"x": "maybe"}') is INVALID and one(f).key('{"x": 2}') is INVALID


@pytest.mark.req("FR-2.14")
def test_number_reads_money_separators_and_tolerances():
    cents = FieldSpec("total", "number", abs_tol=0.01)
    assert agree(cents, "1,234.50", 1234.5) and agree(cents, "$1,234.50", "USD 1234.505")
    assert not agree(cents, "1,234.50", 1234.6)
    euro = FieldSpec("total", "number", decimal=",")
    assert agree(euro, "1.234,50 €", 1234.5) and agree(euro, "EUR 1 234,50", "1234,5")
    assert not agree(euro, "1.234,50", 1.2345)
    exact = FieldSpec("total", "number")
    assert agree(exact, "(5)", -5) and agree(exact, 1, 1.0) and agree(exact, "-$5", "(5.00)")
    assert not agree(exact, "(5)", 5)
    relative = FieldSpec("n", "number", rel_tol=0.01)
    assert agree(relative, 1000, 1009) and not agree(relative, 1000, 1011)
    assert agree(FieldSpec("r", "number", percent="fraction"), "12%", 0.12)
    assert agree(FieldSpec("r", "number"), "12 %", 12)


@pytest.mark.req("FR-2.14")
@pytest.mark.parametrize(
    ("text", "decimal", "want"),
    [
        ("1,234,567.89", ".", 1234567.89),
        ("12,34,567", ".", 1234567.0),  # Indian grouping
        ("1'234.5", ".", 1234.5),
        ("1 234 567,5", ",", 1234567.5),
        ("US$12", ".", 12.0),
        ("12.50 GBP", ".", 12.5),
        ("-1.5e3", ".", -1500.0),
        ("+7", ".", 7.0),
        ("−7", ".", -7.0),  # U+2212 minus sign
        ("1,5", ".", None),  # ambiguous: a group of one digit
        ("1,2345", ".", None),
        ("12 kg", ".", None),  # not a currency code
        ("usd 12", ".", None),  # codes are capitals
        ("NaN", ".", None),
        ("inf", ".", None),
        ("1_000", ".", None),
        ("--5", ".", None),
        ("(-5)", ".", None),
        ("", ".", None),
        ("1e999", ".", None),
    ],
)
def test_parse_number_cases(text, decimal, want):
    got = compare.parse_number(text, decimal)
    assert (None if got is INVALID else got) == want


@pytest.mark.req("FR-2.14")
def test_number_refuses_non_finite_and_non_numbers():
    f = FieldSpec("x", "number")
    for raw in ('"abc"', "true", "[1]", "1e999", '"Infinity"', "NaN", "9" * 400):  # the last: too large for a float
        assert one(f).key(f'{{"x": {raw}}}') is INVALID, raw


@pytest.mark.req("FR-2.14")
def test_ordinal_tolerance():
    f = FieldSpec("priority", "ordinal", order=tickets.PRIORITIES, tolerance=1)
    assert agree(f, "low", "medium") and agree(f, "High", "urgent")
    assert not agree(f, "low", "high")
    assert one(f).key('{"priority": "critical"}') is INVALID
    with pytest.raises(ValueError, match="order must list the labels"):
        FieldSpec("p", "ordinal")


@pytest.mark.req("FR-2.14")
def test_text_modes():
    loose = FieldSpec("vendor", "text", strip_suffixes=("inc", "corp", "pvt ltd"))
    assert agree(loose, "Acme Corp.", "ACME corp") and agree(loose, "Acme, Inc.", "acme")
    assert agree(loose, "Acme Pvt. Ltd.", "Acme")  # a suffix of two tokens
    assert not agree(loose, "Acme Corp.", "Apex Corp.")
    assert agree(FieldSpec("v", "text"), "Inc", "inc.")  # a suffix that is the whole name stays
    # Punctuation separates words, without a space too; apostrophes and the dots of initials join.
    assert agree(loose, "Acme,Inc.", "acme") and agree(loose, "hello-world", "Hello world")
    assert agree(loose, "O'Brien", "OBrien") and agree(loose, "U.S.A.", "usa") and agree(loose, "J.R.R. Tolkien", "jrr tolkien")
    assert not agree(loose, "3.5", "35") and not agree(loose, "A.Smith", "asmith")
    exact = FieldSpec("v", "text", mode="exact")
    assert agree(exact, "Acme  Corp", "acme corp") and not agree(exact, "Acme Corp.", "Acme Corp")
    bag = FieldSpec("v", "text", mode="token_set")
    assert agree(bag, "new york city", "City, New York") and not agree(bag, "new york", "york")


@pytest.mark.req("FR-2.14")
def test_date_formats():
    f = FieldSpec("due", "date")
    for written in ("03 Oct 2026", "October 3rd, 2026", "Oct. 3 2026", "2026/10/03", "3.10.2026", "10/03/2026"):
        assert agree(f, written, "2026-10-03"), written
    assert not agree(f, "03/10/2026", "2026-10-03")  # month first by default: 10 March
    assert agree(FieldSpec("due", "date", day_first=True), "03/10/2026", "2026-10-03")
    assert agree(f, "2026-10-03T23:30:00-05:00", "2026-10-03")  # the day as written
    utc = FieldSpec("at", "date", granularity="datetime")
    assert agree(utc, "2026-10-03T23:30:00-05:00", "2026-10-04T04:30:00Z") and agree(utc, "2026-10-03", "2026-10-03 00:00")
    assert not agree(utc, "2026-10-03T12:00:00Z", "2026-10-03T12:00:01Z")
    for bad in ("31 Feb 2026", "2026-13-01", "soon", "3 Octember 2026", "2026-10-03T25:00"):
        assert one(f).key(json.dumps({"due": bad})) is INVALID, bad


@pytest.mark.req("FR-2.14")
def test_language_codes_and_names():
    f = FieldSpec("lang", "language")
    assert agree(f, "English", "en") and agree(f, "eng", "en-US") and agree(f, "Deutsch", "ger") and agree(f, "日本語", "ja")
    assert not agree(f, "English", "fr")
    assert one(f).key('{"lang": "Klingon"}') is INVALID
    only = FieldSpec("lang", "language", values=("English", "Spanish"))
    assert one(only).valid('{"lang": "español"}') and not one(only).valid('{"lang": "fr"}')
    with pytest.raises(ValueError, match="unknown language"):
        FieldSpec("lang", "language", values=("Elvish",))
    for tag, code in (("pt_BR", "pt"), ("zh-Hant-TW", "zh"), ("es-419", "es"), ("cmn-Hans-CN", "zh"), ("ca-ES-valencia", "ca")):
        assert f.normalize(tag) == code, tag
    # A locale tag is the whole value, with '-' or '_' between subtags: prose that starts with a code is no language,
    # and neither is a tag led by a 639-2 or retired code ('ger-DE', 'in-depth').
    for prose in ("It is English", "no idea", "in progress", "ice cream", "per my answer", "may be english", "en US"):
        assert f.normalize(prose) is INVALID, prose
    for word in ("in-progress", "in-depth", "ger-DE", "my guess is french"):
        assert f.normalize(word) is INVALID, word
    detect = tasks.Task("detect-language", format="text", fields=(FieldSpec("$", "language"),))
    assert detect.agree("English.", "en") and not detect.valid("It is English.")
    assert not detect.agree("It is English.", "It is French.") and not detect.agree("No idea", "Norwegian")


@pytest.mark.req("FR-2.14")
def test_set_order_case_and_jaccard():
    f = FieldSpec("tags", "set")
    assert agree(f, ["Billing", "refund"], ["REFUND", "billing", "billing"]) and not agree(f, [], ["none"])
    assert not agree(f, ["a", "b"], ["a"])
    loose = FieldSpec("tags", "set", min_jaccard=0.8, empty_values=("none", "n/a"))
    assert agree(loose, ["a", "b", "c", "d", "e"], ["a", "b", "c", "d"])  # 4/5 = 0.8
    assert not agree(loose, ["a", "b", "c"], ["a", "b"])  # 2/3
    assert agree(loose, ["none"], [])  # empty values dropped: two empty sets agree
    split = FieldSpec("tags", "set", split=",", item="number")
    assert agree(split, "1, 2,3", [3.0, 2, 1]) and one(FieldSpec("tags", "set")).key('{"tags": "a,b"}') is INVALID


@pytest.mark.req("FR-2.14")
def test_records_unordered_keyed_and_ordered():
    lines = (FieldSpec("sku", "label"), FieldSpec("qty", "number"), FieldSpec("price", "number", abs_tol=0.01))
    a = [{"sku": "A-1", "qty": 2, "price": "9.99"}, {"sku": "B-2", "qty": 1, "price": 5}]
    b = [{"sku": "b_2", "qty": "1", "price": "$5.00"}, {"sku": "a 1", "qty": 2.0, "price": 9.995}]
    unordered = FieldSpec("lines", "records", fields=lines)
    keyed = FieldSpec("lines", "records", fields=lines, key=("sku",))
    ordered = FieldSpec("lines", "records", fields=lines, ordered=True)
    assert agree(unordered, a, b) and agree(keyed, a, b) and not agree(ordered, a, b) and agree(ordered, a, a)
    assert not agree(keyed, a, [b[0], {**b[1], "qty": 3}]) and not agree(unordered, a, b[:1])  # a field, or the length
    assert one(keyed).key(json.dumps({"lines": [a[0], a[0]]})) is INVALID  # two records with one key
    assert one(unordered).key(json.dumps({"lines": [{"sku": "A"}]})) is INVALID  # a required sub-field missing


@pytest.mark.req("FR-2.14")
def test_records_without_a_key_pair_in_any_order():
    """Without a key, records pair by a maximum matching, so the order they are listed in never decides: a first-fit
    pairing gave 1 the 0.5 and left 0 nothing, although 0 with 0.5 and 1 with 2 pair everything."""
    tol = FieldSpec("r", "records", fields=(FieldSpec("v", "number", abs_tol=1),))
    rows = [{"v": 0}, {"v": 1}]
    for listed in (rows, rows[::-1]):
        assert agree(tol, listed, [{"v": 0.5}, {"v": 2}]) and agree(tol, [{"v": 2}, {"v": 0.5}], listed)
    assert not agree(tol, rows, [{"v": 0.5}, {"v": 2.5}]) and not agree(tol, rows, [{"v": -1.5}, {"v": 2}])
    chain = [{"v": float(i)} for i in range(50)]  # each record within 1 of two partners: only one pairing takes all
    assert agree(tol, chain[::-1], [{"v": i + 0.6} for i in range(50)])
    assert not agree(tol, chain, [{"v": i + 0.6} for i in range(49)] + [{"v": 99}])
    # Fields without a tolerance group the records first; nested records and labels too.
    mixed = FieldSpec(
        "r",
        "records",
        fields=(
            FieldSpec("sku", "label"),
            FieldSpec("qty", "number", abs_tol=0.5),
            FieldSpec("parts", "records", fields=(FieldSpec("id", "label"),)),
        ),
    )
    x = [{"sku": "a", "qty": 1, "parts": [{"id": "p"}, {"id": "q"}]}, {"sku": "a", "qty": 2, "parts": []}]
    y = [{"sku": "A", "qty": 2.4, "parts": []}, {"sku": "a", "qty": 1.4, "parts": [{"id": "Q"}, {"id": "p"}]}]
    assert agree(mixed, x, y) and not agree(mixed, x, [y[0], {**y[1], "sku": "b"}])
    assert not agree(mixed, x, [y[0], {**y[1], "parts": [{"id": "p"}]}])


@pytest.mark.req("FR-2.14")
def test_ranking_top_k():
    top3 = FieldSpec("ids", "ranking", k=3)
    assert agree(top3, ["d1", "d2", "d3", "d4"], ["D1", "d2", "d3", "d9"])
    assert not agree(top3, ["d1", "d2", "d3"], ["d2", "d1", "d3"])
    as_set = FieldSpec("ids", "ranking", k=3, mode="set")
    assert agree(as_set, ["d1", "d2", "d3", "x"], ["d3", "d1", "d2", "y"]) and not agree(as_set, ["d1", "d2", "d3"], ["d1", "d2"])
    assert one(top3).key('{"ids": ["d1", "d1"]}') is INVALID  # an id ranked twice


# ---------- paths, formats, required and nullable ----------
@pytest.mark.req("FR-2.14")
def test_root_label_text_format():
    sentiment = tasks.Task("sentiment", format="text", fields=(FieldSpec("$", "label", values=("positive", "negative")),))
    assert sentiment.agree("Positive.", "positive") and sentiment.agree("<think>hmm</think> NEGATIVE!", "negative")
    assert not sentiment.agree("Positive.", "negative") and not sentiment.valid("It is mixed.")
    assert not sentiment.structured  # plain text: no fence unwrapping, no JSON parsing
    with pytest.raises(ValueError, match="one field, '\\$'"):
        tasks.Task("t", format="text", fields=(FieldSpec("x", "label"),))


@pytest.mark.req("FR-2.14")
def test_top_level_array_with_dollar_path():
    tags = tasks.Task("tags", fields=(FieldSpec("$", "set"),))
    assert tags.agree('["b", "A"]', '```json\n["a", "B"]\n```') and tags.agree('Tags: ["a", "b"]', '<think>x</think>["b","a"]')
    assert not tags.agree('["a"]', '["a", "b"]') and not tags.valid('{"tags": ["a"]}') and not tags.valid("a, b")
    number = tasks.Task("total", fields=(FieldSpec("$", "number", abs_tol=0.5),))
    assert number.agree("12", "12.4") and not number.agree("12", "13")
    nested = tasks.Task("n", fields=(FieldSpec("customer.address.city", "text"),))
    assert nested.agree('{"customer": {"address": {"city": "Pune"}}}', '{"customer": {"address": {"city": "pune."}}}')
    assert not nested.valid('{"customer": {"address": "Pune"}}') and not nested.valid('[{"customer": {}}]')


@pytest.mark.req("FR-2.14", "FR-2.2")
def test_empty_json_is_invalid_when_fields_required():
    assert TRIAGE.key("{}") is INVALID and TRIAGE.check("{}") == "invalid field category"
    s = score(TRIAGE, [comp("{}", 0.99)] * 3)
    assert (s.confidence, s.agreement, s.valid) == (0.0, 0.0, False)
    legacy = tasks.Task("legacy", compare_keys=["category", "priority"])  # optional exact fields, as in v0.4
    assert legacy.valid("{}") and legacy.agree("{}", "{}")


@pytest.mark.req("FR-2.14", "INV-16")
def test_out_of_vocab_label_invalid():
    assert not TRIAGE.valid(OUT_OF_VOCAB) and TRIAGE.check(OUT_OF_VOCAB) == "invalid field category"
    assert TRIAGE.check(NO_PRIORITY) == "invalid field priority" and TRIAGE.check("prose") == "invalid answer"
    assert TRIAGE.check("<think>only</think>") == "empty answer" and TRIAGE.check(VALID) is None
    for task, bad in (
        ("source-triage", '{"relevant": "perhaps", "source_type": "docs"}'),
        ("page-read", '{"answers_question": true, "fact_type": "quote"}'),
    ):
        assert not tasks.get(task).valid(bad), task
    # Every answer the mock world writes is valid, so the simulation never loses a vote or an example.
    stream = tickets.TicketStream(seed=5)
    for item in [*(stream.sample(9) for _ in range(100)), *list(research.REGISTRY.values())[:300]]:
        for labels in (item.labels, {k: v[-1] for k, v in item.label_space.items()}):
            assert tasks.get(item.task).valid(item.answer(labels)), item.text


@pytest.mark.req("FR-2.14")
def test_nullable_field_accepts_null():
    strict = tasks.Task("t", fields=(FieldSpec("due", "date"),))
    nullable = tasks.Task("t", fields=(FieldSpec("due", "date", nullable=True),))
    optional = tasks.Task("t", fields=(FieldSpec("due", "date", required=False),))
    assert not strict.valid('{"due": null}') and nullable.valid('{"due": null}') and not nullable.valid("{}")
    assert nullable.agree('{"due": null}', '{"due": null}') and not nullable.agree('{"due": null}', '{"due": "2026-10-03"}')
    assert optional.agree("{}", '{"due": null}') and not optional.agree("{}", '{"due": "2026-10-03"}')


@pytest.mark.req("FR-2.14")
@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: FieldSpec("x", "similarity", metric="bleu"), "metric must be one of"),
        (lambda: FieldSpec("x", "similarity", threshold=0), "threshold must be a number more than 0"),
        (lambda: FieldSpec("x", "similarity", threshold=1.5), "threshold must be a number more than 0"),
        (lambda: FieldSpec("x", "similarity", max_len_ratio=0.5), "max_len_ratio must be a finite number >= 1"),
        (lambda: FieldSpec("x", "similarity", max_len_ratio=math.inf), "max_len_ratio must be a finite number >= 1"),
        (lambda: FieldSpec("x", "label", metric="token_f1"), "metric does not apply"),
        (lambda: FieldSpec("x", "text", threshold=0.5), "threshold does not apply"),
        (lambda: FieldSpec("r", "records", fields=(FieldSpec("s", "similarity"),)), "cannot be similarity"),
        (lambda: FieldSpec("x", "set", item="similarity"), "elements must be of a scalar type"),
        (lambda: FieldSpec("x", "tool_calls"), "reserved for a later release"),
        (lambda: FieldSpec("x", "fuzzy"), "unknown type"),
        (lambda: FieldSpec("a..b", "label"), "dot-separated keys"),
        (lambda: FieldSpec("x", "number", values=("1",)), "values does not apply"),
        (lambda: FieldSpec("x", "label", values=("a", "A")), "same label once normalised"),
        (lambda: FieldSpec("x", "number", abs_tol=-1), "finite number >= 0"),
        (lambda: FieldSpec("x", "set", item=FieldSpec("$", "number", abs_tol=1)), "elements compare exactly"),
        (lambda: FieldSpec("x", "records"), "fields must list"),
        (lambda: FieldSpec("x", "records", fields=(FieldSpec("a", "label"),), key=("b",)), "not one of the record's fields"),
        (lambda: FieldSpec("x", "label", values=tuple(str(i) for i in range(2001))), "more than 2000"),
        (lambda: FieldSpec("x", "number", tolerance=1), "tolerance does not apply"),
        (lambda: FieldSpec("x", "label", abs_tol=1.0), "abs_tol does not apply"),
        (lambda: FieldSpec("x", "label", mode="bogus"), "mode does not apply"),
        (lambda: FieldSpec("x", "set", k=3), "k does not apply"),
        (lambda: FieldSpec("x", "number", min_jaccard=0.5), "min_jaccard does not apply"),
        (lambda: FieldSpec("x", "bool", day_first=True), "day_first does not apply"),
        (
            lambda: FieldSpec("p", "label", values=("low", "high", "urgent"), aliases={"HIGH": "urgent"}),
            "label of the vocabulary",
        ),
        (lambda: FieldSpec("p", "label", values=("low", "high"), aliases={"P1": "high", "p1": "low"}), "names two labels"),
        (lambda: tasks.Task("t", compare_keys=["a.b"]), "not a plain top-level key"),
        (lambda: tasks.Task("t", compare_keys=["$"]), "not a plain top-level key"),
        (lambda: tasks.Task("t", compare_keys=["a"], fields=(FieldSpec("b"),)), "not both"),
        (lambda: tasks.Task("t", fields=(FieldSpec("a"), FieldSpec("a", "label"))), "same path"),
        (lambda: tasks.Task("t", fields=tuple(FieldSpec(f"f{i}") for i in range(65))), "more than 64 fields"),
        (lambda: tasks.Task("t", format="tool_calls"), "reserved for a later release"),
        (lambda: tasks.Task("t", format="sql"), "a sql task needs sql options"),
        (lambda: tasks.Task("t", policy={"sample": 3}), "unknown policy key"),
        (lambda: tasks.Task("t", policy={"audit_rate": 2}), "from 0 to 1"),
        (lambda: tasks.Task("t", tools="student"), "tools must be one of"),
    ],
)
def test_bad_specs_fail_when_defined(make, message):
    with pytest.raises(ValueError, match=message):
        make()


@pytest.mark.req("FR-2.14")
def test_records_nest_at_most_four_deep_and_hold_at_most_200():
    inner = FieldSpec("v", "number")
    for _ in range(4):
        inner = FieldSpec("r", "records", fields=(inner,))
    assert inner.depth == 4
    with pytest.raises(ValueError, match="nested more than 4 deep"):
        FieldSpec("r", "records", fields=(inner,))
    rows = tasks.Task("t", fields=(FieldSpec("rows", "records", fields=(FieldSpec("v", "number"),)),))
    assert rows.valid(json.dumps({"rows": [{"v": i} for i in range(200)]}))
    assert not rows.valid(json.dumps({"rows": [{"v": i} for i in range(201)]}))
    # The 200 count every record of the answer, at any depth: 20 outer records of 10 inner ones each is 220.
    nested = tasks.Task(
        "t", fields=(FieldSpec("o", "records", fields=(FieldSpec("i", "records", fields=(FieldSpec("v", "number"),)),)),)
    )
    assert nested.valid(json.dumps({"o": [{"i": [{"v": 1}] * 9}] * 18}))  # 18 + 162 = 180
    assert not nested.valid(json.dumps({"o": [{"i": [{"v": 1}] * 10}] * 20}))


@pytest.mark.req("FR-2.14")
def test_spec_hash_covers_the_rules_not_the_name():
    a = tasks.Task("a", fields=(FieldSpec("category", "label", values=("x", "y")),))
    assert a.spec_hash == tasks.Task("b", fields=(FieldSpec("category", "label", values=("x", "y")),), source="api").spec_hash
    assert a.spec_hash != tasks.Task("a", fields=(FieldSpec("category", "label", values=("x",)),)).spec_hash
    assert a.spec_hash != tasks.Task("a", fields=a.fields, pinned=True).spec_hash
    legacy = tasks.Task("t", compare_keys=["k", "k"])
    assert legacy.fields == (FieldSpec("k", "exact", required=False),) and legacy.compare_keys == ("k", "k")
    assert dataclasses.replace(a, pinned=True).spec_hash == tasks.Task("a", fields=a.fields, pinned=True).spec_hash
    pinned = dataclasses.replace(legacy, pinned=True)  # replace passes compare_keys and the fields they made
    assert pinned.pinned and pinned.fields == legacy.fields and tasks.register(legacy, "file") is None
    with pytest.raises(TypeError):
        a.policy["samples"] = 5  # read-only, so the hash cannot go stale
    assert legacy.spec_hash == tasks.Task("u", fields=(FieldSpec("k", "exact", required=False),)).spec_hash
    assert len(TRIAGE.spec_hash) == 16 and tasks.get("unknown-task").source == "adhoc"
    assert json.loads(json.dumps(TRIAGE.spec()))["fields"][0] == {
        "path": "category",
        "type": "label",
        "values": list(tickets.CATEGORIES),
    }


# ---------- the v0.4 rule, byte for byte ----------
LEGACY_CORPUS = [
    '{"category": "Billing", "priority": "HIGH", "reply": "x"}',
    '{"category": " billing ", "priority": "high", "reply": "y"}',
    '<think>billing?</think>{"category": "billing", "priority": "high"}',
    'Let me see</think>\n```json\n{"category": "billing", "priority": "high"}\n```',
    '```json\n{"category": "billing"}\n```',
    '{"category": "billing", "priority": null}',
    '{"category": "billing"}',
    "{}",
    '{"category": 1, "priority": 1.0}',
    '{"category": 1.0, "priority": true}',
    '{"category": 1, "priority": 1}',
    '{"category": ["b", "a"], "priority": {"z": 1, "a": 2}}',
    '{"category": ["a", "b"], "priority": {"a": 2, "z": 1}}',
    '{"category": NaN, "priority": "x"}',
    '[{"category": "billing"}]',
    "billing, high",
    "",
    '<think>{"category": "billing", "priority": "high"}',
    'Here it is: {"category": "billing", "priority": "high"} done',
    '{"category": "Ünïcode ", "priority": "ß"}',
    '{"category": "ünïcode", "priority": "SS"}',
    '{"category": "a <think> b", "priority": "</think>"}',
    '{"category": ' + "[" * 400 + "]" * 400 + ', "priority": "x"}',
    "```\n" + "[" * 5000 + "]" * 5000 + "\n```",
    '{"category": "billing", "priority": "high"} and {"x": 1}',
]


def _legacy_canonical(keys, text):
    """v0.4's `Task.canonical` for a task with `compare_keys`, as it was written."""
    clean = tasks.clean_answer(SimpleNamespace(structured=True), text)
    if not clean:
        return tasks.INVALID
    obj = tasks.parse_json(clean)
    if obj is None:
        return tasks.INVALID
    return json.dumps(
        {k: (v.strip().lower() if isinstance(v, str) else v) for k, v in ((k, obj.get(k)) for k in keys)}, sort_keys=True
    )


@pytest.mark.req("FR-2.6", "FR-2.14")
def test_legacy_compare_keys_canonical_byte_identical():
    for keys in (["category", "priority"], ["priority"], ["reply"]):
        task = tasks.Task("legacy", compare_keys=keys)
        assert task.structured and task.format == "json"
        legacy = [_legacy_canonical(keys, text) for text in LEGACY_CORPUS]
        assert [task.canonical(text) for text in LEGACY_CORPUS] == legacy
        for a, ca in zip(LEGACY_CORPUS, legacy, strict=True):
            for b, cb in zip(LEGACY_CORPUS, legacy, strict=True):
                assert task.agree(a, b) == (ca != tasks.INVALID and ca == cb), (keys, a[:60], b[:60])
    assert tasks.Task("t").canonical("Hello   World ") == "hello world"  # without fields: the v0.4 text rule


def _legacy_score(key_fn, samples):
    """v0.4's `confidence.score`: count equal canonical answers with Counter (`key_fn`: the canonical string)."""
    canon = [key_fn(s.text) if s.finish_reason in COMPLETE else tasks.INVALID for s in samples]
    counts = Counter(c for c in canon if c != tasks.INVALID)
    if not counts:
        return samples[0], 0.0, 0.0, samples[0].mean_prob
    top, votes = counts.most_common(1)[0]
    agreement = votes / len(samples)
    best = max(
        (s for s, c in zip(samples, canon, strict=True) if c == top),
        key=lambda s: s.mean_prob if s.mean_prob is not None else 0.0,
    )
    p = best.mean_prob
    return best, (agreement if p is None else 0.4 * p + 0.6 * agreement), agreement, p


def _same_score(task, samples, key_fn):
    got = score(task, samples)
    best, conf, agreement, p = _legacy_score(key_fn, samples)
    assert got.answer is best and (got.confidence, got.agreement, got.token_prob) == (conf, agreement, p)


@pytest.mark.req("FR-2.2", "FR-2.14")
def test_pairwise_score_equals_counter_score_for_exact_tasks():
    rng = random.Random(7)
    keys = ["category", "priority"]
    task = tasks.Task("legacy", compare_keys=keys)
    answers = [
        '{"category": "billing", "priority": "high"}',
        '{"category": "Billing ", "priority": "high", "reply": "other"}',
        '{"category": "refund", "priority": "high"}',
        '{"category": "refund", "priority": 1}',
        '{"category": "refund", "priority": 1.0}',
        "{}",
        "not json",
        "",
    ]
    for _ in range(600):
        samples = [
            comp(
                rng.choice(answers),
                rng.choice([None, 0.5, 0.9, rng.random()]),
                rng.choice(["stop", "stop", "stop", "length", "tool_calls"]),
            )
            for _ in range(rng.randint(1, 5))
        ]
        _same_score(task, samples, lambda text: _legacy_canonical(keys, text))
        if score(task, samples).valid:
            n = rng.randint(1, len(samples))
            got = majority(task, score(task, samples), samples, n)
            assert got is None or all(task.agree(c.text, got[0].text) for c in got)


@pytest.mark.req("FR-2.6", "FR-2.14")
def test_builtin_task_agreement_decisions_unchanged():
    """On answers the mock models write (labels from each item's label space, free-text fields differing), the built-in
    typed specs decide agreement, confidence and the best sample exactly as v0.4's exact `compare_keys` did."""
    rng = random.Random(11)
    old_keys = {"ticket-triage": ["category", "priority"], "source-triage": ["relevant", "source_type"]}
    old_keys["page-read"] = ["answers_question", "fact_type"]
    stream = tickets.TicketStream(seed=3)
    items = [stream.sample(1) for _ in range(30)] + rng.sample(sorted(research.REGISTRY.values(), key=lambda i: i.text), 60)
    for item in items:
        task, keys = tasks.get(item.task), old_keys[item.task]

        def answer(item=item, keys=keys):
            return item.answer({k: rng.choice(item.label_space[k]) for k in keys})

        for _ in range(8):
            a, b = answer(), answer()
            assert task.agree(a, b) == (_legacy_canonical(keys, a) == _legacy_canonical(keys, b)), (a, b)
            samples = [comp(answer(), rng.random(), rng.choice(["stop", "stop", "length"])) for _ in range(3)]
            _same_score(task, samples, lambda text, keys=keys: _legacy_canonical(keys, text))


@pytest.mark.req("FR-2.2", "FR-2.14")
def test_tolerance_non_transitive():
    task = tasks.Task("t", fields=(FieldSpec("x", "number", abs_tol=1),))
    samples = [comp('{"x": 0}'), comp('{"x": 0.6}'), comp('{"x": 1.2}')]
    s = score(task, samples)
    assert s.answer is samples[1] and s.agreement == 1.0  # 0.6 agrees with both ends; the ends do not agree
    assert not task.agree(samples[0].text, samples[2].text)
    # The middle sample is the answer even at a lower token probability: it is the one every member agrees with.
    samples = [comp('{"x": 0}', 0.99), comp('{"x": 0.6}', 0.5), comp('{"x": 1.2}', 0.99)]
    assert score(task, samples).answer is samples[1]
    assert majority(task, score(task, samples), samples, 3) == [samples[1], samples[0], samples[2]]
    # Two groups of two: the first group to reach the top count wins, as Counter's tie-break did.
    samples = [comp('{"x": 5}'), comp('{"x": 0}'), comp('{"x": 5.5}'), comp('{"x": 0.5}')]
    s = score(task, samples)
    assert s.answer is samples[0] and s.agreement == 0.5
    # Four samples: 0.6 and 1.2 both have three votes. The served sample (1.2, more probable) does not match 0, but
    # the agreement is its own: three of four.
    samples = [comp('{"x": 0}'), comp('{"x": 0.6}', 0.5), comp('{"x": 1.2}', 0.99), comp('{"x": 1.8}')]
    s = score(task, samples)
    assert s.answer is samples[2] and s.agreement == 0.75 and not task.agree(samples[0].text, samples[2].text)
    assert sum(task.agree(samples[2].text, x.text) for x in samples) / len(samples) == s.agreement


@pytest.mark.req("FR-2.2", "INV-7")
def test_score_never_raises_when_a_key_does_not_match_itself():
    """The pairwise score must not crash the request path for a comparator that is not reflexive (a value json.dumps
    cannot write from a deeper stack, a future type): a sample that matches nothing, itself included, gets no vote."""

    class Odd:
        def key(self, text):
            return {"$": text}

        def match(self, ka, kb):
            return ka is not kb and ka == kb  # equal keys of other samples match; a key never matches itself

    lone = score(Odd(), [comp("a", 0.9), comp("b", 0.9)])
    assert (lone.confidence, lone.agreement, lone.valid) == (0.0, 0.0, False)
    samples = [comp("a", 0.5), comp("a", 0.9), comp("b")]
    pair = score(Odd(), samples)  # each "a" has one vote, from the other: the more probable one is served
    assert pair.answer is samples[1] and pair.agreement == pytest.approx(1 / 3)


@pytest.mark.req("FR-2.14")
def test_default_compares_json_canonically():
    d = tasks.get("default")
    assert d.agree('{"b": 1, "a": "X"}', '{"a": " x ", "b": 1.0}') and d.agree("[1, 2]", "```json\n[1.0, 2]\n```")
    assert d.agree('{"Name": "Ｐune"}', '{"name": "pune"}')  # keys and strings NFKC-normalised and case-folded
    assert d.agree('{"a": "x  y"}', '{"a": " x y"}') and d.agree('{"a  b": 1}', '{"a b": 1}')  # whitespace, as in v0.4
    assert not d.agree("[1, 2]", "[2, 1]") and not d.agree('{"a": 1}', '{"a": 2}') and not d.agree('{"a": true}', '{"a": 1}')
    assert d.agree("Hello   World", "hello world") and not d.agree("hello", "hello!")  # text: the v0.4 rule
    assert d.agree('{"a": true}', '{"A": TRUE}')  # the second is not JSON, so both compare as text
    assert d.canonical('{"b": 1,  "a": 2}') == '{"b": 1, "a": 2}'  # canonical keeps the v0.4 text form
    assert tasks.get("my-extractor").agree('{"x": [1, {"y": 2}]}', '{"x": [1.0, {"y": 2.0}]}')
    deep = "[" * 5000 + "]" * 5000  # too deep for the JSON parser: compared as text, never a crash
    assert d.agree(deep, deep) and not d.agree(deep, "[]")
    strict = tasks.Task("t", format="json")
    assert strict.structured and strict.valid('{"a": 1}') and strict.valid("[1]") and not strict.valid("a: 1")


@pytest.mark.req("FR-2.14")
def test_comparators_are_linear_on_adversarial_answers():
    """Every sample is normalised on the request path, so no comparator may take seconds on a long answer: each type
    is linear in its value, numbers, dates and languages longer than 100 characters are refused at once, and records
    pair within the 200-record budget: in linear time by their fields without a tolerance or as the same records in
    another order, else by a matching quadratic in the records."""
    lines = FieldSpec("lines", "records", fields=(FieldSpec("sku", "label"), FieldSpec("qty", "number", abs_tol=0.5)))
    rows = [{"sku": f"s{i}", "qty": i} for i in range(200)]
    cases = [
        (FieldSpec("x", "label"), "a" + ". " * 200_000, "a"),
        (FieldSpec("x", "text", strip_suffixes=("inc", "co ltd")), "acme" + " inc" * 100_000, "acme"),
        (FieldSpec("x", "text", mode="token_set"), "a, " * 100_000, frozenset({"a"})),
        (FieldSpec("x", "number"), "1" * 200_000, INVALID),
        (FieldSpec("x", "number"), "(" * 100_000 + "1" + ")" * 100_000, INVALID),
        (FieldSpec("x", "date"), "3" * 200_000, INVALID),
        (FieldSpec("x", "language"), "(" * 200_000, INVALID),
        (FieldSpec("x", "bool"), "y" * 200_000, INVALID),
        (FieldSpec("x", "set", split=","), ",".join(["x"] * 1999), frozenset({"x"})),
    ]
    start = time.perf_counter()
    for f, raw, want in cases:
        assert f.normalize(raw) == want if want is not INVALID else f.normalize(raw) is INVALID, f
    a, b = lines.normalize(rows), lines.normalize(list(reversed(rows)))
    assert lines.match(a, b)  # the same records reversed: paired as equal values, in linear time
    near = lines.normalize([{"sku": f"s{i}", "qty": i + 0.25} for i in reversed(range(200))])
    assert lines.match(a, near)  # within the tolerance: grouped by sku first
    tol = FieldSpec("r", "records", fields=tuple(FieldSpec(f"f{k}", "number", abs_tol=0.5) for k in range(10)))
    wide = [{**{f"f{k}": 1 for k in range(9)}, "f9": i} for i in range(200)]
    x, y = tol.normalize(wide), tol.normalize([{**r, "f9": r["f9"] + 0.25} for r in reversed(wide)])
    assert tol.match(x, y)  # every field with a tolerance, in the worst order: a quadratic matching, bounded by 200
    answer = json.dumps({"category": "billing" + "!" * 300_000, "priority": "high" + " " * 300_000})
    assert TRIAGE.key(answer) == {"category": "billing", "priority": "high"}
    assert time.perf_counter() - start < 2.0


@pytest.mark.req("FR-2.14", "FR-2.55", "INV-7")
def test_comparators_never_raise_on_junk():
    """Model output is arbitrary, and every sample is normalised on the request path: on 3,000 seeded junk values (odd
    strings, NaN, huge integers, nested lists and objects) no comparator raises, and every valid value matches itself
    (the pairwise score counts a sample's vote for itself)."""
    rng = random.Random(1)
    specs = [
        FieldSpec("x", "exact"),
        FieldSpec("x", "label", values=("a", "b c")),
        FieldSpec("x", "bool"),
        FieldSpec("x", "number", abs_tol=0.1),
        FieldSpec("x", "number", decimal=",", percent="fraction"),
        FieldSpec("x", "ordinal", order=("lo", "mid", "hi"), tolerance=1),
        FieldSpec("x", "text", mode="token_set"),
        FieldSpec("x", "date", granularity="datetime", day_first=True),
        FieldSpec("x", "language"),
        FieldSpec("x", "set", split=",", item="number"),
        FieldSpec("x", "set", min_jaccard=0.5, empty_values=("none", None, 0)),
        FieldSpec("x", "records", fields=(FieldSpec("k", "label"), FieldSpec("v", "number", required=False)), key=("k",)),
        FieldSpec("x", "ranking", mode="set", item="date"),
        FieldSpec("x", "similarity"),
        FieldSpec("x", "similarity", metric="token_f1", threshold=0.2, max_len_ratio=10),
        FieldSpec("x", "similarity", metric="rouge_l"),
    ]
    alphabet = "abc 019,.'()%$€₹−-:/TZ+日本語\u00a0"
    samples = ["1,234.50", "(5)", "2026-10-03", "Oct 3 2026", "en", "yes", "lo", "none", "9999-12-31T23:59:59-05:00"]

    def junk(depth=0):
        r = rng.random()
        if r < 0.3:
            return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 20)))
        if r < 0.45:
            return rng.choice([True, False, None, 0, 1, 1.5, float("nan"), float("inf"), 10**400, -0.0, *samples])
        if r < 0.7 and depth < 3:
            return [junk(depth + 1) for _ in range(rng.randint(0, 4))]
        if depth < 3:
            return {rng.choice("kvxy"): junk(depth + 1) for _ in range(rng.randint(0, 3))}
        return "z"

    for _ in range(3000):
        f, raw = rng.choice(specs), junk()
        v = f.normalize(raw)
        assert v is INVALID or f.match(v, v), (f.type, raw)
        f.match(v, f.normalize(junk()))
        one(f).canonical(json.dumps({"x": raw}))


# ---------- what the cascade, the golden eval and the dataset do with invalid answers ----------
def _cascade(teacher, student=None, threshold=0.5, **settings):
    led = Ledger(":memory:")
    s = Settings(
        **{"teacher_model": OPEN_TEACHER, "samples": 3, "audit_rate": 0.0, "redact_pii": True, "teacher_reasoning_tokens": 0}
        | settings
    )
    models = {}
    if student is not None:
        models[_version(led, "live", {"threshold": threshold})] = student
    return led, Ratchet(s, led, teacher, lambda v: (models[v.id], v.adapter_uri), seed=1)


def _row(led):
    return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())


def _skips(led):
    return [e["detail"] for e in led.events() if e["kind"] == "skip_example"]


def _ask(text="I was charged twice this month"):
    return [{"role": "user", "content": text}]


@pytest.mark.req("FR-2.14", "FR-2.13", "INV-16")
def test_invalid_teacher_answer_served_but_not_stored():
    for answer, reason in ((OUT_OF_VOCAB, "invalid field category"), (NO_PRIORITY, "invalid field priority")):
        led, r = _cascade(RecordingProvider(answer))
        out = r.handle(_ask(), "ticket-triage")
        assert out.served_by == "teacher" and out.text == answer  # the client still gets it
        assert led.examples() == [] and _row(led)["stored"] == 0
        assert _skips(led) == [f"ticket-triage: {reason}"]  # the spec's path, never the answer
    led, r = _cascade(RecordingProvider(VALID))
    r.handle(_ask(), "ticket-triage")
    assert [ex["completion"] for ex in led.examples()] == [VALID] and _row(led)["stored"] == 1
    # An invalid student answer is never served either: no vote, so the teacher answers.
    led, r = _cascade(RecordingProvider(VALID), student=RecordingProvider(OUT_OF_VOCAB), threshold=0.0)
    out = r.handle(_ask(), "ticket-triage")
    assert (out.served_by, out.route, out.confidence) == ("teacher", "confidence", 0.0)
    # A task that does not store keeps every teacher answer out of the training set, without a skip.
    quiet = tasks.Task("quiet-triage", fields=TRIAGE.fields, store=False)
    led, r = _cascade(RecordingProvider(VALID))
    assert r.handle(_ask(), quiet).text == VALID and led.examples() == [] and _skips(led) == []


@pytest.mark.req("FR-2.14", "INV-16")
def test_invalid_audit_not_counted():
    led, r = _cascade(RecordingProvider(OUT_OF_VOCAB), student=RecordingProvider(VALID), audit_rate=1.0)
    out = r.handle(_ask(), "ticket-triage")
    assert out.served_by == "student" and out.text == VALID
    row = _row(led)
    assert (row["audited"], row["audit_agree"], row["stored"]) == (0, None, 0) and led.examples() == []
    led, r = _cascade(RecordingProvider(VALID), student=RecordingProvider(VALID), audit_rate=1.0)
    r.handle(_ask(), "ticket-triage")
    assert (_row(led)["audited"], _row(led)["audit_agree"]) == (1, 1)


@pytest.mark.req("FR-2.14", "INV-6", "FR-5.2")
def test_cascade_eval_skips_invalid_golden_refs():
    refs = [VALID, OUT_OF_VOCAB, VALID, "{}", VALID, "prose", VALID, VALID]
    golden = [
        {"task": "ticket-triage", "messages": [{"role": "user", "content": f"ticket {i}"}, {"role": "assistant", "content": ref}]}
        for i, ref in enumerate(refs)
    ]
    student = RecordingProvider(VALID)
    res = cascade_eval(lambda p, k, params: student.complete(p, "student-v1", n=k, logprobs=True), golden, samples=3)
    asked = [c.messages[-1]["content"] for c in student.calls]
    assert asked == ["ticket 0", "ticket 2", "ticket 4", "ticket 6", "ticket 7"]  # an invalid reference is not sampled
    assert (res["golden_invalid_refs"], res["golden_n"], res["test_n"], res["golden_agreement"]) == (3, 5, 2, 1.0)


@pytest.mark.req("FR-2.14", "INV-2", "INV-16", "FR-4.1")
def test_dataset_rows_skip_invalid(tmp_path):
    led = Ledger(":memory:", golden_fraction=0.3)
    lic = licenses.require_distillable(OPEN_TEACHER)
    for i in range(60):  # stored under an older, looser spec: every fourth answer has a label the task now refuses
        answer = OUT_OF_VOCAB if i % 4 == 0 else VALID
        led.add_example("ticket-triage", _ask(f"ticket {i}"), answer, OPEN_TEACHER, lic.name, lic.url)
    stored = led.examples()
    bad_train = [r for r in stored if r["split"] == "train" and r["completion"] == OUT_OF_VOCAB]
    bad_golden = [r for r in stored if r["split"] == "golden" and r["completion"] == OUT_OF_VOCAB]
    assert bad_train and bad_golden
    counts: dict[str, int] = {}
    train = dataset.rows(led, "train", counts=counts)
    assert counts == {"skipped_invalid": len(bad_train)}
    assert len(train) == sum(r["split"] == "train" for r in stored) - len(bad_train)
    assert all(row["messages"][-1]["content"] == VALID for row in train)  # never a target the task refuses
    golden = dataset.rows(led, "golden")  # kept: the golden eval skips and counts them (golden_invalid_refs)
    assert len(golden) == sum(r["split"] == "golden" for r in stored)
    manifest = dataset.build(led, tmp_path)
    assert (manifest["train_examples"], manifest["skipped_invalid"]) == (len(train), len(bad_train))
    assert json.loads((tmp_path / "manifest.json").read_text())["skipped_invalid"] == len(bad_train)
    # The manifest is a temporary file in a night: the count reaches operators through the night's event.
    trainer = RecordingTrainer()
    rep = run_night(led, trainer, RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led), "base", min_new_examples=1)
    assert rep.trained and len(trainer.calls[0].rows) == len(train)
    trained = [e["detail"] for e in led.events() if e["kind"] == "trained"]
    assert f"({len(bad_train)} stored answers invalid under the current spec left out)" in trained[0]
    skip = run_night(led, trainer, RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led), "base", min_new_examples=1)
    skipped = [e["detail"] for e in led.events() if e["kind"] == "skip"]
    assert not skip.trained and skipped == [
        f"only 0 new examples (< 1) ({len(bad_train)} stored answers invalid under the "
        "current spec left out); no training tonight"
    ]


# ---------- free text: the similarity comparator (FR-2.55, ADR 0054) ----------
def _chrf_by_definition(a: str, b: str) -> float:
    """chrF written out from its definition, independently of `ratchet.similarity`: character n-grams of 1 to 6 of each
    case-folded text without whitespace; per order, precision and recall of the matched n-grams (an order only one text
    has counts as 0 and 0, one neither has is skipped); both averaged; F-beta with beta 2, the lower of the two
    directions."""
    x, y = "".join(a.casefold().split()), "".join(b.casefold().split())
    if x == y:
        return 1.0
    ps, rs = [], []
    for n in range(1, 7):
        gx = Counter(x[i : i + n] for i in range(len(x) - n + 1))
        gy = Counter(y[i : i + n] for i in range(len(y) - n + 1))
        if not gx and not gy:
            continue
        matched = sum((gx & gy).values())
        ps.append(matched / sum(gx.values()) if gx else 0.0)
        rs.append(matched / sum(gy.values()) if gy else 0.0)
    if not ps:
        return 0.0
    p, r = sum(ps) / len(ps), sum(rs) / len(rs)

    def f2(p: float, r: float) -> float:
        return 5 * p * r / (4 * p + r) if 4 * p + r else 0.0

    return min(f2(p, r), f2(r, p))


def _lcs_by_table(a: list[str], b: list[str]) -> int:
    """The longest common subsequence by the textbook dynamic-programming table."""
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


SUMMARY_TASK = tasks.Task(
    "short-summary", format="text", fields=(FieldSpec("$", "similarity"),), policy={"samples": 3}, source="file"
)


@pytest.mark.req("FR-2.55")
def test_chrf_identical_one_unrelated_low():
    a = "The customer was charged twice for the March invoice and wants a refund."
    assert similarity.score(a, a) == 1.0
    assert similarity.score(a, "  the CUSTOMER was charged twice for the march invoice and wants a refund.  ") == 1.0
    assert similarity.score(a, "Quantum chromodynamics describes gluons binding quarks.") < 0.25
    # By hand: "ab" against "ac" matches one unigram in two each way and no bigram: P = R = 1/4 averaged, F2 = 1/4.
    assert similarity.score("ab", "ac") == pytest.approx(0.25)
    # "abc" against "ab": P = (2/3 + 1/2 + 0) / 3, R = (1 + 1 + 0) / 3; the lower direction is 14/33.
    assert similarity.score("abc", "ab") == pytest.approx(14 / 33) == similarity.score("ab", "abc")
    # Against the definition on seeded random strings, and symmetric (the pairwise score compares each pair once).
    rng = random.Random(5)
    for _ in range(300):
        x = "".join(rng.choice("ab c") for _ in range(rng.randint(1, 30)))
        y = "".join(rng.choice("abc d") for _ in range(rng.randint(1, 30)))
        if not x.strip() or not y.strip():
            continue
        assert similarity.score(x, y) == pytest.approx(_chrf_by_definition(x, y)), (x, y)
        assert similarity.score(x, y) == pytest.approx(similarity.score(y, x))
    with pytest.raises(ValueError, match="unknown similarity metric"):
        similarity.score("a", "b", "bleu")


@pytest.mark.req("FR-2.55")
def test_token_f1_and_rouge_l():
    f1 = "token_f1"
    # SQuAD: punctuation deleted, the articles a/an/the dropped, a bag of tokens. 3 shared of 3 and 4: 6/7.
    assert similarity.score("Paris is the capital", "The capital is Paris, France.", f1) == pytest.approx(6 / 7)
    assert similarity.score("the cat sat on the mat", "a cat sat on a mat", f1) == 1.0
    assert similarity.score("don't stop", "Dont stop!", f1) == 1.0
    assert similarity.score("the the", "a an", f1) == 0.0  # nothing left to compare, and the texts differ
    assert similarity.score("x y z", "p q r", f1) == 0.0
    # ROUGE-L F1: the longest common subsequence "the cat on the mat" is 5 of 6 tokens each way.
    rl = "rouge_l"
    assert similarity.score("the cat sat on the mat", "the cat is on the mat", rl) == pytest.approx(10 / 12)
    assert similarity.score("Hello, world", "hello world!", rl) == 1.0  # punctuation is not a token
    assert similarity.score("a b c d", "d c b a", rl) == pytest.approx(2 / 8)  # order matters, unlike token F1
    assert similarity.score("a b c d", "d c b a", f1) == 1.0
    # The bit-parallel LCS against the textbook table, on seeded random token lists.
    rng = random.Random(9)
    for _ in range(300):
        a = [rng.choice("abcde") for _ in range(rng.randint(0, 40))]
        b = [rng.choice("abcdef") for _ in range(rng.randint(0, 40))]
        assert similarity.lcs_length(a, b) == _lcs_by_table(a, b) == similarity.lcs_length(b, a), (a, b)
    # ROUGE-L reads at most 512 tokens of each answer: what follows them cannot change the score.
    head = " ".join(f"w{i}" for i in range(similarity.MAX_LCS_TOKENS))
    assert similarity.score(head + " tail one", head + " other words entirely", rl) == 1.0


@pytest.mark.req("FR-2.55", "FR-2.2")
def test_similarity_score_picks_medoid():
    """Similarity is not transitive: A is close to B and B to C, but A is far from C. The pairwise score serves the
    medoid, the sample with the most matches (B, three votes), even with the lowest token probability; between samples
    with as many matches as each other, the one with the highest token probability."""
    task = tasks.Task(
        "chain", format="text", fields=(FieldSpec("$", "similarity", metric="token_f1", threshold=0.5),), policy={"samples": 3}
    )
    a, b, c = "alpha beta gamma delta", "gamma delta epsilon zeta", "epsilon zeta eta theta"
    assert task.agree(a, b) and task.agree(b, c) and not task.agree(a, c)
    s = score(task, [comp(a, 0.99), comp(b, 0.40), comp(c, 0.98)])
    assert s.answer.text == b and s.agreement == 1.0 and s.confidence == pytest.approx(0.4 * 0.40 + 0.6)
    # Two close answers and an outlier: two votes each for the close pair; the more probable of the two is served.
    near_a, near_b = "the refund was sent to the card today", "the refund was sent to your card today"
    far = "please reset your password from the login page"
    assert task.agree(near_a, near_b) and not task.agree(near_a, far)
    s = score(task, [comp(near_a, 0.70), comp(far, 0.99), comp(near_b, 0.90)])
    assert s.answer.text == near_b and s.agreement == pytest.approx(2 / 3)
    assert s.confidence == pytest.approx(0.4 * 0.90 + 0.6 * 2 / 3)
    # The n choices a client asks for are the medoid and the samples that match it; never the outlier.
    samples = [comp(near_a, 0.70), comp(far, 0.99), comp(near_b, 0.90)]
    s = score(task, samples)
    assert [x.text for x in majority(task, s, samples, 2)] == [near_b, near_a] and majority(task, s, samples, 3) is None


@pytest.mark.req("FR-2.55")
def test_len_ratio_blocks_match():
    short, long = "refund issued", "refund issued refund issued refund issued"
    assert similarity.score(short, long, "token_f1") == pytest.approx(0.5)  # 2 shared of 2 and 6 tokens
    strict = FieldSpec("$", "similarity", metric="token_f1", threshold=0.5)
    loose = FieldSpec("$", "similarity", metric="token_f1", threshold=0.5, max_len_ratio=4.0)
    # The score reaches the threshold, but one answer is over twice as long as the other.
    assert not strict.match(strict.normalize(short), strict.normalize(long))
    assert loose.match(loose.normalize(short), loose.normalize(long))
    # Equal texts always agree, and whitespace and case do not count towards the length.
    assert strict.match(strict.normalize("Refund  issued"), strict.normalize("refund issued"))
    t = tasks.Task("s", format="text", fields=(strict,), policy={"samples": 2})
    assert not t.agree(short, long) and t.agree(short, "Refund issued.")  # text format: trailing '.' dropped


@pytest.mark.req("FR-2.55", "FR-2.14", "INV-16")
def test_mixed_label_and_similarity_fields():
    task = tasks.Task(
        "ticket-summary",
        fields=(FieldSpec("category", "label", values=("billing", "bug")), FieldSpec("summary", "similarity")),
        policy={"samples": 3},
    )
    said = "Customer was charged twice for the March invoice and wants a refund."
    a = json.dumps({"category": "billing", "summary": said})
    b = json.dumps(
        {"category": "Billing", "summary": "The customer was charged twice for the March invoice and asks for a refund."}
    )
    other_label = json.dumps({"category": "bug", "summary": said})
    other_text = json.dumps({"category": "billing", "summary": "The login page shows an error after the update."})
    assert task.agree(a, b) and not task.agree(a, other_label) and not task.agree(a, other_text)
    assert task.format == "json" and task.precision_kind == "proxy" and task.free_text and task.verifiable
    for bad, why in (
        ({"category": "billing"}, "invalid field summary"),
        ({"category": "billing", "summary": "   "}, "invalid field summary"),
        ({"category": "billing", "summary": 12}, "invalid field summary"),
        ({"category": "refunds", "summary": "x"}, "invalid field category"),
    ):
        assert task.check(json.dumps(bad)) == why
    assert json.loads(task.canonical(b)) == {
        "category": "billing",
        "summary": "the customer was charged twice for the march invoice and asks for a refund.",
    }
    s = score(task, [comp(a, 0.9), comp(other_text, 0.99), comp(b, 0.8)])
    assert s.answer.text == a and s.agreement == pytest.approx(2 / 3)  # a and b agree; the outlier gets one vote
    # Free text alone (no checkable field) is a proxy too; a task of labels is exact and not free text.
    assert (SUMMARY_TASK.precision_kind, SUMMARY_TASK.free_text, SUMMARY_TASK.verifiable) == ("proxy", True, False)
    assert (TRIAGE.precision_kind, TRIAGE.free_text, tasks.get("default").free_text) == ("exact", False, True)


@pytest.mark.req("FR-2.55", "FR-2.15")
def test_similarity_task_requires_two_samples():
    """With one sample a confidence score's agreement is always 1, so a task with a similarity field must say itself
    that it is scored on two samples or more (RATCHET_SAMPLES=1 cannot take that away)."""
    field = FieldSpec("$", "similarity")
    for policy in ({}, {"samples": 1}, {"audit_rate": 0.1}):
        with pytest.raises(ValueError, match="needs policy.samples of at least 2"):
            tasks.Task("s", format="text", fields=(field,), policy=policy)
    assert tasks.Task("s", format="text", fields=(field,), policy={"samples": 2}).sample_count(5) == 2
    assert TRIAGE.sample_count(3) == 3 and tasks.Task("t", policy={"samples": 7}).sample_count(3) == 7
    need = "a task with a similarity field needs policy.samples of at least 2 (one sample always agrees with itself)"
    spec = {"name": "s", "format": "text", "fields": {"$": {"type": "similarity"}}}
    with pytest.raises(taskspec.SpecError) as caught:
        taskspec.parse_document({"tasks": [spec]})
    assert caught.value.errors == [f"tasks[0].policy.samples: {need}"]
    with pytest.raises(taskspec.SpecError) as caught:
        taskspec.parse_spec({**spec, "policy": {"samples": 1}})
    assert caught.value.errors == [f"policy.samples: {need}"]
    with pytest.raises(taskspec.SpecError) as caught:  # a bad value is reported once, by its own rule
        taskspec.parse_spec({**spec, "policy": {"samples": 0}})
    assert caught.value.errors == ["policy.samples: must be a whole number from 1 to 16"]
    # `extends` that drops the base's samples is refused too.
    doc = {"tasks": [{**spec, "policy": {"samples": 3}}, {"name": "t", "extends": "s", "policy": {"samples": None}}]}
    with pytest.raises(taskspec.SpecError) as caught:
        taskspec.parse_document(doc)
    assert caught.value.errors == [f"tasks[1].policy.samples: {need}"]
    (ok,) = taskspec.parse_document({"tasks": [{**spec, "policy": {"samples": 3}}]})
    assert taskspec.parse_spec(taskspec.document(ok), "file") == ok  # a similarity task round-trips


@pytest.mark.req("FR-14.40", "FR-5.1", "FR-2.55")
def test_router_and_golden_eval_use_policy_samples_per_task():
    """A task's `policy.samples` is how many samples its confidence score rests on: the router draws that many (the
    shadow too, and at least the client's n), and the golden eval scores each row on its task's count, so a task is
    calibrated on what it is served with. A task without it keeps RATCHET_SAMPLES (3 here)."""
    tasks.register(dataclasses.replace(SUMMARY_TASK, policy={"samples": 5}))
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.5})
    _version(led, "shadow", {"threshold": 0.5})
    student = RecordingProvider(lambda m, model: VALID if "ticket" in m[-1]["content"] else "The refund was sent today.")
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, teacher_reasoning_tokens=0)
    r = Ratchet(s, led, RecordingProvider("teacher"), lambda v: (student, v.adapter_uri), seed=1)
    out = r.handle(_ask("summarise"), "short-summary")
    assert out.served_by == "student" and [c.kwargs["n"] for c in student.calls] == [5, 5]  # live, then the shadow
    student.calls.clear()
    r.handle(_ask("summarise"), "short-summary", n=6)
    assert [c.kwargs["n"] for c in student.calls] == [6, 5]  # six to fill the choices; scored on five, as calibrated
    student.calls.clear()
    r.handle(_ask("ticket 1"), "ticket-triage")
    assert [c.kwargs["n"] for c in student.calls] == [3, 3]
    # The golden eval: each row sampled as often as its task is in production.
    rows = [
        {"task": name, "messages": [{"role": "user", "content": f"{name} {i}"}, {"role": "assistant", "content": ref}]}
        for i in range(4)
        for name, ref in (("short-summary", "The refund was sent today."), ("ticket-triage", VALID))
    ]
    asked: list[tuple[str, int]] = []

    def sample(prompt, n, params=None):
        asked.append((prompt[-1]["content"].split()[0], n))
        return student.complete(prompt, "student-v1", n=n, logprobs=True)

    res = cascade_eval(sample, rows, samples=3)
    assert asked == [("short-summary", 5), ("ticket-triage", 3)] * 4
    assert res["tasks"]["short-summary"]["precision_kind"] == "proxy" and res["precision_kind"] == "proxy"
    assert res["tasks"]["ticket-triage"]["precision_kind"] == "exact"
    assert cascade_eval(sample, rows[1::2], samples=3)["precision_kind"] == "exact"  # tickets only


@pytest.mark.req("FR-2.55", "NFR-1")
def test_similarity_is_bounded_on_long_answers(monkeypatch):
    """Every pair of student samples is compared on the request path: a 300,000-character answer normalises, builds its
    features once and compares in well under the time limit under every metric, and an answer's features are built at
    most once however many samples it is compared with (and never for a validity check alone)."""
    huge = " ".join(f"word{i % 977}" for i in range(40_000))
    other = " ".join(f"word{(i * 7) % 977}" for i in range(40_000))
    start = time.perf_counter()
    for metric in similarity.METRICS:
        f = FieldSpec("$", "similarity", metric=metric)
        a, b = f.normalize(huge), f.normalize(other)
        f.match(a, b)
        f.match(b, a)
    assert time.perf_counter() - start < 2.0
    built: list[str] = []
    real = similarity.features
    monkeypatch.setattr(similarity, "features", lambda metric, text: built.append(text) or real(metric, text))
    texts = ["the refund was sent today", "the refund was sent to you today", "your refund went out today"]
    assert SUMMARY_TASK.valid(texts[0]) and built == []  # a validity check builds nothing
    score(SUMMARY_TASK, [comp(t) for t in texts])
    assert sorted(built) == sorted(texts)  # three samples, three pairs (and themselves): each built once


@pytest.mark.req("FR-14.40", "FR-7.7")
def test_stats_report_proxy_precision_and_judge_pass_rate():
    """/ratchet/stats `tasks[]` says what each task's precision means and, for a free-text task the judge graded, its pass
    rate, from the live version's per-task records; an older or hand-edited record gives None, never a wrong label."""
    tasks.register(SUMMARY_TASK)
    led = Ledger(":memory:")

    def rec(task: tasks.Task, **kw):
        base = {"test_n": 20, "threshold": 0.8, "precision": 0.97, "coverage": 0.9, "spec_hash": task.spec_hash}
        return {**base, "precision_kind": task.precision_kind, **kw}

    records = {
        "short-summary": rec(SUMMARY_TASK, judge_n=18, judge_pass_rate=0.8),
        "ticket-triage": rec(TRIAGE),
        "default": rec(tasks.get("default"), precision_kind="fuzzy", judge_pass_rate="high"),
    }
    live = _version(led, "live", {"threshold": 0.8, "tasks": records})
    for name in records:
        _audit(led, live, 1, task=name)
    rows = {r["task"]: r for r in led.stats_by_task()}
    assert (rows["short-summary"]["precision_kind"], rows["short-summary"]["judge_pass_rate"]) == ("proxy", 0.8)
    assert (rows["ticket-triage"]["precision_kind"], rows["ticket-triage"]["judge_pass_rate"]) == ("exact", None)
    assert (rows["default"]["precision_kind"], rows["default"]["judge_pass_rate"]) == (None, None)
