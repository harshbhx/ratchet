# Threat model: security and privacy

Scope: Ratchet v0.1.1 in live mode, made up of the gateway, the SQLite ledger, the Night School driver, the Nebius bucket, the vLLM endpoint and the Token Factory calls. Mock mode handles only synthetic data. Each threat below says plainly what exists today and what is planned.

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| Customer request text | `examples.messages`, `night/train.jsonl` in the bucket, adapters (memorised) | May contain personal or confidential data |
| Teacher outputs (training data) | `examples.completion`, bucket | Business value, and license provenance |
| Student adapters | Bucket `/data/adapters/*` | The product the customer owns |
| Ledger | `RATCHET_DB` | Provenance, version state, audit trail |
| API keys | `NEBIUS_API_KEY`, `RATCHET_STUDENT_KEY`, Nebius CLI and S3 credentials | Spend and data access |
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
| T12 | **Unbounded task registry.** Every new `X-Ratchet-Task` value adds an entry to the in-memory `tasks.REGISTRY` | Low / Low | None | Allow-list task names per tenant |
| T13 | **Judge manipulation.** Candidate output contains instructions to the judge | Low / Medium | The judge is used only for free-text tasks and does not gate releases in v0.1. Labels are compared exactly | Keep exact-match fields as the gate; delimit candidate text in the judge prompt |
| T14 | **Dashboard XSS** from event or license text | Low / Medium | `dashboard.html` escapes all dynamic text with `esc()` and opens links with `rel="noopener"` | Content-Security-Policy header |

## Privacy notes

- Mock mode and the simulation use only synthetic tickets (`sim/tickets.py`). The repo contains no real customer data.
- In live mode, requests that reach the teacher are also processed by Nebius under its terms. Token Factory stores inputs and outputs for speculative decoding unless you opt out (ToS §7). Opt out for customer data.
- Requests the student serves without an audit are not stored as examples, but their metadata (task, confidence, cost) is logged in `requests`. Message text is not stored in `requests`.

## Reporting

See the repository's `SECURITY.md` for how to report a vulnerability.
