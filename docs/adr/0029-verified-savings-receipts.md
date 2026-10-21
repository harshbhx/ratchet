# ADR 0029: Verified savings receipts: an audit-calibrated baseline billed on its lower bound, in signed, chained RFC 9162 receipts

- Status: Accepted
- Date: 2026-10-21

## Context

Ratchet's business model is a share of the savings it makes ([BUSINESS.md](../BUSINESS.md)). Two things made that number impossible to check:

- **The baseline of a student answer is an estimate.** When the student serves a request, nobody knows what the teacher would have cost. The token formula (`Ratchet._formula_baseline`) assumes the teacher writes `RATCHET_TEACHER_REASONING_TOKENS` (300) of reasoning. Nobody has measured that, and a vendor paid on savings earns more the higher it is.
- **The ledger is a SQLite file the vendor can edit.** A savings figure computed from it today says nothing about what it said yesterday.

What already exists:

- Audits are a random sample of the answers the student served: one `rng.random()` draw per served answer, at the rate logged as `requests.label_p` (ADR 0025), independent of what the request costs. Each audit is a real teacher bill (`baseline_cost_usd` of an audited row).
- Every student answer logs the formula's estimate (`requests.baseline_est_usd`, ADR 0028), also when the teacher was called.
- `stats.ratio_estimate` (ADR 0024), the cost split per request (ADR 0023), RFC 9162 trees (`merkle.LogTree`), the append-only `receipts` table (INV-17), Ed25519 signing (`bom.document`, ADR 0010) and `Result.request_id` (the ledger row of a request).

## Decision

### Leaves: one per request, metadata only

A receipt covers one ledger day (`period`) of one API client, or of the whole gateway. Its leaves are the day's requests (only that client's for a client receipt), in id order, each `bom.canonical` of exactly `receipts.LEAF_KEYS`:

`id`, `ts_ms`, `day`, `task`, `client`, `served_by`, `route`, `student_version`, `escalated`, `audited`, `label_p_ppb`, `cost_nusd`, `student_cost_nusd`, `teacher_cost_nusd`, `baseline_nusd`, `baseline_est_nusd`.

No request text (INV-12), and the API key's name, never the key (INV-13). Every amount is whole nano-USD (`round(usd * 1e9)`) and `label_p` is in parts per billion, so sums are exact and the canonical JSON that is hashed and signed holds no float. `route` and `label_p_ppb` are there so a verifier can recompute the calibration from the leaves alone.

### The estimate, per task

Requests fall in three groups (`receipts.category`):

- **measured**: the primary teacher answered (`served_by = 'teacher'`), so the baseline is its bill;
- **served**: the student served on the audit design (a random share is audited);
- **unverified**: a stand-in answer (`UNVERIFIED_ROUTES`, the router's `STAND_IN_ROUTES`): the student's answer for an unavailable teacher (route `degraded`, never audited) or the fallback teacher's (route `fallback_teacher`, whose baseline is the primary teacher's price on another model's tokens).

For each task with served answers (`receipts.calibrate`), U is its N served answers and the sample is its n audited ones. Each sampled answer gives x (the formula's estimate) and y (the audit's teacher bill), weighted 1 / `label_p` (Horvitz-Thompson; equal weights when the audit rate was one constant). Then (Cochran, *Sampling Techniques*, ch. 6):

- R = Σ y/π / Σ x/π (`stats.ratio_estimate`), X_U = Σ x over U, Ŷ = R X_U;
- V = N² (1 − n/N) s_r² / n_eff, with s_r² the weighted residual variance of y − R x and n_eff Kish's effective sample size (n when the rate was constant);
- the interval uses the normal approximation, and the receipt says so (`calibration.method`).

A task with fewer than `RATCHET_RECEIPT_MIN_AUDITS` (30) audits is `insufficient_audits`. A task with a served answer that has no logged estimate (written before v0.5) is `unlogged`. Neither has an interval.

### The bill: the one-sided 95% lower bound

- `baseline_calibrated` = measured bills + each calibrated task's Ŷ + the recorded baseline of every other task and of the unverified answers.
- The **billable set** is the measured requests plus the served answers of the tasks with an interval. Measured requests are in it although their savings are negative (the student's samples were paid for too): the cascade's overhead counts against the vendor.
- `billable_lo` = (billable baseline − billable cost) − ⌈1.645 √Σ V⌉. Only the served tasks' estimates are random; the tasks' rows and draws are separate, so their variances add.
- `net_billable_lo` = `billable_lo` − the day's spend outside requests (the `spend` table: backfill labelling, FR-16.8). A client receipt nets its share of that spend by request count, rounded down.
- `fee` = ⌊`RATCHET_TAKE_RATE` × max(0, `net_billable_lo`)⌋, computed with exact fractions.
- With no calibrated task the basis is `formula` and the fee is 0, whatever the formula says. (Measured requests alone never save money, so this loses nothing.)

The gateway-wide receipt also carries the day's GPU-hours view (ADR 0023's cost report for that day: endpoint uptime and training priced by the hour, often negative). It is informational: billing uses the per-token view (`cost_views.billed`). A client receipt has no GPU view, because the endpoint and training are shared by every client.

### The document

Schema `ratchet.receipt/v1`: `schema`, `generator`, `created_at`, `label` (`simulation (mock models)` or `live traffic`), `period: {day}`, `client`, `first_id`, `last_id`, `n`, `merkle: {alg: rfc9162-sha256, root, size, leaf_keys}`, `totals`, `calibration`, `savings`, `billing`, `cost_views`, `prev_sha256`, `units`, `scope`. `prev_sha256` is the `sha256` (of the canonical manifest) of the same client's previous receipt, or 64 zeros for its first, so each client's receipts form one chain across days. `bom.document` wraps it with the BOM's Ed25519 key (`/ratchet/bom/public-key`); `bom.verify(doc, key, accepted=(SCHEMA,))` checks it. Numbers that are not whole (the ratio, its standard error, n_eff, the share, z) are decimal strings.

### Sealing: once, after the day is over

- A day is sealed once it has ended (`receipts.closed_through`): on a simulated ledger every day before the current one; on a live ledger every day that ended `SEAL_GRACE_S` (300 s) ago on the wall clock, so a request logged at the last moment is in.
- `seal_due` seals every unsealed day up to a limit, oldest first, each client's receipt and then the gateway's, whose presence marks the day done (`Ledger.unsealed_days`). Receipts are written once: `Ledger.add_receipt` checks and inserts in one `BEGIN IMMEDIATE` transaction and raises `ReceiptExists` when another process sealed first, whose document stands; the INV-17 triggers refuse any later change. `seal(ledger, period, client)` returns a stored receipt with no write at all.
- Who seals: the mock gateway after each simulated night (`Runtime.simulate_day`), any receipt endpoint lazily, `python -m ratchet.live seal` from cron, and `python -m ratchet.receipt seal --db` offline. The simulator seals only with `--receipts`.
- `Ledger.period_rows` reads a day in chunks (`RECEIPT_CHUNK`), each under the ledger lock, so the request path waits for one chunk at most.

### Endpoints and access

`GET /ratchet/receipts`, `GET /ratchet/receipts/{period}[?client=]`, `GET /ratchet/receipts/{period}/proof/{request_id}` and `GET /ratchet/receipts/{period}/leaves`. Chat completions return the request's ledger id as `X-Ratchet-Request-Id` and `ratchet.request_id` (also in the last stream chunk), which the proof endpoint takes. With keys on, an API key reads only its own receipts and proofs, and an admin key (`RATCHET_ADMIN_KEYS`) reads every receipt; the mock demo without keys is open; live mode without any keys serves no receipts (403), because the leaves list every request's time, task and cost.

The proof and leaves endpoints rebuild the tree from the ledger and serve only from one that rebuilds the signed root and size. Otherwise the caller gets a 500 and the ledger one `integrity` event per day and client, never a proof that would fail against the signed root. The tree cache keeps ids and hashes only; a proof reads its one row again and checks it against the signed root before serving it.

### Offline verification

`python -m ratchet.receipt verify r.json [--public-key K] [--prev prev.json] [--proof p.json] [--rows leaves.jsonl]` checks the hash and signature, the shape (no float anywhere), the chain (the previous receipt's manifest hash is recomputed, never read), a request's inclusion against the signed root and size (never the proof's own), and with `--rows` rebuilds the root and recomputes every total, estimate and the fee (`receipts.recheck`; amounts that come from the estimator within 2 nano-USD, decimal strings within 1e-5 relative, so another implementation's floating point may differ). It prints OK/FAIL lines and exits 0 or 1 (2 on bad input). The verifiers never raise on malformed input.

### The mock calibration demo

`MockTeacher(reasoning_tokens=300, reasoning_mean=None, reasoning_cv=0.0)`. With `reasoning_mean` set it bills round(mean × exp(cv z − cv²/2)) reasoning tokens, z a standard normal drawn from the request alone (`_rng('reasoning', ...)`), so the same request always costs the same and nothing else draws from it. `RATCHET_MOCK_TEACHER_REASONING_MEAN` sets it in the mock gateway (cv 0.3) and `python -m sim.simulate --receipts --teacher-reasoning-mean 420` in the simulator, which also asks the mock teacher about every request (in memory, never stored) to print the true all-teacher cost beside the lower bound. With 420 tokens, R is about 1.3 to 1.4 for `ticket-triage` (simulation): the 300-token assumption, measured and corrected. Unset, the mock teacher bills exactly 300 and R is about 1.

### What does not move

Sealing reads the ledger and writes receipts only: no model call and no random draw. The request path gains nothing but a header and a key in the `ratchet` block. The reference simulation and the three reference workloads do not move (the simulator seals only with `--receipts`; `test_sealing_moves_no_simulation_number` compares runs with sealing on, off and monkeypatched out). The formula and `baseline_cost_usd` are unchanged: receipts measure the formula, they do not replace it.

## Consequences

- "Verified savings" means the billable lower bound of a sealed receipt, not the formula's savings ([BUSINESS.md](../BUSINESS.md)). A client can check its receipt, the chain and any request it saw without trusting Ratchet's database.
- A task needs about 300 served answers a day at a 10% audit rate before it is billed (30 audits). Until then its savings are reported but not billed. A small client's per-client receipts stay `formula` (fee 0) longer than the gateway's.
- Audits that failed or whose answer was refused (INV-16) are not in the sample. If those are the expensive ones (long reasoning cut off), R is biased low, which lowers the bill.
- A sealed receipt cannot be corrected. A bad take rate or audit minimum stops startup instead (`Settings.receipt_policy()`).

## Limits

- Receipts make edits after sealing detectable, and tie the estimate to audited teacher bills. They cannot catch a false cost written at request time. External anchor: reconcile the summed teacher spend (`totals.teacher_cost_nusd` over the days) with the Token Factory bill for the same API key.
- The interval is a normal approximation. On synthetic populations (y = 1.3 x (1 + 0.1 z), 300 served answers audited at 10% or 20%) the one-sided 95% bound covered the true total in 94% to 95% of 2,000 seeded trials; `test_ratio_estimator_recovers_the_ratio_and_its_lower_bound_covers` asserts at least 180 of 200. Heavier-tailed real bills may need more audits than 30.
- The audit runs at temperature 0 with the audit's parameters, while an all-teacher world would use the client's. The measured ratio is the deterministic answer's.
- The chain shows that a receipt was changed; it does not stop a vendor from withholding one. Clients should keep the receipts they receive.

## Alternatives considered

- **Bill on the formula's savings.** Simple, but the formula's assumption is the vendor's to choose.
- **Bill on the point estimate.** Overbills about half the time.
- **Audit everything.** Exact, but pays the teacher on every request, which removes the savings being measured.
- **A ratio pooled across tasks or clients.** More audits per estimate, but a task's tokens differ, and a client's receipt could not be recomputed from its own leaves.
- **A new `RATCHET_ADMIN_CLIENTS` list of API client names that read every receipt** (the spec's suggestion). The admin keys of ADR 0022 already name operators, are never API keys, and are what every other admin read uses.
- **A sparse Merkle tree keyed by request id** (as for training sets, ADR 0027). Requests already have an order, and an append-style RFC 9162 tree gives the standard audit paths that transparency-log tooling checks.
