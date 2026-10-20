# Lane C: proposed invariants

Invariants proposed by lane C (task model and use cases) for the integrator to number and add to CLAUDE.md. They are not in CLAUDE.md's numbered list yet, so their numbers cannot collide with other lanes'.

## Per-task held-out evidence (TASK-PER-TASK-CALIBRATION, FR-14.1)

**Text.** When several tasks share one adapter, the student answers a task only at a threshold backed by held-out evidence for that task under its current spec. A task that has a per-task record on the serving version is never served by the student without held-out rows (threshold 1.01), nor when its spec changed since the version was scored (1.01 until a night re-scores it), nor when the release gate disabled it on that version; a task the gate disabled stays disabled for that version's life, through every re-score, and its requests never get a student answer, not even in degraded mode. A task with no record (no golden row of it, or a version scored before per-task calibration or with `RATCHET_PER_TASK_CALIBRATION=0`) uses the version's global threshold. See [ADR 0040](../adr/0040-per-task-calibration.md).

**Enforcing code.**

- `ratchet.nightschool.evaluate:_task_record`: 1.01 for a task with no held-out row (and a record with no rows for a task whose golden references are all invalid).
- `ratchet.calibration:threshold`, called by `ratchet.router:Ratchet.threshold` on every request: the task's record under its current `spec_hash`, 1.01 for a stale hash, a disabled record or an unreadable threshold.
- `ratchet.release:ReleaseGate.admit` and `task_failures`: disable (`Ledger.merge_task_eval`, threshold 1.01) or reject on a task's own evidence.
- `ratchet.nightschool.orchestrate:rescore` with `ratchet.calibration:keep_disabled`: a re-score never re-enables a disabled task.
- `ratchet.calibration:held_back`, read by `ratchet.router:Ratchet.handle`: a record that serves nothing means no student call (route `task_disabled`), so degraded mode has no student answer to serve.

**Proving tests** (`tests/test_calibration.py`).

- `test_task_without_test_rows_is_teacher_only`
- `test_stale_spec_hash_threshold_ignored`
- `test_threshold_reads_malformed_records_safely`
- `test_admit_disables_regressed_task_but_ships_version`
- `test_disabled_task_survives_rescore`
- `test_versions_without_task_eval_unchanged`
- `test_disabled_task_skips_the_student`

**For `scripts/reqtrace.py`.** Once numbered (say INV-n), add `"INV-n": ("ratchet.calibration:threshold", "ratchet.nightschool.evaluate:cascade_eval", "ratchet.release:ReleaseGate.admit", "ratchet.nightschool.orchestrate:rescore", "ratchet.calibration:held_back")` to `CODE` and mark the tests above with `@pytest.mark.req("INV-n")`.
