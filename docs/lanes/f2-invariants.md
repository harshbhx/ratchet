# Lane F2: proposed invariants

Invariants lane F2 proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id. Each entry names its text, the code that enforces it and the tests that prove it.

## Label-free drift alarm (item drift-martingale, FR-16.6, FR-6.40, FR-6.41, NFR-55, ADR 0032)

### The drift monitor stays off the request path and draws from its own generators

**Text.** The drift monitor reads only the ledger (`Ledger.confidence_stream`, in request-id order) from the release gate's tick, at gateway startup and from Night School's early-night check; it never runs inside `Ratchet.handle`, which only looks up `Ratchet.audit_boost` (a dict the gate fills). Each stream draws its tie-breaking uniforms from its own `random.Random(f"{seed}:{version}:{task}")`, never from `Ratchet.rng` or the module-level `random`, and its state is a pure function of the ledger's rows, so replaying the ledger reproduces every capital exactly. A seeded simulation must give the same days, versions, totals and events (apart from `drift` events) with the monitor on and off.

**Enforcing code.** `ratchet/drift.py`: `Stream` (its `rng`), `DriftMonitor.update`, `DriftMonitor.rebuild`, `DriftMonitor._replay`; `ratchet/release.py`: `ReleaseGate._tick_drift`, `ReleaseGate.check_drift`; `ratchet/router.py`: `Ratchet.handle` (the boost lookup only).

**Proving tests.** `tests/test_drift.py::test_monitor_draws_nothing_from_the_router_rng`, `test_replaying_the_ledger_reproduces_every_capital_exactly`, `test_compaction_keeps_serving_versions_and_replays_a_restored_one`, `test_a_restarted_gateway_replays_the_ledger_at_startup`, `test_an_up_to_date_monitor_reads_one_query_per_update`.

### A drift alarm is valid however often anyone looks

**Text.** A stream's capital must stay a test supermartingale under exchangeable traffic, so that Ville's inequality bounds its lifetime false-alarm probability by 1/`drift_alarm`. Concretely: each p-value is the smoothed conformal rank of the new confidence among the stream's own scores so far, with a fresh independent uniform for ties; each betting function integrates to at most 1 on [0, 1]; the bet weights and the switching rate depend only on the past and on n, never on the current or a later confidence; a clamp may only lower a bet. A change to the binning, the bets or the mixing must keep all four, and the family bound (K/threshold over the streams held) is always reported next to the per-stream one.

**Enforcing code.** `ratchet/drift.py`: `Stream.observe`, `bin_of`, `BETS`, `JUMP`, `MIN_P`, `DriftMonitor.status`, `Alarm.detail`.

**Proving tests.** `tests/test_drift.py::test_p_values_are_uniform_under_the_null`, `test_rarely_alarms_under_the_null`, `test_rarely_alarms_under_the_null_full_size` (slow), `test_sim_never_alarms_without_drift`, `test_rising_confidence_makes_the_capital_fall`, `test_fenwick_ranks_match_a_sorted_list_with_ties`.

### A drift alarm only reports, unless an operator opts in, and is written once

**Text.** With the defaults, a drift alarm writes one `drift` event per (version, task), once across processes and restarts (`Ledger.event_once`, keyed `drift:<version>:<task>` in `meta`), dated on the alarm's own ledger day (an event the ledger refuses is retried by the next check), and makes the version's certificate read `suspended`; it changes no audit, no training decision and no release decision. The opt-in audit boost keeps exactly one random draw per served answer and logs the rate it was drawn at as `label_p` (lane F's "every audit decision is one logged draw"); the opt-in early night runs after the in-flight check, so it never trains during a release (INV-5), and counts only an alarm whose request was logged after the last `trained` event. `drift` stays an advisory event kind that the pinned reference runs leave out.

**Enforcing code.** `ratchet/release.py`: `ReleaseGate.check_drift`, `ReleaseGate._refresh_audit_boost`, `ReleasePolicy.__post_init__`; `ratchet/ledger.py`: `Ledger.event_once`; `ratchet/router.py`: `Ratchet.handle`; `ratchet/drift.py`: `boosted_rate`; `ratchet/nightschool/orchestrate.py`: `early_night`, `run_night`; `ReleaseGate.drift_alarm_since`; `ratchet/certificate.py`: `status`; `scripts/reference_check.py`: `IGNORED_KINDS`.

**Proving tests.** `tests/test_drift.py::test_tick_writes_one_drift_event_per_alarm_across_restarts`, `test_events_once_and_the_last_event`, `test_concurrent_checks_write_each_alarm_once`, `test_a_drift_event_the_ledger_refused_is_written_by_the_next_check`, `test_an_alarm_found_by_a_replay_keeps_its_day_and_counts_only_after_the_last_training`, `test_audit_boost_raises_only_the_alarmed_versions_rate_with_one_draw_per_answer`, `test_gate_boosts_an_alarmed_serving_version_until_a_newer_one_goes_live`, `test_early_night_halves_the_new_examples_needed_only_after_an_alarm`, `test_early_night_never_trains_while_a_release_is_in_flight`, `test_certificate_is_suspended_while_a_drift_alarm_is_open`.

### Client-chosen task names never hold unbounded state in the drift monitor

**Text.** Task names come from clients (any valid `X-Ratchet-Task` is distilled under `RATCHET_UNKNOWN_TASKS=distill`), so the drift monitor must not hold state per name without a bound: a version holds at most `MAX_STREAMS` (16) task streams, opened in the order it first sees each name, and the rows of every later name share its `OTHER_TASKS` (`*`) stream; a stream's memory follows what it has seen (a sorted array of 2-byte bins until `DENSE_AFTER`, then one Fenwick tree). Which stream a row goes to must depend only on the order of the version's rows, so a replay of the ledger makes the same choice and each stream stays exchangeable under the null (ADR 0032). A new per-name structure in the monitor needs the same kind of bound (threat model T12).

**Enforcing code.** `ratchet/drift.py`: `DriftMonitor._stream`, `DriftMonitor._replay`, `Ranks`, `MAX_STREAMS`, `OTHER_TASKS`, `DENSE_AFTER`, `Stream.compact`.

**Proving tests.** `tests/test_drift.py::test_client_task_names_cannot_grow_the_monitor`, `test_task_headers_sent_to_the_gateway_hold_a_bounded_number_of_streams`, `test_stream_memory_follows_what_it_has_seen`, `test_a_compacted_shared_stream_replays_its_own_rows`, `test_a_shift_under_invented_task_names_alarms_on_the_shared_stream`.
