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
