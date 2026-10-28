import type { ReactNode } from "react";
import { Link } from "react-router";
import { motion } from "motion/react";
import { ArrowRight, FlaskConical, Radio } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Container } from "@/components/ui/section";
import { CountUp } from "@/components/ui/stat";
import type { Stats } from "@/lib/api";
import { rise } from "@/lib/motion";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { fmtAge, lastCall, lastCallLine } from "@/lib/surface-model";
import { studentShareCopy } from "@/lib/who-model";

/** "No Token Factory call since the gateway started 12 min ago". */
function noCall(what: string, since: number | null | undefined): string {
  return since ? `No ${what} call since the gateway started ${fmtAge(Date.now() / 1000 - since)}` : `No ${what} call yet`;
}

/** The gateway's real numbers right now. Hidden when the API is unreachable (static preview). */
export function LiveStrip({ stats }: { stats: Stats | null }) {
  if (!stats) return null;
  const latest = stats.days.length ? stats.days[stats.days.length - 1] : null;
  const live = [...stats.versions].reverse().find((v) => v.state === "live");
  const canary = [...stats.versions].reverse().find((v) => v.state === "canary");
  const shadow = [...stats.versions].reverse().find((v) => v.state === "shadow");
  const mock = stats.mode === "mock";
  const empty = stats.totals.requests === 0;
  // With the GPU endpoint off the hosted, untuned Nano answers student calls, so the share is not "your adapter" (FR-7.40).
  const share = studentShareCopy(stats.student_serving);

  // The last real upstream calls (live mode only; mock models record none), content-free (FR-11.31).
  const tf = !mock && stats.upstream?.recording ? lastCall(stats.upstream, "token_factory") : null;
  const tavily = !mock && stats.upstream?.recording ? lastCall(stats.upstream, "tavily") : null;
  const since = stats.upstream?.since;

  const items: { label: string; value: ReactNode; sub: string }[] = [
    {
      label: "Saved vs all-Ultra",
      value: empty ? "–" : <CountUp value={stats.totals.savings_pct} format={(v) => fmtPct(v)} />,
      sub: empty ? "no requests yet on this live gateway" : `${fmtUsd(stats.totals.saved_usd, 2)} so far`,
    },
    {
      label: share.label,
      value: latest ? <CountUp value={latest.student_share} format={(v) => fmtPct(v)} /> : "–",
      sub: share.sub ?? (latest ? `day ${latest.day} · ${fmtPct(latest.escalation_rate)} to the teacher` : "no traffic yet"),
    },
    {
      label: "Live version",
      value: live ? `v${live.id}` : "none yet",
      sub: canary ? `v${canary.id} in canary at ${canary.traffic_pct}%` : shadow ? `v${shadow.id} in shadow` : "no release in flight",
    },
    {
      label: "Requests",
      value: <CountUp value={stats.totals.requests} format={(v) => fmtInt(v)} />,
      sub: empty ? "send one to start the ledger" : `over ${stats.days.length} ${stats.days.length === 1 ? "day" : "days"}`,
    },
  ];

  return (
    <section data-tour="live-strip" aria-labelledby="live-title" className="relative">
      <Container>
        <motion.div {...rise(0.1, 14)} className="relative overflow-hidden rounded-[22px] border border-line bg-surface/85 shadow-card backdrop-blur-md">
          <div aria-hidden className="edge-light pointer-events-none absolute inset-x-0 top-0 h-px opacity-80" />
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 id="live-title" className="text-[13px] font-semibold text-ink">
                This gateway, right now
              </h2>
              {mock ? (
                <Badge tone="outline">
                  <FlaskConical aria-hidden /> Mock models
                </Badge>
              ) : (
                <Badge tone="good">
                  <span className="live-dot relative inline-flex size-2 rounded-full bg-good" aria-hidden />
                  <Radio aria-hidden /> Live traffic
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <Link to="/dashboard" className="inline-flex items-center gap-1 text-[13px] font-medium text-ink-2 transition-colors hover:text-ink">
                Open the dashboard <ArrowRight className="size-3.5" />
              </Link>
            </div>
          </div>
          <dl className="grid grid-cols-2 lg:grid-cols-4">
            {items.map((it, i) => (
              <div
                key={it.label}
                className={[
                  "min-w-0 px-4 py-4 sm:px-5",
                  i % 2 === 1 ? "border-l border-line" : "",
                  i >= 2 ? "border-t border-line lg:border-t-0" : "",
                  i === 2 ? "lg:border-l" : "",
                ].join(" ")}
              >
                <dt className="min-h-[2.5em] text-[12px] leading-tight text-muted sm:min-h-0">{it.label}</dt>
                <dd className="mt-1.5 text-[22px] font-semibold leading-none tracking-[-0.025em] text-ink tabular sm:text-[26px]">{it.value}</dd>
                <dd className="mt-1.5 text-[12px] leading-snug text-muted [overflow-wrap:anywhere]">{it.sub}</dd>
              </div>
            ))}
          </dl>
          {!mock && stats.upstream?.recording && (
            <dl className="grid gap-x-6 gap-y-1 border-t border-line px-4 py-3 text-[12.5px] text-muted sm:grid-cols-2 sm:px-5">
              <div className="min-w-0 [overflow-wrap:anywhere]">
                <dt className="inline">Last {stats.upstream.token_factory ? "Token Factory" : "model"} call: </dt>
                <dd className="inline text-ink-2">{tf ? lastCallLine(tf) : noCall(stats.upstream.token_factory ? "Token Factory" : "model", since)}</dd>
              </div>
              <div className="min-w-0 [overflow-wrap:anywhere]">
                <dt className="inline">Last Tavily call: </dt>
                <dd className="inline text-ink-2">{tavily ? lastCallLine(tavily) : noCall("Tavily", since)}</dd>
              </div>
            </dl>
          )}
        </motion.div>
      </Container>
    </section>
  );
}
