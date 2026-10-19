"""Build the nightly training set from the ledger.

- Only teacher-written examples whose license allows distillation are used.
- Retrain from the base model on the cumulative dataset every night (replay is built in),
  instead of stacking adapters, which limits forgetting.
- The student's own answers are never used as training targets (avoids model collapse).
- Golden examples (a fixed ~10% by hash) are held out for evaluation and never trained on,
  EXCEPT requests that keep repeating: once seen FREQUENT times they are too valuable to hold
  out, so they move to training and leave the golden set.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import licenses
from ..ledger import Ledger

FREQUENT = 3


def rows(ledger: Ledger, split: str, task: str | None = None) -> list[dict[str, Any]]:
    out = []
    for r in ledger.examples(task=task):
        frequent = int(r["seen"] or 1) >= FREQUENT
        effective = "train" if (r["split"] == "train" or frequent) else "golden"
        if effective != split:
            continue
        lic = licenses.lookup(r["teacher_model"])
        if lic is None or not lic.distill_ok:
            continue
        msgs = json.loads(r["messages"])
        out.append(
            {
                "task": r["task"],
                "messages": msgs + [{"role": "assistant", "content": r["completion"]}],
                "seen": int(r["seen"] or 1),
                "provenance": {"teacher": r["teacher_model"], "license": r["license"], "hash": r["hash"]},
            }
        )
    return out


def dataset_hash(items: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for it in items:
        h.update(it["provenance"]["hash"].encode())
    return h.hexdigest()


def write_jsonl(items: list[dict[str, Any]], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return p


def build(ledger: Ledger, out_dir: str | Path, task: str | None = None) -> dict[str, Any]:
    train = rows(ledger, "train", task)
    golden = rows(ledger, "golden", task)
    out = Path(out_dir)
    write_jsonl(train, out / "train.jsonl")
    write_jsonl(golden, out / "golden.jsonl")
    manifest = {
        "train_examples": len(train),
        "golden_examples": len(golden),
        "dataset_sha256": dataset_hash(train),
        "bill_of_materials": ledger.bill_of_materials(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
