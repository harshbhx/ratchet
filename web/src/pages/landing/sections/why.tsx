import type { ReactNode } from "react";
import { motion } from "motion/react";
import { ArrowDown, EyeOff, FileCode2, FileSignature, Receipt, ScrollText, Split, Target, Undo2, type LucideIcon } from "lucide-react";
import { BentoGrid, bentoSpan } from "@/components/fx/bento-grid";
import { GlowOrbs } from "@/components/fx/glow-orbs";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { Container, Eyebrow, H2, Lead } from "@/components/ui/section";
import { REPO_URL, cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";
import { WHY, type WhyId } from "../why-model";
import { Reveal } from "../shared";

const ICON: Record<WhyId, LucideIcon> = {
  licenses: ScrollText,
  bom: FileSignature,
  golden: Target,
  release: Undo2,
  redaction: EyeOff,
  receipts: Receipt,
};

const TONE: Record<WhyId, "owned" | "rented" | "ink" | "good" | "bad"> = {
  licenses: "rented",
  bom: "ink",
  golden: "owned",
  release: "bad",
  redaction: "ink",
  receipts: "good",
};

/* ---------- the little drawings in each card: what the code does, in miniature ---------- */

/** Chapter 02 draws the license check and the bill of materials in full; these two cards point there instead of repeating them. */
function SeeChapter() {
  return (
    <a href="#catch" className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink">
      Shown in full in chapter 02 <ArrowDown className="size-3.5" aria-hidden />
    </a>
  );
}

function GoldenMini() {
  return (
    <div className="flex items-center gap-2" aria-hidden>
      <div className="grid flex-1 grid-cols-8 gap-1">
        {Array.from({ length: 16 }, (_, i) => (
          <motion.span
            key={i}
            initial={{ opacity: 0, scale: 0.6 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true, margin: "0px 0px -10% 0px" }}
            transition={{ duration: 0.35, delay: 0.1 + i * 0.03, ease: EASE }}
            className={cn("aspect-square rounded-[3px]", i % 2 === 0 ? "bg-owned/35" : "bg-owned")}
          />
        ))}
      </div>
      <div className="shrink-0 space-y-1 text-[11px] text-muted">
        <p className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px] bg-owned/35" /> calibrate
        </p>
        <p className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px] bg-owned" /> measure
        </p>
      </div>
    </div>
  );
}

function ReleaseMini() {
  const stages = ["shadow", "10%", "50%", "100%"];
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-hidden>
      {stages.map((s, i) => (
        <motion.span
          key={s}
          initial={{ opacity: 0, x: -6 }}
          whileInView={{ opacity: 1, x: 0 }}
          viewport={{ once: true, margin: "0px 0px -10% 0px" }}
          transition={{ duration: 0.4, delay: 0.15 + i * 0.18, ease: EASE }}
          className={cn(
            "rounded-full border px-2.5 py-1 font-mono text-[11.5px]",
            i === 0 ? "border-line-strong text-ink-2" : "border-owned/40 bg-owned-soft text-owned-ink",
          )}
        >
          {s}
        </motion.span>
      ))}
      <motion.span
        initial={{ opacity: 0 }}
        whileInView={{ opacity: 1 }}
        viewport={{ once: true, margin: "0px 0px -10% 0px" }}
        transition={{ duration: 0.4, delay: 0.95 }}
        className="inline-flex items-center gap-1 rounded-full border border-bad/40 bg-bad-soft px-2.5 py-1 font-mono text-[11.5px] text-bad"
      >
        <Undo2 className="size-3" /> rollback if it slips
      </motion.span>
    </div>
  );
}

function RedactMini() {
  return (
    <p className="rounded-lg border border-line bg-bg-sunken/70 px-2.5 py-2 font-mono text-[11.5px] leading-relaxed text-ink-2">
      Refund <span className="rounded bg-owned-soft px-1 text-owned-ink">[CARD]</span> to{" "}
      <span className="rounded bg-owned-soft px-1 text-owned-ink">[EMAIL]</span>, ticket from{" "}
      <span className="rounded bg-owned-soft px-1 text-owned-ink">[PHONE]</span>
    </p>
  );
}

function ReceiptMini() {
  return (
    <div className="flex items-center gap-2 font-mono text-[11px] text-muted" aria-hidden>
      {["day 8", "day 9", "day 10"].map((d, i) => (
        <span key={d} className="flex items-center gap-2">
          {i > 0 && <Split className="size-3 rotate-90 text-line-strong" />}
          <span className="rounded-md border border-line bg-bg-sunken/70 px-2 py-1 text-ink-2">{d} ✓</span>
        </span>
      ))}
    </div>
  );
}

const MINI: Record<WhyId, () => ReactNode> = {
  licenses: SeeChapter,
  bom: SeeChapter,
  golden: GoldenMini,
  release: ReleaseMini,
  redaction: RedactMini,
  receipts: ReceiptMini,
};

export function Why() {
  return (
    <section id="why" aria-labelledby="why-title" className="relative scroll-mt-16 border-t border-line py-16 sm:py-20 lg:py-24">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-dots [mask-image:radial-gradient(ellipse_70%_60%_at_50%_40%,black,transparent)]" />
      <GlowOrbs tones={["owned", "rented"]} />
      <Container className="relative">
        <Reveal className="max-w-3xl">
          <Eyebrow>Why it is different</Eyebrow>
          <H2 id="why-title" className="mt-5">
            A router saves money. Ratchet also proves what it did.
          </H2>
          <Lead className="mt-5 max-w-[64ch]">
            Six things in the code that a cascade router or a distillation API does not do. Each card names the file that does it and the
            invariant that states it.
          </Lead>
        </Reveal>
        <BentoGrid className="mt-12">
          {WHY.map((w, i) => {
            const Icon = ICON[w.id];
            const Mini = MINI[w.id];
            return (
              <motion.li
                key={w.id}
                initial={{ opacity: 0, y: 18 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "0px 0px -8% 0px" }}
                transition={{ duration: 0.6, ease: EASE, delay: (i % 3) * 0.08 }}
                className={cn("min-w-0", bentoSpan(w.wide))}
              >
                <SpotlightCard tone={TONE[w.id]} className="h-full">
                  <div className="flex flex-1 flex-col p-5 sm:p-6">
                    <div className="flex items-center justify-between gap-3">
                      <span className="inline-flex size-9 items-center justify-center rounded-xl border border-line bg-surface-2 text-ink-2">
                        <Icon className="size-[18px]" aria-hidden />
                      </span>
                      {w.invariant && <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-muted">Invariant {w.invariant.n}</span>}
                    </div>
                    <h3 className="mt-4 text-balance text-[18px] font-semibold leading-snug tracking-[-0.015em] text-ink">{w.title}</h3>
                    <p className="mt-2 text-pretty text-[14px] leading-relaxed text-ink-2">{w.body}</p>
                    <div className="mt-4 flex flex-1 flex-col justify-end">
                      <Mini />
                    </div>
                  </div>
                  <ul className="flex flex-wrap gap-1.5 border-t border-line px-5 py-3 sm:px-6">
                    {w.files.map((f) => (
                      <li key={f} className="min-w-0 max-w-full">
                        <a
                          href={`${REPO_URL}/blob/main/${f}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex max-w-full items-center gap-1.5 rounded-md font-mono text-[11.5px] text-muted transition-colors hover:text-ink"
                        >
                          <FileCode2 className="size-3.5 shrink-0" aria-hidden />
                          <span className="min-w-0 truncate">{f}</span>
                          <span className="sr-only"> (opens on GitHub)</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </SpotlightCard>
              </motion.li>
            );
          })}
        </BentoGrid>
      </Container>
    </section>
  );
}
