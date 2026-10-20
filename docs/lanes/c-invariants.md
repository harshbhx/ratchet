# Lane C: proposed invariants

Invariants proposed by lane C (task model and use cases) for the integrator to number and add to CLAUDE.md. They are not in CLAUDE.md's numbered list yet, so their numbers cannot collide with other lanes'.

## Per-task held-out evidence (TASK-PER-TASK-CALIBRATION, FR-14.1)

**Text.** When several tasks share one adapter, the student answers a task only at a threshold backed by held-out evidence for that task under its current spec. A version whose eval has per-task records never serves a task that had no held-out golden row (threshold 1.01), whose spec changed since the version was scored (1.01 until a night re-scores it), or that the release gate disabled on it; a task the gate disabled stays disabled for that version's life, through every re-score. A version scored before per-task calibration (no `eval['tasks']`) keeps its one threshold. See [ADR 0040](../adr/0040-per-task-calibration.md).

**Enforcing code.**

- `ratchet.nightschool.evaluate:_task_record`: 1.01 for a task with no held-out row (and a record with no rows for a task whose golden references are all invalid).
- `ratchet.calibration:threshold`, called by `ratchet.router:Ratchet.threshold` on every request: the task's record under its current `spec_hash`, 1.01 for a stale hash, a disabled record or an unreadable threshold.
- `ratchet.release:ReleaseGate.admit` and `task_failures`: disable (`Ledger.merge_task_eval`, threshold 1.01) or reject on a task's own evidence.
- `ratchet.nightschool.orchestrate:rescore` with `ratchet.calibration:keep_disabled`: a re-score never re-enables a disabled task.

**Proving tests** (`tests/test_calibration.py`).

- `test_task_without_test_rows_is_teacher_only`
- `test_stale_spec_hash_threshold_ignored`
- `test_threshold_reads_malformed_records_safely`
- `test_admit_disables_regressed_task_but_ships_version`
- `test_disabled_task_survives_rescore`
- `test_versions_without_task_eval_unchanged`

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.calibration:threshold", "ratchet.nightschool.evaluate:cascade_eval", "ratchet.release:ReleaseGate.admit", "ratchet.nightschool.orchestrate:rescore")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-n")`.
