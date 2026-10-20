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

- `ratchet.router:Ratchet.store_opt_out` (the rule: global, task, the caller's verdict, the no-train lookup)
- `ratchet.router:Ratchet._opted_out` and `ratchet.router:Ratchet.handle` (settled once, recorded per request)
- `ratchet.router:Ratchet._store_example` (asks again before `Ledger.add_example`)
- `ratchet.gateway:_request_meta` and `ratchet.privacy:request_opts_out` (the key's and the request's opt-outs;
  any one wins)
- `ratchet.nightschool.orchestrate:run_night` (`store_examples=False` skips the night)

**Proving tests** (`tests/test_privacy.py`).

- `test_store_false_escalation_calls_the_teacher_and_stores_nothing`: no insert, no bump of a stored duplicate
- `test_store_false_still_audits_and_arbitrates_without_storing`
- `test_zero_retention_stores_nothing_for_any_caller`
- `test_store_header_metadata_and_key_opt_outs`: `store: true`, `X-Ratchet-Store: 1` and `metadata.ratchet_store: "true"`
  never undo an opt-out
- `test_no_train_end_users_are_never_stored_even_when_registered_mid_request`
- `test_opt_outs_hold_under_concurrent_requests`
- `test_zero_retention_mode_night_skips`, `test_live_night_skips_under_zero_retention`

## End users are stored only as keyed hashes

**Text (for CLAUDE.md).** **End-user identifiers are never stored or logged.** A request's `user` and
`safety_identifier` are hashed in `gateway._request_meta` with `privacy.EndUsers.hash` (HMAC-SHA256 under the
deployment's key, scoped to the API key name, 16 hex digits) and go no further; only the hash reaches the router, the
ledger and the no-train table, and no error message, event or `/ratchet/stats` field quotes the value or the hash. Like
invariant 13 for API keys. See [ADR 0080](../adr/0080-storage-opt-outs-and-pseudonymous-end-users.md).

**Enforcing code.**

- `ratchet.gateway:_request_meta` (the only place the raw value is read after validation)
- `ratchet.privacy:EndUsers.hash`, `ratchet.privacy:end_users`
- `ratchet.openai_compat:_end_user` (the length error never quotes the value)
- `ratchet.gateway:_no_train_add`, `ratchet.gateway:_no_train_remove` (events carry counts and names only)

**Proving tests** (`tests/test_privacy.py`).

- `test_end_user_is_an_hmac_and_the_raw_value_is_stored_nowhere`: every text value of every table, with redaction off
- `test_after_no_train_registration_escalations_are_not_stored`: no hash or value in the `no_train` events
- `test_no_train_requests_are_validated`: an over-long value is never echoed
