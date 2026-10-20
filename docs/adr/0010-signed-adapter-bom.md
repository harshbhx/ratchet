# ADR 0010: A signed bill of materials per adapter

- Status: Accepted
- Date: 2026-10-20

## Context

Ratchet's pitch is license-clean distillation ([ADR 0001](0001-open-teacher-only.md)). Up to v0.3, `/ratchet/bom` described the whole ledger at the moment it was called: which teachers and licenses produced the examples, and a fingerprint of all of them. It did not say what a particular adapter was trained on, and anyone with write access to the ledger could change it later. A customer, auditor or model registry needs a document that travels with the adapter and can be checked without trusting the gateway that served it (PRD FR-3.6, threat model T6).

## Decision

- Night School writes a bill of materials for each adapter right after training and evaluation (`run_night` → `ratchet/bom.py`), from the exact rows it used: teacher, license, license URL and count per source; tasks; the dataset hash; a hash of the golden rows; the evaluation; and how many examples had PII redacted. Schema `ratchet.bom/v1`.
- The document carries the SHA-256 of the canonical manifest (sorted keys, no whitespace, UTF-8). When `RATCHET_BOM_SIGNING_KEY` is set, it is also signed with **Ed25519**, and the signature block names the algorithm, the public key and a key id.
- Stored in `versions.bom`; served at `GET /ratchet/versions/{id}/bom`; the public key at `GET /ratchet/bom/public-key`; live mode also writes it to `data/boms/student-vN.json`, next to the training data that is synced to the bucket.
- `python -m ratchet.bom verify bom.json --public-key K` checks a downloaded BOM offline. `keygen` creates a key pair.
- Signing needs `cryptography`, an optional extra (`ratchet-distill[sign]`) that the gateway image installs.

## Consequences

- Anyone with the published public key can check, offline, that an adapter's provenance claims were made by the operator at training time and not edited since.
- The signature proves who signed and that nothing changed; it does not prove the claims are true. A compromised signer can sign anything (threat model T21). The gateway reads the key from the environment and never returns it.
- The BOM records the evaluation at training time. Later re-checks change `/ratchet/stats`, not the signed document.
- Versions trained before v0.4 have no BOM (`bom: null`).

## Alternatives considered

- **HMAC-SHA256** with the standard library. No dependency, but only holders of the secret can verify, which defeats the purpose of showing provenance to a third party.
- **Sigstore / in-toto attestations.** The right long-term format for supply-chain tooling, but they need an identity provider or transparency log at signing time. `ratchet.bom/v1` can be wrapped in an in-toto statement later.
- **Sign the ledger-wide `/ratchet/bom`.** Says nothing about a particular adapter, and changes on every request.
