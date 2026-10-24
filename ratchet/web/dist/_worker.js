// Cloudflare Pages "advanced mode": the static site, plus a proxy that connects its data calls to a gateway.
// The pages call their gateway on the same origin (/ratchet/*, /scout/*, /v1/*, /healthz, /readyz). A static host has nothing behind those
// paths, so the dashboard and the live chapters said "can't reach the gateway". This forwards them to the gateway and serves everything
// else from the static files. Only the paths in _routes.json reach this file at all; the pages themselves never invoke it.
//
// WHICH GATEWAY is a setting, not code: set the variable GATEWAY_ORIGIN (an https address with no path, for example
// https://live.ratchet.dualmindlab.tech) in the Cloudflare dashboard: Workers & Pages, the project, Settings, Variables and Secrets,
// separately for Production and Preview. It takes effect at once, with no push and no deploy. Without it the simulation demo is used.
// SCOUT_ORIGIN is a second, separate setting for the paths under /scout/ only (the Scout page's status, examples, stream, recordings
// and judge calls). It defaults to the live gateway, so Scout on this site is real; set it to the same value as GATEWAY_ORIGIN (or to
// the simulation demo) to send Scout where everything else goes. Everything else (/ratchet/*, /v1/*, /healthz, /readyz) follows GATEWAY_ORIGIN.
// No keys here: the simulation demo is keyless, and a live gateway's key is added by its own Worker.
const DEFAULT_GATEWAY = "https://demo.ratchet.dualmindlab.tech";

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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!API.some((re) => re.test(url.pathname))) return env.ASSETS.fetch(request);
    const target = new URL(url.pathname + url.search, gatewayOf(env, url.pathname));
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("accept-encoding"); // the edge compresses the reply for the browser; no double handling here
    const ip = request.headers.get("cf-connecting-ip");
    if (ip) headers.set("x-forwarded-for", ip);
    const init = { method: request.method, headers, redirect: "manual" };
    if (request.method !== "GET" && request.method !== "HEAD") init.body = request.body;
    return fetch(new Request(target, init));
  },
};
