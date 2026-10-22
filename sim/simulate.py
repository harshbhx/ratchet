"""Offline simulation of Ratchet over several days and nights.

    python -m sim.simulate --days 8 --requests 2000 --bad-deploy-version 3
    python -m sim.simulate --workload research   # Scout's triage + read calls
    python -m sim.simulate --bad-deploy-version 3 --compare-release   # Wilson vs the anytime-valid gate, same seed
    python -m sim.simulate --calibration ltt --paired-gate   # certified thresholds and the paired gate (ADR 0026)
    python -m sim.simulate --db sim.db && python -m ratchet.replay --db sim.db   # replay the run's ledger (ADR 0028)
    python -m sim.simulate --receipts --teacher-reasoning-mean 420   # savings receipts vs the true cost (ADR 0029)
    python -m sim.simulate --workload schema     # structured outputs (sim/generic.py)
    python -m sim.simulate --teacher-noise 0.15 --teacher-noise-day 3   # an unstable teacher from day 3 (ADR 0033)

Prints a day-by-day table and writes results JSON (for the dashboard). Uses mock models:
the numbers show how the loop behaves, not real model quality. Live mode gives real numbers.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from ratchet import kpis, receipts
from ratchet.certificate import CALIBRATIONS
from ratchet.config import MAX_MOCK_REASONING_TOKENS, Settings
from ratchet.cost import cost_report, record_simulated_day
from ratchet.drift import pct as drift_pct
from ratchet.drift import task_label
from ratchet.ledger import Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet
from ratchet.teacher_watch import Relabel, relabel_with

from .mock_models import DEFAULT_REASONING_CV, MockFleet, MockTeacher
from .world import WORKLOADS, make_stream, night_task

# The canary rules --release picks (ADR 0025): today's Wilson bound, or the anytime-valid e-process.
RELEASE_RULES = ("wilson", "sequential")
_SUBJECT = re.compile(r"^v(\d+)\b")  # gate events name their version first ('v3 rolled back: ...')
SIMULATION = "simulation (mock models)"


def build(args: argparse.Namespace, db: str = ":memory:"):
    settings = Settings(
        mode="mock",
        teacher_model="nvidia/Nemotron-3-Ultra-550b-a55b",
        confidence_threshold=args.threshold,
        samples=3,
        audit_rate=args.audit_rate,
        canary_audit_rate=getattr(args, "canary_audit_rate", None),
        calibration=getattr(args, "calibration", "point"),
        cert_target=getattr(args, "cert_target", 0.95),
        cert_delta=getattr(args, "cert_delta", 0.05),
    )
    cert = settings.cert_policy()
    ledger = Ledger(db, golden_fraction=0.10)
    corrupt = {f"student-v{args.bad_deploy_version}"} if args.bad_deploy_version else set()
    fleet = MockFleet(
        corrupt_on_deploy=corrupt,
        policy=settings.gen_policy(),
        calibration=cert.calibration,
        cert_target=cert.target,
        cert_delta=cert.delta,
        min_task_n=settings.min_task_golden_rows(),
        per_task=settings.per_task_calibration,
    )
    # --teacher-reasoning-mean: the mock teacher's billed reasoning varies around that mean (FR-15.5), so the formula's
    # 300-token assumption is off and the receipts measure by how much; unset, it bills exactly the assumption.
    mean = getattr(args, "teacher_reasoning_mean", None)
    cv = getattr(args, "teacher_reasoning_cv", DEFAULT_REASONING_CV) if mean is not None else 0.0
    teacher = MockTeacher(settings.teacher_reasoning_tokens, reasoning_mean=mean, reasoning_cv=cv)
    ratchet = Ratchet(settings, ledger, teacher, fleet.resolve, seed=args.seed)
    policy = ReleasePolicy(
        min_shadow=args.min_shadow,
        min_audits=args.min_audits,
        sequential=int(getattr(args, "release", "wilson") == "sequential"),
        promote_evidence=int(bool(getattr(args, "promote_evidence", False))),
        paired_gate=int(bool(getattr(args, "paired_gate", False))),
        min_certified_precision=getattr(args, "min_certified", 0.0),
        drift_alarm=getattr(args, "drift_alarm", 100.0),
        drift_audit_boost=getattr(args, "drift_boost", 1.0),
        drift_early_night=int(bool(getattr(args, "drift_early_night", False))),
        teacher_watch=int(getattr(args, "teacher_watch", 1)),
    )
    gate = ReleaseGate(ledger, policy)
    ratchet.audit_boost = gate.audit_boost  # the drift audit boost (FR-6.40): empty unless --drift-boost is above 1
    return settings, ledger, fleet, ratchet, gate


def rollbacks(ledger: Ledger, served: dict[int, int]) -> list[dict[str, Any]]:
    """Every rolled-back version: the audits and agreements it had (it serves nothing after a rollback, so these are
    the counts the gate decided on), the answers it served (its exposure, from `served`: `Ledger.served_counts()`) and
    the day of its rollback."""
    days = {}
    for e in ledger.events():
        m = _SUBJECT.match(e["detail"]) if e["kind"] == "rollback" else None
        if m:
            days.setdefault(int(m.group(1)), e["day"])
    out = []
    for v in ledger.versions(["rolled_back"]):
        n, s = ledger.audit_counts(v.id)
        out.append({"version": v.id, "audits": n, "agreed": s, "served": served.get(v.id, 0), "day": days.get(v.id)})
    return out


def receipt_day(ledger: Ledger, day: int, truth: dict[int, int]) -> dict[str, Any] | None:
    """One day's sealed gateway receipt next to the truth (simulation): the calibrated baseline, the billable set's
    baseline and its one-sided 95% lower bound, and the true all-teacher cost of the same requests (`truth`: request id
    -> nano-USD, from asking the mock teacher about every request, kept in memory only). `held` says whether the bound
    was at or below the truth."""
    doc = ledger.receipt(day)
    if doc is None:
        return None
    m = doc["manifest"]
    tasks = m["calibration"]["tasks"]
    bill = m["billing"]["billable"]
    leaves = [receipts.leaf(r) for r in ledger.period_rows(day)]
    true_billable = sum(truth.get(lf["id"], 0) for lf in leaves if receipts.billable(lf, tasks))
    lower = bill["baseline_nusd"] - m["savings"]["margin_nusd"]
    usd = receipts.NANO
    return {
        "day": day,
        "ratio": {t: c["ratio"] for t, c in tasks.items()},
        "status": {t: c["status"] for t, c in tasks.items()},
        "audits": m["totals"]["audits"],
        "baseline_recorded_usd": m["totals"]["baseline_recorded_nusd"] / usd,
        "baseline_calibrated_usd": m["totals"]["baseline_calibrated_nusd"] / usd,
        "true_baseline_usd": sum(truth.get(lf["id"], 0) for lf in leaves) / usd,
        "billable_baseline_usd": bill["baseline_nusd"] / usd,
        "billable_baseline_lo_usd": lower / usd,
        "true_billable_baseline_usd": true_billable / usd,
        "held": lower <= true_billable,
        "billable_lo_usd": m["savings"]["billable_lo_nusd"] / usd,
        "fee_usd": m["billing"]["fee_nusd"] / usd,
        "basis": m["billing"]["basis"],
        "receipt_sha256": doc["sha256"],
    }


def relabel(settings: Settings, ratchet: Ratchet) -> Relabel:
    """Night School's teacher-watch probes and adjudications (FR-5.40, FR-5.41) against the simulation's mock teacher,
    as the mock gateway runs them. At teacher noise 0 every answer matches its stored label, so no number moves."""
    probes, adjudicate = settings.teacher_watch_limits()
    return relabel_with(
        ratchet.teacher,
        settings.teacher_model,
        ratchet.gen,
        probes,
        adjudicate,
        redact_pii=settings.redact_pii,
        price=settings.price(settings.teacher_model),
    )


def run(args: argparse.Namespace, db: str = ":memory:") -> dict:
    settings, ledger, fleet, ratchet, gate = build(args, db)
    policy = settings.cost_policy()  # GPU rates and the simulated endpoint's hours a day (RATCHET_MOCK_ENDPOINT_HOURS)
    workload = getattr(args, "workload", "tickets")
    stream = make_stream(workload, seed=args.seed, drift_day=args.drift_day)
    # --receipts: seal each day's savings receipt after its night (FR-15.5), and keep each request's true all-teacher
    # cost in memory (the mock teacher asked about every request; never stored, no random draw), to show whether the
    # receipts' lower bound held. Sealing reads the ledger and writes receipts only, so no other number moves.
    sealing = bool(getattr(args, "receipts", False))
    seal_config = receipts.SealConfig.from_settings(settings, None, SIMULATION) if sealing else None
    price = settings.price(settings.teacher_model)
    receipt_days = []
    watch = relabel(settings, ratchet)
    noise, noise_day = getattr(args, "teacher_noise", 0.0), getattr(args, "teacher_noise_day", 1)
    nights = []
    for day in range(1, args.days + 1):
        ledger.day = day
        if noise and day >= noise_day:  # an unstable teacher from this day on (fault injection, ADR 0033)
            ratchet.teacher.noise = noise
        ticks = max(1, args.requests // 10)
        truth: dict[int, int] = {}
        for i in range(args.requests):
            t = stream.sample(day)
            res = ratchet.handle(t.messages(), t.task, params=t.params)  # params: a schema or tools; None for tickets
            if sealing and res.request_id is not None:
                c = ratchet.teacher.complete(t.messages(), settings.teacher_model)[0]
                truth[res.request_id] = receipts.nusd(price.cost(c.in_tokens, c.out_tokens)) or 0
            if (i + 1) % ticks == 0:
                gate.tick()
        rep = run_night(
            ledger,
            fleet,
            fleet,
            gate,
            settings.student_base_model,
            min_new_examples=args.min_new,
            task=night_task(workload),
            redact_pii=settings.redact_pii,
            store_examples=settings.store_examples,
            relabel=watch,
        )
        # The day's simulated compute on the synthetic clock: writes compute intervals only, so no other number moves.
        trained = ledger.version(rep.version) if rep.trained and rep.version is not None else None
        record_simulated_day(ledger, policy, day, trained)
        nights.append({"night": day, "trained": rep.trained, "version": rep.version, "admitted": rep.admitted, "eval": rep.eval})
        if seal_config is not None:  # the day is over: no request comes for it after its night
            receipts.seal_due(ledger, seal_config, through=day, closed=day)
            summary = receipt_day(ledger, day, truth)
            if summary is not None:
                receipt_days.append(summary)
    served = ledger.served_counts()
    rolled = rollbacks(ledger, served)
    res = {
        "days": ledger.stats_by_day(),
        "nights": nights,
        "versions": [v.__dict__ for v in ledger.versions()],
        "events": ledger.events(),
        "totals": ledger.totals(),
        "bill_of_materials": ledger.bill_of_materials(),
        "cost": cost_report(ledger, settings),
        # Answers each rolled-back version served before its rollback (its exposure), the counts its rollback was decided
        # on, and the answers every version served (simulation, mock models).
        "exposure": {r["version"]: r["served"] for r in rolled},
        "rollbacks": rolled,
        "served": served,
        # PRD §8.1's success metrics, computed from the ledger as the gateway's /ratchet/kpis does (simulation numbers).
        "kpis": kpis.report(ledger, settings.kpi_window(), args.min_audits, settings.mode),
        # The drift monitor's streams and alarms (FR-16.6, ADR 0032); simulation, mock models.
        "drift": gate.drift_status(),
        # The teacher watch's checks, contested labels and freeze (FR-16.7, ADR 0033); simulation, mock models.
        "teacher": gate.teacher_status(),
    }
    if sealing:  # each day's receipt next to the true all-teacher cost (simulation, mock models); only with --receipts
        res["receipts"] = receipt_days
    return res


def compare_release(args: argparse.Namespace) -> dict[str, dict]:
    """The same run (same arguments and seed) under each canary rule, for --compare-release. --promote-evidence applies
    to the sequential run only (the Wilson rule promotes on the point estimate)."""
    out = {}
    for rule in RELEASE_RULES:
        evidence = bool(getattr(args, "promote_evidence", False)) and rule == "sequential"
        out[rule] = run(argparse.Namespace(**{**vars(args), "release": rule, "promote_evidence": evidence}))
    return out


def compare_table(results: dict[str, dict]) -> str:
    """One row per rule and per version rolled back under either rule: its final state, the audits it had when it was
    rolled back and the day ('-' when that rule did not roll it back), the answers it served (its exposure) and the run's
    total savings."""
    ids = sorted({r["version"] for res in results.values() for r in res["rollbacks"]})
    lines = ["Wilson vs anytime-valid release gate (simulation, mock models)"]
    if not ids:
        lines.append("no version was rolled back under either rule")
    lines.append(f"{'rule':<11} {'version':>7} {'state':<12} {'audits':>6} {'served':>6} {'day':>4} {'saved':>6}")
    for vid in ids:
        for rule, res in results.items():
            state = next((v["state"] for v in res["versions"] if v["id"] == vid), "not trained")
            row = next((r for r in res["rollbacks"] if r["version"] == vid), None)
            audits, day = (str(row["audits"]), str(row["day"])) if row else ("-", "-")
            served = res["served"].get(vid, 0)
            saved = res["totals"]["savings_pct"]
            lines.append(f"{rule:<11} {'v' + str(vid):>7} {state:<12} {audits:>6} {served:>6} {day:>4} {saved:>6.0%}")
    return "\n".join(lines)


def table(res: dict) -> str:
    lines = [f"{'day':>3} {'student':>8} {'escal.':>7} {'audit ok':>8} {'$/1k':>8} {'all-Ultra':>9} {'saved':>6}"]
    for d in res["days"]:
        aa = "-" if d["audit_agreement"] is None else f"{d['audit_agreement']:.0%}"
        lines.append(
            f"{d['day']:>3} {d['student_share']:>8.0%} {d['escalation_rate']:>7.0%} {aa:>8} "
            f"{d['cost_per_1k']:>8.3f} {d['baseline_per_1k']:>9.3f} {d['savings_pct']:>6.0%}"
        )
    t = res["totals"]
    lines.append(
        f"\ntotal: {t['requests']} requests, ${t['cost_usd']:.2f} vs ${t['baseline_usd']:.2f} "
        f"all-Ultra -> saved {t['savings_pct']:.0%}"
    )
    c = res.get("cost")
    if c:  # the same run with the student priced by the GPU-hours it ran (ADR 0023), labelled as simulation
        gpu = c["views"]["gpu_hours"]
        pct = "-" if gpu["savings_pct"] is None else f"{gpu['savings_pct']:.0%}"
        lines.append(
            f"by GPU-hours (simulation, {c['prices']['label']}): ${gpu['total_usd']:.2f} -> saved {pct} "
            f"({c['endpoint_hours']:.0f} endpoint hours, {c['training_hours']:.2f} training hours); "
            f"recommendation: {c['recommendation']}"
        )
    certs = [(n["version"], n["eval"]["certificate"]) for n in res["nights"] if n["eval"] and "certificate" in n["eval"]]
    if certs:  # what each trained version's held-out golden rows certify (ADR 0026), labelled as simulation
        lines.append("\ncertificates (simulation, mock models):")
        for vid, c in certs:
            verdict = "certified" if c["certified"] else "not certified"
            lines.append(
                f"  v{vid}: {c['served'] - c['errors']}/{c['served']} served answers agree at threshold {c['threshold']:g}; "
                f"lower bound {c['lcb']:.1%} vs {c['target'] * 100:.3g}% (delta {c['delta']:g}, {c['calibration']}): {verdict}"
            )
    if res.get("receipts"):  # each day's savings receipt next to the truth (ADR 0029), labelled as simulation
        lines.append("\n" + receipt_table(res["receipts"]))
    k = res.get("kpis")
    if k:  # success metrics (PRD §8.1) over the last days with traffic, from mock models
        lines.append(f"\nsuccess metrics (simulation; SM-1 to SM-3 and NFR-1 over the last {k['window']['days']} days):")
        lines += [f"  {kpi_line(rid, m)}" for rid, m in k["metrics"].items()]
    drift = res.get("drift")
    if drift and drift["enabled"]:  # the label-free drift alarm (ADR 0032), labelled as simulation
        alarmed = [x for x in drift["streams"] if x["alarm"]]
        where = ", ".join(f"v{x['version']} ({task_label(x['task'])}) on day {x['alarm_day']}" for x in alarmed) or "none"
        each, family = drift_pct(drift["false_alarm_bound"]), drift_pct(drift["family_bound"])
        lines.append(
            f"\ndrift (simulation, mock models): {drift['monitored']} streams at threshold {drift['threshold']:g} "
            f"(false alarm <= {each} each, <= {family} in all); alarms: {where}"
        )
    teacher = res.get("teacher")
    if teacher and teacher["checks"]:  # the teacher watch (ADR 0033), labelled as simulation
        agree = "-" if teacher["self_agreement"] is None else f"{teacher['self_agreement']:.1%}"
        lines.append(
            f"\nteacher watch (simulation, mock models): epoch {teacher['epoch']}, {teacher['checks']} first re-tests, "
            f"{teacher['disagreements']} disagree (self-agreement {agree}, e-value {teacher['e_value']:.3g} vs "
            f"{teacher['threshold']:.0f}); {teacher['contested']} labels contested; "
            f"{'Night School frozen' if teacher['frozen'] else 'not frozen'}"
        )
    lines.append("\nevents:")
    lines += [f"  day {e['day']}: [{e['kind']}] {e['detail']}" for e in res["events"]]
    return "\n".join(lines)


def receipt_table(days: list[dict[str, Any]]) -> str:
    """The receipts' calibrated baseline and the lower bound billed on, next to the true all-teacher cost (simulation)."""
    lines = [
        "savings receipts (simulation, mock models): the billable baseline's one-sided 95% lower bound vs the true cost",
        f"{'day':>3} {'R by task':<34} {'recorded':>9} {'calibrated':>10} {'true':>9} {'billable lo':>11} "
        f"{'true bill.':>10} {'held':>4} {'fee':>8}",
    ]
    for d in days:
        ratio = ", ".join(f"{t}={r}" if r is not None else f"{t}=-" for t, r in d["ratio"].items()) or "-"
        if len(ratio) > 34:
            ratio = ratio[:31] + "..."
        lines.append(
            f"{d['day']:>3} {ratio:<34} {d['baseline_recorded_usd']:>9.4f} {d['baseline_calibrated_usd']:>10.4f} "
            f"{d['true_baseline_usd']:>9.4f} {d['billable_baseline_lo_usd']:>11.4f} {d['true_billable_baseline_usd']:>10.4f} "
            f"{'yes' if d['held'] else 'NO':>4} {d['fee_usd']:>8.4f}"
        )
    held = sum(1 for d in days if d["held"])
    lines.append(f"the lower bound held on {held} of {len(days)} days (USD; a one-sided 95% bound misses about 1 day in 20)")
    return "\n".join(lines)


def kpi_line(rid: str, m: dict) -> str:
    """One success metric as a line: its id, value, target and verdict."""
    if m["value"] is None:
        value = "-"
    elif m["unit"] == "fraction":
        value = f"{m['value']:.1%}"
    elif m["unit"] == "ms":
        value = f"{m['value']:.0f} ms"
    else:
        value = f"{m['value']} {m['unit']}"
    target = f"{m['target']:.0%}" if m["unit"] == "fraction" else f"{m['target']:g} {m['unit']}"
    verdict = {True: "met", False: "not met", None: "not known yet"}[m["met"]]
    return f"{rid:<6} {value:>10}  target {m['op']} {target:<10} {verdict}"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Ratchet offline simulation")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--requests", type=int, default=2000, help="requests per day")
    ap.add_argument("--threshold", type=float, default=0.80)
    ap.add_argument("--audit-rate", type=float, default=0.10)
    ap.add_argument("--min-shadow", type=int, default=100)
    ap.add_argument("--min-audits", type=int, default=15)
    ap.add_argument("--min-new", type=int, default=50)
    ap.add_argument("--drift-day", type=int, default=5, help="day a new product launches (0 = never)")
    ap.add_argument(
        "--bad-deploy-version",
        type=int,
        default=0,
        help="fault injection: this student version is pushed corrupted to production",
    )
    ap.add_argument(
        "--workload",
        choices=WORKLOADS,
        default="tickets",
        help="tickets, Scout research calls, both (mixed), structured outputs (schema) or tool requests (tools)",
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--release",
        choices=RELEASE_RULES,
        default="wilson",
        help="canary rule: the Wilson bound per stage, or the anytime-valid e-process (ADR 0025)",
    )
    ap.add_argument(
        "--promote-evidence",
        action="store_true",
        help="with --release sequential: promote a canary stage only on an e-value that precision is above the floor",
    )
    ap.add_argument(
        "--canary-audit-rate",
        type=float,
        default=None,
        help="the canary's own audit rate, more than 0 and at most 1 (default: --audit-rate)",
    )
    ap.add_argument(
        "--calibration",
        choices=CALIBRATIONS,
        default="point",
        help="threshold rule: the point rule at 97%%, or Learn-then-Test over a fixed grid (ADR 0026)",
    )
    ap.add_argument("--cert-target", type=float, default=0.95, help="the precision a certificate must show")
    ap.add_argument("--cert-delta", type=float, default=0.05, help="a certificate's error rate")
    ap.add_argument(
        "--paired-gate",
        action="store_true",
        help="compare each candidate with the live version row by row on the same golden rows (exact McNemar tests)",
    )
    ap.add_argument(
        "--min-certified",
        type=float,
        default=0.0,
        help="reject a candidate whose certified lower bound is below this (0 = off)",
    )
    ap.add_argument(
        "--drift-alarm",
        type=float,
        default=100.0,
        help="the drift monitor's alarm threshold: a false alarm has probability at most 1/threshold per stream (0 = off)",
    )
    ap.add_argument(
        "--drift-boost",
        type=float,
        default=1.0,
        help="after a drift alarm, multiply the version's audit rate by this, up to 0.5 (1 = off)",
    )
    ap.add_argument(
        "--drift-early-night",
        action="store_true",
        help="after a drift alarm, Night School trains on half the usual new examples",
    )
    ap.add_argument(
        "--teacher-noise",
        type=float,
        default=0.0,
        help="fault injection: from --teacher-noise-day, the mock teacher changes a label with this probability (0 = never)",
    )
    ap.add_argument("--teacher-noise-day", type=int, default=1, help="the day the mock teacher becomes noisy")
    ap.add_argument(
        "--teacher-watch",
        type=int,
        choices=(0, 1),
        default=1,
        help="the teacher watch: quarantine, freeze, ceiling warning, probes and adjudication (0 = off; checks still recorded)",
    )
    ap.add_argument(
        "--compare-release",
        action="store_true",
        help="run twice with the same seed, once per --release rule, and compare the rolled-back versions",
    )
    ap.add_argument(
        "--receipts",
        action="store_true",
        help="seal each day's savings receipt and print it next to the true all-teacher cost (ADR 0029)",
    )
    ap.add_argument(
        "--teacher-reasoning-mean",
        type=float,
        default=None,
        help="the mock teacher bills this many reasoning tokens on average instead of exactly 300 (e.g. 420)",
    )
    ap.add_argument(
        "--teacher-reasoning-cv",
        type=float,
        default=DEFAULT_REASONING_CV,
        help=f"with --teacher-reasoning-mean: the spread (coefficient of variation, default {DEFAULT_REASONING_CV})",
    )
    ap.add_argument("--out", default="sim_results.json")
    ap.add_argument(
        "--db",
        default=None,
        help="write the run's ledger to this SQLite file (it must not exist yet), e.g. for python -m ratchet.replay",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = parser()
    args = ap.parse_args(argv)
    if args.drift_day == 0:
        args.drift_day = None
    if args.promote_evidence and args.release != "sequential" and not args.compare_release:
        ap.error("--promote-evidence needs --release sequential")
    if args.canary_audit_rate is not None and not 0 < args.canary_audit_rate <= 1:
        ap.error(f"--canary-audit-rate must be more than 0 and at most 1, not {args.canary_audit_rate}")
    for flag, value in (("--cert-target", args.cert_target), ("--cert-delta", args.cert_delta)):
        if not 0 < value < 1:
            ap.error(f"{flag} must be strictly between 0 and 1, not {value}")
    if not 0 <= args.min_certified <= 1:
        ap.error(f"--min-certified must be from 0 to 1, not {args.min_certified}")
    mean = args.teacher_reasoning_mean
    if mean is not None and not (math.isfinite(mean) and 0 < mean <= MAX_MOCK_REASONING_TOKENS):
        ap.error(f"--teacher-reasoning-mean must be more than 0 and at most {MAX_MOCK_REASONING_TOKENS:,}, not {mean}")
    if not (math.isfinite(args.teacher_reasoning_cv) and 0 <= args.teacher_reasoning_cv <= 3):
        ap.error(f"--teacher-reasoning-cv must be from 0 to 3, not {args.teacher_reasoning_cv}")
    if args.db is not None:
        if args.compare_release:
            ap.error("--db holds one run's ledger; it cannot be used with --compare-release (two runs)")
        if args.db == ":memory:":
            ap.error("--db names a file; without --db the run's ledger stays in memory")
        if Path(args.db).exists():
            ap.error(f"--db {args.db} exists already; name a new file so two runs never share a ledger")
    if not (args.drift_alarm == 0 or args.drift_alarm > 1):
        ap.error(f"--drift-alarm must be more than 1, or 0 (off), not {args.drift_alarm}")
    if not args.drift_boost >= 1:
        ap.error(f"--drift-boost must be 1 or more, not {args.drift_boost}")
    if args.drift_alarm == 0 and (args.drift_boost > 1 or args.drift_early_night):
        ap.error("--drift-boost and --drift-early-night act on drift alarms; --drift-alarm 0 turns the monitor off")
    if not 0 <= args.teacher_noise <= 1:
        ap.error(f"--teacher-noise must be from 0 to 1, not {args.teacher_noise}")
    if args.teacher_noise_day < 1:
        ap.error(f"--teacher-noise-day must be 1 or more, not {args.teacher_noise_day}")
    if args.compare_release:
        results = compare_release(args)
        print(compare_table(results))
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\nwrote {args.out}")
        return
    res = run(args, db=args.db or ":memory:")
    print(table(res))
    print(f"\nsimulation: mock models, workload {args.workload}, seed {args.seed}; not a measure of real model quality")
    Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    print(f"\nwrote {args.out}")
    if args.db is not None:
        print(f"wrote the ledger to {args.db} (simulation, mock models); replay it: python -m ratchet.replay --db {args.db}")


if __name__ == "__main__":
    main()
