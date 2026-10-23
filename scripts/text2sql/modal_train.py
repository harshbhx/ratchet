"""Round 2 of the text-to-SQL adapter on Modal: one detached pipeline that trains on a T4, answers the exam, and reports
its progress to a status endpoint a phone can read. No keys are in this file.

  python -m modal run modal_train.py::build                   # once: compile the Mamba kernels for the T4 (done)
  python -m modal run --detach modal_train.py::pipeline       # the real run: train, then answer the exam
  python -m modal run --detach modal_train.py::pipeline --smoke 1   # 3 training steps and 3 exam rows, to prove the path
  python -m modal deploy status_app.py                        # the status endpoint (a separate, small app)

Money: the T4 job costs about USD 0.81 an hour (GPU 0.59, 2 CPU cores 0.095, 16 GiB 0.128). The pipeline keeps the total
GPU seconds of every attempt in a shared dict and stops the child when the training cap is reached, so a restart cannot
spend past it. The exam step has its own timeout.
"""

import os
import re
import subprocess
import threading
import time

import modal

APP = modal.App("ratchet-text2sql")
WHEELS = modal.Volume.from_name("ratchet-wheels", create_if_missing=True)
OUT = modal.Volume.from_name("ratchet-out", create_if_missing=True)
HF_CACHE = modal.Volume.from_name("ratchet-hf-cache", create_if_missing=True)
STATUS = modal.Dict.from_name("ratchet-status", create_if_missing=True)

RATE_USD_PER_H = 0.59 + 2 * 0.0473 + 16 * 0.008  # T4 + 2 cores + 16 GiB, Modal list prices of 23 Oct 2026
TRAIN_CAP_USD = float(os.environ.get("TRAIN_CAP_USD", "7.5"))
EXAM_TIMEOUT_S = 90 * 60

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("git", "ninja-build", "build-essential")
    .pip_install(
        "torch==2.11.0",
        index_url="https://download.pytorch.org/whl/cu128",
        extra_index_url="https://pypi.org/simple",
    )
    .pip_install(
        "transformers==5.20.1",
        "trl==1.16.0",
        "peft==0.21.2",
        "bitsandbytes==0.50.2",
        "accelerate",
        "datasets",
        "packaging",
        "wheel",
        "ninja",
        "einops",
        "sentencepiece",
        "protobuf",
    )
    .env({"HF_HOME": "/hf", "TOKENIZERS_PARALLELISM": "false", "TRITON_CACHE_DIR": "/hf/triton"})
    .add_local_file("train_t4.py", "/content/train_t4.py")
    .add_local_file("fast_generate.py", "/content/fast_generate.py")
    .add_local_file(os.environ.get("TRAIN_FILE", "train_r2.jsonl"), "/content/train.jsonl")
    .add_local_file("golden.jsonl", "/content/golden.jsonl")
)


# ------------------------------------------------------------------------------------------------ status helpers

STEP_RE = re.compile(r"(\d+)/(\d+) \[")
LOSS_RE = re.compile(r"'loss': '?([0-9.]+)")
STAGE_RE = re.compile(r"\[stage\s+\d+s\] (.+)")


def parse_chunk(text: str, st: dict) -> None:
    """Update the status dict from a piece of the child's output (tqdm lines end in a carriage return)."""
    for part in re.split(r"[\r\n]+", text):
        part = part.strip()
        if not part:
            continue
        m = STEP_RE.search(part)
        if m and int(m.group(2)) > 1:
            st["step"], st["total"] = int(m.group(1)), int(m.group(2))
        m = LOSS_RE.search(part)
        if m:
            st["last_loss"] = float(m.group(1))
            hist = st.setdefault("loss_hist", [])
            if not hist or hist[-1][0] != st.get("step", 0):
                hist.append([st.get("step", 0), st["last_loss"]])
            del hist[:-400]
        m = STAGE_RE.search(part)
        if m:
            st["stage"] = m.group(1)[:120]
        if not part.startswith("Warning") and len(part) > 3 and "it/s" not in part and "s/it" not in part:
            tail = st.setdefault("log_tail", [])
            tail.append(part[:200])
            del tail[:-25]


def publish(st: dict) -> None:
    st["updated_at"] = time.time()
    try:
        STATUS["status"] = st
    except Exception as e:  # reporting must never stop the training
        print("status write failed:", type(e).__name__, flush=True)


def spent_usd() -> float:
    try:
        return float(STATUS.get("spent_s", 0.0)) * RATE_USD_PER_H / 3600
    except Exception:
        return 0.0


# ------------------------------------------------------------------------------------------------ build (done once)


@APP.function(image=image, cpu=8, memory=32768, timeout=3 * 3600, volumes={"/content/wheels": WHEELS})
def build():
    """Compile causal-conv1d and mamba-ssm for the T4 (sm_75) and keep the wheels in a volume."""
    import glob

    env = {
        **os.environ,
        "CC": "gcc",
        "CXX": "g++",
        "TORCH_CUDA_ARCH_LIST": "7.5",
        "MAX_JOBS": "8",
        "CAUSAL_CONV1D_FORCE_BUILD": "TRUE",
        "MAMBA_FORCE_BUILD": "TRUE",
    }
    for pkg in ("causal-conv1d==1.6.2.post1", "mamba-ssm==2.3.2.post1"):
        name = pkg.split("==")[0].replace("-", "_")
        if glob.glob(f"/content/wheels/{name}-*.whl"):
            print("already built:", pkg, flush=True)
            continue
        print("building", pkg, flush=True)
        subprocess.run(f"pip wheel --no-build-isolation --no-deps -q {pkg} -w /content/wheels", shell=True, check=True, env=env)
        WHEELS.commit()
    print("WHEELS:", sorted(glob.glob("/content/wheels/*.whl")), flush=True)


# ------------------------------------------------------------------------------------------------ training


def _train_once(smoke: bool, st: dict) -> int:
    """Run train_t4.py as a child, stream its output into the status dict, enforce the cost cap, return its exit code."""
    import shutil

    tag = "_smoke" if smoke else ""
    os.makedirs(f"/out/ckpt{tag}", exist_ok=True)  # checkpoints live in the volume, so a restart resumes
    link = f"/content/ckpt{tag}"
    if not os.path.lexists(link):
        os.symlink(f"/out/ckpt{tag}", link)
    stop = threading.Event()
    state = {"killed": False}

    env = {
        **os.environ,
        "MODE": "real",
        "DEFER_GEN": "1",
        "EPOCHS": os.environ.get("EPOCHS", "2"),
        "MAX_SEQ": os.environ.get("MAX_SEQ", "1536"),
    }
    if smoke:
        env["SMOKE"] = "1"
    child = subprocess.Popen(["python", "-u", "/content/train_t4.py"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def ticker():
        """Every 20 s, whatever the child prints: add the GPU seconds, publish, commit the volume, enforce the cap."""
        last, n = time.time(), 0
        while not stop.wait(20):
            now = time.time()
            try:
                STATUS["spent_s"] = float(STATUS.get("spent_s", 0.0)) + (now - last)
            except Exception:
                pass
            last, n = now, n + 1
            st["spent_usd"] = round(spent_usd(), 3)
            publish(st)
            if n % 15 == 0:  # every 5 minutes
                try:
                    OUT.commit()
                    HF_CACHE.commit()  # the downloaded model and the compiled Triton kernels survive a restart
                except Exception as e:
                    print("volume commit failed:", type(e).__name__, flush=True)
            if st["spent_usd"] >= TRAIN_CAP_USD and not state["killed"]:
                st["note"] = f"stopped at the cost cap of USD {TRAIN_CAP_USD:.2f}"
                state["killed"] = True
                child.kill()

    threading.Thread(target=ticker, daemon=True).start()
    buf = b""
    while True:
        chunk = child.stdout.read1(4096)
        if not chunk:
            break  # end of the child's output
        buf += chunk
        cut = max(buf.rfind(b"\r"), buf.rfind(b"\n")) + 1
        if cut:
            text, buf = buf[:cut].decode("utf-8", "replace"), buf[cut:]
            print(text, end="", flush=True)
            parse_chunk(text, st)
    rc = child.wait()
    stop.set()
    adapter = "/content/adapter_smoke" if smoke else "/content/adapter"
    if os.path.isdir(adapter):
        shutil.copytree(adapter, "/out/" + os.path.basename(adapter), dirs_exist_ok=True)
    OUT.commit()
    HF_CACHE.commit()
    return 99 if state["killed"] else rc


@APP.function(
    image=image,
    gpu="T4",
    cpu=2,
    memory=16384,
    timeout=8 * 3600,
    retries=modal.Retries(max_retries=1, initial_delay=30.0),
    volumes={"/content/wheels": WHEELS, "/out": OUT, "/hf": HF_CACHE},
)
def train(smoke: bool = False) -> int:
    OUT.reload()
    marker = "/out/adapter_smoke/adapter_config.json" if smoke else "/out/adapter/adapter_config.json"
    if os.path.exists(marker):  # a retry after the adapter was saved must not train again
        return 0
    st = dict(STATUS.get("status", {}) or {})
    st.update(phase="training", state="running", attempt=st.get("attempt", 0) + 1, smoke=smoke, cap_usd=TRAIN_CAP_USD)
    st.setdefault("started_at", time.time())
    publish(st)
    if spent_usd() >= TRAIN_CAP_USD:
        st.update(state="stopped", note=f"cost cap of USD {TRAIN_CAP_USD:.2f} already reached")
        publish(st)
        return 99
    rc = _train_once(smoke, st)
    st["spent_usd"] = round(spent_usd(), 3)
    if rc != 0:
        st.update(state="failed" if rc != 99 else "stopped", error=f"training exited with code {rc}")
        publish(st)
        if rc != 99:
            raise RuntimeError(f"training exited with code {rc}")  # lets the one retry resume from the last checkpoint
    return rc


# ------------------------------------------------------------------------------------------------ the exam


@APP.function(
    image=image,
    gpu="T4",
    cpu=2,
    memory=16384,
    timeout=EXAM_TIMEOUT_S,
    volumes={"/content/wheels": WHEELS, "/out": OUT, "/hf": HF_CACHE},
)
def exam(smoke: bool = False) -> int:
    """Answer the 172 exam rows with the base model and the adapter (raw_r2.jsonl), resumable."""
    OUT.reload()
    st = dict(STATUS.get("status", {}) or {})
    st.update(phase="exam", state="running", note="answering the exam: about 8 s a row")
    for k in ("step", "total"):
        st.pop(k, None)
    publish(st)
    sh = subprocess.run("pip install -q --no-deps /content/wheels/*.whl", shell=True, capture_output=True, text=True)
    out = "/out/raw_r2_smoke.jsonl" if smoke else "/out/raw_r2.jsonl"
    adapter = "/out/adapter_smoke" if smoke else "/out/adapter"
    cmd = ["python", "-u", "/content/fast_generate.py", "--golden", "/content/golden.jsonl", "--adapter", adapter, "--out", out]
    if smoke:
        cmd += ["--limit", "3"]
    child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    stop = threading.Event()

    def heartbeat():
        """Every 20 s, whatever the child prints: count the GPU seconds and publish, so the page never looks stale."""
        t = time.time()
        while not stop.wait(20):
            now = time.time()
            try:
                STATUS["spent_s"] = float(STATUS.get("spent_s", 0.0)) + (now - t)
            except Exception:
                pass
            t = now
            st["spent_usd"] = round(spent_usd(), 3)
            publish(st)

    threading.Thread(target=heartbeat, daemon=True).start()
    last = time.time()
    for line in child.stdout:
        print(line, end="", flush=True)
        m = re.match(r"row (\d+)/(\d+)", line)
        if m:
            st["exam_row"], st["exam_total"] = int(m.group(1)), int(m.group(2))
        tail = st.setdefault("log_tail", [])
        tail.append(line.strip()[:200])
        del tail[:-25]
        if time.time() - last > 20:
            last = time.time()
            publish(st)
    rc = child.wait()
    stop.set()
    OUT.commit()
    HF_CACHE.commit()
    if rc != 0:
        st.update(state="failed", error=f"exam answering exited with code {rc}; pip said: {sh.stderr[-200:]}")
    publish(st)
    return rc


# ------------------------------------------------------------------------------------------------ the pipeline


@APP.function(image=modal.Image.debian_slim(), cpu=0.25, memory=512, timeout=16 * 3600)
def pipeline_job(smoke: bool = False) -> None:
    STATUS["status"] = {
        "phase": "starting",
        "state": "running",
        "smoke": smoke,
        "started_at": time.time(),
        "cap_usd": TRAIN_CAP_USD,
        "rows": 4322,
        "note": "round 2: 4,322 rows, 2 epochs, T4",
    }
    st = dict(STATUS["status"])
    try:
        rc = train.remote(smoke)
        if rc != 0:
            raise RuntimeError(f"training ended with code {rc}")
        rc = exam.remote(smoke)
        if rc != 0:
            raise RuntimeError(f"exam answering ended with code {rc}")
        st = dict(STATUS.get("status", {}) or st)
        st.update(phase="done", state="done", note="finished: the adapter and the exam answers are in the volume ratchet-out")
        publish(st)
    except Exception as e:
        st = dict(STATUS.get("status", {}) or st)
        st.update(state="failed", error=f"{type(e).__name__}: {str(e)[:300]}")
        publish(st)
        raise


@APP.local_entrypoint()
def pipeline(smoke: int = 0):
    """Starts the pipeline; with --detach it keeps running after this process ends."""
    call = pipeline_job.spawn(bool(smoke))
    print("started", call.object_id, "- it keeps running without this window")
