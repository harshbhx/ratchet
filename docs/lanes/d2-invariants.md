# Lane D2: proposed invariants

Lane D2 (config readiness, idempotency, off-path quality checks, fallback attribution) does not number invariants in CLAUDE.md while the lanes build in parallel, because the numbers would collide. Each proposal below names its text, the code that enforces it and the tests that prove it. The integrator gives each one the next free number when the lanes merge, adds it to CLAUDE.md's list and to `CODE` in `scripts/reqtrace.py`, and marks the tests with `@pytest.mark.req("INV-n")`.

## A bad configuration stops the gateway before anything opens, and every setting is known (OPS-CONFIG-READINESS, ADR 0070)

**Text.** `Runtime.__init__` runs `config.check` before `reset()` opens the ledger or builds a provider, and raises one `ConfigError` that lists every error, each naming its variable; a teacher, judge or fallback teacher the license registry refuses is one of them (INV-1), and the error is then also a `LicenseError`. No message of a `ConfigIssue`, a `config_warning` log line or `/readyz` holds a key's value or a URL. A rule a setting must follow goes in `Settings.validate` (or `ReleasePolicy.validate` across policy fields), never only in the code that reads the setting later. Every variable the package reads is in `config.KNOWN_ENV` (a deploy-only variable in `config.DEPLOY_ENV`), so the unknown-variable warning never calls a real setting unknown.

**Why.** A gateway that starts with a bad value fails later, per request, in ways that cost money (every teacher call refused for a missing key) or corrupt the evidence (a golden set that takes every example, a canary that can never collect audits). A gateway that stops one variable at a time costs a restart, and on the demo VM a rebuild, per typo.

**Enforcing code.** `ratchet/config.py`: `load`, `check`, `Settings.validate`, `ConfigError`, `ConfigLicenseError`, `KNOWN_ENV`, `DEPLOY_ENV`, `known_env`, `main`; `ratchet/release.py`: `ReleasePolicy.validate`; `ratchet/gateway.py`: `Runtime._check_config`; `scripts/reqtrace.py`: `rule_env_known`.

**Proving tests.** `tests/test_readiness.py`: `test_bad_setting_stops_startup_naming_the_variable` (29 cases; no ledger file exists after the refusal), `test_every_error_is_listed_at_once`, `test_misspelled_variable_warns_with_a_suggestion_and_strict_mode_stops`, `test_config_check_cli_exit_codes`, `test_known_env_covers_every_variable_the_deploy_kit_sets`; `tests/test_traceability.py`: `test_every_env_var_documented`, `test_env_known_rule_catches_drift`.

## Liveness asks nothing; readiness probes are bounded and off the request path (OPS-CONFIG-READINESS, ADR 0070)

**Text.** `/healthz` and `/livez` are `async def` routes that read only the settings: never the ledger, an endpoint or a worker thread. `/readyz` runs every blocking probe on a `readiness.pool(...)` thread (the ledger's own, or the two endpoint threads), never on the event loop or AnyIO's request threads; a caller waits at most `PROBE_TIMEOUT_S` and a result is reused for `PROBE_TTL_S`, so a dependency sees at most one probe per TTL however often `/readyz` is asked; a probe never spends model tokens; a shared run is never cancelled. `/readyz` is 503 only when the ledger cannot be written or no teacher can answer; a dependency the gateway works around makes it `degraded`, still 200.

**Why.** A liveness check that waits for threads or dependencies restarts working processes under load or turns an outage into a restart loop; a readiness check that runs on the request path, spends tokens or is not rate-bounded becomes a load on the very dependencies it watches.

**Enforcing code.** `ratchet/gateway.py`: `healthz`, `livez`, `readyz`; `ratchet/readiness.py`: `pool`, `POOLS`, `ProbeCache`, `Readiness`, `list_models`; `ratchet/ledger.py`: `Ledger.probe`.

**Proving tests.** `tests/test_readiness.py`: `test_healthz_answers_with_every_worker_thread_busy`, `test_hung_endpoints_never_make_the_ledger_look_unwritable`, `test_probe_cache_shares_runs_times_out_and_names_classes`, `test_readyz_503_when_the_ledger_cannot_be_written`, `test_readyz_reports_missing_adapters_and_queues_their_reload` (one `/models` call per TTL), `test_readyz_follows_the_teacher`.
