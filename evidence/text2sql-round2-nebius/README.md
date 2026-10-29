# Text-to-SQL, round 2 retrained on Nebius AI Cloud: the evidence

The round-2 recipe, run again as a Nebius Serverless Job on one **preemptible L40S**, then scored on the same two exams
with the same scoring code. Run on 27 Oct 2026. The published round-2 numbers (39.0% on exam 1, 33.0% on exam 2) come from the
adapter trained on a borrowed GPU ([round 2](../text2sql-round2/README.md)) and stay as they are. This is a second, independent
training of the same recipe, not a replacement and not the origin of those adapters.

## Result

The round-2 recipe was retrained on Nebius AI Cloud (one preemptible L40S, Serverless Job) on 27 Oct 2026, from the same 4,322 examples:
**exam 1 70 / 172 = 40.7%, exam 2 69 / 200 = 34.5%** (original run on a borrowed GPU: 67 / 172 = 39.0% and 66 / 200 = 33.0%).

| | Original round 2 (borrowed T4) | Retrained on Nebius (L40S) |
|---|---|---|
| Exam 1 (172 rows) | 67 = 39.0% | **70 = 40.7%** (95% interval 33.6 to 48.2) |
| Exam 2 (200 rows) | 66 = 33.0% | **69 = 34.5%** (95% interval 28.3 to 41.3) |

- Row by row against the original adapter: exam 1, 6 questions went from wrong to right and 3 from right to wrong (exact two-sided p = 0.51);
  exam 2, 7 and 4 (p = 0.55). The two adapters are statistically indistinguishable. We read the small difference as run-to-run noise (a different
  GPU, different kernels, one resumed training run), not as an improvement.
- The two adapters write the same answer text on 130 of 172 (exam 1) and 144 of 200 (exam 2) questions.
- By difficulty (Nebius / original): exam 1 simple 17 / 16 of 51, join 25 / 26 of 53, aggregate 21 / 18 of 36, hard 7 / 7 of 32;
  exam 2 simple 17 / 16 of 60, join 13 / 12 of 60, aggregate 25 / 24 of 40, hard 14 / 14 of 40. Exam 1 by industry: seen in training 47 / 44 of 115, never seen 23 / 23 of 57.
- Measurement half and confidence gate, as in round 2: exam 1, 34 / 86 = 39.5%; at the 95% precision target the gate lets 3 of 86 rows through
  (threshold 0.9878) and 1 of those 3 is right. Exam 2, 28 / 100 = 28.0%; the gate lets none of the 100 rows through. The adapter does not replace the teacher.
- Every number above is in `results/results_nebius.json`, written by `score_nebius.py`; none is typed in.

## What ran

| | |
|---|---|
| Where | Nebius AI Cloud, Serverless Job (`nebius ai job create`), eu-north1, a bucket mounted at `/data` |
| GPU | one NVIDIA L40S (46 GB), platform `gpu-l40s-a`, preset `1gpu-8vcpu-32gb`, **preemptible** (the platform can take it back, and did twice) |
| Image | public `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-devel` (Python 3.12.3). No custom image, no registry push |
| Packages | the round-2 pins, checked against the saved list in `logs/pip-freeze.txt`: torch 2.11.0+cu128, transformers 5.20.1, trl 1.16.0, peft 0.21.2, bitsandbytes 0.50.2, causal-conv1d 1.6.2.post1, mamba-ssm 2.3.2.post1 |
| Recipe | unchanged: `scripts/text2sql/train_lora.py` with `MODE=real DEFER_GEN=1 EPOCHS=2 MAX_SEQ=1536`; 4-bit QLoRA, rank 16, alpha 32, q/k/v/o/up/down projections, batch 2 x 4 accumulation, learning rate 2e-4, 1,082 steps |
| Data | the same 4,322 rows, rebuilt with round 2's `data/merge_and_check.py` (`evidence/text2sql-round2/data`); the uploaded `train.jsonl` has SHA-256 `f8cc8318b567e41931bac8dd098a32116fbcb4c6c4a4e4814bebdfdfb78e1d68` (LF line endings) |
| Scripts | `train_lora.py` SHA-256 `0fea1900ed51712752991aea383d47558ceda5fde79566fd699727215208fa83`, `fast_generate.py` `86f8fb190a998636a90bffded5fcc41f81256a5129fc0b67f9450a05254f09d5`, both as in the repository |
| Exams | exam 1 `golden.jsonl` (`6b9c1db4...2864`) and exam 2 `golden.jsonl` (`b7634971...9058`), answered by `fast_generate.py` unchanged, in a separate process from training |
| Runner | `data/run.sh` (what the job executed: environment, kernels, training, both exams, copy to the bucket) |

**Differences from round 2, all in where it ran:** an L40S instead of a T4; the Mamba kernels compiled for sm_89 (the L40S) instead of sm_75 (the T4), 23.5 minutes
on the job's 8 CPU cores, then cached in the bucket; the Python packages installed into a virtual environment (the image's Python blocks system-wide pip);
checkpoints written to the machine's disk and copied to the bucket every few minutes, so a taken-back machine can resume. No hyperparameter, data row or script was changed.

**The training was interrupted, and the final adapter is from a resumed run.** Attempts, in order (`logs/attempts.json`, one row per Nebius job):
two jobs started on machines but a line-ending bug in my runner stopped them at once; one was cancelled when pip refused to install; a smoke job (3 steps, 3 exam rows) passed;
a full job was taken back by the platform before its first step; another trained at 2.9 seconds per step until its first checkpoint save failed on the bucket mount (fixed, cancelled);
another was taken back after step 625; the last one restored checkpoint 550 (weights, optimizer and scheduler state) and finished steps 551 to 1082, saved the adapter, then answered both exams.
Loss went from 0.346 to 0.088 (round 2: 0.345 to 0.087). `adapter/run_info.json` reports `train_seconds` for the last attempt only (729 s).

## Time and cost (the whole effort, failed attempts included)

From `results/run_facts.json` (`tally.py`; per-second list prices read on 27 Oct 2026: L40S preemptible $0.74/h, and $1.82/h for one failed start on the on-demand AMD L40S):

- Machine time: 9,836 s = 2.73 GPU-machine hours over 8 jobs that got a machine (2 more never got one: no L40S was free, which Nebius reports as `NotEnoughResources`).
- Estimated cost: **about $2.16**, all Nebius AI Cloud credits. No Token Factory or other paid call. The Nebius bill decides; disks are not in this estimate.
- Time in training steps across all attempts: 1,715 s (28.6 minutes). First training step (14:29 UTC) to the adapter being saved (about 15:28 UTC): 58.7 minutes of wall clock, including the interruptions.
- Last attempt alone: 15.5 minutes from the start of training (model load included) to the adapter being copied; exam 1 took 827 s and exam 2 715 s on the L40S.

## The adapter and the base model

- The weights (40.5 MB) are not in git. SHA-256 of `adapter_model.safetensors`: `68b9ed06d984a2ec38a32b58090c246a56ae12ba65f3277f66b5d74227ea25d5` (`adapter/adapter_sha256.txt`, recomputed after the download). It differs from round 2's weights, as expected for a new training run.
- The job also answered both exams with the base model (`fast_generate.py` always does). Those answers are not published here: they are not needed for any number above, and the base model's published answers and scores are round 2's. As an internal check, this job's base answers had the same text as round 2's on 170 of 172 and 196 of 200 questions (the rest differ in a few tokens), so the base model is the same one.
- `answers/` holds the new adapter's answers only (`i`, the teacher's reference answer, and the adapter's answer with its confidence); the base field is left out.

## Check it yourself

```bash
python evidence/text2sql-round2-nebius/score_nebius.py verify     # recomputes results_nebius.json from the answers
python evidence/text2sql-round2-nebius/tally.py                   # recomputes time and cost from logs/attempts.json and the logs
sha256sum evidence/text2sql-exam2/data/golden.jsonl               # b7634971...9058, the frozen exam 2
```

`verify` prints `OK: every number in results_nebius.json was recomputed from the answers and matches`, or a `FAIL:` line. It runs each answer's SQL on the exam's database and compares
rows with the reference query (`scripts/measure_adapter.py`, the same function round 2 used). `MANIFEST.json` has a SHA-256 for every file.

## Limits

- One training run on one GPU type, with one resume; a different seed or GPU would give slightly different answers, as the 42 and 56 changed answers show.
- Small adapter, synthetic databases and questions, one greedy answer per question, one reference query per question. The exams are small (a 7-point half-width).
- The timings are from a preemptible machine, not a benchmark.
- Not kept: the exam answering logs (they print the base model's per-row token counts) and the smoke job's logs. This is a hand-run job: the nightly loop has not run nightly on live traffic (Night School ran once by hand on 28 Oct 2026, evidence/nightschool-nebius), and nothing here is endorsed by Nebius.
