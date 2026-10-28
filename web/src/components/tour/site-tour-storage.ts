// The only browser storage the site's tour uses, kept in this one file (INV-18 allows the theme and these flags, nothing else):
// a one-time hint's dismissal and the corner button's hidden flag (localStorage), and where the tour is while it crosses from the
// story page to /live (sessionStorage, gone with the tab). No personal data, no keys. Every access is wrapped: storage can be blocked.

const HINT_KEY = "ratchet-tour-hint";
const BUTTON_KEY = "ratchet-tour-button";
const STATE_KEY = "ratchet-tour-state";

function get(store: "local" | "session", key: string): string | null {
  try {
    return (store === "local" ? window.localStorage : window.sessionStorage).getItem(key);
  } catch {
    return null;
  }
}
function set(store: "local" | "session", key: string, value: string | null): void {
  try {
    const s = store === "local" ? window.localStorage : window.sessionStorage;
    if (value === null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    /* storage blocked: the tour still works, it just forgets */
  }
}

export const hintDismissed = (): boolean => get("local", HINT_KEY) === "dismissed";
export const dismissHint = (): void => set("local", HINT_KEY, "dismissed");
export const buttonHidden = (): boolean => get("local", BUTTON_KEY) === "hidden";
export const setButtonHidden = (hidden: boolean): void => set("local", BUTTON_KEY, hidden ? "hidden" : null);

/** The tour's place across a page change: the index of the step to show next. */
export function readTourState(): { step: number } | null {
  const raw = get("session", STATE_KEY);
  if (!raw) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? { step: n } : null;
}
export const writeTourState = (step: number): void => set("session", STATE_KEY, String(step));
export const clearTourState = (): void => set("session", STATE_KEY, null);
