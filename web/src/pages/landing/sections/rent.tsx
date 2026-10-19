import { useRef } from "react";
import { motion, useInView } from "motion/react";
import { Container } from "@/components/ui/section";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Chapter, ChapterHead, ExtLink, LINKS, Lead, Reveal } from "../shared";

type Shape = "ticket-triage" | "source-triage" | "page-read" | "research-plan" | "research-write";
const NARROW: Shape[] = ["ticket-triage", "source-triage", "page-read"];

// An illustration, not data: a stream of calls where three narrow shapes repeat.
const CALLS: Shape[] = (
  "ttsptt rtsptt pttstw tsttps ttpsts"
    .replace(/\s/g, "")
    .split("") as ("t" | "s" | "p" | "r" | "w")[]
).map((c) => ({ t: "ticket-triage", s: "source-triage", p: "page-read", r: "research-plan", w: "research-write" })[c] as Shape);

const COUNTS = CALLS.reduce<Record<string, number>>((m, c) => ((m[c] = (m[c] || 0) + 1), m), {});

function CallGrid() {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  return (
    <div ref={ref} className="rounded-[22px] border border-line bg-surface p-4 shadow-card sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] font-medium text-ink">Your app's model calls</p>
        <Badge tone="outline">Illustration</Badge>
      </div>
      <ul className="mt-4 grid grid-cols-2 gap-1.5 min-[480px]:grid-cols-3 sm:gap-2" aria-label="Illustration of a stream of model calls, most of them the same three narrow tasks">
        {CALLS.map((c, i) => {
          const narrow = NARROW.includes(c);
          return (
            <motion.li
              key={i}
              initial={{ opacity: 0, y: 6 }}
              animate={inView ? { opacity: 1, y: 0 } : undefined}
              transition={{ duration: 0.4, delay: 0.1 + i * 0.03, ease: [0.22, 1, 0.36, 1] }}
              className={cn(
                "flex min-w-0 items-center gap-1.5 rounded-lg border px-2.5 py-[7px] font-mono text-[11px] leading-none",
                narrow ? "border-line bg-surface-2 text-ink-2" : "border-dashed border-line-strong bg-surface text-muted",
                i >= 16 && "hidden min-[480px]:flex",
              )}
            >
              <span className="size-1.5 shrink-0 rounded-full bg-rented" aria-hidden />
              <span className="truncate">{c}</span>
            </motion.li>
          );
        })}
      </ul>
      <div className="mt-5 space-y-2.5 border-t border-line pt-4">
        {(["ticket-triage", "source-triage", "page-read"] as Shape[]).map((s) => (
          <div key={s} className="grid grid-cols-[8.5rem_minmax(0,1fr)_2rem] items-center gap-3 text-[12px]">
            <span className="truncate font-mono text-ink-2">{s}</span>
            <span className="h-1.5 overflow-hidden rounded-full bg-bg-sunken">
              <motion.span
                className="block h-full rounded-full bg-ink-2"
                initial={{ width: 0 }}
                animate={inView ? { width: `${(COUNTS[s] / CALLS.length) * 100}%` } : undefined}
                transition={{ duration: 0.9, delay: 1.2, ease: [0.22, 1, 0.36, 1] }}
              />
            </span>
            <span className="text-right font-mono text-muted tabular">{COUNTS[s]}</span>
          </div>
        ))}
        <div className="grid grid-cols-[8.5rem_minmax(0,1fr)_2rem] items-center gap-3 text-[12px]">
          <span className="truncate text-muted">open-ended</span>
          <span className="h-1.5 overflow-hidden rounded-full bg-bg-sunken">
            <motion.span
              className="block h-full rounded-full bg-line-strong"
              initial={{ width: 0 }}
              animate={inView ? { width: `${(((COUNTS["research-plan"] || 0) + (COUNTS["research-write"] || 0)) / CALLS.length) * 100}%` } : undefined}
              transition={{ duration: 0.9, delay: 1.3, ease: [0.22, 1, 0.36, 1] }}
            />
          </span>
          <span className="text-right font-mono text-muted tabular">{(COUNTS["research-plan"] || 0) + (COUNTS["research-write"] || 0)}</span>
        </div>
      </div>
      <p className="mt-4 flex items-start gap-2 text-[12px] leading-relaxed text-muted">
        <span className="mt-[5px] size-1.5 shrink-0 rounded-full bg-rented" aria-hidden />
        Each chip is one call, billed per token by a rented model. Most are the same three narrow shapes; a few are open-ended.
      </p>
    </div>
  );
}

export function Rent() {
  return (
    <Chapter id="rent">
      <Container>
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)] lg:items-center lg:gap-16">
          <div>
            <ChapterHead
              id="rent"
              title="Every request is rent."
              lead={
                <>
                  <Lead>
                    You pay a frontier model per token, every time. The bill grows with every user, and none of what it learns from your
                    traffic stays with you.
                  </Lead>
                  <Lead>
                    Yet most app and agent traffic is narrow and repetitive: the same few kinds of request, asked again and again. A small
                    model can learn those.
                  </Lead>
                </>
              }
            />
            <div className="mt-10 grid gap-4 sm:grid-cols-2">
              <Reveal delay={0.05}>
                <figure className="h-full rounded-2xl border border-line bg-surface p-5 shadow-card">
                  <div className="text-[34px] font-semibold leading-none tracking-[-0.035em] text-ink tabular">$12.5B</div>
                  <figcaption className="mt-3 text-[14px] leading-relaxed text-ink-2">
                    spent by enterprises on model APIs in 2025, out of $37B on generative AI.
                    <span className="mt-2 block text-[13px] text-muted">
                      Source: <ExtLink href={LINKS.menlo}>Menlo Ventures</ExtLink>
                    </span>
                  </figcaption>
                </figure>
              </Reveal>
              <Reveal delay={0.12}>
                <figure className="h-full rounded-2xl border border-line bg-surface p-5 shadow-card">
                  <div className="text-[34px] font-semibold leading-none tracking-[-0.035em] text-ink tabular">40–70%</div>
                  <figcaption className="mt-3 text-[14px] leading-relaxed text-ink-2">
                    of LLM calls in agent systems could go to small models, which are 10–30x cheaper to serve.
                    <span className="mt-2 block text-[13px] text-muted">
                      Source: <ExtLink href={LINKS.nvidiaSlm}>NVIDIA Research</ExtLink>
                    </span>
                  </figcaption>
                </figure>
              </Reveal>
            </div>
          </div>
          <Reveal delay={0.1}>
            <CallGrid />
          </Reveal>
        </div>
      </Container>
    </Chapter>
  );
}
