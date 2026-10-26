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
// THE VISITOR'S ADDRESS: a request that came through the site's Pages proxy (web/public/_worker.js) reaches this Worker from Cloudflare, so its
// cf-connecting-ip is not the visitor's. The proxy sends the visitor's address as x-ratchet-client-ip with x-ratchet-edge set to the secret
// EDGE_TOKEN. This Worker believes x-ratchet-client-ip only when x-ratchet-edge equals ITS OWN EDGE_TOKEN (anyone can call this Worker directly with any
// header); else it uses cf-connecting-ip. Either way it sets x-forwarded-for to that address (the gateway's per-IP limits and Scout's hourly limit read
// it) and removes both x-ratchet-* headers, so the token never reaches the container or its logs. EDGE_TOKEN is a secret on BOTH sides, the same value,
// any long random string, never in the repo: `npx wrangler secret put EDGE_TOKEN --name ratchet-live` here and
// `npx wrangler pages secret put EDGE_TOKEN --project-name ratchet` for the site. Without it every main-site visitor looks like one address.
import { Container, getContainer, type StopParams } from "@cloudflare/containers";
import { DurableObject } from "cloudflare:workers";
import { videoRedirect } from "./video";
import { dailyLimits, forwardedHeaders, overQuota, takeQuota, usesDemoKey, visitorIp, type QuotaNamespace } from "./visitor";
import { API_WAIT_MS, READ_RETRY_MS, WAKE_AFTER_MS, isRetryableRead, warmingFor, warmingJson, warmingPage, wantsPage } from "./waking";

interface Env {
  RATCHET_LIVE: DurableObjectNamespace<RatchetLive>;
  NEBIUS_API_KEY: string;
  DEMO_API_KEY: string;
  TAVILY_API_KEY?: string;
  /** Shared with the Pages proxy: it proves x-ratchet-client-ip came from the proxy. A secret on both sides, never in the repo. */
  EDGE_TOKEN?: string;
  /** The daily counters, one object per bucket, day and visitor (wrangler.json). Absent: no daily limit. */
  VISITOR_QUOTA?: DurableObjectNamespace<VisitorQuota>;
  /** Calls per visitor per UTC day for the `try` and `scout` buckets: default 3, "0" is off. Not RATCHET_*: those are passed to the gateway. */
  EDGE_DAILY_TRIES?: string;
  EDGE_DAILY_SCOUT?: string;
  [name: string]: unknown;
}

/** The defaults. Any RATCHET_* variable or secret on the Worker replaces the entry of the same name. */
const DEFAULTS: Record<string, string> = {
  RATCHET_MODE: "live",
  // The image's /data volume does not exist on Cloudflare Containers, and live mode refuses a ledger whose folder is missing.
  // /tmp is on the container's own disk: the ledger starts empty whenever the container does (see the note above).
  RATCHET_DB: "/tmp/ratchet.db",
  RATCHET_DATA_DIR: "/tmp/ratchet-data",
  RATCHET_KEY_LIMITS: "demo:daily_requests=300,daily_usd=1.5,rpm=60,concurrency=4",
  RATCHET_DAILY_BUDGET_USD: "2.5",
  RATCHET_BUDGET_MODE: "block",
  RATCHET_SCOUT_DAILY_BUDGET_USD: "1.5",
  RATCHET_SCOUT_RUNS_PER_HOUR: "6",
  RATCHET_PUBLIC_DASHBOARD: "1",
  RATCHET_TRUST_PROXY: "1",
  RATCHET_IP_RPM: "30",
  RATCHET_MAX_INFLIGHT: "6",
  RATCHET_STATS_TTL_S: "2",
};

/** A secret as a clean string: a value piped in from PowerShell carries a trailing line break, which would break an environment variable or a header. */
const clean = (v: unknown): string => String(v ?? "").trim();

/** The container's environment: keys, then the defaults, then every RATCHET_* setting from the dashboard on top. */
function containerEnv(env: Env): Record<string, string> {
  const out: Record<string, string> = {
    NEBIUS_API_KEY: clean(env.NEBIUS_API_KEY),
    TAVILY_API_KEY: clean(env.TAVILY_API_KEY),
    // One named key for the demo's own pages; the Worker adds it, a visitor never sees it.
    RATCHET_API_KEYS: `demo:${clean(env.DEMO_API_KEY)}`,
    ...DEFAULTS,
  };
  for (const [name, value] of Object.entries(env)) {
    if (name.startsWith("RATCHET_") && typeof value === "string" && value.trim() !== "") out[name] = value.trim();
  }
  return out;
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
  /** Count a call against `limit`: allowed while below it. The read and the write of one call are not interleaved with another's. */
  async take(limit: number): Promise<{ allowed: boolean; count: number }> {
    return takeQuota(this.ctx.storage, limit);
  }

  override async alarm(): Promise<void> {
    await this.ctx.storage.deleteAll();
  }
}

/** How long the readiness check waits for one answer. */
const READY_CHECK_MS = 2500;

const sleep = (ms: number) => new Promise<null>((resolve) => setTimeout(() => resolve(null), ms));

/** The demo's pages call /v1 without a key; the Worker adds the demo key so the key never reaches a browser. */
function withDemoKey(request: Request, env: Env, ip: string | null): Request {
  const url = new URL(request.url);
  const headers = forwardedHeaders(request.headers, ip);
  if (usesDemoKey(url.pathname, headers)) headers.set("authorization", `Bearer ${clean(env.DEMO_API_KEY)}`);
  return new Request(request, { headers });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    // The demo video's short link, answered here: it never wakes the container.
    const video = videoRedirect(url.pathname);
    if (video) return video;

    // The visitor: through the Pages proxy, the address it vouches for with EDGE_TOKEN; otherwise the connection's own.
    const ip = await visitorIp(request.headers, env.EDGE_TOKEN);
    // A few paid calls a day per visitor (Try a real request, Scout runs), refused here without waking the container.
    const quotas = env.VISITOR_QUOTA as unknown as QuotaNamespace | undefined;
    const refused = await overQuota(quotas, dailyLimits(env), request.method, url.pathname, request.headers, ip);
    if (refused) return refused;

    const live = getContainer(env.RATCHET_LIVE);

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
          return await live.fetch(withDemoKey(request, env, ip));
        } catch {
          return warmingJson();
        }
      }
      await sleep(READ_RETRY_MS);
    }
    return warmingFor(request.method, request.headers.get("accept"));
  },
};
