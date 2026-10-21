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
| [0010](0010-signed-adapter-bom.md) | A signed bill of materials per adapter (amended: schema v2, licenses travel with the adapter) | Accepted | 2026-10-20 |
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
| [0025](0025-anytime-valid-release-gate.md) | Anytime-valid release gate: e-process rollback, evidence-based promotion and confidence sequences from two integers per version | Accepted | 2026-10-20 |
| [0026](0026-certified-thresholds-and-a-paired-release-gate.md) | Certified thresholds and a paired release gate: Clopper–Pearson certificates and a frontier in every BOM, opt-in Learn-then-Test and an exact paired champion/challenger gate | Accepted | 2026-10-20 |
| [0027](0027-committed-training-sets.md) | Committed training sets: sparse Merkle roots in every BOM, with membership, non-membership and held-out proofs checked offline | Accepted | 2026-10-20 |
| [0028](0028-designed-propensity-replay.md) | Designed-propensity replay: a ledger time machine for thresholds and audit rates, and a calibrated baseline | Accepted | 2026-10-21 |
| [0029](0029-verified-savings-receipts.md) | Verified savings receipts: an audit-calibrated baseline billed on its lower bound, in signed, chained RFC 9162 receipts | Accepted | 2026-10-21 |
| [0080](0080-storage-opt-outs-and-pseudonymous-end-users.md) | Storage opt-outs at every level, and end users kept only as scoped keyed hashes | Accepted | 2026-10-20 |
| [0081](0081-retention-tombstones-erasure-and-export.md) | Retention: tombstones, erasure and export | Accepted | 2026-10-20 |
| [0090](0090-licenses-travel-with-the-adapter.md) | Licenses travel with the adapter (base-model license in the BOM, NOTICE.md, export), and open teachers addable by configuration without weakening invariant 1 | Accepted | 2026-10-20 |
| [0091](0091-human-labels-as-a-provenance-source.md) | Human labels as a provenance source: request ids, verdicts and corrections (`POST /v1/ratchet/feedback`), labelled-data import that refuses closed-model labels, golden-only rows | Accepted | 2026-10-21 |
| [0100](0100-dashboard-task-writes-and-derived-breaker-state.md) | The dashboard writes API tasks with a key it holds in memory, and reads breaker states from the event log | Accepted | 2026-10-20 |
| [0130](0130-reasoning-by-role.md) | Reasoning by role: Nemotron thinking off for the student, set per role for the teacher | Accepted | 2026-10-21 |
| [0145](0145-train-on-the-served-prompt.md) | Train on the prompt the student is served, in a pinned training image | Accepted | 2026-10-21 |
| [0120](0120-judge-access-and-a-persistent-scout-guard.md) | Judge access and a persistent Scout guard: spend rows restored at startup, a judges' reserve behind a POST-once code and a signed cookie, paced Tavily credits and a labelled search-unavailable path | Accepted | 2026-10-21 |
| [0121](0121-live-measurement-method.md) | Live measurement method: synthetic traffic through the public API in rounds, a measured all-Ultra control arm and one results file behind every live number | Accepted | 2026-10-21 |
| [0060](0060-hot-path-caches-and-throttled-tick.md) | The hot path: a WAL ledger, a version routing snapshot, incremental tallies, a throttled tick and cached stats | Accepted | 2026-10-20 |
| [0061](0061-endpoint-access-levels.md) | Endpoint access levels: a private dashboard API in live mode, scrubbed public reads and a live auth guard | Accepted | 2026-10-20 |
| [0062](0062-request-ids-failures-logs-and-metrics.md) | Request ids end to end, a failures table with spent cost, structured logs without content, and Prometheus metrics | Accepted | 2026-10-20 |
| [0070](0070-configuration-checks-and-readiness.md) | Configuration checks that list every bad variable, a config check CLI, liveness apart from readiness, and `/readyz` with bounded dependency probes | Accepted | 2026-10-21 |
| [0071](0071-idempotency-keys-and-retry-de-duplication.md) | Idempotency keys, and retries that never count as repeats | Accepted | 2026-10-21 |
| [0072](0072-off-path-audits-and-shadow.md) | Off-path audits and shadow: a bounded sidecar, the same draws and calls, pending rows the gate ignores, and audits that are never dropped | Accepted | 2026-10-21 |
| [0063](0063-spend-and-rate-limits.md) | Spend and rate limits: per-key budgets, a gateway budget with a student-only mode, and limits that refuse instead of queueing | Accepted | 2026-10-21 |
| [0064](0064-admin-api-compare-and-set-and-audit-trail.md) | Admin API, compare-and-set transitions, audit trail: one module for manual changes (HTTP and CLI), runtime switches and pins, and release moves that two processes make once | Accepted | 2026-10-21 |
| [0040](0040-per-task-calibration.md) | Per-task calibration when several tasks share one adapter: thresholds, a per-task golden gate, and per-task alerts | Accepted | 2026-10-20 |
| [0041](0041-generic-mock-world.md) | A generic mock world: every request gets a deterministic answer in mock mode, keyed by what a model sees | Accepted | 2026-10-20 |
| [0042](0042-auto-tasks-from-response-format-schemas.md) | Structured outputs: a response_format is a contract, and a json_schema names an auto task | Accepted | 2026-10-21 |
| [0043](0043-tool-calls-teacher-only-by-default.md) | Tool calls: teacher-only by default, opt-in distillation later | Accepted | 2026-10-21 |
| [0048](0048-model-field-routing.md) | The model field names a task, never an upstream model; `/v1/models` lists the tasks; an Azure-style route | Accepted | 2026-10-20 |
| [0049](0049-risk-aware-routing.md) | Risk-aware routing: eligibility, escalation rules on the student's answer, a critical-class miss-rate gate, and a per-task audit rate | Accepted | 2026-10-21 |
| [0050](0050-adjacent-openai-endpoints.md) | The other OpenAI endpoints: an embeddings pass-through, completions and stateless responses on the chat path, typed 404s | Accepted | 2026-10-21 |
| [0054](0054-free-text-similarity-and-a-judge-gate.md) | Free-text tasks: a similarity comparator, per-task sample counts and a judge gate; long-form stays pinned | Accepted | 2026-10-21 |
| [0055](0055-sql-tasks-compared-by-execution.md) | Text-to-SQL tasks compared by what their queries return, in a sandboxed in-memory SQLite fixture | Accepted | 2026-10-21 |
| [0082](0082-placeholder-styles-detector-plugin-and-indian-identifiers.md) | Placeholder styles for extraction tasks, a detector plugin, and Indian identifiers and amounts | Accepted | 2026-10-21 |
| [0083](0083-success-metrics-from-the-ledger.md) | Success metrics computed from the ledger, the same way in mock and live mode | Accepted | 2026-10-21 |
| [0032](0032-label-free-drift-alarm.md) | A label-free drift alarm: a conformal test martingale on each version's own confidence stream, with opt-in actions | Accepted | 2026-10-21 |
| [0033](0033-teacher-watch.md) | Teacher watch: free self-consistency checks with a first-re-test rule, quarantine and adjudication, a training freeze by e-process, and the sqrt(A) ceiling | Accepted | 2026-10-21 |
| [0131](0131-golden-prompts-are-golden-in-every-task.md) | A prompt that is golden under one task is golden under every task, and occurrences count across tasks | Accepted | 2026-10-22 |
| [0073](0073-fallback-attribution.md) | Fallback attribution: an answer is a version's only when its own adapter wrote it; the hosted fallback is served, recorded and never counted toward a release | Accepted | 2026-10-21 |

To add one, copy the format of an existing ADR, take the next number, and link it here.
