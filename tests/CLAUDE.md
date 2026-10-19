# CLAUDE.md: `tests/`

- `test_core.py`: pure logic: tasks, confidence, calibration (incl. ties), licenses, ledger, release gate transitions. Build small ledgers with `Ledger(":memory:")`; use `_version()` / `_audit()` helpers.
- `test_system.py`: end-to-end with mock models through `sim.simulate.run`: learning curve, savings, rollback, drift recovery, provenance, gateway API shape, provider parsing via `httpx.MockTransport`.
- `test_hardening.py`: regressions for the issues listed in [docs/TESTING.md](../docs/TESTING.md): migrations, `auto_day`, student-failure fallback, 502 on teacher failure, fallback threshold, registry growth, alert de-duplication, `ReleasePolicy.from_env`.

Rules:

- No network, no GPU, no API keys. Tests must pass offline in ~10 s.
- Assert on trends and invariants (share rises, escalations fall after drift, rollback happens), not exact simulation numbers.
- Every bug fix gets a test in `test_hardening.py`.
- Tests that reload `ratchet.gateway` must restore state (`gw.rt.reset()`).
