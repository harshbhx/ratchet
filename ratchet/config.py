"""Runtime settings, read from environment variables.

Model IDs follow the Nebius Token Factory catalog as of Oct 2026. Confirm them on
day 1 with `GET https://api.tokenfactory.nebius.com/v1/models?verbose=true`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

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


# RATCHET_ON_TEACHER_FAILURE: what a request whose teacher call failed gets (ADR 0019).
TEACHER_FAILURE_MODES = ("error", "serve_student")


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

    # Request limits on the OpenAI-compatible API (ratchet/openai_compat.py), checked before any model call. 0 = no limit.
    # The body limit applies to every POST, PUT and PATCH; the other two to /v1/chat/completions.
    max_body_bytes: int = field(default_factory=lambda: int(_env("RATCHET_MAX_BODY_BYTES", "2097152")))
    max_messages: int = field(default_factory=lambda: int(_env("RATCHET_MAX_MESSAGES", "256")))
    max_input_chars: int = field(default_factory=lambda: int(_env("RATCHET_MAX_INPUT_CHARS", "120000")))
    # Merge consecutive leading system (and developer) messages into one, for models that accept only one.
    merge_system: bool = field(default_factory=lambda: _env("RATCHET_MERGE_SYSTEM", "0") == "1")
    # Content part types besides text that the teacher accepts ("text,image_url"). Such requests go to the teacher
    # only and are never stored; any other non-text part is a 400.
    teacher_modalities: str = field(default_factory=lambda: _env("RATCHET_TEACHER_MODALITIES", "text"))

    # Ed25519 private key (base64 of the 32-byte seed) that signs each adapter's bill of materials.
    # Generate one with `python -m ratchet.bom keygen`. Empty = manifests are stored unsigned.
    bom_signing_key: str = field(default_factory=lambda: _env("RATCHET_BOM_SIGNING_KEY", ""))

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

    def price(self, model: str) -> Price:
        if model in self.prices:
            return self.prices[model]
        if model.startswith("student"):
            return self.prices["student"]
        return self.prices.get(self.teacher_model, Price(1.0, 3.0))
