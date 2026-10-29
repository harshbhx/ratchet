// A judge's own key on the live site (src/lib/judge-key-model.ts, src/pages/landing/sections/live-request.tsx). Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const m = await import("../src/lib/judge-key-model.ts");

const fake = () => {
  const data = new Map();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) };
};
const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
const KEY = "judge-key-aaaaaaaaaaaaaaaaaaaaaaaa";

test("a key goes to sessionStorage by default and to localStorage only when remembered", () => {
  const s = fake(), l = fake();
  m.saveKey(` ${KEY}\n`, false, s, l);
  assert.equal(s.data.get(m.KEY_STORAGE), KEY);
  assert.equal(l.data.size, 0);
  assert.equal(m.isRemembered(l), false);
  m.saveKey(KEY, true, s, l);
  assert.equal(l.data.get(m.KEY_STORAGE), KEY);
  assert.equal(s.data.size, 0, "never left in both");
  assert.equal(m.isRemembered(l), true);
  assert.equal(m.loadKey(s, l), KEY);
  assert.equal(m.loadKey(fake(), fake()), "");
});

test("Forget key clears both storages, and every storage call survives a throwing store", () => {
  const s = fake(), l = fake();
  s.setItem(m.KEY_STORAGE, KEY);
  l.setItem(m.KEY_STORAGE, KEY);
  m.forgetKey(s, l);
  assert.equal(s.data.size + l.data.size, 0);
  assert.equal(m.loadKey(s, l), "");
  assert.doesNotThrow(() => {
    m.saveKey(KEY, true, throwing, throwing);
    m.forgetKey(throwing, throwing);
    m.forgetKey(null, null);
  });
  assert.equal(m.loadKey(throwing, throwing), "");
  assert.equal(m.isRemembered(throwing), false);
});

test("the key travels as an Authorization header, trimmed, and never otherwise", () => {
  assert.deepEqual(m.keyHeaders(` ${KEY}\r\n`), { authorization: `Bearer ${KEY}` });
  assert.deepEqual(m.keyHeaders(""), {});
  assert.deepEqual(m.keyHeaders("  "), {});
});

test("401 and 429 say the right thing; the 429 is the gateway's own message", () => {
  assert.equal(m.keyErrorText(401, null), "That key wasn't accepted. Check it or forget it.");
  const msg = "daily request quota of 300 used up for 'judges'; resets at 00:00 UTC";
  assert.equal(m.keyErrorText(429, { error: { message: msg } }), msg);
  assert.match(m.keyErrorText(429, null), /reached its limit/);
  assert.equal(m.keyErrorText(500, null), null);
  assert.equal(m.keyErrorText(403, null), null);
});

test("the remaining count comes only from the gateway's header", () => {
  const h = (v) => ({ get: (n) => (n === "x-ratelimit-remaining-requests" ? v : null) });
  assert.equal(m.remainingFromHeaders(h("287")), 287);
  assert.equal(m.remainingFromHeaders(h("0")), 0);
  assert.equal(m.remainingFromHeaders(h(null)), null);
  assert.equal(m.remainingFromHeaders(h("many")), null);
  assert.equal(m.leftText(287), "287 left today");
  assert.equal(m.leftText(null), null);
});

test("the request box skips the human check with a key, and the key is never in an address, a log or the page", () => {
  const box = readFileSync(new URL("../src/pages/landing/sections/live-request.tsx", import.meta.url), "utf8");
  assert.match(box, /key \? \{ go: true as const, token: null \} : await human\.gate\(\)/);
  assert.match(box, /\.\.\.\(key \? keyHeaders\(key\) : tokenHeaders\(gate\.token\)\)/);
  assert.match(box, /<JudgeKey left=\{left\} \/>/);
  assert.match(box, /<WhoBadge/, "the Answered by badge stays");
  for (const file of ["../src/pages/landing/sections/live-request.tsx", "../src/components/judge-key.tsx", "../src/lib/use-judge-key.ts", "../src/lib/judge-key-model.ts"]) {
    const src = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(src, /console\./, `${file} logs nothing`);
    assert.doesNotMatch(src, /URLSearchParams|searchParams|location\.(search|href|hash)|\?key=|EventSource/, `${file} never puts a key in an address`);
  }
  const ui = readFileSync(new URL("../src/components/judge-key.tsx", import.meta.url), "utf8");
  assert.match(ui, /type=\{shown \? "text" : "password"\}/);
  assert.match(ui, /autoComplete="off"/);
  assert.match(ui, /Have a judges' key\?/);
  assert.match(ui, /Remember on this device/);
  assert.match(ui, /Forget key/);
  assert.match(ui, /Use key/);
});

test("Scout does not take the key: it stays on the human check", () => {
  const scout = readFileSync(new URL("../src/pages/scout/index.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(scout, /judge-key|JudgeKey/);
  assert.match(scout, /useHuman\(true\)/);
});
