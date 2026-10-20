# CLAUDE.md: `ratchet/nightschool/`

One night = `orchestrate.run_night(ledger, trainer, evaluator, gate, base_model)`:

1. Skip if a version is in `shadow` or `canary` (release in flight).
2. Build the dataset (`dataset.build`): train rows + golden rows + `manifest.json` with the bill of materials.
3. Skip if fewer than `min_new_examples` new train examples, or no golden rows.
4. Re-score the **live** version on the current golden set (champion vs challenger).
5. `trainer.train(...)` → adapter name (e.g. `student-v4`).
6. `evaluator.evaluate(adapter, golden_rows)` → `{precision, coverage, threshold, golden_agreement, ...}`.
7. `gate.admit(vid)`.

## Interfaces

- `Trainer.train(train_rows, base_model, name) -> adapter_uri`: mock = `sim.mock_models.MockFleet`; live = `ratchet.live.NebiusTrainer` (Serverless Job + runtime LoRA load).
- `Evaluator.evaluate(adapter_uri, golden_rows) -> dict`: both implementations use `evaluate.cascade_eval` and sample the student with `confidence.STUDENT_SAMPLING`, the router's parameters, so mock, live and production agree (FR-5.1; `test_production_and_both_evaluators_share_student_sampling`).

## Rules

- `cascade_eval` calibrates the threshold on even-indexed golden rows and reports precision/coverage on odd-indexed rows. Do not grade on calibration data.
- Thresholds are clamped to `[floor=0.5, 1.01]`; 1.01 means "serve nothing" (keeps JSON finite).
- Train from the base model on the cumulative dataset every night; do not stack adapters.
- `train.py` runs inside the GPU container only, and `main` is excluded from tests. Its arguments (`parse_args`), the row contract (`load_rows` reads what `NebiusTrainer` writes) and `manifest` (`ratchet_manifest.json`) are tested offline in `tests/test_live.py`. LoRA target modules for Nemotron-H must be confirmed on day 1 (`--targets`).
- `ratchet.live.NebiusTrainer` checks `RATCHET_STUDENT_BASE` before it syncs or starts the paid job; keep checks that can fail before any spend at the top.
- Rows carry `provenance` (teacher, license, hash). Keep it all the way to `ratchet_manifest.json` next to the adapter.
- Each rule above has a direct test in `tests/test_nightschool.py` (skip reasons, held-out golden rows, cumulative retraining from the base, held-out grading, champion re-score). Use `RecordingTrainer` and `RecordingEvaluator` from `tests/fakes.py` to test a new rule.
