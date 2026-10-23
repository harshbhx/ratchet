"""Ask the teacher (Nemotron 3 Ultra on Nebius Token Factory) the 172 exam prompts again and score its answers the way the exam
scores the student: run its SQL and the stored SQL on the same database and compare the rows.

This is the ceiling for the exam: how often the teacher, answering the exam prompt, returns the same rows as the label stored for
that question. The stored label was written together with the question, so this is not the same call twice.

The key comes from the NEBIUS_API_KEY variable, is never printed or written, and the run stops when the estimated spend reaches
--max-usd. Writes teacher2.jsonl (the new answers) and teacher_selfcheck.json (the number)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = os.environ.get("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
MODEL = os.environ.get("RATCHET_TEACHER_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
PRICE_IN, PRICE_OUT = 1.0, 3.0  # USD per million tokens (Token Factory catalog, 22 Oct 2026)

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("m", HERE.parent / "measure_adapter.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parent.parent / "evidence" / "text2sql-round1" / "data"))
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    key = os.environ.get("NEBIUS_API_KEY", "")
    if not key:
        sys.exit("NEBIUS_API_KEY is not set")
    out = Path(a.out)
    gold = [json.loads(line) for line in open(out / "golden.jsonl", encoding="utf-8") if line.strip()]
    dbs = json.load(open(out / "dbs.json", encoding="utf-8"))
    spent = {"in": 0, "out": 0, "stop": False}
    lock = threading.Lock()
    # Resume: questions already answered in teacher2.jsonl are kept and not asked (or paid for) again.
    have: dict[int, str] = {}
    prior = out / "teacher2.jsonl"
    if prior.exists():
        for line in open(prior, encoding="utf-8"):
            if line.strip():
                r0 = json.loads(line)
                if r0.get("text") is not None:
                    have[r0["i"]] = r0["text"]
    print(f"{len(have)} answers already saved; asking the other {len(gold) - len(have)}")

    def ask(i: int) -> dict:
        if i in have:
            return {"i": i, "text": have[i], "error": None}
        with lock:
            usd = (spent["in"] * PRICE_IN + spent["out"] * PRICE_OUT) / 1e6
            if usd >= a.max_usd:
                spent["stop"] = True
                return {"i": i, "text": None, "error": "cap"}
        body = json.dumps(
            {
                "model": MODEL,
                "messages": gold[i]["prompt"],
                "temperature": 0,
                "max_tokens": 600,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        ).encode()
        req = urllib.request.Request(
            f"{BASE}/chat/completions", body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    d = json.load(r)
                u = d.get("usage") or {}
                with lock:
                    spent["in"] += int(u.get("prompt_tokens", 0))
                    spent["out"] += int(u.get("completion_tokens", 0))
                return {"i": i, "text": d["choices"][0]["message"]["content"], "error": None}
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    return {"i": i, "text": None, "error": type(e).__name__}
                time.sleep(2 * (attempt + 1))
        return {"i": i, "text": None, "error": "unreachable"}

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        answers = list(ex.map(ask, range(len(gold))))
    usd = (spent["in"] * PRICE_IN + spent["out"] * PRICE_OUT) / 1e6
    with open(out / "teacher2.jsonl", "w", encoding="utf-8") as f:
        for r in answers:
            f.write(json.dumps(r) + "\n")
    got = [r for r in answers if r["text"] is not None]
    print(
        f"asked {len(gold)}, answered {len(got)}, errors {len(gold) - len(got)}, spend about ${usd:.4f}, {time.time() - t0:.0f}s"
    )
    if len(got) < len(gold):
        print("errors:", sorted({r["error"] for r in answers if r["error"]}))

    # Score exactly as the exam scores the student: the second answer plays 'adapter' and 'base'.
    raw = []
    for r in answers:
        t = r["text"] or ""
        side = {"text": t, "mean_prob": 1.0, "min_prob": 1.0, "tokens_in": 0, "tokens_out": 0}
        raw.append({"i": r["i"], "teacher": gold[r["i"]]["completion"][0]["content"], "base": side, "adapter": side})
    rows = m.annotate(raw, gold, dbs)
    agree = sum(1 for r in rows if r["ok"]["adapter"])
    n = len(rows)
    res = {
        "what": "the teacher answering the exam prompt, scored against the stored label by running both on the same database",
        "n": n,
        "answered": len(got),
        "agree": agree,
        "rate": round(agree / n, 4),
        "wilson95": m.wilson(agree, n),
        "spend_usd_estimate": round(usd, 4),
        "tokens": {"in": spent["in"], "out": spent["out"]},
        "model": MODEL,
    }
    (out / "teacher_selfcheck.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
