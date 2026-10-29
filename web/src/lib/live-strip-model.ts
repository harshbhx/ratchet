/**
 * What the "This gateway, right now" strip shows while this gateway's own ledger is empty (a restart empties it): never a bare zero or a
 * dash. The Worker's running totals (`/ratchet/stats/persistent`) when they hold traffic, else dated values read from the measured
 * snapshot (`src/data/measured.json`), each with its badge and the evidence folder it came from. Once the ledger has requests the strip
 * shows its live numbers and this module is not used. Pure, no JSX: `tests/live-strip-model.test.mjs` runs it.
 *
 * No savings figure is made up: "Saved vs all-Ultra" has no dated measured value, so the empty strip shows other measured facts under
 * their own labels.
 */
import { fmtDay, fmtInt, fmtRange, fmtUsd, hasLiveTraffic, liveLine, sourceHref, type MeasuredData, type Persistent } from "./measured-model";

export interface Provenance {
  tag: "Measured" | "Recorded run" | "Live";
  date: string;
  dateEnd?: string | null;
  /** Repository path of the evidence folder (an external link opens it); null for the Worker's own totals. */
  source: string | null;
}

export interface StripItem {
  id: string;
  label: string;
  value: string;
  sub: string;
  from: Provenance | null;
}

/** A value that reads as nothing: "0", "$0", "0%", "-" or the dash. */
export function isBare(v: string): boolean {
  return /^(\s*|[–—-]|\$?0([.,]0+)?%?|none)$/i.test(v.trim());
}

const nightSchool = (d: MeasuredData) => {
  const n = d.nightschool;
  if (!n.present || n.verdict !== "rejected" || !n.numbers?.precision || !n.numbers?.target) return null;
  return {
    sub: `Night School's ${fmtDay(n.date ?? "").replace(/ \d{4}$/, "")} candidate was rejected by the gate (${n.numbers.precision} vs ${n.numbers.target})`,
    from: { tag: "Recorded run", date: n.date ?? "", source: n.source } as Provenance,
  };
};

export interface EmptyOptions {
  measured: MeasuredData | null;
  persistent: Persistent | null;
  /** The live/canary/shadow words the strip already shows for a version in flight, or null when none is. */
  release: { live: string | null; flight: string | null };
}

/**
 * The four cards for an empty ledger. Cards whose evidence is missing are left out; `null` means there is nothing to show but the
 * ledger itself (no snapshot and no totals), and the strip says so in one plain sentence.
 */
export function emptyItems({ measured: d, persistent: p, release }: EmptyOptions): StripItem[] | null {
  const items: StripItem[] = [];
  const live = hasLiveTraffic(p);
  if (live) {
    const l = liveLine(p);
    items.push({
      id: "live-since",
      label: `Requests since ${fmtDay(p.since ?? "")}`,
      value: fmtInt(p.totals.requests),
      sub: `${l.items.find((i) => i.label === "Token Factory cost")?.value ?? fmtUsd(p.totals.cost_usd)} Token Factory, kept across restarts`,
      from: { tag: "Live", date: p.since ?? "", source: null },
    });
  } else if (d && d.scout.runs.length) {
    const costs = d.scout.runs.map((r) => r.cost_usd);
    const lo = Math.min(...costs);
    const hi = Math.max(...costs);
    items.push({
      id: "scout-cost",
      label: "Cost of a recorded Scout run",
      value: lo === hi ? fmtUsd(lo) : `${fmtUsd(lo)} to ${fmtUsd(hi)}`,
      sub: `${d.scout.runs.length} runs, the teacher answered every model call`,
      from: { tag: "Recorded run", date: d.scout.date, dateEnd: d.scout.date_end, source: d.scout.source },
    });
  }
  if (d) {
    const row = d.endpoint.bench.filter((b) => b.adapter === "sql-r2" && b.concurrency === 16)[0];
    if (row)
      items.push({
        id: "endpoint",
        label: "Student on a Nebius endpoint, 16 at once",
        value: `${row.lat_p50_s.toFixed(1)} s`,
        sub: "median answer, SQL adapter; a speed test, no accuracy scored",
        from: { tag: "Measured", date: d.endpoint.date, dateEnd: d.endpoint.date_end, source: d.endpoint.source },
      });
  }
  const ns = d ? nightSchool(d) : null;
  if (release.live || release.flight || ns || d) {
    items.push({
      id: "version",
      label: "Live version",
      value: release.live ?? "none yet",
      sub: release.flight ?? ns?.sub ?? "no release in flight",
      from: release.flight ? null : (ns?.from ?? null),
    });
  }
  if (d && d.smoke.requests > 0)
    items.push({
      id: "smoke",
      label: "Live smoke run",
      value: `${fmtInt(d.smoke.requests)} requests`,
      sub: `${fmtUsd(d.smoke.total_usd)} on Token Factory, the teacher answered every one`,
      from: { tag: "Measured", date: d.smoke.date, source: d.smoke.source },
    });
  return items.length > 1 ? items : null;
}

/** "Measured · 27 to 28 Oct 2026" / "Recorded run · 23 to 24 Oct 2026" / "Live since 29 Oct 2026". */
export function provenanceLabel(f: Provenance): string {
  return f.tag === "Live" ? `Live since ${fmtDay(f.date)}` : `${f.tag} · ${fmtRange(f.date, f.dateEnd)}`;
}

export const provenanceHref = (f: Provenance): string | null => (f.source ? sourceHref(f.source) : null);
