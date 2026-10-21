"""The gateway's own rate limits (FR-12.3, ADR 0063): requests a minute per API key (or per client IP on a gateway without
keys), requests in flight per key, and chat requests in flight in all.

Every limit refuses at once instead of making the request wait: a client over its rate gets a 429 with the time until a
request would pass, and a gateway at its in-flight cap a 503, before the request takes one of the worker threads. A
refused request reads no body and calls no model. The state is per process (one worker, ADR 0006) and keyed by the key's
name or the client's IP, never by a key (INV-13). Standard library only.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import NamedTuple

MAX_BUCKETS = 10_000  # per-IP buckets kept; past it the least recently used is dropped (a full bucket loses nothing)
INFLIGHT = "*"  # the in-flight counter's name in `Concurrency`


def burst(rpm: int) -> int:
    """How many requests a bucket of `rpm` requests a minute lets through at once: ten seconds' worth (`rpm // 6`), and
    at least 1."""
    return max(1, rpm // 6)


class Decision(NamedTuple):
    """A token bucket's answer: whether the request may go, the bucket's limit (requests a minute), the whole requests
    left in it, seconds until one more is there (0 when allowed) and seconds until it is full again."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_s: float
    reset_s: float


class TokenBucket:
    """`rpm` requests a minute on average and at most `capacity` at once: the bucket starts full, each request takes a
    token, and tokens come back continuously at `rpm` a minute. A clock that moves back adds no tokens."""

    def __init__(self, rpm: int, capacity: int, now: float) -> None:
        if rpm <= 0 or capacity <= 0:
            raise ValueError(f"a token bucket needs a rate and a capacity above 0, not {rpm!r} and {capacity!r}")
        self.rpm = rpm
        self.capacity = capacity
        self.rate = rpm / 60.0  # tokens a second
        self.tokens = float(capacity)
        self.stamp = now

    def _refill(self, now: float) -> None:
        if now > self.stamp:  # a clock that moved back refills nothing until it passes the last reading again
            self.tokens = min(float(self.capacity), self.tokens + (now - self.stamp) * self.rate)
            self.stamp = now

    def take(self, now: float) -> Decision:
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return Decision(True, self.rpm, int(self.tokens), 0.0, (self.capacity - self.tokens) / self.rate)
        return Decision(False, self.rpm, 0, (1.0 - self.tokens) / self.rate, (self.capacity - self.tokens) / self.rate)


class RateLimiter:
    """Token buckets by name, made on first use with `burst(rpm)` capacity. At most `max_buckets` are kept; past that the
    least recently used goes, so a flood of client IPs cannot grow memory without bound (a dropped bucket that was not
    full forgets what its IP used). A bucket whose rate changed starts again full. Thread-safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_buckets: int = MAX_BUCKETS) -> None:
        self.clock = clock
        self.max_buckets = max_buckets
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()

    def take(self, name: str, rpm: int) -> Decision | None:
        """Take a token from `name`'s bucket of `rpm` requests a minute; None when `rpm` is 0 (no limit)."""
        if rpm <= 0:
            return None
        with self._lock:
            now = self.clock()
            bucket = self._buckets.get(name)
            if bucket is None or bucket.rpm != rpm:
                bucket = self._buckets[name] = TokenBucket(rpm, burst(rpm), now)
                self._buckets.move_to_end(name)
                while len(self._buckets) > self.max_buckets:
                    self._buckets.popitem(last=False)
            else:
                self._buckets.move_to_end(name)
            return bucket.take(now)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


class Concurrency:
    """Requests in flight by name, refused past a limit instead of queued: `acquire` takes a slot when one is free and
    says whether it did, `release` gives it back. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n: dict[str, int] = {}

    def acquire(self, name: str, limit: int) -> bool:
        with self._lock:
            n = self._n.get(name, 0)
            if n >= limit:
                return False
            self._n[name] = n + 1
            return True

    def release(self, name: str) -> None:
        with self._lock:
            n = self._n.get(name, 0) - 1
            if n > 0:
                self._n[name] = n
            else:
                self._n.pop(name, None)

    def count(self, name: str) -> int:
        with self._lock:
            return self._n.get(name, 0)


class Refused(Exception):
    """A request over one of the gateway's limits. `kind` is `overloaded` (the in-flight cap: 503), `rate` (a token
    bucket: 429) or `concurrency` (a key's in-flight cap: 429); `who` is the key name, `ip` for a client IP's bucket, or
    None for the in-flight cap; `limit` the limit it hit; `decision` the bucket's answer for `rate`."""

    def __init__(self, kind: str, who: str | None, limit: int, decision: Decision | None = None) -> None:
        super().__init__(f"{kind} limit of {limit} reached")
        self.kind = kind
        self.who = who
        self.limit = limit
        self.decision = decision


class Slot:
    """What an admitted request holds: its in-flight slots, given back once by `release` (also as a context manager),
    and the request-rate decision of its key or IP bucket (None when no rate applies), for the response's headers."""

    def __init__(self, limiter: Limiter, held: list[str], decision: Decision | None) -> None:
        self._limiter = limiter
        self._held = held
        self.decision = decision

    def release(self) -> None:
        held, self._held = self._held, []
        for name in held:
            self._limiter.concurrency.release(name)

    def __enter__(self) -> Slot:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class Limiter:
    """The gateway's limits for one request (`admit`), checked in this order so a refusal holds nothing and takes no
    token: the in-flight cap (`max_inflight`, 0 = off), the key's in-flight cap, then the key's or the IP's token bucket
    (`ip_rpm` applies only to requests without a key). `clock` is monotonic (tests pass a fake one)."""

    def __init__(self, max_inflight: int = 0, ip_rpm: int = 0, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_inflight = max_inflight
        self.ip_rpm = ip_rpm
        self.concurrency = Concurrency()
        self.keys = RateLimiter(clock)
        self.ips = RateLimiter(clock)

    @property
    def inflight(self) -> int:
        return self.concurrency.count(INFLIGHT)

    def admit(self, client: str | None, rpm: int = 0, concurrency: int = 0, ip: str | None = None) -> Slot:
        """Admit one request from the key `client` (with its `rpm` and `concurrency` limits; 0 = off), or from the client
        IP `ip` when it has no key. Raises `Refused` over a limit, having given back whatever it took."""
        held: list[str] = []
        try:
            if self.max_inflight > 0:
                if not self.concurrency.acquire(INFLIGHT, self.max_inflight):
                    raise Refused("overloaded", None, self.max_inflight)
                held.append(INFLIGHT)
            if client is not None and concurrency > 0:
                name = "key:" + client
                if not self.concurrency.acquire(name, concurrency):
                    raise Refused("concurrency", client, concurrency)
                held.append(name)
            if client is not None:
                decision = self.keys.take(client, rpm)
                who: str | None = client
            else:
                decision = self.ips.take(ip or "unknown", self.ip_rpm)
                who = "ip"
            if decision is not None and not decision.allowed:
                raise Refused("rate", who, decision.limit, decision)
        except BaseException:
            for name in held:
                self.concurrency.release(name)
            raise
        return Slot(self, held, decision)
