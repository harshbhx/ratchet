/**
 * The human check in the browser, one widget per place it is needed. `solve(container)` draws Cloudflare's Turnstile widget INSIDE the given element
 * (the inline row under the button that asked; see components/human-row.tsx) and resolves to a fresh token for one paid call. Nothing here is ever
 * positioned on its own: no floating box, no fixed corner. Each surface passes its own element and gets its own widget.
 *
 * The widget is the managed one in its quiet form (`interaction-only`): it passes with no click and shows itself, in the same element, only when
 * Cloudflare wants the visitor to do something (`onInteractive` tells the row). Its size is `flexible`, so it fits the column on a phone, and its
 * theme is the site's current one. A token works once and lives 300 s, so every solve draws a new widget and removes it when it has an answer.
 *
 * Once per visit: the live Worker answers a call that carried a good token with a session cookie, after which `isVerified()` is true and no widget is
 * needed. `isVerified()` asks GET /ratchet/human (answered by the Worker, never wakes the container). The site key is public and comes from
 * GET /ratchet/turnstile, so it can be set in the Cloudflare dashboard without rebuilding the site.
 */
import { SITE_KEY_PATH, parseSiteKey } from "./turnstile-model";

interface TurnstileApi {
  render(container: HTMLElement, options: Record<string, unknown>): string;
  remove(id: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
export const HUMAN_PATH = "/ratchet/human";
/** How long a solve waits (a visitor may have to tick a box) before it gives up. */
const WAIT_MS = 60_000;

let siteKeyOnce: Promise<string | null> | null = null;
let scriptOnce: Promise<boolean> | null = null;
let statusOnce: Promise<boolean> | null = null;
/** When the verified cookie ends (ms), as the Worker last said; 0 when not verified. */
let verifiedUntil = 0;
const widgets = new WeakMap<HTMLElement, string>();

/** The site key, asked once; a failed ask is forgotten so the next call asks again. null means the check is off. */
export function siteKey(): Promise<string | null> {
  if (!siteKeyOnce) {
    siteKeyOnce = fetch(SITE_KEY_PATH, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(parseSiteKey)
      .catch(() => {
        siteKeyOnce = null;
        return null;
      });
  }
  return siteKeyOnce;
}

/** Is this visitor verified for the visit? Reads the Worker's answer (and remembers a yes until its expiry). Never rejects. */
export function isVerified(): Promise<boolean> {
  if (verifiedUntil > Date.now()) return Promise.resolve(true);
  if (!statusOnce) {
    statusOnce = fetch(HUMAN_PATH, { cache: "no-store", credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { verified?: unknown; expires?: unknown } | null) => {
        const until = b?.verified === true && typeof b.expires === "string" ? Date.parse(b.expires) : 0;
        verifiedUntil = Number.isFinite(until) ? until : 0;
        return verifiedUntil > Date.now();
      })
      .catch(() => false)
      .finally(() => {
        statusOnce = null;
      });
  }
  return statusOnce;
}

function loadScript(): Promise<boolean> {
  if (window.turnstile) return Promise.resolve(true);
  if (!scriptOnce) {
    scriptOnce = new Promise<boolean>((resolve) => {
      const s = document.createElement("script");
      s.src = SCRIPT_URL;
      s.async = true;
      s.onload = () => resolve(!!window.turnstile);
      s.onerror = () => {
        scriptOnce = null;
        resolve(false);
      };
      document.head.appendChild(s);
    });
  }
  return scriptOnce;
}

/** `off`: the Worker has the check off, nothing to do. `unavailable`: the widget could not load. `failed`: Cloudflare said no. `timeout`: no answer in time. */
export type SolveResult = { ok: true; token: string } | { ok: false; reason: "off" | "unavailable" | "failed" | "timeout" };

/** The site's own theme (not the system's): the pages put `dark` on the root element. */
const siteTheme = (): "light" | "dark" => (document.documentElement.classList.contains("dark") ? "dark" : "light");

/**
 * Draw the widget in `container` and wait for its token. `onInteractive` runs if Cloudflare needs the visitor to do something. The widget is removed
 * when there is an answer, so the container is empty again for the next solve. Never rejects.
 */
export async function solve(container: HTMLElement, hooks: { onInteractive: () => void }): Promise<SolveResult> {
  if (typeof window === "undefined" || typeof document === "undefined") return { ok: false, reason: "off" };
  const key = await siteKey();
  if (!key) return { ok: false, reason: "off" };
  if (!(await loadScript()) || !window.turnstile) return { ok: false, reason: "unavailable" };
  const api = window.turnstile;
  return new Promise<SolveResult>((resolve) => {
    let ended = false;
    const end = (result: SolveResult) => {
      if (ended) return;
      ended = true;
      clearTimeout(timer);
      const id = widgets.get(container);
      if (id !== undefined) {
        try {
          api.remove(id);
        } catch {
          /* already gone */
        }
        widgets.delete(container);
      }
      resolve(result);
    };
    const timer = setTimeout(() => end({ ok: false, reason: "timeout" }), WAIT_MS);
    try {
      const old = widgets.get(container);
      if (old !== undefined) api.remove(old);
      widgets.set(
        container,
        api.render(container, {
          sitekey: key,
          appearance: "interaction-only",
          size: "flexible",
          theme: siteTheme(),
          "response-field": false,
          callback: (token: string) => end({ ok: true, token }),
          "before-interactive-callback": () => {
            if (!ended) hooks.onInteractive();
          },
          "error-callback": () => end({ ok: false, reason: "failed" }),
          "timeout-callback": () => end({ ok: false, reason: "timeout" }),
          "unsupported-callback": () => end({ ok: false, reason: "unavailable" }),
        }),
      );
    } catch {
      end({ ok: false, reason: "unavailable" });
    }
  });
}

/** After a paid call went through with a token, ask again: the Worker's cookie is set if Cloudflare accepted it. */
export function refreshVerified(): Promise<boolean> {
  verifiedUntil = 0;
  return isVerified();
}
