# Lane F: proposed invariants

Invariants lane F proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id. Each entry names its text, the code that enforces it and the tests that prove it.

## Anytime-valid release gate (item anytime-valid-release, FR-16.1, FR-16.2, ADR 0025)

### Release decisions are recomputable from two counts per version

**Text.** Every release-gate decision on a canary or live version (rollback, promotion, the live alert, retirement as `inconclusive`) is computed from `Ledger.audit_counts(vid, since_id)` and `ReleasePolicy` alone, and `ReleaseGate.evidence(v)` recomputes the same numbers. The sequential rollback is one e-process over every audit of the canary's life: never restart it per stage and never store gate state that the ledger cannot reproduce (the in-memory once-per-version alert set is the only exception). Thresholds are compared in log space.

**Enforcing code.** `ratchet/release.py`: `ReleaseGate._sequential_canary`, `ReleaseGate._tick_live`, `ReleaseGate.evidence`; `ratchet/ledger.py`: `Ledger.audit_counts`.

**Proving tests.** `tests/test_release.py::test_sequential_rollback_counts_every_audit_of_the_canarys_life`, `test_evidence_is_recomputed_from_the_counts`, `test_sequential_canary_rolls_back_after_two_failures`, `test_live_alert_uses_the_e_process`; `tests/test_stats.py::test_alt_grid_components_are_supermartingales` (the validity argument).

### Every audit decision is one logged draw

**Text.** A served student answer gets exactly one `rng.random()` draw for its audit, compared with the rate in force for its version (`RATCHET_CANARY_AUDIT_RATE` for a canary when set, else `RATCHET_AUDIT_RATE`), and that rate is stored as `requests.label_p` (1.0 when the student answered and the request escalated). A change to how audits are drawn must keep the draw count and log the probability, or the ledger replay and the receipts become biased.

**Enforcing code.** `ratchet/router.py`: `Ratchet.handle`; `ratchet/config.py`: `Settings.checked_canary_audit_rate`.

**Proving tests.** `tests/test_release.py::test_canary_audit_rate_focuses_audits_on_the_canary`, `test_label_p_is_the_designed_labelling_probability`.

## Certified precision (item certified-precision, FR-16.3, FR-6.30, FR-6.31, ADR 0026)

### A certificate is measured only on rows its threshold was not chosen on

**Text.** A version's `certificate`, `frontier` and `per_task` are computed from the held-out (odd) golden rows at the threshold chosen on the calibration (even) rows, and that threshold is stored exactly as measured, so the router serves at it. Learn-then-Test and the frontier use the fixed grid `stats.CERT_GRID`; never a grid built from observed confidences, never a bound computed on calibration rows, never a threshold rounded after it was measured.

**Enforcing code.** `ratchet/nightschool/evaluate.py`: `cascade_eval`; `ratchet/certificate.py`: `certificate`, `frontier`, `per_task`; `ratchet/stats.py`: `CERT_GRID`, `ltt_fixed_sequence`.

**Proving tests.** `tests/test_nightschool.py::test_cascade_eval_certifies_on_the_held_out_half`, `test_cascade_eval_ltt_picks_a_grid_threshold`; `tests/test_hardening.py::test_stored_threshold_serves_what_the_eval_measured`; `tests/test_stats.py::test_certificate_on_the_held_out_half_holds_at_its_rate`, `test_ltt_controls_the_error_rate`.

### Paired comparisons only on the same rows, signed before admission

**Text.** A candidate's `paired` record compares its test vector with the live version's from the champion re-score of the same night, on the same golden rows in the same order, names that version (`against`), and is stored before `update_eval` and `set_bom`, so the signed BOM carries the counts the gate decides on. The paired gate acts only on a record against the current live version and recomputes its p-values from the counts; anything else falls back to the unpaired rule.

**Enforcing code.** `ratchet/nightschool/orchestrate.py`: `run_night`, `paired_evidence`; `ratchet/release.py`: `ReleaseGate.admit`, `ReleaseGate._paired_regression`, `_paired_counts`.

**Proving tests.** `tests/test_nightschool.py::test_run_night_pairs_the_candidate_with_the_rescored_live_version`, `test_paired_gate_admits_what_the_unpaired_rule_rejects_on_noise`; `tests/test_release.py::test_paired_gate_rejects_only_large_significant_regressions`.

### Every certified number is recomputable from signed counts

**Text.** A certificate, frontier row, per-task row, Learn-then-Test step and paired p-value is a function of integers the eval states (served, errors, n, k, discordant counts) and of the stated level, and the test vector reproduces the certificate's counts. `certificate.recheck` (and so `python -m ratchet.stats check`) recomputes each within 1e-9; the gate recomputes the lower bound and the p-values instead of reading stored ones. A new certified number gets a recomputation in `recheck` in the same change.

**Enforcing code.** `ratchet/certificate.py`: `recheck`, `paired_stats`; `ratchet/stats.py`: `main` (`check`); `ratchet/release.py`: `_below_certified_floor`, `_certified_text`, `_paired_counts`.

**Proving tests.** `tests/test_core.py::test_recheck_recomputes_every_number_from_the_counts`, `test_recheck_follows_the_ltt_trace`; `tests/test_provenance.py::test_certificate_endpoint_and_stats_check_on_a_signed_bom`; `tests/test_release.py::test_min_certified_precision_rejects_a_weak_certificate`.
