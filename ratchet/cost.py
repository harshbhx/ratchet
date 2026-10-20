"""Utilization-aware cost accounting (FR-15.1 to FR-15.4, NFR-11, ADR 0023).

A self-hosted student endpoint bills GPU-hours while it runs, whether requests arrive or not, so the per-token price
every savings figure used until v0.5 flatters the student whenever the endpoint sits idle. This module prices the GPU
time the ledger recorded (`compute_intervals`), reports savings both ways, works out the volume at which self-hosting
breaks even and says what to do: keep self-hosting, use the hosted fallback student, scale the endpoint to zero, or
wait for data.

    python -m ratchet.cost report [--db PATH] [--days N] [--json]
    python -m ratchet.cost endpoint up|down [--db PATH] [--usd-per-hour X] [--gpus N] [--at TIME] [--note TEXT]
    python -m ratchet.cost close KIND [--db PATH] [--at TIME]

GPU and token list prices are unverified until the day-1 checks pass (INV-9), and every report says so until an
operator sets RATCHET_PRICES_VERIFIED=1. Numbers from the simulator or the mock demo are simulation output.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TextIO

from .config import CostPolicy, Settings
from .ledger import COMPUTE_KINDS, DAY_SECONDS, Ledger, Version

RECOMMENDATIONS = ("self_host", "hosted_fallback", "scale_to_zero", "insufficient_data")
# Above this share of its hours idle (no request reached the student that day), the endpoint should scale to zero.
IDLE_SHARE = 0.5
# The daily breakdown covers at most this many days (the latest); the totals always cover the whole window.
MAX_DAILY = 731
# An open training or eval interval older than this is probably a job whose end was never recorded.
STALE_JOB_HOURS = 12.0
HOUR = 3600.0
# Simulation assumptions (docs/TESTING.md): a training job takes 600 s plus 0.05 s per training example, and starts at
# 22:00 of the simulated day it follows.
SIM_TRAIN_BASE_S = 600.0
SIM_TRAIN_PER_EXAMPLE_S = 0.05
SIM_NIGHT_START_S = 22 * HOUR
# Sources an interval may name (`compute_intervals.source`).
SOURCES = ("cli", "api", "deploy", "night", "simulation")


@dataclass(frozen=True)
class Segment:
    """A stretch of time when compute of one kind ran, at one rate: the whole deployment's USD per hour (per-GPU price
    times GPUs)."""

    start: float
    end: float
    usd_per_hour: float

    @property
    def hours(self) -> float:
        return (self.end - self.start) / HOUR

    @property
    def usd(self) -> float:
        return self.hours * self.usd_per_hour


def segments(intervals: Iterable[Mapping[str, Any]], now: float, lo: float = -math.inf, hi: float = math.inf) -> list[Segment]:
    """Sorted, disjoint segments covering the union of `intervals` (rows of one kind) clipped to [lo, hi). An open
    interval runs to `now`. Where records overlap, the segment is billed at the highest rate among them, so two records
    of one endpoint are counted once and never at less than either says. O(k log k) for k intervals."""
    spans: list[tuple[float, float, float]] = []
    for iv in intervals:
        start = float(iv["start_ts"])
        end = float(iv["end_ts"]) if iv["end_ts"] is not None else max(now, start)
        start, end = max(start, lo), min(end, hi)
        if end > start:
            spans.append((start, end, float(iv["usd_per_hour"] or 0.0) * int(iv["gpus"] or 0)))
    if not spans:
        return []
    spans.sort()
    points = sorted({p for s, e, _ in spans for p in (s, e)})
    active: list[tuple[float, float]] = []  # (-rate, end): the most expensive running record on top
    out: list[Segment] = []
    i = 0
    for a, b in zip(points, points[1:], strict=False):
        while i < len(spans) and spans[i][0] <= a:
            heapq.heappush(active, (-spans[i][2], spans[i][1]))
            i += 1
        while active and active[0][1] <= a:  # records that ended; the rest are dropped when they reach the top
            heapq.heappop(active)
        if not active:
            continue
        rate = -active[0][0]
        if out and out[-1].end == a and out[-1].usd_per_hour == rate:
            out[-1] = Segment(out[-1].start, b, rate)
        else:
            out.append(Segment(a, b, rate))
    return out


def usage(segs: Iterable[Segment]) -> tuple[float, float]:
    """(hours, USD) of the segments."""
    hours = usd = 0.0
    for s in segs:
        hours += s.hours
        usd += s.usd
    return hours, usd


def split(segs: Sequence[Segment], windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """(hours, USD) of the segments inside each window. Both lists are sorted and disjoint; one linear sweep."""
    out = [(0.0, 0.0)] * len(windows)
    j = 0
    for seg in segs:
        while j < len(windows) and windows[j][1] <= seg.start:
            j += 1
        k = j
        while k < len(windows) and windows[k][0] < seg.end:
            a, b = max(seg.start, windows[k][0]), min(seg.end, windows[k][1])
            if b > a:
                hours, usd = out[k]
                out[k] = (hours + (b - a) / HOUR, usd + (b - a) / HOUR * seg.usd_per_hour)
            k += 1
    return out


def _ratio(num: float, den: float | None) -> float | None:
    return num / den if den else None


def _pct(savings: float, baseline: float) -> float | None:
    return savings / baseline if baseline > 0 else None


def _money(x: float | None) -> str:
    if x is None:
        return "-"
    digits = 2 if abs(x) >= 1 or x == 0 else (4 if abs(x) >= 0.01 else 6)
    return f"{'-' if x < 0 else ''}${abs(x):,.{digits}f}"


def _count(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:,.0f}" if abs(x) >= 10 else f"{x:,.1f}"


def _share(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _label(policy: CostPolicy) -> str:
    return "prices confirmed by the operator" if policy.prices_verified else "unverified list prices"


def _recommend(
    endpoint_hours: float,
    student_served: int,
    avoided: float | None,
    break_even: float | None,
    crossover: float | None,
    observed: float | None,
    idle_share: float | None,
) -> str:
    """One of RECOMMENDATIONS (ADR 0023). Self-hosting needs both to beat the teacher spend it replaces (observed volume
    at or above break-even) and to beat the hosted fallback (at or above the crossover). Otherwise an endpoint idle
    most of its hours should scale to zero; without student answers there is nothing to judge; below the hosted
    crossover the hosted fallback is cheaper; and below break-even with the hosted fallback no cheaper either, the
    endpoint has to run fewer hours."""
    if endpoint_hours <= 0 or observed is None:
        return "insufficient_data"
    pays = break_even is not None and observed >= break_even
    beats_hosted = crossover is None or observed >= crossover
    if pays and beats_hosted:
        return "self_host"
    if idle_share is not None and idle_share > IDLE_SHARE:
        return "scale_to_zero"
    if student_served <= 0 or avoided is None:
        return "insufficient_data"
    if crossover is not None and observed < crossover:
        return "hosted_fallback"
    return "scale_to_zero"


def _explain(r: dict[str, Any], policy: CostPolicy) -> str:
    """The recommendation as one plain-English paragraph with the numbers behind it."""
    rate = f"{_gpus(policy.student.gpus, policy.student.usd_per_gpu_hour)}; {_label(policy)}"
    gpu = r["views"]["gpu_hours"]
    tok = r["views"]["per_token"]
    money = (
        f"Savings by GPU-hours paid: {_money(gpu['savings_usd'])} ({_share(gpu['savings_pct'])}); "
        f"per token: {_money(tok['savings_usd'])} ({_share(tok['savings_pct'])})."
    )
    observed = r["observed_student_requests_per_endpoint_hour"]
    be = r["break_even_student_requests_per_gpu_hour"]
    cross = r["self_host_beats_hosted_above_requests_per_hour"]
    rec = r["recommendation"]
    if rec == "insufficient_data":
        if r["endpoint_hours"] <= 0:
            why = (
                "No student endpoint uptime is recorded in this window, so the GPU-hour cost is unknown. Record it with "
                "`python -m ratchet.cost endpoint up` and `down` (or POST /ratchet/cost/endpoint)."
            )
        else:
            why = (
                f"The endpoint ran {r['endpoint_hours']:,.1f} h but the student served no requests in this window, so the "
                "teacher spend it avoids is unknown."
            )
        return f"Not enough data. {why} {money}"
    volume = f"The endpoint served {_count(observed)} student requests per hour it ran"
    if rec == "self_host":
        tail = ""
        if r["idle_share"] is not None and r["idle_share"] > IDLE_SHARE:
            tail = f" It was idle {_share(r['idle_share'])} of its hours; scaling to zero between bursts would save more."
        return (
            f"Self-hosting pays. {volume}, at or above the break-even of {_count(be)} and the hosted fallback's crossover "
            f"of {_count(cross) if cross is not None else 'n/a'} ({rate}).{tail} {money}"
        )
    if be is not None:
        below = f"below the break-even of {_count(be)}"
    elif r["avoided_teacher_usd_per_student_request"] is None:
        below = "with no student answer yet to judge it by"
    else:
        below = "and its answers avoid no teacher spend, so it can never break even"
    if rec == "scale_to_zero":
        if r["idle_share"] is not None and r["idle_share"] > IDLE_SHARE:
            lead = (
                f"Scale the endpoint to zero when idle: it ran {r['endpoint_hours']:,.1f} h, {r['idle_hours']:,.1f} h "
                f"({_share(r['idle_share'])}) of them on days the student got no request."
            )
        else:
            lead = "Run the endpoint fewer hours: at this volume neither the endpoint nor the hosted fallback pays for itself."
        return f"{lead} {volume}, {below} ({rate}). {money}"
    return (
        f"Self-hosting does not pay at this volume. {volume}, {below}; below {_count(cross)} an hour the hosted fallback "
        f"is cheaper ({rate}). Use the hosted fallback student on Token Factory (leave RATCHET_STUDENT_BASE empty) or "
        f"run the endpoint only while traffic arrives. {money}"
    )


def cost_report(
    ledger: Ledger,
    settings: Settings,
    since: int | None = None,
    until: int | None = None,
    now: float | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """The cost report for ledger days `since` to `until` (inclusive; None: from the first record, and to the current
    day), or for the last `days` days up to `until`. Requests are counted by their ledger day; compute intervals are
    clipped to the window's days on the ledger's clock, an open one running to `now` (default `ledger.now()`). One
    aggregate query over the requests and one over the intervals; no other I/O. Savings can be negative and are never
    clamped. See ADR 0023 for every formula."""
    policy = settings.cost_policy()
    now = ledger.now() if now is None else float(now)
    current = max(ledger.day, ledger.day_of(now, now))
    hi_day = current if until is None else int(until)
    lo_day = None if since is None else int(since)
    if days is not None:
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError(f"days must be 1 or more, not {days!r}")
        lo_day = hi_day - days + 1
    if lo_day is not None and lo_day > hi_day:
        raise ValueError(f"since (day {lo_day}) is after until (day {hi_day})")
    lo = ledger.day_start(lo_day, now) if lo_day is not None else -math.inf
    hi = ledger.day_start(hi_day + 1, now)

    rows = ledger.cost_by_day(lo_day, hi_day)
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in COMPUTE_KINDS}
    for iv in ledger.compute_intervals(since=None if lo == -math.inf else lo, until=hi):
        by_kind.setdefault(iv["kind"], []).append(iv)
    segs = {kind: segments(ivs, now, lo, hi) for kind, ivs in by_kind.items()}
    endpoint = segs["student_endpoint"]
    jobs = segs["training_job"] + segs["eval_job"]

    def total(key: str) -> Any:
        return sum(r[key] for r in rows)

    requests, served = total("requests"), total("student_served")
    per_token_usd, baseline = total("cost_usd"), total("baseline_usd")
    teacher_usd, student_usd = total("teacher_usd"), total("student_usd")
    endpoint_hours, endpoint_usd = usage(endpoint)
    training_hours, training_usd = usage(jobs)

    # Endpoint uptime on the days requests reached the student: what is left of its hours was idle, and student spend
    # on days without uptime came from the hosted fallback (or from uptime nobody recorded), priced per token.
    traffic = [r for r in rows if r["sampled"] > 0 or r["student_usd"] > 0]
    uptime = split(endpoint, [(ledger.day_start(r["day"], now), ledger.day_start(r["day"] + 1, now)) for r in traffic])
    busy_hours = sum(h for h, _ in uptime)
    served_on_endpoint = sum(r["student_served"] for r, (h, _) in zip(traffic, uptime, strict=True) if h > 0)
    hosted_student_usd = sum(r["student_usd"] for r, (h, _) in zip(traffic, uptime, strict=True) if h <= 0)
    served_off_endpoint = sum(r["student_served"] for r, (h, _) in zip(traffic, uptime, strict=True) if h <= 0)
    idle_hours = max(0.0, endpoint_hours - busy_hours)
    idle_share = idle_hours / endpoint_hours if endpoint_hours > 0 else None

    avoided = total("avoided") / served if served else None
    gpu_per_hour = policy.student.usd_per_hour
    break_even = gpu_per_hour / avoided if avoided is not None and avoided > 0 else None
    fallback_price = settings.price(settings.fallback_student_model)
    with_tokens = total("served_with_tokens")
    hosted = fallback_price.cost(total("student_in_tokens"), total("student_out_tokens")) / with_tokens if with_tokens else None
    crossover = gpu_per_hour / hosted if hosted else None
    observed = served_on_endpoint / endpoint_hours if endpoint_hours > 0 else None
    utilization = observed / break_even if observed is not None and break_even else None

    gpu_total = teacher_usd + endpoint_usd + training_usd + hosted_student_usd
    views = {
        "per_token": {
            "total_usd": per_token_usd,
            "savings_usd": baseline - per_token_usd,
            "savings_pct": _pct(baseline - per_token_usd, baseline),
        },
        "gpu_hours": {
            "total_usd": gpu_total,
            "savings_usd": baseline - gpu_total,
            "savings_pct": _pct(baseline - gpu_total, baseline),
        },
    }
    report: dict[str, Any] = {
        "window": {"since_day": lo_day, "until_day": hi_day, "now": now},
        "model": policy.model,
        "headline": {"model": policy.model, **views[policy.model]},
        "prices": _prices(settings, policy),
        "requests": requests,
        "student_served": served,
        "teacher_served": total("teacher_served"),
        "teacher_usd": teacher_usd,
        "student_per_token_usd": student_usd,
        "hosted_student_usd": hosted_student_usd,
        "student_served_off_endpoint": served_off_endpoint,
        "baseline_usd": baseline,
        "split_unknown_requests": total("split_unknown"),
        "split_unknown_usd": total("split_unknown_usd"),
        "endpoint_hours": endpoint_hours,
        "endpoint_usd": endpoint_usd,
        "idle_hours": idle_hours,
        "idle_share": idle_share,
        "training_hours": training_hours,
        "training_usd": training_usd,
        "views": views,
        "avoided_teacher_usd_per_student_request": avoided,
        "break_even_student_requests_per_gpu_hour": break_even,
        "hosted_fallback_usd_per_request": hosted,
        "self_host_beats_hosted_above_requests_per_hour": crossover,
        "observed_student_requests_per_endpoint_hour": observed,
        "utilization_vs_break_even": utilization,
        "recommendation": _recommend(endpoint_hours, served, avoided, break_even, crossover, observed, idle_share),
    }
    report["explanation"] = _explain(report, policy)
    report["open_intervals"], report["warnings"] = _open(by_kind, now, report)
    report["daily"], report["daily_truncated"] = _daily(ledger, rows, segs, lo_day, hi_day, now)
    return report


def _prices(settings: Settings, policy: CostPolicy) -> dict[str, Any]:
    def per_m(model: str) -> dict[str, float]:
        p = settings.price(model)
        return {"input": p.input, "output": p.output}

    return {
        "verified": policy.prices_verified,
        "label": _label(policy),
        "student_gpu_usd_per_hour": policy.student.usd_per_gpu_hour,
        "student_gpus": policy.student.gpus,
        "student_endpoint_usd_per_hour": policy.student.usd_per_hour,
        "train_gpu_usd_per_hour": policy.train.usd_per_gpu_hour,
        "train_gpus": policy.train.gpus,
        "teacher_model": settings.teacher_model,
        "teacher_usd_per_m_tokens": per_m(settings.teacher_model),
        "student_usd_per_m_tokens": per_m("student"),
        "fallback_student_model": settings.fallback_student_model,
        "fallback_student_usd_per_m_tokens": per_m(settings.fallback_student_model),
    }


def _open(
    by_kind: Mapping[str, list[dict[str, Any]]], now: float, report: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Intervals still running (they count up to `now`), and warnings an operator should act on."""
    opened = [
        {"id": iv["id"], "kind": kind, "start_ts": iv["start_ts"], "hours": max(0.0, now - iv["start_ts"]) / HOUR}
        for kind, ivs in by_kind.items()
        for iv in ivs
        if iv["end_ts"] is None
    ]
    warnings = []
    for iv in opened:
        if iv["kind"] != "student_endpoint" and iv["hours"] > STALE_JOB_HOURS:
            warnings.append(
                f"{iv['kind']} interval {iv['id']} has been open for {iv['hours']:,.1f} h and counts up to now; if the job "
                f"ended, close it with `python -m ratchet.cost close {iv['kind']} --at <end time>`"
            )
    if report["endpoint_hours"] > 0 and report["student_served_off_endpoint"]:
        warnings.append(
            f"{report['student_served_off_endpoint']} student answers fell on days with no recorded endpoint uptime and are "
            "priced per token (the hosted fallback, or uptime nobody recorded)"
        )
    if report["split_unknown_requests"]:
        warnings.append(
            f"{report['split_unknown_requests']} requests from before v0.5 have no student/teacher split; their "
            f"{_money(report['split_unknown_usd'])} counts as teacher spend in the GPU-hour view"
        )
    return opened, warnings


_EMPTY_DAY = {"requests": 0, "student_served": 0, "teacher_usd": 0.0, "student_usd": 0.0, "baseline_usd": 0.0, "cost_usd": 0.0}


def _daily(
    ledger: Ledger,
    rows: list[dict[str, Any]],
    segs: Mapping[str, list[Segment]],
    lo_day: int | None,
    hi_day: int,
    now: float,
) -> tuple[list[dict[str, Any]], bool]:
    """One row per day with requests or compute in the window, the latest MAX_DAILY days at most."""
    first = [ledger.day_of(s[0].start, now) for s in segs.values() if s]
    last = [ledger.day_of(s[-1].end, now) for s in segs.values() if s]
    days = [r["day"] for r in rows]
    if not days and not first:
        return [], False
    d0 = min(days + first)
    d1 = min(hi_day, max(days + last))
    if lo_day is not None:
        d0 = max(d0, lo_day)
    truncated = d1 - d0 + 1 > MAX_DAILY
    if truncated:
        d0 = d1 - MAX_DAILY + 1
    if d1 < d0:
        return [], False
    windows = [(ledger.day_start(d, now), ledger.day_start(d + 1, now)) for d in range(d0, d1 + 1)]
    endpoint = split(segs["student_endpoint"], windows)
    training = split(segs["training_job"], windows)
    evals = split(segs["eval_job"], windows)
    by_day = {r["day"]: r for r in rows}
    out = []
    for i, d in enumerate(range(d0, d1 + 1)):
        r = by_day.get(d)
        hours, endpoint_usd = endpoint[i]
        training_usd = training[i][1] + evals[i][1]
        if r is None and hours <= 0 and training_usd <= 0 and training[i][0] + evals[i][0] <= 0:
            continue
        r = r or _EMPTY_DAY
        hosted = r["student_usd"] if hours <= 0 else 0.0
        gpu_total = r["teacher_usd"] + endpoint_usd + training_usd + hosted
        out.append(
            {
                "day": d,
                "requests": r["requests"],
                "student_served": r["student_served"],
                "teacher_usd": r["teacher_usd"],
                "student_per_token_usd": r["student_usd"],
                "endpoint_hours": hours,
                "endpoint_usd": endpoint_usd,
                "training_usd": training_usd,
                "baseline_usd": r["baseline_usd"],
                "savings_pct_per_token": _pct(r["baseline_usd"] - r["cost_usd"], r["baseline_usd"]),
                "savings_pct_gpu_hours": _pct(r["baseline_usd"] - gpu_total, r["baseline_usd"]),
            }
        )
    return out, truncated


def stats_block(report: Mapping[str, Any]) -> dict[str, Any]:
    """The compact `cost` block of /ratchet/stats."""
    return {
        "model": report["model"],
        "endpoint_hours": report["endpoint_hours"],
        "gpu_hours_savings_pct": report["views"]["gpu_hours"]["savings_pct"],
        "break_even_student_requests_per_gpu_hour": report["break_even_student_requests_per_gpu_hour"],
        "recommendation": report["recommendation"],
    }


def record_simulated_day(ledger: Ledger, policy: CostPolicy, day: int, trained: Version | None = None) -> None:
    """The simulator's compute for one day, on the synthetic clock (day N starts at N x 86400 s): the student endpoint up
    `policy.mock_endpoint_hours` from the start of the day, and, when the night trained a version, a training job from
    22:00 lasting SIM_TRAIN_BASE_S plus SIM_TRAIN_PER_EXAMPLE_S per training example. Simulation assumptions, not
    measurements. Writes only compute intervals, so no request, event or random draw moves."""
    start = float(day * DAY_SECONDS)
    if policy.mock_endpoint_hours > 0:
        ledger.compute_start(
            "student_endpoint",
            policy.student.usd_per_gpu_hour,
            policy.student.gpus,
            "simulation",
            ts=start,
            note=f"simulated: {policy.mock_endpoint_hours:g} h a day",
        )
        ledger.compute_stop("student_endpoint", ts=start + policy.mock_endpoint_hours * HOUR)
    if trained is not None:
        seconds = SIM_TRAIN_BASE_S + SIM_TRAIN_PER_EXAMPLE_S * trained.train_examples
        job = ledger.compute_start(
            "training_job",
            policy.train.usd_per_gpu_hour,
            policy.train.gpus,
            "simulation",
            version=trained.id,
            ts=start + SIM_NIGHT_START_S,
            note=f"simulated: {SIM_TRAIN_BASE_S:g} s + {SIM_TRAIN_PER_EXAMPLE_S:g} s x {trained.train_examples} examples",
        )
        ledger.compute_stop("training_job", ts=start + SIM_NIGHT_START_S + seconds, interval_id=job)


# ---------- the CLI ----------
def parse_time(text: str) -> float:
    """Seconds since the epoch, from a number of seconds or an ISO 8601 time (UTC when it names no zone)."""
    try:
        value = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"not a time: {text!r} (use seconds since the epoch or ISO 8601)") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        value = parsed.timestamp()
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"not a time: {text!r}")
    return value


def render(report: Mapping[str, Any]) -> str:
    """The report as a short table for a terminal."""
    w = report["window"]
    days = f"days {w['since_day']}-{w['until_day']}" if w["since_day"] is not None else f"up to day {w['until_day']}"
    tok, gpu = report["views"]["per_token"], report["views"]["gpu_hours"]
    p = report["prices"]
    lines = [
        f"Cost report, {days} ({p['label']}; headline view: {report['model']})",
        "",
        f"{'':24}{'per token':>14}{'GPU-hours':>14}",
        f"{'total':24}{_money(tok['total_usd']):>14}{_money(gpu['total_usd']):>14}",
        f"{'all-teacher baseline':24}{_money(report['baseline_usd']):>14}{_money(report['baseline_usd']):>14}",
        f"{'savings':24}{_money(tok['savings_usd']):>14}{_money(gpu['savings_usd']):>14}",
        f"{'savings %':24}{_share(tok['savings_pct']):>14}{_share(gpu['savings_pct']):>14}",
        "",
        f"requests: {report['requests']:,} ({report['student_served']:,} served by the student)",
        f"teacher: {_money(report['teacher_usd'])}; student per token: {_money(report['student_per_token_usd'])}",
        f"student endpoint: {report['endpoint_hours']:,.1f} h, {_money(report['endpoint_usd'])} "
        f"(${p['student_gpu_usd_per_hour']:,.2f}/GPU-hour x {p['student_gpus']}); idle {report['idle_hours']:,.1f} h "
        f"({_share(report['idle_share'])})",
        f"training jobs: {report['training_hours']:,.2f} h, {_money(report['training_usd'])}",
        f"teacher spend avoided per student answer: {_money(report['avoided_teacher_usd_per_student_request'])}",
        f"break-even: {_count(report['break_even_student_requests_per_gpu_hour'])} student answers per endpoint hour; "
        f"observed: {_count(report['observed_student_requests_per_endpoint_hour'])} "
        f"({_share(report['utilization_vs_break_even'])} of break-even)",
        f"hosted fallback: {_money(report['hosted_fallback_usd_per_request'])} per student answer; self-hosting beats it "
        f"above {_count(report['self_host_beats_hosted_above_requests_per_hour'])} per hour",
        "",
        f"recommendation: {report['recommendation']}",
        report["explanation"],
    ]
    for warning in report["warnings"]:
        lines.append(f"warning: {warning}")
    if report["daily"]:
        lines += ["", f"{'day':>5}{'requests':>10}{'student':>9}{'endpoint h':>12}{'per token':>11}{'GPU-hours':>11}"]
        for d in report["daily"][-14:]:
            lines.append(
                f"{d['day']:>5}{d['requests']:>10,}{d['student_served']:>9,}{d['endpoint_hours']:>12,.1f}"
                f"{_share(d['savings_pct_per_token']):>11}{_share(d['savings_pct_gpu_hours']):>11}"
            )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m ratchet.cost", description="Ratchet cost accounting (ADR 0023)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    db = argparse.ArgumentParser(add_help=False)
    db.add_argument("--db", help="the live ledger file (default: RATCHET_DB)")
    rep = sub.add_parser("report", parents=[db], help="print the cost report and the recommendation")
    rep.add_argument("--days", type=int, help="only the last N ledger days")
    rep.add_argument("--json", action="store_true", help="print the whole report as JSON")
    ep = sub.add_parser("endpoint", parents=[db], help="record the student endpoint going up or down")
    ep.add_argument("state", choices=("up", "down"))
    ep.add_argument("--usd-per-hour", type=float, help="USD per GPU-hour (default: RATCHET_STUDENT_GPU_USD_PER_HOUR)")
    ep.add_argument("--gpus", type=int, help="GPUs (default: RATCHET_STUDENT_GPUS)")
    ep.add_argument("--at", help="when, if not now: seconds since the epoch or ISO 8601 (UTC unless it says)")
    ep.add_argument("--note", help="a note stored with the interval")
    ep.add_argument("--source", choices=SOURCES, default="cli", help=argparse.SUPPRESS)
    close = sub.add_parser("close", parents=[db], help="close an open interval of a kind (a job whose end was lost)")
    close.add_argument("kind", choices=COMPUTE_KINDS)
    close.add_argument("--at", help="when it ended: seconds since the epoch or ISO 8601 (default: now)")
    return ap


def _ledger(path: str, settings: Settings) -> Ledger:
    """The live ledger at `path`, which must exist: the CLI never creates an empty ledger somewhere it was not asked to."""
    if path != ":memory:" and not os.path.exists(path):
        raise FileNotFoundError(f"no ledger at {path!r}; pass --db or set RATCHET_DB")
    return Ledger(path, settings.golden_fraction, auto_day=True, sync=settings.ledger_sync())


def _when(text: str | None) -> float | None:
    if text is None:
        return None
    ts = parse_time(text)
    if ts > time.time() + 300:
        raise ValueError(f"--at {text!r} is in the future; record what happened, not a plan")
    return ts


def main(argv: Sequence[str] | None = None, out: TextIO | None = None, settings: Settings | None = None) -> int:
    """The CLI. Exit 0 on success, 2 on a usage or input error (argparse's code). Opens the ledger file at --db or
    RATCHET_DB as a live ledger (wall-clock days from the first request)."""
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    try:
        s = settings or Settings()
        policy = s.cost_policy()
        led = _ledger(args.db or s.db_path, s)
        if args.cmd == "report":
            report = cost_report(led, s, days=args.days)
            print(json.dumps(report, indent=2) if args.json else render(report), file=out)
        elif args.cmd == "endpoint":
            when = _when(args.at)
            _, _, summary = record_endpoint(led, policy, args.state, args.source, args.usd_per_hour, args.gpus, when, args.note)
            print(summary, file=out)
        else:
            closed = led.compute_stop(args.kind, ts=_when(args.at))
            print(f"closed {args.kind} interval {closed}" if closed is not None else f"no open {args.kind} interval", file=out)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def record_endpoint(
    ledger: Ledger,
    policy: CostPolicy,
    state: str,
    source: str,
    usd_per_hour: float | None = None,
    gpus: int | None = None,
    ts: float | None = None,
    note: str | None = None,
    by: str | None = None,
) -> tuple[dict[str, Any] | None, bool, str]:
    """Record the student endpoint going `up` or `down` (the CLI's and POST /ratchet/cost/endpoint's shared path):
    (the interval, whether anything changed, a one-line summary). `up` while it is up changes nothing; `down` while it
    is down neither. A change writes an `endpoint` event naming the source and `by` (an admin's name, never a key)."""
    if state not in ("up", "down"):
        raise ValueError(f"state must be up or down, not {state!r}")
    who = f" by {by}" if by else ""
    if state == "up":
        rate = policy.student.usd_per_gpu_hour if usd_per_hour is None else usd_per_hour
        n = policy.student.gpus if gpus is None else gpus
        before = {iv["id"] for iv in ledger.compute_intervals("student_endpoint") if iv["end_ts"] is None}
        iid = ledger.compute_start("student_endpoint", rate, n, source, ts=ts, note=note)
        iv = _interval(ledger, iid)
        if iid in before:
            return (
                iv,
                False,
                f"student endpoint already up since {_stamp(ledger, iv['start_ts'])} (interval {iid}); nothing changed",
            )
        ledger.event("endpoint", f"student endpoint up: {_gpus(n, rate)} ({source}{who})")
        return iv, True, f"student endpoint up at {_stamp(ledger, iv['start_ts'])}: {_gpus(n, rate)} (interval {iid})"
    iid = ledger.compute_stop("student_endpoint", ts=ts, note=note)
    if iid is None:
        return None, False, "student endpoint already down; nothing changed"
    iv = _interval(ledger, iid)
    hours = (iv["end_ts"] - iv["start_ts"]) / HOUR
    ledger.event("endpoint", f"student endpoint down after {hours:,.1f} h ({source}{who})")
    return iv, True, f"student endpoint down at {_stamp(ledger, iv['end_ts'])} after {hours:,.1f} h (interval {iid})"


def _gpus(n: int, rate: float) -> str:
    return f"{n} GPU{'' if n == 1 else 's'} at ${rate:,.2f} per GPU-hour"


def _interval(ledger: Ledger, iid: int) -> dict[str, Any]:
    (iv,) = [iv for iv in ledger.compute_intervals("student_endpoint") if iv["id"] == iid]
    return iv


def _stamp(ledger: Ledger, ts: float) -> str:
    """A moment on the ledger's clock for people: UTC on a live ledger, the simulated day and time otherwise."""
    if ledger.auto_day:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    day, rest = divmod(ts, DAY_SECONDS)
    return f"simulated day {int(day)} {int(rest // HOUR):02d}:{int(rest % HOUR // 60):02d}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
