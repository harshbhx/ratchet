"""Provenance foundations (ADR 0024): ratchet/merkle.py's sparse Merkle tree (`KeySet`: membership and non-membership
proofs, against a brute-force set and under tampering) and RFC 9162 log trees (the Certificate Transparency test vector,
every audit path up to 64 leaves), and the BOM v2 scaffold (`bom.manifest` sections, `verify` accepting v1 and v2 and
refusing anything else, `run_night` and the mock gateway writing v2)."""

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
    v1_manifest = {  # the shape v0.4 stored; stored v1 documents are never rewritten (INV-14)
        "schema": "ratchet.bom/v1",
        "generator": "ratchet 0.4.0",
        "created_at": "2026-10-20T00:00:00Z",
        "adapter": {"version": 1, "name": "student-v1", "base_model": "base", "day": 1},
        "training": {"examples": 2, "dataset_sha256": "dh", "tasks": {}, "targets": "teacher answers only"},
        "golden": {"examples": 1, "sha256": "gh", "held_out": True},
        "sources": [],
        "eval": {},
    }
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
