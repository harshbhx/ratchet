import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  CircleX,
  FileCode2,
  MousePointerClick,
  Pause,
  Play,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Container, Eyebrow, H2, Lead } from "@/components/ui/section";
import { cn, REPO_URL } from "@/lib/utils";
import { EASE, Reveal } from "../shared";
import {
  LAYOUT,
  SCENARIOS,
  SCENARIO_BY_ID,
  STAGE_BY_ID,
  moveFocus,
  pathText,
  sourceUrl,
  stepFrom,
  stepOf,
  type Lane,
  type Scenario,
  type ScenarioId,
  type StageId,
  type Tone,
} from "../lifecycle-model";

/** How long "Walk the path" stays on each stage. */
const PLAY_MS = 2200;

const LANES: { lane: Lane; label: string; sub: string }[] = [
  { lane: "day", label: "By day", sub: "one request" },
  { lane: "night", label: "By night", sub: "one release" },
];

/**
 * Node colours on the path; off the path every node is the same dashed outline. Each tone comes with a word (the
 * stage's title), never colour alone. Pairs are chosen for 4.5:1: the step dots use the `-ink` shades in light mode,
 * and the small notes under a good or bad stage stay ink, since status greens and reds are too light on their soft fills.
 */
const TONE: Record<Tone, { on: string; dot: string; sub: string; text: string; edge: string }> = {
  neutral: { on: "border-ink-2/60 bg-surface", dot: "bg-ink text-bg", sub: "text-ink-2", text: "text-ink-2", edge: "border-l-ink-2" },
  owned: {
    on: "border-owned bg-owned-soft",
    dot: "bg-owned-ink text-white dark:bg-owned dark:text-[#06101f]",
    sub: "text-owned-ink",
    text: "text-owned-ink",
    edge: "border-l-owned",
  },
  rented: {
    on: "border-rented bg-rented-soft",
    dot: "bg-rented-ink text-white dark:bg-rented dark:text-[#1f0b03]",
    sub: "text-rented-ink",
    text: "text-rented-ink",
    edge: "border-l-rented",
  },
  good: { on: "border-good bg-good-soft", dot: "bg-[#17722e] text-white dark:bg-good dark:text-[#06200d]", sub: "text-ink-2", text: "text-[#17722e] dark:text-good", edge: "border-l-good" },
  bad: { on: "border-bad bg-bad-soft", dot: "bg-bad text-white dark:text-[#2a0606]", sub: "text-ink-2", text: "text-bad", edge: "border-l-bad" },
};

const OUTCOME_ICON: Record<Tone, LucideIcon> = {
  neutral: ArrowRight,
  owned: CircleCheck,
  rented: ArrowRight,
  good: CircleCheck,
  bad: CircleX,
};

function StageButton({
  id,
  scenario,
  selected,
  onSelect,
  onKey,
  register,
  fork = false,
}: {
  id: StageId;
  scenario: Scenario;
  selected: boolean;
  /** One branch of a fork: half width on phones, so the note under the title shows from lg only. */
  fork?: boolean;
  onSelect: (id: StageId) => void;
  onKey: (e: KeyboardEvent<HTMLButtonElement>, id: StageId) => void;
  register: (id: StageId, el: HTMLButtonElement | null) => void;
}) {
  const reduce = useReducedMotion();
  const s = STAGE_BY_ID[id];
  const step = stepOf(scenario, id);
  const on = step > 0;
  const tone = TONE[s.tone];
  return (
    <button
      ref={(el) => register(id, el)}
      type="button"
      // Roving focus: only the selected stage is in the tab order; arrow keys move between stages.
      tabIndex={selected ? 0 : -1}
      aria-pressed={selected}
      aria-label={`${s.title}, ${on ? `step ${step} of ${scenario.path.length}` : "not on this path"}`}
      onClick={() => onSelect(id)}
      onKeyDown={(e) => onKey(e, id)}
      className={cn(
        // Phones: one line per stage (title, then its note on the right). From lg: a card with the note under the title.
        "relative flex w-full min-w-0 items-center gap-2 rounded-xl border px-2.5 py-2 text-left transition-[background,border-color,box-shadow,opacity] duration-300 lg:flex-col lg:items-start lg:gap-1",
        on ? tone.on : "border-dashed border-line-strong bg-transparent hover:border-ink-2/50",
        selected && "shadow-card ring-2 ring-ink ring-offset-2 ring-offset-surface",
      )}
    >
      <span className="flex min-w-0 shrink-0 items-center gap-1.5 lg:w-full lg:shrink">
        {on ? (
          <motion.span
            key={`${scenario.id}-${step}`}
            initial={reduce ? false : { scale: 0.3, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.35, ease: EASE, delay: reduce ? 0 : (step - 1) * 0.06 }}
            className={cn("inline-flex size-[18px] shrink-0 items-center justify-center rounded-full font-mono text-[10.5px] font-semibold tabular", tone.dot)}
            aria-hidden
          >
            {step}
          </motion.span>
        ) : (
          <span aria-hidden className="inline-block size-[18px] shrink-0 rounded-full border border-dashed border-line-strong" />
        )}
        <span className={cn("min-w-0 truncate text-[13px] font-semibold leading-tight", on ? "text-ink" : "text-muted")}>{s.title}</span>
      </span>
      <span
        className={cn(
          "ml-auto min-w-0 truncate text-right font-mono text-[10.5px] leading-tight lg:ml-0 lg:block lg:w-full lg:pl-[24px] lg:text-left",
          fork ? "hidden" : "block",
          on ? tone.sub : "text-muted",
        )}
      >
        {s.sub}
      </span>
    </button>
  );
}

/** A connector between two columns, lit when the path steps from one to the next. Vertical below lg, horizontal above. */
function Connector({ lit }: { lit: boolean }) {
  return (
    <span aria-hidden className="flex h-2.5 shrink-0 pl-[19px] lg:h-auto lg:w-4 lg:items-start lg:pl-0">
      {/* From lg the line meets the first node of a column at its middle (a fork's column is two nodes tall). */}
      <span className={cn("block h-full w-px transition-colors duration-500 lg:mt-[24px] lg:h-px lg:w-full", lit ? "bg-ink-2" : "bg-line-strong")} />
    </span>
  );
}

export function Lifecycle() {
  const [scenarioId, setScenarioId] = useState<ScenarioId>("escalated");
  const scenario = SCENARIO_BY_ID[scenarioId];
  const [selected, setSelected] = useState<StageId>(scenario.path[0]);
  const [playing, setPlaying] = useState(false);
  const reduce = useReducedMotion();
  const buttons = useRef(new Map<StageId, HTMLButtonElement>());
  const groupName = useId();
  const detailId = useId();

  const register = (id: StageId, el: HTMLButtonElement | null) => {
    if (el) buttons.current.set(id, el);
    else buttons.current.delete(id);
  };

  const choose = (id: ScenarioId) => {
    setScenarioId(id);
    setSelected(SCENARIO_BY_ID[id].path[0]);
    setPlaying(false);
  };

  const select = (id: StageId) => {
    setSelected(id);
    setPlaying(false);
  };

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, id: StageId) => {
    const next = moveFocus(id, e.key);
    if (!next) return;
    e.preventDefault();
    select(next);
    buttons.current.get(next)?.focus();
  };

  const step = (delta: 1 | -1) => select(stepFrom(scenario, selected, delta));

  // "Walk the path" steps through the scenario once and stops at its end. It runs only when the reader starts it.
  useEffect(() => {
    if (!playing) return;
    const t = window.setTimeout(() => {
      const i = scenario.path.indexOf(selected);
      if (i >= scenario.path.length - 1) setPlaying(false);
      else setSelected(scenario.path[i + 1]);
    }, PLAY_MS);
    return () => window.clearTimeout(t);
  }, [playing, selected, scenario]);

  const play = () => {
    if (playing) return setPlaying(false);
    const i = scenario.path.indexOf(selected);
    if (i < 0 || i === scenario.path.length - 1) setSelected(scenario.path[0]);
    setPlaying(true);
  };

  const stage = STAGE_BY_ID[selected];
  const at = stepOf(scenario, selected);
  const Outcome = OUTCOME_ICON[scenario.outcome.tone];
  const note = scenario.notes[selected];

  return (
    <section id="lifecycle" aria-labelledby="lifecycle-title" className="relative scroll-mt-16 pb-16 sm:pb-20 lg:pb-24">
      <Container>
        <Reveal className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div className="max-w-3xl">
            <Eyebrow>Follow one request</Eyebrow>
            <H2 id="lifecycle-title" className="mt-5">
              Pick what happens. See what guards it.
            </H2>
            <Lead className="mt-5 max-w-[64ch]">
              Choose a scenario, then select any stage: what it does, what goes in and out, the rules that guard it, and the file that implements it.
            </Lead>
          </div>
          <div className="flex max-w-xs flex-col gap-2 lg:items-end lg:text-right">
            <Badge tone="outline" className="self-start lg:self-end">
              <MousePointerClick aria-hidden /> Illustration
            </Badge>
            <p className="text-[12.5px] leading-relaxed text-muted">Not a live call. The stages, rules and files are the real ones; the request is an example.</p>
          </div>
        </Reveal>

        <Reveal delay={0.05} className="mt-8 overflow-hidden rounded-[22px] border border-line bg-surface shadow-card">
          {/* ------------------------------------------------ scenario and map */}
          <div className="p-4 sm:p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between lg:gap-6">
              <fieldset className="min-w-0">
                <legend className="sr-only">What happens to the request</legend>
                <div className="grid grid-cols-2 gap-1 rounded-2xl bg-bg-sunken p-1 sm:inline-flex sm:rounded-full">
                  {SCENARIOS.map((s) => (
                    <label key={s.id} className="relative min-w-0 cursor-pointer">
                      <input
                        type="radio"
                        name={groupName}
                        value={s.id}
                        checked={scenarioId === s.id}
                        onChange={() => choose(s.id)}
                        className="peer sr-only"
                      />
                      <span
                        className={cn(
                          "block truncate rounded-xl px-3.5 py-2 text-center text-[13px] font-medium transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--focus)] sm:rounded-full",
                          scenarioId === s.id ? "bg-surface text-ink shadow-card" : "text-ink-2 hover:text-ink",
                        )}
                      >
                        {s.label}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <p className="text-pretty text-[14px] leading-snug text-ink-2 lg:max-w-[38ch] lg:text-right">{scenario.summary}</p>
            </div>

            <div role="group" aria-label="Stages. Arrow keys move between them." aria-describedby={detailId} className="mt-6 space-y-5">
              {LANES.map(({ lane, label, sub }) => (
                <div key={lane}>
                  <p className="mb-2 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
                    <span className="text-ink-2">{label}</span>
                    <span aria-hidden className="h-px w-5 bg-line-strong" />
                    <span>{sub}</span>
                  </p>
                  <div className="flex flex-col lg:flex-row lg:items-stretch">
                    {LAYOUT[lane].map((col, ci) => {
                      const ids = Array.isArray(col) ? col : [col];
                      const prev = ci > 0 ? LAYOUT[lane][ci - 1] : null;
                      const prevIds = prev ? (Array.isArray(prev) ? prev : [prev]) : [];
                      const lit = prevIds.some((a) => ids.some((b) => stepOf(scenario, a) > 0 && stepOf(scenario, b) === stepOf(scenario, a) + 1));
                      return (
                        <div key={ids.join("|")} className="contents">
                          {ci > 0 && <Connector lit={lit} />}
                          <div className={cn("min-w-0 lg:flex-1", ids.length > 1 && "grid grid-cols-2 gap-1.5 lg:flex lg:flex-col")}>
                            {ids.map((id) => (
                              <StageButton
                                key={id}
                                id={id}
                                scenario={scenario}
                                selected={selected === id}
                                onSelect={select}
                                onKey={onKey}
                                register={register}
                                fork={ids.length > 1}
                              />
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-6 flex flex-col gap-3 border-t border-line pt-4 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0">
                <p className={cn("flex items-start gap-2 text-[14.5px] font-medium leading-snug", TONE[scenario.outcome.tone].text)}>
                  <Outcome className="mt-[2px] size-4 shrink-0" aria-hidden />
                  <span className="min-w-0">
                    <span className="sr-only">Outcome: </span>
                    {scenario.outcome.text}
                  </span>
                </p>
                <p className="mt-1.5 pl-6 font-mono text-[11.5px] leading-relaxed text-muted [overflow-wrap:anywhere]">
                  <span className="sr-only">Path: </span>
                  {pathText(scenario)}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => step(-1)}
                  aria-label="Previous stage on this path"
                  className="inline-flex size-9 items-center justify-center rounded-full border border-line text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  <ChevronLeft className="size-4" aria-hidden />
                </button>
                <button
                  type="button"
                  onClick={play}
                  aria-pressed={playing}
                  className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line px-3.5 text-[13px] font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  {playing ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
                  {playing ? "Pause" : "Walk the path"}
                </button>
                <button
                  type="button"
                  onClick={() => step(1)}
                  aria-label="Next stage on this path"
                  className="inline-flex size-9 items-center justify-center rounded-full border border-line text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  <ChevronRight className="size-4" aria-hidden />
                </button>
              </div>
            </div>
          </div>

          {/* ------------------------------------------------ the selected stage */}
          <div id={detailId} className="border-t border-line bg-bg-sunken/60 p-4 sm:p-6">
            <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
              {stage.title}. {at > 0 ? `Step ${at} of ${scenario.path.length}.` : "Not on this path."} {note ?? ""}
            </p>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${scenarioId}-${selected}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: reduce ? 0 : 0.22, ease: EASE }}
                className="grid grid-cols-1 gap-x-10 gap-y-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]"
              >
                <div className="min-w-0">
                  <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
                    {stage.lane === "day" ? "By day" : "By night"} ·{" "}
                    {at > 0 ? (
                      <span className="text-ink-2">
                        step {at} of {scenario.path.length}
                      </span>
                    ) : (
                      "not on this path"
                    )}
                  </p>
                  <h3 className="mt-2 text-[22px] font-semibold tracking-[-0.02em] text-ink">{stage.title}</h3>
                  <p className="mt-2 max-w-[60ch] text-pretty text-[15px] leading-relaxed text-ink-2">{stage.does}</p>
                  <div className={cn("mt-4 rounded-xl border-l-[3px] bg-surface px-3.5 py-2.5", at > 0 ? TONE[stage.tone].edge : "border-l-line-strong")}>
                    <p className="text-[11px] font-medium uppercase tracking-[0.1em] text-muted">In this scenario</p>
                    <p className="mt-1 text-pretty text-[14.5px] leading-snug text-ink">
                      {note ?? `The "${scenario.label}" path does not go through this stage.`}
                    </p>
                  </div>
                  <dl className="mt-4 grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13.5px] leading-snug">
                    <dt className="font-medium text-muted">In</dt>
                    <dd className="text-ink-2">{stage.inputs}</dd>
                    <dt className="font-medium text-muted">Out</dt>
                    <dd className="text-ink-2">{stage.outputs}</dd>
                  </dl>
                </div>
                <div className="min-w-0">
                  <h4 className="text-[13px] font-semibold text-ink">Safeguards</h4>
                  <ul className="mt-2 space-y-1.5 text-[13.5px] leading-snug text-ink-2">
                    {stage.safeguards.map((g) => (
                      <li key={g} className="flex gap-2">
                        <ShieldCheck className="mt-[2px] size-3.5 shrink-0 text-good" aria-hidden />
                        <span className="min-w-0 text-pretty">{g}</span>
                      </li>
                    ))}
                  </ul>
                  <h4 className="mt-5 text-[13px] font-semibold text-ink">Where it is in the code</h4>
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {stage.files.map((f) => (
                      <li key={f} className="min-w-0 max-w-full">
                        <a
                          href={sourceUrl(REPO_URL, f)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-surface px-2 py-1 font-mono text-[11.5px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
                        >
                          <FileCode2 className="size-3.5 shrink-0 text-muted" aria-hidden />
                          <span className="min-w-0 truncate">{f}</span>
                          <span className="sr-only"> (opens on GitHub)</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              </motion.div>
            </AnimatePresence>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
