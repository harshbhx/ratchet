# Lane G1: proposed invariants

Invariants lane G1 proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id. Each entry names its text, the code that enforces it and the tests that prove it. Lane G1 also reworded INV-11 in place (same id): live mode restores the guard's daily counters from the ledger.

## Judge access, a persistent Scout guard and Tavily credits (item SCOUT-JUDGE-ACCESS, FR-10.20 to FR-10.22, ADR 0120)

### The judge code is handled like a key

**Text.** `RATCHET_SCOUT_JUDGE_CODE` is never stored, logged, echoed or shown in an error message, and never travels in a URL or a custom header (uvicorn and Caddy log both). The gateway keeps only its SHA-256 digest and an HMAC key derived from it, compares digests in constant time, accepts it only in a `POST /scout/judge` body parsed by hand (no validation error can echo it) and carries the session in a signed `HttpOnly`, `SameSite=Strict` cookie. Only that cookie makes a Scout run a judge run; a code in a query or a body is ignored. `Settings.scout_policy()` never prints the code.

**Enforcing code.** `ratchet/scout/judge.py`: `JudgeAccess`, `AttemptLimiter`; `ratchet/gateway.py`: `scout_judge`, `_judge_session`; `ratchet/config.py`: `Settings.scout_policy`, `Settings.scout_judge_code` (`repr=False`), `ScoutPolicy`.

**Proving tests.** `tests/test_scout.py::test_judge_code_never_reaches_logs_ledger_events_or_status`, `test_judge_code_unlocks_the_reserve_over_post_only`, `test_judge_cookie_tampering_rotation_and_expiry`, `test_judge_login_attempts_are_limited`; `tests/test_live.py::test_live_judge_cookie_is_secure`.

### Scout's spend rows are not billable and hold no request data

**Text.** Scout's `spend` rows (`scout_run`, `tavily`) keep `usd` 0 and their amounts in `detail`, because their model calls are already in `requests.cost_usd` and receipts subtract `spend.usd`. Their `detail` holds only the pool, amounts, statuses and HTTP codes: never an IP, the question, a URL, an error message or the judge code. Only `scout_run` rows are ever updated (`MUTABLE_SPEND_KINDS`), and never their `usd`. The guard and the credit meter restore from these rows only, never from `requests`.

**Enforcing code.** `ratchet/ledger.py`: `Ledger.add_spend`, `Ledger.update_spend_detail`, `MUTABLE_SPEND_KINDS`, `Ledger.spend_rows`; `ratchet/scout/guard.py`: `Guard.admit`, `Guard.progress`, `Guard.add_spend`, `Guard.restore`; `ratchet/scout/credits.py`: `CreditMeter.settle`, `CreditMeter.restore`.

**Proving tests.** `tests/test_scout.py::test_scout_spend_rows_are_not_billable_and_hold_no_request_data`, `test_mock_scout_writes_no_spend_rows`, `test_guard_state_survives_a_restart`; `tests/test_hardening.py::test_update_spend_detail_refuses_other_kinds`.

### A failed or refused search never ends a Scout run

**Text.** Around every search, `Scout.run` catches `SearchUnavailable` and `httpx.HTTPError`, labels the run (`detail.unavailable`, `done.search`), stops searching and lets the writer answer from what was found; the reason is a fixed refusal string or the error's class and status (`resilience.describe_failure`), never its message or URL. The credit meter never refuses a run, only a search. `/scout/run` answers 200 for such a run; 502 stays for a model failure. Failures (not credit refusals) are counted and written as throttled `search_error` events, so a broken key stays visible.

**Enforcing code.** `ratchet/scout/agents.py`: `Scout.run`; `ratchet/scout/search.py`: `SearchUnavailable`, `UNAVAILABLE_REASONS`; `ratchet/scout/credits.py`: `MeteredSearch`; `ratchet/gateway.py`: `_run_scout`, `Runtime.note_search`.

**Proving tests.** `tests/test_scout.py::test_search_failure_still_calls_the_teacher`, `test_partial_search_keeps_found_results`, `test_public_run_without_credits_still_calls_the_teacher`, `test_search_unavailable_through_the_gateway_counts_spend_and_events`.

## Live-run harness (item LIVE-RUN-HARNESS, FR-8.20 and FR-8.21, ADR 0121)

### The live-run harness writes no text, no key and no base URL

**Text.** `python -m ratchet.liverun` writes only metadata, counts and its own measurements: no request or answer text (answers are scored, then dropped), no key (the gateway key comes only from `RATCHET_LIVERUN_KEY`, the arms' from `NEBIUS_API_KEY_LIVERUN` or `NEBIUS_API_KEY`; results carry the key's name and the variable's name) and no base URL (a resumed or repeated run takes `--base-url` from its own command line), in every file of its run directory, on stdout and in its ledger events. Errors name the exception's class (`resilience.describe_failure`), never its message.

**Enforcing code.** `ratchet/liverun.py`: `Harness._one` and `Harness._fill` (the `_text` fields popped before a record is kept), `UNSAVED_ARGS`, `Harness._open_run`, `Harness._event`, `_settings_block`, `load_file_rows` (errors name the line and field).

**Proving tests.** `tests/test_live.py::test_liverun_writes_no_text_and_no_key`, `test_liverun_scout_phase`, `test_liverun_file_workload`, `test_liverun_gate_profile_and_the_arms_key`, `test_liverun_resume_continues_the_stream`.

### Measurement calls never reach the gateway or the ledger

**Text.** The harness's own model calls (the control arm, the untuned-Nano arm) go from the harness straight to Token Factory or the student endpoint, never through the gateway, so they are never logged as requests, stored as examples or used as golden rows (INV-2, INV-10 hold for them by construction). The harness writes to the gateway's ledger only through `live.main` and three event kinds (`liverun_start`, `liverun_round`, `liverun_stop`), and calls the gateway only at `/healthz` and `/v1/chat/completions` (never `/ratchet/*`, so never a demo route, INV-8).

**Enforcing code.** `ratchet/liverun.py`: `Harness._control_call`, `Harness._untuned_arm`, `Harness._post`, `Harness._event`, `Harness._live`.

**Proving tests.** `tests/test_live.py::test_liverun_control_arm_is_never_stored`, `test_liverun_end_to_end_offline`, `test_liverun_untuned_arm_paired`, `test_liverun_refuses_mock_gateway_and_wrong_ledger`.

### A live run never spends past its cap by more than one batch

**Text.** Before every batch (and before the untuned arm and each Scout run) the harness adds what the run has spent (the key's teacher spend in the ledger, the endpoint's and training jobs' GPU-hours since the run started, the hosted student's tokens without an endpoint, the arms' own calls) to the projected cost of what comes next, and stops with `max_usd` when the sum would pass `--max-usd`; a batch already sent can overshoot by its own cost. The control arm reserves its next call against its own cap before sending it, so concurrent workers cannot overshoot together. A plan over the cap sends nothing (exit 3).

**Enforcing code.** `ratchet/liverun.py`: `Harness.spent`, `Harness._projected`, `Harness._cap_check`, `Harness._replan`, `_Metered.reserve`, `make_plan`.

**Proving tests.** `tests/test_live.py::test_liverun_usd_cap_stops_between_batches`, `test_liverun_control_budget_is_reserved_across_workers`, `test_liverun_plan_refuses_over_budget`.

### Live numbers come only from a published, non-smoke run

**Text.** A `[LIVE NUMBER]` in the docs is filled only from `evidence/live/results.json`, written by `python -m ratchet.liverun report <run dir> --publish`, which refuses a smoke run always and a stopped run without `--allow-partial`; each number carries its `<!-- claim:live.* -->` anchor, and PRD statuses that wait on live evidence move only in the commit that adds the file. `verify` recomputes every client-side value from the per-request records and fails on any mismatch or a value without a source.

**Enforcing code.** `ratchet/liverun.py`: `cmd_report`, `cmd_verify`, `client_values`, `CLIENT_VALUE_IDS`; `docs/CLAUDE.md` (the rule for docs).

**Proving tests.** `tests/test_live.py::test_liverun_report_bill_publish_and_verify`, `test_liverun_repeat_run_and_report`.
