// The judge's key as a shared value: every box on the site reads the same one. Kept in the browser only (see judge-key-model.ts).
import { useSyncExternalStore } from "react";
import { cleanKey, forgetKey, isRemembered, loadKey, saveKey, type KeyStore } from "./judge-key-model";

const stores = (): [KeyStore | null, KeyStore | null] => {
  try {
    return [window.sessionStorage, window.localStorage];
  } catch {
    return [null, null];
  }
};

// The key is held in memory too, so it still works for the visit when storage is blocked.
let current: string | null = null;
let remembered = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function read(): string {
  if (current === null) {
    const [s, l] = stores();
    current = loadKey(s, l);
    remembered = isRemembered(l);
  }
  return current;
}

export function setJudgeKey(key: string, remember: boolean): void {
  const [s, l] = stores();
  const k = cleanKey(key);
  saveKey(k, remember, s, l);
  current = k;
  remembered = remember && k !== "";
  emit();
}

export function clearJudgeKey(): void {
  const [s, l] = stores();
  forgetKey(s, l);
  current = "";
  remembered = false;
  emit();
}

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => void listeners.delete(cb);
};

/** `key` is "" when none is set. */
export function useJudgeKey(): { key: string; remembered: boolean } {
  const key = useSyncExternalStore(subscribe, read, () => "");
  return { key, remembered };
}
