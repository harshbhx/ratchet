"""OpenAI-compatible gateway, dashboard API, landing page and the Scout multi-agent demo.

    RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000     # offline demo, no keys
    RATCHET_MODE=live NEBIUS_API_KEY=... RATCHET_API_KEYS=... uvicorn ratchet.gateway:app  # real Token Factory teacher

Point any OpenAI SDK at http://localhost:8000/v1. Choose the task with the
`X-Ratchet-Task` header, `metadata.ratchet_task` in the body or `model: "ratchet/<task>"` (in that order; also a model
alias, a task's match rules, a response_format schema's auto task or, opt-in, the system prompt:
`openai_compat.resolve_task`). The `model` field never picks an upstream model. Azure OpenAI clients use
`/openai/deployments/<task>/chat/completions`. With `RATCHET_API_KEYS` set, `/v1/*` needs `Authorization: Bearer <key>`
(or `X-API-Key`, or Azure's `api-key`); live mode refuses to start without them unless told to. Each route has an access
level (`ratchet/access.py`, ADR 0061): in live mode the dashboard API needs an admin or dashboard key unless
RATCHET_PUBLIC_DASHBOARD=1. Errors on `/v1/*` use OpenAI's envelope (`ratchet/openai_compat.py`); every other route
keeps FastAPI's `{"detail": ...}`.
Pages: `/` landing, `/scout` multi-agent demo, `/dashboard` Ratchet dashboard.
"""

from __future__ import annotations

import base64
import calendar
import contextlib
import functools
import itertools
import json
import logging
import math
import queue
import re
import struct
import threading
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import AsyncIterator, Callable, Container, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import anyio
import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import (
    __version__,
    certificate,
    feedback,
    hosted,
    idem,
    kpis,
    licenses,
    live,
    merkle,
    metrics,
    obs,
    privacy,
    proof,
    readiness,
    receipts,
    replay,
    schema_tasks,
    tasks,
    taskspec,
    teacher_watch,
    upstream,
    watch,
)
from . import access as acl
from . import admin as admin_ops
from . import bom as bomdoc
from . import config as cfg
from .auth import KeyLimits, KeyRing, Token, bearer, parse_key_limits
from .config import MAX_GPUS, MAX_USD_PER_GPU_HOUR, ConfigError, ConfigIssue, Settings
from .cost import cost_report, record_endpoint, record_simulated_day, stats_block
from .demo import DemoJanitor, reset_event
from .ledger import DAY_SECONDS, HOSTED_STATE, Ledger, NoTrainFull, Version
from .merkle import KeySet, LogTree
from .nightschool.orchestrate import NightReport, run_night
from .openai_compat import (
    EMBEDDINGS_TASK,
    INVALID_JSON,
    INVALID_REQUEST,
    INVALID_TYPE,
    INVALID_VALUE,
    MISSING,
    MODEL_TASK,
    RATCHET_MODEL,
    SERVICE_TIER,
    TASK_PARAMS,
    TEXT_MODALITY,
    APIError,
    ChatRequest,
    CompletionRequest,
    Limits,
    ModelRouting,
    check_embeddings_model,
    check_model,
    from_validation_errors,
    header_value,
    http_error,
    legacy_history,
    legacy_logprobs,
    model_not_found,
    normalize_messages,
    parse_body,
    parse_modalities,
    resolve_task,
    response_object,
    response_output,
    responses_usage,
    tool_params,
    unsupported_endpoint,
    usage_object,
    uses_tools,
    validate_chat_request,
    validate_completion_request,
    validate_embeddings_request,
    validate_responses_request,
)
from .providers import Embedder, GenParams, OpenAICompatProvider, approx_tokens
from .ratelimit import Decision, Limiter, RateLimiter, Refused, Slot
from .redact import STYLES as REDACT_STYLES
from .redact import Pseudonymizer, RedactionError, redact
from .release import ReleaseGate, ReleasePolicy
from .resilience import (
    CLIENT_CAUSED,
    RETRY_STATUSES,
    TEACHER_CIRCUIT_OPEN,
    TOOL_CALLS_UNSUPPORTED,
    CircuitOpen,
    as_upstream,
    describe_failure,
)
from .router import EVENT_ON_FAILURE, PASSTHROUGH_ROUTE, Choice, Ratchet, RequestMeta, Result, task_label
from .scout import record as scout_record
from .scout.agents import Call, Scout
from .scout.credits import CreditMeter, MeteredSearch
from .scout.guard import Guard
from .scout.judge import COOKIE, COOKIE_PATH, JUDGE_ATTEMPTS_PER_HOUR, AttemptLimiter, JudgeAccess
from .scout.prompts import EXAMPLE_QUESTIONS
from .scout.search import UNAVAILABLE_REASONS, MockSearch, SearchTool, TavilySearch
from .spend import BudgetExceeded, Caps, Hold, SpendMeter, Usage, money, month_bounds
from .upstream import UpstreamRecorder

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.html"
LANDING = HERE / "web" / "landing.html"
SCOUT_PAGE = HERE / "web" / "scout.html"
# The React app (web/, built into ratchet/web/dist). When it is missing (e.g. a source checkout
# without `npm run build`), the hand-written pages above are served instead.
DIST = HERE / "web" / "dist"
SPA_INDEX = DIST / "index.html"
WORKLOADS = ("tickets", "research", "mixed", "schema", "tools")  # sim.world.WORKLOADS, for /ratchet/demo/day
# Workloads the generic mock world answers (sim/generic.py), which RATCHET_MOCK_STRICT=1 turns off: refused before a day starts.
GENERIC_WORKLOADS = ("schema", "tools")
MAX_QUESTION_CHARS = 300
MAX_REDACT_CHARS = 5000
EVIDENCE_STATES = ("canary", "live")  # versions whose `/ratchet/stats` entry carries the release gate's evidence
# Versions that serve or once served traffic, so audits and the gate's evidence exist for them (the certificate endpoint).
SERVED_STATES = ("canary", "live", "rolled_back", "retired")


TRANSPORT_KEYS = ("teacher", "student", "tavily", "embeddings")
RETENTION_INTERVAL_S = 3600.0  # live mode applies the retention windows at most this often (FR-13.4)


class ReceiptTree(NamedTuple):
    """A sealed receipt's request ids (tree order) and tree, rebuilt from the ledger and checked against the root it
    signs (`_receipt_tree`). The leaves themselves are not kept: a proof reads its one row again (`Ledger.receipt_row`)."""

    ledger: Ledger
    sha256: str
    ids: list[int]
    tree: LogTree


# What `Runtime.student_serving` reports (FR-7.40): mock models; our adapter on the student endpoint; the hosted, untuned
# fallback (live mode without RATCHET_STUDENT_BASE); no student yet (the teacher answers everything).
STUDENT_SERVING_KINDS = ("mock", "endpoint", "hosted_fallback", "none")


class SnapshotCache:
    """One computed value served for `ttl_s` seconds on `clock` (ADR 0060): /ratchet/stats, which every dashboard tab
    polls, recomputes at most once per TTL however many read it. Single flight: while one caller recomputes, the others
    get the previous value at once instead of queueing behind it (only the very first callers wait, having nothing to
    serve). A failed recompute raises to its caller and keeps the previous value. `ttl_s` 0 computes on every call, as
    before."""

    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._lock = threading.Lock()  # held while computing
        self._value: Any = None
        self._at: float | None = None

    def _fresh(self, now: float) -> bool:
        return self._at is not None and 0 <= now - self._at < self.ttl_s

    def get(self, compute: Callable[[], Any]) -> Any:
        if self.ttl_s <= 0:
            return compute()
        if self._fresh(self.clock()):
            return self._value
        if self._at is not None:
            if not self._lock.acquire(blocking=False):
                return self._value  # another caller is recomputing: the previous value, now
        else:
            self._lock.acquire()  # nothing to serve yet: wait for the first value
        try:
            if self._fresh(self.clock()):  # computed while this caller waited for the lock
                return self._value
            value = compute()
            self._value, self._at = value, self.clock()
            return value
        finally:
            self._lock.release()


class Runtime:
    """Wires settings, ledger, providers and the release gate together.

    `transports` replaces the network under the live-mode providers ({"teacher": ..., "student": ..., "tavily": ...,
    "embeddings": ...}, each an `httpx.BaseTransport` such as `httpx.MockTransport`; the student's also carries adapter
    reloads), so live mode can run offline in tests, and `sleep` the teacher's waits between retries; mock mode has no
    HTTP providers and ignores both. A fallback teacher on another endpoint (RATCHET_TEACHER_FALLBACK_BASE) also goes
    over the teacher's transport, and so does the /v1/embeddings pass-through unless an "embeddings" transport is given.
    `clock` times the breakers, adapter reloads and demo outages (tests pass a fake one); `wall_clock` is the time of day
    Scout's guard, credit meter and judge sessions count UTC days by (tests only).

    `reloader` is the `live.AdapterReloader` in live mode with a student endpoint, else None.

    The configuration is checked first, before anything is opened (`config.check`, ADR 0070): every error at once in one
    ConfigError (also a LicenseError when a teacher is refused, INV-1). Warnings are kept in `config_warnings` and logged;
    RATCHET_STRICT_CONFIG=1 makes them fatal too."""

    def __init__(
        self,
        settings: Settings | None = None,
        transports: dict[str, httpx.BaseTransport] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        unknown = set(transports or {}) - set(TRANSPORT_KEYS)
        if unknown:
            raise ValueError(f"unknown transport keys {sorted(unknown)}; use {list(TRANSPORT_KEYS)}")
        parsed: list[ConfigIssue] = []
        if settings is None:
            settings, parsed = cfg.load()  # variables that do not parse are listed with the rest, not raised one by one
        self.settings = settings
        self.config_warnings: list[ConfigIssue] = self._check_config(parsed)
        self.transports = dict(transports or {})
        self.sleep = sleep
        self.clock = clock
        self.wall_clock = wall_clock
        self.tasks_lock = threading.Lock()  # admin writes to the registry and the tasks table, and resets, one at a time
        self.keysets_lock = threading.Lock()  # the committed-set cache (`_keyset`)
        self.replay_lock = threading.Lock()  # one ledger replay at a time (`_replaying`)
        self.receipts_lock = threading.Lock()  # the receipt-tree cache (`_receipt_tree`)
        # Retention runs on its own thread, at most every RETENTION_INTERVAL_S on `clock` (`maybe_purge`, FR-13.4).
        self.purge_lock = threading.Lock()
        # The last real upstream call per role, content-free (FR-11.31); made once, so a demo reset keeps it. A fallback
        # teacher on the teacher's own client is told apart by its model id.
        alias = {}
        if settings.teacher_fallback_model and settings.teacher_fallback_model != settings.teacher_model:
            alias[("teacher", settings.teacher_fallback_model)] = "fallback_teacher"
        self.upstream = UpstreamRecorder(clock=wall_clock, aliases=alias)
        self.demo_limiter = RateLimiter(clock=clock)  # the shared simulation's per-IP demo actions (FR-11.30)
        self.purge_thread: threading.Thread | None = None
        self._last_purge: float | None = None
        self.reset()
        for w in self.config_warnings:  # once per start, after reset() set up logging; names settings, never values
            obs.log(logging.WARNING, "config_warning", var=w.var, detail=w.message)

    def _check_config(self, parsed: list[ConfigIssue]) -> list[ConfigIssue]:
        """Raise one ConfigError listing every configuration error (and every warning under RATCHET_STRICT_CONFIG=1);
        return the warnings."""
        issues = [*parsed, *cfg.check(self.settings)]
        errors = [i for i in issues if i.level == cfg.ERROR]
        warnings = [i for i in issues if i.level != cfg.ERROR]
        strict = self.settings.strict_config is True
        if errors or (strict and warnings):
            raise ConfigError.of([*errors, *warnings] if strict else errors)
        return warnings

    def reset(self) -> None:
        s = self.settings
        # The license registry (RATCHET_TEACHER_LICENSES, ADR 0090): a bad file stops startup here, before anything is
        # opened; `Ratchet.__init__` installs the same file again.
        licenses.install(s.teacher_licenses_file)
        self.links = s.links()  # a bad RATCHET_LIVE_URL or RATCHET_SIM_URL stops startup here (FR-11.30)
        self.demo_policy = s.demo_policy()  # a bad RATCHET_DEMO_* stops startup here, in either mode
        self._set_access(s)  # first, so a live gateway without keys stops before it opens anything (ADR 0061)
        self._set_limits(s)  # a bad limit, or RATCHET_KEY_LIMITS naming no key, stops startup here (ADR 0063)
        obs.configure(*s.log_settings())  # a bad RATCHET_LOG_FORMAT or RATCHET_LOG_LEVEL stops startup here (ADR 0062)
        self.readiness = readiness.Readiness(self)  # /readyz's probes and their cache; a demo reset starts it afresh
        self.failure_rows = FailureThrottle(clock=self.clock)
        self.cost_policy = s.cost_policy()  # a bad RATCHET_STUDENT_COST_MODEL or GPU rate stops startup here (ADR 0023)
        cert = s.cert_policy()  # a bad RATCHET_CALIBRATION, _CERT_TARGET or _CERT_DELTA stops startup here (ADR 0026)
        self.retention = s.retention()  # a bad retention window or RATCHET_ERASURE_SHARED stops startup here (ADR 0081)
        # RATCHET_MOCK_TEACHER_REASONING_MEAN out of range stops startup in either mode, as documented; only the mock
        # teacher reads it.
        mean = s.checked_mock_reasoning_mean()
        scout = s.scout_policy()  # a bad judge or Tavily credit setting stops startup here, never echoing the code (ADR 0120)
        self.credits: CreditMeter | None = None
        # The hot path (ADR 0060); a bad RATCHET_DB_SYNC, RATCHET_TICK_INTERVAL_S or RATCHET_STATS_TTL_S stops startup here.
        sync = s.ledger_sync()
        self.tick_interval_s = s.tick_interval()
        self.stats_cache = SnapshotCache(s.stats_ttl(), clock=self.clock)
        self._last_tick: float | None = None
        min_task_n = s.min_task_golden_rows()  # a bad RATCHET_MIN_TASK_GOLDEN stops startup here (FR-14.1)
        self.kpi_window = s.kpi_window()  # a bad RATCHET_KPI_WINDOW_DAYS stops startup here (FR-7.9)
        if s.mode == "mock":
            from sim.mock_models import DEFAULT_REASONING_CV, MockFleet, MockTeacher
            from sim.world import make_stream

            self.ledger = Ledger(":memory:", s.golden_fraction)
            # RATCHET_MOCK_STRICT=1: the mock models answer only the demo's own requests (the 400 below otherwise).
            self.fleet = MockFleet(
                clock=self.clock,
                policy=s.gen_policy(),
                calibration=cert.calibration,
                cert_target=cert.target,
                cert_delta=cert.delta,
                min_task_n=min_task_n,
                strict=s.mock_strict,
                per_task=s.per_task_calibration,
            )
            self.reloader: live.AdapterReloader | None = None
            self.student_http: httpx.Client | None = None  # the client that loads adapters into vLLM (live only)
            self.stream = make_stream("tickets", seed=7, drift_day=5)
            self.streams = {"tickets": self.stream, **{w: make_stream(w, seed=7, drift_day=5) for w in WORKLOADS[1:]}}
            # RATCHET_MOCK_TEACHER_REASONING_MEAN (checked above): the mock teacher's reasoning varies around that mean, so
            # the savings receipts measure and correct the formula's 300-token assumption (FR-15.5, simulation).
            cv = DEFAULT_REASONING_CV if mean is not None else 0.0
            teacher = MockTeacher(s.teacher_reasoning_tokens, reasoning_mean=mean, reasoning_cv=cv, strict=s.mock_strict)
            resolver = self.fleet.resolve
            s.audit_rate = max(s.audit_rate, 0.10)
            # RATCHET_<FIELD> works here too (RATCHET_SEQUENTIAL=1 shows the anytime-valid gate); min_audits is 15 unless set.
            policy = ReleasePolicy.from_env(defaults={"min_audits": 15})
            self.search: SearchTool | None = MockSearch()
            # No budget, no run cap and no ledger rows: the judge reserve holds nothing, but the code and cookie work.
            self.guard = Guard(
                None, 10 * s.scout_runs_per_hour, clock=self.wall_clock, judge_runs_per_day=scout.judge_runs_per_day
            )
            fallback_teacher: OpenAICompatProvider | None = None  # the mock teacher plays any fallback model too
        else:
            self.ledger = Ledger(s.db_path, s.golden_fraction, auto_day=True, sync=sync)
            # Timeouts and retries per role (ADR 0014): the teacher is retried within a deadline; the student never is,
            # because the teacher is its retry. A bad setting raises here, at startup.
            reasoning_field = s.reasoning_policy().field  # where Token Factory-bound calls carry the kwargs (ADR 0130)
            teacher = OpenAICompatProvider(
                s.teacher_base,
                s.teacher_api_key,
                timeout=s.teacher_timeout(),
                retry=s.teacher_retry(),
                transport=self.transports.get("teacher"),
                sleep=self.sleep,
                reasoning_field=reasoning_field,
                observe=self.upstream.bind("teacher", upstream.host_of(s.teacher_base)),
            )
            # The fallback teacher (ADR 0019) has the teacher's key, timeouts and retries. On the teacher's endpoint it
            # shares the teacher's client (Ratchet's default); on another one it gets its own, on the teacher transport.
            fallback_teacher = None
            fallback_base = s.teacher_fallback_base or s.teacher_base
            if s.teacher_fallback_model and fallback_base.rstrip("/") != teacher.base_url:
                fallback_teacher = OpenAICompatProvider(
                    fallback_base,
                    s.teacher_api_key,
                    timeout=s.teacher_timeout(),
                    retry=s.teacher_retry(),
                    transport=self.transports.get("teacher"),
                    sleep=self.sleep,
                    reasoning_field=reasoning_field,
                    observe=self.upstream.bind("fallback_teacher", upstream.host_of(fallback_base)),
                )
            self.reloader = None
            self.student_http = None
            # The hosted, untuned model on Token Factory, still with the student's timeouts: the student while the
            # endpoint is off, and the hosted student's row (FR-12.33, ADR 0146) always, which has no adapter to load.
            fallback = OpenAICompatProvider(
                s.teacher_base,
                s.teacher_api_key,
                timeout=s.student_timeout(),
                transport=self.transports.get("teacher"),
                reasoning_field=reasoning_field,
                observe=self.upstream.bind("student", upstream.host_of(s.teacher_base)),
            )
            if s.student_base:
                student_transport = self.transports.get("student")
                student = OpenAICompatProvider(
                    s.student_base,
                    s.student_api_key,
                    timeout=s.student_timeout(),
                    transport=student_transport,
                    observe=self.upstream.bind("student", "student_endpoint"),
                )
                # vLLM forgets runtime-loaded adapters when it restarts; a 404 for one queues its reload (ADR 0015).
                http = httpx.Client(transport=student_transport) if student_transport is not None else None
                self.student_http = http  # also an admin promote's `load_adapter` (ADR 0064)
                self.reloader = live.adapter_reloader(s, self.ledger, http, clock=self.clock)

                def resolver(v: Version):
                    if v.state == HOSTED_STATE:
                        return fallback, s.fallback_student_model
                    return student, v.adapter_uri  # vLLM serves each LoRA adapter under its own name
            else:  # endpoint offline: the hosted, untuned fallback answers for every version

                def resolver(v: Version):
                    return fallback, s.fallback_student_model

            policy = ReleasePolicy.from_env()
            self.search = None
            if s.tavily_api_key:
                tavily = self.transports.get("tavily")
                client = httpx.Client(timeout=30.0, transport=tavily) if tavily is not None else None
                self.search = TavilySearch(s.tavily_api_key, client=client, observe=self.upstream.bind("tavily", "tavily"))
                self.credits = CreditMeter(
                    scout.monthly_credits,
                    scout.judge_credits,
                    scout.cycle_day,
                    scout.pace_until,
                    clock=self.wall_clock,
                    ledger=self.ledger,
                )
            # The daily caps (INV-11) and the judges' share, counted in the ledger and restored here, so a restart or an
            # update never resets them (ADR 0120). Restoring writes no event.
            self.guard = Guard(
                s.scout_daily_budget_usd,
                s.scout_runs_per_hour,
                s.scout_max_runs_per_day,
                clock=self.wall_clock,
                judge_runs_per_day=scout.judge_runs_per_day,
                judge_budget_usd=scout.judge_budget_usd,
                ledger=self.ledger,
            )
            self.guard.restore()
            if self.credits is not None:
                self.credits.restore()
            if s.hosted_student:
                self._ensure_hosted()
        self.judge = JudgeAccess(scout.judge_code, scout.judge_session_s, clock=self.wall_clock) if scout.judge_enabled else None
        self.judge_attempts = AttemptLimiter(JUDGE_ATTEMPTS_PER_HOUR, clock=self.wall_clock)
        self.search_lock = threading.Lock()
        self.live_check_lock = threading.Lock()
        self.live_check_cache: tuple[float, dict[str, Any] | None] = (0.0, None)
        self.search_errors = 0  # failed searches since the last reset (credit refusals are not failures)
        self.search_errors_today: tuple[str, int] = ("", 0)  # (UTC day, failures on it)
        self.search_last_error: str | None = None  # the most recent search's failure, None after a search that worked
        self.ledger.add_event_listener(obs.mirror_event)  # every release-log event also as a log line (ADR 0062)
        # End users are hashed with this deployment's key (FR-13.2): RATCHET_END_USER_KEY, or one kept in the ledger.
        self.end_users = privacy.end_users(self.ledger, s.end_user_key)
        # No-train registrations scoped to a key name that is gone match nothing: say so (an event, once per change).
        privacy.check_no_train_scopes(self.ledger, self.known_clients())
        # Retries (ADR 0071): answers kept by Idempotency-Key, and the window in which the same client's repeat of a
        # stored example counts once. Both in memory, both new on a demo reset; a bad setting stops startup here.
        self.idem = idem.IdemCache(*s.idempotency(), clock=self.clock)
        window = s.seen_dedup()
        self.ledger.seen_window = idem.SeenWindow(window, clock=self.clock) if window > 0 else None
        self.ledger.flags()  # this ledger's runtime pins replace any an earlier ledger (a demo reset) published (ADR 0064)
        # The registry: the built-in tasks, RATCHET_TASKS_FILE's (a bad file stops startup here, every error listed) and
        # the API tasks this ledger stored (a fresh mock ledger has none, so a demo reset clears them).
        with self.tasks_lock:
            skipped = taskspec.install(self.ledger, s.tasks_file, self.known_clients(), fixtures_dir=s.fixtures_dir)
        for line in skipped:
            self.ledger.event("task_skipped", line)
        self.meter = self._spend_meter()  # today's /v1 spend, read once from the ledger (ADR 0063)
        self.signer = bomdoc.Signer(s.bom_signing_key) if s.bom_signing_key else None
        # Savings receipts (FR-15.5, ADR 0029), signed with the BOM key; a bad RATCHET_TAKE_RATE or
        # RATCHET_RECEIPT_MIN_AUDITS stops startup here, because a sealed receipt can never be corrected (INV-17).
        label = replay.SIMULATION if s.mode == "mock" else "live traffic"
        self.receipt_config = receipts.SealConfig.from_settings(s, self.signer, label)
        self.limits: Limits = s.limits()  # a bad RATCHET_MAX_N stops startup here
        # How the model field and the system prompt pick a task (ADR 0048); a bad setting stops startup here.
        self.routing: ModelRouting = s.model_routing()
        # The /v1/embeddings pass-through (FR-1.51, ADR 0050): None when RATCHET_EMBEDDINGS_BASE is off.
        self.embedder: Embedder | None = self._embedder()
        cap = s.auto_task_cap()  # the shared mock demo holds fewer, as it does API tasks; a reset clears them
        self.max_auto_tasks = min(cap, MOCK_MAX_AUTO_TASKS) if s.mode == "mock" else cap
        self._auto_cap_logged = False
        self.modalities = parse_modalities(s.teacher_modalities)
        self.gateway_errors = 0  # unhandled exceptions since the last reset (see _unhandled_error)
        # Committed sets the proof endpoints read (`_keyset`), by (version, split), least recently used last. A reset
        # makes a new ledger whose version ids start again, so the cache starts again too.
        self.keysets: OrderedDict[tuple[int, str], tuple[Ledger, KeySet]] = OrderedDict()
        self.integrity_reported: set[tuple[int, str]] = set()
        # Receipt trees the proof and leaves endpoints read (`_receipt_tree`), by (period, client), as for the key sets.
        self.receipt_trees: OrderedDict[tuple[int, str], ReceiptTree] = OrderedDict()
        self.receipt_integrity_reported: set[tuple[int, str]] = set()
        reload = self.reloader.request if self.reloader is not None else None
        # Off-path checks (ADR 0072): how long shutdown waits for queued audits and shadows (a bad RATCHET_SIDECAR_* value
        # stops startup here). A demo reset gives up on the old cascade's queued jobs, whose ledger is being replaced.
        self.sidecar_drain_s = s.sidecar()[2]
        old: Ratchet | None = getattr(self, "ratchet", None)
        if old is not None:
            old.close(0.0)
        self.ratchet = Ratchet(
            s,
            self.ledger,
            teacher,
            resolver,
            seed=7,
            clock=self.clock,
            on_adapter_missing=reload,
            fallback_teacher=fallback_teacher,
            tasks_lock=self.tasks_lock,  # auto tasks (ADR 0042) and admin writes never interleave
        )
        # An off-path audit's or shadow's spend counts against the budgets when its job ends (ADR 0063, ADR 0072).
        self.ratchet.on_offpath_spend = lambda client, usd: self.meter.add(client, usd, requests=0)
        self.gate = ReleaseGate(self.ledger, policy)
        self.gate_lock = threading.Lock()
        # The drift audit boost (FR-6.40): the router reads the gate's dict, which stays empty unless
        # RATCHET_DRIFT_AUDIT_BOOST is above 1. Then the drift monitor replays the ledger now, not on the first request.
        self.ratchet.audit_boost = self.gate.audit_boost
        self.gate.check_drift()
        if s.mode == "mock" and s.mock_warmup_days > 0:
            self.warm_up(s.mock_warmup_days, s.mock_warmup_requests)

    def shutdown(self) -> dict[str, Any]:
        """The gateway's shutdown (the app's lifespan, ADR 0072): wait at most RATCHET_SIDECAR_DRAIN_S for the off-path
        audits and shadows still queued, then give up on the rest, whose rows stay pending and are ignored by the release
        gate. Returns the sidecar's report."""
        return self.ratchet.close(self.sidecar_drain_s)

    def _set_access(self, s: Settings) -> None:
        """The key rings, the metrics token and who may read what (ADR 0061, ADR 0062). A bad key spec or switch, a key in
        two rings, a metrics token that is short or also a key, or live mode without RATCHET_API_KEYS (unless
        RATCHET_ALLOW_UNAUTHENTICATED=1) raises ValueError, so the gateway stops at startup."""
        self.access = s.access()
        self.keys = KeyRing(s.api_keys)
        # Admin keys for management writes (ADR 0022) and read-only dashboard keys (ADR 0061). Each key has one role: an
        # API client's key is neither, and a dashboard key is not an admin's, so it can never write.
        self.admin_keys = KeyRing(s.admin_keys, env="RATCHET_ADMIN_KEYS")
        self.dashboard_keys = KeyRing(s.dashboard_keys, env="RATCHET_DASHBOARD_KEYS")
        for ring, other, fix in (
            (self.admin_keys, self.keys, "an API key; give admins their own keys"),
            (self.dashboard_keys, self.keys, "an API key; give dashboards their own keys"),
            (self.dashboard_keys, self.admin_keys, "an admin key; give dashboards their own, read-only keys"),
        ):
            both = ring.shared(other)
            if both:
                raise ValueError(f"{ring.env}: the key for {both[0]!r} is also {fix}")
        if s.mode != "mock" and not self.keys.enabled:
            if not self.access.allow_unauthenticated:
                raise ValueError(
                    "RATCHET_API_KEYS is empty: in live mode /v1/* would answer anyone, on your teacher credits. Set "
                    "RATCHET_API_KEYS, or RATCHET_ALLOW_UNAUTHENTICATED=1 when something in front of the gateway (the demo "
                    "VM's Caddy guard: RATCHET_API_GUARD=guarded and a RATCHET_ADMIN_TOKEN of 32+ characters) checks every "
                    "caller"
                )
            problem = s.guard_problem()
            if problem is not None:
                raise ValueError(problem)
        csp = {"Content-Security-Policy": self.access.csp} if self.access.csp else {}
        self.security_headers: dict[str, str] = {**SECURITY_HEADERS, **csp}
        # A Prometheus scraper's bearer token for GET /metrics (ADR 0062): a role of its own, so never one of the keys.
        self.metrics_token = Token(s.metrics_token, env="RATCHET_METRICS_TOKEN")
        token = s.metrics_token.strip() if isinstance(s.metrics_token, str) else ""
        for ring in (self.keys, self.admin_keys, self.dashboard_keys):
            name = ring.identify(token) if token else None
            if name is not None:
                raise ValueError(
                    f"RATCHET_METRICS_TOKEN is also the key for {name!r} in {ring.env}; give the scraper its own token"
                )
        metrics.set_clients(self.keys.names)  # a metric's client label is a key name, `none` or `other`

    def _set_limits(self, s: Settings) -> None:
        """The spend and rate limits (ADR 0063): each API key's `KeyLimits` (RATCHET_KEY_LIMITS over the
        RATCHET_API_KEY_* defaults) and the limiter. The per-IP rate covers only a gateway without API keys, where it is
        the one thing that tells callers apart. A bad value raises ValueError, so the gateway stops at startup."""
        self.limit_policy = lp = s.limit_policy()
        self.key_limits: dict[str, KeyLimits] = parse_key_limits(lp.key_limits, self.keys.names, lp.key_defaults)
        self.limiter = Limiter(lp.max_inflight, 0 if self.keys.enabled else lp.ip_rpm, clock=self.clock)

    def _spend_meter(self) -> SpendMeter:
        """The spend meter, with the US dollar caps its budget alerts measure against."""
        lp = self.limit_policy
        caps = Caps(
            daily_usd={name: k.daily_usd for name, k in self.key_limits.items() if k.daily_usd > 0},
            monthly_usd={name: k.monthly_usd for name, k in self.key_limits.items() if k.monthly_usd > 0},
            gateway_daily_usd=lp.daily_budget_usd,
            alert_pct=lp.alert_pct,
        )
        return SpendMeter(self.ledger, caps)

    def limits_for(self, client: str | None) -> KeyLimits | None:
        """The API key's limits, or None for a request without a key."""
        return self.key_limits.get(client) if client is not None else None

    def _embedder(self) -> Embedder | None:
        """The model /v1/embeddings passes requests to, or None when RATCHET_EMBEDDINGS_BASE is off: the mock embedder
        in mock mode, else an OpenAI-compatible client with the teacher's timeouts and retries (an embeddings call is
        safe to repeat). Its key is RATCHET_EMBEDDINGS_KEY, else NEBIUS_API_KEY only on the teacher's own endpoint: another
        provider never gets the Token Factory key. A bad setting, or a model alias spelled like the embeddings model (one
        id, two meanings in /v1/models), stops startup."""
        s = self.settings
        base = s.embeddings_endpoint()
        if base is None:
            return None
        if s.embeddings_model in self.routing.aliases:
            model = s.embeddings_model
            raise ValueError(f"RATCHET_MODEL_ALIASES: {model!r} is RATCHET_EMBEDDINGS_MODEL; give the alias another id")
        if s.mode == "mock":
            from sim.mock_models import MockEmbedder

            return MockEmbedder()
        teachers = base.rstrip("/") == s.teacher_base.rstrip("/")
        return OpenAICompatProvider(
            base,
            s.embeddings_api_key or (s.teacher_api_key if teachers else ""),
            timeout=s.teacher_timeout(),
            retry=s.teacher_retry(),
            transport=self.transports.get("embeddings", self.transports.get("teacher")),
            sleep=self.sleep,
        )

    def _ensure_hosted(self) -> None:
        """RATCHET_HOSTED_STUDENT in live mode (FR-12.33, ADR 0146): the hosted student's version row, kept in step with
        `ratchet/hosted.py`'s records (`Ledger.ensure_hosted`), and a `hosted` event when it was made or changed. A row
        whose records serve no task holds every task back, so the teacher answers as on a cold start (route
        `task_disabled`), and the event says why."""
        s = self.settings
        ev = hosted.eval_record()
        v, change = self.ledger.ensure_hosted(s.fallback_student_model, ev)
        if change is None:
            return
        served = hosted.served_tasks(ev)
        if served:
            what = ", ".join(f"{name} at confidence {thr:.4f}" for name, thr in served.items())
            detail = f"answers {what} while no version is live; the teacher answers the rest"
        else:
            why = "; ".join(f"{name}: {rec.get('disabled')}" for name, rec in sorted(ev["tasks"].items())) or "no records"
            detail = f"answers no task (none met its bar: {why}); the teacher answers every request"
        self.ledger.event("hosted", f"v{v.id} {change}: the hosted student {s.fallback_student_model} (untuned) {detail}")

    def known_clients(self) -> list[str] | None:
        """The API key names a task's `clients` may use, or None when keys are off (nothing to check them against)."""
        return self.keys.names if self.keys.enabled else None

    def student_serving(self) -> dict[str, Any]:
        """Which model answers a student call right now, so the pages name it (FR-7.40): `kind` (STUDENT_SERVING_KINDS),
        the served `model` id, the `version` that serves (the live one, or the first student's canary on a cold start),
        the confidence `threshold` it is held to, and whether a certificate covers it. In live mode without
        RATCHET_STUDENT_BASE the resolver sends every version's calls to the untuned hosted model: it is held to the
        global threshold (`Ratchet.threshold`) and never `certified`, because the version's certificate was computed
        for its adapter. Read-only: one query of the versions table. The one place that decides the kind, so a resolver
        that switches between the endpoint and the fallback (scheduled endpoint windows) changes it here."""
        s = self.settings
        serving = self.ledger.versions(["live", "canary"])
        live_now = [v for v in serving if v.state == "live"]
        v = live_now[-1] if live_now else None
        # With no live version the hosted student's row (FR-12.33) takes most requests, when it answers some task.
        stand_in = self.ledger.by_state(HOSTED_STATE) if v is None and s.hosted_student and s.mode == "live" else None
        tasks_served = hosted.served_tasks(stand_in.eval) if stand_in is not None else {}
        if tasks_served:
            v = stand_in
        elif v is None and serving:
            v = serving[-1]
        if s.mode == "mock":
            kind = "mock"  # the mock fleet serves each version's own adapter, so the rest reads as for an endpoint
        elif v is None:
            kind = "none"
        elif s.student_base and v.state != HOSTED_STATE:
            kind = "endpoint"
        else:
            kind = "hosted_fallback"
        if v is None:
            return {"kind": kind, "model": None, "version": None, "threshold": None, "certified": False}
        is_hosted = kind == "hosted_fallback"
        model = s.fallback_student_model if is_hosted else v.adapter_uri
        certified = not is_hosted and certificate.status(certificate.section(v.eval), None, None) == "certified"
        out = {
            "kind": kind,
            "model": model,
            "version": v.id,
            "threshold": self.ratchet.threshold(v, model),
            "certified": certified,
        }
        if v.state == HOSTED_STATE:  # each task's own threshold; `threshold` only when there is one
            out["tasks"] = tasks_served
            out["threshold"] = next(iter(tasks_served.values())) if len(tasks_served) == 1 else None
        return out

    # ----- auto tasks (RATCHET_AUTO_TASK=system_prompt, ADR 0048) -----
    def auto_task(self, name: str) -> tasks.Task | None:
        """The task for an auto task name (`openai_compat.system_prompt_task`): the task registered under it, else a new
        auto task (whole answers compared, never pinned) for `keep_auto_task` to register once the request is admitted,
        or None when there is no room for one (`_auto_room`; the request then gets `default`, and the caller reports it
        with `auto_cap_reached` once the request is admitted). Lookups only."""
        current = tasks.lookup(name)
        if current is not None:
            return current
        if not self._auto_room():
            return None
        return tasks.Task(name, source="auto", description=AUTO_DESCRIPTION)

    def keep_auto_task(self, task: tasks.Task) -> tasks.Task:
        """Register a new auto task and store it in the ledger's `tasks` table (so Night School and a restart find it),
        under the registry lock: the task to serve the request with. That is the task already registered under the name
        when another request was first, or `default` when the cap (or the registry's own) filled up meanwhile."""
        with self.tasks_lock:
            current = tasks.lookup(task.name)
            if current is not None:
                return current
            if not self._auto_room():
                self.auto_cap_reached()
                return tasks.get("default")
            try:
                tasks.register(task)
            except (tasks.RegistryFull, tasks.TaskConflict):
                self.auto_cap_reached()
                return tasks.get("default")
            try:
                self.ledger.save_task(task.name, taskspec.document(task), task.spec_hash, "auto", AUTO_AUTHOR)
            except BaseException:
                tasks.restore(task.name, None)  # the registry and the table never disagree
                raise
            self.ledger.event("task_registered", f"{task.name} (spec {task.spec_hash}) auto: a new system prompt")
        return task

    def _auto_room(self) -> bool:
        """Whether one more auto task may be made: fewer than the cap (RATCHET_MAX_AUTO_TASKS, at most MOCK_MAX_AUTO_TASKS
        in mock mode), and the registry keeps AUTO_FREE_SLOTS slots free after it, so request traffic can never fill the
        registry and lock the admin API out (auto tasks are stored and come back at restart)."""
        return _auto_count() < self.max_auto_tasks and len(tasks.REGISTRY) < tasks.MAX_TASKS - AUTO_FREE_SLOTS

    def auto_cap_reached(self) -> None:
        """Log once per ledger (and look once per process) that no more auto tasks can be made. Called only for an
        admitted request, and never when RATCHET_MAX_AUTO_TASKS is 0 (no auto task is ever made, so nothing filled up)."""
        if self._auto_cap_logged or self.max_auto_tasks == 0:
            return
        self._auto_cap_logged = True
        prefix = f"RATCHET_MAX_AUTO_TASKS={self.max_auto_tasks}:"
        room = f"the registry keeps its last {AUTO_FREE_SLOTS} slots for other tasks"
        detail = f"{prefix} no more auto tasks (the cap is reached, or {room}); requests with a new system prompt get default"
        self.ledger.event_once("auto_task_cap", prefix, detail)

    # ----- mock-mode simulation helpers -----
    def simulate_day(self, requests: int, workload: str = "tickets") -> NightReport:
        """One simulated day of traffic, then one night of Night School (mock mode only), with the day's simulated
        compute (the endpoint's RATCHET_MOCK_ENDPOINT_HOURS and the night's training job) on the synthetic clock."""
        from sim.world import night_task

        stream = self.streams[workload]
        self.ledger.day += 1
        ticks = max(1, requests // 10)
        for i in range(requests):
            item = stream.sample(self.ledger.day)
            self.ratchet.handle(item.messages(), item.task, params=item.params)  # a schema or tools; None for tickets
            if (i + 1) % ticks == 0:
                self.tick(force=True)  # simulated time: never throttled by the wall clock
        rep = run_night(
            self.ledger,
            self.fleet,
            self.fleet,
            self.gate,
            self.settings.student_base_model,
            task=night_task(workload),
            signer=self.signer,
            redact_pii=self.settings.redact_pii,
            store_examples=self.settings.store_examples,
            relabel=self.relabel(),
        )
        trained = self.ledger.version(rep.version) if rep.trained and rep.version is not None else None
        record_simulated_day(self.ledger, self.cost_policy, self.ledger.day, trained)
        # Every earlier day is over now (requests log on the current day), so its receipts are sealed (FR-15.5). Reads
        # the ledger and writes receipts only: no random draw, so no other number moves.
        receipts.seal_due(self.ledger, self.receipt_config)
        return rep

    def relabel(self) -> teacher_watch.Relabel:
        """How Night School re-asks the teacher for the watch's probes and adjudications (FR-5.40, FR-5.41): the
        cascade's own teacher, asked as an audit asks."""
        s = self.settings
        probes, adjudicate = s.teacher_watch_limits()
        return teacher_watch.relabel_with(
            self.ratchet.teacher,
            s.teacher_model,
            self.ratchet.gen,
            probes,
            adjudicate,
            redact_pii=s.redact_pii,
            price=s.price(s.teacher_model),
        )

    def warm_up(self, days: int, requests: int) -> None:
        for _ in range(days):
            self.simulate_day(requests, "mixed")
        self.ledger.event("warmup", f"demo warm-up: {days} simulated days of mixed traffic ({requests} requests/day)")

    def tick(self, force: bool = False) -> bool:
        """Run the release gate's tick; returns whether it ran. With RATCHET_TICK_INTERVAL_S above 0 (live mode: 5 s), a
        tick less than that after the previous one is skipped, and so is one while another thread is ticking, so the
        request path pays for a tick at most once per interval and never waits for one (ADR 0060). A rollback can wait
        up to the interval. `force`, or an interval of 0 (mock mode), runs every call, waiting for a running tick, as
        before. A tick that ran then starts the retention purge when one is due (`maybe_purge`, FR-13.4)."""
        if force or self.tick_interval_s <= 0:
            with self.gate_lock:
                self._last_tick = self.clock()
                self.gate.tick()
            self.maybe_purge()
            return True
        if self._recent(self.clock()) or not self.gate_lock.acquire(blocking=False):
            return False
        try:
            now = self.clock()
            if self._recent(now):  # another thread ticked between the check and the lock
                return False
            self._last_tick = now  # before the tick, so a failing tick is not retried on every request
            self.gate.tick()
        finally:
            self.gate_lock.release()
        self.maybe_purge()
        return True

    def maybe_purge(self) -> threading.Thread | None:
        """In live mode with a retention window set, apply the windows (`privacy.apply_retention`, FR-13.4) at most once
        every RETENTION_INTERVAL_S on `clock`, starting with the first tick, on a background thread, so no request waits
        for a purge. Returns the thread when one started. A failure writes a `purge_failed` event naming the exception's
        class, and the next interval tries again."""
        if self.settings.mode != "live" or not self.retention.enabled:
            return None
        with self.purge_lock:
            now = self.clock()
            if self.purge_thread is not None and self.purge_thread.is_alive():
                return None
            if self._last_purge is not None and now - self._last_purge < RETENTION_INTERVAL_S:
                return None
            self._last_purge = now
            thread = threading.Thread(target=self._purge, name="ratchet-retention", daemon=True)
            self.purge_thread = thread
            thread.start()
            return thread

    def _purge(self) -> None:
        try:
            # Every day that has ended gets its savings receipts first (FR-15.5), from all of its request rows: a receipt
            # is never sealed from a day retention folded (`receipts._seal_period`).
            receipts.seal_due(self.ledger, self.receipt_config)
            # Never fold the rows of a day that ended inside the sealing grace: it is sealed from all of them first.
            privacy.apply_retention(self.ledger, self.retention, fold_before=receipts.fold_floor(self.ledger))
        except Exception as e:  # noqa: BLE001 - a background job: report it and try again next interval
            try:
                self.ledger.event("purge_failed", f"retention: {describe_failure(e)}")
            except Exception:  # noqa: BLE001 - the ledger may be what failed
                pass

    def _recent(self, now: float) -> bool:
        last = self._last_tick
        return last is not None and 0 <= now - last < self.tick_interval_s

    def scout(self, pace: float = 0.0, judge: bool = False, run_id: str = "") -> Scout:
        """`pace` > 0 (mock streams only) sleeps for that share of each call's simulated latency. With a credit meter
        (live mode with Tavily), the run's searches are metered against the judge or the public pool."""
        assert self.search is not None

        def call(messages: list[dict[str, Any]], task: str) -> Call:
            c = Call.from_result(self.ratchet.handle(messages, task))
            if pace > 0:
                time.sleep(pace * c.latency_ms / 1000)
            return c

        search: SearchTool = self.search
        if self.credits is not None:
            search = MeteredSearch(self.search, self.credits, judge, run_id)
        return Scout(call, search, max_results=self.settings.scout_max_results)

    def note_search(self, reason: str | None) -> None:
        """Record one search's outcome from a Scout run: None when it worked, else why it did not. A failure (not a
        credit refusal) is counted, and written as a `search_error` event on the 1st, 10th, 100th and 1000th since the
        last reset, as the router reports failed optional calls; the reason is a class and a status, never a message."""
        if reason in UNAVAILABLE_REASONS:
            return
        with self.search_lock:
            self.search_last_error = reason
            if reason is None:
                return
            self.search_errors += 1
            n = self.search_errors
            day = datetime.fromtimestamp(self.wall_clock(), timezone.utc).strftime("%Y-%m-%d")
            seen_day, today = self.search_errors_today
            self.search_errors_today = (day, today + 1 if seen_day == day else 1)
        if n in EVENT_ON_FAILURE:
            self.ledger.event("search_error", f"scout search: {reason}")

    def search_status(self) -> dict[str, Any]:
        """Scout's search for `/scout/status` and the judging watchdog: provider, whether it works now (configured, not
        paused after a 432 or 433, and the last search did not fail), the last failure, failures today and the credit
        meter (None without one)."""
        credits = self.credits.status() if self.credits is not None else None
        day = datetime.fromtimestamp(self.wall_clock(), timezone.utc).strftime("%Y-%m-%d")
        with self.search_lock:
            last = self.search_last_error
            seen_day, today = self.search_errors_today
        paused = credits is not None and credits["exhausted_until"] is not None
        return {
            "provider": self.search.name if self.search is not None else None,
            "available": self.search is not None and not paused and last is None,
            "last_error": last,
            "errors_today": today if seen_day == day else 0,
            "credits": credits,
        }

    def live_check(self) -> dict[str, Any]:
        """The watchdog's banner flag (FR-11.33): `state` is `failing` only after its probe, through the public path, got a
        teacher-side or gateway-side failure within `watch_flag_max_age_s`; `stale` when the watchdog stopped, `unknown`
        without a row, `off` in mock mode. Content-free: no code, no text. Read from the ledger at most every 30 s."""
        if self.settings.mode != "live":
            return {"state": "off", "since": None, "checked": None, "search": "unknown"}
        now = time.monotonic()
        with self.live_check_lock:
            at, cached = self.live_check_cache
            if cached is not None and now - at < 30:
                return cached
            try:
                raw = self.ledger.meta_get(watch.META_KEY)
            except Exception:  # noqa: BLE001 - a status page never fails for the flag
                raw = None
            view = watch.flag_view(raw, self.wall_clock(), self.settings.watch_flag_max_age_s)
            self.live_check_cache = (now, view)
            return view

    def scout_health(self) -> dict[str, Any]:
        """What a readiness probe or the judging watchdog may read about Scout: search, credits and the gateway lease
        (None until the single-writer lease, NFR-60, is built)."""
        search = self.search_status()
        return {"search": {k: v for k, v in search.items() if k != "credits"}, "credits": search["credits"], "lease": None}


AUTO_AUTHOR = "auto"  # `tasks.updated_by` of an auto task: no admin wrote it
MOCK_MAX_AUTO_TASKS = 50  # the shared mock demo holds at most this many auto tasks; a reset clears them
AUTO_FREE_SLOTS = 50  # registry slots auto tasks never take, so the admin API can always add a task
AUTO_DESCRIPTION = "Made for requests whose first system message hashes to this name (RATCHET_AUTO_TASK=system_prompt)."


def _auto_count() -> int:
    return sum(1 for t in tasks.REGISTRY.values() if t.source == "auto")


# ----- errors and limits -----
API_PREFIXES = ("/v1/", "/openai/")
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _api_path(path: str) -> bool:
    """OpenAI-style errors on the OpenAI-compatible routes; every other route keeps FastAPI's `{"detail": ...}`."""
    return path.startswith(API_PREFIXES) or path in ("/v1", "/openai")


class BodyTooLarge(HTTPException):
    def __init__(self, limit: int) -> None:
        super().__init__(413, f"The request body is larger than this gateway's limit of {limit} bytes.")


def _http_error_response(path: str, method: str, exc: StarletteHTTPException) -> JSONResponse:
    """The response for an HTTP error, in OpenAI's envelope (with the request's id) on API paths and as
    `{"detail": ...}` elsewhere."""
    if _api_path(path):
        err = http_error(exc.status_code, str(exc.detail), method=method, path=path, headers=exc.headers)
        _note_error(exc, err.status, err.code)
        return JSONResponse(_with_request_id(err.body()), status_code=err.status, headers=err.headers or None)
    _note_error(exc, exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def _content_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    for name, value in headers:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # the server rejects a malformed length before the app sees it
    return None


class BodyLimitMiddleware:
    """413 for a request body over `max_bytes` (RATCHET_MAX_BODY_BYTES; 0 = no limit), before it is parsed. A declared
    Content-Length over the limit is refused without reading anything; a chunked body is counted as it arrives and
    refused once it passes the limit, so a client cannot stream an unbounded body into memory."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0 or scope["method"] not in BODY_METHODS:
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope["headers"])
        if declared is not None and declared > self.max_bytes:
            response = _http_error_response(scope["path"], scope["method"], BodyTooLarge(self.max_bytes))
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge(self.max_bytes)  # an HTTPException, so the error handlers render it
            return message

        await self.app(scope, counted, send)


# ----- access and security headers (ADR 0061) -----
# Headers every response carries: a browser never sniffs a JSON or text answer into a script or a page, and a page's URL
# never leaks to the sites it links to. RATCHET_CSP adds a Content-Security-Policy (`Runtime.security_headers`).
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


class SecurityHeadersMiddleware:
    """Adds `headers()` (the runtime's `security_headers`) to every response, keeping any a route set itself. A plain
    ASGI middleware, so streamed answers still stream. A 500 from an unhandled error is sent outside every middleware,
    so `_unhandled_error` adds the same headers itself."""

    def __init__(self, app: ASGIApp, headers: Callable[[], Mapping[str, str]]) -> None:
        self.app = app
        self.headers = headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = self.headers()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ----- request ids, failures, logs and metrics (ADR 0062) -----
CHAT_PATH = "/v1/chat/completions"
# The routes whose requests are counted, refused by reason and logged like a chat request's (`_after_response`): the
# chat path's other doors (FR-1.51) and the Azure-style chat route (`_chat_shaped`).
CHAT_PATHS = frozenset({CHAT_PATH, "/v1/completions", "/v1/responses", "/v1/embeddings"})
AZURE_CHAT = ("/openai/deployments/", "/chat/completions")


def _chat_shaped(path: str) -> bool:
    return path in CHAT_PATHS or (path.startswith(AZURE_CHAT[0]) and path.endswith(AZURE_CHAT[1]))


CTX_KEY = "ratchet_request"  # where the request's context sits in `scope["state"]` (`request.state`)
# Responses that get a failures row when no deeper code decided it: a key refused, a body too large, a limit hit, and
# every server-side error. A validation 400 is only counted in the metrics, so a malformed client cannot drive writes.
FAILURE_STATUSES = frozenset({401, 413, 429})
# The stage of such a row when the code that answered named none (`RequestContext.stage`).
DEFAULT_STAGES = {401: "auth", 413: "gateway", 429: "rate", 500: "gateway"}
# Why a chat request was refused before the cascade (`ratchet_rejections_total`): by error code, then by status.
REJECTION_CODES = {
    "invalid_api_key": "auth",
    "unknown_task": "unknown_task",
    "task_not_allowed": "task_not_allowed",
    "daily_quota_exceeded": "quota",
    "budget_exceeded": "budget",
    "gateway_budget_exceeded": "budget",
    "rate_limit_exceeded": "rate",
    "overloaded": "overloaded",
    "request_too_large": "too_large",
    "client_disconnected": "disconnected",
    "invalid_idempotency_key": "idempotency",
    "idempotency_key_reused": "idempotency",
    "idempotency_key_in_use": "idempotency",
}
REJECTION_STATUSES = {400: "invalid_request", 401: "auth", 403: "forbidden", 413: "too_large", 429: "quota"}
FAILURE_ROWS_PER_MINUTE = 120  # per stage, from the gateway; the router's rows that spent money are never held back


class FailureThrottle:
    """At most `per_minute` gateway-written failures rows per stage in each minute on `clock`: a key guessed in a loop or a
    client hammering past its quota is counted in the metrics but cannot fill the disk with rows. Thread-safe."""

    def __init__(self, per_minute: int = FAILURE_ROWS_PER_MINUTE, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # stage -> (minute, rows written in it)

    def allow(self, stage: str) -> bool:
        minute = int(self.clock() // 60)
        with self._lock:
            seen, n = self._windows.get(stage, (minute, 0))
            if seen != minute:
                n = 0
            if n >= self.per_minute:
                return False
            self._windows[stage] = (minute, n + 1)
            return True


def _context(request: Request | None = None) -> obs.RequestContext | None:
    """The request's context: from the running context variable, or from `scope["state"]` once the middleware has
    reset it (the 500 handler runs outside every middleware)."""
    ctx = obs.current()
    if ctx is None and request is not None:
        state = request.scope.get("state")
        ctx = state.get(CTX_KEY) if isinstance(state, dict) else None
    return ctx


def _note_error(exc: Exception, status: int, code: str | None = None, request: Request | None = None) -> None:
    """Record what refused or failed the request, for its failures row: the exception's class, the status and the error
    code, never the message (which can quote request text)."""
    ctx = _context(request)
    if ctx is not None:
        ctx.code = ctx.code or code
        ctx.error = ctx.error or (f"{type(exc).__name__} {status} {code}" if code else f"{type(exc).__name__} {status}")


def _with_request_id(body: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    """An OpenAI error envelope with the request's id beside `error` (the error object keeps OpenAI's four fields)."""
    ctx = _context(request)
    if ctx is not None:
        body["request_id"] = ctx.request_id
    return body


def _rejection(ctx: obs.RequestContext, status: int) -> str:
    return REJECTION_CODES.get(ctx.code or "") or REJECTION_STATUSES.get(status, "other")


def _log_chat(ctx: obs.RequestContext, status: int) -> None:
    """The chat request's one INFO line: ids, names, numbers and the status; never message or answer text, a URL or a
    key (invariants 12 and 13)."""
    if not obs.LOGGER.isEnabledFor(logging.INFO):
        return
    r = ctx.result or {}
    # A retry's attempt number and a replay by Idempotency-Key with the id of the request it replays (ADR 0071), and
    # checks that run after the answer (ADR 0072; the request's `offpath` line adds their cost), only when there is one.
    retry = {k: r[k] for k in ("retry_count", "idempotent_replay", "replay_of", "overhead_pending") if r.get(k)}
    obs.log(
        logging.INFO,
        "chat",
        client_request_id=ctx.client_request_id,
        client=ctx.client,
        task=ctx.task,
        served_by=r.get("served_by"),
        route=r.get("route"),
        version=r.get("version"),
        confidence=r.get("confidence"),
        escalated=r.get("escalated"),
        audited=r.get("audited"),
        latency_ms=r.get("latency_ms"),
        wall_ms=r.get("wall_ms"),
        cost_usd=r.get("cost_usd"),
        status=status,
        **retry,
    )


async def _failure_row(ctx: obs.RequestContext, status: int) -> None:
    """The failures row of a 401, 413, 429 or 5xx that no deeper code decided (`failure_handled`), and of any error
    status of a request that spent money no `requests` row holds (`failed_cost_usd`: a bug after the student was
    sampled). Written before the response starts, so a client that looks its request id up as soon as the error arrives
    finds the row. Throttled per stage, except a row that carries spend; written in a worker thread, so the event loop
    never waits for the ledger lock. Never raises: a failures row must not fail the request it describes."""
    try:
        spent = ctx.failed_cost_usd
        if ctx.failure_handled or not (status in FAILURE_STATUSES or status >= 500 or (spent > 0 and status >= 400)):
            return
        stage = ctx.stage or DEFAULT_STAGES.get(status) or ("upstream" if status in (502, 503, 504) else "gateway")
        metrics.FAILURES.inc(stage)  # every one, also when the row is throttled
        if spent > 0 or rt.failure_rows.allow(stage):
            row = {
                "request_id": ctx.request_id,
                "client": ctx.client,
                "task": ctx.task,
                "stage": stage,
                "error": ctx.error or f"HTTP {status}",
                "status": status,
                "latency_ms": ctx.elapsed_ms(),
                "cost_usd": spent if spent > 0 else 0.0,
            }
            await anyio.to_thread.run_sync(functools.partial(rt.ledger.log_failure, **row))
    except Exception:  # noqa: BLE001 - observability never fails the response it describes
        pass


async def _after_response(ctx: obs.RequestContext, status: int) -> None:
    """Once the response is sent: a chat request that ended in an error is counted (the cascade counted an answered
    one), a refusal before the cascade is counted by reason, and the chat request's log line is written (its failures
    row, if any, was written before the response, `_failure_row`). Nothing here may fail the request: its response is
    already out."""
    try:
        if _chat_shaped(ctx.path):
            if not ctx.counted:
                metrics.REQUESTS.inc(task_label(ctx.task), "none", "none", str(status))
                metrics.REQUEST_SECONDS.observe(ctx.elapsed_ms() / 1000, "none")
                refused = 400 <= status < 500 or ctx.code == "overloaded"  # the in-flight cap's 503 is a refusal too
                if refused and not ctx.failure_handled:  # the cascade's own failure is no refusal
                    metrics.REJECTIONS.inc(_rejection(ctx, status))
            _log_chat(ctx, status)
    except Exception:  # noqa: BLE001 - observability never fails a response that was already sent
        pass


class ObservabilityMiddleware:
    """Gives every request its context (`obs.new_context`: the server's `req_<hex>` id, a valid client request id, a
    valid W3C traceparent) in the `request_ctx` context variable and in `scope["state"]`, adds `x-request-id` and
    `openai-processing-ms` to every response, writes an error's failures row just before its response starts
    (`_failure_row`) and runs `_after_response` once the response is sent (after an unhandled error both run before the
    500 handler answers: that handler adds the same headers itself). Outermost of the gateway's middlewares; plain
    ASGI, so streamed answers still stream."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {}
        for name, value in scope.get("headers") or ():
            headers.setdefault(name.decode("latin-1").lower(), value.decode("latin-1"))
        ctx = obs.new_context(headers, scope.get("path", ""), scope.get("method", ""))
        scope.setdefault("state", {})[CTX_KEY] = ctx
        token = obs.request_ctx.set(ctx)
        started: list[int] = []

        async def send_with_ids(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = int(message["status"])
                started.append(status)
                await _failure_row(ctx, status)  # before the client has the error and its id to look up
                out = MutableHeaders(scope=message)
                out["x-request-id"] = ctx.request_id
                out["openai-processing-ms"] = str(round(ctx.elapsed_ms()))
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_ids)
            except Exception as e:
                if not started:  # the 500 handler answers it, outside this middleware
                    ctx.error = ctx.error or type(e).__name__
                    await _failure_row(ctx, 500)
                await _after_response(ctx, started[0] if started else 500)
                raise
            if not started:  # the app sent nothing: the server answers 500
                await _failure_row(ctx, 500)
            await _after_response(ctx, started[0] if started else 500)
        finally:
            obs.request_ctx.reset(token)


def _reader(key: str | None) -> tuple[str, str] | None:
    """Whose key this is among the dashboard's readers: ("admin", name), ("dashboard", name) or None. Both rings are
    checked every time, so the time taken does not say which one matched."""
    if not key:
        return None
    admin, dashboard = rt.admin_keys.identify(key), rt.dashboard_keys.identify(key)
    if admin is not None:
        return ("admin", admin)
    return None if dashboard is None else ("dashboard", dashboard)


async def _access(request: Request) -> None:
    """The dashboard API's gate, run before every route (ADR 0061). A route whose level (`access.route_access`; DASHBOARD
    for a route nobody classified) is DASHBOARD or BOM needs an admin or dashboard key, from `Authorization: Bearer` or
    `X-API-Key`, unless RATCHET_PUBLIC_DASHBOARD=1 (BOM: or RATCHET_PUBLIC_BOM=1): 401 for a missing or wrong key, 403
    for an API key, and 403 when no admin or dashboard keys are set. METRICS (GET /metrics) also takes
    RATCHET_METRICS_TOKEN (ADR 0062), and is closed to everyone only when that is unset too. The reader it found
    (`request.state.reader`) decides whether a public read is scrubbed. Other levels check their callers themselves.
    Async, so the chat path pays no thread hop for it."""
    route = request.scope.get("route")
    level = acl.route_access(request.method, getattr(route, "path", request.url.path)) or acl.DASHBOARD
    if level not in acl.READER_LEVELS:
        return
    key = bearer(request.headers.get("authorization"), request.headers.get("x-api-key"))
    reader = _reader(key)
    token = level == acl.METRICS and rt.metrics_token.matches(key)  # checked whatever `_reader` found, like the rings
    if reader is None and token:
        reader = ("metrics", "token")  # a Prometheus scraper (RATCHET_METRICS_TOKEN, ADR 0062)
    request.state.reader = reader
    a = rt.access
    if reader is not None or a.public_dashboard or (level == acl.BOM and a.public_bom):
        return
    if not (rt.admin_keys.enabled or rt.dashboard_keys.enabled or (level == acl.METRICS and rt.metrics_token.enabled)):
        raise HTTPException(
            403, "the dashboard API is private: set RATCHET_DASHBOARD_KEYS or RATCHET_ADMIN_KEYS, or RATCHET_PUBLIC_DASHBOARD=1"
        )
    if rt.keys.identify(key) is not None:
        raise HTTPException(403, "this is an API key; the dashboard API needs a dashboard or admin key")
    raise HTTPException(401, "invalid or missing dashboard key", headers={"WWW-Authenticate": "Bearer"})


def _scrubbed(request: Request) -> bool:
    """Whether this dashboard read is scrubbed (`Access.scrubs`): by default only an anonymous read of a public dashboard
    in live mode."""
    keyed = getattr(request.state, "reader", None) is not None
    return rt.access.scrubs(live=rt.settings.mode != "mock", keyed=keyed)


def _pseudonyms(*names: Iterable[str | None]) -> dict[str, str]:
    """Client pseudonyms over every name a reader could meet: the configured API keys and `names` (the clients in the
    ledger, the names in task allow-lists)."""
    return acl.pseudonyms(itertools.chain(rt.keys.names, *names))


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """At shutdown, after the server stopped taking requests, the demo janitor's thread ends and the off-path queue drains
    for up to RATCHET_SIDECAR_DRAIN_S on a worker thread (`Runtime.shutdown`, ADR 0072)."""
    yield
    _janitor.stop()
    await anyio.to_thread.run_sync(rt.shutdown)


rt = Runtime()
# /docs, /redoc and /openapi.json only with RATCHET_OPENAPI (default: mock mode only): in live mode the schema would show
# anyone every management route.
_DOCS: dict[str, Any] = {} if rt.access.openapi else {"docs_url": None, "redoc_url": None, "openapi_url": None}


async def _demo_limit(request: Request) -> None:
    """The shared simulation's per-IP limit on demo actions (FR-11.30, ADR 0133), run after `_access` on every route. It
    acts only in mock mode, with RATCHET_DEMO_RPM set, on a non-GET call to a `/ratchet/demo/` route: it counts the call
    as activity for the idle reset and takes a token from the IP's bucket, answering 429 with `Retry-After` when it is
    empty. In live mode it does nothing, so `_mock_only()` still answers 403 first (INV-8)."""
    if rt.settings.mode != "mock" or request.method == "GET":
        return
    route = request.scope.get("route")
    if not str(getattr(route, "path", "")).startswith("/ratchet/demo/"):
        return
    policy = rt.demo_policy
    _janitor.touch()
    _janitor.ensure_started()
    if policy.rpm <= 0:
        return
    d = rt.demo_limiter.take(f"demo:{_client_ip(request)}", policy.rpm)
    if d is not None and not d.allowed:
        wait = max(1, math.ceil(d.retry_after_s))
        raise HTTPException(
            429,
            f"the shared simulation takes {policy.rpm} actions a minute per visitor; try again in {wait} s",
            headers={"Retry-After": str(wait)},
        )


app = FastAPI(
    title="Ratchet", version=__version__, dependencies=[Depends(_access), Depends(_demo_limit)], lifespan=_lifespan, **_DOCS
)
app.add_middleware(BodyLimitMiddleware, max_bytes=rt.settings.max_body_bytes)
app.add_middleware(SecurityHeadersMiddleware, headers=lambda: rt.security_headers)  # a 413 carries them too
app.add_middleware(ObservabilityMiddleware)  # outermost: every response, a 413 included, carries x-request-id


@app.exception_handler(APIError)
async def _api_error(request: Request, exc: APIError) -> JSONResponse:
    """OpenAI's envelope, with the request's id beside `error` (ADR 0062)."""
    _note_error(exc, exc.status, exc.code, request)
    return JSONResponse(_with_request_id(exc.body(), request), status_code=exc.status, headers=exc.headers or None)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    if not _api_path(request.url.path):
        _note_error(exc, exc.status_code, request=request)
        return await http_exception_handler(request, exc)
    return _http_error_response(request.url.path, request.method, exc)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    if not _api_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    return await _api_error(request, from_validation_errors(exc.errors()))


def _power_of_ten(n: int) -> bool:
    digits = str(n)
    return digits[0] == "1" and set(digits[1:]) <= {"0"}


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception) -> Response:
    """A bug, not a client mistake: 500 (`api_error` on API paths, with the request's id). The server still logs the
    traceback. The ledger gets a `gateway_error` event with the exception's class name only (a message could hold request
    text) at the 1st, 10th, 100th, ... occurrence, so a crash loop cannot flood it; every one gets a failures row
    (`ObservabilityMiddleware`)."""
    rt.gateway_errors += 1  # handlers run on the event loop, one at a time
    if _power_of_ten(rt.gateway_errors):
        detail = f"{type(exc).__name__} (unhandled error {rt.gateway_errors})"
        try:  # in a worker thread: the ledger lock may be held by a request in progress
            await anyio.to_thread.run_sync(rt.ledger.event, "gateway_error", detail)
        except Exception:  # noqa: BLE001 - the ledger may be what failed; the 500 must still go out
            pass
    if _api_path(request.url.path):
        response = await _api_error(request, APIError(500, "The server had an error while processing your request.", "api_error"))
    else:
        response = PlainTextResponse("Internal Server Error", status_code=500)
    response.headers.update(rt.security_headers)  # sent outside SecurityHeadersMiddleware
    ctx = _context(request)  # and outside ObservabilityMiddleware, which already wrote its failures row
    if ctx is not None:
        response.headers["x-request-id"] = ctx.request_id
        response.headers["openai-processing-ms"] = str(round(ctx.elapsed_ms()))
    return response


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


def _page(path: Path, title: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (  # placeholder until the page exists
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        f"<h1>{title}</h1><p><a href='/'>Home</a> · <a href='/scout'>Scout</a> · <a href='/dashboard'>Dashboard</a></p>"
    )


def _spa_or(path: Path, title: str) -> HTMLResponse:
    """The React app when built, else the lite page."""
    if SPA_INDEX.exists():
        return HTMLResponse(SPA_INDEX.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_page(path, title))


@app.get("/", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    return _spa_or(LANDING, "Ratchet")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return _spa_or(DASHBOARD, "Ratchet dashboard")


@app.get("/scout", response_class=HTMLResponse)
def scout_page() -> HTMLResponse:
    return _spa_or(SCOUT_PAGE, "Scout")


@app.get("/judges", response_class=HTMLResponse)
def judges_page() -> HTMLResponse:
    """The judges' console (React app only; without the build, the placeholder page with links to the others)."""
    return _spa_or(HERE / "web" / "judges.html", "Ratchet for judges")


# The original single-file pages stay available (no JavaScript build, no fonts).
@app.get("/lite", response_class=HTMLResponse)
def lite_landing() -> str:
    return _page(LANDING, "Ratchet")


@app.get("/lite/dashboard", response_class=HTMLResponse)
def lite_dashboard() -> str:
    return _page(DASHBOARD, "Ratchet dashboard")


@app.get("/lite/scout", response_class=HTMLResponse)
def lite_scout() -> str:
    return _page(SCOUT_PAGE, "Scout")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    if not (DIST / "favicon.svg").exists():
        raise HTTPException(404)
    return FileResponse(DIST / "favicon.svg", media_type="image/svg+xml")


# ----- OpenAI-compatible API -----
def _client(authorization: str | None, x_api_key: str | None, api_key: str | None = None) -> str | None:
    """The API client's name, or None when keys are off. 401 when keys are on and this one is wrong. The key comes from
    `Authorization: Bearer`, `X-API-Key` or Azure's `api-key` (`auth.bearer`). The name (never the key) goes on the
    request's context, for its log line and failures row."""
    if not rt.keys.enabled:
        return None
    name = rt.keys.identify(bearer(authorization, x_api_key, api_key))
    if name is None:
        raise HTTPException(401, "invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})
    ctx = obs.current()
    if ctx is not None:
        ctx.client = name
    return name


def _utc_midnight(now: float) -> float:
    return float(calendar.timegm(time.gmtime(now)[:3] + (0, 0, 0)))


def _duration(seconds: float) -> str:
    """OpenAI's duration format for `X-RateLimit-Reset-*`, in whole seconds rounded up: `3h12m5s`, `6m0s`, `45s`."""
    total = max(0, math.ceil(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    return f"{minutes}m{secs}s" if minutes else f"{secs}s"


def _set_stage(stage: str) -> None:
    ctx = obs.current()
    if ctx is not None:
        ctx.stage = stage


def _quota(
    client: str | None, limits: KeyLimits | None, use: Usage, need: int = 1, holds: list[Hold] | None = None
) -> dict[str, str]:
    """Rate-limit headers for the key's daily request quota (`KeyLimits.daily_requests`, from RATCHET_API_KEY_DAILY_REQUESTS
    or RATCHET_KEY_LIMITS), with when it resets (`X-RateLimit-Reset-Requests`); 429 `insufficient_quota` when it is used
    up. The count is the spend meter's (`use`), so the check runs no query (ADR 0063). The 429 says when to come back
    (`Retry-After`, seconds to 00:00 UTC) and tells OpenAI SDKs not to retry (they retry 429s by default, which cannot
    help before midnight). `need` is how many requests of the quota the call will use: more than 1 for a batch of
    prompts (/v1/completions), refused whole when the quota cannot take it all. The room is held for the request
    (`SpendMeter.hold`, appended to `holds`; the caller releases it when the request is counted or ends), so concurrent
    requests cannot all pass the same count: with the quota almost used up, exactly the requests that fit are admitted.
    A batch's later prompts hold their own room once the first is counted, so a concurrent request can still use up the
    quota before one of them runs, which then stops the batch (`_batch_stopped`)."""
    limit = limits.daily_requests if limits is not None else 0
    if client is None or limit <= 0:
        return {}
    reset_in = _utc_midnight(use.now) + 86400 - use.now
    used = use.day_requests
    headers = {"X-RateLimit-Limit-Requests": str(limit), "X-RateLimit-Reset-Requests": _duration(reset_in)}
    hold = rt.meter.hold(client, need, limit)  # the check and the reservation in one step (a concurrent request sees both)
    if hold is None:
        left = max(0, limit - used - rt.meter.held(client))
        headers.update(
            {
                "X-RateLimit-Remaining-Requests": str(left),
                "Retry-After": str(max(1, math.ceil(reset_in))),
                "X-Should-Retry": "false",
            }
        )
        message = f"daily request quota of {limit} used up for {client!r}; resets at 00:00 UTC"
        _set_stage("quota")
        if left:  # a batch larger than what is left
            message = f"daily request quota of {limit} for {client!r} has {left} left, but this request needs {need}"
            message += "; resets at 00:00 UTC"
        raise APIError(429, message, "insufficient_quota", "daily_quota_exceeded", headers=headers)
    if holds is not None:
        holds.append(hold)
    else:  # a caller that only wants the headers
        hold.release()
    return {**headers, "X-RateLimit-Remaining-Requests": str(max(0, limit - used - need))}


def _usd(value: float) -> str:
    """A dollar amount in a header: six decimals, never negative."""
    return f"{max(0.0, value):.6f}"


def _remaining_usd(limits: KeyLimits, use: Usage) -> float:
    """What the key may still spend before its first cap: the smaller of its daily and monthly room."""
    room = [cap - spent for cap, spent in ((limits.daily_usd, use.day_usd), (limits.monthly_usd, use.month_usd)) if cap > 0]
    return max(0.0, min(room)) if room else 0.0


def _spend_headers(limits: KeyLimits | None, use: Usage) -> dict[str, str]:
    """`X-Ratchet-Spend-Today-USD` (the key's dollars since 00:00 UTC) and `X-Ratchet-Budget-Remaining-USD`, when a
    US dollar cap applies to the key; nothing otherwise."""
    if limits is None or not limits.usd_capped:
        return {}
    return {"X-Ratchet-Spend-Today-USD": _usd(use.day_usd), "X-Ratchet-Budget-Remaining-USD": _usd(_remaining_usd(limits, use))}


def _budget(client: str | None, limits: KeyLimits | None, use: Usage) -> bool:
    """The US dollar budgets (ADR 0063), checked before the cascade. A key whose daily or monthly cap is spent gets 429
    `insufficient_quota` / `budget_exceeded`, with `X-Ratchet-Budget-Remaining-USD: 0`, `Retry-After` and
    `X-Ratchet-Budget-Reset` (when the cap that resets last resets) and `X-Should-Retry: false`. When the gateway's own
    daily budget (RATCHET_DAILY_BUDGET_USD) is spent, RATCHET_BUDGET_MODE=block refuses with 429 `gateway_budget_exceeded`;
    `student_only` returns True, so the request runs with `RequestMeta.student_only`. A request in flight can overshoot a
    cap by its own cost: the check reads what was spent before it."""
    if limits is not None and limits.usd_capped:
        spent = []
        if limits.daily_usd > 0 and use.day_usd >= limits.daily_usd:
            spent.append(("daily", limits.daily_usd, _utc_midnight(use.now) + 86400, "00:00 UTC"))
        if limits.monthly_usd > 0 and use.month_usd >= limits.monthly_usd:
            spent.append(("monthly", limits.monthly_usd, month_bounds(use.now)[1], "00:00 UTC on the 1st"))
        if spent:
            period, cap, reset_at, when = max(spent, key=lambda x: x[2])
            reset_in = reset_at - use.now
            headers = {
                **_spend_headers(limits, use),
                "Retry-After": str(max(1, math.ceil(reset_in))),
                "X-Ratchet-Budget-Reset": _duration(reset_in),
                "X-Should-Retry": "false",
            }
            _set_stage("budget")
            message = f"the {period} budget of ${money(cap)} is spent for {client!r}; it resets at {when}"
            raise APIError(429, message, "insufficient_quota", "budget_exceeded", headers=headers)
    cap = rt.limit_policy.daily_budget_usd
    if cap <= 0 or use.gateway_day_usd < cap:
        return False
    if rt.limit_policy.budget_mode == "student_only":
        return True
    raise _gateway_budget_error(use.now)


def _gateway_budget_error(now: float, need: str | None = None) -> APIError:
    """429 `insufficient_quota` / `gateway_budget_exceeded` until 00:00 UTC: in block mode for every chat request, in
    student_only mode for one that needs the teacher (`need`). The gateway's budget itself is the operator's and is not
    named."""
    _set_stage("budget")
    reset_in = _utc_midnight(now) + 86400 - now
    headers = {"Retry-After": str(max(1, math.ceil(reset_in))), "X-Should-Retry": "false"}
    if need is None:
        message = "this gateway's daily budget is spent; it resets at 00:00 UTC"
    else:
        message = (
            "this gateway's daily budget is spent: until 00:00 UTC it serves only answers the student gives alone, and "
            "this request needs the teacher"
        )
    return APIError(429, message, "insufficient_quota", "gateway_budget_exceeded", headers=headers)


def _rate_headers(d: Decision) -> dict[str, str]:
    """OpenAI's request-rate headers for a token bucket's state (FR-12.3): the limit a minute, the whole requests left
    and the time until the bucket is full again."""
    return {
        "X-RateLimit-Limit-Requests": str(d.limit),
        "X-RateLimit-Remaining-Requests": str(d.remaining),
        "X-RateLimit-Reset-Requests": _duration(d.reset_s),
    }


def _rate(request: Request, client: str | None) -> Decision | None:
    """The request rate for a chat request (FR-12.3, ADR 0063), checked on the event loop before the body is read, so a
    request over it reads no body and never waits for a worker thread: the key's requests a minute (`rpm`), or the client
    IP's on a gateway without keys (RATCHET_IP_RPM; `_client_ip`, which honours RATCHET_TRUST_PROXY): 429
    `rate_limit_exceeded` with `Retry-After` and the `X-RateLimit-*-Requests` headers. Returns the bucket's decision for
    the response's headers."""
    limits = rt.limits_for(client)
    ip = _client_ip(request) if client is None and rt.limiter.ip_rpm > 0 else None
    try:
        return rt.limiter.rate(client, limits.rpm if limits else 0, ip)
    except Refused as e:
        raise _refusal(e) from None


def _hold(client: str | None) -> Slot:
    """The in-flight caps for a chat request whose body is in (FR-12.3, ADR 0063), taken just around its worker thread,
    so a client that sends its body slowly holds no slot: the gateway's (RATCHET_MAX_INFLIGHT: 503 `overloaded`,
    `Retry-After: 1`), then the key's (`concurrency`: 429 `rate_limit_exceeded`, `Retry-After: 1`). The slot it returns is
    released when the response is built."""
    limits = rt.limits_for(client)
    try:
        return rt.limiter.hold(client, limits.concurrency if limits else 0)
    except Refused as e:
        raise _refusal(e) from None


def _refusal(e: Refused) -> APIError:
    _set_stage("rate")
    if e.kind == "overloaded":
        message = f"the gateway has {e.limit} chat requests in flight, its limit; retry in a second"
        return APIError(503, message, "api_error", "overloaded", headers={"Retry-After": "1"})
    if e.kind == "concurrency":
        message = f"{e.who!r} has {e.limit} requests in flight, its limit; retry when one of them ends"
        return APIError(429, message, "rate_limit_error", "rate_limit_exceeded", headers={"Retry-After": "1"})
    assert e.decision is not None
    wait = max(1, math.ceil(e.decision.retry_after_s))
    who = "this client address" if e.who == "ip" else repr(e.who)
    message = f"rate limit of {e.limit} requests a minute reached for {who}; retry after {wait} s"
    headers = {"Retry-After": str(wait), **_rate_headers(e.decision)}
    return APIError(429, message, "rate_limit_error", "rate_limit_exceeded", headers=headers)


NO_RETRY = {"X-Should-Retry": "false"}
MAX_UPSTREAM_RETRY_AFTER = 60  # OpenAI SDKs refuse to wait longer than this


def _client_wait(retry_after: float | None) -> int:
    """The teacher's wait as a client's `Retry-After`: whole seconds, 1 to 60 (1 when it sent none)."""
    return min(MAX_UPSTREAM_RETRY_AFTER, max(1, math.ceil(retry_after or 1)))


def _map_upstream(e: httpx.HTTPError) -> APIError:
    """The client's error for a failed teacher call whose answer it needed (INV-7, ADR 0014), after the provider's own
    retries. A cause the client can fix is a 400 with the teacher's message, param and code; a rate limit is a 429 with
    the teacher's wait; a timeout is a 504; everything else is a 502. `X-Should-Retry: false` marks the ones a retry
    cannot fix, so OpenAI SDKs do not repeat the request (and its student samples) for nothing. A 502 or 504 the client
    may retry carries the teacher's `Retry-After` when it sent one: the provider stops retrying at a wait over its cap,
    so the client is the one that has to wait. A teacher tool call to a request that offers no tools
    (`router.Ratchet._ask_teacher`) is a 502 `tool_calls_unsupported` no retry can change. The teacher's open breaker
    (`router.Ratchet._teacher_complete`, ADR 0019) is a 503 `teacher_circuit_open` at once, with the time until its next
    probe as `Retry-After` (1 to 60 s)."""
    u = as_upstream(e)
    if isinstance(u, CircuitOpen) and u.code == TEACHER_CIRCUIT_OPEN:
        wait = _client_wait(u.retry_after)
        message = f"teacher circuit open: recent teacher calls failed, so the gateway is not calling it; retry after {wait} s"
        return APIError(503, message, "api_error", TEACHER_CIRCUIT_OPEN, headers={"Retry-After": str(wait)})
    if u.code == TOOL_CALLS_UNSUPPORTED:
        message = "The teacher answered with a tool call to a request that offers no tools."
        return APIError(502, message, "api_error", TOOL_CALLS_UNSUPPORTED, headers=NO_RETRY)
    if u.status in CLIENT_CAUSED:
        code = u.code or ("request_too_large" if u.status == 413 else None)
        message = u.message or f"The teacher model rejected the request ({u.label})."
        return APIError(400, message, INVALID_REQUEST, code, u.param, headers=NO_RETRY)
    if u.status in (401, 403):
        return APIError(502, f"teacher authentication failed: {u.label}", "api_error", "upstream_auth_error", headers=NO_RETRY)
    if u.status == 404:  # the request's model never selects the teacher, so this is the gateway's configuration
        return APIError(502, f"teacher model not found: {u.label}", "api_error", "upstream_not_found", headers=NO_RETRY)
    if u.status == 429:
        wait = _client_wait(u.retry_after)
        message = f"teacher rate limited: {u.label}; retry after {wait} s"
        return APIError(429, message, "rate_limit_error", "upstream_rate_limited", headers={"Retry-After": str(wait)})
    final = 300 <= u.status < 500 and u.status not in RETRY_STATUSES  # a redirect, a 402: no retry changes the answer
    if final:
        headers: dict[str, str] | None = NO_RETRY
    elif u.retry_after is not None:
        headers = {"Retry-After": str(_client_wait(u.retry_after))}
    else:
        headers = None
    if u.status == 504:
        return APIError(504, f"teacher timeout: {u.label}", "api_error", "teacher_timeout", headers=headers)
    return APIError(502, f"teacher unavailable: {u.label}", "api_error", "teacher_unavailable", headers=headers)


def _envelope(cid: str, obj: str, created: int, model: str, fingerprint: str) -> dict[str, Any]:
    """The fields every response body and stream chunk starts with. `system_fingerprint` names the serving configuration
    (`router.fingerprint`); Ratchet has one service tier."""
    return {
        "id": cid,
        "object": obj,
        "created": created,
        "model": model,
        "system_fingerprint": fingerprint,
        "service_tier": SERVICE_TIER,
    }


def _chunks(text: str, words: int = 4) -> list[str]:
    """Split an answer into a few words per streamed chunk, keeping every character."""
    parts = re.findall(r"\s*\S+\s*|\s+", text)
    return ["".join(parts[i : i + words]) for i in range(0, len(parts), words)] or [""]


TOKENS_PER_CHUNK = 4  # a streamed answer with logprobs: tokens per content chunk


def _logprobs(asked: bool, content: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """A choice's `logprobs`: null unless the client asked (`logprobs: true`); then `{content, refusal: null}`, with
    `content` null when the token logprobs cannot be given (FR-1.14)."""
    return {"content": content, "refusal": None} if asked else None


def _call(call: Mapping[str, Any]) -> dict[str, Any]:
    """A tool call as a response carries it: `{id, type, function: {name, arguments}}` (a copy, so the response never
    shares the router's objects)."""
    fn = call["function"]
    return {"id": call["id"], "type": "function", "function": {"name": fn["name"], "arguments": fn["arguments"]}}


def _message(ch: Choice, legacy: bool) -> dict[str, Any]:
    """A choice's `message` (FR-1.41). With tool calls, `content` is the text the model wrote beside them or null, and
    the calls are `tool_calls` with the upstream ids, or for a request that used the legacy `functions` (`legacy`)
    `function_call`, the first call's name and arguments: the legacy form has one call per message."""
    msg: dict[str, Any] = {"role": "assistant", "content": ch.text, "refusal": None, "annotations": []}
    if ch.tool_calls:
        msg["content"] = ch.text or None
        if legacy:
            msg["function_call"] = _call(ch.tool_calls[0])["function"]
        else:
            msg["tool_calls"] = [_call(c) for c in ch.tool_calls]
    return msg


def _finish(ch: Choice, legacy: bool) -> str:
    """A choice's `finish_reason`: `function_call` in place of `tool_calls` for a legacy request."""
    return "function_call" if legacy and ch.finish_reason == "tool_calls" else ch.finish_reason


def _call_deltas(ch: Choice, legacy: bool) -> list[dict[str, Any]]:
    """The stream deltas of a choice's tool calls: one per call, `{tool_calls: [{index, id, type, function: {name,
    arguments}}]}` with the whole arguments string, which is what the OpenAI SDK's `ChatCompletionStreamState` and
    LangChain's chunk accumulators merge by `index`; for a legacy request one `{function_call: {name, arguments}}`."""
    if not ch.tool_calls:
        return []
    if legacy:
        return [{"function_call": _call(ch.tool_calls[0])["function"]}]
    return [{"tool_calls": [{"index": k, **_call(c)}]} for k, c in enumerate(ch.tool_calls)]


def _stream_pieces(choice: Choice, asked: bool) -> list[tuple[str, dict[str, Any] | None]]:
    """A choice's content chunks as (text, logprobs). With token logprobs, a few tokens per chunk, each chunk carrying
    its own tokens, so the chunks' logprobs add up to the non-streamed response's; tokens that do not spell the text
    (never from the router, which aligns them) go in one chunk with the whole text. Otherwise a few words per chunk."""
    entries = choice.logprobs if asked else None
    if entries is None:
        return [(piece, _logprobs(asked, None)) for piece in _chunks(choice.text)]
    if "".join(e["token"] for e in entries) != choice.text or not entries:
        return [(choice.text, _logprobs(True, entries))]
    groups = [entries[i : i + TOKENS_PER_CHUNK] for i in range(0, len(entries), TOKENS_PER_CHUNK)]
    return [("".join(e["token"] for e in g), _logprobs(True, g)) for g in groups]


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness for Docker: the process answers. Public, even when API keys protect /v1/*. Async, so it answers while
    every worker thread is busy (a full thread pool is not a dead process); readiness is /readyz."""
    return {"status": "ok", "version": __version__, "mode": rt.settings.mode}


@app.get("/livez")
async def livez() -> dict[str, Any]:
    """Liveness under its Kubernetes name: the same answer as /healthz."""
    return await healthz()


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness for load balancers and deploy scripts (FR-12.30, ADR 0070): 200 `ready` or `degraded` while requests can
    be answered, 503 `not_ready` when the ledger cannot be written or no teacher can answer. The probes run on their own
    two threads, never the request pool, each awaited at most 2 s and reused for 15 s (`readiness.Readiness`). Public;
    the answer names states, statuses and exception classes, never URLs, keys or messages. A 503 here is an answer, not a
    failed request: it writes no failures row, however often a load balancer asks."""
    status, body = await rt.readiness.report()
    ctx = _context(request)
    if ctx is not None:
        ctx.failure_handled = True
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


# ----- models (FR-1.2, FR-1.50, ADR 0048) -----
PROCESS_START = int(time.time())  # a model's `created` when no request has named its task yet
RECENT_TASK_DAYS = 30  # an unregistered task stays listed this long after a request last named it
MAX_RECENT_MODELS = 500  # unregistered tasks listed, the most recently used first


def _model_object(model_id: str, task: tasks.Task | None, created: float, live_id: int | None, **extra: Any) -> dict[str, Any]:
    """One `model` object, in OpenAI's shape (`id`, `object`, `created`, `owned_by`) plus a `ratchet` block: the task the
    id names (None for `ratchet`, whose task comes from each request), whether it is pinned, its answer format, the
    answer fields that must agree, and the live version that serves it (None when the student never does: a pinned
    task, or an unregistered one under RATCHET_UNKNOWN_TASKS=teacher)."""
    teacher_only = task is not None and (task.pinned or (task.source == "adhoc" and rt.ratchet.unknown_tasks == "teacher"))
    info = {
        "task": task.name if task is not None else None,
        "pinned": task is not None and task.pinned,
        "format": task.format if task is not None else None,
        "compare_keys": [f.path for f in task.fields] if task is not None else [],
        "live_version": None if teacher_only else live_id,
        **extra,
    }
    return {"id": model_id, "object": "model", "created": int(created), "owned_by": "ratchet", "ratchet": info}


def _offered(name: str, task: tasks.Task | None, client: str | None, recent: Container[str]) -> tasks.Task | None:
    """The task a model id naming `name` stands for, for this client, or None when the id is not offered to it. `task` is
    the one registered under `name` (None: none is); `recent` holds the names this client's requests used in the last
    30 days. A registered task its `clients` allow, but an auto task (named after a digest of some client's system
    prompt, threat model T12) only to a key whose own recent requests used it; with keys off, to everyone. An
    unregistered task name unless RATCHET_UNKNOWN_TASKS is `reject`, as chat completions admit it."""
    if name in tasks.RESERVED_NAMES:  # `embeddings` (FR-1.51): its rows are /v1/embeddings', never a chat task's
        return None
    if task is not None:
        hidden = task.source == "auto" and client is not None and name not in recent
        return None if hidden or not task.allows(client) else task
    if rt.ratchet.unknown_tasks == "reject" or not tasks.valid_name(name):
        return None
    return tasks.get(name)


def _models(client: str | None) -> list[dict[str, Any]]:
    """Every model id this client may send: `ratchet`; `ratchet/<task>` for each registered task it may use (`_offered`)
    and, unless RATCHET_UNKNOWN_TASKS is `reject`, each unregistered task its requests named in the last 30 days; each
    model alias whose task it may use; and the embeddings model, when /v1/embeddings is on. `created` is when a request
    (this client's, with keys on) first named the task, for a task used in the last 30 days, else when the process
    started."""
    live = rt.ledger.live()
    live_id = live.id if live is not None else None
    recent = rt.ledger.recent_tasks(RECENT_TASK_DAYS, client=client, limit=MAX_RECENT_MODELS)
    first = {r["task"]: r["first_ts"] for r in recent}
    registry = tasks.REGISTRY
    out = [_model_object(RATCHET_MODEL, None, PROCESS_START, live_id)]
    out += [
        _model_object(f"ratchet/{t.name}", t, first.get(t.name, PROCESS_START), live_id)
        for t in registry.values()
        if _offered(t.name, t, client, first) is not None
    ]
    out += [
        _model_object(f"ratchet/{r['task']}", t, r["first_ts"], live_id)
        for r in recent
        if r["task"] not in registry and (t := _offered(r["task"], None, client, first)) is not None
    ]
    for alias, target in rt.routing.aliases.items():
        t = _offered(target, registry.get(target), client, first)
        if t is not None:
            out.append(_model_object(alias, t, first.get(target, PROCESS_START), live_id, alias_of=f"ratchet/{target}"))
    if rt.embedder is not None:
        out.append(_embeddings_model())
    return out


def _embeddings_model() -> dict[str, Any]:
    """The model /v1/embeddings always asks for (RATCHET_EMBEDDINGS_MODEL, FR-1.51), so a client can find the id that
    RATCHET_MODEL_POLICY=strict accepts there: no task, served by the upstream (`ratchet.endpoint`)."""
    return _model_object(rt.settings.embeddings_model, None, PROCESS_START, None, endpoint="embeddings")


def _model(model_id: str, client: str | None) -> dict[str, Any] | None:
    """The model object for one id, as `_models` would list it, or None when this client may not send it. It looks up
    that one task (two index probes), not the whole list. Unlike the list it also finds `ratchet/<name>` for an
    unregistered name no request used lately, which chat completions admit unless RATCHET_UNKNOWN_TASKS is `reject`, so
    a client that retrieves its model before the first request gets the same answer as the request."""
    live = rt.ledger.live()
    live_id = live.id if live is not None else None
    if model_id == RATCHET_MODEL:
        return _model_object(RATCHET_MODEL, None, PROCESS_START, live_id)
    if rt.embedder is not None and model_id == rt.settings.embeddings_model:
        return _embeddings_model()
    m = MODEL_TASK.fullmatch(model_id)
    name = m.group(1) if m else rt.routing.aliases.get(model_id)
    if name is None:
        return None
    seen = rt.ledger.task_seen(name, client)
    recent = {name} if seen is not None and seen["last_ts"] >= time.time() - RECENT_TASK_DAYS * DAY_SECONDS else set()
    task = _offered(name, tasks.REGISTRY.get(name), client, recent)
    if task is None:
        return None
    created = seen["first_ts"] if seen is not None and recent else PROCESS_START
    if m:
        return _model_object(f"ratchet/{name}", task, created, live_id)
    return _model_object(model_id, task, created, live_id, alias_of=f"ratchet/{name}")


@app.get("/v1/models")
def models(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """The model ids a client can send (`_models`). Each passes the OpenAI SDK's `Model`."""
    client = _client(authorization, x_api_key, api_key)
    return {"object": "list", "data": _models(client)}


@app.get("/v1/models/{model_id:path}")
def retrieve_model(
    model_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """One model (`_model`; `ratchet:<task>` finds `ratchet/<task>`), or 404 `model_not_found`. The id may hold a slash:
    the OpenAI SDK sends `ratchet/ticket-triage` as `ratchet%2Fticket-triage`, which arrives decoded."""
    client = _client(authorization, x_api_key, api_key)
    found = _model(model_id, client)
    if found is None:
        raise model_not_found(model_id, param=None)
    return found


# The body is read by hand (for the 400 on bad JSON and the size limit), so describe it for /docs.
CHAT_BODY = {"requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}}


@app.post("/v1/chat/completions", response_model=None, openapi_extra=CHAT_BODY)
async def chat(
    request: Request,
    x_ratchet_task: str | None = Header(default=None),
    x_ratchet_store: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
    x_stainless_retry_count: str | None = Header(default=None),
) -> Response:
    """Checks run in this order: a declared body size (413, `BodyLimitMiddleware`), API key (401), the key's or the
    client IP's requests a minute (`_rate`: 429), the body read and counted (413 for a chunked body over the limit), the
    in-flight caps (`_hold`: 503 at the gateway's, 429 at the key's), the Idempotency-Key and JSON (400), a stored answer
    for that key (sent again at once, ADR 0071), request validation (400, including `X-Ratchet-Store`), the model (404
    under RATCHET_MODEL_POLICY=strict), the task (400 unknown, 403 not allowed for the key), the daily request quota
    (429), the US dollar budgets (429), then the cascade. A refused request writes no ledger row and uses no quota or
    budget, and neither does a replay; past the rate it has taken its key's (or IP's) token. The in-flight slots are
    taken only once the body is in, so a client that trickles its body holds none. Everything after reading the body
    runs in a worker thread (the default pool, as for sync routes), so neither a large body nor the ledger lock blocks
    the event loop."""
    client = _client(authorization, x_api_key, api_key)
    return await _chat(request, x_ratchet_task, client, None, x_ratchet_store, idempotency_key, x_stainless_retry_count)


@app.post("/openai/deployments/{deployment}/chat/completions", response_model=None, openapi_extra=CHAT_BODY)
async def azure_chat(
    deployment: str,
    request: Request,
    x_ratchet_task: str | None = Header(default=None),
    x_ratchet_store: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
    x_stainless_retry_count: str | None = Header(default=None),
) -> Response:
    """Azure OpenAI's URL shape, for clients written against it (`AzureOpenAI`): the same handler and checks as
    /v1/chat/completions, with the deployment as the task (a model alias, `ratchet` or a task name; the body's `model`
    is not read) and the key in `api-key`. The `api-version` query parameter is accepted and ignored."""
    client = _client(authorization, x_api_key, api_key)
    return await _chat(request, x_ratchet_task, client, deployment, x_ratchet_store, idempotency_key, x_stainless_retry_count)


async def _chat(
    request: Request,
    header_task: str | None,
    client: str | None,
    deployment: str | None = None,
    header_store: str | None = None,
    idempotency_key: str | None = None,
    retry_header: str | None = None,
) -> Response:
    """Both chat routes after the key: the request rate, the body, the in-flight caps, then the request in a worker
    thread (`_complete_idempotent` with an Idempotency-Key, else `_complete`)."""
    rate = _rate(request, client)
    raw = await _body(request)
    retries = idem.retry_count(retry_header)  # the SDK's attempt number: a retry is never a repeat (INV-3)
    if idempotency_key is not None and rt.idem.enabled:
        work = functools.partial(
            _complete_idempotent, raw, header_task, client, idempotency_key, retries, header_store, rate, deployment
        )
    else:
        work = functools.partial(
            _complete, raw, header_task, client, header_store, retry_count=retries, rate=rate, deployment=deployment
        )
    with _hold(client):  # its in-flight slots are given back when the response is built
        return await anyio.to_thread.run_sync(work)


async def _body(request: Request) -> bytes:
    """The request body, counted by `BodyLimitMiddleware` as it arrives; a 400 when the client goes away first."""
    try:
        return await request.body()
    except ClientDisconnect as e:  # nobody is left to read the answer; not a server error either
        ctx = obs.current()
        if ctx is not None:
            ctx.code = "client_disconnected"
        raise APIError(400, "The client disconnected before the request body was complete.") from e


# ----- retries and idempotency (ADR 0071) -----
# The headers a replay sends again: what describes the answer, never the quota (a replay uses none) or a retry hint.
REPLAY_HEADERS = frozenset({"x-ratchet-served-by", "x-ratchet-degraded", "cache-control", "x-accel-buffering"})
REPLAYED = "Idempotent-Replayed"


def _request_meta(req: Any, client: str | None, header_store: str | None) -> RequestMeta:
    """What the cascade needs to know about storage and the end user (FR-13.1, FR-13.2): whether the API key
    (RATCHET_KEY_LIMITS `store=0`) or the request (`store: false`, `X-Ratchet-Store: none`, `metadata.ratchet_store`)
    opts out, broadest first, and the end user's hash scoped to the key, with the other hashes a no-train registration
    of that end user may use (`user` beside `safety_identifier`, the every-client scope) and the second identifier's
    hash, logged so a correction must name it (`RequestMeta.end_user_secondary`). A bad `X-Ratchet-Store` is a
    400. The raw `user` / `safety_identifier` goes no further than this function. Zero retention, no-store tasks and
    no-train end users are the cascade's own checks (`Ratchet.store_opt_out`), so they hold for every caller."""
    try:
        header = privacy.store_header(header_store)
    except ValueError as e:  # the value is not quoted back
        raise APIError(400, f"Invalid value for header 'X-Ratchet-Store': {e}.", INVALID_REQUEST, INVALID_VALUE) from e
    ids = privacy.end_user_ids(req.user, req.safety_identifier)
    who: dict[str, Any] = {}
    if ids:
        who = {"end_user": rt.end_users.hash(ids[0], client), "end_user_aliases": rt.end_users.aliases(ids, client)}
        if len(ids) > 1:
            who["end_user_secondary"] = rt.end_users.hash(ids[1], client)
    limits = rt.key_limits.get(client) if client is not None else None
    if limits is not None and not limits.store:
        return RequestMeta(storable=False, store_opt_out="key", **who)
    if privacy.request_opts_out(req.store, req.metadata, header):
        return RequestMeta(storable=False, store_opt_out="request", **who)
    return RequestMeta(**who)


async def _sent(chunks: Iterable[Any]) -> AsyncIterator[Any]:
    """Already rendered chunks as the async iterator a StreamingResponse sends without a thread hop per chunk."""
    for chunk in chunks:
        yield chunk


class BufferedStream(StreamingResponse):
    """A stream whose Server-Sent Events were all rendered before the first is sent (the cascade decides before
    streaming, so nothing is lost by it), kept in `events` so the answer can be stored for an Idempotency-Key and sent
    again event for event. Only a request with a key is rendered this way; a stream that is produced while it is sent
    is never stored (`_cacheable`), so its key is released."""

    def __init__(self, events: list[str], headers: Mapping[str, str] | None = None) -> None:
        self.events: tuple[str, ...] = tuple(events)
        super().__init__(_sent(self.events), media_type="text/event-stream", headers=headers)


# The fields of the request's log line a replay's line repeats: what answered, never what it cost or how long it took,
# so a sum over the chat log lines counts each cascade once.
REPLAY_LOG_FIELDS = ("served_by", "route", "version")


def _cacheable(response: Response, ctx: obs.RequestContext | None) -> idem.Cached | None:
    """The answer to keep for an Idempotency-Key: a 200 JSON body or a buffered stream, with the headers in
    REPLAY_HEADERS, the request's task and the log fields a replay repeats (REPLAY_LOG_FIELDS, and `replay_of`: the id of
    the request that ran); None for anything else, which releases the key."""
    if response.status_code != 200:
        return None
    if isinstance(response, BufferedStream):
        body, media, stream = tuple(e.encode("utf-8") for e in response.events), "text/event-stream", True
    elif isinstance(response, JSONResponse):
        body, media, stream = (bytes(response.body),), "application/json", False
    else:
        return None
    headers = tuple((k, v) for k, v in response.headers.items() if k.lower() in REPLAY_HEADERS)
    task = ctx.task if ctx is not None else None
    log: dict[str, Any] = {}
    if ctx is not None:
        result = ctx.result or {}
        log = {k: result.get(k) for k in REPLAY_LOG_FIELDS} | {"replay_of": ctx.request_id}
    return idem.Cached(body, media, stream, headers, task, log)


def _replay(cached: idem.Cached, ctx: obs.RequestContext | None, retry_count: int = 0) -> Response:
    """A stored answer sent again with `Idempotent-Replayed: true`: the same body (the same completion id, created time
    and `ratchet` block, whose request_id names the request that ran) as JSON or as the same events. No cascade, ledger
    row, quota or tick. Its own log line says what it replayed (`replay_of`) at a cost of 0, with this attempt's
    `retry_count`; it is counted in ratchet_idempotent_requests_total, not in ratchet_requests_total, since no cascade
    ran."""
    if ctx is not None:
        ctx.task = cached.task
        ctx.result = {**cached.log, "cost_usd": 0.0, "retry_count": retry_count, "idempotent_replay": True}
        ctx.counted = True
    headers = {**dict(cached.headers), REPLAYED: "true"}
    if cached.stream:
        return StreamingResponse(_sent(cached.body), media_type=cached.media_type, headers=headers)
    return Response(cached.body[0], media_type=cached.media_type, headers=headers)


def _complete_idempotent(
    raw: bytes,
    header_task: str | None,
    client: str | None,
    key: str,
    retry_count: int,
    header_store: str | None = None,
    rate: Decision | None = None,
    deployment: str | None = None,
) -> Response:
    """A chat request with an Idempotency-Key (FR-1.70, ADR 0071), keyed by the API key's name and the client's key. The
    first runs the cascade (`_complete`) and its answer is kept for RATCHET_IDEMPOTENCY_TTL_S; the same key and body
    while it runs waits for it (at most RATCHET_TEACHER_DEADLINE_S, and at most idem.MAX_WAITERS_PER_KEY at once; past
    either, a 409 the SDKs retry) and gets the same answer; once it finished, the answer is sent again at once
    (`_replay`). Another body with the key is a 400 no retry can fix. A failed first request releases the key, so the
    client's retry runs again; an error is never kept. On the Azure route the deployment is part of the request the key
    stands for."""
    ctx = obs.current()
    cache = rt.idem  # this runtime's, even if a demo reset replaces it while the cascade runs
    try:
        key = idem.check_key(key)
    except ValueError as e:
        metrics.IDEMPOTENT.inc("invalid")
        message = f"Invalid Idempotency-Key header: {e}."
        raise APIError(400, message, INVALID_REQUEST, "invalid_idempotency_key", headers=NO_RETRY) from e
    body = parse_body(raw)  # bad JSON is a 400 before the key is claimed
    try:  # a body nested just under what parse_body accepts can be too deep to encode again: the same 400, never a 500
        fingerprint = idem.fingerprint(body, header_task, deployment)
    except RecursionError as e:
        raise APIError(400, "The request body is not valid JSON: it is nested too deeply.", code=INVALID_JSON) from e
    except ValueError as e:
        raise APIError(400, f"The request body is not valid JSON: {e}.", code=INVALID_JSON) from e
    try:
        claim = cache.begin(client or "", key, fingerprint, rt.settings.teacher_deadline_s)
    except idem.KeyReused as e:
        metrics.IDEMPOTENT.inc("reused")
        message = "This Idempotency-Key was already used with a different request; send a new key for a new request."
        raise APIError(400, message, INVALID_REQUEST, "idempotency_key_reused", headers=NO_RETRY) from e
    except idem.InFlight as e:
        metrics.IDEMPOTENT.inc("in_flight")
        message = "The request with this Idempotency-Key is still being answered; retry shortly to get its answer."
        raise APIError(409, message, INVALID_REQUEST, "idempotency_key_in_use", headers={"Retry-After": "1"}) from e
    if claim.cached is not None:
        metrics.IDEMPOTENT.inc("joined" if claim.joined else "replayed")
        return _replay(claim.cached, ctx, retry_count)
    cached: idem.Cached | None = None
    try:
        response = _complete(
            raw,
            header_task,
            client,
            header_store,
            retry_count=retry_count,
            body=body,
            idempotency_key=key,
            rate=rate,
            deployment=deployment,
        )
        cached = _cacheable(response, ctx)
    except BaseException:
        metrics.IDEMPOTENT.inc("failed")
        raise
    finally:
        cache.finish(claim, cached)  # wakes every request waiting for it; None releases the key
    metrics.IDEMPOTENT.inc("first" if cached is not None else "uncached")
    return response


def _complete(
    raw: bytes,
    header_task: str | None,
    client: str | None,
    header_store: str | None = None,
    retry_count: int = 0,
    body: Any = None,
    idempotency_key: str | None = None,
    rate: Decision | None = None,
    deployment: str | None = None,
) -> Response:
    """The chat request after its key and body were read: validation, the chat path (`_serve`) and the response.
    `retry_count` is the SDK's attempt number; `body`, when given, is `raw` already parsed (by `_complete_idempotent`);
    `rate` is the decision `_rate` took, for its headers; `deployment` is the Azure route's."""
    req = validate_chat_request(parse_body(raw) if body is None else body, rt.limits)
    served = _serve(
        req,
        header_task,
        client,
        deployment,
        header_store=header_store,
        retry_count=retry_count,
        idempotency_key=idempotency_key,
        rate=rate,
    )
    return _chat_response(req, served, buffered=idempotency_key is not None)


class Served(NamedTuple):
    """One request answered by the chat path (`_serve`): the cascade's result, the task it was answered as, the response
    headers (quota, `X-Ratchet-*`), the `ratchet` block, the served answer's `usage` in the chat shape and the completion
    id (`chatcmpl-` and the request id's digits, ADR 0062)."""

    result: Result
    task: tasks.Task
    headers: dict[str, str]
    meta: dict[str, Any]
    usage: dict[str, Any]
    cid: str


def _schema_auto(params: GenParams) -> bool:
    """Whether a request's response_format names its task (ADR 0042): a json_schema with a schema, while auto tasks are
    on (RATCHET_MAX_AUTO_TASKS above 0). `Ratchet.task_for` then derives (or finds) its auto task."""
    contract = schema_tasks.contract_for(params.response_format)
    return (
        contract is not None
        and contract.kind == "json_schema"
        and contract.schema is not None
        and (rt.ratchet.max_auto_tasks > 0)
    )


def _serve(*args: Any, **kwargs: Any) -> Served:
    """`_serve_held` with the daily-quota room it holds given back when the request ends, counted or not."""
    holds: list[Hold] = []
    try:
        return _serve_held(*args, holds=holds, **kwargs)
    finally:
        for hold in holds:
            hold.release()


def _serve_held(
    req: ChatRequest,
    header_task: str | None,
    client: str | None,
    deployment: str | None = None,
    *,
    need: int = 1,
    header_store: str | None = None,
    retry_count: int = 0,
    idempotency_key: str | None = None,
    rate: Decision | None = None,
    own_id: bool = False,
    holds: list[Hold] | None = None,
) -> Served:
    """The chat path after validation, the same for /v1/chat/completions, /v1/completions and /v1/responses (FR-1.51):
    the storage opt-outs and end user, normalisation, the model check, task resolution and admission (and the key's own
    task scope), the daily quota (`need` requests of it, for a batch of prompts) and the budgets, the cascade and the
    response's metadata. Each endpoint only builds its own body from the result. `own_id` gives the request an id of its
    own rather than the HTTP request's (a batch's later prompts, each logged as a request of its own)."""
    ctx = obs.current()  # the request's context, copied into this worker thread (ADR 0062)
    meta_in = _request_meta(req, client, header_store)
    # The request's id ('req_' + 32 hex), logged with its row and returned as `ratchet.request_id` and x-request-id; the
    # completion id carries the same digits, so either finds the request (FR-13.30, ADR 0062). The request context's
    # ids when there is one; the same format otherwise.
    if ctx is not None and not own_id:
        meta_in.request_id, cid = ctx.request_id, ctx.completion_id
    else:
        digits = uuid.uuid4().hex
        meta_in.request_id, cid = feedback.REQUEST_PREFIX + digits, feedback.COMPLETION_PREFIX + digits
    # The client's own id and the SDK's attempt number go into the ledger row and failures rows too
    # (requests.client_request_id, requests.retry_count; a retry never bumps an example's seen, ADR 0071).
    meta_in.client_request_id = ctx.client_request_id if ctx is not None else None
    meta_in.retry_count = retry_count
    meta_in.idempotency_key = idempotency_key
    messages, _ = normalize_messages(req.messages, rt.modalities, rt.settings.merge_system)
    limits = rt.limits_for(client)
    params = GenParams.from_request(req)  # the legacy `functions` and `function_call` arrive as tools (FR-1.41)
    if uses_tools(req):  # the teacher answers alone; legacy turns go in the form open-model servers render
        messages = legacy_history(messages)
    # The model field names a task (or, under the strict policy, is refused); it never picks an upstream model (ADR 0048).
    model_alias = check_model(req.model, rt.routing) if deployment is None else None
    name: str | None
    name, source = resolve_task(req, header_task, rt.routing, client=client, deployment=deployment)
    if name in tasks.RESERVED_NAMES:  # `embeddings`: where /v1/embeddings logs its requests, never a chat task (FR-1.51)
        message = f"The task name '{name}' is reserved: /v1/embeddings logs its requests under it. Name another task."
        raise APIError(400, message, INVALID_REQUEST, "reserved_task", TASK_PARAMS.get(source))
    if source in ("auto_system", "default") and _schema_auto(params):  # a schema's auto task before the system prompt's
        name, source = None, "response_format"
    auto = rt.auto_task(name) if source == "auto_system" and name is not None else None
    no_room = source == "auto_system" and auto is None
    if no_room:  # RATCHET_MAX_AUTO_TASKS reached: `default`, as without auto tasks
        name, source = "default", "default"
    # Refused before the quota, so a refusal costs nothing.
    try:
        task = rt.ratchet.task_for(auto if auto is not None else name, client, params)
    except tasks.UnknownTask as e:  # RATCHET_UNKNOWN_TASKS=reject; or teacher/reject and a header that is not a task name
        if limits is not None and limits.tasks is not None:  # list only the tasks this key's scope allows
            allowed = [n for n in tasks.names_for(client) if limits.allows_task(n)]
            e = tasks.UnknownTask(e.name, allowed, invalid=not tasks.valid_name(e.name))
        raise APIError(400, str(e), INVALID_REQUEST, "unknown_task", TASK_PARAMS.get(source)) from e
    except tasks.TaskForbidden as e:  # the task's `clients` do not name this key (threat model T12)
        raise APIError(403, "task not allowed for this key", "permission_error", "task_not_allowed") from e
    if ctx is not None:
        ctx.task = task.name
    if limits is not None and not limits.allows_task(task.name):  # the key's own scope (RATCHET_KEY_LIMITS tasks=)
        raise APIError(403, "task not allowed for this key", "permission_error", "task_not_allowed")
    use = rt.meter.usage(client)  # memory only: the quota and budget checks run no query (ADR 0063)
    headers = _quota(client, limits, use, need, holds)
    student_only = _budget(client, limits, use)
    if rate is not None and "X-RateLimit-Limit-Requests" not in headers:  # the daily quota's headers win, as since v0.4
        headers.update(_rate_headers(rate))
    if no_room:
        rt.auto_cap_reached()  # reported only now, so a refused request leaves no trace
    if auto is not None and tasks.lookup(auto.name) is not auto:
        kept = rt.keep_auto_task(auto)  # a new auto task is registered and stored only for an admitted request
        if kept.name != auto.name:  # the cap filled meanwhile
            source = "default"
        task = rt.ratchet.task_for(kept, client, params)  # bound to the request's response_format, as any task is
        if ctx is not None:
            ctx.task = task.name
    # A part only the teacher reads (RATCHET_TEACHER_MODALITIES) makes the request ineligible for the student (FR-2.50):
    # the cascade sends it to the teacher (route `ineligible:content_type`) and stores nothing. A tool request goes to
    # the teacher alone inside `handle` too (route `tools`, FR-1.41).
    # A spent gateway budget in student_only mode keeps the request off the teacher (ADR 0063); a key with store=0
    # keeps it out of the training set through `_request_meta` (FR-13.1).
    meta_in.student_only = student_only
    try:  # each model call gets the parameters its role allows (GenPolicy, ADR 0017); n choices (FR-1.14)
        try:
            r = rt.ratchet.handle(messages, task, client=client, params=params, meta=meta_in, n=req.n)
        except BaseException:  # what the failed request spent (the student's samples) counts against the budgets
            if ctx is not None and ctx.spent_usd > 0:
                rt.meter.add(client, ctx.spent_usd, requests=0)
            raise
    except BudgetExceeded as e:  # student_only, and the request needs the teacher
        raise _gateway_budget_error(time.time(), e.need) from e
    except KeyError as e:
        if rt.settings.mode != "mock":  # in live mode a KeyError is a bug, not a client mistake
            raise
        # sim.world.lookup under RATCHET_MOCK_STRICT=1; without it the generic mock world answers every request.
        raise HTTPException(400, f"mock mode only answers demo requests: {e}") from e
    except httpx.HTTPError as e:  # the teacher itself failed, after its retries: nothing left to fall back to
        raise _map_upstream(e) from e
    headers.update(_spend_headers(limits, rt.meter.add(client, r.cost_usd, hold=holds[0] if holds else None)))
    rt.tick()
    meta = {
        "served_by": r.served_by,
        "student_version": r.version,
        "confidence": r.confidence,
        "escalated": r.escalated,
        "cost_usd": r.cost_usd,
        "all_teacher_cost_usd": r.baseline_usd,
        "latency_ms": r.latency_ms,
        "usage_breakdown": r.usage_breakdown,
        "degraded": r.degraded,  # "teacher_unavailable" when the student's below-threshold answer stood in (ADR 0019)
        "stored": r.stored,  # a teacher answer of this request became (or bumped) a training example
        "store_opt_out": r.store_opt_out,  # why it could not be stored (privacy.OPT_OUTS), or null (FR-13.1)
        # What POST /v1/ratchet/feedback names this request by (FR-13.30); also x-request-id and
        # GET /v1/ratchet/requests/{id} (ADR 0062).
        "request_id": meta_in.request_id,
        # The request's ledger row, also X-Ratchet-Request-Id: what GET /ratchet/receipts/{day}/proof/{id} proves (FR-15.5)
        "row_id": r.request_id,
        # The task the request was answered as (named, from its model field, an alias, match rules, an auto task from
        # its schema or system prompt, or default) and the rule that chose it (ADR 0042, ADR 0048).
        "task": r.task,
        "task_source": source,
        "schema_valid": r.schema_valid,  # every choice meets the response_format; null without one (ADR 0042)
        "requested_model": req.model,  # as sent; `model` above is the one that served
    }
    if r.overhead_pending:  # the audit or shadow runs after this response: cost_usd is what serving cost (ADR 0072)
        meta["overhead_pending"] = True
    if ctx is not None:  # the fields of the request's log line (`_log_chat`)
        ctx.result = {
            "served_by": r.served_by,
            "route": r.route,
            "version": r.version,
            "confidence": r.confidence,
            "escalated": r.escalated,
            "audited": r.audited,
            "latency_ms": r.latency_ms,
            "wall_ms": r.wall_ms,
            "cost_usd": r.cost_usd,
            "retry_count": retry_count,
        }
        if r.overhead_pending:  # the request's `offpath` line adds the rest when its job ends (ADR 0072)
            ctx.result["overhead_pending"] = True
    # `usage` is the served answer's, as if its model had answered alone; Ratchet's overhead is in the breakdown and in
    # cost_usd (FR-1.12, docs/API.md#usage).
    usage = usage_object(r.prompt_tokens, r.completion_tokens, r.reasoning_tokens, r.cached_tokens)
    headers["X-Ratchet-Served-By"] = r.served_by
    if r.request_id is not None:  # the ledger row: what this request's savings receipt proves it by (FR-15.5)
        headers["X-Ratchet-Request-Id"] = str(r.request_id)
    headers["X-Ratchet-Task"] = task.name
    if model_alias is not None:  # RATCHET_MODEL_POLICY=lenient served a model id Ratchet does not know as `ratchet`
        headers["X-Ratchet-Model-Alias"] = header_value(model_alias)
    if r.degraded:
        headers["X-Ratchet-Degraded"] = r.degraded
    return Served(r, task, headers, meta, usage, cid)


def _chat_response(req: ChatRequest, served: Served, buffered: bool = False) -> Response:
    """A `chat.completion` (or its stream of chunks) for a request the chat path answered. `buffered` renders a stream
    at once (`BufferedStream`), so the answer can be kept for an Idempotency-Key and replayed (ADR 0071)."""
    r, headers, meta, usage, cid = served.result, served.headers, served.meta, served.usage, served.cid
    created = int(time.time())
    legacy = tool_params(req).legacy  # answer tool calls as `function_call`, as the request asked
    # OpenAI echoes `metadata`; never stored (FR-13.1).
    echo = {"metadata": req.metadata} if req.metadata is not None else {}
    asked = req.logprobs is True  # token logprobs in each choice (FR-1.14)
    if not req.stream:
        choices = [
            {
                "index": i,
                "message": _message(ch, legacy),
                "logprobs": _logprobs(asked, ch.logprobs),
                "finish_reason": _finish(ch, legacy),
            }
            for i, ch in enumerate(r.choices)
        ]
        payload = {
            **_envelope(cid, "chat.completion", created, r.model, r.fingerprint),
            "choices": choices,
            "usage": usage,
            **echo,
            "ratchet": meta,
        }
        return JSONResponse(payload, headers=headers)

    # Streaming. The cascade must see the student's whole answer to score its confidence, so the
    # answer is complete before the first chunk; the stream format is what clients need from it.
    envelope = _envelope(cid, "chat.completion.chunk", created, r.model, r.fingerprint)
    include_usage = req.include_usage

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def chunk(index: int, delta: dict[str, Any], finish: str | None = None, logprobs: Any = None, **extra: Any) -> str:
        ev = {**envelope, "choices": [{"index": index, "delta": delta, "logprobs": logprobs, "finish_reason": finish}]}
        if include_usage:
            ev["usage"] = None  # every chunk but the last has it, as null (OpenAI's stream_options.include_usage)
        return sse({**ev, **extra})

    def events() -> Iterator[str]:
        # One choice after the other: its role chunk, its content, its tool calls (one delta each, FR-1.41), its own
        # finish chunk (FR-1.14). A choice that is only tool calls has null content and no content chunks, as OpenAI's.
        last = len(r.choices) - 1
        for i, ch in enumerate(r.choices):
            text = bool(ch.text) or not ch.tool_calls
            yield chunk(i, {"role": "assistant", "content": "" if text else None, "refusal": None})
            if text:
                for piece, logprobs in _stream_pieces(ch, asked):
                    yield chunk(i, {"content": piece}, logprobs=logprobs)
            for delta in _call_deltas(ch, legacy):
                yield chunk(i, delta)
            finish = _finish(ch, legacy)
            if include_usage or i < last:
                yield chunk(i, {}, finish)
            else:  # the ratchet block (and the echoed metadata) ride on the last finish chunk
                yield chunk(i, {}, finish, **echo, ratchet=meta)
        if include_usage:  # one more chunk with no choices, the usage, the echoed metadata and the ratchet block
            yield sse({**envelope, "choices": [], "usage": usage, **echo, "ratchet": meta})
        yield "data: [DONE]\n\n"

    headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    if buffered:  # rendered at once, so the answer can be kept for the key and replayed (ADR 0071)
        return BufferedStream(list(events()), headers=headers)
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


# ----- human feedback (FR-13.30, ADR 0091) -----
FEEDBACK_KEYS = ("request_id", "verdict", "corrected")
# What a correction carries of the request it corrects: its messages (request rows hold no text), the conditions that
# give it the same example hash as the teacher's answer, and the end user it named (hashed only).
CORRECTION_KEYS = ("messages", "response_format", "tools", "tool_choice", "stop", "user", "safety_identifier")


@app.post("/v1/ratchet/feedback")
async def feedback_post(
    request: Request, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)
) -> dict[str, Any]:
    """A verdict on an answer this gateway served: `{"request_id", "verdict": "correct"|"incorrect"}`, the request id
    from the response's `ratchet.request_id` (or its completion id). With `incorrect`, `corrected` (the right answer)
    and the request's `messages` (and the conditions and end user it carried) store a human-labelled training example,
    unless a storage opt-out or the task's pinning keeps it out; a `correct` verdict never stores anything. An API key
    reaches its own requests only (any other request id is a 404); open where chat completions are."""
    client = _client(authorization, x_api_key)
    raw = await _body(request)
    return await anyio.to_thread.run_sync(functools.partial(_feedback, raw, client))


def _feedback_body(raw: bytes) -> tuple[str, str, str | None, dict[str, Any]]:
    """(request id, verdict, corrected, the correction's request fields) from a feedback body, or a 400."""
    body = parse_body(raw)
    if not isinstance(body, dict):
        raise APIError(400, "The request body must be a JSON object.", INVALID_REQUEST, INVALID_TYPE)
    unknown = sorted(set(body) - set(FEEDBACK_KEYS) - set(CORRECTION_KEYS))
    if unknown:
        message = f"Unknown parameter {_short_name(unknown[0])}; use {', '.join(map(repr, FEEDBACK_KEYS + CORRECTION_KEYS))}."
        raise APIError(400, message, INVALID_REQUEST, "unknown_parameter", param=_short_name(unknown[0]))
    for key in ("request_id", "verdict"):
        if body.get(key) is None:
            raise APIError(400, f"Missing required parameter: '{key}'.", INVALID_REQUEST, MISSING, key)
    rid = feedback.parse_request_id(body["request_id"])
    if rid is None:  # never quoted back
        message = "Invalid value for 'request_id': use the response's ratchet.request_id (req_...) or its id (chatcmpl-...)."
        raise APIError(400, message, INVALID_REQUEST, INVALID_VALUE, "request_id")
    verdict = body["verdict"]
    if verdict not in feedback.VERDICTS:
        message = f"Invalid value for 'verdict': use one of {list(feedback.VERDICTS)}."
        raise APIError(400, message, INVALID_REQUEST, INVALID_VALUE, "verdict")
    corrected = body.get("corrected")
    if corrected is not None and not isinstance(corrected, str):
        raise APIError(400, "Invalid type for 'corrected': expected a string.", INVALID_REQUEST, INVALID_TYPE, "corrected")
    fields = {k: body[k] for k in CORRECTION_KEYS if k in body}
    if corrected is None:
        if fields:
            key = next(iter(fields))
            message = f"'{key}' describes the request a correction is for: send it with 'corrected', or leave it out."
            raise APIError(400, message, INVALID_REQUEST, INVALID_VALUE, key)
        return rid, verdict, None, {}
    if verdict != "incorrect":
        message = "A correction goes with the verdict 'incorrect' only; a 'correct' verdict stores nothing."
        raise APIError(400, message, INVALID_REQUEST, INVALID_VALUE, "corrected")
    if "messages" not in fields:
        message = "Missing required parameter: 'messages'. A correction needs the request's messages (no text is logged)."
        raise APIError(400, message, INVALID_REQUEST, MISSING, "messages")
    limit = rt.limits.max_input_chars
    if limit > 0 and len(corrected) > limit:
        message = f"The correction holds {len(corrected)} characters; this gateway accepts at most {limit}."
        raise APIError(400, message, INVALID_REQUEST, INVALID_VALUE, "corrected")
    return rid, verdict, corrected, fields


def _feedback(raw: bytes, client: str | None) -> dict[str, Any]:
    rid, verdict, corrected, fields = _feedback_body(raw)
    correction = None
    if corrected is not None:
        # The request's fields go through the chat route's own validation and normalisation, so a correction of the same
        # request has the same messages, conditions and example hash as the teacher's answer. Text parts only: a request
        # with an image part was the teacher's alone and is never stored.
        req = validate_chat_request(fields, rt.limits)
        messages, _ = normalize_messages(req.messages, frozenset({TEXT_MODALITY}), rt.settings.merge_system)
        correction = feedback.Correction(
            messages,
            corrected,
            feedback.conditions(rt.ratchet.gen, GenParams.from_request(req)),
            privacy.end_user_ids(req.user, req.safety_identifier),
        )
    limits = rt.key_limits.get(client) if client is not None else None
    try:
        return feedback.submit(
            rt.ledger,
            rt.ratchet,
            rid,
            client,
            verdict,
            correction,
            end_users=rt.end_users,
            key_store=limits is None or limits.store,
            policy=rt.gate.policy,
        )
    except feedback.FeedbackNotFound as e:  # unknown, folded by retention, or another key's: one answer for all three
        message = "No request with that id for this API key."
        raise APIError(404, message, INVALID_REQUEST, "request_not_found", "request_id") from e
    except feedback.FeedbackError as e:
        raise APIError(400, str(e), INVALID_REQUEST, e.code, e.param) from e
    except feedback.FeedbackConflict as e:
        raise APIError(409, str(e), INVALID_REQUEST, e.code, "verdict") from e


# ----- end users who opt out of training (FR-13.2, ADR 0080) -----
NO_TRAIN_KEYS = frozenset({"end_user", "client"})
MOCK_MAX_NO_TRAIN = 1000  # the shared mock demo holds at most this many no-train registrations; a reset clears them


def _no_train_caller(authorization: str | None, x_api_key: str | None) -> tuple[str | None, str | None]:
    """(client, admin) for a no-train call. An admin key (RATCHET_ADMIN_KEYS) gives (None, its name). Otherwise the API
    key's name, as for chat completions: 401 when keys are on and this one is wrong; (None, None) when keys are off, the
    keyless scope every unauthenticated request's end users are hashed in."""
    key = bearer(authorization, x_api_key)
    if rt.admin_keys.enabled and key:
        name = rt.admin_keys.identify(key)
        if name is not None:
            return None, name
    return _client(authorization, x_api_key), None


def _short_name(key: str) -> str:
    return key if len(key) <= 64 else key[:61] + "..."


def _no_train_body(raw: bytes, extra: frozenset[str] = frozenset()) -> tuple[str, str | None, bool, dict[str, Any]]:
    """(end user, client, whether `client` was given, the body) from a no-train body (or an erasure's, which also takes
    `extra` keys), or a 400. Only an admin names a client."""
    body = parse_body(raw)
    if not isinstance(body, dict):
        raise APIError(400, "The request body must be a JSON object.", INVALID_REQUEST, INVALID_TYPE)
    unknown = sorted(set(body) - NO_TRAIN_KEYS - extra)
    if unknown:
        also = "".join(f", '{k}'" for k in sorted(extra))
        message = f"Unknown parameter {_short_name(unknown[0])}; use 'end_user'{also} (and 'client' with an admin key)."
        raise APIError(400, message, INVALID_REQUEST, "unknown_parameter", param=_short_name(unknown[0]))
    end_user = body.get("end_user")
    if end_user is None:
        raise APIError(400, "Missing required parameter: 'end_user'.", INVALID_REQUEST, MISSING, "end_user")
    if not isinstance(end_user, str):
        raise APIError(400, "Invalid type for 'end_user': expected a string.", INVALID_REQUEST, INVALID_TYPE, "end_user")
    if not end_user:  # any length, hashed whole, as `user` and `safety_identifier` are; never quoted back
        message = "Invalid value for 'end_user': the value sent as 'user' or 'safety_identifier', not empty."
        raise APIError(400, message, INVALID_REQUEST, INVALID_VALUE, "end_user")
    client = body.get("client")
    if client is not None and not isinstance(client, str):
        raise APIError(400, "Invalid type for 'client': expected a string.", INVALID_REQUEST, INVALID_TYPE, "client")
    return end_user, client, "client" in body, body


def _no_train_scopes(client: str | None, admin: str | None, named: str | None, given: bool) -> list[str | None]:
    """The key scopes a no-train call (or a client's erasure) acts on. A client acts on its own end users only (403 for
    another client's). An admin acts on the client it names, or with none on every API key's end users and the keyless
    scope's (a no-train registration then uses the every-client scope instead, `_no_train_add`)."""
    if admin is None:
        if given and named != client:
            raise HTTPException(403, "an API key manages its own end users only; an admin key may name another client")
        return [client]
    if given:
        if named is not None and named not in rt.keys.names:
            raise APIError(400, "Invalid value for 'client': no API key has that name.", INVALID_REQUEST, INVALID_VALUE, "client")
        return [named]
    return [*rt.keys.names, None]


@app.post("/v1/ratchet/no-train")
async def no_train_add(
    request: Request, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)
) -> dict[str, Any]:
    """Register an end user (`{"end_user": "<user or safety_identifier>"}`) as no-train: from now on that user's
    requests are answered as usual but never stored (FR-13.2), whether a request names the user in `user` or in
    `safety_identifier`. Only the HMAC is kept, and it is not returned: the endpoint must not become a way to compute
    hashes. Open where chat completions are (an opt-out only removes storage); an admin may name a `client`, or
    register the user for every client, also those added later (the every-client scope). Whether the end user was
    registered before (`created`) is told to an API key or an admin key only: without keys anyone may call this."""
    client, admin = _no_train_caller(authorization, x_api_key)
    raw = await _body(request)
    return await anyio.to_thread.run_sync(functools.partial(_no_train_add, raw, client, admin))


def _no_train_add(raw: bytes, client: str | None, admin: str | None) -> dict[str, Any]:
    end_user, named, given, _ = _no_train_body(raw)
    # An admin's registration for every client is one hash in the every-client scope, so keys added later are covered.
    scopes = [privacy.EVERY_CLIENT] if admin is not None and not given else _no_train_scopes(client, admin, named, given)
    entries = rt.end_users.hashes(end_user, scopes)
    limit = MOCK_MAX_NO_TRAIN if rt.settings.mode == "mock" else None
    try:
        created = rt.ledger.no_train_add(entries, admin=admin, limit=limit)
    except NoTrainFull as e:
        raise HTTPException(409, f"the mock demo holds at most {e.limit} no-train registrations; reset it") from e
    who = f"admin {admin}" if admin is not None else (client or "a client without a key")
    if any(created):  # counts and names only: never the end user or its hash (/ratchet/stats is public)
        rt.ledger.event("no_train", f"{sum(created)} end user registration(s) by {who}")
    told = client is not None or admin is not None  # a keyless caller is not told whether it was registered before
    return {
        "object": "ratchet.no_train",
        "end_users": [{"client": c, "created": new if told else None} for (_, c), new in zip(entries, created, strict=True)],
    }


@app.delete("/v1/ratchet/no-train")
async def no_train_remove(
    request: Request, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)
) -> dict[str, Any]:
    """Remove a no-train registration (the same JSON body as POST; never a query string, which access logs keep). An API
    key removes its own end users' registrations, not one an admin made; an admin removes any: for a named `client`, or
    with none in every scope (each configured key, the keyless one, every scope a registration is stored in and the
    every-client one). While an admin's every-client registration stays, the other scopes report `kept`. Without API
    keys this needs an admin key in live mode, so nobody can switch storage back on for someone else's users; the
    mock demo's keyless callers get no status, as they get no `created` from POST."""
    client, admin = _no_train_caller(authorization, x_api_key)
    if admin is None and not rt.keys.enabled and rt.settings.mode != "mock":
        raise HTTPException(403, "removing a no-train registration needs an API key or an admin key")
    raw = await _body(request)
    return await anyio.to_thread.run_sync(functools.partial(_no_train_remove, raw, client, admin))


def _no_train_remove(raw: bytes, client: str | None, admin: str | None) -> dict[str, Any]:
    end_user, named, given, _ = _no_train_body(raw)
    scopes = _no_train_scopes(client, admin, named, given)
    if admin is not None and not given:  # every scope, also a renamed key's, and the every-client registration
        scopes = list(dict.fromkeys([*scopes, *rt.ledger.no_train_scopes_stored(), privacy.EVERY_CLIENT]))
    entries = rt.end_users.hashes(end_user, scopes)
    every = rt.end_users.hash(end_user, privacy.EVERY_CLIENT)
    with rt.ledger.locked():  # the removal and the every-client check see the same rows
        outcomes = rt.ledger.no_train_remove([h for h, _ in entries], admin=admin is not None)
        covered = rt.ledger.is_no_train(every)
    if "removed" in outcomes:
        who = f"admin {admin}" if admin is not None else (client or "a client without a key")
        rt.ledger.event("no_train", f"{outcomes.count('removed')} end user registration(s) removed by {who}")
    if covered:  # still no-train in every scope while an admin's every-client registration stays
        outcomes = [o if h == every else "kept" for (h, _), o in zip(entries, outcomes, strict=True)]
    told = client is not None or admin is not None
    return {
        "object": "ratchet.no_train",
        "end_users": [
            {"client": c, "status": status if told else None} for (_, c), status in zip(entries, outcomes, strict=True)
        ],
    }


# ----- erasure by the client (FR-13.3, ADR 0081) -----
ERASE_EXTRA_KEYS = frozenset({"dry_run", "reason"})
CLIENT_ERASE_REASON = "end user request"
# What a keyless caller is not told: whether, and how much, an end user had stored (no membership oracle on an open
# endpoint, as no-train's `created`).
ERASE_COUNTS = ("matched", "purged", "kept", "requests_unlinked", "affected_versions")


@app.delete("/v1/ratchet/examples")
async def erase_examples(
    request: Request, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)
) -> dict[str, Any]:
    """Erase the stored examples of one of the caller's end users (`{"end_user": "<user or safety_identifier>"}`, as
    for no-train; optional `reason` and `dry_run`, default false): their text goes for good, their hashes are never
    stored again, and the end user's hash is cleared from its request rows (FR-13.3). An API key erases its own end
    users; an admin key a named client's, or with no `client` every client's (each configured key, every scope the
    ledger holds end users in, and the keyless one). Open where chat completions are, like registering no-train: it
    only removes data. A keyless caller (API keys off) gets the counts as null, so the endpoint cannot tell anyone
    whether an identifier had stored examples. The end user is read from the body only: a URL with `end_user` is a 400,
    because access logs keep URLs."""
    if "end_user" in request.query_params:  # never echoed; the value may identify someone
        message = "Send 'end_user' in the JSON body; it is never read from the URL, which access logs keep."
        raise APIError(400, message, INVALID_REQUEST, "end_user_in_url", "end_user")
    client, admin = _no_train_caller(authorization, x_api_key)
    raw = await _body(request)
    return await anyio.to_thread.run_sync(functools.partial(_erase_examples, raw, client, admin))


def _erase_examples(raw: bytes, client: str | None, admin: str | None) -> dict[str, Any]:
    end_user, named, given, body = _no_train_body(raw, ERASE_EXTRA_KEYS)
    dry_run, reason = body.get("dry_run", False), body.get("reason", CLIENT_ERASE_REASON)
    if not isinstance(dry_run, bool):
        raise APIError(400, "Invalid type for 'dry_run': expected a boolean.", INVALID_REQUEST, INVALID_TYPE, "dry_run")
    if not isinstance(reason, str):
        raise APIError(400, "Invalid type for 'reason': expected a string.", INVALID_REQUEST, INVALID_TYPE, "reason")
    scopes = _no_train_scopes(client, admin, named, given)
    if admin is not None and not given:
        scopes = _every_end_user_scope()
    try:
        result = privacy.erase(
            rt.ledger,
            client=scopes[0] if len(scopes) == 1 else None,
            end_user_raw=end_user,
            actor=admin if admin is not None else (client or "keyless"),
            reason=reason,
            dry_run=dry_run,
            shared=rt.retention.erasure_shared,
            hasher=rt.end_users,
            end_user_clients=scopes,
        )
    except ValueError as e:  # the message never quotes the end user
        raise APIError(400, f"Invalid erasure: {e}.", INVALID_REQUEST, INVALID_VALUE) from e
    if client is None and admin is None:  # a keyless caller is not told whether the end user had anything stored
        result.update({k: None for k in ERASE_COUNTS})
    return result


def _every_end_user_scope() -> list[str | None]:
    """The scopes an admin's erasure of an end user for every client covers: each configured API key name, every scope
    the ledger holds end users in (a renamed or removed key's rows too, `Ledger.end_user_scopes`) and the keyless one."""
    stored = {c for c in rt.ledger.end_user_scopes() if c is not None}
    return [None, *sorted(set(rt.keys.names) | stored)]


# ----- the other OpenAI endpoints (FR-1.51, ADR 0050) -----
async def _read_then(request: Request, client: str | None, fn: Callable[..., Any], *args: Any) -> Any:
    """The front door of /v1/chat/completions for the other endpoints, after the key: the request rate (`_rate`, 429),
    the body read and counted (a client that leaves first is a 400), the in-flight caps (`_hold`), then
    `fn(raw, *args, rate=...)` in a worker thread, so neither the body nor the ledger lock blocks the event loop."""
    rate = _rate(request, client)
    raw = await _body(request)
    with _hold(client):  # its in-flight slots are given back when the response is built
        return await anyio.to_thread.run_sync(functools.partial(fn, raw, *args, rate=rate))


@app.post("/v1/embeddings", response_model=None, openapi_extra=CHAT_BODY)
async def embeddings(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Embeddings, passed through to RATCHET_EMBEDDINGS_MODEL: no cascade, no student, nothing stored (`_embed`). Checks
    run in this order: API key (401), the endpoint on (404 `unsupported_endpoint` under RATCHET_EMBEDDINGS_BASE=off), the
    body read, JSON and validation (400), the model (404 under RATCHET_MODEL_POLICY=strict), the daily quota (429)."""
    client = _client(authorization, x_api_key, api_key)
    if rt.embedder is None:
        raise unsupported_endpoint("POST", "/v1/embeddings", "embeddings are turned off here (RATCHET_EMBEDDINGS_BASE=off)")
    return await _read_then(request, client, _embed, client)


def _embed(raw: bytes, client: str | None, rate: Decision | None = None) -> JSONResponse:
    """`_embed_held` with the daily-quota room it holds given back when the request ends, counted or not."""
    holds: list[Hold] = []
    try:
        return _embed_held(raw, client, rate, holds)
    finally:
        for hold in holds:
            hold.release()


def _embed_held(raw: bytes, client: str | None, rate: Decision | None, holds: list[Hold]) -> JSONResponse:
    """One embeddings call to the configured model, whatever `model` the client names (that id never selects an upstream
    model), logged with task `embeddings`, served_by `upstream` and route `passthrough` at the configured model's price
    for its prompt tokens (the upstream's count, else about 4 characters a token). The row counts against the key's
    quota and budgets like any request (in RATCHET_BUDGET_MODE=student_only a spent gateway budget refuses it, as a
    request that needs the teacher); its all-teacher baseline is its own cost, so it moves no savings figure. A failed
    call logs nothing, as a failed chat request does, but what it spent is nothing either: no row, no meter."""
    req = validate_embeddings_request(parse_body(raw), rt.limits)
    embedder = rt.embedder
    if embedder is None:  # checked again: the runtime may have been rebuilt while the body was read
        raise unsupported_endpoint("POST", "/v1/embeddings", "embeddings are turned off here (RATCHET_EMBEDDINGS_BASE=off)")
    cap = getattr(embedder, "max_values", None)  # the mock embedder's, so one request cannot tie up the shared demo
    if cap is not None and len(req.inputs) * (req.dimensions or getattr(embedder, "default_dims", 1)) > cap:
        message = f"The mock demo returns at most {cap} embedding values (inputs x dimensions) per request."
        raise APIError(400, message, INVALID_REQUEST, "unsupported_value", "input")
    served = rt.settings.embeddings_model
    alias = check_embeddings_model(req.model, served, rt.routing)
    ctx = obs.current()
    if ctx is not None:
        ctx.task = EMBEDDINGS_TASK
    limits = rt.limits_for(client)
    use = rt.meter.usage(client)  # memory only, as for chat (ADR 0063)
    headers = _quota(client, limits, use, 1, holds)
    if _budget(client, limits, use):  # student_only: no student answers embeddings
        raise _gateway_budget_error(use.now, EMBEDDINGS_TASK)
    if rate is not None and "X-RateLimit-Limit-Requests" not in headers:
        headers.update(_rate_headers(rate))
    t0 = time.perf_counter()
    try:
        out = embedder.embed(req.inputs, served, req.dimensions)
    except httpx.HTTPError as e:
        raise _map_passthrough(e) from e
    tokens = out.prompt_tokens or sum(approx_tokens(i) if isinstance(i, str) else len(i) for i in req.inputs)
    cost = rt.settings.price(served).cost(tokens, 0)
    if ctx is not None:
        request_id, client_request_id = ctx.request_id, ctx.client_request_id
    else:
        request_id, client_request_id = feedback.REQUEST_PREFIX + uuid.uuid4().hex, None
    rt.ledger.log_request(
        task=EMBEDDINGS_TASK,
        served_by="upstream",
        student_version=None,
        confidence=None,
        escalated=0,
        audited=0,
        audit_agree=None,
        shadow_version=None,
        shadow_serve=None,
        shadow_agree=None,
        latency_ms=out.latency_ms,
        cost_usd=cost,
        baseline_cost_usd=cost,  # no cheaper path was taken, so nothing was saved
        student_cost_usd=0.0,
        teacher_cost_usd=cost,  # hosted per-token spend, the column the cost report reads it from (FR-15.1)
        student_in_tokens=0,
        student_out_tokens=0,
        client=client,
        route=PASSTHROUGH_ROUTE,
        stored=0,
        request_id=request_id,  # what the spend meter reads back at startup (ADR 0063)
        wall_ms=(time.perf_counter() - t0) * 1000,
        client_request_id=client_request_id,
    )
    headers.update(_spend_headers(limits, rt.meter.add(client, cost, hold=holds[0] if holds else None)))
    if ctx is not None:  # the fields of the request's log line (`_log_chat`); no cascade ran, so no request metric
        ctx.result = {"served_by": "upstream", "route": PASSTHROUGH_ROUTE, "cost_usd": cost, "latency_ms": out.latency_ms}
        ctx.counted = True
    headers["X-Ratchet-Served-By"] = "upstream"
    headers["X-Ratchet-Task"] = EMBEDDINGS_TASK
    if alias is not None:  # RATCHET_MODEL_POLICY=lenient: another model id, answered by the configured model
        headers["X-Ratchet-Model-Alias"] = header_value(alias)
    meta = {
        "served_by": "upstream",
        "route": PASSTHROUGH_ROUTE,
        "task": EMBEDDINGS_TASK,
        "cost_usd": cost,
        "all_teacher_cost_usd": cost,
        "latency_ms": out.latency_ms,
        "requested_model": req.model,
        "request_id": request_id,
    }
    data = [{"object": "embedding", "index": i, "embedding": _encoded(v, req.encoding_format)} for i, v in enumerate(out.vectors)]
    body = {
        "object": "list",
        "data": data,
        "model": out.model,
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        "ratchet": meta,
    }
    return JSONResponse(body, headers=headers)


def _encoded(vector: list[float], encoding_format: str) -> list[float] | str:
    """A vector as the client asked: floats, or OpenAI's base64 of little-endian float32 values (what the OpenAI SDKs ask
    for by default, and decode back to floats)."""
    if encoding_format == "base64":
        return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")
    return vector


def _map_passthrough(e: httpx.HTTPError) -> APIError:
    """The client's error for a failed embeddings call, after the provider's retries, mapped by cause as `_map_upstream`
    maps a teacher's (ADR 0014) but with `upstream_*` codes, since no teacher was involved: a cause the client can fix
    (an input over the model's length) is a 400 with the upstream's message, a rate limit a 429 with its wait, a timeout
    a 504, a wrong key or model a 502 `X-Should-Retry: false` names, anything else a 502."""
    u = as_upstream(e)
    if u.status in CLIENT_CAUSED:
        code = u.code or ("request_too_large" if u.status == 413 else None)
        message = u.message or f"The embeddings model rejected the request ({u.label})."
        return APIError(400, message, INVALID_REQUEST, code, u.param, headers=NO_RETRY)
    if u.status in (401, 403):
        message = f"embeddings model authentication failed: {u.label}"
        return APIError(502, message, "api_error", "upstream_auth_error", headers=NO_RETRY)
    if u.status == 404:
        message = f"embeddings model not found: {u.label}; check RATCHET_EMBEDDINGS_MODEL"
        return APIError(502, message, "api_error", "upstream_not_found", headers=NO_RETRY)
    if u.status == 429:
        wait = _client_wait(u.retry_after)
        message = f"embeddings model rate limited: {u.label}; retry after {wait} s"
        return APIError(429, message, "rate_limit_error", "upstream_rate_limited", headers={"Retry-After": str(wait)})
    final = 300 <= u.status < 500 and u.status not in RETRY_STATUSES
    headers: dict[str, str] | None = NO_RETRY if final else None
    if not final and u.retry_after is not None:
        headers = {"Retry-After": str(_client_wait(u.retry_after))}
    if u.status == 504:
        return APIError(504, f"embeddings model timeout: {u.label}", "api_error", "upstream_timeout", headers=headers)
    return APIError(502, f"embeddings model unavailable: {u.label}", "api_error", "upstream_unavailable", headers=headers)


@app.post("/v1/completions", response_model=None, openapi_extra=CHAT_BODY)
async def completions(
    request: Request,
    x_ratchet_task: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> JSONResponse | StreamingResponse:
    """The legacy completions API: each prompt runs the chat path as one user message (`_serve`), with the same checks,
    task resolution, cascade, quota and storage as /v1/chat/completions; `n`, `stream`, `stop`, `max_tokens` and the rest
    are the chat fields of the same name. A batch of prompts needs that many requests of the daily quota, checked before
    the first runs; a batch that stops after its first prompt is an error that names the prompts already answered and
    carries `X-Should-Retry: false` (`_batch_stopped`). A `suffix`, `echo` or `best_of` that would change the answer,
    and token-array prompts, are a 400."""
    client = _client(authorization, x_api_key, api_key)
    return await _read_then(request, client, _legacy_complete, x_ratchet_task, client)


def _legacy_complete(
    raw: bytes, header_task: str | None, client: str | None, rate: Decision | None = None
) -> JSONResponse | StreamingResponse:
    """Run the prompts one after another. A prompt starts only while the batch has used less than the teacher's deadline
    (RATCHET_TEACHER_DEADLINE_S, on `rt.clock`), so one batch holds its worker thread for about two deadlines at most,
    not one per prompt. The first prompt's error is the request's, as a chat request's would be; a later one's (or the
    deadline) is `_batch_stopped`, since the prompts before it are already answered, logged and billed."""
    creq = validate_completion_request(parse_body(raw), rt.limits)
    reqs = creq.requests()
    served: list[Served] = []
    started = rt.clock()
    for i, req in enumerate(reqs):
        if i and rt.clock() - started >= rt.settings.teacher_deadline_s:
            raise _batch_stopped(i, len(reqs), None)
        try:
            # Each prompt is a request of its own in the ledger, with its own id (the first keeps the HTTP request's).
            served.append(_serve(req, header_task, client, need=len(reqs) if i == 0 else 1, rate=rate, own_id=i > 0))
        except (APIError, StarletteHTTPException) as e:
            if not i:
                raise
            raise _batch_stopped(i, len(reqs), e) from e
    return _completion_response(creq, served)


def _batch_stopped(i: int, total: int, cause: APIError | StarletteHTTPException | None) -> APIError:
    """The error for a completions batch that stopped at prompt `i` > 0: the cause's status, type, code and param (a 504
    `batch_deadline_exceeded` when the batch ran out of time), with a message that says which prompts were answered,
    logged and billed, and `X-Should-Retry: false` in place of any `Retry-After`. OpenAI SDKs resend a 429 or 5xx by
    default, and each resend would run, bill and store the earlier prompts again: their examples' `seen` count would
    reach FREQUENT and move golden rows into training (INV-3). The client resends only the prompts that did not run."""
    done, them = ("prompt[0] was", "its answer") if i == 1 else (f"prompt[0] to prompt[{i - 1}] were", "their answers")
    after = f"{done} answered, logged and billed, but this error does not return {them}; resend only the prompts from"
    after += f" prompt[{i}] on."
    if cause is None:
        deadline = rt.settings.teacher_deadline_s
        message = f"The batch stopped before prompt[{i}] of {total}: it ran past RATCHET_TEACHER_DEADLINE_S ({deadline:g} s)."
        return APIError(504, f"{message} {after}", "api_error", "batch_deadline_exceeded", headers=NO_RETRY)
    if isinstance(cause, StarletteHTTPException):
        cause = http_error(cause.status_code, str(cause.detail), headers=cause.headers)
    headers = {k: v for k, v in cause.headers.items() if k.lower() not in ("retry-after", "x-should-retry")}
    message = f"The batch stopped at prompt[{i}] of {total}: {cause.message.rstrip('. ')}. {after}"
    return APIError(cause.status, message, cause.type, cause.code, cause.param, headers={**headers, **NO_RETRY})


def _completion_response(creq: CompletionRequest, served: list[Served]) -> JSONResponse | StreamingResponse:
    """A `text_completion` (or its stream) for the prompts the chat path answered: choice `p * n + j` is prompt p's
    choice j, as OpenAI numbers them; `usage` adds up every prompt's; `ratchet` is the chat path's block for one prompt
    and `{"prompts": [...]}` for several. The headers are the last prompt's (its quota count is the latest), with
    `X-Ratchet-Served-By` `mixed` when the prompts were not all served by the same model."""
    first = served[0].result
    n = creq.chat.n
    headers = dict(served[-1].headers)
    by = {s.result.served_by for s in served}
    headers["X-Ratchet-Served-By"] = by.pop() if len(by) == 1 else "mixed"
    degraded = next((s.result.degraded for s in served if s.result.degraded), None)
    headers.pop("X-Ratchet-Degraded", None)
    if degraded:
        headers["X-Ratchet-Degraded"] = degraded
    usage = usage_object(
        sum(s.result.prompt_tokens for s in served),
        sum(s.result.completion_tokens for s in served),
        sum(s.result.reasoning_tokens for s in served),
        sum(s.result.cached_tokens for s in served),
    )
    meta = served[0].meta if len(served) == 1 else {"prompts": [s.meta for s in served]}
    envelope = {
        "id": f"cmpl-{uuid.uuid4().hex[:24]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": first.model,
        "system_fingerprint": first.fingerprint,
    }
    asked = creq.logprobs is not None
    # (choice index, choice, the prompt's length: where the completion's text offsets start)
    entries = [(p * n + j, ch, len(creq.prompts[p])) for p, s in enumerate(served) for j, ch in enumerate(s.result.choices)]
    if not creq.chat.stream:
        choices = [
            {
                "text": ch.text,
                "index": index,
                "logprobs": legacy_logprobs(ch.logprobs, offset) if asked else None,
                "finish_reason": ch.finish_reason,
            }
            for index, ch, offset in entries
        ]
        return JSONResponse({**envelope, "choices": choices, "usage": usage, "ratchet": meta}, headers=headers)

    include_usage = creq.chat.include_usage

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def chunk(index: int, text: str, finish: str | None = None, logprobs: Any = None, **extra: Any) -> str:
        ev = {**envelope, "choices": [{"text": text, "index": index, "logprobs": logprobs, "finish_reason": finish}]}
        if include_usage:
            ev["usage"] = None
        return sse({**ev, **extra})

    def events() -> Iterator[str]:
        last = len(entries) - 1
        for k, (index, ch, offset) in enumerate(entries):
            pos = offset
            for piece, lp in _stream_pieces(ch, asked):
                yield chunk(index, piece, logprobs=legacy_logprobs(lp["content"], pos) if lp is not None else None)
                pos += len(piece)
            if include_usage or k < last:
                yield chunk(index, "", ch.finish_reason)
            else:  # the ratchet block rides on the last finish chunk, as in a chat stream
                yield chunk(index, "", ch.finish_reason, ratchet=meta)
        if include_usage:
            yield sse({**envelope, "choices": [], "usage": usage, "ratchet": meta})
        yield "data: [DONE]\n\n"

    headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@app.post("/v1/responses", response_model=None, openapi_extra=CHAT_BODY)
async def responses(
    request: Request,
    x_ratchet_task: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> JSONResponse:
    """The stateless subset of the Responses API: the body becomes a chat request (`validate_responses_request`) and
    runs the chat path (`_serve`) with the same checks, task resolution, cascade, quota and storage; the answer comes
    back as a `response` object. Ratchet keeps no response state, so what needs it (`previous_response_id`,
    `conversation`, `background`) and `stream` are a 400 `unsupported_parameter`."""
    client = _client(authorization, x_api_key, api_key)
    return await _read_then(request, client, _respond, x_ratchet_task, client)


def _respond(raw: bytes, header_task: str | None, client: str | None, rate: Decision | None = None) -> JSONResponse:
    rreq = validate_responses_request(parse_body(raw), rt.limits)
    s = _serve(rreq.chat, header_task, client, rate=rate)
    r = s.result
    ch = r.choices[0]
    # A choice's tool calls ({id, type, function}) become function_call items once the gateway returns them.
    output = response_output(ch.text, ch.finish_reason, getattr(ch, "tool_calls", None))
    usage = responses_usage(r.prompt_tokens, r.completion_tokens, r.reasoning_tokens, r.cached_tokens)
    created = int(time.time())
    body = response_object(rreq, created=created, model=r.model, output=output, finish_reason=ch.finish_reason, usage=usage)
    return JSONResponse({**body, "ratchet": s.meta}, headers=s.headers)


@app.post("/v1/moderations", response_model=None)
def moderations(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Not served: a 404 `unsupported_endpoint` that says so and links the endpoint table, so a library that screens
    every request through the base URL fails with a clear reason (after the key check, as every /v1 route)."""
    _client(authorization, x_api_key, api_key)
    raise unsupported_endpoint("POST", "/v1/moderations", "this gateway has no moderation model; screen content before it")


@app.api_route("/v1/responses/{response_id:path}", methods=["GET", "POST", "DELETE"], response_model=None)
def response_state(
    request: Request,
    response_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Retrieving, deleting or cancelling a response, or listing its input items: a 404 `unsupported_endpoint`, because
    Ratchet keeps no response state (each response is built for its request and forgotten)."""
    _client(authorization, x_api_key, api_key)
    why = "Ratchet keeps no response state, so a response cannot be retrieved, deleted, cancelled or listed"
    raise unsupported_endpoint(request.method, "/v1/responses/{response_id}", why)


@app.get("/ratchet/stats")
def stats(request: Request) -> dict[str, Any]:
    """Everything the dashboard shows. Computed at most once per RATCHET_STATS_TTL_S (live mode: 2 s) by one caller at a
    time (`SnapshotCache`), from the ledger's read-only connection on a WAL file, so dashboards never slow requests. A
    scrubbed read (`_scrubbed`: by default an anonymous reader of a public live dashboard) gets a scrubbed copy, with
    client pseudonyms and no URLs or error messages in the events (ADR 0061)."""
    payload = rt.stats_cache.get(_stats_payload)
    if not _scrubbed(request):
        return payload
    return acl.scrub_stats(payload, _pseudonyms(c["client"] for c in payload["clients"]))


def _stats_payload() -> dict[str, Any]:
    led = rt.ledger
    controls = led.flags()  # first: it publishes the runtime pins `tasks` (by-task `pinned`) reads (ADR 0064)
    boms = led.bom_status()
    spend = rt.meter.snapshot()
    mock = rt.settings.mode == "mock"
    return {
        "mode": rt.settings.mode,
        "day": led.day,
        # The other surface's URLs for the header switch (FR-11.30), the shared simulation's limits and the last real
        # upstream call per role (FR-11.31). All three are content-free.
        "links": {"live": rt.links.live, "sim": rt.links.sim},
        "demo": {
            "surface": "simulation" if mock else "live",
            "controls": mock,
            "shared": mock,
            **rt.demo_policy.as_dict(),
            **_janitor.snapshot(),
        },
        "upstream": {
            "recording": not mock,
            "since": None if mock else round(rt.upstream.since, 1),
            "token_factory": upstream.host_of(rt.settings.teacher_base) == "token_factory",
            "calls": [] if mock else rt.upstream.snapshot(),
        },
        "teacher": rt.settings.teacher_model,
        "student_base": rt.settings.student_base_model,
        # The model that answers student calls now: our adapter, or the hosted fallback with the endpoint off (FR-7.40).
        "fallback_student": rt.settings.fallback_student_model,
        "student_serving": rt.student_serving(),
        "days": led.stats_by_day(),
        "tasks": led.stats_by_task(),
        # What the release gate reads for the canary and the live version, from their counts (ADR 0025); null otherwise.
        # The versions Night School trained; the hosted student's row (FR-12.33) is no trained version, and
        # `student_serving` describes it.
        "versions": [
            {**v.__dict__, "bom": boms.get(v.id), "evidence": rt.gate.evidence(v) if v.state in EVIDENCE_STATES else None}
            for v in led.versions()
            if v.state != HOSTED_STATE
        ],
        "events": led.events(last=40),
        # Every savings figure above and in `totals` prices the student per token; `cost` has the GPU-hour view (NFR-11).
        "totals": {**led.totals(), "cost_model": "per_token"},
        "bill_of_materials": led.bill_of_materials(),
        "clients": _clients_with_spend(led.stats_by_client(), spend),
        "privacy": {
            "redaction": rt.settings.redact_pii,
            # How identifiers are replaced (ADR 0082): the default style, tasks that override it, and whether a
            # detector plugin runs. Names only.
            "redaction_style": rt.ratchet.redact_style,
            "redaction_task_styles": {t.name: t.redact_style for t in tasks.registered() if t.redact_style is not None},
            "redaction_plugin": rt.ratchet.detector is not None,
            **led.privacy_stats(),
            **led.opt_out_stats(),
            # No-train registrations whose scope no request can be hashed in now (a renamed or removed key name).
            "no_train_unmatched": sum(led.no_train_unmatched(privacy.no_train_scopes(rt.known_clients())).values()),
            # Storage opt-outs in force (FR-13.1) and where the end-user key lives (FR-13.2); names only.
            "store_examples": rt.settings.store_examples,
            "no_store_tasks": sorted(rt.ratchet.no_store_tasks | {t.name for t in tasks.registered() if not t.store}),
            "no_store_clients": sorted(name for name, lim in rt.key_limits.items() if not lim.store),
            "end_user_key": rt.end_users.source,
            # Retention and erasure (FR-13.3, FR-13.4): the windows in force (0 = forever) and what was removed; counts.
            "example_retention_days": rt.retention.example_days,
            "request_retention_days": rt.retention.request_days,
            "erasure_shared": rt.retention.erasure_shared,
            **led.retention_stats(),
        },
        "security": {
            "api_keys": rt.keys.enabled,
            "daily_requests_per_key": rt.settings.api_key_daily_requests or None,
            "bom_signing": rt.signer is not None,
            "bom_key_id": rt.signer.key_id if rt.signer else None,
            # Who may read this (ADR 0061); `scrubbed` is true in a scrubbed copy (`access.scrub_stats`).
            "admin_keys": rt.admin_keys.enabled,
            "dashboard_keys": rt.dashboard_keys.enabled,
            "public_dashboard": rt.access.public_dashboard,
            "scrubbed": False,
            # The gateway's own limits (ADR 0063): whether any key has one, the per-IP rate and the in-flight cap.
            "key_limits": any(k != KeyLimits() for k in rt.key_limits.values()),
            "ip_rpm": rt.limiter.ip_rpm or None,
            "max_inflight": rt.limiter.max_inflight or None,
        },
        "budget": _budget_block(spend),
        "cost": stats_block(cost_report(led, rt.settings)),
        # Nemotron's thinking mode by role and where the kwargs go (ADR 0130).
        "reasoning": rt.ratchet.reasoning_snapshot(),
        # Human verdicts and labels (FR-13.30, FR-3.54): counts only. Human precision is reported and alerted on, never
        # used to promote or roll back.
        "feedback": {**led.feedback_stats(), "corrections": rt.settings.feedback_corrections},
        # Failures of the last hour by stage (ADR 0062); what failed requests spent is in totals.failed_cost_usd.
        "errors": {"last_hour": led.failure_counts(time.time() - ERRORS_WINDOW_S)},
        # The off-path queue and, per version, what could not go through it (ADR 0072).
        "sidecar": rt.ratchet.sidecar_report(),
        # The operator's runtime switches and task pins (the admin API and CLI, ADR 0064); `audit_rate` null is the
        # configured rate.
        "controls": controls.as_dict(),
        # PRD §8.1's success metrics over the last RATCHET_KPI_WINDOW_DAYS days with traffic, as GET /ratchet/kpis has them.
        "kpis": _kpi_report(),
        # The label-free drift alarm (FR-16.6): streams monitored and how many alarmed; `GET /ratchet/drift` has each.
        "drift": _drift_block(),
        # The teacher watch (FR-16.7): the epoch's checks, the freeze and the contested labels; `GET /ratchet/teacher`.
        "teacher_watch": teacher_watch.stats_block(led, rt.gate.policy),
    }


ERRORS_WINDOW_S = 3600.0  # /ratchet/stats' errors.last_hour


def _drift_block() -> dict[str, Any]:
    """`/ratchet/stats`' `drift`: whether the monitor is on, its threshold and family false-alarm bound, the streams it
    holds (a count here; the list is in `GET /ratchet/drift`) and how many alarmed. Counts only: no row per stream."""
    d = rt.gate.drift_counts()
    return {k: d[k] for k in ("enabled", "threshold", "family_bound", "alarms")} | {"streams": d["monitored"]}


def _clients_with_spend(rows: list[dict[str, Any]], spend: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`stats_by_client` with each key's /v1 spend since 00:00 UTC (the spend meter's) and its daily US dollar cap (null
    without one, or for a name that is no longer a key)."""
    today = spend["clients"]
    out = []
    for row in rows:
        limits = rt.key_limits.get(row["client"])
        cap = limits.daily_usd if limits is not None and limits.daily_usd > 0 else None
        out.append({**row, "spent_today_usd": float(today.get(row["client"], 0.0)), "daily_cap_usd": cap})
    return out


def _budget_block(spend: Mapping[str, Any]) -> dict[str, Any]:
    """/ratchet/stats' `budget`: the gateway's daily /v1 budget (null when off), its mode, what /v1 requests spent since
    00:00 UTC, what is left, whether it is spent, and the shares that write a `budget` event."""
    lp = rt.limit_policy
    cap = lp.daily_budget_usd
    spent = float(spend["gateway_usd"])
    return {
        "daily_usd": cap or None,
        "mode": lp.budget_mode,
        "spent_today_usd": spent,
        "remaining_usd": max(0.0, cap - spent) if cap > 0 else None,
        "exceeded": cap > 0 and spent >= cap,
        "alert_pct": list(lp.alert_pct),
    }


# ----- request lookups and metrics (ADR 0062) -----
def _lookup(request_id: str, client: str | None = None, *, own: bool = False) -> dict[str, Any] | None:
    """A request by the id the gateway gave it (`req_<hex>`, or its completion's `chatcmpl-<hex>`): its `requests` row
    (metadata only; null when it failed before the cascade logged one) and its `failures` rows, or None when there are
    neither. With `own`, only what `client` (the caller's key name, None when keys are off) sent."""
    digits = obs.lookup_hex(request_id)
    if digits is None:
        return None
    rid = obs.REQUEST_PREFIX + digits
    row = rt.ledger.request_record(rid)
    failures = rt.ledger.failures_for(rid)
    if own:
        row = row if row is not None and row["client"] == client else None
        failures = [f for f in failures if f["client"] == client]
    if row is None and not failures:
        return None
    return {"request_id": rid, "request": row, "failures": failures}


@app.get("/ratchet/requests/{request_id}")
def request_lookup(request_id: str, request: Request) -> dict[str, Any]:
    """One request's metadata and failures (`_lookup`), whoever sent it. Read like /ratchet/stats; a scrubbed read gets
    client pseudonyms (ADR 0061)."""
    found = _lookup(request_id)
    if found is None:
        raise HTTPException(404, "no request has this id")
    if not _scrubbed(request):
        return found
    names = _pseudonyms(c["client"] for c in rt.stats_cache.get(_stats_payload)["clients"])
    scrub = acl.scrub_client
    return {
        **found,
        "request": scrub(found["request"], names) if found["request"] is not None else None,
        "failures": [scrub(f, names) for f in found["failures"]],
    }


@app.get("/v1/ratchet/requests/{request_id}")
def own_request(
    request_id: str, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)
) -> dict[str, Any]:
    """One request this API key sent (`_lookup` with `own`): 404 for an id it did not send, so a key cannot tell another
    client's request from one that never existed."""
    client = _client(authorization, x_api_key)
    found = _lookup(request_id, client, own=True)
    if found is None:
        raise APIError(404, "No request with this id was sent with this key.", "not_found_error", "request_not_found")
    return found


def _collect_gauges() -> None:
    """The scrape-time gauges, from the runtime as it is now: each breaker's state, the off-path jobs waiting and running,
    the routed versions' traffic and today's spend per client (since 00:00 UTC, one indexed query per client and
    table)."""
    metrics.INFO.replace([((__version__, rt.settings.mode), 1.0)])
    breakers = rt.ratchet.health()["breakers"]
    metrics.CIRCUIT_OPEN.replace([((name,), 0.0 if snap["state"] == "closed" else 1.0) for name, snap in breakers.items()])
    side = rt.ratchet.sidecar.snapshot()
    metrics.SIDECAR_QUEUE.replace([(("queued",), float(side["queued"])), (("running",), float(side["running"]))])
    led = rt.ledger
    routed = [v for v in (led.live(), led.by_state("canary"), led.by_state("shadow")) if v is not None]
    metrics.VERSION_TRAFFIC.replace([((f"v{v.id}", v.state), float(v.traffic_pct)) for v in routed])
    spend = led.spend_since(_utc_midnight(time.time()), [*rt.keys.names, None])
    metrics.SPEND_TODAY.replace([((metrics.client_label(c),), usd) for c, usd in spend.items()])
    metrics.INFLIGHT.replace([((), float(rt.limiter.inflight))])


@app.get("/metrics", response_class=PlainTextResponse)
def metrics_endpoint(request: Request) -> Response:
    """Prometheus metrics in the text format 0.0.4 (`ratchet/metrics.py`). Read with an admin or dashboard key or
    RATCHET_METRICS_TOKEN, or by anyone when the dashboard is public; a scrubbed read (an anonymous reader of a public
    live dashboard) gets client pseudonyms in the client labels."""
    _collect_gauges()
    relabel: metrics.Relabel | None = None
    if _scrubbed(request):
        names = _pseudonyms(c["client"] for c in rt.stats_cache.get(_stats_payload)["clients"])
        relabel = {"client": lambda v: names.get(v, v if v in ("none", "other") else "client")}
    return Response(metrics.REGISTRY.render(relabel), media_type=metrics.CONTENT_TYPE)


def _kpi_report(days: int | None = None) -> dict[str, Any]:
    """The success metrics (ratchet/kpis.py) of the gateway's ledger, over `days` or RATCHET_KPI_WINDOW_DAYS. The audits
    SM-2 needs are the release gate's own minimum (RATCHET_MIN_AUDITS)."""
    window = rt.kpi_window if days is None else days
    return kpis.report(rt.ledger, window, rt.gate.policy.min_audits, rt.settings.mode)


@app.get("/ratchet/kpis")
def kpi(days: int | None = None) -> dict[str, Any]:
    """PRD §8.1's success metrics (SM-1 to SM-5) and NFR-1's latency target, each with its value, target, whether it is
    met (None when there is not enough data yet) and the counts behind it, computed from the ledger as in the simulator.
    SM-1 to SM-3 and NFR-1 use the last `days` ledger days with traffic (default RATCHET_KPI_WINDOW_DAYS). Public, like
    /ratchet/stats; `simulated` is true in mock mode, where every number comes from mock models."""
    if days is not None and not (1 <= days <= kpis.MAX_WINDOW_DAYS):
        raise HTTPException(400, f"days must be 1 to {kpis.MAX_WINDOW_DAYS}")
    return _kpi_report(days)


@app.get("/ratchet/drift")
def drift(version: int | None = None) -> dict[str, Any]:
    """The label-free drift monitor (FR-16.6, ADR 0032): its threshold, the false-alarm bound per stream and across
    every stream it holds, and each (version, task) stream's test martingale with its trajectory; `version` narrows the
    streams to one version. Read-only: the gateway feeds the monitor after every request."""
    return rt.gate.drift_status(version)


@app.get("/ratchet/bom")
def bom() -> dict[str, Any]:
    """Model bill of materials: proof of which teachers and licenses produced the training data. Read like the dashboard
    API, or by anyone with RATCHET_PUBLIC_BOM=1 (ADR 0061)."""
    return rt.ledger.bill_of_materials()


@app.post("/ratchet/redact")
def redact_preview(
    body: dict[str, Any] = Body(...),
    style: str | None = None,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Show what the redactor would store for a piece of text: in the gateway's style (RATCHET_REDACT_STYLE) or
    `?style=` (ADR 0082). Nothing is logged or stored. The endpoint is public, so the detector plugin, which may be an
    NER model costing tens of milliseconds a call, runs only for a caller with a valid API or admin key; anyone else
    gets the patterns alone (`plugin_skipped`)."""
    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "text is required")
    if len(text) > MAX_REDACT_CHARS:
        raise HTTPException(400, f"text is limited to {MAX_REDACT_CHARS} characters")
    chosen = rt.ratchet.redact_style if style is None else style.strip().lower()
    if chosen not in REDACT_STYLES:
        raise HTTPException(400, f"style must be one of {', '.join(REDACT_STYLES)}")
    key = bearer(authorization, x_api_key)
    keyed = rt.keys.identify(key) is not None or rt.admin_keys.identify(key) is not None  # a wrong key is no key here
    detector = rt.ratchet.detector if keyed else None
    found: Counter[str] = Counter()
    try:
        out = redact(text, found, Pseudonymizer(chosen, detector))
    except RedactionError as e:  # names the plugin's exception class only, never the text
        raise HTTPException(500, str(e)) from None
    return {
        "text": out,
        "found": dict(found),
        "enabled": rt.settings.redact_pii,
        "style": chosen,
        "plugin": detector is not None,
        "plugin_skipped": rt.ratchet.detector is not None and detector is None,
    }


@app.get("/ratchet/bom/public-key")
def bom_public_key() -> dict[str, Any]:
    """The Ed25519 public key that verifies per-adapter BOMs (null when signing is off). Always public."""
    if rt.signer is None:
        return {"alg": bomdoc.ALG, "public_key": None, "key_id": None}
    return {"alg": bomdoc.ALG, "public_key": rt.signer.public_key, "key_id": rt.signer.key_id}


@app.get("/ratchet/versions/{vid}/bom")
def version_bom(vid: int) -> dict[str, Any]:
    """One adapter's bill of materials, written when it was trained. Check it with `python -m ratchet.bom verify`. Read
    like /ratchet/bom."""
    doc = rt.ledger.version_bom(vid)
    if doc is None:
        raise HTTPException(404, f"no bill of materials for version {vid}")
    return doc


@app.get("/ratchet/versions/{vid}/certificate")
def version_certificate(vid: int) -> dict[str, Any]:
    """What a version's golden evals certify about the answers it serves (FR-16.3, ADR 0026): the certificate signed in
    its BOM at training time, the one from the latest re-score (`current`; a live version is re-scored every training
    night), the release gate's evidence from its audits, and what every certificate assumes. Check the signed numbers
    with `python -m ratchet.stats check`."""
    v = rt.ledger.version(vid)
    if v is None:
        raise HTTPException(404, f"no version {vid}")
    doc = rt.ledger.version_bom(vid)
    m = doc.get("manifest") if isinstance(doc, dict) else None
    training = certificate.section(m.get("eval") if isinstance(m, dict) else None)
    current = certificate.section(v.eval)
    if current is not None:
        current["rechecked_day"] = v.eval.get("rechecked_day")
    drift = rt.gate.drift_view(vid)  # an open drift alarm (FR-16.6) makes the status 'suspended'
    return {
        "version": v.id,
        "state": v.state,
        "status": certificate.status(current, training, drift),
        "training": training,
        "current": current,
        "bom": {"sha256": doc.get("sha256"), "signed": bool(doc.get("signature"))} if isinstance(doc, dict) else None,
        "live": rt.gate.evidence(v) if v.state in SERVED_STATES else None,
        "drift": drift,
        "assumptions": list(certificate.ASSUMPTIONS),
    }


# ----- tasks (FR-2.15, ADR 0022) -----
MOCK_ADMIN = "mock-admin"  # the admin's name in mock mode without RATCHET_ADMIN_KEYS
MOCK_MAX_API_TASKS = 50  # the shared mock demo holds at most this many API tasks; a reset clears them


def _admin(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> str:
    """The admin's name for a management call, from `Authorization: Bearer <key>` or `X-API-Key`. With
    RATCHET_ADMIN_KEYS set: 401 for a missing or wrong key, 403 for a regular API key or a (read-only) dashboard key.
    Without them: 403 in live mode (the admin API is off) and `mock-admin` in mock mode, whose demo has nothing to
    protect. Only the name is ever stored or logged (INV-13)."""
    key = bearer(authorization, x_api_key)
    if rt.admin_keys.enabled:
        name = rt.admin_keys.identify(key)
        if name is not None:
            return name
        if rt.keys.identify(key) is not None:
            raise HTTPException(403, "this is an API key; management calls need an admin key (RATCHET_ADMIN_KEYS)")
        if rt.dashboard_keys.identify(key) is not None:
            raise HTTPException(403, "this is a dashboard key, which only reads; management calls need an admin key")
        raise HTTPException(401, "invalid or missing admin key", headers={"WWW-Authenticate": "Bearer"})
    if rt.settings.mode != "mock":
        raise HTTPException(403, "admin API disabled: set RATCHET_ADMIN_KEYS")
    return MOCK_ADMIN


def _task_entry(t: tasks.Task, stats: Mapping[str, Any] | None, live_version: Version | None) -> dict[str, Any]:
    """One task as GET /ratchet/tasks lists it: its spec (`taskspec.document`, which PUT takes back), where it was
    defined, its spec_hash, whether it names fields that can be checked, its traffic, and whether the live version was
    scored under another spec of it (so the teacher answers it until a night re-scores the version)."""
    return {
        **taskspec.document(t),
        "source": t.source,
        "spec_hash": t.spec_hash,
        "verifiable": t.verifiable,
        "requests": int(stats["requests"]) if stats else 0,
        "student_share": float(stats["student_share"]) if stats else 0.0,
        "recalibrating": live_version is not None and rt.ratchet.spec_changed(live_version, t),
        # An operator pinned it to the teacher at runtime (the admin API or CLI, ADR 0064), on top of its spec's `pinned`.
        "runtime_pinned": t.name in rt.ledger.flags().pinned,
    }


def _task_stats() -> dict[str, dict[str, Any]]:
    return {r["task"]: r for r in rt.ledger.stats_by_task()}


def _spec_errors(errors: list[str]) -> JSONResponse:
    return JSONResponse({"detail": "invalid task spec", "errors": errors}, status_code=422)


def _not_yours(t: tasks.Task) -> HTTPException:
    """403 for a task the admin API does not own: the code or the tasks file defines it."""
    if t.source == "file":
        return HTTPException(403, f"task {t.name!r} is defined in RATCHET_TASKS_FILE; edit the file")
    if t.source == "builtin":
        return HTTPException(403, f"task {t.name!r} is built in; use another name (a spec may extend it)")
    return HTTPException(403, f"task {t.name!r} is an {t.source} task; only API and auto tasks can be changed here")


def _scrub_tasks(request: Request, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Task entries as this reader may see them: a scrubbed read (`_scrubbed`) gets the client allow-lists under the
    same pseudonyms /ratchet/stats uses (ADR 0061)."""
    if not _scrubbed(request):
        return entries
    clients = (c["client"] for c in rt.stats_cache.get(_stats_payload)["clients"])
    names = _pseudonyms(clients, (c for e in entries for c in e.get("clients") or ()))
    return [acl.scrub_task(e, names) for e in entries]


@app.get("/ratchet/tasks")
def list_tasks(request: Request) -> dict[str, Any]:
    """Every registered task (`_task_entry`), the unknown-task policy, the registry's cap, and the names requests used
    that are not registered (candidates to register or pin)."""
    stats = _task_stats()
    live_version = rt.ledger.live()
    registered = tasks.registered()
    names = {t.name for t in registered}
    return {
        "unknown_tasks": rt.ratchet.unknown_tasks,
        "max_tasks": tasks.MAX_TASKS,
        "tasks": _scrub_tasks(request, [_task_entry(t, stats.get(t.name), live_version) for t in registered]),
        "unregistered": [
            {"name": n, "requests": int(r["requests"])}
            for n, r in stats.items()
            if n not in names and n not in tasks.RESERVED_NAMES  # /v1/embeddings logs its requests there (FR-1.51)
        ],
    }


@app.get("/ratchet/tasks/{name}")
def get_task(name: str, request: Request) -> dict[str, Any]:
    """One registered task; an API task also says when it was written and by which admin (the key's name)."""
    t = tasks.lookup(name)
    if t is None:
        raise HTTPException(404, f"no registered task is named {name!r}")
    entry = _task_entry(t, _task_stats().get(name), rt.ledger.live())
    saved = rt.ledger.saved_task(name) if t.source in ("api", "auto") else None
    if saved is not None:
        entry.update({k: saved[k] for k in ("created_ts", "updated_ts", "updated_by")})
    return _scrub_tasks(request, [entry])[0]


@app.post("/ratchet/tasks/validate")
async def validate_tasks(request: Request, admin: str = Depends(_admin)) -> dict[str, Any]:
    """A dry run: the errors a body would get, with no side effects. `{"tasks": [...]}` is checked as startup reads
    RATCHET_TASKS_FILE (it may adjust or extend a built-in task, never extend an API task, and must fit in the registry
    with the built-in tasks); one task spec is checked as PUT would take it."""
    raw = await request.body()
    return await anyio.to_thread.run_sync(_validate_tasks, raw)


def _validate_tasks(raw: bytes) -> dict[str, Any]:
    try:
        obj = taskspec.loads(raw)
        if isinstance(obj, Mapping) and "tasks" in obj:  # as `taskspec.install` reads it, before any API task exists
            parsed = taskspec.parse_document(obj, "file", known_clients=rt.known_clients(), fixtures_dir=rt.settings.fixtures_dir)
            errors: list[str] = []
        else:
            parsed = [taskspec.parse_spec(obj, "api", known_clients=rt.known_clients(), fixtures_dir=rt.settings.fixtures_dir)]
            current = tasks.lookup(parsed[0].name)
            yours = current is None or current.source in ("api", "auto")  # what `_put_task` replaces
            errors = [] if yours else [f"name: {_not_yours(current).detail}"]
    except taskspec.SpecError as e:
        return {"valid": False, "errors": e.errors, "tasks": []}
    found = []
    for t in parsed:
        current = tasks.lookup(t.name)
        found.append({"name": t.name, "spec_hash": t.spec_hash, "registered": current.source if current else None})
    return {"valid": not errors, "errors": errors, "tasks": found}


@app.put("/ratchet/tasks/{name}", response_model=None)
async def put_task(name: str, request: Request, admin: str = Depends(_admin)) -> dict[str, Any] | JSONResponse:
    """Create or replace an API task: 200 `{task, spec_hash, changed, created}`, 422 `{errors}` for a bad spec, 403 for
    a built-in or file task, 409 when the registry (or the mock demo's share of it) is full."""
    raw = await request.body()
    return await anyio.to_thread.run_sync(functools.partial(_put_task, name, raw, admin))


def _put_task(name: str, raw: bytes, admin: str) -> dict[str, Any] | JSONResponse:
    if not tasks.valid_name(name):
        return _spec_errors([f"name: {name!r} is not a task name (1 to 64 letters, digits, '_', '.', '-')"])
    try:
        obj = taskspec.loads(raw)
    except taskspec.SpecError as e:
        return _spec_errors(e.errors)
    if not isinstance(obj, Mapping):
        return _spec_errors(["$: must be an object (one task spec)"])
    if "name" in obj and obj["name"] != name:
        return _spec_errors([f"name: the body names {obj['name']!r} but the URL names {name!r}"])
    with rt.tasks_lock:
        current = tasks.lookup(name)
        if current is not None and current.source not in ("api", "auto"):
            raise _not_yours(current)
        try:
            task = taskspec.parse_spec(
                {**obj, "name": name}, "api", known_clients=rt.known_clients(), fixtures_dir=rt.settings.fixtures_dir
            )
        except taskspec.SpecError as e:
            return _spec_errors(e.errors)
        doc = taskspec.document(task)
        # The same document is no change, unless a sql task's fixture text changed (its spec_hash then differs).
        if (
            current is not None
            and current.source == "api"
            and taskspec.document(current) == doc
            and current.spec_hash == task.spec_hash
        ):
            return _put_result(task, changed=False, created=False)
        if (current is None or current.source != "api") and rt.settings.mode == "mock":
            if sum(1 for t in tasks.registered() if t.source == "api") >= MOCK_MAX_API_TASKS:
                raise HTTPException(409, f"the mock demo holds at most {MOCK_MAX_API_TASKS} API tasks; delete one or reset")
        try:
            tasks.register(task)
        except tasks.TaskConflict as e:  # cannot happen under tasks_lock after the check above; refuse it all the same
            raise HTTPException(403, str(e)) from e
        except tasks.RegistryFull as e:
            raise HTTPException(409, str(e)) from e
        try:
            created, old_hash = rt.ledger.save_task(name, doc, task.spec_hash, "api", admin)
        except BaseException:
            tasks.restore(name, current)  # the registry and the table never disagree
            raise
        if created:
            rt.ledger.event("task_registered", f"{name} (spec {task.spec_hash}) by {admin}")
        elif old_hash != task.spec_hash:
            rt.ledger.event("task_changed", f"{name}: spec {old_hash} -> {task.spec_hash} by {admin}")
        else:
            rt.ledger.event(
                "task_changed", f"{name}: spec {task.spec_hash} kept; description, clients, match or privacy by {admin}"
            )
    return _put_result(task, changed=True, created=created)


def _put_result(task: tasks.Task, changed: bool, created: bool) -> dict[str, Any]:
    entry = _task_entry(task, _task_stats().get(task.name), rt.ledger.live())
    return {"task": entry, "spec_hash": task.spec_hash, "changed": changed, "created": created}


@app.delete("/ratchet/tasks/{name}")
def delete_task(name: str, admin: str = Depends(_admin)) -> dict[str, Any]:
    """Remove an API task or an auto task (from a response_format schema, ADR 0042, or a system prompt with
    RATCHET_AUTO_TASK=system_prompt, ADR 0048), which frees its place under RATCHET_MAX_AUTO_TASKS. Its stored examples
    stay, for provenance; requests that name it then get the unknown-task policy (RATCHET_UNKNOWN_TASKS), and a request
    with its schema or system prompt makes it again while there is room."""
    with rt.tasks_lock:
        current = tasks.lookup(name)
        if current is None:
            raise HTTPException(404, f"no registered task is named {name!r}")
        if current.source not in ("api", "auto"):
            raise _not_yours(current)
        tasks.unregister(name)
        try:
            rt.ledger.delete_task(name)
        except BaseException:
            tasks.restore(name, current)
            raise
        kept = rt.ledger.example_count(name)
        rt.ledger.event("task_deleted", f"{name} (spec {current.spec_hash}) by {admin}; {kept} stored examples kept")
    return {"deleted": name, "spec_hash": current.spec_hash, "examples_kept": kept}


# ----- erasure and export for operators (FR-13.3, FR-13.5, ADR 0081) -----
ERASE_KEYS = frozenset({"client", "end_user", "hashes", "contains", "reason", "dry_run"})


@app.post("/ratchet/admin/privacy/erase")
def admin_erase(body: Any = Body(...), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Erase stored examples for good (`privacy.erase`): by `client`, `end_user` (in `client`'s scope, or with no client
    in every API key's scope, every scope the ledger holds end users in and the keyless one), `hashes` and `contains`,
    each narrowing the scope; `reason` is required. A dry run unless `dry_run` is false. Needs an admin key (open in
    mock mode without admin keys, like the other management calls)."""
    if not isinstance(body, Mapping):
        raise HTTPException(400, "the body must be a JSON object")
    unknown = sorted(set(body) - ERASE_KEYS)
    if unknown:
        raise HTTPException(400, f"unknown keys {[_short_name(str(k)) for k in unknown]}; use {sorted(ERASE_KEYS)}")
    dry_run = body.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise HTTPException(400, "dry_run must be true or false")
    client, end_user = body.get("client"), body.get("end_user")
    clients = _every_end_user_scope() if end_user is not None and client is None else None
    try:
        return privacy.erase(
            rt.ledger,
            client=client,
            end_user_raw=end_user,
            hashes=body.get("hashes"),
            contains=body.get("contains"),
            actor=admin,
            reason=body.get("reason"),  # checked by erase: 1 to 500 characters
            dry_run=dry_run,
            shared=rt.retention.erasure_shared,
            hasher=rt.end_users,
            end_user_clients=clients,
        )
    except ValueError as e:  # never quotes an end user or the text to match
        raise HTTPException(400, str(e)) from e


@app.get("/ratchet/admin/export/{kind}")
def admin_export(
    kind: str,
    task: str | None = None,
    split: str | None = None,
    since: str | None = None,
    client: str | None = None,
    admin: str = Depends(_admin),
) -> StreamingResponse:
    """One table as NDJSON (`privacy.export_lines`, FR-13.5): `examples` (those that still have their text, with their
    provenance: teacher, license, license URL, hash, split, seen, redactions, clients), `requests`, `versions` (with
    their BOMs as stored), `events` or `erasures` (with their items). Filters: `task`, `split`, `since` (epoch seconds or
    an ISO 8601 date) and `client`, where the kind has them (400 otherwise). Needs an admin key, like the other
    management calls. End-user hashes are left out; API keys are never stored."""
    try:
        filters = privacy.check_export(kind, task=task, split=split, since=since, client=client)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    headers = {"Content-Disposition": f'attachment; filename="ratchet-{kind}.ndjson"', "Cache-Control": "no-store"}
    return StreamingResponse(privacy.export_lines(rt.ledger, kind, **filters), media_type="application/x-ndjson", headers=headers)


# ----- cost accounting (FR-15.2 to FR-15.4, ADR 0023) -----
MAX_COST_DAYS = 36500
MOCK_MAX_API_INTERVALS = 200  # the shared mock demo holds at most this many endpoint records from the API; a reset clears them
ENDPOINT_KEYS = frozenset({"state", "usd_per_hour", "gpus", "note"})
MAX_NOTE_CHARS = 200


@app.get("/ratchet/cost")
def cost(days: int | None = None) -> dict[str, Any]:
    """The cost report (`ratchet.cost.cost_report`): savings per token and by GPU-hours paid, the break-even volume, the
    crossover with the hosted fallback, observed utilization and a recommendation, for every day or the last `days`
    ledger days. Read like /ratchet/stats (ADR 0061)."""
    if days is not None and not (1 <= days <= MAX_COST_DAYS):
        raise HTTPException(400, f"days must be 1 to {MAX_COST_DAYS}")
    return cost_report(rt.ledger, rt.settings, days=days)


@app.post("/ratchet/cost/endpoint")
def cost_endpoint(body: Any = Body(...), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Record the student endpoint going up or down (`{"state": "up" | "down"}`, optionally `usd_per_hour` per GPU,
    `gpus` and a `note`), as `python -m ratchet.cost endpoint` does. Needs an admin key (open in mock mode without
    admin keys, like the other management calls). `up` while up and `down` while down change nothing (`changed`
    false)."""
    if not isinstance(body, Mapping):
        raise HTTPException(400, "the body must be a JSON object")
    unknown = sorted(set(body) - ENDPOINT_KEYS)
    if unknown:
        raise HTTPException(400, f"unknown keys {unknown}; use {sorted(ENDPOINT_KEYS)}")
    state = body.get("state")
    if state not in ("up", "down"):
        raise HTTPException(400, "state must be 'up' or 'down'")
    rate, gpus, note = body.get("usd_per_hour"), body.get("gpus"), body.get("note")
    # Compared before any conversion, so a 400-digit integer is refused here rather than overflowing a float later.
    if rate is not None and (
        isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= MAX_USD_PER_GPU_HOUR
    ):
        raise HTTPException(400, f"usd_per_hour must be US dollars per GPU-hour, 0 to {MAX_USD_PER_GPU_HOUR:,.0f}")
    if gpus is not None and (isinstance(gpus, bool) or not isinstance(gpus, int) or not 0 <= gpus <= MAX_GPUS):
        raise HTTPException(400, f"gpus must be a whole number from 0 to {MAX_GPUS:,}")
    if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE_CHARS):
        raise HTTPException(400, f"note must be text of at most {MAX_NOTE_CHARS} characters")
    led = rt.ledger
    if rt.settings.mode == "mock" and state == "up":
        if sum(1 for iv in led.compute_intervals("student_endpoint") if iv["source"] == "api") >= MOCK_MAX_API_INTERVALS:
            raise HTTPException(409, f"the mock demo holds at most {MOCK_MAX_API_INTERVALS} endpoint records; reset it")
    interval, changed, summary = record_endpoint(
        led, rt.cost_policy, state, "api", rate, gpus, note=redact(note) if note else None, by=admin
    )
    return {"state": state, "changed": changed, "interval": interval, "summary": summary}


# ----- ledger replay (FR-16.5, ADR 0028) -----
MAX_REPLAY_DAY = 10_000_000  # a ledger day far beyond any real one: larger values are refused, not passed to SQLite


# ----- the teacher watch (FR-16.7, ADR 0033) -----
@app.get("/ratchet/teacher")
def teacher() -> dict[str, Any]:
    """The teacher watch (FR-16.7, ADR 0033): the check epoch, whether Night School is frozen, the model id the teacher
    last reported, the rule (H0's disagreement rate, alpha, the e-value threshold), the epoch's first re-tests with the
    teacher's self-agreement, its 95% interval, the agreement ceiling and the e-value, by source and by task, and the
    contested labels. Public and read-only, like /ratchet/stats."""
    return rt.gate.teacher_status()


def _replay_version(request: Request) -> Version:
    """The one version a replay reads: `?version=` (default: the live version). 400 for no version and no live one, and
    for a value that names several versions or none (`1,2`, `all`, `version` twice): confidence scales differ between
    versions, so a replay never pools them. 404 for a version that does not exist."""
    values = request.query_params.getlist("version")
    if len(values) > 1:
        raise HTTPException(400, "one version at a time: confidence scales differ between versions, so a replay never pools them")
    if not values or values[0] == "":
        v = rt.ledger.live()
        if v is None:
            raise HTTPException(400, "no live version: name one with ?version=")
        return v
    raw = values[0].strip()
    # ASCII digits only: str.isdigit() also accepts superscript and circled digits ('²', '①'), which int() refuses.
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 18:
        raise HTTPException(400, f"version must be one version id (a whole number), not {values[0]!r}; versions are never pooled")
    v = rt.ledger.version(int(raw))
    if v is None:
        raise HTTPException(404, f"no version {int(raw)}")
    return v


def _replay_days(since_day: int | None, until_day: int | None) -> None:
    for name, day in (("since_day", since_day), ("until_day", until_day)):
        if day is not None and not 0 <= day <= MAX_REPLAY_DAY:
            raise HTTPException(400, f"{name} must be a ledger day from 0 to {MAX_REPLAY_DAY:,}")
    if since_day is not None and until_day is not None and since_day > until_day:
        raise HTTPException(400, "since_day must not be after until_day")


def _replay_label() -> str:
    return replay.SIMULATION if rt.settings.mode == "mock" else "live traffic"


def _replay_current(v: Version) -> float:
    """The version's threshold now, as the router holds its answers to it: in live mode without a student endpoint the
    hosted fallback student answers (`Runtime.reset`'s resolver), whose threshold is the configured default; the hosted
    student's row (FR-12.33) always resolves to it."""
    s = rt.settings
    hosted_model = s.mode == "live" and (not s.student_base or v.state == HOSTED_STATE)
    model = s.fallback_student_model if hosted_model else v.adapter_uri
    return rt.ratchet.threshold(v, model)


@contextmanager
def _replaying() -> Iterator[None]:
    """One replay at a time per gateway. A replay reads a version's whole history into memory, so concurrent ones
    would multiply that; a second request meanwhile gets a 429 with `Retry-After` rather than waiting on the worker
    threads that chat completions share."""
    if not rt.replay_lock.acquire(blocking=False):
        raise HTTPException(429, "another replay is running; try again in a moment", headers={"Retry-After": "1"})
    try:
        yield
    finally:
        rt.replay_lock.release()


@app.get("/ratchet/replay")
def replay_version(
    request: Request,
    threshold: float | None = None,
    audit_rate: float | None = None,
    since_day: int | None = None,
    until_day: int | None = None,
) -> dict[str, Any]:
    """Replay one version's logged traffic (FR-16.5, ADR 0028): what happened (`actual`), what would have happened at
    `threshold` (default: the version's threshold now) and `audit_rate` (default: as logged), and the calibrated
    baseline. Read-only and public, like /ratchet/stats: aggregates only, never request text."""
    v = _replay_version(request)
    _replay_days(since_day, until_day)
    try:
        t = None if threshold is None else replay.check_threshold(threshold)
        a = None if audit_rate is None else replay.check_rate(audit_rate)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    with _replaying():
        history = replay.load(rt.ledger, v.id, since_day, until_day)
        out = replay.report(history, t, a, current=_replay_current(v), fallback_rate=rt.settings.audit_rate)
    return {**out, "state": v.state, "label": _replay_label()}


@app.get("/ratchet/replay/frontier")
def replay_frontier(
    request: Request,
    target: float = replay.DEFAULT_TARGET,
    audit_rate: float | None = None,
    since_day: int | None = None,
    until_day: int | None = None,
) -> dict[str, Any]:
    """The cost-precision frontier of one version's logged traffic over thresholds 0.30 to 1.00, the suggested threshold
    for `target` (advisory: an approximate interval, never applied by itself) and the money the current threshold leaves
    on the table (FR-16.5, ADR 0028)."""
    v = _replay_version(request)
    _replay_days(since_day, until_day)
    try:
        target = replay.check_target(target)
        a = None if audit_rate is None else replay.check_rate(audit_rate)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    with _replaying():
        history = replay.load(rt.ledger, v.id, since_day, until_day)
        out = replay.frontier_report(history, _replay_current(v), target, audit_rate=a, fallback_rate=rt.settings.audit_rate)
    return {**out, "state": v.state, "label": _replay_label()}


# ----- committed training sets (FR-16.4, ADR 0027) -----
KEYSET_CACHE = 8  # committed (version, split) trees kept in memory; their leaves never change (INV-17)


def _committed(vid: int, split: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the version's BOM document, the commitment it signs for `split`); 404 for an unknown version or one without a
    commitment (trained before v0.5)."""
    if rt.ledger.version(vid) is None:
        raise HTTPException(404, f"no version {vid}")
    doc = rt.ledger.version_bom(vid)
    committed = bomdoc.commitment(doc.get("manifest") if isinstance(doc, dict) else None, split)
    if doc is None or committed is None:
        raise HTTPException(404, f"commitment not available: version {vid} was trained before set commitments (v0.5)")
    return doc, committed


def _keyset(vid: int, split: str) -> tuple[KeySet, dict[str, Any], dict[str, Any]]:
    """(the `merkle.KeySet` of version `vid`'s committed `split` leaves, the commitment its BOM signs, the BOM). Built
    from `version_leaves` once and kept (`KEYSET_CACHE`, least recently used out), but only when it rebuilds the root and
    size the BOM signs: leaves edited outside Ratchet (the triggers stop any edit through SQLite's API, INV-17) are a 500
    and one `integrity` event, never a proof that would fail against the signed root."""
    doc, committed = _committed(vid, split)
    led, cache = rt.ledger, rt.keysets
    with rt.keysets_lock:
        hit = cache.get((vid, split))
        if hit is not None and hit[0] is led:  # a demo reset makes a new ledger with the same version ids
            cache.move_to_end((vid, split))
            return hit[1], committed, doc
    try:
        keyset = KeySet(led.version_leaves(vid, split))
    except ValueError:  # a malformed or duplicate leaf: only possible in a file edited outside Ratchet
        keyset = None
    if keyset is None or (keyset.root_hex, keyset.size) != (committed["root"], committed["size"]):
        with rt.keysets_lock:
            first = (vid, split) not in rt.integrity_reported
            rt.integrity_reported.add((vid, split))
        if first:
            led.event("integrity", f"v{vid} {split} leaves do not rebuild the root its BOM signs; the ledger was changed")
        raise HTTPException(500, f"the stored {split} leaves of version {vid} do not match the commitment its BOM signs")
    with rt.keysets_lock:
        cache[(vid, split)] = (led, keyset)
        cache.move_to_end((vid, split))
        while len(cache) > KEYSET_CACHE:
            cache.popitem(last=False)
    return keyset, committed, doc


def _split(split: str) -> str:
    if split not in proof.SPLITS:
        raise HTTPException(400, f"split must be one of {list(proof.SPLITS)}")
    return split


@app.post("/ratchet/versions/{vid}/proof")
def version_proof(vid: int, split: str = "train", body: Any = Body(...), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Whether an example is in version `vid`'s committed training (or golden) set, with an O(log n) membership or
    non-membership proof against the root its signed BOM carries (FR-16.4, ADR 0027). The body names the example by
    `key` (its hash) or as the chat request it came from (`task`, `messages`, and any other `/v1/chat/completions`
    field), which is validated, normalised and redacted as the gateway stores it. An admin call: with guessable texts
    the endpoint is a membership oracle for the training data. Check the answer offline with `python -m ratchet.proof
    verify`."""
    _split(split)
    if rt.ledger.version(vid) is None:
        raise HTTPException(404, f"no version {vid}")
    try:
        key = proof.resolve_key(
            body,
            redact_pii=rt.settings.redact_pii,
            policy=rt.ratchet.gen,
            limits=rt.limits,
            modalities=rt.modalities,
            merge_system=rt.settings.merge_system,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    keyset, committed, doc = _keyset(vid, split)
    p = keyset.prove(key)
    out: dict[str, Any] = {
        "version": vid,
        "split": split,
        "key": key,
        "alg": committed["alg"],
        "root": committed["root"],
        "size": committed["size"],
        "result": merkle.verify(keyset.root, key, p),
        "proof": p,
        "bom_sha256": doc.get("sha256"),
    }
    value = keyset.value(key)
    if value is not None:
        out["value"] = value.hex()
    return out


@app.get("/ratchet/versions/{vid}/heldout")
def version_heldout(vid: int, admin: str = Depends(_admin)) -> dict[str, Any]:
    """The held-out certificate of version `vid` (FR-16.4, INV-3, INV-6): its golden set's leaves and, for each golden
    key, a non-membership proof against the training root its signed BOM carries. `python -m ratchet.proof heldout`
    rebuilds the golden root from the leaves and checks every proof, so nobody has to trust that the golden rows were
    never trained on. An admin call, like the proofs."""
    training, train_c, doc = _keyset(vid, "train")
    golden, golden_c, _ = _keyset(vid, "golden")
    keys = golden.keys()
    return {
        "version": vid,
        "alg": train_c["alg"],
        "training": {"root": train_c["root"], "size": train_c["size"]},
        "golden": {"root": golden_c["root"], "size": golden_c["size"], "leaves": [[k.hex(), v.hex()] for k, v in golden.items()]},
        "proofs": {k.hex(): training.prove(k) for k in keys},
        "held_out": not any(k in training for k in keys),
        "bom_sha256": doc.get("sha256"),
    }


# ----- verified savings receipts (FR-15.5, ADR 0029) -----
RECEIPT_TREE_CACHE = 8  # receipt trees kept in memory; a sealed day's rows never change
MAX_PERIOD = 10_000_000  # a ledger day far beyond any real one: larger values are refused, not passed to SQLite


def _receipt_reader(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> str | None:
    """Whose receipts a caller may read: None for every receipt (an admin key, or the open mock demo), else an API
    client's name (its own receipts and proofs only). With keys on (API or admin), a missing or unknown key is a 401; in
    live mode with no keys at all receipts are off (403): their leaves list every request's time, task and cost."""
    if not rt.keys.enabled and not rt.admin_keys.enabled:
        if rt.settings.mode == "mock":
            return None
        raise HTTPException(403, "receipts need RATCHET_API_KEYS or RATCHET_ADMIN_KEYS in live mode")
    key = bearer(authorization, x_api_key)
    if rt.admin_keys.enabled and rt.admin_keys.identify(key) is not None:
        return None
    name = rt.keys.identify(key) if rt.keys.enabled else None
    if name is None:
        raise HTTPException(401, "invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})
    return name


def _receipt_scope(reader: str | None, client: str | None) -> str | None:
    """The receipt a caller reads: for an admin (or the open demo), `client`'s (none: the gateway's); an API client
    reads its own, so `client` may only name it."""
    if reader is None:
        return client or None
    if client not in (None, "", reader):
        raise HTTPException(403, "an API key reads its own receipts only")
    return reader


def _period(period: int) -> int:
    if not 0 <= period <= MAX_PERIOD:
        raise HTTPException(400, f"period must be a ledger day from 0 to {MAX_PERIOD:,}")
    return period


def _receipt(period: int, scope: str | None) -> dict[str, Any]:
    """The sealed receipt of (`period`, `scope`), sealed now if its day has ended (every unsealed day up to it, in
    order); 409 for a day that has not ended, 404 when nobody in scope sent a request that day."""
    led = rt.ledger
    doc = led.receipt(period, scope)
    if doc is None:
        closed = receipts.closed_through(led)
        if period > closed:
            raise HTTPException(409, f"day {period} has not ended yet; its receipt is sealed once it is over")
        receipts.seal_due(led, rt.receipt_config, through=period)
        doc = led.receipt(period, scope)
    if doc is None:
        if led.period_folded(period):  # retention folded the day before anyone sealed it (`receipts._seal_period`)
            raise HTTPException(410, f"the request rows of day {period} were folded by request retention before it was sealed")
        who = f" from {scope}" if scope else ""
        raise HTTPException(404, f"no requests{who} on day {period}, so no receipt")
    return doc


def _receipt_integrity(period: int, scope: str | None) -> HTTPException:
    """The ledger's rows of a sealed day no longer match its receipt: one `integrity` event per (day, client) per
    runtime, and a 500 for the caller, never a proof or leaves that would fail against the signed root."""
    key = (period, scope or "")
    with rt.receipts_lock:
        first = key not in rt.receipt_integrity_reported
        rt.receipt_integrity_reported.add(key)
    if first:
        who = scope or "the gateway"
        rt.ledger.event("integrity", f"receipt of day {period} ({who}): the ledger's rows no longer rebuild its signed root")
    return HTTPException(500, f"the ledger's rows of day {period} no longer match the root its receipt signs")


def _receipt_folded(period: int, scope: str | None) -> HTTPException:
    """A sealed day whose request rows retention has since folded (FR-13.4): its per-request proofs and leaves are gone
    for good, which is no integrity failure; the receipt itself still verifies (its signature, chain and totals)."""
    return HTTPException(
        410,
        f"the request rows of day {period} were folded by request retention (RATCHET_REQUEST_RETENTION_DAYS); "
        "the receipt still verifies, but its leaves and per-request proofs are gone",
    )


def _receipt_leaves(period: int, scope: str | None, doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The receipt's leaves rebuilt from the ledger's rows, in tree order, checked against the root and size the
    receipt signs (`_receipt_integrity` when they do not, or `_receipt_folded` when retention folded the day's rows)."""
    leaves = [receipts.leaf(r) for r in rt.ledger.period_rows(period, scope)]
    tree = receipts.tree(leaves)
    signed = doc["manifest"]["merkle"]
    if (tree.root.hex(), tree.size) != (signed["root"], signed["size"]):
        if rt.ledger.period_folded(period, scope):
            raise _receipt_folded(period, scope)
        raise _receipt_integrity(period, scope)
    return leaves


def _receipt_tree(period: int, scope: str | None, doc: Mapping[str, Any]) -> ReceiptTree:
    """The receipt's ids and tree, kept (`RECEIPT_TREE_CACHE`, least recently used out) only when they rebuild the
    root and size the receipt signs. An entry remembers its ledger and receipt, because a demo reset makes a new ledger
    whose days start again."""
    led, key, cache = rt.ledger, (period, scope or ""), rt.receipt_trees
    with rt.receipts_lock:
        hit = cache.get(key)
        if hit is not None and hit.ledger is led and hit.sha256 == doc.get("sha256"):
            cache.move_to_end(key)
            return hit
    leaves = _receipt_leaves(period, scope, doc)
    entry = ReceiptTree(led, str(doc.get("sha256")), [lf["id"] for lf in leaves], receipts.tree(leaves))
    with rt.receipts_lock:
        cache[key] = entry
        cache.move_to_end(key)
        while len(cache) > RECEIPT_TREE_CACHE:
            cache.popitem(last=False)
    return entry


@app.get("/ratchet/receipts")
def list_receipts(reader: str | None = Depends(_receipt_reader)) -> dict[str, Any]:
    """The sealed savings receipts a caller may read, by day (FR-15.5, ADR 0029), after sealing every day that has
    ended: an admin sees the gateway's (`global`) and every client's, an API client its own only."""
    led = rt.ledger
    receipts.seal_due(led, rt.receipt_config)
    by_day: dict[int, dict[str, Any]] = {}
    for period, client in led.receipt_periods():
        if reader is not None and client != reader:
            continue
        entry = by_day.setdefault(period, {"period": period, "global": False, "clients": []})
        if client is None:
            entry["global"] = True
        else:
            entry["clients"].append(client)
    signer = rt.signer
    return {
        "receipts": [by_day[p] for p in sorted(by_day)],
        "sealed_through": receipts.closed_through(led),
        "public_key": signer.public_key if signer else None,
        "key_id": signer.key_id if signer else None,
        "label": rt.receipt_config.label,
    }


@app.get("/ratchet/receipts/{period}")
def get_receipt(period: int, client: str | None = None, reader: str | None = Depends(_receipt_reader)) -> dict[str, Any]:
    """One day's signed savings receipt: the gateway's, or `client`'s (an API client gets its own). Check it offline
    with `python -m ratchet.receipt verify` and the key from `/ratchet/bom/public-key`."""
    return _receipt(_period(period), _receipt_scope(reader, client))


@app.get("/ratchet/receipts/{period}/proof/{request_id}")
def receipt_proof(
    period: int, request_id: int, client: str | None = None, reader: str | None = Depends(_receipt_reader)
) -> dict[str, Any]:
    """That request `request_id` (`X-Ratchet-Request-Id`) is in the day's receipt: its leaf and RFC 9162 audit path
    against the root the receipt signs. `python -m ratchet.receipt verify r.json --proof p.json` checks it offline."""
    scope = _receipt_scope(reader, client)
    doc = _receipt(_period(period), scope)
    entry = _receipt_tree(period, scope, doc)
    found = receipts.proof_for(entry.ids, entry.tree, request_id)
    if found is None:
        raise HTTPException(404, f"request {request_id} is not in this receipt (another day or client)")
    index, path = found
    signed = doc["manifest"]["merkle"]
    row = rt.ledger.receipt_row(request_id)
    lf = receipts.leaf(row) if row is not None else None
    # The row is read again, so check it against the signed root before serving: a row edited since the tree was cached
    # gets the integrity error, never a proof that fails.
    if lf is None or not merkle.verify_inclusion(receipts.leaf_bytes(lf), index, signed["size"], path, signed["root"]):
        if lf is None and rt.ledger.period_folded(period, scope):
            raise _receipt_folded(period, scope)
        raise _receipt_integrity(period, scope)
    return {
        "period": period,
        "client": scope,
        "request_id": request_id,
        "leaf": lf,
        "index": index,
        "size": signed["size"],
        "path": path,
        "root": signed["root"],
        "alg": signed["alg"],
        "receipt_sha256": doc.get("sha256"),
    }


@app.get("/ratchet/receipts/{period}/leaves")
def receipt_leaves(period: int, client: str | None = None, reader: str | None = Depends(_receipt_reader)) -> Response:
    """The receipt's leaves as JSON lines, in tree order: `python -m ratchet.receipt verify r.json --rows leaves.jsonl`
    rebuilds the root and recomputes every total, estimate and the fee from them."""
    scope = _receipt_scope(reader, client)
    doc = _receipt(_period(period), scope)
    body = "".join(receipts.leaf_bytes(lf).decode() + "\n" for lf in _receipt_leaves(period, scope, doc))
    return PlainTextResponse(body, media_type="application/x-ndjson")


# ----- the admin API (FR-12.1, ADR 0064) -----
# Every route here needs an admin key (`_admin`; `/ratchet/admin/` is an `admin` prefix in access.py) and goes through
# ratchet/admin.py, which the CLI (`python -m ratchet.admin`) shares: the change, an admin_actions row and an `admin`
# event commit together, compare-and-set, and each response has the target's state before and after.
ADMIN_LOG_LIMIT = 50
_ADMIN_SWITCHES: dict[str, tuple[str, str, str]] = {  # path -> (flag, verb that turns it on, verb that turns it off)
    "student": ("student_disabled", "disable", "enable"),
    "releases": ("releases_frozen", "freeze", "unfreeze"),
    "training": ("training_paused", "pause", "resume"),
}


def _admin_body(body: Any, allowed: frozenset[str]) -> Mapping[str, Any]:
    """An admin call's JSON body: an object with only `allowed` keys (400 otherwise); an empty body is {}."""
    if body is None:
        return {}
    if not isinstance(body, Mapping):
        raise HTTPException(400, "the body must be a JSON object")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise HTTPException(400, f"unknown keys {unknown}; use {sorted(allowed)}")
    return body


def _admin_call(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run an admin action with the caller's request id; its AdminError becomes the HTTP status it names."""
    ctx = obs.current()
    try:
        return fn(*args, request_id=ctx.request_id if ctx is not None else None, **kwargs)
    except admin_ops.AdminError as e:
        raise HTTPException(e.status, str(e)) from e


def _adapter_loader(wanted: Any) -> Callable[[Version], object] | None:
    """`load_adapter: true` on a promote: load the version's LoRA adapter into the student endpoint before it takes
    traffic. Mock mode has nothing to load (the mock fleet serves every version); live mode needs RATCHET_STUDENT_BASE
    (409 without it), and a failed load is a 502 that changes nothing."""
    if not isinstance(wanted, bool):
        raise HTTPException(400, f"load_adapter must be true or false, not {wanted!r}")
    s = rt.settings
    if not wanted or s.mode == "mock":
        return None
    if not s.student_base:
        raise HTTPException(409, "RATCHET_STUDENT_BASE is not set: no student endpoint to load the adapter into")

    def load(v: Version) -> None:
        try:
            live.load_adapter(
                s.student_base, s.student_api_key, v.adapter_uri, f"{live.adapter_root()}/{v.adapter_uri}", rt.student_http
            )
        except Exception as e:  # noqa: BLE001 - the class and status only: the message carries the endpoint URL
            raise HTTPException(502, f"loading adapter {v.adapter_uri} failed: {describe_failure(e)}; nothing changed") from e

    return load


@app.get("/ratchet/admin/versions")
def admin_versions(admin: str = Depends(_admin)) -> dict[str, Any]:
    """Every version (newest first) with its golden eval and its current stage's audits or shadow verdicts, and the
    runtime switches (`controls`)."""
    return {"versions": admin_ops.versions(rt.ledger), "controls": rt.ledger.flags().as_dict()}


@app.get("/ratchet/admin/flags")
def admin_flags(admin: str = Depends(_admin)) -> dict[str, Any]:
    """The runtime switches with who set each one last, and the task pins with who set them and why."""
    return admin_ops.flags(rt.ledger)


@app.post("/ratchet/admin/versions/{vid}/rollback")
def admin_rollback(vid: int, body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Roll a canary back (body `{reason}`): its traffic returns to the live version, or the teacher."""
    b = _admin_body(body, frozenset({"reason"}))
    return _admin_call(admin_ops.rollback, rt.ledger, vid, b.get("reason"), f"api:{admin}")


@app.post("/ratchet/admin/versions/{vid}/retire")
def admin_retire(vid: int, body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Retire a live, canary or shadow version (body `{reason}`)."""
    b = _admin_body(body, frozenset({"reason"}))
    return _admin_call(admin_ops.retire, rt.ledger, vid, b.get("reason"), f"api:{admin}")


@app.post("/ratchet/admin/versions/{vid}/promote")
def admin_promote(vid: int, body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Promote a version (body `{reason, to: canary|live, traffic_pct, force, load_adapter}`): to a canary stage, or to
    live over the live version, which is retired in the same transaction."""
    b = _admin_body(body, frozenset({"reason", "to", "traffic_pct", "force", "load_adapter"}))
    load = _adapter_loader(b.get("load_adapter", False))
    return _admin_call(
        admin_ops.promote,
        rt.ledger,
        vid,
        b.get("to"),
        b.get("traffic_pct"),
        b.get("force", False),
        reason=b.get("reason"),
        actor=f"api:{admin}",
        load=load,
    )


def _switch_route(path: str, flag: str, verb: str, on: bool) -> None:
    def route(body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
        b = _admin_body(body, frozenset({"reason"}))
        return _admin_call(admin_ops.set_flag, rt.ledger, flag, on, reason=b.get("reason"), actor=f"api:{admin}")

    route.__name__ = f"admin_{path}_{verb}"
    route.__doc__ = f"Set `{flag}` to {str(on).lower()} (body `{{reason}}`)."
    app.post(f"/ratchet/admin/{path}/{verb}")(route)


for _path, (_flag, _on, _off) in _ADMIN_SWITCHES.items():
    _switch_route(_path, _flag, _on, True)
    _switch_route(_path, _flag, _off, False)


@app.put("/ratchet/admin/settings")
def admin_settings(body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Set the runtime audit rate (body `{audit_rate, reason, force}`; `audit_rate` 0 to 1, or null for
    RATCHET_AUDIT_RATE). 0 while a canary is in flight needs `force`."""
    b = _admin_body(body, frozenset({"audit_rate", "reason", "force"}))
    if "audit_rate" not in b:
        raise HTTPException(400, "audit_rate is required (a number from 0 to 1, or null for the configured rate)")
    return _admin_call(
        admin_ops.set_flag,
        rt.ledger,
        "audit_rate",
        b["audit_rate"],
        reason=b.get("reason"),
        actor=f"api:{admin}",
        force=b.get("force", False),
    )


@app.post("/ratchet/admin/tasks/{name}/pin")
def admin_pin(name: str, body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Pin a registered task to the teacher at runtime (body `{reason}`): nothing of it is stored or trained on."""
    b = _admin_body(body, frozenset({"reason"}))
    return _admin_call(admin_ops.pin_task, rt.ledger, name, reason=b.get("reason"), actor=f"api:{admin}")


@app.delete("/ratchet/admin/tasks/{name}/pin")
def admin_unpin(name: str, body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Remove a task's runtime pin (body `{reason}`); a task its spec pins stays pinned."""
    b = _admin_body(body, frozenset({"reason"}))
    return _admin_call(admin_ops.unpin_task, rt.ledger, name, reason=b.get("reason"), actor=f"api:{admin}")


@app.post("/ratchet/admin/tick")
def admin_tick(body: Any = Body(default=None), admin: str = Depends(_admin)) -> dict[str, Any]:
    """Run the release gate now, as the cron tick does (body `{reason}`, optional), recorded as an admin action; the
    routed versions before and after. The gate's own events say what it decided."""
    b = _admin_body(body, frozenset({"reason"}))
    return _admin_call(admin_ops.tick, rt.ledger, lambda: rt.tick(force=True), f"api:{admin}", b.get("reason"))


@app.get("/ratchet/admin/audit-log")
def admin_audit_log(limit: int = ADMIN_LOG_LIMIT, admin: str = Depends(_admin)) -> dict[str, Any]:
    """The newest admin actions, newest first (`limit` 1 to 1000)."""
    try:
        return {"actions": admin_ops.audit_log(rt.ledger, limit)}
    except admin_ops.AdminError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/ratchet/teacher/ack")
def teacher_ack(admin: str = Depends(_admin)) -> dict[str, Any]:
    """Acknowledge the teacher watch (FR-6.42): a new check epoch, Night School unfrozen, and a `teacher_ack` event naming
    the admin key (never the key). Needs an admin key; open in mock mode without admin keys, like the other management
    calls. Check the teacher's model id first (docs/RUNBOOK.md)."""
    epoch = rt.ledger.teacher_ack(admin)
    return {"epoch": epoch, "frozen": rt.ledger.teacher_state().frozen}


# ----- demo controls (mock mode only) -----
# The public demo is shared by every visitor, so controls run one at a time and reset has a cooldown.
_demo_lock = threading.Lock()
_last_reset = [0.0]


def _reset_locked(reason: str) -> None:
    """The shared simulation's own reset (caller holds `_demo_lock`): a fresh ledger and warm-up, the new ledger's
    `demo_reset` event (reasons and counts only), and the janitor told the state is clean."""
    rt.reset()
    _last_reset[0] = time.time()
    rt.ledger.event("demo_reset", reset_event(reason, rt.demo_policy))
    _janitor.mark_reset(int(rt.ledger.totals().get("requests", 0) or 0), auto=True)


def _auto_reset(reason: str) -> bool:
    """The janitor's callback: reset when no demo action holds the lock, else False (the next pass tries again)."""
    if not _demo_lock.acquire(blocking=False):
        return False
    try:
        _reset_locked(reason)
    finally:
        _demo_lock.release()
    return True


# One per gateway process, so a demo reset keeps its counters; no thread until a mock demo action needs one.
_janitor = DemoJanitor(rt.demo_policy, _auto_reset, lambda: int(rt.ledger.totals().get("requests", 0) or 0), wall=time.time)
_janitor.mark_reset(_janitor._counts())  # the warm-up's requests are the clean baseline
RESET_COOLDOWN_S = 30.0
OUTAGE_MIN_S, OUTAGE_MAX_S = 5, 300


def _mock_only() -> None:
    if rt.settings.mode != "mock":
        raise HTTPException(403, "demo controls are only available in mock mode")


def _demo_busy() -> HTTPException:
    return HTTPException(409, "another demo action is running; try again in a moment")


@app.post("/ratchet/demo/day")
def demo_day(requests: int = 1500, workload: str = "tickets") -> dict[str, Any]:
    """Simulate one day of traffic (support tickets, Scout research calls, both, structured outputs or tool requests),
    then one night of training. Mock models: `simulation` says so in the response."""
    _mock_only()
    if workload not in WORKLOADS:
        raise HTTPException(400, f"workload must be one of {list(WORKLOADS)}")
    if workload in GENERIC_WORKLOADS and rt.settings.mock_strict:  # the strict mock would refuse its first request mid-day
        raise HTTPException(400, f"workload {workload} needs the generic mock world, which RATCHET_MOCK_STRICT=1 turns off")
    if not _demo_lock.acquire(blocking=False):
        raise _demo_busy()
    try:
        # The caps are enforced on a click, so nobody watching a rollback loses the state under them (FR-11.30).
        auto = _janitor.due(int(rt.ledger.totals().get("requests", 0) or 0), rt.ledger.day, _janitor.clock())
        if auto in ("max_days", "max_requests"):
            _reset_locked(auto)
        else:
            auto = None
        rep = rt.simulate_day(max(100, min(requests, 5000)), workload)
    finally:
        _demo_lock.release()
    return {"day": rt.ledger.day, "workload": workload, "simulation": True, "auto_reset": auto, "night": rep.__dict__}


@app.post("/ratchet/demo/break-next")
def demo_break_next() -> dict[str, Any]:
    """Fault injection: the next trained student is deployed corrupted, to show automatic rollback."""
    _mock_only()
    nxt = f"student-v{len(rt.ledger.versions()) + 1}"
    rt.fleet.corrupt_on_deploy.add(nxt)
    rt.ledger.event("fault", f"{nxt} will be deployed corrupted (demo fault injection)")
    return {"corrupt": nxt}


@app.post("/ratchet/demo/student-outage")
def demo_student_outage(seconds: int = 60) -> dict[str, Any]:
    """Fault injection: the student endpoint refuses connections for `seconds` (5 to 300), to show the circuit breaker
    open, the teacher answer meanwhile, and a half-open probe close it again (`circuit` events)."""
    _mock_only()
    seconds = max(OUTAGE_MIN_S, min(seconds, OUTAGE_MAX_S))
    rt.fleet.student_outage(seconds)
    rt.ledger.event("fault", f"student endpoint down for {seconds} s (demo fault injection)")
    return {"seconds": seconds, "until": time.time() + seconds}


@app.get("/ratchet/demo/sample-example")
def demo_sample_example(split: str = "train", version: int | None = None) -> dict[str, Any]:
    """A committed example of `version` (default: the live version) from its `split` set, as a body for
    `POST /ratchet/versions/{id}/proof`: its task, its stored (redacted) messages and the conditions it was stored under,
    never its answer. The first leaf, in key order, whose example is still stored."""
    _mock_only()
    _split(split)
    v = rt.ledger.version(version) if version is not None else rt.ledger.live()
    if v is None:
        raise HTTPException(404, f"no version {version}" if version is not None else "no live version yet")
    row = rt.ledger.leaf_example(v.id, split)
    if row is None:
        raise HTTPException(404, f"version {v.id} has no committed {split} example still stored")
    params = json.loads(row["params"]) if row["params"] else {}
    return {"version": v.id, "split": split, "task": row["task"], "messages": json.loads(row["messages"]), **params}


@app.post("/ratchet/demo/teacher-drift")
def demo_teacher_drift(noise: float = 0.15) -> dict[str, Any]:
    """Fault injection: from now on the mock teacher changes a label with probability `noise` (0 to 1; 0 makes it
    stable again), to show the teacher watch contest labels, freeze Night School (`teacher_drift`) and resume after
    `POST /ratchet/teacher/ack`."""
    _mock_only()
    if not 0 <= noise <= 1:  # also NaN
        raise HTTPException(400, "noise must be from 0 to 1")
    rt.ratchet.teacher.noise = noise
    rt.ledger.event("fault", f"mock teacher changes a label with probability {noise:g} (demo fault injection)")
    return {"noise": noise}


@app.post("/ratchet/demo/reset")
def demo_reset() -> dict[str, str]:
    _mock_only()
    if time.time() - _last_reset[0] < RESET_COOLDOWN_S:
        raise HTTPException(429, f"the demo was reset less than {int(RESET_COOLDOWN_S)} seconds ago")
    if not _demo_lock.acquire(blocking=False):
        raise _demo_busy()
    try:
        rt.reset()
        _last_reset[0] = time.time()
        _janitor.mark_reset(_janitor._counts())
    finally:
        _demo_lock.release()
    return {"status": "reset"}


# ----- Scout: the multi-agent research demo (docs/SCOUT.md) -----
def _client_ip(request: Request) -> str:
    if rt.settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _scout_precheck(question: str, ip: str, judge: bool = False, run_id: str | None = None) -> tuple[int, str] | None:
    """(HTTP status, message) when a run cannot start, else None. Records the run in the guard (as a judge run when the
    request carries a valid judge cookie), which writes its spend row in live mode."""
    q = (question or "").strip()
    if not q:
        return 400, "Ask a question."
    if len(q) > MAX_QUESTION_CHARS:
        return 400, f"Questions are limited to {MAX_QUESTION_CHARS} characters."
    if rt.settings.mode == "mock" and q not in EXAMPLE_QUESTIONS:
        return 400, "This demo runs in mock mode with simulated search results, so it answers the example questions only."
    if rt.search is None:
        return 503, "Live search is not configured (TAVILY_API_KEY is not set)."
    refused = rt.guard.admit(ip, judge=judge, run_id=run_id)
    if refused:
        ctx = obs.current()
        if ctx is not None:
            ctx.stage = "budget"  # the Scout budget guard's refusal, for the failures row of /scout/run's 429
        return 429, refused
    return None


def _run_scout(question: str, emit, pace: float = 0.0, judge: bool = False, run_id: str | None = None) -> dict[str, Any] | None:
    """Run Scout, turning failures into an `error` event. Returns the `done` event or None.
    Spend is counted per step, so a run that fails halfway still counts against the budget; in live mode the guard also
    persists the cost so far after each model call. A run whose search failed or was refused still finishes, labelled
    (`done.search`), and counts as a search failure only when the search itself failed."""
    run_id = run_id or uuid.uuid4().hex[:8]
    spent = [0.0]
    outcome = ["error"]

    def counting_emit(ev: dict[str, Any]) -> None:
        if ev.get("type") == "step":
            spent[0] += float(ev.get("cost_usd") or 0.0)
            if ev.get("served_by") != "tool":
                rt.guard.progress(run_id, spent[0])
            elif ev.get("agent") == "searcher":
                rt.note_search((ev.get("detail") or {}).get("unavailable"))
        emit(ev)

    try:
        done = rt.scout(pace, judge, run_id).run(question.strip(), counting_emit, mode=rt.settings.mode, run_id=run_id)
        outcome[0] = "done" if done.get("search", {}).get("available", True) else "search_unavailable"
    except httpx.HTTPError as e:  # the status and code only: an httpx message carries the URL
        emit({"type": "error", "message": f"a model or search call failed: {as_upstream(e).label}"})
        return None
    except Exception as e:  # noqa: BLE001 - report, never crash the server
        emit({"type": "error", "message": f"run failed: {type(e).__name__}: {e}"[:300]})
        return None
    finally:
        rt.guard.add_spend(spent[0], judge=judge, run_id=run_id, outcome=outcome[0])
        rt.tick()
    return done


def _judge_session(request: Request) -> bool:
    """Whether the request carries a valid judge cookie. Only the cookie counts: a code in the query or the body is
    ignored, so it never needs to appear in a URL (ADR 0120)."""
    return rt.judge is not None and rt.judge.verify(request.cookies.get(COOKIE))


def _judge_status(request: Request) -> dict[str, Any]:
    """`/scout/status`'s `judge` block: whether judge access is on, whether this request has a session, and the reserve."""
    return {"enabled": rt.judge is not None, "session": _judge_session(request), **rt.guard.judge_status()}


@app.get("/scout/examples")
def scout_examples() -> dict[str, Any]:
    return {"mode": rt.settings.mode, "examples": EXAMPLE_QUESTIONS, "free_text": rt.settings.mode == "live"}


@app.get("/scout/recorded")
def scout_recorded() -> dict[str, Any]:
    """Recorded live runs, newest first (FR-10.40): read-only, no model call, no guard budget, no ledger row."""
    return {"recordings": scout_record.listing(rt.settings.scout_recordings_dir)}


@app.get("/scout/recorded/{rid}")
def scout_recorded_one(rid: str) -> dict[str, Any]:
    rec = scout_record.get(rt.settings.scout_recordings_dir, rid)
    if rec is None:
        raise HTTPException(404, "No such recording.")
    return {"id": rid, **rec}


@app.get("/scout/status")
def scout_status(request: Request) -> dict[str, Any]:
    """The guard's state, judge access and search credits (ADR 0120), and which model answers student calls (FR-7.40),
    so the page labels each call without a stats call: a step whose `model` is `fallback_student` came from the hosted
    fallback, not our adapter."""
    return {
        "mode": rt.settings.mode,
        **rt.guard.status(),
        "tavily": isinstance(rt.search, TavilySearch),
        "judge": _judge_status(request),
        "search": rt.search_status(),
        "fallback_student": rt.settings.fallback_student_model,
        "student_serving": rt.student_serving(),
        "live_check": rt.live_check(),
    }


@app.post("/scout/judge")
async def scout_judge(request: Request) -> JSONResponse:
    """Open a judge session: `{"code": "..."}` in the body (never in a URL or a header, which access logs record).
    The body is parsed by hand, so no validation error can echo it. 400 for another shape, 404 when judge access is
    off, 429 past 10 attempts per IP per rolling hour, 401 for a wrong code (no cookie); 200 sets a signed HttpOnly,
    SameSite=Strict cookie scoped to /scout (Secure in live mode) and writes a `judge` event without the IP or the code."""
    raw = await request.body()
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):  # not JSON, not UTF-8, or nested too deeply to parse
        body = None
    code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(code, str) or not code:
        raise HTTPException(400, 'Send {"code": "..."}.')
    judge = rt.judge
    if judge is None:
        raise HTTPException(404, "Judge access is not enabled on this demo.")
    if not rt.judge_attempts.allow(_client_ip(request)):
        raise HTTPException(429, "Too many attempts from this address. Try again in an hour.")
    if not judge.check(code):
        raise HTTPException(401, "That code is not valid.")
    value, expires = judge.issue()
    await anyio.to_thread.run_sync(rt.ledger.event, "judge", "judge session opened")
    resp = JSONResponse(
        {
            "judge": True,
            "expires": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="seconds"),
            "runs_left_today": await anyio.to_thread.run_sync(rt.guard.judge_runs_left),
        }
    )
    resp.set_cookie(
        COOKIE,
        value,
        max_age=int(judge.session_s),
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=rt.settings.mode == "live",
    )
    return resp


@app.delete("/scout/judge", status_code=204)
def scout_judge_end() -> Response:
    """End a judge session on this browser: clears the cookie. Idempotent."""
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict", secure=rt.settings.mode == "live")
    return resp


@app.get("/scout/stream")
def scout_stream(request: Request, q: str = "") -> StreamingResponse:
    """Server-Sent Events. Always HTTP 200: problems arrive as an `error` event (EventSource cannot read status codes)."""
    judge = _judge_session(request)
    run_id = uuid.uuid4().hex[:8]
    problem = _scout_precheck(q, _client_ip(request), judge, run_id)

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def events() -> Iterator[str]:
        if problem is not None:
            yield sse({"type": "error", "message": problem[1]})
            return
        box: queue.Queue[dict[str, Any] | None] = queue.Queue()
        pace = rt.settings.mock_pace if rt.settings.mode == "mock" else 0.0
        worker = threading.Thread(target=lambda: (_run_scout(q, box.put, pace, judge, run_id), box.put(None)), daemon=True)
        worker.start()
        while True:
            try:
                ev = box.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if ev is None:
                return
            yield sse(ev)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@app.post("/scout/run")
def scout_run(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """One run, answered when it finishes: 200 also when search was unavailable (`done.search`), 502 only when a
    model call failed."""
    question = str(body.get("question", ""))
    judge = _judge_session(request)
    run_id = uuid.uuid4().hex[:8]
    problem = _scout_precheck(question, _client_ip(request), judge, run_id)
    if problem is not None:
        raise HTTPException(*problem)
    events: list[dict[str, Any]] = []
    done = _run_scout(question, events.append, 0.0, judge, run_id)
    if done is None:
        raise HTTPException(502, events[-1]["message"] if events else "run failed")
    return {"events": events}
