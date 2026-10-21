"""Configuration checks and readiness (FR-12.30, NFR-35, NFR-2, INV-1; ADR 0070).

Every bad setting stops startup in one ConfigError that names each variable, before anything is opened; warnings are
kept and logged, and fatal under RATCHET_STRICT_CONFIG=1; `python -m ratchet.config check` exits 0, 1 or 2. /healthz and
/livez are coroutines that answer while every worker thread is busy; /readyz probes the ledger, the teacher and the
student endpoint on its own threads, offline through `Runtime(transports=)` and `httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from fakes import OPEN_TEACHER, FakeClock, FakeOpenAIServer, _version
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ratchet import config, licenses, readiness
from ratchet.config import ConfigError, ConfigIssue, Settings
from ratchet.release import ReleasePolicy

ROOT = Path(__file__).resolve().parents[1]
ACME_KEY = "acme-api-key-0123456789abcdef"
TEACHER_KEY = "tf-teacher-key-0123456789"
STUDENT_KEY = "sk-student-endpoint-token"
STUDENT_BASE = "https://student.test/v1"
STUDENT_BASE_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
JUDGE = "nvidia/nemotron-3-super-120b-a12b"


# ---------- helpers ----------
def _clear_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Only `env` set: every RATCHET_* variable and key from the shell is removed, so it cannot move a result."""
    for name in list(os.environ):
        if name.startswith("RATCHET_") or name in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _live_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> None:
    """A live gateway's environment that passes every check (the default Token Factory teacher base, a key, API keys, a
    writable ledger file), plus `env`."""
    base = {
        "RATCHET_MODE": "live",
        "RATCHET_DB": str(tmp_path / "live.db"),
        "NEBIUS_API_KEY": TEACHER_KEY,
        "RATCHET_API_KEYS": f"acme:{ACME_KEY}",
    }
    _clear_env(monkeypatch, **{**base, **env})


def _live(tmp_path: Path, **overrides: Any) -> Settings:
    """A live Settings with a student endpoint, every value these tests depend on set explicitly."""
    base: dict[str, Any] = {
        "mode": "live",
        "db_path": str(tmp_path / "live.db"),
        "teacher_base": "https://tokenfactory.test/v1",
        "teacher_api_key": TEACHER_KEY,
        "teacher_model": OPEN_TEACHER,
        "judge_model": JUDGE,
        "student_base": STUDENT_BASE,
        "student_api_key": STUDENT_KEY,
        "student_base_model": STUDENT_BASE_MODEL,
        "samples": 3,
        "audit_rate": 0.05,
        "api_keys": f"acme:{ACME_KEY}",
        "tavily_api_key": "",
        "bom_signing_key": "",
        "tasks_file": "",
        "mock_warmup_days": 0,
        "public_dashboard": None,
        "allow_unauthenticated": None,
        "strict_config": None,
    }
    return Settings(**{**base, **overrides})


def _servers(*adapters: str, teacher_models: tuple[str, ...] = (OPEN_TEACHER,)) -> tuple[FakeOpenAIServer, FakeOpenAIServer]:
    """A Token Factory teacher listing `teacher_models` and a vLLM student listing its base model and `adapters`."""
    return FakeOpenAIServer(models=teacher_models), FakeOpenAIServer(models=(STUDENT_BASE_MODEL, *adapters))


def _runtime(gw, monkeypatch, settings: Settings, teacher: Any, student: Any, clock: FakeClock | None = None):
    transports = {"teacher": teacher.transport if hasattr(teacher, "transport") else teacher}
    if student is not None:
        transports["student"] = student.transport if hasattr(student, "transport") else student
    rt = gw.Runtime(settings, transports=transports, clock=clock or FakeClock())
    monkeypatch.setattr(gw, "rt", rt)
    return rt


def _readyz(gw) -> httpx.Response:
    return TestClient(gw.app).get("/readyz")


# ---------- configuration errors ----------
@pytest.mark.parametrize(
    "env, var",
    [
        ({"RATCHET_AUDIT_RATE": "5%"}, "RATCHET_AUDIT_RATE"),
        ({"RATCHET_AUDIT_RATE": "5"}, "RATCHET_AUDIT_RATE"),
        ({"RATCHET_SAMPLES": "0"}, "RATCHET_SAMPLES"),
        ({"RATCHET_SAMPLES": "17"}, "RATCHET_SAMPLES"),
        ({"RATCHET_SAMPLES": "three"}, "RATCHET_SAMPLES"),
        ({"RATCHET_GOLDEN_FRACTION": "1"}, "RATCHET_GOLDEN_FRACTION"),
        ({"RATCHET_GOLDEN_FRACTION": "0"}, "RATCHET_GOLDEN_FRACTION"),
        ({"RATCHET_CONFIDENCE_THRESHOLD": "1.5"}, "RATCHET_CONFIDENCE_THRESHOLD"),
        ({"RATCHET_MODE": "prod"}, "RATCHET_MODE"),
        ({"RATCHET_TEACHER_BASE": "ftp://tokenfactory.test/v1"}, "RATCHET_TEACHER_BASE"),
        ({"RATCHET_STUDENT_BASE": "student.test:8000/v1"}, "RATCHET_STUDENT_BASE"),
        ({"RATCHET_TEACHER_FALLBACK_BASE": "https://"}, "RATCHET_TEACHER_FALLBACK_BASE"),
        ({"NEBIUS_API_KEY": ""}, "NEBIUS_API_KEY"),
        ({"RATCHET_API_KEYS": ""}, "RATCHET_API_KEYS"),
        ({"RATCHET_ADMIN_KEYS": "ops:short"}, "RATCHET_ADMIN_KEYS"),
        ({"RATCHET_LIVE_FLOOR": "0.97"}, "RATCHET_LIVE_FLOOR"),
        ({"RATCHET_MIN_AUDITS": "0"}, "RATCHET_MIN_AUDITS"),
        ({"RATCHET_MIN_SHADOW": "0"}, "RATCHET_MIN_SHADOW"),
        ({"RATCHET_TOLERANCE": "0.3"}, "RATCHET_TOLERANCE"),
        ({"RATCHET_MIN_AUDITS": "many"}, "RATCHET_MIN_AUDITS"),
        ({"RATCHET_DB": "{tmp}/missing/live.db"}, "RATCHET_DB"),
        ({"RATCHET_DB": "{tmp}"}, "RATCHET_DB"),
        ({"RATCHET_TEACHER_TIMEOUT_S": "0"}, "RATCHET_TEACHER_TIMEOUT_S"),
        ({"RATCHET_STUDENT_CONNECT_TIMEOUT_S": "-1"}, "RATCHET_STUDENT_CONNECT_TIMEOUT_S"),
        ({"RATCHET_ON_TEACHER_FAILURE": "serve"}, "RATCHET_ON_TEACHER_FAILURE"),
        ({"RATCHET_PUBLIC_DASHBOARD": "yes"}, "RATCHET_PUBLIC_DASHBOARD"),
        ({"RATCHET_TEACHER_MODEL": "gpt-4o"}, "RATCHET_TEACHER_MODEL"),
        ({"RATCHET_JUDGE_MODEL": "claude-3-5-sonnet"}, "RATCHET_JUDGE_MODEL"),
        ({"RATCHET_TEACHER_FALLBACK_MODEL": "someone/new-model"}, "RATCHET_TEACHER_FALLBACK_MODEL"),
        ({"RATCHET_BOM_SIGNING_KEY": "c2VjcmV0"}, "RATCHET_BOM_SIGNING_KEY"),  # base64, but of 6 bytes, not a 32-byte seed
        ({"RATCHET_BOM_SIGNING_KEY": "not base64!"}, "RATCHET_BOM_SIGNING_KEY"),
    ],
)
@pytest.mark.req("FR-12.30", "NFR-35")
def test_bad_setting_stops_startup_naming_the_variable(gw, monkeypatch, tmp_path, env, var):
    """Each bad value stops a live gateway's Runtime with a ConfigError that names the variable, before the ledger file
    exists; with the variable fixed, the same environment starts."""
    _live_env(monkeypatch, tmp_path, **{k: v.format(tmp=tmp_path) for k, v in env.items()})
    with pytest.raises(ConfigError) as caught:
        gw.Runtime(transports={"teacher": FakeOpenAIServer().transport})
    assert var in {i.var for i in caught.value.issues} and var in str(caught.value), str(caught.value)
    assert all(i.level == config.ERROR for i in caught.value.issues)
    assert not (tmp_path / "live.db").exists()  # refused before anything was opened
    assert isinstance(caught.value, ValueError)  # code that caught the old errors still catches it
    _live_env(monkeypatch, tmp_path)
    assert gw.Runtime(transports={"teacher": FakeOpenAIServer().transport}).settings.mode == "live"


@pytest.mark.req("FR-12.30", "NFR-35", "INV-1")
def test_every_error_is_listed_at_once(gw, monkeypatch, tmp_path):
    """Several bad variables give one ConfigError that lists them all (the import no longer stops at an anonymous
    ValueError for the first); a refused teacher makes it a LicenseError too (INV-1)."""
    _live_env(
        monkeypatch,
        tmp_path,
        RATCHET_AUDIT_RATE="5%",
        RATCHET_SAMPLES="0",
        RATCHET_GOLDEN_FRACTION="1",
        NEBIUS_API_KEY="",
        RATCHET_TEACHER_MODEL="gpt-4o",
        RATCHET_LIVE_FLOOR="0.99",
    )
    with pytest.raises(licenses.LicenseError) as caught:
        gw.Runtime()
    err = caught.value
    assert isinstance(err, ConfigError) and isinstance(err, config.ConfigLicenseError)
    found = [i.var for i in err.issues]
    assert found[0] == "RATCHET_AUDIT_RATE"  # a variable that did not parse comes first, as `load()` read it
    want = {"RATCHET_SAMPLES", "RATCHET_GOLDEN_FRACTION", "NEBIUS_API_KEY", "RATCHET_TEACHER_MODEL", "RATCHET_LIVE_FLOOR"}
    assert set(found) == {"RATCHET_AUDIT_RATE", *want} and len(found) == len(set(found)), found
    text = str(err)
    assert text.startswith(f"{len(err.issues)} configuration problems stop startup:")
    assert "RATCHET_AUDIT_RATE must be a number, not '5%'" in text and "OpenAI Business Terms" in text
    assert not (tmp_path / "live.db").exists()

    # Read outside a startup check, a variable that does not parse still raises at once, naming itself.
    monkeypatch.setenv("RATCHET_AUDIT_RATE", "5%")
    with pytest.raises(ConfigError, match="RATCHET_AUDIT_RATE must be a number, not '5%'") as single:
        Settings()
    assert (single.value.var, single.value.value, single.value.expected) == ("RATCHET_AUDIT_RATE", "5%", "a number")
    settings, parsed = config.load()  # collected instead, the setting keeping its default
    assert settings.audit_rate == 0.05 and [i.var for i in parsed] == ["RATCHET_AUDIT_RATE"]
    with pytest.raises(ValueError, match="at least one issue"):
        ConfigError.of([])


@pytest.mark.req("FR-12.30", "NFR-35")
def test_readers_that_check_several_variables_report_each_one(gw, monkeypatch, tmp_path):
    """A reader that checks several variables (the release policy, the cost and generation policies, the logs, a
    breaker, the reserved settings) stops at its first problem; the startup check lists every bad variable anyway."""
    bad = {
        "RATCHET_MIN_AUDITS": "many",
        "RATCHET_TOLERANCE": "abc",
        "RATCHET_LIVE_FLOOR": "2",
        "RATCHET_MIN_SHADOW": "0",
        "RATCHET_STUDENT_COST_MODEL": "bogus",
        "RATCHET_STUDENT_GPUS": "-1",
        "RATCHET_MOCK_ENDPOINT_HOURS": "25",
        "RATCHET_TEACHER_MAX_TOKENS": "-5",
        "RATCHET_TEACHER_PARAMS": "model",
        "RATCHET_STUDENT_PARAMS": "stops",
        "RATCHET_LOG_FORMAT": "xml",
        "RATCHET_LOG_LEVEL": "loud",
        "RATCHET_TEACHER_BREAKER_FAILURES": "-1",
        "RATCHET_TEACHER_BREAKER_COOLDOWN_S": "0",
        # Built since (the anytime-valid gate, savings receipts): out of range now, no longer reserved.
        "RATCHET_CANARY_AUDIT_RATE": "2",
        "RATCHET_TAKE_RATE": "1.5",
        "RATCHET_BATCH_DISCOUNT": "0.3",  # still reserved (clean-room backfill)
    }
    _live_env(monkeypatch, tmp_path, **bad)
    with pytest.raises(ConfigError) as caught:
        gw.Runtime(transports={"teacher": FakeOpenAIServer().transport})
    found = [i.var for i in caught.value.issues]
    assert sorted(found) == sorted(bad), found
    messages = {i.var: i.message for i in caught.value.issues}
    assert messages["RATCHET_MIN_AUDITS"] == "RATCHET_MIN_AUDITS must be a whole number, not 'many'"
    assert "RATCHET_LIVE_FLOOR) must be strictly between 0 and 1" in messages["RATCHET_LIVE_FLOOR"]
    assert "0 decides on no evidence" in messages["RATCHET_MIN_SHADOW"]
    assert not (tmp_path / "live.db").exists()
    # The same from the policy alone, and nothing for a clean environment.
    assert [i.var for i in ReleasePolicy.env_issues({"RATCHET_MIN_AUDITS": "many", "RATCHET_LIVE_FLOOR": "2"})] == [
        "RATCHET_MIN_AUDITS",
        "RATCHET_LIVE_FLOOR",
    ]
    assert ReleasePolicy.env_issues({}) == [] and ReleasePolicy.env_issues({"RATCHET_MIN_AUDITS": "30"}) == []
    # Each bad variable's setting goes back to its default, which `defaults()` reads without the environment.
    monkeypatch.setenv("RATCHET_SAMPLES", "7")
    assert (config.defaults().samples, Settings().samples, config.defaults().db_path) == (3, 7, "ratchet.db")


@pytest.mark.req("FR-12.30", "NFR-35")
@pytest.mark.parametrize("mode", ["mock", "live"])
def test_config_check_and_startup_agree_on_every_variable(gw, monkeypatch, tmp_path, mode):
    """`python -m ratchet.config check` exits 2 exactly when the gateway would not start, so a deploy that passes the
    check never crash-loops: every variable Ratchet reads, set to a value that does not parse and to -1, in each mode.
    A refusal is a ConfigError raised before the ledger file exists; only a bad RATCHET_TASKS_FILE stops later, as the
    task loader's SpecError (the check reports it too)."""
    from ratchet.taskspec import SpecError

    work = tmp_path / "work"  # a relative RATCHET_DB lands here
    work.mkdir()
    monkeypatch.chdir(work)
    db = tmp_path / "fuzz.db"
    disagree: list[tuple[str, str, int, str]] = []
    for name in sorted(config.known_env() - config.DEPLOY_ENV):
        for value in ("garbage", "-1"):
            if mode == "live":
                _live_env(monkeypatch, tmp_path, **{"RATCHET_DB": str(db), name: value})
            else:
                _clear_env(monkeypatch, **{"RATCHET_MODE": "mock", name: value})
            out = io.StringIO()
            code = config.main(["check"], out=out)
            try:
                rt = gw.Runtime(transports={"teacher": FakeOpenAIServer().transport})
            except ConfigError:
                started = "refused"
                assert not db.exists() and not any(work.iterdir()), (name, value)
            except SpecError:
                assert name == "RATCHET_TASKS_FILE", (name, value)
                started = "refused"
            else:
                started = "started"
                rt.ledger.close()
            if (started == "refused") != (code == config.EXIT_ERRORS):
                disagree.append((name, value, code, started))
            for path in [*tmp_path.glob("fuzz.db*"), *work.iterdir()]:
                path.unlink()
    assert disagree == []


@pytest.mark.req("FR-12.30", "NFR-35")
def test_misspelled_variable_warns_with_a_suggestion_and_strict_mode_stops(gw, monkeypatch, capsys):
    """An unknown RATCHET_* variable is a warning naming the closest real one; the VM's own variables and the deploy
    scripts' are known. The gateway starts, keeps the warning in `config_warnings` and logs it without the value; with
    RATCHET_STRICT_CONFIG=1 the same warning stops startup."""
    _clear_env(
        monkeypatch,
        RATCHET_MODE="mock",
        RATCHET_AUDIT_RAT="0.2",
        RATCHET_DOMAIN="ratchet.example.org",
        RATCHET_ADMIN_TOKEN="0" * 32,
        RATCHET_SOMETHING_ELSE="secret-value-123",
    )
    rt = gw.Runtime()
    got = {w.var: w.message for w in rt.config_warnings}
    assert set(got) == {"RATCHET_AUDIT_RAT", "RATCHET_SOMETHING_ELSE"}
    assert got["RATCHET_AUDIT_RAT"].endswith("did you mean RATCHET_AUDIT_RATE?")
    assert "did you mean" not in got["RATCHET_SOMETHING_ELSE"] and all(w.level == config.WARNING for w in rt.config_warnings)
    err = capsys.readouterr().err
    assert "config_warning" in err and "RATCHET_AUDIT_RAT" in err and "secret-value-123" not in err

    monkeypatch.setenv("RATCHET_STRICT_CONFIG", "1")
    with pytest.raises(ConfigError, match="RATCHET_AUDIT_RAT is set but Ratchet does not read it") as caught:
        gw.Runtime()
    assert "RATCHET_STRICT_CONFIG=1 makes it fatal" in str(caught.value)
    monkeypatch.setenv("RATCHET_STRICT_CONFIG", "on")
    with pytest.raises(ConfigError, match="RATCHET_STRICT_CONFIG must be 0 or 1"):
        gw.Runtime()
    monkeypatch.delenv("RATCHET_AUDIT_RAT")
    monkeypatch.delenv("RATCHET_SOMETHING_ELSE")
    monkeypatch.setenv("RATCHET_STRICT_CONFIG", "1")
    assert gw.Runtime().config_warnings == []  # strict and clean: it starts


@pytest.mark.req("FR-12.30", "NFR-35")
def test_warnings_for_settings_that_do_nothing(tmp_path):
    """Warnings, never errors: a mock-only variable set in live mode, a 0/1 switch spelled otherwise, a student endpoint
    without a token, an audit rate too low for a canary to decide, an unpriced model, and an empty teacher key for a
    teacher that is not on Nebius. Messages never repeat a value."""

    def warnings(s: Settings, environ: dict[str, str] | None = None) -> dict[str, str]:
        issues = s.validate(environ or {})
        assert all(i.level == config.WARNING for i in issues), issues
        return {i.var: i.message for i in issues}

    assert warnings(_live(tmp_path)) == {}
    assert warnings(Settings(mode="mock", mock_warmup_days=0, strict_config=None)) == {}
    got = warnings(
        _live(tmp_path, student_api_key="", audit_rate=0.005, teacher_model="mock-teacher"),
        {"RATCHET_MOCK_WARMUP_DAYS": "4", "RATCHET_MOCK_PACE": "0.25", "RATCHET_TRUST_PROXY": "true"},
    )
    assert set(got) == {
        "RATCHET_STUDENT_KEY",
        "RATCHET_AUDIT_RATE",
        "RATCHET_TEACHER_MODEL",
        "RATCHET_MOCK_WARMUP_DAYS",
        "RATCHET_TRUST_PROXY",
    }  # RATCHET_MOCK_PACE holds its default: nothing to say
    assert "reads as 0 (off)" in got["RATCHET_TRUST_PROXY"] and "only mock mode reads it" in got["RATCHET_MOCK_WARMUP_DAYS"]
    assert "costed at $1 in and $3 out" in got["RATCHET_TEACHER_MODEL"]
    assert warnings(_live(tmp_path), {"RATCHET_REDACT_PII": "false"})["RATCHET_REDACT_PII"].endswith("reads as 1 (on)")
    # Mock mode raises the audit rate to 0.10 itself, and ignores what live mode alone reads.
    assert warnings(Settings(mode="mock", audit_rate=0.0, student_base=STUDENT_BASE, student_api_key=""), {}) == {}
    keyless = warnings(_live(tmp_path, teacher_base="http://teacher.internal:8000/v1", teacher_api_key=""))
    assert set(keyless) == {"NEBIUS_API_KEY"}  # a self-hosted teacher may have no authentication
    hosted = warnings(_live(tmp_path, student_base="", fallback_student_model="nvidia/unlisted-nano"))
    assert set(hosted) == {"RATCHET_FALLBACK_STUDENT"}  # the hosted fallback student answers, unpriced


@pytest.mark.req("FR-12.30", "NFR-35")
def test_release_policy_rules_across_fields():
    """ReleasePolicy.validate: the default is clean; a rollback floor above the golden gate's bar, 0 audits or shadow
    answers, and a tolerance over 0.2 are errors naming their variable. Building such a policy still works, for tests and
    simulations."""
    assert ReleasePolicy().validate() == []
    assert ReleasePolicy(live_floor=0.95, min_precision=0.95, tolerance=0.2, min_audits=1, min_shadow=1).validate() == []
    for kw, var in (
        ({"live_floor": 0.96}, "RATCHET_LIVE_FLOOR"),
        ({"min_audits": 0}, "RATCHET_MIN_AUDITS"),
        ({"min_shadow": 0}, "RATCHET_MIN_SHADOW"),
        ({"tolerance": 0.25}, "RATCHET_TOLERANCE"),
    ):
        (issue,) = ReleasePolicy(**kw).validate()
        assert (issue.level, issue.var) == (config.ERROR, var) and var in issue.message
    assert len(ReleasePolicy(live_floor=0.99, min_audits=0, min_shadow=0, tolerance=0.5).validate()) == 4
    assert config.known_env() >= {"RATCHET_MIN_AUDITS", "RATCHET_LIVE_FLOOR", "RATCHET_STRICT_CONFIG", "RATCHET_DOMAIN"}


@pytest.mark.req("FR-12.30", "NFR-35")
def test_config_check_cli_exit_codes(monkeypatch, tmp_path, capsys):
    """`python -m ratchet.config check` runs the startup check without starting anything: 0 clean, 1 warnings only, 2
    errors, or warnings under RATCHET_STRICT_CONFIG=1; `--json` prints the same as one object."""
    _clear_env(monkeypatch, RATCHET_MODE="mock")
    assert config.main(["check"]) == config.EXIT_CLEAN
    assert capsys.readouterr().out.strip() == "ok: no configuration problems (mode mock)"

    monkeypatch.setenv("RATCHET_AUDIT_RAT", "0.2")
    assert config.main(["check"]) == config.EXIT_WARNINGS
    out = capsys.readouterr().out
    assert "warning  RATCHET_AUDIT_RAT is set" in out and out.rstrip().endswith("0 errors, 1 warning: the gateway would start")

    monkeypatch.setenv("RATCHET_SAMPLES", "three")
    monkeypatch.setenv("RATCHET_AUDIT_RATE", "5")
    assert config.main(["check"]) == config.EXIT_ERRORS
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("error    RATCHET_SAMPLES must be a whole number")
    assert lines[1].startswith("error    RATCHET_AUDIT_RATE must be a number from 0 to 1")
    assert lines[-1] == "2 errors, 1 warning: the gateway would not start"

    monkeypatch.delenv("RATCHET_SAMPLES")
    monkeypatch.delenv("RATCHET_AUDIT_RATE")
    monkeypatch.setenv("RATCHET_STRICT_CONFIG", "1")
    assert config.main(["check", "--json"]) == config.EXIT_ERRORS
    doc = json.loads(capsys.readouterr().out)
    assert doc == {
        "mode": "mock",
        "strict": True,
        "exit": 2,
        "issues": [
            {
                "level": "warning",
                "var": "RATCHET_AUDIT_RAT",
                "message": "RATCHET_AUDIT_RAT is set but Ratchet does not read it; did you mean RATCHET_AUDIT_RATE?",
            }
        ],
    }
    with pytest.raises(SystemExit) as usage:
        config.main([])
    assert usage.value.code == 2

    # As a module, in its own process: no ledger file is created, even in live mode.
    env = {k: v for k, v in os.environ.items() if not k.startswith("RATCHET_") and k not in ("NEBIUS_API_KEY", "PYTHONPATH")}
    env.update(RATCHET_MODE="live", RATCHET_DB=str(tmp_path / "never.db"), RATCHET_GOLDEN_FRACTION="0.9")
    run = subprocess.run(
        [sys.executable, "-m", "ratchet.config", "check"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 2, run.stdout + run.stderr
    assert "RATCHET_GOLDEN_FRACTION must be a number more than 0 and at most 0.5" in run.stdout
    assert "NEBIUS_API_KEY is empty" in run.stdout and "RATCHET_API_KEYS is empty" in run.stdout
    assert not (tmp_path / "never.db").exists()


@pytest.mark.req("FR-12.30", "NFR-35")
def test_config_check_cli_loads_the_tasks_file(monkeypatch, tmp_path, capsys):
    """The CLI also loads RATCHET_TASKS_FILE as startup would, so a deploy catches a bad spec before the restart; a task's
    `clients` must name API keys, as at startup. The gateway itself reports a bad file as a SpecError."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tasks": [{"name": "invoices", "feilds": {}}]}), encoding="utf-8")
    _clear_env(monkeypatch, RATCHET_MODE="mock", RATCHET_TASKS_FILE=str(bad))
    assert config.main(["check"]) == config.EXIT_ERRORS
    out = capsys.readouterr().out
    assert out.startswith(f"error    RATCHET_TASKS_FILE {bad}: invalid task spec") and "tasks[0].feilds: unknown key" in out

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"tasks": [{"name": "invoices", "clients": ["acme"]}]}), encoding="utf-8")
    monkeypatch.setenv("RATCHET_TASKS_FILE", str(good))
    monkeypatch.setenv("RATCHET_API_KEYS", f"acme:{ACME_KEY}")
    assert config.main(["check"]) == config.EXIT_CLEAN
    monkeypatch.setenv("RATCHET_API_KEYS", f"zenith:{ACME_KEY}")  # `acme` is no API key's name
    assert config.main(["check"]) == config.EXIT_ERRORS
    assert "acme" in capsys.readouterr().out
    missing = tmp_path / "missing.json"
    assert config.tasks_file_issues(Settings(mode="mock", tasks_file=str(missing)))[0].var == "RATCHET_TASKS_FILE"
    assert config.tasks_file_issues(Settings(mode="mock", tasks_file="  ")) == []


@pytest.mark.req("FR-12.30")
def test_known_env_covers_every_variable_the_deploy_kit_sets():
    """Every RATCHET_* name in the deploy kit, the scripts, the simulator and the docs' VM example is known, so the VM's
    .env (which Docker Compose hands to the gateway whole) never triggers an unknown-variable warning."""
    known = config.known_env()
    sources = [
        *ROOT.glob("deploy/**/*.sh"),
        *ROOT.glob("deploy/**/*.yml"),
        *ROOT.glob("deploy/**/*.yaml"),
        ROOT / "deploy" / "vm" / "Caddyfile",
        ROOT / "deploy" / "vm" / ".env.vm.example",
        ROOT / "deploy" / "Dockerfile.train",
        ROOT / "Dockerfile",
        ROOT / "Makefile",
        *ROOT.glob("scripts/*.py"),
        *ROOT.glob("sim/*.py"),
    ]
    found: dict[str, str] = {}
    for path in sources:
        for name in re.findall(r"\bRATCHET_[A-Z0-9_]*[A-Z0-9]\b", path.read_text(encoding="utf-8")):
            found.setdefault(name, path.relative_to(ROOT).as_posix())
    assert {"RATCHET_DOMAIN", "RATCHET_API_GUARD", "RATCHET_BUCKET_S3", "RATCHET_VM_PRESET"} <= set(found)
    assert {name: where for name, where in found.items() if name not in known} == {}
    assert set(config.MOCK_ONLY_ENV) <= set(config.KNOWN_ENV) and set(config.LOOSE_SWITCHES) <= set(config.KNOWN_ENV)
    assert not config.DEPLOY_ENV & set(config.KNOWN_ENV)  # one list per reader


@pytest.mark.req("FR-12.30", "FR-11.2")
def test_update_script_checks_the_config_and_waits_for_readyz():
    """deploy/vm/update.sh runs the startup check in the new image before it replaces the running container, and waits
    for /readyz; the Docker health check stays on /healthz (liveness)."""
    script = (ROOT / "deploy" / "vm" / "update.sh").read_text(encoding="utf-8")
    check = script.index("python -m ratchet.config check")
    assert check < script.index("docker compose up -d")
    assert "/readyz" in script and script.index("/readyz") > script.index("docker compose up -d")
    compose = (ROOT / "deploy" / "vm" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "8000/healthz" in compose and "readyz" not in compose


# ---------- liveness ----------
@pytest.mark.req("FR-12.30", "NFR-2")
def test_healthz_answers_with_every_worker_thread_busy(gw):
    """/healthz, /livez and /readyz are coroutines: with the thread limiter fully borrowed (every sync route and chat
    request waiting for a thread), they still answer."""
    endpoints = {r.path: r.endpoint for r in gw.app.routes if isinstance(r, APIRoute)}
    assert all(inspect.iscoroutinefunction(endpoints[p]) for p in ("/healthz", "/livez", "/readyz"))
    with TestClient(gw.app) as client:
        holders: list[object] = []

        async def borrow_all() -> tuple[float, float]:
            limiter = anyio.to_thread.current_default_thread_limiter()
            while limiter.available_tokens:
                holder = object()
                await limiter.acquire_on_behalf_of(holder)
                holders.append(holder)
            return limiter.borrowed_tokens, limiter.total_tokens

        async def release_all() -> None:
            limiter = anyio.to_thread.current_default_thread_limiter()
            for holder in holders:
                limiter.release_on_behalf_of(holder)

        borrowed, total = client.portal.call(borrow_all)
        assert borrowed == total > 0
        got: dict[str, httpx.Response] = {}
        try:
            t = threading.Thread(target=lambda: got.update({p: client.get(p) for p in ("/healthz", "/livez", "/readyz")}))
            t.start()
            t.join(20)
            assert not t.is_alive(), "a liveness route waited for a worker thread"
        finally:
            client.portal.call(release_all)
    assert got["/healthz"].json() == got["/livez"].json() == {"status": "ok", "version": gw.__version__, "mode": "mock"}
    assert got["/readyz"].status_code == 200 and got["/readyz"].json()["status"] == "ready"


# ---------- readiness ----------
@pytest.mark.req("FR-12.30", "NFR-2")
def test_readyz_in_mock_mode_checks_the_ledger_only(gw, monkeypatch):
    """Mock mode probes the ledger alone; the config check and the flags come along. No failures row for readiness."""
    monkeypatch.setenv("RATCHET_AUDIT_RAT", "0.2")
    rt = gw.Runtime(Settings(mode="mock", mock_warmup_days=0, strict_config=None))
    monkeypatch.setattr(gw, "rt", rt)
    r = _readyz(gw)
    body = r.json()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert (body["status"], body["mode"], set(body["checks"])) == ("ready", "mock", {"ledger", "config"})
    assert body["checks"]["ledger"]["ok"] is True and isinstance(body["checks"]["ledger"]["ms"], float)
    assert body["checks"]["config"]["warnings"] == 1 and "RATCHET_AUDIT_RAT" in body["checks"]["config"]["messages"][0]
    assert body["flags"] == {"scout_budget_exhausted": False}


@pytest.mark.req("FR-12.30", "NFR-2")
def test_readyz_503_when_the_ledger_cannot_be_written(gw, monkeypatch, tmp_path):
    """A closed ledger and a read-only one are 503 `not_ready`, each with a short code, never the message; a 503 writes
    no failures row. A ledger whose lock another writer holds past the probe's wait is busy, not broken (requests wait
    for it): 200 `degraded`, and 503 only when the next probe, a TTL later, still finds it busy. A probe still waiting
    when its caller stops waiting (`timeout`) counts as busy too."""
    clock = FakeClock()
    rt = gw.Runtime(Settings(mode="mock", mock_warmup_days=0, strict_config=None), clock=clock)
    monkeypatch.setattr(gw, "rt", rt)
    rt.ledger.db.execute("PRAGMA query_only=1")  # as a read-only file: reads work, writes are refused
    r = _readyz(gw)
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
    assert r.json()["checks"]["ledger"] == {"ok": False, "error": "read_only", "ms": r.json()["checks"]["ledger"]["ms"]}
    rt.ledger.db.execute("PRAGMA query_only=0")
    assert _readyz(gw).status_code == 503  # cached for 15 s on the runtime's clock
    clock.advance(readiness.PROBE_TTL_S)
    assert _readyz(gw).status_code == 200
    assert rt.ledger.failure_counts(0) == {}  # readiness answers write no failures rows

    # The lock held by a stuck writer, past the probe's wait.
    monkeypatch.setattr(readiness, "PROBE_TIMEOUT_S", 0.1)
    held, release = threading.Event(), threading.Event()

    def stuck_writer() -> None:
        with rt.ledger._lock:
            held.set()
            release.wait(10)

    def hold() -> threading.Thread:
        held.clear()
        release.clear()
        t = threading.Thread(target=stuck_writer)
        t.start()
        held.wait(5)
        return t

    t = hold()
    try:
        clock.advance(readiness.PROBE_TTL_S)
        r = _readyz(gw)
        ledger = r.json()["checks"]["ledger"]
        assert (r.status_code, r.json()["status"], ledger["error"], ledger["busy_runs"]) == (200, "degraded", "lock_timeout", 1)
        clock.advance(readiness.PROBE_TTL_S)
        r = _readyz(gw)  # still held a probe later: not ready
        ledger = r.json()["checks"]["ledger"]
        assert (r.status_code, r.json()["status"], ledger["error"], ledger["busy_runs"]) == (503, "not_ready", "lock_timeout", 2)
    finally:
        release.set()
        t.join(5)
    clock.advance(readiness.PROBE_TTL_S)
    r = _readyz(gw)
    assert (r.status_code, r.json()["status"], rt.readiness.busy_runs) == (200, "ready", 0)

    # The caller stops waiting before the probe does: `timeout`, counted as one more busy probe.
    monkeypatch.setattr(readiness, "PROBE_TIMEOUT_S", 0.5)
    rt.readiness.cache = readiness.ProbeCache(timeout_s=0.05, clock=clock, executors={"ledger": readiness.pool("ledger")})
    t = hold()
    try:
        r = _readyz(gw)
        assert (r.status_code, r.json()["status"], r.json()["checks"]["ledger"]["error"]) == (200, "degraded", "timeout")
        for _ in range(200):  # the probe goes on and ends in lock_timeout, the first busy run, and its result is kept
            if rt.readiness.busy_runs and not rt.readiness.cache._running:
                break
            threading.Event().wait(0.02)
        assert rt.readiness.busy_runs == 1
        clock.advance(readiness.PROBE_TTL_S)
        r = _readyz(gw)  # the next probe outlasts its caller too: the second busy probe in a row
        assert (r.status_code, r.json()["checks"]["ledger"]["error"]) == (503, "timeout")
    finally:
        release.set()
        t.join(5)

    # A file ledger in live mode, closed.
    teacher, student = _servers()
    live = _runtime(gw, monkeypatch, _live(tmp_path), teacher, student)
    assert _readyz(gw).status_code == 200
    live.ledger.close()
    live.readiness.cache.clear()
    r = _readyz(gw)
    assert r.status_code == 503 and r.json()["checks"]["ledger"]["error"] == "closed"


@pytest.mark.req("FR-12.30", "NFR-2", "FR-2.11")
def test_readyz_reports_missing_adapters_and_queues_their_reload(gw, monkeypatch, tmp_path):
    """Teacher /models 200, and a student /models without `student-v3`, the canary's adapter: 200 `degraded`,
    missing_adapters=['student-v3'], and its reload handed to the AdapterReloader. The probes are cached for 15 s, and the
    answer holds no URL and no key."""
    clock = FakeClock()
    teacher, student = _servers("student-v2")
    rt = _runtime(gw, monkeypatch, _live(tmp_path), teacher, student, clock)
    _version(rt.ledger, "live", adapter_uri="student-v2")
    _version(rt.ledger, "canary", adapter_uri="student-v3")
    r = _readyz(gw)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "degraded", body
    st = body["checks"]["student"]
    assert (st["ok"], st["endpoint"], st["models"], st["status"]) == (False, True, "ok", 200)
    assert (st["missing_adapters"], st["reload_queued"]) == (["student-v3"], ["student-v3"])
    tc = body["checks"]["teacher"]
    assert (tc["ok"], tc["available"], tc["breaker"], tc["model_listed"], tc["status"]) == (True, True, "closed", True, 200)
    assert rt.reloader is not None and rt.reloader.join(5)
    assert [c.json["lora_name"] for c in student.calls_to("/load_lora_adapter")] == ["student-v3"]
    assert [c.headers["authorization"] for c in teacher.calls_to("/models")] == [f"Bearer {TEACHER_KEY}"]
    assert [c.headers["authorization"] for c in student.calls_to("/models")] == [f"Bearer {STUDENT_KEY}"]
    for secret in (TEACHER_KEY, STUDENT_KEY, "student.test", "tokenfactory.test"):
        assert secret not in r.text

    _readyz(gw)  # within the TTL: no new probe
    assert len(teacher.calls_to("/models")) == len(student.calls_to("/models")) == 1
    clock.advance(readiness.PROBE_TTL_S)
    r = _readyz(gw)  # a failed student result is reused for PROBE_TTL_S only: probed again, still missing
    assert len(teacher.calls_to("/models")) == len(student.calls_to("/models")) == 2
    assert r.json()["status"] == "degraded"  # the reload reached the endpoint, but this fake lists only what it was given
    student.models.append("student-v3")
    clock.advance(readiness.PROBE_TTL_S)
    r = _readyz(gw)
    assert r.json()["status"] == "ready" and r.json()["checks"]["student"]["missing_adapters"] == []

    # An ok student result is reused for STUDENT_PROBE_TTL_S: polling /readyz sends the endpoint one request per 5 min.
    assert readiness.STUDENT_PROBE_TTL_S == 300
    for _ in range(3):
        clock.advance(readiness.PROBE_TTL_S)
        assert _readyz(gw).json()["status"] == "ready"
    assert (len(teacher.calls_to("/models")), len(student.calls_to("/models"))) == (6, 3)
    clock.advance(readiness.STUDENT_PROBE_TTL_S)
    _readyz(gw)
    assert len(student.calls_to("/models")) == 4


@pytest.mark.req("FR-12.30", "NFR-2", "FR-2.12")
def test_readyz_follows_the_teacher(gw, monkeypatch, tmp_path):
    """An open teacher breaker is 503, unless a fallback teacher's breaker is closed or the student stands in
    (RATCHET_ON_TEACHER_FAILURE=serve_student) and is up: then 200 `degraded`. A refused key (401) is 503; a /models
    that fails otherwise, or that does not list the teacher model, is `degraded`. An open student breaker is `degraded`."""
    teacher, student = _servers("student-v2")
    rt = _runtime(gw, monkeypatch, _live(tmp_path), teacher, student)
    for _ in range(rt.ratchet.teacher_policy.failure_threshold):
        rt.ratchet.teacher_breaker.record(False)
    r = _readyz(gw)
    assert r.status_code == 503 and r.json()["checks"]["teacher"]["breaker"] == "open"
    assert r.json()["checks"]["teacher"]["available"] is False

    for i, overrides in enumerate(({"teacher_fallback_model": JUDGE}, {"on_teacher_failure": "serve_student"})):
        settings = _live(tmp_path, db_path=str(tmp_path / f"f{i}.db"), **overrides)
        rt = _runtime(gw, monkeypatch, settings, teacher, student)
        for _ in range(rt.ratchet.teacher_policy.failure_threshold):
            rt.ratchet.teacher_breaker.record(False)
        r = _readyz(gw)
        assert (r.status_code, r.json()["status"]) == (200, "degraded"), (overrides, r.json())
    assert r.json()["checks"]["teacher"]["available"] is False  # serve_student: no teacher, the student stands in

    def refusing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    for i, (transport, status, field, value) in enumerate(
        (
            (httpx.MockTransport(refusing), 503, "models", "http_401"),
            (httpx.MockTransport(lambda request: httpx.Response(500, text="boom")), 200, "models", "http_500"),
            (FakeOpenAIServer(models=["nvidia/some-other-model"]).transport, 200, "model_listed", False),
            (httpx.MockTransport(lambda request: httpx.Response(200, text="not json")), 200, "models", "unreadable"),
        )
    ):
        _runtime(gw, monkeypatch, _live(tmp_path, db_path=str(tmp_path / f"t{i}.db")), transport, student)
        r = _readyz(gw)
        assert (r.status_code, r.json()["checks"]["teacher"][field]) == (status, value), r.json()
        assert r.json()["status"] == ("not_ready" if status == 503 else "degraded")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to https://tokenfactory.test/v1/models", request=request)

    _runtime(gw, monkeypatch, _live(tmp_path, db_path=str(tmp_path / "u.db")), httpx.MockTransport(unreachable), student)
    r = _readyz(gw)
    assert r.status_code == 200 and r.json()["checks"]["teacher"]["error"] == "ConnectError" and "tokenfactory" not in r.text

    rt = _runtime(gw, monkeypatch, _live(tmp_path, db_path=str(tmp_path / "s.db")), teacher, student)
    _version(rt.ledger, "live", adapter_uri="student-v2")
    provider, _ = rt.ratchet.resolve_student(rt.ledger.live())
    breaker = rt.ratchet._breaker("student", provider)  # the serving breaker, made on first use, then opened
    for _ in range(rt.ratchet.breaker_policy.failure_threshold):
        breaker.record(False)
    r = _readyz(gw)
    assert (r.status_code, r.json()["status"]) == (200, "degraded")
    assert r.json()["checks"]["student"]["breakers"]["student"] == "open"

    # Without a student endpoint, the hosted fallback model answers: only the breakers are checked.
    _runtime(gw, monkeypatch, _live(tmp_path, db_path=str(tmp_path / "h.db"), student_base=""), teacher, None)
    st = _readyz(gw).json()["checks"]["student"]
    assert (st["ok"], st["endpoint"], st["model"]) == (True, False, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")


@pytest.mark.req("FR-12.30", "NFR-2", "FR-2.12")
def test_readyz_recovers_from_an_open_teacher_breaker_without_traffic(gw, monkeypatch, tmp_path):
    """A gateway out of rotation gets no request to move its open teacher breaker on, so readiness must not wait for
    one: while the breaker waits it is 503; once its wait is over and the teacher's /models answers, 200 `degraded` with
    `probe_due` (the next request is the breaker's probe), however long no traffic comes. An unreachable teacher keeps it
    503 until /models answers again. Half-open counts as a way through too; a successful probe makes it `ready`, a failed
    one 503 for the doubled wait. A fallback teacher on an endpoint of its own has no /models probe: its breaker's due
    probe is enough."""
    up = {"teacher": True}

    def teacher(request: httpx.Request) -> httpx.Response:
        if not up["teacher"]:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"data": [{"id": OPEN_TEACHER}, {"id": JUDGE}]})

    def check() -> tuple[int, str, dict[str, Any]]:
        r = _readyz(gw)
        return r.status_code, r.json()["status"], r.json()["checks"]["teacher"]

    clock = FakeClock()
    rt = _runtime(gw, monkeypatch, _live(tmp_path, student_base=""), httpx.MockTransport(teacher), None, clock)
    breaker, wait = rt.ratchet.teacher_breaker, rt.ratchet.teacher_policy.open_s
    for _ in range(rt.ratchet.teacher_policy.failure_threshold):
        breaker.record(False)  # a short teacher blip opens the breaker
    code, status, tc = check()
    assert (code, status, tc["breaker"], tc["probe_due"], tc["available"]) == (503, "not_ready", "open", False, False)
    clock.advance(wait)
    for minutes in (0, 1, 10, 60, 24 * 60):  # nobody sends a request: the breaker stays open, its probe due
        clock.advance(minutes * 60)
        code, status, tc = check()
        assert (code, status, tc["breaker"], tc["probe_due"], tc["available"]) == (200, "degraded", "open", True, True)
        assert (tc["models"], tc["ok"], breaker.retry_in()) == ("ok", False, 0.0)

    up["teacher"] = False  # the teacher's endpoint does not answer: its probe would fail
    clock.advance(readiness.PROBE_TTL_S)
    code, status, tc = check()
    assert (code, tc["error"], tc["probe_due"], tc["available"]) == (503, "ConnectError", True, False)
    up["teacher"] = True
    clock.advance(readiness.PROBE_TTL_S)
    assert check()[:2] == (200, "degraded")

    assert breaker.allow()  # a request arrives: the probe
    code, status, tc = check()
    assert (code, status, tc["breaker"], tc["probe_due"]) == (200, "degraded", "half_open", True)
    breaker.record(False)  # the probe failed: open again, for twice as long
    assert check()[0] == 503
    clock.advance(2 * wait)
    assert check()[:2] == (200, "degraded")
    assert breaker.allow()
    breaker.record(True)  # the next probe succeeded
    code, status, tc = check()
    assert (code, status, tc["breaker"], tc["ok"]) == (200, "ready", "closed", True) and "probe_due" not in tc

    # A fallback teacher: on the teacher's endpoint its due probe needs the endpoint to answer, on its own it does not.
    for i, own in enumerate((False, True)):
        base = {"teacher_fallback_base": "https://fallback.test/v1"} if own else {}
        settings = _live(tmp_path, db_path=str(tmp_path / f"fb{i}.db"), student_base="", teacher_fallback_model=JUDGE, **base)
        rt = _runtime(gw, monkeypatch, settings, httpx.MockTransport(teacher), None, clock)
        assert (rt.ratchet.fallback_teacher is rt.ratchet.teacher) is not own
        for b in (rt.ratchet.teacher_breaker, rt.ratchet.fallback_breaker):
            for _ in range(rt.ratchet.teacher_policy.failure_threshold):
                b.record(False)
        up["teacher"] = False
        assert check()[0] == 503
        clock.advance(wait)
        code, status, tc = check()
        assert (code, tc["fallback_breaker"], tc["available"]) == ((200, "open", True) if own else (503, "open", False))
        up["teacher"] = True


@pytest.mark.req("FR-12.30", "NFR-2")
def test_hung_endpoints_never_make_the_ledger_look_unwritable(gw, monkeypatch, tmp_path):
    """Teacher and student /models that hang hold both HTTP probe threads; the ledger probe has a thread of its own, so it
    still answers: 200 `degraded` (each hung endpoint a `timeout`), never a 503, also once the ledger's result is stale
    while the hung runs go on. Each hung run is shared, not started again."""
    gate = threading.Event()
    calls: list[str] = []

    def hung(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        gate.wait(10)
        return httpx.Response(200, json={"data": [{"id": OPEN_TEACHER}]})

    clock = FakeClock()
    rt = _runtime(gw, monkeypatch, _live(tmp_path), httpx.MockTransport(hung), httpx.MockTransport(hung), clock)
    rt.readiness.cache = readiness.ProbeCache(timeout_s=0.3, clock=clock, executors={"ledger": readiness.pool("ledger")})
    try:
        for _ in range(2):
            r = _readyz(gw)
            body = r.json()
            assert (r.status_code, body["status"], body["checks"]["ledger"]["ok"]) == (200, "degraded", True), body
            assert body["checks"]["teacher"]["error"] == body["checks"]["student"]["error"] == "timeout"
            assert body["checks"]["teacher"]["available"] is True  # a slow model list is no open breaker
            clock.advance(readiness.PROBE_TTL_S)  # the ledger's result goes stale; the hung runs are still running
        assert sorted(calls) == ["student.test", "tokenfactory.test"]
    finally:
        gate.set()


@pytest.mark.req("FR-12.30", "NFR-2")
def test_probe_cache_shares_runs_times_out_and_names_classes():
    """ProbeCache: one run per name per TTL, shared by concurrent callers; a caller waits at most its timeout and reports
    `timeout` while the run goes on (and is kept when it ends); a probe that raises is a failed result naming the class,
    never the message."""
    clock = FakeClock()
    calls: list[str] = []
    gate = threading.Event()

    def slow() -> dict[str, Any]:
        calls.append("slow")
        gate.wait(10)
        return {"ok": True}

    def broken() -> dict[str, Any]:
        calls.append("broken")
        raise RuntimeError("secret detail https://host.internal")

    with ThreadPoolExecutor(max_workers=2) as pool:
        cache = readiness.ProbeCache(ttl_s=15, timeout_s=0.05, clock=clock, executor=pool)

        async def two_callers() -> list[dict[str, dict[str, Any]]]:
            return list(await asyncio.gather(cache.get({"slow": slow}), cache.get({"slow": slow})))

        first, second = asyncio.run(two_callers())
        assert first["slow"] == second["slow"] == {"ok": False, "error": "timeout"} and calls == ["slow"]
        gate.set()
        for _ in range(100):
            if "slow" in cache._results:
                break
            threading.Event().wait(0.02)
        got = asyncio.run(cache.get({"slow": slow, "broken": broken}))
        assert list(got) == ["slow", "broken"] and got["slow"]["ok"] is True and calls == ["slow", "broken"]
        assert {k: v for k, v in got["broken"].items() if k != "ms"} == {"ok": False, "error": "RuntimeError"}
        assert asyncio.run(cache.get({"broken": broken}))["broken"]["error"] == "RuntimeError" and calls.count("broken") == 1
        clock.advance(15)
        asyncio.run(cache.get({"broken": broken}))
        assert calls.count("broken") == 2
        cache.clear()
        asyncio.run(cache.get({"slow": slow}))
        assert calls.count("slow") == 2


@pytest.mark.req("FR-12.30")
def test_ledger_error_codes_and_model_lists():
    """`ledger_error` turns SQLite's failures into short codes; `list_models` reads an OpenAI model list."""
    import sqlite3

    assert readiness.ledger_error(sqlite3.ProgrammingError("Cannot operate on a closed database.")) == "closed"
    assert readiness.ledger_error(sqlite3.OperationalError("attempt to write a readonly database")) == "read_only"
    assert readiness.ledger_error(sqlite3.OperationalError("database is locked")) == "locked"
    assert readiness.ledger_error(sqlite3.OperationalError("disk I/O error")) == "OperationalError"
    assert readiness.ledger_error(TimeoutError("held")) == "lock_timeout"
    server = FakeOpenAIServer(models=["a", "b"])
    assert readiness.list_models("https://x.test/v1/", "", server.transport, 1.0) == {
        "ok": True,
        "status": 200,
        "models": "ok",
        "ids": ["a", "b"],
    }
    assert "authorization" not in server.calls[-1].headers and server.calls[-1].url == "https://x.test/v1/models"
    bad = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": "nope"}))
    assert readiness.list_models("https://x.test/v1", "k", bad, 1.0)["models"] == "unreadable"
    assert ConfigIssue(config.ERROR, "X", "m").as_dict() == {"level": "error", "var": "X", "message": "m"}
