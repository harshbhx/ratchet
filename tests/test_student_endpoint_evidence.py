"""The student endpoint speed test (evidence/student-endpoint-nebius).

The manifest matches the files, the benchmark file holds the adapters only, the README's numbers are the file's numbers,
and no secret or private address is in the folder.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "evidence" / "student-endpoint-nebius"


@pytest.mark.req("INV-21")
def test_manifest_matches_the_files():
    manifest = json.loads((EV / "MANIFEST.json").read_text(encoding="utf-8"))
    listed = {f["file"] for f in manifest["files"]}
    on_disk = {p.relative_to(EV).as_posix() for p in EV.rglob("*") if p.is_file()} - {"MANIFEST.json", "README.md"}
    assert listed == on_disk
    for f in manifest["files"]:
        assert hashlib.sha256((EV / f["file"]).read_bytes()).hexdigest() == f["sha256"], f["file"]


@pytest.mark.req("INV-21")
def test_benchmark_holds_the_two_adapters_and_the_readme_matches_it():
    bench = json.loads((EV / "results" / "bench_adapters.json").read_text(encoding="utf-8"))["cold"]
    rows = {(r["adapter"], r["concurrency"]): r for r in bench["results"]}
    assert {a for a, _ in rows} == {"sql-r2", "toolcall"}  # base-model rows stay private
    readme = (EV / "README.md").read_text(encoding="utf-8")
    assert "Base-model rows kept private" in readme
    for (adapter, conc), r in rows.items():
        line = next(x for x in readme.splitlines() if x.startswith(f"| `{adapter}` | {conc} |"))
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert float(cells[3]) == pytest.approx(r["lat_p50_s"], abs=0.005), (adapter, conc)
        assert float(cells[4]) == pytest.approx(r["lat_p95_s"], abs=0.005), (adapter, conc)
        assert int(cells[6]) == r["errors"], (adapter, conc)
    assert bench["max_tokens"] == 256 and bench["temperature"] == 0
    prompts = [json.loads(x) for x in (EV / "data" / "prompts.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(prompts) == 30


@pytest.mark.req("INV-21")
def test_the_served_sql_adapter_is_the_published_round_2_one():
    manifest = json.loads((EV / "MANIFEST.json").read_text(encoding="utf-8"))
    served = manifest["adapters_served"]["sql-r2"].split()[0]
    published = json.loads((ROOT / "evidence" / "text2sql-round2" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert served == published["adapter_model.safetensors_sha256"]
    retrained = json.loads((ROOT / "evidence" / "text2sql-round2-nebius" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert served != retrained["adapter_model.safetensors_sha256"]


@pytest.mark.req("INV-21")
def test_no_secret_or_private_address_in_the_folder():
    patterns = [
        r"tunnel\.applications",
        r"aiendpoint-[a-z0-9]",
        r"aijob-[a-z0-9]",
        r"storagebucket-",
        r"(?i)bearer\s+[A-Za-z0-9_\-]{16,}",
        r"AKIA[A-Z0-9]{12}",
        r"hf_[A-Za-z0-9]{20,}",
        r"nvapi-",
        r"(?i)aws_secret_access_key\s*=",
        r"C:\\Users",
        r"/mnt/c/Users",
    ]
    for p in EV.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            for pat in patterns:
                assert not re.search(pat, text), (p.name, pat)
