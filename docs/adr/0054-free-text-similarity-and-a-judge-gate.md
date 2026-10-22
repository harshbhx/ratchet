# ADR 0054: Free-text tasks: a similarity comparator, per-task sample counts and a judge gate; long-form stays pinned

- Status: Accepted
- Date: 2026-10-21

## Context

Every comparator before this decision decides agreement by equality, up to a tolerance: two labels, two amounts, two dates. Free text breaks that. Two good one-paragraph summaries of the same ticket are rarely the same string, so under the default task (whole answers compared) or an `exact` field every pair of good student samples is a disagreement, the confidence score stays low, calibration serves nothing, and the student pays for samples it can never serve. The type name `similarity` was reserved in `compare.py` for this (ADR 0021).

A score fixes agreement but not correctness. "The refund was approved" and "the refund was not approved" are 0.74 apart under chrF: close in wording, opposite in meaning. Agreement with the teacher's answer under a score is a proxy, and a golden precision measured with it can be 100% for a student that gets the point wrong.

The night already had a judge for free text: `LiveEvaluator` asked `RATCHET_JUDGE_MODEL` about every free-text golden row and wrote `judge_pass_rate`. Nothing read it. It also judged a separate greedy student answer (temperature 0) on every row, calibration half included, which is not the answer production serves (three samples at 0.7, the most agreed one) and costs a student call per row.

## Decision

- **A `similarity` field type** (`ratchet/similarity.py`, standard library only), on `$` or a JSON field, so mixed specs work (`{category: label, summary: similarity}`). Three metrics, all from 0 to 1 and symmetric (the confidence score compares each pair of samples once):
  - `chrf` (default): character n-grams of 1 to 6 with whitespace removed, precision and recall averaged over the orders, F-beta with beta 2. chrF is not symmetric, so the score is the lower of its two directions. No tokenizer, so it works for any script.
  - `token_f1`: SQuAD's token F1 (punctuation and the English articles dropped, a bag of words).
  - `rouge_l`: ROUGE-L F1 over the longest common subsequence of word tokens, each answer capped at 512 tokens; the LCS is bit-parallel (Hyyro 2004), so 512 tokens cost a few hundred integer operations.

  Text is NFKC-normalised, case-folded and whitespace-collapsed first. Two answers agree when the longer is at most `max_len_ratio` (2.0) times the shorter, in characters, and the score reaches `threshold` (0.7); equal texts always agree. An empty string, or a value that is not a string, is invalid: no vote, never stored (INV-16). The normalised value is a `str` subclass (`SimilarityText`) that keeps each metric's features once built, so a sample compared with every other one builds them once, and a validity check alone (`Task.check` on a stored answer) builds none. Inputs are capped (8,192 characters for chrF, 4,096 tokens for token F1, 512 for ROUGE-L) because samples are compared on the request path.
- **Not inside `records`, not a set element.** Pairing records or set elements by a score would be quadratic in the records times the length of their text on every pair of samples. Free text gets a field of its own.
- **`policy.samples` of at least 2 for a similarity task, read per task.** With one sample, agreement is always 1, so the confidence says nothing. The task must say it itself (a `SpecError`, or `ValueError` from `Task`), so `RATCHET_SAMPLES=1` cannot take it away. The router (the shadow too) and the golden eval now read a task's `policy.samples` (`Task.sample_count`, else `RATCHET_SAMPLES`), so a task is calibrated on the sample count it is served with. No built-in task sets it, so nothing that existed moves.
- **The medoid is served.** Similarity is not transitive: A close to B and B close to C does not make A close to C. The pairwise score (`confidence.score`, unchanged since ADR 0021) already serves the sample with the most matches, then the highest token probability among those, which for a score is the medoid: never an answer at the edge of the group.
- **Precision is labelled.** Each per-task record carries `precision_kind`: `proxy` for a task with a similarity field, `exact` otherwise; the eval says `proxy` when such a task was scored. `/ratchet/stats` `tasks[]` repeats it with the task's `judge_pass_rate`, read defensively (anything unexpected is null).
- **The judge grades what production would serve.** `cascade_eval(judge=...)`: for free-text tasks (`Task.free_text`: no fields, or a similarity field; never labels, never pinned tasks) the held-out rows whose answer the student would serve at the threshold the eval measures coverage at (the task's own with per-task records, else the global one) are judged after calibration, with the answer chosen from the samples the eval already drew (`STUDENT_SAMPLING`) and cleaned as it would be served. The judge sees the request, the teacher's answer and the candidate in JSON mode and must answer exactly `{"score": 1}` to pass. The eval reports `judge_n` and `judge_pass_rate` overall and per free-text task. No extra student call is made, and rows that would escalate are not judged: nobody would get those answers.
- **A judge gate, off by default.** `ReleasePolicy.min_judge_pass` (`RATCHET_MIN_JUDGE_PASS`, 0 = off). In `admit`, after the precision, coverage and regression checks: an overall `judge_pass_rate` below it rejects the candidate; a free-text task with at least `min_task_test_n` held-out rows whose own rate is below it fails like a task below its precision floor, so `task_mode` decides (disabled on the candidate, or the candidate rejected). With the gate on, a free-text task the candidate would answer that no judge graded fails too: the operator asked for a judge's verdict, and shipping without one would make the setting silently do nothing. A rate over no answers has nothing to fail. The judge stays off the request path (ADR 0008): it runs at night, in the golden eval.
- **The mock judge knows the truth.** `MockFleet.judge` passes a candidate exactly when it is the stored reference (the mock teacher is always right), so the gate path is tested offline end to end: a candidate whose free-text answers keep the teacher's opening and change its closing sentence shows 100% proxy precision under chrF and fails the judge.
- **Long-form stays pinned.** The recommended setup, in CONFIGURATION.md: pin long-form work (summaries of long documents, translations of whole pages, chat replies, planning, long writing), distill short summaries and translations with a similarity field and `RATCHET_MIN_JUDGE_PASS`, and set `RATCHET_UNKNOWN_TASKS=teacher` so free text no operator registered is answered by the teacher and never stored. The use-case catalog that ships such templates is a later item.

## Consequences

- Short free-text tasks can be distilled: their samples can agree, so the student earns a confident score where its answers are close to each other, and the golden eval measures how close they are to the teacher's.
- Proxy precision is never presented as correctness: it is labelled in the eval, in each task's record and in `/ratchet/stats`, and the judge gate can hold a candidate to a verdict on meaning.
- The judge now grades about half as many rows as before (the served held-out answers, not every free-text row) and makes no student calls of its own. Its `judge_pass_rate` is no longer the old number: a candidate that escalates more is judged on fewer, surer answers, which is what a client would see. `judge_n` says how many.
- A judge call that fails still stops the night, as before: a night without its judge's verdict does not ship unjudged.
- A task's `policy.samples` now changes what the router draws and what the eval scores. An operator who set it in a tasks file before this release (documented then as read by a later release) gets it applied.
- The default stays off and no built-in task uses the new type, so the reference simulation and the three pinned workloads are byte-identical. The mock eval now reports `judge_n` and `judge_pass_rate` when golden rows of free-text tasks exist; nothing reads them unless the gate is on.

## Alternatives

- **BLEU or BERTScore.** BLEU is precision-only with a brevity penalty and is unstable on single sentences; BERTScore needs a model on the request path. chrF is robust on short texts and works without a tokenizer.
- **An LLM judge for agreement on every request.** Rejected again (ADR 0008, ADR 0021): a model call per comparison on the request path, and its bias in every confidence score.
- **One sample for free text, with token probability alone.** Small models are poorly calibrated; self-consistency is the signal Ratchet relies on.
- **Reject only on the overall judge rate.** A task sharing an adapter with easy free text would ship below the bar; per-task failures follow the same rule as per-task precision (ADR 0040).
- **Treat an unjudged candidate as passing when the gate is on.** A missing judge (an unset `RATCHET_JUDGE_MODEL`) would then turn the gate off without a word.
