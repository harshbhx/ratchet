"""Public claims (NFR-70): the pages a judge, a customer or an investor reads claim only what is true.

The customer owns a LoRA adapter and its signed training lineage, not the base model or its weights, which stay under
NVIDIA's Nemotron Open Model License. The bill does not "only" go down: below break-even a GPU endpoint raises it, as Ratchet's
own cost report says. OpenRouter already filters for distillable teachers, so "no competitor sells license-clean distillation"
is not true. Of NVIDIA's six LLM-to-SLM steps, task clustering and model selection are manual, so "all six" is not true either.
Nebius product facts are worded as Nebius's docs word them. Competitor claims nobody could source stay out, new competitor rows
carry a source and a date, and the README names its prior art, its limitations, the NVIDIA lineage it builds on and how the
repo was built, and never calls a feature the PRD marks Done "not built yet".
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
    "docs/GLOSSARY.md",
    "docs/ARCHITECTURE.md",
    "ratchet/web/landing.html",
    "ratchet/dashboard.html",
    "web/index.html",
    "pyproject.toml",
)
CLAIM_GLOBS = ("web/src/**/*.tsx", "web/src/**/*.ts", "ratchet/web/*.html")
OVERCLAIMS = (
    # The customer owns the adapter, not the base model or its weights.
    "own the model weights",
    "owns the model weights",
    "own the weights",
    "owns the weights",
    "own the trained weights",
    "weights you keep",
    "model you own",
    "model that you own",
    "your bill only goes down",
    "no competitor sells license-clean distillation",
    # S3 (task clustering) and S4 (SLM selection) of arXiv 2506.02153 are manual.
    "automates all six",
    "runs all six",
    "all six steps",
    # Nebius's docs: a stopped endpoint is not billed for compute or its own storage, a mounted bucket still is; and
    # nothing in Token Factory's current docs says its LoRA serving is Llama-only.
    "stopped endpoints cost nothing",
    "stopped endpoints costing nothing",
    "stopped endpoint costs nothing",
    "llama-only",
    # Authorship: the repo's history is public, and no page claims sole authorship of the code.
    "one student founder built it",
)
# A competitor's own words, quoted as theirs, are not a claim about Ratchet. Each entry must still be in its file.
QUOTED = {"docs/COMPETITIVE_ANALYSIS.md": ('"a specialized model you own"',)}
# Docs beyond the public pages that state Nebius product facts (deployment guides, research notes, deploy scripts).
NEBIUS_FACTS = (
    "stopped endpoints cost nothing",
    "stopped endpoints costing nothing",
    "stopped endpoint costs nothing",
    "llama-only",
)
NEBIUS_GLOBS = ("README.md", "docs/**/*.md", "deploy/**/*.md", "deploy/**/*.sh", "deploy/**/*.yaml", "deploy/**/*.yml")
# docs/TESTING.md describes these tests, so it names the phrases they look for.
NEBIUS_SKIP = ("docs/TESTING.md",)
# Competitor claims no source was found for (research notes, Oct 21, 2026).
UNSOURCED = (
    "orchestra routes per task",
    "guarantees at least 50% lower cost",
    "50% lower cost with an sla",
    "lower cost under sla",
    # Inference.net's launch post (Apr 14, 2026) says new models are "staged for final review and deployment"; it never
    # says a person approves them.
    "releases a person approves",
    "before a person approves",
    "a person reviews it before deployment",
    "leave the release to a person",
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
# Whether each step runs by itself in Ratchet (the "Automatic?" column): S3 and S4 are manual.
AUTOMATIC = {"S1": True, "S2": True, "S3": False, "S4": False, "S5": True, "S6": True}
# NVIDIA's Data Flywheel Blueprint README and the Nano 4B model card, verbatim (read Oct 21, 2026).
FLYWHEEL_QUOTES = (
    '"Promotion to production—as well as any deeper evaluation or dataset curation—remains a human decision"',
    '"Think of the Flywheel as a flashlight, not an autopilot"',
)
NANO_LINEAGE = '"has been compressed from NVIDIA-Nemotron-Nano-9B-v2 using the Nemotron Elastic framework"'
ADAPTER_WORDING = "Nemotron adapter you own (LoRA on NVIDIA's open Nano base)"
# What a "Not built yet" line names, and the PRD row that says whether it is built.
NOT_BUILT_TOPICS = {"committed training sets": "FR-16.4", "merkle proof": "FR-16.4"}
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


def _unquoted(rel: str, text: str) -> str:
    """The file's text without the competitor quotes `QUOTED` allows in it."""
    for quote in QUOTED.get(rel, ()):
        assert quote in text, f"{rel} no longer quotes {quote}: drop it from QUOTED"
        text = text.replace(quote, "")
    return text


def _prd_status(prd: str, rid: str) -> str:
    """The status cell of a PRD requirement row ("Done (v0.5)", "Planned", ...), or "" when the row is missing."""
    m = re.search(rf"^\| {re.escape(rid)} \|.*$", prd, re.M)
    return m.group(0).rstrip().rstrip("|").rsplit("|", 1)[-1].strip() if m else ""


def _stale_not_built(bullet: str, prd: str) -> list[str]:
    """The PRD ids a "Not built yet" line names, by id or by topic, that the PRD marks Done."""
    ids = set(re.findall(r"\b(FR-\d+\.\d+)\b", bullet))
    ids.update(rid for topic, rid in NOT_BUILT_TOPICS.items() if topic in bullet.lower())
    return sorted(rid for rid in ids if _prd_status(prd, rid).startswith("Done"))


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
        '{ k: "nano", label: "Nano · yours", sub: "weights you keep" }',
        "Your own traffic trains a small model that you own",
        "turns your traffic into a cheaper model you own.",
        "Ratchet automates all\nsix:",
        "when idle: stopped endpoints cost nothing",
        "Serverless LoRA serving is **Llama-only**.",
    ):
        assert _hits(sample, OVERCLAIMS), sample
    assert _hits("You own the LoRA adapter; the base keeps its license.", OVERCLAIMS) == []
    assert _hits(f"Your traffic trains a {ADAPTER_WORDING}.", OVERCLAIMS) == []

    files = _claim_files()
    missing = [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    assert missing == [], f"claim files moved or were renamed: {missing}"
    assert sum(p.suffix == ".tsx" for p in files) >= 20, "the web/src glob found no React pages"
    found = {}
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if hits := _hits(_unquoted(rel, p.read_text(encoding="utf-8")), OVERCLAIMS):
            found[rel] = hits
    assert found == {}


@pytest.mark.req("NFR-70")
def test_unsourced_competitor_claims_are_gone():
    """Experiential Labs' "at least 50% lower cost with an SLA" and "Orchestra routes per task" had no source; they
    stay out of every page that compares Ratchet with competitors."""
    found = {
        rel: hits
        for rel in ("README.md", "docs/COMPETITIVE_ANALYSIS.md", "docs/BUSINESS.md")
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
    rows = re.findall(r'^\| (S\d) "([^"]+)" \| (.+?) \| (.+?) \|$', lineage, re.M)
    assert [(s, step) for s, step, *_ in rows] == list(PAPER_STEPS)
    assert all(len(cell.strip()) >= 20 for _, _, cell, _ in rows), "a step has no real mapping"
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
    for topic in ("break-even", "RATCHET_STUDENT_BASE", "ground truth", "pattern-based", "mock models", "§5(f)", "key_fact"):
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


@pytest.mark.req("NFR-70")
def test_nvidia_lineage_is_stated_exactly():
    """Of the paper's six steps, S1, S2, S5 and S6 run by themselves; S3 (task clustering) and S4 (SLM selection) are
    manual, and the README says so in its sentence, in an "Automatic?" column and in the S3 and S4 rows. The Data Flywheel
    Blueprint and the Nano 4B model card are quoted verbatim, and the competitive analysis claims four of six, not six."""
    lineage = _sections(_read("README.md"))["NVIDIA lineage"]
    assert "| Step in the paper | In Ratchet | Automatic? |" in lineage
    rows = {s: (cell, auto) for s, _, cell, auto in re.findall(r'^\| (S\d) "([^"]+)" \| (.+?) \| (.+?) \|$', lineage, re.M)}
    assert sorted(rows) == sorted(AUTOMATIC)
    for step, automatic in AUTOMATIC.items():
        cell, auto = rows[step]
        assert auto.startswith("Yes" if automatic else "No: manual"), (step, auto)
    assert rows["S3"][0].startswith("Not clustered: the caller names each request's task")
    assert rows["S4"][0].startswith("One base, Nemotron 3 Nano 4B, chosen by hand")
    assert (
        "S1, S2, S5 and S6 run automatically. S3 (task clustering) is manual: the caller or operator names each task. "
        "S4 (SLM selection) is manual: one base model." in lineage
    )
    for quote in (*FLYWHEEL_QUOTES, NANO_LINEAGE):
        assert quote in lineage, quote
    assert "Ratchet makes the promotion decision itself, with statistics" in lineage
    assert "Mar 16, 2026" in lineage and "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16" in lineage

    wins = _sections(_read("docs/COMPETITIVE_ANALYSIS.md"))["Where Ratchet differs"]
    assert "runs four of the six steps" in wins and "S1, S2, S5 and S6" in wins
    assert "Task clustering (S3) and model selection (S4) are manual" in wins


@pytest.mark.req("NFR-70")
def test_readme_compares_routers_and_distillation_services():
    """The README's "Why not a router or a distillation API?" compares a cascade router, the distillation services and
    Ratchet on the five questions a buyer asks; Inference.net is described in its launch post's words; "How the repo was
    built" dates the history and names the generated files without claiming sole authorship."""
    sections = _sections(_read("README.md"))
    table = sections["Why not a router or a distillation API?"]
    header = next(line for line in table.splitlines() if line.startswith("| |"))
    assert "Cascade router" in header and "Distillation service" in header and header.rstrip().endswith("| Ratchet |")
    for service in ("OpenPipe", "AWS Bedrock", "OpenAI"):
        assert service in header, service
    rows = [line.split("|")[1].strip() for line in table.splitlines() if line.startswith("| ") and not line.startswith("| |")]
    assert rows == [
        "Who owns the student",
        "Teacher license recorded per example",
        "Automatic nightly retrain",
        "Statistical release gate",
        "Reports when it loses money",
    ]
    assert "Understudy" in table and "Experiential Labs" in table

    prior = sections["Prior art and what is new"]
    assert '"staged for final review and deployment"' in prior
    assert '"Active existing customers will no longer be able to create new fine-tuning jobs" on Jan 6, 2027' in prior

    built = sections["How the repo was built"]
    assert "Oct 19, 2026" in built
    for generated in ("ratchet/web/dist/", "docs/REQUIREMENTS.md", "tests/fixtures/reference.json", "pyproject.toml"):
        assert generated in built, generated
    for claim in ("sole author", "single-handed", "built it alone", "written entirely by", "one student founder"):
        assert claim not in built.lower(), claim


@pytest.mark.req("NFR-70")
def test_ownership_wording_on_every_first_screen():
    """The package description, the docs index, the React and lite landing pages' first screen and the HTML meta
    description say "a Nemotron adapter you own (LoRA on NVIDIA's open Nano base)", and the hero diagram's Nano node keeps an
    adapter, not weights."""
    for rel in ("pyproject.toml", "docs/README.md", "web/index.html", "web/src/pages/landing/sections/hero.tsx"):
        assert ADAPTER_WORDING.lower() in _variants(_read(rel))[0], rel
    landing = _read("ratchet/web/landing.html")
    meta = re.search(r'<meta name="description" content="([^"]*)"', landing)
    hero = re.search(r'<p class="sub">(.*?)</p>', landing, re.S)
    assert meta and ADAPTER_WORDING.lower() in _variants(meta.group(1))[0], "the lite landing page's meta description"
    assert hero and ADAPTER_WORDING.lower() in _variants(hero.group(1))[1], "the lite landing page's hero"
    assert 'sub: "adapter you keep"' in _read("web/src/pages/landing/sections/hero-flow.tsx")


@pytest.mark.req("NFR-70")
def test_nebius_facts_are_worded_as_documented():
    """No doc or deploy script says a stopped endpoint costs nothing (a mounted bucket is still billed) or that Token
    Factory's LoRA serving is Llama-only (its current docs say custom weights are in beta, on request); the PRD's
    dependency row says what a stopped endpoint is billed for."""
    paths = set()
    for pattern in NEBIUS_GLOBS:
        paths.update(ROOT.glob(pattern))
    paths.difference_update(ROOT / rel for rel in NEBIUS_SKIP)
    assert ROOT / "docs" / "DEPLOYMENT.md" in paths and ROOT / "deploy" / "student_endpoint.sh" in paths
    found = {str(p.relative_to(ROOT)): h for p in sorted(paths) if (h := _hits(p.read_text(encoding="utf-8"), NEBIUS_FACTS))}
    assert found == {}

    row = next(line for line in _read("docs/PRD.md").splitlines() if line.startswith("| Nebius Serverless Endpoints |"))
    assert "not billed for compute or its own storage; a mounted Object Storage bucket still is" in row


@pytest.mark.req("NFR-70")
def test_not_built_yet_matches_the_prd():
    """A README line that says something is "not built yet" names its PRD row, and that row is not Done: when a lane
    lands committed training sets (FR-16.4), the line has to go in the same merge."""
    prd = _read("docs/PRD.md")
    assert _prd_status(prd, "FR-16.4"), "the PRD has no FR-16.4 row"
    fake = re.sub(r"^(\| FR-16\.4 \|.*\| )[^|]+( \|)$", r"\1Done (v0.5)\2", prd, count=1, flags=re.M)
    assert _prd_status(fake, "FR-16.4") == "Done (v0.5)"
    assert _stale_not_built("- **Not built yet:** committed training sets, Merkle proofs.", fake) == ["FR-16.4"]
    assert _stale_not_built("- **Not built yet:** nothing named.", fake) == []

    prior = _sections(_read("README.md"))["Prior art and what is new"]
    for bullet in re.findall(r"^- \*\*Not built yet:\*\*.*$", prior, re.M):
        assert re.search(r"\bFR-\d+\.\d+\b", bullet), "a not-built item names its PRD row"
        assert _stale_not_built(bullet, prd) == [], f"the PRD marks these Done: {_stale_not_built(bullet, prd)}"


NEBIUS_OVERCLAIMS = (
    "hosted on nebius end to end",
    "hosted on nebius",
    "the demo runs on nebius",
    "the demo is served from nebius",
    "served on nebius",
    "served on nebius in production",
    "in production on nebius",
    "production serving on nebius",
    "not on nebius",
    "outside nebius",
    "model you own",
    "deployed on nebius",
    "runs on nebius end to end",
)

# The headline (README, for-judges): links stripped to their text before the comparison.
NEBIUS_HEADLINE = (
    "Built on NVIDIA Nemotron and Nebius: every model call on the live site runs on Nebius Token Factory, "
    "the adapter was retrained on Nebius AI Cloud, and the student was served from a Nebius Serverless Endpoint "
    "in a measured test"
)


def _plain(text: str) -> str:
    """Markdown with each [text](url) reduced to its text and the bold markers dropped."""
    import re

    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("**", "")


@pytest.mark.req("NFR-70")
def test_nebius_claims_stay_within_what_ran():
    """Training ran once on Nebius and the student was served once there for a speed test (27 Oct 2026).

    Serving the demo from Nebius, hosting the site or demo there, production serving and endorsements are unclaimed.
    """
    for rel in (
        "README.md",
        "docs/WHERE_IT_RUNS.md",
        "web/src/data/real.json",
        "docs-site/src/content/docs/guides/for-judges.mdx",
    ):
        text = _read(rel).lower()
        for phrase in NEBIUS_OVERCLAIMS:
            assert phrase not in text, (rel, phrase)
    for rel in ("README.md", "docs-site/src/content/docs/guides/for-judges.mdx"):
        assert NEBIUS_HEADLINE in _plain(_read(rel)), rel
    assert "Every model call on the live site runs on Nebius Token Factory" in _read(
        "docs-site/src/content/docs/guides/what-is-ratchet.mdx"
    )
    readme = _read("README.md")
    assert "borrowed gpu" in readme.lower()
    assert "Nebius AI Cloud" in readme and "L40S" in readme
    # The measured endpoint test is stated, linked, and kept apart from the demo.
    for rel in ("README.md", "docs/WHERE_IT_RUNS.md", "docs-site/src/content/docs/guides/for-judges.mdx", "AGENTS.md"):
        text = _read(rel)
        assert "evidence/student-endpoint-nebius" in text, rel
        assert "not connected" in text.lower(), rel
    assert "student-endpoint-nebius" in _read("web/src/data/real.json")
    # The old "has not run" wording about the endpoint is gone.
    for rel in ("README.md", "docs/WHERE_IT_RUNS.md", "AGENTS.md", "docs-site/src/content/docs/guides/what-is-ratchet.mdx"):
        text = _read(rel).lower()
        assert "serving on a nebius endpoint has not happened" not in text, rel
        assert "the student endpoint and the vm kit have not" not in text, rel
        assert "it has not run yet" not in text, rel
    # What is still unrun stays unclaimed.
    assert "the vm kit and the nightly loop's job have not" in _read("docs/WHERE_IT_RUNS.md").lower()


def _prose(text: str) -> str:
    """Public prose only: no code fences, inline code, link or image targets, HTML tags, or bare URLs and paths."""
    import re

    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\]\([^)]*\)", "] ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+|[\w./-]*[/.][\w./-]*\.(?:png|gif|svg|md|mdx|jpg)", " ", text)
    return text


@pytest.mark.req("NFR-70")
def test_public_text_has_no_demo_or_simulation_wording():
    """The README and the docs site describe only what is real and live (a simulator stays a developer tool)."""
    import re

    files = ["README.md"] + sorted(
        str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "docs-site/src/content/docs").rglob("*.mdx")
    )
    bad = []
    for rel in files:
        for n, line in enumerate(_prose(_read(rel)).splitlines(), 1):
            if re.search(r"\bdemos?\b|simulat", line, re.I):
                bad.append((rel, n, line.strip()[:100]))
    assert not bad, bad[:20]
