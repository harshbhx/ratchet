"""Every number the pages show about the two text-to-SQL exams, computed from the saved answers.

    python scripts/text2sql/exam_numbers.py            # write web/src/data/exams.json and docs-site/src/data/exams.json
    python scripts/text2sql/exam_numbers.py --check    # exit 1 if either file differs from what the answers give

Strict scoring only (scripts/measure_adapter.py): an answer is right when running it returns the same rows as the reference query on
the same database. Standard library plus measure_adapter; no GPU and no network.
"""

# ruff: noqa: E501  (the exam descriptions below are page text, kept as written)
import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("measure_adapter", ROOT / "scripts" / "measure_adapter.py")
m = importlib.util.module_from_spec(_spec)
sys.modules["measure_adapter"] = m
_spec.loader.exec_module(m)

R1 = ROOT / "evidence" / "text2sql-round1"
R2 = ROOT / "evidence" / "text2sql-round2"
X2 = ROOT / "evidence" / "text2sql-exam2"
OUTPUTS = [ROOT / "web" / "src" / "data" / "exams.json", ROOT / "docs-site" / "src" / "data" / "exams.json"]
DIFFICULTIES = ["simple", "join", "aggregate", "hard"]
MODELS = [
    {"id": "base", "label": "Base model, no training", "short": "Base model"},
    {"id": "teacher", "label": "Teacher (Nemotron 3 Ultra), same prompts", "short": "Teacher"},
    {"id": "r1", "label": "Adapter, round 1 (821 rows)", "short": "Adapter, round 1"},
    {"id": "r2", "label": "Adapter, round 2 (4,322 rows)", "short": "Adapter, round 2"},
]
EXAMS = [
    {
        "id": "exam1",
        "name": "Exam 1",
        "rows": 172,
        "built": "22 Oct 2026",
        "writer": "Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)",
        "writer_note": "The same model that wrote the training labels, so it favours the teacher and anything trained to copy it.",
        "industries": "14 industries; 57 of the 172 questions come from five industries that were never in training.",
        "checked": "Every reference query was run in the sandbox and kept only if it was one read-only SELECT returning 1 to 100 rows.",
        "fingerprint": "6b9c1db49bbe2aecf4157ed27845fe024d1b4fcb540f93d81b6987b982b22864",
        "evidence": "evidence/text2sql-round1",
    },
    {
        "id": "exam2",
        "name": "Exam 2",
        "rows": 200,
        "built": "23 Oct 2026",
        "writer": "Claude Sonnet 5.5, a different lab's model, used for evaluation only",
        "writer_note": "Written after training by a different author, so it does not depend on the teacher's style. No closed model's output is training data anywhere in this project.",
        "industries": "10 industries in neither the training data nor exam 1: museum collections, veterinary clinic, wine distribution, film production, wind farm maintenance, court cases, recycling plant, podcast network, cinema chain, fishing fleet.",
        "checked": "The same sandbox check, with the same mix as exam 1 (60 simple, 60 join, 40 aggregate, 40 hard). No row shares a question, a query or a set of table names with the training data.",
        "fingerprint": "b7634971cf6b0d79e64e30e4c16d1253fad65ab9a501a06edc6c9f8e70d69058",
        "evidence": "evidence/text2sql-exam2",
    },
]


def load(path):
    return m.load_jsonl(path)


def wilson(k, n):
    lo, hi = m.wilson(k, n)
    return round(lo, 4), round(hi, 4)


def score(gold, dbs, answers):
    """Per-question right/wrong for each answers file; answers = {model: (rows, key or None)}."""
    conns = {}
    out = {}
    for model, (rows, key) in answers.items():
        flags = [False] * len(gold)
        for r in rows:
            g = gold[r["i"]]
            db = g["meta"]["db"]
            if db not in conns:
                conns[db] = m.sql_connect(dbs[db])
            text = r[key]["text"] if key else r["text"]
            flags[r["i"]] = bool(m.sql_same(conns[db], g["completion"][0]["content"], text))
        out[model] = flags
    return out, conns


def shape(gold, rows, conns):
    """How the teacher's misses look: same rows with another column count, same count other values, other row count."""
    c = {"exact": 0, "same_rows_other_columns": 0, "same_count_other_values": 0, "other_row_count": 0, "does_not_run": 0}
    for r in rows:
        g = gold[r["i"]]
        conn = conns[g["meta"]["db"]]
        ref, ans = m.sql_rows(conn, g["completion"][0]["content"]), m.sql_rows(conn, r["text"])
        if ans is None:
            c["does_not_run"] += 1
        elif m.sql_same(conn, g["completion"][0]["content"], r["text"]):
            c["exact"] += 1
        elif len(ref[0]) != len(ans[0]):
            c["other_row_count"] += 1
        elif ref[0] and ans[0] and len(ref[0][0]) != len(ans[0][0]):
            c["same_rows_other_columns"] += 1
        else:
            c["same_count_other_values"] += 1
    return c


def exact_two_sided(up, down):
    n, k = up + down, min(up, down)
    return round(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n), 3)


def build() -> dict:
    data = {"scores": {}, "difficulty": {}, "r1_to_r2": {}, "teacher_shape": {}}
    # exam 1: round 1 answers hold the base model and adapter 1, round 2's file holds adapter 2, the teacher's second answers its own file
    g1 = load(R1 / "data" / "golden.jsonl")
    d1 = json.loads((R1 / "data" / "dbs.json").read_text(encoding="utf-8"))
    r1_raw, r2_raw, t1 = (
        load(R1 / "answers" / "raw.jsonl"),
        load(R2 / "answers" / "raw_r2.jsonl"),
        load(R1 / "answers" / "teacher2.jsonl"),
    )
    s1, c1 = score(
        g1, d1, {"base": (r1_raw, "base"), "r1": (r1_raw, "adapter"), "r2": (r2_raw, "adapter"), "teacher": (t1, None)}
    )
    g2 = load(X2 / "data" / "golden.jsonl")
    d2 = json.loads((X2 / "data" / "dbs.json").read_text(encoding="utf-8"))
    v1, v2, t2 = (
        load(X2 / "answers" / "raw_v1.jsonl"),
        load(X2 / "answers" / "raw_v2.jsonl"),
        load(X2 / "answers" / "teacher2.jsonl"),
    )
    s2, c2 = score(g2, d2, {"base": (v1, "base"), "r1": (v1, "adapter"), "r2": (v2, "adapter"), "teacher": (t2, None)})
    for exam, gold, s, conns, teacher_rows in (("exam1", g1, s1, c1, t1), ("exam2", g2, s2, c2, t2)):
        n = len(gold)
        data["scores"][exam] = {}
        for model in ("base", "teacher", "r1", "r2"):
            k = sum(s[model])
            lo, hi = wilson(k, n)
            data["scores"][exam][model] = {"agree": k, "n": n, "rate": round(k / n, 4), "lo": lo, "hi": hi}
        diff = {}
        for d in DIFFICULTIES:
            idx = [i for i, g in enumerate(gold) if g["meta"]["difficulty"] == d]
            diff[d] = {"n": len(idx), **{model: sum(s[model][i] for i in idx) for model in ("base", "teacher", "r1", "r2")}}
        data["difficulty"][exam] = diff
        up = sum(1 for a, b in zip(s["r1"], s["r2"], strict=True) if not a and b)
        down = sum(1 for a, b in zip(s["r1"], s["r2"], strict=True) if a and not b)
        both = sum(1 for a, b in zip(s["r1"], s["r2"], strict=True) if a and b)
        data["r1_to_r2"][exam] = {
            "wrong_to_right": up,
            "right_to_wrong": down,
            "both_right": both,
            "both_wrong": n - up - down - both,
            "p": exact_two_sided(up, down),
        }
        data["teacher_shape"][exam] = shape(gold, teacher_rows, conns)
    return {"updated": "2026-10-24", "models": MODELS, "exams": EXAMS, **data}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    bad = 0
    for path in OUTPUTS:
        if a.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"STALE: {path.relative_to(ROOT)} differs from the saved answers; run scripts/text2sql/exam_numbers.py")
                bad += 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print("wrote", path.relative_to(ROOT))
    if a.check and not bad:
        print("OK: both exams.json files match the saved answers")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
