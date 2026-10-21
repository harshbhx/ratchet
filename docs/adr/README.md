# Architecture Decision Records

Each ADR records one significant decision: the context, what was decided, the consequences and the alternatives considered. Status values are Proposed, Accepted, Superseded and Deprecated.

| # | Title | Status | Date |
|---|---|---|---|
| [0001](0001-open-teacher-only.md) | Distill only from teachers whose license allows it | Accepted | 2026-10-19 |
| [0002](0002-student-on-nebius-serverless-not-token-factory.md) | Train and serve the student on Nebius AI Cloud, not Token Factory | Accepted | 2026-10-19 |
| [0003](0003-per-request-cascade-with-calibrated-confidence.md) | Per-request cascade with a calibrated, per-version confidence threshold | Accepted | 2026-10-19 |
| [0004](0004-retrain-from-base-cumulative.md) | Retrain from the base model on the cumulative dataset every night | Accepted | 2026-10-19 |
| [0005](0005-shadow-canary-rollback-with-wilson-bounds.md) | Release through shadow and canary, roll back on Wilson upper bound | Accepted | 2026-10-19 |
| [0006](0006-sqlite-ledger-for-v0.md) | SQLite ledger for v0 | Accepted | 2026-10-19 |
| [0007](0007-golden-set-split-and-frequent-requests.md) | Hash-based golden split; frequent requests always train | Accepted | 2026-10-19 |
| [0008](0008-pinned-tasks.md) | Pinned tasks stay on the teacher and are never training data | Accepted | 2026-10-20 |
| [0009](0009-pattern-based-pii-redaction.md) | Pattern-based PII redaction before examples are stored | Accepted | 2026-10-20 |
| [0010](0010-signed-adapter-bom.md) | A signed bill of materials per adapter | Accepted | 2026-10-20 |
| [0011](0011-requirements-traceability.md) | Requirements traceability from PRD rows and test markers, checked in CI | Accepted | 2026-10-20 |
| [0012](0012-openai-request-validation-and-error-envelope.md) | Validate and normalise OpenAI requests at the gateway, and answer errors in OpenAI's envelope | Accepted | 2026-10-20 |
| [0013](0013-optional-teacher-calls-fail-open.md) | Optional teacher calls fail open for the request and count against the shadow in the release gate | Accepted | 2026-10-20 |
| [0014](0014-upstream-failure-semantics.md) | Upstream failure semantics: bounded retries, one error type, and statuses a client can act on | Accepted | 2026-10-20 |
| [0015](0015-student-circuit-breaker-and-adapter-reload.md) | A circuit breaker per student endpoint, automatic adapter reloads, and a route on every request | Accepted | 2026-10-20 |
| [0016](0016-store-only-complete-valid-teacher-answers.md) | Store only complete, valid teacher answers | Accepted | 2026-10-20 |
| [0017](0017-generation-parameters-by-role.md) | Generation parameters by role, token caps, and the conditions stored with an example | Accepted | 2026-10-20 |
| [0018](0018-usage-reports-the-served-answer.md) | `usage` reports the served answer; Ratchet's own calls go in a breakdown | Accepted | 2026-10-20 |
| [0019](0019-teacher-breaker-degraded-mode-and-fallback-teacher.md) | When the teacher is down: a teacher breaker, an opt-in degraded mode and an opt-in licensed fallback teacher | Accepted | 2026-10-20 |
| [0020](0020-n-choices-and-token-logprobs.md) | `n` choices and token logprobs without moving the calibration or the training set | Accepted | 2026-10-20 |
| [0021](0021-field-comparators-and-task-specs.md) | Field comparators and task specs: typed fields, required by default, pairwise confidence | Accepted | 2026-10-20 |
| [0022](0022-task-registry.md) | A task registry: specs as data, operator-only pinning, an unknown-task policy and admin keys | Accepted | 2026-10-20 |
| [0023](0023-utilization-aware-cost-accounting.md) | Utilization-aware cost accounting: a split per request, a compute-time ledger, savings by GPU-hours and a self-host recommendation | Accepted | 2026-10-20 |
| [0024](0024-evidence-foundations.md) | Foundations for the evidence features: exact statistics, Merkle trees, one schema change with append-only tables, reserved settings and three pinned workloads | Accepted | 2026-10-20 |
| [0080](0080-storage-opt-outs-and-pseudonymous-end-users.md) | Storage opt-outs at every level, and end users kept only as scoped keyed hashes | Accepted | 2026-10-20 |
| [0081](0081-retention-tombstones-erasure-and-export.md) | Retention: tombstones, erasure and export | Accepted | 2026-10-20 |
| [0082](0082-placeholder-styles-detector-plugin-and-indian-identifiers.md) | Placeholder styles for extraction tasks, a detector plugin, and Indian identifiers and amounts | Accepted | 2026-10-21 |
| [0083](0083-success-metrics-from-the-ledger.md) | Success metrics computed from the ledger, the same way in mock and live mode | Accepted | 2026-10-21 |

To add one, copy the format of an existing ADR, take the next number, and link it here.
