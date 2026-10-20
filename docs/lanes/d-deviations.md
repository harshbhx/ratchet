# Lane D: deviations from the specs

Where lane D built something other than its spec asked, and why, so the integrator knows what the merged code does.

## OPS-OBSERVABILITY: the retirement event is `live_retired`, not `retired`

**Spec.** Item (5) asks for a `retired` event when `ReleaseGate.tick` retires the old live version on a promotion.

**Built.** `ReleaseGate._tick_canary` writes kind `live_retired` (`v4 retired: v5 replaces it as live`) just before the `live` event. `scripts/reference_check.py` lists `live_retired` in `IGNORED_KINDS`.

**Why.** v0.4 already writes `retired` for a candidate replaced at admission, and those events are part of the pinned event logs that `make reference` compares by kind. Writing the new event as `retired` would change the three pinned workloads; adding `retired` to the ignored kinds would hide v0.4's own events from the comparison. A kind of its own keeps the pinned logs byte-identical and still gives every version transition an event (`tests/test_observability.py::test_every_version_transition_writes_an_event`).

**Where it shows.** `ratchet/release.py` (`_tick_canary`), `scripts/reference_check.py` (`IGNORED_KINDS`), docs/RUNBOOK.md (event kinds), docs/ARCHITECTURE.md, ADR 0062, PRD FR-12.4. A dashboard or alert that looks for the retirement must match `live_retired`.
