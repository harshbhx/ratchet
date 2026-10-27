"""Every real number the story page hard-codes (web/src/data/real-costs.json) is the one in evidence/.

The hero loop, "Every request is rent", "Watch it learn" and "The honest bill" print real dollars next to the exam scores:
what Ultra's labels cost in each round, what one call costs at list prices, the training rows and the "1 of 86" precision-bar
fact. The exam and tool-calling scores come from web/src/data/exams.json and toolcall.json, which tests/test_exam_numbers.py and
tests/test_toolcall_results.py hold to the saved answers. Here the costs file is held to the manifests and READMEs it cites, so
a number typed from memory cannot drift. Simulation numbers are invariant 20's
(test_story_simulation_numbers_match_reference_run).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COSTS = json.loads((ROOT / "web/src/data/real-costs.json").read_text(encoding="utf-8"))
LANDING = ROOT / "web/src/pages/landing"
WEB = ROOT / "web"


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_labelling_costs_are_the_teacher_manifests():
    r1 = json.loads(_text(COSTS["labelling"]["round1"]["source"]))
    r2 = json.loads(_text(COSTS["labelling"]["round2"]["source"]))
    assert COSTS["labelling"]["round1"]["usd"] == r1["spent_usd_estimate"]
    assert COSTS["labelling"]["round2"]["usd"] == r2["spent_usd_estimate"]
    assert COSTS["labelling"]["round1"]["calls"] == r1["teacher_calls"]
    assert COSTS["labelling"]["round2"]["calls"] == r2["teacher_calls"]
    assert COSTS["labelling"]["round1"]["rows"] == r1["rows"]["train"]
    assert r1["prices_per_million"] == r2["prices_per_million"]
    p = r1["prices_per_million"]
    assert COSTS["labelling"]["ultra_usd_per_m_tokens"] == {"input": p["in"], "output": p["out"]}
    # The round 2 cost is recomputable from its logged tokens at those prices.
    t = r2["tokens"]
    assert abs((t["in"] * p["in"] + t["out"] * p["out"]) / 1e6 - r2["spent_usd_estimate"]) < 0.001


def test_round2_row_count_and_precision_bar_are_in_the_readme():
    readme = _text("evidence/text2sql-round2/README.md")
    rows = f"{COSTS['labelling']['round2']['rows_total']:,}"
    assert f"({rows} rows)" in readme
    bar = COSTS["precision_bar"]
    assert bar["source"] == "evidence/text2sql-round2/README.md"
    flat = " ".join(readme.split())
    assert (
        f"At the {bar['target_pct']}% precision target the confidence gate passes {bar['answered']} of {bar['of']} rows" in flat
    )


def test_per_call_prices_are_the_hosted_nano_calibration_runs():
    readme = _text("evidence/hosted-nano/README.md")
    flat = " ".join(readme.split())
    p = COSTS["per_call"]
    assert p["source"] == "evidence/hosted-nano/README.md"
    # "Ultra $0.00081 per triage call (...) and $0.00215 per reader call (...); the Nano's 3 samples $0.000044 and $0.000055."
    m = re.search(
        r"Ultra \$([0-9.]+) per triage call .*? and \$([0-9.]+) per reader call .*?"
        r"the Nano's 3 samples \$([0-9.]+) and \$([0-9]+\.[0-9]*[0-9])",
        flat,
    )
    assert m, "the README's per-call sentence changed"
    assert [float(x) for x in m.groups()] == [p["ultra_triage"], p["ultra_reader"], p["nano_triage"], p["nano_reader"]]


def test_the_story_prints_no_simulated_costs():
    """The story page reads real data only: the simulation file, its scrubber and its rollback chart are gone, and the bill
    shows the real tiles and the gateway's own report or a plain no-break-even note."""
    assert not (WEB / "src" / "lib" / "sim.ts").exists()
    for name in ("hero-flow.tsx", "rent.tsx", "learn.tsx", "bill.tsx"):
        assert "@/lib/sim" not in (LANDING / "sections" / name).read_text(encoding="utf-8"), name
    for gone in ("rollback-story.tsx", "cost-chart.tsx"):
        assert not (LANDING / "sections" / gone).exists(), gone
    learn = (LANDING / "sections/learn.tsx").read_text(encoding="utf-8")
    assert "<RealCurve" in learn and "SimBadge" not in learn
    bill = (LANDING / "sections/bill.tsx").read_text(encoding="utf-8")
    assert "<RealCostTiles" in bill and "No break-even yet" in bill
    for path in LANDING.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8").replace("simulated={", "")
        assert not re.search(r"simulat", text, re.I), path.name
