"""Offline simulation of Ratchet over several days and nights.

    python -m sim.simulate --days 8 --requests 2000 --bad-deploy-version 3
    python -m sim.simulate --workload research   # Scout's triage + read calls

Prints a day-by-day table and writes results JSON (for the dashboard). Uses mock models:
the numbers show how the loop behaves, not real model quality. Live mode gives real numbers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ratchet.config import Settings
from ratchet.cost import cost_report, record_simulated_day
from ratchet.ledger import Ledger
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy
from ratchet.router import Ratchet

from .mock_models import MockFleet, MockTeacher
from .world import WORKLOADS, make_stream, night_task


def build(args: argparse.Namespace, db: str = ":memory:"):
    settings = Settings(
        mode="mock",
        teacher_model="nvidia/Nemotron-3-Ultra-550b-a55b",
        confidence_threshold=args.threshold,
        samples=3,
        audit_rate=args.audit_rate,
    )
    ledger = Ledger(db, golden_fraction=0.10)
    corrupt = {f"student-v{args.bad_deploy_version}"} if args.bad_deploy_version else set()
    fleet = MockFleet(corrupt_on_deploy=corrupt, policy=settings.gen_policy())
    ratchet = Ratchet(settings, ledger, MockTeacher(settings.teacher_reasoning_tokens), fleet.resolve, seed=args.seed)
    gate = ReleaseGate(ledger, ReleasePolicy(min_shadow=args.min_shadow, min_audits=args.min_audits))
    return settings, ledger, fleet, ratchet, gate


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
        )
        # The day's simulated compute on the synthetic clock: writes compute intervals only, so no other number moves.
        trained = ledger.version(rep.version) if rep.trained and rep.version is not None else None
        record_simulated_day(ledger, policy, day, trained)
        nights.append({"night": day, "trained": rep.trained, "version": rep.version, "admitted": rep.admitted, "eval": rep.eval})
    return {
        "days": ledger.stats_by_day(),
        "nights": nights,
        "versions": [v.__dict__ for v in ledger.versions()],
        "events": ledger.events(),
        "totals": ledger.totals(),
        "bill_of_materials": ledger.bill_of_materials(),
        "cost": cost_report(ledger, settings),
    }


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
    ap.add_argument("--out", default="sim_results.json")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.drift_day == 0:
        args.drift_day = None
    res = run(args)
    print(table(res))
    Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
