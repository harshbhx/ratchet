// The Ratchet demo in LIVE mode as a Cloudflare Container: the repo's image, answering through real Token Factory calls.
// The keys are Worker secrets (never in this file, the repo or the image): NEBIUS_API_KEY for Token Factory, DEMO_API_KEY for the
// gateway's own demo key, TAVILY_API_KEY (optional) for Scout's searches. Spending is capped three ways: the demo key's own limits,
// the gateway's daily budget and a per-IP rate, all set below. On demand like the simulation demo: it sleeps 30 minutes after the
// last request and keeps no state across a restart (its ledger is on the container's disk), so every restart also resets the day's counters:
// the caps are therefore set low, and the Token Factory balance is the last limit.
import { Container, getContainer } from "@cloudflare/containers";
import { WAKING_HTML } from "./waking";

interface Env {
  RATCHET_LIVE: DurableObjectNamespace<RatchetLive>;
  NEBIUS_API_KEY: string;
  DEMO_API_KEY: string;
  TAVILY_API_KEY?: string;
}

export class RatchetLive extends Container<Env> {
  defaultPort = 8000;
  sleepAfter = "30m";

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.envVars = {
      RATCHET_MODE: "live",
      NEBIUS_API_KEY: env.NEBIUS_API_KEY,
      TAVILY_API_KEY: env.TAVILY_API_KEY ?? "",
      // One named key for the demo's own pages; the Worker adds it, a visitor never sees it.
      RATCHET_API_KEYS: `demo:${env.DEMO_API_KEY}`,
      RATCHET_KEY_LIMITS: "demo:daily_requests=300,daily_usd=1.5,rpm=10,concurrency=2",
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
  }
}

/** A page that answers within this time is passed straight through; a slower one means the container is waking. */
const WAKE_AFTER_MS = 2000;
/** How long the readiness check waits for one answer. */
const READY_CHECK_MS = 2500;

const sleep = (ms: number) => new Promise<null>((resolve) => setTimeout(() => resolve(null), ms));

/** The demo's pages call /v1 without a key; the Worker adds the demo key so the key never reaches a browser. */
function withDemoKey(request: Request, env: Env): Request {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  const ip = request.headers.get("cf-connecting-ip");
  if (ip) headers.set("x-forwarded-for", ip);
  if (url.pathname.startsWith("/v1/") && !headers.get("authorization")) {
    headers.set("authorization", `Bearer ${env.DEMO_API_KEY}`);
  }
  return new Request(request, { headers });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const live = getContainer(env.RATCHET_LIVE);

    if (url.pathname === "/__ready") {
      const answer = live.fetch(new Request(new URL("/healthz", url.origin)));
      ctx.waitUntil(answer.then(() => undefined).catch(() => undefined));
      const ok = await Promise.race([answer.then((r) => r.ok).catch(() => false), sleep(READY_CHECK_MS).then(() => false)]);
      return Response.json({ ready: ok === true }, { headers: { "cache-control": "no-store" } });
    }

    const wantsPage = request.method === "GET" && (request.headers.get("accept") ?? "").includes("text/html");
    const pending = live.fetch(withDemoKey(request, env)); // starts the container if it is asleep
    if (!wantsPage) return pending;

    const first = await Promise.race([pending.catch(() => null), sleep(WAKE_AFTER_MS)]);
    if (first) return first;
    ctx.waitUntil(pending.then(() => undefined).catch(() => undefined));
    return new Response(WAKING_HTML, {
      status: 200,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    });
  },
};
