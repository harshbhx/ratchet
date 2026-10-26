"""Every number about the tool-calling exams (round 3), computed from the saved scored answers. Standard library only.

    python scripts/toolcall_results.py --collect     # copy the scored files and exams into evidence/toolcall/, hash them, write results.json
    python scripts/toolcall_results.py --check       # exit 1 if results.json, the hashes or a row's verdict no longer follow from the evidence
    python scripts/toolcall_results.py --render      # write the README, docs and VERIFY blocks from results.json (--notes [DIR]: also the Devpost, Notion and Hugging Face notes)
    python scripts/toolcall_results.py --render --web   # also write web/src/data/toolcall.json (the pages' data file)

Strict scoring only: an answer counts when it is one JSON object that the exam's scorer accepts (extra text around the JSON is wrong).
A row the model did not answer counts as wrong. No adapter number is typed in this file: `--collect` reads the scored files, and
`--check` also rescores every saved answer with the exam's own scorer (copied into evidence/toolcall/scorers/) and compares the verdicts.

With no results.json yet, `--check` prints "pending" and exits 0, so CI passes before the numbers exist. Tests point `--evidence` at a
synthetic fixture. Nothing here uses the network or a GPU.
"""

# ruff: noqa: E501  (the drafts below are page text, kept as written)
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE = ROOT / "evidence" / "toolcall"
DEFAULT_ROUND = Path(r"C:\Users\Harshu\Documents\ratchet-proof\tools\round")
SCHEMA = "ratchet.toolcall-results/v1"

# evidence name -> path under the round folder
SOURCES = {
    "scored/scored_adapter_hard.json": "train/scored_adapter_hard.json",
    "scored/scored_adapter_bfcl.json": "train/scored_adapter_bfcl.json",
    "scored/scored_base_hard.json": "baseline/scored_base_hard.json",
    "scored/scored_base_bfcl.json": "baseline/scored_base_bfcl.json",
    "scored/scored_ultra_bfcl.json": "baseline/scored_ultra_bfcl.json",
    "ultra/ultra_answers_hard.jsonl": "exam_hard/ultra_answers.jsonl",
    "ultra/ultra_results_hard.json": "exam_hard/ultra_results.json",
    "exams/exam_hard.jsonl": "exam_hard/exam.jsonl",
    "exams/exam_hard_manifest.json": "exam_hard/exam_manifest.json",
    "exams/exam_bfcl.jsonl": "exam_bfcl/exam.jsonl",
    "exams/exam_bfcl_manifest.json": "exam_bfcl/exam_manifest.json",
    "scorers/common.py": "exam_hard/common.py",
    "scorers/scorer.py": "exam_bfcl/scorer.py",
}
REQUIRED_FOR_BUILD = [k for k in SOURCES if k.startswith(("scored/", "exams/", "ultra/"))]
EXAMS = {
    "hard": {
        "exam": "exams/exam_hard.jsonl",
        "manifest": "exams/exam_hard_manifest.json",
        "kind_key": "kind",
        "scorer": "common",
    },
    "bfcl": {
        "exam": "exams/exam_bfcl.jsonl",
        "manifest": "exams/exam_bfcl_manifest.json",
        "kind_key": "category",
        "scorer": "scorer",
    },
}
SCORED = {
    ("hard", "base"): "scored/scored_base_hard.json",
    ("hard", "adapter"): "scored/scored_adapter_hard.json",
    ("bfcl", "base"): "scored/scored_base_bfcl.json",
    ("bfcl", "adapter"): "scored/scored_adapter_bfcl.json",
    ("bfcl", "ultra"): "scored/scored_ultra_bfcl.json",
}
MODEL_LABEL = {"base": "Untrained Nano 4B", "adapter": "Tool-calling adapter", "ultra": "Teacher (Nemotron 3 Ultra)"}
EXAM_LABEL = {"hard": "Hard exam", "bfcl": "BFCL exam"}


# ---------------------------------------------------------------- statistics


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The same interval as scripts/measure_adapter.py, 4 decimals."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def sign_p(up: int, down: int) -> float:
    """Exact two-sided sign test on the questions that changed (McNemar's exact form)."""
    n, k = up + down, min(up, down)
    if n == 0:
        return 1.0
    return round(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n), 4)


def score(correct: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(correct, n)
    return {"agree": correct, "n": n, "rate": round(correct / n, 4) if n else 0.0, "lo": lo, "hi": hi}


GAP_POINTS = 0.05  # overlapping ranges and a gap of 5 points or more: say lower/higher, not "about the same"


def verdict(a: dict, b: dict) -> str:
    """a against b. 'above' / 'below' when the 95% ranges do not overlap. Overlapping ranges: 'about_same' when the point
    estimates are less than 5 points apart, else 'higher' / 'lower' (the gap is real in the data, but may be chance)."""
    if a["lo"] > b["hi"]:
        return "above"
    if a["hi"] < b["lo"]:
        return "below"
    gap = a["agree"] / a["n"] - b["agree"] / b["n"]
    if round(abs(gap), 9) < GAP_POINTS:
        return "about_same"
    return "higher" if gap > 0 else "lower"


# ---------------------------------------------------------------- files


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scored_rows(ev: Path, exam: str, model: str) -> list[dict]:
    return jload(ev / SCORED[(exam, model)])


def score_rows(rows: list[dict], total: int, kind_key_of: dict[str, str]) -> tuple[dict, dict, int]:
    """Overall score and a score per kind; a row the model did not answer (or that is missing) is wrong."""
    by_id = {r["id"]: r for r in rows}
    unanswered = sum(1 for i in kind_key_of if not by_id.get(i, {}).get("answered"))
    ok = sum(1 for i in kind_key_of if by_id.get(i, {}).get("answered") and by_id[i].get("ok"))
    kinds: dict[str, list[int]] = {}
    for i, k in kind_key_of.items():
        c = kinds.setdefault(k, [0, 0])
        c[1] += 1
        c[0] += 1 if by_id.get(i, {}).get("answered") and by_id[i].get("ok") else 0
    return score(ok, total), {k: score(*v) for k, v in sorted(kinds.items())}, unanswered


def paired(a_rows: list[dict], b_rows: list[dict]) -> dict[str, Any]:
    """A against B on the same questions, over those both models answered (as score_adapter.py does)."""
    b = {r["id"]: r for r in b_rows}
    fixed = broke = both_right = both_wrong = 0
    for r in a_rows:
        y = b.get(r["id"])
        if not y or not r.get("answered") or not y.get("answered"):
            continue
        if r["ok"] and y["ok"]:
            both_right += 1
        elif r["ok"]:
            fixed += 1
        elif y["ok"]:
            broke += 1
        else:
            both_wrong += 1
    return {"fixed": fixed, "broke": broke, "both_right": both_right, "both_wrong": both_wrong, "p": sign_p(fixed, broke)}


def build(ev: Path, date: str | None = None, training: dict | None = None) -> dict[str, Any]:
    """results.json's content, from the evidence folder alone."""
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "strict": True,
        "exams": {},
        "scores": {},
        "kinds": {},
        "unanswered": {},
        "paired": {},
    }
    for exam, spec in EXAMS.items():
        rows = jsonl(ev / spec["exam"])
        man = jload(ev / spec["manifest"])
        kind_of = {r["id"]: r[spec["kind_key"]] for r in rows}
        info = {"rows": len(rows), "sha256": sha256(ev / spec["exam"]), "kinds": dict(sorted(_count(kind_of.values()).items()))}
        if exam == "hard":
            info.update(writer=man.get("author_model", ""), tools=20, areas=8, date=man.get("today", ""))
        else:
            info.update(source="Berkeley Function Calling Leaderboard v3", license="Apache-2.0", seed=man.get("seed"))
        out["exams"][exam] = info
        out["scores"][exam], out["kinds"][exam], out["unanswered"][exam] = {}, {}, {}
        models = ["base", "adapter"] + (["ultra"] if exam == "bfcl" else [])
        for model in models:
            s, k, un = score_rows(scored_rows(ev, exam, model), len(rows), kind_of)
            out["scores"][exam][model], out["kinds"][exam][model], out["unanswered"][exam][model] = s, k, un
        if exam == "hard":  # the teacher's hard-exam answers carry their own verdicts
            ua = jsonl(ev / "ultra/ultra_answers_hard.jsonl")
            s = score(sum(1 for r in ua if r.get("correct")), len(rows))
            out["scores"][exam]["ultra"] = s
            kk: dict[str, list[int]] = {}
            for r in ua:
                c = kk.setdefault(r["kind"], [0, 0])
                c[1] += 1
                c[0] += 1 if r.get("correct") else 0
            out["kinds"][exam]["ultra"] = {k: score(*v) for k, v in sorted(kk.items())}
            out["unanswered"][exam]["ultra"] = len(rows) - len(ua)
        out["paired"][exam] = {"adapter_vs_base": paired(scored_rows(ev, exam, "adapter"), scored_rows(ev, exam, "base"))}
    out["comparisons"] = {
        exam: {
            "adapter_vs_base": verdict(out["scores"][exam]["adapter"], out["scores"][exam]["base"]),
            "adapter_vs_ultra": verdict(out["scores"][exam]["adapter"], out["scores"][exam]["ultra"]),
        }
        for exam in EXAMS
    }
    both_above = all(out["comparisons"][e]["adapter_vs_base"] == "above" for e in EXAMS)
    out["headline"] = {
        "variant": "B" if both_above else "A",
        "rule": "B (tool calling leads) only when the adapter's 95% range is above the untrained model's on BOTH exams, with no overlap; otherwise A (text-to-SQL stays the headline)",
        "reason": "adapter above the untrained model on both exams, ranges not overlapping"
        if both_above
        else "adapter not above the untrained model on both exams ("
        + ", ".join(f"{e}: {out['comparisons'][e]['adapter_vs_base']}" for e in EXAMS)
        + ")",
    }
    if date:
        out["date"] = date
    if training:
        out["training"] = training
    return out


def _count(values) -> dict[str, int]:
    c: dict[str, int] = {}
    for v in values:
        c[v] = c.get(v, 0) + 1
    return c


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- collect and check


def collect(ev: Path, round_dir: Path, date: str | None, training: dict | None) -> int:
    missing = [s for s in SOURCES.values() if not (round_dir / s).is_file()]
    if missing:
        print("not there yet: " + ", ".join(missing))
        return 1
    files = []
    for name, src in SOURCES.items():
        dest = ev / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(round_dir / src, dest)
        files.append({"file": name, "bytes": dest.stat().st_size, "sha256": sha256(dest)})
    (ev / "MANIFEST.json").write_text(
        dump({"what": "Round 3 tool-calling exams: scored answers and the exams, copied unchanged", "files": files}),
        encoding="utf-8",
        newline="\n",
    )
    res = build(ev, date, training)
    (ev / "results.json").write_text(dump(res), encoding="utf-8", newline="\n")
    print(f"wrote {ev / 'results.json'}; headline variant {res['headline']['variant']}: {res['headline']['reason']}")
    return 0


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _first_obj(text: str):
    s = text.find("{")
    if s < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[s:])[0]
    except Exception:
        return None


def regrade(scorefn, expected, text: str | None) -> bool:
    """The strict rule score_all.py used: the whole reply must be one JSON value the scorer accepts."""
    try:
        parsed = json.loads((text or "").strip())
    except Exception:
        return False
    return bool(scorefn(expected, parsed)[0])


def check(ev: Path) -> int:
    res_path = ev / "results.json"
    if not res_path.is_file():
        print("pending: no round 3 results yet (evidence/toolcall/results.json is not there)")
        return 0
    problems: list[str] = []
    man = jload(ev / "MANIFEST.json")
    for f in man["files"]:
        p = ev / f["file"]
        if not p.is_file() or sha256(p) != f["sha256"]:
            problems.append(f"hash differs: {f['file']}")
    if problems:
        print("\n".join(problems))
        return 1
    if res_path.read_text(encoding="utf-8") != dump(_with_extras(build(ev), jload(res_path))):
        problems.append("results.json differs from what the saved answers give: run scripts/toolcall_results.py --collect")
    for exam, spec in EXAMS.items():
        mod = _load_module(ev / "scorers" / f"{spec['scorer']}.py", f"tc_{spec['scorer']}")
        expected = {r["id"]: r["expected"] for r in jsonl(ev / spec["exam"])}
        for model in ("base", "adapter", "ultra"):
            if (exam, model) not in SCORED:
                continue
            bad = sum(
                1
                for r in scored_rows(ev, exam, model)
                if r.get("answered") and regrade(mod.score, expected[r["id"]], r.get("raw")) != bool(r["ok"])
            )
            if bad:
                problems.append(f"{exam} {model}: {bad} verdicts do not follow from the saved answers")
    print("\n".join(problems) if problems else "OK: results.json, the hashes and every saved verdict match the answers")
    return 1 if problems else 0


def _with_extras(fresh: dict, stored: dict) -> dict:
    """date and training are inputs of --collect, not derived from the answers: keep what was stored."""
    for k in ("date", "training"):
        if k in stored:
            fresh[k] = stored[k]
    return fresh


# ---------------------------------------------------------------- render


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def rng(s: dict) -> str:
    return f"{s['lo'] * 100:.1f} to {s['hi'] * 100:.1f}%"


def cell(s: dict) -> str:
    return f"{s['agree']} of {s['n']} ({pct(s['rate'])}, 95% range {rng(s)})"


def phrase(v: str, a: dict, b: dict, other: str) -> str:
    """Honest wording for adapter `a` against `b`, chosen from the ranges."""
    if v == "above":
        return f"above {other}: {pct(a['rate'])} against {pct(b['rate'])}, and the 95% ranges do not overlap"
    if v == "below":
        return f"below {other}: {pct(a['rate'])} against {pct(b['rate'])}, and the 95% ranges do not overlap"
    counts = f"{pct(a['rate'])} against {pct(b['rate'])} ({a['agree']}/{a['n']} against {b['agree']}/{b['n']}"
    if v in ("higher", "lower"):
        return f"{v} than {other}: {counts}; the 95% ranges overlap only slightly, so part of the gap could be chance)"
    side = "higher" if a["rate"] > b["rate"] else "lower" if a["rate"] < b["rate"] else "the same"
    return f"about the same as {other}: {pct(a['rate'])} against {pct(b['rate'])} ({side} by point estimate, but the 95% ranges overlap, so the exam cannot tell them apart)"


def paired_line(p: dict) -> str:
    return f"question by question against the untrained model, the adapter fixed {p['fixed']} and broke {p['broke']} (exact sign test {'p < 0.0001' if p['p'] < 0.0001 else 'p = ' + str(p['p'])})"


CAVEATS = (
    "Free-text arguments are a known weak spot in the training data: the strict scorer cannot agree on free text, so tools such as "
    "create_ticket (15 training rows), search_files, send_payment and search_inbox are thin. The hard exam uses 20 tools that were never in training."
)
SETTING = (
    "The adapter was trained on a borrowed GPU, on synthetic data. "
    "The hard exam (150 questions, 20 new tools) was written by Claude Sonnet 5.5 for evaluation only and is never training data; "
    "the second exam (300 questions) is drawn from the Berkeley Function Calling Leaderboard (Apache-2.0)."
)


def sentences(r: dict) -> dict[str, str]:
    s, c = r["scores"], r["comparisons"]
    out = {}
    for e in EXAMS:
        out[e] = (
            f"On the {EXAM_LABEL[e].replace('Hard', 'hard')} the adapter is {phrase(c[e]['adapter_vs_base'], s[e]['adapter'], s[e]['base'], 'the untrained model')}. "
            f"It is {phrase(c[e]['adapter_vs_ultra'], s[e]['adapter'], s[e]['ultra'], 'the teacher')}."
        )
    return out


def table(r: dict) -> str:
    s = r["scores"]
    lines = ["| | Hard exam (150) | BFCL exam (300) |", "|---|---|---|"]
    for m in ("base", "ultra", "adapter"):
        lines.append(f"| {MODEL_LABEL[m]} | {cell(s['hard'][m])} | {cell(s['bfcl'][m])} |")
    return "\n".join(lines)


def training_line(r: dict) -> str:
    t = r.get("training") or {}
    bits = []
    if "rows" in t:
        bits.append(f"{t['rows']:,} training rows")
    if "cost_usd" in t:
        bits.append(f"about ${t['cost_usd']:g} in all")
    return (" (" + ", ".join(bits) + ")") if bits else ""


def render(r: dict) -> dict[str, str]:
    se = sentences(r)
    p = {e: r["paired"][e]["adapter_vs_base"] for e in EXAMS}
    when = r.get("date", "")
    body = (
        f"{se['hard']}\n\n{se['bfcl']}\n\n"
        f"Hard exam: {paired_line(p['hard'])}. BFCL exam: {paired_line(p['bfcl'])}.\n\n"
        f"Strict scoring: an answer counts only if it is one JSON object with the right tool and arguments; a reply with text around the JSON is wrong. {CAVEATS} {SETTING}"
    )
    readme = (
        "### Tool calling (round 3)\n\n"
        f"A tool-calling adapter on Nemotron 3 Nano 4B{training_line(r)}, scored strictly on two exams it never saw.\n\n"
        f"{table(r)}\n\n{body}\n\n"
        "Every score recomputes from the saved answers: see [Check it yourself](docs/VERIFY.md#tool-calling-round-3).\n"
    )
    docs = (
        "## Tool calling, round 3\n\n"
        f"A tool-calling adapter on Nemotron 3 Nano 4B{training_line(r)}, scored strictly on two exams. These are measured results on real models, not the simulation"
        + (f" ({when})" if when else "")
        + ".\n\n"
        f"{table(r)}\n\n{body}\n\nEvery number comes from `evidence/toolcall/results.json`, which `python scripts/toolcall_results.py --check` recomputes from the saved answers.\n"
    )
    row = (
        f"| Tool-calling adapter, scored strictly on two exams (150 questions written by Claude Sonnet 5.5 for evaluation, 300 from the Berkeley Function Calling Leaderboard) "
        f"| **Measured**: adapter {pct(r['scores']['hard']['adapter']['rate'])} and {pct(r['scores']['bfcl']['adapter']['rate'])}, "
        f"untrained model {pct(r['scores']['hard']['base']['rate'])} and {pct(r['scores']['bfcl']['base']['rate'])}, teacher {pct(r['scores']['hard']['ultra']['rate'])} and {pct(r['scores']['bfcl']['ultra']['rate'])}. Trained on a borrowed GPU |"
    )
    devpost = (
        "# Devpost round 3 snippet\n\n"
        f"## 'What is real' table, add this row\n\n{row}\n\n## 'Results' section, add after the text-to-SQL table\n\n"
        "A second task, tool calling: the same recipe (a small adapter on Nemotron 3 Nano 4B, answers written by Nemotron 3 Ultra) scored strictly on two exams.\n\n"
        f"{table(r)}\n\n{body}\n"
    )
    notion = (
        "Tool-calling round 3 (measured"
        + (f", {when}" if when else "")
        + f"): hard exam, adapter {cell(r['scores']['hard']['adapter'])}, untrained {cell(r['scores']['hard']['base'])}, teacher {cell(r['scores']['hard']['ultra'])}. "
        f"BFCL exam, adapter {cell(r['scores']['bfcl']['adapter'])}, untrained {cell(r['scores']['bfcl']['base'])}, teacher {cell(r['scores']['bfcl']['ultra'])}. "
        f"{se['hard']} {se['bfcl']} Trained on a borrowed GPU; synthetic data. Evidence: evidence/toolcall/results.json.\n"
    )
    hf = (
        "## Collection note\n\nTool calling: a second adapter, trained with the same recipe as the text-to-SQL one. "
        f"On a 150-question hard exam it scores {pct(r['scores']['hard']['adapter']['rate'])} (95% range {rng(r['scores']['hard']['adapter'])}) against "
        f"{pct(r['scores']['hard']['base']['rate'])} for the untrained model; on 300 BFCL questions {pct(r['scores']['bfcl']['adapter']['rate'])} "
        f"(95% range {rng(r['scores']['bfcl']['adapter'])}) against {pct(r['scores']['bfcl']['base']['rate'])}. "
        f"{se['hard']} {se['bfcl']} Strict scoring, trained on a borrowed GPU, synthetic data.\n\n"
        "## Model card link line\n\n"
        "Scores, exams and every saved answer: https://github.com/harshbhx/ratchet/tree/main/evidence/toolcall (recompute with `python scripts/toolcall_results.py --check`).\n"
    )
    verify = (
        "## Tool-calling round 3\n\n"
        "Every claim below recomputes from files in `evidence/toolcall/`. Nothing here needs a GPU, a key or a network connection.\n\n"
        "| Claim | One command |\n|---|---|\n"
        "| The scores, ranges and wording verdicts follow from the saved answers, and every saved verdict follows from its raw answer | `python scripts/toolcall_results.py --check` |\n"
        f"| The hard exam is the frozen one | `python -c \"import hashlib;print(hashlib.sha256(open('evidence/toolcall/exams/exam_hard.jsonl','rb').read()).hexdigest())\"` prints `{r['exams']['hard']['sha256']}` |\n"
        f"| The BFCL exam is the frozen one | `python -c \"import hashlib;print(hashlib.sha256(open('evidence/toolcall/exams/exam_bfcl.jsonl','rb').read()).hexdigest())\"` prints `{r['exams']['bfcl']['sha256']}` |\n"
        "| The copied evidence files are unchanged | `python scripts/toolcall_results.py --check` (compares each file's SHA-256 with `evidence/toolcall/MANIFEST.json`) |\n"
        "| The drafts and the page data are the ones the numbers give | `python scripts/toolcall_results.py --render --web --check-render` |\n\n"
        "**It shows:** the published numbers are the saved answers' numbers, under the exam's own strict scorer. "
        "**It does not show:** that the adapter produced those answers on the borrowed GPU as described: that rests on the training and generation logs kept with the run. A 95% range on 150 or 300 questions is wide, which is why the wording says 'about the same' only when the ranges overlap and the gap is under 5 points, and 'lower' or 'higher' with a chance caveat when the ranges overlap and the gap is larger.\n"
    )
    real_entry = {
        "id": "toolcall-round3",
        "claim": "How well a tool-calling adapter does on two held-out exams",
        "status": "measured",
        "evidence": f"{se['hard']} {se['bfcl']} Strict scoring. {CAVEATS} {SETTING}",
        "check": {
            "label": "Recompute the scores from the saved answers",
            "command": "python scripts/toolcall_results.py --check",
        },
        "paths": ["scripts/toolcall_results.py", "evidence/toolcall/results.json"],
        "date": when or "",
        "facts": [
            {
                "label": f"{EXAM_LABEL[e]}, {MODEL_LABEL[m][:1].lower()}{MODEL_LABEL[m][1:]}",  # product names keep their capitals
                "value": f"{r['scores'][e][m]['agree']} of {r['scores'][e][m]['n']}",
            }
            for e in EXAMS
            for m in ("base", "ultra", "adapter")
        ],
        "metrics": [
            {
                "id": "accuracy",
                "label": "Accuracy (strict)",
                "value": f"{pct(r['scores']['hard']['adapter']['rate'])} on the hard exam and "
                f"{pct(r['scores']['bfcl']['adapter']['rate'])} on BFCL (untrained model "
                f"{pct(r['scores']['hard']['base']['rate'])} and {pct(r['scores']['bfcl']['base']['rate'])})",
            }
        ],
    }
    return {
        "README_block.md": readme,
        "docs_results_section.mdx": docs,
        "DEVPOST_ROUND3_SNIPPET.md": devpost,
        "NOTION_ROUND3.md": notion,
        "HF_NOTE.md": hf,
        "VERIFY_round3.md": verify,
        "real_entry.json": dump(real_entry),
    }


def web_data(r: dict) -> str:
    """web/src/data/toolcall.json: numbers and ready-made sentences; the page decides nothing the script did not."""
    se = sentences(r)
    data = {
        "variant": r["headline"]["variant"],
        "variant_reason": r["headline"]["reason"],
        "date": r.get("date", ""),
        "exams": {
            e: {
                "label": EXAM_LABEL[e],
                "rows": r["exams"][e]["rows"],
                "scores": r["scores"][e],
                "verdicts": r["comparisons"][e],
                "sentence": se[e],
                "paired": r["paired"][e]["adapter_vs_base"],
            }
            for e in EXAMS
        },
        "caveat": CAVEATS,
        "setting": SETTING,
    }
    return dump(data)


NOTE_FILES = ("DEVPOST_ROUND3_SNIPPET.md", "NOTION_ROUND3.md", "HF_NOTE.md")  # paste-ready notes: written only with --notes
DEFAULT_NOTES = ROOT / "out" / "toolcall-notes"  # out/ is gitignored


def write_render(r: dict, out: Path, web: bool, notes: Path | None = None) -> dict[str, str]:
    files = render(r)
    if notes is None:
        for name in NOTE_FILES:
            files.pop(name)
    if web:
        files["__web__"] = web_data(r)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    ap.add_argument("--round-dir", type=Path, default=DEFAULT_ROUND)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--web", action="store_true", help="with --render: also write web/src/data/toolcall.json")
    ap.add_argument("--check-render", action="store_true", help="with --render: compare the drafts on disk instead of writing")
    ap.add_argument("--out", type=Path, default=None, help="render folder (default: <evidence>/rendered)")
    ap.add_argument(
        "--notes",
        type=Path,
        nargs="?",
        const=DEFAULT_NOTES,
        default=None,
        help="with --render: also write the Devpost, Notion and Hugging Face notes (default folder: out/toolcall-notes, not committed)",
    )
    ap.add_argument("--web-out", type=Path, default=ROOT / "web" / "src" / "data" / "toolcall.json")
    ap.add_argument("--date", default=None, help="with --collect: the measurement date, YYYY-MM-DD, kept in results.json")
    ap.add_argument("--train-rows", type=int, default=None, help="with --collect: training rows, kept in results.json")
    ap.add_argument("--cost-usd", type=float, default=None, help="with --collect: total cost in US dollars, kept in results.json")
    a = ap.parse_args(argv)
    if a.collect:
        training = {k: v for k, v in (("rows", a.train_rows), ("cost_usd", a.cost_usd)) if v is not None} or None
        return collect(a.evidence, a.round_dir, a.date, training)
    if a.check:
        return check(a.evidence)
    if a.render:
        res_path = a.evidence / "results.json"
        if not res_path.is_file():
            print("pending: no results.json yet, nothing to render")
            return 1
        r = jload(res_path)
        out = a.out or a.evidence / "rendered"
        files = write_render(r, out, a.web, a.notes)
        stale = []
        for name, text in files.items():
            dest = a.web_out if name == "__web__" else (a.notes if name in NOTE_FILES else out) / name
            if a.check_render:
                if not dest.is_file() or dest.read_text(encoding="utf-8") != text:
                    stale.append(str(dest))
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8", newline="\n")
                print("wrote", dest)
        if a.check_render:
            print("OK: the drafts match results.json" if not stale else "stale: " + ", ".join(stale))
            return 1 if stale else 0
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
