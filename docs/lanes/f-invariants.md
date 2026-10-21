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

## Committed training sets (item committed-training-sets, FR-16.4, ADR 0027)

### Every signed set commitment has its leaves in the ledger, from the exact rows

**Text.** For every version it trains, `run_night` builds the set commitment of exactly the rows it hands to `trainer.train` (split `train`) and to `evaluator.evaluate` (split `golden`) before the training job, writes their leaves once right after `create_version` and before the eval and the BOM, and the BOM signs exactly those roots and sizes (a size that is not the split's row count is refused). Leaves are never written for a version that already has them and never changed afterwards (the append-only triggers); a night whose training fails writes no version and no leaves. Changing what a leaf binds (`dataset.leaf_value`) or how the tree hashes needs a new `alg` and an ADR.

**Enforcing code.** `ratchet/nightschool/orchestrate.py`: `run_night`; `ratchet/nightschool/dataset.py`: `commitment`, `leaf_value`; `ratchet/ledger.py`: `Ledger.set_version_leaves`, the `version_leaves` triggers; `ratchet/bom.py`: `manifest`, `_commitment`.

**Proving tests.** `tests/test_provenance.py::test_night_commits_the_exact_rows_it_trained_and_graded_on`, `test_version_leaves_are_written_once_and_found_by_key`, `test_a_failed_training_job_commits_nothing`, `test_commitment_binds_each_examples_label_teacher_license_and_redactions`; `tests/test_system.py::test_every_version_proves_its_golden_rows_were_never_trained_on` (invariants 3 and 6 checked from the signed roots over a simulated bad deploy).

### Proofs are judged against the signed root only

**Text.** A membership or non-membership proof, and a held-out certificate, is checked against the root and size the version's BOM signs, never against the root, `result` or `value` the proof file states, and the verifiers return a failure (never an exception) for any malformed input. The gateway serves a proof only from a tree that rebuilds the signed root and size; leaves that do not (a ledger edited outside Ratchet) get a 500 and an `integrity` event, never a proof.

**Enforcing code.** `ratchet/proof.py`: `verify_proof`, `verify_heldout`; `ratchet/bom.py`: `commitment`; `ratchet/merkle.py`: `verify`; `ratchet/gateway.py`: `_keyset`.

**Proving tests.** `tests/test_provenance.py::test_an_edited_leaf_fails_against_the_signed_root`, `test_verifiers_never_raise_on_malformed_files`, `test_proofs_and_the_held_out_certificate_through_the_gateway`, `test_tampered_proofs_never_prove_inclusion`.

### Training-set proofs are admin-only, and their key is the stored key

**Text.** `POST /ratchet/versions/{id}/proof` and `GET /ratchet/versions/{id}/heldout` take the admin dependency (`_admin`: 401, 403, off in live mode without `RATCHET_ADMIN_KEYS`), because with guessable texts they are a membership oracle for the training data; the sample endpoint is mock-only. The key a proof request resolves to is computed exactly as `Ratchet._store_example` stores a teacher answer (validated, normalised, redacted when redaction is on, with the stored conditions), so an `absent` answer is never an artefact of a different hash.

**Enforcing code.** `ratchet/gateway.py`: `version_proof`, `version_heldout`, `demo_sample_example` (`_mock_only`); `ratchet/proof.py`: `request_key`, `resolve_key`.

**Proving tests.** `tests/test_provenance.py::test_proof_endpoints_need_an_admin_key_in_live_mode`, `test_request_key_is_the_stored_hash`, `test_resolve_key_validates_like_the_gateway`; `tests/test_contract.py::test_demo_endpoints_are_403_in_live_mode`.

## Ledger time machine (item ledger-time-machine, FR-16.5, ADR 0028)

### Replay labels come only from designed draws, and a replay reads one version

**Text.** `requests.student_agree` is set only from the primary teacher's valid answer to the request's own escalation (labelling probability 1) or random audit (the logged audit rate), compared with the student's valid answer; never from a shadow arbitration (it runs only on disagreements, so its probability is unknown and depends on correctness), a fallback-teacher or degraded answer, or any other teacher call that was not drawn by the design. `requests.threshold` is the exact value the answer was compared with. The request path only logs these (no model call, no random draw). The replay weights each label by 1 / `label_p`, reports a null precision with its reason (never an extrapolation) when a row it counts could never be labelled, never pools versions, and its suggested threshold is advice that nothing applies: thresholds change only through Night School. A new source of labels must come with its designed probability, or stay out of `student_agree`.

**Enforcing code.** `ratchet/router.py`: `Ratchet.handle`, `Ratchet._formula_baseline`; `ratchet/ledger.py`: `Ledger.replay_rows`; `ratchet/replay.py`: `at`, `sweep`, `_Acc.add`, `suggest`; `ratchet/gateway.py`: `_replay_version`.

**Proving tests.** `tests/test_contract.py::test_replay_columns_are_logged_without_extra_calls`; `tests/test_system.py::test_label_p_and_student_agree_follow_the_design`, `test_replay_reproduces_each_versions_logged_coverage_and_audits`; `tests/test_core.py::test_replay_precision_is_unbiased_under_the_designed_labelling`, `test_replay_without_positivity_gives_no_precision_above_the_live_threshold`, `test_replay_suggests_the_lowest_passing_grid_threshold_or_none`; `tests/test_hardening.py::test_replay_endpoint_refuses_to_mix_versions`.
