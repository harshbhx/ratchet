# ADR 0008: Pinned tasks stay on the teacher and are never training data

- Status: Accepted
- Date: 2026-10-20

## Context

Scout ([SCOUT.md](../SCOUT.md)) sends four kinds of calls through Ratchet. Two are narrow and repetitive: labelling a search result (`source-triage`) and pulling a fact out of a page (`page-read`). Their answers have label fields that can be compared exactly. The other two are open-ended: planning queries (`research-plan`) and writing the final answer (`research-write`).

Ratchet decides whether the student agrees with the teacher by comparing answers. For open-ended text there are no label fields, so exact comparison almost never matches. Calibration would then set the threshold to "serve nothing", but every call would still pay for student samples and fill the dataset with examples the student cannot learn well. Training a 4B model on long free-text answers would also dilute what it learns for the labelled tasks.

## Decision

A task can be marked `pinned` in `ratchet/tasks.py`. A pinned task:

- always goes straight to the teacher (no student samples, no audits, no shadow);
- is logged in `requests` like any other call, so cost and the all-teacher baseline stay complete;
- is **never** stored as a training example.

`research-plan` and `research-write` are pinned. `stats_by_task()` reports the flag, and the dashboard shows it.

## Consequences

- Scout pays Ultra prices only for the calls that need a large model, and the student trains only on tasks it can be graded on.
- Savings shown for Scout include the pinned calls, so the reported percentage is honest about what cannot move.
- Pinned calls leave no provenance row in `examples`, because nothing is trained on them. Their teacher and cost are still in `requests`.
- A client cannot pin a task through a header. Pinning is a code-level decision in the registry.

## Alternatives considered

- **Route pinned calls around Ratchet** (agents call Token Factory directly). Rejected: the app would need two base URLs, and the cost picture on the dashboard would be incomplete.
- **Distill everything and let calibration decide.** Rejected: it wastes student samples on every open-ended call and adds unlearnable examples to the dataset.
- **Judge-based agreement for free text** (Nemotron 3 Super as judge). Kept for evaluation only (`LiveEvaluator.judge_pass_rate`). Using a judge per request would add a third model call and the judge's own bias to the request path.
