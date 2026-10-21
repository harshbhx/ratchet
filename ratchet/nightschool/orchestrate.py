"""One night of Night School: dataset -> train -> golden eval -> release gate."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .. import bom, calibration, certificate, privacy, tasks, teacher_watch
from ..confidence import SERVE_NOTHING
from ..config import GpuRate, Retention
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
    night re-scores the version under the new spec (FR-2.15). The per-task records of `cascade_eval` carry the same hash
    (`eval['tasks'][name]['spec_hash']`, read first); this one is kept because it does not depend on the evaluator."""
    return {name: tasks.get(name).spec_hash for name in sorted({r["task"] for r in golden_rows})}


def stale_tasks(version: Version, hashes: dict[str, str]) -> list[str]:
    """The tasks whose spec changed since `version` was last scored: recorded in its eval (its per-task record, else
    `spec_hashes`; `calibration.stale`) with another hash than `hashes` (the current ones). A version scored before spec
    hashes were recorded has none."""
    return sorted(t for t, h in hashes.items() if calibration.stale(version.eval, t, h))


def rescore(
    ledger: Ledger, version: Version, recheck: dict[str, Any], hashes: dict[str, str], gate: ReleaseGate | None = None
) -> None:
    """Store a re-score of `version` (the champion re-check, or a skip night's re-score after a spec change). The tasks
    the release gate disabled on it stay disabled (`calibration.keep_disabled`): the re-score runs no gate but one. With
    `gate`, a task whose critical classes the re-score does not show safe (`ReleaseGate.critical_holds`, FR-2.50) is
    disabled in the same write, with a `task_disabled` event, so the version never serves it on unchecked numbers."""
    new = calibration.keep_disabled(version.eval, {**recheck, "rechecked_day": ledger.day, "spec_hashes": hashes})
    held = gate.critical_holds(new) if gate is not None else []
    if held:
        records = calibration.entries(new)
        for name, why in held:
            records[name] = {**records[name], "threshold": SERVE_NOTHING, "disabled": why}
        new = {**new, "tasks": records}
    ledger.update_eval(version.id, new)
    for name, why in held:
        ledger.event("task_disabled", f"v{version.id} {name}: {why}; the teacher answers it (re-scored)")


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


def _rescore_changed_specs(
    ledger: Ledger,
    evaluator: Evaluator,
    live: Version | None,
    golden_rows: list[dict[str, Any]],
    hashes: dict[str, str],
    gate: ReleaseGate | None = None,
) -> None:
    """A task whose spec changed is on the teacher until the live version is scored under the new spec; on a night that
    does not train, score it now (the same re-score a training night starts with, `gate`'s critical check included). It
    reads the stored golden rows and writes only the version's eval, so it also runs under zero retention. A failure
    leaves the tasks on the teacher."""
    changed = stale_tasks(live, hashes) if live is not None and golden_rows else []
    if live is None or not changed:
        return
    try:
        recheck = evaluator.evaluate(live.adapter_uri, golden_rows)
    except Exception as e:  # noqa: BLE001 - a skip night stays a skip night; the tasks stay on the teacher
        ledger.event("rescore_failed", f"v{live.id}: {describe_failure(e)}; spec changed for {', '.join(changed)}")
    else:
        rescore(ledger, live, recheck, hashes, gate)  # keeps the tasks the gate disabled on it disabled (FR-14.1)
        ledger.event("rescored", f"v{live.id} re-scored on the golden set: spec changed for {', '.join(changed)}")


def _without_erased(ledger: Ledger, train_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """`train_rows` without the examples an erasure tombstoned since they were read, and how many that was (FR-13.3)."""
    gone = ledger.tombstoned(r["provenance"]["hash"] for r in train_rows)
    if not gone:
        return train_rows, 0
    kept = [r for r in train_rows if r["provenance"]["hash"] not in gone]
    return kept, len(train_rows) - len(kept)


def early_night(ledger: Ledger, gate: ReleaseGate, min_new_examples: int) -> tuple[int, str]:
    """The new examples tonight needs, and the note a skip event adds (FR-6.41, ADR 0032). With drift_early_night=1,
    when a drift alarm came from a request logged after the last `trained` event, half of `min_new_examples` (at least
    1): traffic changed, so a student trained on it is worth having sooner. The drift monitor is brought up to date
    first, so a nightly job in its own process sees the alarms the gateway would; the alarm's request decides, not when
    its event was written, so an old alarm that a replay finds late does not count. Otherwise `min_new_examples` and no
    note. It runs after the in-flight check, so it never trains during a release (INV-5)."""
    if not gate.policy.drift_early_night or gate.drift is None:
        return min_new_examples, ""
    gate.check_drift()
    trained = ledger.last_event("trained")
    if not gate.drift_alarm_since(trained["ts"] if trained is not None else None):
        return min_new_examples, ""
    return max(1, min_new_examples // 2), f" (halved from {min_new_examples} after a drift alarm)"


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
    store_examples: bool = True,
    retention: Retention | None = None,
    relabel: teacher_watch.Relabel | None = None,
) -> NightReport:
    """One night. With `retention` (live mode: `Settings.retention()`), the retention windows are applied first
    (`privacy.apply_retention`, FR-13.4), so the night never trains on text that has expired; a failure there stops the
    night. With `store_examples` False (RATCHET_STORE_EXAMPLES=0, zero retention; FR-13.1) nothing is stored, so
    the night skips before anything else (`storage disabled`): examples kept from before are not trained on, but when
    no release is in flight a task whose spec changed is still re-scored on them, so it does not stay on the teacher
    for good (`_rescore_changed_specs`, which stores nothing). With `train_gpu` (live mode: the training job's GPU
    rate), the wall-clock time of `trainer.train()` is recorded as a `training_job` compute interval on `clock`
    (default `ledger.now`) for the cost report (FR-15.2), and
    closed whether training returns or raises. It includes the dataset upload and the adapter load around the GPU job,
    so it is an upper bound on the job's billed time. Simulations record their own intervals on a synthetic clock
    (`cost.record_simulated_day`) and pass none. `redact_pii` (`Settings.redact_pii`) goes into the BOM's `redactor`.

    With `gate.policy.drift_early_night`, a drift alarm since the last training halves `min_new_examples`
    (`early_night`).

    The teacher watch (FR-16.7, ADR 0033) comes first: with `relabel` and no freeze on, the night's probes and
    adjudications run (`teacher_watch.night`, FR-5.40, FR-5.41), also while a release is in flight (they are evidence,
    not training), and the freeze rule is checked again. Then a freeze (`teacher_drift`, FR-6.42) skips the night,
    before the in-flight check, because only an operator can lift it. Contested labels are left out of both sets, and
    the BOM's `teacher` section records the epoch's self-agreement. With RATCHET_TEACHER_WATCH=0 none of this
    happens.

    Each version commits to its training and golden rows (FR-16.4, ADR 0027): their leaves are written right after
    `create_version` (`Ledger.set_version_leaves`, append-only) and their roots signed in the BOM. Later features add
    their steps at fixed places (ADR 0024): early-night checks before the `min_new_examples` gate, paired evidence right
    after the candidate's eval, and BOM sections into the one `bom_extra` dict.

    New data is the training rows now less the most training rows any version had that are still stored
    (`Ledger.train_rows_kept`: each version's `train_examples` less its own training rows purged since), so deleting
    data never hides new data and never makes old data look new (FR-4.4); without purges the count is v0.4's.

    An operator's `training_paused` (the admin API or CLI, ADR 0064) skips the night before anything else, with a `skip`
    event (the gateway's hourly retention purge still runs). Reading the switches also publishes the runtime task pins,
    so the dataset and the golden eval leave a task pinned at runtime out as they do a task pinned in its spec (INV-10)."""
    if ledger.flags().training_paused:
        ledger.event("skip", "training paused by an admin; no training tonight")
        return NightReport(False, None, False, "training paused")
    if retention is not None and retention.enabled:
        privacy.apply_retention(ledger, retention)
    if not store_examples:
        ledger.event("skip", "storage disabled (RATCHET_STORE_EXAMPLES=0): no new examples are stored; no training tonight")
        if not ledger.versions(["shadow", "canary"]):
            golden_rows = dataset.rows(ledger, "golden", task)
            _rescore_changed_specs(ledger, evaluator, ledger.live(), golden_rows, spec_hashes(golden_rows), gate)
        return NightReport(False, None, False, "storage disabled")
    watching = bool(gate.policy.teacher_watch)
    if watching and relabel is not None and not ledger.teacher_state().frozen:
        # Evidence, not training: it runs during a release too, when audits lean on the teacher most.
        teacher_watch.night(ledger, relabel)
        gate.check_teacher()  # tonight's probes may be what tips the e-value
    if watching and ledger.teacher_state().frozen:  # said before an in-flight release: this one needs an operator
        ledger.event("skip", "teacher labels look unstable; no training tonight (POST /ratchet/teacher/ack resumes)")
        return NightReport(False, None, False, "teacher unstable")
    in_flight = ledger.versions(["shadow", "canary"])
    if in_flight:
        v = in_flight[-1]
        ledger.event("skip", f"v{v.id} still in {v.state}; no training tonight (saves GPU time)")
        return NightReport(False, None, False, "release in flight")
    # One read of the ledger: the manifest (the version's example count and signed dataset hash), the trainer's rows and the
    # commitments all come from it, so an example stored while the files are written cannot be trained on unrecorded.
    read_at = time.time()  # an erasure recorded from now on may postdate the rows read below (`Ledger.taint_late_erasures`)
    snap = dataset.read_snapshot(ledger, task, exclude_contested=watching)
    with tempfile.TemporaryDirectory() as tmp:
        manifest = dataset.build(ledger, tmp, task, exclude_contested=watching, snapshot=snap)
    train_rows, golden_rows = snap.train, snap.golden

    new = manifest["train_examples"] - ledger.train_rows_kept(task)
    hashes = spec_hashes(golden_rows)
    live = ledger.live()
    # Stored answers the current spec refuses (it tightened since): left out of training, and said so where operators
    # look, because the manifest itself is a temporary file.
    invalid = manifest.get("skipped_invalid", 0)
    left_out = f" ({invalid} stored answers invalid under the current spec left out)" if invalid else ""
    tagged = manifest.get("skipped_think_tag", 0)  # FR-8.42: the chat template would render these differently
    if tagged:
        left_out += f" ({tagged} answers with a literal think tag left out of training)"
    need, early = early_night(ledger, gate, min_new_examples)
    if new < need:
        ledger.event("skip", f"only {new} new examples (< {need}){early}{left_out}; no training tonight")
        # Without new data a changed spec would wait for a training night: re-score the live version now.
        _rescore_changed_specs(ledger, evaluator, live, golden_rows, hashes, gate)
        return NightReport(False, None, False, "not enough new data")
    if not golden_rows:
        ledger.event("skip", "no golden examples yet; no training tonight")
        return NightReport(False, None, False, "no golden set")

    # Champion vs challenger on the SAME golden set (and the same specs): re-score the live version first.
    if live is not None:
        recheck = evaluator.evaluate(live.adapter_uri, golden_rows)
        rescore(ledger, live, recheck, hashes, gate)  # keeps the tasks the gate disabled on it disabled (FR-14.1)

    # An erasure that landed since the rows were read (the champion re-score takes minutes) is honoured now: its examples
    # are dropped from the training set, and the version's count and dataset hash follow the rows it is trained on.
    train_rows, dropped = _without_erased(ledger, train_rows)
    if dropped:
        manifest = {**manifest, "train_examples": len(train_rows), "dataset_sha256": dataset.dataset_hash(train_rows)}
        ledger.event("erasure_honoured", f"{dropped} examples erased during the night were left out of training")
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
    # An erasure recorded while the adapter trained could not drop its rows, and its own list of affected versions could not
    # name this one (no committed leaves yet): it is pending again, with this version tainted, until a version without the
    # data replaces it.
    late = ledger.taint_late_erasures(vid, read_at)
    if late:
        ledger.event(
            "erasure_tainted",
            f"v{vid} was trained while erasure {', '.join(f'#{i}' for i in late)} ran and holds erased examples: "
            "the erasure is pending until a version trained without them replaces it",
        )
    result = {**evaluator.evaluate(adapter_uri, golden_rows), "spec_hashes": hashes}
    if live is not None:  # paired evidence, before the eval is stored and the BOM signed, so the BOM carries it
        pair = paired_evidence(recheck, result, live.id)
        if pair is not None:
            result["paired"] = pair
    ledger.update_eval(vid, result)
    # The adapter's own bill of materials, written now, while we know exactly what it was trained on. Features add their
    # sections (bom.EXTRA_KEYS) to `bom_extra` above this line.
    bom_extra: dict[str, Any] = {}
    if watching:  # the teacher's self-agreement in this epoch and the contested labels left out (FR-16.7)
        bom_extra["teacher"] = teacher_watch.bom_section(ledger, manifest.get("excluded_contested", 0))
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
    # Only an early night trains on fewer than min_new_examples new examples (FR-6.41); say so after the usual text.
    sooner = ""
    if new < min_new_examples:
        sooner = f"; early night after a drift alarm ({new} new examples, normally {min_new_examples})"
    ledger.event(
        "trained",
        f"v{vid} on {manifest['train_examples']} examples{left_out}; "
        f"golden agreement {result.get('golden_agreement', 0):.1%} "
        f"(n={result.get('golden_n', 0)}){sooner}",
    )
    admitted = gate.admit(vid)
    return NightReport(True, vid, admitted, "admitted" if admitted else "rejected", result)
