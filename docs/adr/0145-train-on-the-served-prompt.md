# ADR 0145: Train on the prompt the student is served, in a pinned training image

- Status: Accepted
- Date: 2026-10-21

## Context

Status: the Nebius Serverless Job and Endpoint scripts in `deploy/` are written and offline-tested, ready to run with `make nebius-plan`. The adapters behind the measured numbers in [evidence/](../../evidence/) were trained on a borrowed GPU. The teacher and the judge do run on Nebius Token Factory. Simulation numbers are labelled Simulation.

The first GPU night trains the student that every later number depends on. Three facts, read on Oct 21, 2026, meant it would have trained on one prompt and been served another, with no error:

- **Nemotron 3 Nano 4B's chat template turns thinking on unless told otherwise** ([chat_template.jinja](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/raw/main/chat_template.jinja), repository commit dfaf35d, sha256 `ab7813c3…7862`, vendored as `tests/fixtures/nano4b_chat_template.jinja`). With `add_generation_prompt` the prompt ends in `<|im_start|>assistant\n<think>\n` (line 200); with `enable_thinking=False` it ends in `<|im_start|>assistant\n<think></think>` (line 202). An assistant turn without think tags is rendered as `<think></think>` followed by its content (lines 113-114).
- **The endpoint serves with thinking off.** `deploy/student_endpoint.sh` starts vLLM with `--default-chat-template-kwargs '{"enable_thinking":false}'` (W0), so every student call is rendered with the second prompt.
- **TRL only warns when training and serving prompts differ.** TRL v1.16.0's SFT tokenizer renders a conversational prompt-completion row twice, the prompt with `add_generation_prompt=True` and prompt+completion, merging the row's `chat_template_kwargs` into both calls. When the prompt is not a prefix of prompt+completion it logs one warning ("The model is trained on a context that differs from the one it sees at inference") and trains from where the two diverge (`trl/trainer/sft_trainer.py` lines 1314-1365, tag v1.16.0).

`train.py` built `{"messages": ...}` rows with `assistant_only_loss=True`. That needs `{% generation %}` markers the template does not have, and it renders with the template default, so training would have seen `...<think>` where serving sends `...<think></think>`. The image made it worse: `deploy/Dockerfile.train` installed unpinned `unsloth` and `trl>=0.19`, hid a failed Mamba kernel build behind `|| true` (the model then runs a slow fallback or fails on the GPU), and had no `python` command, which the job runs (Ubuntu 24.04 ships only `python3`).

Unsloth cannot join a TRL 1.16.0 image: its release of Oct 19, 2026 (2026.10.7) requires `trl<=1.15.0`, `transformers<=5.17.0` and `datasets<5.0.0` (PyPI metadata, read Oct 21, 2026).

## Decision

1. **Rows.** `dataset.write_training` (used by `NebiusTrainer` and `dataset.build`) writes TRL's conversational prompt-completion rows: `prompt` (the stored messages), `completion` (one assistant message: the teacher's stored answer, INV-2) and `chat_template_kwargs` = `dataset.TRAIN_TEMPLATE_KWARGS` = `{"enable_thinking": false}`, keeping `task`, `params`, `seen` and `provenance`. `dataset.rows` and every in-memory consumer (the golden eval, the set commitments, the BOM) keep their `messages` rows; `golden.jsonl` stays in that format, since nothing trains on it.
2. **One set of arguments in three places.** `dataset.TRAIN_TEMPLATE_KWARGS`, `train.TEMPLATE_KWARGS` (a copy: the training image has no httpx, so `train.py` imports nothing from the package) and the endpoint's `DEFAULT_TEMPLATE_KWARGS` are kept equal by a test.
3. **Answers with a literal think tag are not training targets.** The template skips the empty think block for an assistant turn that contains `<think>` or `</think>` (case-sensitive, as the template tests it), so its prompt would not be a prefix either. `dataset.rows` leaves such train rows out and counts them (`skipped_think_tag` in `manifest.json`, and in the night's event when non-zero), before the set commitment, so the signed BOM commits to exactly what is trained (INV-14). They stay golden rows: the eval compares answers and renders no template.
4. **`train.py` refuses to train another prompt.** `check_rows` stops the job before the model loads on a row that is not prompt-completion, lacks the arguments, has a think tag or no provenance, naming the row and the field, never the text. After the tokenizer loads, `check_prefix` renders every row as TRL will (from the `datasets` rows, which give each message every key any message has, as None) and stops on a prompt that is not a prefix of prompt+answer, or that differs from the prompt rendered from the written messages with the endpoint's arguments. Loss: `completion_only_loss=True` (TRL's default for these rows, set explicitly), never `assistant_only_loss`.
5. **A pinned image that fails loudly.** `deploy/Dockerfile.train` installs exact versions: torch 2.11.0 (the newest on the CUDA 12.8 index), transformers 4.57.6 (the last 4.x; TRL needs 4.56.2 or later, and the model's own code was saved with 4.53.0), TRL 1.16.0, PEFT 0.21.2, Accelerate 1.15.0, datasets 4.8.5, causal-conv1d 1.6.2.post1 and mamba-ssm 2.3.2.post1 (released together), and pinned build tools. `deploy/train-versions.txt` lists the same pins and every pip step after torch reads it as constraints, so a disagreement or a dependency that would move a pinned package fails the build. The Mamba line has no `|| true`, and a last `RUN python -c "import torch, mamba_ssm, causal_conv1d; ..."` (which also checks torch's CUDA build) fails the build when the kernels are missing. `python-is-python3` gives the job its `python`. Unsloth is removed from the image and from `train.py`, which replaces the training library [ADR 0002](0002-student-on-nebius-serverless-not-token-factory.md) names (Unsloth, falling back to transformers + PEFT).
6. **Versions travel with the adapter.** `train.py` writes `versions.json` (Python, torch's CUDA version and every pinned package) beside the adapter before training starts, and the chat-template arguments into `ratchet_manifest.json`. They are not copied into the signed BOM: `bom.EXTRA_KEYS` has no section for them, and a stored BOM is never rewritten (INV-14).
7. **LoRA targets wait for the day-1 probe.** `--targets` keeps the Mamba projections by default (`TARGETS_WITH_MAMBA`), the set the runtime-LoRA probe must test; `TARGETS_ATTENTION_MLP` is the one-line fallback if vLLM refuses them.

The versions are candidates read from PyPI and download.pytorch.org on Oct 21, 2026. They have not been built or run on Nebius: the 200-row smoke job (`bash deploy/train_job.sh test`) is still to do, and its `versions.json` replaces these pins in both files together.

## Consequences

- The student is trained on exactly the prompt vLLM renders for it, and a template, tokenizer or argument drift stops the job in seconds instead of training a quietly worse adapter. The parity is tested offline with the vendored template (jinja2, now in the `dev` extra).
- A train row whose answer quotes a think tag is not trained on. Such answers are rare (stored answers are cleaned of reasoning); the count is visible.
- The image takes longer to change: every version bump touches two files, and the build fails on any conflict. That is the point.
- The image is not built or run offline. Whether the pins resolve together, whether a prebuilt Mamba wheel exists for torch 2.11 and CUDA 12 (otherwise the kernels compile from source in the devel image), and whether the import check runs on a builder without a GPU driver are still unchecked.
- `versions.json` is evidence beside the weights, not part of the signed BOM.

## Alternatives considered

- **Render the rows to text in `train.py` and give TRL text prompt-completion pairs.** Works under any TRL version and gives exact control, but duplicates TRL's own rendering and breaks the row contract every other lane builds on; the conversational rows with per-row arguments are what TRL documents, and `check_prefix` gives the same guarantee with the real tokenizer.
- **Keep `assistant_only_loss` with TRL's bundled training template.** It depends on the loaded template string matching TRL's copy exactly, trains earlier assistant turns as well, and still renders with thinking on.
- **Unsloth with TRL 1.15.0.** Faster, but Unsloth's constraints also cap transformers and datasets, and the item's pin is TRL 1.16.0.
- **transformers 5.x.** Newer, but the model runs its own code (`trust_remote_code`), saved with 4.53.0; the 4.x line is the smaller step until the smoke job shows otherwise.
- **Fallback images.** If the image cannot build: an NGC PyTorch image (`nvcr.io/nvidia/pytorch`, a CUDA 12.8 tag) as the base, or Nebius's tutorial image `docker.io/axolotlai/axolotl:main-20260309-py3.11-cu128-2.9.1` ([Nebius fine-tuning tutorial](https://docs.nebius.com/serverless/tutorials/fine-tuning), read Oct 21, 2026) with this repository copied in. `versions.json` then records what that image holds.
