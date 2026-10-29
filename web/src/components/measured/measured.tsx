import { useEffect, useState, type ReactNode } from "react";
import { ArrowUpRight, Gauge, Radio, Video } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import measuredText from "@/data/measured.json?raw";
import {
  hasLiveTraffic,
  liveLine,
  allToDate,
  measuredLabel,
  measuredTiles,
  nightSchoolTile,
  parseMeasured,
  parsePersistent,
  recordedToDate,
  THIN_LIVE_REQUESTS,
  sourceHref,
  fmtDay,
  type Persistent,
  type Tile,
} from "@/lib/measured-model";

/** The measured snapshot (src/data/measured.json), parsed once. */
export const MEASURED = parseMeasured(measuredText);

/**
 * The live totals the Worker keeps across restarts, or null: nothing recorded yet, a gateway without the route, or no answer.
 * `undefined` while the first read is in flight, so a page does not flash the snapshot first and the totals after.
 */
export function usePersistent(): Persistent | null | undefined {
  const [p, setP] = useState<Persistent | null | undefined>(undefined);
  useEffect(() => {
    let off = false;
    (async () => {
      try {
        const r = await fetch("/ratchet/stats/persistent", { cache: "no-store" });
        const ok = r.ok && (r.headers.get("content-type") ?? "").includes("json");
        const v = ok ? parsePersistent(await r.json()) : null;
        if (!off) setP(v);
      } catch {
        if (!off) setP(null);
      }
    })();
    return () => {
      off = true;
    };
  }, []);
  return p;
}

const LINK = "inline-flex items-center gap-1 font-medium text-ink underline decoration-line-strong underline-offset-[3px] transition-colors hover:decoration-ink";

/** "Measured · 27 Oct 2026" with the folder it was read from, as a link: how every measured value on the site says where it is from. */
export function MeasuredChip({ date, dateEnd, source, tag = "Measured", className }: { date: string; dateEnd?: string | null; source: string; tag?: Tile["tag"]; className?: string }) {
  const Icon = tag === "Recorded run" ? Video : Gauge;
  const label = tag === "Recorded run" ? `Recorded run${date ? ` · ${measuredLabel(date, dateEnd).replace("Measured · ", "")}` : ""}` : measuredLabel(date, dateEnd);
  return (
    <span className={cn("inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px]", className)}>
      <Badge tone="owned" className="gap-1">
        <Icon aria-hidden /> {label}
      </Badge>
      <a href={sourceHref(source)} target="_blank" rel="noopener noreferrer" className={cn(LINK, "min-w-0 font-mono text-[11.5px] text-muted [overflow-wrap:anywhere]")}>
        {source}
        <ArrowUpRight aria-hidden className="size-3 shrink-0" />
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    </span>
  );
}

function Bars({ bars }: { bars: NonNullable<Tile["bars"]> }) {
  const groups: { name: string | undefined; rows: typeof bars }[] = [];
  for (const b of bars) {
    const g = groups.find((x) => x.name === b.group);
    if (g) g.rows.push(b);
    else groups.push({ name: b.group, rows: [b] });
  }
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <div key={g.name ?? "all"}>
          {g.name && <p className="mb-1 text-[11.5px] font-medium uppercase tracking-[0.08em] text-muted">{g.name}</p>}
          <ul className="space-y-1.5">
            {g.rows.map((b) => (
              <li key={b.label} className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 text-[12.5px] sm:grid-cols-[9.5rem_minmax(0,1fr)]">
                <span className="truncate text-ink-2" title={b.label}>
                  {b.label}
                </span>
                <span className="flex min-w-0 items-center gap-2">
                  <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-line" aria-hidden>
                    <span className="block h-full rounded-full bg-owned" style={{ width: `${Math.max(2, Math.min(1, b.value) * 100)}%` }} />
                  </span>
                  <span className="shrink-0 font-mono text-[11.5px] tabular-nums text-ink">{b.text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function TileCard({ tile, className }: { tile: Tile; className?: string }) {
  return (
    <article className={cn("flex min-w-0 flex-col gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5", className)} aria-labelledby={`m-${tile.id}`}>
      <div className="min-w-0">
        <h3 id={`m-${tile.id}`} className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
          {tile.title}
        </h3>
        <p className="mt-1 text-pretty text-[13.5px] leading-relaxed text-ink-2">{tile.headline}</p>
      </div>
      {tile.bars && tile.bars.length > 0 && <Bars bars={tile.bars} />}
      {tile.facts.length > 0 && (
        <dl className="grid grid-cols-2 gap-2">
          {tile.facts.map((f, i) => (
            <div key={f.label} className={cn("min-w-0 rounded-xl border border-line bg-surface px-3 py-2", tile.facts.length % 2 === 1 && i === tile.facts.length - 1 && "col-span-2")}>
              <dt className="text-[11.5px] leading-snug text-muted">{f.label}</dt>
              <dd className="mt-0.5 text-[13.5px] font-semibold tabular-nums text-ink [overflow-wrap:anywhere]">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <MeasuredChip date={tile.date} dateEnd={tile.dateEnd} source={tile.source} tag={tile.tag} className="mt-auto pt-1" />
    </article>
  );
}

/** The measured tiles, each dated and linked to its evidence. `ids` picks some (in the order given); the Night School tile shows only when its folder exists. */
export function MeasuredTiles({ ids, className }: { ids?: string[]; className?: string }) {
  if (!MEASURED) return null;
  const all = [...measuredTiles(MEASURED)];
  const night = nightSchoolTile(MEASURED);
  if (night) all.push(night);
  const tiles = ids ? ids.map((id) => all.find((t) => t.id === id)).filter((t): t is Tile => Boolean(t)) : all;
  if (tiles.length === 0) return null;
  return (
    <div className={cn("grid gap-4 md:grid-cols-2 xl:grid-cols-3", className)}>
      {tiles.map((t) => (
        <TileCard key={t.id} tile={t} />
      ))}
    </div>
  );
}

/** "Recorded runs to date: ..." with its date badge and the evidence folder, from the snapshot's own sums. */
export function RecordedLine({ className }: { className?: string }) {
  const r = MEASURED ? recordedToDate(MEASURED) : null;
  if (!r) return null;
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <p className="text-[13.5px] leading-relaxed text-ink-2">{r.headline}</p>
      <MeasuredChip date={r.date} dateEnd={r.dateEnd} source={r.source} tag="Recorded run" />
    </div>
  );
}

/**
 * "Live since 29 Oct 2026: N requests, $X Token Factory, ..." from the Worker's running totals, which survive restarts. With nothing
 * recorded (or while the first read is out) it says what the page shows instead, never a zero.
 */
export function LiveSince({ className }: { className?: string }) {
  const p = usePersistent();
  if (p === undefined) return <div className={cn("h-[72px] rounded-2xl border border-line bg-surface/60", className)} aria-hidden />;
  if (hasLiveTraffic(p)) {
    const l = liveLine(p);
    const all = allToDate(p, MEASURED);
    return (
      <section aria-label="Live totals since the first record" className={cn("rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5", className)}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="good" className="gap-1">
            <Radio aria-hidden /> Live since {fmtDay(p.since ?? "")}
          </Badge>
          {l.note && <span className="text-[12px] text-muted">{l.note}</span>}
        </div>
        <p className="mt-2 text-[15px] font-medium text-ink">{l.headline}</p>
        <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
          {l.items.map((i) => (
            <div key={i.label} className="min-w-0 rounded-xl border border-line bg-surface px-3 py-2">
              <dt className="text-[11.5px] leading-snug text-muted">{i.label}</dt>
              <dd className="mt-0.5 text-[15px] font-semibold tabular-nums text-ink">{i.value}</dd>
              {i.hint && <p className="text-[11.5px] leading-snug text-muted">{i.hint}</p>}
            </div>
          ))}
        </dl>
        {all && <p className="mt-3 text-[13px] leading-snug text-ink-2">{all.line}</p>}
        {!all && p.totals.requests < THIN_LIVE_REQUESTS && MEASURED && <RecordedLine className="mt-3" />}
        <p className="mt-2 text-[12px] leading-snug text-muted">
          Counts and dollars only, kept outside the container so a restart does not reset them. Counted since the first record. No trained adapter serves live traffic yet.
        </p>
      </section>
    );
  }
  return (
    <section aria-label="Live totals" className={cn("rounded-2xl border border-line bg-bg-sunken/50 px-4 py-3.5 sm:px-5", className)}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px] leading-relaxed text-ink-2">
        <Badge tone="outline" className="gap-1">
          <Radio aria-hidden /> Live totals
        </Badge>
        The live counters start with the first request and are kept across restarts. Until then this page shows what was measured, each value dated and linked to its evidence.
      </p>
      {MEASURED && <RecordedLine className="mt-2" />}
    </section>
  );
}

/**
 * What a live panel shows when the gateway has no requests since it started (a restart empties its ledger): the measured tiles that
 * belong to the panel, each dated and linked, under a plain sentence. Never a row of zeros.
 */
export function AfterRestart({ ids, className }: { ids: string[]; className?: string }) {
  return (
    <div className={cn("space-y-3", className)}>
      <p className="text-[13.5px] leading-relaxed text-ink-2">
        This gateway has not answered a request since it last started, and its own ledger starts empty after every restart. What was measured on real runs is shown instead, each
        value with its date and the evidence it was read from. The totals the live gateway keeps across restarts are in the strip at the top of the page.
      </p>
      <MeasuredTiles ids={ids} />
    </div>
  );
}

/** The panel's live content when `requests` > 0, else the measured tiles in `ids`. */
export function Traffic({ requests, ids, children }: { requests: number; ids: string[]; children: ReactNode }) {
  return requests > 0 ? <>{children}</> : <AfterRestart ids={ids} />;
}
