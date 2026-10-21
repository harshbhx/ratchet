"""SQLite ledger: every request, every training example (with provenance), every model version."""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from . import tasks
from .config import MAX_GPUS, MAX_USD_PER_GPU_HOUR

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, task TEXT,
    served_by TEXT,            -- 'student' | 'teacher'
    student_version INTEGER,   -- version id that answered or tried first (NULL on cold start)
    confidence REAL,
    escalated INTEGER,
    audited INTEGER,           -- teacher double-checked a student answer
    audit_agree INTEGER,
    shadow_version INTEGER, shadow_serve INTEGER, shadow_agree INTEGER,
    latency_ms REAL,
    cost_usd REAL, baseline_cost_usd REAL,
    student_cost_usd REAL,     -- cost_usd's student part (live and shadow samples, per token); NULL before v0.5
    teacher_cost_usd REAL,     -- cost_usd's teacher part (escalation, audit, arbitration, pinned); NULL before v0.5
    student_in_tokens INTEGER, student_out_tokens INTEGER, -- the student calls' tokens (prices the hosted fallback)
    client TEXT,               -- API key name (RATCHET_API_KEYS); NULL when auth is off or for internal calls
    route TEXT,                -- how it was served (router.ROUTES): student | confidence | cold_start | pinned | degraded | ...
    stored INTEGER,            -- 1: a teacher answer was stored as an example (inserted, or a duplicate's seen bumped)
    -- Evidence columns (ADR 0024), NULL until the feature that writes them lands, and in older rows:
    threshold REAL,            -- the confidence threshold the student was held to; NULL without a version (FR-16.5)
    label_p REAL,              -- probability that a teacher label of the student's answer was observed (FR-16.5)
    student_agree INTEGER,     -- 1/0: the student's answer agreed with the escalation's or a random audit's (FR-16.5)
    baseline_est_usd REAL,     -- the all-teacher cost by the token formula, also when the teacher answered (FR-16.5)
    optional_failed INTEGER    -- 1: an optional teacher call of this request (audit, arbitration) failed
);
CREATE TABLE IF NOT EXISTS examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, task TEXT,
    hash TEXT UNIQUE,
    messages TEXT, completion TEXT,
    teacher_model TEXT, license TEXT, license_url TEXT,
    split TEXT,                -- 'train' | 'golden'
    seen INTEGER DEFAULT 1,    -- how often this exact request occurred (drives the frequent-request rule)
    redactions INTEGER DEFAULT 0, -- PII placeholders inserted before storing (ratchet/redact.py)
    params TEXT,               -- JSON: the conditions the teacher answered under (STORED_PARAM_KEYS); NULL when none
    -- Evidence columns (ADR 0024):
    origin TEXT,               -- 'escalation' | 'audit' | 'arbitration' | 'backfill'; NULL: not recorded (FR-16.8)
    job TEXT,                  -- the backfill job that wrote it; NULL otherwise (FR-16.8)
    contested INTEGER DEFAULT 0,      -- 1: a later teacher label of the same request disagreed (FR-16.7)
    checked_epoch INTEGER DEFAULT 0   -- the teacher-watch epoch of its last self-consistency check (FR-16.7)
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL, day INTEGER,
    base_model TEXT, adapter_uri TEXT,
    train_examples INTEGER, dataset_hash TEXT,
    eval TEXT,                 -- JSON
    state TEXT,                -- candidate|rejected|shadow|canary|live|retired|rolled_back
    traffic_pct INTEGER DEFAULT 0,
    stage_since INTEGER DEFAULT 0, -- last request id before the current stage began
    bom TEXT                   -- JSON: this adapter's bill of materials, signed when a key is set (ratchet/bom.py)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day INTEGER, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS compute_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,                 -- COMPUTE_KINDS: 'student_endpoint' | 'training_job' | 'eval_job'
    start_ts REAL, end_ts REAL, -- on the ledger's clock (Ledger.now); end_ts NULL while it runs
    usd_per_hour REAL,         -- per GPU, as recorded (list prices are unverified until the day-1 checks pass)
    gpus INTEGER,
    version INTEGER,           -- the version a training job produced; NULL otherwise
    source TEXT,               -- 'cli' | 'api' | 'deploy' | 'night' | 'simulation'
    note TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    name TEXT PRIMARY KEY,
    spec TEXT,                 -- JSON: the task as a spec file writes it (taskspec.document); rules, never request text
    spec_hash TEXT,
    source TEXT,               -- 'api' (the admin API) | 'auto' (derived from requests)
    created_ts REAL, updated_ts REAL,
    updated_by TEXT            -- the admin key's name (RATCHET_ADMIN_KEYS), never the key
);
-- Evidence tables (ADR 0024). Each feature fills its own; an old file gains them on open.
CREATE TABLE IF NOT EXISTS version_leaves (  -- each adapter's committed training and golden sets (FR-16.4)
    version_id INTEGER NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train', 'golden')),
    key TEXT NOT NULL,         -- hex: the example hash
    value TEXT NOT NULL,       -- hex: sha256 of the example's label, teacher, license and redaction count
    PRIMARY KEY (version_id, split, key)
);
CREATE TABLE IF NOT EXISTS teacher_checks (  -- teacher self-consistency checks (FR-16.7)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, epoch INTEGER, task TEXT,
    source TEXT,               -- 'duplicate' | 'probe'
    agree INTEGER, model TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);  -- small ledger-wide state, e.g. the teacher epoch
CREATE TABLE IF NOT EXISTS receipts (        -- sealed, signed savings receipts (FR-15.5)
    period INTEGER NOT NULL,   -- the ledger day
    client TEXT NOT NULL,      -- API key name; '' for the whole gateway
    created_ts REAL,
    doc TEXT NOT NULL,         -- JSON: the signed document
    PRIMARY KEY (period, client)
);
CREATE TABLE IF NOT EXISTS spend (           -- money spent outside requests, e.g. backfill labelling (FR-16.8)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, kind TEXT, ref TEXT,
    usd REAL, realtime_usd REAL, in_tokens INTEGER, out_tokens INTEGER, detail TEXT
);
CREATE TABLE IF NOT EXISTS backfill_jobs (   -- historical prompts relabelled by the teacher (FR-16.8)
    job TEXT PRIMARY KEY,
    ts REAL, backend TEXT, teacher TEXT, license TEXT,
    prompts INTEGER, unique_prompts INTEGER, outputs_discarded INTEGER,
    source_models TEXT,        -- JSON: {model: outputs discarded}
    status TEXT, usd REAL, realtime_usd REAL
);
CREATE TABLE IF NOT EXISTS tombstones (      -- erased example keys, never stored again (FR-16.9)
    key TEXT PRIMARY KEY, erasure_id INTEGER, ts REAL,
    suppressed INTEGER DEFAULT 0  -- how often a tombstoned example was offered again and refused
);
CREATE TABLE IF NOT EXISTS erasures (        -- erasure requests and their certificates (FR-16.9)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, key TEXT,
    tainted TEXT,              -- JSON: ids of the versions trained on the example
    status TEXT,               -- 'pending' | 'complete'
    replacement INTEGER,       -- the version trained without it
    certificate TEXT           -- JSON: the signed certificate
);
-- INV-17: committed leaves and sealed receipts are append-only. A changed or deleted row would silently break the
-- signed roots and chains built from them, so the database refuses UPDATE and DELETE, and an INSERT that would replace
-- a row (INSERT OR REPLACE deletes the old row without firing a DELETE trigger, so it is refused before it runs).
CREATE TRIGGER IF NOT EXISTS version_leaves_no_update BEFORE UPDATE ON version_leaves
BEGIN SELECT RAISE(ABORT, 'version_leaves are append-only (INV-17)'); END;
CREATE TRIGGER IF NOT EXISTS version_leaves_no_delete BEFORE DELETE ON version_leaves
BEGIN SELECT RAISE(ABORT, 'version_leaves are append-only (INV-17)'); END;
CREATE TRIGGER IF NOT EXISTS version_leaves_no_replace BEFORE INSERT ON version_leaves
WHEN EXISTS (SELECT 1 FROM version_leaves WHERE version_id = NEW.version_id AND split = NEW.split AND key = NEW.key)
BEGIN SELECT RAISE(ABORT, 'version_leaves are append-only (INV-17)'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_update BEFORE UPDATE ON receipts
BEGIN SELECT RAISE(ABORT, 'sealed receipts are append-only (INV-17)'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_delete BEFORE DELETE ON receipts
BEGIN SELECT RAISE(ABORT, 'sealed receipts are append-only (INV-17)'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_replace BEFORE INSERT ON receipts
WHEN EXISTS (SELECT 1 FROM receipts WHERE period = NEW.period AND client = NEW.client)
BEGIN SELECT RAISE(ABORT, 'sealed receipts are append-only (INV-17)'); END;
"""


# The generation parameters stored with an example: the ones that define what a right answer is (its format, the tools
# it may call, where it stops). Temperature, seed and penalties change how an answer is sampled, not what it should be,
# so they are neither stored nor part of the hash.
STORED_PARAM_KEYS = ("response_format", "tools", "tool_choice", "stop")


def target_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The STORED_PARAM_KEYS of `params` that are set, or None when there are none. `{"type": "text"}` is the default
    format and counts as not set, so a request that sends it stores and hashes like one that does not."""
    if not params:
        return None
    out: dict[str, Any] = {}
    for key in STORED_PARAM_KEYS:
        value = params.get(key)
        if value is None or (isinstance(value, (list, tuple, dict, str)) and not value):
            continue
        if key == "response_format" and isinstance(value, dict) and value.get("type") == "text":
            continue
        out[key] = list(value) if isinstance(value, tuple) else value
    return out or None


def example_hash(task: str, messages: list[dict[str, Any]], params: Mapping[str, Any] | None = None) -> str:
    """The example's identity: its task, messages and stored conditions (`target_params`). The conditions join the hashed
    object only when there are some, so every v0.4 hash stays the same. The golden or train split comes from the hash
    without conditions (`Ledger._split`), so it stays the same too, for every variant of a prompt."""
    obj: dict[str, Any] = {"task": task, "messages": messages}
    target = target_params(params)
    if target:
        obj["params"] = target
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Version:
    id: int
    day: int
    base_model: str
    adapter_uri: str
    train_examples: int
    dataset_hash: str
    eval: dict[str, Any]
    state: str
    traffic_pct: int
    stage_since: int = 0


# Columns added after v0.1's first schema; existing database files are migrated on open.
MIGRATIONS: dict[str, dict[str, str]] = {
    "requests": {
        "shadow_serve": "INTEGER",
        "client": "TEXT",
        "route": "TEXT",
        "stored": "INTEGER",
        "student_cost_usd": "REAL",
        "teacher_cost_usd": "REAL",
        "student_in_tokens": "INTEGER",
        "student_out_tokens": "INTEGER",
        "threshold": "REAL",
        "label_p": "REAL",
        "student_agree": "INTEGER",
        "baseline_est_usd": "REAL",
        "optional_failed": "INTEGER",
    },
    "examples": {
        "seen": "INTEGER DEFAULT 1",
        "redactions": "INTEGER DEFAULT 0",
        "params": "TEXT",
        "origin": "TEXT",
        "job": "TEXT",
        "contested": "INTEGER DEFAULT 0",
        "checked_epoch": "INTEGER DEFAULT 0",
    },
    "versions": {"stage_since": "INTEGER DEFAULT 0", "bom": "TEXT"},
}
# Created after migrations, because they index columns that old files only get from MIGRATIONS.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS requests_client_ts ON requests (client, ts)",
    "CREATE INDEX IF NOT EXISTS compute_intervals_kind_start ON compute_intervals (kind, start_ts)",
    # A version's audits (version_live_quality, the release gate's counts), its shadow rows (shadow_quality) and a day's
    # rows (stats and receipts by day) without a full scan as the ledger grows (ADR 0024).
    "CREATE INDEX IF NOT EXISTS requests_version_audited ON requests (student_version, audited, id)",
    "CREATE INDEX IF NOT EXISTS requests_shadow_version ON requests (shadow_version)",
    "CREATE INDEX IF NOT EXISTS requests_day ON requests (day)",
    "CREATE INDEX IF NOT EXISTS version_leaves_key ON version_leaves (key)",
    "CREATE INDEX IF NOT EXISTS teacher_checks_epoch ON teacher_checks (epoch)",
    "CREATE INDEX IF NOT EXISTS erasures_status ON erasures (status)",
]
_INDEX_DDL = re.compile(r"CREATE INDEX IF NOT EXISTS (\w+) ON (\w+) \(([\w, ]+)\)")
# Tables and triggers created by SCHEMA for the evidence features (ADR 0024); the migration test checks an old file gains
# each of them.
EVIDENCE_TABLES = ("version_leaves", "teacher_checks", "meta", "receipts", "spend", "backfill_jobs", "tombstones", "erasures")
APPEND_ONLY_TABLES = ("version_leaves", "receipts")  # INV-17: UPDATE, DELETE and replacing INSERTs are refused

DAY_SECONDS = 86400
# The route reported for rows written before requests had a route column (v0.4 and earlier files).
UNRECORDED_ROUTE = "unrecorded"
# What GPUs ran (ADR 0023): the self-hosted student endpoint, a Night School training job, and a separate evaluation job
# (none records one yet: the live golden eval runs on the student endpoint, whose uptime is counted).
COMPUTE_KINDS = ("student_endpoint", "training_job", "eval_job")


def _check_compute(kind: str, usd_per_hour: float, gpus: int) -> None:
    if kind not in COMPUTE_KINDS:
        raise ValueError(f"compute kind must be one of {', '.join(COMPUTE_KINDS)}, not {kind!r}")
    if isinstance(usd_per_hour, bool) or not isinstance(usd_per_hour, (int, float)):
        raise ValueError(f"usd_per_hour must be a number, not {usd_per_hour!r}")
    if not 0 <= usd_per_hour <= MAX_USD_PER_GPU_HOUR:  # also NaN, infinity and an int too large for a float
        raise ValueError(f"usd_per_hour must be 0 to {MAX_USD_PER_GPU_HOUR:,.0f} per GPU-hour, not {usd_per_hour!r}")
    if isinstance(gpus, bool) or not isinstance(gpus, int) or not 0 <= gpus <= MAX_GPUS:
        raise ValueError(f"gpus must be a whole number from 0 to {MAX_GPUS:,}, not {gpus!r}")


def _check_ts(ts: float, what: str) -> float:
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        raise ValueError(f"{what} must be a finite number of seconds, not {ts!r}")
    return float(ts)


def _locked(fn):
    """Serialise ledger access: Scout's agents call the gateway from several threads at once,
    and they all share one SQLite connection."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class Ledger:
    """`day` is set by the caller in simulations. With `auto_day=True` (live mode) it is
    derived from wall-clock time: day 1 starts at the first logged request."""

    def __init__(self, path: str = ":memory:", golden_fraction: float = 0.10, auto_day: bool = False):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.golden_fraction = golden_fraction
        self.auto_day = auto_day
        first = self.db.execute("SELECT MIN(ts) FROM requests").fetchone()[0]
        self._t0: float | None = float(first) if first is not None else None
        last_day = self.db.execute("SELECT COALESCE(MAX(day), 0) FROM requests").fetchone()[0]
        self.day = int(last_day)

    def _migrate(self) -> None:
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        for ddl in INDEXES:
            m = _INDEX_DDL.fullmatch(ddl)
            if m is None:
                raise ValueError(f"INDEXES entries read 'CREATE INDEX IF NOT EXISTS <name> ON <table> (<columns>)', not {ddl!r}")
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({m.group(2)})")}
            # Every file Ratchet wrote has the indexed columns (SCHEMA, then MIGRATIONS); a hand-made partial file may
            # not, and an index is only an aid, so it is skipped there. test_every_index_exists_on_a_new_ledger catches a
            # misspelled column, which would otherwise be skipped everywhere.
            if {c.strip() for c in m.group(3).split(",")} <= have:
                self.db.execute(ddl)
        self.db.commit()

    def _tick_day(self, ts: float) -> None:
        if not self.auto_day:
            return
        if self._t0 is None:
            self._t0 = ts
        self.day = int((ts - self._t0) // DAY_SECONDS) + 1

    # ---------- requests ----------
    @_locked
    def log_request(self, **row: Any) -> int:
        row.setdefault("ts", time.time())
        self._tick_day(row["ts"])
        row.setdefault("day", self.day)
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        cur = self.db.execute(f"INSERT INTO requests ({cols}) VALUES ({qs})", list(row.values()))
        self.db.commit()
        return int(cur.lastrowid)

    @_locked
    def client_requests_since(self, client: str, ts: float) -> int:
        """Requests logged for one API client since `ts` (drives the per-key daily quota)."""
        return int(self.db.execute("SELECT COUNT(*) FROM requests WHERE client=? AND ts>=?", (client, ts)).fetchone()[0])

    @_locked
    def cost_by_day(self, since: int | None = None, until: int | None = None) -> list[dict[str, Any]]:
        """Per ledger day from `since` to `until` (inclusive; None = unbounded), the sums the cost report needs, in one
        aggregate query (ratchet/cost.py). Rows from before v0.5 have no split (`split_unknown`): their whole `cost_usd`
        counts as teacher spend, which can only lower the GPU-hour view's savings. `sampled` counts requests that
        reached the student (it served them, or its calls used tokens), `avoided` the teacher spend student-served
        requests did not need (their all-teacher baseline less the teacher calls they made), and the tokens and
        `served_with_tokens` cover only rows that recorded the student's tokens."""
        q = """SELECT day, COUNT(*) requests,
                      COALESCE(SUM(served_by='student'), 0) student_served,
                      COALESCE(SUM(served_by='teacher'), 0) teacher_served,
                      COALESCE(SUM(cost_usd), 0.0) cost_usd,
                      COALESCE(SUM(baseline_cost_usd), 0.0) baseline_usd,
                      COALESCE(SUM(COALESCE(teacher_cost_usd, cost_usd, 0.0)), 0.0) teacher_usd,
                      COALESCE(SUM(student_cost_usd), 0.0) student_usd,
                      COALESCE(SUM(student_cost_usd IS NULL), 0) split_unknown,
                      COALESCE(SUM(CASE WHEN student_cost_usd IS NULL THEN cost_usd END), 0.0) split_unknown_usd,
                      COALESCE(SUM(served_by='student'
                                   OR COALESCE(student_in_tokens, 0) + COALESCE(student_out_tokens, 0) > 0), 0) sampled,
                      COALESCE(SUM(CASE WHEN served_by='student'
                                   THEN COALESCE(baseline_cost_usd, 0.0) - COALESCE(teacher_cost_usd, cost_usd, 0.0)
                                   END), 0.0) avoided,
                      COALESCE(SUM(student_in_tokens), 0) student_in_tokens,
                      COALESCE(SUM(student_out_tokens), 0) student_out_tokens,
                      COALESCE(SUM(served_by='student' AND student_in_tokens IS NOT NULL), 0) served_with_tokens
               FROM requests WHERE 1=1"""
        args: list[Any] = []
        if since is not None:
            q += " AND day >= ?"
            args.append(since)
        if until is not None:
            q += " AND day <= ?"
            args.append(until)
        return [dict(r) for r in self.db.execute(q + " GROUP BY day ORDER BY day", args)]

    @_locked
    def student_traffic_by_hour(self, since: int | None = None, until: int | None = None) -> list[dict[str, Any]]:
        """On a live ledger, the hours in which requests on ledger days `since` to `until` reached the student (it served
        them, or its calls used tokens, as `cost_by_day`'s `sampled`), each with `start` (on the wall clock; hour 0 starts
        with day 1, at the first request), its ledger `day`, `student_served` and `student_usd`. The cost report's idle
        hours and observed volume come from these (ADR 0023): a request's time and the endpoint's uptime are on one clock
        here, unlike the simulator's. One aggregate query; empty before the first request."""
        if self._t0 is None:
            return []
        x = "((ts - :t0) / 3600.0)"
        where = "".join(
            clause for clause, value in ((" AND day >= :since", since), (" AND day <= :until", until)) if value is not None
        )
        q = f"""SELECT h, COALESCE(SUM(served_by='student'), 0) student_served,
                       COALESCE(SUM(student_cost_usd), 0.0) student_usd,
                       COALESCE(SUM(served_by='student'
                                    OR COALESCE(student_in_tokens, 0) + COALESCE(student_out_tokens, 0) > 0), 0) sampled
                FROM (SELECT CAST({x} AS INTEGER) - ({x} < CAST({x} AS INTEGER)) h, served_by, student_cost_usd,
                             student_in_tokens, student_out_tokens
                      FROM requests WHERE ts IS NOT NULL{where})
                GROUP BY h HAVING sampled > 0 OR student_usd > 0 ORDER BY h"""
        rows = self.db.execute(q, {"t0": self._t0, "since": since, "until": until})
        return [
            {
                "start": self._t0 + r["h"] * 3600.0,
                "day": r["h"] // 24 + 1,
                "student_served": r["student_served"],
                "student_usd": r["student_usd"],
            }
            for r in rows
        ]

    # ---------- compute time (ADR 0023) ----------
    def now(self) -> float:
        """The clock compute intervals are recorded on. A live ledger (`auto_day`) uses wall-clock time. Simulations and
        tests run on a synthetic clock where day N starts at N x 86400 s (a simulated day takes a second of wall time,
        so its 24 hours cannot be on the wall clock); there `now()` is the current day at the wall clock's time of day."""
        t = time.time()
        return t if self.auto_day else self.day * DAY_SECONDS + t % DAY_SECONDS

    def _origin(self, now: float | None) -> float:
        """When day 1 starts on a live ledger: at the first logged request (as `_tick_day` counts). Before there is one,
        at the earliest recorded compute interval (never after `now`), so a report of the last N days still covers the
        uptime recorded before the first request; with nothing recorded, at `now`."""
        if self._t0 is not None:
            return self._t0
        now = self.now() if now is None else now
        first = self.db.execute("SELECT MIN(start_ts) FROM compute_intervals").fetchone()[0]
        return now if first is None else min(float(first), now)

    @_locked
    def day_start(self, day: int, now: float | None = None) -> float:
        """When ledger day `day` starts on the ledger's clock (`now()`)."""
        if not self.auto_day:
            return float(day * DAY_SECONDS)
        return self._origin(now) + (day - 1) * DAY_SECONDS

    @_locked
    def day_of(self, ts: float, now: float | None = None) -> int:
        """The ledger day a moment on the ledger's clock falls in (before the first request of a live ledger: day 0 or
        less)."""
        if not self.auto_day:
            return int(ts // DAY_SECONDS)
        return int((ts - self._origin(now)) // DAY_SECONDS) + 1

    def _begin(self) -> None:
        """Start a write transaction now, so a check and the write after it are atomic across processes too (the CLI
        and the gateway may share the file). Every write commits at once, so none is open here."""
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")

    @_locked
    def compute_start(
        self,
        kind: str,
        usd_per_hour: float,
        gpus: int,
        source: str,
        version: int | None = None,
        ts: float | None = None,
        note: str | None = None,
    ) -> int:
        """Record that compute of `kind` (COMPUTE_KINDS) started at `ts` (default `now()`), billed `usd_per_hour` per GPU
        for `gpus` GPUs; returns the interval's id. A student endpoint is up or down, so starting it while an interval
        is open returns that interval's id unchanged, also when another process opened it. A training or eval job gets
        an interval per start."""
        _check_compute(kind, usd_per_hour, gpus)
        start = self.now() if ts is None else _check_ts(ts, "ts")
        self._begin()
        try:
            if kind == "student_endpoint":
                row = self.db.execute(
                    "SELECT id FROM compute_intervals WHERE kind=? AND end_ts IS NULL ORDER BY start_ts DESC, id DESC",
                    (kind,),
                ).fetchone()
                if row is not None:
                    self.db.commit()
                    return int(row["id"])
            cur = self.db.execute(
                "INSERT INTO compute_intervals (kind, start_ts, end_ts, usd_per_hour, gpus, version, source, note)"
                " VALUES (?,?,NULL,?,?,?,?,?)",
                (kind, start, float(usd_per_hour), gpus, version, source, note),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return int(cur.lastrowid)

    @_locked
    def compute_stop(
        self,
        kind: str,
        ts: float | None = None,
        interval_id: int | None = None,
        version: int | None = None,
        note: str | None = None,
    ) -> int | None:
        """Close compute of `kind` at `ts` (default `now()`): the open interval `interval_id`, or every open interval of
        the kind (an endpoint that goes down ends however many records said it was up). `version` and `note` are set
        when given. Returns the id of the latest interval closed, or None when none was open. An explicit `ts` before
        an interval's start raises ValueError and closes nothing; the default never ends one before it started (a
        synthetic clock's time of day can wrap)."""
        if kind not in COMPUTE_KINDS:
            raise ValueError(f"compute kind must be one of {', '.join(COMPUTE_KINDS)}, not {kind!r}")
        end = None if ts is None else _check_ts(ts, "ts")
        q, args = "SELECT id, start_ts FROM compute_intervals WHERE kind=? AND end_ts IS NULL", [kind]
        if interval_id is not None:
            q += " AND id=?"
            args.append(interval_id)
        self._begin()
        try:
            rows = self.db.execute(q + " ORDER BY start_ts, id", args).fetchall()
            now = self.now() if end is None else end
            for r in rows:
                if end is not None and end < r["start_ts"]:
                    raise ValueError(f"interval {r['id']} started at {r['start_ts']}, after the end given ({end})")
            for r in rows:
                self.db.execute(
                    "UPDATE compute_intervals SET end_ts=?, version=COALESCE(?, version), note=COALESCE(?, note) WHERE id=?",
                    (max(now, r["start_ts"]), version, note, r["id"]),
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return int(rows[-1]["id"]) if rows else None

    @_locked
    def compute_record(
        self,
        kind: str,
        start: float,
        end: float,
        usd_per_hour: float,
        gpus: int,
        source: str,
        version: int | None = None,
        note: str | None = None,
    ) -> int:
        """Insert a closed interval [start, end] of `kind` and return its id. Unlike `compute_start`, it never returns or
        closes an open interval of the kind, so a record of what already happened (the simulator's synthetic day) leaves
        an endpoint someone marked up as it was; overlapping records merge when the report sums them."""
        _check_compute(kind, usd_per_hour, gpus)
        a, b = _check_ts(start, "start"), _check_ts(end, "end")
        if b < a:
            raise ValueError(f"end ({b}) is before start ({a})")
        cur = self.db.execute(
            "INSERT INTO compute_intervals (kind, start_ts, end_ts, usd_per_hour, gpus, version, source, note)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (kind, a, b, float(usd_per_hour), gpus, version, source, note),
        )
        self.db.commit()
        return int(cur.lastrowid)

    @_locked
    def compute_intervals(
        self, kind: str | None = None, since: float | None = None, until: float | None = None
    ) -> list[dict[str, Any]]:
        """Recorded intervals, oldest first, as dicts of the table's columns. With `since` / `until` (moments on the
        ledger's clock), only those overlapping [since, until); an open interval overlaps everything after its start."""
        q, args = "SELECT * FROM compute_intervals WHERE 1=1", []
        if kind is not None:
            q += " AND kind=?"
            args.append(kind)
        if since is not None:
            q += " AND (end_ts IS NULL OR end_ts > ?)"
            args.append(since)
        if until is not None:
            q += " AND start_ts < ?"
            args.append(until)
        return [dict(r) for r in self.db.execute(q + " ORDER BY start_ts, id", args)]

    # ---------- examples (provenance) ----------
    @_locked
    def add_example(
        self,
        task: str,
        messages: list[dict[str, Any]],
        completion: str,
        teacher_model: str,
        license_name: str,
        license_url: str,
        redactions: int = 0,
        params: Mapping[str, Any] | None = None,
    ) -> bool:
        """Store a teacher-labelled example. Returns False if it was a duplicate.
        `messages`, `completion` and `params` arrive already redacted; `redactions` counts the placeholders. `params`
        are the conditions the teacher answered under; only `target_params` of them are stored, and the same messages
        under other conditions are another example, in the same split (`_split`)."""
        target = target_params(params)
        h = example_hash(task, messages, target)
        split = self._split(example_hash(task, messages) if target else h)  # without conditions, h is the split key
        try:
            self.db.execute(
                "INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model,"
                " license, license_url, split, redactions, params) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    self.day,
                    task,
                    h,
                    json.dumps(messages, ensure_ascii=False),
                    completion,
                    teacher_model,
                    license_name,
                    license_url,
                    split,
                    redactions,
                    json.dumps(target, ensure_ascii=False) if target else None,
                ),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.execute("UPDATE examples SET seen = seen + 1 WHERE hash=?", (h,))
            self.db.commit()
            return False

    def _split(self, key: str) -> str:
        """'golden' or 'train', deterministic from `key`, the hash of an example's task and messages without its
        conditions (`example_hash(task, messages)`), so an example never moves between sets and every variant of one
        prompt (with a stop, in JSON mode, or bare) lands in the same one. The trainer sees only the messages, so a golden
        prompt stored again under other conditions in train would be trained on (INV-3) and inflate held-out precision
        (INV-6). Without conditions the key is the example's own hash, so every v0.4 split is unchanged."""
        return "golden" if int(key[:8], 16) / 0xFFFFFFFF < self.golden_fraction else "train"

    @_locked
    def examples(self, split: str | None = None, task: str | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM examples WHERE 1=1", []
        if split:
            q += " AND split=?"
            args.append(split)
        if task:
            q += " AND task=?"
            args.append(task)
        return list(self.db.execute(q + " ORDER BY id", args))

    @_locked
    def example_count(self, task: str) -> int:
        """Stored examples of one task (what deleting the task keeps, for provenance)."""
        return int(self.db.execute("SELECT COUNT(*) FROM examples WHERE task=?", (task,)).fetchone()[0])

    @_locked
    def bill_of_materials(self) -> dict[str, Any]:
        """Model bill of materials: which teachers and licenses produced the training data."""
        rows = self.db.execute(
            "SELECT teacher_model, license, license_url, split, COUNT(*) n FROM examples"
            " GROUP BY teacher_model, license, license_url, split"
        ).fetchall()
        digest = hashlib.sha256()
        for r in self.db.execute("SELECT hash FROM examples ORDER BY hash"):
            digest.update(r["hash"].encode())
        return {
            "generated_at": time.time(),
            "examples_sha256": digest.hexdigest(),
            "sources": [dict(r) for r in rows],
        }

    @_locked
    def privacy_stats(self) -> dict[str, int]:
        r = self.db.execute(
            "SELECT COUNT(*) n, SUM(redactions > 0) redacted, COALESCE(SUM(redactions), 0) placeholders FROM examples"
        ).fetchone()
        return {"examples": int(r["n"]), "redacted_examples": int(r["redacted"] or 0), "placeholders": int(r["placeholders"])}

    # ---------- versions ----------
    @_locked
    def create_version(
        self,
        base_model: str,
        adapter_uri: str,
        train_examples: int,
        dataset_hash: str,
        eval_result: dict[str, Any],
        state: str = "candidate",
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO versions (created_ts, day, base_model, adapter_uri, train_examples,"
            " dataset_hash, eval, state, traffic_pct) VALUES (?,?,?,?,?,?,?,?,0)",
            (time.time(), self.day, base_model, adapter_uri, train_examples, dataset_hash, json.dumps(eval_result), state),
        )
        self.db.commit()
        return int(cur.lastrowid)

    @_locked
    def update_eval(self, vid: int, eval_result: dict[str, Any]) -> None:
        self.db.execute("UPDATE versions SET eval=? WHERE id=?", (json.dumps(eval_result), vid))
        self.db.commit()

    @_locked
    def set_version(self, vid: int, state: str, traffic_pct: int | None = None) -> None:
        since = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()[0]
        if traffic_pct is None:
            self.db.execute("UPDATE versions SET state=?, stage_since=? WHERE id=?", (state, since, vid))
        else:
            self.db.execute(
                "UPDATE versions SET state=?, traffic_pct=?, stage_since=? WHERE id=?", (state, traffic_pct, since, vid)
            )
        self.db.commit()

    @_locked
    def set_bom(self, vid: int, doc: dict[str, Any]) -> None:
        self.db.execute("UPDATE versions SET bom=? WHERE id=?", (json.dumps(doc, ensure_ascii=False), vid))
        self.db.commit()

    @_locked
    def version_bom(self, vid: int) -> dict[str, Any] | None:
        r = self.db.execute("SELECT bom FROM versions WHERE id=?", (vid,)).fetchone()
        return json.loads(r["bom"]) if r and r["bom"] else None

    @_locked
    def bom_status(self) -> dict[int, str]:
        """{version id: 'signed' | 'unsigned'} for versions that have a BOM."""
        out = {}
        for r in self.db.execute("SELECT id, bom FROM versions WHERE bom IS NOT NULL"):
            out[int(r["id"])] = "signed" if json.loads(r["bom"]).get("signature") else "unsigned"
        return out

    @staticmethod
    def _version(r: sqlite3.Row) -> Version:
        return Version(
            r["id"],
            r["day"],
            r["base_model"],
            r["adapter_uri"],
            r["train_examples"],
            r["dataset_hash"],
            json.loads(r["eval"] or "{}"),
            r["state"],
            r["traffic_pct"],
            r["stage_since"] or 0,
        )

    @_locked
    def versions(self, states: Iterable[str] | None = None) -> list[Version]:
        """Versions by id, all or those in `states`. The states are filtered in SQL, so only the rows returned have their
        eval parsed: the request path asks for the live and canary versions on every request, and an eval carries a
        certificate, a frontier and a per-task breakdown (ADR 0026)."""
        if states is None:
            rows = self.db.execute("SELECT * FROM versions ORDER BY id").fetchall()
        else:
            wanted = sorted(set(states))
            marks = ",".join("?" * len(wanted))
            rows = self.db.execute(f"SELECT * FROM versions WHERE state IN ({marks}) ORDER BY id", wanted).fetchall()
        return [self._version(r) for r in rows]

    @_locked
    def version(self, vid: int) -> Version | None:
        r = self.db.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
        return self._version(r) if r else None

    @_locked
    def live(self) -> Version | None:
        live = self.versions(["live"])
        return live[-1] if live else None

    @_locked
    def by_state(self, state: str) -> Version | None:
        vs = self.versions([state])
        return vs[-1] if vs else None

    # ---------- tasks (the admin API's and auto tasks; built-in and file tasks are never stored) ----------
    @_locked
    def save_task(
        self, name: str, spec: Mapping[str, Any], spec_hash: str, source: str, updated_by: str
    ) -> tuple[bool, str | None]:
        """Insert or replace a stored task: `spec` as `taskspec.document` writes it, `updated_by` the admin's key name
        (never the key, INV-13). Returns (created, the spec_hash it had before). The JSON keeps the order of `spec`,
        because the order of `fields` is part of the task's spec_hash."""
        now = time.time()
        text = json.dumps(spec, ensure_ascii=False)
        row = self.db.execute("SELECT spec_hash FROM tasks WHERE name=?", (name,)).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO tasks (name, spec, spec_hash, source, created_ts, updated_ts, updated_by) VALUES (?,?,?,?,?,?,?)",
                (name, text, spec_hash, source, now, now, updated_by),
            )
        else:
            self.db.execute(
                "UPDATE tasks SET spec=?, spec_hash=?, source=?, updated_ts=?, updated_by=? WHERE name=?",
                (text, spec_hash, source, now, updated_by, name),
            )
        self.db.commit()
        return row is None, (row["spec_hash"] if row is not None else None)

    @_locked
    def delete_task(self, name: str) -> bool:
        """Forget a stored task (its examples stay, for provenance). False when none had that name."""
        cur = self.db.execute("DELETE FROM tasks WHERE name=?", (name,))
        self.db.commit()
        return cur.rowcount > 0

    @_locked
    def saved_task(self, name: str) -> dict[str, Any] | None:
        """One stored task (`saved_tasks`' shape), or None."""
        r = self.db.execute("SELECT * FROM tasks WHERE name=?", (name,)).fetchone()
        return self._task_row(r) if r is not None else None

    @_locked
    def saved_tasks(self) -> list[dict[str, Any]]:
        """Every stored task, oldest first: name, spec (a dict), spec_hash, source, created_ts, updated_ts, updated_by. A
        row whose spec is not JSON comes back with spec None, for the caller to skip."""
        return [self._task_row(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY created_ts, name")]

    @staticmethod
    def _task_row(r: sqlite3.Row) -> dict[str, Any]:
        out = dict(r)
        try:
            out["spec"] = json.loads(r["spec"]) if r["spec"] else None
        except ValueError:
            out["spec"] = None
        return out

    # ---------- events + stats ----------
    @_locked
    def event(self, kind: str, detail: str) -> None:
        self.db.execute("INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (time.time(), self.day, kind, detail))
        self.db.commit()

    @_locked
    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT id, ts, day, kind, detail FROM events ORDER BY id")]

    @_locked
    def last_event_id(self, kind: str) -> int:
        """The id of the latest event of `kind`, 0 when there is none (Night School compares 'drift' with 'trained')."""
        return int(self.db.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE kind=?", (kind,)).fetchone()[0])

    @_locked
    def event_once(self, key: str, kind: str, detail: str) -> bool:
        """Write event `kind` unless one was already written under `key`, and record it in `meta` (key -> the event's
        id). The check and both writes are one transaction, so two processes watching the same ledger (the gateway and
        `python -m ratchet.live tick`) or a restart that replays it write the event once. Returns whether it wrote."""
        self._begin()
        try:
            if self.db.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone() is not None:
                self.db.commit()
                return False
            cur = self.db.execute(
                "INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (time.time(), self.day, kind, detail)
            )
            self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, json.dumps({"event": cur.lastrowid})))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return True

    @_locked
    def meta_value(self, key: str) -> str | None:
        """The `meta` table's value for `key`, or None."""
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if r is None else str(r["value"])

    @_locked
    def confidence_stream(
        self,
        after_id: int = 0,
        limit: int = 200_000,
        *,
        version: int | None = None,
        task: str | None = None,
        before_id: int | None = None,
    ) -> list[tuple[int, int, str, float, int]]:
        """(id, student_version, task, confidence, day) of requests after id `after_id` that logged a student confidence,
        oldest first, at most `limit` (the drift monitor's input, FR-16.6). A primary-key range scan, so a monitor that
        is up to date reads only the new rows. `version`, `task` and `before_id` (exclusive) narrow it to one stream's
        rows, to replay a stream whose counts were dropped."""
        q = (
            "SELECT id, student_version, task, confidence, day FROM requests"
            " WHERE id > ? AND confidence IS NOT NULL AND student_version IS NOT NULL"
        )
        args: list[Any] = [after_id]
        if version is not None:
            q += " AND student_version = ?"
            args.append(version)
        if task is not None:
            q += " AND task = ?"
            args.append(task)
        if before_id is not None:
            q += " AND id < ?"
            args.append(before_id)
        args.append(limit)
        rows = self.db.execute(q + " ORDER BY id LIMIT ?", args)
        return [(int(r[0]), int(r[1]), str(r[2]), r[3], r[4]) for r in rows]

    @_locked
    def version_states(self) -> dict[int, str]:
        """{version id: state} of every version, without parsing evals (cheap enough to read on every gate tick), in the
        order the versions entered their current state (`stage_since`, then id): the last retired one is the most
        recently retired."""
        rows = self.db.execute("SELECT id, state FROM versions ORDER BY stage_since, id")
        return {int(r["id"]): r["state"] for r in rows}

    @_locked
    def audit_counts(self, vid: int, since_id: int = 0) -> tuple[int, int]:
        """(audits, agreements): teacher audits of answers version `vid` served, in requests after id `since_id` (0: its
        whole life; a version's `stage_since`: its current stage). Every release decision is computed from these two
        integers (ADR 0025). An audited row without a verdict, which the router never writes, counts as a disagreement."""
        r = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(audit_agree = 1), 0) s FROM requests WHERE student_version=? AND audited=1"
            " AND served_by='student' AND id>?",
            (vid, since_id),
        ).fetchone()
        return int(r["n"]), int(r["s"])

    @_locked
    def version_live_quality(self, vid: int, current_stage_only: bool = True) -> tuple[int, float | None]:
        """(audits, audited precision) for a version, from teacher audits of answers it served (`audit_counts`), in its
        current stage or over its whole life; precision is None without audits."""
        since = 0
        if current_stage_only:
            v = self.version(vid)
            since = v.stage_since if v else 0
        n, s = self.audit_counts(vid, since)
        return n, (s / n if n else None)

    @_locked
    def served_counts(self) -> dict[int, int]:
        """{version id: answers it served}: requests a student version answered (a stand-in answer included), so a
        rolled-back version's exposure is known. Versions that never served are absent."""
        rows = self.db.execute(
            "SELECT student_version v, COUNT(*) n FROM requests WHERE served_by='student' AND student_version IS NOT NULL"
            " GROUP BY student_version"
        )
        return {int(r["v"]): int(r["n"]) for r in rows}

    @_locked
    def shadow_quality(self, vid: int) -> dict[str, Any]:
        """How a shadow candidate would have done on real traffic, next to the live student.

        A row without a verdict (`shadow_serve` NULL) counts toward nothing. A would-serve answer whose arbitration
        failed (`shadow_agree` NULL, `unarbitrated`) counts as a disagreement: arbitration only runs on disagreements,
        so leaving those rows out would raise the precision of the candidate that disagrees most (ADR 0013).
        `live_coverage` leaves out requests the live student never answered (route `student_breaker` or
        `student_error`): its endpoint being down says nothing about how much it would serve, and counting them as
        escalations would lower the bar the shadow's coverage is held to (ADR 0015). They stay in `n` and `coverage`.
        A request the live student was confident about but escalated only because fewer than the client's n samples
        agreed (route `n_choices`) counts as served: the shadow's verdict uses the one-choice rule, so the live student is
        held to the same one (ADR 0020)."""
        r = self.db.execute(
            """SELECT COUNT(*) n, SUM(shadow_serve) served,
                      AVG(CASE WHEN shadow_serve=1 THEN COALESCE(shadow_agree, 0) END) prec,
                      SUM(shadow_serve=1 AND shadow_agree IS NULL) unarbitrated,
                      AVG(CASE WHEN route IN ('student_breaker', 'student_error') THEN NULL
                               WHEN route = 'n_choices' THEN 1
                               ELSE served_by='student' END) live_cov
               FROM requests WHERE shadow_version=? AND shadow_serve IS NOT NULL""",
            (vid,),
        ).fetchone()
        n = int(r["n"] or 0)
        served = int(r["served"] or 0)
        return {
            "n": n,
            "served": served,
            "precision": float(r["prec"]) if r["prec"] is not None else 0.0,
            "coverage": served / n if n else 0.0,
            "live_coverage": float(r["live_cov"]) if r["live_cov"] is not None else None,
            "unarbitrated": int(r["unarbitrated"] or 0),
        }

    @_locked
    def stats_by_day(self) -> list[dict[str, Any]]:
        """Per day: requests, shares, cost, savings, latency, audited agreement, and how many requests a stand-in
        answered because the teacher was unavailable (`degraded`: the student's below-threshold answer;
        `fallback_teacher`: the fallback teacher; ADR 0019)."""
        rows = self.db.execute(
            """SELECT day, COUNT(*) requests,
                      SUM(served_by='student') student_served,
                      SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      AVG(latency_ms) latency_ms,
                      AVG(CASE WHEN audited=1 THEN audit_agree END) audit_agree,
                      SUM(route='degraded') degraded,
                      SUM(route='fallback_teacher') fallback_teacher
               FROM requests GROUP BY day ORDER BY day"""
        ).fetchall()
        out = []
        for r in rows:
            n = r["requests"] or 1
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            out.append(
                {
                    "day": r["day"],
                    "requests": r["requests"],
                    "student_share": (r["student_served"] or 0) / n,
                    "escalation_rate": (r["escalated"] or 0) / n,
                    "cost_per_1k": 1000 * cost / n,
                    "baseline_per_1k": 1000 * base / n,
                    "savings_pct": 0.0 if base == 0 else 1 - cost / base,
                    "avg_latency_ms": r["latency_ms"],
                    "audit_agreement": r["audit_agree"],
                    "degraded": int(r["degraded"] or 0),
                    "fallback_teacher": int(r["fallback_teacher"] or 0),
                }
            )
        return out

    @_locked
    def stats_by_task(self) -> list[dict[str, Any]]:
        """Per task: requests, student share, escalations, cost, savings, latency, and `routes`: how many requests took
        each route (`unrecorded` for rows older than the route column), most frequent first."""
        rows = self.db.execute(
            """SELECT task, COUNT(*) requests, SUM(served_by='student') student_served, SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline, AVG(latency_ms) latency_ms
               FROM requests GROUP BY task ORDER BY COUNT(*) DESC"""
        ).fetchall()
        routes: dict[str, dict[str, int]] = {}
        counted = self.db.execute("SELECT task, route, COUNT(*) n FROM requests GROUP BY task, route ORDER BY n DESC, route")
        for r in counted:
            per_task = routes.setdefault(r["task"], {})
            key = r["route"] or UNRECORDED_ROUTE
            per_task[key] = per_task.get(key, 0) + int(r["n"])
        out = []
        for r in rows:
            n = r["requests"] or 1
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            out.append(
                {
                    "task": r["task"],
                    "pinned": tasks.get(r["task"]).pinned,
                    "requests": r["requests"],
                    "student_share": (r["student_served"] or 0) / n,
                    "escalation_rate": (r["escalated"] or 0) / n,
                    "cost_usd": cost,
                    "baseline_usd": base,
                    "savings_pct": 0.0 if base == 0 else 1 - cost / base,
                    "avg_latency_ms": r["latency_ms"],
                    "routes": routes.get(r["task"], {}),
                }
            )
        return out

    @_locked
    def stats_by_client(self) -> list[dict[str, Any]]:
        """Per API key: requests, cost and savings (the basis for per-client receipts)."""
        rows = self.db.execute(
            """SELECT client, COUNT(*) requests, SUM(served_by='student') student_served,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline
               FROM requests WHERE client IS NOT NULL GROUP BY client ORDER BY COUNT(*) DESC"""
        ).fetchall()
        out = []
        for r in rows:
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            out.append(
                {
                    "client": r["client"],
                    "requests": r["requests"],
                    "student_share": (r["student_served"] or 0) / (r["requests"] or 1),
                    "cost_usd": cost,
                    "baseline_usd": base,
                    "savings_pct": 0.0 if base == 0 else 1 - cost / base,
                }
            )
        return out

    @_locked
    def totals(self) -> dict[str, float]:
        r = self.db.execute("SELECT COUNT(*) n, SUM(cost_usd) c, SUM(baseline_cost_usd) b FROM requests").fetchone()
        c, b = r["c"] or 0.0, r["b"] or 0.0
        return {
            "requests": r["n"],
            "cost_usd": c,
            "baseline_usd": b,
            "saved_usd": b - c,
            "savings_pct": 0.0 if b == 0 else 1 - c / b,
        }
