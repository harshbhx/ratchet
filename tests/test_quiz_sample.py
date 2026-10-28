"""The "Quiz Nano" sample on the site is the saved answers' own rows (invariant 21, the same rule as the exam numbers).

`scripts/text2sql/quiz_sample.py` reads the answers kept under `evidence/` and writes `web/src/data/quiz.json`. The marks in it
are the published scoring (the same rows as the reference query, `exam_numbers.score`), recomputed, never re-judged. These
tests fail if the file is stale (so its marks disagree with a fresh scoring) or if it stops being a balanced sample.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUIZ = ROOT / "web" / "src" / "data" / "quiz.json"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("quiz_sample", ROOT / "scripts" / "text2sql" / "quiz_sample.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["quiz_sample"] = m
    spec.loader.exec_module(m)
    return m


@pytest.mark.req("INV-21")
def test_quiz_sample_is_the_saved_answers(mod):
    assert QUIZ.read_text(encoding="utf-8") == json.dumps(mod.build(), indent=1, ensure_ascii=False) + "\n", (
        "web/src/data/quiz.json is stale: run scripts/text2sql/quiz_sample.py"
    )


@pytest.mark.req("INV-21")
def test_quiz_sample_is_balanced_and_short():
    d = json.loads(QUIZ.read_text(encoding="utf-8"))
    rows = d["rows"]
    assert d["source"] == "harshbhx/ratchet-text2sql-exams"
    assert 40 <= len(rows) <= 60
    for exam in ("exam1", "exam2"):
        mine = [r for r in rows if r["exam"] == exam]
        assert mine, exam
        right = sum(r["nano"]["right"] for r in mine)
        assert 0.4 <= right / len(mine) <= 0.6, f"{exam}: {right} right of {len(mine)}"
    assert len({(r["exam"], r["i"]) for r in rows}) == len(rows)
    for r in rows:
        for key in ("reference", "nano", "ultra", "base"):
            sql = r[key] if key == "reference" else r[key]["sql"]
            assert 0 < len(sql) <= mod_max(), (r["exam"], r["i"], key)
        assert all(isinstance(r[k]["right"], bool) for k in ("nano", "ultra", "base"))
    # the file stays a small separate chunk: under 80 KB of JSON
    assert QUIZ.stat().st_size < 80_000


def mod_max() -> int:
    return 420
