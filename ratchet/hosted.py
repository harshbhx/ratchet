"""The hosted student (FR-12.33, ADR 0146): the untuned Nemotron 3 Nano 30B on Token Factory answers the tasks it was
calibrated for while no trained version is live, and the teacher answers the rest.

Off by default. With RATCHET_HOSTED_STUDENT=1 in live mode the gateway keeps one version row in state `hosted`
(`Ledger.ensure_hosted`): no adapter (`adapter_uri` empty), no training examples, and an eval that holds the records
below. The router reaches it only when no version is live (`Ratchet._pick_version`), the resolver always sends it to
Token Factory, and the router holds each task to its record here (`Ratchet.threshold`): a task without a serving
record is never sampled (route `task_disabled`). Its answers are not any version's (ADR 0073): the response names the
model and no version, and no release statistic counts them. It is never trained on (INV-2): audits store Ultra's
answers as for any student.

The records are what `scripts/hosted_nano_calibration.py` computes from `evidence/hosted-nano/` with the golden eval a
night runs (`nightschool.evaluate.cascade_eval`: even rows calibrate, odd rows measure; the label is Ultra's answer).
`python scripts/hosted_nano_calibration.py check` recomputes them; a test runs it. They hold only for the model, the
sample count and the thinking mode they were measured with, so the startup check refuses any other
(`config.Settings.validate`). Standard library and `confidence` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from .confidence import SERVE_NOTHING

# The model, the samples per request and the thinking mode the records were measured with.
MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
SAMPLES = 3
REASONING = "off"
# A task is served only when its held-out precision against Ultra is at least MIN_PRECISION on at least MIN_SERVED
# served held-out rows: the 20 audits a canary needs before the release gate moves it (RATCHET_MIN_AUDITS).
MIN_PRECISION = 0.95
MIN_SERVED = 20
SOURCE = "evidence/hosted-nano"

# Per task, `cascade_eval`'s record (FR-14.1) after `gate`, as `scripts/hosted_nano_calibration.py show` prints it from
# the evidence. Never edit by hand: the check (and its test) fails. Measured Oct 26, 2026 on 132 rows over 33 distinct
# search results: neither task reached the bar, so with the switch on the teacher still answers every request.
TASKS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "page-read": MappingProxyType(
            {
                "agreement": 0.5151515151515151,
                "calibrated": False,
                "coverage": 0.2,
                "disabled": "held-out precision 0.500 on 4 served rows; needs 0.95 on 20",
                "golden_n": 33,
                "measured_threshold": 0.9575675723954891,
                "precision": 0.5,
                "precision_kind": "exact",
                "spec_hash": "8060ae3b53aba44f",
                "target_precision": 0.97,
                "test_n": 20,
                "threshold": SERVE_NOTHING,
            }
        ),
        "source-triage": MappingProxyType(
            {
                "agreement": 0.6767676767676768,
                "calibrated": True,
                "coverage": 0.15217391304347827,
                "disabled": "held-out precision 1.000 on 7 served rows; needs 0.95 on 20",
                "golden_n": 99,
                "measured_threshold": 0.9549753695768033,
                "precision": 1.0,
                "precision_kind": "exact",
                "spec_hash": "3bc29ad53777a914",
                "target_precision": 0.97,
                "test_n": 46,
                "threshold": SERVE_NOTHING,
            }
        ),
    }
)


def gate(record: Mapping[str, Any]) -> dict[str, Any]:
    """A task's record as the hosted student uses it: as measured when its held-out precision reaches MIN_PRECISION on
    at least MIN_SERVED served rows, else disabled (SERVE_NOTHING), so the teacher answers that task."""
    rec = dict(record)
    served = round(float(rec.get("coverage", 0.0)) * int(rec.get("test_n", 0)))
    precision = float(rec.get("precision", 0.0))
    if served >= MIN_SERVED and precision >= MIN_PRECISION and rec.get("threshold", SERVE_NOTHING) < SERVE_NOTHING:
        return rec
    rec["measured_threshold"] = rec.get("threshold")
    rec["threshold"] = SERVE_NOTHING
    rec["disabled"] = f"held-out precision {precision:.3f} on {served} served rows; needs {MIN_PRECISION} on {MIN_SERVED}"
    return rec


def eval_record() -> dict[str, Any]:
    """The hosted version's eval: the records, a global threshold that serves nothing (so a task without a record goes
    to the teacher), and what the records were measured with."""
    return {
        "threshold": SERVE_NOTHING,
        "tasks": {name: dict(rec) for name, rec in TASKS.items()},
        "hosted": {
            "model": MODEL,
            "samples": SAMPLES,
            "reasoning": REASONING,
            "min_precision": MIN_PRECISION,
            "min_served": MIN_SERVED,
            "source": SOURCE,
        },
    }


def served_tasks(ev: Mapping[str, Any]) -> dict[str, float]:
    """The tasks a hosted version's eval lets the student answer, with their thresholds (for `/ratchet/stats`)."""
    found = ev.get("tasks") if isinstance(ev, Mapping) else None
    if not isinstance(found, Mapping):
        return {}
    out: dict[str, float] = {}
    for name, rec in found.items():
        thr = rec.get("threshold") if isinstance(rec, Mapping) else None
        if isinstance(thr, (int, float)) and not isinstance(thr, bool) and thr < SERVE_NOTHING and not rec.get("disabled"):
            out[name] = float(thr)
    return dict(sorted(out.items()))
