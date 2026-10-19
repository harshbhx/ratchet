import { motion } from "motion/react";
import { ArrowRight, CircleAlert, Clock, RotateCcw, WifiOff, Wallet } from "lucide-react";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn, fmtMs } from "@/lib/utils";
import { AGENTS, AgentDisc, RouteNote, StepMeta, usd } from "./bits";
import { AnswerBody, type SourceFact } from "./answer";
import { agentState, type Derived, type RunState } from "./state";

const rise = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] as const } };

// ---------------------------------------------------------------- writer / answer

export function WriterCard({ run, derived }: { run: RunState; derived: Derived }) {
  const st = run.writer;
  const state = agentState(run, derived, "writer");
  const answer = st?.detail?.answer ?? run.done?.answer ?? "";
  const citations = st?.detail?.citations ?? run.done?.citations ?? [];
  const facts: Record<string, SourceFact> = {};
  for (const r of Object.values(run.reader)) {
    const url = r.detail?.url || run.results[r.detail?.id]?.url;
    if (url && r.detail?.key_fact) facts[url] = { fact: r.detail.key_fact, servedBy: r.served_by, model: r.model };
  }
  return (
    <Card className="h-full min-w-0 overflow-hidden">
      <section aria-labelledby="h-answer">
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line px-5 py-4 sm:px-6">
          <AgentDisc n={5} state={state} />
          <h2 id="h-answer" className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
            Answer <span className="font-normal text-muted">· Writer</span>
          </h2>
          <code className="font-mono text-[12px] text-muted">research-write</code>
          <RouteNote route="pinned" className="sm:ml-auto" />
        </header>
        <div className="px-5 py-6 sm:px-7 sm:py-7">
          {run.question && <p className="mb-4 text-[13.5px] font-medium text-muted [overflow-wrap:anywhere]">{run.question}</p>}
          {answer ? (
            <motion.div {...rise}>
              <AnswerBody answer={answer} citations={citations} facts={facts} />
              {st && <StepMeta step={st} className="mt-6 border-t border-line pt-4" />}
            </motion.div>
          ) : run.status === "running" ? (
            <div className="space-y-3" aria-hidden>
              <p className="mb-5 text-[14px] text-muted">
                {state === "working" ? "Writing the answer from the key facts…" : "The writer starts when the reader has its facts."}
              </p>
              {[96, 88, 92, 60].map((w, i) => (
                <div
                  key={i}
                  className={cn("h-3.5 rounded-full bg-surface-2", state === "working" && "animate-pulse motion-reduce:animate-none")}
                  style={{ width: `${w}%` }}
                />
              ))}
            </div>
          ) : (
            <p className="text-[14px] text-muted">No answer: the run did not finish.</p>
          )}
        </div>
      </section>
    </Card>
  );
}

// ---------------------------------------------------------------- run summary

export function RunSummary({ run, derived }: { run: RunState; derived: Derived }) {
  const t = derived.totals;
  const done = run.status === "done";
  const wall = run.done?.totals?.wall_ms ?? (run.endedAt != null && run.startedAt != null ? run.endedAt - run.startedAt : null);
  const rows = derived.perAgent;

  let sentence: React.ReactNode;
  if (!done)
    sentence =
      run.status === "running"
        ? "The summary appears when the run finishes."
        : "The run did not finish. Totals above count the calls that completed.";
  else if (t.llm === 0) sentence = "No model calls were made.";
  else if (t.student === 0)
    sentence = (
      <>
        <span className="text-rented-ink">Ultra answered all {t.llm} calls</span>
        {wall ? `, in ${fmtMs(wall)}` : ""}.
      </>
    );
  else
    sentence = (
      <>
        <span className="text-owned-ink">
          {t.student} of {t.llm}
        </span>{" "}
        calls answered by your model, <span className="text-ink">{Math.round(t.saved * 100)}% cheaper</span> than all-Ultra
        {wall ? `, in ${fmtMs(wall)}` : ""}.
      </>
    );

  return (
    <Card className="h-full min-w-0 overflow-hidden">
      <section aria-labelledby="h-summary" className="flex h-full flex-col">
        <header className="border-b border-line px-5 py-4 sm:px-6">
          <h2 id="h-summary" className="text-[15px] font-semibold tracking-[-0.01em] text-ink">
            Run summary
          </h2>
        </header>
        <div className="flex flex-1 flex-col px-5 py-5 sm:px-6">
          <p className="text-balance text-[19px] font-semibold leading-snug tracking-[-0.02em] text-ink-2 sm:text-[21px]">{sentence}</p>
          {done && t.student === 0 && t.llm > 0 && (
            <p className="mt-3 text-[13.5px] leading-relaxed text-muted">
              Your model has not learned Scout's tasks yet. Ratchet keeps Ultra's triage and reader answers as training data. Once Night School trains
              a student and it passes the release gates, these calls move to your model.
            </p>
          )}
          {done && t.escalated > 0 && (
            <p className="mt-3 text-[13.5px] text-muted">
              {t.escalated} {t.escalated === 1 ? "call was" : "calls were"} escalated: your model tried, was not confident enough, and Ultra answered.
            </p>
          )}

          {(done || run.status !== "running") && (
            <div className="mt-6 overflow-x-auto">
              <table className="w-full min-w-[300px] border-collapse text-[13px] tabular">
                <caption className="sr-only">Calls, share answered by your model, and cost per agent</caption>
                <thead>
                  <tr className="text-left text-[11.5px] text-muted">
                    <th scope="col" className="pb-2 font-medium">
                      Agent
                    </th>
                    <th scope="col" className="pb-2 text-right font-medium">
                      Calls
                    </th>
                    <th scope="col" className="pb-2 pl-3 font-medium">
                      Yours
                    </th>
                    <th scope="col" className="pb-2 text-right font-medium">
                      Cost
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line border-t border-line">
                  {rows.map((r) => {
                    const meta = AGENTS[r.agent];
                    const tool = meta.route === "tool";
                    const share = r.calls && !tool ? r.student / r.calls : 0;
                    return (
                      <tr key={r.agent} className="align-middle">
                        <th scope="row" className="py-2.5 pr-2 text-left font-medium text-ink">
                          {meta.name}
                          <div
                            className={cn(
                              "text-[11px] font-normal",
                              meta.route === "pinned" ? "text-rented-ink" : meta.route === "distillable" ? "text-owned-ink" : "text-muted",
                            )}
                          >
                            {meta.route === "pinned" ? "pinned" : meta.route === "distillable" ? "distillable" : "tool"}
                          </div>
                        </th>
                        <td className="py-2.5 text-right text-ink-2">{r.calls}</td>
                        <td className="py-2.5 pl-3">
                          {tool ? (
                            <span className="text-muted">–</span>
                          ) : (
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 w-12 overflow-hidden rounded-full bg-rented/70 sm:w-16" aria-hidden>
                                <div className="h-full rounded-full bg-owned" style={{ width: `${share * 100}%` }} />
                              </div>
                              <span className={cn("text-[12.5px]", share > 0 ? "text-owned-ink" : "text-muted")}>{Math.round(share * 100)}%</span>
                            </div>
                          )}
                        </td>
                        <td className="py-2.5 text-right">
                          {tool ? (
                            <span className="text-muted">$0</span>
                          ) : (
                            <div className="leading-tight">
                              <div className="text-ink-2">{usd(r.cost)}</div>
                              {Math.abs(r.cost - r.baseline) > 1e-9 && <div className="text-[11.5px] text-muted">of {usd(r.baseline)}</div>}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot className="border-t border-line-strong">
                  <tr>
                    <th scope="row" className="pt-2.5 text-left font-semibold text-ink">
                      Total
                    </th>
                    <td className="pt-2.5 text-right font-semibold text-ink">{t.llm}</td>
                    <td className="pt-2.5 pl-3 font-semibold text-owned-ink">{t.llm ? Math.round((t.student / t.llm) * 100) : 0}%</td>
                    <td className="pt-2.5 text-right leading-tight">
                      <div className="font-semibold text-ink">{usd(t.cost)}</div>
                      <div className="text-[11.5px] text-muted">of {usd(t.baseline)}</div>
                    </td>
                  </tr>
                </tfoot>
              </table>
              <p className="mt-3 text-[11.5px] leading-relaxed text-muted">
                "Of" is what the same calls would cost on Ultra alone. Searcher calls are web searches, not model calls.
                {run.mode === "mock" && " Simulation: mock models and prices."}
              </p>
            </div>
          )}

          {done && (
            <div className="mt-auto pt-6">
              <ButtonLink to="/dashboard" variant="outline" size="sm">
                See these calls on the dashboard <ArrowRight aria-hidden />
              </ButtonLink>
            </div>
          )}
        </div>
      </section>
    </Card>
  );
}

// ---------------------------------------------------------------- errors

export function classifyError(message: string) {
  const m = message.toLowerCase();
  if (/example questions only|mock mode/.test(m))
    return {
      icon: CircleAlert,
      title: "This demo answers the example questions only",
      retry: false,
      hint: "Pick one of the example questions above.",
    };
  if (/budget/.test(m))
    return {
      icon: Wallet,
      title: "Today's demo budget is used up",
      retry: false,
      hint: /tomorrow|utc/.test(m) ? null : "It resets at 00:00 UTC.",
    };
  if (/rate|limit|too many|per hour|runs per|try again later|429/.test(m))
    return { icon: Clock, title: "The demo limit is reached for now", retry: true, hint: "Wait a few minutes, then try again." };
  if (/connection|reach|network|lost|failed to fetch/.test(m))
    return { icon: WifiOff, title: "The connection to Scout dropped", retry: true, hint: "Check your connection and try again." };
  return { icon: CircleAlert, title: "Scout stopped", retry: true, hint: null as string | null };
}

export function ErrorAlert({ message, onRetry }: { message: string; onRetry: () => void }) {
  const c = classifyError(message);
  const Icon = c.icon;
  const text = message.charAt(0).toUpperCase() + message.slice(1);
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      role="alert"
      className="flex scroll-mt-[196px] flex-col gap-3 rounded-2xl border border-bad/35 bg-bad-soft px-4 py-3.5 sm:flex-row sm:items-start sm:px-5"
    >
      <Icon className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[14.5px] font-semibold text-ink">{c.title}</p>
        <p className="mt-0.5 text-[13.5px] leading-relaxed text-ink-2 [overflow-wrap:anywhere]">
          {text}
          {c.hint && <span className="text-muted"> {c.hint}</span>}
        </p>
      </div>
      {c.retry && (
        <Button type="button" variant="outline" size="sm" onClick={onRetry} className="self-start">
          <RotateCcw aria-hidden /> Try again
        </Button>
      )}
    </motion.div>
  );
}
