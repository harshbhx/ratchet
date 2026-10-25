# ADR 0011: Requirements traceability from PRD rows and test markers, checked in CI

- Status: Accepted
- Date: 2026-10-20

## Context

Up to v0.4 the PRD said what was "Done" and the tests said what was checked, but nothing connected the two. None of the 64 tests named a requirement. Statuses had drifted into free text ("Done (v0.4); the demo VM also has an opt-in edge guard in Caddy", "Tavily untested live"), NFR-5 still called gateway auth "planned" after FR-1.7 shipped it, the §7 and §8.1 rows had no ids or statuses, and docs claimed 64 tests in one place and 49 in another. Ratchet's pitch is that savings and quality come with receipts ([PRD §2.3](../PRD.md#23-buyer-caution)); the same has to hold for the product's own claims. The repo is also changed by several agents in parallel, so a rule that lives only in a doc is not enough.

## Decision

- **Ids live where the text lives.** FR, NFR and SM rows in `docs/PRD.md` (an ID and a Status column), US stories in their bold paragraphs, and invariants as INV-n, the number in the `## Invariants` list of `AGENTS.md`. Ids never change, so invariants are never renumbered.
- **A closed status grammar** (PRD §6): `Done (vX)`, `Done (vX), untested (live|on GPU|on Nebius)`, `Done (vX); <text> planned`, `Partial: <text>`, `Planned`, `Replaced by FR-a.b`, and for metrics `Met in simulation (<text>)`, `Not met (<text>)`, `Not checked`. Anything else is an error, so a typo cannot quietly turn "done" into "not done".
- **Tests name what they prove** with `@pytest.mark.req("FR-2.1", "INV-7")`, registered in `pyproject.toml` with `--strict-markers`.
- **One stdlib script**, `scripts/reqtrace.py`, holds the rest: `CODE` (where each done requirement and invariant is implemented, resolved by import or file path), `EXEMPT` (checked by hand or only live, with the file that holds the check) and `KNOWN_GAPS` (done but untested). It generates `docs/REQUIREMENTS.md` and, with `--check`, fails on the rules listed in [TESTING.md](../TESTING.md#traceability). It runs in `make lint`, as a CI step and as `tests/test_traceability.py`.
- **Gaps are recorded, not hidden, and only shrink.** `KNOWN_GAPS` must stay a subset of the frozen `INITIAL_KNOWN_GAPS` (30 ids when this landed). A known gap or manual exemption that gains a test must be removed. An xfail or skipped test never counts as proof; a `Planned` id or a `Not met` metric is cited only by a strict xfail, which fails once the thing works and forces a status update.
- **Doc conventions are checked by the same script**: every gateway route is in `API.md`, every environment variable read under `ratchet/` is in `CONFIGURATION.md`, and every "N tests" claim matches the parametrize-expanded count, which a test cross-checks against `pytest --collect-only`.

## Consequences

- A behaviour change touches the PRD row, the test marker and the matrix in the same commit; CI says exactly which of them is missing.
- `docs/REQUIREMENTS.md` changes whenever a marked test is added or renamed, so it must be regenerated (`make trace`); the check names the first stale line.
- The 30 known gaps are visible in the matrix (shadow mode, canary split, Night School skip rules, champion vs challenger, several invariants). Closing one is a small, well-defined task.
- `CODE` references are only as good as the person writing them: the check proves they resolve, not that they are the right function. Markers are reviewed like any other test change.
- Live-only items (day-1 checks, the VM, GPU training) stay honest: `untested (live)` needs both an offline test (or a known gap) and a `live` exemption naming the script that checks it.

## Alternatives considered

- **A hand-maintained matrix** (spreadsheet or Markdown). Goes stale at the first refactor, which is the problem this solves.
- **Requirement ids in docstrings or comments.** Invisible to pytest, easy to misspell, and they cannot express xfail.
- **A third-party plugin or ALM tool** (pytest-reqs, Doorstop, a test-management SaaS). More setup than the problem needs, an extra dependency in CI, and the PRD would no longer be the single source of truth.
- **Requiring a test for every row with no exemptions.** UI layout, GPU training and the day-1 checks cannot run offline; forcing tests there would produce fake tests. Exemptions name the file that holds the real check instead.
