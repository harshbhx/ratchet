# Features in depth

The README keeps the short version. This page holds the longer explanations of what Ratchet does and how to check it: the reference simulation, the economics, the safety rules, certificates, proofs of what an adapter was trained on, the ledger replay, savings receipts, and privacy. Everything here also appears, linked, in [the PRD](PRD.md) and the [ADRs](adr/README.md).

## The reference simulation

The simulation uses **mock models**: it shows how the loop behaves (cold start, drift, rollback), not real model quality. Typical run (seed 7, 2,000 tickets/day, product launch on day 5, corrupted deploy of v3):

```
day  student  escal. audit ok     $/1k all-Ultra  saved
  1       0%      0%        -    1.093     1.093     0%     <- cold start: all teacher
  3      98%      2%     100%    0.160     1.093    85%     <- first student live
  4      98%      2%      93%    0.160     1.093    85%     <- v3 deployed corrupted -> rolled back automatically
  5      86%     14%      97%    0.272     1.093    75%     <- product launch: new questions escalate
  9      98%      2%      97%    0.162     1.093    85%     <- learned overnight
 10      98%      2%      99%    0.147     1.093    87%
```

Success metrics for that run (PRD §8.1, computed by `ratchet/kpis.py` over the last three days; simulation, mock models, not real model quality):

| Metric | Target | Simulation (seed 7) |
|---|---|---|
| SM-1 cost reduction vs all-teacher | ≥ 70% | 84.5%, met |
| SM-2 audited precision (585 audits pooled) | ≥ 95% | 98.5%, met (Wilson 97.1–99.2%) |
| SM-3 student share | ≥ 80% | 96.9%, met |
| SM-4 bad deploys reaching 100% traffic | 0 | 0, met (v3 rolled back at 10%) |
| SM-5 time to recover from drift | ≤ 3 nights | 4 nights, **not met** |

`GET /ratchet/kpis` computes the same metrics from a live ledger, and `python -m sim.simulate` prints them. No full live run (rounds with nights and a control arm) has been published; the 20-request smoke run is in `evidence/live-smoke-2026-10-23`.

`RATCHET_MOCK_WARMUP_DAYS=4` runs four simulated days and nights at startup (about 2 s), so the dashboard and Scout open on a student that is already live.

`--workload research` sends Scout's triage and reader calls instead of tickets, and `--workload mixed` sends half of each. Simulation, mock models, seed 7, 8 days × 1,500 requests: the student answered 92% of research calls on day 8 (81% saved that day, 62% over all 8 days), and 94% of mixed calls (82% saved that day, 51% overall), with per-task calibration on (the default: each task held to its own threshold and golden check). With `RATCHET_PER_TASK_CALIBRATION=0` (one pooled threshold, as in v0.4) the same runs give 96% of research calls (85% that day, 63% overall) and 98% of mixed calls (86%, 54%). See [docs/TESTING.md](TESTING.md#workloads-research-and-mixed). `--workload schema` sends structured-output requests and `--workload tools` support requests with tool definitions, from the generic mock world, which also answers any other request the mock gateway gets (free text, an instance of the request's JSON schema, or a mock tool call) instead of refusing it ([docs/TESTING.md](TESTING.md#workloads-schema-and-tools)). Tool calls pass through to the teacher and come back as `message.tool_calls` (also streamed, and in the legacy `function_call` form); a tool request is answered by the teacher alone and never stored, so the `tools` workload saves nothing until tool-call distillation ([ADR 0043](adr/0043-tool-calls-teacher-only-by-default.md)).

## How the economics work

Per token, a student answer costs a fraction of a teacher answer. But the self-hosted student runs on a GPU endpoint that bills by the hour whether requests arrive or not, so Ratchet also reports savings by the GPU-hours actually paid (`python -m ratchet.cost report`, `GET /ratchet/cost`), and says when self-hosting does not pay:

```
break-even student answers per endpoint hour = GPU price per hour ÷ teacher spend avoided per student answer
```

With **estimated prices** (an L40S at $1.35 per GPU-hour on demand, [Nebius pricing](https://docs.nebius.com/compute/resources/pricing), read Oct 22, 2026, and about $0.001 of Ultra spend avoided per ticket-triage answer in the simulation) that is about 1,350 student answers an hour, roughly 32,000 a day. At the demo's 1,500 requests a day a 24/7 endpoint loses money, and the report says so and recommends the hosted fallback or scaling the endpoint to zero. At customer volume (10M requests a month, about 14,000 an hour) the endpoint costs far less than the teacher. Per answer, though, the untuned hosted Nano is cheaper still below roughly 52,000 student answers an hour. So the report says "Self-hosting costs less than the teacher, but the hosted fallback would cost less per answer" and recommends `hosted_fallback`, adding that this assumes the hosted model serves as many requests as the tuned student. One GPU serving many LoRA adapters raises the volume per GPU-hour ([ADR 0023](adr/0023-utilization-aware-cost-accounting.md)).

## How the safety works

- **Confidence** = token probability (vLLM logprobs) + agreement across 3 samples + format validity. Small models are poorly calibrated, so no single signal is trusted.
- **Per-version threshold**, calibrated on half the golden set (target 97% precision) and measured on the other half.
- **Golden gate:** a candidate must reach 95% precision on held-out data and not drop more than 2 points below the live version, which is re-scored on the same golden set the same night (champion vs challenger).
- **Shadow:** the candidate runs silently on real traffic. Disagreements with the live student are arbitrated by the teacher, and the arbitration becomes training data.
- **Canary 10% → 50% → 100%**, gated on teacher audits of what it serves. **Rollback** fires when the 95% upper bound of audited precision falls below 92%.
- **Training data:** only teacher answers, never the student's own (avoids model collapse). Retrains from the base model on the cumulative set each night (limits forgetting). Requests that repeat 3+ times always go to training, so frequent questions are never held out forever.
- **No training while a release is in flight**, which saves GPU money.

## What a certificate means

Every adapter's golden eval, and so its signed bill of materials, carries a **certificate** ([ADR 0026](adr/0026-certified-thresholds-and-a-paired-release-gate.md)). It counts the held-out golden rows the threshold was *not* tuned on, how many of them the version would serve and how many of those agree with the teacher. It then gives an exact one-sided Clopper–Pearson lower bound on that precision. "Certified at 95% (lcb 96.4%, delta 0.05)" means: with probability at least 95%, the answers this version serves at its threshold agree with the teacher at least 96.4% of the time. That is a statement about agreement with the teacher, not ground truth, and it assumes the golden rows look like live traffic (they come from escalations, audits and arbitrations, so they lean towards requests the student found hard). A point estimate such as "99.2% precision" on 130 rows cannot tell 0.96 from 0.99; the bound can.

- **Frontier:** the same bound at every threshold from 0.95 down to 0.50, corrected so all ten hold at once: the certified trade-off between precision and the share of traffic served.
- **Per task:** the counts per task, so a weak task cannot hide inside a pooled number (a diagnostic; it does not change routing).
- **Paired gate (opt-in, `RATCHET_PAIRED_GATE=1`):** the candidate and the live version are compared row by row on the same golden rows with exact McNemar tests, so a difference within noise no longer rejects a better model, and a significant regression still does. In the reference run (simulation) the default rule rejected v2 (70 of 74 rows vs 74); the paired gate admits it, p = 0.0625.
- **Learn-then-Test (opt-in, `RATCHET_CALIBRATION=ltt`):** the threshold itself is chosen so its precision is certified, over a fixed grid of thresholds.

```bash
python -m ratchet.stats plan --target 0.95 --delta 0.05        # 0 errors: 59 rows, 1: 93, 2: 124, 3: 153
curl -s localhost:8000/ratchet/versions/2/certificate          # signed and latest certificate, assumptions
curl -s localhost:8000/ratchet/versions/2/bom > bom.json
python -m ratchet.stats check bom.json --public-key <yours>     # recompute every number from the signed counts
```

## A drift alarm that needs no labels

A certificate holds only while traffic looks like what it was measured on. A **drift alarm** watches for that without labels ([ADR 0032](adr/0032-label-free-drift-alarm.md)): for each version and task, a gambler bets against "traffic has not changed" using the rank of each new confidence score among the earlier ones. On unchanged traffic the bet's capital reaches 100 with probability at most 1% over the stream's whole life, however often anyone looks. When it does, a `drift` event says the traffic changed (not that the model got worse) and the version's certificate reads `suspended` (`curl -s localhost:8000/ratchet/drift`). In simulation (mock models), a product launch on day 5 raised the alarm on day 5 for six seeds out of six, and runs without the launch raised none.

## Proving what an adapter was trained on

Every adapter's signed bill of materials also commits to its exact training and golden sets ([ADR 0027](adr/0027-committed-training-sets.md)): one sparse Merkle root per set, over each example's hash mapped to a hash of its label, teacher, license and redaction count. From the BOM alone, anyone can then check a short proof that one request was, or was not, in an adapter's training set, and a held-out certificate that **no golden example was trained on**: invariants 3 and 6 as something you verify, not something you are told. The proofs show the operator's signed commitment, at training time, to the dataset; they do not show that the weights came from it. With guessable texts the proof endpoint would tell anyone what was trained on, so it needs an admin key.

```bash
curl -s localhost:8000/ratchet/versions/2/bom > bom.json
curl -s "localhost:8000/ratchet/demo/sample-example?version=2" > ex.json                    # mock demo: a committed prompt
curl -s "localhost:8000/ratchet/versions/2/proof?split=train" -H 'content-type: application/json' -d @ex.json > proof.json
python -m ratchet.proof verify proof.json --bom bom.json --expect included
curl -s localhost:8000/ratchet/versions/2/heldout > heldout.json
python -m ratchet.proof heldout heldout.json --bom bom.json   # OK: all N golden examples provably absent from the training set
```

## Replaying past traffic at another threshold

Ratchet designs its own labelling, so it knows the chance of every teacher label it sees: 1 for a request the student escalated, the audit rate for an answer it served. Each request logs that probability, and the **ledger replay** ([ADR 0028](adr/0028-designed-propensity-replay.md)) uses it to answer "what would last week's precision, coverage and cost have been at threshold t, or at audit rate a?" for one version, including thresholds below the live one, where audits alone see nothing. Coverage is exact; precision is a weighted estimate with an approximate interval (null when nothing labels the answers it would count); the frontier's suggested threshold is advice, never applied: thresholds change only through Night School. The replay also measures how far the baseline's 300-reasoning-token assumption is from what the teacher really costs. It adds no model call and no random draw to the request path.

```bash
curl -s "localhost:8000/ratchet/replay?threshold=0.4&audit_rate=0.05"     # what happened, and what would have
curl -s "localhost:8000/ratchet/replay/frontier?target=0.97"               # thresholds 0.30 to 1.00, an advisory suggestion
python -m sim.simulate --db sim.db && python -m ratchet.replay --db sim.db   # the same from a simulated run (simulation)
```

## Savings you can check

Ratchet's planned pricing is a share of the savings, so the savings must be checkable by the customer, not just reported. Each day gets a **savings receipt** for the gateway and one per API client ([ADR 0029](adr/0029-verified-savings-receipts.md)). The baseline of an answer the student served is calibrated on the audited answers' real teacher bills (a ratio estimator with a confidence bound), so a wrong reasoning-token assumption is measured and corrected, and the fee is a share of the one-sided 95% **lower bound** of the savings. Each receipt is an RFC 9162 Merkle tree over the day's request metadata (no text), signed and chained to the client's previous one: any request id a client got in `X-Ratchet-Request-Id` can be proven to be in it, and an edit after sealing shows. Receipts cannot prove the costs written at request time; reconcile them with the Token Factory bill.

```bash
curl -s localhost:8000/ratchet/receipts/3 > r3.json                            # one day's receipt (your own, with an API key)
curl -s localhost:8000/ratchet/receipts/3/proof/3006 > p.json                  # a row id you saw (ratchet.row_id) is in it
python -m ratchet.receipt verify r3.json --public-key <yours> --proof p.json  # OK: signature, OK: proof
python -m sim.simulate --receipts --teacher-reasoning-mean 420                 # the bound next to the true cost (simulation)
```

## The teacher watch

Every one of these numbers is agreement with the teacher, so the **teacher watch** checks the teacher too ([ADR 0033](adr/0033-teacher-watch.md)). A request that repeats is labelled by the teacher again when it escalates or is audited; its first re-test in each check epoch is compared with the label stored for it, at no extra cost, and Night School adds 20 probes a night. A label a re-test disagrees with leaves training and the golden set until two more teacher answers settle it. When the disagreements pass 5% by enough that a stable teacher would get there at most 1% of the time, a `teacher_drift` event pauses Night School until an operator acknowledges (`curl -s localhost:8000/ratchet/teacher`); a new model id in the teacher's responses starts a fresh epoch. In simulation (mock models), a teacher that changed 15% of labels from day 5 froze training on day 5 to 7 for six seeds out of six, and a stable one never did.

## Privacy and security

Built for real traffic, not only the demo ([threat model](THREAT_MODEL.md)):

- **PII redaction before training.** Emails, phone numbers, card numbers (Luhn-checked), IBANs, Aadhaar, SSNs, PANs, GSTINs, UPI ids, IP addresses and API secrets are replaced with placeholders like `[EMAIL]` before a request becomes a training example, so an adapter cannot memorise them. The teacher still sees the original. On by default. Extraction tasks can use numbered (`[EMAIL_1]`) or surrogate placeholders, consistent between the prompt and the answer, and names and addresses are covered when you plug in a detector such as Presidio ([ADR 0009](adr/0009-pattern-based-pii-redaction.md), [ADR 0082](adr/0082-placeholder-styles-detector-plugin-and-indian-identifiers.md)).
- **Gateway API keys** (`RATCHET_API_KEYS=acme:<key>,...`) with a daily quota per key, cost and savings per client, and each client's own signed savings receipts ([above](#savings-you-can-check)).
- **Private by default in live mode**: every route has an access level; the dashboard API needs an admin or read-only dashboard key unless you make it public, and then anonymous readers see client pseudonyms, not names. Live mode does not start without API keys, `/docs` is off, and every response carries `nosniff` and `no-referrer` ([ADR 0061](adr/0061-endpoint-access-levels.md)).
- **Streaming** (`stream: true`) in the OpenAI chunk format, so streaming clients work unchanged.
- **Request ids, failures, logs and metrics** ([ADR 0062](adr/0062-request-ids-failures-logs-and-metrics.md)): every response carries `x-request-id`, which also names the request's ledger row, its log line and its failures; failed requests are recorded with what they spent; live mode logs one JSON line per request with no prompt or answer text, and `/metrics` serves Prometheus metrics.
- **Signed bill of materials per adapter** ([ADR 0010](adr/0010-signed-adapter-bom.md)): teachers, licenses, dataset and golden-set hashes, the evaluation and, since v0.5, a set commitment per split ([above](#proving-what-an-adapter-was-trained-on)), written at training time; in live mode a copy goes into the adapter's folder in the bucket.

```bash
python -m ratchet.bom keygen                       # signing key + public key to publish
RATCHET_API_KEYS="acme:$(openssl rand -hex 24)" RATCHET_BOM_SIGNING_KEY=... make demo
curl -s localhost:8000/ratchet/versions/2/bom | python -m ratchet.bom verify - --public-key <yours>
```

The dashboard's **Trust and privacy** panel shows all of it, including a box where you can paste any text and see what would be stored.

![Trust and privacy panel (simulation)](img/trust.jpg)

