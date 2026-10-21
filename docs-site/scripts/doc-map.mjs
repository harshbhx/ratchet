// Where each file of the repo's docs/ folder lands in the site, and which sidebar group it belongs to.
// Shared by scripts/sync-docs.mjs (which copies the files) and astro.config.mjs (which builds the sidebar),
// so a doc added to docs/ shows up in both without editing this site.
import { existsSync, readdirSync, statSync } from 'node:fs';
import { join, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

export const SITE_DIR = fileURLToPath(new URL('..', import.meta.url));
export const REPO_DIR = join(SITE_DIR, '..');
export const DOCS_DIR = join(REPO_DIR, 'docs');
/** The generated folder (git-ignored): every synced doc lives under /reference/. */
export const OUT_DIR = join(SITE_DIR, 'src', 'content', 'docs', 'reference');
export const ROUTE_BASE = 'reference';

export const REPO_URL = 'https://github.com/harshbhx/ratchet';
/** The branch the GitHub links point at. The docs describe main once the lanes merge. */
export const GITHUB_REF = process.env.DOCS_GITHUB_REF || 'main';
export const SITE_URL = 'https://docs.ratchet.dualmindlab.tech';

/** Files never published: per-folder instructions for contributors, not documentation. */
export function skipped(rel) {
  return posix.basename(rel) === 'CLAUDE.md';
}

/** Every markdown file under docs/, as POSIX paths relative to docs/, sorted. */
export function docFiles(dir = DOCS_DIR, prefix = '') {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const full = join(dir, name);
    const rel = prefix ? `${prefix}/${name}` : name;
    if (statSync(full).isDirectory()) out.push(...docFiles(full, rel));
    else if (name.toLowerCase().endsWith('.md') && !skipped(rel)) out.push(rel);
  }
  return out;
}

/** `API.md` -> `api.md`, `adr/README.md` -> `adr/index.md`, `DEPLOY_NEBIUS_VM.md` -> `deploy-nebius-vm.md`. */
export function outRel(rel) {
  const parts = rel.split('/');
  const file = parts.pop();
  const base = file.replace(/\.md$/i, '');
  const name = /^readme$/i.test(base) ? 'index' : base.toLowerCase().replace(/_/g, '-');
  return [...parts.map((p) => p.toLowerCase()), `${name}.md`].join('/');
}

/** The site slug (no leading or trailing slash), e.g. `reference/api`, `reference/adr`, `reference`. */
export function slugOf(rel) {
  const out = outRel(rel).replace(/\.md$/, '');
  const path = out === 'index' ? '' : out.replace(/\/index$/, '');
  return path ? `${ROUTE_BASE}/${path}` : ROUTE_BASE;
}

/** The site route with slashes, e.g. `/reference/api/`. */
export function routeOf(rel) {
  return `/${slugOf(rel)}/`;
}

// Sidebar groups for the docs we know. Anything else lands in "More reference", so a new doc is never orphaned.
// Labels are short on purpose: the page title (from the doc's first heading) can be longer.
export const GROUPS = {
  reference: [
    ['README.md', 'All documents'],
    ['API.md', 'HTTP API'],
    ['CONFIGURATION.md', 'Configuration'],
    ['ARCHITECTURE.md', 'Architecture'],
    ['SCOUT.md', 'Scout reference'],
    ['GLOSSARY.md', 'Glossary (full)'],
    ['REQUIREMENTS.md', 'Requirements matrix'],
    ['LIVE_RESULTS.md', 'Live results'],
  ],
  operations: [
    ['DEPLOYMENT.md', 'Deployment'],
    ['DEPLOY_NEBIUS_VM.md', 'Demo VM on Nebius'],
    ['DAY1.md', 'Day-1 checklist'],
    ['RUNBOOK.md', 'Runbook'],
    ['THREAT_MODEL.md', 'Threat model'],
    ['TESTING.md', 'Testing'],
  ],
  product: [
    ['PRD.md', 'Product requirements'],
    ['BUSINESS.md', 'Business'],
    ['COMPETITIVE_ANALYSIS.md', 'Competitive analysis'],
    ['RESEARCH_SUMMARY.md', 'Research summary'],
    ['ROADMAP.md', 'Roadmap'],
    ['HACKATHON_SUBMISSION.md', 'Hackathon submission'],
    ['DEMO_SCRIPT.md', 'Demo script'],
  ],
};

/** Sidebar items for one group: only the docs that exist. */
export function groupItems(group, files = docFiles()) {
  const have = new Set(files);
  return GROUPS[group].filter(([rel]) => have.has(rel)).map(([rel, label]) => ({ label, slug: slugOf(rel) }));
}

/** Top-level docs no group names, and folders other than adr/ and lanes/ (each gets its own group). */
export function ungroupedItems(files = docFiles()) {
  const named = new Set(Object.values(GROUPS).flat().map(([rel]) => rel));
  return files
    .filter((rel) => !named.has(rel) && !rel.startsWith('adr/') && !rel.startsWith('lanes/'))
    .map((rel) => ({ label: rel.replace(/\.md$/i, '').replace(/[_-]/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()), slug: slugOf(rel) }));
}
