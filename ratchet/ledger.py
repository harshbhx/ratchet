"""SQLite ledger: every request, every training example (with provenance), every model version."""

from __future__ import annotations

import bisect
import contextlib
import functools
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from . import licenses, tasks
from .config import MAX_GPUS, MAX_USD_PER_GPU_HOUR
from .stats import wilson

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
    optional_failed INTEGER,   -- 1: an optional teacher call of this request (audit, arbitration) failed
    -- Data governance (FR-13.1, FR-13.2, ADR 0080):
    end_user TEXT,             -- HMAC of the request's safety_identifier or user (privacy.EndUsers), never the value
    store_opt_out TEXT,        -- why its teacher answers could not be stored (privacy.OPT_OUTS); NULL: no opt-out
    -- The gateway's request id ('req_' + 32 hex; the completion id is 'chatcmpl-' + the same digits), which feedback
    -- names the request by (FR-13.30); NULL for internal calls (Scout, the simulator) and older rows.
    request_id TEXT,
    -- HMAC of the second end-user identifier the request carried ('user' beside 'safety_identifier'), in the same scope as
    -- end_user, never the value: a correction must name it too, so a no-train registration made with it applies (FR-13.30)
    end_user_secondary TEXT
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
    origin TEXT,               -- 'escalation' | 'audit' | 'arbitration' | 'backfill'; NULL: not recorded (FR-16.8);
                               -- 'feedback' | 'import' for a human or imported label (LABEL_ORIGINS, ADR 0091)
    job TEXT,                  -- the backfill job that wrote it; NULL otherwise (FR-16.8)
    contested INTEGER DEFAULT 0,      -- 1: a later teacher label of the same request disagreed (FR-16.7)
    checked_epoch INTEGER DEFAULT 0,  -- the teacher-watch epoch of its last self-consistency check (FR-16.7)
    -- Data governance (FR-13.2, ADR 0080): who sent the request it was first stored from; NULL for internal calls
    client TEXT,               -- API key name, never the key; later senders are in example_clients
    end_user TEXT,             -- the end user's HMAC (privacy.EndUsers), never the raw value
    -- Retention and erasure (FR-13.3, FR-13.4, ADR 0081). A purge keeps the row as a tombstone: hash, task, split, seen,
    -- teacher and license stay; messages, completion and params become NULL.
    last_seen_ts REAL,         -- when it was stored or last seen again (a duplicate); retention counts from it
    purged_ts REAL,            -- when its text was removed; NULL while it is kept
    purge_reason TEXT,         -- 'retention' (a storable duplicate restores it) | 'erasure' (never stored again)
    purged_train INTEGER,      -- 1: it counted as a training row when purged (dataset.counts_as_train)
    prompt_hash TEXT,          -- a purged row's prompt key (ledger.prompt_key), so its `seen` still counts (INV-3)
    -- Human labels (FR-3.54, FR-4.50, ADR 0091):
    verified INTEGER DEFAULT 0,       -- 1: a person wrote the label (teacher_model 'human': a correction or an import)
    golden_only INTEGER DEFAULT 0     -- 1: held out for good, even past FREQUENT, with every variant of its prompt
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
CREATE TABLE IF NOT EXISTS erasures (        -- erasure requests (FR-13.3) and, later, their certificates (FR-16.9)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER,
    key TEXT,                  -- one erased example key (FR-16.9); NULL for FR-13.3's erasures, whose keys are in erasure_items
    tainted TEXT,              -- JSON: ids of the versions that may have been trained on the erased examples
    status TEXT,               -- 'pending': tainted versions exist and none was replaced yet | 'complete'
    replacement INTEGER,       -- the version trained without them (FR-16.9)
    certificate TEXT,          -- JSON: the signed certificate (FR-16.9)
    actor TEXT,                -- who asked: an admin's or an API key's name, or the CLI's --actor; never a key
    reason TEXT,               -- why, as the operator wrote it, redacted (ratchet/redact.py)
    scope TEXT,                -- JSON: what was selected; end users as HMACs, `contains` as its SHA-256, never text
    purged INTEGER,            -- examples whose text this erasure removed (or whose retention tombstone it made final)
    kept INTEGER               -- shared examples kept under RATCHET_ERASURE_SHARED=keep, with the erased party unlinked
);
-- Data governance tables (FR-13.2, ADR 0080). Hashes and key names only, never a raw end-user value or a key.
CREATE TABLE IF NOT EXISTS example_clients (  -- other API clients whose requests matched a stored example
    hash TEXT NOT NULL,        -- examples.hash
    client TEXT NOT NULL,      -- API key name; examples.client holds the first sender and is never listed here
    PRIMARY KEY (hash, client)
);
CREATE TABLE IF NOT EXISTS no_train (         -- end users whose requests are never stored as training examples
    end_user_hash TEXT PRIMARY KEY,  -- privacy.EndUsers hash, scoped to `client`
    ts REAL,
    client TEXT,               -- the API key name the hash belongs to; NULL for requests sent without a key; '*' for
                               -- an admin's registration for every client (privacy.EVERY_CLIENT)
    admin TEXT                 -- the admin's name when an admin registered it; then only an admin can remove it
);
CREATE TABLE IF NOT EXISTS erasure_items (    -- the examples each erasure purged: hashes, never text (FR-13.3)
    erasure_id INTEGER NOT NULL, hash TEXT NOT NULL, task TEXT, split TEXT,
    PRIMARY KEY (erasure_id, hash)
);
CREATE TABLE IF NOT EXISTS feedback (          -- human verdicts on served answers (FR-13.30): ids and names, never text
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER,      -- when the verdict was last given; a later verdict on the same request replaces it
    request_id TEXT NOT NULL UNIQUE,  -- requests.request_id: one row per request
    client TEXT,               -- the API key name that sent the request (and the feedback); NULL when keys are off
    verdict TEXT,              -- 'correct' | 'incorrect' (feedback.VERDICTS)
    corrected_present INTEGER DEFAULT 0,  -- 1: a correction came with a verdict on this request
    -- Copied from the request row, so the statistics outlive it (request retention folds request rows):
    task TEXT, version INTEGER, served_by TEXT, route TEXT,
    stored INTEGER DEFAULT 0,  -- 1: a correction was stored as a human-labelled example
    example_hash TEXT          -- the example that correction is (examples.hash); NULL otherwise
);
CREATE TABLE IF NOT EXISTS daily_rollup (     -- request rows folded by retention (FR-13.4): sums only, no row or end user
    day INTEGER, task TEXT, client TEXT, route TEXT, store_opt_out TEXT,
    requests INTEGER, student_served INTEGER, teacher_served INTEGER, escalated INTEGER,
    cost REAL, baseline REAL,  -- SUM(cost_usd), SUM(baseline_cost_usd)
    teacher_cost REAL, student_cost REAL, split_unknown INTEGER, split_unknown_cost REAL,  -- cost_by_day's split
    sampled INTEGER, avoided REAL, student_in_tokens INTEGER, student_out_tokens INTEGER, served_with_tokens INTEGER,
    audited INTEGER, audit_agree_sum INTEGER, audit_n INTEGER,  -- audit_n: audited rows that recorded agreement
    wall_ms_sum REAL, latency_n INTEGER, stored_sum INTEGER, stored_n INTEGER,
    folded_ts REAL             -- when these rows were folded; one fold may leave several rows per key, readers sum them
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


def prompt_key(r: Mapping[str, Any]) -> str:
    """The hash of an example's task and messages without its conditions: the same for every variant of one request,
    and the example's own hash when it has none. A purged row has no messages left, so it carries the key it had
    (`prompt_hash`, written by the purge)."""
    if r["prompt_hash"]:
        return str(r["prompt_hash"])
    if r["params"] and r["messages"]:
        return example_hash(r["task"], json.loads(r["messages"]))
    return str(r["hash"])


def seen_count(r: Mapping[str, Any]) -> int:
    """How often an example's exact request occurred (`examples.seen`). A label stored without an occurrence of its
    request (an import, or a correction of a request already counted in another variant) is inserted with 0 and counts
    0 (ADR 0091); NULL, in a row read before the column existed, counts once."""
    return 1 if r["seen"] is None else int(r["seen"])


def count_prompts(rows: Iterable[Mapping[str, Any]]) -> Counter[str]:
    """How often each request was seen (`prompt_key`), over every stored variant, purged rows included: a purge removes
    text, not the fact that the request repeated, so it never moves a frequent request's other variants back to the
    golden set (INV-3). A label that was not an occurrence of its request adds nothing (`seen_count`)."""
    seen: Counter[str] = Counter()
    for r in rows:
        seen[prompt_key(r)] += seen_count(r)
    return seen


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
        "end_user": "TEXT",
        "store_opt_out": "TEXT",
        "request_id": "TEXT",
        "end_user_secondary": "TEXT",
    },
    "examples": {
        "seen": "INTEGER DEFAULT 1",
        "redactions": "INTEGER DEFAULT 0",
        "params": "TEXT",
        "origin": "TEXT",
        "job": "TEXT",
        "contested": "INTEGER DEFAULT 0",
        "checked_epoch": "INTEGER DEFAULT 0",
        "client": "TEXT",
        "end_user": "TEXT",
        "last_seen_ts": "REAL",
        "purged_ts": "REAL",
        "purge_reason": "TEXT",
        "purged_train": "INTEGER",
        "prompt_hash": "TEXT",
        "verified": "INTEGER DEFAULT 0",
        "golden_only": "INTEGER DEFAULT 0",
    },
    "versions": {"stage_since": "INTEGER DEFAULT 0", "bom": "TEXT"},
    "erasures": {"actor": "TEXT", "reason": "TEXT", "scope": "TEXT", "purged": "INTEGER", "kept": "INTEGER"},
}
# Data a new column needs in rows written before it existed: (the columns it reads, the UPDATE), run once, right after
# `_migrate` adds the column, when the table has the columns it reads (a hand-made partial file may not).
BACKFILLS: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {
    ("examples", "last_seen_ts"): (("ts",), "UPDATE examples SET last_seen_ts = ts WHERE last_seen_ts IS NULL"),
}
# Created after migrations, because they index columns that old files only get from MIGRATIONS.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS requests_client_ts ON requests (client, ts)",
    "CREATE INDEX IF NOT EXISTS compute_intervals_kind_start ON compute_intervals (kind, start_ts)",
    # A version's audits (version_live_quality, the release gate's counts), its shadow rows (shadow_quality) and a day's
    # rows (stats and receipts by day) without a full scan as the ledger grows (ADR 0024).
    "CREATE INDEX IF NOT EXISTS requests_version_audited ON requests (student_version, audited, id)",
    # One version's rows in id order, a chunk at a time (`replay_rows`), without sorting the version's whole history.
    "CREATE INDEX IF NOT EXISTS requests_version_id ON requests (student_version, id)",
    "CREATE INDEX IF NOT EXISTS requests_shadow_version ON requests (shadow_version)",
    "CREATE INDEX IF NOT EXISTS requests_day ON requests (day)",
    "CREATE INDEX IF NOT EXISTS version_leaves_key ON version_leaves (key)",
    "CREATE INDEX IF NOT EXISTS teacher_checks_epoch ON teacher_checks (epoch)",
    "CREATE INDEX IF NOT EXISTS erasures_status ON erasures (status)",
    # One end user's requests over time (FR-13.2): what erasure, export and per-user limits look up.
    "CREATE INDEX IF NOT EXISTS requests_end_user_ts ON requests (end_user, ts)",
    "CREATE INDEX IF NOT EXISTS requests_end_user_secondary ON requests (end_user_secondary)",
    # Retention and erasure (FR-13.3, FR-13.4): old requests and examples, a client's or an end user's examples.
    "CREATE INDEX IF NOT EXISTS requests_ts ON requests (ts)",
    "CREATE INDEX IF NOT EXISTS examples_last_seen ON examples (last_seen_ts)",
    "CREATE INDEX IF NOT EXISTS examples_client ON examples (client)",
    "CREATE INDEX IF NOT EXISTS examples_end_user ON examples (end_user)",
    "CREATE INDEX IF NOT EXISTS example_clients_client ON example_clients (client)",
    "CREATE INDEX IF NOT EXISTS daily_rollup_day ON daily_rollup (day)",
    # Feedback (FR-13.30): a request by the gateway's id, and one version's verdicts (its human precision).
    "CREATE INDEX IF NOT EXISTS requests_request_id ON requests (request_id)",
    "CREATE INDEX IF NOT EXISTS feedback_version ON feedback (version)",
]
_INDEX_DDL = re.compile(r"CREATE INDEX IF NOT EXISTS (\w+) ON (\w+) \(([\w, ]+)\)")
# Tables and triggers created by SCHEMA for the evidence features (ADR 0024); the migration test checks an old file gains
# each of them.
EVIDENCE_TABLES = ("version_leaves", "teacher_checks", "meta", "receipts", "spend", "backfill_jobs", "tombstones", "erasures")
APPEND_ONLY_TABLES = ("version_leaves", "receipts")  # INV-17: UPDATE, DELETE and replacing INSERTs are refused
LEAF_SPLITS = ("train", "golden")  # the splits a version commits to (version_leaves.split)
_HEX32 = re.compile(r"[0-9a-f]{64}")


def _hex32(value: object, what: str) -> str:
    """A leaf's key or value: 64 lowercase hex characters (32 bytes), as `merkle.KeySet` and the BOM write them."""
    if not isinstance(value, str) or _HEX32.fullmatch(value) is None:
        raise ValueError(f"a leaf {what} must be 64 lowercase hex characters, not {value!r}")
    return value


# Tables created by SCHEMA for data governance (FR-13.2, ADR 0080); the migration test checks an old file gains them.
GOVERNANCE_TABLES = ("example_clients", "no_train", "erasure_items", "daily_rollup")
# Created by SCHEMA for human feedback (FR-13.30, ADR 0091); the migration test checks an old file gains it.
FEEDBACK_TABLES = ("feedback",)
# examples.origin of a human or imported label (ADR 0091); live teacher answers leave it NULL ('live').
LABEL_ORIGINS = ("feedback", "import")

DAY_SECONDS = 86400
DAY_ORIGIN_META = "day_origin"  # meta row: when day 1 started on a live ledger, kept once request retention deletes rows
PURGE_REASONS = ("retention", "erasure")  # examples.purge_reason
FOLD_CHUNK = 20000  # request rows folded per transaction, so a large fold never holds the ledger lock for long
PURGE_CHUNK = 5000  # examples retention purges per transaction (`privacy.apply_retention`), for the same reason
SQL_CHUNK = 500  # values per `IN (...)` list (SQLite's limit on bound variables is 999 in older builds)
# daily_rollup's sums (FR-13.4): what every stats reader needs from folded request rows, as `ROLLUP_INSERT` computes them
# from `requests` with the same expressions the readers use on live rows.
ROLLUP_SUMS = (
    "requests", "student_served", "teacher_served", "escalated", "cost", "baseline", "teacher_cost", "student_cost",
    "split_unknown", "split_unknown_cost", "sampled", "avoided", "student_in_tokens", "student_out_tokens",
    "served_with_tokens", "audited", "audit_agree_sum", "audit_n", "wall_ms_sum", "latency_n", "stored_sum", "stored_n",
)  # fmt: skip
ROLLUP_INSERT = """INSERT INTO daily_rollup (folded_ts, day, task, client, route, store_opt_out, requests, student_served,
    teacher_served, escalated, cost, baseline, teacher_cost, student_cost, split_unknown, split_unknown_cost, sampled,
    avoided, student_in_tokens, student_out_tokens, served_with_tokens, audited, audit_agree_sum, audit_n, wall_ms_sum,
    latency_n, stored_sum, stored_n)
    SELECT ?, day, task, client, route, store_opt_out, COUNT(*),
           COALESCE(SUM(served_by='student'), 0), COALESCE(SUM(served_by='teacher'), 0), COALESCE(SUM(escalated), 0),
           SUM(cost_usd), SUM(baseline_cost_usd),
           COALESCE(SUM(COALESCE(teacher_cost_usd, cost_usd, 0.0)), 0.0), SUM(student_cost_usd),
           COALESCE(SUM(student_cost_usd IS NULL), 0), SUM(CASE WHEN student_cost_usd IS NULL THEN cost_usd END),
           COALESCE(SUM(served_by='student' OR COALESCE(student_in_tokens, 0) + COALESCE(student_out_tokens, 0) > 0), 0),
           SUM(CASE WHEN served_by='student'
               THEN COALESCE(baseline_cost_usd, 0.0) - COALESCE(teacher_cost_usd, cost_usd, 0.0) END),
           SUM(student_in_tokens), SUM(student_out_tokens),
           COALESCE(SUM(served_by='student' AND student_in_tokens IS NOT NULL), 0),
           COALESCE(SUM(audited=1), 0), SUM(CASE WHEN audited=1 THEN audit_agree END),
           COUNT(CASE WHEN audited=1 THEN audit_agree END),
           SUM(latency_ms), COUNT(latency_ms), SUM(stored), COUNT(stored)
    FROM requests WHERE {where} GROUP BY day, task, client, route, store_opt_out"""
# `Ledger.cost_by_day`'s columns and the daily_rollup sum each one adds (with its zero), for days with folded rows.
COST_COLUMNS: dict[str, tuple[str, float]] = {
    "requests": ("requests", 0),
    "student_served": ("student_served", 0),
    "teacher_served": ("teacher_served", 0),
    "cost_usd": ("cost", 0.0),
    "baseline_usd": ("baseline", 0.0),
    "teacher_usd": ("teacher_cost", 0.0),
    "student_usd": ("student_cost", 0.0),
    "split_unknown": ("split_unknown", 0),
    "split_unknown_usd": ("split_unknown_cost", 0.0),
    "sampled": ("sampled", 0),
    "avoided": ("avoided", 0.0),
    "student_in_tokens": ("student_in_tokens", 0),
    "student_out_tokens": ("student_out_tokens", 0),
    "served_with_tokens": ("served_with_tokens", 0),
}
# What `Ledger.export_page` exports (FR-13.5), and the filters each kind takes.
EXPORT_KINDS = ("examples", "requests", "versions", "events", "erasures")
EXPORT_FILTERS: dict[str, frozenset[str]] = {
    "examples": frozenset({"task", "split", "since", "client"}),
    "requests": frozenset({"task", "since", "client"}),
    "versions": frozenset({"since"}),
    "events": frozenset({"since"}),
    "erasures": frozenset({"since"}),
}
# The route reported for rows written before requests had a route column (v0.4 and earlier files).
UNRECORDED_ROUTE = "unrecorded"
# What GPUs ran (ADR 0023): the self-hosted student endpoint, a Night School training job, and a separate evaluation job
# (none records one yet: the live golden eval runs on the student endpoint, whose uptime is counted).
COMPUTE_KINDS = ("student_endpoint", "training_job", "eval_job")
# Rows `Ledger.replay_rows` reads per locked statement, so a long history never holds the lock for long.
REPLAY_CHUNK = 20_000
# The request columns a savings receipt's leaves are built from (ratchet/receipts.py, FR-15.5): metadata only, never
# request text (INV-12); `client` is the API key's name (INV-13).
RECEIPT_COLUMNS = (
    "id, ts, day, task, client, served_by, route, student_version, escalated, audited, label_p, cost_usd,"
    " student_cost_usd, teacher_cost_usd, baseline_cost_usd, baseline_est_usd"
)
# Rows `Ledger.period_rows` reads per locked statement, so a busy day never holds the lock for long.
RECEIPT_CHUNK = 20_000


class ReceiptExists(ValueError):
    """A receipt for this (period, client) is already sealed: receipts are written once (INV-17)."""


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


def _chunks(values: list[Any], size: int = SQL_CHUNK) -> Iterator[list[Any]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _like(text: str) -> str:
    """A LIKE pattern that matches `text` anywhere, with `%`, `_` and the escape character taken literally."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _add(a: Any, b: Any) -> Any:
    """a + b, where None (SQL's SUM over nothing) adds nothing."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


# A stats row for a key that only folded request rows have (FR-13.4): every column a reader may read, empty.
_EMPTY_STATS: dict[str, Any] = {
    "requests": 0, "student_served": None, "escalated": None, "cost": None, "baseline": None, "latency_ms": None,
    "latency_sum": None, "latency_n": 0, "audit_agree": None, "audit_sum": None, "audit_n": 0, "degraded": 0,
    "fallback_teacher": 0, "end_users": 0, "stored_share": None, "stored_sum": None, "stored_n": 0,
}  # fmt: skip


def _merge(rows: list[dict[str, Any]], key: str, folded: Mapping[Any, Mapping[str, Any]]) -> dict[Any, dict[str, Any]]:
    """A stats query's rows (by `key`) plus the folded sums for the same keys (`Ledger._rollup_by`), in the rows' order
    with folded-only keys after. Sums add; averages are recomputed from their sums and counts. `end_users` is left as
    the rows have it: a fold keeps no end user."""
    out = {r[key]: r for r in rows}
    for k, f in folded.items():
        r = out.get(k)
        if r is None:
            r = out[k] = {key: k, **_EMPTY_STATS}
        r["requests"] = int(r["requests"] or 0) + int(f["requests"] or 0)
        for col in ("student_served", "escalated", "cost", "baseline"):
            r[col] = _add(r.get(col), f[col])
        r["degraded"] = int(r.get("degraded") or 0) + int(f.get("route:degraded", 0))
        r["fallback_teacher"] = int(r.get("fallback_teacher") or 0) + int(f.get("route:fallback_teacher", 0))
        for avg, total, n, rolled_total, rolled_n in (
            ("latency_ms", "latency_sum", "latency_n", "wall_ms_sum", "latency_n"),
            ("audit_agree", "audit_sum", "audit_n", "audit_agree_sum", "audit_n"),
            ("stored_share", "stored_sum", "stored_n", "stored_sum", "stored_n"),
        ):
            r[total] = _add(r.get(total), f[rolled_total])
            r[n] = int(r.get(n) or 0) + int(f[rolled_n] or 0)
            r[avg] = (r[total] or 0) / r[n] if r[n] else None
    return out


class NoTrainFull(Exception):
    """`Ledger.no_train_add` with a `limit` the new registrations would pass (the shared mock demo's)."""

    def __init__(self, limit: int):
        super().__init__(f"at most {limit} no-train registrations")
        self.limit = limit


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
        # Day 1 starts at the first logged request. Request retention deletes old rows, so a fold first records that
        # moment in `meta` (DAY_ORIGIN_META), which wins over the oldest row left; otherwise the days would renumber.
        origin = self.db.execute("SELECT value FROM meta WHERE key=?", (DAY_ORIGIN_META,)).fetchone()
        first = self.db.execute("SELECT MIN(ts) FROM requests").fetchone()[0]
        self._t0: float | None = float(origin[0]) if origin is not None else (float(first) if first is not None else None)
        last_day = self.db.execute(
            "SELECT MAX(COALESCE((SELECT MAX(day) FROM requests), 0), COALESCE((SELECT MAX(day) FROM daily_rollup), 0))"
        ).fetchone()[0]
        self.day = int(last_day)

    def locked(self) -> threading.RLock:
        """The ledger's lock, for a caller that reads, decides and then writes through several methods (an erasure, a
        retention purge): holding it, no other thread of this process writes in between. Re-entrant, so the ledger's
        own methods still work inside."""
        return self._lock

    @contextlib.contextmanager
    def atomic(self) -> Iterator[None]:
        """The ledger's lock and a write transaction (`BEGIN IMMEDIATE`), for a caller whose reads, decision and write
        must not interleave with another process's writes either: the importer CLI and the gateway may share the file
        (`feedback.store_label`). A method that commits ends the transaction early; anything left is committed on exit,
        or rolled back when the block raises."""
        with self._lock:
            self._begin()
            try:
                yield
            except BaseException:
                if self.db.in_transaction:
                    self.db.rollback()
                raise
            if self.db.in_transaction:
                self.db.commit()

    def _migrate(self) -> None:
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    reads, backfill = BACKFILLS.get((table, col), ((), ""))
                    if backfill and set(reads) <= have:
                        self.db.execute(backfill)
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
        rows = [dict(r) for r in self.db.execute(q + " GROUP BY day ORDER BY day", args)]
        if not self._has_rollup():
            return rows
        where = " AND ".join(["1=1"] + (["day >= ?"] if since is not None else []) + (["day <= ?"] if until is not None else []))
        by_day = {r["day"]: r for r in rows}
        for day, f in self._rollup_by("day", f"WHERE {where}", args).items():  # folded request rows (FR-13.4)
            r = by_day.setdefault(day, {"day": day, **{col: zero for col, (_, zero) in COST_COLUMNS.items()}})
            for col, (rolled, zero) in COST_COLUMNS.items():
                r[col] = _add(r[col], f[rolled]) or zero
        return [by_day[d] for d in sorted(by_day)]

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
        client: str | None = None,
        end_user: str | None = None,
        *,
        verified: bool = False,
        golden_only: bool = False,
        origin: str | None = None,
        replace: bool = False,
        bump: bool = True,
    ) -> bool | None:
        """Store a teacher-labelled example. Returns True when it was inserted, False when it was a duplicate, and None
        when it was refused because an erasure tombstoned its hash (FR-13.3): nothing is stored or bumped, and only the
        tombstone's `suppressed` count grows.
        Human and imported labels (ADR 0091, `feedback.store_label`, the only caller that passes these) set `verified`
        (a person wrote the label), `golden_only` (the row goes to the golden split whatever its hash says, and stays
        held out even past FREQUENT; sticky on a duplicate) and `origin` (`LABEL_ORIGINS`). With `replace`, a duplicate's
        label (completion, teacher, license, redactions, verified, origin) becomes this one: how a human label beats a
        teacher's on the same hash. Without it a duplicate keeps its label, so a teacher answer never overwrites a human
        one. `bump` False is for a label that is not a new occurrence of the request (an import, a correction of a request
        already counted): a duplicate's `seen` stays as it is and a new example starts at `seen` 0, so the label never
        counts toward FREQUENT (INV-3).
        `messages`, `completion` and `params` arrive already redacted; `redactions` counts the placeholders. `params`
        are the conditions the teacher answered under; only `target_params` of them are stored, and the same messages
        under other conditions are another example, in the same split (`_split`). `client` (the API key name) and
        `end_user` (its HMAC, `privacy.EndUsers`) record who sent the request (FR-13.2). A duplicate keeps its first
        sender and adds a client that is not that one to `example_clients`, so a client's data can be found later, and
        moves `last_seen_ts`, which retention counts from. A duplicate of an example whose text retention purged restores
        the text, with this answer's teacher and license (FR-13.4)."""
        target = target_params(params)
        h = example_hash(task, messages, target)
        # Without conditions, h is the split key. A golden-only label is golden whatever its hash says (ADR 0091).
        split = "golden" if golden_only else self._split(example_hash(task, messages) if target else h)
        text = json.dumps(messages, ensure_ascii=False)
        stored_params = json.dumps(target, ensure_ascii=False) if target else None
        now = time.time()
        if self.db.execute("SELECT 1 FROM tombstones WHERE key=?", (h,)).fetchone() is not None:
            return self._refuse_erased(h, now)
        label = (completion, teacher_model, license_name, license_url, redactions, int(verified), origin)
        try:
            self.db.execute(
                "INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model, license, license_url,"
                " split, redactions, params, client, end_user, last_seen_ts, verified, golden_only, origin, seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    self.day,
                    task,
                    h,
                    text,
                    completion,
                    teacher_model,
                    license_name,
                    license_url,
                    split,
                    redactions,
                    stored_params,
                    client,
                    end_user,
                    now,
                    int(verified),
                    int(golden_only),
                    origin,
                    int(bump),  # 1 as the column's default for every router write; 0 for a label that is no occurrence
                ),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            pass
        row = self.db.execute("SELECT purge_reason FROM examples WHERE hash=?", (h,)).fetchone()
        reason = row["purge_reason"] if row is not None else None
        if reason == "erasure":  # every erasure writes a tombstone; a row purged without one is refused all the same
            return self._refuse_erased(h, now)
        more = int(bump)
        if reason == "retention":
            self.db.execute(
                "UPDATE examples SET messages=?, completion=?, params=?, teacher_model=?, license=?, license_url=?,"
                " redactions=?, verified=?, origin=?, seen = seen + ?, last_seen_ts=?, purged_ts=NULL, purge_reason=NULL,"
                " purged_train=NULL, golden_only = MAX(COALESCE(golden_only, 0), ?) WHERE hash=?",
                (text, completion, stored_params, teacher_model, license_name, license_url, redactions, int(verified), origin)
                + (more, now, int(golden_only), h),
            )
        elif replace:  # a human label beats the stored one (`feedback.store_label` decides when)
            self.db.execute(
                "UPDATE examples SET completion=?, teacher_model=?, license=?, license_url=?, redactions=?, verified=?,"
                " origin=?, seen = seen + ?, last_seen_ts=?, golden_only = MAX(COALESCE(golden_only, 0), ?) WHERE hash=?",
                (*label, more, now, int(golden_only), h),
            )
        else:
            self.db.execute(
                "UPDATE examples SET seen = seen + ?, last_seen_ts=?, golden_only = MAX(COALESCE(golden_only, 0), ?)"
                " WHERE hash=?",
                (more, now, int(golden_only), h),
            )
        if client is not None:  # `IS NOT` so an example first stored without a client records this one
            self.db.execute(
                "INSERT OR IGNORE INTO example_clients (hash, client) SELECT hash, ? FROM examples"
                " WHERE hash=? AND client IS NOT ?",
                (client, h, client),
            )
        self.db.commit()
        return False

    def _refuse_erased(self, h: str, now: float) -> None:
        """Count a tombstoned example offered again (FR-13.3); the caller stores nothing."""
        self.db.execute("INSERT OR IGNORE INTO tombstones (key, ts) VALUES (?, ?)", (h, now))
        self.db.execute("UPDATE tombstones SET suppressed = suppressed + 1 WHERE key=?", (h,))
        self.db.commit()
        return None

    def _split(self, key: str) -> str:
        """'golden' or 'train', deterministic from `key`, the hash of an example's task and messages without its
        conditions (`example_hash(task, messages)`), so an example never moves between sets and every variant of one
        prompt (with a stop, in JSON mode, or bare) lands in the same one. The trainer sees only the messages, so a golden
        prompt stored again under other conditions in train would be trained on (INV-3) and inflate held-out precision
        (INV-6). Without conditions the key is the example's own hash, so every v0.4 split is unchanged."""
        return "golden" if int(key[:8], 16) / 0xFFFFFFFF < self.golden_fraction else "train"

    @_locked
    def examples(self, split: str | None = None, task: str | None = None) -> list[sqlite3.Row]:
        """Every stored example (of `split` and `task`), oldest first, purged ones included: their `messages`,
        `completion` and `params` are NULL and `purged_ts` is set (FR-13.3, FR-13.4), and their `seen` still counts."""
        q, args = "SELECT * FROM examples WHERE 1=1", []
        if split:
            q += " AND split=?"
            args.append(split)
        if task:
            q += " AND task=?"
            args.append(task)
        return list(self.db.execute(q + " ORDER BY id", args))

    @_locked
    def example_clients(self, h: str) -> list[str]:
        """Every API client whose request matched the example with hash `h`: its first sender, then the others in
        name order (`example_clients`). Empty when the example was stored without a client (or does not exist)."""
        row = self.db.execute("SELECT client FROM examples WHERE hash=?", (h,)).fetchone()
        first = [row["client"]] if row is not None and row["client"] is not None else []
        others = [r["client"] for r in self.db.execute("SELECT client FROM example_clients WHERE hash=? ORDER BY client", (h,))]
        return first + [c for c in others if c not in first]

    @_locked
    def example(self, h: str) -> sqlite3.Row | None:
        """The stored example with hash `h` (purged or not), or None."""
        return self.db.execute("SELECT * FROM examples WHERE hash=?", (h,)).fetchone()

    @_locked
    def prompt_status(self, task: str, prompt: str) -> tuple[int, bool]:
        """For one prompt of one task (`prompt`, its `prompt_key`), over every stored variant, purged ones included: how
        often it was seen (`count_prompts`) and whether a variant is in the train split. What a golden-only label checks
        before it holds a prompt out for good, inside the write transaction that stores it (`feedback.store_label`): a
        prompt that may already have been trained on cannot be held-out evidence (ADR 0091). The candidates are the rows
        whose hash or recorded prompt key is `prompt` and the kept rows with stored conditions, whose key needs their
        messages."""
        rows = self.db.execute(
            "SELECT hash, task, CASE WHEN params IS NOT NULL THEN messages END messages, params, prompt_hash, seen, split"
            " FROM examples WHERE task=? AND (hash=? OR prompt_hash=? OR (params IS NOT NULL AND prompt_hash IS NULL))",
            (task, prompt, prompt),
        ).fetchall()
        mine = [r for r in rows if prompt_key(r) == prompt]
        return count_prompts(mine)[prompt], any(r["split"] == "train" for r in mine)

    @_locked
    def example_count(self, task: str) -> int:
        """Stored examples of one task (what deleting the task keeps, for provenance)."""
        return int(self.db.execute("SELECT COUNT(*) FROM examples WHERE task=?", (task,)).fetchone()[0])

    @_locked
    def bill_of_materials(self) -> dict[str, Any]:
        """Model bill of materials: which teachers and licenses produced the training data. Only examples that still
        have their text count in `sources` and the fingerprint; purged ones (FR-13.3, FR-13.4) are counted in
        `purged_examples`, so a purge changes the fingerprint. Labels from a provenance source that is not a teacher
        (`licenses.SOURCES`: `human`) are listed apart, in `label_sources`, and count in the fingerprint like the rest
        (FR-3.54). Each adapter's own signed BOM is never touched (INV-14)."""
        grouped = self.db.execute(
            "SELECT teacher_model, license, license_url, split, COUNT(*) n FROM examples WHERE purged_ts IS NULL"
            " GROUP BY teacher_model, license, license_url, split"
        ).fetchall()
        rows = [r for r in grouped if r["teacher_model"] not in licenses.SOURCES]
        labels = [r for r in grouped if r["teacher_model"] in licenses.SOURCES]
        digest = hashlib.sha256()
        for r in self.db.execute("SELECT hash FROM examples WHERE purged_ts IS NULL ORDER BY hash"):
            digest.update(r["hash"].encode())
        purged = int(self.db.execute("SELECT COUNT(*) FROM examples WHERE purged_ts IS NOT NULL").fetchone()[0])
        return {
            "generated_at": time.time(),
            "examples_sha256": digest.hexdigest(),
            "sources": [dict(r) for r in rows],
            "label_sources": [dict(r) for r in labels],
            "purged_examples": purged,
        }

    @_locked
    def privacy_stats(self) -> dict[str, int]:
        """Examples that still have their text, and how many carry redaction placeholders."""
        r = self.db.execute(
            "SELECT COUNT(*) n, SUM(redactions > 0) redacted, COALESCE(SUM(redactions), 0) placeholders FROM examples"
            " WHERE purged_ts IS NULL"
        ).fetchone()
        return {"examples": int(r["n"]), "redacted_examples": int(r["redacted"] or 0), "placeholders": int(r["placeholders"])}

    # ---------- retention and erasure (FR-13.3, FR-13.4, ADR 0081) ----------
    @_locked
    def prompt_counts(self, task: str | None = None) -> Counter[str]:
        """`count_prompts` over every stored example (of `task`), purged ones included, reading the messages only of
        rows with stored conditions (the only ones whose key needs them)."""
        q = "SELECT hash, task, CASE WHEN params IS NOT NULL THEN messages END messages, params, prompt_hash, seen FROM examples"
        args: list[Any] = []
        if task:
            q += " WHERE task=?"
            args.append(task)
        return count_prompts(self.db.execute(q, args))

    @_locked
    def erasure_candidates(
        self,
        *,
        client: str | None = None,
        end_users: Collection[str] | None = None,
        hashes: Collection[str] | None = None,
        contains: str | None = None,
    ) -> list[sqlite3.Row]:
        """The examples an erasure scope selects, oldest first: every selector given narrows it. `client` is an API key
        name (the first sender or any later one, `example_clients`), `end_users` HMACs (`examples.end_user`), `hashes`
        example hashes, `contains` text matched with LIKE (case-insensitive for ASCII) in the stored, already-redacted
        messages, answer and conditions. Rows an erasure already purged are left out; rows retention purged are in (no
        text left to match `contains`), so the erasure makes their tombstone final."""
        q = "SELECT * FROM examples WHERE (purge_reason IS NULL OR purge_reason != 'erasure')"
        args: list[Any] = []
        if client is not None:
            q += " AND (client = ? OR hash IN (SELECT hash FROM example_clients WHERE client = ?))"
            args += [client, client]
        if end_users is not None:
            users = sorted(set(end_users))
            q += f" AND end_user IN ({','.join('?' * len(users))})" if users else " AND 0"
            args += users
        if contains is not None:
            raw, encoded = _like(contains), _like(json.dumps(contains, ensure_ascii=False)[1:-1])
            q += r" AND (messages LIKE ? ESCAPE '\' OR completion LIKE ? ESCAPE '\' OR params LIKE ? ESCAPE '\')"
            args += [encoded, raw, encoded]
        if hashes is None:
            return list(self.db.execute(q + " ORDER BY id", args))
        out: list[sqlite3.Row] = []
        wanted = sorted(set(hashes))
        for part in _chunks(wanted):
            out += self.db.execute(q + f" AND hash IN ({','.join('?' * len(part))})", [*args, *part]).fetchall()
        return sorted(out, key=lambda r: r["id"])

    @_locked
    def retention_candidates(self, cutoff: float, client: str | None = None, limit: int | None = None) -> list[sqlite3.Row]:
        """Examples that still have their text and were last stored or seen before `cutoff` (golden ones too), oldest
        first; with `client`, only those that API client sent (first or later); with `limit`, at most that many."""
        cutoff = _check_ts(cutoff, "cutoff")
        q = "SELECT * FROM examples WHERE purged_ts IS NULL AND (last_seen_ts < ? OR (last_seen_ts IS NULL AND ts < ?))"
        args: list[Any] = [cutoff, cutoff]
        if client is not None:
            q += " AND (client = ? OR hash IN (SELECT hash FROM example_clients WHERE client = ?))"
            args += [client, client]
        q += " ORDER BY id"
        if limit is not None:
            q += " LIMIT ?"
            args.append(int(limit))
        return list(self.db.execute(q, args))

    @_locked
    def purge_examples(self, rows: Mapping[str, bool], reason: str, now: float | None = None) -> int:
        """Remove the text of the examples `rows` names ({hash: whether it counts as a training row now,
        `dataset.counts_as_train`}), in one transaction. Each row stays as a tombstone: hash, task, split, seen, teacher,
        license and client stay; messages, completion and params become NULL; `purged_ts`, `purge_reason` and
        `purged_train` are set, and a row with conditions keeps its prompt key (`prompt_hash`) so its `seen` still
        counts toward its request's frequency (INV-3). Retention (`retention`) leaves the end user's hash, so a later
        erasure can still find the row; an erasure (`erasure`, through `record_erasure`) removes it. Returns how many
        rows were purged: a row already purged is skipped, except that an erasure makes a retention tombstone final."""
        if reason not in PURGE_REASONS:
            raise ValueError(f"purge reason must be one of {', '.join(PURGE_REASONS)}, not {reason!r}")
        self._begin()
        try:
            done = self._purge(rows, reason, time.time() if now is None else _check_ts(now, "now"))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return len(done)

    def _purge(self, rows: Mapping[str, bool], reason: str, now: float) -> list[sqlite3.Row]:
        """`purge_examples` inside the caller's transaction; returns the rows purged (hash, task, split)."""
        done: list[sqlite3.Row] = []
        final = reason == "erasure"
        for part in _chunks(sorted(rows)):
            found = self.db.execute(
                "SELECT hash, task, split, messages, params, prompt_hash, purge_reason FROM examples"
                f" WHERE hash IN ({','.join('?' * len(part))})",
                part,
            ).fetchall()
            for r in found:
                if r["purge_reason"] == "erasure" or (r["purge_reason"] is not None and not final):
                    continue
                if r["purge_reason"] is None:
                    key = prompt_key(r) if r["params"] else None  # without conditions the key is the hash itself
                    self.db.execute(
                        "UPDATE examples SET messages=NULL, completion=NULL, params=NULL, purged_ts=?, purge_reason=?,"
                        " purged_train=?, prompt_hash=COALESCE(prompt_hash, ?),"
                        " end_user=CASE WHEN ? THEN NULL ELSE end_user END WHERE hash=?",
                        (now, reason, int(bool(rows[r["hash"]])), key, int(final), r["hash"]),
                    )
                else:  # an erasure of a row retention purged: its text is gone already; it now stays gone
                    self.db.execute("UPDATE examples SET purge_reason='erasure', end_user=NULL WHERE hash=?", (r["hash"],))
                done.append(r)
        return done

    @_locked
    def affected_versions(self, rows: Iterable[tuple[str, float, bool]]) -> list[dict[str, Any]]:
        """The versions that may have been trained on these examples, each `(hash, ts, counted as a training row)`, with
        how many of them: reported, never edited. A version whose training set is committed in `version_leaves` (FR-16.4)
        is checked by membership; any other counts an example it was created after, when the example counted as a
        training row. That approximation errs toward reporting: it includes a version whose dataset was built just
        before the example arrived, and one trained while the example was still golden."""
        rows = list(rows)
        if not rows:
            return []
        stamps = sorted(float(ts) for _, ts, counted in rows if counted and ts is not None)
        committed = {int(r[0]) for r in self.db.execute("SELECT DISTINCT version_id FROM version_leaves WHERE split='train'")}
        hashes = sorted({h for h, _, _ in rows})
        out = []
        for v in self.db.execute("SELECT id, created_ts, state, adapter_uri FROM versions ORDER BY id").fetchall():
            if v["id"] in committed:
                n = 0
                for part in _chunks(hashes):
                    n += int(
                        self.db.execute(
                            "SELECT COUNT(*) FROM version_leaves WHERE version_id=? AND split='train'"
                            f" AND key IN ({','.join('?' * len(part))})",
                            [v["id"], *part],
                        ).fetchone()[0]
                    )
            else:
                n = bisect.bisect_left(stamps, float(v["created_ts"])) if v["created_ts"] is not None else 0
            if n:
                out.append({"id": int(v["id"]), "state": v["state"], "adapter_uri": v["adapter_uri"], "examples": n})
        return out

    @_locked
    def record_erasure(
        self,
        *,
        actor: str,
        reason: str,
        scope: Mapping[str, Any],
        purge: Mapping[str, bool],
        unlink: Iterable[tuple[str, str, str | None]] = (),
        unlink_end_users: Collection[str] = (),
        affected: Iterable[int] = (),
        now: float | None = None,
    ) -> dict[str, int]:
        """Carry out an erasure in one transaction (FR-13.3): purge `purge` ({hash: counted as a training row}) for good,
        tombstone each hash (`add_example` refuses it from now on), list the purged rows in `erasure_items` (hashes, task
        and split, never text), unlink the erased party from the shared examples kept (`unlink`: (hash, 'client' or
        'end_user', the client's name)), clear the erased end users' hashes from their request rows
        (`unlink_end_users`), and write the `erasures` row with the versions that may hold the data (`tainted`;
        `status` 'pending' when there are some, else 'complete'). `actor`, `reason` and `scope` arrive checked and free
        of request text. Returns the erasure's id and the counts. Signed BOMs are never touched (INV-14)."""
        now = time.time() if now is None else _check_ts(now, "now")
        tainted = sorted({int(v) for v in affected})
        self._begin()
        try:
            cur = self.db.execute(
                "INSERT INTO erasures (ts, day, key, tainted, status, actor, reason, scope, purged, kept)"
                " VALUES (?,?,NULL,?,?,?,?,?,0,0)",
                (
                    now,
                    self.day,
                    json.dumps(tainted),
                    "pending" if tainted else "complete",
                    actor,
                    reason,
                    json.dumps(dict(scope), ensure_ascii=False, sort_keys=True),
                ),
            )
            eid = int(cur.lastrowid)
            done = self._purge(purge, "erasure", now)
            self.db.executemany(
                "INSERT OR IGNORE INTO erasure_items (erasure_id, hash, task, split) VALUES (?,?,?,?)",
                [(eid, r["hash"], r["task"], r["split"]) for r in done],
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO tombstones (key, erasure_id, ts) VALUES (?,?,?)", [(r["hash"], eid, now) for r in done]
            )
            kept = self._unlink(unlink)
            unlinked = 0
            for part in _chunks(sorted(set(unlink_end_users)), SQL_CHUNK // 2):  # each value is bound twice
                marks = ",".join("?" * len(part))
                # A row that named the end user under either identifier it carried loses both hashes.
                unlinked += self.db.execute(
                    "UPDATE requests SET end_user=NULL, end_user_secondary=NULL"
                    f" WHERE end_user IN ({marks}) OR end_user_secondary IN ({marks})",
                    part + part,
                ).rowcount
            self.db.execute("UPDATE erasures SET purged=?, kept=? WHERE id=?", (len(done), kept, eid))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"id": eid, "purged": len(done), "kept": kept, "requests_unlinked": unlinked}

    def _unlink(self, entries: Iterable[tuple[str, str, str | None]]) -> int:
        """Remove an erased party from shared examples that stay (RATCHET_ERASURE_SHARED=keep): an erased client leaves
        `example_clients`, or, when it was the first sender, the next client in name order takes its place (without an
        end user, which belonged to the erased client); an erased end user's hash is cleared. Returns the rows changed."""
        changed = 0
        for h, party, name in entries:
            if party == "end_user":
                cur = self.db.execute("UPDATE examples SET end_user=NULL WHERE hash=? AND end_user IS NOT NULL", (h,))
                changed += cur.rowcount
                continue
            if party != "client" or name is None:
                raise ValueError(f"unlink takes ('client', name) or ('end_user', ...), not {(party, name)!r}")
            row = self.db.execute("SELECT client FROM examples WHERE hash=?", (h,)).fetchone()
            if row is not None and row["client"] == name:
                other = self.db.execute(
                    "SELECT MIN(client) FROM example_clients WHERE hash=? AND client != ?", (h, name)
                ).fetchone()[0]
                if other is None:
                    continue  # not shared after all: nothing to keep it for (the caller purges such rows)
                self.db.execute("UPDATE examples SET client=?, end_user=NULL WHERE hash=?", (other, h))
                self.db.execute("DELETE FROM example_clients WHERE hash=? AND client=?", (h, other))
                changed += 1
            changed += self.db.execute("DELETE FROM example_clients WHERE hash=? AND client=?", (h, name)).rowcount
        return changed

    @_locked
    def requests_of_end_users(self, hashes: Collection[str]) -> int:
        """Request rows that name one of these end-user hashes, as the attributed end user or the second identifier (what
        erasing those end users unlinks)."""
        n = 0
        for part in _chunks(sorted(set(hashes)), SQL_CHUNK // 2):  # each value is bound twice
            marks = ",".join("?" * len(part))
            q = f"SELECT COUNT(*) FROM requests WHERE end_user IN ({marks}) OR end_user_secondary IN ({marks})"
            n += int(self.db.execute(q, part + part).fetchone()[0])
        return n

    @_locked
    def end_user_scopes(self) -> list[str | None]:
        """Every scope an end-user hash is stored in (the API key names of examples and request rows that name an end
        user, configured now or not) and the keyless scope (None), keyless first. Erasing an end user for every client
        hashes it in each of these, so a renamed or removed key's rows are reached too, whatever keys the caller has."""
        rows = self.db.execute(
            "SELECT DISTINCT client FROM examples WHERE end_user IS NOT NULL AND client IS NOT NULL"
            " UNION SELECT DISTINCT client FROM requests WHERE end_user IS NOT NULL AND client IS NOT NULL"
        ).fetchall()
        return [None, *sorted(str(r[0]) for r in rows)]

    @_locked
    def train_rows_kept(self, task: str | None = None) -> int:
        """The most training rows any version had that are still stored: for each version, its `train_examples` less its
        training rows purged since (examples that counted as training rows when purged, stored before it was created and
        purged after it), and the largest of those. `run_night` counts new data from it, so deletions never hide new
        data and never make old data look new (FR-4.4). Each version is measured against its own purges: measuring every
        version against the largest one's would add the same purged rows back every night once a later version trained
        on fewer rows. Without purges it is the largest `train_examples`, v0.4's count. `purged_train` errs toward
        counting a row (`dataset.counts_as_train`), so a purged row that was not in a version's set can raise the next
        night's count once, by one; the version trained that night resets it."""
        versions = self.db.execute("SELECT train_examples, created_ts FROM versions ORDER BY train_examples DESC, id DESC")
        q = "SELECT COUNT(*) FROM examples WHERE purged_train = 1 AND ts <= ? AND purged_ts > ?"
        if task:
            q += " AND task=?"
        best = 0
        for v in versions.fetchall():
            trained = int(v["train_examples"] or 0)
            if trained <= best:
                break  # sorted by size: no later version can keep more than it trained on
            if v["created_ts"] is None:
                best = trained
                continue
            args: list[Any] = [v["created_ts"], v["created_ts"], *([task] if task else [])]
            best = max(best, trained - int(self.db.execute(q, args).fetchone()[0]))
        return best

    def fold_requests(
        self,
        cutoff: float,
        client: str | None = None,
        *,
        dry_run: bool = False,
        now: float | None = None,
        chunk: int = FOLD_CHUNK,
    ) -> int:
        """Fold request rows logged before `cutoff` (of one API `client`, when given) into `daily_rollup`, then delete
        them (FR-13.4): per day, task, client, route and opt-out, the sums every stats reader needs, and no end user.
        Rows newer than the `stage_since` of a version in shadow or canary, and the rows such a version served or
        shadowed in earlier stages, are never folded, so a release in flight keeps its evidence (`_fold_where`). `chunk`
        rows per transaction, each under the ledger's lock, so requests keep flowing during a large fold. Returns the rows
        folded (with `dry_run`, the rows that would be, writing nothing)."""
        cutoff = _check_ts(cutoff, "cutoff")
        stamp = time.time() if now is None else _check_ts(now, "now")
        if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk < 1:
            raise ValueError(f"chunk must be a whole number of rows, 1 or more, not {chunk!r}")
        total = 0
        while True:
            with self._lock:
                self._begin()
                try:
                    where, args = self._fold_where(cutoff, client)
                    if dry_run:
                        n = int(self.db.execute(f"SELECT COUNT(*) FROM requests WHERE {where}", args).fetchone()[0])
                        self.db.rollback()
                        return n
                    hi = self.db.execute(
                        f"SELECT MAX(id) FROM (SELECT id FROM requests WHERE {where} ORDER BY id LIMIT ?)", [*args, chunk]
                    ).fetchone()[0]
                    if hi is None:
                        self.db.commit()
                        return total
                    origin = self._t0 if self._t0 is not None else self.db.execute("SELECT MIN(ts) FROM requests").fetchone()[0]
                    if origin is not None:  # day numbers must survive the rows that defined day 1
                        self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (DAY_ORIGIN_META, repr(origin)))
                    self.db.execute(ROLLUP_INSERT.format(where=f"{where} AND id <= ?"), [stamp, *args, hi])
                    n = self.db.execute(f"DELETE FROM requests WHERE {where} AND id <= ?", [*args, hi]).rowcount
                    self.db.commit()
                except BaseException:
                    self.db.rollback()
                    raise
            total += n

    def _fold_where(self, cutoff: float, client: str | None) -> tuple[str, list[Any]]:
        """The rows a fold may take: older than `cutoff`, never newer than the `stage_since` of a version in shadow or
        canary, and never a row such a version served or shadowed, in any stage of its life: the anytime-valid canary
        rule and a version's `evidence` count every audit since it began (`audit_counts(vid)`, ADR 0025)."""
        where, args = "ts < ?", [cutoff]
        inflight = [int(r[0]) for r in self.db.execute("SELECT id FROM versions WHERE state IN ('shadow', 'canary')")]
        protect = self.db.execute("SELECT MIN(stage_since) FROM versions WHERE state IN ('shadow', 'canary')").fetchone()[0]
        if protect is not None:
            where += " AND id <= ?"
            args.append(int(protect))
        if inflight:  # version ids start at 1, so 0 stands for a row without one
            marks = ",".join("?" for _ in inflight)
            where += f" AND COALESCE(student_version, 0) NOT IN ({marks}) AND COALESCE(shadow_version, 0) NOT IN ({marks})"
            args.extend(inflight * 2)
        if client is not None:
            where += " AND client = ?"
            args.append(client)
        return where, args

    def _has_rollup(self) -> bool:
        return self.db.execute("SELECT 1 FROM daily_rollup LIMIT 1").fetchone() is not None

    def _rollup_by(self, key: str, where: str = "", args: Iterable[Any] = ()) -> dict[Any, dict[str, Any]]:
        """Folded request rows summed per `key` (a daily_rollup column), with each route's count in `route:<name>` and
        each opt-out's in `opt_out:<name>`."""
        sums = ", ".join(f"SUM({c}) {c}" for c in ROLLUP_SUMS)
        out: dict[Any, dict[str, Any]] = {}
        for r in self.db.execute(f"SELECT {key} k, {sums} FROM daily_rollup {where} GROUP BY {key}", list(args)):
            out[r["k"]] = {c: r[c] for c in ROLLUP_SUMS}
        per_route = f"SELECT {key} k, route, store_opt_out, SUM(requests) n FROM daily_rollup {where}"
        for r in self.db.execute(per_route + f" GROUP BY {key}, route, store_opt_out", list(args)):
            entry = out[r["k"]]
            route = f"route:{r['route'] or UNRECORDED_ROUTE}"
            entry[route] = entry.get(route, 0) + int(r["n"] or 0)
            if r["store_opt_out"] is not None:
                reason = f"opt_out:{r['store_opt_out']}"
                entry[reason] = entry.get(reason, 0) + int(r["n"] or 0)
        return out

    @_locked
    def retention_stats(self) -> dict[str, Any]:
        """Counts for /ratchet/stats (public, so never a hash): examples purged (by reason), erasures recorded, tombstoned
        examples offered again and refused, and request rows folded into daily_rollup."""
        by_reason = {reason: 0 for reason in PURGE_REASONS}
        q = "SELECT purge_reason, COUNT(*) n FROM examples WHERE purged_ts IS NOT NULL GROUP BY purge_reason"
        for r in self.db.execute(q):
            reason = r["purge_reason"] or "retention"
            by_reason[reason] = by_reason.get(reason, 0) + int(r["n"])
        return {
            "purged_examples": sum(by_reason.values()),
            "purged_by_reason": by_reason,
            "erasures": int(self.db.execute("SELECT COUNT(*) FROM erasures").fetchone()[0]),
            "erasure_refusals": int(self.db.execute("SELECT COALESCE(SUM(suppressed), 0) FROM tombstones").fetchone()[0]),
            "rolled_up_requests": int(self.db.execute("SELECT COALESCE(SUM(requests), 0) FROM daily_rollup").fetchone()[0]),
        }

    @_locked
    def export_page(
        self,
        kind: str,
        after: int = 0,
        limit: int = 500,
        *,
        task: str | None = None,
        split: str | None = None,
        since: float | None = None,
        client: str | None = None,
    ) -> list[dict[str, Any]]:
        """Up to `limit` rows of one table with an id above `after`, in id order, as plain dicts for an export (FR-13.5).
        `kind` is one of EXPORT_KINDS; the filters apply where EXPORT_FILTERS allows (ValueError otherwise). Examples are
        those that still have their text, with their provenance and every client that sent them. End-user hashes never
        leave the ledger this way: request and example rows lose them, an erasure's scope keeps only their count."""
        if kind not in EXPORT_KINDS:
            raise ValueError(f"export kind must be one of {', '.join(EXPORT_KINDS)}, not {kind!r}")
        given = {k for k, v in (("task", task), ("split", split), ("since", since), ("client", client)) if v is not None}
        if given - EXPORT_FILTERS[kind]:
            raise ValueError(f"{kind} take only {', '.join(sorted(EXPORT_FILTERS[kind])) or 'no filters'}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit must be a whole number of rows, 1 or more, not {limit!r}")
        ts_col = "created_ts" if kind == "versions" else "ts"
        q, args = f"SELECT * FROM {kind} WHERE id > ?", [int(after)]
        if kind == "examples":
            q += " AND purged_ts IS NULL"
        if task is not None:
            q += " AND task = ?"
            args.append(task)
        if split is not None:
            q += " AND split = ?"
            args.append(split)
        if since is not None:
            q += f" AND {ts_col} >= ?"
            args.append(_check_ts(since, "since"))
        if client is not None and kind == "examples":
            q += " AND (client = ? OR hash IN (SELECT hash FROM example_clients WHERE client = ?))"
            args += [client, client]
        elif client is not None:
            q += " AND client = ?"
            args.append(client)
        rows = self.db.execute(q + " ORDER BY id LIMIT ?", [*args, limit]).fetchall()
        return getattr(self, f"_export_{kind}")(rows)

    def _export_examples(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        others: dict[str, list[str]] = {}
        for part in _chunks([r["hash"] for r in rows]):
            for c in self.db.execute(
                f"SELECT hash, client FROM example_clients WHERE hash IN ({','.join('?' * len(part))}) ORDER BY client", part
            ):
                others.setdefault(c["hash"], []).append(c["client"])
        out = []
        for r in rows:
            first = [r["client"]] if r["client"] is not None else []
            out.append(
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "day": r["day"],
                    "task": r["task"],
                    "hash": r["hash"],
                    "split": r["split"],
                    "seen": seen_count(r),
                    "messages": json.loads(r["messages"]),
                    "completion": r["completion"],
                    "params": json.loads(r["params"]) if r["params"] else None,
                    "provenance": {
                        "teacher": r["teacher_model"],
                        "license": r["license"],
                        "license_url": r["license_url"],
                        "hash": r["hash"],
                        "redactions": int(r["redactions"] or 0),
                        "origin": r["origin"] or "live",
                    },
                    "client": r["client"],
                    "clients": first + [c for c in others.get(r["hash"], []) if c not in first],
                    "last_seen_ts": r["last_seen_ts"],
                    "verified": bool(r["verified"]),  # a person wrote the label (ADR 0091)
                    "golden_only": bool(r["golden_only"]),
                }
            )
        return out

    def _export_requests(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [{k: r[k] for k in r.keys() if k not in ("end_user", "end_user_secondary")} for r in rows]

    def _export_versions(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            row = dict(r)
            row["eval"] = json.loads(r["eval"] or "{}")
            row["bom"] = json.loads(r["bom"]) if r["bom"] else None  # as stored: a signature covers it (INV-14)
            out.append(row)
        return out

    def _export_events(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(r) for r in rows]

    def _export_erasures(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            scope = json.loads(r["scope"]) if r["scope"] else {}
            if isinstance(scope.get("end_users"), list):
                scope["end_users"] = len(scope["end_users"])
            listed = self.db.execute("SELECT hash, task, split FROM erasure_items WHERE erasure_id=? ORDER BY hash", (r["id"],))
            items = [{"hash": i["hash"], "task": i["task"], "split": i["split"]} for i in listed]
            out.append(
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "day": r["day"],
                    "actor": r["actor"],
                    "reason": r["reason"],
                    "scope": scope,
                    "purged": r["purged"],
                    "kept": r["kept"],
                    "affected_versions": json.loads(r["tainted"]) if r["tainted"] else [],
                    "status": r["status"],
                    "replacement": r["replacement"],
                    "certificate": json.loads(r["certificate"]) if r["certificate"] else None,
                    "items": items,
                }
            )
        return out

    @_locked
    def opt_out_stats(self) -> dict[str, Any]:
        """Requests under a storage opt-out (`not_stored_requests`, and by reason, `privacy.OPT_OUTS`) and end users
        registered as no-train. Counts only, never a hash: /ratchet/stats is public (FR-13.1, FR-13.2)."""
        reasons = {
            row["store_opt_out"]: int(row["n"])
            for row in self.db.execute(
                "SELECT store_opt_out, COUNT(*) n FROM requests WHERE store_opt_out IS NOT NULL"
                " GROUP BY store_opt_out ORDER BY n DESC, store_opt_out"
            )
        }
        if self._has_rollup():  # folded request rows keep their opt-out (FR-13.4)
            for row in self.db.execute(
                "SELECT store_opt_out, SUM(requests) n FROM daily_rollup WHERE store_opt_out IS NOT NULL GROUP BY store_opt_out"
            ):
                reasons[row["store_opt_out"]] = reasons.get(row["store_opt_out"], 0) + int(row["n"] or 0)
            reasons = dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])))
        no_train = int(self.db.execute("SELECT COUNT(*) FROM no_train").fetchone()[0])
        return {
            "not_stored_requests": sum(reasons.values()),
            "not_stored_by_reason": reasons,
            "no_train_end_users": no_train,
        }

    # ---------- end users (FR-13.2, ADR 0080) ----------
    @_locked
    def no_train_add(
        self, entries: Iterable[tuple[str, str | None]], admin: str | None = None, limit: int | None = None
    ) -> list[bool]:
        """Register end users as no-train: `entries` are (hash, the API key name the hash belongs to). Returns, per
        entry, whether it was new. An admin's registration (`admin`, the admin's name) also marks an existing one as
        the admin's, so its client cannot remove it. With `limit`, raises `NoTrainFull` and writes nothing when the new
        entries would take the table past it (entries already there do not count). One transaction."""
        now = time.time()
        entries = list(entries)
        created = []
        self._begin()
        try:
            if limit is not None:
                total = int(self.db.execute("SELECT COUNT(*) FROM no_train").fetchone()[0])
                new = len({h for h, _ in entries if not self._no_train_row(h)})
                if total + new > limit:
                    raise NoTrainFull(limit)
            for h, client in entries:
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO no_train (end_user_hash, ts, client, admin) VALUES (?,?,?,?)", (h, now, client, admin)
                )
                created.append(cur.rowcount > 0)
                if cur.rowcount == 0 and admin is not None:
                    self.db.execute("UPDATE no_train SET admin=? WHERE end_user_hash=? AND admin IS NULL", (admin, h))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return created

    @_locked
    def no_train_remove(self, hashes: Iterable[str], admin: bool = False) -> list[str]:
        """Remove no-train registrations: any of `hashes` with `admin`, else only those a client registered. Returns,
        per hash, `removed`, `kept` (an admin registered it) or `absent`. One transaction."""
        out = []
        self._begin()
        try:
            for h in hashes:
                row = self._no_train_row(h)
                if row is None:
                    out.append("absent")
                elif row["admin"] is not None and not admin:
                    out.append("kept")
                else:
                    self.db.execute("DELETE FROM no_train WHERE end_user_hash=?", (h,))
                    out.append("removed")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return out

    def _no_train_row(self, h: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT admin FROM no_train WHERE end_user_hash=?", (h,)).fetchone()

    @_locked
    def is_no_train(self, *hashes: str | None) -> bool:
        """Whether any of `hashes` (one end user's, in the scopes a registration may use) is registered as no-train: one
        lookup on the primary key."""
        wanted = [h for h in hashes if h is not None]
        if not wanted:
            return False
        marks = ",".join("?" * len(wanted))
        return self.db.execute(f"SELECT 1 FROM no_train WHERE end_user_hash IN ({marks}) LIMIT 1", wanted).fetchone() is not None

    @_locked
    def no_train_count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM no_train").fetchone()[0])

    @_locked
    def no_train_scopes_stored(self) -> list[str | None]:
        """Every scope (`client`) a no-train registration is stored in, configured or not."""
        rows = self.db.execute("SELECT DISTINCT client FROM no_train").fetchall()
        return sorted((r["client"] for r in rows), key=lambda c: (c is not None, c or ""))

    @_locked
    def no_train_unmatched(self, scopes: Iterable[str | None]) -> dict[str | None, int]:
        """{scope: registrations} for the no-train registrations stored in a scope not among `scopes` (the ones requests
        can be hashed in now, `privacy.no_train_scopes`): they match no request."""
        live = set(scopes)
        rows = self.db.execute("SELECT client, COUNT(*) n FROM no_train GROUP BY client").fetchall()
        return {r["client"]: int(r["n"]) for r in rows if r["client"] not in live}

    # ---------- meta (small ledger-wide state) ----------
    @_locked
    def meta_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    @_locked
    def meta_set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    @_locked
    def meta_setdefault(self, key: str, value: str) -> str:
        """The stored value of `key`, storing `value` first when there is none. Atomic across processes sharing the
        file: the first writer wins and everyone reads its value."""
        self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"])

    # ---------- human feedback (FR-13.30, ADR 0091) ----------
    @_locked
    def feedback_request(self, request_id: str) -> sqlite3.Row | None:
        """The metadata a verdict needs of the request the gateway logged under `request_id`: who sent it, its task,
        version, how it was served, its storage opt-out, end user (both identifiers' hashes) and whether a teacher answer
        of it was stored. None when no row has that id (never logged, or folded by request retention)."""
        return self.db.execute(
            "SELECT id, client, task, served_by, route, student_version, store_opt_out, end_user, end_user_secondary, stored"
            " FROM requests WHERE request_id=? ORDER BY id LIMIT 1",
            (request_id,),
        ).fetchone()

    @_locked
    def feedback_row(self, request_id: str) -> sqlite3.Row | None:
        """The verdict recorded for a request, or None."""
        return self.db.execute("SELECT * FROM feedback WHERE request_id=?", (request_id,)).fetchone()

    @_locked
    def record_feedback(
        self,
        request_id: str,
        client: str | None,
        verdict: str,
        request: Mapping[str, Any],
        *,
        corrected: bool = False,
        stored_hash: str | None = None,
    ) -> bool:
        """Record a verdict on a request (`request`: its `feedback_request` row), one row per request: a later verdict
        replaces the earlier one, `corrected_present` and `stored` only ever go from 0 to 1, and the stored example's
        hash is kept from the first correction that was stored. Returns True when an earlier verdict was replaced."""
        now = time.time()
        updated = self.db.execute("SELECT 1 FROM feedback WHERE request_id=?", (request_id,)).fetchone() is not None
        self.db.execute(
            "INSERT INTO feedback (ts, day, request_id, client, verdict, corrected_present, task, version, served_by, route,"
            " stored, example_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(request_id) DO UPDATE SET ts=excluded.ts, day=excluded.day, verdict=excluded.verdict,"
            " corrected_present = MAX(corrected_present, excluded.corrected_present),"
            " stored = MAX(stored, excluded.stored), example_hash = COALESCE(example_hash, excluded.example_hash)",
            (
                now,
                self.day,
                request_id,
                client,
                verdict,
                int(corrected),
                request["task"],
                request["student_version"],
                request["served_by"],
                request["route"],
                int(stored_hash is not None),
                stored_hash,
            ),
        )
        self.db.commit()
        return updated

    @_locked
    def human_precision(self, version: int) -> tuple[int, int]:
        """(verdicts, correct ones) on the answers `version` served by itself (route `student`: not degraded stand-ins),
        the latest verdict per request."""
        r = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(verdict='correct'), 0) ok FROM feedback"
            " WHERE version=? AND served_by='student' AND route='student'",
            (version,),
        ).fetchone()
        return int(r["n"]), int(r["ok"])

    @_locked
    def feedback_stats(self) -> dict[str, Any]:
        """Counts for /ratchet/stats: verdicts by kind, corrections sent and stored, the human-labelled and golden-only
        examples that still have their text, and per version the human precision of the answers it served itself, with
        its 95% Wilson bounds. Never a request id or text: the stats are public."""
        r = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(verdict='correct'), 0) ok, COALESCE(SUM(corrected_present), 0) sent,"
            " COALESCE(SUM(stored), 0) stored FROM feedback"
        ).fetchone()
        e = self.db.execute(
            "SELECT COALESCE(SUM(verified = 1), 0) human, COALESCE(SUM(golden_only = 1), 0) golden_only FROM examples"
            " WHERE purged_ts IS NULL"
        ).fetchone()
        by_version = []
        for v in self.db.execute(
            "SELECT version, COUNT(*) n, COALESCE(SUM(verdict='correct'), 0) ok FROM feedback"
            " WHERE version IS NOT NULL AND served_by='student' AND route='student' GROUP BY version ORDER BY version"
        ):
            n, ok = int(v["n"]), int(v["ok"])
            lo, hi = wilson(ok, n)
            by_version.append(
                {"version": v["version"], "verdicts": n, "correct": ok, "precision": ok / n, "lower": lo, "upper": hi}
            )
        return {
            "verdicts": int(r["n"]),
            "correct": int(r["ok"]),
            "incorrect": int(r["n"]) - int(r["ok"]),
            "corrections_sent": int(r["sent"]),
            "corrections_stored": int(r["stored"]),
            "human_examples": int(e["human"]),
            "golden_only_examples": int(e["golden_only"]),
            "by_version": by_version,
        }

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

    # ---------- committed training sets (FR-16.4, ADR 0027) ----------
    @_locked
    def set_version_leaves(self, vid: int, split: str, pairs: Iterable[tuple[str, str]]) -> int:
        """Write version `vid`'s committed leaves for one split ('train' or 'golden'): (key hex, value hex) pairs, each
        64 lowercase hex characters, keys unique. Leaves are written once (INV-17): ValueError when the split already
        has leaves, the version does not exist or a pair is malformed, and nothing is written then. One transaction, so a
        reader sees all of a split's leaves or none. Returns how many were written (an empty split writes nothing; its
        BOM commitment says size 0)."""
        if split not in LEAF_SPLITS:
            raise ValueError(f"split must be one of {', '.join(LEAF_SPLITS)}, not {split!r}")
        rows = [(vid, split, _hex32(k, "key"), _hex32(v, "value")) for k, v in pairs]
        keys = [r[2] for r in rows]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate keys in the {split} leaves of version {vid}")
        self._begin()
        try:
            if self.db.execute("SELECT 1 FROM versions WHERE id=?", (vid,)).fetchone() is None:
                raise ValueError(f"no version {vid}")
            if self.db.execute("SELECT 1 FROM version_leaves WHERE version_id=? AND split=? LIMIT 1", (vid, split)).fetchone():
                raise ValueError(f"version {vid} already has {split} leaves; committed leaves are written once (INV-17)")
            self.db.executemany("INSERT INTO version_leaves (version_id, split, key, value) VALUES (?,?,?,?)", rows)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return len(rows)

    @_locked
    def version_leaves(self, vid: int, split: str) -> list[tuple[str, str]]:
        """Version `vid`'s committed (key hex, value hex) leaves of one split, in key order; empty when it has none (an
        empty split, or a version trained before v0.5: its BOM says which)."""
        q = "SELECT key, value FROM version_leaves WHERE version_id=? AND split=? ORDER BY key"
        return [(r["key"], r["value"]) for r in self.db.execute(q, (vid, split))]

    @_locked
    def versions_containing(self, key: str, split: str = "train") -> list[int]:
        """The ids of the versions whose committed `split` set holds the example `key` (hex), oldest first: the adapters
        trained on it (split 'train'), on the `version_leaves_key` index."""
        q = "SELECT version_id FROM version_leaves WHERE key=? AND split=? ORDER BY version_id"
        return [int(r["version_id"]) for r in self.db.execute(q, (key.lower(), split))]

    @_locked
    def leaf_example(self, vid: int, split: str) -> sqlite3.Row | None:
        """The stored example behind the first of version `vid`'s `split` leaves (in key order) that still has its text,
        or None: the mock demo's sample of a committed example (prompt only goes out). A purged or erased example keeps
        its row with no text (FR-13.3, FR-13.4), and its leaf stays (INV-17), so it is skipped here."""
        q = (
            "SELECT e.task, e.messages, e.params, e.hash FROM version_leaves l JOIN examples e ON e.hash = l.key"
            " WHERE l.version_id=? AND l.split=? AND e.purged_ts IS NULL AND e.messages IS NOT NULL ORDER BY l.key LIMIT 1"
        )
        return self.db.execute(q, (vid, split)).fetchone()

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

    # ---------- savings receipts (FR-15.5, ADR 0029) ----------
    def period_rows(self, period: int, client: str | None = None) -> list[dict[str, Any]]:
        """The requests of ledger day `period` (only `client`'s when given), in id order, with `RECEIPT_COLUMNS`: what a
        receipt's leaves are built from, on the `requests_day` index. A busy day is read in chunks of RECEIPT_CHUNK rows,
        each under the lock (`_period_chunk`), so `log_request` waits for one chunk at most; a day that has ended gains
        no rows, so the chunks add up to the day. Not `_locked` itself, like `replay_rows`."""
        rows: list[dict[str, Any]] = []
        after = 0
        while True:
            chunk = self._period_chunk(int(period), client, after)
            rows.extend(chunk)
            if len(chunk) < RECEIPT_CHUNK:
                return rows
            after = chunk[-1]["id"]

    @_locked
    def _period_chunk(self, period: int, client: str | None, after: int) -> list[dict[str, Any]]:
        """Up to RECEIPT_CHUNK of `period_rows`' rows with ids above `after`, in id order."""
        q, args = f"SELECT {RECEIPT_COLUMNS} FROM requests WHERE day=? AND id>?", [period, after]
        if client is not None:
            q += " AND client=?"
            args.append(client)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id LIMIT ?", [*args, RECEIPT_CHUNK])]

    @_locked
    def receipt_row(self, request_id: int) -> dict[str, Any] | None:
        """One request's `RECEIPT_COLUMNS` (the leaf a receipt proof shows), or None for an unknown id."""
        r = self.db.execute(f"SELECT {RECEIPT_COLUMNS} FROM requests WHERE id=?", (int(request_id),)).fetchone()
        return dict(r) if r else None

    @_locked
    def period_counts(self, period: int) -> dict[str | None, int]:
        """{client name (None: no key): requests} on ledger day `period`. A name that is empty is counted under None,
        because '' names the gateway-wide receipt."""
        out: dict[str | None, int] = {}
        for r in self.db.execute("SELECT client, COUNT(*) n FROM requests WHERE day=? GROUP BY client", (int(period),)):
            name = r["client"] or None
            out[name] = out.get(name, 0) + int(r["n"])
        return out

    @_locked
    def period_folded(self, period: int, client: str | None = None) -> bool:
        """Whether request retention folded any of ledger day `period`'s rows (only `client`'s when given) into
        `daily_rollup` (FR-13.4): the day's rows no longer rebuild a receipt, so it is never sealed from them, and a
        sealed receipt's per-request proofs and leaves are gone (its signature and totals still verify)."""
        q, args = "SELECT 1 FROM daily_rollup WHERE day=?", [int(period)]
        if client is not None:
            q += " AND client=?"
            args.append(client)
        return self.db.execute(q + " LIMIT 1", args).fetchone() is not None

    @_locked
    def period_spend(self, period: int) -> list[float]:
        """The amounts (USD) spent outside requests on ledger day `period` (the `spend` table: backfill labelling,
        FR-16.8), which a receipt nets from its billable savings."""
        return [r["usd"] for r in self.db.execute("SELECT usd FROM spend WHERE day=? ORDER BY id", (int(period),))]

    @_locked
    def unsealed_days(self, through: int) -> list[int]:
        """Ledger days up to `through` that have requests and come after the last day whose gateway-wide receipt is
        sealed, oldest first. A day's client receipts are sealed before its gateway-wide one (`receipts.seal_due`), so
        that receipt marks the whole day done."""
        last = self.db.execute("SELECT MAX(period) FROM receipts WHERE client=''").fetchone()[0]
        q, args = "SELECT DISTINCT day FROM requests WHERE day IS NOT NULL AND day<=?", [int(through)]
        if last is not None:
            q += " AND day>?"
            args.append(int(last))
        return [int(r["day"]) for r in self.db.execute(q + " ORDER BY day", args)]

    @_locked
    def receipt(self, period: int, client: str | None = None) -> dict[str, Any] | None:
        """The sealed receipt of ledger day `period` for `client` (None: the whole gateway), or None."""
        r = self.db.execute("SELECT doc FROM receipts WHERE period=? AND client=?", (int(period), client or "")).fetchone()
        return json.loads(r["doc"]) if r else None

    @_locked
    def last_receipt(self, client: str | None, before: int) -> dict[str, Any] | None:
        """`client`'s (None: the gateway's) latest sealed receipt for a day before `before`: the one a new receipt
        chains to."""
        r = self.db.execute(
            "SELECT doc FROM receipts WHERE client=? AND period<? ORDER BY period DESC LIMIT 1", (client or "", int(before))
        ).fetchone()
        return json.loads(r["doc"]) if r else None

    @_locked
    def add_receipt(self, period: int, client: str | None, doc: Mapping[str, Any]) -> None:
        """Store a sealed receipt. Receipts are written once (INV-17): `ReceiptExists` when (period, client) already has
        one, also when another process sealed it first (the check and the insert are one transaction, `_begin`)."""
        text = json.dumps(doc, ensure_ascii=False, allow_nan=False)
        self._begin()
        try:
            if self.db.execute("SELECT 1 FROM receipts WHERE period=? AND client=?", (int(period), client or "")).fetchone():
                raise ReceiptExists(f"the receipt of day {period} for {client or 'the gateway'} is sealed already")
            self.db.execute(
                "INSERT INTO receipts (period, client, created_ts, doc) VALUES (?,?,?,?)",
                (int(period), client or "", time.time(), text),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @_locked
    def receipt_periods(self) -> list[tuple[int, str | None]]:
        """Every sealed receipt as (period, client), client None for the gateway's, by period then client."""
        rows = self.db.execute("SELECT period, client FROM receipts ORDER BY period, client")
        return [(int(r["period"]), r["client"] or None) for r in rows]

    # ---------- events + stats ----------
    @_locked
    def event(self, kind: str, detail: str) -> None:
        self.db.execute("INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (time.time(), self.day, kind, detail))
        self.db.commit()

    @_locked
    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT id, ts, day, kind, detail FROM events ORDER BY id")]

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

    def replay_rows(
        self, vid: int, since_day: int | None = None, until_day: int | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """What the ledger replay reads for version `vid` (FR-16.5, ADR 0028), on ledger days `since_day` to `until_day`
        (both included; None: no bound): every request its student answered with the replay's columns written
        (confidence, label_p and threshold not NULL), in id order, and how many of its other rows were left out:
        `before_logging` (the student answered, but the row predates those columns) and `no_student_answer` (the call
        failed or its breaker was open: no confidence to replay). `teacher_usd` is what the teacher alone cost when it
        was called for the request (`baseline_cost_usd` of an escalation the teacher answered, or of an audit), else
        NULL; `estimate_usd` is the token formula's estimate (`baseline_est_usd`). One version only: confidence scales
        differ between versions, so the replay never pools them.

        A version's history can be long, so it is read in chunks of REPLAY_CHUNK rows, each under the lock, up to the
        last request id when the read began (rows logged meanwhile are not read): the request path's `log_request` waits
        for one chunk at most, never the whole history. That is why this method is not `_locked` itself; every statement
        runs in a locked helper."""
        where, args = "student_version=?", [vid]
        if since_day is not None:
            where += " AND day>=?"
            args.append(since_day)
        if until_day is not None:
            where += " AND day<=?"
            args.append(until_day)
        last, excluded = self._replay_bounds(where, args)
        rows: list[dict[str, Any]] = []
        after = 0
        while True:
            chunk = self._replay_chunk(where, args, after, last)
            rows.extend(chunk)
            if len(chunk) < REPLAY_CHUNK:
                return rows, excluded
            after = chunk[-1]["id"]

    @_locked
    def _replay_bounds(self, where: str, args: list[Any]) -> tuple[int, dict[str, int]]:
        """`replay_rows`' last request id and its left-out counts up to that id, in one locked read."""
        last = int(self.db.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()[0])
        left = self.db.execute(
            f"""SELECT COALESCE(SUM(confidence IS NOT NULL AND (label_p IS NULL OR threshold IS NULL)), 0) before_logging,
                       COALESCE(SUM(confidence IS NULL), 0) no_student_answer
                FROM requests WHERE {where} AND id<=?""",
            [*args, last],
        ).fetchone()
        return last, {"before_logging": int(left["before_logging"]), "no_student_answer": int(left["no_student_answer"])}

    @_locked
    def _replay_chunk(self, where: str, args: list[Any], after: int, last: int) -> list[dict[str, Any]]:
        """Up to REPLAY_CHUNK of `replay_rows`' rows with ids in (after, last], in id order."""
        rows = self.db.execute(
            f"""SELECT id, day, task, confidence, threshold, label_p, student_agree, route, audited,
                       COALESCE(student_cost_usd, 0.0) student_usd,
                       CASE WHEN audited=1 OR (escalated=1 AND served_by='teacher') THEN baseline_cost_usd END teacher_usd,
                       COALESCE(baseline_est_usd, baseline_cost_usd, 0.0) estimate_usd
                FROM requests WHERE {where} AND id>? AND id<=?
                  AND confidence IS NOT NULL AND label_p IS NOT NULL AND threshold IS NOT NULL
                ORDER BY id LIMIT ?""",
            [*args, after, last, REPLAY_CHUNK],
        ).fetchall()
        return [dict(r) for r in rows]

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
        `fallback_teacher`: the fallback teacher; ADR 0019). Days whose request rows retention folded (FR-13.4) add
        their `daily_rollup` sums to the rows left; without folded rows every number is computed as before."""
        rows = [
            dict(r)
            for r in self.db.execute(
                """SELECT day, COUNT(*) requests,
                      SUM(served_by='student') student_served,
                      SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      AVG(latency_ms) latency_ms,
                      AVG(CASE WHEN audited=1 THEN audit_agree END) audit_agree,
                      SUM(route='degraded') degraded,
                      SUM(route='fallback_teacher') fallback_teacher,
                      SUM(latency_ms) latency_sum, COUNT(latency_ms) latency_n,
                      SUM(CASE WHEN audited=1 THEN audit_agree END) audit_sum,
                      COUNT(CASE WHEN audited=1 THEN audit_agree END) audit_n
               FROM requests GROUP BY day ORDER BY day"""
            )
        ]
        if self._has_rollup():
            rows = sorted(_merge(rows, "day", self._rollup_by("day")).values(), key=lambda r: r["day"])
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
        each route (`unrecorded` for rows older than the route column), most frequent first. Folded request rows
        (FR-13.4) count too."""
        rows = [
            dict(r)
            for r in self.db.execute(
                """SELECT task, COUNT(*) requests, SUM(served_by='student') student_served, SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline, AVG(latency_ms) latency_ms,
                      SUM(latency_ms) latency_sum, COUNT(latency_ms) latency_n
               FROM requests GROUP BY task ORDER BY COUNT(*) DESC"""
            )
        ]
        routes: dict[str, dict[str, int]] = {}
        counted = self.db.execute("SELECT task, route, COUNT(*) n FROM requests GROUP BY task, route ORDER BY n DESC, route")
        for r in counted:
            per_task = routes.setdefault(r["task"], {})
            key = r["route"] or UNRECORDED_ROUTE
            per_task[key] = per_task.get(key, 0) + int(r["n"])
        if self._has_rollup():
            folded = self._rollup_by("task")
            for name, sums in folded.items():
                per_task = routes.setdefault(name, {})
                for k, v in sums.items():
                    if k.startswith("route:"):
                        per_task[k[6:]] = per_task.get(k[6:], 0) + v
                routes[name] = dict(sorted(per_task.items(), key=lambda kv: (-kv[1], kv[0])))
            rows = sorted(_merge(rows, "task", folded).values(), key=lambda r: -r["requests"])  # stable: ties keep order
        # Human verdicts on the task's answers (FR-13.30), the latest per request; their rows outlive the requests'.
        human = {
            f["task"]: (int(f["n"]), int(f["ok"]))
            for f in self.db.execute(
                "SELECT task, COUNT(*) n, COALESCE(SUM(verdict='correct'), 0) ok FROM feedback GROUP BY task"
            )
        }
        out = []
        for r in rows:
            n = r["requests"] or 1
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            verdicts, correct = human.get(r["task"], (0, 0))
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
                    "human_feedback": verdicts,
                    "human_agreement": correct / verdicts if verdicts else None,
                }
            )
        return out

    @_locked
    def stats_by_client(self) -> list[dict[str, Any]]:
        """Per API key: requests, cost and savings (the basis for per-client receipts); `end_users`, the distinct end
        users its requests named (FR-13.2); and `stored_share`, the share of its requests that stored or bumped a
        training example (None when no request recorded it, as before v0.5). Folded request rows (FR-13.4) count in
        every figure but `end_users`: a fold keeps no end user, so that counts the rows still kept."""
        rows = [
            dict(r)
            for r in self.db.execute(
                """SELECT client, COUNT(*) requests, SUM(served_by='student') student_served,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      COUNT(DISTINCT end_user) end_users, AVG(stored) stored_share,
                      SUM(stored) stored_sum, COUNT(stored) stored_n
               FROM requests WHERE client IS NOT NULL GROUP BY client ORDER BY COUNT(*) DESC"""
            )
        ]
        if self._has_rollup():
            folded = self._rollup_by("client", "WHERE client IS NOT NULL")
            rows = sorted(_merge(rows, "client", folded).values(), key=lambda r: -r["requests"])
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
                    "end_users": int(r["end_users"] or 0),
                    "stored_share": None if r["stored_share"] is None else float(r["stored_share"]),
                }
            )
        return out

    @_locked
    def totals(self) -> dict[str, float]:
        """Requests, cost and savings over the whole ledger, folded request rows included (FR-13.4)."""
        r = dict(self.db.execute("SELECT COUNT(*) n, SUM(cost_usd) c, SUM(baseline_cost_usd) b FROM requests").fetchone())
        if self._has_rollup():
            f = self.db.execute("SELECT SUM(requests) n, SUM(cost) c, SUM(baseline) b FROM daily_rollup").fetchone()
            r = {"n": r["n"] + int(f["n"] or 0), "c": _add(r["c"], f["c"]), "b": _add(r["b"], f["b"])}
        c, b = r["c"] or 0.0, r["b"] or 0.0
        return {
            "requests": r["n"],
            "cost_usd": c,
            "baseline_usd": b,
            "saved_usd": b - c,
            "savings_pct": 0.0 if b == 0 else 1 - c / b,
        }
