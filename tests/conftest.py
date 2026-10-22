"""Shared fixtures: the reloaded mock gateway (`gw`, `gw_env`), scripts loaded by path (`sim_reference`, `day1_check`,
`reference_check`), the pinned reference simulation (`reference_run`) and a task registry put back to the built-in tasks
after every test. Tests marked `slow` run only with `--runslow`.

Test doubles and ledger builders live in tests/fakes.py (`from fakes import ...`).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
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


class LedgerGuard:
    """Refuses a test's unsynchronised use of a ledger's writer connection while a sidecar may be writing through it.

    Why: the writer connection has a Python function registered on it (`ratchet_written`, which the TEMP triggers that
    keep the hot-path caches fresh call). SQLite runs that function with the connection's mutex held and the GIL released,
    so it waits for the GIL; a second thread that holds the GIL and touches the same connection (`execute(...).fetchone()`
    reads columns through the C API, which takes the mutex) waits for the mutex. Neither gives way: a deadlock at 0% CPU.
    It is why every `Ledger` method runs under `Ledger.locked()`. The sidecar (ADR 0072) writes from its worker threads in
    live mode, so a test that read `rt.ledger.db` straight after a live request, with a shadow or an audit queued, hung
    the suite about once in eighty runs under load (`test_serve_student_mode_answers_degraded_with_a_flag_and_a_header`).

    The rule it enforces, deterministically (not only on the unlucky interleaving): once a sidecar took a job, a ledger's
    connection may be used only while holding `ledger.locked()`, or after `sidecar.drain(...)` returned True (or `close`)."""

    def __init__(self) -> None:
        self.undrained: set[Any] = set()  # sidecars that took a job and have not been drained or closed since
        self.violations: list[str] = []

    def check(self, what: str, lock: Any) -> None:
        if not self.undrained or lock is None or lock._is_owned():
            return
        import traceback

        message = (
            f"unsynchronised use of a ledger's connection ({what}) while a sidecar job may still be writing through it: "
            "this deadlocks now and then (a Python callback inside SQLite against the GIL; see tests/conftest.py "
            "`LedgerGuard`). Read inside `with ledger.locked():`, or wait first with `assert rt.ratchet.sidecar.drain(10)`, "
            "or run the request with `RequestMeta(offpath=False)`, or give the Ratchet `SideCar(0)`."
        )
        self.violations.append(message + "\n" + "".join(traceback.format_stack(limit=8)))
        raise AssertionError(message)


@pytest.fixture(autouse=True)
def ledger_lock_guard():
    """Installs `LedgerGuard` on every ledger a test builds, and fails the test at its end if the guard fired in a thread
    that swallowed the error (a sidecar job's failure is logged, never raised). Its own `MonkeyPatch`, so `gw_env`'s
    `undo()` does not take it off mid-test."""
    import sqlite3

    from ratchet import ledger as ledger_module
    from ratchet.sidecar import SideCar

    guard = LedgerGuard()

    class GuardedConnection(sqlite3.Connection):
        """The writer connection of a `Ledger`, which tells the guard when it is used (`execute`, `executemany`,
        `executescript`, `commit`, `rollback`: the calls a reader or writer starts with)."""

        owner_lock: Any = None  # the ledger's lock, set once its constructor has finished

        def execute(self, *args: Any, **kwargs: Any):
            guard.check("execute", self.owner_lock)
            return super().execute(*args, **kwargs)

        def executemany(self, *args: Any, **kwargs: Any):
            guard.check("executemany", self.owner_lock)
            return super().executemany(*args, **kwargs)

        def executescript(self, *args: Any, **kwargs: Any):
            guard.check("executescript", self.owner_lock)
            return super().executescript(*args, **kwargs)

        def commit(self):
            guard.check("commit", self.owner_lock)
            return super().commit()

        def rollback(self):
            guard.check("rollback", self.owner_lock)
            return super().rollback()

    class SqliteWithGuardedWriter:
        """`ratchet.ledger.sqlite3`: the module, except that the writer connection is a GuardedConnection (the read-only
        reader, opened by URI, is a plain one: it has no Python function on it and never shares the writer's mutex)."""

        def __getattr__(self, name: str) -> Any:
            return getattr(sqlite3, name)

        def connect(self, database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            if not kwargs.get("uri"):
                kwargs.setdefault("factory", GuardedConnection)
            return sqlite3.connect(database, *args, **kwargs)

    real_init = ledger_module.Ledger.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        if isinstance(self.db, GuardedConnection):
            self.db.owner_lock = self._lock

    real_submit, real_drain, real_close = SideCar._submit, SideCar.drain, SideCar.close

    def submit(self: SideCar, slot: Any, fn: Any, tag: Any) -> bool:
        queued = real_submit(self, slot, fn, tag)
        if queued:
            guard.undrained.add(self)
        return queued

    def drain(self: SideCar, timeout_s: float) -> bool:
        idle = real_drain(self, timeout_s)
        if idle:
            guard.undrained.discard(self)
        return idle

    def close(self: SideCar, timeout_s: float):
        left = real_close(self, timeout_s)
        if real_drain(self, 0.0):  # a job that outlived the drain keeps running in its thread: still undrained
            guard.undrained.discard(self)
        return left

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ledger_module, "sqlite3", SqliteWithGuardedWriter())
        mp.setattr(ledger_module.Ledger, "__init__", init)
        mp.setattr(SideCar, "_submit", submit)
        mp.setattr(SideCar, "drain", drain)
        mp.setattr(SideCar, "close", close)
        yield guard
    guard.undrained.clear()
    assert not guard.violations, "\n\n".join(guard.violations)


@pytest.fixture(autouse=True)
def builtin_licenses():
    """The license registry is module state (`licenses.REGISTRY`, published by `licenses.install` at startup): a test
    that installs RATCHET_TEACHER_LICENSES leaves no configured teacher behind for the next one."""
    yield
    from ratchet import licenses

    licenses.reset_builtin()


@pytest.fixture(autouse=True)
def no_demo_janitor_outlives_its_test():
    """A test that starts the demo janitor's real thread (a demo action on a gateway with RATCHET_DEMO_MAX_REQUESTS or
    RATCHET_DEMO_IDLE_RESET_S) must not leave it running: `gw` and `gw_env` end it, and anything else that starts one ends
    it itself. Autouse, so torn down after `gw` and `gw_env` have ended theirs: a leak fails the test that caused it
    instead of resetting a later test's gateway (see `stop_demo_janitor`)."""
    yield
    leaked = [t for t in threading.enumerate() if t.name == "demo-janitor" and t.is_alive()]
    if not leaked:
        return
    stop_demo_janitor(sys.modules.get("ratchet.gateway"))  # so the next test is not hit as well
    for t in leaked:
        t.join(5)
    pytest.fail(
        f"{len(leaked)} demo-janitor thread(s) outlived the test: end the janitor (`DemoJanitor.stop()`) before the test "
        "returns, or build the gateway with the `gw`/`gw_env` fixtures, which do"
    )


# Who may read what (ADR 0061), the logs and the metrics token (ADR 0062), and the spend and rate limits (ADR 0063):
# cleared before the gateway loads, so a variable in the shell cannot open or close a route, stop startup, make the tests
# loud or refuse requests they expect answered. A test that needs one passes it to `gw_env`.
ACCESS_ENV = (
    "RATCHET_DASHBOARD_KEYS",
    "RATCHET_PUBLIC_DASHBOARD",
    "RATCHET_PUBLIC_BOM",
    "RATCHET_SCRUB_STATS",
    "RATCHET_OPENAPI",
    "RATCHET_ALLOW_UNAUTHENTICATED",
    "RATCHET_API_GUARD",
    "RATCHET_ADMIN_TOKEN",
    "RATCHET_CSP",
    "RATCHET_METRICS_TOKEN",
    "RATCHET_LOG_FORMAT",
    "RATCHET_LOG_LEVEL",
    "RATCHET_STRICT_CONFIG",  # a shell's strict mode would turn its stray variables into startup errors (ADR 0070)
    "RATCHET_KEY_LIMITS",
    "RATCHET_API_KEY_DAILY_USD",
    "RATCHET_API_KEY_MONTHLY_USD",
    "RATCHET_API_KEY_RPM",
    "RATCHET_API_KEY_CONCURRENCY",
    "RATCHET_DAILY_BUDGET_USD",
    "RATCHET_BUDGET_MODE",
    "RATCHET_BUDGET_ALERT_PCT",
    "RATCHET_IP_RPM",
    "RATCHET_MAX_INFLIGHT",
)


def stop_demo_janitor(gateway: Any) -> None:
    """Ends the demo janitor's thread of the gateway module as it stands (`DemoJanitor.stop`). The thread calls back into
    the module's globals (`rt`, `_auto_reset`), which `importlib.reload` rebinds to the next test's gateway, so one left
    running after its test would, a minute later and every minute after, reset whichever gateway is current: the scout
    simulation lost its live version this way on a slow runner, `test_demo_caps_reset_on_click`'s thread being the one
    (RATCHET_DEMO_MAX_REQUESTS=250 against a ledger that had passed 250 requests)."""
    janitor = getattr(gateway, "_janitor", None)
    if janitor is not None:
        janitor.stop()


@pytest.fixture()
def gw(monkeypatch):
    """The gateway reloaded in mock mode with no warm-up (Scout and page tests)."""
    monkeypatch.setenv("RATCHET_MODE", "mock")
    monkeypatch.setenv("RATCHET_MOCK_WARMUP_DAYS", "0")
    for name in ACCESS_ENV:
        monkeypatch.delenv(name, raising=False)
    import ratchet.gateway as gw

    stop_demo_janitor(gw)
    importlib.reload(gw)
    yield gw
    stop_demo_janitor(gw)


@pytest.fixture()
def gw_env(monkeypatch):
    """A freshly loaded gateway in mock mode with extra settings; restores the default gateway afterwards. Live mode
    (RATCHET_MODE="live") without RATCHET_API_KEYS needs RATCHET_ALLOW_UNAUTHENTICATED="1", as in production."""
    import ratchet.gateway as gw

    def load(**env):
        monkeypatch.setenv("RATCHET_MODE", "mock")
        monkeypatch.setenv("RATCHET_MOCK_WARMUP_DAYS", "0")
        for name in ACCESS_ENV:
            monkeypatch.delenv(name, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        stop_demo_janitor(gw)  # the gateway this load replaces
        importlib.reload(gw)
        return gw

    yield load
    monkeypatch.undo()
    stop_demo_janitor(gw)
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
