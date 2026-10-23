"""Measure a trained adapter against the teacher on held-out rows, and let anyone recompute the numbers.

Two steps, so a judge can check the result without a GPU:

  generate  (GPU)   answer every held-out prompt with the base model and with base + adapter; write raw.jsonl
  report    (CPU)   read raw.jsonl and compute agreement, coverage, precision and savings; write results.json
  verify    (CPU)   recompute the report from raw.jsonl and fail if results.json differs

    python scripts/measure_adapter.py generate --golden golden.jsonl --adapter ./adapter --out raw.jsonl
    python scripts/measure_adapter.py report   --raw raw.jsonl --golden golden.jsonl --adapter ./adapter --out results.json
    python scripts/measure_adapter.py verify   --raw raw.jsonl --results results.json

golden.jsonl rows: {"prompt": [messages], "completion": [{"role": "assistant", "content": <teacher answer>}]}, the
rows `dataset.write_training` writes for held-out (golden) examples. Calibration and measurement use different rows
(even rows set the confidence threshold, odd rows measure it), as in ratchet/nightschool/evaluate.py (invariant 6).
Every number is computed here from raw.jsonl; none is typed in. Prices are inputs and are written into results.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

FIELDS = (
    "category",
    "priority",
)  # the ticket-triage task compares only these (ratchet/tasks.py)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def adapter_hash(path: str | Path) -> str | None:
    p = Path(path)
    f = p / "adapter_model.safetensors" if p.is_dir() else p
    return sha256_file(f) if f.exists() else None


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_answer(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def key_of(text: str) -> tuple | str:
    """What counts as 'the same answer': the compared JSON fields, or the whitespace-normalised text."""
    obj = parse_answer(text)
    if obj is not None and any(k in obj for k in FIELDS):
        return tuple(str(obj.get(k, "")).strip().lower() for k in FIELDS)
    return " ".join(text.lower().split())


# ------------------------------------------------------------------------------------------------ SQL mode (CPU)


def _norm(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 6)
    return v.hex() if isinstance(v, bytes) else v


def sql_connect(script: str):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(script)
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in (20, 21, 31, 33) else sqlite3.SQLITE_DENY)
    return conn


def sql_extract(text: str) -> str:
    m = re.search(r"```(?:sql|sqlite)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else text).strip().rstrip(";").strip()


def sql_rows(conn, text: str):
    """(rows, ordered) for one read-only SELECT that runs within 2 s, else None. Never raises."""
    import sqlite3

    q = sql_extract(text)
    if not re.match(r"(?is)^(select|with)\b", q):
        return None
    deadline = time.monotonic() + 2.0
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
    try:
        rows = conn.execute(q).fetchmany(1001)
    except (sqlite3.Error, ValueError):
        return None
    finally:
        conn.set_progress_handler(None, 0)
    return [tuple(_norm(v) for v in r) for r in rows], bool(re.search(r"(?i)\border\s+by\b", q))


def sql_same(conn, teacher_text: str, answer_text: str) -> bool:
    """Do the two queries return the same rows (column names ignored; order only if the teacher's query orders)?"""
    t, a = sql_rows(conn, teacher_text), sql_rows(conn, answer_text)
    if t is None or a is None:
        return False
    if t[1]:
        return t[0] == a[0]
    return sorted(map(repr, t[0])) == sorted(map(repr, a[0]))


def annotate(
    raw: list[dict[str, Any]],
    golden: list[dict[str, Any]] | None,
    dbs: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Add r["ok"] = {"base": bool, "adapter": bool}: did each answer match the teacher's? JSON mode compares fields;
    SQL mode executes both queries on the row's database."""
    conns: dict[str, Any] = {}
    out = []
    for r in raw:
        r = dict(r)
        if dbs is not None and golden is not None:
            db = golden[r["i"]]["meta"]["db"]
            if db not in conns:
                conns[db] = sql_connect(dbs[db])
            r["ok"] = {w: sql_same(conns[db], r["teacher"], r[w]["text"]) for w in ("base", "adapter")}
            r["meta"] = golden[r["i"]]["meta"]
        else:
            r["ok"] = {w: key_of(r[w]["text"]) == key_of(r["teacher"]) for w in ("base", "adapter")}
        out.append(r)
    return out


# ------------------------------------------------------------------------------------------------ generate (GPU)


def generate(a: argparse.Namespace) -> None:  # pragma: no cover - needs a GPU
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    rows = load_jsonl(a.golden)
    tok = AutoTokenizer.from_pretrained(a.base, trust_remote_code=True)
    quant = None
    if not a.bf16:
        quant = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            llm_int8_skip_modules=["in_proj", "out_proj", "lm_head"],  # as in training: the Mamba kernels read them
        )
    base = AutoModelForCausalLM.from_pretrained(
        a.base,
        trust_remote_code=True,
        quantization_config=quant,
        device_map="auto",
        dtype=torch.bfloat16 if a.bf16 else torch.float16,
    )
    # Slow path, kept for reference: for this model use scripts/text2sql/fast_generate.py (about 45 times faster, same answers).
    # The model's own code expects `cache_position`, which transformers 5 no longer passes after the first token: rebuild it.
    _cls = type(base)
    _orig_prepare = _cls.prepare_inputs_for_generation

    def _prepare(self, input_ids, past_key_values=None, **kwargs):
        cp = kwargs.pop("cache_position", None)
        if cp is None and past_key_values is not None:
            try:
                seen = int(past_key_values.get_seq_length())
            except Exception:  # noqa: BLE001
                seen = input_ids.shape[1] - 1
            cp = torch.arange(seen, input_ids.shape[1], device=input_ids.device)
        return _orig_prepare(self, input_ids, past_key_values=past_key_values, cache_position=cp, **kwargs)

    _cls.prepare_inputs_for_generation = _prepare
    model = PeftModel.from_pretrained(base, a.adapter)
    model.eval()
    kw = {"enable_thinking": False}

    def _safe_generate(fn, **kw):
        """Decode with the cache; if the model's cache code fails on this transformers version, decode without it (slower)."""
        try:
            return fn(**kw)
        except (TypeError, AttributeError, IndexError, RuntimeError) as e:
            print(f"generate fell back to use_cache=False after {type(e).__name__}: {str(e)[:120]}", flush=True)
            return fn(use_cache=False, **kw)

    def run(prompt: list[dict[str, str]], with_adapter: bool) -> dict[str, Any]:
        ids = tok.apply_chat_template(
            prompt,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
            **kw,
        ).to("cuda")
        n_in = ids["input_ids"].shape[1]
        with torch.no_grad():
            if with_adapter:
                out = _safe_generate(
                    model.generate,
                    **ids,
                    max_new_tokens=a.max_new,
                    do_sample=False,
                    output_scores=True,
                    return_dict_in_generate=True,
                )
            else:
                with model.disable_adapter():
                    out = _safe_generate(
                        model.generate,
                        **ids,
                        max_new_tokens=a.max_new,
                        do_sample=False,
                        output_scores=True,
                        return_dict_in_generate=True,
                    )
        gen = out.sequences[0][n_in:]
        probs = [torch.softmax(s[0].float(), -1)[t].item() for s, t in zip(out.scores, gen, strict=True)]
        return {
            "text": tok.decode(gen, skip_special_tokens=True),
            "mean_prob": sum(probs) / max(len(probs), 1),
            "min_prob": min(probs) if probs else 0.0,
            "tokens_in": int(n_in),
            "tokens_out": len(gen),
        }

    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            rec = {
                "i": i,
                "teacher": r["completion"][0]["content"],
                "base": run(r["prompt"], False),
                "adapter": run(r["prompt"], True),
            }
            f.write(json.dumps(rec) + "\n")
            if i % 10 == 0:
                print(f"{i + 1}/{len(rows)} rows, {time.time() - t0:.0f}s", flush=True)


# ------------------------------------------------------------------------------------------------ report (CPU)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def agreement(rows: list[dict[str, Any]], who: str) -> dict[str, Any]:
    ok = sum(1 for r in rows if r["ok"][who])
    return {
        "n": len(rows),
        "agree": ok,
        "rate": round(ok / len(rows), 4) if rows else None,
        "wilson95": wilson(ok, len(rows)),
    }


def calibrate(rows: list[dict[str, Any]], target: float) -> float:
    """Lowest confidence threshold whose answered rows agree with the teacher at least `target` of the time."""
    best = 1.01
    for thr in sorted({r["adapter"]["mean_prob"] for r in rows}, reverse=True):
        sel = [r for r in rows if r["adapter"]["mean_prob"] >= thr]
        if sel and sum(r["ok"]["adapter"] for r in sel) / len(sel) >= target:
            best = thr
    return best


def build_report(raw: list[dict[str, Any]], a: argparse.Namespace, golden=None, dbs=None) -> dict[str, Any]:
    raw = annotate(raw, golden, dbs)
    cal, test = (
        raw[0::2],
        raw[1::2],
    )  # even rows calibrate, odd rows measure (invariant 6)
    thr = calibrate(cal, a.target_precision)
    answered = [r for r in test if r["adapter"]["mean_prob"] >= thr]
    ok = sum(r["ok"]["adapter"] for r in answered)
    tin = sum(r["adapter"]["tokens_in"] for r in test)
    tout = sum(r["adapter"]["tokens_out"] for r in test)
    teacher_all = (tin * a.teacher_in_per_m + tout * a.teacher_out_per_m) / 1e6
    # Escalated rows still go to the teacher; answered rows cost the student's price per million tokens.
    esc = [r for r in test if r["adapter"]["mean_prob"] < thr]
    cost = (
        sum(r["adapter"]["tokens_in"] for r in esc) * a.teacher_in_per_m
        + sum(r["adapter"]["tokens_out"] for r in esc) * a.teacher_out_per_m
    ) / 1e6 + ((sum(r["adapter"]["tokens_in"] + r["adapter"]["tokens_out"] for r in answered)) * a.student_per_m) / 1e6
    groups: dict[str, dict[str, Any]] = {}
    if dbs is not None:
        for field, label in (("difficulty", "difficulty"), ("domain_seen", "domain")):
            for val in sorted({str(r["meta"][field]) for r in test}):
                sub = [r for r in test if str(r["meta"][field]) == val]
                name = f"{label}={('seen' if val == 'True' else 'unseen') if field == 'domain_seen' else val}"
                groups[name] = {
                    "base": agreement(sub, "base"),
                    "adapter": agreement(sub, "adapter"),
                }
    return {
        "mode": "sql" if dbs is not None else "json",
        "breakdown_answering_every_row": groups,
        "rows": {"total": len(raw), "calibration": len(cal), "measured": len(test)},
        "base_model_agreement": agreement(test, "base"),
        "adapter_agreement": agreement(test, "adapter"),
        "confidence_threshold": round(thr, 4),
        "target_precision": a.target_precision,
        "coverage": {
            "answered": len(answered),
            "of": len(test),
            "rate": round(len(answered) / len(test), 4) if test else None,
        },
        "precision_when_answered": {
            "n": len(answered),
            "agree": ok,
            "rate": round(ok / len(answered), 4) if answered else None,
            "wilson95": wilson(ok, len(answered)),
        },
        "cost_usd_on_measured_rows": {
            "all_teacher": round(teacher_all, 6),
            "with_student": round(cost, 6),
            "saved_pct": round(100 * (1 - cost / teacher_all), 2) if teacher_all else None,
            "prices_per_million_tokens": {
                "teacher_in": a.teacher_in_per_m,
                "teacher_out": a.teacher_out_per_m,
                "student": a.student_per_m,
            },
        },
        "label": "estimate: token counts are measured, prices are inputs (see prices_per_million_tokens)",
    }


def report(a: argparse.Namespace) -> None:
    raw = load_jsonl(a.raw)
    golden = load_jsonl(a.golden) if a.dbs else None
    dbs = json.loads(Path(a.dbs).read_text(encoding="utf-8")) if a.dbs else None
    res = build_report(raw, a, golden, dbs)
    res["provenance"] = {
        "raw_sha256": sha256_file(a.raw),
        "golden_sha256": sha256_file(a.golden) if a.golden else None,
        "dbs_sha256": sha256_file(a.dbs) if a.dbs else None,
        "adapter_sha256": adapter_hash(a.adapter) if a.adapter else None,
        "base_model": a.base,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gpu": a.gpu or None,
        "run_note": a.note or None,
        "args": {
            k: getattr(a, k)
            for k in (
                "target_precision",
                "teacher_in_per_m",
                "teacher_out_per_m",
                "student_per_m",
            )
        },
    }
    Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: res[k]
                for k in (
                    "adapter_agreement",
                    "coverage",
                    "precision_when_answered",
                    "cost_usd_on_measured_rows",
                )
            },
            indent=2,
        )
    )


def verify(a: argparse.Namespace) -> None:
    saved = json.loads(Path(a.results).read_text(encoding="utf-8"))
    p = saved["provenance"]
    if sha256_file(a.raw) != p["raw_sha256"]:
        sys.exit("FAIL: raw.jsonl is not the file the results were computed from")
    ns = argparse.Namespace(**p["args"])
    golden = dbs = None
    if p.get("dbs_sha256"):
        if not (a.golden and a.dbs):
            sys.exit("FAIL: this is a SQL result; pass --golden and --dbs too")
        if sha256_file(a.golden) != p["golden_sha256"] or sha256_file(a.dbs) != p["dbs_sha256"]:
            sys.exit("FAIL: golden.jsonl or dbs.json is not the file the results were computed from")
        golden = load_jsonl(a.golden)
        dbs = json.loads(Path(a.dbs).read_text(encoding="utf-8"))
    # Through JSON and back, so tuples compare equal to the lists the saved file holds.
    again = json.loads(json.dumps(build_report(load_jsonl(a.raw), ns, golden, dbs)))
    bad = [k for k in again if again[k] != saved.get(k)]
    if bad:
        sys.exit(f"FAIL: recomputed numbers differ in {bad}")
    print("OK: every number in results.json was recomputed from raw.jsonl and matches")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--golden", required=True)
    g.add_argument("--adapter", required=True)
    g.add_argument("--base", default="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    g.add_argument("--out", required=True)
    g.add_argument("--max-new", type=int, default=160, help="use 256 for SQL")
    g.add_argument(
        "--bf16",
        action="store_true",
        help="load in bf16 instead of 4-bit (needs an L4 or better)",
    )
    g.set_defaults(fn=generate)
    r = sub.add_parser("report")
    r.add_argument("--raw", required=True)
    r.add_argument("--golden")
    r.add_argument(
        "--dbs",
        help="dbs.json: score by RUNNING each answer's SQL on the row's database (needs --golden)",
    )
    r.add_argument("--adapter")
    r.add_argument("--base", default="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    r.add_argument("--out", required=True)
    r.add_argument("--target-precision", type=float, default=0.95)
    r.add_argument(
        "--teacher-in-per-m",
        type=float,
        default=1.0,
        help="USD per 1M input tokens (Nemotron 3 Ultra on Token Factory)",
    )
    r.add_argument("--teacher-out-per-m", type=float, default=3.0)
    r.add_argument(
        "--student-per-m",
        type=float,
        default=0.06,
        help="USD per 1M tokens for the student; an estimate, set it from your serving cost",
    )
    r.add_argument("--gpu", default="")
    r.add_argument("--note", default="")
    r.set_defaults(fn=report)
    v = sub.add_parser("verify")
    v.add_argument("--raw", required=True)
    v.add_argument("--results", required=True)
    v.add_argument("--golden")
    v.add_argument("--dbs")
    v.set_defaults(fn=verify)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
