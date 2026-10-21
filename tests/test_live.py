"""Live-mode and operational entry points, run offline: everything that would otherwise run for the first time on paid
Nebius resources.

- Live mode: `NebiusTrainer` (write, sync, train, hot-load), `reload_adapters`, `python -m ratchet.live night|tick|reload`,
  the gateway's live `Runtime` (real `OpenAICompatProvider`s on `httpx.MockTransport`), `LiveEvaluator` and the
  training script's arguments and row contract.
- Operations: the Scout CLI over HTTP, the simulator CLI, the mock warm-up, `RATCHET_TRUST_PROXY`, the day-1 check and
  the deploy scripts (`bash -n`, `create_vm.sh` dry run).

The seams are default arguments (`transport=`, `transports=`, `client=`, `run=`, `http=`, `provider_factory=`); with
none passed, the code talks to the real services. No test here opens a socket or starts a GPU job.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import (
    OPEN_TEACHER,
    AppTransport,
    FakeClock,
    FakeNebius,
    FakeOpenAIServer,
    RecordingProvider,
    RecordingRun,
    _examples,
    _version,
)
from fastapi.testclient import TestClient

from ratchet import bom, licenses, live
from ratchet.confidence import STUDENT_SAMPLING
from ratchet.config import Settings
from ratchet.ledger import Ledger
from ratchet.nightschool import dataset, train
from ratchet.nightschool.evaluate import JUDGE_PROMPT, LiveEvaluator
from ratchet.providers import DEFAULT_STUDENT_MAX_TOKENS, GenParams, OpenAICompatProvider
from ratchet.resilience import RetryPolicy, timeouts
from ratchet.router import Ratchet
from ratchet.scout.__main__ import main as scout_main
from ratchet.scout.agents import Call
from ratchet.scout.guard import Guard
from ratchet.scout.prompts import EXAMPLE_QUESTIONS
from ratchet.scout.search import MockSearch
from sim import mock_models, simulate
from sim.world import make_stream

ROOT = Path(__file__).resolve().parents[1]
CHAT = "/v1/chat/completions"
STUDENT_BASE = "https://student.test/v1"  # a vLLM Serverless Endpoint
TEACHER_BASE = "https://teacher.test/v1"  # Token Factory
STUDENT_KEY = "sk-student-endpoint-token"
TEACHER_KEY = "tf-teacher-api-key"
FALLBACK = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
JUDGE = "nvidia/nemotron-3-super-120b-a12b"
TRIAGE = "ticket-triage"
LABELS = json.dumps({"category": "billing", "priority": "high"})
SECRET = "ghp_SECRETtoken0123456789"  # must never appear in deploy script output
BASH = shutil.which("bash")


# ---------- helpers ----------
def _live_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Only `env` set: every RATCHET_* variable and key from the shell is removed, so it cannot move a result."""
    for name in list(os.environ):
        if name.startswith("RATCHET_") or name in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _live_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """A live-mode Settings with every value these tests depend on set explicitly."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": TEACHER_BASE,
        "teacher_api_key": TEACHER_KEY,
        "teacher_model": OPEN_TEACHER,
        "judge_model": JUDGE,
        "fallback_student_model": FALLBACK,
        "student_base": STUDENT_BASE,
        "student_api_key": STUDENT_KEY,
        "samples": 3,
        "audit_rate": 0.0,
        "confidence_threshold": 0.8,
        "golden_fraction": 0.10,
        "redact_pii": True,
        "tavily_api_key": "",
        "api_keys": "",
        "api_key_daily_requests": 0,
        "bom_signing_key": "",
        "mock_warmup_days": 0,
    }
    return Settings(**{**base, **overrides})


def _ask(tag: object = 0) -> list[dict[str, Any]]:
    return [{"role": "user", "content": f"Where is my refund for order {tag}?"}]


def _failing(status: int = 500) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, text="upstream error"))


def _http(server: FakeOpenAIServer) -> httpx.Client:
    return httpx.Client(transport=server.transport)


def _answer_like_the_teacher(body: dict[str, Any]) -> str:
    """The student answers `_examples` requests exactly as the teacher did ('teacher answer to <request>')."""
    return f"teacher answer to {body['messages'][-1]['content']}"


# ---------- live mode: training and adapters ----------
@pytest.mark.req("FR-4.6", "FR-8.2", "US-10")
def test_nebius_trainer_writes_syncs_trains_and_hot_loads(tmp_path, monkeypatch):
    data = tmp_path / "data"
    sync = "aws s3 sync ./data s3://customer-bucket/ratchet --endpoint-url https://storage.example"
    _live_env(monkeypatch, RATCHET_DATA_DIR=str(data), RATCHET_SYNC_CMD=sync)  # default train command and adapter root
    rows = [
        {"task": TRIAGE, "messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": LABELS}]}
        for i in range(3)
    ]
    order: list[str] = []
    seen_at_sync: list[list[dict[str, Any]]] = []

    def on_call(argv: list[str]) -> None:
        order.append(f"run {argv[0]}")
        if argv[0] == "aws":  # the dataset is on disk before it is synced to the bucket
            seen_at_sync.append(train.load_rows(str(data / "night" / "train.jsonl")))

    run = RecordingRun(on_call)
    vllm = FakeOpenAIServer(load=lambda body: order.append("load") or (200, "Success"))
    trainer = live.NebiusTrainer(_live_settings(tmp_path), run=run, http=_http(vllm))
    assert trainer.train(rows, "base-model", "student-v1") == "student-v1"

    assert run.commands == [sync, "bash deploy/train_job.sh student-v1"]
    assert run.calls[0][0] == shlex.split(sync) and all(kw == {"check": True} for _, kw in run.calls)
    assert order == ["run aws", "run bash", "load"]  # write, sync, train, then hot-load
    # The job reads prompt-completion rows with the template arguments the student is served with (FR-8.42).
    written = [dataset.training_record(r) for r in rows]
    assert seen_at_sync == [written] and train.load_rows(str(data / "night" / "train.jsonl")) == written
    assert all(r["chat_template_kwargs"] == {"enable_thinking": False} for r in written)
    (load,) = vllm.calls_to("/load_lora_adapter")
    assert (load.method, load.url) == ("POST", "https://student.test/v1/load_lora_adapter")
    assert load.json == {"lora_name": "student-v1", "lora_path": "/data/adapters/student-v1"}
    assert load.headers["authorization"] == f"Bearer {STUDENT_KEY}"

    # US-10: the job writes the adapter into the customer's bucket (mounted at /data) under adapters/<version>, the
    # path the trainer hot-loads from, and reads the dataset where the synced data folder puts it.
    job = (ROOT / "deploy" / "train_job.sh").read_text(encoding="utf-8")
    assert '--volume "${RATCHET_BUCKET_ID}:/data"' in job
    assert "--data /data/night/train.jsonl" in job and "--out /data/adapters/${VERSION}" in job

    # {version} and {data} are substituted into RATCHET_TRAIN_CMD; RATCHET_ADAPTER_ROOT moves the load path.
    monkeypatch.setenv("RATCHET_TRAIN_CMD", "bash deploy/train_job.sh {version} --data {data}")
    monkeypatch.setenv("RATCHET_ADAPTER_ROOT", "/mnt/bucket/adapters")
    monkeypatch.delenv("RATCHET_SYNC_CMD")
    run, vllm = RecordingRun(), FakeOpenAIServer()
    live.NebiusTrainer(_live_settings(tmp_path), run=run, http=_http(vllm)).train(rows, "base-model", "student-v2")
    assert run.commands == [f"bash deploy/train_job.sh student-v2 --data {data}"]  # no sync command, no sync
    assert vllm.calls_to("/load_lora_adapter")[0].json["lora_path"] == "/mnt/bucket/adapters/student-v2"

    # A failed training job stops the night before anything is loaded.
    def fail(argv: list[str]) -> None:
        raise subprocess.CalledProcessError(1, argv)

    vllm = FakeOpenAIServer()
    with pytest.raises(subprocess.CalledProcessError):
        live.NebiusTrainer(_live_settings(tmp_path), run=RecordingRun(fail), http=_http(vllm)).train(rows, "b", "student-v3")
    assert vllm.calls == []

    # No student endpoint: refused (test_hardening checks that it happens before any paid work).
    with pytest.raises(RuntimeError, match="RATCHET_STUDENT_BASE"):
        live.NebiusTrainer(_live_settings(tmp_path, student_base=""), run=RecordingRun()).train(rows, "b", "student-v4")


@pytest.mark.req("FR-8.2")
def test_load_adapter_tolerates_already_loaded_and_raises_otherwise():
    def load(status: int, text: str, base: str = STUDENT_BASE, key: str = STUDENT_KEY) -> FakeOpenAIServer:
        vllm = FakeOpenAIServer(load=lambda body: (status, text))
        live.load_adapter(base, key, "student-v1", "/data/adapters/student-v1", client=_http(vllm))
        return vllm

    # The vLLM root is the base URL without /v1, with or without a trailing slash.
    for base in ("https://student.test/v1", "https://student.test/v1/", "https://student.test"):
        assert load(200, "Success", base).calls[0].url == "https://student.test/v1/load_lora_adapter"
    assert "authorization" not in load(200, "", key="").calls[0].headers  # an endpoint without token auth

    # Loading an adapter twice (a reload after a partial restart) is not an error; any other failure is.
    load(400, "The lora adapter 'student-v1' has already been loaded.")
    load(400, "Adapter ALREADY loaded")
    for status, text in ((400, "invalid lora_path"), (404, "Not Found"), (500, "CUDA out of memory"), (401, "bad token")):
        with pytest.raises(httpx.HTTPStatusError):  # 404: the server runs without VLLM_ALLOW_RUNTIME_LORA_UPDATING
            load(status, text)


@pytest.mark.req("FR-16.4")
def test_bom_copies_never_leave_the_data_folder(tmp_path, monkeypatch, capsys):
    data = tmp_path / "work" / "data"
    _live_env(monkeypatch, RATCHET_DATA_DIR=str(data), RATCHET_SYNC_CMD="rclone copy data bucket:ratchet")
    led = Ledger(":memory:")
    run = RecordingRun()
    trainer = live.NebiusTrainer(_live_settings(tmp_path), run=run)
    for uri in ("../../escaped", "s3://bucket/adapters/x", ".."):
        vid = _version(led, "candidate", adapter_uri=uri)
        led.set_bom(vid, bom.document({"schema": bom.SCHEMA, "adapter": {"version": vid, "name": uri}}))
        assert live.write_boms(led, vid, trainer)
        assert json.loads((data / "boms" / f"student-v{vid}.json").read_text(encoding="utf-8")) == led.version_bom(vid)
        assert f"adapter name {uri!r} is not a folder name" in capsys.readouterr().err
    assert not (data / "adapters").exists() and sorted(p.name for p in tmp_path.rglob("*.json")) == [
        "student-v1.json",
        "student-v2.json",
        "student-v3.json",
    ]
    assert run.commands == ["rclone copy data bucket:ratchet"] * 3  # the boms/ copies are still uploaded
    assert live.write_boms(led, _version(led), trainer) and len(run.calls) == 3  # no BOM yet: nothing to write


@pytest.mark.req("NFR-2")
def test_reload_adapters_loads_live_canary_shadow(tmp_path, monkeypatch):
    _live_env(monkeypatch, RATCHET_ADAPTER_ROOT="/mnt/adapters")
    led = Ledger(":memory:")
    s = _live_settings(tmp_path)
    vllm = FakeOpenAIServer()
    assert live.reload_adapters(s, led, http=_http(vllm)) == [] and vllm.calls == []  # nothing to reload
    assert live.reload_adapters(_live_settings(tmp_path, student_base=""), led) == []

    for state in ("retired", "live", "canary", "shadow", "rejected", "rolled_back", "candidate"):
        _version(led, state)
    # student-v3 survived the restart: vLLM says it is already loaded, and the reload carries on.
    vllm = FakeOpenAIServer(load=lambda body: (400, "already loaded") if body["lora_name"] == "student-v3" else (200, ""))
    assert live.reload_adapters(s, led, http=_http(vllm)) == ["student-v2", "student-v3", "student-v4"]
    loads = vllm.calls_to("/load_lora_adapter")
    assert [c.json for c in loads] == [
        {"lora_name": f"student-v{i}", "lora_path": f"/mnt/adapters/student-v{i}"} for i in (2, 3, 4)
    ]
    assert all(c.headers["authorization"] == f"Bearer {STUDENT_KEY}" for c in loads)

    # A real failure is reported, not swallowed.
    with pytest.raises(httpx.HTTPStatusError):
        live.reload_adapters(s, led, http=httpx.Client(transport=_failing(503)))


@pytest.mark.req("FR-8.4", "FR-3.6", "FR-15.2", "FR-16.4")
def test_live_main_tick_reload_night_offline(tmp_path, monkeypatch, capsys):
    seed, public_key = bom.keygen()
    data, db = tmp_path / "data", tmp_path / "live.db"
    sync = "aws s3 sync ./data s3://customer-bucket/ratchet"
    _live_env(
        monkeypatch,
        RATCHET_DB=str(db),
        RATCHET_DATA_DIR=str(data),
        RATCHET_SYNC_CMD=sync,
        RATCHET_STUDENT_BASE=STUDENT_BASE,
        RATCHET_STUDENT_KEY=STUDENT_KEY,
        RATCHET_TEACHER_BASE=TEACHER_BASE,
        NEBIUS_API_KEY=TEACHER_KEY,
        RATCHET_JUDGE_MODEL=JUDGE,
        RATCHET_SAMPLES="3",
        RATCHET_BOM_SIGNING_KEY=seed,
        RATCHET_CERT_DELTA="0.1",
    )
    _examples(Ledger(str(db)), train=60, golden=6)  # the live ledger after some days of teacher answers
    vllm = FakeOpenAIServer(_answer_like_the_teacher)
    token_factory = FakeOpenAIServer('{"score": 1, "reason": "same answer"}')
    factory_calls: list[tuple[str, str, dict[str, Any]]] = []

    def factory(base_url: str, api_key: str, **options: Any) -> Any:
        factory_calls.append((base_url, api_key, options))
        server = vllm if base_url == STUDENT_BASE else token_factory
        return OpenAICompatProvider(base_url, api_key, transport=server.transport, **options)

    copies_at_sync: list[list[str]] = []  # the adapter folders holding a BOM copy each time the bucket is synced
    run = RecordingRun(
        on_call=lambda argv: (
            copies_at_sync.append(sorted(p.parent.name for p in data.glob("adapters/*/ratchet_bom.json")))
            if " ".join(argv) == sync
            else None
        )
    )

    def main(*argv: str) -> str:
        live.main(list(argv), provider_factory=factory, run=run, http=_http(vllm))
        return capsys.readouterr().out

    assert main("tick") == "" and main("reload") == "reloaded: []\n"
    assert vllm.calls == [] and factory_calls == [] and run.calls == []

    out = main("night")
    boms = data / "boms"
    doc = json.loads((boms / "student-v1.json").read_text(encoding="utf-8"))
    led = Ledger(str(db))
    assert doc == led.version_bom(1) and doc["manifest"]["adapter"]["name"] == "student-v1"
    assert (doc["manifest"]["training"]["examples"], doc["manifest"]["golden"]["examples"]) == (60, 6)
    assert bom.verify(doc, public_key)[0]  # signed with RATCHET_BOM_SIGNING_KEY
    assert f"bill of materials: {boms / 'student-v1.json'}" in out and "[trained] v1 on 60 examples" in out
    # A copy inside the adapter's folder, beside the weights the job wrote there, uploaded by a second sync (FR-16.4).
    inside = data / "adapters" / "student-v1" / "ratchet_bom.json"
    assert json.loads(inside.read_text(encoding="utf-8")) == doc and f"in the adapter folder: {inside}" in out
    assert copies_at_sync == [[], ["student-v1"]]
    assert doc["manifest"]["training"]["commitment"]["size"] == 60 == len(led.version_leaves(1, "train"))
    assert [v.state for v in led.versions()] == ["canary"]  # cold start: straight to canary
    # The training job's wall-clock time is a compute interval for the cost report (FR-15.2), at the default L40S rate.
    (job,) = led.compute_intervals("training_job")
    assert (job["source"], job["version"], job["usd_per_hour"], job["gpus"]) == ("night", 1, 1.55, 1)
    assert job["end_ts"] is not None and job["end_ts"] >= job["start_ts"]
    # The judge is a teacher call (teacher timeouts and retries). The student keeps its own timeouts but is retried like
    # the teacher, because no teacher stands behind it in the golden eval (ADR 0014). All defaults: _live_env cleared them.
    retry = RetryPolicy(max_attempts=3, deadline_s=180.0)
    assert factory_calls == [
        (STUDENT_BASE, STUDENT_KEY, {"timeout": timeouts(15.0, connect_s=2.0), "retry": retry}),
        (TEACHER_BASE, TEACHER_KEY, {"timeout": timeouts(120.0), "retry": retry}),
    ]
    assert run.commands == [sync, "bash deploy/train_job.sh student-v1", sync]
    assert len(train.load_rows(str(data / "night" / "train.jsonl"))) == 60
    # The adapter was hot-loaded, then sampled on the golden set as in production; the judge got JSON mode.
    assert [c.json["lora_name"] for c in vllm.calls_to("/load_lora_adapter")] == ["student-v1"]
    sampled = [c.json for c in vllm.calls_to("/chat/completions") if c.json.get("logprobs")]
    assert len(sampled) == 6 and all(b["model"] == "student-v1" and b["n"] == 3 and b["temperature"] == 0.7 for b in sampled)
    judged = token_factory.calls_to("/chat/completions")
    assert len(judged) == 6 and all(c.json["model"] == JUDGE for c in judged)
    assert all(c.json["response_format"] == {"type": "json_object"} for c in judged)
    assert all(c.headers["authorization"] == f"Bearer {TEACHER_KEY}" for c in judged)
    assert led.version(1).eval["judge_pass_rate"] == 1.0 and led.version(1).eval["precision"] == 1.0
    # The certificate (FR-16.3) is signed with the BOM, at the settings' level (RATCHET_CERT_DELTA).
    cert = doc["manifest"]["eval"]["certificate"]
    assert (cert["calibration"], cert["delta"], cert["target"], cert["served"]) == ("point", 0.1, 0.95, 3), doc["manifest"][
        "eval"
    ]

    # After an endpoint restart, reload loads the canary again; tick leaves a canary without audits where it is.
    assert main("reload").splitlines()[0] == "reloaded: ['student-v1']"
    assert len(vllm.calls_to("/load_lora_adapter")) == 2
    main("tick")
    # No command means night: a release is in flight, so nothing trains and no new BOM appears.
    assert "release in flight" in main()
    assert run.commands == [sync, "bash deploy/train_job.sh student-v1", sync] and sorted(p.name for p in boms.iterdir()) == [
        "student-v1.json"
    ]
    assert len(Ledger(str(db)).compute_intervals("training_job")) == 1  # no training, no GPU time
    # The next trained night, with the upload after it failing: the copies stay local and the command fails at the end.
    led = Ledger(str(db))
    led.set_version(1, "live", 100)
    _examples(led, train=60, golden=0, prefix="later")

    def fail_after_training(argv: list[str]) -> None:
        if " ".join(argv) == sync and (data / "adapters" / "student-v2").is_dir():
            raise subprocess.CalledProcessError(1, argv)

    run.on_call = fail_after_training
    with pytest.raises(SystemExit, match="RATCHET_SYNC_CMD failed after the night"):
        main("night")
    assert "RATCHET_SYNC_CMD failed (CalledProcessError" in capsys.readouterr().err
    assert (data / "adapters" / "student-v2" / "ratchet_bom.json").is_file() and (boms / "student-v2.json").is_file()
    assert Ledger(str(db)).version_bom(2) is not None  # the night itself is done
    with pytest.raises(SystemExit, match="unknown command 'bogus'"):
        live.main(["bogus"], provider_factory=factory, run=run, http=_http(vllm))


@pytest.mark.req("FR-8.1", "FR-8.3", "FR-1.8", "INV-7")
def test_live_runtime_wiring(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    teacher, student = FakeOpenAIServer("teacher says hi"), FakeOpenAIServer("student says hi")
    with pytest.raises(ValueError, match="judge"):
        gw.Runtime(_live_settings(tmp_path), transports={"judge": teacher.transport})
    rt = gw.Runtime(
        _live_settings(tmp_path, mock_warmup_days=3), transports={"teacher": teacher.transport, "student": student.transport}
    )
    assert rt.settings.mode == "live" and not hasattr(rt, "fleet")
    assert (rt.ledger.day, rt.ledger.versions(), rt.ledger.events()) == (0, [], [])  # the mock warm-up never runs live
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app)
    vid = _version(rt.ledger, "live", {"threshold": 0.5})

    # A confident student answers from its LoRA adapter on the vLLM endpoint, with the endpoint's token.
    r = c.post(CHAT, json={"messages": _ask(1)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["ratchet"]["served_by"], body["model"], body["choices"][0]["message"]["content"]) == (
        "student",
        "student-v1",
        "student says hi",
    )
    (call,) = student.calls
    assert call.url == f"{STUDENT_BASE}/chat/completions" and call.headers["authorization"] == f"Bearer {STUDENT_KEY}"
    assert call.json["model"] == rt.ledger.version(vid).adapter_uri == "student-v1" and call.json["messages"] == _ask(1)
    assert {k: call.json[k] for k in ("n", "temperature", "logprobs")} == {"n": 3, **STUDENT_SAMPLING}
    assert teacher.calls == []

    # Unsure: the teacher on Token Factory answers with its own key and model, and its answer becomes training data.
    rt.ledger.update_eval(vid, {"threshold": 0.99})
    body = c.post(CHAT, json={"messages": _ask(2)}).json()
    assert (body["ratchet"]["served_by"], body["ratchet"]["escalated"], body["model"]) == ("teacher", True, OPEN_TEACHER)
    (call,) = teacher.calls
    assert call.url == f"{TEACHER_BASE}/chat/completions" and call.headers["authorization"] == f"Bearer {TEACHER_KEY}"
    assert call.json["model"] == OPEN_TEACHER and "logprobs" not in call.json
    (ex,) = rt.ledger.examples()
    assert (ex["teacher_model"], ex["completion"]) == (OPEN_TEACHER, "teacher says hi")

    # INV-7: a student endpoint returning 500 falls back to the teacher; a failing teacher is a 502, not a crash.
    down = gw.Runtime(
        _live_settings(tmp_path, db_path=str(tmp_path / "down.db")),
        transports={"teacher": teacher.transport, "student": _failing()},
    )
    monkeypatch.setattr(gw, "rt", down)
    _version(down.ledger, "live", {"threshold": 0.5})
    body = c.post(CHAT, json={"messages": _ask(3)}).json()
    assert (body["ratchet"]["served_by"], body["ratchet"]["escalated"]) == ("teacher", True)
    assert [e["kind"] for e in down.ledger.events()] == ["student_error"]
    dead = _live_settings(tmp_path, db_path=str(tmp_path / "dead.db"))
    waits: list[float] = []
    dead_rt = gw.Runtime(dead, transports={"teacher": _failing(), "student": _failing()}, sleep=waits.append)
    monkeypatch.setattr(gw, "rt", dead_rt)
    r = c.post(CHAT, json={"messages": _ask(4)})
    assert r.status_code == 502 and r.json()["detail"] == "teacher unavailable: upstream 500"
    assert r.json()["error"]["code"] == "teacher_unavailable" and len(waits) == 2  # retried twice first (ADR 0014)

    # FR-8.3: no student endpoint, so the hosted untuned Nano on Token Factory answers, under the global threshold (the
    # version's calibrated 0.2 belongs to its adapter, not to the fallback model).
    hosted = FakeOpenAIServer("hosted nano says hi")
    fb = gw.Runtime(
        _live_settings(tmp_path, db_path=str(tmp_path / "fallback.db"), student_base="", confidence_threshold=0.99),
        transports={"teacher": hosted.transport},
    )
    monkeypatch.setattr(gw, "rt", fb)
    _version(fb.ledger, "live", {"threshold": 0.2})
    body = c.post(CHAT, json={"messages": _ask(5)}).json()
    assert body["ratchet"]["served_by"] == "teacher"  # confidence about 0.98 is under the global 0.99
    assert [(x.json["model"], x.json.get("logprobs", False)) for x in hosted.calls] == [(FALLBACK, True), (OPEN_TEACHER, False)]
    assert all(x.headers["authorization"] == f"Bearer {TEACHER_KEY}" for x in hosted.calls)
    fb.settings.confidence_threshold = 0.9
    body = c.post(CHAT, json={"messages": _ask(6)}).json()
    assert (body["ratchet"]["served_by"], body["model"], body["choices"][0]["message"]["content"]) == (
        "student",
        FALLBACK,
        "hosted nano says hi",
    )


# ---------- live mode: which model the pages name (FR-7.40) ----------
CERTIFIED_EVAL = {
    "threshold": 0.42,
    "certificate": {
        "method": "clopper_pearson",
        "certified": True,
        "target": 0.95,
        "delta": 0.05,
        "threshold": 0.42,
        "lcb": 0.97,
    },
}
NO_STUDENT = {"kind": "none", "model": None, "version": None, "threshold": None, "certified": False}


@pytest.mark.req("FR-7.40", "FR-8.3")
def test_stats_student_serving_kinds(gw_env, tmp_path, monkeypatch):
    """`/ratchet/stats` names the model a student call goes to: the mock fleet's adapter, our adapter on the endpoint, the
    hosted untuned fallback without RATCHET_STUDENT_BASE, or nobody on a cold start (the teacher answers everything)."""
    gw = gw_env()
    c = TestClient(gw.app)
    teacher = FakeOpenAIServer("teacher says hi")

    def stats(rt: Any) -> dict[str, Any]:
        monkeypatch.setattr(gw, "rt", rt)
        body = c.get("/ratchet/stats").json()
        assert body["fallback_student"] == rt.settings.fallback_student_model
        return body["student_serving"]

    # Mock mode: `mock` with or without a version; Night School's adapter names never equal the fallback id.
    mock_rt = gw.rt
    assert stats(mock_rt) == {**NO_STUDENT, "kind": "mock"}
    mock_rt.simulate_day(300, "mixed")
    (v,) = mock_rt.ledger.versions()
    assert re.fullmatch(r"student-v\d+", v.adapter_uri) and v.adapter_uri != mock_rt.settings.fallback_student_model
    got = stats(mock_rt)
    assert (got["kind"], got["model"], got["version"]) == ("mock", v.adapter_uri, v.id)  # a first student, in canary
    assert got["threshold"] == v.eval["threshold"]

    # Live, no student endpoint: nobody on a cold start, then the hosted fallback for a live version.
    hosted = gw.Runtime(
        _live_settings(tmp_path, db_path=str(tmp_path / "h.db"), student_base=""), transports={"teacher": teacher.transport}
    )
    assert stats(hosted) == NO_STUDENT
    vid = _version(hosted.ledger, "live", CERTIFIED_EVAL)
    assert stats(hosted) == {"kind": "hosted_fallback", "model": FALLBACK, "version": vid, "threshold": 0.8, "certified": False}

    # Live with the endpoint: our adapter, under its own calibrated threshold, certified by its certificate.
    endpoint = gw.Runtime(_live_settings(tmp_path, db_path=str(tmp_path / "e.db")), transports={"teacher": teacher.transport})
    assert stats(endpoint) == NO_STUDENT
    first = _version(endpoint.ledger, "canary", {"threshold": 0.6})  # a cold start's first student serves in canary
    assert stats(endpoint) == {"kind": "endpoint", "model": "student-v1", "version": first, "threshold": 0.6, "certified": False}
    vid = _version(endpoint.ledger, "live", CERTIFIED_EVAL)  # with a live version, the live one is named
    assert stats(endpoint) == {"kind": "endpoint", "model": "student-v2", "version": vid, "threshold": 0.42, "certified": True}
    assert teacher.calls == []  # reading the stats calls no model


@pytest.mark.req("FR-7.40", "FR-8.3")
def test_hosted_fallback_is_never_certified(gw_env, tmp_path, monkeypatch):
    """The version's certificate was computed for our adapter, so it never covers the hosted model: `certified` is false
    and the threshold is the global one the router holds the fallback to. A certificate that does not certify is not
    `certified` on the endpoint either."""
    gw = gw_env()
    c = TestClient(gw.app)
    teacher = FakeOpenAIServer("teacher says hi")
    settings = _live_settings(tmp_path, student_base="", confidence_threshold=0.85)
    rt = gw.Runtime(settings, transports={"teacher": teacher.transport})
    monkeypatch.setattr(gw, "rt", rt)
    vid = _version(rt.ledger, "live", CERTIFIED_EVAL)
    got = c.get("/ratchet/stats").json()["student_serving"]
    assert got["certified"] is False and got["threshold"] == settings.confidence_threshold == 0.85
    assert got["threshold"] == rt.ratchet.threshold(rt.ledger.version(vid), FALLBACK)

    rt.settings.student_base = STUDENT_BASE  # the same ledger behind an endpoint
    got = c.get("/ratchet/stats").json()["student_serving"]
    assert (got["kind"], got["certified"], got["threshold"]) == ("endpoint", True, 0.42)
    uncertified = {**CERTIFIED_EVAL, "certificate": {**CERTIFIED_EVAL["certificate"], "certified": False, "lcb": 0.81}}
    rt.ledger.update_eval(vid, uncertified)
    assert c.get("/ratchet/stats").json()["student_serving"]["certified"] is False


@pytest.mark.req("FR-7.40", "FR-8.3")
def test_hosted_fallback_response_names_the_fallback_model(gw_env, tmp_path, monkeypatch):
    """A student answer from the hosted fallback says so: the response's `model` is the fallback id (what Token Factory
    served), `ratchet.served_by` stays `student`, and a Scout step built from the same result carries that id, which
    the pages compare with `fallback_student`."""
    gw = gw_env()
    hosted = FakeOpenAIServer("hosted nano says hi")
    rt = gw.Runtime(_live_settings(tmp_path, student_base=""), transports={"teacher": hosted.transport})
    monkeypatch.setattr(gw, "rt", rt)
    _version(rt.ledger, "live", {"threshold": 0.2})
    c = TestClient(gw.app)
    body = c.post(CHAT, json={"messages": _ask(1)}).json()
    assert (body["ratchet"]["served_by"], body["model"]) == ("student", FALLBACK)
    assert (
        body["model"] == c.get("/ratchet/stats").json()["fallback_student"] == c.get("/scout/status").json()["fallback_student"]
    )
    assert [x.json["model"] for x in hosted.calls] == [FALLBACK]
    step = Call.from_result(rt.ratchet.handle(_ask(2)))
    assert (step.served_by, step.model) == ("student", FALLBACK)


@pytest.mark.req("FR-7.40")
def test_scout_status_has_fallback_student(gw_env, tmp_path, monkeypatch):
    """`/scout/status` carries the fallback id and the serving student, so the Scout page labels each call without a
    stats call; the guard's keys are still there."""
    gw = gw_env()
    c = TestClient(gw.app)
    st = c.get("/scout/status").json()
    assert st["fallback_student"] == gw.rt.settings.fallback_student_model and st["student_serving"]["kind"] == "mock"
    assert {"mode", "budget_usd", "spent_today_usd", "runs_today", "runs_per_hour_per_ip", "max_runs_per_day", "tavily"} <= set(
        st
    )

    rt = gw.Runtime(_live_settings(tmp_path, student_base=""), transports={"teacher": FakeOpenAIServer().transport})
    monkeypatch.setattr(gw, "rt", rt)
    vid = _version(rt.ledger, "live", CERTIFIED_EVAL)
    st = c.get("/scout/status").json()
    assert st["mode"] == "live" and st["fallback_student"] == FALLBACK
    assert st["student_serving"] == c.get("/ratchet/stats").json()["student_serving"]
    assert (st["student_serving"]["kind"], st["student_serving"]["version"]) == ("hosted_fallback", vid)


# ---------- live mode: evaluation ----------
@pytest.mark.req("FR-5.1", "FR-5.5", "FR-2.1")
def test_live_evaluator_uses_production_sampling():
    with pytest.raises(TypeError):
        STUDENT_SAMPLING["temperature"] = 0.0  # type: ignore[index]  # read-only, so no caller can drift alone
    rows = [
        {"task": TRIAGE, "messages": [{"role": "user", "content": f"ticket {i}"}, {"role": "assistant", "content": LABELS}]}
        for i in range(4)
    ] + [
        {"task": "default", "messages": [{"role": "user", "content": f"say {w}"}, {"role": "assistant", "content": w}]}
        for w in ("yes", "no")
    ]
    student = RecordingProvider(lambda m, model: LABELS if "ticket" in m[-1]["content"] else "yes")

    def judge_says(messages: list[dict[str, Any]], model: str) -> str:
        sent = json.loads(messages[-1]["content"])
        return '{"score": 1}' if sent["candidate"] == sent["reference"] else "{}"

    judge = RecordingProvider(judge_says)
    res = LiveEvaluator(student, judge, JUDGE, samples=4).evaluate("student-v7", rows)

    sampled = [c for c in student.calls if c.kwargs.get("logprobs")]
    assert [c.messages for c in sampled] == [r["messages"][:-1] for r in rows]  # one cascade call per golden row
    # The router's student parameters for a row stored without conditions: the student's token cap only (FR-5.1).
    plain = {"n": 4, **STUDENT_SAMPLING, "params": GenParams(max_tokens=DEFAULT_STUDENT_MAX_TOKENS)}
    assert all(c.model == "student-v7" and c.kwargs == plain for c in sampled)
    candidates = [c for c in student.calls if not c.kwargs.get("logprobs")]  # the judge's candidates (free text only)
    assert [c.messages for c in candidates] == [r["messages"][:-1] for r in rows[4:]]
    assert all(c.model == "student-v7" and c.kwargs["params"] == plain["params"] for c in candidates)

    # FR-5.5: the judge sees request, reference and candidate in JSON mode, and grades free-text rows only.
    assert len(judge.calls) == 2
    for call, row in zip(judge.calls, rows[4:], strict=True):
        assert call.model == JUDGE and call.kwargs == {"response_format": {"type": "json_object"}}
        assert call.messages[0] == {"role": "system", "content": JUDGE_PROMPT}
        sent = json.loads(call.messages[1]["content"])
        assert sent == {"request": row["messages"][:-1], "reference": row["messages"][-1]["content"], "candidate": "yes"}
    assert res["judge_pass_rate"] == 0.5 and res["golden_n"] == 6  # "yes" passes, the "no" row gets "{}" and scores 0

    # A judge answer that is not a clear {"score": 1} scores 0; fences and reasoning around valid JSON are fine.
    ev = LiveEvaluator(student, RecordingProvider("x"), JUDGE)
    for text, want in (
        ("not json", 0),
        ("", 0),
        ("[1]", 0),
        ('{"score": "1"}', 0),
        ('{"score": 2}', 0),
        ('{"reason": "close"}', 0),
        ('{"score": 0, "reason": "wrong"}', 0),
        ('```json\n{"score": 1}\n```', 1),
        ('<think>compare</think>{"score": 1, "reason": "same"}', 1),
    ):
        ev.judge = RecordingProvider(text)
        assert ev.judge_score(rows[4]["messages"][:-1], "yes", "yes") == want, text
    # No judge, or no free-text rows: no judge calls and no judge_pass_rate.
    assert "judge_pass_rate" not in LiveEvaluator(student, None).evaluate("student-v7", rows)
    judge = RecordingProvider('{"score": 1}')
    assert "judge_pass_rate" not in LiveEvaluator(student, judge, JUDGE).evaluate("student-v7", rows[:4])
    assert judge.calls == []


@pytest.mark.req("FR-5.1", "FR-2.1")
def test_production_and_both_evaluators_share_student_sampling(monkeypatch):
    """The router, the live evaluator and the mock evaluator sample the student with the same parameters (FR-5.1)."""
    seen: dict[str, dict[str, Any]] = {}

    student = RecordingProvider(LABELS)
    led = Ledger(":memory:")
    _version(led, "live", {"threshold": 0.5})
    Ratchet(
        Settings(teacher_model=OPEN_TEACHER, samples=3, audit_rate=0.0, student_params="", student_max_tokens=1024),
        led,
        RecordingProvider(LABELS),
        lambda v: (student, v.adapter_uri),
    ).handle(_ask(), TRIAGE)
    seen["router"] = student.calls[0].kwargs

    item = make_stream("tickets", seed=7).sample(1)
    row = {"task": item.task, "messages": item.messages() + [{"role": "assistant", "content": item.answer(item.labels)}]}
    live_student = RecordingProvider(LABELS)
    LiveEvaluator(live_student, None, samples=3).evaluate("student-v1", [row])
    seen["live"] = live_student.calls[0].kwargs

    real = mock_models.MockStudent.complete

    def spy(self, messages, model, *args, **kwargs):
        seen.setdefault("mock", kwargs)
        return real(self, messages, model, *args, **kwargs)

    monkeypatch.setattr(mock_models.MockStudent, "complete", spy)
    fleet = mock_models.MockFleet()
    fleet.train([row], "base-model", "student-v1")
    fleet.evaluate("student-v1", [row])
    want = {"n": 3, **STUDENT_SAMPLING, "params": GenParams(max_tokens=DEFAULT_STUDENT_MAX_TOKENS)}
    assert seen["router"] == seen["live"] == seen["mock"] == want


@pytest.mark.req("FR-4.5")
def test_train_parse_args_defaults_and_row_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("RATCHET_STUDENT_BASE_MODEL", raising=False)
    a = train.parse_args(["--data", "/data/night/train.jsonl", "--out", "/data/adapters/student-v3"])
    assert (a.data, a.out, a.epochs, a.lr, a.rank, a.max_seq, a.batch) == (
        "/data/night/train.jsonl",
        "/data/adapters/student-v3",
        2.0,
        2e-4,
        16,
        2048,
        8,
    )
    # Nemotron-H mixes attention, Mamba and MLP layers: the default targets include the Mamba projections.
    assert {"q_proj", "o_proj", "in_proj", "out_proj"} <= set(a.targets.split(","))
    # The base model the job trains is the one the gateway, the job script and the endpoint script name.
    assert a.base == Settings().student_base_model
    for script in ("train_job.sh", "student_endpoint.sh"):
        text = (ROOT / "deploy" / script).read_text(encoding="utf-8")
        assert f'BASE="${{RATCHET_STUDENT_BASE_MODEL:-{a.base}}}"' in text, script
    for argv in (["--out", "o"], ["--data", "d"]):  # both paths are required
        with pytest.raises(SystemExit):
            train.parse_args(argv)
    assert train.parse_args(["--data", "d", "--out", "o", "--targets", "all-linear", "--epochs", "0.5"]).epochs == 0.5

    # The row contract between Night School and the GPU job: the rows NebiusTrainer writes are what train.py reads.
    led = Ledger(":memory:")
    _examples(led, train=4, golden=1)
    rows = dataset.rows(led, "train")
    path = dataset.write_training(rows, tmp_path / "night" / "train.jsonl")
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")  # blank lines are skipped
    loaded = train.load_rows(str(path))
    assert loaded == [dataset.training_record(r) for r in rows] and len(loaded) == 4
    train.check_rows(loaded)
    stored = {r["hash"]: r["completion"] for r in led.examples(split="train")}
    for r in loaded:
        # TRL's completion-only loss learns the completion: the teacher's stored answer (INV-2), after the prompt.
        assert r["completion"] == [{"role": "assistant", "content": stored[r["provenance"]["hash"]]}]
        assert {"teacher", "license"} <= set(r["provenance"])
    # ratchet_manifest.json, saved next to the adapter, names its base model, the licenses it learned from and the
    # chat-template arguments it was trained under.
    manifest = json.loads(json.dumps(train.manifest("base-model", loaded), default=list))
    lic = licenses.REGISTRY[OPEN_TEACHER].name
    assert manifest == {
        "base_model": "base-model",
        "examples": 4,
        "provenance": [[OPEN_TEACHER, lic]],
        "chat_template_kwargs": {"enable_thinking": False},
    }


# ---------- operations ----------
@pytest.mark.req("FR-10.6", "FR-1.7")
def test_scout_cli_over_http(gw_env, monkeypatch, capsys):
    key = "scout-cli-key-0123456789abcdef"
    gw = gw_env(RATCHET_API_KEYS=f"cli:{key}", RATCHET_TEACHER_MODEL=OPEN_TEACHER)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)  # simulated search, which matches the mock gateway
    monkeypatch.delenv("RATCHET_API_KEY", raising=False)
    client = httpx.Client(transport=AppTransport(gw.app))
    question = EXAMPLE_QUESTIONS[0]
    base = ["--base-url", "http://testserver/v1", "--max-results", "3"]

    assert scout_main([question, *base, "--api-key", key], client=client) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"question: {question}  (search: {MockSearch.name})"
    agents = [ln.split()[0] for ln in lines[1:] if ln.startswith("  ") and "$" in ln]
    assert agents[0] == "planner" and agents[-1] == "writer" and {"searcher", "triage", "reader"} <= set(agents)
    found = [int(n) for n in re.findall(r"-> (\d+) results", "\n".join(lines))]
    assert found and found[0] == 3 and max(found) == 3  # --max-results reached the search tool
    assert any(re.match(r"  \[\d+\] .+ - https://", ln) for ln in lines)  # citations
    assert re.search(r"\d+/\d+ LLM calls answered by the student; \$[\d.]+ vs \$[\d.]+ all-Ultra", lines[-1])

    # Every agent call reached the gateway with the key (recorded under its name) and its task header.
    rows = [dict(r) for r in gw.rt.ledger.db.execute("SELECT task, client FROM requests")]
    assert {r["client"] for r in rows} == {"cli"}
    tasks_seen = [r["task"] for r in rows]
    assert tasks_seen.count("research-plan") == tasks_seen.count("research-write") == 1
    assert {"source-triage", "page-read"} <= set(tasks_seen)
    assert len(rows) == agents.count("planner") + agents.count("triage") + agents.count("reader") + agents.count("writer")

    # The key can come from RATCHET_API_KEY; a wrong key fails the run with exit code 1 and the gateway's 401.
    monkeypatch.setenv("RATCHET_API_KEY", key)
    assert scout_main([question, *base], client=client) == 0
    capsys.readouterr()
    assert scout_main([question, *base, "--api-key", "wrong-key-0123456789abcdef"], client=client) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: HTTPStatusError") and "401" in err


@pytest.mark.req("FR-9.4", "FR-15.3")
def test_sim_cli_writes_json_and_table(tmp_path, capsys):
    out = tmp_path / "sim.json"
    simulate.main(["--days", "2", "--requests", "200", "--drift-day", "0", "--workload", "mixed", "--out", str(out)])
    printed = capsys.readouterr().out
    res = json.loads(out.read_text(encoding="utf-8"))
    assert set(res) == {
        "days", "nights", "versions", "events", "totals", "bill_of_materials", "cost", "exposure", "rollbacks", "served",
    }  # fmt: skip
    assert res["exposure"] == {} and res["rollbacks"] == []  # nothing rolled back in two days
    assert [d["day"] for d in res["days"]] == [1, 2] and [n["night"] for n in res["nights"]] == [1, 2]
    assert res["totals"]["requests"] == 400 and res["nights"][0]["trained"]
    # The cost report (FR-15.3): the simulated endpoint ran 24 h a day, and the GPU-hour view is printed as simulation.
    cost = res["cost"]
    assert cost["requests"] == 400 and cost["endpoint_hours"] == pytest.approx(48.0) and cost["training_hours"] > 0
    assert f"recommendation: {cost['recommendation']}" in printed and "by GPU-hours (simulation" in printed
    lines = printed.splitlines()
    assert lines[0].split() == ["day", "student", "escal.", "audit", "ok", "$/1k", "all-Ultra", "saved"]
    assert [ln.split()[0] for ln in lines[1:3]] == ["1", "2"]  # one table row per day
    assert f"total: 400 requests, ${res['totals']['cost_usd']:.2f} vs ${res['totals']['baseline_usd']:.2f}" in printed
    assert "events:" in printed and "[trained]" in printed and lines[-1] == f"wrote {out}"
    for e in res["events"]:
        assert f"  day {e['day']}: [{e['kind']}] {e['detail']}" in lines


@pytest.mark.req("FR-9.4", "FR-16.1")
def test_sim_cli_compares_release_rules(tmp_path, capsys):
    """--compare-release runs the same arguments and seed once per canary rule and prints one row per rule for each
    version either rule rolled back (simulation); bad combinations are usage errors (exit 2)."""
    out = tmp_path / "cmp.json"
    argv = ["--days", "2", "--requests", "200", "--drift-day", "0", "--canary-audit-rate", "0.5", "--out", str(out)]
    simulate.main([*argv, "--compare-release", "--promote-evidence"])
    printed = capsys.readouterr().out
    res = json.loads(out.read_text(encoding="utf-8"))
    assert set(res) == {"wilson", "sequential"} and printed.startswith("Wilson vs anytime-valid release gate (simulation")
    assert res["wilson"]["totals"]["requests"] == res["sequential"]["totals"]["requests"] == 400
    results = {
        "wilson": {
            "versions": [{"id": 3, "state": "rolled_back"}],
            "rollbacks": [{"version": 3, "audits": 17, "agreed": 7, "served": 145, "day": 4}],
            "served": {3: 145},
            "totals": {"savings_pct": 0.69},
        },
        "sequential": {
            "versions": [{"id": 3, "state": "rolled_back"}, {"id": 4, "state": "rolled_back"}],
            "rollbacks": [
                {"version": 3, "audits": 7, "agreed": 3, "served": 48, "day": 4},
                {"version": 4, "audits": 3, "agreed": 0, "served": 20, "day": 6},
            ],
            "served": {3: 48, 4: 20},
            "totals": {"savings_pct": 0.7},
        },
    }
    rows = [line.split() for line in simulate.compare_table(results).splitlines()[2:]]
    assert rows == [
        ["wilson", "v3", "rolled_back", "17", "145", "4", "69%"],
        ["sequential", "v3", "rolled_back", "7", "48", "4", "70%"],
        ["wilson", "v4", "not", "trained", "-", "0", "-", "69%"],
        ["sequential", "v4", "rolled_back", "3", "20", "6", "70%"],
    ]
    for bad in (["--promote-evidence"], ["--canary-audit-rate", "0"], ["--canary-audit-rate", "1.5"], ["--release", "x"]):
        with pytest.raises(SystemExit) as exc:
            simulate.main([*argv[:6], *bad])
        assert exc.value.code == 2


@pytest.mark.req("FR-9.4", "FR-16.3", "FR-6.30", "FR-6.31")
def test_sim_cli_certificate_flags(tmp_path, capsys):
    """--calibration, --cert-target, --cert-delta, --paired-gate and --min-certified reach the golden eval and the
    gate, and the table lists each trained version's certificate as simulation; bad values are usage errors (exit 2)."""
    out = tmp_path / "cert.json"
    argv = ["--days", "2", "--requests", "200", "--drift-day", "0", "--out", str(out)]
    simulate.main([*argv, "--calibration", "ltt", "--cert-target", "0.9", "--cert-delta", "0.1"])
    printed = capsys.readouterr().out
    res = json.loads(out.read_text(encoding="utf-8"))
    certs = [n["eval"]["certificate"] for n in res["nights"] if n["trained"]]
    assert certs and all((c["calibration"], c["target"], c["delta"]) == ("ltt", 0.9, 0.1) for c in certs)
    assert "certificates (simulation, mock models):" in printed
    assert f"  v1: {certs[0]['served'] - certs[0]['errors']}/{certs[0]['served']} served answers agree" in printed
    args = simulate.parser().parse_args([*argv, "--paired-gate", "--min-certified", "0.9"])
    _, _, fleet, _, gate = simulate.build(args)
    assert (gate.policy.paired_gate, gate.policy.min_certified_precision, fleet.calibration) == (1, 0.9, "point")
    for bad in (["--cert-target", "1"], ["--cert-delta", "0"], ["--min-certified", "1.5"], ["--calibration", "conformal"]):
        with pytest.raises(SystemExit) as exc:
            simulate.main([*argv, *bad])
        assert exc.value.code == 2


@pytest.mark.req("FR-16.5", "FR-9.4")
def test_sim_db_and_the_replay_cli(tmp_path, capsys, monkeypatch):
    """`python -m sim.simulate --db PATH` keeps the run's ledger in a new file (never an existing one, never with
    --compare-release), and `python -m ratchet.replay --db PATH` prints one table per version from it, labelled as
    simulation, or JSON; bad input exits 2."""
    import sqlite3

    from ratchet import replay
    from ratchet.ledger import Ledger

    db, out = tmp_path / "run.db", tmp_path / "run.json"
    simulate.main(["--days", "1", "--requests", "40", "--drift-day", "0", "--out", str(out), "--db", str(db)])
    assert capsys.readouterr().out.rstrip().endswith(f"replay it: python -m ratchet.replay --db {db}")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 40
    con.close()
    for bad in (["--db", str(db)], ["--db", str(tmp_path / "x.db"), "--compare-release"]):
        with pytest.raises(SystemExit) as exc:
            simulate.main(["--days", "1", "--requests", "10", *bad])
        assert exc.value.code == 2
    assert not (tmp_path / "x.db").exists()
    # A run long enough for a student to serve, kept in memory (a file commits every request) and then saved in one go.
    made: list[Ledger] = []

    class Kept(Ledger):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(simulate, "Ledger", Kept)
    simulate.main(["--days", "3", "--requests", "150", "--drift-day", "0", "--out", str(out)])
    capsys.readouterr()
    db = tmp_path / "three-days.db"
    saved = sqlite3.connect(db)
    made[0].db.backup(saved)
    saved.close()
    served = sorted({r[0] for r in made[0].db.execute("SELECT student_version FROM requests WHERE confidence IS NOT NULL")})
    assert served  # the first student served part of day 3 (seed 7: v1 was rejected, v2 is in its canary stage)
    assert replay.main(["--db", str(db), "--sweep", "0.5:0.9:0.1"]) == 0
    text = capsys.readouterr().out
    assert text.startswith(f"v{served[0]}: ") and "simulation (mock models)" in text and "suggested:" in text
    assert text.count("replayed requests") == len(served)  # one table per version, never pooled
    header = next(line for line in text.splitlines() if line.lstrip().startswith("threshold"))
    assert header.split() == ["threshold", "coverage", "precision", "95%", "interval", "n_eff", "cost", "saved", "errors/1k"]
    assert replay.main(["--db", str(db), "--version", str(served[0]), "--json", "--audit-rate", "0.02"]) == 0
    (rep,) = json.loads(capsys.readouterr().out)
    assert rep["version"] == served[0] and rep["label"] == "simulation (mock models)" and rep["n"] > 0
    assert len(rep["rows"]) == 15 and {row["audit_rate"] for row in rep["rows"]} == {0.02}
    for bad in (
        ["--db", str(tmp_path / "missing.db")],
        ["--db", str(db), "--sweep", "0.9:0.5:0.1"],
        ["--db", str(db), "--sweep", "half"],
        ["--db", str(db), "--target", "1"],
        ["--db", str(db), "--audit-rate", "2"],
        ["--db", str(db), "--version", "99"],
        ["--db", str(db), "--since-day", "3", "--until-day", "2"],
    ):
        assert replay.main(bad) == 2, bad
        assert capsys.readouterr().err.startswith("error: "), bad
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.req("FR-16.3", "FR-8.3")
def test_live_evaluator_passes_its_certificate_settings():
    rows = [
        {"task": TRIAGE, "messages": [{"role": "user", "content": f"ticket {i}"}, {"role": "assistant", "content": LABELS}]}
        for i in range(8)
    ]
    student = RecordingProvider(LABELS)
    res = LiveEvaluator(student, None, calibration="ltt", cert_target=0.6, cert_delta=0.2).evaluate("student-v7", rows)
    cert = res["certificate"]
    assert (cert["calibration"], cert["target"], cert["delta"]) == ("ltt", 0.6, 0.2) and "ltt_trace" in cert
    # Four calibration rows, all right: 0.6^4 = 0.13 <= 0.2 certifies every grid threshold, so the floor (0.5) serves.
    assert res["threshold"] == 0.5 and (cert["served"], cert["errors"]) == (4, 0) and res["test_vector"] == "SSSS"
    assert LiveEvaluator(student, None).evaluate("student-v7", rows)["certificate"]["calibration"] == "point"


@pytest.mark.req("FR-9.6")
def test_mock_warmup_trains_a_student(gw_env):
    gw = gw_env(RATCHET_MOCK_WARMUP_DAYS="1", RATCHET_MOCK_WARMUP_REQUESTS="300", RATCHET_BOM_SIGNING_KEY="")
    led = gw.rt.ledger

    def check() -> None:
        assert led.day == 1 and led.totals()["requests"] == 300
        (v,) = led.versions()
        assert v.state == "canary" and v.train_examples >= 50 and led.version_bom(v.id) is not None
        kinds = [e["kind"] for e in led.events()]
        assert kinds[-1] == "warmup" and "trained" in kinds
        assert "1 simulated days of mixed traffic (300 requests/day)" in led.events()[-1]["detail"]
        assert {t["task"] for t in led.stats_by_task()} >= {TRIAGE, "source-triage", "page-read"}  # mixed traffic

    check()
    stats = TestClient(gw.app).get("/ratchet/stats").json()  # the dashboard opens with a trained student
    assert stats["day"] == 1 and stats["versions"][0]["state"] == "canary"
    gw.rt.reset()  # the demo reset warms up again, on a fresh ledger
    assert gw.rt.ledger is not led
    led = gw.rt.ledger
    check()


@pytest.mark.req("FR-10.5")
def test_trust_proxy_uses_forwarded_ip_only_when_enabled(gw_env):
    class SpyGuard(Guard):
        def __init__(self, runs_per_hour: int) -> None:
            super().__init__(None, runs_per_hour, clock=FakeClock())
            self.ips: list[str] = []

        def admit(self, ip: str, **kw: Any) -> str | None:
            self.ips.append(ip)
            return super().admit(ip, **kw)

    q = {"question": EXAMPLE_QUESTIONS[0]}
    forwarded = {"X-Forwarded-For": "198.51.100.7 , 10.0.0.2"}  # client, then the proxies it passed through

    for trust, want in (("1", "198.51.100.7"), ("0", "testclient")):
        gw = gw_env(RATCHET_TRUST_PROXY=trust)
        assert gw.rt.settings.trust_proxy is (trust == "1")
        gw.rt.guard = spy = SpyGuard(0)  # refuses every run, so nothing runs: only the key the guard saw matters
        c = TestClient(gw.app)
        assert c.post("/scout/run", json=q, headers=forwarded).status_code == 429
        assert c.post("/scout/run", json=q, headers={"X-Forwarded-For": ""}).status_code == 429
        assert c.post("/scout/run", json=q).status_code == 429
        with c.stream("GET", "/scout/stream", params={"q": q["question"]}, headers=forwarded) as s:
            assert s.status_code == 200 and '"type": "error"' in s.read().decode()
        assert spy.ips == [want, "testclient", "testclient", want], trust

    # With trust on, the per-IP hourly limit counts each forwarded client separately.
    gw = gw_env(RATCHET_TRUST_PROXY="1")
    gw.rt.guard = SpyGuard(1)
    c = TestClient(gw.app)
    assert c.post("/scout/run", json=q, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200
    assert c.post("/scout/run", json=q, headers={"X-Forwarded-For": "198.51.100.8"}).status_code == 200
    assert c.post("/scout/run", json=q, headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.2"}).status_code == 429


# ---------- Scout on the demo VM: a guard that survives restarts, judge access (FR-10.20, FR-10.21, ADR 0120) ----------
SCOUT_ANSWER = json.dumps(
    {
        "queries": ["lora basics"],
        "relevant": True,
        "source_type": "docs",
        "reason": "on topic",
        "answers_question": True,
        "fact_type": "definition",
        "key_fact": "LoRA trains small low-rank matrices.",
        "answer": "LoRA trains small low-rank matrices [1].",
        "used": [1],
    }
)
JUDGE_CODE = "fedcba9876543210fedcba98"


def _tavily(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"results": [{"title": "LoRA", "url": "https://a.example.org/lora", "content": "c"}], "usage": {"credits": 1}}
    )


def _scout_runtime(gw: Any, settings: Settings, clock: FakeClock) -> Any:
    teacher = FakeOpenAIServer(SCOUT_ANSWER)
    return gw.Runtime(
        settings, transports={"teacher": teacher.transport, "tavily": httpx.MockTransport(_tavily)}, wall_clock=clock
    )


@pytest.mark.req("FR-10.20", "INV-11")
def test_live_runtime_restart_keeps_the_scout_budget(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    clock = FakeClock()
    settings = _live_settings(
        tmp_path, student_base="", tavily_api_key="tvly-test", scout_daily_budget_usd=1e-6, scout_max_runs_per_day=30
    )
    q = {"question": "What is LoRA?"}
    a = _scout_runtime(gw, settings, clock)
    assert a.guard.status()["persistent"] is True and a.ledger.events() == []
    monkeypatch.setattr(gw, "rt", a)
    c = TestClient(gw.app)
    assert c.post("/scout/run", json=q).status_code == 200  # admitted under the budget; it costs more than the budget
    r = c.post("/scout/run", json=q)
    assert r.status_code == 429 and "budget" in r.json()["detail"]
    spent = a.guard.status()["spent_today_usd"]

    events = len(a.ledger.events())
    b = _scout_runtime(gw, settings, clock)  # `docker compose up -d --build`: a new process on the same ledger file
    assert len(b.ledger.events()) == events  # restoring writes no event
    monkeypatch.setattr(gw, "rt", b)
    r = c.post("/scout/run", json=q)
    assert r.status_code == 429 and "budget" in r.json()["detail"]
    st = c.get("/scout/status").json()
    assert (st["runs_today"], st["spent_today_usd"], st["persistent"]) == (1, spent, True) and spent > 1e-6
    clock.advance(86400)  # the next UTC day starts clean
    assert c.post("/scout/run", json=q).status_code == 200


@pytest.mark.req("FR-10.21")
def test_live_judge_cookie_is_secure(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    settings = _live_settings(
        tmp_path,
        student_base="",
        tavily_api_key="tvly-test",
        scout_judge_code=JUDGE_CODE,
        scout_judge_runs_per_day=1,
        scout_max_runs_per_day=1,
        scout_judge_budget_usd=None,
        tavily_monthly_credits=0,
        tavily_judge_credits=None,
    )
    rt = _scout_runtime(gw, settings, FakeClock())
    monkeypatch.setattr(gw, "rt", rt)
    c = TestClient(gw.app, base_url="https://testserver")  # httpx sends a Secure cookie over https only
    r = c.post("/scout/judge", json={"code": JUDGE_CODE})
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()
    assert c.get("/scout/status").json()["judge"]["session"] is True
    assert c.post("/scout/run", json={"question": "What is LoRA?"}).status_code == 200  # the judges-only run
    public = TestClient(gw.app, base_url="https://testserver")
    r = public.post("/scout/run", json={"question": "What is LoRA?"})
    assert r.status_code == 429 and "judge" not in r.json()["detail"].lower()  # a judges-only day: the public gets 429
    assert c.delete("/scout/judge").status_code == 204
    assert c.get("/scout/status").json()["judge"]["session"] is False


@pytest.mark.req("FR-11.1")
def test_caddy_log_drops_the_edge_token():
    """Caddy logs request headers and redacts only Cookie, Set-Cookie, Authorization and Proxy-Authorization, so the
    guarded mode's edge token must be filtered out of its access log (ADR 0120). `caddy validate` checks it on the VM."""
    caddyfile = (ROOT / "deploy" / "vm" / "Caddyfile").read_text(encoding="utf-8")
    log = caddyfile[caddyfile.index("\tlog {") :]
    assert "format filter {" in log and "request>headers>X-Ratchet-Admin-Token delete" in log and "wrap console" in log


_LEASES = "NFR-60 waits for lane D's leases table and Ledger.acquire_lease (OPS-NIGHT-LEASES-ALERTS); ratchet/lease.py"


@pytest.mark.req("NFR-60")
@pytest.mark.xfail(strict=True, reason=_LEASES)
def test_second_gateway_on_a_live_ledger_warns_by_default(tmp_path):
    from ratchet.lease import GatewayLease  # type: ignore[import-not-found]

    led, clock = Ledger(str(tmp_path / "lease.db"), auto_day=True), FakeClock()
    first = GatewayLease(led, "warn", "host:1:a", clock=clock, start_thread=False)
    second = GatewayLease(Ledger(str(tmp_path / "lease.db"), auto_day=True), "warn", "host:2:b", clock=clock, start_thread=False)
    first.beat()
    second.beat()
    assert [e["kind"] for e in led.events()].count("gateway_lease") == 1
    clock.advance(31)  # the first stopped beating: its lease expires and the second takes it over
    second.beat()
    assert led.lease_holder("gateway")[0] == "host:2:b"


@pytest.mark.req("NFR-60")
@pytest.mark.xfail(strict=True, reason=_LEASES)
def test_exclusive_gateway_refuses_a_held_lease_and_takes_an_expired_one(tmp_path):
    from ratchet.lease import GatewayLease  # type: ignore[import-not-found]

    clock, waits = FakeClock(), []
    holder = GatewayLease(Ledger(str(tmp_path / "lease.db")), "exclusive", "host:1:a", clock=clock, start_thread=False)
    holder.beat()
    with pytest.raises(RuntimeError, match="RATCHET_GATEWAY_LEASE"):
        GatewayLease(Ledger(str(tmp_path / "lease.db")), "exclusive", "host:2:b", clock=clock, sleep=waits.append).acquire()
    clock.advance(31)
    GatewayLease(Ledger(str(tmp_path / "lease.db")), "exclusive", "host:3:c", clock=clock, sleep=waits.append).acquire()


@pytest.mark.req("NFR-60")
@pytest.mark.xfail(strict=True, reason=_LEASES)
def test_lease_is_taken_in_the_lifespan_only(gw_env, tmp_path, monkeypatch):
    gw = gw_env()
    rt = _scout_runtime(gw, _live_settings(tmp_path), FakeClock())
    assert rt.ledger.lease_holder("gateway") is None  # building a Runtime takes no lease
    monkeypatch.setattr(gw, "rt", rt)
    with TestClient(gw.app):
        assert rt.ledger.lease_holder("gateway") is not None
    assert rt.ledger.lease_holder("gateway") is None


@pytest.mark.req("FR-8.5", "INV-9")
def test_day1_check_reports_missing_models_and_logprobs(day1_check, monkeypatch, capsys):
    want = list(day1_check.WANT)
    assert len(want) >= 4
    good = {
        "choices": [
            {
                "message": {"content": '<think>billing</think>```json\n{"category": "billing", "priority": "high"}\n```'},
                "logprobs": {"content": [{"token": "{", "logprob": -0.1}]},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 12},
    }

    def serve(
        present: list[str], special: dict[str, httpx.Response], gen: httpx.Response | None = None
    ) -> tuple[httpx.Client, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/v1/models":
                ids = present + ["nvidia/Some-Other-Nemotron", "meta/llama-3.3-70b"]
                return httpx.Response(200, json={"data": [{"id": m} for m in ids]})
            body = json.loads(request.content)
            if "seed" in body and gen is not None:  # the generation-parameter check
                return gen
            return special.get(body["model"]) or httpx.Response(200, json=good)

        return httpx.Client(transport=httpx.MockTransport(handler)), seen

    monkeypatch.setattr(day1_check, "BASE", "https://tf.test/v1")
    monkeypatch.setattr(day1_check, "TEACHER", want[0])
    monkeypatch.setattr(day1_check, "TEACHER_MAX_TOKENS", 8192)
    monkeypatch.setattr(day1_check, "KEY", "")
    client, seen = serve(want, {})
    assert day1_check.main(client) == 2 and "Set NEBIUS_API_KEY first." in capsys.readouterr().out and seen == []

    monkeypatch.setattr(day1_check, "KEY", "tf-day1-key")
    no_logprobs = httpx.Response(200, json={"choices": [{"message": {"content": "billing, high"}}]})
    client, seen = serve(want[:-1], {want[1]: no_logprobs, want[2]: httpx.Response(500, text="upstream error")})
    assert day1_check.main(client) == 1
    out = capsys.readouterr().out
    assert f"[ok] model {want[0]}" in out and f"[MISSING] model {want[-1]}" in out
    assert "'nvidia/Some-Other-Nemotron'" in out and "llama" not in out  # lists the NVIDIA models it does have
    assert f"[ok] {want[0]}: json ok, logprobs=yes, finish_reason=stop" in out  # INV-16 stores only these
    assert f"[ok] {want[1]}: json NOT parseable, logprobs=NO" in out  # reported; confidence falls back to agreement
    assert f"[FAIL] {want[2]}: HTTP 500 upstream error" in out and "Some checks failed" in out
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/v1/models")] + [("POST", "/v1/chat/completions")] * len(want)
    assert all(r.headers["authorization"] == "Bearer tf-day1-key" for r in seen)
    *bodies, gen = [json.loads(r.content) for r in seen[1:]]
    assert [b["model"] for b in bodies] == want[:-1]  # never asks for a missing model
    assert all(b["logprobs"] is True and b["response_format"] == {"type": "json_object"} for b in bodies)
    assert all(b["messages"] == day1_check.PROMPT and b["max_tokens"] == 400 for b in bodies)
    # Then the teacher with what every escalation sends: the output cap and a client's stop, seed and temperature.
    assert gen == {"model": want[0], "messages": day1_check.PROMPT, "max_tokens": 8192, **day1_check.GEN_PARAMS}
    assert f"[ok] {want[0]} accepts max_tokens=8192, stop, seed, temperature" in out

    client, _ = serve(want, {})
    assert day1_check.main(client) == 0 and capsys.readouterr().out.rstrip().endswith("All good.")

    # A teacher that refuses the cap (above the model's output limit) fails the check: every escalation would get a 4xx.
    over = httpx.Response(400, json={"error": {"message": "max_tokens must be at most 4096"}})
    client, _ = serve(want, {}, gen=over)
    assert day1_check.main(client) == 1
    out = capsys.readouterr().out
    assert f"[FAIL] {want[0]} with max_tokens=8192, stop, seed: HTTP 400" in out and "RATCHET_TEACHER_MAX_TOKENS" in out


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-11.1", "FR-11.2")
def test_deploy_scripts_parse_and_dry_run(tmp_path):
    assert BASH is not None
    scripts = sorted(ROOT.glob("deploy/**/*.sh"))
    names = {p.relative_to(ROOT).as_posix() for p in scripts}
    assert names >= {"deploy/train_job.sh", "deploy/student_endpoint.sh", "deploy/vm/create_vm.sh", "deploy/vm/backup.sh"}
    assert "deploy/vm/update.sh" in names
    for script in scripts:
        r = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, (script, r.stderr)

    key = tmp_path / "id_ed25519.pub"
    key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyForTests test@example\n", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "DRY_RUN": "1",
        "NEBIUS_PROJECT_ID": "project-e00test",
        "SSH_PUBLIC_KEY_PATH": str(key),
        "RATCHET_REPO_URL": f"https://x-access-token:{SECRET}@github.com/harshbhx/ratchet.git",  # a private-repo token
        "RATCHET_DOMAIN": "ratchet.example.org",
        "NEBIUS_IAM_TOKEN": SECRET,
    }

    def create_vm(**extra: str) -> subprocess.CompletedProcess[str]:
        cmd = [BASH, str(ROOT / "deploy" / "vm" / "create_vm.sh")]
        return subprocess.run(cmd, env={**env, **extra}, capture_output=True, text=True, timeout=60)

    r = create_vm()
    assert r.returncode == 0, r.stderr
    shown = [ln for ln in r.stderr.splitlines() if ln.startswith("+ ")]
    assert [ln.split()[1:4] for ln in shown] == [
        ["nebius", "vpc", "subnet"],
        ["nebius", "compute", "disk"],
        ["nebius", "compute", "instance"],
    ]
    assert all("--parent-id project-e00test" in ln for ln in shown)
    assert "--cloud-init-user-data <cloud-init.yaml>" in shown[2] and "--boot-disk-existing-disk-id dry-run-id" in shown[2]
    assert "Public IP:  203.0.113.7" in r.stdout and "Domain:     ratchet.example.org" in r.stdout
    assert SECRET not in r.stdout + r.stderr  # the user data (which holds the repo URL) is never printed

    r = create_vm(NEBIUS_SUBNET_ID="vpcsubnet-e00given")
    assert r.returncode == 0 and "vpc subnet list" not in r.stderr and '"subnet_id": "vpcsubnet-e00given"' in r.stderr
    for extra, message in (
        ({"NEBIUS_PROJECT_ID": ""}, "Set NEBIUS_PROJECT_ID"),
        ({"SSH_PUBLIC_KEY_PATH": str(tmp_path / "missing.pub")}, "SSH public key not found"),
        ({"RATCHET_SSH_USER": "root"}, "reserves the user names root and admin"),
        ({"RATCHET_VM_PRESET": "large"}, "RATCHET_VM_PRESET looks wrong"),
    ):
        r = create_vm(**extra)
        assert r.returncode == 1 and message in r.stderr and "+ nebius" not in r.stderr, extra

    # FR-11.2: the daily backup is a no-op in mock mode (the ledger is in memory), before it touches Docker.
    backup = ROOT / "deploy" / "vm" / "backup.sh"
    r = subprocess.run([BASH, str(backup)], env={**env, "RATCHET_MODE": "mock"}, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "mock mode: ledger is in memory, nothing to back up" in r.stdout


# ---------- deploy scripts against a fake `nebius` (W0: the documented CLI commands) ----------
STUDENT_ID = "aiendpoint-e00fixturestudent00"  # `ratchet-student` in tests/fixtures/nebius_endpoint_list.json (STOPPED)
OTHER_ID = "aiendpoint-e00fixtureother0000"  # `another-endpoint` there (RUNNING)
LIST = ["ai", "endpoint", "list", "--format", "json"]
TRAIN_IMAGE = "cr.eu-north1.nebius.cloud/e00test/ratchet-train:0.1"


def _deploy_env(tmp_path: Path, nebius: FakeNebius, **extra: str) -> dict[str, str]:
    """Only these variables, with the fake `nebius` first on PATH: nothing from the shell can move a result or reach
    Nebius. The scripts run in `tmp_path`, where a stub `ratchet.cost` logs each uptime record (`_recorded`) instead of
    writing a ledger; tests/test_cost.py records into a real one."""
    stub = tmp_path / "ratchet"
    stub.mkdir(exist_ok=True)
    (stub / "__init__.py").write_text("", encoding="utf-8")
    log = tmp_path / "cost-calls.jsonl"
    code = f"import json, sys\nwith open({str(log)!r}, 'a', encoding='utf-8') as f:\n"
    code += "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    (stub / "cost.py").write_text(code, encoding="utf-8")
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "RATCHET_PYTHON": sys.executable,
        "RATCHET_BUCKET_ID": "bucket-e00test",
        "RATCHET_STUDENT_KEY": STUDENT_KEY,
        "RATCHET_TRAIN_IMAGE": TRAIN_IMAGE,
    }
    return nebius.env({**base, **extra})


def _recorded(tmp_path: Path) -> list[str]:
    """The uptime records (`up`, `down`) the scripts made since the last call, each as `ratchet.cost endpoint X
    --source deploy`."""
    log = tmp_path / "cost-calls.jsonl"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    log.unlink(missing_ok=True)
    assert all(c[0] == "endpoint" and c[2:] == ["--source", "deploy"] for c in calls), calls
    return [c[1] for c in calls]


def _deploy(tmp_path: Path, script: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    cmd = [BASH, str(ROOT / "deploy" / script), *args]
    return subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)


def _opt(argv: list[str], flag: str) -> str | None:
    """The value after `flag` in a CLI call (None when absent); a flag given twice is a bug in the script."""
    assert argv.count(flag) <= 1, (flag, argv)
    return argv[argv.index(flag) + 1] if flag in argv else None


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-4.6")
def test_train_job_uses_job_create(tmp_path):
    """The training job is submitted with `nebius ai job create` and the flags the CLI reference documents (auto
    generated 19-Oct-2026), never the interactive `nebius ai create --type job`; the optional subnet and registry
    secret appear only when set, and `DRY_RUN=1` has nebius validate the request without submitting or waiting."""
    nebius = FakeNebius(tmp_path / "nebius")
    env = _deploy_env(tmp_path, nebius)

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        nebius.clear()
        return _deploy(tmp_path, "train_job.sh", *args, env={**env, **extra})

    r = run("student-v3")
    assert r.returncode == 0, r.stderr
    (argv,) = nebius.calls
    assert argv[:3] == ["ai", "job", "create"]
    expected = {
        "--name": "ratchet-student-v3",
        "--image": TRAIN_IMAGE,
        "--platform": "gpu-l40s-a",
        "--preset": "1gpu-8vcpu-32gb",
        "--volume": "bucket-e00test:/data",
        "--disk-size": "200Gi",
        "--timeout": "2h",
        "--container-command": "bash",
    }
    assert {flag: _opt(argv, flag) for flag in expected} == expected
    # The container splits --args as a shell would (Nebius's own fine-tuning tutorial relies on it): bash gets -c and
    # one command line, with the paths ratchet.live writes the dataset to and loads the adapter from.
    base = Settings().student_base_model
    assert shlex.split(_opt(argv, "--args") or "") == [
        "-c",
        f"python -m ratchet.nightschool.train --data /data/night/train.jsonl --base {base} --out /data/adapters/student-v3",
    ]
    assert not {"--subnet-id", "--registry-secret", "--dry-run"} & set(argv)
    assert "Submitted ratchet-student-v3" in r.stdout and "+ nebius ai job create" in r.stderr

    r = run("student-v3", RATCHET_SUBNET_ID="vpcsubnet-e00test", RATCHET_REGISTRY_SECRET="mbsec-e00registry")
    assert r.returncode == 0, r.stderr
    (argv,) = nebius.calls
    assert (_opt(argv, "--subnet-id"), _opt(argv, "--registry-secret")) == ("vpcsubnet-e00test", "mbsec-e00registry")

    # A dry run asks nebius to validate the request and returns: no job, and no wait for an adapter that never comes.
    r = run("student-v3", DRY_RUN="1", RATCHET_BUCKET_S3="s3://bucket-e00test")
    assert r.returncode == 0, r.stderr
    (argv,) = nebius.calls
    assert argv[:3] == ["ai", "job", "create"] and argv[-1] == "--dry-run"
    assert "nothing was submitted" in r.stdout and "Waiting for" not in r.stdout

    # Refused before any call: a DRY_RUN that is not 0 or 1 (DRY_RUN=true must not submit a paid job), a version or
    # base model that would break out of the job's `bash -c '...'` line, a missing image.
    for args, extra, message in (
        (("student-v3",), {"DRY_RUN": "true"}, "DRY_RUN must be 0 or 1"),
        (("v3'; rm -rf /data; '",), {}, "bad version name"),
        (("student-v3",), {"RATCHET_STUDENT_BASE_MODEL": "nvidia/x --out /tmp"}, "bad RATCHET_STUDENT_BASE_MODEL"),
        (("student-v3",), {"RATCHET_TRAIN_IMAGE": ""}, "set RATCHET_TRAIN_IMAGE"),
    ):
        r = run(*args, **extra)
        assert r.returncode != 0 and message in r.stderr and nebius.calls == [], (args, extra, r.stderr)

    # A refused job fails the script, so `RATCHET_TRAIN_CMD` (and the night) stops instead of waiting for nothing.
    nebius.fail(["ai", "job", "create"])
    r = run("student-v3", RATCHET_BUCKET_S3="s3://bucket-e00test")
    assert r.returncode != 0 and "Submitted" not in r.stdout and "Waiting for" not in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-8.2", "FR-15.2")
def test_endpoint_stop_and_start_use_the_id(tmp_path):
    """`nebius ai endpoint start|stop` take the endpoint's id (`--id`), not its name: the script finds the id by name
    in `nebius ai endpoint list --format json`, or takes RATCHET_ENDPOINT_ID and skips the lookup. `start` reuses the
    stopped endpoint (same URL and token) and creates one only when none has the name; `status` prints id and state."""
    nebius = FakeNebius(tmp_path / "nebius")
    env = _deploy_env(tmp_path, nebius)

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        nebius.clear()
        return _deploy(tmp_path, "student_endpoint.sh", *args, env={**env, **extra})

    running = nebius.fixture()
    running["items"][1]["status"]["state"] = "RUNNING"
    nebius.listing(running)
    r = run("stop")
    assert r.returncode == 0, r.stderr
    assert nebius.calls == [LIST, ["ai", "endpoint", "stop", "--id", STUDENT_ID]] and _recorded(tmp_path) == ["down"]

    nebius.listing(nebius.fixture())  # ratchet-student is STOPPED
    for args in (("start",), ()):  # start is the default action
        r = run(*args)
        assert r.returncode == 0, r.stderr
        assert nebius.calls == [LIST, ["ai", "endpoint", "start", "--id", STUDENT_ID]] and _recorded(tmp_path) == ["up"]

    for action in ("start", "stop"):  # a given id needs no lookup
        r = run(action, RATCHET_ENDPOINT_ID="aiendpoint-e00given")
        assert r.returncode == 0, r.stderr
        assert nebius.calls == [["ai", "endpoint", action, "--id", "aiendpoint-e00given"]]
        assert _recorded(tmp_path) == ["up" if action == "start" else "down"]

    # No endpoint with the name: start creates it (once, with the full create command); stop has nothing to stop.
    r = run("start", RATCHET_ENDPOINT_NAME="ratchet-student-2")
    assert r.returncode == 0, r.stderr
    assert [c[:3] for c in nebius.calls] == [LIST[:3], ["ai", "endpoint", "create"]]
    assert _opt(nebius.calls[1], "--name") == "ratchet-student-2" and "creating it" in r.stderr
    assert _recorded(tmp_path) == ["up"]  # once, by create
    r = run("stop", RATCHET_ENDPOINT_NAME="ratchet-student-2")
    assert r.returncode == 1 and "no endpoint named ratchet-student-2" in r.stderr and nebius.calls == [LIST]
    assert _recorded(tmp_path) == []

    # Already in the state asked for: no start or stop call, and the uptime record (idempotent) still runs.
    r = run("stop")
    assert r.returncode == 0 and nebius.calls == [LIST] and "is already STOPPED" in r.stderr
    assert _recorded(tmp_path) == ["down"]
    nebius.listing(running)
    r = run("start")
    assert r.returncode == 0 and nebius.calls == [LIST] and "is already RUNNING" in r.stderr
    assert _recorded(tmp_path) == ["up"]

    nebius.listing(nebius.fixture())
    r = run("status")
    assert r.returncode == 0 and r.stdout == f"{STUDENT_ID} STOPPED\n"
    r = run("status", RATCHET_ENDPOINT_ID=OTHER_ID)
    assert r.returncode == 0 and r.stdout == f"{OTHER_ID} RUNNING\n"
    for extra in ({"RATCHET_ENDPOINT_NAME": "nope"}, {"RATCHET_ENDPOINT_ID": "aiendpoint-e00gone"}):
        r = run("status", **extra)
        assert r.returncode == 1 and r.stdout == "" and nebius.calls == [LIST], extra

    # DRY_RUN=1: the read-only lookup runs; start and stop have no --dry-run, so they are printed, not run, and
    # nothing is recorded.
    r = run("start", DRY_RUN="1")
    assert r.returncode == 0, r.stderr
    assert nebius.calls == [LIST] and f"+ nebius ai endpoint start --id {STUDENT_ID}" in r.stderr
    assert "DRY_RUN=1: not run" in r.stderr and "DRY_RUN=1: endpoint up not recorded" in r.stderr
    nebius.listing(running)
    r = run("stop", DRY_RUN="1")
    assert r.returncode == 0 and nebius.calls == [LIST] and f"+ nebius ai endpoint stop --id {STUDENT_ID}" in r.stderr
    assert _recorded(tmp_path) == []  # status and dry runs record nothing

    # A stop or start that nebius refuses fails the script and records nothing: a `down` the endpoint never reached
    # would hide hours it still bills.
    nebius.fail(["ai", "endpoint", "stop"], ["ai", "endpoint", "start"])
    r = run("stop")
    assert r.returncode != 0 and nebius.calls == [LIST, ["ai", "endpoint", "stop", "--id", STUDENT_ID]]
    nebius.listing(nebius.fixture())
    r = run("start")
    assert r.returncode != 0 and nebius.calls == [LIST, ["ai", "endpoint", "start", "--id", STUDENT_ID]]
    assert _recorded(tmp_path) == []


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-8.2")
def test_endpoint_lookup_never_guesses(tmp_path):
    """When the list cannot say which endpoint is meant, the script stops before any start, stop or create: a second
    endpoint would bill a second GPU, and stopping the wrong one would leave the right one billing."""
    nebius = FakeNebius(tmp_path / "nebius")
    env = _deploy_env(tmp_path, nebius)

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        nebius.clear()
        return _deploy(tmp_path, "student_endpoint.sh", *args, env={**env, **extra})

    def student(state: Any = "STOPPED", **metadata: str) -> dict[str, Any]:
        return {"metadata": {"id": STUDENT_ID, "name": "ratchet-student", **metadata}, "status": {"state": state}}

    # create runs once: an endpoint with the name exists already.
    r = run("create")
    assert r.returncode == 1 and "already exists" in r.stderr and STUDENT_ID in r.stderr and nebius.calls == [LIST]

    every = ("start", "stop", "create")
    cases: list[tuple[Any, str, tuple[str, ...]]] = [
        ("not json", "did not print JSON", ("start",)),
        ({"items": {"id": STUDENT_ID}}, "no items list", ("start",)),
        ({"items": [student(), student(id="aiendpoint-e00twin")]}, "set RATCHET_ENDPOINT_ID", every),  # two with the name
        ({"items": [student(id="")]}, "no usable metadata.id", ("start",)),
        ({"items": [student(id="a b")]}, "no usable metadata.id", ("start",)),
        # The name is not on this page but more pages exist: not proof that there is no such endpoint.
        ({"items": [], "next_page_token": "page-2"}, "first page of the list", every),
    ]
    for listing, message, actions in cases:
        nebius.listing(listing)
        for action in actions:
            r = run(action)
            assert r.returncode == 1 and message in r.stderr and nebius.calls == [LIST], (listing, action, r.stderr)
    assert _recorded(tmp_path) == []
    nebius.listing(None)
    nebius.fail(LIST)
    r = run("start")
    assert r.returncode == 1 and "nebius ai endpoint list failed" in r.stderr and nebius.calls == [LIST]
    nebius.fail()

    # An empty list, however printed, means no endpoint: start creates one.
    for listing in (None, "", "{}", {"items": []}, {"items": None}, {"items": [], "next_page_token": ""}, []):
        nebius.listing(listing)
        r = run("start")
        assert r.returncode == 0, (listing, r.stderr)
        assert [c[:3] for c in nebius.calls] == [LIST[:3], ["ai", "endpoint", "create"]], listing

    # States: in transition (refused, run again later), lower case, unknown or missing (Nebius decides).
    for state, code, call in (
        ("STOPPING", 1, None),
        ("DELETING", 1, None),
        ("stopped", 0, ["ai", "endpoint", "start", "--id", STUDENT_ID]),
        ("ERROR", 0, ["ai", "endpoint", "start", "--id", STUDENT_ID]),
        (6, 0, ["ai", "endpoint", "start", "--id", STUDENT_ID]),  # an enum printed as a number
        (None, 0, ["ai", "endpoint", "start", "--id", STUDENT_ID]),
        ("PROVISIONING", 0, None),
        ("IMAGE_PULLING", 0, None),
    ):
        nebius.listing({"items": [student(state)], "next_page_token": "page-2"})  # found on the first page
        r = run("start")
        assert r.returncode == code and nebius.calls == [LIST] + ([call] if call else []), (state, r.stderr)

    # Refused before anything runs: an unknown action or DRY_RUN value, a lookup with no Python to read the list.
    for args, extra, code, message in (
        (("restart",), {}, 2, "usage:"),
        (("start",), {"DRY_RUN": "yes"}, 2, "DRY_RUN must be 0 or 1"),
        (("start",), {"RATCHET_PYTHON": str(tmp_path / "no-python")}, 1, "set RATCHET_PYTHON"),
    ):
        r = run(*args, **extra)
        assert r.returncode == code and message in r.stderr and not any(c[2] != "list" for c in nebius.calls), args


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.req("FR-8.2", "NFR-5")
def test_endpoint_args_carry_the_model_card_flags(tmp_path):
    """`create` serves the base model with the model card's flags (`--mamba_ssm_cache_dtype float32`), a context and
    batch cap sized for ticket prompts on one L40S, thinking off by default and the optional nano_v3 reasoning parser;
    the image is RATCHET_VLLM_IMAGE (default v0.18.0-cu130). The endpoint's token is passed but never printed."""
    nebius = FakeNebius(tmp_path / "nebius")
    nebius.listing(None)  # no endpoint yet
    env = _deploy_env(tmp_path, nebius)

    def create(**extra: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        nebius.clear()
        r = _deploy(tmp_path, "student_endpoint.sh", "create", env={**env, **extra})
        assert r.returncode == 0, r.stderr
        lookup, argv = nebius.calls
        assert lookup == LIST and argv[:3] == ["ai", "endpoint", "create"]
        return r, argv

    r, argv = create()
    expected = {
        "--name": "ratchet-student",
        "--image": "vllm/vllm-openai:v0.18.0-cu130",
        "--platform": "gpu-l40s-a",
        "--preset": "1gpu-8vcpu-32gb",
        "--container-port": "8000",
        "--shm-size": "16Gi",
        "--volume": "bucket-e00test:/data",
        "--auth": "token",
        "--token": STUDENT_KEY,
        "--env": "VLLM_ALLOW_RUNTIME_LORA_UPDATING=True",
    }
    assert {flag: _opt(argv, flag) for flag in expected} == expected
    assert not {"--subnet-id", "--dry-run"} & set(argv)
    base = Settings().student_base_model
    args = shlex.split(_opt(argv, "--args") or "")  # split as the container does (see test_train_job_uses_job_create)
    assert args[:-1] == [
        "--model",
        base,
        "--trust-remote-code",
        "--enable-lora",
        "--max-lora-rank",
        "16",
        "--max-loras",
        "4",
        "--mamba_ssm_cache_dtype",
        "float32",
        "--max-model-len",
        "8192",
        "--max-num-seqs",
        "64",
        "--default-chat-template-kwargs",
    ]
    assert json.loads(args[-1]) == {"enable_thinking": False}  # vLLM parses it with json.loads
    assert STUDENT_KEY not in r.stdout + r.stderr and "--token <RATCHET_STUDENT_KEY>" in r.stderr
    assert _recorded(tmp_path) == ["up"]

    r, argv = create(
        RATCHET_VLLM_IMAGE="vllm/vllm-openai:v0.18.0",
        RATCHET_SERVE_MAX_LEN="4096",
        RATCHET_SERVE_MAX_SEQS="16",
        RATCHET_REASONING_PARSER_PLUGIN="/data/nano_v3_reasoning_parser.py",
        RATCHET_SUBNET_ID="vpcsubnet-e00test",
        RATCHET_SERVE_TEMPLATE_KWARGS="",  # set but empty: no server default (requests carry their own)
        RATCHET_ENDPOINT_NAME="ratchet-student-b",
    )
    args = shlex.split(_opt(argv, "--args") or "")
    assert (_opt(argv, "--image"), _opt(argv, "--subnet-id"), _opt(argv, "--name")) == (
        "vllm/vllm-openai:v0.18.0",
        "vpcsubnet-e00test",
        "ratchet-student-b",
    )
    assert (_opt(args, "--max-model-len"), _opt(args, "--max-num-seqs")) == ("4096", "16")
    assert args[-4:] == ["--reasoning-parser-plugin", "/data/nano_v3_reasoning_parser.py", "--reasoning-parser", "nano_v3"]
    assert "--default-chat-template-kwargs" not in args and _recorded(tmp_path) == ["up"]

    # A dry run: nebius validates the create request (--dry-run) and nothing is recorded.
    r, argv = create(DRY_RUN="1")
    assert argv[-1] == "--dry-run" and "nothing was created" in r.stderr and _recorded(tmp_path) == []

    # Values that would break the one --args string, or that vLLM would refuse after the GPU is provisioned, are
    # refused first (the JSON check runs after the lookup, which proves a Python is there).
    for extra, message, calls in (
        ({"RATCHET_SERVE_MAX_LEN": "8k"}, "RATCHET_SERVE_MAX_LEN must be a positive whole number", []),
        ({"RATCHET_SERVE_MAX_SEQS": "0"}, "RATCHET_SERVE_MAX_SEQS must be a positive whole number", []),
        ({"RATCHET_STUDENT_BASE_MODEL": "nvidia/x --enforce-eager"}, "bad RATCHET_STUDENT_BASE_MODEL", []),
        ({"RATCHET_REASONING_PARSER_PLUGIN": "nano_v3_reasoning_parser.py"}, "must be an absolute path", []),
        ({"RATCHET_SERVE_TEMPLATE_KWARGS": "{'enable_thinking': False}"}, "must not contain a single quote", []),
        ({"RATCHET_SERVE_TEMPLATE_KWARGS": '{"enable_thinking":'}, "must be a JSON object", [LIST]),
        ({"RATCHET_SERVE_TEMPLATE_KWARGS": "[false]"}, "must be a JSON object", [LIST]),
        ({"RATCHET_STUDENT_KEY": ""}, "set RATCHET_STUDENT_KEY", []),
        ({"RATCHET_BUCKET_ID": ""}, "set RATCHET_BUCKET_ID", []),
    ):
        nebius.clear()
        r = _deploy(tmp_path, "student_endpoint.sh", "create", env={**env, **extra})
        assert r.returncode != 0 and message in r.stderr and nebius.calls == calls, (extra, r.stderr)


@pytest.mark.req("FR-15.2")
def test_deploy_scripts_never_say_cost_nothing():
    """A stopped endpoint is not billed for compute or its own storage, but a mounted bucket is, also while it is
    stopped (docs.nebius.com/serverless/overview, read Oct 21, 2026): no deploy script or deploy doc says otherwise."""
    for rel in ("deploy/student_endpoint.sh", "deploy/train_job.sh", "deploy/CLAUDE.md", "docs/DEPLOYMENT.md"):
        text = " ".join((ROOT / rel).read_text(encoding="utf-8").lower().split())
        assert "cost nothing" not in text and "costs nothing" not in text, rel
    script = " ".join((ROOT / "deploy" / "student_endpoint.sh").read_text(encoding="utf-8").split())
    assert "not billed for compute or its own storage; the mounted bucket still is" in script


@pytest.mark.req("NFR-10")
def test_deploy_variables_are_listed_and_documented():
    """Every RATCHET_* variable the two Nebius scripts read is either read by the package (config.py) or listed in
    `config.DEPLOY_ENV`, which the traceability check holds to a row in docs/CONFIGURATION.md; no name is stale."""
    from ratchet.config import DEPLOY_ENV, KNOWN_ENV

    read: set[str] = set()
    for script in ("student_endpoint.sh", "train_job.sh"):
        read |= set(re.findall(r"\$\{(RATCHET_[A-Z0-9_]+)", (ROOT / "deploy" / script).read_text(encoding="utf-8")))
    head, rest = (ROOT / "ratchet" / "config.py").read_text(encoding="utf-8").split("DEPLOY_ENV: tuple[str, ...] = (", 1)
    config = head + rest.split("\n)\n", 1)[1]  # config.py without the DEPLOY_ENV list itself
    by_package = {name for name in read if f'"{name}"' in config}
    assert read - by_package == set(DEPLOY_ENV), "add the deploy script's new variable to config.DEPLOY_ENV"
    assert len(DEPLOY_ENV) == len(set(DEPLOY_ENV)) and set(DEPLOY_ENV) <= set(KNOWN_ENV)
    documented = set(re.findall(r"`([A-Z][A-Z0-9_]*)`", (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")))
    assert read | {"DRY_RUN"} <= documented


# ---------- the live-run harness (ratchet/liverun.py, ADR 0121) ----------
LR_KEY = "lr-key-0123456789abcdef"
OTHER_KEY = "o-key-0123456789abcdef"
LR_BASE = "http://testserver/v1"
LR_RUN = "20270115T080000Z"  # the run id FakeClock's start gives
CRID = re.compile(rf"liverun-{LR_RUN}-r(\d+)-(\d+)")


def _ticket(body: dict[str, Any]) -> Any:
    from sim import tickets

    return tickets.lookup(body["messages"])


def _ticket_answer(body: dict[str, Any]) -> str:
    """The right ticket JSON for the request's user message: what a good teacher (and a good student) answers."""
    t = _ticket(body)
    return t.answer(t.labels)


def _teacher_and_hosted_nano(body: dict[str, Any]) -> str:
    """Token Factory: the teacher answers right; the untuned hosted Nano is unsure (an invalid answer) on hard templates."""
    if body["model"] == FALLBACK and _ticket(body).difficulty == "hard":
        return "not sure"
    return _ticket_answer(body)


class _Ticks:
    """A timer that moves 5 ms on every reading, so client-side latencies are known and nonzero."""

    def __init__(self, step: float = 0.005) -> None:
        self.t = 0.0
        self.step = step
        self.lock = __import__("threading").Lock()

    def __call__(self) -> float:
        with self.lock:
            self.t += self.step
            return self.t


class _Tagged(httpx.BaseTransport):
    """Marks every request that reaches `server` through the harness's own model clients (the control arm, the arms and
    `live.main`'s), so a test can tell them from the gateway's calls."""

    def __init__(self, server: FakeOpenAIServer) -> None:
        self.server = server

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["x-origin"] = "harness"
        return self.server(request)


class _GatewayClient:
    """The harness's gateway client: an `httpx.Client` on the app in process that records every path and header, can
    send one request with another key just before a given request id (`inject_before`), answer canned responses for
    given request ids (`canned`: {request-id suffix: [responses, first attempt first]}) and raise once it has sent
    `fail_after` chat requests (a crash). It keeps one event-loop portal open while in use (`TestClient` would start one
    per request, several times slower); `close()` ends it and the next request opens another."""

    def __init__(self, app: Any, inject_before: str | None = None, canned: dict[str, list[httpx.Response]] | None = None):
        self.transport = AppTransport(app)
        self.inner = httpx.Client(transport=self.transport, base_url="http://testserver")
        self.portal = False
        self.inject_before = inject_before
        self.canned = {k: list(v) for k, v in (canned or {}).items()}
        self.fail_after: int | None = None
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.chats = 0
        self.lock = __import__("threading").Lock()

    def _open(self) -> None:
        with self.lock:
            if not self.portal:
                self.transport.client.__enter__()
                self.portal = True

    def close(self) -> None:
        with self.lock:
            if self.portal:
                self.transport.client.__exit__(None, None, None)
                self.portal = False

    def get(self, url: str, **kw: Any) -> httpx.Response:
        self._open()
        self.calls.append(("GET", httpx.URL(url).path, {}))
        return self.inner.get(url, **kw)

    def post(self, url: str, json: Any = None, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        self._open()
        headers = dict(headers or {})
        crid = headers.get("X-Client-Request-Id", "")
        with self.lock:
            self.chats += 1
            if self.fail_after is not None and self.chats > self.fail_after:
                raise RuntimeError("the harness's host went away")
            self.calls.append(("POST", httpx.URL(url).path, headers))
            canned = next((v for k, v in self.canned.items() if crid.endswith(k) and v), None)
            response = canned.pop(0) if canned else None
            inject = self.inject_before is not None and crid.endswith(self.inject_before)
            if inject:
                self.inject_before = None
        if inject:
            other = {"Authorization": f"Bearer {OTHER_KEY}", "X-Ratchet-Task": TRIAGE}
            self.inner.post(url, json=json, headers=other)
        if response is not None:
            return response
        return self.inner.post(url, json=json, headers=headers, **kw)


def _canned(status: int, code: str, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after else {}
    return httpx.Response(status, json={"error": {"message": code, "type": "x", "param": None, "code": code}}, headers=headers)


def _ledger_dir(owner: object, root: Path) -> Path:
    """Where a world's ledger file lives: in memory-backed /dev/shm when the host has it (a live ledger commits every
    request, and on a busy disk the fsyncs, not the code, would set these tests' time), removed with its world; else
    beside the run directory. Nothing else differs."""
    import tempfile
    import weakref

    shm = Path("/dev/shm")
    if not (shm.is_dir() and os.access(shm, os.W_OK)):
        return root
    d = Path(tempfile.mkdtemp(prefix="ratchet-liverun-", dir=shm))
    weakref.finalize(owner, shutil.rmtree, d, True)
    return d


class LiveRunWorld:
    """A live gateway in process (`gateway.Runtime` in live mode on Token Factory and vLLM fakes, its ledger in a file),
    the gateway's environment for the harness and `live.main`, and the harness's seams. Use it as a context manager:
    the environment and the gateway's `rt` are patched only inside."""

    def __init__(
        self,
        root: Path,
        *,
        teacher: Any = _teacher_and_hosted_nano,
        student: Any = _ticket_answer,
        env: dict[str, str] | None = None,
        gateway: dict[str, Any] | None = None,
        tf_options: dict[str, Any] | None = None,
        inject_before: str | None = None,
        canned: dict[str, list[httpx.Response]] | None = None,
        run: RecordingRun | None = None,
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.db = _ledger_dir(self, root) / "live.db"
        self.env = {
            "RATCHET_MODE": "live",
            "RATCHET_DB": str(self.db),
            "RATCHET_DATA_DIR": str(root / "data"),
            "RATCHET_TEACHER_BASE": TEACHER_BASE,
            "NEBIUS_API_KEY": TEACHER_KEY,
            "RATCHET_JUDGE_MODEL": JUDGE,
            "RATCHET_FALLBACK_STUDENT": FALLBACK,
            "RATCHET_STUDENT_BASE": STUDENT_BASE,
            "RATCHET_STUDENT_KEY": STUDENT_KEY,
            "RATCHET_SAMPLES": "3",
            "RATCHET_AUDIT_RATE": "0.8",
            "RATCHET_SEQUENTIAL": "1",  # the run's planned gate profile (liverun.RUN_PROFILE)
            "RATCHET_CANARY_AUDIT_RATE": "0.5",
            "RATCHET_MIN_AUDITS": "1",
            "RATCHET_MIN_SHADOW": "1",
            "RATCHET_TRAIN_CMD": "true {version}",
            "RATCHET_API_KEYS": f"liverun:{LR_KEY},other:{OTHER_KEY}",
            "RATCHET_LIVERUN_KEY": LR_KEY,
            **(env or {}),
        }
        self.gateway_overrides = dict(gateway or {})
        options = {"reasoning_tokens": 420, "out_tokens": 480, "models": (OPEN_TEACHER, JUDGE, FALLBACK), **(tf_options or {})}
        self.tf = FakeOpenAIServer(teacher, **options)
        self.vllm = FakeOpenAIServer(student, loaded=set(), models=("nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",))
        self.run_rec = run or RecordingRun()
        self.sleeps: list[float] = []
        self.clock = FakeClock()
        self.timer = _Ticks()
        self.inject_before, self.canned = inject_before, canned
        self.tavily: httpx.Client | None = None
        self.rt: Any = None
        self.client: _GatewayClient | None = None

    def factory(self, base_url: str, api_key: str, **options: Any) -> Any:
        transport = self.vllm.transport if base_url == STUDENT_BASE else _Tagged(self.tf)
        return OpenAICompatProvider(base_url, api_key, transport=transport, **options)

    def __enter__(self) -> LiveRunWorld:
        import importlib

        self.mp = pytest.MonkeyPatch()
        for name in list(os.environ):
            if name.startswith("RATCHET_") or name in ("NEBIUS_API_KEY", "NEBIUS_API_KEY_LIVERUN", "TAVILY_API_KEY"):
                self.mp.delenv(name)
        self.mp.setenv("RATCHET_MODE", "mock")  # importing the gateway the first time builds its (mock) Runtime
        self.mp.setenv("RATCHET_MOCK_WARMUP_DAYS", "0")
        self.gw = importlib.import_module("ratchet.gateway")
        for name, value in self.env.items():
            self.mp.setenv(name, value)
        if self.rt is None:
            transports = {"teacher": self.tf.transport, "student": self.vllm.transport}
            self.rt = self.gw.Runtime(Settings(**self.gateway_overrides), transports=transports)
            self.client = _GatewayClient(self.gw.app, self.inject_before, self.canned)
        self.mp.setattr(self.gw, "rt", self.rt)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.client is not None:
            self.client.close()
        self.mp.undo()

    def endpoint_up(self) -> None:
        from ratchet import cost

        cost.record_endpoint(Ledger(str(self.db), auto_day=True), Settings().cost_policy(), "up", "cli")

    def main(self, *argv: str) -> tuple[int, str]:
        from ratchet import liverun

        out = __import__("io").StringIO()
        code = liverun.main(
            list(argv),
            client=self.client,
            provider_factory=self.factory,
            run=self.run_rec,
            http=httpx.Client(transport=self.vllm.transport),
            clock=self.clock,
            timer=self.timer,
            sleep=self.sleeps.append,
            tavily=self.tavily,
            out=out,
        )
        return code, out.getvalue()

    def run_dir(self, name: str = "run") -> Path:
        return self.root / name


def _results(path: Path) -> dict[str, Any]:
    return json.loads((path / "results.json").read_text(encoding="utf-8"))


MAIN_RUN = ("--rounds", "3", "--requests", "100", "--control-share", "0.5", "--batch", "20", "--max-usd", "20")


def _main_world(root: Path, control_share: str = "0.5") -> LiveRunWorld:
    world = LiveRunWorld(root, inject_before="-r2-20")
    with world:
        world.endpoint_up()
        args = [*MAIN_RUN]
        args[args.index("--control-share") + 1] = control_share
        world.code, world.out = world.main("run", "--base-url", LR_BASE, *args, "--concurrency", "1", "--out", str(root / "run"))
    return world


@pytest.fixture(scope="module")
def liverun_world(tmp_path_factory: pytest.TempPathFactory) -> LiveRunWorld:
    """One offline run (about 4 s), shared: 3 rounds of 100 synthetic tickets with nights between, half of them also sent
    to the teacher by the control arm, one request with another key between two batches of round 2, and the untuned arm
    at the end (the hosted Nano is unsure on hard templates)."""
    return _main_world(tmp_path_factory.mktemp("liverun"))


@pytest.mark.req("FR-8.20")
def test_liverun_plan_refuses_over_budget(tmp_path, monkeypatch):
    from ratchet import liverun
    from ratchet.stats import binom_sf

    world = LiveRunWorld(tmp_path)
    with world:
        code, out = world.main("plan", "--rounds", "4", "--requests", "1500", "--max-usd", "0.01")
    assert code == 3 and "OVER --max-usd $0.01" in out
    assert world.tf.calls == [] and world.vllm.calls == [] and world.client.calls == []  # free: no call at all
    assert "release stages: requests for 1 audits" in out and "configured" in out and "canary 0.5" in out
    assert "canary 1.0" in out and "canary 10%" in out and "canary 50%" in out and "shadow" in out
    assert "audit rates: RATCHET_AUDIT_RATE 0.8; RATCHET_CANARY_AUDIT_RATE 0.5" in out
    assert "gate profile: RATCHET_SEQUENTIAL=1 (in force: 1), RATCHET_CANARY_AUDIT_RATE=0.5 (in force: 0.5): in force" in out
    assert "golden: a certifiable first version needs about" in out
    assert re.search(r"^\s+4\s+[\d,]+\s+\$", out, re.M), out  # a row per round with its USD
    # Unset canary rate: the plan says the 10% stage needs about 10 times the round size at the default rates.
    world = LiveRunWorld(tmp_path / "defaults", env={"RATCHET_CANARY_AUDIT_RATE": "", "RATCHET_AUDIT_RATE": "0.05"})
    with world:
        for name in ("RATCHET_CANARY_AUDIT_RATE", "RATCHET_MIN_AUDITS", "RATCHET_SEQUENTIAL"):
            world.mp.delenv(name)
        code, out = world.main("plan", "--max-usd", "30")
    assert code == 0 and "RATCHET_CANARY_AUDIT_RATE unset (= RATCHET_AUDIT_RATE)" in out
    assert "RATCHET_SEQUENTIAL=1 (in force: 0), RATCHET_CANARY_AUDIT_RATE=0.5 (in force: unset): NOT in force" in out
    assert "7,398" in out and "737" in out and "times the round size" in out  # the canary 10% stage, 0.05 against 0.5
    # plan_stage_requests is the smallest n with P(Binomial(n, p) >= m) >= 0.9, as a brute-force scan finds it.
    for m, p in ((20, 0.0035), (3, 0.05), (1, 0.7)):
        brute = next(n for n in range(m, 100_000) if binom_sf(m, n, p) >= 0.9)
        assert liverun.plan_stage_requests(m, p) == brute
    assert liverun.plan_stage_requests(0, 0.3) == 0 and liverun.plan_stage_requests(5, 0.0) is None


@pytest.mark.req("FR-8.20", "INV-8")
def test_liverun_refuses_mock_gateway_and_wrong_ledger(tmp_path, gw_env):
    # A mock gateway is refused at /healthz, and no demo route is ever asked for.
    mock_gw = gw_env()
    mock_rt = mock_gw.rt  # the world below installs its live runtime on the same module
    world = LiveRunWorld(tmp_path / "mock")
    with world:
        Ledger(str(world.db))  # a ledger file exists; the gateway is the mock one
        client = _GatewayClient(mock_gw.app)
        world.client = client
        world.mp.setattr(world.gw, "rt", mock_rt)
        code, out = world.main("smoke", "--base-url", LR_BASE, "--out", str(tmp_path / "mock" / "run"))
    assert code == 2 and "liverun measures a live gateway; for mock numbers run python -m ratchet.tour" in out
    assert [p for _, p, _ in client.calls] == ["/healthz"]
    assert not any("/ratchet/demo" in p for _, p, _ in client.calls)

    # RATCHET_DB pointing at another file than the gateway's: refused after the one probe request.
    world = LiveRunWorld(tmp_path / "wrongdb", gateway={"db_path": str(tmp_path / "wrongdb" / "gateway.db")})
    with world:
        Ledger(str(world.db))
        code, out = world.main("smoke", "--base-url", LR_BASE, "--out", str(tmp_path / "wrongdb" / "run"))
    assert code == 2 and "not the gateway's ledger" in out
    assert len(world.client.calls) == 2  # /healthz and the probe

    # A gateway without RATCHET_API_KEYS: the row carries no key name.
    world = LiveRunWorld(tmp_path / "open", gateway={"api_keys": ""})
    with world:
        code, out = world.main("smoke", "--base-url", LR_BASE, "--out", str(tmp_path / "open" / "run"))
    assert code == 2 and "add liverun:<key> to RATCHET_API_KEYS" in out

    # No RATCHET_LIVERUN_KEY: refused before any call.
    world = LiveRunWorld(tmp_path / "nokey", env={"RATCHET_LIVERUN_KEY": ""})
    with world:
        world.endpoint_up()
        code, out = world.main("smoke", "--base-url", LR_BASE)
    assert code == 2 and "RATCHET_LIVERUN_KEY is not set" in out
    assert world.client.calls == [] and world.tf.calls == []

    # A training command that cannot run here: refused before the first paid request (the stock image has no nebius CLI).
    for cmd, message in (("no-such-binary-xyz {version}", "is not on PATH"), ("bash deploy/missing.sh {version}", "is not here")):
        world = LiveRunWorld(tmp_path / f"train{len(message)}", env={"RATCHET_TRAIN_CMD": cmd})
        with world:
            world.endpoint_up()
            code, out = world.main("run", "--base-url", LR_BASE, "--max-usd", "5", "--rounds", "2", "--requests", "10")
        assert code == 2 and message in out and "docs/DEPLOYMENT.md step 7" in out
        assert world.tf.calls_to("/chat/completions") == [] and not any(
            p.endswith("/completions") for _, p, _ in world.client.calls
        )

    # No open student endpoint interval: the GPU-hours view would miss the endpoint.
    world = LiveRunWorld(tmp_path / "noendpoint")
    with world:
        Ledger(str(world.db))
        code, out = world.main("run", "--base-url", LR_BASE, "--max-usd", "5", "--rounds", "2", "--requests", "10")
    assert code == 2 and "python -m ratchet.cost endpoint up" in out and world.client.calls == []


@pytest.mark.req("FR-8.20", "FR-8.21", "INV-8")
def test_liverun_end_to_end_offline(liverun_world):
    from ratchet import liverun

    w = liverun_world
    assert w.code == 0, w.out
    res = _results(w.run_dir())
    assert tuple(res) == liverun.RESULT_KEYS and res["generator"] == "ratchet.liverun/1" and res["kind"] == "run"
    assert res["mode"] == "live" and res["run_id"] == LR_RUN and res["stopped"] is None and res["client"] == "liverun"
    # At least two versions came from the nights in process, through live.main's seams (the training command ran).
    assert len(res["kpis"]["run_versions"]) >= 2 and res["values"]["live.run.versions_trained"] >= 2
    assert w.run_rec.commands.count("true student-v1") == 1 and "true student-v2" in w.run_rec.commands
    assert [r["night"]["outcome"] for r in res["rounds"][:2]] == ["trained", "trained"]
    assert res["rounds"][0]["night"]["admitted"] == "canary" and res["rounds"][1]["night"]["admitted"] == "shadow"
    # The harness talked to the gateway only through /healthz and /v1/chat/completions (never /ratchet/*).
    assert {p for _, p, _ in w.client.calls} == {"/healthz", "/v1/chat/completions"}
    # Every cascade request carries its request id; round 0 is the probe.
    chats = [h for m, p, h in w.client.calls if m == "POST"]
    ids = [CRID.fullmatch(h["X-Client-Request-Id"]) for h in chats]
    assert all(ids) and ids[0].groups() == ("0", "0")
    sent = [(int(m.group(1)), int(m.group(2))) for m in ids[1:]]
    assert len(sent) == len(set(sent)) and {k for k, _ in sent} == {1, 2, 3}
    assert all(h["X-Ratchet-Task"] == TRIAGE and h["Authorization"] == f"Bearer {LR_KEY}" for h in chats)
    # The ledger rows carry it once lane D's column is there (read as None before).
    rows = Ledger(str(w.db)).client_window("liverun", 0, 10**9)["rows"]
    assert "client_request_id" in rows[0] and all(r["client_request_id"] in (None, f"liverun-{LR_RUN}-r0-0") for r in rows[:1])
    assert all(v.startswith("live.") for v in res["values"]) and set(res["values"]) <= set(res["sources"])
    kinds = [e["kind"] for e in Ledger(str(w.db)).events()]
    assert kinds.count("liverun_start") == 1 and kinds.count("liverun_round") == 3 and kinds[-1] == "liverun_stop"
    detail = next(e["detail"] for e in Ledger(str(w.db)).events() if e["kind"] == "liverun_start")
    assert detail == f"liverun {LR_RUN} started: synthetic tickets traffic, 3 rounds x 100 requests, cap $20.00 (key liverun)"
    # Models as Token Factory and vLLM list them, recorded and never enforced.
    assert res["models"]["teacher"]["teacher_model_listed"] and res["models"]["teacher"]["listed_nvidia"]
    assert res["models"]["student"]["listed"] == ["nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"]
    assert res["code"]["code_digest"].startswith("sha256:") and res["code"]["version"]
    # Settings carry no key and no base URL; the plan is the first one printed.
    blob = json.dumps(res["settings"])
    assert TEACHER_BASE not in blob and STUDENT_BASE not in blob and TEACHER_KEY not in blob and STUDENT_KEY not in blob
    assert res["plan"]["inputs"]["requests"] == 100 and res["settings"]["release_policy"]["min_audits"] == 1
    # The last round is mostly the student's, audited; its numbers are in values with their sources.
    final = res["rounds"][-1]
    assert final["client"]["student_share"] > 0.8 and final["ledger"]["audits"]["n"] > 20
    assert res["values"]["live.final.audited_precision_pct"] == 100.0 and res["values"]["live.final.audits"] > 20
    assert res["values"]["live.final.student_p50_client_ms"] == pytest.approx(5.0)
    assert "live.final.student_p50_wall_ms" not in res["values"]  # wall_ms comes with lane D
    assert "OPS-PERF-HOTPATH" in res["sources"]["live.final.student_p50_wall_ms"]
    assert res["cost"]["endpoint_hours"] > 0 and res["values"]["live.run.endpoint_hours"] == res["cost"]["endpoint_hours"]
    assert res["cost"]["total_usd"] == pytest.approx(
        res["cost"]["teacher_usd"]
        + res["cost"]["endpoint_usd"]
        + res["cost"]["training_usd"]
        + res["cost"]["control_usd"]
        + res["cost"]["untuned_usd"]
    )
    assert res["cost"]["nebius_bill"] == {"usd": None, "note": None, "entered": None}
    md = (w.run_dir() / "LIVE_RESULTS.md").read_text(encoding="utf-8")
    assert "<!-- claim:live.final.cost_reduction_measured_pct -->" in md and "## Placeholder map" in md
    assert "list prices, unverified" in md and "synthetic workload" in md


@pytest.mark.req("FR-8.20")
def test_liverun_rounds_are_keyed_by_client_and_id_range(liverun_world):
    res = _results(liverun_world.run_dir())
    r1, r2, r3 = res["rounds"]
    assert (r1["other_clients"], r2["other_clients"], r3["other_clients"]) == (0, 1, 0)
    # The other key's request sits inside round 2's id range but is not counted in it.
    assert r1["window"]["upto_id"] < r2["window"]["after_id"] + 1 and r2["window"]["upto_id"] == r3["window"]["after_id"]
    assert r2["ledger"]["rows"] == r2["client"]["ok"] == r2["sent"] and r2["unrecorded"] == 0
    led = Ledger(str(liverun_world.db))
    window = led.client_window("other", r2["window"]["after_id"], r2["window"]["upto_id"])
    assert len(window["rows"]) == 1 and window["other_clients"] == r2["sent"]


@pytest.mark.req("FR-8.20", "INV-12", "INV-13")
def test_liverun_writes_no_text_and_no_key(liverun_world):
    import gzip

    w = liverun_world
    texts: set[str] = set()
    for server in (w.tf, w.vllm):
        for call in server.calls_to("/chat/completions"):
            for m in call.json["messages"]:
                texts.add(m["content"])
            texts.add(server.completion(call.json)["choices"][0]["message"]["content"])
    pieces = {t[i : i + 20] for t in texts for i in range(max(1, len(t) - 19)) if len(t) >= 20}
    assert len(pieces) > 1000
    files = sorted(p for p in w.run_dir().iterdir() if p.is_file())
    assert {p.name for p in files} >= {
        "results.json",
        "requests.ndjson.gz",
        "state.json",
        "plan.json",
        "log.txt",
        "LIVE_RESULTS.md",
    }
    blobs = {p.name: (gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()).decode() for p in files}
    blobs["stdout"] = w.out
    for name, blob in blobs.items():
        for secret in (LR_KEY, OTHER_KEY, TEACHER_KEY, STUDENT_KEY, TEACHER_BASE, STUDENT_BASE):
            assert secret not in blob, (name, secret)
        leaked = next((p for p in pieces if p in blob), None)
        assert leaked is None, (name, leaked)
    for e in Ledger(str(w.db)).events():  # the harness's events carry key names, counts and reasons only
        if e["kind"].startswith("liverun_"):
            assert LR_KEY not in e["detail"] and not any(p in e["detail"] for p in pieces)


@pytest.mark.req("FR-8.21", "INV-2", "INV-10")
def test_liverun_control_arm_is_never_stored(liverun_world, tmp_path):
    w = liverun_world
    led = Ledger(str(w.db))
    records = __import__("ratchet.liverun", fromlist=["read_records"]).read_records(w.run_dir() / "requests.ndjson.gz")
    cascade = [r for r in records if r["status"] == 200]
    rows = led.client_window("liverun", 0, 10**9)["rows"]
    assert len(rows) == len(cascade) == 1 + sum(r["sent"] for r in _results(w.run_dir())["rounds"])
    controls = [r for r in records if (r.get("control") or {}).get("ok")]
    assert len(controls) > 100
    # The same rounds with no control arm, on a fresh copy of the world, store exactly as many examples.
    again = _main_world(tmp_path / "nocontrol", control_share="0")
    assert again.code == 0
    other = Ledger(str(again.db))
    assert len(other.examples()) == len(led.examples()) and len(other.examples("golden")) == len(led.examples("golden"))
    harness_teacher = [c for c in again.tf.calls_to("/chat/completions") if c.headers.get("x-origin") == "harness"]
    assert not any(c.json["model"] == OPEN_TEACHER for c in harness_teacher)  # no control call: the untuned arm's only


@pytest.mark.req("FR-8.21")
def test_liverun_control_call_matches_the_serving_teacher(liverun_world):
    w = liverun_world
    chats = w.tf.calls_to("/chat/completions")
    control = [c for c in chats if c.headers.get("x-origin") == "harness" and c.json["model"] == OPEN_TEACHER]
    gateway = [c for c in chats if c.headers.get("x-origin") is None and c.json["model"] == OPEN_TEACHER]
    by_messages = {json.dumps(c.json["messages"]): c.json for c in gateway}
    matched = [
        (c.json, by_messages[json.dumps(c.json["messages"])]) for c in control if json.dumps(c.json["messages"]) in by_messages
    ]
    assert len(matched) > 50  # round 1 is a cold start: every control request was also escalated
    for mine, theirs in matched:
        assert mine == theirs  # model, max_tokens cap, temperature, no logprobs, the same messages
        assert mine["max_tokens"] == 8192 and mine["temperature"] == 0.0 and "logprobs" not in mine and mine["n"] == 1
    assert all(c.headers["authorization"] == f"Bearer {TEACHER_KEY}" for c in control)


@pytest.mark.req("FR-8.21")
def test_liverun_control_ratio_and_reasoning_tokens(liverun_world):
    from ratchet import liverun
    from ratchet.stats import ratio_estimate

    w = liverun_world
    res = _results(w.run_dir())
    ctl = res["arms"]["control"]
    assert ctl["reasoning_tokens"]["mean"] == 420 and ctl["reasoning_tokens"]["formula"] == 300
    assert res["values"]["live.control.reasoning_tokens_mean"] == 420
    assert "the formula assumes 300" in (w.run_dir() / "LIVE_RESULTS.md").read_text(encoding="utf-8")
    records = liverun.read_records(w.run_dir() / "requests.ndjson.gz")
    rounds = [r for r in liverun._sorted_records(records) if isinstance(r["round"], int) and r["round"] >= 1]
    pairs = [
        (r["all_teacher_cost_usd"], r["control"]["cost"])
        for r in rounds
        if r["status"] == 200 and (r.get("control") or {}).get("ok") and r["served_by"] == "student"
    ]
    assert len(pairs) >= 10
    expected = ratio_estimate(pairs, min_n=10)
    assert ctl["ratio_student"]["ratio"] == expected["ratio"] == res["values"]["live.control.ratio_student"]
    assert expected["ratio"] > 1  # the formula's 300 reasoning tokens undercount the teacher's real output
    # Teacher-served rows' recorded baseline is the serving call itself: the check ratio is 1.
    assert ctl["ratio_teacher"]["ratio"] == pytest.approx(1.0)
    final = res["rounds"][-1]["client"]["control"]
    assert final["status"] == "ok" and final["measured_reduction"] > final["recorded_reduction"]
    lo, hi = final["baseline_measured_ci95"]
    assert lo < final["baseline_measured_usd"] < hi
    assert res["values"]["live.final.cost_reduction_measured_pct"] == pytest.approx(100 * final["measured_reduction"])
    # Nine student-served pairs are not enough: insufficient, and the measured value stays out of values.
    rec = {"status": 200, "served_by": "student", "cost_usd": 0.001, "all_teacher_cost_usd": 0.002, "round": 1}
    few = [{**rec, "i": i, "control": {"called": True, "ok": True, "cost": 0.003, "agree": True}} for i in range(9)]
    est = liverun.control_estimate(few, 300)
    assert est["status"] == "insufficient" and est["measured_reduction"] is None and est["ratio_student"]["ratio"] is None
    values, sources = liverun.client_values(few, 300)
    assert (
        "live.final.cost_reduction_measured_pct" not in values
        and "fewer than 10" in sources["live.final.cost_reduction_measured_pct"]
    )
    ten = few + [{**few[0], "i": 9}]
    assert liverun.control_estimate(ten, 300)["measured_reduction"] == pytest.approx(1 - 0.01 / 0.03)


@pytest.mark.req("FR-8.21")
def test_liverun_accuracy_against_template_labels(tmp_path):
    from sim import tickets

    def teacher(body: dict[str, Any]) -> str:
        t = _ticket(body)
        if t.template == "a3":
            return "I am not sure"  # invalid under the ticket-triage spec
        if t.difficulty == "hard":
            wrong = next(c for c in tickets.CATEGORIES if c != t.category)
            return t.answer({**t.labels, "category": wrong})
        return t.answer(t.labels)

    world = LiveRunWorld(tmp_path, teacher=teacher)
    with world:
        code, out = world.main(
            "smoke", "--base-url", LR_BASE, "--requests", "60", "--control-share", "0.5", "--out", str(tmp_path / "run")
        )
    assert code == 0, out
    res = _results(tmp_path / "run")
    acc = res["rounds"][0]["client"]["accuracy"]
    assert acc["label"] == "synthetic tickets, template labels"
    easy, hard = acc["cascade"]["by_difficulty"]["easy"], acc["cascade"]["by_difficulty"]["hard"]
    assert easy["pct"] == 100.0 and hard["pct"] < 100.0 and hard["n"] > 5
    assert acc["cascade"]["invalid"] > 0 and acc["cascade"]["invalid"] == hard["invalid"]
    # Category and priority apart: the wrong answers keep the priority right.
    fields = acc["cascade"]["fields"]
    assert fields["priority"]["agree"] == fields["priority"]["n"] - acc["cascade"]["invalid"]
    assert fields["category"]["agree"] == acc["cascade"]["accurate"]
    assert res["values"]["live.accuracy.cascade_easy_pct"] == 100.0 and res["values"]["live.accuracy.cascade_hard_pct"] < 100.0
    assert res["values"]["live.accuracy.ultra_hard_pct"] < res["values"]["live.accuracy.ultra_easy_pct"] == 100.0
    assert "template labels" in res["sources"]["live.accuracy.cascade_hard_pct"]
    # Cold start, no nights: every answer is the teacher's, so no student pairs and no measured baseline.
    assert res["rounds"][0]["client"]["control"]["status"] == "insufficient" and res["rounds"][0]["night"] is None
    assert "live.final.cost_reduction_measured_pct" not in res["values"]


@pytest.mark.req("FR-8.20")
def test_liverun_usd_cap_stops_between_batches(tmp_path):
    world = LiveRunWorld(tmp_path, tf_options={"out_tokens": 200_000})  # $0.60 a teacher call at list prices
    with world:
        code, out = world.main(
            "smoke", "--base-url", LR_BASE, "--requests", "40", "--batch", "10", "--max-usd", "10", "--out", str(tmp_path / "run")
        )
    assert code == 1, out
    res = _results(tmp_path / "run")
    state = json.loads((tmp_path / "run" / "state.json").read_text(encoding="utf-8"))
    assert res["stopped"]["reason"] == "max_usd" and state["stopped"]["reason"] == "max_usd"
    assert "stopped: max_usd" in out
    batch_usd = 10 * 0.6 * 1.25 + 0.6
    assert res["cost"]["total_usd"] <= 10 + batch_usd
    assert res["rounds"][0]["sent"] == 10  # one batch, then the next one's projection passed the cap
    # The control arm's own cap (20% of --max-usd) skipped the rest of its sample.
    assert res["arms"]["control"]["meter"]["spent_usd"] <= 2.0 + 0.6 and res["arms"]["control"]["skipped_cap"] >= 1
    assert res["finished"] is not None and state["rounds"][0]["done"] is False
    assert (tmp_path / "run" / "LIVE_RESULTS.md").read_text(encoding="utf-8").count("Stopped early: `max_usd`") == 1


@pytest.mark.req("FR-8.20", "INV-7")
def test_liverun_server_cap_and_rate_limit_responses(tmp_path):
    canned = {
        "-r1-3": [_canned(429, "rate_limit_exceeded", retry_after="2")],
        "-r1-7": [_canned(429, "budget_exceeded", retry_after="3600")],
    }
    world = LiveRunWorld(tmp_path / "cap", canned=canned)
    with world:
        code, out = world.main(
            "smoke", "--base-url", LR_BASE, "--batch", "5", "--concurrency", "1", "--out", str(tmp_path / "cap" / "run")
        )
    assert code == 1
    res = _results(tmp_path / "cap" / "run")
    assert world.sleeps == [2.0]  # Retry-After honoured once, then the retry went through
    crids = [h["X-Client-Request-Id"] for m, _, h in world.client.calls if m == "POST"]
    assert crids.count(f"liverun-{LR_RUN}-r1-3") == 2 and crids.count(f"liverun-{LR_RUN}-r1-7") == 1
    assert res["stopped"] == {"reason": "server_cap", "detail": "the gateway's own cap: budget_exceeded"}
    assert res["rounds"][0]["client"]["retried"] == 1 and res["rounds"][0]["client"]["errors"] == {"429 budget_exceeded": 1}

    world = LiveRunWorld(tmp_path / "auth", canned={"-r1-2": [_canned(401, "invalid_api_key")]})
    with world:
        code, out = world.main("smoke", "--base-url", LR_BASE, "--batch", "5", "--out", str(tmp_path / "auth" / "run"))
    assert code == 1 and _results(tmp_path / "auth" / "run")["stopped"]["reason"] == "auth"

    # The teacher down three times in a row stops the run; scattered errors past 10% of a batch do too.
    down = {f"-r1-{i}": [_canned(503, "teacher_circuit_open")] for i in (5, 6, 7)}
    world = LiveRunWorld(tmp_path / "down", canned=down)
    with world:
        code, out = world.main(
            "smoke", "--base-url", LR_BASE, "--batch", "10", "--concurrency", "1", "--out", str(tmp_path / "down" / "run")
        )
    assert code == 1 and _results(tmp_path / "down" / "run")["stopped"]["reason"] == "teacher_down"
    errors = {f"-r1-{i}": [_canned(502, "teacher_unavailable")] for i in (1, 3)}
    world = LiveRunWorld(tmp_path / "errors", canned=errors)
    with world:
        code, out = world.main("smoke", "--base-url", LR_BASE, "--batch", "10", "--out", str(tmp_path / "errors" / "run"))
    stopped = _results(tmp_path / "errors" / "run")["stopped"]
    assert code == 1 and stopped["reason"] == "gateway_errors" and "502 teacher_unavailable x2" in stopped["detail"]


@pytest.mark.req("FR-8.20")
def test_liverun_night_failure_stops_with_partial_results(tmp_path):
    def fail_training(argv: list[str]) -> None:
        if argv[0] == "true":
            raise subprocess.CalledProcessError(1, argv, output="secret training log")

    args = ("--rounds", "2", "--requests", "80", "--batch", "40", "--control-share", "0", "--max-usd", "20")
    world = LiveRunWorld(tmp_path / "stop", run=RecordingRun(on_call=fail_training))
    with world:
        world.endpoint_up()
        code, out = world.main("run", "--base-url", LR_BASE, *args, "--out", str(tmp_path / "stop" / "run"))
    assert code == 1, out
    res = _results(tmp_path / "stop" / "run")
    night = res["rounds"][0]["night"]
    assert res["stopped"] == {"reason": "night_failed", "detail": "CalledProcessError"}
    assert night["outcome"] == "night_failed" and night["failure"] == "CalledProcessError" and night["versions"] == []
    assert len(res["rounds"]) == 1 and res["rounds"][0]["client"]["ok"] == 80
    assert "secret training log" not in (tmp_path / "stop" / "run" / "results.json").read_text(encoding="utf-8")
    # The training interval was closed: the GPU time is paid whether or not training worked.
    assert night["training_hours"] >= 0 and Ledger(str(world.db)).compute_intervals("training_job")[0]["end_ts"] is not None
    # Fixed, then resumed: the failed night runs again before round 2, and its failure stays on record.
    world.run_rec.on_call = None
    with world:
        code, out = world.main("run", "--resume", str(tmp_path / "stop" / "run"), "--base-url", LR_BASE)
    assert code == 0, out
    res = _results(tmp_path / "stop" / "run")
    assert res["rounds"][0]["night"]["outcome"] == "trained" and len(res["rounds"]) == 2
    assert [f["failure"] for f in res["rounds"][0]["failed_nights"]] == ["CalledProcessError"]
    assert res["rounds"][1]["client"]["student_share"] > 0

    world = LiveRunWorld(tmp_path / "go", run=RecordingRun(on_call=fail_training))
    with world:
        world.endpoint_up()
        code, out = world.main(
            "run", "--base-url", LR_BASE, *args, "--on-night-failure", "continue", "--out", str(tmp_path / "go" / "run")
        )
    assert code == 0, out
    res = _results(tmp_path / "go" / "run")
    assert res["rounds"][0]["night"]["outcome"] == "night_failed" and res["rounds"][1]["client"]["student_share"] == 0.0


@pytest.mark.req("FR-8.20", "INV-5")
def test_liverun_until_settled_extends_the_round(tmp_path):
    # Every canary answer audited, so a canary needs about 80 requests for its 10% stage and 16 for its 50% stage: 70
    # requests a round leave it in flight (the gate settings as set, not the planned profile). One worker, so the
    # gateway's audit and canary draws (seeded) come in the same order every time.
    env = {"RATCHET_MIN_AUDITS": "8", "RATCHET_CANARY_AUDIT_RATE": "1"}
    args = ("--requests", "70", "--batch", "35", "--control-share", "0", "--max-usd", "20", "--gate-profile", "as-set")
    args += ("--concurrency", "1")
    world = LiveRunWorld(tmp_path / "settle", env=env)
    with world:
        world.endpoint_up()
        code, out = world.main(
            "run", "--base-url", LR_BASE, "--rounds", "2", *args, "--max-extend", "150", "--out", str(tmp_path / "settle" / "run")
        )
    assert code == 0, out
    r2 = _results(tmp_path / "settle" / "run")["rounds"][1]
    assert r2["extension"] > 0 and r2["settled"] is True and r2["sent"] == 70 + r2["extension"]
    assert "extension" in out
    assert Ledger(str(world.db)).versions(["canary", "shadow"]) == []

    world = LiveRunWorld(tmp_path / "unsettled", env=env)
    with world:
        world.endpoint_up()
        code, out = world.main(
            "run",
            "--base-url",
            LR_BASE,
            "--rounds",
            "3",
            *args,
            "--max-extend",
            "0",
            "--out",
            str(tmp_path / "unsettled" / "run"),
        )
    assert code == 0, out
    rounds = _results(tmp_path / "unsettled" / "run")["rounds"]
    assert rounds[1]["extension"] == 0 and rounds[1]["settled"] is False
    assert rounds[1]["night"]["outcome"] == "skip"  # INV-5: the night skips on its own; the harness never forces it
    assert any("still in canary" in e["detail"] for e in rounds[1]["night"]["events"])


@pytest.mark.req("FR-8.20")
def test_liverun_resume_continues_the_stream(tmp_path):
    from ratchet import liverun

    args = ("--rounds", "2", "--requests", "60", "--batch", "10", "--control-share", "0.3", "--nights", "off", "--max-usd", "20")
    full = LiveRunWorld(tmp_path / "full")
    with full:
        code, _ = full.main("run", "--base-url", LR_BASE, *args, "--out", str(tmp_path / "full" / "run"))
    assert code == 0
    want = {r["crid"]: r["template"] for r in liverun.read_records(tmp_path / "full" / "run" / "requests.ndjson.gz")}

    world = LiveRunWorld(tmp_path / "cut")
    with world:
        world.client.fail_after = 1 + 2 * 10  # the probe and two batches of round 1, then the host goes away
        code, out = world.main("run", "--base-url", LR_BASE, *args, "--concurrency", "3", "--out", str(tmp_path / "cut" / "run"))
        assert code == 1 and "stopped: error" in out
        first = liverun.read_records(tmp_path / "cut" / "run" / "requests.ndjson.gz")
        state = json.loads((tmp_path / "cut" / "run" / "state.json").read_text(encoding="utf-8"))
        assert state["rounds"][0]["sent"] == 30 and len(first) == 21  # the third batch never reached the gateway
        world.client.fail_after = None
        saved = json.loads((tmp_path / "cut" / "run" / "state.json").read_text(encoding="utf-8"))["args"]
        assert "base_url" not in saved and saved["seed"] == 7  # the gateway's URL is never written; it comes again here
        code, out = world.main("run", "--resume", str(tmp_path / "cut" / "run"), "--base-url", LR_BASE)
    assert code == 0, out
    assert "resuming run" in out
    records = liverun.read_records(tmp_path / "cut" / "run" / "requests.ndjson.gz")
    sent = [h["X-Client-Request-Id"] for m, _, h in world.client.calls if m == "POST"]
    assert len(sent) == len(set(sent))  # no index twice
    later = [r for r in records if r["round"] == 2 or (r["round"] == 1 and r["i"] >= 30)]
    assert len(later) == 30 + 60 and all(want[r["crid"]] == r["template"] for r in later)
    assert len(records) == len(want) - 10  # the ten requests of the lost batch are the only ones missing
    res = _results(tmp_path / "cut" / "run")
    assert res["stopped"] is None and [r["sent"] for r in res["rounds"]] == [60, 60]
    assert liverun.main(["run", "--resume", str(tmp_path / "nowhere")], out=__import__("io").StringIO()) == 2


@pytest.mark.req("FR-8.20")
def test_window_percentiles_filter_by_route_client_and_ids():
    from ratchet import liverun

    led = Ledger()
    for client, route, ms in [("a", "student", 10), ("a", "student", 30), ("b", "student", 99), ("a", "confidence", 500)] + [
        ("a", "student", 20),
        ("a", "student", 40),
        (None, "cold_start", 7),
        ("a", "cold_start", 800),
    ]:
        led.log_request(
            task="t", served_by="student" if route == "student" else "teacher", route=route, latency_ms=ms, client=client
        )
    assert led.last_ids() == {"requests": 8, "events": 0}
    w = led.client_window("a", 0, 8)
    assert [r["id"] for r in w["rows"]] == [1, 2, 4, 5, 6, 8] and (w["other_clients"], w["no_client"]) == (2, 1)
    stats = liverun.window_stats(w["rows"])
    assert stats["latency_ms"]["by_route"]["student"] == {"n": 4, "p50": 20.0, "p95": 40.0}
    assert stats["latency_ms"]["by_route"]["confidence"] == {"n": 1, "p50": 500.0, "p95": 500.0}
    assert stats["wall_ms"] is None  # no wall_ms column before lane D
    part = led.client_window("a", 1, 5)  # (1, 5]: ids 2, 4 and 5
    assert [r["id"] for r in part["rows"]] == [2, 4, 5] and part["other_clients"] == 1
    assert liverun.window_stats(part["rows"])["latency_ms"]["by_route"]["student"] == {"n": 2, "p50": 20.0, "p95": 30.0}
    # Nearest rank, as lane D's _nearest_rank: the ceil(q n)-th smallest, q n rounded to 9 decimals first.
    xs = list(range(1, 101))
    assert liverun.nearest_rank(xs, 0.07) == 7 and liverun.nearest_rank(xs, 0.5) == 50 and liverun.nearest_rank(xs, 0.95) == 95
    assert liverun.nearest_rank([5.0], 0.0) == 5.0 and liverun.nearest_rank([], 0.5) is None
    with pytest.raises(ValueError):
        led.client_window("a", -1, 3)
    led.event("x", "one")
    assert [e["detail"] for e in led.events_since(0)] == ["one"] and led.events_since(1) == []


@pytest.mark.req("FR-8.21")
def test_liverun_report_bill_publish_and_verify(liverun_world, tmp_path, monkeypatch):
    from ratchet import liverun

    run = tmp_path / "copy"
    shutil.copytree(liverun_world.run_dir(), run)
    monkeypatch.chdir(tmp_path)

    def report(*argv: str) -> tuple[int, str]:
        out = __import__("io").StringIO()
        return liverun.main(["report", str(run), *argv], clock=FakeClock(), out=out), out.getvalue()

    assert report("--bill-usd", "-1")[0] == 2 and report("--bill-note", "x")[0] == 2
    assert report("--bill-usd", "1", "--bill-note", "x" * 201)[0] == 2
    code, _ = report("--bill-usd", "12.34", "--bill-note", "console, failed jobs included; ops@example.com")
    assert code == 0
    res = _results(run)
    assert (
        res["values"]["live.bill.nebius_usd"] == 12.34
        and res["cost"]["nebius_bill"]["note"] == "console, failed jobs included; [EMAIL]"
    )
    md = (run / "LIVE_RESULTS.md").read_text(encoding="utf-8")
    bill = next(line for line in md.splitlines() if line.startswith("| Nebius bill"))
    assert "**$12.34** <!-- claim:live.bill.nebius_usd -->" in bill
    assert md.index("| Nebius bill") < md.index("Break-even (whole gateway)")  # next to the break-even line
    # verify recomputes every client-side number from the records; one edited number fails it.
    out = __import__("io").StringIO()
    assert liverun.main(["verify", str(run)], out=out) == 0, out.getvalue()
    assert "not recomputable here" in out.getvalue() and "live.bill.nebius_usd" in out.getvalue()
    # Publishing: a stopped run needs --allow-partial; a smoke run never.
    stopped = json.loads((run / "results.json").read_text(encoding="utf-8"))
    stopped["stopped"] = {"reason": "max_usd", "detail": ""}
    (run / "results.json").write_text(json.dumps(stopped), encoding="utf-8")
    assert report("--publish")[0] == 2 and not (tmp_path / "evidence").exists()
    assert report("--publish", "--allow-partial")[0] == 0
    assert (tmp_path / "evidence" / "live" / "results.json").is_file() and (tmp_path / "docs" / "LIVE_RESULTS.md").is_file()
    assert liverun.main(["verify", str(tmp_path / "evidence" / "live")], out=__import__("io").StringIO()) == 0
    stopped["kind"] = "smoke"
    (run / "results.json").write_text(json.dumps(stopped), encoding="utf-8")
    code, out = report("--publish", "--allow-partial")
    assert code == 2 and "smoke runs never back a doc number" in out
    edited = json.loads((tmp_path / "evidence" / "live" / "results.json").read_text(encoding="utf-8"))
    edited["values"]["live.final.student_share_pct"] += 1
    (tmp_path / "evidence" / "live" / "results.json").write_text(json.dumps(edited), encoding="utf-8")
    out = __import__("io").StringIO()
    assert liverun.main(["verify", str(tmp_path / "evidence" / "live")], out=out) == 1
    assert "mismatch: live.final.student_share_pct" in out.getvalue()
    edited["values"]["live.final.student_share_pct"] -= 1
    edited["sources"].pop("live.final.audits")
    (tmp_path / "evidence" / "live" / "results.json").write_text(json.dumps(edited), encoding="utf-8")
    assert liverun.main(["verify", str(tmp_path / "evidence" / "live")], out=__import__("io").StringIO()) == 1
    assert liverun.main(["verify", str(tmp_path / "nowhere")], out=__import__("io").StringIO()) == 2


@pytest.mark.req("FR-8.20")
def test_live_targets_stay_out_of_test_and_ci():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipes = re.findall(r"^(test|lint):\n((?:\t.*\n)+)", make, re.M)
    assert {name for name, _ in recipes} == {"test", "lint"}
    for _, body in recipes:
        assert "live" not in body and "liverun" not in body
    assert "live-plan:" in make and "live-smoke:" in make and "live-run:" in make
    assert re.search(r"^\.PHONY:.*\blive-plan live-smoke live-run\b", make, re.M)
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        assert "live-smoke" not in text and "live-run" not in text and "ratchet.liverun" not in text, wf


@pytest.mark.req("FR-8.21")
def test_liverun_untuned_arm_paired(liverun_world):
    from ratchet.certificate import paired

    un = _results(liverun_world.run_dir())["arms"]["untuned"]
    assert un["untuned"]["coverage"] < un["tuned"]["coverage"] and un["coverage_difference"] > 0
    assert un["paired"] == paired(un["tuned"]["test_vector"], un["untuned"]["test_vector"])
    assert un["spend_usd"] > 0 and un["rows"] > 0 and un["untuned"]["coverage_at_router_threshold"] is not None
    calls = [c for c in liverun_world.tf.calls_to("/chat/completions") if c.json["model"] == FALLBACK]
    assert calls and all(c.headers.get("x-origin") == "harness" for c in calls)  # the arm never went through the gateway


def _scout_teacher(body: dict[str, Any]) -> str:
    """Token Factory for Scout's calls: the mock world's planner and writer answers, its triage and read labels for the
    pages it knows, and a valid answer for any other page (Tavily's excerpt is not the mock world's)."""
    from ratchet.scout.prompts import READ_SYSTEM, TRIAGE_SYSTEM
    from sim import world
    from sim.mock_models import _pinned_answer

    pinned = _pinned_answer(body["messages"])
    if pinned is not None:
        return pinned
    try:
        item = world.lookup(body["messages"])
        return item.answer(item.labels)
    except KeyError:
        system = body["messages"][0]["content"]
        if system == TRIAGE_SYSTEM:
            return json.dumps({"relevant": True, "source_type": "docs", "reason": "on topic"})
        assert system == READ_SYSTEM
        return json.dumps({"answers_question": True, "fact_type": "definition", "key_fact": "A fact from the page."})


def _tavily_client(credits: int = 1) -> tuple[httpx.Client, list[httpx.Request]]:
    """Tavily on a MockTransport: the mock research world's results for each query, `usage.credits` as Tavily reports."""
    from sim import research

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        query = json.loads(request.content)["query"]
        hits = research.search(query, 4)
        results = [{"title": h.title, "url": h.url, "content": h.excerpt} for h in hits]
        return httpx.Response(200, json={"results": results, "usage": {"credits": credits}})

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


@pytest.mark.req("FR-8.21", "INV-11")
def test_liverun_scout_phase(tmp_path):
    from ratchet.scout.prompts import EXAMPLE_QUESTIONS

    tavily, searches = _tavily_client()
    world = LiveRunWorld(tmp_path / "scout", teacher=_scout_teacher, env={"TAVILY_API_KEY": "tvly-secret-0123456789"})
    world.tavily = tavily
    with world:
        code, out = world.main(
            "smoke", "--base-url", LR_BASE, "--requests", "10", "--scout-runs", "1", "--out", str(tmp_path / "scout" / "run")
        )
    assert code == 0, out
    res = _results(tmp_path / "scout" / "run")
    sc = res["arms"]["scout"]
    assert sc["label"] == "against the recorded all-Ultra baseline" and len(sc["runs"]) == 1 and sc["runs"][0]["ok"]
    assert sc["llm_calls"] == sc["runs"][0]["totals"]["llm_calls"] > 2 and sc["failed_calls"] == 0
    assert sc["tavily_credits"] == len(searches) == sc["runs"][0]["tavily_credits"] and 1 <= len(searches) <= 3
    assert res["values"]["live.scout.llm_calls"] == sc["llm_calls"] and "live.scout.savings_pct" in res["sources"]
    assert sc["window"]["after_id"] < sc["window"]["upto_id"]
    # Every agent call went over /v1 with the harness's key and its own request id (Scout CLI's path, not /scout/*).
    scout_ids = [
        h["X-Client-Request-Id"] for m, _, h in world.client.calls if m == "POST" and "-scout" in h["X-Client-Request-Id"]
    ]
    assert len(scout_ids) == sc["llm_calls"] and all(re.fullmatch(rf"liverun-{LR_RUN}-scout0-\d+", i) for i in scout_ids)
    assert {p for _, p, _ in world.client.calls} == {"/healthz", "/v1/chat/completions"}
    assert json.loads(searches[0].content)["query"] and EXAMPLE_QUESTIONS[0]
    blob = (tmp_path / "scout" / "run" / "results.json").read_text(encoding="utf-8")
    assert "tvly-secret-0123456789" not in blob and EXAMPLE_QUESTIONS[0] not in blob  # no key, no question text
    # Without Tavily it is not the live Scout: refused before any call.
    world = LiveRunWorld(tmp_path / "nokey", teacher=_scout_teacher)
    with world:
        code, out = world.main("smoke", "--base-url", LR_BASE, "--scout-runs", "1")
    assert code == 2 and "--scout-runs needs TAVILY_API_KEY" in out and world.client.calls == []


@pytest.mark.req("FR-8.21")
def test_liverun_file_workload(tmp_path):
    from sim import tickets

    stream = tickets.TicketStream(seed=3, drift_day=None)
    items = [stream.sample(1) for _ in range(12)]
    rows = []
    for n, t in enumerate(items):
        labels = dict(t.labels)
        if n < 3:  # three rows labelled wrong on purpose: the teacher's right answer then disagrees with the file
            labels["category"] = next(c for c in tickets.CATEGORIES if c != t.category)
        rows.append({"task": TRIAGE, "messages": t.messages(), "labels": labels, "difficulty": t.difficulty})
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    world = LiveRunWorld(tmp_path / "file")
    with world:
        code, out = world.main(
            "smoke",
            "--base-url",
            LR_BASE,
            "--workload",
            f"file:{path}",
            "--requests",
            "12",
            "--out",
            str(tmp_path / "file" / "run"),
        )
    assert code == 0, out
    res = _results(tmp_path / "file" / "run")
    acc = res["rounds"][0]["client"]["accuracy"]
    assert acc["label"] == "file workload rows.jsonl, the file's labels" and res["workload"]["label"] == acc["label"]
    assert acc["cascade"]["n"] == 12 and acc["cascade"]["accurate"] == 9 and acc["cascade"]["invalid"] == 0
    assert acc["cascade"]["fields"]["priority"]["agree"] == 12  # only the category was mislabelled
    sent = {json.dumps(c.json["messages"]) for c in world.tf.calls_to("/chat/completions") if c.headers.get("x-origin") is None}
    assert sent >= {json.dumps(r["messages"]) for r in rows}  # one pass sends every row once
    # A bad row names its line and field, never its text.
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(rows[0]) + "\n" + json.dumps({**rows[1], "labels": {}}) + "\n", encoding="utf-8")
    world = LiveRunWorld(tmp_path / "bad")
    with world:
        code, out = world.main("smoke", "--base-url", LR_BASE, "--workload", f"file:{bad}")
    assert code == 2 and "workload file line 2: labels must be a non-empty object" in out
    assert items[1].text not in out and world.client.calls == []
    # Labels the task's spec refuses, or a task the gateway does not know, would score every answer as invalid: refused.
    for row, message in (
        ({**rows[1], "labels": {**rows[1]["labels"], "category": "weather"}}, "labels do not fit task 'ticket-triage''s spec"),
        ({**rows[1], "task": "banking77-intent"}, "task 'banking77-intent' is not registered"),
    ):
        bad.write_text(json.dumps(rows[0]) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
        world = LiveRunWorld(tmp_path / f"bad{len(message)}")
        with world:
            code, out = world.main("smoke", "--base-url", LR_BASE, "--workload", f"file:{bad}")
        assert code == 2 and f"workload file line 2: {message}" in out and world.client.calls == [], out


@pytest.mark.req("FR-8.20", "FR-8.21", "INV-13")
def test_liverun_gate_profile_and_the_arms_key(tmp_path):
    from ratchet import liverun

    # The planned profile (RATCHET_SEQUENTIAL=1, RATCHET_CANARY_AUDIT_RATE=0.5) not in force: run refuses before any call.
    world = LiveRunWorld(tmp_path / "off", env={"RATCHET_SEQUENTIAL": "0"})
    with world:
        world.endpoint_up()
        code, out = world.main("run", "--base-url", LR_BASE, "--max-usd", "5", "--rounds", "1", "--requests", "10")
    assert code == 2 and "gate profile is not in force (RATCHET_SEQUENTIAL is 0)" in out and "--gate-profile as-set" in out
    assert world.client.calls == [] and world.tf.calls == []
    # As set, on purpose: it runs, and the results say which settings were in force. The arms use their own key.
    arms_key = "tf-liverun-arms-key-0123"
    world = LiveRunWorld(tmp_path / "asset", env={"RATCHET_SEQUENTIAL": "0", "NEBIUS_API_KEY_LIVERUN": arms_key})
    with world:
        world.endpoint_up()
        code, out = world.main(
            "run", "--base-url", LR_BASE, "--max-usd", "5", "--rounds", "1", "--requests", "20", "--control-share", "0.5",
            "--gate-profile", "as-set", "--out", str(tmp_path / "asset" / "run"),
        )  # fmt: skip
    assert code == 0, out
    res = _results(tmp_path / "asset" / "run")
    prof = res["settings"]["gate_profile"]
    assert prof["mode"] == "as-set" and prof["matches"] is False and prof["differs"] == ["RATCHET_SEQUENTIAL"]
    assert prof["wanted"] == dict(liverun.RUN_PROFILE) and res["settings"]["arms_teacher_key"] == "NEBIUS_API_KEY_LIVERUN"
    chats = world.tf.calls_to("/chat/completions")
    control = [c for c in chats if c.headers.get("x-origin") == "harness"]
    assert control and all(c.headers["authorization"] == f"Bearer {arms_key}" for c in control)
    assert all(c.headers["authorization"] == f"Bearer {TEACHER_KEY}" for c in chats if c.headers.get("x-origin") is None)
    for p in (tmp_path / "asset" / "run").iterdir():
        if p.suffix != ".gz":
            assert arms_key not in p.read_text(encoding="utf-8"), p.name


@pytest.mark.req("FR-8.21")
def test_liverun_control_budget_is_reserved_across_workers():
    import threading

    from ratchet import liverun
    from ratchet.config import Price

    meter = liverun._Metered(RecordingProvider("ok"), Price(1.0, 3.0))
    barrier = threading.Barrier(8)
    won: list[bool] = []

    def worker() -> None:
        barrier.wait()
        won.append(meter.reserve(1.0, 3.0))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(won) == [False] * 5 + [True] * 3  # three calls in flight fill a $3 cap; the other five are skipped
    meter.release(1.0)
    assert meter.reserve(1.0, 3.0) and not meter.reserve(0.01, 3.0)


@pytest.mark.req("FR-8.21")
def test_liverun_repeat_run_and_report(tmp_path, monkeypatch):
    from ratchet import liverun

    first, again = tmp_path / "first", tmp_path / "again"
    world = LiveRunWorld(tmp_path / "w")
    with world:
        code, _ = world.main("smoke", "--base-url", LR_BASE, "--requests", "20", "--seed", "11", "--out", str(first))
        assert code == 0
        world.clock.advance(3600)  # a new run id
        code, out = world.main("smoke", "--repeat-of", str(first), "--base-url", LR_BASE, "--out", str(again))
        assert code == 0, out
        assert world.main("run", "--repeat-of", str(first), "--base-url", LR_BASE)[0] == 2  # a smoke repeats a smoke
        code, out = world.main("run", "--resume", str(first), "--repeat-of", str(first))
        assert code == 2 and "do not go together" in out
    r1, r2 = _results(first), _results(again)
    assert r2["run_id"] != r1["run_id"] and r2["workload"] == r1["workload"] and r2["workload"]["seed"] == 11
    rep = r2["repeat"]
    assert rep["role"] == "repeat" and rep["other"]["run_id"] == r1["run_id"] and rep["same_code"] is True
    assert set(rep["values"]) == set(r1["values"]) & set(r2["values"]) and rep["values"]
    for vid, row in rep["values"].items():
        assert row == {
            "first": r1["values"][vid],
            "repeat": r2["values"][vid],
            "difference": r2["values"][vid] - r1["values"][vid],
        }
    # The same texts in the same order, under the new run id.
    t1 = [r["template"] for r in liverun.read_records(first / "requests.ndjson.gz") if r["round"] == 1]
    t2 = [r["template"] for r in liverun.read_records(again / "requests.ndjson.gz") if r["round"] == 1]
    assert t1 == t2 and len(t1) == 20
    assert "Two runs of the same plan and seed" in r2["caveats"][0] and "## Repeat run" in (again / "LIVE_RESULTS.md").read_text()
    # report --repeat attaches the comparison to the first run; publishing copies the repeat beside it.
    monkeypatch.chdir(tmp_path)
    out = __import__("io").StringIO()
    assert (
        liverun.main(["report", str(again), "--repeat", str(first)], out=out) == 2 and "is not a repeat of run" in out.getvalue()
    )
    doc = _results(first)
    doc["kind"] = "run"  # so it may be published (a smoke run never backs a doc number)
    (first / "results.json").write_text(json.dumps(doc), encoding="utf-8")
    out = __import__("io").StringIO()
    assert liverun.main(["report", str(first), "--repeat", str(again), "--publish"], clock=FakeClock(), out=out) == 0
    r1 = _results(first)
    assert r1["repeat"]["role"] == "original" and r1["repeat"]["other"]["run_id"] == r2["run_id"]
    assert "Two runs of the same plan and seed" in r1["caveats"][0]
    assert (tmp_path / "evidence" / "live" / "repeat" / "results.json").is_file() and "published the repeat run" in out.getvalue()
    for d in ("evidence/live", "evidence/live/repeat"):
        out = __import__("io").StringIO()
        assert liverun.main(["verify", d], out=out) == 0, out.getvalue()
    edited = _results(tmp_path / "evidence" / "live")
    vid = next(iter(edited["repeat"]["values"]))
    edited["repeat"]["values"][vid]["first"] += 1
    (tmp_path / "evidence" / "live" / "results.json").write_text(json.dumps(edited), encoding="utf-8")
    out = __import__("io").StringIO()
    assert liverun.main(["verify", "evidence/live"], out=out) == 1 and "repeat table" in out.getvalue()
