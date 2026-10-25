// Where each file of the repo's docs/ folder lands in the site, and which sidebar group it belongs to.
// Shared by scripts/sync-docs.mjs (which copies the files) and astro.config.mjs (which builds the sidebar),
// so a doc added to docs/ shows up in both without editing this site.
import { existsSync } from 'node:fs';
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
  return posix.basename(rel) === 'AGENTS.md';
}

/**
 * The only docs published on this site, with their sidebar labels. Everything else in docs/ (planning, business and
 * team notes, decision records) is private and is never copied, linked or listed.
 */
export const PUBLIC_DOCS = [
  ['API.md', 'HTTP API guide'],
  ['ARCHITECTURE.md', 'Architecture'],
  ['CONFIGURATION.md', 'Configuration'],
  ['DEPLOYMENT.md', 'Deployment'],
  ['DEPLOY_NEBIUS_VM.md', 'Demo VM on Nebius'],
  ['RUNBOOK.md', 'Runbook'],
  ['SCOUT.md', 'Scout reference'],
  ['TESTING.md', 'Testing'],
  ['THREAT_MODEL.md', 'Threat model'],
  ['VERIFY.md', 'Verify: the commands'],
  ['WHERE_IT_RUNS.md', 'Where everything runs'],
];

/** The public docs that exist, as POSIX paths relative to docs/, sorted. */
export function docFiles(dir = DOCS_DIR) {
  if (!existsSync(dir)) return [];
  return PUBLIC_DOCS.map(([rel]) => rel)
    .filter((rel) => existsSync(join(dir, rel)))
    .sort();
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

/** Sidebar label of one public doc. */
export function labelOf(rel) {
  return PUBLIC_DOCS.find(([r]) => r === rel)?.[1] ?? rel;
}
