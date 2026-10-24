import { TOOL_EXAMS, TOOL_MODELS, TOOL_MODEL_LABEL, blockPlace, parseToolcall, rangeText, scoreText } from "@/lib/toolcall-model";

// toolcall.json is written by scripts/toolcall_results.py once the exam scores exist. A glob import, unlike a plain one, builds when it is absent.
const FILES = import.meta.glob("../../data/toolcall.json", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

/** The tool-calling data, parsed and checked once; null until the file exists. */
export const TOOLCALL = parseToolcall(Object.values(FILES)[0]);

/**
 * Round 3's results, measured on real models. `where` says which slot of a page this call fills: the data's variant picks
 * the slot ("top" when tool calling leads, "bottom" when text-to-SQL stays first), so both slots can be on a page and only one renders.
 */
export function ToolCallBlock({ where }: { where: "top" | "bottom" }) {
  const d = TOOLCALL;
  if (!d || blockPlace(d) !== where) return null;
  return (
    <section id="toolcall" aria-labelledby="toolcall-title" className="mt-9 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
      <p className="text-[12.5px] font-medium text-muted">Measured on real models{d.date ? `, ${d.date}` : ""}. Not the simulation.</p>
      <h3 id="toolcall-title" className="mt-1 text-[17px] font-semibold tracking-[-0.01em] text-ink">
        Tool calling: a second adapter, scored strictly on two exams
      </h3>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[30rem] text-left text-[13.5px]">
          <caption className="sr-only">Strict tool-calling scores and 95% ranges on two exams</caption>
          <thead className="border-b border-line text-[12px] text-muted">
            <tr>
              <th scope="col" className="py-2 pr-3 font-medium">
                Model
              </th>
              {TOOL_EXAMS.map((e) => (
                <th key={e} scope="col" className="py-2 pr-3 font-medium">
                  {d.exams[e].label} ({d.exams[e].rows})
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {TOOL_MODELS.map((m) => (
              <tr key={m}>
                <th scope="row" className={`py-2 pr-3 font-medium ${m === "adapter" ? "text-ink" : "text-ink-2"}`}>
                  {TOOL_MODEL_LABEL[m]}
                </th>
                {TOOL_EXAMS.map((e) => (
                  <td key={e} className="py-2 pr-3 tabular-nums text-ink">
                    {scoreText(d.exams[e].scores[m])} <span className="text-muted">({rangeText(d.exams[e].scores[m])})</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-3 grid gap-2 text-[14px] leading-relaxed text-ink-2">
        {TOOL_EXAMS.map((e) => (
          <li key={e}>{d.exams[e].sentence}</li>
        ))}
      </ul>
      <p className="mt-3 max-w-[78ch] text-[13px] leading-relaxed text-muted">
        {d.caveat} {d.setting}
      </p>
    </section>
  );
}
