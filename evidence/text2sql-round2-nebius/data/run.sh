#!/usr/bin/env bash
# Round-2 retrain runner for a Nebius Serverless Job. RUN_MODE=smoke|full. Inputs are in /data/inputs, outputs go to /data/$OUTDIR.
set -uo pipefail
MODE="${RUN_MODE:-full}"   # smoke | full | combo (combo = 3-step smoke, then the full run in the same job)
if [ "$MODE" = smoke ]; then OUTDIR=r2-nebius-smoke; else OUTDIR=r2-nebius; fi
ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
WH=/data/wheels-$ARCH
O=/data/$OUTDIR; mkdir -p $O $WH
N=$(cat $O/starts 2>/dev/null || echo 0); N=$((N+1)); echo $N > $O/starts
if [ $N -gt 4 ]; then echo "start number $N: too many restarts, giving up (exit 0 so the job is not restarted again)"; exit 0; fi
T0=$(date +%s); ts() { echo "[run $(( $(date +%s) - T0 ))s] $*"; }
exec > >(tee -a /content-run.log) 2>&1
ts "start mode=$MODE job_name=${JOB_NAME:-?} host=$(hostname)"
mkdir -p /content/wheels /content/hf
( while true; do sleep 60; cp /content-run.log $O/run-$T0.log 2>/dev/null; [ -f /content/train.log ] && cp /content/train.log $O/train-$T0.log 2>/dev/null; done ) &
trap 'cp /content-run.log $O/run-$T0.log 2>/dev/null; cp /content/train.log $O/train-$T0.log 2>/dev/null' EXIT
nvidia-smi > $O/nvidia-smi.txt 2>&1; nvidia-smi | head -15
python --version; python -c "import torch;print('torch',torch.__version__,torch.version.cuda)"
ts "apt"
(apt-get update -qq && apt-get install -y -qq git ninja-build build-essential python3-venv) > /content/apt.log 2>&1 || { ts "apt problem"; tail -5 /content/apt.log; }
ts "venv (system python is externally managed, PEP 668)"
if python -m venv --system-site-packages /content/venv > /content/venv.log 2>&1; then export PATH=/content/venv/bin:$PATH; ts "using /content/venv"; else ts "venv failed: $(tail -2 /content/venv.log); using --break-system-packages"; export PIP_BREAK_SYSTEM_PACKAGES=1; fi
[ -d /usr/local/cuda ] && export CUDA_HOME=/usr/local/cuda
echo "python=$(which python) pip=$(which pip) nvcc=$(which nvcc) CUDA_HOME=${CUDA_HOME:-}"; python --version
ts "pip pins (round 2)"
pip install -q transformers==5.20.1 trl==1.16.0 peft==0.21.2 bitsandbytes==0.50.2 accelerate datasets packaging wheel ninja setuptools einops sentencepiece protobuf 2>&1 | tail -15
python - <<'PY' || { ts "PIN INSTALL FAILED: versions are not round 2's, stopping"; exit 1; }
import torch, transformers, trl, peft, bitsandbytes
want = {"torch": "2.11.0+cu128", "transformers": "5.20.1", "trl": "1.16.0", "peft": "0.21.2", "bitsandbytes": "0.50.2"}
got = {"torch": torch.__version__, "transformers": transformers.__version__, "trl": trl.__version__, "peft": peft.__version__, "bitsandbytes": bitsandbytes.__version__}
print("versions", got)
assert got == want, (got, want)
PY
pip freeze 2>/dev/null | grep -iE "^(transformers|trl|peft|bitsandbytes|mamba|causal|torch|accelerate|datasets)" | tee $O/pip-key-versions.txt
cp /data/inputs/train_lora.py /data/inputs/fast_generate.py /content/
cp /data/inputs/train.jsonl /content/train.jsonl; cp /data/inputs/golden1.jsonl /content/golden.jsonl
cp /data/inputs/golden2.jsonl /content/golden2.jsonl
ts "restore cached wheels ($ARCH): $(ls $WH 2>/dev/null | tr '\n' ' ')"
cp $WH/*.whl /content/wheels/ 2>/dev/null
export HF_HOME=/content/hf TOKENIZERS_PARALLELISM=false TRITON_CACHE_DIR=/content/hf/triton CC=gcc CXX=g++
# Kernels: same pins as round 2, built for this GPU (L40S sm_89, H100 sm_90) instead of the T4 (sm_75); train_lora.py would otherwise build for 7.5.
export TORCH_CUDA_ARCH_LIST=$ARCH MAX_JOBS=6 CAUSAL_CONV1D_FORCE_BUILD=TRUE MAMBA_FORCE_BUILD=TRUE
for pkg in causal-conv1d==1.6.2.post1 mamba-ssm==2.3.2.post1; do
  n=$(echo ${pkg%%==*} | tr - _)
  if ls /content/wheels/${n}-*.whl >/dev/null 2>&1; then ts "wheel present: $pkg"; continue; fi
  ts "building $pkg"
  pip wheel --no-build-isolation --no-deps -q $pkg -w /content/wheels 2>&1 | tail -5 || true
  cp /content/wheels/*.whl $WH/ 2>/dev/null
done
ls -la /content/wheels; ts "kernels done"
ls /content/wheels/causal_conv1d-*.whl /content/wheels/mamba_ssm-*.whl > /dev/null || { ts "KERNEL BUILD FAILED"; exit 1; }
cp /content/wheels/*.whl $WH/ 2>/dev/null
# train (round-2 settings: MODE=real DEFER_GEN=1 EPOCHS=2 MAX_SEQ=1536)
if [ "$MODE" = combo ]; then
  ts "combo: smoke train first (SMOKE=1, 3 steps)"
  SMOKE=1 MODE=real DEFER_GEN=1 EPOCHS=2 MAX_SEQ=1536 python -u /content/train_lora.py > /content/train_smoke.log 2>&1; SRC=$?
  mkdir -p $O; cp /content/train_smoke.log $O/train_smoke.log; tail -c 1500 /content/train_smoke.log
  ts "combo smoke rc=$SRC"
  if [ $SRC -ne 0 ] || [ ! -f /content/adapter_smoke/adapter_config.json ]; then ts "COMBO SMOKE FAILED, stopping"; exit 1; fi
  ts "combo smoke passed, continuing to the full run"
fi
[ "$MODE" = smoke ] && export SMOKE=1
if [ "$MODE" != smoke ]; then
  # The bucket mount cannot take safetensors' own writes, so checkpoints are written locally and copied to the bucket.
  mkdir -p $O/ckpt; rm -rf /content/ckpt; mkdir -p /content/ckpt
  last=$(ls $O/ckpt 2>/dev/null | grep -E '^checkpoint-[0-9]+[.]ok$' | sed -e 's/^checkpoint-//' -e 's/[.]ok$//' | sort -n | tail -1)
  if [ -n "$last" ]; then cp -r $O/ckpt/checkpoint-$last /content/ckpt/ && ts "restored checkpoint-$last from the bucket"; fi
  ( while true; do sleep 45
      for d in /content/ckpt/checkpoint-*; do
        [ -f $d/trainer_state.json ] || continue; b=$(basename $d); [ -f $O/ckpt/$b.ok ] && continue
        sleep 25; rm -rf $O/ckpt/$b; if cp -r $d $O/ckpt/$b 2>/dev/null; then touch $O/ckpt/$b.ok; echo "[sync] $b copied to the bucket"; fi
        for old in $(ls $O/ckpt | grep -E '^checkpoint-[0-9]+\.ok$' | sed 's/\.ok//' | sort -t- -k2 -n | head -n -2); do rm -rf $O/ckpt/$old $O/ckpt/$old.ok; done
      done; done ) &
fi
if [ "$MODE" != smoke ] && [ -f $O/adapter/adapter_config.json ]; then
  ts "adapter already in the bucket (restart): skipping training"; mkdir -p /content/adapter; cp $O/adapter/* /content/adapter/; RC=0
else
  ts "train start (checkpoints in the bucket, resumes if any)"
  MODE=real DEFER_GEN=1 EPOCHS=2 MAX_SEQ=1536 python -u /content/train_lora.py >> /content/train.log 2>&1
  RC=$?; ts "train rc=$RC"; tail -c 3000 /content/train.log
fi
ADIR=/content/adapter; [ "$MODE" = smoke ] && ADIR=/content/adapter_smoke
if [ $RC -ne 0 ] || [ ! -f $ADIR/adapter_config.json ]; then ts "TRAIN FAILED"; cp /content/train.log $O/train-$T0.log; exit 1; fi
mkdir -p $O/adapter && cp $ADIR/* $O/adapter/ && ts "adapter copied"; sha256sum $O/adapter/adapter_model.safetensors | tee $O/adapter_sha256.txt
cp /content/train.log $O/train-$T0.log
ts "exam 1"
if [ "$MODE" = smoke ]; then LIM="--limit 3"; else LIM=""; fi
python -u /content/fast_generate.py --golden /content/golden.jsonl --adapter $ADIR --out $O/raw_exam1.jsonl $LIM > $O/exam1.log 2>&1; ts "exam1 rc=$?"; tail -3 $O/exam1.log
ts "exam 2"
python -u /content/fast_generate.py --golden /content/golden2.jsonl --adapter $ADIR --out $O/raw_exam2.jsonl $LIM > $O/exam2.log 2>&1; ts "exam2 rc=$?"; tail -3 $O/exam2.log
pip freeze > $O/pip-freeze.txt 2>&1; nvidia-smi > $O/nvidia-smi-end.txt 2>&1
ts "ALL DONE"; echo "ALL_DONE total_seconds=$(( $(date +%s) - T0 ))" > $O/DONE.txt