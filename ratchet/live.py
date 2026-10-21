"""Live-mode Night School on Nebius: train on a Serverless Job, hot-load the adapter into vLLM, evaluate.

    python -m ratchet.live night        # run one night against the live ledger
    python -m ratchet.live tick         # advance the release gate (run every few minutes via cron)
    python -m ratchet.live reload       # re-load live/canary/shadow adapters after an endpoint restart (the gateway
                                        # also does this by itself when vLLM answers 404 for an adapter: AdapterReloader)
    python -m ratchet.live seal         # seal the savings receipts of every day that has ended (run hourly via cron:
                                        # a ledger day starts at the first request's time of day, not 00:00 UTC; the
                                        # gateway also seals when a receipt is asked for)

Environment (in addition to ratchet.config):
    RATCHET_DATA_DIR      local folder synced to the bucket mounted in jobs/endpoints (default ./data)
    RATCHET_SYNC_CMD      command that uploads RATCHET_DATA_DIR to the bucket, e.g.
                          "aws s3 sync ./data s3://my-bucket/ratchet --endpoint-url https://storage.eu-north1.nebius.cloud"
                          (run before training, and again after a night that trained, to upload the adapter's BOM
                          into its folder; never with --delete, which would remove the weights the job wrote there)
    RATCHET_TRAIN_CMD     command that runs the training job and waits for it; {version} and {data}
                          are substituted, e.g. "bash deploy/train_job.sh {version}"
    RATCHET_ADAPTER_ROOT  where the endpoint sees adapters (default /data/adapters)
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from . import bom, receipts, taskspec
from .config import Settings
from .ledger import Ledger, Version
from .nightschool import dataset
from .nightschool.evaluate import LiveEvaluator
from .nightschool.orchestrate import run_night
from .providers import OpenAICompatProvider, Provider
from .release import ReleaseGate, ReleasePolicy
from .resilience import describe_failure

# The test seams below are default arguments: `run` replaces subprocess.run, `http` the client that talks to vLLM, and
# `provider_factory` the model clients (called as `factory(base_url, api_key, timeout=..., retry=...)`). Production code
# never passes them.
RunFn = Callable[..., Any]
ProviderFactory = Callable[..., Provider]
NO_STUDENT_BASE = "RATCHET_STUDENT_BASE is not set: no student endpoint to load adapters into"
RELOAD_INTERVAL_S = 60.0  # the gateway reloads one adapter at most this often, however many requests hit its 404
ADAPTER_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")  # an adapter name that is one folder under adapters/


def adapter_root() -> str:
    """Where the endpoint sees adapters (`lora_path` = `<root>/<name>`)."""
    return os.environ.get("RATCHET_ADAPTER_ROOT", "/data/adapters")


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
        self.adapter_root = adapter_root()

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


def write_boms(led: Ledger, vid: int, trainer: NebiusTrainer) -> bool:
    """Write version `vid`'s BOM to `RATCHET_DATA_DIR`: `boms/student-vN.json`, and inside its adapter's folder
    (`adapters/<name>/ratchet_bom.json`, beside the weights the training job wrote there, so a copy of the adapter
    carries its provenance), then run RATCHET_SYNC_CMD again to upload them. The sync must add files without deleting
    (no `--delete`): the bucket's adapter folders hold weights the local folder does not. False when the sync failed
    (the night itself already succeeded; `main` exits non-zero after reporting it)."""
    doc = led.version_bom(vid)
    v = led.version(vid)
    if doc is None or v is None:
        return True
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    out = trainer.data / "boms" / f"student-v{vid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"bill of materials: {out}")
    name = v.adapter_uri
    if ADAPTER_NAME.fullmatch(name) and name not in (".", ".."):  # never a path outside the data folder
        inside = trainer.data / "adapters" / name / "ratchet_bom.json"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text(text, encoding="utf-8")
        print(f"bill of materials in the adapter folder: {inside}")
    else:
        print(f"adapter name {name!r} is not a folder name: no BOM copy beside its weights", file=sys.stderr)
    if not trainer.sync_cmd:
        return True
    try:
        _run(trainer.sync_cmd, trainer.run)
    except Exception as e:  # noqa: BLE001 - the night is done; say what is missing and fail the command at the end
        print(f"RATCHET_SYNC_CMD failed ({describe_failure(e)}); upload {trainer.data} again by hand", file=sys.stderr)
        return False
    return True


def build(s: Settings) -> tuple[Ledger, ReleaseGate]:
    led = Ledger(s.db_path, s.golden_fraction, auto_day=True)
    return led, ReleaseGate(led, ReleasePolicy.from_env())


def reload_adapters(s: Settings, led: Ledger, http: httpx.Client | None = None) -> list[str]:
    """vLLM forgets runtime-loaded adapters when the endpoint restarts: load them again (the manual fallback for
    `AdapterReloader`, which the gateway runs by itself)."""
    root = adapter_root()
    names = [v.adapter_uri for v in led.versions(["live", "canary", "shadow"])]
    if names and not s.student_base:
        raise RuntimeError(NO_STUDENT_BASE)
    for name in names:
        load_adapter(s.student_base, s.student_api_key, name, f"{root}/{name}", http)
    return names


class AdapterReloader:
    """Loads a version's LoRA adapter again when vLLM answers 404 for it: an endpoint restart forgets every adapter
    loaded at runtime, and before this only `python -m ratchet.live reload` by hand brought them back.

    `request(version)` never waits: the request that hit the 404 has already escalated to the teacher. One daemon
    thread, started on the first request, calls `load(adapter_name)` for each queued version in turn, and each adapter
    is tried at most once per `interval_s` (on `clock`), however many requests hit its 404 meanwhile. The outcome is an
    `adapter_reloaded` or `adapter_reload_failed` event; a failure names the error's class and status, never its
    message, which carries the endpoint URL."""

    def __init__(
        self,
        load: Callable[[str], object],
        ledger: Ledger,
        interval_s: float = RELOAD_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.load = load
        self.ledger = ledger
        self.interval_s = interval_s
        self.clock = clock
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._tried: dict[str, float] = {}  # adapter name -> when its last reload was queued
        self._pending = 0
        self._queue: queue.Queue[Version | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False

    def request(self, version: Version) -> bool:
        """Queue a reload of `version`'s adapter, unless one was queued less than `interval_s` ago or the reloader is
        closed. True when queued."""
        name = version.adapter_uri
        with self._lock:
            if self._closed:
                return False
            now = self.clock()
            last = self._tried.get(name)
            if last is not None and now - last < self.interval_s:
                return False
            self._tried[name] = now
            self._pending += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="ratchet-adapter-reloader", daemon=True)
                self._thread.start()
            self._queue.put(version)
        return True

    def join(self, timeout: float | None = None) -> bool:
        """Wait until every queued reload has finished; False if `timeout` passed first. The gateway never waits; tests
        and scripts do."""
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)

    def close(self) -> None:
        """Stop the thread once the reloads already queued are done; later requests are refused."""
        with self._lock:
            if not self._closed and self._thread is not None:
                self._queue.put(None)
            self._closed = True

    def _run(self) -> None:
        while (version := self._queue.get()) is not None:
            try:
                self._reload(version)
            finally:
                with self._idle:
                    self._pending -= 1
                    self._idle.notify_all()

    def _reload(self, version: Version) -> None:
        name = version.adapter_uri
        try:
            self.load(name)
        except Exception as e:  # noqa: BLE001 - report it; the next 404 after interval_s tries again
            kind, detail = "adapter_reload_failed", f"v{version.id} {name}: {describe_failure(e)}"
        else:
            kind, detail = "adapter_reloaded", f"v{version.id} {name}: loaded again after the student endpoint answered 404"
        try:
            self.ledger.event(kind, detail)
        except Exception:  # noqa: BLE001 - the thread must outlive a failing ledger write
            pass


def adapter_reloader(
    s: Settings, led: Ledger, http: httpx.Client | None = None, clock: Callable[[], float] = time.monotonic
) -> AdapterReloader:
    """The gateway's reloader for the student endpoint at `s.student_base` (live mode with an endpoint only)."""
    if not s.student_base:
        raise RuntimeError(NO_STUDENT_BASE)
    root = adapter_root()

    def load(name: str) -> None:
        load_adapter(s.student_base, s.student_api_key, name, f"{root}/{name}", http)

    return AdapterReloader(load, led, clock=clock)


def main(
    argv: list[str],
    provider_factory: ProviderFactory = OpenAICompatProvider,
    run: RunFn = subprocess.run,
    http: httpx.Client | None = None,
) -> None:
    s = Settings(mode="live")
    s.check_reserved()  # a setting reserved for a feature not built yet stops here (ADR 0024)
    policy = s.cost_policy()  # a bad GPU rate stops here, before any paid work
    cert = s.cert_policy()  # so does a bad RATCHET_CALIBRATION, _CERT_TARGET or _CERT_DELTA (ADR 0026)
    s.receipt_policy()  # and a bad RATCHET_TAKE_RATE or RATCHET_RECEIPT_MIN_AUDITS (ADR 0029)
    s.checked_mock_reasoning_mean()  # and RATCHET_MOCK_TEACHER_REASONING_MEAN out of range, as in the gateway
    led, gate = build(s)
    # The gateway's tasks (RATCHET_TASKS_FILE and the API's), so the night trains and grades with the same specs; a bad
    # file stops here with every error listed.
    for line in taskspec.install(led, s.tasks_file):
        print(f"task skipped: {line}", file=sys.stderr)
    cmd = argv[0] if argv else "night"
    synced = True
    if cmd == "tick":
        gate.tick()
    elif cmd == "reload":
        print("reloaded:", reload_adapters(s, led, http))
    elif cmd == "seal":
        # Every day that ended (SEAL_GRACE_S ago) gets its receipts, each client's and the gateway's, signed with the BOM
        # key; a day already sealed is left as it is (INV-17).
        signer = bom.Signer(s.bom_signing_key) if s.bom_signing_key else None
        sealed = receipts.seal_due(led, receipts.SealConfig.from_settings(s, signer, "live traffic"))
        for period, client in sealed:
            print(f"sealed the receipt of day {period} ({client or 'gateway'})")
        if not sealed:
            print("nothing to seal: every day that has ended is sealed")
    elif cmd == "night":
        # The student keeps its own timeouts but is retried like the teacher: in the golden eval no teacher stands
        # behind it, and one failed call would end the night (ADR 0014). The judge is a teacher call.
        student = provider_factory(s.student_base, s.student_api_key, timeout=s.student_timeout(), retry=s.teacher_retry())
        judge = provider_factory(s.teacher_base, s.teacher_api_key, timeout=s.teacher_timeout(), retry=s.teacher_retry())
        evaluator = LiveEvaluator(
            student,
            judge,
            s.judge_model,
            samples=s.samples,
            policy=s.gen_policy(),
            calibration=cert.calibration,
            cert_target=cert.target,
            cert_delta=cert.delta,
        )
        signer = bom.Signer(s.bom_signing_key) if s.bom_signing_key else None
        trainer = NebiusTrainer(s, run, http)
        # The training job's wall-clock time is recorded as a compute interval for the cost report (FR-15.2).
        rep = run_night(
            led,
            trainer,
            evaluator,
            gate,
            s.student_base_model,
            signer=signer,
            train_gpu=policy.train,
            redact_pii=s.redact_pii,
        )
        # Keep the adapter's BOM next to its training data, and in its folder, in the bucket (FR-16.4).
        synced = write_boms(led, rep.version, trainer) if rep.version is not None else True
        print(rep)
    else:
        raise SystemExit(f"unknown command {cmd!r}; use 'night', 'tick', 'reload' or 'seal'")
    for e in led.events()[-10:]:
        print(f"day {e['day']}: [{e['kind']}] {e['detail']}")
    if not synced:
        raise SystemExit("RATCHET_SYNC_CMD failed after the night: the BOM copies are local only (see above)")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
