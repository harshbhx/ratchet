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
