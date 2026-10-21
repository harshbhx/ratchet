"""NOTICE.md for an adapter folder: the licenses that travel with the adapter (NFR-9, ADR 0090).

A LoRA adapter is useless without its base model and was made from teacher outputs, so whoever copies it needs the base
model's license and each teacher's. This module writes them next to the adapter weights:

- `write_notices(out_dir, base_model, sources)` writes NOTICE.md: the base model's license (or a warning when Ratchet
  does not know it), each teacher with its license and URL, that the targets were teacher outputs only, and the Ratchet
  version. `train.py` calls it after `save_pretrained`; live Night School writes it again from the BOM.
- `render` is pure: the text depends only on its arguments and the license registry, so `bom.manifest` puts the exact
  text in the signed BOM (`notices`) and anyone can compare the file next to the adapter with it.
- `write_adapter_files(out_dir, doc)` writes a version's BOM (`ratchet_bom.json`) and the NOTICE.md it signed, for the
  night's adapter folder and for `python -m ratchet.live export`.

Standard library and `ratchet.licenses` only: `train.py` imports it inside the GPU container.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .. import __version__, licenses

NOTICE_FILE = "NOTICE.md"
BOM_FILE = "ratchet_bom.json"


def generator() -> str:
    """The Ratchet version a BOM and a NOTICE name (`bom.manifest`'s `generator`)."""
    return f"ratchet {__version__}"


def base_license(base_model: str) -> dict[str, Any]:
    """The BOM's `base_model_license`: {name, url} of a known base model (`licenses.BASE_MODELS`), else name and url None
    and a `warning`, never an error: an operator may train any base model, and must then ship its license themselves."""
    base_model = str(base_model)
    lic = licenses.base_model_license(base_model)
    if lic is not None:
        return {"name": lic.name, "url": lic.url}
    return {
        "name": None,
        "url": None,
        "warning": f"base model {base_model!r} is not in ratchet.licenses.BASE_MODELS: find its license and ship it with "
        "the adapter",
    }


def sources(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The BOM's `sources` from training rows: one entry per (teacher, license) the rows' provenance records, sorted, with
    the license URL, whether it allows distillation and where the registry entry came from (`license_origin`: 'builtin'
    or 'config'), all from the registry in force, and the number of examples. A teacher the registry no longer knows
    has URL and origin None and `distill_ok` False. Labels from a source that is not a teacher (`licenses.SOURCES`:
    `human`) are not teachers and are left out: `label_sources` lists them (FR-3.54)."""
    counts: Counter[tuple[str, str]] = Counter()
    for r in rows:
        prov = r["provenance"]
        if prov["teacher"] in licenses.SOURCES:
            continue
        counts[(prov["teacher"], prov["license"])] += 1
    out = []
    for (teacher, lic_name), n in sorted(counts.items()):
        lic = licenses.lookup(teacher)
        out.append(
            {
                "teacher": teacher,
                "license": lic_name,
                "license_url": lic.url if lic else None,
                "license_origin": lic.origin if lic else None,
                "distill_ok": bool(lic and lic.distill_ok),
                "examples": n,
            }
        )
    return out


def label_sources(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The BOM's `label_sources` from training rows: one entry per non-teacher provenance source the rows record
    (`licenses.SOURCES`: `human`, labels a customer wrote or approved), sorted, with its license name, note, whether it
    allows distillation, the number of examples and how many came from each origin (`feedback`, `import`). Empty when
    every row is a teacher's answer (ADR 0091)."""
    counts: Counter[tuple[str, str]] = Counter()
    origins: dict[tuple[str, str], Counter[str]] = {}
    for r in rows:
        prov = r["provenance"]
        if prov["teacher"] not in licenses.SOURCES:
            continue
        key = (prov["teacher"], prov["license"])
        counts[key] += 1
        origins.setdefault(key, Counter())[str(prov.get("origin") or "live")] += 1
    out = []
    for (source, lic_name), n in sorted(counts.items()):
        lic = licenses.SOURCES[source]
        out.append(
            {
                "source": source,
                "license": lic_name,
                "note": lic.note,
                "distill_ok": lic.distill_ok,
                "examples": n,
                "origins": dict(sorted(origins[(source, lic_name)].items())),
            }
        )
    return out


def _cell(value: Any) -> str:
    """A value as one Markdown table cell: on one line, with pipes escaped."""
    text = " ".join(str(value).split())
    return text.replace("\\", "\\\\").replace("|", "\\|") or "-"


def _code(value: str) -> str:
    text = " ".join(value.split())
    return f"`{text}`" if "`" not in text else text


def render(
    base_model: str,
    teacher_sources: Iterable[Mapping[str, Any]],
    *,
    version: str | None = None,
    label_sources: Iterable[Mapping[str, Any]] = (),
) -> str:
    """NOTICE.md's text for an adapter trained on `base_model` from `teacher_sources` (entries with `teacher`, `license`
    and, when known, `license_url`, `license_origin` and `examples`, as in a BOM's `sources`) and `label_sources` (a
    BOM's `label_sources`: human labels, ADR 0091). `version` is the Ratchet that trained it (default this one).
    Deterministic: the same arguments give the same bytes, and an adapter without human labels gets the text it got
    before they existed."""
    labels = sorted(label_sources, key=lambda s: (str(s.get("source")), str(s.get("license"))))
    version = version or generator()
    base_model = str(base_model)
    base = base_license(base_model)
    lines = [
        "# NOTICE",
        "",
        f"This folder holds a LoRA adapter trained with Ratchet ({version}). The licenses below travel with it: keep this "
        "file with the adapter wherever you copy, share or deploy it.",
        "",
        "## Base model",
        "",
    ]
    if base["name"] is not None:
        lines.append(f"The adapter runs on top of {_code(base_model)}, which is governed by the {base['name']}: {base['url']}")
    else:
        lines.append(
            f"**Warning:** {_code(base_model)} is not in Ratchet's list of known base models (`ratchet.licenses.BASE_MODELS`), "
            "so its license is not recorded here. Find the license that governs it and ship it with this adapter."
        )
    lines += ["", "## Training data", ""]
    if labels:
        lines.append(
            "The adapter was trained on answers written by the teacher models below and on labels written or approved by "
            "the customer (listed after them). The student's own answers were never training targets."
        )
    else:
        lines.append(
            "The adapter was trained on teacher outputs only: answers written by the teacher models below. The student's "
            "own answers were never training targets."
        )
    lines.append("")
    rows = sorted(teacher_sources, key=lambda s: (str(s.get("teacher")), str(s.get("license"))))
    if not rows:
        lines.append("No teacher outputs are recorded for this adapter.")
    else:
        lines += ["| Teacher | License | License URL | Examples |", "|---|---|---|---|"]
        configured = False
        for s in rows:
            name = _cell(s.get("license"))
            if s.get("license_origin") == "config":
                name += " (configured)"
                configured = True
            examples = s.get("examples")
            lines.append(
                f"| {_cell(s.get('teacher'))} | {name} | {_cell(s.get('license_url') or 'not recorded')} | "
                f"{_cell(examples if examples is not None else '-')} |"
            )
        if configured:
            lines += [
                "",
                "A license marked (configured) was added by the operator in RATCHET_TEACHER_LICENSES, from Ratchet's list of "
                f"allowed licenses ({', '.join(licenses.ALLOWED_LICENSES)}), not by Ratchet's built-in registry.",
            ]
    if labels:
        lines += [
            "",
            "## Human labels",
            "",
            "These labels were not written by a model. Their provenance is the customer's own attestation, recorded with "
            "each import and correction.",
            "",
            "| Source | License | Note | Examples |",
            "|---|---|---|---|",
        ]
        for s in labels:
            examples = s.get("examples")
            lines.append(
                f"| {_cell(s.get('source'))} | {_cell(s.get('license'))} | {_cell(s.get('note') or '-')} | "
                f"{_cell(examples if examples is not None else '-')} |"
            )
    lines += [
        "",
        f"The bill of materials ({BOM_FILE}) lists the exact training and golden sets, their hashes and the evaluation; "
        "check it with `python -m ratchet.bom verify`.",
        "",
        "Not legal advice: check each license yourself.",
        "",
    ]
    return "\n".join(lines)


def write_notices(
    out_dir: str | Path,
    base_model: str,
    teacher_sources: Iterable[Mapping[str, Any]],
    *,
    version: str | None = None,
    label_sources: Iterable[Mapping[str, Any]] = (),
) -> Path:
    """Write NOTICE.md into `out_dir` (created if missing) and return its path (`render`)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / NOTICE_FILE
    text = render(base_model, teacher_sources, version=version, label_sources=label_sources)
    path.write_bytes(text.encode("utf-8"))  # exact bytes: no newline translation
    return path


def notice_entry(text: str) -> dict[str, Any]:
    """One entry of a BOM's `notices`: the file, its SHA-256 and its exact text."""
    return {"file": NOTICE_FILE, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text": text}


def bom_notice(doc: Mapping[str, Any]) -> str:
    """The NOTICE.md text a BOM document signed (its `notices` entry), or, for a BOM written before BOMs carried one
    (v1, or v2 from before NFR-9), the text rendered now from its base model, sources and generator."""
    m = doc["manifest"]
    for entry in m.get("notices") or ():
        if isinstance(entry, Mapping) and entry.get("file") == NOTICE_FILE and isinstance(entry.get("text"), str):
            return entry["text"]
    return render(
        str(m["adapter"]["base_model"]),
        m.get("sources") or [],
        version=m.get("generator"),
        label_sources=m.get("label_sources") or [],
    )


def bom_bytes(doc: Mapping[str, Any]) -> bytes:
    """How a BOM document is written to a file (the same bytes in `data/boms/`, the adapter folder and an export)."""
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


def write_adapter_files(out_dir: str | Path, doc: Mapping[str, Any]) -> tuple[Path, Path]:
    """Write a version's BOM (`ratchet_bom.json`) and its NOTICE.md (`bom_notice`) into `out_dir`, created if missing.
    Returns both paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bom_path, notice_path = out / BOM_FILE, out / NOTICE_FILE
    bom_path.write_bytes(bom_bytes(doc))
    notice_path.write_bytes(bom_notice(doc).encode("utf-8"))
    return bom_path, notice_path
