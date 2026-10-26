import examsText from "@/data/exams.json?raw";
import {
  DIFFICULTIES,
  MODEL_IDS,
  MODEL_STYLE,
  parseExams,
  pct,
  rangeText,
  scoreText,
  xOf,
  type ExamId,
  type ExamInfo,
  type ModelId,
} from "@/lib/exams-model";
import { cn } from "@/lib/utils";

/** The exams' data, parsed and checked once. */
export const EXAMS = parseExams(examsText);

const LABEL_W = 124;
const BAR_X0 = LABEL_W + 6;
const BAR_W = 168;

function Axis({ ticks, max, top, bottom }: { ticks: number[]; max: number; top: number; bottom: number }) {
  return (
    <g aria-hidden>
      {ticks.map((t) => {
        const x = BAR_X0 + xOf(t, max, BAR_W);
        return (
          <g key={t}>
            <line x1={x} x2={x} y1={top} y2={bottom} stroke="var(--grid)" strokeWidth={1} />
            <text x={x} y={bottom + 13} textAnchor="middle" fontSize={10} fill="var(--muted)">
              {Math.round(t * 100)}%
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** How many of one exam's questions each model got right, with the 95% range of each score as a whisker. */
export function ScoreBars({ exam, className }: { exam: ExamId; className?: string }) {
  const info = EXAMS.exams.find((e) => e.id === exam)!;
  const max = 0.6;
  const rowH = 40;
  const top = 6;
  const height = top + MODEL_IDS.length * rowH + 22;
  const labels = Object.fromEntries(EXAMS.models.map((m) => [m.id, m.short])) as Record<ModelId, string>;
  return (
    <figure className={cn("min-w-0", className)}>
      <svg viewBox={`0 0 ${BAR_X0 + BAR_W + 62} ${height}`} role="img" className="h-auto w-full" aria-label={`${info.name}: share of ${info.rows} questions answered exactly right, by model`}>
        <Axis ticks={[0, 0.2, 0.4, 0.6]} max={max} top={top} bottom={top + MODEL_IDS.length * rowH} />
        {MODEL_IDS.map((id, i) => {
          const s = EXAMS.scores[exam][id];
          const y = top + i * rowH;
          const st = MODEL_STYLE[id];
          return (
            <g key={id}>
              <text x={LABEL_W} y={y + 21} textAnchor="end" fontSize={12.5} fontWeight={id === "r2" ? 700 : 500} fill="var(--ink)">
                {labels[id]}
              </text>
              <rect x={BAR_X0} y={y + 8} width={Math.max(2, xOf(s.rate, max, BAR_W))} height={20} rx={4} fill={st.color} opacity={st.opacity} />
              <line x1={BAR_X0 + xOf(s.lo, max, BAR_W)} x2={BAR_X0 + xOf(s.hi, max, BAR_W)} y1={y + 18} y2={y + 18} stroke="var(--ink)" strokeWidth={1.4} opacity={0.75} />
              <line x1={BAR_X0 + xOf(s.lo, max, BAR_W)} x2={BAR_X0 + xOf(s.lo, max, BAR_W)} y1={y + 13} y2={y + 23} stroke="var(--ink)" strokeWidth={1.4} opacity={0.75} />
              <line x1={BAR_X0 + xOf(s.hi, max, BAR_W)} x2={BAR_X0 + xOf(s.hi, max, BAR_W)} y1={y + 13} y2={y + 23} stroke="var(--ink)" strokeWidth={1.4} opacity={0.75} />
              <text x={BAR_X0 + xOf(s.hi, max, BAR_W) + 6} y={y + 22} fontSize={12.5} fontWeight={700} fill="var(--ink)" style={{ fontVariantNumeric: "tabular-nums" }}>
                {pct(s.rate)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-[12.5px] leading-snug text-muted">
        {info.name}: {info.rows} questions, written by {info.writer.split(",")[0]}. The line on each bar is the 95% range.
      </figcaption>
      {/* A table grows to its content whatever its width, so the 1px sr-only box wraps it instead. */}
      <div className="sr-only">
        <table>
          <caption>
            {info.name}, strict scores
          </caption>
          <tbody>
            {MODEL_IDS.map((id) => (
              <tr key={id}>
                <th scope="row">{labels[id]}</th>
                <td>{scoreText(EXAMS.scores[exam][id])}</td>
                <td>{rangeText(EXAMS.scores[exam][id])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

/** Per difficulty: the share of that kind of question each of the base model and the two adapters got right. */
export function DifficultyBars({ exam, className }: { exam: ExamId; className?: string }) {
  const info = EXAMS.exams.find((e) => e.id === exam)!;
  const ids: ModelId[] = ["base", "r1", "r2"];
  const short: Record<ModelId, string> = { base: "base", teacher: "teacher", r1: "round 1", r2: "round 2" };
  const max = 0.8;
  const group = 74;
  const barH = 16;
  const top = 6;
  const height = top + DIFFICULTIES.length * group + 20;
  return (
    <figure className={cn("min-w-0", className)}>
      <svg viewBox={`0 0 ${BAR_X0 + BAR_W + 62} ${height}`} role="img" className="h-auto w-full" aria-label={`${info.name}: share right by question difficulty, base model and the two adapters`}>
        <Axis ticks={[0, 0.2, 0.4, 0.6, 0.8]} max={max} top={top} bottom={top + DIFFICULTIES.length * group - 8} />
        {DIFFICULTIES.map((d, gi) => {
          const row = EXAMS.difficulty[exam][d];
          const y0 = top + gi * group;
          return (
            <g key={d}>
              <text x={LABEL_W} y={y0 + 28} textAnchor="end" fontSize={12.5} fontWeight={600} fill="var(--ink)">
                {d}
              </text>
              <text x={LABEL_W} y={y0 + 44} textAnchor="end" fontSize={10.5} fill="var(--muted)">
                {row.n} questions
              </text>
              {ids.map((id, bi) => {
                const rate = row[id] / row.n;
                const y = y0 + 8 + bi * (barH + 4);
                const st = MODEL_STYLE[id];
                return (
                  <g key={id}>
                    <rect x={BAR_X0} y={y} width={Math.max(2, xOf(rate, max, BAR_W))} height={barH} rx={3} fill={st.color} opacity={st.opacity} />
                    <text x={BAR_X0 + xOf(rate, max, BAR_W) + 5} y={y + 12} fontSize={10.5} fill="var(--ink)" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {short[id]} {row[id]}/{row.n}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-[12.5px] leading-snug text-muted">
        {info.name}, by difficulty. Join questions improved most from round 1 to round 2; simple questions did not.
      </figcaption>
    </figure>
  );
}

/** Round 1 against round 2, question by question, on both exams. */
export function FlipBars({ className }: { className?: string }) {
  const rows: Array<{ exam: ExamId; name: string }> = [
    { exam: "exam1", name: "Exam 1" },
    { exam: "exam2", name: "Exam 2" },
  ];
  const max = 26;
  const mid = LABEL_W + 6 + 84;
  const half = 84;
  const rowH = 44;
  return (
    <figure className={cn("min-w-0", className)}>
      <svg viewBox={`0 0 ${mid + half + 70} ${rows.length * rowH + 28}`} role="img" className="h-auto w-full" aria-label="Questions that changed between the round 1 and the round 2 adapter, on both exams">
        <line x1={mid} x2={mid} y1={2} y2={rows.length * rowH + 2} stroke="var(--line-strong)" />
        {rows.map((r, i) => {
          const f = EXAMS.r1_to_r2[r.exam];
          const y = 6 + i * rowH;
          return (
            <g key={r.exam}>
              <text x={LABEL_W} y={y + 24} textAnchor="end" fontSize={12.5} fontWeight={600} fill="var(--ink)">
                {r.name}
              </text>
              <rect x={mid - xOf(f.right_to_wrong, max, half)} y={y + 8} width={xOf(f.right_to_wrong, max, half)} height={20} rx={3} fill="var(--bad)" opacity={0.85} />
              <text x={mid - xOf(f.right_to_wrong, max, half) - 5} y={y + 23} textAnchor="end" fontSize={12} fontWeight={700} fill="var(--ink)">
                −{f.right_to_wrong}
              </text>
              <rect x={mid} y={y + 8} width={xOf(f.wrong_to_right, max, half)} height={20} rx={3} fill="var(--good)" opacity={0.9} />
              <text x={mid + xOf(f.wrong_to_right, max, half) + 5} y={y + 23} fontSize={12} fontWeight={700} fill="var(--ink)">
                +{f.wrong_to_right}
              </text>
            </g>
          );
        })}
        <text x={mid - 6} y={rows.length * rowH + 20} textAnchor="end" fontSize={10.5} fill="var(--muted)">
          right in round 1 only
        </text>
        <text x={mid + 6} y={rows.length * rowH + 20} fontSize={10.5} fill="var(--muted)">
          right in round 2 only
        </text>
      </svg>
      <figcaption className="mt-1 text-[12.5px] leading-snug text-muted">
        Questions whose answer changed between the two adapters. Round 2 wins more of them on both exams (p = {EXAMS.r1_to_r2.exam1.p} and {EXAMS.r1_to_r2.exam2.p}), so
        the direction repeats, but neither exam alone settles it.
      </figcaption>
    </figure>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] font-medium text-muted">{label}</dt>
      <dd className="mt-0.5 text-[13.5px] leading-snug text-ink-2">{children}</dd>
    </div>
  );
}

/** Who wrote what: the training labels and each exam, how it was checked and its fingerprint. */
export function ExamAuthors({ className }: { className?: string }) {
  const training = {
    name: "Training examples",
    writer: "Nemotron 3 Ultra, the teacher (open model, OpenMDW-1.1)",
    what: "821 examples in round 1 and 3,501 more in round 2 (3,507 written, 6 dropped for overlapping the exam), written on Nebius Token Factory. Every query was run before it was kept.",
    never: "Never used to score anything, and never overlapping with either exam (0 of 172 and 0 of 200 questions, queries or table sets).",
  };
  return (
    <div className={cn("grid gap-3 md:grid-cols-3", className)}>
      <article className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
        <p className="font-mono text-[12px] text-muted">Teaches the adapter</p>
        <h3 className="mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink">{training.name}</h3>
        <dl className="mt-3 space-y-2.5">
          <Fact label="Written by">{training.writer}</Fact>
          <Fact label="What">{training.what}</Fact>
          <Fact label="Kept apart from the exams">{training.never}</Fact>
        </dl>
      </article>
      {EXAMS.exams.map((e: ExamInfo) => (
        <article key={e.id} className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
          <p className="font-mono text-[12px] text-muted">Tests the adapter · {e.rows} questions · {e.built}</p>
          <h3 className="mt-1 text-[16px] font-semibold tracking-[-0.01em] text-ink">{e.name}</h3>
          <dl className="mt-3 space-y-2.5">
            <Fact label="Written by">{e.writer}</Fact>
            <Fact label="Why that matters">{e.writer_note}</Fact>
            <Fact label="Industries">{e.industries}</Fact>
            <Fact label="How it was checked">{e.checked}</Fact>
            <Fact label="Fingerprint (SHA-256)">
              <span className="font-mono text-[11.5px] [overflow-wrap:anywhere]">{e.fingerprint}</span>
            </Fact>
          </dl>
        </article>
      ))}
    </div>
  );
}
