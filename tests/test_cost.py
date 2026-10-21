"""Utilization-aware cost accounting (FR-15.1 to FR-15.4, NFR-11, ADR 0023): the per-request split, the compute-time
ledger, the cost report and its recommendation, the endpoints, the CLI, the deploy script's recording, the migration of a
v0.4 ledger and the settings.

Hand-built ledgers run on the synthetic clock (day N starts at N x 86400 s), like the simulator; the live-ledger tests
count days from the first request. Every number asserted is worked out by hand in the test, except the simulation
tests, which assert signs and trends only.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    BASE_MODEL,
    OPEN_TEACHER,
    REJECTED_EVAL,
    FakeClock,
    FakeNebius,
    FakeOpenAIServer,
    RecordingEvaluator,
    RecordingProvider,
    RecordingTrainer,
    _examples,
    _version,
)
from fastapi.testclient import TestClient

import ratchet.gateway as gateway
from ratchet import cost
from ratchet.config import DEFAULT_PRICES, Price, Settings
from ratchet.ledger import DAY_SECONDS, Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate
from ratchet.router import Ratchet

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
TRIAGE = "ticket-triage"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
B = json.dumps({"category": "refund", "priority": "high", "reply": "Your refund is on its way."})
FALLBACK = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
H = 3600.0
D = DAY_SECONDS
ADMIN_KEY = "cost-admin-key-0123456789abcdef"
API_KEY = "cost-client-key-0123456789abcdef"
REPORT_KEYS = {
    "window", "model", "headline", "prices", "requests", "student_served", "teacher_served", "teacher_usd",
    "student_per_token_usd", "hosted_student_usd", "student_served_off_endpoint", "baseline_usd",
    "split_unknown_requests", "split_unknown_usd", "endpoint_hours", "endpoint_usd", "endpoint_usd_per_hour", "idle_hours",
    "idle_share", "idle_granularity", "training_hours", "training_usd", "views", "avoided_teacher_usd_per_student_request",
    "break_even_student_requests_per_gpu_hour", "hosted_fallback_usd_per_request",
    "self_host_beats_hosted_above_requests_per_hour", "observed_student_requests_per_endpoint_hour",
    "utilization_vs_break_even", "recommendation", "explanation", "open_intervals", "warnings", "daily",
    "daily_truncated",
}  # fmt: skip
COST_ENV = {  # pinned so the shell cannot move a gateway test
    "RATCHET_STUDENT_COST_MODEL": "per_token",
    "RATCHET_STUDENT_GPU_USD_PER_HOUR": "1.55",
    "RATCHET_STUDENT_GPUS": "1",
    "RATCHET_TRAIN_GPU_USD_PER_HOUR": "1.55",
    "RATCHET_TRAIN_GPUS": "1",
    "RATCHET_MOCK_ENDPOINT_HOURS": "24",
    "RATCHET_PRICES_VERIFIED": "0",
    "RATCHET_ADMIN_KEYS": "",
    "RATCHET_API_KEYS": "",
    "RATCHET_TEACHER_MODEL": OPEN_TEACHER,
}


# ---------- helpers ----------
def _settings(**overrides: Any) -> Settings:
    """Every value these tests depend on, set explicitly."""
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "fallback_student_model": FALLBACK,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "teacher_reasoning_tokens": 300,
        "student_cost_model": "per_token",
        "student_gpu_usd_per_hour": 2.0,
        "student_gpus": 1,
        "train_gpu_usd_per_hour": 3.0,
        "train_gpus": 1,
        "mock_endpoint_hours": 24.0,
        "prices_verified": False,
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
    }
    return Settings(**{**base, **overrides})


def _request(led: Ledger, day: int, served_by: str, student: float, teacher: float, baseline: float, **extra: Any) -> None:
    """One logged request with its split, as the router writes it (100 prompt and 30 output student tokens unless set)."""
    row = {
        "day": day,
        "task": TRIAGE,
        "served_by": served_by,
        "escalated": int(served_by == "teacher"),
        "cost_usd": student + teacher,
        "baseline_cost_usd": baseline,
        "student_cost_usd": student,
        "teacher_cost_usd": teacher,
        "student_in_tokens": 100,
        "student_out_tokens": 30,
        "route": "student" if served_by == "student" else "confidence",
        **extra,
    }
    led.log_request(**row)


def _interval(led: Ledger, kind: str, start: float, end: float | None, rate: float = 2.0, gpus: int = 1) -> int:
    iid = led.compute_start(kind, rate, gpus, "simulation", ts=start)
    if end is not None:
        led.compute_stop(kind, ts=end, interval_id=iid)
    return iid


def _hand_ledger(rate: float = 2.0, train_rate: float = 3.0) -> Ledger:
    """Two days on the synthetic clock, worked out in `test_report_math_on_a_hand_built_ledger`:

    day 1: a student answer (student $0.002, baseline $0.010), an audited one (student $0.002, audit $0.006, baseline
    $0.006) and an escalation (student $0.002, teacher $0.010, baseline $0.010); the endpoint up 10 h at `rate` ($2/h)
    and a 0.5 h training job at `train_rate` ($3/h). day 2: one pinned request ($0.010) and the endpoint up 4 h with no
    student traffic. Pass the same `rate` to `_settings(student_gpu_usd_per_hour=...)`: break-even uses the rate the
    intervals recorded."""
    led = Ledger(":memory:")
    _request(led, 1, "student", 0.002, 0.0, 0.010)
    _request(led, 1, "student", 0.002, 0.006, 0.006, audited=1, audit_agree=1)
    _request(led, 1, "teacher", 0.002, 0.010, 0.010)
    _request(led, 2, "teacher", 0.0, 0.010, 0.010, route="pinned", escalated=0, student_in_tokens=0, student_out_tokens=0)
    led.day = 2
    _interval(led, "student_endpoint", 1 * D, 1 * D + 10 * H, rate=rate)
    _interval(led, "training_job", 1 * D + 22 * H, 1 * D + 22.5 * H, rate=train_rate)
    _interval(led, "student_endpoint", 2 * D + 8 * H, 2 * D + 12 * H, rate=rate)
    return led


def _row_split(led: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in led.db.execute("SELECT * FROM requests ORDER BY id")]


@pytest.fixture(scope="module")
def five_days() -> Any:
    """Five simulated days of 1,500 tickets on a mock runtime with the endpoint up 24 h a day (about 4 s, once per
    module): canaries, shadows, audits and arbitrations all happen. Read it, never change it."""
    settings = Settings(
        mode="mock",
        mock_warmup_days=0,
        teacher_model=OPEN_TEACHER,
        samples=3,
        audit_rate=0.10,
        confidence_threshold=0.8,
        teacher_reasoning_tokens=300,
        teacher_params="",
        student_params="",
        api_keys="",
        admin_keys="",
        tasks_file="",
        unknown_tasks="distill",
        bom_signing_key="",
        student_cost_model="per_token",
        student_gpu_usd_per_hour=1.55,
        student_gpus=1,
        train_gpu_usd_per_hour=1.55,
        train_gpus=1,
        mock_endpoint_hours=24.0,
        prices_verified=False,
    )
    rt = gateway.Runtime(settings)
    for _ in range(5):
        rt.simulate_day(1500, "tickets")
    return rt


# ---------- FR-15.1: the per-request split ----------
@pytest.mark.req("FR-15.1", "NFR-3")
def test_split_is_exact_on_every_cost_path():
    """The 11 cost paths of the contract test: each row's student part is the student's samples (live and shadow), the
    teacher part every teacher call, `cost_usd` their sum, and `cost_usd` itself unchanged (the same exact values the
    contract test pins). The student tokens are recorded for the hosted-fallback price."""
    s = _settings()
    student, teacher = s.price("student"), s.price(OPEN_TEACHER)
    live = student.cost(30, 7 * s.samples)
    shadow = student.cost(31, 9 * s.samples)
    call = teacher.cost(120, 45)

    def run(*, audit=0.0, threshold=0.5, shadow_says=None, live_down=False, cold=False, task=TRIAGE):
        led = Ledger(":memory:")
        models: dict[int, RecordingProvider] = {}
        if not cold:
            vid = _version(led, "live", {"threshold": threshold})
            models[vid] = RecordingProvider(A, in_tokens=30, out_tokens=7, raise_on={f"student-v{vid}"} if live_down else ())
        if shadow_says is not None:
            models[_version(led, "shadow", {"threshold": 0.5})] = RecordingProvider(shadow_says, in_tokens=31, out_tokens=9)
        t = RecordingProvider(A, in_tokens=120, out_tokens=45)
        r = Ratchet(_settings(audit_rate=audit), led, t, lambda v: (models[v.id], v.adapter_uri), seed=1)
        out = r.handle([{"role": "user", "content": "I was charged twice, order 1"}], task)
        (row,) = _row_split(led)
        return out, row

    cases = [  # (name, run(...) arguments, student part, teacher part, student tokens (in, out))
        ("served", {}, live, 0.0, (30, 21)),
        ("served and audited", {"audit": 1.0}, live, call, (30, 21)),
        ("escalated", {"threshold": 0.99}, live, call, (30, 21)),
        ("student endpoint down", {"live_down": True}, 0.0, call, (0, 0)),
        ("cold start", {"cold": True}, 0.0, call, (0, 0)),
        ("pinned task", {"task": "research-plan"}, 0.0, call, (0, 0)),
        ("shadow agrees", {"shadow_says": A}, live + shadow, 0.0, (61, 48)),
        ("shadow unsure", {"shadow_says": "not json"}, live + shadow, 0.0, (61, 48)),
        ("shadow disagrees: arbitration", {"shadow_says": B}, live + shadow, call, (61, 48)),
        ("audit is the shadow's reference", {"audit": 1.0, "shadow_says": B}, live + shadow, call, (61, 48)),
        ("escalation is the shadow's reference", {"threshold": 0.99, "shadow_says": B}, live + shadow, call, (61, 48)),
    ]
    for name, kwargs, student_part, teacher_part, tokens in cases:
        out, row = run(**kwargs)
        assert row["student_cost_usd"] == pytest.approx(student_part, rel=1e-12, abs=0), name
        assert row["teacher_cost_usd"] == pytest.approx(teacher_part, rel=1e-12, abs=0), name
        assert abs(row["cost_usd"] - (row["student_cost_usd"] + row["teacher_cost_usd"])) <= 1e-12, name
        assert row["cost_usd"] == pytest.approx(student_part + teacher_part, rel=1e-12, abs=0), name
        assert (row["student_in_tokens"], row["student_out_tokens"]) == tokens, name
        assert (out.student_cost_usd, out.teacher_cost_usd, out.cost_usd) == (
            row["student_cost_usd"],
            row["teacher_cost_usd"],
            row["cost_usd"],
        ), name


@pytest.mark.req("FR-15.1", "INV-10")
def test_pinned_and_teacher_only_requests_are_all_teacher_cost():
    led = Ledger(":memory:")
    vid = _version(led, "live", {"threshold": 0.5})
    student, teacher = RecordingProvider(A), RecordingProvider(A, in_tokens=120, out_tokens=45)
    r = Ratchet(_settings(), led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    pinned = r.handle([{"role": "user", "content": "plan a search"}], "research-plan")
    only = r.teacher_only([{"role": "user", "content": "what is in this image?"}], TRIAGE)
    assert vid and student.calls == []  # the student is never sampled on these paths
    for out, row in zip((pinned, only), _row_split(led), strict=True):
        assert row["route"] == "pinned" and row["student_cost_usd"] == 0.0
        assert row["teacher_cost_usd"] == row["cost_usd"] == out.cost_usd == out.teacher_cost_usd > 0
        assert (row["student_in_tokens"], row["student_out_tokens"]) == (0, 0)


@pytest.mark.req("FR-15.1", "FR-15.2")
def test_split_holds_on_every_row_of_simulated_days(five_days):
    """Five simulated days on the mock runtime (canaries, shadows, audits and arbitrations all happen): every request
    row's cost is exactly its two parts, up to float rounding, and every simulated day and trained version left a
    compute interval on the synthetic clock."""
    led = five_days.ledger
    rows = _row_split(led)
    assert len(rows) == 7500 and {r["route"] for r in rows} >= {"student", "cold_start", "confidence"}
    assert any(r["shadow_version"] for r in rows) and any(r["audited"] for r in rows)
    arbitrated = [r for r in rows if r["shadow_version"] and r["served_by"] == "student" and not r["audited"]]
    assert any(r["teacher_cost_usd"] > 0 for r in arbitrated)  # an arbitration is teacher spend on a student answer
    for r in rows:
        assert r["student_cost_usd"] >= 0 and r["teacher_cost_usd"] >= 0
        assert abs(r["cost_usd"] - (r["student_cost_usd"] + r["teacher_cost_usd"])) <= 1e-12, r
        if r["served_by"] == "student":
            assert r["student_in_tokens"] > 0 and r["student_out_tokens"] > 0
    endpoint = led.compute_intervals("student_endpoint")
    assert [(iv["start_ts"], iv["end_ts"]) for iv in endpoint] == [(d * D, d * D + 24 * H) for d in range(1, 6)]
    jobs = led.compute_intervals("training_job")
    trained = [int(e["detail"].split()[0][1:]) for e in led.events() if e["kind"] == "trained"]
    assert [iv["version"] for iv in jobs] == trained and all(iv["source"] == "simulation" for iv in jobs)
    for iv in jobs:
        v = led.version(iv["version"])
        assert v is not None and iv["end_ts"] - iv["start_ts"] == pytest.approx(600 + 0.05 * v.train_examples)
        assert iv["start_ts"] == led.day_start(v.day) + 22 * H


# ---------- FR-15.2: the compute-time ledger ----------
@pytest.mark.req("FR-15.2")
def test_overlapping_and_nested_intervals_are_not_double_counted():
    rows = [
        {"start_ts": 0.0, "end_ts": 10 * H, "usd_per_hour": 2.0, "gpus": 1},
        {"start_ts": 2 * H, "end_ts": 4 * H, "usd_per_hour": 2.0, "gpus": 1},  # nested: adds nothing
        {"start_ts": 8 * H, "end_ts": 12 * H, "usd_per_hour": 2.0, "gpus": 1},  # overlaps the end: 2 more hours
        {"start_ts": 20 * H, "end_ts": 21 * H, "usd_per_hour": 2.0, "gpus": 1},  # apart: 1 more hour
    ]
    segs = cost.segments(rows, now=0.0)
    assert [(s.start / H, s.end / H) for s in segs] == [(0, 12), (20, 21)]
    assert cost.usage(segs) == pytest.approx((13.0, 26.0))
    # A more expensive record over part of the time bills that part at its rate, once: 6 h x $2 + 2 h x $5 + 4 h x $2.
    pricier = [*rows[:1], {"start_ts": 6 * H, "end_ts": 8 * H, "usd_per_hour": 2.5, "gpus": 2}, rows[2]]
    assert cost.usage(cost.segments(pricier, now=0.0)) == pytest.approx((12.0, 12 + 10 + 8))
    # Clipped to a window; empty and inverted records count nothing.
    assert cost.usage(cost.segments(rows, now=0.0, lo=9 * H, hi=20.5 * H)) == pytest.approx((3.5, 7.0))
    assert cost.segments([{"start_ts": 5.0, "end_ts": 5.0, "usd_per_hour": 9, "gpus": 1}], now=0.0) == []
    # The split by day windows adds up to the whole.
    windows = [(0.0, 6 * H), (6 * H, 11 * H), (11 * H, 24 * H)]
    parts = cost.split(segs, windows)
    assert [x for part in parts for x in part] == pytest.approx([6.0, 12.0, 5.0, 10.0, 2.0, 4.0])


@pytest.mark.req("FR-15.2")
def test_open_interval_counts_to_now_and_to_the_window():
    led = Ledger(":memory:")
    led.day = 3
    iid = led.compute_start("student_endpoint", 2.0, 1, "cli", ts=3 * D + 2 * H)
    (iv,) = led.compute_intervals()
    assert (iv["id"], iv["end_ts"], iv["source"]) == (iid, None, "cli")
    assert cost.usage(cost.segments([iv], now=3 * D + 5 * H)) == pytest.approx((3.0, 6.0))
    assert cost.usage(cost.segments([iv], now=3 * D + 5 * H, hi=3 * D + 4 * H)) == pytest.approx((2.0, 4.0))
    assert cost.segments([iv], now=3 * D) == []  # a clock behind the start counts nothing, never a negative span
    report = cost.cost_report(led, _settings(), now=3 * D + 5 * H)
    assert report["endpoint_hours"] == pytest.approx(3.0) and report["endpoint_usd"] == pytest.approx(6.0)
    assert report["open_intervals"] == [{"id": iid, "kind": "student_endpoint", "start_ts": 3 * D + 2 * H, "hours": 3.0}]
    assert led.compute_intervals(since=3 * D + 4 * H) and not led.compute_intervals(until=3 * D + 2 * H)


@pytest.mark.req("FR-15.2")
def test_endpoint_start_is_idempotent_and_stop_closes_it(tmp_path):
    db = str(tmp_path / "live.db")
    led, other = Ledger(db, auto_day=True), Ledger(db, auto_day=True)  # the gateway and the CLI on one file
    first = led.compute_start("student_endpoint", 1.55, 1, "api", ts=1000.0)
    assert other.compute_start("student_endpoint", 9.0, 4, "cli", ts=2000.0) == first  # already up: unchanged
    assert led.compute_start("student_endpoint", 1.55, 1, "api") == first
    (iv,) = other.compute_intervals("student_endpoint")
    assert (iv["start_ts"], iv["usd_per_hour"], iv["gpus"], iv["source"]) == (1000.0, 1.55, 1, "api")
    with pytest.raises(ValueError, match="after the end given"):
        other.compute_stop("student_endpoint", ts=999.0)  # before it started: nothing closed
    assert led.compute_intervals("student_endpoint")[0]["end_ts"] is None
    assert other.compute_stop("student_endpoint", ts=5000.0) == first
    assert led.compute_stop("student_endpoint", ts=6000.0) is None  # already down
    assert [(iv["start_ts"], iv["end_ts"]) for iv in led.compute_intervals()] == [(1000.0, 5000.0)]
    second = led.compute_start("student_endpoint", 1.55, 1, "cli", ts=7000.0)
    assert second != first

    # Jobs get an interval per start; a stop by id closes that one only.
    a = led.compute_start("training_job", 3.0, 2, "night", ts=100.0)
    b = led.compute_start("training_job", 3.0, 2, "night", ts=200.0)
    assert a != b and led.compute_stop("training_job", ts=300.0, interval_id=b, version=4, note="adapter v4") == b
    jobs = {iv["id"]: iv for iv in led.compute_intervals("training_job")}
    assert jobs[a]["end_ts"] is None and (jobs[b]["end_ts"], jobs[b]["version"], jobs[b]["note"]) == (300.0, 4, "adapter v4")


@pytest.mark.req("FR-15.2", "NFR-6")
def test_concurrent_endpoint_starts_keep_one_open_interval(tmp_path):
    """Eight connections to one file, as the gateway, the CLI and the deploy script are: the ledger's lock cannot
    serialise them, only the write transaction `compute_start` opens before it looks for an open interval."""
    db = str(tmp_path / "live.db")
    ledgers = [Ledger(db, auto_day=True) for _ in range(8)]
    ids: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(ledgers))

    def up(led: Ledger) -> None:
        barrier.wait()
        try:
            ids.append(led.compute_start("student_endpoint", 1.55, 1, "api", ts=10.0))
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=up, args=(led,)) for led in ledgers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(ids) == 8 and len(set(ids)) == 1
    assert len(Ledger(db, auto_day=True).compute_intervals()) == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda led: led.compute_start("gpu_party", 1.0, 1, "cli"),
        lambda led: led.compute_start("student_endpoint", -1.0, 1, "cli"),
        lambda led: led.compute_start("student_endpoint", float("nan"), 1, "cli"),
        lambda led: led.compute_start("student_endpoint", 1.0, True, "cli"),
        lambda led: led.compute_start("student_endpoint", 1.0, -2, "cli"),
        lambda led: led.compute_start("student_endpoint", 1.0, 1, "cli", ts=float("inf")),
        lambda led: led.compute_start("student_endpoint", 1000.5, 1, "cli"),  # above MAX_USD_PER_GPU_HOUR
        lambda led: led.compute_start("student_endpoint", 10**400, 1, "cli"),  # an int no float can hold
        lambda led: led.compute_start("student_endpoint", 1.0, 1025, "cli"),  # above MAX_GPUS
        lambda led: led.compute_stop("gpu_party"),
        lambda led: led.compute_record("student_endpoint", 10.0, 5.0, 1.0, 1, "simulation"),  # ends before it starts
    ],
)
@pytest.mark.req("FR-15.2")
def test_bad_compute_records_are_refused(call):
    led = Ledger(":memory:")
    with pytest.raises(ValueError):
        call(led)
    assert led.compute_intervals() == []


@pytest.mark.req("FR-15.2", "FR-4.2")
def test_training_interval_is_closed_when_train_raises():
    class Broken(RecordingTrainer):
        def train(self, train_rows, base_model, name):
            clock.advance(1800)
            raise RuntimeError("the GPU job failed")

    clock = FakeClock()
    led = Ledger(":memory:")
    _examples(led, train=60, golden=8)
    rate = _settings(train_gpu_usd_per_hour=3.0, train_gpus=2).cost_policy().train
    with pytest.raises(RuntimeError, match="the GPU job failed"):
        run_night(led, Broken(), RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led), BASE_MODEL, train_gpu=rate, clock=clock)
    (job,) = led.compute_intervals("training_job")
    assert (job["start_ts"], job["end_ts"], job["usd_per_hour"], job["gpus"]) == (1_800_000_000.0, 1_800_001_800.0, 3.0, 2)
    assert (job["source"], job["version"], job["note"]) == ("night", None, "adapter student-v1: training failed")
    assert led.versions() == []

    class Slow(RecordingTrainer):
        def train(self, train_rows, base_model, name):
            clock.advance(3600)
            return super().train(train_rows, base_model, name)

    rep = run_night(led, Slow(), RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led), BASE_MODEL, train_gpu=rate, clock=clock)
    assert rep.trained and rep.version is not None
    job = led.compute_intervals("training_job")[-1]
    assert (job["end_ts"] - job["start_ts"], job["version"], job["note"]) == (3600.0, rep.version, "adapter student-v1")
    # Without a GPU rate (the simulator, the mock demo) Night School records nothing itself.
    _examples(led, train=60, prefix="more")
    run_night(led, RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL), ReleaseGate(led), BASE_MODEL)
    assert len(led.compute_intervals("training_job")) == 2


# ---------- FR-15.3 and FR-15.4: the report ----------
@pytest.mark.req("FR-15.3", "FR-15.4", "NFR-11")
def test_report_math_on_a_hand_built_ledger():
    led = _hand_ledger()
    r = cost.cost_report(led, _settings())
    assert set(r) == REPORT_KEYS
    assert (r["requests"], r["student_served"], r["teacher_served"]) == (4, 2, 2)
    assert r["teacher_usd"] == pytest.approx(0.026) and r["student_per_token_usd"] == pytest.approx(0.006)
    assert r["baseline_usd"] == pytest.approx(0.036) and r["split_unknown_requests"] == 0
    assert (r["endpoint_hours"], r["endpoint_usd"], r["endpoint_usd_per_hour"]) == pytest.approx((14.0, 28.0, 2.0))
    assert (r["training_hours"], r["training_usd"]) == pytest.approx((0.5, 1.5))
    assert (r["idle_hours"], r["idle_share"]) == pytest.approx((4.0, 4 / 14))  # day 2: up, no student traffic
    assert r["idle_granularity"] == "day"  # the synthetic clock: whole days
    assert (r["hosted_student_usd"], r["student_served_off_endpoint"]) == (0.0, 0)  # every such day had uptime

    tok, gpu = r["views"]["per_token"], r["views"]["gpu_hours"]
    assert tok == pytest.approx({"total_usd": 0.032, "savings_usd": 0.004, "savings_pct": 0.004 / 0.036})
    assert gpu["total_usd"] == pytest.approx(0.026 + 28 + 1.5)
    assert gpu["savings_usd"] == pytest.approx(0.036 - 29.526) and gpu["savings_pct"] < -800  # negative, never clamped
    assert r["headline"] == {"model": "per_token", **tok}

    avoided = ((0.010 - 0.0) + (0.006 - 0.006)) / 2
    assert r["avoided_teacher_usd_per_student_request"] == pytest.approx(avoided)
    assert r["break_even_student_requests_per_gpu_hour"] == pytest.approx(2.0 / avoided)
    hosted = DEFAULT_PRICES[FALLBACK].cost(300, 90) / 2  # all three requests' student tokens, per student answer
    assert r["hosted_fallback_usd_per_request"] == pytest.approx(hosted)
    assert r["self_host_beats_hosted_above_requests_per_hour"] == pytest.approx(2.0 / hosted)
    assert r["observed_student_requests_per_endpoint_hour"] == pytest.approx(2 / 14)
    assert r["utilization_vs_break_even"] == pytest.approx((2 / 14) / (2.0 / avoided))
    assert r["recommendation"] == "hosted_fallback"
    assert r["explanation"].startswith("Self-hosting does not pay at this volume.")
    assert "below the break-even of 400" in r["explanation"] and "unverified list prices" in r["explanation"]
    assert cost.CROSSOVER_CAVEAT in r["explanation"]
    assert r["prices"]["verified"] is False and r["prices"]["student_endpoint_usd_per_hour"] == 2.0

    day1, day2 = r["daily"]
    assert (day1["day"], day1["requests"], day1["student_served"]) == (1, 3, 2)
    assert (day1["endpoint_hours"], day1["endpoint_usd"], day1["training_usd"]) == pytest.approx((10.0, 20.0, 1.5))
    assert day1["savings_pct_per_token"] == pytest.approx((0.026 - 0.022) / 0.026)
    assert day1["savings_pct_gpu_hours"] == pytest.approx((0.026 - (0.016 + 20 + 1.5)) / 0.026)
    assert (day2["day"], day2["endpoint_hours"], day2["savings_pct_per_token"]) == pytest.approx((2, 4.0, 0.0))
    assert not r["daily_truncated"] and r["warnings"] == [] and r["open_intervals"] == []

    # The headline follows RATCHET_STUDENT_COST_MODEL; nothing else does.
    g = cost.cost_report(led, _settings(student_cost_model="gpu_hours"))
    assert g["headline"] == {"model": "gpu_hours", **gpu} and g["views"] == r["views"]
    # One day only: the window's requests and hours.
    one = cost.cost_report(led, _settings(), days=1)
    assert (one["window"]["since_day"], one["window"]["until_day"], one["requests"]) == (2, 2, 1)
    assert (one["endpoint_hours"], one["training_hours"], one["recommendation"]) == (4.0, 0.0, "scale_to_zero")
    assert cost.cost_report(led, _settings(), since=1, until=1)["endpoint_hours"] == pytest.approx(10.0)
    with pytest.raises(ValueError):
        cost.cost_report(led, _settings(), since=3, until=1)
    with pytest.raises(ValueError):
        cost.cost_report(led, _settings(), days=0)


@pytest.mark.req("FR-15.4")
def test_recommendation_for_each_branch():
    led = _hand_ledger()
    # self_host: a cheap endpoint (and cheap training) against a pricey hosted fallback, so the observed volume clears
    # both bars and the window saves money by GPU-hours. Break-even 0.1/h, crossover about 0.026/h, observed 1/7.
    pricey = {**DEFAULT_PRICES, FALLBACK: Price(100.0, 100.0)}
    cheap = _settings(student_gpu_usd_per_hour=0.0005, prices=pricey)
    r = cost.cost_report(_hand_ledger(rate=0.0005, train_rate=0.0001), cheap)
    assert r["observed_student_requests_per_endpoint_hour"] >= r["break_even_student_requests_per_gpu_hour"]
    assert r["observed_student_requests_per_endpoint_hour"] >= r["self_host_beats_hosted_above_requests_per_hour"]
    assert r["views"]["gpu_hours"]["savings_usd"] > 0
    assert r["recommendation"] == "self_host" and r["explanation"].startswith("Self-hosting pays.")

    # hosted_fallback: the hand ledger as it is (below break-even and below the hosted crossover).
    assert cost.cost_report(led, _settings())["recommendation"] == "hosted_fallback"

    # scale_to_zero: the endpoint stays up 20 h more on a day nobody reached the student, so 24 of 34 h are idle.
    _interval(led, "student_endpoint", 3 * D, 3 * D + 20 * H)
    r = cost.cost_report(led, _settings(), now=3 * D + 21 * H)
    assert r["idle_share"] == pytest.approx(24 / 34) and r["recommendation"] == "scale_to_zero"
    assert "Scale the endpoint to zero" in r["explanation"] and "on days when no request reached" in r["explanation"]
    # Below break-even, but above the crossover of a hosted fallback that costs more per answer than the teacher spend it
    # avoids: neither pays, so the endpoint has to run fewer hours. Break-even 0.4/h, crossover about 0.1/h, observed 1/7.
    between = _settings(student_gpu_usd_per_hour=0.002, prices=pricey)
    r = cost.cost_report(_hand_ledger(rate=0.002), between)
    assert r["self_host_beats_hosted_above_requests_per_hour"] <= r["observed_student_requests_per_endpoint_hour"]
    assert r["observed_student_requests_per_endpoint_hour"] < r["break_even_student_requests_per_gpu_hour"]
    assert r["recommendation"] == "scale_to_zero" and "fewer hours" in r["explanation"]

    # insufficient_data: no endpoint uptime recorded.
    bare = Ledger(":memory:")
    _request(bare, 1, "student", 0.002, 0.0, 0.010)
    bare.day = 1
    r = cost.cost_report(bare, _settings())
    assert (r["endpoint_hours"], r["observed_student_requests_per_endpoint_hour"]) == (0.0, None)
    assert r["endpoint_usd_per_hour"] == 2.0  # no uptime recorded: the configured rate
    assert r["recommendation"] == "insufficient_data" and "No student endpoint uptime" in r["explanation"]
    # insufficient_data: uptime and student traffic, but the student never served, so the avoided spend is unknown.
    unsure = Ledger(":memory:")
    _request(unsure, 1, "teacher", 0.002, 0.010, 0.010)
    unsure.day = 1
    _interval(unsure, "student_endpoint", 1 * D, 1 * D + 5 * H)
    r = cost.cost_report(unsure, _settings())
    assert (r["idle_share"], r["avoided_teacher_usd_per_student_request"], r["recommendation"]) == (
        0.0,
        None,
        "insufficient_data",
    )
    assert r["break_even_student_requests_per_gpu_hour"] is None and "served no requests" in r["explanation"]
    # An empty ledger: zeros and nulls, never a division by zero or a NaN.
    empty = cost.cost_report(Ledger(":memory:"), _settings())
    assert empty["recommendation"] == "insufficient_data" and empty["daily"] == []
    assert empty["views"]["gpu_hours"] == {"total_usd": 0.0, "savings_usd": 0.0, "savings_pct": None}
    json.dumps(empty, allow_nan=False)
    assert set(cost.RECOMMENDATIONS) == {"self_host", "hosted_fallback", "scale_to_zero", "insufficient_data"}


def _volume_ledger(avoided: float, idle_day_hours: float = 0.0) -> Ledger:
    """20 student answers on day 1, each avoiding `avoided` of teacher spend (baseline `avoided`, no teacher call), with
    the endpoint up 2 h at $0.50/h that day; optionally up `idle_day_hours` more on day 2, when nobody reached it. The
    hosted fallback (the Nano price for 100 prompt and 30 output tokens) costs $0.0000132 an answer, so its crossover is
    about 37,879 an hour: far above the observed volume."""
    led = Ledger(":memory:")
    for _ in range(20):
        _request(led, 1, "student", 0.00002, 0.0, avoided)
    led.day = 2 if idle_day_hours else 1
    _interval(led, "student_endpoint", 1 * D, 1 * D + 2 * H, rate=0.5)
    if idle_day_hours:
        _interval(led, "student_endpoint", 2 * D, 2 * D + idle_day_hours * H, rate=0.5)
    return led


@pytest.mark.req("FR-15.4", "NFR-11")
def test_explanation_between_break_even_and_the_hosted_crossover():
    """Above break-even but below the hosted fallback's crossover (where about 14,000 student answers an hour, the
    '10M requests a month' customer, sits at list prices): the endpoint beats the teacher, so GPU-hour savings are
    positive, and the hosted fallback is still cheaper per answer. The explanation says both, never 'does not pay' or
    'below the break-even', and states that the crossover assumes the untuned fallback serves as many requests."""
    r = cost.cost_report(_volume_ledger(avoided=0.10), _settings(student_gpu_usd_per_hour=0.5))
    be, cross = r["break_even_student_requests_per_gpu_hour"], r["self_host_beats_hosted_above_requests_per_hour"]
    assert (be, r["observed_student_requests_per_endpoint_hour"]) == pytest.approx((5.0, 10.0))  # $0.50/h / $0.10
    assert cross == pytest.approx(0.5 / DEFAULT_PRICES[FALLBACK].cost(100, 30))
    assert r["views"]["gpu_hours"]["savings_usd"] == pytest.approx(2.0 - 0.0 - 1.0)  # baseline - teacher - endpoint
    assert r["recommendation"] == "hosted_fallback"
    text = r["explanation"]
    assert text.startswith("Self-hosting beats the teacher, but the hosted fallback would cost less per answer.")
    assert "at or above the break-even of 5.0" in text and "below 37,879 an hour" in text
    assert "Savings by GPU-hours paid: $1.00 (50%)" in text and cost.CROSSOVER_CAVEAT in text
    assert "does not pay" not in text and "below the break-even" not in text

    # The same volume on an endpoint idle most of its hours: scale to zero, still above break-even, below the crossover.
    r = cost.cost_report(_volume_ledger(avoided=0.30, idle_day_hours=3.0), _settings(student_gpu_usd_per_hour=0.5))
    assert (r["idle_share"], r["observed_student_requests_per_endpoint_hour"]) == pytest.approx((0.6, 4.0))
    assert r["break_even_student_requests_per_gpu_hour"] == pytest.approx(0.5 / 0.30)
    assert r["recommendation"] == "scale_to_zero"
    assert "at or above the break-even of 1.7 but below the hosted fallback's crossover" in r["explanation"]
    assert "below the break-even" not in r["explanation"]


@pytest.mark.req("FR-15.4", "NFR-11")
def test_break_even_uses_the_recorded_rate_and_never_says_pays_at_a_loss():
    """Break-even and the crossover use the endpoint's price per hour as recorded (2 GPUs by `--gpus 2`, while
    RATCHET_STUDENT_GPUS says 1), the same rate as the GPU-hour view; and an endpoint that covers its hours while
    training leaves the window at a loss is kept without the words 'Self-hosting pays'."""
    led = _volume_ledger(avoided=0.10)
    led.compute_record("student_endpoint", 1 * D + 3 * H, 1 * D + 4 * H, 0.5, 2, "cli")  # $1/h for an hour, no traffic
    r = cost.cost_report(led, _settings(student_gpu_usd_per_hour=0.5, student_gpus=1))
    assert (r["endpoint_hours"], r["endpoint_usd"], r["endpoint_usd_per_hour"]) == pytest.approx((3.0, 2.0, 2.0 / 3))
    assert r["break_even_student_requests_per_gpu_hour"] == pytest.approx((2.0 / 3) / 0.10)
    assert "$0.67 per endpoint hour as recorded" in r["explanation"]

    # Covers its hours (break-even 5/h, observed 10/h, above the crossover of about 3.8/h of a pricey hosted fallback),
    # but a $5 training job leaves the window at a loss.
    pricey = {**DEFAULT_PRICES, FALLBACK: Price(1000.0, 1000.0)}
    led = _volume_ledger(avoided=0.10)
    _interval(led, "training_job", 1 * D + 22 * H, 1 * D + 23 * H, rate=5.0)
    r = cost.cost_report(led, _settings(student_gpu_usd_per_hour=0.5, prices=pricey))
    assert r["recommendation"] == "self_host" and r["views"]["gpu_hours"]["savings_usd"] == pytest.approx(2.0 - 1.0 - 5.0)
    assert r["explanation"].startswith("Keep the endpoint; it covers its hours but not yet the whole bill.")
    assert "training jobs ($5.00)" in r["explanation"] and "Self-hosting pays" not in r["explanation"]


@pytest.mark.req("FR-15.3")
def test_live_ledger_days_count_from_the_first_request(tmp_path):
    """A live ledger counts days from its first request: uptime before it is day 0, and a window of the last day takes
    only that day's hours. An open interval runs to now."""
    t0 = 1_800_000_000.0
    led = Ledger(str(tmp_path / "live.db"), auto_day=True)
    _request(led, 1, "student", 0.002, 0.0, 0.010, ts=t0)
    _request(led, 3, "student", 0.002, 0.0, 0.010, ts=t0 + 2 * D + 60)
    assert (led.day_start(1), led.day_of(t0 - 1), led.day_of(t0 + 2 * D + 60)) == (t0, 0, 3)
    led.compute_start("student_endpoint", 2.0, 1, "cli", ts=t0 - H)  # up an hour before the first request
    now = t0 + 2 * D + 2 * H
    r = cost.cost_report(led, _settings(), now=now)
    assert r["endpoint_hours"] == pytest.approx(51.0) and r["open_intervals"][0]["hours"] == pytest.approx(51.0)
    assert [(d["day"], round(d["endpoint_hours"], 9)) for d in r["daily"]] == [(0, 1.0), (1, 24.0), (2, 24.0), (3, 2.0)]
    # Idle time in hours on a live ledger: hours 0 and 48 had a request, the other 49 of the 51 hours none.
    assert (r["idle_granularity"], r["idle_hours"], r["recommendation"]) == ("hour", pytest.approx(49.0), "scale_to_zero")
    assert "in hours when no request reached the student" in r["explanation"]
    last = cost.cost_report(led, _settings(), now=now, days=1)
    assert (last["window"]["since_day"], last["endpoint_hours"], last["requests"]) == (3, pytest.approx(2.0), 1)
    # A ledger file reopened later counts the same days.
    again = Ledger(str(tmp_path / "live.db"), auto_day=True)
    assert cost.cost_report(again, _settings(), now=now)["daily"] == r["daily"]


@pytest.mark.req("FR-15.3", "FR-15.4")
def test_live_ledger_counts_observed_volume_and_idle_time_by_hour(tmp_path):
    """On a live ledger requests and uptime share the wall clock, so the report works in hours: an endpoint up only for
    the first hour of the day is credited with that hour's answers, not the whole day's (the RUNBOOK's advice to run it
    only while traffic arrives), the later answers are the hosted fallback's (priced per token, with a warning), and the
    daily rows still add up to the totals."""
    t0 = 1_800_000_000.0
    led = Ledger(str(tmp_path / "live.db"), auto_day=True)
    _request(led, 1, "student", 0.002, 0.0, 0.010, ts=t0)
    _request(led, 1, "student", 0.002, 0.0, 0.010, ts=t0 + 600)
    for i in range(3):  # five hours later, with the endpoint down
        _request(led, 1, "student", 0.003, 0.0, 0.010, ts=t0 + 5 * H + i)
    led.compute_start("student_endpoint", 2.0, 1, "cli", ts=t0)
    led.compute_stop("student_endpoint", ts=t0 + H)
    r = cost.cost_report(led, _settings(), now=t0 + 6 * H)
    assert (r["idle_granularity"], r["endpoint_hours"], r["idle_hours"]) == ("hour", pytest.approx(1.0), 0.0)
    assert r["observed_student_requests_per_endpoint_hour"] == pytest.approx(2.0)  # not 5, as whole days would say
    assert (r["student_served_off_endpoint"], r["hosted_student_usd"]) == (3, pytest.approx(0.009))
    assert any("3 student answers fell in hours with no recorded endpoint uptime" in w for w in r["warnings"])
    assert r["views"]["gpu_hours"]["total_usd"] == pytest.approx(2.0 + 0.009)
    (day,) = r["daily"]
    assert day["savings_pct_gpu_hours"] == pytest.approx(r["views"]["gpu_hours"]["savings_pct"])
    # A 24/7 endpoint with one request a day is idle 23 hours in 24, so it should scale to zero.
    led.compute_start("student_endpoint", 2.0, 1, "cli", ts=t0 + 6 * H)
    _request(led, 2, "student", 0.002, 0.0, 0.010, ts=t0 + D + 60)
    r = cost.cost_report(led, _settings(), now=t0 + 6 * H + D)
    assert r["idle_hours"] == pytest.approx(r["endpoint_hours"] - 2.0) and r["idle_share"] > 0.9
    assert r["recommendation"] == "scale_to_zero"


@pytest.mark.req("FR-15.3")
def test_last_days_before_the_first_live_request_keep_recorded_uptime(tmp_path):
    """Before a live ledger's first request, day 1 starts at the earliest recorded interval (never after now), so a
    report of the last day still holds the hours the endpoint has run; once a request arrives, days count from it."""
    now = 1_800_000_000.0
    led = Ledger(str(tmp_path / "live.db"), auto_day=True)
    assert cost.cost_report(led, _settings(), now=now, days=1)["endpoint_hours"] == 0.0
    led.compute_start("student_endpoint", 2.0, 1, "deploy", ts=now - 5 * H)  # up 5 h, no traffic yet
    for days in (None, 1, 7):
        r = cost.cost_report(led, _settings(), now=now, days=days)
        assert (r["endpoint_hours"], r["idle_share"], r["recommendation"]) == (pytest.approx(5.0), 1.0, "scale_to_zero"), days
    assert led.day_start(1, now) == now - 5 * H and led.day_of(now, now) == 1
    _request(led, 1, "student", 0.002, 0.0, 0.010, ts=now)
    assert led.day_start(1, now) == now  # the first request starts day 1, as auto_day always counted


@pytest.mark.req("FR-15.2", "INV-8")
def test_simulated_day_leaves_an_endpoint_opened_through_the_api_alone():
    """The mock demo's POST /ratchet/cost/endpoint does not take the demo lock, so a visitor can mark the endpoint up
    while a simulated day runs. The day's synthetic interval is written as a closed record of its own: the visitor's
    interval stays open (never closed by the simulator, never 'started after the end given'), and the report merges
    the two."""
    led = Ledger(":memory:")
    policy = _settings(mock_endpoint_hours=8.0).cost_policy()
    led.day = 3
    api = led.compute_start("student_endpoint", 1.55, 1, "api", ts=3 * D + 10 * H)  # 10:00, after the 8 h day ends
    cost.record_simulated_day(led, policy, 3, None)
    rows = {iv["id"]: iv for iv in led.compute_intervals("student_endpoint")}
    assert rows[api]["end_ts"] is None and len(rows) == 2
    (sim,) = [iv for iv in rows.values() if iv["source"] == "simulation"]
    assert (sim["start_ts"], sim["end_ts"]) == (3 * D, 3 * D + 8 * H)
    r = cost.cost_report(led, _settings(), now=3 * D + 12 * H)
    assert r["endpoint_hours"] == pytest.approx(8.0 + 2.0)
    # An API interval opened earlier in the day overlaps the simulated one and is counted once.
    early = Ledger(":memory:")
    early.day = 3
    early.compute_start("student_endpoint", 2.0, 1, "api", ts=3 * D + 2 * H)
    cost.record_simulated_day(early, policy, 3, None)
    r = cost.cost_report(early, _settings(), now=3 * D + 12 * H)
    assert r["endpoint_hours"] == pytest.approx(12.0) and r["open_intervals"][0]["kind"] == "student_endpoint"


@pytest.mark.req("FR-15.3", "NFR-11")
def test_student_answers_without_recorded_uptime_are_priced_per_token():
    """A day with student answers but no recorded endpoint uptime (the hosted fallback served, or someone forgot to
    record the endpoint): the GPU-hour view prices that day's student spend per token rather than dropping it, the
    observed volume leaves those answers out, and a warning says so."""
    led = _hand_ledger()
    _request(led, 3, "student", 0.003, 0.0, 0.010)
    led.day = 3
    r = cost.cost_report(led, _settings(), now=3 * D + H)
    assert (r["hosted_student_usd"], r["student_served_off_endpoint"]) == (pytest.approx(0.003), 1)
    assert r["views"]["gpu_hours"]["total_usd"] == pytest.approx(0.026 + 28 + 1.5 + 0.003)
    assert r["observed_student_requests_per_endpoint_hour"] == pytest.approx(2 / 14)  # day 3's answer is not the endpoint's
    assert any("1 student answers fell on days with no recorded endpoint uptime" in w for w in r["warnings"])
    (day3,) = [d for d in r["daily"] if d["day"] == 3]
    assert day3["endpoint_hours"] == 0.0 and day3["savings_pct_gpu_hours"] == pytest.approx((0.010 - 0.003) / 0.010)


@pytest.mark.req("FR-15.3")
def test_report_warnings_and_daily_cap(monkeypatch):
    led = _hand_ledger()
    stale = led.compute_start("training_job", 3.0, 1, "night", ts=2 * D)
    r = cost.cost_report(led, _settings(), now=2 * D + 30 * H)
    assert any(f"training_job interval {stale} has been open for 30.0 h" in w for w in r["warnings"])
    assert "close training_job" in r["warnings"][0]
    monkeypatch.setattr(cost, "MAX_DAILY", 1)
    r = cost.cost_report(led, _settings(), now=2 * D + 30 * H)
    assert r["daily_truncated"] and [d["day"] for d in r["daily"]] == [3]
    assert r["requests"] == 4  # the totals still cover every day


# ---------- FR-15.3: the mock demo and the simulator ----------
@pytest.mark.req("FR-15.3", "FR-15.4", "NFR-11")
def test_mock_demo_with_a_24h_endpoint_does_not_pay(five_days, monkeypatch):
    """Five simulated days of 1,500 requests with the endpoint up 24 h a day: per token the student saves money, by
    GPU-hours paid it loses money, and the report says not to self-host (simulation numbers: signs only)."""
    monkeypatch.setattr(gateway, "rt", five_days)
    c = TestClient(gateway.app)
    r = c.get("/ratchet/cost").json()
    assert set(r) == REPORT_KEYS and r["requests"] == 7500
    assert r["endpoint_hours"] == pytest.approx(120.0) and r["training_hours"] > 0
    assert r["views"]["per_token"]["savings_usd"] > 0 > r["views"]["gpu_hours"]["savings_usd"]
    assert r["recommendation"] != "self_host" and r["recommendation"] in cost.RECOMMENDATIONS
    assert r["break_even_student_requests_per_gpu_hour"] > r["observed_student_requests_per_endpoint_hour"] > 0
    stats = c.get("/ratchet/stats").json()
    assert stats["cost"] == {
        "model": "per_token",
        "endpoint_hours": r["endpoint_hours"],
        "gpu_hours_savings_pct": r["views"]["gpu_hours"]["savings_pct"],
        "break_even_student_requests_per_gpu_hour": r["break_even_student_requests_per_gpu_hour"],
        "recommendation": r["recommendation"],
    }
    assert stats["totals"]["cost_model"] == "per_token"
    assert stats["totals"]["cost_usd"] == pytest.approx(r["views"]["per_token"]["total_usd"])
    one = c.get("/ratchet/cost", params={"days": 1}).json()
    assert (one["window"]["since_day"], one["requests"], one["endpoint_hours"]) == (5, 1500, 24.0)
    for bad in ("0", "-3", "36501"):
        assert c.get("/ratchet/cost", params={"days": bad}).status_code == 400, bad
    assert c.get("/ratchet/cost", params={"days": "abc"}).status_code == 422


@pytest.mark.req("FR-15.2", "FR-9.4")
def test_simulated_endpoint_hours_follow_the_setting(gw_env):
    gw = gw_env(**{**COST_ENV, "RATCHET_MOCK_ENDPOINT_HOURS": "8"})
    gw.rt.simulate_day(300, "tickets")
    (iv,) = gw.rt.ledger.compute_intervals("student_endpoint")
    assert (iv["start_ts"], iv["end_ts"], iv["source"]) == (D, D + 8 * H, "simulation")
    assert gw.rt.ledger.events()[-1]["kind"] != "endpoint"  # simulated compute writes no events
    gw = gw_env(**{**COST_ENV, "RATCHET_MOCK_ENDPOINT_HOURS": "0"})  # no endpoint: the hosted fallback
    gw.rt.simulate_day(300, "tickets")
    assert gw.rt.ledger.compute_intervals("student_endpoint") == []
    assert TestClient(gw.app).get("/ratchet/cost").json()["recommendation"] == "insufficient_data"


# ---------- the endpoints ----------
@pytest.mark.req("FR-15.2", "INV-13")
def test_post_endpoint_records_uptime_with_an_admin_key(gw_env):
    gw = gw_env(**{**COST_ENV, "RATCHET_ADMIN_KEYS": f"ops:{ADMIN_KEY}", "RATCHET_API_KEYS": f"acme:{API_KEY}"})
    c = TestClient(gw.app)
    url = "/ratchet/cost/endpoint"
    assert c.post(url, json={"state": "up"}).status_code == 401
    assert c.post(url, json={"state": "up"}, headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 403
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    for body, detail in (
        ([], "JSON object"),
        ({}, "state"),
        ({"state": "sideways"}, "state"),
        ({"state": "up", "gpu": 2}, "unknown keys"),
        ({"state": "up", "usd_per_hour": -1}, "usd_per_hour"),
        ({"state": "up", "usd_per_hour": True}, "usd_per_hour"),
        ({"state": "up", "usd_per_hour": 1000.5}, "usd_per_hour"),  # above MAX_USD_PER_GPU_HOUR
        ({"state": "up", "usd_per_hour": 1e308}, "usd_per_hour"),
        ({"state": "up", "usd_per_hour": 10**400}, "usd_per_hour"),  # was a 500: no float holds it
        ({"state": "up", "gpus": 1025}, "gpus"),
        ({"state": "up", "gpus": 1.5}, "gpus"),
        ({"state": "up", "note": "x" * 201}, "note"),
    ):
        r = c.post(url, json=body, headers=admin)
        assert r.status_code == 400 and detail in r.json()["detail"], (body, r.text)
    assert gw.rt.ledger.compute_intervals() == []

    up = c.post(url, json={"state": "up", "usd_per_hour": 2.5, "gpus": 2, "note": "demo"}, headers=admin).json()
    assert (up["state"], up["changed"]) == ("up", True)
    assert (up["interval"]["usd_per_hour"], up["interval"]["gpus"], up["interval"]["source"]) == (2.5, 2, "api")
    again = c.post(url, json={"state": "up"}, headers={"X-API-Key": ADMIN_KEY}).json()
    assert (again["changed"], again["interval"]["id"]) == (False, up["interval"]["id"])
    down = c.post(url, json={"state": "down"}, headers=admin).json()
    assert down["changed"] and down["interval"]["end_ts"] >= down["interval"]["start_ts"]
    assert c.post(url, json={"state": "down"}, headers=admin).json() == {
        "state": "down",
        "changed": False,
        "interval": None,
        "summary": "student endpoint already down; nothing changed",
    }
    events = [e for e in gw.rt.ledger.events() if e["kind"] == "endpoint"]
    assert [e["detail"].split(":")[0].split(" after")[0] for e in events] == ["student endpoint up", "student endpoint down"]
    assert all("(api by ops)" in e["detail"] for e in events)
    dump = json.dumps(gw.rt.ledger.events()) + json.dumps(gw.rt.ledger.compute_intervals())
    assert ADMIN_KEY not in dump and API_KEY not in dump


@pytest.mark.req("FR-15.2", "INV-8")
def test_post_endpoint_is_open_in_mock_capped_and_off_in_live(gw_env, tmp_path, monkeypatch):
    gw = gw_env(**COST_ENV)  # mock mode without admin keys: open, as 'mock-admin'
    c = TestClient(gw.app)
    r = c.post("/ratchet/cost/endpoint", json={"state": "up"})
    assert r.status_code == 200 and "simulated day 0" in r.json()["summary"]
    assert "(api by mock-admin)" in gw.rt.ledger.events()[-1]["detail"]
    monkeypatch.setattr(gw, "MOCK_MAX_API_INTERVALS", 1)
    assert c.post("/ratchet/cost/endpoint", json={"state": "down"}).status_code == 200  # down is never capped
    assert c.post("/ratchet/cost/endpoint", json={"state": "up"}).status_code == 409

    settings = Settings(
        mode="live",
        db_path=str(tmp_path / "live.db"),
        teacher_base="https://tokenfactory.test/v1",
        teacher_api_key="tf-key",
        teacher_model=OPEN_TEACHER,
        student_base="",
        admin_keys="",
        api_keys="",
        allow_unauthenticated=True,
        public_dashboard=True,
        tasks_file="",
        bom_signing_key="",
        tavily_api_key="",
        mock_warmup_days=0,
    )
    rt = gw.Runtime(settings, transports={"teacher": FakeOpenAIServer("ok").transport})
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    r = c.post("/ratchet/cost/endpoint", json={"state": "up"})
    assert (r.status_code, r.json()) == (403, {"detail": "admin API disabled: set RATCHET_ADMIN_KEYS"})
    assert rt.ledger.compute_intervals() == []
    report = c.get("/ratchet/cost").json()  # read like /ratchet/stats: public with RATCHET_PUBLIC_DASHBOARD=1
    assert report["recommendation"] == "insufficient_data" and report["requests"] == 0


# ---------- the CLI ----------
@pytest.mark.req("FR-15.2", "FR-15.3")
def test_cli_records_and_reports(tmp_path, capsys):
    db = tmp_path / "live.db"
    led = Ledger(str(db), auto_day=True)
    _request(led, 1, "student", 0.002, 0.0, 0.010, ts=1_780_000_000.0)
    s = _settings(db_path=str(db))
    assert cost.main(["endpoint", "up", "--db", str(db), "--at", "1780000000", "--gpus", "2"], settings=s) == 0
    assert "student endpoint up at 2026-05-28 20:26:40 UTC: 2 GPUs at $2.00 per GPU-hour" in capsys.readouterr().out
    assert cost.main(["endpoint", "up", "--db", str(db)], settings=s) == 0
    assert "already up since" in capsys.readouterr().out
    assert cost.main(["endpoint", "down", "--db", str(db), "--at", "2026-05-29T06:26:40Z"], settings=s) == 0
    assert "after 10.0 h" in capsys.readouterr().out
    assert cost.main(["report", "--db", str(db)], settings=s) == 0
    out = capsys.readouterr().out
    assert "recommendation: " in out and "unverified list prices" in out and "GPU-hours" in out
    assert cost.main(["report", "--db", str(db), "--json"], settings=s) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == REPORT_KEYS and report["endpoint_hours"] == pytest.approx(10.0)  # the second `up` added nothing
    assert report["endpoint_usd"] == pytest.approx(40.0)  # 10 h x 2 GPUs x $2
    assert cost.main(["report", "--db", str(db), "--json", "--days", "1"], settings=s) == 0
    today = json.loads(capsys.readouterr().out)  # the last day is today, months after that traffic
    assert today["window"]["since_day"] > 100 and (today["requests"], today["endpoint_hours"]) == (0, 0.0)
    assert [e["kind"] for e in Ledger(str(db), auto_day=True).events()] == ["endpoint", "endpoint"]

    led.compute_start("training_job", 3.0, 1, "night", ts=1_780_050_000.0)
    assert cost.main(["close", "training_job", "--db", str(db), "--at", "1780053600"], settings=s) == 0
    assert "closed training_job interval" in capsys.readouterr().out
    assert cost.main(["close", "training_job", "--db", str(db)], settings=s) == 0
    assert "no open training_job interval" in capsys.readouterr().out

    missing = tmp_path / "nowhere.db"
    assert cost.main(["endpoint", "up", "--db", str(db), "--at", "1780100000"], settings=s) == 0
    capsys.readouterr()
    for argv in (
        ["report", "--db", str(missing)],
        ["endpoint", "up", "--db", str(db), "--at", "yesterday"],
        ["endpoint", "up", "--db", str(db), "--at", "4102444800"],  # 2100: a plan, not a record
        ["endpoint", "down", "--db", str(db), "--at", "1780000000"],  # before the open interval started
        ["endpoint", "up", "--db", str(db), "--usd-per-hour", "5000"],  # above MAX_USD_PER_GPU_HOUR
        ["endpoint", "up", "--db", str(db), "--usd-per-hour", "1" + "0" * 400],
        ["endpoint", "up", "--db", str(db), "--gpus", "4096"],
        ["report", "--db", str(db), "--days", "0"],
    ):
        assert cost.main(argv, settings=s) == 2, argv
        assert capsys.readouterr().err.startswith("error: "), argv
    assert not missing.exists()  # the CLI never creates a ledger
    with pytest.raises(SystemExit):
        cost.main(["endpoint", "sideways", "--db", str(db)], settings=s)
    assert cost.main(["report", "--db", str(db)], settings=_settings(student_gpus=-1)) == 2
    assert "RATCHET_STUDENT_GPUS" in capsys.readouterr().err


@pytest.mark.req("FR-15.3")
def test_cli_runs_as_a_module_with_the_environment(tmp_path):
    db = tmp_path / "live.db"
    Ledger(str(db), auto_day=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RATCHET_")}
    env["RATCHET_DB"] = str(db)
    r = subprocess.run(
        [sys.executable, "-m", "ratchet.cost", "report"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    assert "recommendation: insufficient_data" in r.stdout and "Not enough data" in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-15.2", "FR-8.2")
def test_deploy_script_records_uptime_and_never_fails_on_it(tmp_path):
    """`deploy/student_endpoint.sh` start and stop call `nebius` (a fake here) and then record the endpoint in the
    ledger through the CLI; with no ledger to record in, the deploy still succeeds and says so."""
    assert BASH is not None
    nebius = FakeNebius(tmp_path / "nebius")
    nebius.listing(None)  # no endpoint yet: start creates it
    db = tmp_path / "live.db"
    Ledger(str(db), auto_day=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RATCHET_")}
    env = nebius.env(
        {
            **env,
            "RATCHET_PYTHON": sys.executable,
            "RATCHET_DB": str(db),
            "RATCHET_BUCKET_ID": "bucket-e00test",
            "RATCHET_STUDENT_KEY": "student-endpoint-token",
        }
    )
    script = str(ROOT / "deploy" / "student_endpoint.sh")

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, script, *args], cwd=tmp_path, env={**env, **extra}, capture_output=True, text=True, timeout=60
        )

    r = run()
    assert r.returncode == 0, r.stderr
    (iv,) = Ledger(str(db), auto_day=True).compute_intervals("student_endpoint")
    assert (iv["source"], iv["end_ts"], iv["usd_per_hour"]) == ("deploy", None, 1.55)
    running = nebius.fixture()
    running["items"][1]["status"]["state"] = "RUNNING"
    nebius.listing(running)
    r = run("stop")
    assert r.returncode == 0, r.stderr
    assert Ledger(str(db), auto_day=True).compute_intervals("student_endpoint")[0]["end_ts"] is not None
    calls = nebius.calls
    assert [c[:3] for c in calls[:2]] == [["ai", "endpoint", "list"], ["ai", "endpoint", "create"]]
    assert "--name" in calls[1] and calls[1][calls[1].index("--name") + 1] == "ratchet-student"
    stop = ["ai", "endpoint", "stop", "--id", "aiendpoint-e00fixturestudent00"]  # by id, found by name in the list
    assert calls[2:] == [["ai", "endpoint", "list", "--format", "json"], stop]
    r = run("stop", RATCHET_DB=str(tmp_path / "missing.db"))
    assert r.returncode == 0 and "endpoint down not recorded" in r.stderr
    assert not (tmp_path / "missing.db").exists()
    assert run("restart").returncode == 2


# ---------- migration and settings ----------
@pytest.mark.req("FR-15.1", "FR-15.2")
def test_v04_database_gets_the_cost_columns_and_table(tmp_path):
    db = tmp_path / "v04.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT, served_by TEXT, student_version INTEGER,
            confidence REAL, escalated INTEGER, audited INTEGER, audit_agree INTEGER, shadow_version INTEGER,
            shadow_serve INTEGER, shadow_agree INTEGER, latency_ms REAL, cost_usd REAL, baseline_cost_usd REAL, client TEXT
        );
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, kind TEXT, detail TEXT);
        INSERT INTO requests (ts, day, task, served_by, escalated, cost_usd, baseline_cost_usd, latency_ms)
            VALUES (1800000000, 1, 'ticket-triage', 'teacher', 1, 0.012, 0.010, 900),
                   (1800000001, 1, 'ticket-triage', 'student', 0, 0.002, 0.010, 300);
    """)
    con.commit()
    con.close()
    led = Ledger(str(db), auto_day=True)
    cols = {r["name"] for r in led.db.execute("PRAGMA table_info(requests)")}
    assert {"student_cost_usd", "teacher_cost_usd", "student_in_tokens", "student_out_tokens"} <= cols
    assert {r["name"] for r in led.db.execute("PRAGMA table_info(compute_intervals)")} >= {"kind", "start_ts", "end_ts"}
    r = cost.cost_report(led, _settings(), now=1_800_000_100.0)
    assert (r["split_unknown_requests"], r["split_unknown_usd"]) == (2, pytest.approx(0.014))
    assert r["teacher_usd"] == pytest.approx(0.014) and r["student_per_token_usd"] == 0.0  # old rows count as teacher
    assert r["avoided_teacher_usd_per_student_request"] == pytest.approx(0.010 - 0.002)
    assert r["hosted_fallback_usd_per_request"] is None  # no student tokens recorded before v0.5
    assert any("before v0.5" in w for w in r["warnings"])
    assert led.compute_start("student_endpoint", 1.55, 1, "cli", ts=1_800_000_000.0) == 1


@pytest.mark.parametrize(
    "env, name",
    [
        ({"RATCHET_STUDENT_GPU_USD_PER_HOUR": "-1"}, "RATCHET_STUDENT_GPU_USD_PER_HOUR"),
        ({"RATCHET_TRAIN_GPU_USD_PER_HOUR": "cheap"}, "RATCHET_TRAIN_GPU_USD_PER_HOUR"),
        ({"RATCHET_STUDENT_GPUS": "1.5"}, "RATCHET_STUDENT_GPUS"),
        ({"RATCHET_TRAIN_GPUS": "-2"}, "RATCHET_TRAIN_GPUS"),
        ({"RATCHET_STUDENT_COST_MODEL": "per_gpu"}, "RATCHET_STUDENT_COST_MODEL"),
        ({"RATCHET_MOCK_ENDPOINT_HOURS": "25"}, "RATCHET_MOCK_ENDPOINT_HOURS"),
        ({"RATCHET_STUDENT_GPU_USD_PER_HOUR": "nan"}, "RATCHET_STUDENT_GPU_USD_PER_HOUR"),
        ({"RATCHET_STUDENT_GPU_USD_PER_HOUR": "1e308"}, "RATCHET_STUDENT_GPU_USD_PER_HOUR"),
        ({"RATCHET_TRAIN_GPUS": "4096"}, "RATCHET_TRAIN_GPUS"),
    ],
)
@pytest.mark.req("FR-15.3", "NFR-11")
def test_bad_cost_settings_stop_startup_naming_the_variable(gw, monkeypatch, env, name):
    for k, v in {**COST_ENV, **env}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match=name):  # reading the variable, or checking it as the runtime starts
        gw.Runtime(Settings(mode="mock", mock_warmup_days=0))


@pytest.mark.req("NFR-11")
def test_cost_settings_defaults_and_spellings(monkeypatch):
    for k in COST_ENV:
        monkeypatch.delenv(k, raising=False)
    p = Settings().cost_policy()
    assert (p.model, p.student.usd_per_gpu_hour, p.student.gpus, p.train.usd_per_hour) == ("per_token", 1.55, 1, 1.55)
    assert (p.mock_endpoint_hours, p.prices_verified) == (24.0, False)
    monkeypatch.setenv("RATCHET_STUDENT_COST_MODEL", " GPU_Hours ")
    monkeypatch.setenv("RATCHET_PRICES_VERIFIED", "1")
    monkeypatch.setenv("RATCHET_STUDENT_GPUS", "2")
    p = Settings().cost_policy()
    assert (p.model, p.prices_verified, p.student.usd_per_hour) == ("gpu_hours", True, 3.1)
    monkeypatch.setenv("RATCHET_STUDENT_COST_MODEL", "")
    assert Settings().cost_policy().model == "per_token"
    r = cost.cost_report(_hand_ledger(), _settings(prices_verified=True))
    assert r["prices"]["label"] == "prices confirmed by the operator" and "unverified" not in r["explanation"]
    assert math.isclose(cost.parse_time("1970-01-02T00:00:00"), 86400.0)
