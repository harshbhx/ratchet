import { ArrowDown, ArrowUpRight, Hourglass } from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import { fmtDate, REAL_STATUSES, STATUS_TEXT, statusCounts, type RealEntry } from "@/lib/real-model";
import { cn } from "@/lib/utils";
import { Chapter, Command } from "./ui";
import { REAL } from "./real";

const PATH = [
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
  {
    to: "real",
    action: "Read what is real",
    where: "What's real",
    expect: "Which claims rest on real runs, which are simulation, and which have not been run yet.",
  },
];

function jump(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  window.setTimeout(() => (document.getElementById(`${id}-title`) as HTMLElement | null)?.focus({ preventScroll: true }), 400);
}

export function StartHere() {
  return (
    <Chapter
      id="start"
      title="The 90-second path"
      lead={<p>Three things to click, in this order. Each one says what you should see.</p>}
    >
      <ol className="grid gap-3 md:grid-cols-3">
        {PATH.map((p, i) => (
          <li key={p.to} className="relative flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
            <div className="flex items-center gap-3">
              <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-[13px] text-bg tabular-nums">{i + 1}</span>
              <span className="font-mono text-[12px] text-muted">{p.where}</span>
            </div>
            <p className="mt-3 text-[16px] font-semibold leading-snug tracking-[-0.01em] text-ink">{p.action}</p>
            <p className="mt-2 flex-1 text-[14px] leading-relaxed text-ink-2">
              <span className="text-muted">You should see: </span>
              {p.expect}
            </p>
            <button
              type="button"
              onClick={() => jump(p.to)}
              className="mt-4 inline-flex w-fit items-center gap-1.5 rounded-full border border-line-strong px-3 py-1.5 text-[13px] font-medium text-ink transition-colors hover:bg-surface-2"
            >
              Go there <ArrowDown className="size-3.5" aria-hidden />
            </button>
          </li>
        ))}
      </ol>
    </Chapter>
  );
}

/** The status legend: every word a badge on this page can say, and what it means. */
export function Legend({ className }: { className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-x-5 gap-y-2.5", className)} aria-label="What each status means">
      {REAL_STATUSES.map((s) => (
        <li key={s} className="flex min-w-0 items-center gap-2 text-[13px] text-ink-2">
          <RealBadge status={s} />
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
      {e.metrics.map((m) => (
        <div key={m.id} className="min-w-0 bg-surface px-3 py-2.5">
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
  return (
    <Chapter
      id="real"
      title="What's real, and what is simulation"
      lead={
        <>
          <p>
            One file holds every claim this site makes about Ratchet, with what it rests on and how to check it. The demo's numbers come from mock
            models; the text-to-SQL data and adapter are real; nothing has run live on Nebius GPUs yet.
          </p>
          <p className="text-[14px] text-muted">
            {counts.map((c, i) => (
              <span key={c.status}>
                {i > 0 && " · "}
                {c.n} {STATUS_TEXT[c.status].label.toLowerCase()}
              </span>
            ))}
            {REAL.updated && <> · updated {fmtDate(REAL.updated)}</>}
          </p>
        </>
      }
    >

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
            {REAL.entries.map((e) => (
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
        {REAL.entries.map((e) => (
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
