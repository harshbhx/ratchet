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

To add one, copy the format of an existing ADR, take the next number, and link it here.
