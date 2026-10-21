"""Exact, standard-library statistics for the evidence features (ADR 0024).

Release decisions, certificates, replays and receipts all take their numbers from here, so each function is small,
exact where an exact answer exists, and checked against hand-computed values and Monte Carlo runs (tests/test_stats.py).
Binomial tails are summed in log space from one `lgamma` term and a multiplicative recurrence, so they stay accurate for
tiny p-values and cost O(n) multiplications, not O(n) `lgamma` calls; e-values are log-sum-exp mixtures. Nothing here
returns NaN, and only `binom_logpmf` and `log_e_bernoulli` (logs) can return an infinity; the rest go out as JSON and
into signed BOMs.

- Binomial tails and exact bounds: `binom_logpmf`, `binom_sf`, `binom_cdf`, `cp_lower`, `cp_upper`,
  `precision_pvalue`, `required_rows`, `mcnemar_one_sided`.
- Certified thresholds: `CERT_GRID` and `ltt_fixed_sequence` (Learn-then-Test over a fixed, data-independent grid).
- Anytime-valid evidence about a rate: `alt_grid`, `log_e_bernoulli`, `e_bernoulli`, `confidence_sequence`.
- Estimation: `wilson`, `kish_n_eff`, `hajek_mean`, `ratio_estimate`.

The command line (`main`) puts the certificate arithmetic in an operator's hands (ADR 0026):

    python -m ratchet.stats plan --target 0.95 --delta 0.05     # golden rows needed: 0 errors: 59 rows, 1: 93, ...
    python -m ratchet.stats bound --successes 127 --n 128       # one-sided Clopper-Pearson lower bound: 0.9635
    python -m ratchet.stats mcnemar --b 4 --c 0                 # exact one-sided McNemar p-value: 0.0625
    python -m ratchet.stats check bom.json --public-key K       # verify a BOM and recompute its certificate

`check` imports `ratchet.bom` and `ratchet.certificate` when it runs, so importing this module still pulls in nothing
but the standard library.
"""

from __future__ import annotations

import argparse
import bisect
import functools
import json
import math
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, TextIO

# The threshold that serves nothing (`confidence.SERVE_NOTHING`): above every confidence, and finite for JSON. Not
# imported, so this module stays free of the package's other imports (sim/ uses it alone).
SERVE_NOTHING = 1.01
# The thresholds a certificate may choose from, highest first. Fixed in advance: a grid built from the observed
# confidences would make the fixed-sequence test stop at its first one-point group and certify nothing.
CERT_GRID: tuple[float, ...] = (0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50)
# e-values are reported as exp(log e) capped here, so a strong signal stays a finite float.
LOG_E_CAP = 690.0
SIDES = ("below", "above")


def _check_n(n: object, what: str = "n") -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(f"{what} must be a whole number, 0 or more, not {n!r}")
    return n


def _check_counts(k: object, n: object, what: str = "successes") -> tuple[int, int]:
    n = _check_n(n)
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k <= n:
        raise ValueError(f"{what} must be a whole number from 0 to n={n}, not {k!r}")
    return k, n


def _check_prob(p: object, what: str = "p", open_interval: bool = False) -> float:
    ok = not isinstance(p, bool) and isinstance(p, (int, float)) and math.isfinite(p)
    if ok:
        ok = 0 < p < 1 if open_interval else 0 <= p <= 1
    if not ok:
        bounds = "strictly between 0 and 1" if open_interval else "from 0 to 1"
        raise ValueError(f"{what} must be a number {bounds}, not {p!r}")
    return float(p)


def _logsumexp(values: Iterable[float]) -> float:
    vs = list(values)
    m = max(vs, default=-math.inf)
    if m == -math.inf:
        return -math.inf
    if m == math.inf:
        return math.inf
    return m + math.log(sum(math.exp(v - m) for v in vs))


# ---------------------------------------------------------------------------------------------------------------------
# Binomial tails and exact bounds
# ---------------------------------------------------------------------------------------------------------------------
def binom_logpmf(k: int, n: int, p: float) -> float:
    """log P(X = k) for X ~ Binomial(n, p); -inf for a k outside 0..n or a probability of exactly 0."""
    n = _check_n(n)
    p = _check_prob(p)
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValueError(f"k must be a whole number, not {k!r}")
    if k < 0 or k > n:
        return -math.inf
    if p == 0.0:
        return 0.0 if k == 0 else -math.inf
    if p == 1.0:
        return 0.0 if k == n else -math.inf
    log_choose = math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
    return log_choose + k * math.log(p) + (n - k) * math.log1p(-p)


def _tail(start: int, step: int, n: int, p: float) -> float:
    """P(X = start) + P(X = start + step) + ... within 0..n, for 0 < p < 1 and step +1 or -1. Callers start on the far
    side of the mode, so the terms never increase: they are summed relative to the first (one `lgamma`, then the
    ratio of neighbouring terms) and the sum stops once a term is negligible and shrinking fast."""
    first = binom_logpmf(start, n, p)
    odds = p / (1.0 - p)
    total = term = 1.0
    i = start
    while (i < n) if step > 0 else (i > 0):
        ratio = (n - i) / (i + 1) * odds if step > 0 else i / (n - i + 1) / odds
        term *= ratio
        i += step
        total += term
        if ratio < 0.5 and term < total * 1e-17:
            break
    return math.exp(first + math.log(total))


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), exact: 1.0 for k <= 0, 0.0 for k > n. The smaller tail is summed directly,
    so a small result keeps its relative accuracy."""
    n = _check_n(n)
    p = _check_prob(p)
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValueError(f"k must be a whole number, not {k!r}")
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    if k > n * p:
        return min(1.0, _tail(k, 1, n, p))
    return min(1.0, max(0.0, 1.0 - _tail(k - 1, -1, n, p)))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), exact: 0.0 for k < 0, 1.0 for k >= n."""
    n = _check_n(n)
    p = _check_prob(p)
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValueError(f"k must be a whole number, not {k!r}")
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0
    if k < n * p:
        return min(1.0, _tail(k, -1, n, p))
    return min(1.0, max(0.0, 1.0 - _tail(k + 1, 1, n, p)))


def cp_lower(s: int, n: int, delta: float) -> float:
    """One-sided (1 - delta) Clopper-Pearson lower bound on a rate from `s` successes in `n` trials: the p at which
    P(X >= s) = delta, found by bisection and rounded down, so the bound is never above the exact one. 0.0 for no
    successes (or no trials), and delta ** (1 / n) when every trial succeeded."""
    s, n = _check_counts(s, n)
    delta = _check_prob(delta, "delta", open_interval=True)
    if n == 0 or s == 0:
        return 0.0
    if s == n:
        return delta ** (1.0 / n)
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if mid <= lo or mid >= hi:  # the interval is down to adjacent floats
            break
        if binom_sf(s, n, mid) < delta:
            lo = mid
        else:
            hi = mid
    return lo


def cp_upper(s: int, n: int, delta: float) -> float:
    """One-sided (1 - delta) Clopper-Pearson upper bound: 1 - cp_lower(n - s, n, delta); 1.0 with no trials."""
    s, n = _check_counts(s, n)
    return 1.0 - cp_lower(n - s, n, delta)


def precision_pvalue(s: int, n: int, target: float) -> float:
    """Exact one-sided p-value for H0 'precision <= target' after `s` of `n` served answers agreed: P(X >= s) with
    X ~ Binomial(n, target). Small means strong evidence that precision is above the target; 1.0 with no answers."""
    s, n = _check_counts(s, n)
    target = _check_prob(target, "target", open_interval=True)
    return 1.0 if n == 0 else binom_sf(s, n, target)


def required_rows(target: float, delta: float, max_errors: int = 3, limit: int = 1_000_000) -> list[int]:
    """For 0, 1, ..., `max_errors` wrong answers: the fewest served rows that certify precision above `target` at
    level `delta` (the smallest n with precision_pvalue(n - e, n, target) <= delta). The p-value falls as n grows with e
    fixed, so this is a doubling search, then a bisection. ValueError when more than `limit` rows would be needed."""
    target = _check_prob(target, "target", open_interval=True)
    delta = _check_prob(delta, "delta", open_interval=True)
    _check_n(max_errors, "max_errors")
    out = []
    for e in range(max_errors + 1):

        def certifies(n: int, e: int = e) -> bool:
            return precision_pvalue(n - e, n, target) <= delta

        hi = e + 1
        while not certifies(hi):
            if hi > limit:
                raise ValueError(f"more than {limit} rows needed to certify {target} with {e} errors at delta {delta}")
            hi *= 2
        lo = max(e + 1, hi // 2)  # certifies(lo - 1) is False, or lo is the smallest n that can hold e errors
        while lo < hi:
            mid = (lo + hi) // 2
            if certifies(mid):
                hi = mid
            else:
                lo = mid + 1
        out.append(lo)
    return out


def mcnemar_one_sided(b: int, c: int) -> float:
    """Exact one-sided McNemar p-value for 'the b side is larger': P(X >= b) with X ~ Binomial(b + c, 1/2), over the
    discordant pairs only (b: the first model right or serving and the second not; c: the reverse). 1.0 with none."""
    _check_n(b, "b")
    _check_n(c, "c")
    return 1.0 if b + c == 0 else binom_sf(b, b + c, 0.5)


def ltt_fixed_sequence(
    points: Iterable[tuple[float, bool]],
    grid: Sequence[float] = CERT_GRID,
    target: float = 0.95,
    delta: float = 0.05,
) -> tuple[float, list[dict[str, Any]]]:
    """Learn-then-Test threshold by fixed-sequence testing: for each threshold in `grid` from highest to lowest, test
    H0 'precision of the answers at or above it <= target' with `precision_pvalue` at level `delta`, keep going while
    it is rejected and stop at the first that is not. Returns the lowest rejected threshold (SERVE_NOTHING when the
    first already fails) and the trace, one {threshold, n, k, p_value} per threshold tested.

    Testing a fixed order of hypotheses and stopping at the first non-rejection controls the family-wise error at
    `delta` with no correction, so every kept threshold has precision above `target` with probability >= 1 - delta.
    `points` are (confidence, correct) pairs from calibration rows only; the grid must not be built from them."""
    target = _check_prob(target, "target", open_interval=True)
    delta = _check_prob(delta, "delta", open_interval=True)
    lams = sorted({float(g) for g in grid}, reverse=True)
    if not lams or any(not (0 < g <= SERVE_NOTHING) for g in lams):
        raise ValueError(f"grid must hold thresholds in (0, {SERVE_NOTHING}], not {list(grid)!r}")
    pts = sorted((float(c), bool(ok)) for c, ok in points)
    if any(not math.isfinite(c) for c, _ in pts):
        raise ValueError("every point's confidence must be a finite number")
    confs = [c for c, _ in pts]
    agree_after = [0] * (len(pts) + 1)  # agree_after[i]: correct answers among pts[i:]
    for i in range(len(pts) - 1, -1, -1):
        agree_after[i] = agree_after[i + 1] + pts[i][1]
    threshold, trace = SERVE_NOTHING, []
    for lam in lams:
        first = bisect.bisect_left(confs, lam)
        n, k = len(pts) - first, agree_after[first]
        p = precision_pvalue(k, n, target)
        trace.append({"threshold": lam, "n": n, "k": k, "p_value": p})
        if p > delta:
            break
        threshold = lam
    return threshold, trace


# ---------------------------------------------------------------------------------------------------------------------
# Anytime-valid evidence about a rate (e-values and confidence sequences)
# ---------------------------------------------------------------------------------------------------------------------
def alt_grid(p0: float, side: str) -> list[tuple[float, float]]:
    """The alternatives (q, weight) the e-value mixes over, for H0 'rate >= p0' (`side` 'below': evidence the rate is
    below p0) or H0 'rate <= p0' ('above'). Half the weight sits on near alternatives 0.01 to 0.10 away from p0, for
    power against small shifts; half on 20 alternatives spread evenly over the rest of the side. Weights sum to 1 (all
    on the far ones when p0 is within 0.01 of the edge)."""
    p0 = _check_prob(p0, "p0", open_interval=True)
    if side not in SIDES:
        raise ValueError(f"side must be 'below' or 'above', not {side!r}")
    if side == "below":
        near = [p0 - 0.01 * k for k in range(1, 11) if p0 - 0.01 * k > 0]
        far = [p0 * (j - 0.5) / 20 for j in range(1, 21)]
    else:
        near = [p0 + 0.01 * k for k in range(1, 11) if p0 + 0.01 * k < 1]
        far = [p0 + (1 - p0) * (j - 0.5) / 20 for j in range(1, 21)]
    far_weight = 0.5 / len(far) if near else 1.0 / len(far)
    return [(q, 0.5 / len(near)) for q in near] + [(q, far_weight) for q in far]


@functools.lru_cache(maxsize=4096)
def _components(p0: float, side: str) -> tuple[tuple[float, float, float], ...]:
    """Per alternative: (log weight, log(q / p0), log((1 - q) / (1 - p0))), the terms of one audit's likelihood ratio."""
    return tuple((math.log(w), math.log(q / p0), math.log((1 - q) / (1 - p0))) for q, w in alt_grid(p0, side))


def log_e_bernoulli(s: int, n: int, p0: float, side: str) -> float:
    """log of the mixture e-value after `s` successes in `n` trials, against H0 'rate >= p0' (`side` 'below') or
    'rate <= p0' ('above'): log sum_j w_j (q_j / p0)^s ((1 - q_j) / (1 - p0))^(n - s) over `alt_grid`. 0.0 with no
    trials. It depends on the counts only, not their order, so it can be recomputed from two integers at any time.

    Validity (side 'below'; 'above' is the mirror image). Let the trials arrive one at a time, each succeeding with a
    conditional probability q_t >= p0 given the past (no independence or identical distribution needed). One trial
    multiplies alternative j's likelihood ratio by a factor with conditional mean
    q_t q_j / p0 + (1 - q_t)(1 - q_j)/(1 - p0) = 1 + (q_t - p0)(q_j / p0 - (1 - q_j)/(1 - p0)) <= 1, because q_j < p0
    makes the second bracket negative. So each component, and any fixed mixture of them, is a nonnegative
    supermartingale starting at 1, and Ville's inequality gives P(the e-value ever reaches 1/alpha) <= alpha, however
    often it is looked at."""
    s, n = _check_counts(s, n)
    if n == 0:
        _check_prob(p0, "p0", open_interval=True)
        if side not in SIDES:
            raise ValueError(f"side must be 'below' or 'above', not {side!r}")
        return 0.0
    return _logsumexp(lw + s * a + (n - s) * b for lw, a, b in _components(float(p0), side))


def e_bernoulli(s: int, n: int, p0: float, side: str) -> float:
    """The mixture e-value itself: exp(`log_e_bernoulli`), capped at exp(690) so it stays a finite float."""
    return math.exp(min(log_e_bernoulli(s, n, p0, side), LOG_E_CAP))


def confidence_sequence(s: int, n: int, alpha: float = 0.05, step: float = 0.001) -> tuple[float, float]:
    """An anytime-valid (1 - alpha) confidence sequence for a rate after `s` successes in `n` trials: the smallest and
    largest p on a grid of `step` (0.001 by default) where neither one-sided e-value (`log_e_bernoulli` 'below' and
    'above' at p) reaches 2 / alpha. With probability >= 1 - alpha the true rate stays inside at every n at once
    (Ville's inequality on each side, alpha/2 each). (0.0, 1.0) with no trials; if no grid point is kept, which happens
    with probability at most alpha, the point estimate as a zero-width interval. Computed on the grid, so the ends are
    multiples of `step`."""
    s, n = _check_counts(s, n)
    alpha = _check_prob(alpha, "alpha", open_interval=True)
    if isinstance(step, bool) or not isinstance(step, (int, float)) or not 0 < step < 0.5:
        raise ValueError(f"step must be a number strictly between 0 and 0.5, not {step!r}")
    return _confidence_sequence(s, n, alpha, float(step))


@functools.lru_cache(maxsize=4096)
def _confidence_sequence(s: int, n: int, alpha: float, step: float) -> tuple[float, float]:
    """`confidence_sequence` on checked arguments. A scan of the grid costs about 20 ms, and the release gate's events
    and every `/ratchet/stats` call ask again for the same counts until a new audit lands, so results are cached."""
    if n == 0:
        return 0.0, 1.0
    threshold = math.log(2 / alpha)
    steps = int(round(1 / step))
    kept = [
        p
        for p in (round(i * step, 12) for i in range(1, steps))
        if 0 < p < 1 and log_e_bernoulli(s, n, p, "below") < threshold and log_e_bernoulli(s, n, p, "above") < threshold
    ]
    if not kept:
        p_hat = round(min(max(s / n, step), 1 - step), 12)
        return p_hat, p_hat
    return kept[0], kept[-1]


# ---------------------------------------------------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------------------------------------------------
def wilson(successes: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion (two-sided at z = 1.96). (0.0, 1.0) with no trials. A fixed-sample
    interval: looking at it after every new trial is not covered by its level (ADR 0005, ADR 0024). `n` may be an
    effective sample size (Kish's, for weighted data: the ledger replay), which makes the interval approximate."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _weights(weights: Iterable[float], what: str = "weights") -> list[float]:
    out = []
    for w in weights:
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w < 0:
            raise ValueError(f"{what} must be finite numbers, 0 or more, not {w!r}")
        out.append(float(w))
    return out


def kish_n_eff(weights: Iterable[float]) -> float:
    """Kish's effective sample size (sum w)^2 / sum w^2: how many equally weighted rows a weighted sample is worth.
    0.0 for no weight at all."""
    ws = _weights(weights)
    sq = sum(w * w for w in ws)
    return 0.0 if sq == 0 else sum(ws) ** 2 / sq


def hajek_mean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    """The Hajek (self-normalised inverse-probability) mean sum w y / sum w, with w = 1 / (probability the row was
    observed). None when the weights sum to 0 (nothing observed)."""
    ws = _weights(weights)
    if len(values) != len(ws):
        raise ValueError(f"values and weights differ in length ({len(values)} vs {len(ws)})")
    total = sum(ws)
    if total == 0:
        return None
    return sum(w * float(y) for w, y in zip(ws, values, strict=True)) / total


def ratio_estimate(
    pairs: Sequence[tuple[float, float]], weights: Sequence[float] | None = None, min_n: int = 2
) -> dict[str, Any]:
    """The ratio estimator R = sum w y / sum w x over sampled (x, y) pairs (Cochran, Sampling Techniques, ch. 6), with
    w = 1 / inclusion probability (all 1 when `weights` is None): the factor that turns a known total of x (a formula's
    estimate) into an estimate of the total of y (what was really paid). Returns {ratio, se, n, n_eff, x_mean, s2,
    status}: `s2` is the residual variance (sum w (y - R x)^2 / sum w) * n / (n - 1), `x_mean` the weighted mean of x, and
    `se` = sqrt(s2 / n_eff) / x_mean the linearised standard error of R (a normal approximation; with equal weights,
    sqrt(sum (y - R x)^2 / (n (n - 1))) / mean x). Callers scale it to a total and add a finite population correction
    themselves. With fewer than `min_n` pairs (at least 2), or no x at all, the status is 'insufficient' with ratio 1.0
    (the formula as it is) and se None."""
    min_n = max(2, _check_n(min_n, "min_n"))
    xs, ys = [], []
    for pair in pairs:
        x, y = pair
        for v, what in ((x, "x"), (y, "y")):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"{what} must be a finite number, not {v!r}")
        xs.append(float(x))
        ys.append(float(y))
    n = len(xs)
    ws = [1.0] * n if weights is None else _weights(weights)
    if len(ws) != n:
        raise ValueError(f"pairs and weights differ in length ({n} vs {len(ws)})")
    total_w = sum(ws)
    wx = sum(w * x for w, x in zip(ws, xs, strict=True))
    if n < min_n or total_w == 0 or wx <= 0:
        return {"ratio": 1.0, "se": None, "n": n, "n_eff": kish_n_eff(ws), "x_mean": None, "s2": None, "status": "insufficient"}
    ratio = sum(w * y for w, y in zip(ws, ys, strict=True)) / wx
    s2 = sum(w * (y - ratio * x) ** 2 for w, x, y in zip(ws, xs, ys, strict=True)) / total_w * n / (n - 1)
    x_mean = wx / total_w
    n_eff = kish_n_eff(ws)
    se = math.sqrt(s2 / n_eff) / x_mean
    return {"ratio": ratio, "se": se, "n": n, "n_eff": n_eff, "x_mean": x_mean, "s2": s2, "status": "ok"}


# ---------------------------------------------------------------------------------------------------------------------
# Command line (ADR 0026)
# ---------------------------------------------------------------------------------------------------------------------
def _check_bom(path: str, public_key: str | None, out: TextIO) -> int:
    """Verify a BOM document (sha256, and the signature against `public_key` when given), then recompute its
    certificate, frontier, per-task bounds and paired p-values from the counts it signs. 0 when everything holds, 1 on
    any failure or mismatch beyond 1e-9."""
    from ratchet import bom, certificate  # noqa: PLC0415 - only `check` needs the package (see the module docstring)

    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror or e}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(text)
    except ValueError:
        print("FAIL: not JSON", file=out)
        return 1
    ok, reason = bom.verify(doc, public_key)
    print(("OK: " if ok else "FAIL: ") + reason, file=out)
    m = doc.get("manifest") if isinstance(doc, dict) else None
    ev = m.get("eval") if isinstance(m, dict) else None
    if not isinstance(ev, dict) or ev.get("certificate") is None:
        print("no certificate in this BOM (written before certified precision, or by another evaluator)", file=out)
        return 0 if ok else 1
    checked, problems = certificate.recheck(ev)
    for line in checked:
        print(f"OK: {line}", file=out)
    for line in problems:
        print(f"FAIL: {line}", file=out)
    if problems:
        print(
            f"check: {len(problems)} problem(s): the parts above do not follow from the signed counts or cannot be read", file=out
        )
    elif ok:
        print("check: every number recomputed from the signed counts", file=out)
    return 0 if ok and not problems else 1


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """`python -m ratchet.stats plan|bound|mcnemar|check`. Exit status 0, 1 when `check` finds a failure, 2 on bad
    input (argparse's own errors exit 2 too)."""
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="python -m ratchet.stats", description="Exact statistics behind Ratchet's certificates.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan", help="golden rows needed to certify a precision, for 0 to --max-errors wrong answers")
    plan.add_argument("--target", type=float, default=0.95)
    plan.add_argument("--delta", type=float, default=0.05)
    plan.add_argument("--max-errors", type=int, default=3)
    bnd = sub.add_parser("bound", help="one-sided Clopper-Pearson lower bound on a rate")
    bnd.add_argument("--successes", type=int, required=True)
    bnd.add_argument("--n", type=int, required=True)
    bnd.add_argument("--delta", type=float, default=0.05)
    mcn = sub.add_parser("mcnemar", help="exact one-sided McNemar p-value from the discordant pairs")
    mcn.add_argument("--b", type=int, required=True, help="pairs where the first side wins")
    mcn.add_argument("--c", type=int, required=True, help="pairs where the second side wins")
    chk = sub.add_parser("check", help="verify a BOM and recompute its certificate from the signed counts")
    chk.add_argument("path", help="a BOM document (GET /ratchet/versions/{id}/bom), or - for stdin")
    chk.add_argument("--public-key", default=None, help="the base64 Ed25519 public key you trust")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "plan":
            rows = required_rows(args.target, args.delta, args.max_errors)
            parts = [f"{e} errors: {n} rows" if e == 0 else f"{e}: {n}" for e, n in enumerate(rows)]
            print(", ".join(parts), file=out)
        elif args.cmd == "bound":
            print(f"{cp_lower(args.successes, args.n, args.delta):.4f}", file=out)
        elif args.cmd == "mcnemar":
            print(f"{mcnemar_one_sided(args.b, args.c):.4g}", file=out)
        else:
            return _check_bom(args.path, args.public_key, out)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
