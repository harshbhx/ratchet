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
- A train row whose answer is not valid under its task's current spec (`Task.valid`, FR-2.14) is
  left out and counted (`skipped_invalid` in manifest.json): the spec may have tightened since the
  answer was stored (a smaller vocabulary, a field that became required). Golden rows stay, and
  `cascade_eval` skips the ones it cannot grade with (`golden_invalid_refs`).
- A row of a task that is pinned NOW is left out of both sets and counted (`excluded_pinned` in
  manifest.json; INV-10 at read time): pinning a task that already has data keeps that data from the
  student, and unpinning it brings the rows back, because the ledger keeps them.
- A purged row (retention or erasure, FR-13.3, FR-13.4) has no text left and is in neither set; its
  `seen` still counts toward its request's frequency, so a purge never moves another variant of a
  frequent request back to golden (INV-3).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .. import licenses, tasks
from ..ledger import Ledger, prompt_key

FREQUENT = 3


def effective_split(r: Any, seen: Counter[str]) -> str:
    """'train' for a train-split row or one whose request was seen FREQUENT times or more (over every variant, purged
    ones included: `ledger.count_prompts`), else 'golden' (INV-3)."""
    return "train" if (r["split"] == "train" or seen[prompt_key(r)] >= FREQUENT) else "golden"


def counts_as_train(r: Any, seen: Counter[str]) -> bool:
    """Whether a row is a training row by the rules that do not need its answer: its effective split is train, its
    teacher's license allows distillation, and it was not cut at a client's stop. A purge records this
    (`examples.purged_train`) while the text is still there: Night School's new-data count and an erasure's affected
    versions read it after the text is gone. It leaves out the spec and pin checks, which may have changed since a
    version trained, so it errs toward counting a row."""
    if effective_split(r, seen) != "train":
        return False
    lic = licenses.lookup(r["teacher_model"])
    if lic is None or not lic.distill_ok:
        return False
    params = json.loads(r["params"]) if r["params"] else None
    return not (params and "stop" in params)


def rows(ledger: Ledger, split: str, task: str | None = None, counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """The examples of one split ('train' or 'golden') as training rows, after the rules above. `counts`, when given,
    gains `skipped_invalid` (train rows left out because the answer is not valid under the task's current spec) and
    `excluded_pinned` (rows left out because their task is pinned now), each only when it happens."""
    out = []
    specs: dict[str, tasks.Task] = {}
    examples = ledger.examples(task=task)
    keys = [prompt_key(r) for r in examples]
    seen: Counter[str] = Counter()
    for r, key in zip(examples, keys, strict=True):
        seen[key] += int(r["seen"] or 1)  # purged rows count too (INV-3)
    for r, key in zip(examples, keys, strict=True):
        if r["purged_ts"] is not None or r["messages"] is None:
            continue  # purged (FR-13.3, FR-13.4): no text to train or grade on
        frequent = seen[key] >= FREQUENT
        effective = "train" if (r["split"] == "train" or frequent) else "golden"
        if effective != split:
            continue
        spec = specs.get(r["task"]) or specs.setdefault(r["task"], tasks.get(r["task"]))
        if spec.pinned:  # INV-10 at read time: the task was pinned after these rows were stored
            if counts is not None:
                counts["excluded_pinned"] = counts.get("excluded_pinned", 0) + 1
            continue
        params = json.loads(r["params"]) if r["params"] else None
        if effective == "train" and params and "stop" in params:
            continue  # train.py ignores the stop, so the cut answer would teach the bare prompt to stop there
        lic = licenses.lookup(r["teacher_model"])
        if lic is None or not lic.distill_ok:
            continue
        if effective == "train" and not spec.valid(r["completion"]):
            if counts is not None:
                counts["skipped_invalid"] = counts.get("skipped_invalid", 0) + 1
            continue  # the spec tightened since it was stored: never a training target
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
                    # Where the example came from (`examples.origin`): 'live' when the row did not record it.
                    "origin": r["origin"] or "live",
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
    counts: dict[str, int] = {}
    golden_counts: dict[str, int] = {}
    train = rows(ledger, "train", task, counts)
    golden = rows(ledger, "golden", task, golden_counts)
    out = Path(out_dir)
    write_jsonl(train, out / "train.jsonl")
    write_jsonl(golden, out / "golden.jsonl")
    manifest = {
        "train_examples": len(train),
        "golden_examples": len(golden),
        "dataset_sha256": dataset_hash(train),
        "skipped_invalid": counts.get("skipped_invalid", 0),
        "excluded_pinned": counts.get("excluded_pinned", 0) + golden_counts.get("excluded_pinned", 0),
        "bill_of_materials": ledger.bill_of_materials(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
