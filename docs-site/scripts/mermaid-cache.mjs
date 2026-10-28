// @ts-check
/*
  Diagrams drawn at build time.

  astro-mermaid turns every ```mermaid block into <pre class="mermaid">SOURCE</pre> and draws it in the browser. On a long
  page that freezes the main thread for seconds (Mermaid measures text again and again, and each measure forces the whole
  page's style and layout). So the finished SVGs are kept in generated/mermaid/<key>.svg, committed, and the build puts
  them to the site in place of the <pre>. A page whose diagrams are all saved has no pre.mermaid, so astro-mermaid's
  client does not even load Mermaid there.

  The SVGs are big (the architecture page has 40 of them, 800 KB), and a page holds its first paint until the HTML is parsed,
  so they are not written into the page. Each page gets a placeholder (<div class="mermaid" data-static data-src=...>, with the
  diagram's exact size so nothing shifts) and the SVG is copied to dist/mermaid/<key>.<hash>.svg, a content-hashed file that
  can be cached for good. ReadingAids.astro fetches it when the placeholder nears the viewport and puts it inline (inline,
  not <img>: the diagram takes its colours from the page's CSS variables, so dark mode follows the theme, and it uses the
  page's web fonts; Mermaid measured its labels with them). The saved files are HTML-serialised, not XML, so svgo cannot
  read them either.

  key = the first 16 hex characters of the SHA-256 of the diagram's source as written in the block. That is what
  astro-mermaid's client stores in `data-diagram` (the <pre>'s textContent), and what the HTML holds once unescaped.

    node scripts/mermaid-cache.mjs --write   draw every diagram that has no SVG yet, in Chromium (needs `npm run build` first)
    node scripts/mermaid-cache.mjs --check   fail if a diagram in dist/ has no SVG, or an SVG is stale or unused (CI)

  `inlineDiagrams(distDir, logger)` is the build step (astro.config.mjs, astro:build:done).
*/
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, normalize, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');
export const CACHE_DIR = join(SITE_DIR, 'generated', 'mermaid');
const CONFIG_FILE = join(CACHE_DIR, 'config.json');

const PRE = /<pre class="mermaid">([\s\S]*?)<\/pre>/g;
const STATIC = /<div class="mermaid" data-processed="true" data-static="" data-key="([0-9a-f]{16})" data-src="([^"]*)"/g;
/** Where the built SVGs go in dist/, and the URL they are served from. */
const OUT_DIR = 'mermaid';

/** The reverse of astro-mermaid's escapeHtml, plus the one newline an HTML parser drops after <pre>. */
export function unescapeHtml(text) {
  const source = text
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
  return source.startsWith('\n') ? source.slice(1) : source;
}

/** The cache key of a diagram's source (equal to the `data-diagram` hash astro-mermaid's client would see). */
export const keyOf = (source) => createHash('sha256').update(source.replace(/\r\n?/g, '\n')).digest('hex').slice(0, 16);

const cacheFile = (key) => join(CACHE_DIR, `${key}.svg`);

function htmlFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...htmlFiles(path));
    else if (name.endsWith('.html')) out.push(path);
  }
  return out;
}

const plainLogger = {
  info: (m) => console.log(m),
  warn: (m) => console.warn(`warning: ${m}`),
  error: (m) => console.error(m),
};

/** What the cache was drawn with: Mermaid's version and the site's Mermaid configuration. */
async function configStamp() {
  const { mermaidConfig } = await import('../src/config/mermaid.mjs');
  const version = JSON.parse(readFileSync(join(SITE_DIR, 'node_modules', 'mermaid', 'package.json'), 'utf8')).version;
  const config = createHash('sha256').update(JSON.stringify(mermaidConfig)).digest('hex').slice(0, 16);
  return { mermaid: version, config };
}

async function stampProblem() {
  const now = await configStamp();
  if (!existsSync(CONFIG_FILE)) return 'generated/mermaid/config.json is missing';
  const saved = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'));
  if (saved.mermaid !== now.mermaid || saved.config !== now.config)
    return `the saved SVGs were drawn with Mermaid ${saved.mermaid} and config ${saved.config}; now they are ${now.mermaid} and ${now.config}`;
  return '';
}

const escapeAttr = (text) => text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

/** A short plain-text reading of a saved SVG's labels (the placeholder's accessible name, and the no-script text). */
function describe(svg) {
  const texts = [];
  for (const m of svg.matchAll(/<(?:span|p|text|tspan)\b[^>]*>([^<]+)</g)) {
    const t = m[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/\s+/g, ' ').trim();
    if (t && !texts.includes(t)) texts.push(t);
  }
  const all = texts.join(', ');
  return `Diagram${all ? `: ${all.length > 280 ? `${all.slice(0, 277)}...` : all}` : ''}`;
}

/**
 * Replace each <pre class="mermaid"> that has a saved SVG with a sized placeholder, and copy the SVG to dist/mermaid/. A
 * diagram without a saved SVG stays as it is (the browser draws it) and is counted in a warning. Returns the counts.
 */
export function inlineDiagrams(distDir, logger = plainLogger) {
  let inlined = 0;
  let missing = 0;
  const missingPages = new Set();
  const cache = new Map();
  /** key -> { url, width, height, label } of the SVG file written to dist/mermaid, or null when none is saved. */
  const fileOf = (key) => {
    if (!cache.has(key)) {
      let entry = null;
      if (existsSync(cacheFile(key))) {
        const svg = readFileSync(cacheFile(key), 'utf8').trim();
        const size = /^<svg\b[^>]*?\bwidth="([\d.]+)"[^>]*?\bheight="([\d.]+)"/.exec(svg);
        if (!size) throw new Error(`generated/mermaid/${key}.svg has no width and height on its <svg>`);
        const name = `${key}.${createHash('sha256').update(svg).digest('hex').slice(0, 10)}.svg`;
        mkdirSync(join(distDir, OUT_DIR), { recursive: true });
        writeFileSync(join(distDir, OUT_DIR, name), svg);
        entry = { url: `/${OUT_DIR}/${name}`, width: Number(size[1]), height: Number(size[2]), label: describe(svg) };
      }
      cache.set(key, entry);
    }
    return cache.get(key);
  };
  for (const file of htmlFiles(distDir)) {
    const html = readFileSync(file, 'utf8');
    if (!html.includes('<pre class="mermaid">')) continue;
    const next = html.replace(PRE, (whole, escaped) => {
      const key = keyOf(unescapeHtml(escaped));
      const d = fileOf(key);
      if (!d) {
        missing += 1;
        missingPages.add(file);
        return whole;
      }
      inlined += 1;
      const label = escapeAttr(d.label);
      // data-pagefind-ignore: search should not index node labels from a picture.
      return (
        `<div class="mermaid" data-processed="true" data-static="" data-key="${key}" data-src="${d.url}" data-pagefind-ignore>` +
        `<span class="rt-dia-ph" role="img" aria-label="${label}" style="width:${d.width}px;aspect-ratio:${d.width}/${d.height}"></span>` +
        `<noscript><span class="rt-dia-alt">${label}. The drawing needs JavaScript.</span></noscript></div>`
      );
    });
    if (next !== html) writeFileSync(file, next);
  }
  logger.info(`Static diagrams: ${inlined} placed from generated/mermaid (dist/${OUT_DIR}, loaded as they scroll into view).`);
  if (missing)
    logger.warn(
      `${missing} diagram(s) have no saved SVG and are drawn in the browser (${missingPages.size} page(s)). ` +
        'Run: node scripts/mermaid-cache.mjs --write',
    );
  return { inlined, missing };
}

/** Every key a built dist/ uses: inlined ones (data-key) and ones still waiting as <pre>, with the page of each. */
function scanDist(distDir) {
  const inlined = new Set();
  const pending = new Map(); // key -> source
  const pages = new Map(); // page -> keys still pending
  const files = new Set(); // data-src of every placeholder
  for (const file of htmlFiles(distDir)) {
    const html = readFileSync(file, 'utf8');
    for (const m of html.matchAll(STATIC)) {
      inlined.add(m[1]);
      files.add(m[2]);
    }
    for (const m of html.matchAll(PRE)) {
      const source = unescapeHtml(m[1]);
      const key = keyOf(source);
      pending.set(key, source);
      if (!pages.has(file)) pages.set(file, []);
      pages.get(file).push(key);
    }
  }
  return { inlined, pending, pages, files };
}

const savedKeys = () =>
  existsSync(CACHE_DIR) ? readdirSync(CACHE_DIR).filter((f) => f.endsWith('.svg')).map((f) => f.slice(0, -4)) : [];

function check(distDir, logger) {
  if (!existsSync(distDir)) {
    logger.error('dist/ is missing: run `npm run build` first.');
    return 1;
  }
  return configStamp().then(async (now) => {
    const { inlined, pending, pages, files } = scanDist(distDir);
    let bad = 0;
    const gone = [...files].filter((url) => !existsSync(join(distDir, ...url.split('/').filter(Boolean))));
    if (gone.length) {
      logger.error(`${gone.length} diagram file(s) are referenced by a page but missing from dist/: ${gone.slice(0, 5).join(', ')}`);
      bad = 1;
    }
    if (pending.size) {
      logger.error(`${pending.size} diagram(s) have no saved SVG (still <pre class="mermaid"> in dist/):`);
      for (const [file, keys] of pages) logger.error(`  ${file.slice(distDir.length + 1)}: ${keys.length}`);
      logger.error('Run: npm run build && node scripts/mermaid-cache.mjs --write, and commit generated/mermaid.');
      bad = 1;
    }
    const used = new Set([...inlined, ...pending.keys()]);
    const unused = savedKeys().filter((k) => !used.has(k));
    if (unused.length) {
      logger.error(`${unused.length} saved SVG(s) belong to no diagram (a diagram changed or went away): ${unused.join(', ')}`);
      logger.error('Run: node scripts/mermaid-cache.mjs --write (it removes them), and commit.');
      bad = 1;
    }
    const problem = await stampProblem();
    if (problem) {
      logger.error(`${problem}. Delete generated/mermaid, then npm run build && node scripts/mermaid-cache.mjs --write.`);
      bad = 1;
    }
    if (!bad) logger.info(`Static diagrams OK: ${inlined.size} saved SVGs, each served as dist/mermaid/<key>.<hash>.svg, none drawn in the browser (Mermaid ${now.mermaid}).`);
    return bad;
  });
}

// ---------------------------------------------------------------------------------------------------------------------
// --write: draw in Chromium

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.xml': 'application/xml',
};

/** A tiny static server for dist/ (a folder URL serves its index.html). */
function serve(root) {
  const server = createServer((req, res) => {
    const path = decodeURIComponent(new URL(req.url ?? '/', 'http://x').pathname);
    let file = normalize(join(root, path));
    if (!file.startsWith(root + sep) && file !== root) {
      res.writeHead(403).end();
      return;
    }
    if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
    if (!existsSync(file)) {
      res.writeHead(404).end('not found');
      return;
    }
    res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function write(distDir, logger) {
  if (!existsSync(distDir)) {
    logger.error('dist/ is missing: run `npm run build` first.');
    return 1;
  }
  const { inlined, pending, pages } = scanDist(distDir);
  const stamp = await configStamp();
  if (inlined.size && (await stampProblem())) {
    logger.error(
      `${await stampProblem()}. Delete generated/mermaid, then run npm run build and --write again, so every diagram is drawn afresh.`,
    );
    return 1;
  }

  const { chromium } = await import('playwright-core');
  const launch = process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' };
  const browser = await chromium.launch({ ...launch, headless: true });
  const server = await serve(distDir);
  const base = `http://127.0.0.1:${server.address().port}`;
  const drawn = new Map(); // key -> svg
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    let n = 0;
    for (const [file, keys] of pages) {
      if (process.env.MERMAID_ONLY && !file.includes(process.env.MERMAID_ONLY)) continue;
      n += 1;
      const url = `${base}/${file.slice(distDir.length + 1).replace(/\\/g, '/').replace(/index\.html$/, '')}`;
      const started = Date.now();
      const page = await context.newPage();
      page.on('pageerror', (e) => logger.warn(`${url}: ${e.message}`));
      if (process.env.MERMAID_DEBUG) page.on('console', (m) => logger.info(`  console ${m.type()}: ${m.text().slice(0, 300)}`));
      await page.goto(url, { waitUntil: 'load' });
      // Mermaid draws one diagram after another: wait for the last, up to 60 s a page.
      await page.waitForFunction(
        () => {
          const all = document.querySelectorAll('pre.mermaid');
          return all.length > 0 && Array.from(all).every((p) => p.hasAttribute('data-processed'));
        },
        undefined,
        { timeout: 180_000, polling: 250 },
      );
      await page.evaluate(() => document.fonts.ready);
      const results = await page.evaluate(() =>
        Array.from(document.querySelectorAll('pre.mermaid')).map((pre) => {
          const svg = pre.querySelector(':scope > svg');
          return {
            diagram: pre.getAttribute('data-diagram') ?? '',
            svg: svg ? svg.outerHTML : null,
            id: svg ? svg.id : '',
            error: svg ? '' : (pre.textContent ?? '').slice(0, 300),
          };
        }),
      );
      await page.close();
      const seen = [];
      for (const r of results) {
        const key = keyOf(r.diagram);
        if (!r.svg || /Error rendering diagram/.test(r.error)) {
          logger.error(`${url}: diagram ${key} did not draw: ${r.error}`);
          return 1;
        }
        if (!/^mermaid-[a-z0-9]+$/.test(r.id)) {
          logger.error(`${url}: diagram ${key} has an unexpected svg id "${r.id}"`);
          return 1;
        }
        // Mermaid's random id appears in the id attribute, the scoped <style> and the marker references.
        const svg = r.svg.split(r.id).join(`rt-m-${key}`);
        if (drawn.has(key) && drawn.get(key) !== svg) logger.warn(`${url}: diagram ${key} drew twice, differently; kept the first`);
        else drawn.set(key, svg);
        seen.push(key);
      }
      if (seen.slice().sort().join() !== keys.slice().sort().join()) {
        logger.error(`${url}: the browser drew ${seen.length} diagrams, the HTML has ${keys.length}, or their keys differ`);
        return 1;
      }
      logger.info(`[${n}/${pages.size}] ${url}: ${results.length} diagram(s), ${((Date.now() - started) / 1000).toFixed(1)} s`);
    }
  } finally {
    server.close();
    await browser.close();
  }

  mkdirSync(CACHE_DIR, { recursive: true });
  let written = 0;
  let unchanged = 0;
  for (const [key, svg] of drawn) {
    const body = `${svg}\n`;
    if (existsSync(cacheFile(key)) && readFileSync(cacheFile(key), 'utf8') === body) unchanged += 1;
    else {
      writeFileSync(cacheFile(key), body);
      written += 1;
    }
  }
  const keep = new Set([...drawn.keys(), ...inlined]);
  let removed = 0;
  for (const key of savedKeys()) {
    // A run limited to one page (MERMAID_ONLY, for debugging) never removes the others' files.
    if (!keep.has(key) && !process.env.MERMAID_ONLY) {
      rmSync(cacheFile(key));
      removed += 1;
    }
  }
  writeFileSync(CONFIG_FILE, `${JSON.stringify(stamp, null, 2)}\n`);
  // dist/ gets the new SVGs too, so `--check` can follow without another build.
  inlineDiagrams(distDir, logger);
  logger.info(`Saved SVGs: ${written} written, ${unchanged + inlined.size} unchanged, ${removed} removed (${pending.size} drawn now).`);
  return 0;
}

// ---------------------------------------------------------------------------------------------------------------------

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const mode = process.argv[2];
  const distDir = join(SITE_DIR, 'dist');
  const run = mode === '--write' ? write : mode === '--check' ? check : null;
  if (!run) {
    console.error('Usage: node scripts/mermaid-cache.mjs --write | --check');
    process.exit(2);
  }
  run(distDir, plainLogger).then(
    (code) => process.exit(code),
    (error) => {
      console.error(error);
      process.exit(1);
    },
  );
}
