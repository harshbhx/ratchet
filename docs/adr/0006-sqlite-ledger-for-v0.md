# ADR 0006: SQLite ledger for v0

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

Ratchet needs durable storage for requests, provenance-tagged examples, versions and events. Hackathon scale is thousands to tens of thousands of requests per day, from one gateway process.

## Decision

- A single SQLite file (`RATCHET_DB`, default `ratchet.db`; `:memory:` in mock mode) behind the `Ledger` class, with four tables: `requests`, `examples`, `versions` and `events`.
- One connection (`check_same_thread=False`) per process, with a commit after every write. Run the gateway with **one worker**.

## Consequences

- Positive: zero setup, easy to back up (`.backup`), easy to inspect with `sqlite3`, and fast enough at this scale.
- Negative: no concurrent writers. The gateway and `python -m ratchet.live` cron jobs on the same file can conflict, since SQLite locks the file during writes. Keep them on one host and avoid overlapping Night School with heavy traffic.
- Negative: only a minimal migration step. `Ledger._migrate()` adds missing columns listed in `MIGRATIONS` on open (v0.1.1); renames, type changes and data moves are not handled.
- Negative: request rows are written synchronously on the request path.

## Alternatives considered

- **Postgres:** the plan for multi-tenant Ratchet Cloud (with an append-only events table and migrations), but too much setup for the hackathon.
- **Files in the bucket only:** no queries or atomic state changes.
