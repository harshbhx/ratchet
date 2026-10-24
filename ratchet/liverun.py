"""Live-run harness (FR-8.20, FR-8.21, ADR 0121): synthetic workload traffic through a live gateway in rounds, Night
School in process between them, a measured all-teacher control arm, accuracy against the workload's own labels, and one
results file behind every live number in the docs.

    python -m ratchet.liverun plan   [--rounds N] [--requests R] [--max-usd X] [--workload W]   # free: no paid call
    python -m ratchet.liverun smoke  [--base-url URL]                 # 20 requests, about 5 control calls, no nights
    python -m ratchet.liverun run    --max-usd X [--base-url URL] [--rounds N] [--requests R] [...]
    python -m ratchet.liverun run    --resume RUN_DIR [--base-url URL]   # continue an interrupted run
    python -m ratchet.liverun smoke  --repeat-of RUN_DIR [--base-url URL]  # the same plan and seed again
    python -m ratchet.liverun report RUN_DIR [--bill-usd X --bill-note TEXT] [--repeat DIR] [--publish [--allow-partial]]
    python -m ratchet.liverun verify DIR                              # recompute the client-side numbers

It runs on the gateway's host with the gateway's environment (the same RATCHET_DB and .env as `python -m ratchet.live`),
talks to the gateway only through `/healthz` and `/v1/chat/completions` with its own API key (RATCHET_LIVERUN_KEY, read
from the environment, never a flag), reads the gateway's ledger file directly, and runs `python -m ratchet.live night`
and `tick` in process between rounds. `run` needs the planned gate profile on the gateway (RATCHET_SEQUENTIAL=1,
RATCHET_CANARY_AUDIT_RATE=0.5). It writes no request or answer text, no key and no base URL anywhere: only metadata,
counts and its own measurements. Only a published, non-smoke results file may back a number in the docs.

The control arm sends a Bernoulli sample of the same requests straight to the teacher with the serving teacher's
parameters (exactly `router.Ratchet._teacher_complete`'s call): never through the gateway, so never stored. A ratio
estimator on the student-served pairs turns the gateway's recorded all-teacher baseline into a measured one.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import inspect
import io
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import httpx

from . import __version__, cost, licenses, live, taskspec
from . import tasks as tasklib
from .config import Price, Settings
from .ledger import Ledger
from .nightschool.orchestrate import run_night
from .openai_compat import normalize_messages, parse_modalities, validate_chat_request
from .providers import GenParams, OpenAICompatProvider, Provider, approx_tokens
from .release import CANARY_STEPS, ReleasePolicy
from .resilience import TEACHER_CIRCUIT_OPEN, describe_failure
from .stats import binom_sf, cp_lower, ratio_estimate, required_rows, wilson

GENERATOR = "ratchet.liverun/1"
KEY_ENV = "RATCHET_LIVERUN_KEY"  # the harness's gateway key; never a flag (shell history, the process list)
SHA_ENV = "RATCHET_GIT_SHA"  # the code's commit when the image has no .git
ARMS_KEY_ENV = "NEBIUS_API_KEY_LIVERUN"  # the arms' own Token Factory key (falls back to NEBIUS_API_KEY)
# The gate settings the live run is planned with: the anytime-valid canary rule (ADR 0025) and audit focusing, so each
# canary stage gets its audits within a round (the plan's stage table shows the difference). `run` refuses to start
# unless both are in force (`--gate-profile as-set` measures other settings on purpose).
RUN_PROFILE: tuple[tuple[str, str], ...] = (("RATCHET_SEQUENTIAL", "1"), ("RATCHET_CANARY_AUDIT_RATE", "0.5"))
EXIT_OK, EXIT_STOPPED, EXIT_INPUT, EXIT_OVER_PLAN = 0, 1, 2, 3
STOP_REASONS = (
    "max_usd",  # the next batch would pass --max-usd
    "plan_over_budget",  # the re-planned remaining rounds would pass --max-usd
    "server_cap",  # the gateway's own daily quota or budget for the key
    "auth",  # the gateway refused the key
    "teacher_down",  # 503 teacher_circuit_open three times in a row
    "gateway_errors",  # more than 10% of a batch failed
    "control_failed",  # every control call of a batch failed
    "night_failed",  # a night raised (with --on-night-failure stop)
    "error",  # the harness itself failed (the class only)
    "interrupted",  # Ctrl-C
    "preflight",  # the probe refused the run (exit 2, before any round)
)
# Arguments never written to state.json: where the gateway is (a base URL is never written) and how this invocation
# was started. A resumed or repeated run takes them from its own command line.
UNSAVED_ARGS = ("cmd", "resume", "repeat_of", "base_url")
MAX_RETRY_AFTER_S = 60.0  # the longest Retry-After the harness waits before its one retry
ERROR_SHARE = 0.10  # a batch with more failed requests than this share stops the run
TEACHER_DOWN_STREAK = 3
MIN_STUDENT_PAIRS = 10  # paired student-served requests the measured baseline needs
CP_DELTA = 0.05  # one-sided 95% Clopper-Pearson bounds
Z95 = 1.96
NOTE_MAX = 200  # characters of a bill note
RETRY_CODES = frozenset({"rate_limit_exceeded", "overloaded", "upstream_rate_limited"})  # wait, then retry once
SERVER_CAP_CODES = frozenset({"daily_quota_exceeded", "budget_exceeded", "gateway_budget_exceeded"})
SPEND_HEADER = "x-ratchet-spend-today-usd"  # lane D's per-key meter, when the gateway sends it
REQUEST_ID_HEADER = "x-request-id"  # lane D's request id, when the gateway sends it
INFLIGHT = ("shadow", "canary")
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"
_RUN_ID = re.compile(r"\d{8}T\d{6}Z")
_CLIENT = re.compile(r"[A-Za-z0-9._-]{1,64}")
_VERSION_REF = re.compile(r"\bv(\d+)\b")
_HEX_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SHELLS = frozenset({"bash", "sh", "zsh", "python", "python3"})
# Planning assumptions (printed with every plan): visible output tokens per teacher answer beyond its reasoning, seconds
# per teacher-served and per student-served request, the share of shadow would-serve answers the teacher arbitrates,
# model calls per Scout run, and how many sampled requests size a prompt.
PLAN_OUTPUT_EXTRA = 60
PLAN_TEACHER_S = 5.0
PLAN_STUDENT_S = 1.0
PLAN_ARBITRATION_RATE = 0.05
PLAN_SCOUT_CALLS = 20
PLAN_SAMPLES = 20
PLAN_SEED_OFFSET = 1_000_003  # the planner's and the probe's own streams, so the run's stream never moves
PROBE_SEED_OFFSET = 2_000_003
TAVILY_CREDITS_PER_RUN = 3  # Scout's planner writes up to three basic searches
# The fewest new examples a night trains on (run_night's own default, read so the two never disagree).
NIGHT_MIN_EXAMPLES: int = inspect.signature(run_night).parameters["min_new_examples"].default
# results.json's keys (docs/API.md has no endpoint for it; docs/RUNBOOK.md describes the file).
RESULT_KEYS = (
    "generator",
    "kind",
    "mode",
    "run_id",
    "started",
    "finished",
    "stopped",
    "code",
    "models",
    "client",
    "workload",
    "settings",
    "plan",
    "rounds",
    "arms",
    "cost",
    "kpis",
    "repeat",
    "values",
    "sources",
    "caveats",
)
# Where each value goes: the docs' placeholders (docs/LIVE_RESULTS.md's placeholder map) and its claim id.
PLACEHOLDERS = (
    ("submission text, results table", "cost reduction", "live.final.cost_reduction_measured_pct"),
    ("submission text, results table", "audited precision", "live.final.audited_precision_pct"),
    ("submission text, results table", "student latency", "live.final.student_p50_client_ms"),
    ("submission text, results table", "Ultra latency", "live.final.ultra_p50_client_ms"),
    ("submission text, results table", "endpoint hours", "live.run.endpoint_hours"),
    ("submission text, results table", "GPU-hour savings", "live.run.gpu_hours_reduction_pct"),
    ("submission text, Scout row", "student calls", "live.scout.student_calls"),
    ("submission text, Scout row", "LLM calls", "live.scout.llm_calls"),
    ("submission text, Scout row", "Scout savings", "live.scout.savings_pct"),
    ("submission text, checklist", "bill next to break-even", "live.bill.nebius_usd"),
    ("demo script, 2:42", "[cost cut]", "live.final.cost_reduction_measured_pct"),
    ("demo script, 2:42", "[precision]", "live.final.audited_precision_pct"),
    ("demo script, 2:42", "[latency]", "live.final.student_p50_client_ms"),
)
# Values `verify` recomputes from requests.ndjson.gz; the others are snapshots of the ledger taken during the run.
CLIENT_VALUE_IDS = (
    "live.final.cost_reduction_measured_pct",
    "live.final.cost_reduction_recorded_pct",
    "live.run.cost_reduction_measured_pct",
    "live.run.cost_reduction_recorded_pct",
    "live.final.student_p50_client_ms",
    "live.final.ultra_p50_client_ms",
    "live.final.student_share_pct",
    "live.final.paired_agreement_pct",
    "live.run.requests",
    "live.control.n",
    "live.control.reasoning_tokens_mean",
    "live.control.ratio_student",
    "live.accuracy.cascade_easy_pct",
    "live.accuracy.cascade_hard_pct",
    "live.accuracy.ultra_easy_pct",
    "live.accuracy.ultra_hard_pct",
    "live.scout.student_calls",
    "live.scout.llm_calls",
    "live.scout.savings_pct",
)

ProviderFactory = Callable[..., Provider]
RunFn = Callable[..., Any]


class InputError(Exception):
    """Bad input or a refused preflight: exit 2 with one line saying what to fix."""


class _Stop(Exception):
    """Stop the run early with a reason from STOP_REASONS; results are written first."""

    def __init__(self, reason: str, detail: str = "") -> None:
        assert reason in STOP_REASONS, reason
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------------------------------------------------
# Small exact helpers
# ---------------------------------------------------------------------------------------------------------------------
def nearest_rank(values: Iterable[float], q: float) -> float | None:
    """The q-quantile by the nearest-rank rule, the one lane D's `Ledger.latency_percentiles` uses: the smallest
    observed value with at least a share q of the values at or below it (q*n rounded to 9 decimals first, so 0.07 * 100
    counts as 7). None for no values. Every percentile the harness writes follows this one rule."""
    if isinstance(q, bool) or not isinstance(q, (int, float)) or not 0 <= q <= 1:
        raise ValueError(f"q must be a number from 0 to 1, not {q!r}")
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    n = len(xs)
    return xs[min(n, max(1, math.ceil(round(q * n, 9)))) - 1]


def percentiles(values: Iterable[float | None]) -> dict[str, Any]:
    """{n, p50, p95} by `nearest_rank`, over the values that are not None."""
    xs = [float(v) for v in values if v is not None]
    return {"n": len(xs), "p50": nearest_rank(xs, 0.5), "p95": nearest_rank(xs, 0.95)}


def plan_stage_requests(min_count: int, p: float, confidence: float = 0.9) -> int | None:
    """The fewest requests n with P(Binomial(n, p) >= min_count) >= `confidence`: how much traffic a release stage
    needs before its `min_count` audits (or shadow answers) arrive with that probability. A doubling search, then a
    bisection (the tail grows with n), as `stats.required_rows`. 0 for min_count 0; None when p is 0 or more than a
    billion requests would be needed."""
    if isinstance(min_count, bool) or not isinstance(min_count, int) or min_count < 0:
        raise ValueError(f"min_count must be a whole number, 0 or more, not {min_count!r}")
    if not 0 <= p <= 1:
        raise ValueError(f"p must be a probability, not {p!r}")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be strictly between 0 and 1, not {confidence!r}")
    if min_count == 0:
        return 0
    if p <= 0:
        return None
    hi = min_count
    while binom_sf(min_count, hi, p) < confidence:
        if hi > 1_000_000_000:
            return None
        hi *= 2
    lo = max(min_count, hi // 2)
    while lo < hi:
        mid = (lo + hi) // 2
        if binom_sf(min_count, mid, p) >= confidence:
            hi = mid
        else:
            lo = mid + 1
    return lo


def profile_check(release: ReleasePolicy, canary_rate: float | None) -> dict[str, Any]:
    """Whether the planned gate settings (RUN_PROFILE) are in force in this environment, which is the gateway's (the
    harness sources the same .env; the probe checks the audit rates against a served row)."""
    in_force = {
        "RATCHET_SEQUENTIAL": str(release.sequential),
        "RATCHET_CANARY_AUDIT_RATE": "unset" if canary_rate is None else f"{canary_rate:g}",
    }
    off = [name for name, want in RUN_PROFILE if not _same_setting(in_force[name], want)]
    return {"wanted": dict(RUN_PROFILE), "in_force": in_force, "matches": not off, "differs": off}


def _same_setting(have: str, want: str) -> bool:
    try:
        return math.isclose(float(have), float(want), rel_tol=0.0, abs_tol=1e-12)
    except ValueError:
        return have == want


def repeat_values(first: Mapping[str, Any], again: Mapping[str, Any]) -> dict[str, Any]:
    """Value by value, the first run against its repeat (same plan, seed and code unless `same_code` says otherwise):
    both numbers and the difference (repeat - first; for `_pct` values in percentage points). A value only one run
    measured is listed apart, never compared with 0; the hand-entered bill is not a run measurement and is left out."""
    first = {k: v for k, v in first.items() if not k.startswith("live.bill.")}
    again = {k: v for k, v in again.items() if not k.startswith("live.bill.")}
    both = sorted(set(first) & set(again))
    rows = {vid: {"first": first[vid], "repeat": again[vid], "difference": again[vid] - first[vid]} for vid in both}
    pct = [abs(r["difference"]) for vid, r in rows.items() if vid.endswith("_pct")]
    return {
        "values": rows,
        "only_first": sorted(set(first) - set(again)),
        "only_repeat": sorted(set(again) - set(first)),
        "max_abs_difference_pct_points": max(pct) if pct else None,
    }


def _finite(value: Any, lo: float = 0.0) -> float | None:
    """A finite number at or above `lo` from a response, else None (never trust a body's numbers blindly)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < lo:
        return None
    return float(value)


def _pct(num: float, den: float) -> float | None:
    return 100.0 * num / den if den else None


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _num(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _write_json(path: Path, doc: Any) -> None:
    """Write atomically (a temporary file, then a rename), so a crash never leaves half a state or results file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _utc(ts: float | None) -> str:
    return "n/a" if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class FileItem:
    """One row of a `file:` workload: a task, its messages and its labels (human labels, for an independent measure)."""

    task: str
    msgs: list[dict[str, Any]]
    labels: dict[str, Any]
    difficulty: str = "unlabelled"
    template: str = "file"
    line: int = 0  # the row's line in the file, for error messages that never quote it

    def messages(self) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self.msgs))

    def answer(self, labels: dict[str, Any]) -> str:
        return json.dumps(labels)


class FileStream:
    """`--workload file:PATH`: the rows in a seeded order, a new permutation each pass, so the same seed draws the same
    row for the same index and a resumed run continues where it stopped."""

    def __init__(self, rows: list[FileItem], seed: int) -> None:
        if not rows:
            raise InputError("the workload file has no rows")
        self.rows = rows
        self.rng = random.Random(seed)
        self.order: list[int] = []

    def sample(self, day: int) -> FileItem:
        if not self.order:
            self.order = list(range(len(self.rows)))
            self.rng.shuffle(self.order)
        return self.rows[self.order.pop()]


def load_file_rows(path: str) -> list[FileItem]:
    """JSONL rows `{"task", "messages" | "text", "labels"}` (optional `system`, `difficulty`). An error names the line
    and the field, never the row's text (INV-12)."""
    p = Path(path)
    if not p.is_file():
        raise InputError(f"workload file {p.name!r} not found")
    rows: list[FileItem] = []
    with p.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise InputError(f"workload file line {lineno}: not JSON") from None
            if not isinstance(row, dict):
                raise InputError(f"workload file line {lineno}: not an object")
            task = row.get("task")
            if not isinstance(task, str) or not tasklib.valid_name(task):
                raise InputError(f"workload file line {lineno}: task must be a task name")
            labels = row.get("labels")
            if not isinstance(labels, dict) or not labels:
                raise InputError(f"workload file line {lineno}: labels must be a non-empty object")
            if "messages" in row:
                msgs = row["messages"]
                ok = (
                    isinstance(msgs, list)
                    and msgs
                    and all(
                        isinstance(m, dict) and isinstance(m.get("role"), str) and isinstance(m.get("content"), str) for m in msgs
                    )
                )
                if not ok:
                    raise InputError(f"workload file line {lineno}: messages must be a list of {{role, content}} strings")
            elif isinstance(row.get("text"), str) and row["text"]:
                msgs = [{"role": "user", "content": row["text"]}]
                if isinstance(row.get("system"), str) and row["system"]:
                    msgs.insert(0, {"role": "system", "content": row["system"]})
            else:
                raise InputError(f"workload file line {lineno}: needs messages or text")
            difficulty = row.get("difficulty") if isinstance(row.get("difficulty"), str) else "unlabelled"
            rows.append(FileItem(task, msgs, labels, difficulty, line=lineno))
    return rows


def check_file_rows(rows: Sequence[FileItem]) -> None:
    """Every row's task is registered (the gateway's registry, installed first) and its labels are a valid answer under
    the task's spec: otherwise every answer would score as invalid against them. The error names the line, the task and
    the spec's reason (`Task.check`), never the row's text."""
    for row in rows:
        if row.task not in tasklib.REGISTRY:
            raise InputError(
                f"workload file line {row.line}: task {row.task!r} is not registered; add it to RATCHET_TASKS_FILE (or the "
                "admin API) so the gateway and the harness score it by its spec"
            )
        why = tasklib.get(row.task).check(row.answer(row.labels))
        if why is not None:
            raise InputError(f"workload file line {row.line}: labels do not fit task {row.task!r}'s spec ({why})")


def make_workload(name: str, seed: int, drift_round: int | None) -> Any:
    """The workload's own seeded stream: `sim.world.make_stream` (imported here, never at module level: `ratchet` does
    not depend on `sim`), or a `file:` workload. A new instance, so no mock stream moves."""
    if name.startswith("file:"):
        return FileStream(load_file_rows(name[len("file:") :]), seed)
    from sim.world import WORKLOADS, make_stream

    if name not in WORKLOADS:
        raise InputError(f"--workload must be one of {', '.join(WORKLOADS)} or file:PATH, not {name!r}")
    return make_stream(name, seed=seed, drift_day=drift_round)


def workload_label(name: str) -> str:
    """How every accuracy figure is labelled: template labels are the template author's, not independent ground truth."""
    if name.startswith("file:"):
        return f"file workload {Path(name[len('file:') :]).name}, the file's labels"
    return f"synthetic {name}, template labels"


class Draws:
    """The items of each round, drawn in index order from one stream, rounds in order. Resuming replays the draws of
    every request already sent, so the next index gets the same text as an uninterrupted run with the same seed."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.round: int | None = None
        self.items: list[Any] = []

    def item(self, k: int, i: int) -> Any:
        if self.round != k:
            if self.round is not None and k < self.round:
                raise ValueError(f"round {k} after round {self.round}: rounds are drawn in order")
            self.round, self.items = k, []
        while len(self.items) <= i:
            self.items.append(self.stream.sample(k))
        return self.items[i]


# ---------------------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------------------
def score(task: tasklib.Task, text: str | None, truth: Any) -> tuple[bool | None, dict[str, bool] | None]:
    """(accurate, agreement per field) of an answer against the labels' key `truth` (`task.key` of the item's labelled
    answer). (None, None) for an answer the task's spec refuses (`compare.INVALID`), which counts as wrong, and for
    labels that do not validate under the spec."""
    key = task.key(text)
    if key is tasklib.NO_KEY or truth is tasklib.NO_KEY:
        return None, None
    if not task.fields:
        return bool(task.match(key, truth)), None
    fields = {f.path: bool(f.match(key[f.path], truth[f.path])) for f in task.fields}
    return all(fields.values()), fields


def _acc(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """{n, accurate, invalid, pct}: an invalid answer (accurate None) counts in n and as wrong."""
    n = len(rows)
    good = sum(1 for r in rows if r.get("accurate") is True)
    invalid = sum(1 for r in rows if r.get("accurate") is None)
    return {"n": n, "accurate": good, "invalid": invalid, "pct": _pct(good, n)}


def _fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Agreement per field (priority is the softer label, so each field is reported apart); an invalid answer counts as
    a disagreement on every field."""
    paths = sorted({p for r in rows for p in (r.get("fields") or {})})
    out = {}
    for path in paths:
        agree = sum(1 for r in rows if (r.get("fields") or {}).get(path) is True)
        out[path] = {"n": len(rows), "agree": agree, "pct": _pct(agree, len(rows))}
    return out


def accuracy_block(ok: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
    """Template-label accuracy of the cascade's answers (by `served_by` and difficulty) and of the control arm's (Ultra
    alone, on its sample). Easy and hard apart: the hard templates are deliberately ambiguous."""
    controls = [{**r["control"], "difficulty": r.get("difficulty")} for r in ok if (r.get("control") or {}).get("ok")]
    by = sorted({r.get("served_by") for r in ok if r.get("served_by")})
    diffs = sorted({str(r.get("difficulty")) for r in ok})
    cdiffs = sorted({str(c.get("difficulty")) for c in controls})
    return {
        "label": label,
        "cascade": {
            **_acc(ok),
            "by_served_by": {s: _acc([r for r in ok if r.get("served_by") == s]) for s in by},
            "by_difficulty": {d: _acc([r for r in ok if str(r.get("difficulty")) == d]) for d in diffs},
            "fields": _fields(ok),
        },
        "control": {
            **_acc(controls),
            "by_difficulty": {d: _acc([c for c in controls if str(c.get("difficulty")) == d]) for d in cdiffs},
            "fields": _fields(controls),
        },
    }


def control_estimate(records: Sequence[Mapping[str, Any]], formula_reasoning: int) -> dict[str, Any]:
    """The measured all-teacher baseline of a window (FR-8.21, ADR 0121), from its cascade answers and their control
    calls. x is the cascade's recorded `all_teacher_cost_usd`, y the control call's cost (the router's price rule, cached
    input priced in full on both sides). Student-served rows: their recorded baseline is the token formula (or an
    audit's check-role call), so the ratio estimator R_s over their pairs is what the control corrects; teacher-served
    rows' recorded baseline is the serving call itself, used as is (R_t is reported as a check, about 1). B = sum of x
    over teacher-served rows + R_s x sum of x over student-served rows, with a 95% normal interval of +-1.96 se sum x
    (no finite-population correction, which is conservative). Fewer than MIN_STUDENT_PAIRS student-served pairs:
    `insufficient`, and the measured values are None. Every reduction is per token (NFR-11)."""
    ok = [r for r in records if r.get("status") == 200 and r.get("all_teacher_cost_usd") is not None]
    calls = [r["control"] for r in records if (r.get("control") or {}).get("called")]
    good = [c for c in calls if c.get("ok")]
    paired = [r for r in ok if (r.get("control") or {}).get("ok")]
    pairs_s = [(r["all_teacher_cost_usd"], r["control"]["cost"]) for r in paired if r.get("served_by") == "student"]
    pairs_t = [(r["all_teacher_cost_usd"], r["control"]["cost"]) for r in paired if r.get("served_by") == "teacher"]
    r_s = ratio_estimate(pairs_s, min_n=MIN_STUDENT_PAIRS)
    r_t = ratio_estimate(pairs_t)
    cascade = sum(r["cost_usd"] for r in ok if r.get("cost_usd") is not None)
    x_t = sum(r["all_teacher_cost_usd"] for r in ok if r.get("served_by") == "teacher")
    x_s = sum(r["all_teacher_cost_usd"] for r in ok if r.get("served_by") == "student")
    recorded = x_t + x_s
    measured = interval = reduction = None
    status = "ok" if r_s["status"] == "ok" else "insufficient"
    if status == "ok":
        measured = x_t + r_s["ratio"] * x_s
        half = Z95 * r_s["se"] * x_s
        interval = [measured - half, measured + half]
        reduction = 1 - cascade / measured if measured > 0 else None
    reasoning = [c.get("reasoning_tokens") for c in good if c.get("reasoning_tokens") is not None]
    agree_rows = [r for r in paired if r.get("served_by") == "student" and r["control"].get("agree") is not None]
    agree = sum(1 for r in agree_rows if r["control"]["agree"])
    failures = Counter(c.get("failure") or "unknown" for c in calls if not c.get("ok"))
    return {
        "status": status,
        "selected": sum(1 for r in records if r.get("control") is not None),
        "called": len(calls),
        "ok": len(good),
        "failed": dict(sorted(failures.items())),
        "skipped_cap": sum(1 for r in records if (r.get("control") or {}).get("skipped") == "cap"),
        "pairs": {"student": len(pairs_s), "teacher": len(pairs_t)},
        "ratio_student": _ratio_view(r_s),
        "ratio_teacher": _ratio_view(r_t),
        "cascade_usd": cascade,
        "baseline_recorded_usd": recorded,
        "baseline_measured_usd": measured,
        "baseline_measured_ci95": interval,
        "measured_reduction": reduction,
        "recorded_reduction": 1 - cascade / recorded if recorded > 0 else None,
        "cost_model": "per_token",
        "reasoning_tokens": {
            "mean": sum(reasoning) / len(reasoning) if reasoning else None,
            **{k: v for k, v in percentiles(reasoning).items() if k != "n"},
            "formula": formula_reasoning,
        },
        "cached_tokens": sum(c.get("cached_tokens") or 0 for c in good),
        "agreement": {
            "n": len(agree_rows),
            "agree": agree,
            "pct": _pct(agree, len(agree_rows)),
            "cp_lower": cp_lower(agree, len(agree_rows), CP_DELTA) if agree_rows else None,
        },
        "spend_usd": sum(c.get("cost") or 0.0 for c in calls),
    }


def _ratio_view(r: Mapping[str, Any]) -> dict[str, Any]:
    """A ratio estimate as reported: its ratio and se only when it is 'ok' (an insufficient one's 1.0 is no estimate)."""
    ok = r["status"] == "ok"
    return {"ratio": r["ratio"] if ok else None, "se": r["se"] if ok else None, "n": r["n"], "status": r["status"]}


def client_block(records: Sequence[Mapping[str, Any]], label: str, formula_reasoning: int) -> dict[str, Any]:
    """A window's client-side numbers, all recomputable from requests.ndjson.gz (`verify`): requests and statuses,
    student share, client-observed latency of both arms, accuracy, the control estimate and paired agreement."""
    ok = [r for r in records if r.get("status") == 200]
    statuses = Counter(f"{r.get('status')} {r.get('code') or ''}".strip() for r in records if r.get("status") != 200)
    student = sum(1 for r in ok if r.get("served_by") == "student")
    controls = [r["control"] for r in records if (r.get("control") or {}).get("ok")]
    return {
        "requests": len(records),
        "ok": len(ok),
        "errors": dict(sorted(statuses.items())),
        "retried": sum(1 for r in records if r.get("retried")),
        "student_served": student,
        "student_share": student / len(ok) if ok else None,
        "escalated": sum(1 for r in ok if r.get("escalated")),
        "degraded": sum(1 for r in ok if r.get("degraded")),
        "client_ms": {
            "student": percentiles(r.get("client_ms") for r in ok if r.get("served_by") == "student"),
            "teacher": percentiles(r.get("client_ms") for r in ok if r.get("served_by") == "teacher"),
            "control": percentiles(c.get("client_ms") for c in controls),
        },
        "accuracy": accuracy_block([dict(r) for r in ok], label),
        "control": control_estimate(records, formula_reasoning),
    }


def _sorted_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Round records by (round, index), Scout records after them by (run, call): one order for every sum, so `verify`
    recomputes every float to the bit."""

    def key(r: Mapping[str, Any]) -> tuple[int, int, int]:
        if isinstance(r.get("round"), int):
            return (0, r["round"], r["i"])
        return (1, int(r.get("scout") or 0), r["i"])

    return sorted((dict(r) for r in records), key=key)


def scout_block(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The Scout phase's calls, against the recorded all-Ultra baseline (no control arm runs on Scout calls)."""
    ok = [r for r in records if r.get("status") == 200]
    c = sum(r.get("cost_usd") or 0.0 for r in ok)
    b = sum(r.get("all_teacher_cost_usd") or 0.0 for r in ok)
    return {
        "llm_calls": len(ok),
        "student_calls": sum(1 for r in ok if r.get("served_by") == "student"),
        "failed_calls": len(records) - len(ok),
        "cost_usd": c,
        "baseline_recorded_usd": b,
        "savings": 1 - c / b if b > 0 else None,
        "label": "against the recorded all-Ultra baseline",
    }


def client_values(records: Iterable[Mapping[str, Any]], formula_reasoning: int) -> tuple[dict[str, float], dict[str, str]]:
    """The client-side values and their sources, from the per-request records alone: what `verify` recomputes. The last
    round is the last one with an answered request; the cascade's accuracy is the last round's (the steady state the
    headline describes), the control arm's the whole run's (Ultra does not change between rounds)."""
    recs = _sorted_records(records)
    rounds = [r for r in recs if isinstance(r.get("round"), int) and r["round"] >= 1]
    answered = sorted({r["round"] for r in rounds if r.get("status") == 200})
    values: dict[str, float] = {}
    sources: dict[str, str] = {}

    def put(vid: str, value: float | None, source: str, why: str) -> None:
        if value is None or not math.isfinite(value):
            sources[vid] = f"not measured: {why}"
        else:
            values[vid] = value
            sources[vid] = source

    if not answered:
        for vid in CLIENT_VALUE_IDS:
            if not vid.startswith("live.scout."):
                sources[vid] = "not measured: no round traffic was answered"
    else:
        final = answered[-1]
        fin = [r for r in rounds if r["round"] == final]
        cf = control_estimate(fin, formula_reasoning)
        ca = control_estimate(rounds, formula_reasoning)
        few = f"fewer than {MIN_STUDENT_PAIRS} student-served requests paired with a control call"
        put(
            "live.final.cost_reduction_measured_pct",
            None if cf["measured_reduction"] is None else 100 * cf["measured_reduction"],
            f"round {final}: 1 - cascade cost / measured all-teacher baseline (ratio estimator on the control arm's "
            "student-served pairs, recorded baseline for teacher-served rows); per token",
            few,
        )
        put(
            "live.final.cost_reduction_recorded_pct",
            None if cf["recorded_reduction"] is None else 100 * cf["recorded_reduction"],
            f"round {final}: 1 - cascade cost / the gateway's recorded all-teacher baseline (token formula); per token",
            "no recorded baseline",
        )
        put(
            "live.run.cost_reduction_measured_pct",
            None if ca["measured_reduction"] is None else 100 * ca["measured_reduction"],
            "every round: 1 - cascade cost / measured all-teacher baseline; per token",
            few,
        )
        put(
            "live.run.cost_reduction_recorded_pct",
            None if ca["recorded_reduction"] is None else 100 * ca["recorded_reduction"],
            "every round: 1 - cascade cost / recorded all-teacher baseline; per token",
            "no recorded baseline",
        )
        ok_f = [r for r in fin if r.get("status") == 200]
        put(
            "live.final.student_p50_client_ms",
            nearest_rank(
                [r["client_ms"] for r in ok_f if r.get("served_by") == "student" and r.get("client_ms") is not None], 0.5
            ),
            f"round {final}: median client-observed time of student-served cascade requests, timed on the harness host "
            "(nearest rank)",
            "no student-served request in the last round",
        )
        put(
            "live.final.ultra_p50_client_ms",
            nearest_rank(
                [
                    r["control"]["client_ms"]
                    for r in fin
                    if (r.get("control") or {}).get("ok") and r["control"].get("client_ms") is not None
                ],
                0.5,
            ),
            f"round {final}: median client-observed time of the control arm's direct teacher calls, timed on the harness "
            "host (nearest rank)",
            "no control call in the last round",
        )
        student = sum(1 for r in ok_f if r.get("served_by") == "student")
        put(
            "live.final.student_share_pct",
            _pct(student, len(ok_f)),
            f"round {final}: student-served share of answered requests",
            "no answered request",
        )
        put(
            "live.final.paired_agreement_pct",
            cf["agreement"]["pct"],
            f"round {final}: student answers agreeing with the control arm's teacher answer (task.agree), on paired "
            "student-served requests",
            "no paired student-served request",
        )
        put(
            "live.run.requests",
            float(sum(1 for r in rounds if r.get("status") == 200)),
            "every round: cascade requests answered (200)",
            "no answered request",
        )
        put("live.control.n", float(ca["ok"]), "every round: control calls that answered", "no control call")
        put(
            "live.control.reasoning_tokens_mean",
            ca["reasoning_tokens"]["mean"],
            f"every round: mean reasoning tokens per control call (the formula assumes {formula_reasoning})",
            "no control call reported reasoning tokens",
        )
        put(
            "live.control.ratio_student",
            ca["ratio_student"]["ratio"] if ca["status"] == "ok" else None,
            "every round: ratio estimator sum(control cost) / sum(recorded baseline) over student-served pairs",
            few,
        )
        ok_all = [r for r in rounds if r.get("status") == 200]
        acc_f = accuracy_block(ok_f, "")
        acc_a = accuracy_block(ok_all, "")
        for side, block, scope in (("cascade", acc_f, f"round {final}"), ("ultra", acc_a, "every round")):
            arm = block["cascade" if side == "cascade" else "control"]
            for diff in ("easy", "hard"):
                d = arm["by_difficulty"].get(diff)
                who = "cascade answers" if side == "cascade" else "control (teacher alone) answers"
                put(
                    f"live.accuracy.{side}_{diff}_pct",
                    None if d is None else d["pct"],
                    f"{scope}: {who} matching the template labels on {diff} templates (invalid counts as wrong)",
                    f"no {diff} template answered",
                )
    scout = [r for r in recs if r.get("scout") is not None]
    if scout:
        sb = scout_block(scout)
        put("live.scout.student_calls", float(sb["student_calls"]), "Scout phase: calls the student answered", "none")
        put("live.scout.llm_calls", float(sb["llm_calls"]), "Scout phase: model calls answered", "none")
        put(
            "live.scout.savings_pct",
            None if sb["savings"] is None else 100 * sb["savings"],
            "Scout phase: 1 - cost / recorded all-Ultra baseline (no control arm on Scout calls); per token",
            "no recorded baseline",
        )
    else:
        for vid in ("live.scout.student_calls", "live.scout.llm_calls", "live.scout.savings_pct"):
            sources[vid] = "not measured: no Scout phase (--scout-runs 0)"
    return values, sources


# ---------------------------------------------------------------------------------------------------------------------
# Ledger windows
# ---------------------------------------------------------------------------------------------------------------------
def _teacher_part(row: Mapping[str, Any]) -> float:
    """A request's teacher spend: `teacher_cost_usd`, or its whole `cost_usd` for a row without the split."""
    t = row.get("teacher_cost_usd")
    return float(t if t is not None else (row.get("cost_usd") or 0.0))


def window_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A round's ledger snapshot (taken when the round ends, so a later retention fold cannot change a published number):
    served and route counts, audits of student-served answers with a one-sided 95% Clopper-Pearson bound and the Wilson
    interval (canary and live audits pooled; `audits_by_version` splits them), the `label_p` values seen, cost sums, and
    per-route latency percentiles: `latency_ms` (the providers' part) always, `wall_ms` (the whole cascade on the
    gateway's clock) when the ledger records it."""
    served = Counter(str(r.get("served_by")) for r in rows)
    routes = Counter(str(r.get("route") or "unrecorded") for r in rows)
    student = [r for r in rows if r.get("served_by") == "student"]
    audited = [r for r in student if r.get("audited")]
    agree = sum(1 for r in audited if r.get("audit_agree") == 1)
    n = len(audited)
    by_version: dict[str, dict[str, int]] = {}
    for r in student:
        v = by_version.setdefault(str(r.get("student_version")), {"served": 0, "audits": 0, "agree": 0})
        v["served"] += 1
        if r.get("audited"):
            v["audits"] += 1
            v["agree"] += int(r.get("audit_agree") == 1)

    def lat(col: str, key: str) -> dict[str, Any]:
        groups: dict[str, list[float]] = {}
        for r in rows:
            if r.get(col) is not None:
                groups.setdefault(str(r.get(key) or "unrecorded"), []).append(float(r[col]))
        return {g: percentiles(v) for g, v in sorted(groups.items())}

    has_wall = any(r.get("wall_ms") is not None for r in rows)
    return {
        "rows": len(rows),
        "served_by": dict(sorted(served.items())),
        "routes": dict(sorted(routes.items())),
        "escalated": sum(1 for r in rows if r.get("escalated")),
        "stored": sum(1 for r in rows if r.get("stored")),
        "optional_failed": sum(1 for r in rows if r.get("optional_failed")),
        "audits": {
            "n": n,
            "agree": agree,
            "precision": agree / n if n else None,
            "cp_lower": cp_lower(agree, n, CP_DELTA) if n else None,
            "wilson": list(wilson(agree, n)) if n else None,
        },
        "by_version": dict(sorted(by_version.items())),
        "label_p": sorted({float(r["label_p"]) for r in rows if r.get("label_p") is not None}),
        "cost": {
            "cost_usd": sum(float(r.get("cost_usd") or 0.0) for r in rows),
            "baseline_cost_usd": sum(float(r.get("baseline_cost_usd") or 0.0) for r in rows),
            "teacher_usd": sum(_teacher_part(r) for r in rows),
            "student_usd": sum(float(r.get("student_cost_usd") or 0.0) for r in rows),
            "split_unknown": sum(1 for r in rows if r.get("student_cost_usd") is None),
        },
        "latency_ms": {"by_route": lat("latency_ms", "route"), "by_served_by": lat("latency_ms", "served_by")},
        "wall_ms": {"by_route": lat("wall_ms", "route"), "by_served_by": lat("wall_ms", "served_by")} if has_wall else None,
    }


# ---------------------------------------------------------------------------------------------------------------------
# Model clients
# ---------------------------------------------------------------------------------------------------------------------
class _Metered:
    """A model client that counts what an arm spends, priced as the router prices a call (`Ratchet._call_cost`: the
    prompt once, every choice's output; cached input priced in full on both sides). Thread-safe."""

    def __init__(self, provider: Provider, price: Price, spent: float = 0.0, calls: int = 0, max_call: float = 0.0) -> None:
        self.provider = provider
        self.price = price
        self.lock = threading.Lock()
        self.spent = spent
        self.calls = calls
        self.max_call = max_call  # the most expensive call so far: what the next one is assumed to cost
        self.reserved = 0.0
        self.failures: Counter[str] = Counter()

    def reserve(self, usd: float, cap: float) -> bool:
        """Hold `usd` for one call if the spend so far, the calls in flight and this one stay within `cap`: concurrent
        workers cannot all pass the check and then overshoot together."""
        with self.lock:
            if self.spent + self.reserved + usd > cap:
                return False
            self.reserved += usd
            return True

    def release(self, usd: float) -> None:
        with self.lock:
            self.reserved = max(0.0, self.reserved - usd)

    def complete(self, messages: list[dict[str, Any]], model: str, *args: Any, **kwargs: Any) -> Any:
        try:
            cs = self.provider.complete(messages, model, *args, **kwargs)
        except Exception as e:
            with self.lock:
                self.calls += 1
                self.failures[describe_failure(e)] += 1
            raise
        usd = self.price.cost(cs[0].in_tokens, sum(c.out_tokens for c in cs)) if cs else 0.0
        with self.lock:
            self.calls += 1
            self.spent += usd
            self.max_call = max(self.max_call, usd)
        return cs

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "spent_usd": self.spent,
                "calls": self.calls,
                "max_call_usd": self.max_call,
                "failures": dict(sorted(self.failures.items())),
            }


def _list_models(provider: Provider) -> list[str]:
    """`GET {base}/models` through the provider's own client (its key and transport). A failure raises."""
    client = getattr(provider, "client", None)
    base = getattr(provider, "base_url", None)
    if not isinstance(client, httpx.Client) or not isinstance(base, str):
        raise TypeError("the provider has no HTTP client to list models with")
    r = client.get(f"{base}/models")
    r.raise_for_status()
    data = r.json().get("data")
    return [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)] if isinstance(data, list) else []


def code_identity(run: RunFn) -> dict[str, Any]:
    """The code that ran: `git rev-parse HEAD` (through `run`), else RATCHET_GIT_SHA, else null; a SHA-256 over the
    sorted `ratchet/**/*.py` and `sim/**/*.py` sources (names and bytes), which works in an image without .git; and
    `ratchet.__version__`."""
    sha: str | None = None
    root = Path(__file__).resolve().parents[1]
    try:  # in the package's own tree, never the shell's current repository
        out = run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=str(root))
        text = (getattr(out, "stdout", "") or "").strip()
        sha = text if _HEX_SHA.fullmatch(text) else None
    except Exception:  # noqa: BLE001 - no git, no repository: fall back to the variable
        sha = None
    if sha is None:
        env = os.environ.get("RATCHET_GIT_SHA", "").strip().lower()
        sha = env if _HEX_SHA.fullmatch(env) else None
    h = hashlib.sha256()
    for pkg in ("ratchet", "sim"):
        for path in sorted((root / pkg).rglob("*.py")):
            h.update(path.relative_to(root).as_posix().encode() + b"\0")
            h.update(path.read_bytes() + b"\0")
    return {"git_sha": sha, "code_digest": f"sha256:{h.hexdigest()}", "version": __version__}


# ---------------------------------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class PlanInputs:
    """Everything the plan reads; the planning assumptions are printed with it."""

    rounds: int
    requests: int
    share: float
    train_hours: float
    night_minutes: float
    control_share: float
    control_max_usd: float
    concurrency: int
    nights: bool
    scout_runs: int
    audit_rate: float
    canary_rate: float | None
    min_audits: int
    min_shadow: int
    golden_fraction: float
    cert_target: float
    cert_delta: float
    prompt_tokens: int
    teacher_out_tokens: int
    teacher_call_usd: float
    student_endpoint: bool
    endpoint_usd_per_hour: float
    train_usd_per_hour: float
    hosted_student_call_usd: float
    has_live: bool
    prices_verified: bool
    max_usd: float
    measured: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)  # profile_check: the planned gate settings and those in force


def _round_model(k: int, inp: PlanInputs, share: float) -> dict[str, float]:
    """Expected traffic of round k under the planning model: round 1 is a cold start unless a version is live; after a
    night, the new version goes through shadow (only when a version is live), canary 10% and canary 50% for as many
    requests as `plan_stage_requests` says its audits need, then serves at `share`. Canary-served answers are audited at
    the canary rate, the rest at RATCHET_AUDIT_RATE; arbitrations are PLAN_ARBITRATION_RATE of the shadow's would-serve
    answers."""
    r = float(inp.requests)
    c = inp.canary_rate if inp.canary_rate is not None else inp.audit_rate
    a = inp.audit_rate
    live_before = inp.has_live or (inp.nights and k >= 3)
    new_version = inp.nights and k >= 2
    if not new_version:
        served = share * r if live_before else 0.0
        canary_served = arbitrations = 0.0
    else:
        left = r

        def take(n: int | None) -> float:
            nonlocal left
            got = left if n is None else min(left, float(n))
            left -= got
            return got

        r_sh = take(math.ceil(inp.min_shadow / share) if share > 0 else None) if live_before else 0.0
        r10 = take(plan_stage_requests(inp.min_audits, CANARY_STEPS[0] / 100 * share * c))
        r50 = take(plan_stage_requests(inp.min_audits, CANARY_STEPS[1] / 100 * share * c))
        r_live = left
        canary_served = share * (CANARY_STEPS[0] / 100 * r10 + CANARY_STEPS[1] / 100 * r50)
        served = (share * (r_sh + r10 + r50) if live_before else canary_served) + share * r_live
        arbitrations = PLAN_ARBITRATION_RATE * share * r_sh
    audits = c * canary_served + a * (served - canary_served)
    sampled = r if (live_before or new_version) else 0.0  # requests that reach the student
    return {
        "served": served,
        "teacher_served": r - served,
        "audits": audits,
        "arbitrations": arbitrations,
        "teacher_calls": (r - served) + audits + arbitrations,
        "sampled": sampled,
    }


def make_plan(inp: PlanInputs, start_round: int = 1, spent: float = 0.0) -> dict[str, Any]:
    """The plan: requests per release stage for `min_audits` audits with 90% probability (configured canary rate, 0.5
    and 1.0), the golden size a certifiable first version needs, and per round the teacher calls, control calls,
    training, endpoint and cumulative USD. From `start_round` (a re-plan after a round) on top of `spent`."""
    m = inp.measured
    share = m.get("share", inp.share)
    call_usd = m.get("teacher_call_usd", inp.teacher_call_usd)
    canary = inp.canary_rate if inp.canary_rate is not None else inp.audit_rate
    stages = {}
    for name, step in (("canary 10%", CANARY_STEPS[0]), ("canary 50%", CANARY_STEPS[1])):
        stages[name] = {
            label: plan_stage_requests(inp.min_audits, step / 100 * inp.share * rate)
            for label, rate in (("configured", canary), ("0.5", 0.5), ("1.0", 1.0))
        }
    shadow = plan_stage_requests(inp.min_shadow, inp.share)
    stages["shadow"] = {"configured": shadow, "0.5": shadow, "1.0": shadow}
    need = required_rows(inp.cert_target, inp.cert_delta)[0]
    golden_need = math.ceil(need / max(inp.share, 1e-9) * 2 / inp.golden_fraction) if inp.golden_fraction > 0 else None
    # Night School trains only on enough new examples: the teacher answers round 1 stores outside the golden set (an
    # upper bound: a repeated request or an invalid answer is not a new example).
    first_examples = _round_model(start_round, inp, share)["teacher_served"] * (1 - inp.golden_fraction)
    rounds = []
    total = spent
    control_left = max(0.0, inp.control_max_usd - m.get("control_spent", 0.0))
    for k in range(start_round, inp.rounds + 1):
        rm = _round_model(k, inp, share)
        teacher_usd = rm["teacher_calls"] * call_usd
        student_usd = 0.0 if inp.student_endpoint else rm["sampled"] * inp.hosted_student_call_usd
        control_calls = inp.requests * inp.control_share
        control_usd = min(control_left, control_calls * call_usd)
        control_left -= control_usd
        night = inp.nights and k < inp.rounds
        training_usd = inp.train_hours * inp.train_usd_per_hour if night else 0.0
        seconds = m.get("round_seconds")
        if seconds is None:
            busy = rm["teacher_served"] * PLAN_TEACHER_S + rm["served"] * PLAN_STUDENT_S + control_calls * PLAN_TEACHER_S
            seconds = busy / max(1, inp.concurrency)
        hours = seconds / 3600 + (inp.night_minutes / 60 if night else 0.0)
        endpoint_usd = hours * inp.endpoint_usd_per_hour if inp.student_endpoint else 0.0
        if k == 1 and start_round == 1:
            teacher_usd += call_usd  # the probe
        row_total = teacher_usd + student_usd + control_usd + training_usd + endpoint_usd
        total += row_total
        rounds.append(
            {
                "round": k,
                "teacher_calls": rm["teacher_calls"],
                "teacher_usd": teacher_usd,
                "student_token_usd": student_usd,
                "control_calls": control_calls,
                "control_usd": control_usd,
                "training_usd": training_usd,
                "endpoint_hours": hours if inp.student_endpoint else 0.0,
                "endpoint_usd": endpoint_usd,
                "round_usd": row_total,
                "cumulative_usd": total,
            }
        )
    scout_usd = inp.scout_runs * PLAN_SCOUT_CALLS * call_usd if start_round <= inp.rounds + 1 else 0.0
    total += scout_usd
    return {
        "inputs": asdict(inp),
        "start_round": start_round,
        "spent_usd": spent,
        "stages": stages,
        "golden": {
            "held_out_rows": need,
            "stored_answers_needed": golden_need,
            "round_one_requests": inp.requests,
            "enough": golden_need is not None and inp.requests >= golden_need,
        },
        "nights": {
            "min_new_examples": NIGHT_MIN_EXAMPLES,
            "first_round_training_examples": first_examples,
            "enough": first_examples >= NIGHT_MIN_EXAMPLES,
        },
        "rounds": rounds,
        "scout": {"runs": inp.scout_runs, "usd": scout_usd, "tavily_credits_max": TAVILY_CREDITS_PER_RUN * inp.scout_runs},
        "total_usd": total,
        "max_usd": inp.max_usd,
        "within": total <= inp.max_usd,
    }


def render_plan(plan: Mapping[str, Any]) -> str:
    inp = plan["inputs"]
    canary = inp["canary_rate"]
    lines = []
    if plan["start_round"] > 1:
        lines.append(f"re-plan from round {plan['start_round']} (spent so far {_usd(plan['spent_usd'])}):")
    lines.append(
        f"plan: {inp['rounds']} rounds x {inp['requests']:,} requests, control share {inp['control_share']:.2f}, "
        f"nights {'in process' if inp['nights'] else 'off'}"
    )
    lines.append(
        f"assumptions: student share {inp['share']:.2f}, {inp['prompt_tokens']} input and {inp['teacher_out_tokens']} "
        f"output tokens per teacher call (${inp['teacher_call_usd']:.5f} each, "
        f"{'verified prices' if inp['prices_verified'] else 'list prices, unverified'}), "
        f"training {inp['train_hours']:.2f} h a night, nights {inp['night_minutes']:.0f} min, {PLAN_TEACHER_S:.0f} s per "
        f"teacher-served and {PLAN_STUDENT_S:.0f} s per student-served request at concurrency {inp['concurrency']}"
    )
    measured = inp.get("measured") or {}
    if measured:
        lines.append("measured so far: " + ", ".join(f"{k} {v:.4g}" for k, v in sorted(measured.items())))
    rate_text = f"{canary:g}" if canary is not None else "unset (= RATCHET_AUDIT_RATE)"
    lines.append(f"audit rates: RATCHET_AUDIT_RATE {inp['audit_rate']:g}; RATCHET_CANARY_AUDIT_RATE {rate_text}")
    prof = inp.get("profile") or {}
    if prof:
        cells = ", ".join(f"{name}={want} (in force: {prof['in_force'][name]})" for name, want in prof["wanted"].items())
        verdict = "in force" if prof["matches"] else "NOT in force: run refuses until it is (or --gate-profile as-set)"
        lines.append(f"gate profile: {cells}: {verdict}")
    lines.append(
        f"release stages: requests for {inp['min_audits']} audits (shadow: {inp['min_shadow']} would-serve answers) with 90% "
        "probability"
    )
    lines.append(f"  {'stage':<12} {'configured':>12} {'canary 0.5':>12} {'canary 1.0':>12}")
    for name, row in plan["stages"].items():
        cells = [f"{row[k]:,}" if row[k] is not None else "never" for k in ("configured", "0.5", "1.0")]
        lines.append(f"  {name:<12} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12}")
    if canary is None:
        lines.append(
            "  note: RATCHET_CANARY_AUDIT_RATE is unset, so the 10% canary stage alone needs about "
            f"{(plan['stages']['canary 10%']['configured'] or 0) / max(1, inp['requests']):.0f} times the round size; "
            "set RATCHET_CANARY_AUDIT_RATE=0.5 on the gateway"
        )
    g = plan["golden"]
    if g["stored_answers_needed"] is not None:
        lines.append(
            f"golden: a certifiable first version needs about {g['stored_answers_needed']:,} stored teacher answers in "
            f"round 1 ({g['held_out_rows']} held-out served rows at {inp['cert_target']:g} / {inp['cert_delta']:g}, coverage "
            f"{inp['share']:.2f}, golden fraction {inp['golden_fraction']:g}); round 1 sends {g['round_one_requests']:,}: "
            + ("enough" if g["enough"] else "short")
        )
    nt = plan.get("nights") or {}
    if inp["nights"] and nt:
        lines.append(
            f"nights: a night trains only on at least {nt['min_new_examples']} new examples; round {plan['start_round']} "
            f"stores at most about {nt['first_round_training_examples']:,.0f} outside the golden set: "
            + ("enough" if nt["enough"] else "short, so its night will skip; send more requests per round")
        )
    lines.append(
        f"  {'round':>5} {'teacher calls':>14} {'teacher':>10} {'control':>10} {'training':>10} {'endpoint':>10} {'total':>10}"
    )
    for r in plan["rounds"]:
        lines.append(
            f"  {r['round']:>5} {r['teacher_calls']:>14,.0f} {_usd(r['teacher_usd'] + r['student_token_usd']):>10} "
            f"{_usd(r['control_usd']):>10} {_usd(r['training_usd']):>10} {_usd(r['endpoint_usd']):>10} "
            f"{_usd(r['cumulative_usd']):>10}"
        )
    sc = plan["scout"]
    lines.append(
        f"Tavily credits: up to {TAVILY_CREDITS_PER_RUN} x {sc['runs']} = {sc['tavily_credits_max']}; Scout {_usd(sc['usd'])}"
    )
    verdict = "within" if plan["within"] else "OVER"
    lines.append(f"verdict: planned total {_usd(plan['total_usd'])} {verdict} --max-usd {_usd(plan['max_usd'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m ratchet.liverun", description="Live-run harness (ADR 0121)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "smoke", "run"):
        p = sub.add_parser(name)
        p.add_argument("--base-url", default="http://localhost:8000/v1")
        p.add_argument("--client", default="liverun", help="the harness key's name in RATCHET_API_KEYS")
        p.add_argument("--workload", default="tickets", help="tickets, research, mixed, or file:PATH")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--rounds", type=int, default=None)
        p.add_argument("--requests", type=int, default=None)
        p.add_argument("--max-usd", type=float, default=None)
        p.add_argument("--control-share", type=float, default=None)
        p.add_argument("--control-max-usd", type=float, default=None)
        p.add_argument("--concurrency", type=int, default=4)
        p.add_argument("--batch", type=int, default=50)
        p.add_argument("--until-settled", action=argparse.BooleanOptionalAction, default=True)
        p.add_argument("--max-extend", type=int, default=None)
        p.add_argument("--nights", choices=("inprocess", "off"), default=None)
        p.add_argument("--on-night-failure", choices=("stop", "continue"), default="stop")
        p.add_argument("--drift-round", type=int, default=None)
        p.add_argument("--untuned-arm", action=argparse.BooleanOptionalAction, default=None)
        p.add_argument("--scout-runs", type=int, default=0)
        p.add_argument("--plan-share", type=float, default=0.7)
        p.add_argument("--plan-train-hours", type=float, default=0.5)
        p.add_argument("--plan-night-minutes", type=float, default=45.0)
        p.add_argument("--out", default=None)
        p.add_argument("--no-endpoint-check", action="store_true")
        p.add_argument(
            "--gate-profile",
            choices=("planned", "as-set"),
            default=None,
            help="planned (run's default): refuse unless RATCHET_SEQUENTIAL=1 and RATCHET_CANARY_AUDIT_RATE=0.5 are in force",
        )
        if name != "plan":
            p.add_argument("--repeat-of", default=None, metavar="RUN_DIR", help="run the same plan and seed again")
        if name == "run":
            p.add_argument("--resume", default=None, metavar="RUN_DIR")
    rp = sub.add_parser("report")
    rp.add_argument("run_dir")
    rp.add_argument("--bill-usd", type=float, default=None)
    rp.add_argument("--bill-note", default=None)
    rp.add_argument("--repeat", default=None, metavar="REPEAT_DIR", help="attach a repeat run's comparison")
    rp.add_argument("--publish", action="store_true")
    rp.add_argument("--allow-partial", action="store_true")
    vp = sub.add_parser("verify")
    vp.add_argument("dir")
    return ap


def _resolve(args: argparse.Namespace) -> argparse.Namespace:
    """Fill each command's defaults and check every option; InputError names the bad one."""
    smoke = args.cmd == "smoke"
    if getattr(args, "resume", None) and getattr(args, "repeat_of", None):
        raise InputError("--resume and --repeat-of do not go together")
    if getattr(args, "resume", None) or getattr(args, "repeat_of", None):
        # The run's own arguments come back from its state.json; only the cap, the gateway's URL and (for a repeat)
        # the run directory may change.
        if args.max_usd is not None and not (math.isfinite(args.max_usd) and args.max_usd > 0):
            raise InputError(f"--max-usd must be a positive amount, not {args.max_usd}")
        return args
    if args.rounds is None:
        args.rounds = 1 if smoke else 4
    if args.requests is None:
        args.requests = 20 if smoke else 1500
    if args.control_share is None:
        args.control_share = 0.25 if smoke else 0.05
    if args.nights is None:
        args.nights = "off" if smoke else "inprocess"
    if args.max_usd is None:
        if args.cmd == "run":
            raise InputError("run needs --max-usd: the hard cap on the run's total spend")
        args.max_usd = 1.00 if smoke else 30.0
    if args.control_max_usd is None:
        args.control_max_usd = 0.2 * args.max_usd
    if args.max_extend is None:
        args.max_extend = args.requests
    if args.gate_profile is None:
        args.gate_profile = "planned" if args.cmd == "run" else "as-set"

    def need(ok: bool, message: str) -> None:
        if not ok:
            raise InputError(message)

    need(args.rounds >= 1, f"--rounds must be 1 or more, not {args.rounds}")
    need(args.requests >= 1, f"--requests must be 1 or more, not {args.requests}")
    need(math.isfinite(args.max_usd) and args.max_usd > 0, f"--max-usd must be a positive amount, not {args.max_usd}")
    need(0 <= args.control_share <= 1, f"--control-share must be from 0 to 1, not {args.control_share}")
    need(
        math.isfinite(args.control_max_usd) and args.control_max_usd >= 0,
        f"--control-max-usd must be 0 or more, not {args.control_max_usd}",
    )
    need(1 <= args.concurrency <= 64, f"--concurrency must be 1 to 64, not {args.concurrency}")
    need(args.batch >= 1, f"--batch must be 1 or more, not {args.batch}")
    need(args.max_extend >= 0, f"--max-extend must be 0 or more, not {args.max_extend}")
    need(args.drift_round is None or args.drift_round >= 1, f"--drift-round must be 1 or more, not {args.drift_round}")
    need(0 <= args.scout_runs <= 10, f"--scout-runs must be 0 to 10, not {args.scout_runs}")
    need(0 <= args.plan_share <= 1, f"--plan-share must be from 0 to 1, not {args.plan_share}")
    need(args.plan_train_hours >= 0, f"--plan-train-hours must be 0 or more, not {args.plan_train_hours}")
    need(args.plan_night_minutes >= 0, f"--plan-night-minutes must be 0 or more, not {args.plan_night_minutes}")
    need(bool(_CLIENT.fullmatch(args.client)), "--client must be a key name (letters, digits, '.', '_', '-')")
    return args


def _base(url: str) -> str:
    """The gateway's /v1 base, without a trailing slash."""
    return url.rstrip("/")


def _health_url(base: str) -> str:
    return base.removesuffix("/v1").rstrip("/") + "/healthz"


def _retry_after(headers: Mapping[str, str]) -> float:
    raw = headers.get("retry-after")
    try:
        value = float(raw) if raw is not None else 1.0
    except ValueError:
        value = 1.0
    if not math.isfinite(value) or value < 0:
        value = 1.0
    return min(MAX_RETRY_AFTER_S, value)


@dataclass
class _Sent:
    status: int  # 0: no response (a transport failure)
    code: str | None
    body: dict[str, Any] | None
    headers: Mapping[str, str]
    client_ms: float


def _settings_block(s: Settings, policy: Any, cert: Any, release: ReleasePolicy) -> dict[str, Any]:
    """The run's settings, never a key or a base URL."""
    gen = s.gen_policy()
    return {
        "teacher_model": s.teacher_model,
        "judge_model": s.judge_model,
        "fallback_student_model": s.fallback_student_model,
        "student_base_model": s.student_base_model,
        "student_endpoint": bool(s.student_base),
        "samples": s.samples,
        "audit_rate": s.audit_rate,
        "canary_audit_rate": s.checked_canary_audit_rate(),
        "golden_fraction": s.golden_fraction,
        "confidence_threshold": s.confidence_threshold,
        "teacher_reasoning_tokens": s.teacher_reasoning_tokens,
        "gen": {
            "teacher_max_tokens": gen.teacher_max_tokens,
            "student_max_tokens": gen.student_max_tokens,
            "reasoning_headroom": gen.reasoning_headroom,
        },
        "cost_policy": asdict(policy),
        "prices_verified": policy.prices_verified,
        "teacher_usd_per_m_tokens": asdict(s.price(s.teacher_model)),
        "fallback_student_usd_per_m_tokens": asdict(s.price(s.fallback_student_model)),
        "cert_policy": asdict(cert),
        "release_policy": asdict(release),
        "redact_pii": s.redact_pii,
    }


class Harness:
    """One `smoke` or `run`: preflight, plan, rounds with nights between, arms, results. Every stop writes results
    first. The seams are default arguments of `main`; production code never passes them."""

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        client: httpx.Client | None,
        provider_factory: ProviderFactory,
        run: RunFn,
        http: httpx.Client | None,
        tavily: httpx.Client | None,
        clock: Callable[[], float],
        timer: Callable[[], float],
        sleep: Callable[[float], None],
        out: TextIO,
    ) -> None:
        self.args = args
        self.kind = "smoke" if args.cmd == "smoke" else "run"
        self.client = client
        self.provider_factory = provider_factory
        self.run_fn = run
        self.http = http
        self.tavily = tavily
        self.clock = clock
        self.timer = timer
        self.sleep = sleep
        self.out = out
        self.log_lines: list[str] = []
        self.log_path: Path | None = None
        self.dir: Path | None = None
        self.state: dict[str, Any] = {}
        self.records: list[dict[str, Any]] = []
        self.rec_lock = threading.Lock()
        self.tco_streak = 0
        self.led: Ledger | None = None
        self.own_client = client is None

    def close(self) -> None:
        """Close the ledger connection and the gateway client the harness opened itself (never a passed-in seam)."""
        if self.led is not None:
            self.led.db.close()
            self.led = None
        if self.own_client and self.client is not None:
            self.client.close()

    # ----- output -----
    def say(self, line: str) -> None:
        print(line, file=self.out, flush=True)
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        else:
            self.log_lines.append(line)

    # ----- the run -----
    def execute(self) -> int:
        try:
            self._setup()
        except InputError as e:
            self.say(f"liverun: {e}")
            return EXIT_INPUT
        plan = self._first_plan()
        self.say(render_plan(plan))
        if not plan["within"]:
            self.say("liverun: the plan exceeds --max-usd; nothing was sent")
            return EXIT_OVER_PLAN
        try:
            self._open_run(plan)
            self._probe()
        except InputError as e:
            self.say(f"liverun: {e}")
            if self.dir is not None and self.state.get("run_id"):
                self.state["stopped"] = {"reason": "preflight", "detail": str(e)}
                self._save_state()
                self._event("liverun_stop", f"liverun {self.state['run_id']} stopped: preflight")
            return EXIT_INPUT
        stopped: _Stop | None = None
        try:
            self._loop()
        except _Stop as s:
            stopped = s
        except KeyboardInterrupt:
            stopped = _Stop("interrupted", "Ctrl-C")
        except Exception as e:  # noqa: BLE001 - write what was measured; the class only (INV-12)
            stopped = _Stop("error", describe_failure(e))
        if stopped is not None:
            self.state["stopped"] = {"reason": stopped.reason, "detail": stopped.detail}
            self.say(f"stopped: {stopped.reason}" + (f" ({stopped.detail})" if stopped.detail else ""))
        else:
            self.state["stopped"] = None
        self.state["finished"] = self.clock()
        self._save_state()
        self._event(
            "liverun_stop", f"liverun {self.state['run_id']} " + (f"stopped: {stopped.reason}" if stopped else "finished")
        )
        results = self._write_results()
        render_markdown_to(self.dir, results)
        self.say(f"results: {self.dir / 'results.json'}")
        return EXIT_STOPPED if stopped is not None else EXIT_OK

    # ----- setup and preflight -----
    def _setup(self) -> None:
        """Every free check, in order, before the plan; the one paid check (the probe) comes after the plan."""
        a = self.args
        if getattr(a, "resume", None):
            self._load_resume(Path(a.resume))
            a = self.args
        elif getattr(a, "repeat_of", None):
            self._load_repeat(Path(a.repeat_of))
            a = self.args
        try:
            s = Settings(mode="live")
            s.check_reserved()
            self.policy = s.cost_policy()
            self.cert = s.cert_policy()
            self.gen = s.gen_policy()
            self.limits = s.limits()
            self.canary_rate = s.checked_canary_audit_rate()
            self.release = ReleasePolicy.from_env()
            licenses.require_distillable(s.teacher_model)
        except (ValueError, licenses.LicenseError) as e:
            raise InputError(str(e)) from None
        self.s = s
        self.modalities = parse_modalities(s.teacher_modalities)
        self.profile = profile_check(self.release, self.canary_rate)
        if getattr(a, "gate_profile", "as-set") == "planned" and not self.profile["matches"]:
            want = " and ".join(f"{name}={value}" for name, value in RUN_PROFILE)
            have = ", ".join(f"{name} is {self.profile['in_force'][name]}" for name in self.profile["differs"])
            raise InputError(
                f"the run's gate profile is not in force ({have}): set {want} in the gateway's .env and restart it, or "
                "pass --gate-profile as-set to measure the settings as they are"
            )
        self.key = os.environ.get("RATCHET_LIVERUN_KEY", "")
        if not self.key.strip():
            raise InputError(f"{KEY_ENV} is not set: export the key of '{a.client}:<key>' from RATCHET_API_KEYS (never a flag)")
        # The arms' own Token Factory key when one is set, so a runaway run cannot drain the key the demo serves with.
        self.arms_key_env = ARMS_KEY_ENV if os.environ.get("NEBIUS_API_KEY_LIVERUN", "").strip() else "NEBIUS_API_KEY"
        self.arms_key = os.environ.get("NEBIUS_API_KEY_LIVERUN", "").strip() or s.teacher_api_key
        if a.scout_runs and not s.tavily_api_key:
            raise InputError("--scout-runs needs TAVILY_API_KEY: without Tavily it is not the live Scout")
        if a.untuned_arm is None:  # off in a smoke run: the golden set costs more than a smoke run should
            a.untuned_arm = bool(s.student_base) and self.kind == "run"
        self.stream_label = workload_label(a.workload)
        self.stream = make_workload(a.workload, a.seed, a.drift_round)  # a bad file stops here
        if not os.path.exists(s.db_path):
            raise InputError("no ledger at RATCHET_DB: run the harness on the gateway's host with the gateway's .env")
        self.led = Ledger(s.db_path, s.golden_fraction, auto_day=True)
        try:
            for line in taskspec.install(self.led, s.tasks_file):  # the gateway's registry, so scoring uses its specs
                self.say(f"task skipped: {line}")
        except ValueError as e:
            raise InputError(f"RATCHET_TASKS_FILE: {e}") from None
        if isinstance(self.stream, FileStream):
            check_file_rows(self.stream.rows)
        if a.nights == "inprocess":
            if not s.student_base:
                raise InputError("nights in process need RATCHET_STUDENT_BASE (the endpoint adapters are loaded into)")
            self._check_train_cmd()
            if not a.no_endpoint_check and not any(iv["end_ts"] is None for iv in self.led.compute_intervals("student_endpoint")):
                raise InputError(
                    "no open student_endpoint interval: record it with python -m ratchet.cost endpoint up (or "
                    "deploy/student_endpoint.sh start); the GPU-hours view would miss the endpoint"
                )
        self.base = _base(a.base_url)
        if self.client is None:
            self.client = httpx.Client(timeout=httpx.Timeout(s.teacher_deadline_s + 120.0, connect=10.0))
        try:
            r = self.client.get(_health_url(self.base))
            health = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            raise InputError(f"the gateway's /healthz did not answer ({describe_failure(e)}); is it running?") from None
        if not isinstance(health, dict):
            raise InputError(f"the gateway's /healthz answered {r.status_code}")
        if health.get("mode") != "live":
            raise InputError("liverun measures a live gateway; for mock numbers run python -m ratchet.tour")
        teacher = provider_factory_call(
            self.provider_factory, s.teacher_base, self.arms_key, s.teacher_timeout(), s.teacher_retry()
        )
        saved = (self.state.get("arms") or {}).get("control") or {}
        self.control = _Metered(
            teacher,
            s.price(s.teacher_model),
            float(saved.get("spent_usd", 0.0)),
            int(saved.get("calls", 0)),
            float(saved.get("max_call_usd", 0.0)),
        )
        self.control.failures.update(saved.get("failures") or {})
        self.teacher_provider = teacher

    def _check_train_cmd(self) -> None:
        """The training command must run here: its first word on PATH, and a script it names (bash deploy/train_job.sh)
        present. The stock gateway image has neither (DEPLOYMENT.md step 7)."""
        cmd = os.environ.get("RATCHET_TRAIN_CMD", "bash deploy/train_job.sh {version}")
        try:
            words = shlex.split(cmd)
        except ValueError:
            raise InputError("RATCHET_TRAIN_CMD does not parse as a command") from None
        hint = "run the harness where nights can run (docs/DEPLOYMENT.md step 7)"
        if not words or shutil.which(words[0]) is None:
            raise InputError(f"RATCHET_TRAIN_CMD's program {words[0] if words else ''!r} is not on PATH: {hint}")
        if Path(words[0]).name in _SHELLS and len(words) > 1 and not words[1].startswith("-") and "{" not in words[1]:
            if not Path(words[1]).is_file():
                raise InputError(f"RATCHET_TRAIN_CMD's script {words[1]!r} is not here: {hint}")

    def _plan_inputs(self, max_usd: float) -> PlanInputs:
        assert self.led is not None
        return plan_inputs(
            self.args, self.s, self.policy, self.cert, self.canary_rate, self.release, self.led.live() is not None, max_usd
        )

    def _first_plan(self) -> dict[str, Any]:
        inp = self._plan_inputs(self.args.max_usd)
        self.plan_inputs = inp
        if self.state.get("rounds"):  # a resumed run: plan what is left on top of what was spent
            self._recount_spend()
            done = [r for r in self.state["rounds"] if r.get("done")]
            return make_plan(inp, start_round=len(done) + 1, spent=self.spent()["total_usd"])
        return make_plan(inp)

    # ----- the run directory and state -----
    def _open_run(self, plan: Mapping[str, Any]) -> None:
        if self.state.get("run_id"):  # resumed
            assert self.dir is not None
            self.log_path = self.dir / "log.txt"
            self._flush_log()
            self.say(f"resuming run {self.state['run_id']}")
            self.state["stopped"] = None
            self.state["max_usd"] = self.args.max_usd
            self.state["args"]["max_usd"] = self.args.max_usd  # a later resume keeps the raised cap
            self._save_state()
            return
        started = self.clock()
        run_id = datetime.fromtimestamp(started, tz=timezone.utc).strftime(RUN_ID_FORMAT)
        out = Path(self.args.out) if self.args.out else Path("evidence") / "live" / run_id
        if out.exists() and any(out.iterdir()):
            raise InputError(f"{out} is not empty: pass another --out, or --resume it")
        out.mkdir(parents=True, exist_ok=True)
        self.dir = out
        self.log_path = out / "log.txt"
        self._flush_log()
        assert self.led is not None
        args = {k: v for k, v in vars(self.args).items() if k not in UNSAVED_ARGS}
        self.state = {
            "generator": GENERATOR,
            "kind": self.kind,
            "run_id": run_id,
            "args": args,
            "started": started,
            "finished": None,
            "t0": self.led.now(),
            "first_id": self.led.last_ids()["requests"],
            "events_start": self.led.last_ids()["events"],
            "max_usd": self.args.max_usd,
            "probe": None,
            "rounds": [],
            "scout": None,
            "arms": {},
            "stopped": None,
            "code": code_identity(self.run_fn),
            "models": self._models(),
            "repeat_of": getattr(self, "repeat_of", None),
        }
        _write_json(out / "plan.json", plan)
        self._save_state()
        a = self.args
        self._event(
            "liverun_start",
            f"liverun {run_id} started: {self._traffic()} traffic, {a.rounds} rounds x {a.requests} requests, "
            f"cap ${a.max_usd:.2f} (key {a.client})",
        )

    def _traffic(self) -> str:
        w = self.args.workload
        return "file workload" if w.startswith("file:") else f"synthetic {w}"

    def _flush_log(self) -> None:
        assert self.log_path is not None
        if self.log_lines:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(self.log_lines) + "\n")
            self.log_lines = []

    def _save_state(self) -> None:
        if self.dir is None:
            return
        self.state["arms"] = {**self.state.get("arms", {}), "control": self.control.state()}
        _write_json(self.dir / "state.json", self.state)

    def _saved_args(self, run_dir: Path, kind: str, what: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """(state, the run's saved arguments) of an earlier run of `kind`; InputError when `run_dir` holds none."""
        path = run_dir / "state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise InputError(f"no readable state.json in {run_dir}") from None
        if (
            not isinstance(state, dict)
            or state.get("generator") != GENERATOR
            or not _RUN_ID.fullmatch(str(state.get("run_id")))
            or not isinstance(state.get("args"), dict)
        ):
            raise InputError(f"{path} is not a liverun state file")
        if state.get("kind") != kind:
            raise InputError(f"{run_dir} holds a {state.get('kind')} run: {what}")
        saved = {k: v for k, v in state["args"].items() if k not in UNSAVED_ARGS}
        return state, saved

    def _load_resume(self, run_dir: Path) -> None:
        """Continue an interrupted run: its arguments from state.json, with this command line's --max-usd (when given)
        and --base-url (never written)."""
        state, saved = self._saved_args(run_dir, "run", "only a run can be resumed; start a new smoke run instead")
        cap, base = self.args.max_usd, self.args.base_url
        self.args = argparse.Namespace(**{**saved, "cmd": "run", "resume": str(run_dir), "repeat_of": None, "base_url": base})
        if cap is not None:
            self.args.max_usd = cap
        self.dir = run_dir
        self.state = state
        self.records = list(read_records(run_dir / "requests.ndjson.gz"))

    def _load_repeat(self, run_dir: Path) -> None:
        """Run an earlier run's plan again (its workload, seed, rounds, arms and options) into a new run directory, so
        the two results show how much the numbers move between runs. Its values are kept now, so the comparison never
        depends on the earlier directory staying where it is."""
        state, saved = self._saved_args(run_dir, self.kind, f"repeat it with {state_kind_cmd(self.kind)}")
        first = _read_json(run_dir / "results.json")
        if not isinstance(first, dict) or first.get("generator") != GENERATOR or not isinstance(first.get("values"), dict):
            raise InputError(f"no liverun results.json in {run_dir}: only a finished or stopped run can be repeated")
        cap, base, out = self.args.max_usd, self.args.base_url, self.args.out
        self.args = argparse.Namespace(
            **{**saved, "cmd": self.args.cmd, "resume": None, "repeat_of": str(run_dir), "base_url": base, "out": out}
        )
        if cap is not None:
            self.args.max_usd = cap
        self.repeat_of = {
            "run_id": state["run_id"],
            "kind": state["kind"],
            "code_digest": (state.get("code") or {}).get("code_digest"),
            "values": {k: v for k, v in first["values"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)},
        }

    # ----- spend -----
    def _recount_spend(self) -> None:
        """Teacher (and, without an endpoint, student) spend of every row the key wrote since the run started."""
        assert self.led is not None
        self.read_id = int(self.state.get("first_id", 0))
        self.teacher_usd = self.student_token_usd = 0.0
        self.split_unknown = 0
        self._read_new_rows()

    def _read_new_rows(self) -> None:
        assert self.led is not None
        upto = self.led.last_ids()["requests"]
        if upto <= self.read_id:
            return
        w = self.led.client_window(self.args.client, self.read_id, upto)
        for r in w["rows"]:
            self.teacher_usd += _teacher_part(r)
            self.student_token_usd += float(r.get("student_cost_usd") or 0.0)
            self.split_unknown += r.get("student_cost_usd") is None
        self.read_id = upto

    def _compute(self, kind: str, t0: float, now: float) -> tuple[float, float]:
        assert self.led is not None
        segs = cost.segments(self.led.compute_intervals(kind, since=t0), now, lo=t0, hi=now)
        return cost.usage(segs)

    def spent(self) -> dict[str, float]:
        """What the run has spent so far: teacher calls from the ledger (every row the key wrote since the run started,
        the probe, extensions and the Scout phase included; a row without the split counts whole), the hosted student
        per token when there is no endpoint, else the endpoint's GPU-hours since the run started, training jobs since,
        and the arms' own calls."""
        assert self.led is not None
        self._read_new_rows()
        now = self.led.now()
        t0 = float(self.state.get("t0", now))
        endpoint_hours, endpoint_usd = self._compute("student_endpoint", t0, now) if self.s.student_base else (0.0, 0.0)
        training_hours, training_usd = self._compute("training_job", t0, now)
        student = 0.0 if self.s.student_base else self.student_token_usd
        control = self.control.state()["spent_usd"]
        untuned = float(((self.state.get("arms") or {}).get("untuned") or {}).get("spend_usd") or 0.0)
        total = self.teacher_usd + student + endpoint_usd + training_usd + control + untuned
        return {
            "teacher_usd": self.teacher_usd,
            "student_token_usd": student,
            "endpoint_hours": endpoint_hours,
            "endpoint_usd": endpoint_usd,
            "training_hours": training_hours,
            "training_usd": training_usd,
            "control_usd": control,
            "untuned_usd": untuned,
            "total_usd": total,
        }

    def _projected(self, n: int) -> float:
        """What the next `n` requests may cost: the p95 cascade cost per request measured so far (the plan's teacher call
        before any), times n, times (1 + control share), plus the endpoint's GPU time for a batch as long as the last."""
        with self.rec_lock:
            costs = [r["cost_usd"] for r in self.records if r.get("status") == 200 and r.get("cost_usd") is not None]
        per = nearest_rank(costs, 0.95) if costs else self.plan_inputs.teacher_call_usd
        proj = (per or 0.0) * n * (1 + self.args.control_share)
        if self.s.student_base:
            proj += self.policy.student.usd_per_hour * self.state.get("last_batch_s", 0.0) / 3600
        return proj

    def _cap_check(self, n: int, extra: float = 0.0, what: str = "") -> None:
        """Stop before `n` more requests (and `extra` USD of the harness's own calls) could pass --max-usd."""
        spent = self.spent()["total_usd"]
        proj = self._projected(n) + extra
        if spent + proj > self.args.max_usd:
            raise _Stop(
                "max_usd",
                f"spent {_usd(spent)}, the next {what or f'{n} requests'} projected at {_usd(proj)}, "
                f"cap {_usd(self.args.max_usd)}",
            )

    # ----- events -----
    def _event(self, kind: str, detail: str) -> None:
        """liverun_start, liverun_round and liverun_stop: key names, counts and reasons only (INV-12, INV-13)."""
        assert self.led is not None
        try:
            self.led.day = max(self.led.day, self.led.day_of(self.led.now()))
            self.led.event(kind, detail)
        except Exception as e:  # noqa: BLE001 - an event is a label; a failed write must not end a paid run
            self.say(f"event {kind} not written ({describe_failure(e)})")

    # ----- models -----
    def _models(self) -> dict[str, Any]:
        """Model ids as Token Factory and the vLLM endpoint list them, recorded and never enforced (the gateway's own
        /v1/models lists only 'ratchet'). One call each; a failure is recorded."""
        s = self.s
        out: dict[str, Any] = {}
        try:
            listed = _list_models(self.teacher_provider)
            out["teacher"] = {
                "listed_nvidia": sorted(m for m in listed if m.lower().startswith("nvidia/")),
                "teacher_model_listed": s.teacher_model in listed,
                "judge_model_listed": s.judge_model in listed,
                "fallback_student_model_listed": s.fallback_student_model in listed,
                "error": None,
            }
        except Exception as e:  # noqa: BLE001 - recorded, not fatal
            out["teacher"] = {"listed_nvidia": [], "error": describe_failure(e)}
        if s.student_base:
            try:
                student = provider_factory_call(
                    self.provider_factory, s.student_base, s.student_api_key, s.student_timeout(), None
                )
                out["student"] = {"listed": sorted(_list_models(student)), "error": None}
            except Exception as e:  # noqa: BLE001
                out["student"] = {"listed": [], "error": describe_failure(e)}
        else:
            out["student"] = {"listed": [], "error": None, "note": "no student endpoint: the hosted fallback model answers"}
        return out

    # ----- requests -----
    def _post(self, body: dict[str, Any], task: str, crid: str) -> _Sent:
        assert self.client is not None
        headers = {"Authorization": f"Bearer {self.key}", "X-Ratchet-Task": task, "X-Client-Request-Id": crid}
        t0 = self.timer()
        try:
            r = self.client.post(f"{self.base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            return _Sent(0, type(e).__name__, None, {}, (self.timer() - t0) * 1000)
        ms = (self.timer() - t0) * 1000
        try:
            data = r.json()
        except ValueError:
            data = None
        code = None
        if r.status_code != 200 and isinstance(data, dict) and isinstance(data.get("error"), dict):
            raw = data["error"].get("code")
            code = raw if isinstance(raw, str) else None
        return _Sent(r.status_code, code, data if r.status_code == 200 and isinstance(data, dict) else None, r.headers, ms)

    def _send(self, body: dict[str, Any], task: str, crid: str) -> tuple[_Sent, bool]:
        sent = self._post(body, task, crid)
        if sent.status in (429, 503) and sent.code in RETRY_CODES:
            self.sleep(_retry_after(sent.headers))
            return self._post(body, task, crid), True
        return sent, False

    def _control_call(self, body: dict[str, Any], task: tasklib.Task, truth: Any) -> dict[str, Any]:
        """The control arm's call: exactly the serving teacher's (`Ratchet._teacher_complete`): the request validated
        and normalised as the gateway does, the `serve` role's parameters, one choice. Straight to the teacher, never
        through the gateway or the ledger, so it can never become an example (INV-2, INV-10)."""
        estimate = max(self.plan_inputs.teacher_call_usd, self.control.max_call)
        if not self.control.reserve(estimate, self.args.control_max_usd):
            return {"called": False, "skipped": "cap"}
        try:
            req = validate_chat_request(body, self.limits)
            messages, _ = normalize_messages(req.messages, self.modalities, self.s.merge_system)
            gen = self.gen.plan(GenParams.from_request(req), 1).serve
            t0 = self.timer()
            try:
                cs = self.control.complete(
                    messages,
                    self.s.teacher_model,
                    n=1,
                    temperature=gen.temperature or 0.0,
                    logprobs=bool(gen.logprobs),
                    params=gen,
                )
                c = cs[0]
            except Exception as e:  # noqa: BLE001 - counted by class; the run goes on
                return {"called": True, "ok": False, "failure": describe_failure(e), "client_ms": (self.timer() - t0) * 1000}
        finally:
            self.control.release(estimate)
        ms = (self.timer() - t0) * 1000
        accurate, fields = score(task, c.text, truth)
        return {
            "called": True,
            "ok": True,
            "in_tokens": c.in_tokens,
            "out_tokens": c.out_tokens,
            "reasoning_tokens": c.reasoning_tokens,
            "cached_tokens": c.cached_tokens,
            "latency_ms": c.latency_ms,
            "client_ms": ms,
            "finish_reason": c.finish_reason,
            "cost": self.s.price(self.s.teacher_model).cost(c.in_tokens, c.out_tokens),
            "accurate": accurate,
            "fields": fields,
            "agree": None,
            "_text": c.text,
        }

    def _selected(self, k: int, i: int) -> tuple[bool, bool]:
        """(control request, control first): independent Bernoulli draws keyed by (seed, round, index), so they are
        reproducible on resume and independent of routing, cost and content. The order is random because prompt caching
        on Token Factory is unverified: neither arm is favoured."""
        seed = self.args.seed
        control = random.Random(f"{seed}:control:{k}:{i}").random() < self.args.control_share
        first = random.Random(f"{seed}:order:{k}:{i}").random() < 0.5
        return control, control and first

    def _one(self, k: int, i: int, item: Any) -> dict[str, Any]:
        """One cascade request (and its control call). Never raises: an unexpected failure comes back as `crash`."""
        crid = f"liverun-{self.state['run_id']}-r{k}-{i}"
        rec: dict[str, Any] = {
            "round": k,
            "i": i,
            "crid": crid,
            "task": item.task,
            "template": getattr(item, "template", None),
            "difficulty": getattr(item, "difficulty", None),
        }
        try:
            task = tasklib.get(item.task)
            truth = task.key(item.answer(item.labels))
            body = {"model": "ratchet", "messages": item.messages()}
            control, first = self._selected(k, i) if k >= 1 else (False, False)
            ctl = None
            if control and first:
                ctl = self._control_call(body, task, truth)
            sent, retried = self._send(body, item.task, crid)
            self._fill(rec, sent, retried, task, truth)
            if control and not first:
                ctl = self._control_call(body, task, truth) if sent.status == 200 else {"called": False, "skipped": "cascade"}
            if ctl is not None:
                ctl["order"] = "first" if first else "second"
                text = ctl.pop("_text", None)
                if ctl.get("ok") and rec.get("served_by") == "student" and rec.get("_text") is not None:
                    ctl["agree"] = bool(task.agree(rec["_text"], text))
                rec["control"] = ctl
            else:
                rec["control"] = None
        except Exception as e:  # noqa: BLE001 - the batch finishes; the run stops after it with the class only
            rec["crash"] = describe_failure(e)
        rec.pop("_text", None)
        return rec

    def _fill(self, rec: dict[str, Any], sent: _Sent, retried: bool, task: tasklib.Task, truth: Any) -> None:
        rec.update(status=sent.status, code=sent.code, retried=retried, client_ms=sent.client_ms)
        rec["x_request_id"] = sent.headers.get(REQUEST_ID_HEADER) if sent.headers else None
        spend = sent.headers.get(SPEND_HEADER) if sent.headers else None
        try:
            rec["spend_today"] = _finite(float(spend)) if spend is not None else None
        except ValueError:
            rec["spend_today"] = None
        if sent.status != 200:
            return
        body = sent.body or {}
        meta = body.get("ratchet") if isinstance(body.get("ratchet"), dict) else None
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = None
        served_by = meta.get("served_by") if meta else None
        if meta is None or not isinstance(text, str) or served_by not in ("student", "teacher"):
            rec.update(status=-1, code="bad_response")
            return
        version = meta.get("student_version")
        rec.update(
            served_by=served_by,
            student_version=version if isinstance(version, int) and not isinstance(version, bool) else None,
            escalated=bool(meta.get("escalated")),
            confidence=_finite(meta.get("confidence"), lo=-math.inf),
            cost_usd=_finite(meta.get("cost_usd")),
            all_teacher_cost_usd=_finite(meta.get("all_teacher_cost_usd")),
            latency_ms=_finite(meta.get("latency_ms")),
            degraded=bool(meta.get("degraded")),
        )
        accurate, fields = score(task, text, truth)
        rec.update(accurate=accurate, fields=fields, invalid=accurate is None)
        rec["_text"] = text

    # ----- batches -----
    def _batch(self, k: int, rnd: dict[str, Any], n: int) -> None:
        start = rnd["sent"]
        end = start + n
        items = [(i, self.draws.item(k, i)) for i in range(start, end)]  # in index order, before any is sent
        rnd["sent"] = end  # reserved first: a crash mid-batch never sends an index twice on resume
        if rnd.get("t0") is None:
            assert self.led is not None
            rnd["t0"], rnd["started"] = self.led.now(), self.clock()
        self._save_state()
        t_batch = self.clock()
        pool = ThreadPoolExecutor(max_workers=self.args.concurrency, thread_name_prefix="liverun")
        futures = [pool.submit(self._one, k, i, item) for i, item in items]
        try:
            results = [f.result() for f in futures]
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            done = [f.result() for f in futures if f.done() and not f.cancelled()]
            self._keep(done)
            raise
        pool.shutdown(wait=True)
        self.state["last_batch_s"] = max(0.0, self.clock() - t_batch)
        self._keep(results)
        self._judge_batch(k, rnd, results)

    def _keep(self, results: Sequence[dict[str, Any]]) -> None:
        kept = [r for r in results if "crash" not in r]
        with self.rec_lock:
            self.records.extend(kept)
        if self.dir is not None and kept:
            with gzip.open(self.dir / "requests.ndjson.gz", "at", encoding="utf-8") as f:
                for r in kept:
                    f.write(json.dumps(r, sort_keys=True) + "\n")

    def _judge_batch(self, k: int, rnd: dict[str, Any], results: Sequence[dict[str, Any]]) -> None:
        """Stop rules, in order: a crashed request, auth, the server's cap, the teacher down, too many errors, every
        control call failed. Then a progress line."""
        crashed = [r["crash"] for r in results if "crash" in r]
        errors: Counter[str] = Counter()
        peak = self.tco_streak  # a streak may start in the batch before and end inside this one
        for r in sorted((r for r in results if "crash" not in r), key=lambda r: r["i"]):
            status, code = r.get("status"), r.get("code")
            if status == 503 and code == TEACHER_CIRCUIT_OPEN:
                self.tco_streak += 1
                peak = max(peak, self.tco_streak)
            elif status == 200:
                self.tco_streak = 0
            if status != 200:
                errors[f"{status} {code or ''}".strip()] += 1
            if r.get("spend_today") is not None:
                rnd["spend_today"] = r["spend_today"]
        rnd["errors"] = dict(Counter(rnd.get("errors") or {}) + errors)
        self._save_state()
        self._progress(k, rnd)
        if crashed:
            raise _Stop("error", f"a request failed in the harness ({crashed[0]})")
        if any(r.get("status") == 401 for r in results):
            raise _Stop("auth", f"the gateway refused {KEY_ENV} for '{self.args.client}' (401)")
        caps = sorted({r["code"] for r in results if r.get("code") in SERVER_CAP_CODES})
        if caps:
            raise _Stop("server_cap", f"the gateway's own cap: {', '.join(caps)}")
        if peak >= TEACHER_DOWN_STREAK:
            raise _Stop("teacher_down", f"{peak} teacher_circuit_open answers in a row")
        failed = sum(errors.values())
        if failed > ERROR_SHARE * len(results):
            top = ", ".join(f"{name} x{n}" for name, n in errors.most_common(3))
            raise _Stop("gateway_errors", f"{failed} of {len(results)} requests failed: {top}")
        calls = [r["control"] for r in results if (r.get("control") or {}).get("called")]
        if calls and not any(c.get("ok") for c in calls):
            raise _Stop("control_failed", f"all {len(calls)} control calls of the batch failed ({calls[0].get('failure')})")

    def _progress(self, k: int, rnd: dict[str, Any]) -> None:
        with self.rec_lock:
            mine = [r for r in self.records if r.get("round") == k and r.get("status") == 200]
        share = sum(1 for r in mine if r.get("served_by") == "student") / len(mine) if mine else 0.0
        spent = self.spent()["total_usd"]
        errs = sum((rnd.get("errors") or {}).values())
        ext = f" +{rnd['extension']} extension" if rnd.get("extension") else ""
        self.say(
            f"round {k}  sent {rnd['sent']:,}/{rnd['base']:,}{ext}  student {share:.1%}  spend {_usd(spent)} of "
            f"{_usd(self.args.max_usd)}" + (f"  errors {errs}" if errs else "")
        )

    # ----- the probe -----
    def _probe(self) -> None:
        """One request (round 0) that proves the gateway writes the ledger at RATCHET_DB under this key's name, with the
        same audit rates as the harness reads. Skipped on resume (the run's earlier rows prove the ledger instead)."""
        assert self.led is not None
        a = self.args
        if self.state.get("probe"):
            p = self.state["probe"]
            if not self.led.client_window(a.client, p["after_id"], p["upto_id"])["rows"]:
                raise InputError("not the gateway's ledger: the run's earlier rows are not in RATCHET_DB")
            return
        after = self.led.last_ids()["requests"]
        item = make_workload(a.workload, a.seed + PROBE_SEED_OFFSET, None).sample(1)
        rec = self._one(0, 0, item)
        self._keep([rec])
        upto = self.led.last_ids()["requests"]
        self.state["probe"] = {"after_id": after, "upto_id": upto, "crid": rec["crid"]}
        self._save_state()
        if "crash" in rec:
            raise InputError(f"the probe request failed in the harness ({rec['crash']})")
        status = rec.get("status")
        if status == 401:
            raise InputError(f"the gateway refused {KEY_ENV} (401): add {a.client}:<key> to RATCHET_API_KEYS")
        if status != 200:
            raise InputError(f"the probe request answered {status} {rec.get('code') or ''}".strip())
        w = self.led.client_window(a.client, after, upto)
        if not w["rows"]:
            if w["no_client"]:
                raise InputError(
                    f"the gateway runs without RATCHET_API_KEYS: add {a.client}:<key> to RATCHET_API_KEYS (requests must "
                    "carry the key's name)"
                )
            if w["other_clients"]:
                raise InputError(f"the probe's row carries another key name: pass that name as --client, not {a.client!r}")
            raise InputError("not the gateway's ledger: RATCHET_DB points at another file than the gateway's")
        row = w["rows"][-1]
        if row.get("client_request_id") is not None and row["client_request_id"] != rec["crid"]:
            raise InputError("not the gateway's ledger: the probe's request id is not on its row")
        if row.get("served_by") == "student" and row.get("label_p") is not None:
            v = self.led.version(int(row["student_version"])) if row.get("student_version") is not None else None
            expected = (
                self.canary_rate if v is not None and v.state == "canary" and self.canary_rate is not None else self.s.audit_rate
            )
            if abs(float(row["label_p"]) - expected) > 1e-12:
                raise InputError(
                    "the gateway audits at another rate than this shell's settings: source the gateway's .env before "
                    "running the harness"
                )

    # ----- rounds -----
    def _loop(self) -> None:
        a = self.args
        self.draws = Draws(self.stream)
        for r in self.state["rounds"]:  # a resumed run: replay every draw already sent
            for i in range(r["sent"]):
                self.draws.item(r["round"], i)
        self._recount_spend()
        for k in range(1, a.rounds + 1):
            rnd = next((r for r in self.state["rounds"] if r["round"] == k), None)
            if rnd is None:
                assert self.led is not None
                rnd = {
                    "round": k,
                    "after_id": self.led.last_ids()["requests"],
                    "upto_id": None,
                    "base": a.requests,
                    "sent": 0,
                    "extension": 0,
                    "settled": None,
                    "done": False,
                    "night": None,
                    "errors": {},
                    "spend_today": None,
                    "t0": None,
                    "t1": None,
                }
                self.state["rounds"].append(rnd)
                self._save_state()
            if not rnd["done"]:
                self._traffic_round(k, rnd)
                self._finish_round(k, rnd)
            if k < a.rounds and a.nights == "inprocess" and self._night_due(k, rnd):
                self._replan(k)
                self._night(k, rnd)
        if a.untuned_arm and self.state.get("arms", {}).get("untuned") is None:
            self._untuned_arm()
        if a.scout_runs and not (self.state.get("scout") or {}).get("done"):
            self._scout_phase()

    def _night_due(self, k: int, rnd: dict[str, Any]) -> bool:
        """The night after round k has not run, or it failed and the run stopped there (a resumed run tries it again,
        after the operator fixed what failed; with --on-night-failure continue the run went on, so it stays failed)."""
        night = rnd.get("night")
        if night is None:
            return True
        later = any(r["round"] > k for r in self.state["rounds"])
        if night.get("outcome") == "night_failed" and not later:
            rnd.setdefault("failed_nights", []).append({k2: night.get(k2) for k2 in ("failure", "t0", "t1")})
            rnd["night"] = None
            return True
        return False

    def _traffic_round(self, k: int, rnd: dict[str, Any]) -> None:
        a = self.args
        while rnd["sent"] < rnd["base"]:
            n = min(a.batch, rnd["base"] - rnd["sent"])
            self._cap_check(n)
            self._batch(k, rnd, n)
        assert self.led is not None
        if a.until_settled:
            while self.led.versions(list(INFLIGHT)) and rnd["extension"] < a.max_extend:
                n = min(a.batch, a.max_extend - rnd["extension"])
                self._cap_check(n)
                rnd["extension"] += n
                self._batch(k, rnd, n)
        rnd["settled"] = not self.led.versions(list(INFLIGHT))
        if a.nights == "inprocess":
            rnd["tick"] = self._live("tick")["status"]  # gateway traffic has stopped: the two gates never run at once

    def _finish_round(self, k: int, rnd: dict[str, Any]) -> None:
        """Snapshot the round's window when it ends: a later retention fold cannot change a published number."""
        assert self.led is not None
        rnd["upto_id"] = self.led.last_ids()["requests"]
        w = self.led.client_window(self.args.client, rnd["after_id"], rnd["upto_id"])
        rnd["ledger"] = window_stats(w["rows"])
        rnd["other_clients"] = w["other_clients"]
        with self.rec_lock:
            answered = sum(1 for r in self.records if r.get("round") == k and r.get("status") == 200)
        rnd["unrecorded"] = max(0, len(w["rows"]) - answered)
        rnd["t1"], rnd["finished"] = self.led.now(), self.clock()
        rnd["duration_s"] = max(0.0, rnd["finished"] - (rnd.get("started") or rnd["finished"]))
        rnd["done"] = True
        self._save_state()
        led = rnd["ledger"]
        share = led["served_by"].get("student", 0) / led["rows"] if led["rows"] else 0.0
        self._event(
            "liverun_round",
            f"liverun {self.state['run_id']} round {k} done: {led['rows']} requests, {100 * share:.0f}% student, "
            f"{led['audits']['n']} audits",
        )
        self._write_results()

    def _live(self, cmd: str) -> dict[str, Any]:
        """`python -m ratchet.live <cmd>` in process through its seams. Its own report is not kept (it names commands
        and paths); the outcome is read from the ledger. A SystemExit after a trained night is a failed BOM sync."""
        out = io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(out):
                live.main([cmd], provider_factory=self.provider_factory, run=self.run_fn, http=self.http)
        except SystemExit:
            return {"status": "sync_failed", "failure": None}
        except Exception as e:  # noqa: BLE001 - the class only
            return {"status": "failed", "failure": describe_failure(e)}
        return {"status": "ok", "failure": None}

    def _night(self, k: int, rnd: dict[str, Any]) -> None:
        assert self.led is not None
        ev0 = self.led.last_ids()["events"]
        before = {v.id for v in self.led.versions()}
        t0, started = self.led.now(), self.clock()
        self.say(f"night after round {k} ...")
        res = self._live("night")
        events = self.led.events_since(ev0)
        kinds = [e["kind"] for e in events]
        outcome = "trained" if "trained" in kinds else "skip" if "skip" in kinds else "none"
        admitted = next((kind for kind in ("canary", "shadow", "rejected") if kind in kinds), None)
        new = [v for v in self.led.versions() if v.id not in before]
        t1 = self.led.now()
        hours, usd = self._compute("training_job", t0, t1)
        status = res["status"]
        rnd["night"] = {
            "status": status,
            "outcome": outcome + (", sync_failed" if status == "sync_failed" else ""),
            "admitted": admitted,
            "failure": res["failure"],
            "events": [{"kind": e["kind"], "detail": e["detail"]} for e in events],
            "versions": [{"id": v.id, "state": v.state} for v in new],
            "training_hours": hours,
            "training_usd": usd,
            "t0": t0,
            "t1": t1,
            "duration_s": max(0.0, self.clock() - started),
        }
        self._save_state()
        self.say(f"night after round {k}: {rnd['night']['outcome']}" + (f" ({res['failure']})" if res["failure"] else ""))
        if status == "failed":
            rnd["night"]["outcome"] = "night_failed"
            self._save_state()
            if self.args.on_night_failure == "stop":
                raise _Stop("night_failed", res["failure"] or "")
        spent = self.spent()["total_usd"]
        if spent > self.args.max_usd:
            raise _Stop("max_usd", f"spent {_usd(spent)} after the night, cap {_usd(self.args.max_usd)}")

    def _replan(self, k: int) -> None:
        """After round k, plan the rest from what was measured (mean teacher cost per teacher-served request, the
        observed student share once a version served, the round's duration); stop when it would pass the cap."""
        with self.rec_lock:
            rows = [r for r in self.records if isinstance(r.get("round"), int) and r["round"] >= 1 and r.get("status") == 200]
        last = [r for r in rows if r["round"] == k]
        measured: dict[str, Any] = {}
        assert self.led is not None
        teacher_rows = [
            r
            for rnd in self.state["rounds"]
            if rnd.get("done")
            for r in self.led.client_window(self.args.client, rnd["after_id"], rnd["upto_id"])["rows"]
            if r.get("served_by") == "teacher"
        ]
        if teacher_rows:
            measured["teacher_call_usd"] = sum(_teacher_part(r) for r in teacher_rows) / len(teacher_rows)
        if any(r.get("served_by") == "student" for r in last):
            measured["share"] = sum(1 for r in last if r.get("served_by") == "student") / len(last)
        rnd = next(r for r in self.state["rounds"] if r["round"] == k)
        if rnd.get("duration_s"):
            measured["round_seconds"] = rnd["duration_s"]
        measured["control_spent"] = self.control.state()["spent_usd"]
        inp = PlanInputs(**{**asdict(self.plan_inputs), "measured": measured, "has_live": self.led.live() is not None})
        spent = self.spent()["total_usd"]
        plan = make_plan(inp, start_round=k + 1, spent=spent)
        plan["after_round"] = k
        self.state.setdefault("replans", []).append({"after_round": k, "total_usd": plan["total_usd"], "measured": measured})
        self._save_state()
        self.say(render_plan(plan))
        if not plan["within"]:
            raise _Stop("plan_over_budget", f"the remaining plan totals {_usd(plan['total_usd'])} with {_usd(spent)} spent")

    # ----- optional arms -----
    def _untuned_arm(self) -> None:
        """The tuned adapter and the untuned hosted Nano on the same golden rows, under the gateway's sampling and
        certificate settings (followups.md item 1). Only rows of tasks with fields: those need no judge call."""
        from .certificate import paired
        from .nightschool import dataset
        from .nightschool.evaluate import LiveEvaluator

        assert self.led is not None
        s = self.s
        arms = self.state.setdefault("arms", {})
        version = self.led.live()
        if not s.student_base:
            arms["untuned"] = {"skipped": "the student already is the hosted Nano (no RATCHET_STUDENT_BASE)"}
            return
        if version is None:
            arms["untuned"] = {"skipped": "no live version to compare"}
            return
        rows = [r for r in dataset.rows(self.led, "golden") if tasklib.get(r["task"]).fields]
        if not rows:
            arms["untuned"] = {"skipped": "no golden rows of tasks with fields"}
            return
        cert = self.cert
        # The adapter is priced per token as the router prices the student (its real cost is the endpoint's GPU time,
        # already counted); the hosted Nano is billed per token on Token Factory.
        tuned_p = _Metered(
            provider_factory_call(
                self.provider_factory, s.student_base, s.student_api_key, s.student_timeout(), s.teacher_retry()
            ),
            s.price("student"),
        )
        untuned_p = _Metered(
            provider_factory_call(self.provider_factory, s.teacher_base, self.arms_key, s.student_timeout(), s.teacher_retry()),
            s.price(s.fallback_student_model),
        )

        def evaluator(p: Provider) -> Any:
            return LiveEvaluator(
                p,
                None,
                samples=s.samples,
                policy=s.gen_policy(),
                calibration=cert.calibration,
                cert_target=cert.target,
                cert_delta=cert.delta,
            )

        # One call per golden row and arm (n = RATCHET_SAMPLES); the hosted Nano's are billed per token.
        self._cap_check(0, len(rows) * self.plan_inputs.hosted_student_call_usd, "untuned arm")
        try:
            tuned = evaluator(tuned_p).evaluate(version.adapter_uri, rows)
            untuned = evaluator(untuned_p).evaluate(s.fallback_student_model, rows)
        except Exception as e:  # noqa: BLE001 - an optional arm: record it (and what it spent), keep the run's results
            spend = untuned_p.state()["spent_usd"]
            arms["untuned"] = {"skipped": f"failed ({describe_failure(e)})", "spend_usd": spend}
            self._save_state()
            return

        def at(ev: Mapping[str, Any], thr: float) -> float | None:
            row = next((f for f in ev.get("frontier") or [] if abs(f["threshold"] - thr) < 1e-12), None)
            return None if row is None else row["coverage"]

        def arm(ev: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "coverage": ev.get("coverage"),
                "precision": ev.get("precision"),
                "threshold": ev.get("threshold"),
                "coverage_at_router_threshold": at(ev, s.confidence_threshold),
                "golden_n": ev.get("golden_n"),
                "certificate": ev.get("certificate"),
                "test_vector": ev.get("test_vector"),  # one letter per held-out row (S, W, E): no text
            }

        vec_t, vec_u = tuned.get("test_vector"), untuned.get("test_vector")
        pair = paired(vec_t, vec_u) if isinstance(vec_t, str) and isinstance(vec_u, str) and len(vec_t) == len(vec_u) else None
        diff = (tuned.get("coverage") or 0.0) - (untuned.get("coverage") or 0.0)
        esc = [
            _teacher_part(r)
            for rnd in self.state["rounds"]
            if rnd.get("done")
            for r in self.led.client_window(self.args.client, rnd["after_id"], rnd["upto_id"])["rows"]
            if r.get("route") == "confidence"
        ]
        per_escalation = sum(esc) / len(esc) if esc else None
        arms["untuned"] = {
            "version": version.id,
            "rows": len(rows),
            "tuned": arm(tuned),
            "untuned": arm(untuned),
            "router_threshold": s.confidence_threshold,
            "coverage_difference": diff,
            "paired": pair,
            "teacher_usd_per_escalation": per_escalation,
            "extra_escalation_usd_per_1000": None if per_escalation is None else diff * 1000 * per_escalation,
            # Token Factory bills the hosted Nano's calls; the adapter's calls ran on the endpoint already counted.
            "spend_usd": untuned_p.state()["spent_usd"],
            "tuned_token_usd": tuned_p.state()["spent_usd"],
            "note": "cost.py's crossover is unchanged; these numbers are its evidence",
        }
        self._save_state()

    def _scout_phase(self) -> None:
        """K example questions through Scout over /v1 under the harness's key, exactly as `python -m ratchet.scout
        --base-url` runs them, with live Tavily search. Its own window after the last round."""
        from .scout.agents import Call, Scout
        from .scout.prompts import EXAMPLE_QUESTIONS
        from .scout.search import TavilySearch

        assert self.led is not None
        a, s = self.args, self.s
        scout = self.state.get("scout") or {"after_id": self.led.last_ids()["requests"], "runs": [], "done": False}
        self.state["scout"] = scout
        search = TavilySearch(s.tavily_api_key, client=self.tavily)
        for j, question in enumerate(EXAMPLE_QUESTIONS[: a.scout_runs]):
            if j < len(scout["runs"]):
                continue
            self._cap_check(PLAN_SCOUT_CALLS)
            run = self._scout_run(j, question, search, Scout, Call)
            scout["runs"].append(run)
            self._save_state()
            self.say(f"scout run {j + 1}/{a.scout_runs}: {'ok' if run['ok'] else run['failure']}")
        scout["upto_id"] = self.led.last_ids()["requests"]
        scout["done"] = True
        self._save_state()

    def _scout_run(self, j: int, question: str, search: Any, scout_cls: Any, call_cls: Any) -> dict[str, Any]:
        """One Scout run: every agent call over /v1 with the harness's key and its own request id."""
        counter = iter(range(1_000_000))
        lock = threading.Lock()

        def call(messages: list[dict[str, Any]], task: str) -> Any:
            with lock:
                n = next(counter)
            crid = f"liverun-{self.state['run_id']}-scout{j}-{n}"
            sent, retried = self._send({"model": "ratchet", "messages": messages}, task, crid)
            rec: dict[str, Any] = {"round": None, "scout": j, "i": n, "crid": crid, "task": task}
            self._fill(rec, sent, retried, tasklib.get(task), tasklib.NO_KEY)
            text = rec.pop("_text", None)
            for k in ("accurate", "fields", "invalid"):
                rec.pop(k, None)
            self._keep([rec])
            if rec["status"] != 200 or text is None:
                raise RuntimeError(f"Scout call answered {rec['status']}")
            return call_cls(
                text,
                rec["served_by"],
                "",
                rec.get("confidence"),
                bool(rec.get("escalated")),
                rec.get("cost_usd") or 0.0,
                rec.get("all_teacher_cost_usd") or 0.0,
                rec.get("latency_ms") or 0.0,
            )

        done: dict[str, Any] = {}
        searches = [0]

        def emit(ev: dict[str, Any]) -> None:
            if ev.get("type") == "step" and ev.get("agent") == "searcher" and "unavailable" not in (ev.get("detail") or {}):
                searches[0] += 1
            if ev.get("type") == "done":
                done.update(ev)

        try:
            scout_cls(call, search, max_results=self.s.scout_max_results).run(question, emit, mode="client")
        except Exception as e:  # noqa: BLE001 - one failed run is recorded; the phase goes on
            return {"question": j, "ok": False, "failure": describe_failure(e), "tavily_credits": searches[0]}
        totals = done.get("totals") or {}
        return {
            "question": j,
            "ok": True,
            "totals": {k: totals.get(k) for k in ("llm_calls", "student_calls", "cost_usd", "baseline_usd", "savings_pct")},
            "tavily_credits": searches[0] * search.credits_per_search,
            "search": done.get("search"),
        }

    # ----- results -----
    def _write_results(self) -> dict[str, Any]:
        assert self.dir is not None and self.led is not None
        spent = self.spent()
        now = self.led.now()
        t0 = float(self.state["t0"])
        rounds = [r for r in self.state["rounds"] if r.get("t0") is not None]
        endpoint_segs = (
            cost.segments(self.led.compute_intervals("student_endpoint", since=t0), now, lo=t0, hi=now)
            if self.s.student_base
            else []
        )
        windows = [(float(r["t0"]), float(r["t1"] if r.get("t1") is not None else now)) for r in rounds]
        per_round = cost.split(endpoint_segs, windows) if windows else []
        try:
            days = max(1, self.led.day_of(now) - self.led.day_of(t0) + 1)
            report = cost.cost_report(self.led, self.s, days=days)
            gateway_cost = {
                "label": "whole gateway",
                "days": days,
                "recommendation": report["recommendation"],
                "explanation": report["explanation"],
                "break_even_student_requests_per_gpu_hour": report["break_even_student_requests_per_gpu_hour"],
                "self_host_beats_hosted_above_requests_per_hour": report["self_host_beats_hosted_above_requests_per_hour"],
                "prices_verified": report["prices"]["verified"],
            }
        except Exception as e:  # noqa: BLE001 - the report is context; the run's own numbers stand without it
            gateway_cost = {"label": "whole gateway", "error": describe_failure(e)}
        previous = _read_json(self.dir / "results.json") or {}
        bill = (previous.get("cost") or {}).get("nebius_bill") or {"usd": None, "note": None, "entered": None}
        records = _sorted_records(self.records)
        formula = self.s.teacher_reasoning_tokens
        round_out = []
        for idx, rnd in enumerate(rounds):
            recs = [r for r in records if r.get("round") == rnd["round"]]
            hours, usd = per_round[idx] if idx < len(per_round) else (0.0, 0.0)
            round_out.append(
                {
                    **{k: v for k, v in rnd.items() if k not in ("ledger",)},
                    "window": {"after_id": rnd["after_id"], "upto_id": rnd.get("upto_id")},
                    "ledger": rnd.get("ledger"),
                    "client": client_block(recs, self.stream_label, formula),
                    "endpoint_hours": hours,
                    "endpoint_usd": usd,
                }
            )
        run_recs = [r for r in records if isinstance(r.get("round"), int) and r["round"] >= 1]
        run_ctl = control_estimate(run_recs, formula)
        teacher_rounds = sum((r.get("ledger") or {}).get("cost", {}).get("teacher_usd", 0.0) for r in rounds)
        student_rounds = (
            0.0 if self.s.student_base else sum((r.get("ledger") or {}).get("cost", {}).get("student_usd", 0.0) for r in rounds)
        )
        b_run = run_ctl["baseline_measured_usd"]
        gpu_reduction = (
            1 - (teacher_rounds + student_rounds + spent["endpoint_usd"] + spent["training_usd"]) / b_run if b_run else None
        )
        cost_block = {
            "window": {"t0": t0, "t1": now},
            "teacher_usd": spent["teacher_usd"],
            "student_token_usd": spent["student_token_usd"],
            "split_unknown": getattr(self, "split_unknown", 0),
            "endpoint_hours": spent["endpoint_hours"],
            "endpoint_usd": spent["endpoint_usd"],
            "training_hours": spent["training_hours"],
            "training_usd": spent["training_usd"],
            "nights": [
                {
                    "after_round": r["round"],
                    "training_hours": r["night"]["training_hours"],
                    "training_usd": r["night"]["training_usd"],
                }
                for r in rounds
                if r.get("night")
            ],
            "control_usd": spent["control_usd"],
            "untuned_usd": spent["untuned_usd"],
            "total_usd": spent["total_usd"],
            "rounds_teacher_usd": teacher_rounds,
            "baseline_measured_run_usd": b_run,
            "gpu_hours_reduction": gpu_reduction,
            "cost_model": "gpu_hours: teacher calls + endpoint GPU-hours + training GPU-hours against the measured baseline",
            "prices_verified": self.policy.prices_verified,
            "cost_report": gateway_cost,
            "nebius_bill": bill,
        }
        scout_recs = [r for r in records if r.get("scout") is not None]
        arms = {
            "control": {**run_ctl, "meter": self.control.state(), "label": "the serving teacher's call, sent straight to it"},
            "untuned": (self.state.get("arms") or {}).get("untuned"),
            "scout": None
            if self.state.get("scout") is None
            else {
                **scout_block(scout_recs),
                "runs": self.state["scout"]["runs"],
                "tavily_credits": sum(r.get("tavily_credits") or 0 for r in self.state["scout"]["runs"]),
                "window": {"after_id": self.state["scout"]["after_id"], "upto_id": self.state["scout"].get("upto_id")},
            },
        }
        results: dict[str, Any] = {
            "generator": GENERATOR,
            "kind": self.kind,
            "mode": "live",
            "run_id": self.state["run_id"],
            "started": self.state["started"],
            "finished": self.state.get("finished"),
            "stopped": self.state.get("stopped"),
            "code": self.state["code"],
            "models": self.state["models"],
            "client": self.args.client,
            "workload": {
                "name": self.args.workload,
                "seed": self.args.seed,
                "label": self.stream_label,
                "drift_round": self.args.drift_round,
            },
            "settings": _settings_block(self.s, self.policy, self.cert, self.release),
            "plan": _read_json(self.dir / "plan.json"),
            "rounds": round_out,
            "arms": arms,
            "cost": cost_block,
            "kpis": self._kpis(round_out),
            "repeat": None,
            "values": {},
            "sources": {},
            "caveats": [],
        }
        results["settings"]["gate_profile"] = {**self.profile, "mode": getattr(self.args, "gate_profile", "as-set")}
        results["settings"]["arms_teacher_key"] = self.arms_key_env  # the variable's name, never its value
        values, sources = client_values(records, formula)
        lv, ls = ledger_values(results)
        values.update(lv)
        sources.update(ls)
        if bill.get("usd") is not None:
            values["live.bill.nebius_usd"] = bill["usd"]
            sources["live.bill.nebius_usd"] = "entered by hand from the Nebius console (report --bill-usd)"
        else:
            sources["live.bill.nebius_usd"] = "not measured: enter the bill with report --bill-usd"
        results["values"] = dict(sorted(values.items()))
        results["sources"] = dict(sorted(sources.items()))
        first = self.state.get("repeat_of")
        if first:
            results["repeat"] = {
                "role": "repeat",
                "other": {k: first.get(k) for k in ("run_id", "kind", "code_digest")},
                "same_code": first.get("code_digest") == self.state["code"]["code_digest"],
                **repeat_values(first.get("values") or {}, results["values"]),
            }
        results["caveats"] = caveats(results)
        _write_json(self.dir / "results.json", results)
        return results

    def _kpis(self, rounds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """SM-1 to SM-5 on the run's windows by the PRD's formulas (ratchet.kpis is not in this tree: its block is
        'absent'). SM-4 counts run versions with a `live` event followed by `rollback` or `alert`; SM-5 only with
        --drift-round."""
        try:
            import importlib

            kpis_mod = importlib.import_module("ratchet.kpis")
        except ImportError:
            kpis_mod = None
        gateway: Any = "absent"
        if kpis_mod is not None and hasattr(kpis_mod, "block"):
            try:
                gateway = {"label": "whole gateway", "block": kpis_mod.block(self.led, self.s)}
            except Exception as e:  # noqa: BLE001
                gateway = {"label": "whole gateway", "error": describe_failure(e)}
        per_round = []
        for r in rounds:
            c = r["client"]
            led = r.get("ledger") or {}
            ctl = c["control"]
            per_round.append(
                {
                    "round": r["round"],
                    "sm1_cost_reduction": ctl["measured_reduction"] if ctl["status"] == "ok" else ctl["recorded_reduction"],
                    "sm1_baseline": "measured" if ctl["status"] == "ok" else "recorded",
                    "sm2_audited_precision": (led.get("audits") or {}).get("precision"),
                    "sm3_student_share": c["student_share"],
                    "escalation_share": c["escalated"] / c["ok"] if c["ok"] else None,
                }
            )
        assert self.led is not None
        events = self.led.events_since(int(self.state.get("events_start", 0)))
        trained = {v["id"] for r in self.state["rounds"] for v in (r.get("night") or {}).get("versions") or []}
        went_live: set[int] = set()
        bad: set[int] = set()
        for e in events:
            m = _VERSION_REF.search(e["detail"] or "")
            if not m:
                continue
            vid = int(m.group(1))
            if vid not in trained:
                continue
            if e["kind"] == "live":
                went_live.add(vid)
            elif e["kind"] in ("rollback", "alert") and vid in went_live:
                bad.add(vid)
        sm5: dict[str, Any] | None = None
        d = self.args.drift_round
        if d is not None:
            shares = {p["round"]: p["escalation_share"] for p in per_round}
            base = shares.get(d - 1)
            recovered = next(
                (k for k in sorted(shares) if k >= d and base is not None and shares[k] is not None and shares[k] <= base + 0.02),
                None,
            )
            sm5 = {
                "drift_round": d,
                "baseline_escalation_share": base,
                "recovered_round": recovered,
                "nights_to_recover": None if recovered is None else recovered - d,
                "rule": "first round from the drift round whose escalation share is within 2 points of the round before it",
            }
        return {
            "gateway": gateway,
            "formulas": "PRD section 8.1 on the run's windows",
            "per_round": per_round,
            "run_versions": sorted(trained),
            "run_versions_live": sorted(went_live),
            "sm4_bad_deploys_reaching_live": len(bad),
            "sm5": sm5,
        }


def state_kind_cmd(kind: str) -> str:
    return "python -m ratchet.liverun run --repeat-of" if kind == "smoke" else "python -m ratchet.liverun smoke --repeat-of"


def provider_factory_call(factory: ProviderFactory, base: str, key: str, timeout: Any, retry: Any) -> Provider:
    """`factory(base_url, api_key, timeout=..., retry=...)`, the calling convention `live.main` uses."""
    return factory(base, key, timeout=timeout, retry=retry)


def ledger_values(results: Mapping[str, Any]) -> tuple[dict[str, float], dict[str, str]]:
    """The values snapshotted from the ledger and the compute intervals during the run (`verify` cannot recompute them:
    the ledger is not committed, because it holds redacted examples)."""
    values: dict[str, float] = {}
    sources: dict[str, str] = {}

    def put(vid: str, value: float | None, source: str, why: str) -> None:
        if value is None or not math.isfinite(value):
            sources[vid] = f"not measured: {why}"
        else:
            values[vid] = float(value)
            sources[vid] = source

    rounds = [r for r in results["rounds"] if (r.get("client") or {}).get("ok")]
    final = rounds[-1] if rounds else None
    led = (final or {}).get("ledger") or {}
    audits = led.get("audits") or {}
    k = final["round"] if final else None
    put(
        "live.final.audited_precision_pct",
        None if audits.get("precision") is None else 100 * audits["precision"],
        f"round {k}: ledger audits of student-served answers that agreed (canary and live pooled)",
        "no audit of a student-served answer in the last round",
    )
    put(
        "live.final.audited_precision_lcb_pct",
        None if audits.get("cp_lower") is None else 100 * audits["cp_lower"],
        f"round {k}: one-sided 95% Clopper-Pearson lower bound on the audited precision",
        "no audit in the last round",
    )
    put("live.final.audits", None if final is None else float(audits.get("n", 0)), f"round {k}: ledger audits", "no round")
    wall = led.get("wall_ms")
    lane_d = "requests.wall_ms is not recorded by this gateway (lane D's OPS-PERF-HOTPATH)"
    by_route = (wall or {}).get("by_route") or {}
    by_served = (wall or {}).get("by_served_by") or {}
    put(
        "live.final.student_p50_wall_ms",
        (by_route.get("student") or {}).get("p50") if wall else None,
        f"round {k}: median wall_ms (the whole cascade on the gateway's clock) of route student",
        lane_d if not wall else "no student-route request",
    )
    put(
        "live.final.student_p95_wall_ms",
        (by_route.get("student") or {}).get("p95") if wall else None,
        f"round {k}: 95th percentile wall_ms of route student",
        lane_d if not wall else "no student-route request",
    )
    put(
        "live.final.teacher_p50_wall_ms",
        (by_served.get("teacher") or {}).get("p50") if wall else None,
        f"round {k}: median wall_ms of teacher-served requests",
        lane_d if not wall else "no teacher-served request",
    )
    c = results["cost"]
    put(
        "live.run.endpoint_hours",
        c["endpoint_hours"] if results["settings"]["student_endpoint"] else None,
        "student_endpoint compute intervals inside the run window",
        "no student endpoint (hosted fallback)",
    )
    put(
        "live.run.gpu_hours_reduction_pct",
        None if c["gpu_hours_reduction"] is None else 100 * c["gpu_hours_reduction"],
        "1 - (teacher calls in the rounds + endpoint GPU-hours + training GPU-hours) / measured all-teacher baseline of "
        "every round; negative below break-even, never clamped",
        "no measured baseline",
    )
    report = c.get("cost_report") or {}
    put(
        "live.run.break_even_per_gpu_hour",
        report.get("break_even_student_requests_per_gpu_hour"),
        "the cost report's break-even: student requests per endpoint GPU-hour (whole gateway)",
        "the cost report has no break-even yet",
    )
    nights = [r["night"] for r in results["rounds"] if r.get("night")]
    trained = sum(1 for n in nights if str(n.get("outcome", "")).startswith("trained"))
    put("live.run.versions_trained", float(trained), "versions trained by the run's nights", "none")
    put(
        "live.run.versions_live",
        float(len((results.get("kpis") or {}).get("run_versions_live") or [])),
        "versions the run's nights trained that the release gate promoted to live during the run (a live event)",
        "none",
    )
    put(
        "live.run.total_usd",
        c["total_usd"],
        "teacher + endpoint + training + control + untuned arm (list prices unless verified)",
        "none",
    )
    un = results["arms"].get("untuned") or {}
    if un.get("tuned"):
        put(
            "live.tuned.coverage_pct",
            None if un["tuned"]["coverage"] is None else 100 * un["tuned"]["coverage"],
            "untuned-Nano arm: the live adapter's held-out coverage",
            "not evaluated",
        )
        put(
            "live.untuned.coverage_pct",
            None if un["untuned"]["coverage"] is None else 100 * un["untuned"]["coverage"],
            "untuned-Nano arm: the hosted Nano's held-out coverage",
            "not evaluated",
        )
    else:
        why = un.get("skipped") or "the untuned arm did not run"
        sources["live.tuned.coverage_pct"] = f"not measured: {why}"
        sources["live.untuned.coverage_pct"] = f"not measured: {why}"
    return values, sources


def caveats(results: Mapping[str, Any]) -> list[str]:
    s = results["settings"]
    rep = results.get("repeat")
    if rep:
        moved = rep.get("max_abs_difference_pct_points")
        size = "n/a" if moved is None else f"{moved:.1f}"
        first = rep["other"]["run_id"] if rep["role"] == "repeat" else results["run_id"]
        again = results["run_id"] if rep["role"] == "repeat" else rep["other"]["run_id"]
        once = (
            f"Two runs of the same plan and seed ({first}, then its repeat {again}): percentages moved by up to {size} "
            "points between them (the repeat table); two runs show how much a number moves, not a confidence interval."
        )
        if not rep.get("same_code", True):
            once += " The two runs ran different code (their code digests differ)."
    else:
        once = "A single run: no repetition, so no between-run variance is reported."
    out = [
        once,
        f"Workload: {results['workload']['label']}. Template labels are the template author's, not independent ground "
        "truth; they are independent of the teacher only in that the teacher never wrote them.",
        "The hard templates are deliberately ambiguous: read their accuracy apart from the easy ones.",
        f"Latency was measured at concurrency {results['plan']['inputs']['concurrency'] if results.get('plan') else 'n/a'} "
        "from the harness host; client times include the gateway and the network.",
    ]
    cached = (results["arms"].get("control") or {}).get("cached_tokens", 0)
    out.append(
        "Whether Token Factory caches prompts is unverified; the control arm randomises call order and recorded "
        f"{cached} cached input tokens."
    )
    if not s["prices_verified"]:
        out.append("Every dollar figure uses list prices, unverified until the day-1 checks pass (RATCHET_PRICES_VERIFIED=0).")
    out.append(
        "The measured all-teacher baseline is a ratio estimate from the control sample with a normal-approximation interval."
    )
    if results.get("stopped"):
        out.append(f"The run stopped early: {results['stopped']['reason']}.")
    unrec = sum(int(r.get("unrecorded") or 0) for r in results["rounds"])
    if unrec:
        out.append(f"{unrec} requests reached the gateway but were not recorded by the harness; their spend is counted.")
    if results["cost"].get("split_unknown"):
        out.append("Some rows have no student/teacher cost split; their whole cost counts as teacher spend.")
    if results["kind"] == "smoke":
        out.append("A smoke run: it never backs a number in the docs.")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_records(path: Path) -> list[dict[str, Any]]:
    """The per-request records (a gzip file of appended members, one per batch). A member cut short by a crash ends the
    read; the records before it are kept, the rest are counted from the ledger as unrecorded. Duplicates by request id
    are dropped."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not path.exists():
        return out
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    break
                if isinstance(r, dict) and isinstance(r.get("crid"), str) and r["crid"] not in seen:
                    seen.add(r["crid"])
                    out.append(r)
    except (EOFError, OSError, gzip.BadGzipFile):
        pass
    return out


def render_markdown(results: Mapping[str, Any]) -> str:
    """LIVE_RESULTS.md: every number with its claim anchor, the rounds, the arms, the cost next to the hand-entered bill
    and the break-even line, the placeholder map and the caveats."""
    v = results["values"]
    s = results["settings"]
    code = results["code"]
    prices = "verified prices" if s["prices_verified"] else "list prices, unverified"

    MS = "{:.0f}"

    def claim(vid: str, fmt: str = "{:.1f}", unit: str = "") -> str:
        if vid not in v:
            return f"not measured ({results['sources'].get(vid, 'no source')}) <!-- claim:{vid} -->"
        return f"**{fmt.format(v[vid])}{unit}** <!-- claim:{vid} -->"

    teacher = (results["models"].get("teacher") or {}).get("listed_nvidia") or []
    student = (results["models"].get("student") or {}).get("listed") or []
    # Only a run that had a student endpoint (RATCHET_STUDENT_BASE) used one; the text must not say so otherwise.
    has_endpoint = bool(s.get("student_endpoint"))
    where = " and a Nebius Serverless Endpoint" if has_endpoint else " (no student endpoint: no GPU endpoint was used)"
    student_label = "student endpoint" if has_endpoint else "student side"
    lines = [
        "# Live run results",
        "",
        f"Run `{results['run_id']}` ({results['kind']}), {_utc(results['started'])} to {_utc(results['finished'])}. "
        f"Code `{code.get('git_sha') or 'unknown'}`, digest `{code['code_digest']}`, ratchet {code['version']}.",
        "",
        f"Live run on Nebius Token Factory{where}; synthetic workload ({results['workload']['label']}, "
        f"seed {results['workload']['seed']}). Prices: {prices}. Generated by `python -m ratchet.liverun`; check the "
        "client-side numbers with `python -m ratchet.liverun verify`.",
        "",
        f"Models as listed: teacher side {', '.join(f'`{m}`' for m in teacher) or 'not listed'}; {student_label} "
        f"{', '.join(f'`{m}`' for m in student) or 'not listed'}.",
        "",
    ]
    if results.get("stopped"):
        lines += [f"Stopped early: `{results['stopped']['reason']}` ({results['stopped'].get('detail') or ''}).", ""]
    lines += [
        "## Summary",
        "",
        f"- Cost reduction, last round, per token, against the measured all-Ultra baseline: "
        f"{claim('live.final.cost_reduction_measured_pct', unit='%')}; against the recorded (formula) baseline "
        f"{claim('live.final.cost_reduction_recorded_pct', unit='%')}.",
        f"- Whole run, per token: measured {claim('live.run.cost_reduction_measured_pct', unit='%')}, recorded "
        f"{claim('live.run.cost_reduction_recorded_pct', unit='%')}.",
        f"- Audited precision, last round: {claim('live.final.audited_precision_pct', unit='%')} (95% lower bound "
        f"{claim('live.final.audited_precision_lcb_pct', unit='%')}, {claim('live.final.audits', '{:.0f}')} audits).",
        "- Median client-observed latency, last round: student-served "
        f"{claim('live.final.student_p50_client_ms', MS, ' ms')}, Ultra alone "
        f"{claim('live.final.ultra_p50_client_ms', MS, ' ms')}.",
        f"- Server-side wall time, last round: student p50 {claim('live.final.student_p50_wall_ms', MS, ' ms')}, p95 "
        f"{claim('live.final.student_p95_wall_ms', MS, ' ms')}; teacher p50 "
        f"{claim('live.final.teacher_p50_wall_ms', MS, ' ms')}.",
        f"- Student share, last round: {claim('live.final.student_share_pct', unit='%')}; agreement with Ultra on paired "
        f"student-served requests: {claim('live.final.paired_agreement_pct', unit='%')}.",
        f"- Endpoint GPU-hours: {claim('live.run.endpoint_hours', '{:.2f}', ' h')}; savings by GPU-hours paid: "
        f"{claim('live.run.gpu_hours_reduction_pct', unit='%')}.",
        f"- Versions trained {claim('live.run.versions_trained', '{:.0f}')}, promoted to live "
        f"{claim('live.run.versions_live', '{:.0f}')}; requests answered {claim('live.run.requests', '{:,.0f}')}; "
        f"total spend {claim('live.run.total_usd', '${:,.2f}')}.",
        f"- Template-label accuracy ({results['workload']['label']}): cascade easy "
        f"{claim('live.accuracy.cascade_easy_pct', unit='%')}, hard {claim('live.accuracy.cascade_hard_pct', unit='%')}; "
        f"Ultra alone easy {claim('live.accuracy.ultra_easy_pct', unit='%')}, hard "
        f"{claim('live.accuracy.ultra_hard_pct', unit='%')}.",
        "",
        "## Rounds",
        "",
        "| Round | Answered | Student share | Audits | Audited precision (95% lower bound) | Cost | Recorded all-teacher "
        "| Measured all-teacher | Night |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results["rounds"]:
        c = r["client"]
        a = (r.get("ledger") or {}).get("audits") or {}
        ctl = c["control"]
        prec = "n/a" if a.get("precision") is None else f"{100 * a['precision']:.1f}% ({100 * a['cp_lower']:.1f}%)"
        night = (r.get("night") or {}).get("outcome") or ""
        share = "n/a" if c["student_share"] is None else f"{100 * c['student_share']:.1f}%"
        lines.append(
            f"| {r['round']} | {c['ok']:,} | {share} | {a.get('n', 0)} | {prec} | {_usd(ctl['cascade_usd'])} | "
            f"{_usd(ctl['baseline_recorded_usd'])} | {_usd(ctl['baseline_measured_usd'])} | {night} |"
        )
    ctl = results["arms"]["control"]
    rt = ctl["reasoning_tokens"]
    lines += [
        "",
        "## Arms",
        "",
        f"Control (the serving teacher's call, sent straight to it, never stored): {ctl['ok']} calls, ratio on student-served "
        f"pairs {_num(ctl['ratio_student']['ratio'], 3)} (n={ctl['ratio_student']['n']}, {ctl['status']}), on teacher-served "
        f"pairs {_num(ctl['ratio_teacher']['ratio'], 3)} (a check, about 1). Reasoning tokens per call: mean "
        f"{_num(rt['mean'], 0)}, p50 {_num(rt['p50'], 0)}, p95 {_num(rt['p95'], 0)}; the formula assumes {rt['formula']} "
        "(audits are temperature-0 check-role calls and the control arm uses the serve role, so the two need not agree).",
        "",
    ]
    un = results["arms"].get("untuned")
    if un and un.get("tuned"):
        lines.append(
            f"Untuned Nano: held-out coverage {100 * (un['untuned']['coverage'] or 0):.1f}% against the adapter's "
            f"{100 * (un['tuned']['coverage'] or 0):.1f}% on {un['rows']} golden rows; extra escalation cost per 1,000 "
            f"requests {_usd(un['extra_escalation_usd_per_1000'])}."
        )
        lines.append("")
    sc = results["arms"].get("scout")
    if sc:
        lines.append(
            f"Scout: {claim('live.scout.student_calls', '{:.0f}')} of {claim('live.scout.llm_calls', '{:.0f}')} calls answered "
            f"by the student, {claim('live.scout.savings_pct', unit='%')} saved against the recorded all-Ultra baseline; "
            f"{sc['tavily_credits']} Tavily credits."
        )
        lines.append("")
    rp = results.get("repeat")
    if rp:
        other = rp["other"]["run_id"]
        who = f"the repeat of run `{other}`" if rp["role"] == "repeat" else f"repeated as run `{other}`"
        code_note = "the same code" if rp.get("same_code", True) else "different code (the code digests differ)"
        lines += [
            "## Repeat run",
            "",
            f"This run is {who}: the same plan, workload and seed, on {code_note}. Two runs show how much each number "
            "moves between runs; they are not a confidence interval.",
            "",
            "| Value id | First run | Repeat | Difference |",
            "|---|---|---|---|",
        ]
        for vid, row in rp["values"].items():
            lines.append(f"| `{vid}` | {_num(row['first'], 2)} | {_num(row['repeat'], 2)} | {row['difference']:+,.2f} |")
        for name, ids in (("first run", rp.get("only_first")), ("repeat", rp.get("only_repeat"))):
            if ids:
                lines.append(f"\nMeasured in the {name} only: " + ", ".join(f"`{vid}`" for vid in ids) + ".")
        lines.append("")
    c = results["cost"]
    bill = c["nebius_bill"]
    report = c.get("cost_report") or {}
    lines += [
        "## Cost",
        "",
        "| Item | USD |",
        "|---|---|",
        f"| Teacher calls | {_usd(c['teacher_usd'])} |",
        f"| Student endpoint ({c['endpoint_hours']:.2f} GPU-hours) | {_usd(c['endpoint_usd'])} |",
        f"| Training ({c['training_hours']:.2f} GPU-hours) | {_usd(c['training_usd'])} |",
        f"| Control arm | {_usd(c['control_usd'])} |",
        f"| Untuned arm | {_usd(c['untuned_usd'])} |",
        f"| **Ratchet's own total** | **{_usd(c['total_usd'])}** |",
        "| Nebius bill (entered by hand, failed jobs included) | "
        + (
            f"{claim('live.bill.nebius_usd', '${:,.2f}')} ({bill.get('note') or 'no note'})"
            if bill.get("usd") is not None
            else "not entered"
        )
        + " |",
        "",
        f"Break-even (whole gateway): {claim('live.run.break_even_per_gpu_hour', '{:,.0f}')} student requests per endpoint "
        "GPU-hour. "
        f"Recommendation: `{report.get('recommendation', 'n/a')}`. {report.get('explanation', '')}",
        "",
        f"Prices: {prices}.",
        "",
        "## Placeholder map",
        "",
        "| Where | Placeholder | Value id | Value |",
        "|---|---|---|---|",
    ]
    for where, placeholder, vid in PLACEHOLDERS:
        lines.append(f"| {where} | {placeholder} | `{vid}` | {_num(v.get(vid), 2) if vid in v else 'not measured'} |")
    lines += ["", "## Caveats", ""] + [f"- {x}" for x in results["caveats"]] + [""]
    return "\n".join(lines)


def render_markdown_to(run_dir: Path | None, results: Mapping[str, Any]) -> None:
    if run_dir is not None:
        (run_dir / "LIVE_RESULTS.md").write_text(render_markdown(results), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------
# report and verify
# ---------------------------------------------------------------------------------------------------------------------
def cmd_report(args: argparse.Namespace, clock: Callable[[], float], out: TextIO) -> int:
    run_dir = Path(args.run_dir)
    results = _read_json(run_dir / "results.json")
    if not isinstance(results, dict) or results.get("generator") != GENERATOR:
        print(f"liverun: no liverun results.json in {run_dir}", file=out)
        return EXIT_INPUT
    if args.bill_note is not None and args.bill_usd is None:
        print("liverun: --bill-note needs --bill-usd", file=out)
        return EXIT_INPUT
    if args.bill_usd is not None:
        if not (math.isfinite(args.bill_usd) and args.bill_usd >= 0):
            print(f"liverun: --bill-usd must be a finite amount, 0 or more, not {args.bill_usd}", file=out)
            return EXIT_INPUT
        note = args.bill_note
        if note is not None and len(note) > NOTE_MAX:
            print(f"liverun: --bill-note must be at most {NOTE_MAX} characters", file=out)
            return EXIT_INPUT
        from .redact import redact

        results["cost"]["nebius_bill"] = {"usd": args.bill_usd, "note": redact(note) if note else None, "entered": clock()}
        results["values"]["live.bill.nebius_usd"] = args.bill_usd
        results["values"] = dict(sorted(results["values"].items()))
        results["sources"]["live.bill.nebius_usd"] = "entered by hand from the Nebius console (report --bill-usd)"
        results["caveats"] = caveats(results)
        _write_json(run_dir / "results.json", results)
    if args.repeat is not None:
        rdir = Path(args.repeat)
        again = _read_json(rdir / "results.json")
        if not isinstance(again, dict) or again.get("generator") != GENERATOR or not isinstance(again.get("values"), dict):
            print(f"liverun: no liverun results.json in {rdir}", file=out)
            return EXIT_INPUT
        rr = again.get("repeat") if isinstance(again.get("repeat"), dict) else {}
        if rr.get("role") != "repeat" or (rr.get("other") or {}).get("run_id") != results.get("run_id"):
            print(
                f"liverun: {rdir} is not a repeat of run {results.get('run_id')} (start one with --repeat-of {run_dir})", file=out
            )
            return EXIT_INPUT
        results["repeat"] = {
            "role": "original",
            "other": {
                "run_id": again.get("run_id"),
                "kind": again.get("kind"),
                "code_digest": (again.get("code") or {}).get("code_digest"),
            },
            "same_code": (again.get("code") or {}).get("code_digest") == (results.get("code") or {}).get("code_digest"),
            "dir": str(rdir),
            **repeat_values(results["values"], again["values"]),
        }
        results["caveats"] = caveats(results)
        _write_json(run_dir / "results.json", results)
        moved = results["repeat"]["max_abs_difference_pct_points"]
        print(f"repeat {again.get('run_id')}: percentages moved by up to {_num(moved, 1)} points", file=out)
    render_markdown_to(run_dir, results)
    print(f"wrote {run_dir / 'LIVE_RESULTS.md'}", file=out)
    if args.publish:
        if results.get("kind") != "run":
            print("liverun: smoke runs never back a doc number; publish a run", file=out)
            return EXIT_INPUT
        if results.get("stopped") and not args.allow_partial:
            print(
                f"liverun: the run stopped early ({results['stopped']['reason']}); pass --allow-partial to publish it", file=out
            )
            return EXIT_INPUT
        dest = Path("evidence") / "live"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_dir / "results.json", dest / "results.json")
        if (run_dir / "requests.ndjson.gz").exists():
            shutil.copyfile(run_dir / "requests.ndjson.gz", dest / "requests.ndjson.gz")
        Path("docs").mkdir(exist_ok=True)
        shutil.copyfile(run_dir / "LIVE_RESULTS.md", Path("docs") / "LIVE_RESULTS.md")
        print(f"published {dest / 'results.json'} and docs/LIVE_RESULTS.md", file=out)
        rp = results.get("repeat") or {}
        rdir = Path(rp["dir"]) if rp.get("role") == "original" and isinstance(rp.get("dir"), str) else None
        if rdir is not None and (rdir / "results.json").is_file():
            (dest / "repeat").mkdir(exist_ok=True)
            for name in ("results.json", "requests.ndjson.gz", "LIVE_RESULTS.md"):
                if (rdir / name).exists():
                    shutil.copyfile(rdir / name, dest / "repeat" / name)
            print(f"published the repeat run to {dest / 'repeat'}", file=out)
    return EXIT_OK


def _close(a: Any, b: Any) -> bool:
    """Equal JSON values, numbers within a relative 1e-9."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def cmd_verify(args: argparse.Namespace, out: TextIO) -> int:
    """Recompute every client-side value from requests.ndjson.gz and compare with results.json: exit 0 when they match,
    1 on a mismatch, 2 on bad input. Ledger-side values are snapshots; say which."""
    d = Path(args.dir)
    path = d if d.is_file() else d / "results.json"
    results = _read_json(path)
    if not isinstance(results, dict) or results.get("generator") != GENERATOR:
        print(f"liverun: no liverun results.json at {path}", file=out)
        return EXIT_INPUT
    records = read_records(path.parent / "requests.ndjson.gz")
    if not records:
        print(f"liverun: no requests.ndjson.gz next to {path}", file=out)
        return EXIT_INPUT
    try:
        formula = int(results["settings"]["teacher_reasoning_tokens"])
        label = results["workload"]["label"]
        values = results["values"]
        sources = results["sources"]
        rounds = results["rounds"]
    except (KeyError, TypeError, ValueError):
        print("liverun: results.json is missing keys", file=out)
        return EXIT_INPUT
    problems = []
    mine, _ = client_values(records, formula)
    for vid in CLIENT_VALUE_IDS:
        if (vid in values) != (vid in mine) or (vid in values and not _close(values[vid], mine[vid])):
            problems.append(f"{vid}: results.json says {values.get(vid)!r}, the records give {mine.get(vid)!r}")
    recs = _sorted_records(records)
    for r in rounds:
        want = client_block([x for x in recs if x.get("round") == r.get("round")], label, formula)
        if not _close(r.get("client"), want):
            problems.append(f"round {r.get('round')}: the client-side block differs from the records")
    for vid in values:
        if vid not in sources:
            problems.append(f"{vid}: no sources entry")
    rp = results.get("repeat")
    if isinstance(rp, dict) and rp.get("role") in ("original", "repeat"):
        side = "first" if rp["role"] == "original" else "repeat"
        for vid, row in (rp.get("values") or {}).items():
            if vid in values and not _close(row.get(side), values[vid]):
                problems.append(f"repeat table: {vid}'s {side} value is not this run's value")
    snapshot = sorted(vid for vid in values if vid not in CLIENT_VALUE_IDS)
    for line in problems:
        print(f"mismatch: {line}", file=out)
    print(
        f"recomputed {sum(1 for vid in CLIENT_VALUE_IDS if vid in values)} client-side values and {len(rounds)} rounds from "
        f"{len(records)} records",
        file=out,
    )
    if snapshot:
        print("not recomputable here (ledger snapshots or entered by hand): " + ", ".join(snapshot), file=out)
    return 1 if problems else EXIT_OK


def plan_inputs(
    args: argparse.Namespace,
    s: Settings,
    policy: Any,
    cert: Any,
    canary: float | None,
    release: ReleasePolicy,
    has_live: bool,
    max_usd: float,
) -> PlanInputs:
    """The plan's inputs from the options and the gateway's settings. The prompt size is the mean `approx_tokens` of
    PLAN_SAMPLES requests from the workload's own planning stream (never the run's), the teacher's output is
    RATCHET_TEACHER_REASONING_TOKENS + PLAN_OUTPUT_EXTRA tokens, at the configured (list, unless verified) prices."""
    sampler = make_workload(args.workload, args.seed + PLAN_SEED_OFFSET, None)
    toks = [approx_tokens(str(sampler.sample(1).messages())) for _ in range(PLAN_SAMPLES)]
    prompt = round(sum(toks) / len(toks))
    out_t = s.teacher_reasoning_tokens + PLAN_OUTPUT_EXTRA
    return PlanInputs(
        rounds=args.rounds,
        requests=args.requests,
        share=args.plan_share,
        train_hours=args.plan_train_hours,
        night_minutes=args.plan_night_minutes,
        control_share=args.control_share,
        control_max_usd=args.control_max_usd,
        concurrency=args.concurrency,
        nights=args.nights == "inprocess",
        scout_runs=args.scout_runs,
        audit_rate=s.audit_rate,
        canary_rate=canary,
        min_audits=release.min_audits,
        min_shadow=release.min_shadow,
        golden_fraction=s.golden_fraction,
        cert_target=cert.target,
        cert_delta=cert.delta,
        prompt_tokens=prompt,
        teacher_out_tokens=out_t,
        teacher_call_usd=s.price(s.teacher_model).cost(prompt, out_t),
        student_endpoint=bool(s.student_base),
        endpoint_usd_per_hour=policy.student.usd_per_hour,
        train_usd_per_hour=policy.train.usd_per_hour,
        hosted_student_call_usd=s.price(s.fallback_student_model).cost(prompt, s.samples * PLAN_OUTPUT_EXTRA),
        has_live=has_live,
        prices_verified=policy.prices_verified,
        max_usd=max_usd,
        profile=profile_check(release, canary),
    )


def cmd_plan(args: argparse.Namespace, out: TextIO) -> int:
    """The plan alone: no gateway, no key, no paid call. Reads the settings and, when the ledger exists, whether a
    version is live (round 1 is then not a cold start)."""
    try:
        s = Settings(mode="live")
        s.check_reserved()
        policy, cert = s.cost_policy(), s.cert_policy()
        canary = s.checked_canary_audit_rate()
        release = ReleasePolicy.from_env()
    except ValueError as e:
        print(f"liverun: {e}", file=out)
        return EXIT_INPUT
    has_live = False
    if os.path.exists(s.db_path):
        led = Ledger(s.db_path, s.golden_fraction, auto_day=True)
        try:
            has_live = led.live() is not None
        finally:
            led.db.close()
    plan = make_plan(plan_inputs(args, s, policy, cert, canary, release, has_live, args.max_usd))
    print(render_plan(plan), file=out)
    return EXIT_OK if plan["within"] else EXIT_OVER_PLAN


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    provider_factory: ProviderFactory = OpenAICompatProvider,
    run: RunFn = subprocess.run,
    http: httpx.Client | None = None,
    tavily: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
    timer: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    out: TextIO | None = None,
) -> int:
    """The exit code: 0 done, 1 stopped early with a valid partial results file, 2 bad input or a refused preflight, 3
    the plan exceeds --max-usd. The seams (`client` for the gateway, `provider_factory` for the arms and `live.main`,
    `run` and `http` for `live.main`, `tavily` for Scout's search, `clock`, `timer`, `sleep`, `out`) are default
    arguments; production code never passes them."""
    out = out if out is not None else sys.stdout
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as e:
        return EXIT_INPUT if e.code else EXIT_OK
    try:
        if args.cmd == "report":
            return cmd_report(args, clock, out)
        if args.cmd == "verify":
            return cmd_verify(args, out)
        args = _resolve(args)
        if args.cmd == "plan":
            return cmd_plan(args, out)
    except InputError as e:
        print(f"liverun: {e}", file=out)
        return EXIT_INPUT
    harness = Harness(
        args,
        client=client,
        provider_factory=provider_factory,
        run=run,
        http=http,
        tavily=tavily,
        clock=clock,
        timer=timer,
        sleep=sleep,
        out=out,
    )
    try:
        return harness.execute()
    finally:
        harness.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
