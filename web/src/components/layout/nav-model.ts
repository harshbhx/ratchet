// Every link the header and the footer show, as data. Add a page with one line in PAGE_NAV (and, if the footer should
// list it, one line in FOOTER_COLUMNS); the header's pill, the phone menu and the footer render from these lists.
// No JSX here, so a test can import it.
import type { ChapterId } from "@/pages/landing/shared";
import { DEMO_URL, DOCS_URL, REPO_URL } from "@/lib/utils";

/** `section`: an anchor on the story page; `route`: a page of this app; `external`: another site, opened in a new tab. */
export type NavKind = "section" | "route" | "external";

export type NavItem =
  | { label: string; href: `#${ChapterId | "real-run"}`; kind: "section" }
  | { label: string; href: `/${string}`; kind: "route" }
  | { label: string; href: `https://${string}`; kind: "external" };

/** The story page's chapters the pill follows as the reader scrolls (ids from CHAPTERS in pages/landing/shared.tsx). */
export const STORY_NAV: readonly NavItem[] = [
  { label: "Results", href: "#real-run", kind: "section" },
  { label: "How it works", href: "#how", kind: "section" },
  { label: "Safety", href: "#safety", kind: "section" },
  { label: "Learn", href: "#learn", kind: "section" },
  { label: "The bill", href: "#bill", kind: "section" },
  { label: "Scout", href: "#scout", kind: "section" },
  { label: "Docs", href: DOCS_URL as `https://${string}`, kind: "external" },
];

/** The app's pages, shown in the pill everywhere but the story page, and in the phone menu on every page. */
export const PAGE_NAV: readonly NavItem[] = [
  { label: "Story", href: "/", kind: "route" },
  { label: "Scout", href: "/scout", kind: "route" },
  { label: "Dashboard", href: "/dashboard", kind: "route" },
  { label: "For judges", href: "/judges", kind: "route" },
  { label: "Docs", href: DOCS_URL as `https://${string}`, kind: "external" },
];

/** Shown in the phone menu after the pages. */
export const MORE_NAV: readonly NavItem[] = [{ label: "GitHub", href: REPO_URL as `https://${string}`, kind: "external" }];

const docs = (path: string) => `${DOCS_URL}${path}` as `https://${string}`;
const repo = (path: string) => `${REPO_URL}${path}` as `https://${string}`;

const HF = "https://huggingface.co/harshbhx";
/** Where each real artifact lives; the footer and the dashboard's results panel link the same addresses. */
export const HF_MODEL = `${HF}/ratchet-nemotron-nano-4b-text2sql` as `https://${string}`;
export const HF_TRAIN = "https://huggingface.co/datasets/harshbhx/ratchet-text2sql-synthetic" as `https://${string}`;
export const HF_EXAMS = "https://huggingface.co/datasets/harshbhx/ratchet-text2sql-exams" as `https://${string}`;
export const HF_TOOL_MODEL = `${HF}/ratchet-nemotron-nano-4b-toolcall` as `https://${string}`;
/** The maintainer's answer on the license question for the docs site's adapted components. */
export const REACT_BITS_PERMISSION = "https://github.com/DavidHDev/react-bits/discussions/1111" as `https://${string}`;

/** The footer's link columns. Every external URL was checked to resolve when it was added. */
export const FOOTER_COLUMNS: readonly { title: string; items: readonly NavItem[] }[] = [
  {
    title: "Product",
    items: [
      { label: "Story", href: "/", kind: "route" },
      { label: "Scout", href: "/scout", kind: "route" },
      { label: "Dashboard", href: "/dashboard", kind: "route" },
      { label: "For judges", href: "/judges", kind: "route" },
      { label: "Simulation demo", href: DEMO_URL as `https://${string}`, kind: "external" },
    ],
  },
  {
    title: "Real results",
    items: [
      { label: "Exam scores", href: "/judges", kind: "route" },
      { label: "Trained adapter", href: HF_MODEL, kind: "external" },
      { label: "Training data", href: HF_TRAIN, kind: "external" },
      { label: "Exams and answers", href: HF_EXAMS, kind: "external" },
      { label: "Tool-calling adapter", href: HF_TOOL_MODEL, kind: "external" },
    ],
  },
  {
    title: "Learn",
    items: [
      { label: "Docs", href: docs(""), kind: "external" },
      { label: "Quickstart", href: docs("guides/quickstart/"), kind: "external" },
      { label: "FAQ", href: docs("guides/faq/"), kind: "external" },
      { label: "API reference", href: docs("api/endpoints/"), kind: "external" },
    ],
  },
  {
    title: "Open source",
    items: [
      { label: "GitHub", href: repo(""), kind: "external" },
      { label: "License (Apache-2.0)", href: repo("/blob/main/LICENSE"), kind: "external" },
      { label: "Issues", href: repo("/issues"), kind: "external" },
    ],
  },
  {
    title: "Trust",
    items: [
      { label: "Security", href: docs("guides/security/"), kind: "external" },
      { label: "Licenses", href: docs("guides/licenses/"), kind: "external" },
      { label: "Evidence", href: docs("guides/evidence/"), kind: "external" },
      { label: "Check it yourself", href: docs("reference/verify/"), kind: "external" },
    ],
  },
];

/** The story page shows its chapters in the pill; every other page shows the pages. */
export function pillItems(pathname: string): readonly NavItem[] {
  return pathname === "/" ? STORY_NAV : PAGE_NAV;
}

/** The section ids the pill follows on this page (none off the story page). */
export function sectionIds(items: readonly NavItem[]): string[] {
  return items.filter((i) => i.kind === "section").map((i) => i.href.slice(1));
}

/** A route is current on its own path, and `/` only on itself. */
export function isCurrentRoute(item: NavItem, pathname: string): boolean {
  if (item.kind !== "route") return false;
  if (item.href === "/") return pathname === "/";
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}
