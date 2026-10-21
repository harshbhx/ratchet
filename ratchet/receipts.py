"""Verified savings receipts (FR-15.5, ADR 0029): what Ratchet saved each day, for the whole gateway and for each API
client, measured against audited teacher bills, with a lower confidence bound to bill on, signed and chained.

The all-teacher baseline of an answer the student served is an estimate: the token formula assumes the teacher writes
RATCHET_TEACHER_REASONING_TOKENS of reasoning, and nobody measured that, while a vendor paid on savings earns more the
higher it is. Audits are a random sample of the served answers, drawn independently of cost (one draw per answer, at the
rate logged as `label_p`), and each audit is a real teacher bill. So, per task, the ratio of what the teacher really cost
to what the formula said is estimated by the design-based ratio estimator over the audited answers (Cochran, Sampling
Techniques, ch. 6; Horvitz-Thompson weights 1 / label_p), with its variance and a one-sided 95% lower bound (a normal
approximation, and labelled as one). A receipt bills on that bound, never on the formula.

A receipt covers one ledger day (`period`) of one client, or of the gateway: an RFC 9162 Merkle tree over each request's
metadata (`LEAF_KEYS`: no request text, INV-12; the API key's name, never the key, INV-13), the totals, calibration and
fee computed from those leaves alone (`compute`), and the hash of the same client's previous receipt, signed with the BOM
key (`bom.document`). Receipts are written once (INV-17), so a request id a client saw in `X-Ratchet-Request-Id` can be
proved to be in its receipt, and a later edit of the ledger, or of an earlier receipt, shows.

Every amount is an integer number of nano-USD (`round(usd * 1e9)`), so sums are exact; every other number that is not a
whole number is a decimal string, so the canonical JSON that is hashed and signed holds no floats.

    python -m ratchet.receipt verify r.json [--public-key K] [--prev prev.json] [--proof p.json] [--rows leaves.jsonl]
    python -m ratchet.receipt seal --db ratchet.db [--period N] [--client NAME]

Scope: receipts make edits after sealing detectable and tie the estimate to audited teacher bills. They cannot catch a
false cost written at request time; reconcile the summed teacher spend with the Token Factory bill for the same API key.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import sys
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, TextIO

from . import __version__, stats
from . import bom as bomdoc
from .ledger import Ledger, ReceiptExists
from .merkle import LOG_ALG, LogTree, verify_inclusion

SCHEMA = "ratchet.receipt/v1"
# What each leaf holds, in this order of meaning (the canonical JSON sorts the keys). `route` and `label_p_ppb` let a
# verifier recompute the calibration from the leaves alone; the cost split is FR-15.1's (never split again here).
LEAF_KEYS = (
    "id",
    "ts_ms",
    "day",
    "task",
    "client",
    "served_by",
    "route",
    "student_version",
    "escalated",
    "audited",
    "label_p_ppb",
    "cost_nusd",
    "student_cost_nusd",
    "teacher_cost_nusd",
    "baseline_nusd",
    "baseline_est_nusd",
)
NANO = 10**9  # nano-USD per USD, and parts per billion of a probability
ZERO_SHA256 = "0" * 64  # `prev_sha256` of a client's first receipt
Z = Decimal("1.645")  # the one-sided 95% normal quantile
Z_FLOAT = float(Z)
BASIS = "one-sided 95% lower bound"
FORMULA = "formula"  # no task calibrated: nothing is billed
OK, INSUFFICIENT, UNLOGGED = "ok", "insufficient_audits", "unlogged"
MEASURED, SERVED, UNVERIFIED = "measured", "served", "unverified"
# Routes whose baseline is neither a teacher bill nor on the audit design: the student's answer standing in for an
# unavailable teacher (`degraded`), and the fallback teacher's answer, whose baseline is the primary teacher's price on
# another model's tokens (`fallback_teacher`). The router's `STAND_IN_ROUTES` (a test keeps the two equal).
UNVERIFIED_ROUTES = frozenset({"degraded", "fallback_teacher"})
DEFAULT_SHARE, DEFAULT_MIN_AUDITS = 0.25, 30
METHOD = (
    "ratio estimator (Cochran, Sampling Techniques, ch. 6) per task over audited student answers, weighted 1/label_p; "
    "variance N^2 (1 - n/N) s_r^2 / n_eff; one-sided 95% lower bound by the normal approximation"
)
UNITS = "amounts in nano-USD (1e-9 USD); numbers that are not whole are decimal strings"
SCOPE = (
    "edits after sealing are detectable, and the served answers' baseline is calibrated on audited teacher bills; costs "
    "written at request time are not verified: reconcile teacher spend with the provider's bill for the same key"
)
GPU_VIEW_BASIS = "informational: billing uses the per-token view"
CLIENT_GPU_NOTE = "endpoint and training GPU-hours are shared by every client; the gateway-wide receipt has them"
# On a live ledger a day is sealed this long after it ended, so a request logged at its last moment is in.
SEAL_GRACE_S = 300.0
# How far a recomputation may differ on numbers that come from the estimator (floating point in another implementation
# or summation order): nano-USD on amounts, and relative on the decimal strings.
TOLERANCE_NUSD = 2
TOLERANCE_REL = 1e-5
ESTIMATED_KEYS = frozenset(
    {
        "point_nusd",
        "se_nusd",
        "baseline_calibrated_nusd",
        "calibrated_nusd",
        "billable_nusd",
        "margin_nusd",
        "billable_lo_nusd",
        "net_billable_lo_nusd",
        "fee_nusd",
        "baseline_nusd",
    }
)
DECIMAL_KEYS = frozenset({"ratio", "ratio_se", "n_eff"})
_SEAL_LOCK = threading.Lock()  # one sealing run at a time per process; across processes, `Ledger.add_receipt` decides
_HEX = frozenset("0123456789abcdef")


class PeriodOpen(ValueError):
    """The day has not ended yet, so its receipt cannot be sealed."""


# ---------------------------------------------------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------------------------------------------------
def nusd(usd: object) -> int | None:
    """`usd` as whole nano-USD (`round(usd * 1e9)`), or None for a missing or non-finite value."""
    if usd is None or isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return None
    try:
        x = float(usd)
    except OverflowError:
        return None
    return round(x * NANO) if math.isfinite(x) else None


def _whole(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _ppb(p: object) -> int | None:
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
        return None
    return round(float(p) * NANO)


def leaf(row: Mapping[str, Any]) -> dict[str, Any]:
    """One request's leaf, from a `Ledger.period_rows` row: exactly `LEAF_KEYS`, every value a whole number, a string or
    None. Metadata only, never request text (INV-12); `client` is the key's name (INV-13)."""
    ts = row.get("ts")
    return {
        "id": _whole(row.get("id")),
        "ts_ms": round(float(ts) * 1000) if isinstance(ts, (int, float)) and math.isfinite(ts) else None,
        "day": _whole(row.get("day")),
        "task": _text(row.get("task")),
        "client": _text(row.get("client")) or None,
        "served_by": _text(row.get("served_by")),
        "route": _text(row.get("route")),
        "student_version": _whole(row.get("student_version")),
        "escalated": _whole(row.get("escalated")),
        "audited": _whole(row.get("audited")),
        "label_p_ppb": _ppb(row.get("label_p")),
        "cost_nusd": nusd(row.get("cost_usd")),
        "student_cost_nusd": nusd(row.get("student_cost_usd")),
        "teacher_cost_nusd": nusd(row.get("teacher_cost_usd")),
        "baseline_nusd": nusd(row.get("baseline_cost_usd")),
        "baseline_est_nusd": nusd(row.get("baseline_est_usd")),
    }


def leaf_bytes(lf: Mapping[str, Any]) -> bytes:
    """The bytes a leaf is hashed as in the tree: its canonical JSON (`bom.canonical`)."""
    return bomdoc.canonical(dict(lf))


def tree(leaves: Sequence[Mapping[str, Any]]) -> LogTree:
    """The RFC 9162 tree over the leaves, in the order given (a receipt's are in request id order)."""
    return LogTree([leaf_bytes(lf) for lf in leaves])


def category(lf: Mapping[str, Any]) -> str:
    """`measured`: the primary teacher answered, so the baseline is its bill; `served`: the student served on the audit
    design (a random share is audited); `unverified`: a stand-in answer (`UNVERIFIED_ROUTES`, ADR 0019): the student's
    for an unavailable teacher, never audited, or the fallback teacher's, whose baseline is the primary teacher's price on
    another model's tokens. An unverified baseline is never billed."""
    if lf.get("route") in UNVERIFIED_ROUTES:
        return UNVERIFIED
    return SERVED if lf.get("served_by") == "student" else MEASURED


# ---------------------------------------------------------------------------------------------------------------------
# The estimate and the bill (pure functions of the leaves)
# ---------------------------------------------------------------------------------------------------------------------
def _sum(leaves: Iterable[Mapping[str, Any]], key: str) -> int:
    return sum(v for v in (lf.get(key) for lf in leaves) if isinstance(v, int) and not isinstance(v, bool))


def _check_min_audits(min_audits: object) -> int:
    if isinstance(min_audits, bool) or not isinstance(min_audits, int) or min_audits < 2:
        raise ValueError(f"min_audits must be a whole number, at least 2, not {min_audits!r}")
    return min_audits


def share_text(share: object) -> str:
    """The take rate as the decimal string a receipt states (0.25 -> '0.25'); ValueError outside 0 to 1."""
    try:
        if isinstance(share, bool):
            raise InvalidOperation
        d = Decimal(share) if isinstance(share, str) else Decimal(repr(float(share)))  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise ValueError(f"share must be a number from 0 to 1, not {share!r}") from None
    if not d.is_finite() or not 0 <= d <= 1:
        raise ValueError(f"share must be a number from 0 to 1, not {share!r}")
    text = format(d.normalize(), "f")
    return text if text != "-0" else "0"


def _task(rows: Sequence[Mapping[str, Any]], min_audits: int) -> dict[str, Any]:
    """One task's estimate of its served answers' all-teacher cost. R = sum w y / sum w x over its audited answers
    (x = the formula's estimate, y = the audit's real teacher cost, w = 1 / label_p; `stats.ratio_estimate`), the point
    Y = R X_U over all N served answers, and V = N^2 (1 - n/N) s_r^2 / n_eff (Kish's n_eff is n when the audit rate was
    constant). Fewer than `min_audits` audits: `insufficient_audits`; a served answer without a logged formula estimate
    (written before v0.5): `unlogged`. Neither has an interval, so neither is billed."""
    n_served = len(rows)
    out: dict[str, Any] = {
        "status": OK,
        "n_served": n_served,
        "n_audits": 0,
        "n_eff": None,
        "cost_nusd": _sum(rows, "cost_nusd"),
        "recorded_nusd": _sum(rows, "baseline_nusd"),
        "x_total_nusd": None,
        "ratio": None,
        "ratio_se": None,
        "point_nusd": None,
        "se_nusd": None,
    }
    xs = [lf.get("baseline_est_nusd") for lf in rows]
    if any(not isinstance(x, int) or isinstance(x, bool) for x in xs):
        out["status"] = UNLOGGED
        return out
    out["x_total_nusd"] = x_total = sum(xs)  # type: ignore[arg-type]
    pairs: list[tuple[float, float]] = []
    weights: list[float] = []
    for lf, x in zip(rows, xs, strict=True):
        y, p = lf.get("baseline_nusd"), lf.get("label_p_ppb")
        if lf.get("audited") != 1 or not isinstance(y, int) or isinstance(y, bool):
            continue
        if isinstance(p, int) and not isinstance(p, bool):
            if p <= 0:
                continue  # drawn at rate 0: cannot be weighted (never written by the router)
            weights.append(NANO / p)
        else:
            weights.append(1.0)  # a row from before label_p was logged: the audit rate was one constant then
        pairs.append((float(x), float(y)))  # type: ignore[arg-type]
    n = out["n_audits"] = len(pairs)
    est = stats.ratio_estimate(pairs, weights, min_n=min_audits)
    if est["status"] != "ok":
        out["status"] = INSUFFICIENT
        return out
    ratio, s2, n_eff = est["ratio"], est["s2"], est["n_eff"]
    variance = n_served * n_served * (1 - n / n_served) * s2 / n_eff
    out.update(
        n_eff=f"{n_eff:.1f}",
        ratio=f"{ratio:.6f}",
        ratio_se=f"{est['se']:.6f}",
        point_nusd=round(ratio * x_total),
        se_nusd=math.ceil(math.sqrt(max(0.0, variance))),
    )
    return out


def calibrate(leaves: Sequence[Mapping[str, Any]], min_audits: int = DEFAULT_MIN_AUDITS) -> dict[str, dict[str, Any]]:
    """{task: its estimate} for the tasks with answers the student served on the audit design (`category` `served`), in
    task order. See `_task` for the formulas and statuses."""
    min_audits = _check_min_audits(min_audits)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for lf in leaves:
        if category(lf) == SERVED:
            groups.setdefault(str(lf.get("task")), []).append(lf)
    return {task: _task(groups[task], min_audits) for task in sorted(groups)}


def billable(lf: Mapping[str, Any], tasks: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether a leaf is in the billable set: the teacher answered it (a measured baseline, whose savings are usually
    negative: the student's samples were spent too), or the student served it for a task whose estimate has an
    interval (status `ok`)."""
    c = category(lf)
    return c == MEASURED or (c == SERVED and tasks.get(str(lf.get("task")), {}).get("status") == OK)


def stand_in_losses(leaves: Iterable[Mapping[str, Any]]) -> int:
    """What the stand-in answers lost, in nano-USD (0 or less): the sum of min(0, baseline - cost) over them. Their
    baselines are never billed, but a stand-in can still cost more than its baseline (the student's samples were spent
    before the fallback teacher answered), and the cascade's overhead counts against the vendor, as on measured
    requests; leaving it out would raise the fee. A gain is never billed, so only losses count."""
    total = 0
    for lf in leaves:
        cost, base = lf.get("cost_nusd"), lf.get("baseline_nusd")
        if all(isinstance(v, int) and not isinstance(v, bool) for v in (cost, base)):
            total += min(0, base - cost)
    return total


def margin(se_nusd: Iterable[int]) -> int:
    """The bound's distance below the billable savings, in nano-USD: ceil(1.645 sqrt(sum se^2)) over the calibrated
    tasks' standard errors (the tasks' estimates are independent: separate rows, separate draws)."""
    total = sum(int(s) * int(s) for s in se_nusd)
    return math.ceil(Z_FLOAT * math.sqrt(total)) if total else 0


def compute(
    leaves: Sequence[Mapping[str, Any]],
    *,
    share: float | str = DEFAULT_SHARE,
    min_audits: int = DEFAULT_MIN_AUDITS,
    spend_nusd: int = 0,
) -> dict[str, Any]:
    """A receipt's `totals`, `calibration`, `savings` and `billing`, from its leaves alone, so anyone holding them can
    recompute every number (`recheck`). The billable savings are the billable set's baseline (measured bills plus each
    calibrated task's point) less its cost, plus what the stand-in answers lost (`stand_in_losses`, never a gain); the
    bound subtracts `margin`; the period's spend outside requests (`spend_nusd`, backfill labelling) comes off next; the
    fee is `share` of what is left, floored at 0 and rounded down to a nano-USD. With no calibrated task the basis is
    `formula` and the fee 0."""
    share_s = share_text(share)
    if isinstance(spend_nusd, bool) or not isinstance(spend_nusd, int) or spend_nusd < 0:
        raise ValueError(f"spend_nusd must be a whole number of nano-USD, 0 or more, not {spend_nusd!r}")
    groups: dict[str, list[Mapping[str, Any]]] = {MEASURED: [], SERVED: [], UNVERIFIED: []}
    for lf in leaves:
        groups[category(lf)].append(lf)
    tasks = calibrate(leaves, min_audits)
    ok = {t: c for t, c in tasks.items() if c["status"] == OK}
    measured, unverified = groups[MEASURED], groups[UNVERIFIED]
    cost = _sum(leaves, "cost_nusd")
    recorded = _sum(leaves, "baseline_nusd")
    baseline_measured = _sum(measured, "baseline_nusd")
    served_part = sum(c["point_nusd"] if c["status"] == OK else c["recorded_nusd"] for c in tasks.values())
    calibrated = baseline_measured + served_part + _sum(unverified, "baseline_nusd")
    bill_baseline = baseline_measured + sum(c["point_nusd"] for c in ok.values())
    bill_cost = _sum(measured, "cost_nusd") + sum(c["cost_nusd"] for c in ok.values())
    stand_in_loss = stand_in_losses(unverified)
    bill_savings = bill_baseline - bill_cost + stand_in_loss
    lo_margin = margin(c["se_nusd"] for c in ok.values())
    lower = bill_savings - lo_margin
    net = lower - spend_nusd
    basis = BASIS if ok else FORMULA
    fee = math.floor(Fraction(share_s) * max(0, net)) if ok else 0
    missing = sum(1 for lf in leaves if lf.get("cost_nusd") is None or lf.get("baseline_nusd") is None)
    return {
        "totals": {
            "requests": len(leaves),
            "teacher_served": len(measured),
            "student_served": len(groups[SERVED]),
            "unverified": len(unverified),
            "audits": sum(c["n_audits"] for c in tasks.values()),
            "cost_nusd": cost,
            "student_cost_nusd": _sum(leaves, "student_cost_nusd"),
            "teacher_cost_nusd": _sum(leaves, "teacher_cost_nusd"),
            "baseline_recorded_nusd": recorded,
            "baseline_measured_nusd": baseline_measured,
            "baseline_calibrated_nusd": calibrated,
            "missing_amounts": missing,
        },
        "calibration": {"method": METHOD, "min_audits": _check_min_audits(min_audits), "z": str(Z), "tasks": tasks},
        "savings": {
            "recorded_nusd": recorded - cost,
            "calibrated_nusd": calibrated - cost,
            "stand_in_loss_nusd": stand_in_loss,
            "billable_nusd": bill_savings,
            "margin_nusd": lo_margin,
            "billable_lo_nusd": lower,
            "spend_nusd": spend_nusd,
            "net_billable_lo_nusd": net,
        },
        "billing": {
            "share": share_s,
            "fee_nusd": fee,
            "basis": basis,
            "billable": {
                "requests": sum(1 for lf in leaves if billable(lf, tasks)),
                "baseline_nusd": bill_baseline,
                "cost_nusd": bill_cost,
                "tasks": sorted(ok),
            },
        },
    }


# ---------------------------------------------------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SealConfig:
    """How receipts are sealed: the BOM signer (None: unsigned, with the sha256 only), the take rate, the audits a task
    needs, the settings that price the gateway-wide receipt's GPU-hours view (None: no such view) and the label every
    receipt carries (`simulation (mock models)` or `live traffic`)."""

    signer: bomdoc.Signer | None = None
    share: float = DEFAULT_SHARE
    min_audits: int = DEFAULT_MIN_AUDITS
    settings: Any = None
    label: str | None = None

    def __post_init__(self) -> None:
        share_text(self.share)
        _check_min_audits(self.min_audits)

    @classmethod
    def from_settings(cls, settings: Any, signer: bomdoc.Signer | None = None, label: str | None = None) -> SealConfig:
        """From `Settings.receipt_policy()` (RATCHET_TAKE_RATE, RATCHET_RECEIPT_MIN_AUDITS; a bad value raises)."""
        policy = settings.receipt_policy()
        return cls(signer, policy.share, policy.min_audits, settings, label)


def closed_through(ledger: Ledger) -> int:
    """The last ledger day that has ended. A simulated ledger's day moves when the caller says (every day before the
    current one has ended); on a live ledger (`auto_day`), every day that ended SEAL_GRACE_S ago on the wall clock, also
    when no request came since (the ledger's own `day` moves only with requests)."""
    if not ledger.auto_day:
        return ledger.day - 1
    now = ledger.now()
    return ledger.day_of(now - SEAL_GRACE_S, now) - 1


def _gpu_view(ledger: Ledger, period: int, settings: Any, recorded: int, calibrated: int) -> dict[str, Any]:
    """The gateway-wide receipt's GPU-hours view of its day (ADR 0023's cost report for that day): informational, from
    compute intervals that can still be recorded later; billing uses the per-token view."""
    from .cost import cost_report  # noqa: PLC0415 - only a sealing gateway or CLI needs the report

    rep = cost_report(ledger, settings, since=period, until=period)
    total = nusd(rep["views"]["gpu_hours"]["total_usd"]) or 0
    return {
        "teacher_nusd": nusd(rep["teacher_usd"]),
        "endpoint_nusd": nusd(rep["endpoint_usd"]),
        "training_nusd": nusd(rep["training_usd"]),
        "hosted_student_nusd": nusd(rep["hosted_student_usd"]),
        "total_nusd": total,
        "endpoint_seconds": round(rep["endpoint_hours"] * 3600),
        "training_seconds": round(rep["training_hours"] * 3600),
        "savings_recorded_nusd": recorded - total,
        "savings_calibrated_nusd": calibrated - total,
        "prices": rep["prices"]["label"],
        "basis": GPU_VIEW_BASIS,
    }


def manifest(
    ledger: Ledger,
    period: int,
    client: str | None,
    rows: Sequence[Mapping[str, Any]],
    config: SealConfig,
    spend_nusd: int = 0,
) -> dict[str, Any]:
    """The `ratchet.receipt/v1` manifest of one (period, client) from its rows (`Ledger.period_rows`), chained to the
    client's previous receipt."""
    leaves = [leaf(r) for r in rows]
    t = tree(leaves)
    sections = compute(leaves, share=config.share, min_audits=config.min_audits, spend_nusd=spend_nusd)
    prev = ledger.last_receipt(client, before=period)
    totals = sections["totals"]
    if client is None and config.settings is not None:
        gpu: dict[str, Any] | None = _gpu_view(
            ledger, period, config.settings, totals["baseline_recorded_nusd"], totals["baseline_calibrated_nusd"]
        )
        note = None
    else:
        gpu, note = None, CLIENT_GPU_NOTE if client is not None else "not computed"
    return {
        "schema": SCHEMA,
        "generator": f"ratchet {__version__}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": config.label,
        "period": {"day": int(period)},
        "client": client,
        "first_id": leaves[0]["id"] if leaves else None,
        "last_id": leaves[-1]["id"] if leaves else None,
        "n": len(leaves),
        "merkle": {"alg": LOG_ALG, "root": t.root.hex(), "size": t.size, "leaf_keys": list(LEAF_KEYS)},
        **sections,
        "cost_views": {"billed": "per_token", "gpu_hours": gpu, "gpu_hours_note": note},
        "prev_sha256": prev.get("sha256", ZERO_SHA256) if isinstance(prev, dict) else ZERO_SHA256,
        "units": UNITS,
        "scope": SCOPE,
    }


def _seal_one(ledger: Ledger, period: int, client: str | None, config: SealConfig, spend_nusd: int) -> bool:
    if ledger.receipt(period, client) is not None:
        return False
    rows = ledger.period_rows(period, client)
    if not rows:
        return False
    doc = bomdoc.document(manifest(ledger, period, client, rows, config, spend_nusd), config.signer)
    try:
        ledger.add_receipt(period, client, doc)
    except ReceiptExists:  # another process sealed it first; its document stands
        return False
    return True


def _seal_period(ledger: Ledger, period: int, config: SealConfig) -> list[tuple[int, str | None]]:
    """Seal every client's receipt of `period`, then the gateway's (its presence marks the day done). The day's spend
    outside requests is netted in full from the gateway's receipt; each client's receipt nets its share by request count,
    rounded down."""
    counts = ledger.period_counts(period)
    total = sum(counts.values())
    spend = sum(n for n in (nusd(u) for u in ledger.period_spend(period)) if n is not None and n > 0)
    sealed: list[tuple[int, str | None]] = []
    for client in sorted(c for c in counts if c is not None):
        if _seal_one(ledger, period, client, config, spend * counts[client] // total if total else 0):
            sealed.append((period, client))
    if _seal_one(ledger, period, None, config, spend):
        sealed.append((period, None))
    return sealed


def seal_due(
    ledger: Ledger, config: SealConfig | None = None, through: int | None = None, closed: int | None = None
) -> list[tuple[int, str | None]]:
    """Seal every unsealed day up to `through` (default: every day that has ended), oldest first, so each client's
    chain stays one line. `closed` says which days have ended when the caller knows better than the clock (the
    simulator after a day's night, an offline simulated ledger); a `through` after it raises `PeriodOpen`. Returns the
    (period, client) receipts sealed now (client None for the gateway's)."""
    config = config or SealConfig()
    last = closed_through(ledger) if closed is None else int(closed)
    upto = last if through is None else int(through)
    if upto > last:
        raise PeriodOpen(f"day {upto} has not ended yet; receipts are sealed once their day is over (last ended: {last})")
    sealed: list[tuple[int, str | None]] = []
    with _SEAL_LOCK:
        for period in ledger.unsealed_days(upto):
            sealed += _seal_period(ledger, period, config)
    return sealed


def seal(
    ledger: Ledger,
    period: int,
    client: str | None = None,
    config: SealConfig | None = None,
    closed: int | None = None,
) -> dict[str, Any] | None:
    """The receipt of `period` for `client` (None: the gateway's). Idempotent: a sealed receipt is returned as stored,
    with no write at all; otherwise every unsealed day up to `period` is sealed first, in order (`seal_due`). None when
    the client sent no request that day. `PeriodOpen` when the day has not ended."""
    existing = ledger.receipt(period, client)
    if existing is not None:
        return existing
    seal_due(ledger, config, through=period, closed=closed)
    return ledger.receipt(period, client)


# ---------------------------------------------------------------------------------------------------------------------
# Verification (untrusted input: never raises)
# ---------------------------------------------------------------------------------------------------------------------
def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _manifest(doc: object) -> dict[str, Any] | None:
    m = doc.get("manifest") if isinstance(doc, dict) else None
    return m if isinstance(m, dict) else None


def _day(m: Mapping[str, Any]) -> int | None:
    """A manifest's `period.day` when `period` is an object and `day` a whole number, else None: the verifiers read it
    from untrusted files, where `period` can be a string, a list or a number."""
    period = m.get("period")
    day = period.get("day") if isinstance(period, dict) else None
    return day if isinstance(day, int) and not isinstance(day, bool) else None


def check_manifest(doc: object) -> list[str]:
    """What is malformed in a receipt document's shape (schema, tree, chain, amounts), as readable lines; empty when the
    shape is right. Floats are refused anywhere in the manifest."""
    m = _manifest(doc)
    if m is None:
        return ["not a receipt document (no manifest)"]
    problems = []
    if m.get("schema") != SCHEMA:
        problems.append(f"schema is {m.get('schema')!r}, not {SCHEMA}")
    tr = m.get("merkle")
    if not isinstance(tr, dict) or tr.get("alg") != LOG_ALG or not _hex64(tr.get("root")) or not _count(tr.get("size")):
        problems.append(f"merkle must be {{alg: {LOG_ALG}, root: 64 hex characters, size: a count}}")
    elif tr.get("size") != m.get("n"):
        problems.append(f"merkle size {tr.get('size')} is not n = {m.get('n')}")
    if not _hex64(m.get("prev_sha256")):
        problems.append("prev_sha256 must be 64 hex characters")
    period = m.get("period")
    if not isinstance(period, dict) or not isinstance(period.get("day"), int) or isinstance(period.get("day"), bool):
        problems.append("period must be {day: a whole number}")
    if m.get("client") is not None and not isinstance(m.get("client"), str):
        problems.append("client must be a name or null")
    stack: list[tuple[str, Any]] = [("manifest", m)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, float):
            problems.append(f"{path} is a float; a receipt holds whole numbers and decimal strings only")
        elif isinstance(value, dict):
            stack.extend((f"{path}.{k}", v) for k, v in value.items())
        elif isinstance(value, list):
            stack.extend((f"{path}[{i}]", v) for i, v in enumerate(value))
    return problems


def verify_receipt(doc: object, public_key: str | None = None) -> tuple[bool, list[str]]:
    """The receipt's hash and signature (`bom.verify` with the receipts' schema; pass the public key you trust) and its
    shape. (ok, lines)."""
    try:
        ok, reason = bomdoc.verify(doc, public_key, accepted=(SCHEMA,))  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001 - untrusted input must never crash a verifier
        ok, reason = False, f"cannot check the document ({type(e).__name__})"
    lines = [("OK: " if ok else "FAIL: ") + reason]
    if ok:
        problems = check_manifest(doc)
        lines += [f"FAIL: {p}" for p in problems]
        ok = not problems
    return ok, lines


def verify_chain(doc: object, prev: object) -> tuple[bool, str]:
    """Whether `prev` is the receipt `doc` chains to: same client, an earlier day, and the sha256 of its manifest equal
    to `doc`'s `prev_sha256` (recomputed, never read from `prev`). (ok, line)."""
    try:
        return _chain(doc, prev)
    except Exception as e:  # noqa: BLE001 - untrusted input must never crash a verifier
        return False, f"FAIL: chain: cannot check the receipts ({type(e).__name__})"


def _chain(doc: object, prev: object) -> tuple[bool, str]:
    m, pm = _manifest(doc), _manifest(prev)
    if m is None or pm is None:
        return False, "FAIL: chain: not a receipt document"
    try:
        digest = hashlib.sha256(bomdoc.canonical(pm)).hexdigest()
    except (TypeError, ValueError):
        return False, "FAIL: chain: the previous receipt cannot be hashed"
    if pm.get("client") != m.get("client"):
        theirs, ours = pm.get("client") or "the gateway", m.get("client") or "the gateway"
        return False, f"FAIL: chain: the previous receipt is {theirs}'s, not {ours}'s"
    day, pday = _day(m), _day(pm)
    if day is None or pday is None or pday >= day:
        return False, f"FAIL: chain: the previous receipt (day {pday}) is not from before day {day}"
    if digest != m.get("prev_sha256"):
        return False, f"FAIL: chain: prev_sha256 is not the sha256 of day {pday}'s receipt (one of them was changed)"
    return True, f"OK: chain: prev_sha256 is the sha256 of day {pday}'s receipt"


def _leaf_ok(lf: object) -> bool:
    return isinstance(lf, dict) and set(lf) == set(LEAF_KEYS) and _count(lf.get("id"))


def verify_proof(doc: object, proof: object) -> tuple[bool, str]:
    """Whether `proof` (`GET /ratchet/receipts/{period}/proof/{id}`) shows its leaf in the receipt: RFC 9162 inclusion
    against the root and size the receipt signs, never the ones the proof states. (ok, line)."""
    try:
        return _proof(doc, proof)
    except Exception as e:  # noqa: BLE001 - untrusted input must never crash a verifier
        return False, f"FAIL: proof: cannot check the proof ({type(e).__name__})"


def _proof(doc: object, proof: object) -> tuple[bool, str]:
    m = _manifest(doc)
    tr = m.get("merkle") if m is not None else None
    if m is None or not isinstance(tr, dict) or not _hex64(tr.get("root")) or not _count(tr.get("size")):
        return False, "FAIL: proof: the receipt has no well-formed merkle section"
    if not isinstance(proof, dict) or not _leaf_ok(proof.get("leaf")):
        return False, f"FAIL: proof: the leaf must be an object with exactly {', '.join(LEAF_KEYS)}"
    lf, index, path = proof["leaf"], proof.get("index"), proof.get("path")
    if not _count(index) or not isinstance(path, list):
        return False, "FAIL: proof: index must be a count and path a list of hex hashes"
    other_client = m.get("client") is not None and lf.get("client") != m.get("client")
    day = _day(m)
    if day is None or lf.get("day") != day or other_client:
        return False, "FAIL: proof: the leaf is from another day or client than the receipt"
    try:
        included = verify_inclusion(leaf_bytes(lf), index, tr["size"], path, tr["root"])
    except (TypeError, ValueError):
        included = False
    if not included:
        return False, f"FAIL: proof: request {lf.get('id')} does not verify against the receipt's signed root"
    return True, f"OK: proof: request {lf['id']} is leaf {index} of {tr['size']} in the signed tree"


def read_leaves(text: str) -> list[Any]:
    """Leaves from a file: JSON lines (one leaf per line, as `GET /ratchet/receipts/{period}/leaves` sends them), a JSON
    list, or an object with `leaves`. ValueError when it is none of these."""
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("leaves"), list):
            return data["leaves"]
        if isinstance(data, dict) and _leaf_ok(data):
            return [data]
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def _walk(want: Any, have: Any, path: str, out: list[str]) -> None:
    """Compare a stored section with its recomputation: whole numbers exactly, except the estimator's (ESTIMATED_KEYS,
    within TOLERANCE_NUSD) and its decimal strings (within TOLERANCE_REL); everything else exactly."""
    key = path.rsplit(".", 1)[-1]
    if isinstance(have, dict):
        if not isinstance(want, dict) or set(want) != set(have):
            out.append(f"{path}: stated {json.dumps(want)[:80]}, recomputed {json.dumps(have)[:80]}")
            return
        for k in sorted(have):
            _walk(want[k], have[k], f"{path}.{k}", out)
        return
    if key in ESTIMATED_KEYS and isinstance(have, int) and isinstance(want, int) and not isinstance(want, bool):
        if abs(want - have) > TOLERANCE_NUSD:
            out.append(f"{path}: stated {want}, recomputed {have}")
        return
    if key in DECIMAL_KEYS and isinstance(have, str) and isinstance(want, str):
        try:
            a, b = float(want), float(have)
        except ValueError:
            out.append(f"{path}: stated {want!r} is not a number")
            return
        if not math.isfinite(a) or abs(a - b) > TOLERANCE_REL * max(1.0, abs(b)):
            out.append(f"{path}: stated {want}, recomputed {have}")
        return
    if want != have or type(want) is not type(have):
        out.append(f"{path}: stated {json.dumps(want)[:80]}, recomputed {json.dumps(have)[:80]}")


def recheck(doc: object, leaves: Sequence[Any]) -> list[str]:
    """Recompute a receipt from its leaves: the tree (root and size), the ids (in order, first and last), every leaf's day
    and client, then every total, task estimate, saving and the fee (`compute`, with the receipt's own share, audits
    needed and spend), within the tolerances above. The problems as readable lines; empty when everything follows."""
    m = _manifest(doc)
    if m is None:
        return ["not a receipt document (no manifest)"]
    bad = [i for i, lf in enumerate(leaves) if not _leaf_ok(lf)]
    if bad:
        return [f"leaf {bad[0]} is not an object with exactly {', '.join(LEAF_KEYS)} ({len(bad)} such leaves)"]
    problems: list[str] = []
    ids = [lf["id"] for lf in leaves]
    if any(b <= a for a, b in zip(ids, ids[1:], strict=False)):
        problems.append("leaves are not in increasing request id order")
    tr = m.get("merkle") if isinstance(m.get("merkle"), dict) else {}
    root = tree(leaves).root.hex()
    if (root, len(leaves)) != (tr.get("root"), tr.get("size")):
        signed = str(tr.get("root"))[:16]
        problems.append(f"the {len(leaves)} leaves rebuild root {root[:16]}..., not the signed {signed}... of {tr.get('size')}")
    if (m.get("first_id"), m.get("last_id"), m.get("n")) != ((ids[0], ids[-1], len(ids)) if ids else (None, None, 0)):
        problems.append("first_id, last_id or n do not match the leaves")
    day, client = _day(m), m.get("client")
    if any(lf["day"] != day or (client is not None and lf["client"] != client) for lf in leaves):
        problems.append("a leaf is from another day or client than the receipt")
    try:
        billing, cal, sav = m["billing"], m["calibration"], m["savings"]
        again = compute(leaves, share=billing["share"], min_audits=cal["min_audits"], spend_nusd=sav["spend_nusd"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        return [*problems, f"cannot recompute the totals: {type(e).__name__}: {e}"]
    for section in ("totals", "calibration", "savings", "billing"):
        _walk(m.get(section), again[section], section, problems)
    return problems


def verify_rows(doc: object, leaves: Sequence[Any]) -> tuple[bool, list[str]]:
    """`recheck` as OK/FAIL lines."""
    try:
        problems = recheck(doc, leaves)
    except Exception as e:  # noqa: BLE001 - untrusted input must never crash a verifier
        problems = [f"cannot recheck the leaves ({type(e).__name__})"]
    if problems:
        more = [f"FAIL: rows: and {len(problems) - 20} more"] if len(problems) > 20 else []
        return False, [f"FAIL: rows: {p}" for p in problems[:20]] + more
    return True, [f"OK: rows: {len(leaves)} leaves rebuild the signed root, and every total, estimate and the fee follow"]


def proof_for(ids: Sequence[int], t: LogTree, request_id: int) -> tuple[int, list[str]] | None:
    """(index, audit path as hex) of request `request_id` among a receipt's request ids (in id order, the tree's leaf
    order) with their tree `t`; None when it is not one of them."""
    i = bisect.bisect_left(ids, request_id)
    if i == len(ids) or ids[i] != request_id:
        return None
    return i, [h.hex() for h in t.path(i)]


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------
def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _verify(args: argparse.Namespace, out: TextIO) -> int:
    results: list[bool] = []

    def load(path: str, what: str) -> Any:
        try:
            return json.loads(_read(path))
        except ValueError:
            print(f"FAIL: {what} {path} is not JSON", file=out)
            results.append(False)
            return None

    doc = load(args.receipt, "receipt")
    if doc is None:
        return 1
    ok, lines = verify_receipt(doc, args.public_key)
    results.append(ok)
    print("\n".join(lines), file=out)
    m = _manifest(doc) or {}
    if args.prev:
        prev = load(args.prev, "previous receipt")
        if prev is not None:
            ok, line = verify_chain(doc, prev)
            results.append(ok)
            print(line, file=out)
    elif m.get("prev_sha256") == ZERO_SHA256:
        print("chain: the first receipt for this client (prev_sha256 is zero)", file=out)
    else:
        print("chain: not checked (pass --prev with the previous receipt)", file=out)
    if args.proof:
        p = load(args.proof, "proof")
        if p is not None:
            ok, line = verify_proof(doc, p)
            results.append(ok)
            print(line, file=out)
    if args.rows:
        try:
            leaves = read_leaves(_read(args.rows))
        except ValueError:
            print(f"FAIL: rows: {args.rows} is not JSON lines, a JSON list or {{leaves: [...]}}", file=out)
            results.append(False)
        else:
            ok, lines = verify_rows(doc, leaves)
            results.append(ok)
            print("\n".join(lines), file=out)
    return 0 if all(results) else 1


def _seal(args: argparse.Namespace, out: TextIO) -> int:
    from .config import Settings  # noqa: PLC0415 - the verifier needs no settings
    from .replay import SIMULATION, simulated  # noqa: PLC0415

    settings = Settings()
    path = args.db or settings.db_path
    if path == ":memory:" or not os.path.exists(path):
        raise FileNotFoundError(f"no ledger at {path!r}; pass --db or set RATCHET_DB")
    led = Ledger(path, settings.golden_fraction, auto_day=True)
    sim = simulated(led)
    closed = None
    if sim:  # a finished simulator run: every day it logged is over, and its clock is synthetic
        led.db.close()
        led = Ledger(path, settings.golden_fraction)
        closed = led.day
    signer = bomdoc.Signer(settings.bom_signing_key) if settings.bom_signing_key else None
    config = SealConfig.from_settings(settings, signer, SIMULATION if sim else "live traffic")
    through = args.period
    sealed = seal_due(led, config, through=through, closed=closed)
    for period, client in sealed:
        print(f"sealed day {period} ({client or 'gateway'})", file=sys.stderr)
    if through is not None:
        doc = led.receipt(through, args.client)
        if doc is None:
            print(f"error: no requests {'from ' + args.client + ' ' if args.client else ''}on day {through}", file=sys.stderr)
            return 1
        print(json.dumps(doc, indent=2, ensure_ascii=False), file=out)
    elif not sealed:
        print("nothing to seal: every day that has ended is sealed", file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m ratchet.receipt", description="Verify or seal verified savings receipts (FR-15.5, ADR 0029)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check a receipt (file, or - for stdin)")
    v.add_argument("receipt")
    v.add_argument("--public-key", default=None, help="the base64 public key you trust (GET /ratchet/bom/public-key)")
    v.add_argument("--prev", help="the same client's previous receipt: check the chain")
    v.add_argument("--proof", help="a request's inclusion proof (GET /ratchet/receipts/{period}/proof/{id})")
    v.add_argument("--rows", help="the receipt's leaves (GET /ratchet/receipts/{period}/leaves): recompute everything")
    s = sub.add_parser("seal", help="seal every day that has ended in a ledger file, offline")
    s.add_argument("--db", help="the ledger file (default: RATCHET_DB)")
    s.add_argument("--period", type=int, help="seal through this ledger day and print its receipt")
    s.add_argument("--client", help="with --period: print this client's receipt (default: the gateway's)")
    return ap


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """`verify`: exit 0 when every check passes, 1 when one fails (also a file that is not JSON). `seal`: exit 0, 1
    when the day asked for has no requests. Both exit 2 on bad input (a missing file, an open day)."""
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "verify":
            return _verify(args, out)
        if args.client is not None and args.period is None:
            raise ValueError("--client needs --period")
        return _seal(args, out)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
