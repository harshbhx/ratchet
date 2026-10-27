import { useEffect, useState } from "react";
import { CircleDashed, Info, ShieldCheck, Unplug } from "lucide-react";
import type { Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { WhoBadge } from "@/components/ui/who";
import { cn, fmtInt, fmtPct } from "@/lib/utils";
import { hasWindows, hostedThreshold } from "@/lib/who-model";
import { eventLabel, eventTone } from "./model";
import {
  BREAKER_TONE,
  BREAKER_WORD,
  EVENT_WINDOW,
  FALLBACK_ROUTES,
  breakerViews,
  fallbackTotals,
  fmtAgo,
  fmtClock,
  fmtSpan,
  latestStandIns,
  resilienceEvents,
  type BreakerView,
} from "./resilience-model";
import { BREAKER_ICON, DetailText, Empty, eventIcon, Panel, TONE_DOT, TONE_TEXT } from "./ui";

/** Re-render every `ms` while `active`, so a countdown moves; idle otherwise. */
function useTick(active: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [active, ms]);
  return now;
}

function BreakerChip({ view }: { view: BreakerView }) {
  if (view.state == null) {
    return (
      <Badge tone="outline">
        <CircleDashed aria-hidden /> No recent change
      </Badge>
    );
  }
  const Icon = BREAKER_ICON[view.state];
  return (
    <Badge tone={BREAKER_TONE[view.state]}>
      <Icon aria-hidden /> {BREAKER_WORD[view.state]}
    </Badge>
  );
}

/** What a breaker's badge needs to name the model behind it (`whoLabel`). */
interface StudentBadge {
  model?: string | null;
  fallback?: string | null;
  threshold?: number | null;
  windows?: boolean;
  simulated?: boolean;
}

function BreakerCard({ view, nowMs, student }: { view: BreakerView; nowMs: number; student: StudentBadge }) {
  const { meta, state, since } = view;
  const nowS = nowMs / 1000;
  let meaning: string;
  if (state === "open") meaning = meta.open;
  else if (state === "half_open") meaning = "One request is probing the endpoint; every other request is handled as if it were open.";
  else if (state === "closed") meaning = "Calls go through again.";
  else meaning = `Breakers start closed. None of the last ${EVENT_WINDOW} events changed this one.`;

  let probe: string | null = null;
  if (state === "open") {
    if (view.waitS != null && since != null) {
      const at = since + view.waitS;
      probe = at > nowS ? `The first request after ${fmtClock(at)} probes it (a ${fmtSpan(view.waitS)} wait).` : "The next request probes it.";
    } else {
      probe = "The first request after its cooldown probes it (30 s by default, doubling after each failed probe up to 300 s).";
    }
  }
  const recent = view.changes.slice(-3).reverse();

  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line bg-bg-sunken/50 p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-[13px] font-medium text-ink">
            {meta.label}
            <span className="font-mono text-[11.5px] font-normal text-muted">{view.name}</span>
          </p>
          <WhoBadge
            servedBy={meta.who}
            model={meta.who === "student" ? student.model : undefined}
            fallback={student.fallback}
            threshold={student.threshold}
            windows={student.windows}
            simulated={student.simulated}
            className="mt-1.5"
          />
        </div>
        <BreakerChip view={view} />
      </div>
      {state != null && since != null && (
        <p className="mt-2.5 text-[12.5px] text-muted">
          {state === "open" ? "Opened" : state === "half_open" ? "Probing since" : "Closed at"}{" "}
          <time className="text-ink-2 tabular" dateTime={new Date(since * 1000).toISOString()}>
            {fmtClock(since)}
          </time>{" "}
          · {fmtAgo(nowS - since)}
          {view.reason && <span className="block text-ink-2 [overflow-wrap:anywhere]">{view.reason}</span>}
        </p>
      )}
      <p className="mt-2 text-pretty text-[13px] leading-5 text-ink-2">
        {meaning}
        {probe && <span className="mt-1 block text-muted">{probe}</span>}
      </p>
      <p className="mt-2 text-[12px] leading-5 text-muted">{meta.guards}</p>
      {recent.length > 0 && (
        <ol className="mt-3 space-y-1 border-t border-line pt-2.5" aria-label={`Recent changes of the ${meta.label.toLowerCase()} breaker`}>
          {recent.map((c, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-2 text-[12px] text-muted">
              <span className="tabular">{c.ts != null ? fmtClock(c.ts) : `day ${c.day}`}</span>
              <span className="text-ink-2">
                {BREAKER_WORD[c.from].toLowerCase()} → <span className={cn("font-medium", TONE_TEXT[BREAKER_TONE[c.to]])}>{BREAKER_WORD[c.to].toLowerCase()}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Fallbacks({ stats }: { stats: Stats }) {
  const totals = fallbackTotals(stats.tasks);
  const standIns = latestStandIns(stats.days);
  if (!totals.known) {
    return (
      <p className="rounded-xl border border-dashed border-line-strong px-3.5 py-3 text-[13px] text-muted">
        This gateway does not report routes per task, so fallbacks cannot be counted here.
      </p>
    );
  }
  return (
    <div className="rounded-xl border border-line p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-[13px] font-medium text-ink">Answered by a fallback</p>
        <p className="text-[12px] text-muted">
          All days: <span className="font-medium text-ink-2 tabular">{fmtInt(totals.total)}</span> of {fmtInt(totals.requests)} requests
          {totals.requests > 0 && totals.total > 0 && <> ({fmtPct(totals.total / totals.requests, totals.total / totals.requests < 0.1 ? 1 : 0)})</>}
        </p>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 lg:grid-cols-4">
        {FALLBACK_ROUTES.map((r) => {
          const n = totals.byRoute[r.route] ?? 0;
          return (
            <div key={r.route} className="min-w-0 rounded-lg border border-line bg-surface px-3 py-2.5" title={r.hint}>
              <dt className="flex items-center justify-between gap-2 text-[12px] text-muted">
                <span className="truncate">{r.label}</span>
                <span className="font-mono text-[10.5px]">{r.route}</span>
              </dt>
              <dd className="mt-1 text-[20px] font-semibold leading-none tracking-[-0.02em] text-ink tabular">{fmtInt(n)}</dd>
              <dd className="mt-1.5 text-pretty text-[11.5px] leading-4 text-muted">{r.hint}</dd>
            </div>
          );
        })}
      </dl>
      {standIns && (standIns.degraded > 0 || standIns.fallback > 0) && (
        <p className="mt-2 text-[12px] text-muted">
          Day {standIns.day}: {fmtInt(standIns.degraded)} degraded, {fmtInt(standIns.fallback)} by the fallback teacher.
        </p>
      )}
    </div>
  );
}

function RecentFailures({ stats, nowMs }: { stats: Stats; nowMs: number }) {
  const events = resilienceEvents(stats.events);
  if (events.length === 0) {
    return (
      <Empty icon={ShieldCheck} title="No upstream failures in the recent log" className="py-6">
        Breaker changes, failed student, audit and shadow calls, stand-in answers and adapter reloads appear here.
      </Empty>
    );
  }
  return (
    <ol className="space-y-2" aria-label="Recent upstream failures and recoveries, newest first">
      {events.map((e, i) => {
        const Icon = eventIcon(e);
        const tone = eventTone(e);
        return (
          <li key={`${e.id ?? i}-${e.ts ?? ""}`} className="flex gap-3">
            <span aria-hidden className={cn("mt-0.5 grid size-[27px] shrink-0 place-items-center rounded-full border", TONE_DOT[tone])}>
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="text-[13px] font-semibold text-ink">{eventLabel(e)}</span>
                <span className="ml-auto text-[11.5px] text-muted tabular">
                  {e.ts != null ? `${fmtClock(e.ts)} · ${fmtAgo(nowMs / 1000 - e.ts)}` : `day ${e.day}`}
                </span>
              </div>
              <p className="mt-0.5 break-words font-mono text-[12px] leading-5 text-ink-2">
                <DetailText text={e.detail} />
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Circuit breakers, fallbacks and recent upstream failures, from /ratchet/stats only.
 */
export function ResiliencePanel({ stats }: { stats: Stats }) {
  const views = breakerViews(stats.events);
  // A student-side breaker guards whatever answers student calls now: the hosted fallback while the endpoint is off.
  const student: StudentBadge = {
    model: stats.student_serving?.model,
    fallback: stats.fallback_student,
    threshold: hostedThreshold(stats.student_serving),
    windows: hasWindows(stats.student_serving),
    simulated: stats.mode === "mock",
  };
  const anyOpen = views.some((v) => v.state === "open" || v.state === "half_open");
  // Relative times move every 5 s while a breaker is open; otherwise each stats poll re-renders them.
  const nowMs = useTick(anyOpen, 5000);

  return (
    <Panel
      id="resilience"
      title="Resilience"
      description="What the gateway does when a model endpoint fails: circuit breakers, answers a fallback gave, and recent failures."
      actions={
        anyOpen ? (
          <Badge tone="bad">
            <Unplug aria-hidden /> Breaker open
          </Badge>
        ) : (
          <Badge tone="good">
            <ShieldCheck aria-hidden /> No open breaker
          </Badge>
        )
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        {views.map((v) => (
          <BreakerCard key={v.name} view={v} nowMs={nowMs} student={student} />
        ))}
      </div>

      <div className="mt-3">
        <Fallbacks stats={stats} />
      </div>

      <div className="mt-3 grid gap-3">
        <div className="min-w-0 rounded-xl border border-line p-3.5">
          <p className="text-[13px] font-medium text-ink">Recent failures and recoveries</p>
          <div className="mt-3">
            <RecentFailures stats={stats} nowMs={nowMs} />
          </div>
        </div>
      </div>

      <p className="mt-3 flex items-start gap-2 text-pretty text-[12px] leading-5 text-muted">
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        <span>
          Breaker states are read from the <span className="font-mono">circuit</span> events in the last {EVENT_WINDOW} events of the log; a breaker moves only
          when a request arrives, and at its longest wait (300 s) only some probes are logged. Failures are logged on the 1st, 10th, 100th and 1000th
          occurrence, so the counts above, not this list, say how many there were.
        </span>
      </p>
    </Panel>
  );
}
