"""Public claims (NFR-70): the pages a judge, a customer or an investor reads claim only what is true.

The customer owns a LoRA adapter and its signed training lineage, not the base model's weights, which stay under NVIDIA's
Nemotron Open Model License. The bill does not "only" go down: below break-even a GPU endpoint raises it, as Ratchet's own
cost report says. OpenRouter already filters for distillable teachers, so "no competitor sells license-clean distillation"
is not true. Competitor claims nobody could source stay out, new competitor rows carry a source and a date, and the README
names its prior art, its limitations and the NVIDIA lineage it builds on.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Pages people read as claims about Ratchet. Sources only: ratchet/web/dist is built from web/src.
CLAIM_FILES = (
    "README.md",
    "docs/README.md",
    "docs/PRD.md",
    "docs/COMPETITIVE_ANALYSIS.md",
    "docs/BUSINESS.md",
    "docs/HACKATHON_SUBMISSION.md",
    "docs/GLOSSARY.md",
    "docs/DEMO_SCRIPT.md",
    "ratchet/web/landing.html",
    "ratchet/dashboard.html",
    "web/index.html",
)
CLAIM_GLOBS = ("web/src/**/*.tsx", "web/src/**/*.ts", "ratchet/web/*.html")
OVERCLAIMS = (
    "own the model weights",
    "owns the model weights",
    "your bill only goes down",
    "no competitor sells license-clean distillation",
)
# Competitor claims no source was found for (research notes, Oct 21, 2026).
UNSOURCED = (
    "orchestra routes per task",
    "guarantees at least 50% lower cost",
    "50% lower cost with an sla",
    "lower cost under sla",
)
# NVIDIA Research, "Small Language Models are the Future of Agentic AI", arXiv 2506.02153 v3: the LLM-to-SLM steps.
PAPER_STEPS = (
    ("S1", "Secure usage data collection"),
    ("S2", "Data curation and filtering"),
    ("S3", "Task clustering"),
    ("S4", "SLM selection"),
    ("S5", "Specialized SLM fine-tuning"),
    ("S6", "Iteration and refinement"),
)
# OpenMDW-1.1's outputs clause, verbatim (license page last updated May 27, 2026).
OPENMDW_QUOTE = (
    '"does not impose any restrictions or obligations with respect to any use, modification, or sharing of any outputs"'
)
# Rows the competitive analysis added on Oct 21, 2026; each needs a link and a date.
SOURCED_ROWS = (
    "OpenRouter distillable routing",
    "NVIDIA Data Flywheel Blueprint",
    "NVIDIA license-compliant distillation tutorial",
    "TensorZero",
    "Inference.net",
    "Portkey",
)
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
DATE = re.compile(rf"\b(?:{_MONTH} (?:\d{{1,2}}, )?20\d\d|20\d\d-\d\d-\d\d)\b")  # Oct 21, 2026 or 2026-10-21


def _variants(text: str) -> tuple[str, str]:
    """Lower-case prose with Markdown emphasis, entities, JSX spacers and line breaks removed, so a phrase split over two
    lines or wrapped in ** still matches; the second variant also drops HTML and JSX tags (`<strong>`). Both are
    checked, because a stray `<` ... `>` in code could otherwise swallow the text between them."""
    text = html.unescape(text).replace("\u2019", "'")  # a curly apostrophe reads as a straight one
    text = re.sub(r"\{\s*([\"'`])\s*\1\s*\}", " ", text)  # {" "} in JSX
    text = re.sub(r"[*_`]", "", text)
    plain = re.sub(r"\s+", " ", text).lower()
    untagged = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).lower()
    return plain, untagged


def _hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    seen = _variants(text)
    return [p for p in phrases if any(p in v for v in seen)]


def _claim_files() -> list[Path]:
    paths = {ROOT / f for f in CLAIM_FILES}
    for pattern in CLAIM_GLOBS:
        paths.update(ROOT.glob(pattern))
    return sorted(paths)


def _sections(markdown: str) -> dict[str, str]:
    """Level-2 heading -> its body, ignoring lines inside fenced code blocks."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    fenced = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = out.setdefault(line[3:].strip(), [])
            continue
        if current is not None:
            current.append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.req("NFR-70")
def test_no_overclaims():
    """None of the public pages says the customer owns the model weights, that the bill only goes down, or that no
    competitor sells license-clean distillation, in any wrapping or markup."""
    # The matcher sees through line breaks, emphasis, markup, entities and JSX spacers.
    for sample in (
        "you **own the model\n  weights**",
        "<p>You own the <strong>model</strong> weights</p>",
        "Your bill only goes&nbsp;down",
        'Ratchet owns the{" "}model weights',
        "_No competitor sells_ license-clean\ndistillation.",
    ):
        assert _hits(sample, OVERCLAIMS), sample
    assert _hits("You own the LoRA adapter; the base keeps its license.", OVERCLAIMS) == []

    files = _claim_files()
    missing = [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    assert missing == [], f"claim files moved or were renamed: {missing}"
    assert sum(p.suffix == ".tsx" for p in files) >= 20, "the web/src glob found no React pages"
    found = {str(p.relative_to(ROOT)): hits for p in files if (hits := _hits(p.read_text(encoding="utf-8"), OVERCLAIMS))}
    assert found == {}


@pytest.mark.req("NFR-70")
def test_unsourced_competitor_claims_are_gone():
    """Experiential Labs' "at least 50% lower cost with an SLA" and "Orchestra routes per task" had no source; they
    stay out of every page that compares Ratchet with competitors."""
    found = {
        rel: hits
        for rel in ("README.md", "docs/COMPETITIVE_ANALYSIS.md", "docs/BUSINESS.md", "docs/HACKATHON_SUBMISSION.md")
        if (hits := _hits(_read(rel), UNSOURCED))
    }
    assert found == {}


@pytest.mark.req("NFR-70")
def test_readme_names_prior_art_and_limits():
    """The README has "Prior art and what is new", "Limitations" and "NVIDIA lineage": the paper's six steps in its
    words and order, each mapped to Ratchet, with its arXiv id; the known ideas with their links; the limits that
    break first; and the teacher's license quoted exactly under "Why license-clean"."""
    sections = _sections(_read("README.md"))
    for heading in ("Prior art and what is new", "Limitations", "NVIDIA lineage"):
        assert heading in sections, f"README has no '## {heading}' section"

    lineage = sections["NVIDIA lineage"]
    rows = re.findall(r'^\| (S\d) "([^"]+)" \| (.+?) \|$', lineage, re.M)
    assert [(s, step) for s, step, _ in rows] == list(PAPER_STEPS)
    assert all(len(cell.strip()) >= 20 for *_, cell in rows), "a step has no real mapping"
    assert "https://arxiv.org/abs/2506.02153" in lineage and "v3, Sep 22, 2026" in lineage
    assert "Data Flywheel Blueprint" in lineage and "human decision" in lineage

    prior = sections["Prior art and what is new"]
    for name in (
        "LiteLLM",
        "OpenPipe",
        "Inference.net",
        "FrugalGPT",
        "Cache & Distil",
        "Online Cascade Learning",
        "Data Flywheel Blueprint",
        "enforce_distillable_text",
        "license-compliant distillation tutorial",
        "Trust or Escalate",
        "Netflix",
    ):
        assert name in prior, name
    assert len(re.findall(r"\]\(https://", prior)) >= 14, "the known ideas need their sources"
    assert re.search(r"^- \*\*New in Ratchet, together:\*\*", prior, re.M)

    limits = sections["Limitations"]
    assert len(re.findall(r"^- \*\*", limits, re.M)) >= 6
    for topic in ("break-even", "RATCHET_STUDENT_BASE", "ground truth", "pattern-based", "mock models", "§5(f)"):
        assert topic in limits, topic

    assert OPENMDW_QUOTE in sections['Why "license-clean"']
    assert "https://openmdw.ai/license/1-1/" in sections['Why "license-clean"']


@pytest.mark.req("NFR-70")
def test_ownership_is_stated_exactly():
    """The README's ownership line names the adapter, the lineage, the base's license and the break-even warning; the lite
    landing page prints the same sentence; the PRD's overview, G5, P3 and US-10 say "adapter", not "weights"."""
    readme = _read("README.md")
    lines = [line for line in readme.splitlines() if "You own the LoRA adapter" in line]
    assert len(lines) == 1, lines
    line = lines[0]
    for part in ("signed training lineage", "Nemotron Open Model License", "would cost more than it saves"):
        assert part in line, part

    landing = _read("ratchet/web/landing.html")
    after = re.findall(r'<p class="after">(.*?)</p>', landing, re.S)
    assert [html.unescape(re.sub(r"\s+", " ", a)).strip() for a in after] == [line.strip()]

    prd = _read("docs/PRD.md")
    overview = _sections(prd)["1. Overview"]
    assert "The customer owns the LoRA adapter and its signed training lineage" in overview
    g5 = re.search(r"^\| G5 \| (.+) \|$", prd, re.M)
    assert g5 and "LoRA adapter" in g5.group(1) and "own license" in g5.group(1)
    assert "- **Needs:** own the adapter," in prd
    assert re.search(r"^\*\*US-10\. Own the adapter\.\*\*", prd, re.M)


@pytest.mark.req("NFR-70")
def test_competitive_analysis_cites_sources_and_dates():
    """The competitive analysis is dated, its summary makes the narrower true claim, and each row added on Oct 21, 2026
    (OpenRouter's distillable routing, NVIDIA's blueprint and tutorial, TensorZero, Inference.net, Portkey) links its
    source and gives a date."""
    doc = _read("docs/COMPETITIVE_ANALYSIS.md")
    assert re.search(rf"^As of {DATE.pattern}", doc, re.M), "the analysis needs an as-of date"

    summary = _sections(doc)["Summary"].strip().split("\n\n")[0]
    assert summary.endswith(
        "OpenRouter filters for distillable teachers at request time; no gateway we found records each example's teacher "
        "and license when it is captured and signs that lineage into every trained adapter."
    )

    rows = [line for line in doc.splitlines() if line.startswith("| [")]
    for name in SOURCED_ROWS:
        row = next((r for r in rows if r.startswith(f"| [{name}](https://")), None)
        assert row is not None, f"no linked row for {name}"
        assert DATE.search(row), f"the {name} row gives no date"
