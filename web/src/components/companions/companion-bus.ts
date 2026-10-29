// The small part of the companions that lives in the main bundle: a window event the page uses to talk to them (the hero's
// "Watch Nano learn" button, the live request box, the footer's hide control) and the hidden flag as a hook. The characters
// themselves are a separate chunk, loaded after first paint.
import { useEffect, useState } from "react";
import { readHidden, writeHidden } from "./companion-storage";

export const COMPANION_EVENT = "ratchet-companion";

export type CompanionSignal =
  | { type: "story" }
  | { type: "sheet" }
  | { type: "show" }
  | { type: "hide" }
  | { type: "live"; phase: "sent" | "answer" | "limit" | "error" };

export function signal(s: CompanionSignal): void {
  if (typeof window === "undefined") return;
  if (s.type === "show" || s.type === "story" || s.type === "sheet") writeHidden(false);
  if (s.type === "hide") writeHidden(true);
  window.dispatchEvent(new CustomEvent<CompanionSignal>(COMPANION_EVENT, { detail: s }));
}

/** Whether the visitor hid Nano and Ultra (remembered), following changes made by another control on the page. */
export function useCompanionsHidden(): boolean {
  const [hidden, setHidden] = useState(readHidden);
  useEffect(() => {
    const on = (e: Event) => {
      const t = (e as CustomEvent<CompanionSignal>).detail?.type;
      if (t === "show" || t === "story" || t === "sheet") setHidden(false);
      else if (t === "hide") setHidden(true);
    };
    window.addEventListener(COMPANION_EVENT, on);
    return () => window.removeEventListener(COMPANION_EVENT, on);
  }, []);
  return hidden;
}
