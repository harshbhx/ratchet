## What changed and why

<!-- One or two sentences. Link the issue it closes, if any. -->

## How it was tested

- [ ] `make lint test` passes
- [ ] `make sim` still shows the expected trend (if the loop changed)
- [ ] `make reference` passes (if routing, release or the simulator changed)

## Checklist

- [ ] Docs updated (API.md / CONFIGURATION.md / ARCHITECTURE.md / RUNBOOK.md as relevant)
- [ ] Schema change added to both `SCHEMA` and `MIGRATIONS`
- [ ] No closed-model teacher added; invariants in AGENTS.md still hold
- [ ] Simulation numbers labelled as simulation, and measured results traceable to a command in [docs/VERIFY.md](../docs/VERIFY.md)
- [ ] If `web/` changed: `make web` was run and `ratchet/web/dist` is committed
- [ ] No keys, tokens or personal data in the diff

## Screenshots

<!-- For a UI change, before and after (mock mode is fine). -->
