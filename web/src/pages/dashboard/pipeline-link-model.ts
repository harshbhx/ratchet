// When the live dashboard points at the demo gateway: only on a live gateway with no days, requests or versions yet.
export const PIPELINE_LINK = {
  title: "See the full pipeline in action",
  line: "Four days of traffic, a trained student, a canary and a rollback, on the demo gateway.",
} as const;

type Shape = { mode?: string; days?: readonly unknown[]; versions?: readonly unknown[]; totals?: { requests?: number } } | null | undefined;

export function showPipelineLink(stats: Shape): boolean {
  if (!stats || stats.mode !== "live") return false;
  return (stats.days?.length ?? 0) === 0 && (stats.versions?.length ?? 0) === 0 && (stats.totals?.requests ?? 0) === 0;
}
