"""Fallback attribution (FR-12.32, FR-8.3, NFR-2, NFR-8; ADR 0073).

Without RATCHET_STUDENT_BASE the live gateway sends every version's student calls to the hosted, untuned fallback model
(RATCHET_FALLBACK_STUDENT). Those answers are served, but they are not the version's: the response names no student
version, the ledger records the model (`requests.student_model`, `shadow_model`), the release gate's queries count only
answers the version's own adapter wrote, and a shadow that resolves to the fallback does not run. Here:

- the release gate: a canary holds on the fallback's audited disagreements (and the same answers from its adapter roll
  it back), the live alert stays quiet, a shadow under the fallback never samples and holds;
- the response through the live gateway: `ratchet.student_version` null, `model` the configured fallback id, a
  fingerprint without a version, and the row that still names the version it was routed to;
- the ledger: legacy rows (NULL model) still count, the incremental tally and its pending audits follow the same rule,
  `days[].fallback_served`, and a v0.4 file gains the columns;
- off the request path: pending rows carry their model, a failed shadow clears it with its verdict, and a shadow under
  the fallback takes no sidecar place.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import OPEN_TEACHER, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet.config import Settings
from ratchet.ledger import OWN_STUDENT, Ledger
from ratchet.providers import Completion
from ratchet.release import CANARY_STEPS, ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet, RequestMeta, fingerprint
from ratchet.sidecar import SideCar

A = json.dumps({"category": "billing", "priority": "high"})  # the teacher's label
B = json.dumps({"category": "refund", "priority": "low"})  # a confident, wrong student
TRIAGE = "ticket-triage"
CHAT = "/v1/chat/completions"
FALLBACK = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
GOOD = {"precision": 0.98, "coverage": 0.60, "threshold": 0.5}
META = RequestMeta(request_id="req_" + "0" * 32)
WAIT_S = 10.0
NOT_COUNTED = "not counted toward its release"  # the end of an `unattributed` event for an answer
WAITS = "the shadow waits"  # and for a shadow that did not run


def _settings(**overrides: Any) -> Settings:
    """Every value these tests depend on, set explicitly, so RATCHET_* variables in the shell cannot move a result."""
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "fallback_student_model": FALLBACK,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "teacher_reasoning_tokens": 0,
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
    }
    return Settings(**{**base, **overrides})


def _ask(tag: object) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice ({tag})"}]


def _hosted(provider: RecordingProvider):
    """The live gateway's resolver without a student endpoint: every version on the hosted fallback model."""
    return lambda v: (provider, FALLBACK)


def _rows(led: Ledger, where: str = "1", args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute(f"SELECT * FROM requests WHERE {where} ORDER BY id", args)]


def _events(led: Ledger, kind: str) -> list[str]:
    return [e["detail"] for e in led.events() if e["kind"] == kind]


def _gate_events(led: Ledger) -> list[str]:
    return [e["kind"] for e in led.events() if e["kind"] in ("canary", "live", "live_retired", "rollback", "alert", "rejected")]


# ---------- the release gate ----------
@pytest.mark.req("FR-12.32", "FR-8.3", "NFR-8")
def test_fallback_answers_do_not_count_toward_canary():
    """Live v1 and canary v2 at 10%, both resolved to the hosted fallback, which is confidently wrong on every request:
    40 audited disagreements land on v2 (and hundreds on v1), and the gate holds both. Neither version has an audit
    to its name, no rollback, promotion or alert is written, and the answers are still served, audited and visible in
    the day's stats. The same 20 disagreements from v2's own adapter roll it back, so the filter is what held it."""
    led = Ledger(":memory:")
    live = _version(led, "live", GOOD)
    canary = _version(led, "canary", GOOD)
    hosted, teacher = RecordingProvider(B), RecordingProvider(A)
    s = _settings(audit_rate=1.0)
    r = Ratchet(s, led, teacher, _hosted(hosted), seed=3)
    gate = ReleaseGate(led, ReleasePolicy(min_audits=20))
    results = []
    for i in range(2_000):  # about 400 requests at 10%
        results.append(r.handle(_ask(i), TRIAGE))
        gate.tick()
        if len(_rows(led, "student_version=? AND audited=1", (canary,))) == 40:
            break

    assert (led.version(canary).state, led.version(canary).traffic_pct) == ("canary", CANARY_STEPS[0])
    assert led.version(live).state == "live" and _gate_events(led) == []
    assert len(_rows(led, "student_version=? AND audited=1", (canary,))) == 40
    assert led.version_live_quality(canary) == (0, None) == led.version_live_quality(live)
    assert led.version_live_quality(canary, current_stage_only=False) == (0, None)
    # Served by the fallback, routed to a version, audited: recorded as such, and every one a disagreement.
    rows = _rows(led)
    assert {(x["served_by"], x["route"], x["student_model"], x["audited"], x["audit_agree"]) for x in rows} == {
        ("student", "student", FALLBACK, 1, 0)
    }
    assert {x["student_version"] for x in rows} == {live, canary}
    # The response names the model, not a version, and one fingerprint, whichever version the request was routed to.
    assert {(x.served_by, x.version, x.model) for x in results} == {("student", None, FALLBACK)}
    assert {x.fingerprint for x in results} == {fingerprint("student", FALLBACK, s.confidence_threshold)}
    (day,) = led.stats_by_day()
    assert (day["fallback_served"], day["student_share"], day["audit_agreement"]) == (len(results), 1.0, 0.0)
    # One throttled event per version (1st, 10th, 100th ...), naming the model, never request text.
    details, expected = _events(led, "unattributed"), 0
    for vid, state in ((live, "live"), (canary, "canary")):
        text = f"v{vid} ({state}): answered by the hosted fallback student {FALLBACK}, not its adapter; " + NOT_COUNTED
        answered = sum(1 for x in rows if x["student_version"] == vid)
        want = sum(1 for n in (1, 10, 100, 1000) if n <= answered)
        assert details.count(text) == want > 0
        expected += want
    assert len(details) == expected

    # Its own adapter, confidently wrong in the same way: 20 audits in this stage and the canary is rolled back.
    adapter = Ratchet(s, led, teacher, lambda v: (hosted, v.adapter_uri), seed=3)
    for i in range(2_000):
        adapter.handle(_ask(f"own {i}"), TRIAGE)
        gate.tick()
        if led.version(canary).state != "canary":
            break
    # (The live version's own wrong answers raise its alert meanwhile; the fallback's never did.)
    assert led.version(canary).state == "rolled_back" and _gate_events(led).count("rollback") == 1
    assert led.version_live_quality(canary, current_stage_only=False) == (20, 0.0)
    assert len(_rows(led, "student_version=? AND student_model=?", (canary, f"student-v{canary}"))) == 20


@pytest.mark.req("FR-12.32", "FR-8.3", "NFR-8")
def test_fallback_audits_never_alert_on_the_live_version():
    """The live alert reads the same counts: a live version under the fallback gets no `alert` however wrong the
    fallback is, while the same answers from its adapter raise one."""
    led = Ledger(":memory:")
    live = _version(led, "live", GOOD)
    teacher = RecordingProvider(A)
    gate = ReleaseGate(led, ReleasePolicy(min_audits=20))
    r = Ratchet(_settings(audit_rate=1.0), led, teacher, _hosted(RecordingProvider(B)), seed=1)
    for i in range(60):
        r.handle(_ask(i), TRIAGE)
        gate.tick()
    assert _gate_events(led) == [] and led.version_live_quality(live) == (0, None)
    own = Ratchet(_settings(audit_rate=1.0), led, teacher, lambda v: (RecordingProvider(B), v.adapter_uri), seed=1)
    for i in range(20):
        own.handle(_ask(f"own {i}"), TRIAGE)
    gate.tick()
    assert _gate_events(led) == ["alert"] and led.version_live_quality(live) == (20, 0.0)


@pytest.mark.req("FR-12.32", "FR-8.3", "FR-6.3", "NFR-2")
def test_shadow_skipped_under_fallback():
    """A shadow that resolves to the hosted fallback never samples: its verdicts would compare the fallback with itself.
    The live student (the fallback too) still answers every request, no row carries a shadow verdict, the shadow holds,
    and a throttled `unattributed` event says why. With its adapter the shadow runs and counts again."""
    led = Ledger(":memory:")
    _version(led, "live", GOOD)
    shadow = _version(led, "shadow", GOOD)
    hosted, teacher = RecordingProvider(A), RecordingProvider(A)
    r = Ratchet(_settings(), led, teacher, _hosted(hosted), seed=1)
    gate = ReleaseGate(led, ReleasePolicy(min_shadow=5))
    for i in range(12):
        assert r.handle(_ask(i), TRIAGE).served_by == "student"
        gate.tick()
    assert len(hosted.calls) == 12 and teacher.calls == []  # one call per request: the live student's
    assert {(x["shadow_version"], x["shadow_serve"], x["shadow_model"]) for x in _rows(led)} == {(None, None, None)}
    assert led.shadow_quality(shadow)["n"] == 0 and led.version(shadow).state == "shadow" and _gate_events(led) == []
    detail = f"v{shadow} shadow: not run, it resolves to the hosted fallback student {FALLBACK}, not its adapter; " + WAITS
    assert [d for d in _events(led, "unattributed") if "shadow" in d] == [detail, detail]  # the 1st and the 10th

    # Back on the endpoint, the shadow runs on its adapter and its verdicts count.
    student = RecordingProvider(A)
    own = Ratchet(_settings(), led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    for i in range(5):
        own.handle(_ask(f"own {i}"), TRIAGE)
    gate.tick()
    on_adapter = _rows(led, "shadow_version=?", (shadow,))
    assert len(on_adapter) == 5 and {x["shadow_model"] for x in on_adapter} == {f"student-v{shadow}"}
    assert led.version(shadow).state == "canary"  # 5 would-serve answers that agree: promoted


@pytest.mark.req("FR-12.32", "INV-15", "NFR-2")
def test_a_shadow_resolver_that_raises_is_a_shadow_failure():
    """The shadow is resolved on the request path now (before a sidecar place is taken), so a resolver that raises for
    the candidate is a failed shadow sample, counted and recorded, and never fails the request (INV-15)."""
    led = Ledger(":memory:")
    _version(led, "live", GOOD)
    shadow = _version(led, "shadow", GOOD)
    student = RecordingProvider(A)

    def resolve(v):
        if v.id == shadow:
            raise KeyError("no such adapter")
        return student, v.adapter_uri

    r = Ratchet(_settings(), led, RecordingProvider(A), resolve, seed=1)
    out = r.handle(_ask(1), TRIAGE, meta=META)
    assert (out.served_by, out.version) == ("student", 1)
    assert r.health()["shadow_errors"] == 1 and _events(led, "shadow_error") == [f"v{shadow} sampling: KeyError"]
    assert [f["stage"] for f in led.failures_for(META.request_id)] == ["shadow"]
    assert _rows(led)[0]["shadow_version"] is None


@pytest.mark.req("FR-12.32", "FR-8.3", "FR-2.12")
def test_degraded_fallback_answer_names_no_version():
    """A degraded answer (the teacher unavailable, RATCHET_ON_TEACHER_FAILURE=serve_student) from the fallback is not the
    version's either: no student version, the fallback's fingerprint, and a row that records the model."""
    led = Ledger(":memory:")
    _version(led, "live", {**GOOD, "threshold": 0.99})
    s = _settings(on_teacher_failure="serve_student", confidence_threshold=0.99, teacher_breaker_failures=0)
    r = Ratchet(s, led, RecordingProvider(A, raise_on={OPEN_TEACHER}), _hosted(RecordingProvider(A)), seed=1)
    out = r.handle(_ask(1), TRIAGE)
    assert (out.route, out.served_by, out.version, out.model) == ("degraded", "student", None, FALLBACK)
    assert out.fingerprint == fingerprint("student", FALLBACK, 0.99)
    (row,) = _rows(led)
    assert (row["student_version"], row["student_model"], row["audited"]) == (1, FALLBACK, 0)
    assert led.stats_by_day()[0]["fallback_served"] == 1


# ---------- the response through the live gateway ----------
def _live_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "tf-teacher-api-key",
        "teacher_model": OPEN_TEACHER,
        "judge_model": "nvidia/nemotron-3-super-120b-a12b",
        "fallback_student_model": FALLBACK,
        "student_base": "",
        "student_api_key": "",
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.9,
        "redact_pii": True,
        "tavily_api_key": "",
        "api_keys": "",
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,
    }
    return Settings(**{**base, **overrides})


@pytest.mark.req("FR-12.32", "FR-8.3", "NFR-2")
def test_fallback_response_reports_no_student_version(gw_env, tmp_path, monkeypatch):
    """The live gateway without RATCHET_STUDENT_BASE: a confident fallback answer is served (`served_by` student) with
    `ratchet.student_version` null, `model` the configured fallback id (even when the server echoes another name) and
    a fingerprint without a version. Its ledger row still names the version it was routed to and records the model.
    With a student endpoint, the same request is the version's answer."""
    gw = gw_env()
    hosted = FakeOpenAIServer("hosted nano says hi")
    echo = FakeOpenAIServer.completion
    hosted.completion = lambda body: {**echo(hosted, body), "model": "token-factory/" + body["model"]}  # type: ignore[method-assign]
    rt = gw.Runtime(_live_settings(tmp_path), transports={"teacher": hosted.transport})
    monkeypatch.setattr(gw, "rt", rt)
    vid = _version(rt.ledger, "live", {"threshold": 0.2})
    c = TestClient(gw.app)

    r = c.post(CHAT, json={"messages": _ask(1)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["ratchet"]["served_by"], body["ratchet"]["student_version"], body["model"]) == ("student", None, FALLBACK)
    assert body["system_fingerprint"] == fingerprint("student", FALLBACK, 0.9) and r.headers["x-ratchet-served-by"] == "student"
    assert [x.json["model"] for x in hosted.calls] == [FALLBACK]
    row = c.get(f"/ratchet/requests/{body['ratchet']['request_id']}").json()["request"]
    assert (row["student_version"], row["student_model"], row["route"], row["shadow_model"]) == (vid, FALLBACK, "student", None)
    days = c.get("/ratchet/stats").json()["days"]
    assert days[-1]["fallback_served"] == 1
    assert _events(rt.ledger, "unattributed") == [
        f"v{vid} (live): answered by the hosted fallback student {FALLBACK}, not its adapter; " + NOT_COUNTED
    ]

    # With an endpoint the adapter answers, and the answer is the version's.
    endpoint = FakeOpenAIServer("adapter says hi")
    own = gw.Runtime(
        _live_settings(tmp_path, db_path=str(tmp_path / "own.db"), student_base="https://student.test/v1", student_api_key="k"),
        transports={"teacher": hosted.transport, "student": endpoint.transport},
    )
    monkeypatch.setattr(gw, "rt", own)
    vid = _version(own.ledger, "live", {"threshold": 0.2})
    body = c.post(CHAT, json={"messages": _ask(2)}).json()
    assert (body["ratchet"]["student_version"], body["model"]) == (vid, f"student-v{vid}")
    v = own.ledger.version(vid)
    assert body["system_fingerprint"] == fingerprint("student", v.adapter_uri, vid, v.dataset_hash, 0.2)
    row = c.get(f"/ratchet/requests/{body['ratchet']['request_id']}").json()["request"]
    assert row["student_model"] == f"student-v{vid}" and c.get("/ratchet/stats").json()["days"][-1]["fallback_served"] == 0


# ---------- the ledger ----------
def _log(led: Ledger, **row: Any) -> int:
    base: dict[str, Any] = {"task": "t", "served_by": "student", "cost_usd": 0.0, "baseline_cost_usd": 0.0, "latency_ms": 1.0}
    return led.log_request(**{**base, **row})


def _full(led: Ledger, vid: int, since: int = 0) -> tuple[int, float | None]:
    """version_live_quality as one aggregate: audits with a verdict of answers the version's adapter wrote (or an older
    row without a model)."""
    r = led.db.execute(
        "SELECT COUNT(audit_agree), AVG(audit_agree) FROM requests"
        f" WHERE student_version=?1 AND audited=1 AND served_by='student' AND id>?2 AND {OWN_STUDENT}",
        (vid, since),
    ).fetchone()
    return int(r[0]), (None if r[1] is None else float(r[1]))


@pytest.mark.req("FR-12.32", "NFR-8", "FR-12.8")
def test_release_queries_count_only_the_versions_own_answers(tmp_path):
    """version_live_quality and shadow_quality count rows the version's adapter answered and rows without a model
    (older rows); never another model's, also among pending off-path audits whose verdict arrives later, and also for
    a fresh tally (another process). The incremental tally equals one aggregate at every step."""
    led = Ledger(str(tmp_path / "q.db"))
    vid = _version(led, "canary", GOOD)
    uri = led.version(vid).adapter_uri
    _log(led, student_version=vid, audited=1, audit_agree=1)  # an older row: no model recorded, counted
    _log(led, student_version=vid, audited=1, audit_agree=0, student_model=uri)
    _log(led, student_version=vid, audited=1, audit_agree=0, student_model=FALLBACK)  # not the version's
    assert led.version_live_quality(vid) == (2, 0.5) == _full(led, vid)
    pending_own = _log(led, student_version=vid, audited=1, student_model=uri)
    pending_other = _log(led, student_version=vid, audited=1, student_model=FALLBACK)
    assert led.version_live_quality(vid) == (2, 0.5)
    assert set(led._tallies[(vid, led.version(vid).stage_since)].pending) == {pending_own}  # the fallback's never waits
    led.update_request(pending_other, audited=1, audit_agree=1)
    led.update_request(pending_own, audited=1, audit_agree=1)
    assert led.version_live_quality(vid) == (3, 2 / 3) == _full(led, vid)
    assert Ledger(str(tmp_path / "q.db")).version_live_quality(vid) == (3, 2 / 3)  # a fresh tally agrees

    shadow = _version(led, "shadow", GOOD)
    s_uri = led.version(shadow).adapter_uri
    for model, agree in ((None, 1), (s_uri, 0), (FALLBACK, 1), (FALLBACK, 1)):
        _log(led, shadow_version=shadow, shadow_serve=1, shadow_agree=agree, shadow_model=model)
    q = led.shadow_quality(shadow)
    assert (q["n"], q["served"], q["precision"]) == (2, 2, 0.5)
    assert led.shadow_quality(999)["n"] == 0  # an unknown version: no adapter, nothing but rows without a model


@pytest.mark.req("FR-12.32", "NFR-6")
def test_fallback_served_counts_student_answers_another_model_wrote():
    """`days[].fallback_served`: student-served rows whose model is not their version's adapter; never a teacher answer,
    an escalation the fallback was unsure about, an older row without a model, or the version's own answer."""
    led = Ledger(":memory:")
    vid = _version(led, "live", GOOD)
    uri = led.version(vid).adapter_uri
    _log(led, day=1, student_version=vid, student_model=FALLBACK)
    _log(led, day=1, student_version=vid, student_model=FALLBACK, route="degraded")
    _log(led, day=1, student_version=vid, student_model=uri)
    _log(led, day=1, student_version=vid)
    _log(led, day=1, served_by="teacher", student_version=vid, student_model=FALLBACK, escalated=1)
    _log(led, day=2, student_version=vid, student_model=uri)
    assert [(d["day"], d["fallback_served"]) for d in led.stats_by_day()] == [(1, 2), (2, 0)]


@pytest.mark.req("FR-12.32", "NFR-6")
def test_v04_database_gets_student_model_column(tmp_path):
    """A v0.4 ledger opens and gains `student_model` and `shadow_model`. Its rows have no model, so they keep counting
    toward their versions exactly as before; a new fallback row does not, an adapter row does, and opening the file
    again changes nothing."""
    db = tmp_path / "v04.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, served_by TEXT, student_version INTEGER,
            confidence REAL, escalated INTEGER, audited INTEGER, audit_agree INTEGER, shadow_version INTEGER,
            shadow_serve INTEGER, shadow_agree INTEGER, latency_ms REAL, cost_usd REAL, baseline_cost_usd REAL, client TEXT
        );
        CREATE TABLE versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts REAL, day INTEGER, base_model TEXT, adapter_uri TEXT,
            train_examples INTEGER, dataset_hash TEXT, eval TEXT, state TEXT, traffic_pct INTEGER DEFAULT 0,
            stage_since INTEGER DEFAULT 0, bom TEXT
        );
        INSERT INTO versions (created_ts, day, base_model, adapter_uri, train_examples, dataset_hash, eval, state, traffic_pct)
            VALUES (1800000000, 1, 'b', 'student-v1', 10, 'h1', '{"threshold": 0.5}', 'live', 100),
                   (1800000000, 1, 'b', 'student-v2', 10, 'h2', '{"threshold": 0.5}', 'shadow', 0);
        INSERT INTO requests (ts, day, task, served_by, student_version, escalated, audited, audit_agree, shadow_version,
                              shadow_serve, shadow_agree, cost_usd, baseline_cost_usd, latency_ms)
            VALUES (1800000000, 1, 'ticket-triage', 'student', 1, 0, 1, 1, 2, 1, 1, 0.001, 0.01, 300),
                   (1800000001, 1, 'ticket-triage', 'student', 1, 0, 1, 0, 2, 1, 0, 0.001, 0.01, 300);
    """)
    con.commit()
    con.close()
    led = Ledger(str(db))
    cols = {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
    assert {"student_model", "shadow_model"} <= cols
    assert [(r["student_model"], r["shadow_model"]) for r in _rows(led)] == [(None, None), (None, None)]
    assert led.version_live_quality(1) == (2, 0.5)
    assert led.shadow_quality(2)["n"] == 2 and led.shadow_quality(2)["precision"] == 0.5
    assert led.stats_by_day()[0]["fallback_served"] == 0
    _log(led, day=1, student_version=1, audited=1, audit_agree=1, student_model=FALLBACK)
    assert led.version_live_quality(1) == (2, 0.5) and led.stats_by_day()[0]["fallback_served"] == 1
    _log(led, day=1, student_version=1, audited=1, audit_agree=1, student_model="student-v1")
    assert led.version_live_quality(1) == (3, 2 / 3)
    led.close()
    again = Ledger(str(db))
    assert again.version_live_quality(1) == (3, 2 / 3) and len(_rows(again)) == 4


# ---------- off the request path ----------
@pytest.mark.req("FR-12.32", "FR-12.31", "INV-15")
def test_offpath_rows_carry_their_model_and_a_failed_shadow_clears_it():
    """Off the request path the row is logged pending with the models already: the fallback's audit never counts once
    its verdict arrives; a shadow under the fallback takes no sidecar place; an adapter shadow's pending row names its
    model, which a failed shadow clears with its verdict, as the inline run logs it."""
    led = Ledger(":memory:")
    live = _version(led, "live", GOOD)
    teacher = RecordingProvider(B)
    r = Ratchet(_settings(audit_rate=1.0), led, teacher, _hosted(RecordingProvider(A)), seed=1, sidecar=SideCar(2))
    out = r.handle(_ask(1), TRIAGE, meta=META)
    assert out.overhead_pending and out.version is None
    assert r.sidecar.drain(WAIT_S)
    (row,) = _rows(led)
    assert (row["student_model"], row["audited"], row["audit_agree"]) == (FALLBACK, 1, 0)
    assert led.version_live_quality(live) == (0, None)

    shadow = _version(led, "shadow", GOOD)
    quiet = Ratchet(_settings(), led, teacher, _hosted(RecordingProvider(A)), seed=1, sidecar=SideCar(2))
    assert not quiet.handle(_ask(2), TRIAGE, meta=META).overhead_pending
    assert quiet.sidecar.snapshot()["submitted"] == 0 and _rows(led)[-1]["shadow_version"] is None

    # On the endpoint: the shadow's job is queued with its model; its samples fail, so the verdict and the model go.
    adapter = f"student-v{shadow}"
    students = {live: RecordingProvider(A), shadow: RecordingProvider(A, raise_on={adapter})}
    on_endpoint = Ratchet(
        _settings(), led, teacher, lambda v: (students[v.id], v.adapter_uri), seed=1, sidecar=SideCar(2, max_bytes=10**6)
    )
    assert on_endpoint.handle(_ask(3), TRIAGE, meta=META).overhead_pending
    assert on_endpoint.sidecar.drain(WAIT_S)
    cols = ("shadow_version", "shadow_serve", "shadow_model")
    assert [_rows(led)[-1][k] for k in cols] == [None, None, None] and on_endpoint.health()["shadow_errors"] == 1
    inline = Ratchet(_settings(), led, teacher, lambda v: (students[v.id], v.adapter_uri), seed=1)
    inline.handle(_ask(4), TRIAGE)
    assert [_rows(led)[-1][k] for k in cols] == [None, None, None]  # the inline run logs the same

    # A shadow that answers: the pending row names the adapter from the start and keeps it with the verdict.
    gated = _Gated(A)
    students[shadow] = gated
    assert on_endpoint.handle(_ask(5), TRIAGE, meta=META).overhead_pending
    assert gated.entered.wait(WAIT_S)
    assert [_rows(led)[-1][k] for k in cols] == [shadow, None, adapter]  # pending: no verdict yet, the model already
    gated.release.set()
    assert on_endpoint.sidecar.drain(WAIT_S)
    assert [_rows(led)[-1][k] for k in cols] == [shadow, 1, adapter]


class _Gated(RecordingProvider):
    """A model that answers only once `release` is set; `entered` is set when a call starts."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, *args: Any, **kwargs: Any) -> list[Completion]:
        self.entered.set()
        assert self.release.wait(WAIT_S), "the test never released the model"
        return super().complete(*args, **kwargs)
