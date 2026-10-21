# Lane E2: proposed invariants

Lane E2 (license notices and portability, feedback and labelled-data import) does not number invariants in `CLAUDE.md`,
because lanes build in parallel and the numbers would collide. Each proposal below is enforced and tested in this
branch; the integrator gives a new invariant the next free number in `CLAUDE.md`'s list (or folds an amendment into the
invariant it extends), adds its `CODE` entry in `scripts/reqtrace.py`, and adds the number to the proving tests'
`@pytest.mark.req` markers.

## Amendment to invariant 1: configuration cannot add a closed teacher (LIC-NOTICES-PORTABILITY)

**Text (append to invariant 1 in CLAUDE.md).** Open teachers may also come from `RATCHET_TEACHER_LICENSES`
(`licenses.install`, merged into a copy of `BUILTIN` at startup), but only under a license in
`licenses.ALLOWED_LICENSES` (OpenMDW-1.1, Apache-2.0, MIT, NVIDIA Nemotron Open Model License) and for a model id no part
of which starts with a `CLOSED` prefix, contains `gpt`, `claude` or `gemini`, or contains a closed vendor's name
(`licenses.closed_family`, `licenses.CLOSED_VENDORS`); a configured entry never replaces a built-in one, and
a bad file stops startup. Never widen the allowlist or loosen the closed-family check without a reviewed ADR. See
[ADR 0090](../adr/0090-licenses-travel-with-the-adapter.md).

**Enforcing code.**

- `ratchet.licenses:parse_entries` (the allowlist, the closed-family check, no built-in override, no duplicates)
- `ratchet.licenses:closed_family` (any part of the id: a closed prefix at its start, a family name or a closed vendor's
  name anywhere in it; stricter than `lookup`'s last-segment rule)
- `ratchet.licenses:lookup` (defence in depth: a configured entry naming a closed family resolves to the closed terms)
- `ratchet.licenses:install` (publishes `{**BUILTIN, **configured}` in one assignment; a bad file leaves the registry
  as it was) and its callers at startup: `ratchet.gateway:Runtime.reset`, `ratchet.router:Ratchet.__init__`,
  `ratchet.live:main`, `ratchet.admin:main`

**Proving tests** (`tests/test_licensing.py`, marked `INV-1`).

- `test_config_teacher_license_allowed_and_closed_prefix_still_refused`: `gpt-4o`, `azure-gpt-4o`, `chatgpt-4o-latest`,
  `openai/davinci-002`, `anthropic/next-model`, `openai/gpt-oss-120b`, `claude-3-5-sonnet`, `google/gemini-2.5-pro` and
  others claimed as Apache-2.0 are refused; unknown or restrictive license names are refused; a built-in teacher cannot
  be relabelled; a closed id forced into the registry still raises `LicenseError`.
- `test_config_teacher_end_to_end`: without the file the configured teacher is unknown (refused at startup, its rows
  out of training).
- `test_bad_teacher_license_file_fails_at_startup`: every entry point stops on a bad file before it opens anything.

## Extension of invariant 14: the NOTICE is part of the signed BOM (LIC-NOTICES-PORTABILITY)

**Text (append to invariant 14 in CLAUDE.md).** A BOM written by `bom.manifest` carries the base model's license
(`base_model_license`) and the exact `NOTICE.md` text with its SHA-256 (`notices`). Whatever writes a NOTICE next to a
trained adapter from its BOM (live Night School's `publish_bom`, `python -m ratchet.live export`) writes the text the BOM
signed (`notices.bom_notice`), never a re-rendered one; only a BOM without `notices` (v1, or v2 written before NFR-9) gets
a NOTICE rendered from what it records.

**Enforcing code.** `ratchet.bom:manifest`, `ratchet.nightschool.notices:bom_notice`,
`ratchet.nightschool.notices:write_adapter_files`, `ratchet.live:publish_bom`, `ratchet.live:export`.

**Proving tests** (`tests/test_licensing.py`, marked `INV-14`).

- `test_bom_v2_has_base_model_license_and_v1_still_verifies`: the signature covers the NOTICE text (editing it fails
  `verify`); v1 and early v2 documents still verify and get a rendered NOTICE.
- `test_export_bundle`: the exported `NOTICE.md` is the BOM's text, replacing the training job's copy; a stored BOM
  that does not verify is refused.
