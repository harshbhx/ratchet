import { useState } from "react";
import { Check, Copy, ExternalLink, Fingerprint, ShieldCheck } from "lucide-react";
import type { Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn, fmtInt, safeHref } from "@/lib/utils";
import { EMPTY_SHA } from "./model";
import { DataTable, Panel, td, tdNum, th, thNum } from "./ui";

export function Provenance({ bom }: { bom: Stats["bill_of_materials"] }) {
  const [copied, setCopied] = useState(false);
  const empty = bom.sources.length === 0 || bom.examples_sha256 === EMPTY_SHA;
  const total = bom.sources.reduce((a, s) => a + s.n, 0);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(bom.examples_sha256);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <Panel id="provenance" title="Provenance" description="Bill of materials for the training data behind your adapter.">
      {empty ? (
        <p className="rounded-xl border border-dashed border-line-strong bg-bg-sunken/60 px-4 py-6 text-center text-[13px] text-muted">
          No training data yet. Every teacher answer is logged with its teacher and license as it arrives.
        </p>
      ) : (
        <DataTable caption="Training examples by teacher, license and split">
          <thead>
            <tr>
              <th className={th}>Teacher · license</th>
              <th className={th}>Split</th>
              <th className={thNum}>Examples</th>
            </tr>
          </thead>
          <tbody>
            {bom.sources.map((s) => {
              const href = safeHref(s.license_url);
              const [org, ...rest] = s.teacher_model.split("/");
              return (
                <tr key={`${s.teacher_model}-${s.split}`}>
                  <td className={cn(td, "whitespace-normal")}>
                    <div className="break-all font-mono text-[12px] leading-4 text-ink-2" title={s.teacher_model}>
                      {rest.length ? (
                        <>
                          <span className="text-muted">{org}/</span>
                          {rest.join("/")}
                        </>
                      ) : (
                        s.teacher_model
                      )}
                    </div>
                    <div className="mt-1 text-[12px]">
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-owned-ink underline-offset-2 hover:underline">
                          {s.license}
                          <ExternalLink className="size-3" aria-hidden />
                          <span className="sr-only">(license, opens in a new tab)</span>
                        </a>
                      ) : (
                        <span className="text-ink-2">{s.license}</span>
                      )}
                    </div>
                  </td>
                  <td className={td}>
                    <Badge tone={s.split === "golden" ? "outline" : "neutral"}>{s.split}</Badge>
                  </td>
                  <td className={tdNum}>{fmtInt(s.n)}</td>
                </tr>
              );
            })}
          </tbody>
          {bom.sources.length > 1 && (
            <tfoot>
              <tr>
                <td className={cn(td, "border-b-0 text-muted")} colSpan={2}>
                  Total
                </td>
                <td className={cn(tdNum, "border-b-0 font-medium text-ink")}>{fmtInt(total)}</td>
              </tr>
            </tfoot>
          )}
        </DataTable>
      )}

      <div className="mt-4 rounded-xl border border-line bg-bg-sunken px-3 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-ink-2">
            <Fingerprint className="size-3.5 text-muted" aria-hidden /> Dataset fingerprint <span className="font-normal text-muted">SHA-256</span>
          </span>
          <button
            type="button"
            onClick={copy}
            disabled={empty}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink disabled:opacity-40"
            aria-label="Copy dataset fingerprint"
          >
            {copied ? <Check className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <code className="mt-1.5 block break-all font-mono text-[11.5px] leading-[1.15rem] text-ink-2">{empty ? "no examples yet" : bom.examples_sha256}</code>
      </div>

      <p className="mt-3 flex gap-2 text-pretty text-[12.5px] leading-5 text-muted">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
        <span>
          Every example records the teacher that wrote it and the license that allows training on it, so you can show which data, and whose terms, a version was
          built from. Closed models whose terms forbid this are refused as teachers.
        </span>
      </p>
    </Panel>
  );
}
