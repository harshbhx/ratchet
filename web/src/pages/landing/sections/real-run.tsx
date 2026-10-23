import { Link } from "react-router";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { DifficultyBars, EXAMS, ExamAuthors, FlipBars, ScoreBars } from "@/components/charts/exam-charts";
import { MODEL_IDS, pct, scoreText, timesBase } from "@/lib/exams-model";
import { Reveal } from "../shared";

/**
 * The real runs, between the story and the honest bill: what two trained adapters did on two exams, who wrote each exam, and what the
 * numbers do and do not show. Every figure comes from `src/data/exams.json`, which `scripts/text2sql/exam_numbers.py` computes from the
 * saved answers (a test fails if the file drifts). The simulation chapters stay labelled as simulation.
 */
const LINK = "font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink";

export function RealRun() {
  const e1 = EXAMS.scores.exam1;
  const e2 = EXAMS.scores.exam2;
  return (
    <section id="real-run" aria-labelledby="real-run-title" className="relative scroll-mt-16 border-t border-line py-14 sm:py-16">
      <Container>
        <Reveal>
          <div className="flex flex-wrap items-center gap-2">
            <RealBadge status="measured" />
            <span className="text-[12.5px] text-muted">22 to 24 Oct 2026 · two real training runs, two exams, not the simulation</span>
          </div>
          <h2 id="real-run-title" className="mt-4 max-w-3xl text-balance text-2xl font-semibold tracking-[-0.02em] text-ink sm:text-3xl">
            Two real exams: the trained adapter answers {pct(e2.r2.rate)} to {pct(e1.r2.rate)} of the questions, the untrained model {pct(e1.base.rate)}.
          </h2>
          <p className="mt-4 max-w-[66ch] text-pretty text-[15px] leading-relaxed text-ink-2">
            A small Nemotron 3 Nano 4B adapter learned to write database queries from examples that Nemotron 3 Ultra wrote and that were checked by running every query:
            821 examples in round 1, 4,322 in round 2. We then tested both adapters on two exams of questions about databases they never trained on. A query
            counts only when running it returns exactly the same rows as the stored reference query. That is strict: a good query with an extra column counts as
            wrong, so even the teacher, answering the same prompts, gets {pct(e1.teacher.rate)} and {pct(e2.teacher.rate)}.
          </p>

          <div className="mt-8 grid gap-6 lg:grid-cols-2">
            <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
              <p className="text-[13px] font-semibold text-ink">Exam 1 · {EXAMS.exams[0].rows} questions · written by the teacher</p>
              <ScoreBars exam="exam1" className="mt-2" />
            </div>
            <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
              <p className="text-[13px] font-semibold text-ink">Exam 2 · {EXAMS.exams[1].rows} questions · written by a different model</p>
              <ScoreBars exam="exam2" className="mt-2" />
            </div>
          </div>

          <div className="mt-6 overflow-x-auto rounded-2xl border border-line bg-surface shadow-card">
            <table className="w-full min-w-[30rem] text-left text-[14px]">
              <caption className="sr-only">Strict scores on both exams</caption>
              <thead className="border-b border-line text-[12px] uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium sm:px-6">
                    Model
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Exam 1
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Exam 2
                  </th>
                </tr>
              </thead>
              <tbody>
                {MODEL_IDS.map((id) => {
                  const m = EXAMS.models.find((x) => x.id === id)!;
                  const bold = id === "r2";
                  return (
                    <tr key={id} className="border-b border-line last:border-0">
                      <th scope="row" className={`px-4 py-3 font-medium sm:px-6 ${bold ? "text-ink" : "text-ink-2"}`}>
                        {m.label}
                      </th>
                      <td className={`px-4 py-3 tabular-nums ${bold ? "font-semibold text-ink" : "text-ink-2"}`}>{scoreText(e1[id])}</td>
                      <td className={`px-4 py-3 tabular-nums ${bold ? "font-semibold text-ink" : "text-ink-2"}`}>{scoreText(e2[id])}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h3 className="mt-12 text-[18px] font-semibold tracking-[-0.01em] text-ink">Who wrote what</h3>
          <p className="mt-2 max-w-[66ch] text-[14.5px] leading-relaxed text-ink-2">
            Exam 1 was written by the same model that wrote the training examples, which flatters the teacher and anything trained to copy it. So exam 2 was written
            afterwards by a different model from a different lab, on industries that appear in neither. It was used to test, never to train.
          </p>
          <ExamAuthors className="mt-4" />

          <div className="mt-10 grid gap-6 lg:grid-cols-2">
            <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
              <p className="text-[13px] font-semibold text-ink">Where the adapter does well and badly (exam 2)</p>
              <DifficultyBars exam="exam2" className="mt-2" />
            </div>
            <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
              <p className="text-[13px] font-semibold text-ink">Round 1 against round 2, question by question</p>
              <FlipBars className="mt-2" />
            </div>
          </div>

          <ul className="mt-8 grid max-w-4xl gap-3 text-[14px] leading-relaxed text-ink-2 sm:grid-cols-2">
            <li className="rounded-xl border border-line p-4">
              <span className="font-medium text-ink">The effect repeats.</span> The untrained model scores about the same on both exams ({pct(e1.base.rate)} and{" "}
              {pct(e2.base.rate)}); the round 2 adapter is {timesBase(EXAMS, "exam1").toFixed(1)} and {timesBase(EXAMS, "exam2").toFixed(1)} times that.
              Round 2 beat round 1 by 10 questions on both exams, with p = {EXAMS.r1_to_r2.exam1.p} and {EXAMS.r1_to_r2.exam2.p}, so the direction is consistent but not proven.
            </li>
            <li className="rounded-xl border border-line p-4">
              <span className="font-medium text-ink">Not yet a replacement for the teacher.</span> At a 95 percent precision target the confidence gate lets almost
              nothing through, so it saves about 1 percent or less. Refusing to serve weak answers is the system working as designed. The teacher scores low here mostly because
              it adds extra columns the strict exam marks wrong ({EXAMS.teacher_shape.exam2.same_rows_other_columns} of its 200 answers on exam 2), so this does not show the adapter beats it.
            </li>
            <li className="rounded-xl border border-line p-4 sm:col-span-2">
              <span className="font-medium text-ink">Limits.</span> Trained on a free Colab T4 (round 1) and a rented Modal T4 (round 2), not on Nebius GPUs. The data is
              synthetic. Each question has one reference answer. The ranges (the lines on the bars) are wide: with 172 to 200 questions a score is good to about 7 points.
            </li>
          </ul>

          <p className="mt-6 text-[13px] text-muted">
            The simulation on this page uses mock models and shows how the loop behaves, not how good a model is.{" "}
            <Link to="/judges#exams" className={LINK}>
              The exams in detail
            </Link>{" "}
            ·{" "}
            <Link to="/judges#real" className={LINK}>
              What is real, and how to check it
            </Link>{" "}
            ·{" "}
            <a href="https://github.com/harshbhx/ratchet/tree/main/evidence/text2sql-exam2" className={LINK}>
              the answers and the check command
            </a>
          </p>
        </Reveal>
      </Container>
    </section>
  );
}
