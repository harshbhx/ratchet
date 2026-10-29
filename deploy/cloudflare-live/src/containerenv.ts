/** The slice of the Worker env this file reads. */
export interface ContainerEnvSource {
  NEBIUS_API_KEY?: unknown;
  TAVILY_API_KEY?: unknown;
  DEMO_API_KEY?: unknown;
  JUDGES_API_KEY?: unknown;
  [name: string]: unknown;
}

const JUDGES_LIMITS = "judges:daily_requests=300,daily_usd=1,rpm=30,concurrency=2";

/** The defaults. Any RATCHET_* variable or secret on the Worker replaces the entry of the same name. */
const DEFAULTS: Record<string, string> = {
  RATCHET_MODE: "live",
  // The image's /data volume does not exist on Cloudflare Containers, and live mode refuses a ledger whose folder is missing.
  // /tmp is on the container's own disk: the ledger starts empty whenever the container does (see the note above).
  RATCHET_DB: "/tmp/ratchet.db",
  RATCHET_DATA_DIR: "/tmp/ratchet-data",
  RATCHET_KEY_LIMITS: "demo:daily_requests=300,daily_usd=1.5,rpm=60,concurrency=4",
  RATCHET_DAILY_BUDGET_USD: "2.5",
  RATCHET_BUDGET_MODE: "block",
  RATCHET_SCOUT_DAILY_BUDGET_USD: "1.5",
  RATCHET_SCOUT_RUNS_PER_HOUR: "6",
  RATCHET_PUBLIC_DASHBOARD: "1",
  RATCHET_TRUST_PROXY: "1",
  RATCHET_IP_RPM: "30",
  RATCHET_MAX_INFLIGHT: "6",
  RATCHET_STATS_TTL_S: "2",
};

/** A secret as a clean string: a value piped in from PowerShell carries a trailing line break, which would break an environment variable or a header. */
export const clean = (v: unknown): string => String(v ?? "").trim();

/** The container's environment: keys, then the defaults, then every RATCHET_* setting from the dashboard on top. */
export function containerEnv(env: ContainerEnvSource): Record<string, string> {
  const out: Record<string, string> = {
    NEBIUS_API_KEY: clean(env.NEBIUS_API_KEY),
    TAVILY_API_KEY: clean(env.TAVILY_API_KEY),
    // One named key for the demo's own pages; the Worker adds it, a visitor never sees it.
    RATCHET_API_KEYS: `demo:${clean(env.DEMO_API_KEY)}`,
    ...DEFAULTS,
  };
  // The optional judges' key: a second named key with its own, tighter limits. Unset or blank: nothing changes. The gateway
  // takes keys as name:key separated by "," and limits as name:field=value,field=value separated by ";" (ratchet/auth.py).
  // A RATCHET_API_KEYS or RATCHET_KEY_LIMITS setting on the dashboard replaces these whole, so it drops both built-in entries.
  const judges = clean(env.JUDGES_API_KEY);
  if (judges !== "") {
    out.RATCHET_API_KEYS += `,judges:${judges}`;
    out.RATCHET_KEY_LIMITS = `${DEFAULTS.RATCHET_KEY_LIMITS};${JUDGES_LIMITS}`;
  }
  for (const [name, value] of Object.entries(env)) {
    if (name.startsWith("RATCHET_") && typeof value === "string" && value.trim() !== "") out[name] = value.trim();
  }
  return out;
}

