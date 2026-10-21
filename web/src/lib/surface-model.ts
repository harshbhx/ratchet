// Which surface this page is on (live or the simulation), the other one's link, and the last real upstream call, from
// /ratchet/stats (FR-11.30, FR-11.31). Pure, so web/tests/surface-model.test.mjs runs it without a browser. Every field is
// optional: an older gateway sends none of these blocks and the pages simply hide what depends on them.

export interface UpstreamCall {
  role: string;
  host: string;
  requested_model: string | null;
  model: string | null;
  ok: boolean;
  status: number | null;
  error: string | null;
  reason: string | null;
  latency_ms: number;
  at: number;
  age_s: number;
  last_ok_age_s: number | null;
}

export interface Upstream {
  recording: boolean;
  since: number | null;
  token_factory: boolean;
  calls: UpstreamCall[];
}

export interface DemoInfo {
  surface: "live" | "simulation" | string;
  controls: boolean;
  shared: boolean;
  max_days: number | null;
  max_requests: number | null;
  idle_reset_s: number | null;
  actions_per_minute: number | null;
  auto_resets: number;
  last_reset_at: number | null;
}

export interface Links {
  live: string | null;
  sim: string | null;
}

export interface SurfaceStats {
  mode?: string;
  links?: Links;
  demo?: DemoInfo;
  upstream?: Upstream;
}

export interface SurfaceSwitch {
  current: "live" | "simulation";
  liveLabel: string;
  liveHref: string | null;
  simHref: string | null;
}

/** The header switch "Live on Nebius Token Factory | Simulation", or null on a gateway that sends neither block. */
export function surfaceSwitch(stats: SurfaceStats | null | undefined): SurfaceSwitch | null {
  if (!stats) return null;
  const surface = stats.demo?.surface ?? stats.mode;
  const current = surface === "simulation" || surface === "mock" ? "simulation" : surface === "live" ? "live" : null;
  if (!current) return null;
  if (!stats.demo && !stats.links) return null;
  return {
    current,
    liveLabel: stats.upstream?.token_factory ? "Live on Nebius Token Factory" : "Live",
    liveHref: stats.links?.live ?? null,
    simHref: stats.links?.sim ?? null,
  };
}

/** The newest call on `host` (the teacher and the hosted student both use Token Factory), or null. */
export function lastCall(upstream: Upstream | null | undefined, host: "token_factory" | "tavily"): UpstreamCall | null {
  const calls = (upstream?.calls ?? []).filter((c) => c.host === host);
  if (!calls.length) return null;
  return calls.reduce((a, b) => (b.at > a.at ? b : a));
}

/** "just now", "N min ago", "N h ago" or "N d ago". */
export function fmtAge(s: number): string {
  if (!Number.isFinite(s) || s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** "3 min ago · nvidia/Nemotron-3-Ultra-550b-a55b · 4.2 s", or "failed 3 min ago (timeout)", "(HTTP 429)", then the last success. */
export function lastCallLine(call: UpstreamCall): string {
  if (call.ok) {
    const model = call.model ?? call.requested_model;
    return [fmtAge(call.age_s), model, seconds(call.latency_ms)].filter(Boolean).join(" · ");
  }
  const why = call.reason === "http" && call.status != null ? `HTTP ${call.status}` : (call.reason ?? "error").replace("_", " ");
  const earlier = call.last_ok_age_s != null ? `, last success ${fmtAge(call.last_ok_age_s)}` : "";
  return `failed ${fmtAge(call.age_s)} (${why})${earlier}`;
}

/** The notice above the demo controls on the shared simulation; null on the live gateway. Only the limits that are set appear. */
export function sharedNotice(demo: DemoInfo | null | undefined): string | null {
  if (!demo?.shared) return null;
  const limits: string[] = [];
  if (demo.idle_reset_s) limits.push(`${demo.idle_reset_s >= 3600 ? `${+(demo.idle_reset_s / 3600).toFixed(1)} h` : `${Math.round(demo.idle_reset_s / 60)} min`} without activity`);
  if (demo.max_days) limits.push(`${demo.max_days} simulated days`);
  if (demo.max_requests) limits.push(`${demo.max_requests.toLocaleString("en-US")} requests`);
  const base = "Shared simulation: every visitor sees and changes the same state.";
  if (!limits.length) return base;
  const [first, ...rest] = limits;
  const tail = rest.length ? [first, ...rest.slice(0, -1)].join(", ") + ` or ${rest[rest.length - 1]}` : first;
  return `${base} It resets after ${tail}.`;
}
