import { useEffect, useState } from "react";
import { Activity, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { PHONE_URL, STATUS_URL, duration, lossPath, runView, type RunStatus, type RunView } from "./training-model";

/**
 * A live card for the training job that is running now, read from the job's small public status service every 30 seconds.
 * It shows nothing at all when no run is under way (or one ended more than a day ago), and nothing when the service cannot be
 * reached, so a quiet day leaves no empty widget. Spend is an estimate from GPU time at the list price.
 */
export function TrainingStatus() {
  const [view, setView] = useState<RunView | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const ctl = new AbortController();
      const timer = window.setTimeout(() => ctl.abort(), 8000);
      try {
        const r = await fetch(STATUS_URL, { cache: "no-store", signal: ctl.signal });
        const s = (await r.json()) as RunStatus;
        if (alive) setView(runView(s));
      } catch {
        if (alive) setView(null);
      } finally {
        window.clearTimeout(timer);
      }
    };
    void tick();
    const id = window.setInterval(tick, 30000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  if (!view) return null;
  const tone = view.state === "running" ? "text-owned-ink" : view.state === "done" ? "text-good-ink" : "text-bad-ink";
  const path = lossPath(view.lossHistory);
  const numbers: [string, string][] = [
    ["Elapsed", view.elapsedS != null ? duration(view.elapsedS) : "-"],
    ["Time left", view.etaS != null ? duration(view.etaS) : "-"],
    ["Spent (estimate)", `$${view.spentUsd.toFixed(2)}${view.capUsd != null ? ` of $${view.capUsd.toFixed(2)}` : ""}`],
    ["Latest loss", view.loss != null ? view.loss.toFixed(3) : "-"],
  ];
  return (
    <section aria-label="Live training run" className="mb-8 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Activity className="size-4 text-muted" aria-hidden />
        <h2 className="text-[15px] font-semibold text-ink">Live training run: {view.title}</h2>
        <span className={cn("rounded-full border border-current px-2.5 py-0.5 text-[12px] font-semibold", tone)}>{view.state}</span>
        <a
          href={PHONE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-[13px] font-medium text-ink-2 underline decoration-line-strong underline-offset-[3px] hover:text-ink"
        >
          Phone view <ArrowUpRight className="size-3.5" aria-hidden />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      </div>
      <p className="mt-2 text-[13.5px] text-ink-2">
        {view.phase}
        {view.progress && <span className="text-muted"> ({view.progress})</span>}
      </p>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(view.percent)}
        aria-label="Progress"
        className="mt-2 h-2.5 overflow-hidden rounded-full bg-line"
      >
        <div className="h-full rounded-full bg-owned transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${view.percent}%` }} />
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        {numbers.map(([k, v]) => (
          <div key={k}>
            <dt className="text-[12px] text-muted">{k}</dt>
            <dd className="text-[16px] font-semibold tabular-nums text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {path && (
        <svg viewBox="0 0 300 90" preserveAspectRatio="none" className="mt-3 h-[72px] w-full" role="img" aria-label="Training loss over time">
          <path d={path} fill="none" stroke="currentColor" className="text-owned" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      {view.staleS != null && (
        <p role="status" className="mt-3 text-[13px] text-warn-ink">
          No update for {duration(view.staleS)}. The job may be loading, or may have stopped.
        </p>
      )}
      {view.error && (
        <p role="alert" className="mt-3 text-[13px] text-bad-ink">
          {view.error}
        </p>
      )}
    </section>
  );
}
