"""Write a second text-to-SQL exam with Claude, as an evaluation set only (never training data).

For each new industry, Claude designs a small SQLite database and writes questions with the query that answers each. Every
query is run in the sandbox and kept only if it is one read-only SELECT that returns 1 to 100 rows (the checks of the first
exam, from gen_sql_data.py). Rows that share a question or a query with the training data are dropped from the exam.
The key is read from ANTHROPIC_API_KEY, never printed or written.

    python scripts/text2sql/gen_exam2.py --out DIR --train train_merged.jsonl [--only 1] [--model claude-sonnet-5-5]
"""

# ruff: noqa: E501  (the prompt below is text sent to the model, kept as written)
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_sql_data as g  # noqa: E402  (build_db, run_select, parse_items, sql_block, example)

API = "https://api.anthropic.com/v1/messages"

# Industries that appear neither in the training data nor in the first exam.
INDUSTRIES = [
    "museum collections",
    "veterinary clinic",
    "wine distribution",
    "film production schedule",
    "wind farm maintenance",
    "court case management",
    "recycling plant operations",
    "podcast network",
    "cinema chain",
    "fishing fleet",
]

PROMPT = (
    "Design a small SQLite database for this industry: {domain}. Give 3 to 5 tables with PRIMARY KEY and FOREIGN KEY clauses, "
    "realistic column names, a mix of TEXT, INTEGER, REAL and dates stored as ISO text, and 10 to 15 realistic rows per table "
    "(consistent with the foreign keys, some repeated categories, different dates, some NULLs), as one SQL script of CREATE TABLE "
    "and INSERT statements in a single ```sql code block.\n\n"
    "Then write {n} different questions a business user might ask about this data, each with the SQLite SELECT query that answers it. "
    "Mix: {simple} 'simple' (one table: filter, sort or top-N), {join} 'join' (two or more tables), {agg} 'aggregate' "
    "(GROUP BY, COUNT, SUM, AVG), {hard} 'hard' (subquery, HAVING, three tables, CASE, or a date calculation).\n"
    "Rules: phrase each question the way a person would, without copying table or column names; every query must be one read-only "
    "SELECT, valid SQLite, answerable from the data above, and return between 1 and 20 rows.\n"
    "After the SQL script, output the questions in exactly this format for each item:\n"
    "### <number>\nDIFFICULTY: <simple|join|aggregate|hard>\nQUESTION: <question>\nSQL: <query>\n"
)


class Meter:
    def __init__(self):
        self.tin = self.tout = self.calls = 0
        self.lock = threading.Lock()


def ask(key: str, model: str, prompt: str, meter: Meter) -> str | None:
    body = json.dumps({"model": model, "max_tokens": 16000, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        API, body, {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.load(r)
            with meter.lock:
                meter.tin += d.get("usage", {}).get("input_tokens", 0)
                meter.tout += d.get("usage", {}).get("output_tokens", 0)
                meter.calls += 1
            return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            try:
                err = json.loads(e.read().decode("utf-8", "replace")).get("error", {})
                detail = f"{err.get('type')}: {str(err.get('message'))[:240]}"
            except Exception:  # noqa: BLE001
                detail = "unreadable body"
            print(f"API error {e.code} ({detail})", flush=True)  # only the error type and message, never the request
            return None
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                print(f"request failed: {type(e).__name__}", flush=True)
                return None
            time.sleep(5 * (attempt + 1))
    return None


def build(job: dict, key: str, model: str, meter: Meter, n: int):
    mix = {"simple": round(n * 0.3), "join": round(n * 0.3), "agg": round(n * 0.2)}
    mix["hard"] = n - sum(mix.values())
    text = ask(key, model, PROMPT.format(domain=job["domain"], n=n, **mix), meter)
    if not text:
        return {"job": job, "rows": [], "fail": "no response"}
    script = g.sql_block(text.split("### 1")[0]) if "### 1" in text else g.sql_block(text)
    built = g.build_db(script) if script else None
    if not built:
        return {"job": job, "rows": [], "fail": "schema", "raw": text}
    conn, ddl = built
    schema_text = "\n\n".join(d.strip().rstrip(";") + ";" for d in ddl)
    rows, seen, bad = [], set(), 0
    for it in g.parse_items(text):
        if it["difficulty"] not in ("simple", "join", "aggregate", "hard") or it["question"].lower() in seen:
            bad += 1
            continue
        if g.run_select(conn, it["sql"]) is None:
            bad += 1
            continue
        seen.add(it["question"].lower())
        rows.append({"question": it["question"], "sql": it["sql"].strip().rstrip(";") + ";", "difficulty": it["difficulty"]})
    return {"job": job, "script": script, "schema": schema_text, "rows": rows, "rejected": bad, "raw": text}


def tables(prompt_user: str) -> frozenset:
    return frozenset(re.findall(r"(?i)create table\s+(?:if not exists\s+)?[\"`]?(\w+)", prompt_user))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--train", required=True, help="the merged training file, to drop rows that overlap it")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--n", type=int, default=20, help="questions asked per database")
    ap.add_argument("--only", type=int, default=0, help="only the first N industries (a pilot)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set")
    out = Path(a.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    jobs = [{"id": f"exam2-{i}", "domain": d} for i, d in enumerate(INDUSTRIES[: a.only or None])]
    meter = Meter()
    with ThreadPoolExecutor(a.workers) as ex:
        dbs = list(ex.map(lambda j: build(j, key, a.model, meter, a.n), jobs))
    for d in dbs:
        if d.get("raw"):
            (out / "raw" / f"{d['job']['id']}.txt").write_text(d["raw"], encoding="utf-8")
    train = [json.loads(line) for line in open(a.train, encoding="utf-8") if line.strip()]
    tq = {r["prompt"][1]["content"].rsplit("Question:", 1)[-1].strip().lower() for r in train}
    ta = {r["completion"][0]["content"].strip() for r in train}
    tt = {tables(r["prompt"][1]["content"]) for r in train}
    golden, used, dropped = [], {}, 0
    for d in dbs:
        if not d["rows"]:
            continue
        if tables(d["schema"]) in tt:
            dropped += len(d["rows"])
            continue
        for r in d["rows"]:
            if r["question"].lower() in tq or r["sql"].strip() in ta:
                dropped += 1
                continue
            ex = g.example(
                {"job": {"id": d["job"]["id"], "domain": d["job"]["domain"], "seen": False}, "schema": d["schema"]}, r, True
            )
            golden.append(ex)
            used[d["job"]["id"]] = d["script"]
    (out / "golden.jsonl").write_text("\n".join(json.dumps(r) for r in golden) + "\n", encoding="utf-8")
    (out / "dbs.json").write_text(json.dumps(used), encoding="utf-8")
    by = {}
    for r in golden:
        by[r["meta"]["difficulty"]] = by.get(r["meta"]["difficulty"], 0) + 1
    manifest = {
        "what": "A second text-to-SQL exam, written by a different model than the training labels. Evaluation only; never trained on.",
        "author_model": a.model,
        "industries": [j["domain"] for j in jobs],
        "rows": len(golden),
        "by_difficulty": by,
        "databases": len(used),
        "failed_databases": sum(1 for d in dbs if not d["rows"]),
        "rejected_by_execution_check": sum(d.get("rejected", 0) for d in dbs),
        "dropped_for_overlap_with_training": dropped,
        "api_calls": meter.calls,
        "tokens": {"in": meter.tin, "out": meter.tout},
        "sha256": {f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in ("golden.jsonl", "dbs.json")},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0 if golden else 1


if __name__ == "__main__":
    sys.exit(main())
