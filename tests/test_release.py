"""The release gate's rules (golden gate, one candidate in flight, shadow, canary, rollback) and the router's side of a
release: shadow arbitration and the canary traffic split."""

from __future__ import annotations

import json
from collections import Counter

import pytest
from fakes import OPEN_TEACHER, RecordingProvider, _audit, _shadow, _version

from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.release import CANARY_STEPS, ReleaseGate, ReleasePolicy, wilson
from ratchet.router import Ratchet

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
