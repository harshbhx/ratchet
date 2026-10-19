# ADR 0003: Per-request cascade with a calibrated, per-version confidence threshold

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

A cascade saves money only if the student answers most requests, and it is safe only if it knows when it is wrong. Small models are poorly calibrated (Kadavath et al. 2022). Their token probabilities alone do not tell right from wrong, and self-consistency helps (Wang et al. 2022). Competitors route per *task* once a specialist passes evals, which wastes savings on easy requests within hard tasks and risks errors on hard requests within easy tasks.

In early simulation runs, one global threshold of 0.80 let about 10% wrong answers through.

## Decision

- **Per request:** the student is sampled `n=3` times (`temperature=0.7`, `logprobs=True`).
- **Confidence** = `0.4 × exp(mean token logprob of the best majority sample) + 0.6 × majority agreement`. If logprobs are missing, agreement alone is used; if every answer is invalid, confidence is 0.
- **Per-version threshold:** at training time, `cascade_eval` calibrates on even-indexed golden rows. The threshold is the lowest confidence where cumulative precision stays ≥ 0.97, with ties handled. It is clamped to [0.5, 1.01] and measured on the odd-indexed rows. The threshold is stored in `versions.eval` and used by `Ratchet.threshold(version)`.
- Agreement is judged per task on verifiable fields (`compare_keys`), not by an LLM judge.

## Consequences

- Positive: every request gets the cheapest safe path, and the threshold adapts as the student improves.
- Positive: precision and coverage are measured on held-out data, so the release gate uses honest numbers.
- Negative: 3 samples triple the student's output tokens (a small cost next to one teacher call) and add a little latency.
- Negative: tasks without structured fields fall back to exact text comparison, which is strict; those tasks need a judge-based gate later.

## Alternatives considered

- **Per-task routing** (Orchestra-style): simpler, but it gives up savings and safety within a task.
- **Trained router** (RouteLLM, Not Diamond): needs preference data; a possible later addition.
- **A single global threshold:** measured too permissive in simulation.
- **LLM-judge confidence:** costs a teacher-sized call per request, which defeats the purpose.
