# ADR 0027: Committed training sets: sparse Merkle roots in every BOM, with membership, non-membership and held-out proofs

- Status: Accepted
- Date: 2026-10-20

## Context

A v1 bill of materials ([ADR 0010](0010-signed-adapter-bom.md)) commits to `dataset_sha256`, a SHA-256 over the concatenated example hashes of the training set, and `golden.sha256`, the same over the golden rows. That has three gaps:

- Nobody can check one example against it without the whole list of hashes, which the operator would have to publish.
- It cannot show that an example is absent. Invariant 3 (golden rows are never trained on) and invariant 6 (precision is measured on held-out rows) are claims: tested in this repository's CI, but not checkable by anyone holding only an adapter's BOM.
- It binds example keys only. The label (the teacher answer trained on), the teacher, its license and the redaction count are not part of what is signed per example.

The roadmap also had an open item: a copy of each adapter's BOM inside its folder in the bucket, so a copied adapter carries its provenance.

[ADR 0024](0024-evidence-foundations.md) built the pieces: `merkle.KeySet` (a collapsed sparse Merkle tree with membership and non-membership proofs), the append-only `version_leaves` table (INV-17) and BOM schema v2 with an optional `commitment` per split. This ADR uses them.

## Decision

### What is committed

For every adapter, one `KeySet` per split ('train' and 'golden'), over exactly the rows Night School handed to the trainer and to the golden eval:

- **Key**: the example's hash (`examples.hash`): SHA-256 over the task, the redacted messages and, when there are any, the stored generation conditions (`ledger.example_hash`). Anyone holding the request can recompute it.
- **Value**: `dataset.leaf_value(row)` = SHA-256 of the canonical JSON (`bom.canonical`) of `{completion, teacher, license, redactions}`: the stored teacher answer the student was trained on (cleaned and redacted), the teacher that wrote it, its license and the number of PII placeholders in it. A membership proof therefore binds what an example was trained on and under which license, not only that its key was there.

The tree is `merkle.KeySet` as specified in ADR 0024: an empty node is 32 zero bytes, a node with one key collapses to `sha256(0x00 || key || value)`, any other node is `sha256(0x01 || left || right)`, split on the key's bits (most significant first). The root depends on the set alone. A proof is the sibling hashes along the key's path (depth 0 first) and what the path ends at: a leaf (the key itself for a member; for a non-member, the one key that shares its path) or an empty node.

### When it is written

`run_night` builds both commitments (`dataset.commitment(rows)` → root, size, sorted leaves) before the paid training job, so a duplicate key fails the night before any GPU time. Right after `create_version` it writes the leaves (`Ledger.set_version_leaves`: one transaction per split, refused when the split already has leaves or a pair is not 64 lowercase hex characters; the triggers refuse any later UPDATE, DELETE or replacing INSERT, INV-17), and only then evaluates and signs. `bom.manifest(commitments=...)` puts `{alg: 'ratchet.smt/v1', root, size}` into `training.commitment` and `golden.commitment`, and refuses a size that is not the split's row count. Every signed root therefore has its leaves in the ledger. A failed training job writes no version and no leaves. No random draw and no request-path code is involved, so the reference simulation and the three reference workloads do not move.

In live mode `python -m ratchet.live night` also writes the BOM to `{RATCHET_DATA_DIR}/adapters/<name>/ratchet_bom.json` (an adapter name that is not one folder name is skipped, never followed as a path) and runs `RATCHET_SYNC_CMD` again, so the bucket's adapter folder holds the BOM beside the weights. A failed upload fails the command after the night is reported; the night itself stands.

### Serving proofs

- `POST /ratchet/versions/{id}/proof?split=train|golden` takes `{"key": hex}` or the chat request itself (`task` and `messages`, and any other `/v1/chat/completions` field). The request is validated, normalised and redacted exactly as the gateway stores it (`proof.resolve_key`, the gateway's own limits, modalities and `RATCHET_MERGE_SYSTEM`; `test_request_key_is_the_stored_hash` checks it against `Ratchet._store_example` with redaction on and off), so the key is the one a teacher answer to that request was stored under. A request with a part only the teacher reads is a 400: it is never stored, so no adapter trained on it. The answer is `{version, split, key, alg, root, size, result, proof, bom_sha256}` and `value` when included.
- `GET /ratchet/versions/{id}/heldout` is the held-out certificate: the golden leaves, and for each golden key a non-membership proof against the training root.
- Both need an admin key (`RATCHET_ADMIN_KEYS`), because with guessable texts the proof endpoint is a membership oracle for the training data ([threat model](../THREAT_MODEL.md), membership oracle).
- The gateway keeps the last 8 trees it built (per runtime, so a demo reset whose version ids start again never serves an old tree). A tree is cached only when it rebuilds the root and size the BOM signs; otherwise the endpoint answers 500 and writes one `integrity` event per version and split. It never serves a proof that would fail against the signed root.
- A version without a commitment (trained before v0.5, or without a BOM) is a 404 `commitment not available`.
- Mock mode only: `GET /ratchet/demo/sample-example?split=&version=` returns a committed example's prompt (task, stored messages and conditions, never the answer), as a body for the proof endpoint.

### Checking proofs offline

`python -m ratchet.proof` needs only the BOM and, for a trusted signature, the public key:

- `verify proof.json --bom bom.json [--public-key K] [--expect included|absent]` checks the BOM's hash and signature, that the proof's root is the one the BOM signs for that split, and `merkle.verify` against the **signed** root. The file's own `root`, `result` and `value` are never trusted; a disagreement with what the proof shows is a failure.
- `heldout cert.json --bom bom.json [--public-key K]` rebuilds the golden `KeySet` from the certificate's leaves, requires its root and size to equal the BOM's golden commitment, and requires every golden key to verify `absent` against the signed training root.
- `key --task T --messages m.json [--no-redact]` prints the key a request is stored under.

Exit status 0 when everything checks, 1 on any failed check (a bad signature, a root the BOM does not sign, an invalid proof, an unexpected result, a file that is not JSON), 2 on bad input (a missing file, a request that does not validate). The verifiers read untrusted files and never raise.

### Why non-membership is sound

For one root and one key, the fold path is fixed by the key's bits. Suppose an inclusion proof and a non-inclusion proof for the same key both verified against the same root. Folding from the root down, the two proofs' intermediate values agree at every depth they share, or the two `node_hash` preimages differ while their hashes are equal (a collision). So at the shallower terminal's depth, one proof's leaf hash or `EMPTY` equals the other proof's internal node hash, or a leaf hash for a different key. That needs a SHA-256 collision across the 0x00 and 0x01 domains (both preimages are 65 bytes, so neither is a prefix of the other) or a preimage of 32 zero bytes. This holds even against the operator who built the tree.

Two further checks are defence in depth: a proof has at most 256 siblings (one per key bit), and a leaf terminal for another key must share the first `len(siblings)` bits of the key asked about, which is where that key would be.

A Merkle tree over a sorted list proves absence with two adjacent leaves that bracket the key. That is sound only if the leaves really are sorted, which a verifier holding one proof cannot check: an operator who inserts a key out of order can later "prove" it absent. It is not used.

### What the proofs show, and what they do not

They show the operator's binding commitment, signed at training time, to the dataset an adapter was trained and graded on: a given example (with its label, teacher, license and redaction count) was in that set, or was not. They do not prove that the weights were trained on that dataset; an operator who signs one dataset and trains on another is not caught by a Merkle proof. The BOM copy in the adapter folder ties the commitment to the weights it ships with, and the training job reads the same rows (`night/train.jsonl`) whose keys are committed; a training job that recomputes the root itself is a possible follow-up.

## Consequences

- Every BOM written from v0.5 on carries both commitments; v1 documents stay as they are and still verify (INV-14), and the proof tools report "no set commitment" for them.
- Night School does a little more work: building a `KeySet` takes about 10 ms for 2,500 keys and about 0.3 s for 50,000 (CPython on the development machine; a proof is then about 20 µs). `version_leaves` grows by one row per example per version (cumulative retraining, ADR 0004): about 1,400 rows a version in the four-day mock demo, but 50,000 examples over 30 versions would be 1.5 million rows (two 64-character hex strings each, plus the key index). Pruning is impossible by design (INV-17); a later item could store each example's value once and per-version membership separately.
- A proof is about log2(n) siblings: a median of 11 for 1,288 random keys and 16 for 50,000 (at most 20 and 29). A held-out certificate for 133 golden rows against 1,288 training leaves is about 113 KB of JSON (mock demo, four simulated days).
- A non-membership proof names the leaf at the end of the key's path: another example's key and value, both hashes. A held-out certificate therefore discloses up to one training key per golden key. Hashes of redacted text are not text, but a guessable text can be confirmed against them, so certificates go to auditors, not to the public by default.
- `version_leaves` rows are written once; an edit outside SQLite's API (dropping the triggers) is caught by the integrity check and by any verifier holding the BOM.

## Alternatives considered

- **Keep the hash list (v1).** No single-example proof without publishing every hash, no absence proof, no binding of labels or licenses.
- **A sorted-list Merkle tree (RFC 6962 over sorted keys).** Absence relies on sortedness the verifier cannot check (above).
- **A full 256-level sparse Merkle tree without collapsing.** Sound, but every proof has 256 siblings (or needs a compression scheme); the collapsed tree gives about log2(n) and the same soundness argument.
- **RSA accumulators, KZG or Verkle commitments.** Shorter proofs, but they need number-theoretic or pairing libraries (and a trusted setup for KZG); the rest of the evidence code is standard library only.
- **Publishing the full leaf lists.** Lets anyone confirm any guessable text against the whole training set at once; proofs disclose one path at a time, to an admin.
- **A public proof endpoint, or a new `RATCHET_ADMIN_CLIENTS` list of API clients allowed to ask.** Public is a membership oracle for training data. The admin keys of the task registry ([ADR 0022](0022-task-registry.md)) already separate management calls from client calls, with 401 and 403 and nothing to configure in mock mode, so a second access list was not added.
