# Lane E2: proposed invariants (merged)

Lane E2's proposals are now in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these), with `CODE` entries in
`scripts/reqtrace.py` and the proving tests marked:

- Invariant 1, extended: configuration cannot relabel a known closed teacher (`RATCHET_TEACHER_LICENSES`,
  `licenses.closed_family`, [ADR 0090](../adr/0090-licenses-travel-with-the-adapter.md)), and `human` is never a teacher
  ([ADR 0091](../adr/0091-human-labels-as-a-provenance-source.md)).
- Invariant 2, amended: training targets are teacher answers or customer-attested human labels, never student outputs;
  a verdict is never a label.
- Invariant 3, extended: golden-only rows, and labels that are no occurrence of their request never count toward
  `FREQUENT`.
- Invariant 10, extended: no human or imported label for a pinned task.
- Invariant 14, extended: the NOTICE is part of the signed BOM.
- Invariant 16, amended: `feedback.store_label` is the second writer of examples, with the spec check and the label
  precedence in one transaction.
- INV-26 (new): human verdicts never promote or roll back a version.

The notes below are still open for later merges.

## Notes for the integrator (DATA-FEEDBACK-IMPORT review fixes)

- **`examples.origin` on a retention restore.** `Ledger.add_example` now writes the caller's `origin` when a duplicate
  restores the text of a row retention purged (with that answer's label, teacher and license), where it used to keep
  the stored origin. From the router that is NULL in this lane; lane F's FR-16.8, which owns the column and passes an
  origin from the router, should keep the rule "the restored label's own origin" when the branches merge. The column's
  values now include `feedback` and `import` (`ledger.LABEL_ORIGINS`).
- **`examples.seen` may be 0.** A label stored with `bump=False` is inserted with `seen` 0 (router writes still insert 1,
  the column's default). Any reader added by another lane must count it with `ledger.seen_count` (or `seen` with NULL as
  1), not `seen or 1`.
- **`requests.end_user_secondary`.** A second end-user hash per request row, logged by the router from
  `RequestMeta.end_user_secondary`. Every place that clears or exports `requests.end_user` must treat it the same way
  (this lane: `Ledger.record_erasure`, `Ledger.requests_of_end_users`, `Ledger._export_requests`; the request fold
  deletes rows, so it needs nothing).
