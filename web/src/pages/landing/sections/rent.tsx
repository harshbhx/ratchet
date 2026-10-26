import { useRef } from "react";
import { motion, useInView } from "motion/react";
import { Compare } from "@/components/fx/compare";
import { GlowOrbs } from "@/components/fx/glow-orbs";
import { Highlight } from "@/components/fx/highlight";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { Container } from "@/components/ui/section";
import { Badge } from "@/components/ui/badge";
import { SIM_DAYS, SIM_SOURCE } from "@/lib/sim";
import { cn, fmtPct, fmtUsd } from "@/lib/utils";
import { EASE } from "@/lib/motion";
import { WHO_TEXT } from "@/lib/who-model";
import { Chapter, ChapterHead, ExtLink, LINKS, Lead, Reveal, SimBadge } from "../shared";

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
    <SpotlightCard tone="rented" className="h-full">
      <div ref={ref} className="p-4 sm:p-6">
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
                transition={{ duration: 0.4, delay: 0.1 + i * 0.03, ease: EASE }}
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
                  transition={{ duration: 0.9, delay: 1.2, ease: EASE }}
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
                transition={{ duration: 0.9, delay: 1.3, ease: EASE }}
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
    </SpotlightCard>
  );
}

/**
 * One side of the slider: the same day of tickets, billed one way. The two panels share a layout, with the all-Ultra
 * bill's text on the left and the Ratchet bill's on the right, so the handle reveals one without covering the other.
 */
function Bill({ title, who, tone, costPer1k, student, note }: { title: string; who: string; tone: "rented" | "owned"; costPer1k: number; student: number; note: string }) {
  const owned = tone === "owned";
  return (
    <div className={cn("flex h-[320px] flex-col justify-between p-5 pt-12 sm:p-7 sm:pt-12", owned ? "items-end bg-surface text-right" : "bg-bg-sunken")}>
      <div className={cn("max-w-[60%]", owned && "flex flex-col items-end")}>
        <p className="font-mono text-[11.5px] uppercase tracking-[0.12em] text-muted">{title}</p>
        <p className={cn("mt-2 text-[40px] font-semibold leading-none tracking-[-0.04em] tabular sm:text-[52px]", owned ? "text-owned-ink" : "text-rented-ink")}>
          {fmtUsd(costPer1k, 3)}
        </p>
        <p className="mt-1.5 text-[13px] text-muted">per 1,000 requests</p>
      </div>
      <div className="w-full">
        <div className={cn("flex items-center justify-between text-[12.5px] text-ink-2", owned && "flex-row-reverse")}>
          <span>{who}</span>
          <span className="font-mono tabular">{fmtPct(owned ? student : 1 - student)}</span>
        </div>
        <div className={cn("mt-2 flex h-2.5 overflow-hidden rounded-full bg-rented", owned && "justify-end")} aria-hidden>
          <span className="h-full rounded-full bg-owned" style={{ width: `${(owned ? student : 0) * 100}%` }} />
        </div>
        {/* The handle starts at 58%: each caption stays on its own side of it, so neither is cut mid-word at first sight. */}
        <p className={cn("mt-2.5 text-pretty text-[12px] leading-relaxed text-muted", owned ? "ml-auto max-w-[36%]" : "max-w-[50%]")}>{note}</p>
      </div>
    </div>
  );
}

function TwoBills() {
  const first = SIM_DAYS[0];
  const last = SIM_DAYS[SIM_DAYS.length - 1];
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">The same 2,000 tickets, two bills</h3>
          <p className="mt-1 text-[13px] text-muted">Drag the handle. Left: every request answered by Ultra. Right: day {last.day} of the reference simulation.</p>
        </div>
        <SimBadge>Simulation · reference run</SimBadge>
      </div>
      <Compare
        ariaLabel="How much of the card shows the all-Ultra bill versus the Ratchet bill"
        beforeLabel="All-Ultra"
        afterLabel={`With Ratchet · day ${last.day}`}
        initial={58}
        before={
          <Bill
            title="All-Ultra"
            who={WHO_TEXT.rented}
            tone="rented"
            costPer1k={first.baselinePer1k}
            student={0}
            note="Every request is billed per token by the teacher. The bill grows with every user."
          />
        }
        after={
          <Bill
            title={`With Ratchet, day ${last.day}`}
            who={WHO_TEXT.owned}
            tone="owned"
            costPer1k={last.costPer1k}
            student={last.student}
            note={`${fmtPct(last.student)} answered by the adapter you own; ${fmtPct(last.escalated)} still go to the teacher. ${fmtPct(last.saved)} saved that day.`}
          />
        }
      />
      <p className="mt-3 text-[12px] leading-relaxed text-muted">
        {SIM_SOURCE} Priced per token at unverified list prices; the honest bill below prices the same traffic by GPU-hours too.
      </p>
    </div>
  );
}

export function Rent() {
  return (
    <Chapter id="rent">
      <GlowOrbs tones={["rented"]} className="[mask-image:linear-gradient(to_bottom,black,transparent_85%)]" />
      <Container className="relative">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)] lg:items-center lg:gap-16">
          <div>
            <ChapterHead
              id="rent"
              title={
                <>
                  Every request is <Highlight tone="rented">rent.</Highlight>
                </>
              }
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
                <SpotlightCard as="div" className="h-full rounded-2xl">
                  <figure className="p-5">
                    <div className="text-[34px] font-semibold leading-none tracking-[-0.035em] text-ink tabular">$12.5B</div>
                    <figcaption className="mt-3 text-[14px] leading-relaxed text-ink-2">
                      spent by enterprises on model APIs in 2025, out of $37B on generative AI.
                      <span className="mt-2 block text-[13px] text-muted">
                        Source: <ExtLink href={LINKS.menlo}>Menlo Ventures</ExtLink>
                      </span>
                    </figcaption>
                  </figure>
                </SpotlightCard>
              </Reveal>
              <Reveal delay={0.12}>
                <SpotlightCard as="div" className="h-full rounded-2xl">
                  <figure className="p-5">
                    <div className="text-[34px] font-semibold leading-none tracking-[-0.035em] text-ink tabular">40–70%</div>
                    <figcaption className="mt-3 text-[14px] leading-relaxed text-ink-2">
                      of LLM calls in agent systems could go to small models, which are 10–30x cheaper to serve.
                      <span className="mt-2 block text-[13px] text-muted">
                        Source: <ExtLink href={LINKS.nvidiaSlm}>NVIDIA Research</ExtLink>
                      </span>
                    </figcaption>
                  </figure>
                </SpotlightCard>
              </Reveal>
            </div>
          </div>
          <Reveal delay={0.1}>
            <CallGrid />
          </Reveal>
        </div>
        <Reveal className="mt-14 sm:mt-16">
          <TwoBills />
        </Reveal>
      </Container>
    </Chapter>
  );
}
