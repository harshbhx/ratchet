/**
 * Which numbers the dashboard's headline tiles show. Judges see all the numbers, never one process's numbers since its last restart:
 *   - `cumulative`: the Worker's totals kept across restarts (`/ratchet/stats/persistent`), labelled "Live since <date>";
 *   - `loading`: those totals are being read, so nothing is shown yet (no flash of this process's numbers);
 *   - `process`: the totals are unreachable and this process has a ledger: its tiles, labelled "this server instance since <time>";
 *   - `measured`: the totals are unreachable and this process has no traffic: the dated measured tiles.
 * Mock mode has no Worker and shows its own ledger. Pure, no JSX: `tests/dashboard-cumulative.test.mjs` runs it.
 */
import type { Stats } from "@/lib/api";
import type { Persistent } from "@/lib/measured-model";
import { cumulativeItems, type StripItem } from "@/lib/live-strip-model";

export type Headline =
  | { kind: "cumulative"; items: StripItem[] }
  | { kind: "loading" }
  | { kind: "process" }
  | { kind: "measured" };

export function headline(opts: { mock: boolean; persistent: Persistent | null | undefined; stats: Pick<Stats, "days"> }): Headline {
  if (opts.mock) return { kind: "process" };
  if (opts.persistent === undefined) return { kind: "loading" };
  const items = cumulativeItems(opts.persistent);
  if (items) return { kind: "cumulative", items };
  return opts.stats.days.length === 0 ? { kind: "measured" } : { kind: "process" };
}

/** "this server instance since 29 Oct 2026, 14:02 UTC" from the epoch seconds the upstream block records, else "since its last start". */
export function instanceLabel(sinceSeconds: number | null | undefined): string {
  if (!sinceSeconds || !Number.isFinite(sinceSeconds)) return "this server instance since its last start";
  const d = new Date(sinceSeconds * 1000);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `this server instance since ${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${hh}:${mm} UTC`;
}

/** The one sentence a daily chart shows when this process has nothing to plot: no empty axis, a pointer to the all-time tiles. */
export function emptyChartSentence(sinceSeconds: number | null | undefined): string {
  return `Nothing to plot for ${instanceLabel(sinceSeconds)}: the daily charts read this server's own ledger, which starts empty after every restart. The all-time totals are in the Overview tiles above.`;
}

/** The small caption above charts that do have rows. */
export function chartCaption(sinceSeconds: number | null | undefined): string {
  return `Daily charts: ${instanceLabel(sinceSeconds)}. The headline totals above are cumulative.`;
}
