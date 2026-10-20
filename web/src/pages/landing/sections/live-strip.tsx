import { Link } from "react-router";
import { ArrowRight, FlaskConical, Radio } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Container } from "@/components/ui/section";
import type { Stats } from "@/lib/api";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/utils";

/** The gateway's real numbers right now. Hidden when the API is unreachable (static preview). */
export function LiveStrip({ stats }: { stats: Stats | null }) {
  if (!stats) return null;
  const latest = stats.days.length ? stats.days[stats.days.length - 1] : null;
  const live = [...stats.versions].reverse().find((v) => v.state === "live");
  const canary = [...stats.versions].reverse().find((v) => v.state === "canary");
  const shadow = [...stats.versions].reverse().find((v) => v.state === "shadow");
  const mock = stats.mode === "mock";

  const items = [
    {
      label: "Saved vs all-Ultra",
      value: fmtPct(stats.totals.savings_pct),
      sub: `${fmtUsd(stats.totals.saved_usd, 2)} so far`,
    },
    {
      label: "Answered by your model",
      value: latest ? fmtPct(latest.student_share) : "–",
      sub: latest ? `day ${latest.day} · ${fmtPct(latest.escalation_rate)} to the teacher` : "no traffic yet",
    },
    {
      label: "Live version",
      value: live ? `v${live.id}` : "none yet",
      sub: canary ? `v${canary.id} in canary at ${canary.traffic_pct}%` : shadow ? `v${shadow.id} in shadow` : "no release in flight",
    },
    {
      label: "Requests",
      value: fmtInt(stats.totals.requests),
      sub: `over ${stats.days.length} ${stats.days.length === 1 ? "day" : "days"}`,
    },
  ];

  return (
    <section aria-labelledby="live-title">
      <Container>
        <div className="rounded-2xl border border-line bg-surface shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
            <div className="flex flex-wrap items-center gap-2.5">
              <h2 id="live-title" className="text-[13px] font-semibold text-ink">
                This gateway, right now
              </h2>
              {mock ? (
                <Badge tone="outline">
                  <FlaskConical aria-hidden /> Simulation (mock models)
                </Badge>
              ) : (
                <Badge tone="good">
                  <Radio aria-hidden /> Live traffic
                </Badge>
              )}
            </div>
            <Link to="/dashboard" className="inline-flex items-center gap-1 text-[13px] font-medium text-ink-2 transition-colors hover:text-ink">
              Open the dashboard <ArrowRight className="size-3.5" />
            </Link>
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
                <dd className="mt-1.5 text-[22px] font-semibold leading-none tracking-[-0.025em] text-ink tabular sm:text-[24px]">{it.value}</dd>
                <dd className="mt-1.5 truncate text-[12px] text-muted">{it.sub}</dd>
              </div>
            ))}
          </dl>
        </div>
      </Container>
    </section>
  );
}
