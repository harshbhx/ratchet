// The Ratchet demo as a Cloudflare Container: the repo's mock-mode image, no keys, no secrets.
// On demand: nothing calls it in the background. The first visitor starts it (about a minute while the mock warm-up runs) and it
// goes back to sleep 20 minutes after the last request, so it costs nothing while nobody is looking.
// While it wakes, a page that opens the demo itself when it is ready replaces the blank wait.
import { Container, getContainer } from "@cloudflare/containers";
import { WAKING_HTML } from "./waking";

export class RatchetDemo extends Container {
  defaultPort = 8000;
  sleepAfter = "20m"; // sleeps after 20 minutes without a request
  envVars = {
    RATCHET_MODE: "mock",
    RATCHET_MOCK_WARMUP_DAYS: "2",
    RATCHET_DEMO_RPM: "30",
    RATCHET_DEMO_MAX_DAYS: "30",
    RATCHET_DEMO_MAX_REQUESTS: "200000",
    RATCHET_DEMO_IDLE_RESET_S: "7200",
    RATCHET_IP_RPM: "60",
    RATCHET_MAX_INFLIGHT: "8",
    RATCHET_STATS_TTL_S: "2",
  };
}

interface Env {
  RATCHET_DEMO: DurableObjectNamespace<RatchetDemo>;
}

/** A page that answers within this time is passed straight through; a slower one means the container is waking. */
const WAKE_AFTER_MS = 2000;
/** How long the readiness check waits for one answer. */
const READY_CHECK_MS = 2500;

const sleep = (ms: number) => new Promise<null>((resolve) => setTimeout(() => resolve(null), ms));

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const demo = getContainer(env.RATCHET_DEMO);

    // The waiting page asks this every two seconds. It also keeps the start going if the visitor's own request has ended.
    if (url.pathname === "/__ready") {
      const answer = demo.fetch(new Request(new URL("/healthz", url.origin)));
      ctx.waitUntil(answer.then(() => undefined).catch(() => undefined));
      const ok = await Promise.race([answer.then((r) => r.ok).catch(() => false), sleep(READY_CHECK_MS).then(() => false)]);
      return Response.json({ ready: ok === true }, { headers: { "cache-control": "no-store" } });
    }

    const wantsPage = request.method === "GET" && (request.headers.get("accept") ?? "").includes("text/html");
    const pending = demo.fetch(request); // starts the container if it is asleep
    if (!wantsPage) return pending; // scripts and API calls simply wait

    const first = await Promise.race([pending.catch(() => null), sleep(WAKE_AFTER_MS)]);
    if (first) return first;
    ctx.waitUntil(pending.then(() => undefined).catch(() => undefined)); // keep waking it
    return new Response(WAKING_HTML, {
      status: 200,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    });
  },
};
