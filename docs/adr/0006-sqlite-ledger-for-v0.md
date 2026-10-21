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

## Amendment (2026-10-20, [ADR 0060](0060-hot-path-caches-and-throttled-tick.md)): the WAL journal

- A ledger file now runs in SQLite's WAL journal with `synchronous=NORMAL` and a 5 s busy timeout (`RATCHET_DB_SYNC=NORMAL`, the default). Readers no longer block the writer, so the gateway and a `python -m ratchet.live` cron job on the same file conflict only writer against writer, for the length of one commit, and dashboard reads use a second, read-only connection. Request rows are still written synchronously on the request path, but a commit no longer waits for an fsync.
- The price: a power cut or an operating-system crash can lose the last commits (the file is never corrupted). `RATCHET_DB_SYNC=FULL` keeps the rollback journal with `synchronous=FULL`, this ADR's original behaviour.
- WAL needs shared memory: every process must open the file on the same host, from a local disk (never a network or bucket mount; use `FULL` there). Back up with the backup API (`.backup`), which copies committed WAL content too; a plain file copy can miss it. Before restoring a copy, remove the old `ratchet.db-wal` and `ratchet.db-shm`.
- Still one gateway process and one uvicorn worker: the caches ADR 0060 adds are per process.
