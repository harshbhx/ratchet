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

## Placeholders never link examples (an amendment to invariant 12)

**Text (for CLAUDE.md, appended to invariant 12).** Redaction maps values to placeholders per example only: one
`redact.Pseudonymizer` per stored example, made in `Ratchet._store_example`, used for its messages, then its stored
conditions, then its answer, and dropped. Never keep a mapping across examples, never store an original value or a
mapping to reverse one, and never share a `Pseudonymizer` between requests or threads. In `label` style the redactor
writes exactly what v0.4 wrote, apart from new rules and a card or IBAN typed with a short tail; never add a retry that
tries more windows of a digit run. A detector plugin (`RATCHET_REDACT_PLUGIN`) that fails at store time stores
nothing, never an unredacted example. An example stored in another style or with the plugin records its key under the
default redaction (`canon_hash`), which an erasure tombstones too. See
[ADR 0082](../adr/0082-placeholder-styles-detector-plugin-and-indian-identifiers.md).

This amends invariant 12 rather than adding a number: it is the same rule ("redact before storing"), now with
placeholders that carry a number.

**Enforcing code.**

- `ratchet.router:Ratchet._store_example` (one `Pseudonymizer` per call; messages, conditions, answer; `RedactionError`
  skips the example as `redaction failed`)
- `ratchet.redact:Pseudonymizer` (the per-example mapping), `ratchet.redact:_pattern_spans` and
  `ratchet.redact:_matches` (v0.4's pass, with the short-tail retry for cards and IBANs only)
- `ratchet.ledger:Ledger.add_example` and `ratchet.ledger:Ledger.record_erasure` (`canon_hash` checked and tombstoned)
- `ratchet.redact:load_detector` (startup import and probe) and `ratchet.redact:_detected` (spans checked; errors carry
  neither the text nor the plugin's message)

**Proving tests** (`tests/test_redaction.py`).

- `test_original_values_never_stored` (each style, with the plugin and a stop string): no original in any table
- `test_concurrent_examples_never_share_placeholders`: 16 threads, every example starts at `[EMAIL_1]`
- `test_the_example_hash_never_depends_on_the_answer`: one request, three answers, one example
- `test_label_style_matches_v04`: v0.4's spans and output on 4,500 fuzzed texts and 8,000 long reference numbers
- `test_a_refused_match_no_longer_hides_a_card`: the short-tail rule is the only other difference, on 3,000 fuzzed texts
- `test_long_reference_numbers_store_as_in_v04`: a tracking number in the prompt and the answer stored as in v0.4
- `test_an_erasure_holds_under_every_style_and_plugin`: erased under any of five settings, refused under all five
- `test_a_failing_detector_stores_nothing_and_never_leaks_the_text` (7 kinds of failure)

## For the integrator: notes from PRIV-REDACTION-EXT

- `proof.request_key` / `resolve_key` (lane F, FR-16.4) recompute the key a request is stored under. That key now
  depends on the task's redaction style and the detector: redact with `Pseudonymizer(task.redact_style or
  ratchet.redact_style, ratchet.detector)`, messages first and then the conditions, exactly as `_store_example` does.
  With the default settings (`label`, no plugin) nothing changes.
- Any new writer of examples (lane F's backfill) must redact the same way: one `Pseudonymizer` per example in the
  task's style, and skip the example on `RedactionError`.
- `taskspec.SPEC_KEYS` gains `privacy` (after `store`), so `GET /ratchet/tasks` entries and stored API task documents
  have twelve spec keys; other lanes that add spec keys merge next to it.
- `tombstones` now holds `examples.canon_hash` keys too (an example's key under the default redaction, written when it
  was stored in another style or with the plugin). They are no example's hash: provable erasure (FR-16.9) takes the
  erased hashes from `erasure_items`, not from `tombstones`. A backfill that redacts in a task's non-default style must
  pass `canon_hash` to `add_example` the way `_store_example` does.
- `POST /ratchet/redact` runs the detector plugin only for a caller with a valid API or admin key and adds
  `plugin_skipped` to its response.
