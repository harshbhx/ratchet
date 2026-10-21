#!/usr/bin/env bash
# Run one Night School training job on Nebius Serverless (AI Jobs).
#   bash deploy/train_job.sh student-v3
#   DRY_RUN=1 bash deploy/train_job.sh student-v3   # nebius validates the request, submits nothing
#
# Needs: nebius CLI logged in, a bucket with the dataset at <bucket>/night/train.jsonl,
# and a training image (see deploy/Dockerfile.train).
# Optional: RATCHET_SUBNET_ID (default: the project's default subnet) and RATCHET_REGISTRY_SECRET
# (a MysteryBox secret holding the registry credentials, for a private image).
# Flags follow the Nebius CLI reference for `nebius ai job create` (auto generated on 19-Oct-2026,
# read Oct 21, 2026) and Nebius's L40S fine-tuning tutorial. `nebius ai create` is the interactive
# wizard; scripts use `nebius ai <type> create`.
# `--timeout 2h` is the job's own flag (1h to 168h, default 24h; on `job create` it replaces the CLI's
# global request timeout of the same name): it caps a stuck job's GPU time at the 2 h this script waits.
# VERIFY ON DAY 1 with `nebius ai job create --help`, and run `DRY_RUN=1 bash deploy/train_job.sh test`.
set -euo pipefail

VERSION="${1:?usage: train_job.sh <version-name>}"
: "${RATCHET_BUCKET_ID:?set RATCHET_BUCKET_ID (bucket mounted at /data)}"
IMAGE="${RATCHET_TRAIN_IMAGE:?set RATCHET_TRAIN_IMAGE (built from deploy/Dockerfile.train)}"
BASE="${RATCHET_STUDENT_BASE_MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16}"
PLATFORM="${RATCHET_TRAIN_PLATFORM:-gpu-l40s-a}"
PRESET="${RATCHET_TRAIN_PRESET:-1gpu-8vcpu-32gb}"
DRY_RUN="${DRY_RUN:-0}"

# Anything but 0 or 1 is refused: DRY_RUN=true must not submit a paid job because it is not "1".
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || { echo "error: DRY_RUN must be 0 or 1, not ${DRY_RUN}" >&2; exit 2; }
# Both go into the job's `bash -c '...'` line, where a space or a quote would break it or run something else.
[[ "${VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "error: bad version name: ${VERSION}" >&2; exit 2; }
[[ "${BASE}" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || { echo "error: bad RATCHET_STUDENT_BASE_MODEL: ${BASE}" >&2; exit 2; }

CMD=(nebius ai job create
  --name "ratchet-${VERSION}"
  --image "${IMAGE}"
  --platform "${PLATFORM}" --preset "${PRESET}"
  --volume "${RATCHET_BUCKET_ID}:/data"
  --disk-size 200Gi
  --timeout 2h
  --container-command bash
  --args "-c 'python -m ratchet.nightschool.train --data /data/night/train.jsonl --base ${BASE} --out /data/adapters/${VERSION}'")
if [[ -n "${RATCHET_SUBNET_ID:-}" ]]; then CMD+=(--subnet-id "${RATCHET_SUBNET_ID}"); fi
if [[ -n "${RATCHET_REGISTRY_SECRET:-}" ]]; then CMD+=(--registry-secret "${RATCHET_REGISTRY_SECRET}"); fi
if [[ "${DRY_RUN}" == "1" ]]; then CMD+=(--dry-run); fi

echo "+ ${CMD[*]}" >&2
"${CMD[@]}"
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "DRY_RUN=1: nebius validated the job request; nothing was submitted."
  exit 0
fi

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
