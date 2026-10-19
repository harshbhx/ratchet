# CLAUDE.md: `tests/`

- `test_core.py`: pure logic: tasks, confidence, calibration (incl. ties), licenses, ledger, release gate transitions. Build small ledgers with `Ledger(":memory:")`; use `_version()` / `_audit()` helpers.
- `test_system.py`: end-to-end with mock models through `sim.simulate.run`: learning curve, savings, rollback, drift recovery, provenance, gateway API shape, provider parsing via `httpx.MockTransport`.
- `test_hardening.py`: regressions for the issues listed in [docs/TESTING.md](../docs/TESTING.md): migrations, `auto_day`, student-failure fallback, 502 on teacher failure, fallback threshold, registry growth, alert de-duplication, `ReleasePolicy.from_env`.
- `test_scout.py`: Scout and its pieces: example questions match the research world, the documented event shape, pinned tasks never stored as training data, the student learning Scout calls over simulated days, `--workload research`, all gateway pages and `/scout/*` routes (incl. SSE and the 400/429 paths), the guard (budget, hourly window, daily run cap, with a fake clock), `TavilySearch` against `httpx.MockTransport`, and bad JSON from models. Use the `gw` fixture: it reloads the gateway in mock mode with `RATCHET_MOCK_WARMUP_DAYS=0`.

49 tests in total (19 core, 7 system, 8 hardening, 14 Scout).

Rules:

- No network, no GPU, no API keys. Tests must pass offline in ~10 s.
- Assert on trends and invariants (share rises, escalations fall after drift, rollback happens), not exact simulation numbers.
- Every bug fix gets a test in `test_hardening.py`.
- Tests that reload `ratchet.gateway` must restore state (`gw.rt.reset()`). Set `RATCHET_MOCK_WARMUP_DAYS=0` when you reload it, so a warm-up in your shell does not slow tests or change their numbers.
- Guard tests use an injected clock (`Guard(..., clock=lambda: now[0])`), never `time.sleep`.
