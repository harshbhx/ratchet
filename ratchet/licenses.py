"""Teacher license registry: Ratchet only distills from teachers whose terms allow it.

This is the core of "license-clean distillation". Every training example records the
teacher that wrote it and the license that permits training on its output. Closed
models whose terms forbid training competing models are refused as teachers.

The built-in registry (`BUILTIN`) is code. An operator can add open teachers without a code
change in `RATCHET_TEACHER_LICENSES` (a JSON list of {model, license, url, note}); `install`
merges them into a copy of the built-in registry at startup and publishes it as `REGISTRY`. A
configured entry is accepted only under a license in `ALLOWED_LICENSES` and for a model id
that belongs to no `CLOSED` family, so invariant 1 cannot be configured away (ADR 0090).

`BASE_MODELS` names the license of each known student base model: it travels with every
adapter in its BOM and its NOTICE.md (NFR-9).

Not legal advice: check each license yourself before relying on it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

ORIGINS = ("builtin", "config")  # where a registry entry came from: this file, or RATCHET_TEACHER_LICENSES


@dataclass(frozen=True)
class TeacherLicense:
    name: str
    distill_ok: bool
    url: str
    note: str = ""
    origin: str = "builtin"  # ORIGINS


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

# The teachers defined in code. Read-only: `install` builds `REGISTRY` from a copy of it.
BUILTIN: Mapping[str, TeacherLicense] = MappingProxyType(
    {
        "nvidia/Nemotron-3-Ultra-550b-a55b": OPEN_MDW,
        "nvidia/Nemotron-3_5-Lightning": TeacherLicense(
            "OpenMDW-1.1", True, "https://huggingface.co/nvidia", "Verify on the model card."
        ),
        "nvidia/nemotron-3-super-120b-a12b": NEMOTRON_OPEN,
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": NEMOTRON_OPEN,
        "mock-teacher": TeacherLicense("mock (simulation only)", True, "", "Used by the simulator."),
    }
)
# The registry in force: the built-in teachers plus the configured ones (`install`). A new dict is published in one
# assignment, never changed in place, so a reader on another thread sees the old registry or the new one. Always read
# `licenses.REGISTRY`, never `from .licenses import REGISTRY`.
REGISTRY: dict[str, TeacherLicense] = dict(BUILTIN)

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

# The student base models whose license is known: it travels with every adapter trained on them (the BOM's
# `base_model_license` and NOTICE.md). Hugging Face ids, matched in any case. Any other base model is not an error: the
# BOM and the NOTICE carry a warning instead, so the operator finds and ships its license themselves.
BASE_MODELS: Mapping[str, TeacherLicense] = MappingProxyType(
    {
        "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16": NEMOTRON_OPEN,
        "nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8": NEMOTRON_OPEN,
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": NEMOTRON_OPEN,
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16": NEMOTRON_OPEN,
        "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-Base-BF16": NEMOTRON_OPEN,
    }
)

# The only licenses a configured teacher may claim: open licenses that put no restriction on training other models with
# the outputs. Anything else (a community license with use restrictions, a custom one) needs a reviewed code change.
ALLOWED_LICENSES: tuple[str, ...] = ("OpenMDW-1.1", "Apache-2.0", "MIT", NEMOTRON_OPEN.name)
MAX_CONFIGURED = 100  # entries in RATCHET_TEACHER_LICENSES
MAX_MODEL_CHARS = 200
MAX_URL_CHARS = 500
MAX_NOTE_CHARS = 500
ENTRY_KEYS = ("model", "license", "url", "note")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# For a configured teacher only (`closed_family`): vendors whose hosted models are all closed, refused by name even when
# the id carries no family prefix (`anthropic/<a new name>`), mapped to their `CLOSED` entry. Their open releases
# (`openai/gpt-oss-120b`) are added in code, after their license is read.
CLOSED_VENDORS: Mapping[str, str] = MappingProxyType({"openai": "gpt", "anthropic": "claude"})


class LicenseError(ValueError):
    pass


class LicenseConfigError(LicenseError):
    """Every problem with RATCHET_TEACHER_LICENSES, each `<path>: <message>`. A ValueError, so a bad file stops startup
    with all of its errors in the message, as a bad RATCHET_TASKS_FILE does."""

    def __init__(self, errors: Iterable[str], source: str = "") -> None:
        self.errors = list(errors)
        self.source = source
        head = f"{source}: " if source else ""
        n = len(self.errors)
        super().__init__(f"{head}invalid teacher licenses ({n} error{'s' if n != 1 else ''}):\n  " + "\n  ".join(self.errors))


def closed_family(model: str) -> TeacherLicense | None:
    """The closed terms a model id belongs to, else None. The id is split at every character that is not a letter or
    digit; it belongs to a closed family when a part starts with a `CLOSED` prefix (`azure-gpt-4o`, `proxy/o1`,
    `claude-3-5-sonnet@20241022`), a part contains a prefix of three letters or more (`chatgpt-4o-latest`), or a part
    contains a `CLOSED_VENDORS` name (`anthropic/<a new name>`). Stricter than `lookup`'s rule, which reads only the start
    of the last path segment: this is the check a configured teacher must pass, so it errs toward refusing."""
    for token in _TOKEN_SPLIT.split(model.lower()):
        if not token:
            continue
        for prefix, lic in CLOSED.items():
            if token.startswith(prefix) or (len(prefix) >= 3 and prefix in token):
                return lic
        for vendor, family in CLOSED_VENDORS.items():
            if vendor in token:
                return CLOSED[family]
    return None


def lookup(model: str) -> TeacherLicense | None:
    registry = REGISTRY  # read once: `install` publishes a new dict
    lic = registry.get(model)
    if lic is not None:
        # A configured entry never names a closed family (`parse_entries`); refuse one here too, should it get in.
        return lic if lic.origin == "builtin" else (closed_family(model) or lic)
    bare = model.split("/")[-1].lower()
    for prefix, closed in CLOSED.items():
        if bare.startswith(prefix):
            return closed
    return None


def require_distillable(model: str) -> TeacherLicense:
    """Return the license for `model`, or raise if training on its outputs is not allowed."""
    lic = lookup(model)
    if lic is None:
        raise LicenseError(
            f"Unknown teacher '{model}'. Add it to ratchet.licenses.BUILTIN with its license, or to RATCHET_TEACHER_LICENSES "
            f"under one of {', '.join(ALLOWED_LICENSES)}, first."
        )
    if not lic.distill_ok:
        raise LicenseError(f"Teacher '{model}' is not distillable under {lic.name}: {lic.note}")
    return lic


def base_model_license(model: str) -> TeacherLicense | None:
    """The license of a known student base model (`BASE_MODELS`, any case), or None for one Ratchet does not know."""
    lic = BASE_MODELS.get(model)
    if lic is not None:
        return lic
    folded = model.casefold()
    for known, known_lic in BASE_MODELS.items():
        if known.casefold() == folded:
            return known_lic
    return None


def configured() -> dict[str, TeacherLicense]:
    """The registry's entries that came from RATCHET_TEACHER_LICENSES."""
    return {m: lic for m, lic in REGISTRY.items() if lic.origin == "config"}


# ---------------------------------------------------------------------------------------------------------------------
# RATCHET_TEACHER_LICENSES


def _canonical_license(name: str) -> str | None:
    folded = name.strip().casefold()
    for allowed in ALLOWED_LICENSES:
        if allowed.casefold() == folded:
            return allowed
    return None


def _text(entry: Mapping[str, Any], key: str, where: str, limit: int, errors: list[str], required: bool = True) -> str | None:
    """A string field of an entry, or None after appending its error (or when an optional field is absent)."""
    if not required and key not in entry:
        return None
    value = entry.get(key)
    if not isinstance(value, str) or (required and not value.strip()):
        errors.append(f"{where}.{key}: must be a non-empty string" if required else f"{where}.{key}: must be a string")
        return None
    if len(value) > limit:
        errors.append(f"{where}.{key}: at most {limit} characters")
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        errors.append(f"{where}.{key}: must not contain control characters")
        return None
    return value


def _builtin_name(model: str) -> str | None:
    """The built-in teacher `model` names in any case (Hugging Face ids are case-insensitive), else None."""
    folded = model.casefold()
    return next((m for m in BUILTIN if m.casefold() == folded), None)


def _entry_problems(model: str | None, name: str | None, url: str | None, where: str, seen: Mapping[str, Any]) -> list[str]:
    errors = []
    if model is not None:
        closed = closed_family(model)
        if not _MODEL_RE.fullmatch(model):
            errors.append(f"{where}.model: {model!r} is not a model id (letters, digits and . _ : / @ + -)")
        elif closed is not None:
            errors.append(
                f"{where}.model: {model!r} belongs to a closed model family ({closed.name}); its outputs may not train "
                "other models (invariant 1), whatever license is claimed"
            )
        elif (builtin := _builtin_name(model)) is not None:
            errors.append(f"{where}.model: {model!r} is a built-in teacher ({BUILTIN[builtin].name}); edit ratchet/licenses.py")
        elif model in seen:
            errors.append(f"{where}.model: {model!r} is listed twice")
    if name is not None and _canonical_license(name) is None:
        errors.append(f"{where}.license: {name!r} is not an allowed license; use one of {list(ALLOWED_LICENSES)}")
    if url is not None and (len(url) <= len("https://") or not url.startswith("https://") or any(c.isspace() for c in url)):
        errors.append(f"{where}.url: must be an https:// URL without spaces")
    return errors


def parse_entries(doc: Any) -> dict[str, TeacherLicense]:
    """{model: TeacherLicense with origin 'config'} from a parsed RATCHET_TEACHER_LICENSES document: a JSON list of
    {model, license, url, note}. Raises LicenseConfigError listing every problem. An entry is refused when its license is
    not in `ALLOWED_LICENSES` (any case; kept in its canonical spelling), its model id belongs to a closed family
    (`closed_family`), names a built-in teacher (configuration never changes what the code says), or repeats one earlier
    in the list; `url` must be an https URL, `note` is optional, and no other key is accepted."""
    if not isinstance(doc, list):
        raise LicenseConfigError(["$: must be a JSON list of {model, license, url, note} objects"])
    if len(doc) > MAX_CONFIGURED:
        raise LicenseConfigError([f"$: at most {MAX_CONFIGURED} entries, not {len(doc)}"])
    errors: list[str] = []
    out: dict[str, TeacherLicense] = {}
    for i, entry in enumerate(doc):
        where = f"[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: must be an object with model, license, url and an optional note")
            continue
        found: list[str] = []
        unknown = sorted(str(k) for k in entry if k not in ENTRY_KEYS)
        if unknown:
            found.append(f"{where}: unknown keys {unknown}; use {list(ENTRY_KEYS)}")
        model = _text(entry, "model", where, MAX_MODEL_CHARS, found)
        name = _text(entry, "license", where, MAX_MODEL_CHARS, found)
        url = _text(entry, "url", where, MAX_URL_CHARS, found)
        note = _text(entry, "note", where, MAX_NOTE_CHARS, found, required=False)
        found += _entry_problems(model, name, url, where, out)
        if found or model is None or name is None or url is None:
            errors += found
            continue
        canonical = _canonical_license(name)
        assert canonical is not None  # checked in _entry_problems
        out[model] = TeacherLicense(canonical, True, url, note or "Added by RATCHET_TEACHER_LICENSES.", origin="config")
    if errors:
        raise LicenseConfigError(errors)
    return out


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def load_file(path: str | os.PathLike[str]) -> dict[str, TeacherLicense]:
    """The configured teachers of a RATCHET_TEACHER_LICENSES file (strict JSON: no NaN or Infinity). Raises
    LicenseConfigError, naming the file, for a file it cannot read or parse and for every invalid entry."""
    p = Path(path)
    source = f"RATCHET_TEACHER_LICENSES {p}"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise LicenseConfigError([f"$: cannot read the file: {getattr(e, 'strerror', None) or e}"], source) from None
    try:
        doc = json.loads(text, parse_constant=_no_constants)
    except (ValueError, RecursionError) as e:
        raise LicenseConfigError([f"$: not valid JSON: {e}"], source) from None
    try:
        return parse_entries(doc)
    except LicenseConfigError as e:
        raise LicenseConfigError(e.errors, source) from None


def install(path: str | os.PathLike[str] | None = "") -> list[str]:
    """Publish the registry for this process: a copy of `BUILTIN` plus the teachers of the RATCHET_TEACHER_LICENSES file
    at `path` (none when empty), in one assignment. A bad file raises LicenseConfigError and leaves the registry as it
    was. Returns the configured model ids. `Ratchet.__init__` (the gateway, the simulator), `python -m ratchet.live` and
    `python -m ratchet.admin` call it at startup, before anything looks a teacher up."""
    path = str(path).strip() if path else ""
    extra = load_file(path) if path else {}
    global REGISTRY
    REGISTRY = {**BUILTIN, **extra}
    return sorted(extra)


def reset_builtin() -> None:
    """Make the registry the built-in teachers again (tests)."""
    global REGISTRY
    REGISTRY = dict(BUILTIN)
