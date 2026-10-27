import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { motion, useInView, useReducedMotion } from "motion/react";
import { CircleDashed, CloudCog, FlaskConical, Info, PowerOff, Radio, ServerCog, ShieldCheck, TrendingDown, type LucideIcon } from "lucide-react";
import { api, type CostReport, type Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container } from "@/components/ui/section";
import { SIM_COMMAND, SIM_COST_REPORT, SIM_DAYS, SIM_SOURCE } from "@/lib/sim";
import { cn, fmtInt } from "@/lib/utils";
import { codeParts, fmtDecade, fmtHours, fmtMoney, fmtRate, fmtSignedPct, fmtTokenPrice, recMeta, windowLabel } from "../../dashboard/economics-model";
import {
  barRows,
  baselineName,
  billOf,
  fmtAbout,
  fmtSmallUsd,
  isCostReport,
  meter,
  meterSummary,
  outcomes,
  partWidths,
  rowText,
  simulateLine,
  tellsTheStory,
  verdict,
  type Bill,
  type BarPart,
  type BillSource,
  type MeterMark,
} from "../bill-model";
import { Chapter, ChapterHead, EASE, ExtLink, LINKS, Lead, Reveal } from "../shared";
import { PrecisionFact, RealCostTiles } from "./real-numbers";

const REC_ICON: Record<string, LucideIcon> = {
  self_host: ServerCog,
  hosted_fallback: CloudCog,
  scale_to_zero: PowerOff,
  insufficient_data: CircleDashed,
};

const FILL: Record<BarPart["fill"], string> = {
  rented: "bg-rented",
  owned: "bg-owned",
  "owned-soft": "bg-owned/45",
};

/**
 * GET /ratchet/cost once the chapter is near the screen, and again whenever a stats poll shows the traffic moved. The
 * last good report stays when a refresh fails; null on a static preview or a gateway older than v0.5, which leaves
 * the reference simulation on screen.
 */
function useStoryCost(stats: Stats | null, near: boolean): CostReport | null {
  const [report, setReport] = useState<CostReport | null>(null);
  const seq = useRef(0);
  const key = stats ? `${stats.day}:${stats.totals.requests}` : null;
  useEffect(() => {
    if (!near || key == null) return;
    const id = ++seq.current;
    api
      .cost()
      .then((r) => {
        if (id === seq.current && isCostReport(r)) setReport(r);
      })
      .catch(() => {
        /* keep the last good report */
      });
  }, [near, key]);
  return report;
}

/** A figure inside a sentence: kept on one line, so a minus sign never wraps away from its number. */
function Num({ children }: { children: ReactNode }) {
  return <b className="whitespace-nowrap font-semibold text-ink tabular">{children}</b>;
}

function ReportText({ text }: { text: string }) {
  return (
    <>
      {codeParts(text).map((p, i) =>
        p.code ? (
          <code key={i} className="rounded border border-line bg-surface-2 px-1 py-px font-mono text-[12px] text-ink [overflow-wrap:anywhere]">
            {p.text}
          </code>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  );
}

function SourceToggle({ value, onChange, mock }: { value: BillSource; onChange: (s: BillSource) => void; mock: boolean }) {
  const opts: Array<{ key: BillSource; label: string }> = [
    { key: "gateway", label: mock ? "This gateway (mock)" : "This gateway" },
    { key: "reference", label: "Reference run" },
  ];
  return (
    <div role="group" aria-label="Whose numbers to show" className="inline-flex rounded-full border border-line bg-bg-sunken p-0.5">
      {opts.map((o) => (
        <button
          key={o.key}
          type="button"
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
          className={cn(
            "h-7 whitespace-nowrap rounded-full px-3 text-[12.5px] font-medium transition-colors",
            value === o.key ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * One bill as a stacked bar on the shared dollar scale. It grows from zero the first time it comes into view; with
 * reduced motion it is drawn at its final width from the start.
 */
function BillBar({ parts, max, inView, reduce, delay }: { parts: BarPart[]; max: number; inView: boolean; reduce: boolean; delay: number }) {
  const widths = partWidths(parts, max);
  return (
    <div aria-hidden className="mt-1.5 flex h-3 w-full overflow-hidden rounded-full bg-bg-sunken">
      {parts.map((p, i) => {
        const width = `${widths[i]}%`;
        return (
          <motion.span
            key={p.key}
            className={cn("h-full first:rounded-l-full last:rounded-r-full", FILL[p.fill])}
            initial={reduce ? false : { width: 0 }}
            animate={reduce ? { width } : inView ? { width } : undefined}
            style={reduce ? { width } : undefined}
            transition={reduce ? { duration: 0 } : { duration: 0.9, ease: EASE, delay: delay + i * 0.12 }}
          />
        );
      })}
    </div>
  );
}

function Bills({ bill, name, sub }: { bill: Bill; name: string; sub: ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  const reduce = !!useReducedMotion();
  const rows = barRows(bill, name);
  const max = Math.max(...rows.map((r) => r.total), 0);
  const perTok = verdict(bill.perToken, bill.baseline, name);
  const gpu = verdict(bill.gpuHours, bill.baseline, name);
  return (
    <div ref={ref} className="min-w-0">
      <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">The same traffic, billed two ways</h3>
      <p className="mt-1 text-[13px] text-muted">{sub}</p>
      <div className="mt-5 space-y-5">
        {rows.map((r, i) => (
          <div key={r.key}>
            <div className="flex items-baseline justify-between gap-3 text-[13px]">
              <span className="min-w-0 text-ink-2">{r.label}</span>
              <span className="shrink-0 font-mono text-[13.5px] font-medium text-ink tabular">{fmtMoney(r.total)}</span>
            </div>
            <BillBar parts={r.parts} max={max} inView={inView} reduce={reduce} delay={0.15 + i * 0.25} />
            {r.key === "baseline" && <p className="mt-1.5 text-[12px] text-muted">Every request answered by the teacher, per token.</p>}
            {r.key === "per_token" && <p className="mt-1.5 text-[12px] text-muted">{perTok.text}, as if a provider hosted the student.</p>}
            {r.key === "gpu_hours" && (
              <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted">
                {gpu.kind === "more" && (
                  <Badge tone="warn">
                    <TrendingDown aria-hidden /> Costs more
                  </Badge>
                )}
                <span>{gpu.kind === "more" ? `${gpu.text}: the GPU bills every hour it runs.` : `${gpu.text}, with the GPU-hours paid.`}</span>
              </p>
            )}
          </div>
        ))}
      </div>
      <ul className="mt-5 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-ink-2" aria-label="Legend">
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm bg-rented" /> Teacher calls (rented)
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm bg-owned" /> Student endpoint hours (yours)
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm bg-owned/45" /> Student tokens, training
        </li>
      </ul>
      {/* A table grows to its content whatever its width, so the 1px sr-only box wraps it instead. */}
      <div className="sr-only">
        <table>
          <caption>What the same traffic cost, three ways</caption>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <th scope="row">{r.label}</th>
                <td>{rowText(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <GpuParts bill={bill} />
    </div>
  );
}

/** Where the GPU-hour total comes from, line by line. */
function GpuParts({ bill }: { bill: Bill }) {
  const lines: Array<[ReactNode, number | null]> = [
    ["Teacher calls: escalations, audits, arbitrations, pinned tasks", bill.teacherUsd],
    [
      <>
        Student endpoint: {fmtHours(bill.endpointHours)}
        {bill.endpointPerHour != null && <> at {fmtSmallUsd(bill.endpointPerHour)} an hour</>}
      </>,
      bill.endpointUsd,
    ],
    [<>Night School training: {fmtHours(bill.trainingHours)}</>, bill.trainingUsd],
  ];
  if (bill.hostedStudentUsd != null && bill.hostedStudentUsd > 0) lines.push([`Hosted student tokens, ${bill.idleBuckets} without endpoint hours`, bill.hostedStudentUsd]);
  if (lines.every(([, v]) => v == null)) return null;
  return (
    <dl className="mt-5 space-y-1.5 border-t border-line pt-4 text-[12.5px]">
      {lines.map(([k, v], i) => (
        <div key={i} className="flex items-baseline justify-between gap-3">
          <dt className="min-w-0 text-muted">{k}</dt>
          <dd className="shrink-0 font-mono text-ink-2 tabular">{fmtMoney(v)}</dd>
        </div>
      ))}
      <div className="flex items-baseline justify-between gap-3 border-t border-line pt-1.5">
        <dt className="font-medium text-ink">By GPU-hours paid</dt>
        <dd className="shrink-0 font-mono font-medium text-ink tabular">{fmtMoney(bill.gpuHours.total)}</dd>
      </div>
      {bill.idleShare != null && bill.idleShare > 0 && (
        <p className="pt-1 text-[12px] text-muted">
          {fmtSignedPct(bill.idleShare)} of the endpoint hours fell {bill.idleBuckets} the student got no request.
        </p>
      )}
    </dl>
  );
}

function Mark({ kind }: { kind: MeterMark }) {
  if (kind === "dot") return <span className="block size-3.5 rounded-full bg-owned ring-[3px] ring-surface" />;
  if (kind === "tick") return <span className="block h-5 w-[3px] rounded-full bg-ink ring-2 ring-surface" />;
  return <span className="block size-3.5 rounded-full border-2 border-ink-2 bg-surface" />;
}

function BreakEven({ bill }: { bill: Bill }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  const reduce = useReducedMotion();
  const { rows, scale } = meter(bill);
  const be = bill.breakEven;
  return (
    <div ref={ref} className="min-w-0">
      <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">When does self-hosting pay?</h3>
      <p className="mt-1 text-[13px] text-muted">When the teacher spend it replaces covers the GPU's hourly price.</p>

      <div className="mt-5 rounded-xl border border-line bg-bg-sunken/70 p-4">
        <p className="font-mono text-[12px] leading-5 text-muted">
          <span className="text-owned-ink">break-even</span> = endpoint $ per hour ÷ teacher $ avoided per student answer
        </p>
        <p className="mt-1 font-mono text-[13px] leading-5 text-ink-2 tabular">
          = {fmtSmallUsd(bill.endpointPerHour)} ÷ {fmtSmallUsd(bill.avoided, 3)}
        </p>
        {be != null ? (
          <p className="mt-2 text-[13px] leading-snug text-ink-2">
            <span className="mr-1.5 text-[26px] font-semibold leading-none tracking-[-0.03em] text-ink tabular">{fmtRate(be)}</span>
            student answers per endpoint hour, about {fmtAbout(be * 24)} a day.
          </p>
        ) : (
          <p className="mt-2 text-[13px] leading-snug text-muted">
            {bill.avoided == null ? "Unknown until the student has answered requests." : "Its answers avoid no teacher spend, so it never breaks even."}
          </p>
        )}
      </div>

      {scale && (
        <div className="mt-5" role="img" aria-label={meterSummary(rows, scale.log)}>
          <div aria-hidden className="relative h-7">
            <span className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-bg-sunken" />
            {be != null && (
              <span
                className="absolute top-1/2 h-2 -translate-y-1/2 rounded-r-full bg-owned-soft"
                style={{ left: `${scale.at(be) * 100}%`, right: 0 }}
              />
            )}
            {scale.ticks.map((t) => (
              <span key={t.value} className="absolute inset-y-1 w-px bg-line-strong/70" style={{ left: `${t.at * 100}%` }} />
            ))}
            {rows.map((r, i) =>
              r.value == null ? null : (
                <motion.span
                  key={r.key}
                  className="absolute top-1/2"
                  initial={reduce ? false : { left: "0%", opacity: 0 }}
                  animate={inView || reduce ? { left: `${scale.at(r.value) * 100}%`, opacity: 1 } : undefined}
                  style={reduce ? { left: `${scale.at(r.value) * 100}%` } : undefined}
                  transition={reduce ? { duration: 0 } : { duration: 1.1, ease: EASE, delay: 0.2 + i * 0.15 }}
                >
                  <span className="block -translate-x-1/2 -translate-y-1/2">
                    <Mark kind={r.mark} />
                  </span>
                </motion.span>
              ),
            )}
          </div>
          <div aria-hidden className="relative mt-1 h-4 text-[11px] text-muted tabular">
            {scale.ticks.map((t) => (
              <span
                key={t.value}
                className="absolute top-0 whitespace-nowrap"
                style={{ left: `${t.at * 100}%`, transform: t.at <= 0 ? "none" : t.at >= 1 ? "translateX(-100%)" : "translateX(-50%)" }}
              >
                {t.label}
              </span>
            ))}
          </div>
          <p aria-hidden className="mt-1 text-right text-[11px] text-muted">
            {scale.log ? `Answers per hour, log scale: each gridline is ${fmtDecade(scale.ratio)}× the last.` : "Answers per hour, from 0."}
          </p>
          <ul aria-hidden className="mt-3 space-y-1.5 text-[12.5px]">
            {rows.map((r) => (
              <li key={r.key} className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-2">
                <span className="flex justify-center">
                  <Mark kind={r.mark} />
                </span>
                <span className="min-w-0 text-ink-2">{r.label}</span>
                <span className="font-mono text-ink tabular">{r.value == null ? "–" : fmtRate(r.value)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-4 text-pretty text-[13px] leading-relaxed text-ink-2">
        {bill.observed != null && be != null ? (
          <>
            This traffic reached <Num>{fmtRate(bill.observed)}</Num> student answers per endpoint hour,{" "}
            {bill.utilization != null && <Num>{fmtSignedPct(bill.utilization)}</Num>} {bill.utilization != null ? "of" : "against"} break-even.{" "}
          </>
        ) : bill.observed == null ? (
          <>No endpoint hours are recorded here yet, so there is no volume to compare. </>
        ) : null}
        {bill.crossover != null && bill.hostedPerAnswer != null && (
          <>
            The hosted Nano costs <Num>{fmtSmallUsd(bill.hostedPerAnswer, 3)}</Num> an answer, so the endpoint beats it only above{" "}
            <Num>{fmtRate(bill.crossover)}</Num> an hour.
          </>
        )}
      </p>
    </div>
  );
}

function Recommendation({ bill }: { bill: Bill }) {
  const meta = recMeta(bill.recommendation);
  const Icon = REC_ICON[bill.recommendation] ?? Info;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-10">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2.5">
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">What Ratchet recommends</h3>
          <Badge tone={meta.tone}>
            <Icon aria-hidden /> {meta.label}
          </Badge>
        </div>
        {bill.explanation ? (
          <blockquote className="mt-3 border-l-2 border-line-strong pl-4 text-pretty text-[14px] leading-relaxed text-ink-2">
            <ReportText text={bill.explanation} />
          </blockquote>
        ) : (
          <p className="mt-3 text-[13.5px] text-muted">This gateway sent no explanation.</p>
        )}
      </div>
      <div className="min-w-0">
        <p className="text-[12px] font-medium uppercase tracking-[0.1em] text-muted">The four answers it can give</p>
        <ul className="mt-2.5 space-y-2">
          {outcomes(bill.recommendation).map((o) => {
            const OIcon = REC_ICON[o.key] ?? Info;
            return (
              <li
                key={o.key}
                aria-current={o.current ? "true" : undefined}
                className={cn(
                  "flex gap-2.5 rounded-xl border px-3 py-2 text-[12.5px] leading-snug",
                  o.current ? "border-owned/40 bg-owned-soft/60" : "border-line",
                )}
              >
                <OIcon className={cn("mt-0.5 size-3.5 shrink-0", o.current ? "text-owned-ink" : "text-muted")} aria-hidden />
                <span className="min-w-0">
                  <span className="font-medium text-ink">{o.label}.</span> <span className="text-ink-2">{o.when}</span>
                  {o.current && <span className="ml-1 whitespace-nowrap font-medium text-owned-ink">This report.</span>}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function Prices({ report }: { report: CostReport }) {
  const p = report.prices;
  if (!p) return null;
  const gpu = (n: number | undefined, rate: number | undefined) => (rate == null ? null : `${n ?? 1} GPU${(n ?? 1) === 1 ? "" : "s"} at ${fmtSmallUsd(rate)} per GPU-hour`);
  const items = [
    gpu(p.student_gpus, p.student_gpu_usd_per_hour) && `student endpoint ${gpu(p.student_gpus, p.student_gpu_usd_per_hour)}`,
    gpu(p.train_gpus, p.train_gpu_usd_per_hour) && `training ${gpu(p.train_gpus, p.train_gpu_usd_per_hour)}`,
    p.teacher_usd_per_m_tokens && `teacher ${fmtTokenPrice(p.teacher_usd_per_m_tokens)} per million tokens`,
    p.fallback_student_usd_per_m_tokens && `hosted Nano ${fmtTokenPrice(p.fallback_student_usd_per_m_tokens)}`,
  ].filter(Boolean) as string[];
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px] text-muted">
      {p.verified ? (
        <Badge tone="good">
          <ShieldCheck aria-hidden /> Prices confirmed by the operator
        </Badge>
      ) : (
        <Badge tone="outline">
          <Info aria-hidden /> Unverified list prices
        </Badge>
      )}
      <span>Prices used: {items.join("; ")}.</span>
    </p>
  );
}

export function HonestBill({ stats }: { stats: Stats | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const near = useInView(ref, { once: true, margin: "800px 0px 800px 0px" });
  const report = useStoryCost(stats, near);
  const [choice, setChoice] = useState<BillSource | null>(null);
  const source: BillSource = report ? (choice ?? (tellsTheStory(report) ? "gateway" : "reference")) : "reference";
  const shown = source === "gateway" && report ? report : SIM_COST_REPORT;
  const bill = billOf(shown);
  const name = baselineName(bill.teacherModel);
  const mock = stats?.mode === "mock";

  const span = source === "gateway" ? windowLabel(shown) : null;
  const sub =
    source === "gateway"
      ? `${fmtInt(shown.requests ?? 0)} requests on this gateway${span ? `, ${span.toLowerCase()}` : ""}.`
      : `${fmtInt(shown.requests ?? 0)} support tickets over ${SIM_DAYS.length} days in the reference simulation.`;

  return (
    <Chapter id="bill" className="border-t border-line">
      <Container>
        {/* Starts the report fetch when the chapter is near the screen. */}
        <div ref={ref} aria-hidden />
        <ChapterHead
          id="bill"
          title="A GPU bills by the hour, busy or not."
          lead={
            <>
              <Lead>
                The savings above price each student answer per token, as if a provider hosted the student. A student you host yourself runs
                on a GPU endpoint, and that endpoint is billed for every hour it runs, whether requests arrive or not.
              </Lead>
              <Lead>
                So Ratchet prices the same traffic both ways, works out the volume at which self-hosting breaks even, and says plainly when
                it does not pay.
              </Lead>
            </>
          }
          aside={
            <figure className="max-w-sm rounded-2xl border border-line bg-surface p-5 shadow-card lg:mb-1">
              <blockquote className="text-[15px] font-medium leading-snug tracking-[-0.01em] text-ink">
                “Self-hosting only wins on cost at high utilization.”
              </blockquote>
              <figcaption className="mt-3 text-[12.5px] leading-relaxed text-muted">
                The second-place project in Nebius's Serverless AI Builders Challenge.{" "}
                <ExtLink href={LINKS.nebiusChallenge}>Nebius blog</ExtLink>
              </figcaption>
            </figure>
          }
        />

        <Reveal className="mt-12">
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">The real bill so far</h3>
          <p className="mb-4 mt-1 max-w-[70ch] text-[13px] leading-relaxed text-muted">
            Measured: what the teacher's labels cost for each round and what one call costs at list prices. Live: the demo's own savings
            counter. Break-even and GPU-hours have no real run behind them yet, so they follow, labelled Simulation.
          </p>
          <RealCostTiles stats={stats} />
          <PrecisionFact className="mt-4" />
        </Reveal>

        <Reveal className="mt-14">
          <h3 className="mb-4 text-[15px] font-semibold tracking-[-0.01em] text-ink">Break-even and GPU-hours</h3>
          <div className="overflow-hidden rounded-[24px] border border-line bg-surface shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-b border-line px-4 py-3.5 sm:px-6">
              <div className="flex flex-wrap items-center gap-2">
                {source === "reference" ? (
                  <Badge tone="outline">
                    <FlaskConical aria-hidden /> Simulation · reference run
                  </Badge>
                ) : mock ? (
                  <Badge tone="outline">
                    <FlaskConical aria-hidden /> Simulation (mock models) · this gateway
                  </Badge>
                ) : (
                  <Badge tone="good">
                    <Radio aria-hidden /> Live · this gateway
                  </Badge>
                )}
                <span className="font-mono text-[12px] text-muted">GET /ratchet/cost</span>
              </div>
              {report && <SourceToggle value={source} onChange={setChoice} mock={mock} />}
            </div>

            <div aria-live="polite" className="sr-only">
              {source === "gateway" ? "Showing this gateway's cost report." : "Showing the reference simulation's cost report."}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
              <div className="border-b border-line p-4 sm:p-6 lg:border-b-0 lg:border-r">
                <Bills key={source} bill={bill} name={name} sub={sub} />
              </div>
              <div className="p-4 sm:p-6">
                <BreakEven key={source} bill={bill} />
              </div>
            </div>

            <div className="border-t border-line p-4 sm:p-6">
              <Recommendation bill={bill} />
            </div>

            <div className="space-y-3 border-t border-line bg-surface-2/60 px-4 py-4 sm:px-6">
              <Prices report={shown} />
              {source === "reference" ? (
                <>
                  <p className="text-[12.5px] leading-relaxed text-muted">
                    {SIM_SOURCE} The simulator keeps the student endpoint up 24 hours a day and times each night's training job (simulation
                    assumptions). Not real model quality or a real bill.
                  </p>
                  <CodeBlock title="reproduce · terminal" copy={SIM_COMMAND} className="bg-surface [&_pre]:whitespace-pre-wrap [&_pre]:[overflow-wrap:anywhere]">
                    {tok.k("python")} {SIM_COMMAND.replace(/^python /, "")}
                    {"\n"}
                    {tok.c(`# ${simulateLine(SIM_COST_REPORT)}`)}
                  </CodeBlock>
                </>
              ) : (
                <p className="text-[12.5px] leading-relaxed text-muted">
                  {mock
                    ? "This gateway runs mock models: its traffic and compute hours are simulated, so these are simulation numbers, not a real bill."
                    : "Endpoint hours are what the operator recorded (python -m ratchet.cost endpoint up and down); an hour nobody recorded costs nothing here."}{" "}
                  The same report: <code className="font-mono text-[12px] text-ink-2">python -m ratchet.cost report</code>.
                </p>
              )}
            </div>
          </div>
        </Reveal>
      </Container>
    </Chapter>
  );
}
