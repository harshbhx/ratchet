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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__, live
from . import bom as bomdoc
from .auth import KeyRing, bearer
from .config import Settings
from .ledger import Ledger, Version
from .nightschool.orchestrate import NightReport, run_night
from .openai_compat import (
    INVALID_REQUEST,
    APIError,
    Limits,
    from_validation_errors,
    http_error,
    normalize_messages,
    parse_body,
    parse_modalities,
    validate_chat_request,
)
from .providers import OpenAICompatProvider
from .redact import redact
from .release import ReleaseGate, ReleasePolicy
from .resilience import RETRY_STATUSES, as_upstream
from .router import Ratchet
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
WORKLOADS = ("tickets", "research", "mixed")
MAX_QUESTION_CHARS = 300
MAX_REDACT_CHARS = 5000


TRANSPORT_KEYS = ("teacher", "student")


class Runtime:
    """Wires settings, ledger, providers and the release gate together.

    `transports` replaces the network under the live-mode providers ({"teacher": ..., "student": ...}, each an
    `httpx.BaseTransport` such as `httpx.MockTransport`; the student's also carries adapter reloads), so live mode can
    run offline in tests, and `sleep` the teacher's waits between retries; mock mode has no HTTP providers and ignores
    both. `clock` times the student breakers, adapter reloads and demo outages (tests pass a fake one).

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
        self.reset()

    def reset(self) -> None:
        s = self.settings
        if s.mode == "mock":
            from sim.mock_models import MockFleet, MockTeacher
            from sim.world import make_stream

            self.ledger = Ledger(":memory:", s.golden_fraction)
            self.fleet = MockFleet(clock=self.clock)
            self.reloader: live.AdapterReloader | None = None
            self.stream = make_stream("tickets", seed=7, drift_day=5)
            self.streams = {"tickets": self.stream, **{w: make_stream(w, seed=7, drift_day=5) for w in ("research", "mixed")}}
            teacher = MockTeacher(s.teacher_reasoning_tokens)
            resolver = self.fleet.resolve
            s.audit_rate = max(s.audit_rate, 0.10)
            policy = ReleasePolicy(min_audits=15)
            self.search: SearchTool | None = MockSearch()
            self.guard = Guard(None, 10 * s.scout_runs_per_hour)
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
        self.signer = bomdoc.Signer(s.bom_signing_key) if s.bom_signing_key else None
        self.limits = Limits(max_messages=s.max_messages, max_input_chars=s.max_input_chars)
        self.modalities = parse_modalities(s.teacher_modalities)
        self.gateway_errors = 0  # unhandled exceptions since the last reset (see _unhandled_error)
        reload = self.reloader.request if self.reloader is not None else None
        self.ratchet = Ratchet(s, self.ledger, teacher, resolver, seed=7, clock=self.clock, on_adapter_missing=reload)
        self.gate = ReleaseGate(self.ledger, policy)
        self.gate_lock = threading.Lock()
        if s.mode == "mock" and s.mock_warmup_days > 0:
            self.warm_up(s.mock_warmup_days, s.mock_warmup_requests)

    # ----- mock-mode simulation helpers -----
    def simulate_day(self, requests: int, workload: str = "tickets") -> NightReport:
        """One simulated day of traffic, then one night of Night School (mock mode only)."""
        from sim.world import night_task

        stream = self.streams[workload]
        self.ledger.day += 1
        ticks = max(1, requests // 10)
        for i in range(requests):
            item = stream.sample(self.ledger.day)
            self.ratchet.handle(item.messages(), item.task)
            if (i + 1) % ticks == 0:
                self.tick()
        return run_night(
            self.ledger,
            self.fleet,
            self.fleet,
            self.gate,
            self.settings.student_base_model,
            task=night_task(workload),
            signer=self.signer,
        )

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


# Upstream statuses the client caused (its request is too long or malformed for the teacher): a 400 it should fix.
CLIENT_CAUSED = frozenset({400, 413, 422})
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
    so the client is the one that has to wait."""
    u = as_upstream(e)
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


def _chunks(text: str, words: int = 4) -> list[str]:
    """Split an answer into a few words per streamed chunk, keeping every character."""
    parts = re.findall(r"\s*\S+\s*|\s+", text)
    return ["".join(parts[i : i + words]) for i in range(0, len(parts), words)] or [""]


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
    headers = _quota(client)
    task = header_task or req.task  # the header wins over metadata.ratchet_task
    # A part only the teacher reads (RATCHET_TEACHER_MODALITIES): no student, nothing stored, as for a pinned task.
    serve = rt.ratchet.teacher_only if flags.multimodal else rt.ratchet.handle
    try:
        r = serve(messages, task, client=client)
    except KeyError as e:
        if rt.settings.mode != "mock":  # in live mode a KeyError is a bug, not a client mistake
            raise
        raise HTTPException(400, f"mock mode only answers demo requests: {e}") from e  # sim.world.lookup
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
    }
    cid, created = f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())
    headers["X-Ratchet-Served-By"] = r.served_by
    if not req.stream:
        payload = {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": r.model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": r.text}}],
            "ratchet": meta,
        }
        return JSONResponse(payload, headers=headers)

    # Streaming. The cascade must see the student's whole answer to score its confidence, so the
    # answer is complete before the first chunk; the stream format is what clients need from it.
    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        ev = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": r.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def events() -> Iterator[str]:
        yield chunk({"role": "assistant", "content": ""})
        for piece in _chunks(r.text):
            yield chunk({"content": piece})
        yield chunk({}, "stop", ratchet=meta)
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
        "totals": led.totals(),
        "bill_of_materials": led.bill_of_materials(),
        "clients": led.stats_by_client(),
        "privacy": {"redaction": rt.settings.redact_pii, **led.privacy_stats()},
        "security": {
            "api_keys": rt.keys.enabled,
            "daily_requests_per_key": rt.settings.api_key_daily_requests or None,
            "bom_signing": rt.signer is not None,
            "bom_key_id": rt.signer.key_id if rt.signer else None,
        },
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
    """Simulate one day of traffic (support tickets, Scout research calls, or both), then one night of training."""
    _mock_only()
    if workload not in WORKLOADS:
        raise HTTPException(400, f"workload must be one of {list(WORKLOADS)}")
    if not _demo_lock.acquire(blocking=False):
        raise _demo_busy()
    try:
        rep = rt.simulate_day(max(100, min(requests, 5000)), workload)
    finally:
        _demo_lock.release()
    return {"day": rt.ledger.day, "workload": workload, "night": rep.__dict__}


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
