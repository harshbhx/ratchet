# Lane D: proposed invariants

Lane D (operations, security, performance) does not number invariants in CLAUDE.md while the lanes build in parallel, because the numbers would collide. Each proposal below names its text, the code that enforces it and the tests that prove it. The integrator gives each one the next free number when the lanes merge, adds it to CLAUDE.md's list and to `CODE` in `scripts/reqtrace.py`, and marks the tests with `@pytest.mark.req("INV-n")`.

## Routing never reads a stale version (OPS-PERF-HOTPATH, ADR 0060)

**Text.** Every write to `versions`, through any connection, is seen by the next `Ledger.live()` / `Ledger.by_state()` call. The writer's TEMP triggers (`ledger.WATCHED`) bump the routing generation for every write on its connection, raw SQL included, and `PRAGMA data_version` changes for another connection's commit. Never cache a version's state anywhere else on the request path, and never write the ledger through a second connection inside the gateway process.

**Why.** A rollback that the router did not see would keep sending canary traffic to a version the gate has just found worse than the floor.

**Enforcing code.** `ratchet/ledger.py`: `WATCHED`, `Ledger._watch`, `_bump`, `Ledger._routed`, `Ledger.live`, `Ledger.by_state`.

**Proving tests.** `tests/test_perf.py`: `test_version_cache_invalidates_on_state_change` (every writer and raw SQL on `ledger.db`), `test_version_cache_sees_another_connections_writes` (a second ledger on the file, as the cron tick), `test_routing_reads_no_version_rows_until_a_version_changes`.

## Hot-path caches change no number (OPS-PERF-HOTPATH, ADR 0060)

**Text.** A cache on the request path, the release gate or `/ratchet/stats` returns exactly what the uncached query returns for the data it covers: `version_live_quality`'s incremental tally equals one `COUNT(*)` and `AVG(audit_agree)` over the stage, the cached BOM digest equals a fresh hash of every example, and a day's cached percentiles equal `latency_percentiles` for that day. A reader-side cache never keys on a write generation the writer may not have committed (`Ledger._settled`). Only freshness may differ, and only where a setting says so (`RATCHET_STATS_TTL_S`, `RATCHET_TICK_INTERVAL_S`).

**Why.** Release decisions, receipts and the pinned reference simulation must not depend on whether a value came from a cache.

**Enforcing code.** `ratchet/ledger.py`: `_Tally`, `Ledger.version_live_quality`, `Ledger._settled`, `Ledger.bill_of_materials`, `Ledger._day_percentiles`; `ratchet/gateway.py`: `SnapshotCache`.

**Proving tests.** `tests/test_perf.py`: `test_live_quality_tally_matches_a_full_count`, `test_bom_digest_is_cached_until_examples_change`, `test_stats_by_day_reports_latency_percentiles_and_sorts_only_changed_days`, `test_perf_guard_200k_requests` (the tally against a full count on 200,000 rows); `tests/test_system.py`: `test_reference_simulation_unchanged`, `test_reference_workloads_reproduce_v040`.
