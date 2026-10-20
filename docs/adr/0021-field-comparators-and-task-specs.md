# ADR 0021: Field comparators and task specs

- Status: Accepted
- Date: 2026-10-20

## Context

A task decides when the student agrees with the teacher. Until v0.5 a task listed `compare_keys`, and two answers agreed when those top-level JSON values were equal after strings were stripped and lower-cased (`Task.canonical`). That is right for short labels and wrong for most other fields a business extracts:

- **Numbers.** `1` and `1.0` disagreed, and so did `"1,234.50"` and `1234.5`. An invoice total written two ways was two answers.
- **Lists.** A list of tags in another order was another answer.
- **Missing fields.** `{}` was a valid object, every key was None, and None equals None. Three `{}` samples from a broken student scored 0.98 and were served (a strict xfail in `tests/test_contract.py` pinned the bug). A teacher answer without the fields was stored as a training target.
- **Vocabularies.** Nothing checked a label against the list the prompt offered, so a teacher answer such as `"billing inquiry"` became a training target and a golden reference no student answer would match.
- **Free text by default.** A task without keys compared whole texts, so two JSON answers that differed only in key order or spacing disagreed.

Agreement drives everything: the confidence score, audits, the shadow's verdict, the golden eval's precision. A comparator that is too strict escalates good answers and lowers measured precision; one that is too loose (the `{}` case) serves wrong ones.

## Decision

- **Typed fields.** A task's `fields` is a tuple of `compare.FieldSpec`: a path (`$` for the whole answer, dot paths into nested objects) and a type, each with a normaliser that runs once per answer and a matcher over two normalised values. Types: `exact` (v0.4's rule), `label`, `bool`, `number`, `ordinal`, `text`, `date`, `language`, `set`, `records` and `ranking`; the options are in [CONFIGURATION.md](../CONFIGURATION.md#tasks). `similarity` and `tool_calls` are reserved names for later releases: a spec that uses them is refused, so no task silently compares free text or tool calls with the wrong rule. Formats `tool_calls` and `sql` are reserved the same way.
- **Required by default.** A field is required unless it says `required=False`, and null only when `nullable`. An answer with a required field missing, a null where none is allowed, or a value that does not normalise (a label outside `values`, a number that is not one) is invalid: `Task.key` returns `compare.INVALID`. One rule, so the student and the teacher are held to the same spec:
  - an invalid student sample gets no vote, and a sample set with no valid answer scores 0 and is never served;
  - an invalid teacher answer is served but not stored (`skip_example` with the reason `invalid field <path>`, which names the spec's path and never quotes the answer), is not an audit, and settles no shadow disagreement (invariant 16);
  - an invalid golden reference is skipped by `cascade_eval` before the student is sampled (`golden_invalid_refs`), and a stored train row that the current spec refuses is left out of training (`skipped_invalid` in the dataset manifest). The spec may tighten after answers were stored; the ledger keeps them, so loosening the spec again brings them back.
- **Pairwise confidence.** `confidence.score` computes each sample's key once, counts for each valid sample the valid samples it matches (at most n² = 9 matches, no I/O), takes the first sample with the most votes, and serves the member with the highest token probability among the members with that many votes. For transitive comparators (equality, as in every built-in task) this is exactly the old count of equal canonical answers with `Counter.most_common`'s tie-break, so every number stays the same. With a tolerance, agreement is not transitive (0, 0.6 and 1.2 under `abs_tol=1`: the ends disagree), and the served answer is the one that agrees with every member, never an edge.
- **Vocabularies in core.** The built-in tasks' labels are required and checked against `ratchet/vocab.py`. The prompts that offer them (`sim/tickets.py`, `scout/prompts.py`) import the same lists, so a prompt cannot offer a label its task refuses; `ratchet/` does not import `sim/`.
- **A JSON-aware default.** A task without fields (`default`, and any unknown task name) compares canonical JSON (sorted keys, strings NFKC-normalised, case-folded and stripped, numbers as floats) when both answers are a JSON object or array, and otherwise the v0.4 text rule.
- **Backwards compatible.** `compare_keys` stays as a constructor shortcut: each key becomes an optional `exact` field, and `Task.canonical` gives v0.4's string byte for byte (`test_legacy_compare_keys_canonical_byte_identical`).
- **Specs fail when defined.** Bad options raise `ValueError` in `FieldSpec` or `Task`, so a broken spec stops the gateway at startup instead of failing on the request path. Limits: 64 fields per task (counting fields inside records), records nested at most 4 deep, at most 2,000 values, aliases or order entries per field, at most 200 records per answer at any depth, at most 2,000 elements per set or ranking. Numbers, dates and languages longer than 100 characters are not values.
- **A hash of the rules.** `Task.spec_hash` (16 hex digits of SHA-256 over `Task.spec()`, without the name or `source`) is derived and cannot be set, so it never drifts from the rules; later releases record it with a version's evaluation to notice a changed task. `Task` and `FieldSpec` are frozen.
- **Stdlib only, linear.** Every sample on the request path is normalised, so each normaliser is linear in its value (no regex that rescans from every position), month names are read without the locale-dependent `strptime`, and records pair by key, by position or greedily within the 200-record budget. Scoring three ticket answers costs about 50 µs.

## Consequences

- PRD FR-2.14 (new), FR-2.2 and FR-2.6 extended; invariant 16 now covers the task's spec.
- The mock models write only valid answers with labels from the vocabularies, and the built-in comparators decide exactly as before on them (`test_builtin_task_agreement_decisions_unchanged`), so the pinned reference simulation is identical.
- A live teacher that answers outside the vocabulary (`"Billing Inquiry"`) now produces fewer examples, and the `skip_example` events name the field. Fix the prompt, or add the spelling as an alias, rather than loosening the field.
- Answers stored under an older, looser spec stay in the ledger, but Night School neither trains nor grades with them while the spec refuses them. The manifest and the eval count them.
- `policy` (`samples`, `audit_rate`, `target_precision`, `min_precision`), `routing` and `tools` are part of the spec and its hash, validated but not read yet; per-task calibration, routing rules and tool distillation will read them. `store=False` keeps a task's teacher answers out of the training set already.
- A set compares its elements by their normalised value only; an element type with a tolerance is refused (use `records` for that).

## Alternatives considered

- **Keep exact top-level equality and fix numbers only.** Smaller, but leaves `{}` agreeing with itself, lists order-sensitive and vocabularies unchecked, and every later use case (invoices, dates, entity lists, rankings) would need its own patch.
- **An LLM judge for agreement.** Handles anything, but costs a model call per comparison on the request path, adds judge bias to labels, and is not reproducible. The judge stays for free-text tasks in the nightly eval ([ADR 0003](0003-per-request-cascade-with-calibrated-confidence.md)).
- **JSON Schema for validity.** Says what is well formed, not when two answers agree (tolerances, aliases, set overlap). A schema library would also be a dependency on the request path. Structured outputs can derive field specs from a schema later.
- **Majority by canonical string, then tolerance on the winner.** Keeps `Counter`, but groups 0.6 and 1.2 apart and misses the group the tolerance means; pairwise votes cost nine comparisons.
- **Pick the best member by token probability alone (the spec's first reading).** Serves an edge of a non-transitive group (0 or 1.2) that disagrees with another member; restricting the pick to the members with the top vote count serves the sample every member agrees with, and changes nothing for transitive comparators.
