import { useRef } from "react";
import { motion, useInView } from "motion/react";
import { CheckCircle2, FileCheck2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Container } from "@/components/ui/section";
import { cn } from "@/lib/utils";
import { Chapter, ChapterHead, EASE, ExampleBadge, ExtLink, LINKS, Lead, Reveal } from "../shared";

const TEACHERS = [
  { model: "gpt-*", ok: false, terms: "OpenAI Business Terms §3.3(e)", href: LINKS.openai },
  { model: "claude-*", ok: false, terms: "Anthropic Commercial Terms §D.4(a)", href: LINKS.anthropic },
  { model: "gemini-*", ok: false, terms: "Google Gemini API Terms", href: LINKS.gemini },
  { model: "nvidia/Nemotron-3-Ultra-550b-a55b", ok: true, terms: "OpenMDW-1.1: no restrictions on outputs", href: LINKS.ultra },
];

function LicenseCheck() {
  const ref = useRef<HTMLDivElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  return (
    <div ref={ref} className="flex h-full flex-col overflow-hidden rounded-[22px] border border-line bg-surface shadow-card">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <p className="truncate font-mono text-[12px] text-muted">
          <span className="text-ink-2">licenses.require_distillable</span>(teacher)
        </p>
        <span className="hidden shrink-0 font-mono text-[11px] text-muted sm:inline">ratchet/licenses.py</span>
      </div>
      <ul className="flex-1 divide-y divide-line">
        {TEACHERS.map((t, i) => (
          <li key={t.model} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-3.5 sm:px-5">
            <code className={cn("min-w-0 font-mono text-[13px] [overflow-wrap:anywhere]", t.ok ? "text-ink" : "text-ink-2")}>{t.model}</code>
            <motion.span
              initial={{ opacity: 0, scale: 0.9 }}
              animate={inView ? { opacity: 1, scale: 1 } : undefined}
              transition={{ duration: 0.35, delay: 0.35 + i * 0.32, ease: EASE }}
              className="row-span-2 self-center justify-self-end"
            >
              {t.ok ? (
                <Badge tone="good">
                  <CheckCircle2 aria-hidden /> Allowed
                </Badge>
              ) : (
                <Badge tone="bad">
                  <XCircle aria-hidden /> Refused
                </Badge>
              )}
            </motion.span>
            <motion.span
              initial={{ opacity: 0 }}
              animate={inView ? { opacity: 1 } : undefined}
              transition={{ duration: 0.4, delay: 0.45 + i * 0.32 }}
              className="text-[12.5px] text-muted"
            >
              <a href={t.href} target="_blank" rel="noopener noreferrer" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
                {t.terms}
              </a>
            </motion.span>
          </li>
        ))}
      </ul>
      <p className="border-t border-line bg-surface-2/60 px-4 py-3 text-[12.5px] text-muted sm:px-5">
        A closed teacher raises <code className="font-mono text-[12px] text-ink-2">LicenseError</code> when the gateway starts.
      </p>
    </div>
  );
}

const BOM = [
  ["Teacher", "nvidia/Nemotron-3-Ultra-550b-a55b", true],
  ["License", "OpenMDW-1.1", false],
  ["Split", "train", true],
  ["Examples", "2,085", true],
  ["Dataset SHA-256", "07ac2ca35bac7895…", true],
  ["Signature", "Ed25519, per adapter", false],
] as const;

function BomCard() {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-[22px] border border-line bg-surface shadow-card">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <p className="flex items-center gap-2 text-[13px] font-medium text-ink">
          <FileCheck2 className="size-4 text-muted" aria-hidden /> Bill of materials
        </p>
        <ExampleBadge />
      </div>
      <p className="px-4 pt-4 text-[13.5px] leading-relaxed text-ink-2 sm:px-5">
        Every training source, the license that permits it, and a fingerprint of the exact dataset. Each adapter gets its own copy at training
        time, signed so anyone with the public key can check it.
      </p>
      <dl className="flex flex-1 flex-col justify-center space-y-3 px-4 py-5 sm:px-5">
        {BOM.map(([k, v, mono]) => (
          <div key={k} className="flex items-baseline gap-3 text-[13px]">
            <dt className="shrink-0 text-muted">{k}</dt>
            <span aria-hidden className="h-px min-w-4 flex-1 translate-y-[-3px] border-b border-dotted border-line-strong" />
            <dd className={cn("min-w-0 text-right text-ink [overflow-wrap:anywhere]", mono && "font-mono text-[12.5px]")}>{v}</dd>
          </div>
        ))}
      </dl>
      <div className="border-t border-line bg-bg-sunken px-4 py-3 font-mono text-[12px] text-muted sm:px-5">
        <span className="text-owned-ink">GET</span> /ratchet/versions/<span className="text-ink-2">{"{id}"}</span>/bom
      </div>
    </div>
  );
}

export function Catch() {
  return (
    <Chapter id="catch" className="border-y border-line bg-bg-sunken/60">
      <Container>
        <ChapterHead
          id="catch"
          title="The shortcut breaks the terms."
          lead={
            <>
              <Lead>
                The obvious fix is to train a small model on a frontier model's answers. Their terms forbid exactly that:{" "}
                <ExtLink href={LINKS.openai} className="whitespace-nowrap">OpenAI §3.3(e)</ExtLink> and{" "}
                <ExtLink href={LINKS.anthropic} className="whitespace-nowrap">Anthropic §D.4(a)</ExtLink> rule out using outputs to train
                competing models.
              </Lead>
              <Lead>
                So Ratchet only learns from teachers whose license allows it.{" "}
                <ExtLink href={LINKS.ultra} className="whitespace-nowrap">Nemotron 3 Ultra</ExtLink> is under OpenMDW-1.1, which places no
                restrictions on outputs. Closed teachers are refused in code, and every training example records who wrote it, under which
                license.
              </Lead>
            </>
          }
        />
        <div className="mt-12 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <Reveal className="h-full">
            <LicenseCheck />
          </Reveal>
          <Reveal className="h-full" delay={0.1}>
            <BomCard />
          </Reveal>
        </div>
        <p className="mt-5 text-[12.5px] text-muted">Not legal advice: check each license yourself before relying on it.</p>
      </Container>
    </Chapter>
  );
}
