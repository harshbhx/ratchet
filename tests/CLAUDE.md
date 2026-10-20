# CLAUDE.md: `tests/`

- `test_core.py`: pure logic: tasks, confidence, calibration (incl. ties), licenses, ledger, release gate transitions. Build small ledgers with `Ledger(":memory:")`; use `_version()` / `_audit()` helpers.
- `test_system.py`: end-to-end with mock models through `sim.simulate.run`: learning curve, savings, rollback, drift recovery, provenance, gateway API shape, provider parsing via `httpx.MockTransport`.
- `test_hardening.py`: regressions for the issues listed in [docs/TESTING.md](../docs/TESTING.md): migrations, `auto_day`, student-failure fallback, 502 on teacher failure, fallback threshold, registry growth, alert de-duplication, `ReleasePolicy.from_env`. Also the v0.4 hardening: PII redaction (incl. a check that it never changes a mock request), API keys and quotas, streaming, signed BOMs, `/healthz`, `/ratchet/redact`. Use the `gw_env` fixture there: `gw_env(RATCHET_API_KEYS=...)` reloads the gateway with those variables and restores the default gateway afterwards.
- `test_traceability.py`: the requirements traceability check (`scripts/reqtrace.py`, loaded with `importlib.util.spec_from_file_location`). Repo tests run each rule on the real repo; mini-world tests write a tiny PRD, CLAUDE.md and test file to `tmp_path` and assert that each kind of mistake gives exactly one error. One test runs `pytest --collect-only` in a subprocess to prove the AST test count matches pytest.
- `test_scout.py`: Scout and its pieces: example questions match the research world, the documented event shape, pinned tasks never stored as training data, the student learning Scout calls over simulated days, `--workload research`, all gateway pages and `/scout/*` routes (incl. SSE and the 400/429 paths), the guard (budget, hourly window, daily run cap, with a fake clock), `TavilySearch` against `httpx.MockTransport`, and bad JSON from models. Use the `gw` fixture: it reloads the gateway in mock mode with `RATCHET_MOCK_WARMUP_DAYS=0`.

108 tests in total (19 core, 7 system, 23 hardening, 15 Scout, 44 traceability).

Rules:

- No network, no GPU, no API keys. The suite must pass offline in about 20 s.
- Assert on trends and invariants (share rises, escalations fall after drift, rollback happens), not exact simulation numbers.
- Every bug fix gets a test in `test_hardening.py`.
- Mark each test with the requirements it proves: `@pytest.mark.req("FR-2.1", "INV-7")` (string literals only, so the AST scan can read them), or a module- or class-level `pytestmark`. Ids come from [docs/PRD.md](../docs/PRD.md) (FR, NFR, US, SM) and the invariants in [CLAUDE.md](../CLAUDE.md) (INV-n). Never cite an unknown or replaced id; a test for a `Planned` requirement or a `Not met` metric must be `@pytest.mark.xfail(strict=True, reason=...)`. An xfail or `@pytest.mark.skip` test never counts as proof. After adding or renaming a marked test, run `make trace` and commit `docs/REQUIREMENTS.md`; if the id was in `KNOWN_GAPS` in `scripts/reqtrace.py`, remove it there.
- Keep `parametrize` argument lists literal (or a module-level list literal) so the suite size in the docs can be counted statically. Update the "N tests" lines in the docs when the count changes; `make lint` names every stale one.
- Tests that reload `ratchet.gateway` must restore state (`gw.rt.reset()`). Set `RATCHET_MOCK_WARMUP_DAYS=0` when you reload it, so a warm-up in your shell does not slow tests or change their numbers.
- Guard tests use an injected clock (`Guard(..., clock=lambda: now[0])`), never `time.sleep`.
