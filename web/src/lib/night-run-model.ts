/**
 * The Night School run on Nebius (28 Oct 2026) as the story's card shows it: its four steps, the gate's precision against the target
 * and the rows. Every number is read from the measured snapshot (`src/data/measured.json`, written from `evidence/nightschool-nebius/`
 * by `scripts/measured_numbers.py`), so the evidence holds it; a number the snapshot lacks leaves the card out, never filled.
 * Pure, no JSX: `tests/night-run-model.test.mjs` runs it.
 */
import { fmtDay, type MeasuredData } from "./measured-model";

export interface NightStep {
  id: "gateway" | "training" | "serving" | "gate";
  label: string;
  title: string;
  detail: string;
  /** The gate stops the line: its mark is a cross, not a check. */
  stops?: boolean;
}

export interface NightRun {
  date: string;
  steps: NightStep[];
  /** 0 to 1. */
  precision: number;
  target: number;
  precisionText: string;
  targetText: string;
  right: string;
  answered: string;
  questions: string;
  coverage: string;
  rows: { imported: string; training: string; golden: string };
}

const frac = (pct: string | undefined): number | null => {
  const n = pct ? Number.parseFloat(pct) : Number.NaN;
  return Number.isFinite(n) ? n / 100 : null;
};

/** The card's data, or null while the run's evidence (or any number the card prints) is missing. */
export function nightRun(d: MeasuredData | null): NightRun | null {
  const n = d?.nightschool;
  if (!n || !n.present || n.verdict !== "rejected" || !n.numbers) return null;
  const x = n.numbers;
  const precision = frac(x.precision);
  const target = frac(x.target);
  const need = [x.precision, x.target, x.coverage, x.steps, x.loss_start, x.loss_end, x.gate_right, x.gate_answered, x.gate_questions, x.rows_imported, x.training_rows, x.golden_rows];
  if (precision === null || target === null || need.some((v) => !v) || !n.date) return null;
  return {
    date: fmtDay(n.date),
    steps: [
      { id: "gateway", label: "Gateway", title: "A Nebius CPU VM", detail: "Ran the gateway and the night" },
      { id: "training", label: "Training", title: "Spot L40S job", detail: `${x.steps} steps · loss ${x.loss_start} → ${x.loss_end}` },
      { id: "serving", label: "Serving", title: "Serverless Endpoint", detail: "Served the new adapter" },
      { id: "gate", label: "Golden gate", title: "Rejected", detail: "The student was never made live", stops: true },
    ],
    precision,
    target,
    precisionText: x.precision!,
    targetText: x.target!,
    right: x.gate_right!,
    answered: x.gate_answered!,
    questions: x.gate_questions!,
    coverage: x.coverage!,
    rows: { imported: x.rows_imported!, training: x.training_rows!, golden: x.golden_rows! },
  };
}

/** The meter's geometry as percentages of its track: the filled bar, the shaded gap to the target and the marker. */
export function meter(r: Pick<NightRun, "precision" | "target">): { fill: number; gapFrom: number; gapWidth: number; marker: number } {
  const clamp = (v: number) => Math.min(100, Math.max(0, v * 100));
  const fill = clamp(r.precision);
  const marker = clamp(r.target);
  return { fill, gapFrom: Math.min(fill, marker), gapWidth: Math.max(0, marker - fill), marker };
}
