/**
 * Turns the `{expression}` parts of an MDX page into plain text for /llms-full.txt, using the numbers the page itself reads:
 * its JSON imports and its `export const` lines. An expression that cannot be evaluated is dropped, never printed raw.
 * Used only for the plain-text export; the rendered page is built by Astro as before.
 */
import { readFileSync } from 'node:fs';

type Scope = Record<string, unknown>;

function run(expr: string, scope: Scope): unknown {
  const names = Object.keys(scope);
  // eslint-disable-next-line no-new-func
  return new Function(...names, `return (${expr});`)(...names.map((n) => scope[n]));
}

/** The page's data: JSON imports resolved against the page file, then its `export const` lines in order. */
export function pageScope(body: string, pageUrl: URL): Scope {
  const scope: Scope = {};
  for (const m of body.matchAll(/^import\s+(\w+)\s+from\s+'([^']+\.json)';?\s*$/gm)) {
    try {
      scope[m[1]] = JSON.parse(readFileSync(new URL(m[2], pageUrl), 'utf8'));
    } catch {
      // A missing data file leaves the names undefined; their expressions are dropped below.
    }
  }
  for (const m of body.matchAll(/^export const (\w+) = ([\s\S]*?);[ \t]*$/gm)) {
    try {
      scope[m[1]] = run(m[2], scope);
    } catch {
      // Skip what cannot be evaluated.
    }
  }
  return scope;
}

/** The index of the brace that closes the one opened at `open`, skipping strings and nested braces; -1 when unbalanced. */
function close(s: string, open: number): number {
  let depth = 0;
  let quote = '';
  for (let i = open; i < s.length; i++) {
    const c = s[i];
    if (quote) {
      if (c === '\\') i++;
      else if (c === quote) quote = '';
    } else if (c === "'" || c === '"' || c === '`') quote = c;
    else if (c === '{') depth++;
    else if (c === '}' && --depth === 0) return i;
  }
  return -1;
}

function show(v: unknown): string {
  return v === undefined || v === null || v === false ? '' : String(v);
}

function fill(text: string, scope: Scope): string {
  let out = '';
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf('{', i);
    if (open < 0) break;
    const end = close(text, open);
    if (end < 0) break;
    out += text.slice(i, open);
    const expr = text.slice(open + 1, end).trim();
    const map = /^([\s\S]+?)\.map\(\(\[([\w\s,]+)\]\) => \(\s*([\s\S]*?)\s*\)\)$/.exec(expr);
    try {
      if (map) {
        const names = map[2].split(',').map((n) => n.trim());
        const rows = run(map[1], scope) as unknown[][];
        out += rows.map((row) => fill(map[3], { ...scope, ...Object.fromEntries(names.map((n, k) => [n, row[k]])) })).join('\n');
      } else out += show(run(expr, scope));
    } catch {
      // Dropped: a half-evaluated expression is worse than a gap.
    }
    i = end + 1;
  }
  return out + text.slice(i);
}

/** `{expression}` parts evaluated outside code; fences and inline code are kept. */
export function evaluateExpressions(body: string, scope: Scope): string {
  return body
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, k) => (k % 2 ? part : fill(part, scope)))
    .join('')
    .replace(/^export const (\w+) = [\s\S]*?;[ \t]*$/gm, '');
}
