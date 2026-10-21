"""Provenance foundations (ADR 0024): ratchet/merkle.py's sparse Merkle tree (`KeySet`: membership and non-membership
proofs, against a brute-force set and under tampering) and RFC 9162 log trees (the Certificate Transparency test vector,
every audit path up to 64 leaves), and the BOM v2 scaffold (`bom.manifest` sections, `verify` accepting v1 and v2 and
refusing anything else, `run_night` and the mock gateway writing v2). Committed training sets (FR-16.4, ADR 0027):
leaf values, `version_leaves` written once, every night committing the exact rows it trained and graded on, the example
key a request is stored under, the proof and held-out endpoints, the `python -m ratchet.proof` verifiers and CLI, an
edited leaf caught against the signed root, v1 BOMs, and admin keys in live mode."""

from __future__ import annotations

import hashlib
import json
import random
import time

import pytest
from fakes import RecordingEvaluator, RecordingTrainer, _examples

from ratchet import bom, merkle
from ratchet.config import Settings
from ratchet.ledger import Ledger, Version
from ratchet.nightschool import dataset
from ratchet.nightschool.orchestrate import run_night
from ratchet.release import ReleaseGate, ReleasePolicy

GOOD_EVAL = {"precision": 0.99, "coverage": 0.9, "threshold": 0.7, "golden_agreement": 0.98, "golden_n": 10}


def _key(rng: random.Random) -> bytes:
    return hashlib.sha256(rng.randbytes(16)).digest()


def _pairs(rng: random.Random, n: int) -> list[tuple[bytes, bytes]]:
    return [(_key(rng), _key(rng)) for _ in range(n)]


# ---------- KeySet: the sparse Merkle tree ----------
def test_keyset_roots():
    assert merkle.KeySet([]).root == merkle.EMPTY and merkle.KeySet([]).size == 0
    k, v = b"\x01" * 32, b"\x02" * 32
    one = merkle.KeySet([(k, v)])
    assert one.root == merkle.leaf_hash(k, v) == hashlib.sha256(b"\x00" + k + v).digest()
    assert merkle.KeySet([(k.hex(), v.hex())]).root == one.root  # hex or bytes
    pairs = _pairs(random.Random(1), 300)
    shuffled = pairs[:]
    random.Random(2).shuffle(shuffled)
    assert merkle.KeySet(pairs).root == merkle.KeySet(shuffled).root  # the set decides, not the order
    assert merkle.KeySet(pairs).root != merkle.KeySet(pairs[1:]).root
    assert merkle.KeySet(pairs).root_hex == merkle.KeySet(pairs).root.hex()


def test_keyset_proves_members_and_non_members():
    rng = random.Random(3)
    pairs = _pairs(rng, 1000)
    ks = merkle.KeySet(pairs)
    assert len(ks) == 1000 and all(k in ks for k, _ in pairs[:50])
    for k, v in pairs:
        proof = ks.prove(k)
        assert merkle.verify(ks.root, k, proof) == "included"
        assert proof["terminal"]["leaf"] == {"key": k.hex(), "value": v.hex()} and ks.value(k) == v
    for _ in range(1000):
        k = _key(rng)
        assert merkle.verify(ks.root, k, ks.prove(k)) == "absent" and ks.value(k) is None and k not in ks
    assert merkle.verify(ks.root.hex(), pairs[0][0].hex(), ks.prove(pairs[0][0].hex())) == "included"


def test_keyset_agrees_with_a_brute_force_set():
    """Sizes 0 to 40 over keys that share long prefixes (the first two bytes take 2 values each), so nodes with one
    empty child are common; every key of the universe verifies 'included' exactly when it is in the set."""
    rng = random.Random(4)
    universe = []
    for i in range(120):
        k = bytearray(_key(rng))
        k[0], k[1] = (i % 2) * 0x80, (i // 2 % 2) * 0x01
        universe.append(bytes(k))
    for size in range(41):
        members = rng.sample(universe, size)
        values = {k: _key(rng) for k in members}
        ks = merkle.KeySet(values.items())
        for k in universe:
            assert merkle.verify(ks.root, k, ks.prove(k)) == ("included" if k in values else "absent"), size


def test_tampered_proofs_never_prove_inclusion():
    rng = random.Random(5)
    pairs = _pairs(rng, 64)
    ks = merkle.KeySet(pairs)
    member, value = pairs[7]
    proof = ks.prove(member)
    for i in range(len(proof["siblings"])):
        for pos in (0, 31):
            bad = json.loads(json.dumps(proof))
            sib = bytearray(bytes.fromhex(bad["siblings"][i]))
            sib[pos] ^= 0x01
            bad["siblings"][i] = sib.hex()
            assert merkle.verify(ks.root, member, bad) == "invalid"
    for pos in range(32):
        bad = json.loads(json.dumps(proof))
        val = bytearray(value)
        val[pos] ^= 0x80
        bad["terminal"]["leaf"]["value"] = val.hex()
        assert merkle.verify(ks.root, member, bad) == "invalid"
        other = bytearray(member)
        other[pos] ^= 0x01
        assert merkle.verify(ks.root, bytes(other), proof) != "included"  # the proof is for another key
    absent = _key(rng)
    assert merkle.verify(ks.root, absent, proof) != "included"
    assert merkle.verify(ks.root, member, ks.prove(member)) != "absent"  # an inclusion proof never reads as absence


def test_malformed_and_overlong_proofs_are_invalid():
    rng = random.Random(6)
    pairs = _pairs(rng, 16)
    ks = merkle.KeySet(pairs)
    member = pairs[0][0]
    good = ks.prove(member)
    # A terminal leaf for another key must share the queried key's first len(siblings) bits.
    stranger = bytes([member[0] ^ 0x80]) + member[1:]
    lying = {"siblings": good["siblings"], "terminal": {"leaf": {"key": stranger.hex(), "value": "00" * 32}}}
    assert merkle.verify(ks.root, member, lying) == "invalid"
    assert merkle.verify(merkle.EMPTY, member, {"siblings": ["00" * 32] * 257, "terminal": {"empty": True}}) == "invalid"
    for bad in (
        {},
        {"siblings": "nope", "terminal": {"empty": True}},
        {"siblings": ["zz"], "terminal": {"empty": True}},
        {"siblings": ["00" * 31], "terminal": {"empty": True}},
        {"siblings": [], "terminal": {"empty": False}},
        {"siblings": [], "terminal": "empty"},
        {"siblings": [], "terminal": {"leaf": {"key": member.hex()}}},
        None,
        [],
    ):
        assert merkle.verify(ks.root, member, bad) == "invalid"  # type: ignore[arg-type]
    assert merkle.verify("not hex", member, good) == "invalid" and merkle.verify(ks.root, b"short", good) == "invalid"
    assert merkle.verify(merkle.EMPTY, member, {"siblings": [], "terminal": {"empty": True}}) == "absent"
    with pytest.raises(ValueError, match="duplicate"):
        merkle.KeySet([pairs[0], pairs[0]])
    with pytest.raises(ValueError):
        merkle.KeySet([(b"short", b"\x00" * 32)])
    with pytest.raises(ValueError):
        merkle.leaf_hash(b"\x00" * 31, b"\x00" * 32)


def test_keyset_builds_and_proves_quickly():
    pairs = _pairs(random.Random(8), 2500)
    started = time.perf_counter()
    ks = merkle.KeySet(pairs)
    for k, _ in pairs[:200]:
        ks.prove(k)
    assert time.perf_counter() - started < 0.5  # about 15 ms here; generous for slow CI machines


# ---------- RFC 9162 log trees ----------
def _reference_mth(leaves: list[bytes]) -> bytes:
    """RFC 9162 section 2.1.1, written out independently of ratchet.merkle."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return hashlib.sha256(b"\x00" + leaves[0]).digest()
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return hashlib.sha256(b"\x01" + _reference_mth(leaves[:k]) + _reference_mth(leaves[k:])).digest()


def test_log_tree_matches_rfc_vectors():
    assert merkle.mth([]) == hashlib.sha256(b"").digest()
    assert merkle.mth([b""]).hex() == "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"
    leaves = [
        b"",
        b"\x00",
        b"\x10",
        b"\x20\x21",
        b"\x30\x31",
        b"\x40\x41\x42\x43",
        bytes(range(0x50, 0x58)),
        bytes(range(0x60, 0x70)),
    ]
    # The Certificate Transparency test vector for these 8 leaves.
    assert merkle.mth(leaves).hex() == "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328"
    for n in range(0, 9):
        assert merkle.mth(leaves[:n]) == _reference_mth(leaves[:n])


def test_every_inclusion_path_verifies_and_alterations_fail():
    """Sizes 1 to 64. The size must come from the same signed record as the root (a receipt signs both): for some
    leaves a path also verifies against the same root under a nearby size (leaf 0 of 3 leaves verifies as leaf 0 of 4),
    so the checks below change the size together with its tree, or by enough that the path no longer fits."""
    rng = random.Random(9)
    previous: tuple[list[bytes], merkle.LogTree] | None = None
    for size in range(1, 65):
        leaves = [rng.randbytes(rng.randint(0, 6)) + bytes([i]) for i in range(size)]
        tree = merkle.LogTree(leaves)
        assert tree.root == merkle.mth(leaves) and tree.size == size
        grown = merkle.LogTree(leaves + [b"one more"])
        for i in range(size):
            path = tree.path(i)
            assert path == merkle.inclusion_path(leaves, i)
            assert merkle.verify_inclusion(leaves[i], i, size, path, tree.root)
            assert merkle.verify_inclusion(leaves[i], i, size, [p.hex() for p in path], tree.root.hex())
            assert not merkle.verify_inclusion(leaves[i] + b"x", i, size, path, tree.root)
            assert not merkle.verify_inclusion(leaves[i], (i + 1) % size, size, path, tree.root) or size == 1
            assert not merkle.verify_inclusion(leaves[i], i, size + 1, path, grown.root)  # another tree, another root
            assert not merkle.verify_inclusion(leaves[i], i, 2 * size + 1, path, tree.root)  # too short for that size
            assert not merkle.verify_inclusion(leaves[i], i, size, path + [tree.root], tree.root)
        if previous is not None:
            old_leaves, old_tree = previous
            assert not merkle.verify_inclusion(old_leaves[0], 0, size, old_tree.path(0), tree.root)
        previous = (leaves, tree)
    tree = merkle.LogTree([b"a", b"b", b"c"])
    for bad in [(b"a", 3, 3), (b"a", -1, 3), (b"a", 0, 0), ("a", 0, 3), (b"a", True, 3)]:
        assert not merkle.verify_inclusion(bad[0], bad[1], bad[2], tree.path(0), tree.root)  # type: ignore[arg-type]
    assert not merkle.verify_inclusion(b"a", 0, 3, ["zz"], tree.root)
    with pytest.raises(ValueError):
        tree.path(3)
    with pytest.raises(ValueError):
        merkle.LogTree(["text"])  # type: ignore[list-item]


# ---------- BOM v2 scaffold ----------
def _rows(n: int, origin: str | None = None, start: int = 0) -> list[dict]:
    rows = []
    for i in range(start, start + n):
        h = hashlib.sha256(f"row {i}".encode()).hexdigest()
        prov = {"teacher": "nvidia/Nemotron-3-Ultra-550b-a55b", "license": "OpenMDW-1.1", "hash": h, "redactions": i % 2}
        if origin is not None:
            prov["origin"] = origin
        rows.append({"task": "ticket-triage", "messages": [], "params": None, "seen": 1, "provenance": prov})
    return rows


# The shape v0.4 stored; stored v1 documents are never rewritten (INV-14).
V1_MANIFEST = {
    "schema": "ratchet.bom/v1",
    "generator": "ratchet 0.4.0",
    "created_at": "2026-10-20T00:00:00Z",
    "adapter": {"version": 1, "name": "student-v1", "base_model": "base", "day": 1},
    "training": {"examples": 2, "dataset_sha256": "dh", "tasks": {}, "targets": "teacher answers only"},
    "golden": {"examples": 1, "sha256": "gh", "held_out": True},
    "sources": [],
    "eval": {},
}


def _v(vid: int = 4) -> Version:
    return Version(vid, 3, "base", f"student-v{vid}", 5, "dh", {"precision": 0.97}, "candidate", 0)


def test_bom_v2_manifest_sections():
    train, golden = _rows(3) + _rows(2, "backfill", 3), _rows(2, start=10)
    m = bom.manifest(_v(), train, golden, redact_pii=True)
    assert m["schema"] == bom.SCHEMA == "ratchet.bom/v2"
    assert m["training"]["origins"] == {"backfill": 2, "live": 3}
    assert m["redactor"]["redact_pii"] is True and m["redactor"]["rules"] > 0 and len(m["redactor"]["sha256"]) == 64
    assert bom.redactor()["sha256"] == m["redactor"]["sha256"] and bom.redactor()["redact_pii"] is None
    assert "commitment" not in m["training"] and "commitment" not in m["golden"]  # only when a night commits the sets
    assert not set(bom.EXTRA_KEYS) & set(m)
    for key in ("generator", "created_at", "adapter", "sources", "eval"):  # v1's fields are all still there
        assert key in m
    root = merkle.KeySet([(bytes.fromhex(r["provenance"]["hash"]), b"\x00" * 32) for r in train]).root_hex
    m = bom.manifest(
        _v(), train, golden, commitments={"training": (root, 5)}, extra={"erasures_honored": [1], "teacher": {"checks": 3}}
    )
    assert m["training"]["commitment"] == {"alg": "ratchet.smt/v1", "root": root, "size": 5}
    assert m["erasures_honored"] == [1] and m["teacher"] == {"checks": 3}
    for bad in (
        {"extra": {"weights": "trust me"}},
        {"commitments": {"weights": (root, 5)}},
        {"commitments": {"training": (root, 4)}},  # the size must be the rows it commits to
        {"commitments": {"training": (root.upper(), 5)}},
        {"commitments": {"golden": "nope"}},
    ):
        with pytest.raises(ValueError):
            bom.manifest(_v(), train, golden, **bad)


def test_bom_verify_accepts_v1_and_v2_and_refuses_unknown_schemas():
    seed, pub = bom.keygen()
    signer = bom.Signer(seed)
    v2 = bom.document(bom.manifest(_v(), _rows(2), _rows(1, start=5)), signer)
    assert bom.verify(v2, pub)[0]
    v1_manifest = V1_MANIFEST
    assert bom.verify(bom.document(v1_manifest, signer), pub)[0]
    assert bom.verify(bom.document(v1_manifest))[0]  # unsigned, sha256 matches
    future = bom.document({**v1_manifest, "schema": "ratchet.bom/v9"}, signer)
    ok, reason = bom.verify(future, pub)
    assert not ok and "unknown schema" in reason
    for junk in ({"manifest": []}, {"manifest": {"schema": None}}, {"nope": 1}, []):
        assert not bom.verify(junk)[0]  # type: ignore[arg-type]


def test_rows_report_their_origin():
    led = Ledger(":memory:")
    _examples(led, train=3, golden=0)
    led.db.execute("UPDATE examples SET origin='backfill' WHERE id=1")  # no writer sets it yet (clean-room backfill)
    origins = sorted(r["provenance"]["origin"] for r in dataset.rows(led, "train"))
    assert origins == ["backfill", "live", "live"]


def test_night_writes_a_v2_bom_with_its_redaction_setting():
    led = Ledger(":memory:")
    _examples(led, train=60, golden=4)
    gate = ReleaseGate(led, ReleasePolicy())
    rep = run_night(led, RecordingTrainer(), RecordingEvaluator(GOOD_EVAL), gate, "base", redact_pii=False)
    m = led.version_bom(rep.version)["manifest"]
    assert m["schema"] == "ratchet.bom/v2" and m["redactor"]["redact_pii"] is False
    assert m["training"]["origins"] == {"live": 60} and m["training"]["examples"] == 60
    assert bom.verify(led.version_bom(rep.version))[0]


def test_mock_gateway_bom_records_the_redaction_setting(gw):
    rt = gw.Runtime(Settings(mode="mock", mock_warmup_days=0, redact_pii=False))
    rep = rt.simulate_day(250)
    assert rep.trained
    assert rt.ledger.version_bom(rep.version)["manifest"]["redactor"]["redact_pii"] is False


# ---------- certified precision: the certificate endpoint and `python -m ratchet.stats check` (FR-16.3, ADR 0026) ----------
CERTIFICATE_KEYS = {"version", "state", "status", "training", "current", "bom", "live", "drift", "assumptions"}


@pytest.mark.req("FR-16.3", "INV-14")
def test_certificate_endpoint_and_stats_check_on_a_signed_bom(gw_env, tmp_path):
    import io

    from fastapi.testclient import TestClient

    from ratchet import stats

    seed, pub = bom.keygen()
    gw = gw_env(RATCHET_BOM_SIGNING_KEY=seed)
    client = TestClient(gw.app)
    assert client.get("/ratchet/versions/1/certificate").status_code == 404  # nothing trained yet
    for _ in range(2):
        assert client.post("/ratchet/demo/day?requests=400").status_code == 200
    first = gw.rt.ledger.version(1)
    assert first is not None and first.state in ("canary", "live")

    body = client.get("/ratchet/versions/1/certificate").json()
    assert set(body) == CERTIFICATE_KEYS and (body["version"], body["state"]) == (1, first.state)
    doc = client.get("/ratchet/versions/1/bom").json()
    signed_eval = doc["manifest"]["eval"]
    assert body["bom"] == {"sha256": doc["sha256"], "signed": True}
    assert body["training"]["certificate"] == signed_eval["certificate"]
    assert body["training"]["frontier"] == signed_eval["frontier"] and body["training"]["per_task"] == signed_eval["per_task"]
    assert body["training"]["calibration"] == "point" and body["training"]["threshold"] == signed_eval["threshold"]
    current = body["current"]
    assert current["certificate"] == first.eval["certificate"] and current["rechecked_day"] == first.eval.get("rechecked_day")
    assert body["status"] == ("certified" if current["certificate"]["certified"] else "not certified")
    assert body["live"] == gw.rt.gate.evidence(first) and body["drift"] is None
    assert len(body["assumptions"]) == 3 and all(isinstance(a, str) for a in body["assumptions"])
    assert client.get("/ratchet/versions/999/certificate").status_code == 404
    # A nightly re-score replaces the version's eval: `current` and the status follow it, `training` stays the signed one.
    flipped = {**first.eval["certificate"], "certified": not first.eval["certificate"]["certified"]}
    gw.rt.ledger.update_eval(1, {**first.eval, "certificate": flipped, "rechecked_day": 5})
    again = client.get("/ratchet/versions/1/certificate").json()
    assert again["current"]["certificate"] == flipped and again["current"]["rechecked_day"] == 5
    assert again["training"] == body["training"] and again["status"] != body["status"]
    # A version from before v0.5 (no certificate) and before v0.4 (no BOM) still answers, with nothing to certify.
    from fakes import _version

    old_vid = _version(gw.rt.ledger, "retired", {"precision": 0.97, "coverage": 0.9, "threshold": 0.8})
    old_body = client.get(f"/ratchet/versions/{old_vid}/certificate").json()
    assert (old_body["status"], old_body["training"], old_body["bom"], old_body["drift"]) == ("no certificate", None, None, None)
    assert old_body["current"]["certificate"] is None and old_body["current"]["threshold"] == 0.8
    assert old_body["live"] == gw.rt.gate.evidence(gw.rt.ledger.version(old_vid))  # it once served: audits may exist

    path = tmp_path / "bom.json"
    path.write_text(json.dumps(doc))

    def check(*args) -> tuple[int, str]:
        out = io.StringIO()
        return stats.main(["check", str(path), *args], out=out), out.getvalue()

    code, out = check("--public-key", pub)
    assert code == 0 and "OK: signature valid" in out and "every number recomputed from the signed counts" in out
    assert "OK: certificate: " in out and "OK: frontier: 10 thresholds" in out

    edited = json.loads(json.dumps(doc))
    edited["manifest"]["eval"]["certificate"]["errors"] += 1
    path.write_text(json.dumps(edited))
    code, out = check("--public-key", pub)
    assert code == 1 and "FAIL: sha256 does not match" in out and "FAIL: certificate.precision" in out
    # Re-hashed so the document matches itself again: the numbers still do not follow from the counts.
    path.write_text(json.dumps(bom.document(edited["manifest"])))
    code, out = check()
    assert code == 1 and "OK: unsigned; sha256 matches" in out and "do not follow from the signed counts" in out
    assert check("--public-key", pub)[0] == 1  # and it is no longer signed by the key

    old = bom.document({**doc["manifest"], "eval": {"precision": 0.97, "threshold": 0.8}})  # a BOM from before v0.5
    path.write_text(json.dumps(old))
    code, out = check()
    assert code == 0 and "no certificate in this BOM" in out
    path.write_text("{not json")
    assert check() == (1, "FAIL: not JSON\n")
    assert stats.main(["check", str(tmp_path / "missing.json")], out=io.StringIO()) == 2


# ---------- committed training sets (FR-16.4, ADR 0027) ----------
ADMIN_KEY = "proof-admin-key-0123456789abcdef"
API_KEY = "proof-api-key-0123456789abcdef"
TRIAGE = '{"category": "account", "priority": "high", "reply": "Mailed you at bob@example.com"}'


def _leaf_preimage(row: dict) -> bytes:
    """`dataset.leaf_value`'s preimage, written out independently: canonical JSON of the label, teacher, license and
    redaction count."""
    prov = row["provenance"]
    obj = {"completion": row["messages"][-1]["content"], "license": prov["license"], "redactions": prov["redactions"]}
    obj["teacher"] = prov["teacher"]
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.mark.req("FR-16.4")
def test_commitment_binds_each_examples_label_teacher_license_and_redactions():
    rows = _rows(5)
    for i, r in enumerate(rows):
        r["messages"] = [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"answer {i} é"}]
    root, size, leaves = dataset.commitment(rows)
    assert size == 5 and [k for k, _ in leaves] == sorted(r["provenance"]["hash"] for r in rows)
    values = {k: v for k, v in leaves}
    for r in rows:
        assert values[r["provenance"]["hash"]] == hashlib.sha256(_leaf_preimage(r)).hexdigest()
    ks = merkle.KeySet((bytes.fromhex(r["provenance"]["hash"]), dataset.leaf_value(r)) for r in rows)
    assert root == ks.root_hex
    # Each bound field moves the leaf and so the root: a different label, teacher, license or redaction count.
    for edit in (
        lambda r: r["messages"][-1].update(content="another answer"),
        lambda r: r["provenance"].update(teacher="nvidia/nemotron-3-super-120b-a12b"),
        lambda r: r["provenance"].update(license="other"),
        lambda r: r["provenance"].update(redactions=r["provenance"]["redactions"] + 1),
    ):
        changed = json.loads(json.dumps(rows))
        edit(changed[2])
        assert dataset.commitment(changed)[0] != root
    assert dataset.commitment(list(reversed(rows)))[0] == root  # the set decides, not the order
    assert dataset.commitment([]) == (merkle.EMPTY.hex(), 0, [])
    with pytest.raises(ValueError, match="duplicate"):
        dataset.commitment(rows + rows[:1])


@pytest.mark.req("FR-16.4", "INV-17")
def test_version_leaves_are_written_once_and_found_by_key():
    import sqlite3

    led = Ledger(":memory:")
    a, b = led.create_version("base", "student-v1", 2, "dh", {}), led.create_version("base", "student-v2", 2, "dh", {})
    k1, k2, k3, v = "1" * 64, "2" * 64, "3" * 64, "a" * 64
    assert led.set_version_leaves(a, "train", [(k2, v), (k1, v)]) == 2
    assert led.version_leaves(a, "train") == [(k1, v), (k2, v)] and led.version_leaves(a, "golden") == []
    assert led.set_version_leaves(a, "golden", [(k3, v)]) == 1
    assert led.set_version_leaves(b, "train", [(k1, v)]) == 1
    assert led.set_version_leaves(b, "golden", []) == 0  # an empty split writes nothing; the BOM says size 0
    assert led.versions_containing(k1) == [a, b] and led.versions_containing(v) == []
    assert led.versions_containing(k3) == [] and led.versions_containing(k3, "golden") == [a]
    for bad in (
        (a, "train", [(k3, v)]),  # the split already has leaves: written once
        (99, "train", [(k1, v)]),  # no such version
        (b, "golden", [(k1, v), (k1, v)]),  # duplicate keys
        (b, "golden", [("A" * 64, v)]),  # not lowercase hex
        (b, "golden", [("zz", v)]),
        (b, "golden", [(k1, None)]),
        (b, "validation", [(k1, v)]),
    ):
        with pytest.raises(ValueError):
            led.set_version_leaves(*bad)  # type: ignore[arg-type]
    assert led.version_leaves(b, "golden") == [] and len(led.version_leaves(a, "train")) == 2  # nothing written
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):  # INV-17: the database refuses edits too
        led.db.execute("UPDATE version_leaves SET value=? WHERE key=?", ("b" * 64, k1))


@pytest.mark.req("FR-16.4", "INV-3", "INV-14")
def test_night_commits_the_exact_rows_it_trained_and_graded_on():
    led = Ledger(":memory:")
    _examples(led, train=60, golden=6)
    trainer, evaluator = RecordingTrainer(), RecordingEvaluator(GOOD_EVAL)
    gate = ReleaseGate(led, ReleasePolicy())
    first = run_night(led, trainer, evaluator, gate, "base")
    led.set_version(first.version, "live", 100)  # no release in flight, so the next night trains
    _examples(led, train=60, golden=0, prefix="later")
    second = run_night(led, trainer, evaluator, gate, "base")
    for rep, call in ((first, trainer.calls[0]), (second, trainer.calls[1])):
        m = led.version_bom(rep.version)["manifest"]
        golden_rows = [e for e in evaluator.calls if e.adapter_uri == f"student-v{rep.version}"][0].golden_rows
        for split, section, rows in (("train", "training", call.rows), ("golden", "golden", golden_rows)):
            leaves = led.version_leaves(rep.version, split)
            assert m[section]["commitment"]["size"] == m[section]["examples"] == len(rows) == len(leaves)
            assert dataset.commitment(rows) == (m[section]["commitment"]["root"], len(rows), leaves)
        train_root = m["training"]["commitment"]["root"]
        for key, _ in led.version_leaves(rep.version, "golden"):  # INV-3, from the signed root alone
            assert merkle.verify(train_root, key, merkle.KeySet(led.version_leaves(rep.version, "train")).prove(key)) == "absent"
    old_key = trainer.calls[0].rows[0]["provenance"]["hash"]
    new_key = trainer.calls[1].rows[-1]["provenance"]["hash"]
    assert led.versions_containing(old_key) == [first.version, second.version]  # cumulative retraining (ADR 0004)
    assert led.versions_containing(new_key) == [second.version]


def test_a_failed_training_job_commits_nothing():
    led = Ledger(":memory:")
    _examples(led, train=60, golden=4)

    class Failing(RecordingTrainer):
        def train(self, train_rows, base_model, name):
            raise RuntimeError("GPU job failed")

    with pytest.raises(RuntimeError):
        run_night(led, Failing(), RecordingEvaluator(GOOD_EVAL), ReleaseGate(led, ReleasePolicy()), "base")
    assert led.versions() == [] and led.db.execute("SELECT COUNT(*) FROM version_leaves").fetchone()[0] == 0


@pytest.mark.req("FR-16.4", "INV-12")
def test_request_key_is_the_stored_hash():
    """`proof.request_key` and `resolve_key` compute the key `Ratchet._store_example` stores a teacher answer under:
    normalised messages (a developer role, text parts), the stored conditions, and PII redaction on or off."""
    from fakes import OPEN_TEACHER, RecordingProvider

    from ratchet import proof
    from ratchet.openai_compat import normalize_messages, validate_chat_request
    from ratchet.providers import GenParams
    from ratchet.router import Ratchet

    bodies = [
        {"messages": [{"role": "user", "content": "Locked out. I'm bob@example.com"}]},
        {
            "messages": [
                {"role": "developer", "content": "Be brief; escalations to +44 20 7946 0958"},
                {"role": "user", "content": [{"type": "text", "text": "Card 4111 1111 1111 1111 declined"}]},
            ],
            "stop": ["\n\nEND bob@example.com"],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,  # sampling is not a stored condition
        },
    ]
    for redact_pii in (True, False):
        led = Ledger(":memory:")
        r = Ratchet(
            Settings(redact_pii=redact_pii, teacher_model=OPEN_TEACHER), led, RecordingProvider(TRIAGE), lambda v: (None, "x")
        )
        for body in bodies:
            req = validate_chat_request(body)
            messages, _ = normalize_messages(req.messages)
            params = GenParams.from_request(req)
            r.handle(messages, "ticket-triage", params=params)  # cold start: the teacher answers and it is stored
            stored = led.examples()[-1]["hash"]
            assert proof.request_key("ticket-triage", messages, params, redact_pii=redact_pii, policy=r.gen) == stored
            assert proof.resolve_key({**body, "task": "ticket-triage"}, redact_pii=redact_pii, policy=r.gen) == stored
        assert len(led.examples()) == 2 and led.examples()[1]["params"] is not None


def test_resolve_key_validates_like_the_gateway():
    from ratchet import proof

    plain = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hello"}]
    key = proof.request_key("default", plain)
    assert proof.resolve_key({"messages": plain}) == key  # no task: 'default', as a request without one
    developer = [{"role": "developer", "content": "Be brief."}, {"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    assert proof.resolve_key({"messages": developer}) == key  # normalised as the gateway stores it
    assert proof.resolve_key({"messages": plain, "metadata": {"ratchet_task": "ticket-triage"}}) == proof.request_key(
        "ticket-triage", plain
    )
    assert proof.resolve_key({"messages": plain, "task": "faq"}) == proof.request_key("faq", plain)  # task wins
    assert proof.resolve_key({"key": key.upper()}) == key
    image = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}]}]
    for bad, needle in (
        ([], "JSON object"),
        ({}, "give either"),
        ({"task": "default"}, "give either"),
        ({"key": key, "task": "default"}, "alone"),
        ({"key": "ab"}, "64 hex"),
        ({"key": 7}, "64 hex"),
        ({"messages": "hello"}, "messages"),
        ({"messages": plain, "task": "no spaces allowed"}, "task name"),
        ({"messages": plain, "task": 3}, "task name"),
        ({"messages": image}, "image_url"),
    ):
        with pytest.raises(ValueError, match=needle):
            proof.resolve_key(bad)
    with pytest.raises(ValueError, match="never stored"):  # a teacher-only part: no adapter can have trained on it
        proof.resolve_key({"messages": image}, modalities=frozenset({"text", "image_url"}))


def _write(path, obj) -> str:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def _cli(*argv: str) -> tuple[int, str]:
    import io

    from ratchet import proof

    out = io.StringIO()
    return proof.main(list(argv), out=out), out.getvalue()


@pytest.mark.req("FR-16.4", "INV-3", "INV-6", "INV-14")
def test_proofs_and_the_held_out_certificate_through_the_gateway(gw_env, tmp_path):
    from fakes import _version
    from fastapi.testclient import TestClient

    from ratchet import proof

    seed, pub = bom.keygen()
    gw = gw_env(RATCHET_BOM_SIGNING_KEY=seed)
    c = TestClient(gw.app)
    for _ in range(2):
        assert c.post("/ratchet/demo/day?requests=400").status_code == 200
    led = gw.rt.ledger
    doc = c.get("/ratchet/versions/1/bom").json()
    m = doc["manifest"]
    assert m["schema"] == "ratchet.bom/v2" and bom.verify(doc, pub)[0]
    assert m["training"]["commitment"]["size"] == m["training"]["examples"] > 0
    assert m["golden"]["commitment"]["size"] == m["golden"]["examples"] > 0
    assert m["training"]["commitment"]["root"] == merkle.KeySet(led.version_leaves(1, "train")).root_hex

    train_ex = c.get("/ratchet/demo/sample-example?split=train&version=1").json()
    golden_ex = c.get("/ratchet/demo/sample-example?split=golden&version=1").json()
    assert set(train_ex) == set(golden_ex) == {"version", "split", "task", "messages"}  # the prompt, never the answer
    assert all(msg["role"] != "assistant" for msg in train_ex["messages"])
    included = c.post("/ratchet/versions/1/proof?split=train", json=train_ex).json()
    assert included["result"] == "included" and included["value"] == dict(led.version_leaves(1, "train"))[included["key"]]
    assert (included["root"], included["size"], included["bom_sha256"]) == (
        m["training"]["commitment"]["root"],
        m["training"]["examples"],
        doc["sha256"],
    )
    absent = c.post("/ratchet/versions/1/proof", json=golden_ex).json()  # split defaults to train
    assert absent["result"] == "absent" and "value" not in absent and absent["split"] == "train"
    assert c.post("/ratchet/versions/1/proof?split=golden", json=golden_ex).json()["result"] == "included"
    assert c.post("/ratchet/versions/1/proof?split=train", json={"key": included["key"].upper()}).json() == included
    for p, expect in ((included, "included"), (absent, "absent")):
        ok, lines = proof.verify_proof(p, doc, pub, expect)
        assert ok, lines
    assert proof.verify_proof(included, doc, pub)[1][0].startswith(f"OK: example {included['key'][:12]} IS in the training set")
    if led.live() is None:  # v1 is still a canary after two days: no default version to sample
        r = c.get("/ratchet/demo/sample-example")
        assert (r.status_code, r.json()) == (404, {"detail": "no live version yet"})
        led.set_version(1, "live", 100)
    default = c.get("/ratchet/demo/sample-example").json()  # the live version's
    assert default["version"] == led.live().id and default["split"] == "train"

    cert = c.get("/ratchet/versions/1/heldout").json()
    assert cert["held_out"] is True and len(cert["proofs"]) == len(cert["golden"]["leaves"]) == m["golden"]["examples"]
    ok, lines = proof.verify_heldout(cert, doc, pub)
    assert ok and lines == [
        f"OK: all {m['golden']['examples']} golden examples provably absent from the training set of student-v1 "
        f"({m['training']['examples']} leaves); BOM signature valid (Ed25519, key {bom.key_id(pub)}, trusted key)"
    ]

    # The CLI: 0 when everything checks, 1 on any failure.
    bom_file = _write(tmp_path / "bom.json", doc)
    inc, abs_, held = (_write(tmp_path / f"{n}.json", o) for n, o in (("in", included), ("out", absent), ("held", cert)))
    assert _cli("verify", inc, "--bom", bom_file, "--public-key", pub, "--expect", "included")[0] == 0
    assert _cli("verify", abs_, "--bom", bom_file, "--expect", "absent")[0] == 0  # embedded key only: still checked
    assert _cli("heldout", held, "--bom", bom_file, "--public-key", pub) == (0, lines[0] + "\n")
    code, out = _cli("verify", abs_, "--bom", bom_file, "--public-key", pub, "--expect", "included")
    assert code == 1 and "expected 'included', but the example is 'absent'" in out
    _, other = bom.keygen()
    code, out = _cli("verify", inc, "--bom", bom_file, "--public-key", other)
    assert code == 1 and "FAIL: BOM bad signature" in out
    tampered = json.loads(json.dumps(included))
    sib = tampered["proof"]["siblings"][0]
    tampered["proof"]["siblings"][0] = ("0" if sib[0] != "0" else "1") + sib[1:]
    code, out = _cli("verify", _write(tmp_path / "t.json", tampered), "--bom", bom_file, "--public-key", pub)
    assert code == 1 and "invalid proof" in out
    lying = {**absent, "result": "included"}  # the file's own claim is never trusted
    code, out = _cli("verify", _write(tmp_path / "l.json", lying), "--bom", bom_file, "--public-key", pub)
    assert code == 1 and "the file says 'included', but the proof shows 'absent'" in out
    for edit, needle in (
        ({"root": "ab" * 32}, "root mismatch"),  # a proof against another tree (another version's, say)
        ({"bom_sha256": "0" * 64}, "issued for another BOM"),
        ({"version": 2}, "it is for version 2"),
        ({"split": "golden"}, "invalid proof"),  # checked against the golden root the BOM signs
    ):
        code, out = _cli("verify", _write(tmp_path / "e.json", {**included, **edit}), "--bom", bom_file, "--public-key", pub)
        assert code == 1 and needle in out, (edit, out)
    bad_cert = json.loads(json.dumps(cert))
    bad_cert["golden"]["leaves"][0][1] = "0" * 64
    assert _cli("heldout", _write(tmp_path / "h1.json", bad_cert), "--bom", bom_file)[0] == 1
    bad_cert = json.loads(json.dumps(cert))
    bad_cert["proofs"].pop(next(iter(bad_cert["proofs"])))
    code, out = _cli("heldout", _write(tmp_path / "h2.json", bad_cert), "--bom", bom_file)
    assert code == 1 and "1 golden examples have no proof" in out
    (tmp_path / "junk.json").write_text("{not json")
    assert _cli("verify", str(tmp_path / "junk.json"), "--bom", bom_file) == (
        1,
        f"FAIL: the proof {tmp_path / 'junk.json'} is not JSON\n",
    )
    assert _cli("verify", str(tmp_path / "missing.json"), "--bom", bom_file)[0] == 2

    # Errors: unknown versions, versions without a commitment, a bad split or body.
    old = _version(led, "retired")  # no BOM, as a version trained before v0.5
    assert c.post("/ratchet/versions/999/proof", json={"key": included["key"]}).status_code == 404
    r = c.post(f"/ratchet/versions/{old}/proof", json={"key": included["key"]})
    assert r.status_code == 404 and "commitment not available" in r.json()["detail"]
    assert (
        c.get(f"/ratchet/versions/{old}/heldout").status_code == 404 and c.get("/ratchet/versions/999/heldout").status_code == 404
    )
    assert c.post("/ratchet/versions/1/proof?split=test", json={"key": included["key"]}).status_code == 400
    assert c.post("/ratchet/versions/1/proof", json={"key": "nope"}).status_code == 400
    assert c.post("/ratchet/versions/1/proof", json={"messages": []}).status_code == 400
    assert c.get("/ratchet/demo/sample-example?split=test").status_code == 400
    assert c.get("/ratchet/demo/sample-example?version=999").status_code == 404
    assert c.get(f"/ratchet/demo/sample-example?version={old}").status_code == 404


@pytest.mark.req("FR-16.4", "INV-17")
def test_an_edited_leaf_fails_against_the_signed_root(gw_env, tmp_path):
    from fastapi.testclient import TestClient

    from ratchet import proof

    gw = gw_env()
    c = TestClient(gw.app)
    assert c.post("/ratchet/demo/day?requests=300").status_code == 200
    led = gw.rt.ledger
    doc = led.version_bom(1)
    key, value = led.version_leaves(1, "train")[0]
    # Someone with the database file drops the trigger and edits a leaf (INV-17 stops it through SQLite's API).
    led.db.execute("DROP TRIGGER version_leaves_no_update")
    led.db.execute("UPDATE version_leaves SET value=? WHERE version_id=1 AND split='train' AND key=?", ("0" * 64, key))
    led.db.commit()
    for _ in range(2):
        r = c.post("/ratchet/versions/1/proof", json={"key": key})
        assert r.status_code == 500 and "do not match the commitment its BOM signs" in r.json()["detail"]
    assert c.get("/ratchet/versions/1/heldout").status_code == 500
    assert [e["kind"] for e in led.events()].count("integrity") == 1  # once, however often it is asked
    # A proof built from the edited leaves does not verify against the root the BOM signs.
    forged = merkle.KeySet(led.version_leaves(1, "train"))
    p = {"version": 1, "split": "train", "key": key, "root": forged.root_hex, "result": "included", "proof": forged.prove(key)}
    ok, lines = proof.verify_proof(p, doc)
    assert not ok and any("root mismatch" in line for line in lines) and any("invalid proof" in line for line in lines)
    ok, lines = proof.verify_proof({k: v for k, v in p.items() if k != "root"}, doc)
    assert not ok and lines == ["FAIL: invalid proof: it does not fold to the training set root the BOM signs"]
    assert merkle.verify(forged.root, key, forged.prove(key)) == "included"  # only the signed root tells them apart


@pytest.mark.req("FR-16.4", "INV-14")
def test_a_v1_bom_still_verifies_and_has_nothing_to_prove_against(tmp_path):

    seed, pub = bom.keygen()
    doc = bom.document(V1_MANIFEST, bom.Signer(seed))
    assert bom.verify(doc, pub)[0] and bom.commitment(V1_MANIFEST, "train") is None
    p = {"split": "train", "key": "1" * 64, "proof": {"siblings": [], "terminal": {"empty": True}}}
    code, out = _cli("verify", _write(tmp_path / "p.json", p), "--bom", _write(tmp_path / "b.json", doc), "--public-key", pub)
    assert code == 1 and "no set commitment for its training set" in out
    code, out = _cli("heldout", _write(tmp_path / "h.json", {"golden": {"leaves": []}}), "--bom", str(tmp_path / "b.json"))
    assert code == 1 and "no set commitments" in out
    for junk in (None, {}, {"training": {"commitment": {"alg": "other", "root": "0" * 64, "size": 1}}}):
        assert bom.commitment(junk, "train") is None
    assert bom.commitment({"golden": {"commitment": {"alg": "ratchet.smt/v1", "root": "0" * 64, "size": True}}}, "golden") is None


@pytest.mark.req("FR-16.4")
def test_verifiers_never_raise_on_malformed_files():
    """`verify_proof` and `verify_heldout` read files anyone can write: every malformed one is a FAIL line, never an
    exception (a regression: a non-list `siblings` raised TypeError while the failure line was being written)."""
    from ratchet import proof

    train = merkle.KeySet([("1" * 64, "a" * 64), ("2" * 64, "b" * 64)])
    golden = merkle.KeySet([("3" * 64, "c" * 64)])
    m = {
        "schema": bom.SCHEMA,
        "adapter": {"version": 1, "name": "student-v1"},
        "training": {"commitment": {"alg": merkle.SMT_ALG, "root": train.root_hex, "size": 2}},
        "golden": {"commitment": {"alg": merkle.SMT_ALG, "root": golden.root_hex, "size": 1}},
    }
    doc = bom.document(m)
    good = {"split": "train", "key": "1" * 64, "proof": train.prove("1" * 64)}
    assert proof.verify_proof(good, doc, expect="included")[0]
    assert proof.verify_proof({**good, "value": "A" * 64}, doc)[0]  # hex case does not matter
    for bad in (
        None,
        [],
        "proof",
        {"split": ["train"], "key": "1" * 64},
        {**good, "key": None},
        {**good, "proof": None},
        {**good, "proof": {"siblings": 5, "terminal": {"empty": True}}},
        {**good, "proof": {"siblings": "abc", "terminal": {"empty": True}}},
        {**good, "proof": {"siblings": [[1]], "terminal": []}},
        {**good, "proof": {"siblings": [], "terminal": {"leaf": {"key": 1, "value": None}}}},
        {**good, "value": ["not", "hex"]},
        {**good, "value": "b" * 64},
        {**good, "version": {"a": 1}},
        {**good, "root": 7},
    ):
        ok, lines = proof.verify_proof(bad, doc)
        assert not ok and lines and all(line.startswith("FAIL: ") for line in lines), (bad, lines)
    cert = {"golden": {"leaves": [["3" * 64, "c" * 64]]}, "proofs": {"3" * 64: train.prove("3" * 64)}}
    assert proof.verify_heldout(cert, doc)[0]
    for bad in (
        None,
        {"golden": None},
        {"golden": {"leaves": "3"}},
        {"golden": {"leaves": [["3" * 64]]}},
        {"golden": {"leaves": [[1, 2]]}},
        {"golden": {"leaves": [[["x"], {"a": 1}]]}},
        {"golden": {"leaves": [["3" * 64, "c" * 64], ["3" * 64, "c" * 64]]}},
        {**cert, "proofs": [1]},
        {**cert, "proofs": {"3" * 64: {"siblings": 7, "terminal": 1}}},
        {**cert, "training": "stated"},
        {**cert, "training": {"root": 5, "size": []}},
    ):
        ok, lines = proof.verify_heldout(bad, doc)
        assert not ok and lines and all(line.startswith("FAIL: ") for line in lines), (bad, lines)
    assert not proof.verify_proof(good, {"manifest": "junk"})[0] and not proof.verify_heldout(cert, 3)[0]


@pytest.mark.req("FR-16.4", "INV-8", "INV-13")
def test_proof_endpoints_need_an_admin_key_in_live_mode(gw, tmp_path, monkeypatch):
    from fakes import OPEN_TEACHER, FakeOpenAIServer
    from fastapi.testclient import TestClient

    def runtime(**overrides):
        settings = Settings(
            mode="live",
            db_path=str(tmp_path / f"live-{len(overrides)}.db"),
            teacher_base="https://tokenfactory.test/v1",
            teacher_api_key="tf-key",
            teacher_model=OPEN_TEACHER,
            student_base="",
            tasks_file="",
            bom_signing_key="",
            tavily_api_key="",
            mock_warmup_days=0,
            allow_unauthenticated=True,  # the runtime without API keys below starts (ADR 0061)
            **{"admin_keys": "", "api_keys": "", **overrides},
        )
        rt = gw.Runtime(settings, transports={"teacher": FakeOpenAIServer("ok").transport})
        monkeypatch.setattr(gw, "rt", rt)
        return TestClient(gw.app)

    key = {"key": "1" * 64}
    c = runtime(admin_keys=f"ops:{ADMIN_KEY}", api_keys=f"app:{API_KEY}")
    assert c.post("/ratchet/versions/1/proof", json=key).status_code == 401
    assert c.get("/ratchet/versions/1/heldout").status_code == 401
    as_app = {"Authorization": f"Bearer {API_KEY}"}
    assert c.post("/ratchet/versions/1/proof", json=key, headers=as_app).status_code == 403  # a client is not an admin
    assert c.get("/ratchet/versions/1/heldout", headers=as_app).status_code == 403
    as_ops = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert c.post("/ratchet/versions/1/proof", json=key, headers=as_ops).status_code == 404  # admitted; nothing trained
    assert c.get("/ratchet/demo/sample-example", headers=as_ops).status_code == 403  # mock-only (INV-8)
    c = runtime()
    r = c.post("/ratchet/versions/1/proof", json=key)
    assert (r.status_code, r.json()) == (403, {"detail": "admin API disabled: set RATCHET_ADMIN_KEYS"})


@pytest.mark.req("FR-16.4")
def test_cli_key_prints_the_stored_key(tmp_path, monkeypatch):
    from ratchet import proof

    for var in ("RATCHET_MERGE_SYSTEM", "RATCHET_TEACHER_PARAMS", "RATCHET_TEACHER_MODALITIES"):
        monkeypatch.delenv(var, raising=False)
    msgs = [{"role": "user", "content": "Locked out. I'm bob@example.com"}]
    path = _write(tmp_path / "m.json", msgs)
    assert _cli("key", "--task", "ticket-triage", "--messages", path) == (0, proof.request_key("ticket-triage", msgs) + "\n")
    raw = proof.request_key("ticket-triage", msgs, redact_pii=False)
    assert _cli("key", "--task", "ticket-triage", "--messages", path, "--no-redact") == (0, raw + "\n")
    body = _write(tmp_path / "b.json", {"messages": msgs, "metadata": {"ratchet_task": "ticket-triage"}, "stop": ["END"]})
    from ratchet.providers import GenParams

    want = proof.request_key("ticket-triage", msgs, GenParams(stop=("END",)))
    assert _cli("key", "--messages", body) == (0, want + "\n") and want != proof.request_key("ticket-triage", msgs)
    (tmp_path / "junk.json").write_text("[{")
    for argv in (["--messages", str(tmp_path / "junk.json")], ["--messages", str(tmp_path / "none.json")]):
        assert _cli("key", *argv)[0] == 2
    assert _cli("key", "--messages", _write(tmp_path / "s.json", "text"))[0] == 2
    assert _cli("key", "--messages", _write(tmp_path / "e.json", []))[0] == 2  # no messages: the gateway's 400
