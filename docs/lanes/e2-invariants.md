# Lane E2: proposed invariants

Lane E2 (license notices and portability, feedback and labelled-data import) does not number invariants in `CLAUDE.md`,
because lanes build in parallel and the numbers would collide. Each proposal below is enforced and tested in this
branch; the integrator gives a new invariant the next free number in `CLAUDE.md`'s list (or folds an amendment into the
invariant it extends), adds its `CODE` entry in `scripts/reqtrace.py`, and adds the number to the proving tests'
`@pytest.mark.req` markers.

## Amendment to invariant 1: configuration cannot relabel a known closed teacher (LIC-NOTICES-PORTABILITY)

**Text (append to invariant 1 in CLAUDE.md).** Open teachers may also come from `RATCHET_TEACHER_LICENSES`
(`licenses.install`, merged into a copy of `BUILTIN` at startup), but only under a license in
`licenses.ALLOWED_LICENSES` (OpenMDW-1.1, Apache-2.0, MIT, NVIDIA Nemotron Open Model License) and for a model id no part
of which starts with a `CLOSED` prefix or `o` and a digit, contains `gpt`, `claude` or `gemini`, a closed model's name or
a closed vendor's name, and which is no router alias (`licenses.closed_family`, `licenses.CLOSED_NAMES`,
`licenses.CLOSED_VENDORS`, `licenses.ROUTER`); a configured entry never replaces a built-in one, and a bad file stops
startup. The check knows only the closed models it names: any other id is admitted on the operator's claim, which every
BOM and NOTICE marks `config`. Never widen the allowlist or loosen the closed-family check without a reviewed ADR. See
[ADR 0090](../adr/0090-licenses-travel-with-the-adapter.md).

**Enforcing code.**

- `ratchet.licenses:parse_entries` (the allowlist, the closed-family check, no built-in override, no duplicates)
- `ratchet.licenses:closed_family` (any part of the id: a closed prefix or `o` and a digit at its start, a family name,
  a closed model's name or a closed vendor's name anywhere in it, a router's name; a last path segment `auto`; stricter
  than `lookup`'s last-segment rule)
- `ratchet.licenses:lookup` (defence in depth: a configured entry naming a closed family resolves to the closed terms)
- `ratchet.licenses:install` (publishes `{**BUILTIN, **configured}` in one assignment; a bad file leaves the registry
  as it was) and its callers at startup: `ratchet.gateway:Runtime.reset`, `ratchet.router:Ratchet.__init__`,
  `ratchet.live:main`, `ratchet.admin:main`

**Proving tests** (`tests/test_licensing.py`, marked `INV-1`).

- `test_config_teacher_license_allowed_and_closed_prefix_still_refused`: `gpt-4o`, `azure-gpt-4o`, `chatgpt-4o-latest`,
  `openai/davinci-002`, `davinci-002`, `codex-mini-latest`, `o5-mini`, `anthropic/next-model`, `openai/gpt-oss-120b`,
  `claude-3-5-sonnet`, `google/gemini-2.5-pro` and others claimed as Apache-2.0 are refused, and so are router aliases
  (`openrouter/auto`); unknown or restrictive license names are refused; a built-in teacher cannot be relabelled; a
  closed id or router alias forced into the registry still raises `LicenseError`.
- `test_config_teacher_end_to_end`: without the file the configured teacher is unknown (refused at startup, its rows
  out of training).
- `test_bad_teacher_license_file_fails_at_startup`: every entry point stops on a bad file before it opens anything.

## Extension of invariant 14: the NOTICE is part of the signed BOM (LIC-NOTICES-PORTABILITY)

**Text (append to invariant 14 in CLAUDE.md).** A BOM written by `bom.manifest` carries the base model's license
(`base_model_license`) and the exact `NOTICE.md` text with its SHA-256 (`notices`). Whatever writes a NOTICE next to a
trained adapter from its BOM (live Night School's `publish_bom`, `python -m ratchet.live export`) writes the text the BOM
signed (`notices.bom_notice`), never a re-rendered one; only a BOM without `notices` (v1, or v2 written before NFR-9) gets
a NOTICE rendered from what it records.

**Enforcing code.** `ratchet.bom:manifest`, `ratchet.nightschool.notices:bom_notice`,
`ratchet.nightschool.notices:write_adapter_files`, `ratchet.live:publish_bom`, `ratchet.live:export`.

**Proving tests** (`tests/test_licensing.py`, marked `INV-14`).

- `test_bom_v2_has_base_model_license_and_v1_still_verifies`: the signature covers the NOTICE text (editing it fails
  `verify`); v1 and early v2 documents still verify and get a rendered NOTICE.
- `test_export_bundle`: the exported `NOTICE.md` is the BOM's text, replacing the training job's copy; a stored BOM
  that does not verify is refused.

## Amendment to invariant 2: human labels are targets, student outputs never (DATA-FEEDBACK-IMPORT)

**Text (already applied to invariant 2 in CLAUDE.md on this branch, because its old wording, "teacher answers only", is
no longer true here).** **Never train on student outputs.** Training targets are teacher answers or customer-attested
human labels (provenance `human`: feedback corrections and attested imports), never student outputs (avoids model
collapse). A verdict is never a label: a `correct` verdict stores nothing. See
[ADR 0091](../adr/0091-human-labels-as-a-provenance-source.md).

**Enforcing code.**

- `ratchet.feedback:submit` (a correction goes with `incorrect` only; a `correct` verdict writes the `feedback` row and
  nothing else) and `ratchet.gateway:_feedback_body` (the same rule at the edge, before anything is read)
- `ratchet.feedback:store_label`, the only caller of `Ledger.add_example` besides `Ratchet._store_example`
  (`ALLOWED_ADD_EXAMPLE_CALLERS` in `tests/test_nightschool.py`): its source is `licenses.HUMAN` or an open teacher that
  passes `licenses.require_distillable`
- `ratchet.nightschool.dataset:rows` (`licenses.lookup_source`: a teacher in the registry, or `human`; anything else is
  dropped)

**Proving tests** (marked `INV-2`).

- `tests/test_feedback.py::test_correct_verdict_never_creates_example`: a `correct` verdict on a student answer stores
  no example and calls no model; a `correct` verdict with the student's own text as `corrected` is a 400.
- `tests/test_feedback.py::test_corrected_feedback_stored_with_human_provenance_redacted_and_verified`
- `tests/test_nightschool.py::test_add_example_called_only_from_allowed_callers`

**Residual risk (not enforced).** A client can paste the student's served answer into `corrected` with an `incorrect`
verdict; it is stored as that client's human label, because the gateway keeps no digest of the served text to compare
with (ADR 0091, threat model T23). The invariant is about the system never turning student output into a target; this
is a client attesting a label, within T23's bounds (own requests only, one correction per request, the spec, the
opt-outs, `RATCHET_FEEDBACK_CORRECTIONS=0`).

## Amendment to invariant 16: the second writer of examples (DATA-FEEDBACK-IMPORT)

**Text (replace "`Ratchet._store_example` (the one caller of `Ledger.add_example`)" in invariant 16).**
`Ratchet._store_example` and `feedback.store_label` (human and imported labels, ADR 0091) are the only callers of
`Ledger.add_example`. `store_label` stores a label only when it is valid under the task's spec after
`tasks.clean_answer` (`Task.check`, also after redaction; `feedback.prepare`); a correction the spec refuses is a 400,
an imported row the spec refuses is counted as invalid. On an existing example a label replaces the stored one only
when it is stronger (teacher < a client's correction < an operator's import) or as strong and from the same writer,
decided and written in one write transaction (`Ledger.atomic`).

**Enforcing code.** `ratchet.feedback:prepare`, `ratchet.feedback:store_label`, `ratchet.ledger:Ledger.atomic`,
`ratchet.ledger:Ledger.add_example` (`replace`, `bump`).

**Proving tests.** `tests/test_feedback.py::test_feedback_body_is_checked` (an invalid correction is a 400 and records
nothing), `test_import_openai_finetune_jsonl` (invalid labels counted, not stored),
`test_label_precedence_human_teacher_import`, `test_store_label_is_atomic_across_processes`.

## Extension of invariant 3: golden-only rows (DATA-FEEDBACK-IMPORT)

**Text (append to invariant 3).** A row imported with `--golden-only` (`examples.golden_only`) is the one example whose
split is not its hash's: it is golden for good, never trained on even past `FREQUENT`, and so is every other variant of
its prompt (the same task and messages under other conditions), because the trainer sees only the messages. A
golden-only row is refused for a prompt that may already have been trained on (a variant in the train split, or seen
`FREQUENT` times), checked for that prompt inside the transaction that writes the row, so a human-verified golden set
is held-out evidence (invariant 6). Every other label, a human one included, keeps its hash's split. A label that is no
occurrence of its request (an import, or a correction of a request already counted) never counts toward `FREQUENT`: a
new example starts at `seen` 0 and a stored one keeps its count, so only real requests move a prompt to training.

**Enforcing code.** `ratchet.nightschool.dataset:rows` (prompts with a golden-only row are held out),
`ratchet.nightschool.dataset:effective_split`, `ratchet.feedback:held_out_ok`, `ratchet.ledger:Ledger.prompt_status`,
`ratchet.ledger:Ledger.add_example` (`golden_only`, sticky on a duplicate; `bump` False inserts `seen` 0),
`ratchet.ledger:seen_count` (0 counts 0 in `count_prompts`, `dataset.rows` and the export).

**Proving tests** (marked `INV-3`, `INV-6`). `tests/test_feedback.py::test_golden_only_rows_never_trained`: rows whose
hash says train stay golden, seen `FREQUENT + 2` times and under other conditions too; a prompt already in train or seen
`FREQUENT` times is refused (`trained`); a real night trains on none of them.
`test_golden_only_check_reads_the_ledger_at_each_row`: a train-split row stored while an import runs keeps that
golden-only row out. `test_labels_that_are_no_occurrence_never_reach_frequent`: an imported variant of a twice-seen
golden prompt, and a non-bumping correction, leave it golden; the third real request moves it.

## Extension of invariant 10: no human label for a pinned task (DATA-FEEDBACK-IMPORT)

**Text (append to invariant 10).** A pinned task takes no human or imported label either: `feedback.store_label`
refuses one (`not_stored: pinned`), and `python -m ratchet.importer` refuses a pinned task before it reads a row; a task
pinned after rows were imported keeps them out of Night School like any other rows.

**Enforcing code.** `ratchet.feedback:store_label`, `ratchet.feedback:_policy_reason`, `ratchet.importer:check_task`,
`ratchet.nightschool.dataset:rows`.

**Proving tests** (marked `INV-10`). `tests/test_feedback.py::test_import_refuses_pinned_task`,
`test_correction_storage_rules_still_apply`.

## Extension of invariant 1: `human` is never a teacher (DATA-FEEDBACK-IMPORT)

**Text (append to invariant 1).** Provenance that is not a model (`licenses.SOURCES`: `human`) is kept out of the
teacher registry: `require_distillable` refuses a `SOURCES` name in any case (`RATCHET_TEACHER_MODEL=human` stops
startup), a `RATCHET_TEACHER_LICENSES` entry may not use one, and readers of stored rows ask `licenses.lookup_source`.
An import of a teacher's outputs (`--source teacher:<model>`) passes `require_distillable` and the closed-family check
(`licenses.closed_family`, router aliases included), with the clause of the closed terms quoted.

**Enforcing code.** `ratchet.licenses:require_distillable`, `ratchet.licenses:is_source`,
`ratchet.licenses:lookup_source`, `ratchet.licenses:parse_entries`, `ratchet.importer:parse_source`.

**Proving tests** (marked `INV-1`). `tests/test_feedback.py::test_teacher_model_human_refused_at_startup`,
`test_import_refuses_closed_model_source`.

## New invariant: human verdicts never decide a release (DATA-FEEDBACK-IMPORT)

**Text (a new invariant, next free number).** **Human verdicts never promote or roll back a version.** Feedback
(`POST /v1/ratchet/feedback`) feeds each task's `human_agreement` and each version's human precision on the answers it
served itself, which `/ratchet/stats` reports and `feedback.check_human_alert` alerts on (one `human_alert` event per
version); `ReleaseGate` never reads the `feedback` table, because clients report what they notice and the sample is
biased. The audited precision stays the release signal. An API key gives verdicts on its own requests only; any other
request id is the same 404 as an unknown one.

The alert is anytime-valid: it fires when the mixture e-value against "precision >= live_floor" reaches
1 / `feedback.ALERT_ALPHA`, because it is checked after every verdict.

**Enforcing code.** `ratchet.feedback:check_human_alert` (writes an event and a meta row, nothing else),
`ratchet.feedback:submit` (`FeedbackNotFound` for another key's request), `ratchet.ledger:Ledger.human_precision`.

**Proving tests.** `tests/test_feedback.py::test_human_precision_alerts_and_never_moves_a_release` (marked
`FR-13.30`): verdicts below the floor write one `human_alert`, the version stays live and no `alert`, `rollback` or
`retired` event is written; `test_human_alert_is_anytime_valid` (the e-value rule, not a Wilson bound looked at after
every verdict); `test_feedback_only_on_own_requests` (marked `INV-13`).

## Notes for the integrator (DATA-FEEDBACK-IMPORT review fixes)

- **`examples.origin` on a retention restore.** `Ledger.add_example` now writes the caller's `origin` when a duplicate
  restores the text of a row retention purged (with that answer's label, teacher and license), where it used to keep
  the stored origin. From the router that is NULL in this lane; lane F's FR-16.8, which owns the column and passes an
  origin from the router, should keep the rule "the restored label's own origin" when the branches merge. The column's
  values now include `feedback` and `import` (`ledger.LABEL_ORIGINS`).
- **`examples.seen` may be 0.** A label stored with `bump=False` is inserted with `seen` 0 (router writes still insert 1,
  the column's default). Any reader added by another lane must count it with `ledger.seen_count` (or `seen` with NULL as
  1), not `seen or 1`.
- **`requests.end_user_secondary`.** A second end-user hash per request row, logged by the router from
  `RequestMeta.end_user_secondary`. Every place that clears or exports `requests.end_user` must treat it the same way
  (this lane: `Ledger.record_erasure`, `Ledger.requests_of_end_users`, `Ledger._export_requests`; the request fold
  deletes rows, so it needs nothing).
