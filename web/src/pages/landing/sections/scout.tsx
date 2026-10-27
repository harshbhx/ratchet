import { ArrowRight, BookOpen, Filter, ListTree, PenLine, Search, type LucideIcon } from "lucide-react";
import type { ServedBy } from "@/lib/api";
import { GlowOrbs } from "@/components/fx/glow-orbs";
import { Magnet } from "@/components/fx/magnet";
import { TiltCard } from "@/components/fx/tilt-card";
import { ButtonLink } from "@/components/ui/button";
import { Container } from "@/components/ui/section";
import { WhoBadge, useWho } from "@/components/ui/who";
import { cn } from "@/lib/utils";
import { Chapter, ChapterHead, ExtLink, LINKS, Lead, Reveal } from "../shared";

const AGENTS: { name: string; icon: LucideIcon; task: string | null; calls: string; who: ServedBy; route: string; why: string }[] = [
  { name: "Planner", icon: ListTree, task: "research-plan", calls: "1 call", who: "teacher", route: "Pinned", why: "Open-ended: turns a question into three searches." },
  { name: "Searcher", icon: Search, task: null, calls: "1 per query", who: "tool", route: "Tool", why: "Tavily Search API. Not a model call." },
  { name: "Triage", icon: Filter, task: "source-triage", calls: "1 per result, up to 12", who: "student", route: "Distilled", why: "Short label: relevant or not, and what kind of source." },
  { name: "Reader", icon: BookOpen, task: "page-read", calls: "1 per relevant result, up to 6", who: "student", route: "Distilled", why: "Pulls one key fact from each relevant page." },
  { name: "Writer", icon: PenLine, task: "research-write", calls: "1 call", who: "teacher", route: "Pinned", why: "Open-ended: writes the cited answer." },
];

function Pipeline() {
  const who = useWho();
  return (
    <ol className="grid grid-cols-1 gap-3 md:grid-cols-5 md:gap-4">
      {AGENTS.map((a, i) => {
        // The owned blue only while our adapter answers the distilled agents' calls.
        const tone = a.who === "student" ? (who.hosted ? "bg-line-strong" : "bg-owned") : a.who === "teacher" ? "bg-rented" : "bg-line-strong";
        const last = i === AGENTS.length - 1;
        return (
          <Reveal as="li" key={a.name} delay={i * 0.07} className="relative">
            {/* mobile connector */}
            {!last && <span aria-hidden className="absolute bottom-[-12px] left-[27px] top-14 w-px bg-line-strong md:hidden" />}
            {/* desktop arrow in the gap */}
            {!last && (
              <span
                aria-hidden
                className="absolute -right-[13px] top-[34px] z-10 hidden size-[18px] items-center justify-center rounded-full border border-line bg-bg text-muted md:flex"
              >
                <ArrowRight className="size-3" />
              </span>
            )}
            <TiltCard className="h-full" max={5}>
              <div className="relative flex h-full gap-3.5 rounded-2xl border border-line bg-surface p-4 shadow-card md:flex-col md:gap-0 lg:p-5">
                <span className={cn("absolute inset-y-4 left-0 w-[3px] rounded-r-full md:inset-x-5 md:inset-y-auto md:top-0 md:h-[3px] md:w-auto md:rounded-b-full md:rounded-r-none", tone)} aria-hidden />
                <span className="relative z-10 inline-flex size-10 shrink-0 items-center justify-center rounded-xl border border-line bg-surface-2 text-ink-2">
                  <a.icon className="size-[18px]" aria-hidden />
                </span>
                <div className="min-w-0 flex-1 md:mt-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                    <h3 className="text-[16px] font-semibold text-ink">{a.name}</h3>
                    <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-muted">{a.route}</span>
                  </div>
                  <code className="mt-1 block font-mono text-[12px] text-ink-2">{a.task ?? "tool call"}</code>
                  <div className="mt-3">
                    {/* Who answers this agent's calls now: our adapter, or the hosted fallback while the endpoint is off. */}
                    <WhoBadge servedBy={a.who} model={a.who === "tool" ? "tavily" : a.who === "student" ? who.serving : undefined} />
                  </div>
                  <p className="mt-3 text-[13px] leading-relaxed text-ink-2">{a.why}</p>
                  <p className="mt-2 font-mono text-[11.5px] text-muted">{a.calls}</p>
                </div>
              </div>
            </TiltCard>
          </Reveal>
        );
      })}
    </ol>
  );
}

export function Scout() {
  return (
    <Chapter id="scout">
      <GlowOrbs tones={["owned", "rented", "owned"]} className="[mask-image:linear-gradient(to_bottom,transparent,black_30%,black_70%,transparent)]" />
      <Container className="relative">
        <ChapterHead
          id="scout"
          title="On real agent traffic."
          lead={
            <>
              <Lead>
                Scout is a small team of research agents that answers a question from the web. Every agent is a plain OpenAI client pointed at
                Ratchet, with a task name.
              </Lead>
              <Lead>
                The Planner and Writer are open-ended, so they stay pinned to Ultra and never become training data. Triage and Reader make many
                short, narrow calls. Those are what the student learns: the kind of call{" "}
                <ExtLink href={LINKS.nvidiaSlm}>NVIDIA Research</ExtLink> estimates makes up 40–70% of agent traffic.
              </Lead>
            </>
          }
        />
        <div className="mt-12">
          <Pipeline />
        </div>
        <div className="mt-6">
          <Reveal delay={0.1} className="flex flex-col justify-between gap-6 rounded-[22px] border border-line bg-surface-2/50 p-5 sm:p-6">
            <p className="text-pretty text-[17px] leading-relaxed text-ink-2">
              Ask Scout a question and watch every call arrive, each tagged with who answered it and what it cost against Ultra alone.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Magnet>
                <ButtonLink to="/scout" size="lg">
                  Try Scout live <ArrowRight />
                </ButtonLink>
              </Magnet>
            </div>
          </Reveal>
        </div>
      </Container>
    </Chapter>
  );
}
