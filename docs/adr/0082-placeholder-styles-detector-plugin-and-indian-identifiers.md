# ADR 0082: Placeholder styles for extraction tasks, a detector plugin, and Indian identifiers and amounts

- Status: Accepted
- Date: 2026-10-21
- Amends: [ADR 0009](0009-pattern-based-pii-redaction.md)

## Context

ADR 0009 replaces every identifier with a bare label, `[EMAIL]`. That works for classification tasks, whose answers do not repeat identifiers, but not for extraction. A request with a sales address and a billing address and an answer `{"contact": "bo@y.io"}` was stored as `Sales: [EMAIL]. Billing: [EMAIL].` and `{"contact": "[EMAIL]"}`: the training target no longer says which address to copy, and the golden eval cannot tell a student that copies the wrong one from one that copies the right one. The prompt and the answer were also redacted with separate counters, so nothing tied a placeholder in the answer to one in the prompt.

Three more gaps came out of the gap audit and of testing:

- Names and street addresses need an NER model. ADR 0009 kept Presidio out of the package (a large dependency and per-request latency) and left NER as "the next step".
- Indian identifiers. A GSTIN (`27AAPFU0939F1ZV`) embeds its holder's PAN, but the PAN pattern needs a word boundary, so the GSTIN was stored unchanged. A UPI id (`name@okicici`) was stored unchanged, and `9876543210@ybl` became `[PHONE]@ybl`. Number fields read `₹` and `INR`, but not `Rs.`, a closing `/-`, lakh or crore.
- A refused match hid the identifier inside it. Each pattern ran once over the text; a match that failed its check was skipped whole. A card number followed by its CVV or expiry (`4111 1111 1111 1111 123`, `4111111111111111 12/27`) is one longer digit run that fails Luhn, so the card was stored as typed. So was a card after another number (`Pay 500 4111 ...`), or after a phone number; those two cases stay (see below).

## Decision

### Placeholder styles

`redact.Pseudonymizer(style, detector)` holds one example's mapping from a value to its placeholder. Three styles (`RATCHET_REDACT_STYLE`, or a task spec's `privacy.redact_style`, which overrides it for that task):

| Style | A value becomes | Notes |
|---|---|---|
| `label` (default) | `[EMAIL]` | v0.4's output, byte for byte, apart from the new UPI and GSTIN rules and a card or IBAN typed with a short tail (below) |
| `numbered` | `[EMAIL_1]`, `[EMAIL_2]`, ... | numbered per label in order of first appearance |
| `surrogate` | a reserved value of the same shape | emails `person1@example.com` (RFC 2606); phones `+12025550100` to `+12025550199`, then the next area code (555-0100 to 555-0199 are set aside for fiction in every North American area code); cards `400000000000NNNc` whose last digit is one more than the Luhn check digit, so they never pass Luhn and a second pass leaves them alone (their first 12 digits fail Verhoeff, so they never contain an Aadhaar either); IBANs `ZZcc` plus 15 digits with valid check digits under `ZZ`, an ISO 3166 code reserved for user assignment and never a country; IPv4 from the documentation networks of RFC 5737. Other labels, and a label past its last reserved value, fall back to the numbered placeholder |

- **One per example, then dropped.** `Ratchet._store_example` makes one `Pseudonymizer` per example and passes it to `redact_messages`, `redact_json` (the stored conditions) and `redact` (the answer), in that order. The same value gets the same placeholder in all three, nothing original is kept after the call, and nothing links two examples: every example starts again at `[EMAIL_1]`. Scout calls the cascade from several threads; each request has its own `Pseudonymizer`, so none is shared.
- **The request is numbered before the answer.** The example's hash covers its messages and conditions (ADR 0017). Numbering the answer before the conditions would make a stop string `[EMAIL_3]` after an answer that names a new address and `[EMAIL_2]` after one that does not, so one request would become two examples and the frequent-request count (INV-3) would split. For the same reason `redact_json` numbers an object's members in key order, as the example hash sorts them, whatever order the client wrote them in (the stored copy keeps the client's order).
- **When two spellings are one value.** Emails and UPI ids ignore case; cards, Aadhaar numbers and SSNs compare digits; IBANs, GSTINs and PANs compare letters and digits; a phone number is its last 10 digits, so `+91 98765 43210` and `98765-43210` are one value (and two numbers that differ only in their country code are one too; a lookup, never a scan, however many numbers a text holds); a detector's span ignores case and spacing.
- **Surrogates are one token each.** Phones are written in E.164 and IBANs in the electronic format, without spaces, so the digit groups around a surrogate cannot join it into a different match on a second pass. The IBAN has 15 digits after its check digits, so the IBAN pattern ends on a short group and cannot take a four-character word after it.
- **Comparators read placeholders as ordinary strings.** The golden eval samples the stored, redacted prompt, so it checks that the student copied the right placeholder.
- **Changing the style changes the stored text, so new rows hash differently.** The same request stored under `label` and then under `numbered` is two examples. This is a one-time break in deduplication for new rows; old rows are never rewritten. A task's `privacy.redact_style` is left out of `Task.spec()` and `spec_hash`: it changes how new examples are stored, not how answers compare, so it never sends the task back to the teacher for recalibration.
- **An erasure holds across styles and the plugin.** An erasure tombstones the example's hash (ADR 0081), which covers the redacted text, so the same request under another style, or with the plugin switched on or off, would hash differently and be stored again. An example stored otherwise than in the default (`label`, no plugin) therefore also records its key under the default redaction (`examples.canon_hash`: the example hash of the label-style, plugin-free redaction of its messages and conditions; NULL when that is its hash). An erasure tombstones both keys, and `Ledger.add_example` refuses a request when either of its keys is tombstoned. Because the default redaction is coarser than `numbered` or `surrogate` (it forgets which identifier repeats), an erasure also refuses a request that differs from the erased one only in which identifiers repeat. With the plugin on, two requests that differ only in a name the plugin replaced are one example; the row keeps the first one's plugin-free key, so a later request with the other name, sent after the plugin is switched off, is a different text and is stored. A change of the rules themselves (this ADR's UPI and GSTIN rules, for one) also changes what the default redaction writes: an erasure made before such a change does not cover the newly redacted form. Register the end user as no-train (FR-13.1) or erase again after upgrading.

### The detector plugin

`RATCHET_REDACT_PLUGIN='module:function'` (a dotted attribute path after the colon) names a `redact.Detector`, `Callable[[str], Iterable[tuple[int, int, str]]]`: the spans of a text it finds, `(start, end, label)` with `0 <= start < end <= len(text)` and a label of 1 to 32 letters, digits and `_` (upper-cased).

- **Imported at startup.** `Ratchet.__init__` calls `redact.load_detector`, which imports the module, resolves the attribute, checks it is callable and calls it once on an empty text, which must find nothing. A missing module, a missing attribute or a detector that fails on the probe stops startup with a message naming `RATCHET_REDACT_PLUGIN`.
- **Merged with the patterns.** The patterns' spans and the detector's are merged into one sorted list without overlaps: of overlapping spans the one that starts first wins, then the longer, then a pattern's; the winner's placeholder covers every span it overlaps, so no part of a name or an email is left in the text. Each span then gets its placeholder in the active style.
- **A failing detector stores nothing.** A detector that raises, or returns something other than valid spans, raises `RedactionError` at store time. The answer is still served; `_store_example` stores nothing and logs a throttled `skip_example` event `<task>: redaction failed`. The error names the problem or the exception's class, never the text or the detector's own message, which may hold what it was looking for. `POST /ratchet/redact` answers such a failure with a 500 carrying the same message (only callers with a key reach the plugin there; see Reporting).
- **On the request path.** The detector runs only when an example is stored (escalations, audits, arbitrations), once per message string, condition string and answer. Its latency adds to those requests; it must be thread-safe (guard it with a lock if it is not).
- **No dependency.** Nothing in the package imports an NER library. A Presidio detector is a few lines in the operator's own module:

```python
# my_ner.py, on the gateway's PYTHONPATH: RATCHET_REDACT_PLUGIN=my_ner:detect
from presidio_analyzer import AnalyzerEngine

_engine = AnalyzerEngine()  # loads the spaCy model once, at startup


def detect(text: str) -> list[tuple[int, int, str]]:
    found = _engine.analyze(text=text, language="en", entities=["PERSON", "LOCATION"], score_threshold=0.6)
    return [(r.start, r.end, r.entity_type) for r in found]
```

### Indian identifiers

- `UPI`: a handle of letters, digits, `.`, `_` and `-` (2 to 256 characters, not after `+`, `%` or a word character), `@`, then 2 to 64 letters with no domain after them. It runs after `EMAIL` (an email is already replaced) and before `PHONE`, which would otherwise take the digits of a mobile-number id. Handles that are package tags or branches (`latest`, `next`, `main`, `master`, ...) are not UPI ids, so `react@latest` stays.
- `GSTIN`: a 2-digit state code, the holder's PAN, an entity number (1 to 9 or a letter), a letter or digit (`Z` today), a check character. Format only, like PAN: the PAN inside is an identifier whether or not the check character is right. GSTN publishes the format but no authoritative specification of the check character's algorithm (the widely used mod-36 rule is reverse-engineered), and a checksum would only ever store more GSTINs unredacted, so none is used.
- The BOM's `redactor.sha256` covers the rules, so a BOM written from now on shows 11 rules and a new hash (v0.4's nine rules hash to `e85726…`; with GSTIN and UPI, `7100b2…`).

### Indian amounts

`compare.parse_number`, and so every `number` field, also reads `Rs.`, `Rs` or `Re.` before or after the number (any case), `rupees` after it, a closing `/-`, and `lakh`/`lakhs`/`lac`/`lacs` (10^5), `crore`/`crores` (10^7) and `lakh crore` (10^12) after it, scaled in decimal so `1.15 lakh` is exactly 115000. The Indian digit grouping (`1,23,456.50`), `₹` and `INR` were already read. `Cr` and `L` are left out: on a bank statement `Cr` means credit, and `L` cannot be told from a unit. Only strings that were invalid before gain a value; no value that parsed before changes.

### A card typed with a short tail is found

A card number followed by its CVV, its expiry month or one more short number (`4111 1111 1111 1111 123`, `4111111111111111 12/27`), or an IBAN followed by a year or a short word (`BE68 5390 0754 7034 2026`), is one match of the rule that its checksum refuses, and v0.4 skipped it whole. For `CARD` and `IBAN` only, a refused match is tried again: shorter, ending at each space inside it, longest first, and the first shorter match that passes the checksum is kept when

- nothing before the refused match joins it to more text: no letter, digit, `_` or `+` right before it, no `.` or `-` after a letter or digit, no space after a digit (the same number typed in groups; a match this rule kept before it ends a run), and
- either 1 to 4 characters of its run follow it (digits for a card, digits and capitals for an IBAN, one space or dash apart) and nothing after them joins them to more text (no letter, digit or `_`, no `.` or `-` before one: the `123` of `123.45.67.89` or of a phone number), or the rest of the refused match is the start of the next word (the `27` of `27AAPFU0939F1ZV`, a GSTIN, which the card rule read as a fifth group).

A short tail stays in the text as typed and is hidden from the later rules, which could not have matched it anyway. The search then goes on after the refused match, where v0.4's went on.

- **Everything else is v0.4's, byte for byte.** Only these few windows are new, so a long reference number (a tracking, policy or invoice number of 20 to 32 digits in groups of four) is redacted exactly as v0.4 redacted it: v0.4's own windows still find a chance card or Aadhaar in about one in ten such numbers, but no new window is tried. `test_label_style_matches_v04` checks 4,500 fuzzed texts and 8,000 reference numbers against a verbatim copy of v0.4's replacement; `test_a_refused_match_no_longer_hides_a_card` checks on 3,000 fuzzed texts that wherever the output differs, it is this rule.
- **What stays as v0.4 left it.** A card after another number on the same run (`Pay 500 4111 1111 1111 1111`), after an Aadhaar or a phone number one space before it, and two phone numbers one space apart are one digit run that no rule takes, as in v0.4; a second redaction of the stored text, which then sees a placeholder between them, can find more. A refused match is never tried from a word inside it, and `AADHAAR` (always exactly 12 digits) and `PHONE` (no checksum) are never retried.
- **Cost.** The tail is read at most five characters far, so the retry is linear in the text, like the patterns.

### Reporting

`POST /ratchet/redact` previews in the gateway's style or `?style=label|numbered|surrogate` and returns `style`, `plugin` (whether the detector ran) and `plugin_skipped`. The endpoint is public, so the detector, which may be an NER model costing tens of milliseconds a call, runs only for a caller with a valid API or admin key; anyone else gets the patterns alone and `plugin_skipped: true`, so a failing plugin cannot turn every public preview into a 500 either. `/ratchet/stats` `privacy` adds `redaction_style`, `redaction_task_styles` (tasks that override it) and `redaction_plugin` (whether one runs; never its name).

## Consequences

- Extraction tasks can be distilled from redacted data: the stored answer names the prompt's placeholder, and the golden eval scores whether the student picked the right one.
- Defaults are unchanged: `label` style, no plugin. The reference simulation and the pinned workloads do not move (the mock world has no identifiers).
- A redacted example redacted again is unchanged in every style, with one known exception: two numbers typed one space apart are one digit run, which the first redaction may leave (a card after an Aadhaar) or read as one (a surrogate card and the number after it, which the phone pattern takes on a second pass); the second redaction, which sees a placeholder or a surrogate between them, can then find more.
- A UPI-shaped token that is not a payment id (`root@server`, numpy's `a@b` written without spaces) is replaced as `[UPI]`. That loses a little text, never an identifier.
- The BOM does not record the style or the plugin: schema v2's sections are fixed (ADR 0024), and rows stored before a change keep the style they were stored under (a row's `canon_hash` says only that it was stored otherwise than in the default, not how). A later schema version can add them.
- `tombstones` holds canonical keys as well as example hashes. A canonical key never appears in `version_leaves`; whatever proves an erasure from committed sets (FR-16.9) takes the erased example hashes from `erasure_items`.
- For the integrator: `proof.request_key` (FR-16.4, lane F) recomputes the example key a request is stored under. With this change that key depends on the task's style and the detector: it must redact with `Pseudonymizer(task.redact_style or settings' style, detector)`, messages first and then the conditions, as `_store_example` does.

## Alternatives considered

- **Keyed hashes as placeholders (`[EMAIL_3f9a]`).** Consistent across examples, so the same customer would be linkable across the whole training set, which is the linkage redaction exists to prevent; and the student would learn to write meaningless hex.
- **A mapping table kept with the ledger.** Lets a placeholder be reversed, but stores every original value next to the data it was removed from.
- **Realistic fake values (Faker).** A generated name, phone or card can belong to a real person. The surrogates here are reserved and can belong to no one; where no reserved range exists (Aadhaar, PAN, GSTIN, UPI, secrets), the numbered placeholder is used.
- **Retry every checked pattern inside the first pass.** Tried first: a chance Luhn match across a phone number and an Aadhaar then took the text both would have matched, and part of each was left in the clear.
- **A second pass over what the first left, retrying every checksum rule from every word, then every rule again.** Built first, and dropped in review: about one window in ten passes Luhn or Verhoeff by chance, so trying every window of a long reference number cut it into `[CARD]`, `[AADHAAR]` and `[PHONE]` pieces (4,490 of 20,000 texts of the form `Ref <5 to 8 groups of four digits>.` changed), and a prompt could be stored redacted while its unspaced answer was not, so the training target held digits its prompt lacked, and the example hashes moved. The rule above changes only a card or IBAN with a short tail or a word after it.
