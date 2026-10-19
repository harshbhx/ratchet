# CLAUDE.md: `sim/` (offline simulation)

Mock models that let the whole loop run in seconds with no keys or GPUs. Used by `make sim`, the mock dashboard, and `tests/test_system.py`.

- `tickets.py`: 20 synthetic support-ticket templates (6 categories, 4 priorities, "easy"/"hard"), plus 3 drift templates that appear from `drift_day` (a product launch). `REGISTRY` maps ticket text → ground truth so mock models can look it up.
- `mock_models.py`:
  - `MockTeacher`: always right on labels, 2.4 s latency, bills 300 reasoning tokens.
  - `MockStudent`: skill per template = `max(0.35, ceil × (1 − e^(−n/6)))` with ceil 0.985 easy / 0.86 hard; exact texts it trained on are known. When it does not know a ticket, 15% of the time its samples agree on the **same wrong answer** with fairly high probability (the case audits and shadow exist for).
  - `MockFleet`: trainer + evaluator + resolver. `corrupt_on_deploy` breaks billing/refund/security for a version once it reaches canary/live (fault injection for rollback demos).
- `simulate.py`: CLI (`--days --requests --drift-day --bad-deploy-version --seed`) and `run(args)` for tests.

Rules:

- These parameters are assumptions, not measurements. Never present simulation output as real model quality.
- If you change mock parameters, re-run `make sim` with seeds 1, 2, 3, 7, 11 and update [docs/TESTING.md](../docs/TESTING.md) if the trend changes.
- Keep it deterministic: all randomness goes through seeded `random.Random` or `_rng(...)`.
