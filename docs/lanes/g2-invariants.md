# Lane G2: proposed invariants

Invariants lane G2 proposes for the numbered list in [CLAUDE.md](../../CLAUDE.md#invariants-do-not-break-these). The integrator numbers them after the lanes merge; until then they are not numbered and no test cites them by id.

## Live and simulation side by side (item DEMO-LIVE-AND-SIM, FR-11.30, FR-11.31, NFR-67, ADR 0133)

### Upstream call records are content-free

**Text.** The last-call record per role (`/ratchet/stats` `upstream`) holds a role, a host label, model ids that match the id pattern in full, a status, an exception class name, a fixed reason, a latency and times. It never holds prompt or answer text, a URL, a key, a header, an error message or the upstream's own error code, and recording never changes or fails a model call (it runs in a `finally` that suppresses every exception).

**Enforcing code.** `ratchet/upstream.py`: `UpstreamRecorder`, `classify`, `model_id`; `ratchet/providers.py`: `OpenAICompatProvider.complete`; `ratchet/scout/search.py`: `TavilySearch.search_with_usage`.

**Proving tests.** `tests/test_livesim.py::test_upstream_recorder_is_content_free`, `test_upstream_failures_record_class_status_and_last_success`, `test_classify_never_returns_a_message`, `test_tavily_calls_are_recorded_without_content`, `test_providers_take_observe_as_keyword_only`.

### The simulation host holds no live secret and is mock by its own environment

**Text.** The `ratchet-sim` container is defined with its own `environment:` and no `env_file`, no volume and no published port; it never interpolates a live key, token or signing key; and `update.sh` fails when the simulation host answers `/healthz` in any mode but mock. A live gateway still answers every demo route 403 before any simulation limit runs (INV-8).

**Enforcing code.** `deploy/vm/docker-compose.yml`, `deploy/vm/Caddyfile`, `deploy/vm/update.sh`; `ratchet/gateway.py`: `_demo_limit`, `_mock_only`.

**Proving tests.** `tests/test_live.py::test_compose_and_caddy_run_a_keyless_simulation_beside_the_live_gateway`, `test_update_script_checks_both_hosts`; `tests/test_contract.py::test_demo_limits_never_answer_before_the_mode_check`.
