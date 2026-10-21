// Copy the repo's docs/ into the site, so docs/ stays the single source of truth.
//
//   node scripts/sync-docs.mjs          (runs before `npm run dev` and `npm run build`)
//
// For each docs/**/*.md (except the per-folder contributor notes) it writes src/content/docs/reference/<name>.md (git-ignored):
// - frontmatter: the title from the first `# ` heading (which it removes), a description from the first paragraph,
//   an edit link to the file in docs/ on GitHub, and a sidebar label for ADRs;
// - links between docs become site routes (`API.md#errors` -> `/reference/api/#errors`); links to anything else in the
//   repo (code, contributor notes, folders) become GitHub URLs; an anchor that matches no heading in its target is dropped (the
//   link goes to the top of the page) and reported, so no link on the site points at nothing;
// - tags that are not HTML (`<yours>` outside code) are escaped so they show as text.
// docs/img/ is copied next to the pages so image links keep working.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, posix } from 'node:path';
import GithubSlugger from 'github-slugger';
import {
  DOCS_DIR,
  GITHUB_REF,
  OUT_DIR,
  REPO_DIR,
  REPO_URL,
  SITE_DIR,
  docFiles,
  outRel,
  routeOf,
  skipped,
} from './doc-map.mjs';

const HTML_TAGS = new Set(
  'a abbr b blockquote br code dd del details div dl dt em h1 h2 h3 h4 h5 h6 hr i img ins kbd li ol p picture pre q s samp small source span strong sub summary sup table tbody td tfoot th thead tr u ul var'.split(' '),
);

const warnings = [];
const warn = (msg) => warnings.push(msg);

/** Split a markdown text into lines tagged as inside a fenced code block or not. */
function fenced(lines) {
  let fence = null;
  return lines.map((line) => {
    const m = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (fence) {
      // A closing fence: the same character, at least as long, nothing after it.
      const close = line.match(/^\s{0,3}(`{3,}|~{3,})\s*$/);
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) fence = null;
      return { line, code: true };
    }
    if (m) {
      fence = m[1];
      return { line, code: true };
    }
    return { line, code: false };
  });
}

/** Apply `fn` to the parts of a line outside inline code spans. */
function outsideCode(line, fn) {
  let out = '';
  let i = 0;
  while (i < line.length) {
    const tick = line.indexOf('`', i);
    if (tick === -1) {
      out += fn(line.slice(i));
      break;
    }
    out += fn(line.slice(i, tick));
    let run = 1;
    while (line[tick + run] === '`') run++;
    const marker = '`'.repeat(run);
    const close = line.indexOf(marker, tick + run);
    if (close === -1) {
      out += line.slice(tick);
      break;
    }
    out += line.slice(tick, close + run);
    i = close + run;
  }
  return out;
}

/** Heading text as it renders (links to their text, code kept, emphasis and tags dropped). */
function headingText(raw) {
  return raw
    .replace(/\s+#+\s*$/, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(^|\W)[*_](\S.*?)[*_](?=\W|$)/g, '$1$2')
    .trim();
}

/** Every heading id a doc will have, as GitHub (and Astro) slug them. */
function headingIds(text) {
  const slugger = new GithubSlugger();
  const ids = new Set();
  for (const { line, code } of fenced(text.split(/\r?\n/))) {
    if (code) continue;
    const m = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
    if (m) ids.add(slugger.slug(headingText(m[2])));
  }
  return ids;
}

/** Find `](target)` link destinations outside code and rewrite each with `fn(target)`. */
function rewriteLinks(text, fn) {
  return outsideCode(text, (part) => {
    let out = '';
    let i = 0;
    while (i < part.length) {
      const at = part.indexOf('](', i);
      if (at === -1) {
        out += part.slice(i);
        break;
      }
      out += part.slice(i, at + 2);
      // Read the destination up to the matching parenthesis.
      let depth = 1;
      let j = at + 2;
      while (j < part.length && depth > 0) {
        if (part[j] === '(') depth++;
        else if (part[j] === ')') depth--;
        if (depth > 0) j++;
      }
      if (depth !== 0) {
        out += part.slice(at + 2);
        break;
      }
      const inner = part.slice(at + 2, j);
      const m = inner.match(/^(\s*)(<[^>]*>|[^\s]+)(\s+(?:"[^"]*"|'[^']*'))?(\s*)$/);
      if (!m) {
        out += `${inner})`;
      } else {
        const angle = m[2].startsWith('<');
        const target = angle ? m[2].slice(1, -1) : m[2];
        const next = fn(target);
        out += `${m[1]}${angle ? `<${next}>` : next}${m[3] || ''}${m[4]})`;
      }
      i = j + 1;
    }
    return out;
  });
}

function escapeTags(part) {
  return part.replace(/<(\/?)([A-Za-z][\w.-]*)([^<>\n]*)>/g, (all, slash, name, rest) => {
    if (HTML_TAGS.has(name.toLowerCase())) return all;
    if (/^[a-z][a-z0-9+.-]*:/i.test(name + rest)) return all; // autolink <https://...>
    if (/@/.test(name + rest)) return all; // email autolink
    return `&lt;${slash}${name}${rest}&gt;`;
  });
}

function github(repoRel, hash) {
  const full = join(REPO_DIR, repoRel);
  const kind = existsSync(full) && statSync(full).isDirectory() ? 'tree' : 'blob';
  if (!existsSync(full)) warn(`missing link target ${repoRel} (linked to GitHub anyway)`);
  const path = repoRel.replace(/\/$/, '');
  return `${REPO_URL}/${kind}/${GITHUB_REF}/${path}${hash ? `#${hash}` : ''}`;
}

function frontmatter(fields) {
  const lines = ['---'];
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined) continue;
    if (value && typeof value === 'object') {
      lines.push(`${key}:`);
      for (const [k, v] of Object.entries(value)) lines.push(`  ${k}: ${JSON.stringify(v)}`);
    } else {
      lines.push(`${key}: ${JSON.stringify(value)}`);
    }
  }
  lines.push('---', '');
  return lines.join('\n');
}

/** First paragraph after the title, as plain text, cut to about 155 characters. */
function describe(lines) {
  const para = [];
  for (const { line, code } of lines) {
    if (code) {
      if (para.length) break;
      continue;
    }
    const t = line.trim();
    if (!t) {
      if (para.length) break;
      continue;
    }
    if (/^(#|\||-|\*|>|\d+\.|!\[|<)/.test(t)) {
      if (para.length) break;
      continue;
    }
    para.push(t);
  }
  let text = headingText(para.join(' ')).replace(/\s+/g, ' ');
  if (text.length > 155) text = `${text.slice(0, 152).replace(/\s+\S*$/, '')}...`;
  return text || undefined;
}

/**
 * Rewrite one link found in docs/<rel>. With `samePage` set, a bare `#anchor` points at that route instead (used when
 * text from a doc is placed on a generated page).
 */
function makeLinker(rel, sources, ids, { samePage } = {}) {
  const dir = posix.dirname(rel);
  return (target) => {
    if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('//')) return target; // absolute URL
    const [pathPart, ...rest] = target.split('#');
    const hash = rest.join('#');
    if (!pathPart) {
      // Same-page anchor.
      if (hash && !ids.get(rel).has(hash)) {
        warn(`${rel}: anchor #${hash} matches no heading on the same page`);
        if (samePage) return samePage;
      }
      return samePage ? `${samePage}${hash ? `#${hash}` : ''}` : target;
    }
    let decoded = pathPart;
    try {
      decoded = decodeURI(pathPart);
    } catch {}
    const repoRel = posix.normalize(posix.join('docs', dir, decoded));
    if (repoRel.startsWith('..')) {
      warn(`${rel}: link ${target} leaves the repository`);
      return target;
    }
    if (repoRel.startsWith('docs/') && /\.md$/i.test(repoRel)) {
      const docRel = repoRel.slice('docs/'.length);
      if (sources.has(docRel) && !skipped(docRel)) {
        if (hash && !ids.get(docRel).has(hash)) {
          warn(`${rel}: anchor #${hash} matches no heading in ${docRel}; linking to the page`);
          return routeOf(docRel);
        }
        return `${routeOf(docRel)}${hash ? `#${hash}` : ''}`;
      }
    }
    if (repoRel.startsWith('docs/img/') && !samePage) {
      // Copied next to the pages: keep the path relative to the output file.
      const fromDir = posix.dirname(outRel(rel));
      const relPath = posix.relative(fromDir === '.' ? '' : fromDir, repoRel.slice('docs/'.length));
      return relPath.startsWith('.') ? relPath : `./${relPath}`;
    }
    return github(repoRel, hash);
  };
}

/** Split a markdown table row into cells, keeping `|` inside code spans and escaped `\|`. */
function cells(row) {
  const out = [];
  let cur = '';
  let tick = 0;
  const s = row.trim().replace(/^\|/, '').replace(/\|$/, '');
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === '\\' && s[i + 1] === '|') {
      cur += '\\|';
      i++;
    } else if (ch === '`') {
      let run = 1;
      while (s[i + run] === '`') run++;
      cur += '`'.repeat(run);
      i += run - 1;
      tick = tick === run ? 0 : tick || run;
    } else if (ch === '|' && !tick) {
      out.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur.trim());
  return out;
}

const ENV_TOKEN = /`((?:RATCHET|NEBIUS|TAVILY)_[A-Z0-9_<>]*[A-Z0-9>]|DRY_RUN)`/g;
const API_DIR = join(SITE_DIR, 'src', 'content', 'docs', 'api');

/**
 * api/environment.md: every variable in docs/CONFIGURATION.md's tables, grouped as there, with where it matters, checked
 * against the lists in ratchet/config.py that generated/env.json carries (python docs-site/scripts/export-reference.py).
 */
function environmentPage(sources, ids) {
  const rel = 'CONFIGURATION.md';
  const envFile = join(SITE_DIR, 'generated', 'env.json');
  if (!sources.has(rel) || !existsSync(envFile)) return null;
  const env = JSON.parse(readFileSync(envFile, 'utf8'));
  const code = new Set([...env.package, ...env.deploy, ...env.release]);
  const deploy = new Set(env.deploy);
  const tool = new Set(env.tool);
  const mockOnly = new Set(Object.keys(env.mock_only));
  const reserved = new Set(Object.keys(env.reserved));
  const route = routeOf(rel);
  const link = makeLinker(rel, sources, ids, { samePage: route });
  const fix = (text) => outsideCode(rewriteLinks(text, link), escapeTags);

  const slugger = new GithubSlugger();
  const sections = [];
  let h2 = '';
  let current = null;
  let header = null;
  const documented = new Set();
  for (const { line, code: inCode } of fenced(sources.get(rel).split(/\r?\n/))) {
    if (inCode) continue;
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const text = headingText(h[2]);
      const id = slugger.slug(text);
      if (h[1].length === 2) h2 = text;
      if (h[1].length >= 2) current = { title: h[1].length === 2 ? text : `${h2}: ${text}`, id, rows: [] };
      header = null;
      continue;
    }
    if (!line.startsWith('|')) {
      header = null;
      continue;
    }
    const row = cells(line);
    if (!header) {
      if (row.includes('Variable')) {
        header = row;
        if (current && !sections.includes(current)) sections.push(current);
      }
      continue;
    }
    if (row.every((c) => /^:?-+:?$/.test(c))) continue;
    const get = (name) => row[header.indexOf(name)] ?? '';
    const variable = get('Variable');
    const names = [...variable.matchAll(ENV_TOKEN)].map((m) => m[1]);
    names.forEach((n) => documented.add(n));
    const where = new Set();
    for (const n of names) {
      if (mockOnly.has(n)) where.add('mock only');
      else if (reserved.has(n)) where.add('reserved');
      else if (tool.has(n)) where.add('live-run tool');
      else if (deploy.has(n) || n === 'DRY_RUN') where.add('deploy kit');
    }
    if (!where.size) {
      if (/^Mock-mode/i.test(current.title)) where.add('mock only');
      else if (/^(Live Night School|Live-run harness)/i.test(current.title)) where.add('live');
      else if (/^(Deploy scripts|Demo VM)/i.test(current.title)) where.add('deploy kit');
      else where.add('both');
    }
    let meaning = get('Meaning') || get('Used in') || get('Feature that will read it');
    if (header.includes('Field')) meaning = `Field ${get('Field')}. ${meaning}`;
    if (header.includes('Used by')) meaning = `Used by ${get('Used by')}. ${meaning}`;
    current.rows.push({ variable, def: get('Default'), where: [...where].join(', '), meaning });
  }

  const missing = [...code].filter((n) => !documented.has(n) && !/<FIELD>/.test(n)).sort();
  const total = sections.reduce((n, s) => n + s.rows.length, 0);
  const out = [];
  out.push(
    frontmatter({
      title: 'Environment variables',
      description: 'Every environment variable Ratchet and its deploy kit read, grouped, with its default, where it matters and what it does.',
      editUrl: `${REPO_URL}/edit/${GITHUB_REF}/docs/CONFIGURATION.md`,
    }),
  );
  out.push('<!-- Generated by docs-site/scripts/sync-docs.mjs from docs/CONFIGURATION.md and docs-site/generated/env.json. -->\n');
  out.push(
    `Ratchet reads its settings from environment variables. This page lists all ${total} rows of the configuration tables, grouped as in the [configuration guide](${route}), which has the longer explanations. It is built from \`docs/CONFIGURATION.md\` and checked against the lists in [\`ratchet/config.py\`](${REPO_URL}/blob/${GITHUB_REF}/ratchet/config.py) (\`KNOWN_ENV\`, \`DEPLOY_ENV\`, \`MOCK_ONLY_ENV\`, \`TOOL_ENV\`, \`RESERVED_SETTINGS\`) and \`ReleasePolicy\`'s variables, exported by \`python docs-site/scripts/export-reference.py\`.\n`,
  );
  out.push(':::tip[Check before you start]');
  out.push(
    'Run `python -m ratchet.config check` after every change to the environment. It lists every problem at once and exits 0 (clean), 1 (warnings) or 2 (errors: the gateway would not start). An unknown `RATCHET_*` variable is a warning, so a typo is caught. See [Command line](/api/cli/#python--m-ratchetconfig).',
  );
  out.push(':::\n');
  out.push('## What you need for each mode\n');
  out.push('- **Mock mode** (the default, `RATCHET_MODE=mock`): nothing. No keys, no network, an in-memory ledger.');
  out.push(
    '- **Live mode** (`RATCHET_MODE=live`): `NEBIUS_API_KEY` (Token Factory), and `RATCHET_API_KEYS` (or `RATCHET_ALLOW_UNAUTHENTICATED=1` when a proxy such as the Caddy guard checks callers). Add `RATCHET_STUDENT_BASE` and `RATCHET_STUDENT_KEY` only while the GPU endpoint runs; without them the hosted fallback answers student calls. `TAVILY_API_KEY` gives Scout real web search. Keep the Scout budget guard on.',
  );
  out.push('- **Night School in live mode**: `RATCHET_SYNC_CMD`, `RATCHET_TRAIN_CMD` (or its default) and the deploy kit variables for the bucket, image and endpoint.\n');
  out.push('The **Where** column says where a variable matters:\n');
  out.push('| Where | Meaning |\n|---|---|');
  out.push('| both | Read by the gateway in mock and live mode (some only change live behaviour; the meaning says so) |');
  out.push('| mock only | Read only in mock mode; the startup check warns when one is set in live mode |');
  out.push('| live | Read by live mode tools such as `python -m ratchet.live` |');
  out.push('| deploy kit | Read only by `deploy/*.sh` and the demo VM, never by the package |');
  out.push('| live-run tool | Read by the live-run harness (`python -m ratchet.liverun`) |');
  out.push('| reserved | Declared for a feature not built yet: any value but the default stops startup |\n');
  for (const s of sections) {
    out.push(`## ${s.title}\n`);
    out.push(`From [${s.title}](${route}#${s.id}) in the configuration guide.\n`);
    out.push('| Variable | Default | Where | Meaning |\n|---|---|---|---|');
    for (const r of s.rows) out.push(`| ${fix(r.variable)} | ${fix(r.def)} | ${r.where} | ${fix(r.meaning)} |`);
    out.push('');
  }
  out.push('## Checked against the code\n');
  if (missing.length) {
    out.push(
      `${missing.length} variable(s) the code reads have no row in the configuration guide yet: ${missing.map((n) => `\`${n}\``).join(', ')}. The traceability check (\`python scripts/reqtrace.py --check\`) is meant to catch this.\n`,
    );
    warn(`environment page: ${missing.length} variable(s) in ratchet/config.py without a docs row: ${missing.join(', ')}`);
  } else {
    out.push(
      `Every one of the ${code.size} variables the package and the deploy kit read (${env.package.length} in \`KNOWN_ENV\`, ${env.deploy.length} in \`DEPLOY_ENV\`, ${env.release.length} release policy fields) has a row above.\n`,
    );
  }
  return out.join('\n');
}

// What a Make target is for, when the Makefile has no comment above it (wording from the repo's command list).
const MAKE_NOTES = {
  install: 'Install the package with its dev dependencies and ruff.',
  test: 'Run the test suite (no network, no GPU).',
  lint: 'Lint, check formatting and check the requirements matrix. Run it with `make test` before every commit.',
  fmt: 'Format the code and apply safe lint fixes.',
  sim: 'The 10-day simulation with a corrupted deploy of v3 (mock models).',
  demo: 'The mock gateway with a 4-day warm-up on `http://localhost:8000`: `/`, `/scout` and `/dashboard`.',
  day1: 'Live Token Factory checks before live mode (needs `NEBIUS_API_KEY`; costs cents).',
  'live-plan': 'Live-run harness: plan a run (requests per release stage, golden size, dollars per round). Free.',
  'live-smoke': 'Live-run harness: 20 requests and a few control calls, no nights. Costs cents.',
  'live-run': 'Live-run harness: the measured run, capped at `MAX_USD`.',
  docker: 'Build the gateway image (`ratchet-gateway`).',
  clean: 'Remove caches, build output, `sim_results.json` and `ratchet.db`.',
};

const CLI_GROUPS = [
  ['Run it in live mode', ['live', 'live-export', 'live-purge', 'config', 'cost', 'admin', 'importer', 'liverun']],
  ['Check the evidence offline', ['bom', 'stats', 'proof', 'receipt', 'replay']],
  ['Demos and simulation', ['scout', 'simulate']],
  ['Build, train and check the repo', ['train', 'day1', 'reqtrace', 'reference-check', 'regen-sim']],
];

/** api/cli.md: every command with a "when to use it" line and its help, from generated/cli.json. */
function cliPage() {
  const file = join(SITE_DIR, 'generated', 'cli.json');
  if (!existsSync(file)) return null;
  const data = JSON.parse(readFileSync(file, 'utf8'));
  const byId = new Map(data.commands.map((c) => [c.id, c]));
  const fence = (text) => `\`\`\`text\n${text.replace(/```/g, "'''")}\n\`\`\``;
  const out = [];
  out.push(
    frontmatter({
      title: 'Command line',
      description: 'Every Ratchet command and Make target, with when to use it and its help text captured from the code.',
      editUrl: `${REPO_URL}/edit/${GITHUB_REF}/docs-site/scripts/export-reference.py`,
    }),
  );
  out.push('<!-- Generated by docs-site/scripts/sync-docs.mjs from docs-site/generated/cli.json. -->\n');
  out.push(
    'Every command line tool in the repository, with a line on when to use it and its help text. The help text is captured from the code by `python docs-site/scripts/export-reference.py`, which runs each tool with `--help` (or reads its module docstring when it has no parser), so it matches the code it was exported from. Run commands from the repository root after `make install`.\n',
  );
  out.push(':::note[Mock mode first]');
  out.push(
    'Nothing here needs a key in mock mode except the live tools (`ratchet.live`, `ratchet.liverun`, `scripts/day1_check.py`). Commands that write take a ledger file with `--db` or `RATCHET_DB`; never point two gateways at one ledger.',
  );
  out.push(':::\n');
  out.push('## Make targets\n');
  out.push('From the [`Makefile`](' + `${REPO_URL}/blob/${GITHUB_REF}/Makefile` + '). On Windows, run the command in the second column from WSL or Git Bash, or set the variables the PowerShell way (`$env:RATCHET_MODE="mock"`).\n');
  out.push('| Target | Runs | What it is for |\n|---|---|---|');
  for (const t of data.make) {
    const recipe = t.recipe ? t.recipe.split('\n').map((l) => `\`${l.replace(/\|/g, '\\|')}\``).join('<br>') : '';
    const note = (t.comment || MAKE_NOTES[t.name] || '').replace(/\|/g, '\\|');
    out.push(`| \`make ${t.name}\` | ${recipe} | ${note} |`);
  }
  out.push('');
  for (const [group, ids] of CLI_GROUPS) {
    out.push(`## ${group}\n`);
    for (const id of ids) {
      const c = byId.get(id);
      if (!c) continue;
      out.push(`### \`${c.cmd}\`\n`);
      out.push(`**When to use it:** ${c.when}\n`);
      out.push(`Help (${c.help_from}):\n`);
      out.push(fence(c.help));
      out.push('');
      for (const sub of c.subcommands ?? []) {
        if (!sub.ok) continue;
        out.push(`<details><summary><code>${c.cmd} ${sub.name} --help</code></summary>\n`);
        out.push(fence(sub.help));
        out.push('\n</details>\n');
      }
    }
  }
  const known = new Set(CLI_GROUPS.flatMap(([, ids]) => ids));
  const rest = data.commands.filter((c) => !known.has(c.id));
  if (rest.length) {
    out.push('## Other commands\n');
    for (const c of rest) {
      out.push(`### \`${c.cmd}\`\n`, `**When to use it:** ${c.when}\n`, fence(c.help), '');
    }
  }
  return out.join('\n');
}

function main() {
  if (!existsSync(DOCS_DIR)) throw new Error(`no docs folder at ${DOCS_DIR}`);
  const files = docFiles();
  const sources = new Map(files.map((rel) => [rel, readFileSync(join(DOCS_DIR, rel), 'utf8').replace(/^﻿/, '')]));
  const ids = new Map([...sources].map(([rel, text]) => [rel, headingIds(text)]));

  rmSync(OUT_DIR, { recursive: true, force: true });
  mkdirSync(OUT_DIR, { recursive: true });

  for (const [rel, text] of sources) {
    const lines = fenced(text.split(/\r?\n/));
    // Title: the first `# ` heading outside code, removed from the body.
    const at = lines.findIndex(({ line, code }) => !code && /^#\s+\S/.test(line));
    let title = rel.replace(/\.md$/i, '');
    if (at !== -1) {
      title = headingText(lines[at].line.replace(/^#\s+/, ''));
      lines.splice(at, 1);
      if (lines[at] && !lines[at].code && lines[at].line.trim() === '') lines.splice(at, 1);
    } else {
      warn(`${rel}: no "# " heading, title from the file name`);
    }
    const link = makeLinker(rel, sources, ids);

    const body = lines
      .map(({ line, code }) => (code ? line : outsideCode(rewriteLinks(line, link), escapeTags)))
      .join('\n');

    const fields = { title };
    const description = describe(lines);
    if (description) fields.description = description;
    fields.editUrl = /^REQUIREMENTS\.md$/i.test(rel) ? false : `${REPO_URL}/edit/${GITHUB_REF}/docs/${rel}`;
    if (rel.startsWith('adr/') && /^ADR\s+\d+/i.test(title)) {
      fields.sidebar = { label: title.replace(/^ADR\s+/i, '') };
    } else if (/^adr\/README\.md$/i.test(rel)) {
      fields.sidebar = { label: 'All decisions', order: 0 };
    }

    const banner = `<!-- Generated by docs-site/scripts/sync-docs.mjs from docs/${rel}. Edit that file, not this one. -->\n\n`;
    const out = join(OUT_DIR, outRel(rel));
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, frontmatter(fields) + banner + body.replace(/^\n+/, ''));
  }

  const img = join(DOCS_DIR, 'img');
  if (existsSync(img)) cpSync(img, join(OUT_DIR, 'img'), { recursive: true });

  // The generated reference pages (git-ignored; their data is committed under generated/).
  mkdirSync(API_DIR, { recursive: true });
  for (const [name, text] of [
    ['environment.md', environmentPage(sources, ids)],
    ['cli.md', cliPage()],
  ]) {
    const path = join(API_DIR, name);
    if (text) writeFileSync(path, text);
    else {
      rmSync(path, { force: true });
      warn(`api/${name} not generated: run python docs-site/scripts/export-reference.py`);
    }
  }

  console.log(`sync-docs: ${sources.size} docs from docs/ into src/content/docs/reference/, plus api/environment and api/cli`);
  for (const w of warnings) console.warn(`sync-docs: ${w}`);
  if (process.env.DOCS_STRICT === '1' && warnings.length) process.exit(1);
}

main();
