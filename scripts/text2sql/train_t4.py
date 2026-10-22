"""Runs ON the Colab T4: compile or install the Mamba kernels, train a LoRA adapter in 4-bit fp16, then answer the
held-out rows with the base model and the adapter (scripts/measure_adapter.py generate). Writes /content/package.zip.

Env: MODE=real|pipeline-test, EPOCHS (default 2), MAX_STEPS (default 0 = use epochs), MAX_SEQ (default 768).
Everything it does is printed with a [stage] prefix, so the laptop log shows where it is."""

import gc
import glob
import json
import os
import shutil
import subprocess
import sys
import time

BASE = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
MODE = os.environ.get("MODE", "pipeline-test")
EPOCHS = float(os.environ.get("EPOCHS", "2"))
MAX_STEPS = int(os.environ.get("MAX_STEPS", "0"))
MAX_SEQ = int(os.environ.get("MAX_SEQ", "768"))
OUT = "/content/adapter"
T0 = time.time()


def log(msg):
    print(f"[stage {int(time.time() - T0):>5}s] {msg}", flush=True)


def sh(cmd, env=None, quiet=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if not quiet:
        print((r.stdout + r.stderr)[-1500:], flush=True)
    return r.returncode


# ---- 1. Mamba kernels: cached wheels first, else build (slow on 2 CPU cores) and hand the wheels back for caching
log("kernels")
os.makedirs("/content/wheels", exist_ok=True)
have = sh("python -c 'import mamba_ssm, causal_conv1d'", quiet=True) == 0
if not have and glob.glob("/content/wheels/*.whl"):
    sh("pip install -q --no-deps /content/wheels/*.whl")
    have = sh("python -c 'import mamba_ssm, causal_conv1d'", quiet=True) == 0
if not have:
    env = {"TORCH_CUDA_ARCH_LIST": "7.5", "MAX_JOBS": "2", "CAUSAL_CONV1D_FORCE_BUILD": "TRUE", "MAMBA_FORCE_BUILD": "TRUE"}
    sh("pip install -q ninja packaging wheel", quiet=True)
    for pkg in ("causal-conv1d==1.6.2.post1", "mamba-ssm==2.3.2.post1"):
        if glob.glob("/content/wheels/" + pkg.split("==")[0].replace("-", "_") + "-*.whl"):
            log(f"{pkg}: a built wheel is already here, skipping its build")
            continue
        log(f"building {pkg}")
        rc = sh(f"pip wheel --no-build-isolation --no-deps -q {pkg} -w /content/wheels", env=env)
        if rc:
            log(f"BUILD FAILED {pkg}")
            sys.exit(2)
    sh("pip install -q --no-deps /content/wheels/*.whl")
    have = sh("python -c 'import mamba_ssm, causal_conv1d'", quiet=True) == 0
for w in sorted(glob.glob("/content/wheels/*.whl")):
    print("WHEEL:" + w, flush=True)
if not have:
    log("kernels do not import")
    sys.exit(3)
log("kernels ok")

# ---- 2. Train
import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # noqa: E402
from trl import SFTConfig, SFTTrainer  # noqa: E402

rows = [json.loads(line) for line in open("/content/train.jsonl")]
log(f"{MODE}: {len(rows)} training rows on {torch.cuda.get_device_name(0)}")
tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
r0 = rows[0]
p = tok.apply_chat_template(r0["prompt"], tokenize=False, add_generation_prompt=True, **r0["chat_template_kwargs"])
f = tok.apply_chat_template(
    r0["prompt"] + r0["completion"], tokenize=False, add_generation_prompt=False, **r0["chat_template_kwargs"]
)
assert f.startswith(p), "the training rendering is not a prefix extension of the serving prompt"
bnb = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
    llm_int8_skip_modules=["in_proj", "out_proj", "lm_head"],
)  # the Mamba kernels read these weights directly, so they stay fp16
model = AutoModelForCausalLM.from_pretrained(
    BASE, trust_remote_code=True, quantization_config=bnb, device_map="auto", dtype=torch.float16
)
log(f"loaded, peak memory {torch.cuda.max_memory_allocated() / 2**30:.1f} GB")
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
model.enable_input_require_grads()
model = get_peft_model(
    model,
    LoraConfig(
        r=16,
        lora_alpha=32,
        task_type="CAUSAL_LM",
        target_modules=os.environ.get("TARGETS", "q_proj,k_proj,v_proj,o_proj,up_proj,down_proj").split(","),
    ),
)
args = SFTConfig(
    output_dir="/content/ckpt",
    num_train_epochs=EPOCHS,
    max_steps=MAX_STEPS or -1,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    max_length=MAX_SEQ,
    logging_steps=5,
    save_strategy="steps",
    save_steps=50,
    save_total_limit=2,
    fp16=True,
    bf16=False,
    gradient_checkpointing=True,
    completion_only_loss=True,
    report_to=[],
)
tr = SFTTrainer(model=model, processing_class=tok, train_dataset=Dataset.from_list(rows), args=args)
log("training")
res = tr.train()
losses = [h["loss"] for h in tr.state.log_history if "loss" in h]
log(
    f"trained: loss {losses[:1]} -> {losses[-1:]} in {res.metrics.get('train_runtime', 0):.0f}s, "
    f"peak {torch.cuda.max_memory_allocated() / 2**30:.1f} GB"
)
os.makedirs(OUT, exist_ok=True)
model.save_pretrained(OUT)
tok.save_pretrained(OUT)
import importlib.metadata as m  # noqa: E402

json.dump(
    {
        "mode": MODE,
        "gpu": torch.cuda.get_device_name(0),
        "base": BASE,
        "rows": len(rows),
        "epochs": EPOCHS,
        "max_steps": MAX_STEPS,
        "loss_first": losses[:1],
        "loss_last": losses[-1:],
        "train_seconds": res.metrics.get("train_runtime"),
        "peak_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "versions": {
            x: (m.version(x) if x else None)
            for x in ("torch", "transformers", "trl", "peft", "bitsandbytes", "mamba-ssm", "causal-conv1d")
        },
    },
    open(f"{OUT}/run_info.json", "w"),
    indent=2,
)
log("adapter saved")

# ---- 3. Measure: raw answers on the held-out rows, base and base+adapter
del tr, model
gc.collect()
torch.cuda.empty_cache()
rc = sh(
    "python /content/measure_adapter.py generate --golden /content/golden.jsonl "
    f"--adapter {OUT} --out /content/raw.jsonl --max-new {os.environ.get('MAX_NEW', '160')}"
)
log(f"measure generate rc={rc}")
shutil.make_archive("/content/package", "zip", OUT)
print("PACKAGE_READY", flush=True)
sys.exit(0 if rc == 0 else 4)
