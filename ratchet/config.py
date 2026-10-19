"""Runtime settings, read from environment variables.

Model IDs follow the Nebius Token Factory catalog as of Oct 2026. Confirm them on
day 1 with `GET https://api.tokenfactory.nebius.com/v1/models?verbose=true`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


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

    # Extra output tokens the teacher spends on reasoning (billed, not returned).
    teacher_reasoning_tokens: int = field(default_factory=lambda: int(_env("RATCHET_TEACHER_REASONING_TOKENS", "300")))

    prices: dict[str, Price] = field(default_factory=lambda: dict(DEFAULT_PRICES))

    def price(self, model: str) -> Price:
        if model in self.prices:
            return self.prices[model]
        if model.startswith("student"):
            return self.prices["student"]
        return self.prices.get(self.teacher_model, Price(1.0, 3.0))
