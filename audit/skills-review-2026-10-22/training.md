# Audit: training and distillation path (main @ 3c9d063, read-only)

## Summary
1. The core SFT recipe holds up against TRL 1.16.0 and the real Nemotron-H code (both downloaded and read). The loss covers only the completion, the chat template is the same in training and serving with thinking off, the fused LM head keeps memory sane, and the split and invariant rules hold.
2. The real risks are in the handoff from the GPU job to vLLM. Without `RATCHET_BUCKET_S3` nothing waits for the job. A version name is reused after a failed night, and the poll only checks that a file exists. So a stale or half-written adapter can be loaded, evaluated and signed as tonight's.
3. `measure_adapter.py` scores SQL by matching text, but the docs say it uses execution match and name functions that do not exist. Also, the Mamba `out_proj` LoRA never trains, because the fused kernel reads the base weight directly.

Sources checked beyond the repo: TRL 1.16.0 wheel (`sft_trainer.py`, `sft_config.py`, `utils.add_fused_lm_head`), PEFT 0.21.2 (`peft_model.save_pretrained`, `tuners_utils.weight`, `LoraConfig`), HF `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` at sha dfaf35de (`modeling_nemotron_h.py`, `tokenizer_config.json`), vLLM v0.18.0 (`models/nemotron_h.py`, `layers/mamba/mamba_mixer2.py`, `lora/layers/column_parallel_linear.py`). `pytest -q tests/test_nightschool.py`: 31 passed.

## Findings (ranked)

### HIGH-1: Handoff from the GPU job to vLLM: no wait, a reused folder and a racy poll
- `deploy/train_job.sh:53-64`: the script blocks only when `RATCHET_BUCKET_S3` is set. Otherwise it prints "Submitted" and exits 0. `ratchet/live.py:146-147` (`NebiusTrainer.train`) then calls `load_adapter` at once. Nothing in `config.py` or `NebiusTrainer` requires the variable (it is only listed in `DEPLOY_ENV`, `config.py:451`).
- `ratchet/nightschool/orchestrate.py:253`: `name = f"student-v{len(ledger.versions()) + 1}"`. A night that fails inside `trainer.train` creates no version, so the next night reuses the same `/data/adapters/student-vN`. `train.py:210` does `out.mkdir(exist_ok=True)` and never refuses a folder that already holds weights.
- `train_job.sh:56` polls only for `adapter_model.safetensors`. PEFT 0.21.2 `save_pretrained` writes the safetensors (peft_model.py ~l.356) before `adapter_config.json` (~l.414). `train.py:252-254` then writes the tokenizer, `ratchet_manifest.json` and NOTICE.md.
- A failed job is not detected. The poll waits the full 2 h (`seq 1 240` × 30 s).
- **Failure scenarios.**
  - (a) Operator sets everything but `RATCHET_BUCKET_S3`: every night submits a paid job, then fails at `load_adapter` (vLLM 400/404), and the job keeps running.
  - (b) Night N fails after the job was submitted, either from (a) or from a load error. The job finishes later and writes `student-vN/adapter_model.safetensors`. On night N+1 the poll sees the old file at once, so last night's weights are loaded, evaluated, given tonight's `train_examples`, `dataset_sha256` and set commitments, and signed into the BOM (INV-14's "exact rows" no longer holds). Meanwhile the new job overwrites the same file. After the next endpoint restart, `AdapterReloader` loads the new weights under a version that was evaluated on the old ones.
  - (c) The poll lands between the safetensors and the config write, and vLLM refuses the load.
- **Fix.**
  - Give each attempt its own folder (for example `student-vN-<night ts>`), or make `train.py` exit non-zero when `out/adapter_model.safetensors` already exists.
  - Have `train.py` write a sentinel (`_DONE`, holding the dataset sha) after `write_metadata`, and poll for that file, not the weights.
  - Make `NebiusTrainer.train` refuse to start when `RATCHET_TRAIN_CMD` is the default and `RATCHET_BUCKET_S3` is unset.
  - Poll `nebius ai job get <id>` as well, so a failed job ends the wait early.

### HIGH-2: `measure_adapter.py` does not do the "execution match" the docs describe
- `scripts/measure_adapter.py:31,63-68`: `key_of` compares the ticket fields `category`/`priority`, or else the lower-cased, whitespace-normalised text. `agreement` (l.148), `calibrate` (l.155-157) and `build_report` (l.166) all use it. The script never reads `dbs.json` and never runs a query.
- `docs-site/.../guides/text-to-sql.mdx:130-137` and `results.mdx:90` say "execution match: ... return the same rows on the same database". `text-to-sql.mdx:248` names `measure_adapter.py: sql_same, annotate` and `pipeline.sh`. None of these exist in code (`git log -S sql_same` finds them only in docs commits).
- The text match also undercounts:
  - `gen_sql_data.py:247` stores every teacher SQL with a trailing `;`, so a student query without one is "wrong".
  - A student answer inside a ```sql fence is "wrong" (there is no `clean_answer`).
  - `--max-new 160` cuts longer queries off.
- Confidence there is the mean greedy token probability (l.111-114), not the gateway's rule (3 samples at 0.7 with logprobs plus agreement). Yet `results.mdx` presents coverage and precision as "the confidence gate".
- `docs-site/src/data/sql-results.json` is still `{"status": "pending"}`, so nothing wrong has been published yet.
- **Fix.** Before publishing, add `sql_same` (run both queries on `dbs.json[meta.db]` with `gen_sql_data.run_select`, compare rows as a multiset, or in order when the teacher's query has ORDER BY, floats rounded to 6 places) and use it in `agreement` and `calibrate`. Or change the docs to say "normalised text match". Label the confidence as "mean token probability (not the gateway's rule)".

### MEDIUM-1: The Mamba `out_proj` LoRA is a silent no-op in training
- `train.py:58,198`: the default `TARGETS_WITH_MAMBA` includes `in_proj,out_proj`.
- In the model's own code (HF rev dfaf35de, `modeling_nemotron_h.py` l.427-441), training mode with `cache_params is None` calls `mamba_split_conv1d_scan_combined(..., outproj_weight=self.out_proj.weight, ...)`. That is always the case under SFT: TRL forces `use_cache=False`, and gradient checkpointing is on by default in TRL 1.16. The fused kernel never calls the `out_proj` module.
- PEFT's `BaseTunerLayer.weight` returns the base weight (tuners_utils ~l.1943), so `lora_B` for `out_proj` gets no gradient and stays all zeros. `in_proj` is called as a module (l.361), so it does train.
- Effect:
  - The adapter carries 21 dead `out_proj` LoRA pairs.
  - The docs' "the default targets include the Mamba projections" (`night-school.mdx:183`) is only half true.
  - The day-1 vLLM acceptance check is staked on a module that adds nothing.
  - The only adapter actually trained (`train_t4.py:99`) used attention+MLP only, so the production default is untested.
- **Fix.** Drop `out_proj` from `TARGETS_WITH_MAMBA` (keep `in_proj`). In the day-1 smoke job, assert after one step that every targeted LoRA parameter has a non-zero gradient.

### MEDIUM-2: Silent truncation at `--max-seq 2048`
- `train.py:194,240` hands `max_length=2048` to TRL. TRL 1.16 truncates with `keep_start` after building labels (`sft_trainer.py` ~l.1436-1460), which cuts the end of the answer, including `<|im_end|>`. It also drops rows left fully masked. Neither is counted.
- The gateway accepts up to 120k characters (`openai_compat.Limits.max_input_chars`), and the endpoint serves up to `--max-model-len 8192`.
- Long rows are still counted in `train_examples`, `dataset_sha256`, the training-set Merkle root, the NOTICE example counts and the BOM, though the model saw none of their answer or only part of it. Rows with a cut answer teach the student to keep generating rather than stop.
- `train_t4.py:20` uses `MAX_SEQ=768` with SQL prompts that hold the full DDL of up to 7 tables (`gen_sql_data.py:141`), so the same thing happens to the SQL answers.
- **Fix.** In `check_prefix`, which already renders each row with the real tokenizer, tokenize prompt+answer. Fail the job, or write `skipped_too_long` and exclude the row, when it is longer than `max_seq`. Better still, apply a character-length cap in `dataset.rows` before the BOM is built, so the signed sets match what was trained. Consider `--max-seq 4096` or more to match serving.

### MEDIUM-3: The base model revision and its remote code are not pinned
- `train.py:220,228` call `from_pretrained(a.base, trust_remote_code=True)` with no `revision=`. `deploy/student_endpoint.sh:181` does `--model ${BASE} --trust-remote-code`.
- Each night the job downloads and runs whatever `modeling_nemotron_h.py` and weights are on HF at that moment. The endpoint pinned whatever existed when it was created.
- An upstream update means adapters are trained against a base other than the one served (LoRA deltas are base-specific). It also means unreviewed code runs in the job.
- Neither `versions.json` nor the BOM records the base revision.
- **Fix.** Add `RATCHET_STUDENT_BASE_REVISION` (a commit sha), pass it to the tokenizer, the model and vLLM (`--revision`/`--code-revision`), and record it in `versions.json` and the BOM. Cache the weights on `/data` (`HF_HOME`) so each night stops re-downloading about 8 GB.

### MEDIUM-4: Redaction skew between training and serving, invisible to the golden eval
- Training rows and golden rows hold redacted prompts and answers (`router.py:1306-1330`). The student is served the raw messages (`router.py:1805`).
- With the default `label` style, an extraction task learns to answer the literal `[EMAIL]`. The golden eval samples the same redacted prompt and its reference is also `[EMAIL]`, so it counts this as agreement. The certificate and the gate's precision overstate live precision on fields that carry PII. Only live audits can see the gap.
- ADR 0082 adds `numbered`/`surrogate` styles but keeps `label` as the default and does not discuss the measurement gap.
- **Fix.** Default tasks with extraction-type fields to `surrogate`. Report the share of golden rows whose reference contains a placeholder, and keep those rows out of calibration (or flag them in the eval/BOM).

### LOW
- **Hyperparameters for small data** (`train.py:191-195,230-248`):
  - Fixed 2 epochs, batch 8, no gradient accumulation, no warmup, and `lora_dropout` at PEFT's default 0.0. The skills suggest 0.05.
  - A first night with 50-100 rows gets 13-25 optimizer steps. Consider a minimum step count, for example `max_steps = max(ceil(epochs*N/batch), 100)`, and dropout 0.05.
  - `ratchet_manifest.json` (l.141-149) records no hyperparameters, no seed (TrainingArguments' default is 42) and no targets. Add them so an adapter can be reproduced.
- **Distillation method is not stated.** The method is sequence-level, hard-label distillation (SFT on the teacher's text, reasoning stripped by `tasks.clean_answer`, no teacher logprobs). Nothing overclaims, but no page says so. Add one sentence to night-school.mdx and the README. Note too that the student learns answers without the teacher's rationale, which limits it on "hard" items.
- **Conflicting targets.** The same messages stored under two tasks, or under two `response_format` schemas, give several training rows with the same rendered prompt and possibly different completions: neither the task nor the schema is rendered (`dataset.py:171-175`, `train.columns`).
- **No label-quality filter for free text.** Beyond "non-empty" and the spec, teacher refusals and boilerplate on free-text or default tasks become training targets.
- **`train_t4.py`:**
  - The prefix check runs on row 0 only (l.74-79), and there is no check against the prompt the endpoint serves.
  - It trains in fp16 on a bf16-trained hybrid with no NaN or inf guard on the loss.
  - `measure_adapter.generate` (l.83-85) quantizes `in_proj`/`out_proj` to 4 bit, while training kept them fp16 (`train_t4.py:85`). So the measured model is neither the trained model nor the bf16 model that would be served.
- **`gen_sql_data.py`:**
  - "Every label checked" means the query runs and returns 1-100 rows, not that it answers the question. Say so.
  - Golden rows are grouped by database, and even/odd calibration interleaves within each database, so the calibration and test halves share schemas and are correlated.
- **Template override not checked.** `RATCHET_SERVE_TEMPLATE_KWARGS` (`student_endpoint.sh:40`) can change the serving template arguments without touching `train.TEMPLATE_KWARGS`. `test_training_rows_carry_reasoning_off` checks only the default.
- **No checkpoints.** `save_strategy="no"`: a job that dies near its 2 h limit loses everything. That is acceptable at current sizes; revisit once the dataset grows.

## Verified sound
- **Loss on the answer only.** TRL 1.16's prompt-completion path builds a `completion_mask` and sets prompt labels to -100. `completion_only_loss=True` is set explicitly, and the completion includes `<|im_end|>`. `assistant_only_loss` is correctly off (the template has no `{% generation %}` markers).
- **Template parity, thinking off.**
  - `enable_thinking: false` appears in every row (`dataset.py:66`), in `train.py:36` and in `student_endpoint.sh:39`.
  - The vendored `tests/fixtures/nano4b_chat_template.jinja` differs from the HF `tokenizer_config.json` template only in blank lines between `{%-` tags, which render the same.
  - `check_prefix` checks prefix and serving parity on every row with the real tokenizer.
  - Rows with think tags are excluded.
- **Memory on the L40S.** TRL 1.16 patches a fused, chunked LM head (`add_fused_lm_head`), and it works with the remote NemotronH: `base_model` resolves to `backbone`, which returns `last_hidden_state`, and `get_output_embeddings` returns `lm_head`. The 8×2048×131k fp32 logits are never built. Gradient checkpointing is on by default and the remote code supports it (`supports_gradient_checkpointing=True`); `enable_input_require_grads` is applied for PEFT.
- **Precision and padding.** bf16 on the L40S is correct. With pad token None, TRL falls back to `<|im_end|>`, and labels come from the mask, so EOS stays in the loss. Right padding is safe for the Mamba layers (`apply_mask_to_padding_states`).
- **Data rules:**
  - Every night retrains from the base on the cumulative data.
  - Targets are teacher answers or human labels only (INV-2), and only complete, valid, reasoning-stripped teacher answers are stored (INV-16, `router.answer_problem`, `_store_example`).
  - Golden and train are split by prompt hash across tasks (ADR 0131). Golden-only rows, the FREQUENT rule, rows with a client stop, rows invalid under the spec, pinned tasks and contested labels are all handled.
  - Exact duplicates collapse into one row.
  - Calibration uses the even golden rows and measurement the odd ones (INV-6), and the champion is re-scored first (INV-4).
- **Serving compatibility.**
  - The student is served the same raw messages that were stored, before redaction.
  - Rank 16 fits the endpoint's `--max-lora-rank 16`.
  - vLLM v0.18's `NemotronHForCausalLM` is `SupportsLoRA` with a qkv packed mapping. The Mamba `in_proj` is a 5-slice `MergedColumnParallelLinear`, for which vLLM has a variable-slice LoRA class. That looks workable, but still needs confirming on day 1, as the repo says.
- **Image.** The training image pins exact versions and fails its build when the Mamba kernels do not import. TRL 1.16 and transformers 4.57.6 are compatible, and the model's `generation_config` was saved with 4.57.1.
