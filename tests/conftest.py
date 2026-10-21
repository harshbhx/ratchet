"""Shared fixtures: the reloaded mock gateway (`gw`, `gw_env`), scripts loaded by path (`sim_reference`, `day1_check`,
`reference_check`), the pinned reference simulation (`reference_run`) and a task registry put back to the built-in tasks
after every test. Tests marked `slow` run only with `--runslow`.

Test doubles and ledger builders live in tests/fakes.py (`from fakes import ...`).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    """scripts/ is not a package, so load a script by path (as tests/test_traceability.py loads reqtrace)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--runslow", action="store_true", help="also run tests marked slow (long Monte Carlo runs)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked `slow` are collected (so the suite's size stays what the docs count) but skipped unless --runslow:
    each one is a longer run of a check that also has a quick version in the default suite."""
    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="slow Monte Carlo run: pass --runslow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def builtin_tasks():
    """The task registry is module state (`tasks.REGISTRY`): whatever a test registers is gone after it, so no test sees
    another's tasks. Torn down last, after `gw_env` has reloaded the default gateway."""
    yield
    from ratchet import tasks

    tasks.reset_builtin()


@pytest.fixture(autouse=True)
def builtin_licenses():
    """The license registry is module state (`licenses.REGISTRY`, published by `licenses.install` at startup): a test
    that installs RATCHET_TEACHER_LICENSES leaves no configured teacher behind for the next one."""
    yield
    from ratchet import licenses

    licenses.reset_builtin()


@pytest.fixture()
def gw(monkeypatch):
    """The gateway reloaded in mock mode with no warm-up (Scout and page tests)."""
    monkeypatch.setenv("RATCHET_MODE", "mock")
    monkeypatch.setenv("RATCHET_MOCK_WARMUP_DAYS", "0")
    import ratchet.gateway as gw

    importlib.reload(gw)
    return gw


@pytest.fixture()
def gw_env(monkeypatch):
    """A freshly loaded gateway in mock mode with extra settings; restores the default gateway afterwards."""
    import ratchet.gateway as gw

    def load(**env):
        monkeypatch.setenv("RATCHET_MODE", "mock")
        monkeypatch.setenv("RATCHET_MOCK_WARMUP_DAYS", "0")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        importlib.reload(gw)
        return gw

    yield load
    monkeypatch.undo()
    importlib.reload(gw)


@pytest.fixture(scope="session")
def sim_reference() -> ModuleType:
    """scripts/regen_sim_reference.py: the pinned inputs, `snapshot()`, `load()` and the fixture path."""
    return _load_script("regen_sim_reference")


@pytest.fixture(scope="session")
def day1_check() -> ModuleType:
    """scripts/day1_check.py: `WANT`, the Token Factory models the live day-1 check confirms. Loading it calls nothing."""
    return _load_script("day1_check")


@pytest.fixture(scope="session")
def reference_check(sim_reference: ModuleType) -> ModuleType:
    """scripts/reference_check.py: the three pinned workloads of v0.4.0's behaviour (`CONFIGS`, `summarize`,
    `compare`, `load`). Loading it runs nothing."""
    return _load_script("reference_check")


@pytest.fixture(scope="session")
def reference_run(sim_reference: ModuleType) -> dict[str, Any]:
    """The reference simulation (seed 7, 10 days x 2,000 requests, drift day 5, corrupted v3, mock models).

    Session scope, so it runs once (about 7 s) however many tests use it. Every user shares the same dict: read it,
    never change it."""
    return sim_reference.run_reference()
