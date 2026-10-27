import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ExternalLink, EyeOff, FileSignature, KeyRound, ShieldAlert, ShieldCheck, Users, type LucideIcon } from "lucide-react";
import { api, type ClientStats, type RedactPreview, type Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { ShareBar } from "./tables";
import { DataTable, Empty, Panel, td, tdNum, th, thNum } from "./ui";

// Synthetic sample: the card number is the standard Visa test number, the rest is made up.
const SAMPLE =
  "Hi, I'm Maya. I was charged twice on card 4111 1111 1111 1111 for order #48213. Reach me at maya.k@example.com or +91 98765 43210.";
const LABELS: Record<string, string> = {
  EMAIL: "email",
  PHONE: "phone",
  CARD: "card",
  UPI: "UPI id",
  GSTIN: "GSTIN",
  IBAN: "IBAN",
  AADHAAR: "Aadhaar",
  SSN: "SSN",
  PAN: "PAN",
  IP: "IP address",
  SECRET: "secret",
};

function Tile({
  icon: Icon,
  title,
  status,
  children,
  foot,
}: {
  icon: LucideIcon;
  title: string;
  status: ReactNode;
  children: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line bg-bg-sunken/50 p-3.5">
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-2 text-[13px] font-medium text-ink">
          <span className="grid size-7 place-items-center rounded-lg border border-line bg-surface text-muted">
            <Icon className="size-3.5" aria-hidden />
          </span>
          {title}
        </p>
        {status}
      </div>
      <div className="mt-3 flex-1 text-pretty text-[13px] leading-5 text-ink-2">{children}</div>
      {foot && <div className="mt-3 border-t border-line pt-2.5 text-[12px] leading-5 text-muted">{foot}</div>}
    </div>
  );
}

function Redacted({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\[[A-Z]+\])/g).map((part, i) =>
        /^\[[A-Z]+\]$/.test(part) ? (
          <mark key={i} className="rounded-md border border-line-strong bg-surface-2 px-1 py-px font-mono text-[11.5px] font-medium text-ink">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

/** Type anything and see what the ledger would store. Calls POST /ratchet/redact, which stores nothing. */
function RedactPlayground() {
  const [text, setText] = useState(SAMPLE);
  const [out, setOut] = useState<RedactPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const inputId = useId();

  useEffect(() => {
    const id = ++seq.current;
    const t = window.setTimeout(async () => {
      try {
        const r = await api.redact(text);
        if (id === seq.current) {
          setOut(r);
          setError(null);
        }
      } catch (e) {
        if (id === seq.current) setError(e instanceof Error ? e.message : "The gateway did not answer.");
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [text]);

  const found = Object.entries(out?.found ?? {});
  return (
    // `redactor` is the story page's link target (/dashboard#redactor); scroll-mt clears the sticky header.
    <div id="redactor" className="min-w-0 scroll-mt-20 rounded-xl border border-line p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <label htmlFor={inputId} className="text-[13px] font-medium text-ink">
          Try the redactor
        </label>
        <span className="text-[12px] text-muted">Runs on the gateway. Nothing you type is stored.</span>
      </div>
      <textarea
        id={inputId}
        value={text}
        onChange={(e) => setText(e.target.value.slice(0, 5000))}
        rows={3}
        spellCheck={false}
        className="mt-2 block w-full resize-y rounded-lg border border-line bg-surface px-3 py-2 text-[13px] leading-5 text-ink placeholder:text-muted focus:border-line-strong"
        placeholder="Paste a support message, a log line, anything."
      />
      <p className="mt-3 text-[12px] font-medium text-muted">Stored as training data</p>
      <div aria-live="polite" className="mt-1 min-h-[3.5rem] rounded-lg bg-bg-sunken px-3 py-2 text-[13px] leading-6 text-ink-2 [overflow-wrap:anywhere]">
        {error ? <span className="text-bad">Could not reach the redactor: {error}</span> : out ? <Redacted text={out.text} /> : <span className="text-muted">…</span>}
      </div>
      <div className="mt-2 flex min-h-6 flex-wrap items-center gap-1.5">
        {found.length === 0 && out && !error && <span className="text-[12px] text-muted">No identifiers found. The text is stored as is.</span>}
        {found.map(([k, n]) => (
          <Badge key={k} tone="neutral">
            {n} × {LABELS[k] ?? k}
          </Badge>
        ))}
        {out && !out.enabled && (
          <Badge tone="warn" className="ml-auto">
            <ShieldAlert aria-hidden /> Redaction is off on this gateway
          </Badge>
        )}
      </div>
    </div>
  );
}

function Clients({ clients, keys }: { clients: ClientStats[]; keys: boolean }) {
  if (clients.length === 0) {
    return (
      <Empty icon={Users} title={keys ? "No client traffic yet" : "No API clients"} className="h-full">
        {keys ? (
          "Each API key's requests, cost and savings appear here as they arrive."
        ) : (
          <>
            Set <span className="font-mono">RATCHET_API_KEYS</span> to give each client its own key; cost and savings are then reported per client.
          </>
        )}
      </Empty>
    );
  }
  return (
    <div className="min-w-0 rounded-xl border border-line px-4 pt-3 sm:px-5">
      <p className="text-[13px] font-medium text-ink">By API client</p>
      <DataTable caption="Traffic by API client" minWidth={420}>
        <thead>
          <tr>
            <th className={th}>Client</th>
            <th className={thNum}>Requests</th>
            <th className={thNum} title="Share of requests answered by the student (Nano with your adapter)">
              Your adapter
            </th>
            <th className={thNum}>Cost reduction</th>
          </tr>
        </thead>
        <tbody>
          {clients.map((c) => (
            <tr key={c.client} className="transition-colors last:[&>td]:border-b-0 hover:bg-surface-2/70">
              <td className={td}>
                <span className="font-mono text-[12.5px] font-medium text-ink">{c.client}</span>
              </td>
              <td className={tdNum}>{fmtInt(c.requests)}</td>
              <td className={tdNum}>
                <ShareBar share={c.student_share} esc={0} />
              </td>
              <td className={tdNum} title={`${fmtUsd(c.cost_usd)} spent vs ${fmtUsd(c.baseline_usd)} all-teacher`}>
                <span className="text-ink-2">{fmtPct(c.savings_pct)}</span>
                <span className="ml-1.5 text-[11.5px] text-muted">{fmtUsd(c.cost_usd)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </div>
  );
}

export function TrustPanel({ stats }: { stats: Stats }) {
  const { privacy, security } = stats;
  if (!privacy || !security) return null; // gateway older than v0.4
  const clients = stats.clients ?? [];
  const withBom = stats.versions.filter((v) => v.bom);
  const signed = withBom.filter((v) => v.bom === "signed").length;
  const quota = security.daily_requests_per_key;

  return (
    <Panel
      id="trust"
      title="Trust and privacy"
      description="What keeps personal data out of training, who may call the API, and proof of what each adapter was trained on."
    >
      <div className="grid gap-3 md:grid-cols-3">
        <Tile
          icon={EyeOff}
          title="PII redaction"
          status={
            privacy.redaction ? (
              <Badge tone="good">
                <ShieldCheck aria-hidden /> On
              </Badge>
            ) : (
              <Badge tone="warn">
                <ShieldAlert aria-hidden /> Off
              </Badge>
            )
          }
          foot={<>Emails, phones, cards, IBANs, national IDs, IPs and API secrets. The teacher still sees the original request.</>}
        >
          <span className="text-[22px] font-semibold leading-none tracking-[-0.02em] text-ink tabular">{fmtInt(privacy.redacted_examples)}</span>
          <span className="text-muted"> of {fmtInt(privacy.examples)} stored examples</span>
          <p className="mt-1">
            had identifiers replaced with placeholders ({fmtInt(privacy.placeholders)} in total) before they became training data.
            {stats.mode === "mock" && privacy.redacted_examples === 0 && " The mock traffic contains none; try the redactor below."}
          </p>
        </Tile>

        <Tile
          icon={KeyRound}
          title="Gateway API keys"
          status={
            security.api_keys ? (
              <Badge tone="good">
                <ShieldCheck aria-hidden /> Required
              </Badge>
            ) : (
              <Badge tone="warn">
                <ShieldAlert aria-hidden /> Open
              </Badge>
            )
          }
          foot="Pages, Scout and /healthz stay public either way."
        >
          {security.api_keys ? (
            <>
              <span className="font-mono text-[12.5px]">/v1/*</span> needs <span className="font-mono text-[12.5px]">Authorization: Bearer</span> with a
              configured key. {clients.length === 1 ? "1 client has" : `${fmtInt(clients.length)} clients have`} sent traffic.
              <p className="mt-1">{quota ? `Quota: ${fmtInt(quota)} requests per key per UTC day.` : "No daily quota per key."}</p>
            </>
          ) : (
            <>
              Anyone who can reach <span className="font-mono text-[12.5px]">/v1/*</span> can use it. Set{" "}
              <span className="font-mono text-[12.5px]">RATCHET_API_KEYS</span> before exposing a live gateway.
            </>
          )}
        </Tile>

        <Tile
          icon={FileSignature}
          title="Adapter bill of materials"
          status={
            security.bom_signing ? (
              <Badge tone="good">
                <ShieldCheck aria-hidden /> Ed25519
              </Badge>
            ) : (
              <Badge tone="outline">Unsigned</Badge>
            )
          }
          foot={
            <>
              Check one with <span className="font-mono text-ink-2">python -m ratchet.bom verify</span>.
            </>
          }
        >
          {withBom.length === 0 ? (
            "Each trained adapter gets its own bill of materials: teachers, licenses, dataset and golden-set hashes, and the evaluation it passed."
          ) : security.bom_signing ? (
            <>
              <span className="text-[22px] font-semibold leading-none tracking-[-0.02em] text-ink tabular">{signed}</span>
              <span className="text-muted"> of {withBom.length} versions</span>
              <p className="mt-1">
                carry a signed bill of materials. Key <span className="font-mono text-[12.5px] text-ink">{security.bom_key_id}</span>{" "}
                <a
                  href="/ratchet/bom/public-key"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 font-medium text-owned-ink underline-offset-2 hover:underline"
                >
                  public key <ExternalLink className="size-3" aria-hidden />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              </p>
            </>
          ) : (
            <>
              {withBom.length} {withBom.length === 1 ? "version carries" : "versions carry"} a bill of materials with a SHA-256. Set{" "}
              <span className="font-mono text-[12.5px]">RATCHET_BOM_SIGNING_KEY</span> to sign them.
            </>
          )}
        </Tile>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <RedactPlayground />
        <Clients clients={clients} keys={security.api_keys} />
      </div>
    </Panel>
  );
}
