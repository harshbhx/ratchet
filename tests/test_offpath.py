"""Off-path quality checks (FR-12.31, NFR-1, NFR-3, INV-15; ADR 0072).

A request the gateway serves (`RequestMeta`) returns as soon as its answer is ready: the teacher's audit of a served
student answer, the shadow candidate's samples and the teacher's arbitration run afterwards on the router's bounded
sidecar (`ratchet/sidecar.py`), and finish the request's ledger row with `Ledger.update_request`. Here:

- `SideCar` itself: never blocking, bounded, places kept for audits, the request's context in its jobs, drain and close.
- `Ledger.update_request` and the release gate's reads: a pending audit or shadow counts for nothing until its verdict
  is written; a verdict is written once; costs add up call by call.
- The router: the answer returns before the teacher's audit ends; the rows an off-path run leaves are the rows an
  inline run writes (identical release signals); a drawn audit is never dropped (it runs inline when the queue is full)
  and a shadow without a place is skipped and counted per version; failures follow INV-15; the arbitration's shorter
  deadline; requests without meta (Scout, the simulator) stay inline; a ledger error gives the place back; a sidecar
  that stops meanwhile hands the job back to the request.
- The gateway: `ratchet.overhead_pending`, the `sidecar` block of `/ratchet/stats`, the gauges, and the lifespan's
  drain at shutdown.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, FakeClock, FakeOpenAIServer, RecordingProvider, _version
from fastapi.testclient import TestClient

from ratchet import metrics, obs
from ratchet.config import ConfigError, Settings, load
from ratchet.ledger import AUDIT_PENDING_SQL, Ledger
from ratchet.providers import Completion, OpenAICompatProvider, UpstreamError
from ratchet.resilience import RetryPolicy
from ratchet.router import ARBITRATION_DEADLINE_S, Ratchet, RequestMeta
from ratchet.sidecar import MAX_BYTES, SideCar, job_bytes

A = json.dumps({"category": "billing", "priority": "high"})
B = json.dumps({"category": "refund", "priority": "high"})
C = json.dumps({"category": "account", "priority": "low"})
TRIAGE = "ticket-triage"
CHAT = "/v1/chat/completions"
META = RequestMeta(request_id="req_" + "0" * 32)
WAIT_S = 10.0  # how long a test waits for a job; a passing run waits milliseconds


def _ask(tag: object) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice ({tag})"}]


def _row(led: Ledger, rid: int) -> dict[str, Any]:
    return dict(led.db.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone())


def _events(led: Ledger, kind: str) -> list[str]:
    return [e["detail"] for e in led.events() if e["kind"] == kind]


@pytest.fixture()
def ratchet_log(caplog: pytest.LogCaptureFixture):
    """caplog wired to the `ratchet` logger (which does not propagate once a gateway configured it), at INFO."""
    logger = logging.getLogger("ratchet")
    propagate = logger.propagate
    logger.propagate = False  # each record once, through this handler, whether or not a gateway configured the logger
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger="ratchet")
    yield caplog
    logger.removeHandler(caplog.handler)
    logger.propagate = propagate


def _json_lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    fmt = obs.JsonFormatter()
    return [json.loads(fmt.format(r)) for r in caplog.records if r.name == "ratchet" and r.getMessage() == event]


class GatedProvider(RecordingProvider):
    """A model that answers only once `release` is set; `entered` is set when a call starts."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, *args: Any, **kwargs: Any) -> list[Completion]:
        self.entered.set()
        assert self.release.wait(WAIT_S), "the test never released the model"
        return super().complete(*args, **kwargs)


def _cascade(
    teacher: RecordingProvider,
    audit_rate: float = 1.0,
    workers: int = 2,
    queue: int = 1000,
    *,
    shadow: RecordingProvider | None = None,
    student: RecordingProvider | None = None,
    max_bytes: int = MAX_BYTES,
    **settings: Any,
) -> tuple[Ledger, dict[str, int], Ratchet]:
    """A live version with a calibrated threshold of 0.5, which its unanimous student (confidence 0.98) clears, an
    optional shadow candidate, and a sidecar with `workers` threads."""
    led = Ledger(":memory:")
    vids = {"live": _version(led, "live", {"threshold": 0.5}, adapter_uri="live")}
    students = {"live": student or RecordingProvider(A)}
    if shadow is not None:
        vids["shadow"] = _version(led, "shadow", {"threshold": 0.5}, adapter_uri="shadow")
        students["shadow"] = shadow
    base: dict[str, Any] = {"on_teacher_failure": "error", "teacher_fallback_model": "", "teacher_breaker_failures": 5}
    s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=audit_rate, teacher_reasoning_tokens=0, **{**base, **settings})
    r = Ratchet(
        s,
        led,
        teacher,
        lambda v: (students[v.adapter_uri], v.adapter_uri),
        seed=1,
        sidecar=SideCar(workers, queue, max_bytes=max_bytes),
    )
    return led, vids, r


# ---------- the sidecar ----------
def test_sidecar_without_workers_runs_nothing():
    car = SideCar(0)
    assert car.inline and not car.accepting and car.reserve() is None and car.reserve(priority=True) is None
    assert car.snapshot()["full"] == 0  # no workers is not a full queue
    assert car.drain(0) and car.close(0) == {}


def test_sidecar_is_bounded_never_blocks_and_keeps_places_for_audits():
    car = SideCar(1, 10)  # one place in ten for audits
    assert car.audit_places == 1
    t0 = time.monotonic()
    plain = [car.reserve() for _ in range(9)]
    assert all(s is not None for s in plain) and car.reserve() is None  # shadow work stops before the audits' place
    audit = car.reserve(priority=True)
    assert audit is not None and car.reserve(priority=True) is None  # now the queue is full for everyone
    assert time.monotonic() - t0 < 1.0  # a refusal never waits
    snap = car.snapshot()
    assert (snap["queued"], snap["full"], snap["submitted"]) == (10, 2, 0)
    plain[0].cancel()
    assert car.reserve(priority=True) is not None  # a place given back can be taken again
    with pytest.raises(RuntimeError):
        plain[0].cancel()  # a slot ends once


def test_sidecar_audit_places_scale_with_the_queue():
    assert [SideCar(1, q).audit_places for q in (1, 2, 10, 1000)] == [0, 1, 1, 100]
    assert SideCar(1, 1000, reserve=0).audit_places == 0
    bad_args = ({"workers": -1}, {"workers": 65}, {"workers": True}, {"max_queue": 0}, {"max_queue": 100_001}, {"max_bytes": 0})
    for bad in bad_args:
        with pytest.raises(ValueError):
            SideCar(**{"workers": 1, "max_queue": 10, **bad})
    with pytest.raises(ValueError):
        SideCar(1, 10, reserve=1.0)


def test_sidecar_bounds_the_request_text_its_jobs_hold():
    """Each job keeps its request's messages until it ends, so the bytes they hold are bounded too (`max_bytes`, with
    the same share kept for audits); a reservation past the budget is refused like one for a full queue, and every way
    a job ends gives its bytes back."""
    car = SideCar(1, 100, max_bytes=1000)  # 100 bytes kept for audits
    assert car.audit_bytes == 100
    shadow = car.reserve(size=600)
    assert shadow is not None and car.reserve(size=301) is None  # past the 900 bytes shadow work may hold
    audit = car.reserve(priority=True, size=400)
    assert audit is not None and car.reserve(priority=True, size=1) is None  # the whole budget is taken
    assert (car.snapshot()["bytes"], car.snapshot()["max_bytes"], car.snapshot()["full"]) == (1000, 1000, 2)
    shadow.cancel()
    assert car.snapshot()["bytes"] == 400
    gate, started = threading.Event(), threading.Event()
    assert audit.submit(lambda: started.set() or gate.wait(WAIT_S))
    assert started.wait(WAIT_S) and (car.snapshot()["queued"], car.snapshot()["bytes"]) == (0, 400)  # held while running
    late = car.reserve(size=400)
    queued = car.reserve(size=100)
    assert late is not None and queued is not None and queued.submit(lambda: None)
    assert car.reserve(priority=True, size=1001) is None  # larger than the whole budget: never taken
    assert car.close(0.0) == {None: 1} and car.snapshot()["bytes"] == 800  # the abandoned job's bytes come back
    assert not late.submit(lambda: None) and car.snapshot()["bytes"] == 400  # so do a refused job's
    gate.set()
    assert car.drain(WAIT_S) and car.snapshot()["bytes"] == 0
    # Python stores wider characters in more bytes, and the estimate follows it.
    short, wide = (
        job_bytes([{"role": "user", "content": "x" * 1000}]),
        job_bytes([{"role": "user", "content": "\U0001f600" * 1000}]),
    )
    assert 1000 < short < 2000 and wide > 4000


@pytest.mark.req("FR-12.31")
def test_a_long_request_past_the_byte_budget_audits_on_the_request_path():
    led, vids, r = _cascade(RecordingProvider(A), audit_rate=1.0, max_bytes=3000)
    short = r.handle(_ask(1), TRIAGE, meta=META)
    long = r.handle([{"role": "user", "content": "x" * 5000}], TRIAGE, meta=META)
    assert short.overhead_pending and not long.overhead_pending and long.audited
    assert r.sidecar.drain(WAIT_S) and r.sidecar.snapshot()["bytes"] == 0
    assert [(x["audited"], x["audit_agree"]) for x in (_row(led, short.request_id), _row(led, long.request_id))] == [(1, 1)] * 2
    assert r.sidecar_report()["by_version"][0]["audits_inline"] == 1


def test_sidecar_jobs_run_in_the_submitting_context_and_failures_are_counted(ratchet_log):
    var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="unset")
    seen: list[str] = []
    car = SideCar(2, 10)
    var.set("request-1")
    slot = car.reserve()
    assert slot is not None and slot.submit(lambda: seen.append(var.get()))
    bad = car.reserve()
    assert bad is not None

    def boom() -> None:
        raise ValueError("a message that must not be logged")

    assert bad.submit(boom)
    assert car.drain(WAIT_S)
    assert seen == ["request-1"]
    snap = car.snapshot()
    assert (snap["submitted"], snap["done"], snap["failed"], snap["queued"], snap["running"]) == (2, 1, 1, 0, 0)
    (failed,) = _json_lines(ratchet_log, "sidecar_job_failed")
    assert failed["error"] == "ValueError" and "must not be logged" not in json.dumps(failed)


def test_sidecar_close_drains_then_gives_up_and_refuses_late_jobs():
    gate, started = threading.Event(), threading.Event()
    car = SideCar(1, 10)
    first, second, late = car.reserve(), car.reserve(), car.reserve()
    assert first is not None and second is not None and late is not None
    assert first.submit(lambda: started.set() or gate.wait(WAIT_S), tag=("a", None))
    assert second.submit(lambda: None, tag=("b", None))
    assert started.wait(WAIT_S) and not car.drain(0.05)  # the first job holds the only worker
    left = car.close(0.05)  # `late` is still reserved: closing waits for it, then gives up
    assert left == {("b", None): 1} and not car.accepting and car.reserve(priority=True) is None
    assert not late.submit(lambda: None)  # refused: the caller runs it itself
    gate.set()
    assert car.drain(WAIT_S)
    snap = car.snapshot()
    assert (snap["done"], snap["abandoned"], snap["refused"], snap["closed"]) == (1, 1, 1, True)
    assert car.close(0) == {}  # again: nothing more to give up


# ---------- the ledger ----------
def test_update_request_writes_each_verdict_once_and_adds_costs_call_by_call():
    led = Ledger(":memory:")
    rid = led.log_request(
        task="t", served_by="student", student_version=1, audited=1, audit_agree=None, shadow_version=2, cost_usd=0.1,
        student_cost_usd=0.1, teacher_cost_usd=0.0, stored=0, baseline_cost_usd=0.5,
    )  # fmt: skip
    terms = [0.2, 0.3, 1e-9]
    assert led.update_request(rid, audited=1, audit_agree=0, add_cost_usd=terms, add_teacher_cost_usd=[0.2], stored=1)
    want = ((0.1 + 0.2) + 0.3) + 1e-9  # the float an inline run sums
    row = _row(led, rid)
    assert (row["audited"], row["audit_agree"], row["cost_usd"], row["teacher_cost_usd"], row["stored"]) == (1, 0, want, 0.2, 1)
    # A verdict is written once: a second write changes nothing, and `stored` never goes back down.
    assert led.update_request(rid, audited=0, stored=0)
    assert (_row(led, rid)["audited"], _row(led, rid)["audit_agree"], _row(led, rid)["stored"]) == (1, 0, 1)
    assert led.update_request(rid, shadow_version=2, shadow_serve=1, shadow_agree=None, baseline_cost_usd=0.7)
    assert led.update_request(rid, shadow_version=None, shadow_serve=None, shadow_agree=None)  # no longer pending
    row = _row(led, rid)
    assert (row["shadow_version"], row["shadow_serve"], row["shadow_agree"], row["baseline_cost_usd"]) == (2, 1, None, 0.7)
    assert not led.update_request(rid + 1, audited=0)  # no such row
    for bad in (
        {"route": "x"},
        {"served_by": "teacher"},
        {"add_route": 1},
        {"add_cost_usd": float("nan")},
        {"add_cost_usd": True},
    ):
        with pytest.raises(ValueError):
            led.update_request(rid, **bad)
    assert _row(led, rid)["cost_usd"] == want  # a refused call writes nothing


def test_update_request_does_not_invalidate_the_hot_path_caches(tmp_path):
    """Finishing a request's checks rewrites no column a cache keys on, so it leaves the `requests` generation the caches
    compare alone (a tick does not recount a stage); any other rewrite still moves it (ADR 0060)."""
    led = Ledger(str(tmp_path / "gen.db"))
    rid = led.log_request(task="t", served_by="student", student_version=1, audited=1, audit_agree=None, cost_usd=0.0)
    before = led._requests_gen()
    led.update_request(rid, audited=1, audit_agree=1, add_cost_usd=[0.1])
    assert led._requests_gen() == before
    led.db.execute("UPDATE requests SET route='x' WHERE id=?", (rid,))
    led.db.commit()
    assert led._requests_gen() == before + 1


@pytest.mark.req("FR-12.31")
def test_live_quality_ignores_pending_audits_until_their_verdict(tmp_path):
    led = Ledger(str(tmp_path / "q.db"))
    vid = _version(led, "live", {"threshold": 0.5})

    def audit(agree: int | None) -> int:
        return led.log_request(task="t", served_by="student", student_version=vid, audited=1, audit_agree=agree, cost_usd=0.0)

    audit(1)
    pending = [audit(None), audit(None), audit(None)]
    assert led.version_live_quality(vid) == (1, 1.0)  # pending audits are no evidence yet
    led.update_request(pending[0], audited=1, audit_agree=0)
    led.update_request(pending[1], audited=0)  # the audit failed: not an audit
    assert led.version_live_quality(vid) == (2, 0.5)
    led.update_request(pending[2], audited=1, audit_agree=1)
    assert led.version_live_quality(vid) == (3, 2 / 3) == led.version_live_quality(vid, current_stage_only=False)
    assert led.stats_by_day()[0]["audit_agreement"] == 2 / 3
    fresh = Ledger(str(tmp_path / "q.db"))  # a new tally (another process, a restart) reads the same numbers
    assert fresh.version_live_quality(vid) == (3, 2 / 3)
    # Finding a stage's pending audits searches the gate's index, never the whole table (ADR 0060).
    plan = [r[-1] for r in led.db.execute("EXPLAIN QUERY PLAN " + AUDIT_PENDING_SQL, (vid, 0, 10))]
    assert plan[0] == "SEARCH requests USING INDEX requests_version_audited (student_version=? AND audited=? AND id>? AND id<?)"
    # The version's adapter (attribution, ADR 0073) is read once, by primary key; nothing scans a table.
    assert "SEARCH versions USING INTEGER PRIMARY KEY (rowid=?)" in plan and not any("SCAN" in p for p in plan)


@pytest.mark.req("FR-12.31")
def test_shadow_quality_ignores_pending_shadow_rows():
    led = Ledger(":memory:")
    shadow = _version(led, "shadow", {"threshold": 0.5})
    done = led.log_request(task="t", served_by="student", shadow_version=shadow, shadow_serve=1, shadow_agree=1, cost_usd=0.0)
    pending = led.log_request(task="t", served_by="student", shadow_version=shadow, cost_usd=0.0)
    assert done and led.shadow_quality(shadow)["n"] == 1
    led.update_request(pending, shadow_version=shadow, shadow_serve=1, shadow_agree=0)
    q = led.shadow_quality(shadow)
    assert (q["n"], q["served"], q["precision"]) == (2, 2, 0.5)


# ---------- the router ----------
@pytest.mark.req("FR-12.31", "NFR-1")
def test_an_audited_answer_returns_before_the_teachers_audit_ends():
    teacher = GatedProvider(A)
    led, vids, r = _cascade(teacher, audit_rate=1.0)
    t0 = time.monotonic()
    res = r.handle(_ask(1), TRIAGE, meta=META)
    assert time.monotonic() - t0 < WAIT_S / 2  # it did not wait for the teacher
    serving = res.cost_usd
    assert (res.served_by, res.overhead_pending, res.audited, res.usage_breakdown["audit"]) == ("student", True, False, None)
    assert teacher.entered.wait(WAIT_S)  # the audit is running on a worker meanwhile
    row = _row(led, res.request_id)
    assert (row["audited"], row["audit_agree"], row["cost_usd"], row["teacher_cost_usd"]) == (1, None, serving, 0.0)
    assert led.version_live_quality(vids["live"]) == (0, None)  # pending: no evidence yet
    teacher.release.set()
    assert r.sidecar.drain(WAIT_S)
    row = _row(led, res.request_id)
    audit_price = r._cost(OPEN_TEACHER, teacher.complete(_ask(1), OPEN_TEACHER)[0])
    assert (row["audited"], row["audit_agree"], row["stored"]) == (1, 1, 1)
    assert row["cost_usd"] == serving + audit_price and row["teacher_cost_usd"] == audit_price
    assert row["baseline_cost_usd"] == r._cost(OPEN_TEACHER, teacher.complete(_ask(1), OPEN_TEACHER)[0], 1)
    assert led.version_live_quality(vids["live"]) == (1, 1.0) and len(led.examples()) == 1


def _scripted(i: int) -> dict[str, Any]:
    """What each model does with request `i` in the equivalence test (deterministic, so two runs see the same calls)."""
    escalate = i % 6 == 5
    return {
        "student": [A, B, C] if escalate else A,  # three different answers: low confidence, the teacher answers
        "teacher_fails": i % 9 == 2 and not escalate,  # only audits and arbitrations fail, never a served answer
        "teacher": "not json" if i % 10 == 7 else (B if i % 4 == 1 else A),
        "shadow_fails": i % 8 == 6,
        "shadow": B if i % 3 == 0 else A,
    }


def _index(messages: list[dict[str, Any]]) -> int:
    return int(messages[-1]["content"].rsplit("(", 1)[1].rstrip(")"))


def _equivalence_run(workers: int) -> tuple[Ledger, Ratchet, tuple[float, float]]:
    """60 scripted requests through a cascade with `workers` sidecar threads, each drained before the next, and what
    they added to `ratchet_cost_usd_total` and `ratchet_baseline_usd_total` (client `none`)."""
    before = (metrics.COST.value("none"), metrics.BASELINE.value("none"))

    def student(messages: list[dict[str, Any]], model: str) -> Any:
        return _scripted(_index(messages))["student"]

    def teacher(messages: list[dict[str, Any]], model: str) -> str:
        plan = _scripted(_index(messages))
        if plan["teacher_fails"]:
            raise httpx.ConnectError("teacher down")
        return plan["teacher"]

    def shadow(messages: list[dict[str, Any]], model: str) -> str:
        plan = _scripted(_index(messages))
        if plan["shadow_fails"]:
            raise httpx.ConnectError("shadow endpoint down")
        return plan["shadow"]

    led, _, r = _cascade(
        RecordingProvider(teacher),
        audit_rate=0.5,
        workers=workers,
        student=RecordingProvider(student),
        shadow=RecordingProvider(shadow),
    )
    for i in range(60):
        r.handle(_ask(i), TRIAGE, meta=META)
        assert r.sidecar.drain(WAIT_S)
    return led, r, (metrics.COST.value("none") - before[0], metrics.BASELINE.value("none") - before[1])


def _table(led: Ledger, table: str, skip: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{k: v for k, v in dict(row).items() if k not in skip} for row in led.db.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.mark.req("FR-12.31", "INV-15")
def test_offpath_checks_leave_the_rows_an_inline_run_writes():
    """The same traffic, inline (no workers) and off the request path: every request row (verdicts, pending flags
    cleared, costs to the last bit, stored, baseline), every example, event and failure, the failure counters and the
    release gate's numbers and the money the metrics count are the same. Audits that agree, disagree, fail or measure
    nothing; shadows that agree, disagree and get arbitrated, fail to sample or meet a failed audit; escalations."""
    inline_led, inline, inline_spend = _equivalence_run(0)
    off_led, off, off_spend = _equivalence_run(2)
    assert inline.sidecar.snapshot()["submitted"] == 0 and off.sidecar.snapshot()["submitted"] > 20
    timing = ("ts", "wall_ms")
    rows = _table(off_led, "requests", timing)
    assert rows == _table(inline_led, "requests", timing)
    assert {r["audited"] for r in rows} == {0, 1} and {r["audit_agree"] for r in rows} == {None, 0, 1}
    assert {r["shadow_agree"] for r in rows if r["shadow_version"]} == {None, 0, 1}
    assert any(r["shadow_version"] is None and r["served_by"] == "student" for r in rows)  # failed shadow samples
    assert not [r for r in rows if r["audited"] == 1 and r["audit_agree"] is None]  # nothing left pending
    stamps = ("ts", "last_seen_ts")  # wall-clock times (last_seen_ts: retention's clock, FR-13.4)
    assert _table(off_led, "examples", stamps) == _table(inline_led, "examples", stamps)
    assert _table(off_led, "failures", ("ts", "latency_ms")) == _table(inline_led, "failures", ("ts", "latency_ms"))
    assert [(e["kind"], e["detail"]) for e in off_led.events()] == [(e["kind"], e["detail"]) for e in inline_led.events()]
    assert {k: v for k, v in off.health().items() if k != "breakers"} == {
        k: v for k, v in inline.health().items() if k != "breakers"
    }
    for led in (inline_led, off_led):
        assert led.version_live_quality(1)[0] > 0
    assert off_led.version_live_quality(1) == inline_led.version_live_quality(1)
    assert off_led.shadow_quality(2) == inline_led.shadow_quality(2)
    # The metrics count each cent once: the request its serving cost (and a baseline unless an audit is pending), the
    # job the rest. Only the order of the float additions differs.
    totals = off_led.db.execute("SELECT SUM(cost_usd), SUM(baseline_cost_usd) FROM requests").fetchone()
    assert off_spend == pytest.approx(inline_spend, rel=1e-9) and off_spend == pytest.approx(tuple(totals), rel=1e-9)


@pytest.mark.req("FR-12.31")
def test_a_drawn_audit_runs_inline_when_the_queue_is_full():
    """An audit that was drawn is never dropped (a sample that depended on load would bias the release statistics):
    with every place taken, it runs on the request path, counted per version."""
    led, vids, r = _cascade(RecordingProvider(B), audit_rate=1.0, workers=1, queue=2)
    held = [r.sidecar.reserve(priority=True), r.sidecar.reserve(priority=True)]
    assert all(s is not None for s in held) and r.sidecar.reserve(priority=True) is None
    res = r.handle(_ask(1), TRIAGE, meta=META)
    assert (res.served_by, res.overhead_pending, res.audited) == ("student", False, True)
    row = _row(led, res.request_id)
    assert (row["audited"], row["audit_agree"]) == (1, 0)
    report = r.sidecar_report()
    assert report["by_version"] == [
        {"version": vids["live"], "audits_inline": 1, "shadows_dropped": 0, "audits_abandoned": 0, "shadows_abandoned": 0}
    ]
    assert _events(led, "sidecar_inline") == [f"v{vids['live']}: audit ran on the request path, the off-path queue is full"]
    for slot in held:
        slot.cancel()


@pytest.mark.req("FR-12.31")
def test_a_shadow_without_a_place_is_skipped_and_counted_but_rides_with_an_audit():
    teacher = RecordingProvider(A)
    led, vids, r = _cascade(teacher, audit_rate=0.0, workers=1, queue=2, shadow=RecordingProvider(A))
    held = r.sidecar.reserve()  # the one place shadow work may take; the other is kept for audits
    assert held is not None and r.sidecar.reserve() is None
    res = r.handle(_ask(1), TRIAGE, meta=META)
    assert res.served_by == "student" and not res.overhead_pending
    assert _row(led, res.request_id)["shadow_version"] is None  # skipped, and nothing else changed
    assert _events(led, "sidecar_dropped") == [f"v{vids['shadow']}: shadow skipped, the off-path queue is full"]
    assert r.sidecar_report()["by_version"][1] == {
        "version": vids["shadow"], "audits_inline": 0, "shadows_dropped": 1, "audits_abandoned": 0, "shadows_abandoned": 0,
    }  # fmt: skip
    r.s.audit_rate = 1.0  # an audit takes the place kept for audits, and the shadow goes with it
    res = r.handle(_ask(2), TRIAGE, meta=META)
    assert res.overhead_pending
    held.cancel()
    assert r.sidecar.drain(WAIT_S)
    row = _row(led, res.request_id)
    assert (row["audited"], row["audit_agree"], row["shadow_version"], row["shadow_serve"], row["shadow_agree"]) == (
        1, 1, vids["shadow"], 1, 1,
    )  # fmt: skip


@pytest.mark.req("FR-12.31", "INV-15")
def test_a_shadow_queued_after_an_inline_audit_keeps_the_audit_as_its_reference():
    """The queue is full when the audit is drawn, so the audit runs on the request path; a place frees up meanwhile and
    the shadow goes off the path. It is judged as inline: against the counted audit's answer (no arbitration), and after
    a failed audit not arbitrated at all (INV-15), so the teacher is called once either way."""
    for teacher_down, want in ((False, (1, 0, 1, 1)), (True, (0, None, 1, None))):
        held: list[Any] = []

        def teacher_answer(messages: list[dict[str, Any]], model: str, down: bool = teacher_down, held: list = held) -> str:
            for slot in held:  # the inline audit is running: the places come free before the shadow asks for one
                slot.cancel()
            held.clear()
            if down:
                raise httpx.ConnectError("teacher down")
            return B  # disagrees with the student's A, agrees with the shadow's B

        teacher = RecordingProvider(teacher_answer)
        led, vids, r = _cascade(teacher, audit_rate=1.0, workers=1, queue=2, shadow=RecordingProvider(B))
        held += [r.sidecar.reserve(priority=True), r.sidecar.reserve(priority=True)]
        assert all(s is not None for s in held)
        res = r.handle(_ask(1), TRIAGE, meta=META)
        assert res.audited is not teacher_down and res.overhead_pending  # the audit ran inline, the shadow is queued
        assert r.sidecar.drain(WAIT_S)
        row = _row(led, res.request_id)
        assert (row["audited"], row["audit_agree"], row["shadow_serve"], row["shadow_agree"]) == want
        assert row["shadow_version"] == vids["shadow"] and len(teacher.calls) == 1
        assert r.sidecar_report()["by_version"][0]["audits_inline"] == 1


@pytest.mark.req("FR-12.31", "INV-15")
def test_offpath_failures_follow_the_optional_call_rules():
    """A failed off-path audit is no audit (`audited` 0) and stops the arbitration, which leaves the shadow's
    disagreement unarbitrated (counted against it); a failed shadow sample leaves no shadow record. The request was
    served long before; each failure is counted, logged as a throttled event and a failures row naming the request."""
    teacher = RecordingProvider(A, raise_on={OPEN_TEACHER})
    led, vids, r = _cascade(teacher, audit_rate=1.0, shadow=RecordingProvider(B))
    res = r.handle(_ask(1), TRIAGE, meta=META)
    assert res.served_by == "student" and res.overhead_pending
    assert r.sidecar.drain(WAIT_S)
    row = _row(led, res.request_id)
    assert (row["audited"], row["audit_agree"], row["shadow_version"], row["shadow_serve"], row["shadow_agree"]) == (
        0, None, vids["shadow"], 1, None,
    )  # fmt: skip
    assert len(teacher.calls) == 1  # the audit; no arbitration after it failed
    assert r.health()["audit_errors"] == 1 and _events(led, "audit_error") == [f"{TRIAGE}: ConnectError"]
    failures = led.failures_for(META.request_id)
    assert [f["stage"] for f in failures] == ["audit"]
    assert led.shadow_quality(vids["shadow"])["unarbitrated"] == 1

    led, vids, r = _cascade(RecordingProvider(A), audit_rate=0.0, shadow=RecordingProvider(A, raise_on={"shadow"}))
    res = r.handle(_ask(2), TRIAGE, meta=META)
    assert r.sidecar.drain(WAIT_S)
    assert _row(led, res.request_id)["shadow_version"] is None and r.health()["shadow_errors"] == 1
    assert r.health()["breakers"]["shadow"]["failures"] == 1  # the shadow's own breaker


@pytest.mark.req("FR-12.31")
def test_an_offpath_arbitration_gives_up_sooner_than_a_served_answer():
    """Off the request path the arbitration's retries end after ARBITRATION_DEADLINE_S; an unarbitrated disagreement
    counts against the shadow, so the shorter wait never flatters it. On the request path nothing changes."""
    for workers, want in ((2, ARBITRATION_DEADLINE_S), (0, None)):
        teacher = RecordingProvider(A)
        led, _, r = _cascade(teacher, audit_rate=0.0, workers=workers, shadow=RecordingProvider(B))
        r.handle(_ask(1), TRIAGE, meta=META)
        assert r.sidecar.drain(WAIT_S)
        assert [c.kwargs.get("deadline_s") for c in teacher.calls] == [want]


def test_a_provider_deadline_shortens_its_retries_never_lengthens_them():
    clock = FakeClock()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, headers={"Retry-After": "2"}, json={"error": {"message": "busy"}})

    p = OpenAICompatProvider(
        "https://teacher.test/v1",
        "k",
        retry=RetryPolicy(max_attempts=4, deadline_s=180.0),
        transport=httpx.MockTransport(handler),
        sleep=clock.advance,
        clock=clock,
    )
    with pytest.raises(UpstreamError):
        p.complete([{"role": "user", "content": "hi"}], "m")
    assert len(calls) == 4
    calls.clear()
    with pytest.raises(UpstreamError):
        p.complete([{"role": "user", "content": "hi"}], "m", deadline_s=3.0)  # one wait of 2 s fits, the next does not
    assert len(calls) == 2
    calls.clear()
    with pytest.raises(UpstreamError):
        p.complete([{"role": "user", "content": "hi"}], "m", deadline_s=10_000.0)
    assert len(calls) == 4


def test_requests_without_meta_keep_their_checks_on_the_request_path():
    """Scout's agents, the simulator and the mock warm-up pass no meta: their Result holds every cent (Scout's budget
    guard sums it, INV-11), so their checks never leave the request path, whatever the sidecar."""
    teacher = RecordingProvider(A)
    led, _, r = _cascade(teacher, audit_rate=1.0)
    res = r.handle(_ask(1), TRIAGE)
    assert (res.overhead_pending, res.audited) == (False, True) and res.cost_usd == _row(led, res.request_id)["cost_usd"]
    res = r.handle(_ask(2), TRIAGE, meta=RequestMeta(request_id=META.request_id, offpath=False))
    assert not res.overhead_pending and r.sidecar.snapshot()["submitted"] == 0


def test_a_ledger_error_gives_the_place_back():
    led, _, r = _cascade(RecordingProvider(A), audit_rate=1.0)

    def broken(**row: Any) -> int:
        raise OSError("disk full")

    led.log_request = broken  # type: ignore[method-assign]
    with pytest.raises(OSError):
        r.handle(_ask(1), TRIAGE, meta=META)
    assert r.sidecar.snapshot()["queued"] == 0 and r.sidecar.drain(0)


def test_a_sidecar_that_stops_meanwhile_hands_the_job_back_to_the_request():
    led, _, r = _cascade(RecordingProvider(A), audit_rate=1.0)
    reserve = r.sidecar.reserve

    def reserve_then_close(priority: bool = False, size: int = 0) -> Any:
        slot = reserve(priority, size)
        closing = threading.Thread(target=r.sidecar.close, args=(0.0,))  # gives up on the reserved place at once
        closing.start()
        closing.join(WAIT_S)
        return slot

    r.sidecar.reserve = reserve_then_close  # type: ignore[method-assign]
    res = r.handle(_ask(1), TRIAGE, meta=META)
    assert res.overhead_pending  # cost_usd is still the serving cost; the row has the rest already
    row = _row(led, res.request_id)
    assert (row["audited"], row["audit_agree"]) == (1, 1) and row["cost_usd"] > res.cost_usd
    assert r.sidecar.snapshot()["refused"] == 1


@pytest.mark.req("FR-12.31", "INV-15")
def test_close_gives_up_on_queued_jobs_and_counts_them_per_version():
    teacher = GatedProvider(A)
    led, vids, r = _cascade(teacher, audit_rate=1.0, workers=1, shadow=RecordingProvider(A))
    first = r.handle(_ask(1), TRIAGE, meta=META)
    assert teacher.entered.wait(WAIT_S)  # the only worker is busy with the first audit
    second = r.handle(_ask(2), TRIAGE, meta=META)
    report = r.close(0.05)
    assert report["closed"] and report["abandoned"] == 1
    assert report["by_version"] == [
        {"version": vids["live"], "audits_inline": 0, "shadows_dropped": 0, "audits_abandoned": 1, "shadows_abandoned": 0},
        {"version": vids["shadow"], "audits_inline": 0, "shadows_dropped": 0, "audits_abandoned": 0, "shadows_abandoned": 1},
    ]
    (event,) = _events(led, "sidecar_dropped")
    assert event.startswith("shutdown: 1 off-path jobs not run") and f"audits v{vids['live']}: 1" in event
    row = _row(led, second.request_id)  # pending for good: the gate ignores it
    assert (row["audited"], row["audit_agree"], row["shadow_version"], row["shadow_serve"]) == (1, None, vids["shadow"], None)
    teacher.release.set()
    assert r.sidecar.drain(WAIT_S)  # the running job still finishes
    assert _row(led, first.request_id)["audit_agree"] == 1
    assert led.version_live_quality(vids["live"]) == (1, 1.0)
    third = r.handle(_ask(3), TRIAGE, meta=META)  # after shutdown began, checks run on the request path again
    assert not third.overhead_pending and _row(led, third.request_id)["audit_agree"] == 1
    assert r.sidecar_report()["full"] == 0  # a closed sidecar is not a full one


@pytest.mark.req("FR-12.31")
def test_concurrent_requests_all_finish_their_checks():
    """Scout and the gateway call `handle` from many threads; every job finishes, each row is complete, and the
    sidecar's counts add up."""
    led, vids, r = _cascade(RecordingProvider(A), audit_rate=0.5, workers=4, queue=50, shadow=RecordingProvider(B))
    errors: list[BaseException] = []

    def client(k: int) -> None:
        try:
            for i in range(15):
                r.handle(_ask(f"{k}-{i}"), TRIAGE, meta=META)
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=client, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT_S * 3)
    assert r.sidecar.drain(WAIT_S) and errors == []
    rows = [dict(x) for x in led.db.execute("SELECT * FROM requests")]
    assert len(rows) == 120
    assert not [x for x in rows if x["audited"] == 1 and x["audit_agree"] is None]
    assert not [x for x in rows if x["shadow_version"] is not None and x["shadow_serve"] is None]
    snap = r.sidecar.snapshot()
    assert snap["done"] == snap["submitted"] > 0 and snap["failed"] == 0
    assert snap["submitted"] + sum(v["audits_inline"] for v in r.sidecar_report()["by_version"]) >= sum(
        x["audited"] for x in rows
    )
    assert all(abs(x["cost_usd"] - x["student_cost_usd"] - x["teacher_cost_usd"]) < 1e-12 for x in rows)
    n, _ = led.version_live_quality(vids["live"])
    assert n == sum(x["audited"] for x in rows)


# ---------- configuration ----------
def test_sidecar_settings_defaults_and_bounds(monkeypatch):
    assert Settings(mode="mock").sidecar() == (0, 1000, 10.0)
    assert Settings(mode="live").sidecar() == (4, 1000, 10.0)
    assert Settings(mode="mock", sidecar_workers=2, sidecar_queue=5, sidecar_drain_s=0.0).sidecar() == (2, 5, 0.0)
    for field, value, var in (
        ("sidecar_workers", -1, "RATCHET_SIDECAR_WORKERS"),
        ("sidecar_workers", 65, "RATCHET_SIDECAR_WORKERS"),
        ("sidecar_queue", 0, "RATCHET_SIDECAR_QUEUE"),
        ("sidecar_queue", 100_001, "RATCHET_SIDECAR_QUEUE"),
        ("sidecar_drain_s", -1.0, "RATCHET_SIDECAR_DRAIN_S"),
        ("sidecar_drain_s", float("nan"), "RATCHET_SIDECAR_DRAIN_S"),
    ):
        with pytest.raises(ValueError, match=var):
            Settings(mode="live", **{field: value}).sidecar()
        issues = Settings(mode="mock", **{field: value}).validate({})
        assert any(i.var == var and i.level == "error" for i in issues), (field, value, issues)
    monkeypatch.setenv("RATCHET_SIDECAR_WORKERS", "four")
    _, parsed = load()
    assert [i.var for i in parsed] == ["RATCHET_SIDECAR_WORKERS"]
    with pytest.raises(ConfigError):
        Settings()


def test_mock_mode_and_the_simulator_keep_every_check_on_the_request_path():
    led = Ledger(":memory:")
    r = Ratchet(Settings(mode="mock", teacher_model=OPEN_TEACHER), led, RecordingProvider(A), lambda v: (None, ""))
    assert r.sidecar.inline and not r._offpath(META)
    live = Ratchet(Settings(mode="live", teacher_model=OPEN_TEACHER), led, RecordingProvider(A), lambda v: (None, ""))
    assert live.sidecar.workers == 4 and live._offpath(META) and not live._offpath(None)
    assert live.sidecar.snapshot()["submitted"] == 0 and not live.sidecar._threads  # threads start on first use


# ---------- the gateway ----------
def _live_runtime(gw: Any, tmp_path: Path, teacher: FakeOpenAIServer, **overrides: Any) -> Any:
    """A live Runtime with a student endpoint, every request audited, the teacher and the student behind MockTransport,
    and a live version its unanimous student clears."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / f"live{len(list(tmp_path.iterdir()))}.db"),
        "teacher_base": "https://teacher.test/v1",
        "teacher_api_key": "tf-teacher-api-key",
        "teacher_model": OPEN_TEACHER,
        "student_base": "https://student.test/v1",
        "student_api_key": "sk-student-endpoint-token",
        "samples": 3,
        "audit_rate": 1.0,
        "api_keys": "",
        "allow_unauthenticated": True,
        "api_guard": "guarded",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "public_dashboard": True,  # /ratchet/stats and /metrics without a key
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
        "sidecar_workers": 1,
        "sidecar_drain_s": 5.0,
    }
    rt = gw.Runtime(
        Settings(**{**base, **overrides}),
        transports={"teacher": teacher.transport, "student": FakeOpenAIServer(A).transport},
        sleep=lambda s: None,
    )
    _version(rt.ledger, "live", {"threshold": 0.5}, adapter_uri="adapter-v1")
    return rt


def _gated_teacher() -> tuple[FakeOpenAIServer, threading.Event, threading.Event]:
    """A teacher that answers only once the returned gate is set; the second event is set when a call starts."""
    gate, entered = threading.Event(), threading.Event()

    def answer(body: dict[str, Any]) -> str:
        entered.set()
        assert gate.wait(WAIT_S), "the test never released the teacher"
        return A

    return FakeOpenAIServer(answer), gate, entered


@pytest.mark.req("FR-12.31", "NFR-1", "NFR-3")
def test_the_gateway_answers_first_and_its_shutdown_drains_the_queue(gw_env, tmp_path, monkeypatch, ratchet_log):
    gw = gw_env()
    teacher, gate, _ = _gated_teacher()
    rt = _live_runtime(gw, tmp_path, teacher)
    monkeypatch.setattr(gw, "rt", rt)
    with TestClient(gw.app) as c:
        resp = c.post(CHAT, json={"messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 200
        block = resp.json()["ratchet"]
        assert (block["served_by"], block["overhead_pending"], block["usage_breakdown"]["audit"]) == ("student", True, None)
        row = rt.ledger.request_record(resp.headers["x-request-id"])
        assert (row["audited"], row["audit_agree"]) == (1, None) and row["cost_usd"] == block["cost_usd"]
        stats = c.get("/ratchet/stats").json()["sidecar"]
        assert (stats["workers"], stats["submitted"]) == (1, 1)
        assert 'ratchet_sidecar_jobs{state="queued"}' in c.get("/metrics").text
        threading.Timer(0.2, gate.set).start()  # the teacher answers while the shutdown waits
    row = rt.ledger.request_record(resp.headers["x-request-id"])  # the lifespan drained the queue on the way out
    assert (row["audited"], row["audit_agree"], row["stored"]) == (1, 1, 1) and row["cost_usd"] > block["cost_usd"]
    assert rt.ratchet.sidecar_report()["closed"] and not _events(rt.ledger, "sidecar_dropped")
    # The chat line says the checks were pending; the job's `offpath` line, under the same request id, adds the rest.
    (chat,) = _json_lines(ratchet_log, "chat")
    (job,) = _json_lines(ratchet_log, "offpath")
    assert chat["overhead_pending"] is True and chat["request_id"] == job["request_id"] == resp.headers["x-request-id"]
    assert chat["cost_usd"] + job["cost_usd"] == pytest.approx(row["cost_usd"], rel=1e-12)


@pytest.mark.req("FR-12.31")
def test_a_shutdown_past_the_drain_leaves_rows_pending_and_says_so(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    teacher, gate, entered = _gated_teacher()
    rt = _live_runtime(gw, tmp_path, teacher, sidecar_drain_s=0.0)
    monkeypatch.setattr(gw, "rt", rt)
    with TestClient(gw.app) as c:
        ids = [c.post(CHAT, json={"messages": [{"role": "user", "content": "hi 1"}]}).headers["x-request-id"]]
        assert entered.wait(WAIT_S)  # the only worker is busy with the first audit
        ids.append(c.post(CHAT, json={"messages": [{"role": "user", "content": "hi 2"}]}).headers["x-request-id"])
    rows = [rt.ledger.request_record(i) for i in ids]
    assert [(x["audited"], x["audit_agree"]) for x in rows] == [(1, None), (1, None)]  # one running, one given up
    (event,) = _events(rt.ledger, "sidecar_dropped")
    assert event.startswith("shutdown: 1 off-path jobs not run after 0 s (audits v1: 1)")
    assert rt.ledger.version_live_quality(1) == (0, None)
    gate.set()
    assert rt.ratchet.sidecar.drain(WAIT_S)
    assert rt.ledger.version_live_quality(1) == (1, 1.0)  # the job that was running finished; the other stays pending


def test_mock_gateway_answers_have_no_pending_overhead(gw):
    c = TestClient(gw.app)
    stream = gw.rt.stream.sample(1)
    body = c.post(CHAT, json={"messages": stream.messages()}, headers={"X-Ratchet-Task": stream.task}).json()
    assert "overhead_pending" not in body["ratchet"]
    side = c.get("/ratchet/stats").json()["sidecar"]
    assert (side["workers"], side["queued"], side["by_version"]) == (0, 0, [])


def test_a_demo_reset_stops_the_old_sidecar(gw_env):
    gw = gw_env(RATCHET_SIDECAR_WORKERS="1")
    old = gw.rt.ratchet
    assert old.sidecar.accepting
    gw.rt.reset()
    assert not old.sidecar.accepting and gw.rt.ratchet.sidecar.accepting and gw.rt.ratchet is not old


def test_offpath_job_logs_one_line_without_content(ratchet_log):
    led, _, r = _cascade(RecordingProvider(A), audit_rate=1.0)
    before = metrics.SIDECAR_JOBS.value("done")
    res = r.handle(_ask("secret text"), TRIAGE, meta=META)
    assert r.sidecar.drain(WAIT_S)
    (line,) = _json_lines(ratchet_log, "offpath")
    assert (line["row"], line["audited"], line["audit_agree"], line["stored"]) == (res.request_id, 1, 1, True)
    assert line["cost_usd"] == _row(led, res.request_id)["cost_usd"] - res.cost_usd
    assert all("secret text" not in json.dumps(x) for x in _json_lines(ratchet_log, "offpath"))
    assert metrics.SIDECAR_JOBS.value("done") == before + 1
