// The video link's deploy settings (the Wrangler config, the deploy workflow) and every link to it in the repository. Run with `npm test`.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./hooks.mjs", import.meta.url);
const { SOURCES } = await import("../src/index.ts");
const ROOT = new URL("../../../", import.meta.url);

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

test("the deploy workflow is manual, confirmed, applies migrations, and its smoke never counts a human", () => {
  assert.match(workflow, /workflow_dispatch:/);
  assert.doesNotMatch(workflow, /^\s*(push|pull_request|schedule):/m);
  assert.match(workflow, /inputs\.confirm == 'deploy-video'/);
  assert.match(workflow, /wrangler d1 migrations apply ratchet-video --remote/);
  assert.match(workflow, /CLOUDFLARE_API_TOKEN: \$\{\{ secrets\.CLOUDFLARE_API_TOKEN \}\}/);
  // Every request the smoke makes to the counted address is a HEAD; a GET goes only to /stats, which is never counted, and a HEAD only ever counts as a bot.
  const calls = [...workflow.matchAll(/curl [^\n]*video\.ratchet\.dualmindlab\.tech[^\n]*/g)].map((m) => m[0]);
  assert.ok(calls.length >= 2, "the smoke calls the link");
  for (const call of calls) {
    if (/\/stats/.test(call)) continue;
    assert.match(call, /--head\b/, call);
  }
  assert.match(workflow, /ratchet-video-smoke\/1/);
});

/** Every file under `dir` (relative to the repository) whose name matches `re`, without node_modules or build output. */
function files(dir, re) {
  let names;
  try {
    names = readdirSync(new URL(dir, ROOT), { recursive: true });
  } catch {
    return [];
  }
  return names
    .map((n) => String(n).replaceAll("\\", "/"))
    .filter((n) => re.test(n) && !/(^|\/)(node_modules|dist|\.astro)\//.test(n))
    .map((n) => `${dir}${n}`);
}

test("every link to the counter in the repository names a source it counts, and each entry point has its own", () => {
  const scanned = [
    "README.md",
    "web/public/_worker.js",
    "docs-site/public/_redirects",
    ...files("docs/", /\.md$/),
    ...files("docs-site/src/content/", /\.mdx?$/),
    ...files("web/src/", /\.(ts|tsx|json)$/),
    ...files("deploy/cloudflare-live/src/", /\.ts$/),
    ...files("deploy/cloudflare/src/", /\.ts$/),
  ];
  const found = new Map();
  for (const rel of scanned) {
    const text = readFileSync(new URL(rel, ROOT), "utf8");
    const tags = [
      ...[...text.matchAll(/video\.ratchet\.dualmindlab\.tech\/\?from=([A-Za-z0-9_-]+)/g)].map((m) => m[1]),
      ...[...text.matchAll(/videoHref\([^,()]+,\s*"([^"]+)"\)/g)].map((m) => m[1]),
    ];
    for (const tag of tags) {
      assert.ok(SOURCES.includes(tag), `${rel}: ?from=${tag} is not in the counter's allowlist (it would count as "other")`);
      found.set(tag, [...(found.get(tag) ?? []), rel]);
    }
  }
  const has = (tag, rel) => (found.get(tag) ?? []).includes(rel);
  assert.ok(has("site", "web/public/_worker.js"), "the story site's /video");
  assert.ok(has("live", "deploy/cloudflare-live/src/video.ts"), "the live demo's /video");
  assert.ok(has("demo", "deploy/cloudflare/src/video.ts"), "the simulation's /video");
  assert.ok(has("docs", "docs-site/public/_redirects"), "the docs site's /video");
  assert.ok(has("readme", "README.md"), "the README's video links");
});
