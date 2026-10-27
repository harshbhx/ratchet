# The student served on a Nebius Serverless Endpoint: speed tests, the evidence

On 27 Oct 2026 the student was served for the first time, by hand, on a Nebius AI Cloud Serverless Endpoint, to measure how fast it starts and how fast it answers. A second session on 27 to 28 Oct 2026 (IST) measured a cold start again and, for the first time, a restart (stop, then start).
Both were short tests. The endpoint was deleted when the second session ended and nothing is left running. **The public live demo is not connected to this endpoint**: the teacher answers every live request.
No accuracy was scored here. Accuracy is in the exam folders ([round 2](../text2sql-round2/README.md), [tool calling](../toolcall/MANIFEST.json)).

## Result

- **Cold start:** about **7.2 minutes** from the create command to the first successful `/v1/models` call (432.5 s). The endpoint reported RUNNING at 293 s; vLLM then needed another 2.3 minutes to load the model.
  Both adapters loaded into the running server in about a second each (0.8 s and 0.7 s). The first completion through each adapter came at 471.9 s (about 7.9 minutes).
- **Restart (session 2):** about **7.1 minutes** from the start command to the first answer (424.2 s), against **7.6 minutes** (457.3 s) for the cold start measured in the same session: about 33 s faster. Both runs reached RUNNING at 275.8 s, so pulling the image dominates both; vLLM was ready (`/v1/models` OK) at 384.8 s after the restart and at 418.0 s cold.
  The adapters must be loaded again after every restart (the runtime LoRA API): they were loaded at 386.3 s after the restart (0.8 s and 0.7 s), and the first completion through each adapter came at 424.2 s.
  Files: `results/session2_cold_start.json`, `results/session2_restart.json`, `results/timeline_session2.md`.
- **Speed after the restart, 4 requests at once** (30 non-exam prompts, at most 256 tokens, no accuracy scored): SQL adapter p50 1.26 s (p95 1.99 s, about 110 tokens per second, 0 errors); tool-calling adapter p50 1.08 s (p95 1.73 s, about 97 tokens per second, 0 errors). File: `results/session2_bench_restart.json`, adapters only.
- **Speed, 16 requests at once (session 1):** SQL adapter **p50 1.63 s** (p95 3.94 s, about 276 tokens per second in total); tool-calling adapter **p50 1.23 s** (p95 2.53 s, about 302 tokens per second).
- **Cost:** session 1 about **$0.45** of GPU time, failed attempts included; session 2 about 18 minutes of GPU time, about **$0.22**; about 53 minutes and **$0.67** across both. The Nebius bill decides.

Session 1 (cold start), all rows below:

| Adapter | Concurrent requests | Requests (ok) | p50 s | p95 s | tokens per second (all requests) | errors |
|---|---|---|---|---|---|---|
| `sql-r2` | 1 | 20 (19) | 0.99 | 1.74 | 11.4 | 1 |
| `sql-r2` | 4 | 40 (40) | 1.25 | 1.89 | 121 | 0 |
| `sql-r2` | 16 | 64 (64) | 1.63 | 3.94 | 276 | 0 |
| `toolcall` | 1 | 20 (20) | 0.95 | 1.26 | 27.7 | 0 |
| `toolcall` | 4 | 40 (40) | 1.05 | 1.31 | 101 | 0 |
| `toolcall` | 16 | 64 (64) | 1.23 | 2.53 | 302 | 0 |

- The one error was a connection error at the very start of the first run (`sql-r2`, 1 at a time). It also stretched that run's wall time, so its 11.4 tokens per second is not comparable with the other rows; the p50 latency is.
- Latency is the time from sending the request to the last streamed token. Every request asked for at most 256 tokens at temperature 0.
- The benchmark used 30 ordinary prompts (15 text-to-SQL, 15 tool calling) in `data/prompts.jsonl`. None is an exam question. It measured **speed only, not accuracy**: the answers were not scored.
- Session 1 is one cold instance, one run, on one spot GPU. A different hour or a different GPU could give different numbers.
- The base model was also benchmarked. **Base-model rows kept private**; they are not in this folder and are stripped from the copied benchmark file.

## What ran

| | |
|---|---|
| Where | Nebius AI Cloud Serverless Endpoint (`nebius ai endpoint create`), eu-north1, a bucket mounted at `/data` |
| GPU | one NVIDIA L40S, platform `gpu-l40s-a`, preset `1gpu-8vcpu-32gb`, **preemptible (spot)**, price following the spot price. No preemption happened |
| Server | public image `vllm/vllm-openai:v0.18.0-cu130` (vLLM v0.18.0), `--auth token` |
| Model | the base model NVIDIA Nemotron 3 Nano 4B BF16 (`nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`), with LoRA adapters loaded at runtime |
| vLLM flags | `--trust-remote-code --enable-lora --max-lora-rank 16 --max-loras 4 --mamba_ssm_cache_dtype float32 --max-model-len 8192 --max-num-seqs 64`, thinking off by default, `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`, `--shm-size 16Gi` |
| Cache directories | `HF_HOME` and `VLLM_CACHE_ROOT` on the machine's local disk (the fix after the two crashes below) |
| Adapters | `sql-r2` and `toolcall`, read from `adapters/<name>/` in the bucket and loaded with `/v1/load_lora_adapter` |
| Scripts | `scripts/startup.py` (times the start-up), `scripts/bench.py` (the speed test), `scripts/common.py` (helpers), as they were run |

The create script is not copied: it reads the endpoint's access token from a private file. Its flags are the ones in the table above.

## Which adapters were served

- **`sql-r2`** is the **published round-2 text-to-SQL adapter**: `adapter_model.safetensors` SHA-256 `38f99d441e0527b75fdf8fd9d10acbf4473dcf70db7febe3ad73fccd631a6aa7`.
  This is the hash in [`text2sql-round2/MANIFEST.json`](../text2sql-round2/MANIFEST.json) (39.0% on exam 1, 33.0% on exam 2). The files were downloaded from the public Hugging Face repository `harshbhx/ratchet-nemotron-nano-4b-text2sql`, checked against that hash, and uploaded to the bucket.
- It is **not** the adapter retrained on Nebius ([`text2sql-round2-nebius`](../text2sql-round2-nebius/README.md), SHA-256 `68b9ed06d984a2ec38a32b58090c246a56ae12ba65f3277f66b5d74227ea25d5`).
- **`toolcall`** is the published tool-calling adapter from `harshbhx/ratchet-nemotron-nano-4b-toolcall`, SHA-256 `fde47de3913fdf14d29328a174b5a92411c3f058989017c7709b4c572db324b9`, computed on the downloaded file. This repository has no earlier record of that hash to compare with.

## What went wrong in session 1, and how session 2 fixed it

- **Attempt 1 crashed.** The first endpoint reached RUNNING at 268.8 s, but vLLM had stopped: with `HF_HOME` on the bucket mount, the Hugging Face download left a 0-byte `config.json` (the mount does not handle the download's rename). `/v1/models` never answered (`results/failed_attempt1.log`). The fix was to move `HF_HOME` to local disk. That endpoint was deleted and one new one was created (attempt 2).
- **Attempt 1 was also an accident of process:** the endpoint was meant to be a dry run of the create command and was created for real. It had the spot flags, so it was kept as the first try.
- **Attempt 2 is the cold start measured above.** Its console log is `results/attempt2_cold_start.log`. The machine-readable start-up file for attempt 2 was overwritten, so the log and `results/timeline.md` are its record; `results/attempt1_and_cached_restart.json` holds attempt 1 and the restart.
- **Session 1's restart crashed, so it was not measured then.** After the benchmark the endpoint was stopped (it reached STOPPED within 10 s) and started again. It reached RUNNING at 268 s, then vLLM crashed: the torch-compile cache in `VLLM_CACHE_ROOT`, which was still on the bucket mount, held a truncated file ("Input data is too short to contain checksum"). The cause was cache files on the bucket mount. The fix, used in session 2, was to move `HF_HOME` and `VLLM_CACHE_ROOT` to local disk. Session 2's restart (above) worked with it.
- **Session 2's first start command hit an operation conflict:** the stop was still finishing and nothing started; the start was retried about 7 minutes later and the restart numbers above count from the retry (`results/timeline_session2.md`).
- **No spot preemptions** were seen, in either session.
- **Cleanup:** the endpoint of each session was deleted at the end (a stopped endpoint still keeps its 250 GiB disk). The endpoint list was empty and no jobs were running.

## Time and cost

Session 1: GPU time about 35 minutes across all attempts: attempt 1 about 5 to 10 minutes, the cold run and benchmark about 13 minutes, the failed restart about 10 minutes.
At the $0.74 per hour list price for a preemptible L40S read on 27 Oct 2026 that is about **$0.45**.
Session 2: about 18 minutes of GPU time (the time the endpoint was stopped is not counted), about **$0.22** at the same price. Both sessions: about 53 minutes, about **$0.67**. Disks and the bucket are not in these estimates. The Nebius bill decides.

## Redacted

Endpoint ids, the endpoint's public host name, retraining job ids and the private paths of the operator's machine are replaced by `<redacted>`. The access token and the bucket's keys are not in any file here (the scripts read the token from a private file). The base-model rows are removed, from session 2's benchmark too. Session 2's start-up files have the endpoint id and host name replaced with `<redacted>`; its endpoint-details file is not copied.

## Limits

- One cold run (session 1), one cold start and one restart (session 2), on spot GPUs, 30 prompts, speed only. The restart was measured once. This says how fast the student can answer, not how good its answers are.
- Nothing here is a production deployment. The endpoint existed for the tests only, and the live demo never used it.
- A restart is not instant: image pulling dominates, so a restart saves about 33 s against a cold start, not minutes. The cold starts (7.2 and 7.6 minutes) are a fresh machine with nothing cached; the 7.1 minute restart still had to load both adapters again.

`MANIFEST.json` has a SHA-256 for every file.
