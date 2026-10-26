/**
 * The tour's stops per page. Steps target ids the pages already render (the dashboard's panel ids, the judges page's
 * chapter heads); a step whose target is missing is skipped, so a panel a gateway does not show never breaks the tour.
 */
import { JUDGE_CHAPTERS } from "@/pages/judges/chapters";
import type { TourStep } from "./tour-model";

export const JUDGES_STEPS: TourStep[] = JUDGE_CHAPTERS.map((c) => ({
  targets: [`#${c.id}-head`, `#${c.id}`],
  title: c.tourTitle,
  body: c.tour,
}));

export const DASHBOARD_STEPS: TourStep[] = [
  {
    targets: ["#share", "#tasks"],
    title: "Who answered",
    body: "The share of requests the small student answered, day by day. The rest went to the teacher.",
  },
  {
    targets: ["#tasks"],
    title: "The route of each request",
    body: "Per task: how many requests the student served, how many escalated, and the routes they took.",
  },
  {
    targets: ["#pipeline"],
    title: "The release stage",
    body: "Where each version is: shadow, canary at 10, 50 or 100 percent, live, or rolled back.",
  },
  {
    targets: ["#precision"],
    title: "Audits and precision",
    body: "The teacher re-checks a sample of the student's answers. A canary whose audited precision drops is rolled back on its own.",
  },
  {
    targets: ["#economics", "#cost"],
    title: "The cost",
    body: "Savings per token and by GPU-hours paid, with the break-even. On the live gateway these are its own traffic.",
  },
  {
    targets: ["#trust"],
    title: "Trust and privacy",
    body: "Redaction before storage, API keys, the signed bill of materials and a redactor you can try.",
  },
];

/** The steps for a page, by its path; none for a page without a tour (the story page has none). */
export function stepsFor(pathname: string): TourStep[] {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p === "/judges") return JUDGES_STEPS;
  if (p === "/dashboard") return DASHBOARD_STEPS;
  return [];
}
