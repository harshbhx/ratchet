import type { ReactNode } from "react";
import { motion } from "motion/react";
import { FlaskConical, Radio, RefreshCw, ServerOff, Terminal, TriangleAlert } from "lucide-react";
import type { Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Container, Eyebrow } from "@/components/ui/section";
import { WhoBadge } from "@/components/ui/who";
import { cn, fmtInt } from "@/lib/utils";
import { lastCall, lastCallLine } from "@/lib/surface-model";
import { hostedServing, servingLine } from "@/lib/who-model";
import { CostChart, PrecisionChart, ShareChart } from "./charts";
import { EconomicsPanel, hasEconomics } from "./economics";
import { DemoSplitTile, KpiSkeleton, Kpis } from "./kpis";
import { MeasuredResults } from "./measured";
import { LiveSince, MEASURED, MeasuredTiles } from "@/components/measured/measured";
import { nightSchoolWording } from "@/lib/measured-model";
import { Group, JumpBar, SECTIONS, type SectionId } from "./sections";
import { armedFault, fmtTime } from "./model";
import { Pipeline } from "./pipeline";
import { Provenance } from "./provenance";
import { TaskRegistryPanel } from "./registry";
import { ReleaseLog } from "./release-log";
import { ResiliencePanel } from "./resilience";
import { TasksTable, VersionsTable } from "./tables";
import { TrustPanel } from "./trust";
import { useWaking } from "@/lib/waking-store";
import { isWaking } from "@/lib/waking-model";
import { useCostReport, useDashboardStats, useNow, useTaskRegistry } from "./use-dashboard";

const POLL_MS = 15000;
const NO_FRESH: Set<string> = new Set();

function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  return `${m} min ago`;
}

function Unreachable({ error, onRetry, retrying }: { error: string; onRetry: () => void; retrying: boolean }) {
  // While the live gateway's container starts the banner under the header says so; this is not an outage.
  if (isWaking(useWaking())) {
    return (
      <div className="mx-auto max-w-xl rounded-2xl border border-line bg-surface p-6 text-center shadow-card sm:p-10">
        <h2 className="text-[18px] font-semibold tracking-[-0.015em] text-ink">Waking up the live gateway</h2>
        <p className="mx-auto mt-2 max-w-md text-pretty text-[14px] leading-6 text-muted">
          It sleeps when idle to save cost. The dashboard fills in by itself when it is ready.
        </p>
      </div>
    );
  }
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
        <span className="truncate">RATCHET_MODE=mock uvicorn ratchet.gateway:app</span>
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

function ColdStart({ mock }: { mock: boolean }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
      <div aria-hidden className="bg-grid pointer-events-none absolute inset-0 opacity-60 [mask-image:linear-gradient(to_left,black,transparent_70%)]" />
      <div className="relative max-w-2xl">
        <p className="font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">{mock ? "No traffic yet" : "Since the last restart"}</p>
        <h2 className="mt-1.5 text-[18px] font-semibold tracking-[-0.015em] text-ink">
          {mock ? "No traffic yet. Every request will go to the teacher at first." : "This gateway has not answered a request since it last started."}
        </h2>
        <p className="mt-1.5 text-pretty text-[14px] leading-6 text-ink-2">
          {mock
            ? "Send requests to /v1/chat/completions with an X-Ratchet-Task header."
            : "Its own ledger lives on the container's disk, so a restart empties it. The totals kept across restarts are above when there are any; below, what was measured on real runs, each with its date and the evidence it was read from."}{" "}
          {MEASURED ? nightSchoolWording(MEASURED).pipeline : ""} Ultra teaches here, and a random 40% of eligible requests are answered by the hosted Nano 30B.
        </p>
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
                <FlaskConical aria-hidden /> Mock models
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
            {stats.totals.requests > 0 && (
              <>
                <div className="flex items-center gap-1.5">
                  <dt>Day</dt>
                  <dd className="font-medium text-ink-2 tabular">{stats.day}</dd>
                </div>
                <div className="flex items-center gap-1.5">
                  <dt className="sr-only">Requests this server instance has answered</dt>
                  <span>this server instance:</span>
                  <dd className="font-medium text-ink-2 tabular">{fmtInt(stats.totals.requests)}</dd>
                  <span>since its last start; all traffic is in the totals below</span>
                </div>
              </>
            )}
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
  const waking = isWaking(useWaking());
  const cost = useCostReport(updatedAt);
  const registry = useTaskRegistry(updatedAt);
  const mock = stats?.mode === "mock";
  const cold = stats != null && stats.days.length === 0;
  const fault = stats ? armedFault(stats) : null;
  const shown: SectionId[] = SECTIONS.map((x) => x.id);

  return (
    <div className="[--teacher-only:var(--line-strong)]">
      <Container className="py-8 sm:py-10">
        <Header stats={stats} updatedAt={updatedAt} refreshing={refreshing} onRefresh={() => void refresh()} />

        {stats && <LiveSince className="mt-5" />}

        {stats && <JumpBar show={shown} />}

        <div className="mt-6 space-y-4">
          {!stats && loading && <Skeleton />}
          {!stats && !loading && error && <Unreachable error={error} onRetry={() => void refresh()} retrying={refreshing} />}

          {stats && (
            <>
              {error && !waking && (
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

              <div className="space-y-4">
                {cold && <ColdStart mock={mock} />}

                <Group id="overview" title="Overview" hint={mock ? "Mock models" : "Live"}>
                  <Reveal>
                    {cold && !mock ? (
                      <MeasuredTiles ids={["toolcall", "smoke", "hosted-nano"]} />
                    ) : (
                      <>
                        <Kpis stats={stats} />
                        <DemoSplitTile stats={stats} />
                      </>
                    )}
                  </Reveal>
                  <Reveal delay={0.05}>
                    <Pipeline versions={stats.versions} faultId={fault?.id} />
                  </Reveal>
                </Group>

                <Group id="money" title="Money" hint="What each answer cost, and what the student saved">
                  {cold && !mock ? (
                    <Reveal delay={0.08}>
                      <MeasuredTiles ids={["labelling", "endpoint", "scout"]} />
                    </Reveal>
                  ) : (
                    <>
                      {hasEconomics(stats, cost) && (
                        <Reveal delay={0.08}>
                          <EconomicsPanel stats={stats} cost={cost} />
                        </Reveal>
                      )}
                      <Reveal delay={0.1} className="grid gap-4 lg:grid-cols-2">
                        <CostChart days={stats.days} hosted={hostedServing(stats.student_serving)} />
                        <ShareChart days={stats.days} hosted={hostedServing(stats.student_serving)} />
                      </Reveal>
                    </>
                  )}
                </Group>

                <Group id="versions" title="Versions and tasks" hint="Which student is serving, and for which tasks">
                  <Reveal delay={0.12}>
                    <VersionsTable versions={stats.versions} live={stats.mode === "live"} />
                  </Reveal>
                  {!(cold && !mock) && (
                    <Reveal delay={0.13}>
                      <TasksTable tasks={stats.tasks} hosted={hostedServing(stats.student_serving)} />
                    </Reveal>
                  )}
                  {!registry.missing && (
                    <Reveal delay={0.14}>
                      <TaskRegistryPanel state={registry} mode={stats.mode} onChanged={() => void refresh()} />
                    </Reveal>
                  )}
                </Group>

                <Group id="reliability" title="Reliability" hint="Breakers and fallbacks">
                  <Reveal delay={0.15}>
                    <ResiliencePanel stats={stats} />
                  </Reveal>
                </Group>

                <Group id="trust" title="Trust" hint="Privacy, licenses and the signed bill of materials">
                  <Reveal delay={0.16}>
                    <TrustPanel stats={stats} />
                  </Reveal>
                </Group>

                <Group id="activity" title="Activity" hint="The release log and precision over time">
                  <Reveal delay={0.17} className="grid gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
                    <ReleaseLog events={stats.events} fresh={NO_FRESH} />
                    <div className="flex min-w-0 flex-col gap-4">
                      {cold && !mock && <MeasuredTiles ids={["retrain"]} className="md:grid-cols-1 xl:grid-cols-1" />}
                      {!(cold && !mock) && <PrecisionChart
                        days={stats.days}
                        rollbacks={stats.events
                          .filter((e) => e.kind === "rollback")
                          .map((e) => ({ day: e.day, label: `${/\bv\d+\b/.exec(e.detail)?.[0] ?? ""} rolled back`.trim() }))}
                      />}
                      <Provenance bom={stats.bill_of_materials} />
                    </div>
                  </Reveal>
                </Group>

                <Group id="measured" title="Measured" hint="Real models">
                  <Reveal>
                    <MeasuredResults />
                  </Reveal>
                </Group>

              </div>
            </>
          )}
        </div>
      </Container>
    </div>
  );
}
