# Text-to-SQL, round 2: the evidence

A second adapter, trained on more data and scored on the same exam as round 1 ([round 1](../text2sql-round1/README.md)).
Measured on 23 Oct 2026. Everything needed to recompute the numbers without a GPU is here or in round 1's `data/` folder.

## What was done

1. **More data.** The teacher (Nemotron 3 Ultra on Nebius Token Factory) wrote 3,507 new rows from new databases
   (`data/train_round2_new.jsonl.gz`, run record in `data/teacher_manifest_round2.json`, cost $9.18). Round 1's 821 rows plus
   the new ones, minus duplicates and 6 rows that shared a question or a query with the exam, give **4,322 training rows**
   (`data/merge_and_check.py` rebuilds the merged set from round 1's `train.jsonl` and the new file).
2. **Leakage check.** 0 of 172 exam prompts, questions, stored queries or table-name sets appear in the merged training data
   (`results/leakage_check_round2.txt`).
3. **Training.** The same recipe as round 1 (Nemotron-3-Nano-4B, 4-bit LoRA r=16, 2 epochs), with the sequence limit raised
   from 1024 to 1536 tokens so that no row is cut off. 1,080 steps in 5.2 hours on a **borrowed GPU**;
   loss 0.345 to 0.087 (`adapter/run_info.json`, `logs/final_status.json`). The job is `scripts/text2sql/modal_train.py`. The weights
   (40.5 MB) are not in git; their SHA-256 is in `MANIFEST.json`.
4. **Exam.** The adapter answered the same 172 questions (`answers/raw_r2.jsonl`, written by `scripts/text2sql/fast_generate.py`). An
   answer is right when running it returns the same rows as the stored reference query on the same database.
5. **Scoring.** `scripts/measure_adapter.py report` wrote `results/results_r2.json`; `verify` recomputes every number from the
   answers (`results/verify_output_r2.txt`). The base model's and the teacher's answers are round 1's
   (`../text2sql-round1/answers/`); the base model does not change between rounds.

## The numbers (all 172 rows)

| | Match | Rate | 95% interval |
|---|---|---|---|
| Base model | 22 / 172 | 12.8% | 8.6 to 18.6 |
| Teacher answering the exam prompt | 44 / 172 | 25.6% | 19.6 to 32.6 |
| Adapter, round 1 (821 rows) | 57 / 172 | 33.1% | 26.5 to 40.5 |
| **Adapter, round 2 (4,322 rows)** | **67 / 172** | **39.0%** | **32.0 to 46.4** |

- Row by row against round 1: 22 questions went from wrong to right, 12 from right to wrong, 45 stayed right. An exact two-sided test on those
  34 changes gives p = 0.12, so the gain may be partly chance.
- By industry: seen in training 44 / 115 (round 1: 34); never seen 23 / 57 (round 1: 23). The gain is in familiar industries.
- By difficulty: simple 16 / 51, join 26 / 53, aggregate 18 / 36, hard 7 / 32.
- Measurement half (86 rows): adapter 34 / 86 = 39.5% (29.9 to 50.1), base 13 / 86. At the 95% precision target the confidence gate passes
  1 of 86 rows (threshold 0.9914), saving about 1% of the cost at that coverage: the adapter does not yet replace the teacher.

## Check it yourself

```bash
python scripts/measure_adapter.py verify \
  --raw evidence/text2sql-round2/answers/raw_r2.jsonl \
  --results evidence/text2sql-round2/results/results_r2.json \
  --golden evidence/text2sql-round1/data/golden.jsonl \
  --dbs evidence/text2sql-round1/data/dbs.json
```

It prints `OK: every number in results.json was recomputed from raw.jsonl and matches`, or a `FAIL:` line.

## Limits

- Small adapter, synthetic databases and questions, one greedy answer per question.
- One reference query per question, so a correct query with other columns counts as a miss; that is why even the teacher scores 25.6%.
- The exam is small: a 7-point half-width. The point estimate is above the teacher's, but the exam's reference queries are the teacher's own, so we do
  not claim the adapter beats the teacher.
- Not kept: the full raw API responses of the teacher calls (only the text and token totals), and the GPU job's container logs (the job's own progress
  record, `logs/final_status.json`, is kept).
