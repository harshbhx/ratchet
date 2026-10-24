// What the Scout page's "Judge code" control shows (ADR 0120): the `judge` block of GET /scout/status read
// defensively, and a plain-words message for each status POST /scout/judge can answer. Pure: no storage, no network.

export interface JudgeView {
  /** Judge access is switched on for this gateway (hidden otherwise, mock mode included). */
  enabled: boolean;
  /** This browser holds a valid judge cookie. */
  session: boolean;
  /** Runs a judge can still start today, or null when the gateway does not say. */
  runsLeft: number | null;
}

const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : null);

/** Read `status.judge`. Anything missing or malformed counts as "judge access off", so the control stays hidden. */
export function parseJudge(status: unknown): JudgeView {
  const j = (status as { judge?: unknown } | null | undefined)?.judge;
  if (typeof j !== "object" || j === null) return { enabled: false, session: false, runsLeft: null };
  const b = j as Record<string, unknown>;
  const enabled = b.enabled === true;
  return { enabled, session: enabled && b.session === true, runsLeft: enabled ? count(b.runs_left_today) : null };
}

/** The message for a failed unlock, by HTTP status (0: the request never got an answer). */
export function unlockError(httpStatus: number): string {
  switch (httpStatus) {
    case 400:
      return "Type the judge code first.";
    case 401:
      return "That code is not right. Check it and try again.";
    case 404:
      return "Judge access is not turned on for this demo.";
    case 429:
      return "Too many tries from this address. Try again in an hour.";
    case 0:
      return "Could not reach the demo. Check your connection and try again.";
    default:
      return `Something went wrong (${httpStatus}). Try again in a moment.`;
  }
}

/** The message for a failed sign-out. */
export function signOutError(httpStatus: number): string {
  return httpStatus === 0 ? "Could not reach the demo. Check your connection and try again." : `Could not end the session (${httpStatus}).`;
}

/** The runs-left line of the "Judge access on" state. */
export function runsLeftText(runsLeft: number | null): string {
  if (runsLeft === null) return "";
  if (runsLeft === 0) return "No judge runs left today. The count resets at 00:00 UTC.";
  return `${runsLeft} judge ${runsLeft === 1 ? "run" : "runs"} left today.`;
}

/** What the live region says after an action. */
export const JUDGE_ON_ANNOUNCE = "Judge access is on.";
export const JUDGE_OFF_ANNOUNCE = "Judge access ended.";
