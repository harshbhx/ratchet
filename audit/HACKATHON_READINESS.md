# Hackathon readiness

Checked Oct 22, 2026. Ratchet targets the Nebius x NVIDIA Global AI Hackathon (Devpost), track "Best Apps and Agents",
plus "Best Use of Tavily".

## What the rules say (and how sure we are)

The Devpost rules page answers automated fetches with HTTP 403, so the rules below come from search-result snippets of
that page and secondary listings, checked Oct 22, 2026. Treat every line as **to be confirmed by a human on the rules
page** before submitting.

| Requirement | Source | Confidence |
|---|---|---|
| Submission period ends Oct 30, 2026, 10:00 am PT (10:30 pm IST) | [startupgrantsindia write-up of the rules](https://www.startupgrantsindia.com/nebius-nvidia-global-ai-hackathon), repo's `docs/HACKATHON_SUBMISSION.md` | Medium |
| The project must run on Nebius Token Factory or Nebius AI Cloud: a runtime call to the Token Factory inference API, or deployed/run on Nebius AI Cloud compute | [rules page snippet](https://nebiusglobalaihackathon.devpost.com/rules), [Devpost on X](https://x.com/devpost/status/2092665844601958550) | Medium-high |
| At least one NVIDIA open model | same | Medium-high |
| A working demo or test build URL (Physical AI exempt), a public repo with an open-source license | [startupgrantsindia](https://www.startupgrantsindia.com/nebius-nvidia-global-ai-hackathon) | Medium |
| Feedback on Token Factory / AI Cloud / NVIDIA tools in the submission | [rules page snippet](https://nebiusglobalaihackathon.devpost.com/rules) | Medium |
| Four equally weighted criteria: Technological Implementation, Design ("a complete, coherent product experience, not just a technical proof of concept"), Potential Impact, Quality of the Idea | rules page snippet, [tierones listing](https://tierones.io/opportunities/nebius-nvidia-ai-2026) | Medium |
| Judging Dec 1-15, 2026 | rules page snippet | Medium-high |
| Demo video under 3 minutes | repo's submission doc (participant summary); not seen in a primary source here | Low |

## Stage One (pass/fail) status

| Item | Status | Evidence / what is missing |
|---|---|---|
| Runtime call to Token Factory | **Not verified (Blocked here)** | No live run has ever happened: there is no `evidence/` directory, `docs/LIVE_RESULTS.md` does not exist, and `docs/HACKATHON_SUBMISSION.md` still has 5 `[LIVE NUMBER]` placeholders. This environment has no `NEBIUS_API_KEY`. Mock mode makes no Token Factory call, so **a demo left in mock mode fails Stage One.** |
| NVIDIA open model | Ready in code | Nemotron 3 Ultra (teacher), Nano 4B (student base) in `ratchet/config.py`; IDs unverified until `scripts/day1_check.py` passes (INV-9). |
| Working demo URL, free for judges until Dec 15 | Not done | `[DEMO URL]` placeholder; the VM kit (`deploy/vm/`) has only been dry-run. Token Factory credit must last through Dec 15. |
| Public repo with open-source license | **Blocked on the owner (RAT-021)** | `LICENSE` is Apache-2.0, but `origin/main` is an unrelated v0.3.0 history. Judges and the story page's source links see `main`. This branch must become `main` (or the default branch) before submitting. |
| Video < 3 min, public | Not done | `docs/DEMO_SCRIPT.md` exists; a video needs a live run to show (and the rules let judges skip the demo URL). |
| Feedback section | Drafted | In `docs/HACKATHON_SUBMISSION.md`. |

**Bottom line: the code is in good shape; the submission is not.** The three blockers are a real live run, publishing this
branch as `main`, and a hosted demo in live mode. None can be done from this session without the owner's credentials,
spending and authorization.

## Criteria traceability

| Criterion | What Ratchet has | Code | Verified by | Demo moment | Gap |
|---|---|---|---|---|---|
| Technological Implementation (Token Factory, Nemotron) | Teacher-student cascade, calibrated confidence, Night School on Serverless Jobs, staged release with automatic rollback, signed BOMs | `ratchet/router.py`, `release.py`, `nightschool/`, `bom.py`, `live.py` | 1,815 offline tests; 3 reference workloads; simulation | Dashboard: a corrupted deploy rolled back | No live run, no GPU training run, no endpoint run (all Not verified) |
| Design (complete product experience) | Story page with the request-lifecycle explorer, Scout demo, operator dashboard | `web/src/` | `npm test` (37), `tsc`, Playwright at 360/1440 px, both themes, reduced motion (design/FRONTEND_VERIFICATION.md) | Hero → explorer "Bad candidate" → dashboard | Only Chromium; no screen-reader run |
| Potential Impact | Measured savings and break-even, honest when self-hosting does not pay | `cost.py`, `receipts.py`, `kpis.py` | `tests/test_cost.py`, `test_receipts.py`; simulation numbers labelled | "The honest bill" chapter | Every savings number is still simulation |
| Quality of the Idea | License-clean distillation with provenance, statistical release safety | `licenses.py`, `certificate.py`, `stats.py`, `proof.py` | `test_licensing.py`, `test_stats.py` (independent oracles), the audits' checks | BOM verify on screen | Novelty claims must name prior art (README already does) |
| Best Use of Tavily | Scout's searcher | `ratchet/scout/search.py`, `credits.py` | `tests/test_scout.py` (MockTransport) | Live Scout run | Needs a real Tavily call in live mode |

## Claims safe to use in the Devpost text today

- Ratchet is an OpenAI-compatible gateway (chat, completions, stateless responses, embeddings pass-through, Azure-style
  route), checked against the real `openai` SDK in tests.
- Only open teachers whose license allows distillation are accepted; closed model families are refused at startup.
- Every stored training example is redacted first and recorded with its teacher and license; adapters ship with a signed
  bill of materials and a NOTICE.md carrying the base model's license.
- A candidate adapter is gated on a held-out golden set, released shadow → 10% → 50% → 100%, and rolled back
  automatically on audited precision; release moves are compare-and-set on both release rules (fixed in this audit).
- In **simulation** (mock models, seed 7): the corrupted v3 is rolled back at 10% canary on day 4; savings figures must be
  labelled simulation.

Not safe until a live run: any measured cost saving, latency, student share, or "runs on Token Factory" in the past tense.

## Recommended order of work before Oct 29

1. Publish this branch as `main` (RAT-021).
2. `make day1` with a real key (cents), then `make live-smoke` (cents), then decide the budget for `make live-run`.
3. Deploy the VM in live mode with `RATCHET_API_GUARD=guarded` and judge access; check `caddy validate`.
4. Fill `[LIVE NUMBER]`s only from `python -m ratchet.liverun report ... --publish`.
5. Record the video from the live demo; keep a labelled simulation fallback.
