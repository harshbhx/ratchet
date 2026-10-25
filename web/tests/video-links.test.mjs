// The /video short links outside the story site: the live Worker (deploy/cloudflare-live/src/video.ts, from=live), the simulation
// Worker (deploy/cloudflare/src/video.ts, from=demo) and the docs site (docs-site/public/_redirects, from=docs). Each is a plain 302
// to the video's counting link, answered before the container is touched. The story site's own /video is in worker.test.mjs.
// Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const COUNTER = "https://video.ratchet.dualmindlab.tech/";
const WORKERS = [
  { name: "live", dir: "cloudflare-live", from: "live", container: "getContainer(env.RATCHET_LIVE)" },
  { name: "simulation", dir: "cloudflare", from: "demo", container: "getContainer(env.RATCHET_DEMO)" },
];

for (const w of WORKERS) {
  const mod = await import(`../../deploy/${w.dir}/src/video.ts`);
  const index = readFileSync(new URL(`../../deploy/${w.dir}/src/index.ts`, import.meta.url), "utf8");

  test(`${w.name} Worker: /video and /video/ are a no-store 302 to the counting link, from=${w.from}`, async () => {
    const want = `${COUNTER}?from=${w.from}`;
    assert.equal(mod.VIDEO_URL, want);
    for (const path of ["/video", "/video/"]) {
      const r = mod.videoRedirect(path);
      assert.ok(r, path);
      assert.equal(r.status, 302, path);
      assert.equal(r.headers.get("location"), want, path);
      assert.equal(r.headers.get("cache-control"), "no-store", path);
      assert.equal(await r.text(), "", path);
    }
  });

  test(`${w.name} Worker: any other path is left to the gateway`, () => {
    for (const path of ["/", "/videos", "/video/x", "/v1/video", "/ratchet/stats", "/__ready", "/Video"]) {
      assert.equal(mod.videoRedirect(path), null, path);
    }
  });

  test(`${w.name} Worker: /video is answered first, before the container is reached or woken`, () => {
    const handler = index.slice(index.indexOf("export default"));
    const redirect = handler.indexOf("videoRedirect(url.pathname)");
    assert.ok(redirect > 0, "the fetch handler calls videoRedirect");
    assert.ok(redirect < handler.indexOf(w.container), "before getContainer");
    assert.ok(redirect < handler.indexOf("/__ready"), "before the readiness check");
    assert.match(handler, /const video = videoRedirect\(url\.pathname\);\s*if \(video\) return video;/);
  });
}

test("docs site: _redirects sends /video and /video/ to the counting link, from=docs, as a 302", () => {
  const text = readFileSync(new URL("../../docs-site/public/_redirects", import.meta.url), "utf8");
  const rules = text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"))
    .map((l) => l.split(/\s+/));
  const want = `${COUNTER}?from=docs`;
  assert.deepEqual(rules, [
    ["/video", want, "302"],
    ["/video/", want, "302"],
  ]);
});
