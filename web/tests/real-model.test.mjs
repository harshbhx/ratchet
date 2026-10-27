// "What's real" (src/lib/real-model.ts, src/data/real.json): the status vocabulary, the parser that keeps a bad edit from
// printing a number its status does not back, and the data file itself. Run with `npm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

register("./ts-hooks.mjs", import.meta.url);
const m = await import("../src/lib/real-model.ts");
const text = readFileSync(new URL("../src/data/real.json", import.meta.url), "utf8");

test("the vocabulary is the four statuses, each with words and a tone", () => {
  assert.deepEqual([...m.REAL_STATUSES], ["real", "measured", "pending", "not-run"]);
  for (const s of m.REAL_STATUSES) {
    assert.ok(m.STATUS_TEXT[s].label && m.STATUS_TEXT[s].meaning, s);
    assert.ok(m.STATUS_TONE[s], s);
  }
  assert.equal(m.statusLabel("not-run"), "Ready to run: built and tested offline, ready to run for real");
});

test("real.json parses whole: every entry kept, with evidence and a way to check", () => {
  const raw = JSON.parse(text);
  const data = m.parseReal(text);
  assert.equal(data.entries.length, raw.entries.length);
  for (const e of data.entries) {
    assert.ok(e.evidence.length > 20, e.id);
    assert.ok(e.check.command || e.check.href, e.id);
    assert.match(e.date, /^\d{4}-\d{2}-\d{2}$/, e.id);
  }
  assert.equal(new Set(data.entries.map((e) => e.id)).size, data.entries.length);
  assert.ok(data.links.repo.startsWith("https://") && data.links.docs.startsWith("https://"));
});

test("the exam stays pending with no number until every metric is filled", () => {
  const data = m.parseReal(text);
  const exam = m.entryById(data, "exam-result");
  assert.ok(exam);
  if (exam.status === "pending") assert.ok(exam.metrics.every((x) => x.value === null));
  if (exam.status === "measured") assert.ok(exam.metrics.every((x) => x.value !== null));

  const raw = JSON.parse(text);
  const e = raw.entries.find((x) => x.id === "exam-result");
  // Measured, but one number missing: shown as pending, with none of its numbers.
  e.status = "measured";
  e.metrics = e.metrics.map((x, i) => ({ ...x, value: i === 0 ? null : "50%" }));
  const half = m.entryById(m.parseReal(JSON.stringify(raw)), "exam-result");
  assert.equal(half.status, "pending");
  assert.ok(half.metrics.every((x) => x.value === null));
  // Every number in: measured, as written.
  e.metrics = e.metrics.map((x) => ({ ...x, value: "50%" }));
  const full = m.entryById(m.parseReal(JSON.stringify(raw)), "exam-result");
  assert.equal(full.status, "measured");
  assert.ok(full.metrics.every((x) => x.value === "50%"));
  // Pending with a stray number: the number is withheld.
  e.status = "pending";
  const stray = m.entryById(m.parseReal(JSON.stringify(raw)), "exam-result");
  assert.ok(stray.metrics.every((x) => x.value === null));
});

test("a malformed entry is dropped, an unknown status reads as pending", () => {
  const data = m.parseReal(
    JSON.stringify({
      entries: [
        { id: "a", claim: "c", status: "real", evidence: "e", check: { label: "l" } },
        { id: "b", claim: "c", status: "proven", evidence: "e", check: { label: "l", command: "make test" } },
      ],
    }),
  );
  assert.deepEqual(data.entries.map((e) => [e.id, e.status]), [["b", "pending"]]);
  assert.deepEqual(m.parseReal("not json").entries, []);
});

test("the video: YouTube in links.video, the counting link in links.videoShort, tagged by where the click came from", () => {
  const data = m.parseReal(text);
  assert.equal(data.links.video, "https://youtu.be/GIeSmDtopfs");
  assert.equal(data.links.videoShort, "https://video.ratchet.dualmindlab.tech/");
  assert.equal(m.videoHref(data.links, "site"), "https://video.ratchet.dualmindlab.tech/?from=site");
  assert.equal(m.videoHref(data.links, "judges"), "https://video.ratchet.dualmindlab.tech/?from=judges");
  const yt = "https://youtu.be/x";
  // No counting link, or a bad one: straight to the video. No video: no link at all.
  for (const videoShort of [null, "", "not a url", "http://video.example.com/"]) assert.equal(m.videoHref({ video: yt, videoShort }, "site"), yt, String(videoShort));
  assert.equal(m.videoHref({ video: null, videoShort: "https://video.ratchet.dualmindlab.tech/" }, "site"), null);
  assert.equal(m.videoHref({ video: yt, videoShort: "https://v.example.com/?from=old" }, "judges"), "https://v.example.com/?from=judges");
  assert.equal(m.parseReal("{}").links.videoShort, null);
});

test("the site's video links go through videoHref: the hero tagged site, the judges page tagged judges", () => {
  const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");
  assert.match(src("pages/landing/sections/hero.tsx"), /videoHref\(REAL\.links, "site"\)/);
  assert.match(src("pages/judges/real.ts"), /JUDGES_VIDEO = videoHref\(REAL\.links, "judges"\)/);
  for (const p of ["pages/landing/sections/hero.tsx", "pages/judges/check.tsx", "pages/judges/stage-one.tsx"]) {
    assert.doesNotMatch(src(p), /links\.video\b/, p);
  }
});

test("a gateway's numbers: measured on a live gateway, no badge on a mock one", () => {
  assert.equal(m.gatewayStatus("mock"), null);
  assert.equal(m.gatewayStatus("live"), "measured");
  assert.equal(m.gatewayStatus(undefined), null);
  assert.equal(m.fmtDate("2026-10-22"), "22 Oct 2026");
  assert.equal(m.fmtDate("soon"), "soon");
  assert.deepEqual(
    m.statusCounts(m.parseReal(text)).map((c) => c.status),
    m.REAL_STATUSES.filter((s) => m.parseReal(text).entries.some((e) => e.status === s)),
  );
});
