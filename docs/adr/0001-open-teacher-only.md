# ADR 0001: Distill only from teachers whose license allows it

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

Ratchet's student learns from the teacher's answers. The commercial terms of the most popular closed models forbid exactly that:

- OpenAI Business Terms §3.3(e) forbid using Output "to develop artificial intelligence models that compete with OpenAI" (research notes, 2026-10-19; terms page not re-read for this edit).
- Anthropic Commercial Terms §D.4(a) forbid using the Services "to build a competing product or service, including to train competing AI models" (research notes, 2026-10-19; terms page not re-read for this edit).

A distillation tool that learns from GPT or Claude traffic therefore puts its customers in breach. [COMPETITIVE_ANALYSIS.md](../COMPETITIVE_ANALYSIS.md) lists Orchestra/Understudy and Experiential Labs (YC Summer 2026) with their sources and dates; they describe routing traffic through frontier models (source and date there).

The licenses of the teacher we want to use are permissive:

- NVIDIA Nemotron 3 Ultra uses OpenMDW-1.1, which "does not impose any restrictions or obligations with respect to any use, modification, or sharing of any outputs".
- The NVIDIA Nemotron Open Model License (Nano, Super) allows commercial derivatives and claims no ownership of outputs.

## Decision

- The teacher must be listed in `ratchet/licenses.REGISTRY` with `distill_ok=True`.
- `Ratchet.__init__` calls `licenses.require_distillable(settings.teacher_model)`. Unknown models and models matching the `CLOSED` prefixes (`gpt`, `o1`, `o3`, `o4`, `claude`, `gemini`) raise `LicenseError`, so the gateway refuses to start.
- Every training example stores `teacher_model`, `license` and `license_url`. `dataset.rows()` drops rows whose teacher is not distillable.
- `/ratchet/bom` exports a model bill of materials with a dataset fingerprint. `train.py` writes `ratchet_manifest.json` next to each adapter.

## Consequences

- Positive: "license-clean distillation" becomes a product feature and the main difference from competitors. Customers can show provenance to their legal teams.
- Positive: it fits the hackathon's open-infrastructure theme and NVIDIA's open models.
- Negative: customers whose current traffic is on GPT or Claude cannot use those answers as training data. They must route through an open teacher first.
- Negative: license registry entries need upkeep as model cards change. Matching on ID prefixes is a heuristic, not legal review.

## Alternatives considered

- **Any teacher, customer's responsibility:** simplest, but it hides a legal risk inside the product. Rejected.
- **A warning instead of a hard block:** easy to ignore. Rejected; an override would have to be an explicit code change.
