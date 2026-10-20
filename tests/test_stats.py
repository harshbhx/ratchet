"""ratchet/stats.py, the exact statistics the evidence features build on (ADR 0024): binomial tails against brute force,
Clopper-Pearson bounds and the sample-size planner against hand-computed values, Learn-then-Test, the e-values and
confidence sequences (their supermartingale property exactly, and their error rates by seeded Monte Carlo), and the
estimators (Wilson, Kish, Hajek, the ratio estimator). Each test runs in under a second; the `slow` ones repeat a Monte
Carlo check at full size (pytest --runslow)."""

from __future__ import annotations

import math
import random

import pytest

from ratchet import confidence, release, stats

ORACLE_E = [  # (s, n, p0, side, e-value), computed by hand from the mixture's definition
    (0, 2, 0.92, "below", 29.77),
    (0, 1, 0.92, "below", 4.22),
    (20, 20, 0.92, "below", 0.2006),
    (7, 17, 0.92, "below", 2.59e5),
    (60, 60, 0.92, "above", 27.6),
    (83, 83, 0.92, "above", 129.7),
]


def _brute_sf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(max(k, 0), n + 1))


# ---------- binomial tails and exact bounds ----------
def test_binomial_tails_match_brute_force():
    rng = random.Random(3)
    cases = [(k, n, p) for n in (0, 1, 2, 7, 30) for k in range(-1, n + 2) for p in (0.0, 0.03, 0.5, 0.92, 1.0)]
    cases += [(rng.randint(0, 120), 120, rng.random()) for _ in range(60)]
    for k, n, p in cases:
        want = _brute_sf(k, n, p) if k <= n else 0.0
        assert stats.binom_sf(k, n, p) == pytest.approx(want, rel=1e-9, abs=1e-15), (k, n, p)
        assert stats.binom_cdf(k - 1, n, p) == pytest.approx(1 - want, rel=1e-9, abs=1e-12), (k, n, p)
    assert stats.binom_logpmf(-1, 5, 0.5) == stats.binom_logpmf(6, 5, 0.5) == -math.inf
    assert stats.binom_logpmf(0, 5, 0.0) == 0.0 and stats.binom_logpmf(5, 5, 1.0) == 0.0
    assert math.exp(stats.binom_logpmf(3, 10, 0.3)) == pytest.approx(math.comb(10, 3) * 0.3**3 * 0.7**7, rel=1e-12)


def test_binomial_tails_keep_tiny_values_and_refuse_bad_input():
    assert stats.binom_sf(100, 100, 0.5) == pytest.approx(0.5**100, rel=1e-12)  # summed directly, not 1 - (almost 1)
    assert stats.binom_cdf(0, 200, 0.9) == pytest.approx(0.1**200, rel=1e-9)
    assert stats.binom_sf(1, 1000, 1e-9) == pytest.approx(1 - (1 - 1e-9) ** 1000, rel=1e-6)
    for bad in [(1, -1, 0.5), (1, 5, 1.5), (1, 5, float("nan")), (1.5, 5, 0.5), (True, 5, 0.5), (1, 5.0, 0.5)]:
        with pytest.raises(ValueError):
            stats.binom_sf(*bad)


def test_clopper_pearson_bounds():
    assert stats.cp_lower(46, 48, 0.05) == pytest.approx(0.8746, abs=1e-4)
    assert stats.cp_lower(129, 130, 0.05) == pytest.approx(0.9640, abs=1e-4)
    assert stats.cp_lower(127, 128, 0.05) == pytest.approx(0.9635, abs=1e-4)
    for s in (1, 5, 59, 300):
        assert stats.cp_lower(s, s, 0.05) == pytest.approx(0.05 ** (1 / s), rel=1e-15)
    assert stats.cp_lower(0, 10, 0.05) == stats.cp_lower(0, 0, 0.05) == 0.0
    assert stats.cp_upper(0, 0, 0.05) == 1.0
    rng = random.Random(7)
    for _ in range(50):
        n = rng.randint(1, 400)
        s = rng.randint(1, n)
        d = rng.choice([0.01, 0.05, 0.1])
        lo = stats.cp_lower(s, n, d)
        assert lo <= s / n
        if s < n:  # the bound is where P(X >= s) = delta, from below
            assert stats.binom_sf(s, n, lo) == pytest.approx(d, abs=1e-9)
            assert stats.binom_sf(s, n, lo) <= d + 1e-15
        assert stats.cp_upper(n - s, n, d) == pytest.approx(1 - lo, abs=1e-15)
    with pytest.raises(ValueError):
        stats.cp_lower(5, 4, 0.05)
    with pytest.raises(ValueError):
        stats.cp_lower(3, 4, 1.0)


def test_precision_pvalue_planner_and_mcnemar():
    assert stats.required_rows(0.95, 0.05) == [59, 93, 124, 153]
    assert stats.required_rows(0.97, 0.05) == [99, 157, 208, 257]
    assert stats.required_rows(0.90, 0.05) == [29, 46, 61, 76]
    assert stats.required_rows(0.95, 0.05, max_errors=0) == [59]
    p = [stats.precision_pvalue(s, 100, 0.9) for s in range(101)]
    assert all(a >= b for a, b in zip(p, p[1:], strict=False)) and p[0] == 1.0  # more agreement, stronger evidence
    assert stats.precision_pvalue(0, 0, 0.95) == 1.0
    assert stats.precision_pvalue(59, 59, 0.95) <= 0.05 < stats.precision_pvalue(58, 58, 0.95)
    assert stats.mcnemar_one_sided(4, 0) == 0.0625
    assert stats.mcnemar_one_sided(0, 0) == stats.mcnemar_one_sided(0, 4) == 1.0
    assert stats.mcnemar_one_sided(10, 2) == pytest.approx(_brute_sf(10, 12, 0.5), rel=1e-12)
    with pytest.raises(ValueError):
        stats.required_rows(0.999999, 0.05, limit=1000)


def test_ltt_fixed_sequence_stops_at_the_first_failure():
    assert stats.ltt_fixed_sequence([(0.99, True)] * 10)[0] == stats.SERVE_NOTHING  # 0.95^10 = 0.6 > 0.05
    assert stats.SERVE_NOTHING == confidence.SERVE_NOTHING
    # 80 correct answers at 0.97, then 40 wrong at 0.72: 0.95 to 0.75 serve the same 80 (certified), 0.70 serves all
    # 120 (precision 2/3: not), so the sequence stops there even though lower thresholds would serve the same.
    points = [(0.97, True)] * 80 + [(0.72, False)] * 40
    lam, trace = stats.ltt_fixed_sequence(points, stats.CERT_GRID, target=0.95, delta=0.05)
    assert lam == 0.75
    assert [t["threshold"] for t in trace] == [0.95, 0.90, 0.85, 0.80, 0.75, 0.70]
    assert trace[0] == {"threshold": 0.95, "n": 80, "k": 80, "p_value": pytest.approx(0.95**80)}
    assert trace[-1]["n"] == 120 and trace[-1]["p_value"] > 0.05
    assert stats.ltt_fixed_sequence(points, list(reversed(stats.CERT_GRID)))[0] == lam  # tested highest first anyway
    assert stats.ltt_fixed_sequence([], stats.CERT_GRID)[0] == stats.SERVE_NOTHING
    for grid in ([], [0.0], [1.5]):
        with pytest.raises(ValueError):
            stats.ltt_fixed_sequence(points, grid)
    with pytest.raises(ValueError):
        stats.ltt_fixed_sequence([(float("nan"), True)])


def test_ltt_controls_the_error_rate():
    """Confidences U(0,1) and P(correct | c) = c, so precision at threshold lam is (1 + lam) / 2: below the 0.80 target
    only for 0.55 and 0.50. With 400 calibration rows, the chosen threshold may be one of those in at most delta of
    the draws (plus 3 Monte Carlo standard errors)."""
    rng = random.Random(11)
    runs, bad = 200, 0
    for _ in range(runs):
        points = []
        for _ in range(400):
            c = rng.random()
            points.append((c, rng.random() < c))
        lam, _ = stats.ltt_fixed_sequence(points, stats.CERT_GRID, target=0.80, delta=0.05)
        bad += lam < stats.SERVE_NOTHING and (1 + lam) / 2 < 0.80
    rate = bad / runs
    assert rate <= 0.05 + 3 * math.sqrt(0.05 * 0.95 / runs)


# ---------- e-values and confidence sequences ----------
@pytest.mark.req("FR-16.1")
def test_e_value_oracles_and_confidence_sequence_oracles():
    for s, n, p0, side, want in ORACLE_E:
        assert stats.e_bernoulli(s, n, p0, side) == pytest.approx(want, rel=0.01), (s, n, side)
    assert stats.confidence_sequence(0, 2) == pytest.approx((0.001, 0.931), abs=0.002)
    assert stats.confidence_sequence(35, 38) == pytest.approx((0.710, 0.992), abs=0.002)


@pytest.mark.req("FR-16.1", "FR-16.2")
def test_alt_grid_components_are_supermartingales():
    """One trial with true rate q multiplies component j's likelihood ratio by a factor whose mean is
    q q_j / p0 + (1 - q)(1 - q_j) / (1 - p0); under H0 (q >= p0 for 'below', q <= p0 for 'above') it is at most 1."""
    for p0 in (0.5, 0.8, 0.92, 0.97):
        for side in stats.SIDES:
            grid = stats.alt_grid(p0, side)
            assert math.fsum(w for _, w in grid) == pytest.approx(1.0, abs=1e-12)
            assert all(0 < q < 1 and (q < p0 if side == "below" else q > p0) for q, _ in grid)
            qs = [p0 + 0.01 * i for i in range(100) if p0 + 0.01 * i < 1] if side == "below" else []
            qs = qs or [p0 - 0.01 * i for i in range(100) if p0 - 0.01 * i > 0]
            for q in qs + ([0.999] if side == "below" else [0.001]):
                for qj, _ in grid:
                    assert q * qj / p0 + (1 - q) * (1 - qj) / (1 - p0) <= 1 + 1e-12, (p0, side, q, qj)
    assert math.fsum(w for _, w in stats.alt_grid(0.005, "below")) == pytest.approx(1.0)  # no near alternatives
    assert math.fsum(w for _, w in stats.alt_grid(0.995, "above")) == pytest.approx(1.0)


def test_e_values_edges_and_input_checks():
    assert stats.log_e_bernoulli(0, 0, 0.92, "below") == 0.0 and stats.e_bernoulli(0, 0, 0.92, "above") == 1.0
    assert stats.e_bernoulli(0, 10_000, 0.92, "below") == math.exp(stats.LOG_E_CAP)  # capped, still a float
    assert math.isfinite(stats.e_bernoulli(10_000, 10_000, 0.92, "below"))
    assert stats.e_bernoulli(20, 20, 0.92, "below") < 1 < stats.e_bernoulli(20, 20, 0.92, "above")
    for bad in [(0, 1, 0.0, "below"), (0, 1, 1.0, "above"), (0, 1, 0.5, "sideways"), (2, 1, 0.5, "below"), (0, 0, 2, "above")]:
        with pytest.raises(ValueError):
            stats.log_e_bernoulli(*bad)
    assert stats.confidence_sequence(0, 0) == (0.0, 1.0)
    lo, hi = stats.confidence_sequence(50, 100)
    assert lo < 0.5 < hi and 0.001 <= lo and hi <= 0.999
    lo2, hi2 = stats.confidence_sequence(500, 1000)
    assert lo < lo2 < 0.5 < hi2 < hi  # narrower with more data
    with pytest.raises(ValueError):
        stats.confidence_sequence(1, 2, step=0.0)
    # Results are cached by checked counts (the gate's events and /ratchet/stats ask again until a new audit lands): the
    # cache returns what a scan returns, and input is still checked first.
    assert stats.confidence_sequence(1, 2) == stats._confidence_sequence.__wrapped__(1, 2, 0.05, 0.001)
    for bad in [(True, 2), ([1], 2), (1, 2.0), (3, 2)]:
        with pytest.raises(ValueError):
            stats.confidence_sequence(*bad)


def _components(p0: float, side: str) -> list[tuple[float, float, float]]:
    return [(math.log(w), math.log(q / p0), math.log((1 - q) / (1 - p0))) for q, w in stats.alt_grid(p0, side)]


def _crossings(rng: random.Random, p: float, runs: int, horizon: int) -> tuple[float, float, float]:
    """Seeded counts-only runs at true rate p, each checked after every trial: the share where the 'below' e-value at
    p0 = p ever reaches 20 (a false rollback at alpha 0.05), where Wilson's upper bound (n >= 20) ever falls below p (the
    old rule's false rollback), and where p ever leaves the 95% confidence sequence (either e-value at p reaches 40).
    Each e-value is kept per component and updated in O(30) per trial, then checked against log_e_bernoulli."""
    below, above = _components(p, "below"), _components(p, "above")
    e_hits = w_hits = cs_hits = 0
    for _ in range(runs):
        lb, la = [c[0] for c in below], [c[0] for c in above]
        s = 0
        e_hit = w_hit = cs_hit = False
        for n in range(1, horizon + 1):
            x = rng.random() < p
            s += x
            lb = [v + (a if x else b) for v, (_, a, b) in zip(lb, below, strict=True)]
            la = [v + (a if x else b) for v, (_, a, b) in zip(la, above, strict=True)]
            log_b, log_a = stats._logsumexp(lb), stats._logsumexp(la)
            e_hit = e_hit or log_b >= math.log(20)
            cs_hit = cs_hit or max(log_b, log_a) >= math.log(40)
            w_hit = w_hit or (n >= 20 and stats.wilson(s, n)[1] < p)
        assert log_b == pytest.approx(stats.log_e_bernoulli(s, horizon, p, "below"), abs=1e-9)
        e_hits, w_hits, cs_hits = e_hits + e_hit, w_hits + w_hit, cs_hits + cs_hit
    return e_hits / runs, w_hits / runs, cs_hits / runs


@pytest.mark.req("FR-16.1", "FR-16.2")
def test_e_process_false_rollback_rate_and_wilson_peeking():
    """At a true rate exactly on the floor (0.92), checked after every audit: the e-process rolls back at most alpha
    (0.05) of good versions; Wilson's upper bound checked the same way does far more than its nominal 2.5%, the reason
    the gate needs an anytime-valid rule (ADR 0024). Counts only, seeded."""
    runs = 120
    e_rate, wilson_rate, _ = _crossings(random.Random(5), 0.92, runs, 200)
    assert e_rate <= 0.05 + 3 * math.sqrt(0.05 * 0.95 / runs)
    assert wilson_rate > 0.10  # a regression guard: peeking at a fixed-sample interval inflates its error


@pytest.mark.req("FR-16.1")
def test_confidence_sequence_covers_the_truth_at_every_step():
    runs = 120
    _, _, cs_miss = _crossings(random.Random(9), 0.95, runs, 200)
    assert cs_miss <= 0.05 + 3 * math.sqrt(0.05 * 0.95 / runs)


@pytest.mark.slow
def test_e_process_and_confidence_sequence_error_rates_full():
    for p, seed in ((0.92, 1), (0.95, 2), (0.80, 3)):
        runs = 300
        e_rate, wilson_rate, cs_miss = _crossings(random.Random(seed), p, runs, 300)
        se = 3 * math.sqrt(0.05 * 0.95 / runs)
        assert e_rate <= 0.05 + se and cs_miss <= 0.05 + se
        assert wilson_rate > 0.10


@pytest.mark.slow
def test_clopper_pearson_coverage_full():
    """P(cp_lower(s, n) <= p) >= 1 - delta for every (n, p) below, over 2,000 seeded draws each."""
    rng = random.Random(4)
    for n, p in ((20, 0.90), (50, 0.95), (100, 0.97), (130, 0.99)):
        runs = 2000
        cover = sum(stats.cp_lower(sum(rng.random() < p for _ in range(n)), n, 0.05) <= p for _ in range(runs)) / runs
        assert cover >= 0.95 - 3 * math.sqrt(0.05 * 0.95 / runs), (n, p, cover)


# ---------- estimators ----------
def test_wilson_lives_in_stats_and_release_still_exports_it():
    assert release.wilson is stats.wilson
    lo, hi = stats.wilson(18, 20)
    assert lo == pytest.approx(0.6990, abs=1e-4) and hi == pytest.approx(0.9721, abs=1e-4)
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_kish_and_hajek():
    assert stats.kish_n_eff([1.0] * 7) == pytest.approx(7)
    assert stats.kish_n_eff([1.0, 0.0]) == 1.0 and stats.kish_n_eff([]) == 0.0
    assert stats.kish_n_eff([1, 10]) == pytest.approx(121 / 101)
    # Two labelled rows seen with probability 1 and one with probability 0.1 (weight 10): (1 + 0 + 10) / 12.
    assert stats.hajek_mean([1, 0, 1], [1, 1, 10]) == pytest.approx(11 / 12)
    assert stats.hajek_mean([], []) is None and stats.hajek_mean([1], [0]) is None
    with pytest.raises(ValueError):
        stats.hajek_mean([1, 2], [1])
    with pytest.raises(ValueError):
        stats.kish_n_eff([1, -1])


def test_ratio_estimate():
    rng = random.Random(13)
    pairs = []
    for _ in range(400):
        x = rng.uniform(100, 400)
        pairs.append((x, 1.3 * x * (1 + rng.gauss(0, 0.1))))
    r = stats.ratio_estimate(pairs)
    assert r["status"] == "ok" and r["n"] == 400 and r["ratio"] == pytest.approx(1.3, abs=0.02)
    xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
    ratio = sum(ys) / sum(xs)
    textbook = math.sqrt(sum((y - ratio * x) ** 2 for x, y in pairs) / (400 * 399)) / (sum(xs) / 400)
    assert r["ratio"] == pytest.approx(ratio, rel=1e-12) and r["se"] == pytest.approx(textbook, rel=1e-12)
    assert stats.ratio_estimate(pairs, [2.5] * 400)["se"] == pytest.approx(textbook, rel=1e-9)  # equal weights: same
    weighted = stats.ratio_estimate([(1, 1), (1, 3)], [3, 1])
    assert weighted["ratio"] == pytest.approx(6 / 4) and weighted["n_eff"] == pytest.approx(16 / 10)
    for few in ([], [(1.0, 2.0)], [(0.0, 1.0), (0.0, 2.0)]):
        out = stats.ratio_estimate(few)
        assert out["status"] == "insufficient" and out["ratio"] == 1.0 and out["se"] is None
    assert stats.ratio_estimate(pairs[:20], min_n=30)["status"] == "insufficient"
    with pytest.raises(ValueError):
        stats.ratio_estimate([(1.0, float("inf")), (1.0, 1.0)])


def test_ratio_estimate_interval_covers_the_truth():
    """A 95% interval R +/- 1.96 se, from 150 audited rows of a population with y = 1.3 x (1 + noise), covers 1.3 in
    at least 90% of 200 seeded trials (a normal approximation, so the check leaves room)."""
    rng = random.Random(17)
    cover = 0
    for _ in range(200):
        pairs = [(x, 1.3 * x * (1 + rng.gauss(0, 0.1))) for x in (rng.uniform(100, 400) for _ in range(150))]
        r = stats.ratio_estimate(pairs)
        cover += abs(r["ratio"] - 1.3) <= 1.96 * r["se"]
    assert cover / 200 >= 0.90
