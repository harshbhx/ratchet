# ADR 0040: Per-task calibration when several tasks share one adapter: thresholds, a per-task golden gate on Wilson bounds, and per-task alerts

- Status: Accepted
- Date: 2026-10-20

## Context

One adapter often serves several tasks: Scout's triage and reader calls next to support tickets (the `mixed` workload), or a company's own tasks registered through the task registry (UC-06). Until now everything after the student's samples treated them as one:

- `cascade_eval` calibrated one threshold over the pooled golden rows. The student is rarely equally good at every task. The easy tasks' right answers dilute a hard task's wrong ones, so the pooled threshold serves the hard task below the target precision (97%). When the hard task pushes the threshold up instead, the easy tasks are held back.
- `ReleaseGate.admit` read the pooled precision and coverage only. A candidate that got one task badly wrong shipped if the others hid it.
- `Ratchet.threshold` ignored the task, apart from the spec-change check of [ADR 0022](0022-task-registry.md).
- The live alert looked at a version's audits as a whole, so a task the student does badly never raised it when the other tasks were fine.

## Decision

- **One record per task in every eval.** `cascade_eval` scores each golden row once and splits the rows into calibration and held-out halves by their global position, exactly as before, so the global numbers do not move. Each point also goes into its task's lists, and `eval['tasks'][name]` holds `golden_n`, `test_n`, `agreement`, `threshold`, `precision`, `coverage`, `spec_hash`, `target_precision` and `calibrated`. With one task the record is the global numbers again: the tickets reference simulation is byte-identical.
  - A task's threshold is calibrated on its own calibration rows to its spec's `policy.target_precision` (default 97%), by the same rule as the global one.
  - With fewer than `RATCHET_MIN_TASK_GOLDEN` (20) calibration rows a task gets the higher of its own threshold and the global one (`calibrated: false`), so a few lucky rows cannot lower it.
  - A task with no held-out row gets 1.01: the student never answers a task without evidence measured on rows the threshold was not tuned on (INV-6). A task whose golden references are all invalid under its spec has a record with no rows, so it gets 1.01 too.
  - `spec_hash` replaces `eval['spec_hashes']` as the record of the spec a task was scored under. `run_night` still writes `spec_hashes`, because it does not depend on the evaluator (a custom `Evaluator` may return no records), and readers check the record first (`calibration.recorded_hash`).
- **The router holds each task to its own threshold** (`calibration.threshold`, called by `Ratchet.threshold`): the task's record when its `spec_hash` is current, 1.01 when it is not (or when the older `spec_hashes` names another spec), else the version's global threshold. A version scored before this change has no records and keeps its one threshold. It is a dict lookup on a JSON object the request path already reads. `system_fingerprint` hashes the threshold the answer was held to, so each task of one version now has its own.
- **A per-task golden gate on statistical evidence.** After the global checks, which are unchanged, `ReleaseGate.task_failures` checks each task with at least `RATCHET_MIN_TASK_TEST_N` (10) held-out rows that the candidate would answer. A task fails when the 95% Wilson upper bound of its held-out precision (over the answers it would serve) is below its floor (`policy.min_precision`, else `min_precision`), or below the live version's precision on that task less `tolerance`. Live is compared only where it answers the task under the same spec with as many rows.
  - `RATCHET_TASK_MODE=disable` (the default) disables the task on the candidate (`Ledger.merge_task_eval` sets its threshold to 1.01 and `disabled` to the reason, in one transaction) and writes `task_disabled`; the version ships for its other tasks. `reject` rejects the candidate.
  - Either way, a candidate with records is rejected when the share of its held-out rows it would still answer, each task at its own threshold and the failing ones off, is below `min_coverage`. A version that serves (almost) nothing would wait in shadow or canary forever (no audits, no would-serve shadow answers), and no night trains while it does (INV-5).
- **A disabled task stays disabled for the version's life.** The champion re-check and a skip night's re-score overwrite a version's eval but run no gate, so `run_night` carries the disabled records over (`calibration.keep_disabled`), also for a task the re-score has no record for.
- **One alert per (version, task).** A new tick step, `_tick_tasks`, applies the live alert's rule (Wilson upper bound of audited precision below `live_floor`, at least `min_audits` audits in the current stage) per task, for the live version and the canary. A version whose audits in the stage are all one task's is left to its own alert and rollback, which say the same thing, so a single-task run writes nothing new. Once-only is read from the ledger's events (`Ledger.has_event`, a prefix comparison rather than LIKE because task names may hold `_`), so a new gate does not repeat it: each `python -m ratchet.live tick` builds one. Rollback stays per version, and nothing is disabled automatically on a live version: pinning a task is an operator's decision.
- **Stats and BOM.** `stats_by_task` adds the teacher's audits of each task (`audits`, `audit_agreement`) and, from the live version's eval, `threshold` (as the router applies it), `precision`, `coverage`, `calibrated` and `disabled`. The BOM's `eval` block carries the records written at training time; a later disable is a release decision, recorded in events and in the version's eval, never in the signed BOM (INV-14).
- `ratchet/calibration.py` holds the readers, pure functions over plain dicts, so the router, the gate, Night School and the stats read an older or hand-edited eval the same way. A record whose threshold is not a number sends its task to the teacher rather than guess.

## Consequences

- The noisy task of a shared adapter is served at the precision it was calibrated to, and the easy ones are no longer held back by it. With small golden sets the per-task thresholds are conservative, because a task's own threshold on a few dozen rows moves with every error near the top. In the mixed and research simulations (mock models) the student serves a few points less traffic than with one pooled threshold, and audited agreement is the same or higher.
- The mixed and research reference workloads (`tests/fixtures/reference.json`) were captured again; the tickets workload has one task and is unchanged, as is the pinned reference simulation.
- The global numbers are still measured at the global threshold, because the gate's global checks and every earlier version read them; the records describe what the version serves. Folding the records into the global checks, and certified per-task bounds, are left to certified precision (FR-16.3), for which the records are the interface.
- Changing a task's `target_precision` or `min_precision` changes its `spec_hash`, so the task goes to the teacher until the next night calibrates it under the new target.
- The per-task decisions use Wilson bounds on the held-out rows, a single fixed-sample look per night, so the bound's level holds for them. The per-task alert looks after every tick, as the version-level one does ([ADR 0024](0024-evidence-foundations.md) has the numbers); the anytime-valid alert (FR-16.2) can take over both.

## Alternatives

- **A point-estimate per-task gate** (precision below the floor, or below live's less 2 points), as the global gate uses. On the few dozen held-out rows a task has, that is mostly noise: a task at 97% shows under 95% on 30 rows about one night in four, and a candidate exactly as good as live shows more than 2 points behind it on 30 rows about one night in three. In the mixed simulation it disabled two of three tasks of a good candidate in one night. The Wilson bound acts only on strong evidence, as rollback does.
- **A golden set per task**, split by task before the even/odd split. Cleaner on paper, but it changes which rows calibrate the global threshold, so every existing number would move, including the single-task reference simulation.
- **Disable a task on the live version on a per-task alert.** Automatic action on live traffic is what the version-level alert avoids too; an operator pins the task instead, and the next version is checked afresh.
- **Make per-task thresholds opt-in.** It would keep the multi-task reference runs unchanged, but leave every shared adapter serving its hardest task below target by default.
