"""The label-free drift alarm (FR-16.6, ADR 0032): the Fenwick ranks and smoothed conformal p-values, the test
martingale's validity under the null and its power after a drop in confidence, the decreasing switching rate, replay
and compaction, the ledger's reads, the gate's `drift` events, the endpoints that report and act on alarms, the opt-in
actions (the audit boost, FR-6.40, and the early night, FR-6.41), and the simulator: alarms on the launch day, none
without drift, and no draw from the router's random generator."""

from __future__ import annotations

import bisect
import functools
import gc
import json
import math
import random
import sqlite3
import threading
import time
import tracemalloc
from collections import Counter

import pytest
from fakes import BASE_MODEL, OPEN_TEACHER, REJECTED_EVAL, RecordingEvaluator, RecordingProvider, RecordingTrainer, _version
from fakes import _examples as _store_examples
from fastapi.testclient import TestClient

from ratchet import drift
from ratchet.config import Settings
from ratchet.drift import (
    BINS,
    COMPACT_EVERY,
    MAX_POINTS,
    MAX_STREAMS,
    OTHER_TASKS,
    DriftMonitor,
    Fenwick,
    Stream,
    bin_of,
    boosted_rate,
)
from ratchet.ledger import Ledger
from ratchet.nightschool.orchestrate import early_night, run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet
from sim import simulate

TASK = "ticket-triage"
CERTIFIED = {"precision": 0.98, "coverage": 0.6, "threshold": 0.5, "certificate": {"certified": True, "served": 60}}


def _confidences(seed: int, n: int, drop_at: int | None = None, share: float = 0.3) -> list[float]:
    """`n` confidences like a trained student's (mostly high, some unsure). From `drop_at` on, a `share` of requests is
    new to the student and scores low: the drift a product launch causes."""
    r = random.Random(seed)
    out = []
    for i in range(n):
        if drop_at is not None and i >= drop_at and r.random() < share:
            out.append(r.uniform(0.1, 0.5))
        else:
            out.append(r.uniform(0.8, 0.97) if r.random() < 0.7 else r.uniform(0.35, 0.92))
    return out


def _feed(stream: Stream, xs: list[float], start: int = 1) -> int | None:
    """Observe every confidence; the index (from 0) of the observation that alarmed, or None."""
    hit = None
    for i, x in enumerate(xs):
        if stream.observe(x, start + i, 1) and hit is None:
            hit = i
    return hit


def _log(led: Ledger, vid: int | None, confs: list[float | None], task: str = TASK, day: int | None = None) -> None:
    for c in confs:
        row = dict(
            task=task,
            served_by="student" if c is not None and c >= 0.5 else "teacher",
            student_version=vid,
            confidence=c,
            escalated=int(c is None or c < 0.5),
            audited=0,
            cost_usd=0.0,
            baseline_cost_usd=0.0,
            latency_ms=1.0,
        )
        if day is not None:
            row["day"] = day
        led.log_request(**row)


# ---------- the statistics (stdlib) ----------
@pytest.mark.req("FR-16.6")
def test_fenwick_ranks_match_a_sorted_list_with_ties():
    """`prefix(i)` counts the values in bins 0..i, as `bisect` does on a sorted list, with many values per bin (the
    quantised ties the smoothed p-value breaks), and at both ends of the range."""
    r = random.Random(3)
    tree, seen = Fenwick(BINS + 1), []
    for _ in range(3000):
        q = bin_of(r.choice([0.0, 1.0, 0.5, 0.25]) if r.random() < 0.3 else r.random())
        tree.add(q)
        bisect.insort(seen, q)
        probe = r.randrange(-1, BINS + 2)
        assert tree.prefix(probe) == bisect.bisect_right(seen, probe)
    for probe in (-5, -1, 0, 2500, 5000, BINS - 1, BINS, BINS + 7):
        assert tree.prefix(probe) == bisect.bisect_right(seen, probe)
    with pytest.raises(IndexError):
        tree.add(BINS + 1)
    with pytest.raises(IndexError):
        tree.add(-1)
    with pytest.raises(ValueError):
        Fenwick(0)


@pytest.mark.req("FR-16.6")
def test_bins_and_usable_confidences():
    assert (bin_of(0.0), bin_of(1.0), bin_of(-0.3), bin_of(1.7)) == (0, BINS, 0, BINS)
    assert bin_of(0.12345) == 1234 and bin_of(0.5) == BINS // 2
    assert drift.usable(0.5) and drift.usable(0) and drift.usable(1)
    assert not any(drift.usable(x) for x in (None, math.nan, math.inf, True, "0.9"))
    s = Stream(1, TASK)
    for bad in (math.nan, math.inf, None):
        with pytest.raises(ValueError):
            s.observe(bad, 1, 1)  # type: ignore[arg-type]
    assert s.n == 0
    with pytest.raises(ValueError):
        Stream(1, TASK, threshold=1.0)
    with pytest.raises(ValueError):
        DriftMonitor(threshold=0.5)


@pytest.mark.req("FR-16.6")
@pytest.mark.parametrize("ties", [False, True])
def test_p_values_are_uniform_under_the_null(ties):
    """5,000 i.i.d. confidences give p-values flat over [0, 1]: each decile holds 500 +- 4 binomial standard deviations,
    also when the scores take only 9 values (the smoothing draw breaks ties)."""
    r = random.Random(11)
    s, ps = Stream(1, TASK, seed=5), []
    for i in range(5000):
        s.observe(r.randint(1, 9) / 10 if ties else r.random(), i, 1)
        assert s.p is not None and 0 < s.p <= 1
        ps.append(s.p)
    deciles = [0] * 10
    for p in ps:
        deciles[min(9, int(p * 10))] += 1
    tol = 4 * math.sqrt(5000 * 0.1 * 0.9)
    assert all(abs(c - 500) <= tol for c in deciles), deciles


@pytest.mark.req("FR-16.6")
def test_rarely_alarms_under_the_null():
    """Ville's bound, quickly: 100 seeded i.i.d. streams of 300 points at threshold 10 alarm at most 10% of the time
    (the guarantee). The full-size check (200 streams of 2,000 at threshold 100) runs with --runslow."""
    alarmed = 0
    for seed in range(100):
        s = Stream(seed, TASK, seed=seed, threshold=10)
        alarmed += _feed(s, [random.Random(seed).random() for _ in range(300)]) is not None
    assert alarmed / 100 <= 0.10


@pytest.mark.slow
@pytest.mark.req("FR-16.6")
def test_rarely_alarms_under_the_null_full_size():
    """200 seeded i.i.d. streams of 2,000 points at threshold 100: at most 3% ever alarm (the bound is 1%)."""
    alarmed = 0
    for seed in range(200):
        r = random.Random(1000 + seed)
        alarmed += _feed(Stream(seed, TASK, seed=seed), [r.random() for _ in range(2000)]) is not None
    assert alarmed / 200 <= 0.03


@pytest.mark.req("FR-16.6")
def test_alarms_soon_after_confidence_drops():
    """A stream whose confidence drops at n = 1,000 (30% of requests new to the student) alarms within 500 points, and
    never before the drop; the same stream without the drop never alarms."""
    s = Stream(1, TASK)
    hit = _feed(s, _confidences(4, 1500, drop_at=1000))
    assert hit is not None and 1000 <= hit < 1500
    assert s.alarm_n == hit + 1 and s.alarm_at == hit + 1 and s.alarm_day == 1
    assert _feed(Stream(1, TASK), _confidences(4, 1500)) is None


@pytest.mark.req("FR-16.6")
def test_rising_confidence_makes_the_capital_fall():
    """One-sided: confidence that rises after n = 1,000 is not what the bets pay on, so the capital falls further."""
    r = random.Random(8)
    xs = [r.uniform(0.3, 0.8) for _ in range(1000)] + [r.uniform(0.85, 1.0) for _ in range(1000)]
    s = Stream(1, TASK)
    assert _feed(s, xs) is None
    at_change = dict(s.trajectory)
    assert s.log_c < math.log(10) * min(v for n, v in at_change.items() if n <= 1000)


@pytest.mark.req("FR-16.6")
def test_a_late_change_is_caught_as_fast_as_an_early_one():
    """The switching rate falls as 1/n after 100 observations, so the capital only shrinks like a power of n while
    nothing changes (a constant rate of 0.01 would leave it about 70 orders of magnitude down after 20,000) and a
    drop after 20,000 requests is still caught within 1,000."""
    s = Stream(1, TASK)
    hit = _feed(s, _confidences(9, 21_000, drop_at=20_000))
    assert hit is not None and 20_000 <= hit < 21_000
    trough = min(v for n, v in s.trajectory if n <= 20_000)
    assert trough > -6  # log10 of the capital just before the change


@pytest.mark.req("FR-16.6")
def test_alarm_latches_and_the_trajectory_stays_small():
    s = Stream(2, TASK)
    xs = _confidences(4, 1000) + [0.05] * 3000
    alarms = [i for i, x in enumerate(xs) if s.observe(x, 100 + i, 3)]
    assert len(alarms) == 1 and s.alarm_at == 100 + alarms[0] and s.alarm_day == 3
    assert s.peak >= s.log_threshold and s.n == 4000
    assert len(s.trajectory) < MAX_POINTS and s.stride == 32
    assert [n for n, _ in s.trajectory] == list(range(s.stride, 4001, s.stride))[: len(s.trajectory)]
    summary = s.summary()
    assert summary["alarm"] and summary["n"] == 4000 and summary["log10_martingale"] == round(s.log_c / math.log(10), 4)
    assert summary["trajectory"][-1] == list(s.trajectory[-1])


@pytest.mark.req("FR-16.6", "NFR-55")
def test_ten_thousand_observations_take_under_150_ms():
    xs = _confidences(1, 10_000)
    best = math.inf
    for _ in range(3):
        s = Stream(1, TASK)
        t = time.perf_counter()
        for i, x in enumerate(xs):
            s.observe(x, i, 1)
        best = min(best, time.perf_counter() - t)
    assert best < 0.150, f"{best * 1000:.0f} ms"


def test_boosted_rate_is_capped_and_never_lowers_a_rate():
    assert boosted_rate(0.1, 3.0) == pytest.approx(0.3)
    assert boosted_rate(0.2, 3.0) == 0.5  # capped at MAX_BOOSTED_AUDIT_RATE
    assert boosted_rate(0.8, 3.0) == 0.8  # a canary already audited above the cap keeps its rate
    assert boosted_rate(0.1, 1.0) == 0.1


# ---------- the ledger and the monitor ----------
@pytest.mark.req("FR-16.6")
def test_confidence_stream_reads_only_student_confidences_in_id_order():
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    _log(led, None, [None])  # cold start: no version
    _log(led, v, [0.9, None, 0.3])  # None: the student never answered (breaker, error)
    _log(led, None, [None], task="research-plan")  # a pinned task: the teacher alone, no confidence
    _log(led, v, [0.7], task="page-read", day=4)
    rows = led.confidence_stream()
    assert [(vid, task, c) for _, vid, task, c, _ in rows] == [(v, TASK, 0.9), (v, TASK, 0.3), (v, "page-read", 0.7)]
    assert [r[0] for r in rows] == sorted(r[0] for r in rows) and rows[-1][4] == 4
    assert led.confidence_stream(after_id=rows[0][0]) == rows[1:]
    assert led.confidence_stream(limit=1) == rows[:1]
    assert led.confidence_stream(task="page-read") == rows[2:]
    assert led.confidence_stream(version=v, before_id=rows[1][0]) == rows[:1]
    assert led.confidence_stream(version=v + 1) == []
    assert led.version_states() == {v: "live"}


@pytest.mark.req("FR-16.6")
def test_events_once_and_the_last_event(tmp_path):
    """`event_once` writes an event under a key once, also from a second connection to the same file (the gateway and
    `python -m ratchet.live tick`), and records the event's id in `meta` with any extra entries; `day` dates an event
    that happened on another day. `last_event` is None without one; `request_ts` reads a request's time."""
    path = str(tmp_path / "ratchet.db")
    a, b = Ledger(path), Ledger(path)
    assert a.last_event("drift") is None and a.request_ts(1) is None
    a.event("trained", "v1 on 60 examples")
    assert a.event_once("drift:1:t", "drift", "first") is True
    assert b.event_once("drift:1:t", "drift", "again") is False
    assert a.event_once("drift:1:t", "drift", "again") is False
    assert b.event_once("drift:2:t", "drift", "other stream", day=3, meta={"request": 7, "day": 3}) is True
    events = a.events()
    kinds = [(e["kind"], e["detail"], e["day"]) for e in events]
    assert kinds == [("trained", "v1 on 60 examples", 0), ("drift", "first", 0), ("drift", "other stream", 3)]
    assert a.last_event("drift") == events[-1] and a.last_event("trained") == events[0]
    assert a.meta_value("drift:1:t") == f'{{"event": {events[1]["id"]}}}' and a.meta_value("nope") is None
    assert json.loads(a.meta_value("drift:2:t")) == {"event": events[2]["id"], "request": 7, "day": 3}
    rid = a.log_request(task=TASK, served_by="teacher", cost_usd=0.0, baseline_cost_usd=0.0, latency_ms=1.0, ts=12.5)
    assert a.request_ts(rid) == 12.5 and a.request_ts(rid + 1) is None


def _three_streams(led: Ledger) -> tuple[int, int, int]:
    v1 = _version(led, "retired", CERTIFIED)
    v2 = _version(led, "live", CERTIFIED)
    v3 = _version(led, "canary", CERTIFIED)
    for i in range(6):  # interleaved, as real traffic is
        _log(led, v1, _confidences(i, 150), day=i)
        _log(led, v2, _confidences(10 + i, 150, drop_at=50 if i >= 3 else None, share=0.6), day=i)
        _log(led, v2, _confidences(20 + i, 40), task="page-read", day=i)
        _log(led, v3, _confidences(30 + i, 60), day=i)
    return v1, v2, v3


def _state(m: DriftMonitor) -> dict:
    return {k: (s.n, s.log_c, s.peak, s.alarm_at, s.alarm_day, tuple(s.trajectory)) for k, s in m.streams.items()}


@pytest.mark.req("FR-16.6", "NFR-55")
def test_replaying_the_ledger_reproduces_every_capital_exactly():
    """A monitor fed tick by tick and one that replays the whole ledger at once (a restart), or in small batches, hold
    the same numbers to the last bit, alarms included: each stream draws from its own seeded generator."""
    led = Ledger(":memory:")
    _three_streams(led)
    rows = led.confidence_stream()

    class Upto:
        """The ledger as it stood when request `upto` was logged."""

        def __init__(self, upto: int) -> None:
            self.upto = upto

        def confidence_stream(self, after_id=0, limit=drift.BATCH, **kw):
            return [r for r in led.confidence_stream(after_id, limit, **kw) if r[0] <= self.upto]

        def version_states(self):
            return led.version_states()

    incremental = DriftMonitor()
    seen: list = []
    for cut in range(0, len(rows), 37):  # a tick every 37 requests
        seen += incremental.update(Upto(rows[min(cut + 36, len(rows) - 1)][0]))
    replayed, batched = DriftMonitor(), DriftMonitor(batch=50)
    assert replayed.update(led) == seen == batched.update(led)
    assert _state(incremental) == _state(replayed) == _state(batched)
    assert len(seen) == 1 and seen[0].task == TASK and seen[0].version == 2
    assert replayed.rebuild(led) == seen and _state(replayed) == _state(batched)
    other = DriftMonitor(seed=8)
    other.update(led)
    assert _state(other) != _state(replayed)  # the seed matters, and only the seed


@pytest.mark.req("FR-16.6")
def test_compaction_keeps_serving_versions_and_replays_a_restored_one():
    """Counts are kept for canary and live versions and the most recently retired one; an older retired or rolled-back
    version keeps only its final numbers. Restored to live by hand, its stream is replayed from the ledger first, so
    its capital is what a full replay gives."""
    led = Ledger(":memory:")
    old = _version(led, "retired", CERTIFIED)
    _log(led, old, _confidences(1, 200))
    v1, v2, v3 = _three_streams(led)
    gone = _version(led, "rolled_back", CERTIFIED)
    _log(led, gone, _confidences(2, 30))
    m = DriftMonitor()
    m.update(led)
    active = {k: s.active for k, s in m.streams.items()}
    assert active == {
        (old, TASK): False,
        (v1, TASK): True,  # the most recently retired
        (v2, TASK): True,
        (v2, "page-read"): True,
        (v3, TASK): True,
        (gone, TASK): False,
    }
    frozen = m.streams[(old, TASK)].summary()
    assert frozen["n"] == 200 and frozen["active"] is False and frozen["trajectory"]
    with pytest.raises(RuntimeError):
        m.streams[(old, TASK)].observe(0.9, 10**6, 1)
    led.set_version(old, "live", 100)  # an operator restores it
    _log(led, old, _confidences(3, 50))
    m.update(led)
    assert m.streams[(old, TASK)].active and m.streams[(old, TASK)].n == 250
    fresh = DriftMonitor()
    fresh.update(led)
    assert _state(fresh)[(old, TASK)] == _state(m)[(old, TASK)]


@pytest.mark.req("FR-16.6")
def test_compaction_keeps_the_most_recently_retired_version_not_the_newest():
    """A canary retired early (v2) has a higher id than the live version retired later (v1); v1 is the one an operator
    would restore, so v1 keeps its counts and v2 is compacted."""
    led = Ledger(":memory:")
    v1 = _version(led, "live", CERTIFIED)
    _log(led, v1, _confidences(1, 100))
    v2 = _version(led, "canary", CERTIFIED)
    _log(led, v2, _confidences(2, 30))
    led.set_version(v2, "retired", 0)  # replaced, or inconclusive
    _log(led, v1, _confidences(4, 20))  # v1 serves on
    v3 = _version(led, "live", CERTIFIED)
    led.set_version(v1, "retired", 0)  # v3 went live later
    _log(led, v3, _confidences(3, 30))
    assert list(led.version_states()) == [v2, v1, v3]
    m = DriftMonitor()
    m.update(led)
    assert {k[0]: s.active for k, s in m.streams.items()} == {v1: True, v2: False, v3: True}


@pytest.mark.req("FR-16.6")
def test_rows_without_a_finite_confidence_are_skipped():
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    _log(led, v, [0.9, math.inf, 0.8, -math.inf, 0.7])
    m = DriftMonitor()
    m.update(led)
    assert m.streams[(v, TASK)].n == 3 and m.skipped == 2 and m.last_id == 5


def _traced(fn):
    """`fn()`'s result and the memory it still holds once it returns and garbage is collected (tracemalloc)."""
    tracemalloc.start()
    try:
        out = fn()
        gc.collect()
        return out, tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()


@pytest.mark.req("FR-16.6", "NFR-56")
def test_stream_memory_follows_what_it_has_seen(monkeypatch):
    """A stream keeps its bins in a sorted array (2 bytes each) until DENSE_AFTER observations, then in one Fenwick tree.
    A stream that saw one confidence holds a few KB, not a tree; both forms count the same, so the capital is the same
    to the last bit whichever held the bins."""
    one, held = _traced(lambda: _feed(Stream(1, TASK), [0.9]))
    assert one is None and held < 8_000, held
    xs = _confidences(5, drift.DENSE_AFTER + 2_000, drop_at=drift.DENSE_AFTER + 500)
    lazy = Stream(1, TASK)
    hit = _feed(lazy, xs)
    assert hit is not None and lazy.ranks.tree is not None and lazy.ranks.bins is None
    monkeypatch.setattr(drift, "DENSE_AFTER", 0)  # a tree from the first observation
    dense = Stream(1, TASK)
    assert _feed(dense, xs) == hit
    assert (lazy.log_c, lazy.peak, lazy.alarm_at, lazy.trajectory) == (dense.log_c, dense.peak, dense.alarm_at, dense.trajectory)
    bins = sorted(bin_of(x) for x in xs)
    tree = Fenwick(BINS + 1)
    for b in bins:
        tree.add(b)
    assert Fenwick.build(BINS + 1, bins).tree == tree.tree == lazy.ranks.tree.tree
    with pytest.raises(IndexError):
        Fenwick.build(3, [3])
    lazy.compact()
    assert lazy.ranks is None and lazy.rng is None and not lazy.active


def _junk(led: Ledger, vid: int, n: int, start: int = 0, seed: int = 3) -> list[str]:
    """`n` requests for `vid`, each under a task name of its own: what a client inventing X-Ratchet-Task values logs
    under RATCHET_UNKNOWN_TASKS=distill (the default)."""
    r = random.Random(seed)
    names = [f"junk-{i}" for i in range(start, start + n)]
    for name in names:
        _log(led, vid, [r.uniform(0.5, 0.99)], task=name)
    return names


@pytest.mark.req("FR-16.6", "NFR-56")
def test_client_task_names_cannot_grow_the_monitor():
    """docs/THREAT_MODEL.md T12: a version opens MAX_STREAMS task streams, in the order it first sees the names, and every
    later name shares its OTHER_TASKS stream. 1,000 invented names hold 17 streams, not 1,000 (it was 164 KB a
    stream); 5,000 more add no stream and at most one Fenwick tree; a name that got a stream keeps it; another version
    gets its own MAX_STREAMS; the family bound stops growing with the names."""
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    names = _junk(led, v, 1_000)
    m, held = _traced(lambda: (mon := DriftMonitor(), mon.update(led))[0])
    assert len(m.streams) == MAX_STREAMS + 1 and held < 250_000, (len(m.streams), held)
    assert set(m.streams) == {(v, t) for t in [*names[:MAX_STREAMS], OTHER_TASKS]}
    assert m.streams[(v, OTHER_TASKS)].n == 1_000 - MAX_STREAMS
    assert m.family_bound() == (MAX_STREAMS + 1) / 100 and m.counts()["monitored"] == MAX_STREAMS + 1

    _junk(led, v, 5_000, start=1_000, seed=4)
    _log(led, v, [0.8, 0.7], task=names[0])  # an early name keeps its own stream
    _, grew = _traced(lambda: m.update(led))
    assert len(m.streams) == MAX_STREAMS + 1 and grew < 150_000, grew
    assert m.streams[(v, names[0])].n == 3 and m.streams[(v, OTHER_TASKS)].n == 6_000 - MAX_STREAMS
    w = _version(led, "canary", CERTIFIED)
    _junk(led, w, 40, start=10_000)
    m.update(led)
    assert len(m.streams) == 2 * (MAX_STREAMS + 1) and m.streams[(w, OTHER_TASKS)].n == 40 - MAX_STREAMS
    fresh = DriftMonitor()
    fresh.update(led)
    assert _state(fresh) == _state(m)  # the choice of streams depends on the rows' order alone, so a replay agrees
    capped = DriftMonitor(max_streams=0)  # every task shares one stream per version
    capped.update(led)
    assert set(capped.streams) == {(v, OTHER_TASKS), (w, OTHER_TASKS)}
    with pytest.raises(ValueError):
        DriftMonitor(max_streams=-1)


@pytest.mark.req("FR-16.6")
def test_a_compacted_shared_stream_replays_its_own_rows():
    """When a compacted version serves again, its OTHER_TASKS stream is replayed from the version's rows whose task has
    no stream of its own, so it holds what a full replay gives; a new name still goes to it."""
    led = Ledger(":memory:")
    old = _version(led, "retired", CERTIFIED)
    for i in range(MAX_STREAMS + 4):
        _log(led, old, _confidences(i, 30), task=f"t{i}")
    newer = _version(led, "retired", CERTIFIED)  # the most recently retired keeps its counts; `old` is compacted
    _log(led, newer, _confidences(99, 30))
    m = DriftMonitor()
    m.update(led)
    assert not m.streams[(old, OTHER_TASKS)].active and m.streams[(old, OTHER_TASKS)].n == 4 * 30
    led.set_version(old, "live", 100)  # an operator restores it
    _log(led, old, _confidences(50, 40), task=f"t{MAX_STREAMS + 1}")  # a pooled name
    _log(led, old, _confidences(51, 40), task="t0")  # a name with its own stream
    _log(led, old, _confidences(52, 40), task="brand-new")  # a new name: pooled
    m.update(led)
    assert m.streams[(old, OTHER_TASKS)].n == 4 * 30 + 80 and m.streams[(old, "t0")].n == 70
    fresh = DriftMonitor()
    fresh.update(led)
    assert _state(fresh) == _state(m)


# ---------- the gate: events, the certificate, the policy ----------
@pytest.mark.req("FR-16.6")
def test_tick_writes_one_drift_event_per_alarm_across_restarts(tmp_path):
    """An alarm writes one `drift` event naming the version, the task, the evidence and both false-alarm bounds; later
    ticks, and a second gate replaying the same file (a restart, or the cron tick), write no other."""
    path = str(tmp_path / "ratchet.db")
    led = Ledger(path)
    v = _version(led, "live", CERTIFIED)
    _log(led, v, _confidences(4, 1000), day=4)
    gate = ReleaseGate(led)
    gate.tick()
    assert [e for e in led.events() if e["kind"] == "drift"] == []
    _log(led, v, _confidences(5, 500, drop_at=0), day=5)
    gate.tick()
    gate.tick()
    (event,) = [e for e in led.events() if e["kind"] == "drift"]
    alarm = gate.drift.open_alarm(v)
    assert event["day"] == alarm["alarm_day"] == 5 != led.day  # the alarm's own day, not the ledger's today
    recorded = json.loads(led.meta_value(f"drift:{v}:{TASK}"))
    assert recorded == {"event": event["id"], "request": alarm["alarm_at_request"], "day": 5}
    d = event["detail"]
    assert d.startswith(f"v{v} ({TASK}): confidence on recent traffic no longer matches earlier traffic for this version")
    assert "test martingale " in d and ">= 100 after " in d and "false-alarm probability <= 1% for this stream" in d
    assert "<= 1% across 1 stream)" in d and "this alone does not mean the model got worse" in d
    restarted = ReleaseGate(Ledger(path))
    assert restarted.check_drift()[0].version == v  # it replays the alarm ...
    assert len([e for e in Ledger(path).events() if e["kind"] == "drift"]) == 1  # ... but writes it once
    status = gate.drift_status()
    assert status["enabled"] and status["alarms"] == 1 and status["monitored"] == 1
    assert status["family_bound"] == 0.01 and status["false_alarm_bound"] == 0.01
    (stream,) = status["streams"]
    assert stream["alarm"] and stream["alarm_day"] == 5 and stream["version"] == v and stream["trajectory"]
    assert gate.drift.open_alarm(v)["alarm_at_request"] == stream["alarm_at_request"]
    assert gate.drift.open_alarm(v + 1) is None and gate.drift.alarmed_versions() == {v}


@pytest.mark.req("FR-16.6", "NFR-56")
def test_a_shift_under_invented_task_names_alarms_on_the_shared_stream():
    """Traffic under task names beyond MAX_STREAMS is still watched, in one stream: a drop there alarms on it, and the
    event names it 'other tasks'."""
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    for i in range(MAX_STREAMS):
        _log(led, v, _confidences(i, 10), task=f"t{i}", day=4)
    confs = _confidences(4, 1000) + _confidences(5, 500, drop_at=0)
    for i, c in enumerate(confs):
        _log(led, v, [c], task=f"junk-{i}", day=4 if i < 1000 else 5)
    gate = ReleaseGate(led)
    gate.tick()
    (event,) = [e for e in led.events() if e["kind"] == "drift"]
    assert event["detail"].startswith(f"v{v} (other tasks): confidence on recent traffic no longer matches")
    assert f"across {MAX_STREAMS + 1} streams" in event["detail"] and event["day"] == 5
    assert led.meta_value(f"drift:{v}:{OTHER_TASKS}") is not None
    assert gate.drift.open_alarm(v)["task"] == OTHER_TASKS and gate.drift_counts()["monitored"] == MAX_STREAMS + 1


@pytest.mark.req("FR-16.6")
def test_a_drift_event_the_ledger_refused_is_written_by_the_next_check(monkeypatch):
    """The monitor latches an alarm as it sees it; if the ledger refuses the event (a busy database), the error
    propagates and the next check writes the event, once."""
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    _drifting(led, v, drop=True)
    gate = ReleaseGate(led)
    real = led.event_once

    def busy(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(led, "event_once", busy)
    with pytest.raises(sqlite3.OperationalError):
        gate.check_drift()
    assert gate.drift.alarmed_versions() == {v} and not [e for e in led.events() if e["kind"] == "drift"]
    monkeypatch.setattr(led, "event_once", real)
    assert gate.check_drift() == []  # no new alarm; the queued event is written
    gate.check_drift()
    (event,) = [e for e in led.events() if e["kind"] == "drift"]
    assert event["day"] == 5 and event["detail"].startswith(f"v{v} ({TASK}): ")


@pytest.mark.req("FR-16.6")
def test_policy_turns_the_monitor_off_and_refuses_actions_without_it(monkeypatch):
    off = ReleaseGate(Ledger(":memory:"), ReleasePolicy(drift_alarm=0))
    assert off.drift is None and off.check_drift() == [] and off.drift_view(1) is None
    assert off.drift_status() == {
        "enabled": False,
        "threshold": None,
        "false_alarm_bound": None,
        "family_bound": None,
        "monitored": 0,
        "alarms": 0,
        "streams": [],
    }
    assert ReleaseGate(Ledger(":memory:"), ReleasePolicy(drift_alarm=1000)).drift.threshold == 1000
    for kw, says in (
        ({"drift_alarm": 1.0}, "more than 1, or 0"),
        ({"drift_alarm": -5}, "more than 1, or 0"),
        ({"drift_audit_boost": 0.5}, "1 or more"),
        ({"drift_alarm": 0, "drift_audit_boost": 3.0}, "RATCHET_DRIFT_AUDIT_BOOST acts on drift alarms"),
        ({"drift_alarm": 0, "drift_early_night": 1}, "RATCHET_DRIFT_EARLY_NIGHT acts on drift alarms"),
        ({"drift_early_night": 2}, "0 or 1"),
    ):
        with pytest.raises(ValueError, match=says):
            ReleasePolicy(**kw)
    monkeypatch.setenv("RATCHET_DRIFT_ALARM", "50")
    monkeypatch.setenv("RATCHET_DRIFT_AUDIT_BOOST", "3")
    monkeypatch.setenv("RATCHET_DRIFT_EARLY_NIGHT", "1")
    p = ReleasePolicy.from_env()
    assert (p.drift_alarm, p.drift_audit_boost, p.drift_early_night) == (50.0, 3.0, 1)


# ---------- the gateway ----------
def _drift_in_gateway(gw) -> int:
    """A live version with a certificate whose traffic drops on day 5, logged into the gateway's ledger and ticked."""
    led = gw.rt.ledger
    v = _version(led, "live", CERTIFIED)
    _log(led, v, _confidences(4, 1000), day=4)
    _log(led, v, _confidences(5, 500, drop_at=0), day=5)
    gw.rt.tick()
    return v


@pytest.mark.req("FR-16.6", "FR-16.3")
def test_certificate_is_suspended_while_a_drift_alarm_is_open(gw_env):
    gw = gw_env()
    client = TestClient(gw.app)
    led = gw.rt.ledger
    quiet = _version(led, "live", CERTIFIED)
    _log(led, quiet, _confidences(4, 300))
    gw.rt.tick()
    body = client.get(f"/ratchet/versions/{quiet}/certificate").json()
    assert body["status"] == "certified" and body["drift"]["alarm"] is False
    assert [s["task"] for s in body["drift"]["streams"]] == [TASK] and "trajectory" not in body["drift"]["streams"][0]
    led.set_version(quiet, "retired", 0)
    v = _drift_in_gateway(gw)
    body = client.get(f"/ratchet/versions/{v}/certificate").json()
    assert body["status"] == "suspended" and body["drift"]["alarm"] is True and body["drift"]["threshold"] == 100
    assert body["current"]["certificate"]["certified"] is True  # still shown; it no longer covers today's traffic
    candidate = _version(led, "candidate", CERTIFIED)  # never served: nothing to monitor
    assert client.get(f"/ratchet/versions/{candidate}/certificate").json()["drift"] is None


@pytest.mark.req("FR-16.6")
def test_drift_endpoint_and_stats_block(gw_env):
    gw = gw_env()
    client = TestClient(gw.app)
    empty = client.get("/ratchet/drift").json()
    assert empty["enabled"] and empty["streams"] == [] and empty["alarms"] == 0 and empty["family_bound"] == 0
    v = _drift_in_gateway(gw)
    body = client.get("/ratchet/drift").json()
    assert set(body) == {"enabled", "threshold", "false_alarm_bound", "family_bound", "monitored", "alarms", "streams"}
    (stream,) = body["streams"]
    assert set(stream) == {
        "version",
        "task",
        "n",
        "log10_martingale",
        "peak_log10",
        "alarm",
        "alarm_at_request",
        "alarm_day",
        "alarm_n",
        "last_request",
        "active",
        "trajectory",
    }
    assert stream["version"] == v and stream["n"] == 1500 and stream["alarm"] and stream["log10_martingale"] > 2
    assert all(len(p) == 2 for p in stream["trajectory"]) and len(stream["trajectory"]) < MAX_POINTS
    assert client.get(f"/ratchet/drift?version={v + 1}").json()["streams"] == []
    assert client.get(f"/ratchet/drift?version={v}").json()["streams"] == [stream]
    assert client.get("/ratchet/drift?version=x").status_code == 422
    block = client.get("/ratchet/stats").json()["drift"]
    assert block == {"enabled": True, "threshold": 100.0, "family_bound": 0.01, "alarms": 1, "streams": 1}
    off = gw_env(RATCHET_DRIFT_ALARM="0")
    assert TestClient(off.app).get("/ratchet/drift").json()["enabled"] is False
    assert TestClient(off.app).get("/ratchet/stats").json()["drift"] == {
        "enabled": False,
        "threshold": None,
        "family_bound": None,
        "alarms": 0,
        "streams": 0,
    }


@pytest.mark.req("FR-16.6", "NFR-56")
def test_task_headers_sent_to_the_gateway_hold_a_bounded_number_of_streams(gw_env):
    """docs/THREAT_MODEL.md T12 end to end: unauthenticated requests to the mock gateway, each with an invented
    X-Ratchet-Task, are distilled and log a confidence, but the monitor holds MAX_STREAMS + 1 streams for the version
    and `/ratchet/stats` reports that count and its family bound."""
    gw = gw_env()
    client = TestClient(gw.app)
    led = gw.rt.ledger
    v = _version(led, "live", {"threshold": 0.5, "precision": 0.97, "coverage": 0.8})
    gw.rt.fleet.train([], BASE_MODEL, led.version(v).adapter_uri)  # the mock student serves this adapter
    msgs = gw.rt.stream.sample(1).messages()
    extra = 24
    for i in range(MAX_STREAMS + extra):
        r = client.post("/v1/chat/completions", json={"model": "ratchet", "messages": msgs}, headers={"X-Ratchet-Task": f"x-{i}"})
        assert r.status_code == 200 and r.json()["ratchet"]["student_version"] == v
    block = client.get("/ratchet/stats").json()["drift"]
    assert block["streams"] == MAX_STREAMS + 1 and block["family_bound"] == (MAX_STREAMS + 1) / 100
    streams = client.get(f"/ratchet/drift?version={v}").json()["streams"]
    assert len(streams) == MAX_STREAMS + 1 and [s["n"] for s in streams if s["task"] == OTHER_TASKS] == [extra]


@pytest.mark.req("FR-16.6")
def test_a_restarted_gateway_replays_the_ledger_at_startup(tmp_path, monkeypatch, gw):
    """The live Runtime feeds its monitor from the ledger file when it starts, so the first request does not pay for the
    replay, and an alarm already recorded is not written again."""
    from ratchet.config import Settings

    path = str(tmp_path / "ratchet.db")
    led = Ledger(path, auto_day=True)
    v = _version(led, "live", CERTIFIED)
    _log(led, v, _confidences(4, 1000))
    _log(led, v, _confidences(5, 500, drop_at=0))
    ReleaseGate(led).check_drift()
    assert len([e for e in led.events() if e["kind"] == "drift"]) == 1
    rt = gw.Runtime(Settings(mode="live", db_path=path, teacher_api_key="k"))
    assert rt.gate.drift.alarmed_versions() == {v} and rt.gate.drift.last_id == 1500
    assert len([e for e in rt.ledger.events() if e["kind"] == "drift"]) == 1
    assert rt.ratchet.audit_boost is rt.gate.audit_boost


def test_sqlite_stores_no_nan():
    """A NaN confidence cannot reach the monitor: SQLite stores it as NULL, which `confidence_stream` leaves out."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (x REAL)")
    db.execute("INSERT INTO t VALUES (?)", (math.nan,))
    assert db.execute("SELECT x FROM t").fetchone()[0] is None


@pytest.mark.req("FR-16.6", "NFR-55")
def test_an_up_to_date_monitor_reads_one_query_per_update():
    """After its replay, an update with new rows is one primary-key range query; the versions' states are read again
    only every COMPACT_EVERY rows (to drop the counts of versions that stopped serving)."""
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    _log(led, v, _confidences(1, 50))
    calls: Counter = Counter()

    class Counting:
        def confidence_stream(self, *a, **kw):
            calls["confidence_stream"] += 1
            return led.confidence_stream(*a, **kw)

        def version_states(self):
            calls["version_states"] += 1
            return led.version_states()

    m = DriftMonitor()
    m.update(Counting())
    assert calls == {"confidence_stream": 1, "version_states": 1}  # the replay compacts once
    for _ in range(COMPACT_EVERY - 1):
        _log(led, v, [0.9])
        m.update(Counting())
    assert calls == {"confidence_stream": COMPACT_EVERY, "version_states": 1}
    _log(led, v, [0.9])
    m.update(Counting())
    assert calls == {"confidence_stream": COMPACT_EVERY + 1, "version_states": 2}
    m.update(Counting())  # nothing new: one query, nothing else
    assert calls == {"confidence_stream": COMPACT_EVERY + 2, "version_states": 2}


@pytest.mark.req("FR-16.6")
def test_concurrent_checks_write_each_alarm_once():
    """Scout calls the router from several threads and a mock night checks drift while requests tick: however the checks
    interleave, each row is observed once, each alarm is written once, and the capital is a plain replay's."""
    led = Ledger(":memory:")
    v = _version(led, "live", CERTIFIED)
    _log(led, v, _confidences(4, 1000), day=4)
    _log(led, v, _confidences(5, 500, drop_at=0), day=5)
    gate = ReleaseGate(led, ReleasePolicy(drift_audit_boost=3.0))
    found: list = []
    barrier = threading.Barrier(6)

    def check():
        barrier.wait()
        found.extend(gate.check_drift())

    threads = [threading.Thread(target=check) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [(a.version, a.task) for a in found] == [(v, TASK)]
    assert len([e for e in led.events() if e["kind"] == "drift"]) == 1
    replay = DriftMonitor()
    replay.update(led)
    assert _state(gate.drift) == _state(replay) and gate.audit_boost == {v: 3.0}


# ---------- the opt-in actions: the audit boost (FR-6.40) and the early night (FR-6.41) ----------
class _CountingRandom(random.Random):
    draws = 0

    def random(self) -> float:
        self.draws += 1
        return super().random()


@pytest.mark.req("FR-6.40")
def test_audit_boost_raises_only_the_alarmed_versions_rate_with_one_draw_per_answer():
    """A version in `Ratchet.audit_boost` is audited at its rate times the boost (capped at 0.5), others at their own
    rate. Still one draw per served answer, so the same seed sends the same requests to each version, and `label_p`
    records the rate each answer was drawn at."""
    a = json.dumps({"category": "billing", "priority": "high"})

    def run(boost: dict[int, float]) -> tuple[list[tuple], int]:
        led = Ledger(":memory:")
        _version(led, "live", {"threshold": 0.5})
        _version(led, "canary", {"threshold": 0.5}, pct=50)
        s = Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.1)
        provider = RecordingProvider(a)
        r = Ratchet(s, led, provider, lambda v: (provider, v.adapter_uri), seed=5)
        r.rng = _CountingRandom(5)
        r.audit_boost = boost
        for i in range(400):
            assert r.handle([{"role": "user", "content": f"ticket {i}"}], TASK).served_by == "student"
        rows = led.db.execute("SELECT student_version, label_p FROM requests ORDER BY id").fetchall()
        return [tuple(row) for row in rows], r.rng.draws

    plain, plain_draws = run({})
    boosted, draws = run({2: 3.0})
    capped, capped_draws = run({2: 10.0})
    assert draws == plain_draws == capped_draws == 800  # the canary pick and the audit draw, per request
    assert [v for v, _ in boosted] == [v for v, _ in plain] == [v for v, _ in capped]
    assert set(plain) == {(1, 0.1), (2, 0.1)}
    assert {(v, round(p, 9)) for v, p in boosted} == {(1, 0.1), (2, 0.3)}
    assert set(capped) == {(1, 0.1), (2, 0.5)}


@pytest.mark.req("FR-6.40")
def test_gate_boosts_an_alarmed_serving_version_until_a_newer_one_goes_live():
    """With drift_audit_boost above 1, an alarm boosts its version while it serves (canary or live), in the dict the
    router reads; a newer version going live clears it. Off by default: an alarm alone changes no audit rate."""
    led = Ledger(":memory:")
    v1 = _version(led, "live", CERTIFIED)
    _log(led, v1, _confidences(4, 1000), day=4)
    _log(led, v1, _confidences(5, 500, drop_at=0), day=5)
    default = ReleaseGate(led)
    default.tick()
    assert default.drift.alarmed_versions() == {v1} and default.audit_boost == {}
    gate = ReleaseGate(led, ReleasePolicy(drift_audit_boost=3.0))
    boost = gate.audit_boost
    gate.tick()
    assert gate.audit_boost == {v1: 3.0} and gate.audit_boost is boost  # changed in place: the router holds this dict
    v2 = _version(led, "live", CERTIFIED)
    led.set_version(v1, "retired", 0)
    _log(led, v2, _confidences(6, 20), day=6)
    gate.tick()
    assert gate.audit_boost == {} and gate.audit_boost is boost
    assert len([e for e in led.events() if e["kind"] == "drift"]) == 1  # the alarm stays recorded


def _drifting(led: Ledger, vid: int, *, drop: bool) -> None:
    """1,000 ordinary confidences for `vid`, then 500 that drop (a launch) or 500 more ordinary ones."""
    _log(led, vid, _confidences(4, 1000), day=4)
    _log(led, vid, _confidences(5, 500, drop_at=0 if drop else None), day=5)


@pytest.mark.req("FR-6.41", "INV-5")
def test_early_night_halves_the_new_examples_needed_only_after_an_alarm():
    """With drift_early_night=1, Night School needs half of min_new_examples when a `drift` event is newer than the last
    `trained` one; it checks the monitor itself, so a nightly job in its own process sees the alarm. Without an alarm,
    with the action off, or once a night has trained after the alarm, it needs the usual number."""
    led = Ledger(":memory:", golden_fraction=0.3)
    live = _version(led, "live", {"precision": 0.97, "coverage": 0.8}, train_examples=0)
    _store_examples(led, train=30, golden=10, prefix="day 4")
    _drifting(led, live, drop=False)
    early = ReleasePolicy(drift_early_night=1)
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, ReleaseGate(led, early), BASE_MODEL)
    assert (rep.trained, rep.reason) == (False, "not enough new data") and trainer.calls == []
    assert led.events()[-1]["detail"].startswith("only 30 new examples (< 50); no training")

    _log(led, live, _confidences(6, 500, drop_at=0), day=5)  # the launch: the gateway's next tick records the alarm
    ReleaseGate(led).tick()
    rep = run_night(led, trainer, evaluator, ReleaseGate(led), BASE_MODEL)  # the action is off by default
    assert (rep.trained, rep.reason) == (False, "not enough new data")
    assert [e["kind"] for e in led.events()][-2:] == ["drift", "skip"] and "(< 50); no training" in led.events()[-1]["detail"]

    rep = run_night(led, trainer, evaluator, ReleaseGate(led, early), BASE_MODEL)
    assert rep.trained and len(trainer.calls) == 1 and rep.reason == "rejected"  # REJECTED_EVAL: nothing left in flight
    (trained,) = [e for e in led.events() if e["kind"] == "trained"]
    assert trained["detail"].endswith("; early night after a drift alarm (30 new examples, normally 50)")

    _store_examples(led, train=30, golden=5, prefix="day 6")  # 30 more, but no alarm since that night trained
    rep = run_night(led, trainer, evaluator, ReleaseGate(led, early), BASE_MODEL)
    assert (rep.trained, rep.reason) == (False, "not enough new data") and len(trainer.calls) == 1
    assert led.events()[-1]["detail"].startswith("only 30 new examples (< 50); no training")


@pytest.mark.req("FR-6.41", "INV-5")
def test_early_night_never_trains_while_a_release_is_in_flight():
    """An alarm on a canary does not let Night School train: the in-flight check comes first (INV-5)."""
    led = Ledger(":memory:", golden_fraction=0.3)
    canary = _version(led, "canary", {"precision": 0.97, "coverage": 0.8})
    _store_examples(led, train=200, golden=20)
    _drifting(led, canary, drop=True)
    gate = ReleaseGate(led, ReleasePolicy(drift_early_night=1))
    assert gate.check_drift()[0].version == canary
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(REJECTED_EVAL)
    rep = run_night(led, trainer, evaluator, gate, BASE_MODEL)
    assert (rep.trained, rep.reason) == (False, "release in flight")
    assert trainer.calls == [] and evaluator.calls == []
    assert led.events()[-1]["detail"] == f"v{canary} still in canary; no training tonight (saves GPU time)"


@pytest.mark.req("FR-6.41")
def test_an_alarm_found_by_a_replay_keeps_its_day_and_counts_only_after_the_last_training():
    """A monitor that replays the ledger (the first start after an upgrade, a lowered threshold, a nightly job in its own
    process) finds an old alarm late. Its event is dated on the alarm's day, and an alarm from before the last training
    brings no early night, even though its event is newer; a change after that training does, even when only Night
    School's own check finds it."""
    led = Ledger(":memory:")
    v1 = _version(led, "live", CERTIFIED)
    _log(led, v1, _confidences(4, 1000), day=2)
    _log(led, v1, _confidences(5, 500, drop_at=0), day=3)  # a launch on day 3 under v1; no gate ticked then
    v2 = _version(led, "live", CERTIFIED)
    led.set_version(v1, "retired", 0)
    led.day = 9
    led.event("trained", f"v{v2} on 300 examples")  # v2 trained after the launch
    _log(led, v2, _confidences(6, 1000), day=9)
    gate = ReleaseGate(led, ReleasePolicy(drift_early_night=1))
    assert early_night(led, gate, 50) == (50, "")
    (event,) = [e for e in led.events() if e["kind"] == "drift"]
    assert event["day"] == 3 and event["detail"].startswith(f"v{v1} ({TASK}): ")
    assert event["id"] > led.last_event("trained")["id"]  # what the old rule compared, and got wrong
    _log(led, v2, _confidences(7, 500, drop_at=0), day=10)  # traffic changes after that training
    assert early_night(led, gate, 50) == (25, " (halved from 50 after a drift alarm)")
    assert [e["day"] for e in led.events() if e["kind"] == "drift"] == [3, 10]


# ---------- the simulator (simulation, mock models) ----------
@functools.cache
def _sim(seed: int, drift_day: int, days: int, requests: int, alarm: float = 100.0) -> dict:
    """One simulated run, cached for the module: tests read it, never change it."""
    argv = ["--seed", str(seed), "--days", str(days), "--requests", str(requests), "--drift-day", str(drift_day)]
    return simulate.run(simulate.parser().parse_args([*argv, "--drift-alarm", str(alarm)]))


def _drift_days(res: dict) -> list[int]:
    return [e["day"] for e in res["events"] if e["kind"] == "drift"]


@pytest.mark.req("FR-16.6", "FR-9.4")
def test_sim_alarms_on_the_launch_day(reference_run):
    """The ticket launch on day 5 raises the alarm on day 5 or 6 and never before (seed 7 at 2,000 requests a day: the
    reference run, with its corrupted v3, which is no traffic change and raises none; seed 11 at 1,000)."""
    for res in (reference_run, _sim(11, 5, 6, 1000)):
        days = _drift_days(res)
        assert days and days[0] in (5, 6) and min(days) >= 5, days
        alarmed = [s for s in res["drift"]["streams"] if s["alarm"]]
        assert alarmed and alarmed[0]["alarm_day"] == days[0] and res["drift"]["enabled"]
    assert {s["version"] for s in reference_run["drift"]["streams"] if s["alarm"]} == {1}


@pytest.mark.req("FR-16.6")
@pytest.mark.parametrize("seed", [7, 11])
def test_sim_never_alarms_without_drift(seed):
    res = _sim(seed, 0, 6, 1000)
    assert _drift_days(res) == [] and res["drift"]["monitored"] >= 1 and res["drift"]["alarms"] == 0


@pytest.mark.req("FR-16.6", "NFR-55")
def test_monitor_draws_nothing_from_the_router_rng():
    """The same seeded run with the monitor on and off (RATCHET_DRIFT_ALARM=0) gives the same days, versions and
    events, apart from the `drift` event itself: the monitor reads the ledger and draws from its own generators."""
    on, off = _sim(11, 5, 6, 1000), _sim(11, 5, 6, 1000, alarm=0.0)
    assert _drift_days(on) and not _drift_days(off) and off["drift"]["enabled"] is False
    assert on["days"] == off["days"] and on["versions"] == off["versions"] and on["totals"] == off["totals"]
    strip = [(e["day"], e["kind"], e["detail"]) for e in on["events"] if e["kind"] != "drift"]
    assert strip == [(e["day"], e["kind"], e["detail"]) for e in off["events"]]


@pytest.mark.slow
@pytest.mark.req("FR-16.6")
@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_sim_alarm_sweep(seed):
    """Seeds 1, 2, 3, 7 and 11 at 2,000 requests a day: the launch on day 5 alarms on day 5 or 6, and six days
    without drift never alarm."""
    days = _drift_days(_sim(seed, 5, 8, 2000))
    assert days and days[0] in (5, 6) and min(days) >= 5, days
    assert _drift_days(_sim(seed, 0, 6, 2000)) == []


def test_sim_flags_reach_the_policy_and_the_router(capsys):
    """--drift-alarm, --drift-boost and --drift-early-night set the gate's policy, the router reads the gate's boosts,
    and settings that would do nothing are refused."""
    args = simulate.parser().parse_args(["--drift-alarm", "50", "--drift-boost", "3", "--drift-early-night"])
    _settings, _ledger, _fleet, ratchet, gate = simulate.build(args)
    assert (gate.policy.drift_alarm, gate.policy.drift_audit_boost, gate.policy.drift_early_night) == (50.0, 3.0, 1)
    assert ratchet.audit_boost is gate.audit_boost and gate.drift.threshold == 50
    for argv, says in (
        (["--drift-alarm", "1"], "--drift-alarm must be more than 1, or 0"),
        (["--drift-boost", "0.5"], "--drift-boost must be 1 or more"),
        (["--drift-alarm", "0", "--drift-early-night"], "--drift-alarm 0 turns the monitor off"),
    ):
        with pytest.raises(SystemExit):
            simulate.main(argv)
        assert says in capsys.readouterr().err
    table = simulate.table(_sim(11, 5, 6, 1000))
    assert "drift (simulation, mock models): " in table and "alarms: v1 (ticket-triage) on day 5" in table
    assert "[drift] v1 (ticket-triage): confidence on recent traffic" in table


def test_false_alarm_bounds_keep_three_significant_digits():
    """A threshold above 200 has a bound below 0.5%, which a whole-percent format would print as 0%."""
    assert drift.pct(1 / 1000) == "0.1%" and drift.pct(1 / 100) == "1%" and drift.pct(17 / 1000) == "1.7%"
    args = simulate.parser().parse_args(["--days", "2", "--requests", "300", "--drift-alarm", "1000"])
    assert "(false alarm <= 0.1% each, <= 0.1% in all)" in simulate.table(simulate.run(args))
