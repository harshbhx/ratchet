// The only browser storage the site's tour uses, kept in this one file (INV-18 allows the theme and these flags, nothing else):
// where the tour is while it crosses from the story page to /live (sessionStorage, gone with the tab). No personal data.
// Every access is wrapped: storage can be blocked.

const STATE_KEY = "ratchet-tour-state";

function get(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function set(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    /* storage blocked: the tour still works, it just forgets */
  }
}

/** The tour's place across a page change: the index of the step to show next. */
export function readTourState(): { step: number } | null {
  const raw = get(STATE_KEY);
  if (!raw) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? { step: n } : null;
}
export const writeTourState = (step: number): void => set(STATE_KEY, String(step));
export const clearTourState = (): void => set(STATE_KEY, null);
