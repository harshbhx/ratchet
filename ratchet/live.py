"""Live-mode Night School on Nebius: train on a Serverless Job, hot-load the adapter into vLLM, evaluate.

    python -m ratchet.live night        # run one night against the live ledger
    python -m ratchet.live tick         # advance the release gate (run every few minutes via cron)
    python -m ratchet.live reload       # re-load live/canary/shadow adapters after an endpoint restart (the gateway
                                        # also does this by itself when vLLM answers 404 for an adapter: AdapterReloader)
    python -m ratchet.live purge [--client NAME] [--older-than-days N] [--dry-run]
                                        # apply the retention windows now (FR-13.4); the gateway does it hourly
    python -m ratchet.live export --version N --out DIR
                                        # an adapter with its licenses: the adapter folder (when synced locally), its
                                        # BOM and NOTICE.md in DIR; prints the BOM's sha256 (NFR-9)
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

After a night that trained, the adapter's BOM and NOTICE.md go to RATCHET_DATA_DIR/adapters/<name>/ (ratchet_bom.json,
NOTICE.md) and RATCHET_SYNC_CMD runs again, so the bucket's adapter folder holds the weights and their licenses together.
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from . import bom, licenses, privacy, receipts, taskspec
from .config import Settings
from .ledger import Ledger, Version
from .nightschool import dataset, notices
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
# An adapter name that is one folder under adapters/ (and never `.` or `..`: `adapter_dir` also refuses any `..`).
ADAPTER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def adapter_root() -> str:
    """Where the endpoint sees adapters (`lora_path` = `<root>/<name>`)."""
    return os.environ.get("RATCHET_ADAPTER_ROOT", "/data/adapters")


def data_dir() -> Path:
    """The local folder synced to the bucket (RATCHET_DATA_DIR): `night/`, `boms/` and `adapters/<name>/`."""
    return Path(os.environ.get("RATCHET_DATA_DIR", "data"))


def adapter_dir(data: Path, name: str) -> Path:
    """`<data>/adapters/<name>`, the local copy of the bucket folder the training job writes the adapter to. The name
    must be one path segment (`student-v3`), so a version can never point outside the adapters folder."""
    if not ADAPTER_NAME.fullmatch(name) or ".." in name:
        raise ValueError(f"adapter name {name!r} is not a single folder name")
    return data / "adapters" / name


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
        dataset.write_training(train_rows, self.data / "night" / "train.jsonl")  # prompt-completion rows (FR-8.42)
        if self.sync_cmd:
            _run(self.sync_cmd, self.run)
        _run(self.train_cmd.format(version=name, data=str(self.data)), self.run)
        load_adapter(self.s.student_base, self.s.student_api_key, name, f"{self.adapter_root}/{name}", self.http)
        return name


def write_boms(led: Ledger, vid: int, trainer: NebiusTrainer) -> bool:
    """After a night that trained version `vid`: write its BOM and NOTICE.md to `RATCHET_DATA_DIR` (`publish_bom`:
    `boms/student-vN.json`, and `adapters/<name>/ratchet_bom.json` with `NOTICE.md` beside the weights the training job
    wrote there, so a copy of the adapter carries its provenance and its licenses, FR-16.4, NFR-9), then run
    RATCHET_SYNC_CMD again to upload them. The sync must add files without deleting (no `--delete`): the bucket's
    adapter folders hold weights the local folder does not. False when the sync failed (the night itself already
    succeeded; a `sync_failed` event names the exception class, and `main` exits non-zero after reporting it)."""
    written = publish_bom(led, vid, trainer.data)
    folder = {notices.BOM_FILE, notices.NOTICE_FILE}
    for path in written:
        print(f"{'notice' if path.name == notices.NOTICE_FILE else 'bill of materials'}: {path}")
    inside = [p for p in written if p.name in folder]
    if inside:
        print(f"licenses in the adapter folder: {', '.join(str(p) for p in inside)}")
    if not written or not trainer.sync_cmd:
        return True
    try:
        _run(trainer.sync_cmd, trainer.run)
    except Exception as e:  # noqa: BLE001 - the night is done; say what is missing and fail the command at the end
        cause = describe_failure(e)
        print(f"RATCHET_SYNC_CMD failed ({cause}); upload {trainer.data} again by hand", file=sys.stderr)
        what = "BOM and NOTICE.md" if inside else "BOM"
        led.event("sync_failed", f"v{vid}: {what} written locally, not uploaded: {cause}")
        return False
    return True


def build(s: Settings) -> tuple[Ledger, ReleaseGate]:
    led = Ledger(s.db_path, s.golden_fraction, auto_day=True, sync=s.ledger_sync())
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


def publish_bom(led: Ledger, vid: int, data: Path) -> list[Path]:
    """After a night that trained version `vid`: write its BOM to `<data>/boms/student-v<vid>.json` (beside the training
    data, as since v0.4) and, with its NOTICE.md, into the adapter's folder `<data>/adapters/<name>/` (`ratchet_bom.json`,
    `NOTICE.md`), so the next RATCHET_SYNC_CMD puts the licenses into the bucket folder that holds the weights (NFR-9).
    An adapter name that is not one folder name (`adapter_dir`) gets the `boms/` copy only, never a path outside the data
    folder (FR-16.4). Returns the files written; none for a version without a BOM."""
    doc = led.version_bom(vid)
    version = led.version(vid)
    if doc is None or version is None:
        return []
    out = data / "boms" / f"student-v{vid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(notices.bom_bytes(doc))
    try:
        folder = adapter_dir(data, version.adapter_uri)
    except ValueError:
        print(f"adapter name {version.adapter_uri!r} is not a folder name: no BOM copy beside its weights", file=sys.stderr)
        return [out]
    return [out, *notices.write_adapter_files(folder, doc)]


# What makes an adapter folder usable on its own: PEFT's config and a weights file (`save_pretrained` writes both).
ADAPTER_CONFIG = "adapter_config.json"
WEIGHT_SUFFIXES = (".safetensors", ".bin")


def adapter_files(folder: Path) -> list[Path]:
    """The adapter's own files under `folder`, sorted: everything but the BOM and NOTICE.md at its top level, which
    `publish_bom` (and the training job, for NOTICE.md) write there and an export writes again from the stored BOM. After
    a live night with an upload-only RATCHET_SYNC_CMD the local folder holds only those two, and no adapter."""
    if not folder.is_dir():
        return []
    ours = {notices.BOM_FILE, notices.NOTICE_FILE}
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.relative_to(folder).as_posix() not in ours)


def has_weights(files: list[Path]) -> bool:
    """Whether `files` (`adapter_files`) make a loadable adapter: an `adapter_config.json` and a weights file."""
    return any(p.name == ADAPTER_CONFIG for p in files) and any(p.suffix in WEIGHT_SUFFIXES for p in files)


def _inside(path: Path, folder: Path) -> bool:
    """Whether `path` is `folder` or lies under it, once both are resolved (symlinks and `..` followed)."""
    target, root = path.resolve(), folder.resolve()
    return target == root or root in target.parents


def export_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m ratchet.live export",
        description="Copy an adapter with its licenses into a folder: the adapter files (when RATCHET_DATA_DIR/adapters/"
        "<name> holds them), its bill of materials (ratchet_bom.json) and NOTICE.md; prints the BOM's sha256.",
    )
    ap.add_argument("--version", type=int, required=True, help="the version id (as in /ratchet/versions/{id}/bom)")
    ap.add_argument("--out", required=True, help="the folder to write; it must not exist yet or be empty")
    return ap


def export(led: Ledger, argv: list[str], data: Path | None = None) -> dict[str, Any]:
    """`python -m ratchet.live export`: an adapter ready to hand over (US-10, NFR-7, NFR-9). The adapter folder is copied
    when it is on this machine (a bucket mount, or synced down); the BOM is the one stored at training time (INV-14),
    checked against its own sha256 and signature first; NOTICE.md is the text the BOM signed (`notices.bom_notice`), so it
    replaces any copy in the folder. `adapter_files` counts the adapter's own files, never the BOM and NOTICE.md that a
    live night leaves in the local folder, and without the weights (`has_weights`) the command says where to copy them
    from. The output folder must be new or empty, so an export never mixes two adapters, and outside RATCHET_DATA_DIR,
    which the next sync uploads to the bucket."""
    args = export_parser().parse_args(argv)
    data = data if data is not None else data_dir()
    version = led.version(args.version)
    if version is None:
        raise SystemExit(f"export: no version {args.version}")
    doc = led.version_bom(args.version)
    if doc is None:
        raise SystemExit(f"export: version {args.version} has no bill of materials (trained before v0.4), so no licenses to ship")
    ok, reason = bom.verify(doc)
    if not ok:
        raise SystemExit(f"export: the stored bill of materials of version {args.version} does not verify: {reason}")
    out = Path(args.out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SystemExit(f"export: {out} exists and is not an empty folder; choose a new one")
    try:
        src = adapter_dir(data, version.adapter_uri)
    except ValueError as e:
        raise SystemExit(f"export: version {args.version}: {e}") from None
    if _inside(out, src):
        # Inside the adapter folder the bundle would be copied into itself, and the next sync would upload it there.
        raise SystemExit(f"export: {out} is inside the adapter folder {src}; choose a folder outside it")
    if _inside(out, data):
        # RATCHET_SYNC_CMD uploads the whole data folder: a second copy of the weights would land in the bucket.
        raise SystemExit(
            f"export: {out} is inside RATCHET_DATA_DIR ({data}), which RATCHET_SYNC_CMD uploads; choose a folder outside it"
        )
    files = adapter_files(src)
    weights = has_weights(files)
    if files:
        shutil.copytree(src, out, dirs_exist_ok=True)
    bom_path, notice_path = notices.write_adapter_files(out, doc)  # replaces any BOM or NOTICE.md the copy brought
    result = {
        "version": version.id,
        "adapter": version.adapter_uri,
        "out": str(out),
        "adapter_files": len(files),
        "weights": weights,
        "bom": str(bom_path),
        "notice": str(notice_path),
        "bom_sha256": doc["sha256"],
        "signed": bool(doc.get("signature")),
    }
    if weights:
        print(f"exported {version.adapter_uri} (v{version.id}) from {src} to {out}")
    else:
        what = f"the licenses and {len(files)} other file{'s' if len(files) != 1 else ''}" if files else "the licenses"
        print(
            f"exported {what} of {version.adapter_uri} (v{version.id}) to {out}; the adapter weights ({ADAPTER_CONFIG} and "
            f"adapter_model.safetensors) are not at {src}: copy them from the bucket's adapters/{version.adapter_uri}/ into {out}"
        )
    print(f"bill of materials: {bom_path} ({reason})")
    print(f"notice: {notice_path}")
    print(f"bom sha256: {doc['sha256']}")
    return result


def purge_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m ratchet.live purge",
        description="Apply the retention windows now (FR-13.4): purge the text of examples not seen for "
        "RATCHET_EXAMPLE_RETENTION_DAYS and fold request rows older than RATCHET_REQUEST_RETENTION_DAYS into daily sums.",
    )
    ap.add_argument("--client", help="only this API client's examples and request rows")
    ap.add_argument(
        "--older-than-days", type=int, help="this window, in whole days, for both examples and requests (instead of the settings)"
    )
    ap.add_argument("--dry-run", action="store_true", help="count what would go and change nothing")
    return ap


def seal_receipts(led: Ledger, s: Settings) -> list[tuple[int, str | None]]:
    """Seal the savings receipts of every day that has ended (FR-15.5, ADR 0029), each client's and the gateway's,
    signed with the BOM key; a day already sealed is left as it is (INV-17). Also run before retention folds request
    rows, so every receipt is sealed from all of its day's rows (`receipts._seal_period` never seals a folded day)."""
    signer = bom.Signer(s.bom_signing_key) if s.bom_signing_key else None
    return receipts.seal_due(led, receipts.SealConfig.from_settings(s, signer, "live traffic"))


def purge(led: Ledger, s: Settings, argv: list[str]) -> dict[str, Any]:
    """`python -m ratchet.live purge`: the retention windows applied once, as the gateway does hourly (and, like it,
    after sealing the receipts of every day that has ended). With `--older-than-days N`, N days for both windows; with
    neither that nor a setting, nothing to do (the summary says so)."""
    args = purge_parser().parse_args(argv)
    days = args.older_than_days
    if days is not None and days < 1:
        raise SystemExit("--older-than-days must be 1 or more")
    if not args.dry_run:
        seal_receipts(led, s)
    try:
        out = privacy.apply_retention(
            led, s.retention(), client=args.client, example_days=days, request_days=days, dry_run=args.dry_run
        )
    except ValueError as e:  # a bad --client or window: a message, not a traceback
        raise SystemExit(f"purge: {e}") from None
    verb = "would purge" if args.dry_run else "purged"
    if not out["example_days"] and not out["request_days"]:
        print("nothing to purge: RATCHET_EXAMPLE_RETENTION_DAYS and RATCHET_REQUEST_RETENTION_DAYS are 0 (keep forever)")
    else:
        print(
            f"{verb}: {out['examples_purged']} examples (window {out['example_days'] or 'off'} days), "
            f"{out['requests_folded']} request rows folded (window {out['request_days'] or 'off'} days)"
        )
    return out


def main(
    argv: list[str],
    provider_factory: ProviderFactory = OpenAICompatProvider,
    run: RunFn = subprocess.run,
    http: httpx.Client | None = None,
) -> None:
    s = Settings(mode="live")
    s.check_reserved()  # a setting reserved for a feature not built yet stops here (ADR 0024)
    # The gateway's license registry (RATCHET_TEACHER_LICENSES, ADR 0090), so the night trains on the same teachers'
    # rows and the BOM names their licenses; a bad file stops here, every error listed.
    licenses.install(s.teacher_licenses_file)
    policy = s.cost_policy()  # a bad GPU rate stops here, before any paid work
    cert = s.cert_policy()  # so does a bad RATCHET_CALIBRATION, _CERT_TARGET or _CERT_DELTA (ADR 0026)
    retention = s.retention()  # a bad retention window stops here too, before anything is deleted or trained
    s.receipt_policy()  # and a bad RATCHET_TAKE_RATE or RATCHET_RECEIPT_MIN_AUDITS (ADR 0029)
    s.checked_mock_reasoning_mean()  # and RATCHET_MOCK_TEACHER_REASONING_MEAN out of range, as in the gateway
    led, gate = build(s)
    # The gateway's tasks (RATCHET_TASKS_FILE and the API's), so the night trains and grades with the same specs; a bad
    # file stops here with every error listed.
    for line in taskspec.install(led, s.tasks_file):
        print(f"task skipped: {line}", file=sys.stderr)
    cmd = argv[0] if argv else "night"
    if cmd == "export":  # reads the ledger and writes files only: nothing else to report
        export(led, argv[1:])
        return
    synced = True
    if cmd == "tick":
        gate.tick()
    elif cmd == "purge":
        purge(led, s, argv[1:])
    elif cmd == "reload":
        print("reloaded:", reload_adapters(s, led, http))
    elif cmd == "seal":
        # Every day that ended (SEAL_GRACE_S ago) gets its receipts, each client's and the gateway's, signed with the BOM
        # key; a day already sealed is left as it is (INV-17).
        sealed = seal_receipts(led, s)
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
            min_task_n=s.min_task_golden_rows(),
            per_task=s.per_task_calibration,
        )
        signer = bom.Signer(s.bom_signing_key) if s.bom_signing_key else None
        trainer = NebiusTrainer(s, run, http)
        if retention.request_days:  # the night folds request rows first: every day that ended is sealed before that
            seal_receipts(led, s)
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
            store_examples=s.store_examples,
            retention=retention,  # expired text is purged before the night reads the ledger (FR-13.4)
        )
        # Keep the adapter's BOM next to its training data, and in its folder, in the bucket (FR-16.4).
        synced = write_boms(led, rep.version, trainer) if rep.version is not None else True
        print(rep)
    else:
        raise SystemExit(f"unknown command {cmd!r}; use 'night', 'tick', 'reload', 'purge', 'export' or 'seal'")
    for e in led.events(last=10):
        print(f"day {e['day']}: [{e['kind']}] {e['detail']}")
    if not synced:  # after the report, so the night's outcome is on screen; a non-zero exit for cron
        raise SystemExit(
            "RATCHET_SYNC_CMD failed after the night: the BOM and NOTICE.md copies are local only (RATCHET_SYNC_CMD failed "
            "after training, see above); run it again to upload them"
        )


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
