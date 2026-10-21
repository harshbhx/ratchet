# CLAUDE.md: `ratchet/nightschool/`

One night = `orchestrate.run_night(ledger, trainer, evaluator, gate, base_model)`:

0. Skip if an operator paused training (`ledger.flags().training_paused`, the admin API or CLI; ADR 0064), with a `skip` event, before anything else. Reading the switches also publishes the runtime task pins, so `dataset.rows` and `cascade_eval` leave a task pinned at runtime out (`excluded_pinned`) exactly as one its spec pins (INV-10).
1. Skip if a version is in `shadow` or `canary` (release in flight).
2. Build the dataset (`dataset.build`): train rows + golden rows + `manifest.json` with the bill of materials.
3. Skip if fewer than `min_new_examples` new train examples, or no golden rows.
4. Re-score the **live** version on the current golden set (champion vs challenger), recording `spec_hashes` (`orchestrate.spec_hashes`: each golden task's current `spec_hash`) in its eval. A night skipped for too few new examples still re-scores the live version when its recorded hashes are stale (`stale_tasks`; event `rescored`, or `rescore_failed` without failing the night), because `Ratchet.threshold` keeps a changed task on the teacher until then (FR-2.15).
5. `trainer.train(...)` → adapter name (e.g. `student-v4`).
6. `evaluator.evaluate(adapter, golden_rows)` → `{precision, coverage, threshold, golden_agreement, ...}`, plus `spec_hashes`.
7. The adapter's BOM (`bom.manifest(..., extra=bom_extra or None, redact_pii=redact_pii)`, schema v2), then `gate.admit(vid)`.

Later features add their steps at fixed places (ADR 0024): early-night checks (teacher freeze, erasures, the drift-advanced night) before the `min_new_examples` gate; set commitments right after `create_version`; paired evidence right after the candidate's eval, before `update_eval` and the BOM; and BOM sections (`bom.EXTRA_KEYS`) into the one `bom_extra` dict. `run_night(redact_pii=)` takes `Settings.redact_pii` (the gateway, the simulator and `python -m ratchet.live night` pass it) for the BOM's `redactor`.

## Interfaces

- `Trainer.train(train_rows, base_model, name) -> adapter_uri`: mock = `sim.mock_models.MockFleet`; live = `ratchet.live.NebiusTrainer` (Serverless Job + runtime LoRA load).
- `Evaluator.evaluate(adapter_uri, golden_rows) -> dict`: both implementations use `evaluate.cascade_eval` and sample the student with `confidence.STUDENT_SAMPLING`, the router's parameters, and with `GenPolicy.student` applied to each row's stored conditions (`row["params"]`, from `examples.params`; `LiveEvaluator(policy=)`, `MockFleet(policy=)`, both `Settings.gen_policy()` in the gateway, `live.main` and the simulator), so mock, live and production agree (FR-5.1; `test_production_and_both_evaluators_share_student_sampling`, `test_golden_eval_runs_under_production_conditions`). `cascade_eval`'s sampler is `sample(prompt, n, params)`. A row the student endpoint refuses for what it asks (`evaluate.REFUSED`: 400, 413, 422, such as a stored condition it cannot serve) scores as confidence 0 and wrong, as production would escalate it (INV-7), and `refused_n` counts them; any other failure (401, 403, 404, 5xx, a timeout) refuses every row and still stops the night. `LiveEvaluator`'s judge loop treats a refused candidate as not passing.

## Rules

- `cascade_eval` calibrates the threshold on even-indexed golden rows and reports precision/coverage on odd-indexed rows. Do not grade on calibration data.
- Thresholds are clamped to `[floor=0.5, 1.01]`; 1.01 means "serve nothing" (keeps JSON finite).
- Train from the base model on the cumulative dataset every night; do not stack adapters.
- `train.py` runs inside the GPU container only, and `main` is excluded from tests. Its arguments (`parse_args`), the row contract (`load_rows` reads what `NebiusTrainer` writes) and `manifest` (`ratchet_manifest.json`) are tested offline in `tests/test_live.py`. LoRA target modules for Nemotron-H must be confirmed on day 1 (`--targets`).
- `ratchet.live.NebiusTrainer` checks `RATCHET_STUDENT_BASE` before it syncs or starts the paid job; keep checks that can fail before any spend at the top.
- Rows carry `provenance` (teacher, license, hash, redactions, and `origin`: `examples.origin`, or `live` when the row did not record one; the BOM counts them in `training.origins`) and `params` (the stored conditions, or None). Keep `provenance` all the way to `ratchet_manifest.json` next to the adapter. `train.py` renders the messages only and ignores every stored condition (`stop`, `response_format`, `tools`, `tool_choice`), so `dataset.rows` leaves a row with a `stop` out of training (the answer ends where the client's stop cut it, and the student would learn to stop there on the bare prompt); it still serves as a golden row. Change that rule only together with `train.py`.
- A train row whose answer is not valid under its task's current spec (`Task.valid`, FR-2.14) is left out and counted (`rows(..., counts=)`, `skipped_invalid` in `manifest.json`); golden rows stay, and `cascade_eval` skips a row whose reference is invalid before sampling the student and reports `golden_invalid_refs` (the halves alternate over the scored rows). The ledger keeps such rows, so loosening a spec brings them back.
- A row of a task that is pinned **now** is left out of both sets (`dataset.rows`, counted as `excluded_pinned` in `manifest.json`) and out of `cascade_eval` (`excluded_pinned` in the eval, only when there are some): INV-10 at read time, so pinning a task that already has data keeps it from the student and from the threshold. The ledger keeps the rows; unpinning brings them back. The registry decides (`tasks.get(name).pinned`), so Night School runs after `taskspec.install` (`live.main` calls it).
- Every variant of a prompt (the same task and messages under other conditions) shares its split (`Ledger._split`) and, for the frequent rule, its `seen` count: `dataset.rows` sums `seen` over the hash without conditions (`_prompt_key`).
- Each rule above has a direct test in `tests/test_nightschool.py` (skip reasons, held-out golden rows, variants sharing a split and a count, stop rows held out of training, cumulative retraining from the base, held-out grading, refused rows, champion re-score). Use `RecordingTrainer` and `RecordingEvaluator` from `tests/fakes.py` to test a new rule.
