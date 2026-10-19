# Threat model: security and privacy

Scope: Ratchet v0.2.0 in live mode, made up of the gateway, the SQLite ledger, the Night School driver, the Nebius bucket, the vLLM endpoint, the Token Factory calls, and the Scout demo with Tavily search, as hosted on the demo VM ([DEPLOY_NEBIUS_VM.md](DEPLOY_NEBIUS_VM.md)). Mock mode handles only synthetic data. Each threat below says plainly what exists today and what is planned.

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| Customer request text | `examples.messages`, `night/train.jsonl` in the bucket, adapters (memorised) | May contain personal or confidential data |
| Teacher outputs (training data) | `examples.completion`, bucket | Business value, and license provenance |
| Student adapters | Bucket `/data/adapters/*` | The product the customer owns |
| Ledger | `RATCHET_DB` | Provenance, version state, audit trail |
| API keys | `NEBIUS_API_KEY`, `RATCHET_STUDENT_KEY`, `TAVILY_API_KEY`, `RATCHET_ADMIN_TOKEN`, Nebius CLI and S3 credentials | Spend and data access |
| Demo budget | Scout's daily spend and Tavily credits | Real money and free-plan quota on a public URL |
| Release decisions | `versions.state` | Control which model answers users |

## Trust boundaries

```
[Client app] --(1)--> [Gateway + ledger host] --(2)--> [Token Factory]
                               |  \--(3)--> [vLLM Serverless Endpoint] <--(5)-- [Bucket]
                               \--(4) cron: ratchet.live --> [Serverless Job] --(5)--> [Bucket]
```

1. Client to gateway: **no authentication in v0.1.** Plain HTTP unless you add a TLS proxy.
2. Gateway to Token Factory: HTTPS with a bearer key.
3. Gateway to vLLM: HTTPS with a bearer token (`--auth token`), if the endpoint is configured that way.
4. Cron host to the Nebius CLI: local CLI credentials.
5. Job and endpoint to bucket: a Nebius-managed mount.
6. Gateway to Tavily (live Scout only): HTTPS with a bearer key. Search results are **untrusted web content** that flows into agent prompts.
7. On the demo VM, internet to Caddy: HTTPS. Caddy is the only thing that can reach the gateway's port 8000.

## Threats and mitigations

| # | Threat | Likelihood / impact | Present in v0.1 | Planned |
|---|---|---|---|---|
| T1 | **Unauthenticated gateway use.** Anyone who can reach the port can spend teacher credits and read `/ratchet/stats` and `/ratchet/bom` | High / High | Nothing in code. Run on a private network or behind a reverse proxy that does auth | API keys per tenant, rate limits, an auth-protected dashboard |
| T2 | **Data poisoning via prompts.** An attacker sends crafted requests, the teacher answers, and those answers become training data. The attacker controls the prompt, not the label, but can skew the data distribution, or plant prompt-injection text that makes the teacher output harmful labels | Medium / High | The teacher writes every label; the student's own outputs are never training targets. A fixed golden split and release gates catch broad quality drops. Per-tenant task names keep datasets separate | Per-tenant datasets and adapters, anomaly detection on new examples (bursts from one client, near-duplicates), a cap on examples per client, optional human review of the golden set |
| T3 | **Backdoor through rare triggers.** Poisoned data affects only inputs with a trigger phrase, so golden and audit precision stay high | Low / High | Audits sample live traffic at random, but a rare trigger may never be audited | Trigger scanning, higher audit rates on low-frequency patterns, provenance per client |
| T4 | **PII in logs and training data.** Escalated and audited requests are stored verbatim and trained on; adapters can memorise text | High / High | None. `README` and `RUNBOOK` describe the manual deletion procedure | Presidio-style redaction before `add_example`, a per-tenant opt-out, retention limits, Token Factory zero-data-retention setting, excluding fields marked sensitive |
| T5 | **Key leakage** (`NEBIUS_API_KEY`, `RATCHET_STUDENT_KEY`) | Medium / High | Keys are read from the environment only. `.env` is in `.gitignore` | A secrets manager, separate keys for gateway and Night School, spend alerts |
| T6 | **License misuse.** Training on outputs from a teacher whose terms forbid it | Medium / High | `licenses.require_distillable()` refuses closed teachers (`gpt`, `o1`, `o3`, `o4`, `claude`, `gemini` prefixes) and unknown models at startup. Every example stores its teacher and license. `dataset.rows()` drops non-distillable rows. `/ratchet/bom` exports provenance | Signed bill of materials per adapter, a license registry kept up to date with model cards, and legal review per teacher. *Not legal advice* |
| T7 | **Token Factory terms §5(f).** "Develop a product or service that competes with the Service" | Unknown / High | Written clarification requested from Token Factory support (Oct 19, 2026). Positioned as running on Nebius end to end | Act on Nebius's answer |
| T8 | **Student endpoint exposed** without a token, or with a weak one | Medium / Medium | `student_endpoint.sh` requires `RATCHET_STUDENT_KEY` and sets `--auth token` | Rotate keys, private networking |
| T9 | **Unauthorised runtime LoRA loading.** `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` lets any caller with the endpoint token load adapters from paths the server can read | Medium / High | Protected only by the endpoint token | Separate admin path or proxy, an allow-list of adapter paths |
| T10 | **Release state tampering.** Writing to the ledger changes which model serves users | Low / High | File permissions on the host | Signed state changes, an audit table of manual changes |
| T11 | **Denial of wallet.** Long prompts, or many escalations to the teacher | Medium / Medium | None beyond Token Factory rate limits | Per-tenant budgets, `max_tokens` caps, escalation-rate alerts |
| T12 | **Unbounded task registry.** Every new `X-Ratchet-Task` value adds an entry to the in-memory `tasks.REGISTRY` | Low / Low | Fixed in v0.1.1: unknown names get a temporary task that is not stored; names must match `[A-Za-z0-9_.-]{1,64}` | Allow-list task names per tenant |
| T13 | **Judge manipulation.** Candidate output contains instructions to the judge | Low / Medium | The judge is used only for free-text tasks and does not gate releases in v0.1. Labels are compared exactly | Keep exact-match fields as the gate; delimit candidate text in the judge prompt |
| T14 | **Dashboard XSS** from event or license text | Low / Medium | `dashboard.html` escapes all dynamic text with `esc()` and opens links with `rel="noopener"` | Content-Security-Policy header |
| T15 | **Prompt injection from web search results into Scout's agents.** A page Tavily returns contains text like "ignore your instructions and…", aimed at the triage, reader or writer agent | Medium / Medium | Results are only data: titles, URLs and snippets go into a JSON user message, never into a system prompt. Triage and reader outputs are labels (`relevant`, `source_type`, `answers_question`, `fact_type`) plus one short free-text field that is ignored for agreement and training comparison. The writer is pinned to the teacher, so injected text cannot steer which model answers it. No agent runs tools or code from model output: the only tool is search, called with the planner's queries. The Scout page escapes all text and only links `http(s)` URLs, opened with `rel="noopener noreferrer"` | Delimit untrusted text more strictly in prompts; cap excerpt length further; flag answers that cite a source the reader marked as not answering |
| T16 | **Poisoned training data through Scout.** Injected search text makes the teacher write wrong labels, which become training data for `source-triage` and `page-read` | Low / Medium | Only label fields are compared; the release gate and audits catch broad quality drops (see T2). Pinned planner and writer answers are never stored | Same as T2 |
| T17 | **Budget exhaustion on the public demo.** Someone runs Scout over and over to spend Token Factory credit and Tavily credits | High / Medium | Budget guard in live mode: `RATCHET_SCOUT_DAILY_BUDGET_USD` (default $3.00 per UTC day), `RATCHET_SCOUT_MAX_RUNS_PER_DAY` (default 30, about 90 Tavily credits), `RATCHET_SCOUT_RUNS_PER_HOUR` per IP (default 6). Spend is counted per step, so failed runs count. Questions are capped at 300 characters. Worst case per day is the cap plus the runs in progress when it is reached. Many IPs can still use up the daily budget early (a denial of service for others, not extra cost) | Per-user quotas behind a login; CAPTCHA |
| T18 | **Shared mock demo controls.** Any visitor can run days, break the next deploy or reset the shared simulation, or flood the controls | Medium / Low | Mock mode only, no real cost. `day` and `reset` run one at a time (409 when busy); `reset` has a 30 s cooldown (429); `requests` is clamped to 100–5,000. `RATCHET_API_GUARD=guarded` blocks `/ratchet/demo/*` at Caddy | Per-visitor simulations |
| T19 | **Public paid API on the demo URL.** `/v1/chat/completions` is open on the VM, and the Scout budget guard does not cover it | High / High in live mode | Opt-in Caddy guard: `RATCHET_API_GUARD=guarded` returns 403 on `/v1/*` and `/ratchet/demo/*` without `X-Ratchet-Admin-Token`; a token shorter than 32 characters blocks everyone; Caddy strips the header before the gateway. The guide says to turn it on before switching to live mode. It is **off by default** (`open`) | Gateway API keys (see T1) |
| T20 | **Spoofed client IP** to dodge the per-IP limit | Low / Low | On the VM, Caddy overwrites `X-Forwarded-For` and uvicorn trusts it only because port 8000 is not published. `RATCHET_TRUST_PROXY` is off by default | Keep port 8000 private |

## Privacy notes

- Mock mode and the simulation use only synthetic tickets (`sim/tickets.py`) and simulated search results on `example.org` (`sim/research.py`). The repo contains no real customer data.
- In live mode, Scout sends its search queries (written by the planner from the user's question) to Tavily under Tavily's terms. Do not type personal data into the public demo.
- In live mode, requests that reach the teacher are also processed by Nebius under its terms. Token Factory stores inputs and outputs for speculative decoding unless you opt out (ToS §7). Opt out for customer data.
- Requests the student serves without an audit are not stored as examples, but their metadata (task, confidence, cost) is logged in `requests`. Message text is not stored in `requests`.

## Reporting

See the repository's `SECURITY.md` for how to report a vulnerability.
