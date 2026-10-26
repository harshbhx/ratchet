# ADR 0061: Endpoint access levels: a private dashboard API in live mode, scrubbed public reads and a live auth guard

- Status: Accepted
- Date: 2026-10-20

## Context

v0.4 protected `/v1/*` with named API keys, and ADR 0022 added admin keys for management writes. Everything else stayed public in every mode, also with `RATCHET_API_KEYS` set:

- `/ratchet/stats` lists every client by key name with its requests and spend, and the last 40 events. A v0.4 ledger's `student_error` events hold httpx's message, which names the student endpoint's URL (`ConnectError: ... https://student...`); today's events name the class and status only (`resilience.describe_failure`), but old rows stay.
- `/ratchet/bom`, `/ratchet/versions/{id}/bom`, `/ratchet/redact`, `/ratchet/cost` and `GET /ratchet/tasks` (task specs with their client allow-lists) answer anyone.
- `/docs` and `/openapi.json` hand anyone a map of every route, management writes included.
- Live mode started without `RATCHET_API_KEYS`, so a forgotten variable made `/v1/*` a public endpoint spending the operator's teacher credits.
- No response carried `X-Content-Type-Options` or `Referrer-Policy` unless Caddy added them, and there was no way to send a Content-Security-Policy (threat model T14).

The mock demo is different: it is synthetic and meant to be shown, and judges of a public demo (on the VM kit or on Cloudflare) should see the live dashboard too.

## Decision

- **One access table.** `ratchet/access.py` gives every route one level by method and path template (`ROUTES`, then `PREFIXES`): `public` (pages, health, the BOM public key, Scout), `api` (`/v1/*`, checked by `_client`), `dashboard` (read-only management data), `bom` (bills of materials), `admin` (management writes, checked by `_admin`) and `mock` (demo controls, `_mock_only`). A route the table does not know is `dashboard`, so a new endpoint is closed in live mode until someone classifies it, and a test fails until they do. The table already names routes other work packages add (`/livez`, `/readyz`, `/metrics`, `/ratchet/kpis`, anything under `/ratchet/requests/` or `/ratchet/versions/`), so they get the right level when they merge. docs/API.md's Access column is checked against it.
- **One gate.** The FastAPI app has one global async dependency, `_access`, which looks up the matched route's level and enforces the reader levels. Async, so `/v1/chat/completions` pays a dictionary lookup and no thread hop.
- **Read-only dashboard keys.** `RATCHET_DASHBOARD_KEYS` (the `RATCHET_API_KEYS` syntax) read what an admin key reads and write nothing. Each key has one role: a dashboard key that is also an API or admin key stops startup, as an admin key that is an API key already did. `_admin` answers a dashboard key with 403, not 401.
- **A private dashboard API in live mode.** `RATCHET_PUBLIC_DASHBOARD` defaults to 1 in mock mode and 0 in live mode. When 0, `dashboard` and `bom` routes need an admin or dashboard key (Bearer or `X-API-Key`): 401 with `WWW-Authenticate: Bearer` without one, 403 for an API key, and 403 naming the variables when no reader keys are set (as the admin API does without admin keys). `RATCHET_PUBLIC_BOM=1` keeps `bom` routes public for auditors.
- **Scrubbed public reads.** When the dashboard is public in live mode, an anonymous read of `/ratchet/stats` is scrubbed: clients become `client-1`..`client-n` by sorted name over the configured keys and the ledger's clients (stable while the keys stay the same), event details lose `http(s)://` URLs, and an error message after an exception's class is cut. The same pseudonyms replace the names in task allow-lists from `GET /ratchet/tasks`. A reader who sends an admin or dashboard key sees everything. `RATCHET_SCRUB_STATS=1` scrubs every read, `0` none. The cached payload (ADR 0060) is never changed: a scrubbed read is a shallow copy with three keys replaced, and `security.scrubbed` says which a reader got.
- **OpenAPI off in live mode.** `RATCHET_OPENAPI` (1 in mock, 0 in live) builds the app with or without `docs_url`, `redoc_url` and `openapi_url`.
- **A live auth guard.** `Runtime` checks access before it opens the ledger: live mode without `RATCHET_API_KEYS` raises unless `RATCHET_ALLOW_UNAUTHENTICATED=1`, which is for gateways something else guards (the demo VM's Caddy guard, a private network).
- **Security headers.** A plain ASGI middleware, outermost, adds `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` to every response (keeping a header a route set), and `RATCHET_CSP` when it is set. `_unhandled_error` adds them too, because Starlette sends a 500 outside every middleware. The CSP is opt-in so the React build and the lite pages never break by default; docs/CONFIGURATION.md has a value both work with.
- Switches take 0 or 1; anything else stops startup naming the variable (`config._bool`, `Settings.access()`).

## Consequences

- A live gateway is private by default: its dashboard API answers only admin and dashboard keys, it has no `/docs`, and it does not start without API keys by accident. The mock demo behaves as before.
- The dashboard page sends no key yet, so a private live dashboard shows no data in the browser. Operators read the API with a key, or set `RATCHET_PUBLIC_DASHBOARD=1` and let scrubbing protect client names (the demo VM's example does). A key prompt in the web app is left to the UI work.
- `/ratchet/cost`, which the auditor's list missed, is a dashboard read like `/ratchet/stats`, whose `cost` block it extends.
- Scrubbing matches patterns, so it is defence in depth for old rows and stray text, not a guarantee: the invariant that matters is still that events name exception classes, never messages (ADR 0013, ADR 0014). Admin names in events ("by ops") are operators' names and stay.
- Pseudonyms are stable only while the set of names is: a new API key can renumber them. Hash-based pseudonyms would be stable across key changes but harder to read on a demo dashboard; the spec asked for `client-1`..`client-n`.
- Behind the demo VM's Caddy, Caddy's own `Referrer-Policy` (`strict-origin-when-cross-origin`) replaces the gateway's.

## Alternatives considered

- **Leave the dashboard API public in every mode.** Rejected: it lists clients and endpoint URLs.
- **Rely on the Caddy edge guard only.** Rejected: local and Docker deployments have no proxy.
