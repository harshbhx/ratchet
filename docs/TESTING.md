# Testing

```bash
pip install -e ".[dev]"
pytest -q                     # 108 tests (test_core, test_system, test_hardening, test_scout, test_traceability), ~20 s
python scripts/reqtrace.py --check                   # requirements traceability (also in make lint and CI)
pytest -q --cov=ratchet --cov=sim --cov-report=term   # with coverage (as in CI)
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3
python -m sim.simulate --workload research --days 8 --requests 1500   # Scout's triage + reader calls
```

| File | Tests |
|---|---|
| `tests/test_core.py` | 19 (15 functions; one has 5 cases) |
| `tests/test_system.py` | 7 |
| `tests/test_hardening.py` | 23 |
| `tests/test_scout.py` | 15 |
| `tests/test_traceability.py` | 44 (22 functions; one has 23 cases) |
| **Total** | **108** |

There are three layers:

1. **Unit tests** (`tests/test_core.py`): the pure logic of each module.
2. **System tests** (`tests/test_system.py`, `tests/test_scout.py`): the whole loop over simulated days, the gateway API, the HTTP clients, and Scout end to end.
3. **Simulation** (`sim/`): a reproducible offline world with a mock teacher, mock students that learn, synthetic traffic, drift and fault injection. It is used for demos, and to find design bugs before spending on GPUs.

Every test names the requirements it proves with `@pytest.mark.req`, and `tests/test_traceability.py` checks that the PRD, the invariants, the code and the tests agree ([Traceability](#traceability)).

None of these touch Token Factory or a GPU. Live behaviour is checked with `scripts/day1_check.py` and the steps in [DEPLOYMENT.md](DEPLOYMENT.md#5-verification-after-deploy).

## What each test covers

### `tests/test_core.py`

| Test | Covers |
|---|---|
| `test_task_agree_ignores_free_text_and_reasoning` | `Task.agree` compares only `compare_keys`, case-insensitively, and ignores `<think>` blocks and code fences |
| `test_invalid_json_never_agrees` | Two invalid answers never count as agreeing |
| `test_unanimous_confident_samples_score_high` | 3 identical samples at p=0.95 give agreement 1.0 and confidence above 0.9 |
| `test_disagreement_lowers_confidence` | 2-of-3 majority gives agreement 2/3, confidence below 0.85, and the majority answer is chosen |
| `test_all_invalid_scores_zero` | All invalid samples give confidence 0 |
| `test_calibrate_threshold_hits_target_precision` | The calibrated threshold actually reaches the target precision, **including ties** (the regression test for bug 5 below) |
| `test_closed_teachers_are_refused` (5 cases) | `gpt-5.6-sol`, `openai/gpt-4o`, `claude-opus-5-5`, `anthropic/claude-haiku`, `gemini-3.1-pro` all raise `LicenseError` |
| `test_open_teacher_allowed_and_unknown_refused` | Ultra maps to OpenMDW-1.1; an unknown model is refused |
| `test_examples_dedupe_and_count_and_stable_split` | Duplicate requests are stored once with `seen=2`; the split is the same in a fresh ledger |
| `test_bill_of_materials_lists_licenses` | BOM groups by license and has a 64-character fingerprint |
| `test_wilson_bounds` | 19/20 interval contains 0.95; n=0 gives (0, 1) |
| `test_cold_start_goes_to_canary_and_bad_candidates_rejected` | Golden precision 0.80 is rejected; with no live version, a good candidate goes to canary at 10% |
| `test_regression_vs_live_is_rejected_and_good_one_shadows` | A candidate worse than live on coverage is rejected; an equal-or-better one goes to shadow; live is untouched |
| `test_canary_advances_then_goes_live` | 12 clean audits move canary 10 → 50; with no new audits it **holds** at 50 (per-stage counting); 12 more promote it to live and retire the old one |
| `test_canary_rolls_back_on_strong_evidence` | 20 audits at 50% precision cause a rollback; live is unchanged; a `rollback` event is written |

### `tests/test_system.py`

| Test | Covers |
|---|---|
| `test_loop_learns_and_saves_money` | 6 days × 1,500 requests with no drift: day 1 is 0% student; final day is above 80% student and above 60% savings; a version reaches `live`; daily audited agreement never falls below 85% |
| `test_bad_deploy_is_rolled_back_and_users_return_to_live` | Fault-injects `student-v3`; v3 ends `rolled_back` and v1 stays `live` (skips if v3 was rejected before deploy for that seed) |
| `test_drift_raises_escalations_then_recovers` | 9 days with a product launch on day 5: escalations rise on day 5 and are lower by day 9 |
| `test_provenance_only_lists_distillable_teachers` | Every BOM source is Ultra with OpenMDW-1.1 |
| `test_gateway_openai_shape_and_demo_flow` | Mock gateway: `/` (landing page) returns 200; `/ratchet/demo/day` works; `/v1/chat/completions` returns a valid `chat.completion` with the right category; stats count 401 requests; break-next and reset work |
| `test_gateway_rejects_bad_requests` | Empty `messages` returns 400; a `stream: true` request that fails validation gets a normal HTTP 400 before any stream starts |
| `test_openai_compat_provider_parses_logprobs_and_usage` | `OpenAICompatProvider` against an `httpx.MockTransport`: sends `logprobs` and `n`, sends the bearer header, parses logprobs per choice, splits completion tokens across choices, computes `mean_prob` |

### `tests/test_hardening.py`

Regression tests for the issues in [Issues found while documenting](#issues-found-while-documenting-and-their-fixes): `test_old_database_file_is_migrated`, `test_auto_day_counts_days_from_first_request`, `test_student_failure_falls_back_to_teacher`, `test_fallback_model_uses_default_threshold`, `test_teacher_failure_returns_502`, `test_unknown_tasks_do_not_grow_registry`, `test_live_alert_fires_once_per_version`, `test_policy_from_env`.

The v0.4 hardening tests (privacy, API keys, streaming, signed BOMs) are in the same file. The `gw_env` fixture reloads the gateway in mock mode with extra environment variables and reloads it again with the defaults afterwards, so settings such as API keys never leak into other tests.

| Test | Covers |
|---|---|
| `test_redact_finds_each_kind_and_checks_checksums` | Every label is found in one text (two phones, ten placeholders); a card number that fails Luhn is left alone |
| `test_redact_leaves_ordinary_text_alone` | Order numbers, dates, times, prices, version strings, page counts, URLs and an ISBN are not touched |
| `test_redact_messages_handles_content_parts` | OpenAI content-part lists are redacted part by part; non-text parts and the caller's copy are untouched |
| `test_redaction_is_a_no_op_on_the_mock_world` | 2,000 requests from the `mixed` stream and their teacher answers are unchanged by redaction, so mock lookups and simulation numbers cannot shift |
| `test_examples_are_stored_redacted_but_the_teacher_sees_the_original` | The teacher receives the email and phone; the stored example has `[EMAIL]` and `[PHONE]` in messages and completion, `redactions == 3`, and `privacy_stats` agrees; with `redact_pii=False` the example is stored verbatim |
| `test_keyring_parses_and_identifies` | Named and bare keys, lookup, short keys, bad names and duplicate names raise; `bearer()` reads `Authorization` and `X-API-Key` |
| `test_api_keys_protect_v1_and_name_the_client` | No key or a wrong key gets 401 on `/v1/chat/completions` and `/v1/models`; Bearer and `X-API-Key` both work; `clients` in stats lists both names and never the keys; Scout stays public |
| `test_daily_quota_per_key` | Quota 2: remaining-requests header counts down, the third call gets 429 with remaining 0, and another key is unaffected |
| `test_streaming_matches_the_non_streaming_answer` | `stream: true` returns `text/event-stream`, a role chunk first, content chunks that join into exactly the non-streaming answer, a final chunk with `finish_reason: "stop"` and the `ratchet` block, then `[DONE]` |
| `test_signed_bom_per_adapter_verifies_and_detects_tampering` | With a signing key, a simulated night stores a BOM that matches the schema, lists only distillable sources and verifies with the public key; editing it breaks the SHA-256, re-hashing still fails the signature, and another key fails; unknown versions get 404 |
| `test_bom_is_stored_unsigned_without_a_key_and_cli_verifies` | Without a key, a simulated night stores an unsigned BOM whose dataset hash matches the version; the CLI `verify` and `keygen` commands work |
| `test_bad_signing_key_fails_at_startup` | A key that is not base64, or not 32 bytes, raises |
| `test_v03_database_gets_v04_columns` | A v0.3 database file gets `client`, `redactions` and `bom` on open |
| `test_healthz_stays_public_when_keys_are_on` | `/healthz` answers 200 while `/v1/models` needs a key |
| `test_redact_preview_endpoint` | `POST /ratchet/redact` returns the redacted text and counts; over-long or missing text gets 400 |

### `tests/test_scout.py`

The `gw` fixture reloads the gateway in mock mode with `RATCHET_MOCK_WARMUP_DAYS=0`.

| Test | Covers |
|---|---|
| `test_examples_match_the_research_world` | `research.EXAMPLES` equals the UI's `EXAMPLE_QUESTIONS`; every planned query returns simulated results |
| `test_scout_run_emits_the_documented_events` | A run emits `start` … `done`; steps have exactly the documented keys; planner first, writer last, all five agents present; one reader step per relevant triage; `llm_calls` excludes searcher steps; `cost_usd` equals the sum of the steps |
| `test_pinned_tasks_go_to_teacher_and_are_never_training_data` | `research-plan` and `research-write` are pinned, `source-triage` is not; after a run the only stored example tasks are `source-triage` and `page-read`; `stats_by_task` shows 0% student share for the planner and one writer request |
| `test_student_learns_scout_calls_over_simulated_days` | After 4 simulated days of `mixed` traffic a version is live, and a Scout run has at least 60% student calls and more than 30% savings |
| `test_research_workload_simulation_learns` | `--workload research`, 6 days × 1,200: day 1 is 0% student; the last day is above 80% student and above 60% savings |
| `test_gateway_scout_routes` | `/`, `/scout`, `/dashboard` return 200; `/scout/examples` in mock mode; `/scout/run` returns events ending in `done`; non-example and blank questions get 400; `/scout/stream` streams `start` … `done` with HTTP 200, and a bad question gives HTTP 200 with a single `error` event; `/scout/status`; `tasks` in `/ratchet/stats`; `/ratchet/demo/day?workload=research` works and an unknown workload gets 400 |
| `test_rate_limit_returns_429` | With a guard of 2 runs per hour, the third `/scout/run` gets 429 |
| `test_guard_budget_and_hourly_window` | Per-IP hourly limit, the window rolling over after an hour, the budget refusing new runs once spent, and a new UTC day resetting it (fake clock) |
| `test_tavily_search_sends_bearer_and_parses_results` | `TavilySearch` against `httpx.MockTransport`: bearer header, `search_depth: basic`, `max_results`; results without a URL are skipped; a missing title falls back to the URL; snippet ≤ 500 and excerpt ≤ 1,500 characters with whitespace collapsed; an empty key raises |
| `test_scout_survives_bad_json_from_models` | When every model call returns non-JSON, nothing is judged relevant, nothing is read, and the writer's raw text becomes the answer |
| `test_guard_caps_runs_per_day` | `max_runs_per_day=2` refuses the third run of the day and resets the next UTC day |
| `test_demo_controls_are_serialised_and_reset_has_a_cooldown` | A demo action while another holds `_demo_lock` gets 409; a second reset within 30 s gets 429 |
| `test_failed_run_returns_502_and_its_spend_still_counts` | When the writer's call fails, `/scout/run` returns 502 and the planner, triage and reader spend is still added to the budget |
| `test_stats_mark_pinned_tasks` | `/ratchet/stats` `tasks` reports `pinned` for the planner and writer tasks only |
| `test_react_app_is_served_with_lite_fallback` | With the committed build, `/`, `/scout` and `/dashboard` return the React shell and `/assets/*` is served; `/lite*` return the hand-written pages; without a build `/` falls back to the lite page |

### `tests/test_traceability.py`

Every test here carries `NFR-10` through a module-level `pytestmark`.

| Test | Covers |
|---|---|
| `test_ids_unique_and_statuses_parse` | Every PRD id and invariant is well formed and unique, every status matches the grammar, invariants are numbered 1..N, §7 has a Status column, §6.12–6.14 exist |
| `test_done_requirements_have_tests_or_known_gap` | Rule (b) on the repo: every done, partial or met requirement, user story and invariant has a passing test, an `EXEMPT` entry or a known gap |
| `test_known_gaps_and_exemptions_only_shrink` | `KNOWN_GAPS` ⊆ `INITIAL_KNOWN_GAPS`; no known gap or manual exemption that now has a test; every exemption's evidence file exists |
| `test_markers_cite_known_ids` | No marker cites an unknown or replaced id; markers are string literals |
| `test_planned_ids_only_in_strict_xfail` | A test citing a `Planned` id is a strict xfail; no test cites a `Not checked` metric |
| `test_not_met_metrics_have_strict_xfail` | Each `Not met` metric has a strict-xfail test |
| `test_code_refs_resolve` | Every done requirement and invariant has a `CODE` entry, and every entry imports and resolves (or the file exists) |
| `test_live_unverified_have_offline_test_and_live_exemption` | Each `untested (live)` row has a `live` exemption and an offline test (or is a known gap) |
| `test_requirements_md_is_current` | `docs/REQUIREMENTS.md` equals a fresh `render()`, and rendering twice gives the same text |
| `test_every_route_documented_in_api_md` | Every route in the gateway's OpenAPI schema appears in `docs/API.md` with its method on the same line |
| `test_every_env_var_documented` | Every variable read under `ratchet/` (`_env`, `os.environ`, `os.getenv`, the `ReleasePolicy` fields) has a row in `docs/CONFIGURATION.md` |
| `test_doc_test_counts_match` | Every "N tests" claim and per-file count in the docs equals the parametrize-expanded count |
| `test_ast_count_matches_pytest_collection` | `pytest --collect-only` in a subprocess collects exactly the AST count per file |
| `test_check_cli_passes_and_writes_the_committed_matrix` | `--check` exits 0 and prints `OK`; writing to `--out` reproduces the committed matrix |
| `test_mini_world_is_clean_and_renders` | A tiny PRD, CLAUDE.md and test file pass every core rule and render the expected rows, an empty section, xfail tests and a live check; a table inside a code block is not parsed |
| `test_each_mistake_gives_exactly_one_error` (23 cases) | Bad status wording, a cited replaced id, a duplicate id, an unknown marker id, a malformed id, an FR row in the wrong section, a metric status on an FR row, a missing replacement target, a done row without a test, a planned id without strict xfail, a `Not met` metric without one, a cited `Not checked` metric, a known gap that has a test, a grown `KNOWN_GAPS`, a tested manual exemption, missing evidence, a live row without a live exemption or without an offline test, a broken or missing `CODE` ref, a non-literal marker, an untested invariant and a requirement proved only by a skipped test each give exactly one error |
| `test_status_grammar` | Each status form gives its flags; the old wording (`untested live`), free text after `Done (vX);` that does not end in `planned`, a metric status on an FR row and an empty status are errors |
| `test_scan_reads_markers_and_counts_cases` | The AST scan reads module, class and function marks, nested classes, stacked and name-referenced `parametrize`, skips fixtures and classes with `__init__`, reports an uncountable `parametrize`, and treats a `@pytest.mark.skip` test as no proof |
| `test_missing_sources_are_findings_not_crashes` | With no PRD, CLAUDE.md, API.md or CONFIGURATION.md, every rule reports what is missing instead of raising |
| `test_doc_rules_catch_drift` | A wrong "N tests" line and a wrong per-file count are reported with file and line; a route documented without its method is reported; the env-var scan finds `os.environ.get`, `getenv`, `_env`, `os.environ[...]` and `_int` reads and skips non-literal names and writes |
| `test_invariants_keep_their_list_numbers` | INV-n is the list number; a gap in the numbering is one error; continuation lines join the item |
| `test_clip_keeps_table_cells_valid` | Matrix text is at most 100 characters, ends in an ellipsis, never leaves an open code span or half an escaped pipe |

The React app itself is checked with `npx tsc --noEmit` and a production build in CI (`web` job), and by Playwright screenshots during development (light, dark, 1440 and 390 px).

Not covered by tests yet: `RATCHET_TRUST_PROXY`, the mock warm-up at startup (it runs the same `simulate_day` the tests exercise), and the live-mode BOM file written by `python -m ratchet.live night`.

During development the v0.4 features were also checked end to end against a running gateway with the official OpenAI Python SDK (401 on a wrong key, streaming text identical to the non-streaming answer, the quota cutting one key off at its limit while another kept working, a signed BOM verifying with `python -m ratchet.bom verify` and failing once edited), and the dashboard's Trust and privacy panel with Playwright in light and dark mode at desktop and phone widths.

## Simulation methodology

`sim/simulate.py` runs `--days` days. Each day sends `--requests` requests from the chosen `--workload` through `Ratchet.handle()`, calls `ReleaseGate.tick()` ten times, then runs `run_night()`. It prints a day table and the event log, and writes JSON (`--out`, default `sim_results.json`).

| Flag | Default | Meaning |
|---|---|---|
| `--days` | 8 | Days to simulate |
| `--requests` | 2000 | Requests per day |
| `--threshold` | 0.80 | Fallback threshold before calibration |
| `--audit-rate` | 0.10 | Higher than live (0.05) so canaries move in a few days |
| `--min-shadow` | 100 | `ReleasePolicy.min_shadow` |
| `--min-audits` | 15 | `ReleasePolicy.min_audits` (live default 20) |
| `--min-new` | 50 | `run_night(min_new_examples=…)` |
| `--drift-day` | 5 | Product launch day (0 = never) |
| `--bad-deploy-version` | 0 | Fault injection: this version is served corrupted once in canary/live |
| `--workload` | `tickets` | `tickets` (support tickets), `research` (Scout's triage and reader calls), `mixed` (half each). With `tickets` the night trains on `ticket-triage` only; otherwise on every task |
| `--seed` | 7 | Seed for traffic and routing |
| `--out` | `sim_results.json` | Results file |

### Assumptions (these drive the numbers; they are not measurements)

**Traffic** (`sim/tickets.py`):

- 20 templates across 6 categories (billing, technical, account, shipping, refund, security) and 4 priorities. 6 of them are marked `hard`.
- Names, products and order numbers vary, so some templates produce many distinct texts and others very few (frequent repeats).
- From `--drift-day`, 20% of traffic comes from 3 new "Ratchet Watch" templates the student has never seen (1 of them hard).

**Research traffic** (`sim/research.py`, `--workload research`):

- 8 questions about well-known ML ideas, each phrased 4 ways, each with a pool of 72 simulated search results (12 templates × 6 variants). 60% of requests are `source-triage` calls, 40% `page-read` calls.
- Each template has fixed labels. 6 of the 12 are `hard` (for example a good forum answer next to an off-topic one, or a broad survey paper).
- From `--drift-day`, 20% of traffic comes from questions 7 and 8, which the student has not seen.
- `mixed` alternates between the ticket and research streams at random (50/50, its own seed), so both keep their own sequences.

**Mock teacher:**

- Always right on the labels.
- Bills `approx_tokens(prompt)` input plus answer and 300 reasoning output tokens.
- 2.4 s latency.

**Mock student skill** for a ticket of template `t`:

- If its exact text was in training: skill = 0.985.
- Otherwise: skill = max(0.35, CEIL × (1 − e^(−n/6))), where `n` is the number of distinct training examples of `t`, and CEIL = 0.985 (easy) or 0.86 (hard).
- Whether the student "knows" a ticket is fixed per (version, text).

**Sample behaviour:**

- **Knows:** each sample is right with probability 0.97; token probability 0.80–0.97.
- **Doesn't know, confidently wrong** (15% of unknown cases): all samples give the same wrong labels with probability 0.62–0.92. This deliberately overlaps with correct answers, so calibration cannot separate them perfectly. That is the case audits and shadow exist for.
- **Doesn't know, otherwise:** random labels, probability 0.35–0.65.

Research items use the same skill model, per result template. Scout's pinned planner and writer prompts are answered by the mock teacher only.

**Fault injection:**

- The corrupted version is fine on the staging copy, which golden eval and shadow use.
- Once it reaches canary or live, its "fragile" slices become confidently wrong: billing, refund and security tickets; `paper` and `docs` triage results; `date` reads. This models pushing the wrong adapter file to production.

**Costs:** Ultra $1/$3 per 1M tokens. The student uses the $0.06/$0.24 proxy, with 3 samples billed.

### Typical result (seed 7, 10 days, 2,000/day, drift day 5, bad deploy v3)

```
day  student  escal. audit ok     $/1k all-Ultra  saved
  1       0%      0%        -    1.093     1.093     0%
  2      44%      1%     100%    0.675     1.093    38%
  3      98%      2%     100%    0.160     1.093    85%
  4      98%      2%      93%    0.160     1.093    85%   v3 canary → rolled back (41% audited precision)
  5      86%     14%      97%    0.272     1.093    75%   product launch
  6      88%     12%      92%    0.246     1.093    77%
  7      88%     12%      97%    0.255     1.093    77%
  8      95%      5%      99%    0.200     1.093    82%
  9      98%      2%      97%    0.162     1.093    85%
 10      98%      2%      99%    0.147     1.093    87%
total saved 69%
```

### Seed robustness (10 days, 2,000/day, drift day 5, bad deploy v3)

| Seed | Final student share | Final-day savings | Total savings | Min daily audit agreement | Bad v3 outcome |
|---|---|---|---|---|---|
| 1 | 98% | 85% | 69% | 97% | rejected before deploy |
| 2 | 98% | 86% | 66% | 96% | rejected before deploy |
| 3 | 98% | 87% | 67% | 93% | rolled back |
| 11 | 98% | 85% | 66% | 92% | rolled back |

"Total" includes the cold-start days, when all traffic goes to the teacher.

The ticket numbers above are the same as in v0.1. Adding the research world did not change the random draws for tickets ([sim/CLAUDE.md](../sim/CLAUDE.md)).

### Workloads: research and mixed

Simulation, mock models. 8 days × 1,500 requests/day, drift day 5, no fault injection (selected days shown):

```
python -m sim.simulate --workload research --days 8 --requests 1500     (seed 7)
day  student  escal. audit ok     $/1k all-Ultra  saved
  1       0%      0%        -    1.133     1.133     0%
  2      16%      2%     100%    0.979     1.131    13%
  3      82%     14%      99%    0.319     1.132    72%
  4      95%      5%      99%    0.190     1.132    83%
  5      95%      5%      96%    0.194     1.132    83%
  8      96%      4%      99%    0.172     1.132    85%
total: 12000 requests, $4.98 vs $13.58 all-Ultra -> saved 63%

python -m sim.simulate --workload mixed --days 8 --requests 1500        (seed 7)
day  student  escal. audit ok     $/1k all-Ultra  saved
  1       0%      0%        -    1.112     1.112     0%
  3      21%      0%     100%    0.912     1.112    18%
  4      97%      3%      98%    0.174     1.111    84%
  5      92%      8%      97%    0.205     1.111    82%
  8      98%      2%      99%    0.159     1.112    86%
total: 12000 requests, $6.12 vs $13.34 all-Ultra -> saved 54%
```

Seeds 1, 2, 3, 7, 11 (same settings):

| Workload | Final student share | Final-day savings | Total savings | Lowest daily audit agreement | First version live |
|---|---|---|---|---|---|
| research | 96–98% | 85–86% | 57–65% | 96–98% | day 2–3 |
| mixed | 92–98% | 79–86% | 54–63% | 93–96% | day 2–3 |

On mixed traffic the first student comes a night later with seed 7 (v1 was rejected at 94.3% golden precision), so total savings are lower.

## Bugs the simulation found (and the fixes)

Running the loop end to end exposed design flaws that unit tests would not have caught. Each fix is in the code now.

| # | Symptom in the simulation | Root cause | Fix |
|---|---|---|---|
| 1 | Every new candidate rejected as "regresses vs live" for days | Candidate measured on today's golden set, live measured on its own (older, easier) golden set from the day it was trained | **Champion vs challenger:** `run_night` re-evaluates the live version on the same golden set first and overwrites its eval (`rechecked_day`) |
| 2 | Every candidate rejected in shadow at ~70% "agreement" | Shadow judged on all traffic, including requests it would have escalated; and an agreement with the live student was the only reference | Shadow runs the full cascade: records `shadow_serve` and judges **precision only on what it would serve**, plus **coverage** vs live. Disagreements with the live student are **arbitrated by the teacher** |
| 3 | Student never learned the most common questions | The hash-based golden split held some frequently repeated requests out of training forever | Requests seen at least `FREQUENT = 3` times always go to training and leave the golden set |
| 4 | Canary never finished; promotions stalled | Each night's new candidate replaced the version in canary | `run_night` **skips training while a shadow or canary is in flight**, which also saves GPU time |
| 5 | Threshold promised 95% precision but delivered 94% | `calibrate_threshold` checked precision before including all points tied at the same confidence | Walk down in groups of equal confidence; return `inf` (serve nothing) if no threshold qualifies; `cascade_eval` caps it at 1.01 |
| 6 | Golden coverage 94% vs real coverage 80% | Deduplication meant the golden set over-represented rare phrasings, and the threshold was calibrated and measured on the same rows | `seen` count stored per example; **calibrate on one half of the golden set, measure on the other**; frequent requests handled by fix 3 |

Two more problems were fixed earlier in development. A fixed global threshold let about 10% wrong answers through, which led to per-version calibration ([ADR 0003](adr/0003-per-request-cascade-with-calibrated-confidence.md)). A point-estimate rollback rule would have rolled back healthy canaries about 19% of the time at n=20, which led to the Wilson upper bound ([ADR 0005](adr/0005-shadow-canary-rollback-with-wilson-bounds.md)).

## Issues found while documenting, and their fixes

Writing these docs turned up ten gaps in v0.1. Each one is fixed in v0.1.1 and covered by `tests/test_hardening.py`.

| # | Issue | Fix | Test |
|---|---|---|---|
| 1 | Live gateway never advanced `Ledger.day`, so every request landed in one bucket | `Ledger(auto_day=True)` in live mode: day 1 starts at the first logged request, one day per 24 hours | `test_auto_day_counts_days_from_first_request` |
| 2 | A student HTTP error became a 500 | Student errors fall back to the teacher and log a `student_error` event (1st, 10th, 100th, 1000th). A teacher error returns **502** | `test_student_failure_falls_back_to_teacher`, `test_teacher_failure_returns_502` |
| 3 | Runtime-loaded LoRA adapters are lost when the endpoint restarts | New command `python -m ratchet.live reload` re-loads live, canary and shadow adapters | Manual (needs a vLLM endpoint); see [RUNBOOK.md](RUNBOOK.md) |
| 4 | The untuned fallback model was judged with the tuned version's threshold | `Ratchet.threshold(version, model)` uses the global default for `RATCHET_FALLBACK_STUDENT` | `test_fallback_model_uses_default_threshold` |
| 5 | `alert` events repeated on every tick | One alert per live version | `test_live_alert_fires_once_per_version` |
| 6 | No schema migration for existing database files | `Ledger._migrate()` adds missing columns on open (`MIGRATIONS` table in `ledger.py`) | `test_old_database_file_is_migrated` |
| 7 | Stale docstring and column comment | Fixed | n/a |
| 8 | Unknown `X-Ratchet-Task` values were stored forever in `tasks.REGISTRY` | Unknown names get a temporary whole-text task; names must match `[A-Za-z0-9_.-]{1,64}` | `test_unknown_tasks_do_not_grow_registry` |
| 9 | `ReleasePolicy` could only be changed in code | `ReleasePolicy.from_env()` reads `RATCHET_<FIELD>` (e.g. `RATCHET_MIN_AUDITS`) | `test_policy_from_env` |
| 10 | Unsloth LoRA targets might miss Nemotron-H Mamba layers | `train.py --targets` (default adds `in_proj,out_proj`); confirm names on day 1 | Manual (GPU) |

Still open: `RATCHET_SYNC_CMD` and `RATCHET_TRAIN_CMD` run without a shell, so pipes and `&&` do not work; wrap them in a script.

## Traceability

[REQUIREMENTS.md](REQUIREMENTS.md) maps every requirement to the code that implements it and the tests that prove it. `scripts/reqtrace.py` generates it (`make trace`) and checks it (`python scripts/reqtrace.py --check`, part of `make lint` and a CI step after pytest). It uses only the standard library, reads the PRD, CLAUDE.md and the tests with regular expressions and `ast`, and finishes in about half a second.

Sources of truth:

| Source | What it holds |
|---|---|
| [PRD.md](PRD.md) | FR, NFR and SM rows (id, text, Status) and the US stories. Statuses follow the grammar in [PRD §6](PRD.md#6-functional-requirements) |
| [CLAUDE.md](../CLAUDE.md) `## Invariants` | INV-n is the list number, so invariants are never renumbered |
| `@pytest.mark.req("FR-2.1", "INV-7")` in `tests/` | Which tests prove which ids. A module- or class-level `pytestmark` works too. Registered in `pyproject.toml`, and `--strict-markers` makes a misspelled marker an error |
| `CODE` in `scripts/reqtrace.py` | Where each done requirement and each invariant lives: `ratchet.router:Ratchet._sample` (imported and resolved) or a repo path for UI, shell and Docker files |
| `EXEMPT` in `scripts/reqtrace.py` | Requirements checked outside the offline suite: `manual` (the dashboard UI, the CI simulation smoke test) or `live` (needs Nebius, a GPU or a real key), each with the file that holds the check |
| `KNOWN_GAPS` in `scripts/reqtrace.py` | Done requirements without a test. It must stay a subset of the frozen `INITIAL_KNOWN_GAPS` (30 ids when the check landed), so it can only shrink |

The check fails when:

| Rule | Fails when |
|---|---|
| (a) | An id is malformed (`FR-a.b`, `NFR-n`, `US-n`, `SM-n`, `INV-n`) or duplicated, an `FR-a.b` row is not in section 6.a, a status is outside the grammar, invariants are not numbered 1..N, or a `Replaced by` target does not exist |
| (b) | A done, partial or met requirement, a user story or an invariant has no passing test, no `EXEMPT` entry and is not a known gap. An xfail or `@pytest.mark.skip` test is not proof |
| (c) | `KNOWN_GAPS` grows, or a known gap or a manual exemption has a test now ("remove it"); an exemption's evidence file is missing |
| (d) | A marker cites an unknown or replaced id, is not a string literal, or cites a `Planned` id without `@pytest.mark.xfail(strict=True)`; or a test cites a `Not checked` metric (give the metric a status) |
| (e) | A done requirement or an invariant has no `CODE` entry, or an entry does not import, resolve or exist |
| (f) | An `untested (live)` row has no `live` exemption, or no offline test and is not a known gap |
| (g) | A `Not met` metric has no strict-xfail test (so the test fails loudly once the target is met) |
| (h) | `docs/REQUIREMENTS.md` differs from a fresh render |
| (i) | A gateway route (from `ratchet.gateway.app.routes`, imported in mock mode) is missing from [API.md](API.md); an environment variable read under `ratchet/` is missing from [CONFIGURATION.md](CONFIGURATION.md); or an "N tests" claim in `CLAUDE.md`, `tests/CLAUDE.md`, this file, `README.md`, `DEPLOYMENT.md` or `HACKATHON_SUBMISSION.md` differs from the parametrize-expanded count |

To change behaviour:

1. Update or add the PRD row (or the invariant in CLAUDE.md) in the same commit.
2. Mark the test that proves it with `@pytest.mark.req("FR-a.b")`. If the id was a known gap, delete it from `KNOWN_GAPS`.
3. Add a `CODE` entry for a new done requirement or invariant. Something that can only be checked by hand or live gets an `EXEMPT` entry with the file that holds the check; an `untested (live)` row also needs an offline test.
4. Run `make trace` and commit `docs/REQUIREMENTS.md`, then `make lint test`.

## Adding tests

- Pure logic goes in `test_core.py` with small hand-built ledgers (`Ledger(":memory:")`). Use `_version()` and `_audit()` to set up release states.
- Scout, the research world and the guard go in `test_scout.py`. Use the `gw` fixture (mock gateway, no warm-up) and fake clocks for the guard.
- Loop behaviour goes in `test_system.py` through `sim(...)` with fixed seeds. Assert on trends (rising share, falling escalations), not exact numbers, so tuning the mock does not break tests.
- Never call real APIs in tests. Use `httpx.MockTransport` as in `test_openai_compat_provider_parses_logprobs_and_usage`.
- Mark every new test with the requirement ids it proves (`@pytest.mark.req`), keep `parametrize` lists literal so the suite can be counted statically, and update the test counts above ([Traceability](#traceability)).
