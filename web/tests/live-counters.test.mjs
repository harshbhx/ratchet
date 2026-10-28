// The live Worker's running totals (deploy/cloudflare-live/src/counters.ts): they add up across container restarts without counting
// anything twice, hold only counts and dollars, and say "nothing recorded" instead of zeros. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const c = await import("../../deploy/cloudflare-live/src/counters.ts");

/** An in-memory stand-in for Durable Object storage. */
const memory = () => {
  const m = new Map();
  return { get: async (k) => m.get(k), put: async (k, v) => void m.set(k, structuredClone(v)), raw: m };
};
const snap = (instance, over = {}) => ({
  instance,
  values: { ...c.emptyTotals(), ...over },
});
const json = (instance, over = {}) => ({ instance, ...snap(instance, over).values });
const DAY = Date.UTC(2026, 9, 29, 10, 0, 0);

test("nothing recorded yet: the view says so instead of showing zeros as a measurement", async () => {
  const v = await c.readView(memory());
  assert.equal(v.recorded, false);
  assert.equal(v.since, null);
  assert.equal(v.instances, 0);
});

test("the first snapshot sets `since` to its UTC day and counts in full", async () => {
  const s = memory();
  const v = await c.recordSnapshot(s, snap("a1b2c3d4e5f6", { requests: 5, requests_teacher: 5, cost_usd: 0.0123 }), DAY);
  assert.equal(v.recorded, true);
  assert.equal(v.since, "2026-10-29");
  assert.equal(v.totals.requests, 5);
  assert.equal(v.totals.cost_usd, 0.0123);
  assert.equal(v.instances, 1);
});

test("the same snapshot twice counts once; a later one of the same instance adds the difference", async () => {
  const s = memory();
  await c.recordSnapshot(s, snap("a1b2c3d4e5f6", { requests: 5 }), DAY);
  await c.recordSnapshot(s, snap("a1b2c3d4e5f6", { requests: 5 }), DAY + 1000);
  assert.equal((await c.readView(s)).totals.requests, 5);
  await c.recordSnapshot(s, snap("a1b2c3d4e5f6", { requests: 9, errors: 1 }), DAY + 2000);
  const v = await c.readView(s);
  assert.equal(v.totals.requests, 9);
  assert.equal(v.totals.errors, 1);
  assert.equal(v.since, "2026-10-29", "since never moves");
});

test("a restart is a new instance: its counters start from zero and add on top, nothing is lost or counted twice", async () => {
  const s = memory();
  await c.recordSnapshot(s, snap("aaaaaaaa", { requests: 10, cost_usd: 0.5, scout_runs: 2 }), DAY);
  await c.recordSnapshot(s, snap("bbbbbbbb", { requests: 3, cost_usd: 0.1, scout_runs: 1 }), DAY + 5000);
  await c.recordSnapshot(s, snap("bbbbbbbb", { requests: 4, cost_usd: 0.12, scout_runs: 1 }), DAY + 6000);
  const v = await c.readView(s);
  assert.equal(v.totals.requests, 14);
  assert.equal(v.totals.scout_runs, 3);
  assert.equal(v.totals.cost_usd, 0.62);
  assert.equal(v.instances, 2);
});

test("a counter that goes down inside one instance never subtracts, and the next rise counts from the highest reading", async () => {
  const s = memory();
  await c.recordSnapshot(s, snap("aaaaaaaa", { errors: 5 }), DAY);
  await c.recordSnapshot(s, snap("aaaaaaaa", { errors: 3 }), DAY + 1);
  assert.equal((await c.readView(s)).totals.errors, 5);
  await c.recordSnapshot(s, snap("aaaaaaaa", { errors: 6 }), DAY + 2);
  assert.equal((await c.readView(s)).totals.errors, 6);
});

test("a float sum does not drift into digits nobody measured", async () => {
  const s = memory();
  for (let i = 1; i <= 50; i++) await c.recordSnapshot(s, snap("aaaaaaaa", { cost_usd: i * 0.0001 }), DAY + i);
  assert.equal((await c.readView(s)).totals.cost_usd, 0.005);
});

test("parseSnapshot keeps only the nine counters and rejects anything else", () => {
  const ok = c.parseSnapshot({ ...json("0123456789abcdef"), mode: "live", started: 1, prompt: "hello", ip: "203.0.113.7" });
  assert.deepEqual(Object.keys(ok).sort(), ["instance", "values"]);
  assert.deepEqual(Object.keys(ok.values).sort(), [...c.FIELDS].sort());
  assert.equal(c.FIELDS.length, 9);
  assert.equal(c.parseSnapshot({ ...json("0123456789abcdef"), requests: -1 }), null);
  assert.equal(c.parseSnapshot({ ...json("0123456789abcdef"), requests: "5" }), null);
  assert.equal(c.parseSnapshot({ ...json("not hex!") }), null);
  assert.equal(c.parseSnapshot({ detail: "warming up" }), null);
  assert.equal(c.parseSnapshot(null), null);
  const { errors, ...missing } = json("0123456789abcdef");
  assert.equal(c.parseSnapshot(missing), null, `a snapshot without ${errors === 0 ? "errors" : "a field"} is not one`);
});

test("what is stored and what is shown carry no text, address or client", async () => {
  const s = memory();
  const dirty = c.parseSnapshot({ ...json("0123456789abcdef", { requests: 2 }), prompt: "secret question", client: "acme", ip: "203.0.113.7" });
  await c.recordSnapshot(s, dirty, DAY);
  const stored = JSON.stringify([...s.raw.values()]);
  for (const word of ["secret question", "acme", "203.0.113.7"]) assert.ok(!stored.includes(word), word);
  const shown = await c.readView(s);
  assert.deepEqual(Object.keys(shown).sort(), ["instances", "recorded", "since", "totals", "updated"]);
  assert.ok(!JSON.stringify(shown).includes("0123456789abcdef"), "the instance id is not shown");
});

test("a damaged stored state is read as empty, not as a crash or as numbers", async () => {
  const s = memory();
  await s.put("state", { totals: { requests: "lots" }, since: "yesterday", last: { instance: 5 } });
  const v = await c.readView(s);
  assert.equal(v.recorded, false);
  assert.equal(v.totals.requests, 0);
});

test("claimPull lets one pull through per gap", async () => {
  const s = memory();
  assert.equal(await c.claimPull(s, 3000, 1000), true);
  assert.equal(await c.claimPull(s, 3000, 2000), false);
  assert.equal(await c.claimPull(s, 3000, 4500), true);
});

test("paid calls and streamed runs are the ones the totals catch up after", () => {
  assert.equal(c.isPaidCall("POST", "/v1/chat/completions"), true);
  assert.equal(c.isPaidCall("POST", "/scout/run"), true);
  assert.equal(c.isPaidCall("GET", "/ratchet/stats"), false);
  assert.equal(c.isStreamedRun("GET", "/scout/stream"), true);
  assert.equal(c.isStreamedRun("GET", "/scout/status"), false);
});

test("the Worker wires it: a v3 migration, the binding, the path answered without waking the container", () => {
  const w = JSON.parse(readFileSync(new URL("../../deploy/cloudflare-live/wrangler.json", import.meta.url), "utf8"));
  assert.ok(w.durable_objects.bindings.some((b) => b.name === "LIVE_COUNTERS" && b.class_name === "LiveCounters"));
  const m = w.migrations.find((x) => x.new_sqlite_classes?.includes("LiveCounters"));
  assert.ok(m, "LiveCounters has a migration");
  assert.equal(new Set(w.migrations.map((x) => x.tag)).size, w.migrations.length, "migration tags are unique");
  const src = readFileSync(new URL("../../deploy/cloudflare-live/src/index.ts", import.meta.url), "utf8");
  assert.ok(src.includes("export class LiveCounters"));
  assert.ok(src.includes("PERSISTENT_PATH"));
});
