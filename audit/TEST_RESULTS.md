# Test results

Environment: Linux container, Python 3.13.16, Node 22.22.0, npm 10.9.4, ruff 0.16.9, pytest with xdist (`-n 8`). No
network access to Nebius or Tavily and no credentials for them; Docker CLI present without a daemon. Note: the `pytest`
on this machine's PATH is a separate uv-tool install without the project's dependencies, so every run uses
`python -m pytest`.

## Baseline (`3bfa5ab`, before any change)

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check .` | pass |
| Format | `ruff format --check .` | pass (228 files) |
| Traceability | `python scripts/reqtrace.py --check` | OK: 215 requirements, 0 known gaps |
| Config | `RATCHET_MODE=mock python -m ratchet.config check` | ok |
| Reference workloads | `python scripts/reference_check.py` | 3/3 match (32 s) |
| Simulation smoke | `python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3` | runs; v3 rolled back on day 4 (simulation) |
| Web | `npx tsc -b`, `npm test`, `npm run build` | pass; rebuilt dist identical to the committed one |
| Suite | `python -m pytest -q -n 8` | **1,760 passed, 4 failed, 13 skipped, 4 xfailed** (175 s) |

The 4 baseline failures: `test_story_trust_claims_match_code` and `test_task_registry_contract` (web drift, RAT-019),
`test_allowlists_and_token_caps_through_the_gateway` (stale assertion, RAT-020) and `test_perf_guard_200k_requests`
(timing). The run also logged `RuntimeError: release unlocked lock` from the `ratchet-retention` thread (RAT-008).

## Final (`81b8c79` plus the audit documents)

| Check | Command | Result |
|---|---|---|
| Lint, format | `ruff check .`, `ruff format --check .` | pass (469 files) |
| Traceability | `python scripts/reqtrace.py --check` | OK: 215 requirements, 1,237 of 1,309 test functions carry an id, 0 known gaps |
| Config | `RATCHET_MODE=mock python -m ratchet.config check` | ok |
| Reference workloads | `python scripts/reference_check.py` | tickets-seed7, mixed-seed1, research-seed7 match v0.4.0 |
| Pinned simulation | `python scripts/regen_sim_reference.py --check` | matches a fresh run |
| Web | `npx tsc -b`; `npm test`; `npm run build` | pass; 37 node tests; dist rebuilt byte-identical to the committed one |
| Suite, as CI runs it | `python -m pytest -q -n 8 --runslow` | **1,810 passed, 1 failed, 4 xfailed** (221 s) |

The one failure is `tests/test_perf.py::test_perf_guard_200k_requests` (a timing ceiling of 250 ms; 1.5 s here). It fails
the same way at the baseline commit on this machine (checked by checking out `3bfa5ab` and running it alone), so it is
this container's speed, not a regression; CI's hardware is what its bound was set for. The 4 xfails are strict
expected failures for planned work (the single-writer lease, SM-5).

## New regression tests (each fails without its fix)

| Test | Finding |
|---|---|
| `test_release.py::test_sequential_canary_moves_compare_and_set`, `test_sequential_gate_never_revives_a_version_moved_under_it`, `test_sequential_gate_holds_promotion_while_releases_are_frozen` | RAT-001 |
| `test_privacy.py::test_every_chat_shaped_route_honours_the_storage_opt_out` (6), `test_sdk_retries_and_idempotency_keys_on_completions_and_responses` (2) | RAT-002, RAT-003 |
| `test_calibration.py::test_the_certificate_counts_what_per_task_thresholds_serve`, `test_learn_then_test_holds_for_each_tasks_threshold` | RAT-004, RAT-005 |
| `test_resilience.py::test_a_cut_off_answer_is_never_paid_for_twice`, `test_an_unwritable_or_absurd_teacher_answer_never_crashes_after_it_was_paid` (4) | RAT-006, RAT-007, RAT-016 |
| `test_hardening.py::test_retention_purge_never_releases_the_gate_lock` | RAT-008 |
| `test_redaction.py::test_identifiers_grouped_with_unicode_separators_are_redacted` (6) | RAT-009 |
| `test_feedback.py::test_a_correction_is_redacted_as_the_teachers_answer_is` (2) | RAT-010 |
| `test_limits.py::test_a_completions_batch_counts_each_prompt_against_the_rate`, `test_no_train_and_erasure_writes_count_against_the_rate`, `test_a_keys_task_scope_covers_embeddings` | RAT-011, RAT-013, RAT-018 |
| `test_routing.py::test_replay_counts_rule_escalations_as_unserved` | RAT-012 |
| `test_hardening.py::test_live_alert_fires_once_per_version_across_gates` (2) | RAT-014 |
| `test_live.py::test_caddy_guard_covers_every_paid_route` (extended) | RAT-015 |
| `test_hardening.py` append-only cases (extended) | RAT-017 |
| `test_live.py::test_train_job_waits_for_the_file_train_py_writes_last` | RAT-026 |
| `web/tests/lifecycle-model.test.mjs` | explorer content held to the repo |

## Not run, and why

| Check | Why |
|---|---|
| `make day1`, `make live-smoke`, `make live-run` | No `NEBIUS_API_KEY`; they also spend money and need the owner's authorization |
| A Serverless training job, the student endpoint, object storage sync | No Nebius credentials or CLI; GPU spend |
| Tavily search | No key |
| `docker build` | Docker daemon not running |
| `caddy validate` on the new Caddyfile | No Caddy binary; the matcher syntax (method plus path with a middle wildcard) should be validated on the VM |
| Screen reader, browsers other than Chromium | Not available |
