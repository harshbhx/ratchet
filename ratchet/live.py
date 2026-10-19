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

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .ledger import Ledger
from .nightschool import dataset
from .nightschool.evaluate import LiveEvaluator
from .nightschool.orchestrate import run_night
from .providers import OpenAICompatProvider
from .release import ReleaseGate, ReleasePolicy


def _run(cmd: str) -> None:
    print(f"$ {cmd}", flush=True)
    subprocess.run(shlex.split(cmd), check=True)


def load_adapter(student_base: str, api_key: str, name: str, path: str) -> None:
    """Hot-load a LoRA adapter into a running vLLM server.

    Needs the server started with VLLM_ALLOW_RUNTIME_LORA_UPDATING=True and --enable-lora.
    """
    root = student_base.rstrip("/").removesuffix("/v1")
    r = httpx.post(
        f"{root}/v1/load_lora_adapter",
        json={"lora_name": name, "lora_path": path},
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=300,
    )
    if r.status_code >= 400 and "already" not in r.text.lower():
        r.raise_for_status()


class NebiusTrainer:
    """Writes the dataset, syncs it to the bucket, runs the Serverless Job, then hot-loads the adapter."""

    def __init__(self, s: Settings):
        self.s = s
        self.data = Path(os.environ.get("RATCHET_DATA_DIR", "data"))
        self.sync_cmd = os.environ.get("RATCHET_SYNC_CMD", "")
        self.train_cmd = os.environ.get("RATCHET_TRAIN_CMD", "bash deploy/train_job.sh {version}")
        self.adapter_root = os.environ.get("RATCHET_ADAPTER_ROOT", "/data/adapters")

    def train(self, train_rows: list[dict[str, Any]], base_model: str, name: str) -> str:
        dataset.write_jsonl(train_rows, self.data / "night" / "train.jsonl")
        if self.sync_cmd:
            _run(self.sync_cmd)
        _run(self.train_cmd.format(version=name, data=str(self.data)))
        if not self.s.student_base:
            raise RuntimeError("RATCHET_STUDENT_BASE is not set: no endpoint to load the adapter into")
        load_adapter(self.s.student_base, self.s.student_api_key, name, f"{self.adapter_root}/{name}")
        return name


def build(s: Settings) -> tuple[Ledger, ReleaseGate]:
    led = Ledger(s.db_path, s.golden_fraction, auto_day=True)
    return led, ReleaseGate(led, ReleasePolicy.from_env())


def reload_adapters(s: Settings, led: Ledger) -> list[str]:
    """vLLM forgets runtime-loaded adapters when the endpoint restarts: load them again."""
    root = os.environ.get("RATCHET_ADAPTER_ROOT", "/data/adapters")
    names = [v.adapter_uri for v in led.versions(["live", "canary", "shadow"])]
    for name in names:
        load_adapter(s.student_base, s.student_api_key, name, f"{root}/{name}")
    return names


def main(argv: list[str]) -> None:
    s = Settings(mode="live")
    led, gate = build(s)
    cmd = argv[0] if argv else "night"
    if cmd == "tick":
        gate.tick()
    elif cmd == "reload":
        print("reloaded:", reload_adapters(s, led))
    elif cmd == "night":
        student = OpenAICompatProvider(s.student_base, s.student_api_key)
        judge = OpenAICompatProvider(s.teacher_base, s.teacher_api_key)
        evaluator = LiveEvaluator(student, judge, s.judge_model, samples=s.samples)
        rep = run_night(led, NebiusTrainer(s), evaluator, gate, s.student_base_model)
        print(rep)
    else:
        raise SystemExit(f"unknown command {cmd!r}; use 'night', 'tick' or 'reload'")
    for e in led.events()[-10:]:
        print(f"day {e['day']}: [{e['kind']}] {e['detail']}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
