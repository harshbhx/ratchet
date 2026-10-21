"""Ledger time machine (FR-16.5, ADR 0028): what a version's logged traffic would have looked like under another
threshold or audit rate.

Ratchet designs its own labelling, so every labelling probability is known and logged (`requests.label_p`): a request
the student answered and escalated is labelled by the teacher with probability 1, an answer it served with the audit
rate it was drawn at. With known probabilities and an audit rate above 0 (positivity), Hajek's weighted mean estimates
precision at any threshold, including thresholds below the live one, where audits alone see nothing. Coverage needs no
label at all. Cost uses each request's logged student spend and its all-teacher cost (known when the teacher was called,
else the token formula's estimate times a ratio calibrated on audited rows).

Nothing here touches the request path: the router only logs (`threshold`, `label_p`, `student_agree`,
`baseline_est_usd`), and this module reads the ledger. Never pool versions: their confidence scales differ.

    python -m ratchet.replay --db ratchet.db [--version 3] [--sweep 0.3:0.95:0.05] [--audit-rate 0.02] [--target 0.97]

The suggested threshold is advisory: its interval is approximate (Wilson at Kish's effective sample size), not a
certificate, and thresholds change only through Night School (ADR 0026).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, TextIO

from . import stats, tasks
from .ledger import Ledger

# The frontier's default grid, and the most thresholds one frontier may hold (an endpoint or CLI cannot ask for more).
GRID_LO, GRID_HI, GRID_STEP = 0.30, 1.00, 0.01
MAX_GRID = 1001
DEFAULT_TARGET = 0.97
# Audited answers the cost ratio needs before it replaces the formula (below it, R = 1: the formula as it is).
MIN_RATIO_N = 30
# A labelling probability below this is treated as 0 (no label can be expected), so weights stay finite.
MIN_LABEL_P = 1e-6
Z95 = 1.96
INTERVAL = "approximate (Wilson at Kish n_eff)"
RATIO_METHOD = "ratio estimator (Cochran), audited served answers weighted by 1/label_p"
ADVISORY = "approximate interval, not a certificate; thresholds change only through Night School"
# Why a precision is null.
POSITIVITY = "audit rate was 0: precision above the live threshold cannot be identified"
NOTHING_SERVED = "nothing would be served at this threshold"
NO_LABELS = "no labelled answer at this threshold"
# A request escalated on this route was confident but could not fill the client's n choices; that does not depend on the
# threshold, so it is never served at any threshold (ADR 0020).
NEVER_SERVED_ROUTES = frozenset({"n_choices"})
SIMULATION = "simulation (mock models)"


class Row(NamedTuple):
    """One request the version's student answered, as the replay reads it (`Ledger.replay_rows`)."""

    id: int
    day: int
    task: str
    confidence: float
    threshold: float  # the threshold the answer was held to when it was logged
    label_p: float  # the designed probability that a teacher label of the answer is observed
    agree: int | None  # 1/0: the student's answer agreed with the escalation's or a random audit's teacher answer
    route: str | None
    audited: bool
    student_usd: float  # the request's student spend as logged (its samples, and any shadow's)
    teacher_usd: float | None  # what the teacher alone cost, when it was called (escalation or audit)
    estimate_usd: float  # the token formula's all-teacher estimate

    @property
    def servable(self) -> bool:
        """Whether some threshold above 0 serves it: a valid answer (confidence above 0; a sample set without one scores
        0) that did not escalate for a reason the threshold does not decide."""
        return self.confidence > 0 and self.route not in NEVER_SERVED_ROUTES


@dataclass(frozen=True)
class History:
    """A version's replayable traffic (`load`): its rows in id order and how many of its rows were left out, by reason
    (`before_logging`, `no_student_answer`, `pinned_now`, `malformed`)."""

    version: int
    rows: tuple[Row, ...]
    excluded: dict[str, int] = field(default_factory=dict)

    @property
    def n_excluded(self) -> int:
        return sum(self.excluded.values())


def _number(value: object) -> float | None:
    """`value` as a float when it is a finite int or float (never a bool), else None. An int too large for a float is
    None too, instead of an OverflowError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        x = float(value)
    except OverflowError:
        return None
    return x if math.isfinite(x) else None


def _finite(value: object, lo: float | None = None, hi: float | None = None) -> bool:
    """Whether `value` is a finite number within [lo, hi] (either bound may be left out)."""
    x = _number(value)
    return x is not None and (lo is None or x >= lo) and (hi is None or x <= hi)


def _row(r: dict[str, Any]) -> Row | None:
    """A ledger row as a `Row`, or None when a value is out of range (a ledger edited outside Ratchet): numbers must be
    finite, probabilities in [0, 1], costs 0 or more, so every result stays a finite JSON number."""
    agree = r["student_agree"]
    teacher = r["teacher_usd"]
    if not (
        _finite(r["confidence"], 0.0, 1.0)
        and _finite(r["threshold"], 0.0)
        and _finite(r["label_p"], 0.0, 1.0)
        and _finite(r["student_usd"], 0.0)
        and _finite(r["estimate_usd"], 0.0)
        and (teacher is None or _finite(teacher, 0.0))
        and agree in (None, 0, 1)
    ):
        return None
    return Row(
        id=int(r["id"]),
        day=int(r["day"] or 0),
        task=str(r["task"]),
        confidence=float(r["confidence"]),
        threshold=float(r["threshold"]),
        label_p=float(r["label_p"]),
        agree=None if agree is None else int(agree),
        route=r["route"],
        audited=bool(r["audited"]),
        student_usd=float(r["student_usd"]),
        teacher_usd=None if teacher is None else float(teacher),
        estimate_usd=float(r["estimate_usd"]),
    )


def load(ledger: Ledger, version: int, since_day: int | None = None, until_day: int | None = None) -> History:
    """Version `version`'s requests on ledger days `since_day` to `until_day` (`Ledger.replay_rows`), less the rows of a
    task that is pinned now: the student will never answer those again, so no threshold applies to them (INV-10 at read
    time, as Night School reads it)."""
    raw, excluded = ledger.replay_rows(version, since_day, until_day)
    pinned: dict[str, bool] = {}
    rows: list[Row] = []
    left = {**excluded, "pinned_now": 0, "malformed": 0}
    for r in raw:
        name = r["task"]
        if name not in pinned:
            pinned[name] = tasks.get(name).pinned
        if pinned[name]:
            left["pinned_now"] += 1
            continue
        row = _row(r)
        if row is None:
            left["malformed"] += 1
            continue
        rows.append(row)
    return History(version, tuple(rows), left)


# ---------------------------------------------------------------------------------------------------------------------
# Estimators
# ---------------------------------------------------------------------------------------------------------------------
def ratio(rows: Iterable[Row]) -> dict[str, Any]:
    """The calibrated baseline: the ratio R of what the teacher really cost to the token formula's estimate, on audited
    answers the student served (a random sample of them, weighted by 1 / label_p), with its linearised standard error
    (`stats.ratio_estimate`). With fewer than MIN_RATIO_N audited answers, R is 1.0 (the formula as it is) and the status
    'insufficient'. R tells how far the assumed RATCHET_TEACHER_REASONING_TOKENS is from what the teacher really writes."""
    pairs, weights = [], []
    for r in rows:
        if r.audited and r.route == "student" and r.teacher_usd is not None and r.label_p >= MIN_LABEL_P:
            pairs.append((r.estimate_usd, r.teacher_usd))
            weights.append(1.0 / r.label_p)
    est = stats.ratio_estimate(pairs, weights, min_n=MIN_RATIO_N)
    se = est["se"]
    ok = est["status"] == "ok" and se is not None
    return {
        "R": est["ratio"],
        "se": se if ok else None,
        "interval": [est["ratio"] - Z95 * se, est["ratio"] + Z95 * se] if ok else None,
        "n": est["n"],
        "n_eff": est["n_eff"],
        "min_n": MIN_RATIO_N,
        "status": est["status"],
        "method": RATIO_METHOD,
    }


def logged_rate(rows: Iterable[Row]) -> float | None:
    """The audit rate the version's served answers were drawn at (their mean `label_p`; one value unless a canary had
    its own rate), or None when it served nothing. It prices the audits of a request that escalated in fact but would be
    served at a lower threshold."""
    rates = [r.label_p for r in rows if r.route == "student"]
    return sum(rates) / len(rates) if rates else None


class _Point(NamedTuple):
    confidence: float
    threshold: float
    servable: bool
    p: float
    agree: int | None
    teacher: float  # T_i: the all-teacher cost, known or calibrated
    rate: float  # the audit rate it is drawn at if served, as logged
    student: float


def _points(rows: Sequence[Row], r: float, default_rate: float) -> list[_Point]:
    """The rows as the accumulators read them, by confidence, highest first (ties in id order)."""
    pts = [
        _Point(
            row.confidence,
            row.threshold,
            row.servable,
            row.label_p,
            row.agree,
            row.teacher_usd if row.teacher_usd is not None else row.estimate_usd * r,
            row.label_p if row.route == "student" else default_rate,
            row.student_usd,
        )
        for row in rows
    ]
    pts.sort(key=lambda p: -p.confidence)
    return pts


class _Acc:
    """Running sums over the requests a threshold serves."""

    __slots__ = ("served", "w", "w2", "wy", "labelled", "zero", "teacher", "audits")

    def __init__(self) -> None:
        self.served = self.labelled = self.zero = 0
        self.w = self.w2 = self.wy = self.teacher = self.audits = 0.0

    def add(self, pt: _Point, audit_rate: float | None) -> None:
        self.served += 1
        if pt.p < MIN_LABEL_P:
            self.zero += 1  # never labelled by design: positivity fails at this threshold
        elif pt.agree is not None:  # a designed label that was obtained (a failed or invalid teacher answer is missing)
            w = 1.0 / pt.p
            self.w += w
            self.w2 += w * w
            self.wy += w * pt.agree
            self.labelled += 1
        self.teacher += pt.teacher
        self.audits += (pt.rate if audit_rate is None else audit_rate) * pt.teacher


def _result(acc: _Acc, n: int, student: float, teacher: float, t: float | None, audit_rate: float | None) -> dict[str, Any]:
    """One threshold's numbers. cost = student spend + the all-teacher cost of every request not served + the audit rate
    times that of every request served; baseline = the all-teacher cost of every request."""
    precision: float | None = None
    reason: str | None = None
    if acc.served == 0:
        reason = NOTHING_SERVED
    elif acc.zero:
        reason = POSITIVITY
    elif acc.w <= 0:
        reason = NO_LABELS
    else:
        precision = min(1.0, max(0.0, acc.wy / acc.w))
    n_eff = acc.w * acc.w / acc.w2 if acc.w2 > 0 and precision is not None else 0.0
    interval = list(stats.wilson(precision * n_eff, n_eff)) if precision is not None and n_eff > 0 else None
    coverage = acc.served / n if n else None
    cost = student + max(0.0, teacher - acc.teacher) + acc.audits
    return {
        "threshold": t,
        "audit_rate": audit_rate,
        "n": n,
        "served": acc.served,
        "coverage": coverage,
        "labelled": acc.labelled,
        "precision": precision,
        "precision_reason": reason,
        "n_eff": n_eff,
        "interval": interval,
        "interval_method": INTERVAL,
        "served_errors_per_1k": (1000 * coverage * (1 - precision) if coverage is not None and precision is not None else None),
        "cost_usd": cost,
        "baseline_usd": teacher,
        "saved_usd": teacher - cost,
        "savings": 1 - cost / teacher if teacher > 0 else None,
    }


def _defaults(rows: Sequence[Row], r: float | None, default_rate: float | None) -> tuple[float, float]:
    if r is None:
        r = ratio(rows)["R"]
    if default_rate is None:
        default_rate = logged_rate(rows)
    return float(r), float(default_rate if default_rate is not None else 0.0)


def check_threshold(t: object, what: str = "threshold") -> float:
    """A threshold to replay: a number above 0 and at most stats.SERVE_NOTHING (1.01, serve nothing). At 0 a sample set
    without a valid answer would count as served, which the router never does."""
    x = _number(t)
    if x is None or not 0 < x <= stats.SERVE_NOTHING:
        raise ValueError(f"{what} must be more than 0 and at most {stats.SERVE_NOTHING}, not {t!r}")
    return x


def check_rate(a: object, what: str = "audit_rate") -> float:
    x = _number(a)
    if x is None or not 0 <= x <= 1:
        raise ValueError(f"{what} must be from 0 to 1, not {a!r}")
    return x


def check_target(target: object) -> float:
    x = _number(target)
    if x is None or not 0 < x < 1:
        raise ValueError(f"target must be strictly between 0 and 1, not {target!r}")
    return x


def sweep(
    rows: Sequence[Row],
    thresholds: Sequence[float],
    audit_rate: float | None = None,
    *,
    r: float | None = None,
    default_rate: float | None = None,
) -> list[dict[str, Any]]:
    """`at` for each threshold, in the order given, in one pass over the rows sorted by confidence (O(N log N + G)), so
    a frontier costs about as much as one replay. Each result equals `at(rows, t, ...)` exactly."""
    ts = [check_threshold(t) for t in thresholds]
    if audit_rate is not None:
        audit_rate = check_rate(audit_rate)
    r, default_rate = _defaults(rows, r, default_rate)
    pts = _points(rows, r, default_rate)
    student = sum(p.student for p in pts)
    teacher = sum(p.teacher for p in pts)
    acc, i, out = _Acc(), 0, {}
    for t in sorted(set(ts), reverse=True):
        while i < len(pts) and pts[i].confidence >= t:
            if pts[i].servable:
                acc.add(pts[i], audit_rate)
            i += 1
        out[t] = _result(acc, len(pts), student, teacher, t, audit_rate)
    return [dict(out[t]) for t in ts]


def at(
    rows: Sequence[Row],
    t: float | None = None,
    audit_rate: float | None = None,
    *,
    r: float | None = None,
    default_rate: float | None = None,
) -> dict[str, Any]:
    """Coverage, precision, cost and savings had the version served at threshold `t` with audit rate `audit_rate`.

    - `t` None replays each request at the threshold it was logged with (what happened); `audit_rate` None prices each
      served request's audits at the rate it was drawn at (a request that escalated in fact at `default_rate`, by
      default the version's logged rate, `logged_rate`).
    - coverage = (1/N) sum 1[c >= t], exact: no label is needed. A request without a valid answer, or one that could not
      fill its n choices, is never served.
    - precision (Hajek) = sum over labelled served rows of y / label_p, divided by the sum of 1 / label_p; null with a
      reason when nothing is served, when a served row could never be labelled (audit rate 0: positivity), or when no
      served row has a label. Labels come from the logs, so `audit_rate` changes cost only.
    - interval: `stats.wilson(precision * n_eff, n_eff)` at Kish's n_eff = (sum w)^2 / sum w^2: approximate.
    - cost = sum s_i + sum over c < t of T_i + a * sum over c >= t of T_i, with T_i the request's all-teacher cost
      (known when the teacher was called, else the formula's estimate times `r`, by default `ratio(rows)['R']`);
      baseline = sum T_i; savings = 1 - cost / baseline. An audit is priced as the request's T_i (exact for one choice).
    - served_errors_per_1k = 1000 * coverage * (1 - precision)."""
    if t is not None:
        return sweep(rows, [t], audit_rate, r=r, default_rate=default_rate)[0]
    if audit_rate is not None:
        audit_rate = check_rate(audit_rate)
    r, default_rate = _defaults(rows, r, default_rate)
    pts = _points(rows, r, default_rate)
    acc = _Acc()
    for p in pts:
        if p.servable and p.confidence >= p.threshold:
            acc.add(p, audit_rate)
    return _result(acc, len(pts), sum(p.student for p in pts), sum(p.teacher for p in pts), None, audit_rate)


def grid(lo: float = GRID_LO, hi: float = GRID_HI, step: float = GRID_STEP) -> list[float]:
    """Thresholds from `lo` to `hi` (both included when on the grid) every `step`, counted in whole steps so they do
    not drift (0.3 + 7 x 0.1 is 0.7, not 0.7000000000000001). At most MAX_GRID of them."""
    lo, hi = check_threshold(lo, "lo"), check_threshold(hi, "hi")
    width = _number(step)
    if width is None or width <= 0:
        raise ValueError(f"step must be a number above 0, not {step!r}")
    if lo > hi:
        raise ValueError(f"lo ({lo}) must not be above hi ({hi})")
    count = int(math.floor((hi - lo) / width + 1e-9)) + 1
    if count > MAX_GRID:
        raise ValueError(f"at most {MAX_GRID} thresholds; {count} asked for (raise step)")
    return [round(lo + i * width, 10) for i in range(count)]


def frontier(
    rows: Sequence[Row],
    lo: float = GRID_LO,
    hi: float = GRID_HI,
    step: float = GRID_STEP,
    audit_rate: float | None = None,
    **kw: Any,
) -> list[dict[str, Any]]:
    """`at` for each threshold of `grid(lo, hi, step)`: the cost-precision frontier. Coverage never rises with t."""
    return sweep(rows, grid(lo, hi, step), audit_rate, **kw)


def suggest(rows: Sequence[Row], target: float = DEFAULT_TARGET, audit_rate: float | None = None, **kw: Any) -> dict | None:
    """The lowest threshold of `stats.CERT_GRID` (scanned from high to low, stopping at the first that fails) whose
    interval's lower bound reaches `target`, or None when the highest fails. A fixed-sequence scan over a fixed grid,
    but the interval is approximate, so this is advice, never a certificate and never applied by itself: thresholds
    change only through Night School (ADR 0026, ADR 0028)."""
    target = check_target(target)
    passed: dict[str, Any] | None = None
    for res in sweep(rows, stats.CERT_GRID, audit_rate, **kw):
        lower = res["interval"][0] if res["interval"] is not None else None
        if lower is None or lower < target:
            break
        passed = res
    if passed is None:
        return None
    return {
        "threshold": passed["threshold"],
        "target": target,
        "lower": passed["interval"][0],
        "precision": passed["precision"],
        "coverage": passed["coverage"],
        "savings": passed["savings"],
        "advisory": ADVISORY,
        "method": f"fixed-sequence scan of {list(stats.CERT_GRID)}, {INTERVAL}",
    }


def money_left_on_table(
    rows: Sequence[Row], current: float, suggested: dict | None, audit_rate: float | None = None, **kw: Any
) -> dict[str, Any] | None:
    """What serving at the suggested threshold would have saved over the current one, when it is lower: in savings
    (fraction of the baseline) and in US dollars. None otherwise."""
    if suggested is None or not suggested["threshold"] < current:
        return None
    cur, sug = sweep(rows, [current, suggested["threshold"]], audit_rate, **kw)
    if cur["savings"] is None or sug["savings"] is None:
        return None
    return {
        "threshold": suggested["threshold"],
        "current_threshold": current,
        "savings": sug["savings"] - cur["savings"],
        "usd": cur["cost_usd"] - sug["cost_usd"],
    }


# ---------------------------------------------------------------------------------------------------------------------
# Reports (the endpoints and the CLI)
# ---------------------------------------------------------------------------------------------------------------------
def as_current(t: float | None) -> float | None:
    """The version's threshold now, as the frontier compares with it: above SERVE_NOTHING it serves nothing either, so it
    reads as SERVE_NOTHING; anything that is not a threshold (0, negative, not a number) reads as unknown (None)."""
    x = _number(t)
    if x is None or x <= 0:
        return None
    return min(x, stats.SERVE_NOTHING)


def report(
    history: History,
    threshold: float | None = None,
    audit_rate: float | None = None,
    current: float | None = None,
    fallback_rate: float | None = None,
) -> dict[str, Any]:
    """GET /ratchet/replay: what happened (`actual`, each request at its logged threshold and audit rate), the
    counterfactual at `threshold` (default `current`, the version's threshold now) and `audit_rate`, and the calibrated
    baseline. `fallback_rate` prices audits when the version served nothing (the configured audit rate)."""
    rows = history.rows
    cal = ratio(rows)
    rate = logged_rate(rows)
    kw = {"r": cal["R"], "default_rate": rate if rate is not None else fallback_rate}
    current = as_current(current)
    t = threshold if threshold is not None else current
    return {
        "version": history.version,
        "n": len(rows),
        "excluded": history.n_excluded,
        "excluded_by": dict(history.excluded),
        "current_threshold": current,
        "logged_audit_rate": rate,
        "actual": at(rows, None, None, **kw),
        "counterfactual": at(rows, t, audit_rate, **kw) if t is not None else None,
        "calibrated_baseline": cal,
        "cost_model": "per_token",
    }


def frontier_report(
    history: History,
    current: float | None,
    target: float = DEFAULT_TARGET,
    lo: float = GRID_LO,
    hi: float = GRID_HI,
    step: float = GRID_STEP,
    audit_rate: float | None = None,
    fallback_rate: float | None = None,
) -> dict[str, Any]:
    """GET /ratchet/replay/frontier: `at` over the grid, the suggested threshold (advisory) and the money the current
    threshold leaves on the table."""
    rows = history.rows
    current = as_current(current)
    cal = ratio(rows)
    rate = logged_rate(rows)
    kw = {"r": cal["R"], "default_rate": rate if rate is not None else fallback_rate}
    suggested = suggest(rows, target, audit_rate, **kw)
    money = money_left_on_table(rows, current, suggested, audit_rate, **kw) if current is not None else None
    return {
        "version": history.version,
        "n": len(rows),
        "excluded": history.n_excluded,
        "excluded_by": dict(history.excluded),
        "current_threshold": current,
        "target": check_target(target),
        "rows": frontier(rows, lo, hi, step, audit_rate, **kw),
        "suggested": suggested,
        "money_left_on_table": money,
        "calibrated_baseline": cal,
        "cost_model": "per_token",
    }


def _pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def _usd(x: float | None) -> str:
    return "-" if x is None else f"${x:,.4f}"


def render(rep: dict[str, Any], label: str | None = None) -> str:
    """One version's frontier report as a plain table."""
    cal = rep["calibrated_baseline"]
    ex = rep["excluded_by"]
    cur = rep["current_threshold"]
    head = f"v{rep['version']}: {rep['n']:,} replayed requests; current threshold {'-' if cur is None else f'{cur:g}'}"
    if label:
        head += f"; {label}"
    lines = [
        head,
        f"left out: {ex.get('before_logging', 0):,} logged before the replay's columns, {ex.get('no_student_answer', 0):,} "
        f"without a student answer, {ex.get('pinned_now', 0):,} of tasks pinned now, {ex.get('malformed', 0):,} malformed",
    ]
    if cal["status"] == "ok":
        lo, hi = cal["interval"]
        lines.append(
            f"calibrated baseline: R = {cal['R']:.3f} (95% {lo:.3f} to {hi:.3f}) from {cal['n']:,} audited answers: "
            "what the teacher really cost over the token formula's estimate"
        )
    else:
        lines.append(f"calibrated baseline: insufficient ({cal['n']} audited answers, {cal['min_n']} needed); R = 1")
    lines.append("")
    lines.append(
        f"{'threshold':>9} {'coverage':>9} {'precision':>9} {'95% interval':>17} {'n_eff':>8} {'cost':>11} "
        f"{'saved':>7} {'errors/1k':>9}"
    )
    for row in rep["rows"]:
        iv = row["interval"]
        interval = "-" if iv is None else f"{iv[0] * 100:.1f}-{iv[1] * 100:.1f}%"
        errs = row["served_errors_per_1k"]
        lines.append(
            f"{row['threshold']:>9.2f} {_pct(row['coverage']):>9} {_pct(row['precision']):>9} {interval:>17} "
            f"{row['n_eff']:>8.1f} {_usd(row['cost_usd']):>11} {_pct(row['savings'], 0):>7} "
            f"{'-' if errs is None else f'{errs:.1f}':>9}"
        )
    reasons = sorted({row["precision_reason"] for row in rep["rows"] if row["precision_reason"]})
    for reason in reasons:
        lines.append(f"precision null where: {reason}")
    sug = rep["suggested"]
    lines.append("")
    if sug is None:
        lines.append(f"suggested: none (the highest grid threshold's lower bound is below {rep['target']:g})")
    else:
        lines.append(
            f"suggested: {sug['threshold']:g} (lower bound {sug['lower']:.3f} >= {sug['target']:g}); advisory: {sug['advisory']}"
        )
    money = rep["money_left_on_table"]
    if money is not None:
        lines.append(
            f"money left on the table: {money['savings'] * 100:.1f} points of savings ({_usd(money['usd'])}) at "
            f"{money['threshold']:g} instead of {money['current_threshold']:g}"
        )
    return "\n".join(lines)


def _parse_sweep(text: str) -> tuple[float, float, float]:
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(f"--sweep reads LO:HI:STEP, such as 0.3:0.95:0.05, not {text!r}")
    try:
        lo, hi, step = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"--sweep reads LO:HI:STEP, such as 0.3:0.95:0.05, not {text!r}") from None
    grid(lo, hi, step)  # checks
    return lo, hi, step


def simulated(ledger: Ledger) -> bool:
    """Whether the simulator (or the mock demo) wrote this ledger: it records its synthetic compute with source
    'simulation', which a live ledger never does. The CLI labels such numbers as simulation."""
    return any(iv["source"] == "simulation" for iv in ledger.compute_intervals())


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m ratchet.replay",
        description="Replay a version's logged traffic under another threshold or audit rate (FR-16.5, ADR 0028)",
    )
    ap.add_argument("--db", help="the ledger file (default: RATCHET_DB)")
    ap.add_argument("--version", type=int, help="one version (default: every version with replayable traffic)")
    ap.add_argument("--sweep", default="0.30:1.00:0.05", help="thresholds LO:HI:STEP (default 0.30:1.00:0.05)")
    ap.add_argument("--audit-rate", type=float, help="price audits at this rate (default: as logged)")
    ap.add_argument("--target", type=float, default=DEFAULT_TARGET, help="precision the suggestion needs (default 0.97)")
    ap.add_argument("--since-day", type=int, help="first ledger day")
    ap.add_argument("--until-day", type=int, help="last ledger day")
    ap.add_argument("--json", action="store_true", help="print the reports as JSON")
    return ap


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """The CLI: one table per version, never pooled. Exit 0, 2 on a usage or input error. The ledger file must exist;
    opening an older file migrates it, as the gateway does."""
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    try:
        lo, hi, step = _parse_sweep(args.sweep)
        target = check_target(args.target)
        audit_rate = None if args.audit_rate is None else check_rate(args.audit_rate, "--audit-rate")
        for flag, day in (("--since-day", args.since_day), ("--until-day", args.until_day)):
            if day is not None and day < 0:
                raise ValueError(f"{flag} must be 0 or more, not {day}")
        if args.since_day is not None and args.until_day is not None and args.since_day > args.until_day:
            raise ValueError("--since-day must not be after --until-day")
        from .config import Settings

        settings = Settings()
        path = args.db or settings.db_path
        if path != ":memory:" and not os.path.exists(path):
            raise FileNotFoundError(f"no ledger at {path!r}; pass --db or set RATCHET_DB")
        led = Ledger(path, settings.golden_fraction)
        versions = led.versions()
        if args.version is not None:
            versions = [v for v in versions if v.id == args.version]
            if not versions:
                raise ValueError(f"no version {args.version} in {path}")
        label = SIMULATION if simulated(led) else None
        reports = []
        for v in versions:
            history = load(led, v.id, args.since_day, args.until_day)
            if not history.rows and args.version is None:
                continue
            current = float(v.eval.get("threshold", settings.confidence_threshold))
            rep = frontier_report(history, current, target, lo, hi, step, audit_rate, settings.audit_rate)
            reports.append({**rep, "state": v.state, "label": label})
        led.db.close()
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(reports, indent=2, allow_nan=False), file=out)
    elif not reports:
        print("no replayable traffic: no version's student answered a request logged with the replay's columns", file=out)
    else:
        print("\n\n".join(render(rep, label) for rep in reports), file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
