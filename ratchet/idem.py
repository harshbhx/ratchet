"""Retry de-duplication (FR-1.70, ADR 0071): a client's retry neither pays twice nor counts as a repeat.

- `IdemCache`: answers by `Idempotency-Key`. The first request with a key runs the cascade; the same key and body while it
  runs waits for it (bounded) and gets its answer; after it finished, the stored answer is sent again without a cascade,
  a ledger row or quota. The same key with another body is refused. In memory only, never on disk: a response holds
  request-derived text (INV-12). Keyed by the API key's name (never the key, INV-13) and the client's key.
- `SeenWindow`: the ledger's memory of when it last counted an example's repeat per sender, so the same sender asking the
  same thing again within `RATCHET_SEEN_DEDUP_S` is not counted again (`Ledger.add_example`); a retry without a key would
  otherwise reach the frequent-request rule (`dataset.FREQUENT`) and move a golden example into training (INV-3).
- `retry_count`: the attempt number OpenAI's (Stainless) SDKs send as `x-stainless-retry-count`.

Standard library only. Every structure is bounded and thread-safe: the gateway answers from AnyIO's worker threads, and
Scout calls the cascade from several threads at once.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_KEY_CHARS = 255
# What an Idempotency-Key may hold once trimmed: 1 to 255 printable ASCII characters (what Stripe and the IETF draft's
# examples use; a header value outside ASCII arrives as Latin-1 mojibake, never as what the client meant).
KEY = re.compile(rf"[\x20-\x7e]{{1,{MAX_KEY_CHARS}}}")
# The most bytes of stored answers an IdemCache keeps, whatever RATCHET_IDEMPOTENCY_CACHE allows: a response can be tens
# of kilobytes (n choices of a long answer), and a full cache must not be what runs a small VM out of memory. The least
# recently used answers go first; one larger than this is shared with the requests waiting for it, then dropped.
MAX_CACHE_BYTES = 64 * 1024 * 1024
# What a finished entry holds besides its body, counted against MAX_CACHE_BYTES: the entry, its Event (a Condition and a
# lock), the key, the headers and the log fields, about 1.9 KB measured with tracemalloc. Without it the byte budget
# would bound only the bodies, and RATCHET_IDEMPOTENCY_CACHE's largest value of tiny answers would hold about 2 GB.
ENTRY_OVERHEAD = 2048
# Requests that may wait at once for one running first request with their key. A waiter holds a worker thread and uses no
# quota, so without a bound one client could hold the whole thread pool with copies of one request; past it a duplicate
# gets InFlight at once (a 409 the SDKs retry). OpenAI's SDKs make at most 2 retries by default.
MAX_WAITERS_PER_KEY = 4
SEEN_ENTRIES = 10_000  # (example, sender) pairs a SeenWindow remembers, the least recently counted dropped first
MAX_RETRY_COUNT = 1_000_000  # a larger x-stainless-retry-count is no SDK's attempt number: read as 0
_RETRY_COUNT = re.compile(r"[0-9]{1,7}")


class KeyReused(Exception):
    """The Idempotency-Key was used with another body (or task header): a client bug no retry can fix."""


class InFlight(Exception):
    """The first request with this key was still running when the wait ran out, or MAX_WAITERS_PER_KEY requests were
    already waiting for it."""


def check_key(value: str) -> str:
    """The Idempotency-Key a request sent, trimmed; ValueError when it is empty, longer than 255 characters or not
    printable ASCII."""
    key = value.strip()
    if not KEY.fullmatch(key):
        raise ValueError(f"an Idempotency-Key is 1 to {MAX_KEY_CHARS} printable ASCII characters")
    return key


def fingerprint(body: Any, task_header: str | None = None) -> str:
    """sha256 of the request as parsed JSON with sorted keys (so a client that serialises its retry again in another key
    order still matches), with the X-Ratchet-Task header, which changes what the request means. Headers that differ
    between attempts (the retry count, a client request id) are not part of it."""
    blob = json.dumps({"body": body, "task": task_header}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()


def retry_count(value: str | None) -> int:
    """The attempt number in `x-stainless-retry-count` (0 for the first attempt); 0 when missing or not a plain count.
    It is telemetry, not a contract, so a malformed value is ignored rather than refused."""
    if value is None:
        return 0
    value = value.strip()
    if not _RETRY_COUNT.fullmatch(value):
        return 0
    n = int(value)
    return n if n <= MAX_RETRY_COUNT else 0


@dataclass(frozen=True)
class Cached:
    """A finished answer as the client got it: the body (one JSON document, or each Server-Sent Event of a stream), its
    media type and the headers safe to send again (no rate-limit or retry headers: a replay uses no quota). `task` and
    `log` are the request's task and its log line's fields, so the replay's own log line says what it replayed."""

    body: tuple[bytes, ...]
    media_type: str
    stream: bool
    headers: tuple[tuple[str, str], ...] = ()
    task: str | None = None
    log: Mapping[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(len(b) for b in self.body)


@dataclass(eq=False)
class _Entry:
    fingerprint: str
    done: threading.Event = field(default_factory=threading.Event)
    response: Cached | None = None
    finished_at: float | None = None
    size: int = 0
    waiters: int = 0  # requests waiting for it now (`IdemCache.begin`)


@dataclass(frozen=True, eq=False)
class Claim:
    """What `IdemCache.begin` decided. `cached` set: send it (the request ran before, or ran while this one waited, which
    `joined` says). Otherwise this request owns the key and must call `IdemCache.finish` exactly once, with its answer or
    None (`try`/`finally`), or every later request with the key waits out its bound."""

    cached: Cached | None = None
    joined: bool = False
    key: tuple[str, str] | None = None
    entry: _Entry | None = None

    @property
    def owner(self) -> bool:
        return self.cached is None


class IdemCache:
    """Answers by (scope, Idempotency-Key): an OrderedDict kept in least-recently-used order under one lock, with at most
    `max_entries` entries and `max_bytes` (MAX_CACHE_BYTES) of finished answers, each counted as its body plus
    `entry_overhead` (ENTRY_OVERHEAD) for what the entry itself holds. A finished answer is kept `ttl_s` seconds on
    `clock` from when it finished (0: none is kept, so only a request that arrives while the first runs shares its
    answer). `max_entries` 0 turns the cache off (`enabled`): the gateway then ignores Idempotency-Key. Only successful
    answers are stored: a request that failed releases its key, so the client's retry runs again (that is what a retry
    is for). At most `max_waiters` requests wait for one running entry at a time."""

    def __init__(
        self,
        max_entries: int = 10_000,
        ttl_s: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        max_bytes: int = MAX_CACHE_BYTES,
        max_waiters: int = MAX_WAITERS_PER_KEY,
        entry_overhead: int = ENTRY_OVERHEAD,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 0:
            raise ValueError(f"max_entries must be a whole number, 0 or more, not {max_entries!r}")
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)) or not 0 <= ttl_s < float("inf"):
            raise ValueError(f"ttl_s must be a number of seconds, 0 or more, not {ttl_s!r}")
        self.max_entries = max_entries
        self.ttl_s = float(ttl_s)
        self.clock = clock
        self.max_bytes = max_bytes
        self.max_waiters = max_waiters
        self.entry_overhead = entry_overhead
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._bytes = 0
        self._next_sweep = 0.0
        self._waiting = 0  # requests waiting for a running first request with their key

    @property
    def enabled(self) -> bool:
        return self.max_entries > 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def stats(self) -> dict[str, int]:
        """Entries kept, how many of them are still running, the requests waiting for one, and the bytes of stored
        answers (bodies plus `entry_overhead` each)."""
        with self._lock:
            running = sum(1 for e in self._entries.values() if e.response is None)
            return {"entries": len(self._entries), "in_flight": running, "waiting": self._waiting, "bytes": self._bytes}

    def begin(self, scope: str, key: str, fingerprint: str, wait_s: float) -> Claim:
        """Claim `key` for this request, or get the answer it already has. While another request holds the key with the
        same `fingerprint`, wait for it at most `wait_s` seconds of real time (`time.monotonic`, whatever `clock` is):
        its answer when it ends, or the key when it fails, so this request runs instead. Raises KeyReused for another
        fingerprint (at once, running or finished), and InFlight when the wait runs out or `max_waiters` requests already
        wait for the key (at once)."""
        k = (scope, key)
        deadline = time.monotonic() + max(0.0, wait_s)
        joined = False
        while True:
            with self._lock:
                now = self.clock()
                self._sweep(now)
                e = self._entries.get(k)
                if e is not None and self._expired(e, now):
                    self._drop(k)
                    e = None
                if e is None:
                    e = _Entry(fingerprint)
                    self._entries[k] = e
                    self._evict()
                    return Claim(key=k, entry=e, joined=joined)
                if e.fingerprint != fingerprint:
                    raise KeyReused(key)
                if e.response is not None:
                    self._entries.move_to_end(k)
                    return Claim(cached=e.response, joined=joined)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or e.waiters >= self.max_waiters:
                    raise InFlight(key)
                e.waiters += 1
                self._waiting += 1
            try:
                ended = e.done.wait(remaining)
            finally:
                with self._lock:
                    e.waiters -= 1
                    self._waiting -= 1
            if not ended:
                raise InFlight(key)
            joined = True
            if e.response is not None:  # finished while this request waited, kept or not (ttl 0, too large, evicted)
                return Claim(cached=e.response, joined=True)
            # It failed and released the key: claim it, or wait for whoever claimed it first.

    def finish(self, claim: Claim, response: Cached | None) -> None:
        """End the owner's claim: keep `response` for `ttl_s` (None, after a failure or for an answer that cannot be
        stored, releases the key), and wake every request waiting for it, which get `response` even when it is not kept.
        Safe to call for a claim that is not an owner's (it does nothing) and more than once (only the first counts)."""
        e, k = claim.entry, claim.key
        if e is None or k is None:
            return
        with self._lock:
            if e.done.is_set():
                return
            e.response = response
            current = self._entries.get(k) is e
            size = (response.size + self.entry_overhead) if response is not None else 0
            keep = response is not None and self.ttl_s > 0 and size <= self.max_bytes
            if current and keep:
                e.finished_at = self.clock()
                e.size = size
                self._bytes += e.size
                self._entries.move_to_end(k)
                self._evict()
            elif current:
                self._drop(k)
            e.done.set()

    def _expired(self, e: _Entry, now: float) -> bool:
        return e.finished_at is not None and not 0 <= now - e.finished_at < self.ttl_s

    def _drop(self, k: tuple[str, str]) -> None:
        e = self._entries.pop(k)
        self._bytes -= e.size

    def _evict(self) -> None:
        """Drop the least recently used finished answers until the entry count and the bytes fit. A running entry is
        never dropped: its owner and the requests waiting for it still need it (they are bounded by the thread pool)."""
        while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
            victim = next((k for k, e in self._entries.items() if e.response is not None), None)
            if victim is None:
                return
            self._drop(victim)

    def _sweep(self, now: float) -> None:
        """Drop every expired answer, at most once per half TTL (one pass over at most `max_entries` entries), so expired
        answers do not hold memory until the cache fills."""
        if now < self._next_sweep:
            return
        self._next_sweep = now + max(self.ttl_s, 1.0) / 2
        for k in [k for k, e in self._entries.items() if self._expired(e, now)]:
            self._drop(k)


class SeenWindow:
    """When the ledger last counted a repeat of each (example hash, sender), for `window_s` seconds on `clock`, in at most
    `max_entries` pairs (least recently counted dropped first, so a flood of distinct examples can only make the window
    forget, never grow). `count` says whether a repeat counts now; `note` records a count that happened (a new example
    is one). Thread-safe on its own; the ledger also calls it under its lock, so a check and the write it decides are one
    step."""

    def __init__(self, window_s: float, max_entries: int = SEEN_ENTRIES, clock: Callable[[], float] = time.monotonic) -> None:
        if isinstance(window_s, bool) or not isinstance(window_s, (int, float)) or not 0 <= window_s < float("inf"):
            raise ValueError(f"window_s must be a number of seconds, 0 or more, not {window_s!r}")
        self.window_s = float(window_s)
        self.max_entries = max_entries
        self.clock = clock
        self._lock = threading.Lock()
        self._last: OrderedDict[tuple[str, str], float] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._last)

    def count(self, example: str, sender: str) -> bool:
        """True when a repeat of `example` from `sender` counts now (none counted within the window, and then this one is
        remembered); False when one was counted less than `window_s` ago (nothing changes, so a client retrying every
        few minutes still gets counted once per window)."""
        k = (example, sender)
        with self._lock:
            now = self.clock()
            last = self._last.get(k)
            if last is not None and 0 <= now - last < self.window_s:
                return False
            self._remember(k, now)
            return True

    def note(self, example: str, sender: str) -> None:
        """Remember a count that happened now (a new example)."""
        with self._lock:
            self._remember((example, sender), self.clock())

    def _remember(self, k: tuple[str, str], now: float) -> None:
        self._last[k] = now
        self._last.move_to_end(k)
        while len(self._last) > self.max_entries:
            self._last.popitem(last=False)
