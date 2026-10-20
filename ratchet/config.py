"""Runtime settings, read from environment variables.

Model IDs follow the Nebius Token Factory catalog as of Oct 2026. Confirm them on
day 1 with `GET https://api.tokenfactory.nebius.com/v1/models?verbose=true`.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import httpx

from .openai_compat import DEFAULT_MAX_N, Limits
from .providers import (
    DEFAULT_STUDENT_MAX_TOKENS,
    DEFAULT_STUDENT_PARAMS,
    DEFAULT_TEACHER_MAX_TOKENS,
    DEFAULT_TEACHER_PARAMS,
    GenPolicy,
    parse_param_allowlist,
)
from .resilience import MAX_OPEN_S, BreakerPolicy, RetryPolicy, timeouts


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _float(name: str, default: str) -> float:
    """A number from the environment; ValueError names the variable, so a typo stops startup with a message that says
    which setting is wrong."""
    raw = _env(name, default)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, not {raw!r}") from None


def _int(name: str, default: str) -> int:
    """A whole number from the environment; ValueError names the variable."""
    raw = _env(name, default)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {raw!r}") from None


def _float_or_none(name: str) -> float | None:
    """A number from the environment, or None when the variable is unset or empty; ValueError names the variable."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, not {raw!r}") from None


# Settings declared for the evidence features (FR-15.5, FR-16.x; ADR 0024) before the items that read them are built:
# field name -> (variable, its default as the environment spells it, the feature that will read it). Their defaults keep
# today's behaviour. Until a feature lands, `Settings.check_reserved()` stops startup on any other value, so nobody
# relies on a setting that does nothing yet; the item that builds the feature removes its entries here (and adds its own
# checks). docs/CONFIGURATION.md lists them under "Reserved settings".
RESERVED_SETTINGS: dict[str, tuple[str, str, str]] = {
    "canary_audit_rate": ("RATCHET_CANARY_AUDIT_RATE", "", "the anytime-valid release gate (FR-16.1)"),
    "calibration": ("RATCHET_CALIBRATION", "point", "certified precision (FR-16.3)"),
    "cert_target": ("RATCHET_CERT_TARGET", "0.95", "certified precision (FR-16.3)"),
    "cert_delta": ("RATCHET_CERT_DELTA", "0.05", "certified precision (FR-16.3)"),
    "teacher_probes": ("RATCHET_TEACHER_PROBES", "20", "the teacher watch (FR-16.7)"),
    "adjudicate_max": ("RATCHET_ADJUDICATE_MAX", "50", "the teacher watch (FR-16.7)"),
    "take_rate": ("RATCHET_TAKE_RATE", "0.25", "verified savings receipts (FR-15.5)"),
    "receipt_min_audits": ("RATCHET_RECEIPT_MIN_AUDITS", "30", "verified savings receipts (FR-15.5)"),
    "mock_teacher_reasoning_mean": ("RATCHET_MOCK_TEACHER_REASONING_MEAN", "", "verified savings receipts (FR-15.5)"),
    "batch_api": ("RATCHET_BATCH_API", "operations", "clean-room backfill (FR-16.8)"),
    "batch_discount": ("RATCHET_BATCH_DISCOUNT", "0.5", "clean-room backfill (FR-16.8)"),
    "backfill_max_tokens": ("RATCHET_BACKFILL_MAX_TOKENS", "2048", "clean-room backfill (FR-16.8)"),
    "db_sync": ("RATCHET_DB_SYNC", "FULL", "live readiness (WAL journal)"),
}
# Variables read through a helper the traceability check cannot see (scripts/reqtrace.py: every one needs a row in
# docs/CONFIGURATION.md).
KNOWN_ENV: tuple[str, ...] = tuple(env for env, _, _ in RESERVED_SETTINGS.values())


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

    # Student on a Nebius Serverless Endpoint running vLLM with LoRA adapters.
    student_base: str = field(default_factory=lambda: _env("RATCHET_STUDENT_BASE", ""))
    student_api_key: str = field(default_factory=lambda: _env("RATCHET_STUDENT_KEY", ""))
    student_base_model: str = field(
        default_factory=lambda: _env("RATCHET_STUDENT_BASE_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    )

    # Cascade.
    confidence_threshold: float = field(default_factory=lambda: float(_env("RATCHET_CONFIDENCE_THRESHOLD", "0.80")))
    samples: int = field(default_factory=lambda: int(_env("RATCHET_SAMPLES", "3")))
    audit_rate: float = field(default_factory=lambda: float(_env("RATCHET_AUDIT_RATE", "0.05")))
    golden_fraction: float = field(default_factory=lambda: float(_env("RATCHET_GOLDEN_FRACTION", "0.10")))
    # Calibration rows (the even half of a task's golden rows) a task needs before its own threshold may go below the
    # version's global one (FR-14.1, ADR 0040); with fewer it gets the higher of the two.
    min_task_golden: int = field(default_factory=lambda: _int("RATCHET_MIN_TASK_GOLDEN", "20"))

    # Upstream timeouts and retries (ratchet/resilience.py, ADR 0014). The read timeout is how long a model may take to
    # answer; connect, write and pool timeouts are 5, 10 and 5 s (the student's connect timeout is its own). A failed
    # teacher call (the judge's too) is retried up to RATCHET_TEACHER_RETRIES times, within RATCHET_TEACHER_DEADLINE_S
    # seconds in all. A student call on the request path is never retried: the teacher is its retry.
    teacher_timeout_s: float = field(default_factory=lambda: float(_env("RATCHET_TEACHER_TIMEOUT_S", "120")))
    teacher_retries: int = field(default_factory=lambda: int(_env("RATCHET_TEACHER_RETRIES", "2")))
    teacher_deadline_s: float = field(default_factory=lambda: float(_env("RATCHET_TEACHER_DEADLINE_S", "180")))
    student_timeout_s: float = field(default_factory=lambda: float(_env("RATCHET_STUDENT_TIMEOUT_S", "15")))
    student_connect_timeout_s: float = field(default_factory=lambda: float(_env("RATCHET_STUDENT_CONNECT_TIMEOUT_S", "2")))
    # Student circuit breaker (ADR 0015). After RATCHET_STUDENT_BREAKER_FAILURES endpoint failures in a row (timeouts,
    # connection errors, 5xx, 408, 429) the student is skipped for RATCHET_STUDENT_BREAKER_COOLDOWN_S seconds and the
    # teacher answers at once; then one probe decides, and each failed probe doubles the wait up to 300 s. 0 = no breaker.
    student_breaker_failures: int = field(default_factory=lambda: int(_env("RATCHET_STUDENT_BREAKER_FAILURES", "5")))
    student_breaker_cooldown_s: float = field(default_factory=lambda: float(_env("RATCHET_STUDENT_BREAKER_COOLDOWN_S", "30")))

    # When the teacher is down (ADR 0019). Its breaker opens after RATCHET_TEACHER_BREAKER_FAILURES failed calls in a row
    # (each after its retries), and requests that need its answer then get a 503 at once instead of waiting out the
    # deadline; the same waits as the student's breaker. 0 = no breaker.
    teacher_breaker_failures: int = field(default_factory=lambda: int(_env("RATCHET_TEACHER_BREAKER_FAILURES", "5")))
    teacher_breaker_cooldown_s: float = field(default_factory=lambda: float(_env("RATCHET_TEACHER_BREAKER_COOLDOWN_S", "30")))
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
    teacher_reasoning_tokens: int = field(default_factory=lambda: int(_env("RATCHET_TEACHER_REASONING_TOKENS", "300")))

    # Generation parameters (ADR 0017). Which of a client's request fields each role forwards (comma-separated; empty =
    # the default list, 'none' = nothing, 'all' = every standard field), and the token caps (0 = no cap). The teacher
    # serving an answer gets the client's max_tokens plus the headroom, for reasoning it writes before the answer.
    teacher_params: str = field(default_factory=lambda: _env("RATCHET_TEACHER_PARAMS", ""))
    student_params: str = field(default_factory=lambda: _env("RATCHET_STUDENT_PARAMS", ""))
    teacher_max_tokens: int = field(
        default_factory=lambda: int(_env("RATCHET_TEACHER_MAX_TOKENS", str(DEFAULT_TEACHER_MAX_TOKENS)))
    )
    student_max_tokens: int = field(
        default_factory=lambda: int(_env("RATCHET_STUDENT_MAX_TOKENS", str(DEFAULT_STUDENT_MAX_TOKENS)))
    )
    teacher_reasoning_headroom: int = field(default_factory=lambda: int(_env("RATCHET_TEACHER_REASONING_HEADROOM", "0")))

    # Scout, the multi-agent research demo (docs/SCOUT.md).
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY", ""))
    scout_daily_budget_usd: float = field(default_factory=lambda: float(_env("RATCHET_SCOUT_DAILY_BUDGET_USD", "3.00")))
    scout_runs_per_hour: int = field(default_factory=lambda: int(_env("RATCHET_SCOUT_RUNS_PER_HOUR", "6")))
    scout_max_runs_per_day: int = field(default_factory=lambda: int(_env("RATCHET_SCOUT_MAX_RUNS_PER_DAY", "30")))
    scout_max_results: int = field(default_factory=lambda: int(_env("RATCHET_SCOUT_MAX_RESULTS", "4")))
    # Behind a reverse proxy (Caddy on the demo VM), take the client IP from X-Forwarded-For.
    trust_proxy: bool = field(default_factory=lambda: _env("RATCHET_TRUST_PROXY", "0") == "1")

    # Privacy: replace emails, phone numbers, card numbers etc. with placeholders before a
    # request and the teacher's answer are stored as a training example (ratchet/redact.py).
    redact_pii: bool = field(default_factory=lambda: _env("RATCHET_REDACT_PII", "1") != "0")

    # Gateway API keys for /v1/*: "name:key,name:key". Empty = no authentication (v0.3 behaviour).
    api_keys: str = field(default_factory=lambda: _env("RATCHET_API_KEYS", ""))
    # Requests per key per UTC day on /v1/chat/completions. 0 = unlimited.
    api_key_daily_requests: int = field(default_factory=lambda: int(_env("RATCHET_API_KEY_DAILY_REQUESTS", "0")))
    # Admin keys for management writes (PUT and DELETE /ratchet/tasks/...): "name:key,name:key", like RATCHET_API_KEYS
    # and never the same keys. Empty: the admin API is off in live mode and open in mock mode (ADR 0022). Not the Caddy
    # edge guard's RATCHET_ADMIN_TOKEN.
    admin_keys: str = field(default_factory=lambda: _env("RATCHET_ADMIN_KEYS", ""))

    # Tasks (ratchet/taskspec.py, ADR 0022). A JSON (or YAML, with PyYAML) file of task specs registered at startup; a bad
    # file stops startup with every error listed. Empty = the built-in tasks and the ones the admin API stored.
    tasks_file: str = field(default_factory=lambda: _env("RATCHET_TASKS_FILE", ""))
    # What a request naming an unregistered task gets: UNKNOWN_TASK_POLICIES.
    unknown_tasks: str = field(default_factory=lambda: _env("RATCHET_UNKNOWN_TASKS", "distill"))
    # Where fixture files that a task spec names must live (`taskspec.resolve_fixture`), so a spec cannot read
    # any other file on the host.
    fixtures_dir: str = field(default_factory=lambda: _env("RATCHET_FIXTURES_DIR", "fixtures"))

    # Request limits on the OpenAI-compatible API (ratchet/openai_compat.py), checked before any model call. 0 = no limit.
    # The body limit applies to every POST, PUT and PATCH; the other two to /v1/chat/completions.
    max_body_bytes: int = field(default_factory=lambda: int(_env("RATCHET_MAX_BODY_BYTES", "2097152")))
    max_messages: int = field(default_factory=lambda: int(_env("RATCHET_MAX_MESSAGES", "256")))
    max_input_chars: int = field(default_factory=lambda: int(_env("RATCHET_MAX_INPUT_CHARS", "120000")))
    # The most choices (`n`) a request may ask for, 1 to 128; more is a 400. Each choice past the student's own samples
    # is one more student sample, and an escalated request asks the teacher for n choices (FR-1.14).
    max_n: int = field(default_factory=lambda: int(_env("RATCHET_MAX_N", str(DEFAULT_MAX_N))))
    # Merge consecutive leading system (and developer) messages into one, for models that accept only one.
    merge_system: bool = field(default_factory=lambda: _env("RATCHET_MERGE_SYSTEM", "0") == "1")
    # Content part types besides text that the teacher accepts ("text,image_url"). Such requests go to the teacher
    # only and are never stored; any other non-text part is a 400.
    teacher_modalities: str = field(default_factory=lambda: _env("RATCHET_TEACHER_MODALITIES", "text"))

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

    # The evidence features (ADR 0024). Declared with defaults that keep today's behaviour; reserved until the item that
    # reads each one lands (RESERVED_SETTINGS, `check_reserved`). Per feature: the canary's own audit rate (unset = the
    # audit rate); the golden eval's calibration rule and certificate level; teacher-watch probes and adjudications per
    # night; the receipts' take rate, audits needed per task and the mock teacher's mean reasoning tokens (unset = the
    # fixed RATCHET_TEACHER_REASONING_TOKENS); the backfill's batch API, discount and output cap; the ledger's sync mode.
    canary_audit_rate: float | None = field(default_factory=lambda: _float_or_none("RATCHET_CANARY_AUDIT_RATE"))
    calibration: str = field(default_factory=lambda: _env("RATCHET_CALIBRATION", "point"))
    cert_target: float = field(default_factory=lambda: _float("RATCHET_CERT_TARGET", "0.95"))
    cert_delta: float = field(default_factory=lambda: _float("RATCHET_CERT_DELTA", "0.05"))
    teacher_probes: int = field(default_factory=lambda: _int("RATCHET_TEACHER_PROBES", "20"))
    adjudicate_max: int = field(default_factory=lambda: _int("RATCHET_ADJUDICATE_MAX", "50"))
    take_rate: float = field(default_factory=lambda: _float("RATCHET_TAKE_RATE", "0.25"))
    receipt_min_audits: int = field(default_factory=lambda: _int("RATCHET_RECEIPT_MIN_AUDITS", "30"))
    mock_teacher_reasoning_mean: float | None = field(
        default_factory=lambda: _float_or_none("RATCHET_MOCK_TEACHER_REASONING_MEAN")
    )
    batch_api: str = field(default_factory=lambda: _env("RATCHET_BATCH_API", "operations"))
    batch_discount: float = field(default_factory=lambda: _float("RATCHET_BATCH_DISCOUNT", "0.5"))
    backfill_max_tokens: int = field(default_factory=lambda: _int("RATCHET_BACKFILL_MAX_TOKENS", "2048"))
    db_sync: str = field(default_factory=lambda: _env("RATCHET_DB_SYNC", "FULL"))

    # Mock mode only: simulate this many days of mixed traffic + nights at startup, so the
    # public demo opens with a trained student instead of a cold start.
    mock_warmup_days: int = field(default_factory=lambda: int(_env("RATCHET_MOCK_WARMUP_DAYS", "0")))
    mock_warmup_requests: int = field(default_factory=lambda: int(_env("RATCHET_MOCK_WARMUP_REQUESTS", "1500")))
    # Mock mode only: Scout's live stream waits this fraction of each call's simulated latency,
    # so the page shows agents working instead of finishing in 20 ms. 0 = no waiting.
    mock_pace: float = field(default_factory=lambda: float(_env("RATCHET_MOCK_PACE", "0.25")))

    prices: dict[str, Price] = field(default_factory=lambda: dict(DEFAULT_PRICES))

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
        return GenPolicy(
            teacher_allow=parse_param_allowlist(self.teacher_params, DEFAULT_TEACHER_PARAMS, "RATCHET_TEACHER_PARAMS"),
            student_allow=parse_param_allowlist(self.student_params, DEFAULT_STUDENT_PARAMS, "RATCHET_STUDENT_PARAMS"),
            teacher_max_tokens=self.teacher_max_tokens,
            student_max_tokens=self.student_max_tokens,
            reasoning_headroom=self.teacher_reasoning_headroom,
        )

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
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{env} must be a price in USD per GPU-hour, 0 or more, not {value!r}")
        for env, value in (("RATCHET_STUDENT_GPUS", self.student_gpus), ("RATCHET_TRAIN_GPUS", self.train_gpus)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{env} must be a whole number of GPUs, 0 or more, not {value!r}")
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
