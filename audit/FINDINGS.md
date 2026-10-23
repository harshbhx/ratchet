# Findings register

Inspected commit `3bfa5ab` on `claude/epic-mendel-c2kbrx` (Oct 22, 2026). Every finding below was reproduced by a script
or a failing test before it was fixed, and each fix has a regression test that fails without it (checked by stashing the
fix and rerunning). "Confirmed" means reproduced; "Likely" means read in the code but not reproduced; "Open" means not
fixed in this pass.

Severity is about this system as deployed for the hackathon (a public demo VM, live mode against Token Factory), not
about the prize. Four independent read-only audits (security, ML and statistics, privacy and provenance, API and
deployment) proposed findings; the lead reproduced each one before acting on it. Two audits found RAT-001
independently.

| ID | Sev. | Status | Title | Fix commit |
|---|---|---|---|---|
| RAT-001 | High | Fixed | Anytime-valid canary rule moves versions without compare-and-set and ignores the release freeze | `ea3fa26` |
| RAT-002 | High | Fixed | `/v1/completions` and `/v1/responses` ignore storage opt-outs, SDK retry counts and `Idempotency-Key` | `82a5d90` |
| RAT-003 | Medium | Fixed | `/v1/responses` `store: false` accepted and ignored | `82a5d90` |
| RAT-004 | Medium | Fixed | The signed certificate describes a serving set the router does not use (per-task thresholds) | `a510c48` |
| RAT-005 | Medium | Fixed | Per-task thresholds bypass `RATCHET_CALIBRATION=ltt` | `a510c48` |
| RAT-006 | Medium | Fixed | A teacher answer cut off after its status line is retried and billed up to 3x (9x behind an SDK) | `6afc5fb` |
| RAT-007 | Medium | Fixed | A lone UTF-16 surrogate in a teacher answer is a 500 after the teacher is billed | `6afc5fb` |
| RAT-008 | Medium | Fixed | Retention job releases the release gate's lock it never took | `ea3fa26` |
| RAT-009 | Medium | Fixed | Redactor misses identifiers grouped with Unicode spaces or dashes | `69f32e6` |
| RAT-010 | Medium | Fixed | Feedback corrections and imports ignore the redaction style and detector plugin | `a620b39` |
| RAT-011 | Medium | Fixed | A completions batch runs 16 cascades on one rate-limit token | `9e543ad` |
| RAT-012 | Medium | Fixed | Ledger replay drops `escalate_when` escalations and overstates coverage | `aad1842` |
| RAT-013 | Low-Med | Fixed | No-train and erasure endpoints unthrottled; one key can flood the release log | `9e543ad` |
| RAT-014 | Low-Med | Fixed | Live alert "once per version" holds only within one process | `aad1842` |
| RAT-015 | Low | Fixed | Caddy guard leaves admin-level routes open on a guarded mock demo | `d284bfd` |
| RAT-016 | Low | Fixed | Huge integers in upstream `usage` crash the provider; 1e300 tokens priced as a bill | `6afc5fb` |
| RAT-017 | Low | Fixed | Append-only triggers (INV-17) bypassed by `INSERT OR REPLACE` naming a rowid | `c07461d` |
| RAT-018 | Low | Fixed | A key's `tasks=` scope does not cover `/v1/embeddings` | `9e543ad` |
| RAT-019 | Medium | Fixed (UI branch) | Web code drifted from the Python: the story under-states the redactor; "Edit spec" drops `sql` and `privacy` on PUT | `78b1a31` (merged) |
| RAT-020 | Low | Fixed | A test expected `logit_bias` to reach the student, which ADR 0017 removed | `ea3fa26` |
| RAT-021 | High (submission) | Open | `origin/main` is an unrelated v0.3.0 history; all current work lives on a feature branch | needs the owner |
| RAT-022 | Medium | Open | Cross-task golden rule can pull a trained prompt back into golden (ML audit #4) | proposal below |
| RAT-023 | Low | Fixed | Public scrubbed dashboard still names API keys in `no_train`/`erase` events; anonymous request lookup returns the end-user HMAC | see REMEDIATION_LOG |
| RAT-024 | Low | Open (policy) | A client erasure tombstones a shared example for every tenant | proposal below |
| RAT-025 | Low | Open | `/v1/responses` errors for content parts name chat fields | proposal below |
| RAT-026 | Low | Likely, fixed | `deploy/train_job.sh` reports the adapter ready when the weights file appears, before the manifest | `59ab018` |
| RAT-027 | Low | Open | Gateway container runs as root; `--token` on the command line in `student_endpoint.sh` | proposal below |

---

### RAT-001: anytime-valid canary rule moves versions without compare-and-set and ignores the release freeze
- **Severity:** High. **Confidence:** Confirmed. **Criterion:** ML quality and release safety.
- **Where:** `ratchet/release.py` `_sequential_canary` (rollback, advance, promotion and futility all called `Ledger.set_version`).
- **Root cause:** ADR 0064 made every release move compare-and-set (`Ledger.transition`, `Ledger.promote_live`); the Wilson path was converted, the sequential path (`RATCHET_SEQUENTIAL=1`) was not. It also never read `releases_frozen`. `test_every_version_transition_writes_an_event` and the freeze tests ran the default policy only.
- **Scenario:** the live-run harness requires `RATCHET_SEQUENTIAL=1` (`RUN_PROFILE`). An operator rolls a canary back (admin API) while the gateway's or cron's tick is between its read and its write: the tick writes `canary 50%` over `rolled_back`, putting a version an operator removed back in front of half the traffic. A promotion retired the old live version and promoted the canary in two commits, with no `live_retired` event. A frozen release still advanced to 100%.
- **Reproduction:** `test_sequential_gate_never_revives_a_version_moved_under_it` (an admin rollback injected at the gate's first canary read; before: `('canary', 50)`, after: `('rolled_back', 0)`); `test_sequential_canary_moves_compare_and_set` (`set_version` patched to raise); `test_sequential_gate_holds_promotion_while_releases_are_frozen`.
- **Fix:** the same writers and event text as the Wilson rule; a `releases_frozen` check before advance and promotion; rollbacks and futility still run while frozen.
- **Verification:** 3 new tests fail before, pass after; `make reference` and the pinned reference simulation unchanged.

### RAT-002: completions and responses ignore storage opt-outs, retry counts and Idempotency-Key
- **Severity:** High (INV-22 is a privacy promise). **Confidence:** Confirmed.
- **Where:** `ratchet/gateway.py` `completions`, `responses`, `_legacy_complete`, `_respond` (before the fix they took neither header).
- **Root cause:** ADR 0050 says whatever the chat path gains "reaches them without another change", but these three headers are read in the chat route before `_serve`.
- **Scenario:** a client sends `X-Ratchet-Store: none` to `/v1/completions` or `/v1/responses`; its teacher answer is stored as training data. The SDK's retries (`x-stainless-retry-count`) each bump `seen`, so three attempts make an example FREQUENT and move a golden row into training (INV-3). Every retry re-runs and re-bills the cascade.
- **Reproduction:** `tests/test_privacy.py::test_every_chat_shaped_route_honours_the_storage_opt_out` (6 cases) and `test_sdk_retries_and_idempotency_keys_on_completions_and_responses`: 5 fail before the fix.
- **Fix:** both routes read the three headers and go through the idempotent wrapper, keyed by route so a key never replays a chat answer as a completion.

### RAT-003: `/v1/responses` `store: false` ignored
- **Severity:** Medium. **Confidence:** Confirmed. `openai_compat.RESPONSES_IGNORED` listed `store` with the comment "nothing is stored", which was false. Fixed with RAT-002: `store` maps to the chat request's `store`. ADR 0050 and API.md corrected.

### RAT-004: the signed certificate describes a serving set the router does not use
- **Severity:** Medium (it undermines the "certified precision" claim the BOM signs). **Confidence:** Confirmed.
- **Where:** `ratchet/nightschool/evaluate.py` `cascade_eval`: `certificate`, `per_task` and `test_vector` were built at the global threshold; the router serves each task at its own (`calibration.threshold`, on by default since ADR 0040).
- **Reproduction:** the ML audit's counterexample: signed certificate 200 served / 0 wrong, certified; what the router serves on the same held-out rows: 300 / 9 wrong, Clopper-Pearson lower bound 0.948 (exact Decimal computation), not certified. Regression test `test_the_certificate_counts_what_per_task_thresholds_serve`.
- **Fix:** with per-task records, each held-out row counts as served exactly when its task's threshold serves it (`certificate_serves: per_task`); the frontier stays a global what-if. `certificate.recheck` passes unchanged. ADR 0026 amended.

### RAT-005: per-task thresholds bypass Learn-then-Test
- **Severity:** Medium (opt-in `RATCHET_CALIBRATION=ltt`). **Confidence:** Confirmed.
- **Where:** `evaluate._task_threshold` always used the point rule.
- **Reproduction:** 30 agreeing calibration rows at confidence 0.68: the global LTT threshold is SERVE_NOTHING (P(Bin(30,0.95)=30) = 0.215 > 0.05), but the task's record said 0.68 and the router served it there. `test_learn_then_test_holds_for_each_tasks_threshold`.
- **Fix:** under `ltt`, each task's own threshold is `ltt_fixed_sequence` on its own calibration rows.

### RAT-006: a cut-off 2xx body is retried and billed again
- **Severity:** Medium (denial of wallet on a flaky network). **Confidence:** Confirmed.
- **Where:** `ratchet/resilience.py` `SAFE_TO_RETRY` includes `httpx.RemoteProtocolError`, which httpx also raises for "peer closed connection without sending complete message body" after a 200.
- **Reproduction:** `tests/test_resilience.py::test_a_cut_off_answer_is_never_paid_for_twice` (3 attempts before, 1 after; SDK retries made it 9).
- **Fix:** the provider streams the response, reads the body itself and raises `BodyCutOff` (502 `bad_upstream_response`, never retried); an unreadable 2xx answers the client `X-Should-Retry: false`. A `RemoteProtocolError` before the status line is still retried. ADR 0014 amended.

### RAT-007: lone surrogate in a teacher answer
- **Severity:** Medium. **Confidence:** Confirmed. `providers._content` passed `"\ud83d"` through; the ledger write (or the response) raised `UnicodeEncodeError` after the teacher was billed, a 500 the SDK retried twice, and the failures row recorded $0. Fixed by replacing unpaired surrogates with U+FFFD (`providers._utf8`). `test_an_unwritable_or_absurd_teacher_answer_never_crashes_after_it_was_paid`.

### RAT-008: the retention job releases the gate's lock
- **Severity:** Medium. **Confidence:** Confirmed (the baseline run logged `RuntimeError: release unlocked lock` from the `ratchet-retention` thread).
- **Where:** `ratchet/gateway.py` `Runtime._purge` ended with `return True` and `finally: self.gate_lock.release()`, lines left from `tick()`.
- **Scenario:** `threading.Lock` releases for any thread. Hourly in live mode with a retention window, a purge that ends while a request ticks the gate releases that request's lock: a second tick runs beside the first, and the first one's own release then raises in a request thread.
- **Reproduction:** `tests/test_hardening.py::test_retention_purge_never_releases_the_gate_lock`.

### RAT-009: redactor misses Unicode separators
- **Severity:** Medium (INV-12). **Confidence:** Confirmed: `Card 4111\xa01111\xa01111\xa01111` and `06\xa012\xa034\xa056\xa078` were stored with 0 redactions.
- **Fix:** patterns run on a same-length copy in which Unicode space separators and hyphen/dash/minus characters read as ASCII (`redact.SEPARATORS`); ASCII text is matched exactly as before (all v0.4 oracle tests pass); the BOM's `redactor` hash covers the table. Still not caught (documented in ADR 0082): zero-width characters inside an identifier, and e-mail addresses with non-ASCII parts.

### RAT-010: corrections and imports ignore the redaction style and plugin
- **Severity:** Medium. **Confidence:** Confirmed: with `RATCHET_REDACT_PLUGIN=redact_plugins:names`, the teacher's copy stored `[NAME]` while a correction stored `Priya Patel`; under `numbered` style the correction hashed apart from the teacher's answer, never replaced it, and the two copies could land in different splits.
- **Fix:** `feedback.prepare` takes the router's style and detector (`redaction_for`), records `canon_hash` for erasures, and returns `redaction failed` when the plugin fails. `test_a_correction_is_redacted_as_the_teachers_answer_is`.

### RAT-011 / RAT-013 / RAT-018: rate limits and scopes
- RAT-011: one rate token admitted a 16-prompt completions batch (16 teacher calls, 64 with `n=4`). Now the other prompts are charged to the same bucket as debt. `test_a_completions_batch_counts_each_prompt_against_the_rate`.
- RAT-013: `POST/DELETE /v1/ratchet/no-train` and `DELETE /v1/ratchet/examples` took no rate token: 2,500 calls in 9 s pushed every rollback and alert out of the dashboard's last 40 events. Now rate-limited and in-flight-capped (admin keys exempt). `test_no_train_and_erasure_writes_count_against_the_rate`.
- RAT-018: `acme:tasks=ticket-triage` could still call `/v1/embeddings`. Now a 403 unless the scope names `embeddings`. `test_a_keys_task_scope_covers_embeddings`.

### RAT-012: replay overstates coverage for escalate_when tasks
- **Severity:** Medium (the replay is advertised as exact). **Confidence:** Confirmed: true coverage 0.50, replay 1.00 with 50 rows excluded as `before_logging`.
- **Fix:** rule escalations log `label_p` 1.0 (as a confidence escalation does) and are never servable in the replay. `test_replay_counts_rule_escalations_as_unserved`.

### RAT-014: live alert repeats across processes
- **Severity:** Low-Medium. **Confidence:** Confirmed: 5 cron ticks plus the gateway wrote 6 alerts for one version.
- **Fix:** `Ledger.event_once`, which now also reaches the log listeners as `event` does (it did not, so per-task alerts never reached the JSON log either). `test_live_alert_fires_once_per_version_across_gates`.

### RAT-015: Caddy guard gap
- **Severity:** Low (mock demo only). **Confidence:** Confirmed in-app; Caddy itself was not run here (no binary). With `RATCHET_API_GUARD=guarded` and no admin keys, anyone could `PUT /ratchet/tasks/x`, `POST /ratchet/teacher/ack` or record "1,024 GPUs at $1,000 per GPU-hour" on the public cost report. A method-scoped matcher now covers every admin-level route; `test_caddy_guard_covers_every_paid_route` enumerates them from `ratchet/access.py`. Run `caddy validate` on the VM before relying on it.

### RAT-016, RAT-017
- RAT-016: `10**400` completion tokens raised `OverflowError` (500); `1e300` prompt tokens logged a cost of 4e294. Counts above 10^9 are now treated as unreported.
- RAT-017: `INSERT OR REPLACE ... (rowid, ...)` replaced a committed leaf or sealed receipt whatever its key. New `*_no_rowid_replace` triggers (created on open for old files).

### RAT-019: web drift (fixed on the UI branch)
At HEAD, `test_story_trust_claims_match_code` and `test_task_registry_contract` failed: the story page listed the redactor's rules without UPI and GSTIN (INV-21), and the dashboard's "Edit spec" copied a task's keys without `sql` and `privacy`, so saving an API task through the dashboard silently dropped its SQL fixture spec or its redaction style. CI on this branch was therefore red.

---

## Open items, with proposals

### RAT-021: the public `main` branch is not this code (submission blocker)
`origin/main` (`d080eb2`, "Docs for v0.3.0") shares no history with `claude/epic-mendel-c2kbrx` (130 commits, v0.4 and the unreleased v0.5 work). A judge opening the repository sees v0.3.0, and the story page's new source links (`blob/main/...`) point at files that do not exist there (`ratchet/sidecar.py`, `ratchet/drift.py`, ...). The owner must decide how to publish this branch as `main` (for example a pull request that replaces main, or making this branch the default); this pass did not push to `main`.

### RAT-022: cross-task golden rule leaks over time (ML audit #4, Confirmed by the auditor's script, not fixed)
`dataset.rows` applies the cross-task golden rule (ADR 0131) at read time, so a prompt trained on through task B moves back into golden when a copy arrives under task A whose hash is golden; the next night re-scores the live champion on rows it trained on (INV-4, INV-6). Proposal: never move a row whose key is in any version's committed `train` leaves back to golden (exclude it from both sets), or fix a group's split by its earliest row. It needs an ADR 0131 amendment, so it was left for the owner.

### RAT-023: scrubbed public dashboard leaks (fixed after the first report)
`access.scrub_event` pseudonymised `budget` events only; `no_train`, `erase`, `integrity` and `purge` details named the API key, and the anonymous request lookup returned `end_user` and `end_user_secondary` (HMACs, not raw ids). Now every client name in any event detail is replaced by its pseudonym (`access.pseudonymize`, word-bounded) and the two columns are dropped for scrubbed reads (`access.PRIVATE_COLUMNS`). Cases in `test_scrub_helpers`.

### RAT-024: one tenant's erasure tombstones a shared example for all tenants
With `RATCHET_ERASURE_SHARED=delete` (the default) and global tombstones (INV-24), client A can erase a throwaway end user who sent a predictable prompt first and so block client B's identical prompt from being stored for good. This follows from a documented policy; proposal: treat shared rows as `keep` for a client-initiated (non-admin) erasure.

### RAT-025 to RAT-027 (RAT-026 fixed)
- RAT-025: a Responses `input_image` part errors with `param: messages[0].content[1].type` and the chat part name. Wrap normalisation in `as_responses_error`.
- RAT-026 (Likely, fixed in `59ab018`): `deploy/train_job.sh` returned once `adapter_model.safetensors` existed; `train.py` writes the config, tokenizer, manifest and NOTICE.md after it. The script now waits for NOTICE.md, which `write_metadata` writes last (`test_train_job_waits_for_the_file_train_py_writes_last` ties the two). It still does not check the job's own status, so a failed job waits the full 2 h. Not reproduced on a GPU.
- RAT-027: the gateway `Dockerfile` has no `USER`; `student_endpoint.sh` passes `--token` on the command line (visible in `ps`). Low risk on a single-tenant VM.

## Areas checked and found sound (summary of the four audits)
- Route access: every API route of a private live gateway with no key, an API key and a dashboard key gave 401/403; demo routes are 403 in live mode; unknown routes default to the dashboard level; HEAD/OPTIONS give 405.
- Keys: constant-time digest comparison, duplicate and short keys refused, a key in two rings refused.
- Request validation: about 80 malformed and edge-case chat bodies (nulls, NaN, BOMs, 100k nesting, 5,000-digit ints, surrogates, n of 10^30) all gave a 400 in OpenAI's envelope; the real `openai` 3.36.1 SDK parsed every supported surface (chat, stream, `parse()`, tools, completions, responses, embeddings, models, `AzureOpenAI`).
- Statistics: binomial tails within 2.4e-12 relative error of exact Decimal sums; `cp_lower`, `required_rows`, McNemar and LTT validity; the e-value mixture is an exact martingale; the confidence sequence's time-uniform miss rate 0.01 against 0.05; the drift alarm's false-alarm rate 1.25% against 5% with heavy ties.
- Provenance: Merkle leaves and nodes are domain-separated; RFC 9162 inclusion matches the RFC; proofs are checked against the BOM-signed root; BOM signatures cover the canonical manifest; `closed_family` refuses case and vendor-prefix variants of closed models.
- Deployment: `bash -n` and shellcheck 0.11.0 clean on every `deploy/**/*.sh`; `create_vm.sh` dry run; `backup.sh` uses SQLite's online backup and `integrity_check`; port 8000 is not published; the training image pins match `train-versions.txt`.
