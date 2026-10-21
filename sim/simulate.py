"""Offline simulation of Ratchet over several days and nights.

    python -m sim.simulate --days 8 --requests 2000 --bad-deploy-version 3
    python -m sim.simulate --workload research   # Scout's triage + read calls
    python -m sim.simulate --bad-deploy-version 3 --compare-release   # Wilson vs the anytime-valid gate, same seed
    python -m sim.simulate --calibration ltt --paired-gate   # certified thresholds and the paired gate (ADR 0026)
    python -m sim.simulate --db sim.db && python -m ratchet.replay --db sim.db   # replay the run's ledger (ADR 0028)

Prints a day-by-day table and writes results JSON (for the dashboard). Uses mock models:
the numbers show how the loop behaves, not real model quality. Live mode gives real numbers.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ratchet.certificate import CALIBRATIONS
from ratchet.config import Settings
from ratchet.cost import cost_report, record_simulated_day
from ratchet.ledger import Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet

from .mock_models import MockFleet, MockTeacher
from .world import WORKLOADS, make_stream, night_task

# The canary rules --release picks (ADR 0025): today's Wilson bound, or the anytime-valid e-process.
RELEASE_RULES = ("wilson", "sequential")
_SUBJECT = re.compile(r"^v(\d+)\b")  # gate events name their version first ('v3 rolled back: ...')


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
    )
    ratchet = Ratchet(settings, ledger, MockTeacher(settings.teacher_reasoning_tokens), fleet.resolve, seed=args.seed)
    policy = ReleasePolicy(
        min_shadow=args.min_shadow,
        min_audits=args.min_audits,
        sequential=int(getattr(args, "release", "wilson") == "sequential"),
        promote_evidence=int(bool(getattr(args, "promote_evidence", False))),
        paired_gate=int(bool(getattr(args, "paired_gate", False))),
        min_certified_precision=getattr(args, "min_certified", 0.0),
    )
    gate = ReleaseGate(ledger, policy)
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


def run(args: argparse.Namespace, db: str = ":memory:") -> dict:
    settings, ledger, fleet, ratchet, gate = build(args, db)
    policy = settings.cost_policy()  # GPU rates and the simulated endpoint's hours a day (RATCHET_MOCK_ENDPOINT_HOURS)
    workload = getattr(args, "workload", "tickets")
    stream = make_stream(workload, seed=args.seed, drift_day=args.drift_day)
    nights = []
    for day in range(1, args.days + 1):
        ledger.day = day
        ticks = max(1, args.requests // 10)
        for i in range(args.requests):
            t = stream.sample(day)
            ratchet.handle(t.messages(), t.task)
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
        )
        # The day's simulated compute on the synthetic clock: writes compute intervals only, so no other number moves.
        trained = ledger.version(rep.version) if rep.trained and rep.version is not None else None
        record_simulated_day(ledger, policy, day, trained)
        nights.append({"night": day, "trained": rep.trained, "version": rep.version, "admitted": rep.admitted, "eval": rep.eval})
    served = ledger.served_counts()
    rolled = rollbacks(ledger, served)
    return {
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
    }


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
    lines.append("\nevents:")
    lines += [f"  day {e['day']}: [{e['kind']}] {e['detail']}" for e in res["events"]]
    return "\n".join(lines)


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
    ap.add_argument("--workload", choices=WORKLOADS, default="tickets", help="tickets, Scout research calls, or both")
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
        "--compare-release",
        action="store_true",
        help="run twice with the same seed, once per --release rule, and compare the rolled-back versions",
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
    if args.db is not None:
        if args.compare_release:
            ap.error("--db holds one run's ledger; it cannot be used with --compare-release (two runs)")
        if args.db == ":memory:":
            ap.error("--db names a file; without --db the run's ledger stays in memory")
        if Path(args.db).exists():
            ap.error(f"--db {args.db} exists already; name a new file so two runs never share a ledger")
    if args.compare_release:
        results = compare_release(args)
        print(compare_table(results))
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\nwrote {args.out}")
        return
    res = run(args, db=args.db or ":memory:")
    print(table(res))
    Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    print(f"\nwrote {args.out}")
    if args.db is not None:
        print(f"wrote the ledger to {args.db} (simulation, mock models); replay it: python -m ratchet.replay --db {args.db}")


if __name__ == "__main__":
    main()
