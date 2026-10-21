# ADR 0032: A label-free drift alarm: a conformal test martingale on each version's own confidence stream

- Status: Accepted
- Date: 2026-10-21

## Context

Every precision claim Ratchet makes assumes that the traffic a version serves looks like the traffic it was measured on: the golden gate, the certificate of [ADR 0026](0026-certified-thresholds-and-a-paired-release-gate.md) (its first assumption says so), and the canary's audits, which are a sample of past requests. Before this decision Ratchet noticed a change in traffic in two ways only:

- **Audits.** A teacher call on 5% of served answers. They measure quality, but slowly and at a price, and a change that the student escalates (low confidence) is never audited at all.
- **The next night's golden set.** A day late, and only if Night School trains.

An operator could watch the escalation rate rise on the dashboard, but nothing said when a rise was more than noise, and nothing reacted. The certificate kept saying `certified` for traffic it was never measured on (its `suspended` status was reserved for this item).

The student already logs a confidence score for every request it answers (`requests.confidence`), and that needs no labels.

## Decision

A drift monitor (`ratchet/drift.py`, stdlib only) runs a conformal test martingale on each (version, task) stream of logged confidence, inside the release gate's tick.

- **The null hypothesis.** For a fixed version, its requests are exchangeable: their order carries no information. A version's confidence is a fixed function of the request and of the student's own sampling, so its confidence stream is exchangeable too. Nothing about the model has to be assumed, and no label is needed.
- **P-values.** Each new confidence gets a smoothed conformal p-value: its rank among the stream's scores so far, `p = (#{lower} + θ · #{tied, itself included}) / n` with θ uniform on [0, 1], so it is small when the confidence is unusually low for this version. Under exchangeability these p-values are independent and uniform on [0, 1] (Vovk, Gammerman and Shafer 2005; Vovk et al. 2021). Scores are counted in 10,001 bins (4 decimals) by a Fenwick tree, so a rank costs two walks of about 14 steps and the stream is never stored or sorted; θ breaks the ties binning creates.
- **Betting.** A gambler bets against the null with the power betting functions `f_e(p) = e · p^(e−1)`, `e ∈ {0.05, 0.15, 0.3, 0.6, 1.0}`. Each integrates to 1 on [0, 1]; `e = 1` is "no bet". They are mixed by the Simple Jumper (Vovk, Petej, Nouretdinov, Manokhin and Gammerman 2021, Algorithm 1): before each bet a share J of the capital is spread evenly over the bets, so the gambler can move to the bet that pays after a change. The capital is kept as a natural log.
- **Validity.** The bet weights depend only on the past and each bet has mean 1 under a uniform p-value, so the capital is a test martingale: it starts at 1, stays non-negative and its expected next value is its current value. Clamping a p-value up to 1e-12 (so every bet stays finite) only lowers decreasing bets, which keeps a supermartingale. By Ville's inequality, the chance that a stream's capital ever reaches the threshold c is at most 1/c, however long the stream runs and however often anyone looks. With K streams the chance that any of them alarms is at most K/c; `GET /ratchet/drift` and each `drift` event report it next to the per-stream bound, so it is never hidden.
- **One-sided.** Every bet with `e < 1` pays on small p-values, which come from confidence that is lower than before. Confidence that rises (the student got better at this traffic) makes the capital fall and never alarms.
- **The switching rate falls as 1/n.** The spec proposed the paper's constant J = 0.01. While nothing changes, a constant rate moves `J · (1 − 1/K)` of the capital per request into bets that lose, so the capital shrinks exponentially with the version's age. Measured on a stream like a trained student's (simulation, counts only, 3 seeds each), the log10 capital at the change was −3.8 after 1,000 requests, −17.6 after 5,000 and −68 after 20,000, and a launch after 20,000 requests took 248 to 281 requests to alarm. We use `J = min(0.01, 1/n)`: the first 100 requests as in the paper, then a rate that shrinks the capital only like a power of n (switching with a decreasing rate, as in fixed-share forecasting). The same launch after 20,000 requests alarmed after 5 to 41 requests, with the capital at about −2.9 just before. J depends on n alone, never on the data, so the validity argument is unchanged.
- **The alarm latches.** Only the first crossing reports. An alarm stays open for the version's life: its own traffic stopped looking like its earlier traffic, so no fixed set of rows it was measured on stands for what it now serves. The next version, trained on the new traffic, gets new streams.
- **Where it runs.** `ReleaseGate._tick_drift`, the first of the tick's steps reserved for it ([ADR 0024](0024-evidence-foundations.md)), calls `check_drift`: one primary-key range query, `Ledger.confidence_stream(after_id)`, for the confidences logged since the last tick (rows with a confidence and a student version; pinned and teacher-only requests log none, and shadow confidences are not logged). `Ratchet.handle` is not touched, apart from reading the audit boost below. The gateway runs it once at startup, so the replay of the ledger does not fall on the first request.
- **Reproducible.** Each stream draws θ from its own `random.Random(f"{seed}:{version}:{task}")`, never from `Ratchet.rng`, so the router's draws (canary splits, audits) are exactly what they were, and replaying the ledger gives the same capital to the last bit. A restarted gateway, `python -m ratchet.live tick` and the nightly job therefore agree, and each alarm's `drift` event is written once: `Ledger.event_once` checks the `meta` table (`drift:<version>:<task>`) and writes the event in one transaction.
- **Memory.** Counts are kept for versions in canary or live and for the most recently retired one (the one an operator would restore by hand); an older stream keeps only its final numbers. A compacted stream that gets new rows (a version restored by hand) is replayed from the ledger first. Deciding what to compact reads the versions' states, a second query, so it runs after the replay and then once per 1,000 rows.
- **Actions.** An alarm writes a `drift` event in plain words ("Traffic changed; this alone does not mean the model got worse") and makes `GET /ratchet/versions/{id}/certificate` report `suspended`. It decides no release. Two opt-in actions, off by default:
  - **Audit boost** (`RATCHET_DRIFT_AUDIT_BOOST`, FR-6.40). The alarmed version's audit rate is multiplied by the boost while it serves (canary or live), up to 0.5 and never below its own rate. The gate fills a `{version: boost}` dict that the router reads (`Ratchet.audit_boost`, the same object); a version that stops serving is taken out, so a newer version going live clears it. There is still exactly one random draw per served answer and the boosted rate is logged as `label_p`, so the ledger replay and the receipts stay unbiased. The spec proposed a default of 3; that would have changed the audits, and so the training data and every reference number, so it is opt-in.
  - **Early night** (`RATCHET_DRIFT_EARLY_NIGHT`, FR-6.41). When a `drift` event is newer than the last `trained` event, Night School needs `max(1, min_new_examples // 2)` new examples. It runs after the in-flight check, so it never trains during a release (INV-5), and it brings the monitor up to date itself, so a nightly job in its own process sees the alarms the gateway would.
- **Settings.** `RATCHET_DRIFT_ALARM` (100, so 1% per stream) is the threshold; 0 turns the monitor off. `ReleasePolicy` refuses a threshold of 1 or less, a boost below 1, and an action with the monitor off.

### Measured in simulation

Mock models, ticket workload, 2,000 requests a day, product launch on day 5 (`python -m sim.simulate --drift-day 5`):

- Seeds 1, 2, 3, 7, 11 and 23: the alarm fired on day 5 for every seed, 70 to 437 requests into the launch day, on the live version's `ticket-triage` stream. The prototype's claim (alarms on the launch day for seeds 7, 11 and 23) holds.
- The same seeds without a launch, 10 days: no alarm.
- At 1,000 requests a day, seeds 7 and 11 alarm on day 5; at 600 a day, neither alarms within the two launch days of a 6-day run. In the mixed workload (half tickets) and the research workload (seed 7, 1,500 requests a day, 8 days) no stream alarmed, with or without the launch. Power depends on the volume per stream and on how far confidence falls.
- With the monitor on and off, every daily number, version and other event is identical (`tests/test_drift.py::test_monitor_draws_nothing_from_the_router_rng`).
- Under the null (counts only): 2 of 200 i.i.d. streams of 2,000 points reached 100 (bound 1%), and 19 of 1,000 reached 10 (bound 10%). A drop at n = 1,000 alarmed within 22 to 57 points across 10 seeds.

The escalation rate also rises on the launch day, so an operator watching the chart would see this launch too. What the alarm adds is a stated false-alarm rate, an event that says what it means, a certificate that stops claiming what it no longer covers, and actions that can react.

## Consequences

- Positive: a change in traffic is noticed without labels or teacher calls, with a false-alarm bound that holds at any number of looks, and is reproducible from the ledger alone.
- Positive: the certificate no longer reads `certified` for traffic that left its golden rows behind.
- Positive: no reference number moved. The defaults raise only events (`drift` is an advisory kind that `scripts/reference_check.py` and the reference simulation leave out) and change no audit, training or release decision.
- Negative: the bound is per stream. Every version and task adds a stream, so across a long life with many tasks some alarm is likely by chance (K/c is reported for that reason). Raising `RATCHET_DRIFT_ALARM` trades power for fewer false alarms.
- Negative: an alarm means "traffic changed", including harmless changes: a Scout burst, a time-of-day pattern, a new client. Per-(version, task) streams limit this; the wording and the runbook say so.
- Negative: the monitor sees only confidence. A change that leaves the student's confidence where it was (confidently wrong on new traffic) is invisible to it; audits and the live alert remain the quality signal.
- Negative: a process that starts fresh replays the whole ledger once, about 10 µs per logged confidence (`python -m ratchet.live tick` does so on each run). The gateway does it at startup.
- Neutral: the `meta` table (reserved in ADR 0024) gains one row per alarm.

## Alternatives

- **Kolmogorov–Smirnov or PSI on windows** (yesterday against today, or a reference window against a sliding one). Each window comparison is a fixed-sample test; checked after every tick, its error rate grows without bound, as the Wilson rule's did (ADR 0025). A window length must be chosen and is wrong for some change, PSI has no error rate at all, and both need a reference window that is itself assumed clean.
- **CUSUM or Page–Hinkley on the escalation rate.** Needs the pre- and post-change distributions or a tuned drift parameter, and its false-alarm rate is an average run length, not a probability over the stream's life.
- **A martingale on audit outcomes.** That is the live alert's e-process (ADR 0025); it measures quality, needs labels, and sees only served answers.
- **One stream per version, all tasks pooled.** Fewer streams and a smaller family bound, but a change in the task mix alone (more of an easy task) would alarm, and an alarm could not say which task changed.
- **Persist each stream's state.** Faster restarts, but the state would have to be kept consistent with the ledger across processes; a replay from the ledger is exact by construction.
- **The Simple Jumper with constant J, or a mixture over fixed J.** See above: a constant rate makes the alarm slow for old versions. A mixture over several rates would also be valid, at more cost per point and with more to explain.
