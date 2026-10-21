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

## Teacher watch (item teacher-watch, FR-16.7, FR-5.40, FR-5.41, FR-6.42, FR-6.43, NFR-57, ADR 0033)

### Each stored request gives at most one teacher check per epoch, and only between two independent greedy labels

**Text.** A `teacher_checks` row compares a stored label with that request's first re-test in the current check epoch, and nothing else: `examples.checked_epoch` is set to the epoch in the same transaction as the check (the duplicate branch of `Ledger.add_example`, or `Ledger.record_teacher_check` with `BEGIN IMMEDIATE`), so threads and processes cannot add a second check of one example to an epoch. Both labels must come from the same configured teacher at temperature 0 without a client's reasoning effort, need no redaction placeholder, and be valid under the current spec of a registered task that names fields and is not pinned. A contested label (selected by a disagreement) and an adjudicated one (`UNCHECKABLE`, a majority, not one draw) are never compared again. Comparing a stored label with several re-tests makes the checks dependent and the freeze rule invalid; any new source of checks must keep this rule.

**Enforcing code.** `ratchet/ledger.py`: `Ledger._check_label`, `Ledger.add_example` (its `comparable` flag and duplicate branch), `Ledger.record_teacher_check`, `Ledger.resolve_contested`, `Ledger.probe_candidates`, `UNCHECKABLE`; `ratchet/router.py`: `comparable_label`, `Ratchet._store_example`.

**Proving tests.** `tests/test_teacher_watch.py::test_first_re_test_rule_one_check_per_request_per_epoch`, `test_concurrent_duplicates_give_one_check_per_epoch`, `test_a_contested_or_adjudicated_label_is_never_compared_again`, `test_labels_from_another_teacher_or_sampled_labels_are_not_compared`, `test_labels_that_needed_redaction_are_never_checked`, `test_redaction_placeholders_never_create_false_disagreements`, `test_router_stores_sampled_teacher_answers_as_uncheckable`, `test_reusing_a_stored_label_breaks_the_guarantee_the_first_re_test_keeps`.

### Contested labels never train or grade

**Text.** While the teacher watch is on (`RATCHET_TEACHER_WATCH=1`, the default), `dataset.rows` leaves every row with `contested` set (1, or 2 after an adjudication without a majority) out of both the training and the golden split, and `run_night` builds its sets, the golden eval and the BOM from those rows only. Only an adjudication clears `contested`; an agreeing check later does not.

**Enforcing code.** `ratchet/nightschool/dataset.py`: `rows`, `build`; `ratchet/nightschool/orchestrate.py`: `run_night`; `ratchet/ledger.py`: `Ledger.resolve_contested`, `Ledger.mark_unresolved`.

**Proving tests.** `tests/test_teacher_watch.py::test_a_disagreeing_label_is_checked_contested_and_left_out_of_both_splits`, `test_a_frozen_watch_skips_the_night_before_any_teacher_call`, `test_adjudication_two_of_three_resolves_and_one_one_one_stays_contested`, `test_the_bom_records_the_teachers_self_agreement_and_the_labels_left_out`.

### A teacher-drift freeze stops training until an operator acknowledges, and the watch never fails a request

**Text.** Once `teacher_frozen` is set (one `teacher_drift` event, written in the transaction that sets it and re-checks the epoch), `run_night` writes `skip` and returns before any teacher call, dataset build or training, also before the in-flight check; only `Ledger.teacher_ack` (from `POST /ratchet/teacher/ack`, an admin key in live mode) or the operator's own call lifts it, starting a new epoch. The freeze decides no release and never touches serving. The watch's work on the request path (recording a check, the model-id comparison) never fails the request it rides on: a failed comparison is no check, and a ledger error in the model-id watch leaves the id unseen.

**Enforcing code.** `ratchet/release.py`: `ReleaseGate._tick_watchers`, `ReleaseGate.check_teacher`; `ratchet/teacher_watch.py`: `Watch.check`, `evidence`; `ratchet/ledger.py`: `Ledger.teacher_freeze`, `Ledger.teacher_ack`, `Ledger._check_label`; `ratchet/nightschool/orchestrate.py`: `run_night`; `ratchet/router.py`: `Ratchet._watch_model`; `ratchet/gateway.py`: `teacher_ack`.

**Proving tests.** `tests/test_teacher_watch.py::test_the_gate_freezes_night_school_once_and_an_ack_resumes_it`, `test_a_frozen_watch_skips_the_night_before_any_teacher_call`, `test_teacher_ack_needs_an_admin_key_in_live_mode`, `test_a_ledger_that_refuses_the_model_id_never_fails_the_teachers_answer`, `test_the_freeze_rule_rarely_alarms_when_the_teacher_is_within_its_noise`.

### An adjudication relabels only with the same teacher's complete, valid majority

**Text.** Night School may replace a stored label only after an adjudication: a strict majority of the stored label and two new answers from the configured teacher that wrote it, each new answer complete (`finish_reason` in `COMPLETE`), valid under the task's spec once cleaned, and, with redaction on, needing no placeholder. The row keeps its hash, conditions, teacher, license and redaction count (INV-1, INV-2, INV-12, INV-16), and the label becomes `UNCHECKABLE`.

**Enforcing code.** `ratchet/teacher_watch.py`: `night`, `_adjudicate`, `majority`, `usable_label`, `relabel_with`; `ratchet/ledger.py`: `Ledger.resolve_contested`, `Ledger.contested_examples`.

**Proving tests.** `tests/test_teacher_watch.py::test_adjudication_two_of_three_resolves_and_one_one_one_stays_contested`, `test_a_probe_answer_that_could_not_be_stored_is_no_check`, `test_labels_never_adjudicated_go_before_unresolved_ones`.
