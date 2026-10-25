/**
 * The live training panel's pure logic: turn the status a training job reports into what the panel shows, or into
 * nothing. The panel stays hidden unless a run is under way or ended recently, so a visitor never sees an empty widget.
 * The status service is a small read-only app in the training job's folder; it holds no secrets.
 */

export const STATUS_URL = "https://creharsh--ratchet-status-status.us-east.modal.run";
export const PHONE_URL = "https://ratchet-progress.pages.dev";
/** A finished run stays on screen for a day. */
const KEEP_DONE_S = 24 * 3600;

export interface RunStatus {
  state?: string;
  phase?: string;
  title?: string;
  note?: string;
  step?: number;
  total?: number;
  exam_row?: number;
  exam_total?: number;
  stage?: string;
  started_at?: number;
  updated_at?: number;
  now?: number;
  spent_usd?: number;
  spent_s?: number;
  cap_usd?: number;
  last_loss?: number;
  loss_hist?: [number, number][];
  error?: string;
}

export interface RunView {
  title: string;
  state: string;
  phase: string;
  percent: number;
  progress: string;
  elapsedS: number | null;
  etaS: number | null;
  spentUsd: number;
  capUsd: number | null;
  loss: number | null;
  lossHistory: [number, number][];
  staleS: number | null;
  error: string;
}

const PHASE: Record<string, string> = { starting: "Starting up", training: "Training", exam: "Answering the exam", done: "Finished" };
/** List price per hour for the training job's GPU, 2 cores and 16 GiB (the job's own constant). */
const RATE_PER_H = 0.59 + 2 * 0.0473 + 16 * 0.008;

/** What to show for a status, or null when there is nothing worth showing. */
export function runView(s: RunStatus | null | undefined): RunView | null {
  if (!s || typeof s !== "object") return null;
  const state = s.state ?? "unknown";
  if (state === "waiting" || state === "unknown" || !s.started_at) return null;
  const now = s.now ?? Date.now() / 1000;
  const updated = s.updated_at ?? s.started_at;
  if (state !== "running" && now - updated > KEEP_DONE_S) return null;

  let percent = 0;
  let progress = s.stage ?? "";
  if (s.phase === "exam" && s.exam_total) {
    percent = (100 * (s.exam_row ?? 0)) / s.exam_total;
    progress = `exam row ${s.exam_row ?? 0} of ${s.exam_total}`;
  } else if (s.total) {
    percent = (100 * (s.step ?? 0)) / s.total;
    progress = `step ${s.step ?? 0} of ${s.total}`;
  }
  if (s.phase === "done") percent = 100;
  const elapsed = now - s.started_at;
  const eta = s.phase === "training" && s.total && (s.step ?? 0) > 5 ? (elapsed * (s.total - (s.step ?? 0))) / (s.step ?? 1) : null;
  const age = now - updated;
  return {
    title: s.title ?? "A training run",
    state,
    phase: PHASE[s.phase ?? ""] ?? s.phase ?? "Waiting",
    percent: Math.max(0, Math.min(100, percent)),
    progress,
    elapsedS: elapsed,
    etaS: eta,
    spentUsd: s.spent_usd ?? ((s.spent_s ?? 0) * RATE_PER_H) / 3600,
    capUsd: s.cap_usd ?? null,
    loss: s.last_loss ?? null,
    lossHistory: Array.isArray(s.loss_hist) ? s.loss_hist : [],
    staleS: state === "running" && age > 300 ? age : null,
    error: s.error ?? "",
  };
}

/** `2 h 5 min`, `40 min`, `0 min`. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

/** An SVG path for the loss curve inside a 300 x 84 box, or "" when there are fewer than two points. */
export function lossPath(hist: readonly [number, number][]): string {
  if (hist.length < 2) return "";
  const xs = hist.map((p) => p[0]);
  const ys = hist.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const px = (x: number) => 6 + (x1 === x0 ? 0 : (x - x0) / (x1 - x0)) * 288;
  const py = (y: number) => 84 - (y1 === y0 ? 0.5 : (y - y0) / (y1 - y0)) * 78;
  return hist.map((p, i) => `${i ? "L" : "M"}${px(p[0]).toFixed(1)} ${py(p[1]).toFixed(1)}`).join(" ");
}
