#!/usr/bin/env bash
# Run one Night School training job on Nebius Serverless (AI Jobs).
#   bash deploy/train_job.sh student-v3
#
# Needs: nebius CLI logged in, a bucket with the dataset at <bucket>/night/train.jsonl,
# and a training image (see deploy/Dockerfile.train).
# VERIFY ON DAY 1 with `nebius ai create --help`: flags below follow the Nebius Serverless
# quickstart and fine-tuning tutorial as of Oct 2026.
set -euo pipefail

VERSION="${1:?usage: train_job.sh <version-name>}"
: "${RATCHET_BUCKET_ID:?set RATCHET_BUCKET_ID (bucket mounted at /data)}"
IMAGE="${RATCHET_TRAIN_IMAGE:?set RATCHET_TRAIN_IMAGE (built from deploy/Dockerfile.train)}"
BASE="${RATCHET_STUDENT_BASE_MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16}"
PLATFORM="${RATCHET_TRAIN_PLATFORM:-gpu-l40s-a}"
PRESET="${RATCHET_TRAIN_PRESET:-1gpu-8vcpu-32gb}"

nebius ai create --type job \
  --name "ratchet-${VERSION}" \
  --image "${IMAGE}" \
  --platform "${PLATFORM}" --preset "${PRESET}" \
  --volume "${RATCHET_BUCKET_ID}:/data" \
  --disk-size 200Gi \
  --timeout 2h \
  --container-command bash \
  --args "-c 'python -m ratchet.nightschool.train --data /data/night/train.jsonl --base ${BASE} --out /data/adapters/${VERSION}'"

# Wait until the adapter lands in the bucket (S3-compatible Nebius Object Storage).
if [[ -n "${RATCHET_BUCKET_S3:-}" ]]; then
  echo "Waiting for ${RATCHET_BUCKET_S3}/adapters/${VERSION}/adapter_model.safetensors ..."
  for _ in $(seq 1 240); do
    if aws s3 ls "${RATCHET_BUCKET_S3}/adapters/${VERSION}/adapter_model.safetensors" \
         ${RATCHET_S3_ENDPOINT:+--endpoint-url "$RATCHET_S3_ENDPOINT"} >/dev/null 2>&1; then
      echo "Adapter ready."; exit 0
    fi
    sleep 30
  done
  echo "Timed out waiting for the adapter." >&2; exit 1
fi
echo "Submitted ratchet-${VERSION}. Set RATCHET_BUCKET_S3 to make this script wait for the adapter."
