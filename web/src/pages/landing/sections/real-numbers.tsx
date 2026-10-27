import type { ReactNode } from "react";
import { Radio } from "lucide-react";
import type { Stats } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { RealBadge } from "@/components/ui/real-badge";
import costsText from "@/data/real-costs.json?raw";
import { callRatio, demoSavings, labellingTotal, parseCosts } from "@/lib/real-story-model";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/utils";
import { fmtSmallUsd } from "../bill-model";

/** The labelling costs, the list prices per call and the precision-bar fact, parsed once; `tests/test_story_real_numbers.py` holds the file to `evidence/`. */
export const COSTS = parseCosts(costsText);

export function LiveBadge({ children = "Live" }: { children?: ReactNode }) {
  return (
    <Badge tone="good">
      <Radio aria-hidden /> {children}
    </Badge>
  );
}

function Tile({ value, label, note, badge }: { value: ReactNode; label: string; note?: ReactNode; badge: ReactNode }) {
  return (
    <li className="flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
      <div className="flex">{badge}</div>
      <p className="mt-3 text-[26px] font-semibold leading-none tracking-[-0.03em] text-ink tabular sm:text-[30px]">{value}</p>
      <p className="mt-2 text-[13px] leading-snug text-ink-2">{label}</p>
      {note ? <p className="mt-1.5 text-[12px] leading-relaxed text-muted">{note}</p> : null}
    </li>
  );
}

/**
 * What the labelling cost, and what a call costs, in the dollars of the real runs (Measured), beside the live gateway's own
 * savings counter (Live, from `/ratchet/stats` `demo_split`). With no stats, the live tile says so and shows no number.
 */
export function RealCostTiles({ stats, className }: { stats: Stats | null; className?: string }) {
  const c = COSTS;
  const live = demoSavings(stats);
  return (
    <ul className={`grid grid-cols-1 gap-3 min-[560px]:grid-cols-2 lg:grid-cols-4 ${className ?? ""}`}>
      <Tile
        badge={<RealBadge status="measured" />}
        value={fmtUsd(c.labelling.round1.usd, 2)}
        label="Ultra's labels for round 1"
        note={`${c.labelling.round1.calls} teacher calls, ${c.labelling.round1.rows} training rows.`}
      />
      <Tile
        badge={<RealBadge status="measured" />}
        value={fmtUsd(c.labelling.round2.usd, 2)}
        label="Ultra's labels for round 2"
        note={`${c.labelling.round2.calls} teacher calls, ${fmtInt(c.labelling.round2.rows_total)} rows with round 1's. Both rounds: ${fmtUsd(labellingTotal(c), 2)}.`}
      />
      <Tile
        badge={<RealBadge status="measured" />}
        value={
          <span className="text-[19px] leading-tight sm:text-[21px]">
            {fmtSmallUsd(c.per_call.ultra_triage)}
            <span className="text-[15px] font-medium text-muted"> vs </span>
            {fmtSmallUsd(c.per_call.nano_triage)}
          </span>
        }
        label="one Scout triage call: Ultra vs the hosted Nano"
        note={`List prices. The Nano is about ${callRatio(c, "triage")} times cheaper per triage call; a reader call is ${fmtSmallUsd(c.per_call.ultra_reader)} vs ${fmtSmallUsd(c.per_call.nano_reader)}.`}
      />
      {live ? (
        <Tile
          badge={<LiveBadge />}
          value={fmtUsd(live.savedUsd, live.savedUsd < 1 ? 4 : 2)}
          label={`saved by the random split${live.savingsPct != null ? `, ${fmtPct(live.savingsPct)} below all-Ultra` : ""}`}
          note={`${fmtInt(live.requests)} requests answered by the hosted Nano, picked at random: ${fmtUsd(live.costUsd, 4)} paid against ${fmtUsd(live.baselineUsd, 4)} all-Ultra.`}
        />
      ) : (
        <Tile
          badge={<LiveBadge>Live</LiveBadge>}
          value="–"
          label="saved by the random split"
          note="Not available right now: the gateway's stats did not load, or the split has served nothing yet. No number is shown."
        />
      )}
    </ul>
  );
}

/** One line: at the 95% bar the SQL adapter answers 1 of 86 measured questions. Measured. */
export function PrecisionFact({ className }: { className?: string }) {
  const p = COSTS.precision_bar;
  return (
    <p className={`max-w-[78ch] rounded-xl border border-line bg-surface-2 px-4 py-3 text-[13.5px] leading-relaxed text-ink-2 ${className ?? ""}`}>
      <span className="font-medium text-ink">The honest limit.</span> At a {p.target_pct}% precision bar the SQL adapter answers {p.answered} of {p.of} measured
      questions (exam 1, round 2) and the gate sends the rest to the teacher. The adapter does not replace Ultra yet.
    </p>
  );
}
