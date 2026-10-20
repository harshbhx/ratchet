# CLAUDE.md: `ratchet/` (core package)

## Request path (`router.py`)

`Ratchet.handle(messages, task, client=None)` (`client` is the API key name; the gateway passes it, internal callers leave it `None`):

0. Pinned task (`tasks.get(name).pinned`) → `_pinned()`: teacher answers, request logged, **no** `add_example`, no student/audit/shadow. Return.
1. `_pick_version()`: canary gets `traffic_pct`% of requests, otherwise live; `None` on cold start → teacher.
2. Student: `n = Settings.samples` samples at temperature 0.7 with logprobs → `confidence.score()`.
3. Serve the student if `confidence >= threshold(version, model)`. The threshold comes from the version's eval (calibrated); the untuned fallback model uses `Settings.confidence_threshold`.
4. With probability `audit_rate`, the teacher also answers a served request (quality signal + free training data).
5. Otherwise, or if the student call raised, escalate: teacher answers, and `_call_teacher` stores the example with its license, after `redact_messages` / `redact` replace identifiers (when `Settings.redact_pii`) and with the placeholder count.
6. Shadow: if a version is in `shadow`, it runs the same cascade silently. A would-serve answer that disagrees with a live-student answer is arbitrated by the teacher.
7. `ledger.log_request(...)` with `cost_usd` and `baseline_cost_usd` (= the all-teacher cost of this request).

Keep `handle` free of I/O beyond the providers and the ledger. Anything slow belongs in Night School.

## Release (`release.py`)

States: `candidate → rejected | shadow | canary(10) → canary(50) → live`; `canary → rolled_back`; old versions → `retired`.

- `admit(vid)` runs after training: precision ≥ `min_precision`, coverage ≥ `min_coverage`, no regression vs live beyond `tolerance`. Cold start (no live version) goes straight to canary 10%.
- `tick()` runs periodically: shadow decisions, canary advance (needs `min_audits` audits **in the current stage**, tracked via `stage_since`), rollback when the Wilson upper bound < `live_floor`.
- Change thresholds through `ReleasePolicy` (or `RATCHET_<FIELD>` env vars), never inline.

## Ledger (`ledger.py`)

- Every write commits immediately (v0 simplicity; fine for a single gateway process).
- Every public method is wrapped in `@_locked` (one `threading.RLock`), because Scout's agents call `Ratchet.handle` from several threads and share one SQLite connection (`check_same_thread=False`). Add `@_locked` to any new public method. Re-entrant, so locked methods may call each other.
- `stats_by_task()` feeds `tasks` in `/ratchet/stats` and the dashboard's **By task** table.
- `examples.hash` is unique per (task, messages); duplicates increment `seen`.
- Only the functions in `ALLOWED_ADD_EXAMPLE_CALLERS` (`tests/test_nightschool.py`; today `Ratchet._call_teacher`) may call `add_example`. An AST test fails on any other use under `ratchet/` until the allowlist is changed in a reviewed edit (INV-2, INV-10).
- `split` is deterministic from the hash (`golden_fraction`), so an example never moves between sets, except via the `FREQUENT` rule applied in `nightschool/dataset.py`.
- Schema changes need both `SCHEMA` and `MIGRATIONS`. Indexes on migrated columns go in `INDEXES` (created after migrations), not in `SCHEMA`.

## Privacy, keys and BOMs

- `redact.py`: `PATTERNS` is an ordered list of `(label, regex, check)`; earlier patterns win, so put specific formats (with checksums) before broad ones like `PHONE`. Every new pattern needs a positive test **and** must keep `test_redact_leaves_ordinary_text_alone` and `test_redaction_is_a_no_op_on_the_mock_world` passing (mock lookups match exact text).
- `auth.py`: `KeyRing(spec)` raises `ValueError` on a bad spec, so a typo stops the gateway at startup. `identify()` compares SHA-256 digests with `hmac.compare_digest` over every key.
- `bom.py`: `manifest()` from the exact train and golden rows, `document()` wraps it with `sha256` and an optional Ed25519 signature over `canonical()`. `Signer` imports `cryptography` lazily (the `sign` extra). Change the manifest shape only with a new `SCHEMA` version.
- Gateway: `_client()` (401) then `_quota()` (429) run before the cascade on `/v1/chat/completions`; `/healthz` is public. Streaming runs the cascade first, then yields chunks (`_chunks`), so errors are ordinary HTTP errors.

## Licenses (`licenses.py`)

To add a teacher, add it to `REGISTRY` with its real license name and URL. `CLOSED` prefixes are refused. `Ratchet.__init__` calls `require_distillable` on the teacher, so a bad config fails at startup.

## Tasks (`tasks.py`)

`REGISTRY` holds the known tasks. `Task.pinned=True` means always the teacher, never training data (`research-plan`, `research-write`). Distillable tasks with `compare_keys` compare only label fields. `stats_by_task()` reports `pinned` per task, which the dashboard's **By task** table shows.

## Gateway (`gateway.py`)

`Runtime` wires everything; `Runtime.reset()` rebuilds it (mock reset endpoint) and runs the mock warm-up (`RATCHET_MOCK_WARMUP_DAYS`) if set. Mock mode imports from `sim/` and keeps three seeded streams (`tickets`, `research`, `mixed`). Live mode uses `Ledger(auto_day=True)` and `ReleasePolicy.from_env()`. `rt.tick()` runs `gate.tick()` under `gate_lock`.

Pages: the React build in `web/dist` (source in `/web`, see [web/CLAUDE.md](../web/CLAUDE.md)) at `/`, `/scout` and `/dashboard`. The hand-written static pages (`web/landing.html`, `web/scout.html`, `dashboard.html`) are served at `/lite*` and as a fallback when the build is missing. Both are package data (`pyproject.toml`).

Demo controls are shared by every visitor: `day` and `reset` take `_demo_lock` without blocking (busy → 409), and `reset` has a 30 s cooldown (429).

## Scout (`scout/`)

Multi-agent research demo ([docs/SCOUT.md](../docs/SCOUT.md)).

| File | What |
|---|---|
| `agents.py` | `Scout.run(question, emit)`: planner → searcher → triage → reader → writer; emits `start`/`step`/`done` events. `Call` (what an agent learns back), `http_caller` (any gateway over HTTP) |
| `prompts.py` | System prompts, message builders (`plan_messages`, `triage_messages`, `read_messages`, `write_messages`), `EXAMPLE_QUESTIONS` |
| `search.py` | `TavilySearch` (live), `MockSearch` (simulated results from `sim/research.py`) |
| `guard.py` | `Guard`: daily budget, daily run cap, per-IP hourly limit; in-memory, resets at 00:00 UTC |
| `__main__.py` | CLI: `python -m ratchet.scout "<question>" --base-url ... [--api-key] [--max-results]` |

Rules:

- **`prompts.py` is shared with the simulator.** `sim/research.py` builds its mock requests with the same message builders, and the mock teacher recognises the planner and writer by their system prompts. The mock models look a request up by its exact user text, so if you change a prompt or a builder, mock lookups still work only because both sides call the same function. Never build Scout messages anywhere else.
- **Threads.** Triage and reader calls run in a `ThreadPoolExecutor` (6 workers). `Scout.run` serialises `emit` with its own lock; the ledger has its lock. `Ratchet.rng` is shared across threads, so the order of random draws (canary pick, audits) is not reproducible inside a Scout run. Tests on Scout must not depend on exact routing.
- **Budget.** The gateway runs `_scout_precheck` (which calls `Guard.admit`) before every run and `Guard.add_spend` in a `finally` after it, summing each finished step's `cost_usd`. Keep both on every run path in live mode.
- `/scout/stream` always returns HTTP 200 and reports failures as an `error` event; `/scout/run` uses status codes (400/429/502/503).
