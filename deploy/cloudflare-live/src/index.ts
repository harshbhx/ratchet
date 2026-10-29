// The Ratchet demo in LIVE mode as a Cloudflare Container: the repo's image, answering through real Token Factory calls.
//
// EVERYTHING TUNABLE IS A CLOUDFLARE SETTING, NOT CODE. Any variable or secret on this Worker whose name starts with RATCHET_ is passed to the gateway
// as it is, and overrides the default below. So you can change, in the Cloudflare dashboard (Workers & Pages, ratchet-live, Settings, Variables and
// Secrets) and without a push or a deploy:
//   - the GPU endpoint of the student:      RATCHET_STUDENT_BASE (https://<endpoint>/v1)  and the secret  RATCHET_STUDENT_KEY
//   - the limits:                           RATCHET_KEY_LIMITS, RATCHET_DAILY_BUDGET_USD, RATCHET_SCOUT_DAILY_BUDGET_USD, RATCHET_IP_RPM, ...
//   - any other setting in docs/CONFIGURATION.md.
// A change reaches the gateway the next time its container starts: the container sleeps 30 minutes after the last request, or press
// "Redeploy" on the Worker to apply it at once. The three secrets below are never in this file, the repo or the image:
//   NEBIUS_API_KEY (Token Factory), DEMO_API_KEY (the gateway's own demo key; any long random string), TAVILY_API_KEY (optional, Scout's searches).
// Spending is capped five ways by default: a daily limit per visitor, the demo key's own limits, the gateway's daily budget, Scout's budget and a per-IP
// rate. The container keeps its ledger on its own disk, so a restart resets the day's counters: the defaults are low for that reason, and the Token
// Factory balance is the last limit.
//
// THE DAILY LIMIT PER VISITOR (3 a day in each of two buckets): every paid call reaches Token Factory, and every visitor shares the one demo key, so the
// key's own limits would be shared by all of them. The Worker counts each visitor itself, before waking the container, in a small Durable Object
// (VisitorQuota, one per bucket, UTC day and SHA-256 of the visitor's address; the address itself is never stored or logged) and answers 429 over the
// limit, with `retry-after` set to the next 00:00 UTC. The buckets: `try`, a keyless POST /v1/* (the "Try a real request" box), and `scout`, GET
// /scout/stream and POST /scout/run (a Scout run is about 20 Token Factory calls); a request with a ratchet_judge cookie skips the Scout count (the
// gateway verifies the cookie and keeps its own guard). Reads (/scout/status, /v1/models, /ratchet/*) and calls that bring their own key are not
// counted. The limits are the settings EDGE_DAILY_TRIES and EDGE_DAILY_SCOUT (default 3; 0 turns a bucket off; no RATCHET_ prefix, so they never reach
// the gateway). If the Durable Object fails, the call goes through: the demo key's limits and the budgets still cap the spend. The key's own rpm (60)
// and concurrency (4) are only the ceiling all visitors share.
//
// THE HUMAN CHECK (Cloudflare Turnstile, turnstile.ts): off until the secret TURNSTILE_SECRET_KEY is set. Then a browser's paid call must carry a
// token (header cf-turnstile-response, or the same query parameter for Scout's EventSource), verified once at the top of this Worker and removed
// before forwarding; a call with no token (curl) keeps its 3 a day and shares EDGE_DAILY_CURL_TOTAL (default 100) a day across everybody. Settings:
// TURNSTILE_SECRET_KEY (secret), TURNSTILE_SITE_KEY (public variable, served at GET /ratchet/turnstile), EDGE_TURNSTILE_MAX_FAILS (default 5),
// EDGE_DAILY_CURL_TOTAL (default 100, 0 is off). turnstile.ts says what counts as a failure and what fails open. A passed check earns a 12-hour
// signed session cookie (rt_human, human.ts), so a visitor verifies once per visit; GET /ratchet/human says whether the cookie is valid.
//
// THE RUNNING TOTALS (LiveCounters, one Durable Object, SQLite-backed, migration v3): the ledger above is emptied by every restart, so the Worker
// keeps the gateway's aggregate counters itself. After each paid call, when someone reads the totals while the container is up, and when the
// container's idle timeout expires, it reads GET /ratchet/counters from the container and adds the difference to what it holds (counters.ts
// says how a restart is told apart and why nothing is counted twice). GET /ratchet/stats/persistent answers from the Durable Object without
// waking the container: totals since the first record, requests by route group, student tokens, Token Factory cost, Scout runs, errors and server errors.
// Counts and dollars only, never an address, a prompt or a client. Nothing to configure and no secret: the binding is in wrangler.json.
//
// THE VISITOR'S ADDRESS: a request that came through the site's Pages proxy (web/public/_worker.js) reaches this Worker from Cloudflare, so its
// cf-connecting-ip is not the visitor's. The proxy sends the visitor's address as x-ratchet-client-ip with x-ratchet-edge set to the secret
// EDGE_TOKEN. This Worker believes x-ratchet-client-ip only when x-ratchet-edge equals ITS OWN EDGE_TOKEN (anyone can call this Worker directly with any
// header); else it uses cf-connecting-ip. Either way it sets x-forwarded-for to that address (the gateway's per-IP limits and Scout's hourly limit read
// it) and removes both x-ratchet-* headers, so the token never reaches the container or its logs. EDGE_TOKEN is a secret on BOTH sides, the same value,
// any long random string, never in the repo: `npx wrangler secret put EDGE_TOKEN --name ratchet-live` here and
// `npx wrangler pages secret put EDGE_TOKEN --project-name ratchet` for the site. Without it every main-site visitor looks like one address.
import { Container, getContainer, type StopParams } from "@cloudflare/containers";
import { DurableObject } from "cloudflare:workers";
import { PERSISTENT_PATH, claimPull, isPaidCall, isStreamedRun, parseSnapshot, readView, recordSnapshot, type CounterStore, type PublicView, type Snapshot } from "./counters";
import { videoRedirect } from "./video";
import { bumpCount, dailyLimits, forwardedHeaders, peekCount, takeQuota, usesDemoKey, visitorIp, type QuotaStore } from "./visitor";
import { humanAnswer, withoutHumanCookie } from "./human";
import { guardPaidCall, siteKeyAnswer, turnstileSettings, withoutTokenParam, type GuardNamespace } from "./turnstile";
import { clean, containerEnv } from "./containerenv";
import { API_WAIT_MS, READ_RETRY_MS, WAKE_AFTER_MS, isRetryableRead, warmingFor, warmingJson, warmingPage, wantsPage } from "./waking";

interface Env {
  RATCHET_LIVE: DurableObjectNamespace<RatchetLive>;
  NEBIUS_API_KEY: string;
  DEMO_API_KEY: string;
  /** Optional: a second gateway key for judges (calls with it skip the visitor quota and the human check). Unset: no judges key. */
  JUDGES_API_KEY?: string;
  TAVILY_API_KEY?: string;
  /** Shared with the Pages proxy: it proves x-ratchet-client-ip came from the proxy. A secret on both sides, never in the repo. */
  EDGE_TOKEN?: string;
  /** The daily counters, one object per bucket, day and visitor (wrangler.json). Absent: no daily limit. */
  VISITOR_QUOTA?: DurableObjectNamespace<VisitorQuota>;
  /** The running totals that survive restarts (wrangler.json). Absent: /ratchet/stats/persistent says nothing is recorded. */
  LIVE_COUNTERS?: DurableObjectNamespace<LiveCounters>;
  /** Calls per visitor per UTC day for the `try` and `scout` buckets: default 3, "0" is off. Not RATCHET_*: those are passed to the gateway. */
  EDGE_DAILY_TRIES?: string;
  EDGE_DAILY_SCOUT?: string;
  /** The human check (turnstile.ts). The secret turns it on; without it nothing below does anything. */
  TURNSTILE_SECRET_KEY?: string;
  /** The widget's PUBLIC site key. The web app reads it from GET /ratchet/turnstile, so changing it needs no rebuild. */
  TURNSTILE_SITE_KEY?: string;
  /** Failed human checks per visitor per UTC day before a block until 00:00 UTC: default 5, "0" is off. */
  EDGE_TURNSTILE_MAX_FAILS?: string;
  /** Keyless calls without a token (curl) per UTC day, all visitors together: default 100, "0" is off. */
  EDGE_DAILY_CURL_TOTAL?: string;
  /** Comma-separated hostnames a good token may come from: default ratchet.dualmindlab.tech and live.ratchet.dualmindlab.tech. */
  TURNSTILE_HOSTNAMES?: string;
  [name: string]: unknown;
}

export class RatchetLive extends Container<Env> {
  defaultPort = 8000;
  sleepAfter = "30m";

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    this.envVars = containerEnv(env);
  }

  // What the container did, in the Worker's logs (no keys, no request text): an exit code of 1 is a crash at start, 137 a kill.
  override onStart(): void {
    console.log(JSON.stringify({ event: "container_start" }));
  }

  /** The idle timeout: take the counters while the container is still up, then stop it as the default does. A failure here never keeps it running. */
  override async onActivityExpired(): Promise<void> {
    try {
      if (this.env.LIVE_COUNTERS) {
        const answer = await this.containerFetch(new Request("http://container/ratchet/counters"));
        const snap = answer.ok ? parseSnapshot(await answer.json()) : null;
        if (snap) await countersOf(this.env).record(snap);
      }
    } catch {
      console.log(JSON.stringify({ event: "counters_error", where: "idle" }));
    }
    await super.onActivityExpired();
  }

  override onStop(params: StopParams): void {
    console.log(JSON.stringify({ event: "container_stop", exitCode: params.exitCode, reason: params.reason }));
  }

  override onError(error: unknown): unknown {
    console.log(JSON.stringify({ event: "container_error", error: String(error).slice(0, 300) }));
    throw error;
  }
}

/** One visitor's count for one bucket and one UTC day (the object's name says which; see visitor.ts). A counter and an alarm that deletes it. */
export class VisitorQuota extends DurableObject {
  private get store(): QuotaStore {
    return this.ctx.storage as unknown as QuotaStore;
  }

  /** The count so far, without counting (the human check's failures: is this visitor blocked?). */
  async peek(): Promise<number> {
    return peekCount(this.store);
  }

  /** Add one (a failed human check). */
  async bump(): Promise<number> {
    return bumpCount(this.store);
  }

  /** Count a call against `limit`: allowed while below it. The read and the write of one call are not interleaved with another's. */
  async take(limit: number): Promise<{ allowed: boolean; count: number }> {
    return takeQuota(this.ctx.storage, limit);
  }

  override async alarm(): Promise<void> {
    await this.ctx.storage.deleteAll();
  }
}

/** The gateway's running totals: one object that holds them all, so two calls never write at once (counters.ts). */
export class LiveCounters extends DurableObject {
  private get store(): CounterStore {
    return this.ctx.storage as unknown as CounterStore;
  }

  async record(snap: Snapshot): Promise<PublicView> {
    return recordSnapshot(this.store, snap);
  }

  async view(): Promise<PublicView> {
    return readView(this.store);
  }

  /** True when no pull started within `gapMs`: a burst of calls takes one snapshot. */
  async claim(gapMs: number): Promise<boolean> {
    return claimPull(this.store, gapMs);
  }
}

/** The one counters object, by name. */
function countersOf(env: Env) {
  const ns = env.LIVE_COUNTERS as DurableObjectNamespace<LiveCounters>;
  return ns.get(ns.idFromName("totals"));
}

/** Read the container's counters and add them to the totals. Never throws: the counters are a courtesy, not part of any request. */
async function pullCounters(env: Env, origin: string, gapMs: number): Promise<void> {
  if (!env.LIVE_COUNTERS) return;
  try {
    const counters = countersOf(env);
    if (!(await counters.claim(gapMs))) return;
    const answer = await getContainer(env.RATCHET_LIVE).fetch(new Request(new URL("/ratchet/counters", origin)));
    const snap = answer.ok ? parseSnapshot(await answer.json()) : null;
    if (snap) await counters.record(snap);
  } catch {
    console.log(JSON.stringify({ event: "counters_error", where: "pull" }));
  }
}

/** The totals for GET /ratchet/stats/persistent. While the container is up the Worker first brings them up to date; it never wakes it. */
async function persistentTotals(env: Env, origin: string): Promise<Response> {
  const headers = { "content-type": "application/json", "cache-control": "no-store" };
  if (!env.LIVE_COUNTERS) return new Response(JSON.stringify({ recorded: false, since: null, updated: null, instances: 0, totals: null }), { headers });
  try {
    const state = await getContainer(env.RATCHET_LIVE).getState();
    if (state.status === "running" || state.status === "healthy") await pullCounters(env, origin, 5000);
    return new Response(JSON.stringify(await countersOf(env).view()), { headers });
  } catch {
    return new Response(JSON.stringify({ recorded: false, since: null, updated: null, instances: 0, totals: null }), { headers });
  }
}

const STREAM_RUN_MS = 25_000;

/** How long the readiness check waits for one answer. */
const READY_CHECK_MS = 2500;

const sleep = (ms: number) => new Promise<null>((resolve) => setTimeout(() => resolve(null), ms));

/** The demo's pages call /v1 without a key; the Worker adds the demo key so the key never reaches a browser. */
function withDemoKey(request: Request, env: Env, ip: string | null): Request {
  const url = new URL(request.url);
  const headers = forwardedHeaders(request.headers, ip);
  if (usesDemoKey(url.pathname, headers)) headers.set("authorization", `Bearer ${clean(env.DEMO_API_KEY)}`);
  // The human cookie is for this Worker only; the judge cookie and any other travel on.
  const rest = withoutHumanCookie(headers.get("cookie"));
  if (rest === null) headers.delete("cookie");
  else headers.set("cookie", rest);
  // The human-check token (header or Scout's query parameter) was spent at the top of fetch; the gateway never sees it.
  return new Request(withoutTokenParam(url), new Request(request, { headers }));
}

/** The same answer with the human-check cookie added (an answer from the container is immutable, so it is copied; a streamed body passes through). */
function withCookie(answer: Response, cookie: string): Response {
  const headers = new Headers(answer.headers);
  headers.append("set-cookie", cookie);
  return new Response(answer.body, { status: answer.status, statusText: answer.statusText, headers });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    // A token that passed the human check earns the visit's cookie (human.ts); it rides on whatever answer this request gets, a refusal included.
    const out: { cookie?: string } = {};
    const answer = await handle(request, env, ctx, out);
    return out.cookie ? withCookie(answer, out.cookie) : answer;
  },
};

async function handle(request: Request, env: Env, ctx: ExecutionContext, out: { cookie?: string }): Promise<Response> {
    const url = new URL(request.url);
    // The demo video's short link, answered here: it never wakes the container.
    const video = videoRedirect(url.pathname);
    if (video) return video;

    // The visitor: through the Pages proxy, the address it vouches for with EDGE_TOKEN; otherwise the connection's own.
    const ip = await visitorIp(request.headers, env.EDGE_TOKEN);
    const settings = turnstileSettings(env);
    // The widget's public site key, for the web app: answered here, never wakes the container.
    if (request.method === "GET" && url.pathname === "/ratchet/turnstile") return siteKeyAnswer(settings);
    // Is this visitor already verified for the visit (the rt_human cookie)? Answered here too, so the page knows whether to show the check at all.
    if (request.method === "GET" && url.pathname === "/ratchet/human") return humanAnswer(settings.secret, request.headers, ip);
    // The human check, then a few paid calls a day per visitor (Try a real request, Scout runs) and the shared curl total, refused here without
    // waking the container. The token is verified once, here: the loops below that re-send a request while the container wakes never ask again.
    const quotas = env.VISITOR_QUOTA as unknown as GuardNamespace | undefined;
    const refused = await guardPaidCall({ quotas, limits: dailyLimits(env), settings, method: request.method, url, headers: request.headers, ip, out });
    if (refused) return refused;

    // The running totals: answered here, from the Durable Object.
    if (request.method === "GET" && url.pathname === PERSISTENT_PATH) return persistentTotals(env, url.origin);

    const live = getContainer(env.RATCHET_LIVE);
    // After a paid call, catch the totals up (a Scout stream answers at once and runs on, so its pull waits for the run).
    const catchUp = (): void => {
      if (isPaidCall(request.method, url.pathname)) ctx.waitUntil(pullCounters(env, url.origin, 3000));
      else if (isStreamedRun(request.method, url.pathname)) ctx.waitUntil(sleep(STREAM_RUN_MS).then(() => pullCounters(env, url.origin, 3000)));
    };

    if (url.pathname === "/__ready") {
      const answer = live.fetch(new Request(new URL("/healthz", url.origin)));
      ctx.waitUntil(answer.then(() => undefined).catch(() => undefined));
      const ok = await Promise.race([answer.then((r) => r.ok).catch(() => false), sleep(READY_CHECK_MS).then(() => false)]);
      return Response.json({ ready: ok === true }, { headers: { "cache-control": "no-store" } });
    }

    const page = wantsPage(request.method, request.headers.get("accept"));
    if (page) {
      const pending = live.fetch(withDemoKey(request, env, ip)); // starts the container if it is asleep
      const first = await Promise.race([pending.catch(() => null), sleep(WAKE_AFTER_MS)]);
      if (first) return first;
      ctx.waitUntil(pending.then(() => undefined).catch(() => undefined));
      return warmingPage();
    }

    const deadline = Date.now() + API_WAIT_MS;
    if (isRetryableRead(request.method, url.pathname)) {
      // A read may be sent again if the container drops while it starts; the wait is bounded, then the visitor gets the warming-up JSON.
      const attempts = (async (): Promise<Response | null> => {
        while (Date.now() < deadline) {
          try {
            return await live.fetch(withDemoKey(request, env, ip));
          } catch {
            await sleep(READ_RETRY_MS);
          }
        }
        return null;
      })();
      const answer = await Promise.race([attempts, sleep(Math.max(0, deadline - Date.now()))]);
      if (answer) return answer;
      ctx.waitUntil(attempts.then(() => undefined).catch(() => undefined));
      return warmingJson();
    }

    // Anything that may cost money or change state is sent exactly once, and only after the container answers /healthz.
    while (Date.now() < deadline) {
      const left = deadline - Date.now();
      const ok = await Promise.race([
        live.fetch(new Request(new URL("/healthz", url.origin))).then((r) => r.ok).catch(() => false),
        sleep(left).then(() => false),
      ]);
      if (ok) {
        try {
          const answer = await live.fetch(withDemoKey(request, env, ip));
          catchUp();
          return answer;
        } catch {
          return warmingJson();
        }
      }
      await sleep(READ_RETRY_MS);
    }
    return warmingFor(request.method, request.headers.get("accept"));
}
