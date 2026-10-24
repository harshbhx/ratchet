# ADR 0131: A prompt that is golden under one task is golden under every task

- **Status:** Accepted
- **Date:** 2026-10-22

## Context

One adapter serves every task, but an example's identity, its split and its `seen` count all include the task:
`examples.hash` and the split hash are over `{task, messages}` ([ADR 0007](0007-golden-set-split-and-frequent-requests.md)).
The same messages sent under two tasks (a default task and an auto task, a client that names a task and one that does not,
two registered tasks with the same system prompt) are two examples with two independent coin flips. A prompt that is golden
under task A and train under task B is trained on through B, and A's golden precision (INV-6), the certificate and the
paired gate then partly measure memorisation. The same prompt seen twice under A and once under B is seen three times, but
never `FREQUENT` (3) under either.

A general fix, a per-task `split_on` key, was proposed (not in this repository) and is left as future work (see Consequences).
This ADR takes only the part that closes the cross-task hole and changes no hash.

## Decision

- **A prompt is one group across tasks.** `ledger.content_key` is the SHA-256 of the stored messages (`examples.content_hash`,
  written with the example; computed from `messages` for a row stored before the column existed; kept when a purge removes the
  text). It is the same whatever the task or the stored conditions.
- **`dataset.rows` groups by it.** `seen` adds up over the group, so `FREQUENT` counts occurrences across tasks. A group with a
  golden-only row stays golden (FR-4.50). A group that is `FREQUENT` trains. Otherwise a row is golden when its own split is
  golden or any row of its group is golden in the split, whatever the task. With one task this is exactly ADR 0007's rule.
- **Nothing stored changes.** Example hashes, stored splits, commitments and every simulation number stay as they were; the
  rule applies when rows are read. `dataset.rows(task=...)` still reads every task's rows to group them.
- **Not changed here.** `dataset.counts_as_train` (what a purge records as `purged_train`, and an erasure's affected versions)
  and `Ledger.prompt_status` (a golden-only label's check) keep their per-task key; they err toward counting a row. A legacy row
  whose text a purge removed before `content_hash` existed groups by its own task's prompt key.

## Consequences

- A prompt is never both a training row of one task and a golden row of another, so the golden number of every task is
  measured on prompts the adapter did not train on.
- A prompt shared by tasks becomes golden in all of them when one copy is golden. Training loses those copies until the
  prompt is `FREQUENT`.
- Per-task split keys (`split_on`: user message, first user message) remain future work. They address templated system prompts
  and long conversations within one task, which this rule does not.

## Alternatives considered

- **A per-task `split_on` key.** Left as future work (see Consequences).
- **Change stored hashes.** Rejected: it would move every commitment and simulation number.
