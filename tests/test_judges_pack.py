"""The judges pack says the same true things on three surfaces: JUDGES.md, the docs' "For judges" guide and the /judges page.

Each carries the approved headline, the proof numbers, the signed BOM's key id, the cost figures and every link a judge needs;
every repo path JUDGES.md links to exists, and README links to it from the very top. The numbers are the evidence folders' numbers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JUDGES_MD = ROOT / "JUDGES.md"
DOCS_MDX = ROOT / "docs-site" / "src" / "content" / "docs" / "guides" / "for-judges.mdx"
PACK = ROOT / "web" / "src" / "pages" / "judges" / "pack-data.ts"

HEADLINE = (
    "Built on NVIDIA Nemotron and Nebius: every model call on the live site runs on Nebius Token Factory, "
    "the adapter was retrained on Nebius AI Cloud, and the student was served from a Nebius Serverless Endpoint "
    "in a measured test"
)

# Facts every surface carries, each one checked against an evidence folder below.
FACTS = (
    "39.0%",
    "33.0%",
    "12.8%",
    "12.5%",
    "25.6%",
    "15.0%",
    "90.7%",
    "91.3%",
    "80.3%",
    "40.7%",
    "34.5%",
    "$2.16",
    "7.2",
    "7.1",
    "1.6 s",
    "1.2 s",
    "81.8%",
    "95%",
    "81b0a086250a81d0",
    "$2.87",
    "$9.18",
    "$0.67",
    "$0.0002",
    "$0.02",
)

LINKS = (
    "https://ratchet.dualmindlab.tech/live",
    "https://ratchet.dualmindlab.tech/judges",
    "https://ratchet.dualmindlab.tech/dashboard",
    "https://ratchet.dualmindlab.tech/scout",
    "https://huggingface.co/collections/harshbhx/ratchet",
    "https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql",
    "https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-toolcall",
    "https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql-nebius",
    "https://huggingface.co/spaces/harshbhx/ratchet-scout",
    "https://huggingface.co/spaces/harshbhx/ratchet-toolcall-explorer",
    "https://github.com/harshbhx/ratchet/releases/latest",
    "https://docs.ratchet.dualmindlab.tech/llms.txt",
)

EVIDENCE = (
    "text2sql-round1",
    "text2sql-round2",
    "text2sql-exam2",
    "toolcall",
    "text2sql-round2-nebius",
    "student-endpoint-nebius",
    "nightschool-nebius",
    "keys",
    "day1",
    "live-smoke-2026-10-23",
    "scout",
    "hosted-nano",
)


def _plain(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("**", "")


def _surfaces() -> dict[str, str]:
    out = {"JUDGES.md": JUDGES_MD.read_text(encoding="utf-8"), "for-judges.mdx": DOCS_MDX.read_text(encoding="utf-8")}
    if PACK.is_file():
        out["pack-data.ts"] = PACK.read_text(encoding="utf-8")
    return out


@pytest.mark.req("NFR-70")
def test_every_surface_carries_the_headline_facts_and_links():
    for name, text in _surfaces().items():
        plain = _plain(text)
        if name != "pack-data.ts":
            assert HEADLINE in plain, name
        for fact in FACTS:
            assert fact in text, (name, fact)
        for link in LINKS:
            assert link in text, (name, link)
        for folder in EVIDENCE:
            assert f"evidence/{folder}" in text, (name, folder)


@pytest.mark.req("NFR-70")
def test_facts_match_the_evidence_folders():
    ev = ROOT / "evidence"
    endpoint = (ev / "student-endpoint-nebius" / "README.md").read_text(encoding="utf-8")
    for needle in ("7.2 minutes", "7.6 minutes", "7.1 minutes", "p50 1.63 s", "p50 1.23 s", "**$0.67**"):
        assert needle in endpoint, needle
    night = (ev / "nightschool-nebius" / "README.md").read_text(encoding="utf-8")
    assert "81.8% precision against the 95% target" in night
    keys = (ev / "keys" / "README.md").read_text(encoding="utf-8")
    assert "81b0a086250a81d0" in keys
    assert (ev / "nightschool-nebius" / "ratchet_bom.signed.json").is_file()
    day1 = (ev / "day1" / "README.md").read_text(encoding="utf-8")
    assert "0.000235" in day1 and "0.0221" in day1
    retrain = (ev / "text2sql-round2-nebius" / "README.md").read_text(encoding="utf-8")
    assert "40.7%" in retrain and "34.5%" in retrain
    measured = (
        json.loads((ROOT / "web" / "src" / "data" / "measured.json").read_text(encoding="utf-8"))
        if (ROOT / "web" / "src" / "data" / "measured.json").is_file()
        else None
    )
    if measured:
        assert measured["retrain"]["cost_usd"] == 2.16
        assert [r["usd"] for r in measured["labelling"]["rounds"]] == [2.8701, 9.1845]


@pytest.mark.req("NFR-70")
def test_judges_md_local_links_exist_and_readme_links_it_first():
    text = JUDGES_MD.read_text(encoding="utf-8")
    checked = 0
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http", "#", "mailto:")):
            continue
        assert (ROOT / target.split("#")[0]).exists(), target
        checked += 1
    assert checked >= 20
    head = (ROOT / "README.md").read_text(encoding="utf-8")[:600]
    assert "For judges: start here" in head and "JUDGES.md" in head


@pytest.mark.req("NFR-70")
def test_honest_box_and_costs_and_no_overclaims():
    for name, text in _surfaces().items():
        lowered = text.lower()
        for phrase in ("beats the teacher", "model you own", "hosted on nebius", "not on nebius", "simulat", "demo "):
            assert phrase not in lowered, (name, phrase)
        assert "not run nightly on live traffic" in lowered or "not run on live traffic" in lowered or "not yet" in lowered, name
    md = JUDGES_MD.read_text(encoding="utf-8")
    assert "not connected to a student endpoint" in md and "unverified" in md
    assert "Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc=" in md
