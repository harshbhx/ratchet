// "Live" links stay on this site: no click target points at the live gateway's own address, which is kept only as the API base URL in
// curl snippets (it is joined with /v1). `npm test`.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { test } from "node:test";

const SRC = new URL("../src/", import.meta.url);

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = new URL(name + (statSync(new URL(name, dir)).isDirectory() ? "/" : ""), dir);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(ts|tsx|json)$/.test(name)) out.push(p);
  }
  return out;
}

test("no link or button points at the live gateway's absolute address", () => {
  const found = [];
  for (const p of walk(SRC)) {
    const text = readFileSync(p, "utf8");
    if (/href=\{LIVE_URL\}|to=\{LIVE_URL\}|href: LIVE_URL|"href":\s*"https:\/\/live\.ratchet/.test(text)) found.push(p.pathname.split("/src/")[1]);
  }
  assert.deepEqual(found, []);
});

test("LIVE_PATH is /live and LIVE_URL appears only in curl snippets", () => {
  const utils = readFileSync(new URL("lib/utils.ts", SRC), "utf8");
  assert.match(utils, /LIVE_PATH = "\/live"/);
  for (const p of walk(SRC)) {
    const text = readFileSync(p, "utf8");
    if (!/LIVE_URL/.test(text) || p.pathname.endsWith("lib/utils.ts")) continue;
    for (const line of text.split("\n").filter((l) => /LIVE_URL/.test(l) && !/^\s*import /.test(l))) {
      assert.match(line, /curl|\/v1\/|replace\(/i, `${p.pathname}: ${line.trim()}`);
    }
  }
});
