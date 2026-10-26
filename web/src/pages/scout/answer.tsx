import { Fragment, useState, type ReactNode } from "react";
import type { Citation, ServedBy } from "@/lib/api";
import { WhoBadge } from "@/components/ui/who";
import { cn, safeHref } from "@/lib/utils";
import { Domain, WebLink } from "./bits";

/**
 * Minimal, safe markdown for the Writer's answer, built from React elements (React escapes all text).
 * Supports paragraphs, bullet and numbered lists, simple headings, fenced and inline code,
 * **bold**, *italic*, and citation markers like [1] or [1, 3] that link to the citation URLs.
 */
type Block =
  | { type: "p"; lines: string[] }
  | { type: "h"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[]; start: number }
  | { type: "code"; text: string };

const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;
const FENCE = /^\s*```/;

export function parseBlocks(src: string): Block[] {
  const lines = String(src ?? "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (FENCE.test(line)) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !FENCE.test(lines[i])) body.push(lines[i++]);
      i++; // closing fence (or end)
      out.push({ type: "code", text: body.join("\n") });
    } else if (!line.trim()) {
      i++;
    } else if (BULLET.test(line)) {
      const items: string[] = [];
      while (i < lines.length && BULLET.test(lines[i])) items.push(lines[i++].replace(BULLET, "$1"));
      out.push({ type: "ul", items });
    } else if (ORDERED.test(line)) {
      const start = Number(line.match(ORDERED)?.[1] ?? 1);
      const items: string[] = [];
      while (i < lines.length && ORDERED.test(lines[i])) items.push(lines[i++].replace(ORDERED, "$2"));
      out.push({ type: "ol", items, start });
    } else if (HEADING.test(line)) {
      out.push({ type: "h", text: line.replace(HEADING, "$1") });
      i++;
    } else {
      const ls: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !BULLET.test(lines[i]) &&
        !ORDERED.test(lines[i]) &&
        !FENCE.test(lines[i]) &&
        !HEADING.test(lines[i])
      )
        ls.push(lines[i++]);
      out.push({ type: "p", lines: ls });
    }
  }
  return out;
}

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[\d+(?:\s*,\s*\d+)*\])/g;

interface InlineCtx {
  cites: Map<number, Citation>;
  onCite: (n: number | null) => void;
}

function Cite({ n, c, ctx }: { n: number; c: Citation | undefined; ctx: InlineCtx }) {
  const href = safeHref(c?.url);
  const cls =
    "mx-px inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-md px-1 align-[2px] font-mono text-[11px] font-semibold leading-none";
  if (!c || !href) return <span className={cn(cls, "bg-surface-2 text-muted")}>{n}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={c.title || href}
      aria-label={`Source ${n}: ${c.title || href} (opens in a new tab)`}
      onMouseEnter={() => ctx.onCite(n)}
      onMouseLeave={() => ctx.onCite(null)}
      onFocus={() => ctx.onCite(n)}
      onBlur={() => ctx.onCite(null)}
      className={cn(cls, "bg-owned-soft text-owned-ink no-underline transition-colors hover:bg-owned hover:text-white dark:hover:text-[#06101f]")}
    >
      {n}
    </a>
  );
}

function inline(text: string, ctx: InlineCtx, key = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const tok = m[0];
    const id = `${key}-${k++}`;
    if (m[1])
      out.push(
        <code key={id} className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.88em] text-ink">
          {tok.slice(1, -1)}
        </code>,
      );
    else if (m[2])
      out.push(
        <strong key={id} className="font-semibold text-ink">
          {inline(tok.slice(2, -2), ctx, id)}
        </strong>,
      );
    else if (m[3]) out.push(<em key={id}>{inline(tok.slice(1, -1), ctx, id)}</em>);
    else if (m[4]) {
      const ns = tok
        .slice(1, -1)
        .split(/\s*,\s*/)
        .map(Number);
      out.push(
        <span key={id} className="whitespace-nowrap">
          {ns.map((n) => (
            <Cite key={n} n={n} c={ctx.cites.get(n)} ctx={ctx} />
          ))}
        </span>,
      );
    }
    last = at + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export interface SourceFact {
  fact: string;
  servedBy: ServedBy;
  model: string;
  /** The call's route; `demo_split` is labelled on its badge. */
  route?: string;
}

export function AnswerBody({
  answer,
  citations,
  facts,
  className,
}: {
  answer: string;
  citations: Citation[];
  facts?: Record<string, SourceFact>;
  className?: string;
}) {
  const [hot, setHot] = useState<number | null>(null);
  const cites = new Map<number, Citation>();
  for (const c of citations ?? []) if (c && typeof c.n === "number") cites.set(c.n, c);
  const ctx: InlineCtx = { cites, onCite: setHot };
  const blocks = parseBlocks(answer);
  const sources = [...cites.values()].sort((a, b) => a.n - b.n);

  return (
    <div className={className}>
      <div className="space-y-4 text-pretty text-[17px] leading-[1.7] text-ink-2 [overflow-wrap:anywhere] sm:text-[18px]">
        {blocks.map((b, i) => {
          const key = `b${i}`;
          if (b.type === "code")
            return (
              <pre key={key} className="overflow-x-auto rounded-xl border border-line bg-bg-sunken p-4 font-mono text-[13px] leading-6 text-ink-2">
                <code>{b.text}</code>
              </pre>
            );
          if (b.type === "h")
            return (
              <p key={key} className="font-semibold text-ink">
                {inline(b.text, ctx, key)}
              </p>
            );
          if (b.type === "ul")
            return (
              <ul key={key} className="list-disc space-y-1.5 pl-6 marker:text-muted">
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it, ctx, `${key}-${j}`)}</li>
                ))}
              </ul>
            );
          if (b.type === "ol")
            return (
              <ol key={key} start={b.start} className="list-decimal space-y-1.5 pl-6 marker:text-muted">
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it, ctx, `${key}-${j}`)}</li>
                ))}
              </ol>
            );
          return (
            <p key={key}>
              {b.lines.map((l, j) => (
                <Fragment key={j}>
                  {j > 0 && <br />}
                  {inline(l, ctx, `${key}-${j}`)}
                </Fragment>
              ))}
            </p>
          );
        })}
      </div>

      {sources.length > 0 && (
        <div className="mt-7 border-t border-line pt-5">
          <h3 className="font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">Sources</h3>
          <ol className="mt-3 space-y-2">
            {sources.map((c) => (
              <li
                key={c.n}
                className={cn("-mx-2 flex items-baseline gap-3 rounded-lg px-2 py-2 text-[14px] transition-colors", hot === c.n && "bg-owned-soft")}
              >
                <span className="inline-flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-md bg-surface-2 px-1 font-mono text-[11px] font-semibold text-ink-2">
                  {c.n}
                </span>
                <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
                  <WebLink url={c.url} className="font-medium text-ink">
                    {c.title || c.url}
                  </WebLink>{" "}
                  <Domain url={c.url} />
                  {facts?.[c.url] && (
                    <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] leading-snug text-muted">
                      <span>Key fact read by</span>
                      <WhoBadge servedBy={facts[c.url].servedBy} model={facts[c.url].model} route={facts[c.url].route} />
                      <span className="basis-full text-ink-2">{facts[c.url].fact}</span>
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
