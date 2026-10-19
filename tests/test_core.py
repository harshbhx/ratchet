import json
import math

import pytest

from ratchet import licenses, tasks
from ratchet.confidence import calibrate_threshold, score
from ratchet.ledger import Ledger
from ratchet.providers import Completion
from ratchet.release import ReleaseGate, ReleasePolicy, wilson


def comp(text, p=0.9, n=5):
    return Completion(text=text, model="m", in_tokens=10, out_tokens=n, token_logprobs=[math.log(p)] * n)


TRIAGE = tasks.get("ticket-triage")
A = json.dumps({"category": "billing", "priority": "high", "reply": "x"})
B = json.dumps({"category": "refund", "priority": "high", "reply": "y"})


# ---------- tasks ----------
def test_task_agree_ignores_free_text_and_reasoning():
    a = '<think>hmm</think>```json\n{"category": "Billing", "priority": "high", "reply": "hello"}\n```'
    b = json.dumps({"category": "billing", "priority": "high", "reply": "totally different"})
    assert TRIAGE.agree(a, b)
    assert not TRIAGE.agree(a, B)


def test_invalid_json_never_agrees():
    assert not TRIAGE.agree("not json", "not json")


# ---------- confidence ----------
def test_unanimous_confident_samples_score_high():
    s = score(TRIAGE, [comp(A, 0.95)] * 3)
    assert s.agreement == 1.0 and s.confidence > 0.9


def test_disagreement_lowers_confidence():
    s = score(TRIAGE, [comp(A, 0.95), comp(B, 0.95), comp(A, 0.5)])
    assert s.agreement == pytest.approx(2 / 3)
    assert s.confidence < 0.85
    assert TRIAGE.agree(s.answer.text, A)


def test_all_invalid_scores_zero():
    assert score(TRIAGE, [comp("nope")] * 3).confidence == 0.0


def test_calibrate_threshold_hits_target_precision():
    pts = [(0.99, True)] * 50 + [(0.9, True)] * 30 + [(0.85, False)] * 5 + [(0.6, False)] * 20
    thr = calibrate_threshold(pts, 0.95)
    served = [ok for c, ok in pts if c >= thr]
    assert sum(served) / len(served) >= 0.95
    assert thr <= 0.9


# ---------- licenses ----------
@pytest.mark.parametrize("model", ["gpt-5.6-sol", "openai/gpt-4o", "claude-opus-5-5", "anthropic/claude-haiku", "gemini-3.1-pro"])
def test_closed_teachers_are_refused(model):
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable(model)


def test_open_teacher_allowed_and_unknown_refused():
    assert licenses.require_distillable("nvidia/Nemotron-3-Ultra-550b-a55b").name == "OpenMDW-1.1"
    with pytest.raises(licenses.LicenseError):
        licenses.require_distillable("someone/unknown-model")


# ---------- ledger ----------
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


def test_bill_of_materials_lists_licenses():
    led = Ledger(":memory:")
    led.add_example("t", [{"role": "user", "content": "q"}], "a", "nvidia/Nemotron-3-Ultra-550b-a55b", "OpenMDW-1.1", "u")
    bom = led.bill_of_materials()
    assert bom["sources"][0]["license"] == "OpenMDW-1.1"
    assert len(bom["examples_sha256"]) == 64


# ---------- release gate ----------
def _version(led, precision, coverage, state="candidate"):
    return led.create_version("base", "a", 100, "h", {"precision": precision, "coverage": coverage}, state)


def test_wilson_bounds():
    lo, hi = wilson(19, 20)
    assert lo < 0.95 < hi
    assert wilson(0, 0) == (0.0, 1.0)


def test_cold_start_goes_to_canary_and_bad_candidates_rejected():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    bad = _version(led, 0.80, 0.9)
    assert not gate.admit(bad)
    assert led.version(bad).state == "rejected"
    good = _version(led, 0.97, 0.8)
    assert gate.admit(good)
    assert led.version(good).state == "canary" and led.version(good).traffic_pct == 10


def test_regression_vs_live_is_rejected_and_good_one_shadows():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    live = _version(led, 0.98, 0.90, state="live")
    worse = _version(led, 0.97, 0.80)
    assert not gate.admit(worse)
    better = _version(led, 0.98, 0.92)
    assert gate.admit(better)
    assert led.version(better).state == "shadow"
    assert led.live().id == live


def _audit(led, vid, n, agree_every):
    for i in range(n):
        led.log_request(
            task="t",
            served_by="student",
            student_version=vid,
            audited=1,
            audit_agree=int(i % agree_every != 0) if agree_every else 1,
            escalated=0,
            cost_usd=0,
            baseline_cost_usd=0,
            latency_ms=1,
        )


def test_canary_advances_then_goes_live():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=10))
    old = _version(led, 0.97, 0.9, state="live")
    v = _version(led, 0.97, 0.9)
    led.set_version(v, "canary", 10)
    _audit(led, v, 12, 0)
    gate.tick()
    assert led.version(v).traffic_pct == 50
    gate.tick()  # no new audits in this stage: must hold
    assert led.version(v).traffic_pct == 50
    _audit(led, v, 12, 0)
    gate.tick()
    assert led.version(v).state == "live" and led.version(old).state == "retired"


def test_canary_rolls_back_on_strong_evidence():
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=10))
    live = _version(led, 0.97, 0.9, state="live")
    v = _version(led, 0.97, 0.9)
    led.set_version(v, "canary", 10)
    _audit(led, v, 20, 2)  # 50% precision
    gate.tick()
    assert led.version(v).state == "rolled_back"
    assert led.live().id == live
    assert any(e["kind"] == "rollback" for e in led.events())
