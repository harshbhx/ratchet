import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { ChevronsDownUp, ChevronsUpDown, Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { cn, fmtInt } from "@/lib/utils";
import { JUDGE_CHAPTERS, type ChapterGroup } from "./chapters";
import { JudgesProvider, useJudges } from "./data";
import { Legend, StartHere, WhatsReal } from "./start";
import { ExamsChapter } from "./exams";
import { MeasuredChapter } from "./measured";
import { LiveSince, MEASURED } from "@/components/measured/measured";
import { nightSchoolWording } from "@/lib/measured-model";
import { ApiChapter, ClientChapter, FeaturesChapter } from "./outside";
import { ArchitectureChapter, CascadeChapter, LedgerChapter, ReleaseChapter } from "./inside-a";
import { CertificateChapter, ProofChapter, ReceiptsChapter, WatchChapter } from "./inside-b";
import { CostChapter, PrivacyChapter, ResilienceChapter } from "./inside-c";
import { CheckChapter, LimitsChapter, SponsorsChapter } from "./check";
import { ChapterProvider, useChapters } from "./ui";
import { ChecklistSection, LinksSection, PackHero, ProofSection } from "./pack";
import { JUDGES_VIDEO } from "./real";
import { JudgeMode } from "./judge-mode";

/** The page's sections, in order: what the sticky bar links to and what each section's header says. */
const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "proof", label: "Proof" },
  { id: "links", label: "Links" },
  { id: "checklist", label: "Checklist" },
  { id: "group-results", label: "Results" },
  { id: "group-outside", label: "Use it" },
  { id: "group-inside", label: "Inside" },
  { id: "group-check", label: "Check it" },
] as const;

/** The section in the reading band of the screen (in memory only). */
function useCurrentSection(ids: string[]) {
  const [current, setCurrent] = useState(ids[0]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const visible = new Set<string>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.add(e.target.id);
          else visible.delete(e.target.id);
        }
        const first = ids.find((id) => visible.has(id));
        if (first) setCurrent(first);
      },
      { rootMargin: "-25% 0px -60% 0px" },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [ids]);
  return current;
}

/** The site header's height, measured, for the sticky offset. */
function useHeaderHeight(): number {
  const [h, setH] = useState(64);
  useEffect(() => {
    const el = document.querySelector("header");
    if (!el) return;
    const measure = () => setH(Math.round(el.getBoundingClientRect().height));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return h;
}

function scrollToId(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  window.setTimeout(() => (document.getElementById(`${id}-title`) as HTMLElement | null)?.focus({ preventScroll: true }), reduce ? 0 : 450);
}

/** One sticky bar for every screen size: five sections, the guided tour on the right. */
function SectionBar({ current, top, onTour }: { current: string; top: number; onTour: () => void }) {
  return (
    <div data-tour-inset style={{ top }} className="sticky z-30 border-b border-line bg-bg/85 backdrop-blur-xl">
      <Container className="flex h-12 items-center gap-2">
        <nav aria-label="Sections" className="-mx-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto px-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {SECTIONS.map((s) => {
            const on = current === s.id;
            return (
              <a
                key={s.id}
                href={`#${s.id}`}
                aria-current={on ? "location" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  scrollToId(s.id);
                }}
                className={cn(
                  "inline-flex h-8 shrink-0 items-center rounded-full px-3.5 text-[13.5px] font-medium transition-colors",
                  on ? "bg-ink text-bg" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )}
              >
                {s.label}
              </a>
            );
          })}
        </nav>
        <button
          type="button"
          onClick={onTour}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 text-[13px] font-medium text-ink transition-colors hover:bg-surface-2"
        >
          <Compass className="size-3.5" aria-hidden />
          <span className="hidden min-[420px]:inline">Guided tour</span>
          <span className="min-[420px]:hidden">Tour</span>
        </button>
      </Container>
    </div>
  );
}

function GatewayLine() {
  const { stats, statsRead, status } = useJudges();
  if (statsRead.state === "loading") return <p className="text-[13px] text-muted">Connecting to this gateway…</p>;
  if (!stats)
    return (
      <p className="text-[13px] leading-relaxed text-ink-2">
        No gateway answered this page, so the live chapters say "not available". Run <code className="font-mono">RATCHET_MODE=mock uvicorn ratchet.gateway:app</code> and open{" "}
        <code className="font-mono">/judges</code> on it.
      </p>
    );
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[13px] leading-relaxed text-ink-2">
      {status && <RealBadge status={status} />}
      <span>
        Live chapters read the gateway serving this page: {stats.mode === "mock" ? "a mock gateway" : "a live gateway"}
        {stats.totals.requests > 0
          ? `, day ${fmtInt(stats.day)}. The totals for all traffic since the first record are in the live strip.`
          : ". Its own ledger starts empty after every restart, so those chapters show dated, measured runs where it has nothing yet."}
      </span>
    </p>
  );
}

/** A group of chapters: its header, a one-line purpose and, for folding groups, "Open all" and "Close all". */
function Group({ id, title, purpose, group, children }: { id: string; title: string; purpose: ReactNode; group: ChapterGroup; children: ReactNode }) {
  const ctl = useChapters();
  const ids = useMemo(() => JUDGE_CHAPTERS.filter((c) => c.group === group).map((c) => c.id), [group]);
  const folds = ["outside", "inside"].includes(group);
  const allOpen = !!ctl && ids.every((i) => ctl.open.has(i));
  return (
    <section id={id} aria-label={title} className="scroll-mt-28 pt-12 sm:pt-16">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-[62ch]">
          <p className="text-[26px] font-semibold leading-tight tracking-[-0.025em] text-ink sm:text-[32px]">{title}</p>
          <p className="mt-1.5 text-pretty text-[15.5px] leading-relaxed text-ink-2">{purpose}</p>
        </div>
        {folds && ctl && (
          <Button variant="outline" size="sm" onClick={() => ctl.setMany(ids, !allOpen)} aria-label={`${allOpen ? "Close" : "Open"} all ${ids.length} chapters in ${title}`}>
            {allOpen ? <ChevronsDownUp aria-hidden /> : <ChevronsUpDown aria-hidden />}
            {allOpen ? "Close all" : "Open all"}
          </Button>
        )}
      </div>
      <div className={cn(folds ? "space-y-3" : "divide-y divide-line")}>{children}</div>
    </section>
  );
}

function Page() {
  const ids = useMemo(() => SECTIONS.map((s) => s.id) as string[], []);
  const current = useCurrentSection(ids);
  const headerH = useHeaderHeight();
  const { stats } = useJudges();
  const [, setParams] = useSearchParams();

  useEffect(() => {
    // The layout names the page after its own effect runs; this one runs after it.
    const id = window.setTimeout(() => (document.title = "For judges · Ratchet"), 0);
    return () => window.clearTimeout(id);
  }, []);

  const startTour = () =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        n.set("tour", "1");
        return n;
      },
      { replace: true },
    );

  return (
    <div className="relative overflow-x-clip">
      <div id="overview" className="scroll-mt-28">
        <PackHero video={JUDGES_VIDEO ?? "https://video.ratchet.dualmindlab.tech/?from=judges"}>
          <GatewayLine />
          <LiveSince />
          <JudgeMode mode={stats?.mode ?? null} />
        </PackHero>
      </div>

      <SectionBar current={current} top={headerH} onTour={startTour} />

      <Container className="pb-24">
        <ProofSection />
        <LinksSection />
        <ChecklistSection />

        <div className="mt-16 border-t border-line pt-10 sm:mt-20">
          <p className="text-[26px] font-semibold leading-tight tracking-[-0.025em] text-ink sm:text-[32px]">Go deeper: the console</p>
          <p className="mt-1.5 max-w-[62ch] text-pretty text-[15.5px] leading-relaxed text-ink-2">
            The same claims, chapter by chapter, with the live gateway's own numbers. Every claim carries one status.
          </p>
          <div className="mt-4 rounded-2xl border border-line bg-bg-sunken/50 px-4 py-3.5 sm:px-5">
            <p className="mb-2.5 text-[13px] font-medium text-ink">Status key</p>
            <Legend />
          </div>
        </div>

        <StartHere />

        <Group id="group-results" title="Results" group="start" purpose="What was measured, and what every claim on this site rests on.">
          <ExamsChapter />
          <MeasuredChapter />
          <WhatsReal />
        </Group>

        <Group id="group-outside" title="Use it" group="outside" purpose="How an app calls Ratchet: one changed line, then the API it serves. Open a chapter to read it.">
          <ClientChapter />
          <ApiChapter />
          <FeaturesChapter />
        </Group>

        <Group
          id="group-inside"
          title="Inside the gateway"
          group="inside"
          purpose={`The loop built to train a new small model each night and release it safely, read live from this gateway. ${MEASURED ? nightSchoolWording(MEASURED).pipeline : ""} Open what you care about.`}
        >
          <ArchitectureChapter />
          <CascadeChapter />
          <LedgerChapter />
          <ReleaseChapter />
          <CertificateChapter />
          <ProofChapter />
          <ReceiptsChapter />
          <WatchChapter />
          <PrivacyChapter />
          <ResilienceChapter />
          <CostChapter />
        </Group>

        <Group id="group-check" title="Check it" group="check" purpose="Commands to reproduce the claims, what each sponsor technology does here, and what this is not yet.">
          <CheckChapter />
          <SponsorsChapter />
          <LimitsChapter />
        </Group>
      </Container>
    </div>
  );
}

/** The judges' console: the hackathon checklist, the results, the gateway's own endpoints chapter by chapter, and a guided tour. */
export default function JudgesPage() {
  return (
    <JudgesProvider>
      <ChapterProvider>
        <Page />
      </ChapterProvider>
    </JudgesProvider>
  );
}
