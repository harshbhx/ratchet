// The inline human check under a button, as a small state machine. Pure: no DOM, no network. src/lib/turnstile.ts talks to Cloudflare,
// src/lib/use-human.ts runs this model, and src/components/human-row.tsx draws it.
//
// One check per visit: the live Worker answers a passed check with a session cookie (deploy/cloudflare-live/src/human.ts), after which a paid call needs
// nothing more and this row shows nothing at all.

export type HumanReason = "failed" | "blocked" | "unavailable";

export type HumanState =
  /** Nothing to show: not needed, or already verified for the visit. */
  | { phase: "idle" }
  /** Asking Cloudflare; usually over in a moment with no click. */
  | { phase: "checking" }
  /** Cloudflare wants the visitor to do something: the widget is shown in the same spot. */
  | { phase: "interactive" }
  /** The check passed just now; the request that was waiting goes on by itself. */
  | { phase: "verified" }
  /** The request cannot go on: the check failed, the visitor is blocked, or (Scout) the check did not load. */
  | { phase: "error"; reason: HumanReason }
  /** The check did not load (an ad blocker?) but the request still goes, as a plain keyless call with a few a day. */
  | { phase: "skipped" };

export type HumanEvent =
  | { type: "start" }
  | { type: "interactive" }
  | { type: "passed" }
  | { type: "failed" }
  | { type: "blocked" }
  /** The widget could not load. `required`: the call cannot go without it (Scout). */
  | { type: "unavailable"; required: boolean }
  | { type: "reset" };

export const initialHuman: HumanState = { phase: "idle" };

export function reduceHuman(state: HumanState, ev: HumanEvent): HumanState {
  switch (ev.type) {
    case "start":
      return { phase: "checking" };
    case "interactive":
      // Only a check in progress can turn interactive (a late callback after a pass or a failure changes nothing).
      return state.phase === "checking" ? { phase: "interactive" } : state;
    case "passed":
      return { phase: "verified" };
    case "failed":
      return { phase: "error", reason: "failed" };
    case "blocked":
      return { phase: "error", reason: "blocked" };
    case "unavailable":
      return ev.required ? { phase: "error", reason: "unavailable" } : { phase: "skipped" };
    case "reset":
      return initialHuman;
  }
}

/** Does the row take up room? */
export const rowOpen = (s: HumanState): boolean => s.phase !== "idle";
/** Is the widget itself in view (only while Cloudflare wants a click)? */
export const widgetShown = (s: HumanState): boolean => s.phase === "interactive";
/** Can the visitor ask for the check again? */
export const canRetry = (s: HumanState): boolean => s.phase === "error" && s.reason !== "blocked";

export const CHECKING_TEXT = "Checking you're human";
export const CHECKING_BRAND = "Cloudflare";
export const INTERACTIVE_TEXT = "One quick check, then you're set for this visit.";
export const VERIFIED_TEXT = "Verified for this visit";
export const FAILED_TEXT = "Couldn't confirm you're human. Try again.";
export const BLOCKED_TEXT = "Too many failed checks from your network today. It resets at 00:00 UTC.";
export const SKIPPED_TEXT = "The human check didn't load (an ad blocker?). Requests still work, a few a day.";
export const SCOUT_UNAVAILABLE_TEXT = "Scout needs the human check, and it didn't load (an ad blocker?). Allow challenges.cloudflare.com and try again.";

/** The one line of words for a state, or null when it has none (idle). `required`: this surface cannot go without the check (Scout). */
export function humanText(s: HumanState, required: boolean): string | null {
  switch (s.phase) {
    case "idle":
      return null;
    case "checking":
      return CHECKING_TEXT;
    case "interactive":
      return INTERACTIVE_TEXT;
    case "verified":
      return VERIFIED_TEXT;
    case "skipped":
      return SKIPPED_TEXT;
    case "error":
      if (s.reason === "blocked") return BLOCKED_TEXT;
      if (s.reason === "unavailable") return required ? SCOUT_UNAVAILABLE_TEXT : SKIPPED_TEXT;
      return FAILED_TEXT;
  }
}

/**
 * What a refused call says about the check, if anything: the Worker's own 403 for a failed or missing check, its 429 for a blocked visitor.
 * `code` is the /v1 error code; Scout's refusals carry only a `detail` sentence, which is read from `message`.
 */
export function failureOf(status: number, code: unknown, message?: unknown): "failed" | "blocked" | null {
  const text = typeof message === "string" ? message.trimStart() : "";
  if (status === 403 && (code === "human_check_failed" || code === "human_check_required" || text.startsWith("Human check"))) return "failed";
  if (status === 429 && (code === "human_check_blocked" || text.startsWith("Too many failed human checks"))) return "blocked";
  return null;
}

/** The same for a Scout error message with no status (the run's error event or the fallback's ApiError). */
export function failureOfMessage(message: string): "failed" | "blocked" | null {
  const text = message.trimStart();
  if (text.startsWith("Human check")) return "failed";
  if (text.startsWith("Too many failed human checks")) return "blocked";
  return null;
}
