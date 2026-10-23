"""Merge round 1 and round 2 training rows, drop duplicates, and check that nothing from the exam is in the result.

Run from the repository root:

    python evidence/text2sql-round2/data/merge_and_check.py

It reads round 1's train.jsonl, this folder's train_round2_new.jsonl.gz and the exam (round 1's golden.jsonl), writes the merged
training file next to this script (train_merged.jsonl, not kept in git) and exits 1 if any test finds a leak.
"""

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND1 = HERE.parents[1] / "text2sql-round1" / "data"


def load(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def question(r):
    return r["prompt"][1]["content"].rsplit("Question:", 1)[-1].strip().lower()


def answer(r):
    return r["completion"][0]["content"].strip()


def key(r):
    return hashlib.sha256(json.dumps(r["prompt"], sort_keys=True).encode()).hexdigest()


def tables(r):
    return frozenset(re.findall(r"(?i)create table\s+(?:if not exists\s+)?[\"`]?(\w+)", r["prompt"][1]["content"]))


def main() -> int:
    old = load(ROUND1 / "train.jsonl")
    new = load(HERE / "train_round2_new.jsonl.gz")
    exam = load(ROUND1 / "golden.jsonl")
    ex_questions = {question(r) for r in exam}
    ex_answers = {answer(r) for r in exam}
    ex_prompts = {key(r) for r in exam}
    ex_tables = {tables(r) for r in exam}
    seen, merged, dup, dropped = set(), [], 0, 0
    for r in old + new:
        k = key(r)
        if k in seen:
            dup += 1
            continue
        if question(r) in ex_questions or answer(r) in ex_answers:  # generic wording shared with the exam: removed to be safe
            dropped += 1
            continue
        seen.add(k)
        merged.append(r)
    res = {
        "train rows round 1": len(old),
        "train rows round 2": len(new),
        "duplicates dropped": dup,
        "rows dropped for sharing an exam question or answer": dropped,
        "merged": len(merged),
        "merged rows whose full prompt is an exam prompt": sum(key(r) in ex_prompts for r in merged),
        "merged rows asking an exam question": sum(question(r) in ex_questions for r in merged),
        "merged rows whose answer is an exam answer word for word": sum(answer(r) in ex_answers for r in merged),
        "merged rows whose exact set of table names is an exam database's": sum(tables(r) in ex_tables for r in merged),
    }
    print(json.dumps(res, indent=1))
    (HERE / "train_merged.jsonl").write_text("\n".join(json.dumps(r) for r in merged) + "\n", encoding="utf-8")
    leaks = [k for k, v in res.items() if k.startswith("merged rows") and v]
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
