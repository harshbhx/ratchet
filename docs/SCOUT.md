# Scout: the multi-agent research demo

Scout is a small team of research agents that answers a question from the web. Every agent is a plain OpenAI client pointed at Ratchet. Two of its agents make many narrow, repetitive calls. Ratchet learns those calls and moves them from Nemotron 3 Ultra to the student. The other two agents make open-ended calls, which stay on Ultra.

It exists to show Ratchet on real agent traffic: the case NVIDIA Research estimates covers 40–70% of LLM calls in agent systems ([arXiv 2506.02153](https://arxiv.org/html/2506.02153v1)).

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

- **mock** (no keys): the searcher returns simulated results from `sim/research.py`, and the mock teacher and students answer. Only the example questions work (`GET /scout/examples`). The UI must show a "Simulated search results" label in this mode.
- **live**: Tavily (`TAVILY_API_KEY`) plus the real Ratchet cascade (Token Factory teacher, vLLM student or hosted fallback). A budget guard protects the public demo.

## HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Landing page (`ratchet/web/landing.html`) |
| GET | `/scout` | Scout UI (`ratchet/web/scout.html`) |
| GET | `/dashboard` | Ratchet dashboard (`ratchet/dashboard.html`) |
| GET | `/scout/examples` | `{"mode": "mock"\|"live", "examples": [str, ...], "free_text": bool}`. `free_text` is false in mock mode |
| GET | `/scout/stream?q=<question>` | Server-Sent Events, one JSON object per `data:` line (below) |
| POST | `/scout/run` | Body `{"question": str}`. Returns `{"events": [ ...same objects... ]}` once the run finishes |
| GET | `/scout/status` | `{"mode", "budget_usd", "spent_today_usd", "runs_today", "runs_per_hour_per_ip", "max_runs_per_day", "tavily": bool}`. `budget_usd` and `max_runs_per_day` are `null` in mock mode |

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
 "detail": {"answer": "LoRA freezes the base weights and trains small low-rank matrices [1]. ...", "citations": [{"n": 1, "title": "...", "url": "..."}]}}

{"type": "done", "run_id": "a1b2c3", "answer": "...", "citations": [{"n": 1, "title": "...", "url": "..."}],
 "totals": {"calls": 17, "llm_calls": 15, "student_calls": 11, "teacher_calls": 4, "cost_usd": 0.0062,
            "baseline_usd": 0.0189, "savings_pct": 0.67, "wall_ms": 4200.0}}

{"type": "error", "message": "The daily demo budget is used up. Try again tomorrow (UTC), or run Ratchet yourself."}
```

- `served_by` is `student`, `teacher` or `tool`. `escalated` is true when a student was tried and was below its threshold.
- `baseline_usd` is what the same call would cost on Ultra alone (Ratchet's `all_teacher_cost_usd`).
- Steps for parallel agents may arrive in any order. Use `detail.id` to match a triage step and its reader step to a search result.
- An `error` event ends the stream. `/scout/stream` always answers HTTP 200 and reports problems as an `error` event, because browsers' `EventSource` cannot read status codes. `/scout/run` uses HTTP codes instead: 400 (empty question, a question over 300 characters, or a non-example question in mock mode), 429 (rate limit, daily run cap or budget), 503 (live mode without `TAVILY_API_KEY`), 502 (a model or search call failed during the run).

## Budget guard (live mode)

`ratchet/scout/guard.py`. It is checked once, when a run is admitted.

| Variable | Default | Meaning |
|---|---|---|
| `TAVILY_API_KEY` | empty | Tavily key. Without it, live Scout returns 503 |
| `RATCHET_SCOUT_DAILY_BUDGET_USD` | `3.00` | Maximum Ratchet spend on Scout runs per UTC day. When reached, Scout returns 429 |
| `RATCHET_SCOUT_RUNS_PER_HOUR` | `6` | Runs per client IP per rolling hour |
| `RATCHET_SCOUT_MAX_RUNS_PER_DAY` | `30` | Runs per UTC day, all clients together. When reached, Scout returns 429. This caps Tavily credits: each run makes about 3 searches (1 credit each), so 30 runs use at most about 90 credits a day |
| `RATCHET_SCOUT_MAX_RESULTS` | `4` | Tavily results per query |
| `RATCHET_TRUST_PROXY` | `0` | `1` = take the client IP from the first `X-Forwarded-For` entry. Use it only behind a proxy that overwrites that header. The demo VM does not need it: uvicorn runs there with `--proxy-headers`, which already gives the gateway the real client IP from Caddy |

How spend is counted:

- Spend is added **per step**, from each step's `cost_usd` (which includes audit and shadow calls Ratchet made for it). A run that fails halfway still counts what its finished steps cost.
- A run that is admitted counts against the hourly and daily run limits even if it fails.
- The budget is checked when a run starts, not during it. A run that starts just under the cap can finish over it, so the real daily maximum is the cap plus the runs in progress at that moment (a few cents each).
- Counters are in memory: they reset at 00:00 UTC and when the gateway restarts.

Mock mode has no budget and no daily run cap, but the per-IP rate limit applies, at 10x the live rate.

Mock-mode demo settings:

| Variable | Default | Meaning |
|---|---|---|
| `RATCHET_MOCK_WARMUP_DAYS` | `0` | Simulated days of `mixed` traffic, each followed by a night, run at startup (and on demo reset) so the demo opens with a trained student. `4` takes about 2 s and leaves a live student that answers about 80–90% of Scout's LLM calls (simulation, mock models: 14–18 of 17–20 calls per example question, 72–86% cheaper than all-Ultra) |
| `RATCHET_MOCK_WARMUP_REQUESTS` | `1500` | Requests per warm-up day |
| `RATCHET_MOCK_PACE` | `0.25` | `/scout/stream` waits this share of each call's simulated latency (Ultra 2.4 s → 0.6 s), so agents visibly work. `0` = no waiting |

## Mock research world (`sim/research.py`)

- 8 example questions about well-established ML concepts (LoRA, knowledge distillation, model cascades, the Wilson score interval, vLLM PagedAttention, Mamba state-space models, self-consistency, model collapse). The facts are from the original papers.
- Each question has 3 planned queries and a pool of 72 simulated results: 12 result templates (`r-*`) × 6 variants. The templates cover relevant papers, docs and blogs, off-topic news, near-miss forum posts, and SEO listicles. Each template has fixed labels for `source-triage` and `page-read`.
- Result URLs use `example.org`, so nothing pretends to be a real page.
- `ResearchStream` produces triage and read requests for the simulator and `POST /ratchet/demo/day?workload=research|mixed`. Users phrase each question 4 ways. Questions 7 and 8 (self-consistency, model collapse) appear only from the drift day, as 20% of traffic.
- The simulator and the agents build messages with the same functions in `ratchet/scout/prompts.py`, so the mock teacher and students can look up each request by its exact user text.

## Command line

```bash
python -m ratchet.scout "What is LoRA fine-tuning?" --base-url http://localhost:8000/v1
```

Runs Scout as an outside client: each agent call goes to the gateway's `/v1/chat/completions` with an `X-Ratchet-Task` header. Flags: `--base-url` (default `http://localhost:8000/v1`), `--api-key` (default `$RATCHET_API_KEY` or `unused`), `--max-results` (default 4). Search runs locally: Tavily when `TAVILY_API_KEY` is set, otherwise simulated results, which only cover the example questions and only make sense against a mock-mode gateway. These calls do not pass through the budget guard; they are plain `/v1` traffic. The command exits 0 when the run finishes and 1 when a call fails (the error, such as a 401 for a wrong key, goes to stderr).
