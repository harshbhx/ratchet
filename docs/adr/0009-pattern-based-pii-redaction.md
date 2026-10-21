# ADR 0009: Pattern-based PII redaction before examples are stored

- Status: Accepted
- Date: 2026-10-20

## Context

Every escalated or audited request becomes a training example: the request text and the teacher's answer are stored in `examples`, written to `train.jsonl` in the bucket, and trained into a LoRA adapter. Adapters can memorise what they are trained on, so a customer's email address or card number in one support ticket can come back out of the student for someone else. The PRD (FR-4.7, R9) and the threat model (T4) planned "Presidio redaction before storing examples".

Presidio finds names and addresses with a spaCy NER model. That brings a large dependency (about 500 MB with a language model), slower startup, and per-request latency on the request path, which `ratchet/CLAUDE.md` asks us to keep free of slow work. Most of the identifiers that do real harm in support and agent traffic (emails, phone numbers, card numbers, bank accounts, national ID numbers, IP addresses, leaked API keys) have fixed formats, and several have checksums.

## Decision

`ratchet/redact.py` replaces identifiers with `[LABEL]` placeholders using regular expressions, with a checksum where the format has one:

| Label | Check |
|---|---|
| `EMAIL`, `IP`, `SSN`, `PAN`, `SECRET` | format only |
| `CARD` | Luhn, 13–19 digits |
| `IBAN` | mod-97 |
| `AADHAAR` | Verhoeff |
| `PHONE` | 10–15 digits |

- `Ratchet._store_example` (called for every teacher answer by `_call_teacher`; before v0.5 `_call_teacher` did it itself) redacts the messages and the teacher's answer before `Ledger.add_example`. The teacher receives the original request, because it needs the details to answer well. `redact_messages` covers every field of a message that can carry what the user typed: content (strings and text parts), `name`, an assistant's `refusal`, and tool and function call arguments (v0.5; tool arguments often repeat an email or an order's customer details). Message keys the gateway does not use are dropped by `normalize_messages` before this point ([ADR 0012](0012-openai-request-validation-and-error-envelope.md)), so nothing reaches the ledger unredacted.
- The number of placeholders is stored per example (`examples.redactions`), reported in `/ratchet/stats` (`privacy`) and in each adapter's BOM (`examples_with_pii_redacted`).
- On by default (`RATCHET_REDACT_PII=1`). The hash, and so the golden split and the frequent-request count, are computed on the redacted text.
- `POST /ratchet/redact` shows what would be stored for any text, so operators can test it on their own data.

## Consequences

- No new dependency and no measurable latency: the redactor runs only on the teacher path, in microseconds.
- Names and street addresses are **not** removed. The threat model and the docs say so; NER-based detection stays planned.
- Two requests that differ only in an identifier become one example (same redacted hash), which counts towards the frequent-request rule. That is the desired behaviour: the student should learn the pattern, not the identifier.
- The student learns to write placeholders where the teacher wrote an identifier from the request. For label tasks this does not matter; for free-text replies it can.
- The mock world contains no identifiers, so mock simulation numbers are unchanged; a test checks that redaction never changes a synthetic request.

## Alternatives considered

- **Presidio in the gateway.** Better recall on names and addresses, but a heavy dependency and slower requests. Kept as the next step, behind the same `redact()` interface.
- **Redact before calling the teacher.** Protects data from the teacher provider as well, but worse answers for requests that need the details (an order lookup, a reply that addresses the customer). Token Factory's zero-data-retention setting is the better control for that.
- **Drop examples that contain PII.** Loses the most realistic training data and biases the dataset.

## Amendment (v0.5): placeholder styles, a detector plugin, Indian identifiers

[ADR 0082](0082-placeholder-styles-detector-plugin-and-indian-identifiers.md) extends this decision without changing its default:

- `[LABEL]` stays the default style. `RATCHET_REDACT_STYLE=numbered` (`[EMAIL_1]`) or `surrogate` (`person1@example.com`), or a task spec's `privacy.redact_style`, keeps two identifiers apart for extraction tasks: one `redact.Pseudonymizer` per example gives a value the same placeholder in the prompt, the stored conditions and the answer, and is dropped after the example is stored.
- Names and addresses can be found by an operator's detector (`RATCHET_REDACT_PLUGIN='module:function'`, such as a Presidio wrapper), imported at startup, which fails when it is missing. The package still adds no NER dependency. When the detector fails at store time, the answer is served and nothing is stored.
- Two more rules: `UPI` (checked against a stop-list of package tags such as `latest`) and `GSTIN` (format only, like `PAN`). The BOM's `redactor` section shows 11 rules.
- The checksum rules (`IBAN`, `CARD`, `AADHAAR`) search again in what the first pass left, so a card number followed by its CVV or expiry is no longer stored as typed. Everything the first pass finds is exactly what v0.4 found.
