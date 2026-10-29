/**
 * The human check in the browser. `getToken()` resolves to a FRESH Turnstile token for one paid call, or null when there is none to give: the Worker
 * has the check off (no site key), the script did not load (an ad blocker, no network) or the widget did not answer in time. A call then goes without a
 * token, which the Worker treats as a plain keyless call (3 a day, the shared curl allowance) and, for Scout, refuses with a plain message.
 *
 * A token works once and lives 300 s, so this never hands one out twice: every call resets the widget and waits for a new token. Calls are queued, so
 * two at once use the widget one after the other. The widget is the managed one in its quiet form (`interaction-only`): it passes with no click
 * and shows itself only when Cloudflare wants the visitor to do something. The site key is public and comes from the live Worker (GET /ratchet/turnstile),
 * so it can be set in the Cloudflare dashboard without rebuilding the site.
 */
import { SITE_KEY_PATH, parseSiteKey } from "./turnstile-model";

interface TurnstileApi {
  render(container: HTMLElement, options: Record<string, unknown>): string;
  reset(id: string): void;
  execute(id: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
/** How long a call waits for a token (a visitor may have to tick a box) before it goes without one. */
const WAIT_MS = 45_000;

let siteKeyOnce: Promise<string | null> | null = null;
let scriptOnce: Promise<boolean> | null = null;
let widgetId: string | null = null;
let host: HTMLElement | null = null;
let settle: ((token: string | null) => void) | null = null;
let queue: Promise<unknown> = Promise.resolve();

/** The site key, asked once; a failed ask is forgotten so the next call asks again. */
function siteKey(): Promise<string | null> {
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

function container(): HTMLElement {
  if (!host) {
    host = document.createElement("div");
    host.setAttribute("data-turnstile-host", "");
    host.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:60";
    document.body.appendChild(host);
  }
  return host;
}

async function fresh(): Promise<string | null> {
  if (typeof window === "undefined" || typeof document === "undefined") return null;
  const key = await siteKey();
  if (!key || !(await loadScript()) || !window.turnstile) return null;
  const api = window.turnstile;
  return new Promise<string | null>((resolve) => {
    const timer = setTimeout(() => done(null), WAIT_MS);
    const done = (token: string | null) => {
      clearTimeout(timer);
      settle = null;
      resolve(token);
    };
    settle = done;
    try {
      if (widgetId === null) {
        widgetId = api.render(container(), {
          sitekey: key,
          execution: "execute",
          appearance: "interaction-only",
          callback: (token: string) => settle?.(token),
          "error-callback": () => settle?.(null),
        });
      } else {
        api.reset(widgetId);
      }
      api.execute(widgetId);
    } catch {
      done(null);
    }
  });
}

/** A fresh token for one paid call, or null (see the top of this file). Never rejects. */
export function getToken(): Promise<string | null> {
  const next = queue.then(fresh, fresh).catch(() => null);
  queue = next;
  return next;
}
