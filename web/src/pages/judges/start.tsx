import { useState } from "react";
import { ArrowDown, ArrowUpRight, Hourglass } from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import { fmtDate, REAL_STATUSES, STATUS_TEXT, statusCounts, type RealEntry, type RealStatus } from "@/lib/real-model";
import { cn, DEMO_URL } from "@/lib/utils";
import { useJudges } from "./data";
import { Chapter, Command, useChapters } from "./ui";
import { REAL } from "./real";

interface PathStep {
  /** The chapter on this page the step jumps to. */
  to: string;
  action: string;
  where: string;
  expect: string;
  /** Another site the step opens instead (the simulation demo). */
  href?: string;
}

const READ_REAL: PathStep = {
  to: "real",
  action: "Read what is real",
  where: "What's real",
  expect: "Which claims rest on real runs, which are simulation, and which are built and ready to run.",
};

/** On the simulation demo: mock models, and the demo controls that break a release on purpose. */
const MOCK_PATH: PathStep[] = [
  {
    to: "client",
    action: "Send a request",
    where: "Send a request",
    expect: "An answer comes back with who served it, the route it took and a request id. The demo's models are mock models.",
  },
  {
    to: "release",
    action: "Inject a bad deploy, then run simulated days",
    where: "Releases",
    expect: "The next version Night School trains goes into canary, fails its audits and is rolled back on its own, within a few simulated days. The log shows each step.",
  },
  READ_REAL,
];

/** On the live gateway: one real, keyless request here; the release drill on the simulation demo, which has the controls. */
const LIVE_PATH: PathStep[] = [
  {
    to: "client",
    action: "Send one real request",
    where: "Send a request",
    expect: "An answer from Nemotron 3 Ultra on Nebius Token Factory, with a badge that says who answered. No key needed. It is a real call, so send a few at most; your text is not stored.",
  },
  {
    to: "release",
    action: "Inject a bad deploy, then run simulated days",
    where: "Simulation demo",
    expect: "On the simulation demo (mock models): the next version Night School trains goes into canary, fails its audits and is rolled back on its own, within a few simulated days.",
    href: `${DEMO_URL}/judges#release`,
  },
  READ_REAL,
];

export function StartHere() {
  const { mock } = useJudges();
  const path = mock ? MOCK_PATH : LIVE_PATH;
  const ctl = useChapters();
  const jump = (id: string) => {
    ctl?.reveal(id);
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    window.setTimeout(() => (document.getElementById(`${id}-title`) as HTMLElement | null)?.focus({ preventScroll: true }), 400);
  };
  return (
    <Chapter id="start" title="Try it in 90 seconds" lead={<p>Three things to click, in this order. Each one says what you should see.</p>}>
      <ol className="grid divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface shadow-card md:grid-cols-3 md:divide-x md:divide-y-0">
        {path.map((p, i) => (
          <li key={p.to} className="flex min-w-0 flex-col p-5">
            <div className="flex items-center gap-3">
              <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-[13px] text-bg tabular-nums">{i + 1}</span>
              <span className="font-mono text-[12px] text-muted">{p.where}</span>
            </div>
            <p className="mt-3 text-[16px] font-semibold leading-snug tracking-[-0.01em] text-ink">{p.action}</p>
            <p className="mt-2 flex-1 text-[14px] leading-relaxed text-ink-2">
              <span className="text-muted">You should see: </span>
              {p.expect}
            </p>
            {p.href ? (
              <a
                href={p.href}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-4 inline-flex h-9 w-fit items-center gap-1.5 rounded-full border border-line-strong px-3.5 text-[13px] font-medium text-ink transition-colors hover:bg-surface-2"
              >
                Open the simulation <ArrowUpRight className="size-3.5" aria-hidden />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            ) : (
              <button
                type="button"
                onClick={() => jump(p.to)}
                className="mt-4 inline-flex h-9 w-fit items-center gap-1.5 rounded-full border border-line-strong px-3.5 text-[13px] font-medium text-ink transition-colors hover:bg-surface-2"
              >
                Go there <ArrowDown className="size-3.5" aria-hidden />
              </button>
            )}
          </li>
        ))}
      </ol>
    </Chapter>
  );
}

/** The status legend: every word a badge on this page can say, and what it means. */
export function Legend({ className, stacked }: { className?: string; stacked?: boolean }) {
  return (
    <ul className={cn(stacked ? "grid gap-2.5" : "flex flex-wrap gap-x-5 gap-y-2.5", className)} aria-label="What each status means">
      {REAL_STATUSES.map((s) => (
        <li key={s} className={cn("flex min-w-0 gap-2 text-[13px] text-ink-2", stacked ? "items-start" : "items-center")}>
          <span className={cn(stacked && "w-[108px] shrink-0")}>
            <RealBadge status={s} />
          </span>
          <span className="min-w-0">{STATUS_TEXT[s].meaning}</span>
        </li>
      ))}
    </ul>
  );
}

function CheckCell({ e }: { e: RealEntry }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <p className="text-[13px] leading-snug text-ink-2">{e.check.label}</p>
      {e.check.command && <Command cmd={e.check.command} label={e.claim} wrap />}
      {e.check.href && (
        <a
          href={e.check.href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-mono text-[12px] text-owned-ink underline decoration-owned/30 underline-offset-2 hover:decoration-owned [overflow-wrap:anywhere]"
        >
          {e.check.href.replace(/^https:\/\/github\.com\/harshbhx\/ratchet\/blob\/main\//, "")}
          <ArrowUpRight className="size-3 shrink-0" aria-hidden />
        </a>
      )}
    </div>
  );
}

function Metrics({ e }: { e: RealEntry }) {
  if (!e.metrics.length) return null;
  return (
    <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line">
      {e.metrics.map((m, i) => (
        <div key={m.id} className={cn("min-w-0 bg-surface px-3 py-2.5", e.metrics.length % 2 === 1 && i === e.metrics.length - 1 && "col-span-2")}>
          <dt className="text-[12px] leading-snug text-muted">{m.label}</dt>
          <dd className="mt-1">
            {m.value != null ? (
              <span className="text-[17px] font-semibold tabular-nums text-ink">{m.value}</span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-warn">
                <Hourglass className="size-3.5" aria-hidden /> Measuring
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Facts({ e }: { e: RealEntry }) {
  if (!e.facts.length) return null;
  return (
    <ul className="mt-2.5 flex flex-wrap gap-1.5">
      {e.facts.map((f) => (
        <li key={f.label} className="rounded-md border border-line bg-bg-sunken px-2 py-1 text-[12px] text-ink-2">
          <span className="text-muted">{f.label}: </span>
          <span className="font-medium tabular-nums text-ink">{f.value}</span>
        </li>
      ))}
    </ul>
  );
}

export function WhatsReal() {
  const counts = statusCounts(REAL);
  const [filter, setFilter] = useState<RealStatus | "all">("all");
  const shown = filter === "all" ? REAL.entries : REAL.entries.filter((e) => e.status === filter);
  return (
    <Chapter
      id="real"
      title="What's real, and what is simulation"
      lead={
        <>
          <p>
            One file holds the status of each result and feature, with what it rests on and how to check it. The simulation demo's numbers come from mock
            models; the text-to-SQL data and adapter are real; the gateway calls Nebius Token Factory for real; the student endpoint and Night School jobs are scripted and ready to run.
          </p>
          {REAL.updated && <p className="text-[13.5px] text-muted">Updated {fmtDate(REAL.updated)}.</p>}
        </>
      }
    >
      <div role="group" aria-label="Show claims with a status" className="mb-4 flex flex-wrap gap-2">
        {([{ status: "all" as const, n: REAL.entries.length }, ...counts] as { status: RealStatus | "all"; n: number }[]).map((c) => {
          const on = filter === c.status;
          return (
            <button
              key={c.status}
              type="button"
              aria-pressed={on}
              onClick={() => setFilter(c.status)}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium transition-colors",
                on ? "border-ink bg-ink text-bg" : "border-line-strong bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink",
              )}
            >
              {c.status === "all" ? "All" : STATUS_TEXT[c.status].label}
              <span className={cn("tabular-nums", on ? "text-bg/70" : "text-muted")}>{c.n}</span>
            </button>
          );
        })}
      </div>

      {/* Wide screens: a table. Narrow: one card per claim. */}
      <div className="hidden overflow-hidden rounded-2xl border border-line bg-surface shadow-card lg:block">
        <table className="w-full table-fixed border-collapse text-left">
          <caption className="sr-only">Every claim, its status, what it rests on and how to check it</caption>
          <colgroup>
            <col className="w-[22%]" />
            <col className="w-[12%]" />
            <col className="w-[38%]" />
            <col className="w-[28%]" />
          </colgroup>
          <thead className="border-b border-line bg-bg-sunken/60 text-[12px] text-muted">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">Claim</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
              <th scope="col" className="px-4 py-2.5 font-medium">What it rests on</th>
              <th scope="col" className="px-4 py-2.5 font-medium">How to check</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line align-top">
            {shown.map((e) => (
              <tr key={e.id} id={`real-${e.id}`}>
                <th scope="row" className="px-4 py-4 text-[14px] font-semibold leading-snug text-ink">
                  {e.claim}
                  <p className="mt-1.5 font-mono text-[11.5px] font-normal text-muted">{fmtDate(e.date)}</p>
                </th>
                <td className="px-4 py-4">
                  <RealBadge status={e.status} />
                </td>
                <td className="px-4 py-4 text-[13.5px] leading-relaxed text-ink-2">
                  {e.evidence}
                  <Facts e={e} />
                  <Metrics e={e} />
                </td>
                <td className="px-4 py-4">
                  <CheckCell e={e} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="space-y-3 lg:hidden">
        {shown.map((e) => (
          <li key={e.id} className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <h3 className="min-w-0 text-[15px] font-semibold leading-snug text-ink">{e.claim}</h3>
              <RealBadge status={e.status} />
            </div>
            <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{e.evidence}</p>
            <Facts e={e} />
            <Metrics e={e} />
            <div className="mt-3 border-t border-line pt-3">
              <CheckCell e={e} />
            </div>
            <p className="mt-2 font-mono text-[11.5px] text-muted">{fmtDate(e.date)}</p>
          </li>
        ))}
      </ul>
    </Chapter>
  );
}
