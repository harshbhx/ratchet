# Testing

```bash
pip install -e ".[dev]"
pytest -q                     # 34 tests (test_core, test_system, test_hardening), ~10 s
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3
```

There are three layers:

1. **Unit tests** (`tests/test_core.py`): the pure logic of each module.
2. **System tests** (`tests/test_system.py`): the whole loop over simulated days, plus the gateway API and the HTTP client.
3. **Simulation** (`sim/`): a reproducible offline world with a mock teacher, mock students that learn, synthetic traffic, drift and fault injection. It is used for demos, and to find design bugs before spending on GPUs.

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
| `test_gateway_openai_shape_and_demo_flow` | Mock gateway: dashboard returns 200; `/ratchet/demo/day` works; `/v1/chat/completions` returns a valid `chat.completion` with the right category; stats count 401 requests; break-next and reset work |
| `test_gateway_rejects_bad_requests` | Empty `messages` and `stream: true` both return 400 |
| `test_openai_compat_provider_parses_logprobs_and_usage` | `OpenAICompatProvider` against an `httpx.MockTransport`: sends `logprobs` and `n`, sends the bearer header, parses logprobs per choice, splits completion tokens across choices, computes `mean_prob` |

## Simulation methodology

`sim/simulate.py` runs `--days` days. Each day sends `--requests` tickets through `Ratchet.handle()`, calls `ReleaseGate.tick()` ten times, then runs `run_night()`. It prints a day table and the event log, and writes JSON (`--out`, default `sim_results.json`).

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
| `--seed` | 7 | Seed for traffic and routing |
| `--out` | `sim_results.json` | Results file |

### Assumptions (these drive the numbers; they are not measurements)

**Traffic** (`sim/tickets.py`):

- 20 templates across 6 categories (billing, technical, account, shipping, refund, security) and 4 priorities. 6 of them are marked `hard`.
- Names, products and order numbers vary, so some templates produce many distinct texts and others very few (frequent repeats).
- From `--drift-day`, 20% of traffic comes from 3 new "Ratchet Watch" templates the student has never seen (1 of them hard).

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

**Fault injection:**

- The corrupted version is fine on the staging copy, which golden eval and shadow use.
- Once it reaches canary or live, its billing, refund and security answers become confidently wrong. This models pushing the wrong adapter file to production.

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

## Adding tests

- Pure logic goes in `test_core.py` with small hand-built ledgers (`Ledger(":memory:")`). Use `_version()` and `_audit()` to set up release states.
- Loop behaviour goes in `test_system.py` through `sim(...)` with fixed seeds. Assert on trends (rising share, falling escalations), not exact numbers, so tuning the mock does not break tests.
- Never call real APIs in tests. Use `httpx.MockTransport` as in `test_openai_compat_provider_parses_logprobs_and_usage`.
