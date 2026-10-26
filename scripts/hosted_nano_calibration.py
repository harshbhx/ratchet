"""Calibrate the hosted, untuned Nemotron 3 Nano 30B on Token Factory as a stand-in student for Scout's two distillable
tasks (FR-12.33, ADR 0146), from real calls, the way a night scores a version.

    python scripts/hosted_nano_calibration.py collect --max-usd 0.40   # real Token Factory calls (cents); needs NEBIUS_API_KEY
    python scripts/hosted_nano_calibration.py check                    # offline: recompute, compare with ratchet/hosted.py
    python scripts/hosted_nano_calibration.py show                     # offline: print the records and the numbers

`collect` rebuilds the triage prompts of the three recorded live Scout runs (`evidence/scout/`) exactly, for every pair of
a recorded question and a recorded search result (3 x 33). Ultra labels each pair as an audit would (the audit role's
parameters: temperature 0); the pairs Ultra calls relevant also become reader prompts, with the result's recorded
snippet (up to 500 characters) as the excerpt, which Ultra labels too. The hosted Nano answers every prompt the way the
gateway samples a student (`RATCHET_SAMPLES` 3 choices in one call, temperature 0.7, token logprobs, thinking off). Both
answers are saved with their token counts in `evidence/hosted-nano/rows.jsonl`; the key is read from the environment
and never written.

`check` and `show` replay the saved samples through `nightschool.evaluate.cascade_eval`, the golden eval a night runs
(even rows calibrate, odd rows measure; the label is Ultra's answer, compared field by field as an audit compares), and
apply the stand-in's own rule: a task is served only when its held-out precision is at least `hosted.MIN_PRECISION`.
Nothing in `check` uses the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ratchet import hosted, reasoning, tasks  # noqa: E402
from ratchet.confidence import STUDENT_SAMPLING  # noqa: E402
from ratchet.config import DEFAULT_PRICES, Settings  # noqa: E402
from ratchet.nightschool.evaluate import cascade_eval  # noqa: E402
from ratchet.providers import Completion  # noqa: E402
from ratchet.router import answer_problem  # noqa: E402
from ratchet.scout.prompts import read_messages, triage_messages  # noqa: E402

RECORDINGS = ROOT / "evidence" / "scout"
EVIDENCE = ROOT / "evidence" / "hosted-nano"
SCHEMA = "ratchet.hosted-nano/v1"
TRIAGE, READ = "source-triage", "page-read"


# ---------- the prompts ----------
def recorded_runs(folder: Path = RECORDINGS) -> list[dict[str, Any]]:
    """Each recorded run's question, its search results by id and Ultra's recorded triage labels by result id."""
    runs = []
    for path in sorted(folder.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        hits = {r["id"]: r for e in run["events"] if e.get("agent") == "searcher" for r in e["detail"]["results"]}
        triage = {e["detail"]["id"]: e["detail"] for e in run["events"] if e.get("agent") == "triage"}
        runs.append({"file": path.name, "question": run["question"], "hits": hits, "triage": triage})
    return runs


def triage_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One triage prompt per (recorded question, recorded result) pair. `own` marks the pairs Scout itself triaged, whose
    recorded Ultra label (served, not audited) is kept beside the new one."""
    rows = []
    for qi, q in enumerate(runs):
        for ri, r in enumerate(runs):
            for hid, h in sorted(r["hits"].items()):
                own = qi == ri
                rec = r["triage"].get(hid) if own else None
                rows.append(
                    {
                        "id": f"t:{qi}:{ri}:{hid}",
                        "task": TRIAGE,
                        "question": q["question"],
                        "url": h["url"],
                        "own": own,
                        "prompt": triage_messages(q["question"], h["title"], h["url"], h["snippet"]),
                        "recorded": {k: rec[k] for k in ("relevant", "source_type")} if rec else None,
                    }
                )
    return rows


def read_rows(runs: list[dict[str, Any]], triaged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A reader prompt for each pair Ultra's new label calls relevant, as Scout reads only the results triage passed."""
    rows = []
    for t in triaged:
        obj = tasks.parse_json(t.get("reference") or "") or {}
        if obj.get("relevant") is not True:
            continue
        _, qi, ri, hid = t["id"].split(":")
        q, h = runs[int(qi)], runs[int(ri)]["hits"][int(hid)]
        rows.append(
            {
                "id": f"r:{qi}:{ri}:{hid}",
                "task": READ,
                "question": q["question"],
                "url": h["url"],
                "own": qi == ri,
                "prompt": read_messages(q["question"], h["title"], h["url"], h["snippet"]),
                "recorded": None,
            }
        )
    return rows


# ---------- collect (real calls) ----------
class Spend:
    """List-price spend of this run's calls, with a hard cap checked before each call."""

    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self.usd: dict[str, float] = {}
        self.lock = threading.Lock()

    def total(self) -> float:
        return sum(self.usd.values())

    def reserve(self, estimate: float) -> None:
        with self.lock:
            if self.total() + estimate > self.cap:
                spent, est = self.total(), estimate
                raise SystemExit(f"stopped: ${spent:.4f} spent, the next call (about ${est:.4f}) would pass --max-usd")

    def add(self, model: str, in_tokens: int, out_tokens: int) -> None:
        p = DEFAULT_PRICES[model]
        with self.lock:
            self.usd[model] = self.usd.get(model, 0.0) + p.cost(in_tokens, out_tokens)


def _sample_json(c: Completion) -> dict[str, Any]:
    return {"text": c.text, "finish_reason": c.finish_reason, "token_logprobs": c.token_logprobs, "out": c.out_tokens}


def collect(out: Path, max_usd: float, workers: int = 4) -> None:
    from ratchet.providers import OpenAICompatProvider

    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("NEBIUS_API_KEY is not set")
    s = Settings()
    if s.samples != hosted.SAMPLES:
        raise SystemExit(f"RATCHET_SAMPLES must be {hosted.SAMPLES} (the gateway's default) to calibrate")
    plan = s.gen_policy().plan(None, 1)
    student = replace(plan.student, template_kwargs=reasoning.template_kwargs(s.reasoning_policy().fallback_student))
    audit = plan.check  # the audit and arbitration role (temperature 0)
    provider = OpenAICompatProvider(s.teacher_base, key, timeout=120.0, reasoning_field=s.reasoning_policy().field)
    spend = Spend(max_usd)

    def label_and_sample(row: dict[str, Any]) -> dict[str, Any]:
        spend.reserve(0.004)  # an Ultra call with reasoning, generously
        t = provider.complete(row["prompt"], s.teacher_model, n=1, temperature=0.0, params=audit)[0]
        spend.add(s.teacher_model, t.in_tokens, t.out_tokens)
        task = tasks.get(row["task"])
        ok = answer_problem(task, t) is None  # what an audit counts and stores (INV-16)
        spend.reserve(0.0005)
        got = provider.complete(row["prompt"], s.fallback_student_model, n=hosted.SAMPLES, params=student, **STUDENT_SAMPLING)
        spend.add(s.fallback_student_model, got[0].in_tokens, sum(c.out_tokens for c in got))
        return {
            **row,
            "reference": tasks.clean_answer(task, t.text) if ok else None,
            "teacher": {
                "model": t.model,
                "finish_reason": t.finish_reason,
                "in": t.in_tokens,
                "out": t.out_tokens,
                "reasoning": t.reasoning_tokens,
                "usd": DEFAULT_PRICES[s.teacher_model].cost(t.in_tokens, t.out_tokens),
            },
            "student": {
                "model": got[0].model,
                "in": got[0].in_tokens,
                "usd": DEFAULT_PRICES[s.fallback_student_model].cost(got[0].in_tokens, sum(c.out_tokens for c in got)),
                "samples": [_sample_json(c) for c in got],
            },
        }

    runs = recorded_runs()
    with ThreadPoolExecutor(workers) as pool:
        triaged = list(pool.map(label_and_sample, triage_rows(runs)))
        read = list(pool.map(label_and_sample, read_rows(runs, triaged)))
    out.mkdir(parents=True, exist_ok=True)
    rows = triaged + read
    with (out / "rows.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    meta = {
        "schema": SCHEMA,
        "collected": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "teacher_model": s.teacher_model,
        "student_model": s.fallback_student_model,
        "student_call": {**student.to_body(None), **dict(STUDENT_SAMPLING), "n": hosted.SAMPLES, **student.template_kwargs},
        "label_call": {**audit.to_body(None), "temperature": 0.0},
        "reasoning_field": s.reasoning_policy().field,
        "rows": {TRIAGE: len(triaged), READ: len(read)},
        "spend_usd": {m: round(v, 6) for m, v in spend.usd.items()},
    }
    (out / "collect.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(meta, indent=2))


# ---------- calibrate (offline) ----------
def load(folder: Path = EVIDENCE) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (folder / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def _order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A fixed order that mixes own and cross pairs and both tasks: by a hash of the row id. Even positions calibrate,
    odd ones measure (cascade_eval)."""
    return sorted(rows, key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`cascade_eval` over the saved rows, replaying the saved samples: the eval a night would write for the hosted
    Nano on these golden rows (target precision, floor and the per-task minimum at their defaults)."""
    rows = _order(rows)
    by_prompt = {
        json.dumps(r["prompt"], sort_keys=True): [
            Completion(
                text=x["text"],
                model=r["student"]["model"],
                in_tokens=r["student"]["in"],
                out_tokens=x["out"],
                token_logprobs=list(x["token_logprobs"]),
                finish_reason=x["finish_reason"],
            )
            for x in r["student"]["samples"]
        ]
        for r in rows
    }

    def sample(prompt: list[dict[str, Any]], n: int, params: Any = None) -> list[Completion]:
        return by_prompt[json.dumps(prompt, sort_keys=True)][:n]

    golden = [
        {"task": r["task"], "messages": [*r["prompt"], {"role": "assistant", "content": r["reference"] or ""}]} for r in rows
    ]
    return cascade_eval(sample, golden, samples=hosted.SAMPLES)


def records(ev: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The stand-in's per-task records: cascade_eval's, with the task served only when its held-out precision reaches
    `hosted.MIN_PRECISION` on at least one served row (`hosted.gate`)."""
    return {name: hosted.gate(rec) for name, rec in sorted(ev["tasks"].items())}


def costs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Measured per-call costs and Ultra's reasoning tokens by task, from the saved token counts at list prices."""
    out: dict[str, Any] = {}
    for name in (TRIAGE, READ):
        rs = [r for r in rows if r["task"] == name]
        if not rs:
            continue
        reasoning_tokens = [r["teacher"]["reasoning"] for r in rs]
        out[name] = {
            "n": len(rs),
            "ultra_usd_mean": statistics.fmean(r["teacher"]["usd"] for r in rs),
            "nano_usd_mean": statistics.fmean(r["student"]["usd"] for r in rs),
            "ultra_reasoning_mean": statistics.fmean(reasoning_tokens),
            "ultra_reasoning_p50": statistics.median(reasoning_tokens),
            "ultra_out_mean": statistics.fmean(r["teacher"]["out"] for r in rs),
            "ultra_in_mean": statistics.fmean(r["teacher"]["in"] for r in rs),
        }
    return out


def agreement_with_recorded(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How often Ultra's new (audit) label agrees with its recorded (served) one on the pairs Scout triaged itself."""
    task = tasks.get(TRIAGE)
    pairs = [r for r in rows if r["task"] == TRIAGE and r["recorded"] and r["reference"]]
    same = sum(task.agree(json.dumps(r["recorded"]), r["reference"]) for r in pairs)
    return {"n": len(pairs), "agree": int(same)}


def check(folder: Path = EVIDENCE) -> int:
    rows = load(folder)
    got = records(evaluate(rows))
    want = {name: dict(rec) for name, rec in hosted.TASKS.items()}
    if got != want:
        print("ratchet/hosted.py TASKS does not follow from the evidence:")
        print(json.dumps({"evidence": got, "code": want}, indent=2, sort_keys=True))
        return 1
    print(f"ok: {len(rows)} rows, records match ratchet/hosted.py for {', '.join(sorted(got))}")
    return 0


def show(folder: Path = EVIDENCE) -> None:
    rows = load(folder)
    ev = evaluate(rows)
    report = {
        "records": records(ev),
        "pooled": {k: ev[k] for k in ("golden_n", "golden_agreement", "threshold", "coverage", "precision", "test_n")},
        "golden_invalid_refs": ev["golden_invalid_refs"],
        "costs": costs(rows),
        "ultra_recorded_vs_audit": agreement_with_recorded(rows),
        "distinct_results": len({r["url"] for r in rows}),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="real calls: label with Ultra, sample the hosted Nano (cents)")
    c.add_argument("--out", type=Path, default=EVIDENCE)
    c.add_argument("--max-usd", type=float, required=True)
    for name in ("check", "show"):
        p = sub.add_parser(name)
        p.add_argument("--dir", type=Path, default=EVIDENCE)
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        collect(args.out, args.max_usd)
        return 0
    if args.cmd == "show":
        show(args.dir)
        return 0
    return check(args.dir)


if __name__ == "__main__":
    sys.exit(main())
