/**
 * Builds /llms.txt (the llmstxt.org format: a title, a one-line summary, then sections of links with a short description) and
 * /llms-full.txt (every page's Markdown, then the HTTP API operations) at build time, from the docs' own source. Nothing is
 * written by hand: the text is the pages' text, with the site's component tags removed. The section order follows the sidebar
 * in astro.config.mjs; a page that is not listed there is appended under "More".
 */
import { readFileSync } from 'node:fs';
import { getCollection } from 'astro:content';
import { glossaryMarkdown } from './glossary';
import { evaluateExpressions, pageScope } from './llms-eval';

const SITE = 'https://docs.ratchet.dualmindlab.tech/';

/** The sidebar's groups, in order (keep in step with astro.config.mjs). */
const SECTIONS: { title: string; slugs: string[] }[] = [
  {
    title: 'Start here',
    slugs: ['guides/what-is-ratchet', 'guides/why-it-matters', 'guides/business', 'guides/text-to-sql', 'guides/results', 'guides/quickstart', 'guides/for-judges'],
  },
  {
    title: 'How it works',
    slugs: [
      'guides/big-picture',
      'guides/request-journey',
      'guides/confidence',
      'guides/escalation-and-audits',
      'guides/ledger',
      'guides/night-school',
      'guides/release-gate',
      'guides/training-on-your-data',
      'guides/licenses',
      'guides/cost-and-savings',
      'guides/evidence',
      'guides/proof',
      'reference/verify',
    ],
  },
  { title: 'Scout', slugs: ['guides/scout', 'reference/scout'] },
  {
    title: 'Run it',
    slugs: ['reference/where-it-runs', 'guides/host-on-nebius', 'reference/deployment', 'reference/deploy-nebius-vm', 'reference/runbook'],
  },
  { title: 'Security', slugs: ['guides/security', 'reference/threat-model'] },
  {
    title: 'API and reference',
    slugs: [
      'reference/api',
      'api/endpoints',
      'api/environment',
      'reference/configuration',
      'api/cli',
      'api/python',
      'reference/architecture',
      'reference/testing',
    ],
  },
  { title: 'Help', slugs: ['guides/glossary', 'guides/faq'] },
];

const SUMMARY =
  'Ratchet is an OpenAI-compatible gateway that distills open teacher models into a small student. You end up with an adapter you own, learned only from open teachers, with a gated nightly release.';

const url = (id: string, site?: URL) => `${(site ? site.href : SITE).replace(/\/?$/, '/')}${id === 'index' ? '' : `${id}/`}`;

/** The page's source as plain Markdown: imports and the site's component tags are dropped, their text stays. */
function clean(body: string): string {
  return body
    .replace(/^\s*import .*$/gm, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<\/?[A-Z][\w.]*(?:\s[^<>]*?)?\/?>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** A page's body with its `{expression}` parts replaced by their values, when the page computes any from its own data. */
function withValues(e: Entry): string {
  const body = (e.body ?? '').replace('<Glossary />', glossaryMarkdown());
  if (!/^export const /m.test(body)) return body;
  const scope = pageScope(body, new URL(`../content/docs/${e.id}.mdx`, import.meta.url));
  return evaluateExpressions(body, scope);
}

type Entry = { id: string; data: { title: string; description?: string }; body?: string };

async function pages(): Promise<Entry[]> {
  return (await getCollection('docs')) as unknown as Entry[];
}

/** Ordered pages: the sidebar's groups, then anything else. */
async function ordered(): Promise<{ title: string; entries: Entry[] }[]> {
  const all = await pages();
  const byId = new Map(all.map((e) => [e.id, e]));
  const used = new Set<string>();
  const out = SECTIONS.map((s) => ({
    title: s.title,
    entries: s.slugs.flatMap((slug) => {
      const e = byId.get(slug);
      if (!e) return [];
      used.add(slug);
      return [e];
    }),
  })).filter((s) => s.entries.length);
  const rest = all.filter((e) => !used.has(e.id) && e.id !== 'index').sort((a, b) => a.id.localeCompare(b.id));
  if (rest.length) out.push({ title: 'More', entries: rest });
  return out;
}

const oneLine = (s = '') => s.replace(/\s+/g, ' ').trim();

export async function llmsIndex(site?: URL): Promise<string> {
  const lines = ['# Ratchet docs', '', `> ${SUMMARY}`, '', 'The full text of every page is in [llms-full.txt](' + url('llms-full.txt', site).replace(/\/$/, '') + ').', ''];
  for (const s of await ordered()) {
    lines.push(`## ${s.title}`, '');
    for (const e of s.entries) {
      const d = oneLine(e.data.description);
      lines.push(`- [${e.data.title}](${url(e.id, site)})${d ? `: ${d}` : ''}`);
    }
    lines.push('');
  }
  lines.push('## HTTP API', '', `- [HTTP API reference](${url('api/http', site)}): every route, generated from the gateway's OpenAPI schema`, '');
  return lines.join('\n');
}

type Op = { summary?: string; description?: string };

export async function llmsFull(site?: URL): Promise<string> {
  const out = ['# Ratchet docs: full text', '', `> ${SUMMARY}`, ''];
  for (const s of await ordered()) {
    for (const e of s.entries) {
      out.push('---', '', `# ${e.data.title}`, '', `Source: ${url(e.id, site)}`, '', clean(withValues(e)), '');
    }
  }
  // The generated HTTP API pages come from the OpenAPI schema, not from a content file.
  try {
    const schema = JSON.parse(readFileSync(new URL('../../openapi/ratchet.json', import.meta.url), 'utf8'));
    out.push('---', '', '# HTTP API (generated)', '', `Source: ${url('api/http', site)}`, '');
    for (const [path, ops] of Object.entries<Record<string, Op>>(schema.paths ?? {})) {
      for (const [method, op] of Object.entries(ops)) {
        if (!['get', 'post', 'put', 'patch', 'delete'].includes(method)) continue;
        out.push(`## ${method.toUpperCase()} ${path}`, '');
        const text = oneLine(op.summary);
        if (text) out.push(text, '');
        const desc = (op.description ?? '').trim();
        if (desc && desc !== op.summary) out.push(desc, '');
      }
    }
  } catch {
    // No schema file: the pages above stand on their own.
  }
  return out.join('\n');
}
