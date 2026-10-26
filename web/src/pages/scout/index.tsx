import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { FastForward, FlaskConical, Info, LoaderCircle, Play, Radio, Video } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { WhoProvider } from "@/components/ui/who";
import { Container, Eyebrow } from "@/components/ui/section";
import { Button } from "@/components/ui/button";
import { recordingLabel, watchLabel } from "@/lib/recording-model";
import { cn, fmtUsd } from "@/lib/utils";
import { AgentGraph, GraphLegend } from "./agent-graph";
import { AskBar } from "./ask-bar";
import { AGENTS, RouteNote } from "./bits";
import { Explainer } from "./explainer";
import { PlannerLane, ReaderLane, SearcherLane, TriageLane } from "./lanes";
import { JudgeCode } from "./judge-code";
import { ErrorAlert, RunSummary, WriterCard } from "./summary";
import { announceText, phaseText } from "./state";
import { budgetLeft, runsLeftText, runsLeftToday } from "./scout-model";
import { TotalsBar } from "./totals-bar";
import { useScout } from "./use-scout";

/** The budget a visitor's runs can still use today (the judges' reserve excluded), against the whole daily budget. */
function BudgetLine({ status }: { status: NonNullable<ReturnType<typeof useScout>["status"]> }) {
  const b = budgetLeft(status);
  if (!b) return null;
  const share = b.share;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13px] text-muted sm:justify-center">
      <span className="inline-flex items-center gap-2">
        <span className="text-ink-2">
          Left for visitors today: <span className="font-semibold text-ink tabular">{fmtUsd(b.left, 2)}</span> of the {fmtUsd(b.total, 2)} daily
          budget
        </span>
        <span className="h-1.5 w-16 overflow-hidden rounded-full bg-line" aria-hidden>
          <span className={cn("block h-full rounded-full", share > 0.2 ? "bg-good" : "bg-warn")} style={{ width: `${share * 100}%` }} />
        </span>
      </span>
      {status.runs_per_hour_per_ip > 0 && <span>Up to {status.runs_per_hour_per_ip} runs per hour per visitor</span>}
    </div>
  );
}

/** "N runs left today" for a visitor (the judges' reserve excluded); hidden without a run cap. */
function RunsLeft({ status }: { status: NonNullable<ReturnType<typeof useScout>["status"]> }) {
  const r = runsLeftToday(status);
  if (!r) return null;
  return (
    <Badge tone={r.left <= 3 ? "warn" : "neutral"} className="text-[12.5px] tabular" title={`${r.used} of the ${r.max} runs open to visitors today are used`}>
      {runsLeftText(r)}
    </Badge>
  );
}

/** Placeholder for the budget line while /scout/status loads for the first time. */
function StatusSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the demo budget" className="flex flex-wrap items-center gap-3 sm:justify-center">
      <span className="h-4 w-56 animate-pulse rounded bg-surface-2" />
      <span className="h-4 w-28 animate-pulse rounded bg-surface-2" />
    </div>
  );
}

/** Empty-state explainer under the idle graph: what each of the five agents does. */
function TeamStrip() {
  return (
    <ol className="grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-5">
      {(Object.keys(AGENTS) as (keyof typeof AGENTS)[]).map((k) => {
        const a = AGENTS[k];
        return (
          <li key={k} className="bg-surface px-4 py-3.5">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-[11.5px] text-muted">{a.n}</span>
              <span className="text-[14px] font-semibold text-ink">{a.name}</span>
            </div>
            <p className="mt-1 text-[13px] leading-snug text-muted">{a.does}</p>
            <RouteNote route={a.route} className="mt-2" />
          </li>
        );
      })}
    </ol>
  );
}

export default function ScoutPage() {
  const scout = useScout();
  return (
    <WhoProvider value={scout.who}>
      <ScoutBody scout={scout} />
    </WhoProvider>
  );
}

function ScoutBody({ scout }: { scout: ReturnType<typeof useScout> }) {
  const { run, derived, examples, examplesError, status, statusLoading, mode, start, stop, reloadExamples, refreshStatus, recording, playing, liveRecording, play, skip } = scout;
  const [hint, setHint] = useState<string | null>(null);
  const graphRef = useRef<HTMLDivElement | null>(null);
  const running = run.status === "running";
  const started = run.status !== "idle";
  const progress = phaseText(run, derived);

  const go = useCallback(
    (q: string) => {
      start(q);
      // Bring the graph into view on small screens, where it sits below the fold.
      // Wait two frames so the sticky totals bar has rendered and scroll-margin is right.
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          const el = graphRef.current;
          if (!el || el.getBoundingClientRect().top < window.innerHeight * 0.5) return;
          const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
          el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
        }),
      );
    },
    [start],
  );

  useEffect(() => {
    if (running) setHint(null);
  }, [running]);

  return (
    <div className="pb-24">
      {/* ---------------- header band */}
      <section aria-labelledby="h-scout" className="relative overflow-hidden border-b border-line">
        <div
          aria-hidden
          className="bg-grid pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_60%_90%_at_50%_0%,#000_25%,transparent_75%)]"
        />
        <Container className="relative pb-10 pt-10 sm:pb-12 sm:pt-14 sm:text-center lg:pt-16">
          <Eyebrow className="sm:justify-center">Multi-agent demo</Eyebrow>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 sm:justify-center">
            <h1 id="h-scout" className="text-[2.75rem] font-semibold leading-none tracking-[-0.035em] text-ink sm:text-[3.5rem]">
              Scout
            </h1>
            {recording && (
              <Badge
                tone={recording.mode === "mock" ? "warn" : "neutral"}
                className="text-[12.5px]"
                title="Played back from a file, at the pace it ran. No model or search call is made now."
              >
                <Video aria-hidden /> {recordingLabel(recording)}
              </Badge>
            )}
            {!recording && mode === "mock" && (
              <Badge tone="outline" className="text-[12.5px]" title="Mock models and simulated search results">
                <FlaskConical aria-hidden /> Simulation
              </Badge>
            )}
            {!recording && mode === "live" && (
              <Badge tone="good" className="text-[12.5px]">
                <Radio aria-hidden /> Live: real web search and models
              </Badge>
            )}
          </div>
          <p className="mt-4 max-w-[64ch] text-pretty sm:mx-auto sm:text-balance text-[17px] leading-relaxed text-ink-2 sm:text-lg">
            Five research agents answer a question from the web. Every call goes through Ratchet: the narrow, repetitive ones move from Nemotron 3
            Ultra, which you rent, to Nano 4B with an adapter you own.
          </p>
          <div className="mt-3 flex min-h-5 flex-col items-start gap-1.5 sm:items-center">
            {mode === "mock" && (
              <p className="inline-flex items-center gap-1.5 text-[13px] text-muted">
                <Info className="size-3.5" aria-hidden /> Search results are simulated, and so are the models and prices.
              </p>
            )}
            {status && <BudgetLine status={status} />}
            {status && <RunsLeft status={status} />}
            {!status && statusLoading && <StatusSkeleton />}
          </div>

          <div className="mx-auto mt-8 max-w-3xl text-left">
            <AskBar
              examples={examples}
              examplesError={examplesError}
              running={running}
              current={run.question}
              onRun={go}
              onStop={stop}
              onRetryExamples={() => void reloadExamples()}
              hint={setHint}
            />
            {(liveRecording || playing) && (
              <div className="mt-3 flex flex-wrap gap-2 sm:justify-center">
                {liveRecording && !playing && (
                  <Button type="button" variant="outline" size="sm" onClick={() => void play(liveRecording)}>
                    <Play aria-hidden /> {watchLabel(liveRecording)}
                  </Button>
                )}
                {playing && (
                  <Button type="button" variant="outline" size="sm" onClick={skip}>
                    <FastForward aria-hidden /> Skip to the end
                  </Button>
                )}
              </div>
            )}
            <JudgeCode status={status} running={running} onChange={refreshStatus} />
            {hint && (
              <p className="mt-2 inline-flex items-center gap-1.5 text-[13.5px] font-medium text-warn" role="alert">
                <Info className="size-4" aria-hidden /> {hint}
              </p>
            )}
          </div>
        </Container>
      </section>

      {/* Screen-reader announcements: one per phase. The visible progress line lives in the graph header. */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announceText(run, derived)}
      </div>

      {(running || run.steps.length > 0) && <TotalsBar run={run} derived={derived} onStop={stop} />}

      <Container className={cn("space-y-6", started ? "pt-2" : "pt-8")}>
        {/* ---------------- agent graph (errors show right above it, where the eye is during a run) */}
        <div ref={graphRef} className="scroll-mt-[200px] space-y-6 sm:scroll-mt-[192px]">
          {run.status === "error" && run.error && <ErrorAlert message={run.error} onRetry={() => go(run.question)} onWatch={liveRecording ? () => void play(liveRecording) : undefined} />}
          <Card className="overflow-hidden">
            <section aria-labelledby="h-graph">
              <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line px-4 py-3.5 sm:px-6">
                <div className="flex min-w-0 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-3">
                  <h2 id="h-graph" className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
                    The team
                  </h2>
                  <p className={cn("flex min-w-0 items-center gap-1.5 text-[13px]", running ? "text-ink-2" : "text-muted")} aria-hidden>
                    {running && <LoaderCircle className="size-3.5 shrink-0 animate-spin motion-reduce:animate-none" />}
                    <span className="sm:truncate">
                      {started ? progress : "Pick a question to watch five agents work. Each dot is one model call."}
                    </span>
                  </p>
                </div>
              </header>
              <div className="bg-grid relative px-3 pb-4 pt-5 sm:px-6 sm:pt-6 [background-position:-1px_-1px]">
                <AgentGraph run={run} derived={derived} />
              </div>
              <div className="border-t border-line px-4 py-3.5 sm:px-6">
                <GraphLegend hosted={derived.hosted} />
              </div>
            </section>
          </Card>
        </div>

        {!started && (
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}>
            <TeamStrip />
          </motion.div>
        )}

        {started && (
          <>
            {/* ---------------- answer + summary */}
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
              <WriterCard run={run} derived={derived} />
              <RunSummary run={run} derived={derived} />
            </div>

            {/* ---------------- lanes */}
            <section aria-labelledby="h-lanes" className="space-y-5 pt-6">
              <div>
                <h2 id="h-lanes" className="text-[22px] font-semibold tracking-[-0.02em] text-ink sm:text-[26px]">
                  What each agent did
                </h2>
                <p className="mt-1.5 max-w-[68ch] text-[14.5px] text-muted">
                  Every model call, who answered it, how sure the student was, and what it cost against Ultra alone.
                </p>
              </div>
              <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
                <PlannerLane run={run} derived={derived} />
                <SearcherLane run={run} derived={derived} />
              </div>
              <div className="grid items-start gap-6 lg:grid-cols-2">
                <TriageLane run={run} derived={derived} />
                <ReaderLane run={run} derived={derived} />
              </div>
            </section>
          </>
        )}

        <Explainer />
      </Container>
    </div>
  );
}
