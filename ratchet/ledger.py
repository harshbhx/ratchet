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
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from . import calibration, licenses, metrics, tasks
from .config import MAX_GPUS, MAX_USD_PER_GPU_HOUR
from .idem import SeenWindow
from .stats import wilson

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, task TEXT,
    served_by TEXT,            -- 'student' | 'teacher' | 'upstream' (an embeddings pass-through, route 'passthrough')
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
    uses_tools INTEGER,        -- 1: it offered tools or carried tool calls or results (FR-1.41); NULL before v0.5
    -- Evidence columns (ADR 0024), NULL until the feature that writes them lands, and in older rows:
    threshold REAL,            -- the confidence threshold the student was held to; NULL without a version (FR-16.5)
    label_p REAL,              -- probability that a teacher label of the student's answer was observed (FR-16.5)
    student_agree INTEGER,     -- 1/0: the student's answer agreed with the escalation's or a random audit's (FR-16.5)
    baseline_est_usd REAL,     -- the all-teacher cost by the token formula, also when the teacher answered (FR-16.5)
    optional_failed INTEGER,   -- 1: an optional teacher call of this request (audit, arbitration) failed
    -- Data governance (FR-13.1, FR-13.2, ADR 0080):
    end_user TEXT,             -- HMAC of the request's safety_identifier or user (privacy.EndUsers), never the value
    store_opt_out TEXT,        -- why its teacher answers could not be stored (privacy.OPT_OUTS); NULL: no opt-out
    -- The gateway's request id ('req_' + 32 hex, also in x-request-id; the completion id is 'chatcmpl-' + the same digits),
    -- which feedback names the request by (FR-13.30, ADR 0062); NULL for internal calls (Scout, the simulator) and older rows.
    request_id TEXT,
    -- HMAC of the second end-user identifier the request carried ('user' beside 'safety_identifier'), in the same scope as
    -- end_user, never the value: a correction must name it too, so a no-train registration made with it applies (FR-13.30)
    end_user_secondary TEXT,
    -- The client's own request id (X-Request-Id or X-Client-Request-Id, validated; ADR 0062); NULL for internal calls and
    -- older rows.
    client_request_id TEXT,
    retry_count INTEGER DEFAULT 0  -- the client SDK's attempt number (x-stainless-retry-count, ADR 0071); 0 when unsent
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
    golden_only INTEGER DEFAULT 0,    -- 1: held out for good, even past FREQUENT, with every variant of its prompt
    -- The example's key under the default redaction (`label` style, no detector plugin; ADR 0082) when it was stored
    -- otherwise and the key differs; NULL when it is `hash`. An erasure tombstones both, so a style or plugin change
    -- never lets an erased example be stored again.
    canon_hash TEXT
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
-- The admin API and CLI (ratchet/admin.py, ADR 0064): every manual change, the runtime switches and the task pins.
CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    actor TEXT,                -- 'api:<admin key name>' or 'cli:<OS user>'; never a key
    action TEXT,               -- admin.ACTIONS: rollback | retire | promote | set_flag | pin_task | unpin_task
    target TEXT,               -- 'v7', a flag name or a task name
    reason TEXT,               -- the operator's reason, 3 to 500 characters, redacted
    before TEXT, after TEXT,   -- JSON: the target's state before and after
    request_id TEXT            -- the gateway's request id of the admin call; NULL from the CLI
);
CREATE TABLE IF NOT EXISTS runtime_flags (  -- FLAG_NAMES; a missing row means the default
    name TEXT PRIMARY KEY, value TEXT, ts REAL, actor TEXT
);
CREATE TABLE IF NOT EXISTS task_overrides (  -- tasks an operator pinned to the teacher at runtime, on top of their spec
    task TEXT PRIMARY KEY, pinned INTEGER, ts REAL, actor TEXT, reason TEXT
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
    -- usd: billable spend outside requests (backfill). Scout's rows (kinds 'scout_run' and 'tavily', ADR 0120) keep 0
    -- here and their amounts in detail (JSON): their model calls are already in requests.cost_usd.
    usd REAL, realtime_usd REAL, in_tokens INTEGER, out_tokens INTEGER, detail TEXT
);
CREATE TABLE IF NOT EXISTS backfill_jobs (   -- historical prompts relabelled by the teacher (FR-16.8)
    job TEXT PRIMARY KEY,
    ts REAL, backend TEXT, teacher TEXT, license TEXT,
    prompts INTEGER, unique_prompts INTEGER, outputs_discarded INTEGER,
    source_models TEXT,        -- JSON: {model: outputs discarded}
    status TEXT, usd REAL, realtime_usd REAL
);
CREATE TABLE IF NOT EXISTS tombstones (      -- erased example keys (hash and canon_hash), never stored again (FR-16.9)
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
    folded_ts REAL,            -- when these rows were folded; one fold may leave several rows per key, readers sum them
    answered INTEGER, answered_escalated INTEGER  -- rows the student answered, and of those escalated (FR-7.9, SM-5)
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


# Every state a version can be in (ARCHITECTURE §5). A release moves a version only through `transition` and
# `promote_live` (compare-and-set, ADR 0064): at most one version is live, and at most one is in flight (shadow or
# canary), across every process that shares the ledger file. `set_version` stays for tests and migrations.
VERSION_STATES = ("candidate", "rejected", "shadow", "canary", "live", "retired", "rolled_back")
IN_FLIGHT_STATES = ("shadow", "canary")
# The runtime switches an operator sets with the admin API or CLI (ADR 0064), stored in `runtime_flags`. The booleans
# default to off; `audit_rate` (0 to 1) replaces Settings.audit_rate while it is set.
FLAG_NAMES = ("student_disabled", "releases_frozen", "training_paused", "audit_rate")
BOOL_FLAGS = ("student_disabled", "releases_frozen", "training_paused")


@dataclass(frozen=True)
class RuntimeFlags:
    """The operator's runtime switches and task pins (`Ledger.flags`), as one snapshot. `audit_rate` None means
    Settings.audit_rate; `pinned` holds the names of the tasks pinned to the teacher at runtime (`task_overrides`)."""

    student_disabled: bool = False
    releases_frozen: bool = False
    training_paused: bool = False
    audit_rate: float | None = None
    pinned: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready, for the admin API, the CLI and `/ratchet/stats`' `controls`."""
        return {
            "student_disabled": self.student_disabled,
            "releases_frozen": self.releases_frozen,
            "training_paused": self.training_paused,
            "audit_rate": self.audit_rate,
            "pinned_tasks": sorted(self.pinned),
        }


def _flag_value(name: str, raw: Any) -> bool | float | None:
    """A stored flag's value; None for an audit rate that is not a number from 0 to 1 (a row written by hand), which is
    then ignored: a bad row never fails the request path."""
    if name in BOOL_FLAGS:
        return raw in ("1", 1)
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and 0.0 <= rate <= 1.0 else None


# Columns added after v0.1's first schema; existing database files are migrated on open.
MIGRATIONS: dict[str, dict[str, str]] = {
    "requests": {
        "shadow_serve": "INTEGER",
        "client": "TEXT",
        "route": "TEXT",
        "stored": "INTEGER",
        "uses_tools": "INTEGER",
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
        "wall_ms": "REAL",
        "client_request_id": "TEXT",
        "retry_count": "INTEGER DEFAULT 0",
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
        "canon_hash": "TEXT",
    },
    "versions": {"stage_since": "INTEGER DEFAULT 0", "bom": "TEXT"},
    "erasures": {"actor": "TEXT", "reason": "TEXT", "scope": "TEXT", "purged": "INTEGER", "kept": "INTEGER"},
    "daily_rollup": {"answered": "INTEGER", "answered_escalated": "INTEGER"},
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
    # The routing snapshot's rebuild reads only the live, canary and shadow rows (ADR 0060). SQLite ends every index with
    # the rowid, so requests_shadow_version already serves `shadow_version=? AND id>?` ranges.
    "CREATE INDEX IF NOT EXISTS versions_state ON versions (state)",
    # The tasks requests named and when, of every client and of one (recent_tasks and task_seen, for GET /v1/models;
    # FR-1.50): a few index probes per distinct task name instead of a scan of every request.
    "CREATE INDEX IF NOT EXISTS requests_task_ts ON requests (task, ts)",
    "CREATE INDEX IF NOT EXISTS requests_client_task_ts ON requests (client, task, ts)",
    "CREATE INDEX IF NOT EXISTS version_leaves_key ON version_leaves (key)",
    "CREATE INDEX IF NOT EXISTS teacher_checks_epoch ON teacher_checks (epoch)",
    # The teacher watch's quarantine (FR-16.7): the contested rows by task without a scan of every example.
    "CREATE INDEX IF NOT EXISTS examples_contested ON examples (contested, task)",
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
    # Scout's guard and credit meter restore today's and this cycle's rows at startup (ADR 0120).
    "CREATE INDEX IF NOT EXISTS spend_kind_ts ON spend (kind, ts)",
    # Request lookups by id (requests_request_id, above), the failures of the last hour (/ratchet/stats) and today's
    # spend per client (/metrics).
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
LEAF_SPLITS = ("train", "golden")  # the splits a version commits to (version_leaves.split)
# Spend rows whose `detail` may be rewritten after they are written: a Scout run's row, updated with its cost so far while
# it runs (ADR 0120). Every other kind (a backfill's bill, a Tavily search) is written once and never edited.
MUTABLE_SPEND_KINDS = ("scout_run",)
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

# The teacher watch (FR-16.7, ADR 0033). Its ledger-wide state is in `meta` under these keys: the check epoch (1 until the
# first ack or model change), the ledger day it started (unset for epoch 1, which starts on day 1), whether Night School
# is frozen ('1'), the model id the teacher's responses last reported, and the ids reported since the epoch started (a
# JSON list, at most MAX_EPOCH_MODEL_IDS), so a teacher that alternates between two ids starts one epoch, not one per
# response.
TEACHER_EPOCH, TEACHER_EPOCH_DAY = "teacher_epoch", "teacher_epoch_day"
TEACHER_FROZEN, TEACHER_MODEL = "teacher_frozen", "teacher_model"
TEACHER_MODEL_IDS = "teacher_model_ids"
MAX_EPOCH_MODEL_IDS = 8
# `examples.checked_epoch` of a label that is not one independent greedy teacher answer: sampled above temperature 0 (or
# under a client's reasoning effort), where two labels of one request may differ by design, or chosen by an adjudication
# (a majority picked after a disagreement). It is never compared with another label again, nor probed.
UNCHECKABLE = -1
# `examples.contested`: 0 clean; CONTESTED when a re-test disagreed (quarantined until adjudicated); UNRESOLVED when an
# adjudication found no strict majority (still quarantined, adjudicated again after the CONTESTED rows).
CONTESTED, UNRESOLVED = 1, 2
CHECK_SOURCES = ("duplicate", "probe")  # teacher_checks.source: a stored request's first re-test, or a Night School probe
MAX_MODEL_ID = 200  # characters of a response's model id kept in `meta` and events (the upstream chooses the string)
_META_UPSERT = "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"

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
    "answered", "answered_escalated",
)  # fmt: skip
ROLLUP_INSERT = """INSERT INTO daily_rollup (folded_ts, day, task, client, route, store_opt_out, requests, student_served,
    teacher_served, escalated, cost, baseline, teacher_cost, student_cost, split_unknown, split_unknown_cost, sampled,
    avoided, student_in_tokens, student_out_tokens, served_with_tokens, audited, audit_agree_sum, audit_n, wall_ms_sum,
    latency_n, stored_sum, stored_n, answered, answered_escalated)
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
           SUM(latency_ms), COUNT(latency_ms), SUM(stored), COUNT(stored),
           COUNT(confidence), COALESCE(SUM(escalated=1 AND confidence IS NOT NULL), 0)
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
# The request columns `Ledger.client_window` returns: metadata only, never text. `wall_ms` and `client_request_id` come
# with lane D's migrations (OPS-PERF-HOTPATH, OPS-OBSERVABILITY) and read as None on a ledger without them.
WINDOW_COLUMNS = (
    "id", "ts", "task", "served_by", "route", "student_version", "escalated", "audited", "audit_agree", "shadow_version",
    "shadow_serve", "shadow_agree", "latency_ms", "wall_ms", "cost_usd", "baseline_cost_usd", "student_cost_usd",
    "teacher_cost_usd", "label_p", "stored", "optional_failed", "client_request_id",
)  # fmt: skip
# Where a request failed (`failures.stage`, ADR 0062). Upstream stages: the serving teacher (also the fallback teacher),
# the student's samples, a teacher audit, the shadow's samples or its arbitration, and an upstream a gateway route called
# itself (Scout's models and search). Gateway stages: the daily quota, an API, admin or dashboard key, the Scout budget
# guard, a rate limit of the gateway's own, and the gateway itself (a 413, an unhandled error).
FAILURE_STAGES = ("teacher", "student", "audit", "shadow", "quota", "auth", "budget", "rate", "upstream", "gateway")
FAILURE_COLUMNS = frozenset({"ts", "day", "request_id", "client", "task", "stage", "error", "status", "latency_ms", "cost_usd"})
# `stats_by_task`'s `compare_source` by a task's source: where its compared fields come from (FR-14.2). An unregistered
# name (`adhoc`) compares whole answers: None.
COMPARE_SOURCES = {"builtin": "registry", "file": "registry", "api": "registry", "auto": "schema"}
# Distinct task names `recent_tasks` finds one index probe at a time; past this, one aggregate over the index is cheaper.
RECENT_TASK_PROBES = 2000
# The route reported for rows written before requests had a route column (v0.4 and earlier files).
UNRECORDED_ROUTE = "unrecorded"
# Rows other than the /v1/embeddings pass-through's (route `router.PASSTHROUGH_ROUTE`, FR-1.51): the headline ratios
# (student share, escalation rate, cost per 1k, latency, savings) describe the distillation, which embeddings are not
# part of. A row without a route (older than the column) is never a pass-through.
NOT_PASSTHROUGH = "route IS NOT 'passthrough'"
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
# tallies read rows above the last id they saw), so the request path pays nothing for them. Any write to the runtime
# switches or the task pins invalidates the flags snapshot (`Ledger.flags`, ADR 0064).
WATCHED = {
    "versions": ("INSERT", "UPDATE", "DELETE"),
    "requests": ("UPDATE", "DELETE"),
    "examples": ("UPDATE OF hash, purged_ts", "DELETE"),  # purged_ts: a purge or a restore moves the BOM digest
    "runtime_flags": ("INSERT", "UPDATE", "DELETE"),
    "task_overrides": ("INSERT", "UPDATE", "DELETE"),
}
MAX_TALLIES = 64  # version_live_quality's incremental tallies kept, oldest dropped first (two are used per tick)
# What `update_request` may write (ADR 0072): a pending audit's verdict, a pending shadow's, whether the request stored an
# example, its all-teacher baseline, and the cost and token columns the off-path calls add to. Never a column a cache or
# the routing keys on (`ts`, `day`, `served_by`, `route`, `student_version`, `wall_ms`, ...).
# `student_agree` (FR-16.5) is an off-path audit's label of the student's answer, written with its verdict.
AUDIT_VERDICT = ("audited", "audit_agree", "student_agree")
SHADOW_VERDICT = ("shadow_version", "shadow_serve", "shadow_agree")
ADDABLE_COLUMNS = ("cost_usd", "student_cost_usd", "teacher_cost_usd", "student_in_tokens", "student_out_tokens")
_IN_CHUNK = 500  # ids per `IN (...)` when the tally checks its pending audits

# The release gate's queries, named so tests can check their plans (each must search an index, never scan requests).
# A version's audits of answers it served, above a request id (requests_version_audited): version_live_quality's tally.
AUDIT_TALLY_SQL = (
    "SELECT COUNT(*) n, COUNT(audit_agree) k, COALESCE(SUM(audit_agree), 0) s, MAX(id) top FROM requests"
    " WHERE student_version=? AND audited=1 AND served_by='student' AND id>?"
)
# A version's audits still pending (an off-path audit not done yet, ADR 0072) in the id range the tally just read.
AUDIT_PENDING_SQL = (
    "SELECT id FROM requests WHERE student_version=? AND audited=1 AND served_by='student' AND id>? AND id<=?"
    " AND audit_agree IS NULL"
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
# A release transition, compare-and-set (ADR 0064): it changes the row only while the version is still in the state and
# stage the caller saw, and starts the new stage after the last request logged so far (`stage_since`).
TRANSITION_SQL = (
    "UPDATE versions SET state=?, traffic_pct=?, stage_since=(SELECT COALESCE(MAX(id), 0) FROM requests)"
    " WHERE id=? AND state=? AND COALESCE(traffic_pct, 0)=?"
)
ADMIN_COLUMNS = ("id", "ts", "actor", "action", "target", "reason", "before", "after", "request_id")
MAX_ADMIN_ACTIONS = 1000  # rows one admin_actions read returns at most


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


class TeacherState(NamedTuple):
    """The teacher watch's ledger-wide state (`Ledger.teacher_state`)."""

    epoch: int  # the check epoch, 1 or more
    frozen: bool  # Night School stops training until an operator acknowledges
    since_day: int  # the ledger day the epoch started


def _epoch(value: object) -> int:
    """A check epoch read from `meta`: a whole number of at least 1; 1 when it is unset or unreadable."""
    try:
        epoch = int(str(value))
    except ValueError:
        return 1
    return max(1, epoch)


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


# `Ledger.kpi_days`'s sums and their zeros, for a day that only folded request rows have (FR-7.9, FR-13.4).
KPI_ZERO: dict[str, Any] = {
    "requests": 0, "student_served": 0, "escalated": 0, "answered": 0, "answered_escalated": 0, "cost": 0.0, "baseline": 0.0,
}  # fmt: skip


def _day_range(day_from: int | None, day_to: int | None) -> tuple[str, list[Any]]:
    """A WHERE clause (and its arguments) for ledger days `day_from` to `day_to`, inclusive; None leaves a side open."""
    where, args = "1=1", []
    for op, day in ((">=", day_from), ("<=", day_to)):
        if day is not None:
            if isinstance(day, bool) or not isinstance(day, int):
                raise ValueError(f"a ledger day must be a whole number, not {day!r}")
            where += f" AND day {op} ?"
            args.append(day)
    return where, args


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


def _live_calibration(live: Version | None, task: tasks.Task) -> dict[str, Any]:
    """`stats_by_task`'s view of how the live version holds the student to `task` (FR-14.1)."""
    out: dict[str, Any] = {
        "threshold": None,
        "precision": None,
        "coverage": None,
        "calibrated": None,
        "disabled": None,
        "precision_kind": None,
        "judge_pass_rate": None,
    }
    if live is None or task.pinned:
        return out
    rec = calibration.entry(live.eval, task.name)
    default = calibration.number(live.eval.get("threshold"))
    # Without a record or a global threshold the router uses RATCHET_CONFIDENCE_THRESHOLD, which the ledger does not know;
    # a task with critical classes and no measured record serves nothing whatever the global threshold (FR-2.50).
    if rec is not None or default is not None or calibration.spec_changed(live.eval, task) or task.routing_rules.critical:
        out["threshold"] = calibration.threshold(live.eval, task, calibration.SERVE_NOTHING if default is None else default)
    if rec is not None:
        out["precision"] = calibration.number(rec.get("precision"))
        out["coverage"] = calibration.number(rec.get("coverage"))
        out["calibrated"] = rec.get("calibrated") if isinstance(rec.get("calibrated"), bool) else None
        out["disabled"] = rec.get("disabled") if isinstance(rec.get("disabled"), str) else None
        # FR-14.40: what the precision means (a similarity task's is a proxy) and the judge's verdict on its served answers.
        kind = rec.get("precision_kind")
        out["precision_kind"] = kind if kind in tasks.PRECISION_KINDS else None
        out["judge_pass_rate"] = calibration.number(rec.get("judge_pass_rate"))
    return out


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
    n: int = 0  # audits with a verdict (audit_agree set): the count and AVG's denominator
    agreed: int = 0  # their sum
    pending: set[int] = field(default_factory=set)  # ids at or below last_id whose off-path audit had no verdict yet


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
        # Day 1 starts at the first logged request. Request retention deletes old rows, so a fold first records that
        # moment in `meta` (DAY_ORIGIN_META), which wins over the oldest row left; otherwise the days would renumber.
        origin = self.db.execute("SELECT value FROM meta WHERE key=?", (DAY_ORIGIN_META,)).fetchone()
        first = self.db.execute("SELECT MIN(ts) FROM requests").fetchone()[0]
        self._t0: float | None = float(origin[0]) if origin is not None else (float(first) if first is not None else None)
        last_day = self.db.execute(
            "SELECT MAX(COALESCE((SELECT MAX(day) FROM requests), 0), COALESCE((SELECT MAX(day) FROM daily_rollup), 0))"
        ).fetchone()[0]
        self.day = int(last_day)
        # Hot-path caches (ADR 0060). Generations count this connection's writes to WATCHED tables; PRAGMA data_version
        # changes when another connection (the cron tick, the cost CLI) commits.
        self._gen = dict.fromkeys(WATCHED, 0)
        # The `requests` bumps `update_request` caused (ADR 0072): it rewrites no column a cache keys on, and the audit
        # tallies follow pending rows themselves, so those rewrites are left out of the generation the caches compare
        # (`_requests_gen`). `_gen_lock` makes a reader's (bumps, generation) pair consistent.
        self._offpath_bumps = 0
        self._gen_lock = threading.Lock()
        self._routing_key: tuple[int, int] | None = None
        self._routing: dict[str, sqlite3.Row] = {}
        self._tallies: dict[tuple[int, int], _Tally] = {}
        # stats_by_day's percentiles per day: (count, highest id, request generation) -> (p50 student, p95).
        self._day_latency: dict[int | None, tuple[tuple[int, int, int | None], float | None, float | None]] = {}
        self._digest: tuple[tuple[int, int | None, int | None, int], str] | None = None  # bill_of_materials' sha
        self._listeners: list[Callable[[dict[str, Any]], object]] = []  # event listeners (add_event_listener)
        # When each (example, sender) repeat was last counted (`add_example`, ADR 0071); None counts every repeat, as
        # before. The gateway sets one from RATCHET_SEEN_DEDUP_S; in memory only, like the request path's other caches.
        self.seen_window: SeenWindow | None = None
        # The runtime switches and task pins (`flags`, ADR 0064), cached like the routing snapshot.
        self._flags_key: tuple[int, int, int] | None = None
        self._flags = RuntimeFlags()
        # `transaction()`: how deep the current thread's transaction is nested, and the events written in it, handed to
        # the listeners once it commits. Only touched under `_lock`, which a transaction holds throughout.
        self._txn_depth = 0
        self._txn_events: list[dict[str, Any]] = []
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
        gen = self._requests_gen() if table == "requests" else self._gen[table]
        return None if self.db.in_transaction else gen

    def _requests_gen(self) -> int:
        """The `requests` generation without `update_request`'s finishing writes (`_offpath_bumps`)."""
        with self._gen_lock:
            return self._gen["requests"] - self._offpath_bumps

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

    def probe(self, wait_s: float = 2.0) -> None:
        """Readiness (FR-12.30, ADR 0070): raise unless this ledger can still be read and written. SELECT 1 on the
        writer's connection, then a write that is rolled back: BEGIN IMMEDIATE takes the write lock (it fails while
        another process holds it past `wait_s`) and re-setting `user_version` to its own value needs a writable file (a
        read-only file or `query_only` connection refuses it, where BEGIN IMMEDIATE alone succeeds). Nothing is
        committed, so no reader cache moves. Waits at most `wait_s` for the ledger's lock (TimeoutError), and lowers the
        connection's busy timeout to `wait_s` meanwhile, so a probe never holds requests behind a stuck writer for
        longer. A connection already inside a transaction (a write of this thread in progress) is only read."""
        if not self._lock.acquire(timeout=max(0.0, wait_s)):
            raise TimeoutError("the ledger's lock was held for longer than the probe waits")
        try:
            self.db.execute("SELECT 1").fetchone()
            if self.db.in_transaction:
                return
            self.db.execute(f"PRAGMA busy_timeout={max(0, int(wait_s * 1000))}")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    version = int(self.db.execute("PRAGMA user_version").fetchone()[0])
                    self.db.execute(f"PRAGMA user_version={version}")
                finally:
                    self.db.execute("ROLLBACK")
            finally:
                self.db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            self._lock.release()

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
    def update_request(self, request_id: int, **cols: Any) -> bool:
        """Finish a logged request's off-path checks (ADR 0072) in one transaction; True when the row exists.

        `audited` and `audit_agree` are written only while the row's audit is pending (`audited=1`, `audit_agree` NULL),
        and `shadow_version`, `shadow_serve` and `shadow_agree` only while its shadow is (`shadow_version` set,
        `shadow_serve` NULL), so a verdict is written once and never rewritten. `stored` only goes up; `baseline_cost_usd`
        is set. `add_<column>` adds to a cost or token column (ADDABLE_COLUMNS) a number, or a sequence of numbers one
        after the other, so the total is the float the request path would have summed call by call. Any other column
        raises ValueError before anything is written.

        None of these is a column a cache keys on, and a verdict only leaves pending, which the audit tallies follow
        themselves (`version_live_quality`), so these writes do not count in the `requests` generation the caches compare
        (`_offpath_bumps`): finishing a request's checks never makes a tick recount a stage or a dashboard re-sort a day."""
        audit = {k: cols.pop(k) for k in AUDIT_VERDICT if k in cols}
        shadow = {k: cols.pop(k) for k in SHADOW_VERDICT if k in cols}
        sets: list[str] = []
        args: list[Any] = []
        if "stored" in cols:
            sets.append("stored=MAX(COALESCE(stored, 0), ?)")
            args.append(int(bool(cols.pop("stored"))))
        if "baseline_cost_usd" in cols:
            sets.append("baseline_cost_usd=?")
            args.append(cols.pop("baseline_cost_usd"))
        for key in [k for k in cols if k.startswith("add_") and k[4:] in ADDABLE_COLUMNS]:
            value = cols.pop(key)
            terms = list(value) if isinstance(value, (list, tuple)) else [value]
            for v in terms:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError(f"{key} takes finite numbers, not {v!r}")
            if terms:
                expr = f"COALESCE({key[4:]}, 0)"
                for _ in terms:
                    expr = f"({expr} + ?)"
                sets.append(f"{key[4:]}={expr}")
                args.extend(terms)
        if cols:
            raise ValueError(f"update_request cannot write {sorted(cols)}")
        if self.db.execute("SELECT 1 FROM requests WHERE id=?", (request_id,)).fetchone() is None:
            return False
        statements: list[tuple[str, list[Any]]] = []
        if audit:
            assigned = ", ".join(f"{k}=?" for k in audit)
            where = "id=? AND audited=1 AND audit_agree IS NULL"
            statements.append((f"UPDATE requests SET {assigned} WHERE {where}", [*audit.values(), request_id]))
        if shadow:
            assigned = ", ".join(f"{k}=?" for k in shadow)
            where = "id=? AND shadow_version IS NOT NULL AND shadow_serve IS NULL"
            statements.append((f"UPDATE requests SET {assigned} WHERE {where}", [*shadow.values(), request_id]))
        if sets:
            statements.append((f"UPDATE requests SET {', '.join(sets)} WHERE id=?", [*args, request_id]))
        try:
            with self._gen_lock:
                before = self._gen["requests"]
                for sql, values in statements:
                    self.db.execute(sql, values)
                self._offpath_bumps += self._gen["requests"] - before
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return True

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
    def events_of_kind_since(self, kind: str, since: float) -> list[dict[str, Any]]:
        """The events of one kind written since `since` (a wall-clock time), oldest first (`events_kind_ts`)."""
        rows = self._rdb.execute("SELECT id, ts, day, kind, detail FROM events WHERE kind=? AND ts>=? ORDER BY id", (kind, since))
        return [dict(r) for r in rows]

    @_locked
    def client_requests_since(self, client: str, ts: float) -> int:
        """Requests logged for one API client since `ts`. The per-key daily quota no longer counts rows on the request
        path: it reads `spend.SpendMeter` (ADR 0063), which reads the ledger at startup through `api_usage_since`."""
        return int(self.db.execute("SELECT COUNT(*) FROM requests WHERE client=? AND ts>=?", (client, ts)).fetchone()[0])

    @_reads
    def last_ids(self) -> dict[str, int]:
        """The highest `requests.id` and `events.id`, 0 for an empty table. The live-run harness keys its rounds by
        request-id ranges and reads a night's outcome from the events after it (FR-8.20): a live ledger's day is wall
        time since the first request, not a round."""
        r = self.db.execute(
            "SELECT (SELECT COALESCE(MAX(id), 0) FROM requests) req, (SELECT COALESCE(MAX(id), 0) FROM events) ev"
        ).fetchone()
        return {"requests": int(r["req"]), "events": int(r["ev"])}

    @_locked
    def client_window(self, client: str, after_id: int, upto_id: int) -> dict[str, Any]:
        """One API client's requests with `after_id < id <= upto_id`, in id order, as metadata only (`WINDOW_COLUMNS`;
        `requests` holds no text, INV-12), and how many rows of other clients (or of none) fall in the same range
        (`other_clients`, of which `no_client` have no client: an open gateway or an internal call). Columns a later
        migration adds (`wall_ms`, `client_request_id`) read as None on a ledger without them. A primary-key range scan."""
        for name, value in (("after_id", after_id), ("upto_id", upto_id)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number, 0 or more, not {value!r}")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(requests)")}
        cols = ", ".join(c if c in have else f"NULL AS {c}" for c in WINDOW_COLUMNS)
        rows = self.db.execute(
            f"SELECT {cols} FROM requests WHERE id > ? AND id <= ? AND client = ? ORDER BY id", (after_id, upto_id, client)
        ).fetchall()
        other = self.db.execute(
            "SELECT COALESCE(SUM(client IS NULL OR client != ?), 0) other, COALESCE(SUM(client IS NULL), 0) none"
            " FROM requests WHERE id > ? AND id <= ?",
            (client, after_id, upto_id),
        ).fetchone()
        return {"rows": [dict(r) for r in rows], "other_clients": int(other["other"]), "no_client": int(other["none"])}

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
        rows = [dict(r) for r in self._rdb.execute(q + " GROUP BY day ORDER BY day", args)]
        if not self._has_rollup(self._rdb):
            return rows
        where = " AND ".join(["1=1"] + (["day >= ?"] if since is not None else []) + (["day <= ?"] if until is not None else []))
        by_day = {r["day"]: r for r in rows}
        for day, f in self._rollup_by("day", f"WHERE {where}", args, con=self._rdb).items():  # folded rows (FR-13.4)
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

    @_locked
    def recent_tasks(self, days: float, client: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """The tasks requests named in the last `days` days of wall-clock time, most recently used first, at most `limit`:
        each as {task, first_ts, last_ts}, `first_ts` being its first request ever. With `client` (an API key name) only
        that key's requests count, so one client's ad hoc task names are never listed to another. For GET /v1/models
        (FR-1.50), which holds the ledger's lock meanwhile: so the cost is three index probes per distinct task name
        (`_task_spans`), not a pass over every request, up to RECENT_TASK_PROBES names; past them, one aggregate over the
        covering index."""
        if isinstance(days, bool) or not isinstance(days, (int, float)) or not 0 < days < math.inf:
            raise ValueError(f"days must be a positive number, not {days!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit must be a whole number, 1 or more, not {limit!r}")
        cutoff = time.time() - days * DAY_SECONDS
        spans = self._task_spans(client, RECENT_TASK_PROBES)
        if spans is None:
            where, args = ("task IS NOT NULL", []) if client is None else ("client = ? AND task IS NOT NULL", [client])
            spans = self.db.execute(f"SELECT task, MIN(ts), MAX(ts) FROM requests WHERE {where} GROUP BY task", args).fetchall()
        recent = sorted((r for r in spans if r[2] is not None and r[2] >= cutoff), key=lambda r: (-r[2], r[0]))
        return [{"task": t, "first_ts": float(first), "last_ts": float(last)} for t, first, last in recent[:limit]]

    def _task_spans(self, client: str | None, most: int) -> list[sqlite3.Row] | None:
        """(task, first ts, last ts) for every distinct task name requests (of `client`, when given) named, found by
        walking the (task, ts) or (client, task, ts) index one name at a time; None when there are more than `most`."""
        who = "" if client is None else "client = :client AND "
        rows = self.db.execute(
            f"""WITH RECURSIVE names(task) AS (
                    SELECT (SELECT MIN(task) FROM requests WHERE {who}task IS NOT NULL)
                    UNION ALL
                    SELECT (SELECT MIN(task) FROM requests WHERE {who}task > names.task) FROM names
                    WHERE names.task IS NOT NULL LIMIT :cap)
                SELECT task, (SELECT MIN(ts) FROM requests WHERE {who}task = names.task),
                       (SELECT MAX(ts) FROM requests WHERE {who}task = names.task)
                FROM names WHERE task IS NOT NULL""",
            {"client": client, "cap": most + 2},
        ).fetchall()
        return rows if len(rows) <= most else None

    @_locked
    def task_seen(self, task: str, client: str | None = None) -> dict[str, float] | None:
        """When requests (with `client`, that key's) first and last named `task`, as {first_ts, last_ts}; None when none
        did. Two index probes, for GET /v1/models/{id}."""
        who = "" if client is None else "client = :client AND "
        probe = f"FROM requests WHERE {who}task = :task"
        q = f"SELECT (SELECT MIN(ts) {probe}), (SELECT MAX(ts) {probe})"
        first, last = self.db.execute(q, {"client": client, "task": task}).fetchone()
        return None if last is None else {"first_ts": float(first), "last_ts": float(last)}

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

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """One write transaction (BEGIN IMMEDIATE ... COMMIT) under the ledger's lock, so what it checks and what it writes
        are atomic across threads and across processes on the same file (the gateway, the cron tick and the admin CLI).
        Nested calls join the outermost one. Events written inside (`event`) are committed with it and handed to the
        listeners after the commit; an exception rolls everything back, events included, and invalidates every cache a
        write inside may have refreshed. Inside, call only reads, `event`, `transition`, `promote_live`, `record_admin`,
        `set_flag` and the task-override writers: any other writer commits at once, which would split the transaction
        (checked at the commit)."""
        with self._lock:
            if self._txn_depth:
                self._txn_depth += 1
                try:
                    yield
                finally:
                    self._txn_depth -= 1
                return
            self._begin()
            self._txn_depth, self._txn_events = 1, []
            try:
                yield
                if not self.db.in_transaction:
                    raise RuntimeError("a write inside Ledger.transaction() committed early; use the methods it names")
                self.db.commit()
            except BaseException:
                if self.db.in_transaction:
                    self.db.rollback()
                for table in self._gen:  # a cache rebuilt inside saw rows that are gone now
                    self._gen[table] += 1
                self._txn_events = []
                raise
            finally:
                self._txn_depth = 0
            written, self._txn_events = self._txn_events, []
            listeners = tuple(self._listeners)
        for row in written:
            self._announce(listeners, row)

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
        count_seen: bool = True,
        sender: str | None = None,
        canon_hash: str | None = None,
        comparable: bool = True,
    ) -> bool | None:
        """Store a teacher-labelled example. Returns True when it was inserted, False when it was a duplicate, and None
        when it was refused because an erasure tombstoned its hash or its `canon_hash` (FR-13.3): nothing is stored or
        bumped, and only the tombstone's `suppressed` count grows. `canon_hash` is the example's key under the default
        redaction (`label` style, no detector plugin; ADR 0082) when it was redacted otherwise; it is stored with the
        row, so an erasure tombstones it too, and an example erased under one style or plugin setting is refused under
        any other.
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
        the text, with this answer's teacher and license (FR-13.4).

        A duplicate adds one to `seen`, which the frequent-request rule reads (INV-3), unless it is a retry (FR-1.70,
        ADR 0071): `count_seen=False` (the client's SDK said it retries) never counts, and with a seen window
        (`seen_window`, RATCHET_SEEN_DEDUP_S) a repeat from the same `sender` counts at most once per window. `sender`
        is who sent the request (the API key's name, '' without one), or None for internal traffic (Scout, the
        simulator, the mock warm-up), whose repeats are traffic by construction and always count. The window is
        checked and the row written under the ledger's lock, so two concurrent retries cannot both count.

        The teacher watch (FR-16.7, ADR 0033): a duplicate whose label and stored label both need no redaction and come
        from the same teacher is the stored request's re-test, and its first one in the current check epoch is compared
        with the stored label (`_check_label`). `comparable` is False for a label the teacher sampled above temperature 0
        or under a client's reasoning effort: such a label is stored as UNCHECKABLE, and as a re-test it is not compared."""
        target = target_params(params)
        h = example_hash(task, messages, target)
        # Without conditions, h is the split key. A golden-only label is golden whatever its hash says (ADR 0091).
        split = "golden" if golden_only else self._split(example_hash(task, messages) if target else h)
        text = json.dumps(messages, ensure_ascii=False)
        stored_params = json.dumps(target, ensure_ascii=False) if target else None
        now = time.time()
        canon = canon_hash if canon_hash and canon_hash != h else None
        keys = (h,) if canon is None else (h, canon)
        hit = self.db.execute(f"SELECT key FROM tombstones WHERE key IN ({','.join('?' * len(keys))})", keys).fetchone()
        if hit is not None:
            return self._refuse_erased(hit["key"], now)
        label = (completion, teacher_model, license_name, license_url, redactions, int(verified), origin)
        window = self.seen_window if sender is not None else None
        try:
            self.db.execute(
                "INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model, license, license_url,"
                " split, redactions, params, client, end_user, last_seen_ts, verified, golden_only, origin, seen, canon_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    canon,
                ),
            )
            if not comparable:  # a sampled label is no reference for a later one (the teacher watch)
                self.db.execute("UPDATE examples SET checked_epoch=? WHERE hash=?", (UNCHECKABLE, h))
            self.db.commit()
            if window is not None:
                window.note(h, sender or "")
            return True
        except sqlite3.IntegrityError:
            pass
        row = self.db.execute("SELECT purge_reason FROM examples WHERE hash=?", (h,)).fetchone()
        reason = row["purge_reason"] if row is not None else None
        if reason == "erasure":  # every erasure writes a tombstone; a row purged without one is refused all the same
            return self._refuse_erased(h, now)
        # A duplicate is one more occurrence of its request (INV-3), unless it is no occurrence (`bump` False) or a
        # client's retry (FR-1.70, ADR 0071): the SDK said so, or the same sender repeated it inside the seen window.
        more = int(bump)
        if more and not count_seen:
            metrics.SEEN_SKIPPED.inc("retry")
            more = 0
        elif more and window is not None and not window.count(h, sender or ""):
            metrics.SEEN_SKIPPED.inc("window")
            more = 0
        if comparable and not redactions and reason is None:  # the stored request's re-test (FR-16.7), before a label replaces it
            self._check_label(h, completion, teacher_model, "duplicate")
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
        self.db.commit()  # also ends the transaction the refused INSERT opened
        return False

    def _refuse_erased(self, h: str, now: float) -> None:
        """Count a tombstoned example offered again (FR-13.3); the caller stores nothing."""
        self.db.execute("INSERT OR IGNORE INTO tombstones (key, ts) VALUES (?, ?)", (h, now))
        self.db.execute("UPDATE tombstones SET suppressed = suppressed + 1 WHERE key=?", (h,))
        self.db.commit()
        return None

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

    @_reads
    def bill_of_materials(self) -> dict[str, Any]:
        """Model bill of materials: which teachers and licenses produced the training data. Only examples that still
        have their text count in `sources` and the fingerprint; purged ones (FR-13.3, FR-13.4) are counted in
        `purged_examples`, so a purge changes the fingerprint. Labels from a provenance source that is not a teacher
        (`licenses.SOURCES`: `human`) are listed apart, in `label_sources`, and count in the fingerprint like the rest
        (FR-3.54). Each adapter's own signed BOM is never touched (INV-14). `examples_sha256` (over every kept example
        hash in order) is recomputed only when the examples' count, highest id or rewrite generation moved: ids are never
        reused, so an insert or a delete moves the first two, and a hash rewritten or a text purged or restored on this
        connection moves the third (`WATCHED`; another process never rewrites a hash in place, and its commits change
        the purged count the key also holds)."""
        gen = self._settled("examples")  # before the snapshot: a later write makes the next call recompute
        grouped = self._rdb.execute(
            "SELECT teacher_model, license, license_url, split, COUNT(*) n FROM examples WHERE purged_ts IS NULL"
            " GROUP BY teacher_model, license, license_url, split"
        ).fetchall()
        rows = [r for r in grouped if r["teacher_model"] not in licenses.SOURCES]
        labels = [r for r in grouped if r["teacher_model"] in licenses.SOURCES]
        count, top, purged = self._rdb.execute(
            "SELECT COUNT(*), MAX(id), COALESCE(SUM(purged_ts IS NOT NULL), 0) FROM examples"
        ).fetchone()
        key = (int(count), top, gen, int(purged))
        if gen is not None and self._digest is not None and self._digest[0] == key:
            sha = self._digest[1]
        else:
            digest = hashlib.sha256()
            for r in self._rdb.execute("SELECT hash FROM examples WHERE purged_ts IS NULL ORDER BY hash"):
                digest.update(r["hash"].encode())
            sha = digest.hexdigest()
            self._digest = (key, sha) if gen is not None else None
        return {
            "generated_at": time.time(),
            "examples_sha256": sha,
            "sources": [dict(r) for r in rows],
            "label_sources": [dict(r) for r in labels],
            "purged_examples": int(purged),
        }

    @_reads
    def privacy_stats(self) -> dict[str, int]:
        """Examples that still have their text, and how many carry redaction placeholders."""
        r = self._rdb.execute(
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
                "SELECT hash, task, split, messages, params, prompt_hash, purge_reason, canon_hash FROM examples"
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
        tombstone each hash and canonical key (`canon_hash`; `add_example` refuses both from now on), list the purged
        rows in `erasure_items` (hashes, task and split, never text), unlink the erased party from the shared examples
        kept (`unlink`: (hash, 'client' or 'end_user', the client's name)), clear the erased end users' hashes from their
        request rows (`unlink_end_users`), and write the `erasures` row with the versions that may hold the data
        (`tainted`; `status` 'pending' when there are some, else 'complete'). `actor`, `reason` and `scope` arrive
        checked and free of request text. Returns the erasure's id and the counts. Signed BOMs are never touched
        (INV-14)."""
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
            keys = [r["hash"] for r in done] + [r["canon_hash"] for r in done if r["canon_hash"]]
            self.db.executemany(
                "INSERT OR IGNORE INTO tombstones (key, erasure_id, ts) VALUES (?,?,?)", [(k, eid, now) for k in keys]
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

    def _has_rollup(self, con: sqlite3.Connection | None = None) -> bool:
        """Whether any request rows were folded; `con` is the connection to read (the writer by default, the reader
        connection from a `_reads` method, so its statements share one snapshot)."""
        return (con or self.db).execute("SELECT 1 FROM daily_rollup LIMIT 1").fetchone() is not None

    def _rollup_by(
        self, key: str, where: str = "", args: Iterable[Any] = (), con: sqlite3.Connection | None = None
    ) -> dict[Any, dict[str, Any]]:
        """Folded request rows summed per `key` (a daily_rollup column), with each route's count in `route:<name>` and
        each opt-out's in `opt_out:<name>`."""
        sums = ", ".join(f"SUM({c}) {c}" for c in ROLLUP_SUMS)
        out: dict[Any, dict[str, Any]] = {}
        for r in (con or self.db).execute(f"SELECT {key} k, {sums} FROM daily_rollup {where} GROUP BY {key}", list(args)):
            out[r["k"]] = {c: r[c] for c in ROLLUP_SUMS}
        per_route = f"SELECT {key} k, route, store_opt_out, SUM(requests) n FROM daily_rollup {where}"
        for r in (con or self.db).execute(per_route + f" GROUP BY {key}, route, store_opt_out", list(args)):
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

    # ---------- the teacher watch (FR-16.7, ADR 0033) ----------
    def _teacher_epoch(self) -> int:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (TEACHER_EPOCH,)).fetchone()
        return _epoch(r["value"] if r is not None else None)

    def _check_label(self, h: str, completion: str, teacher_model: str, source: str) -> bool | None:
        """Compare a new label of stored example `h` with its stored label, if this is the example's first re-test in the
        current check epoch, and record the outcome: a `teacher_checks` row, `checked_epoch` set to the epoch, and
        `contested` set when they disagree. Returns whether they agree, or None when no check ran.

        A check runs only when both labels can be compared: the stored one needs no redaction (a placeholder could make
        two different raw requests share one redacted text), it was written by the same configured teacher, it is not
        UNCHECKABLE or contested (a label a re-test already disagreed with was selected by that disagreement, so it is no
        independent draw any more), and its task is registered, names fields (`Task.verifiable`), is not pinned, and
        finds both labels valid under its current spec (a stored label the spec refuses now says nothing about the
        teacher). The caller checks the new label's redactions. At most one check per example per epoch keeps the checks
        of an epoch valid as an e-process: every check compares two independent draws, never a re-used stored label with
        a second re-test. Runs inside the caller's transaction."""
        row = self.db.execute(
            "SELECT task, completion, teacher_model, redactions, checked_epoch, contested FROM examples WHERE hash=?", (h,)
        ).fetchone()
        if row is None or row["redactions"] or row["contested"] or row["teacher_model"] != teacher_model:
            return None
        epoch = self._teacher_epoch()
        done = int(row["checked_epoch"] or 0)
        if done < 0 or done >= epoch:  # UNCHECKABLE, or already re-tested in this epoch
            return None
        task = tasks.lookup(row["task"])
        if task is None or task.pinned or not task.verifiable:
            return None
        try:
            stored, new = task.key(row["completion"]), task.key(completion)
            if stored is tasks.NO_KEY or new is tasks.NO_KEY:
                return None
            agree = bool(task.match(stored, new))
        except Exception:  # noqa: BLE001 - a comparison that fails is no check; never fail the write it rides on
            return None
        self.db.execute(
            "INSERT INTO teacher_checks (ts, day, epoch, task, source, agree, model) VALUES (?,?,?,?,?,?,?)",
            (time.time(), self.day, epoch, row["task"], source, int(agree), teacher_model),
        )
        self.db.execute("UPDATE examples SET checked_epoch=?, contested=? WHERE hash=?", (epoch, 0 if agree else CONTESTED, h))
        return agree

    @_locked
    def record_teacher_check(self, h: str, completion: str, teacher_model: str, source: str = "probe") -> bool | None:
        """`_check_label` in its own transaction, for a label that is not stored (a Night School probe): whether it agrees
        with stored example `h`'s label, or None when no check ran (already re-tested this epoch, not comparable). The
        check and its writes are one transaction, so the gateway storing a duplicate at the same time cannot add a second
        check of the same example to the epoch."""
        if source not in CHECK_SOURCES:
            raise ValueError(f"source must be one of {', '.join(CHECK_SOURCES)}, not {source!r}")
        self._begin()
        try:
            agree = self._check_label(h, completion, teacher_model, source)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return agree

    @_locked
    def meta_set(self, key: str, value: str) -> None:
        """Set the `meta` table's `key` to `value` (`meta_value` reads it)."""
        self.db.execute(_META_UPSERT, (key, value))
        self.db.commit()

    @_locked
    def teacher_state(self) -> TeacherState:
        """The teacher watch's check epoch, whether Night School is frozen, and the day the epoch started, in one query
        of `meta`."""
        keys = (TEACHER_EPOCH, TEACHER_FROZEN, TEACHER_EPOCH_DAY)
        got = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta WHERE key IN (?, ?, ?)", keys)}
        try:
            since = int(str(got.get(TEACHER_EPOCH_DAY, 1)))
        except ValueError:
            since = 1
        return TeacherState(_epoch(got.get(TEACHER_EPOCH)), got.get(TEACHER_FROZEN) == "1", since)

    def _new_epoch(self, kind: str, detail: str, unfreeze: bool, model_ids: list[str]) -> int:
        """Start the next check epoch, today, with `model_ids` as the ids reported in it so far, and write event `kind`
        (its `detail` may use `{epoch}`), inside the caller's transaction. Returns the new epoch."""
        epoch = self._teacher_epoch() + 1
        self.db.execute(_META_UPSERT, (TEACHER_EPOCH, str(epoch)))
        self.db.execute(_META_UPSERT, (TEACHER_EPOCH_DAY, str(self.day)))
        self.db.execute(_META_UPSERT, (TEACHER_MODEL_IDS, json.dumps(model_ids[-MAX_EPOCH_MODEL_IDS:])))
        if unfreeze:
            self.db.execute(_META_UPSERT, (TEACHER_FROZEN, "0"))
        self.db.execute(
            "INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)",
            (time.time(), self.day, kind, detail.format(epoch=epoch)),
        )
        return epoch

    def _epoch_model_ids(self) -> list[str]:
        """The model ids reported since the current epoch started (`TEACHER_MODEL_IDS`); [] when unset or unreadable."""
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (TEACHER_MODEL_IDS,)).fetchone()
        try:
            ids = json.loads(r["value"]) if r is not None and r["value"] else []
        except ValueError:
            return []
        return [str(i) for i in ids] if isinstance(ids, list) else []

    @_locked
    def teacher_model_seen(self, model: str) -> str | None:
        """Record the model id a primary-teacher response reported. When it is an id not yet reported in the current
        epoch and differs from the last one recorded, start a new check epoch with a `teacher_model_changed` event and
        return the previous id; the first id ever seen is only recorded. An id already reported in this epoch (a teacher
        alternating between two backends) only becomes the last one: one epoch and one event per new id, not one per
        response. None otherwise (and for an empty id). One transaction, so two processes that see the change write one
        event and one new epoch."""
        model = model[:MAX_MODEL_ID] if isinstance(model, str) else ""
        if not model:
            return None
        self._begin()
        try:
            r = self.db.execute("SELECT value FROM meta WHERE key=?", (TEACHER_MODEL,)).fetchone()
            before = str(r["value"]) if r is not None and r["value"] else None
            if before == model:
                self.db.commit()
                return None
            self.db.execute(_META_UPSERT, (TEACHER_MODEL, model))
            ids = self._epoch_model_ids() or ([before] if before is not None else [])
            if before is None or model in ids:
                if model not in ids:
                    ids.append(model)
                self.db.execute(_META_UPSERT, (TEACHER_MODEL_IDS, json.dumps(ids[-MAX_EPOCH_MODEL_IDS:])))
                self.db.commit()
                return None
            self._new_epoch(
                "teacher_model_changed",
                f"teacher responses now report model {model} (was {before}); check epoch {{epoch}} starts",
                unfreeze=False,
                model_ids=[before, model],
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return before

    @_locked
    def teacher_freeze(self, epoch: int, detail: str) -> bool:
        """Freeze Night School and write the `teacher_drift` event, unless the epoch moved on since the caller counted
        (an ack or a model change) or it is frozen already. One transaction, so the gateway and a nightly job that both
        find the alarm write one event. Returns whether it froze."""
        self._begin()
        try:
            frozen = self.db.execute("SELECT value FROM meta WHERE key=?", (TEACHER_FROZEN,)).fetchone()
            if self._teacher_epoch() != epoch or (frozen is not None and frozen["value"] == "1"):
                self.db.commit()
                return False
            self.db.execute(_META_UPSERT, (TEACHER_FROZEN, "1"))
            self.db.execute(
                "INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (time.time(), self.day, "teacher_drift", detail)
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return True

    @_locked
    def teacher_ack(self, by: str) -> int:
        """An operator's acknowledgement (`POST /ratchet/teacher/ack`): a new check epoch, Night School unfrozen, and a
        `teacher_ack` event naming who (an admin key's name, never the key). Returns the new epoch."""
        self._begin()
        try:
            frozen = self.db.execute("SELECT value FROM meta WHERE key=?", (TEACHER_FROZEN,)).fetchone()
            was = "Night School resumes" if frozen is not None and frozen["value"] == "1" else "Night School was not frozen"
            last = self.db.execute("SELECT value FROM meta WHERE key=?", (TEACHER_MODEL,)).fetchone()
            ids = [str(last["value"])] if last is not None and last["value"] else []
            epoch = self._new_epoch(
                "teacher_ack", f"acknowledged by {by}: check epoch {{epoch}} starts; {was}", unfreeze=True, model_ids=ids
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return epoch

    @_locked
    def teacher_check_counts(self, epoch: int, by: str = "task") -> dict[str, tuple[int, int]]:
        """{task (or source, with `by='source'`): (checks, disagreements)} of check epoch `epoch`. One aggregate query."""
        if by not in ("task", "source"):
            raise ValueError(f"by must be 'task' or 'source', not {by!r}")
        rows = self.db.execute(
            f"SELECT {by} k, COUNT(*) n, COALESCE(SUM(agree = 0), 0) d FROM teacher_checks WHERE epoch=? GROUP BY {by}",
            (epoch,),
        )
        return {str(r["k"]): (int(r["n"]), int(r["d"])) for r in rows}

    @_locked
    def teacher_check_days(
        self, epoch: int, after_id: int = 0, *, by_task: bool = False
    ) -> tuple[int, list[tuple[str, int, int, int]]]:
        """(the last check id read, [(task, day, checks, disagreements)]) of epoch `epoch`'s checks after id `after_id`,
        by ledger day (and by task with `by_task`; task '' otherwise), days in order. The release gate keeps the epoch's
        counts up to date by reading only the new rows: a primary-key range (`+epoch` keeps the planner off the epoch
        index, which would scan the whole epoch). From the start (`after_id` 0) the epoch index is the short way."""
        cols, group = ("task, day", "task, day") if by_task else ("'' AS task, day", "day")
        where = "id > ? AND +epoch = ?" if after_id else "id > ? AND epoch = ?"
        rows = self.db.execute(
            f"SELECT {cols}, MAX(id) last, COUNT(*) n, COALESCE(SUM(agree = 0), 0) d FROM teacher_checks"
            f" WHERE {where} GROUP BY {group} ORDER BY day, task",
            (after_id, epoch),
        ).fetchall()
        last = max((int(r["last"]) for r in rows), default=after_id)
        return last, [(str(r["task"]), int(r["day"] or 0), int(r["n"]), int(r["d"])) for r in rows]

    @_locked
    def contested_counts(self) -> dict[str, int]:
        """{task: stored examples quarantined because a re-test disagreed (`contested`), adjudicated or not}. On the
        `examples_contested` index."""
        rows = self.db.execute("SELECT task, COUNT(*) n FROM examples WHERE contested > 0 GROUP BY task")
        return {str(r["task"]): int(r["n"]) for r in rows}

    @_locked
    def probe_candidates(self, epoch: int, teacher_model: str, task_names: Iterable[str], limit: int) -> list[sqlite3.Row]:
        """Up to `limit` stored examples a Night School probe may re-ask (FR-5.40): of the tasks `task_names`, written by
        `teacher_model`, needing no redaction, not contested and not yet re-tested in epoch `epoch` (UNCHECKABLE rows
        never), in hash order, so each night takes the next ones and no row is favoured by its content."""
        names = sorted(set(task_names))
        if limit <= 0 or not names:
            return []
        marks = ",".join("?" * len(names))
        return list(
            self.db.execute(
                "SELECT * FROM examples WHERE redactions = 0 AND COALESCE(contested, 0) = 0 AND checked_epoch >= 0"
                f" AND checked_epoch < ? AND teacher_model = ? AND task IN ({marks}) ORDER BY hash LIMIT ?",
                (epoch, teacher_model, *names, limit),
            )
        )

    @_locked
    def contested_examples(self, teacher_model: str, task_names: Iterable[str], limit: int) -> list[sqlite3.Row]:
        """Up to `limit` contested examples of the tasks `task_names` written by `teacher_model`: what one night
        adjudicates (FR-5.41). Those never adjudicated come first, oldest first, then the UNRESOLVED ones (no strict
        majority before), so requests the teacher is split on cannot take every night's adjudications."""
        names = sorted(set(task_names))
        if limit <= 0 or not names:
            return []
        marks = ",".join("?" * len(names))
        return list(
            self.db.execute(
                f"SELECT * FROM examples WHERE contested > 0 AND teacher_model = ? AND task IN ({marks})"
                " ORDER BY contested, id LIMIT ?",
                (teacher_model, *names, limit),
            )
        )

    @_locked
    def resolve_contested(self, h: str, completion: str | None = None) -> bool:
        """End example `h`'s quarantine after an adjudication: `completion` (the majority teacher answer, already cleaned
        and needing no redaction) replaces its label when given; its teacher, license and redaction count stay, because
        the same teacher wrote the majority. The label is UNCHECKABLE from now on: a majority picked after a disagreement
        is no independent teacher draw, so a later re-test is not compared with it. Only a row that is still contested
        changes. Returns whether one did."""
        if completion is None:
            cur = self.db.execute(
                "UPDATE examples SET contested = 0, checked_epoch = ? WHERE hash=? AND contested > 0", (UNCHECKABLE, h)
            )
        else:
            cur = self.db.execute(
                "UPDATE examples SET contested = 0, checked_epoch = ?, completion = ?"
                " WHERE hash=? AND contested > 0 AND redactions = 0",
                (UNCHECKABLE, completion, h),
            )
        self.db.commit()
        return cur.rowcount > 0

    @_locked
    def mark_unresolved(self, h: str) -> bool:
        """Record that an adjudication of contested example `h` found no strict majority: it stays quarantined, as
        UNRESOLVED, and later nights adjudicate it after the labels never adjudicated (`contested_examples`). Returns
        whether a row changed."""
        cur = self.db.execute("UPDATE examples SET contested = ? WHERE hash=? AND contested > 0", (UNRESOLVED, h))
        self.db.commit()
        return cur.rowcount > 0

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
    def merge_task_eval(self, vid: int, task: str, changes: Mapping[str, Any]) -> None:
        """Merge `changes` into version `vid`'s per-task record `eval['tasks'][task]` (FR-14.1), creating it if needed.
        The read and the write are one transaction, so another process's eval write cannot be lost in between. KeyError
        for a version that does not exist."""
        self._begin()
        try:
            row = self.db.execute("SELECT eval FROM versions WHERE id=?", (vid,)).fetchone()
            if row is None:
                raise KeyError(f"no version {vid}")
            ev = json.loads(row["eval"] or "{}")
            records = ev.get("tasks") if isinstance(ev.get("tasks"), dict) else {}
            current = records.get(task) if isinstance(records.get(task), dict) else {}
            ev["tasks"] = {**records, task: {**current, **changes}}
            self.db.execute("UPDATE versions SET eval=? WHERE id=?", (json.dumps(ev), vid))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @_locked
    def set_version(self, vid: int, state: str, traffic_pct: int | None = None) -> None:
        """Put a version in a state unconditionally. For tests and migrations only: a release moves versions with
        `transition` and `promote_live`, which compare and set (ADR 0064), so two processes can never both act on one
        decision."""
        since = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()[0]
        if traffic_pct is None:
            self.db.execute("UPDATE versions SET state=?, stage_since=? WHERE id=?", (state, since, vid))
        else:
            self.db.execute(
                "UPDATE versions SET state=?, traffic_pct=?, stage_since=? WHERE id=?", (state, traffic_pct, since, vid)
            )
        self.db.commit()

    @staticmethod
    def _check_move(state: str, pct: int, what: str) -> None:
        if state not in VERSION_STATES:
            raise ValueError(f"{what} state must be one of {', '.join(VERSION_STATES)}, not {state!r}")
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
            raise ValueError(f"{what} traffic_pct must be a whole number from 0 to 100, not {pct!r}")

    def _cas(self, vid: int, from_state: str, from_pct: int, to_state: str, to_pct: int) -> bool:
        """One compare-and-set on a version row (TRANSITION_SQL), inside a transaction the caller holds."""
        cur = self.db.execute(TRANSITION_SQL, (to_state, to_pct, vid, from_state, from_pct))
        return cur.rowcount == 1

    @_locked
    def transition(
        self,
        vid: int,
        from_state: str,
        from_pct: int | None,
        to_state: str,
        to_pct: int,
        event: tuple[str, str] | None = None,
    ) -> bool:
        """Move version `vid` from (`from_state`, `from_pct`) to (`to_state`, `to_pct`) and start a new stage, only if it
        is still where the caller saw it, and write `event` (kind, detail) in the same transaction, only when the move
        happened. Returns whether it did: False means another process (the cron tick, the admin CLI) or thread moved it
        first, and nothing was written. A move into shadow or canary is refused (False) while another version is in
        flight, so at most one version is ever in flight. A move to live raises ValueError: `promote_live` retires the
        old live version in the same transaction, so two versions are never live (ADR 0064). A `from_pct` of None (a row
        written without one) matches 0, as the stored NULL does."""
        from_pct = 0 if from_pct is None else from_pct
        self._check_move(from_state, from_pct, "from")
        self._check_move(to_state, to_pct, "to")
        if to_state == "live":
            raise ValueError("a version becomes live only through promote_live, which retires the old live version with it")
        with self.transaction():
            if to_state in IN_FLIGHT_STATES:
                marks = ",".join("?" * len(IN_FLIGHT_STATES))
                other = self.db.execute(
                    f"SELECT 1 FROM versions WHERE state IN ({marks}) AND id<>? LIMIT 1", (*IN_FLIGHT_STATES, vid)
                ).fetchone()
                if other is not None:
                    return False
            if not self._cas(vid, from_state, from_pct, to_state, to_pct):
                return False
            if event is not None:
                self.event(*event)
        return True

    @_locked
    def promote_live(
        self,
        vid: int,
        from_state: str,
        from_pct: int | None,
        event: tuple[str, str] | None = None,
        *,
        old_live: int | None,
        retired_event: tuple[str, str] | None = None,
    ) -> bool:
        """Make version `vid` live at 100% and retire `old_live` (the live version the caller saw; None when it saw none)
        in ONE transaction, both compare-and-set: nothing changes unless `vid` is still in (`from_state`, `from_pct`) and
        the versions now live are exactly `old_live`. `retired_event` is written for the retirement, then `event` for the
        promotion, only when both moves happened. Returns whether they did; so two versions are never live, and two
        processes promoting at once promote once (ADR 0064)."""
        from_pct = 0 if from_pct is None else from_pct
        self._check_move(from_state, from_pct, "from")
        if from_state == "live":
            raise ValueError(f"v{vid} is live already")
        with self.transaction():
            live = [int(r[0]) for r in self.db.execute("SELECT id FROM versions WHERE state='live' ORDER BY id")]
            if live != ([] if old_live is None else [old_live]):
                return False
            if not self._cas(vid, from_state, from_pct, "live", 100):
                return False
            if old_live is not None:
                # Checked live above, inside this transaction, whatever its traffic says (a row written by hand).
                self.db.execute(
                    "UPDATE versions SET state='retired', traffic_pct=0,"
                    " stage_since=(SELECT COALESCE(MAX(id), 0) FROM requests) WHERE id=? AND state='live'",
                    (old_live,),
                )
                if retired_event is not None:
                    self.event(*retired_event)
            if event is not None:
                self.event(*event)
        return True

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
        return self._version(r) if r else self._version(r) if r is not None else None

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

    # ---------- runtime switches, task pins and the admin audit trail (ADR 0064) ----------
    @_locked
    def flags(self) -> RuntimeFlags:
        """The operator's runtime switches and task pins, rebuilt only when one was written: on this connection (the
        TEMP triggers' generations) or by another process, such as the admin CLI (PRAGMA data_version), so a change
        applies on the next request without a restart. Each call also publishes the pins to `tasks`
        (`tasks.set_overrides`), so `tasks.get` in this process sees a task pinned at runtime as pinned (INV-10)."""
        key = (
            self._gen["runtime_flags"],
            self._gen["task_overrides"],
            int(self.db.execute("PRAGMA data_version").fetchone()[0]),
        )
        if key != self._flags_key:
            values: dict[str, Any] = {}
            for r in self.db.execute("SELECT name, value FROM runtime_flags"):
                if r["name"] in FLAG_NAMES:
                    value = _flag_value(r["name"], r["value"])
                    if value is not None:
                        values[r["name"]] = value
            pinned = frozenset(r[0] for r in self.db.execute("SELECT task FROM task_overrides WHERE pinned=1"))
            self._flags, self._flags_key = RuntimeFlags(pinned=pinned, **values), key
        flags = self._flags
        if tasks.OVERRIDES != flags.pinned:
            tasks.set_overrides(flags.pinned)
        return flags

    @_locked
    def flag_rows(self) -> dict[str, dict[str, Any]]:
        """Every stored runtime switch, by name: {value, ts, actor} as written (who changed it last, and when)."""
        rows = self.db.execute("SELECT name, value, ts, actor FROM runtime_flags ORDER BY name")
        return {r["name"]: {"value": r["value"], "ts": r["ts"], "actor": r["actor"]} for r in rows}

    @_locked
    def set_flag(self, name: str, value: bool | float | None, actor: str) -> None:
        """Store a runtime switch (FLAG_NAMES): a boolean for the switches, a number from 0 to 1 for `audit_rate`, or None
        to go back to the default (the row is deleted). `actor` is who did it ('api:<admin name>' or 'cli:<user>'),
        never a key. Callers go through `ratchet.admin`, which also writes the audit trail in the same transaction."""
        if name not in FLAG_NAMES:
            raise ValueError(f"flag must be one of {', '.join(FLAG_NAMES)}, not {name!r}")
        text: str | None = None
        if value is not None and name in BOOL_FLAGS:
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false, not {value!r}")
            text = "1" if value else "0"
        elif value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a number from 0 to 1, not {value!r}")
            text = repr(float(value))
        with self.transaction():
            if text is None:
                self.db.execute("DELETE FROM runtime_flags WHERE name=?", (name,))
                return
            self.db.execute(
                "INSERT INTO runtime_flags (name, value, ts, actor) VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET value=excluded.value, ts=excluded.ts, actor=excluded.actor",
                (name, text, time.time(), actor),
            )

    @_locked
    def task_overrides(self) -> list[dict[str, Any]]:
        """The tasks pinned at runtime, by name: task, pinned, ts, actor, reason."""
        return [dict(r) for r in self.db.execute("SELECT * FROM task_overrides ORDER BY task")]

    @_locked
    def set_task_override(self, task: str, actor: str, reason: str) -> None:
        """Pin `task` to the teacher at runtime (`task_overrides`); `ratchet.admin.pin_task` checks the task first."""
        with self.transaction():
            self.db.execute(
                "INSERT INTO task_overrides (task, pinned, ts, actor, reason) VALUES (?,1,?,?,?)"
                " ON CONFLICT(task) DO UPDATE SET pinned=1, ts=excluded.ts, actor=excluded.actor, reason=excluded.reason",
                (task, time.time(), actor, reason),
            )

    @_locked
    def clear_task_override(self, task: str) -> bool:
        """Remove a runtime pin; False when the task had none."""
        with self.transaction():
            cur = self.db.execute("DELETE FROM task_overrides WHERE task=?", (task,))
        return cur.rowcount > 0

    @_locked
    def record_admin(
        self,
        actor: str,
        action: str,
        target: str,
        reason: str | None,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        request_id: str | None = None,
        event: str | None = None,
    ) -> int:
        """One `admin_actions` row and its `admin` event (`event`, or `<action> <target> by <actor>: <reason>`), in one
        transaction (the caller's, when it holds one, so the change and its record commit together). `reason` arrives
        checked and redacted (`ratchet.admin.check_reason`; None only for a tick, where it is optional); `actor` is a
        name, never a key (INV-13). Returns the row id."""
        with self.transaction():
            cur = self.db.execute(
                "INSERT INTO admin_actions (ts, actor, action, target, reason, before, after, request_id)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    actor,
                    action,
                    target,
                    reason,
                    json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
                    json.dumps(after, ensure_ascii=False, sort_keys=True) if after is not None else None,
                    request_id,
                ),
            )
            self.event("admin", event or f"{action} {target} by {actor}: {reason}")
        return int(cur.lastrowid)

    @_reads
    def admin_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        """The newest `limit` admin actions (1 to MAX_ADMIN_ACTIONS), newest first, with `before` and `after` parsed."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ADMIN_ACTIONS:
            raise ValueError(f"limit must be a whole number from 1 to {MAX_ADMIN_ACTIONS}, not {limit!r}")
        out = []
        for r in self._rdb.execute(f"SELECT {', '.join(ADMIN_COLUMNS)} FROM admin_actions ORDER BY id DESC LIMIT ?", (limit,)):
            row = dict(r)
            for k in ("before", "after"):
                try:
                    row[k] = json.loads(row[k]) if row[k] else None
                except ValueError:
                    pass  # written by hand: shown as stored
            out.append(row)
        return out

    # ---------- tasks (the admin API's and auto tasks; built-in and file tasks are never stored) ----------
    @_locked
    def save_task(
        self, name: str, spec: Mapping[str, Any], spec_hash: str, source: str, updated_by: str | None
    ) -> tuple[bool, str | None]:
        """Insert or replace a stored task: `spec` as `taskspec.document` writes it, `updated_by` the admin's key name
        (never the key, INV-13), or None for an auto task, which no admin wrote. Returns (created, the spec_hash it had
        before). The JSON keeps the order of `spec`, because the order of `fields` is part of the task's spec_hash."""
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

    # ---------- spend outside requests ----------
    @_locked
    def add_spend(
        self,
        kind: str,
        ref: str,
        usd: float = 0.0,
        realtime_usd: float | None = None,
        in_tokens: int | None = None,
        out_tokens: int | None = None,
        detail: Mapping[str, Any] | None = None,
        ts: float | None = None,
    ) -> int:
        """Write one `spend` row and return its id. `usd` is billable spend that no request row holds (a backfill's bill);
        Scout's rows keep it 0 and put their amounts in `detail`, written as sorted JSON (ADR 0120)."""
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"a spend kind must be a non-empty string, not {kind!r}")
        for what, value in (("usd", usd), ("realtime_usd", realtime_usd)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < math.inf
            ):
                raise ValueError(f"{what} must be a finite amount, 0 or more, not {value!r}")
        when = time.time() if ts is None else _check_ts(ts, "ts")
        cur = self.db.execute(
            "INSERT INTO spend (ts, day, kind, ref, usd, realtime_usd, in_tokens, out_tokens, detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                when,
                self.day,
                kind,
                ref,
                float(usd),
                None if realtime_usd is None else float(realtime_usd),
                in_tokens,
                out_tokens,
                None if detail is None else json.dumps(dict(detail), sort_keys=True),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    @_locked
    def update_spend_detail(self, row_id: int, detail: Mapping[str, Any]) -> None:
        """Rewrite the `detail` of a spend row of a kind in MUTABLE_SPEND_KINDS (a Scout run's cost so far). Any other
        kind, or a missing row, raises ValueError: a bill or a search is written once. `usd` is never touched."""
        row = self.db.execute("SELECT kind FROM spend WHERE id=?", (row_id,)).fetchone()
        if row is None:
            raise ValueError(f"no spend row {row_id!r}")
        if row["kind"] not in MUTABLE_SPEND_KINDS:
            raise ValueError(f"spend rows of kind {row['kind']!r} are written once; only {list(MUTABLE_SPEND_KINDS)} rows change")
        self.db.execute("UPDATE spend SET detail=? WHERE id=?", (json.dumps(dict(detail), sort_keys=True), row_id))
        self.db.commit()

    @_locked
    def spend_rows(self, kinds: Iterable[str], since_ts: float) -> list[dict[str, Any]]:
        """Spend rows of `kinds` written at or after `since_ts`, oldest first: id, ts, kind, ref and `detail` parsed (in
        Python, so no JSON1 dependency). A row whose detail is not a JSON object is skipped: restoring counters from it
        could only guess."""
        kinds = list(kinds)
        if not kinds:
            return []
        marks = ",".join("?" for _ in kinds)
        out = []
        for r in self.db.execute(
            f"SELECT id, ts, kind, ref, detail FROM spend WHERE kind IN ({marks}) AND ts >= ? ORDER BY id",
            (*kinds, since_ts),
        ):
            try:
                detail = json.loads(r["detail"]) if r["detail"] else None
            except ValueError:
                detail = None
            if isinstance(detail, dict):
                out.append({"id": r["id"], "ts": r["ts"], "kind": r["kind"], "ref": r["ref"], "detail": detail})
        return out

    # ---------- events + stats ----------
    def event(self, kind: str, detail: str) -> None:
        """Append to the release log, then hand the row ({id, ts, day, kind, detail}) to every listener
        (`add_event_listener`), outside the lock when the caller does not hold it. A failing listener is ignored: the
        event is already stored, and a log handler must never fail what wrote it. Inside `transaction()` the row commits
        with the transaction, and the listeners get it after the commit (never for a transaction rolled back)."""
        with self._lock:
            ts, day = time.time(), self.day
            cur = self.db.execute("INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (ts, day, kind, detail))
            row = {"id": int(cur.lastrowid), "ts": ts, "day": day, "kind": kind, "detail": detail}
            if self._txn_depth:
                self._txn_events.append(row)
                return
            self.db.commit()
            listeners = tuple(self._listeners)
        self._announce(listeners, row)

    @staticmethod
    def _announce(listeners: tuple[Callable[[dict[str, Any]], object], ...], row: dict[str, Any]) -> None:
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
    def events_since(self, event_id: int) -> list[dict[str, Any]]:
        """The events with `id > event_id`, in the shape of `events()`: what a night or a release step just wrote."""
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 0:
            raise ValueError(f"event_id must be a whole number, 0 or more, not {event_id!r}")
        return [
            dict(r) for r in self.db.execute("SELECT id, ts, day, kind, detail FROM events WHERE id > ? ORDER BY id", (event_id,))
        ]

    @_locked
    def last_event(self, kind: str) -> dict[str, Any] | None:
        """The latest event of `kind` (id, ts, day, kind, detail), or None. Night School reads the last 'trained' one to
        tell a drift alarm that came after it from one found late."""
        r = self.db.execute("SELECT id, ts, day, kind, detail FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,))
        row = r.fetchone()
        return dict(row) if row is not None else None

    @_locked
    def request_ts(self, rid: int) -> float | None:
        """When request `rid` was logged (its `ts`), or None for an id the ledger does not hold (or a row without one)."""
        r = self.db.execute("SELECT ts FROM requests WHERE id=?", (rid,)).fetchone()
        return None if r is None or r["ts"] is None else float(r["ts"])

    @_locked
    def event_keyed(
        self, key: str, kind: str, detail: str, *, day: int | None = None, meta: dict[str, Any] | None = None
    ) -> bool:
        """Write event `kind` unless one was already written under `key`, and record it in `meta` (key -> the event's
        id, with `meta`'s entries). The check and both writes are one transaction, so two processes watching the same
        ledger (the gateway and `python -m ratchet.live tick`) or a restart that replays it write the event once. `day`
        dates the event when it happened on another day than today (a drift alarm found by replaying old rows). Returns
        whether it wrote."""
        self._begin()
        try:
            if self.db.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone() is not None:
                self.db.commit()
                return False
            cur = self.db.execute(
                "INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)",
                (time.time(), self.day if day is None else day, kind, detail),
            )
            value = json.dumps({"event": cur.lastrowid, **(meta or {})})
            self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
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
        integers (ADR 0025).

        Only audits with a verdict count: a row whose off-path audit is still pending (`audited=1`, `audit_agree` NULL,
        ADR 0072) counts in neither number until its verdict is written, so the count and the agreements always describe
        the same rows (a bound over a count that included pending rows would claim evidence it does not have).

        Incremental (ADR 0060): a tally per (version, `since_id`) keeps the count and sum up to the last id it read, and
        each call reads only rows above it, through `requests_version_audited`. SQLite assigns ids in commit order, so no
        row can appear below a tally later. Pending audits it read are kept by id and checked again on each call, so a
        verdict `update_request` writes later is counted then (a failed or invalid audit leaves as `audited=0`). Any
        other rewrite or deletion of a request on this connection (the `requests` generation) or any commit by another
        connection (PRAGMA data_version) starts the tally again from the first row. The numbers are exactly what one
        `COUNT(audit_agree)`, `SUM(audit_agree)` over those rows gives."""
        since = since_id
        gen = (self._requests_gen(), int(self.db.execute("PRAGMA data_version").fetchone()[0]))
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
            top = int(r["top"])
            tally.n += int(r["k"])
            tally.agreed += int(r["s"])
            if int(r["n"]) > int(r["k"]):  # audits in this range without a verdict yet
                tally.pending.update(int(row[0]) for row in self.db.execute(AUDIT_PENDING_SQL, (vid, tally.last_id, top)))
            tally.last_id = top
        if tally.pending:
            self._settle_pending(tally)
        return tally.n, tally.agreed

    @_locked
    def version_live_quality(
        self, vid: int, current_stage_only: bool = True, task: str | None = None
    ) -> tuple[int, float | None]:
        """(audits, audited precision) for a version, from teacher audits of answers it served (`audit_counts`), in its
        current stage (by default: rows after `stage_since`) or over its whole life; precision is None without audits.
        With `task`, of that task's answers only (FR-14.1, one query). Only audits with a verdict count (ADR 0072): see
        `audit_counts`."""
        since = self._stage_start(vid, current_stage_only)
        if task is None:
            n, s = self.audit_counts(vid, since)
            return n, (s / n if n else None)
        r = self.db.execute(
            "SELECT COUNT(audit_agree) n, AVG(audit_agree) a FROM requests WHERE student_version=? AND audited=1"
            " AND served_by='student' AND id>? AND task=?",
            (vid, since, task),
        ).fetchone()
        n = int(r["n"])
        return n, (float(r["a"]) if n else None)

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
    def has_event(self, kind: str, detail_prefix: str = "") -> bool:
        """Whether an event of `kind` whose detail starts with `detail_prefix` was ever logged. A plain prefix
        comparison, not LIKE: task names may hold '_'. A writer that must not repeat an event uses `event_once`, which
        checks and writes in one transaction."""
        row = self.db.execute(
            "SELECT 1 FROM events WHERE kind=? AND substr(detail, 1, ?)=? LIMIT 1", (kind, len(detail_prefix), detail_prefix)
        ).fetchone()
        return row is not None

    @_locked
    def event_once(self, kind: str, detail_prefix: str, detail: str) -> bool:
        """Log `detail` as a `kind` event unless an event of `kind` whose detail starts with `detail_prefix` was logged
        already; True when it wrote one. The check and the write are one write transaction (`_begin`), so two processes
        on one ledger file (the gateway's tick after a request and a `python -m ratchet.live tick` cron) cannot both
        write it: what keeps a once-only event (a per-task alert, FR-14.1) once-only across processes and restarts.
        ValueError when `detail` does not start with `detail_prefix`, since the event could never be found again."""
        if not detail.startswith(detail_prefix):
            raise ValueError(f"event detail {detail!r} does not start with {detail_prefix!r}")
        self._begin()
        try:
            found = self.db.execute(
                "SELECT 1 FROM events WHERE kind=? AND substr(detail, 1, ?)=? LIMIT 1",
                (kind, len(detail_prefix), detail_prefix),
            ).fetchone()
            if found is None:
                self.db.execute(
                    "INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (time.time(), self.day, kind, detail)
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return found is None

    def _stage_start(self, vid: int, current_stage_only: bool) -> int:
        if not current_stage_only:
            return 0
        v = self.version(vid)
        return v.stage_since if v else 0

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

    def _settle_pending(self, tally: _Tally) -> None:
        """Count the tally's pending audits that have a verdict now and forget those that are no audit any more
        (`audited` 0: the off-path audit failed or measured nothing). One primary-key lookup per id."""
        ids = sorted(tally.pending)
        for i in range(0, len(ids), _IN_CHUNK):
            chunk = ids[i : i + _IN_CHUNK]
            rows = self.db.execute(
                f"SELECT id, audited, audit_agree FROM requests WHERE id IN ({','.join('?' * len(chunk))})", chunk
            ).fetchall()
            found = {int(row["id"]): row for row in rows}
            for rid in chunk:
                row = found.get(rid)
                if row is None or row["audited"] != 1:
                    tally.pending.discard(rid)
                elif row["audit_agree"] is not None:
                    tally.n += 1
                    tally.agreed += int(row["audit_agree"])
                    tally.pending.discard(rid)

    @_locked
    def last_audit_id(self, vid: int) -> int | None:
        """The id of the newest audited request of version `vid`, or None: one seek on `requests_version_audited`, so a
        gate step can tell cheaply whether a new audit arrived since it last looked."""
        row = self.db.execute("SELECT MAX(id) FROM requests WHERE student_version=? AND audited=1", (vid,)).fetchone()
        return None if row[0] is None else int(row[0])

    @_locked
    def task_live_quality(self, vid: int, current_stage_only: bool = True) -> dict[str, tuple[int, float | None]]:
        """{task: (audits, audited precision)} for a version, `version_live_quality` per task in one query (FR-14.1)."""
        rows = self.db.execute(
            "SELECT task, COUNT(*) n, AVG(audit_agree) a FROM requests WHERE student_version=? AND audited=1"
            " AND served_by='student' AND id>? GROUP BY task ORDER BY task",
            (vid, self._stage_start(vid, current_stage_only)),
        )
        return {r["task"]: (int(r["n"]), None if r["a"] is None else float(r["a"])) for r in rows}

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
        `fallback_teacher`: the fallback teacher; ADR 0019). Days whose request rows retention folded (FR-13.4) add
        their `daily_rollup` sums to the rows left; without folded rows every number is computed as before. Embeddings
        pass-through rows (`NOT_PASSTHROUGH`) are left out: they are not part of the distillation these figures
        describe; their spend is in `stats_by_task` and the cost report (`cost_by_day`).
        `p50_student_ms` and `p95_ms` are nearest-rank percentiles of `wall_ms` (the whole cascade on the gateway's clock)
        over the day's student-served and all requests that recorded it and are still kept (None without any). A day's
        percentiles are computed again only when its count or highest id moved (ids are never reused) or a request was
        rewritten or folded on this connection, so a stats read sorts today's rows only."""
        gen = self._settled("requests")  # before the snapshot: a later rewrite makes the next call recompute
        rows = [
            dict(r)
            for r in self._rdb.execute(
                f"""SELECT day, COUNT(*) requests,
                      SUM(served_by='student') student_served,
                      SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      AVG(latency_ms) latency_ms,
                      AVG(CASE WHEN audited=1 THEN audit_agree END) audit_agree,
                      SUM(route='degraded') degraded,
                      SUM(route='fallback_teacher') fallback_teacher,
                      SUM(latency_ms) latency_sum, COUNT(latency_ms) latency_n,
                      SUM(CASE WHEN audited=1 THEN audit_agree END) audit_sum,
                      COUNT(CASE WHEN audited=1 THEN audit_agree END) audit_n,
                      MAX(id) top, COUNT(wall_ms) timed,
                      COALESCE(SUM(served_by='student' AND wall_ms IS NOT NULL), 0) timed_student
               FROM requests WHERE {NOT_PASSTHROUGH} GROUP BY day ORDER BY day"""
            )
        ]
        if self._has_rollup(self._rdb):
            folded = self._rollup_by("day", f"WHERE {NOT_PASSTHROUGH}", con=self._rdb)
            rows = sorted(_merge(rows, "day", folded).values(), key=lambda r: r["day"])
        out = []
        for r in rows:
            n = r["requests"] or 1
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            # A day whose rows were all folded has no kept row to time (`_EMPTY_STATS` has no `top`).
            timing = (r["requests"], r.get("top"), gen)
            p50_student, p95 = self._day_percentiles(r["day"], timing, r.get("timed_student") or 0, r.get("timed") or 0)
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
        each route (`unrecorded` for rows older than the route column), most frequent first. Folded request rows
        (FR-13.4) count too (all but `tool_requests`, which a fold does not keep). Since FR-14.1 also the
        teacher's audits of the task's student answers (`audits`, `audit_agreement`, over every version) and, from the
        live version's eval, how the student is held to it: `threshold` as the router applies it
        (`calibration.threshold`: 1.01 when disabled or when its spec changed since), its held-out `precision` and
        `coverage`, whether its threshold is its own (`calibrated`) and why the release gate disabled it (`disabled`);
        since FR-14.40 also what that precision means (`precision_kind`: `exact`, or `proxy` for a similarity task) and
        the judge's pass rate on the answers it would serve (`judge_pass_rate`, free-text tasks with a judge only).
        They are None without a live version, for a pinned task, and (all but `threshold`) when the live version has no
        record for the task. `compare_keys` are the paths of the fields its answers are compared on, and
        `compare_source` where they come from: `schema` for an auto task (derived from a response_format schema,
        FR-14.2), `registry` for a built-in, file or API task, None for an unregistered name (whole answers compared).
        `tool_requests` counts the requests that offered tools or carried tool calls or results, whatever their route
        (FR-1.41; rows written before the column count as none)."""
        rows = [
            dict(r)
            for r in self._rdb.execute(
                """SELECT task, COUNT(*) requests, SUM(served_by='student') student_served, SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline, AVG(latency_ms) latency_ms,
                      SUM(latency_ms) latency_sum, COUNT(latency_ms) latency_n,
                      SUM(audited=1) audits, AVG(CASE WHEN audited=1 THEN audit_agree END) audit_agree,
                      SUM(CASE WHEN audited=1 THEN audit_agree END) audit_sum,
                      COUNT(CASE WHEN audited=1 THEN audit_agree END) audit_n,
                      SUM(uses_tools=1) tool_requests
               FROM requests GROUP BY task ORDER BY COUNT(*) DESC"""
            )
        ]
        routes: dict[str, dict[str, int]] = {}
        counted = self._rdb.execute("SELECT task, route, COUNT(*) n FROM requests GROUP BY task, route ORDER BY n DESC, route")
        for r in counted:
            per_task = routes.setdefault(r["task"], {})
            key = r["route"] or UNRECORDED_ROUTE
            per_task[key] = per_task.get(key, 0) + int(r["n"])
        if self._has_rollup(self._rdb):
            folded = self._rollup_by("task", con=self._rdb)
            for name, sums in folded.items():
                per_task = routes.setdefault(name, {})
                for k, v in sums.items():
                    if k.startswith("route:"):
                        per_task[k[6:]] = per_task.get(k[6:], 0) + v
                routes[name] = dict(sorted(per_task.items(), key=lambda kv: (-kv[1], kv[0])))
            rows = sorted(_merge(rows, "task", folded).values(), key=lambda r: -r["requests"])  # stable: ties keep order
            for r in rows:  # the audits a fold kept as sums (`_merge` recomputed audit_agree from them)
                f = folded.get(r["task"])
                if f is not None:
                    r["audits"] = int(r.get("audits") or 0) + int(f.get("audited") or 0)
        # Human verdicts on the task's answers (FR-13.30), the latest per request; their rows outlive the requests'.
        human = {
            f["task"]: (int(f["n"]), int(f["ok"]))
            for f in self._rdb.execute(
                "SELECT task, COUNT(*) n, COALESCE(SUM(verdict='correct'), 0) ok FROM feedback GROUP BY task"
            )
        }
        # The live version from this snapshot: a `_reads` method never takes the writer's lock (`Ledger.live` does).
        found = self._rdb.execute("SELECT * FROM versions WHERE state='live' ORDER BY id DESC LIMIT 1").fetchone()
        live = self._version(found) if found is not None else None
        out = []
        for r in rows:
            n = r["requests"] or 1
            cost, base = r["cost"] or 0.0, r["baseline"] or 0.0
            verdicts, correct = human.get(r["task"], (0, 0))
            task = tasks.get(r["task"])
            out.append(
                {
                    "task": r["task"],
                    "pinned": task.pinned,
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
                    "audits": int(r.get("audits") or 0),
                    "audit_agreement": None if r.get("audit_agree") is None else float(r["audit_agree"]),
                    **_live_calibration(live, task),
                    "compare_keys": [f.path for f in task.fields],
                    "compare_source": COMPARE_SOURCES.get(task.source),
                    "tool_requests": int(r.get("tool_requests") or 0),
                }
            )
        return out

    @_reads
    def stats_by_client(self) -> list[dict[str, Any]]:
        """Per API key: requests, cost and savings (the basis for per-client receipts); `end_users`, the distinct end
        users its requests named (FR-13.2); and `stored_share`, the share of its requests that stored or bumped a
        training example (None when no request recorded it, as before v0.5). Folded request rows (FR-13.4) count in
        every figure but `end_users`: a fold keeps no end user, so that counts the rows still kept. Embeddings
        pass-through rows are left out, as in `stats_by_day`."""
        rows = [
            dict(r)
            for r in self._rdb.execute(
                f"""SELECT client, COUNT(*) requests, SUM(served_by='student') student_served,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      COUNT(DISTINCT end_user) end_users, AVG(stored) stored_share,
                      SUM(stored) stored_sum, COUNT(stored) stored_n
               FROM requests WHERE client IS NOT NULL AND {NOT_PASSTHROUGH}
               GROUP BY client ORDER BY COUNT(*) DESC"""
            )
        ]
        if self._has_rollup(self._rdb):
            folded = self._rollup_by("client", f"WHERE client IS NOT NULL AND {NOT_PASSTHROUGH}", con=self._rdb)
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

    @_reads
    def totals(self) -> dict[str, float]:
        """Requests, cost and baseline over the whole ledger, folded request rows included (FR-13.4) and embeddings
        pass-through rows left out, as in `stats_by_day`. `failed_cost_usd` is what failed requests spent without leaving
        a `requests` row (the student's samples before a teacher failure, NFR-3); it counts against the savings:
        `saved_usd` = baseline - cost - failed cost, `savings_pct` = 1 - (cost + failed cost) / baseline. A ledger without
        failures, folded rows or embeddings gives exactly v0.4's numbers."""
        q = f"SELECT COUNT(*) n, SUM(cost_usd) c, SUM(baseline_cost_usd) b FROM requests WHERE {NOT_PASSTHROUGH}"
        r = dict(self._rdb.execute(q).fetchone())
        if self._has_rollup(self._rdb):
            q = f"SELECT SUM(requests) n, SUM(cost) c, SUM(baseline) b FROM daily_rollup WHERE {NOT_PASSTHROUGH}"
            f = self._rdb.execute(q).fetchone()
            r = {"n": r["n"] + int(f["n"] or 0), "c": _add(r["c"], f["c"]), "b": _add(r["b"], f["b"])}
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

    # ---------- success metrics (FR-7.9, ratchet/kpis.py) ----------
    @_locked
    def kpi_days(self) -> list[dict[str, Any]]:
        """Per ledger day, oldest first, the sums the success metrics read (ratchet/kpis.py), folded request rows
        (FR-13.4) included: requests, student-served and escalated requests, cost and baseline, `answered` (requests whose
        student samples came back, so the student judged them: not a cold start, a pinned or teacher-only task, or a
        student that was down) and `answered_escalated` (of those, the ones it was unsure of). Rows folded before the
        `answered` columns existed count as not answered. Rows without a day are left out: they have no place in time."""
        rows = self.db.execute(
            """SELECT day, COUNT(*) requests,
                      COALESCE(SUM(served_by='student'), 0) student_served,
                      COALESCE(SUM(escalated), 0) escalated,
                      COUNT(confidence) answered,
                      COALESCE(SUM(escalated=1 AND confidence IS NOT NULL), 0) answered_escalated,
                      COALESCE(SUM(cost_usd), 0.0) cost,
                      COALESCE(SUM(baseline_cost_usd), 0.0) baseline
               FROM requests WHERE day IS NOT NULL GROUP BY day"""
        )
        by_day = {r["day"]: dict(r) for r in rows}
        if self._has_rollup():
            folded = self.db.execute(
                """SELECT day, SUM(requests) requests, SUM(student_served) student_served, SUM(escalated) escalated,
                          SUM(answered) answered, SUM(answered_escalated) answered_escalated,
                          SUM(cost) cost, SUM(baseline) baseline
                   FROM daily_rollup WHERE day IS NOT NULL GROUP BY day"""
            )
            for f in folded:
                r = by_day.setdefault(f["day"], {"day": f["day"], **KPI_ZERO})
                for col, zero in KPI_ZERO.items():
                    r[col] = _add(r[col], f[col]) or zero
        return [by_day[d] for d in sorted(by_day)]

    @_locked
    def audit_totals(self, day_from: int | None = None, day_to: int | None = None) -> tuple[int, int]:
        """(audits, agreements) pooled over ledger days `day_from` to `day_to` (inclusive; None = unbounded), every version
        together, folded request rows included: each audited request that recorded the teacher's verdict, the rows
        `stats_by_day` averages per day. SM-2 reads it; the release gate counts per version and per stage instead."""
        where, args = _day_range(day_from, day_to)
        r = self.db.execute(
            f"SELECT COUNT(*) n, COALESCE(SUM(audit_agree), 0) agree FROM requests "
            f"WHERE audited=1 AND audit_agree IS NOT NULL AND {where}",
            args,
        ).fetchone()
        n, agree = int(r["n"]), int(r["agree"])
        if self._has_rollup():
            f = self.db.execute(
                f"SELECT COALESCE(SUM(audit_n), 0) n, COALESCE(SUM(audit_agree_sum), 0) agree FROM daily_rollup WHERE {where}",
                args,
            ).fetchone()
            n, agree = n + int(f["n"]), agree + int(f["agree"])
        return n, agree

    @_locked
    def failed_cost(self, day_from: int | None = None, day_to: int | None = None) -> float:
        """What failed requests spent over ledger days `day_from` to `day_to` (inclusive; None = unbounded) without leaving
        a request row, from the `failures` table when the ledger has one (ADR 0062); 0.0 otherwise. It counts against the
        savings (SM-1)."""
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='failures'").fetchone() is None:
            return 0.0
        where, args = _day_range(day_from, day_to)
        return float(self.db.execute(f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM failures WHERE {where}", args).fetchone()[0])

    @_locked
    def latency_quantile(
        self, q: float, day_from: int | None = None, day_to: int | None = None, served_by: str | None = None
    ) -> tuple[float | None, int, str]:
        """(the q-quantile, rows, column) of request latency over ledger days `day_from` to `day_to` (inclusive; None =
        unbounded), of one `served_by` when given, by the nearest-rank rule: the smallest observed value with a share q of
        the values at or below it, never an interpolation (an ORDER BY with an OFFSET, no SQLite extension). The column is
        `wall_ms` (the whole cascade on the gateway's clock) when the ledger has it and a row in the range recorded it,
        else `latency_ms` (the providers' part). Folded rows keep no latencies, so they do not count. (None, 0, column)
        when no row matches."""
        if isinstance(q, bool) or not isinstance(q, (int, float)) or not 0 <= q <= 1:
            raise ValueError(f"q must be a number from 0 to 1, not {q!r}")
        if served_by is not None and not isinstance(served_by, str):
            raise ValueError(f"served_by must be a string, not {served_by!r}")
        base, args = _day_range(day_from, day_to)
        if served_by is not None:
            base += " AND served_by = ?"
            args.append(served_by)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(requests)")}
        n, col = 0, "latency_ms"
        for col in ("wall_ms", "latency_ms") if "wall_ms" in have else ("latency_ms",):
            n = int(self.db.execute(f"SELECT COUNT(*) FROM requests WHERE {base} AND {col} IS NOT NULL", args).fetchone()[0])
            if n:
                break
        if n == 0:
            return None, 0, col
        offset = min(n, max(1, math.ceil(round(q * n, 9)))) - 1
        q_sql = f"SELECT {col} FROM requests WHERE {base} AND {col} IS NOT NULL ORDER BY {col} LIMIT 1 OFFSET ?"
        return float(self.db.execute(q_sql, [*args, offset]).fetchone()[0]), n, col

    @_locked
    def events_of(self, kinds: Iterable[str]) -> list[dict[str, Any]]:
        """The events of the given kinds, oldest first (SM-4 reads the release log without the rest)."""
        wanted = sorted(set(kinds))
        if not wanted:
            return []
        qs = ",".join("?" for _ in wanted)
        q = f"SELECT id, ts, day, kind, detail FROM events WHERE kind IN ({qs}) ORDER BY id"
        return [dict(r) for r in self.db.execute(q, wanted)]
