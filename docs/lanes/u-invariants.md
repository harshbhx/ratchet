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
