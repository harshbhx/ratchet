"""SQLite ledger: every request, every training example (with provenance), every model version."""

from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
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
    client TEXT                -- API key name (RATCHET_API_KEYS); NULL when auth is off or for internal calls
);
CREATE TABLE IF NOT EXISTS examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, day INTEGER, task TEXT,
    hash TEXT UNIQUE,
    messages TEXT, completion TEXT,
    teacher_model TEXT, license TEXT, license_url TEXT,
    split TEXT,                -- 'train' | 'golden'
    seen INTEGER DEFAULT 1,    -- how often this exact request occurred (drives the frequent-request rule)
    redactions INTEGER DEFAULT 0  -- PII placeholders inserted before storing (ratchet/redact.py)
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
"""


def example_hash(task: str, messages: list[dict[str, Any]]) -> str:
    blob = json.dumps({"task": task, "messages": messages}, sort_keys=True, ensure_ascii=False)
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
    "requests": {"shadow_serve": "INTEGER", "client": "TEXT"},
    "examples": {"seen": "INTEGER DEFAULT 1", "redactions": "INTEGER DEFAULT 0"},
    "versions": {"stage_since": "INTEGER DEFAULT 0", "bom": "TEXT"},
}
# Created after migrations, because they index columns that old files only get from MIGRATIONS.
INDEXES = ["CREATE INDEX IF NOT EXISTS requests_client_ts ON requests (client, ts)"]

DAY_SECONDS = 86400


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
    ) -> bool:
        """Store a teacher-labelled example. Returns False if it was a duplicate.
        `messages` and `completion` arrive already redacted; `redactions` counts the placeholders."""
        h = example_hash(task, messages)
        # Deterministic split from the hash, so an example never moves between sets.
        split = "golden" if int(h[:8], 16) / 0xFFFFFFFF < self.golden_fraction else "train"
        try:
            self.db.execute(
                "INSERT INTO examples (ts, day, task, hash, messages, completion, teacher_model,"
                " license, license_url, split, redactions) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.execute("UPDATE examples SET seen = seen + 1 WHERE hash=?", (h,))
            self.db.commit()
            return False

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

    @_locked
    def versions(self, states: Iterable[str] | None = None) -> list[Version]:
        rows = self.db.execute("SELECT * FROM versions ORDER BY id").fetchall()
        out = [
            Version(
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
            for r in rows
        ]
        if states is not None:
            wanted = set(states)
            out = [v for v in out if v.state in wanted]
        return out

    @_locked
    def version(self, vid: int) -> Version | None:
        for v in self.versions():
            if v.id == vid:
                return v
        return None

    @_locked
    def live(self) -> Version | None:
        live = self.versions(["live"])
        return live[-1] if live else None

    @_locked
    def by_state(self, state: str) -> Version | None:
        vs = self.versions([state])
        return vs[-1] if vs else None

    # ---------- events + stats ----------
    @_locked
    def event(self, kind: str, detail: str) -> None:
        self.db.execute("INSERT INTO events (ts, day, kind, detail) VALUES (?,?,?,?)", (time.time(), self.day, kind, detail))
        self.db.commit()

    @_locked
    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT id, ts, day, kind, detail FROM events ORDER BY id")]

    @_locked
    def version_live_quality(self, vid: int, current_stage_only: bool = True) -> tuple[int, float | None]:
        """(audits, audited precision) for a version, from teacher audits of answers it served."""
        since = 0
        if current_stage_only:
            v = self.version(vid)
            since = v.stage_since if v else 0
        r = self.db.execute(
            "SELECT COUNT(*) n, AVG(audit_agree) a FROM requests WHERE student_version=? AND audited=1"
            " AND served_by='student' AND id>?",
            (vid, since),
        ).fetchone()
        return int(r["n"]), (None if r["a"] is None else float(r["a"]))

    @_locked
    def shadow_quality(self, vid: int) -> dict[str, Any]:
        """How a shadow candidate would have done on real traffic, next to the live student.

        A row without a verdict (`shadow_serve` NULL) counts toward nothing. A would-serve answer whose arbitration
        failed (`shadow_agree` NULL, `unarbitrated`) counts as a disagreement: arbitration only runs on disagreements,
        so leaving those rows out would raise the precision of the candidate that disagrees most (ADR 0013)."""
        r = self.db.execute(
            """SELECT COUNT(*) n, SUM(shadow_serve) served,
                      AVG(CASE WHEN shadow_serve=1 THEN COALESCE(shadow_agree, 0) END) prec,
                      SUM(shadow_serve=1 AND shadow_agree IS NULL) unarbitrated,
                      AVG(served_by='student') live_cov
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
        rows = self.db.execute(
            """SELECT day, COUNT(*) requests,
                      SUM(served_by='student') student_served,
                      SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline,
                      AVG(latency_ms) latency_ms,
                      AVG(CASE WHEN audited=1 THEN audit_agree END) audit_agree
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
                }
            )
        return out

    @_locked
    def stats_by_task(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT task, COUNT(*) requests, SUM(served_by='student') student_served, SUM(escalated) escalated,
                      SUM(cost_usd) cost, SUM(baseline_cost_usd) baseline, AVG(latency_ms) latency_ms
               FROM requests GROUP BY task ORDER BY COUNT(*) DESC"""
        ).fetchall()
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
