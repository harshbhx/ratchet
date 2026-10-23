# Text-to-SQL scripts

The scripts behind the text-to-SQL adapter described in the docs' text-to-SQL and results guides. They are tools you
run by hand, not part of the gateway, and no test imports them. The evidence from the first run is in
[`evidence/text2sql-round1`](../../evidence/text2sql-round1).

Honest scope: the adapter was trained on a free Google Colab T4 GPU, never on Nebius. The teacher calls that wrote the
training data went to Nemotron 3 Ultra on Nebius Token Factory (22 Oct 2026). The gateway's own live mode has not been
verified.

| File | What it does |
|---|---|
| `gen_sql_data.py` | Asks the teacher to invent small SQLite databases and write questions with SQL answers. Each SQL runs on its database and is kept only when it runs and returns rows. Writes `train.jsonl`, `golden.jsonl` (the exam), `dbs.json` and `teacher_manifest.json`. |
| `train_t4.py` | Runs on the Colab T4: installs the Mamba kernels, trains a 4-bit LoRA adapter on Nemotron 3 Nano 4B. `SMOKE=1` runs 3 steps first; `DEFER_GEN=1` stops after saving the adapter, so the answering runs in a fresh process. |
| `fast_generate.py` | Answers the exam with the base model and with the adapter. It passes the model's decoding cache correctly (the model's own code drops it, which made every answer 45 times slower), stops when the SQL ends, writes each row at once and resumes after an interruption. `--suffix` adds a line to every question (prompt v2). |
| `teacher_selfcheck.py` | Asks the teacher the same exam prompts and scores its answers like the student's (a yardstick for the exam; costs about 15 US cents). |
| `../measure_adapter.py` | `report` scores the answers and writes the results file; `verify` recomputes every number from the answers. |

## Commands

```bash
# 1. Build the data (needs NEBIUS_API_KEY; costs a few US dollars at most, capped by --max-usd)
export NEBIUS_API_KEY=...        # never commit it
python scripts/text2sql/gen_sql_data.py --out out/sql --n-train 1200 --n-golden 250 --max-usd 3

# 2. On a Colab T4 (the Mamba kernels need a GPU with CUDA): upload train.jsonl, golden.jsonl and train_t4.py to /content, then
SMOKE=1 DEFER_GEN=1 MODE=real python /content/train_t4.py     # 3 steps, to prove the whole path first
DEFER_GEN=1 MODE=real EPOCHS=2 python /content/train_t4.py    # the real run; the adapter lands in /content/adapter

# 3. Answer the exam in a separate process (resumes if interrupted)
python fast_generate.py --golden golden.jsonl --adapter adapter --out raw.jsonl

# 4. On any machine, no GPU: score the answers and check the score
python scripts/measure_adapter.py report --raw raw.jsonl --golden golden.jsonl --dbs dbs.json --adapter adapter --out results.json
python scripts/measure_adapter.py verify --raw raw.jsonl --results results.json --golden golden.jsonl --dbs dbs.json
```

Overrides for step 1: `RATCHET_TEACHER_BASE` and `RATCHET_TEACHER_MODEL` (defaults: Token Factory and Nemotron 3 Ultra).

## What went wrong on the way (so you do not repeat it)

Keep the Mamba `in_proj`, `out_proj` and `lm_head` layers out of 4-bit; cast the LoRA weights to fp32 after the trainer is built;
answer the exam in a separate process from training; copy partial answers off a free Colab machine every few minutes, because
Colab takes the machine back without warning.

## Round 2 on Modal

`modal_train.py` runs the same `train_t4.py` and `fast_generate.py` on a rented T4 at Modal, as one detached pipeline (train, then answer the
exam) that survives the laptop being off, with a cost cap enforced in code and a read-only progress endpoint (`modal_status_app.py`). Put
`train_t4.py`, `fast_generate.py`, `golden.jsonl` and the merged training file (`train_r2.jsonl`, see
`evidence/text2sql-round2/data/merge_and_check.py`) in one folder with these two scripts, then:

```bash
python -m modal run modal_train.py::build                 # once: compile the Mamba kernels for the T4
python -m modal run --detach modal_train.py::pipeline     # train, then answer the exam
python -m modal volume get ratchet-out raw_r2.jsonl .     # the answers; the adapter is in the volume's adapter/ folder
```

The first training step compiles GPU code for 15 to 25 minutes while the GPU sits idle; that is normal.
