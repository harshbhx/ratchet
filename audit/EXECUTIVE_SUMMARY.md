# Executive summary

**Scope.** A full audit of Ratchet at `3bfa5ab` (branch `claude/epic-mendel-c2kbrx`, Oct 22, 2026): about 42,000 lines of
Python in `ratchet/`, 14,500 lines of TypeScript in `web/`, the deploy kit and CI. Four independent read-only audits
(security, ML and statistics, privacy and provenance, API and deployment) ran in parallel; a fifth workstream redesigned
the landing page in an isolated worktree. The lead reproduced every claimed defect before fixing it.

**Bottom line.** The engineering is unusually thorough for a hackathon entry: 1,781 offline tests at baseline, exact
statistics checked against independent oracles, provenance that verifies. The audit still found real defects on the
paths that matter most (release safety, privacy promises, money), and fixed 20 of them with regression tests. **The
submission is not ready**: nothing has ever run against Token Factory, so Stage One (a runtime Token Factory call) is
unproven, and the public `main` branch is an unrelated v0.3.0 history.

## What was found and fixed (details in FINDINGS.md)

- **Release safety.** The anytime-valid canary rule (`RATCHET_SEQUENTIAL=1`, which the live run requires) moved versions
  without compare-and-set: an admin's rollback could be overwritten and a rolled-back canary put back at 50% of traffic,
  and an operator's release freeze did not stop it (RAT-001, High). The signed certificate described the global
  threshold's serving set while the router served per-task thresholds (RAT-004), and per-task thresholds ignored
  Learn-then-Test (RAT-005).
- **Privacy promises.** `/v1/completions` and `/v1/responses` ignored `X-Ratchet-Store: none` and `store: false`, so
  opted-out answers were stored as training data, and SDK retries could push a golden example into training (RAT-002,
  RAT-003). The redactor missed cards and phone numbers grouped with no-break spaces or Unicode dashes (RAT-009);
  corrections and imports ignored the configured redaction style and plugin (RAT-010).
- **Money.** A teacher answer cut off mid-body was retried and billed up to 3 times (9 behind an SDK) (RAT-006); a lone
  surrogate in an answer caused a 500 after billing (RAT-007); a completions batch ran 16 cascades on one rate token
  (RAT-011).
- **Operations.** The hourly retention job released the release gate's lock without holding it (RAT-008, a merge
  leftover visible as a thread exception in the baseline run); the demo VM's guard left admin-level routes open on a
  guarded mock demo (RAT-015); the training job reported an adapter ready before its folder was complete (RAT-026).
- **CI was red at HEAD**: two web contract tests failed because the web code had drifted from the Python (RAT-019).

## Test evidence (TEST_RESULTS.md)

Baseline: 1,760 passed, 4 failed. After: see TEST_RESULTS.md for the final run. Lint, format, the traceability check
(215 requirements, 0 known gaps), the config check, the three reference workloads and the pinned reference simulation
all pass; no simulation or reference number moved. The perf guard on 200,000 rows fails in this container at the
baseline commit too (machine speed, not a regression).

## Engineering scorecard (an assessment, not a prediction of the judges)

| Category | Weight | Before | After | Why |
|---|---:|---:|---:|---|
| Technical implementation | 20 | 15 | 16.5 | Deep, well-tested design; real state and concurrency defects on the release path, fixed; CI was red |
| Security and privacy | 15 | 11 | 12.5 | Strong access model; an opt-out bypass and redaction gaps fixed; scrubbed-dashboard leaks and unhashed adapter weights remain |
| ML quality and release safety | 15 | 10 | 12 | Release gate and certificate now describe what is served; no real model quality has been measured; RAT-022 open |
| Live operation and reliability | 15 | 3 | 3.5 | Never run live: no Token Factory call, no GPU job, no endpoint, no hosted demo. Provider hardening only |
| Measured economic value | 10 | 2 | 2 | Every number is simulation, honestly labelled; the cost model says when self-hosting does not pay |
| UI/UX and judge experience | 10 | 7 | 8 | New mark, sharper hero, an accessible lifecycle explorer; web drift fixed |
| Differentiation and impact | 10 | 7 | 7 | License-clean provenance plus statistical release safety is a coherent, defensible angle |
| Reproducibility and submission readiness | 5 | 2 | 2.5 | Offline reproducibility is excellent; `main` is the wrong history and no demo URL exists |
| **Total** | 100 | **57** | **64** | |

The next band (75+) needs a real live run with published, verifiable numbers and a hosted live-mode demo; nothing else
moves "Live operation" and "Measured economic value", which together are a quarter of the score.

## What the owner must do (authorization or credentials needed)

1. Make this branch the public `main` (RAT-021).
2. Run `make day1`, `make live-smoke` (cents each), then budget `make live-run` (`MAX_USD`), on a machine with
   `NEBIUS_API_KEY`.
3. Deploy the VM in live mode with the guard and judge access; `caddy validate` the new Caddyfile.
4. Decide RAT-022 (cross-task golden rule), RAT-023 (scrub key names) and RAT-024 (erasure policy).
