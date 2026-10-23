import { Link } from "react-router";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { Reveal } from "../shared";

/**
 * The first run on real models, between the story and the honest bill: what a trained adapter did on a held-out exam.
 * The numbers are the exam entry of `src/data/real.json` (also on the judges' page), kept apart from the simulation's.
 */
const ROWS: Array<{ who: string; right: string; rate: string; range: string; bold?: boolean }> = [
  { who: "Base model, no training", right: "22 of 172", rate: "12.8%", range: "8.6 to 18.6" },
  { who: "Teacher, same prompts", right: "44 of 172", rate: "25.6%", range: "19.6 to 32.6" },
  { who: "Our adapter, round 1 (821 rows)", right: "57 of 172", rate: "33.1%", range: "26.5 to 40.5" },
  { who: "Our adapter, round 2 (4,322 rows)", right: "67 of 172", rate: "39.0%", range: "32.0 to 46.4", bold: true },
];

const LINK = "font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink";

export function RealRun() {
  return (
    <section id="real-run" aria-labelledby="real-run-title" className="relative scroll-mt-16 border-t border-line py-14 sm:py-16">
      <Container>
        <Reveal>
          <div className="flex flex-wrap items-center gap-2">
            <RealBadge status="measured" />
            <span className="text-[12.5px] text-muted">22 to 23 Oct 2026 · two real runs, not the simulation</span>
          </div>
          <h2 id="real-run-title" className="mt-4 max-w-3xl text-balance text-2xl font-semibold tracking-[-0.02em] text-ink sm:text-3xl">
            Two real runs: the adapter answers 39% of the exam, the base model 13%.
          </h2>
          <p className="mt-4 max-w-[64ch] text-pretty text-[15px] leading-relaxed text-ink-2">
            A small Nemotron 3 Nano 4B adapter, trained on text-to-SQL examples that Nemotron 3 Ultra wrote and that were checked by
            running every query, answered 172 held-out questions. Round 1 used 821 examples and round 2 used 4,322. An answer counts only when running it returns the same rows as the stored
            reference query. That is strict: even the teacher, answering the same prompts, gets a quarter right.
          </p>
          <div className="mt-8 overflow-x-auto rounded-2xl border border-line bg-surface shadow-card">
            <table className="w-full min-w-[30rem] text-left text-[14px]">
              <caption className="sr-only">Exam results on 172 held-out text-to-SQL questions</caption>
              <thead className="border-b border-line text-[12px] uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium sm:px-6">
                    Model
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Matches the reference
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Rate
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    95% range
                  </th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.who} className="border-b border-line last:border-0">
                    <th scope="row" className={`px-4 py-3 font-medium sm:px-6 ${r.bold ? "text-ink" : "text-ink-2"}`}>
                      {r.who}
                    </th>
                    <td className="px-4 py-3 tabular-nums text-ink-2">{r.right}</td>
                    <td className={`px-4 py-3 tabular-nums ${r.bold ? "font-semibold text-ink" : "text-ink-2"}`}>{r.rate}</td>
                    <td className="px-4 py-3 tabular-nums text-muted">{r.range}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="mt-6 grid max-w-4xl gap-3 text-[14px] leading-relaxed text-ink-2 sm:grid-cols-2">
            <li className="rounded-xl border border-line p-4">
              <span className="font-medium text-ink">Not yet a replacement for the teacher.</span> At a 95 percent precision target the
              confidence gate let 1 of 86 answers through in round 2 (4 in round 1), so it saved about 1 percent. The gate refusing to
              serve weak answers is the system working as designed.
            </li>
            <li className="rounded-xl border border-line p-4">
              <span className="font-medium text-ink">Limits.</span> Trained on a free Colab T4 (round 1) and a rented Modal T4 (round 2), not on Nebius GPUs. The data is synthetic.
              One reference answer per question. The round 2 gain is on familiar industries and could partly be chance (p = 0.12), and the
              adapter and the teacher ranges nearly overlap, so this does not show it beats the teacher.
            </li>
          </ul>
          <p className="mt-6 text-[13px] text-muted">
            The simulation on this page uses mock models and shows how the loop behaves, not how good a model is.{" "}
            <Link to="/judges" className={LINK}>
              What is real, and how to check it
            </Link>{" "}
            ·{" "}
            <a href="https://github.com/harshbhx/ratchet/tree/main/evidence/text2sql-round2" className={LINK}>
              the answers and the check command
            </a>
          </p>
        </Reveal>
      </Container>
    </section>
  );
}
