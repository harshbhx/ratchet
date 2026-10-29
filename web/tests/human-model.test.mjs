// The inline human check under a button: its state machine and wording (src/lib/human-model.ts), that its widget is never positioned on its own,
// and that a Scout run's first token is used once. Pure helpers; run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/human-model.ts");
const stream = await import("../src/lib/scout-stream.ts");

const run = (events, from = m.initialHuman) => events.reduce((s, e) => m.reduceHuman(s, e), from);

test("a quiet pass: idle, checking, verified, and the row says so", () => {
  assert.deepEqual(m.initialHuman, { phase: "idle" });
  assert.equal(m.rowOpen(m.initialHuman), false, "nothing is shown until a check starts");
  const checking = run([{ type: "start" }]);
  assert.equal(checking.phase, "checking");
  assert.equal(m.humanText(checking, false), "Checking you're human");
  assert.equal(m.rowOpen(checking), true);
  assert.equal(m.widgetShown(checking), false, "invisible while it passes by itself");
  const verified = run([{ type: "passed" }], checking);
  assert.equal(m.humanText(verified, false), "Verified for this visit");
  assert.equal(m.humanText(run([{ type: "reset" }], verified), false), null, "a later click shows nothing at all");
});

test("a check that needs a click opens the widget in the same spot with one short line", () => {
  const s = run([{ type: "start" }, { type: "interactive" }]);
  assert.equal(s.phase, "interactive");
  assert.equal(m.widgetShown(s), true);
  assert.equal(m.humanText(s, false), "One quick check, then you're set for this visit.");
  assert.equal(run([{ type: "passed" }], s).phase, "verified");
  // A late callback after the check ended changes nothing.
  assert.equal(run([{ type: "interactive" }], { phase: "verified" }).phase, "verified");
  assert.equal(run([{ type: "interactive" }]).phase, "idle");
});

test("failures: a friendly line with Try again, blocked with none, no raw codes", () => {
  const failed = run([{ type: "start" }, { type: "failed" }]);
  assert.deepEqual(failed, { phase: "error", reason: "failed" });
  assert.equal(m.humanText(failed, false), "Couldn't confirm you're human. Try again.");
  assert.equal(m.canRetry(failed), true);
  const blocked = run([{ type: "blocked" }]);
  assert.equal(m.humanText(blocked, false), "Too many failed checks from your network today. It resets at 00:00 UTC.");
  assert.equal(m.canRetry(blocked), false);
  // Try again starts a fresh check.
  assert.equal(run([{ type: "start" }], failed).phase, "checking");
  for (const s of [failed, blocked]) assert.doesNotMatch(m.humanText(s, false), /human_check|403|429|error/i);
});

test("a check that did not load: /v1 goes on as a plain keyless call, Scout stops and says it needs the check", () => {
  const skipped = run([{ type: "unavailable", required: false }]);
  assert.equal(skipped.phase, "skipped");
  assert.equal(m.humanText(skipped, false), "The human check didn't load (an ad blocker?). Requests still work, a few a day.");
  const scout = run([{ type: "unavailable", required: true }]);
  assert.deepEqual(scout, { phase: "error", reason: "unavailable" });
  assert.match(m.humanText(scout, true), /^Scout needs the human check/);
  assert.equal(m.canRetry(scout), true);
});

test("the Worker's refusals are read as failed or blocked; any other refusal is not about the check", () => {
  assert.equal(m.failureOf(403, "human_check_failed"), "failed");
  assert.equal(m.failureOf(403, "human_check_required"), "failed");
  assert.equal(m.failureOf(429, "human_check_blocked"), "blocked");
  assert.equal(m.failureOf(403, undefined, "Human check required. Open the site in a browser"), "failed");
  assert.equal(m.failureOf(429, undefined, "Too many failed human checks from your address today."), "blocked");
  assert.equal(m.failureOf(429, "daily_limit_reached"), null);
  assert.equal(m.failureOf(429, "curl_allowance_used"), null);
  assert.equal(m.failureOf(403, "something_else"), null);
  assert.equal(m.failureOf(200, "human_check_failed"), null);
  assert.equal(m.failureOfMessage("Human check failed. Refresh the page and try again."), "failed");
  assert.equal(m.failureOfMessage("Too many failed human checks from your address today. It resets at 00:00 UTC."), "blocked");
  assert.equal(m.failureOfMessage("Daily limit reached: 3 Scout runs per visitor per day."), null);
});

test("the widget and its row are never positioned on their own: no fixed or absolute container anywhere", () => {
  for (const file of ["../src/lib/turnstile.ts", "../src/components/human-row.tsx", "../src/lib/use-human.ts"]) {
    const src = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(src.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ""), /position\s*:|\bfixed\b|\babsolute\b|cssText|appendChild\(\s*host|createElement\("div"\)/, file);
  }
  const row = readFileSync(new URL("../src/components/human-row.tsx", import.meta.url), "utf8");
  assert.match(row, /data-companion-avoid/);
  assert.match(row, /aria-live="polite"/);
  assert.match(row, /motion-reduce:transition-none/);
  const t = readFileSync(new URL("../src/lib/turnstile.ts", import.meta.url), "utf8");
  assert.match(t, /size: "flexible"/);
  assert.match(t, /appearance: "interaction-only"/);
  assert.match(t, /theme: siteTheme\(\)/);
  // Both surfaces that spend money have their own inline row.
  assert.match(readFileSync(new URL("../src/pages/landing/sections/live-request.tsx", import.meta.url), "utf8"), /<HumanRow human=\{human\}/);
  assert.match(readFileSync(new URL("../src/pages/scout/index.tsx", import.meta.url), "utf8"), /<HumanRow human=\{human\}/);
});

test("a Scout run uses the token the inline check gave only once; a retry asks for another", async () => {
  const opened = [];
  const makeSource = (url) => {
    const s = { url, closed: false, onmessage: null, onerror: null, close() { this.closed = true; } };
    opened.push(s);
    return s;
  };
  let asked = 0;
  const ran = [];
  const finished = new Promise((resolve) => {
    stream.streamScoutWith("q", (e) => e.type === "done" && resolve(), {
      first: "first-token",
      token: async () => `next-${++asked}`,
      makeSource,
      run: async (q, tk) => (ran.push(tk), { events: [{ type: "done", run_id: "r", answer: "a", citations: [], totals: {} }] }),
      pause: async () => undefined,
    });
  });
  await new Promise((r) => setTimeout(r, 0));
  assert.match(opened[0].url, /cf-turnstile-response=first-token/);
  assert.equal(asked, 0, "the first attempt did not ask for another");
  opened[0].onerror();
  await finished;
  assert.deepEqual(ran, ["next-1"]);
  assert.equal(opened.length, 1);
  // Verified for the visit: a null first token sends no token parameter (the cookie rides along).
  const second = [];
  stream.streamScoutWith("q", () => undefined, { first: null, token: async () => "x", makeSource: (u) => (second.push(u), { onmessage: null, onerror: null, close() {} }), run: async () => ({ events: [] }), pause: async () => undefined });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(second[0], "/scout/stream?q=q");
});
