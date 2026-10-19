import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Circle, ScrollText } from "lucide-react";
import type { LedgerEvent } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EVENT_KINDS, EVENT_LABEL, EVENT_TONE, eventKey } from "./model";
import { DetailText, Empty, EVENT_ICON, Panel, TONE_DOT } from "./ui";

export function ReleaseLog({ events, fresh }: { events: LedgerEvent[]; fresh: Set<string> }) {
  const [filter, setFilter] = useState<string | null>(null);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of events) m.set(e.kind, (m.get(e.kind) ?? 0) + 1);
    return m;
  }, [events]);
  const kinds = [...EVENT_KINDS.filter((k) => counts.has(k)), ...[...counts.keys()].filter((k) => !(EVENT_KINDS as readonly string[]).includes(k))];
  const active = filter && counts.has(filter) ? filter : null;
  const rows = events
    .map((e, i) => ({ e, i }))
    .reverse()
    .filter(({ e }) => !active || e.kind === active);

  // Group consecutive rows by day (newest first).
  const groups: { day: number; items: { e: LedgerEvent; i: number }[] }[] = [];
  for (const r of rows) {
    const g = groups[groups.length - 1];
    if (g && g.day === r.e.day) g.items.push(r);
    else groups.push({ day: r.e.day, items: [r] });
  }

  const chip = (k: string | null, label: string, n: number) => {
    const on = active === k;
    return (
      <button
        key={k ?? "all"}
        type="button"
        aria-pressed={on}
        onClick={() => setFilter(k)}
        className={cn(
          "inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 text-[12px] font-medium transition-colors",
          on ? "border-ink bg-ink text-bg" : "border-line text-ink-2 hover:border-line-strong hover:bg-surface-2",
        )}
      >
        {label}
        <span className={cn("tabular", on ? "text-bg/70" : "text-muted")}>{n}</span>
      </button>
    );
  };

  return (
    <Panel
      id="log"
      title="Release log"
      description="The last 40 events from training and the release gate, newest first."
      bodyClassName="flex flex-col"
    >
      {events.length === 0 ? (
        <Empty icon={ScrollText} title="Nothing yet">
          Events appear after the first night: training, gate decisions, promotions and rollbacks.
        </Empty>
      ) : (
        <>
          <div className="-mx-4 mb-3 flex gap-1.5 overflow-x-auto px-4 pb-1 sm:-mx-5 sm:flex-wrap sm:px-5" role="group" aria-label="Filter events by kind">
            {chip(null, "All", events.length)}
            {kinds.map((k) => chip(k, EVENT_LABEL[k] ?? k, counts.get(k) ?? 0))}
          </div>
          <div className="-mx-4 max-h-[460px] overflow-y-auto px-4 sm:-mx-5 sm:px-5 lg:max-h-none lg:min-h-[420px] lg:basis-0 lg:grow" tabIndex={0} aria-label="Release events">
            {groups.map((g) => (
              <section key={`${g.day}-${g.items[0].i}`} aria-label={`Day ${g.day}`} className="pb-1">
                <h3 className="sticky top-0 z-[2] bg-surface/95 py-1.5 font-mono text-[11px] uppercase tracking-[0.12em] text-muted backdrop-blur">
                  Day {g.day}
                </h3>
                <ol className="relative">
                  <AnimatePresence initial={false}>
                    {g.items.map(({ e, i }) => {
                      const Icon = EVENT_ICON[e.kind] ?? Circle;
                      const tone = EVENT_TONE[e.kind] ?? "neutral";
                      const isNew = fresh.has(eventKey(e));
                      return (
                        <motion.li
                          key={i}
                          layout="position"
                          initial={{ opacity: 0, y: -6 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
                          className="group relative flex gap-3 pb-3 last:pb-2"
                        >
                          {/* timeline rail */}
                          <span aria-hidden className="absolute bottom-0 left-[13px] top-7 w-px bg-line group-last:hidden" />
                          <span aria-hidden className={cn("relative z-[1] mt-0.5 grid size-[27px] shrink-0 place-items-center rounded-full border", TONE_DOT[tone])}>
                            <Icon className="size-3.5" />
                          </span>
                          <div className={cn("min-w-0 flex-1 rounded-lg px-2 py-1 -mx-2 transition-colors", isNew && "bg-owned-soft/60")}>
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                              <span className="text-[13px] font-semibold text-ink">{EVENT_LABEL[e.kind] ?? e.kind}</span>
                              {isNew && (
                                <span className="rounded-full bg-owned px-1.5 text-[10.5px] font-semibold uppercase leading-4 tracking-wide text-white dark:text-[#06101f]">
                                  new
                                </span>
                              )}
                              {e.ts != null && (
                                <span className="ml-auto text-[11.5px] text-muted tabular">
                                  {new Date(e.ts * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
                                </span>
                              )}
                            </div>
                            <p className="mt-0.5 break-words text-[13px] leading-5 text-ink-2">
                              <DetailText text={e.detail} />
                            </p>
                          </div>
                        </motion.li>
                      );
                    })}
                  </AnimatePresence>
                </ol>
              </section>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}
