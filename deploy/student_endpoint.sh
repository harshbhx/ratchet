#!/usr/bin/env bash
# Serve the student on a Nebius Serverless Endpoint with vLLM + LoRA hot-loading.
#   bash deploy/student_endpoint.sh          # create (start) the endpoint
#   bash deploy/student_endpoint.sh stop     # stop it: no compute billed while stopped (a mounted bucket still is)
#
# New adapters are loaded at runtime by ratchet.live (POST /v1/load_lora_adapter),
# so promotions need no restart. Stop the endpoint when idle.
# Each path also records the endpoint's uptime in the ledger for the cost report
# (`python -m ratchet.cost endpoint up|down`, FR-15.2). That needs the ledger file (RATCHET_DB)
# on this machine; it is best effort and never fails the deploy. Otherwise run the same command
# where the ledger is, or POST /ratchet/cost/endpoint with an admin key.
# VERIFY ON DAY 1 with `nebius ai endpoint create --help` and `nebius ai endpoint stop --help`.
set -euo pipefail

ACTION="${1:-start}"
NAME="${RATCHET_ENDPOINT_NAME:-ratchet-student}"
BASE="${RATCHET_STUDENT_BASE_MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16}"
PLATFORM="${RATCHET_SERVE_PLATFORM:-gpu-l40s-a}"
PRESET="${RATCHET_SERVE_PRESET:-1gpu-8vcpu-32gb}"

record() {  # best effort: a missing ledger, Python or package must not fail the deploy
  "${RATCHET_PYTHON:-python}" -m ratchet.cost endpoint "$1" --source deploy \
    || echo "note: endpoint $1 not recorded; run 'python -m ratchet.cost endpoint $1' where RATCHET_DB is" >&2
}

case "${ACTION}" in
  start)
    : "${RATCHET_BUCKET_ID:?set RATCHET_BUCKET_ID (bucket with /adapters, mounted at /data)}"
    : "${RATCHET_STUDENT_KEY:?set RATCHET_STUDENT_KEY (token clients must send)}"
    nebius ai endpoint create \
      --name "${NAME}" \
      --image vllm/vllm-openai:v0.18.0-cu130 \
      --platform "${PLATFORM}" --preset "${PRESET}" \
      --container-port 8000 \
      --shm-size 16Gi \
      --volume "${RATCHET_BUCKET_ID}:/data" \
      --auth token --token "${RATCHET_STUDENT_KEY}" \
      --env VLLM_ALLOW_RUNTIME_LORA_UPDATING=True \
      --args "--model ${BASE} --trust-remote-code --enable-lora --max-lora-rank 16 --max-loras 4"
    record up
    ;;
  stop)
    nebius ai endpoint stop --name "${NAME}"
    record down
    ;;
  *)
    echo "usage: bash deploy/student_endpoint.sh [start|stop]" >&2
    exit 2
    ;;
esac
