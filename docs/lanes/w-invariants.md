# Lane W: proposed invariants

Invariants lane W proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id. Each entry names its text, the code that enforces it and the tests that prove it.

## Honest labels (item W1-DEMO-HONEST-LABELS, FR-7.40)

### A hosted fallback answer is never shown as our adapter's

**Text.** A page names the model a call's `model` says answered it. A student answer whose `model` is `Settings.fallback_student_model` is labelled as the hosted fallback ("Nano 30B · hosted"), never "Nano · yours", and while the hosted fallback serves (`student_serving.kind == "hosted_fallback"`) no page shows the serving version's golden precision or certificate as if it described those answers, and the pages stop saying "your model". `student_serving.certified` is never true for the hosted fallback: a version's certificate was measured on its adapter, never on the hosted model. Badges get their words from `whoLabel` only; a new badge or page that names who answered goes through it.

**Enforcing code.** `ratchet/gateway.py`: `Runtime.student_serving`, `stats`, `scout_status`; `web/src/lib/who-model.ts`: `whoLabel`, `studentCopy`, `precisionShown`, `servingLine`, `studentShareCopy`; `web/src/components/ui/who.tsx`: `WhoBadge`; `web/src/pages/scout/state.ts`: `derive`.

**Proving tests.** `tests/test_live.py::test_hosted_fallback_is_never_certified`, `test_stats_student_serving_kinds`, `test_hosted_fallback_response_names_the_fallback_model`; `tests/test_contract.py::test_who_model_helpers` (`web/tests/who-model.test.mjs`), `test_student_serving_kinds_match_the_ui`.

## Recorded Scout runs (item W2-SCOUT-RECORDED-RUN, FR-10.40, NFR-71)

**Proposed:** The gateway never writes a Scout recording and never stores a visitor's question; a recording holds the operator's question, the run's events, the host name and the totals, never a header, an API key or a judge code. Enforced by `ratchet.scout.record` (only the CLI writes; the endpoints only read) and proved by `test_recording_never_holds_secrets`, `test_recorded_endpoints` and `test_recorded_endpoints_spend_nothing` in `tests/test_scout.py`.
