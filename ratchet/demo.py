"""Limits for the shared public simulation (FR-11.30, ADR 0133).

Every visitor of the simulation host sees and changes one mock state. These limits keep one visitor from monopolising it and
keep its memory bounded over a judging fortnight: a per-IP rate on the demo actions (the gateway's `ratelimit.RateLimiter`)
and an automatic reset past a number of simulated days or requests, or after idle time. They act in mock mode only and only
when set; every default is off. Standard library only; the gateway wires the callbacks.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_DAYS_LIMIT = 3650
MAX_REQUESTS_LIMIT = 10_000_000
MAX_RPM = 600
IDLE_MIN_S, IDLE_MAX_S = 300.0, 604_800.0
REASONS = ("max_days", "max_requests", "idle")
_log = logging.getLogger("ratchet.demo")


@dataclass(frozen=True)
class DemoPolicy:
    """`rpm` demo actions a minute per client IP, `max_days` simulated days, `max_requests` ledger requests and
    `idle_reset_s` seconds of silence before the shared simulation resets itself. 0 is off for each."""

    rpm: int = 0
    max_days: int = 0
    max_requests: int = 0
    idle_reset_s: float = 0.0

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "max_days": self.max_days or None,
            "max_requests": self.max_requests or None,
            "idle_reset_s": self.idle_reset_s or None,
            "actions_per_minute": self.rpm or None,
        }

    @property
    def background(self) -> bool:
        """Whether a janitor thread is needed (the day cap is enforced on a click instead)."""
        return bool(self.idle_reset_s or self.max_requests)


def check_policy(
    rpm: object, max_days: object, max_requests: object, idle_s: object, warmup_days: int, warmup_requests: int
) -> DemoPolicy:
    """The four RATCHET_DEMO_* settings, checked: ValueError naming the variable. The two warm-up rules keep a cap from
    resetting the simulation right after its own warm-up, which would loop."""

    def whole(name: str, value: object, hi: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= hi:
            raise ValueError(f"{name} must be a whole number from 0 (off) to {hi}, not {value!r}")
        return value

    r = whole("RATCHET_DEMO_RPM", rpm, MAX_RPM)
    d = whole("RATCHET_DEMO_MAX_DAYS", max_days, MAX_DAYS_LIMIT)
    n = whole("RATCHET_DEMO_MAX_REQUESTS", max_requests, MAX_REQUESTS_LIMIT)
    if d and d <= warmup_days:
        raise ValueError(f"RATCHET_DEMO_MAX_DAYS must be 0 or above RATCHET_MOCK_WARMUP_DAYS ({warmup_days}), not {d}")
    if n and n <= warmup_days * warmup_requests:
        raise ValueError(
            f"RATCHET_DEMO_MAX_REQUESTS must be 0 or above RATCHET_MOCK_WARMUP_DAYS x RATCHET_MOCK_WARMUP_REQUESTS "
            f"({warmup_days * warmup_requests}), not {n}"
        )
    bad_idle = isinstance(idle_s, bool) or not isinstance(idle_s, (int, float)) or idle_s != idle_s
    if bad_idle or (idle_s and not IDLE_MIN_S <= idle_s <= IDLE_MAX_S):  # type: ignore[operator]
        raise ValueError(f"RATCHET_DEMO_IDLE_RESET_S must be 0 (off) or from {IDLE_MIN_S:g} to {IDLE_MAX_S:g}, not {idle_s!r}")
    return DemoPolicy(r, d, n, float(idle_s))  # type: ignore[arg-type]


class DemoJanitor:
    """Resets the shared simulation when it is due: past `max_requests`, or idle for `idle_reset_s` while dirty. One per
    gateway process, so it survives `Runtime.reset()`. `reset(reason)` is the gateway's callback and says whether it ran
    (False when a demo action holds the lock); `counts()` is the ledger's request total. `thread_factory` lets a test see
    the start without a thread. `stop()` ends its thread for good (the app's shutdown): its callbacks read the gateway
    module's globals, so a thread that outlived the module's state it was built for (a test that reloads the module)
    would reset whichever gateway is current."""

    def __init__(
        self,
        policy: DemoPolicy,
        reset: Callable[[str], bool],
        counts: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self.policy = policy
        self._reset = reset
        self._counts = counts
        self.clock = clock
        self.wall = wall
        self.thread_factory = thread_factory
        self._lock = threading.Lock()
        self._started = False
        self._stopping = threading.Event()
        self._touched = clock()
        self._seen_requests = 0  # the request count at the last activity (or reset)
        self._clean_requests = 0  # the request count right after the last reset
        self._dirty = False
        self.auto_resets = 0
        self.last_reset_at: float | None = None

    def touch(self) -> None:
        with self._lock:
            self._touched = self.clock()
            self._dirty = True

    def mark_reset(self, requests_now: int, auto: bool = False) -> None:
        """Record a reset: the state is clean again. `auto` counts it as one of the simulation's own resets."""
        with self._lock:
            self._clean_requests = self._seen_requests = requests_now
            self._touched = self.clock()
            self._dirty = False
            if auto:
                self.auto_resets += 1
                self.last_reset_at = self.wall()

    def due(self, requests_total: int, day: int, now: float) -> str | None:
        p = self.policy
        if p.max_requests and requests_total >= p.max_requests:
            return "max_requests"
        if p.max_days and day >= p.max_days:
            return "max_days"
        if p.idle_reset_s:
            with self._lock:
                if requests_total != self._seen_requests:  # /v1 traffic is activity
                    self._seen_requests = requests_total
                    self._touched = now
                dirty = self._dirty or requests_total != self._clean_requests
                idle = now - self._touched
            if dirty and idle >= p.idle_reset_s:
                return "idle"
        return None

    def check(self, day: int = 0, now: float | None = None) -> str | None:
        """One pass: reset when due and the gateway lets it. Returns the reason that ran, else None. The day cap is not
        checked here by the thread (`day` defaults to 0): a click enforces it, so nobody loses a rollback they watch."""
        now = self.clock() if now is None else now
        reason = self.due(self._counts(), day, now)
        if reason is not None and self._reset(reason):
            return reason
        return None

    def ensure_started(self) -> None:
        """Start the daemon thread once, when a time or request cap is set. Never at import, in live mode or by default,
        and never again after `stop()`."""
        if self._started or not self.policy.background:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
        every = min(60.0, max(10.0, self.policy.idle_reset_s / 4)) if self.policy.idle_reset_s else 60.0

        def loop() -> None:
            while not self._stopping.wait(every):  # a stop wakes it at once
                try:
                    self.check()
                except Exception as e:  # the janitor must never die
                    _log.warning("demo janitor pass failed: %s", type(e).__name__)

        self.thread_factory(target=loop, name="demo-janitor", daemon=True).start()

    def stop(self) -> None:
        """End the thread, if one runs, and never start another: no pass runs after this returns (one already past its
        wake-up finishes). Safe to call twice and without a thread."""
        self._stopping.set()
        with self._lock:
            self._started = True

    def snapshot(self) -> dict[str, float | int | None]:
        return {"auto_resets": self.auto_resets, "last_reset_at": self.last_reset_at}


def reset_event(reason: str, policy: DemoPolicy) -> str:
    """The `demo_reset` event's text: reasons and counts only."""
    idle = f"{policy.idle_reset_s / 3600:g} h" if policy.idle_reset_s >= 3600 else f"{policy.idle_reset_s:g} s"
    why = {
        "idle": f"after {idle} without activity (RATCHET_DEMO_IDLE_RESET_S)",
        "max_days": f"after {policy.max_days} simulated days (RATCHET_DEMO_MAX_DAYS)",
        "max_requests": f"after {policy.max_requests} requests (RATCHET_DEMO_MAX_REQUESTS)",
    }.get(reason, "")
    return f"the shared simulation reset {why}; the warm-up ran again"
