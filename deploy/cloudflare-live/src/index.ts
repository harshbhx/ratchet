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
// Spending is capped three ways by default: the demo key's own limits, the gateway's daily budget and a per-IP rate. The container keeps its ledger
// on its own disk, so a restart resets the day's counters: the defaults are low for that reason, and the Token Factory balance is the last limit.
import { Container, getContainer, type StopParams } from "@cloudflare/containers";
import { WAKING_HTML } from "./waking";

interface Env {
  RATCHET_LIVE: DurableObjectNamespace<RatchetLive>;
  NEBIUS_API_KEY: string;
  DEMO_API_KEY: string;
  TAVILY_API_KEY?: string;
  [name: string]: unknown;
}

/** The defaults. Any RATCHET_* variable or secret on the Worker replaces the entry of the same name. */
const DEFAULTS: Record<string, string> = {
  RATCHET_MODE: "live",
  // The image's /data volume does not exist on Cloudflare Containers, and live mode refuses a ledger whose folder is missing.
  // /tmp is on the container's own disk: the ledger starts empty whenever the container does (see the note above).
  RATCHET_DB: "/tmp/ratchet.db",
  RATCHET_DATA_DIR: "/tmp/ratchet-data",
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
    headers.set("authorization", `Bearer ${clean(env.DEMO_API_KEY)}`);
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
