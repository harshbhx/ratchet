import type { ReactNode } from "react";
import { ArrowDown, ArrowRight, Eye, Gauge, Moon, Radio, Sun } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RealBadge } from "@/components/ui/real-badge";
import { WhoBadge } from "@/components/ui/who";
import type { LedgerEvent, ServedBy, Version } from "@/lib/api";
import { cn, fmtInt, fmtPct } from "@/lib/utils";
import { eventLabel, eventTone, STATE_TONE, stateLabel } from "@/pages/dashboard/model";
import { MEASURED, TileCard, Traffic } from "@/components/measured/measured";
import { nightSchoolTile, nightSchoolWording } from "@/lib/measured-model";
import { TONE_DOT } from "@/pages/dashboard/ui";
import { useGatewayText, useGateway, useJudges } from "./data";
import { latestGlobalPeriod, parseLeaves, routeTotals, routeWords, shortHash, stageOf, studentRoute } from "./judges-model";
import { Chapter, Facts, Loading, Panel, Unavailable } from "./ui";

// ---------- the loop in one picture ----------

function Node({ title, sub, tone, children }: { title: string; sub: string; tone?: "owned" | "rented"; children?: ReactNode }) {
  return (
    <div
      className={cn(
        "min-w-0 flex-1 rounded-xl border px-3.5 py-3",
        tone === "owned" && "border-owned/35 bg-owned-soft",
        tone === "rented" && "border-rented/35 bg-rented-soft",
        !tone && "border-line bg-surface",
      )}
    >
      <p className={cn("text-[14px] font-semibold leading-snug", tone === "owned" ? "text-owned-ink" : tone === "rented" ? "text-rented-ink" : "text-ink")}>{title}</p>
      <p className="mt-0.5 text-[12.5px] leading-snug text-ink-2">{sub}</p>
      {children}
    </div>
  );
}

function Arrow({ label }: { label?: string }) {
  return (
    <div className="flex shrink-0 items-center justify-center gap-1 self-center text-muted md:flex-col" aria-hidden>
      <ArrowDown className="size-4 md:hidden" />
      <ArrowRight className="hidden size-4 md:block" />
      {label && <span className="font-mono text-[11px]">{label}</span>}
    </div>
  );
}

function Lane({ icon: Icon, title, children }: { icon: typeof Sun; title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="mb-2.5 flex items-center gap-1.5 font-mono text-[12px] text-muted">
        <Icon className="size-3.5" aria-hidden /> {title}
      </p>
      <div className="flex min-w-0 flex-col items-stretch gap-2 md:flex-row">{children}</div>
    </div>
  );
}

export function ArchitectureChapter() {
  return (
    <Chapter
      id="architecture"
      title="The loop in one picture"
      lead={
        <p>
          By day the student answers what it can and the teacher fills the gaps. By night the teacher's answers train a better student, and two gates
          decide whether it ships. <span className="text-owned-ink">Blue</span> is what you own: a LoRA adapter on NVIDIA's open Nano base.{" "}
          <span className="text-rented-ink">Orange</span> is what you rent per token.
        </p>
      }
    >
      <figure className="rounded-2xl border border-line bg-bg-sunken/50 p-4 sm:p-5">
        <div className="space-y-6">
          <Lane icon={Sun} title="By day: every request">
            <Node title="Your app" sub="OpenAI client, one base URL changed" />
            <Arrow />
            <Node title="Student first" sub="Nemotron 3 Nano 4B + your LoRA adapter" tone="owned" />
            <Arrow label="unsure" />
            <Node title="Teacher" sub="Nemotron 3 Ultra on Nebius Token Factory" tone="rented" />
            <Arrow />
            <Node title="Ledger" sub="teacher answer, redacted, with its license" />
          </Lane>
          <Lane icon={Moon} title="By night: Night School">
            <Node title="Train" sub="a new adapter on teacher answers and attested human labels" />
            <Arrow />
            <Node title="Golden gate" sub="held-out set it never trained on" />
            <Arrow />
            <Node title="Release gate" sub="shadow, then canary 10% → 50% → 100%; rollback" />
            <Arrow />
            <Node title="New student" sub="serves from the next request" tone="owned" />
          </Lane>
        </div>
        <figcaption className="mt-4 text-[12.5px] leading-relaxed text-muted">
          Written along the way: a bill of materials and a precision certificate per adapter, and a savings receipt per day, each signed when the gateway has a signing key.
        </figcaption>
      </figure>
    </Chapter>
  );
}

// ---------- the cascade ----------

interface Leaf {
  id: number;
  task: string;
  served_by: string;
  route: string;
  student_version: number | null;
}

export function CascadeChapter() {
  const { stats, status, sent, statsRead } = useJudges();
  const list = useGateway<{ receipts?: { period: number; global?: boolean }[] }>("/ratchet/receipts");
  const period = list.state === "ok" ? latestGlobalPeriod(list.data) : null;
  const leaves = useGatewayText(period != null ? `/ratchet/receipts/${period}/leaves` : null);
  const parsed = leaves.state === "ok" ? (parseLeaves(leaves.data) as unknown as Leaf[]).slice(-8).reverse() : [];
  const routes = routeTotals(stats?.tasks ?? []);
  const totalRoutes = routes.reduce((a, r) => a + r.n, 0);
  const serving = stats?.student_serving;
  return (
    <Chapter
      id="cascade"
      status={status}
      title="The cascade: student first, teacher when unsure"
      lead={
        <p>
          Each request goes to the student first. It answers three times; agreement, token probability and a valid format make its confidence. Below
          the version's calibrated threshold, the request escalates to the teacher. Every request records the route it took.
        </p>
      }
    >
      {!stats ? (
        statsRead.state === "error" ? <Unavailable path="/ratchet/stats" read={statsRead} /> : <Loading what="the gateway's stats" />
      ) : (
        <Traffic requests={stats.totals.requests} ids={["smoke", "scout", "endpoint"]}>
          <div className="space-y-4">
            <Facts
              items={[
                {
                  label: "Answers student calls now",
                  value: serving?.model ? <WhoBadge servedBy="student" model={serving.model} /> : "No student yet",
                },
                { label: "Its threshold", value: serving?.threshold != null ? serving.threshold.toFixed(2) : "–" },
                { label: "Requests so far", value: fmtInt(stats.totals.requests) },
                { label: "Day", value: fmtInt(stats.day) },
              ]}
            />
            <div className="grid min-w-0 gap-4 lg:grid-cols-2">
              <Panel title="Routes, all days" aside={status && <RealBadge status={status} />}>
                {routes.length === 0 ? (
                  <p className="text-[13.5px] text-muted">No requests yet on this gateway.</p>
                ) : (
                  <ul className="space-y-2.5">
                    {routes.map((r) => (
                      <li key={r.route} className="min-w-0">
                        <div className="flex items-baseline justify-between gap-3 text-[13px]">
                          <span className="min-w-0 text-ink-2">
                            {routeWords(r.route)} <code className="font-mono text-[11.5px] text-muted">{r.route}</code>
                          </span>
                          <span className="shrink-0 tabular-nums text-ink">
                            {fmtInt(r.n)} <span className="text-muted">({fmtPct(r.n / totalRoutes)})</span>
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line" aria-hidden>
                          <div className={cn("h-full rounded-full", studentRoute(r.route) ? "bg-owned" : "bg-rented")} style={{ width: `${(r.n / totalRoutes) * 100}%` }} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
              <Panel title={period != null ? `The last requests of day ${period}` : "The last requests"} aside={status && <RealBadge status={status} />}>
                {sent.length > 0 && (
                  <div className="mb-3">
                    <p className="mb-1.5 text-[12px] text-muted">Sent from this page</p>
                    <RequestRows
                      rows={sent.map((s) => ({ key: s.requestId, label: s.requestId.slice(0, 12) + "…", task: s.task, servedBy: s.servedBy, model: s.model, route: s.route }))}
                    />
                  </div>
                )}
                {list.state === "error" ? (
                  <Unavailable path="/ratchet/receipts" read={list} />
                ) : leaves.state === "error" ? (
                  <Unavailable path={`/ratchet/receipts/${period}/leaves`} read={leaves} />
                ) : period == null ? (
                  <p className="text-[13.5px] text-muted">{list.state === "ok" ? "No day has ended yet, so no sealed day to read from." : "Reading…"}</p>
                ) : (
                  <>
                    <RequestRows
                      rows={parsed.map((l) => ({
                        key: String(l.id),
                        label: `#${l.id}`,
                        task: l.task,
                        servedBy: l.served_by,
                        model: l.served_by === "student" && l.student_version != null ? `student-v${l.student_version}` : "",
                        route: l.route,
                      }))}
                    />
                    <p className="mt-2 text-[12px] leading-snug text-muted">From the day's sealed receipt: request metadata only, never text.</p>
                  </>
                )}
              </Panel>
            </div>
          </div>
        </Traffic>
      )}
    </Chapter>
  );
}

function RequestRows({ rows }: { rows: { key: string; label: string; task: string; servedBy: string; model: string; route: string | null }[] }) {
  return (
    <ul className="divide-y divide-line rounded-xl border border-line">
      {rows.map((r) => (
        <li key={r.key} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 px-3 py-2">
          <span className="min-w-0 truncate text-[12.5px]">
            <span className="font-mono text-[11.5px] text-muted">{r.label}</span> <span className="font-mono text-ink-2">{r.task}</span>
          </span>
          <WhoBadge servedBy={(r.servedBy || "teacher") as ServedBy} model={r.model || undefined} route={r.route} className="row-span-2 justify-self-end" />
          <span className="min-w-0 truncate text-[12px] text-muted">{routeWords(r.route)}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------- the ledger ----------

export function LedgerChapter() {
  const { stats, status, statsRead } = useJudges();
  const bom = stats?.bill_of_materials;
  const privacy = stats?.privacy as (Record<string, unknown> & { examples?: number; redacted_examples?: number; placeholders?: number; redaction?: boolean }) | undefined;
  const bySplit = (split: string) => (bom?.sources ?? []).filter((s) => s.split === split).reduce((a, s) => a + s.n, 0);
  const humans = (bom as { label_sources?: { n?: number }[] } | undefined)?.label_sources ?? [];
  return (
    <Chapter
      id="ledger"
      status={status}
      title="The ledger: every teacher answer, with its license"
      lead={
        <p>
          A teacher answer becomes training data only when it is complete and valid, after PII redaction, and it keeps the teacher's name and the
          license that allows training on it. Requests are logged as metadata only. Student answers are never training targets.
        </p>
      }
    >
      {!stats ? (
        statsRead.state === "error" ? <Unavailable path="/ratchet/stats" read={statsRead} /> : <Loading what="the ledger" />
      ) : (
        <Traffic requests={stats.totals.requests} ids={["labelling", "hosted-nano"]}>
          <div className="space-y-4">
            <Facts
              items={[
                { label: "Requests logged", value: fmtInt(stats.totals.requests) },
                { label: "Teacher answers stored", value: fmtInt(privacy?.examples ?? bySplit("train") + bySplit("golden")) },
                { label: "For training", value: fmtInt(bySplit("train")) },
                { label: "Held out (golden set)", value: fmtInt(bySplit("golden")) },
                { label: "Examples with PII redacted", value: fmtInt(privacy?.redacted_examples ?? 0), hint: privacy?.redaction === false ? "redaction is off" : "redaction on" },
                { label: "Placeholders written", value: fmtInt(privacy?.placeholders ?? 0) },
                { label: "Human labels", value: fmtInt(humans.reduce((a, h) => a + (h.n ?? 0), 0)) },
                { label: "Examples digest", value: <code className="font-mono text-[13px]">{shortHash(bom?.examples_sha256, 8, 4)}</code> },
              ]}
            />
            <Panel title="Who wrote the training data" aside={status && <RealBadge status={status} />}>
              {(bom?.sources ?? []).length === 0 ? (
                <p className="text-[13.5px] text-muted">Nothing stored yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[480px] text-left text-[13px]">
                    <thead className="text-[12px] text-muted">
                      <tr className="border-b border-line">
                        <th scope="col" className="py-2 pr-3 font-medium">Teacher</th>
                        <th scope="col" className="py-2 pr-3 font-medium">License</th>
                        <th scope="col" className="py-2 pr-3 font-medium">Split</th>
                        <th scope="col" className="py-2 text-right font-medium">Examples</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {(bom?.sources ?? []).map((s) => (
                        <tr key={`${s.teacher_model}-${s.split}`}>
                          <td className="py-2 pr-3">
                            <WhoBadge servedBy="teacher" model={s.teacher_model} />
                          </td>
                          <td className="py-2 pr-3">
                            <a href={s.license_url} target="_blank" rel="noopener noreferrer" className="text-ink underline decoration-line-strong underline-offset-2 hover:decoration-ink">
                              {s.license}
                            </a>
                          </td>
                          <td className="py-2 pr-3 text-ink-2">{s.split === "golden" ? "golden (held out)" : s.split}</td>
                          <td className="py-2 text-right tabular-nums text-ink">{fmtInt(s.n)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>
        </Traffic>
      )}
    </Chapter>
  );
}

// ---------- releases ----------

const STAGES = [
  { id: "shadow", label: "Shadow", note: "answers silently beside live", icon: Eye },
  { id: "canary10", label: "Canary 10%", note: "audited by the teacher", icon: Gauge },
  { id: "canary50", label: "Canary 50%", note: "audited by the teacher", icon: Gauge },
  { id: "live", label: "Live 100%", note: "watched for drops", icon: Radio },
] as const;

/** One stage of the release pipeline: the versions in it, or, plainly, that none has been here. */
function StageTile({ stage, here, last }: { stage: (typeof STAGES)[number]; here: Version[]; last: boolean }) {
  const Icon = stage.icon;
  const reached = here.length > 0;
  return (
    <li
      className={cn(
        "relative min-w-0 rounded-2xl border p-3.5",
        reached ? "border-owned/35 bg-owned-soft" : "border-dashed border-line-strong bg-bg-sunken/40",
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cn("grid size-7 shrink-0 place-items-center rounded-full border", reached ? "border-owned/30 bg-surface text-owned-ink" : "border-line bg-surface text-muted")}>
          <Icon className="size-3.5" aria-hidden />
        </span>
        <p className="text-[13.5px] font-semibold text-ink">{stage.label}</p>
      </div>
      <p className="mt-1.5 text-[12px] leading-snug text-muted">{stage.note}</p>
      <p className={cn("mt-2.5 text-[13px] leading-snug", reached ? "font-mono text-owned-ink" : "text-ink-2")}>
        {reached ? here.map((v) => `v${v.id}`).join(", ") : <>Not reached: no version has come here on this gateway</>}
      </p>
      {!last && <ArrowRight className="absolute -right-3 top-1/2 z-10 hidden size-4 -translate-y-1/2 text-muted xl:block" aria-hidden />}
    </li>
  );
}

/** The release log as a timeline: a rail, a dot per event in its tone, its kind, day and time, and what it said. */
function Timeline({ events }: { events: LedgerEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-line-strong bg-bg-sunken/40 px-3.5 py-3 text-[13px] leading-relaxed text-ink-2">
        No release events since this gateway started. Its log is kept in the container's ledger, which a restart empties; training, gate decisions, promotions and rollbacks appear here as they happen.
      </p>
    );
  }
  return (
    <ol className="relative max-h-[420px] space-y-4 overflow-y-auto border-l border-line-strong pl-5 pr-1">
      {events.map((e, i) => {
        const tone = eventTone(e);
        return (
          <li key={`${e.ts ?? i}-${e.kind}-${i}`} className="relative min-w-0">
            <span aria-hidden className={cn("absolute -left-[26px] top-1 size-2.5 rounded-full border-2 bg-surface", TONE_DOT[tone].split(" ").filter((c) => c.startsWith("border-")).join(" "))} />
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Badge tone={tone}>{eventLabel(e)}</Badge>
              <span className="font-mono text-[11.5px] text-muted">
                day {e.day}
                {e.ts ? ` · ${new Date(e.ts * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC` : ""}
              </span>
            </div>
            <p className="mt-1 text-[12.5px] leading-snug text-ink-2 [overflow-wrap:anywhere]">{e.detail}</p>
          </li>
        );
      })}
    </ol>
  );
}

export function ReleaseChapter() {
  const { stats, status, statsRead } = useJudges();
  const versions: Version[] = stats?.versions ?? [];
  const events = [...(stats?.events ?? [])].reverse().slice(0, 12);
  const words = MEASURED ? nightSchoolWording(MEASURED) : null;
  const night = MEASURED ? nightSchoolTile(MEASURED) : null;
  return (
    <Chapter
      id="release"
      status={status}
      title="Night School and the release gate"
      lead={
        <p>
          Each night a new adapter trains from the base model on teacher answers and attested human labels, never the student's own, and is tested on the golden set it never saw. If it passes, it
          runs in shadow, then serves a canary at 10%, 50% and 100% with teacher audits at each stage. If audited precision slips, traffic goes back
          to the last good version on its own.
        </p>
      }
    >
      {!stats ? (
        statsRead.state === "error" ? <Unavailable path="/ratchet/stats" read={statsRead} /> : <Loading what="the versions" />
      ) : (
        <div className="space-y-5">
          <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Release stages and the versions in them">
            {STAGES.map((s, i) => (
              <StageTile key={s.id} stage={s} here={versions.filter((v) => stageOf(v.state, v.traffic_pct) === s.id)} last={i === STAGES.length - 1} />
            ))}
          </ol>
          {night && <TileCard tile={night} />}
          <div className="grid min-w-0 gap-4">
            <Panel title="Versions" aside={status && <RealBadge status={status} />}>
              {versions.length === 0 ? (
                <p className="text-[13.5px] leading-relaxed text-ink-2">
                  {stats?.mode === "live" && words ? words.versions : "No version yet: every request goes to the teacher until the first night."}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[520px] text-left text-[13px]">
                    <thead className="text-[12px] text-muted">
                      <tr className="border-b border-line">
                        <th scope="col" className="py-2 pr-3 font-medium">Version</th>
                        <th scope="col" className="py-2 pr-3 font-medium">State</th>
                        <th scope="col" className="py-2 pr-3 text-right font-medium">Golden precision</th>
                        <th scope="col" className="py-2 pr-3 text-right font-medium">Coverage</th>
                        <th scope="col" className="py-2 pr-3 text-right font-medium">Trained on</th>
                        <th scope="col" className="py-2 font-medium">BOM</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {[...versions].reverse().map((v) => (
                        <tr key={v.id}>
                          <td className="py-2 pr-3 font-mono text-ink">v{v.id}</td>
                          <td className="py-2 pr-3">
                            <Badge tone={STATE_TONE[v.state] ?? "neutral"}>{stateLabel(v)}</Badge>
                          </td>
                          <td className="py-2 pr-3 text-right tabular-nums">{fmtPct(v.eval?.precision, 1)}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{fmtPct(v.eval?.coverage, 1)}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{fmtInt(v.train_examples)}</td>
                          <td className="py-2 text-ink-2">{v.bom ?? "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
            <Panel title="Release log, newest first" aside={status && <RealBadge status={status} />}>
              <Timeline events={events} />
            </Panel>
          </div>
        </div>
      )}
    </Chapter>
  );
}
