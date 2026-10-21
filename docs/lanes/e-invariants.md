# Lane E: proposed invariants (merged)

The four invariants lane E proposed here are now numbered in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these),
each with its `CODE` entry in `scripts/reqtrace.py` and its proving tests marked:

- INV-22: opt-outs only reduce storage ([ADR 0080](../adr/0080-storage-opt-outs-and-pseudonymous-end-users.md)).
- INV-23: end-user identifiers are never stored or logged (ADR 0080).
- INV-24: an erased example is gone for good and leaves hashes only
  ([ADR 0081](../adr/0081-retention-tombstones-erasure-and-export.md)).
- INV-25: request retention never removes a release's evidence (ADR 0081). At the merge with lane F's anytime-valid
  release gate (ADR 0025) it was extended: a fold also keeps every row a version in shadow or canary served or shadowed
  in an earlier stage, because the sequential canary rule counts every audit of the canary's life
  (`test_retention_keeps_every_stage_of_a_canary_for_the_anytime_valid_rule`).

The notes below are still open for later merges.

## For the integrator: writers of examples added later

`Ratchet._store_example` and `feedback.store_label` (lane E2) are the writers of examples now, and both ask. Every
other entry that joins `ALLOWED_ADD_EXAMPLE_CALLERS` at a later merge (lane F's clean-room backfill, which writes examples with origin
`backfill`) must ask `Ratchet.store_opt_out` before `Ledger.add_example`: at least zero retention (`global`) and no-store
tasks (`task`), which need no request meta (`store_opt_out(task, None)`), and the request's end user when the source row
has one. `test_every_add_example_caller_honours_storage_opt_outs` fails until it does; also add a behaviour test that a
backfill under `RATCHET_STORE_EXAMPLES=0`, and one for a no-store task, stores nothing.

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
