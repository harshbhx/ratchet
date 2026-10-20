# Lane U: proposed invariants

Invariants this lane proposes for the numbered list in `CLAUDE.md`. They are kept here, not numbered, because the lanes build in parallel and numbers would collide; the integrator assigns numbers when the lanes merge.

## U-1. An admin key typed into the dashboard stays in the page's memory

Text: The dashboard never writes an admin key to `localStorage`, `sessionStorage`, IndexedDB, a cookie or a URL, and sends it only as an `Authorization: Bearer` header on management calls. The only thing the web app keeps in browser storage is the theme. See [ADR 0100](../adr/0100-dashboard-task-writes-and-derived-breaker-state.md).

- Enforcing code: `web/src/pages/dashboard/registry.tsx` (`adminKey` is React state in `TaskRegistryPanel`), `web/src/lib/api.ts` (`adminHeaders`, used by `validateTask`, `putTask` and `deleteTask`), `web/src/pages/dashboard/task-drawer.tsx` (a password field with `autoComplete="off"`).
- Proving test: `tests/test_contract.py::test_dashboard_admin_key_stays_in_memory` (a scan of `web/src`: browser storage only in `lib/hooks.ts` for `ratchet-theme`; no file that holds the key touches `history.pushState`, `location` or `URLSearchParams`; `api.ts` uses the key only through `adminHeaders`). Checked in the browser too (Playwright during development: no storage write and no cookie or URL holding the key after a save with `RATCHET_ADMIN_KEYS`).

## U-2. Dashboard status is what the gateway reports, parsed with a grammar the gateway's own events pass

Text: A status the dashboard shows (a breaker's state, the outage drill's countdown, a failed probe's wait) comes from a field or an event the gateway sent, parsed with the regexes in the dashboard's source; it is never assumed. A breaker no recent event moved is shown as "No recent change", not as closed. Changing the text of a `circuit` event or the outage drill's `fault` event requires changing the dashboard's grammar in the same change.

- Enforcing code: `web/src/pages/dashboard/model.ts` (`CIRCUIT_RE`, `parseCircuit`), `web/src/pages/dashboard/resilience-model.ts` (`OUTAGE_RE`, `WAIT_RE`, `breakerViews`, `lastOutage`), `ratchet/router.py` (`Ratchet._circuit_event`), `ratchet/gateway.py` (`demo_student_outage`).
- Proving test: `tests/test_contract.py::test_resilience_panel_contract` (the regexes read from the TypeScript source match every event of an outage drill and of a teacher outage to the longest wait; the state they give equals `Ratchet.health()["breakers"][name]["state"]` at every step, except the documented one-probe lag at the longest wait; the parsed waits are the breaker's real waits).

## U-3. Every simulation number on the story page is the reference run's

Text: A number the story page prints from the reference simulation (`web/src/lib/sim.ts`: the days and totals of "Watch it learn", and `SIM_COST_REPORT`, the same run priced per token and by GPU-hours, with its break-even, crossover, recommendation and explanation) is the output of a fresh run of the command the page prints, at the precision its literal is written with, and is labelled Simulation wherever it appears. A change that moves the simulator or the cost report updates the literals in the same change.

- Enforcing code: `web/src/lib/sim.ts` (`SIM_DAYS`, `SIM_TOTAL`, `SIM_COST_REPORT` as strict JSON up to its `// end` marker, `SIM_COMMAND`), `web/src/pages/landing/bill-model.ts` (`billOf`, `simulateLine`: the reference report renders through the code a live one uses), `web/src/pages/landing/sections/bill.tsx` and `learn.tsx` (the Simulation badges and `SIM_SOURCE`).
- Proving test: `tests/test_contract.py::test_story_simulation_numbers_match_reference_run` (every leaf of `SIM_COST_REPORT` against `reference_run["cost"]`, every day and total against the run, the command parsed into the reference inputs, the printed GPU-hour line pinned in `web/tests/story-model.test.mjs`), and `test_story_page_helpers` (the formulas the page prints hold for its rounded numbers).

## U-4. The story's trust claims are what the code does

Text: The "Built to be checked" chapter claims only what the code does today, and every claim names how to check it. The files it names exist, its redaction list is the redactor's rules, its gate numbers are the release gate's defaults, the invariants it cites say what it says, the routes it names are served, the commands it prints work and print what it shows, and work still being built is named only as v0.5 where the PRD plans it and its foundations are in the code.

- Enforcing code: `web/src/pages/landing/trust-model.ts` (the claims as one-line constants: `TRUST_FILES`, `TRUST_REDACTS`, `TRUST_GATE`, `BOM_FETCH`, `BOM_VERIFY`, `BOM_OK`, `BOM_TAMPERED`, `REQTRACE_CHECK`, `GATE_TESTS`), `web/src/pages/landing/sections/checked.tsx`.
- Proving test: `tests/test_contract.py::test_story_trust_claims_match_code` (against `redact.PATTERNS`, `ReleasePolicy()`, CLAUDE.md's invariants, the gateway's routes, a signed BOM fetched from a mock gateway and verified with `python -m ratchet.bom verify` as printed, `.github/workflows/ci.yml` and `docs/PRD.md`).
