"""Certified precision (FR-16.3, ADR 0026): what an adapter's golden eval can prove about the answers it serves.

`cascade_eval` picks a threshold on the even half of the golden rows and measures on the odd half (INV-6). The odd half
plays no part in choosing the threshold, so the agreement count of the answers it would serve there is one exact
binomial experiment, and a one-sided Clopper-Pearson bound turns it into a certificate: with probability at least
1 - delta, the precision of what the version serves at that threshold is at least `lcb`. Precision means agreement with
the teacher, on requests exchangeable with the golden rows (`ASSUMPTIONS`). From (confidence, correct) points:

- `certificate`: served, errors, precision, `lcb` and `certified` (lcb >= target) at the chosen threshold;
- `frontier`: the same at every threshold of the fixed grid `stats.CERT_GRID`, each bound at delta / len(grid)
  (Bonferroni), so every row holds at once and an operator may pick any of them afterwards;
- `per_task`: the certificate's counts per task: a diagnostic that shows a weak task inside a pooled number (one bound
  per task at delta, not corrected for multiplicity, and never used for routing);
- `outcome_vector` (the eval's `test_vector`): one letter per held-out row in golden order (`S` served and right, `W`
  served and wrong, `E` escalated), the input of `paired`, the exact McNemar comparison of two versions on the same rows;
- `recheck`: every number above recomputed from the counts an eval (or a signed BOM's `manifest.eval`) carries, for
  `python -m ratchet.stats check`.

Standard library and `stats` only, so it can be imported anywhere; everything returned is JSON (finite floats).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .stats import CERT_GRID, SERVE_NOTHING, cp_lower, mcnemar_one_sided, precision_pvalue, wilson

METHOD = "clopper-pearson"
# How a version's threshold is chosen: v0.4's point rule (`confidence.calibrate_threshold`) or Learn-then-Test over the
# fixed grid (`stats.ltt_fixed_sequence`). Either way the certificate is measured on the held-out half.
CALIBRATIONS = ("point", "ltt")
SERVED_OK, SERVED_WRONG, ESCALATED = "S", "W", "E"
OUTCOMES = frozenset({SERVED_OK, SERVED_WRONG, ESCALATED})
# A recomputed number may differ from the stated one by float rounding only.
TOLERANCE = 1e-9
# What a certificate assumes, served with every certificate (GET /ratchet/versions/{id}/certificate).
ASSUMPTIONS = (
    "Golden rows are assumed exchangeable with live traffic, but they are teacher answers to escalations, audits and "
    "arbitrations, so they over-represent requests the student found hard.",
    "Precision means agreement with the teacher model under the task's comparators, not correctness.",
    "Which requests are golden is decided by a hash of the request, which acts like a coin flip: it does not look at how "
    "hard the request is or at any model's answer.",
)

Point = tuple[float, bool]


def counts(points: Iterable[Point], threshold: float) -> tuple[int, int]:
    """(served, errors): the points at or above `threshold`, and how many of those are wrong."""
    served = errors = 0
    for c, ok in points:
        if c >= threshold:
            served += 1
            errors += not ok
    return served, errors


def bound(served: int, errors: int, delta: float) -> dict[str, Any]:
    """{served, errors, precision, lcb}: precision is 0.0 when nothing is served (as the eval's own `precision`), and
    lcb the one-sided (1 - delta) Clopper-Pearson lower bound on it (0.0 when nothing is served)."""
    return {
        "served": served,
        "errors": errors,
        "precision": (served - errors) / served if served else 0.0,
        "lcb": cp_lower(served - errors, served, delta),
    }


def certificate(
    points: Sequence[Point],
    threshold: float,
    target: float,
    delta: float,
    calibration: str = "point",
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The certificate of a version at `threshold`, from the held-out `points` only: `certified` when the lower bound
    reaches `target`. `trace` is Learn-then-Test's (`ltt_trace`), kept only for that calibration."""
    out = {
        "method": METHOD,
        "calibration": calibration,
        "delta": delta,
        "target": target,
        "threshold": threshold,
        **bound(*counts(points, threshold), delta),
    }
    out["certified"] = out["lcb"] >= target
    if trace is not None:
        out["ltt_trace"] = trace
    return out


def frontier(points: Sequence[Point], delta: float, grid: Sequence[float] = CERT_GRID) -> list[dict[str, Any]]:
    """Coverage, counts, precision and a lower bound at every threshold of the fixed `grid`, highest first. Each bound
    is at delta / len(grid), so all of them hold at once with probability >= 1 - delta (Bonferroni); `delta` in each row
    is that corrected level."""
    lams = sorted({float(g) for g in grid}, reverse=True)
    if not lams:
        raise ValueError("the frontier needs at least one threshold")
    each = delta / len(lams)
    n = len(points)
    rows = []
    for lam in lams:
        b = bound(*counts(points, lam), each)
        rows.append({"threshold": lam, "coverage": b["served"] / n if n else 0.0, **b, "delta": each})
    return rows


def per_task(points: Sequence[Point], names: Sequence[str], threshold: float, delta: float) -> dict[str, dict[str, Any]]:
    """{task: {n_test, served, errors, coverage, precision, lcb}} at `threshold`, by task name. Each bound is at `delta`
    on its own: a diagnostic, not corrected for the number of tasks."""
    if len(points) != len(names):
        raise ValueError(f"points and task names differ in length ({len(points)} vs {len(names)})")
    groups: dict[str, list[Point]] = {}
    for point, name in zip(points, names, strict=True):
        groups.setdefault(name, []).append(point)
    out = {}
    for name in sorted(groups):
        pts = groups[name]
        b = bound(*counts(pts, threshold), delta)
        out[name] = {"n_test": len(pts), **b, "coverage": b["served"] / len(pts)}
    return out


def outcome_vector(points: Iterable[Point], threshold: float) -> str:
    """The eval's `test_vector`: one letter per point, in order: `S` served and right, `W` served and wrong, `E`
    escalated."""
    return "".join((SERVED_OK if ok else SERVED_WRONG) if c >= threshold else ESCALATED for c, ok in points)


def _check_vector(vec: object, what: str) -> str:
    if not isinstance(vec, str) or set(vec) - OUTCOMES:
        raise ValueError(f"{what} must be a string of S, W and E, not {vec!r:.40}")
    return vec


def paired(live_vec: str, cand_vec: str) -> dict[str, Any]:
    """The exact paired comparison of the live version (re-scored tonight) and a candidate on the same held-out rows,
    from their test vectors (equal length, same rows in the same order):

    - coverage: `cov_only_live` (b: live serves, the candidate escalates) and `cov_only_cand` (c: the reverse), with
      `p_cov` = McNemar's one-sided p-value for 'the candidate serves less';
    - errors, as a client sees them (an escalated row gets the teacher's answer, which is right by definition):
      `neg_flips` (the candidate serves a wrong answer where live was right: served right or escalated) and `pos_flips`
      (live served a wrong answer and the candidate does not), with `p_err` = McNemar's one-sided p-value for 'the
      candidate serves more wrong answers'; (neg_flips - pos_flips) / n is the change in wrong answers per request;
    - the negative flip rate: `neg_flips_served` (live served a right answer, the candidate serves a wrong one) over
      `live_served_ok`, with its 95% Wilson interval `nfr_95`.

    ValueError when the vectors differ in length or hold anything but S, W and E."""
    live, cand = _check_vector(live_vec, "live_vec"), _check_vector(cand_vec, "cand_vec")
    if len(live) != len(cand):
        raise ValueError(f"the test vectors differ in length ({len(live)} vs {len(cand)}): not the same rows")
    b = c = nf = pf = nf_served = live_ok = 0
    for x, y in zip(live, cand, strict=True):
        b += x != ESCALATED and y == ESCALATED
        c += x == ESCALATED and y != ESCALATED
        nf += x != SERVED_WRONG and y == SERVED_WRONG
        pf += x == SERVED_WRONG and y != SERVED_WRONG
        nf_served += x == SERVED_OK and y == SERVED_WRONG
        live_ok += x == SERVED_OK
    return paired_stats(len(live), b, c, nf, pf, nf_served, live_ok)


def paired_stats(n: int, b: int, c: int, nf: int, pf: int, nf_served: int, live_ok: int) -> dict[str, Any]:
    """`paired`'s result from its counts (the p-values, the rate and its interval are functions of them only)."""
    lo, hi = wilson(nf_served, live_ok)
    return {
        "n": n,
        "cov_only_live": b,
        "cov_only_cand": c,
        "p_cov": mcnemar_one_sided(b, c),
        "neg_flips": nf,
        "pos_flips": pf,
        "p_err": mcnemar_one_sided(nf, pf),
        "neg_flips_served": nf_served,
        "live_served_ok": live_ok,
        "nfr": nf_served / live_ok if live_ok else 0.0,
        "nfr_95": [lo, hi],
    }


def section(ev: object) -> dict[str, Any] | None:
    """The certificate part of an eval (a version's, or a BOM's `manifest.eval`): its certificate, frontier, per-task
    breakdown, threshold, calibration and paired comparison, each None when the eval lacks it. None for no eval."""
    if not isinstance(ev, Mapping) or not ev:
        return None
    cert = ev.get("certificate") if isinstance(ev.get("certificate"), Mapping) else None
    return {
        "certificate": cert,
        "frontier": ev.get("frontier"),
        "per_task": ev.get("per_task"),
        "threshold": ev.get("threshold"),
        "calibration": cert.get("calibration") if cert else None,
        "paired": ev.get("paired"),
    }


def status(current: Mapping[str, Any] | None, training: Mapping[str, Any] | None, drift: Mapping[str, Any] | None) -> str:
    """'certified', 'not certified', 'suspended' (a drift alarm is open, FR-16.6) or 'no certificate'. The latest
    certificate decides: the nightly re-score's (`current`) when there is one, else the one signed at training."""
    cert = None
    for sec in (current, training):
        if sec and isinstance(sec.get("certificate"), Mapping):
            cert = sec["certificate"]
            break
    if cert is None:
        return "no certificate"
    if drift and drift.get("alarm"):
        return "suspended"
    return "certified" if cert.get("certified") is True else "not certified"


# ---------------------------------------------------------------------------------------------------------------------
# Recomputing a certificate from its counts
# ---------------------------------------------------------------------------------------------------------------------
class _Malformed(ValueError):
    pass


def _int(obj: Mapping[str, Any], key: str, where: str) -> int:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise _Malformed(f"{where}.{key} must be a whole number, 0 or more, not {v!r}")
    return v


def _float(obj: Mapping[str, Any], key: str, where: str) -> float:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _Malformed(f"{where}.{key} must be a finite number, not {v!r}")
    return float(v)


def _level(obj: Mapping[str, Any], key: str, where: str) -> float:
    v = _float(obj, key, where)
    if not 0 < v < 1:
        raise _Malformed(f"{where}.{key} must be strictly between 0 and 1, not {v!r}")
    return v


def _counts_of(obj: Mapping[str, Any], where: str) -> tuple[int, int]:
    served, errors = _int(obj, "served", where), _int(obj, "errors", where)
    if errors > served:
        raise _Malformed(f"{where}: {errors} errors among {served} served answers")
    return served, errors


def _compare(out: list[str], where: str, stated: Mapping[str, Any], want: Mapping[str, Any]) -> None:
    """Append a line to `out` for every key of `want` whose stated value differs (floats beyond TOLERANCE)."""
    for key, value in want.items():
        have = stated.get(key)
        if isinstance(value, bool) or isinstance(value, (str, int)) and not isinstance(value, float):
            same = have == value and type(have) is type(value)
        elif isinstance(value, float):
            same = not isinstance(have, bool) and isinstance(have, (int, float)) and abs(float(have) - value) <= TOLERANCE
        else:  # a list of floats (an interval)
            same = (
                isinstance(have, list)
                and len(have) == len(value)
                and all(
                    not isinstance(h, bool) and isinstance(h, (int, float)) and abs(float(h) - v) <= TOLERANCE
                    for h, v in zip(have, value, strict=True)
                )
            )
        if not same:
            out.append(f"{where}.{key}: stated {have!r}, recomputed {value!r}")


def recheck(ev: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Recompute every number of an eval's certificate, frontier, per-task breakdown and paired comparison from the
    counts it states, and check the counts against each other and against its test vector. Returns (checked, problems):
    a line per part that was checked, and a line per mismatch (beyond TOLERANCE for floats) or malformed part. An eval
    without a certificate has nothing to check: ([], [])."""
    checked: list[str] = []
    problems: list[str] = []
    cert = ev.get("certificate")
    if cert is None:
        return checked, problems
    test_n = ev.get("test_n") if isinstance(ev.get("test_n"), int) and not isinstance(ev.get("test_n"), bool) else None
    vector = ev.get("test_vector")
    delta = None
    try:
        if not isinstance(cert, Mapping):
            raise _Malformed(f"certificate must be an object, not {cert!r:.40}")
        delta, target = _level(cert, "delta", "certificate"), _level(cert, "target", "certificate")
        threshold = _float(cert, "threshold", "certificate")
        served, errors = _counts_of(cert, "certificate")
        want = bound(served, errors, delta)
        want_cert = {"precision": want["precision"], "lcb": want["lcb"], "certified": want["lcb"] >= target}
        if cert.get("method") != METHOD:
            problems.append(f"certificate.method: stated {cert.get('method')!r}, this checker reads {METHOD!r}")
        _compare(problems, "certificate", cert, want_cert)
        if vector is not None:
            _check_vector(vector, "test_vector")
            from_vector = {"served": len(vector) - vector.count(ESCALATED), "errors": vector.count(SERVED_WRONG)}
            _compare(problems, "certificate (from test_vector)", cert, from_vector)
            if test_n is not None and len(vector) != test_n:
                problems.append(f"test_vector: {len(vector)} rows, test_n says {test_n}")
        if "ltt_trace" in cert:
            _recheck_trace(cert["ltt_trace"], threshold, target, delta, problems)
        verdict = "certified" if want_cert["certified"] else "not certified"
        checked.append(
            f"certificate: {served - errors} of {served} served answers agree at threshold {threshold:g} (delta {delta:g}): "
            f"lower bound {want['lcb']:.4f} vs target {target:g}, {verdict}"
        )
    except (_Malformed, ValueError) as e:
        problems.append(f"certificate: {e}")
    _recheck_frontier(ev.get("frontier"), delta, test_n, checked, problems)
    _recheck_per_task(ev.get("per_task"), cert, delta, test_n, checked, problems)
    _recheck_paired(ev.get("paired"), vector, checked, problems)
    return checked, problems


def _recheck_trace(trace: object, threshold: float, target: float, delta: float, problems: list[str]) -> None:
    """Learn-then-Test's trace (calibration rows): each p-value from its counts, the fixed-sequence stop rule (every
    test but the last rejected; the last not, unless the grid ran out), and a threshold no lower than the lowest one the
    sequence kept (the eval's floor may only raise it)."""
    if not isinstance(trace, list) or not all(isinstance(t, Mapping) for t in trace):
        raise _Malformed(f"ltt_trace must be a list of objects, not {trace!r:.40}")
    kept = SERVE_NOTHING
    last_lam = math.inf
    for i, step in enumerate(trace):
        where = f"certificate.ltt_trace[{i}]"
        lam, n, k = _float(step, "threshold", where), _int(step, "n", where), _int(step, "k", where)
        if k > n or lam >= last_lam:
            raise _Malformed(f"{where}: {k} of {n} at threshold {lam!r} (thresholds must fall, k <= n)")
        last_lam = lam
        p = precision_pvalue(k, n, target)
        _compare(problems, where, step, {"p_value": p})
        if p <= delta:
            kept = lam
            continue
        if i != len(trace) - 1:
            problems.append(f"{where}: not rejected (p {p:.4g} > delta {delta:g}), yet the sequence went on")
        break
    if threshold < kept:
        problems.append(f"certificate.threshold: {threshold!r} is below {kept!r}, the lowest threshold Learn-then-Test kept")


def _recheck_frontier(rows: object, delta: float | None, test_n: int | None, checked: list[str], problems: list[str]) -> None:
    if rows is None:
        return
    try:
        if not isinstance(rows, list) or not rows or not all(isinstance(r, Mapping) for r in rows):
            raise _Malformed(f"must be a non-empty list of objects, not {rows!r:.40}")
        last: tuple[float, int, int] | None = None
        each = 0.0
        for i, row in enumerate(rows):
            where = f"frontier[{i}]"
            lam, each = _float(row, "threshold", where), _level(row, "delta", where)
            served, errors = _counts_of(row, where)
            if delta is not None and abs(each - delta / len(rows)) > TOLERANCE:
                problems.append(f"{where}.delta: stated {each!r}, the certificate's delta / {len(rows)} is {delta / len(rows)!r}")
            want: dict[str, Any] = {k: v for k, v in bound(served, errors, each).items() if k in ("precision", "lcb")}
            if test_n is not None:
                want["coverage"] = served / test_n if test_n else 0.0
            _compare(problems, where, row, want)
            # Thresholds fall down the list, and a lower threshold serves a superset: neither count may shrink.
            if last is not None and (lam >= last[0] or served < last[1] or errors < last[2]):
                problems.append(f"{where}: thresholds must fall and counts must not shrink down the frontier")
            last = (lam, served, errors)
        checked.append(f"frontier: {len(rows)} thresholds, each bound at delta {each:g} (Bonferroni)")
    except _Malformed as e:
        problems.append(f"frontier: {e}")


def _recheck_per_task(
    tasks: object, cert: object, delta: float | None, test_n: int | None, checked: list[str], problems: list[str]
) -> None:
    if tasks is None:
        return
    try:
        if not isinstance(tasks, Mapping) or not all(isinstance(t, Mapping) for t in tasks.values()):
            raise _Malformed(f"must be an object of objects, not {tasks!r:.40}")
        if delta is None:
            raise _Malformed("cannot be checked without the certificate's delta")
        total = [0, 0, 0]
        for name, row in tasks.items():
            where = f"per_task[{name!r}]"
            n = _int(row, "n_test", where)
            served, errors = _counts_of(row, where)
            if served > n:
                raise _Malformed(f"{where}: {served} served of {n} rows")
            want = {k: v for k, v in bound(served, errors, delta).items() if k in ("precision", "lcb")}
            want["coverage"] = served / n if n else 0.0
            _compare(problems, where, row, want)
            total = [total[0] + n, total[1] + served, total[2] + errors]
        if isinstance(cert, Mapping):
            whole = [test_n, cert.get("served"), cert.get("errors")]
            for name, have, want_total in zip(("n_test", "served", "errors"), total, whole, strict=True):
                if want_total is not None and have != want_total:
                    problems.append(f"per_task: {name} adds up to {have}, the whole eval says {want_total}")
        checked.append(f"per_task: {len(tasks)} tasks")
    except _Malformed as e:
        problems.append(f"per_task: {e}")


def _recheck_paired(pair: object, vector: object, checked: list[str], problems: list[str]) -> None:
    """The paired comparison's p-values and rate from its counts, and its counts against the candidate's own test
    vector (the live version's is not in the candidate's eval): the same number of rows, b no more than the rows the
    candidate escalates, c no more than it serves, negative flips no more than its wrong answers, positive flips no more
    than the rest."""
    if pair is None:
        return
    try:
        if not isinstance(pair, Mapping):
            raise _Malformed(f"must be an object, not {pair!r:.40}")
        n = _int(pair, "n", "paired")
        keys = ("cov_only_live", "cov_only_cand", "neg_flips", "pos_flips", "neg_flips_served", "live_served_ok")
        b, c, nf, pf, nf_served, live_ok = (_int(pair, k, "paired") for k in keys)
        if b + c > n or nf + pf > n or live_ok > n or nf_served > min(nf, live_ok):
            raise _Malformed(f"counts that cannot come from {n} rows: {dict((k, pair.get(k)) for k in keys)}")
        if isinstance(vector, str) and not set(vector) - OUTCOMES:
            escalated, wrong = vector.count(ESCALATED), vector.count(SERVED_WRONG)
            if n != len(vector):
                problems.append(f"paired.n: stated {n}, the test vector has {len(vector)} rows")
            elif b > escalated or c > n - escalated or nf > wrong or pf > n - wrong:
                problems.append(
                    f"paired: counts the test vector rules out ({escalated} escalated and {wrong} wrong of {n}: "
                    f"cov_only_live {b}, cov_only_cand {c}, neg_flips {nf}, pos_flips {pf})"
                )
        want = paired_stats(n, b, c, nf, pf, nf_served, live_ok)
        _compare(problems, "paired", pair, {k: want[k] for k in ("p_cov", "p_err", "nfr", "nfr_95")})
        checked.append(f"paired against v{pair.get('against')}: p_cov {want['p_cov']:.4g}, p_err {want['p_err']:.4g}")
    except _Malformed as e:
        problems.append(f"paired: {e}")
