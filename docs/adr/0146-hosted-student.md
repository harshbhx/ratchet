# ADR 0146: The hosted student, calibrated from real labels

- Status: Accepted
- Date: 2026-10-26

## Context

The public live gateway has no trained version and no student endpoint, so Ultra answers every request. The gateway already
knows the untuned, hosted Nemotron 3 Nano 30B on Token Factory (`RATCHET_FALLBACK_STUDENT`): it answers student calls for a
trained version while the endpoint is off, under the global `RATCHET_CONFIDENCE_THRESHOLD`, and its answers count toward no
release ([ADR 0073](0073-fallback-attribution.md)). With no version at all it is never called. We wanted the live site to show the
cascade on real models: the hosted Nano answering the calls it is sure of, Ultra the rest, with real costs.

Two things had to hold. The threshold had to come from data, not a guess, measured the way a night measures a version.
And nothing about the hosted Nano could read as our adapter or move a release.

## Decision

- **A switch, off by default.** `RATCHET_HOSTED_STUDENT=1` in live mode. With it, the gateway keeps one version row in a new
  state, `hosted` (`Ledger.ensure_hosted`): base model the hosted id, `adapter_uri` empty, no training examples, and an eval
  that holds per-task records. The row is created or brought up to date at startup, with a `hosted` event that says what it
  answers. No release move starts from `hosted`.
- **It stands in only while nothing is live.** `Ratchet._pick_version` returns it when no version is live, after the
  canary's draw (so the random stream does not change). A live version always wins; `student_disabled` and the switch off
  send everything to the teacher as before.
- **Per-task records, the existing machinery.** `Ratchet.threshold` holds the row to its records through
  `calibration.threshold`, with a global threshold that serves nothing, and `Ratchet._held_back` keeps every task without a
  serving record from being sampled (route `task_disabled`, the teacher answers and its answer is stored as on a cold
  start). The resolver always sends the row to Token Factory, also with a student endpoint set: it has no adapter to load.
- **Attribution as ADR 0073 says.** The row's adapter is empty, so its answers are never the version's own: the response
  names the hosted model and no version, audits run as drawn and store Ultra's answer (INV-2), and no release statistic
  counts them. The `unattributed` event is not written for it (there is no release to hold). `/ratchet/stats` leaves the
  row out of `versions` (Night School trained none of it) and `student_serving` says `hosted_fallback`, with each task's
  threshold in `tasks`, only while it answers some task, so the pages' existing "Nano 30B · hosted" badge, with its
  "untuned" tooltip, names it.
- **Records from real labels.** `ratchet/hosted.py` holds the records; `scripts/hosted_nano_calibration.py` computes them
  from `evidence/hosted-nano/` with `cascade_eval` (even rows calibrate, odd rows measure, the default 97% target), and a
  test recomputes them. A task serves only at 95% held-out precision against Ultra on at least 20 served rows, the audits a
  canary needs before it moves. The records hold for one model, three samples and thinking off: any other setting stops
  startup.

## Measured result (26 Oct 2026)

132 rows from the 33 real search results in the recorded Scout runs, labelled by Ultra as an audit would, $0.157 in all
(`evidence/hosted-nano/README.md`):

- source-triage: threshold 0.9550; 7 of 46 held-out rows served, all 7 right, but 6 of them are easy cross pairs (a result
  paired with another run's question). On the 33 production-like pairs the Nano agrees with Ultra on 24 and would answer 3.
- page-read: 2 of 4 served held-out rows right.
- Ultra agrees with its own earlier label on 30 of the 33 triage pairs, so 95% against Ultra is near the teacher's own
  consistency on `source_type`.

Neither task met the bar. With the switch on, the row exists and the teacher still answers every request.

## Alternatives considered

- **No row, a router path for "no version".** Every part of the cascade (sampling, thresholds, audits, attribution, the
  ledger's `student_version`) is keyed on a version; a parallel path would duplicate them.
- **A live version that names the hosted model.** The pages would show "Live version v1" and a trained-looking row, Night
  School would re-score it as the champion, and the live alert would act on another model's answers.
- **The global `RATCHET_CONFIDENCE_THRESHOLD` (0.80), as the fallback uses.** At 0.80 the Nano answers 58 of 99 triage rows
  at 81% agreement: a guess, and a poor one.
- **A lower bar for the live site.** A choice for the owner, not this change: the records are recomputed with one command.

## Consequences

- The switch is safe to set: today it adds a row, an event and the route `task_disabled` for every distillable request.
- When better data clears the bar, the records change in one reviewed commit and the live site's hosted Nano answers those
  calls with no other code change. More recorded Scout runs (own pairs) are the data that would decide it.
- A new built-in spec for a task makes its record stale, so the teacher answers it until the records are measured again.
