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
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .. import bom, licenses, tasks
from ..ledger import Ledger, example_hash
from ..merkle import KeySet

FREQUENT = 3


def _prompt_key(r: Any) -> str:
    """The hash of an example's task and messages without its conditions: the same for every variant of one request,
    and the example's own hash when it has none (`ledger.example_hash`)."""
    return example_hash(r["task"], json.loads(r["messages"])) if r["params"] else r["hash"]


def rows(ledger: Ledger, split: str, task: str | None = None, counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """The examples of one split ('train' or 'golden') as training rows, after the rules above. `counts`, when given,
    gains `skipped_invalid` (train rows left out because the answer is not valid under the task's current spec) and
    `excluded_pinned` (rows left out because their task is pinned now), each only when it happens."""
    out = []
    specs: dict[str, tasks.Task] = {}
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


def leaf_value(row: dict[str, Any]) -> bytes:
    """A row's leaf value in its split's set commitment (FR-16.4, ADR 0027): the SHA-256 of the canonical JSON of its
    label (the stored teacher answer, which is the training target), the teacher and license that wrote it, and the
    number of PII placeholders in it. A proof then binds what an example was trained on and under which license, not
    only that its key was there; anyone holding the example can recompute the value."""
    prov = row["provenance"]
    preimage = {
        "completion": row["messages"][-1]["content"],
        "teacher": prov["teacher"],
        "license": prov["license"],
        "redactions": int(prov["redactions"]),
    }
    return hashlib.sha256(bom.canonical(preimage)).digest()


def commitment(items: list[dict[str, Any]]) -> tuple[str, int, list[tuple[str, str]]]:
    """The set commitment of one split's rows (`merkle.KeySet`, ADR 0027): (root hex, size, the (key hex, value hex)
    leaves sorted by key). The key is the example's hash (`provenance.hash`, `examples.hash`), so the training and golden
    sets of one night never share a key (`rows` puts each example in exactly one), and a duplicate raises ValueError."""
    keyset = KeySet((bytes.fromhex(it["provenance"]["hash"]), leaf_value(it)) for it in items)
    return keyset.root_hex, keyset.size, [(k.hex(), v.hex()) for k, v in keyset.items()]


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
