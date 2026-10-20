import type { CostReport } from "./api";

/**
 * The reference simulation run (mock models, NOT real model quality):
 * `python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3`, seed 7.
 * Same numbers as the README table. Always label these as simulation in the UI.
 * tests/test_contract.py::test_story_simulation_numbers_match_reference_run checks every number in this file against a
 * fresh run, at the precision each literal is written with, so a change to the simulator cannot leave the page stale.
 */
export const SIM_COMMAND = "python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3";

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

/**
 * The same run's cost report (`cost` in the results JSON; `ratchet.cost.cost_report`, ADR 0023): the student priced by
 * the GPU-hours its endpoint ran (the simulator keeps it up 24 h a day) as well as per token, at the default list
 * prices, which are unverified. A rounded subset of `GET /ratchet/cost`, so the story renders it with the same code as
 * a live report. Strict JSON up to the end marker, so the test can read it.
 */
export const SIM_COST_REPORT: CostReport = {
  "model": "per_token",
  "prices": {
    "verified": false,
    "label": "unverified list prices",
    "student_gpu_usd_per_hour": 1.55,
    "student_gpus": 1,
    "student_endpoint_usd_per_hour": 1.55,
    "train_gpu_usd_per_hour": 1.55,
    "train_gpus": 1,
    "teacher_model": "nvidia/Nemotron-3-Ultra-550b-a55b",
    "teacher_usd_per_m_tokens": { "input": 1.0, "output": 3.0 },
    "fallback_student_model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "fallback_student_usd_per_m_tokens": { "input": 0.06, "output": 0.24 }
  },
  "requests": 20000,
  "student_served": 15848,
  "teacher_served": 4152,
  "teacher_usd": 6.26,
  "student_per_token_usd": 0.477,
  "hosted_student_usd": 0,
  "baseline_usd": 21.86,
  "endpoint_hours": 240,
  "endpoint_usd": 372.0,
  "idle_hours": 24,
  "idle_share": 0.1,
  "training_hours": 1.34,
  "training_usd": 2.08,
  "views": {
    "per_token": { "total_usd": 6.74, "savings_usd": 15.12, "savings_pct": 0.692 },
    "gpu_hours": { "total_usd": 380.34, "savings_usd": -358.48, "savings_pct": -16.398 }
  },
  "avoided_teacher_usd_per_student_request": 0.000984,
  "break_even_student_requests_per_gpu_hour": 1575,
  "hosted_fallback_usd_per_request": 0.0000301,
  "self_host_beats_hosted_above_requests_per_hour": 51528,
  "observed_student_requests_per_endpoint_hour": 66.03,
  "utilization_vs_break_even": 0.042,
  "recommendation": "hosted_fallback",
  "explanation": "Self-hosting does not pay at this volume. The endpoint served 66 student requests per hour it ran, below the break-even of 1,575; below 51,528 an hour the hosted fallback is cheaper (1 GPU at $1.55 per GPU-hour; unverified list prices). Use the hosted fallback student on Token Factory (leave RATCHET_STUDENT_BASE empty) or run the endpoint only while traffic arrives. Savings by GPU-hours paid: -$358.48 (-1640%); per token: $15.12 (69%)."
};
// end SIM_COST_REPORT
