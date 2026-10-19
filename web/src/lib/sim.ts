/**
 * The reference simulation run (mock models, NOT real model quality):
 * `python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3`, seed 7.
 * Same numbers as the README table. Always label these as simulation in the UI.
 */
export interface SimDay {
  day: number;
  student: number; // share answered by the student
  escalated: number;
  audit: number | null; // audited precision
  costPer1k: number;
  baselinePer1k: number;
  saved: number;
  note?: string;
}

export const SIM_SOURCE = "Simulation, mock models: seed 7, 2,000 support tickets a day, product launch on day 5, corrupted deploy of v3.";

export const SIM_DAYS: SimDay[] = [
  { day: 1, student: 0.0, escalated: 0.0, audit: null, costPer1k: 1.093, baselinePer1k: 1.093, saved: 0.0, note: "Cold start: the teacher answers everything" },
  { day: 2, student: 0.44, escalated: 0.01, audit: 1.0, costPer1k: 0.675, baselinePer1k: 1.093, saved: 0.38, note: "First student enters canary" },
  { day: 3, student: 0.98, escalated: 0.02, audit: 1.0, costPer1k: 0.16, baselinePer1k: 1.093, saved: 0.85, note: "Student is live" },
  { day: 4, student: 0.98, escalated: 0.02, audit: 0.93, costPer1k: 0.16, baselinePer1k: 1.093, saved: 0.85, note: "v3 deployed corrupted: rolled back at 41% audited precision" },
  { day: 5, student: 0.86, escalated: 0.14, audit: 0.97, costPer1k: 0.272, baselinePer1k: 1.093, saved: 0.75, note: "Product launch: new questions escalate" },
  { day: 6, student: 0.88, escalated: 0.12, audit: 0.92, costPer1k: 0.246, baselinePer1k: 1.093, saved: 0.77 },
  { day: 7, student: 0.88, escalated: 0.12, audit: 0.97, costPer1k: 0.255, baselinePer1k: 1.093, saved: 0.77 },
  { day: 8, student: 0.95, escalated: 0.05, audit: 0.99, costPer1k: 0.2, baselinePer1k: 1.093, saved: 0.82, note: "Learned the launch questions overnight" },
  { day: 9, student: 0.98, escalated: 0.02, audit: 0.97, costPer1k: 0.162, baselinePer1k: 1.093, saved: 0.85 },
  { day: 10, student: 0.98, escalated: 0.02, audit: 0.99, costPer1k: 0.147, baselinePer1k: 1.093, saved: 0.87 },
];

export const SIM_TOTAL = { requests: 20000, cost: 6.74, baseline: 21.86, saved: 0.69 };
