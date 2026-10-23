"""The exam numbers on the landing page, the judges' page and the docs are the saved answers' numbers (invariant 21).

`scripts/text2sql/exam_numbers.py` scores the answers kept under `evidence/` and writes `web/src/data/exams.json`
and `docs-site/src/data/exams.json`. These tests recompute that and fail if either file is stale, if an evidence
manifest no longer matches its files, or if a page names an exam author the evidence does not.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("exam_numbers", ROOT / "scripts" / "text2sql" / "exam_numbers.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exam_numbers"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def built():
    return _load().build()


@pytest.mark.req("INV-21")
def test_exam_pages_show_the_saved_answers_numbers(built):
    expected = json.dumps(built, indent=1, ensure_ascii=False) + "\n"
    for rel in ("web/src/data/exams.json", "docs-site/src/data/exams.json"):
        assert (ROOT / rel).read_text(encoding="utf-8") == expected, (
            f"{rel} is stale: run python scripts/text2sql/exam_numbers.py"
        )


@pytest.mark.req("INV-21")
def test_the_headline_numbers_are_the_ones_we_state(built):
    s = built["scores"]
    assert {k: v["agree"] for k, v in s["exam1"].items()} == {"base": 22, "teacher": 44, "r1": 57, "r2": 67}
    assert {k: v["agree"] for k, v in s["exam2"].items()} == {"base": 25, "teacher": 30, "r1": 56, "r2": 66}
    assert s["exam1"]["r2"]["n"] == 172 and s["exam2"]["r2"]["n"] == 200


@pytest.mark.req("INV-21")
def test_evidence_manifests_match_their_files():
    for folder in ("text2sql-round1", "text2sql-round2", "text2sql-exam2"):
        manifest = json.loads((ROOT / "evidence" / folder / "MANIFEST.json").read_text(encoding="utf-8"))
        for f in manifest["files"]:
            data = (ROOT / "evidence" / folder / f["file"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == f["sha256"], f"{folder}/{f['file']}"


@pytest.mark.req("INV-21")
def test_exam_2_is_the_frozen_one_and_names_its_author(built):
    exam = next(e for e in built["exams"] if e["id"] == "exam2")
    golden = ROOT / "evidence" / "text2sql-exam2" / "data" / "golden.jsonl"
    assert hashlib.sha256(golden.read_bytes()).hexdigest() == exam["fingerprint"]
    gen = json.loads((ROOT / "evidence" / "text2sql-exam2" / "data" / "exam_manifest.json").read_text(encoding="utf-8"))
    assert gen["sha256"]["golden.jsonl"] == exam["fingerprint"]
    assert "Claude" in exam["writer"] and gen["author_model"].startswith("claude")
    assert gen["dropped_for_overlap_with_training"] == 0
    exam1 = next(e for e in built["exams"] if e["id"] == "exam1")
    assert (
        hashlib.sha256((ROOT / "evidence" / "text2sql-round1" / "data" / "golden.jsonl").read_bytes()).hexdigest()
        == exam1["fingerprint"]
    )
