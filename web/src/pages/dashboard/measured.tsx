import { ArrowUpRight } from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import { EXAMS, ScoreBars } from "@/components/charts/exam-charts";
import { HF_EXAMS, HF_MODEL, HF_TRAIN } from "@/components/layout/nav-model";
import { timesBase } from "@/lib/exams-model";

const LINK = "inline-flex items-center gap-1 font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink";

/**
 * The one panel on this page that is not about the running gateway: what the trained adapters scored on the two exams (real
 * models, not a simulation). It sits first, so a visitor sees what was measured before the gateway's own numbers.
 * Every figure is from `src/data/exams.json`, which a test recomputes from the saved answers.
 */
export function MeasuredResults() {
  const x1 = timesBase(EXAMS, "exam1").toFixed(1);
  const x2 = timesBase(EXAMS, "exam2").toFixed(1);
  return (
    <section aria-labelledby="measured-title" className="rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <RealBadge status="measured" />
        <span className="text-[12.5px] text-muted">Real models, two exams, 22 to 24 Oct 2026. Not the simulation.</span>
      </div>
      <h2 id="measured-title" className="mt-3 text-balance text-[18px] font-semibold tracking-[-0.015em] text-ink sm:text-[20px]">
        The trained adapter scores {x2} to {x1} times the untrained model, on the strict exam
      </h2>
      <p className="mt-1.5 max-w-[68ch] text-[13.5px] leading-relaxed text-ink-2">
        A query counts only if it returns exactly the reference rows. It was trained on a borrowed GPU, on synthetic data. At a 95%
        precision bar it answers 1 of 86 questions on exam 1 and none on exam 2 by itself, so most traffic still goes to the teacher.
      </p>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="min-w-0 rounded-xl border border-line bg-surface-2/40 p-3 sm:p-4">
          <p className="text-[12.5px] font-semibold text-ink">Exam 1 · {EXAMS.exams[0].rows} questions · written by the teacher</p>
          <ScoreBars exam="exam1" className="mt-2" />
        </div>
        <div className="min-w-0 rounded-xl border border-line bg-surface-2/40 p-3 sm:p-4">
          <p className="text-[12.5px] font-semibold text-ink">Exam 2 · {EXAMS.exams[1].rows} questions · written by a different model</p>
          <ScoreBars exam="exam2" className="mt-2" />
        </div>
      </div>
      <p className="mt-4 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px]">
        <a href={HF_MODEL} target="_blank" rel="noopener noreferrer" className={LINK}>
          Adapter <ArrowUpRight aria-hidden className="size-3" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
        <a href={HF_TRAIN} target="_blank" rel="noopener noreferrer" className={LINK}>
          Training data <ArrowUpRight aria-hidden className="size-3" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
        <a href={HF_EXAMS} target="_blank" rel="noopener noreferrer" className={LINK}>
          Exams and answers <ArrowUpRight aria-hidden className="size-3" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      </p>
    </section>
  );
}
