"""Success metrics (PRD §8.1, FR-7.9): computed from the ledger, the same way in mock and live mode.

Each metric is a pure function of plain rows (ledger days, audit counts, events), so tests can feed it synthetic days,
and `report` reads those rows from a `Ledger` and puts the metrics together for `GET /ratchet/kpis`, the `kpis` block of
`/ratchet/stats` and the simulator. Nothing here writes.

SM-1 to SM-3 and NFR-1 are measured over a window: the last W ledger days with traffic (RATCHET_KPI_WINDOW_DAYS, 3 by
default), so they describe the steady state the PRD targets, not the cold start. SM-4 and SM-5 read the whole history.
Every metric returns {name, value, target, op, unit, met, detail}; `met` is None when there is not enough data to say
(no traffic in the window, too few audits, too few days to see drift, or a drift episode still open within its target).
The targets live in SM_TARGETS, and the traceability check (scripts/reqtrace.py) compares them with the PRD's table.
See ADR 0083.
"""

from __future__ import annotations

import math
import operator
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .stats import wilson

DEFAULT_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 3650
DEFAULT_MIN_AUDITS = 20  # ReleasePolicy.min_audits' default: the audits a canary decision needs
# SM-5's episode rule: a day whose escalation rate is at least DRIFT_START above the lowest of the DRIFT_BASELINE_DAYS
# days before it starts an episode, and the first later day at most DRIFT_END above that baseline ends it. A day counts
# only when the student judged at least DRIFT_MIN_ANSWERED requests, so a quiet day's noise is not read as drift.
DRIFT_BASELINE_DAYS = 3
DRIFT_START = 0.05
DRIFT_END = 0.02
DRIFT_MIN_ANSWERED = 50
NFR1_QUANTILE = 0.5
_EPS = 1e-9  # rates at a boundary count as reaching it ("at least", "at most"), whatever the float rounding
# The release events SM-4 reads. An alert is only ever raised on the live version; a rollback names the version it
# pulled; a `live` event names the version that reached 100% (the gate's promotion, or a manual restore).
RELEASE_KINDS = ("live", "alert", "rollback")
_VERSION = re.compile(r"\bv(\d+)\b")

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "<": operator.lt,
    ">": operator.gt,
}


@dataclass(frozen=True)
class Target:
    """A metric's target: `value <op> target`, in `unit` (`fraction` for a share, else a count or milliseconds)."""

    name: str
    op: str
    value: float
    unit: str

    def reached(self, value: float) -> bool:
        if self.unit == "fraction":  # a share computed from sums: 0.7 must count as reaching ">= 0.70"
            return _OPS[self.op](round(value, 9), self.value)
        return _OPS[self.op](value, self.value)


# The PRD §8.1 table, as numbers. scripts/reqtrace.py checks each against the PRD's Target column, so the two cannot
# drift apart.
SM_TARGETS: dict[str, Target] = {
    "SM-1": Target("Cost reduction vs all-teacher, steady state", ">=", 0.70, "fraction"),
    "SM-2": Target("Audited precision of student-served answers", ">=", 0.95, "fraction"),
    "SM-3": Target("Student share of traffic, steady state", ">=", 0.80, "fraction"),
    "SM-4": Target("Bad deploys reaching 100% traffic", "==", 0, "versions"),
    "SM-5": Target("Time to recover from drift (escalations back to baseline)", "<=", 3, "nights"),
}
# NFR-1's latency target: the median student-served request under one second.
NFR1_TARGET = Target("Median latency of student-served requests", "<", 1000.0, "ms")


def _metric(target: Target, value: float | None, met: bool | None, **detail: Any) -> dict[str, Any]:
    return {
        "name": target.name,
        "value": value,
        "target": target.value,
        "op": target.op,
        "unit": target.unit,
        "met": met,
        "detail": detail,
    }


def _judge(target: Target, value: float | None) -> bool | None:
    return None if value is None else target.reached(value)


def check_window_days(days: object) -> int:
    """RATCHET_KPI_WINDOW_DAYS (or a request's `days`): a whole number of days from 1 to MAX_WINDOW_DAYS."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_WINDOW_DAYS:
        raise ValueError(f"the KPI window must be a whole number of days from 1 to {MAX_WINDOW_DAYS}, not {days!r}")
    return days


def window(days: Sequence[Mapping[str, Any]], size: int = DEFAULT_WINDOW_DAYS) -> list[Mapping[str, Any]]:
    """The last `size` days with traffic (`requests` > 0), oldest first. Days without requests never take a place in it,
    so a quiet weekend does not push the working days out. Fewer when the ledger has fewer days."""
    check_window_days(size)
    busy = sorted((d for d in days if (d.get("requests") or 0) > 0), key=lambda d: d["day"])
    return busy[-size:]


def _sum(days: Iterable[Mapping[str, Any]], key: str) -> float:
    return sum(d.get(key) or 0 for d in days)


def cost_reduction(days: Sequence[Mapping[str, Any]], failed_cost: float = 0.0) -> dict[str, Any]:
    """SM-1: 1 - (cost + failed cost) / all-teacher baseline over the window's days. `failed_cost` is what failed requests
    spent without leaving a request row (`Ledger.failed_cost`): it was spent, so it counts. Per-token prices (NFR-11:
    the GPU-hour view is the cost report's)."""
    t = SM_TARGETS["SM-1"]
    cost, base = _sum(days, "cost"), _sum(days, "baseline")
    detail: dict[str, Any] = {
        "cost_usd": cost,
        "failed_cost_usd": failed_cost,
        "baseline_usd": base,
        "requests": int(_sum(days, "requests")),
        "cost_model": "per_token",
    }
    if base <= 0:
        return _metric(t, None, None, **detail, reason="no traffic in the window" if not days else "no baseline cost")
    value = 1 - (cost + failed_cost) / base
    return _metric(t, value, _judge(t, value), **detail)


def audited_precision(n: int, agree: int, min_audits: int = DEFAULT_MIN_AUDITS) -> dict[str, Any]:
    """SM-2: the share of audited student answers the teacher agreed with, pooled over the window, with a 95% Wilson
    interval. Met when the point estimate reaches the target on at least `min_audits` audits; with fewer, `met` is None."""
    t = SM_TARGETS["SM-2"]
    if isinstance(n, bool) or not isinstance(n, int) or isinstance(agree, bool) or not isinstance(agree, int):
        raise ValueError(f"audits and agreements must be whole numbers, not {n!r} and {agree!r}")
    if not 0 <= agree <= n:
        raise ValueError(f"agreements must be from 0 to the {n} audits, not {agree}")
    lo, hi = wilson(agree, n)
    detail: dict[str, Any] = {"audits": n, "agree": agree, "lower": lo, "upper": hi, "min_audits": min_audits}
    if n == 0:
        return _metric(t, None, None, **detail, reason="no audits in the window")
    value = agree / n
    if n < min_audits:
        return _metric(t, value, None, **detail, reason=f"{n} audits, fewer than {min_audits}")
    return _metric(t, value, t.reached(value), **detail)


def student_share(days: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """SM-3: the share of the window's requests the student answered."""
    t = SM_TARGETS["SM-3"]
    n, served = int(_sum(days, "requests")), int(_sum(days, "student_served"))
    if n == 0:
        return _metric(t, None, None, requests=0, student_served=0, reason="no traffic in the window")
    value = served / n
    return _metric(t, value, t.reached(value), requests=n, student_served=served)


def _named_version(detail: str) -> int | None:
    """Release events name their subject version first ('live v2 audited precision ...', 'v3 audited precision ...;
    traffic back to live v1')."""
    m = _VERSION.search(detail or "")
    return int(m.group(1)) if m else None


def bad_deploys(events: Sequence[Mapping[str, Any]], states: Mapping[int, str]) -> dict[str, Any]:
    """SM-4: versions that reached 100% of traffic and were later found bad, over the whole history.

    A version reached 100% when a `live` event names it or it is live now. It was bad when an `alert` names it (alerts
    are raised only on the live version), when a `rollback` names it after it reached 100%, or when it is `rolled_back`
    now after reaching 100% (a manual rollback whose event names no version). `events` are the release events oldest
    first (`Ledger.events_of(RELEASE_KINDS)`); `states` maps each version id to its state now. `met` is None until some
    version has reached 100%: before that the release gate has not been tested at full traffic."""
    t = SM_TARGETS["SM-4"]
    reached: dict[int, int | None] = {}  # version -> day it first reached 100% (None when only its state says so)
    bad: dict[int, dict[str, Any]] = {}
    for e in events:
        v = _named_version(e.get("detail", ""))
        if v is None:
            continue
        kind = e.get("kind")
        if kind == "live":
            reached.setdefault(v, e.get("day"))
        elif kind == "alert" or (kind == "rollback" and v in reached):
            reached.setdefault(v, e.get("day"))  # an alert proves it was live, even if its promotion was not logged
            bad.setdefault(v, {"version": v, "evidence": kind, "day": e.get("day")})
    for v, state in states.items():
        if state == "live":
            reached.setdefault(v, None)
        elif state == "rolled_back" and v in reached:
            bad.setdefault(v, {"version": v, "evidence": "rolled_back", "day": None})
    value = len(bad)
    detail: dict[str, Any] = {"reached_full_traffic": len(reached), "bad_versions": [bad[v] for v in sorted(bad)]}
    if not reached:
        return _metric(t, value, None, **detail, reason="no version has reached 100% of traffic yet")
    return _metric(t, value, t.reached(value), **detail)


@dataclass
class Episode:
    """A drift episode: the day it started, its baseline (the lowest escalation rate of the days before), the highest
    rate it reached, and the day it ended (None while it is open)."""

    start: int
    baseline: float
    peak: float
    end: int | None = None

    @property
    def nights(self) -> int | None:
        return None if self.end is None else self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "nights": self.nights, "baseline": self.baseline, "peak": self.peak}


def escalation_rates(days: Sequence[Mapping[str, Any]], min_answered: int = DRIFT_MIN_ANSWERED) -> list[tuple[int, float]]:
    """(day, rate) for each day the student judged at least `min_answered` requests, oldest first: the share of the
    requests it answered that it was unsure of and escalated. Requests it never saw (a cold start, a pinned or teacher-only
    task, an endpoint outage) are left out, so a student outage or a change in the traffic mix is not read as drift."""
    out = []
    for d in sorted(days, key=lambda d: d["day"]):
        answered = int(d.get("answered") or 0)
        if answered >= max(1, min_answered):
            out.append((int(d["day"]), int(d.get("answered_escalated") or 0) / answered))
    return out


def drift_episodes(
    rates: Sequence[tuple[int, float]],
    baseline_days: int = DRIFT_BASELINE_DAYS,
    start: float = DRIFT_START,
    end: float = DRIFT_END,
) -> list[Episode]:
    """Drift episodes in a series of (day, escalation rate), oldest first. Outside an episode, a day whose rate is at
    least `start` above the lowest rate of the `baseline_days` days before it (all of them needed) starts one; the first
    later day at most `end` above that baseline ends it. The last episode may still be open (`end` None)."""
    episodes: list[Episode] = []
    current: Episode | None = None
    for i, (day, rate) in enumerate(rates):
        if current is not None:
            current.peak = max(current.peak, rate)
            if rate <= current.baseline + end + _EPS:
                current.end = day
                episodes.append(current)
                current = None
        elif i >= baseline_days:
            base = min(r for _, r in rates[i - baseline_days : i])
            if rate >= base + start - _EPS:
                current = Episode(start=day, baseline=base, peak=rate)
    if current is not None:
        episodes.append(current)
    return episodes


def drift_recovery(
    days: Sequence[Mapping[str, Any]],
    baseline_days: int = DRIFT_BASELINE_DAYS,
    min_answered: int = DRIFT_MIN_ANSWERED,
) -> dict[str, Any]:
    """SM-5: the longest drift episode in nights (end day - start day), over the whole history. None while an episode
    is open (it has not recovered yet); 0 when the escalation rate never rose. `met` is False once any episode took longer
    than the target, also one still open; None with too few days to see drift, or while an open episode is within it."""
    t = SM_TARGETS["SM-5"]
    rates = escalation_rates(days, min_answered)
    episodes = drift_episodes(rates, baseline_days)
    last = max((int(d["day"]) for d in days if (d.get("requests") or 0) > 0), default=None)
    detail: dict[str, Any] = {"days_measured": len(rates), "episodes": [e.as_dict() for e in episodes]}
    if len(rates) <= baseline_days:
        reason = f"{len(rates)} days with at least {min_answered} requests the student judged; drift needs {baseline_days + 1}"
        return _metric(t, None, None, **detail, reason=reason)
    closed = [e.nights for e in episodes if e.nights is not None]
    open_ep = next((e for e in episodes if e.end is None), None)
    longest = max(closed, default=0)
    if open_ep is not None:
        so_far = (last if last is not None else open_ep.start) - open_ep.start
        detail["open_nights"] = so_far
        late = max(longest, so_far)
        met = False if not t.reached(late) else None
        return _metric(t, None, met, **detail, reason=f"the episode that started on day {open_ep.start} is still open")
    return _metric(t, longest, t.reached(longest), **detail)


def student_latency(p50_ms: float | None, n: int, column: str) -> dict[str, Any]:
    """NFR-1: the median latency of the window's student-served requests (`Ledger.latency_quantile`), under one second.
    `column` says what was timed: `wall_ms` (the whole cascade) when the ledger records it, else `latency_ms` (the
    providers' part)."""
    t = NFR1_TARGET
    detail: dict[str, Any] = {"requests": n, "column": column, "quantile": NFR1_QUANTILE, "served_by": "student"}
    if p50_ms is None or n == 0:
        return _metric(t, None, None, **detail, reason="no timed student-served request in the window")
    return _metric(t, p50_ms, t.reached(p50_ms), **detail)


def summary(metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """How many metrics are met, not met, and not known yet."""
    met = [m["met"] for m in metrics.values()]
    return {"met": met.count(True), "not_met": met.count(False), "unknown": sum(1 for m in met if m is None)}


def report(
    ledger: Any,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_audits: int = DEFAULT_MIN_AUDITS,
    mode: str = "live",
) -> dict[str, Any]:
    """Every success metric, from `ledger` (a `ratchet.ledger.Ledger`). `mode` is the gateway's: in mock mode the
    numbers come from mock models, and `simulated` says so. Each ledger read takes the ledger's lock on its own, so
    requests keep flowing while a report is built; the metrics may then differ by the few requests logged in between."""
    check_window_days(window_days)
    if isinstance(min_audits, bool) or not isinstance(min_audits, int) or min_audits < 0:
        raise ValueError(f"min_audits must be a whole number, 0 or more, not {min_audits!r}")
    days = ledger.kpi_days()
    win = window(days, window_days)
    lo = int(win[0]["day"]) if win else None
    hi = int(win[-1]["day"]) if win else None
    if win:
        failed = ledger.failed_cost(lo, hi)
        n, agree = ledger.audit_totals(lo, hi)
        p50, timed, column = ledger.latency_quantile(NFR1_QUANTILE, lo, hi, served_by="student")
    else:
        failed, n, agree, p50, timed, column = 0.0, 0, 0, None, 0, "latency_ms"
    states = {v.id: v.state for v in ledger.versions(["live", "rolled_back"])}
    metrics = {
        "SM-1": cost_reduction(win, failed),
        "SM-2": audited_precision(n, agree, min_audits),
        "SM-3": student_share(win),
        "SM-4": bad_deploys(ledger.events_of(RELEASE_KINDS), states),
        "SM-5": drift_recovery(days),
        "NFR-1": student_latency(p50, timed, column),
    }
    for m in metrics.values():  # a value that is not a number says nothing about its target
        if isinstance(m["value"], float) and not math.isfinite(m["value"]):
            m["value"], m["met"] = None, None
    return _json_safe(
        {
            "mode": mode,
            "simulated": mode == "mock",
            "window_days": window_days,
            "window": {"from": lo, "to": hi, "days": len(win), "complete": len(win) >= window_days},
            "metrics": metrics,
            "summary": summary(metrics),
        }
    )


def _json_safe(x: Any) -> Any:
    """`x` with every NaN or infinity replaced by None. JSON has neither, and the gateway's responses refuse them, so one
    bad cost in the ledger would otherwise turn /ratchet/stats into a 500. A ledger written by Ratchet holds none."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x
