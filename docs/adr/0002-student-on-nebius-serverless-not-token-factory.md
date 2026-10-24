# ADR 0002: Train and serve the student on Nebius AI Cloud, not Token Factory

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

The hackathon requires running on Nebius Token Factory or Nebius AI Cloud with an NVIDIA open model. The natural plan was to fine-tune and host the student on Token Factory. Research on 2026-10-19 found:

- Token Factory fine-tuning supports Qwen, Llama, gpt-oss, Gemma 4 and DeepSeek, but **no Nemotron** models.
- Token Factory serverless LoRA serving accepts only `meta-llama/Llama-3.1-8B-Instruct` and `Llama-3.3-70B-Instruct` as base models.
- Custom weights on dedicated endpoints are beta and on request, with undocumented architectures.
- Nemotron 3 Nano 30B-A3B needs about 60 GB of VRAM for LoRA (Unsloth). `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` is commercially usable, served by vLLM 0.15.1 or later, and fits a single L40S.

## Decision

- **Student:** `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` with LoRA (rank 16).
- **Training:** a Nebius **Serverless Job** (`deploy/train_job.sh`, image from `deploy/Dockerfile.train`, `gpu-l40s-a`) running `ratchet.nightschool.train` (Unsloth, falling back to transformers + PEFT).
- **Serving:** a Nebius **Serverless Endpoint** with `vllm/vllm-openai:v0.18.0-cu130`, `--enable-lora`, and runtime adapter loading (`VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`). `ratchet.live.load_adapter` calls `/v1/load_lora_adapter` after each training run. Each adapter is addressed by its name as the `model` field.
- **Token Factory keeps:** the teacher (Ultra), the judge (Super), and the hosted untuned fallback student (Nano 30B) used when the endpoint is offline.

## Consequences

- Positive: uses more of the Nebius stack (Token Factory, Serverless Jobs, Serverless Endpoints, Object Storage), which matches the judging panel.
- Positive: the customer gets real adapter files they can download and run elsewhere.
- Negative: more moving parts and GPU-hour cost. Runtime-loaded adapters vanish on restart (see [RUNBOOK.md](../RUNBOOK.md)).
- Risk: Nemotron-H (hybrid Mamba) LoRA training and serving are unverified on this stack. The day-1 checks are in `python scripts/day1_check.py`.

## Alternatives considered

- **A Llama-8B student on Token Factory serverless LoRA:** the easiest serving path, but the student would not be an NVIDIA model, and the NVIDIA story weakens. Kept as a fallback.
- **Nemotron Nano 30B student on H100:** better quality, about 3× the GPU cost, and H100 is available only in eu-north1. A stretch goal.
- **Merging adapters and serving one endpoint per version:** simpler with vLLM but slower to promote. This is the fallback if runtime LoRA fails.
