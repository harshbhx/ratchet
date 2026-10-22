# Text-to-SQL scripts

The scripts behind the text-to-SQL adapter described in the docs' text-to-SQL and results guides. They are tools you
run by hand, not part of the gateway, and no test imports them.

Honest scope: the adapter was trained on a free Google Colab T4 GPU, never on Nebius. The teacher calls that wrote the
training data went to Nemotron 3 Ultra on Nebius Token Factory (22 Oct 2026). The gateway's own live mode has not been
verified.

| File | What it does |
|---|---|
| `gen_sql_data.py` | Asks the teacher to invent small SQLite databases and write questions with SQL answers. Each SQL runs on its database and is kept only when it is one read-only SELECT that returns 1 to 100 rows. The exam uses whole databases (and some domains) that never appear in training. Writes `train.jsonl`, `golden.jsonl`, `dbs.json` and `teacher_manifest.json`. |
| `train_t4.py` | Runs on the Colab T4: installs the Mamba kernels, trains a 4-bit LoRA adapter on Nemotron 3 Nano 4B, then answers the held-out rows with the base model and the adapter (through `../measure_adapter.py generate`). Writes `/content/package.zip`. |
| `../measure_adapter.py` | `generate` answers the exam on the T4; `report` scores the answers and writes the results file. |

## Commands

```bash
# 1. Build the data (needs NEBIUS_API_KEY; costs a few US dollars at most, capped by --max-usd)
export NEBIUS_API_KEY=...        # never commit it
python scripts/text2sql/gen_sql_data.py --out out/sql --n-train 1200 --n-golden 250 --max-usd 3

# 2. On a Colab T4: upload train.jsonl, golden.jsonl, scripts/measure_adapter.py and train_t4.py to /content, then
MODE=real EPOCHS=2 python /content/train_t4.py
#    MODE=pipeline-test (the default) is a quick dry run; MAX_STEPS and MAX_SEQ are also read.

# 3. Back on your machine: score the base model and the adapter
python scripts/measure_adapter.py report --raw raw.jsonl --golden golden.jsonl --adapter adapter --out results.json
```

Overrides for step 1: `RATCHET_TEACHER_BASE` and `RATCHET_TEACHER_MODEL` (defaults: Token Factory and Nemotron 3 Ultra).
