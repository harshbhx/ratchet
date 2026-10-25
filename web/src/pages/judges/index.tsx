import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { ChevronsDownUp, ChevronsUpDown, Compass, LayoutDashboard } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { cn, fmtInt } from "@/lib/utils";
import { JUDGE_CHAPTERS, type ChapterGroup } from "./chapters";
import { JudgesProvider, useJudges } from "./data";
import { Legend, StartHere, WhatsReal } from "./start";
import { TrainingStatus } from "./training-status";
import { ExamsChapter } from "./exams";
import { ApiChapter, ClientChapter, FeaturesChapter } from "./outside";
import { ArchitectureChapter, CascadeChapter, LedgerChapter, ReleaseChapter } from "./inside-a";
import { CertificateChapter, ProofChapter, ReceiptsChapter, WatchChapter } from "./inside-b";
import { CostChapter, PrivacyChapter, ResilienceChapter } from "./inside-c";
import { CheckChapter, LimitsChapter, SponsorsChapter } from "./check";
import { ChapterProvider, useChapters } from "./ui";
import { StageOneCard } from "./stage-one";

/** The page's sections, in order: what the sticky bar links to and what each section's header says. */
const SECTIONS = [
  { id: "overview", label: "Overview" },
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

/** Opens a chapter, then scrolls to its head; the same for a plain section. */
function useGo() {
  const ctl = useChapters();
  return (id: string) => {
    if (JUDGE_CHAPTERS.some((c) => c.id === id)) ctl?.reveal(id);
    scrollToId(id);
  };
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
        No gateway answered this page, so the live chapters say "not available". Run <code className="font-mono">make demo</code> and open{" "}
        <code className="font-mono">/judges</code> on it.
      </p>
    );
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[13px] leading-relaxed text-ink-2">
      {status && <RealBadge status={status} />}
      <span>
        Live chapters read the gateway serving this page: {stats.mode === "mock" ? "the mock demo" : "a live gateway"}, day {fmtInt(stats.day)},{" "}
        {fmtInt(stats.totals.requests)} requests so far.
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
  const go = useGo();
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
        <Container className="pb-10 pt-8 sm:pt-12">
          <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-12">
            <div className="min-w-0">
              <p className="font-mono text-[12.5px] text-muted">For judges</p>
              <h1 className="mt-3 text-balance text-[34px] font-semibold leading-[1.06] tracking-[-0.03em] text-ink sm:text-[44px]">
                What is real in Ratchet, and how to check it.
              </h1>
              <p className="mt-4 text-pretty text-[17px] leading-relaxed text-ink-2">
                Ratchet is an OpenAI-compatible gateway: a small model answers first and a larger open model steps in when it is unsure. Every claim below carries one
                status, so you can see what ran for real and what is still to come.
              </p>
              <div className="mt-6 flex flex-wrap items-center gap-2">
                <Button size="lg" onClick={startTour}>
                  <Compass aria-hidden /> Start the guided tour
                </Button>
                <Link
                  to="/dashboard?tour=1"
                  className="inline-flex h-12 items-center gap-2 rounded-full px-4 text-[15px] font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  <LayoutDashboard className="size-4" aria-hidden /> Tour the dashboard
                </Link>
              </div>
              <div className="mt-6">
                <GatewayLine />
              </div>
            </div>
            <StageOneCard onChapter={go} />
          </div>
          <div className="mt-6 rounded-2xl border border-line bg-bg-sunken/50 px-4 py-3.5 sm:px-5">
            <p className="mb-2.5 text-[13px] font-medium text-ink">Status key</p>
            <Legend />
          </div>
        </Container>
      </div>

      <SectionBar current={current} top={headerH} onTour={startTour} />

      <Container className="pb-24">
        <TrainingStatus />
        <StartHere />

        <Group id="group-results" title="Results" group="start" purpose="What was measured, and what every claim on this site rests on.">
          <ExamsChapter />
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
          purpose="The loop that trains a new small model each night and releases it safely, read live from this gateway. Open what you care about."
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
