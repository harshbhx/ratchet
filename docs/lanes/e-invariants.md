# Lane E: proposed invariants

Lane E (data governance and KPIs) does not number invariants in `CLAUDE.md`, because lanes build in parallel and the
numbers would collide. Each invariant below is enforced and tested in this branch; the integrator gives it the next
free number in `CLAUDE.md`'s list, adds its `CODE` entry in `scripts/reqtrace.py`, and adds the number to the proving
tests' `@pytest.mark.req` markers.

## Opt-outs only reduce storage

**Text (for CLAUDE.md).** **Opt-outs only reduce storage.** `store: false`, `X-Ratchet-Store: none`,
`metadata.ratchet_store`, a key's `store=0`, no-store tasks, no-train end users and zero retention
(`RATCHET_STORE_EXAMPLES=0`) never create or bump an example, and nothing in a request can undo one (`store: true` is
the default spelled out). `Ratchet.store_opt_out` is the one rule; `handle` settles it once and `_store_example` asks
again before writing. The cascade runs as usual under an opt-out (audits and arbitrations included). Pinned tasks are
unchanged and redaction still applies. See [ADR 0080](../adr/0080-storage-opt-outs-and-pseudonymous-end-users.md).

The item's spec called this INV-17; that number already means "committed leaves and sealed receipts are append-only"
(ADR 0024), so it needs the next free one.

**Enforcing code.**

- `ratchet.router:Ratchet.store_opt_out` (the rule: global, task, the caller's verdict, the no-train lookup on every
  hash the request's end user may be registered under: `end_user` and `end_user_aliases`)
- `ratchet.router:Ratchet._opted_out` and `ratchet.router:Ratchet.handle` (settled once on the request's own copy of its
  meta, recorded per request)
- `ratchet.router:Ratchet._store_example` (asks again before `Ledger.add_example`, and writes an opt-out found then onto
  the request's meta, so the row and the response report it)
- `ratchet.privacy:end_user_ids` and `ratchet.privacy:EndUsers.aliases` (both identifiers a request carries, and the
  every-client scope an admin registers in)
- `ratchet.gateway:_request_meta` and `ratchet.privacy:request_opts_out` (the key's and the request's opt-outs;
  any one wins)
- `ratchet.nightschool.orchestrate:run_night` (`store_examples=False` skips the night)

**Proving tests** (`tests/test_privacy.py`).

- `test_store_false_escalation_calls_the_teacher_and_stores_nothing`: no insert, no bump of a stored duplicate
- `test_store_false_still_audits_and_arbitrates_without_storing`
- `test_zero_retention_stores_nothing_for_any_caller`
- `test_store_header_metadata_and_key_opt_outs`: `store: true`, `X-Ratchet-Store: 1` and `metadata.ratchet_store: "true"`
  never undo an opt-out
- `test_no_train_end_users_are_never_stored_even_when_registered_mid_request`: also reported as `end_user`
- `test_a_no_train_registration_holds_whichever_identifier_a_request_sends`
- `test_no_train_registrations_across_key_renames_and_keys_added_later`
- `test_opt_outs_hold_under_concurrent_requests`
- `test_zero_retention_mode_night_skips`, `test_live_night_skips_under_zero_retention`
- `test_every_add_example_caller_honours_storage_opt_outs`: every entry of INV-2's `ALLOWED_ADD_EXAMPLE_CALLERS` calls
  `store_opt_out` before `add_example`

**For the integrator.** `Ratchet._store_example` is the only writer of examples in this branch. Every other entry that
joins `ALLOWED_ADD_EXAMPLE_CALLERS` at the merge (lane F's clean-room backfill, which writes examples with origin
`backfill`) must ask `Ratchet.store_opt_out` before `Ledger.add_example`: at least zero retention (`global`) and no-store
tasks (`task`), which need no request meta (`store_opt_out(task, None)`), and the request's end user when the source row
has one. `test_every_add_example_caller_honours_storage_opt_outs` fails until it does; also add a behaviour test that a
backfill under `RATCHET_STORE_EXAMPLES=0`, and one for a no-store task, stores nothing.

## End users are stored only as keyed hashes

**Text (for CLAUDE.md).** **End-user identifiers are never stored or logged.** A request's `user` and
`safety_identifier` are hashed in `gateway._request_meta` with `privacy.EndUsers.hash` (HMAC-SHA256 under the
deployment's key, scoped to the API key name, 16 hex digits) and go no further; only the hash reaches the router, the
ledger and the no-train table, and no error message, event or `/ratchet/stats` field quotes the value or the hash. Like
invariant 13 for API keys. See [ADR 0080](../adr/0080-storage-opt-outs-and-pseudonymous-end-users.md).

**Enforcing code.**

- `ratchet.gateway:_request_meta` (the only place the raw value is read after validation)
- `ratchet.privacy:EndUsers.hash`, `ratchet.privacy:end_users`
- `ratchet.openai_compat:_end_user` (the type error never quotes the value)
- `ratchet.gateway:_no_train_add`, `ratchet.gateway:_no_train_remove` (events carry counts and names only)

**Proving tests** (`tests/test_privacy.py`).

- `test_end_user_is_an_hmac_and_the_raw_value_is_stored_nowhere`: every text value of every table, with redaction off
- `test_after_no_train_registration_escalations_are_not_stored`: no hash or value in the `no_train` events
- `test_no_train_requests_are_validated`: a long value is accepted and never echoed

## Erasure is final and leaves hashes only

**Text (for CLAUDE.md).** **An erased example is gone for good and leaves hashes only.** An erasure (`privacy.erase`)
removes an example's messages, answer, stored conditions and end-user hash and tombstones its hash: `Ledger.add_example`
refuses that hash from then on, from any client, and nothing restores it (a retention purge, by contrast, may be undone
by a storable duplicate). The erasure record (`erasures`, `erasure_items`, `tombstones`, the `erase` event) holds hashes,
counts, names and a redacted reason, never request text or a raw end-user value. Versions that may have been trained on
the erased examples are reported, never edited; their signed BOMs stay as written (invariant 14). Never hard-delete an
example row: Night School's frequency rule and new-data count read the tombstone. See
[ADR 0081](../adr/0081-retention-tombstones-erasure-and-export.md).

**Enforcing code.**

- `ratchet.privacy:erase` and `ratchet.privacy:_check_erasure` (the scope, the redacted reason, the SHA-256 of `contains`)
- `ratchet.ledger:Ledger.record_erasure` and `ratchet.ledger:Ledger._purge` (one transaction: purge, tombstones, items)
- `ratchet.ledger:Ledger.add_example` and `ratchet.ledger:Ledger._refuse_erased` (the refusal)
- `ratchet.router:Ratchet._store_example` (a refused answer is skipped as `erased`, `requests.stored` 0)

**Proving tests** (`tests/test_erasure.py`).

- `test_erasure_records_hold_hashes_and_no_text`: every text value of every table after an erasure by client, end user and text
- `test_an_erased_hash_is_never_stored_again_and_a_retention_purged_one_is_restored`
- `test_the_cascade_skips_a_tombstoned_answer_as_erased`
- `test_erasure_makes_a_retention_tombstone_final`
- `test_erasure_holds_under_concurrent_stores`: duplicates stored from three threads during the erasure revive nothing
- `test_affected_versions_are_reported_and_their_signed_boms_stay_unchanged`

## Retention never cuts a release in flight

**Text (for CLAUDE.md).** **Request retention never removes a release's evidence.** `Ledger.fold_requests` never folds
or deletes a request row newer than the `stage_since` of a version in shadow or canary, and every stats reader adds the
folded sums (`daily_rollup`) back, so a release decision and every figure but distinct end users are the same before and
after a fold. The first fold records day 1's start in `meta`, so day numbers never shift. See
[ADR 0081](../adr/0081-retention-tombstones-erasure-and-export.md).

**Enforcing code.**

- `ratchet.ledger:Ledger.fold_requests` and `ratchet.ledger:Ledger._fold_where`
- `ratchet.ledger:_merge` and `ratchet.ledger:Ledger._rollup_by` (the readers: `stats_by_day`, `stats_by_task`,
  `stats_by_client`, `totals`, `opt_out_stats`, `cost_by_day`)
- `ratchet.ledger:Ledger.__init__` (`DAY_ORIGIN_META`)

**Proving tests** (`tests/test_erasure.py`).

- `test_retention_never_folds_rows_of_a_release_in_flight`: a canary's old rows stay, its audited precision unchanged
- `test_request_rollup_keeps_every_stat_identical`: half and full folds, also in chunks of 7
- `test_day_numbers_survive_folding_the_rows_that_defined_day_one`

## For the integrator: spec deviations in PRIV-RETENTION-ERASURE-EXPORT

These are not invariants; they are listed here so the merge does not miss them.

- `DELETE /v1/ratchet/examples` reads the end user from the JSON body, not from `?end_user=` as the spec wrote: access
  logs keep URLs. A URL with `end_user` is a 400 `end_user_in_url`. A keyless caller (API keys off) gets the counts as
  null. See ADR 0081 (Alternatives) and docs/API.md.
- Night School's new-data count is `manifest - Ledger.train_rows_kept()` (each version's training rows less its own
  purged ones, the largest), not the spec's "rows stored after the last version", which changes v0.4's count without
  any deletion. `tests/test_erasure.py::test_run_night_trains_again_after_deletions_once_50_new_examples_arrive` runs
  nights with no new data after a net deletion and asserts none trains.
- `python -m ratchet.admin erase --end-user` refuses unless its RATCHET_END_USER_KEY (or the ledger's generated key) has
  the key id the ledger recorded, and never writes when it refuses (`privacy.recorded_end_users`); without `--client`
  it searches every scope the ledger holds end users in (`Ledger.end_user_scopes`), not RATCHET_API_KEYS.
