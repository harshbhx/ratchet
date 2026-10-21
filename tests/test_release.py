"""The release gate's rules (golden gate, the judge gate on free-text tasks, one candidate in flight, shadow, canary,
rollback, the anytime-valid canary and
live alert) and the router's side of a release: shadow arbitration, the canary traffic split and audit focusing."""

from __future__ import annotations

import ast
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fakes import OPEN_TEACHER, RecordingProvider, _audit, _shadow, _version
from fastapi.testclient import TestClient

from ratchet import licenses, stats, tasks
from ratchet.compare import FieldSpec
from ratchet.confidence import SERVE_NOTHING
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import CANARY_STEPS, CS_ALPHA, ReleaseGate, ReleasePolicy, wilson
from ratchet.router import Ratchet
from sim.mock_models import MockFleet, MockTeacher

GOOD = {"precision": 0.98, "coverage": 0.60, "threshold": 0.5}


def _kinds(led, *kinds):
    return [e for e in led.events() if e["kind"] in kinds]


# ---------- the tick's steps (ADR 0024) ----------
def test_tick_runs_each_step_once_in_order_with_the_live_version_it_started_with():
    """`tick` is five steps, each owned by one feature: drift, live alert, shadow, canary, watchers. `live` is read
    once, before any of them, so a canary promoted in this tick retires the version the tick started with."""
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(min_audits=4))
    old_live = _version(led, "live", GOOD)
    canary = _version(led, "canary", GOOD, pct=50)
    _audit(led, canary, 4)  # 4 of 4 agree: promoted from the last canary stage
    calls = []
    for step in ("_tick_drift", "_tick_live", "_tick_shadow", "_tick_canary", "_tick_watchers"):
        original = getattr(gate, step)

        def record(*args, _step=step, _original=original):
            calls.append((_step, [a.id if a is not None else None for a in args]))
            return _original(*args)

        setattr(gate, step, record)
    gate.tick()
    assert calls == [
        ("_tick_drift", []),
        ("_tick_live", [old_live]),
        ("_tick_shadow", [old_live]),
        ("_tick_canary", [old_live]),
        ("_tick_watchers", []),
    ]
    assert led.live().id == canary and led.version(old_live).state == "retired"
    empty = Ledger(":memory:")
    ReleaseGate(empty).tick()  # the drift and watcher steps do nothing until their features land
    assert empty.events() == []


@pytest.mark.parametrize(
    "field, value",
    [
        ("live_floor", 1.0),
        ("live_floor", 0.0),
        ("min_precision", 1.2),
        ("min_coverage", -0.1),
        ("min_audits", -1),
        ("min_audits", 2.5),
        ("min_shadow", True),
        ("tolerance", float("inf")),
    ],
)
def test_policy_refuses_values_out_of_range(field, value):
    with pytest.raises(ValueError, match=f"RATCHET_{field.upper()}"):
        ReleasePolicy(**{field: value})


# ---------- golden gate ----------
@pytest.mark.req("FR-6.1")
def test_gate_rejects_low_coverage():
    p = ReleasePolicy()
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    low = _version(led, "candidate", {"precision": 0.99, "coverage": p.min_coverage - 0.01})
    assert not gate.admit(low)
    assert led.version(low).state == "rejected" and "golden coverage" in led.events()[-1]["detail"]
    missing = _version(led, "candidate", {"precision": 0.99})  # an eval without coverage fails closed
    assert not gate.admit(missing) and led.version(missing).state == "rejected"
    at_floor = _version(led, "candidate", {"precision": 0.99, "coverage": p.min_coverage})
    assert gate.admit(at_floor) and led.version(at_floor).state == "canary"


@pytest.mark.req("FR-6.1")
@pytest.mark.parametrize("metric", ["precision", "coverage"])
def test_gate_tolerance_boundary(metric):
    p = ReleasePolicy()
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    _version(led, "live", GOOD)
    beyond = _version(led, "candidate", {**GOOD, metric: GOOD[metric] - p.tolerance - 1e-6})
    assert not gate.admit(beyond)
    assert led.version(beyond).state == "rejected" and "regresses vs live v1" in led.events()[-1]["detail"]
    at = _version(led, "candidate", {**GOOD, metric: GOOD[metric] - p.tolerance})  # exactly `tolerance` worse: allowed
    assert gate.admit(at) and led.version(at).state == "shadow"


@pytest.mark.req("FR-6.8")
def test_admit_retires_older_inflight():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    live = _version(led, "live", GOOD)
    shadow = _version(led, "shadow", GOOD)
    canary = _version(led, "canary", GOOD, pct=50)
    bad = _version(led, "candidate", {**GOOD, "precision": 0.5})
    assert not gate.admit(bad)  # a rejected candidate leaves the release in flight alone
    assert [led.version(v).state for v in (shadow, canary)] == ["shadow", "canary"]

    new = _version(led, "candidate", GOOD)
    assert gate.admit(new)
    assert [(v.id, v.state, v.traffic_pct) for v in led.versions()] == [
        (live, "live", 100),
        (shadow, "retired", 0),
        (canary, "retired", 0),
        (bad, "rejected", 0),
        (new, "shadow", 0),
    ]
    assert [e["detail"] for e in _kinds(led, "retired")] == [
        f"v{shadow} replaced by newer candidate v{new}",
        f"v{canary} replaced by newer candidate v{new}",
    ]
    assert len(led.versions(["shadow", "canary"])) == 1


# ---------- shadow ----------
def _shadowing():
    led = Ledger(":memory:")
    live = _version(led, "live", GOOD)
    cand = _version(led, "shadow", GOOD)
    return led, ReleaseGate(led), live, cand


@pytest.mark.req("FR-6.4", "US-5")
def test_shadow_promotes_and_both_rejections():
    p = ReleasePolicy()

    # Promotion: min_shadow would-serve answers at exactly min_precision, and the same coverage as live.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, p.min_shadow - 1, agree_every=20, live_vid=live)
    gate.tick()
    assert led.version(cand).state == "shadow"  # 99 answers: not enough evidence yet, keep shadowing
    _shadow(led, cand, 1, live_vid=live)
    s = led.shadow_quality(cand)
    assert (s["served"], s["precision"], s["coverage"], s["live_coverage"]) == (100, 0.95, 1.0, 1.0)
    gate.tick()
    v = led.version(cand)
    assert (v.state, v.traffic_pct) == ("canary", CANARY_STEPS[0])
    assert led.live().id == live and _kinds(led, "canary")[-1]["detail"].startswith(f"v{cand} shadow precision 95.0%")

    # The evidence is would-serve answers, not shadowed requests: 150 requests of which it would serve 75 is not enough.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, 150, serve_every=2, live_vid=live, live_escalate_every=2)
    s = led.shadow_quality(cand)
    assert (s["n"], s["served"], s["precision"], s["coverage"], s["live_coverage"]) == (150, 75, 1.0, 0.5, 0.5)
    gate.tick()
    assert led.version(cand).state == "shadow" and not _kinds(led, "canary", "rejected")
    _shadow(led, cand, 50, serve_every=2, live_vid=live, live_escalate_every=2)  # 100 would-serve answers out of 200
    gate.tick()
    assert led.version(cand).state == "canary"

    # Rejection on precision: 90% of what it would serve agrees with the reference.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, 200, agree_every=10, live_vid=live)
    gate.tick()
    assert led.version(cand).state == "rejected" and "shadow precision 90.0% < 95%" in led.events()[-1]["detail"]
    assert led.live().id == live

    # Rejection on coverage: precise, but it would serve half of what the live student already serves.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, 400, serve_every=2, live_vid=live)
    s = led.shadow_quality(cand)
    assert (s["served"], s["precision"], s["coverage"]) == (200, 1.0, 0.5)
    gate.tick()
    assert led.version(cand).state == "rejected" and "shadow coverage 50.0% < live 100.0%" in led.events()[-1]["detail"]
    assert led.live().id == live

    # Coverage is judged against the live student's real coverage, not against 100%.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, 400, serve_every=2, live_vid=live, live_escalate_every=2)
    gate.tick()
    assert led.version(cand).state == "canary"


@pytest.mark.req("FR-6.4", "FR-2.11")
def test_shadow_coverage_is_not_compared_with_requests_the_live_student_never_answered():
    """Regression: while the live student's breaker was open (or its call failed) and the shadow's was not, those rows
    counted as the live student escalating, which lowered the coverage bar the candidate had to clear."""
    for skipped in ("student_breaker", "student_error"):
        led, gate, live, cand = _shadowing()
        _shadow(led, cand, 400, serve_every=2, live_vid=live, live_escalate_every=2, live_route=skipped)
        s = led.shadow_quality(cand)
        # Live answered 200 requests and served them all; the 200 it never answered stay in n and the shadow's coverage.
        assert (s["n"], s["served"], s["coverage"], s["live_coverage"]) == (400, 200, 0.5, 1.0)
        gate.tick()
        assert led.version(cand).state == "rejected"
        assert "shadow coverage 50.0% < live 100.0%" in led.events()[-1]["detail"]

    # The same pattern with the live student unsure (route confidence) is real live coverage of 50%: promoted.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, 400, serve_every=2, live_vid=live, live_escalate_every=2)
    assert led.shadow_quality(cand)["live_coverage"] == 0.5
    gate.tick()
    assert led.version(cand).state == "canary"


@pytest.mark.req("FR-6.4", "FR-1.14")
def test_shadow_coverage_counts_an_n_choices_escalation_as_live_served():
    """A request the live student was confident about, escalated only because fewer than the client's n samples agreed
    (route `n_choices`), would have been served as one choice; the shadow's verdict uses that one-choice rule, so the
    live student is held to it too. Counting those rows as escalations would lower the bar for the candidate."""
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, 400, serve_every=2, live_vid=live, live_escalate_every=2, live_route="n_choices")
    s = led.shadow_quality(cand)
    assert (s["n"], s["served"], s["coverage"], s["live_coverage"]) == (400, 200, 0.5, 1.0)
    gate.tick()
    assert led.version(cand).state == "rejected"
    assert "shadow coverage 50.0% < live 100.0%" in led.events()[-1]["detail"]


@pytest.mark.req("FR-6.4", "FR-2.10", "NFR-8")
def test_shadow_gate_counts_unarbitrated_disagreements_against_the_candidate():
    """Arbitration only runs when the shadow disagrees with the served answer, so a failed one is a disagreement whose
    outcome is unknown. Dropping those rows would raise the precision of the candidate that disagrees most."""
    p = ReleasePolicy()

    # Every arbitrated answer agrees, but the teacher could not arbitrate 10 of the 100: 90%, not 100%, and rejected.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, p.min_shadow, unarbitrated_every=10, live_vid=live)
    s = led.shadow_quality(cand)
    assert (s["n"], s["served"], s["unarbitrated"], s["precision"], s["coverage"]) == (100, 100, 10, 0.9, 1.0)
    gate.tick()
    assert led.version(cand).state == "rejected"
    assert led.events()[-1]["detail"] == f"v{cand}: shadow precision 90.0% < 95% (10 unarbitrated counted as disagreements)"

    # Two of 100 still leave 98%: promoted, and the event says what the number includes.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, p.min_shadow, unarbitrated_every=50, live_vid=live)
    assert led.shadow_quality(cand)["unarbitrated"] == 2
    gate.tick()
    assert led.version(cand).state == "canary"
    assert _kinds(led, "canary")[-1]["detail"].startswith(
        f"v{cand} shadow precision 98.0% (2 unarbitrated counted as disagreements), coverage 100.0%"
    )

    # A row with no verdict (shadow_serve NULL) counts toward nothing: not n, coverage, the evidence count or precision.
    led, gate, live, cand = _shadowing()
    _shadow(led, cand, p.min_shadow - 1, live_vid=live)
    for _ in range(50):
        led.log_request(
            task="t",
            served_by="teacher",
            student_version=live,
            escalated=1,
            audited=0,
            shadow_version=cand,
            shadow_serve=None,
            shadow_agree=None,
            cost_usd=0.0,
            baseline_cost_usd=0.0,
            latency_ms=1.0,
        )
    s = led.shadow_quality(cand)
    assert s == {"n": 99, "served": 99, "precision": 1.0, "coverage": 1.0, "live_coverage": 1.0, "unarbitrated": 0}
    gate.tick()
    assert led.version(cand).state == "shadow"  # 99 would-serve answers: still not enough evidence


# ---------- canary ----------
@pytest.mark.req("FR-6.5", "FR-6.6")
def test_canary_holds_without_strong_evidence():
    p = ReleasePolicy()  # 20 audits per stage, floor 92%
    led = Ledger(":memory:")
    gate = ReleaseGate(led, p)
    live = _version(led, "live", GOOD)
    cand = _version(led, "canary", GOOD)
    _audit(led, cand, p.min_audits, agree_every=10)  # 18 of 20 agree
    lo, hi = wilson(18, 20)
    assert 18 / 20 < p.live_floor < hi  # below the floor, but not strong evidence that it is worse

    for _ in range(3):
        gate.tick()
    v = led.version(cand)
    assert (v.state, v.traffic_pct) == ("canary", CANARY_STEPS[0])
    assert not _kinds(led, "canary", "live", "rollback") and led.live().id == live

    _audit(led, cand, 20)  # 38 of 40: now it has earned the next step
    gate.tick()
    assert led.version(cand).traffic_pct == CANARY_STEPS[1]

    # The rollback boundary: 16 of 20 is the most agreement that still rolls back (Wilson upper bound 91.9%, just under
    # the floor); 17 of 20 (94.8%) is not strong evidence and only holds the stage.
    for agree, disagree_every, rolled_back in ((16, 5, True), (17, 7, False)):
        led = Ledger(":memory:")
        gate = ReleaseGate(led, p)
        live = _version(led, "live", GOOD)
        cand = _version(led, "canary", GOOD)
        _audit(led, cand, p.min_audits, agree_every=disagree_every)
        assert led.version_live_quality(cand) == (20, agree / 20)
        hi = wilson(agree, 20)[1]
        assert (hi < p.live_floor) == rolled_back and abs(hi - p.live_floor) < 0.03, (agree, hi)
        gate.tick()
        v = led.version(cand)
        assert (v.state, v.traffic_pct) == (("rolled_back", 0) if rolled_back else ("canary", CANARY_STEPS[0])), agree
        assert led.live().id == live and not _kinds(led, "canary", "live")
        rollbacks = [e["detail"] for e in _kinds(led, "rollback")]
        assert len(rollbacks) == int(rolled_back)
        assert all(d.endswith(f"traffic back to live v{live}") for d in rollbacks)


@pytest.mark.req("FR-6.6", "US-6")
def test_rollback_without_live_returns_traffic_to_teacher():
    led = Ledger(":memory:")
    gate = ReleaseGate(led)
    cand = _version(led, "canary", GOOD)  # cold start: the first student, no live version behind it
    _audit(led, cand, 20, agree_every=2)  # 50% audited precision
    gate.tick()
    v = led.version(cand)
    assert (v.state, v.traffic_pct) == ("rolled_back", 0) and led.live() is None
    last = led.events()[-1]
    assert last["kind"] == "rollback" and last["detail"].endswith("traffic back to the teacher")

    teacher = RecordingProvider(json.dumps({"category": "billing", "priority": "low"}))
    student = RecordingProvider("must not be asked")
    r = Ratchet(Settings(teacher_model=OPEN_TEACHER, audit_rate=0.0), led, teacher, lambda v: (student, v.adapter_uri), seed=3)
    for i in range(50):
        out = r.handle([{"role": "user", "content": f"after the rollback {i}"}], "ticket-triage")
        assert (out.served_by, out.version, out.escalated) == ("teacher", None, False)
    assert student.calls == [] and len(teacher.calls) == 50


# ---------- the router's side: shadow arbitration and the canary split ----------
A = json.dumps({"category": "billing", "priority": "high"})
B = json.dumps({"category": "refund", "priority": "high"})


@pytest.mark.req("FR-6.3", "NFR-3", "INV-7")
def test_shadow_arbitration_calls_teacher_once_and_stores_example():
    led = Ledger(":memory:")
    live = _version(led, "live", {"threshold": 0.5})
    cand = _version(led, "shadow", {"threshold": 0.5})
    live_student = RecordingProvider(A)

    def shadow_says(msgs, model):
        text = msgs[-1]["content"]
        return A if "agrees" in text else "not json" if "unsure" in text else B

    shadow_student = RecordingProvider(shadow_says)
    teacher = RecordingProvider(B, in_tokens=50, out_tokens=20)
    students = {live: live_student, cand: shadow_student}
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0)
    r = Ratchet(s, led, teacher, lambda v: (students[v.id], v.adapter_uri), seed=1)
    samples = s.price("student").cost(10, 5 * 3)  # one student call: 3 samples
    teacher_call = s.price(OPEN_TEACHER).cost(50, 20)

    def last_request():
        return dict(led.db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 1").fetchone())

    # The shadow would serve B where the live student served A: the teacher arbitrates, once.
    out = r.handle([{"role": "user", "content": "I was charged twice"}], "ticket-triage")
    assert (out.served_by, out.text) == ("student", A)  # the user keeps the live student's answer
    assert len(teacher.calls) == 1
    examples = led.examples()
    assert len(examples) == 1 and (examples[0]["completion"], examples[0]["teacher_model"]) == (B, OPEN_TEACHER)
    row = last_request()
    assert (row["shadow_version"], row["shadow_serve"], row["shadow_agree"], row["audited"]) == (cand, 1, 1, 0)
    assert out.cost_usd == pytest.approx(2 * samples + teacher_call)  # live samples + shadow samples + arbitration
    assert row["cost_usd"] == pytest.approx(out.cost_usd)

    # No disagreement, or a shadow that would not serve: no teacher call, and only the samples are paid for.
    for text, serve in (("the shadow agrees", 1), ("the shadow is unsure", 0)):
        out = r.handle([{"role": "user", "content": text}], "ticket-triage")
        assert len(teacher.calls) == 1 and len(led.examples()) == 1
        assert last_request()["shadow_serve"] == serve
        assert out.cost_usd == pytest.approx(2 * samples)

    # An audit is the reference when there is one: the shadow is graded against it without a second teacher call.
    audited = Ratchet(Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=1.0), led, teacher, r.resolve_student)
    out = audited.handle([{"role": "user", "content": "audited request"}], "ticket-triage")
    assert len(teacher.calls) == 2 and len(led.examples()) == 2
    row = last_request()
    assert (row["audited"], row["audit_agree"], row["shadow_serve"], row["shadow_agree"]) == (1, 0, 1, 1)
    assert out.cost_usd == pytest.approx(2 * samples + teacher_call)  # the audit is counted too

    # A shadow whose endpoint is down never touches the request: no shadow record, no teacher call, no extra cost.
    down = RecordingProvider(B, raise_on={"student-v2"})
    broken = {live: live_student, cand: down}
    r3 = Ratchet(s, led, teacher, lambda v: (broken[v.id], v.adapter_uri), seed=1)
    out = r3.handle([{"role": "user", "content": "while the shadow is down"}], "ticket-triage")
    assert (out.served_by, out.text, len(down.calls), len(teacher.calls)) == ("student", A, 1, 2)
    assert last_request()["shadow_version"] is None and out.cost_usd == pytest.approx(samples)


@pytest.mark.req("FR-2.8")
def test_canary_traffic_split():
    z99 = 2.576  # 99% two-sided
    n = 4000
    for pct in (10, 50):
        led = Ledger(":memory:")
        live = _version(led, "live", GOOD)
        canary = _version(led, "canary", GOOD, pct=pct)
        r = Ratchet(Settings(teacher_model=OPEN_TEACHER), led, RecordingProvider("t"), lambda v: (None, ""), seed=pct)
        picks = Counter(r._pick_version().id for _ in range(n))
        assert set(picks) == {live, canary}
        lo, hi = wilson(picks[canary], n, z=z99)
        assert lo <= pct / 100 <= hi, (pct, picks)

    # Edges: 0% never picks the canary; 100% always does; at cold start the rest of the traffic goes to the teacher.
    for pct, live_state, want in ((0, "live", {1}), (100, "live", {2}), (10, "retired", {None, 2})):
        led = Ledger(":memory:")
        _version(led, live_state, GOOD)
        _version(led, "canary", GOOD, pct=pct)
        r = Ratchet(Settings(teacher_model=OPEN_TEACHER), led, RecordingProvider("t"), lambda v: (None, ""), seed=7)
        picked = {v.id if v else None for v in (r._pick_version() for _ in range(500))}
        assert picked == want, (pct, live_state)


# ---------- the anytime-valid gate (FR-16.1, FR-16.2, ADR 0025) ----------
def _gate(policy: ReleasePolicy, live: bool = True) -> tuple[Ledger, ReleaseGate, int | None, int]:
    """A ledger with a live version (unless `live` is False: cold start) and a canary at 10%, under `policy`."""
    led = Ledger(":memory:")
    live_id = _version(led, "live", GOOD) if live else None
    return led, ReleaseGate(led, policy), live_id, _version(led, "canary", GOOD)


@pytest.mark.req("FR-16.1", "US-6")
def test_sequential_canary_rolls_back_after_two_failures():
    """With no minimum audit count, two disagreeing audits are enough evidence (e-value 29.8 >= 20 at alpha 5%); one
    (4.22) is not, and no audit is no decision. The Wilson rule holds on the same two audits until it has min_audits."""
    led, gate, live, cand = _gate(ReleasePolicy(sequential=1, min_audits=15))
    gate.tick()
    assert led.version(cand).state == "canary" and not _kinds(led, "rollback")  # no audits: no decision
    _audit(led, cand, 1, agree_every=1)
    gate.tick()
    assert led.version(cand).state == "canary"  # 0/1: e = 4.22
    _audit(led, cand, 1, agree_every=1)
    gate.tick()
    v = led.version(cand)
    assert (v.state, v.traffic_pct) == ("rolled_back", 0) and led.live().id == live
    (detail,) = [e["detail"] for e in _kinds(led, "rollback")]
    assert detail == (
        f"v{cand} rolled back: 0/2 audits agree; e-value 29.8 >= 20 (anytime-valid, alpha 5%); 95% CS [0.001, 0.931]; "
        f"traffic back to live v{live}"
    )
    assert not _kinds(led, "canary", "live", "alert")

    led, gate, live, cand = _gate(ReleasePolicy(sequential=0, min_audits=15))  # the default rule: Wilson, opt-in stays off
    _audit(led, cand, 2, agree_every=1)
    for _ in range(3):
        gate.tick()
    assert led.version(cand).state == "canary" and not _kinds(led, "rollback")


@pytest.mark.req("FR-16.1", "US-6")
def test_sequential_rollback_at_cold_start_returns_traffic_to_the_teacher():
    led, gate, live, cand = _gate(ReleasePolicy(sequential=1), live=False)
    _audit(led, cand, 3, agree_every=1)
    gate.tick()
    assert led.version(cand).state == "rolled_back" and led.live() is None
    assert _kinds(led, "rollback")[0]["detail"].endswith("traffic back to the teacher")


@pytest.mark.req("FR-16.1")
def test_sequential_rollback_counts_every_audit_of_the_canarys_life():
    """Rollback is one e-process over the canary's whole life, never restarted per stage, so its error rate needs no
    union bound over stages: 40 agreeing audits at 10% still count at 50%, where 2 failures alone would have reached 20.
    The price is that a long good stage dilutes later failures: here it takes 11."""
    led, gate, live, cand = _gate(ReleasePolicy(sequential=1))
    _audit(led, cand, 40)
    gate.tick()
    assert led.version(cand).traffic_pct == CANARY_STEPS[1]
    _audit(led, cand, 2, agree_every=1)
    gate.tick()
    assert stats.e_bernoulli(0, 2, 0.92, "below") >= 20 > stats.e_bernoulli(40, 42, 0.92, "below")
    assert led.version(cand).state == "canary"  # held: 40/42 over its life, and 0/2 is below the floor
    _audit(led, cand, 8, agree_every=1)
    gate.tick()
    assert led.version(cand).state == "canary"  # 40/50: e = 11.1
    _audit(led, cand, 1, agree_every=1)
    gate.tick()
    assert led.version(cand).state == "rolled_back" and led.live().id == live
    assert _kinds(led, "rollback")[0]["detail"].startswith(f"v{cand} rolled back: 40/51 audits agree; e-value 23.2 >= 20")


@pytest.mark.req("FR-16.1", "FR-6.5")
def test_sequential_promotion_needs_evidence():
    """With promote_evidence=1 a stage advances only on an e-value of at least 1/alpha_promote that precision is above the
    floor: 20 agreeing audits give 2.57 and hold; 60 give 27.6 and advance. The count restarts in each stage, and 56
    agreeing audits (21.3) is the fewest that pass at a 92% floor and alpha 5% (55 give 19.996)."""
    led, gate, live, cand = _gate(ReleasePolicy(sequential=1, promote_evidence=1))
    _audit(led, cand, 20)
    gate.tick()
    assert led.version(cand).traffic_pct == CANARY_STEPS[0] and not _kinds(led, "canary", "live")
    assert gate.evidence(led.version(cand))["e_promote"] == pytest.approx(2.573, abs=1e-3)
    _audit(led, cand, 40)
    gate.tick()
    assert led.version(cand).traffic_pct == CANARY_STEPS[1]
    (step,) = [e["detail"] for e in _kinds(led, "canary")]
    assert step.startswith(f"v{cand} audited precision 100.0% (n=60); canary 50%; ")  # the old text, then the evidence
    assert step.endswith("; e-value 27.6 >= 20 (anytime-valid, alpha 5%) that precision is above 92%; 95% CS [0.916, 0.999]")
    _audit(led, cand, 55)
    gate.tick()
    assert led.version(cand).state == "canary"  # this stage's 55 agreeing audits: e = 19.996
    _audit(led, cand, 1)
    gate.tick()
    assert led.live().id == cand and led.version(live).state == "retired"
    (promoted,) = [e["detail"] for e in _kinds(led, "live")]
    assert promoted.startswith(f"v{cand} promoted to 100% (audited precision 100.0%, n=56); e-value 21.3 >= 20")

    # The point rule (promote_evidence=0) promotes on 20 agreeing audits, as the Wilson rule does.
    led, gate, live, cand = _gate(ReleasePolicy(sequential=1))
    _audit(led, cand, 20)
    gate.tick()
    assert led.version(cand).traffic_pct == CANARY_STEPS[1]
    assert _kinds(led, "canary")[0]["detail"].endswith("; point estimate at or above 92%; 95% CS [0.741, 0.999]")


@pytest.mark.req("FR-16.1")
@pytest.mark.parametrize(
    "policy, audits, disagree_every",
    [
        (ReleasePolicy(sequential=1, max_stage_audits=50), 50, 10),  # 45/50: below the floor, no evidence it is worse
        (ReleasePolicy(sequential=1, promote_evidence=1, max_stage_audits=60), 60, 12),  # 55/60: neither e-value
    ],
)
def test_futility_retires_inconclusive_canary(policy, audits, disagree_every):
    led, gate, live, cand = _gate(policy)
    _audit(led, cand, audits - 1, agree_every=disagree_every)
    gate.tick()
    assert led.version(cand).state == "canary"  # one audit short of the cap: holds
    _audit(led, cand, 1)
    gate.tick()
    v = led.version(cand)
    assert (v.state, v.traffic_pct) == ("retired", 0) and led.live().id == live and led.by_state("canary") is None
    (detail,) = [e["detail"] for e in _kinds(led, "inconclusive")]
    agreed = audits - math.ceil((audits - 1) / disagree_every)
    lo, hi = stats.confidence_sequence(agreed, audits)
    assert detail == (
        f"v{cand} retired after {audits} audits at canary 10% without enough evidence either way ({agreed}/{audits} agree); "
        f"95% CS [{lo:.3f}, {hi:.3f}]; traffic back to live v{live}"
    )
    assert not _kinds(led, "rollback", "canary", "live")


@pytest.mark.req("FR-16.1")
def test_futility_counts_audits_not_time():
    """A canary that gets no audits holds, however often the gate runs (as under the Wilson rule)."""
    led, gate, _, cand = _gate(ReleasePolicy(sequential=1, max_stage_audits=20))
    for i in range(500):  # served, never audited
        led.log_request(task="t", served_by="student", student_version=cand, audited=0, cost_usd=0.0, baseline_cost_usd=0.0)
        if i % 50 == 0:
            gate.tick()
    assert led.version(cand).state == "canary" and not _kinds(led, "inconclusive", "rollback")


@pytest.mark.req("FR-16.1")
def test_sequential_policy_refuses_settings_that_decide_nothing():
    with pytest.raises(ValueError, match="RATCHET_PROMOTE_EVIDENCE=1 needs RATCHET_SEQUENTIAL=1"):
        ReleasePolicy(promote_evidence=1)
    with pytest.raises(ValueError, match="RATCHET_MAX_STAGE_AUDITS=10 is below RATCHET_MIN_AUDITS=20"):
        ReleasePolicy(sequential=1, max_stage_audits=10)
    with pytest.raises(ValueError, match="a stage needs 56 audits that all agree"):
        ReleasePolicy(sequential=1, promote_evidence=1, max_stage_audits=50)
    with pytest.raises(ValueError, match="RATCHET_MAX_STAGE_AUDITS=400 is too few.*needs 450 audits"):
        ReleasePolicy(sequential=1, promote_evidence=1, live_floor=0.99)
    for ok in (
        ReleasePolicy(sequential=1, promote_evidence=1, max_stage_audits=56),
        ReleasePolicy(sequential=1, promote_evidence=1, max_stage_audits=0),  # no cap
        ReleasePolicy(sequential=1, max_stage_audits=20),
        ReleasePolicy(max_stage_audits=5),  # the cap applies only to the sequential rule
    ):
        assert ok.sequential in (0, 1)
    for field in ("alpha_rollback", "alpha_promote", "alpha_alert"):
        for bad in (0.0, 1.0):
            with pytest.raises(ValueError, match=f"RATCHET_{field.upper()}.*strictly between 0 and 1"):
                ReleasePolicy(**{field: bad})
    for field in ("sequential", "promote_evidence", "alert_eprocess"):
        with pytest.raises(ValueError, match=f"RATCHET_{field.upper()}.*0 or 1"):
            ReleasePolicy(**{field: 2})


@pytest.mark.req("FR-16.2", "FR-6.7")
def test_live_alert_uses_the_e_process():
    """The live alert's default rule: once the e-value against 'precision >= floor' over the audits since the version
    went live reaches 1/alpha_alert (100): 0/2 (29.8) does not, 0/3 (268) does, with no minimum count. Audits from
    before it went live do not count, and a version that keeps agreeing is never alerted on."""
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy())  # alert_eprocess=1, alpha_alert=0.01
    vid = _version(led, "canary", GOOD)
    _audit(led, vid, 5, agree_every=1)  # as a canary: not the live alert's business
    led.set_version(vid, "live", 100)
    _audit(led, vid, 2, agree_every=1)
    gate.tick()
    assert not _kinds(led, "alert")
    _audit(led, vid, 1, agree_every=1)
    for _ in range(3):
        gate.tick()
    (detail,) = [e["detail"] for e in _kinds(led, "alert")]
    assert detail == (
        f"live v{vid}: 0/3 audits agree; e-value 268 >= 100 (anytime-valid, alpha 1%); 95% CS [0.001, 0.843]; "
        "investigate or roll back manually (docs/RUNBOOK.md)"
    )
    assert led.version(vid).state == "live"  # never rolled back automatically

    good = Ledger(":memory:")
    gate = ReleaseGate(good, ReleasePolicy())
    vid = _version(good, "live", GOOD)
    for _ in range(20):
        _audit(good, vid, 20, agree_every=20)  # 19 of 20 at a time: above the floor
        gate.tick()
    assert not _kinds(good, "alert")

    # alert_eprocess=0 keeps the Wilson rule: no alert before min_audits, however bad.
    led = Ledger(":memory:")
    gate = ReleaseGate(led, ReleasePolicy(alert_eprocess=0))
    vid = _version(led, "live", GOOD)
    _audit(led, vid, 19, agree_every=1)
    gate.tick()
    assert not _kinds(led, "alert")
    _audit(led, vid, 1, agree_every=1)
    gate.tick()
    assert _kinds(led, "alert")[0]["detail"].startswith(f"live v{vid} audited precision 0.0% (n=20) below floor")


@pytest.mark.req("FR-16.1", "FR-16.2")
def test_evidence_is_recomputed_from_the_counts():
    """`evidence` stores nothing: every number comes from the version's audit counts over its life and in its current
    stage, so anyone can re-check a decision, and every value is finite JSON."""
    led, gate, live, cand = _gate(ReleasePolicy(sequential=1, promote_evidence=1))
    _audit(led, cand, 12, agree_every=6)  # 10 of 12 at 10%
    gate.tick()
    assert led.version(cand).state == "canary"
    _audit(led, live, 30, agree_every=10)  # 27 of 30 for the live version
    ev = gate.evidence(led.version(cand))
    assert ev == {
        "rule": "sequential",
        "promote_rule": "evidence",
        "alert_rule": "eprocess",
        "floor": 0.92,
        "n": 12,
        "s": 10,
        "e_rollback": stats.e_bernoulli(10, 12, 0.92, "below"),
        "n_stage": 12,
        "s_stage": 10,
        "e_promote": stats.e_bernoulli(10, 12, 0.92, "above"),
        "e_alert": stats.e_bernoulli(10, 12, 0.92, "below"),
        "thresholds": {"rollback": 20.0, "promote": 20.0, "alert": 100.0},
        "cs": list(stats.confidence_sequence(10, 12, CS_ALPHA)),
        "wilson": list(wilson(10, 12)),
    }
    lv = gate.evidence(led.version(live))
    assert (lv["n"], lv["s"], lv["n_stage"], lv["s_stage"]) == (30, 27, 30, 27)
    assert json.loads(json.dumps(lv, allow_nan=False)) == lv
    # A long history stays finite: the e-value is capped, and a vanishing one is still a number.
    big = Ledger(":memory:")
    vid = _version(big, "live", GOOD)
    _audit(big, vid, 3000, agree_every=1)
    ev = ReleaseGate(big, ReleasePolicy(alert_eprocess=0)).evidence(big.version(vid))
    assert ev["e_rollback"] == math.exp(stats.LOG_E_CAP) and 0.0 <= ev["e_promote"] < 1e-30 and ev["alert_rule"] == "wilson"
    assert json.dumps(ev, allow_nan=False)
    fresh = Ledger(":memory:")
    empty = ReleaseGate(fresh).evidence(fresh.version(_version(fresh, "canary", GOOD)))  # no audits yet
    assert (empty["n"], empty["e_rollback"], empty["cs"], empty["wilson"]) == (0, 1.0, [0.0, 1.0], [0.0, 1.0])


@pytest.mark.req("FR-16.1", "FR-6.5")
def test_canary_audit_rate_focuses_audits_on_the_canary():
    """RATCHET_CANARY_AUDIT_RATE audits a canary's answers at its own rate and live answers at the audit rate, with still
    exactly one random draw per served answer: the same seed sends the same requests to the canary with it and without
    it. The rate each request was drawn at is logged as `label_p`."""
    a = json.dumps({"category": "billing", "priority": "high"})

    class CountingRandom(random.Random):
        draws = 0

        def random(self) -> float:
            self.draws += 1
            return super().random()

    def run(canary_rate: float | None) -> tuple[list[tuple], int]:
        led = Ledger(":memory:")
        _version(led, "live", {"threshold": 0.5})
        _version(led, "canary", {"threshold": 0.5}, pct=50)
        s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, canary_audit_rate=canary_rate)
        provider = RecordingProvider(a)
        r = Ratchet(s, led, provider, lambda v: (provider, v.adapter_uri), seed=5)
        r.rng = CountingRandom(5)
        for i in range(200):
            assert r.handle([{"role": "user", "content": f"ticket {i}"}], "ticket-triage").served_by == "student"
        rows = led.db.execute("SELECT student_version, audited, label_p FROM requests ORDER BY id").fetchall()
        return [tuple(row) for row in rows], r.rng.draws

    focused, draws = run(1.0)
    plain, plain_draws = run(None)
    assert draws == plain_draws == 400  # the canary pick and the audit draw, per request
    assert [v for v, _, _ in focused] == [v for v, _, _ in plain]  # the same requests reached the canary
    by_version = Counter((v, audited, label_p) for v, audited, label_p in focused)
    assert set(by_version) == {(1, 0, 0.0), (2, 1, 1.0)} and min(by_version.values()) > 50
    assert {(v, audited, label_p) for v, audited, label_p in plain} == {(1, 0, 0.0), (2, 0, 0.0)}


@pytest.mark.req("FR-16.1")
def test_label_p_is_the_designed_labelling_probability():
    """`label_p` (FR-16.5's propensity): the audit rate a served answer was drawn at, 1.0 when the student answered and
    the request escalated (the teacher's answer labels it), NULL without a student answer (cold start)."""
    a = json.dumps({"category": "billing", "priority": "high"})
    led = Ledger(":memory:")
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.25)
    provider = RecordingProvider(a)
    r = Ratchet(s, led, provider, lambda v: (provider, v.adapter_uri), seed=1)
    msgs = [{"role": "user", "content": "ticket"}]
    assert r.handle(msgs, "ticket-triage").route == "cold_start"
    vid = _version(led, "live", {"threshold": 1.01})  # serves nothing
    assert r.handle(msgs, "ticket-triage").route == "confidence"
    led.update_eval(vid, {"threshold": 0.5})
    assert r.handle(msgs, "ticket-triage").route == "student"
    labels = [row[0] for row in led.db.execute("SELECT label_p FROM requests ORDER BY id")]
    assert labels == [None, 1.0, 0.25]


@pytest.mark.req("FR-16.1", "FR-16.2")
def test_stats_versions_carry_the_gates_evidence(gw_env, monkeypatch):
    """/ratchet/stats: the canary's and the live version's entries carry `evidence` (what the gate reads, from their
    counts), every other version's is null. Mock mode reads RATCHET_<FIELD> too, with min_audits 15 unless set."""
    monkeypatch.delenv("RATCHET_MIN_AUDITS", raising=False)
    gw = gw_env(RATCHET_SEQUENTIAL="1")
    assert (gw.rt.gate.policy.sequential, gw.rt.gate.policy.min_audits) == (1, 15)
    led = gw.rt.ledger
    old = _version(led, "retired", GOOD)
    live = _version(led, "live", GOOD)
    cand = _version(led, "canary", GOOD)
    _audit(led, cand, 4, agree_every=4)
    versions = {v["id"]: v for v in TestClient(gw.app).get("/ratchet/stats").json()["versions"]}
    assert versions[old]["evidence"] is None
    assert versions[live]["evidence"] == gw.rt.gate.evidence(led.version(live))
    ev = versions[cand]["evidence"]
    assert ev == gw.rt.gate.evidence(led.version(cand)) and (ev["rule"], ev["n"], ev["s"]) == ("sequential", 4, 3)
    gw = gw_env(RATCHET_SEQUENTIAL="0", RATCHET_MIN_AUDITS="30")
    assert (gw.rt.gate.policy.sequential, gw.rt.gate.policy.min_audits) == (0, 30)


# ---------- certified precision: the certified floor and the paired gate (FR-16.3, FR-6.30, FR-6.31, ADR 0026) ----------
from ratchet import certificate  # noqa: E402


def _cert(served: int, errors: int, target: float = 0.95, delta: float = 0.05, **override) -> dict:
    """A certificate as `cascade_eval` writes it, from its counts."""
    lcb = stats.cp_lower(served - errors, served, delta)
    cert = {
        "method": "clopper-pearson",
        "calibration": "point",
        "delta": delta,
        "target": target,
        "threshold": 0.5,
        "served": served,
        "errors": errors,
        "precision": (served - errors) / served,
        "lcb": lcb,
        "certified": lcb >= target,
    }
    return {**cert, **override}


@pytest.mark.req("FR-6.31", "FR-16.3")
def test_min_certified_precision_rejects_a_weak_certificate():
    weak = {**GOOD, "certificate": _cert(48, 2)}  # 46/48: lower bound 0.8746 (the reference run's v1, simulation)
    led = Ledger(":memory:")
    vid = _version(led, eval=weak)
    assert not ReleaseGate(led, ReleasePolicy(min_certified_precision=0.90)).admit(vid)
    assert led.events()[-1]["detail"] == f"v{vid}: certified lower bound 87.5% < 90% (46/48 served answers agree, delta 0.05)"
    for floor, admitted in ((0.85, True), (0.0, True)):
        led = Ledger(":memory:")
        vid = _version(led, eval=weak)
        assert ReleaseGate(led, ReleasePolicy(min_certified_precision=floor)).admit(vid) is admitted
    # The bound is recomputed from the counts, never read from the stored `lcb`; no certificate fails a floor.
    for ev, says in (
        ({**GOOD, "certificate": _cert(48, 2, lcb=0.99, certified=True)}, "certified lower bound 87.5% < 90%"),
        (GOOD, "no certificate to check against RATCHET_MIN_CERTIFIED_PRECISION=0.9"),
        ({**GOOD, "certificate": {**_cert(48, 2), "errors": 60}}, "no certificate"),
    ):
        led = Ledger(":memory:")
        vid = _version(led, eval=ev)
        assert not ReleaseGate(led, ReleasePolicy(min_certified_precision=0.9)).admit(vid)
        assert says in led.events()[-1]["detail"]
    led = Ledger(":memory:")
    vid = _version(led, eval={**GOOD, "precision": 0.90, "certificate": _cert(130, 1)})
    assert not ReleaseGate(led, ReleasePolicy(min_certified_precision=0.9)).admit(vid)  # the precision floor comes first
    assert "golden precision" in led.events()[-1]["detail"]


@pytest.mark.req("FR-16.3")
def test_certified_versions_say_so_in_their_gate_events():
    led = Ledger(":memory:")
    first = _version(led, eval={**GOOD, "certificate": _cert(130, 1)})  # 129/130: lower bound 0.964
    assert ReleaseGate(led).admit(first)
    assert led.events()[-1]["detail"] == (
        f"v{first} first student: golden precision 98.0%, coverage 60.0%; canary 10%; certified >= 95% (lcb 96.4%, delta 0.05)"
    )
    led.set_version(first, "live", 100)
    second = _version(led, eval={**GOOD, "certificate": _cert(48, 2)})  # not certified: nothing appended
    assert ReleaseGate(led).admit(second)
    assert led.events()[-1]["detail"].endswith(f"shadowing live v{first}")
    third = _version(led, eval={**GOOD, "certificate": _cert(80, 0, target=0.965, delta=0.1)})  # 0.1^(1/80) = 0.9716
    assert ReleaseGate(led).admit(third)
    assert led.events()[-1]["detail"].endswith("; certified >= 96.5% (lcb 97.2%, delta 0.1)")
    fourth = _version(led, eval={**GOOD, "certificate": _cert(48, 2, certified=True)})  # a claim the counts do not back
    assert ReleaseGate(led).admit(fourth)
    assert "certified" not in led.events()[-1]["detail"]


def _paired_case(live_vec: str, cand_vec: str, policy: ReleasePolicy, *, against: int | None = None, **pair_edit):
    """A live v1 and a candidate whose evals have the coverage of their vectors (every served answer right unless the
    vector says W), the candidate's carrying the paired comparison; returns (admitted, the last event's detail)."""

    def ev(vec: str) -> dict:
        served = len(vec) - vec.count("E")
        return {"precision": 1.0 if not served else (served - vec.count("W")) / served, "coverage": served / len(vec)}

    led = Ledger(":memory:")
    live = _version(led, "live", ev(live_vec))
    pair = {**certificate.paired(live_vec, cand_vec), "against": live if against is None else against, **pair_edit}
    vid = _version(led, eval={**ev(cand_vec), "paired": pair})
    admitted = ReleaseGate(led, policy).admit(vid)
    return admitted, led.events()[-1]["detail"]


@pytest.mark.req("FR-6.30", "FR-16.3")
def test_paired_gate_rejects_only_large_significant_regressions():
    paired = ReleasePolicy(paired_gate=1, min_precision=0.9)  # a precision floor the cases below pass
    # Coverage within noise: 12 rows only live serves, 10 only the candidate, of 63 (2 rows, 3.2% > the 2-point
    # tolerance; McNemar p = 0.42). The unpaired rule rejects it (coverage 81.0% vs 84.1%); the paired gate admits it.
    live_vec, cand_vec = "S" * 12 + "E" * 10 + "S" * 41, "E" * 12 + "S" * 10 + "S" * 41
    ok, why = _paired_case(live_vec, cand_vec, ReleasePolicy())
    assert not ok and "regresses vs live v1" in why
    ok, why = _paired_case(live_vec, cand_vec, paired)
    assert ok and why.endswith("serves -2 of 63 vs live v1 (McNemar p=0.42); 0 negative flips, 0 positive flips")
    # Data that is not a comparison with this live version, or not readable, falls back to the unpaired rule.
    for kwargs in ({"against": 99}, {"cov_only_live": "12"}, {"cov_only_live": 64}):
        ok, why = _paired_case(live_vec, cand_vec, paired, **kwargs)
        assert not ok and "regresses vs live v1" in why

    # A large, significant coverage loss: 10 rows only live serves, none only the candidate (p = 0.001).
    ok, why = _paired_case("S" * 60, "E" * 10 + "S" * 50, paired)
    assert not ok and why == (
        "v2: serves 10 fewer of 60 golden rows than live v1 (10 only live, 0 only the candidate; McNemar p=0.000977 < 0.05)"
    )
    # More wrong answers: 8 rows newly wrong (5 that live served right, 3 it escalated), none fixed, of 100 (p = 0.004).
    ok, why = _paired_case("S" * 92 + "E" * 8, "W" * 5 + "S" * 87 + "W" * 3 + "E" * 5, paired)
    assert (
        not ok and why == "v2: 8 negative flips vs 0 positive flips on 100 golden rows against live v1 (McNemar p=0.00391 < 0.05)"
    )
    # The same 8 wrong answers on 1,000 rows are within the tolerance (0.8%), so only the flip-rate bound can catch them.
    ok, _ = _paired_case("S" * 992 + "E" * 8, "W" * 5 + "S" * 987 + "W" * 3 + "E" * 5, paired)
    assert ok

    # Negative flips: 6 of the 60 answers live served right are now wrong, offset by 5 rows live got wrong that the
    # candidate gets right, so the error count is within noise (p = 0.5); the flip rate's 95% lower bound is 4.7%.
    live_vec, cand_vec = "S" * 60 + "W" * 5, "W" * 6 + "S" * 54 + "S" * 5
    ok, why = _paired_case(live_vec, cand_vec, ReleasePolicy(paired_gate=1, min_precision=0.9, max_nfr=0.02))
    assert (
        not ok and why == "v2: negative flip rate 10.0% (95% lower bound 4.7% > 2.0%): 6 of the 60 answers live v1 served right"
    )
    ok, why = _paired_case(live_vec, cand_vec, paired)  # max_nfr 1: off
    assert ok and why.endswith("serves +0 of 65 vs live v1 (McNemar p=1.00); 6 negative flips, 5 positive flips")
    ok, _ = _paired_case(live_vec, cand_vec, ReleasePolicy(paired_gate=1, min_precision=0.9, max_nfr=0.05))
    assert ok  # 4.7% <= 5%
    ok, _ = _paired_case(live_vec, cand_vec, ReleasePolicy(paired_gate=1, min_precision=0.9, regression_alpha=0.6, tolerance=0.0))
    assert not ok  # a looser alpha and no tolerance: 1 more wrong answer than live, p = 0.5 < 0.6


# ---------- the judge gate on free-text tasks (FR-14.40, ADR 0054) ----------
def _similarity_task(name: str) -> tasks.Task:
    """Register a free-text task compared by chrF, as a tasks file would."""
    task = tasks.Task(name, format="text", fields=(FieldSpec("$", "similarity"),), policy={"samples": 3}, source="file")
    tasks.register(task)
    return task


def _judged_record(name: str, test_n: int, judge_n: int | None, judge_pass_rate: float | None) -> dict[str, Any]:
    """A per-task record serving every one of its `test_n` held-out rows at 100% precision, with the judge's numbers."""
    rec = {
        "golden_n": 2 * test_n,
        "test_n": test_n,
        "agreement": 1.0,
        "threshold": 0.8,
        "precision": 1.0,
        "coverage": 1.0,
        "spec_hash": tasks.get(name).spec_hash,
        "target_precision": 0.97,
        "calibrated": True,
        "precision_kind": tasks.get(name).precision_kind,
    }
    if judge_n is not None:
        rec |= {"judge_n": judge_n, "judge_pass_rate": judge_pass_rate}
    return rec


@pytest.mark.req("FR-14.40", "FR-6.1")
def test_gate_rejects_below_min_judge_pass(monkeypatch):
    judged = {**GOOD, "judge_n": 20, "judge_pass_rate": 0.7}

    def admit(ev: dict[str, Any], policy: ReleasePolicy) -> tuple[bool, Ledger, int]:
        led = Ledger(":memory:")
        v = _version(led, "candidate", ev)
        return ReleaseGate(led, policy).admit(v), led, v

    assert admit(judged, ReleasePolicy())[0]  # off by default: the judge's verdict is reported, not enforced
    ok, led, v = admit(judged, ReleasePolicy(min_judge_pass=0.8))
    assert not ok and led.version(v).state == "rejected"
    assert _kinds(led, "rejected")[0]["detail"] == f"v{v}: golden judge pass rate 70.0% on 20 answers < 80%"
    # At the floor passes, as the other gates do; with nothing served to judge there is nothing to fail.
    assert admit({**GOOD, "judge_n": 20, "judge_pass_rate": 0.8}, ReleasePolicy(min_judge_pass=0.8))[0]
    assert admit({**GOOD, "judge_n": 0, "judge_pass_rate": None}, ReleasePolicy(min_judge_pass=0.8))[0]
    # The golden gate runs first: a candidate below the precision floor is rejected for that.
    ok, led, v = admit({**judged, "precision": 0.5}, ReleasePolicy(min_judge_pass=0.8))
    assert not ok and "golden precision" in _kinds(led, "rejected")[0]["detail"]
    # A free-text task the candidate would answer that no judge graded: the gate cannot be met, so it is refused.
    _similarity_task("notes")
    unjudged = {**GOOD, "spec_hashes": {"notes": "x", "ticket-triage": "y"}}
    ok, led, v = admit(unjudged, ReleasePolicy(min_judge_pass=0.8))
    assert not ok
    assert _kinds(led, "rejected")[0]["detail"] == (
        f"v{v}: free-text tasks notes were not judged; RATCHET_MIN_JUDGE_PASS needs a judge"
    )
    records = {"notes": _judged_record("notes", 20, None, None), "ticket-triage": _judged_record("ticket-triage", 20, None, None)}
    assert not admit({**GOOD, "tasks": records}, ReleasePolicy(min_judge_pass=0.8))[0]
    # Labels need no judge, and neither does a free-text task the candidate serves nothing of.
    assert admit({**GOOD, "spec_hashes": {"ticket-triage": "y"}}, ReleasePolicy(min_judge_pass=0.8))[0]
    off = {**records, "notes": {**records["notes"], "threshold": SERVE_NOTHING}}
    assert admit({**GOOD, "tasks": off}, ReleasePolicy(min_judge_pass=0.8))[0]
    # RATCHET_MIN_JUDGE_PASS is read like every policy field, and checked.
    monkeypatch.setenv("RATCHET_MIN_JUDGE_PASS", "0.85")
    assert ReleasePolicy.from_env().min_judge_pass == 0.85
    for bad in (1.5, -0.1, float("nan")):
        with pytest.raises(ValueError, match="RATCHET_MIN_JUDGE_PASS"):
            ReleasePolicy(min_judge_pass=bad)


def _version_obj(ev: dict[str, Any]):
    led = Ledger(":memory:")
    return led.version(_version(led, "candidate", ev))


@pytest.mark.req("FR-14.40", "FR-14.1")
def test_judge_gate_disables_task():
    """Two free-text tasks and a label task on one adapter. The overall judge pass rate (36 of 40) clears 85%, but one
    task's own (6 of 10) does not: that task is disabled on the candidate, which ships for the others (`task_mode`
    'disable'), or the candidate is rejected ('reject'). A task with fewer held-out rows than `min_task_test_n` is left
    to the overall rate, as the per-task precision check leaves it."""
    _similarity_task("good-notes"), _similarity_task("bad-notes")
    records = {
        "good-notes": _judged_record("good-notes", 30, 30, 1.0),
        "bad-notes": _judged_record("bad-notes", 10, 10, 0.6),
        "ticket-triage": _judged_record("ticket-triage", 40, None, None),
    }
    ev = {**GOOD, "judge_n": 40, "judge_pass_rate": 0.9, "tasks": records}
    led = Ledger(":memory:")
    cand = _version(led, "candidate", ev)
    gate = ReleaseGate(led, ReleasePolicy(min_judge_pass=0.85))
    why = "golden judge pass rate 60.0% on 10 answers < 85%"
    assert gate.judge_problem(led.version(cand)) is None and gate.judge_failures(led.version(cand)) == [("bad-notes", why)]
    assert gate.admit(cand) and led.version(cand).state == "canary"
    tasks_now = led.version(cand).eval["tasks"]
    assert tasks_now["bad-notes"]["threshold"] == SERVE_NOTHING and tasks_now["bad-notes"]["disabled"] == why
    assert "disabled" not in tasks_now["good-notes"] and "disabled" not in tasks_now["ticket-triage"]
    assert _kinds(led, "task_disabled")[0]["detail"] == (
        f"v{cand} bad-notes: {why}; the teacher answers it, v{cand} ships for its other tasks"
    )
    r = Ratchet(Settings(teacher_model=OPEN_TEACHER), led, RecordingProvider("x"), lambda v: (RecordingProvider("x"), "a"))
    assert r.threshold(led.version(cand), "a", "bad-notes") == SERVE_NOTHING
    # reject mode: the candidate goes, naming the task.
    led = Ledger(":memory:")
    cand = _version(led, "candidate", ev)
    assert not ReleaseGate(led, ReleasePolicy(min_judge_pass=0.85, task_mode="reject")).admit(cand)
    assert _kinds(led, "rejected")[0]["detail"] == f"v{cand}: task bad-notes: {why}"
    # Too few held-out rows for a check of its own; a task the judge did not grade fails when it has enough.
    few = {**records, "bad-notes": _judged_record("bad-notes", 9, 9, 0.6)}
    assert ReleaseGate(led, ReleasePolicy(min_judge_pass=0.85)).judge_failures(_version_obj({**ev, "tasks": few})) == []
    missing = {**records, "bad-notes": _judged_record("bad-notes", 10, None, None)}
    assert ReleaseGate(led, ReleasePolicy(min_judge_pass=0.85)).judge_failures(_version_obj({**ev, "tasks": missing})) == [
        ("bad-notes", "golden: its answers were not judged; RATCHET_MIN_JUDGE_PASS needs a judge")
    ]
    # Off (the default): nothing is disabled for the judge.
    assert ReleaseGate(led).judge_failures(_version_obj(ev)) == [] and ReleaseGate(led).judge_problem(_version_obj(ev)) is None


@pytest.mark.req("FR-14.40", "FR-5.5", "FR-9.3")
def test_mock_judge_catches_what_the_proxy_misses():
    """The gate path, offline. A free-text task compared by chrF at 0.7: the mock student's wrong answers keep the
    teacher's opening and change its closing sentence, which chrF still calls close (about 0.71 to 0.75). A candidate
    that learned nothing therefore shows 100% proxy precision on the golden set, and only the judge, which in the mock
    world knows the stored truth, sees that most of its answers are wrong. With RATCHET_MIN_JUDGE_PASS it is rejected;
    a candidate trained on the task passes. Simulation: mock models."""
    _similarity_task("note-summary")
    teacher = MockTeacher(0)
    model = "nvidia/Nemotron-3-Ultra-550b-a55b"
    lic = licenses.require_distillable(model)

    def ledger() -> Ledger:
        led = Ledger(":memory:", golden_fraction=0.6)
        for i in range(120):
            note = f"Summarise this note for the team: delivery {i} to the Pune office is late by {i % 5 + 1} days."
            msgs = [{"role": "user", "content": note}]
            led.add_example("note-summary", msgs, teacher.complete(msgs, model)[0].text, model, lic.name, lic.url)
        return led

    fleet = MockFleet()

    class Forgetful:  # a training job that produced an adapter which learned nothing
        def train(self, rows: list[dict[str, Any]], base_model: str, name: str) -> str:
            return fleet.train([], base_model, name)

    policy = ReleasePolicy(min_judge_pass=0.9)
    led = ledger()
    rep = run_night(led, Forgetful(), fleet, ReleaseGate(led, policy), "base", min_new_examples=1)
    rec = rep.eval["tasks"]["note-summary"]
    assert rec["precision_kind"] == "proxy" == rep.eval["precision_kind"]
    assert rec["precision"] == 1.0 and rec["coverage"] > 0.9  # the proxy sees nothing wrong
    assert rec["judge_n"] == round(rec["coverage"] * rec["test_n"]) and rec["judge_pass_rate"] < 0.6  # the judge does
    assert not rep.admitted and "golden judge pass rate" in _kinds(led, "rejected")[0]["detail"]
    # Without the judge gate the same candidate would ship: what RATCHET_MIN_JUDGE_PASS is for.
    led = ledger()
    assert run_night(led, Forgetful(), fleet, ReleaseGate(led), "base", min_new_examples=1).admitted
    # A candidate trained on the task's examples passes the judge gate.
    led = ledger()
    rep = run_night(led, fleet, fleet, ReleaseGate(led, policy), "base", min_new_examples=1)
    assert rep.admitted and rep.eval["judge_pass_rate"] >= 0.9
    # Every answer the judge passes agrees with the teacher by the proxy too: the judge only ever lowers the estimate.
    assert rep.eval["judge_pass_rate"] <= rep.eval["tasks"]["note-summary"]["precision"]


# The modules a chat request runs through (`gateway.chat` -> `Ratchet.handle`): scoring, comparing, thresholds, providers.
REQUEST_PATH = ("router.py", "confidence.py", "compare.py", "similarity.py", "tasks.py", "calibration.py", "providers.py")


@pytest.mark.req("FR-14.40")
def test_judge_never_on_the_request_path():
    """The judge grades free text at night, in the golden eval (ADR 0008, ADR 0054): no module a chat request runs
    through names a judge in its code (comments and docstrings aside) or imports the golden eval, so a similarity task
    costs no judge call per request."""
    root = Path(__file__).resolve().parent.parent / "ratchet"
    found = []
    for name in REQUEST_PATH:
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            word = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if word is not None and "judge" in word.lower():
                found.append(f"{name}:{node.lineno} {word}")
            if isinstance(node, ast.ImportFrom) and node.module and "evaluate" in node.module:
                found.append(f"{name}:{node.lineno} imports {node.module}")
            if isinstance(node, (ast.Import, ast.ImportFrom)) and any("evaluate" in a.name for a in node.names):
                found.append(f"{name}:{node.lineno} imports {[a.name for a in node.names]}")
    assert found == []
