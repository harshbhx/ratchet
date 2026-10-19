"""Teacher license registry: Ratchet only distills from teachers whose terms allow it.

This is the core of "license-clean distillation". Every training example records the
teacher that wrote it and the license that permits training on its output. Closed
models whose terms forbid training competing models are refused as teachers.

Not legal advice: check each license yourself before relying on it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeacherLicense:
    name: str
    distill_ok: bool
    url: str
    note: str = ""


OPEN_MDW = TeacherLicense(
    name="OpenMDW-1.1",
    distill_ok=True,
    url="https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
    note="Places no restrictions or obligations on outputs.",
)
NEMOTRON_OPEN = TeacherLicense(
    name="NVIDIA Nemotron Open Model License",
    distill_ok=True,
    url="https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/",
    note="Commercial use and derivative works allowed; NVIDIA claims no ownership of outputs.",
)

REGISTRY: dict[str, TeacherLicense] = {
    "nvidia/Nemotron-3-Ultra-550b-a55b": OPEN_MDW,
    "nvidia/Nemotron-3_5-Lightning": TeacherLicense(
        "OpenMDW-1.1", True, "https://huggingface.co/nvidia", "Verify on the model card."
    ),
    "nvidia/nemotron-3-super-120b-a12b": NEMOTRON_OPEN,
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": NEMOTRON_OPEN,
    "mock-teacher": TeacherLicense("mock (simulation only)", True, "", "Used by the simulator."),
}

# Terms that forbid using outputs to build or train competing models.
CLOSED: dict[str, TeacherLicense] = {
    "gpt": TeacherLicense(
        "OpenAI Business Terms",
        False,
        "https://openai.com/policies/business-terms/",
        "Section 3.3(e) forbids using Output to develop competing AI models.",
    ),
    "o1": TeacherLicense("OpenAI Business Terms", False, "https://openai.com/policies/business-terms/"),
    "o3": TeacherLicense("OpenAI Business Terms", False, "https://openai.com/policies/business-terms/"),
    "o4": TeacherLicense("OpenAI Business Terms", False, "https://openai.com/policies/business-terms/"),
    "claude": TeacherLicense(
        "Anthropic Commercial Terms",
        False,
        "https://www.anthropic.com/legal/commercial-terms",
        "Section D.4(a) forbids using the Services to train competing AI models.",
    ),
    "gemini": TeacherLicense(
        "Google Gemini API Terms",
        False,
        "https://ai.google.dev/gemini-api/terms",
        "Restricts using the API to develop competing models. Verify current clause.",
    ),
}


class LicenseError(ValueError):
    pass


def lookup(model: str) -> TeacherLicense | None:
    if model in REGISTRY:
        return REGISTRY[model]
    bare = model.split("/")[-1].lower()
    for prefix, lic in CLOSED.items():
        if bare.startswith(prefix):
            return lic
    return None


def require_distillable(model: str) -> TeacherLicense:
    """Return the license for `model`, or raise if training on its outputs is not allowed."""
    lic = lookup(model)
    if lic is None:
        raise LicenseError(f"Unknown teacher '{model}'. Add it to ratchet.licenses.REGISTRY with its license first.")
    if not lic.distill_ok:
        raise LicenseError(f"Teacher '{model}' is not distillable under {lic.name}: {lic.note}")
    return lic
