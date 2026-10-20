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

## Every route has an access level, and a live gateway is private by default (SEC-ENDPOINT-AUTHZ, ADR 0061)

**Text.** Every gateway route has exactly one access level in `ratchet/access.py` (`ROUTES`, then `PREFIXES`), shown in the Access column of docs/API.md; a route the table does not know is `dashboard`. In live mode, `dashboard` and `bom` routes answer only admin and dashboard keys unless an operator sets `RATCHET_PUBLIC_DASHBOARD=1` (or `RATCHET_PUBLIC_BOM=1` for the BOMs), and an anonymous read of a public live dashboard is scrubbed: client names appear only as pseudonyms, and event details carry no URL and no error message after an exception's class. A scrubbed read is a copy; the cached payload is never changed. Live mode never starts without `RATCHET_API_KEYS` unless `RATCHET_ALLOW_UNAUTHENTICATED=1`. Each key has one role: no key is in two of `RATCHET_API_KEYS`, `RATCHET_ADMIN_KEYS` and `RATCHET_DASHBOARD_KEYS`. Never add a route without a level, and never read a key from a request anywhere but `_client`, `_admin` and `_access`.

**Why.** The dashboard API lists clients, spend and events (old ones with endpoint URLs); a new endpoint that defaulted to public, or a live gateway started without keys, would publish them or spend the operator's teacher credits for anyone.

**Enforcing code.** `ratchet/access.py`: `ROUTES`, `PREFIXES`, `route_access`, `scrub_stats`, `scrub_task`, `scrub_detail`, `pseudonyms`. `ratchet/gateway.py`: `_access` (a global dependency of the app), `_scrubbed`, `Runtime._set_access`, `_admin`. `ratchet/config.py`: `Settings.access`, `Access.scrubs`, `_bool`.

**Proving tests.** `tests/test_security.py`: `test_every_route_has_an_access_level`, `test_private_live_dashboard_needs_an_admin_or_dashboard_key`, `test_private_dashboard_without_reader_keys_is_closed_to_everyone`, `test_public_live_stats_are_scrubbed_for_anonymous_readers`, `test_live_mode_without_api_keys_does_not_start`, `test_each_key_has_one_role`.

## Logs, metrics and failures rows carry no content (OPS-OBSERVABILITY, ADR 0062)

**Text.** A log line, a metric label or sample, and a `failures` row hold ids, names, numbers and statuses only: never message or answer text, a URL, an exception's message or a key (invariants 12 and 13). Log through `obs.log` (or `obs.upstream_failure`, which names the exception's class, role and status), write failures through `Ledger.log_failure` with `error` from `resilience.describe_failure` or an exception's class, and mirror events only through `obs.mirror_event`, which scrubs them like a public dashboard (`access.scrub_detail`). A client's own request id is kept only when it matches `obs.CLIENT_REQUEST_ID`; a metric's task label is a registered name, `other` or `none` (`router.task_label`) and its client label a key name, `none` or `other` (`metrics.client_label`), so no request can add text or a series. Never pass a message, a completion, `str(exception)` or a header value other than a validated id to any of them.

**Why.** Logs and metrics leave the ledger's protections: they are shipped to collectors, kept for months and read by people who may not read the ledger. A prompt or a key in a log line is a leak the redaction and the key digests were built to prevent.

**Enforcing code.** `ratchet/obs.py`: `log`, `upstream_failure`, `mirror_event`, `client_request_id`, `JsonFormatter` and `TextFormatter` (an attached exception as its class only). `ratchet/ledger.py`: `Ledger.log_failure` (`FAILURE_COLUMNS`, `FAILURE_STAGES`). `ratchet/router.py`: `Ratchet._record_failure`, `task_label`. `ratchet/metrics.py`: `client_label`. `ratchet/gateway.py`: `_note_error`, `_log_chat`.

**Proving tests.** `tests/test_observability.py`: `test_json_log_lines_carry_no_content_and_no_keys` (a marker from a message and from an answer, a card number, an API key, the teacher key and both endpoint hosts never reach a line), `test_error_responses_carry_x_request_id_and_envelope_request_id` (a 500 whose exception message holds a marker leaves only the class), `test_teacher_502_through_the_gateway_writes_one_row_with_the_student_cost` (an upstream message with a URL never reaches the row), `test_request_id_echoed_and_client_id_stored` (malformed client ids are dropped), `test_metrics_parse_count_and_bound_their_labels` (an ad-hoc task name is never a label), `test_metrics_401_in_live_mode_without_a_key_or_token` (the token reaches no row).

## A request that spent money and failed leaves a failures row (OPS-OBSERVABILITY, ADR 0062)

**Text.** When the teacher's answer cannot be had after the student was sampled, `Ratchet` writes a `failures` row with `cost_usd` equal to what the student's samples cost before the error reaches the client (stage `teacher` for an HTTP failure; for a bug on that path the gateway writes the `gateway` row with the spend the router left on the request's context, never throttled), and `Ledger.totals()` counts that spend against `saved_usd` and `savings_pct` (`failed_cost_usd`). A failure around an answered request records `cost_usd = 0`, because its own `requests` row already holds the cost. Never count one spend in both tables.

**Why.** NFR-3 says overhead is counted in reported cost; without the row, every teacher outage would make the savings look better than they were.

**Enforcing code.** `ratchet/router.py`: `Ratchet._teacher_failed`, `Ratchet._record_failure`. `ratchet/gateway.py`: `_failure_row`. `ratchet/obs.py`: `RequestContext.failed_cost_usd`. `ratchet/ledger.py`: `Ledger.log_failure`, `Ledger.totals`.

**Proving tests.** `tests/test_observability.py`: `test_teacher_failure_row_carries_the_student_cost`, `test_teacher_502_through_the_gateway_writes_one_row_with_the_student_cost`, `test_a_bug_on_the_teacher_path_is_a_gateway_failure_not_the_teachers` (a bug's row carries the spend, also past the throttle), `test_swallowed_failures_get_rows_and_breaker_refusals_are_sampled` (cost 0 around an answered request); `tests/test_system.py`: `test_reference_workloads_reproduce_v040` (three workloads' totals exactly as v0.4.0 gave them).
