# ADR 0081: Retention: tombstones, erasure and export

- Status: Accepted
- Date: 2026-10-20

## Context

Ratchet kept every stored example and every request row for good. There was no delete path: the runbook's answer to a PII leak was to stop the gateway and run `DELETE` by hand, which also broke Night School. `run_night` counted new data as "training rows now minus the largest version's training rows", so any deletion made the count negative and training silently stopped until the lost rows were replaced. Opt-outs (ADR 0080) keep new data out, but a client that leaves, an end user who asks to be forgotten, a contract with a retention limit, or an auditor who wants the data cannot be served without a way to remove and export what is there.

Three things make deletion harder than a `DELETE`:

- Night School's rules read more than the text. The golden split comes from the hash; the frequent-request rule (INV-3) adds `seen` over every stored variant of a request; the new-data count compares with past versions.
- Adapters trained on a row keep whatever they memorised, and each adapter's signed BOM (INV-14) describes the exact rows it saw. Neither can be edited.
- The stats, the cost report and the release gate read request rows; deleting old ones must not change a figure or a release decision.

The provable-erasure feature (FR-16.9: signed certificates and an exact retrain) will be built on top of this after the lanes merge, so the tables have to be generic.

## Decision

- **Tombstones, not row deletes.** A purge (`Ledger.purge_examples`) sets `messages`, `completion` and `params` to NULL and records `purged_ts`, `purge_reason` (`retention` or `erasure`) and `purged_train` (whether the row counted as a training row then, `dataset.counts_as_train`). Hash, task, split, seen count, teacher, license and client stay. A row with stored conditions keeps its prompt key (`prompt_hash`), so its `seen` still counts toward its request's frequency: a purge never moves another variant of a frequent request back to golden (INV-3). `dataset.rows` skips purged rows; the golden eval only sees what `dataset.rows` returns, so it never grades on purged text.
- **Erasure is final; retention is not.** An erasure (`privacy.erase`) also clears the end-user hash, writes the hash to `tombstones`, and `Ledger.add_example` refuses that hash from then on, from any client (returning None; the router logs a `skip_example` `erased` event and the request `stored` 0; `tombstones.suppressed` counts refusals). A retention purge keeps the end-user hash (so a later erasure can still find the row) and a later duplicate that may be stored brings the text back, with that answer's teacher and license. Erasing a retention tombstone makes it final.
- **Scopes.** `client` (first sender or any later one), `end_user` (its HMAC in the key's scope; an admin without a client covers every key and the keyless scope, as no-train does), `hashes` and `contains` (a LIKE match, with `%`, `_` and `\` escaped, on the stored text; messages and conditions are matched in their JSON form). Every selector narrows the scope; none means refuse. `RATCHET_ERASURE_SHARED` decides an example another client also sent when the scope names a party: `delete` (default) purges it, since its text is the erased party's data too; `keep` keeps it for the other client and unlinks the erased party. Erasing an end user also clears its hash from `requests`.
- **The record holds hashes only.** The reserved `erasures` table (ADR 0024) gains `actor`, `reason` (redacted), `scope` (end users as HMACs, `contains` as its SHA-256), `purged` and `kept`; its `tainted` column holds the affected versions, `status` is `pending` while such versions exist and `complete` otherwise, and `key`, `replacement` and `certificate` stay for FR-16.9. `erasure_items(erasure_id, hash, task, split)` lists what each erasure purged. An `erase` event gives counts and names, never a hash, because `/ratchet/stats` is public. A dry run (the admin API's and the CLI's default) writes nothing.
- **Affected versions are reported, never edited.** `Ledger.affected_versions` uses a version's committed training set (`version_leaves`, FR-16.4) when it has one, and otherwise counts the examples it was created after while they counted as training rows. The approximation errs toward listing (a dataset built before the example arrived, a row that was golden then). Signed BOMs stay as written; the erasure log explains the gap between a BOM and today's ledger, and `/ratchet/bom` (the whole ledger) changes its fingerprint and counts `purged_examples`.
- **Request retention folds, then deletes.** `Ledger.fold_requests` writes, per day, task, client, route and opt-out, every sum a reader needs (requests, shares, cost and its split, tokens, audits, latency, stored) into `daily_rollup`, then deletes the rows, in chunks so requests keep flowing. `stats_by_day`, `stats_by_task`, `stats_by_client`, `totals`, `opt_out_stats` and `cost_by_day` add the sums back; without folded rows they run their old queries unchanged, so the reference runs cannot move. Rows newer than the `stage_since` of a version in shadow or canary are never folded, so a release in flight keeps its evidence. The first fold records day 1's start in `meta`, so day numbers do not shift when the oldest rows go.
- **When retention runs.** At most hourly from the live gateway's tick, on a background thread so no request waits (`Runtime.maybe_purge`; a failure is a `purge_failed` event naming the exception's class); at the start of every live night, before the dataset is read; and from `python -m ratchet.live purge [--client X] [--older-than-days N] [--dry-run]`.
- **Night School's new-data rule survives deletions.** New data is the training rows now, less the training rows of the largest version so far, plus the training rows that version had which were purged since (`Ledger.train_rows_purged_since`: rows with `purged_train`, stored before it and purged after it). Without purges it is exactly v0.4's count.
- **Export.** `GET /ratchet/admin/export/{kind}` and `python -m ratchet.admin export` stream NDJSON a page at a time (`Ledger.export_page`, `privacy.export_lines`): examples that still have their text with their provenance and every client that sent them, request metadata, versions with their BOMs as stored, events, and erasures with their items. End-user hashes are left out; API keys are never stored.
- **Extension points for provable erasure (FR-16.9).** Hook a forced retrain after `Ledger.record_erasure` (the next version is trained without the purged rows, since `dataset.rows` skips them); set `erasures.replacement`, `certificate` and `status` there; prove absence from the new version's training set with `merkle.KeySet` over `version_leaves`, using `tombstones` as the list of keys; list honoured erasure ids in the BOM's reserved `erasures_honored` section. Committed leaves then also make `affected_versions` exact.

## Consequences

- An operator can honour a client's exit, an end user's erasure request and a retention clause without stopping anything, and can hand an auditor the data with its provenance.
- An erased text can never be stored again, even when another client sends it later. That is the point of a tombstone; an operator who erased by mistake cannot undo it (the dry run exists for that).
- Distinct end users per client count only the request rows still kept; every other figure is unchanged by a fold.
- Purged rows stay in the ledger as hashes. Hashes of redacted text are not text, but whoever holds a candidate text can test it against them; that is also what makes the refusal and the erasure log checkable.
- The live alert on a long-lived version reads the audits still kept, a window of `RATCHET_REQUEST_RETENTION_DAYS`; in-flight stages are never cut.
- Adapters trained before an erasure may still reproduce the erased text. Ratchet reports them; retiring them, or retraining and releasing a replacement, is the operator's decision, and certifying the replacement is FR-16.9.

## Alternatives

- **Delete rows.** Simple, but the frequency rule, the golden split and the new-data count would change under Night School's feet, and nothing would stop the same text from being stored again by the next request.
- **Rewrite stored BOMs without the erased rows.** Breaks their signatures and makes them lie about what each adapter saw (INV-14).
- **Count new data by time only** (rows stored after the last version, as the item's spec proposed). It misses golden rows that became frequent since and rows excluded at the time, so it changes v0.4's count (13 nights in the reference workloads, by up to 18 rows, and the pinned "only 40 new examples" skip) without a deletion.
- **Keep every request row and compute stats from a cache.** Retention is the requirement; a cache would still hold the rows.
- **Erasure by end user from a query string**, as the item's spec proposed. Access logs keep URLs, so the identifier being erased would be written to them; the body is used instead and a URL with `end_user` is refused.
