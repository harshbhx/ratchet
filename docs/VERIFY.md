# Check it yourself

Every claim Ratchet makes about a result can be rechecked from files in this repository. This page lists each claim, the command that checks it, what the command should print, and what it does **not** prove. This is the quick sheet: copy a command, run it, compare the output. The longer explanations are in [Proof you can run](https://docs.ratchet.dualmindlab.tech/guides/proof/) and [Evidence you can check](https://docs.ratchet.dualmindlab.tech/guides/evidence/). You need Python 3.10 or newer and a clone of the repository. Nothing here needs a GPU, a key or a network connection unless a row says so.

```bash
git clone https://github.com/harshbhx/ratchet.git && cd ratchet
pip install -e ".[dev]"
```

## At a glance

| Claim | One command | Needs |
|---|---|---|
| The exam scores are what the saved answers give | `python scripts/text2sql/exam_numbers.py --check` | nothing |
| One adapter's score recomputes row by row | `python scripts/measure_adapter.py verify …` ([below](#2-one-adapters-score-recomputes-row-by-row)) | nothing |
| The exam was frozen before any model answered it | `sha256sum evidence/text2sql-exam2/data/golden.jsonl` | nothing |
| The adapters are the ones on Hugging Face | `sha256sum adapter_model.safetensors` ([below](#4-the-adapters-are-the-ones-on-hugging-face)) | a download |
| The live run's numbers come from its records | `python -m ratchet.liverun verify evidence/live-smoke-2026-10-23` | nothing |
| The live gateway calls Token Factory | `curl` the live gateway ([below](#5-the-live-gateway-calls-token-factory)) | a network |
| The loop and the rollback behave as tested, on mock models (not a live result) | `python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3` | nothing |
| The tool-calling scores (round 3) recompute from the saved answers | `python scripts/toolcall_results.py --check` ([below](#tool-calling-round-3)) | nothing |
| A signed BOM verifies offline | `python -m ratchet.bom verify …` ([below](#7-a-signed-bill-of-materials-per-adapter)) | nothing |
| The hosted Nano's thresholds follow from real Ultra labels ([evidence/hosted-nano](../evidence/hosted-nano/README.md)) | `python scripts/hosted_nano_calibration.py check` | nothing |
| Every requirement maps to code and tests | `python scripts/reqtrace.py --check` | nothing |

## 1. The exam scores are what the saved answers give

```bash
python scripts/text2sql/exam_numbers.py --check
```

Expected: `OK: both exams.json files match the saved answers`.

This reruns the strict scoring over every saved answer of the untrained model, the [teacher](https://docs.ratchet.dualmindlab.tech/guides/glossary/#teacher) and both adapters on both exams, and compares the result with the numbers the website and the README chart show (`web/src/data/exams.json`). Scoring is strict: an answer is right only if running it on the exam's database returns exactly the reference rows.

**It shows:** the published numbers are the saved answers' numbers, with no typed-in figure.
**It does not show:** that the answers were produced the way the README says. For that, read the [exam 2 evidence](../evidence/text2sql-exam2/README.md) and [exam 1 and round 1](../evidence/text2sql-round1/README.md), which name each script and, where recorded, its cost.

## 2. One adapter's score recomputes row by row

```bash
python scripts/measure_adapter.py verify \
  --raw     evidence/text2sql-exam2/answers/raw_v2.jsonl \
  --results evidence/text2sql-exam2/results/results_v2.json \
  --golden  evidence/text2sql-exam2/data/golden.jsonl \
  --dbs     evidence/text2sql-exam2/data/dbs.json
```

Expected: `OK: every number in results.json was recomputed from raw.jsonl and matches`.

This runs each of the 200 answers against the exam's [SQLite](https://docs.ratchet.dualmindlab.tech/guides/glossary/#sqlite) databases and recomputes the agreement, the coverage and the confidence-gate numbers. Swap `raw_v2` and `results_v2` for `raw_v1` and `results_v1` to check the round 1 adapter.

## 3. The exam was frozen before any model answered it

```bash
sha256sum evidence/text2sql-exam2/data/golden.jsonl
```

Expected: `b7634971cf6b0d79e64e30e4c16d1253fad65ab9a501a06edc6c9f8e70d69058` (on Windows: `python -c "import hashlib;print(hashlib.sha256(open('evidence/text2sql-exam2/data/golden.jsonl','rb').read()).hexdigest())"`).

The repository's `.gitattributes` keeps every file under `evidence/` byte for byte on checkout, so a Windows clone hashes the same as Linux. With an older clone, or a Git that ignores it, clone again with `git clone -c core.autocrlf=false https://github.com/harshbhx/ratchet.git`.

That hash was written into `evidence/text2sql-exam2/data/exam_manifest.json` when the exam was generated. Exam 1's fingerprint is `6b9c1db49bbe2aecf4157ed27845fe024d1b4fcb540f93d81b6987b982b22864`, in the [round 1 manifest](../evidence/text2sql-round1/MANIFEST.json).

**It shows:** the exam file is the one that was frozen. **It does not show:** when the hash was first recorded: that rests on this repository's dated history and the saved generator output.

## 4. The adapters are the ones on Hugging Face

Download an adapter's `adapter_model.safetensors` from the [model page](https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql) (the file at the top level is round 2, the one in `round1/` is round 1) and hash it:

| Adapter | SHA-256 of the weights |
|---|---|
| Round 1 | `a883bcf6bbf4152efb5a874520b8fa1a09d9bead55c172507725dc255f18236a` |
| Round 2 | `38f99d441e0527b75fdf8fd9d10acbf4473dcf70db7febe3ad73fccd631a6aa7` |

The same values are in each evidence folder's `MANIFEST.json`. Round 2's first run is in [`evidence/text2sql-round2`](../evidence/text2sql-round2). The weights themselves are 40.5 MB each and are not in git.

## 5. The live gateway calls Token Factory

```bash
curl -s https://live.ratchet.dualmindlab.tech/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"ratchet","messages":[{"role":"user","content":"In one sentence, what is a LoRA adapter?"}]}'
```

The reply carries a `ratchet` block. Expect `served_by: "teacher"` and the model `nvidia/Nemotron-3-Ultra-550b-a55b`, or, for a random 40% of eligible requests, the hosted Nano 30B (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, route `demo_split`). Either way you get a small `cost_usd` (a fraction of a cent) and a `request_id`. It is a real call, so please keep it to a few. Ratchet's own adapter is not connected to the live gateway; every model call on it runs on Nebius Token Factory.

**The run on record:** `python -m ratchet.liverun verify evidence/live-smoke-2026-10-23` recomputes the client-side numbers of a recorded 20-request run from its 21 per-request records. Expected: `recomputed 11 client-side values and 1 rounds from 21 records`. It also prints a line that starts `not recomputable here (ledger snapshots or entered by hand)`; those values are listed, not checked. Three recorded live Scout runs are in [`evidence/scout`](../evidence/scout), and you can [replay](https://docs.ratchet.dualmindlab.tech/guides/glossary/#replay) them [in your browser](https://huggingface.co/spaces/harshbhx/ratchet-scout).

## 6. The loop and the rollback, on mock models

```bash
python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3
make demo        # then open http://localhost:8000/dashboard
make reference   # three pinned workloads must reproduce the v0.4.0 behaviour
```

These run **mock models**. They show how the cascade, the release gate and the rollback behave, not real model quality, and every figure they print is a mock-mode run.

## 7. A signed bill of materials per adapter

In the mock-mode gateway you can generate a key, sign an adapter's [bill of materials](https://docs.ratchet.dualmindlab.tech/guides/glossary/#bom) and verify it offline:

```bash
python -m ratchet.bom keygen                       # a signing key and its public key
RATCHET_BOM_SIGNING_KEY=<the signing key> make demo
curl -s localhost:8000/ratchet/versions/2/bom | python -m ratchet.bom verify - --public-key <the public key>
```

The public half of Ratchet's own signing key ([Ed25519](https://docs.ratchet.dualmindlab.tech/guides/glossary/#ed25519), key id `81b0a086250a81d0`) is in [evidence/keys](../evidence/keys/README.md). The 28 Oct Night School [BOM](https://docs.ratchet.dualmindlab.tech/guides/glossary/#bom) was signed with it afterwards, on 29 Oct 2026 (`evidence/nightschool-nebius/ratchet_bom.signed.json`; the content is unchanged from the run's own record). Check a document against it with `--public-key Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc=` (and `--notice NOTICE.md` for the adapter's notice).

A signed BOM is the operator's signed statement of the data and evaluation behind an adapter: it shows that nothing changed after signing, not that the weights were trained on that data. See [Features in depth](FEATURES.md).

## 8. The test suite and the requirements matrix

```bash
make test                      # 2012 tests, no network, no GPU (Linux, macOS or WSL)
python -m pytest -q            # the same suite without make
python scripts/reqtrace.py --check
```

Run the suite on Linux, macOS or WSL: Windows has no `make`, and `tests/test_watch.py` needs `fcntl`, which Windows lacks.

Expected for the last command: `reqtrace: OK: 228 requirements …`. Every requirement and every invariant maps to code and to tests in [REQUIREMENTS.md](REQUIREMENTS.md).

## What it cost

About $17 on record for the two text-to-SQL adapters. All are estimates recorded by the scripts, not invoices; check the billing pages before quoting them.

| Item | USD | Where it is recorded |
|---|---|---|
| Teacher answers, round 1 data and exam 1 (273 calls) | 2.87 | `evidence/text2sql-round1/data/teacher_manifest.json` (`spent_usd_estimate`) |
| Teacher answers, round 2 data (914 calls) | 9.18 | `evidence/text2sql-round2/data/teacher_manifest_round2.json` (`spent_usd_estimate`) |
| Teacher answering exam 2 (200 rows) | 0.11 | `evidence/text2sql-exam2/results/teacher_selfcheck.json` |
| GPU job, round 2 training and exam 1 answers | 4.80 | `evidence/text2sql-round2/logs/final_status.json` (`spent_usd`) |
| Round 1 training | 0 | a borrowed GPU, no charge |

Not costed: the GPU runs that answered exam 2, small teacher re-runs on exam 1, and any earlier probes. The tool-calling round (about $2.33 of GPU time, plus teacher spend) is separate and is not in this sum. So is the hosted Nano's [calibration](https://docs.ratchet.dualmindlab.tech/guides/glossary/#calibration) ($0.16 of Token Factory calls, `evidence/hosted-nano/collect.json`).

## What none of this proves

- The exams are synthetic text-to-SQL, not your data. A strict one-reference exam also marks a correct query wrong when it returns extra columns; the teacher's own score shows that.
- Agreement is with the teacher's answers, not with ground truth.
- The adapters were trained on a borrowed GPU with the standalone scripts in `scripts/text2sql/`; Night School's Nebius job ran once by hand on 28 Oct 2026 ([`evidence/nightschool-nebius`](../evidence/nightschool-nebius)), and these exam results do not test it.
- The hard tool-calling exam was written by one model (Claude Sonnet 5.5), so it may favour that model's style; the [BFCL](https://docs.ratchet.dualmindlab.tech/guides/glossary/#bfcl) exam is a sample of a public benchmark, which models may have seen.
- On the BFCL exam the adapter's gain over the untrained model is not significant (sign test p = 0.0784), and its gain comes from the `irrelevance` category only. See [Tool-calling round 3](#tool-calling-round-3).
- Scores come from one run of each model at one setting. The 95% ranges say how much a different sample of questions could move them; they do not cover a different training run.

## Tool-calling round 3

Every claim below recomputes from files in `evidence/toolcall/`. Nothing here needs a GPU, a key or a network connection.

| Claim | One command |
|---|---|
| The scores, ranges and wording verdicts follow from the saved answers, and every saved verdict follows from its raw answer | `python scripts/toolcall_results.py --check` |
| The hard exam is the frozen one | `python -c "import hashlib;print(hashlib.sha256(open('evidence/toolcall/exams/exam_hard.jsonl','rb').read()).hexdigest())"` prints `e26b6d020caedf2ce050b48641d5c2a55a96202826f51ca66ad81be9d04dada2` |
| The BFCL exam is the frozen one | `python -c "import hashlib;print(hashlib.sha256(open('evidence/toolcall/exams/exam_bfcl.jsonl','rb').read()).hexdigest())"` prints `c6b2e9dd3abd6bb2a5e7b33105e0de372612be51e82e2117087e6c36faeccb37` |
| The copied evidence files are unchanged | `python scripts/toolcall_results.py --check` (compares each file's SHA-256 with `evidence/toolcall/MANIFEST.json`) |
| The drafts and the page data are the ones the numbers give | `python scripts/toolcall_results.py --render --web --check-render` |

**It shows:** the published numbers are the saved answers' numbers, under the exam's own strict scorer. **It does not show:** that the adapter produced those answers on the borrowed GPU as described: that rests on the training and generation logs kept with the run. A 95% range on 150 or 300 questions is wide, which is why the wording says 'about the same' only when the ranges overlap and the gap is under 5 points, and 'lower' or 'higher' with a chance caveat when the ranges overlap and the gap is larger.

Found something that does not match? Open an [issue](https://github.com/harshbhx/ratchet/issues/new/choose). A number that cannot be rechecked should not be on the page.
