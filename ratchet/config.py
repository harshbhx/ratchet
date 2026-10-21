"""Runtime settings, read from environment variables.

Model IDs follow the Nebius Token Factory catalog as of Oct 2026. Confirm them on
day 1 with `GET https://api.tokenfactory.nebius.com/v1/models?verbose=true`.
"""

from __future__ import annotations

import argparse
import contextvars
import difflib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

import httpx

from . import licenses, reasoning, watch
from .auth import MAX_RPM, MAX_USD, KeyLimits, KeyRing, Token, parse_key_limits
from .certificate import CALIBRATIONS
from .kpis import check_window_days
from .openai_compat import AUTO_TASK_MODES, DEFAULT_MAX_N, MODEL_POLICIES, Limits, ModelRouting, parse_aliases
from .providers import (
    DEFAULT_STUDENT_MAX_TOKENS,
    DEFAULT_STUDENT_PARAMS,
    DEFAULT_TEACHER_MAX_TOKENS,
    DEFAULT_TEACHER_PARAMS,
    GenPolicy,
    parse_param_allowlist,
)
from .redact import STYLES as REDACT_STYLES
from .resilience import MAX_OPEN_S, BreakerPolicy, RetryPolicy, timeouts
from .tasks import MAX_TASKS
from .tasks import valid_name as valid_task_name

# While True, every environment read in this module returns its default (`defaults()`).
_DEFAULTS_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar("ratchet_config_defaults_only", default=False)


def _env(name: str, default: str) -> str:
    if _DEFAULTS_ONLY.get():
        return default
    return os.environ.get(name, default)


# ---------- configuration problems (FR-12.30, ADR 0070) ----------
ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class ConfigIssue:
    """One problem with the configuration: `level` ERROR (the gateway does not start) or WARNING (it starts;
    RATCHET_STRICT_CONFIG=1 stops it too), the variable `var` it is about, and a `message` that names the variable and
    never holds a key's or token's value. `license` marks a refused teacher (INV-1), so the ConfigError raised for it is
    also a LicenseError."""

    level: str
    var: str
    message: str
    license: bool = False

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "var": self.var, "message": self.message}


def _render(issues: Sequence[ConfigIssue]) -> str:
    def line(i: ConfigIssue) -> str:
        return i.message if i.level == ERROR else f"{i.message} (a warning; RATCHET_STRICT_CONFIG=1 makes it fatal)"

    if len(issues) == 1:
        return line(issues[0])
    return f"{len(issues)} configuration problems stop startup:\n" + "\n".join(f"  - {line(i)}" for i in issues)


class ConfigError(ValueError):
    """Settings Ratchet cannot start with. `issues` lists every problem found, and the message lists them, each naming
    its variable; `var`, `value` and `expected` describe the first. A ValueError, so code that caught the old errors
    still catches it. Build one from a single bad value, `ConfigError(var, value, expected)`, or from a list,
    `ConfigError.of(issues)`."""

    def __init__(self, var: str, value: object = None, expected: str = "", *, issues: Sequence[ConfigIssue] = ()) -> None:
        if not issues:
            issues = (ConfigIssue(ERROR, var, f"{var} must be {expected}, not {value!r}"),)
        self.issues: tuple[ConfigIssue, ...] = tuple(issues)
        self.var, self.value, self.expected = var, value, expected
        super().__init__(_render(self.issues))

    @classmethod
    def of(cls, issues: Iterable[ConfigIssue]) -> ConfigError:
        """One error for several problems; a ConfigLicenseError (also a LicenseError) when a teacher was refused."""
        found = tuple(issues)
        if not found:
            raise ValueError("ConfigError.of needs at least one issue")
        kind = ConfigLicenseError if any(i.license for i in found) else ConfigError
        return kind(found[0].var, issues=found)


class ConfigLicenseError(ConfigError, licenses.LicenseError):
    """A ConfigError whose problems include a refused teacher (INV-1): `except LicenseError` still catches it."""


# While `load()` reads the environment, a variable that does not parse is collected here instead of raised (its setting
# keeps its default meanwhile), so one startup check lists every bad variable at once.
_COLLECT: contextvars.ContextVar[list[ConfigIssue] | None] = contextvars.ContextVar("ratchet_config_collect", default=None)


def _fail(var: str, raw: object, expected: str, fallback: Any) -> Any:
    """Raise ConfigError for a variable that does not parse, or collect it during `load()` and return `fallback`."""
    error = ConfigError(var, raw, expected)
    sink = _COLLECT.get()
    if sink is None:
        raise error
    sink.extend(error.issues)
    return fallback


def default_fixtures_dir() -> str:
    """RATCHET_FIXTURES_DIR (default `fixtures`, relative to the working directory): `Settings.fixtures_dir`, and what
    `taskspec` reads when a caller parses a sql task's spec without naming a directory."""
    return _env("RATCHET_FIXTURES_DIR", "fixtures")


def _float(name: str, default: str) -> float:
    """A number from the environment; ConfigError names the variable, so a typo stops startup with a message that says
    which setting is wrong."""
    raw = _env(name, default)
    try:
        return float(raw)
    except ValueError:
        return _fail(name, raw, "a number", float(default))


def _int(name: str, default: str) -> int:
    """A whole number from the environment; ConfigError names the variable."""
    raw = _env(name, default)
    try:
        return int(raw)
    except ValueError:
        return _fail(name, raw, "a whole number", int(default))


TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
FALSE_WORDS = frozenset({"0", "false", "no", "off"})


def _bool(name: str, default: str) -> bool:
    """A switch from the environment: 1, true, yes or on, or 0, false, no or off, in any case (empty means the default).
    Anything else raises ValueError naming the variable: for a privacy switch, a typo must stop startup instead of
    quietly meaning the other thing."""
    raw = _env(name, default)
    word = raw.strip().lower() or default
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return _fail(name, raw, "1 or 0 (true/false, yes/no, on/off)", default.strip().lower() in TRUE_WORDS)


def _float_or_none(name: str) -> float | None:
    """A number from the environment, or None when the variable is unset or empty; ConfigError names the variable."""
    raw = _env(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return _fail(name, raw, "a number", None)


def _int_or_none(name: str) -> int | None:
    """A whole number from the environment, or None when the variable is unset or empty (the setting's default, which
    may depend on the mode); ValueError names the variable."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {raw!r}") from None


def _int_or_none(name: str) -> int | None:
    """A whole number from the environment, or None when the variable is unset or empty; ConfigError names the variable."""
    raw = _env(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return _fail(name, raw, "a whole number", None)


def _switch(name: str) -> bool | None:
    """A 0/1 switch from the environment, or None when the variable is unset or empty (the setting's default, which may
    depend on the mode); `_bool` is the word switch with a default (1, true, yes, on). Anything else raises ConfigError
    naming the variable: a typo in a security switch must stop startup instead of quietly picking a side."""
    raw = _env(name, "").strip()
    if not raw:
        return None
    if raw not in ("0", "1"):
        return _fail(name, raw, "0 or 1", None)
    return raw == "1"


# The longest interval RATCHET_TICK_INTERVAL_S and RATCHET_STATS_TTL_S take: an hour. Longer would leave rollbacks to the
# cron tick alone, or the dashboard showing hour-old numbers, which is never what a typo meant.
MAX_INTERVAL_S = 3600.0
# RATCHET_DB_SYNC values (`ledger.SYNC_MODES` repeats them; config imports nothing from the ledger).
SYNC_MODES = ("NORMAL", "FULL")
# RATCHET_LOG_FORMAT and RATCHET_LOG_LEVEL values (`obs.LOG_FORMATS` and `obs.LOG_LEVELS` repeat them).
LOG_FORMATS = ("json", "text")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _seconds(env: str, value: object, default: float) -> float:
    """An optional interval setting: `default` when unset (None), else a finite number of seconds from 0 to
    MAX_INTERVAL_S; ValueError names the variable otherwise."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_INTERVAL_S:
        raise ValueError(f"{env} must be a number of seconds from 0 to {MAX_INTERVAL_S:.0f}, not {value!r}")
    return float(value)


# Settings declared for the evidence features (FR-16.x; ADR 0024) before the items that read them are built:
# field name -> (variable, its default as the environment spells it, the feature that will read it). Their defaults keep
# today's behaviour. Until a feature lands, `Settings.check_reserved()` stops startup on any other value, so nobody
# relies on a setting that does nothing yet; the item that builds the feature removes its entries here (and adds its own
# checks). docs/CONFIGURATION.md lists them under "Reserved settings".
RESERVED_SETTINGS: dict[str, tuple[str, str, str]] = {
    "batch_api": ("RATCHET_BATCH_API", "operations", "clean-room backfill (FR-16.8)"),
    "batch_discount": ("RATCHET_BATCH_DISCOUNT", "0.5", "clean-room backfill (FR-16.8)"),
    "backfill_max_tokens": ("RATCHET_BACKFILL_MAX_TOKENS", "2048", "clean-room backfill (FR-16.8)"),
    "gateway_lease": ("RATCHET_GATEWAY_LEASE", "warn", "one gateway process per live ledger (NFR-60)"),
}
# Variables the live-run harness (ratchet/liverun.py) reads outside `Settings`: its gateway key, the code's commit in an
# image without .git, and an optional Token Factory key for its own arms (docs/CONFIGURATION.md#live-run-harness).
TOOL_ENV: tuple[str, ...] = (
    "RATCHET_LIVERUN_KEY",
    "RATCHET_GIT_SHA",
    "NEBIUS_API_KEY_LIVERUN",
    "RATCHET_DEMO_CHECK_KEY",  # scripts/demo_check.py (FR-11.34)
    "RATCHET_WATCH_KEY",
)
# Every environment variable the package reads, but ReleasePolicy's RATCHET_<FIELD> (`known_env()` adds those). The
# startup check calls any other RATCHET_* variable unknown (`Settings.validate`), so a variable a change starts reading
# goes here: `python scripts/reqtrace.py --check` fails until it does, and until docs/CONFIGURATION.md documents it.
KNOWN_ENV: tuple[str, ...] = tuple(
    sorted(
        {
            "NEBIUS_API_KEY",
            # The demo watchdog's host-side settings (ratchet/watch.py, FR-11.32), in the .env the gateway also reads.
            "RATCHET_ALERT_KINDS",
            "RATCHET_ALERT_WEBHOOK_SECRET",
            "RATCHET_ALERT_WEBHOOK_URL",
            "RATCHET_INSTANCE_NAME",
            "RATCHET_WATCH",
            "RATCHET_WATCH_CLIENT",
            "RATCHET_WATCH_CONNECT",
            "RATCHET_WATCH_FAILURES",
            "RATCHET_WATCH_FLAG_MAX_AGE_S",
            "RATCHET_WATCH_HEARTBEAT_URL",
            "RATCHET_WATCH_MAX_RESTARTS_PER_HOUR",
            "RATCHET_WATCH_PROBE_TIMEOUT_S",
            "RATCHET_WATCH_REALERT_S",
            "RATCHET_WATCH_RESTART_COOLDOWN_S",
            "RATCHET_WATCH_START_GRACE_S",
            "RATCHET_WATCH_STATE_DIR",
            "RATCHET_ADAPTER_ROOT",
            "RATCHET_ADMIN_KEYS",
            "RATCHET_ADMIN_TOKEN",
            "RATCHET_ALLOW_UNAUTHENTICATED",
            "RATCHET_API_GUARD",
            "RATCHET_API_KEY",
            "RATCHET_API_KEYS",
            "RATCHET_API_KEY_DAILY_REQUESTS",
            "RATCHET_AUDIT_RATE",
            "RATCHET_BOM_SIGNING_KEY",
            "RATCHET_CONFIDENCE_THRESHOLD",
            "RATCHET_CSP",
            "RATCHET_DASHBOARD_KEYS",
            "RATCHET_DATA_DIR",
            "RATCHET_DB",
            "RATCHET_DOMAIN",
            "RATCHET_DB_SYNC",
            "RATCHET_CHECK_REASONING",
            "RATCHET_FALLBACK_STUDENT",
            "RATCHET_FALLBACK_STUDENT_REASONING",
            "RATCHET_FIXTURES_DIR",
            "RATCHET_GOLDEN_FRACTION",
            "RATCHET_IDEMPOTENCY_CACHE",
            "RATCHET_IDEMPOTENCY_TTL_S",
            "RATCHET_JUDGE_MODEL",
            "RATCHET_ADJUDICATE_MAX",
            "RATCHET_KPI_WINDOW_DAYS",
            "RATCHET_LOG_FORMAT",
            "RATCHET_LOG_LEVEL",
            "RATCHET_MAX_BODY_BYTES",
            "RATCHET_MAX_INPUT_CHARS",
            "RATCHET_MAX_MESSAGES",
            "RATCHET_MAX_N",
            "RATCHET_MERGE_SYSTEM",
            "RATCHET_METRICS_TOKEN",
            "RATCHET_MOCK_ENDPOINT_HOURS",
            "RATCHET_MOCK_PACE",
            "RATCHET_MOCK_WARMUP_DAYS",
            "RATCHET_MOCK_WARMUP_REQUESTS",
            "RATCHET_MODE",
            "RATCHET_ON_TEACHER_FAILURE",
            "RATCHET_OPENAPI",
            "RATCHET_PRICES_VERIFIED",
            "RATCHET_PUBLIC_BOM",
            "RATCHET_PUBLIC_DASHBOARD",
            "RATCHET_REDACT_PII",
            "RATCHET_REDACT_PLUGIN",
            "RATCHET_REDACT_STYLE",
            "RATCHET_SAMPLES",
            "RATCHET_SCOUT_DAILY_BUDGET_USD",
            "RATCHET_SCOUT_MAX_RESULTS",
            "RATCHET_SCOUT_MAX_RUNS_PER_DAY",
            "RATCHET_SCOUT_RUNS_PER_HOUR",
            "RATCHET_SCRUB_STATS",
            "RATCHET_SEEN_DEDUP_S",
            "RATCHET_SIDECAR_DRAIN_S",
            "RATCHET_SIDECAR_QUEUE",
            "RATCHET_SIDECAR_WORKERS",
            "RATCHET_STATS_TTL_S",
            "RATCHET_STORE_FALLBACK_TEACHER",
            "RATCHET_STRICT_CONFIG",
            "RATCHET_STUDENT_BASE",
            "RATCHET_STUDENT_BASE_MODEL",
            "RATCHET_STUDENT_BREAKER_COOLDOWN_S",
            "RATCHET_STUDENT_BREAKER_FAILURES",
            "RATCHET_STUDENT_CONNECT_TIMEOUT_S",
            "RATCHET_STUDENT_COST_MODEL",
            "RATCHET_STUDENT_GPUS",
            "RATCHET_STUDENT_GPU_USD_PER_HOUR",
            "RATCHET_STUDENT_KEY",
            "RATCHET_STUDENT_MAX_TOKENS",
            "RATCHET_STUDENT_PARAMS",
            "RATCHET_STUDENT_REASONING",
            "RATCHET_STUDENT_TIMEOUT_S",
            "RATCHET_SYNC_CMD",
            "RATCHET_TASKS_FILE",
            "RATCHET_TEACHER_BASE",
            "RATCHET_TEACHER_BREAKER_COOLDOWN_S",
            "RATCHET_TEACHER_BREAKER_FAILURES",
            "RATCHET_TEACHER_DEADLINE_S",
            "RATCHET_TEACHER_FALLBACK_BASE",
            "RATCHET_TEACHER_FALLBACK_MODEL",
            "RATCHET_TEACHER_MAX_TOKENS",
            "RATCHET_TEACHER_MODALITIES",
            "RATCHET_TEACHER_MODEL",
            "RATCHET_TEACHER_PARAMS",
            "RATCHET_TEACHER_REASONING",
            "RATCHET_TEACHER_REASONING_FIELD",
            "RATCHET_TEACHER_REASONING_HEADROOM",
            "RATCHET_TEACHER_REASONING_TOKENS",
            "RATCHET_TEACHER_RETRIES",
            "RATCHET_TEACHER_TIMEOUT_S",
            "RATCHET_TICK_INTERVAL_S",
            "RATCHET_TRAIN_CMD",
            "RATCHET_TRAIN_GPUS",
            "RATCHET_TRAIN_GPU_USD_PER_HOUR",
            "RATCHET_TRUST_PROXY",
            "RATCHET_UNKNOWN_TASKS",
            # Read by the features merged with the hackathon lanes (integration's privacy, licensing, receipts and
            # certificates; lane G1's judge access and Tavily credits).
            "RATCHET_CALIBRATION",
            "RATCHET_CANARY_AUDIT_RATE",
            "RATCHET_CERT_DELTA",
            "RATCHET_CERT_TARGET",
            "RATCHET_END_USER_KEY",
            "RATCHET_ERASURE_SHARED",
            "RATCHET_EXAMPLE_RETENTION_DAYS",
            "RATCHET_FEEDBACK_CORRECTIONS",
            "RATCHET_KEY_LIMITS",
            "RATCHET_MOCK_TEACHER_REASONING_MEAN",
            "RATCHET_NO_STORE_TASKS",
            "RATCHET_RECEIPT_MIN_AUDITS",
            "RATCHET_REQUEST_RETENTION_DAYS",
            "RATCHET_SCOUT_JUDGE_BUDGET_USD",
            "RATCHET_SCOUT_JUDGE_CODE",
            "RATCHET_SCOUT_JUDGE_RUNS_PER_DAY",
            "RATCHET_SCOUT_JUDGE_SESSION_S",
            "RATCHET_STORE_EXAMPLES",
            "RATCHET_TAKE_RATE",
            "RATCHET_TEACHER_PROBES",
            "RATCHET_TAVILY_CYCLE_DAY",
            "RATCHET_TAVILY_JUDGE_CREDITS",
            "RATCHET_TAVILY_MONTHLY_CREDITS",
            "RATCHET_TAVILY_PACE_UNTIL",
            "RATCHET_TEACHER_LICENSES",
            # Spend and rate limits (lane D, ADR 0063).
            "RATCHET_API_KEY_CONCURRENCY",
            "RATCHET_API_KEY_DAILY_USD",
            "RATCHET_API_KEY_MONTHLY_USD",
            "RATCHET_API_KEY_RPM",
            "RATCHET_BUDGET_ALERT_PCT",
            "RATCHET_BUDGET_MODE",
            "RATCHET_DAILY_BUDGET_USD",
            "RATCHET_IP_RPM",
            "RATCHET_MAX_INFLIGHT",
            # Per-task calibration, structured outputs and the generic mock world (lane C).
            "RATCHET_MAX_AUTO_TASKS",
            "RATCHET_MIN_TASK_GOLDEN",
            "RATCHET_MOCK_STRICT",
            "RATCHET_PER_TASK_CALIBRATION",
            "RATCHET_SCHEMA_REPAIR",
            # Model routing and the other OpenAI endpoints (lane C2: ADR 0048, ADR 0050)
            "RATCHET_AUTO_TASK",
            "RATCHET_EMBEDDINGS_BASE",
            "RATCHET_EMBEDDINGS_KEY",
            "RATCHET_EMBEDDINGS_MODEL",
            "RATCHET_MODEL_ALIASES",
            "RATCHET_MODEL_POLICY",
            "TAVILY_API_KEY",
            *TOOL_ENV,
            *(env for env, _, _ in RESERVED_SETTINGS.values()),
        }
    )
)
GUARD_TOKEN_CHARS = 32  # RATCHET_ADMIN_TOKEN: what deploy/vm/Caddyfile requires before it lets a guarded path through
# RATCHET_* variables only the deploy scripts and the demo VM read (deploy/student_endpoint.sh, deploy/train_job.sh,
# deploy/vm/.env), never the package. Docker Compose hands the VM's whole .env to the gateway, so the startup check must
# not call them unknown; the traceability check holds each to a row in docs/CONFIGURATION.md ("Deploy scripts"), and
# tests/test_live.py checks the list against the Nebius scripts.
DEPLOY_ENV: frozenset[str] = frozenset(
    {
        "RATCHET_BACKUP_DIR",
        "RATCHET_BACKUP_KEEP",
        "RATCHET_BACKUP_TAG",
        "RATCHET_BUCKET_ID",
        "RATCHET_BUCKET_S3",
        "RATCHET_ENDPOINT_ID",
        "RATCHET_ENDPOINT_NAME",
        "RATCHET_PYTHON",
        "RATCHET_REASONING_PARSER_PLUGIN",
        "RATCHET_REGISTRY_SECRET",
        "RATCHET_REPO_URL",
        "RATCHET_S3_ENDPOINT",
        "RATCHET_SERVE_MAX_LEN",
        "RATCHET_SERVE_MAX_SEQS",
        "RATCHET_SERVE_PLATFORM",
        "RATCHET_SERVE_PRESET",
        "RATCHET_SERVE_TEMPLATE_KWARGS",
        "RATCHET_SSH_USER",
        "RATCHET_SUBNET_ID",
        "RATCHET_TRAIN_IMAGE",
        "RATCHET_TRAIN_PLATFORM",
        "RATCHET_TRAIN_PRESET",
        "RATCHET_VLLM_IMAGE",
        "RATCHET_VM_DISK_GB",
        "RATCHET_VM_NAME",
        "RATCHET_VM_PLATFORM",
        "RATCHET_VM_PRESET",
    }
)
# Variables only mock mode reads, with their defaults as the environment spells them: set to anything else in live mode,
# they do nothing, which the startup check says (a warning).
MOCK_ONLY_ENV: dict[str, str] = {
    "RATCHET_MOCK_WARMUP_DAYS": "0",
    "RATCHET_MOCK_WARMUP_REQUESTS": "1500",
    "RATCHET_MOCK_PACE": "0.25",
    "RATCHET_MOCK_ENDPOINT_HOURS": "24",
    "RATCHET_MOCK_TEACHER_REASONING_MEAN": "",
}
# 0/1 switches read as `== "1"` (or `!= "0"` for RATCHET_REDACT_PII, on unless 0): another value is not an error, for
# v0.4 compatibility, but it is a warning, because `true` reads as off (and `false` as on for redaction).
LOOSE_SWITCHES: dict[str, bool] = {
    "RATCHET_TRUST_PROXY": False,
    "RATCHET_MERGE_SYSTEM": False,
    "RATCHET_STORE_FALLBACK_TEACHER": False,
    "RATCHET_PRICES_VERIFIED": False,
    "RATCHET_REDACT_PII": True,
}
MODES = ("mock", "live")
MAX_SAMPLES = 16  # RATCHET_SAMPLES, as a task's own `policy.samples`
MAX_GOLDEN_FRACTION = 0.5  # RATCHET_GOLDEN_FRACTION: more would hold most examples out of training
MIN_USEFUL_AUDIT_RATE = 0.01  # below it a canary may never collect RATCHET_MIN_AUDITS audits (a warning)
MAX_IDEMPOTENCY_CACHE = 1_000_000  # RATCHET_IDEMPOTENCY_CACHE: answers kept by Idempotency-Key (ADR 0071)
MAX_RETRY_WINDOW_S = 86_400.0  # RATCHET_IDEMPOTENCY_TTL_S and RATCHET_SEEN_DEDUP_S: a day at most
DEFAULT_SEEN_DEDUP_S = 300.0  # RATCHET_SEEN_DEDUP_S unset in live mode
# The off-path sidecar (ADR 0072; `sidecar.MAX_WORKERS` and `sidecar.MAX_QUEUE` repeat the bounds, config imports nothing
# from the router's side).
DEFAULT_SIDECAR_WORKERS = 4  # RATCHET_SIDECAR_WORKERS unset in live mode
MAX_SIDECAR_WORKERS = 64
MAX_SIDECAR_QUEUE = 100_000
_ENV_NAME = re.compile(r"\b(?:RATCHET|NEBIUS|TAVILY)_[A-Z0-9_]*[A-Z0-9]")


def known_env() -> frozenset[str]:
    """Every variable Ratchet reads or its deploy scripts set: KNOWN_ENV, DEPLOY_ENV and ReleasePolicy's RATCHET_<FIELD>."""
    from .release import policy_env  # release imports config

    return frozenset(KNOWN_ENV) | DEPLOY_ENV | policy_env()


def _is_default(value: object, default: str) -> bool:
    """Whether a reserved setting holds its default (`default` as the environment spells it; empty means unset)."""
    if default == "":
        return value is None or value == ""
    if isinstance(value, str):
        return value.strip().lower() in ("", default.lower())
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) == float(default)


# RATCHET_ON_TEACHER_FAILURE: what a request whose teacher call failed gets (ADR 0019).
TEACHER_FAILURE_MODES = ("error", "serve_student")
# RATCHET_UNKNOWN_TASKS: what a request naming a task that is not registered gets (ADR 0022). `distill`: a task without
# fields made up for the name, distilled like any other (v0.4); `teacher`: the teacher answers and nothing is stored;
# `reject`: a 400 that lists the registered tasks.
UNKNOWN_TASK_POLICIES = ("distill", "teacher", "reject")
# RATCHET_STUDENT_COST_MODEL: which student cost the cost report's headline savings use (ratchet/cost.py, ADR 0023).
# `per_token`: the hosted Nano price per token, as every savings figure since v0.1; `gpu_hours`: the GPU-hours the student
# endpoint and the training jobs ran. Each request's `cost_usd` keeps the per-token price in both, so history never moves.
COST_MODELS = ("per_token", "gpu_hours")
# Bounds on a GPU rate wherever one is set or recorded (settings, the CLI, POST /ratchet/cost/endpoint, the ledger): no GPU
# lists near $1,000 an hour, and the bounds keep every total finite, so a typo or a visitor of the shared mock demo cannot
# turn the cost report into infinities.
MAX_USD_PER_GPU_HOUR = 1000.0
MAX_GPUS = 1024
# Savings receipts (ratchet/receipts.py, ADR 0029): audits a task needs before its baseline is calibrated are at least 2
# (a variance needs two), and the mock teacher's mean reasoning tokens are bounded so a typo cannot price a request at
# thousands of dollars.
MIN_RECEIPT_AUDITS = 2
MAX_MOCK_REASONING_TOKENS = 100_000
# Scout's judge access (ADR 0120): the code's length (`openssl rand -hex 16` gives 32 characters) and how long a judge's
# session cookie may last.
JUDGE_CODE_CHARS = (16, 256)
JUDGE_SESSION_S = (60.0, 30 * 86400.0)
# RATCHET_GATEWAY_LEASE (NFR-60, reserved until the leases land): what a second gateway process on a live ledger does.
LEASE_MODES = ("warn", "exclusive", "off")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# RATCHET_BUDGET_MODE: what /v1 requests get once the gateway's daily budget (RATCHET_DAILY_BUDGET_USD) is spent (ADR 0063).
# `block`: a 429 for every chat request until 00:00 UTC; `student_only`: answers the student gives alone (confident, no
# audit, no shadow) are still served, and a request that needs the teacher gets the 429.
BUDGET_MODES = ("block", "student_only")
DEFAULT_ALERT_PCT = "50,80,100"
MAX_INFLIGHT = 100_000
LIVE_IP_RPM = 120  # RATCHET_IP_RPM in live mode when unset: an open live gateway is limited per client IP


@dataclass(frozen=True)
class LimitPolicy:
    """The spend and rate limits, checked (`Settings.limit_policy()`, ADR 0063). `key_defaults`: every API key's limits
    without an override (RATCHET_API_KEY_*); `key_limits`: RATCHET_KEY_LIMITS as given, parsed against the key names by
    the gateway (`auth.parse_key_limits`); `daily_budget_usd`: the gateway's /v1 spend per UTC day (0: off) and
    `budget_mode` what happens past it (BUDGET_MODES); `alert_pct`: the shares of a cap that write a `budget` event, in
    ascending order; `ip_rpm`: requests a minute per client IP on a gateway without API keys (0: off); `max_inflight`:
    chat requests in flight before a 503 (0: off)."""

    key_defaults: KeyLimits
    key_limits: str
    daily_budget_usd: float
    budget_mode: str
    alert_pct: tuple[int, ...]
    ip_rpm: int
    max_inflight: int


def parse_alert_pct(raw: str, env: str = "RATCHET_BUDGET_ALERT_PCT") -> tuple[int, ...]:
    """`50,80,100` -> (50, 80, 100): whole percentages from 1 to 100, sorted, each once; empty: no alerts. Anything else
    raises ValueError naming `env`."""
    out: set[int] = set()
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= 100:
            raise ValueError(f"{env} must be whole percentages from 1 to 100 separated by commas, not {raw!r}")
        out.add(int(part))
    return tuple(sorted(out))


@dataclass(frozen=True)
class Access:
    """Who may read what, resolved from the access switches and the mode (`Settings.access()`, ADR 0061).

    `public_dashboard`: the dashboard API (/ratchet/stats, the task list, the cost report, ...) needs no key; else an
    admin or dashboard key. `public_bom`: the bills of materials need no key even when the dashboard API does.
    `scrub_stats`: True scrubs every dashboard read (client pseudonyms, no URLs, error texts cut to the class), False
    none, None (the default) only the anonymous reads of a public dashboard in live mode. `openapi`: /docs, /redoc and
    /openapi.json exist. `csp`: the Content-Security-Policy every response carries ('' = none). `allow_unauthenticated`:
    live mode may start without RATCHET_API_KEYS (something in front of the gateway checks callers)."""

    public_dashboard: bool
    public_bom: bool
    scrub_stats: bool | None
    openapi: bool
    csp: str
    allow_unauthenticated: bool

    def scrubs(self, live: bool, keyed: bool) -> bool:
        """Whether a dashboard read is scrubbed: `live` is the mode, `keyed` whether the reader sent an admin or dashboard
        key. A keyed reader of a public live dashboard sees what a private dashboard shows."""
        if self.scrub_stats is not None:
            return self.scrub_stats
        return live and self.public_dashboard and not keyed


MAX_AUTO_TASKS_LIMIT = MAX_TASKS // 2  # RATCHET_MAX_AUTO_TASKS: auto tasks may hold at most half the task registry


def _choice(value: object, env: str, choices: tuple[str, ...]) -> str:
    """One of `choices` from a setting, in any case; set but empty means the first (the default). ValueError names the
    variable otherwise."""
    mode = value.strip().lower() if isinstance(value, str) else None
    if mode == "":
        mode = choices[0]
    if mode not in choices:
        raise ValueError(f"{env} must be {' or '.join(choices)}, not {value!r}")
    return mode


# The most probes (RATCHET_TEACHER_PROBES) or adjudications (RATCHET_ADJUDICATE_MAX) one night may run: each is paid
# teacher calls, so a typo cannot turn the nightly job into a relabelling run.
MAX_TEACHER_WATCH_CALLS = 10_000


def _breaker_policy(role: str, failures: object, cooldown: object) -> BreakerPolicy:
    """A breaker policy from RATCHET_<ROLE>_BREAKER_FAILURES and _COOLDOWN_S; ValueError names the bad variable. The
    longest wait is 300 s, or the cooldown itself when that is longer."""
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
        raise ValueError(f"RATCHET_{role}_BREAKER_FAILURES must be 0 (no breaker) or more, not {failures!r}")
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or not (0 < cooldown < float("inf")):
        raise ValueError(f"RATCHET_{role}_BREAKER_COOLDOWN_S must be a positive number of seconds, not {cooldown!r}")
    return BreakerPolicy(failures, float(cooldown), max(MAX_OPEN_S, float(cooldown)))


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    output: float

    def cost(self, in_tokens: int, out_tokens: int) -> float:
        return (in_tokens * self.input + out_tokens * self.output) / 1_000_000


@dataclass(frozen=True)
class GpuRate:
    """What a GPU deployment costs while it runs: USD per GPU-hour for `gpus` GPUs. List prices are unverified until the
    day-1 checks pass (INV-9)."""

    usd_per_gpu_hour: float
    gpus: int

    @property
    def usd_per_hour(self) -> float:
        """The whole deployment's price per hour."""
        return self.usd_per_gpu_hour * self.gpus


@dataclass(frozen=True)
class CostPolicy:
    """The cost settings, checked (`Settings.cost_policy()`): the headline cost model (COST_MODELS), the student endpoint's
    and the training jobs' GPU rates, the hours a day a simulated endpoint runs, and whether an operator confirmed the
    prices (RATCHET_PRICES_VERIFIED)."""

    model: str
    student: GpuRate
    train: GpuRate
    mock_endpoint_hours: float
    prices_verified: bool


@dataclass(frozen=True)
class ReceiptPolicy:
    """The savings receipts' settings, checked (`Settings.receipt_policy()`, ADR 0029): the share of the billable lower
    bound that is the fee (RATCHET_TAKE_RATE) and the audits a task needs before its baseline is calibrated
    (RATCHET_RECEIPT_MIN_AUDITS)."""

    share: float
    min_audits: int


@dataclass(frozen=True)
class CertPolicy:
    """How each version's golden eval picks its threshold and certifies it (`Settings.cert_policy()`, ADR 0026): the
    calibration rule (`certificate.CALIBRATIONS`), the precision a certificate must show and its error rate."""

    calibration: str
    target: float
    delta: float


# RATCHET_ERASURE_SHARED: what an erasure of a client or an end user does with an example another client also sent
# (FR-13.3, ADR 0081). `delete`: purge it too; `keep`: keep it for the other client and unlink the erased party.
ERASURE_SHARED = ("delete", "keep")
MAX_RETENTION_DAYS = 36500  # RATCHET_EXAMPLE_RETENTION_DAYS and RATCHET_REQUEST_RETENTION_DAYS: 0 (forever) to this


@dataclass(frozen=True)
class Retention:
    """The retention and erasure settings, checked (`Settings.retention()`, FR-13.3, FR-13.4): whole days examples and
    request rows are kept after they were last seen or logged (0 = forever), and ERASURE_SHARED's choice."""

    example_days: int = 0
    request_days: int = 0
    erasure_shared: str = "delete"

    @property
    def enabled(self) -> bool:
        """Whether there is anything for a scheduled purge to do."""
        return self.example_days > 0 or self.request_days > 0


@dataclass(frozen=True)
class ScoutPolicy:
    """Scout's judge access and Tavily credit settings, checked (`Settings.scout_policy()`, ADR 0120). The judge budget
    and judge credits are resolved: the variable's value, or the share of judge runs in the daily run cap when it is
    unset. The code is kept out of `repr`."""

    judge_code: str = field(repr=False)
    judge_runs_per_day: int
    judge_budget_usd: float
    judge_session_s: float
    monthly_credits: int
    judge_credits: int
    cycle_day: int
    pace_until: date | None

    @property
    def judge_enabled(self) -> bool:
        return bool(self.judge_code)


DEFAULT_PRICES: dict[str, Price] = {
    # Token Factory list prices (USD / 1M tokens), Oct 2026. Verify in the console.
    "nvidia/Nemotron-3-Ultra-550b-a55b": Price(1.00, 3.00),
    "nvidia/nemotron-3-super-120b-a12b": Price(0.30, 0.90),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": Price(0.06, 0.24),
    "nvidia/Nemotron-3_5-Lightning": Price(0.06, 0.24),
    # Self-hosted student on a Nebius Serverless Endpoint. Real cost is GPU-hours;
    # this per-token proxy uses the hosted Nano price, which is conservative.
    "student": Price(0.06, 0.24),
}
# Embeddings models the /v1/embeddings pass-through prices (FR-1.51): input tokens only. Kept apart from DEFAULT_PRICES,
# whose models are teachers, judges and students with a license each; an embeddings model writes no training data.
# Unverified until scripts/day1_check.py passes (INV-9), like every price here. A model with no price here is priced as
# the teacher (`Settings.price`), which overstates its cost.
EMBEDDING_PRICES: dict[str, Price] = {
    "Qwen/Qwen3-Embedding-8B": Price(0.01, 0.0),
}
DEFAULT_EMBEDDINGS_MODEL = "Qwen/Qwen3-Embedding-8B"  # RATCHET_EMBEDDINGS_MODEL
EMBEDDINGS_OFF = "off"  # RATCHET_EMBEDDINGS_BASE=off: /v1/embeddings answers 404 unsupported_endpoint


@dataclass
class Settings:
    mode: str = field(default_factory=lambda: _env("RATCHET_MODE", "mock"))  # "mock" | "live"
    db_path: str = field(default_factory=lambda: _env("RATCHET_DB", "ratchet.db"))

    # Teacher + judge on Nebius Token Factory (OpenAI-compatible).
    teacher_base: str = field(default_factory=lambda: _env("RATCHET_TEACHER_BASE", "https://api.tokenfactory.nebius.com/v1"))
    teacher_api_key: str = field(default_factory=lambda: _env("NEBIUS_API_KEY", ""))
    teacher_model: str = field(default_factory=lambda: _env("RATCHET_TEACHER_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b"))
    judge_model: str = field(default_factory=lambda: _env("RATCHET_JUDGE_MODEL", "nvidia/nemotron-3-super-120b-a12b"))
    # Untuned hosted model used when the self-hosted student endpoint is offline.
    fallback_student_model: str = field(
        default_factory=lambda: _env("RATCHET_FALLBACK_STUDENT", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
    )
    # Open teachers added without a code change (ratchet/licenses.py, ADR 0090): a JSON list of {model, license, url,
    # note}, merged into a copy of the built-in registry at startup. Only allowed licenses, never a closed model family;
    # a bad file stops startup with every error listed. Empty = the built-in registry only.
    teacher_licenses_file: str = field(default_factory=lambda: _env("RATCHET_TEACHER_LICENSES", ""))

    # Student on a Nebius Serverless Endpoint running vLLM with LoRA adapters.
    student_base: str = field(default_factory=lambda: _env("RATCHET_STUDENT_BASE", ""))
    student_api_key: str = field(default_factory=lambda: _env("RATCHET_STUDENT_KEY", ""))
    student_base_model: str = field(
        default_factory=lambda: _env("RATCHET_STUDENT_BASE_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    )

    # Cascade.
    confidence_threshold: float = field(default_factory=lambda: _float("RATCHET_CONFIDENCE_THRESHOLD", "0.80"))
    samples: int = field(default_factory=lambda: _int("RATCHET_SAMPLES", "3"))
    audit_rate: float = field(default_factory=lambda: _float("RATCHET_AUDIT_RATE", "0.05"))
    golden_fraction: float = field(default_factory=lambda: _float("RATCHET_GOLDEN_FRACTION", "0.10"))
    # Calibration rows (the even half of a task's golden rows) a task needs before its own threshold may go below the
    # version's global one (FR-14.1, ADR 0040); with fewer it gets the higher of the two.
    min_task_golden: int = field(default_factory=lambda: _int("RATCHET_MIN_TASK_GOLDEN", "20"))
    # RATCHET_PER_TASK_CALIBRATION=0: Night School writes no per-task records, so each new version keeps one pooled
    # threshold and the golden gate checks only the pooled numbers, as in v0.4 (FR-14.1, ADR 0040). On by default.
    per_task_calibration: bool = field(default_factory=lambda: _env("RATCHET_PER_TASK_CALIBRATION", "1") != "0")

    # Upstream timeouts and retries (ratchet/resilience.py, ADR 0014). The read timeout is how long a model may take to
    # answer; connect, write and pool timeouts are 5, 10 and 5 s (the student's connect timeout is its own). A failed
    # teacher call (the judge's too) is retried up to RATCHET_TEACHER_RETRIES times, within RATCHET_TEACHER_DEADLINE_S
    # seconds in all. A student call on the request path is never retried: the teacher is its retry.
    teacher_timeout_s: float = field(default_factory=lambda: _float("RATCHET_TEACHER_TIMEOUT_S", "120"))
    teacher_retries: int = field(default_factory=lambda: _int("RATCHET_TEACHER_RETRIES", "2"))
    teacher_deadline_s: float = field(default_factory=lambda: _float("RATCHET_TEACHER_DEADLINE_S", "180"))
    student_timeout_s: float = field(default_factory=lambda: _float("RATCHET_STUDENT_TIMEOUT_S", "15"))
    student_connect_timeout_s: float = field(default_factory=lambda: _float("RATCHET_STUDENT_CONNECT_TIMEOUT_S", "2"))
    # Student circuit breaker (ADR 0015). After RATCHET_STUDENT_BREAKER_FAILURES endpoint failures in a row (timeouts,
    # connection errors, 5xx, 408, 429) the student is skipped for RATCHET_STUDENT_BREAKER_COOLDOWN_S seconds and the
    # teacher answers at once; then one probe decides, and each failed probe doubles the wait up to 300 s. 0 = no breaker.
    student_breaker_failures: int = field(default_factory=lambda: _int("RATCHET_STUDENT_BREAKER_FAILURES", "5"))
    student_breaker_cooldown_s: float = field(default_factory=lambda: _float("RATCHET_STUDENT_BREAKER_COOLDOWN_S", "30"))

    # When the teacher is down (ADR 0019). Its breaker opens after RATCHET_TEACHER_BREAKER_FAILURES failed calls in a row
    # (each after its retries), and requests that need its answer then get a 503 at once instead of waiting out the
    # deadline; the same waits as the student's breaker. 0 = no breaker.
    teacher_breaker_failures: int = field(default_factory=lambda: _int("RATCHET_TEACHER_BREAKER_FAILURES", "5"))
    teacher_breaker_cooldown_s: float = field(default_factory=lambda: _float("RATCHET_TEACHER_BREAKER_COOLDOWN_S", "30"))
    # 'error': a request whose teacher call failed gets the error. 'serve_student': an escalated request is answered with
    # the student's own (below-threshold) answer instead, marked degraded.
    on_teacher_failure: str = field(default_factory=lambda: _env("RATCHET_ON_TEACHER_FAILURE", "error"))
    # A second licensed teacher that answers when the primary is unavailable; empty = none. Same API key as the teacher,
    # at RATCHET_TEACHER_FALLBACK_BASE (empty = RATCHET_TEACHER_BASE). Its answers are stored as training data only with
    # RATCHET_STORE_FALLBACK_TEACHER=1.
    teacher_fallback_model: str = field(default_factory=lambda: _env("RATCHET_TEACHER_FALLBACK_MODEL", ""))
    teacher_fallback_base: str = field(default_factory=lambda: _env("RATCHET_TEACHER_FALLBACK_BASE", ""))
    store_fallback_teacher: bool = field(default_factory=lambda: _env("RATCHET_STORE_FALLBACK_TEACHER", "0") == "1")

    # Extra output tokens the teacher spends on reasoning (billed, not returned).
    teacher_reasoning_tokens: int = field(default_factory=lambda: _int("RATCHET_TEACHER_REASONING_TOKENS", "300"))

    # Generation parameters (ADR 0017). Which of a client's request fields each role forwards (comma-separated; empty =
    # the default list, 'none' = nothing, 'all' = every standard field), and the token caps (0 = no cap). The teacher
    # serving an answer gets the client's max_tokens plus the headroom, for reasoning it writes before the answer.
    teacher_params: str = field(default_factory=lambda: _env("RATCHET_TEACHER_PARAMS", ""))
    student_params: str = field(default_factory=lambda: _env("RATCHET_STUDENT_PARAMS", ""))
    teacher_max_tokens: int = field(default_factory=lambda: _int("RATCHET_TEACHER_MAX_TOKENS", str(DEFAULT_TEACHER_MAX_TOKENS)))
    student_max_tokens: int = field(default_factory=lambda: _int("RATCHET_STUDENT_MAX_TOKENS", str(DEFAULT_STUDENT_MAX_TOKENS)))
    teacher_reasoning_headroom: int = field(default_factory=lambda: _int("RATCHET_TEACHER_REASONING_HEADROOM", "0"))
    # Nemotron's thinking switch by role, sent as chat_template_kwargs (ADR 0130; reasoning.py). Empty = the default.
    student_reasoning: str = field(default_factory=lambda: _env("RATCHET_STUDENT_REASONING", ""))
    fallback_student_reasoning: str = field(default_factory=lambda: _env("RATCHET_FALLBACK_STUDENT_REASONING", ""))
    teacher_reasoning: str = field(default_factory=lambda: _env("RATCHET_TEACHER_REASONING", ""))
    check_reasoning: str = field(default_factory=lambda: _env("RATCHET_CHECK_REASONING", ""))
    teacher_reasoning_field: str = field(default_factory=lambda: _env("RATCHET_TEACHER_REASONING_FIELD", ""))

    # Scout, the multi-agent research demo (docs/SCOUT.md).
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY", ""))
    scout_daily_budget_usd: float = field(default_factory=lambda: _float("RATCHET_SCOUT_DAILY_BUDGET_USD", "3.00"))
    scout_runs_per_hour: int = field(default_factory=lambda: _int("RATCHET_SCOUT_RUNS_PER_HOUR", "6"))
    scout_max_runs_per_day: int = field(default_factory=lambda: _int("RATCHET_SCOUT_MAX_RUNS_PER_DAY", "30"))
    scout_max_results: int = field(default_factory=lambda: _int("RATCHET_SCOUT_MAX_RESULTS", "4"))
    # Judge access (ADR 0120): runs per UTC day held back for hackathon judges, unlocked by a code sent once by POST
    # (`POST /scout/judge`) and then carried by a signed cookie. Empty code = off. The judge budget defaults to the judges'
    # share of the run cap. Read with `scout_policy()`, which never echoes the code.
    scout_judge_code: str = field(default_factory=lambda: _env("RATCHET_SCOUT_JUDGE_CODE", ""), repr=False)
    scout_judge_runs_per_day: int = field(default_factory=lambda: _int("RATCHET_SCOUT_JUDGE_RUNS_PER_DAY", "0"))
    scout_judge_budget_usd: float | None = field(default_factory=lambda: _float_or_none("RATCHET_SCOUT_JUDGE_BUDGET_USD"))
    scout_judge_session_s: float = field(default_factory=lambda: _float("RATCHET_SCOUT_JUDGE_SESSION_S", "604800"))
    # How old the watchdog's last probe may be before the banner flag reads `stale` (FR-11.33); 600 s to 7 days.
    watch_flag_max_age_s: int = field(default_factory=lambda: _int("RATCHET_WATCH_FLAG_MAX_AGE_S", "10800"))
    # Tavily credits per cycle (0 = counted, not capped), the judges' share (unset = their share of the run cap), the
    # day of the month the cycle starts (00:00 UTC) and the date until which the daily allowance spreads what is left.
    tavily_monthly_credits: int = field(default_factory=lambda: _int("RATCHET_TAVILY_MONTHLY_CREDITS", "0"))
    tavily_judge_credits: float | None = field(default_factory=lambda: _float_or_none("RATCHET_TAVILY_JUDGE_CREDITS"))
    tavily_cycle_day: int = field(default_factory=lambda: _int("RATCHET_TAVILY_CYCLE_DAY", "1"))
    tavily_pace_until: str = field(default_factory=lambda: _env("RATCHET_TAVILY_PACE_UNTIL", ""))
    # Behind a reverse proxy (Caddy on the demo VM), take the client IP from X-Forwarded-For.
    trust_proxy: bool = field(default_factory=lambda: _env("RATCHET_TRUST_PROXY", "0") == "1")

    # Privacy: replace emails, phone numbers, card numbers etc. with placeholders before a
    # request and the teacher's answer are stored as a training example (ratchet/redact.py).
    redact_pii: bool = field(default_factory=lambda: _env("RATCHET_REDACT_PII", "1") != "0")
    # Placeholder style (ADR 0082): `label` ([EMAIL], v0.4), `numbered` ([EMAIL_1], the same value the same number in
    # the prompt and the answer) or `surrogate` (person1@example.com). A task spec's `privacy.redact_style` overrides it.
    redact_style: str = field(default_factory=lambda: _env("RATCHET_REDACT_STYLE", "label"))
    # A detector plugin, 'module:function' (redact.Detector), such as an NER model for names. Imported at startup, which
    # fails when it is missing. Empty: patterns only.
    redact_plugin: str = field(default_factory=lambda: _env("RATCHET_REDACT_PLUGIN", ""))
    # Storage opt-outs (FR-13.1, ADR 0080). 0 is zero retention: no teacher answer is stored as a training example and
    # Night School skips every night. The requests table (metadata only) is still written.
    store_examples: bool = field(default_factory=lambda: _bool("RATCHET_STORE_EXAMPLES", "1"))
    # Tasks whose teacher answers are never stored, comma-separated: the same as `store: false` in their specs.
    no_store_tasks: str = field(default_factory=lambda: _env("RATCHET_NO_STORE_TASKS", ""))
    # End-user attribution (FR-13.2): the HMAC key `user` and `safety_identifier` are hashed with (at least 16
    # characters). Empty = 32 random bytes generated once and kept in the ledger's meta table.
    end_user_key: str = field(default_factory=lambda: _env("RATCHET_END_USER_KEY", ""))
    # Human feedback (FR-13.30, ADR 0091): whether a correction sent with an 'incorrect' verdict may be stored as a
    # human-labelled training example. 0 keeps the verdicts (statistics and alerts) and stores no correction.
    feedback_corrections: bool = field(default_factory=lambda: _bool("RATCHET_FEEDBACK_CORRECTIONS", "1"))
    # Retention and erasure (FR-13.3, FR-13.4, ADR 0081): whole days a stored example is kept after it was last seen, and a
    # request row after it was logged (its sums are kept in daily_rollup), 0 = forever; and whether erasing a client or
    # an end user also purges an example another client sent too (`delete`) or keeps it for that client (`keep`).
    example_retention_days: int = field(default_factory=lambda: _int("RATCHET_EXAMPLE_RETENTION_DAYS", "0"))
    request_retention_days: int = field(default_factory=lambda: _int("RATCHET_REQUEST_RETENTION_DAYS", "0"))
    erasure_shared: str = field(default_factory=lambda: _env("RATCHET_ERASURE_SHARED", "delete"))

    # Gateway API keys for /v1/*: "name:key,name:key". Empty = no authentication (v0.3 behaviour).
    api_keys: str = field(default_factory=lambda: _env("RATCHET_API_KEYS", ""))
    # Requests per key per UTC day on /v1/chat/completions. 0 = unlimited.
    api_key_daily_requests: int = field(default_factory=lambda: _int("RATCHET_API_KEY_DAILY_REQUESTS", "0"))
    # Spend and rate limits (ADR 0063), checked by `limit_policy()`. Every API key's defaults (0 = off): US dollars per
    # UTC day and month, requests a minute and requests in flight; RATCHET_KEY_LIMITS overrides them (and the daily
    # requests, task scopes and storage) per key name: "acme:daily_usd=5,rpm=60,tasks=a|b;globex:store=0". The gateway's
    # own /v1 budget per UTC day (0 = off), what happens past it (BUDGET_MODES) and the shares of a cap that write a
    # `budget` event. Requests a minute per client IP on a gateway without API keys (unset: 0 in mock mode, 120 in live
    # mode) and chat requests in flight before a 503 (0 = off; below the 40 worker threads, so a burst cannot queue).
    key_limits: str = field(default_factory=lambda: _env("RATCHET_KEY_LIMITS", ""))
    api_key_daily_usd: float = field(default_factory=lambda: _float("RATCHET_API_KEY_DAILY_USD", "0"))
    api_key_monthly_usd: float = field(default_factory=lambda: _float("RATCHET_API_KEY_MONTHLY_USD", "0"))
    api_key_rpm: int = field(default_factory=lambda: _int("RATCHET_API_KEY_RPM", "0"))
    api_key_concurrency: int = field(default_factory=lambda: _int("RATCHET_API_KEY_CONCURRENCY", "0"))
    daily_budget_usd: float = field(default_factory=lambda: _float("RATCHET_DAILY_BUDGET_USD", "0"))
    budget_mode: str = field(default_factory=lambda: _env("RATCHET_BUDGET_MODE", "block"))
    budget_alert_pct: str = field(default_factory=lambda: _env("RATCHET_BUDGET_ALERT_PCT", DEFAULT_ALERT_PCT))
    ip_rpm: int | None = field(default_factory=lambda: _int_or_none("RATCHET_IP_RPM"))
    max_inflight: int = field(default_factory=lambda: _int("RATCHET_MAX_INFLIGHT", "32"))
    # Admin keys for management writes (PUT and DELETE /ratchet/tasks/...): "name:key,name:key", like RATCHET_API_KEYS
    # and never the same keys. Empty: the admin API is off in live mode and open in mock mode (ADR 0022). Not the Caddy
    # edge guard's RATCHET_ADMIN_TOKEN.
    admin_keys: str = field(default_factory=lambda: _env("RATCHET_ADMIN_KEYS", ""))
    # Who may read what (`Settings.access()`, ADR 0061). Read-only keys for the dashboard API, "name:key,name:key" like
    # RATCHET_API_KEYS and never the same keys as an API client or an admin. The switches are 0 or 1; unset takes the
    # default: the dashboard API and /docs public in mock mode and private in live mode, the BOM endpoints as the
    # dashboard, stats scrubbed for anonymous readers of a public live dashboard, and live mode refusing to start
    # without RATCHET_API_KEYS. RATCHET_CSP: a Content-Security-Policy for every response (empty = none).
    dashboard_keys: str = field(default_factory=lambda: _env("RATCHET_DASHBOARD_KEYS", ""))
    public_dashboard: bool | None = field(default_factory=lambda: _switch("RATCHET_PUBLIC_DASHBOARD"))
    public_bom: bool | None = field(default_factory=lambda: _switch("RATCHET_PUBLIC_BOM"))
    scrub_stats: bool | None = field(default_factory=lambda: _switch("RATCHET_SCRUB_STATS"))
    openapi: bool | None = field(default_factory=lambda: _switch("RATCHET_OPENAPI"))
    allow_unauthenticated: bool | None = field(default_factory=lambda: _switch("RATCHET_ALLOW_UNAUTHENTICATED"))
    csp: str = field(default_factory=lambda: _env("RATCHET_CSP", ""))
    # The demo VM's edge guard, which the gateway cannot see: Caddy answers 403 on `/v1/*`, `/openai/*`, `/ratchet/demo/*`
    # and `/ratchet/admin/*` unless a request carries `X-Ratchet-Admin-Token` (deploy/vm/Caddyfile). Compose hands both
    # variables to the gateway, which refuses live mode without API keys unless they say the guard is on (`guard_problem`).
    api_guard: str = field(default_factory=lambda: _env("RATCHET_API_GUARD", "open"))
    admin_token: str = field(default_factory=lambda: _env("RATCHET_ADMIN_TOKEN", ""), repr=False)

    # Tasks (ratchet/taskspec.py, ADR 0022). A JSON (or YAML, with PyYAML) file of task specs registered at startup; a bad
    # file stops startup with every error listed. Empty = the built-in tasks and the ones the admin API stored.
    tasks_file: str = field(default_factory=lambda: _env("RATCHET_TASKS_FILE", ""))
    # What a request naming an unregistered task gets: UNKNOWN_TASK_POLICIES.
    unknown_tasks: str = field(default_factory=lambda: _env("RATCHET_UNKNOWN_TASKS", "distill"))
    # How a request without a task header or metadata finds its task (ratchet/openai_compat.py, ADR 0048). What a `model`
    # Ratchet does not know gets: 'lenient' serves it as `ratchet`, 'strict' answers 404 `model_not_found`. Model ids that
    # name a task ('gpt-4o-mini=ticket-triage,gpt-4o=default'). 'system_prompt': a request no other rule places gets an
    # auto task per first system message, at most RATCHET_MAX_AUTO_TASKS of them ('off': it gets `default`).
    model_policy: str = field(default_factory=lambda: _env("RATCHET_MODEL_POLICY", "lenient"))
    model_aliases: str = field(default_factory=lambda: _env("RATCHET_MODEL_ALIASES", ""))
    auto_task: str = field(default_factory=lambda: _env("RATCHET_AUTO_TASK", "off"))
    # Where fixture files that a task spec names must live (`taskspec.resolve_fixture`), so a spec cannot read
    # any other file on the host: the SQL scripts sql tasks run their answers on (FR-2.56, ratchet/sqlcheck.py).
    fixtures_dir: str = field(default_factory=default_fixtures_dir)
    # The most auto tasks the registry holds (ADR 0042, ADR 0048): those derived from a response_format json_schema
    # (ratchet/schema_tasks.py; past the cap a new schema gets the unknown-task path) and those made per system prompt
    # (RATCHET_AUTO_TASK=system_prompt; past the cap `default`) count together. 0 turns auto tasks off.
    max_auto_tasks: int = field(default_factory=lambda: _int("RATCHET_MAX_AUTO_TASKS", "200"))
    # One more teacher call when a teacher answer breaks the request's response_format (0: none).
    schema_repair: bool = field(default_factory=lambda: _env("RATCHET_SCHEMA_REPAIR", "1") != "0")

    # Request limits on the OpenAI-compatible API (ratchet/openai_compat.py), checked before any model call. 0 = no limit.
    # The body limit applies to every POST, PUT and PATCH; the other two to /v1/chat/completions.
    max_body_bytes: int = field(default_factory=lambda: _int("RATCHET_MAX_BODY_BYTES", "2097152"))
    max_messages: int = field(default_factory=lambda: _int("RATCHET_MAX_MESSAGES", "256"))
    max_input_chars: int = field(default_factory=lambda: _int("RATCHET_MAX_INPUT_CHARS", "120000"))
    # The most choices (`n`) a request may ask for, 1 to 128; more is a 400. Each choice past the student's own samples
    # is one more student sample, and an escalated request asks the teacher for n choices (FR-1.14).
    max_n: int = field(default_factory=lambda: _int("RATCHET_MAX_N", str(DEFAULT_MAX_N)))
    # Merge consecutive leading system (and developer) messages into one, for models that accept only one.
    merge_system: bool = field(default_factory=lambda: _env("RATCHET_MERGE_SYSTEM", "0") == "1")
    # Content part types besides text that the teacher accepts ("text,image_url"). Such requests go to the teacher
    # only and are never stored; any other non-text part is a 400.
    teacher_modalities: str = field(default_factory=lambda: _env("RATCHET_TEACHER_MODALITIES", "text"))
    # The /v1/embeddings pass-through (FR-1.51, ADR 0050): the OpenAI-compatible base URL that embeds (empty = the
    # teacher's, RATCHET_TEACHER_BASE; 'off' turns the endpoint off), the one model it always asks for, whatever a client
    # names, and its API key (empty = NEBIUS_API_KEY). Nothing of an embeddings request is stored; mock mode embeds
    # locally.
    embeddings_base: str = field(default_factory=lambda: _env("RATCHET_EMBEDDINGS_BASE", ""))
    embeddings_model: str = field(default_factory=lambda: _env("RATCHET_EMBEDDINGS_MODEL", DEFAULT_EMBEDDINGS_MODEL))
    embeddings_api_key: str = field(default_factory=lambda: _env("RATCHET_EMBEDDINGS_KEY", ""))

    # Retries (ADR 0071). Answers kept by Idempotency-Key, in memory: how many (0 = the header is ignored) and for how
    # many seconds after each finished (0 = only a request that arrives while the first runs shares its answer). And the
    # seconds within which the same API client's repeat of a stored example counts once toward the frequent-request
    # rule (unset: 300 in live mode, 0 in mock mode, whose repeats are synthetic traffic; 0 = every repeat counts, v0.4).
    idempotency_cache: int = field(default_factory=lambda: _int("RATCHET_IDEMPOTENCY_CACHE", "10000"))
    idempotency_ttl_s: float = field(default_factory=lambda: _float("RATCHET_IDEMPOTENCY_TTL_S", "600"))
    seen_dedup_s: float | None = field(default_factory=lambda: _float_or_none("RATCHET_SEEN_DEDUP_S"))

    # Ed25519 private key (base64 of the 32-byte seed) that signs each adapter's bill of materials.
    # Generate one with `python -m ratchet.bom keygen`. Empty = manifests are stored unsigned.
    bom_signing_key: str = field(default_factory=lambda: _env("RATCHET_BOM_SIGNING_KEY", ""))

    # Cost accounting (ratchet/cost.py, ADR 0023). The self-hosted student endpoint and the training jobs bill GPU-hours
    # while they run; these rates (USD per GPU-hour, the L40S list price, unverified until the day-1 checks pass) price
    # the compute intervals the ledger records, and RATCHET_STUDENT_COST_MODEL picks the headline view.
    student_cost_model: str = field(default_factory=lambda: _env("RATCHET_STUDENT_COST_MODEL", "per_token"))
    student_gpu_usd_per_hour: float = field(default_factory=lambda: _float("RATCHET_STUDENT_GPU_USD_PER_HOUR", "1.55"))
    student_gpus: int = field(default_factory=lambda: _int("RATCHET_STUDENT_GPUS", "1"))
    train_gpu_usd_per_hour: float = field(default_factory=lambda: _float("RATCHET_TRAIN_GPU_USD_PER_HOUR", "1.55"))
    train_gpus: int = field(default_factory=lambda: _int("RATCHET_TRAIN_GPUS", "1"))
    # 1 once scripts/day1_check.py passed and the GPU rates match the Nebius bill; until then every report says the
    # prices are unverified list prices.
    prices_verified: bool = field(default_factory=lambda: _env("RATCHET_PRICES_VERIFIED", "0") == "1")
    # Mock mode only: hours a day the simulated student endpoint runs (each simulated day records one interval).
    mock_endpoint_hours: float = field(default_factory=lambda: _float("RATCHET_MOCK_ENDPOINT_HOURS", "24"))

    # Success metrics (ratchet/kpis.py, FR-7.9): SM-1 to SM-3 and NFR-1 are measured over the last this many ledger days
    # with traffic, so they describe the steady state rather than the cold start.
    kpi_window_days: int = field(default_factory=lambda: _int("RATCHET_KPI_WINDOW_DAYS", "3"))

    # Audit focusing (FR-16.1, ADR 0025): the canary's own audit rate, more than 0 and at most 1; unset = RATCHET_AUDIT_RATE.
    # Read with `checked_canary_audit_rate()`.
    canary_audit_rate: float | None = field(default_factory=lambda: _float_or_none("RATCHET_CANARY_AUDIT_RATE"))

    # Certified precision (FR-16.3, ADR 0026): how the golden eval picks each version's threshold ('point': the lowest
    # whose calibration answers reach 97%, as in v0.4; 'ltt': Learn-then-Test over a fixed grid, certified at the target),
    # and the precision a certificate must show with error rate RATCHET_CERT_DELTA. Read with `cert_policy()`.
    calibration: str = field(default_factory=lambda: _env("RATCHET_CALIBRATION", "point"))
    cert_target: float = field(default_factory=lambda: _float("RATCHET_CERT_TARGET", "0.95"))
    cert_delta: float = field(default_factory=lambda: _float("RATCHET_CERT_DELTA", "0.05"))

    # The teacher watch (FR-16.7, ADR 0033): stored labels Night School re-asks the teacher about per night (probes,
    # FR-5.40) and contested labels it adjudicates per night with two more teacher answers each (FR-5.41); 0 = none.
    # Read with `teacher_watch_limits()`.
    teacher_probes: int = field(default_factory=lambda: _int("RATCHET_TEACHER_PROBES", "20"))
    adjudicate_max: int = field(default_factory=lambda: _int("RATCHET_ADJUDICATE_MAX", "50"))

    # Savings receipts (FR-15.5, ADR 0029): the share of the billable lower bound that is the fee, the audits a task needs
    # before its baseline is calibrated (read with `receipt_policy()`), and, in mock mode only, the mock teacher's mean
    # billed reasoning tokens (unset: the fixed RATCHET_TEACHER_REASONING_TOKENS; `checked_mock_reasoning_mean()`).
    take_rate: float = field(default_factory=lambda: _float("RATCHET_TAKE_RATE", "0.25"))
    receipt_min_audits: int = field(default_factory=lambda: _int("RATCHET_RECEIPT_MIN_AUDITS", "30"))
    mock_teacher_reasoning_mean: float | None = field(
        default_factory=lambda: _float_or_none("RATCHET_MOCK_TEACHER_REASONING_MEAN")
    )

    # The evidence features (ADR 0024). Declared with defaults that keep today's behaviour; reserved until the item that
    # reads each one lands (RESERVED_SETTINGS, `check_reserved`). Per feature: the backfill's batch API, discount and
    # output cap; the ledger's sync mode.
    batch_api: str = field(default_factory=lambda: _env("RATCHET_BATCH_API", "operations"))
    batch_discount: float = field(default_factory=lambda: _float("RATCHET_BATCH_DISCOUNT", "0.5"))
    backfill_max_tokens: int = field(default_factory=lambda: _int("RATCHET_BACKFILL_MAX_TOKENS", "2048"))

    # The hot path (ADR 0060). The ledger file's journal: NORMAL = WAL with synchronous=NORMAL (the default), FULL = the
    # rollback journal with synchronous=FULL (v0.4). How often the request path runs the release gate and how long
    # /ratchet/stats serves one computed payload, in seconds (unset = 5 and 2 in live mode, 0 and 0 in mock mode, where
    # simulations and tests tick after every request and read fresh stats).
    db_sync: str = field(default_factory=lambda: _env("RATCHET_DB_SYNC", "NORMAL"))
    # NFR-60 (reserved until lane D's leases land): `warn`, `exclusive` or `off` for a second gateway on a live ledger.
    gateway_lease: str = field(default_factory=lambda: _env("RATCHET_GATEWAY_LEASE", "warn"))
    tick_interval_s: float | None = field(default_factory=lambda: _float_or_none("RATCHET_TICK_INTERVAL_S"))
    stats_ttl_s: float | None = field(default_factory=lambda: _float_or_none("RATCHET_STATS_TTL_S"))
    # The off-path sidecar (ADR 0072): threads that run a request's audit, shadow samples and arbitration after its answer
    # is sent (unset: 4 in live mode, 0 in mock mode, where every check stays on the request path and simulations are
    # unchanged), the jobs that may wait for them, and how long the gateway's shutdown waits for the queue to empty.
    sidecar_workers: int | None = field(default_factory=lambda: _int_or_none("RATCHET_SIDECAR_WORKERS"))
    sidecar_queue: int = field(default_factory=lambda: _int("RATCHET_SIDECAR_QUEUE", "1000"))
    sidecar_drain_s: float = field(default_factory=lambda: _float("RATCHET_SIDECAR_DRAIN_S", "10"))

    # Observability (ADR 0062). Log lines on stderr as JSON objects or key=value text, at a level (unset: text and WARNING
    # in mock mode, so tests and the demo stay quiet; JSON and INFO in live mode, one line per chat request). A bearer
    # token a Prometheus scraper may send to GET /metrics besides the admin and dashboard keys (empty: none).
    log_format: str = field(default_factory=lambda: _env("RATCHET_LOG_FORMAT", ""))
    log_level: str = field(default_factory=lambda: _env("RATCHET_LOG_LEVEL", ""))
    metrics_token: str = field(default_factory=lambda: _env("RATCHET_METRICS_TOKEN", ""))

    # Configuration checks (ADR 0070). 1: a warning from the startup check (`validate`) stops startup like an error.
    strict_config: bool | None = field(default_factory=lambda: _switch("RATCHET_STRICT_CONFIG"))

    # Mock mode only: simulate this many days of mixed traffic + nights at startup, so the
    # public demo opens with a trained student instead of a cold start.
    mock_warmup_days: int = field(default_factory=lambda: _int("RATCHET_MOCK_WARMUP_DAYS", "0"))
    mock_warmup_requests: int = field(default_factory=lambda: _int("RATCHET_MOCK_WARMUP_REQUESTS", "1500"))
    # Mock mode only: Scout's live stream waits this fraction of each call's simulated latency,
    # so the page shows agents working instead of finishing in 20 ms. 0 = no waiting.
    mock_pace: float = field(default_factory=lambda: _float("RATCHET_MOCK_PACE", "0.25"))
    # Mock mode only: answer just the demo's own requests (tickets, research calls), a 400 for anything else, as before
    # the generic mock world (sim/generic.py) answered every request.
    mock_strict: bool = field(default_factory=lambda: _env("RATCHET_MOCK_STRICT", "0") == "1")

    prices: dict[str, Price] = field(default_factory=lambda: {**DEFAULT_PRICES, **EMBEDDING_PRICES})

    def teacher_timeout(self) -> httpx.Timeout:
        """Timeouts for Token Factory calls (teacher, judge). A bad value raises ValueError, so live mode fails at startup."""
        return timeouts(self.teacher_timeout_s)

    def student_timeout(self) -> httpx.Timeout:
        """Timeouts for student calls (the vLLM endpoint, or the hosted fallback model)."""
        return timeouts(self.student_timeout_s, self.student_connect_timeout_s)

    def teacher_retry(self) -> RetryPolicy:
        """Retries for teacher and judge calls: RATCHET_TEACHER_RETRIES more attempts, within RATCHET_TEACHER_DEADLINE_S."""
        if isinstance(self.teacher_retries, bool) or not isinstance(self.teacher_retries, int) or self.teacher_retries < 0:
            raise ValueError(f"RATCHET_TEACHER_RETRIES must be 0 or more, not {self.teacher_retries!r}")
        if not self.teacher_deadline_s > 0:
            raise ValueError(f"RATCHET_TEACHER_DEADLINE_S must be more than 0, not {self.teacher_deadline_s!r}")
        return RetryPolicy(max_attempts=self.teacher_retries + 1, deadline_s=self.teacher_deadline_s)

    def student_breaker(self) -> BreakerPolicy:
        """The student breakers' policy. A bad value raises ValueError, so the gateway fails at startup. The longest wait
        is 300 s, or the cooldown itself when that is longer."""
        return _breaker_policy("STUDENT", self.student_breaker_failures, self.student_breaker_cooldown_s)

    def teacher_breaker(self) -> BreakerPolicy:
        """The teacher breakers' policy (the primary teacher's and the fallback teacher's), as for the student."""
        return _breaker_policy("TEACHER", self.teacher_breaker_failures, self.teacher_breaker_cooldown_s)

    def serve_student_on_teacher_failure(self) -> bool:
        """RATCHET_ON_TEACHER_FAILURE: True for `serve_student`, False for `error` (or empty). Anything else raises
        ValueError, so a typo stops the gateway at startup instead of quietly failing requests the operator meant to
        serve."""
        mode = self.on_teacher_failure.strip().lower() if isinstance(self.on_teacher_failure, str) else None
        if mode == "":  # set but empty, as in an .env file: the default
            mode = "error"
        if mode not in TEACHER_FAILURE_MODES:
            choices = " or ".join(TEACHER_FAILURE_MODES)
            raise ValueError(f"RATCHET_ON_TEACHER_FAILURE must be {choices}, not {self.on_teacher_failure!r}")
        return mode == "serve_student"

    def unknown_task_policy(self) -> str:
        """RATCHET_UNKNOWN_TASKS: `distill` (the default, also when set but empty), `teacher` or `reject`. Anything else
        raises ValueError, so a typo stops the gateway at startup instead of distilling tasks the operator meant to keep
        on the teacher."""
        mode = self.unknown_tasks.strip().lower() if isinstance(self.unknown_tasks, str) else None
        if mode == "":
            mode = "distill"
        if mode not in UNKNOWN_TASK_POLICIES:
            raise ValueError(f"RATCHET_UNKNOWN_TASKS must be {', '.join(UNKNOWN_TASK_POLICIES)}, not {self.unknown_tasks!r}")
        return mode

    def redact_detector(self) -> Any:
        """RATCHET_REDACT_PLUGIN loaded: the detector function, or None when unset. A spec that is not `module:function`,
        a module that cannot be imported or a detector that fails on an empty text raises ValueError (ADR 0082), here and
        at startup alike."""
        from .redact import load_detector  # redact reads no settings; imported here to keep config's imports light

        plugin = self.redact_plugin
        if not isinstance(plugin, str):
            raise ValueError(f"RATCHET_REDACT_PLUGIN must be 'module:function', not {plugin!r}")
        return load_detector(plugin) if plugin.strip() else None

    def redaction_style(self) -> str:
        """RATCHET_REDACT_STYLE: `label` (the default, also when set but empty), `numbered` or `surrogate`. Case and spaces
        are ignored; anything else raises ValueError, so a typo stops startup instead of storing another style."""
        raw = self.redact_style
        style = raw.strip().lower() if isinstance(raw, str) else None
        if style == "":
            style = "label"
        if style not in REDACT_STYLES:
            raise ValueError(f"RATCHET_REDACT_STYLE must be {', '.join(REDACT_STYLES)}, not {raw!r}")
        return style

    def no_store_task_names(self) -> frozenset[str]:
        """RATCHET_NO_STORE_TASKS as a set of task names (FR-13.1). A name need not be registered (an unregistered name
        may still be used under RATCHET_UNKNOWN_TASKS=distill), but one that is not a task name raises ValueError, so a
        typo such as a space-separated list stops startup instead of storing what the operator meant to keep out."""
        raw = self.no_store_tasks if isinstance(self.no_store_tasks, str) else None
        if raw is None:
            raise ValueError(f"RATCHET_NO_STORE_TASKS must be comma-separated task names, not {self.no_store_tasks!r}")
        names = [n.strip() for n in raw.split(",") if n.strip()]
        for name in names:
            if not valid_task_name(name):
                raise ValueError(
                    f"RATCHET_NO_STORE_TASKS: {name!r} is not a task name (1 to 64 letters, digits, '_', '.' or '-'; "
                    "separate names with commas)"
                )
        return frozenset(names)

    def retention(self) -> Retention:
        """RATCHET_EXAMPLE_RETENTION_DAYS, RATCHET_REQUEST_RETENTION_DAYS and RATCHET_ERASURE_SHARED, checked. A bad
        value raises ValueError naming the variable, so the gateway and `python -m ratchet.live` stop at startup instead
        of keeping data longer, or deleting more, than the operator meant."""
        for env, days in (
            ("RATCHET_EXAMPLE_RETENTION_DAYS", self.example_retention_days),
            ("RATCHET_REQUEST_RETENTION_DAYS", self.request_retention_days),
        ):
            if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_RETENTION_DAYS:
                raise ValueError(f"{env} must be a whole number of days, 0 (keep forever) to {MAX_RETENTION_DAYS}, not {days!r}")
        raw = self.erasure_shared
        shared = raw.strip().lower() if isinstance(raw, str) else None
        if shared == "":  # set but empty, as in an .env file: the default
            shared = "delete"
        if shared not in ERASURE_SHARED:
            raise ValueError(f"RATCHET_ERASURE_SHARED must be {' or '.join(ERASURE_SHARED)}, not {raw!r}")
        return Retention(self.example_retention_days, self.request_retention_days, shared)

    def model_routing(self) -> ModelRouting:
        """RATCHET_MODEL_POLICY, RATCHET_MODEL_ALIASES and RATCHET_AUTO_TASK, checked (ADR 0048): in any case, empty meaning
        the default (`lenient`, no aliases, `off`). Anything else raises ValueError naming the variable, so a typo stops
        the gateway at startup instead of routing requests to a task nobody chose."""
        policy = _choice(self.model_policy, "RATCHET_MODEL_POLICY", MODEL_POLICIES)
        auto = _choice(self.auto_task, "RATCHET_AUTO_TASK", AUTO_TASK_MODES)
        if not isinstance(self.model_aliases, str):
            raise ValueError(f"RATCHET_MODEL_ALIASES must be text, not {self.model_aliases!r}")
        return ModelRouting(policy, parse_aliases(self.model_aliases), auto)

    def auto_task_cap(self) -> int:
        """RATCHET_MAX_AUTO_TASKS, checked: a whole number from 0 (no auto task is ever made) to MAX_AUTO_TASKS_LIMIT,
        half the task registry, so request traffic cannot take the places of the file and API tasks. Anything else
        raises ValueError, so the gateway and the simulator stop at startup."""
        n = self.max_auto_tasks
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_AUTO_TASKS_LIMIT:
            raise ValueError(f"RATCHET_MAX_AUTO_TASKS must be a whole number from 0 to {MAX_AUTO_TASKS_LIMIT}, not {n!r}")
        return n

    def min_task_golden_rows(self) -> int:
        """RATCHET_MIN_TASK_GOLDEN, checked: a whole number, 1 or more. Anything else raises ValueError, so the gateway,
        the simulator and `python -m ratchet.live` stop at startup instead of calibrating tasks on a rule nobody chose."""
        n = self.min_task_golden
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"RATCHET_MIN_TASK_GOLDEN must be a whole number, 1 or more, not {n!r}")
        return n

    def limits(self) -> Limits:
        """The request limits `validate_chat_request` applies (RATCHET_MAX_MESSAGES, RATCHET_MAX_INPUT_CHARS,
        RATCHET_MAX_N). A bad RATCHET_MAX_N raises ValueError, so the gateway fails at startup."""
        return Limits(max_messages=self.max_messages, max_input_chars=self.max_input_chars, max_n=self.max_n)

    def idempotency(self) -> tuple[int, float]:
        """(RATCHET_IDEMPOTENCY_CACHE, RATCHET_IDEMPOTENCY_TTL_S), checked: up to MAX_IDEMPOTENCY_CACHE answers and up to
        MAX_RETRY_WINDOW_S seconds, 0 or more. Anything else raises ValueError naming the variable, so the gateway stops
        at startup."""
        n, ttl = self.idempotency_cache, self.idempotency_ttl_s
        if not _whole_in(n, 0, MAX_IDEMPOTENCY_CACHE):
            raise ValueError(f"RATCHET_IDEMPOTENCY_CACHE must be a whole number from 0 to {MAX_IDEMPOTENCY_CACHE}, not {n!r}")
        if not _number_in(ttl, 0.0, MAX_RETRY_WINDOW_S, False):
            raise ValueError(f"RATCHET_IDEMPOTENCY_TTL_S must be 0 to {MAX_RETRY_WINDOW_S:.0f} seconds, not {ttl!r}")
        return n, float(ttl)

    def seen_dedup(self) -> float:
        """RATCHET_SEEN_DEDUP_S, checked: 0 to MAX_RETRY_WINDOW_S seconds (unset: DEFAULT_SEEN_DEDUP_S in live mode, 0 in
        mock mode, where repeats are the simulator's and the demo's traffic, as RATCHET_TICK_INTERVAL_S is 0 there);
        ValueError naming the variable otherwise."""
        window = self.seen_dedup_s
        if window is None:
            return 0.0 if self.mode == "mock" else DEFAULT_SEEN_DEDUP_S
        if not _number_in(window, 0.0, MAX_RETRY_WINDOW_S, False):
            raise ValueError(f"RATCHET_SEEN_DEDUP_S must be 0 to {MAX_RETRY_WINDOW_S:.0f} seconds, not {window!r}")
        return float(window)

    def embeddings_endpoint(self) -> str | None:
        """The base URL the /v1/embeddings pass-through calls (FR-1.51): RATCHET_EMBEDDINGS_BASE, the teacher's base when
        it is empty, or None for 'off' (in any case; the endpoint then answers 404). A base that is not an http(s) URL,
        or an empty or Ratchet-named RATCHET_EMBEDDINGS_MODEL while the endpoint is on, raises ValueError, so the
        gateway stops at startup instead of failing every embeddings request."""
        raw = self.embeddings_base.strip() if isinstance(self.embeddings_base, str) else None
        if raw is None:
            raise ValueError(f"RATCHET_EMBEDDINGS_BASE must be a URL or 'off', not {self.embeddings_base!r}")
        if raw.lower() == EMBEDDINGS_OFF:
            return None
        base = raw or self.teacher_base
        if not base.lower().startswith(("http://", "https://")):
            raise ValueError(f"RATCHET_EMBEDDINGS_BASE must be an http(s) URL or 'off', not {base!r}")
        model = self.embeddings_model.strip() if isinstance(self.embeddings_model, str) else ""
        if not model or model != self.embeddings_model or not model.isprintable():
            raise ValueError(f"RATCHET_EMBEDDINGS_MODEL must be a model id (no spaces around it), not {self.embeddings_model!r}")
        if model == "ratchet" or model.startswith(("ratchet/", "ratchet:")):
            raise ValueError(f"RATCHET_EMBEDDINGS_MODEL must name the upstream model, not a Ratchet model id ({model!r})")
        return base

    def reasoning_policy(self) -> reasoning.ReasoningPolicy:
        """The thinking mode of each role and where Token Factory-bound calls carry it. A bad value raises ValueError
        naming its variable, so startup stops. An empty RATCHET_CHECK_REASONING follows RATCHET_TEACHER_REASONING."""
        serve = reasoning.parse(self.teacher_reasoning, "RATCHET_TEACHER_REASONING")
        return reasoning.ReasoningPolicy(
            student=reasoning.parse(self.student_reasoning, "RATCHET_STUDENT_REASONING", reasoning.STUDENT_MODES, "off"),
            fallback_student=reasoning.parse(
                self.fallback_student_reasoning, "RATCHET_FALLBACK_STUDENT_REASONING", reasoning.FALLBACK_STUDENT_MODES, "off"
            ),
            serve=serve,
            check=reasoning.parse(self.check_reasoning, "RATCHET_CHECK_REASONING", default=serve),
            field=reasoning.parse(
                self.teacher_reasoning_field, "RATCHET_TEACHER_REASONING_FIELD", reasoning.FIELDS, "chat_template_kwargs"
            ),
        )

    def gen_policy(self) -> GenPolicy:
        """What each role forwards of a client's generation parameters, and the token caps. A bad value raises
        ValueError, so the gateway fails at startup."""
        for env, value in (
            ("RATCHET_TEACHER_MAX_TOKENS", self.teacher_max_tokens),
            ("RATCHET_STUDENT_MAX_TOKENS", self.student_max_tokens),
            ("RATCHET_TEACHER_REASONING_HEADROOM", self.teacher_reasoning_headroom),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{env} must be a whole number of tokens, 0 or more, not {value!r}")
        mode = self.reasoning_policy()
        return GenPolicy(
            student_reasoning=mode.student,
            serve_reasoning=mode.serve,
            check_reasoning=mode.check,
            teacher_allow=parse_param_allowlist(self.teacher_params, DEFAULT_TEACHER_PARAMS, "RATCHET_TEACHER_PARAMS"),
            student_allow=parse_param_allowlist(self.student_params, DEFAULT_STUDENT_PARAMS, "RATCHET_STUDENT_PARAMS"),
            teacher_max_tokens=self.teacher_max_tokens,
            student_max_tokens=self.student_max_tokens,
            reasoning_headroom=self.teacher_reasoning_headroom,
        )

    def kpi_window(self) -> int:
        """RATCHET_KPI_WINDOW_DAYS, checked: a whole number of days from 1 to 3650 (`kpis.check_window_days`). A bad value
        raises ValueError naming the variable, so the gateway and the simulator stop at startup instead of reporting
        metrics over a window nobody asked for."""
        try:
            return check_window_days(self.kpi_window_days)
        except ValueError as e:
            raise ValueError(f"RATCHET_KPI_WINDOW_DAYS: {e}") from None

    def cost_policy(self) -> CostPolicy:
        """The cost settings, checked. A bad value raises ValueError naming the variable, so the gateway, the simulator,
        `python -m ratchet.live` and `python -m ratchet.cost` stop at startup instead of reporting a wrong bill."""
        raw = self.student_cost_model
        model = raw.strip().lower() if isinstance(raw, str) else None
        if model == "":  # set but empty, as in an .env file: the default
            model = "per_token"
        if model not in COST_MODELS:
            raise ValueError(f"RATCHET_STUDENT_COST_MODEL must be {' or '.join(COST_MODELS)}, not {raw!r}")
        for env, value in (
            ("RATCHET_STUDENT_GPU_USD_PER_HOUR", self.student_gpu_usd_per_hour),
            ("RATCHET_TRAIN_GPU_USD_PER_HOUR", self.train_gpu_usd_per_hour),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_USD_PER_GPU_HOUR:
                raise ValueError(f"{env} must be a price in USD per GPU-hour, 0 to {MAX_USD_PER_GPU_HOUR:,.0f}, not {value!r}")
        for env, value in (("RATCHET_STUDENT_GPUS", self.student_gpus), ("RATCHET_TRAIN_GPUS", self.train_gpus)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_GPUS:
                raise ValueError(f"{env} must be a whole number of GPUs, 0 to {MAX_GPUS:,}, not {value!r}")
        hours = self.mock_endpoint_hours
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not (0 <= hours <= 24):
            raise ValueError(f"RATCHET_MOCK_ENDPOINT_HOURS must be 0 to 24 hours a day, not {hours!r}")
        return CostPolicy(
            model=model,
            student=GpuRate(float(self.student_gpu_usd_per_hour), self.student_gpus),
            train=GpuRate(float(self.train_gpu_usd_per_hour), self.train_gpus),
            mock_endpoint_hours=float(hours),
            prices_verified=bool(self.prices_verified),
        )

    def checked_canary_audit_rate(self) -> float | None:
        """RATCHET_CANARY_AUDIT_RATE, checked: None (unset: a canary is audited at RATCHET_AUDIT_RATE), or more than 0 and
        at most 1. At 0 a canary would get no audits, so the release gate could never promote, roll back or retire it,
        and Night School would wait for it forever (INV-5). A bad value raises ValueError, so startup stops."""
        rate = self.canary_audit_rate
        if rate is None:
            return None
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
            raise ValueError(f"RATCHET_CANARY_AUDIT_RATE must be more than 0 and at most 1 (or unset), not {rate!r}")
        return float(rate)

    def cert_policy(self) -> CertPolicy:
        """RATCHET_CALIBRATION (`point`, the default also when set but empty, or `ltt`), RATCHET_CERT_TARGET and
        RATCHET_CERT_DELTA (each strictly between 0 and 1), checked. A bad value raises ValueError naming the variable,
        so the gateway, the simulator and the nightly job stop at startup instead of certifying against the wrong
        target."""
        raw = self.calibration
        mode = raw.strip().lower() if isinstance(raw, str) else None
        if mode == "":
            mode = "point"
        if mode not in CALIBRATIONS:
            raise ValueError(f"RATCHET_CALIBRATION must be {' or '.join(CALIBRATIONS)}, not {raw!r}")
        for env, value in (("RATCHET_CERT_TARGET", self.cert_target), ("RATCHET_CERT_DELTA", self.cert_delta)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
                raise ValueError(f"{env} must be a number strictly between 0 and 1, not {value!r}")
        return CertPolicy(mode, float(self.cert_target), float(self.cert_delta))

    def receipt_policy(self) -> ReceiptPolicy:
        """RATCHET_TAKE_RATE (0 to 1) and RATCHET_RECEIPT_MIN_AUDITS (a whole number, at least 2), checked. A bad value
        raises ValueError naming the variable, so the gateway, the simulator and `python -m ratchet.live` stop at startup
        instead of sealing receipts that bill the wrong share, which could never be corrected (INV-17)."""
        share = self.take_rate
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 <= share <= 1:
            raise ValueError(f"RATCHET_TAKE_RATE must be a share from 0 to 1, not {share!r}")
        n = self.receipt_min_audits
        if isinstance(n, bool) or not isinstance(n, int) or not MIN_RECEIPT_AUDITS <= n <= 1_000_000:
            raise ValueError(
                f"RATCHET_RECEIPT_MIN_AUDITS must be a whole number from {MIN_RECEIPT_AUDITS} to 1,000,000, not {n!r}"
            )
        return ReceiptPolicy(float(share), n)

    def checked_mock_reasoning_mean(self) -> float | None:
        """RATCHET_MOCK_TEACHER_REASONING_MEAN, checked: None (unset: the mock teacher bills the fixed
        RATCHET_TEACHER_REASONING_TOKENS) or more than 0 and at most MAX_MOCK_REASONING_TOKENS. Only the mock teacher
        reads it, but the gateway and `python -m ratchet.live` check it at startup in every mode, so a value out of range
        always stops startup as CONFIGURATION.md says."""
        mean = self.mock_teacher_reasoning_mean
        if mean is None:
            return None
        if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not 0 < mean <= MAX_MOCK_REASONING_TOKENS:
            limit = f"{MAX_MOCK_REASONING_TOKENS:,}"
            raise ValueError(f"RATCHET_MOCK_TEACHER_REASONING_MEAN must be more than 0 and at most {limit} tokens, not {mean!r}")
        return float(mean)

    def scout_policy(self) -> ScoutPolicy:
        """The judge access and Tavily credit settings, checked. A bad value raises ValueError naming the variable, so
        the gateway stops at startup; no message ever shows the judge code's value (ADR 0120)."""
        code = self.scout_judge_code if isinstance(self.scout_judge_code, str) else None
        lo, hi = JUDGE_CODE_CHARS
        if code is None or (code and not (lo <= len(code) <= hi and all(33 <= ord(ch) <= 126 for ch in code))):
            raise ValueError(
                f"RATCHET_SCOUT_JUDGE_CODE must be empty (off) or {lo} to {hi} printable ASCII characters with no spaces, "
                "for example from `openssl rand -hex 16`; its value is not shown"
            )
        max_runs = self.scout_max_runs_per_day
        if isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 0:
            raise ValueError(f"RATCHET_SCOUT_MAX_RUNS_PER_DAY must be a whole number, 0 or more, not {max_runs!r}")
        runs = self.scout_judge_runs_per_day
        if isinstance(runs, bool) or not isinstance(runs, int) or not 0 <= runs <= max_runs:
            raise ValueError(
                f"RATCHET_SCOUT_JUDGE_RUNS_PER_DAY must be a whole number from 0 to RATCHET_SCOUT_MAX_RUNS_PER_DAY "
                f"({max_runs}), not {runs!r}"
            )
        if bool(code) != (runs > 0):
            raise ValueError(
                "RATCHET_SCOUT_JUDGE_CODE and RATCHET_SCOUT_JUDGE_RUNS_PER_DAY go together: set both (a code and 1 or more "
                "runs) or neither"
            )
        enabled = bool(code)
        share = runs / max_runs if max_runs else 0.0
        budget = self.scout_daily_budget_usd
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 <= budget < math.inf:
            raise ValueError(f"RATCHET_SCOUT_DAILY_BUDGET_USD must be a finite amount, 0 or more, not {budget!r}")
        judge_usd = self.scout_judge_budget_usd
        if judge_usd is None:
            judge_usd = math.floor(budget * share * 100 + 1e-9) / 100  # rounded down to the cent
        elif isinstance(judge_usd, bool) or not isinstance(judge_usd, (int, float)) or not 0 <= judge_usd <= budget:
            raise ValueError(
                f"RATCHET_SCOUT_JUDGE_BUDGET_USD must be from 0 to RATCHET_SCOUT_DAILY_BUDGET_USD ({budget}), not {judge_usd!r}"
            )
        elif judge_usd > 0 and not enabled:
            raise ValueError("RATCHET_SCOUT_JUDGE_BUDGET_USD holds money back for judges, so it needs RATCHET_SCOUT_JUDGE_CODE")
        session = self.scout_judge_session_s
        slo, shi = JUDGE_SESSION_S
        if isinstance(session, bool) or not isinstance(session, (int, float)) or not slo <= session <= shi:
            raise ValueError(f"RATCHET_SCOUT_JUDGE_SESSION_S must be {slo:.0f} s to {shi:.0f} s (30 days), not {session!r}")
        monthly = self.tavily_monthly_credits
        if isinstance(monthly, bool) or not isinstance(monthly, int) or monthly < 0:
            raise ValueError(f"RATCHET_TAVILY_MONTHLY_CREDITS must be a whole number, 0 or more, not {monthly!r}")
        credits = self.tavily_judge_credits
        if credits is None:
            judge_credits = math.floor(monthly * share + 1e-9)
        else:
            if isinstance(credits, bool) or not isinstance(credits, (int, float)) or not math.isfinite(credits):
                raise ValueError(f"RATCHET_TAVILY_JUDGE_CREDITS must be a whole number, not {credits!r}")
            if credits != int(credits) or not 0 <= credits <= monthly:
                raise ValueError(
                    f"RATCHET_TAVILY_JUDGE_CREDITS must be a whole number from 0 to RATCHET_TAVILY_MONTHLY_CREDITS "
                    f"({monthly}), not {credits!r}"
                )
            if credits > 0 and not enabled:
                raise ValueError(
                    "RATCHET_TAVILY_JUDGE_CREDITS holds credits back for judges, so it needs RATCHET_SCOUT_JUDGE_CODE"
                )
            judge_credits = int(credits)
        day = self.tavily_cycle_day
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
            raise ValueError(f"RATCHET_TAVILY_CYCLE_DAY must be a day of the month from 1 to 28, not {day!r}")
        raw = self.tavily_pace_until
        pace: date | None = None
        if not isinstance(raw, str) or raw.strip():
            try:
                text = raw.strip() if isinstance(raw, str) else ""
                pace = date.fromisoformat(text) if _ISO_DATE.fullmatch(text) else None
            except ValueError:
                pace = None
            if pace is None:
                raise ValueError(f"RATCHET_TAVILY_PACE_UNTIL must be a date as YYYY-MM-DD (UTC), or empty, not {raw!r}")
        lease = self.gateway_lease.strip().lower() if isinstance(self.gateway_lease, str) else None
        if lease not in (*LEASE_MODES, ""):
            raise ValueError(f"RATCHET_GATEWAY_LEASE must be {', '.join(LEASE_MODES)}, not {self.gateway_lease!r}")
        return ScoutPolicy(
            judge_code=code,
            judge_runs_per_day=runs,
            judge_budget_usd=float(judge_usd),
            judge_session_s=float(session),
            monthly_credits=monthly,
            judge_credits=judge_credits,
            cycle_day=day,
            pace_until=pace,
        )

    def ledger_sync(self) -> str:
        """RATCHET_DB_SYNC: `NORMAL` (the default, also when set but empty) or `FULL`, in any case. Anything else raises
        ValueError, so a typo stops startup instead of picking a journal the operator did not ask for."""
        raw = self.db_sync
        mode = raw.strip().upper() if isinstance(raw, str) else None
        if mode == "":
            mode = "NORMAL"
        if mode not in SYNC_MODES:
            raise ValueError(f"RATCHET_DB_SYNC must be {' or '.join(SYNC_MODES)}, not {raw!r}")
        return mode

    def tick_interval(self) -> float:
        """RATCHET_TICK_INTERVAL_S: the least time between two release-gate ticks from the request path (unset: 5 s in
        live mode, 0 in mock mode, every request). A rollback can wait this long; the cron tick runs regardless."""
        return _seconds("RATCHET_TICK_INTERVAL_S", self.tick_interval_s, 0.0 if self.mode == "mock" else 5.0)

    def stats_ttl(self) -> float:
        """RATCHET_STATS_TTL_S: how long /ratchet/stats serves one computed payload (unset: 2 s in live mode, 0 in mock
        mode, a fresh payload per read)."""
        return _seconds("RATCHET_STATS_TTL_S", self.stats_ttl_s, 0.0 if self.mode == "mock" else 2.0)

    def sidecar(self) -> tuple[int, int, float]:
        """(RATCHET_SIDECAR_WORKERS, RATCHET_SIDECAR_QUEUE, RATCHET_SIDECAR_DRAIN_S), checked: 0 to MAX_SIDECAR_WORKERS
        threads (unset: 4 in live mode, 0 in mock mode), 1 to MAX_SIDECAR_QUEUE waiting jobs, 0 to MAX_INTERVAL_S seconds.
        Anything else raises ValueError naming the variable, so the gateway stops at startup."""
        workers = self.sidecar_workers
        if workers is None:
            workers = 0 if self.mode == "mock" else DEFAULT_SIDECAR_WORKERS
        if not _whole_in(workers, 0, MAX_SIDECAR_WORKERS):
            raise ValueError(f"RATCHET_SIDECAR_WORKERS must be a whole number from 0 to {MAX_SIDECAR_WORKERS}, not {workers!r}")
        if not _whole_in(self.sidecar_queue, 1, MAX_SIDECAR_QUEUE):
            raise ValueError(
                f"RATCHET_SIDECAR_QUEUE must be a whole number from 1 to {MAX_SIDECAR_QUEUE}, not {self.sidecar_queue!r}"
            )
        if not _number_in(self.sidecar_drain_s, 0.0, MAX_INTERVAL_S, False):
            raise ValueError(f"RATCHET_SIDECAR_DRAIN_S must be 0 to {MAX_INTERVAL_S:.0f} seconds, not {self.sidecar_drain_s!r}")
        return workers, self.sidecar_queue, float(self.sidecar_drain_s)

    def log_settings(self) -> tuple[str, str]:
        """(RATCHET_LOG_FORMAT, RATCHET_LOG_LEVEL), checked: `json` or `text` and DEBUG, INFO, WARNING or ERROR, in any
        case; unset (or set but empty) means text and WARNING in mock mode, json and INFO in live mode. Anything else raises
        ValueError naming the variable, so a typo stops startup instead of silencing the logs."""
        mock = self.mode == "mock"
        fmt = self.log_format.strip().lower() if isinstance(self.log_format, str) else None
        level = self.log_level.strip().upper() if isinstance(self.log_level, str) else None
        if fmt == "":
            fmt = "text" if mock else "json"
        if level == "":
            level = "WARNING" if mock else "INFO"
        if fmt not in LOG_FORMATS:
            raise ValueError(f"RATCHET_LOG_FORMAT must be {' or '.join(LOG_FORMATS)}, not {self.log_format!r}")
        if level not in LOG_LEVELS:
            raise ValueError(f"RATCHET_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, not {self.log_level!r}")
        return fmt, level

    def access(self) -> Access:
        """The access switches, checked and resolved by mode (ADR 0061): unset means public dashboard and /docs in mock
        mode, private in live mode. A value that is not 0, 1 or unset, or a RATCHET_CSP that cannot be a header value
        (anything but printable ASCII), raises ValueError naming the variable, so the gateway stops at startup."""
        mock = self.mode == "mock"
        switches: dict[str, bool | None] = {}
        for env, name in (
            ("RATCHET_PUBLIC_DASHBOARD", "public_dashboard"),
            ("RATCHET_PUBLIC_BOM", "public_bom"),
            ("RATCHET_SCRUB_STATS", "scrub_stats"),
            ("RATCHET_OPENAPI", "openapi"),
            ("RATCHET_ALLOW_UNAUTHENTICATED", "allow_unauthenticated"),
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{env} must be 0 or 1, not {value!r}")
            switches[name] = value
        csp = self.csp.strip() if isinstance(self.csp, str) else None
        if csp is None or any(not (" " <= ch <= "~") for ch in csp):
            raise ValueError(f"RATCHET_CSP must be one header value in printable ASCII, not {self.csp!r}")
        public_dashboard = switches["public_dashboard"]
        openapi = switches["openapi"]
        return Access(
            public_dashboard=mock if public_dashboard is None else public_dashboard,
            public_bom=bool(switches["public_bom"]),
            scrub_stats=switches["scrub_stats"],
            openapi=mock if openapi is None else openapi,
            csp=csp,
            allow_unauthenticated=bool(switches["allow_unauthenticated"]),
        )

    def limit_policy(self) -> LimitPolicy:
        """The spend and rate limits, checked (ADR 0063). A value out of range, an unknown RATCHET_BUDGET_MODE or a bad
        RATCHET_BUDGET_ALERT_PCT raises ValueError naming the variable, so the gateway stops at startup.
        RATCHET_KEY_LIMITS itself is parsed by the gateway, which knows the key names."""
        defaults = KeyLimits(
            daily_requests=self.api_key_daily_requests,
            daily_usd=self.api_key_daily_usd,
            monthly_usd=self.api_key_monthly_usd,
            rpm=self.api_key_rpm,
            concurrency=self.api_key_concurrency,
        ).check("RATCHET_API_KEY_*")
        budget = self.daily_budget_usd
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 <= budget <= MAX_USD:  # also NaN
            raise ValueError(f"RATCHET_DAILY_BUDGET_USD must be US dollars from 0 (off) to {MAX_USD}, not {budget!r}")
        raw = self.budget_mode
        mode = raw.strip().lower() if isinstance(raw, str) else None
        if mode == "":  # set but empty, as in an .env file: the default
            mode = "block"
        if mode not in BUDGET_MODES:
            raise ValueError(f"RATCHET_BUDGET_MODE must be {' or '.join(BUDGET_MODES)}, not {raw!r}")
        if not isinstance(self.budget_alert_pct, str):
            raise ValueError(f"RATCHET_BUDGET_ALERT_PCT must be text such as '50,80,100', not {self.budget_alert_pct!r}")
        ip_rpm = self.ip_rpm if self.ip_rpm is not None else (0 if self.mode == "mock" else LIVE_IP_RPM)
        if isinstance(ip_rpm, bool) or not isinstance(ip_rpm, int) or not 0 <= ip_rpm <= MAX_RPM:
            raise ValueError(f"RATCHET_IP_RPM must be a whole number from 0 (off) to {MAX_RPM}, not {ip_rpm!r}")
        inflight = self.max_inflight
        if isinstance(inflight, bool) or not isinstance(inflight, int) or not 0 <= inflight <= MAX_INFLIGHT:
            raise ValueError(f"RATCHET_MAX_INFLIGHT must be a whole number from 0 (off) to {MAX_INFLIGHT}, not {inflight!r}")
        if not isinstance(self.key_limits, str):
            raise ValueError(f"RATCHET_KEY_LIMITS must be text, not {self.key_limits!r}")
        return LimitPolicy(
            key_defaults=defaults,
            key_limits=self.key_limits,
            daily_budget_usd=float(budget),
            budget_mode=mode,
            alert_pct=parse_alert_pct(self.budget_alert_pct),
            ip_rpm=ip_rpm,
            max_inflight=inflight,
        )

    def guard_problem(self) -> str | None:
        """Why a live gateway without API keys may not answer anyone even with RATCHET_ALLOW_UNAUTHENTICATED=1: the edge
        guard it says it stands behind is not configured. `RATCHET_ALLOW_UNAUTHENTICATED=1` used to be enough, one typo or a
        forgotten variable from an open, paid endpoint. It now needs `RATCHET_API_GUARD=guarded` and a
        `RATCHET_ADMIN_TOKEN` of at least 32 characters (what the Caddyfile checks before it lets a request through), or
        API keys. None when the guard is configured. Never quotes the token."""
        guard = self.api_guard.strip().lower() if isinstance(self.api_guard, str) else ""
        token = self.admin_token.strip() if isinstance(self.admin_token, str) else ""
        if guard == "guarded" and len(token) >= GUARD_TOKEN_CHARS:
            return None
        return (
            "RATCHET_ALLOW_UNAUTHENTICATED=1 needs the edge guard it relies on: set RATCHET_API_GUARD=guarded and a "
            f"RATCHET_ADMIN_TOKEN of at least {GUARD_TOKEN_CHARS} characters (openssl rand -hex 32), or set RATCHET_API_KEYS; "
            "otherwise /v1/* would answer anyone, on your teacher credits"
        )

    def teacher_watch_limits(self) -> tuple[int, int]:
        """(RATCHET_TEACHER_PROBES, RATCHET_ADJUDICATE_MAX), checked: whole numbers from 0 to MAX_TEACHER_WATCH_CALLS,
        because each probe is one teacher call and each adjudication two, paid every night. A bad value raises ValueError,
        so the gateway, the simulator and the nightly job stop at startup."""
        for env, value in (("RATCHET_TEACHER_PROBES", self.teacher_probes), ("RATCHET_ADJUDICATE_MAX", self.adjudicate_max)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TEACHER_WATCH_CALLS:
                raise ValueError(f"{env} must be a whole number from 0 to {MAX_TEACHER_WATCH_CALLS:,}, not {value!r}")
        return self.teacher_probes, self.adjudicate_max

    def check_reserved(self) -> None:
        """Stop startup when a reserved setting (RESERVED_SETTINGS) holds anything but its default: it is declared for a
        feature that is not built yet, so it would do nothing. ValueError names the variable and the feature. `Ratchet`
        and `python -m ratchet.live` call it, so the gateway, the simulator and the nightly job all refuse."""
        for name, (env, default, feature) in RESERVED_SETTINGS.items():
            value = getattr(self, name)
            if not _is_default(value, default):
                fix = "unset it" if default == "" else f"unset it or set it to {default}"
                raise ValueError(f"{env}={value!r} has no effect yet: it is reserved for {feature}, not built yet; {fix}")

    def price(self, model: str) -> Price:
        if model in self.prices:
            return self.prices[model]
        if model.startswith("student"):
            return self.prices["student"]
        return self.prices.get(self.teacher_model, Price(1.0, 3.0))

    def validate(self, environ: Mapping[str, str] | None = None) -> list[ConfigIssue]:
        """Every problem with these settings, errors and warnings, each naming its variable (FR-12.30, ADR 0070). Nothing
        is opened or called: the gateway's Runtime runs it before anything else, and `python -m ratchet.config check`
        prints it. `environ` (default os.environ) is where unknown, mock-only and loosely spelled variables are looked
        for. Variables that did not parse are `load()`'s to report; ReleasePolicy's rules are `ReleasePolicy.validate`'s
        (`check()` runs all three)."""
        env = os.environ if environ is None else environ
        issues: list[ConfigIssue] = []

        def error(var: str, message: str, license: bool = False) -> None:
            issues.append(ConfigIssue(ERROR, var, message, license))

        def warning(var: str, message: str) -> None:
            issues.append(ConfigIssue(WARNING, var, message))

        live = self.mode == "live"
        if self.mode not in MODES:
            error("RATCHET_MODE", f"RATCHET_MODE must be mock or live, not {self.mode!r}")
        for var, value, lo, hi, low_open in (
            ("RATCHET_AUDIT_RATE", self.audit_rate, 0.0, 1.0, False),
            ("RATCHET_CONFIDENCE_THRESHOLD", self.confidence_threshold, 0.0, 1.0, False),
            ("RATCHET_GOLDEN_FRACTION", self.golden_fraction, 0.0, MAX_GOLDEN_FRACTION, True),
        ):
            if not _number_in(value, lo, hi, low_open):
                span = f"more than {lo:g} and at most {hi:g}" if low_open else f"from {lo:g} to {hi:g}"
                error(var, f"{var} must be a number {span}, not {value!r}")
        if not _whole_in(self.samples, 1, MAX_SAMPLES):
            error("RATCHET_SAMPLES", f"RATCHET_SAMPLES must be a whole number from 1 to {MAX_SAMPLES}, not {self.samples!r}")
        for var, value in (
            ("RATCHET_API_KEY_DAILY_REQUESTS", self.api_key_daily_requests),
            ("RATCHET_MAX_BODY_BYTES", self.max_body_bytes),
            ("RATCHET_TEACHER_REASONING_TOKENS", self.teacher_reasoning_tokens),
            ("RATCHET_SCOUT_RUNS_PER_HOUR", self.scout_runs_per_hour),
            ("RATCHET_SCOUT_MAX_RUNS_PER_DAY", self.scout_max_runs_per_day),
            ("RATCHET_SCOUT_MAX_RESULTS", self.scout_max_results),
            ("RATCHET_MOCK_WARMUP_DAYS", self.mock_warmup_days),
            ("RATCHET_MOCK_WARMUP_REQUESTS", self.mock_warmup_requests),
        ):
            if not _whole_in(value, 0, None):
                error(var, f"{var} must be a whole number, 0 or more, not {value!r}")
        for var, number in (
            ("RATCHET_SCOUT_DAILY_BUDGET_USD", self.scout_daily_budget_usd),
            ("RATCHET_MOCK_PACE", self.mock_pace),
        ):
            if not _number_in(number, 0.0, math.inf, False):
                error(var, f"{var} must be a number, 0 or more, not {number!r}")
        for var, url, required in (
            ("RATCHET_TEACHER_BASE", self.teacher_base, True),
            ("RATCHET_STUDENT_BASE", self.student_base, False),
            ("RATCHET_TEACHER_FALLBACK_BASE", self.teacher_fallback_base, False),
        ):
            problem = _url_problem(url, required)
            if problem:
                error(var, f"{var} {problem}")
        # The checks each setting's own reader makes, collected instead of raised at the first. `var` is put in front
        # of a message that names no variable (the timeouts' own messages do not). A reader that checks several
        # variables stops at its first problem, so `_every_problem` runs it again past each one.
        readers: tuple[tuple[str, Callable[[Settings], object]], ...] = (
            ("RATCHET_TEACHER_TIMEOUT_S", Settings.teacher_timeout),
            ("RATCHET_STUDENT_TIMEOUT_S", lambda s: timeouts(s.student_timeout_s)),
            ("RATCHET_STUDENT_CONNECT_TIMEOUT_S", lambda s: timeouts(1.0, s.student_connect_timeout_s)),
            ("RATCHET_TEACHER_RETRIES", Settings.teacher_retry),
            ("RATCHET_STUDENT_BREAKER_FAILURES", Settings.student_breaker),
            ("RATCHET_TEACHER_BREAKER_FAILURES", Settings.teacher_breaker),
            ("RATCHET_ON_TEACHER_FAILURE", Settings.serve_student_on_teacher_failure),
            ("RATCHET_UNKNOWN_TASKS", Settings.unknown_task_policy),
            ("RATCHET_MAX_N", Settings.limits),
            ("RATCHET_IDEMPOTENCY_CACHE", Settings.idempotency),
            ("RATCHET_SEEN_DEDUP_S", Settings.seen_dedup),
            ("RATCHET_TEACHER_PARAMS", Settings.gen_policy),
            ("RATCHET_STUDENT_COST_MODEL", Settings.cost_policy),
            ("RATCHET_DB_SYNC", Settings.ledger_sync),
            ("RATCHET_TICK_INTERVAL_S", Settings.tick_interval),
            ("RATCHET_STATS_TTL_S", Settings.stats_ttl),
            ("RATCHET_SIDECAR_WORKERS", Settings.sidecar),
            ("RATCHET_LOG_FORMAT", Settings.log_settings),
            ("RATCHET_PUBLIC_DASHBOARD", Settings.access),
            ("RATCHET_BOM_SIGNING_KEY", _bom_signer),
            ("RATCHET_TEACHER_LICENSES", _teacher_licenses),
            ("RATCHET_NO_STORE_TASKS", Settings.no_store_task_names),
            ("RATCHET_EXAMPLE_RETENTION_DAYS", Settings.retention),
            ("RATCHET_END_USER_KEY", _end_user_key),
            ("RATCHET_CANARY_AUDIT_RATE", Settings.checked_canary_audit_rate),
            ("RATCHET_CALIBRATION", Settings.cert_policy),
            ("RATCHET_TAKE_RATE", Settings.receipt_policy),
            ("RATCHET_MOCK_TEACHER_REASONING_MEAN", Settings.checked_mock_reasoning_mean),
            ("RATCHET_SCOUT_JUDGE_CODE", Settings.scout_policy),
            ("RATCHET_API_KEY_DAILY_USD", Settings.limit_policy),
            ("RATCHET_MODEL_POLICY", Settings.model_routing),
            ("RATCHET_MAX_AUTO_TASKS", Settings.auto_task_cap),
            ("RATCHET_MIN_TASK_GOLDEN", Settings.min_task_golden_rows),
            ("RATCHET_TEACHER_PROBES", Settings.teacher_watch_limits),
            ("RATCHET_KPI_WINDOW_DAYS", Settings.kpi_window),
            ("RATCHET_REDACT_STYLE", Settings.redaction_style),
            ("RATCHET_REDACT_PLUGIN", Settings.redact_detector),
            ("RATCHET_EMBEDDINGS_BASE", Settings.embeddings_endpoint),
            ("RATCHET_MODEL_ALIASES", _embeddings_alias),
            ("", Settings.check_reserved),
        )
        for var, read in readers:
            issues.extend(_every_problem(self, var, read))
        # INV-1: every model that writes or judges answers must be distillable; a refused one stops startup.
        for var, model in (
            ("RATCHET_TEACHER_MODEL", self.teacher_model),
            ("RATCHET_JUDGE_MODEL", self.judge_model),
            ("RATCHET_TEACHER_FALLBACK_MODEL", self.teacher_fallback_model),
        ):
            if var == "RATCHET_TEACHER_FALLBACK_MODEL" and not model:
                continue
            try:
                licenses.require_distillable(model)
            except licenses.LicenseError as e:
                error(var, f"{var}: {e}", license=True)
        self._check_keys(error)
        if live:
            self._check_live(error, warning)
        self._check_prices(warning)
        _check_environ(env, live, warning)
        if not 600 <= self.watch_flag_max_age_s <= 604800:
            error("RATCHET_WATCH_FLAG_MAX_AGE_S", "RATCHET_WATCH_FLAG_MAX_AGE_S must be 600 to 604800 seconds")
        for problem in watch.config_issues(env):  # the host watchdog's settings (FR-11.32): a warning in mock mode
            (error if live else warning)("RATCHET_WATCH", problem)
        return issues

    def _check_keys(self, error: Callable[[str, str], None]) -> None:
        """The key rings and the metrics token, as the gateway reads them (`Runtime._set_access`), every problem listed:
        a bad spec, a short key, a key in two roles, and live mode without API keys."""
        rings: dict[str, KeyRing] = {}
        for var, spec in (
            ("RATCHET_API_KEYS", self.api_keys),
            ("RATCHET_ADMIN_KEYS", self.admin_keys),
            ("RATCHET_DASHBOARD_KEYS", self.dashboard_keys),
        ):
            try:
                rings[var] = KeyRing(spec if isinstance(spec, str) else "", env=var)
            except ValueError as e:
                error(var, str(e))
        if "RATCHET_API_KEYS" in rings:  # RATCHET_KEY_LIMITS names API keys (FR-13.1); a bad entry stops startup
            try:
                parse_key_limits(self.key_limits if isinstance(self.key_limits, str) else "", rings["RATCHET_API_KEYS"].names)
            except ValueError as e:
                error("RATCHET_KEY_LIMITS", str(e))
        for ring, other, fix in (
            ("RATCHET_ADMIN_KEYS", "RATCHET_API_KEYS", "an API key; give admins their own keys"),
            ("RATCHET_DASHBOARD_KEYS", "RATCHET_API_KEYS", "an API key; give dashboards their own keys"),
            ("RATCHET_DASHBOARD_KEYS", "RATCHET_ADMIN_KEYS", "an admin key; give dashboards their own, read-only keys"),
        ):
            if ring in rings and other in rings:
                both = rings[ring].shared(rings[other])
                if both:
                    error(ring, f"{ring}: the key for {both[0]!r} is also {fix}")
        token = self.metrics_token.strip() if isinstance(self.metrics_token, str) else ""
        try:
            Token(token, env="RATCHET_METRICS_TOKEN")
        except ValueError as e:
            error("RATCHET_METRICS_TOKEN", str(e))
        for var, ring in rings.items():
            name = ring.identify(token) if token else None
            if name is not None:
                error(
                    "RATCHET_METRICS_TOKEN",
                    f"RATCHET_METRICS_TOKEN is also the key for {name!r} in {var}; give the scraper its own token",
                )
        keyed = "RATCHET_API_KEYS" in rings and rings["RATCHET_API_KEYS"].enabled
        if self.mode == "live" and not keyed and "RATCHET_API_KEYS" in rings:
            if self.allow_unauthenticated is not True:
                error(
                    "RATCHET_API_KEYS",
                    "RATCHET_API_KEYS is empty: in live mode /v1/* would answer anyone, on your teacher credits. Set "
                    "RATCHET_API_KEYS, or RATCHET_ALLOW_UNAUTHENTICATED=1 when something in front of the gateway (the demo "
                    "VM's Caddy guard: RATCHET_API_GUARD=guarded and a RATCHET_ADMIN_TOKEN of 32+ characters) checks every "
                    "caller",
                )
            elif (problem := self.guard_problem()) is not None:
                error("RATCHET_ALLOW_UNAUTHENTICATED", problem)

    def _check_live(self, error: Callable[[str, str], None], warning: Callable[[str, str], None]) -> None:
        """What only live mode needs: a teacher key, a ledger file it can write, a student key, enough audits."""
        if not (self.teacher_api_key.strip() if isinstance(self.teacher_api_key, str) else ""):
            if _nebius(self.teacher_base):
                error(
                    "NEBIUS_API_KEY",
                    "NEBIUS_API_KEY is empty: in live mode Token Factory refuses every teacher call without it. Set it, "
                    "or run RATCHET_MODE=mock",
                )
            else:
                warning(
                    "NEBIUS_API_KEY",
                    "NEBIUS_API_KEY is empty: teacher calls carry no Authorization header (fine only for an endpoint "
                    "without authentication)",
                )
        problem = _db_problem(self.db_path)
        if problem:
            error("RATCHET_DB", f"RATCHET_DB={self.db_path!r}: {problem}, so the ledger cannot be written")
        if self.student_base and not (self.student_api_key or "").strip():
            warning(
                "RATCHET_STUDENT_KEY",
                "RATCHET_STUDENT_KEY is empty while RATCHET_STUDENT_BASE is set: student calls carry no token (fine only "
                "for an endpoint without authentication)",
            )
        rate = self.audit_rate
        if _number_in(rate, 0.0, 1.0, False) and rate < MIN_USEFUL_AUDIT_RATE:
            warning(
                "RATCHET_AUDIT_RATE",
                f"RATCHET_AUDIT_RATE={rate:g} audits under 1% of student answers: a canary may never collect "
                "RATCHET_MIN_AUDITS audits, so no release would be promoted or rolled back on evidence",
            )

    def _check_prices(self, warning: Callable[[str, str], None]) -> None:
        """A model without a price is priced as another, silently: say so (INV-9: prices are checked on day 1)."""
        models = [
            ("RATCHET_TEACHER_MODEL", self.teacher_model),
            ("RATCHET_JUDGE_MODEL", self.judge_model),
            ("RATCHET_TEACHER_FALLBACK_MODEL", self.teacher_fallback_model),
        ]
        if self.mode == "live" and not self.student_base:
            models.append(("RATCHET_FALLBACK_STUDENT", self.fallback_student_model))
        for var, model in models:
            if model and model not in self.prices:
                p = self.price(model)
                warning(
                    var,
                    f"{var}={model!r} has no price in config.DEFAULT_PRICES: its calls are costed at ${p.input:g} in and "
                    f"${p.output:g} out per 1M tokens instead; add its price",
                )


_FIELD_NAMES = frozenset(f.name for f in fields(Settings))


def defaults() -> Settings:
    """The settings as if no variable were set."""
    token = _DEFAULTS_ONLY.set(True)
    try:
        return Settings()
    finally:
        _DEFAULTS_ONLY.reset(token)


MAX_READER_PROBLEMS = 32  # `_every_problem` stops after this many from one reader


def _every_problem(settings: Settings, var: str, read: Callable[[Settings], object]) -> list[ConfigIssue]:
    """Every problem `read` finds in `settings`, though it raises at its first: after each, the setting of the variable
    the problem names (RATCHET_<FIELD>) goes back to its default and `read` runs again, until it passes, or names a
    variable that is no setting or already holds its default."""
    found: list[ConfigIssue] = []
    base: Settings | None = None
    while len(found) < MAX_READER_PROBLEMS:
        try:
            read(settings)
            break
        except ValueError as e:
            new = issues_from(e, var)
        found.extend(new)
        name = new[0].var.removeprefix("RATCHET_").lower()
        base = base or defaults()
        if name not in _FIELD_NAMES or getattr(settings, name) == getattr(base, name):
            break
        settings = replace(settings, **{name: getattr(base, name)})
    return found


def _teacher_licenses(settings: Settings) -> object:
    """RATCHET_TEACHER_LICENSES read as startup reads it (`licenses.load_file`, without installing it): a file that is
    missing, not JSON or YAML, or holds a closed model, a router alias or a license outside the allowlist raises
    ValueError (ADR 0090)."""
    path = settings.teacher_licenses_file.strip() if isinstance(settings.teacher_licenses_file, str) else ""
    return licenses.load_file(path) if path else None


def _embeddings_alias(settings: Settings) -> object:
    """The gateway's check of the two settings together (`Runtime._embedder`, ADR 0050): with /v1/embeddings on, a model
    alias spelled like RATCHET_EMBEDDINGS_MODEL (one id, two meanings in /v1/models) raises ValueError."""
    if settings.embeddings_endpoint() is not None and settings.embeddings_model in settings.model_routing().aliases:
        model = settings.embeddings_model
        raise ValueError(f"RATCHET_MODEL_ALIASES: {model!r} is RATCHET_EMBEDDINGS_MODEL; give the alias another id")
    return None


def _end_user_key(settings: Settings) -> object:
    """RATCHET_END_USER_KEY: empty (the ledger keeps a generated key) or at least `privacy.MIN_KEY_CHARS` characters,
    as `privacy.EndUsers` reads it (FR-13.2). The value is never quoted."""
    key = settings.end_user_key
    if not isinstance(key, str):
        raise ValueError("RATCHET_END_USER_KEY must be a string")
    from . import privacy  # imports the ledger, which the settings themselves do not need

    if key and len(key) < privacy.MIN_KEY_CHARS:
        raise ValueError(f"RATCHET_END_USER_KEY must be at least {privacy.MIN_KEY_CHARS} characters")
    return None


def _bom_signer(settings: Settings) -> object:
    """RATCHET_BOM_SIGNING_KEY read as the gateway reads it (`bom.Signer`, when the key is set): bad base64, a seed that
    is not 32 bytes, or a missing `cryptography` package raises ValueError naming the variable."""
    if not settings.bom_signing_key:
        return None
    from . import bom  # imports the ledger, which the settings themselves do not need

    try:
        return bom.Signer(settings.bom_signing_key)
    except RuntimeError as e:  # `cryptography` is missing
        raise ValueError(str(e)) from e


def _number_in(value: object, lo: float, hi: float, low_open: bool) -> bool:
    """A finite real number (not a bool) from `lo` (excluded when `low_open`) to `hi`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return False
    return (lo < value if low_open else lo <= value) and value <= hi and math.isfinite(value)


def _whole_in(value: object, lo: int, hi: int | None) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= lo and (hi is None or value <= hi)


def _url_problem(url: object, required: bool) -> str | None:
    """Why `url` is not an http(s) base URL, or None. The message never repeats the URL, which may hold a password."""
    if not isinstance(url, str):
        return f"must be an http(s) URL, not {type(url).__name__}"
    if not url.strip():
        return "is empty; it needs an http(s) base URL" if required else None
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        shown = f" (it starts with {parts.scheme!r})" if parts.scheme else ""
        return f"must start with http:// or https://{shown}"
    if not parts.netloc:
        return "must name a host after http(s)://"
    return None


def _nebius(url: object) -> bool:
    """Whether a base URL is on Nebius (Token Factory), which needs a key for every call."""
    host = (urlsplit(url.strip()).hostname or "") if isinstance(url, str) else ""
    return host == "nebius.com" or host.endswith(".nebius.com")


def _db_problem(path: object) -> str | None:
    """Why the ledger file cannot be written, or None. An in-memory ledger has nothing to check. Nothing is created."""
    if not isinstance(path, str) or not path.strip():
        return "is empty"
    if path == ":memory:" or path.startswith("file:"):
        return None
    p = Path(path)
    if p.is_dir():
        return "is a directory"
    if p.exists() and not os.access(p, os.R_OK | os.W_OK):
        return "is not writable"
    parent = p.parent
    if not parent.is_dir():
        return "its directory does not exist"
    if not os.access(parent, os.W_OK | os.X_OK):  # the journal (-wal, -shm or -journal) is written beside it
        return "its directory is not writable"
    return None


def _check_environ(env: Mapping[str, str], live: bool, warning: Callable[[str, str], None]) -> None:
    """Variables that do nothing as set: unknown RATCHET_* names (with the closest known name), mock-only ones in live
    mode, and 0/1 switches spelled otherwise. Messages name variables, never their values."""
    known = known_env()
    names = sorted(n for n in known if n.startswith("RATCHET_"))
    for name in sorted(env):
        if name.startswith("RATCHET_") and name not in known:
            close = difflib.get_close_matches(name, names, n=1, cutoff=0.8)
            hint = f"; did you mean {close[0]}?" if close else ""
            warning(name, f"{name} is set but Ratchet does not read it{hint}")
    if live:
        for name, default in MOCK_ONLY_ENV.items():
            raw = env.get(name, "").strip()
            if raw and not _same_number(raw, default):
                warning(name, f"{name} is set but only mock mode reads it; live mode ignores it")
    for name, on_unless_zero in LOOSE_SWITCHES.items():
        raw = env.get(name, "").strip()
        if raw and raw not in ("0", "1"):
            reads = "1 (on)" if on_unless_zero else "0 (off)"
            warning(name, f"{name} takes 0 or 1; this value reads as {reads}")


def _same_number(raw: str, default: str) -> bool:
    if raw == default:
        return True
    try:
        return float(raw) == float(default)
    except ValueError:
        return False


def issues_from(e: Exception, var: str = "") -> list[ConfigIssue]:
    """The ConfigIssues for an error a setting's reader raised: a ConfigError's own, else one with the message and the
    variable it names (or `var`, put in front when the message names none)."""
    if isinstance(e, ConfigError):
        return list(e.issues)
    message = str(e)
    found = _ENV_NAME.search(message)
    if found is None and var:
        message = f"{var}: {message}"
    return [ConfigIssue(ERROR, found.group(0) if found else var, message, isinstance(e, licenses.LicenseError))]


def load() -> tuple[Settings, list[ConfigIssue]]:
    """Settings from the environment, with every variable that does not parse as an issue instead of an exception (its
    setting keeps the default meanwhile), so one startup check lists them all."""
    sink: list[ConfigIssue] = []
    token = _COLLECT.set(sink)
    try:
        settings = Settings()
    finally:
        _COLLECT.reset(token)
    return settings, sink


def check(settings: Settings | None = None, environ: Mapping[str, str] | None = None) -> list[ConfigIssue]:
    """Every problem the gateway's startup finds, errors first (FR-12.30): variables that do not parse (when `settings`
    is None and the environment is read here), `Settings.validate`, and the release policy (`ReleasePolicy.from_env`
    and `ReleasePolicy.validate`), which both modes build their gate from (mock mode with `min_audits` 15 unless set)."""
    issues: list[ConfigIssue] = []
    if settings is None:
        settings, issues = load()
    issues.extend(settings.validate(environ))
    from .release import ReleasePolicy  # release imports config

    issues.extend(ReleasePolicy.env_issues(environ))
    return sorted(issues, key=lambda i: i.level != ERROR)


def tasks_file_issues(settings: Settings) -> list[ConfigIssue]:
    """A RATCHET_TASKS_FILE that would stop startup, as an error listing every spec error (`taskspec.load_file`, which
    registers nothing). The gateway reports a bad file itself, as a SpecError, when it builds the registry; the CLI asks
    here so `python -m ratchet.config check` catches it before a deploy."""
    path = settings.tasks_file.strip() if isinstance(settings.tasks_file, str) else ""
    if not path:
        return []
    from . import taskspec  # imports the task modules, which the settings themselves do not need

    try:
        ring = KeyRing(settings.api_keys if isinstance(settings.api_keys, str) else "")
        known = ring.names if ring.enabled else None  # a task's `clients` must name API keys, as the gateway checks
    except ValueError:  # a bad key spec is `validate`'s to report
        known = None
    try:
        taskspec.load_file(path, known)
    except taskspec.SpecError as e:
        return [ConfigIssue(ERROR, "RATCHET_TASKS_FILE", str(e))]
    return []


EXIT_CLEAN, EXIT_WARNINGS, EXIT_ERRORS = 0, 1, 2


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """`python -m ratchet.config check [--json]`: the gateway's startup check, without starting anything, plus the tasks
    file (`tasks_file_issues`). Exit 0 when clean, 1 with warnings only, 2 with errors (or warnings under
    RATCHET_STRICT_CONFIG=1, which would stop startup)."""
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m ratchet.config", description="Check Ratchet's settings.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("check", help="list every configuration error and warning; exit 0, 1 (warnings) or 2 (errors)")
    cmd.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    settings, parsed = load()
    issues = sorted([*parsed, *check(settings), *tasks_file_issues(settings)], key=lambda i: i.level != ERROR)
    strict = settings.strict_config is True
    errors = sum(i.level == ERROR for i in issues)
    warnings = len(issues) - errors
    code = EXIT_ERRORS if errors or (strict and warnings) else EXIT_WARNINGS if warnings else EXIT_CLEAN
    if args.json:
        doc = {"mode": settings.mode, "strict": strict, "exit": code, "issues": [i.as_dict() for i in issues]}
        print(json.dumps(doc, indent=2), file=out)
        return code
    for i in issues:
        print(f"{i.level:<8} {i.message}", file=out)
    if code == EXIT_CLEAN:
        print(f"ok: no configuration problems (mode {settings.mode})", file=out)
    else:
        fatal = "the gateway would not start" if code == EXIT_ERRORS else "the gateway would start"
        strictly = " (RATCHET_STRICT_CONFIG=1 makes warnings fatal)" if strict and warnings and not errors else ""
        print(f"{_count(errors, 'error')}, {_count(warnings, 'warning')}: {fatal}{strictly}", file=out)
    return code


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


if __name__ == "__main__":  # pragma: no cover - `python -m ratchet.config`: one copy of the module, ratchet.config
    from ratchet.config import main as _main

    raise SystemExit(_main(sys.argv[1:]))
