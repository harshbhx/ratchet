# Serving audit: student endpoint, runtime LoRA, providers (main @ 3c9d063)

## Summary
1. The vLLM side is mostly right for a hybrid Mamba model on one L40S. The flags, the runtime-LoRA API, 404 detection and reload, logprobs and the thinking switch all hold up, and the targeted tests pass (32 passed).
2. The real risk is the train-then-load handoff. Adapter names are reused after a failed night, the job writes into an existing folder, the poll only checks that a file exists, and vLLM's "already loaded" counts as success. Together these can evaluate, sign and promote weights that are not the ones the BOM describes. The documented "stop the endpoint when idle" practice is enough to trigger it.
3. Cost and first-run problems: the default `RATCHET_TRAIN_CMD` does not wait unless `RATCHET_BUCKET_S3` is set. Nothing checks the endpoint before the paid job starts. A failed job is waited on for 2 h. Every endpoint `start` downloads the 8 GB base model again.

vLLM facts below apply to the version the repo pins (`vllm/vllm-openai:v0.18.0`, as the code comments state). The runtime-LoRA endpoints, `VLLM_ALLOW_RUNTIME_LORA_UPDATING`, the per-process adapter registry and the 404 for an unknown model are long-standing (v0.6+). Raw (pre-temperature) logprobs as the V1 default comes from `--logprobs-mode` (v0.10+). I did not run a v0.18 image here; DAY1 rows 5, 6, 12 and 16 are the real check.

## Findings (ranked)

### HIGH-1: a reused adapter name can make Night School evaluate and sign weights that are not the ones it trained
- Where:
  - `ratchet/nightschool/orchestrate.py:253`: `name = f"student-v{len(ledger.versions()) + 1}"`. The count moves only after `create_version`, which runs after `trainer.train`, which ends with `load_adapter`.
  - `ratchet/nightschool/train.py:210`: `out.mkdir(..., exist_ok=True)`. The old folder is reused and never cleared.
  - `deploy/train_job.sh:55-58`: the poll is `aws s3 ls .../adapter_model.safetensors`, an existence check that runs once before the first `sleep`.
  - `ratchet/live.py:110`: any 4xx whose text contains "already" counts as success.
- How it fails:
  1. Night N trains `student-v5`. The job finishes, but `load_adapter` fails because the endpoint is stopped, which DEPLOYMENT.md §3 and deploy/CLAUDE.md recommend. `reload_before_night` failing does not stop the night (`live.py:195-207`). The paid job is wasted and no version is created.
  2. Night N+1 names its version `student-v5` again. It submits the job, and the poll finds last night's `adapter_model.safetensors` straight away and prints "Adapter ready". `load_adapter` loads last night's weights.
  3. `LiveEvaluator` grades those weights. The BOM signs tonight's training rows and dataset hash, and the certificate goes with them. 30-60 min later the job overwrites the bucket folder while vLLM keeps the old weights in memory. After the next endpoint restart, `AdapterReloader` loads the new weights, which were never evaluated.
  - The same happens after `deploy/vm/restore.sh` brings back a ledger with fewer versions. If the endpoint was not restarted, the reused name gets vLLM's 400 "already loaded", which `load_adapter` accepts, so the old in-memory weights are served under the new version.
- This breaks the BOM/certificate story (INV-14, FR-16.3/16.4) and can release an ungated model.
- Fix (any one closes most of it; do all three):
  - Make the adapter name unique per attempt, for example `student-v5-<dataset_sha[:8]>` or a timestamp. `ADAPTER_NAME` allows it.
  - Have `train.py` refuse a non-empty `--out`, or write to a temp folder and rename it at the end.
  - Poll for `ratchet_manifest.json`, which is written last (`train.py:254`), and compare its dataset hash with the one `ratchet.live` wrote.
  - For loads right after training, treat "already loaded" as an error, or unload and then load. Keep the tolerance only in `reload_adapters` / `AdapterReloader`.

### MEDIUM-1: the default `RATCHET_TRAIN_CMD` does not wait for training unless `RATCHET_BUCKET_S3` is set
- Where:
  - `ratchet/live.py:136`: the default is `bash deploy/train_job.sh {version}`.
  - `deploy/train_job.sh:53-64`: without `RATCHET_BUCKET_S3` the script submits the job and exits 0.
  - `.env.vm.example:173` and `.env.example:50` leave `RATCHET_BUCKET_S3` empty, and `config.py:451` only lists it in `DEPLOY_ENV`. Nothing warns.
- How it fails: on the first live night `load_adapter` runs seconds after the job is submitted. vLLM refuses a missing path, so the night raises while the paid job keeps running. On the next night the folder exists, so every night loads the previous night's weights without anyone noticing (HIGH-1, made routine).
- Fix:
  - Make `train_job.sh` exit non-zero when `RATCHET_BUCKET_S3` is unset, unless `--no-wait` is passed. Or have `NebiusTrainer.train` check for it.
  - Add a `Settings.validate` warning for live mode when the train command is the default and `RATCHET_BUCKET_S3` is empty.

### MEDIUM-2: no check that the endpoint is reachable before the paid training job
- Where: `ratchet/live.py:139-147` checks only that `student_base` is set. `reload_before_night` (`live.py:195-207`) logs a failure and carries on.
- How it fails: an endpoint that is stopped or down at 02:30 means the night pays for a full GPU training job and then fails at `load_adapter` (ConnectError). That is the HIGH-1 trigger. The `training_job` interval still records the GPU-hours.
- Fix: before `_run(self.sync_cmd)`, call `readiness.list_models(student_base, key, ...)`. On failure, raise, or start the endpoint (`student_endpoint.sh start`) and wait for `RUNNING`. Optionally stop it again after the eval.

### MEDIUM-3: the poll never looks at the job's state
- Where: `deploy/train_job.sh:55-62`.
- How it fails:
  - A job that dies at minute 3 (a `TemplateMismatch`, OOM, an image pull error) still makes the night wait the full 2 h. The `training_job` compute interval (`orchestrate.py:256-271`) then records about 2 h of GPU at the training rate, so the cost report overstates training spend.
  - The job name `ratchet-student-vN` is reused after a failed night (HIGH-1). If Nebius refuses a duplicate job name, every later night fails at `job create` with the same name, permanently. I have not verified whether it does; check `nebius ai job create --help` on day 1.
- Fix:
  - In the loop, also check `nebius ai job get` (or `list`) for the job's state. Exit 1 on FAILED/ERROR/CANCELLED.
  - Add a unique suffix to the job name.

### MEDIUM-4: every endpoint `start` downloads the base model again and recompiles (slow, billed cold starts)
- Where: `deploy/student_endpoint.sh:208-225` sets no `HF_HOME`, `--download-dir`, `HF_TOKEN` or `VLLM_CACHE_ROOT`. Only `/data` (the bucket) persists.
- How it fails: each start/stop session, the pattern the docs recommend, pulls ~8 GB of BF16 weights from Hugging Face without a token, then does torch.compile and CUDA-graph capture again. That adds minutes of paid GPU time per start. Anonymous Hugging Face downloads can be throttled, so a start can hang or fail. Until the model is up, the gateway's student calls fail and the breaker opens, which is handled but costs teacher money.
- Fix:
  - Pass `--env HF_HOME=/data/hf` (or `--download-dir /data/hf`) and `--env VLLM_CACHE_ROOT=/data/vllm-cache`. Measure FUSE read speed against a fresh download on day 1.
  - Or bake the weights into a derived image.
  - Pass `HF_TOKEN` through a secret.

### LOW-1: the student's 15 s read timeout is shorter than its 1024-token cap can take, and a long answer counts as the endpoint being down
- Where:
  - `ratchet/config.py:823` (`RATCHET_STUDENT_TIMEOUT_S=15`) and `providers.py:170` (`DEFAULT_STUDENT_MAX_TOKENS=1024`).
  - `resilience.endpoint_down` (`resilience.py:341-342`) treats any httpx timeout as unhealthy.
- How it fails: a non-streamed answer that runs to the cap needs roughly 10-15 s at a few dozen concurrent sequences on an L40S. A student that rambles (a reasoning misconfiguration, or rows trained without EOS, see LOW-2) then trips the shared `student` breaker for all traffic after 5 slow answers. The cause is the answer's length, not the endpoint's health.
- Fix:
  - Lower the student's token cap for label and JSON tasks (a per-task or global `RATCHET_STUDENT_MAX_TOKENS` of about 256).
  - Or set the read timeout from the cap and a minimum decode rate.

### LOW-2: training truncates at 2048 tokens while the endpoint serves 8192
- Where: `train.py:194` (`--max-seq 2048`) and `train.py:240` (`max_length=a.max_seq`). `check_rows` (`train.py:100`) does not reject over-long rows. The endpoint uses `--max-model-len 8192` (`student_endpoint.sh:64`).
- How it fails: TRL keeps the start of an over-long row, so the target's tail and its end-of-turn token are cut. The adapter learns not to stop on long inputs (finish `length`, no vote, escalation), and a prompt longer than 2048 tokens contributes no loss tokens at all.
- Fix: drop rows longer than `--max-seq` in `check_rows` / `dataset.write_training` and count them, or raise `--max-seq` to match what is served.

### LOW-3: the LoRA rank lives in two places that nothing ties together
- Where: `student_endpoint.sh:208` (`--max-lora-rank 16`) and `train.py:193` (`--rank` default 16).
- How it fails: raising one alone makes every load a vLLM 400 ("LoRA rank N is greater than max_lora_rank"), after the paid job. `day1_check._load_hint` explains it afterwards.
- Fix: one setting (`RATCHET_LORA_RANK`) read by both scripts, or a test that asserts the two are equal.

### LOW-4: the logprob semantics behind every calibrated threshold are not pinned
- Where: `confidence.STUDENT_SAMPLING` samples at temperature 0.7 with logprobs. vLLM V1 returns raw, pre-temperature logprobs by default (`--logprobs-mode raw_logprobs`, v0.10+).
- How it fails: production and the eval read the same endpoint, so they agree today. But a new image whose default changes (or an operator setting `processed_logprobs`) would move every live version's confidence against a threshold it was calibrated without. Precision would change with no event.
- Fix: pass `--logprobs-mode raw_logprobs` explicitly in `student_endpoint.sh`, and record the vLLM version in the eval or BOM.

### LOW-5: the `extra_body` reasoning field sends a key no server reads
- Where: `reasoning.py:58-59` (`{"extra_body": {"chat_template_kwargs": ...}}`) and `providers.py:544-545`.
- How it fails: `extra_body` is an option of the OpenAI Python SDK, which merges it into the request body. On the wire it is an unknown top-level key. A vLLM-style server ignores it, with only a warning, so `RATCHET_TEACHER_REASONING_FIELD=extra_body` silently does nothing. DAY1 row 15 measures it, so the harm is limited to a misleading option.
- Fix: remove the option, or document it as "never applied by vLLM-based servers".

### LOW-6: runtime-loaded LoRA state belongs to one replica
- Where: `student_endpoint.sh:216-225` sets no replica count. `load_adapter`, `AdapterReloader` and readiness all go through the endpoint URL.
- How it fails: if the Nebius endpoint ever runs more than one replica (autoscaling, or a rolling restart), a load reaches only one of them. The others answer 404 for part of the requests. The reload then lands on a random replica, often getting "already loaded", which counts as success, so escalations persist and the canary's evidence is skewed toward one replica.
- Fix: pin min = max = 1 replica if the CLI has the flags (check on day 1), and say so in DEPLOYMENT.md.

### LOW-7 (housekeeping): retired adapters are never unloaded
- Where: only `day1_check` calls `unload_adapter` (`live.py:115-124`).
- Impact: `/v1/models` and vLLM's adapter registry grow by one entry per night. GPU slots are not affected (`--max-loras` bounds the slots in one batch). Optionally unload `retired` and `rejected` adapters at the end of a night.

## Verified sound
- vLLM arguments (`student_endpoint.sh:208-215`):
  - `--mamba_ssm_cache_dtype float32` (the model card's flag; vLLM accepts underscores), `--enable-lora` and `--trust-remote-code`.
  - `--max-lora-rank 16` matches `train.py`'s rank 16.
  - `--max-loras 4` covers live + canary + shadow + tonight's candidate in one batch.
  - `--max-model-len 8192` and `--max-num-seqs 64` are sound for 4B BF16 (about 8 GB of weights) on a 48 GB L40S at vLLM's default 0.9 GPU memory utilisation; the fp32 SSM state for 64 sequences fits.
  - `--enable-prefix-caching` is not forced, which is the safe choice for a hybrid Mamba model.
  - The default `vllm serve` port 8000 matches `--container-port`.
- `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` is accepted (vLLM lowercases the value and compares it with "1"/"true").
- The `/v1/load_lora_adapter` and `/v1/unload_lora_adapter` URL comes from `student_base` without `/v1` (`live.py:92-124`). `lora_path` = `RATCHET_ADAPTER_ROOT/<name>` matches the job's `--out /data/adapters/<version>`. The load timeout is 300 s.
- Lost-adapter detection:
  - `adapter_missing` needs a 404 whose message names the whole model name (`resilience.py:317-330`).
  - The 404 does not count against the breaker (`endpoint_down` returns False; `router.py:1813-1824`).
  - The request still escalates (INV-7, `router.py:2397-2406`).
  - `AdapterReloader` is non-blocking, retries an adapter at most once a minute and logs no URL (`live.py:210-305`).
  - `/readyz` checks `/models` against the routed adapters every 5 min and queues the missing ones (`readiness.py:246-259`).
  - The night reloads before the eval and on a 404 inside it (`live.py:195-207`, `543-545`).
- Student calls are never retried on the request path (`gateway.py:389-395`, no `retry`). The night's eval retries them (`live.py:529`). Connect timeout 2 s, read 15 s.
- Logprobs: the student is always asked for `logprobs: true` with n samples at 0.7. `top_logprobs` is sent only when logprobs are (`providers.py:547-551`), within vLLM's default `--max-logprobs` of 20. `_content` never reads `reasoning_content` (`providers.py:639-648`). `served_logprobs` returns None rather than misaligned tokens.
- Thinking switch: `chat_template_kwargs: {"enable_thinking": false}` is sent with every student request (`GenPolicy.student`, `reasoning.py`), and the server's `--default-chat-template-kwargs` is only a backup. A client cannot set `chat_template_kwargs` (`NEVER_FORWARDED`). The fallback student has its own mode.
- Optional teacher calls fail open (INV-15). Student failures and an open breaker fall back to the teacher (INV-7).
- `student_endpoint.sh` refuses `DRY_RUN` values other than 0 or 1, never guesses the endpoint id, hides the token when echoing commands, and checks the JSON kwargs before anything is provisioned. The CUDA 13 image tag has a documented CUDA 12 fallback (DAY1 row 12).
- The day-1 student probe covers the right risks: load twice, the Mamba `in_proj`/`out_proj` targets refused at load, the adapter's answer differing from the base model's, the thinking line, the 404 run through the router's own predicate, and unload. It refuses `student-v<n>` names.
- Tests: `python -m pytest -q tests/test_resilience.py tests/test_live.py -k "adapter or student or day1"` gives 32 passed.
