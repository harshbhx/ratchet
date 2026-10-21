"""OpenAI-compatible gateway, dashboard API, landing page and the Scout multi-agent demo.

    RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000     # offline demo, no keys
    RATCHET_MODE=live NEBIUS_API_KEY=... uvicorn ratchet.gateway:app  # real Token Factory teacher

Point any OpenAI SDK at http://localhost:8000/v1. Choose the task with the
`X-Ratchet-Task` header (or `metadata.ratchet_task` in the body; the header wins). With `RATCHET_API_KEYS`
set, `/v1/*` needs `Authorization: Bearer <key>`. Errors on `/v1/*` use OpenAI's envelope
(`ratchet/openai_compat.py`); every other route keeps FastAPI's `{"detail": ...}`.
Pages: `/` landing, `/scout` multi-agent demo, `/dashboard` Ratchet dashboard.
"""

from __future__ import annotations

import calendar
import functools
import json
import math
import queue
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__, live, tasks, taskspec
from . import bom as bomdoc
from .auth import KeyRing, bearer
from .config import Settings
from .cost import cost_report, record_endpoint, record_simulated_day, stats_block
from .ledger import Ledger, Version
from .nightschool.orchestrate import NightReport, run_night
from .openai_compat import (
    INVALID_REQUEST,
    SERVICE_TIER,
    APIError,
    Limits,
    from_validation_errors,
    http_error,
    legacy_history,
    normalize_messages,
    parse_body,
    parse_modalities,
    tool_params,
    usage_object,
    uses_tools,
    validate_chat_request,
)
from .providers import GenParams, OpenAICompatProvider
from .redact import redact
from .release import ReleaseGate, ReleasePolicy
from .resilience import CLIENT_CAUSED, RETRY_STATUSES, TEACHER_CIRCUIT_OPEN, TOOL_CALLS_UNSUPPORTED, CircuitOpen, as_upstream
from .router import Choice, Ratchet
from .scout.agents import Call, Scout
from .scout.guard import Guard
from .scout.prompts import EXAMPLE_QUESTIONS
from .scout.search import MockSearch, SearchTool, TavilySearch

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


TRANSPORT_KEYS = ("teacher", "student")


class Runtime:
    """Wires settings, ledger, providers and the release gate together.

    `transports` replaces the network under the live-mode providers ({"teacher": ..., "student": ...}, each an
    `httpx.BaseTransport` such as `httpx.MockTransport`; the student's also carries adapter reloads), so live mode can
    run offline in tests, and `sleep` the teacher's waits between retries; mock mode has no HTTP providers and ignores
    both. A fallback teacher on another endpoint (RATCHET_TEACHER_FALLBACK_BASE) also goes over the teacher's transport.
    `clock` times the breakers, adapter reloads and demo outages (tests pass a fake one).

    `reloader` is the `live.AdapterReloader` in live mode with a student endpoint, else None."""

    def __init__(
        self,
        settings: Settings | None = None,
        transports: dict[str, httpx.BaseTransport] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        unknown = set(transports or {}) - set(TRANSPORT_KEYS)
        if unknown:
            raise ValueError(f"unknown transport keys {sorted(unknown)}; use {list(TRANSPORT_KEYS)}")
        self.settings = settings or Settings()
        self.transports = dict(transports or {})
        self.sleep = sleep
        self.clock = clock
        self.tasks_lock = threading.Lock()  # admin writes to the registry and the tasks table, and resets, one at a time
        self.reset()

    def reset(self) -> None:
        s = self.settings
        self.cost_policy = s.cost_policy()  # a bad RATCHET_STUDENT_COST_MODEL or GPU rate stops startup here (ADR 0023)
        min_task_n = s.min_task_golden_rows()  # a bad RATCHET_MIN_TASK_GOLDEN stops startup here (FR-14.1)
        if s.mode == "mock":
            from sim.mock_models import MockFleet, MockTeacher
            from sim.world import make_stream

            self.ledger = Ledger(":memory:", s.golden_fraction)
            # RATCHET_MOCK_STRICT=1: the mock models answer only the demo's own requests (the 400 below otherwise).
            self.fleet = MockFleet(
                clock=self.clock,
                policy=s.gen_policy(),
                min_task_n=min_task_n,
                strict=s.mock_strict,
                per_task=s.per_task_calibration,
            )
            self.reloader: live.AdapterReloader | None = None
            self.stream = make_stream("tickets", seed=7, drift_day=5)
            self.streams = {"tickets": self.stream, **{w: make_stream(w, seed=7, drift_day=5) for w in WORKLOADS[1:]}}
            teacher = MockTeacher(s.teacher_reasoning_tokens, strict=s.mock_strict)
            resolver = self.fleet.resolve
            s.audit_rate = max(s.audit_rate, 0.10)
            policy = ReleasePolicy(min_audits=15)
            self.search: SearchTool | None = MockSearch()
            self.guard = Guard(None, 10 * s.scout_runs_per_hour)
            fallback_teacher: OpenAICompatProvider | None = None  # the mock teacher plays any fallback model too
        else:
            self.ledger = Ledger(s.db_path, s.golden_fraction, auto_day=True)
            # Timeouts and retries per role (ADR 0014): the teacher is retried within a deadline; the student never is,
            # because the teacher is its retry. A bad setting raises here, at startup.
            teacher = OpenAICompatProvider(
                s.teacher_base,
                s.teacher_api_key,
                timeout=s.teacher_timeout(),
                retry=s.teacher_retry(),
                transport=self.transports.get("teacher"),
                sleep=self.sleep,
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
                )
            self.reloader = None
            if s.student_base:
                student_transport = self.transports.get("student")
                student = OpenAICompatProvider(
                    s.student_base, s.student_api_key, timeout=s.student_timeout(), transport=student_transport
                )
                # vLLM forgets runtime-loaded adapters when it restarts; a 404 for one queues its reload (ADR 0015).
                http = httpx.Client(transport=student_transport) if student_transport is not None else None
                self.reloader = live.adapter_reloader(s, self.ledger, http, clock=self.clock)

                def resolver(v: Version):
                    return student, v.adapter_uri  # vLLM serves each LoRA adapter under its own name
            else:  # endpoint offline: the hosted, untuned fallback on Token Factory, still with the student's timeouts
                fallback = OpenAICompatProvider(
                    s.teacher_base, s.teacher_api_key, timeout=s.student_timeout(), transport=self.transports.get("teacher")
                )

                def resolver(v: Version):
                    return fallback, s.fallback_student_model

            policy = ReleasePolicy.from_env()
            self.search = TavilySearch(s.tavily_api_key) if s.tavily_api_key else None
            self.guard = Guard(s.scout_daily_budget_usd, s.scout_runs_per_hour, s.scout_max_runs_per_day)
        self.keys = KeyRing(s.api_keys)
        # Admin keys for management writes (ADR 0022); never the same keys as an API client's.
        self.admin_keys = KeyRing(s.admin_keys, env="RATCHET_ADMIN_KEYS")
        both = self.admin_keys.shared(self.keys)
        if both:
            raise ValueError(f"RATCHET_ADMIN_KEYS: the key for {both[0]!r} is also an API key; give admins their own keys")
        # The registry: the built-in tasks, RATCHET_TASKS_FILE's (a bad file stops startup here, every error listed) and
        # the API tasks this ledger stored (a fresh mock ledger has none, so a demo reset clears them).
        with self.tasks_lock:
            skipped = taskspec.install(self.ledger, s.tasks_file, self.known_clients())
        for line in skipped:
            self.ledger.event("task_skipped", line)
        self.signer = bomdoc.Signer(s.bom_signing_key) if s.bom_signing_key else None
        self.limits: Limits = s.limits()  # a bad RATCHET_MAX_N stops startup here
        self.modalities = parse_modalities(s.teacher_modalities)
        self.gateway_errors = 0  # unhandled exceptions since the last reset (see _unhandled_error)
        reload = self.reloader.request if self.reloader is not None else None
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
        self.gate = ReleaseGate(self.ledger, policy)
        self.gate_lock = threading.Lock()
        if s.mode == "mock" and s.mock_warmup_days > 0:
            self.warm_up(s.mock_warmup_days, s.mock_warmup_requests)

    def known_clients(self) -> list[str] | None:
        """The API key names a task's `clients` may use, or None when keys are off (nothing to check them against)."""
        return self.keys.names if self.keys.enabled else None

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
                self.tick()
        rep = run_night(
            self.ledger,
            self.fleet,
            self.fleet,
            self.gate,
            self.settings.student_base_model,
            task=night_task(workload),
            signer=self.signer,
            redact_pii=self.settings.redact_pii,
        )
        trained = self.ledger.version(rep.version) if rep.trained and rep.version is not None else None
        record_simulated_day(self.ledger, self.cost_policy, self.ledger.day, trained)
        return rep

    def warm_up(self, days: int, requests: int) -> None:
        for _ in range(days):
            self.simulate_day(requests, "mixed")
        self.ledger.event("warmup", f"demo warm-up: {days} simulated days of mixed traffic ({requests} requests/day)")

    def tick(self) -> None:
        with self.gate_lock:
            self.gate.tick()

    def scout(self, pace: float = 0.0) -> Scout:
        """`pace` > 0 (mock streams only) sleeps for that share of each call's simulated latency."""
        assert self.search is not None

        def call(messages: list[dict[str, Any]], task: str) -> Call:
            c = Call.from_result(self.ratchet.handle(messages, task))
            if pace > 0:
                time.sleep(pace * c.latency_ms / 1000)
            return c

        return Scout(call, self.search, max_results=self.settings.scout_max_results)


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
    """The response for an HTTP error, in OpenAI's envelope on API paths and as `{"detail": ...}` elsewhere."""
    if _api_path(path):
        err = http_error(exc.status_code, str(exc.detail), method=method, path=path, headers=exc.headers)
        return JSONResponse(err.body(), status_code=err.status, headers=err.headers or None)
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


rt = Runtime()
app = FastAPI(title="Ratchet", version=__version__)
app.add_middleware(BodyLimitMiddleware, max_bytes=rt.settings.max_body_bytes)


@app.exception_handler(APIError)
async def _api_error(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers or None)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    if not _api_path(request.url.path):
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
    """A bug, not a client mistake: 500 (`api_error` on API paths). The server still logs the traceback. The ledger gets
    a `gateway_error` event with the exception's class name only (a message could hold request text) at the 1st,
    10th, 100th, ... occurrence, so a crash loop cannot flood it."""
    rt.gateway_errors += 1  # handlers run on the event loop, one at a time
    if _power_of_ten(rt.gateway_errors):
        detail = f"{type(exc).__name__} (unhandled error {rt.gateway_errors})"
        try:  # in a worker thread: the ledger lock may be held by a request in progress
            await anyio.to_thread.run_sync(rt.ledger.event, "gateway_error", detail)
        except Exception:  # noqa: BLE001 - the ledger may be what failed; the 500 must still go out
            pass
    if _api_path(request.url.path):
        return await _api_error(request, APIError(500, "The server had an error while processing your request.", "api_error"))
    return PlainTextResponse("Internal Server Error", status_code=500)


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
def _client(authorization: str | None, x_api_key: str | None) -> str | None:
    """The API client's name, or None when keys are off. 401 when keys are on and this one is wrong."""
    if not rt.keys.enabled:
        return None
    name = rt.keys.identify(bearer(authorization, x_api_key))
    if name is None:
        raise HTTPException(401, "invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})
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


def _quota(client: str | None) -> dict[str, str]:
    """Rate-limit headers for this client, with when the quota resets (`X-RateLimit-Reset-Requests`); 429
    `insufficient_quota` when its daily quota is used up. The 429 says when to come back (`Retry-After`, seconds to
    00:00 UTC) and tells OpenAI SDKs not to retry (they retry 429s by default, which cannot help before midnight)."""
    limit = rt.settings.api_key_daily_requests
    if client is None or limit <= 0:
        return {}
    now = time.time()
    midnight = _utc_midnight(now)
    reset_in = midnight + 86400 - now
    used = rt.ledger.client_requests_since(client, midnight)
    headers = {"X-RateLimit-Limit-Requests": str(limit), "X-RateLimit-Reset-Requests": _duration(reset_in)}
    if used >= limit:
        headers.update(
            {"X-RateLimit-Remaining-Requests": "0", "Retry-After": str(max(1, math.ceil(reset_in))), "X-Should-Retry": "false"}
        )
        message = f"daily request quota of {limit} used up for {client!r}; resets at 00:00 UTC"
        raise APIError(429, message, "insufficient_quota", "daily_quota_exceeded", headers=headers)
    return {**headers, "X-RateLimit-Remaining-Requests": str(limit - used - 1)}


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
def healthz() -> dict[str, Any]:
    """Liveness for Docker and load balancers. Public, even when API keys protect /v1/*."""
    return {"status": "ok", "version": __version__, "mode": rt.settings.mode}


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _client(authorization, x_api_key)
    return {"object": "list", "data": [{"id": "ratchet", "object": "model", "owned_by": "you"}]}


# The body is read by hand (for the 400 on bad JSON and the size limit), so describe it for /docs.
CHAT_BODY = {"requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}}


@app.post("/v1/chat/completions", response_model=None, openapi_extra=CHAT_BODY)
async def chat(
    request: Request,
    x_ratchet_task: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse | StreamingResponse:
    """Checks run in this order: a declared body size (413, `BodyLimitMiddleware`), API key (401), the body read and
    counted (413 for a chunked body over the limit), JSON and request validation (400), daily quota (429), then the
    cascade. A refused request writes no ledger row and uses no quota. Everything after reading the body runs in a
    worker thread (the default pool, as for sync routes), so neither a large body nor the ledger lock blocks the event
    loop."""
    client = _client(authorization, x_api_key)
    try:
        raw = await request.body()
    except ClientDisconnect as e:  # nobody is left to read the answer; not a server error either
        raise APIError(400, "The client disconnected before the request body was complete.") from e
    return await anyio.to_thread.run_sync(functools.partial(_complete, raw, x_ratchet_task, client))


def _complete(raw: bytes, header_task: str | None, client: str | None) -> JSONResponse | StreamingResponse:
    req = validate_chat_request(parse_body(raw), rt.limits)
    messages, flags = normalize_messages(req.messages, rt.modalities, rt.settings.merge_system)
    params = GenParams.from_request(req)  # the legacy `functions` and `function_call` arrive as tools (FR-1.41)
    legacy = tool_params(req).legacy  # answer tool calls as `function_call`, as the request asked
    if uses_tools(req):  # the teacher answers alone; legacy turns go in the form open-model servers render
        messages = legacy_history(messages)
    # The header wins over metadata.ratchet_task, and either over a response_format schema's auto task (ADR 0042);
    # refused before the quota, so a refusal costs nothing.
    try:
        task = rt.ratchet.task_for(header_task or req.task, client, params)
    except tasks.UnknownTask as e:  # RATCHET_UNKNOWN_TASKS=reject; or teacher/reject and a header that is not a task name
        param = None if header_task else "metadata.ratchet_task" if req.task else "response_format"
        raise APIError(400, str(e), INVALID_REQUEST, "unknown_task", param) from e
    except tasks.TaskForbidden as e:  # the task's `clients` do not name this key (threat model T12)
        raise APIError(403, "task not allowed for this key", "permission_error", "task_not_allowed") from e
    headers = _quota(client)
    # A part only the teacher reads (RATCHET_TEACHER_MODALITIES): no student, nothing stored, as for a pinned task. A tool
    # request takes the same path inside `handle` (route `tools`, FR-1.41).
    serve = functools.partial(rt.ratchet.teacher_only, route="multimodal") if flags.multimodal else rt.ratchet.handle
    try:  # each model call gets the parameters its role allows (GenPolicy, ADR 0017); n choices (FR-1.14)
        r = serve(messages, task, client=client, params=params, n=req.n)
    except KeyError as e:
        if rt.settings.mode != "mock":  # in live mode a KeyError is a bug, not a client mistake
            raise
        # sim.world.lookup under RATCHET_MOCK_STRICT=1; without it the generic mock world answers every request.
        raise HTTPException(400, f"mock mode only answers demo requests: {e}") from e
    except httpx.HTTPError as e:  # the teacher itself failed, after its retries: nothing left to fall back to
        raise _map_upstream(e) from e
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
        "task": r.task,  # the task the request was resolved to: named, an auto task from its schema, or default
        "schema_valid": r.schema_valid,  # every choice meets the response_format; null without one (ADR 0042)
    }
    # `usage` is the served answer's, as if its model had answered alone; Ratchet's overhead is in the breakdown and in
    # cost_usd (FR-1.12, docs/API.md#usage).
    usage = usage_object(r.prompt_tokens, r.completion_tokens, r.reasoning_tokens, r.cached_tokens)
    cid, created = f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())
    headers["X-Ratchet-Served-By"] = r.served_by
    if r.degraded:
        headers["X-Ratchet-Degraded"] = r.degraded
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
            else:  # the ratchet block rides on the last finish chunk
                yield chunk(i, {}, finish, ratchet=meta)
        if include_usage:  # one more chunk with no choices, the usage and the ratchet block
            yield sse({**envelope, "choices": [], "usage": usage, "ratchet": meta})
        yield "data: [DONE]\n\n"

    headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@app.get("/ratchet/stats")
def stats() -> dict[str, Any]:
    led = rt.ledger
    boms = led.bom_status()
    return {
        "mode": rt.settings.mode,
        "day": led.day,
        "teacher": rt.settings.teacher_model,
        "student_base": rt.settings.student_base_model,
        "days": led.stats_by_day(),
        "tasks": led.stats_by_task(),
        "versions": [{**v.__dict__, "bom": boms.get(v.id)} for v in led.versions()],
        "events": led.events()[-40:],
        # Every savings figure above and in `totals` prices the student per token; `cost` has the GPU-hour view (NFR-11).
        "totals": {**led.totals(), "cost_model": "per_token"},
        "bill_of_materials": led.bill_of_materials(),
        "clients": led.stats_by_client(),
        "privacy": {"redaction": rt.settings.redact_pii, **led.privacy_stats()},
        "security": {
            "api_keys": rt.keys.enabled,
            "daily_requests_per_key": rt.settings.api_key_daily_requests or None,
            "bom_signing": rt.signer is not None,
            "bom_key_id": rt.signer.key_id if rt.signer else None,
        },
        "cost": stats_block(cost_report(led, rt.settings)),
    }


@app.get("/ratchet/bom")
def bom() -> dict[str, Any]:
    """Model bill of materials: proof of which teachers and licenses produced the training data."""
    return rt.ledger.bill_of_materials()


@app.post("/ratchet/redact")
def redact_preview(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Show what the redactor would store for a piece of text. Nothing is logged or stored."""
    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "text is required")
    if len(text) > MAX_REDACT_CHARS:
        raise HTTPException(400, f"text is limited to {MAX_REDACT_CHARS} characters")
    found: Counter[str] = Counter()
    return {"text": redact(text, found), "found": dict(found), "enabled": rt.settings.redact_pii}


@app.get("/ratchet/bom/public-key")
def bom_public_key() -> dict[str, Any]:
    """The Ed25519 public key that verifies per-adapter BOMs (null when signing is off)."""
    if rt.signer is None:
        return {"alg": bomdoc.ALG, "public_key": None, "key_id": None}
    return {"alg": bomdoc.ALG, "public_key": rt.signer.public_key, "key_id": rt.signer.key_id}


@app.get("/ratchet/versions/{vid}/bom")
def version_bom(vid: int) -> dict[str, Any]:
    """One adapter's bill of materials, written when it was trained. Check it with `python -m ratchet.bom verify`."""
    doc = rt.ledger.version_bom(vid)
    if doc is None:
        raise HTTPException(404, f"no bill of materials for version {vid}")
    return doc


# ----- tasks (FR-2.15, ADR 0022) -----
MOCK_ADMIN = "mock-admin"  # the admin's name in mock mode without RATCHET_ADMIN_KEYS
MOCK_MAX_API_TASKS = 50  # the shared mock demo holds at most this many API tasks; a reset clears them


def _admin(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> str:
    """The admin's name for a management call, from `Authorization: Bearer <key>` or `X-API-Key`. With
    RATCHET_ADMIN_KEYS set: 401 for a missing or wrong key, 403 for a regular API key. Without them: 403 in live mode
    (the admin API is off) and `mock-admin` in mock mode, whose demo has nothing to protect. Only the name is ever
    stored or logged (INV-13)."""
    key = bearer(authorization, x_api_key)
    if rt.admin_keys.enabled:
        name = rt.admin_keys.identify(key)
        if name is not None:
            return name
        if rt.keys.identify(key) is not None:
            raise HTTPException(403, "this is an API key; management calls need an admin key (RATCHET_ADMIN_KEYS)")
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
    return HTTPException(403, f"task {t.name!r} is an {t.source} task; only API tasks can be changed here")


@app.get("/ratchet/tasks")
def list_tasks() -> dict[str, Any]:
    """Every registered task (`_task_entry`), the unknown-task policy, the registry's cap, and the names requests used
    that are not registered (candidates to register or pin)."""
    stats = _task_stats()
    live_version = rt.ledger.live()
    registered = tasks.registered()
    names = {t.name for t in registered}
    return {
        "unknown_tasks": rt.ratchet.unknown_tasks,
        "max_tasks": tasks.MAX_TASKS,
        "tasks": [_task_entry(t, stats.get(t.name), live_version) for t in registered],
        "unregistered": [{"name": n, "requests": int(r["requests"])} for n, r in stats.items() if n not in names],
    }


@app.get("/ratchet/tasks/{name}")
def get_task(name: str) -> dict[str, Any]:
    """One registered task; an API task also says when it was written and by which admin (the key's name)."""
    t = tasks.lookup(name)
    if t is None:
        raise HTTPException(404, f"no registered task is named {name!r}")
    entry = _task_entry(t, _task_stats().get(name), rt.ledger.live())
    saved = rt.ledger.saved_task(name) if t.source in ("api", "auto") else None
    if saved is not None:
        entry.update({k: saved[k] for k in ("created_ts", "updated_ts", "updated_by")})
    return entry


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
            parsed = taskspec.parse_document(obj, "file", known_clients=rt.known_clients())
            errors: list[str] = []
        else:
            parsed = [taskspec.parse_spec(obj, "api", known_clients=rt.known_clients())]
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
            task = taskspec.parse_spec({**obj, "name": name}, "api", known_clients=rt.known_clients())
        except taskspec.SpecError as e:
            return _spec_errors(e.errors)
        doc = taskspec.document(task)
        if current is not None and current.source == "api" and taskspec.document(current) == doc:
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
            rt.ledger.event("task_changed", f"{name}: spec {task.spec_hash} kept; description, clients or match by {admin}")
    return _put_result(task, changed=True, created=created)


def _put_result(task: tasks.Task, changed: bool, created: bool) -> dict[str, Any]:
    entry = _task_entry(task, _task_stats().get(task.name), rt.ledger.live())
    return {"task": entry, "spec_hash": task.spec_hash, "changed": changed, "created": created}


@app.delete("/ratchet/tasks/{name}")
def delete_task(name: str, admin: str = Depends(_admin)) -> dict[str, Any]:
    """Remove an API task or an auto task. Its stored examples stay, for provenance; requests that name it then get the
    unknown-task policy (RATCHET_UNKNOWN_TASKS), and a request with a response_format schema registers its auto task
    again, from that request's schema (ADR 0042)."""
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


# ----- cost accounting (FR-15.2 to FR-15.4, ADR 0023) -----
MAX_COST_DAYS = 36500
MOCK_MAX_API_INTERVALS = 200  # the shared mock demo holds at most this many endpoint records from the API; a reset clears them
ENDPOINT_KEYS = frozenset({"state", "usd_per_hour", "gpus", "note"})
MAX_NOTE_CHARS = 200


@app.get("/ratchet/cost")
def cost(days: int | None = None) -> dict[str, Any]:
    """The cost report (`ratchet.cost.cost_report`): savings per token and by GPU-hours paid, the break-even volume, the
    crossover with the hosted fallback, observed utilization and a recommendation, for every day or the last `days`
    ledger days. Public, like /ratchet/stats."""
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
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate < math.inf):
        raise HTTPException(400, "usd_per_hour must be a number of US dollars per GPU-hour, 0 or more")
    if gpus is not None and (isinstance(gpus, bool) or not isinstance(gpus, int) or not 0 <= gpus <= 1024):
        raise HTTPException(400, "gpus must be a whole number from 0 to 1024")
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


# ----- demo controls (mock mode only) -----
# The public demo is shared by every visitor, so controls run one at a time and reset has a cooldown.
_demo_lock = threading.Lock()
_last_reset = [0.0]
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
        rep = rt.simulate_day(max(100, min(requests, 5000)), workload)
    finally:
        _demo_lock.release()
    return {"day": rt.ledger.day, "workload": workload, "simulation": True, "night": rep.__dict__}


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


def _scout_precheck(question: str, ip: str) -> tuple[int, str] | None:
    """(HTTP status, message) when a run cannot start, else None. Records the run in the guard."""
    q = (question or "").strip()
    if not q:
        return 400, "Ask a question."
    if len(q) > MAX_QUESTION_CHARS:
        return 400, f"Questions are limited to {MAX_QUESTION_CHARS} characters."
    if rt.settings.mode == "mock" and q not in EXAMPLE_QUESTIONS:
        return 400, "This demo runs in mock mode with simulated search results, so it answers the example questions only."
    if rt.search is None:
        return 503, "Live search is not configured (TAVILY_API_KEY is not set)."
    refused = rt.guard.admit(ip)
    if refused:
        return 429, refused
    return None


def _run_scout(question: str, emit, pace: float = 0.0) -> dict[str, Any] | None:
    """Run Scout, turning failures into an `error` event. Returns the `done` event or None.
    Spend is counted per step, so a run that fails halfway still counts against the budget."""
    spent = [0.0]

    def counting_emit(ev: dict[str, Any]) -> None:
        if ev.get("type") == "step":
            spent[0] += float(ev.get("cost_usd") or 0.0)
        emit(ev)

    try:
        done = rt.scout(pace).run(question.strip(), counting_emit, mode=rt.settings.mode)
    except httpx.HTTPError as e:  # the status and code only: an httpx message carries the URL
        emit({"type": "error", "message": f"a model or search call failed: {as_upstream(e).label}"})
        return None
    except Exception as e:  # noqa: BLE001 - report, never crash the server
        emit({"type": "error", "message": f"run failed: {type(e).__name__}: {e}"[:300]})
        return None
    finally:
        rt.guard.add_spend(spent[0])
        rt.tick()
    return done


@app.get("/scout/examples")
def scout_examples() -> dict[str, Any]:
    return {"mode": rt.settings.mode, "examples": EXAMPLE_QUESTIONS, "free_text": rt.settings.mode == "live"}


@app.get("/scout/status")
def scout_status() -> dict[str, Any]:
    return {"mode": rt.settings.mode, **rt.guard.status(), "tavily": isinstance(rt.search, TavilySearch)}


@app.get("/scout/stream")
def scout_stream(request: Request, q: str = "") -> StreamingResponse:
    """Server-Sent Events. Always HTTP 200: problems arrive as an `error` event (EventSource cannot read status codes)."""
    problem = _scout_precheck(q, _client_ip(request))

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def events() -> Iterator[str]:
        if problem is not None:
            yield sse({"type": "error", "message": problem[1]})
            return
        box: queue.Queue[dict[str, Any] | None] = queue.Queue()
        pace = rt.settings.mock_pace if rt.settings.mode == "mock" else 0.0
        worker = threading.Thread(target=lambda: (_run_scout(q, box.put, pace), box.put(None)), daemon=True)
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
    question = str(body.get("question", ""))
    problem = _scout_precheck(question, _client_ip(request))
    if problem is not None:
        raise HTTPException(*problem)
    events: list[dict[str, Any]] = []
    done = _run_scout(question, events.append)
    if done is None:
        raise HTTPException(502, events[-1]["message"] if events else "run failed")
    return {"events": events}
