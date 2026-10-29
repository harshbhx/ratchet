/** Helpers over src/data/glossary.ts, shared by <Term>, <Glossary> and /llms-full.txt. */
import { GLOSSARY, type GlossaryTerm } from '../data/glossary';

export const GLOSSARY_PATH = '/guides/glossary/';

const BY_SLUG = new Map(GLOSSARY.map((t) => [t.slug, t]));

/** One term by slug. An unknown slug fails the build, so a typo in a `<Term t="...">` never ships. */
export function getTerm(slug: string): GlossaryTerm {
  const term = BY_SLUG.get(slug);
  if (!term) throw new Error(`Unknown glossary term "${slug}" (add it to src/data/glossary.ts)`);
  return term;
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** The data file's small Markdown subset (`code`, **bold**, [links](/path/)) as HTML. Links become plain text when `links` is false. */
export function inline(md: string, links = true): string {
  return esc(md)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, links ? '<a href="$2">$1</a>' : '$1');
}

/** The first letter a term is filed under (digits and symbols are skipped). */
export const letterOf = (t: GlossaryTerm) => (t.name.match(/[A-Za-z]/)?.[0] ?? '#').toUpperCase();

/** The glossary as Markdown, for /llms-full.txt (the page itself is rendered from the same data). */
export function glossaryMarkdown(): string {
  return GLOSSARY.map((t) => {
    const page = t.page ? ` (${t.page[0]}: ${t.page[1]})` : '';
    const learn = t.learn ? ` Learn more: ${t.learn[1]}` : '';
    return `- **${t.name}**: ${t.long ?? t.short}${page}${learn}`;
  }).join('\n');
}
