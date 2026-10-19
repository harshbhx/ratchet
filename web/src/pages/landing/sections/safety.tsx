import { useRef } from "react";
import { motion, useInView, useReducedMotion } from "motion/react";
import { Eye, Gauge, Target, Undo2, type LucideIcon } from "lucide-react";
import { Container } from "@/components/ui/section";
import { Chapter, ChapterHead, EASE, Lead, Reveal, ratchetPath } from "../shared";
import { RollbackStory } from "./rollback-story";

const GATES: { icon: LucideIcon; name: string; figure: string; figureLabel: string; points: string[] }[] = [
  {
    icon: Target,
    name: "Golden gate",
    figure: "≥ 95%",
    figureLabel: "precision on a held-out golden set",
    points: ["Coverage of at least 25%", "No regression against the live model beyond 2 points, re-scored on the same set the same night"],
  },
  {
    icon: Eye,
    name: "Shadow",
    figure: "≥ 100",
    figureLabel: "would-serve answers on real traffic",
    points: ["Runs silently, never serves", "When it disagrees with the live student, the teacher arbitrates, and that answer becomes training data"],
  },
  {
    icon: Gauge,
    name: "Canary",
    figure: "10 → 50 → 100%",
    figureLabel: "of traffic, one stage at a time",
    points: ["At least 20 teacher audits at each stage", "Audited precision of 92% or more to move up"],
  },
  {
    icon: Undo2,
    name: "Rollback",
    figure: "< 92%",
    figureLabel: "upper bound of audited precision",
    points: ["Uses the 95% Wilson upper bound, so it fires only when even the best case is too low", "Traffic returns to the last good version"],
  },
];

/** The one deliberate metaphor moment: the wheel turns forward, tries to slip back, and the pawl holds it. */
function Pawl() {
  const ref = useRef<SVGSVGElement | null>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -20% 0px" });
  const reduce = useReducedMotion();
  const play = inView && !reduce;
  return (
    <svg ref={ref} viewBox="0 0 160 160" className="size-28 sm:size-36" aria-hidden>
      <circle cx="80" cy="84" r="66" fill="var(--bg-sunken)" stroke="var(--line)" />
      <motion.g
        style={{ originX: "80px", originY: "84px" }}
        initial={false}
        animate={play ? { rotate: [0, 30, 30, 22, 24] } : { rotate: 24 }}
        transition={{ duration: 2.2, times: [0, 0.35, 0.55, 0.75, 1], ease: EASE, delay: 0.3 }}
      >
        <path d={ratchetPath(12, 50, 38)} transform="translate(80 84)" fill="var(--ink)" />
        <circle cx="80" cy="84" r="13" fill="var(--bg-sunken)" />
        <circle cx="80" cy="57" r="3" fill="var(--bg-sunken)" />
      </motion.g>
      <motion.g
        style={{ originX: "128px", originY: "22px" }}
        initial={false}
        animate={play ? { rotate: [0, -10, 0, 0, 0] } : { rotate: 0 }}
        transition={{ duration: 2.2, times: [0, 0.2, 0.35, 0.75, 1], ease: EASE, delay: 0.3 }}
      >
        <path d="M128 22 L104 46" stroke="var(--rented)" strokeWidth="6" strokeLinecap="round" />
        <circle cx="128" cy="22" r="6" fill="var(--rented)" />
      </motion.g>
    </svg>
  );
}

export function Safety() {
  return (
    <Chapter id="safety" className="border-t border-line">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-[520px] bg-grid [mask-image:linear-gradient(to_bottom,black,transparent)]" />
      <Container className="relative">
        <ChapterHead
          id="safety"
          title="It only turns one way."
          lead={
            <Lead>
              A ratchet has a pawl that stops it slipping back. Ratchet's pawl is four gates. A new student must pass each one before it serves
              everyone, and it is pulled automatically if it slips.
            </Lead>
          }
          aside={
            <div className="hidden justify-self-end sm:block">
              <Pawl />
            </div>
          }
        />

        <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {GATES.map((g, i) => (
            <Reveal as="li" key={g.name} delay={i * 0.08} className="relative flex h-full flex-col rounded-2xl border border-line bg-surface p-5 shadow-card">
                <div className="flex items-center justify-between">
                  <span className="inline-flex size-9 items-center justify-center rounded-xl border border-line bg-surface-2 text-ink-2">
                    <g.icon className="size-[18px]" aria-hidden />
                  </span>
                  <span className="font-mono text-[11.5px] text-muted">Gate {i + 1}</span>
                </div>
                <h3 className="mt-5 text-[17px] font-semibold tracking-[-0.01em] text-ink">{g.name}</h3>
                <div className="mt-3 font-mono text-[24px] font-medium leading-none tracking-[-0.03em] text-ink tabular">{g.figure}</div>
                <div className="mt-1.5 text-[13px] text-muted">{g.figureLabel}</div>
                <ul className="mt-4 space-y-2 border-t border-line pt-4 text-[13.5px] leading-relaxed text-ink-2">
                  {g.points.map((p) => (
                    <li key={p} className="flex gap-2">
                      <span aria-hidden className="mt-[9px] h-px w-2.5 shrink-0 bg-line-strong" />
                      <span>{p}</span>
                    </li>
                  ))}
                </ul>
            </Reveal>
          ))}
        </ol>
        <p className="mt-4 text-[12.5px] text-muted">
          Defaults from <code className="font-mono text-[12px]">ratchet/release.py</code>. No training runs while a release is in flight.
        </p>

        <Reveal className="mt-14 sm:mt-20">
          <RollbackStory />
        </Reveal>
      </Container>
    </Chapter>
  );
}
