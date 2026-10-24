# ADR 0140: A recorded live Scout run, played back read-only

- Status: Accepted
- Date: 2026-10-21

## Context

The Scout budget guard (INV-11, [ADR 0120](0120-judge-access-and-a-persistent-scout-guard.md)) refuses runs once the day's budget, the run cap or the Tavily credits are used up, and a live call can fail while Token Factory or Tavily is down. A judge who arrives then meets an error. A live run also takes tens of seconds, so even a working demo is slow to show.

## Decision

- **Record once, play back for free.** The operator records one live run with `python -m ratchet.scout record "<question>" --base-url URL [--judge-code C] [--out DIR] [--allow-mock]`. It reads `GET /scout/stream` like the page does, stamps each event with `t_ms` since the first, and writes `DIR/<UTC compact time>-<slug>.json` (default `evidence/scout`, committed). Schema `ratchet.scout-recording/v1`: `recorded_at`, `host` (the host name only), `mode`, `question`, `duration_ms`, `events`, `totals`.
- **It refuses rather than writes a bad recording.** A stream that ends without `done`, holds an `error` event, does not begin with `start` or reports mock mode without `--allow-mock` exits 1 and writes nothing. The judge code is posted once to `POST /scout/judge` (the cookie stays in the HTTP client) and is never written; no header, key or cookie reaches the file (INV-13).
- **Two read-only public endpoints.** `RATCHET_SCOUT_RECORDINGS_DIR` (empty: off) names the folder. `GET /scout/recorded` lists up to 20, newest first; `GET /scout/recorded/{id}` serves one when the id matches `^[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,40}$`, the resolved path stays inside the folder, the file is at most 2 MB and the schema parses; anything else is 404. They make no model call, take no guard budget and write no ledger row, read files at request time (cached by modification time and size) and never write one. The gateway stores no visitor question (INV-12): only an operator records, with a question they chose.
- **Playback uses the page's own reducer.** The Scout page dispatches `begin` and then each event at its recorded offset (`web/src/lib/recording-model.ts`), so a recording looks like a run and is cancellable; "Skip to the end" delivers the rest at once. A badge replaces the live one: "Recorded live run" with the date and time in UTC, or "Recorded simulation" for a mock-mode recording (never offered as the live button). The button shows beside the question box when a live recording exists and in the error panel when a run is refused or fails.

## Consequences

- A judge always sees a real run, labelled as a recording, at its real pace, whatever the guard or the providers do.
- A recording shows the run's numbers as they were; it does not change with later prices or models, and its date says how old it is.
- The operator records one run after each deploy and commits it (RUNBOOK). A mock-mode recording is kept only with `--allow-mock` and is labelled a simulation.

## Alternatives considered

- **Run Scout live for every judge.** Rejected: the guard can refuse and live runs take tens of seconds.
- **Store visitors' questions to replay.** Rejected: INV-12.
