import { useCallback, useRef, useState, type ReactNode } from "react";
import { motion } from "motion/react";
import { FlaskConical, Play, Radio, RefreshCw, ServerOff, Terminal, TriangleAlert } from "lucide-react";
import { ApiError, api, type LedgerEvent, type NightReport, type Stats, type Workload } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Container, Eyebrow } from "@/components/ui/section";
import { WhoBadge } from "@/components/ui/who";
import { cn, fmtInt, fmtPct } from "@/lib/utils";
import { lastCall, lastCallLine, sharedNotice } from "@/lib/surface-model";
import { hostedServing, servingLine } from "@/lib/who-model";
import { CostChart, PrecisionChart, ShareChart } from "./charts";
import { DemoControls, WORKLOADS, type Busy, type Notice } from "./controls";
import { EconomicsPanel, hasEconomics } from "./economics";
import { KpiSkeleton, Kpis } from "./kpis";
import { MeasuredResults } from "./measured";
import { Group, JumpBar, SECTIONS, type SectionId } from "./sections";
import { armedFault, eventKey, fmtTime, newEvents, stateLabel } from "./model";
import { skipInfo, skipSentence } from "./night-model";
import { Pipeline } from "./pipeline";
import { Provenance } from "./provenance";
import { TaskRegistryPanel } from "./registry";
import { ReleaseLog } from "./release-log";
import { ResiliencePanel } from "./resilience";
import { TasksTable, VersionsTable } from "./tables";
import { TrustPanel } from "./trust";
import { DetailText, KindChip } from "./ui";
import { useCostReport, useDashboardStats, useNow, useTaskRegistry } from "./use-dashboard";

const POLL_MS = 15000;
const DAY_REQUESTS = 1500;
const NOTABLE = new Set(["rollback", "alert", "live", "rejected", "canary", "shadow", "student_error", "circuit", "degraded", "fallback_teacher"]);

function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  return `${m} min ago`;
}

function nightSentence(night: NightReport, after: Stats | null, skipText?: string): string {
  const prec = night.eval?.precision != null ? `golden precision ${fmtPct(night.eval.precision)}` : null;
  if (night.trained && night.version != null) {
    const v = after?.versions.find((x) => x.id === night.version);
    if (night.admitted) {
      const where = v ? stateLabel(v).toLowerCase() : "the release pipeline";
      return `Trained v${night.version}${prec ? `: ${prec}` : ""}, admitted to ${where}.`;
    }
    return `Trained v${night.version}${prec ? `: ${prec}` : ""}, rejected at the golden gate.`;
  }
  if (night.reason === "not enough new data" && skipText) return skipText;
  const why: Record<string, string> = {
    "release in flight": "release in flight",
    "not enough new data": "not enough new data",
    "no golden set": "no golden set yet",
  };
  return `Skipped training: ${why[night.reason] ?? night.reason}.`;
}

function sentence(msg: string): string {
  const t = msg.trim();
  if (!t) return t;
  return t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".");
}

function errorText(e: unknown, what: string): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return `${what}. ${sentence(e.message || "another demo action is running; try again in a moment")}`;
    if (e.status === 429) return `${what}. ${sentence(e.message || "the demo was reset less than 30 seconds ago")}`;
    if (e.status === 403) return "Demo controls are only available in mock mode.";
    return `${what}. ${sentence(e.message)}`;
  }
  return `${what}. The gateway did not answer.`;
}

function Unreachable({ error, onRetry, retrying }: { error: string; onRetry: () => void; retrying: boolean }) {
  return (
    <div className="mx-auto max-w-xl rounded-2xl border border-line bg-surface p-6 text-center shadow-card sm:p-10">
      <span className="mx-auto grid size-11 place-items-center rounded-full border border-line bg-surface-2 text-muted">
        <ServerOff className="size-5" aria-hidden />
      </span>
      <h2 className="mt-4 text-[18px] font-semibold tracking-[-0.015em] text-ink">Can't reach the gateway</h2>
      <p className="mx-auto mt-2 max-w-md text-pretty text-[14px] leading-6 text-muted">
        The dashboard reads <span className="font-mono text-[13px] text-ink-2">/ratchet/stats</span> from a running Ratchet gateway. Last error: {error}.
      </p>
      <div className="mx-auto mt-5 flex max-w-sm items-center gap-2 rounded-xl border border-line bg-bg-sunken px-3 py-2 text-left font-mono text-[12.5px] text-ink-2">
        <Terminal className="size-3.5 shrink-0 text-muted" aria-hidden />
        <span className="truncate">make demo</span>
        <span className="ml-auto text-muted">mock gateway on :8000</span>
      </div>
      <Button className="mt-6" onClick={onRetry} disabled={retrying}>
        <RefreshCw className={cn(retrying && "animate-spin")} aria-hidden /> Retry
      </Button>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading dashboard">
      <KpiSkeleton />
      <div className="h-[290px] animate-pulse rounded-2xl border border-line bg-surface" />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-[330px] animate-pulse rounded-2xl border border-line bg-surface" />
        <div className="h-[330px] animate-pulse rounded-2xl border border-line bg-surface" />
      </div>
    </div>
  );
}

/** What a click's `auto_reset` says: the shared simulation started over before the day ran. */
function resetSentence(reason: string, stats: Stats | null | undefined): string {
  const d = stats?.demo;
  const after = reason === "max_days" && d?.max_days ? `after ${d.max_days} simulated days` : reason === "max_requests" && d?.max_requests ? `after ${fmtInt(d.max_requests)} requests` : "";
  return `The shared simulation reset${after ? ` ${after}` : ""} and ran its warm-up again.`;
}

/** The last real Token Factory and Tavily calls, content-free (FR-11.31); live mode only. */
function LastCalls({ stats }: { stats: Stats }) {
  const tf = lastCall(stats.upstream, "token_factory");
  const tavily = lastCall(stats.upstream, "tavily");
  const name = stats.upstream?.token_factory ? "Token Factory" : "Model";
  return (
    <>
      <div className="flex min-w-0 flex-wrap items-center gap-x-1.5">
        <dt>Last {name} call</dt>
        <dd className="min-w-0 text-ink-2 [overflow-wrap:anywhere]">{tf ? lastCallLine(tf) : "none since start"}</dd>
      </div>
      {tavily && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-1.5">
          <dt>Last Tavily call</dt>
          <dd className="min-w-0 text-ink-2 [overflow-wrap:anywhere]">{lastCallLine(tavily)}</dd>
        </div>
      )}
    </>
  );
}

function ColdStart({ mock, onRun, busy }: { mock: boolean; onRun: () => void; busy: boolean }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
      <div aria-hidden className="bg-grid pointer-events-none absolute inset-0 opacity-60 [mask-image:linear-gradient(to_left,black,transparent_70%)]" />
      <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="max-w-2xl">
          <p className="font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">Cold start</p>
          <h2 className="mt-1.5 text-[18px] font-semibold tracking-[-0.015em] text-ink">{mock ? "No traffic yet. Every request will go to the teacher at first." : "No requests yet on this live demo. Every request goes to the teacher at first."}</h2>
          <p className="mt-1.5 text-pretty text-[14px] leading-6 text-ink-2">
            {mock ? (
              <>
                Run a day to send {fmtInt(DAY_REQUESTS)} simulated requests through the cascade. That night, Night School trains v1 on the teacher's answers and the
                release gate decides whether it may serve.
              </>
            ) : (
              <>
                Send requests to <span className="font-mono text-[13px]">/v1/chat/completions</span> with an <span className="font-mono text-[13px]">X-Ratchet-Task</span>{" "}
                header. The first student is trained after the first night with enough examples.
              </>
            )}
          </p>
        </div>
        {mock && (
          <Button onClick={onRun} disabled={busy} className="shrink-0">
            <Play aria-hidden /> Run day 1 + night
          </Button>
        )}
      </div>
    </div>
  );
}

function Header({
  stats,
  updatedAt,
  refreshing,
  onRefresh,
}: {
  stats: Stats | null;
  updatedAt: number | null;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const now = useNow(5000);
  const mock = stats?.mode !== "live";
  // The student that answers now, not just its base model (FR-7.40); null in mock mode and on an older gateway.
  const serving = servingLine(stats?.student_serving);
  return (
    <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0">
        <Eyebrow className="mb-3">Operator console</Eyebrow>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-[28px] font-semibold leading-none tracking-[-0.03em] text-ink sm:text-[32px]">Dashboard</h1>
          {stats &&
            (mock ? (
              <Badge tone="outline" className="gap-1.5">
                <FlaskConical aria-hidden /> Simulation (mock models)
              </Badge>
            ) : (
              <Badge tone="good" className="gap-1.5">
                <Radio aria-hidden /> Live
              </Badge>
            ))}
        </div>
        {stats && (
          <dl className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-muted">
            <div className="flex min-w-0 items-center gap-2">
              <dt className="sr-only">Teacher</dt>
              <WhoBadge servedBy="teacher" simulated={mock} />
              <dd className="min-w-0 truncate font-mono text-[12px] text-ink-2">{stats.teacher}</dd>
            </div>
            {serving ? (
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <dt className="sr-only">Student answering now</dt>
                {/* The hosted line starts with the hosted badge's own words, so only our adapter gets a badge here. */}
                {serving.kind === "owned" && <WhoBadge servedBy="student" model={stats.student_serving?.model} />}
                <dd className="min-w-0 text-[12.5px] text-ink-2 [overflow-wrap:anywhere]" title={serving.title}>
                  {serving.text}
                  {serving.detail && <span className="text-muted"> · {serving.detail}</span>}
                </dd>
              </div>
            ) : (
              <div className="flex min-w-0 items-center gap-2">
                <dt className="sr-only">Student base model</dt>
                <WhoBadge servedBy="student" simulated={mock} />
                <dd className="min-w-0 truncate font-mono text-[12px] text-ink-2">{stats.student_base}</dd>
              </div>
            )}
            {!mock && stats.upstream?.recording && (
              <LastCalls stats={stats} />
            )}
            <div className="flex items-center gap-1.5">
              <dt>Day</dt>
              <dd className="font-medium text-ink-2 tabular">{stats.day}</dd>
            </div>
            <div className="flex items-center gap-1.5">
              <dt className="sr-only">Total requests</dt>
              <dd className="font-medium text-ink-2 tabular">{fmtInt(stats.totals.requests)}</dd>
              <span>requests</span>
            </div>
          </dl>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-[12.5px] text-muted">
        {updatedAt && (
          <span title={`Polls every ${POLL_MS / 1000} s`}>
            Updated <time className="text-ink-2 tabular" dateTime={new Date(updatedAt).toISOString()}>{fmtTime(updatedAt)}</time>
            <span className="hidden sm:inline"> · {ago(now - updatedAt)}</span>
          </span>
        )}
        {stats && <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing} aria-label="Refresh now" className="h-8 px-2.5">
          <RefreshCw className={cn(refreshing && "animate-spin")} aria-hidden />
          <span className="hidden sm:inline">Refresh</span>
        </Button>}
      </div>
    </header>
  );
}

function Reveal({ children, className, delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}

export default function DashboardPage() {
  const { stats, error, updatedAt, loading, refreshing, refresh } = useDashboardStats(POLL_MS);
  const cost = useCostReport(updatedAt);
  const registry = useTaskRegistry(updatedAt);
  // The gateway's clock minus this browser's, measured when this viewer starts an outage drill (for its countdown).
  const [skewMs, setSkewMs] = useState(0);
  const [workload, setWorkload] = useState<Workload>("mixed");
  const [busy, setBusy] = useState<Busy>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const noticeId = useRef(0);
  const skipStreak = useRef(0);
  const statsRef = useRef<Stats | null>(null);
  statsRef.current = stats;

  const say = useCallback((n: Omit<Notice, "id">) => setNotice({ ...n, id: ++noticeId.current }), []);

  const markFresh = (before: LedgerEvent[] | undefined, after: Stats | null): LedgerEvent[] => {
    if (!after) return [];
    const added = newEvents(before, after.events);
    setFresh(new Set(added.map(eventKey)));
    return added;
  };

  const runDay = async () => {
    const before = statsRef.current?.events;
    setBusy("day");
    try {
      const r = await api.demoDay(workload, DAY_REQUESTS);
      if (r.auto_reset) setFresh(new Set());
      const after = await refresh();
      const added = markFresh(before, after);
      const notable = added.filter((e) => NOTABLE.has(e.kind) && e.kind !== "trained");
      const bad = added.some((e) => e.kind === "rollback" || e.kind === "alert" || e.kind === "student_error" || e.kind === "circuit");
      const wl = WORKLOADS.find((w) => w.id === r.workload)?.label ?? r.workload;
      // A night that trained resets the streak of skips; a skip for another reason leaves it alone.
      if (r.night.trained) skipStreak.current = 0;
      else if (r.night.reason === "not enough new data") skipStreak.current += 1;
      const lastDay = after?.days[after.days.length - 1];
      const skipText = skipSentence(skipInfo(added), lastDay ? lastDay.student_share : null, skipStreak.current);
      say({
        tone: r.auto_reset ? "info" : bad ? "warn" : r.night.admitted || added.some((e) => e.kind === "live") ? "good" : "info",
        title: (
          <>
            {r.auto_reset && <>{resetSentence(r.auto_reset, after ?? stats)} </>}
            Day {r.day} done: {fmtInt(DAY_REQUESTS)} requests ({wl}). {nightSentence(r.night, after, skipText)}
          </>
        ),
        body:
          notable.length > 0 ? (
            <ul className="mt-1 space-y-1">
              {notable.slice(-4).map((e, i) => (
                <li key={i} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <KindChip kind={e.kind} />
                  <span>
                    <DetailText text={e.detail} />
                  </span>
                </li>
              ))}
            </ul>
          ) : undefined,
      });
    } catch (e) {
      say({ tone: e instanceof ApiError && (e.status === 409 || e.status === 429) ? "warn" : "bad", title: errorText(e, "Could not run the day") });
      void refresh();
    } finally {
      setBusy(null);
    }
  };

  const breakNext = async () => {
    const before = statsRef.current?.events;
    setBusy("break");
    try {
      const r = await api.breakNext();
      const after = await refresh();
      markFresh(before, after);
      const v = /student-v(\d+)/.exec(r.corrupt)?.[1];
      say({
        tone: "warn",
        title: <>Fault armed: v{v ?? "?"} will be served corrupted once it reaches canary.</>,
        body: "Its billing, refund and security answers will be confidently wrong. Run a day or two and watch the release gate roll it back.",
      });
    } catch (e) {
      say({ tone: "bad", title: errorText(e, "Could not arm the fault") });
    } finally {
      setBusy(null);
    }
  };

  const studentOutage = async (seconds: number): Promise<Omit<Notice, "id"> | null> => {
    const before = statsRef.current?.events;
    setBusy("outage");
    try {
      const sent = Date.now();
      const r = await api.studentOutage(seconds);
      const got = Date.now();
      // The gateway's `until` is its clock at the start plus the length; the start fell between `sent` and `got`.
      if (Number.isFinite(r.until)) setSkewMs(Math.round(r.until * 1000 - r.seconds * 1000 - (sent + got) / 2));
      const after = await refresh();
      markFresh(before, after);
      return null; // the panel's countdown says what happens next
    } catch (e) {
      return {
        tone: e instanceof ApiError && (e.status === 409 || e.status === 429) ? "warn" : "bad",
        title: errorText(e, "Could not take the student endpoint down"),
      };
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    setBusy("reset");
    try {
      await api.reset();
      skipStreak.current = 0;
      await refresh();
      setFresh(new Set());
      say({ tone: "info", title: "Demo reset.", body: "New ledger, new mock fleet and fresh traffic streams. Any warm-up days ran again." });
    } catch (e) {
      say({ tone: e instanceof ApiError && (e.status === 409 || e.status === 429) ? "warn" : "bad", title: errorText(e, "Could not reset") });
    } finally {
      setBusy(null);
    }
  };

  const onOutageEnd = useCallback(() => void refresh(), [refresh]);
  const mock = stats?.mode === "mock";
  const cold = stats != null && stats.days.length === 0;
  const fault = stats ? armedFault(stats) : null;
  const shown: SectionId[] = SECTIONS.map((x) => x.id);

  return (
    <div className="[--teacher-only:var(--line-strong)]">
      <Container className="py-8 sm:py-10">
        <Header stats={stats} updatedAt={updatedAt} refreshing={refreshing} onRefresh={() => void refresh()} />

        {stats && <JumpBar show={shown} />}

        <div className="mt-6 space-y-4">
          {!stats && loading && <Skeleton />}
          {!stats && !loading && error && <Unreachable error={error} onRetry={() => void refresh()} retrying={refreshing} />}

          {stats && (
            <>
              {error && (
                <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-warn/30 bg-warn-soft/70 px-3 py-2 text-[13px] text-ink-2">
                  <TriangleAlert className="size-4 text-warn" aria-hidden />
                  <span>
                    Can't reach the gateway ({error}). Showing data from {updatedAt ? fmtTime(updatedAt) : "the last poll"}.
                  </span>
                  <Button size="sm" variant="outline" className="ml-auto h-7" onClick={() => void refresh()} disabled={refreshing}>
                    <RefreshCw className={cn(refreshing && "animate-spin")} aria-hidden /> Retry
                  </Button>
                </div>
              )}

              {mock && sharedNotice(stats.demo) && (
                <p className="rounded-xl border border-line bg-surface-2/60 px-3 py-2 text-[13px] text-ink-2">{sharedNotice(stats.demo)}</p>
              )}

              {mock && (
                <DemoControls
                  workload={workload}
                  onWorkload={setWorkload}
                  busy={busy}
                  onRunDay={() => void runDay()}
                  onBreak={() => void breakNext()}
                  onReset={() => void reset()}
                  armed={fault?.id ?? null}
                  armedPending={fault != null && !fault.trained}
                  nextDay={stats.day + 1}
                  notice={notice}
                  onDismiss={() => setNotice(null)}
                />
              )}

              <div className={cn("space-y-4 transition-opacity duration-500 ease-out", busy === "day" || busy === "reset" ? "opacity-60" : "opacity-100")} aria-busy={busy != null}>
                {cold && <ColdStart mock={mock} onRun={() => void runDay()} busy={busy != null} />}

                <Group id="overview" title="Overview" hint={mock ? "Simulation" : "Live"}>
                  <Reveal>
                    <Kpis stats={stats} />
                  </Reveal>
                  <Reveal delay={0.05}>
                    <Pipeline versions={stats.versions} faultId={fault?.id} />
                  </Reveal>
                </Group>

                <Group id="money" title="Money" hint="What each answer cost, and what the student saved">
                  {hasEconomics(stats, cost) && (
                    <Reveal delay={0.08}>
                      <EconomicsPanel stats={stats} cost={cost} />
                    </Reveal>
                  )}
                  <Reveal delay={0.1} className="grid gap-4 lg:grid-cols-2">
                    <CostChart days={stats.days} hosted={hostedServing(stats.student_serving)} />
                    <ShareChart days={stats.days} hosted={hostedServing(stats.student_serving)} />
                  </Reveal>
                </Group>

                <Group id="versions" title="Versions and tasks" hint="Which student is serving, and for which tasks">
                  <Reveal delay={0.12}>
                    <VersionsTable versions={stats.versions} />
                  </Reveal>
                  <Reveal delay={0.13}>
                    <TasksTable tasks={stats.tasks} hosted={hostedServing(stats.student_serving)} />
                  </Reveal>
                  {!registry.missing && (
                    <Reveal delay={0.14}>
                      <TaskRegistryPanel state={registry} mode={stats.mode} onChanged={() => void refresh()} />
                    </Reveal>
                  )}
                </Group>

                <Group id="reliability" title="Reliability" hint="Breakers, fallbacks and the outage drill">
                  <Reveal delay={0.15}>
                    <ResiliencePanel
                      stats={stats}
                      demo={
                        mock
                          ? {
                              busy,
                              onOutage: studentOutage,
                              onRunDay: () => void runDay(),
                              onOutageEnd: onOutageEnd,
                              skewMs,
                            }
                          : undefined
                      }
                    />
                  </Reveal>
                </Group>

                <Group id="trust" title="Trust" hint="Privacy, licenses and the signed bill of materials">
                  <Reveal delay={0.16}>
                    <TrustPanel stats={stats} />
                  </Reveal>
                </Group>

                <Group id="activity" title="Activity" hint="The release log and precision over time">
                  <Reveal delay={0.17} className="grid gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
                    <ReleaseLog events={stats.events} fresh={fresh} />
                    <div className="flex min-w-0 flex-col gap-4">
                      <PrecisionChart
                        days={stats.days}
                        rollbacks={stats.events
                          .filter((e) => e.kind === "rollback")
                          .map((e) => ({ day: e.day, label: `${/\bv\d+\b/.exec(e.detail)?.[0] ?? ""} rolled back`.trim() }))}
                      />
                      <Provenance bom={stats.bill_of_materials} />
                    </div>
                  </Reveal>
                </Group>

                <Group id="measured" title="Measured" hint="Real models, not the simulation">
                  <Reveal>
                    <MeasuredResults />
                  </Reveal>
                </Group>

                {mock && (
                  <p className="pt-2 text-center text-[12px] text-muted">
                    Simulation: mock teacher, student and trainer from <span className="font-mono">sim/</span>. Numbers show how the loop behaves, not real model quality
                    or prices.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </Container>
    </div>
  );
}
