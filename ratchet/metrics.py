"""Prometheus metrics in the text exposition format 0.0.4, standard library only (ADR 0062).

Counters and histograms live in one process-wide `REGISTRY`, so they only ever grow while the process runs (a demo
reset keeps them; Prometheus' `rate()` reads a restart as a reset). Each metric takes a lock per update: a few dict
operations, so the request path pays about a microsecond per increment. The scrape-time gauges (breakers, version
traffic, today's spend) are set by `GET /metrics` from the runtime just before it renders (`Gauge.replace`).

Label values stay a bounded set: a task label is a registered task's name or `other` (`router.task_label`), a client
label a `RATCHET_API_KEYS` name, `none` (no key) or `other` (`client_label`), and every other label comes from a fixed
vocabulary. Nothing in a label or a sample is ever request text, an answer, a URL or a key (invariants 12 and 13).
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

# Seconds. A request spans a student call (tens of ms to seconds) and possibly a teacher call (up to the 180 s deadline).
REQUEST_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
UPSTREAM_BUCKETS = REQUEST_BUCKETS

LabelKey = tuple[str, ...]
Relabel = Mapping[str, Callable[[str], str]]


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def number(value: float) -> str:
    """A sample value as the text format writes it: integers without a fraction, `+Inf`, `-Inf` and `NaN`."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


class _Metric:
    kind = ""

    def __init__(self, name: str, help: str, labels: Sequence[str] = ()) -> None:  # noqa: A002 - Prometheus' word
        if not _NAME.fullmatch(name):
            raise ValueError(f"bad metric name {name!r}")
        for label in labels:
            if not _LABEL.fullmatch(label) or label.startswith("__") or label == "le":
                raise ValueError(f"bad label name {label!r} for {name}")
        self.name = name
        self.help = help
        self.labels = tuple(labels)
        self._lock = threading.Lock()

    def _key(self, values: Sequence[object]) -> LabelKey:
        if len(values) != len(self.labels):
            raise ValueError(f"{self.name} takes {len(self.labels)} label values {self.labels}, not {len(values)}")
        return tuple("" if v is None else str(v) for v in values)

    def samples(self) -> list[tuple[str, LabelKey, tuple[str, ...], float]]:
        """(sample name, label names, label values, value) for every series, in a stable order."""
        raise NotImplementedError


class Counter(_Metric):
    """A value that only goes up."""

    kind = "counter"

    def __init__(self, name: str, help: str, labels: Sequence[str] = ()) -> None:  # noqa: A002
        super().__init__(name, help, labels)
        self._values: dict[LabelKey, float] = {}

    def inc(self, *values: object, by: float = 1.0) -> None:
        """Add `by` (0 or more) to the series these label values name."""
        if not by >= 0:  # also refuses NaN
            raise ValueError(f"{self.name}: a counter only goes up, not by {by!r}")
        key = self._key(values)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + by

    def value(self, *values: object) -> float:
        with self._lock:
            return self._values.get(self._key(values), 0.0)

    def samples(self) -> list[tuple[str, LabelKey, tuple[str, ...], float]]:
        with self._lock:
            items = sorted(self._values.items())
        return [(self.name, self.labels, k, v) for k, v in items]


class Gauge(_Metric):
    """A value that goes up and down. The scrape-time gauges replace every series at once (`replace`), so a breaker or a
    version that is gone does not linger with its last value."""

    kind = "gauge"

    def __init__(self, name: str, help: str, labels: Sequence[str] = ()) -> None:  # noqa: A002
        super().__init__(name, help, labels)
        self._values: dict[LabelKey, float] = {}

    def set(self, value: float, *values: object) -> None:
        key = self._key(values)
        with self._lock:
            self._values[key] = float(value)

    def replace(self, series: Iterable[tuple[Sequence[object], float]]) -> None:
        """Every series of this gauge at once: (label values, value) pairs; any series not listed is dropped."""
        fresh = {self._key(values): float(v) for values, v in series}
        with self._lock:
            self._values = fresh

    def value(self, *values: object) -> float | None:
        with self._lock:
            return self._values.get(self._key(values))

    def samples(self) -> list[tuple[str, LabelKey, tuple[str, ...], float]]:
        with self._lock:
            items = sorted(self._values.items())
        return [(self.name, self.labels, k, v) for k, v in items]


class Histogram(_Metric):
    """Observations counted into fixed buckets (upper bounds, ascending), with their sum and count. Rendered as
    cumulative `_bucket{le=...}` series ending in `le="+Inf"`, then `_sum` and `_count`."""

    kind = "histogram"

    def __init__(self, name: str, help: str, labels: Sequence[str] = (), buckets: Sequence[float] = REQUEST_BUCKETS) -> None:  # noqa: A002
        super().__init__(name, help, labels)
        bounds = [float(b) for b in buckets]
        if not bounds or any(not math.isfinite(b) for b in bounds) or bounds != sorted(set(bounds)):
            raise ValueError(f"{name}: buckets must be finite, distinct and ascending, not {buckets!r}")
        self.buckets = tuple(bounds)
        self._series: dict[LabelKey, list[float]] = {}  # per-bucket counts (not cumulative), then +Inf, sum, count

    def observe(self, value: float, *values: object) -> None:
        if math.isnan(value):
            return  # nothing to count; a NaN would poison the sum
        key = self._key(values)
        n = len(self.buckets)
        slot = next((i for i, b in enumerate(self.buckets) if value <= b), n)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = self._series[key] = [0.0] * (n + 3)
            series[slot] += 1
            series[n + 1] += value
            series[n + 2] += 1

    def count(self, *values: object) -> float:
        with self._lock:
            series = self._series.get(self._key(values))
            return series[-1] if series else 0.0

    def samples(self) -> list[tuple[str, LabelKey, tuple[str, ...], float]]:
        with self._lock:
            items = sorted((k, list(v)) for k, v in self._series.items())
        out = []
        n = len(self.buckets)
        names = (*self.labels, "le")
        for key, series in items:
            running = 0.0
            for i, bound in enumerate((*self.buckets, math.inf)):
                running += series[i]
                out.append((f"{self.name}_bucket", names, (*key, "+Inf" if math.isinf(bound) else repr(bound)), running))
            out.append((f"{self.name}_sum", self.labels, key, series[n + 1]))
            out.append((f"{self.name}_count", self.labels, key, series[n + 2]))
        return out


class Registry:
    """Metrics by name, rendered in registration order."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def _add(self, metric: _Metric) -> _Metric:
        with self._lock:
            if metric.name in self._metrics:
                raise ValueError(f"metric {metric.name} is already registered")
            self._metrics[metric.name] = metric
        return metric

    def counter(self, name: str, help: str, labels: Sequence[str] = ()) -> Counter:  # noqa: A002
        return self._add(Counter(name, help, labels))  # type: ignore[return-value]

    def gauge(self, name: str, help: str, labels: Sequence[str] = ()) -> Gauge:  # noqa: A002
        return self._add(Gauge(name, help, labels))  # type: ignore[return-value]

    def histogram(
        self, name: str, help: str, labels: Sequence[str] = (), buckets: Sequence[float] = REQUEST_BUCKETS
    ) -> Histogram:  # noqa: A002
        return self._add(Histogram(name, help, labels, buckets))  # type: ignore[return-value]

    def render(self, relabel: Relabel | None = None) -> str:
        """Every metric in the text format 0.0.4: `# HELP` and `# TYPE` lines, then its samples. `relabel` maps a label
        name to a function of its value (client pseudonyms for a scrubbed read); series that end up with the same labels
        are added together."""
        with self._lock:
            metrics = list(self._metrics.values())
        lines: list[str] = []
        for m in metrics:
            lines.append(f"# HELP {m.name} {_escape_help(m.help)}")
            lines.append(f"# TYPE {m.name} {m.kind}")
            merged: dict[tuple[str, LabelKey, tuple[str, ...]], float] = {}
            for sample, names, values, value in m.samples():
                if relabel:
                    values = tuple(relabel[n](v) if n in relabel else v for n, v in zip(names, values, strict=True))
                k = (sample, names, values)
                merged[k] = merged.get(k, 0.0) + value
            for (sample, names, values), value in merged.items():
                labels = ",".join(f'{n}="{_escape_label(v)}"' for n, v in zip(names, values, strict=True))
                lines.append(f"{sample}{{{labels}}} {number(value)}" if labels else f"{sample} {number(value)}")
        return "\n".join(lines) + "\n"


# ---------- label values ----------
_clients: frozenset[str] = frozenset()


def set_clients(names: Iterable[str]) -> None:
    """The API key names a client label may carry (`RATCHET_API_KEYS`); the runtime sets them at startup."""
    global _clients
    _clients = frozenset(names)


def client_label(client: str | None) -> str:
    """`none` for a request without a key (an open gateway, Scout, the simulator), the key's name for a configured key,
    `other` for anything else, so a caller of `Ratchet.handle` cannot grow the label set."""
    if not client:
        return "none"
    return client if client in _clients else "other"


# ---------- Ratchet's metrics ----------
REGISTRY = Registry()
INFO = REGISTRY.gauge("ratchet_info", "Always 1; the labels name the running version and mode.", ("version", "mode"))
REQUESTS = REGISTRY.counter(
    "ratchet_requests_total",
    "Requests by task (a registered name, other or none), who served them, route and status. Answered requests are "
    "counted by the cascade with status 200; requests that ended in an error by the gateway, with served_by none.",
    ("task", "served_by", "route", "status"),
)
REQUEST_SECONDS = REGISTRY.histogram(
    "ratchet_request_seconds", "Wall-clock time per request, by who served it (none for an error).", ("served_by",)
)
UPSTREAM = REGISTRY.counter(
    "ratchet_upstream_requests_total",
    "Model calls by upstream (teacher, fallback_teacher, student, shadow, audit, arbitration) and outcome (ok, error, "
    "timeout, circuit_open: refused by the breaker without a call).",
    ("upstream", "outcome"),
)
UPSTREAM_SECONDS = REGISTRY.histogram(
    "ratchet_upstream_seconds", "Time per model call made, retries included, by upstream.", ("upstream",), UPSTREAM_BUCKETS
)
COST = REGISTRY.counter(
    "ratchet_cost_usd_total",
    "US dollars spent on model calls, by client, including what failed requests spent (per-token prices; simulated in "
    "mock mode).",
    ("client",),
)
FAILED_COST = REGISTRY.counter(
    "ratchet_failed_cost_usd_total",
    "US dollars spent by requests that failed without an answer (the student's samples before a teacher failure), by "
    "client; also counted in ratchet_cost_usd_total.",
    ("client",),
)
FAILURES = REGISTRY.counter(
    "ratchet_failures_total",
    "Failures by stage (the failures table's stages), every one: the table samples breaker refusals and throttles the "
    "gateway's rows, this counter does not.",
    ("stage",),
)
BASELINE = REGISTRY.counter(
    "ratchet_baseline_usd_total", "What the answered requests would have cost with the teacher alone, by client.", ("client",)
)
ESCALATIONS = REGISTRY.counter("ratchet_escalations_total", "Requests the student tried and the teacher answered.", ("task",))
AUDITS = REGISTRY.counter(
    "ratchet_audits_total",
    "Teacher audits of served student answers by result: agree, disagree, invalid (the teacher's answer measured "
    "nothing), error, circuit_open.",
    ("result",),
)
EXAMPLES = REGISTRY.counter(
    "ratchet_examples_stored_total", "New training examples stored, by task and split (train, golden).", ("task", "split")
)
REJECTIONS = REGISTRY.counter(
    "ratchet_rejections_total",
    "Chat requests refused before the cascade, by reason (auth, invalid_request, unknown_task, task_not_allowed, quota, "
    "budget, rate, overloaded, too_large, disconnected, forbidden, other).",
    ("reason",),
)
CIRCUIT_OPEN = REGISTRY.gauge("ratchet_circuit_open", "1 while a breaker is open or half-open, else 0.", ("breaker",))
VERSION_TRAFFIC = REGISTRY.gauge(
    "ratchet_version_traffic_pct", "Traffic share of the live, canary and shadow versions (shadow: 0).", ("version", "state")
)
SPEND_TODAY = REGISTRY.gauge(
    "ratchet_spend_today_usd", "US dollars spent since 00:00 UTC by client, failed requests included.", ("client",)
)
INFLIGHT = REGISTRY.gauge(
    "ratchet_inflight_requests", "Chat requests in flight now; RATCHET_MAX_INFLIGHT refuses past it with a 503."
)
