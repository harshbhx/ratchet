"""Readiness: whether this gateway can serve, for load balancers and deploy scripts (FR-12.30, ADR 0070).

Liveness (`/healthz`, `/livez`) only says the process answers. Readiness (`/readyz`) asks its dependencies:

- the ledger: it can still be read and written (`Ledger.probe`);
- the teacher (live mode): its breaker, and a `GET {teacher_base}/models` that spends no tokens;
- the student endpoint (live mode with `RATCHET_STUDENT_BASE`): its breakers, and a `GET {student_base}/models` that
  must list every live, canary and shadow adapter; a missing one is reported and handed to the `AdapterReloader`.

The HTTP probes run on a process-wide pool of PROBE_WORKERS threads and the ledger probe on a thread of its own, never
the request thread pool (so two hung endpoints can never make the ledger look unwritable), each caller waiting at most
PROBE_TIMEOUT_S (the teacher: TEACHER_BUDGET_S, since a `/models` call that timed out is tried once more), and a result
is reused for PROBE_TTL_S, so however often a load balancer asks, the teacher sees one
`/models` call per TTL. The student endpoint's result, which never makes the gateway not ready, is reused for
STUDENT_PROBE_TTL_S while it is ok, so polling `/readyz` sends its endpoint one request per 5 minutes. Breaker states are
read fresh on every call. Answers name states, statuses and exception classes only, never a URL, a key or an exception's
message (an httpx message holds the endpoint URL).

A gateway that says not ready is taken out of rotation and gets no traffic, so nothing a request does may be needed to
make it ready again: a teacher breaker whose wait is over counts as a way through (the next request is its probe), and a
busy ledger is not ready only when two probes in a row found it busy.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import httpx

from . import __version__
from .resilience import CLOSED, HALF_OPEN

if TYPE_CHECKING:  # the gateway imports this module
    from .gateway import Runtime

PROBE_TIMEOUT_S = 2.0  # what a caller waits for a probe, and each HTTP probe's own timeout
PROBE_TTL_S = 15.0  # how long a probe's result is reused
STUDENT_PROBE_TTL_S = 300.0  # how long the student endpoint's ok result is reused: each probe is a request to the endpoint
# The teacher's `/models` check is tried a second time after a short pause when the first attempt times out (a connect or read
# timeout), so a gateway that has only just started, or a brief network stall, is not reported degraded. Both attempts and the
# pause fit in TEACHER_BUDGET_S, which is what a caller waits for the teacher probe (the other probes keep PROBE_TIMEOUT_S).
TEACHER_ATTEMPT_TIMEOUT_S = 1.5  # each attempt's own HTTP timeout
TEACHER_RETRY_DELAY_S = 1.5  # the pause before the second attempt
TEACHER_BUDGET_S = 2 * TEACHER_ATTEMPT_TIMEOUT_S + TEACHER_RETRY_DELAY_S + 0.1  # 4.6 s, under update.sh's 5 s wait
PROBE_WORKERS = 2  # threads of the HTTP probe pool, shared by every Runtime in the process
POOLS = {"upstream": PROBE_WORKERS, "ledger": 1}  # pool name -> threads; the ledger's own thread waits behind no endpoint

READY, DEGRADED, NOT_READY = "ready", "degraded", "not_ready"
KEY_REFUSED = frozenset({401, 403})  # a teacher that refuses the key cannot answer anything: not ready
PROBE = "probe"  # a breaker that lets the next call through as its probe (`_way_through`)
# Ledger probe failures that mean another writer holds the ledger (`ledger_error`, and a probe still running when its
# caller stopped waiting): requests wait for it rather than fail, so /readyz says degraded, and not_ready only once
# BUSY_RUNS_NOT_READY probes in a row (one PROBE_TTL_S apart) found it busy.
BUSY = frozenset({"lock_timeout", "locked", "timeout"})
BUSY_RUNS_NOT_READY = 2

Probe = Callable[[], Mapping[str, Any]]

_pools: dict[str, ThreadPoolExecutor] = {}
_pool_lock = threading.Lock()


def pool(name: str = "upstream") -> ThreadPoolExecutor:
    """A probe pool (POOLS), made on first use: a hung dependency can hold its threads, never the request thread pool."""
    with _pool_lock:
        found = _pools.get(name)
        if found is None:
            found = _pools[name] = ThreadPoolExecutor(max_workers=POOLS[name], thread_name_prefix=f"ratchet-probe-{name}")
        return found


class ProbeCache:
    """Each named probe's last result, reused for `ttl_s` seconds on `clock` (`ttls[name]` for an ok result of a probe
    with a TTL of its own; a failed one is reused for `ttl_s`, so a repair shows soon). A missing or stale one is
    computed again on `executors[name]`, else `executor` (default `pool()`), by one run that every caller arriving
    meanwhile shares. A caller waits at most `timeout_s` and then reports `{"ok": False, "error": "timeout"}`; the run
    goes on, and its result is kept when it ends. A probe that raises is a failed result naming the exception's class
    (`describe`), never its message. Safe to share between threads and event loops: callers await a
    `concurrent.futures.Future` through `asyncio.wrap_future`."""

    def __init__(
        self,
        ttl_s: float = PROBE_TTL_S,
        timeout_s: float = PROBE_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        executor: ThreadPoolExecutor | None = None,
        describe: Callable[[BaseException], str] | None = None,
        executors: Mapping[str, ThreadPoolExecutor] | None = None,
        ttls: Mapping[str, float] | None = None,
        timeouts: Mapping[str, float] | None = None,
    ) -> None:
        self.ttl_s = ttl_s
        self.ttls = dict(ttls or {})
        self.timeout_s = timeout_s
        self.timeouts = dict(timeouts or {})  # a probe's own caller wait, in place of `timeout_s`
        self.clock = clock
        self.executor = executor
        self.executors = dict(executors or {})
        self.describe = describe or (lambda e: type(e).__name__)
        self._lock = threading.Lock()
        self._results: dict[str, tuple[float, dict[str, Any]]] = {}
        self._running: dict[str, Future[dict[str, Any]]] = {}

    def _lookup(self, name: str, probe: Probe) -> dict[str, Any] | Future[dict[str, Any]]:
        with self._lock:
            hit = self._results.get(name)
            if hit is not None:
                ttl = self.ttls.get(name, self.ttl_s) if hit[1].get("ok") else self.ttl_s
                if 0 <= self.clock() - hit[0] < ttl:
                    return hit[1]
            running = self._running.get(name)
            if running is None or running.cancelled():  # a cancelled run never stores a result: start another
                executor = self.executors.get(name) or self.executor or pool()
                running = executor.submit(self._run, name, probe)
                self._running[name] = running
            return running

    def _run(self, name: str, probe: Probe) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = dict(probe())
        except Exception as e:  # noqa: BLE001 - a failed probe is a result, not a crash of /readyz
            result = {"ok": False, "error": self.describe(e)}
        result["ms"] = round((time.perf_counter() - started) * 1000, 1)
        with self._lock:
            self._results[name] = (self.clock(), result)
            self._running.pop(name, None)
        return result

    async def get(self, probes: Mapping[str, Probe]) -> dict[str, dict[str, Any]]:
        """Every probe's result, in `probes`' order; a caller waits at most `timeout_s` for each (`timeouts[name]` for a
        probe with a wait of its own), all of them at the same time."""
        out: dict[str, dict[str, Any]] = {}
        waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        for name, probe in probes.items():
            found = self._lookup(name, probe)
            if isinstance(found, dict):
                out[name] = found
            else:
                waiting[name] = asyncio.wrap_future(found)
        if waiting:
            # asyncio.wait, not wait_for: a timeout must not cancel a run that other callers share.
            async def settle(name: str, fut: asyncio.Future[dict[str, Any]]) -> None:
                done, _ = await asyncio.wait([fut], timeout=self.timeouts.get(name, self.timeout_s))
                if fut in done and not fut.cancelled() and fut.exception() is None:
                    out[name] = fut.result()
                else:
                    out[name] = {"ok": False, "error": "timeout"}

            await asyncio.gather(*(settle(name, fut) for name, fut in waiting.items()))
        return {name: out[name] for name in probes}

    def clear(self) -> None:
        with self._lock:
            self._results.clear()


def ledger_error(e: BaseException) -> str:
    """A ledger probe's failure as a short code, never the message: `closed`, `read_only`, `locked`, `lock_timeout`, or
    the exception's class."""
    text = str(e).lower()
    if isinstance(e, TimeoutError):
        return "lock_timeout"
    if isinstance(e, sqlite3.ProgrammingError) and "closed" in text:
        return "closed"
    if isinstance(e, sqlite3.OperationalError):
        if "readonly" in text or "read-only" in text:
            return "read_only"
        if "locked" in text or "busy" in text:
            return "locked"
    return type(e).__name__


def list_models(base_url: str, api_key: str, transport: httpx.BaseTransport | None, timeout_s: float) -> dict[str, Any]:
    """`GET {base_url}/models` (an OpenAI-compatible model list; spends no tokens): `ok`, `status` and the model `ids`,
    or `models` naming what failed (`http_<status>`, `unreadable`). Connection errors and timeouts raise, for the cache
    to name by class. A fresh client per call: probes run once per TTL."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(transport=transport, timeout=timeout_s) as client:
        r = client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "models": f"http_{r.status_code}"}
    try:
        data = r.json().get("data")
    except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
        data = None
    if not isinstance(data, list):
        return {"ok": False, "status": 200, "models": "unreadable"}
    ids = [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]
    return {"ok": True, "status": 200, "models": "ok", "ids": ids}


def _breaker_state(breaker: Any) -> str | None:
    return None if breaker is None else str(breaker.state)


def _way_through(breaker: Any) -> str | None:
    """What a breaker does with the next call: CLOSED (it goes through), PROBE (it goes through as the breaker's probe:
    the breaker is open with its wait over, or half-open), or None (it is refused, or there is no breaker). Only a call
    moves an open breaker on, and a gateway out of rotation gets none, so readiness counts a due probe as a way through;
    else one teacher blip would keep the gateway out for good."""
    if breaker is None:
        return None
    state = breaker.state
    if state == CLOSED:
        return CLOSED
    if state == HALF_OPEN or breaker.retry_in() == 0:
        return PROBE
    return None


# Operational switches reported under `flags` (they never change the status): name -> how to read it from the runtime.
# Features that add a switch (a gateway budget, frozen releases, paused training) register it here.
FLAGS: dict[str, Callable[[Runtime], bool]] = {}


def _scout_budget_exhausted(rt: Runtime) -> bool:
    status = rt.guard.status()
    budget, spent = status.get("budget_usd"), status.get("spent_today_usd")
    return budget is not None and spent is not None and float(spent) >= float(budget)


FLAGS["scout_budget_exhausted"] = _scout_budget_exhausted


class Readiness:
    """`/readyz` for one Runtime: `report()` -> (HTTP status, body). Made by `Runtime.reset`, so a demo reset forgets
    the cached probes with the old ledger. `clock` is the runtime's (tests pass a fake one to age the cache)."""

    def __init__(self, rt: Runtime, cache: ProbeCache | None = None) -> None:
        self.rt = rt
        self.cache = cache or ProbeCache(
            clock=rt.clock,
            executors={"ledger": pool("ledger")},
            ttls={"student": STUDENT_PROBE_TTL_S},
            timeouts={"teacher": TEACHER_BUDGET_S},
        )
        self.busy_runs = 0  # ledger probes in a row that found the ledger busy (BUSY)

    # ----- probes (run on the probe pool) -----
    def _ledger(self) -> dict[str, Any]:
        try:
            self.rt.ledger.probe(PROBE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - reported as a code, never the message
            code = ledger_error(e)
            self.busy_runs = self.busy_runs + 1 if code in BUSY else 0
            return {"ok": False, "error": code, **({"busy_runs": self.busy_runs} if code in BUSY else {})}
        self.busy_runs = 0
        return {"ok": True}

    def _teacher_models(self) -> dict[str, Any]:
        """The teacher's model list. A timeout on the first attempt is tried once more after TEACHER_RETRY_DELAY_S; the
        gateway is reported degraded only when the second attempt fails too (its error is the one reported)."""
        s = self.rt.settings
        transport = self.rt.transports.get("teacher")
        try:
            return list_models(s.teacher_base, s.teacher_api_key, transport, TEACHER_ATTEMPT_TIMEOUT_S)
        except httpx.TimeoutException:
            time.sleep(TEACHER_RETRY_DELAY_S)
            return list_models(s.teacher_base, s.teacher_api_key, transport, TEACHER_ATTEMPT_TIMEOUT_S)

    def _student_models(self) -> dict[str, Any]:
        """The endpoint's model list, checked against the adapters of every routed version (live, canary, shadow). Each
        missing one is handed to the AdapterReloader (it reloads one adapter at most once a minute)."""
        rt = self.rt
        s = rt.settings
        result = list_models(s.student_base, s.student_api_key, rt.transports.get("student"), PROBE_TIMEOUT_S)
        if not result["ok"]:
            return result
        listed = set(result.pop("ids"))
        routed = rt.ledger.versions(["live", "canary", "shadow"])
        missing = [v for v in routed if v.adapter_uri not in listed]
        queued = [v.adapter_uri for v in missing if rt.reloader is not None and rt.reloader.request(v)]
        result.update(missing_adapters=[v.adapter_uri for v in missing], reload_queued=queued, ok=not missing)
        return result

    # ----- the report -----
    async def report(self) -> tuple[int, dict[str, Any]]:
        rt = self.rt
        live = rt.settings.mode != "mock"
        probes: dict[str, Probe] = {"ledger": self._ledger}
        if live:
            probes["teacher"] = self._teacher_models
            if rt.settings.student_base:
                probes["student"] = self._student_models
        results = await self.cache.get(probes)
        checks: dict[str, Any] = {"ledger": results["ledger"]}
        if live:
            checks["teacher"] = self._teacher(results["teacher"])
            checks["student"] = self._student(results.get("student"))
        warnings = rt.config_warnings
        config: dict[str, Any] = {"ok": True, "warnings": len(warnings)}
        if not live:  # a live gateway's warnings name settings; its operators read them in the log or the CLI
            config["messages"] = [w.message for w in warnings]
        checks["config"] = config
        status = self._status(checks)
        flags = {name: read(rt) for name, read in FLAGS.items()}
        body = {"status": status, "mode": rt.settings.mode, "version": __version__, "checks": checks, "flags": flags}
        return (503 if status == NOT_READY else 200), body

    def _teacher(self, models: Mapping[str, Any]) -> dict[str, Any]:
        """The teacher's check. `available`: some teacher can answer and the key is not refused. A teacher can answer
        when its breaker is closed, or when the breaker would let the next call through as its probe (`probe_due`) and
        its endpoint answers HTTP below 500 (the cached model list; a fallback teacher on an endpoint of its own has no
        such probe, so its breaker decides). `ok`: available, the primary's breaker closed, its model list read and the
        teacher model on it."""
        rt = self.rt
        breaker = _breaker_state(rt.ratchet.teacher_breaker)
        fallback = _breaker_state(rt.ratchet.fallback_breaker)
        out: dict[str, Any] = {"breaker": breaker}
        if fallback is not None:
            out["fallback_breaker"] = fallback
        out.update({k: v for k, v in models.items() if k != "ids"})
        out.pop("ok", None)
        status = models.get("status")
        refused = status in KEY_REFUSED
        answers = isinstance(status, int) and status < 500 and not refused
        listed = rt.settings.teacher_model in models["ids"] if "ids" in models else None
        out["model_listed"] = listed
        primary, backup = _way_through(rt.ratchet.teacher_breaker), _way_through(rt.ratchet.fallback_breaker)
        if breaker != CLOSED:
            out["probe_due"] = primary == PROBE
        own_endpoint = rt.ratchet.fallback_teacher is not rt.ratchet.teacher
        primary_ok = primary == CLOSED or (primary == PROBE and answers)
        backup_ok = backup == CLOSED or (backup == PROBE and (answers or own_endpoint))
        available = (primary_ok or backup_ok) and not refused
        out["available"] = available
        out["ok"] = available and breaker == CLOSED and bool(models.get("ok")) and listed is not False
        return out

    def _student(self, models: Mapping[str, Any] | None) -> dict[str, Any]:
        """The student's check: its breakers (the shadow's is reported, not counted) and, with an endpoint, its model
        list and the routed adapters on it. Without an endpoint the hosted fallback model answers for it."""
        rt = self.rt
        teachers = {"teacher", "fallback_teacher"}
        breakers = {name: snap["state"] for name, snap in rt.ratchet.health()["breakers"].items() if name not in teachers}
        serving_closed = all(state == CLOSED for name, state in breakers.items() if name.startswith("student"))
        out: dict[str, Any] = {"endpoint": bool(rt.settings.student_base), "breakers": breakers}
        if models is None:
            out["model"] = rt.settings.fallback_student_model
            out["ok"] = serving_closed
            return out
        out.update(models)
        out["ok"] = serving_closed and bool(models.get("ok"))
        return out

    def _status(self, checks: Mapping[str, Any]) -> str:
        """not_ready (503) when the ledger cannot be written (busy for BUSY_RUNS_NOT_READY probes in a row counts), or no
        teacher can answer (unless the student stands in, RATCHET_ON_TEACHER_FAILURE=serve_student, and is up); degraded
        (200) when something is down or busy that the gateway works around; else ready."""
        ledger = checks["ledger"]
        if not ledger["ok"]:
            runs = ledger.get("busy_runs", self.busy_runs + 1)  # a run still going past the wait is one more busy probe
            busy = ledger.get("error") in BUSY and runs < BUSY_RUNS_NOT_READY
            return DEGRADED if busy else NOT_READY
        teacher, student = checks.get("teacher"), checks.get("student")
        if teacher is not None and not teacher["available"]:
            stands_in = self.rt.ratchet.serve_student_on_failure and student is not None and student["ok"]
            return DEGRADED if stands_in else NOT_READY
        if (teacher is not None and not teacher["ok"]) or (student is not None and not student["ok"]):
            return DEGRADED
        return READY
