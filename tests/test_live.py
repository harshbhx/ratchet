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

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import OPEN_TEACHER, AppTransport, FakeClock, FakeOpenAIServer, RecordingProvider, RecordingRun, _examples, _version
from fastapi.routing import APIRoute
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
    assert seen_at_sync == [rows] and train.load_rows(str(data / "night" / "train.jsonl")) == rows
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


@pytest.mark.req("FR-8.4", "FR-3.6", "FR-15.2")
def test_live_main_tick_reload_night_offline(tmp_path, monkeypatch, capsys):
    seed, public_key = bom.keygen()
    data, db = tmp_path / "data", tmp_path / "live.db"
    _live_env(
        monkeypatch,
        RATCHET_DB=str(db),
        RATCHET_DATA_DIR=str(data),
        RATCHET_STUDENT_BASE=STUDENT_BASE,
        RATCHET_STUDENT_KEY=STUDENT_KEY,
        RATCHET_TEACHER_BASE=TEACHER_BASE,
        NEBIUS_API_KEY=TEACHER_KEY,
        RATCHET_JUDGE_MODEL=JUDGE,
        RATCHET_SAMPLES="3",
        RATCHET_BOM_SIGNING_KEY=seed,
    )
    _examples(Ledger(str(db)), train=60, golden=6)  # the live ledger after some days of teacher answers
    vllm = FakeOpenAIServer(_answer_like_the_teacher)
    token_factory = FakeOpenAIServer('{"score": 1, "reason": "same answer"}')
    factory_calls: list[tuple[str, str, dict[str, Any]]] = []

    def factory(base_url: str, api_key: str, **options: Any) -> Any:
        factory_calls.append((base_url, api_key, options))
        server = vllm if base_url == STUDENT_BASE else token_factory
        return OpenAICompatProvider(base_url, api_key, transport=server.transport, **options)

    run = RecordingRun()

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
    assert run.commands == ["bash deploy/train_job.sh student-v1"]
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

    # After an endpoint restart, reload loads the canary again; tick leaves a canary without audits where it is.
    assert main("reload").splitlines()[0] == "reloaded: ['student-v1']"
    assert len(vllm.calls_to("/load_lora_adapter")) == 2
    main("tick")
    # No command means night: a release is in flight, so nothing trains and no new BOM appears.
    assert "release in flight" in main()
    assert run.commands == ["bash deploy/train_job.sh student-v1"] and sorted(p.name for p in boms.iterdir()) == [
        "student-v1.json"
    ]
    assert len(Ledger(str(db)).compute_intervals("training_job")) == 1  # no training, no GPU time
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
    path = dataset.write_jsonl(rows, tmp_path / "night" / "train.jsonl")
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")  # blank lines are skipped
    loaded = train.load_rows(str(path))
    assert loaded == rows and len(loaded) == 4
    stored = {r["hash"]: r["completion"] for r in led.examples(split="train")}
    for r in loaded:
        # SFT with assistant_only_loss learns the last message: the teacher's stored answer (INV-2).
        assert r["messages"][-1] == {"role": "assistant", "content": stored[r["provenance"]["hash"]]}
        assert {"teacher", "license"} <= set(r["provenance"])
    # ratchet_manifest.json, saved next to the adapter, names its base model and the licenses it learned from.
    manifest = json.loads(json.dumps(train.manifest("base-model", loaded), default=list))
    lic = licenses.REGISTRY[OPEN_TEACHER].name
    assert manifest == {"base_model": "base-model", "examples": 4, "provenance": [[OPEN_TEACHER, lic]]}


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
    assert set(res) == {"days", "nights", "versions", "events", "totals", "bill_of_materials", "cost"}
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
    assert lines[-3] == "simulation: mock models, workload mixed, seed 7; not a measure of real model quality"
    for e in res["events"]:
        assert f"  day {e['day']}: [{e['kind']}] {e['detail']}" in lines


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

        def admit(self, ip: str) -> str | None:
            self.ips.append(ip)
            return super().admit(ip)

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


@pytest.mark.req("FR-8.5", "INV-9", "FR-1.51")
def test_day1_check_reports_missing_models_and_logprobs(day1_check, monkeypatch, capsys):
    want = list(day1_check.WANT)
    assert len(want) >= 4
    embed = day1_check.WANT_EMBEDDINGS[0]
    monkeypatch.setattr(day1_check, "EMBEDDINGS", embed)
    monkeypatch.setattr(day1_check, "EMBEDDINGS_BASE", "")
    monkeypatch.setattr(day1_check, "EMBEDDINGS_KEY", "")
    vectors = {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}], "usage": {"prompt_tokens": 6}}
    embed_reply: list[httpx.Response] = [httpx.Response(200, json=vectors)]
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
            if request.url.path == "/v1/embeddings":
                return embed_reply[0]
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
    assert f"[ok] model {want[0]}" in out and f"[MISSING] model {want[-1]}" in out and f"[MISSING] model {embed}" in out
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

    client, seen = serve(want + [embed], {})
    assert day1_check.main(client) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("All good.") and f"[ok] embeddings {embed}: 2 dimensions" in out
    (sent,) = [json.loads(r.content) for r in seen if r.url.path == "/v1/embeddings"]
    assert sent == {"model": embed, "input": ["Ratchet day-1 check", "refund"]}

    # The embeddings model answering with an error, or with vectors of different lengths, fails the check; `off` skips it.
    for reply in (
        httpx.Response(500, text="down"),
        httpx.Response(200, json={"data": [{"embedding": [1.0]}, {"embedding": []}]}),
    ):
        embed_reply[0] = reply
        client, _ = serve(want + [embed], {})
        assert day1_check.main(client) == 1 and f"[FAIL] embeddings {embed}" in capsys.readouterr().out
    monkeypatch.setattr(day1_check, "EMBEDDINGS_BASE", "off")
    client, seen = serve(want, {})
    assert day1_check.main(client) == 0 and "[skip] embeddings" in capsys.readouterr().out
    assert not any(r.url.path == "/v1/embeddings" for r in seen)
    # Another provider's base: not looked up in Token Factory's list, and never sent the Token Factory key.
    embed_reply[0] = httpx.Response(200, json=vectors)
    monkeypatch.setattr(day1_check, "EMBEDDINGS_BASE", "https://tei.test/v1")
    client, seen = serve(want, {})
    assert day1_check.main(client) == 0
    (call,) = [r for r in seen if r.url.path == "/v1/embeddings"]
    assert call.url.host == "tei.test" and "authorization" not in call.headers
    monkeypatch.setattr(day1_check, "EMBEDDINGS_KEY", "tei-key")
    client, seen = serve(want, {})
    assert day1_check.main(client) == 0
    (call,) = [r for r in seen if r.url.path == "/v1/embeddings"]
    assert call.headers["authorization"] == "Bearer tei-key"
    capsys.readouterr()
    monkeypatch.setattr(day1_check, "EMBEDDINGS_KEY", "")
    monkeypatch.setattr(day1_check, "EMBEDDINGS_BASE", "")

    # A teacher that refuses the cap (above the model's output limit) fails the check: every escalation would get a 4xx.
    over = httpx.Response(400, json={"error": {"message": "max_tokens must be at most 4096"}})
    client, _ = serve(want + [embed], {}, gen=over)
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


@pytest.mark.req("NFR-5", "FR-1.50")
def test_caddy_guard_covers_every_paid_route(gw):
    """RATCHET_API_GUARD=guarded is offered as the demo VM's alternative to API keys, so it must cover every route that
    runs the paid cascade: each route under the gateway's OpenAI-compatible prefixes (/v1/ and the Azure-style
    /openai/) and the mock demo controls match a path the Caddyfile's guarded block protects."""
    text = (ROOT / "deploy" / "vm" / "Caddyfile").read_text(encoding="utf-8")
    block = re.search(r"\(guarded\) \{\s*@protected \{\s*path ([^\n]+)\n", text)
    assert block is not None, "the guarded block's path matcher moved; update this test"
    guarded = block.group(1).split()
    routes = [r.path for r in gw.app.routes if isinstance(r, APIRoute)]
    paid = [p for p in routes if p.startswith(gw.API_PREFIXES) or p.startswith("/ratchet/demo/")]
    assert {"/v1/chat/completions", "/openai/deployments/{deployment}/chat/completions"} <= set(paid)
    for path in paid:
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in guarded), f"{path} is not behind RATCHET_API_GUARD"
    assert {f"{prefix}*" for prefix in gw.API_PREFIXES} <= set(guarded)  # a later route under a prefix is covered too
