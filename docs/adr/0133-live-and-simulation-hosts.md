# ADR 0133: Live and simulation hosts

- Status: Accepted
- Date: 2026-10-22

## Context

The hackathon's pass/fail Stage One needs a runtime call to Token Factory, so the hosted gateway runs in live mode from the Oct 29 submission until judging ends on Dec 15, 2026. In live mode INV-8 makes every demo control answer 403 (`_mock_only()`), which is right and stays. But a judge at the URL then sees no rollout, no rollback and no learning curve, which is the loop the project is about. A live ledger with the student endpoint stopped is also teacher-only. Judges arrive at any time between Dec 1 and Dec 15, so the demo needs both, labelled: proof that it runs live, and a free, clickable loop that runs in simulation. The live pages also gave no runtime evidence that a Token Factory or Tavily call happened.

## Decision

- **Two gateways from one image on one VM, behind one Caddy.** The live one is at the root domain (the URL submitted for Stage One) and keeps the edge guard and INV-8 in the app. The simulation is `ratchet-sim` (`RATCHET_MODE=mock`, an in-memory ledger, a 4-day warm-up) at `RATCHET_SIM_DOMAIN` (default `sim.<RATCHET_DOMAIN>`, which sslip.io resolves). It has its own `environment:` and no `env_file`, no volume, no published port, so it never holds a live key, token or signing key; it is capped at 1 vCPU and 1 GiB. `update.sh` fails when it answers `/healthz` in any mode but mock.
- **The guard stays a live-host concern and INV-8 stays in the app.** The simulation host has no guard, because its demo controls are meant to be clicked, and the app refuses them anyway in live mode.
- **The shared simulation limits itself** (`ratchet/demo.py`, mock mode only, every default off): a per-IP token bucket on `/ratchet/demo/*` POSTs (the gateway's `ratelimit.RateLimiter`), and an automatic reset after N simulated days or requests (enforced when a visitor clicks, so nobody watching a rollback loses the state) or after idle time (a daemon thread started on the first demo action). `RATCHET_DEMO_RPM`, `_MAX_DAYS`, `_MAX_REQUESTS`, `_IDLE_RESET_S`.
- **A content-free last-call record per role** (`ratchet/upstream.py`): the provider and the Tavily client take a keyword-only `observe` callback, run in a `finally` that cannot raise. It keeps the role, a host label, the requested and the reported model id (only when it looks like a model id), ok or the exception class and HTTP status, a fixed reason, latency and time; never text, URLs, keys, headers, error messages or the upstream's own error code. It is exposed in `/ratchet/stats` `upstream` next to `links` (the header switch) and `demo`, under the dashboard's access and scrubbing rules, and shown in a header switch "Live on Nebius Token Factory | Simulation", the landing page's strip and the dashboard header.

## Consequences

- Visitors share one simulation; the dashboard on that host says so.
- One more certificate and DNS name (automatic with sslip.io; an A record for a custom domain).
- Memory and CPU caps keep the simulation from starving the live gateway. That both gateways fit in 2 vCPU / 8 GB under judging load is **unverified**; the VM may be smaller still, and the operator measures it on day 1 ([RUNBOOK](../RUNBOOK.md#simulation-host)). `RATCHET_SIM=off` drops the host.
- The record resets on restart and covers the gateway process only (not Night School, not the live-run harness). Breaker refusals are not calls and are not recorded.
- Simulation numbers stay labelled simulation.
- The janitor's thread can be ended: `DemoJanitor.stop()` wakes it at once and it never starts again, and the app's lifespan calls it at shutdown. Its callbacks read the gateway module's globals (`rt`, `_auto_reset`), so a thread that outlives the state it was built for, as the test fixtures that reload the module did after `test_demo_caps_reset_on_click` (RATCHET_DEMO_MAX_REQUESTS=250), resets whichever gateway is current a minute later and every minute after. The fixtures now end the janitor of the module they replace, and an autouse fixture fails a test that leaves one running.
- No schema change, no new endpoint, no `add_example` caller; every default is off, so the reference simulation and the three pinned workloads are unchanged.

## Alternatives

- One gateway with a per-request mode switch: breaks INV-8 and mixes ledgers.
- A `/sim` path on the same host: the web app uses absolute API paths, so it would need a base path and a second build.
- A simulation per visitor: memory times visitors.
- A second VM: about $69 more at list prices.
- Only the recorded video: not clickable.
- Reading the last call from `/metrics`: counters by purpose, with no model id or time.
