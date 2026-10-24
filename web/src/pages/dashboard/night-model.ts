/**
 * What the dashboard says after a simulated day whose night did not train. The gateway's own "skip" event holds the numbers
 * ("only 12 new examples (< 40); no training tonight"), so the sentence quotes them instead of repeating "not enough new data".
 * Pure, so a test can check the wording.
 */

export interface SkipInfo {
  /** New training examples that arrived since the last training. */
  fresh: number;
  /** How many a night needs before it trains. */
  need: number;
}

/** The numbers in the day's "skip" event, or null when the night skipped for another reason. */
export function skipInfo(added: readonly { kind: string; detail: string }[]): SkipInfo | null {
  for (let i = added.length - 1; i >= 0; i--) {
    const e = added[i];
    if (e.kind !== "skip") continue;
    const m = /only (\d+) new examples \(< (\d+)\)/.exec(e.detail);
    if (m) return { fresh: Number(m[1]), need: Number(m[2]) };
  }
  return null;
}

const plural = (n: number) => (n === 1 ? "example" : "examples");

/**
 * The sentence for a night that skipped for lack of new data. `share` is the day's student share (0 to 1) or null;
 * `streak` counts the skips in a row including this one: the first explains, later ones only restate the numbers.
 */
export function skipSentence(info: SkipInfo | null, share: number | null, streak: number): string {
  if (!info) return "Skipped training: not enough new data.";
  if (streak >= 2) return `Still no training: ${info.fresh} of ${info.need} new ${plural(info.need)}.`;
  const why = share != null && share >= 0.8 ? ` The student answered ${Math.round(share * 100)}% of today's requests itself, so there is little new for it to learn.` : "";
  return `No training tonight: ${info.fresh} new ${plural(info.fresh)}, ${info.need} needed.${why} Run a different workload to give it something new.`;
}
