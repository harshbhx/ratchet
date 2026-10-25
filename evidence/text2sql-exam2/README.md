# Text-to-SQL, exam 2: the evidence

A second exam, written by a different model than the training labels, answered by the base model, the round 1 adapter, the round 2
adapter and the teacher. Measured on 23 to 24 Oct 2026. Everything needed to recompute the numbers without a GPU is in this folder.

## What was done

1. **A different author.** Claude Sonnet 5.5 designed 10 small SQLite databases in industries that appear neither in the training data
   nor in exam 1 (museum collections, veterinary clinic, wine distribution, film production schedule, wind farm maintenance, court case
   management, recycling plant operations, podcast network, cinema chain, fishing fleet) and wrote 20 questions for each with the query that
   answers it. The mix is 60 simple, 60 join, 40 aggregate and 40 hard, as in exam 1. The generator is `scripts/text2sql/gen_exam2.py`; Claude's
   raw responses are in `data/claude_raw/`. The exam is evaluation only: nothing was trained on it, and no closed model's output was used for
   training anywhere in this project.
2. **Checked by running.** Every reference query was run in the sandbox and kept only if it was one read-only SELECT returning 1 to 100 rows.
   All 200 passed. No row shares a question, a query or a set of table names with the training data (`data/exam_manifest.json`).
3. **Frozen before scoring.** `data/golden.jsonl` has SHA-256 `b7634971cf6b0d79e64e30e4c16d1253fad65ab9a501a06edc6c9f8e70d69058`. The hash was recorded in
   `data/exam_manifest.json` when the exam was generated, before any model answered it.
4. **Answered.** Nemotron 3 Ultra (the teacher) answered the same prompts on Token Factory at temperature 0, thinking off
   (`answers/teacher2.jsonl`, `scripts/text2sql/teacher_selfcheck.py`, about $0.11). The base model and each adapter answered on a borrowed GPU
   (`answers/raw_v1.jsonl` with the round 1 adapter, `answers/raw_v2.jsonl` with the round 2 adapter, written by
   `scripts/text2sql/fast_generate.py`). The base model gave identical answers in both jobs, 200 of 200 rows.
5. **Scored.** `scripts/measure_adapter.py report` wrote `results/results_v1.json` and `results/results_v2.json`; `verify` recomputes every
   number from the answers (`results/verify_output_v1.txt`, `results/verify_output_v2.txt`). An answer is right when running it returns the same
   rows as the reference query on the same database.

## The numbers (all 200 rows, strict)

| | Match | Rate | 95% interval |
|---|---|---|---|
| Base model | 25 / 200 | 12.5% | 8.6 to 17.8 |
| Teacher answering the exam prompt | 30 / 200 | 15.0% | 10.7 to 20.6 |
| Adapter, round 1 (821 rows) | 56 / 200 | 28.0% | 22.2 to 34.6 |
| **Adapter, round 2 (4,322 rows)** | **66 / 200** | **33.0%** | **26.9 to 39.8** |

- Round 2 against round 1, row by row: 23 questions went from wrong to right, 13 from right to wrong. An exact two-sided test on those 36 changes
  gives p = 0.13. Exam 1 gave 22 against 12 (p = 0.12). The direction is the same on both exams; neither alone settles it.
- By difficulty, base / round 1 / round 2: simple 15 / 19 / 16 of 60, join 3 / 5 / 12 of 60, aggregate 5 / 22 / 24 of 40, hard 2 / 10 / 14 of 40.
- At the 95% precision target the confidence gate passes none of the 100 measurement-half questions for either adapter (threshold above 1), so
  the adapter does not replace the teacher.
- The teacher's low strict score is mostly shape: of its 200 answers, 102 return the right number of rows with a different number of columns, 48
  return the same number of rows with different values, 20 a different number of rows, and none fails to run. The adapter's lead over the teacher
  on this strict exam is therefore largely a matter of matching the reference's columns, and we do not claim it beats the teacher.

## Both exams

| | Exam 1 (172 rows, written by Ultra) | Exam 2 (200 rows, written by Claude) |
|---|---|---|
| Base model | 22 = 12.8% | 25 = 12.5% |
| Teacher | 44 = 25.6% | 30 = 15.0% |
| Adapter, round 1 | 57 = 33.1% | 56 = 28.0% |
| Adapter, round 2 | 67 = 39.0% | 66 = 33.0% |

## Check it yourself

```bash
python scripts/measure_adapter.py verify \
  --raw evidence/text2sql-exam2/answers/raw_v2.jsonl \
  --results evidence/text2sql-exam2/results/results_v2.json \
  --golden evidence/text2sql-exam2/data/golden.jsonl \
  --dbs evidence/text2sql-exam2/data/dbs.json
```

It prints `OK: every number in results.json was recomputed from raw.jsonl and matches`, or a `FAIL:` line. The same works for `raw_v1.jsonl` and
`results_v1.json`. `MANIFEST.json` has a SHA-256 for every file.

## Limits

- A strict exam with one reference query per question: a correct query with other columns counts as a miss.
- Written by one model; a different author would give somewhat different questions. Its numbers sit about 5 to 6 points below exam 1 for the adapters,
  as expected on an exam from a new author.
- Small adapters, synthetic databases and questions, one greedy answer per question.
- Not kept: the GPU job's container logs. The answers and the scores are the evidence.
