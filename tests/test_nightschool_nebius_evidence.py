"""The first Night School run on Nebius (evidence/nightschool-nebius).

The manifest matches the files, the README's numbers are the data files' numbers, the verdict is a rejection that was
never made live, and no address, id or key is in the folder.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "evidence" / "nightschool-nebius"
ADAPTER = "dc9ad4b9d2575e7a41120cb30bb023ee7aec35e997f4735ac819b545baddd00b"


def _text(rel: str) -> str:
    return (EV / rel).read_text(encoding="utf-8")


def _eval() -> dict:
    """The version 2 evaluation from the ledger dump (the line after `VERSION 2 rejected`)."""
    lines = _text("data/ledger_dump.txt").splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("VERSION 2 rejected"))
    return json.loads(lines[i + 1])


@pytest.mark.req("INV-21")
def test_manifest_matches_the_files():
    manifest = json.loads((EV / "MANIFEST.json").read_text(encoding="utf-8"))
    listed = {f["file"] for f in manifest["files"]}
    on_disk = {p.relative_to(EV).as_posix() for p in EV.rglob("*") if p.is_file()} - {"MANIFEST.json", "README.md"}
    assert listed == on_disk
    for f in manifest["files"]:
        data = (EV / f["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"] and len(data) == f["bytes"], f["file"]
    assert manifest["adapter"]["sha256"] == ADAPTER and manifest["verdict"] == "rejected"


@pytest.mark.req("INV-21")
def test_the_readme_numbers_are_the_data_files_numbers():
    readme = _text("README.md")
    ev = _eval()
    assert (ev["golden_n"], ev["test_n"], ev["certificate"]["served"], ev["certificate"]["errors"]) == (458, 229, 11, 2)
    assert f"{ev['golden_agreement']:.1%}" == "17.9%" and f"{ev['coverage']:.1%}" == "4.8%"
    assert f"{ev['precision']:.1%}" == "81.8%" and round(ev["threshold"], 3) == 0.996
    assert ev["certificate"]["target"] == 0.95 and ev["certificate"]["certified"] is False
    assert ev["judge_n"] == 11 and f"{ev['certificate']['lcb']:.0%}" == "53%"
    for needle in ("17.9%", "4.8%", "0.996", "81.8%", "9 of 11", "95%", "458", "3,864", "4,322", "**Rejected.**", ADAPTER):
        assert needle in readme, needle
    # The version rows and the events say rejected, never admitted.
    dump = _text("data/ledger_dump.txt")
    assert "VERSION 2 rejected student-v1" in dump and "rejected v2: golden precision 81.8% < 95%" in dump
    assert "trained v2 on 3864 examples" in dump and "4322 new, 0 replaced, 0 duplicates, 0 invalid, 0 refused" in dump
    seed_text = _text("logs/seed_import.txt")
    seed = json.loads(seed_text[seed_text.index("{") :])
    assert (seed["rows"], seed["stored"], seed["invalid"], seed["refused"]) == (4322, 4322, 0, 0)


@pytest.mark.req("INV-21")
def test_the_training_numbers_match_the_logs():
    readme = _text("README.md")
    saved = _text("logs/training_job3_SAVED.log").splitlines()
    final = ast.literal_eval(next(line for line in saved if line.startswith("{'train_runtime'")))
    assert float(final["train_runtime"]) == 1478 and float(final["train_loss"]) == pytest.approx(0.1164)
    assert float(final["mean_token_accuracy"]) == pytest.approx(0.9752) and float(final["epoch"]) == 2
    assert float(final["train_runtime"]) * float(final["train_steps_per_second"]) == pytest.approx(966, abs=2)
    assert 3864 * 2 / 8 == 966  # two epochs of 3,864 rows at batch size 8
    losses = [float(m.group(1)) for line in saved if (m := re.match(r"\{'loss': '([0-9.]+)'", line))]
    assert 0.14 <= losses[0] <= 0.16 and 0.06 <= losses[-1] <= 0.10
    failed_log = _text("logs/training_job2_FAILED_SAVE.log")
    failed = ast.literal_eval(next(line for line in failed_log.splitlines() if line.startswith("{'train_runtime'")))
    assert float(failed["train_runtime"]) == 1472 and "Operation not permitted" in failed_log
    for needle in (
        "1,478 s",
        "966 steps",
        "0.975",
        "1,472 s",
        "EPERM",
        "b1b0f3a1",
        "120 s",
        "reuse_night.py",
        "Nebius bill decides",
    ):
        assert needle in readme, needle
    # The first attempt ended in the student timeout; the rerun is the one that finished.
    assert "upstream_timeout" in _text("logs/night_run1.log")
    assert "admitted=False, reason='rejected'" in _text("logs/night_eval_rerun.log")


@pytest.mark.req("INV-21")
def test_the_readme_states_the_limits_honestly():
    readme = _text("README.md")
    lowered = readme.lower()
    for needle in (
        "never made live",
        "not connected to any of this",
        "not run nightly on live traffic",
        "no saving is claimed",
        "four jobs",
        "run again on the saved adapter",
        "now upstreamed",
    ):
        assert needle in lowered, needle
    assert not re.search(r"(?i)\bdemo\b|simulat", readme)
    assert not re.search(r"(?i)we saved|saves? \d+%|cost reduction", readme)


@pytest.mark.req("INV-21")
def test_the_driver_changes_only_the_trainer_and_the_minimum():
    code = _text("scripts/reuse_night.py")
    assert "min_new_examples=0" in code and "class ReuseTrainer" in code and 'live.main(["night"])' in code
    assert "student-v1" in code and not re.search(r"https?://", code)


@pytest.mark.req("INV-21")
def test_no_secret_address_or_id_in_the_folder():
    patterns = [
        r"tunnel\.applications",
        r"aiendpoint-[a-z0-9]{10,}",
        r"aijob-[a-z0-9]{10,}",
        r"storagebucket-[a-z0-9]{10,}",
        r"computeinstance-[a-z0-9]{10,}",
        r"vpcsubnet-[a-z0-9]{10,}",
        r"\be00[a-z0-9]{12,}",
        r"(?i)bearer\s+[A-Za-z0-9_\-]{16,}",
        r"AKIA[A-Z0-9]{12}",
        r"hf_[A-Za-z0-9]{20,}",
        r"nvapi-",
        r"(?i)aws_secret_access_key\s*=",
        r"C:\\Users",
        r"/mnt/c/Users",
        r"/c/Users",
        r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        r"(?i)ratchet-demo",
        r"ubuntu@",
        r"storage\.[a-z0-9-]+\.nebius",
        r"\x1b\[",
    ]
    allowed_hosts = {"huggingface.co", "www.nvidia.com"}  # the licence links inside the adapter's bill of materials
    for p in EV.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            for pat in patterns:
                assert not re.search(pat, text), (p.name, pat)
            hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", text))
            assert hosts <= allowed_hosts, (p.name, hosts - allowed_hosts)


@pytest.mark.req("INV-21")
def test_the_bom_was_signed_afterwards_with_the_published_key():
    from ratchet import bom

    pub = (ROOT / "evidence" / "keys" / "bom-signing-2026-10-29.pub").read_text(encoding="utf-8").strip()
    original = json.loads(_text("ratchet_bom.json"))
    signed = json.loads(_text("ratchet_bom.signed.json"))
    assert bom.key_id(pub) == "81b0a086250a81d0" == signed["signature"]["key_id"]
    # The run's own record is unsigned, and signing changed nothing but the signature.
    assert original["signature"] is None and bom.verify(original) == (True, "unsigned; sha256 matches")
    assert signed["manifest"] == original["manifest"] and signed["sha256"] == original["sha256"]
    ok, reason = bom.verify(signed, pub)
    assert ok and "trusted key" in reason, reason
    notice = (EV / "NOTICE.md").read_bytes()
    assert bom.check_notice(signed, notice) == (True, "NOTICE.md matches the BOM")
    # A different key, or an edited manifest, fails.
    other = bom.keygen()[1]
    assert bom.verify(signed, other)[0] is False
    edited = json.loads(json.dumps(signed))
    edited["manifest"]["training"]["examples"] += 1
    assert bom.verify(edited, pub)[0] is False
    assert original["manifest"]["created_at"].startswith("2026-10-28")
    assert "Signed on 29 Oct 2026 with Ed25519 key `81b0a086250a81d0`, after the run" in _text("README.md")
