"""Build the nightly training set from the ledger.

- Only teacher-written examples whose license allows distillation are used.
- Retrain from the base model on the cumulative dataset every night (replay is built in),
  instead of stacking adapters, which limits forgetting.
- The student's own answers are never used as training targets (avoids model collapse).
- Golden examples (a fixed ~10% by hash) are held out for evaluation and never trained on,
  EXCEPT requests that keep repeating: once seen FREQUENT times they are too valuable to hold
  out, so they move to training and leave the golden set. A request counts as one however many
  stored conditions it came with (`examples.params`): its variants share a split and a count.
- train.py renders the messages only, never the stored conditions, so an example the teacher
  wrote under a client's `stop` is held out of training: it ends where that stop cut it, and the
  student would learn to stop there on the bare prompt. It still serves the golden eval, which
  samples the student under the same stop.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .. import licenses
from ..ledger import Ledger, example_hash

FREQUENT = 3


def _prompt_key(r: Any) -> str:
    """The hash of an example's task and messages without its conditions: the same for every variant of one request,
    and the example's own hash when it has none (`ledger.example_hash`)."""
    return example_hash(r["task"], json.loads(r["messages"])) if r["params"] else r["hash"]


def rows(ledger: Ledger, split: str, task: str | None = None) -> list[dict[str, Any]]:
    out = []
    examples = ledger.examples(task=task)
    keys = [_prompt_key(r) for r in examples]
    seen: Counter[str] = Counter()
    for r, key in zip(examples, keys, strict=True):
        seen[key] += int(r["seen"] or 1)
    for r, key in zip(examples, keys, strict=True):
        frequent = seen[key] >= FREQUENT
        effective = "train" if (r["split"] == "train" or frequent) else "golden"
        if effective != split:
            continue
        params = json.loads(r["params"]) if r["params"] else None
        if effective == "train" and params and "stop" in params:
            continue  # train.py ignores the stop, so the cut answer would teach the bare prompt to stop there
        lic = licenses.lookup(r["teacher_model"])
        if lic is None or not lic.distill_ok:
            continue
        msgs = json.loads(r["messages"])
        out.append(
            {
                "task": r["task"],
                "messages": msgs + [{"role": "assistant", "content": r["completion"]}],
                # The conditions the teacher answered under (ledger.STORED_PARAM_KEYS), or None: the golden eval samples
                # the student under them, as production does (FR-5.1).
                "params": params,
                "seen": int(r["seen"] or 1),
                "provenance": {
                    "teacher": r["teacher_model"],
                    "license": r["license"],
                    "hash": r["hash"],
                    "redactions": int(r["redactions"] or 0),
                },
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
