import json
import math

import pytest
from fakes import _audit, _version

from ratchet import licenses, tasks
from ratchet.confidence import calibrate_threshold, score
from ratchet.ledger import Ledger
from ratchet.providers import Completion
from ratchet.release import ReleaseGate, ReleasePolicy, wilson


def comp(text, p=0.9, n=5, finish_reason="stop"):
    return Completion(
        text=text, model="m", in_tokens=10, out_tokens=n, token_logprobs=[math.log(p)] * n, finish_reason=finish_reason
    )


TRIAGE = tasks.get("ticket-triage")
A = json.dumps({"category": "billing", "priority": "high", "reply": "x"})
B = json.dumps({"category": "refund", "priority": "high", "reply": "y"})


# ---------- tasks ----------
@pytest.mark.req("FR-2.6", "FR-2.7")
def test_task_agree_ignores_free_text_and_reasoning():
    a = '<think>hmm</think>```json\n{"category": "Billing", "priority": "high", "reply": "hello"}\n```'
    b = json.dumps({"category": "billing", "priority": "high", "reply": "totally different"})
    assert TRIAGE.agree(a, b)
    assert not TRIAGE.agree(a, B)


@pytest.mark.req("FR-2.6")
def test_invalid_json_never_agrees():
    assert not TRIAGE.agree("not json", "not json")


@pytest.mark.req("FR-2.7")
@pytest.mark.parametrize(
    ("text", "want"),
    [
        pytest.param('<think>billing, surely</think>{"a": 1}', '{"a": 1}', id="full-pair"),
        pytest.param('Let me think about it</think>\n{"a": 1}', '{"a": 1}', id="closing-only"),
        # The fence stays: strip_reasoning removes reasoning only; clean_answer unwraps fences for structured tasks.
        pytest.param('Let me think</think>\n```json\n{"a":1}\n```', '```json\n{"a":1}\n```', id="closing-only-fenced"),
        pytest.param("<think>the answer is billing, high", "", id="unterminated"),
        pytest.param('<THINK>hmm</THINK> {"a": 1} ', '{"a": 1}', id="uppercase"),
        pytest.param("<Think>hmm</tHiNk>answer", "answer", id="mixed-case"),
        pytest.param("A plain answer, no tags.", "A plain answer, no tags.", id="no-tags"),
        pytest.param("  padded  ", "padded", id="no-tags-padded"),
        pytest.param("billing <think>on second thought", "billing", id="answer-then-unterminated"),
        pytest.param("plan</think>a<think>b</think>c", "ac", id="closing-then-pair"),
        pytest.param("plan</think>answer<think>cut", "answer", id="closing-then-unterminated"),
        pytest.param("<think>a<think>b</think>c</think>answer", "answer", id="nested"),
        pytest.param("<think>a</think>first</think>second", "second", id="last-closing-wins"),
        pytest.param("", "", id="empty"),
        pytest.param(None, "", id="none"),
    ],
)
def test_strip_reasoning_cases(text, want):
    assert tasks.strip_reasoning(text) == want
    assert tasks.strip_reasoning(want) == want  # idempotent: nothing left to strip


@pytest.mark.req("FR-2.7", "FR-2.13")
def test_clean_answer_unwraps_fences_only_for_structured_tasks():
    free = tasks.get("default")
    obj = '{"category": "billing", "priority": "high"}'
    for fenced in (f"```json\n{obj}\n```", f"```\n{obj}\n```", f"```JSON\n{obj}\n```", f"```{obj}```", f"```json\n{obj}```"):
        assert tasks.clean_answer(TRIAGE, fenced) == obj, fenced
        assert tasks.clean_answer(free, fenced) == fenced  # a code block is content in a free-text answer
    assert tasks.clean_answer(TRIAGE, f"<think>billing</think>\n```json\n{obj}\n```") == obj
    assert tasks.clean_answer(TRIAGE, f"Let me see.</think>```json\n{obj}\n```") == obj
    assert tasks.clean_answer(free, "<think>x</think>```py\nprint(1)\n```") == "```py\nprint(1)\n```"
    # Unwrapped only when one fence is the whole answer and its body is JSON.
    for kept in (
        "```json\nbilling, high\n```",  # body is not JSON
        f"```json\n{obj}\n```\nand\n```json\n{obj}\n```",  # two fences
        f"Here it is: ```json\n{obj}\n```",  # prose around the fence
        f"````json\n{obj}\n````",  # a four-backtick fence is not unwrapped
        "``````",
        obj,
    ):
        assert tasks.clean_answer(TRIAGE, kept) == kept, kept
    deep = "```\n" + "[" * 100_000 + "]" * 100_000 + "\n```"  # too deep for the JSON parser: kept, never a crash
    assert tasks.clean_answer(TRIAGE, deep) == deep
    assert TRIAGE.canonical(f"```json\n{obj}\n```") == TRIAGE.canonical(obj) != tasks.INVALID


@pytest.mark.req("FR-2.2", "FR-2.13")
def test_empty_answers_are_invalid_for_every_task():
    free = tasks.get("default")
    for empty in ("", "   \n", "<think>still thinking", "<think>done</think>"):
        assert free.canonical(empty) == TRIAGE.canonical(empty) == tasks.INVALID
        assert not free.agree(empty, empty)
    s = score(free, [comp("<think>never finished")] * 3)
    assert (s.confidence, s.agreement, s.valid) == (0.0, 0.0, False)


# ---------- confidence ----------
@pytest.mark.req("FR-2.2")
def test_unanimous_confident_samples_score_high():
    s = score(TRIAGE, [comp(A, 0.95)] * 3)
    assert s.agreement == 1.0 and s.confidence > 0.9


@pytest.mark.req("FR-2.2")
def test_disagreement_lowers_confidence():
    s = score(TRIAGE, [comp(A, 0.95), comp(B, 0.95), comp(A, 0.5)])
    assert s.agreement == pytest.approx(2 / 3)
    assert s.confidence < 0.85
    assert TRIAGE.agree(s.answer.text, A)


@pytest.mark.req("FR-2.2")
def test_all_invalid_scores_zero():
    assert score(TRIAGE, [comp("nope")] * 3).confidence == 0.0


@pytest.mark.req("FR-2.2", "FR-2.13")
def test_length_samples_are_invalid_in_score():
    """A sample cut off by the token limit (or a content filter) gets no vote and is never the answer, even when its
    visible text parses."""
    cut = score(TRIAGE, [comp(A, 0.99, finish_reason="length")] * 3)
    assert (cut.confidence, cut.agreement, cut.valid) == (0.0, 0.0, False)
    assert not score(TRIAGE, [comp(A, 0.99, finish_reason="content_filter")] * 3).valid
    # Two cut-off samples of A and one complete B: B is the only vote, with agreement 1/3.
    s = score(TRIAGE, [comp(A, 0.99, finish_reason="length"), comp(B, 0.9), comp(A, 0.99, finish_reason="length")])
    assert s.answer.text == B and s.agreement == pytest.approx(1 / 3) and s.valid
    # Complete answers vote as before, including ones that end in a tool call.
    full = score(TRIAGE, [comp(A, 0.95), comp(A, 0.95, finish_reason="tool_calls"), comp(A, 0.95)])
    assert full.agreement == 1.0 and full.confidence > 0.9


@pytest.mark.req("FR-5.2", "FR-2.3")
def test_calibrate_threshold_hits_target_precision():
    pts = [(0.99, True)] * 50 + [(0.9, True)] * 30 + [(0.85, False)] * 5 + [(0.6, False)] * 20
    thr = calibrate_threshold(pts, 0.95)
    served = [ok for c, ok in pts if c >= thr]
    assert sum(served) / len(served) >= 0.95
    assert thr <= 0.9


# ---------- licenses ----------
@pytest.mark.req("FR-3.2", "US-8", "INV-1")
@pytest.mark.parametrize("model", ["gpt-5.6-sol", "openai/gpt-4o", "claude-opus-5-5", "anthropic/claude-haiku", "gemini-3.1-pro"])
def test_closed_teachers_are_refused(model):
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable(model)


@pytest.mark.req("FR-3.1", "FR-3.2", "US-8", "INV-1")
def test_open_teacher_allowed_and_unknown_refused():
    assert licenses.require_distillable("nvidia/Nemotron-3-Ultra-550b-a55b").name == "OpenMDW-1.1"
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable("someone/unknown-model")


# ---------- ledger ----------
@pytest.mark.req("FR-3.4", "FR-4.3")
def test_examples_dedupe_and_count_and_stable_split():
    led = Ledger(":memory:", golden_fraction=0.5)
    msgs = [{"role": "user", "content": "hi"}]
    assert led.add_example("t", msgs, "a", "mock-teacher", "mock", "")
    assert not led.add_example("t", msgs, "a", "mock-teacher", "mock", "")
    rows = led.examples()
    assert len(rows) == 1 and rows[0]["seen"] == 2
    led2 = Ledger(":memory:", golden_fraction=0.5)
    led2.add_example("t", msgs, "a", "mock-teacher", "mock", "")
    assert led2.examples()[0]["split"] == rows[0]["split"]


@pytest.mark.req("FR-3.5", "US-7")
def test_bill_of_materials_lists_licenses():
    led = Ledger(":memory:")
    led.add_example("t", [{"role": "user", "content": "q"}], "a", "nvidia/Nemotron-3-Ultra-550b-a55b", "OpenMDW-1.1", "u")
    bom = led.bill_of_materials()
    assert bom["sources"][0]["license"] == "OpenMDW-1.1"
    assert len(bom["examples_sha256"]) == 64


# ---------- release gate ----------
def _pc(precision, coverage):
    return {"precision": precision, "coverage": coverage}


@pytest.mark.req("FR-6.6", "NFR-8")
def test_wilson_bounds():
    lo, hi = wilson(19, 20)
    assert lo < 0.95 < hi
    assert wilson(0, 0) == (0.0, 1.0)


@pytest.mark.req("FR-6.1", "FR-6.2", "US-5")
def test_cold_start_goes_to_canary_and_bad_candidates_rejected():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    bad = _version(led, "candidate", _pc(0.80, 0.9))
    assert not gate.admit(bad)
    assert led.version(bad).state == "rejected"
    good = _version(led, "candidate", _pc(0.97, 0.8))
    assert gate.admit(good)
    assert led.version(good).state == "canary" and led.version(good).traffic_pct == 10


@pytest.mark.req("FR-6.1", "US-5")
def test_regression_vs_live_is_rejected_and_good_one_shadows():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    live = _version(led, "live", _pc(0.98, 0.90))
    worse = _version(led, "candidate", _pc(0.97, 0.80))
    assert not gate.admit(worse)
    better = _version(led, "candidate", _pc(0.98, 0.92))
    assert gate.admit(better)
    assert led.version(better).state == "shadow"
    assert led.live().id == live


@pytest.mark.req("FR-6.5", "US-5")
def test_canary_advances_then_goes_live():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=10))
    old = _version(led, "live", _pc(0.97, 0.9))
    v = _version(led, "candidate", _pc(0.97, 0.9))
    led.set_version(v, "canary", 10)
    _audit(led, v, 12, 0)
    gate.tick()
    assert led.version(v).traffic_pct == 50
    gate.tick()  # no new audits in this stage: must hold
    assert led.version(v).traffic_pct == 50
    _audit(led, v, 12, 0)
    gate.tick()
    assert led.version(v).state == "live" and led.version(old).state == "retired"


@pytest.mark.req("FR-6.6", "US-6")
def test_canary_rolls_back_on_strong_evidence():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=10))
    live = _version(led, "live", _pc(0.97, 0.9))
    v = _version(led, "candidate", _pc(0.97, 0.9))
    led.set_version(v, "canary", 10)
    _audit(led, v, 20, 2)  # 50% precision
    gate.tick()
    assert led.version(v).state == "rolled_back"
    assert led.live().id == live
    assert any(e["kind"] == "rollback" for e in led.events())
