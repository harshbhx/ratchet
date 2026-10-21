"""Request ids, trace context and structured logs that carry no content (ADR 0062).

Every gateway request gets a `RequestContext` (`new_context`), held in the `request_ctx` context variable for as long
as the request runs, so code deep in the cascade can name the request without the id being passed down: log lines
(`log`), the trace headers a provider forwards (`upstream_headers`), and the router's note that it counted or logged
the request. The context crosses into worker threads with the variable (`anyio.to_thread.run_sync` copies the context);
a plain `threading.Thread` starts without it.

- The server's id is always `req_` + 32 hex digits (a UUID4); the completion id is `chatcmpl-` + the same digits. A
  client's `X-Request-Id` or `X-Client-Request-Id` that matches `CLIENT_REQUEST_ID` is kept as `client_request_id`;
  anything else is ignored, so a header can never put arbitrary text into the ledger or a log line.
- A W3C `traceparent` (version 00, or a later version's first four fields) is accepted; its trace id is logged and
  forwarded to the teacher and the student with a new span id per HTTP attempt (`upstream_headers`). A malformed one is
  ignored. No trace is started for a request without one: the OpenTelemetry SDK is deferred.
- Log lines go to the `ratchet` logger as one event name and a dict of fields (`log`), rendered as one JSON object per
  line (`JsonFormatter`, live mode's default) or as `key=value` text (`TextFormatter`, mock mode's). A field is an id,
  a name, a number or a status, never message text, completion text, a URL or a key (invariants 12 and 13): upstream
  failures carry the exception's class, the role and the status only (`upstream_failure`), and ledger events mirrored
  to the log are scrubbed of URLs and error messages first (`mirror_event`).

Standard library only, plus httpx's exception types, `access.scrub_detail` and `resilience.as_upstream` for what a
failure may say.
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import re
import secrets
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from .access import scrub_detail
from .resilience import as_upstream

LOGGER = logging.getLogger("ratchet")

# A client's own request id (X-Request-Id, X-Client-Request-Id): what OpenAI's SDKs and most proxies send fits.
CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
CLIENT_ID_HEADERS = ("x-request-id", "x-client-request-id")  # the first valid one wins
# A request id as the lookup endpoints take it: the server's `req_<hex>`, or the completion's `chatcmpl-<hex>`.
LOOKUP_ID = re.compile(r"(?:req_|chatcmpl-)([0-9a-f]{32})")
REQUEST_PREFIX = "req_"
COMPLETION_PREFIX = "chatcmpl-"
# W3C Trace Context (https://www.w3.org/TR/trace-context/): lower-case hex only; version ff is invalid; an all-zero trace
# or parent id is invalid. A version after 00 may append fields after a '-', which are not read.
_TRACEPARENT = re.compile(r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})(-.*)?")
MAX_TRACESTATE = 512  # the W3C limit a vendor may truncate to
_HEADER_SAFE = re.compile(r"[\x20-\x7e]*")  # printable ASCII: what a forwarded header value may hold

# RATCHET_LOG_FORMAT and RATCHET_LOG_LEVEL values (`Settings.log_settings`).
LOG_FORMATS = ("json", "text")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class RequestContext:
    """What the gateway knows about the request in progress. `request_id` and `hex` name it; `client_request_id` and
    the trace fields come from the client's headers (validated). The rest is filled while it runs: `client` and `task`
    once known, `stage`, `code` and `error` by the code that refused or failed it (for its failures row), `result` by
    the chat route (the fields of its log line), `counted` when the router counted it in `ratchet_requests_total`, and
    `failure_handled` when the router wrote (or, sampling, chose not to write) its failures row, so the gateway does not
    write another, and `failed_cost_usd` when a request that spent money failed with an error that is not the teacher's
    (a bug), so the gateway's row carries that spend. `spent_usd` is what a request that failed had spent on model calls
    (the student's samples), whatever failed it, so the gateway counts it against the key's budget (ADR 0063). One object
    per request, shared with the worker thread the route runs in."""

    request_id: str
    hex: str
    client_request_id: str | None = None
    trace_id: str | None = None
    trace_flags: str = "00"
    tracestate: str | None = None
    path: str = ""
    method: str = ""
    started: float = field(default_factory=time.perf_counter)
    client: str | None = None
    task: str | None = None
    stage: str | None = None
    code: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    counted: bool = False
    failure_handled: bool = False
    failed_cost_usd: float = 0.0
    spent_usd: float = 0.0

    @property
    def completion_id(self) -> str:
        return COMPLETION_PREFIX + self.hex

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000


request_ctx: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("ratchet_request", default=None)


def current() -> RequestContext | None:
    """The request in progress on this thread's context, or None (Scout's worker threads, the simulator, tests)."""
    return request_ctx.get()


def client_request_id(value: str | None) -> str | None:
    """`value` when it is a client request id this gateway keeps (`CLIENT_REQUEST_ID`), else None."""
    if value is None:
        return None
    value = value.strip()
    return value if CLIENT_REQUEST_ID.fullmatch(value) else None


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    """(trace id, flags) of a valid W3C `traceparent`, or None for a missing or malformed one."""
    if not value:
        return None
    m = _TRACEPARENT.fullmatch(value.strip())
    if m is None:
        return None
    version, trace_id, parent_id, flags, rest = m.groups()
    if version == "ff" or (version == "00" and rest) or set(trace_id) == {"0"} or set(parent_id) == {"0"}:
        return None
    return trace_id, flags


def _tracestate(value: str | None) -> str | None:
    """A `tracestate` worth forwarding beside a valid `traceparent`: printable ASCII, at most MAX_TRACESTATE chars."""
    if not value:
        return None
    value = value.strip()
    if not value or len(value) > MAX_TRACESTATE or not _HEADER_SAFE.fullmatch(value):
        return None
    return value


def new_context(headers: Mapping[str, str], path: str = "", method: str = "") -> RequestContext:
    """A new request's context from its headers (lower-case names, as ASGI gives them)."""
    hex_ = uuid.uuid4().hex
    client_id = None
    for name in CLIENT_ID_HEADERS:
        client_id = client_request_id(headers.get(name))
        if client_id is not None:
            break
    ctx = RequestContext(REQUEST_PREFIX + hex_, hex_, client_id, path=path, method=method)
    trace = parse_traceparent(headers.get("traceparent"))
    if trace is not None:
        ctx.trace_id, ctx.trace_flags = trace
        ctx.tracestate = _tracestate(headers.get("tracestate"))
    return ctx


def lookup_hex(value: str) -> str | None:
    """The 32 hex digits a lookup id names (`req_<hex>` or `chatcmpl-<hex>`), or None when it is neither."""
    m = LOOKUP_ID.fullmatch(value)
    return m.group(1) if m else None


def _span_id() -> str:
    while True:
        span = secrets.token_hex(8)
        if span != "0" * 16:  # all zeros is invalid; a 1 in 2**64 draw
            return span


def upstream_headers() -> dict[str, str]:
    """The trace headers for one outgoing model call: the request's trace id with a new span id, its flags and its
    `tracestate`; empty outside a traced request. Call it once per HTTP attempt: each attempt is its own span."""
    ctx = request_ctx.get()
    if ctx is None or ctx.trace_id is None:
        return {}
    out = {"traceparent": f"00-{ctx.trace_id}-{_span_id()}-{ctx.trace_flags}"}
    if ctx.tracestate:
        out["tracestate"] = ctx.tracestate
    return out


# ---------- logs ----------
def log(level: int, event: str, **fields: Any) -> None:
    """One log line: `event` and `fields`, plus the request's id and trace id when a request is in progress. Pass ids,
    names, numbers and statuses only, never request or answer text, URLs or keys. Costs one level check when the line
    would not be written."""
    if not LOGGER.isEnabledFor(level):
        return
    ctx = request_ctx.get()
    if ctx is not None:
        fields.setdefault("request_id", ctx.request_id)
        if ctx.trace_id is not None:
            fields.setdefault("trace_id", ctx.trace_id)
    LOGGER.log(level, event, extra={"ratchet": {"event": event, **fields}})


def failure_status(e: BaseException) -> int | None:
    """The HTTP status a failed upstream call stands for (`resilience.as_upstream`: a timeout is 504, no connection 502,
    an open breaker 503), or None for an error that is not an HTTP failure (a bug)."""
    return as_upstream(e).status if isinstance(e, httpx.HTTPError) else None


def upstream_failure(role: str, e: BaseException, **fields: Any) -> None:
    """A WARNING line for a failed upstream call: the role (teacher, student, shadow, audit, arbitration, ...), the
    exception's class and its status, never its message (an httpx message carries the endpoint URL)."""
    if not LOGGER.isEnabledFor(logging.WARNING):
        return
    log(logging.WARNING, "upstream_error", role=role, error=type(e).__name__, status=failure_status(e), **fields)


def mirror_event(row: Mapping[str, Any]) -> None:
    """`Ledger.add_event_listener` hook: every ledger event as an INFO line, its detail scrubbed of URLs and of error
    messages after an exception's class (`access.scrub_detail`), as a public dashboard shows it."""
    if not LOGGER.isEnabledFor(logging.INFO):
        return
    log(logging.INFO, "ledger_event", kind=row.get("kind"), detail=scrub_detail(str(row.get("detail", ""))), day=row.get("day"))


def _timestamp(created: float) -> str:
    return datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(created % 1 * 1000):03d}Z"


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    """The record's event and fields (`log`), or its message for a record another call wrote."""
    fields = getattr(record, "ratchet", None)
    if isinstance(fields, dict):
        return fields
    return {"event": "message", "message": record.getMessage()}


def _plain(value: Any) -> Any:
    """A field value JSON can hold: non-finite floats as strings (JSON has no NaN), anything unknown as its str."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line: `ts` (UTC, ms), `level`, `logger`, then the event and its fields. An exception
    attached to a record shows as its class only (`exc_type`): a traceback's text can quote a request."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": _timestamp(record.created), "level": record.levelname.lower(), "logger": record.name}
        for key, value in _fields(record).items():
            out.setdefault(key, _plain(value))
        if record.exc_info and record.exc_info[0] is not None:
            out["exc_type"] = record.exc_info[0].__name__
        return json.dumps(out, ensure_ascii=False, default=str, separators=(",", ":"))


def _text_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else str(value)
    text = str(value)
    return text if text and all(c.isprintable() and c not in ' "=' for c in text) else json.dumps(text, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """`<ts> <LEVEL> <event> key=value ...` on one line; values with spaces, quotes or '=' are JSON-quoted."""

    def format(self, record: logging.LogRecord) -> str:
        fields = dict(_fields(record))
        event = fields.pop("event", "message")
        parts = [_timestamp(record.created), record.levelname, str(event)]
        parts += [f"{k}={_text_value(v)}" for k, v in fields.items()]
        if record.exc_info and record.exc_info[0] is not None:
            parts.append(f"exc_type={record.exc_info[0].__name__}")
        return " ".join(parts)


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever `sys.stderr` is when each record is written: test runners and hosts replace it, and a handler
    holding an old stream would write to a closed file."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        pass


_MARK = "_ratchet_handler"


def configure(fmt: str, level: str, stream: Any = None) -> logging.Handler:
    """Send the `ratchet` logger's records at `level` and above to stderr (or `stream`) in `fmt` (LOG_FORMATS), replacing
    the handler an earlier call installed, so reconfiguring (a demo reset, a reloaded gateway) never doubles lines. The
    logger stops propagating, so a host's root handler does not print each line a second time. Returns the handler."""
    if fmt not in LOG_FORMATS:
        raise ValueError(f"log format must be one of {', '.join(LOG_FORMATS)}, not {fmt!r}")
    if level not in LOG_LEVELS:
        raise ValueError(f"log level must be one of {', '.join(LOG_LEVELS)}, not {level!r}")
    for h in list(LOGGER.handlers):
        if getattr(h, _MARK, False):
            LOGGER.removeHandler(h)
    handler: logging.Handler = _StderrHandler() if stream is None else logging.StreamHandler(stream)
    setattr(handler, _MARK, True)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    return handler
