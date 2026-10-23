import { DifficultyBars, EXAMS, ExamAuthors, FlipBars, ScoreBars } from "@/components/charts/exam-charts";
import { MODEL_IDS, pct, rangeText, scoreText, timesBase } from "@/lib/exams-model";
import { Chapter, Command } from "./ui";

/** The text-to-SQL exams: who wrote each, what every model scored, and what the numbers do and do not show. All figures come from exams.json. */
export function ExamsChapter() {
  const e1 = EXAMS.scores.exam1;
  const e2 = EXAMS.scores.exam2;
  return (
    <Chapter
      id="exams"
      status="measured"
      title="The exams: who wrote them and what the models scored"
      lead={
        <>
          <p>
            Two exams of text-to-SQL questions on databases the models never saw. A query counts only when running it returns exactly the same rows as the stored reference query,
            so a good query with an extra column counts as wrong. Each exam was fixed, with its fingerprint, before any adapter answered it.
          </p>
          <p className="text-[14px] text-muted">
            Exam 1 ({EXAMS.exams[0].rows} questions) was written by the teacher. Exam 2 ({EXAMS.exams[1].rows} questions) was written afterwards by a different model, for testing only.
          </p>
        </>
      }
    >
      <ExamAuthors />

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
          <p className="text-[13px] font-semibold text-ink">Exam 1: written by the teacher</p>
          <ScoreBars exam="exam1" className="mt-2" />
        </div>
        <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
          <p className="text-[13px] font-semibold text-ink">Exam 2: written by a different model</p>
          <ScoreBars exam="exam2" className="mt-2" />
        </div>
      </div>

      <div className="mt-6 overflow-x-auto rounded-2xl border border-line bg-surface shadow-card">
        <table className="w-full min-w-[34rem] text-left text-[13.5px]">
          <caption className="sr-only">Strict scores and 95% ranges on both exams</caption>
          <thead className="border-b border-line bg-bg-sunken/60 text-[12px] text-muted">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">
                Model
              </th>
              <th scope="col" className="px-4 py-2.5 font-medium">
                Exam 1 right
              </th>
              <th scope="col" className="px-4 py-2.5 font-medium">
                Exam 1 range
              </th>
              <th scope="col" className="px-4 py-2.5 font-medium">
                Exam 2 right
              </th>
              <th scope="col" className="px-4 py-2.5 font-medium">
                Exam 2 range
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {MODEL_IDS.map((id) => (
              <tr key={id}>
                <th scope="row" className={`px-4 py-2.5 font-medium ${id === "r2" ? "text-ink" : "text-ink-2"}`}>
                  {EXAMS.models.find((m) => m.id === id)!.label}
                </th>
                <td className="px-4 py-2.5 tabular-nums text-ink">{scoreText(e1[id])}</td>
                <td className="px-4 py-2.5 tabular-nums text-muted">{rangeText(e1[id])}</td>
                <td className="px-4 py-2.5 tabular-nums text-ink">{scoreText(e2[id])}</td>
                <td className="px-4 py-2.5 tabular-nums text-muted">{rangeText(e2[id])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
          <p className="text-[13px] font-semibold text-ink">By difficulty, exam 1</p>
          <DifficultyBars exam="exam1" className="mt-2" />
        </div>
        <div className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
          <p className="text-[13px] font-semibold text-ink">By difficulty, exam 2</p>
          <DifficultyBars exam="exam2" className="mt-2" />
        </div>
      </div>

      <div className="mt-6 min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
        <p className="text-[13px] font-semibold text-ink">Round 1 against round 2, question by question</p>
        <FlipBars className="mt-2" />
      </div>

      <h3 className="mt-10 text-[17px] font-semibold tracking-[-0.01em] text-ink">What the numbers show, and what they do not</h3>
      <ul className="mt-3 grid max-w-4xl gap-3 text-[14px] leading-relaxed text-ink-2 sm:grid-cols-2">
        <li className="rounded-xl border border-line p-4">
          <span className="font-medium text-ink">Training works, and it repeats on a new exam.</span> The untrained model scores {pct(e1.base.rate)} and {pct(e2.base.rate)}; the round 2 adapter is{" "}
          {timesBase(EXAMS, "exam1").toFixed(1)} and {timesBase(EXAMS, "exam2").toFixed(1)} times that, on two exams with different authors.
        </li>
        <li className="rounded-xl border border-line p-4">
          <span className="font-medium text-ink">More data helped, but not provably.</span> Round 2 beat round 1 by 10 questions on both exams (p = {EXAMS.r1_to_r2.exam1.p} and {EXAMS.r1_to_r2.exam2.p}). Most of the gain
          is on join questions; simple questions did not improve.
        </li>
        <li className="rounded-xl border border-line p-4">
          <span className="font-medium text-ink">Not a replacement for the teacher.</span> At a 95 percent precision target the confidence gate passes almost nothing. The exam is also strict and short: a score is
          good to about 7 points either way.
        </li>
        <li className="rounded-xl border border-line p-4">
          <span className="font-medium text-ink">The low teacher score is about shape.</span> On exam 2, {EXAMS.teacher_shape.exam2.same_rows_other_columns} of the teacher&apos;s 200 answers return the right number of
          rows with a different number of columns, which the strict exam marks wrong. We do not claim the adapter beats the teacher.
        </li>
      </ul>

      <p className="mt-6 max-w-[72ch] text-[13.5px] leading-relaxed text-muted">
        Trained on a free Colab T4 (round 1) and a rented Modal T4 (round 2), not on Nebius GPUs. The data is synthetic. No closed model&apos;s output is training data anywhere in this project; the exam 2 author
        was used for evaluation only.
      </p>
      <div className="mt-4 max-w-[72ch]">
        <Command
          cmd="python scripts/measure_adapter.py verify --raw evidence/text2sql-exam2/answers/raw_v2.jsonl --results evidence/text2sql-exam2/results/results_v2.json --golden evidence/text2sql-exam2/data/golden.jsonl --dbs evidence/text2sql-exam2/data/dbs.json"
          label="Recompute exam 2's scores for the round 2 adapter"
          wrap
        />
      </div>
    </Chapter>
  );
}
