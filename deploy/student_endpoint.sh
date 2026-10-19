#!/usr/bin/env bash
# Serve the student on a Nebius Serverless Endpoint with vLLM + LoRA hot-loading.
#   bash deploy/student_endpoint.sh
#
# New adapters are loaded at runtime by ratchet.live (POST /v1/load_lora_adapter),
# so promotions need no restart. Stop the endpoint when idle: stopped endpoints cost nothing.
# VERIFY ON DAY 1 with `nebius ai endpoint create --help`.
set -euo pipefail

: "${RATCHET_BUCKET_ID:?set RATCHET_BUCKET_ID (bucket with /adapters, mounted at /data)}"
: "${RATCHET_STUDENT_KEY:?set RATCHET_STUDENT_KEY (token clients must send)}"
BASE="${RATCHET_STUDENT_BASE_MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16}"
PLATFORM="${RATCHET_SERVE_PLATFORM:-gpu-l40s-a}"
PRESET="${RATCHET_SERVE_PRESET:-1gpu-8vcpu-32gb}"

nebius ai endpoint create \
  --name ratchet-student \
  --image vllm/vllm-openai:v0.18.0-cu130 \
  --platform "${PLATFORM}" --preset "${PRESET}" \
  --container-port 8000 \
  --shm-size 16Gi \
  --volume "${RATCHET_BUCKET_ID}:/data" \
  --auth token --token "${RATCHET_STUDENT_KEY}" \
  --env VLLM_ALLOW_RUNTIME_LORA_UPDATING=True \
  --args "--model ${BASE} --trust-remote-code --enable-lora --max-lora-rank 16 --max-loras 4"
