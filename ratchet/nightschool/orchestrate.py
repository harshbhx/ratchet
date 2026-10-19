"""One night of Night School: dataset -> train -> golden eval -> release gate."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Any, Protocol

from ..ledger import Ledger
from ..release import ReleaseGate
from . import dataset


class Trainer(Protocol):
    def train(self, train_rows: list[dict[str, Any]], base_model: str, name: str) -> str:
        """Train a LoRA adapter and return where it lives (bucket path / adapter name)."""
        ...


class Evaluator(Protocol):
    def evaluate(self, adapter_uri: str, golden_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Return at least {'golden_agreement': float, 'golden_n': int}."""
        ...


@dataclass
class NightReport:
    trained: bool
    version: int | None
    admitted: bool
    reason: str
    eval: dict[str, Any] | None = None


def run_night(
    ledger: Ledger,
    trainer: Trainer,
    evaluator: Evaluator,
    gate: ReleaseGate,
    base_model: str,
    min_new_examples: int = 50,
    task: str | None = None,
) -> NightReport:
    in_flight = ledger.versions(["shadow", "canary"])
    if in_flight:
        v = in_flight[-1]
        ledger.event("skip", f"v{v.id} still in {v.state}; no training tonight (saves GPU time)")
        return NightReport(False, None, False, "release in flight")
    with tempfile.TemporaryDirectory() as tmp:
        manifest = dataset.build(ledger, tmp, task)
    train_rows = dataset.rows(ledger, "train", task)
    golden_rows = dataset.rows(ledger, "golden", task)

    last = max((v.train_examples for v in ledger.versions()), default=0)
    new = manifest["train_examples"] - last
    if new < min_new_examples:
        ledger.event("skip", f"only {new} new examples (< {min_new_examples}); no training tonight")
        return NightReport(False, None, False, "not enough new data")
    if not golden_rows:
        ledger.event("skip", "no golden examples yet; no training tonight")
        return NightReport(False, None, False, "no golden set")

    # Champion vs challenger on the SAME golden set: re-score the live version first.
    live = ledger.live()
    if live is not None:
        recheck = evaluator.evaluate(live.adapter_uri, golden_rows)
        ledger.update_eval(live.id, {**recheck, "rechecked_day": ledger.day})

    name = f"student-v{len(ledger.versions()) + 1}"
    adapter_uri = trainer.train(train_rows, base_model, name)
    vid = ledger.create_version(
        base_model, adapter_uri, manifest["train_examples"], manifest["dataset_sha256"], {}, state="candidate"
    )
    result = evaluator.evaluate(adapter_uri, golden_rows)
    ledger.update_eval(vid, result)
    ledger.event(
        "trained",
        f"v{vid} on {manifest['train_examples']} examples; "
        f"golden agreement {result.get('golden_agreement', 0):.1%} "
        f"(n={result.get('golden_n', 0)})",
    )
    admitted = gate.admit(vid)
    return NightReport(True, vid, admitted, "admitted" if admitted else "rejected", result)
