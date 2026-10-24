# ADR 0024: Foundations for the evidence features: exact statistics, Merkle trees, one schema change, reserved settings and three pinned workloads

- Status: Accepted
- **Update:** see [CONFIGURATION.md](../CONFIGURATION.md#reserved-settings) for what is still reserved; the counts in Consequences are as of this ADR's date.
- Date: 2026-10-20

## Context

v0.5 adds a set of features that turn trust claims into numbers a third party can check (PRD §6.16 and FR-15.5): an anytime-valid release gate, certified precision, committed training sets, a ledger replay, a drift alarm, a teacher watch, savings receipts, clean-room backfill and provable erasure. They share three things:

- the same statistics (exact binomial bounds, e-values, weighted estimators);
- the same two kinds of Merkle tree (a set commitment with absence proofs, and an append-only log);
- the same files: the ledger schema, `Settings`, `ReleasePolicy`, the BOM, `ReleaseGate.tick` and `run_night`.

Built one by one, each would add its own helpers, its own columns and migrations, and its own edit to every shared function. Two risks follow. Statistics that are slightly wrong (a peeked-at interval, an off-by-one in an exact bound) look fine in unit tests. And every feature must leave Ratchet's decisions exactly as they are unless it is switched on, but today only one workload (the seed-7 tickets run) pins them.

## Decision

Build the shared pieces once, before any feature, with defaults that change nothing.

- **`ratchet/stats.py`, standard library only.**
  - Binomial tails sum the smaller tail from one `lgamma` term and the ratio of neighbouring terms, so tiny p-values keep their relative accuracy and a call costs O(n) multiplications.
  - On top of them: one-sided Clopper-Pearson bounds (bisection, rounded down), the exact precision p-value, the sample-size planner `required_rows` (59 golden rows certify 95% with no error), the exact one-sided McNemar test and Learn-then-Test by fixed-sequence testing over the fixed `CERT_GRID` (never a grid built from the data).
  - Mixture e-values for a Bernoulli rate (`log_e_bernoulli`: half the weight on alternatives 0.01 to 0.10 from the null, half spread over the rest) and the confidence sequence they imply. Their validity argument is in the docstring: each component is a nonnegative supermartingale under the composite null, with no independence assumption, so Ville's inequality bounds the chance of ever crossing 1/alpha.
  - `wilson` moves here (`release.wilson` still imports), and the estimators the replay and receipts share: Kish's effective sample size, the Hajek mean and the ratio estimator.
- **`ratchet/merkle.py`, standard library only.**
  - `KeySet` is a collapsed binary sparse Merkle tree over 32-byte keys: an empty node is 32 zero bytes, a node with one key collapses to its leaf `sha256(0x00 || key || value)`, any other node is `sha256(0x01 || left || right)`, split on the key's bits. Its root depends on the set alone. A proof is the siblings along the key's path and what the path ends at, so absence is provable even against whoever built the tree: two verifying proofs for one key, one of inclusion and one of absence, would need a SHA-256 collision across the two domains or a preimage of zero.
  - A Merkle tree over a sorted list is not used for absence, because a verifier cannot check that the leaves are sorted.
  - RFC 9162 log trees (`mth`, `inclusion_path`, `verify_inclusion`, `LogTree`) are kept for append-only records. The tree size must come from the same signed record as the root: for some leaves an audit path also verifies against the same root under a nearby size.
  - Verifiers take untrusted input and answer `invalid` or `False`; they never raise.
- **One schema change** (`ledger.py`, in `SCHEMA` and `MIGRATIONS`, indexes in `INDEXES`), so no later item touches the migrations:
  - `requests` gains `threshold`, `label_p`, `student_agree`, `baseline_est_usd` and `optional_failed`.
  - `examples` gains `origin`, `job`, `contested` (default 0) and `checked_epoch` (default 0).
  - New tables: `version_leaves`, `teacher_checks`, `meta`, `receipts`, `spend`, `backfill_jobs`, `tombstones` and `erasures`.
  - Indexes: requests by (`student_version`, `audited`, `id`), by `shadow_version` and by `day`. These are what the gate, the shadow statistics and the per-day stats filter on; today each is a full scan per tick. Also `version_leaves(key)`, `teacher_checks(epoch)` and `erasures(status)`.
  - An index on a column that a hand-made partial file lacks is skipped (an index only speeds things up). A test checks that a new ledger has every index, so a misspelled column cannot hide.
  - Nothing writes the new columns yet; they stay NULL (or their default) until their feature lands.
- **INV-17: committed leaves and sealed receipts are append-only, enforced by the database.** Triggers refuse UPDATE and DELETE on `version_leaves` and `receipts`. They also refuse an INSERT that would replace a row: `INSERT OR REPLACE` deletes the old row without firing a DELETE trigger, and `INSERT OR IGNORE` is refused too, so a writer checks first. Key columns are NOT NULL, because SQLite lets a NULL slip past a composite primary key. A changed leaf or receipt would silently break the signed roots and chains built from it.
- **Reserved settings.**
  - Every new `Settings` and `ReleasePolicy` field is declared now (`config.RESERVED_SETTINGS`, `release.RESERVED_POLICY`), with a default that keeps today's behaviour.
  - Until its feature lands, any other value stops startup with a `ValueError` that names the variable and the feature. `Settings.check_reserved()` runs in `Ratchet.__init__` and `python -m ratchet.live`; `ReleasePolicy.__post_init__` runs on every construction. Without this, someone could set `RATCHET_SEQUENTIAL=1` and believe the anytime-valid gate was on.
  - The item that builds a feature removes its entries. Where the feature's own default differs from today's behaviour (`alert_eprocess`, `RATCHET_DB_SYNC`), today's value is the default until then.
  - `ReleasePolicy` now checks its ranges when it is built (`live_floor` strictly between 0 and 1, alphas in (0, 1), switches 0 or 1). `from_env` names the variable on a bad value.
- **BOM schema v2, as a scaffold.**
  - `bom.SCHEMA` is `ratchet.bom/v2` and `verify` accepts v1 and v2 (anything else is "unknown schema"). Stored v1 documents are never rewritten (INV-14).
  - A v2 manifest keeps every v1 field and adds `redactor` (a SHA-256 over the redaction rules, and whether redaction was on; `run_night(redact_pii=)`) and `training.origins` (training rows by `examples.origin`, `live` when not recorded).
  - Optional sections come later: `training.commitment` and `golden.commitment` (`manifest(commitments=)`, `{alg: ratchet.smt/v1, root, size}`, with the size checked against the rows), and the keys in `bom.EXTRA_KEYS` (`teacher`, `backfill_jobs`, `erasures_honored`; `manifest(extra=)` refuses any other key), filled from one `bom_extra` dict in `run_night`.
- **Fixed places for later edits.**
  - `ReleaseGate.tick` is five steps, each owned by one feature: `_tick_drift`, `_tick_live`, `_tick_shadow`, `_tick_canary`, `_tick_watchers`. The live version is read once at the start, so behaviour is byte-identical.
  - `run_night` documents where early-night checks, commitments, paired evidence and BOM sections go.
  - `Result.request_id` carries the ledger row id that `log_request` already returned. It is not `RequestMeta.request_id`, the caller's own id.
- **Three pinned workloads** (`scripts/reference_check.py`, `tests/fixtures/reference.json`, `make reference`, a CI step). The tickets run is the reference simulation; mixed (seed 1) and research (seed 7) run for 8 days of 1,500 requests. The fixture was captured from v0.4.0 (commit 1f9b18c). This change reproduces it exactly: every float, every event detail and every dataset hash.
  - The check pins every input, v0.4's per-day stats, the totals, each version's final state, size and dataset hash, and the event log.
  - Floats agree within a relative 1e-9, so a new index that adds the same numbers in another order does not fail it.
  - Days and totals may gain keys. Events compare as (day, kind, version) in order, and each pinned detail must be a prefix of the fresh one, so a feature may append evidence to a message but never change it. The advisory kinds later features add (`drift`, `teacher_drift`, `teacher_model_changed`, `warning`, `student_healed`) are left out.
  - The pytest suite checks the tickets run for free (the session's reference run), and the rules on hand-made differences.
- **A `slow` marker.** Full-size Monte Carlo runs are marked `slow`. They are collected, so the documented suite size is unchanged, but skipped unless pytest gets `--runslow`, as CI does. Each has a quick version (under a second) in the default suite.

## Consequences

- A feature fills in logic instead of touching shared plumbing, and it cannot change a decision by default without failing the reference check on one of three workloads.
- Several features' numbers rest on `stats.py` being exact. It is checked against hand-computed values for every oracle the feature specs give, against brute-force binomial sums, and by seeded Monte Carlo. At a true rate on the 92% floor, checked after every audit, the e-process falsely rolls back about 3% of good versions (alpha 5%), while Wilson's bound checked the same way does in about 23% (nominal 2.5%; simulation, 120 counts-only runs of 200 audits). That is the reason the gate needs an anytime-valid rule.
- New BOMs say `ratchet.bom/v2`. v0.4's `python -m ratchet.bom verify` does not read the schema field, so it still checks a v2 document's hash and signature.
- Setting a reserved variable stops startup until its feature lands. That is deliberate, and the message names the variable and the feature (RUNBOOK).
- Thirteen reserved settings and sixteen reserved policy fields are documented before they do anything, in their own section of CONFIGURATION.md.
- `student_cost_usd` already exists (FR-15.1: live and shadow samples), so the replay uses it as it is.
- The router's `route` column already records why a request was not served by the student (`cold_start`, `confidence`, `student_error`, `student_breaker`, ...). So no separate `reason` column was added: the replay reads `route`.
- The ledger's journal mode is unchanged. WAL comes with live readiness (`RATCHET_DB_SYNC`), which must also keep the ledger off the bucket mount. (Amended by [ADR 0060](0060-hot-path-caches-and-throttled-tick.md): WAL landed with the hot-path item, `RATCHET_DB_SYNC` now defaults to `NORMAL`, and `FULL` keeps the rollback journal for such mounts.)

## Alternatives

- **Each feature brings its own helpers and columns.** That means more migrations, statistics written several times, and no single place to prove them correct.
- **numpy or scipy for the statistics.** They are heavy dependencies for a gateway that serves from a small VM. The few functions needed are exact in the standard library, at the sizes Ratchet sees (hundreds to thousands of rows).
- **Accept the reserved settings silently.** Simpler, but a knob that does nothing invites a wrong belief about what protects a release.
- **Pin only the tickets run.** Cheaper, but the research and mixed workloads exercise other tasks, Scout's pinned calls and other release paths that a change could move unnoticed.
- **Enforce append-only tables in Python only.** A later writer, or a raw SQL fix during an incident, could still edit a sealed receipt. Triggers hold for every connection, including the cost CLI's.
