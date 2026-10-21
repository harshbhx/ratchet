# ADR 0060: The hot path: a WAL ledger, a version routing snapshot, incremental tallies, a throttled tick and cached stats

- Status: Accepted
- Date: 2026-10-20

## Context

The request path and the dashboard read the same SQLite ledger, and several of those reads grew with the ledger's age:

- `Ledger.versions()` read every version row and parsed its eval. `live()` and `by_state()` called it, three times per request (live, canary, shadow) and about six times per release-gate tick. With a year of nightly versions that was milliseconds per request, for three rows that change a few times a day.
- The gate ran after every request. Its canary and live-alert evidence (`version_live_quality`) counted every audit of the stage again on each run, however long the stage had lasted.
- `/ratchet/stats`, which every open dashboard tab polls, recomputed everything (full-table aggregates, a sorted hash of every example, the whole event log) on each read, on the writer's connection, under the lock every request takes.
- The file used SQLite's default rollback journal with `synchronous=FULL`: an fsync per commit, about a millisecond or more for each request row, and readers that block the writer.
- `latency_ms` is the providers' time. Nothing measured Ratchet's own time per request, so NFR-1 (p50 under 1 s for student-served requests) could not be checked from the ledger.

ADR 0024 had reserved `RATCHET_DB_SYNC` for the WAL journal (default `FULL`, today's journal, until the feature landed), and had added indexes for the gate's queries.

## Decision

- **WAL for files.** `Ledger(path, sync=...)` puts a file ledger in the WAL journal with `synchronous=NORMAL` and a 5 s busy timeout (`RATCHET_DB_SYNC=NORMAL`, now the default). `FULL` keeps the rollback journal with `synchronous=FULL` (v0.4), also taking a WAL file back, and stops startup when another process holds the file open in WAL. When SQLite cannot enable WAL (no shared memory), the rollback journal stays with `synchronous=FULL`, the level it needs. In-memory ledgers are unchanged.
- **A read-only connection for stats.** On a WAL file, stats and report queries (`_reads`) run on a second connection opened `mode=ro`, under their own lock, each in one read transaction. Everything the request path and the gate read stays on the writer, so they always see the latest state.
- **A routing snapshot.** The ledger keeps the newest row of each routed state (live, canary, shadow) and rebuilds it when a version was written. Its own writes are counted by TEMP triggers on `versions` that call back into the ledger (`ratchet_written`); they exist only on the writer's connection and fire for every write through it, raw SQL included, so a new writer method cannot forget to invalidate. Other connections' commits change `PRAGMA data_version`. `live()` and `by_state()` return a fresh `Version` per call (the eval parsed from the cached row); `version(id)` is one query by id; `versions()` is unchanged.
- **Incremental tallies.** `version_live_quality` keeps, per (version, stage), the count, the verdict count and the sum of `audit_agree` up to the last id it read, and reads only rows above it (`requests_version_audited`). A deleted or updated request on the writer (TEMP triggers on `requests`, which do not fire on inserts) or any other connection's commit restarts it. The result is exactly one `COUNT(*)` and `AVG(audit_agree)` over the stage.
- **A throttled tick.** `Runtime.tick()` runs the gate at most once per `RATCHET_TICK_INTERVAL_S` (default 5 s in live mode, 0 in mock mode) and skips, without waiting, while another thread ticks; `force=True` (the mock demo's simulated days) always runs. The last tick time is set before the gate runs, so a failing gate is not retried on every request.
- **Cached stats.** `/ratchet/stats` goes through `SnapshotCache`: computed at most once per `RATCHET_STATS_TTL_S` (2 s live, 0 mock), by one caller at a time, the others served the previous payload. The BOM digest is cached under (examples' count, highest id, rewrite generation), each day's latency percentiles under (that day's count, highest id, request generation), and the event list reads its newest 40 rows. A cache key never uses a generation the writer may not have committed (`_settled`: none while the writer is in a transaction).
- **Wall-clock latency.** `Ratchet.handle` and `teacher_only` measure `time.perf_counter()` (`Ratchet(timer=)`) from entry to the ledger write into the new `requests.wall_ms`; `latency_ms` keeps its meaning. `Ledger.latency_percentiles` gives nearest-rank percentiles with `ORDER BY ... LIMIT 1 OFFSET k`, and `stats_by_day` adds `p50_student_ms` and `p95_ms`.
- **One new index.** `versions_state` serves the snapshot's rebuild. The gate's other queries already search ADR 0024's indexes: `requests_version_audited (student_version, audited, id)` serves the tallies, and `requests_shadow_version` serves `shadow_version=? AND id>?` because SQLite ends every index with the rowid.

## Consequences

- Positive: a request's own time no longer grows with the number of versions or logged requests (365 versions cost what one does; with 200,000 rows a warm tick takes a few milliseconds and a request a fraction of one, `tests/test_perf.py`). Logging a request no longer waits for an fsync. A dashboard never slows requests, and many dashboards cost one computation per TTL.
- Positive: NFR-1 can be checked from the ledger: per-day p50 and p95 wall-clock latency, and the overhead `wall_ms - latency_ms`.
- Negative: with `NORMAL`, a power cut or an operating-system crash can lose the last commits (never corrupt the file). `FULL` restores per-commit durability at the old cost.
- Negative: WAL needs shared memory, so every process that opens the ledger must run on the same host, with the file on a local disk (never a network or bucket mount; use `FULL` there). Backups must use SQLite's backup API, not a file copy, and a restore must remove the old `-wal` and `-shm` files first (RUNBOOK, DEPLOY_NEBIUS_VM).
- Negative: a rollback decided by the request path can wait up to `RATCHET_TICK_INTERVAL_S`, and a dashboard can be up to `RATCHET_STATS_TTL_S` behind.
- Negative: the caches are per process and rely on rules: rows are only appended to `requests` and `examples` in the normal course (ids in commit order); an example's hash is rewritten in place only through the writer's connection; ledger writes commit at once. Other processes' writes are seen through `PRAGMA data_version` and row counts. `ratchet/CLAUDE.md` lists the rules.
- The reference simulation and the three pinned workloads are unchanged: the tallies give the same numbers as the aggregates, mock mode keeps a tick per request and fresh stats, and the new stats keys are not pinned.

## Alternatives considered

- **Bump a generation in each version writer by hand** (the first design): one forgotten call would keep routing traffic to a rolled-back canary. TEMP triggers cover every write on the connection, at no cost to request inserts.
- **A persistent trigger and counter table:** visible to other processes without `data_version`, but it changes the file's schema and writes on every version change for a value only one process reads.
- **A partial index for audits (`WHERE audited=1 AND served_by='student'`):** smaller, but `requests_version_audited` already turns the tally into an index range read, and the incremental tally reads only new rows.
- **An index on `requests(task)`:** measured slower for `stats_by_task` on 200,000 rows (a table scan with a temporary B-tree beats an index walk with a row lookup per row), and one more index on every insert.
- **Caching in a separate process or Redis:** more moving parts than a single-host SQLite gateway needs (ADR 0006).
