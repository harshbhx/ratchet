"""Live-mode Night School on Nebius: train on a Serverless Job, hot-load the adapter into vLLM, evaluate.

    python -m ratchet.live night        # run one night against the live ledger
    python -m ratchet.live tick         # advance the release gate (run every few minutes via cron)
    python -m ratchet.live reload       # re-load live/canary/shadow adapters after an endpoint restart

Environment (in addition to ratchet.config):
    RATCHET_DATA_DIR      local folder synced to the bucket mounted in jobs/endpoints (default ./data)
    RATCHET_SYNC_CMD      command that uploads RATCHET_DATA_DIR to the bucket, e.g.
                          "aws s3 sync ./data s3://my-bucket/ratchet --endpoint-url https://storage.eu-north1.nebius.cloud"
    RATCHET_TRAIN_CMD     command that runs the training job and waits for it; {version} and {data}
                          are substituted, e.g. "bash deploy/train_job.sh {version}"
    RATCHET_ADAPTER_ROOT  where the endpoint sees adapters (default /data/adapters)
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from . import bom
from .config import Settings
from .ledger import Ledger
from .nightschool import dataset
from .nightschool.evaluate import LiveEvaluator
from .nightschool.orchestrate import run_night
from .providers import OpenAICompatProvider, Provider
from .release import ReleaseGate, ReleasePolicy

# The test seams below are default arguments: `run` replaces subprocess.run, `http` the client that talks to vLLM, and
# `provider_factory` the model clients (called as `factory(base_url, api_key, timeout=..., retry=...)`). Production code
# never passes them.
RunFn = Callable[..., Any]
ProviderFactory = Callable[..., Provider]
NO_STUDENT_BASE = "RATCHET_STUDENT_BASE is not set: no student endpoint to load adapters into"


def _run(cmd: str, run: RunFn = subprocess.run) -> None:
    print(f"$ {cmd}", flush=True)
    run(shlex.split(cmd), check=True)


def load_adapter(student_base: str, api_key: str, name: str, path: str, client: httpx.Client | None = None) -> None:
    """Hot-load a LoRA adapter into a running vLLM server. An adapter that is already loaded is not an error.

    Needs the server started with VLLM_ALLOW_RUNTIME_LORA_UPDATING=True and --enable-lora.
    """
    root = student_base.rstrip("/").removesuffix("/v1")
    post = client.post if client is not None else httpx.post
    r = post(
        f"{root}/v1/load_lora_adapter",
        json={"lora_name": name, "lora_path": path},
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=300,
    )
    if r.status_code >= 400 and "already" not in r.text.lower():
        r.raise_for_status()


class NebiusTrainer:
    """Writes the dataset, syncs it to the bucket, runs the Serverless Job, then hot-loads the adapter."""

    def __init__(self, s: Settings, run: RunFn = subprocess.run, http: httpx.Client | None = None):
        self.s = s
        self.run = run
        self.http = http
        self.data = Path(os.environ.get("RATCHET_DATA_DIR", "data"))
        self.sync_cmd = os.environ.get("RATCHET_SYNC_CMD", "")
        self.train_cmd = os.environ.get("RATCHET_TRAIN_CMD", "bash deploy/train_job.sh {version}")
        self.adapter_root = os.environ.get("RATCHET_ADAPTER_ROOT", "/data/adapters")

    def train(self, train_rows: list[dict[str, Any]], base_model: str, name: str) -> str:
        # Check before the sync and the paid GPU job: an adapter with no endpoint to load into cannot be evaluated.
        if not self.s.student_base:
            raise RuntimeError(NO_STUDENT_BASE)
        dataset.write_jsonl(train_rows, self.data / "night" / "train.jsonl")
        if self.sync_cmd:
            _run(self.sync_cmd, self.run)
        _run(self.train_cmd.format(version=name, data=str(self.data)), self.run)
        load_adapter(self.s.student_base, self.s.student_api_key, name, f"{self.adapter_root}/{name}", self.http)
        return name


def build(s: Settings) -> tuple[Ledger, ReleaseGate]:
    led = Ledger(s.db_path, s.golden_fraction, auto_day=True)
    return led, ReleaseGate(led, ReleasePolicy.from_env())


def reload_adapters(s: Settings, led: Ledger, http: httpx.Client | None = None) -> list[str]:
    """vLLM forgets runtime-loaded adapters when the endpoint restarts: load them again."""
    root = os.environ.get("RATCHET_ADAPTER_ROOT", "/data/adapters")
    names = [v.adapter_uri for v in led.versions(["live", "canary", "shadow"])]
    if names and not s.student_base:
        raise RuntimeError(NO_STUDENT_BASE)
    for name in names:
        load_adapter(s.student_base, s.student_api_key, name, f"{root}/{name}", http)
    return names


def main(
    argv: list[str],
    provider_factory: ProviderFactory = OpenAICompatProvider,
    run: RunFn = subprocess.run,
    http: httpx.Client | None = None,
) -> None:
    s = Settings(mode="live")
    led, gate = build(s)
    cmd = argv[0] if argv else "night"
    if cmd == "tick":
        gate.tick()
    elif cmd == "reload":
        print("reloaded:", reload_adapters(s, led, http))
    elif cmd == "night":
        # The student keeps its own timeouts but is retried like the teacher: in the golden eval no teacher stands
        # behind it, and one failed call would end the night (ADR 0014). The judge is a teacher call.
        student = provider_factory(s.student_base, s.student_api_key, timeout=s.student_timeout(), retry=s.teacher_retry())
        judge = provider_factory(s.teacher_base, s.teacher_api_key, timeout=s.teacher_timeout(), retry=s.teacher_retry())
        evaluator = LiveEvaluator(student, judge, s.judge_model, samples=s.samples)
        signer = bom.Signer(s.bom_signing_key) if s.bom_signing_key else None
        rep = run_night(led, NebiusTrainer(s, run, http), evaluator, gate, s.student_base_model, signer=signer)
        if rep.version is not None:  # keep the adapter's BOM next to its training data in the bucket
            doc = led.version_bom(rep.version)
            out = Path(os.environ.get("RATCHET_DATA_DIR", "data")) / "boms" / f"student-v{rep.version}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
            print(f"bill of materials: {out}")
        print(rep)
    else:
        raise SystemExit(f"unknown command {cmd!r}; use 'night', 'tick' or 'reload'")
    for e in led.events()[-10:]:
        print(f"day {e['day']}: [{e['kind']}] {e['detail']}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
