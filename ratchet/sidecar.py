"""A bounded pool for the work a request need not wait for: the teacher's audit of a served answer, the shadow's samples
and the teacher's arbitration (ADR 0072).

`SideCar(workers, max_queue)`. A request takes a place first (`reserve(size=job_bytes(messages))`, never blocking; None
when the queue is full, the pool is closed or it has no workers), writes its ledger row, then hands the job over
(`Slot.submit(fn, tag)`) or gives the place back (`Slot.cancel()`). Reserving before the row is written lets the request
decide, while it can still do the work itself, whether its checks run here or on the request path. A job that carries
an audit reserves with `priority=True` and may use the last `audit_places` places, which a shadow-only job may not, so a
burst of shadow work cannot push audits onto the request path. The router never drops an audit that was drawn (a sample
that depended on load would bias the release statistics): when even the reserved places are taken, it runs the audit
inline. Besides the count of waiting jobs, `max_bytes` bounds the request text the jobs hold until they end (each keeps
its request's messages to call the teacher with), so a queue of long requests cannot exhaust memory: a reservation that
would pass it is refused like one for a full queue, with the same share kept for audits.

`workers=0` (mock mode and the simulator, by default) runs nothing here: the router keeps every check on the request
path, exactly as before.

Jobs run in daemon threads started on first use, each in a copy of the submitting thread's context variables (the
gateway's request context: log lines and failures rows name the request). A job that raises is counted and logged by its
exception's class, never raised. `drain(timeout_s)` waits for the queue to empty; `close(timeout_s)` takes no new
places, drains, and returns what it could not run, counted by tag. A job handed over after `close` gave up is refused
(`Slot.submit` returns False), so the request that holds it, still on its own path, runs it itself.
"""

from __future__ import annotations

import contextvars
import logging
import math
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

from . import metrics, obs

AUDIT_RESERVE = 0.1  # the share of the queue (places and bytes) only jobs that carry an audit may take
MAX_WORKERS = 64  # RATCHET_SIDECAR_WORKERS
MAX_QUEUE = 100_000  # RATCHET_SIDECAR_QUEUE
# The request text queued and running jobs may hold (`job_bytes`). At RATCHET_MAX_INPUT_CHARS' default (120,000) a job
# holds at most about 0.5 MB, so the budget only binds when the queue fills with long requests.
MAX_BYTES = 64 * 1024 * 1024
MESSAGE_OVERHEAD = 256  # bytes counted per message and per nested value, besides its strings


def job_bytes(value: Any) -> int:
    """About how much memory a job's reference to the request's messages keeps alive: every string as Python stores it
    (`sys.getsizeof`, so non-ASCII text counts its wider storage) plus MESSAGE_OVERHEAD per dict or list. One pass over
    the messages, no copy and no encoding."""
    total, stack = 0, [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            total += sys.getsizeof(v)
        elif isinstance(v, dict):
            total += MESSAGE_OVERHEAD
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            total += MESSAGE_OVERHEAD
            stack.extend(v)
    return total


@dataclass
class _Job:
    fn: Callable[[], object]
    tag: Hashable
    context: contextvars.Context
    queued_at: float
    size: int


class Slot:
    """A place in a SideCar's queue (`SideCar.reserve`). Submit one job into it, or cancel it; either ends it."""

    __slots__ = ("_car", "_open", "priority", "size")

    def __init__(self, car: SideCar, priority: bool, size: int) -> None:
        self._car = car
        self._open = True
        self.priority = priority
        self.size = size

    def submit(self, fn: Callable[[], object], tag: Hashable = None) -> bool:
        """Queue `fn` to run in a copy of this thread's context; `tag` is what `close` counts it under if it never runs.
        False when the pool finished closing first: nothing is queued, and the caller runs `fn` itself."""
        return self._car._submit(self, fn, tag)

    def cancel(self) -> None:
        """Give the place back unused (the request failed before its job could be queued)."""
        self._car._cancel(self)


class SideCar:
    """A bounded, non-blocking job queue on `workers` daemon threads. `max_queue` bounds the jobs waiting (reserved or
    queued, not running); `audit_places` of them only a `priority` reservation may take. `max_bytes` bounds the sizes
    reservations declare (`job_bytes`) until their jobs end, running ones included; `audit_bytes` of it is kept for
    `priority` reservations in the same way. `clock` times the jobs for `ratchet_sidecar_job_seconds`."""

    def __init__(
        self,
        workers: int = 0,
        max_queue: int = 1000,
        *,
        max_bytes: int = MAX_BYTES,
        reserve: float = AUDIT_RESERVE,
        clock: Callable[[], float] = time.monotonic,
        name: str = "ratchet-sidecar",
    ) -> None:
        if isinstance(workers, bool) or not isinstance(workers, int) or not 0 <= workers <= MAX_WORKERS:
            raise ValueError(f"workers must be a whole number from 0 to {MAX_WORKERS}, not {workers!r}")
        if isinstance(max_queue, bool) or not isinstance(max_queue, int) or not 1 <= max_queue <= MAX_QUEUE:
            raise ValueError(f"max_queue must be a whole number from 1 to {MAX_QUEUE}, not {max_queue!r}")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError(f"max_bytes must be a whole number above 0, not {max_bytes!r}")
        if isinstance(reserve, bool) or not isinstance(reserve, (int, float)) or not 0 <= reserve < 1:
            raise ValueError(f"reserve must be a share from 0 to less than 1, not {reserve!r}")
        self.workers = workers
        self.max_queue = max_queue
        self.max_bytes = max_bytes
        # At least one place for audits once the queue has two, never every place.
        self.audit_places = 0 if max_queue < 2 or reserve == 0 else min(max_queue - 1, max(1, math.floor(max_queue * reserve)))
        self.audit_bytes = math.floor(max_bytes * reserve)
        self.clock = clock
        self.name = name
        self._cv = threading.Condition()
        self._jobs: deque[_Job] = deque()
        self._reserved = 0  # places taken, jobs not submitted yet
        self._bytes = 0  # the sizes of reserved, queued and running jobs
        self._running = 0
        self._closed = False  # no new places
        self._stopped = False  # close() finished: workers exit once the queue is empty, nothing is queued any more
        self._threads: list[threading.Thread] = []
        self._counts: Counter[str] = Counter()  # queued, done, failed, full, refused, abandoned

    @property
    def inline(self) -> bool:
        """True when this pool runs nothing: the caller keeps every check on the request path."""
        return self.workers == 0

    @property
    def accepting(self) -> bool:
        """True while this pool takes jobs: it has workers and `close` has not been called. A caller asks before it
        reserves, so a closed pool is not mistaken for a full one."""
        return self.workers > 0 and not self._closed

    def reserve(self, priority: bool = False, size: int = 0) -> Slot | None:
        """A place in the queue for a job that holds `size` bytes (`job_bytes`), or None (never waiting) when the pool
        has no workers, is closed, or is full: a `priority` reservation may take every place and byte, any other leaves
        the last `audit_places` and `audit_bytes` free. A job larger than the whole budget is never taken."""
        if self.inline:
            return None
        size = max(0, int(size))
        places = self.max_queue if priority else self.max_queue - self.audit_places
        budget = self.max_bytes if priority else self.max_bytes - self.audit_bytes
        with self._cv:
            if self._closed:
                return None
            if self._reserved + len(self._jobs) >= places or self._bytes + size > budget:
                self._counts["full"] += 1
                return None
            self._reserved += 1
            self._bytes += size
        return Slot(self, priority, size)

    def _end(self, slot: Slot) -> None:
        if not slot._open:
            raise RuntimeError("a sidecar slot takes one job, or one cancel")
        slot._open = False
        self._reserved -= 1

    def _cancel(self, slot: Slot) -> None:
        with self._cv:
            self._end(slot)
            self._bytes -= slot.size
            self._cv.notify_all()

    def _submit(self, slot: Slot, fn: Callable[[], object], tag: Hashable) -> bool:
        job = _Job(fn, tag, contextvars.copy_context(), self.clock(), slot.size)
        with self._cv:
            self._end(slot)
            if self._stopped:  # closing gave up on this place before the job came: the caller runs it
                self._bytes -= slot.size
                self._counts["refused"] += 1
                self._cv.notify_all()
                return False
            self._jobs.append(job)
            self._counts["queued"] += 1
            self._start_workers()
            self._cv.notify_all()
        metrics.SIDECAR_JOBS.inc("queued")
        return True

    def _start_workers(self) -> None:
        """Start the worker threads on first use (call with the condition held)."""
        if self._threads:
            return
        for i in range(self.workers):
            t = threading.Thread(target=self._work, name=f"{self.name}-{i + 1}", daemon=True)
            self._threads.append(t)
            t.start()

    def _work(self) -> None:
        while True:
            with self._cv:
                while not self._jobs and not self._stopped:
                    self._cv.wait()
                if not self._jobs:
                    return
                job = self._jobs.popleft()
                self._running += 1
            outcome = "failed"
            try:
                job.context.run(job.fn)
                outcome = "done"
            except Exception as e:  # noqa: BLE001 - a job's failure is counted and logged, never raised in a worker
                obs.log(logging.WARNING, "sidecar_job_failed", error=type(e).__name__)
            finally:
                with self._cv:
                    self._running -= 1
                    self._bytes -= job.size
                    self._counts[outcome] += 1
                    self._cv.notify_all()
                metrics.SIDECAR_JOBS.inc(outcome)
                metrics.SIDECAR_SECONDS.observe(max(0.0, self.clock() - job.queued_at))

    def _idle(self) -> bool:
        return self._reserved == 0 and not self._jobs and self._running == 0

    def drain(self, timeout_s: float) -> bool:
        """Wait at most `timeout_s` seconds (real time) until no job is reserved, queued or running; True when that
        happened."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._cv:
            while not self._idle():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
            return True

    def close(self, timeout_s: float) -> Counter[Hashable]:
        """Take no new places, wait at most `timeout_s` seconds for the queue to empty, then give up on the jobs still
        queued: they never run, and they come back counted by tag. A place reserved but not submitted by then is refused
        when its job arrives (`Slot.submit` returns False and the caller runs it). A job already running finishes in its
        daemon thread. Calling it again gives up on nothing more."""
        with self._cv:
            self._closed = True
        self.drain(timeout_s)
        with self._cv:
            self._stopped = True
            left = list(self._jobs)
            self._jobs.clear()
            self._bytes -= sum(job.size for job in left)
            self._counts["abandoned"] += len(left)
            self._cv.notify_all()
        if left:
            metrics.SIDECAR_JOBS.inc("abandoned", by=len(left))
        return Counter(job.tag for job in left)

    def snapshot(self) -> dict[str, Any]:
        """Its settings and counts: places waiting (`queued`, reserved ones included), `running`, the bytes their jobs
        hold (`bytes`, of `max_bytes`), and since start `submitted`, `done`, `failed` (the job raised), `full` (a
        reservation refused because the queue or its byte budget was full), `refused` (a job handed over after closing
        gave up; its caller ran it) and `abandoned` (never run: still queued when it closed)."""
        with self._cv:
            return {
                "workers": self.workers,
                "max_queue": self.max_queue,
                "audit_places": self.audit_places,
                "max_bytes": self.max_bytes,
                "queued": self._reserved + len(self._jobs),
                "running": self._running,
                "bytes": self._bytes,
                "closed": self._closed,
                "submitted": self._counts["queued"],
                "done": self._counts["done"],
                "failed": self._counts["failed"],
                "full": self._counts["full"],
                "refused": self._counts["refused"],
                "abandoned": self._counts["abandoned"],
            }
