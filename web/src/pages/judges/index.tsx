import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Check, Compass, LayoutDashboard, Pin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { cn, fmtInt } from "@/lib/utils";
import { GROUP_LABEL, JUDGE_CHAPTERS, type ChapterGroup } from "./chapters";
import { JudgesProvider, useJudges } from "./data";
import { Legend, StartHere, WhatsReal } from "./start";
import { ExamsChapter } from "./exams";
import { ApiChapter, ClientChapter, FeaturesChapter } from "./outside";
import { ArchitectureChapter, CascadeChapter, LedgerChapter, ReleaseChapter } from "./inside-a";
import { CertificateChapter, ProofChapter, ReceiptsChapter, WatchChapter } from "./inside-b";
import { CostChapter, PrivacyChapter, ResilienceChapter } from "./inside-c";
import { CheckChapter, LimitsChapter, SponsorsChapter } from "./check";

const GROUPS: ChapterGroup[] = ["start", "outside", "inside", "check"];

/** Which chapters have been on screen (in memory only), and which one is current. */
function useProgress(ids: string[]) {
  const [seen, setSeen] = useState<Set<string>>(() => new Set());
  const [current, setCurrent] = useState(ids[0]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const visible = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.set(e.target.id, e.boundingClientRect.top);
          else visible.delete(e.target.id);
        }
        const inView = ids.filter((id) => visible.has(id));
        if (inView.length) {
          setCurrent(inView[0]);
          setSeen((s) => {
            if (inView.every((id) => s.has(id))) return s;
            const n = new Set(s);
            inView.forEach((id) => n.add(id));
            return n;
          });
        }
      },
      { rootMargin: "-30% 0px -55% 0px" },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [ids]);
  return { seen, current };
}

/** The site header's height (it grows a row when the gateway reports a live/simulation switch), for sticky offsets. */
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

function go(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  window.setTimeout(() => (document.getElementById(`${id}-title`) as HTMLElement | null)?.focus({ preventScroll: true }), reduce ? 0 : 450);
}

function Rail({ seen, current, top }: { seen: Set<string>; current: string; top: number }) {
  const pinned = JUDGE_CHAPTERS.filter((c) => c.pinned);
  const done = JUDGE_CHAPTERS.filter((c) => seen.has(c.id)).length;
  return (
    <nav aria-label="Chapters" className="sticky overflow-y-auto pb-6 pr-2" style={{ top: top + 24, maxHeight: `calc(100dvh - ${top + 40}px)` }}>
      <div className="mb-4">
        <div className="flex items-baseline justify-between text-[12px] text-muted">
          <span>Progress</span>
          <span className="tabular-nums">
            {done} of {JUDGE_CHAPTERS.length} seen
          </span>
        </div>
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line" aria-hidden>
          <div className="h-full rounded-full bg-ink transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${(done / JUDGE_CHAPTERS.length) * 100}%` }} />
        </div>
      </div>
      {pinned.map((c) => (
        <a
          key={c.id}
          href={`#${c.id}`}
          onClick={(e) => {
            e.preventDefault();
            go(c.id);
          }}
          className={cn(
            "mb-4 flex items-center gap-2 rounded-lg border px-2.5 py-2 text-[13px] font-medium transition-colors",
            current === c.id ? "border-ink bg-surface text-ink" : "border-line bg-surface text-ink-2 hover:text-ink",
          )}
        >
          <Pin className="size-3.5 text-muted" aria-hidden /> {c.label}
        </a>
      ))}
      {GROUPS.map((g) => (
        <div key={g} className="mb-4">
          <p className="mb-1 px-2.5 font-mono text-[11.5px] text-muted">{GROUP_LABEL[g]}</p>
          <ul>
            {JUDGE_CHAPTERS.filter((c) => c.group === g && !c.pinned).map((c) => {
              const on = current === c.id;
              return (
                <li key={c.id}>
                  <a
                    href={`#${c.id}`}
                    aria-current={on ? "location" : undefined}
                    onClick={(e) => {
                      e.preventDefault();
                      go(c.id);
                    }}
                    className={cn(
                      "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] transition-colors",
                      on ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-flex size-3.5 shrink-0 items-center justify-center rounded-full border",
                        seen.has(c.id) ? "border-ink bg-ink text-bg" : on ? "border-ink" : "border-line-strong",
                      )}
                      aria-hidden
                    >
                      {seen.has(c.id) && <Check className="size-2.5" strokeWidth={3} />}
                    </span>
                    <span className="min-w-0 truncate">{c.label}</span>
                    {seen.has(c.id) && <span className="sr-only">(seen)</span>}
                  </a>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Phones and tablets: a sticky bar with the current chapter, progress and a jump list. */
function MobileBar({ seen, current, top }: { seen: Set<string>; current: string; top: number }) {
  const index = JUDGE_CHAPTERS.findIndex((c) => c.id === current);
  const done = JUDGE_CHAPTERS.filter((c) => seen.has(c.id)).length;
  return (
    <div data-tour-inset style={{ top }} className="sticky z-30 -mx-4 border-b border-line bg-bg/90 px-4 py-2 backdrop-blur-xl sm:-mx-6 sm:px-6 lg:hidden">
      <label className="flex items-center gap-3">
        <span className="shrink-0 font-mono text-[11.5px] text-muted tabular-nums">
          {index + 1}/{JUDGE_CHAPTERS.length}
        </span>
        <span className="sr-only">Jump to a chapter</span>
        <select
          value={current}
          onChange={(e) => go(e.target.value)}
          className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface px-2.5 text-[14px] text-ink"
        >
          {GROUPS.map((g) => (
            <optgroup key={g} label={GROUP_LABEL[g]}>
              {JUDGE_CHAPTERS.filter((c) => c.group === g).map((c) => (
                <option key={c.id} value={c.id}>
                  {seen.has(c.id) ? "✓ " : ""}
                  {c.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      <div className="mt-2 h-0.5 overflow-hidden rounded-full bg-line" aria-hidden>
        <div className="h-full bg-ink" style={{ width: `${(done / JUDGE_CHAPTERS.length) * 100}%` }} />
      </div>
    </div>
  );
}

function GatewayLine() {
  const { stats, statsRead, status } = useJudges();
  if (statsRead.state === "loading") return <p className="text-[13px] text-muted">Connecting to this gateway…</p>;
  if (!stats)
    return (
      <p className="text-[13px] text-ink-2">
        No gateway answered this page, so the live chapters say "not available". Run <code className="font-mono">make demo</code> and open{" "}
        <code className="font-mono">/judges</code> on it.
      </p>
    );
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[13px] text-ink-2">
      {status && <RealBadge status={status} />}
      <span>
        This page reads the gateway it is served by: {stats.mode === "mock" ? "the mock demo" : "a live gateway"}, day {fmtInt(stats.day)},{" "}
        {fmtInt(stats.totals.requests)} requests so far.
      </span>
    </p>
  );
}

function Page() {
  const ids = useMemo(() => JUDGE_CHAPTERS.map((c) => c.id), []);
  const { seen, current } = useProgress(ids);
  const headerH = useHeaderHeight();
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
    <div className="overflow-x-clip">
      <Container className="pb-10 pt-12 sm:pt-16">
        <div className="max-w-[68ch]">
          <p className="font-mono text-[12.5px] text-muted">For judges</p>
          <h1 className="mt-3 text-balance text-[34px] font-semibold leading-[1.08] tracking-[-0.03em] text-ink sm:text-[44px]">
            Everything Ratchet does, on one page, with what is real marked.
          </h1>
          <p className="mt-4 text-pretty text-[17px] leading-relaxed text-ink-2">
            The outside first (how an app calls it), then the inside, read live from the gateway's own endpoints, then how to check it yourself. Every
            number carries its status.
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            <Button size="lg" onClick={startTour}>
              <Compass aria-hidden /> Start the guided tour
            </Button>
            <Button variant="outline" size="lg" onClick={() => go("start")}>
              The 90-second path
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
        <div className="mt-8 rounded-2xl border border-line bg-surface p-4 sm:p-5">
          <p className="mb-3 text-[13px] font-medium text-ink">Every claim and number on this page wears one of these:</p>
          <Legend />
        </div>
      </Container>

      <Container>
        <MobileBar seen={seen} current={current} top={headerH} />
        <div className="grid gap-10 pt-8 lg:grid-cols-[220px_minmax(0,1fr)] lg:pt-4">
          <aside className="hidden lg:block">
            <Rail seen={seen} current={current} top={headerH} />
          </aside>
          <div className="min-w-0 pb-20">
            <StartHere />
            <WhatsReal />
            <ExamsChapter />
            <ClientChapter />
            <ApiChapter />
            <FeaturesChapter />
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
            <CheckChapter />
            <SponsorsChapter />
            <LimitsChapter />
          </div>
        </div>
      </Container>
    </div>
  );
}

/** The judges' console: what is real, the gateway's own endpoints chapter by chapter, and a guided tour. */
export default function JudgesPage() {
  return (
    <JudgesProvider>
      <Page />
    </JudgesProvider>
  );
}
