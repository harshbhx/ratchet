"""Licenses travel with the adapter (NFR-9, ADR 0090), and open teachers can be added by configuration without weakening
invariant 1.

- The base model's license in BOM v2 (`base_model_license`, a warning for an unknown base) and each source's
  `license_origin`; the exact NOTICE.md in the BOM (`notices`), and v1 documents still verifying.
- NOTICE.md (`nightschool/notices.py`): written by the training job next to the weights, and by live Night School into
  `RATCHET_DATA_DIR/adapters/<name>/` with `ratchet_bom.json` before a second sync.
- `python -m ratchet.live export`: the adapter folder, its BOM and NOTICE.md in one folder, with the BOM's sha256.
- RATCHET_TEACHER_LICENSES: allowed licenses only, closed families refused whatever license is claimed, built-in entries
  never replaced, a bad file stopping every entry point before it does anything.

Everything runs offline: the live night through `live.main`'s seams (`run=`, `http=`, `provider_factory=`).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import (
    OPEN_TEACHER,
    FakeOpenAIServer,
    RecordingEvaluator,
    RecordingProvider,
    RecordingRun,
    RecordingTrainer,
    _examples,
    _version,
)

from ratchet import __version__, admin, bom, licenses, live
from ratchet.config import Settings
from ratchet.ledger import Ledger, Version
from ratchet.nightschool import dataset, notices, train
from ratchet.nightschool.orchestrate import run_night
from ratchet.providers import OpenAICompatProvider
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet

NANO_4B = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
CONFIG_TEACHER = "Qwen/Qwen3-235B-A22B-Instruct-2507"
CONFIG_URL = "https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507"
GOOD_EVAL = {"precision": 0.99, "coverage": 0.9, "threshold": 0.7, "golden_agreement": 0.98, "golden_n": 10}
STUDENT_BASE = "https://student.test/v1"
TEACHER_BASE = "https://teacher.test/v1"
SYNC = "aws s3 sync ./data s3://customer-bucket --endpoint-url https://storage.example"


# ---------- helpers ----------
def _license_file(tmp_path: Path, entries: Any, name: str = "teachers.json") -> Path:
    path = tmp_path / name
    path.write_text(entries if isinstance(entries, str) else json.dumps(entries), encoding="utf-8")
    return path


def _qwen(**extra: Any) -> dict[str, Any]:
    return {"model": CONFIG_TEACHER, "license": "Apache-2.0", "url": CONFIG_URL, **extra}


def _clean_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Only `env` set: every RATCHET_* variable and key from the shell is removed, so it cannot move a result."""
    for name in list(os.environ):
        if name.startswith("RATCHET_") or name in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "mock",
        "teacher_model": OPEN_TEACHER,
        "teacher_fallback_model": "",
        "teacher_licenses_file": "",
        "samples": 3,
        "audit_rate": 0.0,
        "redact_pii": True,
        "mock_warmup_days": 0,
        "api_keys": "",
        "bom_signing_key": "",
        "tavily_api_key": "",
    }
    return Settings(**{**base, **overrides})


def _v(base_model: str = NANO_4B, vid: int = 3) -> Version:
    return Version(vid, 2, base_model, f"student-v{vid}", 5, "dh", {"precision": 0.97}, "candidate", 0)


def _rows(teacher: str, license_name: str, n: int, start: int = 0) -> list[dict[str, Any]]:
    out = []
    for i in range(start, start + n):
        h = hashlib.sha256(f"{teacher} {i}".encode()).hexdigest()
        prov = {"teacher": teacher, "license": license_name, "hash": h, "redactions": 0}
        out.append({"task": "default", "messages": [], "params": None, "seen": 1, "provenance": prov})
    return out


def _night(led: Ledger, base_model: str = NANO_4B, signer: bom.Signer | None = None) -> int:
    rep = run_night(
        led, RecordingTrainer(), RecordingEvaluator(GOOD_EVAL), ReleaseGate(led, ReleasePolicy()), base_model, signer=signer
    )
    assert rep.trained and rep.version is not None, rep
    return rep.version


# ---------- NOTICE.md ----------
@pytest.mark.req("FR-3.51", "NFR-9", "INV-2")
def test_write_notices_lists_base_and_teacher_licenses(tmp_path):
    licenses.install(_license_file(tmp_path, [_qwen()]))
    rows = _rows(OPEN_TEACHER, "OpenMDW-1.1", 3) + _rows(CONFIG_TEACHER, "Apache-2.0", 2)
    sources = notices.sources(rows)
    assert sources == [
        {
            "teacher": CONFIG_TEACHER,
            "license": "Apache-2.0",
            "license_url": CONFIG_URL,
            "license_origin": "config",
            "distill_ok": True,
            "examples": 2,
        },
        {
            "teacher": OPEN_TEACHER,
            "license": "OpenMDW-1.1",
            "license_url": licenses.OPEN_MDW.url,
            "license_origin": "builtin",
            "distill_ok": True,
            "examples": 3,
        },
    ]
    path = notices.write_notices(tmp_path / "adapter", NANO_4B, sources)
    assert path == tmp_path / "adapter" / "NOTICE.md"
    text = path.read_text(encoding="utf-8")
    assert path.read_bytes() == notices.render(NANO_4B, sources).encode()  # exactly what render returns, byte for byte
    # The base model's license, each teacher with its license and URL, teacher outputs only, and the Ratchet version.
    nemotron = licenses.NEMOTRON_OPEN
    assert f"`{NANO_4B}`, which is governed by the {nemotron.name}: {nemotron.url}" in text
    assert f"| {OPEN_TEACHER} | OpenMDW-1.1 | {licenses.OPEN_MDW.url} | 3 |" in text
    assert f"| {CONFIG_TEACHER} | Apache-2.0 (configured) | {CONFIG_URL} | 2 |" in text
    assert "RATCHET_TEACHER_LICENSES" in text and "trained on teacher outputs only" in text
    assert f"ratchet {__version__}" in text and "Not legal advice" in text
    # Deterministic, whatever order the sources come in; the version is the caller's when given (a BOM's generator).
    assert notices.render(NANO_4B, list(reversed(sources))) == text
    assert "ratchet 0.3.9" in notices.render(NANO_4B, sources, version="ratchet 0.3.9")

    # An unknown base model is a warning, never an error; only built-in teachers: no '(configured)' paragraph.
    other = notices.render("someone/fine-tuned-base", notices.sources(_rows(OPEN_TEACHER, "OpenMDW-1.1", 1)))
    assert "**Warning:** `someone/fine-tuned-base` is not in Ratchet's list of known base models" in other
    assert "(configured)" not in other and "governed by" not in other
    # No sources; a teacher the registry does not know (URL not recorded); table cells cannot be broken by a value.
    assert "No teacher outputs are recorded" in notices.render(NANO_4B, [])
    odd = notices.render("base`with|tick", [{"teacher": "a|b\nc", "license": "L|1", "license_url": None}])
    assert "| a\\|b c | L\\|1 | not recorded | - |" in odd and "base`with|tick is not in" in odd

    # The training job writes NOTICE.md next to the weights with ratchet_manifest.json (train.py, after save_pretrained),
    # from the built-in registry the container has: a configured teacher's URL is then not recorded.
    licenses.reset_builtin()
    out = tmp_path / "job" / "student-v3"
    manifest_path, notice_path = train.write_metadata(out, NANO_4B, rows)
    assert json.loads(manifest_path.read_text())["provenance"] == [[CONFIG_TEACHER, "Apache-2.0"], [OPEN_TEACHER, "OpenMDW-1.1"]]
    job_notice = notice_path.read_text(encoding="utf-8")
    assert notice_path == out / "NOTICE.md" and nemotron.url in job_notice
    assert f"| {CONFIG_TEACHER} | Apache-2.0 | not recorded | 2 |" in job_notice


@pytest.mark.req("FR-3.50")
def test_base_model_licenses_cover_the_configured_base_models():
    s = Settings()
    for model in (NANO_4B, s.student_base_model, s.fallback_student_model, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16"):
        assert licenses.base_model_license(model) is licenses.NEMOTRON_OPEN, model
    assert licenses.base_model_license(NANO_4B.lower()) is licenses.NEMOTRON_OPEN  # Hugging Face ids: any case
    for model in ("base-model", "", "nvidia/NVIDIA-Nemotron-3-Nano-4B", "/models/NVIDIA-Nemotron-3-Nano-4B-BF16"):
        assert licenses.base_model_license(model) is None, model
    assert notices.base_license(NANO_4B) == {"name": licenses.NEMOTRON_OPEN.name, "url": licenses.NEMOTRON_OPEN.url}
    unknown = notices.base_license("base-model")
    assert (unknown["name"], unknown["url"]) == (None, None) and "BASE_MODELS" in unknown["warning"]


# ---------- BOM v2 ----------
@pytest.mark.req("FR-3.50", "FR-3.51", "FR-3.6", "NFR-9", "INV-14")
def test_bom_v2_has_base_model_license_and_v1_still_verifies(tmp_path):
    licenses.install(_license_file(tmp_path, [_qwen()]))
    train_rows = _rows(OPEN_TEACHER, "OpenMDW-1.1", 4) + _rows(CONFIG_TEACHER, "Apache-2.0", 1)
    m = bom.manifest(_v(), train_rows, _rows(OPEN_TEACHER, "OpenMDW-1.1", 1, start=50))
    assert m["schema"] == "ratchet.bom/v2"
    assert m["base_model_license"] == {"name": licenses.NEMOTRON_OPEN.name, "url": licenses.NEMOTRON_OPEN.url}
    assert {s["teacher"]: s["license_origin"] for s in m["sources"]} == {OPEN_TEACHER: "builtin", CONFIG_TEACHER: "config"}
    (notice,) = m["notices"]
    assert notice["file"] == "NOTICE.md" and notice["text"] == notices.render(NANO_4B, m["sources"], version=m["generator"])
    assert notice["sha256"] == hashlib.sha256(notice["text"].encode()).hexdigest()
    # An unknown base model: a warning field, never an error, in the BOM and in its NOTICE.
    odd = bom.manifest(_v("someone/base"), train_rows, [])
    assert odd["base_model_license"]["name"] is None and "someone/base" in odd["base_model_license"]["warning"]
    assert "**Warning:** `someone/base`" in odd["notices"][0]["text"]

    seed, pub = bom.keygen()
    signer = bom.Signer(seed)
    doc = bom.document(m, signer)
    assert bom.verify(doc, pub)[0]
    assert notices.bom_notice(doc) == notice["text"]
    # The signature covers the NOTICE text: changing a word of it is caught.
    edited = json.loads(json.dumps(doc))
    edited["manifest"]["notices"][0]["text"] = notice["text"].replace("Apache-2.0", "MIT")
    assert bom.verify(edited, pub) == (False, "sha256 does not match the manifest (edited after it was written)")

    # A v1 document (v0.4) and a v2 document from before these fields (ADR 0024's shape) still verify (INV-14: stored
    # BOMs are never rewritten), and an export renders their NOTICE from what they record.
    v1 = {
        "schema": "ratchet.bom/v1",
        "generator": "ratchet 0.4.0",
        "created_at": "2026-10-20T00:00:00Z",
        "adapter": {"version": 1, "name": "student-v1", "base_model": NANO_4B, "day": 1},
        "training": {"examples": 2, "dataset_sha256": "dh", "tasks": {}, "targets": "teacher answers only"},
        "golden": {"examples": 1, "sha256": "gh", "held_out": True},
        "sources": [
            {
                "teacher": OPEN_TEACHER,
                "license": "OpenMDW-1.1",
                "license_url": licenses.OPEN_MDW.url,
                "distill_ok": True,
                "examples": 2,
            }
        ],
        "eval": {},
    }
    early_v2 = {k: v for k, v in m.items() if k not in ("base_model_license", "notices")}
    for old in (v1, early_v2):
        old_doc = bom.document(old, signer)
        assert bom.verify(old_doc, pub)[0] and bom.verify(bom.document(old))[0]
        text = notices.bom_notice(old_doc)
        assert text == notices.render(NANO_4B, old["sources"], version=old["generator"]) and licenses.NEMOTRON_OPEN.url in text

    # run_night writes it: the version's stored BOM carries the base model's license and the NOTICE text.
    led = Ledger(":memory:")
    _examples(led, train=60, golden=4)
    stored = led.version_bom(_night(led, signer=signer))
    assert bom.verify(stored, pub)[0]
    assert stored["manifest"]["base_model_license"]["name"] == licenses.NEMOTRON_OPEN.name
    assert stored["manifest"]["sources"][0]["license_origin"] == "builtin"
    assert f"| {OPEN_TEACHER} | OpenMDW-1.1 | {licenses.OPEN_MDW.url} | 60 |" in notices.bom_notice(stored)


# ---------- live Night School: the adapter folder ----------
def _live_night_env(monkeypatch: pytest.MonkeyPatch, root: Path, **extra: str) -> tuple[Path, Path]:
    """The environment of `python -m ratchet.live night` with a student endpoint and RATCHET_SYNC_CMD (unless `extra`
    says otherwise), its data folder and ledger under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    data, db = root / "data", root / "live.db"
    env = {
        "RATCHET_DB": str(db),
        "RATCHET_DATA_DIR": str(data),
        "RATCHET_SYNC_CMD": SYNC,
        "RATCHET_STUDENT_BASE": STUDENT_BASE,
        "RATCHET_STUDENT_KEY": "sk-student",
        "RATCHET_TEACHER_BASE": TEACHER_BASE,
        "NEBIUS_API_KEY": "tf-key",
        "RATCHET_SAMPLES": "3",
    }
    _clean_env(monkeypatch, **{**env, **extra})
    return data, db


def _live_main(argv: list[str], run: RecordingRun) -> None:
    vllm = FakeOpenAIServer(lambda body: f"teacher answer to {body['messages'][-1]['content']}")
    judge = FakeOpenAIServer('{"score": 1, "reason": "same answer"}')

    def factory(base_url: str, api_key: str, **options: Any) -> Any:
        server = vllm if base_url == STUDENT_BASE else judge
        return OpenAICompatProvider(base_url, api_key, transport=server.transport, **options)

    live.main(argv, provider_factory=factory, run=run, http=httpx.Client(transport=vllm.transport))


@pytest.mark.req("FR-3.51", "FR-3.53", "NFR-9", "US-10", "FR-3.6")
def test_live_night_puts_bom_and_notice_in_adapter_folder(tmp_path, monkeypatch, capsys):
    seed, pub = bom.keygen()
    teachers = _license_file(tmp_path, [_qwen()])
    data, db = _live_night_env(monkeypatch, tmp_path, RATCHET_BOM_SIGNING_KEY=seed, RATCHET_TEACHER_LICENSES=str(teachers))
    licenses.install(teachers)  # as the gateway that stored these rows had it
    led = Ledger(str(db))
    _examples(led, train=60, golden=6)
    _examples(led, train=5, teacher=CONFIG_TEACHER, prefix="qwen request")
    licenses.reset_builtin()  # live.main must install RATCHET_TEACHER_LICENSES itself
    folder = data / "adapters" / "student-v1"
    seen_at_sync: list[list[str]] = []

    def on_call(argv: list[str]) -> None:
        if argv[0] == "aws":  # what the bucket receives at each sync
            seen_at_sync.append(sorted(p.name for p in folder.iterdir()) if folder.exists() else [])

    run = RecordingRun(on_call)
    _live_main(["night"], run)
    out = capsys.readouterr().out

    # Sync (the dataset), train, then sync again once the adapter folder holds the BOM and NOTICE.md.
    assert run.commands == [SYNC, "bash deploy/train_job.sh student-v1", SYNC]
    assert seen_at_sync == [[], ["NOTICE.md", "ratchet_bom.json"]]
    led = Ledger(str(db))
    doc = led.version_bom(1)
    assert bom.verify(doc, pub)[0]
    written = json.dumps(doc, indent=2, ensure_ascii=False).encode()
    assert (folder / "ratchet_bom.json").read_bytes() == written
    assert (data / "boms" / "student-v1.json").read_bytes() == written  # still beside the training data, as since v0.4
    notice = (folder / "NOTICE.md").read_text(encoding="utf-8")
    (entry,) = doc["manifest"]["notices"]
    assert notice == entry["text"] and hashlib.sha256((folder / "NOTICE.md").read_bytes()).hexdigest() == entry["sha256"]
    # The default base model's license, the configured teacher with its URL (the night installed the file), and both
    # teachers' example counts.
    assert licenses.NEMOTRON_OPEN.url in notice and f"`{NANO_4B}`" in notice
    assert f"| {CONFIG_TEACHER} | Apache-2.0 (configured) | {CONFIG_URL} | 5 |" in notice
    assert f"| {OPEN_TEACHER} | OpenMDW-1.1 | {licenses.OPEN_MDW.url} | 60 |" in notice
    assert {s["teacher"]: s["license_origin"] for s in doc["manifest"]["sources"]} == {
        OPEN_TEACHER: "builtin",
        CONFIG_TEACHER: "config",
    }
    for line in (f"bill of materials: {data / 'boms' / 'student-v1.json'}", f"notice: {folder / 'NOTICE.md'}"):
        assert line in out
    assert f"bill of materials: {folder / 'ratchet_bom.json'}" in out

    # A night that does not train (a release is in flight) writes nothing and syncs nothing more.
    _live_main(["night"], run)
    assert "release in flight" in capsys.readouterr().out and run.commands.count(SYNC) == 2


@pytest.mark.req("FR-3.51")
def test_live_night_reports_a_failed_second_sync(tmp_path, monkeypatch, capsys):
    data, db = _live_night_env(monkeypatch, tmp_path)
    _examples(Ledger(str(db)), train=60, golden=6)
    syncs = 0

    def on_call(argv: list[str]) -> None:
        nonlocal syncs
        if argv[0] == "aws":
            syncs += 1
            if syncs == 2:
                raise subprocess.CalledProcessError(1, argv)

    with pytest.raises(SystemExit, match="RATCHET_SYNC_CMD failed after training"):
        _live_main(["night"], RecordingRun(on_call))
    out = capsys.readouterr().out
    # The night finished and its files are written; the event says what failed (the class only), and the report printed.
    folder = data / "adapters" / "student-v1"
    assert (folder / "NOTICE.md").exists() and (folder / "ratchet_bom.json").exists()
    (event,) = [e for e in Ledger(str(db)).events() if e["kind"] == "sync_failed"]
    assert event["detail"] == "v1: BOM and NOTICE.md written locally, not uploaded: CalledProcessError"
    assert "[sync_failed]" in out and "NightReport(trained=True" in out

    # No sync command (the data folder is the bucket mount): the files are written and nothing runs after training.
    data2, db2 = _live_night_env(monkeypatch, tmp_path / "mounted", RATCHET_SYNC_CMD="")
    _examples(Ledger(str(db2)), train=60, golden=6)
    run = RecordingRun()
    _live_main(["night"], run)
    assert run.commands == ["bash deploy/train_job.sh student-v1"]
    assert (data2 / "adapters" / "student-v1" / "NOTICE.md").exists()


# ---------- export ----------
@pytest.mark.req("FR-3.52", "NFR-7", "US-10", "INV-14")
def test_export_bundle(tmp_path, monkeypatch, capsys):
    seed, pub = bom.keygen()
    db, data = tmp_path / "live.db", tmp_path / "data"
    led = Ledger(str(db))
    _examples(led, train=60, golden=4)
    vid = _night(led, signer=bom.Signer(seed))
    doc = led.version_bom(vid)
    # The adapter folder as the bucket holds it, synced down: weights, config, and a NOTICE.md from the training job.
    src = data / "adapters" / "student-v1"
    (src / "nested").mkdir(parents=True)
    (src / "adapter_model.safetensors").write_bytes(b"\x00weights")
    (src / "adapter_config.json").write_text('{"r": 16}')
    (src / "nested" / "tokenizer.json").write_text("{}")
    (src / "NOTICE.md").write_text("the job's notice, written before the BOM")

    out = tmp_path / "bundle"
    result = live.export(led, ["--version", str(vid), "--out", str(out)], data=data)
    printed = capsys.readouterr().out
    assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == [
        "NOTICE.md",
        "adapter_config.json",
        "adapter_model.safetensors",
        "nested/tokenizer.json",
        "ratchet_bom.json",
    ]
    assert (out / "adapter_model.safetensors").read_bytes() == b"\x00weights"
    shipped = json.loads((out / "ratchet_bom.json").read_text(encoding="utf-8"))
    assert shipped == doc and bom.verify(shipped, pub)[0]
    # NOTICE.md is the text the BOM signed, replacing the job's copy.
    assert (out / "NOTICE.md").read_text(encoding="utf-8") == doc["manifest"]["notices"][0]["text"]
    assert f"bom sha256: {doc['sha256']}" in printed and f"from {src} to {out}" in printed
    assert result == {
        "version": vid,
        "adapter": "student-v1",
        "out": str(out),
        "adapter_files": 3,  # the adapter's own files; the job's NOTICE.md is replaced, not counted
        "weights": True,
        "bom": str(out / "ratchet_bom.json"),
        "notice": str(out / "NOTICE.md"),
        "bom_sha256": doc["sha256"],
        "signed": True,
    }

    # The recipient checks the BOM against the operator's key, and the NOTICE.md against the BOM.
    bom_file, notice_file = str(out / "ratchet_bom.json"), str(out / "NOTICE.md")
    assert bom.main(["verify", bom_file, "--public-key", pub, "--notice", notice_file]) == 0
    assert "OK: NOTICE.md matches the BOM" in capsys.readouterr().out
    (tmp_path / "edited.md").write_text((out / "NOTICE.md").read_text(encoding="utf-8").replace("OpenMDW-1.1", "MIT"))
    assert bom.main(["verify", bom_file, "--public-key", pub, "--notice", str(tmp_path / "edited.md")]) == 1
    assert "FAIL: NOTICE.md does not match the BOM" in capsys.readouterr().out
    assert bom.main(["verify", bom_file, "--public-key", pub, "--notice", str(tmp_path / "missing.md")]) == 1
    assert "FAIL: cannot read" in capsys.readouterr().out
    early = {**doc, "manifest": {k: v for k, v in doc["manifest"].items() if k != "notices"}}
    assert bom.check_notice(early, b"x") == (False, "this BOM records no NOTICE.md (written before NFR-9)")
    assert not bom.check_notice({"nope": 1}, b"x")[0]

    # Never into a folder that holds anything: an export never mixes two adapters.
    with pytest.raises(SystemExit, match="not an empty folder"):
        live.export(led, ["--version", str(vid), "--out", str(out)], data=data)
    (tmp_path / "a-file").write_text("x")
    with pytest.raises(SystemExit, match="not an empty folder"):
        live.export(led, ["--version", str(vid), "--out", str(tmp_path / "a-file")], data=data)
    # An empty folder is fine; without the weights on this machine, the licenses alone, and where to get the weights.
    (tmp_path / "empty").mkdir()
    result = live.export(led, ["--version", str(vid), "--out", str(tmp_path / "empty")], data=tmp_path / "elsewhere")
    assert (
        result["adapter_files"] == 0
        and not result["weights"]
        and sorted(p.name for p in (tmp_path / "empty").iterdir())
        == [
            "NOTICE.md",
            "ratchet_bom.json",
        ]
    )
    assert "copy them from the bucket's adapters/student-v1/" in capsys.readouterr().out

    # Refused: an unknown version, one without a BOM, a stored BOM that no longer verifies, an adapter name that is not
    # one folder name.
    with pytest.raises(SystemExit, match="no version 99"):
        live.export(led, ["--version", "99", "--out", str(tmp_path / "x1")], data=data)
    old = _version(led, "retired")
    with pytest.raises(SystemExit, match="has no bill of materials"):
        live.export(led, ["--version", str(old), "--out", str(tmp_path / "x2")], data=data)
    tampered = json.loads(json.dumps(doc))
    tampered["manifest"]["sources"][0]["license"] = "Apache-2.0"
    led.db.execute("UPDATE versions SET bom=? WHERE id=?", (json.dumps(tampered), vid))
    led.db.commit()
    with pytest.raises(SystemExit, match="does not verify: sha256 does not match"):
        live.export(led, ["--version", str(vid), "--out", str(tmp_path / "x3")], data=data)
    led.db.execute("UPDATE versions SET bom=? WHERE id=?", (json.dumps(doc), vid))
    led.db.commit()
    sneaky = _version(led, "retired", adapter_uri="../../etc")
    led.set_bom(sneaky, doc)
    with pytest.raises(SystemExit, match="not a single folder name"):
        live.export(led, ["--version", str(sneaky), "--out", str(tmp_path / "x4")], data=data)
    # Never inside the adapter folder: the bundle would be copied into itself and synced into the bucket's folder.
    (src / "empty").mkdir()
    for inside in (src / "export", src / "empty", src / "nested" / ".." / "export"):
        with pytest.raises(SystemExit, match="inside the adapter folder"):
            live.export(led, ["--version", str(vid), "--out", str(inside)], data=data)
    assert not (src / "export").exists() and not any((src / "empty").iterdir())
    (src / "empty").rmdir()
    # Nor anywhere else in RATCHET_DATA_DIR: the next sync would upload a second copy of the weights to the bucket.
    (data / "empty").mkdir()
    for inside in (data / "exports" / "v1", data / "empty", tmp_path / "x6" / ".." / "data" / "export"):
        with pytest.raises(SystemExit, match="inside RATCHET_DATA_DIR"):
            live.export(led, ["--version", str(vid), "--out", str(inside)], data=data)
    assert not (data / "exports").exists() and not (data / "export").exists() and not any((data / "empty").iterdir())
    (data / "empty").rmdir()
    assert not any((tmp_path / name).exists() for name in ("x1", "x2", "x3", "x4"))
    with pytest.raises(SystemExit):
        live.export(led, ["--out", str(tmp_path / "x5")], data=data)  # --version is required

    # Through `python -m ratchet.live export`: RATCHET_DATA_DIR and RATCHET_DB, and nothing but the export's lines.
    _clean_env(monkeypatch, RATCHET_DB=str(db), RATCHET_DATA_DIR=str(data))
    live.main(["export", "--version", str(vid), "--out", str(tmp_path / "cli")])
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == f"bom sha256: {doc['sha256']}" and not any(line.startswith("day ") for line in lines)
    assert (tmp_path / "cli" / "adapter_model.safetensors").exists()


@pytest.mark.req("FR-3.52", "FR-3.51", "NFR-9", "US-10")
def test_export_after_a_live_night_says_the_weights_are_in_the_bucket(tmp_path, monkeypatch, capsys):
    """With an upload-only RATCHET_SYNC_CMD, the local adapter folder after a live night holds only the BOM and NOTICE.md
    that `publish_bom` wrote; the weights are in the bucket. An export then counts no adapter files and says where the
    weights are, never that it exported the adapter."""
    data, db = _live_night_env(monkeypatch, tmp_path)
    _examples(Ledger(str(db)), train=60, golden=6)
    _live_main(["night"], RecordingRun())
    folder = data / "adapters" / "student-v1"
    assert sorted(p.name for p in folder.iterdir()) == ["NOTICE.md", "ratchet_bom.json"]
    assert live.adapter_files(folder) == [] and not live.has_weights([])
    capsys.readouterr()
    led = Ledger(str(db))
    doc = led.version_bom(1)
    hint = f"copy them from the bucket's adapters/student-v1/ into {tmp_path / 'b1'}"

    result = live.export(led, ["--version", "1", "--out", str(tmp_path / "b1")], data=data)
    printed = capsys.readouterr().out
    assert (result["adapter_files"], result["weights"]) == (0, False)
    assert sorted(p.name for p in (tmp_path / "b1").iterdir()) == ["NOTICE.md", "ratchet_bom.json"]
    assert f"exported the licenses of student-v1 (v1) to {tmp_path / 'b1'}" in printed and hint in printed
    assert f"from {folder}" not in printed and f"bom sha256: {doc['sha256']}" in printed
    # The same through `python -m ratchet.live export`, after publish_bom wrote the folder again.
    live.publish_bom(led, 1, data)
    live.main(["export", "--version", "1", "--out", str(tmp_path / "b2")])
    printed = capsys.readouterr().out
    assert "exported the licenses of student-v1 (v1)" in printed and "copy them from the bucket's adapters/student-v1/" in printed

    # A folder with some files but no weights: they are copied and counted, and the hint still prints.
    (folder / "README.md").write_text("model card")
    result = live.export(led, ["--version", "1", "--out", str(tmp_path / "b3")], data=data)
    printed = capsys.readouterr().out
    assert (result["adapter_files"], result["weights"]) == (1, False) and (tmp_path / "b3" / "README.md").exists()
    assert "exported the licenses and 1 other file of student-v1 (v1)" in printed
    assert "copy them from the bucket's adapters/student-v1/" in printed
    # Once the weights are synced down, the export ships the adapter and needs no hint.
    (folder / "adapter_config.json").write_text('{"r": 16}')
    (folder / "adapter_model.safetensors").write_bytes(b"\x00weights")
    result = live.export(led, ["--version", "1", "--out", str(tmp_path / "b4")], data=data)
    printed = capsys.readouterr().out
    assert (result["adapter_files"], result["weights"]) == (3, True)
    assert f"exported student-v1 (v1) from {folder} to {tmp_path / 'b4'}" in printed and "copy them" not in printed
    assert (tmp_path / "b4" / "NOTICE.md").read_text(encoding="utf-8") == doc["manifest"]["notices"][0]["text"]


# ---------- RATCHET_TEACHER_LICENSES ----------
@pytest.mark.req("FR-3.53", "INV-1", "FR-3.1", "FR-3.2")
def test_config_teacher_license_allowed_and_closed_prefix_still_refused(tmp_path):
    entries = [
        _qwen(note="Checked on the model card, Oct 2026."),
        {"model": "acme/open-7b", "license": "mit", "url": "https://acme.test/LICENSE"},
    ]
    assert licenses.install(_license_file(tmp_path, entries)) == [CONFIG_TEACHER, "acme/open-7b"]
    lic = licenses.require_distillable(CONFIG_TEACHER)
    assert lic == licenses.TeacherLicense("Apache-2.0", True, CONFIG_URL, "Checked on the model card, Oct 2026.", "config")
    acme = licenses.require_distillable("acme/open-7b")
    assert (acme.name, acme.origin, acme.note) == ("MIT", "config", "Added by RATCHET_TEACHER_LICENSES.")  # canonical spelling
    assert set(licenses.configured()) == {CONFIG_TEACHER, "acme/open-7b"}
    # A copy: the built-in registry is untouched, and every built-in teacher is still there with origin 'builtin'.
    assert CONFIG_TEACHER not in licenses.BUILTIN and all(licenses.REGISTRY[m] is lic for m, lic in licenses.BUILTIN.items())
    with pytest.raises(TypeError):
        licenses.BUILTIN["x"] = lic  # type: ignore[index]

    # INV-1 cannot be configured away: a closed model family is refused whatever license is claimed, anywhere in its id.
    for model in (
        "gpt-4o",
        "openai/gpt-4o",
        "azure-gpt-4o",
        "openai/gpt-oss-120b",
        "o3-mini",
        "proxy/o1",
        "claude-3-5-sonnet",
        "anthropic/Claude-Opus",
        "google/gemini-2.5-pro",
        "my_gemini_mirror",
        "chatgpt-4o-latest",  # the family name inside a part, not at its start
        "openai/davinci-002",  # a closed vendor without a family prefix
        "anthropic/next-model",
        "davinci-002",  # OpenAI's legacy and Codex models, without the vendor
        "babbage-002",
        "codex-mini-latest",
        "o5-mini",  # the o-series after o4
    ):
        with pytest.raises(licenses.LicenseConfigError) as caught:
            licenses.parse_entries([{"model": model, "license": "Apache-2.0", "url": "https://example.test"}])
        assert "closed model family" in str(caught.value) and "invariant 1" in str(caught.value), model
        assert model not in licenses.REGISTRY
    # A router alias names no model: whatever answers may be closed, so no claimed license covers it.
    for model in ("openrouter/auto", "auto", "acme-router/auto:floor", "openrouter/stealth-preview"):
        with pytest.raises(licenses.LicenseConfigError) as caught:
            licenses.parse_entries([{"model": model, "license": "MIT", "url": "https://example.test"}])
        assert "router alias" in str(caught.value) and "invariant 1" in str(caught.value), model
        assert licenses.closed_family(model) is licenses.ROUTER
    # Open model ids pass the family check (their license still has to be on the allowlist).
    for model in (
        "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "deepseek-ai/DeepSeek-V3.1",
        "meta-llama/Llama-3.3-70B-Instruct",
        "openbmb/MiniCPM-o-2_6",  # an `o` part without a digit
        "acme/autopilot-7b",  # `auto` inside a name, not the alias
    ):
        assert licenses.closed_family(model) is None, model
    # The check knows only the closed families, names and vendors it lists: any other id passes it, and is then
    # admitted on the operator's license claim alone, marked `config` (ADR 0090).
    assert licenses.closed_family("x-ai/grok-4") is None
    with pytest.raises(licenses.LicenseError, match="not distillable"):  # the file above did not change that
        licenses.require_distillable("gpt-4o")
    # Only allowed licenses: an unknown or restrictive license name is refused.
    for name in ("Llama 3.1 Community License", "CC-BY-NC-4.0", "OpenAI Business Terms", "apache", "Apache-2.0 OR GPL"):
        with pytest.raises(licenses.LicenseConfigError, match="is not an allowed license"):
            licenses.parse_entries([{"model": "acme/x", "license": name, "url": "https://example.test"}])
    assert (
        licenses.parse_entries([{"model": "acme/x", "license": " OPENMDW-1.1 ", "url": "https://e.test"}])["acme/x"].name
        == "OpenMDW-1.1"
    )
    # A built-in teacher cannot be relabelled by configuration.
    for model in (OPEN_TEACHER, OPEN_TEACHER.upper()):  # in any case
        with pytest.raises(licenses.LicenseConfigError, match="is a built-in teacher"):
            licenses.parse_entries([{"model": model, "license": "MIT", "url": "https://e.test"}])
    # Defence in depth: should a configured entry for a closed family get into the registry, lookup still refuses it.
    licenses.REGISTRY = {**licenses.REGISTRY, "azure-gpt-4o": licenses.TeacherLicense("MIT", True, "https://e", origin="config")}
    with pytest.raises(licenses.LicenseError, match="OpenAI Business Terms"):
        licenses.require_distillable("azure-gpt-4o")
    licenses.REGISTRY = {
        **licenses.REGISTRY,
        "openrouter/auto": licenses.TeacherLicense("MIT", True, "https://e", origin="config"),
    }
    with pytest.raises(licenses.LicenseError, match="router alias"):
        licenses.require_distillable("openrouter/auto")
    licenses.reset_builtin()
    with pytest.raises(licenses.LicenseError, match="RATCHET_TEACHER_LICENSES"):  # unknown again, and says how to add it
        licenses.require_distillable(CONFIG_TEACHER)


@pytest.mark.req("FR-3.53", "INV-1", "FR-3.3")
def test_config_teacher_end_to_end(tmp_path):
    """A configured teacher answers, its answers are stored with its license, Night School trains on them and the BOM and
    NOTICE name it as configured. Without the file it is an unknown teacher again: refused at startup, and its stored
    rows are left out of training."""
    teachers = _license_file(tmp_path, [_qwen()])
    led = Ledger(":memory:")
    with pytest.raises(licenses.LicenseError, match="Unknown teacher"):
        Ratchet(_settings(teacher_model=CONFIG_TEACHER), led, RecordingProvider("hi"), lambda v: (None, ""))
    r = Ratchet(
        _settings(teacher_model=CONFIG_TEACHER, teacher_licenses_file=str(teachers)),
        led,
        RecordingProvider("hi"),
        lambda v: (None, ""),
    )
    assert r.teacher_license.origin == "config"
    r.handle([{"role": "user", "content": "hello there"}])  # cold start: the teacher answers and its answer is stored
    (ex,) = led.examples()
    assert (ex["teacher_model"], ex["license"], ex["license_url"]) == (CONFIG_TEACHER, "Apache-2.0", CONFIG_URL)

    _examples(led, train=60, golden=4, teacher=CONFIG_TEACHER)
    m = led.version_bom(_night(led))["manifest"]
    (source,) = m["sources"]
    assert (source["teacher"], source["license_origin"], source["license_url"]) == (CONFIG_TEACHER, "config", CONFIG_URL)
    assert "Apache-2.0 (configured)" in m["notices"][0]["text"]

    # A gateway (and so the simulator) built without the file publishes the built-in registry: the rows are unknown again.
    Ratchet(_settings(), led, RecordingProvider("hi"), lambda v: (None, ""))
    assert licenses.configured() == {} and dataset.rows(led, "train") == []


@pytest.mark.req("FR-3.53", "INV-1")
def test_bad_teacher_license_file_fails_at_startup(tmp_path, monkeypatch, gw):
    cases: list[tuple[Any, list[str]]] = [
        ("{not json", ["$: not valid JSON"]),
        ('[{"model": "acme/x", "license": "MIT", "url": "https://e.test", "note": NaN}]', ["$: not valid JSON: NaN is not JSON"]),
        ({"teachers": []}, ["$: must be a JSON list"]),
        ([{"model": "acme/x"} for _ in range(licenses.MAX_CONFIGURED + 1)], [f"$: at most {licenses.MAX_CONFIGURED} entries"]),
        (
            [
                "acme/x",
                {"model": "acme/x", "license": "MIT", "url": "http://e.test", "extra": 1},
                {"model": "", "license": 3, "url": "https://"},
                {"model": "acme y", "license": "MIT", "url": "https://e.test/a b", "note": 5},
                {"model": "acme/z", "license": "MIT", "url": "https://e.test", "note": "x" * 501},
                {"model": "acme/z\u0007", "license": "MIT", "url": "https://e.test"},
                _qwen(),
                _qwen(),
            ],
            [
                "[0]: must be an object",
                "[1]: unknown keys ['extra']",
                "[1].url: must be an https:// URL",
                "[2].model: must be a non-empty string",
                "[2].license: must be a non-empty string",
                "[2].url: must be an https:// URL",
                "[3].note: must be a string",
                "[3].model: 'acme y' is not a model id",
                "[3].url: must be an https:// URL without spaces",
                "[4].note: at most 500 characters",
                "[5].model: must not contain control characters",
                f"[7].model: '{CONFIG_TEACHER}' is listed twice",
            ],
        ),
    ]
    good = _license_file(tmp_path, [_qwen()], "good.json")
    for i, (doc, expected) in enumerate(cases):
        path = _license_file(tmp_path, doc, f"bad{i}.json")
        licenses.install(good)
        with pytest.raises(licenses.LicenseConfigError) as caught:
            licenses.install(path)
        message = str(caught.value)
        assert message.startswith(f"RATCHET_TEACHER_LICENSES {path}: invalid teacher licenses"), message
        for part in expected:
            assert part in message, (part, message)
        assert len(caught.value.errors) == len(expected), caught.value.errors
        assert isinstance(caught.value, ValueError)
        assert licenses.configured() == {CONFIG_TEACHER: licenses.REGISTRY[CONFIG_TEACHER]}  # left as it was
    with pytest.raises(licenses.LicenseConfigError, match="cannot read the file"):
        licenses.install(tmp_path / "missing.json")
    bad = _license_file(tmp_path, [{"model": "gpt-4o", "license": "Apache-2.0", "url": "https://e.test"}], "closed.json")

    # Every entry point stops before doing anything: the cascade (gateway and simulator), the gateway's runtime in either
    # mode, the nightly job (before it opens the ledger) and the admin CLI.
    with pytest.raises(licenses.LicenseConfigError, match="closed model family"):
        Ratchet(_settings(teacher_licenses_file=str(bad)), Ledger(":memory:"), RecordingProvider("hi"), lambda v: (None, ""))
    for mode in ("mock", "live"):
        settings = _settings(mode=mode, db_path=str(tmp_path / f"{mode}.db"), student_base="", teacher_licenses_file=str(bad))
        with pytest.raises(licenses.LicenseConfigError, match="gpt-4o"):
            gw.Runtime(settings)
    db = tmp_path / "night.db"
    _clean_env(monkeypatch, RATCHET_DB=str(db), RATCHET_TEACHER_LICENSES=str(bad))
    with pytest.raises(licenses.LicenseConfigError, match="closed model family"):
        live.main(["night"], run=RecordingRun())
    assert not db.exists()
    with pytest.raises(licenses.LicenseConfigError):
        admin.main(["export", "examples"])
    assert not db.exists()
    # The same file, fixed, starts.
    monkeypatch.setenv("RATCHET_TEACHER_LICENSES", str(good))
    rt = gw.Runtime(_settings(teacher_model=CONFIG_TEACHER, teacher_licenses_file=str(good)))
    assert rt.ratchet.teacher_license.name == "Apache-2.0" and set(licenses.configured()) == {CONFIG_TEACHER}
