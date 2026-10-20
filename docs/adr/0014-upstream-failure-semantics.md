# ADR 0014: Upstream failure semantics: bounded retries, one error type, and statuses a client can act on

- Status: Accepted
- Date: 2026-10-20

## Context

Every model call goes through `OpenAICompatProvider`: the teacher and judge on Token Factory, the student on a vLLM endpoint. Until now it had one 120 s timeout for every phase and every role, no retries, and `raise_for_status()`. The gateway turned any `httpx.HTTPError` from a teacher call whose answer was needed into a 502 `teacher unavailable: <class>`. That was wrong in both directions:

- **Too eager to fail.** A single dropped connection or a 503 from an overloaded endpoint failed the request, although the next attempt would most likely have worked.
- **Too eager to retry.** An upstream 400 (`context_length_exceeded`: the conversation does not fit the teacher) or 429 became a 502, which OpenAI SDKs retry twice by default. Each retry re-ran the student's three samples and asked the teacher again, for a request that could not succeed, and the client never learned why.
- **A hung student cost two minutes.** The student shared the teacher's 120 s timeout, so an endpoint that accepted connections but never answered held every request for 120 s before the teacher stepped in.
- **A 200 with no choices was a 500.** `_call_teacher` indexed `[0]` into an empty list, and the gateway reported its own bug.
- **The daily quota's 429** was typed `rate_limit_error` with no code, so clients could not tell it from a teacher rate limit.

## Decision

- **One error type.** `ratchet/resilience.py` defines `UpstreamError(httpx.HTTPError)` with `status`, OpenAI's `type`, `code` and `param` when the error body had them, `message` (at most 300 characters, control characters and lone surrogates cleaned), and `retry_after` (from `retry-after-ms`, or `Retry-After` in seconds or as an HTTP date). It reads OpenAI's `{"error": {...}}`, vLLM's older top-level `{"object": "error", ...}` and FastAPI's `{"detail": ...}`. It never holds the request, its headers or the URL: an httpx message carries the URL, which can hold a host or a token. Subclassing `httpx.HTTPError` keeps every existing `except httpx.HTTPError` working. The provider raises nothing else: after its retries, a timeout is `UpstreamError(504, code="upstream_timeout")`, a connection or protocol failure `UpstreamError(502, code="upstream_unreachable")`, and a 2xx body that is not an object with at least one choice `UpstreamError(502, code="bad_upstream_response")`. Malformed optional fields (usage, logprobs, a non-string model) count as absent instead of crashing.
- **Retry what can work, once it is worth it.** `retry_call(fn, policy, sleep, clock, rng)` with `RetryPolicy(max_attempts=3, base_s=0.5, cap_s=8.0, deadline_s=180.0)`:
  - Retried: `ConnectError`, `ConnectTimeout`, `PoolTimeout`, `RemoteProtocolError` (the model never saw the request), and statuses 408, 409, 429, 500, 502, 503, 504.
  - A `ReadTimeout` is retried once at most: the model may already be generating, and those tokens are billed.
  - Never retried: any other 4xx, a write timeout or read error (the request may have reached the model), and a 2xx body that cannot be read (the tokens are billed; the parse happens after `retry_call`).
  - Wait: full jitter, `uniform(0, min(cap_s, base_s * 2**i))`, or the server's wait when it sent one. A server wait above `cap_s`, or any wait that would end past the deadline, stops the retries and returns the error at once: holding a request (and a worker thread) for a retry that the server has said it will refuse helps nobody.
  - The deadline bounds the whole call: each attempt's timeouts are capped by the time that is left, so a read timeout followed by a retry cannot take 240 s.
- **Timeouts and retries per role** (`Settings`, read from the environment):
  - Teacher and judge: read `RATCHET_TEACHER_TIMEOUT_S` (120 s), connect 5 s, write 10 s, pool 5 s; `RATCHET_TEACHER_RETRIES` (2) more attempts within `RATCHET_TEACHER_DEADLINE_S` (180 s).
  - Student on the request path: read `RATCHET_STUDENT_TIMEOUT_S` (15 s), connect `RATCHET_STUDENT_CONNECT_TIMEOUT_S` (2 s), no retries, because the teacher is its retry. This includes the hosted fallback model on Token Factory when no student endpoint is configured: it plays the student, so it gets the student's timeouts on its own client.
  - Student in Night School's golden eval (`python -m ratchet.live night`): the student's timeouts with the teacher's retries, because no teacher stands behind it there and one failed call would end the night.
  - A bad value raises `ValueError` when live mode starts.
- **Statuses a client can act on.** `gateway._map_upstream` maps the `UpstreamError` of a teacher call whose answer was needed:

  | Upstream | Client | `type` / `code` | `X-Should-Retry` |
  |---|---|---|---|
  | 400, 413, 422 | 400 | `invalid_request_error`, the teacher's code (`request_too_large` for a bare 413), its message and param | `false` |
  | 401, 403 | 502 | `api_error` / `upstream_auth_error` | `false` |
  | 404 | 502 | `api_error` / `upstream_not_found` | `false` |
  | 429 | 429, `Retry-After` 1 to 60 s | `rate_limit_error` / `upstream_rate_limited` | (SDK default: retry after the wait) |
  | 504, timeout | 504 | `api_error` / `teacher_timeout` | (SDK default: retry) |
  | anything else | 502 | `api_error` / `teacher_unavailable` | `false` for a redirect or a 4xx no retry fixes (402, 410, ...) |

  A teacher 404 is the gateway's configuration (the model name or base URL), never the client's, because the request's `model` field does not choose the teacher; so it is a 502 for the operator, not a 400 that blames the client.
- **The daily quota's 429** is `insufficient_quota` / `daily_quota_exceeded`, with `Retry-After` (seconds to 00:00 UTC, rounded up) and `X-Should-Retry: false`; every response under a quota carries `X-RateLimit-Reset-Requests` in OpenAI's duration format (`3h12m5s`).
- **Events and logs say status and code, never the message.** Optional-call events read `UpstreamError 504 upstream_timeout`; a failed Scout run says `a model or search call failed: upstream 503`.

Invariant 7 now reads: student failures fall back to the teacher; a teacher failure on the serving path returns a mapped upstream status (400/429 for client causes, 502/503/504 otherwise). 503 is reserved for a teacher circuit breaker that answers fast while the teacher is known to be down.

## Consequences

- PRD FR-1.11 (new), FR-1.8 and NFR-2 reworded. `Ratchet.handle` gains no I/O beyond the provider's retry waits.
- A transient teacher failure costs latency (about 1.5 s of backoff at most for two quick retries) instead of an error. A teacher that hangs costs up to the 180 s deadline instead of 120 s; that includes audits, which are synchronous, so the runbook's advice to set `RATCHET_AUDIT_RATE=0` during a long outage matters a little more. Moving audits off the request path removes that wait; it is planned with the off-path quality-control sidecar.
- A hung student endpoint costs at most its own timeouts (2 s to connect, 15 s to answer) before the teacher answers.
- Clients learn when their request is the problem (400 with the teacher's reason), when to come back (429 with a wait), and when not to bother retrying (`X-Should-Retry: false`), so OpenAI SDKs no longer re-run three student samples for a request that cannot work.
- Waits use real time in production. Tests inject `sleep` (and `clock`, `rng` for the provider) through default arguments: `OpenAICompatProvider(sleep=, clock=, rng=)` and `Runtime(sleep=)`, so the suite never waits.
- The reference simulation is unchanged: mock mode has no HTTP providers.
- `resilience.py` imports httpx for its exception types; it needs no retry library. The later breaker items put their `CircuitOpen(UpstreamError)` next to it, which is why `UpstreamError` lives there and `providers` re-exports it.

## Alternatives considered

- **A retry library (tenacity, backoff).** A new dependency for 60 lines, and none of them knows that a read timeout should be retried once and a 400 never, so the policy would be written anyway.
- **Let httpx retry** (`HTTPTransport(retries=n)`). It retries connection failures only, never a 429 or 503, and ignores `Retry-After`.
- **Retry everything up to the deadline.** Simple, but it double-bills read timeouts, waits out 429s the server has said it will refuse, and repeats 400s that cannot succeed.
- **Sleep the server's full wait, however long.** Faithful to the server, but a 30 s `Retry-After` would hold the request and a worker thread for 30 s or more; passing the wait to the client lets it decide, and OpenAI SDKs honour it up to 60 s.
- **Map the teacher's 404 to 400**, as for other upstream 4xx. It would tell clients their request was wrong when the gateway's configuration is, and the operator would not see it as an outage.
- **Retry the student too.** The teacher is the student's retry, and a second student attempt would add its timeout to every escalation while the endpoint is down.
