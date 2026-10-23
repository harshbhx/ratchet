# Security threat model: audit cases

The project's own threat model is `docs/THREAT_MODEL.md` (T1-T20). This page records the abuse cases this audit tested,
with the result. "Repro" names the test or script that shows it; all ran offline (mock mode, or live mode on
`httpx.MockTransport` fakes).

| # | Case | Boundary | Expected invariant | Result | Status |
|---|---|---|---|---|---|
| 1 | Unauthenticated caller spends teacher credits (live) | Edge, `_client` | Live mode refuses to start without keys unless the edge guard is on | Every API route gave 401/403 without a key (security audit sweep) | Sound |
| 2 | Dashboard visitor reads protected data or acts as admin | `access.py`, `_access`, `_admin` | Admin routes need an admin key in live mode | Sound in live mode; on a guarded **mock** demo without admin keys, 7 admin-level routes outside `/ratchet/admin/*` were open | Fixed (RAT-015) |
| 3 | Rate-limit bypass via concurrency, proxy headers, alternate routes | `ratelimit.py`, `_rate` | One request = one token | A 16-prompt completions batch used one token; no-train and erasure calls used none | Fixed (RAT-011, RAT-013) |
| 4 | Task spec manipulates routing or training eligibility | `taskspec`, admin API | Only an operator pins a task (INV-10) | Writes need an admin key; specs fully validated | Sound |
| 5 | Provider returns malformed, truncated or huge responses | `providers.py` | Only `UpstreamError`, never a 500 after billing | Cut-off body retried and billed 3x; lone surrogate and huge ints caused 500s | Fixed (RAT-006, RAT-007, RAT-016) |
| 6 | Provider times out after generating; client retries | `retry_call`, idempotency | Billed once per logical request where possible | `Idempotency-Key` honoured on chat; not on completions/responses | Fixed (RAT-002) |
| 7 | Model output carries hostile instructions or fake provenance | `licenses`, `_store_example` | License recorded from configuration, never from the response's `model` | Sound (license comes from `Ratchet.licenses`) | Sound |
| 8 | Retrieved web content tries to steer Scout | `scout/` | Search text is data; no tools beyond search; budget guard outside the model | Search results are rendered as text, links through `safeHref`; the guard admits before any call | Sound (prompt injection can still change an answer's text) |
| 9 | PII hidden in nested or structured content | `redact.py` | Nothing raw reaches `add_example` (INV-12) | Content parts, names, tool-call arguments and stored conditions covered; **Unicode separators defeated the patterns**; corrections ignored the plugin | Fixed (RAT-009, RAT-010); zero-width and non-ASCII e-mail open |
| 10 | Two requests race past a spending limit | `spend.py` | Overshoot bounded by in-flight requests | Documented in ADR 0063; room is held per in-flight request | Sound (documented) |
| 11 | Training job uploads an incomplete adapter | `train_job.sh`, `live.py` | Load only a complete folder | Script waited for the weights file only | Fixed (RAT-026, not run on a GPU) |
| 12 | Release tick runs twice concurrently | `release.py`, `ledger.transition` | Each decision once | Wilson rule sound; **anytime-valid rule overwrote concurrent moves** | Fixed (RAT-001) |
| 13 | Small or contaminated evaluation set | `evaluate.py`, `dataset.py` | Held-out measurement (INV-6) | Certificate described the wrong serving set; cross-task golden rule can leak over time | RAT-004/005 fixed; RAT-022 open |
| 14 | Adapter replaced after evaluation | BOM, `proof.py` | BOM signs the training and golden roots | Proofs checked against the signed root only | Sound (the adapter weights themselves are not hashed into the BOM; see residual risks) |
| 15 | Signing key absent, malformed or mismatched | `bom.Signer`, verifiers | Verifiers never raise; unsigned BOMs say so | Sound | Sound |
| 16 | Database write fails mid-transition | `Ledger.transaction` | All-or-nothing | `promote_live` is one transaction; the sequential path used two commits before RAT-001 | Fixed |
| 17 | Path traversal, injection | `sqlcheck`, fixtures, live export | Fixture reads confined, SQL sandboxed | Sound (O_NOFOLLOW, size caps, authorizer, `query_only`) | Sound |
| 18 | Teacher outage during fallback | breakers, degraded mode | Stand-in answers never stored or counted as audits | Sound | Sound |
| 19 | Public demo control exhausts quota or changes state | `_mock_only`, guard | Demo controls mock-only (INV-8) | Sound; shared mock state is by design (T18) | Sound |
| 20 | Fresh deploy breaks on CLI or version mismatch | `deploy/`, day-1 check | Dry runs before paid work | Dry runs pass; real `nebius` CLI not available here | Not verified |
| 21 | Append-only evidence edited in place | ledger triggers | INV-17 | `INSERT OR REPLACE` naming a rowid got past the triggers | Fixed (RAT-017) |

## Residual risks worth stating to judges

- The BOM binds the training and golden sets and the eval, but not a hash of the adapter weights themselves; an adapter
  file swapped in the bucket after training would not be detected by the BOM. (Proposal: hash the weights in the job and
  sign it in the BOM's next schema version.)
- Erasure removes stored text and tombstones its hash; it cannot remove what an already trained adapter learned. The
  BOMs of affected versions are reported, never edited (INV-24).
- One gateway per ledger file; two gateways on one file are not supported yet (NFR-60).
