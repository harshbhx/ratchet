"""Check that v0.4.0's behaviour is unchanged on three workloads, against tests/fixtures/reference.json.

    python scripts/reference_check.py                       # run each workload and compare (exit 1 on a difference)
    python scripts/reference_check.py --only mixed-seed1    # one workload (repeat --only for more)
    python scripts/reference_check.py --write --captured-from "<release or commit>"   # rewrite the fixture

The fixture holds three runs of the simulator with mock models, captured from v0.4.0 (commit 1f9b18c): `tickets-seed7`
(10 days of 2,000 requests, a product launch on day 5 and a corrupted v3: the reference simulation), `mixed-seed1` and
`research-seed7` (8 days of 1,500 requests each). For each it pins every simulator input, every v0.4 key of each day's
stats (`DAY_KEYS`), the totals, each version's final state, training-set size and dataset hash, and the event log. Every later
feature defaults to v0.4's behaviour (ADR 0024), so every change must pass this check; CI runs it, and
`make reference` runs it locally (about 25 s).

One feature is on by default and moves the multi-task runs by design: per-task calibration (FR-14.1, ADR 0040) holds
each task of the mixed and research workloads to its own threshold. The runs here switch it off (`LEGACY_ENV`), so they
still reproduce v0.4.0 exactly; with one task it changes nothing, and the tickets run is also checked with it on (the
session's reference simulation in tests/test_system.py).

What counts as unchanged:

- Floats agree within a relative 1e-9 (an index or a query plan may add the same floats in another order); everything
  else exactly.
- A day's stats and the totals may gain keys (stats only grow); a pinned key may not change or disappear.
- Events compare as (day, kind, version) in order, and each pinned detail must be a prefix of the fresh one: a feature
  may append evidence to a message, never change what it said. New advisory kinds (`IGNORED_KINDS`) are left out.
- A simulator flag added later is not compared; a changed or removed default of a pinned input is.

Regenerate only when a change is meant to move these runs, review the diff (one day or event per line), and add a
CHANGELOG line that says why. The numbers are simulation output from mock models, not measurements.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for _path in (ROOT, SCRIPTS):  # the repo for `sim` and `ratchet`, scripts/ for the reference simulation's helpers
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import regen_sim_reference as regen  # noqa: E402  (shares its clean environment and version parsing)

from sim.simulate import parser, run  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "reference.json"
CONFIGS: dict[str, tuple[str, ...]] = {
    "tickets-seed7": regen.ARGV,  # the reference simulation, also pinned by tests/fixtures/sim_reference_seed7.json
    "mixed-seed1": ("--seed", "1", "--days", "8", "--requests", "1500", "--workload", "mixed"),
    "research-seed7": ("--seed", "7", "--days", "8", "--requests", "1500", "--workload", "research"),
}
# Settings the runs use beside the defaults: they switch off the features that change these runs by design, so the
# fixture stays v0.4.0's output (per-task calibration, FR-14.1, ADR 0040; tested on its own in tests/test_calibration.py).
LEGACY_ENV: dict[str, str] = {"RATCHET_PER_TASK_CALIBRATION": "0"}
# Advisory event kinds later features write without changing a decision; they are not part of v0.4's log.
IGNORED_KINDS = frozenset({"drift", "teacher_drift", "teacher_model_changed", "warning", "student_healed", "live_retired"})
# v0.4.0's keys of `Ledger.stats_by_day` rows; keys added since are not pinned, so the fixture is exactly v0.4.0's output.
DAY_KEYS = (
    "day", "requests", "student_share", "escalation_rate", "cost_per_1k", "baseline_per_1k", "savings_pct",
    "avg_latency_ms", "audit_agreement",
)  # fmt: skip
VERSION_KEYS = ("id", "state", "traffic_pct", "train_examples", "dataset_hash")
REL_TOL = 1e-9
MAX_REPORTED = 20  # differences printed per workload


def args_for(name: str) -> argparse.Namespace:
    return parser().parse_args([*CONFIGS[name], "--out", os.devnull])


def inputs(name: str) -> dict[str, Any]:
    """Every simulator input of a workload, defaults included."""
    return {k: v for k, v in sorted(vars(args_for(name)).items()) if k != "out"}


def summarize(name: str, res: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a `sim.simulate.run` result the fixture pins, for workload `name`."""
    return {
        "argv": list(CONFIGS[name]),
        "inputs": inputs(name),
        "days": [{k: d[k] for k in DAY_KEYS if k in d} for d in res["days"]],
        "totals": dict(res["totals"]),
        "versions": [{k: v[k] for k in VERSION_KEYS} for v in res["versions"]],
        "events": [
            [e["day"], e["kind"], regen._version(e["detail"]), e["detail"]]
            for e in res["events"]
            if e["kind"] not in IGNORED_KINDS
        ],
    }


def capture(name: str) -> dict[str, Any]:
    """Run one workload on the default settings (every RATCHET_* variable unset while it runs) plus `LEGACY_ENV`, and
    summarize it."""
    with regen._default_settings():
        os.environ.update(LEGACY_ENV)  # `_default_settings` puts the caller's RATCHET_* variables back afterwards
        try:
            return summarize(name, run(args_for(name)))
        finally:
            for key in LEGACY_ENV:
                os.environ.pop(key, None)


def _same(want: Any, have: Any) -> bool:
    if isinstance(want, float) or isinstance(have, float):
        if isinstance(want, bool) or isinstance(have, bool):
            return False
        if not isinstance(want, (int, float)) or not isinstance(have, (int, float)):
            return False
        return math.isclose(want, have, rel_tol=REL_TOL, abs_tol=1e-12)
    return want == have


def compare(name: str, want: Mapping[str, Any], have: Mapping[str, Any]) -> list[str]:
    """Every way `have` (a fresh `summarize`) differs from `want` (the fixture's entry), as readable lines."""
    out: list[str] = []
    fresh_inputs = regen.pinned_inputs(want["inputs"], have["inputs"])
    for k, v in want["inputs"].items():
        if fresh_inputs[k] != v:
            out.append(f"input {k}: pinned {v!r}, now {fresh_inputs[k]!r}")
    if len(want["days"]) != len(have["days"]):
        out.append(f"days: pinned {len(want['days'])}, now {len(have['days'])}")
    for w, h in zip(want["days"], have["days"]):  # noqa: B905 - a length difference is reported above
        for k, v in w.items():
            if k not in h:
                out.append(f"day {w.get('day')} {k}: pinned {v!r}, now missing")
            elif not _same(v, h[k]):
                out.append(f"day {w.get('day')} {k}: pinned {v!r}, now {h[k]!r}")
    for k, v in want["totals"].items():
        if k not in have["totals"]:
            out.append(f"totals {k}: pinned {v!r}, now missing")
        elif not _same(v, have["totals"][k]):
            out.append(f"totals {k}: pinned {v!r}, now {have['totals'][k]!r}")
    if want["versions"] != have["versions"]:
        for i in range(max(len(want["versions"]), len(have["versions"]))):
            w = want["versions"][i] if i < len(want["versions"]) else None
            h = have["versions"][i] if i < len(have["versions"]) else None
            if w != h:
                out.append(f"version {i + 1}: pinned {w}, now {h}")
    events = [e for e in have["events"] if e[1] not in IGNORED_KINDS]
    if len(want["events"]) != len(events):
        out.append(f"events: pinned {len(want['events'])}, now {len(events)} (advisory kinds left out)")
    for i, (w, h) in enumerate(zip(want["events"], events)):  # noqa: B905 - a length difference is reported above
        if list(w[:3]) != list(h[:3]):
            out.append(f"event {i}: pinned day {w[0]} [{w[1]}] v{w[2]}, now day {h[0]} [{h[1]}] v{h[2]}: {h[3]}")
        elif not str(h[3]).startswith(str(w[3])):
            out.append(f"event {i} [{w[1]}] text changed: pinned {w[3]!r}, now {h[3]!r}")
    return [f"{name}: {line}" for line in out]


def load(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render(doc: Mapping[str, Any]) -> str:
    """JSON with one day, version or event per line, so a diff of the fixture shows exactly what moved."""

    def rows(items: Sequence[Any]) -> str:
        return "[\n" + ",\n".join(f"        {json.dumps(item, ensure_ascii=False)}" for item in items) + "\n      ]"

    blocks = []
    for name, entry in doc["configs"].items():
        fields = [
            f'      "argv": {json.dumps(entry["argv"])}',
            f'      "inputs": {json.dumps(entry["inputs"], sort_keys=True)}',
            f'      "days": {rows(entry["days"])}',
            f'      "totals": {json.dumps(entry["totals"], sort_keys=True)}',
            f'      "versions": {rows(entry["versions"])}',
            f'      "events": {rows(entry["events"])}',
        ]
        blocks.append(f"    {json.dumps(name)}: {{\n" + ",\n".join(fields) + "\n    }")
    head = f'  "captured_from": {json.dumps(doc["captured_from"])}'
    return "{\n" + head + ',\n  "configs": {\n' + ",\n".join(blocks) + "\n  }\n}\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check (or rewrite) the three pinned reference runs of v0.4.0's behaviour.")
    ap.add_argument("--only", action="append", choices=list(CONFIGS), help="check this workload only (repeatable)")
    ap.add_argument("--write", action="store_true", help="rewrite the fixture from fresh runs of every workload")
    ap.add_argument("--captured-from", help="with --write: the release or commit the runs reproduce, stored in the fixture")
    args = ap.parse_args(argv)
    rel = FIXTURE.relative_to(ROOT).as_posix()
    if args.write:
        if args.only or not args.captured_from:
            ap.error("--write rewrites every workload and needs --captured-from")
        doc = {"captured_from": args.captured_from, "configs": {name: capture(name) for name in CONFIGS}}
        FIXTURE.write_text(render(doc), encoding="utf-8")
        print(f"reference_check: wrote {rel}; add a CHANGELOG line that says why the reference moved")
        return 0
    if not FIXTURE.exists():
        print(f"reference_check: {rel} not found")
        return 1
    doc = load()
    problems: list[str] = []
    for name in args.only or list(CONFIGS):
        want = doc["configs"].get(name)
        found = [f"{name}: missing from the fixture"] if want is None else compare(name, want, capture(name))
        print(f"reference_check: {name} {'matches' if not found else f'differs in {len(found)} place(s)'}")
        problems += found[:MAX_REPORTED] + (
            [f"{name}: ... and {len(found) - MAX_REPORTED} more"] if len(found) > MAX_REPORTED else []
        )
    for line in problems:
        print(f"  {line}")
    if not problems:
        print(f"reference_check: legacy behaviour unchanged (captured from {doc.get('captured_from', 'unknown')})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
