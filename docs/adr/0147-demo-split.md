# ADR 0147: The live demo's split

- Status: Accepted
- Date: 2026-10-26

## Context

[ADR 0146](0146-hosted-student.md) lets the untuned hosted Nemotron 3 Nano 30B answer a task only when its measured
records clear a bar, and today none does: with `RATCHET_HOSTED_STUDENT=1` Ultra still answers every request. A visitor or
a judge of the live demo therefore never sees a Nano answer, a real Nano price or a real audit of one. The owner decided
the live demo should show them, and show them as what they are.

## Decision

- **A switch, off by default.** `RATCHET_DEMO_SPLIT`, a share from 0 to 1 (0 is off), valid in live mode with
  `RATCHET_HOSTED_STUDENT=1` only (the startup check says otherwise). Each request that reaches the cascade is sent to the
  hosted Nano with that probability, whatever its confidence; otherwise the normal cascade runs.
- **Eligibility is the cascade's.** The split is drawn after every teacher-only decision: pinned tasks (Scout's planner and
  writer, INV-10), tool requests, an unregistered task under `RATCHET_UNKNOWN_TASKS=teacher` and requests the task's
  routing block keeps from the student never reach it. It also skips a request for `n` > 1 choices, a task with critical
  classes (nothing measured how often the Nano misses them) and a student an operator turned off. Scout's triage and
  reader calls and the free-text task are eligible.
- **Its own random draw.** `Ratchet.split_rng`, a secure generator: never `Ratchet.rng`, so no other draw moves and the
  pinned reference simulations stay byte-identical, and not seeded, so a visitor cannot predict it. With the split off no
  number is drawn.
- **One sample, no threshold.** A forced answer is one Nano sample, valid under the task and held to no confidence
  threshold (`requests.threshold` NULL, so the replay skips it). A rule of the task's `escalate_when` still sends it to
  Ultra, and an answer the task refuses goes to Ultra as for any unsure student.
- **Labelled, never disguised.** Every such answer has route `demo_split` (`router.ROUTES`), `ratchet.route` in the
  response, the Nano's model id and no version (ADR 0073). The dashboard and Scout say "Demo split: picked at random, not by
  the confidence gate" on the answer's badge, the dashboard overview shows the split's rate, share, audited agreement and
  savings from `/ratchet/stats` `demo_split`, and `docs/LIVE_MODE.md` says the same. The confidence gate decides only in
  normal mode.
- **Audited.** Each forced answer is audited at `RATCHET_DEMO_SPLIT_AUDIT` (default 0.25), drawn from the split's generator,
  so the dashboard shows how often the Nano agreed with Ultra. As for any audit, only Ultra's answer is stored, never the
  Nano's (INV-2). A forced row is no version's (student_version is the hosted row's, student_model the Nano): no release
  statistic counts it (ADR 0073), and no shadow runs for it.
- **Costs are real.** The row carries the Nano's real tokens at its price, the audits' Ultra price and the all-Ultra
  baseline, so the savings it shows are what the split saved on those calls. Spend meters and the Scout budget guard
  (INV-11) are unchanged.

## Consequences

- The demo shows real Nano answers and real savings; the price is quality: the Nano agrees with Ultra on about 2 of 3
  triage pairs and 1 of 2 reader pairs (`evidence/hosted-nano/`), so some Scout answers rest on Nano reads. That is why the
  label is on every such answer and the audited agreement is on the dashboard.
- Neither the split nor its audits change any release decision, certificate or BOM.
- Turning the split to 0 returns to ADR 0146's behaviour with no other change.
