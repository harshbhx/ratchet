// The live Worker's container environment and the optional judges' key (deploy/cloudflare-live/src/containerenv.ts). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";

const { containerEnv } = await import("../../deploy/cloudflare-live/src/containerenv.ts");

const DEMO = "demo:daily_requests=300,daily_usd=1.5,rpm=60,concurrency=4";
const JUDGES = "judges:daily_requests=300,daily_usd=1,rpm=30,concurrency=2";
const base = { NEBIUS_API_KEY: "n", DEMO_API_KEY: "demo-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" };

test("without JUDGES_API_KEY the environment is as before", () => {
  for (const extra of [{}, { JUDGES_API_KEY: "" }, { JUDGES_API_KEY: " \r\n" }, { JUDGES_API_KEY: undefined }]) {
    const e = containerEnv({ ...base, ...extra });
    assert.equal(e.RATCHET_API_KEYS, `demo:${base.DEMO_API_KEY}`);
    assert.equal(e.RATCHET_KEY_LIMITS, DEMO);
  }
});

test("with JUDGES_API_KEY both keys and both limits are present, in the gateway's syntax", () => {
  const e = containerEnv({ ...base, JUDGES_API_KEY: "judges-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" });
  assert.equal(e.RATCHET_API_KEYS, `demo:${base.DEMO_API_KEY},judges:judges-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`);
  assert.equal(e.RATCHET_KEY_LIMITS, `${DEMO};${JUDGES}`);
  assert.ok(!("JUDGES_API_KEY" in e));
});

test("whitespace and a trailing line break are trimmed", () => {
  const e = containerEnv({ ...base, DEMO_API_KEY: ` ${base.DEMO_API_KEY}\r\n`, JUDGES_API_KEY: "  judges-key-cccccccccccccccccccccc\n" });
  assert.equal(e.RATCHET_API_KEYS, `demo:${base.DEMO_API_KEY},judges:judges-key-cccccccccccccccccccccc`);
});

test("a dashboard RATCHET_API_KEYS or RATCHET_KEY_LIMITS still wins", () => {
  const e = containerEnv({ ...base, JUDGES_API_KEY: "judges-key-dddddddddddddddddddddd", RATCHET_API_KEYS: " own:own-key-eeeeeeeeeeeeeeeeeeeeeeee\n", RATCHET_KEY_LIMITS: "own:rpm=5" });
  assert.equal(e.RATCHET_API_KEYS, "own:own-key-eeeeeeeeeeeeeeeeeeeeeeee");
  assert.equal(e.RATCHET_KEY_LIMITS, "own:rpm=5");
});
