"""The hot path (FR-12.8, NFR-1, ADR 0060): the ledger's WAL journal and read-only connection, the indexes the release
gate searches, the version routing snapshot, the incremental audit tallies, the cached BOM digest and day percentiles,
the throttled tick, the cached /ratchet/stats, and `requests.wall_ms`.

Timing tests use zero-latency fakes and generous bounds (each ratio or ceiling is several times what a laptop
measures), and take the best of a few runs, so a busy CI machine does not fail them; together they run in a few
seconds."""

from __future__ import annotations

import json
import random
import sqlite3
import statistics
import threading
import time
from typing import Any

import pytest
from fakes import OPEN_TEACHER, FakeClock, FakeOpenAIServer, RecordingProvider, _audit, _examples, _version
from fastapi.testclient import TestClient

from ratchet import config
from ratchet.config import Settings
from ratchet.ledger import AUDIT_TALLY_SQL, ROUTED_SQL, ROUTED_STATES, SHADOW_QUALITY_SQL, SYNC_MODES, Ledger
from ratchet.providers import OpenAICompatProvider
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet

CHAT = "/v1/chat/completions"
A = json.dumps({"category": "billing", "priority": "high", "reply": "I am checking the charge now."})
TRIAGE = "ticket-triage"


def _settings(**overrides: Any) -> Settings:
    """Every value these tests depend on, set explicitly, so RATCHET_* variables in the shell cannot move a result."""
    base: dict[str, Any] = {
        "teacher_model": OPEN_TEACHER,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "redact_pii": True,
        "teacher_params": "",
        "student_params": "",
        "on_teacher_failure": "error",
        "teacher_fallback_model": "",
    }
    return Settings(**{**base, **overrides})


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"I was charged twice this month, order {tag}"}]


def _full_quality(led: Ledger, vid: int, since: int) -> tuple[int, float | None]:
    """version_live_quality as one aggregate over the stage, the way it was computed before the tallies. Only audits
    with a verdict count (`COUNT(audit_agree)`): a pending off-path audit is no evidence yet (ADR 0072)."""
    r = led.db.execute(
        "SELECT COUNT(audit_agree), AVG(audit_agree) FROM requests"
        " WHERE student_version=? AND audited=1 AND served_by='student' AND id>?",
        (vid, since),
    ).fetchone()
    return int(r[0]), (None if r[1] is None else float(r[1]))


def _plan(led: Ledger, sql: str, args: tuple[Any, ...]) -> str:
    return " | ".join(r[3] for r in led.db.execute("EXPLAIN QUERY PLAN " + sql, args))


def _timed(led: Ledger, day: int, wall: float, served_by: str = "student", **row: Any) -> int:
    return led.log_request(task="t", day=day, served_by=served_by, cost_usd=0.0, baseline_cost_usd=0.0, wall_ms=wall, **row)


# ---------- journal and connections ----------
@pytest.mark.req("FR-12.8")
def test_file_ledger_uses_wal_and_memory_does_not(tmp_path):
    """A file ledger runs in WAL with synchronous=NORMAL and a busy timeout, and reads stats on a second, read-only
    connection; an in-memory one has neither. An old rollback-journal file is switched on open, and gains wall_ms."""
    led = Ledger(str(tmp_path / "wal.db"))
    assert led.journal_mode == "wal" and led.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert led.db.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert led.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert led._ro is not None and led._rdb is led._ro
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        led._ro.execute("INSERT INTO events (kind) VALUES ('x')")
    led.log_request(task="t", served_by="teacher", cost_usd=0.0, baseline_cost_usd=0.0)
    assert led.totals()["requests"] == 1  # the reader sees the writer's commit at once
    led.close()

    mem = Ledger(":memory:")
    assert mem.journal_mode == "memory" and mem._ro is None and mem._rdb is mem.db

    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, task TEXT)")
    con.commit()
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    con.close()
    migrated = Ledger(str(old))
    assert migrated.journal_mode == "wal"
    assert "wall_ms" in {r["name"] for r in migrated.db.execute("PRAGMA table_info(requests)")}
    migrated.close()


@pytest.mark.req("FR-12.8")
def test_sync_full_keeps_the_rollback_journal_and_bad_values_stop_startup(tmp_path, monkeypatch):
    """RATCHET_DB_SYNC=FULL is v0.4's rollback journal with synchronous=FULL (also on a file that was in WAL), with no
    second connection; anything but NORMAL or FULL stops startup, naming the variable."""
    path = str(tmp_path / "full.db")
    Ledger(path).close()  # the file is in WAL now
    led = Ledger(path, sync=" full ")
    assert led.journal_mode == "delete" and led._ro is None and led._rdb is led.db
    assert led.db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    led.log_request(task="t", served_by="teacher", cost_usd=0.0, baseline_cost_usd=0.0)
    assert led.totals()["requests"] == 1
    led.close()
    for bad in ("fast", "", None, 1):
        with pytest.raises(ValueError, match="RATCHET_DB_SYNC must be NORMAL or FULL"):
            Ledger(":memory:", sync=bad)  # type: ignore[arg-type]

    assert SYNC_MODES == config.SYNC_MODES
    for raw, want in (("", "NORMAL"), ("normal", "NORMAL"), (" Full ", "FULL")):
        assert Settings(db_sync=raw).ledger_sync() == want
    monkeypatch.delenv("RATCHET_DB_SYNC", raising=False)
    assert Settings().ledger_sync() == "NORMAL"
    with pytest.raises(ValueError, match="RATCHET_DB_SYNC must be NORMAL or FULL, not 'wal'"):
        Settings(db_sync="wal").ledger_sync()
    monkeypatch.setenv("RATCHET_DB_SYNC", "off")
    with pytest.raises(ValueError, match="RATCHET_DB_SYNC"):
        Settings(mode="mock", mock_warmup_days=0).ledger_sync()


@pytest.mark.req("FR-12.8")
def test_full_cannot_take_a_file_out_of_wal_while_another_process_holds_it(tmp_path):
    """Leaving WAL needs the file to itself: FULL on a file another connection holds open in WAL stops with an error
    that names the setting, instead of a half-switched ledger. Joining WAL works with others open."""
    path = str(tmp_path / "shared.db")
    holder = Ledger(path)
    holder.live()  # an open read of the WAL file
    with pytest.raises(sqlite3.OperationalError, match="RATCHET_DB_SYNC=FULL"):
        Ledger(path, sync="FULL")
    other = Ledger(path)  # NORMAL alongside: fine
    assert other.journal_mode == "wal"
    other.close()
    holder.close()


# ---------- indexes ----------
@pytest.mark.req("FR-12.8")
def test_gate_queries_search_their_indexes():
    """EXPLAIN QUERY PLAN for the release gate's queries as the ledger runs them: the audit tally searches
    requests_version_audited from the last id it read, the shadow verdicts requests_shadow_version, the routing
    rebuild versions_state, and a day's percentiles requests_day. None scans the requests table."""
    led = Ledger(":memory:")
    tally = _plan(led, AUDIT_TALLY_SQL, (1, 100))
    assert "USING INDEX requests_version_audited (student_version=? AND audited=? AND id>?)" in tally
    assert "USING INDEX requests_shadow_version (shadow_version=?)" in _plan(led, SHADOW_QUALITY_SQL, (1,))
    assert "USING INDEX versions_state" in _plan(led, ROUTED_SQL, ROUTED_STATES)
    q, args = led._where_wall(3, "student")
    assert "USING INDEX requests_day (day=?)" in _plan(led, f"SELECT wall_ms{q} ORDER BY wall_ms LIMIT 1 OFFSET ?", (*args, 0))
    for sql, params in ((AUDIT_TALLY_SQL, (1, 0)), (SHADOW_QUALITY_SQL, (1,))):
        assert "SCAN requests" not in _plan(led, sql, params)


# ---------- the version routing snapshot ----------
@pytest.mark.req("FR-12.8")
def test_version_cache_sees_another_connections_writes(tmp_path):
    """Two ledgers on one file, as the gateway and the cron tick: what the second one writes (a new live version, a
    canary step, a rollback) the first sees on its very next read, through PRAGMA data_version."""
    path = str(tmp_path / "two.db")
    gateway, cron = Ledger(path), Ledger(path)
    assert gateway.live() is None and gateway.by_state("canary") is None  # cached: no versions yet
    v1 = _version(cron, "live", {"threshold": 0.5})
    assert gateway.live().id == v1
    v2 = _version(cron, "canary", {"threshold": 0.6})
    assert gateway.by_state("canary").traffic_pct == 10
    cron.set_version(v2, "canary", 50)
    assert gateway.by_state("canary").traffic_pct == 50
    cron.set_version(v2, "rolled_back", 0)
    assert gateway.by_state("canary") is None and gateway.live().id == v1
    cron.update_eval(v1, {"threshold": 0.7})
    assert gateway.live().eval == {"threshold": 0.7}
    cron.close()
    gateway.close()


@pytest.mark.req("FR-12.8")
def test_version_cache_invalidates_on_state_change():
    """Every version write invalidates the routing snapshot: the ledger's own writers and SQL on `ledger.db` alike (a
    TEMP trigger counts them). Each read is a fresh copy, so a caller changing one cannot change the next."""
    led = Ledger(":memory:")
    assert led.live() is None
    v1 = _version(led, "live", {"threshold": 0.5})
    assert led.live().id == v1 and led.live().stage_since == 0
    led.update_eval(v1, {"threshold": 0.55})
    assert led.live().eval == {"threshold": 0.55}
    v2 = _version(led, "canary", {"threshold": 0.6})
    assert (led.by_state("canary").id, led.by_state("canary").traffic_pct) == (v2, 10)
    led.set_version(v2, "canary", 50)
    assert led.by_state("canary").traffic_pct == 50
    led.set_version(v2, "live", 100)
    led.set_version(v1, "retired", 0)
    assert led.live().id == v2 and led.by_state("canary") is None
    assert led.by_state("retired").id == v1  # not a routed state: one indexed query
    v3 = _version(led, "shadow")
    assert led.by_state("shadow").id == v3
    led.set_bom(v3, {"schema": "x"})
    assert led.by_state("shadow").id == v3
    led.db.execute("UPDATE versions SET state='rejected' WHERE id=?", (v3,))  # not through a Ledger writer
    led.db.commit()
    assert led.by_state("shadow") is None
    led.db.execute("DELETE FROM versions WHERE id=?", (v2,))
    led.db.commit()
    assert led.live() is None
    v4 = _version(led, "live", {"threshold": 0.5, "nested": {"a": 1}})
    copy = led.live()
    copy.eval["nested"]["a"] = 99
    copy.state = "retired"
    assert led.live().eval == {"threshold": 0.5, "nested": {"a": 1}} and led.live().state == "live"
    assert led.live() == led.versions(["live"])[-1] and led.live().id == v4
    assert led.version(v4) == led.live() and led.version(10_000) is None


@pytest.mark.req("FR-12.8")
def test_routing_reads_no_version_rows_until_a_version_changes():
    """After the first read, routing a request costs one PRAGMA per lookup and no version row, however many versions
    there are; the next version write makes exactly the next read rebuild."""
    led = Ledger(":memory:")
    for _ in range(30):
        _version(led, "retired")
    vid = _version(led, "live", {"threshold": 0.5})
    led.live()
    seen: list[str] = []
    led.db.set_trace_callback(seen.append)
    for _ in range(10):
        assert led.live().id == vid
        led.by_state("canary"), led.by_state("shadow")
    assert seen and all("data_version" in s for s in seen)
    led.set_version(vid, "retired", 0)
    seen.clear()
    assert led.live() is None
    assert sum("FROM versions" in s for s in seen) == 1


# ---------- incremental audit tallies ----------
@pytest.mark.req("FR-12.8")
def test_live_quality_tally_matches_a_full_count(tmp_path):
    """version_live_quality equals one COUNT and AVG over the stage at every step: new audits (read above the last
    id only), audits without a verdict (pending off-path audits: counted in neither number, ADR 0072), other versions'
    rows, a new stage, a deleted row on this connection, and rows another connection commits."""
    path = str(tmp_path / "tally.db")
    led = Ledger(path)
    vid = _version(led, "live", {"threshold": 0.5})
    other = _version(led, "canary", {"threshold": 0.5})

    def check() -> tuple[int, float | None]:
        got = led.version_live_quality(vid)
        assert got == _full_quality(led, vid, led.version(vid).stage_since)
        assert led.version_live_quality(vid, current_stage_only=False) == _full_quality(led, vid, 0)
        return got

    assert check() == (0, None)
    _audit(led, vid, 7, 3)  # 4 of 7 agree
    assert check() == (7, 4 / 7)
    top = led.db.execute("SELECT MAX(id) FROM requests").fetchone()[0]
    assert led._tallies[(vid, 0)].last_id == top  # the next call reads only rows above it
    led.log_request(task="t", served_by="student", student_version=vid, audited=1, audit_agree=None, cost_usd=0.0)
    led.log_request(task="t", served_by="teacher", student_version=vid, audited=1, audit_agree=0, cost_usd=0.0)
    _audit(led, other, 5)
    assert check() == (7, 4 / 7)  # the pending audit counts once it has a verdict
    led.set_version(vid, "live", 100)  # a new stage starts after the last request
    assert check() == (0, None)
    _audit(led, vid, 4, 2)
    assert check() == (4, 0.5)
    since = led.version(vid).stage_since
    first = led.db.execute(
        "SELECT MIN(id) FROM requests WHERE student_version=? AND audit_agree=0 AND id>?", (vid, since)
    ).fetchone()[0]
    led.db.execute("DELETE FROM requests WHERE id=?", (first,))
    led.db.commit()
    assert check() == (3, 2 / 3)
    cron = Ledger(path)  # another process (the cron tick, a CLI) commits rows
    _audit(cron, vid, 3)
    cron.close()
    assert check() == (6, 5 / 6)
    led.close()


# ---------- reader-side caches ----------
@pytest.mark.req("FR-12.8")
def test_bom_digest_is_cached_until_examples_change(tmp_path):
    """bill_of_materials hashes every example only when one was added, deleted or had its hash rewritten (on this or
    another connection); a duplicate (`seen` bumped) changes nothing; the digest always equals a fresh computation."""
    led = Ledger(str(tmp_path / "bom.db"))
    stored = _examples(led, train=6, golden=2)

    def fresh() -> str:
        import hashlib

        digest = hashlib.sha256()
        for (h,) in led.db.execute("SELECT hash FROM examples ORDER BY hash"):
            digest.update(h.encode())
        return digest.hexdigest()

    def digest_reads() -> tuple[str, int]:
        seen: list[str] = []
        led._ro.set_trace_callback(seen.append)
        try:
            sha = led.bill_of_materials()["examples_sha256"]
        finally:
            led._ro.set_trace_callback(None)
        return sha, sum("ORDER BY hash" in s for s in seen)

    assert digest_reads() == (fresh(), 1)
    assert digest_reads() == (fresh(), 0)  # cached
    led.add_example("t", _ask("dup"), "a", OPEN_TEACHER, "OpenMDW-1.1", "")
    assert digest_reads() == (fresh(), 1)
    led.add_example("t", _ask("dup"), "a", OPEN_TEACHER, "OpenMDW-1.1", "")  # a duplicate: seen + 1, same hashes
    assert digest_reads() == (fresh(), 0)
    led.db.execute("UPDATE examples SET hash=? WHERE hash=?", ("f" * 64, stored[0].hash))
    led.db.commit()
    assert digest_reads() == (fresh(), 1)
    led.db.execute("DELETE FROM examples WHERE hash=?", ("f" * 64,))
    led.db.commit()
    assert digest_reads() == (fresh(), 1)
    elsewhere = Ledger(led.path)
    elsewhere.add_example("t", _ask("elsewhere"), "a", OPEN_TEACHER, "OpenMDW-1.1", "")
    elsewhere.close()
    assert digest_reads() == (fresh(), 1)
    led.db.execute("UPDATE examples SET hash=? WHERE hash=?", ("e" * 64, stored[1].hash))  # not committed yet
    assert led.bill_of_materials()["examples_sha256"] == digest_reads()[0]  # nothing cached against it
    led.db.commit()
    assert digest_reads() == (fresh(), 1)
    led.close()


@pytest.mark.req("FR-12.8")
def test_events_last_reads_only_the_newest():
    led = Ledger(":memory:")
    for i in range(50):
        led.event("kind", f"event {i}")
    assert led.events(last=40) == led.events()[-40:]
    assert led.events(last=0) == [] and led.events(last=500) == led.events()
    for bad in (-1, True, 2.5):
        with pytest.raises(ValueError, match="last must be a whole number"):
            led.events(last=bad)  # type: ignore[arg-type]


@pytest.mark.req("FR-12.8")
def test_stats_reads_skip_the_writer_lock(tmp_path):
    """On a WAL file every stats query runs on the read-only connection: it returns while another thread holds the
    writer's lock (a slow write, Night School), so a dashboard never waits for a request, nor a request for it."""
    led = Ledger(str(tmp_path / "busy.db"))
    _timed(led, 1, 120.0)
    led.event("kind", "detail")
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with led._lock:
            holding.set()
            release.wait(10)

    writer = threading.Thread(target=hold)
    writer.start()
    assert holding.wait(5)
    out: dict[str, Any] = {}

    def read() -> None:
        out["days"] = led.stats_by_day()
        out["tasks"] = led.stats_by_task()
        out["clients"] = led.stats_by_client()
        out["totals"] = led.totals()
        out["privacy"] = led.privacy_stats()
        out["bom"] = led.bill_of_materials()
        out["boms"] = led.bom_status()
        out["events"] = led.events(last=5)
        out["cost"] = led.cost_by_day()
        out["latency"] = led.latency_percentiles()

    try:
        reader = threading.Thread(target=read)
        reader.start()
        reader.join(5)
        assert not reader.is_alive(), "a stats read waited for the writer's lock"
    finally:
        release.set()
        writer.join()
    assert out["totals"]["requests"] == 1 and out["days"][0]["p95_ms"] == 120.0 and len(out["events"]) == 1
    led.close()


# ---------- the throttled tick ----------
def _counting(rt: Any) -> list[int]:
    calls: list[int] = []
    real = rt.gate.tick

    def tick() -> None:
        calls.append(1)
        real()

    rt.gate.tick = tick
    return calls


@pytest.mark.req("FR-12.8")
def test_tick_throttle_runs_the_gate_once_per_interval():
    """With RATCHET_TICK_INTERVAL_S=5, two ticks within 5 s run the gate once; a tick while another thread is ticking
    is skipped without waiting; `force` (the mock demo's simulated days) always runs; 0 runs every call."""
    from ratchet import gateway as gw

    clock = FakeClock()
    rt = gw.Runtime(Settings(mode="mock", mock_warmup_days=0, tick_interval_s=5.0, stats_ttl_s=0.0), clock=clock)
    calls = _counting(rt)
    assert rt.tick() is True and rt.tick() is False and len(calls) == 1
    clock.advance(4.999)
    assert rt.tick() is False
    clock.advance(0.001)
    assert rt.tick() is True and len(calls) == 2
    clock.advance(60)
    rt.gate_lock.acquire()  # another thread is ticking
    try:
        started = time.perf_counter()
        assert rt.tick() is False
        assert time.perf_counter() - started < 1  # it did not wait
    finally:
        rt.gate_lock.release()
    assert rt.tick() is True and len(calls) == 3
    assert rt.tick(force=True) is True and len(calls) == 4

    rt.simulate_day(100, "tickets")  # simulated time: every tenth request ticks, whatever the wall clock says
    assert len(calls) == 4 + 10

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("gate failed")

    rt.gate.tick = boom
    clock.advance(60)
    with pytest.raises(RuntimeError):
        rt.tick()
    assert rt.tick() is False  # a failing tick is not retried on every request

    every = gw.Runtime(Settings(mode="mock", mock_warmup_days=0, tick_interval_s=0.0, stats_ttl_s=0.0), clock=clock)
    calls = _counting(every)
    assert [every.tick() for _ in range(3)] == [True] * 3 and len(calls) == 3


@pytest.mark.req("FR-12.8")
def test_request_path_ticks_at_most_once_per_interval(gw_env):
    """Through the gateway: requests within the interval share one tick; the next request after it ticks again."""
    gw = gw_env(RATCHET_TICK_INTERVAL_S="5", RATCHET_STATS_TTL_S="0")
    clock = FakeClock()
    gw.rt.clock = clock
    calls = _counting(gw.rt)
    c = TestClient(gw.app)
    msgs = gw.rt.stream.sample(1).messages()
    for _ in range(3):
        assert c.post(CHAT, json={"messages": msgs}).status_code == 200
    assert len(calls) == 1
    clock.advance(5)
    assert c.post(CHAT, json={"messages": msgs}).status_code == 200
    assert len(calls) == 2


# ---------- the cached /ratchet/stats ----------
@pytest.mark.req("FR-12.8")
def test_snapshot_cache_ttl_and_single_flight():
    """One value per TTL; while one caller recomputes, the others get the previous value at once (only the first
    callers wait, having nothing to serve); a failed recompute raises and keeps the previous value; TTL 0 computes
    every time."""
    from ratchet.gateway import SnapshotCache

    clock = FakeClock()
    cache = SnapshotCache(2.0, clock=clock)
    n = iter(range(100))
    assert cache.get(lambda: next(n)) == 0 and cache.get(lambda: next(n)) == 0
    clock.advance(1.99)
    assert cache.get(lambda: next(n)) == 0
    clock.advance(0.01)
    assert cache.get(lambda: next(n)) == 1

    clock.advance(5)
    entered, finish = threading.Event(), threading.Event()

    def slow() -> str:
        entered.set()
        assert finish.wait(5)
        return "new"

    worker = threading.Thread(target=lambda: cache.get(slow))
    worker.start()
    assert entered.wait(5)
    assert cache.get(lambda: "never") == 1  # the previous value, at once, while `slow` runs
    finish.set()
    worker.join()
    assert cache.get(lambda: "never") == "new"

    clock.advance(5)

    def fail() -> str:
        raise RuntimeError("ledger busy")

    with pytest.raises(RuntimeError):
        cache.get(fail)
    assert cache.get(lambda: "again") == "again"

    first = SnapshotCache(2.0, clock=clock)
    entered.clear()
    finish.clear()
    got: list[str] = []
    worker = threading.Thread(target=lambda: got.append(first.get(slow)))
    worker.start()
    assert entered.wait(5)
    waiter = threading.Thread(target=lambda: got.append(first.get(lambda: "never")))
    waiter.start()
    waiter.join(0.2)
    assert waiter.is_alive()  # nothing to serve yet: it waits for the first value
    finish.set()
    worker.join()
    waiter.join(5)
    assert got == ["new", "new"]

    every = SnapshotCache(0.0, clock=clock)
    m = iter(range(3))
    assert [every.get(lambda: next(m)) for _ in range(3)] == [0, 1, 2]


@pytest.mark.req("FR-12.8")
def test_stats_ttl_returns_the_cached_payload(gw_env):
    """With RATCHET_STATS_TTL_S=2 a read within 2 s of the last computation returns the same payload, a request in
    between notwithstanding; the next read after the TTL shows it. A demo reset starts a fresh cache."""
    gw = gw_env(RATCHET_STATS_TTL_S="2", RATCHET_TICK_INTERVAL_S="0")
    clock = FakeClock()
    gw.rt.stats_cache.clock = clock
    c = TestClient(gw.app)
    before = c.get("/ratchet/stats").json()
    assert before["totals"]["requests"] == 0
    assert c.post(CHAT, json={"messages": gw.rt.stream.sample(1).messages()}).status_code == 200
    assert c.get("/ratchet/stats").json() == before
    clock.advance(2)
    assert c.get("/ratchet/stats").json()["totals"]["requests"] == 1
    gw.rt.reset()
    assert c.get("/ratchet/stats").json()["totals"]["requests"] == 0


@pytest.mark.req("FR-12.8")
def test_interval_settings_have_mode_defaults_and_bounds(monkeypatch):
    for env in ("RATCHET_TICK_INTERVAL_S", "RATCHET_STATS_TTL_S"):
        monkeypatch.delenv(env, raising=False)
    assert (Settings(mode="live").tick_interval(), Settings(mode="live").stats_ttl()) == (5.0, 2.0)
    assert (Settings(mode="mock").tick_interval(), Settings(mode="mock").stats_ttl()) == (0.0, 0.0)
    assert Settings(mode="live", tick_interval_s=0, stats_ttl_s=3600).stats_ttl() == 3600.0
    for bad in (-1, 3600.5, float("nan"), float("inf"), True):
        with pytest.raises(ValueError, match="RATCHET_TICK_INTERVAL_S must be a number of seconds from 0 to 3600"):
            Settings(tick_interval_s=bad).tick_interval()
        with pytest.raises(ValueError, match="RATCHET_STATS_TTL_S"):
            Settings(stats_ttl_s=bad).stats_ttl()
    monkeypatch.setenv("RATCHET_STATS_TTL_S", " ")
    assert Settings(mode="live").stats_ttl() == 2.0  # set but empty: the default
    monkeypatch.setenv("RATCHET_TICK_INTERVAL_S", "soon")
    with pytest.raises(ValueError, match="RATCHET_TICK_INTERVAL_S must be a number"):
        Settings()
    monkeypatch.setenv("RATCHET_TICK_INTERVAL_S", "-2")
    from ratchet import gateway as gw

    with pytest.raises(ValueError, match="RATCHET_TICK_INTERVAL_S"):
        gw.Runtime(Settings(mode="mock", mock_warmup_days=0))


# ---------- latency ----------
@pytest.mark.req("NFR-1", "FR-12.8")
def test_latency_percentiles():
    """Nearest-rank percentiles of wall_ms, exact: the value at rank ceil(q * n), filtered by day and served_by; rows
    without wall_ms (older rows) count toward nothing; None without any."""
    led = Ledger(":memory:")
    for ms in range(1, 21):  # day 1: 1..20 ms, odd ones student-served
        _timed(led, 1, float(ms), "student" if ms % 2 else "teacher")
    for ms in (500.0, 700.0, 900.0):
        _timed(led, 2, ms)
    led.log_request(task="t", day=2, served_by="student", cost_usd=0.0)  # no wall_ms: a v0.4 row
    assert led.latency_percentiles(day=1) == {0.5: 10.0, 0.95: 19.0}
    assert led.latency_percentiles(day=1, served_by="student") == {0.5: 9.0, 0.95: 19.0}  # 1, 3, ..., 19
    assert led.latency_percentiles(day=1, qs=(0, 0.01, 0.07, 1)) == {0: 1.0, 0.01: 1.0, 0.07: 2.0, 1: 20.0}
    assert led.latency_percentiles(day=2) == {0.5: 700.0, 0.95: 900.0}
    assert led.latency_percentiles() == {0.5: 12.0, 0.95: 700.0}  # 23 rows: ranks 12 and 22
    assert led.latency_percentiles(day=3) == {0.5: None, 0.95: None}
    assert led.latency_percentiles(day=1, served_by="teacher", qs=()) == {}
    for bad in (-0.1, 1.5, "0.5", True, float("nan")):
        with pytest.raises(ValueError, match="quantiles must be numbers from 0 to 1"):
            led.latency_percentiles(qs=(bad,))  # type: ignore[arg-type]


@pytest.mark.req("NFR-1")
def test_stats_by_day_reports_latency_percentiles_and_sorts_only_changed_days():
    """stats_by_day's p50_student_ms and p95_ms are latency_percentiles' numbers per day (None without wall_ms). A
    read sorts only the days whose rows changed since the last one: today's after a new request, every day after a
    rewrite on this connection."""
    led = Ledger(":memory:")
    for day in (1, 2, 3):
        for ms in range(1, 11):
            _timed(led, day, float(10 * day + ms), "student" if ms <= 5 else "teacher")
    led.log_request(task="t", day=4, served_by="student", cost_usd=0.0)  # a day with no wall_ms

    def sorts() -> tuple[list[dict[str, Any]], int]:
        seen: list[str] = []
        led.db.set_trace_callback(seen.append)
        try:
            days = led.stats_by_day()
        finally:
            led.db.set_trace_callback(None)
        return days, sum("ORDER BY wall_ms" in s for s in seen)

    days, n = sorts()
    assert n == 6
    for d in days[:3]:
        assert d["p50_student_ms"] == led.latency_percentiles(d["day"], "student")[0.5]
        assert d["p95_ms"] == led.latency_percentiles(d["day"])[0.95]
    assert (days[0]["p50_student_ms"], days[0]["p95_ms"]) == (13.0, 20.0)
    assert (days[3]["p50_student_ms"], days[3]["p95_ms"]) == (None, None)
    assert sorts()[1] == 0
    _timed(led, 3, 1000.0)  # today's traffic
    days, n = sorts()
    assert n == 2 and days[2]["p95_ms"] == 1000.0 and days[0]["p95_ms"] == 20.0
    led.db.execute("DELETE FROM requests WHERE day=1 AND wall_ms=20")
    led.db.commit()
    days, n = sorts()
    assert n == 6 and days[0]["p95_ms"] == 19.0


@pytest.mark.req("NFR-1")
def test_wall_ms_logged_and_at_least_latency():
    """Every path logs wall_ms: the whole cascade on the gateway's clock, never less than latency_ms (the providers'
    own time, measured inside it by OpenAICompatProvider), so `wall_ms - latency_ms` is Ratchet's overhead."""
    server = FakeOpenAIServer(A)
    teacher = OpenAICompatProvider("https://teacher.test/v1", "k", transport=server.transport)
    student = OpenAICompatProvider("https://student.test/v1", "k", transport=server.transport)
    led = Ledger(":memory:")
    r = Ratchet(_settings(), led, teacher, lambda v: (student, v.adapter_uri), seed=1)
    r.handle(_ask("cold"), TRIAGE)  # cold start: the teacher
    vid = _version(led, "live", {"threshold": 0.5})
    assert r.handle(_ask("served"), TRIAGE).served_by == "student"
    led.update_eval(vid, {"threshold": 1.01})
    assert r.handle(_ask("unsure"), TRIAGE).route == "confidence"
    r.handle(_ask("pinned"), "research-plan")
    r.teacher_only(_ask("image"), TRIAGE)
    rows = led.db.execute("SELECT route, wall_ms, latency_ms FROM requests ORDER BY id").fetchall()
    assert [row["route"] for row in rows] == ["cold_start", "student", "confidence", "pinned", "pinned"]
    for row in rows:
        assert row["latency_ms"] > 0 and row["wall_ms"] >= row["latency_ms"], dict(row)


@pytest.mark.req("NFR-1")
def test_wall_ms_is_the_timer_around_the_whole_cascade():
    """`Ratchet(timer=...)`: wall_ms is the time from the start of `handle` (or `teacher_only`) to the ledger write,
    two reads of the timer per request; latency_ms keeps the providers' figure."""
    ticks = iter([10.0, 10.25, 20.0, 20.5, 30.0, 30.125])
    led = Ledger(":memory:")
    teacher = RecordingProvider(A, latency_ms=40.0)
    r = Ratchet(_settings(), led, teacher, lambda v: (RecordingProvider(A), v.adapter_uri), seed=1, timer=lambda: next(ticks))
    r.handle(_ask(1), TRIAGE)
    r.handle(_ask(2), "research-write")
    r.teacher_only(_ask(3), TRIAGE)
    rows = led.db.execute("SELECT wall_ms, latency_ms FROM requests ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [(250.0, 40.0), (500.0, 40.0), (125.0, 40.0)]


def _mean_ms(r: Ratchet, n: int, tag: str) -> float:
    """The best of three runs' mean wall time of `n` requests, in ms."""
    best = float("inf")
    for run in range(3):
        start = time.perf_counter()
        for i in range(n):
            r.handle(_ask(f"{tag}-{run}-{i}"), TRIAGE)
        best = min(best, (time.perf_counter() - start) * 1000 / n)
    return best


@pytest.mark.req("NFR-1", "FR-12.8")
def test_handle_overhead_does_not_grow_with_versions():
    """With zero-latency models, a request costs about the same with 365 retired versions as with one: routing reads
    the snapshot, not every version and its eval (before ADR 0060, three full reads per request)."""

    def cascade(retired: int) -> Ratchet:
        led = Ledger(":memory:")
        for _ in range(retired):
            _version(led, "retired", {"threshold": 0.5, "precision": 0.97, "coverage": 0.6})
        _version(led, "live", {"threshold": 0.5})
        student = RecordingProvider(A, latency_ms=0.0)
        return Ratchet(_settings(), led, RecordingProvider(A, latency_ms=0.0), lambda v: (student, v.adapter_uri), seed=1)

    one, many = cascade(0), cascade(365)
    _mean_ms(one, 20, "warm"), _mean_ms(many, 20, "warm")
    one_ms, many_ms = _mean_ms(one, 100, "one"), _mean_ms(many, 100, "many")
    assert many_ms <= 2 * one_ms + 0.5, (one_ms, many_ms)


def _bulk(led: Ledger, n: int, live: int, canary: int, shadow: int) -> None:
    """`n` request rows in one transaction, over 20 days: the live version serves most, the canary a tenth, the
    teacher audits 5% of what the student served, and every 100th request was shadowed."""
    rng = random.Random(7)
    rows = []
    for i in range(n):
        vid = canary if i % 10 == 0 else live
        served = "student" if rng.random() < 0.7 else "teacher"
        audited = int(served == "student" and rng.random() < 0.05)
        shadowed = i % 100 == 0
        rows.append(
            (
                1_800_000_000.0 + i,
                1 + i * 20 // n,
                TRIAGE,
                served,
                vid,
                0.9,
                int(served == "teacher"),
                audited,
                int(rng.random() < 0.97) if audited else None,
                shadow if shadowed else None,
                int(rng.random() < 0.6) if shadowed else None,
                1 if shadowed else None,
                250.0,
                300.0 + 50 * rng.random(),
                0.0001,
                0.001,
                served if served == "student" else "confidence",
            )
        )
    led.db.executemany(
        "INSERT INTO requests (ts, day, task, served_by, student_version, confidence, escalated, audited, audit_agree,"
        " shadow_version, shadow_serve, shadow_agree, latency_ms, wall_ms, cost_usd, baseline_cost_usd, route)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    led.db.commit()


@pytest.mark.req("NFR-1", "FR-12.8")
def test_perf_guard_200k_requests(tmp_path):
    """A WAL ledger with 200,000 requests: a release-gate tick takes under 20 ms once its tallies are warm, and a
    request's own overhead (zero-latency models, a live, a canary and a shadow version) stays under 5 ms. Generous
    bounds; a laptop measures a small fraction of each."""
    led = Ledger(str(tmp_path / "big.db"))
    live = _version(led, "live", {"threshold": 0.5})
    canary = _version(led, "canary", {"threshold": 0.5})
    shadow = _version(led, "shadow", {"threshold": 0.5})
    _bulk(led, 200_000, live, canary, shadow)
    gate = ReleaseGate(led, ReleasePolicy(min_shadow=10**6, min_audits=10**6))  # gathers evidence, decides nothing
    # The drift monitor replays every logged confidence once (ADR 0032: about 10 us each). The gateway does it at startup
    # (`Runtime.reset` calls `check_drift`), "so the replay of the ledger does not fall on the first request", so it is
    # timed apart from the first tick, which guards the tallies. Bound: 60 us a row, six times the ADR's figure.
    started = time.perf_counter()
    gate.check_drift()
    replay_ms = (time.perf_counter() - started) * 1000
    assert replay_ms < 200_000 * 0.060, replay_ms
    started = time.perf_counter()
    gate.tick()
    cold_ms = (time.perf_counter() - started) * 1000
    student = RecordingProvider(A, latency_ms=0.0)
    r = Ratchet(_settings(), led, RecordingProvider(A, latency_ms=0.0), lambda v: (student, v.adapter_uri), seed=1)
    warm: list[float] = []
    for i in range(10):
        for j in range(5):
            r.handle(_ask(f"{i}-{j}"), TRIAGE)
        started = time.perf_counter()
        gate.tick()
        warm.append((time.perf_counter() - started) * 1000)
    assert cold_ms < 250, cold_ms
    assert statistics.median(warm) < 20, warm
    assert (led.live().id, led.by_state("canary").id, led.by_state("shadow").id) == (live, canary, shadow)
    assert _mean_ms(r, 20, "big") < 5
    overhead = [row[0] for row in led.db.execute("SELECT wall_ms - latency_ms FROM requests ORDER BY id DESC LIMIT 60")]
    assert statistics.median(overhead) < 5, overhead
    assert led.version_live_quality(live) == _full_quality(led, live, led.version(live).stage_since)
    led.close()
