import { useRef, type ReactNode } from "react";
import { motion, useInView, useReducedMotion } from "motion/react";
import { CheckCircle2, Eye, Gauge, Target, Undo2, type LucideIcon } from "lucide-react";
import { Lamp } from "@/components/fx/lamp";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { Timeline, type TimelineItem } from "@/components/fx/timeline";
import { Badge } from "@/components/ui/badge";
import { Container } from "@/components/ui/section";
import { EASE } from "@/lib/motion";
import { LIFECYCLE_NUMBERS as N } from "../lifecycle-model";
import { Chapter, ChapterHead, Lead, Reveal, ratchetPath } from "../shared";
import { RollbackStory } from "./rollback-story";

const pct = (x: number) => `${Math.round(x * 100)}%`;

const GATES: { icon: LucideIcon; name: string; figure: string; figureLabel: string; points: string[] }[] = [
  {
    icon: Target,
    name: "Golden gate",
    figure: `≥ ${pct(N.min_precision)}`,
    figureLabel: "precision on a held-out golden set",
    points: [`Coverage of at least ${pct(N.min_coverage)}`, `No regression against the live model beyond ${Math.round(N.tolerance * 100)} points, re-scored on the same set the same night`],
  },
  {
    icon: Eye,
    name: "Shadow",
    figure: `≥ ${N.min_shadow}`,
    figureLabel: "would-serve answers on real traffic",
    points: ["Runs silently, never serves", "When it disagrees with the live student, the teacher arbitrates, and that answer becomes training data"],
  },
  {
    icon: Gauge,
    name: "Canary",
    figure: "10 → 50 → 100%",
    figureLabel: "of traffic, one stage at a time",
    points: [`At least ${N.min_audits} teacher audits at each stage`, `Audited precision of ${pct(N.live_floor)} or more to move up`],
  },
  {
    icon: Undo2,
    name: "Rollback",
    figure: `< ${pct(N.live_floor)}`,
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

function Rule({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-2 text-[13.5px] leading-relaxed text-ink-2">
      <span aria-hidden className="mt-[9px] h-px w-2.5 shrink-0 bg-line-strong" />
      <span>{children}</span>
    </li>
  );
}

function Stage({ children, badge }: { children: ReactNode; badge?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
      {badge && <div className="mb-3">{badge}</div>}
      <ul className="space-y-1.5">{children}</ul>
    </div>
  );
}

/** The release stages in order, with the gate's defaults (held to `ratchet/release.py` by the explorer's test). */
const STAGES: TimelineItem[] = [
  {
    title: "Golden gate",
    sub: "every night, before anything serves",
    content: (
      <Stage>
        <Rule>Precision of at least {pct(N.min_precision)} on the held-out half of the golden set, which training never sees</Rule>
        <Rule>Coverage of at least {pct(N.min_coverage)}: it must be sure of enough requests to be worth releasing</Rule>
        <Rule>No regression against the live version beyond {Math.round(N.tolerance * 100)} points, re-scored on the same rows the same night</Rule>
      </Stage>
    ),
  },
  {
    title: "Shadow",
    sub: "silent, on real traffic",
    content: (
      <Stage>
        <Rule>Answers every request next to the live student, and never serves a client</Rule>
        <Rule>Needs at least {N.min_shadow} answers it would have served before it is judged</Rule>
        <Rule>When the two disagree, the teacher arbitrates; an unarbitrated disagreement counts against the candidate</Rule>
      </Stage>
    ),
  },
  {
    title: "Canary 10%",
    sub: "the first slice",
    tone: "owned",
    content: (
      <Stage badge={<Badge tone="owned">10% of traffic</Badge>}>
        <Rule>Serves one request in ten; the live version keeps the rest</Rule>
        <Rule>At least {N.min_audits} teacher audits in this stage, at {pct(N.live_floor)} or better, before it moves up</Rule>
        <Rule>Every move is compare-and-set, so each decision is made exactly once however many processes tick</Rule>
      </Stage>
    ),
  },
  {
    title: "Canary 50%",
    sub: "half the traffic",
    tone: "owned",
    content: (
      <Stage badge={<Badge tone="owned">50% of traffic</Badge>}>
        <Rule>The same rule again on a bigger slice: {N.min_audits} audits at {pct(N.live_floor)} or better</Rule>
        <Rule>An operator can freeze promotions; rollbacks and alerts keep running</Rule>
      </Stage>
    ),
  },
  {
    title: "Canary 100%",
    sub: "everyone, still a canary",
    tone: "owned",
    content: (
      <Stage badge={<Badge tone="owned">100% of traffic</Badge>}>
        <Rule>Serves everything, but is still audited and still a canary until the last {N.min_audits} audits pass</Rule>
      </Stage>
    ),
  },
  {
    title: "Live",
    sub: "the new version",
    tone: "good",
    content: (
      <Stage
        badge={
          <Badge tone="good">
            <CheckCircle2 aria-hidden /> Promoted
          </Badge>
        }
      >
        <Rule>The old live version is retired; Night School may train again</Rule>
        <Rule>Audits keep measuring it, and an alert fires if its audited precision drops</Rule>
      </Stage>
    ),
  },
  {
    title: "Rolled back",
    sub: "at any canary stage",
    tone: "bad",
    content: (
      <Stage
        badge={
          <Badge tone="bad">
            <Undo2 aria-hidden /> Automatic
          </Badge>
        }
      >
        <Rule>
          When the 95% Wilson upper bound of the canary's audited precision is below {pct(N.live_floor)}, its traffic returns to the last good
          version and the candidate is marked rolled back. Nobody has to be awake.
        </Rule>
      </Stage>
    ),
  },
];

export function Safety() {
  return (
    <Chapter id="safety" className="border-t border-line pt-0 sm:pt-0 lg:pt-0">
      <Lamp className="pt-20 sm:pt-24 lg:pt-28">
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
        </Container>
      </Lamp>
      <Container className="relative">
        <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {GATES.map((g, i) => (
            <Reveal as="li" key={g.name} delay={i * 0.08} className="min-w-0">
              <SpotlightCard tone={i === 3 ? "bad" : "owned"} className="h-full rounded-2xl">
                <div className="flex h-full flex-col p-5">
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
                </div>
              </SpotlightCard>
            </Reveal>
          ))}
        </ol>
        <p className="mt-4 text-[12.5px] text-muted">
          Defaults from <code className="font-mono text-[12px]">ratchet/release.py</code>. No training runs while a release is in flight.
        </p>

        <Reveal className="mt-16 sm:mt-20">
          <div className="mb-8 max-w-2xl">
            <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-muted">The release, stage by stage</p>
            <h3 className="mt-3 text-balance text-2xl font-semibold tracking-[-0.025em] text-ink sm:text-[1.75rem]">
              From a night's training run to serving everyone, or back.
            </h3>
          </div>
          <Timeline items={STAGES} />
        </Reveal>

        <Reveal className="mt-14 sm:mt-20">
          <RollbackStory />
        </Reveal>
      </Container>
    </Chapter>
  );
}
