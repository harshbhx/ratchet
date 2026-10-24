import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useInView } from "motion/react";
import { TracingBeam } from "@/components/fx/tracing-beam";
import { Container } from "@/components/ui/section";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";
import { Chapter, ChapterHead, Lead } from "../shared";
import { HowDiagram } from "./how-diagram";

/** The colour behind the diagram follows the step: the student's blue, the teacher's orange, the night, the gate. */
const TINTS = [
  "radial-gradient(closest-side at 30% 25%, color-mix(in srgb, var(--owned) 18%, transparent), transparent 100%)",
  "radial-gradient(closest-side at 75% 25%, color-mix(in srgb, var(--rented) 20%, transparent), transparent 100%)",
  "radial-gradient(closest-side at 50% 60%, color-mix(in srgb, var(--ink) 10%, transparent), transparent 100%)",
  "radial-gradient(closest-side at 50% 85%, color-mix(in srgb, var(--good) 16%, transparent), transparent 100%)",
];

const STEPS: { title: string; body: ReactNode; chips: string[] }[] = [
  {
    title: "The student tries first.",
    body: (
      <>
        Every request goes to your small model, Nemotron 3 Nano 4B with a LoRA adapter. It answers three times. Confidence combines token
        probability, agreement across the three samples, and a valid format, because no single signal from a small model can be trusted.
      </>
    ),
    chips: ["3 samples", "logprobs", "per-version threshold"],
  },
  {
    title: "Unsure? The teacher answers.",
    body: (
      <>
        Below its calibrated threshold, the request escalates to Nemotron 3 Ultra on Nebius Token Factory. Your app gets the teacher's answer.
        The ledger keeps it, with the teacher's name and the license that permits training on it.
      </>
    ),
    chips: ["escalation", "provenance", "OpenMDW-1.1"],
  },
  {
    title: "Night School retrains.",
    body: (
      <>
        Each night Night School fine-tunes a new adapter from the base model, on every teacher answer so far. It is built to run as a Nebius Serverless Job; so far the adapters were trained by hand on T4 GPUs. Never on the
        student's own answers. Then it is tested on a held-out golden set that training never sees.
      </>
    ),
    chips: ["Serverless Job · L40S (planned)", "from base, cumulative", "golden set held out"],
  },
  {
    title: "The release gate decides.",
    body: (
      <>
        A new version runs in shadow first, then serves a canary: 10%, then 50%, then 100% of traffic, with teacher audits at every stage. If
        it slips, traffic goes back to the last good version on its own.
      </>
    ),
    chips: ["shadow", "canary 10% → 50% → 100%", "auto rollback"],
  },
];

function Step({ i, active, onActive }: { i: number; active: boolean; onActive: (i: number) => void }) {
  const ref = useRef<HTMLLIElement | null>(null);
  const inView = useInView(ref, { margin: "-48% 0px -48% 0px" });
  useEffect(() => {
    if (inView) onActive(i);
  }, [inView, i, onActive]);
  const s = STEPS[i];
  return (
    <li ref={ref} className="flex flex-col justify-center py-8 lg:min-h-[60vh] lg:py-0">
      <div className={cn("transition-opacity duration-500 lg:opacity-45", active && "lg:opacity-100")}>
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "inline-flex size-8 items-center justify-center rounded-full border font-mono text-[13px] tabular transition-colors duration-500",
              active ? "border-ink bg-ink text-bg" : "border-line-strong bg-bg text-muted",
            )}
          >
            {i + 1}
          </span>
          <span className="font-mono text-[12px] uppercase tracking-[0.14em] text-muted">Step {i + 1} of 4</span>
        </div>
        <h3 className="mt-5 text-balance text-2xl font-semibold tracking-[-0.025em] text-ink sm:text-[1.75rem]">{s.title}</h3>
        <p className="mt-4 max-w-[52ch] text-pretty text-[16.5px] leading-relaxed text-ink-2">{s.body}</p>
        <ul className="mt-5 flex flex-wrap gap-2">
          {s.chips.map((c) => (
            <li key={c} className="rounded-full border border-line bg-surface px-2.5 py-1 font-mono text-[11.5px] text-ink-2">
              {c}
            </li>
          ))}
        </ul>
      </div>
      {/* Mobile and tablet: one diagram per step, no sticky. */}
      <div className="mt-7 rounded-[20px] border border-line bg-surface px-1.5 py-3 shadow-card sm:p-5 lg:hidden">
        <HowDiagram active={i} compact className="mx-auto block w-full max-w-[520px]" />
      </div>
    </li>
  );
}

export function How() {
  const [active, setActive] = useState(0);
  return (
    <Chapter id="how" className="pb-12 sm:pb-16 lg:pb-20">
      <Container>
        <ChapterHead
          id="how"
          title="Every night, one click forward."
          lead={
            <Lead>
              A request flows through four parts. By day the student answers what it can and the teacher fills the gaps. By night the teacher's
              answers become a better student, and the gate decides whether it ships.
            </Lead>
          }
        />
        <div className="mt-10 lg:mt-6 lg:grid lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] lg:gap-16">
          {/* The beam lights the steps as far as the reader has scrolled; below lg it is hidden and the steps stand alone. */}
          <TracingBeam lineClassName="hidden lg:block" contentClassName="lg:pl-12">
            <ol>
              {STEPS.map((_, i) => (
                <Step key={i} i={i} active={active === i} onActive={setActive} />
              ))}
            </ol>
          </TracingBeam>
          <div className="relative hidden lg:block">
            <div className="sticky top-[calc(50vh-260px)]">
              {/* the tint behind the card follows the active step (the sticky-scroll idea) */}
              {TINTS.map((bg, i) => (
                <motion.div
                  key={i}
                  aria-hidden
                  initial={false}
                  animate={{ opacity: i === active ? 1 : 0 }}
                  transition={{ duration: 0.8, ease: EASE }}
                  className="pointer-events-none absolute -inset-20"
                  style={{ background: bg }}
                />
              ))}
              <div className="relative rounded-[24px] border border-line bg-surface p-6 shadow-card">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div className="flex gap-1.5" aria-hidden>
                    {STEPS.map((_, i) => (
                      <span key={i} className={cn("h-1 rounded-full transition-all duration-500", i === active ? "w-8 bg-ink" : "w-4 bg-line-strong")} />
                    ))}
                  </div>
                  <div className="relative h-5 min-w-0 flex-1 overflow-hidden text-right" aria-live="polite">
                    <AnimatePresence mode="popLayout" initial={false}>
                      <motion.span
                        key={active}
                        initial={{ y: 12, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: -12, opacity: 0 }}
                        transition={{ duration: 0.35, ease: EASE }}
                        className="absolute inset-x-0 truncate font-mono text-[12px] text-muted"
                      >
                        {active + 1} · {STEPS[active].title.replace(/\.$/, "")}
                      </motion.span>
                    </AnimatePresence>
                  </div>
                </div>
                <HowDiagram active={active} className="block w-full" />
              </div>
            </div>
          </div>
        </div>
      </Container>
    </Chapter>
  );
}
