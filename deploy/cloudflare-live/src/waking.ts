// The page shown while the demo container wakes up. Self-contained (no external files), light and dark, reduced-motion safe.
// It asks /__ready every two seconds and reloads the same address when the demo answers.
export const WAKING_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex" />
<title>Waking up the Ratchet live demo</title>
<style>
  :root { color-scheme: light dark; --bg:#fafafa; --ink:#111113; --muted:#5d5d66; --line:#e3e3e8; --blue:#2f6bff; --orange:#f08a24; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0b0b0c; --ink:#f3f3f5; --muted:#a0a0aa; --line:#26262b; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:24px; background:var(--bg); color:var(--ink);
         font:16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width:30rem; width:100%; text-align:center; }
  .orbit { position:relative; width:72px; height:72px; margin:0 auto 28px; }
  .orbit i { position:absolute; inset:0; border-radius:50%; border:3px solid var(--line); }
  .orbit i:nth-child(2) { border-color:transparent; border-top-color:var(--blue); animation:spin 1.1s linear infinite; }
  .orbit i:nth-child(3) { inset:14px; border-color:transparent; border-bottom-color:var(--orange); animation:spin 1.7s linear infinite reverse; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .orbit i { animation:none !important; } }
  h1 { margin:0 0 8px; font-size:1.55rem; letter-spacing:-0.02em; }
  p { margin:0 0 14px; color:var(--muted); }
  .time { font-variant-numeric:tabular-nums; color:var(--ink); font-weight:600; }
  .note { margin-top:26px; padding-top:18px; border-top:1px solid var(--line); font-size:.9rem; }
  a { color:var(--blue); text-underline-offset:3px; }
  #slow { display:none; color:var(--ink); }
</style>
</head>
<body>
<main>
  <div class="orbit" aria-hidden="true"><i></i><i></i><i></i></div>
  <h1>Waking up the live demo</h1>
  <p role="status" aria-live="polite">The live demo sleeps when nobody is using it. Starting it takes about a minute. It opens by itself when the server is ready.</p>
  <p>Waiting <span class="time" id="t">0</span> s</p>
  <p id="slow">This is taking longer than usual. It is still starting; if nothing happens after a few more minutes, reload the page.</p>
  <p class="note">This is the live demo: its answers are real calls to Nemotron 3 Ultra on Nebius Token Factory, with daily spending caps. It keeps no history across a restart.<br />
    While you wait: <a href="https://ratchet-docs.pages.dev/" target="_blank" rel="noopener noreferrer">read the docs</a>,
    <a href="https://github.com/harshbhx/ratchet" target="_blank" rel="noopener noreferrer">see the code</a>.</p>
</main>
<script>
  var start = Date.now();
  var t = document.getElementById("t"), slow = document.getElementById("slow");
  setInterval(function () {
    var s = Math.round((Date.now() - start) / 1000);
    t.textContent = s;
    if (s > 150) slow.style.display = "block";
  }, 1000);
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
