// Browser test for the <Term> tooltip (src/components/Term.astro), against the built site in dist/.
//   npm run build && node scripts/test-term.mjs        (SHOTS=<dir> saves screenshots; CHROME_PATH picks a browser)
// It checks that the card is never clipped by an overflow:hidden box around the word, flips above near the bottom of the
// window, stays inside the window, closes on Escape, opens on keyboard focus, and opens on the first touch tap.
import assert from 'node:assert/strict';
import { createReadStream, existsSync, mkdirSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const dist = join(fileURLToPath(new URL('..', import.meta.url)), 'dist');
const shots = process.env.SHOTS;
if (shots) mkdirSync(shots, { recursive: true });
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.json': 'application/json', '.webp': 'image/webp', '.png': 'image/png' };

const server = createServer((req, res) => {
  let p = normalize(decodeURIComponent(req.url.split('?')[0])).replace(/^([/\\])+/, '');
  let file = join(dist, p);
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) { res.writeHead(404).end(); return; }
  res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;
const launch = process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' };
const browser = await chromium.launch({ ...launch, headless: true });
const PAGE = `${base}/guides/what-is-ratchet/`;
// the term inside the "Reading this diagram" box (a figure with overflow: hidden)
const IN_BOX = 'figure.rt-diagram figcaption a.rt-term__link[href$="#provenance"]';
const cardState = (page) =>
  page.evaluate(() => {
    const c = document.querySelector('.rt-card');
    if (!c) return null;
    const r = c.getBoundingClientRect();
    const def = c.querySelector('.rt-card__def');
    return {
      shown: c.matches(':popover-open') || (!c.hasAttribute('popover') && !c.hidden),
      open: c.classList.contains('is-open'),
      parentIsBody: c.parentElement === document.body,
      placement: c.dataset.placement,
      rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom },
      vw: document.documentElement.clientWidth,
      vh: innerHeight,
      defFits: def.scrollHeight <= def.clientHeight + 1,
      text: c.textContent,
    };
  });
const waitOpen = (page) => page.waitForFunction(() => document.querySelector('.rt-card')?.classList.contains('is-open'), null, { timeout: 3000 });
const shot = async (page, name) => (shots ? (await page.waitForTimeout(350), page.screenshot({ path: join(shots, name) })) : null);
const clip = async (page, name, pad = 90) =>
  shots
    ? (await page.waitForTimeout(350), page).evaluate(() => { const r = document.querySelector('.rt-card').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }).then(([x, y, w, h]) =>
        page.screenshot({ path: join(shots, name), clip: { x: Math.max(0, x - pad), y: Math.max(0, y - pad), width: Math.min(w + pad * 2, 900), height: h + pad * 2 } }))
    : null;

// diagrams load as they scroll into view and shift the page: bring the word into view and let the layout settle first
const settle = async (page, link) => {
  await link.scrollIntoViewIfNeeded();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(700);
  await link.scrollIntoViewIfNeeded();
  await page.waitForTimeout(200);
};
let failed = 0;
const fresh = async (page) => {
  await page.goto(PAGE);
  await page.mouse.move(2, 2);
  await page.waitForTimeout(100);
};
const test = async (name, fn) => {
  try { await fn(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n' + e.message); }
};

for (const scheme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: scheme });
  const page = await ctx.newPage();
  await page.goto(PAGE);

  await test(`${scheme}: a term inside an overflow:hidden box shows its whole card`, async () => {
    const link = page.locator(IN_BOX).first();
    await settle(page, link);
    const hidden = await link.evaluate((el) => { const f = el.closest('figure.rt-diagram'); return getComputedStyle(f).overflow; });
    assert.equal(hidden, 'hidden', 'the test needs the box to clip');
    await link.hover();
    await waitOpen(page);
    const s = await cardState(page);
    assert.ok(s.shown && s.parentIsBody, 'the card lives in <body>, outside every box');
    assert.ok(await link.evaluate((el) => !el.closest('figure').contains(document.querySelector('.rt-card'))));
    assert.ok(s.defFits, 'the whole definition shows');
    assert.match(s.text, /Where a training example came from: which teacher wrote it and under which license/);
    assert.ok(s.rect.left >= 8 && s.rect.right <= s.vw - 8, 'inside the window with an 8px margin');
    // the card's centre point hits the card itself, not the box behind it
    assert.ok(await page.evaluate(() => { const r = document.querySelector('.rt-card').getBoundingClientRect(); return !!document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest('.rt-card'); }));
    await clip(page, `${scheme}-in-diagram-box.png`);
  });

  await test(`${scheme}: Escape closes, and only one card is open`, async () => {
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.rt-card')?.classList.contains('is-open'));
    assert.equal(await page.locator('.rt-card').count(), 1);
  });

  await test(`${scheme}: the card can be entered and its link clicked`, async () => {
    const link = page.locator(IN_BOX).first();
    await fresh(page);
    await settle(page, link);
    await link.hover();
    await waitOpen(page);
    const more = page.locator('.rt-card .rt-card__more a').first();
    await more.hover(); // pointer travels from the word to the card
    await page.waitForTimeout(400);
    assert.ok((await cardState(page)).open, 'still open while the pointer is on the card');
    await Promise.all([page.waitForURL(/\/guides\/glossary\/#provenance$/), more.click()]);
    await page.goto(PAGE);
  });

  await test(`${scheme}: near the bottom of the window the card flips above, arrow follows`, async () => {
    await fresh(page);
    const link = page.locator(IN_BOX).first();
    await settle(page, link);
    for (let i = 0; i < 3; i++) {
      await link.evaluate((el) => { const r = el.getBoundingClientRect(); scrollBy(0, r.top - (innerHeight - 50)); });
      await page.waitForTimeout(400);
    }
    await link.hover();
    await waitOpen(page);
    const s = await cardState(page);
    assert.equal(s.placement, 'top');
    assert.ok(s.rect.bottom <= s.vh - 8 && s.rect.top >= 8);
    await clip(page, `${scheme}-flipped-above.png`);
    await page.keyboard.press('Escape');
  });

  await test(`${scheme}: keyboard focus opens it, Tab reaches the card's links, Escape returns focus`, async () => {
    await fresh(page);
    const link = page.locator(IN_BOX).first();
    await settle(page, link);
    await page.keyboard.press('Shift');
    await link.focus();
    await waitOpen(page);
    await page.keyboard.press('Tab');
    assert.ok(await page.evaluate(() => !!document.activeElement.closest('.rt-card')), 'focus moved into the card');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.rt-card')?.classList.contains('is-open'));
    assert.ok(await link.evaluate((el) => el === document.activeElement), 'focus is back on the word');
  });

  await test(`${scheme}: a term near the right edge stays inside the window`, async () => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(PAGE);
    const idx = await page.evaluate(() => {
      const links = [...document.querySelectorAll('.sl-markdown-content p a.rt-term__link, .sl-markdown-content li a.rt-term__link')];
      let best = -1, bestRight = 0;
      links.forEach((a, i) => { const r = a.getClientRects()[0]; if (r && r.right > bestRight && r.right <= innerWidth) { best = i; bestRight = r.right; } });
      return best;
    });
    const link = page.locator('.sl-markdown-content p a.rt-term__link, .sl-markdown-content li a.rt-term__link').nth(idx);
    await settle(page, link);
    await link.hover();
    await waitOpen(page);
    const s = await cardState(page);
    assert.ok(s.rect.left >= 8 - 0.5 && s.rect.right <= s.vw - 8 + 0.5, JSON.stringify(s.rect));
    await shot(page, `${scheme}-narrow-right-edge.png`);
  });
  await ctx.close();
}

await test('touch: the first tap opens the card without navigating, a tap outside closes it, a second tap follows the link', async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  await page.goto(PAGE);
  const link = page.locator(IN_BOX).first();
  await settle(page, link);
  await link.tap();
  await waitOpen(page);
  assert.equal(new URL(page.url()).pathname, '/guides/what-is-ratchet/');
  await shot(page, 'touch-open.png');
  await page.touchscreen.tap(8, 300);
  await page.waitForFunction(() => !document.querySelector('.rt-card')?.classList.contains('is-open'));
  await link.tap();
  await waitOpen(page);
  await Promise.all([page.waitForURL(/\/guides\/glossary\/#provenance$/), link.tap()]);
  await ctx.close();
});

await browser.close();
server.close();
if (failed) { console.log(`\n${failed} failed`); process.exit(1); }
console.log('\nall term tests passed');
