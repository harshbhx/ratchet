"""One night of Night School: dataset -> train -> golden eval -> release gate."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .. import bom, certificate, tasks
from ..config import GpuRate
from ..ledger import Ledger, Version
from ..release import ReleaseGate
from ..resilience import describe_failure
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


def spec_hashes(golden_rows: list[dict[str, Any]]) -> dict[str, str]:
    """{task: spec_hash} of the tasks in the golden rows, under their current specs: recorded in every eval this night
    writes (`eval['spec_hashes']`), so `Ratchet.threshold` sends a task whose spec changed since to the teacher until a
    night re-scores the version under the new spec (FR-2.15)."""
    return {name: tasks.get(name).spec_hash for name in sorted({r["task"] for r in golden_rows})}


def stale_tasks(version: Version, hashes: dict[str, str]) -> list[str]:
    """The tasks whose spec changed since `version` was last scored: recorded in its eval with another hash than
    `hashes` (the current ones). A version scored before spec hashes were recorded has none."""
    recorded = version.eval.get("spec_hashes")
    if not isinstance(recorded, dict):
        return []
    return sorted(t for t, h in hashes.items() if t in recorded and recorded[t] != h)


def paired_evidence(recheck: dict[str, Any], result: dict[str, Any], live_id: int) -> dict[str, Any] | None:
    """The candidate's exact paired comparison with the live version (`certificate.paired`, ADR 0026), from the test
    vectors of tonight's re-score and the candidate's eval, plus `against` (the live version's id), or None when either
    eval has no vector or they differ in length. Both evals ran on the same golden rows tonight (INV-4), so the vectors
    are the same held-out rows in the same order."""
    live_vec, cand_vec = recheck.get("test_vector"), result.get("test_vector")
    if not (isinstance(live_vec, str) and isinstance(cand_vec, str) and live_vec and len(live_vec) == len(cand_vec)):
        return None
    try:
        return {**certificate.paired(live_vec, cand_vec), "against": live_id}
    except ValueError:  # a vector from another evaluator, with letters the comparison does not know
        return None


def run_night(
    ledger: Ledger,
    trainer: Trainer,
    evaluator: Evaluator,
    gate: ReleaseGate,
    base_model: str,
    min_new_examples: int = 50,
    task: str | None = None,
    signer: bom.Signer | None = None,
    train_gpu: GpuRate | None = None,
    clock: Callable[[], float] | None = None,
    redact_pii: bool | None = None,
) -> NightReport:
    """One night. With `train_gpu` (live mode: the training job's GPU rate), the wall-clock time of `trainer.train()` is
    recorded as a `training_job` compute interval on `clock` (default `ledger.now`) for the cost report (FR-15.2), and
    closed whether training returns or raises. It includes the dataset upload and the adapter load around the GPU job,
    so it is an upper bound on the job's billed time. Simulations record their own intervals on a synthetic clock
    (`cost.record_simulated_day`) and pass none. `redact_pii` (`Settings.redact_pii`) goes into the BOM's `redactor`.

    Each version commits to its training and golden rows (FR-16.4, ADR 0027): their leaves are written right after
    `create_version` (`Ledger.set_version_leaves`, append-only) and their roots signed in the BOM. Later features add
    their steps at fixed places (ADR 0024): early-night checks before the `min_new_examples` gate, paired evidence right
    after the candidate's eval, and BOM sections into the one `bom_extra` dict."""
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
    hashes = spec_hashes(golden_rows)
    live = ledger.live()
    # Stored answers the current spec refuses (it tightened since): left out of training, and said so where operators
    # look, because the manifest itself is a temporary file.
    invalid = manifest.get("skipped_invalid", 0)
    left_out = f" ({invalid} stored answers invalid under the current spec left out)" if invalid else ""
    if new < min_new_examples:
        ledger.event("skip", f"only {new} new examples (< {min_new_examples}){left_out}; no training tonight")
        # A task whose spec changed is on the teacher until the live version is scored under the new spec; without new
        # data that would wait for a training night, so score it now (the same re-score a training night starts with).
        changed = stale_tasks(live, hashes) if live is not None and golden_rows else []
        if live is not None and changed:
            try:
                recheck = evaluator.evaluate(live.adapter_uri, golden_rows)
            except Exception as e:  # noqa: BLE001 - a skip night stays a skip night; the tasks stay on the teacher
                ledger.event("rescore_failed", f"v{live.id}: {describe_failure(e)}; spec changed for {', '.join(changed)}")
            else:
                ledger.update_eval(live.id, {**recheck, "rechecked_day": ledger.day, "spec_hashes": hashes})
                ledger.event("rescored", f"v{live.id} re-scored on the golden set: spec changed for {', '.join(changed)}")
        return NightReport(False, None, False, "not enough new data")
    if not golden_rows:
        ledger.event("skip", "no golden examples yet; no training tonight")
        return NightReport(False, None, False, "no golden set")

    # Champion vs challenger on the SAME golden set (and the same specs): re-score the live version first.
    if live is not None:
        recheck = evaluator.evaluate(live.adapter_uri, golden_rows)
        ledger.update_eval(live.id, {**recheck, "rechecked_day": ledger.day, "spec_hashes": hashes})

    # The set commitments of tonight's rows, built before the paid training job so nothing can fail after it.
    commitments = {"training": dataset.commitment(train_rows), "golden": dataset.commitment(golden_rows)}
    name = f"student-v{len(ledger.versions()) + 1}"
    now = clock or ledger.now
    job = None
    if train_gpu is not None:
        job = ledger.compute_start(
            "training_job", train_gpu.usd_per_gpu_hour, train_gpu.gpus, "night", ts=now(), note=f"adapter {name}"
        )
    ended: float | None = None
    vid: int | None = None
    try:
        adapter_uri = trainer.train(train_rows, base_model, name)
        ended = now()
        vid = ledger.create_version(
            base_model, adapter_uri, manifest["train_examples"], manifest["dataset_sha256"], {}, state="candidate"
        )
    finally:
        if job is not None:  # the GPU time is paid whether or not training worked: close the interval, then re-raise
            note = None if ended is not None else f"adapter {name}: training failed"
            ledger.compute_stop("training_job", ts=ended if ended is not None else now(), interval_id=job, version=vid, note=note)
    # Set commitments (FR-16.4, ADR 0027): the leaves of the exact rows the adapter was trained and graded on, written
    # once, before the eval and the BOM, so every signed root has its leaves in the ledger.
    for section, (_, _, leaves) in commitments.items():
        ledger.set_version_leaves(vid, bom.COMMITMENT_SPLITS[section], leaves)
    result = {**evaluator.evaluate(adapter_uri, golden_rows), "spec_hashes": hashes}
    if live is not None:  # paired evidence, before the eval is stored and the BOM signed, so the BOM carries it
        pair = paired_evidence(recheck, result, live.id)
        if pair is not None:
            result["paired"] = pair
    ledger.update_eval(vid, result)
    # The adapter's own bill of materials, written now, while we know exactly what it was trained on. Features add their
    # sections (bom.EXTRA_KEYS) to `bom_extra` above this line.
    bom_extra: dict[str, Any] = {}
    version = ledger.version(vid)
    assert version is not None
    m = bom.manifest(
        version,
        train_rows,
        golden_rows,
        commitments={section: (root, size) for section, (root, size, _) in commitments.items()},
        extra=bom_extra or None,
        redact_pii=redact_pii,
    )
    ledger.set_bom(vid, bom.document(m, signer))
    ledger.event(
        "trained",
        f"v{vid} on {manifest['train_examples']} examples{left_out}; "
        f"golden agreement {result.get('golden_agreement', 0):.1%} "
        f"(n={result.get('golden_n', 0)})",
    )
    admitted = gate.admit(vid)
    return NightReport(True, vid, admitted, "admitted" if admitted else "rejected", result)
