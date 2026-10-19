# CLAUDE.md: `sim/` (offline simulation)

Mock models that let the whole loop run in seconds with no keys or GPUs. Used by `make sim`, the mock gateway (dashboard and Scout), `tests/test_system.py` and `tests/test_scout.py`.

- `tickets.py`: 20 synthetic support-ticket templates (6 categories, 4 priorities, "easy"/"hard"), plus 3 drift templates that appear from `drift_day` (a product launch). `REGISTRY` maps ticket text → ground truth so mock models can look it up.
- `mock_models.py`:
  - `MockTeacher`: always right on labels, 2.4 s latency, bills 300 reasoning tokens. Also answers Scout's pinned planner and writer prompts (`_pinned_answer`).
  - `MockStudent`: skill per template = `max(0.35, ceil × (1 − e^(−n/6)))` with ceil 0.985 easy / 0.86 hard; exact texts it trained on are known. When it does not know a ticket, 15% of the time its samples agree on the **same wrong answer** with fairly high probability (the case audits and shadow exist for).
  - `MockFleet`: trainer + evaluator + resolver. `corrupt_on_deploy` breaks the `fragile` slices (billing/refund/security tickets; `paper`/`docs` triage; `date` reads) for a version once it reaches canary/live (fault injection for rollback demos).
- `research.py`: the mock research world for Scout. 8 questions (facts from the original papers), each phrased 4 ways, with 72 simulated search results (12 templates × 6 variants) on `example.org`. Each template has fixed labels for `source-triage` and `page-read`. `REGISTRY` maps request text → `ResearchItem`; `search()` returns a deterministic sample per query; `ResearchStream` feeds the simulator (60% triage, 40% read; questions 7–8 only from `drift_day`). `plan_answer` / `write_answer` back the mock teacher's pinned answers.
- `world.py`: one `lookup()` over tickets and research items, the `Item` protocol, and the workloads `tickets`, `research`, `mixed` (`make_stream`, `night_task`).
- `simulate.py`: CLI (`--days --requests --drift-day --bad-deploy-version --workload --seed`) and `run(args)` for tests.

### Generic item interface

The mock models only see this interface (`sim/world.py` `Item`), so tickets and research calls share one skill model:

`text`, `template`, `difficulty` (`easy`/`hard`), `task`, `labels` (right answer), `label_space` (possible values per label), `fragile` (broken by a corrupted deploy), `answer(labels)` (JSON text), `messages()`.

To add a workload, implement these on its item class, register its texts so `world.lookup` finds them, and add it to `make_stream`.

Rules:

- These parameters are assumptions, not measurements. Never present simulation output as real model quality.
- If you change mock parameters, re-run `make sim` (and `--workload research`, `--workload mixed`) with seeds 1, 2, 3, 7, 11 and update [docs/TESTING.md](../docs/TESTING.md) if the trend changes.
- Keep it deterministic: all randomness goes through seeded `random.Random` or `_rng(...)`.
- **Do not change the order of random draws for tickets.** `TicketStream.sample`, the key order of `Ticket.label_space` (`category`, then `priority`) and the draw order in `MockStudent.complete` decide every ticket number. With them unchanged, `make sim` still reproduces the v0.1 table in README and TESTING exactly. Give new workloads their own RNG (as `ResearchStream` and `MixedStream` do) instead of drawing from the ticket stream's.
- Scout messages in `research.py` must be built with `ratchet/scout/prompts.py`, never by hand, or mock lookups break.
