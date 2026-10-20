"""Regenerate the pinned reference simulation that tests/test_system.py compares against.

    python scripts/regen_sim_reference.py           # rewrite tests/fixtures/sim_reference_seed7.json
    python scripts/regen_sim_reference.py --check   # exit 1 if a fresh run no longer matches it; writes nothing

The reference run is `make sim` with every input pinned: seed 7, 10 days of 2,000 requests, a product launch on
day 5 and a corrupted v3, with mock models. `test_reference_simulation_unchanged` compares a fresh run with the
fixture exactly, so a change can show that it leaves the legacy behaviour byte-identical. Regenerate only when a
change is meant to move the simulation, and add a CHANGELOG line that says why.

The fixture holds simulation numbers from mock models, not measurements.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `python scripts/regen_sim_reference.py` puts scripts/, not the repo, on the path
    sys.path.insert(0, str(ROOT))

from sim.simulate import parser, run  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sim_reference_seed7.json"
ARGV = (
    "--seed", "7", "--days", "10", "--requests", "2000", "--drift-day", "5", "--bad-deploy-version", "3",
    "--workload", "tickets",
)  # fmt: skip
DAY_KEYS = ("day", "requests", "student_share", "escalation_rate", "savings_pct", "audit_agreement")
VERSION_KEYS = ("id", "state", "traffic_pct", "train_examples")
# Night School and release-gate decisions, in order. Other kinds (student errors, anything added later) are left
# out, so a new kind of log line is not mistaken for a change in behaviour.
RELEASE_KINDS = frozenset({"trained", "skip", "rejected", "shadow", "canary", "live", "retired", "rollback", "alert"})
_VERSION = re.compile(r"\bv(\d+)\b")
DECIMALS = 4


def reference_args() -> argparse.Namespace:
    return parser().parse_args([*ARGV, "--out", os.devnull])


def inputs() -> dict[str, Any]:
    """Every simulator input, defaults included, so a changed default shows up as an input change, not a mystery."""
    return {k: v for k, v in sorted(vars(reference_args()).items()) if k != "out"}


MISSING = "<missing>"


def pinned_inputs(pinned: Mapping[str, Any], fresh: Mapping[str, Any]) -> dict[str, Any]:
    """The fresh value of every input the fixture pins (MISSING for a flag that is gone).

    Equal to `pinned` when no pinned input changed. A flag added after the fixture was made is not compared, so a new
    option whose default keeps the old behaviour does not force a regeneration; a changed or removed default does."""
    return {k: fresh.get(k, MISSING) for k in pinned}


def matches(want: Mapping[str, Any], have: Mapping[str, Any]) -> bool:
    """The rule `test_reference_simulation_unchanged` applies: same pinned inputs, days, events and versions."""
    return (
        set(have) == set(want)
        and pinned_inputs(want["inputs"], have["inputs"]) == want["inputs"]
        and all(have[k] == want[k] for k in ("days", "events", "versions"))
    )


@contextlib.contextmanager
def _default_settings() -> Iterator[None]:
    """`Settings` reads RATCHET_* variables the simulator does not pass explicitly (RATCHET_TEACHER_REASONING_TOKENS
    changes every cost), so the reference always runs on the defaults, whatever the shell has set."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("RATCHET_")}
    for k in saved:
        del os.environ[k]
    try:
        yield
    finally:
        os.environ.update(saved)


def run_reference() -> dict[str, Any]:
    """One full run, about 7 s. Tests get it once per session from the `reference_run` fixture in tests/conftest.py."""
    with _default_settings():
        return run(reference_args())


def _round(value: Any) -> Any:
    return round(value, DECIMALS) if isinstance(value, float) else value


def _version(detail: str) -> int | None:
    """Event details name their subject version first ('v3 audited precision ...; traffic back to live v1')."""
    m = _VERSION.search(detail)
    return int(m.group(1)) if m else None


def summarize(res: dict[str, Any]) -> dict[str, Any]:
    """The part of a `sim.simulate.run` result that the fixture pins: daily numbers, release events and where each
    version ended up (with how many examples Night School trained it on)."""
    return {
        "days": [{k: _round(d[k]) for k in DAY_KEYS} for d in res["days"]],
        "events": [
            {"day": e["day"], "kind": e["kind"], "version": _version(e["detail"])}
            for e in res["events"]
            if e["kind"] in RELEASE_KINDS
        ],
        "versions": [{k: v[k] for k in VERSION_KEYS} for v in res["versions"]],
    }


def snapshot(res: dict[str, Any]) -> dict[str, Any]:
    return {"inputs": inputs(), **summarize(res)}


def render(snap: dict[str, Any]) -> str:
    """JSON with one day or one event per line, so a diff of the fixture shows exactly what moved."""

    def block(name: str, items: list[dict[str, Any]]) -> str:
        body = ",\n".join(f"    {json.dumps(item)}" for item in items)
        return f'  "{name}": [\n{body}\n  ]'

    parts = [f'  "inputs": {json.dumps(snap["inputs"], sort_keys=True)}']
    parts += [block(name, snap[name]) for name in ("days", "events", "versions")]
    return "{\n" + ",\n".join(parts) + "\n}\n"


def load() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate (or check) the pinned reference simulation fixture.")
    ap.add_argument("--check", action="store_true", help="compare a fresh run with the fixture and write nothing")
    args = ap.parse_args(argv)
    snap = snapshot(run_reference())
    rel = FIXTURE.relative_to(ROOT).as_posix()
    if args.check:
        same = FIXTURE.exists() and matches(load(), snap)
        print(f"regen_sim_reference: {rel} {'matches a fresh run' if same else 'differs from a fresh run'}")
        return 0 if same else 1
    text = render(snap)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(text, encoding="utf-8")
    print(f"regen_sim_reference: wrote {rel}; add a CHANGELOG line that says why the reference moved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
