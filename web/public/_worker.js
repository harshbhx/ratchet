// Cloudflare Pages "advanced mode": the static site, plus a proxy that connects its data calls to a gateway.
// The pages call their gateway on the same origin (/ratchet/*, /scout/*, /v1/*, /healthz, /readyz). A static host has nothing behind those
// paths, so the dashboard and the live chapters said "can't reach the gateway". This forwards them to the gateway and serves everything
// else from the static files. Only the paths in _routes.json reach this file at all; the pages themselves never invoke it.
//
// WHICH GATEWAY is a setting, not code: set the variable GATEWAY_ORIGIN (an https address with no path, for example
// https://live.ratchet.dualmindlab.tech) in the Cloudflare dashboard: Workers & Pages, the project, Settings, Variables and Secrets,
// separately for Production and Preview. It takes effect at once, with no push and no deploy. Without it the live gateway is used; set it to
// https://demo.ratchet.dualmindlab.tech to send the dashboard and every other gateway-backed page to the simulation again.
// SCOUT_ORIGIN is a second, separate setting for the paths under /scout/ only (the Scout page's status, examples, stream, recordings
// and judge calls). It defaults to the live gateway, like GATEWAY_ORIGIN, so the whole site is one live surface; set either to the simulation demo to move that part. Everything else (/ratchet/*, /v1/*, /healthz, /readyz) follows GATEWAY_ORIGIN.
// No keys here: the simulation demo is keyless, and a live gateway's key is added by its own Worker.
//
// /video (and /video/) is the demo video's short link: a 302 to its counting link, tagged from=site, answered here before anything
// else (no gateway call, nothing counted here) and never cached, so each visit reaches the counter.
const VIDEO_URL = "https://video.ratchet.dualmindlab.tech/?from=site";

function videoRedirect() {
  return new Response(null, { status: 302, headers: { location: VIDEO_URL, "cache-control": "no-store" } });
}

const DEFAULT_GATEWAY = "https://live.ratchet.dualmindlab.tech";

const DEFAULT_SCOUT = "https://live.ratchet.dualmindlab.tech";
const SCOUT = /^\/scout\//;

const API = [/^\/ratchet\//, /^\/scout\//, /^\/v1\//, /^\/healthz$/, /^\/readyz$/, /^\/livez$/];

/** A plain https origin from a setting, else the fallback (a typo must not take the site down). */
function originOf(raw, fallback) {
  try {
    const u = new URL(typeof raw === "string" ? raw.trim() : "");
    if (u.protocol === "https:") return u.origin;
  } catch {
    /* fall through to the fallback */
  }
  return fallback;
}

/** Where a path goes: /scout/* to SCOUT_ORIGIN, everything else to GATEWAY_ORIGIN. */
function gatewayOf(env, pathname) {
  if (SCOUT.test(pathname)) return originOf(env.SCOUT_ORIGIN, DEFAULT_SCOUT);
  return originOf(env.GATEWAY_ORIGIN, DEFAULT_GATEWAY);
}

/** Waits between tries when the gateway's container is not ready: 0.7 s, 1.5 s, 3 s (so a cold start of about 5 s is hidden). */
const RETRY_DELAYS_MS = [700, 1500, 3000];

/** What the edge or the container runtime says while a container starts or has just dropped. */
const WAKE_TEXT = /container suddenly disconnected|container is not running|container is not ready|network connection lost|connection reset/i;

/** The answer after the tries ran out: the same JSON the live Worker sends, which the web app turns into its "waking up" banner. */
function warmingResponse() {
  return new Response(
    JSON.stringify({ error: { code: "warming_up", message: "The live demo is waking up. This takes about 20 seconds." }, warming_up: true }),
    { status: 503, headers: { "content-type": "application/json", "retry-after": "5", "cache-control": "no-store" } },
  );
}

/**
 * May this request be sent again? Only reads. GET and HEAD, except the two paths that start a paid Scout run (a retry would pay twice),
 * and the demo actions; and three POSTs that change nothing and cost nothing (the judge unlock, the redaction preview, a task check).
 * Never /v1/*, never /scout/run.
 */
function retryable(method, pathname) {
  if (/^\/v1\//.test(pathname) || /^\/scout\/(run|stream)(\/|$)/.test(pathname) || /^\/ratchet\/demo\//.test(pathname)) return false;
  if (method === "GET" || method === "HEAD") return true;
  return method === "POST" && /^(\/scout\/judge|\/ratchet\/redact|\/ratchet\/tasks\/validate)$/.test(pathname);
}

/** Does this answer mean "the container is not ready"? A real gateway error (its own JSON, such as /readyz saying 503) is not that. */
async function notReady(res) {
  if (res.status === 502 || res.status === 504) return true;
  if (res.status < 500) return false;
  let text = "";
  try {
    text = (await res.clone().text()).slice(0, 2000);
  } catch {
    return true;
  }
  if (res.status === 503 && text.trim() === "") return true;
  try {
    const body = JSON.parse(text);
    if (body && typeof body === "object") return body.warming_up === true || WAKE_TEXT.test(JSON.stringify(body.error ?? {}));
  } catch {
    /* not JSON: judge by the text */
  }
  return res.status === 503 || WAKE_TEXT.test(text);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Send the request to the gateway. A success, and any answer that is not "container not ready", goes back untouched. A reading request that
 * got "not ready" (or no answer at all) is sent again up to three times; if it still fails the visitor gets the 503 warming_up JSON.
 * Any other request is sent once, and a "not ready" answer is turned into the same JSON without a retry.
 */
async function forward(request, target, deps = {}) {
  const send = deps.fetch ?? fetch;
  const sleep = deps.sleep ?? wait;
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("accept-encoding"); // the edge compresses the reply for the browser; no double handling here
  const ip = request.headers.get("cf-connecting-ip");
  if (ip) headers.set("x-forwarded-for", ip);
  const canRetry = retryable(request.method, url.pathname);
  let body;
  if (request.method !== "GET" && request.method !== "HEAD") body = canRetry ? await request.arrayBuffer() : request.body;
  const attempt = () => send(new Request(target, { method: request.method, headers, redirect: "manual", ...(body === undefined ? {} : { body, duplex: "half" }) }));
  const tries = canRetry ? RETRY_DELAYS_MS.length + 1 : 1;
  for (let i = 0; i < tries; i++) {
    if (i > 0) await sleep(RETRY_DELAYS_MS[i - 1]);
    let res;
    try {
      res = await attempt();
    } catch (e) {
      if (!canRetry) return warmingResponse();
      continue;
    }
    if (!(await notReady(res))) return res;
  }
  return warmingResponse();
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/video" || url.pathname === "/video/") return videoRedirect();
    if (!API.some((re) => re.test(url.pathname))) return env.ASSETS.fetch(request);
    const target = new URL(url.pathname + url.search, gatewayOf(env, url.pathname));
    return forward(request, target);
  },
  // Exposed for tests/worker.test.mjs only; Cloudflare ignores other keys.
  _test: { forward, retryable, notReady, RETRY_DELAYS_MS, VIDEO_URL },
};
