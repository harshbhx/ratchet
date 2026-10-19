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
- `Evaluator.evaluate(adapter_uri, golden_rows) -> dict`: both implementations use `evaluate.cascade_eval`, so mock and live are graded the same way.

## Rules

- `cascade_eval` calibrates the threshold on even-indexed golden rows and reports precision/coverage on odd-indexed rows. Do not grade on calibration data.
- Thresholds are clamped to `[floor=0.5, 1.01]`; 1.01 means "serve nothing" (keeps JSON finite).
- Train from the base model on the cumulative dataset every night; do not stack adapters.
- `train.py` runs inside the GPU container only. It is excluded from tests. LoRA target modules for Nemotron-H must be confirmed on day 1 (`--targets`).
- Rows carry `provenance` (teacher, license, hash). Keep it all the way to `ratchet_manifest.json` next to the adapter.
