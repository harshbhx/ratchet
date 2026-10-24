# Text-to-SQL, round 1: the evidence

Everything needed to recompute the adapter's numbers without a GPU. Measured on 22 to 23 Oct 2026.

## What was done

1. **Data (22 Oct).** The teacher (Nemotron 3 Ultra on Nebius Token Factory) wrote small SQLite databases, then questions with a
   reference query for each. Every query was run in a sandbox and kept only if it ran and returned rows
   (`data/teacher_manifest.json`; teacher cost $2.87). 821 rows trained the adapter (`data/train.jsonl`). 172 rows are the exam
   (`data/golden.jsonl`, with the databases in `data/dbs.json`); 57 rows (33 percent) come from two industries (airline operations, pharmacy inventory) held out of training.
2. **Training.** Nemotron-3-Nano-4B, 4-bit, LoRA r=16, on a **free Colab T4 (not on Nebius GPUs)**: 206 steps, 66 minutes, training loss
   0.334 to 0.075 (`logs/training_log.txt`, `adapter/run_info.json`). The weights (40.5 MB) are not in git; their SHA-256 is in
   `MANIFEST.json`, so any copy of the weights can be matched to this run.
3. **Exam.** The base model and the adapter each answer all 172 questions (`answers/raw.jsonl`, written by
   `scripts/text2sql/fast_generate.py`). An answer is right when running it returns the same rows as the reference query on the same
   database. Calibration uses the even rows and measurement the odd rows.
4. **Scoring.** `scripts/measure_adapter.py report` writes `results/results.json`. `verify` recomputes every number from the answers.
5. **Yardstick.** The teacher answered the same exam prompts a second time (`answers/teacher2.jsonl`, `scripts/text2sql/teacher_selfcheck.py`),
   scored the same way. The reference was written together with the question, so this is not the same call twice.
6. **Prompt v2.** The same questions with the line "Select only the columns the question asks for." added to the end, for both models
   (`answers/raw_v2.jsonl`, `results/results_v2.json`).

## The numbers (all 172 rows; same rows as the reference query)

| | Match | Rate | 95% interval |
|---|---|---|---|
| Base model | 22 / 172 | 12.8% | 8.6 to 18.6 |
| Teacher answering the exam prompt | 44 / 172 | 25.6% | 19.6 to 32.6 |
| **Adapter** | **57 / 172** | **33.1%** | **26.5 to 40.5** |
| Base model, prompt v2 | 19 / 172 | 11.0% | 7.2 to 16.6 |
| Adapter, prompt v2 | 59 / 172 | 34.3% | 27.6 to 41.7 |

Measurement half (86 rows, the headline view of `results.json`): adapter 28 / 86 = 32.6% (23.6 to 43.0), base 13 / 86 = 15.1%.
Confidence gate at the 95 percent precision target: 4 of 86 rows pass, 2 are right; cost saved at that coverage 3.5 percent.
The adapter is about three times better than the base model and in the same range as the teacher (the intervals overlap, so this
does not show it is better than the teacher). It does not yet replace the teacher at 95 percent precision.

## Check it yourself

```bash
python scripts/measure_adapter.py verify \
  --raw evidence/text2sql-round1/answers/raw.jsonl \
  --results evidence/text2sql-round1/results/results.json \
  --golden evidence/text2sql-round1/data/golden.jsonl \
  --dbs evidence/text2sql-round1/data/dbs.json
```

It prints `OK: every number in results.json was recomputed from raw.jsonl and matches`, or a `FAIL:` line. The same works for
`raw_v2.jsonl` and `results_v2.json`. `MANIFEST.json` has a SHA-256 for every file. `results/leakage_check.txt` shows that no exam
prompt, question, reference query or table-name set appears in the training data.

## Limits

- Small adapter (821 rows, 2 epochs, 4-bit, free T4); the databases and questions are synthetic.
- One reference query per question: a correct query with other columns or another sort order counts as a miss. That is why even the
  teacher scores 25.6 percent.
- One greedy answer per question; sampling and voting were not tried.
- Not kept: the full raw API responses of the first teacher calls (only the text and token totals), and a first, slow exam run
  that Colab interrupted.
