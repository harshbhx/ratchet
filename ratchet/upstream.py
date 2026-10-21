"""The last real upstream call per role, content-free (FR-11.31, ADR 0133).

One process-local, in-memory record: which role called (teacher, fallback teacher, student, Tavily), on which kind of host,
with which model ids, whether it worked, how long it took and when. It never holds a prompt or an answer, a URL, a key, a
header, an error message or the upstream's own error code (INV-12, INV-13): a response's `model` is upstream-controlled text,
so it is kept only when it looks like a model id. The observer runs in a `finally` in the provider and cannot raise, so
recording never changes a model call (INV-7, INV-15). Standard library and httpx's exception classes only.
"""

from __future__ import annotations

import functools
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .resilience import BAD_RESPONSE, UPSTREAM_TIMEOUT, UPSTREAM_UNREACHABLE, UpstreamError

ROLES = ("teacher", "fallback_teacher", "student", "tavily")
HOSTS = ("token_factory", "student_endpoint", "tavily", "other")
REASONS = ("timeout", "unreachable", "bad_response", "http", "other")
# Token Factory's host (docs.tokenfactory.nebius.com/quickstart); config.py's default RATCHET_TEACHER_BASE is on it, and a
# test ties the two together. Unverified with a real key until scripts/day1_check.py passes (INV-9).
TOKEN_FACTORY_HOST = "api.tokenfactory.nebius.com"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_LOCAL_CODES = {UPSTREAM_TIMEOUT: "timeout", UPSTREAM_UNREACHABLE: "unreachable", BAD_RESPONSE: "bad_response"}


def host_of(base_url: str) -> str:
    """`token_factory` when the URL's host is Token Factory's, else `other`. The URL itself is never stored."""
    try:
        host = urlsplit(base_url if isinstance(base_url, str) else "").hostname
    except ValueError:
        host = None
    return "token_factory" if host == TOKEN_FACTORY_HOST else "other"


def model_id(value: object) -> str | None:
    """`value` when it is a model id (the pattern in full, no `://`), else None."""
    if isinstance(value, str) and MODEL_ID.fullmatch(value) and "://" not in value:
        return value
    return None


def classify(e: BaseException) -> tuple[str, str, int | None]:
    """(error, reason, status) for a failed call: the exception's class name, a fixed reason, and the HTTP status when the
    upstream answered. Never the message, the response body or the upstream's own error code."""
    name = type(e).__name__
    error = name if _IDENT.fullmatch(name) else "Exception"
    if isinstance(e, UpstreamError):
        local = _LOCAL_CODES.get(e.code or "")
        return (error, local, None) if local else (error, "http", e.status)
    if isinstance(e, httpx.TimeoutException):
        return error, "timeout", None
    if isinstance(e, httpx.HTTPStatusError):
        return error, "http", e.response.status_code
    if isinstance(e, httpx.DecodingError):
        return error, "bad_response", None
    if isinstance(e, httpx.TransportError):
        return error, "unreachable", None
    return error, "other", None


@dataclass(frozen=True)
class Call:
    role: str
    host: str
    requested_model: str | None
    model: str | None
    ok: bool
    status: int | None
    error: str | None
    reason: str | None
    latency_ms: float
    at: float


Observer = Callable[..., None]


class UpstreamRecorder:
    """The newest `Call` per role, and the time of each role's last success. `bind(role, host)` gives a provider its
    observer. `aliases` maps (role, requested model) to another role: a fallback teacher that shares the teacher's client
    is recorded as `fallback_teacher`. `record` never raises (a bad record is counted in `dropped`)."""

    def __init__(self, clock: Callable[[], float] = time.time, aliases: dict[tuple[str, str], str] | None = None) -> None:
        self.clock = clock
        self.aliases = dict(aliases or {})
        self.since = clock()
        self.dropped = 0
        self._lock = threading.Lock()
        self._last: dict[str, Call] = {}
        self._last_ok: dict[str, float] = {}

    def bind(self, role: str, host: str) -> Observer:
        if role not in ROLES:
            raise ValueError(f"unknown upstream role {role!r}; use one of {list(ROLES)}")
        if host not in HOSTS:
            raise ValueError(f"unknown upstream host {host!r}; use one of {list(HOSTS)}")
        return functools.partial(self.record, role, host)

    def record(
        self,
        role: str,
        host: str,
        requested_model: object = None,
        *,
        served_model: object = None,
        status: object = None,
        error: BaseException | None = None,
        latency_ms: object = 0.0,
    ) -> None:
        try:
            requested = model_id(requested_model)
            if isinstance(requested_model, str) and requested is not None:
                role = self.aliases.get((role, requested), role)
            if role not in ROLES or host not in HOSTS:
                raise ValueError("unknown role or host")
            at = self.clock()
            ms = round(float(latency_ms), 1)  # type: ignore[arg-type]
            if error is None:
                code = status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None
                call = Call(role, host, requested, model_id(served_model), True, code, None, None, ms, at)
            else:
                err, reason, code = classify(error)
                call = Call(role, host, requested, model_id(served_model), False, code, err, reason, ms, at)
            with self._lock:
                self._last[role] = call
                if call.ok:
                    self._last_ok[role] = at
        except Exception:  # recording must never change a model call
            self.dropped += 1

    def last(self, role: str) -> Call | None:
        with self._lock:
            return self._last.get(role)

    def last_ok_at(self, role: str) -> float | None:
        with self._lock:
            return self._last_ok.get(role)

    def last_ok_age(self, role: str, now: float | None = None) -> float | None:
        at = self.last_ok_at(role)
        return None if at is None else max(0.0, (self.clock() if now is None else now) - at)

    def snapshot(self, now: float | None = None) -> list[dict[str, Any]]:
        """One dict per role called since start, in `ROLES` order: the `Call` fields plus `age_s` and `last_ok_age_s`."""
        now = self.clock() if now is None else now
        with self._lock:
            calls = [(self._last[r], self._last_ok.get(r)) for r in ROLES if r in self._last]
        out = []
        for c, ok_at in calls:
            row = asdict(c)
            row["age_s"] = round(max(0.0, now - c.at), 1)
            row["last_ok_age_s"] = None if ok_at is None else round(max(0.0, now - ok_at), 1)
            out.append(row)
        return out
