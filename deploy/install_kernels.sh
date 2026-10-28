#!/usr/bin/env bash
# Installs the Mamba kernels at job start, the way evidence/text2sql-round2-nebius/data/run.sh did (its lines 38-52):
# restore the wheels cached in the job's bucket for this GPU (/data/wheels-<compute capability>, 8.9 on the L40S), install
# them, and build them with run.sh's flags only when a wheel is missing, then cache the new wheel in the bucket.
# The image holds no wheels and no credentials; the bucket is the job's own volume.
set -uo pipefail
CAUSAL="causal-conv1d==1.6.2.post1"; MAMBA="mamba-ssm==2.3.2.post1"
ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
[[ "${ARCH}" =~ ^[0-9]+\.[0-9]+$ ]] || { echo "error: cannot read the GPU compute capability (${ARCH})" >&2; exit 1; }
WH="${RATCHET_WHEELS_DIR:-/data}/wheels-${ARCH}"; LOCAL=/tmp/wheels
mkdir -p "${WH}" "${LOCAL}"
cp "${WH}"/*.whl "${LOCAL}/" 2>/dev/null
export TORCH_CUDA_ARCH_LIST="${ARCH}" MAX_JOBS=6 CAUSAL_CONV1D_FORCE_BUILD=TRUE MAMBA_FORCE_BUILD=TRUE CC=gcc CXX=g++
for pkg in "${CAUSAL}" "${MAMBA}"; do
  name=$(echo "${pkg%%==*}" | tr - _)
  if ls "${LOCAL}/${name}"-*.whl >/dev/null 2>&1; then echo "wheel present: ${pkg}"; continue; fi
  echo "building ${pkg} for sm_${ARCH} (no cached wheel in ${WH})"
  pip wheel --no-build-isolation --no-deps -q "${pkg}" -w "${LOCAL}"
  cp "${LOCAL}/${name}"-*.whl "${WH}/" 2>/dev/null
done
ls "${LOCAL}"/causal_conv1d-*.whl "${LOCAL}"/mamba_ssm-*.whl >/dev/null || { echo "KERNEL BUILD FAILED" >&2; exit 1; }
pip install --no-deps -q "${LOCAL}"/causal_conv1d-*.whl "${LOCAL}"/mamba_ssm-*.whl
python -c "import torch, mamba_ssm, causal_conv1d; print('kernels ok', torch.__version__)"
