"""Upstream failure handling: bounded retries with full-jitter backoff, the server's Retry-After, and one error type.

Every failed call to an OpenAI-compatible endpoint (Token Factory, vLLM) ends as an `UpstreamError` with an HTTP-like
status, so the gateway can answer with a status a client can act on instead of a blanket 502 (ADR 0014). `retry_call`
decides what is worth another attempt:

- Retried: failures before the request reached the model (`ConnectError`, `ConnectTimeout`, `PoolTimeout`,
  `RemoteProtocolError`) and the statuses an endpoint uses for "try again" (408, 409, 429, 500, 502, 503, 504).
- A `ReadTimeout` is retried once at most: the model may already be generating, and those tokens are billed.
- Any other 4xx is never retried. The same request cannot succeed.

Waits use full jitter, `uniform(0, min(cap_s, base_s * 2**i))`, or the server's `retry-after-ms` / `Retry-After` when it
sends one. A server wait longer than `cap_s`, or any wait that would end past the deadline, stops the retries: the error
goes back to the caller at once rather than holding the request for a retry that cannot be in time.

httpx is the only import outside the standard library (for its exception types); no retry library is needed.
"""

from __future__ import annotations

import email.utils
import math
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

T = TypeVar("T")

# Statuses an endpoint uses for "not now": rate limits, overload, a gateway or lock timeout.
RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
# Failures before the model saw the request: nothing was generated or billed, so another attempt is safe.
SAFE_TO_RETRY = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)

# UpstreamError.code values set by Ratchet itself (anything else came from the endpoint's error body).
UPSTREAM_TIMEOUT = "upstream_timeout"
UPSTREAM_UNREACHABLE = "upstream_unreachable"
BAD_RESPONSE = "bad_upstream_response"

# Timeouts other than the read timeout: a healthy endpoint accepts a connection and a request body quickly.
CONNECT_S = 5.0
WRITE_S = 10.0
POOL_S = 5.0

MAX_MESSAGE = 300
MAX_PARAM = 128
_IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try one upstream call. `max_attempts` counts the first try; `deadline_s` bounds the whole call,
    waits and attempts together (later attempts get at most the time that is left as their timeout)."""

    max_attempts: int = 3
    base_s: float = 0.5
    cap_s: float = 8.0
    deadline_s: float = 180.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError(f"max_attempts must be 1 or more, not {self.max_attempts!r}")
        for name in ("base_s", "cap_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"{name} must be a finite number of seconds, 0 or more, not {value!r}")
        if not self.deadline_s > 0:  # also refuses NaN; math.inf means no deadline
            raise ValueError(f"deadline_s must be more than 0 seconds, not {self.deadline_s!r}")

    def backoff(self, retry: int, rng: Callable[[], float] = random.random) -> float:
        """Full jitter before retry number `retry` (0 for the first retry)."""
        return rng() * min(self.cap_s, self.base_s * 2.0 ** min(retry, 64))


NO_RETRY = RetryPolicy(max_attempts=1, deadline_s=math.inf)


def timeouts(read_s: float, connect_s: float = CONNECT_S) -> httpx.Timeout:
    """httpx timeouts for one endpoint. `read_s` is how long the model may take to answer; write and pool are fixed."""
    for name, value in (("read", read_s), ("connect", connect_s)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (math.isfinite(value) and value > 0):
            raise ValueError(f"the {name} timeout must be a positive number of seconds, not {value!r}")
    return httpx.Timeout(connect=connect_s, read=read_s, write=WRITE_S, pool=POOL_S)


def _text(value: Any, limit: int) -> str | None:
    """`value` as printable text of at most `limit` characters, or None. Control characters become spaces and lone
    surrogates become '?', so the text can always be written as UTF-8 (in a log line, an event or a response)."""
    if not isinstance(value, str):
        return None
    text = _CONTROL.sub(" ", value.encode("utf-8", "replace").decode("utf-8")).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ident(value: Any) -> str | None:
    """An OpenAI `type` or `code` (a short identifier), or None. vLLM sends an integer `code`; that is dropped."""
    return value if isinstance(value, str) and _IDENT.fullmatch(value) else None


def parse_retry_after(headers: Mapping[str, str], now: float | None = None) -> float | None:
    """Seconds the server asked us to wait: `retry-after-ms`, else `Retry-After` in seconds or as an HTTP date (measured
    from `now`, default the current time). None when absent, malformed, not positive or not finite."""
    lower = {k.lower(): v for k, v in headers.items()}
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = lower.get(name)
        if raw is None:
            continue
        try:
            seconds = float(raw.strip()) / scale
        except ValueError:
            if name != "retry-after":
                continue
            try:
                parsed = email.utils.parsedate_tz(raw)
                if parsed is None:
                    continue
                seconds = email.utils.mktime_tz(parsed) - (time.time() if now is None else now)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    return None


class UpstreamError(httpx.HTTPError):
    """A failed upstream model call, reduced to what is safe to log and to act on.

    `status` is the endpoint's HTTP status, or 504 (`code` `upstream_timeout`) and 502 (`upstream_unreachable`,
    `bad_upstream_response`) for failures without one. `type`, `code` and `param` are OpenAI's fields when the error body
    had them; `message` is its message (at most 300 characters); `retry_after` is the wait it asked for, in seconds. It
    never holds the request, its headers or the URL: an httpx message carries the URL, which can hold a host or a token.
    A subclass of `httpx.HTTPError`, so existing `except httpx.HTTPError` clauses still catch it."""

    def __init__(
        self,
        status: int,
        message: str = "",
        *,
        type: str | None = None,  # the OpenAI field name
        code: str | None = None,
        param: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.status = int(status)
        self.message = _text(message, MAX_MESSAGE) or ""
        self.type = _ident(type)
        self.code = _ident(code)
        self.param = _text(param, MAX_PARAM)
        self.retry_after = retry_after if retry_after is not None and math.isfinite(retry_after) and retry_after > 0 else None
        super().__init__(f"{self.label}: {self.message}" if self.message else self.label)

    @property
    def label(self) -> str:
        """`upstream <status>`, plus the code when there is one: what a log line or an event may say about it."""
        return f"upstream {self.status} {self.code}" if self.code else f"upstream {self.status}"

    @classmethod
    def from_response(cls, response: httpx.Response, now: float | None = None) -> UpstreamError:
        """Read an error response: OpenAI's `{"error": {...}}`, vLLM's top-level `{"object": "error", ...}`, FastAPI's
        `{"detail": ...}`, or anything else (then the reason phrase). Only the body and `Retry-After` are read."""
        try:
            data = response.json()
        except (ValueError, RuntimeError):  # not JSON, not UTF-8, or a streamed body nobody read
            data = None
        fields: dict[str, Any] = {}
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                fields = err
            elif isinstance(err, str):
                fields = {"message": err}
            elif isinstance(data.get("message"), str):
                fields = data  # vLLM before its OpenAI-shaped errors, and other plain {"message": ...} bodies
            elif "detail" in data:
                fields = _fastapi_detail(data["detail"])
        message = _text(fields.get("message"), MAX_MESSAGE) or response.reason_phrase or f"HTTP {response.status_code}"
        return cls(
            response.status_code,
            message,
            type=fields.get("type"),
            code=fields.get("code"),
            param=fields.get("param"),
            retry_after=parse_retry_after(response.headers, now),
        )


def _fastapi_detail(detail: Any) -> dict[str, Any]:
    """FastAPI's `detail`: a message, or a list of validation errors whose first one names the field."""
    if isinstance(detail, str):
        return {"message": detail}
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        loc = detail[0].get("loc")
        parts = [str(p) for p in loc if p != "body"] if isinstance(loc, list) else []
        return {"message": detail[0].get("msg"), "param": ".".join(parts) or None}
    return {}


def as_upstream(e: BaseException) -> UpstreamError:
    """Any httpx failure as an UpstreamError: a timeout is 504 `upstream_timeout`, a connection or protocol failure 502
    `upstream_unreachable`, a status error keeps its status, anything else is 502 `bad_upstream_response`. The class name
    stays in the message (`ReadTimeout`); httpx's own message, which carries the URL, does not."""
    if isinstance(e, UpstreamError):
        return e
    name = type(e).__name__
    if isinstance(e, httpx.TimeoutException):
        return UpstreamError(504, f"the upstream model did not answer in time ({name})", code=UPSTREAM_TIMEOUT)
    if isinstance(e, httpx.TransportError):
        return UpstreamError(502, f"could not reach the upstream model ({name})", code=UPSTREAM_UNREACHABLE)
    if isinstance(e, httpx.HTTPStatusError):
        return UpstreamError.from_response(e.response)
    return UpstreamError(502, f"the upstream model's response could not be read ({name})", code=BAD_RESPONSE)


def retryable(e: BaseException) -> bool:
    """Whether another attempt can help (a ReadTimeout is handled separately: once at most)."""
    if isinstance(e, UpstreamError):
        return e.status in RETRY_STATUSES
    return isinstance(e, SAFE_TO_RETRY)


def retry_call(
    fn: Callable[[float], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    rng: Callable[[], float] = random.random,
) -> T:
    """Call `fn(remaining_s)` until it returns, the error is not worth retrying, the attempts run out, or the next wait
    would end past the deadline; then re-raise the last error unchanged. `remaining_s` is the time left before the
    deadline (math.inf without one), so `fn` can cap its own timeouts with it. Only httpx errors (UpstreamError
    included) are retried; anything else propagates at once."""
    deadline = clock() + policy.deadline_s
    read_timeouts = 0
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn(max(deadline - clock(), 1e-3))
        except httpx.HTTPError as e:
            if attempt >= policy.max_attempts:
                raise
            if isinstance(e, httpx.ReadTimeout):
                read_timeouts += 1
                if read_timeouts > 1:  # the model may have been generating, twice: don't pay a third time
                    raise
            elif not retryable(e):
                raise
            asked = e.retry_after if isinstance(e, UpstreamError) else None
            if asked is not None and asked > policy.cap_s:  # the server will refuse a retry sooner than that
                raise
            delay = asked if asked is not None else policy.backoff(attempt - 1, rng)
            if clock() + delay >= deadline:
                raise
            sleep(delay)
    raise AssertionError("unreachable: the last attempt returns or raises")
