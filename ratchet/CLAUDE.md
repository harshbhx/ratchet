# CLAUDE.md: `ratchet/` (core package)

## Request path (`router.py`)

`Ratchet.handle(messages, task)`:

1. `_pick_version()`: canary gets `traffic_pct`% of requests, otherwise live; `None` on cold start → teacher.
2. Student: `n = Settings.samples` samples at temperature 0.7 with logprobs → `confidence.score()`.
3. Serve the student if `confidence >= threshold(version, model)`. The threshold comes from the version's eval (calibrated); the untuned fallback model uses `Settings.confidence_threshold`.
4. With probability `audit_rate`, the teacher also answers a served request (quality signal + free training data).
5. Otherwise, or if the student call raised, escalate: teacher answers, and `_call_teacher` stores the example with its license.
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
- `examples.hash` is unique per (task, messages); duplicates increment `seen`.
- `split` is deterministic from the hash (`golden_fraction`), so an example never moves between sets, except via the `FREQUENT` rule applied in `nightschool/dataset.py`.
- Schema changes need both `SCHEMA` and `MIGRATIONS`.

## Licenses (`licenses.py`)

To add a teacher, add it to `REGISTRY` with its real license name and URL. `CLOSED` prefixes are refused. `Ratchet.__init__` calls `require_distillable` on the teacher, so a bad config fails at startup.

## Gateway (`gateway.py`)

`Runtime` wires everything; `Runtime.reset()` rebuilds it (mock reset endpoint). Mock mode imports from `sim/`. Live mode uses `Ledger(auto_day=True)` and `ReleasePolicy.from_env()`. The dashboard is a single static HTML file (`dashboard.html`, no build step, no external scripts).
