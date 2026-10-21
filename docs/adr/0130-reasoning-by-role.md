# ADR 0130: Reasoning by role

- Status: Accepted
- Date: 2026-10-21

## Context

Nemotron 3 chat templates turn thinking on unless the call says otherwise (`enable_thinking`, read from the model cards on Oct 21, 2026). Ratchet set it nowhere, and with no GPU the live demo's student is the hosted Nemotron Nano on Token Factory:

- Each request samples the student `RATCHET_SAMPLES` times under a 1,024-token cap (`RATCHET_STUDENT_MAX_TOKENS`). A sample that reasons is cut at the cap, gets no vote and escalates, or it times out, so the hosted student would serve almost nothing and the demo would be teacher-only. Reasoning tokens are also in the logprobs vLLM returns, which skews the confidence score the threshold was calibrated on.
- The trained student is trained with thinking off ([ADR 0145](0145-train-on-the-served-prompt.md)) and the endpoint serves it with thinking off by default (W0). A request-level field is still needed: the `nano_v3` reasoning parser returns an empty `content` unless the request itself says `enable_thinking: false`.
- The teacher's mode was whatever Token Factory defaults to, with no way to change it.

Whether Token Factory accepts `chat_template_kwargs`, top level or inside `extra_body`, is **unverified** (its OpenAPI schema has `additionalProperties: true` and no such field). Day 1 checks it.

## Decision

1. **Operator-set template kwargs by role.** `GenParams.template_kwargs` carries `{"enable_thinking": false}` and the like. It is set only by `GenPolicy` (`student`, `teacher(role)`), never by a client, and is outside `PARAM_FIELDS`, every allowlist, the stored conditions and every hash. `chat_template_kwargs` and `extra_body` join `NEVER_FORWARDED`: a client's copies are accepted and ignored, because a label must not depend on who asked (threat model T2).
2. **The student is off everywhere.** `RATCHET_STUDENT_REASONING` is `off` (or `default`, which sends nothing); `on` and `medium` stop startup, since a student that reasons is not the model that was trained. Production, canary, shadow and both golden evals share it through `GenPolicy.student`. The hosted fallback student has its own setting, `RATCHET_FALLBACK_STUDENT_REASONING` (`off`), swapped in by `Ratchet._sample` when the resolver returns the fallback model.
3. **The teacher stays at `default` until day 1.** `RATCHET_TEACHER_REASONING` (serving) and `RATCHET_CHECK_REASONING` (audits and arbitrations; empty follows the serving mode) take `default`, `off`, `on` or `medium` (Ultra's `medium_effort`). A rejected field on the teacher would fail every escalation, so nothing is sent until a probe shows it works.
4. **The field's place is configurable.** `RATCHET_TEACHER_REASONING_FIELD` is `chat_template_kwargs` (top level) or `extra_body` (nested), for the Token Factory-bound providers: the teacher, the fallback teacher and the hosted fallback student. The vLLM endpoint always gets the top-level field (vLLM v0.18.0 reads it there).
5. **`/ratchet/stats` gains a `reasoning` block** with the modes and the field.

## Consequences

- The student loses some accuracy on prompts that need reasoning; those escalate to the teacher instead of being answered badly or cut off.
- The hosted student's every failure is fail-open (INV-7): if Token Factory refuses the field, each student call fails fast and the request escalates (route `student_error`). If it ignores the field, the result equals `default`. If it applies it, the student serves. Setting `RATCHET_FALLBACK_STUDENT_REASONING=default` removes the field.
- Stored labels do not record the mode they were written under, and the BOM is unchanged. Settings changes need a restart.
- The student's request body gains `chat_template_kwargs`; the tests that pin it are updated. The mock models ignore it, so no simulation number moved.

## Alternatives considered

- **The server flag alone** (`--default-chat-template-kwargs`): does not reach the hosted student, and fails with the reasoning parser on.
- **The student on with a larger token cap:** breaks the confidence score and the latency target.
- **A client-controlled field:** labels would depend on the caller.

## Not built here

A per-task `reasoning` spec key, measured teacher reasoning tokens (`RATCHET_TEACHER_REASONING_ESTIMATE`), `train.py` and the endpoint script changes: training and the endpoint are covered by ADR 0145 and W0.
