// The video link's deploy settings: the Wrangler config and the deploy workflow. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const config = JSON.parse(readFileSync(new URL("../wrangler.json", import.meta.url), "utf8"));
const workflow = readFileSync(new URL("../../../.github/workflows/deploy-cloudflare-video.yml", import.meta.url), "utf8");
const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const livePkg = JSON.parse(readFileSync(new URL("../../cloudflare-live/package.json", import.meta.url), "utf8"));

test("served only at video.ratchet.dualmindlab.tech, a custom domain, with no workers.dev or preview address", () => {
  assert.equal(config.name, "ratchet-video");
  assert.deepEqual(config.routes, [{ pattern: "video.ratchet.dualmindlab.tech", custom_domain: true }]);
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
});

test("one D1 database with its migrations, a 03:30 UTC cron, and no stored request logs", () => {
  assert.equal(config.d1_databases.length, 1);
  const [d1] = config.d1_databases;
  assert.equal(d1.binding, "DB");
  assert.equal(d1.database_name, "ratchet-video");
  assert.match(d1.database_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  assert.equal(d1.migrations_dir, "migrations");
  assert.deepEqual(config.triggers, { crons: ["30 3 * * *"] });
  // Workers Logs would keep each request's details; the Worker's own failure lines are seen with `wrangler tail`.
  assert.equal(config.observability.enabled, false);
});

test("email is a plain on/off switch in the config; the stats key is a secret, never a variable", () => {
  // "1" sends; "0", or the variable missing, sends nothing (worker.test.mjs checks the Worker's side).
  assert.ok(["0", "1"].includes(config.vars.EMAIL_ENABLED), config.vars.EMAIL_ENABLED);
  assert.deepEqual(config.send_email, [{ name: "EMAIL" }]);
  assert.match(config.vars.EMAIL_FROM, /@mail\.dualmindlab\.tech$/);
  assert.ok(!("STATS_KEY" in config.vars));
  assert.ok(!("keep_vars" in config) || config.keep_vars === false);
});

test("the same Wrangler version as the other Cloudflare packages, pinned exactly", () => {
  assert.equal(pkg.devDependencies.wrangler, livePkg.devDependencies.wrangler);
  for (const [name, version] of Object.entries(pkg.devDependencies)) assert.match(version, /^\d+\.\d+\.\d+$/, name);
});

test("the deploy workflow is manual, confirmed, applies migrations, and its smoke never counts a click", () => {
  assert.match(workflow, /workflow_dispatch:/);
  assert.doesNotMatch(workflow, /^\s*(push|pull_request|schedule):/m);
  assert.match(workflow, /inputs\.confirm == 'deploy-video'/);
  assert.match(workflow, /wrangler d1 migrations apply ratchet-video --remote/);
  assert.match(workflow, /CLOUDFLARE_API_TOKEN: \$\{\{ secrets\.CLOUDFLARE_API_TOKEN \}\}/);
  // Every request the smoke makes to the counted address is a HEAD; a GET goes only to /stats, which is never counted.
  const calls = [...workflow.matchAll(/curl [^\n]*video\.ratchet\.dualmindlab\.tech[^\n]*/g)].map((m) => m[0]);
  assert.ok(calls.length >= 2, "the smoke calls the link");
  for (const call of calls) {
    if (/\/stats/.test(call)) continue;
    assert.match(call, /--head\b/, call);
  }
  assert.match(workflow, /ratchet-video-smoke\/1/);
});
