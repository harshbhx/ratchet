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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tasks

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
    wall_ms REAL,              -- the whole cascade on the gateway's clock (perf_counter); latency_ms is the providers' part
    -- Evidence columns (ADR 0024), NULL until the feature that writes them lands, and in older rows:
    threshold REAL,            -- the confidence threshold the student was held to; NULL without a version (FR-16.5)
    label_p REAL,              -- probability that a teacher label of the student's answer was observed (FR-16.5)
    student_agree INTEGER,     -- 1/0: the student's answer agreed with the escalation's or a random audit's (FR-16.5)
    baseline_est_usd REAL,     -- the all-teacher cost by the token formula, also when the teacher answered (FR-16.5)
    optional_failed INTEGER,   -- 1: an optional teacher call of this request (audit, arbitration) failed
    -- Request ids (ADR 0062): the gateway's id ('req_' + 32 hex, also in x-request-id) and the client's own
    -- (X-Request-Id or X-Client-Request-Id, validated); NULL for internal calls and older rows.
    request_id TEXT,
    client_request_id TEXT
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
CREATE TABLE IF NOT EXISTS failures (  -- what failed, with what it spent (ADR 0062); metadata only, never text
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER,
    request_id TEXT,           -- the gateway's request id; NULL for a call outside a gateway request (Scout, simulator)
    client TEXT,               -- API key name; NULL when unknown (a 401), when keys are off, or for internal calls
    task TEXT,                 -- NULL when the request failed before naming one
    stage TEXT,                -- FAILURE_STAGES: where it failed
    error TEXT,                -- the exception's class, status and code (resilience.describe_failure); never a message
    status INTEGER,            -- the HTTP status of the stage that failed (an upstream's for upstream stages)
    latency_ms REAL,           -- from the start of the request (or the cascade) to the failure
    cost_usd REAL              -- what a request that left no requests row spent (its student samples); 0 otherwise
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
        "wall_ms": "REAL",
        "request_id": "TEXT",
        "client_request_id": "TEXT",
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
    # The routing snapshot's rebuild reads only the live, canary and shadow rows (ADR 0060). SQLite ends every index with
    # the rowid, so requests_shadow_version already serves `shadow_version=? AND id>?` ranges.
    "CREATE INDEX IF NOT EXISTS versions_state ON versions (state)",
    "CREATE INDEX IF NOT EXISTS version_leaves_key ON version_leaves (key)",
    "CREATE INDEX IF NOT EXISTS teacher_checks_epoch ON teacher_checks (epoch)",
    "CREATE INDEX IF NOT EXISTS erasures_status ON erasures (status)",
    # Request lookups by id, the failures of the last hour (/ratchet/stats) and today's spend per client (/metrics).
    "CREATE INDEX IF NOT EXISTS requests_request_id ON requests (request_id)",
    "CREATE INDEX IF NOT EXISTS failures_request_id ON failures (request_id)",
    "CREATE INDEX IF NOT EXISTS failures_ts ON failures (ts)",
    "CREATE INDEX IF NOT EXISTS failures_client_ts ON failures (client, ts)",
    # The period's budget alerts the spend meter reads when it starts, so a restart does not repeat one (ADR 0063).
    "CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts)",
]
_INDEX_DDL = re.compile(r"CREATE INDEX IF NOT EXISTS (\w+) ON (\w+) \(([\w, ]+)\)")
# Tables and triggers created by SCHEMA for the evidence features (ADR 0024); the migration test checks an old file gains
# each of them.
EVIDENCE_TABLES = ("version_leaves", "teacher_checks", "meta", "receipts", "spend", "backfill_jobs", "tombstones", "erasures")
APPEND_ONLY_TABLES = ("version_leaves", "receipts")  # INV-17: UPDATE, DELETE and replacing INSERTs are refused

DAY_SECONDS = 86400
# Where a request failed (`failures.stage`, ADR 0062). Upstream stages: the serving teacher (also the fallback teacher),
# the student's samples, a teacher audit, the shadow's samples or its arbitration, and an upstream a gateway route called
# itself (Scout's models and search). Gateway stages: the daily quota, an API, admin or dashboard key, the Scout budget
# guard, a rate limit of the gateway's own, and the gateway itself (a 413, an unhandled error).
FAILURE_STAGES = ("teacher", "student", "audit", "shadow", "quota", "auth", "budget", "rate", "upstream", "gateway")
FAILURE_COLUMNS = frozenset({"ts", "day", "request_id", "client", "task", "stage", "error", "status", "latency_ms", "cost_usd"})
# The route reported for rows written before requests had a route column (v0.4 and earlier files).
UNRECORDED_ROUTE = "unrecorded"
# What GPUs ran (ADR 0023): the self-hosted student endpoint, a Night School training job, and a separate evaluation job
# (none records one yet: the live golden eval runs on the student endpoint, whose uptime is counted).
COMPUTE_KINDS = ("student_endpoint", "training_job", "eval_job")

# RATCHET_DB_SYNC (ADR 0060): NORMAL puts a file ledger in the WAL journal with synchronous=NORMAL (readers never wait for
# the writer and a commit does not wait for the disk; a power cut can lose the last commits, never corrupt the file);
# FULL keeps the rollback journal with synchronous=FULL, v0.4's behaviour, for a filesystem without shared memory.
SYNC_MODES = ("NORMAL", "FULL")
BUSY_TIMEOUT_MS = 5000  # how long a connection waits for another process's write lock before it raises
# Paths SQLite keeps out of any file other connections can open: no journal mode, no second connection.
PRIVATE_PATHS = ("", ":memory:")
# The versions a request is routed by (`Ledger.live`, `Ledger.by_state`), kept in the routing snapshot.
ROUTED_STATES = ("live", "canary", "shadow")
# Tables whose writes on this connection bump a generation (`_bump`, through TEMP triggers that live only on
# the writer's connection): any write to `versions` invalidates the routing snapshot; a deleted or rewritten request or
# example invalidates the incremental tallies built from them. Inserts into requests and examples are not watched (the
# tallies read rows above the last id they saw), so the request path pays nothing for them.
WATCHED = {
    "versions": ("INSERT", "UPDATE", "DELETE"),
    "requests": ("UPDATE", "DELETE"),
    "examples": ("UPDATE OF hash", "DELETE"),
}
MAX_TALLIES = 64  # version_live_quality's incremental tallies kept, oldest dropped first (two are used per tick)

# The release gate's queries, named so tests can check their plans (each must search an index, never scan requests).
# A version's audits of answers it served, above a request id (requests_version_audited): version_live_quality's tally.
AUDIT_TALLY_SQL = (
    "SELECT COUNT(*) n, COUNT(audit_agree) k, COALESCE(SUM(audit_agree), 0) s, MAX(id) top FROM requests"
    " WHERE student_version=? AND audited=1 AND served_by='student' AND id>?"
)
# A shadow candidate's verdicts (requests_shadow_version): shadow_quality.
SHADOW_QUALITY_SQL = """SELECT COUNT(*) n, SUM(shadow_serve) served,
                      AVG(CASE WHEN shadow_serve=1 THEN COALESCE(shadow_agree, 0) END) prec,
                      SUM(shadow_serve=1 AND shadow_agree IS NULL) unarbitrated,
                      AVG(CASE WHEN route IN ('student_breaker', 'student_error') THEN NULL
                               WHEN route = 'n_choices' THEN 1
                               ELSE served_by='student' END) live_cov
               FROM requests WHERE shadow_version=? AND shadow_serve IS NOT NULL"""
# The routing snapshot's rebuild (versions_state).
ROUTED_SQL = f"SELECT * FROM versions WHERE state IN ({','.join('?' * len(ROUTED_STATES))}) ORDER BY id"


def _check_compute(kind: str, usd_per_hour: float, gpus: int) -> None:
    if kind not in COMPUTE_KINDS:
        raise ValueError(f"compute kind must be one of {', '.join(COMPUTE_KINDS)}, not {kind!r}")
    if isinstance(usd_per_hour, bool) or not isinstance(usd_per_hour, (int, float)) or not math.isfinite(usd_per_hour):
        raise ValueError(f"usd_per_hour must be a number, not {usd_per_hour!r}")
    if usd_per_hour < 0:
        raise ValueError(f"usd_per_hour must be 0 or more, not {usd_per_hour!r}")
    if isinstance(gpus, bool) or not isinstance(gpus, int) or gpus < 0:
        raise ValueError(f"gpus must be a whole number, 0 or more, not {gpus!r}")


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


def _reads(fn):
    """A read-only query for stats, the dashboard and reports. On a file ledger in WAL mode it runs on the read-only
    connection (`self._rdb`) under its own lock, inside one read transaction, so its statements see one snapshot and it
    never waits for the writer's lock (nor makes a request wait for it). Elsewhere `self._rdb` is the writer, under its
    lock, as before. A method wrapped here must only read, through `self._rdb`, and must not take `self._lock`."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if self._ro is None:
            with self._lock:
                return fn(self, *args, **kwargs)
        with self._ro_lock:
            if self._ro.in_transaction:  # called from another reader method: already inside its snapshot
                return fn(self, *args, **kwargs)
            self._ro.execute("BEGIN")
            try:
                return fn(self, *args, **kwargs)
            finally:
                self._ro.execute("ROLLBACK")  # a read transaction: nothing to commit

    return wrapper


@dataclass
class _Tally:
    """version_live_quality's running audit counts for one (version, stage) above request id `last_id`, valid while the
    writer's request generation and data_version stay `gen`."""

    gen: tuple[int, int]
    last_id: int
    n: int = 0
    agreed_n: int = 0  # rows with audit_agree set (AVG's denominator)
    agreed: int = 0  # their sum


def _bump(gen: dict[str, int], table: str) -> None:
    """`ratchet_written(table)`, called by the writer's TEMP triggers (`Ledger._watch`)."""
    gen[table] += 1


def _nearest_rank(q: float, n: int) -> int:
    """The 0-based offset of the q-quantile among n sorted values, by the nearest-rank rule: the smallest value with at
    least a share q of the values at or below it (q=0: the minimum, q=1: the maximum). q*n is rounded to 9 decimals
    first, so 0.07 * 100 counts as 7, not 7.000000000000001."""
    return min(n, max(1, math.ceil(round(q * n, 9)))) - 1


def _check_quantiles(qs: Iterable[float]) -> tuple[float, ...]:
    out = tuple(qs)
    for q in out:
        if isinstance(q, bool) or not isinstance(q, (int, float)) or not 0 <= q <= 1:
            raise ValueError(f"quantiles must be numbers from 0 to 1, not {q!r}")
    return out


class Ledger:
    """`day` is set by the caller in simulations. With `auto_day=True` (live mode) it is
    derived from wall-clock time: day 1 starts at the first logged request."""

    def __init__(self, path: str = ":memory:", golden_fraction: float = 0.10, auto_day: bool = False, sync: str = "NORMAL"):
        mode = sync.strip().upper() if isinstance(sync, str) else None
        if mode not in SYNC_MODES:
            raise ValueError(f"RATCHET_DB_SYNC must be {' or '.join(SYNC_MODES)}, not {sync!r}")
        self._lock = threading.RLock()
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
        self.db.row_factory = sqlite3.Row
        self.journal_mode = self._journal(mode)
        self.db.executescript(SCHEMA)
        self._migrate()
        self.golden_fraction = golden_fraction
        self.auto_day = auto_day
        first = self.db.execute("SELECT MIN(ts) FROM requests").fetchone()[0]
        self._t0: float | None = float(first) if first is not None else None
        last_day = self.db.execute("SELECT COALESCE(MAX(day), 0) FROM requests").fetchone()[0]
        self.day = int(last_day)
        # Hot-path caches (ADR 0060). Generations count this connection's writes to WATCHED tables; PRAGMA data_version
        # changes when another connection (the cron tick, the cost CLI) commits.
        self._gen = dict.fromkeys(WATCHED, 0)
        self._routing_key: tuple[int, int] | None = None
        self._routing: dict[str, sqlite3.Row] = {}
        self._tallies: dict[tuple[int, int], _Tally] = {}
        # stats_by_day's percentiles per day: (count, highest id, request generation) -> (p50 student, p95).
        self._day_latency: dict[int | None, tuple[tuple[int, int, int | None], float | None, float | None]] = {}
        self._digest: tuple[tuple[int, int | None, int | None], str] | None = None  # bill_of_materials' sha
        self._listeners: list[Callable[[dict[str, Any]], object]] = []  # event listeners (add_event_listener)
        self._watch()
        self._ro, self._ro_lock = self._reader()
        self._rdb = self._ro if self._ro is not None else self.db

    def _journal(self, mode: str) -> str:
        """Set a file ledger's journal (WAL for NORMAL, the rollback journal for FULL) and sync level, and return the
        journal mode SQLite reports. In-memory and private databases keep theirs. When WAL cannot be had (a filesystem
        without shared memory), the rollback journal stays and synchronous stays FULL, the level that journal needs to
        survive a power cut."""
        if self.path in PRIVATE_PATHS:
            return str(self.db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        self.db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        want = "WAL" if mode == "NORMAL" else "DELETE"
        try:
            got = str(self.db.execute(f"PRAGMA journal_mode={want}").fetchone()[0]).lower()
        except sqlite3.OperationalError as e:  # leaving WAL needs the file to itself
            raise sqlite3.OperationalError(
                f"cannot switch the ledger at {self.path!r} to the {want.lower()} journal (RATCHET_DB_SYNC={mode}): {e}; "
                "another process has it open, so stop it or give every process the same RATCHET_DB_SYNC"
            ) from e
        self.db.execute(f"PRAGMA synchronous={'NORMAL' if got == 'wal' else 'FULL'}")
        return got

    def _watch(self) -> None:
        """TEMP triggers that bump `_gen[table]` on every write to a WATCHED table through this connection, whoever
        issues it (a Ledger method or SQL on `ledger.db`), so no write can leave a cache stale. They exist only on this
        connection: other processes see a plain file, and their commits show up in PRAGMA data_version instead."""
        # The callback holds the counters only, not the ledger, so the connection and the ledger form no reference cycle.
        self.db.create_function("ratchet_written", 1, functools.partial(_bump, self._gen))
        for table, ops in WATCHED.items():
            for op in ops:
                name = f"ratchet_watch_{table}_{op.split()[0].lower()}"
                self.db.execute(
                    f"CREATE TEMP TRIGGER IF NOT EXISTS {name} AFTER {op} ON main.{table} "
                    f"BEGIN SELECT ratchet_written('{table}'); END"
                )

    def _settled(self, table: str) -> int | None:
        """This connection's write generation for `table`, for a reader-side cache key; None while the writer is inside
        a transaction, because a write it counted may not be committed yet (the TEMP trigger fires before the commit),
        and caching what a snapshot without it shows would outlive the commit. Read it before the snapshot's first
        statement: once the writer is outside a transaction, every counted write has ended and the snapshot sees it."""
        gen = self._gen[table]
        return None if self.db.in_transaction else gen

    def _reader(self) -> tuple[sqlite3.Connection | None, threading.RLock]:
        """A read-only connection for `_reads` methods, on a file ledger in WAL mode (where readers never block the
        writer); None elsewhere, or when it cannot be opened (the writer then serves reads, as before)."""
        lock = threading.RLock()
        if self.journal_mode != "wal":
            return None, lock
        try:
            ro = sqlite3.connect(
                Path(self.path).resolve().as_uri() + "?mode=ro",
                uri=True,
                check_same_thread=False,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,  # explicit read transactions only (`_reads`), never an implicit one left open
            )
        except sqlite3.Error:
            return None, lock
        ro.row_factory = sqlite3.Row
        return ro, lock

    @_locked
    def close(self) -> None:
        """Close both connections. The ledger cannot be used afterwards."""
        if self._ro is not None:
            with self._ro_lock:
                self._ro.close()
        self.db.close()

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
    def log_failure(self, **row: Any) -> int:
        """One `failures` row (FAILURE_COLUMNS; `stage` one of FAILURE_STAGES, `error` a class, status and code, never a
        message). `cost_usd` is what a request that left no `requests` row spent, so `totals()` can count it; a failure
        around an answered request (its cost is in its own row) records 0. A failure never starts a live ledger's day 1
        (a scanner's 401, a teacher down during bring-up): `__init__` rebuilds the origin from `requests` alone, so a
        failure-made origin would move back on a restart. Until the first request a live ledger files a failure under
        `self.day` (0 on a fresh file).
        Returns the row id."""
        unknown = set(row) - FAILURE_COLUMNS
        if unknown:
            raise ValueError(f"failures has no columns {sorted(unknown)}")
        if row.get("stage") not in FAILURE_STAGES:
            raise ValueError(f"failure stage must be one of {', '.join(FAILURE_STAGES)}, not {row.get('stage')!r}")
        row.setdefault("ts", time.time())
        if self._t0 is not None:  # only a request starts the clock
            self._tick_day(row["ts"])
        row.setdefault("day", self.day)
        row.setdefault("cost_usd", 0.0)
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        cur = self.db.execute(f"INSERT INTO failures ({cols}) VALUES ({qs})", list(row.values()))
        self.db.commit()
        return int(cur.lastrowid)

    @_reads
    def request_record(self, request_id: str) -> dict[str, Any] | None:
        """The `requests` row (metadata only: the table holds no text) the gateway logged under `request_id`, or None."""
        r = self._rdb.execute("SELECT * FROM requests WHERE request_id=? ORDER BY id LIMIT 1", (request_id,)).fetchone()
        return dict(r) if r is not None else None

    @_reads
    def failures_for(self, request_id: str) -> list[dict[str, Any]]:
        """The `failures` rows of one request, oldest first: a failed request's, or the failed optional calls around an
        answered one."""
        rows = self._rdb.execute("SELECT * FROM failures WHERE request_id=? ORDER BY id", (request_id,))
        return [dict(r) for r in rows]

    @_reads
    def failure_counts(self, since: float) -> dict[str, int]:
        """Failures since `since` (a wall-clock time) by stage, most frequent first."""
        rows = self._rdb.execute(
            "SELECT stage, COUNT(*) n FROM failures WHERE ts>=? GROUP BY stage ORDER BY n DESC, stage", (since,)
        )
        return {r["stage"]: int(r["n"]) for r in rows}

    @_reads
    def spend_since(self, since: float, clients: Iterable[str | None]) -> dict[str | None, float]:
        """US dollars each client in `clients` (an API key name, or None for requests without one) spent since `since`,
        answered and failed requests together: one indexed range per client and table (`requests_client_ts`,
        `failures_client_ts`), so a scrape stays cheap on a large ledger."""
        out: dict[str | None, float] = {}
        for client in dict.fromkeys(clients):
            where = "client IS NULL" if client is None else "client=?"
            args: tuple[Any, ...] = (since,) if client is None else (client, since)
            total = 0.0
            for table in ("requests", "failures"):
                q = f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM {table} WHERE {where} AND ts>=?"
                total += float(self._rdb.execute(q, args).fetchone()[0])
            out[client] = total
        return out

    @_reads
    def api_usage_since(self, since: float) -> dict[str | None, tuple[int, float]]:
        """Per client (an API key name, or None for requests without one): (answered requests, US dollars) of gateway API
        requests since `since` (a wall-clock time), the spend of failed ones included. Gateway API requests are the rows
        with a gateway request id; Scout's agents and the simulator call the cascade without one, so their spend stays
        under their own guard (INV-11). What `spend.SpendMeter` reads when it starts (ADR 0063): one pass over the
        `(client, ts)` index of each table, at startup only."""
        out: dict[str | None, tuple[int, float]] = {}
        rows = self._rdb.execute(
            "SELECT client, COUNT(*) n, COALESCE(SUM(cost_usd), 0.0) usd FROM requests"
            " WHERE ts>=? AND request_id IS NOT NULL GROUP BY client",
            (since,),
        )
        for r in rows:
            out[r["client"]] = (int(r["n"]), float(r["usd"]))
        failed = self._rdb.execute(
            "SELECT client, COALESCE(SUM(cost_usd), 0.0) usd FROM failures"
            " WHERE ts>=? AND cost_usd>0 AND request_id IS NOT NULL GROUP BY client",
            (since,),
        )
        for r in failed:
            n, usd = out.get(r["client"], (0, 0.0))
            out[r["client"]] = (n, usd + float(r["usd"]))
        return out

    @_reads
    def events_since(self, kind: str, since: float) -> list[dict[str, Any]]:
        """The events of one kind written since `since` (a wall-clock time), oldest first (`events_kind_ts`)."""
        rows = self._rdb.execute("SELECT id, ts, day, kind, detail FROM events WHERE kind=? AND ts>=? ORDER BY id", (kind, since))
        return [dict(r) for r in rows]

    @_locked
    def client_requests_since(self, client: str, ts: float) -> int:
        """Requests logged for one API client since `ts`. The per-key daily quota no longer counts rows on the request
        path: it reads `spend.SpendMeter` (ADR 0063), which reads the ledger at startup through `api_usage_since`."""
        return int(self.db.execute("SELECT COUNT(*) FROM requests WHERE client=? AND ts>=?", (client, ts)).fetchone()[0])

    @_reads
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
        return [dict(r) for r in self._rdb.execute(q + " GROUP BY day ORDER BY day", args)]

    # ---------- compute time (ADR 0023) ----------
    def now(self) -> float:
        """The clock compute intervals are recorded on. A live ledger (`auto_day`) uses wall-clock time. Simulations and
        tests run on a synthetic clock where day N starts at N x 86400 s (a simulated day takes a second of wall time,
        so its 24 hours cannot be on the wall clock); there `now()` is the current day at the wall clock's time of day."""
        t = time.time()
        return t if self.auto_day else self.day * DAY_SECONDS + t % DAY_SECONDS

    def _origin(self, now: float | None) -> float:
        """When day 1 starts on a live ledger: at the first logged request (as `_tick_day` counts), or at `now` before
        there is one."""
        if self._t0 is not None:
            return self._t0
        return self.now() if now is None else now

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

    def split_of(self, task: str, messages: list[dict[str, Any]]) -> str:
        """The split ('train' or 'golden') `add_example` puts these messages in, under any conditions: a pure function of
        the task and the messages (`_split`), for the metrics. Reads nothing."""
        return self._split(example_hash(task, messages))

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

    @_reads
    def bill_of_materials(self) -> dict[str, Any]:
        """Model bill of materials: which teachers and licenses produced the training data. `examples_sha256` (over
        every example hash in order) is recomputed only when the examples' count, highest id or rewrite generation
        moved: ids are never reused, so an insert or a delete moves the first two, and a hash rewritten on this
        connection moves the third (another process never rewrites a hash in place; it is the example's identity)."""
        gen = self._settled("examples")  # before the snapshot: a later write makes the next call recompute
        rows = self._rdb.execute(
            "SELECT teacher_model, license, license_url, split, COUNT(*) n FROM examples"
            " GROUP BY teacher_model, license, license_url, split"
        ).fetchall()
        count, top = self._rdb.execute("SELECT COUNT(*), MAX(id) FROM examples").fetchone()
        key = (int(count), top, gen)
        if gen is not None and self._digest is not None and self._digest[0] == key:
            sha = self._digest[1]
        else:
            digest = hashlib.sha256()
            for r in self._rdb.execute("SELECT hash FROM examples ORDER BY hash"):
                digest.update(r["hash"].encode())
            sha = digest.hexdigest()
            self._digest = (key, sha) if gen is not None else None
        return {"generated_at": time.time(), "examples_sha256": sha, "sources": [dict(r) for r in rows]}

    @_reads
    def privacy_stats(self) -> dict[str, int]:
        r = self._rdb.execute(
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

    @_reads
    def bom_status(self) -> dict[int, str]:
        """{version id: 'signed' | 'unsigned'} for versions that have a BOM."""
        out = {}
        for r in self._rdb.execute("SELECT id, bom FROM versions WHERE bom IS NOT NULL"):
            out[int(r["id"])] = "signed" if json.loads(r["bom"]).get("signature") else "unsigned"
        return out

    @staticmethod
    def _version(r: sqlite3.Row) -> Version:
        """A new Version from a `versions` row, with its own eval dict (callers may change it freely)."""
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
        out = [self._version(r) for r in self.db.execute("SELECT * FROM versions ORDER BY id")]
        if states is not None:
            wanted = set(states)
            out = [v for v in out if v.state in wanted]
        return out

    @_locked
    def version(self, vid: int) -> Version | None:
        r = self.db.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
        return self._version(r) if r is not None else None

    def _routed(self) -> dict[str, sqlite3.Row]:
        """The newest version row per routed state (ROUTED_STATES), rebuilt only when a version was written: on this
        connection (the `versions` generation, bumped by a TEMP trigger on every write) or by another connection, such
        as the cron tick or the cost CLI (PRAGMA data_version). Two cheap reads per call instead of every version row and
        its eval. Call it under the lock."""
        key = (self._gen["versions"], int(self.db.execute("PRAGMA data_version").fetchone()[0]))
        if key != self._routing_key:
            routed: dict[str, sqlite3.Row] = {}
            for r in self.db.execute(ROUTED_SQL, ROUTED_STATES):
                routed[r["state"]] = r  # ascending ids: the newest wins, as `versions([state])[-1]` picks it
            self._routing, self._routing_key = routed, key
        return self._routing

    @_locked
    def live(self) -> Version | None:
        """The live version (the newest one, should there be two), from the routing snapshot; a fresh copy per call."""
        return self.by_state("live")

    @_locked
    def by_state(self, state: str) -> Version | None:
        """The newest version in `state`. The routed states come from the routing snapshot, any other from one indexed
        query; either way a fresh copy per call."""
        if state in ROUTED_STATES:
            r = self._routed().get(state)
        else:
            r = self.db.execute("SELECT * FROM versions WHERE state=? ORDER BY id DESC LIMIT 1", (state,)).fetchone()
        return self._version(r) if r is not None else None

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
    def event(self, kind: str, detail: str) -> None:
        """Append to the release log, then hand the row ({id, ts, day, kind, detail}) to every listener
        (`add_event_listener`), outside the lock when the caller does not hold it. A failing listener is ignored: the
        event is already stored, and a log handler must never fail what wrote it."""
        with self._lock:
            ts, day = time.time(), self.day
            cur = self.db.execute("INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (ts, day, kind, detail))
            self.db.commit()
            listeners = tuple(self._listeners)
        if listeners:
            row = {"id": int(cur.lastrowid), "ts": ts, "day": day, "kind": kind, "detail": detail}
            for fn in listeners:
                try:
                    fn(row)
                except Exception:  # noqa: BLE001 - the event is stored; a listener (the log) must not undo that
                    pass

    def add_event_listener(self, fn: Callable[[dict[str, Any]], object]) -> None:
        """Call `fn(row)` after every event this ledger writes (the gateway mirrors them to the log, ADR 0062). Adding the
        same function twice calls it once."""
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    @_reads
    def events(self, last: int | None = None) -> list[dict[str, Any]]:
        """Every event, oldest first; with `last`, only the newest `last` of them (still oldest first), read without
        the rest (the dashboard shows 40 of a log that only grows)."""
        q = "SELECT id, ts, day, kind, detail FROM events"
        if last is None:
            return [dict(r) for r in self._rdb.execute(q + " ORDER BY id")]
        if isinstance(last, bool) or not isinstance(last, int) or last < 0:
            raise ValueError(f"last must be a whole number, 0 or more, not {last!r}")
        return [dict(r) for r in self._rdb.execute(f"SELECT * FROM ({q} ORDER BY id DESC LIMIT ?) ORDER BY id", (last,))]

    @_locked
    def version_live_quality(self, vid: int, current_stage_only: bool = True) -> tuple[int, float | None]:
        """(audits, audited precision) for a version, from teacher audits of answers it served (in its current stage,
        by default: rows after `stage_since`).

        Incremental (ADR 0060): a tally per (version, stage) keeps the count and sum up to the last id it read, and each
        call reads only rows above it, through `requests_version_audited`. SQLite assigns ids in commit order, so no row
        can appear below a tally later. A deleted or rewritten request on this connection (the `requests` generation) or
        any commit by another connection (PRAGMA data_version) starts the tally again from the stage's first row. The
        numbers are exactly what one `COUNT(*)`, `AVG(audit_agree)` over the stage gives."""
        since = 0
        if current_stage_only:
            v = self.version(vid)
            since = v.stage_since if v else 0
        gen = (self._gen["requests"], int(self.db.execute("PRAGMA data_version").fetchone()[0]))
        key = (vid, since)
        tally = self._tallies.get(key)
        if tally is None or tally.gen != gen:
            tally = _Tally(gen, since)
            self._tallies.pop(key, None)
            self._tallies[key] = tally
            while len(self._tallies) > MAX_TALLIES:
                self._tallies.pop(next(iter(self._tallies)))
        r = self.db.execute(AUDIT_TALLY_SQL, (vid, tally.last_id)).fetchone()
        if r["top"] is not None:
            tally.n += int(r["n"])
            tally.agreed_n += int(r["k"])
            tally.agreed += int(r["s"])
            tally.last_id = int(r["top"])
        return tally.n, (tally.agreed / tally.agreed_n if tally.agreed_n else None)

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
        r = self.db.execute(SHADOW_QUALITY_SQL, (vid,)).fetchone()
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

    @_reads
    def stats_by_day(self) -> list[dict[str, Any]]:
        """Per day: requests, shares, cost, savings, latency, audited agreement, and how many requests a stand-in
        answered because the teacher was unavailable (`degraded`: the student's below-threshold answer;
        `fallback_teacher`: the fallback teacher; ADR 0019). `p50_student_ms` and `p95_ms` are nearest-rank percentiles
        of `wall_ms` (the whole cascade on the gateway's clock) over the day's student-served and all requests that
        recorded it (None without any). A day's percentiles are computed again only when its count or highest id moved
        (ids are never reused) or a request was rewritten on this connection, so a stats read sorts today's rows only."""
        gen = self._settled("requests")  # before the snapshot: a later rewrite makes the next call recompute
        rows = self._rdb.execute(
            """SELECT day, COUNT(*) requests,
                      SUM(served_by='student') student_served,
                      SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      AVG(latency_ms) latency_ms,
                      AVG(CASE WHEN audited=1 THEN audit_agree END) audit_agree,
                      SUM(route='degraded') degraded,
                      SUM(route='fallback_teacher') fallback_teacher,
                      MAX(id) top, COUNT(wall_ms) timed,
                      COALESCE(SUM(served_by='student' AND wall_ms IS NOT NULL), 0) timed_student
               FROM requests GROUP BY day ORDER BY day"""
        ).fetchall()
        out = []
        for r in rows:
            n = r["requests"] or 1
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            p50_student, p95 = self._day_percentiles(r["day"], (r["requests"], r["top"], gen), r["timed_student"], r["timed"])
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
                    "p50_student_ms": p50_student,
                    "p95_ms": p95,
                }
            )
        days = {r["day"] for r in rows}
        for day in [d for d in self._day_latency if d not in days]:
            del self._day_latency[day]
        return out

    def _day_percentiles(
        self, day: int | None, key: tuple[int, int, int | None], timed_student: int, timed: int
    ) -> tuple[float | None, float | None]:
        """(p50 of the day's student-served wall_ms, p95 of all of its wall_ms), cached under `key` (count, highest id,
        settled request generation; never cached without one). Call it from a `_reads` method."""
        cached = self._day_latency.get(day)
        if key[2] is not None and cached is not None and cached[0] == key:
            return cached[1], cached[2]
        p50 = self._nth_wall(_nearest_rank(0.5, timed_student), day, "student") if timed_student else None
        p95 = self._nth_wall(_nearest_rank(0.95, timed), day, None) if timed else None
        if key[2] is None:
            self._day_latency.pop(day, None)
        else:
            self._day_latency[day] = (key, p50, p95)
        return p50, p95

    def _where_wall(self, day: int | None, served_by: str | None) -> tuple[str, list[Any]]:
        q, args = " FROM requests WHERE wall_ms IS NOT NULL", []
        if day is not None:
            q += " AND day=?"
            args.append(day)
        if served_by is not None:
            q += " AND served_by=?"
            args.append(served_by)
        return q, args

    def _nth_wall(self, offset: int, day: int | None, served_by: str | None) -> float | None:
        """The wall_ms at 0-based `offset` in ascending order among the rows the filters select: an ORDER BY with an
        OFFSET, no SQLite extension."""
        q, args = self._where_wall(day, served_by)
        r = self._rdb.execute(f"SELECT wall_ms{q} ORDER BY wall_ms LIMIT 1 OFFSET ?", (*args, offset)).fetchone()
        return float(r[0]) if r is not None else None

    @_reads
    def latency_percentiles(
        self, day: int | None = None, served_by: str | None = None, qs: Iterable[float] = (0.5, 0.95)
    ) -> dict[float, float | None]:
        """{q: wall_ms} for each quantile in `qs` (0 to 1) over the requests that recorded `wall_ms`, of one day and one
        `served_by` ('student' or 'teacher') when given, by the nearest-rank rule (`_nearest_rank`: a value that was
        observed, never an interpolation). None for each q when no row matches. Overhead per request is `wall_ms -
        latency_ms` (the providers' part)."""
        quantiles = _check_quantiles(qs)
        q, args = self._where_wall(day, served_by)
        n = int(self._rdb.execute(f"SELECT COUNT(*){q}", args).fetchone()[0])
        return {p: (self._nth_wall(_nearest_rank(p, n), day, served_by) if n else None) for p in quantiles}

    @_reads
    def stats_by_task(self) -> list[dict[str, Any]]:
        """Per task: requests, student share, escalations, cost, savings, latency, and `routes`: how many requests took
        each route (`unrecorded` for rows older than the route column), most frequent first."""
        rows = self._rdb.execute(
            """SELECT task, COUNT(*) requests, SUM(served_by='student') student_served, SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline, AVG(latency_ms) latency_ms
               FROM requests GROUP BY task ORDER BY COUNT(*) DESC"""
        ).fetchall()
        routes: dict[str, dict[str, int]] = {}
        counted = self._rdb.execute("SELECT task, route, COUNT(*) n FROM requests GROUP BY task, route ORDER BY n DESC, route")
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

    @_reads
    def stats_by_client(self) -> list[dict[str, Any]]:
        """Per API key: requests, cost and savings (the basis for per-client receipts)."""
        rows = self._rdb.execute(
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

    @_reads
    def totals(self) -> dict[str, float]:
        """Requests, cost and baseline over the whole ledger. `failed_cost_usd` is what failed requests spent without
        leaving a `requests` row (the student's samples before a teacher failure, NFR-3); it counts against the savings:
        `saved_usd` = baseline - cost - failed cost, `savings_pct` = 1 - (cost + failed cost) / baseline. A ledger without
        failures gives exactly v0.4's numbers."""
        r = self._rdb.execute("SELECT COUNT(*) n, SUM(cost_usd) c, SUM(baseline_cost_usd) b FROM requests").fetchone()
        c, b = r["c"] or 0.0, r["b"] or 0.0
        failed = float(self._rdb.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM failures").fetchone()[0])
        spent = c + failed
        return {
            "requests": r["n"],
            "cost_usd": c,
            "baseline_usd": b,
            "saved_usd": b - spent,
            "savings_pct": 0.0 if b == 0 else 1 - spent / b,
            "failed_cost_usd": failed,
        }
