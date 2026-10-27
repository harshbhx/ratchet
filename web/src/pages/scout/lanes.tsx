import type { ReactNode } from "react";
import { motion } from "motion/react";
import { Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { studentCopy } from "@/lib/who-model";
import { AGENTS, AgentDisc, Domain, RouteNote, StepMeta, WebLink, YesNo, stateLabel } from "./bits";
import { agentState, type AgentRow, type Derived, type RunState } from "./state";

const rise = { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] as const } };

function LaneShell({
  agent,
  run,
  derived,
  progress,
  children,
  className,
}: {
  agent: AgentRow["agent"];
  run: RunState;
  derived: Derived;
  progress?: string;
  children: ReactNode;
  className?: string;
}) {
  const meta = AGENTS[agent];
  const state = agentState(run, derived, agent);
  const id = `lane-${agent}`;
  return (
    <Card className={cn("min-w-0 overflow-hidden", className)}>
      <section aria-labelledby={id} aria-busy={state === "working"}>
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line px-4 py-3.5 sm:px-5">
          <AgentDisc n={meta.n} state={state} />
          <h3 id={id} className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
            {meta.name}
          </h3>
          {meta.task && <code className="font-mono text-[12px] text-muted">{meta.task}</code>}
          <span className="ml-auto font-mono text-[12px] text-muted tabular">{progress ?? stateLabel(state)}</span>
          <RouteNote route={meta.route} className="basis-full pl-9" />
        </header>
        <div className="px-4 py-4 sm:px-5">{children}</div>
      </section>
    </Card>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-[14px] leading-relaxed text-muted">{children}</p>;
}

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="h-3 animate-pulse rounded-full bg-surface-2 motion-reduce:animate-none" style={{ width: `${88 - i * 17}%` }} />
      ))}
    </div>
  );
}

export function PlannerLane({ run, derived }: { run: RunState; derived: Derived }) {
  const st = run.planner;
  return (
    <LaneShell agent="planner" run={run} derived={derived} progress={st ? `${st.detail?.queries?.length ?? 0} queries` : undefined}>
      {st ? (
        <motion.div {...rise} className="space-y-3">
          <ol className="space-y-2">
            {(st.detail?.queries ?? []).map((q, i) => (
              <li key={i} className="flex gap-3 text-[14.5px] leading-snug text-ink">
                <span className="mt-px font-mono text-[12px] text-muted tabular">Q{i + 1}</span>
                <span className="min-w-0 [overflow-wrap:anywhere]">{q}</span>
              </li>
            ))}
          </ol>
          <StepMeta step={st} />
        </motion.div>
      ) : run.status === "running" ? (
        <Skeleton />
      ) : (
        <Empty>{run.status === "idle" ? AGENTS.planner.does : "Not run."}</Empty>
      )}
    </LaneShell>
  );
}

export function SearcherLane({ run, derived }: { run: RunState; derived: Derived }) {
  const mock = run.mode === "mock";
  return (
    <LaneShell
      agent="searcher"
      run={run}
      derived={derived}
      progress={
        derived.searchesDone
          ? `${derived.order.length} results`
          : derived.groups.length
            ? `${derived.groups.filter((g) => g.step).length} of ${derived.groups.length} queries`
            : undefined
      }
    >
      {mock && <p className="-mt-1 mb-3 text-[12.5px] text-muted">Search results are examples (example.org pages).</p>}
      {derived.groups.length === 0 ? (
        <Empty>{run.status === "idle" ? AGENTS.searcher.does : run.status === "running" ? "Waiting for the planner's queries." : "Not run."}</Empty>
      ) : (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {derived.groups.map((g) => (
            <div key={g.qi} className="min-w-0">
              <h4 className="flex items-start gap-2 text-[13.5px] font-semibold leading-snug text-ink">
                <Search className="mt-0.5 size-3.5 shrink-0 text-muted" aria-hidden />
                <span className="[overflow-wrap:anywhere]">{g.query}</span>
              </h4>
              {g.step ? (
                <motion.div {...rise}>
                  <StepMeta step={g.step} className="mt-2" />
                  <ul className="mt-2 divide-y divide-line">
                    {(g.step.detail?.results ?? []).map((r) => (
                      <li key={r.id} className="min-w-0 py-2.5">
                        <WebLink url={r.url} className="text-[14px] font-medium leading-snug text-ink [overflow-wrap:anywhere]">
                          {r.title || r.url}
                        </WebLink>
                        <div>
                          <Domain url={r.url} />
                        </div>
                        {r.snippet && <p className="mt-1 line-clamp-2 text-[13px] leading-snug text-muted [overflow-wrap:anywhere]">{r.snippet}</p>}
                      </li>
                    ))}
                    {(g.step.detail?.results ?? []).length === 0 && <li className="py-2.5 text-[13px] text-muted">No results.</li>}
                  </ul>
                </motion.div>
              ) : run.status === "running" ? (
                <div className="mt-3">
                  <Skeleton lines={4} />
                </div>
              ) : (
                <p className="mt-2 text-[13px] text-muted">Not run.</p>
              )}
            </div>
          ))}
        </div>
      )}
    </LaneShell>
  );
}

function ResultTitle({ run, id, fallbackUrl, fallbackTitle }: { run: RunState; id: number; fallbackUrl?: string; fallbackTitle?: string }) {
  const r = run.results[id];
  const url = r?.url || fallbackUrl || "";
  const title = r?.title || fallbackTitle || url || `Result ${id}`;
  return (
    <span className="min-w-0 flex-1 basis-40">
      <WebLink url={url} className="text-[14px] font-medium leading-snug text-ink [overflow-wrap:anywhere]">
        {title}
      </WebLink>{" "}
      <Domain url={url} />
    </span>
  );
}

function Row({ children, muted, pending, id }: { children: ReactNode; muted?: boolean; pending?: boolean; id?: string }) {
  return (
    <motion.li
      id={id}
      {...rise}
      className={cn(
        "min-w-0 rounded-xl border px-3.5 py-3 transition-colors",
        pending ? "border-dashed border-line-strong bg-transparent" : "border-line bg-surface",
        muted && "bg-surface-2/60",
      )}
    >
      {children}
    </motion.li>
  );
}

export function TriageLane({ run, derived }: { run: RunState; derived: Derived }) {
  const tri = Object.values(run.triage);
  const yours = tri.filter((s) => s.served_by === "student").length;
  const halted = run.status === "error" || run.status === "stopped";
  return (
    <LaneShell
      agent="triage"
      run={run}
      derived={derived}
      progress={derived.order.length ? `${tri.length} of ${derived.order.length} · ${yours} ${studentCopy(derived.hosted).byShort}` : undefined}
    >
      {derived.order.length === 0 ? (
        <Empty>{run.status === "idle" ? AGENTS.triage.does : run.status === "running" ? "Waiting for search results." : "Not run."}</Empty>
      ) : (
        <ul className="space-y-2">
          {derived.order.map((id) => {
            const st = run.triage[id];
            if (!st)
              return (
                <Row key={id} pending>
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <ResultTitle run={run} id={id} />
                  </div>
                  <p className="mt-1 text-[12.5px] text-muted">{halted ? "Not run." : "Waiting for triage…"}</p>
                </Row>
              );
            const d = st.detail;
            return (
              <Row key={id} muted={!d.relevant} id={`triage-${id}`}>
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                  <YesNo yes={d.relevant === true} yesLabel="Relevant" noLabel="Not relevant" />
                  <Badge tone="outline" className="font-mono text-[11px]">
                    {d.source_type || "other"}
                  </Badge>
                </div>
                <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2">
                  <ResultTitle run={run} id={id} fallbackUrl={d.url} fallbackTitle={d.title} />
                </div>
                {d.reason && <p className="mt-1 text-[13px] leading-snug text-muted [overflow-wrap:anywhere]">{d.reason}</p>}
                <StepMeta step={st} className="mt-2" />
              </Row>
            );
          })}
        </ul>
      )}
    </LaneShell>
  );
}

export function ReaderLane({ run, derived }: { run: RunState; derived: Derived }) {
  const rd = Object.values(run.reader);
  const yours = rd.filter((s) => s.served_by === "student").length;
  const halted = run.status === "error" || run.status === "stopped";
  const ids = [...derived.readQueue, ...derived.notRead];
  const total = derived.readQueue.length;
  return (
    <LaneShell
      agent="reader"
      run={run}
      derived={derived}
      progress={total || rd.length ? `${rd.length} of ${total}${derived.triageDone ? "" : "+"} · ${yours} ${studentCopy(derived.hosted).byShort}` : undefined}
    >
      {ids.length === 0 ? (
        <Empty>
          {run.status === "idle"
            ? AGENTS.reader.does
            : derived.triageDone
              ? "Triage found no relevant results to read."
              : run.status === "running"
                ? "Waiting for triage to find relevant results."
                : "Not run."}
        </Empty>
      ) : (
        <ul className="space-y-2">
          {ids.map((id) => {
            const st = run.reader[id];
            const limited = derived.notRead.includes(id);
            const cite = derived.citedByUrl[run.results[id]?.url ?? ""];
            if (!st)
              return (
                <Row key={id} pending>
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <ResultTitle run={run} id={id} />
                  </div>
                  <p className="mt-1 text-[12.5px] text-muted">
                    {limited
                      ? "Not read: the reader reads at most 6 results per question."
                      : halted
                        ? "Not run."
                        : derived.triageDone
                          ? "Reading…"
                          : "Queued until triage finishes."}
                  </p>
                </Row>
              );
            const d = st.detail;
            return (
              <Row key={id} muted={!d.answers_question} id={`reader-${id}`}>
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                  <YesNo yes={d.answers_question === true} yesLabel="Answers" noLabel="Does not answer" />
                  <Badge tone="outline" className="font-mono text-[11px]">
                    {d.fact_type || "none"}
                  </Badge>
                  {cite != null && (
                    <Badge tone="neutral" className="ml-auto font-mono text-[11px]" title="The writer cited this source">
                      cited [{cite}]
                    </Badge>
                  )}
                </div>
                {d.key_fact && (
                  <p className="mt-2 border-l-2 border-line-strong pl-3 text-[14.5px] leading-snug text-ink [overflow-wrap:anywhere]">{d.key_fact}</p>
                )}
                <div className="mt-2 flex flex-wrap items-baseline gap-x-2">
                  <ResultTitle run={run} id={id} fallbackUrl={d.url} />
                </div>
                <StepMeta step={st} className="mt-2" />
              </Row>
            );
          })}
        </ul>
      )}
    </LaneShell>
  );
}
