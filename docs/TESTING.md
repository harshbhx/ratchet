# Testing

```bash
pip install -e ".[dev]"
pytest -q                     # 728 tests (test_core, test_tasks, test_registry, test_nightschool, test_release, test_system, test_hardening, test_resilience, test_scout, test_contract, test_openai_compat, test_traceability, test_live, test_cost, test_stats, test_provenance, test_privacy), ~75 s
pytest -q --runslow           # also the full-size Monte Carlo runs marked slow (as in CI)
python scripts/reqtrace.py --check                   # requirements traceability (also in make lint and CI)
pytest -q --cov=ratchet --cov=sim --cov-report=term   # with coverage (as in CI)
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3
python -m sim.simulate --workload research --days 8 --requests 1500   # Scout's triage + reader calls
python scripts/regen_sim_reference.py --check        # does the pinned reference simulation still match? (writes nothing)
python scripts/reference_check.py                    # do tickets, mixed and research still reproduce v0.4.0? (make reference, CI)
```

| File | Tests |
|---|---|
| `tests/test_core.py` | 52 (30 functions; one has 19 cases, one has 5) |
| `tests/test_tasks.py` | 81 (35 functions; one has 28 cases, one has 20) |
| `tests/test_registry.py` | 29 |
| `tests/test_nightschool.py` | 18 (14 functions; one has 5 cases) |
| `tests/test_release.py` | 21 (13 functions; one has 8 cases, one has 2) |
| `tests/test_system.py` | 10 |
| `tests/test_hardening.py` | 80 (54 functions; one has 11 cases, one has 8, one has 6, one has 4, one has 2) |
| `tests/test_resilience.py` | 87 (49 functions; one has 20 cases, two have 7, one has 6, one has 3) |
| `tests/test_scout.py` | 15 |
| `tests/test_contract.py` | 45 (34 functions; one has 9 cases, one has 4) |
| `tests/test_openai_compat.py` | 144 (37 functions; one has 87 cases, one has 22) |
| `tests/test_traceability.py` | 44 (22 functions; one has 23 cases) |
| `tests/test_live.py` | 14 |
| `tests/test_cost.py` | 36 (24 functions; two have 7 cases) |
| `tests/test_stats.py` | 17 (2 of them slow: skipped without `--runslow`) |
| `tests/test_provenance.py` | 13 |
| `tests/test_privacy.py` | 22 |
| **Total** | **728** |

There are three layers:

1. **Unit tests** (`tests/test_core.py`, `tests/test_tasks.py`, `tests/test_nightschool.py`, `tests/test_release.py`, `tests/test_stats.py`, `tests/test_provenance.py`): the pure logic of each module (the exact statistics and the Merkle trees against hand-computed values, brute force and seeded Monte Carlo), and every Night School and release-gate rule tested directly against a small ledger with recording fakes (`tests/fakes.py`).
2. **System tests** (`tests/test_system.py`, `tests/test_resilience.py`, `tests/test_scout.py`, `tests/test_openai_compat.py`, `tests/test_live.py`, `tests/test_cost.py`, `tests/test_privacy.py`): the whole loop over simulated days, the gateway API (including OpenAI request validation and the real OpenAI SDK in-process), the HTTP clients, Scout end to end, and the live-mode and operational entry points run offline ([Live-path tests](#live-path-tests)). One of them pins the reference simulation exactly ([below](#the-pinned-reference-simulation)).
3. **Simulation** (`sim/`): a reproducible offline world with a mock teacher, mock students that learn, synthetic traffic, drift and fault injection. It is used for demos, and to find design bugs before spending on GPUs.

Across the first two layers, the **contract tests** (`tests/test_contract.py`) pin what clients, the dashboard and the license rules depend on, so a rewrite of `Ratchet.handle` or `gateway.chat` cannot change it unnoticed ([Contract tests](#contract-tests)).

Shared fixtures (`gw`, `gw_env`, `day1_check`, `reference_check`, `reference_run`) and the `slow` marker (`--runslow`) are in `tests/conftest.py`; `make_openai_client(app)` (the OpenAI SDK wired to the gateway in-process) is in `tests/sdk_bridge.py`; the recording provider, trainer and evaluator, `FakeClock`, the live-mode doubles (`FakeOpenAIServer`, `RecordingRun`, `AppTransport`) and the ledger builders (`_version`, `_audit`, `_shadow`, `_examples`) are in `tests/fakes.py`. [tests/CLAUDE.md](../tests/CLAUDE.md) lists them.

Every test names the requirements it proves with `@pytest.mark.req`, and `tests/test_traceability.py` checks that the PRD, the invariants, the code and the tests agree ([Traceability](#traceability)).

None of these touch Token Factory or a GPU. Live behaviour is checked with `scripts/day1_check.py` and the steps in [DEPLOYMENT.md](DEPLOYMENT.md#5-verification-after-deploy); `tests/test_live.py` runs the same code paths offline first.

## What each test covers

### `tests/test_core.py`

| Test | Covers |
|---|---|
| `test_task_agree_ignores_free_text_and_reasoning` | `Task.agree` compares only the task's fields (`category` and `priority` as labels), case-insensitively, and ignores `<think>` blocks and code fences |
| `test_invalid_json_never_agrees` | Two invalid answers never count as agreeing |
| `test_strip_reasoning_cases` (19 cases) | FR-2.7: `strip_reasoning` removes a full `<think>` pair, everything before a lone `</think>` (also before a fence, which it keeps), an unterminated `<think>` that opens what is left with what follows (`''`, also after whitespace, a pair or a lone `</think>`), but keeps one after answer text (content, such as a reply that mentions the tag), tags in upper or mixed case, nested and repeated blocks (the last `</think>` wins); text without tags is unchanged apart from surrounding whitespace; `None` is `''`; stripping twice changes nothing |
| `test_clean_answer_unwraps_fences_only_for_structured_tasks` | FR-2.7, FR-2.13: for `ticket-triage`, one fence around the whole answer (`json`, `JSON`, none, single-line, closing fence on the last line) is unwrapped after reasoning is stripped; a free-text task keeps it. Kept as is: a fence whose body is not JSON, two fences, prose around a fence, a four-backtick fence, a body nested too deeply to parse |
| `test_answers_that_mention_think_tags_keep_their_text` | FR-2.7, FR-2.13: a structured answer that is one JSON object quoting `<think>` or `</think>` in a string is kept whole, bare, fenced, after a reasoning pair or after a lone `</think>`; its canonical labels and `parse_json` value are right, and cleaning it twice changes nothing; a free-text answer that mentions `<think>` keeps it; reasoning cut off while holding an object or quoting a tag is still `<invalid>` (review finding) |
| `test_empty_answers_are_invalid_for_every_task` | FR-2.13: an answer that is empty after cleaning (whitespace, unterminated or reasoning-only) is `<invalid>` for free-text and structured tasks, never agrees, and three of them score 0 with `valid` false |
| `test_unanimous_confident_samples_score_high` | 3 identical samples at p=0.95 give agreement 1.0 and confidence above 0.9 |
| `test_disagreement_lowers_confidence` | 2-of-3 majority gives agreement 2/3, confidence below 0.85, and the majority answer is chosen |
| `test_all_invalid_scores_zero` | All invalid samples give confidence 0 |
| `test_length_samples_are_invalid_in_score` | FR-2.13: samples with `finish_reason` `length`, `content_filter`, `abort`, `unknown` or another value but `stop` and `tool_calls` get no vote even when their text parses (three of them: confidence 0, `valid` false); with two cut-off samples and one complete one, the complete one is the answer at agreement 1/3; `tool_calls` votes as usual |
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
| `test_gen_params_from_a_validated_request` | FR-1.13: `GenParams.from_request` takes every generation field of a validated request (`max_completion_tokens` as `max_tokens`, `stop` as a tuple, an unknown field in `extra`) and `to_body` sends them by OpenAI's names, never `user`, `safety_identifier`, `store` or `metadata`; empty `tools` and `logit_bias` count as not sent; `only` keeps the object when nothing is dropped; an extra key never replaces a standard field or sets `model`, `n` or `messages` |
| `test_param_allowlists_parse_and_refuse_what_ratchet_never_forwards` | FR-1.13, INV-1 (and FR-1.14: the default teacher list now includes `logprobs` and `top_logprobs`, only the tool-call fields are left out): empty is the default, `none` nothing, `all` the standard fields, `all,top_k` adds a vendor field, `max_completion_tokens` means `max_tokens`, names match in any case; `model`, `messages`, `n`, `stream`, `user`, `safety_identifier`, `store`, `metadata`, `service_tier`, `functions` and a name that is not an identifier raise, and so does a near miss of a standard field (`max_token`, `stops`, `temprature`, `tool`: "did you mean"), while vendor fields (`top_k`, `min_p`, `min_tokens`, `repetition_penalty`, `max_new_tokens`, `guided_json`) pass; the default lists leave out tool calls and the teacher's logprobs |
| `test_gen_policy_gives_each_role_its_parameters_and_caps` | FR-1.13, NFR-3: the student gets the shaping fields capped at 1,024 and never the client's temperature or logprobs; the serving teacher gets every standard field but tools and logprobs by default (all of them when allowed; a vendor field only when listed), at temperature 0 when none was sent, capped at 8,192; audits and arbitrations run at 0 without `CHECK_DROPS`, keeping limit, stops, format, tools and effort; the headroom adds to the teacher's limit under the cap and `visible_limit` says when an answer can exceed the client's; a field outside an allowlist is never sent but the caps apply; 0 means no cap; bad caps raise |
| `test_stored_conditions_join_the_hash_only_when_set` | FR-1.13, FR-4.3: no conditions, sampling fields only, or the default text format hash like v0.4; `response_format` and `stop` each give another hash; a tuple and a list of the same stops are one example; `GenParams.from_stored` reads stored conditions back and leaves out values of the wrong shape |
| `test_v04_example_hash_unchanged` | FR-1.13, FR-4.3: literals computed with the v0.4 code (a Unicode ticket and a plain request) still come out of `example_hash`, and `add_example` with only a seed stores that hash with no conditions |
| `test_redact_json_redacts_string_values_and_keeps_keys` | FR-1.13, INV-12: emails in a stop string and a tool description become `[EMAIL]` (counted), keys and numbers stay, the input is not changed, and 5,000 levels of nesting are fine |
| `test_served_logprobs_spell_the_served_text_and_drop_reasoning` | FR-1.14: `providers.served_logprobs` returns the tokens as they are when they spell the served text, in OpenAI's shape, without changing the provider's list; drops `<think>` reasoning, a fence and the whitespace `clean_answer` strips (trimming a token only by whitespace, bytes recomputed); returns only the answer's tokens when a server lists reasoning tokens its content leaves out; None when a token holds both the answer and a tag, when reasoning was removed from the middle, or when the tokens do not spell the answer (U+FFFD parts), and when the model sent none; `[]` for an empty answer |
| `test_served_logprobs_check_every_entry_and_keep_the_clients_top_logprobs` | FR-1.14: `top_logprobs` cut to the client's count (none for 0 or unset), malformed alternatives left out; a malformed entry (no logprob, a string, a boolean, NaN, +Infinity, no token, an unpaired surrogate) makes the list None; -Infinity is -9999.0 (`MIN_LOGPROB`); the server's bytes are kept when they are bytes; the fakes' `token_entries` spell their text |
| `test_majority_gives_n_agreeing_choices_with_the_best_sample_first` | FR-1.14, FR-2.2: `confidence.majority` gives the best scored sample first, then the other agreeing samples by token probability, never the minority; None when fewer than n agree, a cut-off or invalid sample never fills a choice, and a repeated object counts twice |
| `test_gen_plan_for_n_choices` | FR-1.14, FR-1.13: `GenPolicy.plan(params, n)`: the serving teacher at 0.7 (`CHOICES_TEMPERATURE`) for n > 1 without a client temperature that reaches it (also when the allowlist leaves temperature out), the client's otherwise; audits at 0; the student's sampling unchanged; the teacher's logprobs only when asked, never in an audit; a bad n is a `ValueError` |

### `tests/test_tasks.py`

Field comparators and task specs (FR-2.14, [ADR 0021](adr/0021-field-comparators-and-task-specs.md)). Pure logic on `compare.FieldSpec` and `tasks.Task`, plus small ledgers and `RecordingProvider` for what the cascade, the golden eval and the dataset do with an answer the spec refuses.

| Test | Covers |
|---|---|
| `test_exact_is_the_v04_rule` | `exact`: strings stripped and lower-cased; `1` and `1.0`, `true` and `1`, lists in another order differ |
| `test_label_normalises_and_checks_its_vocabulary` | `label`: NFKC, case, trailing sentence punctuation and separators; a value outside `values` is invalid; `c#` is not `c` |
| `test_label_aliases` | `P1` is `urgent`, `SEV-2` is `high`; an alias outside the vocabulary is refused |
| `test_bool_reads_words_and_json` | `yes`/`no`/`y`/`n`/`1`/`0` and JSON booleans; `maybe` and `2` invalid |
| `test_number_reads_money_separators_and_tolerances` | `1,234.50` == 1234.5 with `abs_tol` 0.01; `1.234,50 €` with `decimal=","`; `(5)` == -5; relative tolerance; percent as number or fraction |
| `test_parse_number_cases` (20 cases) | Thousands groupings (also Indian, apostrophe, space), `US$`, ISO codes, exponents, signs; ambiguous `1,5`, lower-case codes, units, NaN, infinities, `1_000`, double signs refused |
| `test_number_refuses_non_finite_and_non_numbers` | Strings, booleans, lists, `1e999`, NaN and an integer too large for a float are invalid |
| `test_ordinal_tolerance` | `low` and `medium` agree with tolerance 1, `low` and `high` do not; an unknown label is invalid; `order` is required |
| `test_text_modes` | `loose` with suffixes (`Acme Corp.` == `ACME corp` == `Acme, Inc.` == `Acme,Inc.`, a two-token suffix), punctuation as a word break (`hello-world`), apostrophes and initials joined (`O'Brien`, `U.S.A.`), a suffix that is the whole name kept, `exact`, `token_set` |
| `test_date_formats` | Six written forms == `2026-10-03`; month first by default, `day_first`; the local day of a datetime; UTC datetimes; impossible dates invalid |
| `test_language_codes_and_names` | `English` == `en` == `eng` == `en-US`, `Deutsch`, `日本語`, locale tags with scripts, regions and variants; unknown languages invalid; `values` restricts; prose that starts with a code (`It is English`, `no idea`, `in progress`) and tags led by a 639-2 or retired code are invalid, so `It is English.` and `It is French.` do not agree |
| `test_set_order_case_and_jaccard` | Order, case and duplicates ignored; Jaccard 0.8 boundary; `empty_values`; `split` with number elements; a string without `split` is invalid |
| `test_records_unordered_keyed_and_ordered` | Unkeyed and keyed pairing across order, `ordered=True`, a changed field or length, duplicate keys and a missing sub-field invalid |
| `test_records_without_a_key_pair_in_any_order` | Records with a tolerance pair by a maximum matching: the listed order never decides (`[0, 1]` and `[1, 0]` both agree with `[0.5, 2]`), a 50-record chain with one pairing, labels and nested records grouping first |
| `test_ranking_top_k` | Top-3 in order and as a set; an id ranked twice is invalid |
| `test_root_label_text_format` | A text task's `$` label: `Positive.` == `positive`, reasoning removed; other paths refused |
| `test_top_level_array_with_dollar_path` | A `$` set over a top-level array (fenced, after a sentence, after reasoning), a `$` number, dot paths into nested objects |
| `test_empty_json_is_invalid_when_fields_required` | `{}` is invalid for `ticket-triage` (score 0), but still valid for a `compare_keys` task, as in v0.4 |
| `test_out_of_vocab_label_invalid` | INV-16: `check` reasons (`invalid field category`, `invalid field priority`, `invalid answer`, `empty answer`); every answer the mock world writes is valid |
| `test_nullable_field_accepts_null` | Required, nullable and optional fields and how null and missing agree |
| `test_bad_specs_fail_when_defined` (28 cases) | Reserved and unknown types, bad paths, options on the wrong type (`values`, `tolerance`, `abs_tol`, `mode`, `k`, `min_jaccard`, `day_first`), duplicate labels, an alias that is a label of the vocabulary or names two labels, tolerances on set elements, record keys, the value, field and format limits, `compare_keys` with `.` or `$`, policy keys and ranges, `tools` |
| `test_records_nest_at_most_four_deep_and_hold_at_most_200` | Depth 4 allowed and 5 refused; 200 records valid and 201 invalid; the 200 count across nesting |
| `test_spec_hash_covers_the_rules_not_the_name` | Same rules, same hash, whatever the name or source; a vocabulary or `pinned` changes it; `dataclasses.replace` recomputes it, also on a `compare_keys` task; `policy` is read-only |
| `test_legacy_compare_keys_canonical_byte_identical` | FR-2.6: a `compare_keys` task's `canonical` and `agree` equal v0.4's on 25 answers with fences, `<think>`, nulls, `1` vs `1.0`, nested values, NaN and deep nesting |
| `test_pairwise_score_equals_counter_score_for_exact_tasks` | FR-2.2: on 600 seeded random sample sets (invalid and cut-off samples, ties, missing logprobs), the pairwise score equals v0.4's `Counter` score: the same answer object, confidence, agreement and token probability; `majority`'s choices agree |
| `test_builtin_task_agreement_decisions_unchanged` | On mock answers for the three built-in tasks, the typed specs decide agreement, confidence and the best sample exactly as v0.4's exact keys |
| `test_tolerance_non_transitive` | 0, 0.6, 1.2 with `abs_tol` 1: the middle sample, agreement 1.0, even at a lower token probability; `majority`; two groups tie and the first wins; with four samples the served sample's agreement is its own |
| `test_score_never_raises_when_a_key_does_not_match_itself` | INV-7: a comparator that is not reflexive gives confidence 0 instead of raising on the request path |
| `test_default_compares_json_canonically` | Key order, spacing (inside strings too, as in v0.4), case, NFKC and `1` vs `1.0` agree when both are JSON; list order and `true` vs `1` do not; the text rule otherwise; deep nesting compared as text; a `json` task without fields |
| `test_comparators_are_linear_on_adversarial_answers` | Long labels, suffix runs, token sets, numbers, dates, languages and sets normalise at once; 200 records pair reversed, shifted within a tolerance, and with ten tolerant fields in the worst order, all under 2 s |
| `test_comparators_never_raise_on_junk` | INV-7: 3,000 seeded junk values (odd strings, NaN, huge integers, nested lists and objects) never make a comparator raise, and every valid value matches itself |
| `test_invalid_teacher_answer_served_but_not_stored` | FR-2.13, INV-16: an out-of-vocabulary or incomplete teacher answer is served, not stored, `skip_example` names the field; an invalid student answer is never served; `store=False` stores nothing without a skip |
| `test_invalid_audit_not_counted` | INV-16: an audit whose teacher answer the spec refuses is `audited=0`, not stored |
| `test_cascade_eval_skips_invalid_golden_refs` | INV-6: rows with invalid references are not sampled and are counted in `golden_invalid_refs`; the halves alternate over the scored rows |
| `test_dataset_rows_skip_invalid` | INV-2, INV-16: train rows stored under a looser spec are left out and counted (`skipped_invalid`, also in `manifest.json` and in the night's `trained` or `skip` event); golden rows stay |

### `tests/test_registry.py`

The task registry (FR-2.15, [ADR 0022](adr/0022-task-registry.md)): specs from `RATCHET_TASKS_FILE` and the admin API, the admin key, operator-only pinning, the unknown-task policy, client allow-lists, pinned rows left out of Night School, and spec changes. Gateway tests use `gw_env` (mock) or a live `Runtime` on a file database with the teacher behind `FakeOpenAIServer`; unit tests use `taskspec`, `tasks` and a small ledger. The registry is module state, so `tests/conftest.py` resets it after every test.

| Test | Covers |
|---|---|
| `test_tasks_file_loads_and_registers` | FR-1.3, FR-2.9: a file's tasks (one extending and adjusting the built-in `default`, one pinned) listed by `GET /ratchet/tasks` with their source and spec; a request distilled under the file task, the pinned one answered by the teacher and not stored |
| `test_tasks_file_in_yaml` | The same in YAML (skipped without PyYAML); `clients` checked against the API key names; a syntax error with its line; bare `yes`/`no` read as booleans refused |
| `test_tasks_file_unknown_key_fails_startup_with_path` | A bad file stops `Runtime` and `python -m ratchet.live` with every error and its path (`tasks[0].feilds`); a missing file, broken JSON and NaN refused |
| `test_yaml_without_pyyaml_message` | A `.yaml` file without PyYAML is an error that says so; JSON needs nothing extra |
| `test_spec_errors_list_every_problem_with_its_path` | One document with 35 mistakes (unknown keys and options, options for another type, bad paths, ranges, reserved types and formats, policy, match, routing, clients, names, duplicates, `extends` cycles and missing bases, records nested too deep) gives exactly 35 errors, in file order |
| `test_spec_parser_never_raises_anything_but_spec_error` | 3,000 seeded junk specs (wrong types, unhashable values, unknown types, nesting) in `parse_spec` and `parse_document` give `SpecError` or a task, never another exception |
| `test_specs_round_trip_and_every_option_is_checked` | Every `FieldSpec` option has a check; a spec with every type, and every built-in task, survives `document` and `parse_spec` with the same hash; `description`, `clients` and `match` leave the hash alone, field order does not; the ledger keeps the order |
| `test_extends_copies_and_merges` | `extends` within a file (forward too), from a built-in task, a file adjusting a built-in task of the same name, a copy of that, through the API on the task's own name; `fields`, `policy` and `match` merge, null removes, `name` and `description` are not inherited |
| `test_registry_precedence_and_sources` | A file task replaces a built-in one; the API never replaces a built-in or file task; an auto task never shadows a registered one; adhoc and built-in tasks cannot be registered; removal and restore; `names_for` and allow-lists; an adhoc task is never pinned |
| `test_registry_reads_never_see_a_half_made_registry` | Lock-free reads under 4 writer threads registering, removing and resetting |
| `test_registry_cap` | `MAX_TASKS` refuses a new name but not a replacement; a file with too many tasks; 409 for the mock cap and the registry cap |
| `test_put_task_requires_admin_key` | INV-13, FR-1.7: 401 without or with a wrong admin key, 403 with an API key, for PUT, DELETE and validate; reads stay open; only the admin's name reaches the ledger and the events (the database dump holds no key) |
| `test_admin_keys_are_their_own` | INV-13: a key in both `RATCHET_ADMIN_KEYS` and `RATCHET_API_KEYS` stops startup; errors name the right variable |
| `test_admin_disabled_in_live_without_keys` | Live mode without admin keys: 403 `admin API disabled` for every write; mock mode: open, as `mock-admin` |
| `test_put_builtin_task_forbidden` | INV-13: 403 for a built-in or file task (PUT and DELETE), 404 for an unknown one; `validate` says the same |
| `test_client_cannot_pin_via_header_metadata_model_or_schema` | INV-10, FR-2.9: pin-like headers, metadata, `model` and a `response_format` with `x-ratchet` never pin; the tasks are distilled; nor can a header unpin `research-write`; `Task` refuses a pinned adhoc or auto task |
| `test_task_lifecycle_through_the_admin_api` | Create, an identical PUT (`changed: false`, no event), traffic, a changed spec (`task_changed` with both hashes), a metadata-only change, `recalibrating`, every 422 (bad JSON, NaN, an unpaired surrogate, another name, not an object, a bad option, 65 fields, a bad URL name), delete (examples kept), the name then following the unknown-task policy and listed as unregistered |
| `test_api_task_persists_across_runtime_restart` | A live `Runtime` on a file database: an API task registered again after a restart with the same hash and `updated_by`; a change logs both hashes; a deleted task stays gone, its examples kept |
| `test_stored_task_that_no_longer_parses_is_skipped` | A stored task that no longer parses, one whose name the file now has, and a garbled row are skipped with `task_skipped` events; the gateway starts |
| `test_validate_endpoint_has_no_side_effects` | One spec and a document, valid and not; a document may adjust a built-in task, a single spec may not; the registry, the events and the tasks table are unchanged |
| `test_concurrent_admin_writes_keep_the_registry_and_the_table_in_step` | 8 threads creating, changing and deleting tasks: the registry, the table and the event counts agree |
| `test_unknown_tasks_teacher_policy_stores_nothing` | FR-1.3, INV-10: `teacher` answers an unregistered task with the teacher only (route `unknown_task`, no student call even with a live and a shadow version, nothing stored); registered tasks still distil; a typo stops startup; the same through the gateway |
| `test_unknown_tasks_reject_policy_400` | FR-1.3: `reject` is a 400 `unknown_task` listing the registered tasks (`param` for metadata), logs nothing and uses no quota; direct callers get the same; at most 20 names listed |
| `test_clients_allow_list_403` | INV-13: a `clients` name that is not an API key is a 422; another key gets 403 `task_not_allowed` before the quota; the named key is served; rejection messages list only tasks the key may use |
| `test_pinning_task_excludes_existing_examples_from_dataset_and_eval` | INV-10, FR-2.9, INV-2: pinning a task with data leaves its rows out of `dataset.rows`, the manifest (`excluded_pinned`), `cascade_eval` and `run_night`'s trainer and evaluator; the eval records `spec_hashes`; unpinning brings them back |
| `test_spec_change_forces_teacher_until_recalibrated` | FR-2.3, INV-4: a changed spec makes the threshold 1.01 for that task only; the next night (even without enough data to train) re-scores the live version and records the new hash (`rescored`), and the student serves again; nothing to re-score the night after; a failed re-score keeps the task on the teacher (`rescore_failed`) |
| `test_fixture_paths_resolve_inside_the_fixtures_dir` | `resolve_fixture` refuses `..`, absolute paths, empty and NUL paths and a symlink out of `RATCHET_FIXTURES_DIR` |

### `tests/test_nightschool.py`

The invariant tests for Night School (INV-1 to INV-6). Each runs `run_night` or `cascade_eval` on a small ledger with a `RecordingTrainer` and a `RecordingEvaluator`, so it can assert on exactly which rows reached the trainer and which golden rows were scored.

| Test | Covers |
|---|---|
| `test_training_targets_are_teacher_answers_only` | INV-2: the teacher answers JSON that is valid for both tasks used (INV-16 would skip anything else); a student that answers `STUDENT-ONLY` with high confidence serves 200 requests with `audit_rate=1` and a shadow present, then 200 more without audits where a disagreeing shadow (`SHADOW-ONLY`) makes the teacher arbitrate and unparseable answers escalate. All 400 stored examples and dataset rows hold exactly the teacher's answer to their own request, and neither marker appears anywhere |
| `test_add_example_called_only_from_allowed_callers` | INV-2, INV-10: an AST scan of `ratchet/` finds every use of `add_example` (a call, a bound method passed on, or `getattr(..., "add_example")`); each must sit in a function in `ALLOWED_ADD_EXAMPLE_CALLERS` (today only `Ratchet._store_example`), and every listed caller must still use it (so moving a call site replaces its entry rather than adding one beside it) |
| `test_add_example_guard_sees_indirect_uses` | The scanner itself (no requirement marker: it tests the tool, not INV-2), on a throwaway package: a direct call, `getattr` inside a nested function and a bound method in a module-level lambda are found and named; a docstring that mentions `add_example` is not |
| `test_golden_rows_never_reach_the_trainer_except_frequent` | INV-3: the trainer gets the train split plus the golden requests seen at least `FREQUENT` (3) times; the evaluator gets the other golden rows; the two never overlap; a golden request seen twice stays held out; the stored split never changes |
| `test_every_variant_of_a_prompt_shares_its_split_and_count` | INV-3, INV-6, FR-4.3, FR-1.13: 200 prompts, each stored bare and under three sets of conditions: every prompt's four examples share one split (both splits occur), and the bare example's split is the one it gets alone (v0.4's); a request seen once bare and twice in JSON mode is frequent, so both variants move to training together |
| `test_examples_cut_at_a_stop_are_held_out_of_training` | INV-3, FR-4.1, FR-5.1: `train.py` ignores stored conditions, so a row the client's stop cut is left out of the train rows (bare and JSON-mode rows stay); as a golden row it is kept, with its stop |
| `test_closed_teacher_rows_are_dropped_at_dataset_build` | INV-1: rows from `openai/gpt-4o` and an unregistered teacher in the ledger never reach `dataset.rows`, `train.jsonl`, `golden.jsonl`, the trainer, the evaluator or the adapter's BOM |
| `test_night_skips_with_reason` (5 cases) | FR-4.4, INV-5: 49 new examples, 40 new since the last version, no golden set, a shadow in flight and a canary in flight each skip with their reason and a `skip` event; the trainer and evaluator are never called (not even to re-score the champion) and no version changes |
| `test_retrain_from_base_on_cumulative_data` | FR-4.2: three nights (the first with exactly 50 new examples). Each candidate passes and is released to live through the gate, so nights 2 and 3 train while the previous night's adapter is the champion; every night still trains from the base model (each `TrainCall.base_model` and `Version.base_model`), never the live adapter. Each night's rows contain every earlier night's rows, and their counts (50, 100, 175) equal each version's `train_examples` |
| `test_cascade_eval_measures_on_held_out_half` | INV-6: with right answers on even golden rows and wrong ones on odd rows at the same confidence, precision is 0.0 (graded on the odd half only), `test_n` is half of `golden_n` and all report keys are present; the mirror image calibrates on wrong answers and serves nothing (threshold 1.01) |
| `test_cascade_eval_passes_row_params_to_sample` | FR-5.1, FR-1.13, INV-6: the sampler gets each golden row's stored conditions, `None` for a row without them |
| `test_cascade_eval_scores_a_refused_row_as_not_served` | FR-5.1, INV-7, INV-6: three of six golden rows the sampler refuses with a 400 score as confidence 0 and wrong (`refused_n` 3, agreement 0.5, coverage 2/3 of the odd rows); a 401, 404, 429 or 503 still raises |
| `test_golden_eval_runs_under_production_conditions` | FR-5.1, FR-1.13, FR-4.1: an example stored with a stop and JSON mode comes back from `dataset.rows` with them; the router (with `RATCHET_STUDENT_MAX_TOKENS=256`), `LiveEvaluator` and `MockFleet` send the student exactly the same arguments for those rows |
| `test_champion_rescored_on_same_golden_set` | INV-4: the live version is evaluated first, on the same golden list as the candidate; its stored eval now holds the re-checked precision and coverage, with `rechecked_day` = the ledger's day; the candidate, which would regress against the stale eval, is admitted to shadow against the fresh one; the candidate is trained from the base model, not from the champion (FR-4.2) |

### `tests/test_release.py`

| Test | Covers |
|---|---|
| `test_gate_rejects_low_coverage` | Golden coverage one point under `min_coverage` is rejected; an eval without coverage is rejected (fails closed); exactly `min_coverage` passes |
| `test_gate_tolerance_boundary` (2 cases) | For precision and for coverage: a candidate exactly `tolerance` worse than live is admitted to shadow; a hair more is rejected as a regression |
| `test_admit_retires_older_inflight` | A rejected candidate leaves the shadow and canary in flight alone; an admitted one retires both (traffic 0, one `retired` event each) and is the only version in flight |
| `test_shadow_promotes_and_both_rejections` | 99 would-serve answers hold; at 100, precision exactly 95% and live's coverage it moves to canary 10%. The count is would-serve answers, not shadowed requests: 150 requests with 75 would-serve answers hold, 200 with 100 promote. 90% precision is rejected; 50% coverage against a live student at 100% is rejected, but passes against a live student that also serves 50% |
| `test_shadow_coverage_is_not_compared_with_requests_the_live_student_never_answered` | FR-6.4, FR-2.11 (regression): rows where the live student never answered (route `student_breaker` or `student_error`) stay in the shadow's `n` and coverage but not in `live_coverage`, so a shadow serving 50% is rejected against a live student that served all it answered; the same pattern with the live student unsure (route `confidence`) is live coverage of 50% and promotes |
| `test_shadow_coverage_counts_an_n_choices_escalation_as_live_served` | FR-6.4, FR-1.14: a request the live student was confident about but escalated because fewer than n samples agreed (route `n_choices`) counts as served in `live_coverage`, so a shadow serving 50% is rejected against a live student that would have served them all |
| `test_shadow_gate_counts_unarbitrated_disagreements_against_the_candidate` | FR-6.4, FR-2.10: 100 would-serve answers whose arbitrated ones all agree, but 10 of which the teacher could not arbitrate (`shadow_agree` NULL), give precision 90%, not 100%, and are rejected with "(10 unarbitrated counted as disagreements)"; 2 of 100 give 98% and promote, and the event says so; 50 rows without a shadow verdict (`shadow_serve` NULL) change neither `n`, coverage, the evidence count nor precision |
| `test_canary_holds_without_strong_evidence` | 18 of 20 audits (90%, under the 92% floor, Wilson upper bound above it) neither roll back nor advance, over several ticks; 20 more agreeing audits move it to 50%. The rollback boundary (FR-6.6): 16 of 20 (Wilson upper bound 91.9%, just under the floor) rolls back to live, 17 of 20 (94.8%) only holds |
| `test_rollback_without_live_returns_traffic_to_teacher` | A cold-start canary at 50% audited precision is rolled back with "traffic back to the teacher"; afterwards 50 requests are all answered by the teacher and the student is never called |
| `test_shadow_arbitration_calls_teacher_once_and_stores_example` | A shadow that would serve a different answer than the live student triggers exactly one teacher call, whose answer is stored as the example, while the user keeps the live answer; agreeing or unsure shadows trigger none; with an audit, the audit is the reference and no second call is made; a shadow whose endpoint is down (`raise_on`) leaves the request untouched, with no shadow record and no extra cost (INV-7). Reported cost includes the shadow's samples, the arbitration and the audit (NFR-3) |
| `test_canary_traffic_split` | `Ratchet._pick_version` with a seeded RNG: over 4,000 picks at 10% and at 50%, the canary's share is inside the 99% Wilson interval; 0% never picks it, 100% always does, and at cold start the rest of the traffic gets `None` (the teacher) |
| `test_tick_runs_each_step_once_in_order_with_the_live_version_it_started_with` | ADR 0024: `tick` calls `_tick_drift`, `_tick_live`, `_tick_shadow`, `_tick_canary` and `_tick_watchers` once each, in that order, the middle three with the live version read before any step (a canary promoted in the tick retires that one); the drift and watcher steps write nothing yet |
| `test_policy_refuses_values_out_of_range` (8 cases) | ADR 0024: `ReleasePolicy` refuses a `live_floor` of 1 or 0, a precision above 1, a negative coverage or audit count, a fractional or boolean count and an infinite tolerance, naming the variable |

### `tests/test_system.py`

| Test | Covers |
|---|---|
| `test_loop_learns_and_saves_money` | 6 days × 1,500 requests with no drift: day 1 is 0% student; final day is above 80% student and above 60% savings; a version reaches `live`; daily audited agreement never falls below 85% |
| `test_bad_deploy_is_rolled_back_and_users_return_to_live` | Fault-injects `student-v3`; v3 ends `rolled_back` and v1 stays `live` (skips if v3 was rejected before deploy for that seed) |
| `test_drift_raises_escalations_then_recovers` | 9 days with a product launch on day 5: escalations rise on day 5 and are lower by day 9 |
| `test_reference_simulation_unchanged` | The pinned reference run equals `tests/fixtures/sim_reference_seed7.json` exactly: the pinned inputs (a flag added later is not compared), daily numbers, release events and final versions ([below](#the-pinned-reference-simulation)) |
| `test_reference_workloads_reproduce_v040` | ADR 0024: the tickets workload of `tests/fixtures/reference.json` (captured from v0.4.0) matches the session's reference run under `scripts/reference_check.py`'s rules, and every input the fixture pins for all three workloads is still the simulator's default |
| `test_reference_check_rules` | ADR 0024: `reference_check.compare` ignores float noise below a relative 1e-9, new day and totals keys, advisory event kinds and text appended to an event, and reports one line for a moved number, a removed key or day, reworded event text, an extra event, a changed version or a changed input default |
| `test_provenance_only_lists_distillable_teachers` | Every BOM source is Ultra with OpenMDW-1.1 |
| `test_gateway_openai_shape_and_demo_flow` | Mock gateway: `/` (landing page) returns 200; `/ratchet/demo/day` works; `/v1/chat/completions` returns a valid `chat.completion` with the right category; stats count 401 requests; break-next and reset work |
| `test_gateway_rejects_bad_requests` | Empty `messages` returns 400; a `stream: true` request that fails validation gets a normal HTTP 400 before any stream starts |
| `test_openai_compat_provider_parses_logprobs_and_usage` | `OpenAICompatProvider` against an `httpx.MockTransport`: sends `logprobs` and `n`, sends the bearer header, parses logprobs per choice, splits completion tokens across choices (evenly here, because one choice has no logprobs), computes `mean_prob` |

### `tests/test_hardening.py`

Regression tests for the issues in [Issues found while documenting](#issues-found-while-documenting-and-their-fixes): `test_old_database_file_is_migrated`, `test_auto_day_counts_days_from_first_request`, `test_student_failure_falls_back_to_teacher`, `test_fallback_model_uses_default_threshold`, `test_teacher_failure_returns_502`, `test_unknown_tasks_do_not_grow_registry`, `test_live_alert_fires_once_per_version`, `test_policy_from_env`. `test_task_names_match_in_full` covers a bug found while building request validation: a task name with a trailing newline passed the `^...$` check. `test_live_night_checks_the_student_endpoint_before_paid_work` covers the issue found while testing the live entry points ([Live-path tests](#live-path-tests)). The answer-hygiene tests (v0.5, FR-2.7, FR-2.13, INV-16, [ADR 0016](adr/0016-store-only-complete-valid-teacher-answers.md)) cover what a code reading found before any live run: reasoning before a lone `</think>` was served and stored, an unterminated `<think>` let the reasoning's JSON count as the answer, structured answers were stored inside code fences, a teacher answer cut off by the token limit (or empty, or not JSON for a structured task) became a training target and an audit, JSON nested too deeply crashed the request instead of escalating, and some long answers took seconds to clean.

The v0.4 hardening tests (privacy, API keys, streaming, signed BOMs) are in the same file. The `gw_env` fixture (in `tests/conftest.py`) reloads the gateway in mock mode with extra environment variables and reloads it again with the defaults afterwards, so settings such as API keys never leak into other tests.

| Test | Covers |
|---|---|
| `test_redact_finds_each_kind_and_checks_checksums` | Every label is found in one text (two phones, ten placeholders); a card number that fails Luhn is left alone |
| `test_redact_leaves_ordinary_text_alone` | Order numbers, dates, times, prices, version strings, page counts, URLs and an ISBN are not touched |
| `test_redact_messages_handles_content_parts` | OpenAI content-part lists are redacted part by part; non-text parts and the caller's copy are untouched |
| `test_redact_messages_covers_names_refusals_and_call_arguments` | `name`, an assistant's `refusal`, tool-call and legacy function-call arguments are redacted and counted; a malformed tool call and the caller's copy are untouched |
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
| `test_v04_database_gets_the_route_column` | A v0.4 database file with requests in it gets `requests.route` on open; `stats_by_task` reports the old rows as `unrecorded` next to a new `cold_start` row |
| `test_v04_database_gets_the_examples_params_column` | FR-1.13, FR-4.1: a v0.4 examples table gains `params`; its old row keeps its v0.4 hash and no conditions, a repeat bumps it, the same messages under JSON mode are a new example, and `dataset.rows` returns both |
| `test_healthz_stays_public_when_keys_are_on` | `/healthz` answers 200 while `/v1/models` needs a key |
| `test_redact_preview_endpoint` | `POST /ratchet/redact` returns the redacted text and counts; over-long or missing text gets 400 |
| `test_live_night_checks_the_student_endpoint_before_paid_work` | Without `RATCHET_STUDENT_BASE`, `NebiusTrainer.train` raises before the sync, the training job or any file write; `reload` with adapters to load raises the same clear error (and with none, returns an empty list) |
| `test_task_names_match_in_full` | `tasks.valid_name` matches the whole name: a trailing newline, 65 characters, an empty string, a space or a tab is not a name, and `tasks.get` falls back to `default` for each |
| `test_teacher_reasoning_and_fences_are_not_stored_or_served` (2 cases) | FR-2.7, FR-2.13, INV-2, INV-16: a teacher answer with a `<think>` block, or with only a closing `</think>`, and a fenced JSON answer is served and stored as the bare JSON, with `stored = 1` and no skip |
| `test_served_text_is_clean_on_the_student_and_pinned_paths` | FR-2.7, INV-10: a confident student's fenced answer with reasoning is served as bare JSON; a pinned (`research-plan`) and a teacher-only answer lose the reasoning but keep their fence (free text), store nothing and are no skip |
| `test_truncated_teacher_answer_is_not_stored` | FR-2.13, INV-16: through the real `OpenAICompatProvider` and a `FakeOpenAIServer` that reports `finish_reason: "length"`, a teacher answer whose visible part parses is served on a structured and a free-text task, but nothing is stored, both rows have `stored = 0`, and one `skip_example` event names the task and `finish_reason length` |
| `test_empty_teacher_answer_not_stored` (4 cases) | FR-2.13, INV-16: an empty, whitespace, unterminated-reasoning or reasoning-only answer is served as `''` on both kinds of task, not stored, `stored = 0`, `empty answer` |
| `test_invalid_json_teacher_answer_for_ticket_triage_not_stored` | FR-2.13, INV-16: prose, cut JSON and a JSON list are served but not stored for `ticket-triage` (`invalid answer`), while the same text is stored for the free-text `default` task |
| `test_redaction_that_breaks_a_structured_answer_is_not_stored` | FR-2.13, INV-16, INV-12: a card-like number outside quotes becomes `[CARD]` and breaks the JSON, so the answer is served but not stored (`invalid after redaction`); with redaction off it is stored as is |
| `test_unusable_teacher_answer_is_not_an_audit` | FR-2.13, INV-16: an audit whose teacher answer was cut off gives `audited = 0`, `audit_agree` NULL, `stored = 0`, no audit for the version, and a cost that still includes the teacher's call; a complete answer is an audit and an example again |
| `test_invalid_shadow_reference_leaves_shadow_agree_null` | FR-2.13, INV-16, FR-6.3: a shadow disagreeing with the live student, arbitrated with prose, gets `shadow_agree` NULL and one unarbitrated row, nothing stored; a valid arbitration settles the next one and is stored; an escalation whose teacher answer was cut off leaves `shadow_agree` NULL too |
| `test_unusable_audit_answer_is_no_shadow_reference` | FR-2.13, INV-16, FR-6.3: with a cut-off audit answer, a shadow that agrees with the served answer gets `shadow_agree = 1` (no arbitration, precision 1.0), as without the audit; a disagreeing shadow is still arbitrated, and the cut-off ruling leaves NULL (review finding) |
| `test_answers_that_quote_think_tags_are_served_and_stored_whole` | FR-2.7, FR-2.13, INV-16: a triage reply that mentions a stray `<think>` tag is served and stored whole with no skip; a confident student quoting `</think>` in its reply is served; a free-text answer that mentions `<think>` is served and stored whole (review finding) |
| `test_skip_example_events_are_throttled_per_reason` | FR-2.13, INV-16, INV-12: 200 cut-off answers from 8 threads write exactly 3 events (1st, 10th, 100th); `empty answer` and an unnamed finish reason (`other`) are counted apart; no event quotes the answer; every row has `stored = 0` |
| `test_stored_column_marks_requests_that_stored_an_example` | FR-2.13, FR-3.4: an escalation and its duplicate (`seen` 2) both have `stored = 1`; pinned and an unaudited student answer have 0; an audited one has 1 |
| `test_v05_database_gets_the_stored_column` | A ledger from before answer hygiene gains `requests.stored`; its old row keeps NULL and a new escalation records 1 |
| `test_answers_nested_too_deeply_are_invalid_not_a_crash` | FR-2.2, FR-2.7: `parse_json` returns None instead of raising `RecursionError` on deeply nested JSON, so such a student answer is invalid and the request escalates (route `confidence`) instead of failing with a 500 |
| `test_answer_cleaning_is_linear_on_adversarial_answers` | FR-2.7, NFR-1: 20,000 unclosed `<think>` tags, 100,000 spaces, 100,000 `{`, a long space run before a fence, and stray closing tags followed by unclosed ones are each cleaned and judged invalid in well under 2 s (seconds each before, from regexes that rescanned every position); a fenced object after a sentence still parses |
| `test_provider_parses_finish_reason_reasoning_tokens_and_per_choice_tokens` | FR-2.13, FR-1.11, NFR-7: per choice, the text is `content` only (never `reasoning_content` or `reasoning`), `finish_reason` is read, output tokens follow the logprob counts and add up to `usage.completion_tokens` (in proportion when the counts do not, evenly with the remainder first when a choice has no logprobs, the counts as they are without usage), and reasoning tokens are split evenly; a single teacher choice gets the whole bill |
| `test_finish_reason_values_are_read_safely` (11 cases) | FR-2.13, FR-1.11: a missing, null, empty or blank `finish_reason` is `stop`; a word is lower-cased and trimmed; a number, a phrase or more than 40 characters is `unknown` |
| `test_logprobs_too_large_for_a_float_are_ignored_not_a_crash` | FR-1.14, FR-1.11 (regression): a token logprob of 10^400, which `json.loads` accepts as an integer, made `math.isfinite` raise OverflowError while parsing (a 500 once the teacher returns logprobs); now it is left out of the confidence's logprobs and the served logprobs are None, and a normal value is served |
| `test_provider_sends_params_and_its_explicit_arguments_win` | FR-1.13, FR-1.11: `OpenAICompatProvider.complete(params=...)` adds the fields to the body; the explicit temperature, `n`, logprobs (off also drops `top_logprobs`) and `response_format` win; without params the body is v0.4's, and an unknown keyword argument is ignored; `allow` narrows what is sent |
| `test_answers_under_label_altering_sampling_are_served_not_stored` (6 cases) | FR-1.13, INV-2, FR-4.1, threat model T2: a client's `logit_bias` of +100, a presence penalty of 2, a negative frequency penalty, temperature 2, a vendor field (`guided_choice`) or all three at once reach the serving teacher and its answer is returned, but nothing is stored (`stored` 0, one `skip_example` event naming the reason); neutral sampling (temperature 1, `top_p`, a seed, zero penalties) stores; an audit, which drops these fields, stores its answer |
| `test_v04_file_gains_the_evidence_schema` | ADR 0024, INV-17: a ledger file with v0.4.0's schema and one row per table gains every new column (old rows: `contested` and `checked_epoch` 0, the rest NULL), the eight evidence tables, the append-only triggers and the new indexes, twice without change; its old data reads as before and new rows take the columns |
| `test_every_index_exists_on_a_new_ledger` | ADR 0024: every `INDEXES` entry exists on a fresh ledger (a misspelled column would make `_migrate` skip it everywhere), and the gate's audit count uses `requests_version_audited` |
| `test_committed_leaves_and_sealed_receipts_are_append_only` | INV-17: `version_leaves` and `receipts` accept new rows and refuse UPDATE, DELETE, `INSERT OR REPLACE`, `INSERT OR IGNORE` and a duplicate INSERT (also on a migrated v0.4 file), a NULL key column and a split other than train or golden, leaving the rows as they were |
| `test_reserved_settings_stop_startup_until_their_feature_lands` | ADR 0024: each of the 13 reserved settings set away from its default stops `Settings.check_reserved()`, `Ratchet` and `python -m ratchet.live` (before the ledger is opened), naming the variable and the feature; the defaults spelled another way pass; a non-number names its variable |
| `test_reserved_release_policy_fields_stop_startup` | ADR 0024: each of the 16 reserved `ReleasePolicy` fields set through its variable stops `from_env`; the defaults, also set explicitly, pass |
| `test_policy_from_env_names_a_bad_value` (8 cases) | ADR 0024: a non-number, a fractional or negative count, a `live_floor` of 1 or 0, NaN and a negative tolerance each stop `from_env` with the variable named |

### `tests/test_resilience.py`

Upstream failures: around a request the student already answered (FR-2.10, INV-15, [ADR 0013](adr/0013-optional-teacher-calls-fail-open.md)), the provider's retries and the client statuses they end in (FR-1.11, FR-1.8, INV-7, [ADR 0014](adr/0014-upstream-failure-semantics.md)), the student circuit breaker with automatic adapter reloads (FR-2.11, NFR-2, [ADR 0015](adr/0015-student-circuit-breaker-and-adapter-reload.md)), and the teacher's breaker, degraded mode and the fallback teacher (FR-2.12, [ADR 0019](adr/0019-teacher-breaker-degraded-mode-and-fallback-teacher.md)); the student falling back to the teacher is in `test_hardening.py`. Breaker tests run on a `FakeClock` (`Ratchet(clock=...)`, `Runtime(clock=...)`), so no test waits out a cooldown. The failing teacher puts its URL, with a query token, in each message, and every test checks the events, errors and responses never repeat it. Retry tests use the local `Upstream` fake (a scripted endpoint behind `httpx.MockTransport` that records each attempt's timeouts) and inject `sleep`, `clock` and `rng`, so nothing really waits.

| Test | Covers |
|---|---|
| `test_audit_failure_still_serves_the_student` (3 cases) | With `audit_rate=1`, a teacher that times out, answers 503 or returns an unreadable body: the confident student's answer is served, the row has `audited=0` and `audit_agree` NULL, nothing is billed for the failed call, the baseline is the no-audit estimate, nothing is stored, one `audit_error` event (`ticket-triage: ReadTimeout`, `HTTPStatusError 503`, `ValueError`; never the URL), and `health()` counts one audit error |
| `test_audit_errors_are_counted_and_throttled_and_escalation_still_fails` | With the teacher's breaker off (`teacher_breaker_failures=0`), 12 failed audits: 12 counted, events on the 1st and 10th only, no row counted as audited. The teacher comes back: audits and their training examples resume. An unsure student with the teacher down still raises (INV-7, an error status at the gateway) and is not counted as an optional failure |
| `test_failure_counters_are_exact_under_threads` | 8 threads, 200 failed audits (the teacher's breaker off): every request served by the student, exactly 200 counted, exactly three events (1st, 10th, 100th) |
| `test_shadow_arbitration_failure_is_silent` | The shadow disagrees with the live student and the teacher times out only on that request: the user gets the live answer, the row keeps `shadow_serve=1` with `shadow_agree` NULL, no teacher cost and no example, one `shadow_error` (`v2 arbitration: ReadTimeout`); `shadow_quality` counts the row in `n` and coverage and as a disagreement (precision 1.0 → 0.5, `unarbitrated` 1) |
| `test_shadow_sampling_error_counted` | With the breaker off (`student_breaker_failures=0`), a shadow endpoint that times out on 10 requests: all served by the live student at the samples' cost, no shadow record and no teacher call, events on the 1st and 10th, `shadow_errors` 10 |
| `test_optional_teacher_failures_through_the_gateway` | Live `Runtime` with the real HTTP provider, its retry policy and a Token Factory that times out: audited requests (plain and streamed) are 200 with the student's answer, and the shadow's disagreement is not sent to the teacher again after the audit failed (one teacher call, two HTTP attempts, per request); unaudited, the failed arbitration is still a 200; the events read `UpstreamError 504 upstream_timeout`; an escalation is a 504 `teacher_timeout` |
| `test_student_error_event_never_quotes_the_endpoint` | INV-12: live `Runtime` with a student endpoint whose 400 quotes the request (`Invalid content: 'My SSN is 123-45-6789, email jane@corp.com'`) and its URL, as vLLM's validation errors can: the teacher answers (route `student_error`), and the `student_error` event, in the ledger and in the public `/ratchet/stats`, reads `v1: UpstreamError 400`, with no request text, message or URL |
| `test_retry_backs_off_with_full_jitter_under_the_cap` | 503, 503, 200: three attempts, waits `uniform(0, min(cap, base·2^i))` that grow and stay under the cap; a long outage waits at the cap and returns the last `UpstreamError` (still an `httpx.HTTPError`); jitter's low end waits 0 |
| `test_retry_after_is_honoured` | A 429 with `Retry-After: 2` waits exactly 2 s; `retry-after-ms` wins over `Retry-After`; a wait over the 8 s cap is not waited for: one attempt, and the error carries the wait |
| `test_parse_retry_after_forms` | Seconds, fractions, milliseconds, an HTTP date, case-insensitive names, a bad ms value falling back; empty, zero, negative, infinite, NaN, past and malformed values are ignored |
| `test_client_errors_are_not_retried` (6 cases) | 400, 401, 403, 404, 413 and 422: one attempt, no wait |
| `test_deadline_stops_retries` | With a 5 s deadline and 1 s attempts, a wait that would end past it is not started (three attempts), each attempt's connect and read timeouts shrink to the time left (5, 3.5, 1.5 s), and a server wait counts too; with no policy, one attempt with the configured timeouts |
| `test_read_timeout_is_retried_exactly_once` | A read timeout twice gives up after two attempts with 504 `upstream_timeout` (the cause kept, no URL); a 503 first does not use up that retry; connection failures before the model saw the request are retried up to `max_attempts`; write timeouts and read or write errors are not retried |
| `test_bad_upstream_bodies_are_502_and_not_retried` (7 cases) | Empty, missing or null `choices`, a choice that is not an object, a body that is not an object or not JSON, JSON nested too deeply for the parser (a `RecursionError`): 502 `bad_upstream_response` after one attempt (before: an `IndexError`, so a 500) |
| `test_malformed_optional_fields_count_as_absent` | Text parts joined, junk logprobs (strings, booleans, NaN) skipped, bad usage counted as 0, a missing model replaced by the requested one |
| `test_upstream_error_bodies_are_parsed_without_the_url` (7 cases) | OpenAI's `{"error": {...}}`, vLLM's legacy body (integer `code` dropped), an error string, FastAPI's `detail` (string and validation list, with `param`), HTML (the reason phrase), and a long message with a control character and a lone surrogate (cleaned, clipped to 300); never the URL or the key; `raise_for_status()`'s error reads the same |
| `test_upstream_errors_map_to_client_statuses` (20 cases) | Live `Runtime` at cold start, the teacher behind `MockTransport`: upstream 400 `context_length_exceeded` → 400 with the teacher's message, param and code; 413 → 400 `request_too_large`; 429 → 429 with `Retry-After` 7 (after two 7 s waits), 1 when none was sent, 60 for a 600 s wait (not retried); 500 and a connection error → 502 `teacher_unavailable` after three attempts; a 503 with `Retry-After` 2 → 502 with `Retry-After` 2 after two 2 s waits, with 30 or 600 → 502 with `Retry-After` 30 or 60 after one attempt (a wait over the 8 s cap goes to the client), a 504 with `Retry-After` 20 → 504 with it; a read timeout → 504 `teacher_timeout` after two; 401 and 403 → 502 `upstream_auth_error`; 404 → 502 `upstream_not_found`; 402 (also with a `Retry-After`, which is then not passed on) and a 301 redirect → 502 with `X-Should-Retry: false`; empty `choices` and JSON nested too deeply → 502, not 500. `X-Should-Retry: false` exactly where a retry cannot help; nothing logged, no `gateway_error` |
| `test_live_runtime_passes_timeouts_and_retries` | The teacher gets its read timeout and retry policy (and the injected `sleep`), the student its own timeouts and no retries; the timeouts arrive on each request; the hosted fallback is a separate client with the student's settings; the variables reach `Settings`; zero, negative or NaN values stop live mode at startup |
| `test_quota_429_says_when_it_resets` | At 23:00 UTC: `X-RateLimit-Reset-Requests: 1h0m0s` on the 200 and the 429; the 429 is `insufficient_quota` / `daily_quota_exceeded` with `Retry-After: 3600` and `X-Should-Retry: false`; 90.5 s before midnight rounds up to 91 s and `1m31s`; refusals are not counted; `_duration` formats |
| `test_sdk_sends_an_exhausted_quota_once` | The real OpenAI SDK with its default two retries sends the quota-exhausted request once and raises `RateLimitError` with the type and code |
| `test_sdk_sends_an_upstream_client_error_once` | The SDK raises `BadRequestError` with the teacher's `context_length_exceeded` and `param` after one request, and `InternalServerError` 502 `upstream_auth_error` without retrying |
| `test_scout_error_event_names_the_upstream_status` | A failed Scout run says `a model or search call failed: upstream 503 service_unavailable` (or `upstream 502 upstream_unreachable`, `upstream 504 upstream_timeout`), never the URL |
| `test_breaker_opens_probes_backs_off_and_closes` | `Breaker` on a fake clock: 4 failures and a success stay closed, 5 in a row open it (`open_until` 30 s on); exactly one probe after the wait; failed probes wait 60, 120, 240, 300, 300 s (at 300 s only the 1st probe's transitions are reported); a successful probe closes it and resets the wait; a released probe and a lost one (no report within the wait) let the next caller probe; a late result from another thread in half_open, and results while open, change nothing; threshold 0 never opens; a failing listener is swallowed; bad arguments and bad `RATCHET_STUDENT_BREAKER_*` settings raise `ValueError` when the cascade is built |
| `test_half_open_lets_exactly_one_probe_through` | 25 rounds of 16 threads released by a barrier at the end of the cooldown: exactly one `allow()` is True each time |
| `test_a_probe_slower_than_the_cooldown_still_counts_and_backs_the_breaker_off` | Regression (review of FR-2.11): a probe held past the cooldown on a hung endpoint used to be replaced and its failure dropped, so the breaker stayed half-open for the whole outage. Now a lost probe's failure reopens the breaker and doubles the wait (and its success closes it), and with `probe_timeout_s` nobody else probes while the probe can still be running. A 900 s hung-endpoint run (a request a second, calls held 15, 17 or 60 s, cooldowns 5 and 30 s) backs off to 300 s every time, with one probe in flight at most when `probe_timeout_s` is set; a bad `probe_timeout_s` raises |
| `test_router_never_replaces_a_probe_that_can_still_be_running` | The router gives a breaker the student call's longest time (`OpenAICompatProvider.longest_call_s()`: pool, connect, write and read timeouts, 32 s by default, plus 5 s): with a 5 s cooldown, requests 5, 10 and 36.9 s into a probe that hangs on a `MockTransport` student skip the student (route `student_breaker`), and the probe's timeout reopens the breaker for 10 s |
| `test_circuit_events_at_the_longest_wait_are_throttled` | 120 failed probes at 300 s report only the 1st, 10th and 100th pair of `circuit` transitions (each says which), every probe still counts in `failures`, closing is always reported, and the count starts again after the breaker closes |
| `test_endpoint_failures_are_told_from_request_errors` | Timeouts, connection and protocol errors, 5xx, 408, 429 and an unreadable answer count as the endpoint down; 400, 401, 404 and `AdapterMissing` as the endpoint answering; a `KeyError` or `ValueError` as neither. `CircuitOpen` is a 503 `circuit_open` |
| `test_open_student_breaker_sends_requests_straight_to_the_teacher` | A student that times out: 5 `student_error`s and `student: closed->open after 5 failures (ReadTimeout)`; the 6th request never reaches the student (call counter), is served by the teacher with route `student_breaker`, `escalated=1`, `confidence` NULL and only the teacher's cost, and is not another student error; a failed probe after 30 s waits 60 s; once the student is back the probe serves and closes it; the five `circuit` events in order; the teacher's breaker stays closed |
| `test_shadow_breaker_is_its_own` | A shadow that times out opens the `shadow` breaker after 5 calls; the live student serves all 10 requests and its breaker stays closed; skipped shadow calls are not counted as errors and leave no shadow record |
| `test_breakers_are_per_endpoint_and_role` | Live and canary on one endpoint share the `student` breaker (five failures between them open it for both); breakers are keyed by `base_url` (two clients of one URL share one), by class without one (a new provider object per call keeps one breaker), and by role; names `student`, `student-2`, `student-3`, `shadow` (and the teacher's own `teacher`) |
| `test_lost_adapter_is_reported_and_does_not_open_the_breaker` | vLLM's 404 ``The model `student-v1` does not exist.``: 10 requests escalate, `on_adapter_missing` is called each time with the version, the breaker stays closed, the `student_error` detail says `AdapterMissing 404 adapter_missing`; a 404 that names no adapter or another one, and the hosted fallback model's 404, call nothing; a failing or missing callback never fails the request; a shadow's lost adapter is reported too and leaves the `shadow` breaker closed |
| `test_errors_without_an_http_status_say_nothing_to_the_breaker` | 10 `KeyError`s from the student leave the breaker closed with no failures; a half-open probe that ends in a `KeyError` is handed back and the next request probes (and closes it); a 200 with no choices is a 502 `bad_upstream_response` that counts, so five of them open the breaker |
| `test_adapter_reloader_rate_limits_and_reports` | `AdapterReloader` on a fake clock: one queued reload per adapter per 60 s, `adapter_reloaded` and `adapter_reload_failed` (`HTTPStatusError 503`, never the URL) events; the thread survives a failing ledger write; nothing is queued after `close()`; `adapter_reloader` refuses to build one without `RATCHET_STUDENT_BASE` |
| `test_live_gateway_reloads_a_lost_adapter_once_a_minute` | Live `Runtime` with `FakeOpenAIServer(loaded=set())` as a restarted vLLM: 10 requests in 50 s are 200s from the teacher, one `load_lora_adapter` call with the adapter's path and the endpoint token, the breaker closed, rows routed `student_error`, `adapter_reload_failed` while loading fails; a minute later the next 404 reloads it and the student serves again. Mock mode and the hosted fallback have no reloader |
| `test_demo_student_outage_opens_and_closes_the_breaker` | Mock gateway on a fake clock: `seconds` clamped to 5 and 300; a 60 s outage gives 5 `student_error`s, then `student_breaker`, a failed probe at 30 s, `student_breaker` again, and at 91 s a probe served by the student; the five `circuit` events and the `fault` event; `/ratchet/stats` routes add up to the task's requests |
| `test_every_request_row_has_a_route` | Cold start, a pinned task, a teacher-only request, a confident student, one that is confident but cannot fill 4 choices, an unsure student, the fallback teacher for a down teacher, a degraded answer with both teachers down, a failing student and an open breaker give `cold_start`, `pinned`, `pinned`, `student`, `n_choices`, `confidence`, `fallback_teacher`, `degraded`, `student_error`, `student_breaker`: every route in `router.ROUTES`, no NULL route, `stats_by_task` routes per task, most frequent first, and `stats_by_day`'s `degraded` and `fallback_teacher` counts |
| `test_open_teacher_breaker_answers_503_at_once_with_retry_after` | FR-2.12: live `Runtime` at cold start with a Token Factory that answers 503: five requests are 502 `teacher_unavailable` after three attempts each and open the teacher's breaker (`teacher: closed->open after 5 failures (UpstreamError 503)`); the 6th is a 503 `teacher_circuit_open` with `Retry-After: 30`, no `X-Should-Retry` and no teacher call; 12.5 s later the OpenAI SDK raises `InternalServerError` 503 with `Retry-After: 18`; nothing is logged; after the cooldown the probe finds the teacher back and closes the breaker. `_map_upstream` rounds the wait up and clamps it to 1 to 60 s; a student's `CircuitOpen`, or an upstream's own `teacher_circuit_open` code, stays a 502 |
| `test_open_teacher_breaker_skips_audits_and_arbitration_without_counting_them` | Five escalations that time out open the breaker; the 6th raises `CircuitOpen` (503, `retry_after` 30) with no call and no row; then a confident student is served with the audit and the shadow's arbitration skipped (no teacher call, rows `audited=0`, `shadow_agree` NULL), no `audit_error` or `shadow_error`, only the `circuit` event; after the cooldown the next audit is the probe, succeeds and closes the breaker |
| `test_teacher_breaker_counts_only_teacher_outages` | Seven 400 `context_length_exceeded` answers leave the breaker closed; four timeouts, then a tool-call answer (a success for the breaker) and a `ValueError` (no count), then four timeouts: still closed with 4 failures; `teacher_breaker_failures=0` never opens; bad `RATCHET_TEACHER_BREAKER_*` values stop startup; `Breaker.retry_in()` is None while closed or half-open, the time left while open, 0 once it is up |
| `test_serve_student_mode_answers_degraded_with_a_flag_and_a_header` | FR-2.12: live `Runtime` with `RATCHET_ON_TEACHER_FAILURE=serve_student`, a student below its 0.99 threshold and a Token Factory that answers 503: a 200 with the student's answer, `ratchet.degraded: "teacher_unavailable"`, `X-Ratchet-Degraded` and `X-Ratchet-Served-By: student`; the row is route `degraded`, `audited=0`, `escalated=1`, `stored=0`, no shadow record (the shadow student is not called), billed for the samples only; a `degraded` event (`default: UpstreamError 503`); streamed the same; after the breaker opens, degraded answers come with no teacher call; no audits for the live version, no shadow evidence; `/ratchet/stats` counts them per day and per route; a confident answer has `degraded: null` and no header |
| `test_error_mode_is_the_default_and_pinned_requests_never_degrade` | FR-2.12: by default an unsure student with the teacher down is still a 502 with no degraded header and no row; with `serve_student`, a pinned task (`research-plan`) is a 502 five times, then a 503 once the breaker is open, and the student is never called |
| `test_degraded_mode_never_hides_a_client_error_or_serves_an_invalid_answer` | With `serve_student`, an unavailable teacher degrades (also with a client `max_tokens`), but these still raise: a request with `tools`, the teacher-only path, a 400 `context_length_exceeded`, a non-HTTP error, a student whose samples are all invalid, a student that failed or whose breaker is open, a teacher tool-call answer and a cold start. `Serve_Student` with spaces is accepted, empty means `error`, other values stop startup |
| `test_degraded_answers_are_counted_exactly_under_threads` | 8 threads, 200 degraded answers: every row route `degraded` with `audited` and `stored` 0, `health()["degraded_answers"]` exactly 200, three `degraded` events (1st, 10th, 100th) |
| `test_n_choices_when_the_teacher_is_down` | FR-2.12, FR-1.14: a degraded answer for n = 2 serves two agreeing student samples (usage of both); with only 2 of 3 agreeing, n = 3 gets the teacher's error while n = 1 still degrades; the fallback teacher writes n choices at 0.7 and stores nothing by default |
| `test_fallback_teacher_answers_when_the_primary_is_unavailable` | FR-2.12: with the primary down, the fallback answers an escalation (route `fallback_teacher`, `served_by` teacher, the fallback's model id, its own price, the primary's price as the baseline, its own fingerprint), not stored, no shadow call, one `fallback_teacher` event; a 400 about the request is not sent to the fallback; five failures open the primary's breaker and the fallback then answers without a primary call; a pinned task gets the fallback, priced lower than its baseline; an audit is skipped, never sent to the fallback; with both down the primary's `CircuitOpen` is raised; no fallback-served row has a shadow record |
| `test_stored_fallback_answers_carry_the_fallback_license` | INV-1, INV-2, FR-3.3: with `store_fallback_teacher`, the fallback's answer is stored with its own model and license (NVIDIA Nemotron Open Model License) and the primary's with OpenMDW-1.1, and the BOM lists both; by default nothing is stored, and without its own provider the fallback model is asked on the teacher's client; `RequestMeta(storable=False)` stores nothing, with no `skip_example` event |
| `test_live_runtime_wires_the_fallback_teacher` | FR-2.12, INV-1: `RATCHET_TEACHER_FALLBACK_BASE` gives the fallback its own client (the teacher's key, retries and timeouts); at cold start with Token Factory's host down the response's `model` is the fallback's, after three primary attempts and one fallback call, both with the teacher's key; without it, the fallback model is asked on the teacher's client; breakers `teacher` and `fallback_teacher`; the six variables reach `Settings`; `RATCHET_TEACHER_FALLBACK_MODEL=gpt-4o` stops the live `Runtime` |
| `test_a_golden_row_the_student_refuses_never_stops_night_school` | FR-5.1, INV-7, FR-4.4: 60 examples, half stored with `tools`; a student behind `MockTransport` that answers 400 to `tools` as vLLM without a tool parser does; under an allowlist that forwards tools, `run_night` trains and reports `refused_n` equal to the golden rows with tools; the default allowlist sends no tools; the judge's refused candidate does not pass (pass rate 0.5); a 401 still stops the eval |

### `tests/test_scout.py`

The `gw` fixture (in `tests/conftest.py`) reloads the gateway in mock mode with `RATCHET_MOCK_WARMUP_DAYS=0`. The guard tests use `FakeClock`.

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

### `tests/test_contract.py`

The contract tests ([Contract tests](#contract-tests)). Gateway tests use `gw_env`; cascade tests build a small ledger with `_version` and script the models with `RecordingProvider`, with every setting they depend on passed explicitly.

| Test | Covers |
|---|---|
| `test_v1_models_lists_ratchet` | FR-1.2: `GET /v1/models` returns a `list` with a `ratchet` model; with API keys on it needs a key (401) and answers the same with one |
| `test_task_from_metadata_and_header_precedence` | FR-1.3: the ledger's `task` column is `default` with neither, the metadata task alone, the header alone, and the header when both are set (both orders); other metadata keys are ignored |
| `test_missing_or_malformed_messages` (9 cases) | FR-1.5: missing, `null`, empty, string, object and number `messages`, an unknown role, a number as content and `null` user content get 400 and log nothing (the last three were 500s until v0.5) |
| `test_messages_items_must_be_objects_400` | FR-1.5: `["hi"]`, `[null]`, `[1]` and a nested list as messages get 400 and log nothing |
| `test_bad_metadata_is_400` | FR-1.3: `metadata` as a string, a list, or with a non-string `ratchet_task` gets 400 and logs nothing |
| `test_text_content_parts_answered_in_mock` | FR-1.1: a demo ticket sent as OpenAI text content parts gets the same answer as the string form and the same stored example (`seen` 2) |
| `test_bom_endpoint_counts_by_split_and_fingerprint_changes` | FR-3.5, US-7: `/ratchet/bom` counts per split equal the ledger's; every source names the configured teacher and its license; `examples_sha256` is the documented SHA-256 over the sorted example hashes, stable between reads and equal to the stats block; a repeated request changes nothing, a new one changes the fingerprint and adds one |
| `test_stats_contract` | FR-7.1 to FR-7.4, FR-7.7, US-9: every key in `tests/fixtures/stats_keys.json`, with its JSON type, on a fresh gateway (lists empty) and after two simulated days and nights plus a keyed request (every list has items, each item checked); the fixture still holds the keys this item named; totals, days, tasks and clients add up to the same requests and dollars, and the savings are positive; each task's `routes` add up to its requests and use only known routes |
| `test_demo_endpoints_are_403_in_live_mode` | INV-8: in live mode `POST /ratchet/demo/day` (also with bad arguments), `break-next`, `student-outage` and `reset` get 403 and change nothing in the ledger |
| `test_student_sampled_once_with_production_params` | FR-2.1, NFR-1: one student call per request with `n = Settings.samples` (3 and 5), temperature 0.7 and logprobs on, to the version's adapter with the request as sent; an escalated or audited request adds exactly one teacher call |
| `test_confidence_formula_exact` | FR-2.2: two of three samples agreeing, the best majority sample at p = 0.9, give 0.4 × 0.9 + 0.6 × 2/3 (not the minority's 0.99, not the weaker majority sample's 0.6), from `score()` and from the router, which logs the same number |
| `test_confidence_without_logprobs_is_agreement` | FR-2.2: without token logprobs the confidence is the agreement: 2/3 for a split, exactly 1.0 for a unanimous student whose endpoint ignores `logprobs=True` |
| `test_threshold_boundary_serves_at_equality_and_escalates_below` | FR-2.3, FR-2.4, US-3: for a calibrated threshold and for the global default, a confidence equal to the threshold is served by the student (no teacher call, nothing stored) and one a float step below escalates (teacher answers once, its answer stored, the version still logged as the one that tried) |
| `test_audit_rate_zero_and_one` | FR-2.5, US-4: rate 0 never calls the teacher on served answers; rate 1 audits every one, logs each verdict, stores each answer and never changes what the user gets, and `version_live_quality` reads the result; an escalation is never counted as an audit |
| `test_audit_rate_is_respected_statistically` | FR-2.5, US-4: at rate 0.2 over 2,000 served requests (seeded), the audited share is inside the 99% Wilson interval, with one teacher call and one stored example per audit |
| `test_cost_includes_audit_shadow_and_arbitration` | NFR-3: cost and all-teacher baseline to the token in 11 cases (served, audited, escalated, student endpoint down, cold start, pinned task, shadow agreeing, unsure and arbitrated, an audit or an escalation as the shadow's reference), with distinct token counts per model; the ledger row equals the result |
| `test_usage_is_the_served_answer_and_the_breakdown_every_call` | FR-1.12, NFR-3: on the 11 paths of the cost test, `Result`'s usage is the served answer's (the chosen student sample's 30 + 7 tokens, or the teacher's 120 + 45 with its 40 reasoning and 100 cached tokens), `usage_breakdown` lists exactly the calls made (student samples, served, audit, shadow, arbitration; `None` for the others), `cost_usd` equals the price of the breakdown to 1e-12, and the fingerprint is the live version's or the teacher's |
| `test_usage_estimates_missing_counts_and_keeps_details_inside_totals` | FR-1.12: a teacher that reports no usage gets prompt tokens estimated from the messages as JSON (non-ASCII kept) and completion tokens from the answer as served (reasoning stripped), while the breakdown keeps its zeros; an empty answer has 0 completion tokens; reasoning and cached tokens above their totals are cut to them |
| `test_concurrent_requests_keep_the_ledger_consistent` | NFR-6: 8 threads × 40 requests through one `Ratchet`: no request lost or logged twice, one teacher call and one example per audit, each row's cost matches its own path, totals equal the sum |
| `test_each_result_names_its_ledger_row` | ADR 0024: `Result.request_id` is the id of the row `log_request` wrote, on the student, pinned, teacher-only and cold-start paths, and for each of 100 requests from 4 threads |
| `test_each_call_gets_the_parameters_its_role_allows` | FR-1.13, FR-2.1, FR-2.5, FR-2.9, INV-1: with one client parameter set, the confident student gets `n` 3, 0.7, logprobs and its shaping fields, and the audit temperature 0 with the limit, stop and format; an escalation, a cold start, a pinned task and a teacher-only request send the teacher everything, logprobs included (FR-1.14), at the client's temperature; a shadow samples like the live student and its arbitration is a check; without parameters both caps alone; the model is always the configured teacher; a `Task` object works as `task` |
| `test_examples_store_the_conditions_the_teacher_answered_under` | FR-1.13, FR-4.3, INV-2, INV-12: an escalation stores JSON mode and its stop (an email in it redacted and counted) as `params`, hashed with them; the same messages without conditions are a second example, and sampling fields that keep the teacher's own distribution (temperature 0.8, `top_p`, a seed, a zero penalty) bump it; the completion is the teacher's; an audit stores its own (check) conditions |
| `test_reasoning_headroom_marks_answers_over_the_clients_budget` | FR-1.13, FR-2.13, INV-16: with 600 tokens of headroom the teacher gets 620 for a client's 20; an answer whose visible part is longer is served whole with `finish_reason` `length`, not stored, and a `skip_example` event names it; a short one after long reasoning is stored; an over-budget audit is not an audit; without headroom the teacher's own `stop` stands |
| `test_a_request_that_is_not_storable_stores_nothing` | FR-1.13, FR-2.13, INV-2: `RequestMeta(storable=False)` on an audited student answer and an escalation stores and bumps nothing, still logs the audit's verdict, and writes no `skip_example` event |
| `test_bad_generation_settings_stop_startup` | FR-1.13: a forbidden or malformed allowlist entry, or a negative cap or headroom, makes `Ratchet(...)` raise; `Settings.gen_policy()` parses a valid list |
| `test_n_choices_are_scored_as_calibrated_and_choice_zero_is_the_one_choice_answer` | FR-1.14, FR-2.1, FR-2.2, NFR-3: with scripted samples, n = 1 to 4 draws `max(3, n)` samples and reports and logs the one-choice confidence; served choices are the best scored sample then the agreeing ones by token probability, with usage (prompt once, every choice), breakdown, cost and baseline to the token; a confident student that cannot fill n escalates with route `n_choices`, the teacher writes n choices at 0.7, cost and baseline price the whole call, and only choice 0 is stored; an unsure student escalates on `confidence` |
| `test_teacher_writes_n_choices_and_only_choice_zero_is_stored` | FR-1.14, FR-4.3, INV-2, INV-10, FR-2.5: an escalation gets n choices at 0.7 or the client's temperature, and its example is bumped once per request; cold start, pinned and teacher-only requests get n choices, pinned ones stored never; a teacher returning fewer choices gives fewer; an audit of an n = 2 student answer is one answer at temperature 0 without logprobs, judged against choice 0, with a baseline of one audit-sized answer per choice; the shadow samples 3 while the live student draws 4, and its disagreement with choice 0 is arbitrated |
| `test_logprobs_come_back_only_when_asked_and_match_the_score` | FR-1.14, FR-2.2: choice 0's token logprobs spell its text and their mean probability is `Scored.token_prob`, with the client's `top_logprobs` alternatives (the student got `top_logprobs`); none without `logprobs`, the student sampled as before; each of n choices carries its own; the teacher gets `logprobs` and `top_logprobs` only when asked, an audit never; a teacher that sends none gives None |
| `test_empty_json_answer_scores_zero` | FR-2.2, FR-2.6, FR-2.14: three `{}` answers to `ticket-triage` score 0 and `{}` never agrees with `{}` (a strict xfail until field comparators made the built-in fields required) |
| `test_o_series_prefixes_refused` (4 cases) | FR-3.2, US-8, INV-1: `o1-preview`, `o3-mini`, `o4-mini` and `openai/o3-mini` are refused under the OpenAI terms |
| `test_closed_teacher_fails_at_startup` | FR-3.2, INV-1: `Ratchet(...)` raises for `gpt-4o` and for an unknown teacher; the gateway's `Runtime` refuses to start with `gpt-4o` in live and mock mode |
| `test_closed_fallback_teacher_fails_at_startup` | FR-3.2, INV-1, FR-2.12: `Ratchet(...)` raises `LicenseError` for a fallback teacher that is closed (`gpt-4o`, `openai/gpt-4o`, `o3-mini`, a Claude and a Gemini model) or unknown; the gateway's `Runtime` refuses to start with it in live and mock mode; a distillable fallback is licensed up front (`Ratchet.licenses`) |
| `test_examples_record_license_url` | FR-3.3: for two registered teachers, the stored example has the teacher, license name and URL from the registry, the request hash, a split, and the BOM lists exactly that source |
| `test_configured_models_are_licensed_and_day1_checked` | INV-9 (offline proxy): every priced model in `config.py` except `student` is in `licenses.REGISTRY` and in `WANT` in `scripts/day1_check.py`, and the default teacher, judge and fallback student are priced |

### `tests/test_openai_compat.py`

OpenAI compatibility of `/v1/chat/completions` (FR-1.5, FR-1.10, FR-1.12, US-1; [ADR 0012](adr/0012-openai-request-validation-and-error-envelope.md), [ADR 0018](adr/0018-usage-reports-the-served-answer.md)): request validation, message normalisation, the error envelope, the limits, the rule that a client mistake is never a 500, and responses and stream chunks that pass the OpenAI SDK's own models, with `usage` and `system_fingerprint`. Gateway tests reload the mock gateway with every setting they depend on pinned; the SDK tests drive the real `openai` package through `tests/sdk_bridge.py` (skipped if it is not installed).

| Test | Covers |
|---|---|
| `test_bad_request_gets_an_openai_error` (22 cases) | `["hi"]` as messages, `metadata: "x"`, a missing role, role `robot`, temperature 3, `n` 0 and 5 (above `RATCHET_MAX_N`), five stop strings, `top_logprobs` without `logprobs`, `json_schema` without a name, bad JSON (incl. `NaN`, 100,000 nested brackets, bad UTF-8, an empty body, an unpaired UTF-16 surrogate escaped or as UTF-8 bytes), a non-object body, a body over 2 MiB (413), 120,001 characters of input, an image part, and a bad field with `stream: true`: each gets the expected status, `code` and `param` in the envelope (with `detail` repeating the message), as JSON, with nothing logged |
| `test_no_client_mistake_gives_500` | Every value of a request that uses every field (developer role, text parts, tool calls, metadata, `json_schema`, tools) swapped for one of eight kinds of junk, or deleted, each judged by `validate_chat_request` first: one it refuses gets that exact `code` and `param` from the gateway; one it accepts gets 200 from a live-mode gateway with the teacher behind `MockTransport`, and the mock's own 400 in mock mode. Never a 500; refused requests are never logged |
| `test_unpaired_surrogate_is_a_400_in_both_modes` | An unpaired surrogate in a system message sent with a demo ticket, in `name`, in a text part or in a key is 400 `invalid_json` with its JSON path in mock mode, and in live mode the teacher is never called; a valid escaped pair (an emoji) reaches the teacher as one character |
| `test_unknown_v1_path_and_wrong_method` | Unknown paths under `/v1` and `/openai` get 404 `not_found_error` / `unknown_url` with `Unknown request URL: METHOD path.`; `GET /v1/chat/completions` gets 405 with `Allow: POST`; other paths keep `{"detail": "Not Found"}` |
| `test_unhandled_error_is_a_500_envelope_and_an_event` | A crash in the cascade gives 500 `api_error` without the exception's message; the ledger gets `gateway_error` events (class name only) at the 1st and 10th crash; a crash outside `/v1` keeps the plain-text 500 and is counted but not written (11th); when the ledger itself fails at the 100th, the 500 still goes out |
| `test_mock_keyerror_is_400_and_live_keyerror_is_500` | In mock mode an unknown request, a request without a user message and a Scout writer request without JSON facts (also facts nested too deeply to parse) get 400 `mock mode only answers demo requests`; in live mode a `KeyError` from the cascade is a 500 |
| `test_framework_validation_errors_become_400_on_v1_only` | FastAPI's validation errors are 400 envelopes with `param` and `code` on `/v1`, and stay 422 `{"detail": [...]}` elsewhere |
| `test_ratchet_routes_keep_detail` | `/ratchet/redact` (400, and 413 over a 1,000-byte limit), `/ratchet/versions/999/bom`, `/ratchet/demo/day` and `/scout/run` errors keep `{"detail": ...}` |
| `test_checks_run_in_order_and_refusals_cost_nothing` | With a key and a quota of 1: no key and a bad body is 401 (`invalid_api_key`, `WWW-Authenticate` kept); a bad body with a key is 400 and uses no quota; the next good request is 200; the one after is 429 `rate_limit_error` with `Retry-After` and `X-Should-Retry: false`; a bad body at the quota is still 400; one ledger row |
| `test_body_limit_declared_and_streamed` | A declared `Content-Length` over the limit is 413; a chunked body driven straight into the ASGI app is refused at the chunk that passes the limit, with the rest left unread; outside `/v1` the 413 is `{"detail"}`; a chunked body under the limit is answered; a client that disconnects halfway through its body gets a 400, not a 500; `0` turns the limit off |
| `test_message_and_input_limits_come_from_settings` | `RATCHET_MAX_MESSAGES` and `RATCHET_MAX_INPUT_CHARS` (text parts added up) give 400; `0` turns both off |
| `test_developer_role_reaches_the_teacher_as_system` | A `developer` message reaches the teacher, and the stored example, as `system`; `RATCHET_MERGE_SYSTEM=1` merges the leading system messages with a blank line |
| `test_dropped_keys_and_redacted_tool_calls_end_to_end` | Keys a message's role does not use (a vendor field, `annotations`, a tool call's `index`) reach neither the teacher nor the stored example; a tool call's arguments reach the teacher as sent and are stored with `[EMAIL]` (INV-12) |
| `test_text_parts_ticket_matches_the_string_form` | A demo ticket sent as text parts gets the same message back as the string form, and one stored example, seen twice, under the string form's hash |
| `test_multimodal_parts_go_to_the_teacher_only` | With `RATCHET_TEACHER_MODALITIES=text,image_url` and a live student, an image request reaches the teacher with its parts as sent, never the student, stores nothing and logs one teacher row; a text request on the same gateway is still served by the student; audio stays refused; the mock teacher gives a 400 for the image request |
| `test_generation_parameters_reach_teacher_student_and_audit` | FR-1.13, FR-2.1, FR-2.5, INV-1: a live gateway, both models behind `MockTransport`: for `max_completion_tokens` 50, a stop, seed 7, temperature 0.9, `model: gpt-4o` and a vendor `top_k`, the escalated teacher's body has `max_tokens` 50, the stop, the seed and 0.9 under the configured model, the student's has 0.7, `n` 3, logprobs, the stop, the seed and 50, and a forced audit's has 0 with the same stop and limit and no seed; `top_k` reaches the teacher only with `RATCHET_TEACHER_PARAMS=all,top_k` |
| `test_allowlists_and_token_caps_through_the_gateway` | FR-1.13, NFR-3: `max_tokens` 10⁶ is sent as 8,192 to the teacher and 1,024 to the student; without `logit_bias` in `RATCHET_TEACHER_PARAMS` it is not forwarded; `RATCHET_STUDENT_PARAMS=none` leaves the student its cap; `RATCHET_TEACHER_MAX_TOKENS=0` sends the client's value; a request without parameters still carries both caps; a forbidden allowlist entry or a negative cap stops `Runtime` |
| `test_a_cut_off_teacher_answer_reaches_the_client_as_length` | FR-1.13, FR-2.13, FR-1.6: a teacher answer with `finish_reason` `length` is returned with `length` (and only the stream's last chunk carries it), and is not stored; an upstream value clients do not know is returned as `stop` |
| `test_finish_reason_is_never_tool_calls_without_tool_calls` | FR-1.13, FR-1.1, FR-1.6: a request with `tools`: by default neither model gets `tools` or `tool_choice` and the prose answer ends with `stop`; a teacher that answers with a tool call (default or `RATCHET_TEACHER_PARAMS=all`) is a 502 `tool_calls_unsupported` with `X-Should-Retry: false`, also when streaming, and stores nothing; a student sample that ended in a tool call (student tools allowed) is served with `stop`; no response says `tool_calls` without `message.tool_calls` |
| `test_mock_models_honour_max_tokens_and_stop` | FR-1.13, FR-2.13, FR-9.2: in mock mode `max_completion_tokens` 3 cuts the teacher's answer to 12 characters with `length` and stores nothing; a stop string ends it with `stop` and stores it with that condition; the real OpenAI SDK reads `length` |
| `test_openai_sdk_round_trip_and_errors` | The real OpenAI SDK: a `ChatCompletion` with the `ratchet` block, the same answer for text parts and the developer role, and `BadRequestError` with `code` and `param` for temperature 3 and for too much input |
| `test_openai_sdk_auth_and_quota_errors` | The SDK raises `AuthenticationError` with code `invalid_api_key`, and `RateLimitError` for the daily quota without retrying it (two requests sent with `max_retries=2`) |
| `test_responses_are_openai_chat_completions_with_usage` | FR-1.1, FR-1.12, US-1: on a live gateway (both models behind `MockTransport`), a student-served and a teacher-served response pass the OpenAI SDK's `ChatCompletion` model with exactly OpenAI's top-level fields, `service_tier` `default`, a `fp_` fingerprint, `message.refusal` null, `annotations` empty and `logprobs` null; `usage` is one student sample's tokens (10 + 5, not the 3 samples' 15), or the teacher's 40 + 320 with 300 reasoning and 16 cached tokens; the breakdown lists the student's call and the served one; a cut-off teacher answer is `length` and still validates; the real SDK reads the same usage and fingerprint |
| `test_mock_teacher_reports_its_reasoning_tokens` | FR-1.12, FR-9.2, FR-2.9: with `RATCHET_TEACHER_REASONING_TOKENS=120` the mock teacher's response reports 120 reasoning tokens inside `completion_tokens`, `total_tokens` is their sum and positive, the fingerprint is the teacher's; a pinned Scout planner request has usage from its one call and a breakdown with only `served` |
| `test_system_fingerprint_follows_the_serving_configuration` | FR-1.12: equal for answers of one version (and the documented hash of adapter, version, training-set hash and threshold); another after a promotion; the old one again after a rollback; another when the threshold changes; the teacher's own, stable, also in every stream chunk |
| `test_stream_chunks_validate_and_include_usage_ends_with_the_usage` | FR-1.6, FR-1.12, US-1: student- and teacher-served, every chunk passes `ChatCompletionChunk` and carries the id, model, fingerprint, tier and null logprobs; with `include_usage` every chunk has `usage: null` and the last data chunk before `[DONE]` has no choices, the non-streamed usage and the `ratchet` block; without it (absent, false, `{}`) no chunk has `usage` and the block is on the finish chunk; `stream_options` without a stream changes nothing; the real SDK's last chunk has the usage |
| `test_provider_reads_cached_prompt_tokens` | FR-1.12, FR-1.11: `usage.prompt_tokens_details.cached_tokens` reaches every choice of a call; a string, negative or boolean count, or details that are not an object, count as 0 |
| `test_n_choices_from_a_trained_mock_student` | FR-1.14, FR-1.12, FR-1.6, US-1: the mock fleet trained on 40 demo tickets; n = 2 with logprobs gives two choices with the same labels, usage that adds up both (also in the breakdown), a valid `ChatCompletion`, each choice's tokens spelling its content with one alternative each, and choice 0's mean token probability equal to `Scored.token_prob` of the same seeded samples; streamed, choice 0's role, content and finish chunks come before choice 1's, every chunk validates as a `ChatCompletionChunk`, content and logprobs per index add up to the non-streamed ones, the role and finish chunks carry none, and `include_usage` ends with the same usage; without logprobs both are null; the real OpenAI SDK reads both choices and their logprobs |
| `test_n_choices_escalated_over_http` | FR-1.14, FR-1.13, INV-2: escalated, the teacher behind `MockTransport` receives `n` 2 at temperature 0.7 without logprobs, the student `n` 3, the response has both choices (usage 12) and one example is stored; with `logprobs`, `top_logprobs` 2 and temperature 0.3 the teacher gets them all and each choice spells its content, the example bumped once; `n` 4 draws 4 student samples; a teacher-only image request gets `n` too; a reasoning teacher whose tokens include `<think>` text returns only the answer's tokens and nothing of the reasoning; a teacher that returns no logprobs gives `{"content": null, "refusal": null}` per choice, also in every streamed content chunk, and the response and chunks still validate |
| `test_max_n_comes_from_settings` | FR-1.14, FR-1.10: `RATCHET_MAX_N=1` refuses `n` 2 (400 `unsupported_value`, nothing logged), `6` accepts 5 (five choices from the mock teacher), the default is 4, and 0, -1, 129, 2.5 or `True` stop `Limits` and the `Runtime` with a `ValueError` naming the variable |
| `test_chat_request_fields` | `ChatRequest`: `max_completion_tokens` wins over `max_tokens`, a string `stop` becomes a list, `null` fields are unset, unknown fields are kept in `extra`; conversations with tool calls, tool answers, legacy function calls and assistant messages dumped from a response (unused fields `null`) pass; valid forms of every optional field come back as sent; a non-string `metadata` key from a Python caller is refused |
| `test_validation_rules` (87 cases) | One case per rule in [API.md](API.md#request-validation): the body, messages, roles, content and parts, tool messages, assistant tool calls in full (`id`, `type`, `function.name`, `function.arguments`), legacy `function_call` arguments, `refusal`, every sampling range, `n`, `stop`, `logit_bias`, `logprobs`, `metadata` (17 keys, long keys and values, task names incl. a trailing newline), `response_format` and `json_schema` (name, type, depth 17, over 64 KB, `strict`), `tools` (129, custom type, names, parameters, over 64 KB), `tool_choice`, the legacy function fields, the string and boolean fields and `stream_options.include_usage`: each gives a 400 with the documented `code` and `param` |
| `test_limits_and_their_off_switch` | `Limits`: too many messages, too many characters in a string, in text parts, in a refusal part, and with `name`, `refusal`, `tool_call_id` or tool and function call arguments counted; keys a role does not use are not counted; exactly at the limit passes; `0` means no limit; `max_n` (4 by default) |
| `test_parse_body_and_error_helpers` | Strict JSON (`Infinity`, `NaN`, bad UTF-8 refused); a valid surrogate pair is one character (bytes, text, UTF-16), and an unpaired surrogate (escaped, as bytes, in a key, CESU-8) is refused with its JSON path in an error that is itself valid UTF-8; the status-to-type table for plain HTTP errors (401 `invalid_api_key`, 403, 404 `unknown_url` only for the router's own 404, 409, 413, 429, 5xx `api_error`); headers kept; FastAPI validation errors turned into a JSON path |
| `test_normalize_rewrites_only_what_it_must` | `developer` → `system` (other keys kept), text parts joined by newlines, a refusal part becomes text, keys a role does not use dropped (assistant `audio` and `annotations`, vendor fields, `tool_calls` on a user message), tool and function calls kept to their documented fields, plain messages returned as the same objects, the caller's list never changed, and merging leading system messages |
| `test_non_text_parts_need_a_teacher_modality` | `image_url`, `input_audio` and `file` parts, and a refusal part from the user, are refused with their exact `param`; with `image_url` allowed the message comes back as sent and `multimodal` is set; `parse_modalities` |
| `test_normalize_is_identity_on_the_mock_world` | Every mock ticket (every template, drift too) and every Scout request in the research world comes back as the same objects, with or without merging, and passes validation with its task, so example hashes, golden splits and the reference simulation cannot move |

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

### `tests/test_live.py`

The live-path tests ([Live-path tests](#live-path-tests)). Each runs offline: model endpoints are `FakeOpenAIServer` behind `httpx.MockTransport`, commands go to `RecordingRun`, and the Scout CLI reaches the gateway through `AppTransport`.

| Test | Covers |
|---|---|
| `test_nebius_trainer_writes_syncs_trains_and_hot_loads` | FR-4.6, FR-8.2, US-10: commands in order (`RATCHET_SYNC_CMD`, then `bash deploy/train_job.sh student-v1`, each with `check=True`); `night/train.jsonl` holds exactly the rows and is on disk before the sync; then `POST {root}/v1/load_lora_adapter` with `lora_name`, `lora_path` under `/data/adapters` and the endpoint's bearer token. `{version}` and `{data}` are substituted, `RATCHET_ADAPTER_ROOT` moves the path, no sync command means no sync, a failed job stops before loading, and an empty `student_base` raises. `deploy/train_job.sh` mounts the bucket at `/data`, reads `/data/night/train.jsonl` and writes `/data/adapters/${VERSION}`, the paths the trainer uses |
| `test_load_adapter_tolerates_already_loaded_and_raises_otherwise` | FR-8.2: the vLLM root is the base URL without `/v1` (with or without a trailing slash, or no `/v1` at all); no token, no header; a 400 saying "already" (any case) is tolerated; any other 400, a 401, a 404 (runtime LoRA loading off) and a 500 raise |
| `test_reload_adapters_loads_live_canary_shadow` | NFR-2: only `live`, `canary` and `shadow` adapters are reloaded, in version order, from `RATCHET_ADAPTER_ROOT`, with the bearer token; one already loaded is skipped over; nothing to reload makes no request; a 503 raises |
| `test_live_main_tick_reload_night_offline` | FR-8.4, FR-3.6, FR-15.2: `python -m ratchet.live tick`, `reload` and `night` against a ledger file with 60 train and 6 golden examples. The night records its training job as a closed `training_job` compute interval at the default GPU rate, linked to the version; a night that skips records none. `night` builds the student and judge clients with the configured bases and keys, the judge with the teacher's timeouts and retries and the student with its own timeouts and the teacher's retries (no teacher stands behind it in the eval), runs the training command, hot-loads `student-v1`, samples it on every golden row as in production, asks the judge in JSON mode, admits it to canary and writes `data/boms/student-v1.json`, equal to the stored BOM and signed with `RATCHET_BOM_SIGNING_KEY`. `reload` then loads the canary; no command means `night`, which skips while the release is in flight; an unknown command exits with a message |
| `test_live_runtime_wiring` | FR-8.1, FR-8.3, FR-1.8, INV-7: the gateway's live `Runtime` with `transports`, over HTTP. A confident student answers from the adapter named by the version, with the endpoint token and `STUDENT_SAMPLING`; an unsure one escalates to the teacher with the Token Factory key and model, and the answer is stored. A 500 from the student falls back to the teacher (`student_error` event); a 500 from the teacher is a 502 `teacher_unavailable` after two retries (waits injected). Without `student_base`, the hosted Nano on Token Factory answers under the global threshold, not the version's. The mock warm-up never runs in live mode, and an unknown transport key is refused |
| `test_live_evaluator_uses_production_sampling` | FR-5.1, FR-5.5: one cascade call per golden row with `n = samples`, exactly `STUDENT_SAMPLING` (read-only) and the student's token cap as `params` (rows without stored conditions), the judge's candidates under the same `params`; the judge grades free-text rows only, in JSON mode, with request, reference and candidate; its pass rate is reported; a judge answer that is not a clear `{"score": 1}` (not JSON, a list, a string or 2, no score) scores 0, while fences and reasoning around valid JSON are fine; no judge or no free-text rows means no judge calls |
| `test_production_and_both_evaluators_share_student_sampling` | FR-5.1, FR-2.1: `Ratchet._sample`, `LiveEvaluator.evaluate` and `MockFleet.evaluate` send the same sampling arguments, `{"n": 3, **STUDENT_SAMPLING, "params": GenParams(max_tokens=1024)}` |
| `test_train_parse_args_defaults_and_row_contract` | FR-4.5: `train.py` defaults (epochs, learning rate, rank, sequence length, batch, Mamba projections in the targets), required `--data` and `--out`, the same base model in `config.py`, `train_job.sh` and `student_endpoint.sh`; rows written by Night School load back unchanged (blank lines skipped), end with the teacher's stored answer and carry teacher and license; `train.manifest` (saved as `ratchet_manifest.json`) names the base model and the licenses |
| `test_scout_cli_over_http` | FR-10.6, FR-1.7: `python -m ratchet.scout` against the mock gateway in-process, with an API key: every agent prints a line, `--max-results` reaches search, citations and the cost summary print, every call is logged under the key's name with its task header (one planner and one writer request); the key also comes from `RATCHET_API_KEY`; a wrong key exits 1 with the 401 on stderr |
| `test_sim_cli_writes_json_and_table` | FR-9.4, FR-15.3: `python -m sim.simulate` (2 days × 200, mixed, `--drift-day 0`) prints one table row per day, the totals, the GPU-hour line with its recommendation (labelled simulation) and every event, and writes the JSON results with the documented keys, `cost` included (48 endpoint hours) |
| `test_mock_warmup_trains_a_student` | FR-9.6: `RATCHET_MOCK_WARMUP_DAYS=1` and `RATCHET_MOCK_WARMUP_REQUESTS=300` give day 1, 300 requests of mixed traffic, a `student-v1` in canary with a BOM and a `warmup` event, also in `/ratchet/stats`; the demo reset warms up again on a fresh ledger |
| `test_trust_proxy_uses_forwarded_ip_only_when_enabled` | FR-10.5: with `RATCHET_TRUST_PROXY=1` the guard sees the first `X-Forwarded-For` address on `/scout/run` and `/scout/stream` (the peer address when the header is missing or empty); with it off the header is ignored; with it on, two forwarded clients each get their own hourly limit |
| `test_day1_check_reports_missing_models_and_logprobs` | FR-8.5, INV-9: `scripts/day1_check.py` against a fake Token Factory: no key exits 2 without a request; a missing model is reported (with the NVIDIA models that exist) and never called; missing logprobs and unparseable JSON are reported, and each model's `finish_reason` is printed (INV-16 stores only `stop` answers); an HTTP 500 fails the check (exit 1); every model request has the bearer key, `logprobs`, JSON mode and `max_tokens` 400; then the teacher gets `max_tokens` 8,192 with a stop, a seed, a temperature and JSON mode, and a 400 there fails the check; all present exits 0 with "All good." |
| `test_deploy_scripts_parse_and_dry_run` | FR-11.1, FR-11.2: `bash -n` on every `deploy/**/*.sh`; `DRY_RUN=1 bash deploy/vm/create_vm.sh` prints the subnet, disk and instance `nebius` commands with the project id and `<cloud-init.yaml>` in place of the user data, never the private-repo token; a given subnet skips the lookup; a missing project, missing key file, reserved user name or bad preset stops before any command; `backup.sh` exits 0 in mock mode. Skipped where bash is missing |

The React app itself is checked with `npx tsc --noEmit` and a production build in CI (`web` job), and by Playwright screenshots during development (light, dark, 1440 and 390 px).

During development the v0.4 features were also checked end to end against a running gateway with the official OpenAI Python SDK (401 on a wrong key, streaming text identical to the non-streaming answer, the quota cutting one key off at its limit while another kept working, a signed BOM verifying with `python -m ratchet.bom verify` and failing once edited), and the dashboard's Trust and privacy panel with Playwright in light and dark mode at desktop and phone widths.

### `tests/test_cost.py`

Utilization-aware cost accounting (FR-15.1 to FR-15.4, NFR-11, [ADR 0023](adr/0023-utilization-aware-cost-accounting.md)). Hand-built ledgers run on the synthetic clock (day N starts at N × 86400 s) and every number is worked out in the test; the simulated days assert signs and trends only. A module fixture runs five simulated days of 1,500 tickets on a mock `Runtime` once (about 4 s).

| Test | Covers |
|---|---|
| `test_split_is_exact_on_every_cost_path` | FR-15.1, NFR-3: the 11 cost paths of the contract test, each row's student part (live and shadow samples), teacher part (escalation, audit, arbitration, pinned), `cost_usd` equal to their sum and to the contract test's exact values, and the student tokens |
| `test_pinned_and_teacher_only_requests_are_all_teacher_cost` | FR-15.1, INV-10: a pinned task and a teacher-only request: no student call, every cent the teacher's, zero student tokens |
| `test_split_holds_on_every_row_of_simulated_days` | FR-15.1, FR-15.2: 7,500 simulated requests with canaries, shadows, audits and arbitrations: every row's parts add up (within 1e-12), one 24-hour endpoint interval per day and one training interval per trained version (600 s + 0.05 s per example from 22:00) |
| `test_overlapping_and_nested_intervals_are_not_double_counted` | FR-15.2: nested, overlapping and separate intervals merge into hours once; a pricier overlapping record bills only its stretch at its rate; clipping to a window; empty records count nothing; the per-day split adds up |
| `test_open_interval_counts_to_now_and_to_the_window` | FR-15.2: an open interval runs to `now`, never past the window, never negative; listed in `open_intervals` |
| `test_endpoint_start_is_idempotent_and_stop_closes_it` | FR-15.2: a second `up` (also from a second connection to the file) returns the open interval; an end before the start is refused and closes nothing; `down` while down is None; jobs get an interval per start and close by id with their version and note |
| `test_concurrent_endpoint_starts_keep_one_open_interval` | FR-15.2, NFR-6: eight threads starting the endpoint at once leave one interval |
| `test_bad_compute_records_are_refused` | FR-15.2: an unknown kind, a negative or NaN rate, `True` or negative GPUs, an infinite time (7 cases) |
| `test_training_interval_is_closed_when_train_raises` | FR-15.2, FR-4.2: `run_night(train_gpu=...)` closes the interval at the failure time with a note and re-raises; a successful night records the job's duration and version; without a rate it records nothing |
| `test_report_math_on_a_hand_built_ledger` | FR-15.3, FR-15.4, NFR-11: every total, both views (a negative GPU-hour saving, not clamped), s̄, break-even, the hosted-fallback price and crossover, observed volume, utilization, idle hours, the `hosted_fallback` recommendation, the daily rows, the headline following `RATCHET_STUDENT_COST_MODEL`, and the window arguments |
| `test_recommendation_for_each_branch` | FR-15.4: `self_host`, `hosted_fallback`, `scale_to_zero` (idle days, and below break-even with a pricier hosted fallback), `insufficient_data` (no uptime; uptime but no student answer), an empty ledger with no division by zero or NaN |
| `test_live_ledger_days_count_from_the_first_request` | FR-15.3: a live ledger's days start at its first request; uptime before it is day 0; a one-day window takes that day's hours; a reopened file reports the same days |
| `test_student_answers_without_recorded_uptime_are_priced_per_token` | FR-15.3, NFR-11: student spend on a day with no recorded uptime stays in the GPU-hour view at its per-token price, stays out of the observed volume, and gets a warning |
| `test_report_warnings_and_daily_cap` | FR-15.3: a training job open for 30 hours is named with the command that closes it; the daily breakdown keeps the latest `MAX_DAILY` days while the totals keep every day |
| `test_mock_demo_with_a_24h_endpoint_does_not_pay` | FR-15.3, FR-15.4, NFR-11: five simulated days of 1,500 requests: per-token savings positive, GPU-hour savings negative, never `self_host`, observed below break-even; `/ratchet/stats` `cost` and `totals.cost_model`; `?days=1`; 400 and 422 for bad `days` |
| `test_simulated_endpoint_hours_follow_the_setting` | FR-15.2, FR-9.4: `RATCHET_MOCK_ENDPOINT_HOURS=8` records 8-hour days without events; `0` records none and the report says `insufficient_data` |
| `test_post_endpoint_records_uptime_with_an_admin_key` | FR-15.2, INV-13: 401 without a key, 403 for an API key, 400 for each bad body (8), `up` with rate, GPUs and note, `up` again unchanged, `down`, `down` again; `endpoint` events with the admin's name and no key anywhere |
| `test_post_endpoint_is_open_in_mock_capped_and_off_in_live` | FR-15.2, INV-8: open in mock mode as `mock-admin` on the synthetic clock, the mock cap (409, never for `down`), 403 in live mode without admin keys, and the report public in live mode |
| `test_cli_records_and_reports` | FR-15.2, FR-15.3: `endpoint up` (with `--at` and `--gpus`), `up` again, `down` with an ISO time, `report` and `report --json` (the second `up` adds nothing), `--days 1` meaning today, `close training_job`; exit 2 for a missing ledger (never created), a bad or future `--at`, an end before the start, `--days 0` and a bad setting |
| `test_cli_runs_as_a_module_with_the_environment` | FR-15.3: `python -m ratchet.cost report` in a subprocess with `RATCHET_DB` |
| `test_deploy_script_records_uptime_and_never_fails_on_it` | FR-15.2, FR-8.2: `deploy/student_endpoint.sh` start and stop with a stub `nebius` record the endpoint through the CLI; with no ledger the deploy still exits 0 and says so; an unknown action exits 2. Skipped where bash is missing |
| `test_v04_database_gets_the_cost_columns_and_table` | FR-15.1, FR-15.2: a v0.4 file gains the split and token columns and `compute_intervals`; its old rows count as teacher spend (`split_unknown_requests`, a warning) and give no hosted-fallback price |
| `test_bad_cost_settings_stop_startup_naming_the_variable` | FR-15.3, NFR-11: a negative, non-numeric or NaN rate, fractional or negative GPUs, an unknown cost model and 25 endpoint hours a day each stop the runtime with the variable's name (7 cases) |
| `test_cost_settings_defaults_and_spellings` | NFR-11: the defaults, a cost model in any case with spaces, `RATCHET_PRICES_VERIFIED=1` changing the label, an empty model meaning `per_token`, and `--at` parsing |

### `tests/test_stats.py`

The exact statistics of `ratchet/stats.py` ([ADR 0024](adr/0024-evidence-foundations.md)). Monte Carlo checks are seeded, counts-only and sized to run in under a second; the two marked slow repeat them at full size.

| Test | Covers |
|---|---|
| `test_binomial_tails_match_brute_force` | `binom_sf`, `binom_cdf` and `binom_logpmf` equal sums of `math.comb` terms for every k of small n, edge probabilities 0 and 1 and 60 random cases at n = 120 |
| `test_binomial_tails_keep_tiny_values_and_refuse_bad_input` | 0.5^100 and 0.1^200 to relative 1e-12 and 1e-9 (summed directly, not as 1 minus almost 1); bad counts and probabilities raise |
| `test_clopper_pearson_bounds` | 46/48 → 0.8746, 129/130 → 0.9640, 127/128 → 0.9635; s/s → delta^(1/s); for 50 random (s, n) the bound is where P(X ≥ s) = delta, from below, and the upper bound mirrors it |
| `test_precision_pvalue_planner_and_mcnemar` | `required_rows`: 0.95 → 59, 93, 124, 153 rows for 0 to 3 errors; 0.97 → 99, 157, 208, 257; 0.90 → 29, 46, 61, 76; the p-value falls as agreement rises; McNemar 4 vs 0 is 0.0625 |
| `test_ltt_fixed_sequence_stops_at_the_first_failure` | 10 correct points certify nothing (1.01); 80 correct at 0.97 and 40 wrong at 0.72 certify 0.75 and stop at 0.70 with its trace; grid order does not matter; bad grids raise |
| `test_ltt_controls_the_error_rate` | Confidences U(0,1) with P(correct) = c: over 200 seeded draws of 400 rows, the chosen threshold has true precision below the 0.80 target in at most 5% (+3 SE) of draws |
| `test_e_value_oracles_and_confidence_sequence_oracles` | e(0/2 below 0.92) = 29.77, e(0/1) = 4.22, e(20/20) = 0.2006, e(7/17) = 2.59e5, e(60/60 above) = 27.6, e(83/83 above) = 129.7, within 1%; CS(0/2) = (0.001, 0.931) and CS(35/38) = (0.710, 0.992) within 0.002 |
| `test_alt_grid_components_are_supermartingales` | For p0 in 0.5, 0.8, 0.92, 0.97 and every true rate on the null side, each alternative's one-trial mean factor is at most 1 (the validity argument, checked exactly); weights sum to 1, also near the edges |
| `test_e_values_edges_and_input_checks` | No trials: e = 1; a huge count stays finite (capped at exp(690)); the confidence sequence narrows with data; bad sides, probabilities and steps raise |
| `test_e_process_false_rollback_rate_and_wilson_peeking` | At a true rate on the 0.92 floor, checked after every audit (120 runs of 200): the e-process rolls back at most 5% (+3 SE); Wilson's bound checked the same way does more than 10% (about 23%, simulation) |
| `test_confidence_sequence_covers_the_truth_at_every_step` | At p = 0.95, the true rate leaves the 95% confidence sequence at any step in at most 5% (+3 SE) of 120 runs |
| `test_e_process_and_confidence_sequence_error_rates_full` (slow) | The two checks above at 300 runs of 300 audits, at 0.92, 0.95 and 0.80 |
| `test_clopper_pearson_coverage_full` (slow) | P(cp_lower ≤ p) ≥ 0.95 (−3 SE) for (20, 0.90), (50, 0.95), (100, 0.97) and (130, 0.99), 2,000 draws each |
| `test_wilson_lives_in_stats_and_release_still_exports_it` | `release.wilson` is `stats.wilson`; 18/20 → (0.699, 0.972) |
| `test_kish_and_hajek` | Kish's n_eff of equal and unequal weights; the Hajek mean of a hand-computed example; empty or zero weights give None; bad input raises |
| `test_ratio_estimate` | R = 1.3 recovered from 400 synthetic pairs; the ratio and standard error equal the textbook formulas; equal weights change nothing; weighted case by hand; too few pairs or no x are `insufficient` (ratio 1.0, se None) |
| `test_ratio_estimate_interval_covers_the_truth` | R ± 1.96 se covers 1.3 in at least 90% of 200 seeded samples of 150 rows |

### `tests/test_provenance.py`

Merkle trees and the BOM v2 scaffold ([ADR 0024](adr/0024-evidence-foundations.md)).

| Test | Covers |
|---|---|
| `test_keyset_roots` | The empty set's root is 32 zero bytes, one key's root is its leaf hash, hex and bytes agree, and the root depends on the set, not the order |
| `test_keyset_proves_members_and_non_members` | 1,000 random members verify `included` (with their value) and 1,000 random non-members `absent` |
| `test_keyset_agrees_with_a_brute_force_set` | Sizes 0 to 40 over keys that share long prefixes: every key of a 120-key universe verifies `included` exactly when it is in the set |
| `test_tampered_proofs_never_prove_inclusion` | Flipping a byte of any sibling or of the terminal value makes a proof `invalid`; another key or an absent key never reads as `included`; an inclusion proof never reads as `absent` |
| `test_malformed_and_overlong_proofs_are_invalid` | A terminal leaf without the key's prefix, 257 siblings, missing fields, bad hex and wrong lengths are `invalid`, never an error; duplicate or short keys raise when building |
| `test_keyset_builds_and_proves_quickly` | 2,500 keys built and 200 proofs in well under 0.5 s |
| `test_log_tree_matches_rfc_vectors` | RFC 9162: the empty tree is sha256(''), the leaf hash of '' is 6e340b9c..., the Certificate Transparency 8-leaf vector's root is 5dc9da79..., and every prefix matches an independent implementation |
| `test_every_inclusion_path_verifies_and_alterations_fail` | Sizes 1 to 64: every audit path verifies (bytes and hex); an altered leaf or index, another tree's root, a size the path cannot fit or an extra node fails; malformed input is False |
| `test_bom_v2_manifest_sections` | Schema v2 with every v1 field, `redactor`, `training.origins`; commitments only when given (`ratchet.smt/v1`, size checked against the rows); `EXTRA_KEYS` merged; unknown sections, a wrong size and a bad root refused |
| `test_bom_verify_accepts_v1_and_v2_and_refuses_unknown_schemas` | Signed v2 and v1 documents verify with the public key, an unsigned v1 document by its hash; an unknown schema and junk are refused |
| `test_rows_report_their_origin` | `dataset.rows` reports `examples.origin`, or `live` when it is not recorded |
| `test_night_writes_a_v2_bom_with_its_redaction_setting` | `run_night(redact_pii=False)` writes a verifiable v2 BOM with `redactor.redact_pii` false and the origins of its 60 rows |
| `test_mock_gateway_bom_records_the_redaction_setting` | A mock `Runtime` with `redact_pii=False` writes it into its first adapter's BOM |

### `tests/test_privacy.py`

Data governance: storage opt-outs and pseudonymous end users (FR-13.1, FR-13.2, NFR-4, [ADR 0080](adr/0080-storage-opt-outs-and-pseudonymous-end-users.md)). The proposed invariant "opt-outs only reduce storage" and its proving tests are listed in [docs/lanes/e-invariants.md](lanes/e-invariants.md).

| Test | Covers |
|---|---|
| `test_store_false_escalation_calls_the_teacher_and_stores_nothing` | FR-13.1, INV-2: `RequestMeta(storable=False)` on an escalation: the teacher answers, `Result.stored` false, `store_opt_out` `request`, the row `stored` 0 with its reason; the storable request stores, an opted-out duplicate (reason `key`) does not bump it; no `skip_example` event |
| `test_store_false_still_audits_and_arbitrates_without_storing` | FR-13.1, INV-15: with `audit_rate=1` the audit still records `audit_agree` 1 and nothing is stored; a shadow that disagrees is still arbitrated (`shadow_agree` 0) and the arbitration is not stored, while the same disagreement on a storable request stores it |
| `test_zero_retention_stores_nothing_for_any_caller` | FR-13.1, NFR-4: `store_examples=False` stores nothing for Scout's call shape (no meta), a request with an end user, or an audited student answer (still audited); every row `global`, `stored` 0 |
| `test_zero_retention_mode_night_skips` | FR-13.1: `run_night(store_examples=False)` skips with `storage disabled` before the trainer or the evaluator is called, although 60 stored examples would train; the default trains; the mock `Runtime`'s simulated day under `RATCHET_STORE_EXAMPLES=0` serves 100 requests, stores nothing and skips |
| `test_live_night_skips_under_zero_retention` | FR-13.1: `python -m ratchet.live night` with `RATCHET_STORE_EXAMPLES=0` prints `storage disabled`, runs no training command and creates no version |
| `test_no_store_tasks_from_the_setting_and_the_spec` | FR-13.1: `RATCHET_NO_STORE_TASKS` (spaces trimmed, unregistered names allowed) and a spec's `store: false` give reason `task`; other tasks are stored; a space-separated list or an over-long name stops startup |
| `test_examples_record_client` | FR-13.2, FR-3.3: an example keeps its first client and end user; a duplicate from another client adds one `example_clients` row, its own duplicates, internal calls and opted-out requests add none; an example first stored without a client records the first client that sends it again |
| `test_no_train_end_users_are_never_stored_even_when_registered_mid_request` | FR-13.2: a registered end user's escalation is served and not stored (`end_user`), another end user's is; a registration made while the request waits for the teacher still keeps it out (the store-time check) |
| `test_pinned_and_teacher_only_requests_record_the_end_user_and_opt_out` | FR-13.1, INV-10: pinned and teacher-only rows record the end user's hash and the opt-out, and store nothing |
| `test_opt_outs_hold_under_concurrent_requests` | FR-13.1: 80 requests from 8 threads, three quarters opted out three ways (request, key, no-train end user): exactly the 24 storable ones are stored, and every row's `stored` matches its `store_opt_out` |
| `test_store_false_over_http_escalation` | FR-13.1: `store: false` over HTTP on an escalation: the student and the teacher are called once each, `ratchet.stored` false and `store_opt_out` `request`, nothing stored; `store: true` stores |
| `test_store_header_metadata_and_key_opt_outs` | FR-13.1: `X-Ratchet-Store` `none`, `0` and ` FALSE `, `metadata.ratchet_store` `"false"` and `"0"`, and a key with `store=0` in `RATCHET_KEY_LIMITS` (also with `store: true`, `X-Ratchet-Store: 1` and `ratchet_store: "true"`) store nothing; explicit opt-ins store; any one opt-out wins; an unknown header or metadata value is a 400 before anything is logged; `/ratchet/stats` names the key and counts by reason |
| `test_end_user_is_an_hmac_and_the_raw_value_is_stored_nowhere` | FR-13.2, INV-12, INV-13, NFR-4: with redaction off, `user` and `safety_identifier` (which wins) become 16-hex HMACs scoped per key in `requests` and `examples`; an empty `user` is none; a 129-character `user` is a 400 that does not quote it; the raw values, a no-train value and the keys appear in no text value of any table; events carry no hash; `clients[].end_users` and `privacy.no_train_end_users` count |
| `test_end_user_hash_survives_reopening_the_file_ledger` | FR-13.2: the generated key survives reopening the file (same hash, no event); another file gives another hash; `RATCHET_END_USER_KEY` gives the same hash in any file; switching keys writes one `end_user_key` event without the key; a short key or a corrupted stored key raises |
| `test_after_no_train_registration_escalations_are_not_stored` | FR-13.2: `POST /v1/ratchet/no-train` with an API key registers the scoped hash without returning it (`created` true, then false); that client's end user is no longer stored, another client's same identifier is; 403 for another client, 200 for its own name; an admin registers for every key and the keyless scope, or for a named client (400 for an unknown one); a client cannot remove an admin's registration (`kept`), removes its own (`removed`, and storage resumes), an admin removes any; `absent`; an admin registration over a client's own protects it; `no_train` events name who and never the end user or its hash; 401 without or with a wrong key |
| `test_no_train_requests_are_validated` | FR-13.2: missing, non-string, empty and over-long `end_user` (never echoed), an unknown field (`unknown_parameter`), a non-string `client`, a non-object body and bad JSON are 400s in OpenAI's envelope; a keyless caller cannot name a client (403) and registers in the keyless scope; the mock cap (409) |
| `test_removing_a_registration_needs_a_key_in_live_mode` | FR-13.2, NFR-5: a live gateway without API keys registers for anyone but removes only for an admin key (403 otherwise) |
| `test_metadata_is_echoed_and_the_ratchet_block_says_whether_it_was_stored` | FR-13.1, FR-7.7: `metadata` echoed in the response, on the stream's `ratchet` chunk (the finish chunk, or the usage chunk with `include_usage`) and nowhere else, absent when not sent, never stored |
| `test_privacy_stats_report_opt_outs_and_clients` | FR-13.1, FR-13.2, FR-7.7: `/ratchet/stats` privacy keys on a fresh gateway (zero counts, `no_store_tasks` from the setting and specs, `no_store_clients`), `not_stored_by_reason` after traffic, `clients[].end_users` and `stored_share` (3 of 5), and `store_examples` false and `end_user_key` `env` when set |
| `test_key_limits_parse_and_refuse_typos` | FR-13.1, INV-13: `RATCHET_KEY_LIMITS` parsing (spaces, empty fields, several keys); an unknown name (not quoted, in case it is a key), an unknown field, a bad `store`, a key or field twice and malformed entries raise; with keys off every name is unknown |
| `test_storage_settings_refuse_typos` | FR-13.1, FR-13.2: `RATCHET_STORE_EXAMPLES` accepts 1/0, true/false, yes/no, on/off in any case (empty is the default) and refuses anything else; a non-bool in code stops `Ratchet`; a short `RATCHET_END_USER_KEY` and `RATCHET_KEY_LIMITS` without API keys stop the gateway |
| `test_ledger_from_before_opt_outs_gains_the_columns_and_tables` | FR-13.2: a file from before this change gains `requests.end_user` and `store_opt_out`, `examples.client` and `end_user`, `example_clients`, `no_train` and the `(end_user, ts)` index (used by the query planner), twice without change; old rows read NULL |

## Live-path tests

Live mode, the training job, the day-1 check, the Scout CLI and the deploy scripts would otherwise run for the first time on paid Nebius resources. `tests/test_live.py` runs each of them offline first, through seams that are default arguments, so production code passes nothing and talks to the real services:

| Seam | Where | Test double |
|---|---|---|
| `transport=` | `OpenAICompatProvider(base_url, api_key, timeout, retry, transport, sleep, clock=, rng=)` | `FakeOpenAIServer(...).transport` (an `httpx.MockTransport`); `sleep=`, `clock=` and `rng=` replace the waits between retries |
| `transports=` | `Runtime(settings, transports={"teacher": ..., "student": ...})`, live mode only (other keys raise) | the same; later items drive live mode offline through it |
| `client=` / `http=` | `load_adapter`, `NebiusTrainer`, `reload_adapters`, `live.main`, `http_caller`, the Scout CLI's `main(argv, client)`, `day1_check.main(client)` | an `httpx.Client` on a fake server, or on `AppTransport(gateway.app)` |
| `run=` | `NebiusTrainer`, `live.main` (in place of `subprocess.run`) | `RecordingRun` |
| `provider_factory=` | `live.main` (builds the student and judge clients) | a factory returning providers on fake servers |
| `argv=` | `sim.simulate.main(argv)`, the Scout CLI | a list |

The student's sampling parameters are one read-only constant, `confidence.STUDENT_SAMPLING` (`temperature` 0.7, `logprobs` on), used by `Ratchet._sample`, `LiveEvaluator.evaluate` and `MockFleet.evaluate`, so the golden eval samples exactly as production does (FR-5.1) and a test fails if one of them drifts. The judge's free-text candidate is still sampled greedily (TASK-FREE-TEXT-SIMILARITY changes that).

These tests prove the wiring, the order of operations and the failure handling. They do not prove that the real services accept the requests: model ids, `nebius` CLI flags, vLLM's runtime LoRA loading and GPU training stay `untested (live)` in the PRD until the day-1 checks ([DAY1.md](DAY1.md)) and a live run pass.

Found while writing them: without `RATCHET_STUDENT_BASE`, `NebiusTrainer` ran the sync and the paid training job before noticing there was no endpoint to load the adapter into. It now checks first, and `python -m ratchet.live reload` gives the same clear error instead of an httpx URL error (`test_live_night_checks_the_student_endpoint_before_paid_work` in `test_hardening.py`).

## Contract tests

`tests/test_contract.py` was written before the v0.5 rewrites of `Ratchet.handle` and `gateway.chat` (request validation and normalisation, field comparators), as their regression net. Both have landed (`tests/test_openai_compat.py`, `tests/test_tasks.py`), and their five strict xfails now pass as ordinary tests. Each test pins behaviour that someone outside the module depends on: an OpenAI client (status codes, `/v1/models`, task selection), the dashboard (`/ratchet/stats`), an auditor (`/ratchet/bom`, license fields), or the operator's bill (cost to the token).

Rules:

- **The stats keys only grow.** `tests/fixtures/stats_keys.json` lists every key the dashboard reads from `/ratchet/stats` (it mirrors `Stats` in `web/src/lib/api.ts`) with its JSON type; a `[]` key is a list whose every item is checked. A change may add keys to the payload and to the fixture. It must not remove or retype one, so the React dashboard and other clients keep working. The test also fails if the fixture loses one of the keys this contract started with.
- **Known bugs are strict xfails that name the fixing item**, a v0.5 work item (none today: `API-VALIDATE-NORMALIZE` removed its four and `TASK-COMPARATORS` its one). When the fix lands the test passes, strict mode turns that into a failure, and the fix removes the marker in the same change. Write the mark literally (`@pytest.mark.xfail(strict=True, reason=...)`, or `marks=pytest.mark.xfail(...)` in a `pytest.param`): the traceability scan reads marks from the source, so a mark held in a variable would make an xfail count as proof.
- Cost assertions are token-exact: each model gets distinct token counts, and the expected cost is built from `Settings.price(...)`, so a term billed with the wrong tokens, price or multiplicity fails.
- The dashboard's rendering is still checked by hand ([web/CLAUDE.md](../web/CLAUDE.md)); `test_stats_contract` proves the data it renders. FR-7.6 (light and dark mode, phone width) stays a manual check.

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

This run is the pinned reference ([below](#the-pinned-reference-simulation)), so these numbers cannot drift silently.

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

Fixes 1, 3, 4 and 6 also have direct tests now: `test_champion_rescored_on_same_golden_set`, `test_golden_rows_never_reach_the_trainer_except_frequent`, `test_night_skips_with_reason` and `test_cascade_eval_measures_on_held_out_half`; fix 2 is covered by `test_shadow_promotes_and_both_rejections` and `test_shadow_arbitration_calls_teacher_once_and_stores_example`.

Two more problems were fixed earlier in development. A fixed global threshold let about 10% wrong answers through, which led to per-version calibration ([ADR 0003](adr/0003-per-request-cascade-with-calibrated-confidence.md)). A point-estimate rollback rule would have rolled back healthy canaries about 19% of the time at n=20, which led to the Wilson upper bound ([ADR 0005](adr/0005-shadow-canary-rollback-with-wilson-bounds.md)).

## Issues found while documenting, and their fixes

Writing these docs turned up ten gaps in v0.1. Each one is fixed in v0.1.1 and covered by `tests/test_hardening.py`.

| # | Issue | Fix | Test |
|---|---|---|---|
| 1 | Live gateway never advanced `Ledger.day`, so every request landed in one bucket | `Ledger(auto_day=True)` in live mode: day 1 starts at the first logged request, one day per 24 hours | `test_auto_day_counts_days_from_first_request` |
| 2 | A student HTTP error became a 500 | Student errors fall back to the teacher and log a `student_error` event (1st, 10th, 100th, 1000th). A teacher error returns **502** | `test_student_failure_falls_back_to_teacher`, `test_teacher_failure_returns_502` |
| 3 | Runtime-loaded LoRA adapters are lost when the endpoint restarts | New command `python -m ratchet.live reload` re-loads live, canary and shadow adapters | `test_reload_adapters_loads_live_canary_shadow` (in `test_live.py`, against a fake vLLM); on a real endpoint, see [RUNBOOK.md](RUNBOOK.md) |
| 4 | The untuned fallback model was judged with the tuned version's threshold | `Ratchet.threshold(version, model)` uses the global default for `RATCHET_FALLBACK_STUDENT` | `test_fallback_model_uses_default_threshold` |
| 5 | `alert` events repeated on every tick | One alert per live version | `test_live_alert_fires_once_per_version` |
| 6 | No schema migration for existing database files | `Ledger._migrate()` adds missing columns on open (`MIGRATIONS` table in `ledger.py`) | `test_old_database_file_is_migrated` |
| 7 | Stale docstring and column comment | Fixed | n/a |
| 8 | Unknown `X-Ratchet-Task` values were stored forever in `tasks.REGISTRY` | Unknown names get a temporary whole-text task; names must match `[A-Za-z0-9_.-]{1,64}` | `test_unknown_tasks_do_not_grow_registry` |
| 9 | `ReleasePolicy` could only be changed in code | `ReleasePolicy.from_env()` reads `RATCHET_<FIELD>` (e.g. `RATCHET_MIN_AUDITS`) | `test_policy_from_env` |
| 10 | Unsloth LoRA targets might miss Nemotron-H Mamba layers | `train.py --targets` (default adds `in_proj,out_proj`); confirm names on day 1 | The default: `test_train_parse_args_defaults_and_row_contract`; the module names: manual (GPU) |

Still open: `RATCHET_SYNC_CMD` and `RATCHET_TRAIN_CMD` run without a shell, so pipes and `&&` do not work; wrap them in a script.

## The pinned reference simulation

`tests/fixtures/sim_reference_seed7.json` holds one run of the simulator with every input pinned (seed 7, 10 days × 2,000 requests, drift day 5, corrupted v3, mock models; the same run as `make sim`): the inputs, each day's requests, student share, escalation rate, savings and audited agreement rounded to 4 decimal places, the ordered Night School and release events as (day, kind, version), and each version's final state, traffic and training-set size. `test_reference_simulation_unchanged` runs it once per session (the session-scoped `reference_run` fixture, about 7 s) and compares exactly. The inputs compared are the ones the fixture pins: a simulator flag added later, whose default keeps the old behaviour, does not force a regeneration, while a changed or removed pinned input fails the test.

It and the reference workloads below are the deliberate exceptions to "assert on trends, not numbers". Refactors and new features that claim to leave the legacy behaviour untouched prove it by keeping this test green. When a change is meant to move the simulation, run `python scripts/regen_sim_reference.py`, review the JSON diff (one day or event per line), commit it with the change and add a CHANGELOG line saying why; never edit the file by hand. `--check` compares without writing, with the same rule as the test.

The run unsets every `RATCHET_*` variable while it runs, so settings in your shell cannot move it, and it depends only on seeded `random.Random` streams, SHA-256-derived seeds and SQLite arithmetic. When the fixture was made it was identical on CPython 3.10, 3.11, 3.12 and 3.13 (Linux, the CI matrix) and under different `PYTHONHASHSEED` values. If it fails on one platform only, compare the first differing day before regenerating anything. The numbers are simulation output, not measurements.

### The reference workloads (v0.4.0's behaviour)

`scripts/reference_check.py` pins three workloads, captured from v0.4.0 (commit 1f9b18c) in `tests/fixtures/reference.json` ([ADR 0024](adr/0024-evidence-foundations.md)): `tickets-seed7` (the run above), `mixed-seed1` and `research-seed7` (8 days × 1,500 requests). It pins more than the reference simulation does: every input, every v0.4 key of each day's stats at full precision (`cost_per_1k`, `avg_latency_ms`, ...), the totals, each version's final state, training-set size and dataset hash, and the event log with its text. Every later feature defaults to v0.4's behaviour, so every change passes it: `make reference` (about 25 s) and a CI step run all three; the pytest suite checks the tickets workload against the session's reference run at no extra cost (`test_reference_workloads_reproduce_v040`).

The rules: floats agree within a relative 1e-9 (an index may add the same numbers in another order); days and totals may gain keys; events compare as (day, kind, version) in order, and each pinned text must be a prefix of the fresh one, so a feature may append evidence to a message but never change it; the advisory kinds later features add (`drift`, `teacher_drift`, `teacher_model_changed`, `warning`, `student_healed`) are left out; a flag added later is not compared, a changed default is. When a change is meant to move these runs: `python scripts/reference_check.py --write --captured-from "<release or commit>"`, review the diff, and add a CHANGELOG line that says why. The numbers are simulation output from mock models, not measurements.

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

- Pure logic goes in `test_core.py` with small hand-built ledgers (`Ledger(":memory:")`); Night School rules in `test_nightschool.py`; release-gate and router release behaviour in `test_release.py`. Set up states with the builders in `tests/fakes.py` (`_version`, `_audit`, `_shadow`, `_examples`) and fake models with `RecordingProvider`, `RecordingTrainer` and `RecordingEvaluator`, rather than new local helpers.
- A change to what clients or the dashboard see (a status code, a `/ratchet/stats` key, cost accounting) goes in `test_contract.py`. New stats keys also go in `tests/fixtures/stats_keys.json`; existing ones stay.
- Scout, the research world and the guard go in `test_scout.py`. Use the `gw` fixture (mock gateway, no warm-up) and fake clocks for the guard.
- Live mode, CLIs and deploy scripts go in `test_live.py`. Pass the seams ([Live-path tests](#live-path-tests)) instead of monkeypatching `httpx` or `subprocess`, and set every `Settings` value and environment variable the test depends on.
- Loop behaviour goes in `test_system.py` through `sim(...)` with fixed seeds. Assert on trends (rising share, falling escalations), not exact numbers, so tuning the mock does not break tests. The exception is the pinned reference run; reuse the `reference_run` fixture instead of starting another 10-day simulation.
- A new `Ledger.add_example` call under `ratchet/` needs a reviewed edit to `ALLOWED_ADD_EXAMPLE_CALLERS` in `test_nightschool.py`.
- Never call real APIs in tests. Use `httpx.MockTransport` as in `test_openai_compat_provider_parses_logprobs_and_usage`.
- Mark every new test with the requirement ids it proves (`@pytest.mark.req`), keep `parametrize` lists literal so the suite can be counted statically, and update the test counts above ([Traceability](#traceability)).
