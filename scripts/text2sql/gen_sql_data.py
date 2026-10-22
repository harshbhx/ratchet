"""Build a text-to-SQL training set and a held-out exam, with every label checked by running it.

The teacher (Nemotron 3 Ultra on Nebius Token Factory) invents a small SQLite database per domain (tables, keys, sample
rows), then writes business questions with SQL answers. Each SQL is EXECUTED on that database and kept only when it is one
read-only SELECT that runs and returns 1 to 100 rows. The exam uses whole databases that never appear in training, and
some domains that never appear in training at all.

Writes train.jsonl, golden.jsonl (with "meta"), dbs.json (the exam's databases) and teacher_manifest.json to --out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_URL = os.environ.get("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
MODEL = os.environ.get("RATCHET_TEACHER_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
PRICE_IN, PRICE_OUT = 1.0, 3.0  # USD per million tokens (Token Factory catalog, read 22 Oct 2026)

DOMAINS = [
    "online retail",
    "software subscriptions (SaaS)",
    "retail banking",
    "clinic appointments",
    "freight and logistics",
    "human resources",
    "university courses",
    "travel bookings",
    "restaurant chain",
    "real estate listings",
    "electric utility billing",
    "telecom plans",
    "music streaming",
    "video game studio",
    "car insurance claims",
    "factory production",
    "farm harvests",
    "charity donations",
    "sports league",
    "public library",
    "airline operations",
    "ad campaigns",
    "IoT sensor readings",
    "hotel reservations",
    "e-learning platform",
    "pharmacy inventory",
    "ride sharing",
    "event ticketing",
    "construction projects",
    "customer support desk",
]
HELD_OUT_DOMAINS = ["airline operations", "pharmacy inventory", "ride sharing", "construction projects", "IoT sensor readings"]
SYSTEM = "You are a SQLite expert. Given a database schema and a question, reply with one SQLite SELECT query and nothing else."


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Budget:
    def __init__(self, max_usd: float):
        self.max_usd, self.tin, self.tout, self.calls, self.stopped = max_usd, 0, 0, 0, False
        self.lock = threading.Lock()

    @property
    def usd(self) -> float:
        return (self.tin * PRICE_IN + self.tout * PRICE_OUT) / 1e6

    def ok(self) -> bool:
        with self.lock:
            if self.usd >= self.max_usd:
                self.stopped = True
                return False
            return True

    def add(self, tin: int, tout: int) -> None:
        with self.lock:
            self.tin += tin
            self.tout += tout
            self.calls += 1


def teacher(budget: Budget, key: str, system: str, user: str, max_tokens: int, temperature: float) -> str | None:
    if not budget.ok():
        return None
    body = json.dumps(
        {
            "model": MODEL,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},  # thinking tokens ate the output limit in the pilot
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            u = d.get("usage") or {}
            budget.add(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)))
            ch = d["choices"][0]
            return ch["message"]["content"] if ch.get("finish_reason") in ("stop", None) else None
        except Exception:  # noqa: BLE001
            if attempt == 3:
                return None
            time.sleep(2 * (attempt + 1))
    return None


# ------------------------------------------------------------------------------------------------ sandbox


def norm_value(v):
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, bytes):
        return v.hex()
    return v


def build_db(script: str) -> tuple[sqlite3.Connection, list[str]] | None:
    """Run the teacher's CREATE/INSERT script in memory; None unless it gives 3+ tables with 5+ rows each."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(script)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if not 3 <= len(tables) <= 7:
            return None
        for t in tables:
            if conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] < 5:
                return None
        ddl = [
            r[0]
            for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")
        ]
    except sqlite3.Error:
        return None
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in (20, 21, 31, 33) else sqlite3.SQLITE_DENY)
    return conn, ddl


def run_select(conn: sqlite3.Connection, sql: str, max_rows: int = 100):
    """(rows, ordered) for one read-only SELECT within a 2 second budget, else None."""
    s = sql.strip().rstrip(";").strip()
    if not re.match(r"(?is)^(select|with)\b", s):
        return None
    deadline = time.monotonic() + 2.0
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
    try:
        rows = conn.execute(s).fetchmany(max_rows + 1)
    except (sqlite3.Error, ValueError):
        return None
    finally:
        conn.set_progress_handler(None, 0)
    if not 1 <= len(rows) <= max_rows:
        return None
    return [tuple(norm_value(v) for v in r) for r in rows], bool(re.search(r"(?i)\border\s+by\b", s))


# ------------------------------------------------------------------------------------------------ teacher prompts

SCHEMA_SYSTEM = "You are a database designer. Output only SQL, in one ```sql block, with no explanation."


def schema_prompt(domain: str, variant: int) -> str:
    return (
        f"Design a small SQLite database for this domain: {domain}. Variation {variant}: invent a different company or context "
        "from other variations, with its own table and column names.\n"
        "Rules: SQLite syntax only. 3 to 5 tables with PRIMARY KEY and FOREIGN KEY clauses, realistic column names, a mix of "
        "TEXT, INTEGER, REAL and dates stored as ISO text. After the CREATE TABLE statements write INSERT statements giving "
        "10 to 15 realistic rows per table, consistent with the foreign keys, with varied values (some repeated categories, "
        "different dates, some NULLs)."
    )


QUESTION_SYSTEM = "You write business questions and exact SQLite answers. Follow the output format exactly."


def question_prompt(script: str, n: int) -> str:
    return (
        f"Here is a SQLite database (schema and data):\n\n{script}\n\n"
        f"Write {n} different questions a business user might ask about this data, "
        "each with the SQLite SELECT query that answers it.\n"
        "Mix: 3 'simple' (one table: filter, sort or top-N), 3 'join' (two or more tables), "
        "2 'aggregate' (GROUP BY, COUNT, SUM, AVG), "
        "2 'hard' (subquery, HAVING, three tables, CASE, or a date calculation).\n"
        "Rules: phrase each question the way a person would, without copying table or column names; every query must be one "
        "read-only SELECT, valid SQLite, answerable from the data above, and return between 1 and 20 rows.\n"
        "Output format, for each item exactly:\n### <number>\nDIFFICULTY: <simple|join|aggregate|hard>\n"
        "QUESTION: <question>\nSQL: <query>\n"
    )


def sql_block(text: str) -> str | None:
    m = re.search(r"```(?:sql|sqlite)?\s*(.*?)```", text, re.S | re.I)
    body = (m.group(1) if m else text).strip()
    return body or None


def parse_items(text: str) -> list[dict]:
    items = []
    for chunk in re.split(r"^###\s*\d*\s*$", text, flags=re.M)[1:]:
        d = re.search(r"DIFFICULTY:\s*(\w+)", chunk)
        q = re.search(r"QUESTION:\s*(.+?)\s*\n\s*SQL:", chunk, re.S)
        s = re.search(r"SQL:\s*(.+)$", chunk, re.S)
        if d and q and s:
            sql = re.sub(r"^```(?:sql)?|```$", "", s.group(1).strip(), flags=re.M).strip()
            items.append({"difficulty": d.group(1).lower(), "question": " ".join(q.group(1).split()), "sql": sql})
    return items


def make_db(job: dict, key: str, budget: Budget, per_db: int) -> dict | None:
    raw = teacher(budget, key, SCHEMA_SYSTEM, schema_prompt(job["domain"], job["variant"]), 6000, 0.7)
    script = sql_block(raw) if raw else None
    built = build_db(script) if script else None
    if not built:
        return {"job": job, "rows": [], "fail": "schema"}
    conn, ddl = built
    text = teacher(budget, key, QUESTION_SYSTEM, question_prompt(script, per_db), 6000, 0.3)
    if not text:
        return {"job": job, "rows": [], "fail": "questions"}
    schema_text = "\n\n".join(d.strip().rstrip(";") + ";" for d in ddl)
    rows, seen, bad = [], set(), 0
    for it in parse_items(text):
        if it["difficulty"] not in ("simple", "join", "aggregate", "hard") or it["question"].lower() in seen:
            bad += 1
            continue
        res = run_select(conn, it["sql"])
        if res is None:
            bad += 1
            continue
        seen.add(it["question"].lower())
        rows.append({"question": it["question"], "sql": it["sql"].strip().rstrip(";") + ";", "difficulty": it["difficulty"]})
    return {"job": job, "script": script, "schema": schema_text, "rows": rows, "rejected_sql": bad}


def example(db: dict, r: dict, meta: bool) -> dict:
    user = f"Schema:\n{db['schema']}\n\nQuestion: {r['question']}"
    out = {
        "prompt": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "completion": [{"role": "assistant", "content": r["sql"]}],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if meta:
        out["meta"] = {
            "db": db["job"]["id"],
            "domain": db["job"]["domain"],
            "difficulty": r["difficulty"],
            "domain_seen": db["job"]["seen"],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-train", type=int, default=1200)
    ap.add_argument("--n-golden", type=int, default=250)
    ap.add_argument("--per-db", type=int, default=10)
    ap.add_argument("--max-usd", type=float, default=3.0)
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    key = os.environ.get("NEBIUS_API_KEY", "")
    if not key:
        sys.exit("NEBIUS_API_KEY is not set (the SQL task needs the real teacher)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    budget = Budget(a.max_usd)
    seen_domains = [d for d in DOMAINS if d not in HELD_OUT_DOMAINS]
    keep = 0.75  # about 3 in 4 teacher items survive the execution check
    n_train_db = math.ceil(a.n_train / (a.per_db * keep))
    n_test_db = math.ceil(a.n_golden / (a.per_db * keep))
    jobs = [
        {
            "id": f"train-{i}",
            "domain": seen_domains[i % len(seen_domains)],
            "variant": i // len(seen_domains) + 1,
            "seen": True,
            "split": "train",
        }
        for i in range(n_train_db)
    ]
    for i in range(n_test_db):
        if i % 5 < 2:  # 40% of the exam: domains the student never trains on
            dom, seen = HELD_OUT_DOMAINS[i % len(HELD_OUT_DOMAINS)], False
        else:  # 60%: a seen domain, but a different database (new variant, never trained on)
            dom, seen = seen_domains[(i * 7) % len(seen_domains)], True
        jobs.append({"id": f"test-{i}", "domain": dom, "variant": 100 + i, "seen": seen, "split": "test"})
    print(f"{len(jobs)} databases to build ({n_train_db} train, {n_test_db} exam)", flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        dbs = [d for d in ex.map(lambda j: make_db(j, key, budget, a.per_db), jobs) if d]
    train_dbs = [d for d in dbs if d["job"]["split"] == "train" and d["rows"]]
    test_dbs = [d for d in dbs if d["job"]["split"] == "test" and d["rows"]]
    train = [example(d, r, False) for d in train_dbs for r in d["rows"]][: a.n_train]
    golden = [example(d, r, True) for d in test_dbs for r in d["rows"]][: a.n_golden]
    used = {g["meta"]["db"] for g in golden}
    if {d["job"]["id"] for d in train_dbs} & used:
        sys.exit("FAIL: a database is in both the training set and the exam")
    (out / "train.jsonl").write_text("\n".join(json.dumps(r) for r in train) + "\n", encoding="utf-8")
    (out / "golden.jsonl").write_text("\n".join(json.dumps(r) for r in golden) + "\n", encoding="utf-8")
    (out / "dbs.json").write_text(
        json.dumps({d["job"]["id"]: d["script"] for d in test_dbs if d["job"]["id"] in used}), encoding="utf-8"
    )
    by_diff = {}
    for g in golden:
        by_diff[g["meta"]["difficulty"]] = by_diff.get(g["meta"]["difficulty"], 0) + 1
    manifest = {
        "mode": "real teacher",
        "task": "text-to-SQL (SQLite), every label executed and checked",
        "teacher_model": MODEL,
        "teacher_base_url": BASE_URL,
        "teacher_license": "OpenMDW-1.1 (NVIDIA Nemotron 3 Ultra model card)",
        "rows": {"train": len(train), "golden": len(golden)},
        "databases": {"train": len(train_dbs), "exam": len(used), "failed_to_build": sum(1 for d in dbs if not d["rows"])},
        "exam_rows_by_difficulty": by_diff,
        "exam_rows_from_unseen_domains": sum(1 for g in golden if not g["meta"]["domain_seen"]),
        "held_out_domains": HELD_OUT_DOMAINS,
        "sql_rejected_by_execution_check": sum(d.get("rejected_sql", 0) for d in dbs),
        "verification": "each SQL executed on its database; kept only if one read-only SELECT returning 1 to 100 rows",
        "spent_usd_estimate": round(budget.usd, 4),
        "teacher_calls": budget.calls,
        "tokens": {"in": budget.tin, "out": budget.tout},
        "stopped_for_budget": budget.stopped,
        "prices_per_million": {"in": PRICE_IN, "out": PRICE_OUT},
        "sha256": {f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in ("train.jsonl", "golden.jsonl", "dbs.json")},
        "overlap_train_exam_databases": 0,
    }
    (out / "teacher_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    if len(train) < 100 or len(golden) < 40:
        sys.exit("FAIL: too few verified rows to train or measure")


if __name__ == "__main__":
    main()
