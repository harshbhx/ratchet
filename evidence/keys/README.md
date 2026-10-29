# Ratchet BOM and receipt signing key (public half)

- Algorithm: Ed25519
- Key id: `81b0a086250a81d0` (the first 16 hex characters of the SHA-256 of the public key's raw bytes, `ratchet.bom.key_id`)
- Public key (base64): `Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc=`, also in `bom-signing-2026-10-29.pub`
- Created 29 Oct 2026. The private seed is kept off this repository.

Verify a document signed with it:

```bash
python -m ratchet.bom verify bom.json --public-key Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc= [--notice NOTICE.md]
```

Status on 29 Oct 2026: **no document has been signed with this key yet.** The 28 Oct Night School BOM was not signed: only a cut-off excerpt of it was kept (see `evidence/nightschool-nebius/README.md`), and a signature over a rebuilt copy would not be the run's own record. Live-gateway BOMs and receipts are signed only once the key is set on the Worker.
