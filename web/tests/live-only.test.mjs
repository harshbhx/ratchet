// The site shows only what is real and live: no "simulation", "simulated" or "demo" wording in what a visitor reads, and
// none of the simulation or recorded-fallback files left in the source. Code identifiers and API field names are allowed
// (demo_split, RATCHET_DEMO_SPLIT, /ratchet/demo/ paths, the `simulated` prop that marks a mock model). `npm test`.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { test } from "node:test";

const SRC = new URL("../src/", import.meta.url);

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = new URL(name + (statSync(new URL(name, dir)).isDirectory() ? "/" : ""), dir);
    if (name.endsWith("/")) continue;
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(ts|tsx|json|css)$/.test(name)) out.push(p);
  }
  return out;
}

/** Identifiers and ids that may keep the words: they are never printed as prose. */
const ALLOWED = [
  /demo_split|demo_reset|demoSplit\w*|DemoSplit\w*|DEMO_SPLIT\w*|demoSavings|DemoSavings|DemoInfo|demoNote/g,
  /RATCHET_DEMO_\w+/g,
  /\/ratchet\/demo\/[\w/{}-]*/g,
  /data-demo-split/g,
  /\blive-demo\b/g,
  /\bsimulated(?=\s*[?:=,}]|\s*\?\?)|\.simulated\b|\bSIMULATED\b/g,
  /^\s*demo\??:\s*;?\s*$|\|demo\)/gm,
  /\bstats\??\.demo\b|\bdemo\??\.(controls|shared|max_days|max_requests|idle_reset_s)\b/g,
  /\bsurface: "live" \| "simulation" \| string;/g,
  /\blinks\??\.sim\b|\bsim: string \| null;/g,
];

/** The source with comments and allowed identifiers removed, one entry per line that still has a banned word. */
function visibleHits(text) {
  let s = text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " ")).replace(/(^|[\s;{(,])\/\/[^\n]*/g, "$1");
  for (const re of ALLOWED) s = s.replace(re, "");
  return s
    .split("\n")
    .map((line, i) => ({ line: line.trim(), n: i + 1 }))
    .filter((l) => /simulat|demo/i.test(l.line));
}

test("no simulation or demo wording in the web source's visible text", () => {
  const found = [];
  for (const p of walk(SRC)) {
    for (const h of visibleHits(readFileSync(p, "utf8"))) found.push(`${p.pathname.split("/src/")[1]}:${h.n}: ${h.line.slice(0, 140)}`);
  }
  assert.deepEqual(found, []);
});

test("the simulation and recorded-fallback files are gone", () => {
  for (const f of [
    "lib/sim.ts",
    "lib/recording-model.ts",
    "data/recorded-tries.json",
    "pages/landing/recorded-tries.ts",
    "pages/landing/sections/rollback-story.tsx",
    "pages/landing/sections/cost-chart.tsx",
    "pages/dashboard/controls.tsx",
    "pages/dashboard/pipeline-link.tsx",
  ]) {
    assert.equal(existsSync(new URL(f, SRC)), false, f);
  }
});

test("no link to the simulation site is left", () => {
  for (const p of walk(SRC)) assert.doesNotMatch(readFileSync(p, "utf8"), /demo\.ratchet\.dualmindlab\.tech|sim\.ratchet/, p.pathname);
});
