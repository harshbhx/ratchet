// A cost report shaped like GET /ratchet/cost, for the pure-helper tests. Test data only; the site never renders it.
export const FIXTURE_COST_REPORT = {
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
  "endpoint_usd_per_hour": 1.55,
  "idle_hours": 24,
  "idle_share": 0.1,
  "idle_granularity": "day",
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
  "explanation": "Self-hosting does not pay at this volume. The endpoint served 66 student requests per hour it ran, below the break-even of 1,575; below 51,528 an hour the hosted fallback is cheaper per answer (1 GPU at $1.55 per GPU-hour; unverified list prices). Use the hosted fallback student on Token Factory (leave RATCHET_STUDENT_BASE empty) or run the endpoint only while traffic arrives. The crossover assumes the hosted fallback serves as many requests as the tuned student; it is untuned, so more of its requests may go to the teacher. Savings by GPU-hours paid: -$358.48 (-1640%); per token: $15.12 (69%)."
};
