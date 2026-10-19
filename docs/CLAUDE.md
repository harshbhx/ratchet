# CLAUDE.md: `docs/`

Index: [docs/README.md](README.md). Product docs (PRD, business, competitive analysis, roadmap, Devpost submission, research summary, glossary) and technical docs (architecture, API, configuration, deployment, runbook, threat model, testing, ADRs).

Rules:

- Docs must match the code. When you change behaviour, update the doc that describes it in the same commit (API.md for endpoints, CONFIGURATION.md for env vars, ARCHITECTURE.md for flow/states, RUNBOOK.md for operations).
- Plain English, short sentences, no hype. Numbers need units and a source link or a "simulation"/"estimate" label.
- Facts about third parties (prices, model IDs, competitor status, terms) carry a source link and an as-of date. Re-check before the Devpost submission.
- `HACKATHON_SUBMISSION.md` has `[LIVE NUMBER]` placeholders. Fill them only with results from a real live run.
- New architectural decisions get an ADR (`adr/NNNN-title.md`: Status, Context, Decision, Consequences, Alternatives) and a line in `adr/README.md`.
