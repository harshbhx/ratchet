"""Scores the round-2 recipe retrained on Nebius AI Cloud, on both exams, with the same scoring code as round 2.

    python evidence/text2sql-round2-nebius/score_nebius.py report   # writes results/results_nebius.json
    python evidence/text2sql-round2-nebius/score_nebius.py verify   # recomputes it and compares

An answer is right when running it returns the same rows as the exam's reference query on the same database
(scripts/measure_adapter.py: sql_same). Every number in results_nebius.json comes from this script; none is typed in.
The base model's answers are not part of this evidence, so the base-model keys that measure_adapter.build_report
produces are left out (they do not enter any adapter number; the script checks that). Standard library plus measure_adapter.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("measure_adapter", ROOT / "scripts" / "measure_adapter.py")
m = importlib.util.module_from_spec(_spec)
sys.modules["measure_adapter"] = m
_spec.loader.exec_module(m)

EXAMS = {
    "exam1": {
        "rows": 172,
        "golden": ROOT / "evidence" / "text2sql-round1" / "data" / "golden.jsonl",
        "dbs": ROOT / "evidence" / "text2sql-round1" / "data" / "dbs.json",
        "nebius": HERE / "answers" / "raw_exam1_nebius.jsonl",
        "original": ROOT / "evidence" / "text2sql-round2" / "answers" / "raw_r2.jsonl",
    },
    "exam2": {
        "rows": 200,
        "golden": ROOT / "evidence" / "text2sql-exam2" / "data" / "golden.jsonl",
        "dbs": ROOT / "evidence" / "text2sql-exam2" / "data" / "dbs.json",
        "nebius": HERE / "answers" / "raw_exam2_nebius.jsonl",
        "original": ROOT / "evidence" / "text2sql-exam2" / "answers" / "raw_v2.jsonl",
    },
}
OUT = HERE / "results" / "results_nebius.json"
DIFFICULTIES = ["simple", "join", "aggregate", "hard"]
ARGS = argparse.Namespace(target_precision=0.95, teacher_in_per_m=1.0, teacher_out_per_m=3.0, student_per_m=0.06)


def flags(gold, dbs, rows):
    conns, out = {}, [False] * len(gold)
    for r in rows:
        g = gold[r["i"]]
        db = g["meta"]["db"]
        if db not in conns:
            conns[db] = m.sql_connect(dbs[db])
        out[r["i"]] = bool(m.sql_same(conns[db], g["completion"][0]["content"], r["adapter"]["text"]))
    return out


def two_sided(up, down):
    n, k = up + down, min(up, down)
    return round(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n), 3)


def rate(k, n):
    lo, hi = m.wilson(k, n)
    return {"agree": k, "n": n, "rate": round(k / n, 4), "wilson95": [round(lo, 4), round(hi, 4)]}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build():
    res = {"what": "Round-2 recipe retrained on Nebius AI Cloud (one preemptible L40S), adapter answers only", "exams": {}}
    for name, e in EXAMS.items():
        gold, dbs = m.load_jsonl(e["golden"]), json.loads(Path(e["dbs"]).read_text(encoding="utf-8"))
        neb, orig = m.load_jsonl(e["nebius"]), m.load_jsonl(e["original"])
        assert len(neb) == len(orig) == len(gold) == e["rows"], name
        assert [r["i"] for r in neb] == list(range(e["rows"]))
        assert all(set(r) == {"i", "teacher", "adapter"} for r in neb), "answers file must hold adapter answers only"
        sn, so = flags(gold, dbs, neb), flags(gold, dbs, orig)
        n = e["rows"]
        up = sum(1 for a, b in zip(so, sn, strict=True) if not a and b)
        down = sum(1 for a, b in zip(so, sn, strict=True) if a and not b)
        both = sum(1 for a, b in zip(so, sn, strict=True) if a and b)
        diff = {}
        for d in DIFFICULTIES:
            idx = [i for i, g in enumerate(gold) if g["meta"]["difficulty"] == d]
            diff[d] = {"n": len(idx), "nebius": sum(sn[i] for i in idx), "original_round2": sum(so[i] for i in idx)}
        seen = {}
        for val, label in ((True, "seen_industries"), (False, "unseen_industries")):
            idx = [i for i, g in enumerate(gold) if g["meta"].get("domain_seen") is val]
            if idx:
                seen[label] = {"n": len(idx), "nebius": sum(sn[i] for i in idx), "original_round2": sum(so[i] for i in idx)}
        # The measurement half and the confidence gate, with build_report. Its base keys are dropped: a stand-in base (a copy of the  # noqa: E501
        # adapter's answer) is passed only because the function wants the field; the check below shows no adapter number depends on it.  # noqa: E501
        stand_in = [{**r, "base": r["adapter"]} for r in neb]
        other = [{**r, "base": {"text": "", "mean_prob": 0.0, "min_prob": 0.0, "tokens_in": 0, "tokens_out": 0}} for r in neb]
        rep = m.build_report(stand_in, ARGS, gold, dbs)
        rep2 = m.build_report(other, ARGS, gold, dbs)
        strip = lambda d: {k: v for k, v in d.items() if k != "base_model_agreement" and k != "breakdown_answering_every_row"}  # noqa: E731
        assert json.loads(json.dumps(strip(rep))) == json.loads(json.dumps(strip(rep2))), (
            "an adapter number depends on the base field"
        )
        half = {
            k: rep[k]
            for k in (
                "rows",
                "adapter_agreement",
                "confidence_threshold",
                "target_precision",
                "coverage",
                "precision_when_answered",
            )
        }
        res["exams"][name] = {
            "nebius_retrain": rate(sum(sn), n),
            "original_round2_borrowed_gpu": rate(sum(so), n),
            "row_by_row_vs_original": {
                "wrong_to_right": up,
                "right_to_wrong": down,
                "both_right": both,
                "both_wrong": n - up - down - both,
                "p_exact_two_sided": two_sided(up, down),
            },
            "by_difficulty": diff,
            "by_industry": seen,
            "measurement_half": half,
            "answers_identical_to_original_adapter": sum(
                a["adapter"]["text"] == b["adapter"]["text"] for a, b in zip(neb, orig, strict=True)
            ),
            "inputs_sha256": {
                "answers": sha(e["nebius"]),
                "golden": sha(e["golden"]),
                "dbs": sha(e["dbs"]),
                "original_answers": sha(e["original"]),
            },
        }
    res["adapter_weights_sha256"] = (HERE / "adapter" / "adapter_sha256.txt").read_text().split()[0]
    return json.loads(json.dumps(res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report", "verify"])
    cmd = ap.parse_args().cmd
    res = build()
    if cmd == "report":
        OUT.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
        for k, v in res["exams"].items():
            print(
                k,
                v["nebius_retrain"],
                "original",
                v["original_round2_borrowed_gpu"]["agree"],
                "/",
                v["original_round2_borrowed_gpu"]["n"],
            )
        return 0
    saved = json.loads(OUT.read_text(encoding="utf-8"))
    if saved != res:
        bad = [k for k in res if saved.get(k) != res[k]]
        print("FAIL: recomputed numbers differ in", bad)
        return 1
    print("OK: every number in results_nebius.json was recomputed from the answers and matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
