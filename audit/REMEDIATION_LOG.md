# Remediation log

Branch `claude/epic-mendel-c2kbrx`, from `3bfa5ab`. Every fix followed the same steps: reproduce, write a test that
fails, make the smallest change, rerun the focused tests, stash the fix and confirm the new test fails without it, run
the broader suites and `scripts/reference_check.py`, update the docs that describe the behaviour, commit.

| Commit | Findings | Files | Tests added or changed |
|---|---|---|---|
| `ea3fa26` | RAT-001, RAT-008, RAT-020 | `ratchet/release.py`, `ratchet/gateway.py`, CHANGELOG | `test_release.py`: 3 sequential-gate tests; `test_hardening.py::test_retention_purge_never_releases_the_gate_lock`; stale assertion in `test_openai_compat.py` corrected |
| `82a5d90` | RAT-002, RAT-003 | `ratchet/gateway.py` (`_chat_shaped_route`, `_complete_idempotent(run=, route=)`), `ratchet/openai_compat.py`, ADR 0050, API.md | `test_privacy.py`: 6 opt-out cases on every chat-shaped route; retries and idempotency on completions/responses |
| `6afc5fb` | RAT-006, RAT-007, RAT-016 | `ratchet/providers.py` (`_post`, `_utf8`, `MAX_TOKEN_COUNT`), `ratchet/resilience.py` (`BodyCutOff`), `ratchet/gateway.py`, ADR 0014 | `test_resilience.py`: cut-off body, 4 unwritable/absurd answers; 2 parametrized expectations changed to `X-Should-Retry: false` |
| `d284bfd` | RAT-015 | `deploy/vm/Caddyfile`, DEPLOY_NEBIUS_VM.md, THREAT_MODEL.md | `test_live.py::test_caddy_guard_covers_every_paid_route` now enumerates every admin-level route |
| `aad1842` | RAT-012, RAT-014 | `ratchet/router.py`, `ratchet/replay.py`, `ratchet/release.py`, `ratchet/ledger.py` (`event_once` announces) | `test_routing.py::test_replay_counts_rule_escalations_as_unserved`; `test_hardening.py::test_live_alert_fires_once_per_version_across_gates` |
| `9e543ad` | RAT-011, RAT-013, RAT-018 | `ratchet/ratelimit.py` (`charge`), `ratchet/obs.py` (`rate_bucket`), `ratchet/gateway.py` (`_charge_rate`, `_limited`), CONFIGURATION.md | `test_limits.py`: 3 tests |
| `a510c48`, `1ad6a20` | RAT-004, RAT-005 | `ratchet/nightschool/evaluate.py`, nightschool CLAUDE.md, ADR 0026 | `test_calibration.py`: 2 tests; the switched-off test now excludes the certificate's parts from its equality |
| `69f32e6` | RAT-009 | `ratchet/redact.py` (`SEPARATORS`, `_fold`), `ratchet/bom.py` (redactor hash), ADR 0082 | `test_redaction.py`: 6 cases |
| `a620b39` | RAT-010 | `ratchet/feedback.py` (`prepare(style=, detector=)`, `redaction_for`, `Prepared.canon`), `ratchet/importer.py` | `test_feedback.py`: 2 cases |
| `c07461d` | RAT-017 | `ratchet/ledger.py` (two triggers) | `test_hardening.py` append-only cases; `test_receipts.py` purge test no longer pre-acquires the gate lock (it had been written around RAT-008 and hung once that was fixed) |
| `78b1a31` (merged in `5d8d364`) | RAT-019 | `web/src/pages/landing/trust-model.ts`, `web/src/pages/dashboard/registry-model.ts`, `web/src/lib/api.ts`, rebuilt `ratchet/web/dist` | existing `test_contract.py` tests now pass |
| `7d1148a`, `d2668b9`, `375f530` (merged) | design work | `web/src/components/ui/logo.tsx`, `web/public/favicon.svg`, `sections/hero.tsx`, `sections/lifecycle.tsx`, `lifecycle-model.ts`, `design/` | `web/tests/lifecycle-model.test.mjs` (every file named exists, every number equals the Python default) |
| `e2601cd` | RAT-019 follow-up | `tests/fixtures/tasks_keys.json` (`sql`, `privacy`) | contract fixture |
| `59ab018` | RAT-026 | `deploy/train_job.sh`, deploy CLAUDE.md | `test_live.py::test_train_job_waits_for_the_file_train_py_writes_last` |
| (after the report) | RAT-023 | `ratchet/access.py` (`pseudonymize`, `PRIVATE_COLUMNS`) | `test_security.py::test_scrub_helpers` cases |
| `b2cf656`, `81b8c79` | bookkeeping | CHANGELOG, `docs/REQUIREMENTS.md` (regenerated), documented test counts | - |

## What did not move

- `scripts/reference_check.py`: all three workloads (tickets seed 7, mixed seed 1, research seed 7) still reproduce
  v0.4.0 after every behavioural commit.
- `scripts/regen_sim_reference.py --check`: the pinned reference simulation matches.
- No dependency was added (Python or npm). No schema column was added; two triggers were.
- No test was skipped, deleted or weakened. Four existing assertions were changed, each because it pinned the defect
  being fixed: the `logit_bias` student assertion (ADR 0017), the two bad-2xx `X-Should-Retry` cases, the per-task
  switched-off equality on the certificate, and the receipts test's pre-acquired lock.

## Not changed, deliberately

- RAT-022 (cross-task golden rule over time) needs an amendment to ADR 0131's design; left with a proposal.
- RAT-024 (one tenant's erasure tombstoning a shared example) follows from a documented policy (`RATCHET_ERASURE_SHARED`);
  changing it is a product decision.
- RAT-021 (`main` is an unrelated history) needs the owner: this session pushes only to its feature branch.
