# ADR 0100: The dashboard writes API tasks with a key it holds in memory, and reads breaker states from the event log

- Status: Accepted
- Date: 2026-10-20

## Context

The task registry (ADR 0022) and the circuit breakers (ADR 0015, ADR 0019) had no place on the dashboard. An operator could read `GET /ratchet/tasks` only with curl and could change an API task only by writing JSON by hand, and nobody watching the demo could see a breaker open, although the mock-only outage drill (`POST /ratchet/demo/student-outage`) exists for exactly that.

Two questions decide how the new panels work:

1. **Should a browser page write to the admin API?** The API already guards its writes: `PUT`, `DELETE` and `POST /ratchet/tasks/validate` need a key from `RATCHET_ADMIN_KEYS` (403 in live mode without them; open in mock mode, where at most 50 API tasks exist and a reset clears them), a spec is checked strictly (422 with every error and its path), a built-in or file task cannot be changed (403), and only the key's name is stored (invariant 13). What the page must not add is a new place for the key to leak.
2. **Where does breaker state come from?** `Ratchet.health()` has each breaker's `snapshot()`, but no endpoint serves it yet (a later `/readyz` or `/metrics` may). `/ratchet/stats` serves the last 40 events, and every breaker transition is a `circuit` event (`student: closed->open after 5 failures (ConnectError)`), except the repeated open/half-open pair once the wait is at 300 s, which is logged only for the 1st, 10th, 100th and 1000th probe. This lane does not change Python behaviour, so it cannot add a field.

## Decision

- **Writes, with the key in memory only.** The Task registry panel registers, checks, changes and deletes API tasks through the existing endpoints. The operator types the admin key into a password field; the panel keeps it in React state, sends it only as `Authorization: Bearer` (`adminHeaders` in `web/src/lib/api.ts`), and never writes it to `localStorage`, `sessionStorage`, IndexedDB, a cookie or a URL. Reloading the page forgets it, and a "Forget" button clears it sooner. An empty key sends no header, which is what mock mode without admin keys expects. Built-in and file tasks are read-only in the page; "New task based on this" makes an API task that `extends` one. `test_dashboard_admin_key_stays_in_memory` scans `web/src` for any other use of browser storage and for the key reaching a URL (proposed invariant U-1, `docs/lanes/u-invariants.md`).
- **Breaker state from the log, said so.** The Resilience panel derives each breaker's state from its latest `circuit` event in `/ratchet/stats`: a breaker changes only when a call arrives, so the latest reported change is its state, except during a long outage at the 300 s wait, where it can be one probe behind (shown as open while a probe is out). The panel says this in a footnote, shows "No recent change" for a breaker none of the last 40 events moved (rather than claiming it is closed), and shows the student's and the teacher's breakers always and any other breaker the log names. The wait after a failed probe comes from the event's reason; the wait before the first probe is not reported, so the panel names the setting instead of a number. Fallbacks are counted from each task's `routes`, not from the throttled failure events.
- **One grammar, checked against the gateway.** The regexes the panel parses events with (`CIRCUIT_RE` in `web/src/pages/dashboard/model.ts`, `OUTAGE_RE` and `WAIT_RE` in `resilience-model.ts`) are read from the TypeScript source by `test_resilience_panel_contract`, compiled by Python, and checked against the events of a real outage drill and a teacher outage that reaches the longest wait; at every step the state they give must equal the breaker's real `snapshot()` state, except at the one documented lag. Changing the wording of `Ratchet._circuit_event` or the drill's `fault` detail fails that test until the panel's grammar follows.
- **The drill's countdown from its event.** The drill's end is the `fault` event's time plus its length, so every viewer sees the same countdown; the viewer who started it also corrects for the browser's clock offset, measured from the drill's response.

## Consequences

- An operator can manage API tasks without curl, with the same checks and error messages as the API, and the public mock demo gains nothing it did not already allow.
- A browser extension or a script injected into the page could still read the key while it is in memory, as with any page that sends one. The page loads no third-party scripts (web/CLAUDE.md), and the key is gone when the tab closes.
- The breaker panel is honest about its source but limited by it: after 40 newer events a breaker's last change scrolls out of view ("No recent change"), and the first wait is not shown. When an endpoint serves `Ratchet.health()`, the panel should read the snapshot first and keep the log for the history; that is a backend change for another lane.
- The event text is now an interface: `ratchet/CLAUDE.md` and `docs/ARCHITECTURE.md` say so next to the code that writes it.
