# Ratchet BOM and receipt signing key (public half)

- Algorithm: Ed25519
- Key id: `81b0a086250a81d0` (the first 16 hex characters of the SHA-256 of the public key's raw bytes, `ratchet.bom.key_id`)
- Public key (base64): `Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc=`, also in `bom-signing-2026-10-29.pub`
- Created 29 Oct 2026. The private seed is kept off this repository.

Verify a document signed with it:

```bash
python -m ratchet.bom verify bom.json --public-key Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc= [--notice NOTICE.md]
```

Signed so far: one BOM. Signed on 29 Oct 2026 with Ed25519 key `81b0a086250a81d0`, after the run; the BOM content is unchanged from the run's own record: [`evidence/nightschool-nebius/ratchet_bom.signed.json`](../nightschool-nebius/ratchet_bom.signed.json) (with its `NOTICE.md`).

```bash
python -m ratchet.bom verify evidence/nightschool-nebius/ratchet_bom.signed.json --public-key Gh68hnDDlRSjBRxmc5dTo4l+rvqkftUsH7B2Nm72gpc= --notice evidence/nightschool-nebius/NOTICE.md
```

This key is also configured on the live gateway from 29 Oct 2026. Live BOMs and receipts produced after that carry it, so anyone can check them with the same public key.
