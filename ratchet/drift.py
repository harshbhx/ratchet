"""Label-free drift alarm (FR-16.6, ADR 0032): a conformal test martingale on each version's own confidence stream.

Every precision claim Ratchet makes (the golden gate, a certificate, the canary's audits) assumes today's requests look
like the ones it was measured on. Audits notice a change only through the teacher, and the golden set only after a
night. This monitor needs no labels: it watches the confidence scores the student already logs.

The null hypothesis is that, for a fixed version, requests are exchangeable (their order carries no information). A
version's confidence is then a fixed, randomised function of the request, so its confidence stream is exchangeable
too. Each new score gets a smoothed conformal p-value, its rank among the version's earlier scores (low confidence
gives a small p-value; ties are broken by an independent uniform draw). Under the null these p-values are independent
and uniform on [0, 1] (Vovk, Gammerman and Shafer 2005; Vovk et al. 2021). A gambler bets on them with the power
betting functions f_e(p) = e * p**(e - 1), each of which integrates to 1 on [0, 1], mixed by the Simple Jumper
(Vovk, Petej, Nouretdinov, Manokhin and Gammerman 2021, Algorithm 1): before each bet a share J of the capital is
spread evenly over the bets, so the gambler can switch to the bet that pays after a change. The capital is then a test
martingale (it starts at 1, stays non-negative, and its expected next value is its current value), so by Ville's
inequality the probability that it ever reaches `threshold` is at most 1/threshold, however long the stream runs and
however often anyone looks. Clamping a p-value up to MIN_P only lowers a decreasing bet, so the capital stays a
supermartingale.

J is 0.01 for a stream's first 100 observations and 1/n after that. A constant J moves J*(1 - 1/K) of the capital per
observation into bets that lose while nothing changes, so the capital shrinks exponentially with the version's age: a
version live for 5,000 requests would start a change some 17 orders of magnitude down and rarely recover in time. With
J = 1/n it shrinks only like a power of n (switching with a decreasing rate, as in fixed-share forecasting), and a
change late in a long life is caught about as fast as an early one. J depends on n alone, never on the data, so the
validity argument is unchanged.

The bound is per stream: with K streams (one per version and task), the chance that any of them ever alarms under
the null is at most K/threshold, and the monitor reports that number. The test is one-sided: every bet with e < 1
pays on small p-values, so confidence that rises makes the capital fall. An alarm says traffic changed for this
version; it does not say the model got worse (that is what audits measure).

The request path is untouched: the monitor reads the ledger in `ReleaseGate.tick`, draws from its own random
generator per stream (never `Ratchet.rng`), and is rebuilt by replaying the ledger, which gives the same capital to
the last digit. Standard library only.
"""

from __future__ import annotations

import math
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

BINS = 10_000  # a confidence c falls in bin round(c * BINS): 0 for 0, BINS for 1
BETS = (0.05, 0.15, 0.3, 0.6, 1.0)  # the power betting functions' exponents; e = 1 is "no bet"
JUMP = 0.01  # the Simple Jumper's switching rate J for the first 1/JUMP observations; 1/n after that
MIN_P = 1e-12  # a p-value is clamped up to this, so every bet stays finite
MAX_POINTS = 200  # a stream's trajectory keeps at most this many (n, log10 capital) points
BATCH = 200_000  # ledger rows read per query
COMPACT_EVERY = 1_000  # rows between two compactions (each reads the versions' states)
DEFAULT_THRESHOLD = 100.0  # the alarm level of the capital: a false alarm has probability at most 1/100 per stream
DEFAULT_SEED = 7
# Versions that may still log confidence. A stream of any other version keeps only its final numbers, except the most
# recently retired version's (the one an operator would restore by hand), which stays ready.
ACTIVE_STATES = ("canary", "live")
# The highest audit rate a drift boost reaches (FR-6.40); a rate already above it is never lowered.
MAX_BOOSTED_AUDIT_RATE = 0.5
LOG_CAP = 690.0  # exp() of anything larger overflows a float; the capital is reported capped like stats.e_bernoulli
_LN10 = math.log(10)
_K = len(BETS)


class Fenwick:
    """Counts per bin with O(log n) inserts and prefix sums (a binary indexed tree): the rank of a new confidence among
    a stream's earlier ones without storing or sorting them. Bins are numbered from 0."""

    __slots__ = ("size", "tree")

    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError(f"a Fenwick tree needs at least one bin, not {size}")
        self.size = size
        self.tree = [0] * (size + 1)

    def add(self, i: int, k: int = 1) -> None:
        """Add `k` to bin `i`."""
        if not 0 <= i < self.size:
            raise IndexError(f"bin {i} is outside 0..{self.size - 1}")
        i += 1
        tree, size = self.tree, self.size
        while i <= size:
            tree[i] += k
            i += i & -i

    def prefix(self, i: int) -> int:
        """The count in bins 0 to `i`, inclusive: 0 for `i` < 0, everything for `i` >= the last bin."""
        i = min(i, self.size - 1) + 1
        tree, total = self.tree, 0
        while i > 0:
            total += tree[i]
            i -= i & -i
        return total


def bin_of(confidence: float) -> int:
    """The bin of a confidence: min(BINS, max(0, round(confidence * BINS))). Quantising is a fixed function of the
    score, so it keeps exchangeability; the smoothed p-value breaks the ties it creates."""
    return min(BINS, max(0, round(confidence * BINS)))


def usable(confidence: object) -> bool:
    """Whether a logged confidence can be observed: a finite number (the router logs one in [0, 1], or NULL)."""
    return isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence)


def boosted_rate(rate: float, boost: float) -> float:
    """The audit rate of a version under a drift boost (FR-6.40): `rate` times `boost`, at most
    MAX_BOOSTED_AUDIT_RATE, and never below `rate` itself (a canary may already be audited at a higher rate)."""
    return max(rate, min(MAX_BOOSTED_AUDIT_RATE, rate * boost))


def capital(log_c: float) -> float:
    """The capital exp(log_c), capped so it never overflows."""
    return math.exp(min(log_c, LOG_CAP))


class Stream:
    """One version's confidence stream for one task, and the capital of the Simple Jumper betting against it.

    The random generator is the stream's own, seeded by (seed, version, task), so the p-values depend only on the
    stream's rows in id order: a replay of the ledger reproduces the capital exactly."""

    __slots__ = (
        "version",
        "task",
        "log_threshold",
        "tree",
        "counts",
        "n",
        "caps",
        "log_c",
        "peak",
        "alarm_at",
        "alarm_day",
        "alarm_n",
        "last_id",
        "p",
        "trajectory",
        "stride",
        "rng",
    )

    def __init__(self, version: int, task: str, seed: int = DEFAULT_SEED, threshold: float = DEFAULT_THRESHOLD) -> None:
        if not threshold > 1:
            raise ValueError(f"the alarm threshold must be more than 1, not {threshold!r}")
        self.version, self.task = version, task
        self.log_threshold = math.log(threshold)
        self.tree: Fenwick | None = Fenwick(BINS + 1)
        self.counts: list[int] | None = [0] * (BINS + 1)
        self.n = 0
        self.caps = [1.0 / _K] * _K  # each bet's share of the capital (they sum to 1)
        self.log_c = 0.0  # natural log of the capital, which starts at 1
        self.peak = 0.0
        self.alarm_at: int | None = None  # the request id at which the capital first reached the threshold
        self.alarm_day: int | None = None
        self.alarm_n: int | None = None
        self.last_id = 0
        self.p: float | None = None  # the last p-value
        self.trajectory: list[tuple[int, float]] = []  # (n, log10 capital) at every `stride`-th observation
        self.stride = 1
        self.rng = random.Random(f"{seed}:{version}:{task}")

    @property
    def active(self) -> bool:
        """Whether the stream can take more observations (its counts are kept; see `compact`)."""
        return self.tree is not None

    @property
    def alarmed(self) -> bool:
        return self.alarm_at is not None

    def observe(self, confidence: float, request_id: int, day: int | None = None) -> bool:
        """Add one confidence (logged in request `request_id` on ledger day `day`) and bet on its p-value. Returns True
        only on the observation at which the capital first reaches the threshold: the alarm latches."""
        tree, counts = self.tree, self.counts
        if tree is None or counts is None:
            raise RuntimeError(f"stream v{self.version} ({self.task}) was compacted; replay it before observing")
        if not usable(confidence):
            raise ValueError(f"a confidence must be a finite number, not {confidence!r}")
        q = bin_of(confidence)
        self.n += 1
        tree.add(q)
        counts[q] += 1
        below, ties = tree.prefix(q - 1), counts[q]  # ties include this observation
        # The smoothed conformal p-value: small when this confidence is unusually low for the version.
        p = max(MIN_P, (below + self.rng.random() * ties) / self.n)
        self.p = p
        caps, total = self.caps, 0.0
        jump = min(JUMP, 1.0 / self.n)  # depends on n alone, so the mix stays a test martingale (ADR 0032)
        for k, e in enumerate(BETS):
            c = ((1 - jump) * caps[k] + jump / _K) * (e * p ** (e - 1))
            caps[k] = c
            total += c
        self.log_c += math.log(total)
        for k in range(_K):
            caps[k] /= total
        if self.log_c > self.peak:
            self.peak = self.log_c
        self.last_id = request_id
        if self.n % self.stride == 0:
            self.trajectory.append((self.n, round(self.log_c / _LN10, 4)))
            if len(self.trajectory) >= MAX_POINTS:  # keep every other point and halve the sampling rate
                self.trajectory = self.trajectory[1::2]
                self.stride *= 2
        if self.alarm_at is None and self.log_c >= self.log_threshold:
            self.alarm_at, self.alarm_day, self.alarm_n = request_id, day, self.n
            return True
        return False

    def compact(self) -> None:
        """Drop the counts (most of the stream's memory) and keep the final numbers. A version that no longer serves
        logs no confidence; should it serve again, `DriftMonitor` replays its rows before observing more."""
        self.tree = self.counts = None

    def summary(self, trajectory: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "task": self.task,
            "n": self.n,
            "log10_martingale": round(self.log_c / _LN10, 4),
            "peak_log10": round(self.peak / _LN10, 4),
            "alarm": self.alarmed,
            "alarm_at_request": self.alarm_at,
            "alarm_day": self.alarm_day,
            "alarm_n": self.alarm_n,
            "last_request": self.last_id,
            "active": self.active,
        }
        if trajectory:
            out["trajectory"] = [[n, v] for n, v in self.trajectory]
        return out


@dataclass(frozen=True)
class Alarm:
    """A stream whose capital first reached the threshold: version, task, observations, capital (log), the request and
    ledger day it happened on, and how many streams the monitor held then (the family bound's K)."""

    version: int
    task: str
    n: int
    log_c: float
    request_id: int
    day: int | None
    streams: int
    threshold: float

    @property
    def key(self) -> str:
        """The ledger `meta` key that records this alarm's event once, across processes and restarts."""
        return f"drift:{self.version}:{self.task}"

    def detail(self) -> str:
        """The `drift` event's text: what changed, the evidence, and what it does not mean."""
        thr, k = self.threshold, self.streams
        return (
            f"v{self.version} ({self.task}): confidence on recent traffic no longer matches earlier traffic for this "
            f"version (test martingale {capital(self.log_c):.3g} >= {thr:g} after {self.n:,} requests; false-alarm "
            f"probability <= {_pct(1 / thr)} for this stream, <= {_pct(min(1.0, k / thr))} across {k} "
            f"stream{'' if k == 1 else 's'}). Traffic changed; this alone does not mean the model got worse. Its "
            "certificate, if it has one, is suspended."
        )


def _pct(x: float) -> str:
    return f"{x * 100:.3g}%"


class ConfidenceLedger(Protocol):
    """What the monitor reads from `ratchet.ledger.Ledger`."""

    def confidence_stream(
        self,
        after_id: int = 0,
        limit: int = BATCH,
        *,
        version: int | None = None,
        task: str | None = None,
        before_id: int | None = None,
    ) -> list[tuple[int, int, str, float, int]]: ...

    def version_states(self) -> dict[int, str]: ...  # in the order the versions entered their current state


class DriftMonitor:
    """Every (version, task) stream of logged student confidence, fed from the ledger in request-id order.

    `update(ledger)` reads the rows logged since the last call (one primary-key range query when it is current) and
    returns the alarms they raised; `on_alarm` is called for each, after the monitor's lock is released. A fresh monitor
    replays the whole ledger on its first update, which reproduces every capital exactly. Updates and status reads are
    safe from several threads (the gateway's ticks and a mock night run concurrently)."""

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        seed: int = DEFAULT_SEED,
        on_alarm: Callable[[Alarm], object] | None = None,
        batch: int = BATCH,
    ) -> None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not threshold > 1:
            raise ValueError(f"the drift alarm threshold must be more than 1, not {threshold!r}")
        if batch < 1:
            raise ValueError(f"batch must be at least 1, not {batch}")
        self.threshold = float(threshold)
        self.seed = seed
        self.on_alarm = on_alarm
        self.batch = batch
        self.streams: dict[tuple[int, str], Stream] = {}
        self.last_id = 0  # the last request id read from the ledger
        self.skipped = 0  # rows whose confidence was not a finite number (never written by the router)
        self._since_compact = 0  # rows read since the last compaction
        self._lock = threading.RLock()

    def update(self, ledger: ConfidenceLedger) -> list[Alarm]:
        """Feed every row logged since the last update to its stream; return the alarms raised, in row order."""
        with self._lock:
            alarms = self._update(ledger)
        self._notify(alarms)
        return alarms

    def rebuild(self, ledger: ConfidenceLedger) -> list[Alarm]:
        """Forget everything and replay the ledger from its first row (what a fresh monitor does on its first update).
        Returns every alarm the ledger holds, in row order."""
        with self._lock:
            self.streams.clear()
            self.last_id = 0
            self.skipped = 0
            self._since_compact = 0
            alarms = self._update(ledger)
        self._notify(alarms)
        return alarms

    def _update(self, ledger: ConfidenceLedger) -> list[Alarm]:
        replay = self.last_id == 0
        alarms: list[Alarm] = []
        while True:
            rows = ledger.confidence_stream(after_id=self.last_id, limit=self.batch)
            for rid, vid, task, conf, day in rows:
                alarm = self._feed(ledger, rid, vid, task, conf, day)
                if alarm is not None:
                    alarms.append(alarm)
                self.last_id = rid
            self._since_compact += len(rows)
            if len(rows) < self.batch:
                break
        # Compacting reads every version's state, a second query; after a replay and then once per COMPACT_EVERY rows
        # keeps an up-to-date monitor at one query per update. A version that stopped serving logs nothing, so its
        # counts waiting a little longer cost only memory.
        if self._since_compact and (replay or self._since_compact >= COMPACT_EVERY):
            self._compact(ledger)
            self._since_compact = 0
        return alarms

    def _notify(self, alarms: list[Alarm]) -> None:
        """Call `on_alarm` for each new alarm, outside the lock, so a callback may read the monitor."""
        if self.on_alarm is not None:
            for alarm in alarms:
                self.on_alarm(alarm)

    def _feed(self, ledger: ConfidenceLedger, rid: int, vid: int, task: str, conf: Any, day: int | None) -> Alarm | None:
        if not usable(conf):
            self.skipped += 1
            return None
        key = (int(vid), str(task))
        s = self.streams.get(key)
        if s is None:
            s = self.streams[key] = Stream(key[0], key[1], self.seed, self.threshold)
        elif not s.active:
            s = self.streams[key] = self._replay(ledger, s, rid)
        if not s.observe(float(conf), rid, day):
            return None
        return Alarm(s.version, s.task, s.n, s.log_c, rid, day, len(self.streams), self.threshold)

    def _replay(self, ledger: ConfidenceLedger, old: Stream, before_id: int) -> Stream:
        """A compacted stream's state rebuilt from its rows before `before_id`: the same rows through a fresh generator
        with the same seed give the same capital, so it can take new rows again."""
        fresh = Stream(old.version, old.task, self.seed, self.threshold)
        after = 0
        while True:
            rows = ledger.confidence_stream(
                after_id=after, limit=self.batch, version=old.version, task=old.task, before_id=before_id
            )
            for rid, _vid, _task, conf, day in rows:
                if usable(conf):
                    fresh.observe(float(conf), rid, day)
                after = rid
            if len(rows) < self.batch:
                break
        return fresh

    def _compact(self, ledger: ConfidenceLedger) -> None:
        """Keep counts only for versions that may log confidence (ACTIVE_STATES) and the most recently retired one."""
        if not any(s.active for s in self.streams.values()):
            return
        states = ledger.version_states()  # in the order they entered their state
        keep = {vid for vid, state in states.items() if state in ACTIVE_STATES}
        retired = [vid for vid, state in states.items() if state == "retired"]
        if retired:
            keep.add(retired[-1])
        for s in self.streams.values():
            if s.active and s.version not in keep:
                s.compact()

    # ----- reads (safe from any thread) -----
    def family_bound(self) -> float:
        """The chance that any stream monitored so far ever alarms under the null: min(1, K / threshold)."""
        with self._lock:
            return min(1.0, len(self.streams) / self.threshold)

    def status(self, version: int | None = None, trajectory: bool = True) -> dict[str, Any]:
        """The monitor's numbers: the threshold, the per-stream and family false-alarm bounds, how many streams it holds
        and how many alarmed, and each stream (or `version`'s) by version and task."""
        with self._lock:
            streams = sorted(self.streams.values(), key=lambda s: (s.version, s.task))
            k = len(streams)
            rows = [s.summary(trajectory) for s in streams if version is None or s.version == version]
            alarms = sum(s.alarmed for s in streams)
        return {
            "threshold": self.threshold,
            "false_alarm_bound": 1 / self.threshold,
            "family_bound": min(1.0, k / self.threshold),
            "monitored": k,
            "alarms": alarms,
            "streams": rows,
        }

    def open_alarm(self, version: int) -> dict[str, Any] | None:
        """The first stream of `version` that alarmed (by request id), without its trajectory, or None. An alarm stays
        open for the version's life: the version's own traffic stopped looking like its earlier traffic, so traffic is
        not exchangeable with any fixed set of rows it was measured on."""
        with self._lock:
            hit = [s for s in self.streams.values() if s.version == version and s.alarm_at is not None]
            if not hit:
                return None
            return min(hit, key=lambda s: s.alarm_at or 0).summary(trajectory=False)

    def alarmed_versions(self) -> set[int]:
        with self._lock:
            return {s.version for s in self.streams.values() if s.alarmed}

    def version_view(self, version: int) -> dict[str, Any] | None:
        """What the certificate endpoint reports for `version`: whether an alarm is open, the threshold, and each of its
        streams without trajectories; None when it has no stream (it never logged a confidence)."""
        with self._lock:
            streams = sorted((s for s in self.streams.values() if s.version == version), key=lambda s: s.task)
            if not streams:
                return None
            return {
                "alarm": any(s.alarmed for s in streams),
                "threshold": self.threshold,
                "streams": [s.summary(trajectory=False) for s in streams],
            }
