"""The "Quiz Nano" sample: real exam rows with the recorded answers, for the companion sheet on the site.

    python scripts/text2sql/quiz_sample.py            # write web/src/data/quiz.json
    python scripts/text2sql/quiz_sample.py --check    # exit 1 if the file differs from what the saved answers give

Nothing here calls a model. Each row is one question from the two published text-to-SQL exams, with what the round-2 adapter
(Nano) answered, what the teacher (Ultra) answered on the same prompt, what the untrained base model answered on exam 1 and 2's
saved files, and the reference query. Whether an answer is right is the published scoring (scripts/text2sql/exam_numbers.py,
`score`: the same rows as the reference query on the same database), recomputed from the saved answers, never re-judged.

The sample is 50 rows: 25 per exam, 13 where Nano was right and 12 where it was wrong, chosen with a fixed seed from the rows
whose queries are short enough to show on a card. Standard library plus exam_numbers and measure_adapter; no GPU and no network.
"""

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("exam_numbers", ROOT / "scripts" / "text2sql" / "exam_numbers.py")
en = importlib.util.module_from_spec(_spec)
sys.modules["exam_numbers"] = en
_spec.loader.exec_module(en)

OUTPUT = ROOT / "web" / "src" / "data" / "quiz.json"
SEED = 5
PER_GROUP = {True: 13, False: 12}  # per exam: rows Nano got right, rows Nano got wrong
MAX_SQL = 420  # characters: a query longer than this does not fit on a card


def question_of(gold_row: dict) -> str:
    user = gold_row["prompt"][1]["content"]
    return user.rsplit("Question:", 1)[1].strip()


def build() -> dict:
    exams = []
    # Exam 1: round 1's file has the base model, round 2's the round-2 adapter. Exam 2: v1 has the base model, v2 round 2.
    sources = (
        ("exam1", en.R1, en.R1 / "answers" / "raw.jsonl", en.R2 / "answers" / "raw_r2.jsonl"),
        ("exam2", en.X2, en.X2 / "answers" / "raw_v1.jsonl", en.X2 / "answers" / "raw_v2.jsonl"),
    )
    for exam_id, folder, base_file, r2_file in sources:
        gold = en.load(folder / "data" / "golden.jsonl")
        dbs = json.loads((folder / "data" / "dbs.json").read_text(encoding="utf-8"))
        base_rows, r2_rows, t_rows = en.load(base_file), en.load(r2_file), en.load(folder / "answers" / "teacher2.jsonl")
        flags, _ = en.score(gold, dbs, {"base": (base_rows, "base"), "r2": (r2_rows, "adapter"), "teacher": (t_rows, None)})
        base_text = {r["i"]: r["base"]["text"] for r in base_rows}
        r2_text = {r["i"]: r["adapter"]["text"] for r in r2_rows}
        t_text = {r["i"]: r["text"] for r in t_rows}
        rows = []
        for i, g in enumerate(gold):
            ref = g["completion"][0]["content"]
            texts = [ref, r2_text[i], t_text[i], base_text[i]]
            if any(len(t) > MAX_SQL for t in texts):
                continue
            rows.append(
                {
                    "exam": exam_id,
                    "i": i,
                    "domain": g["meta"]["domain"],
                    "difficulty": g["meta"]["difficulty"],
                    "question": question_of(g),
                    "reference": ref,
                    "nano": {"sql": r2_text[i], "right": flags["r2"][i]},
                    "ultra": {"sql": t_text[i], "right": flags["teacher"][i]},
                    "base": {"sql": base_text[i], "right": flags["base"][i]},
                }
            )
        rng = random.Random(f"{SEED}-{exam_id}")
        for right, n in PER_GROUP.items():
            pool = [r for r in rows if r["nano"]["right"] is right]
            rng.shuffle(pool)
            exams.extend(sorted(pool[:n], key=lambda r: r["i"]))
    return {
        "source": "harshbhx/ratchet-text2sql-exams",
        "evidence": ["evidence/text2sql-round1", "evidence/text2sql-round2", "evidence/text2sql-exam2"],
        "scoring": "an answer is right when running it returns the same rows as the reference query on the same database",
        "rows": exams,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if a.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print(f"STALE: {OUTPUT.relative_to(ROOT)} differs from the saved answers; run scripts/text2sql/quiz_sample.py")
            return 1
        print("ok:", OUTPUT.relative_to(ROOT))
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print("wrote", OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
