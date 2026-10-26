// When the live dashboard points at the demo gateway: on a live gateway with no trained version (Night School does not run
// there), whatever traffic it has had.
export const PIPELINE_LINK = {
  title: "See the full pipeline in action",
  line: "Four days of traffic, a trained student, a canary and a rollback, on the demo gateway.",
} as const;

type Shape = { mode?: string; days?: readonly unknown[]; versions?: readonly unknown[]; totals?: { requests?: number } } | null | undefined;

export function showPipelineLink(stats: Shape): boolean {
  if (!stats || stats.mode !== "live") return false;
  return (stats.versions?.length ?? 0) === 0;
}
