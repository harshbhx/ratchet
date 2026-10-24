"""scripts/toolcall_results.py on a synthetic fixture: no real round 3 score is needed (and none is used here)."""

# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("toolcall_results", ROOT / "scripts" / "toolcall_results.py")
tc = importlib.util.module_from_spec(spec)
sys.modules["toolcall_results"] = tc
spec.loader.exec_module(tc)

SCORER = "def score(exp, ans):\n    return (ans == exp, 'ok' if ans == exp else 'wrong')\n"


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _exam(n: int, kind_key: str) -> list[dict]:
    return [{"id": f"q{i}", kind_key: "a" if i % 2 else "b", "expected": {"name": f"t{i}", "arguments": {}}} for i in range(n)]


def _scored(exam: list[dict], kind_key: str, right: set[int], unanswered: set[int] = frozenset()) -> list[dict]:
    rows = []
    for i, r in enumerate(exam):
        if i in unanswered:
            rows.append({"id": r["id"], "kind": r[kind_key], "answered": False})
            continue
        text = json.dumps(r["expected"]) if i in right else '{"name": "wrong", "arguments": {}}'
        rows.append({"id": r["id"], "kind": r[kind_key], "answered": True, "ok": i in right, "cat": "x", "raw": text})
    return rows


def make_round(root: Path, n_hard: int, n_bfcl: int, adapter_hard: set[int], adapter_bfcl: set[int]) -> Path:
    """A fake tools/round folder shaped like the real one."""
    hard, bfcl = _exam(n_hard, "kind"), _exam(n_bfcl, "category")
    for folder, exam, _name in (("exam_hard", hard, "kind"), ("exam_bfcl", bfcl, "category")):
        _w(root / folder / "exam.jsonl", "\n".join(json.dumps(r) for r in exam) + "\n")
    _w(root / "exam_hard" / "exam_manifest.json", json.dumps({"author_model": "writer-model", "today": "2026-10-24"}))
    _w(root / "exam_bfcl" / "exam_manifest.json", json.dumps({"seed": 1}))
    _w(root / "exam_hard" / "common.py", SCORER)
    _w(root / "exam_bfcl" / "scorer.py", SCORER)
    base_h, base_b = set(range(0, n_hard, 4)), set(range(0, n_bfcl, 4))
    _w(root / "train" / "scored_adapter_hard.json", json.dumps(_scored(hard, "kind", adapter_hard)))
    _w(root / "train" / "scored_adapter_bfcl.json", json.dumps(_scored(bfcl, "category", adapter_bfcl)))
    _w(root / "baseline" / "scored_base_hard.json", json.dumps(_scored(hard, "kind", base_h)))
    _w(root / "baseline" / "scored_base_bfcl.json", json.dumps(_scored(bfcl, "category", base_b)))
    _w(
        root / "baseline" / "scored_ultra_bfcl.json",
        json.dumps(_scored(bfcl, "category", set(range(0, n_bfcl, 10)) | set(range(n_bfcl)) - {0})),
    )
    ultra = [{"id": r["id"], "kind": r["kind"], "correct": i % 10 != 0} for i, r in enumerate(hard)]
    _w(root / "exam_hard" / "ultra_answers.jsonl", "\n".join(json.dumps(u) for u in ultra) + "\n")
    _w(root / "exam_hard" / "ultra_results.json", "{}")
    return root


@pytest.fixture()
def strong(tmp_path):
    rnd = make_round(
        tmp_path / "round",
        100,
        200,
        set(range(0, 100, 10)) | set(range(100)) - set(range(0, 100, 3)),
        set(range(200)) - set(range(0, 200, 5)),
    )
    return rnd, tmp_path / "ev"


def test_wilson_matches_the_measured_baseline():
    s = tc.score(94, 150)
    assert (s["lo"], s["hi"]) == (0.547, 0.7)
    assert tc.score(137, 150)["rate"] == 0.9133
    assert tc.sign_p(0, 0) == 1.0 and tc.sign_p(10, 0) == pytest.approx(0.002, abs=0.0001)


def test_verdict_uses_the_ranges_then_the_gap():
    a, b = tc.score(60, 100), tc.score(50, 100)
    assert tc.verdict(a, b) == "higher"  # 60 vs 50 of 100: ranges overlap, gap of 10 points
    assert tc.verdict(tc.score(90, 100), tc.score(50, 100)) == "above"
    assert tc.verdict(tc.score(30, 100), tc.score(60, 100)) == "below"


def test_verdict_words_for_the_three_cases():
    # (a) ranges apart: above / below
    assert tc.verdict(tc.score(90, 100), tc.score(50, 100)) == "above"
    assert tc.verdict(tc.score(30, 100), tc.score(60, 100)) == "below"
    # (b) ranges overlap, gap under 5 points: about the same (hard vs Ultra 90.7 vs 91.3, BFCL adapter vs base 80.3 vs 77.3)
    assert tc.verdict(tc.score(136, 150), tc.score(137, 150)) == "about_same"
    assert tc.verdict(tc.score(241, 300), tc.score(232, 300)) == "about_same"
    # (c) ranges overlap, gap of 5 points or more: lower / higher (BFCL adapter vs Ultra 80.3 vs 88.3)
    a, u = tc.score(241, 300), tc.score(265, 300)
    assert a["hi"] >= u["lo"] and tc.verdict(a, u) == "lower"
    assert tc.verdict(u, a) == "higher"
    assert tc.verdict(tc.score(55, 100), tc.score(50, 100)) == "higher"  # exactly 5 points


def test_phrase_says_lower_with_the_chance_clause():
    a, u = tc.score(241, 300), tc.score(265, 300)
    text = tc.phrase("lower", a, u, "Nemotron 3 Ultra")
    assert text == (
        "lower than Nemotron 3 Ultra: 80.3% against 88.3% (241/300 against 265/300; "
        "the 95% ranges overlap only slightly, so part of the gap could be chance)"
    )
    assert tc.phrase("higher", u, a, "x").startswith("higher than x: 88.3% against 80.3%")
    assert tc.phrase("about_same", a, tc.score(232, 300), "x").startswith("about the same as x: 80.3% against 77.3%")


def test_pending_when_there_are_no_results(tmp_path):
    assert tc.main(["--check", "--evidence", str(tmp_path / "none")]) == 0
    assert tc.main(["--collect", "--evidence", str(tmp_path / "ev"), "--round-dir", str(tmp_path / "missing")]) == 1


def test_the_real_repository_state_passes_the_check():
    """True before the numbers exist (pending) and after (every number recomputes)."""
    assert tc.main(["--check"]) == 0
    # the committed chart is a fresh render of results.json (the SQL chart's --check pattern); nothing to draw before the numbers exist
    if (ROOT / "evidence" / "toolcall" / "results.json").exists():
        svg = importlib.util.spec_from_file_location("toolcall_results_svg", ROOT / "scripts" / "toolcall_results_svg.py")
        mod = importlib.util.module_from_spec(svg)
        svg.loader.exec_module(mod)
        assert mod.OUT.read_text(encoding="utf-8") == mod.build(), "run python scripts/toolcall_results_svg.py"


def test_collect_check_render_round_trip(strong, capsys):
    rnd, ev = strong
    assert (
        tc.main(["--collect", "--evidence", str(ev), "--round-dir", str(rnd), "--date", "2026-10-25", "--train-rows", "2718"])
        == 0
    )
    assert tc.main(["--check", "--evidence", str(ev)]) == 0
    r = json.loads((ev / "results.json").read_text(encoding="utf-8"))
    assert r["exams"]["hard"]["rows"] == 100 and r["exams"]["bfcl"]["rows"] == 200
    assert r["scores"]["hard"]["adapter"]["agree"] == sum(
        1 for x in json.loads((rnd / "train/scored_adapter_hard.json").read_text()) if x["ok"]
    )
    assert r["headline"]["variant"] == "B"
    assert tc.main(["--render", "--web", "--evidence", str(ev), "--web-out", str(ev / "web.json")]) == 0
    assert tc.main(["--render", "--web", "--check-render", "--evidence", str(ev), "--web-out", str(ev / "web.json")]) == 0
    text = (ev / "rendered" / "README_block.md").read_text(encoding="utf-8")
    assert "above the untrained model" in text and "rented Modal T4, not on Nebius GPUs" in text and "create_ticket" in text
    assert "2,718 training rows" in text
    assert json.loads((ev / "web.json").read_text(encoding="utf-8"))["variant"] == "B"


def test_weak_adapter_keeps_sql_as_the_headline(tmp_path):
    rnd = make_round(tmp_path / "round", 100, 200, set(range(0, 100, 4)), set(range(0, 200, 4)))
    ev = tmp_path / "ev"
    assert tc.main(["--collect", "--evidence", str(ev), "--round-dir", str(rnd)]) == 0
    r = json.loads((ev / "results.json").read_text(encoding="utf-8"))
    assert r["headline"]["variant"] == "A"
    assert r["comparisons"]["hard"]["adapter_vs_base"] == "about_same"
    tc.main(["--render", "--evidence", str(ev)])
    assert "about the same as the untrained model" in (ev / "rendered" / "README_block.md").read_text(encoding="utf-8")


def test_variant_b_needs_both_exams(tmp_path):
    rnd = make_round(tmp_path / "round", 100, 200, set(range(100)) - set(range(0, 100, 5)), set(range(0, 200, 4)))
    ev = tmp_path / "ev"
    tc.main(["--collect", "--evidence", str(ev), "--round-dir", str(rnd)])
    r = json.loads((ev / "results.json").read_text(encoding="utf-8"))
    assert r["comparisons"]["hard"]["adapter_vs_base"] == "above" and r["comparisons"]["bfcl"]["adapter_vs_base"] == "about_same"
    assert r["headline"]["variant"] == "A"


def test_unanswered_rows_count_as_wrong(tmp_path):
    rnd = make_round(tmp_path / "round", 100, 200, set(range(100)), set(range(200)))
    rows = _scored(_exam(100, "kind"), "kind", set(range(100)), unanswered={0, 1})
    _w(rnd / "train" / "scored_adapter_hard.json", json.dumps(rows))
    ev = tmp_path / "ev"
    tc.main(["--collect", "--evidence", str(ev), "--round-dir", str(rnd)])
    r = json.loads((ev / "results.json").read_text(encoding="utf-8"))
    assert r["scores"]["hard"]["adapter"] == tc.score(98, 100) and r["unanswered"]["hard"]["adapter"] == 2


def test_check_catches_an_edited_answer_and_an_edited_result(strong):
    rnd, ev = strong
    tc.main(["--collect", "--evidence", str(ev), "--round-dir", str(rnd)])
    path = ev / "results.json"
    good = path.read_text(encoding="utf-8")
    path.write_text(good.replace('"agree": ', '"agree": 1', 1), encoding="utf-8")
    assert tc.main(["--check", "--evidence", str(ev)]) == 1
    path.write_text(good, encoding="utf-8")
    assert tc.main(["--check", "--evidence", str(ev)]) == 0
    # a verdict flipped inside a copied scored file also changes its hash
    f = ev / "scored" / "scored_adapter_hard.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"ok": false', '"ok": true', 1), encoding="utf-8")
    assert tc.main(["--check", "--evidence", str(ev)]) == 1
