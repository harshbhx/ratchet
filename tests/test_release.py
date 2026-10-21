"""The release gate's rules (golden gate, the judge gate on free-text tasks, one candidate in flight, shadow, canary,
rollback) and the router's side of a release: shadow arbitration and the canary traffic split."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fakes import OPEN_TEACHER, RecordingProvider, _audit, _shadow, _version

from ratchet import licenses, tasks
from ratchet.compare import FieldSpec
from ratchet.confidence import SERVE_NOTHING
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import CANARY_STEPS, ReleaseGate, ReleasePolicy, wilson
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
