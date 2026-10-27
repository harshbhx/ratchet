// What a visitor gets while the live gateway's container is starting. No imports and no external files: the Worker uses it, and
// web/tests/live-waking.test.mjs runs it under Node.

/** How long a request waits for the container before the visitor is told it is still waking up. */
export const API_WAIT_MS = 25_000;
/** A page that answers within this time is passed straight through; a slower one means the container is waking. */
export const WAKE_AFTER_MS = 2000;
/** Waits between tries of a read while the container starts. */
export const READ_RETRY_MS = 700;

/** The JSON every API caller gets (the Pages proxy sends the same body), which the web app turns into its "waking up" banner. */
export const WARMING_BODY = {
  error: { code: "warming_up", message: "The live gateway is waking up. This takes about 20 seconds." },
  warming_up: true,
};

/** A browser navigation: a GET that asks for HTML. */
export function wantsPage(method: string, accept: string | null): boolean {
  return method === "GET" && (accept ?? "").includes("text/html");
}

/**
 * May the Worker send this request again if the container drops while starting? Reads only, never a paid Scout run
 * (/scout/stream and /scout/run) and never /v1/*. Everything else waits for readiness first and is then sent exactly once.
 */
export function isRetryableRead(method: string, pathname: string): boolean {
  if (method !== "GET" && method !== "HEAD") return false;
  return !/^\/v1\//.test(pathname) && !/^\/scout\/(run|stream)(\/|$)/.test(pathname) && !/^\/ratchet\/demo\//.test(pathname);
}

export function warmingJson(): Response {
  return new Response(JSON.stringify(WARMING_BODY), {
    status: 503,
    headers: { "content-type": "application/json", "retry-after": "5", "cache-control": "no-store" },
  });
}

export function warmingPage(): Response {
  return new Response(WAKING_HTML, {
    status: 503,
    headers: { "content-type": "text/html; charset=utf-8", "retry-after": "5", "cache-control": "no-store" },
  });
}

/** The answer for a request that is still waiting on the container: a splash page for a browser, the JSON for anything else. */
export function warmingFor(method: string, accept: string | null): Response {
  return wantsPage(method, accept) ? warmingPage() : warmingJson();
}

// The page shown while the gateway container wakes up. Self-contained, light and dark, reduced-motion safe.
// It reloads itself every 3 seconds (meta refresh) and sooner when /__ready says the gateway answers.
export const WAKING_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex" />
<meta http-equiv="refresh" content="3" />
<title>Waking up the Ratchet live gateway</title>
<style>
  :root { color-scheme: light dark; --bg:#fbfaf7; --ink:#121212; --muted:#64625d; --line:#e6e3dc; --blue:#2a78d6; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0b0b0c; --ink:#f2f1ec; --muted:#a4a39c; --line:#26262b; --blue:#5b9bec; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:24px; background:var(--bg); color:var(--ink);
         font:16px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width:30rem; width:100%; text-align:center; }
  h1 { margin:0 0 10px; font-size:1.55rem; letter-spacing:-0.02em; }
  p { margin:0 0 14px; color:var(--muted); }
  .bar { position:relative; height:4px; width:min(16rem,80%); margin:26px auto 22px; overflow:hidden; border-radius:999px; background:var(--line); }
  .bar i { position:absolute; inset:0 auto 0 0; width:35%; border-radius:999px; background:var(--blue); animation:slide 1.6s ease-in-out infinite; }
  @keyframes slide { 0% { transform:translateX(-100%); } 100% { transform:translateX(300%); } }
  @media (prefers-reduced-motion: reduce) { .bar i { animation:none; width:100%; opacity:.55; } }
  .note { margin-top:26px; padding-top:18px; border-top:1px solid var(--line); font-size:.9rem; }
  a { color:var(--blue); text-underline-offset:3px; }
</style>
</head>
<body>
<main>
  <h1>Waking up the live gateway</h1>
  <p role="status" aria-live="polite">It sleeps when idle to save cost. This takes about 20 seconds.</p>
  <div class="bar" aria-hidden="true"><i></i></div>
  <p class="note">This page reloads by itself. The live gateway answers with real calls to Nemotron 3 Ultra on Nebius Token Factory, under daily spending caps.<br />
    While you wait: <a href="https://docs.ratchet.dualmindlab.tech/" target="_blank" rel="noopener noreferrer">read the docs</a>,
    <a href="https://github.com/harshbhx/ratchet" target="_blank" rel="noopener noreferrer">see the code</a>.</p>
</main>
<script>
  function check() {
    fetch("/__ready", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (j && j.ready) { location.reload(); } else { setTimeout(check, 2000); } })
      .catch(function () { setTimeout(check, 3000); });
  }
  setTimeout(check, 1500);
</script>
</body>
</html>`;
