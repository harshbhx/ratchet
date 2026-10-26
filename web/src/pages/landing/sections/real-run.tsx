import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { EXAMS, ScoreBars } from "@/components/charts/exam-charts";
import { TOOLCALL, ToolCallBlock } from "@/components/charts/toolcall-block";
import { pct, timesBase } from "@/lib/exams-model";
import { LINKS, Reveal } from "../shared";

/**
 * The results, right after the hero: what two trained adapters scored on two exams, who wrote each exam, and the one limit that
 * matters. Short on purpose; the detail (difficulty, round 1 against round 2, the scoring rule, the fingerprints) is on the judges'
 * page and in the docs. Every figure comes from `src/data/exams.json`, which `scripts/text2sql/exam_numbers.py` computes from the
 * saved answers (a test fails if it drifts). The simulation chapters further down stay labelled as simulation.
 */
const LINK = "inline-flex items-center gap-1 font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink";

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <li className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
      <p className="text-[28px] font-semibold leading-none tracking-[-0.03em] text-ink tabular-nums sm:text-[34px]">{value}</p>
      <p className="mt-2 text-[13px] leading-snug text-ink-2">{label}</p>
    </li>
  );
}

export function RealRun() {
  const e1 = EXAMS.scores.exam1;
  const e2 = EXAMS.scores.exam2;
  const x1 = timesBase(EXAMS, "exam1").toFixed(1);
  const x2 = timesBase(EXAMS, "exam2").toFixed(1);
  return (
    <section id="real-run" aria-labelledby="real-run-title" className="relative scroll-mt-16 border-t border-line py-14 sm:py-16">
      <Container>
        <Reveal>
          <div className="flex flex-wrap items-center gap-2">
            <RealBadge status="measured" />
            <span className="text-[12.5px] text-muted">Real models, two exams, 22 to 24 Oct 2026. Not the simulation.</span>
          </div>
          <h2 id="real-run-title" className="mt-4 max-w-3xl text-balance text-2xl font-semibold tracking-[-0.02em] text-ink sm:text-[2.1rem] sm:leading-[1.12]">
            A small model taught by a big one: {x2} to {x1} times the untrained score, for about $17 on record.
          </h2>
          <p className="mt-3 max-w-[60ch] text-[15px] leading-relaxed text-ink-2">
            About $12 of teacher answers and about $5 of GPU time (estimates from the logged token counts and GPU seconds, not an invoice). Scored the strict way: a query counts only if it returns exactly the reference rows.
          </p>

          <ul className="mt-7 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile value={`${x2}–${x1}×`} label="the untrained model's score, on both exams" />
            <Tile value={`${Math.round(e1.r2.rate * 100)}% · ${Math.round(e2.r2.rate * 100)}%`} label="exact match on exam 1 · exam 2, the round 2 adapter" />
            <Tile value="2 authors" label="exam 1 by the teacher, exam 2 by a different model" />
            <Tile value="0 overlaps" label="exam and training data checked; every score recomputable" />
          </ul>

          <div className="mt-6 grid gap-5 lg:grid-cols-2">
            <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
              <p className="text-[13px] font-semibold text-ink">
                Exam 1 · {EXAMS.exams[0].rows} questions · written by Nemotron 3 Ultra (the teacher)
              </p>
              <ScoreBars exam="exam1" className="mt-2" />
            </div>
            <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
              <p className="text-[13px] font-semibold text-ink">
                Exam 2 · {EXAMS.exams[1].rows} questions · written by Claude Sonnet 5.5, for testing only
              </p>
              <ScoreBars exam="exam2" className="mt-2" />
            </div>
          </div>

          <p className="mt-5 max-w-[78ch] rounded-xl border border-line bg-surface-2 px-4 py-3 text-[13.5px] leading-relaxed text-ink-2">
            <span className="font-medium text-ink">The honest limit.</span> It does not replace the teacher yet: when we ask for 95% right, the student
            answers 1 of 86 questions on exam 1 and none on exam 2, and the gate sends the rest to the teacher, which is the gate doing its job. The strict exam has one reference
            answer, so even the teacher scores {pct(e1.teacher.rate)} and {pct(e2.teacher.rate)}. Trained on a borrowed GPU.
          </p>

          <ToolCallBlock where="bottom" />

          <p className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-[13.5px]">
            <Link to="/judges#exams" className={LINK}>
              Every number, by difficulty
            </Link>
            <a href={LINKS.hfModel} target="_blank" rel="noopener noreferrer" className={LINK}>
              Adapter on Hugging Face <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
            </a>
            <a href={LINKS.hfTrain} target="_blank" rel="noopener noreferrer" className={LINK}>
              Training data <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
            </a>
            <a href={LINKS.hfExams} target="_blank" rel="noopener noreferrer" className={LINK}>
              Exams and answers <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
            </a>
            {TOOLCALL ? (
              <a href={LINKS.hfToolModel} target="_blank" rel="noopener noreferrer" className={LINK}>
                Tool-calling adapter <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
              </a>
            ) : null}
          </p>
        </Reveal>
      </Container>
    </section>
  );
}
