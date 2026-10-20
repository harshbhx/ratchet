"""Per-task calibration records in a version's eval (FR-14.1, ADR 0040).

Several tasks can share one adapter, and the student is not equally good at each. One threshold calibrated over the
pooled golden rows serves the noisy task below its target precision and holds the easy one back, so `cascade_eval`
also writes `eval['tasks'][name]`: each task's own threshold and held-out numbers, on the same even/odd split as the
version's global ones (so a version with one task gets the global numbers again). The router, the release gate, Night
School and `/ratchet/stats` read those records through the helpers below, so an older or malformed eval reads the same
everywhere: a version scored before per-task calibration has no `tasks` and keeps its one threshold, and a version
scored under the task registry's first release has only `spec_hashes`, which still sends a changed task to the teacher.

Pure functions over plain dicts, no I/O: `threshold` runs on every request.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from . import tasks
from .confidence import SERVE_NOTHING

# The precision a task's threshold is calibrated to when its spec's policy sets no `target_precision` (FR-5.2).
DEFAULT_TARGET_PRECISION = 0.97
# Calibration rows (the even half of a task's golden rows) a task needs before its own threshold may go below the
# version's global one (RATCHET_MIN_TASK_GOLDEN). With fewer, a few lucky rows could lower it: it gets the higher of the
# two instead.
MIN_TASK_GOLDEN = 20


def number(value: object) -> float | None:
    """`value` as a finite float, or None (a bool, a string, NaN or a missing key is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def entries(ev: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """`eval['tasks']` as {task name: record}; {} for a version scored before per-task calibration. Anything that is not
    a mapping (a hand-edited eval) is left out."""
    found = ev.get("tasks") if isinstance(ev, Mapping) else None
    if not isinstance(found, Mapping):
        return {}
    return {name: dict(rec) for name, rec in found.items() if isinstance(name, str) and isinstance(rec, Mapping)}


def entry(ev: Mapping[str, Any] | None, name: str) -> Mapping[str, Any] | None:
    """The record of task `name` in `ev`, or None. Reads without copying (the router calls it on every request)."""
    found = ev.get("tasks") if isinstance(ev, Mapping) else None
    rec = found.get(name) if isinstance(found, Mapping) else None
    return rec if isinstance(rec, Mapping) else None


def recorded_hash(ev: Mapping[str, Any] | None, name: str) -> str | None:
    """The spec_hash of task `name` that the version was scored under: its per-task record's, else the task registry's
    `spec_hashes` (still written by `run_night` for every golden task, whatever the evaluator returns). None when the
    eval records neither."""
    rec = entry(ev, name)
    if rec is not None:
        h = rec.get("spec_hash")
        return h if isinstance(h, str) else None
    hashes = ev.get("spec_hashes") if isinstance(ev, Mapping) else None
    h = hashes.get(name) if isinstance(hashes, Mapping) else None
    return h if isinstance(h, str) else None


def spec_changed(ev: Mapping[str, Any] | None, task: tasks.Task) -> bool:
    """Whether the version was scored under another spec of `task` than the current one (FR-2.15). A version with no
    record for the task has nothing to compare."""
    h = recorded_hash(ev, task.name)
    return h is not None and h != task.spec_hash


def threshold(ev: Mapping[str, Any] | None, task: tasks.Task, default: float) -> float:
    """The confidence the student needs to answer `task` (FR-14.1): its per-task threshold when the version has a record
    for it under the current spec (SERVE_NOTHING, 1.01, once the release gate disabled it, or when it had no held-out
    rows); SERVE_NOTHING when the record, or the older `spec_hashes`, names another spec (the teacher answers until a
    night re-scores the version); otherwise `default`, the version's global threshold. A record that names no spec, or
    whose threshold is not a number, sends the task to the teacher rather than guess."""
    rec = entry(ev, task.name)
    if rec is not None:
        if rec.get("spec_hash") != task.spec_hash or rec.get("disabled"):
            return SERVE_NOTHING
        thr = number(rec.get("threshold"))
        return SERVE_NOTHING if thr is None else thr
    if spec_changed(ev, task):
        return SERVE_NOTHING
    return default


def serves(rec: Mapping[str, Any]) -> bool:
    """Whether a per-task record lets the student answer the task at all: not disabled and its threshold below
    SERVE_NOTHING."""
    thr = number(rec.get("threshold"))
    return not rec.get("disabled") and thr is not None and thr < SERVE_NOTHING


def served_rows(rec: Mapping[str, Any]) -> int:
    """How many of a record's held-out rows the student would answer at its threshold (coverage x test_n, an integer by
    construction); 0 for a record that serves nothing or that cannot be read."""
    test_n, cov = number(rec.get("test_n")), number(rec.get("coverage"))
    if not serves(rec) or test_n is None or cov is None:
        return 0
    return max(0, round(cov * test_n))


def keep_disabled(old: Mapping[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """`new`, a re-score of a version, with the tasks the release gate disabled on it in `old` still disabled. A gate
    decision holds for the version's life: a re-score measures it again but runs no gate, so it must not quietly put a
    task back on the student. A disabled task the re-score has no record for keeps its old record."""
    disabled = {name: rec for name, rec in entries(old).items() if rec.get("disabled")}
    if not disabled:
        return new
    merged = entries(new)
    for name, rec in disabled.items():
        merged[name] = {**merged.get(name, rec), "threshold": SERVE_NOTHING, "disabled": rec["disabled"]}
    return {**new, "tasks": merged}


def target_precision(task: tasks.Task, default: float) -> float:
    """The precision `task`'s threshold is calibrated to: its spec's `policy.target_precision`, else `default`."""
    value = number(task.policy.get("target_precision"))
    return default if value is None else value


def min_precision(task: tasks.Task, default: float) -> float:
    """The golden precision `task` must reach on a candidate: its spec's `policy.min_precision`, else `default` (the
    release policy's)."""
    value = number(task.policy.get("min_precision"))
    return default if value is None else value
