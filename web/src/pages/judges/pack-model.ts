/**
 * The judges pack's data (`src/data/pack.json`): the pitch, the five-minute path, the proof cards, the links hub, the hackathon
 * checklist, what is real and what is not yet, and what the runs cost. Pure, no JSX: `tests/pack-model.test.mjs` runs it and
 * `tests/test_judges_pack.py` holds the text to the evidence folders and to `measured.json`. Every number in the file is one a judge
 * can recompute from the repository; the same facts are on JUDGES.md and the docs' "For judges" guide.
 */

export type PackStatus = "real" | "measured";
export type PackIcon = "radio" | "book" | "github" | "shield" | "hf" | "youtube";

export interface PackLink {
  label: string;
  href: string;
  note?: string;
}

export interface PathStep {
  id: string;
  title: string;
  body: string;
  command?: string;
  cta: { label: string; href: string };
}

export interface ProofCard {
  id: string;
  title: string;
  status: PackStatus;
  number: string;
  note: string;
  date: string;
  evidence: PackLink[];
  command?: string;
}

export interface LinkGroup {
  id: string;
  title: string;
  icon: PackIcon;
  items: PackLink[];
}

export interface Pack {
  updated: string;
  headline: string;
  pitch: string;
  path: PathStep[];
  proof: ProofCard[];
  groups: LinkGroup[];
  where: { where: string; what: string }[];
  checklist: { id: string; item: string; detail: string; links: PackLink[] }[];
  trackNote: string;
  ran: string[];
  notYet: string[];
  costs: { item: string; value: string; note: string }[];
}

/** The pack's JSON text as data, or null when it is not a pack (the page then shows nothing rather than half a pack). */
export function parsePack(text: string): Pack | null {
  try {
    const d = JSON.parse(text) as Partial<Pack>;
    const lists = ["path", "proof", "groups", "where", "checklist", "ran", "notYet", "costs"] as const;
    if (typeof d.headline !== "string" || typeof d.pitch !== "string") return null;
    for (const k of lists) if (!Array.isArray(d[k]) || d[k].length === 0) return null;
    return d as Pack;
  } catch {
    return null;
  }
}

export type HrefKind = "page" | "anchor" | "external";

/** How a pack link is opened: a page of this site (client-side), an anchor on this page, or another address (a new tab). */
export function hrefKind(href: string): HrefKind {
  if (href.startsWith("#")) return "anchor";
  if (href.startsWith("/")) return "page";
  return "external";
}

/** Every link in the pack, flattened: what the link test and the page's own tests walk. */
export function allLinks(p: Pack): PackLink[] {
  return [
    ...p.path.map((s) => ({ label: s.title, href: s.cta.href })),
    ...p.proof.flatMap((c) => c.evidence),
    ...p.groups.flatMap((g) => g.items),
    ...p.checklist.flatMap((c) => c.links),
  ];
}
