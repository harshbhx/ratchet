"""Committed training sets: prove from an adapter's signed BOM alone that an example was, or was not, in its training or
golden set (FR-16.4, ADR 0027).

    python -m ratchet.proof key --task ticket-triage --messages request.json      # the key a request is stored under
    python -m ratchet.proof verify proof.json --bom bom.json --public-key K [--expect included|absent]
    python -m ratchet.proof heldout heldout.json --bom bom.json --public-key K   # every golden row absent from training

Every trained adapter's BOM (schema `ratchet.bom/v2`) signs a `merkle.KeySet` root per split: each example's key (its
hash, `examples.hash`) mapped to `dataset.leaf_value` (a hash of its label, teacher, license and redaction count). A proof
from `POST /ratchet/versions/{id}/proof` is a membership or non-membership proof against one of those roots; the
held-out certificate from `GET /ratchet/versions/{id}/heldout` is the golden set's leaves plus a non-membership proof of
each golden key against the training root, which turns invariants 3 and 6 (golden rows never trained on, precision
measured on unseen rows) into something anyone with the public key can check offline.

What a proof shows: the operator's binding commitment, at training time, to the dataset it trained on. It does not show
that the weights came from that dataset.

Exit status: 0 when everything checks, 1 on any failure (a bad signature, a root that is not the BOM's, an invalid proof,
an unexpected result, a file that is not JSON), 2 on bad input (a missing file, a request that does not validate).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from . import bom, merkle, tasks
from .ledger import STORED_PARAM_KEYS, example_hash, target_params
from .openai_compat import TEXT_MODALITY, APIError, Limits, normalize_messages, parse_modalities, validate_chat_request
from .providers import GenParams, GenPolicy
from .redact import redact_json, redact_messages

SPLITS = ("train", "golden")
SET_NAMES = {"train": "training set", "golden": "golden set"}
# Body keys of a proof request that are not part of the chat request whose key is computed.
PROOF_KEYS = frozenset({"key", "task"})


def _hex64(value: Any) -> str | None:
    """A 32-byte hex string (an example key), lowercased, or None."""
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
        return None
    return value.lower()


def request_key(
    task: str,
    messages: list[dict[str, Any]],
    params: GenParams | None = None,
    *,
    redact_pii: bool = True,
    policy: GenPolicy | None = None,
) -> str:
    """The example key (hash) a teacher answer to this request is stored under, computed as `Ratchet._store_example`
    stores it: the messages (already validated and normalised, as the gateway hands them to the cascade) and the
    conditions the serving teacher answered under (`STORED_PARAM_KEYS` of `policy.plan(params).serve`; audits keep the
    same ones), both redacted when `redact_pii` (RATCHET_REDACT_PII). `test_request_key_is_the_stored_hash` keeps the
    two in step."""
    conditions = None
    if params is not None:
        serve = (policy or GenPolicy()).plan(params).serve
        conditions = target_params({k: getattr(serve, k) for k in STORED_PARAM_KEYS})
    if redact_pii:
        found: Counter[str] = Counter()
        messages = redact_messages(messages, found)
        if conditions:
            conditions = redact_json(conditions, found)
    return example_hash(task, messages, conditions)


def resolve_key(
    body: Any,
    *,
    redact_pii: bool = True,
    policy: GenPolicy | None = None,
    limits: Limits | None = None,
    modalities: frozenset[str] = frozenset({TEXT_MODALITY}),
    merge_system: bool = False,
) -> str:
    """The example key a proof request names: `{"key": hex}`, or a chat request (`messages` and any other
    `/v1/chat/completions` field; `response_format`, `stop`, `tools` and `tool_choice` decide its stored conditions)
    with its `task` (or `metadata.ratchet_task`; default 'default'). The request is validated and normalised as the
    gateway does (`limits`, `modalities`, `merge_system` are the gateway's), so the key is the one its teacher answer was
    stored under. ValueError, with a message for the client, for anything else."""
    if not isinstance(body, Mapping):
        raise ValueError("the body must be a JSON object: {'key': hex} or {'task', 'messages', ...}")
    if "key" in body:
        if set(body) != {"key"}:
            raise ValueError("give either 'key' alone, or 'task' and 'messages'")
        key = _hex64(body["key"])
        if key is None:
            raise ValueError("key must be an example hash: 64 hex characters")
        return key
    if "messages" not in body:
        raise ValueError("give either 'key', or 'task' and 'messages'")
    try:
        req = validate_chat_request({k: v for k, v in body.items() if k not in PROOF_KEYS}, limits)
        messages, flags = normalize_messages(req.messages, modalities, merge_system)
    except APIError as e:
        raise ValueError(f"{e.param}: {e.message}" if e.param else e.message) from None
    if flags.multimodal:
        raise ValueError("a request with parts only the teacher reads is never stored, so no adapter was trained on it")
    name = body.get("task", req.task)
    if name is None:
        name = "default"
    if not isinstance(name, str) or not tasks.valid_name(name):
        raise ValueError("task must be a task name: 1 to 64 letters, digits, '_', '.' or '-'")
    return request_key(name, messages, GenParams.from_request(req), redact_pii=redact_pii, policy=policy)


# ---------------------------------------------------------------------------------------------------------------------
# Verifiers: they read untrusted files, so every malformed input is a failure line, never an exception.
# ---------------------------------------------------------------------------------------------------------------------
def _adapter(m: Any) -> tuple[Any, str]:
    a = m.get("adapter") if isinstance(m, dict) else None
    if not isinstance(a, dict):
        return None, "this adapter"
    return a.get("version"), str(a.get("name") or f"version {a.get('version')}")


def _bom_line(bom_doc: Any, public_key: str | None) -> tuple[bool, str, Any]:
    """(the BOM verifies, what to print about it, its manifest)."""
    ok, reason = bom.verify(bom_doc, public_key)
    m = bom_doc.get("manifest") if isinstance(bom_doc, dict) else None
    return ok, f"BOM {reason}", m


def _same_bom(doc: Mapping[str, Any], bom_doc: Any, m: Any, problems: list[str]) -> None:
    """A proof or certificate names the BOM and version it was issued for; both must be the ones given."""
    version, _ = _adapter(m)
    if doc.get("version") is not None and doc.get("version") != version:
        problems.append(f"it is for version {doc.get('version')!r}, but the BOM is version {version!r}'s")
    sha = bom_doc.get("sha256") if isinstance(bom_doc, dict) else None
    if doc.get("bom_sha256") is not None and doc.get("bom_sha256") != sha:
        problems.append("it was issued for another BOM (bom_sha256 differs)")


def _report(bom_ok: bool, bom_line: str, problems: list[str], success: str) -> tuple[bool, list[str]]:
    if bom_ok and not problems:
        return True, [success]
    return False, [f"FAIL: {p}" for p in ([] if bom_ok else [bom_line]) + problems]


def verify_proof(doc: Any, bom_doc: Any, public_key: str | None = None, expect: str | None = None) -> tuple[bool, list[str]]:
    """Check a proof (the body `POST /ratchet/versions/{id}/proof` returns) against a BOM document: the BOM's hash and
    signature (`bom.verify`, against `public_key` when given), that the proof's root is the one the BOM signs for its
    split, and `merkle.verify` against that signed root, never the file's own claims. (ok, lines): ok only when every
    check passes and, with `expect`, the result is the one expected."""
    bom_ok, bom_line, m = _bom_line(bom_doc, public_key)
    problems: list[str] = []
    if not isinstance(doc, Mapping):
        return _report(bom_ok, bom_line, ["not a proof (a JSON object from /ratchet/versions/{id}/proof)"], "")
    split, key = doc.get("split"), _hex64(doc.get("key"))
    if split not in SPLITS or key is None:
        problem = f"split must be 'train' or 'golden', not {split!r}" if split not in SPLITS else "key must be 64 hex characters"
        return _report(bom_ok, bom_line, [problem], "")
    committed = bom.commitment(m, split)
    if committed is None:
        problem = f"this BOM has no set commitment for its {SET_NAMES[split]} (schema v1, or written before v0.5)"
        return _report(bom_ok, bom_line, [problem], "")
    if doc.get("root") is not None and doc.get("root") != committed["root"]:
        stated = str(doc.get("root"))[:16]
        problems.append(f"root mismatch: the proof is against {stated}…, the BOM signs {committed['root'][:16]}…")
    _same_bom(doc, bom_doc, m, problems)
    proof = doc.get("proof")
    result = merkle.verify(committed["root"], key, proof) if isinstance(proof, Mapping) else merkle.INVALID
    if result == merkle.INVALID:
        problems.append(f"invalid proof: it does not fold to the {SET_NAMES[split]} root the BOM signs")
    else:
        claimed = doc.get("result")
        if claimed is not None and claimed != result:
            problems.append(f"the file says {claimed!r}, but the proof shows {result!r}")
        if result == merkle.INCLUDED and doc.get("value") is not None:
            leaf_value = _hex64(proof["terminal"]["leaf"]["value"])  # type: ignore[index]
            if _hex64(doc.get("value")) != leaf_value:
                problems.append("the value given is not the leaf the proof includes")
        if expect is not None and result != expect:
            problems.append(f"expected {expect!r}, but the example is {result!r}")
    _, adapter = _adapter(m)
    raw = proof.get("siblings") if isinstance(proof, Mapping) else None
    siblings = len(raw) if isinstance(raw, list) else 0  # a valid proof's siblings are a list; anything else is invalid
    verb = "IS" if result == merkle.INCLUDED else "IS NOT"
    success = (
        f"OK: example {key[:12]} {verb} in the {SET_NAMES[split]} of {adapter} "
        f"({committed['size']:,} leaves, {siblings} siblings); {bom_line}"
    )
    return _report(bom_ok, bom_line, problems, success)


def verify_heldout(cert: Any, bom_doc: Any, public_key: str | None = None) -> tuple[bool, list[str]]:
    """Check a held-out certificate (the body `GET /ratchet/versions/{id}/heldout` returns) against a BOM document:
    the BOM's hash and signature, that the golden leaves rebuild the golden root and size the BOM signs, and that every
    golden key has a proof verifying 'absent' against the signed training root. (ok, lines)."""
    bom_ok, bom_line, m = _bom_line(bom_doc, public_key)
    if not isinstance(cert, Mapping):
        return _report(bom_ok, bom_line, ["not a held-out certificate (a JSON object from /ratchet/versions/{id}/heldout)"], "")
    training, golden = bom.commitment(m, "train"), bom.commitment(m, "golden")
    if training is None or golden is None:
        return _report(bom_ok, bom_line, ["this BOM has no set commitments (schema v1, or written before v0.5)"], "")
    problems: list[str] = []
    _same_bom(cert, bom_doc, m, problems)
    stated = cert.get("training")
    if stated is not None and (
        not isinstance(stated, Mapping) or (stated.get("root"), stated.get("size")) != (training["root"], training["size"])
    ):
        problems.append("the training root or size it states is not the one the BOM signs")
    g = cert.get("golden")
    leaves = g.get("leaves") if isinstance(g, Mapping) else None
    try:
        if not isinstance(leaves, list) or not all(isinstance(p, list) and len(p) == 2 for p in leaves):
            raise ValueError("golden.leaves must be a list of [key, value] pairs")
        keyset = merkle.KeySet((k, v) for k, v in leaves)
    except (ValueError, TypeError) as e:
        return _report(bom_ok, bom_line, [*problems, f"the golden leaves cannot be read: {e}"], "")
    if (keyset.root_hex, keyset.size) != (golden["root"], golden["size"]):
        problems.append(
            f"the golden leaves do not rebuild the golden set the BOM signs ({keyset.size:,} leaves, root "
            f"{keyset.root_hex[:16]}…; the BOM signs {golden['size']:,} leaves, root {golden['root'][:16]}…)"
        )
    proofs = cert.get("proofs")
    proofs = proofs if isinstance(proofs, Mapping) else {}
    found: dict[str, list[str]] = {merkle.ABSENT: [], merkle.INCLUDED: [], merkle.INVALID: [], "missing": []}
    for k in keyset.keys():
        proof = proofs.get(k.hex())
        found["missing" if not isinstance(proof, Mapping) else merkle.verify(training["root"], k, proof)].append(k.hex()[:12])
    for result, what in (
        (merkle.INCLUDED, "ARE in the training set"),
        (merkle.INVALID, "have an invalid proof"),
        ("missing", "have no proof"),
    ):
        if found[result]:
            shown = ", ".join(found[result][:5]) + (", …" if len(found[result]) > 5 else "")
            problems.append(f"{len(found[result])} golden examples {what}: {shown}")
    _, adapter = _adapter(m)
    success = (
        f"OK: all {keyset.size:,} golden examples provably absent from the training set of {adapter} "
        f"({training['size']:,} leaves); {bom_line}"
    )
    return _report(bom_ok, bom_line, problems, success)


# ---------------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------------
class _BadInput(Exception):
    """A file that cannot be read (exit 2)."""


def _read(path: str) -> str:
    try:
        return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise _BadInput(f"cannot read {path}: {e.strerror or e}") from None


def _json(path: str, what: str, out: TextIO) -> tuple[bool, Any]:
    try:
        return True, json.loads(_read(path))
    except ValueError:
        print(f"FAIL: {what} {path} is not JSON", file=out)
        return False, None


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """`python -m ratchet.proof key|verify|heldout`. Exit status 0, 1 on a failed check, 2 on bad input."""
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="python -m ratchet.proof", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("key", help="print the example key a request's teacher answer is stored under")
    k.add_argument("--messages", required=True, help="a JSON file: a messages list or a chat request body (- for stdin)")
    k.add_argument("--task", default=None, help="the task (default: the body's metadata.ratchet_task, else 'default')")
    k.add_argument("--no-redact", action="store_true", help="the gateway runs with RATCHET_REDACT_PII=0")
    v = sub.add_parser("verify", help="check a membership or non-membership proof against a BOM")
    v.add_argument("path", help="the proof (POST /ratchet/versions/{id}/proof), or - for stdin")
    v.add_argument("--bom", required=True, help="the adapter's BOM (GET /ratchet/versions/{id}/bom)")
    v.add_argument("--public-key", default=None, help="the base64 Ed25519 public key you trust")
    v.add_argument("--expect", choices=(merkle.INCLUDED, merkle.ABSENT), default=None)
    h = sub.add_parser("heldout", help="check that every golden example is absent from the training set")
    h.add_argument("path", help="the certificate (GET /ratchet/versions/{id}/heldout), or - for stdin")
    h.add_argument("--bom", required=True, help="the adapter's BOM (GET /ratchet/versions/{id}/bom)")
    h.add_argument("--public-key", default=None, help="the base64 Ed25519 public key you trust")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "key":
            return _key(args, out)
        ok, doc = _json(args.path, "the proof" if args.cmd == "verify" else "the certificate", out)
        ok_bom, bom_doc = _json(args.bom, "the BOM", out)
        if not (ok and ok_bom):
            return 1
        if args.cmd == "verify":
            passed, lines = verify_proof(doc, bom_doc, args.public_key, args.expect)
        else:
            passed, lines = verify_heldout(doc, bom_doc, args.public_key)
    except _BadInput as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for line in lines:
        print(line, file=out)
    return 0 if passed else 1


def _key(args: argparse.Namespace, out: TextIO) -> int:
    from .config import Settings  # noqa: PLC0415 - only `key` reads the gateway's settings

    try:
        body = json.loads(_read(args.messages))
    except ValueError:
        print(f"error: {args.messages} is not JSON", file=sys.stderr)
        return 2
    if isinstance(body, list):
        body = {"messages": body}
    if not isinstance(body, dict):
        print("error: the file must hold a messages list or a chat request object", file=sys.stderr)
        return 2
    if args.task is not None:
        body = {**body, "task": args.task}
    body.pop("key", None)
    try:
        s = Settings()
        key = resolve_key(
            body,
            redact_pii=not args.no_redact,
            policy=s.gen_policy(),
            limits=s.limits(),
            modalities=parse_modalities(s.teacher_modalities),
            merge_system=s.merge_system,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(key, file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
