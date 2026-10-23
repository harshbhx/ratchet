import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  BadgeCheck,
  BookOpenCheck,
  Braces,
  ChevronDown,
  Coins,
  FileText,
  Fingerprint,
  LifeBuoy,
  ListChecks,
  type LucideIcon,
  Receipt,
  Rocket,
  Route,
  Send,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import type { RealStatus } from "@/lib/real-model";
import { cn } from "@/lib/utils";
import { FOLDING_GROUPS, JUDGE_CHAPTERS } from "./chapters";

interface ChapterControl {
  open: ReadonlySet<string>;
  toggle: (id: string) => void;
  /** Open a chapter (a link, the tour or the address named it). */
  reveal: (id: string) => void;
  setMany: (ids: string[], open: boolean) => void;
}

const ChapterContext = createContext<ChapterControl | null>(null);

/** Which folding chapters are open: in memory only; opened by a click, a link to its id, an address hash or a tour stop. */
export function ChapterProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const reveal = useCallback((id: string) => setOpen((s) => (s.has(id) ? s : new Set(s).add(id))), []);
  const toggle = useCallback((id: string) => {
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }, []);
  const setMany = useCallback((ids: string[], on: boolean) => {
    setOpen((s) => {
      const n = new Set(s);
      for (const id of ids) {
        if (on) n.add(id);
        else n.delete(id);
      }
      return n;
    });
  }, []);

  useEffect(() => {
    const known = new Set(JUDGE_CHAPTERS.map((c) => c.id));
    const fromHash = () => {
      let id = "";
      try {
        id = decodeURIComponent(window.location.hash.slice(1));
      } catch {
        /* a malformed hash names nothing */
      }
      if (known.has(id)) reveal(id);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    // The guided tour announces the element it is about to light; a folded chapter opens under its head.
    const onTour = (e: Event) => {
      const id = (e.target as HTMLElement | null)?.id?.replace(/-head$/, "");
      if (id && known.has(id)) reveal(id);
    };
    document.addEventListener("tour-focus", onTour);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      document.removeEventListener("tour-focus", onTour);
    };
  }, [reveal]);

  const value = useMemo(() => ({ open, toggle, reveal, setMany }), [open, toggle, reveal, setMany]);
  return <ChapterContext.Provider value={value}>{children}</ChapterContext.Provider>;
}

export function useChapters(): ChapterControl | null {
  return useContext(ChapterContext);
}

const ICONS: Record<string, LucideIcon> = {
  client: Send,
  api: Braces,
  features: ListChecks,
  architecture: Workflow,
  cascade: Route,
  ledger: BookOpenCheck,
  release: Rocket,
  certificate: BadgeCheck,
  proof: Fingerprint,
  receipts: Receipt,
  watch: Activity,
  privacy: ShieldCheck,
  resilience: LifeBuoy,
  cost: Coins,
};

/**
 * One chapter: an anchored section whose head (title, summary, status) is what the tour lights. In a folding group
 * (`FOLDING_GROUPS`) the head is a disclosure button; the body mounts on first open and opens with the grid trick (rows
 * 0fr to 1fr, plus a fade), so nothing in it is drawn, fetched or tabbed into until a reader asks.
 */
export function Chapter({
  id,
  title,
  lead,
  status,
  tryIt,
  children,
  aside,
}: {
  id: string;
  title: ReactNode;
  lead?: ReactNode;
  status?: RealStatus | null;
  tryIt?: ReactNode;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  const ch = JUDGE_CHAPTERS.find((c) => c.id === id);
  const ctl = useChapters();
  const folds = !!ch && !!ctl && FOLDING_GROUPS.includes(ch.group);
  const isOpen = !folds || ctl!.open.has(id);
  const [mounted, setMounted] = useState(isOpen);
  const [settled, setSettled] = useState(isOpen);
  useEffect(() => {
    if (isOpen) setMounted(true);
    else setSettled(false);
  }, [isOpen]);

  if (!folds) {
    return (
      <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-40 py-8 sm:py-10">
        <div id={`${id}-head`} className="max-w-[72ch]">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            {status && <RealBadge status={status} />}
            {aside}
          </div>
          <h2
            id={`${id}-title`}
            tabIndex={-1}
            className="mt-2.5 text-balance text-[24px] font-semibold leading-tight tracking-[-0.025em] text-ink outline-none sm:text-[28px]"
          >
            {title}
          </h2>
          {lead && <div className="mt-3 space-y-3 text-pretty text-[16px] leading-relaxed text-ink-2">{lead}</div>}
          {tryIt}
        </div>
        {children && <div className="mt-7 min-w-0">{children}</div>}
      </section>
    );
  }

  const Icon = ICONS[id] ?? FileText;
  const hasIntro = !!(lead || aside || tryIt);
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className={cn(
        "scroll-mt-40 rounded-2xl border bg-surface transition-[border-color,box-shadow] duration-200",
        isOpen ? "border-line-strong shadow-card" : "border-line hover:border-line-strong",
      )}
    >
      <div id={`${id}-head`}>
        <h2 id={`${id}-title`} tabIndex={-1} className="m-0 text-base outline-none">
          <button
            type="button"
            aria-expanded={isOpen}
            aria-controls={`${id}-body`}
            onClick={() => ctl!.toggle(id)}
            className="group flex w-full items-start gap-3.5 rounded-2xl px-4 py-4 text-left transition-colors hover:bg-surface-2/60 sm:gap-4 sm:px-5 sm:py-[18px]"
          >
            <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-xl border border-line bg-bg-sunken text-ink-2 transition-colors group-hover:text-ink">
              <Icon className="size-[18px]" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                <span className="text-balance text-[16.5px] font-semibold leading-snug tracking-[-0.015em] text-ink sm:text-[17.5px]">{title}</span>
                {status && <RealBadge status={status} />}
              </span>
              {ch && <span className="mt-1 block text-pretty text-[14px] font-normal leading-relaxed text-muted">{ch.tour}</span>}
            </span>
            <span className="mt-1.5 inline-flex shrink-0 items-center gap-1.5 text-[12.5px] font-medium text-muted group-hover:text-ink">
              <span className="hidden sm:inline">{isOpen ? "Close" : "Open"}</span>
              <ChevronDown className={cn("size-4 transition-transform duration-300 motion-reduce:transition-none", isOpen && "rotate-180")} aria-hidden />
            </span>
          </button>
        </h2>
      </div>
      <div
        id={`${id}-body`}
        role="region"
        aria-labelledby={`${id}-title`}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none",
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        )}
        onTransitionEnd={(e) => {
          if (e.target === e.currentTarget && e.propertyName === "grid-template-rows") setSettled(isOpen);
        }}
        inert={!isOpen}
      >
        <div className={cn("min-h-0", isOpen && settled ? "overflow-visible" : "overflow-hidden")}>
          {mounted && (
            <div className="border-t border-line px-4 pb-6 pt-5 sm:px-5">
              {hasIntro && (
                <div className="max-w-[72ch]">
                  {aside && <div className="mb-3 flex flex-wrap items-center gap-2">{aside}</div>}
                  {lead && <div className="space-y-3 text-pretty text-[15.5px] leading-relaxed text-ink-2">{lead}</div>}
                  {tryIt}
                </div>
              )}
              {children && <div className={cn("min-w-0", hasIntro && "mt-6")}>{children}</div>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
