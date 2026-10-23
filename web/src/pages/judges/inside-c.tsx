import { useId, useState } from "react";
import { Eraser, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RealBadge } from "@/components/ui/real-badge";
import { cn, fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { eventLabel, eventTone } from "@/pages/dashboard/model";
import { getJson, useGateway, useJudges } from "./data";
import { routeTotals, routeWords, type Fetched } from "./judges-model";
import { Chapter, Facts, Loading, Panel, TryIt, Unavailable } from "./ui";

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === "object" ? (v as Obj) : {});
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const OPT_OUTS = [
  ["store: false", "in the request body"],
  ["X-Ratchet-Store: none", "a header"],
  ["metadata.ratchet_store", "request metadata"],
  ["a key's store=0", "per API key"],
  ["no-train end users", "POST /v1/ratchet/no-train"],
  ["no-store tasks", "per task"],
] as const;

const SAMPLE = "Hi, I'm Maya (maya.r@example.com, +1 415 555 0134). Card 4111 1111 1111 1111 was charged twice.";

function Redactor() {
  const [text, setText] = useState(SAMPLE);
  const [res, setRes] = useState<Fetched<{ text: string; found: Record<string, number>; enabled: boolean }>>({ state: "idle" });
  const id = useId();
  const run = async () => {
    setRes({ state: "loading" });
    setRes(await getJson("/ratchet/redact", { method: "POST", body: JSON.stringify({ text }) }));
  };
  return (
    <Panel title="Try the redactor (stores nothing)">
      <label htmlFor={id} className="sr-only">
        Text to redact
      </label>
      <textarea
        id={id}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        maxLength={2000}
        className="block w-full resize-y rounded-xl border border-line-strong bg-bg px-3 py-2 text-[13.5px] leading-relaxed text-ink"
      />
      <Button size="sm" variant="outline" className="mt-2" onClick={() => void run()} disabled={res.state === "loading"}>
        {res.state === "loading" ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Eraser aria-hidden />} Show what would be
        stored
      </Button>
      <div aria-live="polite" className="mt-3">
        {res.state === "error" && <Unavailable path="/ratchet/redact" read={res} />}
        {res.state === "ok" && (
          <div className="space-y-2">
            <p className="rounded-lg bg-bg-sunken px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink [overflow-wrap:anywhere]">{res.data.text}</p>
            <p className="flex flex-wrap gap-1.5">
              {Object.entries(res.data.found ?? {}).map(([k, n]) => (
                <Badge key={k} tone="neutral">
                  {k} × {n}
                </Badge>
              ))}
              {!res.data.enabled && <Badge tone="warn">redaction is off on this gateway</Badge>}
            </p>
          </div>
        )}
      </div>
    </Panel>
  );
}

export function PrivacyChapter() {
  const { stats, status, statsRead } = useJudges();
  const p = obj(stats?.privacy);
  const s = obj(stats?.security);
  return (
    <Chapter
      id="privacy"
      status={status}
      title="Privacy and access"
      lead={
        <p>
          Before a teacher answer is stored, identifiers such as emails, card numbers and phone numbers become placeholders. Clients can keep their
          traffic out of training, stored examples can be erased for good, and retention windows can purge them. API keys and end users are never
          stored: only a key's name, and end users as keyed hashes.
        </p>
      }
    >
      {!stats ? (
        statsRead.state === "error" ? <Unavailable path="/ratchet/stats" read={statsRead} /> : <Loading what="the privacy settings" />
      ) : (
        <div className="space-y-4">
          <Facts
            items={[
              { label: "Redaction", value: p.redaction === false ? "off" : `on (${String(p.redaction_style ?? "label")})` },
              { label: "Examples stored", value: fmtInt(num(p.examples)) },
              { label: "Requests not stored (opt-outs)", value: fmtInt(num(p.not_stored_requests)) },
              { label: "No-train end users", value: fmtInt(num(p.no_train_end_users)) },
              { label: "Erasures", value: fmtInt(num(p.erasures)) },
              { label: "Example retention", value: num(p.example_retention_days) ? `${num(p.example_retention_days)} days` : "kept" },
              { label: "API keys", value: s.api_keys ? "on" : "off (open demo)" },
              { label: "Dashboard reads", value: s.public_dashboard ? "public" : "need a key" },
            ]}
          />
          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            <Redactor />
            <Panel title="Ways to keep traffic out of training">
              <ul className="divide-y divide-line">
                {OPT_OUTS.map(([what, where]) => (
                  <li key={what} className="flex min-w-0 items-baseline justify-between gap-3 py-2 text-[13px]">
                    <code className="font-mono text-[12.5px] text-ink">{what}</code>
                    <span className="text-right text-muted">{where}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
                An opt-out only ever reduces what is stored; the cascade runs as usual. Every route's access level is in the API surface chapter.
              </p>
            </Panel>
          </div>
        </div>
      )}
    </Chapter>
  );
}

const BREAKER_ROUTES = ["student_breaker", "student_error", "degraded", "fallback_teacher"];

export function ResilienceChapter() {
  const { stats, status, statsRead } = useJudges();
  const ready = useGateway<Obj>("/readyz");
  const events = [...(stats?.events ?? [])].filter((e) => ["circuit", "fault", "student_error", "degraded", "fallback_teacher"].includes(e.kind)).reverse().slice(0, 8);
  const routes = routeTotals(stats?.tasks ?? []).filter((r) => BREAKER_ROUTES.includes(r.route));
  const lastHour = obj(obj((stats as unknown as Obj | null)?.errors).last_hour);
  const checks = obj(ready.state === "ok" ? ready.data.checks : {});
  return (
    <Chapter
      id="resilience"
      status={status}
      title="When a model fails"
      lead={
        <p>
          A student failure falls back to the teacher; a request is never lost to it. After repeated failures a breaker stops calling the student for a
          while and probes it again later. Optional teacher calls (audits, arbitrations) never fail a request the student already answered.
        </p>
      }
      tryIt={
        <TryIt
          actions={["outage", "day"]}
          note="Start the outage, then run a simulated day while it lasts: requests take the breaker route to the teacher, and the breaker closes once the student answers again."
        />
      }
    >
      {!stats ? (
        statsRead.state === "error" ? <Unavailable path="/ratchet/stats" read={statsRead} /> : <Loading what="the breakers" />
      ) : (
        <div className="grid min-w-0 gap-4 lg:grid-cols-2">
          <Panel title="Readiness and fallbacks" aside={status && <RealBadge status={status} />}>
            {ready.state === "error" ? (
              <Unavailable path="/readyz" read={ready} />
            ) : ready.state === "ok" ? (
              <p className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
                <Badge tone={ready.data.status === "ready" ? "good" : ready.data.status === "degraded" ? "warn" : "bad"}>{String(ready.data.status)}</Badge>
                {Object.entries(checks).map(([k, v]) => (
                  <span key={k} className="text-muted">
                    {k}: {obj(v).ok === false ? "failing" : "ok"}
                  </span>
                ))}
              </p>
            ) : (
              <Loading what="readiness" />
            )}
            <ul className="mt-3 space-y-1.5 text-[13px]">
              {routes.length === 0 ? (
                <li className="text-muted">No fallback routes taken yet.</li>
              ) : (
                routes.map((r) => (
                  <li key={r.route} className="flex items-baseline justify-between gap-3">
                    <span className="text-ink-2">{routeWords(r.route)}</span>
                    <span className="tabular-nums text-ink">{fmtInt(r.n)}</span>
                  </li>
                ))
              )}
            </ul>
            <p className="mt-3 text-[12.5px] text-muted">
              Failures in the last hour: {Object.keys(lastHour).length ? Object.entries(lastHour).map(([k, v]) => `${k} ${String(v)}`).join(", ") : "none"}
            </p>
          </Panel>
          <Panel title="Breaker and fault events" aside={status && <RealBadge status={status} />}>
            {events.length === 0 ? (
              <p className="text-[13.5px] text-muted">No breaker has moved yet. Start the outage drill to see one open and close.</p>
            ) : (
              <ol className="space-y-2.5">
                {events.map((e, i) => (
                  <li key={`${e.ts ?? i}-${i}`} className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge tone={eventTone(e)}>{eventLabel(e)}</Badge>
                      <span className="font-mono text-[11.5px] text-muted">day {e.day}</span>
                    </div>
                    <p className="mt-1 text-[12.5px] leading-snug text-ink-2 [overflow-wrap:anywhere]">{e.detail}</p>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
      )}
    </Chapter>
  );
}

/** The four answers the cost report can give (the dashboard's words, economics-model.ts). */
const REC_WORDS: Record<string, string> = {
  self_host: "Self-host",
  hosted_fallback: "Use the hosted fallback",
  scale_to_zero: "Scale to zero when idle",
  insufficient_data: "Not enough data",
};

export function CostChapter() {
  const { status } = useJudges();
  const cost = useGateway<Obj>("/ratchet/cost");
  const c = cost.state === "ok" ? cost.data : {};
  const views = obj(c.views);
  const perToken = obj(views.per_token);
  const gpu = obj(views.gpu_hours);
  const baseline = num(c.baseline_usd);
  const rows = [
    { label: "All teacher (no Ratchet)", usd: baseline, tone: "bg-rented" },
    { label: "Ratchet, student priced per token", usd: num(perToken.total_usd), tone: "bg-owned" },
    { label: "Ratchet, by GPU-hours paid", usd: num(gpu.total_usd), tone: "bg-ink" },
  ];
  const max = Math.max(...rows.map((r) => r.usd ?? 0), 1e-9);
  const prices = obj(c.prices);
  return (
    <Chapter
      id="cost"
      status={status}
      title="The honest bill"
      lead={
        <p>
          The same traffic, priced two ways: the student per token, and by the GPU-hours actually paid for its endpoint and training. Below the
          break-even volume a dedicated endpoint loses money, and the report says so instead of hiding it.
        </p>
      }
      tryIt={<TryIt actions={["day"]} />}
    >
      {cost.state === "error" ? (
        <Unavailable path="/ratchet/cost" read={cost} />
      ) : cost.state !== "ok" ? (
        <Loading what="the cost report" />
      ) : (
        <div className="space-y-4">
          <Panel title="One dollar scale" aside={status && <RealBadge status={status} />}>
            <ul className="space-y-3">
              {rows.map((r) => (
                <li key={r.label} className="min-w-0">
                  <div className="flex items-baseline justify-between gap-3 text-[13px]">
                    <span className="text-ink-2">{r.label}</span>
                    <span className="tabular-nums font-medium text-ink">{fmtUsd(r.usd)}</span>
                  </div>
                  <div className="mt-1 h-2 overflow-hidden rounded-full bg-line" aria-hidden>
                    <div className={cn("h-full rounded-full", r.tone)} style={{ width: `${((r.usd ?? 0) / max) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </Panel>
          <Facts
            items={[
              { label: "Saved, per token", value: fmtPct(num(perToken.savings_pct), 1) },
              { label: "Saved, by GPU-hours", value: fmtPct(num(gpu.savings_pct), 0), hint: (num(gpu.savings_pct) ?? 0) < 0 ? "a loss at this volume" : undefined },
              { label: "Endpoint hours", value: fmtInt(num(c.endpoint_hours)), hint: num(c.idle_share) != null ? `${fmtPct(num(c.idle_share))} idle` : undefined },
              {
                label: "Break-even",
                value: num(c.break_even_student_requests_per_gpu_hour) != null ? `${fmtInt(num(c.break_even_student_requests_per_gpu_hour))} / GPU-hour` : "–",
                hint: "student requests needed",
              },
            ]}
          />
          <p className="rounded-xl border border-line bg-surface px-4 py-3 text-[14px] leading-relaxed text-ink">
            <span className="font-semibold">{REC_WORDS[String(c.recommendation)] ?? String(c.recommendation ?? "–")}.</span>{" "}
            {typeof c.explanation === "string" && <span className="text-ink-2">{c.explanation}</span>}
          </p>
          <p className="text-[12.5px] text-muted">Prices: {String(prices.label ?? "list prices")}, not verified against a real bill.</p>
        </div>
      )}
    </Chapter>
  );
}
