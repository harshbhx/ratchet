"""Signed bill of materials for each trained adapter.

`/ratchet/bom` describes the whole ledger. This module writes one document per adapter, at
training time: which teachers and licenses produced its training examples, the dataset hash,
the golden-set hash and the evaluation it passed. The document is stored with the version and
signed with Ed25519 when `RATCHET_BOM_SIGNING_KEY` is set, so anyone holding the public key can
check that a given adapter was trained on what its BOM says, and that nobody edited it later.

    python -m ratchet.bom keygen                          # new signing key + public key
    python -m ratchet.bom verify bom.json --public-key K  # check a downloaded BOM
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__, licenses
from .ledger import Version

SCHEMA = "ratchet.bom/v1"
ALG = "Ed25519"


def canonical(obj: Any) -> bytes:
    """The exact bytes that are hashed and signed: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _rows_hash(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(r["provenance"]["hash"].encode())
    return h.hexdigest()


def manifest(version: Version, train_rows: list[dict[str, Any]], golden_rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources: Counter[tuple[str, str]] = Counter()
    for r in train_rows:
        sources[(r["provenance"]["teacher"], r["provenance"]["license"])] += 1
    out_sources = []
    for (teacher, lic_name), n in sorted(sources.items()):
        lic = licenses.lookup(teacher)
        out_sources.append(
            {
                "teacher": teacher,
                "license": lic_name,
                "license_url": lic.url if lic else None,
                "distill_ok": bool(lic and lic.distill_ok),
                "examples": n,
            }
        )
    return {
        "schema": SCHEMA,
        "generator": f"ratchet {__version__}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "adapter": {
            "version": version.id,
            "name": version.adapter_uri,
            "base_model": version.base_model,
            "day": version.day,
        },
        "training": {
            "examples": len(train_rows),
            "dataset_sha256": version.dataset_hash,
            "tasks": dict(sorted(Counter(r["task"] for r in train_rows).items())),
            "targets": "teacher answers only",
            "examples_with_pii_redacted": sum(1 for r in train_rows if r["provenance"].get("redactions")),
        },
        "golden": {"examples": len(golden_rows), "sha256": _rows_hash(golden_rows), "held_out": True},
        "sources": out_sources,
        "eval": version.eval,
    }


class Signer:
    """Ed25519 signer. Needs the `cryptography` package (`pip install "ratchet-distill[sign]"`)."""

    def __init__(self, seed_b64: str):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError as e:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                'RATCHET_BOM_SIGNING_KEY is set but `cryptography` is missing: pip install "ratchet-distill[sign]"'
            ) from e
        try:
            seed = base64.b64decode(seed_b64.strip(), validate=True)
        except ValueError as e:
            raise ValueError("RATCHET_BOM_SIGNING_KEY is not valid base64") from e
        if len(seed) != 32:
            raise ValueError("RATCHET_BOM_SIGNING_KEY must be the base64 of a 32-byte Ed25519 seed")
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self.public_key = _public_b64(self._key)
        self.key_id = key_id(self.public_key)

    def sign(self, data: bytes) -> str:
        return base64.b64encode(self._key.sign(data)).decode()


def _public_b64(private_key: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def key_id(public_key_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:16]


def document(m: dict[str, Any], signer: Signer | None = None) -> dict[str, Any]:
    """Wrap a manifest with its SHA-256 and, when a signer is configured, its signature."""
    data = canonical(m)
    sig = None
    if signer is not None:
        sig = {"alg": ALG, "key_id": signer.key_id, "public_key": signer.public_key, "value": signer.sign(data)}
    return {"manifest": m, "sha256": hashlib.sha256(data).hexdigest(), "signature": sig}


def verify(doc: dict[str, Any], public_key_b64: str | None = None) -> tuple[bool, str]:
    """(ok, reason). Pass the public key you trust: the key embedded in the document only proves
    the document matches itself, not who signed it."""
    try:
        data = canonical(doc["manifest"])
    except (KeyError, TypeError):
        return False, "not a Ratchet BOM document"
    if hashlib.sha256(data).hexdigest() != doc.get("sha256"):
        return False, "sha256 does not match the manifest (edited after it was written)"
    sig = doc.get("signature")
    if not sig:
        return (False, "unsigned") if public_key_b64 else (True, "unsigned; sha256 matches")
    if sig.get("alg") != ALG:
        return False, f"unknown signature algorithm {sig.get('alg')!r}"
    key = public_key_b64 or sig.get("public_key", "")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(base64.b64decode(sig["value"]), data)
    except ImportError:  # pragma: no cover
        return False, 'cannot check the signature: pip install "ratchet-distill[sign]"'
    except (InvalidSignature, ValueError, KeyError):
        return False, "bad signature"
    trust = "trusted key" if public_key_b64 else "embedded key only; pass --public-key to check the signer"
    return True, f"signature valid ({ALG}, key {key_id(key)}, {trust})"


def keygen() -> tuple[str, str]:
    """(private seed base64, public key base64)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    k = Ed25519PrivateKey.generate()
    seed = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return base64.b64encode(seed).decode(), _public_b64(k)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m ratchet.bom", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="print a new signing key and its public key")
    v = sub.add_parser("verify", help="check a BOM document (file or - for stdin)")
    v.add_argument("path")
    v.add_argument("--public-key", default=None, help="the base64 public key you trust")
    args = ap.parse_args(argv)
    if args.cmd == "keygen":
        seed, pub = keygen()
        print(f"RATCHET_BOM_SIGNING_KEY={seed}")
        print(f"# public key (publish this): {pub}")
        print(f"# key id: {key_id(pub)}")
        return 0
    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    ok, reason = verify(json.loads(text), args.public_key)
    print(("OK: " if ok else "FAIL: ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
