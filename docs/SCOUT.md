# Scout: the multi-agent research tool

Scout is a small team of research agents that answers a question from the web. Every agent is a plain OpenAI client pointed at Ratchet. Two of its agents make many narrow, repetitive calls. Ratchet learns those calls and moves them from Nemotron 3 Ultra to the student. The other two agents make open-ended calls, which stay on Ultra. In the mock-mode gateway the student takes over most of the narrow calls. On the live gateway no trained student is served yet, so every call is answered by Ultra; the three recorded live runs in `evidence/scout` show Scout working end to end, not savings.

It exists to show Ratchet on real agent traffic: the kind of narrow call that, NVIDIA Research estimates, could go to small models, in the three open agent systems it studied (about 40%, 60% and 70% of their LLM queries; [arXiv 2506.02153](https://arxiv.org/abs/2506.02153), v3, Sep 22, 2026).

## Agents

| Agent | Task name | Calls per question | Route | Output (JSON) |
|---|---|---|---|---|
| Planner | `research-plan` | 1 | **pinned** to the teacher | `{"queries": [str, str, str]}` |
| Searcher | none (tool) | 1 per query | Tavily Search API (`search_depth: basic`, 1 credit) | results: title, url, content |
| Triage | `source-triage` | 1 per search result (up to 12) | **distillable** | `{"relevant": bool, "source_type": "docs\|paper\|blog\|forum\|news\|other", "reason": str}` |
| Reader | `page-read` | 1 per relevant result (up to 6) | **distillable** | `{"answers_question": bool, "fact_type": "definition\|number\|date\|name\|none", "key_fact": str}` |
| Writer | `research-write` | 1 | **pinned** to the teacher | `{"answer": str (markdown, cites [n]), "used": [int]}` |

- **Distillable** tasks compare only their label fields: `relevant` + `source_type`, and `answers_question` + `fact_type`. Free text (`reason`, `key_fact`) is ignored when deciding agreement, as with `ticket-triage`. So page-read's precision covers its `answers_question` and `fact_type` fields only; the free-text `key_fact` the writer cites is not checked.
- **Pinned** tasks (`Task.pinned = True`) always go to the teacher and are never stored as training examples. Open-ended planning and writing are where the big model earns its price.
- Triage and Reader calls run in parallel (thread pool). The ledger is guarded by a lock.

## Modes

- **mock** (no keys): the searcher returns mock results from `sim/research.py`, and the mock teacher and students answer. Only the example questions work (`GET /scout/examples`). The UI must show a "Mock search results" label in this mode.
- **live**: Tavily (`TAVILY_API_KEY`) plus the real Ratchet cascade (Token Factory teacher, vLLM student or hosted fallback). A budget guard protects the public live gateway.

## HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/`, `/scout`, `/dashboard` | The React app (`ratchet/web/dist`, built from `web/`). The hand-written pages are served at `/lite`, `/lite/scout` and `/lite/dashboard`, and when there is no build |
| GET | `/scout/examples` | `{"mode": "mock"\|"live", "examples": [str, ...], "free_text": bool}`. `free_text` is false in mock mode |
| GET | `/scout/stream?q=<question>` | Server-Sent Events, one JSON object per `data:` line (below) |
| POST | `/scout/run` | Body `{"question": str}`. Returns `{"events": [ ...same objects... ]}` once the run finishes |
| GET | `/scout/status` | `{"mode", "budget_usd", "spent_today_usd", "runs_today", "runs_per_hour_per_ip", "max_runs_per_day", "tavily": bool, "fallback_student", "student_serving"}`. `budget_usd` and `max_runs_per_day` are `null` in mock mode; `fallback_student` and `student_serving` (v0.5) say which model answers student calls ([Who answered](#who-answered)). Since v0.5 also `public_runs_left_today`, `public_budget_left_usd`, `judge_runs_today`, `persistent`, `judge` and `search` (below) |
| POST | `/scout/judge` | Body `{"code": str}`: opens a judge session (a signed `ratchet_judge` cookie). 400, 401, 404 (judge access off), 429 (10 attempts per IP per hour) |
| DELETE | `/scout/judge` | Ends a judge session: clears the cookie |

Request and response details for each endpoint are in [API.md](API.md#scout-endpoints).

### Event objects (SSE `data:` payloads, in order)

```json
{"type": "start", "run_id": "a1b2c3", "question": "What is LoRA fine-tuning?", "mode": "mock"}

{"type": "step", "agent": "planner", "task": "research-plan", "served_by": "teacher",
 "model": "nvidia/Nemotron-3-Ultra-550b-a55b", "confidence": null, "escalated": false,
 "cost_usd": 0.0013, "baseline_usd": 0.0013, "latency_ms": 2400.0,
 "detail": {"queries": ["LoRA low-rank adaptation explained", "LoRA paper Hu 2021", "LoRA vs full fine-tuning"]}}

{"type": "step", "agent": "searcher", "task": null, "served_by": "tool", "model": "tavily",
 "confidence": null, "escalated": false, "cost_usd": 0.0, "baseline_usd": 0.0, "latency_ms": 650.0,
 "detail": {"query": "LoRA paper Hu 2021", "results": [{"id": 3, "title": "...", "url": "...", "snippet": "..."}]}}

{"type": "step", "agent": "triage", "task": "source-triage", "served_by": "student", "model": "student-v4",
 "confidence": 0.94, "escalated": false, "cost_usd": 0.00003, "baseline_usd": 0.0011, "latency_ms": 310.0,
 "detail": {"id": 3, "url": "...", "title": "...", "relevant": true, "source_type": "paper", "reason": "..."}}

{"type": "step", "agent": "reader", "task": "page-read", "served_by": "teacher", "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
 "confidence": 0.61, "escalated": true, "cost_usd": 0.0014, "baseline_usd": 0.0013, "latency_ms": 2710.0,
 "detail": {"id": 3, "url": "...", "answers_question": true, "fact_type": "date", "key_fact": "..."}}

{"type": "step", "agent": "writer", "task": "research-write", "served_by": "teacher", "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
 "confidence": null, "escalated": false, "cost_usd": 0.0021, "baseline_usd": 0.0021, "latency_ms": 3100.0,
 "detail": {"answer": "LoRA freezes the base weights and trains small low-rank matrices [1]. ...", "citations": [{"n": 1, "title": "...", "url": "..."}],
            "search_unavailable": false}}

{"type": "done", "run_id": "a1b2c3", "answer": "...", "citations": [{"n": 1, "title": "...", "url": "..."}],
 "totals": {"calls": 17, "llm_calls": 15, "student_calls": 11, "teacher_calls": 4, "cost_usd": 0.0062,
            "baseline_usd": 0.0189, "savings_pct": 0.67, "wall_ms": 4200.0},
 "search": {"available": true, "partial": false, "reason": null}}

{"type": "error", "message": "The daily Scout budget is used up. Try again tomorrow (UTC), or run Ratchet yourself."}
```

- `served_by` is `student`, `teacher` or `tool`. `escalated` is true when a student was tried and was below its threshold. `model` is the id the call came back with: a student step from the hosted fallback carries `fallback_student` ([Who answered](#who-answered)).
- `baseline_usd` is what the same call would cost on Ultra alone (Ratchet's `all_teacher_cost_usd`).
- Steps for parallel agents may arrive in any order. Use `detail.id` to match a triage step and its reader step to a search result.
- An `error` event ends the stream. `/scout/stream` always answers HTTP 200 and reports problems as an `error` event, because browsers' `EventSource` cannot read status codes. `/scout/run` uses HTTP codes instead: 400 (empty question, a question over 300 characters, or a non-example question in mock mode), 429 (rate limit, daily run cap or budget), 503 (live mode without `TAVILY_API_KEY`), 502 (a model call failed during the run).
- Every step keeps exactly the top-level keys above; new fields go inside `detail`.
- A call the live gateway's split sent to the hosted Nano at random (`RATCHET_DEMO_SPLIT`, [ADR 0147](adr/0147-demo-split.md)) has `detail.route = "demo_split"`, and `totals.demo_split_calls` counts them; the page labels such a call "Random split" (picked at random, not by the confidence gate). Absent otherwise.

### When search is unavailable (v0.5)

A search that fails (any HTTP or transport error, for example a revoked key's 401 or Tavily's 432 when credits run out) or that the credit meter refuses does not end the run ([ADR 0120](adr/0120-judge-access-and-a-persistent-scout-guard.md)):

- The searcher step for that query has `detail = {"query": ..., "results": [], "unavailable": "<reason>"}` and no further queries are made. The reason is `public search credits for today are used up`, `search credits are used up`, or the error's class and status (`HTTPStatusError 401`, `ConnectError`), never its message or URL.
- Triage and reader run on what earlier queries found. With nothing found they make no calls, and the writer gets an empty fact list (its prompt already says to say when the facts do not answer the question). The answer text is the model's own, never rewritten.
- The writer step's `detail.search_unavailable` is `true`, and the `done` event's `search` is `{"available": false, "partial": <bool>, "reason": "<reason>"}` (`partial`: some results were found first). `/scout/run` answers 200: the run finished.
- The planner is pinned to the teacher and runs before any search, so every admitted run makes a runtime Token Factory call, with or without search.
- This is a fallback, not a hidden failure: `/scout/status.search` shows the last failure and the failures today, a broken key still needs fixing, and failures (not credit refusals) write a `search_error` event on the 1st, 10th, 100th and 1000th.

## Who answered

Each step's badge names the model that answered it (`web/src/lib/who-model.ts`, `whoLabel`; FR-7.40). A step's `served_by` says student, teacher or tool; for a student step only its `model` tells our adapter from the hosted fallback, which the live gateway sends student calls to when the GPU endpoint is off (no `RATCHET_STUDENT_BASE`). The page reads `fallback_student` and `student_serving` from `/scout/status` and compares:

| Step | Badge | Tooltip |
|---|---|---|
| `student`, `model` is a Night School adapter (`student-vN`) | Nano + your adapter | "Nemotron 3 Nano 4B with our LoRA adapter v[N], built to run on a Nebius Serverless Endpoint" |
| `student`, `model` is `fallback_student` | Nano 30B · hosted | "Untuned Nemotron 3 Nano 30B on Token Factory, held to confidence threshold [T]. Not covered by our adapter's precision certificate." [T] is `student_serving.threshold`. With daily endpoint windows (`student_serving.window`) it adds "Our adapter answers during the daily endpoint windows." |
| `teacher` | Ultra · Token Factory | "Nemotron 3 Ultra on Nebius Token Factory" |
| `tool` | Tavily, or Search | the tool's id |

In mock mode the tooltips start with "Simulation: a mock model plays". When a student answer of the run came from the hosted fallback, or none came from the student while the hosted fallback serves, the totals read "Answered by the student" and the summary says "answered by the hosted Nano (untuned)" instead of "by your adapter". The lite page (`/lite/scout`) uses the same words.

## Budget guard (live mode)

`ratchet/scout/guard.py`. It is checked once, when a run is admitted (INV-11: every run, judge runs included, goes through `Guard.admit` before it starts and `Guard.add_spend` when it ends).

| Variable | Default | Meaning |
|---|---|---|
| `TAVILY_API_KEY` | empty | Tavily key. Without it, live Scout returns 503 |
| `RATCHET_SCOUT_DAILY_BUDGET_USD` | `3.00` | Maximum Ratchet spend on Scout runs per UTC day. When reached, Scout returns 429 |
| `RATCHET_SCOUT_RUNS_PER_HOUR` | `6` | Runs per client IP per rolling hour |
| `RATCHET_SCOUT_MAX_RUNS_PER_DAY` | `30` | Runs per UTC day, all clients together. When reached, Scout returns 429. This caps Tavily credits: each run makes about 3 searches (1 credit each), so 30 runs use at most about 90 credits a day |
| `RATCHET_SCOUT_MAX_RESULTS` | `4` | Tavily results per query |
| `RATCHET_TRUST_PROXY` | `0` | `1` = take the client IP from the first `X-Forwarded-For` entry. Use it only behind a proxy that overwrites that header. The VM does not need it: uvicorn runs there with `--proxy-headers`, which already gives the gateway the real client IP from Caddy |

How spend is counted:

- Spend is added **per step**, from each step's `cost_usd` (which includes audit and shadow calls Ratchet made for it). A run that fails halfway still counts what its finished steps cost.
- A run that is admitted counts against the hourly and daily run limits even if it fails.
- The budget is checked when a run starts, not during it. A run that starts just under the cap can finish over it, so the real daily maximum is the cap plus the runs in progress at that moment (a few cents each).
- In live mode the counters are kept in the ledger and restored at startup, so a restart, `update.sh` or a crash does not reset them (v0.5). This holds where the ledger is on a volume (the Nebius VM kit). On the Cloudflare Containers live gateway the ledger is on the container's disk and a restart (after 30 idle minutes) resets the counters; the low defaults in `deploy/cloudflare-live/src/index.ts` and the Token Factory balance are the limit there. Each admitted run writes a `scout_run` row to the `spend` table: `usd` stays 0 (the model calls are already in `requests.cost_usd`), and `detail` holds `{"judge", "llm_usd", "status"}`, updated after each model call and when the run ends (`done`, `error`, `search_unavailable`). A row still `admitted` (a process killed mid-run) counts its last recorded cost. Rows hold no IP, question, URL, error message or code. Counters reset at 00:00 UTC; a run counts against the day it started.
- The per-IP windows stay in memory, so no IP is stored; they reset on restart. On the first restart after upgrading, runs from earlier that day have no rows and are not counted, once.

Mock mode has no budget and no daily run cap, writes no spend rows, but the per-IP rate limit applies, at 10x the live rate.

### Judges' reserve

`RATCHET_SCOUT_JUDGE_CODE` and `RATCHET_SCOUT_JUDGE_RUNS_PER_DAY` (with `RATCHET_SCOUT_JUDGE_BUDGET_USD`, [CONFIGURATION.md](CONFIGURATION.md#judge-access-and-tavily-credits)) hold back part of the day for hackathon judges, so visitors from the public gallery cannot use it up first. With the day's total T (runs, or USD), the judges' reserve J, public use P and judge use U today:

- A public run is admitted only while P + max(0, U − J) + m < T − J. For runs m is 0. For spend, m is the largest finished run today (when a judge budget is reserved), so public runs already in flight cannot spill into the judges' share.
- A judge run is admitted only while P + U < T. Judges get at least their reserve and can use public room that is left.
- Runs stay at or under T. Spend stays at or under the budget plus the runs in flight when it is reached, as before. Judge runs and public runs from one address have separate hourly windows.

On the Scout page, a small "I am a judge" link under the ask bar opens a password field and an Unlock button; it shows only when `GET /scout/status` reports `judge.enabled`, so it is absent in mock mode and on a gateway without a code. After a good code the page says "Judge access on" with the runs left today and offers Sign out (`DELETE /scout/judge`). A wrong code, too many tries (10 per IP per hour) and a gateway with judge access off each get a plain message. The page keeps the code in the input's memory only, sends it once in the POST body with `credentials: 'same-origin'`, clears it right after, and never stores or logs it ([invariant 18](../AGENTS.md)'s rule, scanned by `test_dashboard_admin_key_stays_in_memory`). A judge sends the code once (`POST /scout/judge`, in the body: uvicorn and Caddy log URLs and custom headers) and gets a signed cookie (`ratchet_judge`, `HttpOnly`, `SameSite=Strict`, `Path=/scout`, `Secure` in live mode). Only the cookie makes a run a judge run. The gateway keeps the code's SHA-256 digest and a key derived from it, never the code; the cookie survives a restart, and rotating the code ends every session. A judge without the code is a public visitor: with search unavailable they still get a run that calls Token Factory, where a 429 would give them nothing.

### Tavily credits

The free Tavily plan gives 1,000 API credits a month; a basic search costs 1 credit, an advanced one 2 ([Tavily docs](https://docs.tavily.com/documentation/api-credits), checked Oct 20, 2026). Tavily's monthly reset day is not documented (unverified), so the cycle day is a setting. With `RATCHET_TAVILY_MONTHLY_CREDITS` set, `ratchet/scout/credits.py` meters every search:

- Each search reserves its credits first and is settled after the call with what it cost: `usage.credits` when Tavily reports it (the request sends `include_usage`), else the table amount; nothing for a 4xx (432 and 433 included) or a request never sent; the table amount for a 5xx or a failure after the request was sent, because whether Tavily bills those is not documented. Each call writes a `tavily` spend row (`usd` 0; `{"judge", "credits", "depth", "http", "counted"}`).
- Public use is paced: today's allowance is what the public pool had left at 00:00 UTC, spread evenly over the days until `RATCHET_TAVILY_PACE_UNTIL` (while it falls in this cycle) or the cycle's end; unused credits roll forward. Judges draw on their reserve (`RATCHET_TAVILY_JUDGE_CREDITS`) first, then on today's allowance, so public and judge use together never pass the monthly credits.
- After a 432 ("Key limit or Plan Limit exceeded") or 433 ("PayGo limit exceeded"), no search is sent for an hour (one `tavily_exhausted` event).
- Credits never refuse a run: a public run whose allowance is used up runs without search, labelled (above). The run and USD caps bound the cost.
- The meter restores the cycle's rows at startup. Reconcile it with the Tavily dashboard weekly ([RUNBOOK.md](RUNBOOK.md)).

Mock mode settings:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MOCK_WARMUP_DAYS` | `0` | Mock days of `mixed` traffic, each followed by a night, run at startup (and on a mock-mode reset) so the mock-mode gateway opens with a trained student. `4` takes about 2 s and leaves a live student that answers about 80–90% of Scout's LLM calls (mock-mode run, mock models: 14–18 of 17–20 calls per example question, 72–86% cheaper than all-Ultra) |
| `RATCHET_MOCK_WARMUP_REQUESTS` | `1500` | Requests per warm-up day |
| `RATCHET_MOCK_PACE` | `0.25` | `/scout/stream` waits this share of each call's mock latency (Ultra 2.4 s → 0.6 s), so agents visibly work. `0` = no waiting |

## Mock research world (`sim/research.py`)

- 8 example questions about well-established ML concepts (LoRA, knowledge distillation, model cascades, the Wilson score interval, vLLM PagedAttention, Mamba state-space models, self-consistency, model collapse). The facts are from the original papers.
- Each question has 3 planned queries and a pool of 72 mock results: 12 result templates (`r-*`) × 6 variants. The templates cover relevant papers, docs and blogs, off-topic news, near-miss forum posts, and SEO listicles. Each template has fixed labels for `source-triage` and `page-read`.
- Result URLs use `example.org`, so nothing pretends to be a real page.
- `ResearchStream` produces triage and read requests for the simulator and `POST /ratchet/demo/day?workload=research|mixed`. Users phrase each question 4 ways. Questions 7 and 8 (self-consistency, model collapse) appear only from the drift day, as 20% of traffic.
- The simulator and the agents build messages with the same functions in `ratchet/scout/prompts.py`, so the mock teacher and students can look up each request by its exact user text.

## Recorded runs

The operator can save a real run as a file: the question, the run's events with their offsets and the totals. The gateway no longer serves these files (the `/scout/recorded` routes were removed); the three committed runs in `evidence/scout/` are evidence ([ADR 0140](adr/0140-recorded-scout-run.md)).

```bash
python -m ratchet.scout record "What is LoRA fine-tuning?" --base-url https://demo.example [--judge-code C] [--out evidence/scout] [--allow-mock]
```

It reads `GET /scout/stream` and writes `<out>/<UTC time>-<slug>.json` (schema `ratchet.scout-recording/v1`: question, host name, mode, events with `t_ms`, totals). It exits 1 and writes nothing when the run errors, does not finish, or the gateway is in mock mode and `--allow-mock` is not given. The judge code (or `$RATCHET_SCOUT_JUDGE_CODE`) is used for one `POST /scout/judge` and never written. The gateway never writes a recording, so no visitor's question is stored.

## Command line

```bash
python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1
```

Runs Scout as an outside client: each agent call goes to the gateway's `/v1/chat/completions` with an `X-Ratchet-Task` header. Flags: `--base-url` (default `http://localhost:8000/v1`), `--api-key` (default `$RATCHET_API_KEY` or `unused`), `--max-results` (default 4). Search runs locally: Tavily when `TAVILY_API_KEY` is set, otherwise mock results, which only cover the example questions and only make sense against a mock-mode gateway. These calls do not pass through the budget guard or the credit meter; they are plain `/v1` traffic. The command exits 0 when the run finishes and 1 when a model call fails (the error, such as a 401 for a wrong key, goes to stderr). A failed search prints `<query> -> search unavailable (<reason>)` and a warning on stderr, and the run still finishes with exit code 0.
