import { useEffect, useState } from "react";
import { BadgeCheck, CircleAlert, Link2, Loader2, ShieldQuestion } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RealBadge } from "@/components/ui/real-badge";
import { cn, fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { getJson, useGateway, useJudges } from "./data";
import { alarmProgress, focusVersion, latestGlobalPeriod, nusdToUsd, shortHash, type Fetched } from "./judges-model";
import { Chapter, Command, Facts, Loading, Meter, Panel, TryIt, Unavailable } from "./ui";

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === "object" ? (v as Obj) : {});
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

function origin(): string {
  return typeof window !== "undefined" && /^https?:/.test(window.location.origin) ? window.location.origin : "http://localhost:8000";
}

function Signature({ sig, what }: { sig: unknown; what: string }) {
  const s = obj(sig);
  if (s.value)
    return (
      <p className="flex items-start gap-2 text-[13.5px] text-ink-2">
        <BadgeCheck className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
        <span>
          Signed: {str(s.alg) || "Ed25519"}, key <code className="font-mono text-[12.5px]">{str(s.key_id)}</code>. Verify it offline with the commands below.
        </span>
      </p>
    );
  return (
    <p className="flex items-start gap-2 text-[13.5px] text-ink-2">
      <ShieldQuestion className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
      <span>
        Unsigned: this gateway has no signing key, so the {what} carries its SHA-256 but no signature. A gateway with{" "}
        <code className="font-mono text-[12.5px]">RATCHET_BOM_SIGNING_KEY</code> set signs every one.
      </span>
    </p>
  );
}

// ---------- certificate and BOM ----------

export function CertificateChapter() {
  const { stats, status, statsRead } = useJudges();
  const versions = stats?.versions ?? [];
  const focus = focusVersion(versions);
  const [picked, setPicked] = useState<number | null>(null);
  const vid = picked ?? focus?.id ?? null;
  const cert = useGateway<Obj>(vid != null ? `/ratchet/versions/${vid}/certificate` : null);
  const bom = useGateway<Obj>(vid != null ? `/ratchet/versions/${vid}/bom` : null);
  const c = obj(obj(obj(cert.state === "ok" ? cert.data : {}).training).certificate);
  const cur = obj(obj(obj(cert.state === "ok" ? cert.data : {}).current).certificate);
  const m = obj(bom.state === "ok" ? bom.data.manifest : {});
  const training = obj(m.training);
  const golden = obj(m.golden);
  const sources = Array.isArray(m.sources) ? (m.sources as Obj[]) : [];
  const baseLicense = obj(m.base_model_license);
  const certStatus = cert.state === "ok" ? str(cert.data.status) : "";
  return (
    <Chapter
      id="certificate"
      status={status}
      title="A precision certificate and a bill of materials per version"
      lead={
        <p>
          At training time each adapter gets a bill of materials: the exact training and held-out sets by hash, the teacher and its license, the base
          model's license and the evaluation. Its certificate is an exact (Clopper-Pearson) lower bound on precision, measured on held-out rows only.
        </p>
      }
      tryIt={<TryIt actions={["day"]} note="A day that trains a new version adds a new bill of materials and certificate." />}
    >
      {!stats ? (
        statsRead.state === "error" ? <Unavailable path="/ratchet/stats" read={statsRead} /> : <Loading what="the versions" />
      ) : versions.length === 0 ? (
        <p className="text-[13.5px] text-muted">{stats?.mode === "mock" ? "No version yet. Run a simulated day to train the first one." : "No version yet on this live demo: the teacher answers every request until Night School trains one."}</p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Which version">
            {versions.map((v) => (
              <button
                key={v.id}
                type="button"
                onClick={() => setPicked(v.id)}
                aria-pressed={v.id === vid}
                className={cn(
                  "rounded-full border px-3 py-1 font-mono text-[12.5px] transition-colors",
                  v.id === vid ? "border-ink bg-ink text-bg" : "border-line-strong text-ink-2 hover:bg-surface-2",
                )}
              >
                v{v.id} <span className="font-sans text-[11px] opacity-70">{v.state.replace("_", " ")}</span>
              </button>
            ))}
          </div>
          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            <Panel title={`Certificate, v${vid}`} aside={status && <RealBadge status={status} />}>
              {cert.state === "error" ? (
                <Unavailable path={`/ratchet/versions/${vid}/certificate`} read={cert} />
              ) : cert.state !== "ok" ? (
                <Loading what="the certificate" />
              ) : (
                <div className="space-y-3">
                  <p className="flex items-center gap-2 text-[13.5px]">
                    <Badge tone={certStatus === "certified" ? "good" : certStatus === "suspended" ? "warn" : "outline"}>{certStatus || "unknown"}</Badge>
                    <span className="text-ink-2">
                      target {fmtPct(num(c.target), 0)} at delta {num(c.delta) ?? "–"}
                    </span>
                  </p>
                  <Facts
                    className="sm:grid-cols-2 lg:grid-cols-2"
                    items={[
                      { label: "Held-out answers served", value: fmtInt(num(c.served)) },
                      { label: "Wrong among them", value: fmtInt(num(c.errors)) },
                      { label: "Precision", value: fmtPct(num(c.precision), 1) },
                      { label: "Lower bound (95%)", value: fmtPct(num(c.lcb), 1), hint: str(c.method) },
                    ]}
                  />
                  {num(cur.lcb) != null && (
                    <p className="text-[12.5px] text-muted">
                      Re-scored on today's golden set: precision {fmtPct(num(cur.precision), 1)}, lower bound {fmtPct(num(cur.lcb), 1)}.
                    </p>
                  )}
                </div>
              )}
            </Panel>
            <Panel title={`Bill of materials, v${vid}`} aside={status && <RealBadge status={status} />}>
              {bom.state === "error" ? (
                <Unavailable path={`/ratchet/versions/${vid}/bom`} read={bom} />
              ) : bom.state !== "ok" ? (
                <Loading what="the bill of materials" />
              ) : (
                <div className="space-y-3">
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
                    <dt className="text-muted">Schema</dt>
                    <dd className="font-mono text-ink-2">{str(m.schema)}</dd>
                    <dt className="text-muted">Base model</dt>
                    <dd className="text-ink-2 [overflow-wrap:anywhere]">
                      {str(obj(m.adapter).base_model)}
                      {str(baseLicense.name) && <span className="text-muted"> · {str(baseLicense.name)}</span>}
                    </dd>
                    <dt className="text-muted">Training set</dt>
                    <dd className="text-ink-2">
                      {fmtInt(num(training.examples))} examples, {str(training.targets) || "teacher answers"}
                    </dd>
                    <dt className="text-muted">Held out</dt>
                    <dd className="text-ink-2">{fmtInt(num(golden.examples))} examples, never trained on</dd>
                    <dt className="text-muted">Teachers</dt>
                    <dd className="text-ink-2 [overflow-wrap:anywhere]">
                      {sources.map((s) => `${str(s.teacher)} (${str(s.license)})`).join(", ") || "–"}
                    </dd>
                    <dt className="text-muted">Training root</dt>
                    <dd className="font-mono text-[12px] text-ink-2">{shortHash(str(obj(training.commitment).root))}</dd>
                    <dt className="text-muted">SHA-256</dt>
                    <dd className="font-mono text-[12px] text-ink-2">{shortHash(str(bom.data.sha256))}</dd>
                  </dl>
                  <Signature sig={bom.data.signature} what="bill of materials" />
                </div>
              )}
            </Panel>
          </div>
          <div className="space-y-2">
            <p className="text-[13px] text-ink-2">Check it offline:</p>
            <Command cmd={`curl -s ${origin()}/ratchet/versions/${vid}/bom > bom.json`} label="fetch the bill of materials" />
            <Command cmd="python -m ratchet.bom verify bom.json --public-key <published key>" label="verify the bill of materials" />
            <Command cmd="python -m ratchet.stats check bom.json --public-key <published key>" label="recompute the certificate" />
          </div>
        </div>
      )}
    </Chapter>
  );
}

// ---------- training-set proof ----------

export function ProofChapter() {
  const { stats, status, mock } = useJudges();
  const live = focusVersion(stats?.versions);
  const bom = useGateway<Obj>(live ? `/ratchet/versions/${live.id}/bom` : null);
  const [proof, setProof] = useState<Fetched<Obj>>({ state: "idle" });
  const [sample, setSample] = useState<Obj | null>(null);
  const [busy, setBusy] = useState(false);
  const signedRoot = str(obj(obj(obj(bom.state === "ok" ? bom.data.manifest : {}).training).commitment).root);

  const run = async () => {
    setBusy(true);
    setProof({ state: "loading" });
    try {
      const s = await getJson<Obj>("/ratchet/demo/sample-example");
      if (s.state !== "ok") {
        setProof(s.state === "error" ? s : { state: "idle" });
        return;
      }
      setSample(s.data);
      const body: Obj = {};
      for (const [k, v] of Object.entries(s.data)) if (k !== "version" && k !== "split") body[k] = v;
      const p = await getJson<Obj>(`/ratchet/versions/${String(s.data.version)}/proof?split=train`, { method: "POST", body: JSON.stringify(body) });
      setProof(p);
    } finally {
      setBusy(false);
    }
  };

  const p = proof.state === "ok" ? proof.data : null;
  const matches = p && signedRoot && str(p.root) === signedRoot;
  const firstUser = sample && Array.isArray(sample.messages) ? (sample.messages as Obj[]).filter((x) => x.role === "user").map((x) => str(x.content)).pop() : "";
  return (
    <Chapter
      id="proof"
      status={status}
      title="Prove what a version was trained on"
      lead={
        <p>
          Every bill of materials signs a Merkle root over the exact training set and another over the held-out set. Given a request, the gateway
          returns a proof that it is in the training set, or that it is not, checked against that signed root.
        </p>
      }
      tryIt={
        mock ? (
          <div className="mt-5 rounded-xl border border-dashed border-line-strong bg-bg-sunken/60 p-3 sm:p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-1 text-[12.5px] font-medium text-ink-2">
                Try it <span className="font-normal text-muted">(simulation)</span>
              </span>
              <Button size="sm" onClick={() => void run()} disabled={busy}>
                {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Link2 aria-hidden />} Prove a stored example
              </Button>
            </div>
            <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
              Takes a committed example from the demo (its prompt, never its answer) and asks for its proof. On a live gateway the proof route needs
              an admin key.
            </p>
          </div>
        ) : (
          <TryIt actions={[]} />
        )
      }
    >
      <div aria-live="polite" className="space-y-3">
        {proof.state === "error" && <Unavailable path="/ratchet/versions/{id}/proof" read={proof} />}
        {proof.state === "loading" && <Loading what="the proof" />}
        {p && (
          <Panel title={`Proof against v${String(p.version)}'s training root`} aside={status && <RealBadge status={status} />}>
            {firstUser && (
              <p className="mb-3 rounded-lg bg-bg-sunken px-3 py-2 text-[13px] text-ink-2">
                <span className="text-muted">Example: </span>
                {firstUser}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={p.result === "included" ? "good" : "neutral"}>{str(p.result)}</Badge>
              {matches ? (
                <span className="flex items-center gap-1.5 text-[13px] text-ink-2">
                  <BadgeCheck className="size-4 text-good" aria-hidden /> The root equals the one in the version's bill of materials.
                </span>
              ) : (
                <span className="flex items-center gap-1.5 text-[13px] text-ink-2">
                  <CircleAlert className="size-4 text-warn" aria-hidden /> Compare the root with the bill of materials before trusting it.
                </span>
              )}
            </div>
            <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
              <dt className="text-muted">Key</dt>
              <dd className="font-mono text-[12px] text-ink-2">{shortHash(str(p.key))}</dd>
              <dt className="text-muted">Root</dt>
              <dd className="font-mono text-[12px] text-ink-2">{shortHash(str(p.root))}</dd>
              <dt className="text-muted">Set size</dt>
              <dd className="tabular-nums text-ink-2">{fmtInt(num(p.size))}</dd>
              <dt className="text-muted">Proof length</dt>
              <dd className="tabular-nums text-ink-2">{fmtInt((obj(p.proof).siblings as unknown[] | undefined)?.length ?? 0)} hashes</dd>
            </dl>
          </Panel>
        )}
        <Command cmd="python -m ratchet.proof verify proof.json --bom bom.json --public-key <published key>" label="verify a proof offline" />
      </div>
    </Chapter>
  );
}

// ---------- savings receipts ----------

export function ReceiptsChapter() {
  const { status } = useJudges();
  const list = useGateway<{ receipts?: { period: number; global?: boolean }[]; sealed_through?: number; label?: string }>("/ratchet/receipts");
  const period = list.state === "ok" ? latestGlobalPeriod(list.data) : null;
  const doc = useGateway<Obj>(period != null ? `/ratchet/receipts/${period}` : null);
  const m = obj(doc.state === "ok" ? doc.data.manifest : {});
  const totals = obj(m.totals);
  const savings = obj(m.savings);
  const billing = obj(m.billing);
  const merkle = obj(m.merkle);
  const firstId = num(m.first_id);
  const lastId = num(m.last_id);
  const [inc, setInc] = useState<Fetched<Obj>>({ state: "idle" });
  useEffect(() => setInc({ state: "idle" }), [period]);
  const prove = async () => {
    if (firstId == null || lastId == null) return;
    const id = Math.floor(firstId + Math.random() * (lastId - firstId + 1));
    setInc({ state: "loading" });
    setInc(await getJson<Obj>(`/ratchet/receipts/${period}/proof/${id}`));
  };
  const days = list.state === "ok" ? (list.data.receipts ?? []).filter((r) => r.global).length : 0;
  return (
    <Chapter
      id="receipts"
      status={status}
      title="Savings receipts, one per day, chained"
      lead={
        <p>
          When a day ends, the gateway seals a receipt: a Merkle root over every request's metadata and cost, the totals and savings computed from
          those rows alone, and the hash of the day before. Anyone with the rows can recompute it.
        </p>
      }
      tryIt={<TryIt actions={["day"]} note="Each simulated day ends the one before it, which seals that day's receipt." />}
    >
      {list.state === "error" ? (
        <Unavailable path="/ratchet/receipts" read={list} />
      ) : list.state !== "ok" ? (
        <Loading what="the receipts" />
      ) : period == null ? (
        <p className="text-[13.5px] text-muted">No day has ended yet, so nothing is sealed.</p>
      ) : doc.state === "error" ? (
        <Unavailable path={`/ratchet/receipts/${period}`} read={doc} />
      ) : doc.state !== "ok" ? (
        <Loading what="the receipt" />
      ) : (
        <div className="space-y-4">
          <p className="text-[13px] text-muted">
            {days} sealed {days === 1 ? "day" : "days"}; showing day {period}. Label in the receipt: <span className="text-ink-2">{str(m.label) || "–"}</span>
          </p>
          <Facts
            items={[
              { label: "Requests", value: fmtInt(num(totals.requests)) },
              { label: "Student served", value: fmtInt(num(totals.student_served)) },
              { label: "Teacher served", value: fmtInt(num(totals.teacher_served)) },
              { label: "Audits", value: fmtInt(num(totals.audits)) },
              { label: "Cost", value: fmtUsd(nusdToUsd(num(totals.cost_nusd))) },
              { label: "Savings (recorded)", value: fmtUsd(nusdToUsd(num(savings.recorded_nusd))) },
              { label: "Billable, lower bound", value: fmtUsd(nusdToUsd(num(savings.billable_lo_nusd))), hint: `fee basis: ${str(billing.basis) || "–"}` },
              { label: "Fee", value: fmtUsd(nusdToUsd(num(billing.fee_nusd))) },
            ]}
          />
          <Panel title="Seal and chain" aside={status && <RealBadge status={status} />}>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
              <dt className="text-muted">Merkle root</dt>
              <dd className="font-mono text-[12px] text-ink-2">
                {shortHash(str(merkle.root))} <span className="font-sans text-muted">over {fmtInt(num(merkle.size))} requests ({str(merkle.alg)})</span>
              </dd>
              <dt className="text-muted">Previous day</dt>
              <dd className="font-mono text-[12px] text-ink-2">{m.prev_sha256 ? shortHash(str(m.prev_sha256)) : <span className="font-sans text-muted">none: the first receipt</span>}</dd>
              <dt className="text-muted">This receipt</dt>
              <dd className="font-mono text-[12px] text-ink-2">{shortHash(str(doc.data.sha256))}</dd>
            </dl>
            <div className="mt-3">
              <Signature sig={doc.data.signature} what="receipt" />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => void prove()} disabled={inc.state === "loading" || firstId == null}>
                <Link2 aria-hidden /> Prove a request is in it
              </Button>
              <span aria-live="polite" className="min-w-0 text-[13px] text-ink-2">
                {inc.state === "ok" &&
                  `Request #${String(inc.data.request_id)} is leaf ${fmtInt(num(inc.data.index))} of ${fmtInt(num(inc.data.size))}; its path has ${(inc.data.path as unknown[] | undefined)?.length ?? 0} hashes up to the signed root.`}
                {inc.state === "error" && "That request has no proof here."}
              </span>
            </div>
          </Panel>
          <Command cmd={`curl -s ${origin()}/ratchet/receipts/${period} > r.json`} label="fetch the receipt" />
          <Command cmd="python -m ratchet.receipt verify r.json --public-key <published key>" label="verify the receipt" />
        </div>
      )}
    </Chapter>
  );
}

// ---------- drift and teacher watch ----------

interface Stream {
  version: number;
  task: string;
  n: number;
  log10_martingale: number;
  alarm: boolean;
  active?: boolean;
}

export function WatchChapter() {
  const { status } = useJudges();
  const drift = useGateway<Obj>("/ratchet/drift");
  const teacher = useGateway<Obj>("/ratchet/teacher");
  const d = drift.state === "ok" ? drift.data : {};
  const t = teacher.state === "ok" ? teacher.data : {};
  const streams = (Array.isArray(d.streams) ? (d.streams as Stream[]) : []).filter((s) => s.active !== false).slice(0, 8);
  const threshold = num(d.threshold) ?? 100;
  const tThreshold = num(t.threshold) ?? 100;
  const eValue = num(t.e_value);
  return (
    <Chapter
      id="watch"
      status={status}
      title="Watching the student and the teacher"
      lead={
        <p>
          A drift alarm watches each task's stream of student confidence, with no labels needed; an unchanged stream alarms with probability at most
          1%. The teacher watch re-checks stored labels when a request repeats, and stops training if the teacher itself starts to change its answers.
        </p>
      }
      tryIt={<TryIt actions={["day"]} note="More traffic grows both: the streams below and the teacher's re-checks." />}
    >
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <Panel title="Drift alarm" aside={status && <RealBadge status={status} />}>
          {drift.state === "error" ? (
            <Unavailable path="/ratchet/drift" read={drift} />
          ) : drift.state !== "ok" ? (
            <Loading what="the drift monitor" />
          ) : (
            <div className="space-y-3">
              <p className="text-[13px] text-ink-2">
                {d.enabled === false ? "Off on this gateway." : `${fmtInt(num(d.monitored))} streams watched, ${fmtInt(num(d.alarms))} alarms.`}{" "}
                <span className="text-muted">Alarm at a test martingale of {fmtInt(threshold)}.</span>
              </p>
              <ul className="space-y-2.5">
                {streams.map((s) => (
                  <li key={`${s.version}-${s.task}`} className="min-w-0">
                    <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
                      <span className="min-w-0 font-mono text-ink-2">
                        v{s.version} · {s.task}
                      </span>
                      <span className="shrink-0 tabular-nums text-muted">
                        {s.alarm ? <span className="font-medium text-bad">alarm</span> : `10^${s.log10_martingale.toFixed(2)}`} · n {fmtInt(s.n)}
                      </span>
                    </div>
                    <div className="mt-1">
                      <Meter
                        value={s.alarm ? 1 : alarmProgress(s.log10_martingale, threshold)}
                        tone={s.alarm ? "bad" : "ink"}
                        label={`${s.task}: ${s.alarm ? "alarm" : `${Math.round(alarmProgress(s.log10_martingale, threshold) * 100)}% of the way to an alarm`}`}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
        <Panel title="Teacher watch" aside={status && <RealBadge status={status} />}>
          {teacher.state === "error" ? (
            <Unavailable path="/ratchet/teacher" read={teacher} />
          ) : teacher.state !== "ok" ? (
            <Loading what="the teacher watch" />
          ) : (
            <div className="space-y-3">
              <p className="flex items-center gap-2 text-[13px] text-ink-2">
                <Badge tone={t.frozen ? "bad" : "good"}>{t.frozen ? "training frozen" : "training allowed"}</Badge>
                epoch {fmtInt(num(t.epoch))}
              </p>
              <Facts
                className="sm:grid-cols-2 lg:grid-cols-2"
                items={[
                  { label: "Re-checks", value: fmtInt(num(t.checks)) },
                  { label: "Disagreements", value: fmtInt(num(t.disagreements)) },
                  { label: "Self-agreement", value: fmtPct(num(t.self_agreement), 1) },
                  { label: "Contested labels", value: fmtInt(num(t.contested)) },
                ]}
              />
              <div>
                <p className="mb-1 text-[12.5px] text-muted">
                  Evidence the teacher changed: e-value {eValue != null ? eValue.toFixed(2) : "–"}, freeze at {fmtInt(tThreshold)}
                </p>
                <Meter value={eValue != null && eValue > 1 ? Math.log10(eValue) / Math.log10(tThreshold) : 0} tone="warn" label="Progress of the teacher watch's evidence toward a freeze" />
              </div>
            </div>
          )}
        </Panel>
      </div>
    </Chapter>
  );
}
