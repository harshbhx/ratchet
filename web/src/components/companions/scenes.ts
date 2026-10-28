// What the two are doing wherever the reader stops. Every landing section and every route has a scene: how each one looks and what it
// holds or does, and sometimes a short piece of choreography played once per visit. Placement is separate (perch.ts): the scene says
// what, the perch says where, and neither ever puts them on text. Every caption-like string is held to the site's facts.
import type { Facts } from "./banter";
import type { Look } from "./companion-model";
import type { DuoMove } from "./reactions";

export interface Scene {
  nano: Partial<Look>;
  ultra: Partial<Look>;
  /** Played once per visit when the reader first stops here (both walk together first). */
  once?: DuoMove;
}

/** The landing sections the page watches for the scene, by element id, in page order. */
export const SCENE_SECTIONS = ["hero", "real-run", "try", "whats-in", "rent", "catch", "why", "how", "lifecycle", "safety", "learn", "bill", "trust", "scout", "stack", "integrate", "nebius", "run", "closing-cta", "footer"] as const;
export const ROUTE_SCENES = ["live", "judges", "scout-page", "dashboard"] as const;

export function scenes(f: Facts): Record<string, Scene> {
  const pct = (s: string) => s.replace(".0%", "%");
  return {
    // The first screen: they greet, and Nano points at the button.
    hero: { nano: { expr: "wave", pose: "pointLeft" }, ultra: { expr: "happy" } },
    // The exam: Nano holds up the score, Ultra checks it with the red pen.
    "real-run": {
      nano: { expr: "proud", held: "score", text: pct(f.r2e1), pose: "carry", pop: "sparkle" },
      ultra: { expr: "idle", held: "papers", pose: "carry", fx: "pen" },
    },
    try: { nano: { expr: "happy", pose: "pointLeft" }, ultra: { expr: "idle" } },
    "whats-in": { nano: { expr: "thinking" }, ultra: { expr: "idle", fx: "pen", held: "papers", pose: "carry" } },
    // Ultra teaches the section's one idea on a small card; the chip on Nano's head fills a segment when the lesson lands.
    rent: { nano: { expr: "thinking", pop: "?" }, ultra: { expr: "idle", held: "tag", text: "Pay per token", pose: "carry" } },
    catch: { nano: { expr: "sad" }, ultra: { expr: "determined", pose: "fold" } },
    why: { nano: { expr: "thinking" }, ultra: { expr: "idle", brow: "raise" } },
    // How a request is routed: Nano carries the request card, Ultra receives it.
    how: { nano: { expr: "determined", held: "card", pose: "carry" }, ultra: { expr: "idle", held: "tag", text: "Student first", pose: "carry" }, once: "pass" },
    lifecycle: { nano: { expr: "thinking", held: "card", pose: "carry" }, ultra: { expr: "idle", pose: "watch", fx: "watch" } },
    // The gate: Ultra in front of a door, blocking Nano.
    safety: { nano: { expr: "sad", sweat: true }, ultra: { expr: "determined", pose: "stop", fx: "door" }, once: "sneak" },
    // Night School: Ultra teaches, Nano takes notes, and now and then a guess is corrected and tried again.
    learn: { nano: { expr: "happy", held: "book", pose: "carry", fx: "read" }, ultra: { expr: "idle", held: "tag", text: "Teacher labels", pose: "carry" }, once: "correct" },
    bill: { nano: { expr: "surprised" }, ultra: { expr: "idle", fx: "pen", held: "papers", pose: "carry" } },
    trust: { nano: { expr: "determined" }, ultra: { expr: "proud", pose: "fold" } },
    scout: { nano: { expr: "thinking", held: "card", pose: "carry" }, ultra: { expr: "idle", pose: "pointLeft" } },
    // Where each one runs: each stands next to its place.
    stack: {
      nano: { expr: "proud", held: "tag", text: "Nebius AI Cloud", pose: "carry" },
      ultra: { expr: "idle", held: "tag", text: "Token Factory", pose: "carry" },
    },
    integrate: { nano: { expr: "wave" }, ultra: { expr: "happy", held: "tag", text: "One line change", pose: "carry" } },
    nebius: { nano: { expr: "thinking" }, ultra: { expr: "idle", fx: "watch", pose: "watch" } },
    run: { nano: { expr: "happy" }, ultra: { expr: "proud", pose: "fold" } },
    // The closing card: both cheer.
    "closing-cta": { nano: { expr: "laughing", pose: "jump", pop: "sparkle" }, ultra: { expr: "happy", pose: "jump" }, once: "highfive" },
    // The foot of the page: both wave goodbye.
    footer: { nano: { expr: "wave" }, ultra: { expr: "wave" } },
    // Other pages.
    live: { nano: { expr: "happy", pose: "pointLeft" }, ultra: { expr: "idle" } },
    judges: { nano: { expr: "determined", held: "book", pose: "carry", fx: "read" }, ultra: { expr: "idle", held: "papers", pose: "carry", fx: "pen" } },
    "scout-page": { nano: { expr: "thinking", held: "card", pose: "carry" }, ultra: { expr: "idle", pose: "pointLeft" } },
    dashboard: { nano: { expr: "idle" }, ultra: { expr: "idle", pose: "watch", fx: "watch" } },
  };
}

/** Which scene a route has: the landing page is read section by section; the others have one each. */
export function routeScene(pathname: string): string | null {
  if (pathname === "/") return null;
  if (pathname.startsWith("/live")) return "live";
  if (pathname.startsWith("/judges")) return "judges";
  if (pathname.startsWith("/scout")) return "scout-page";
  if (pathname.startsWith("/dashboard")) return "dashboard";
  return null;
}

/**
 * The section the reader is at: the last one whose top is above the line a little over a third down the screen, and the page's
 * foot and head count as the footer and the hero. `tops` are the sections' top edges in viewport pixels, in page order.
 */
export function currentSection(tops: Array<{ id: string; top: number; bottom: number }>, vh: number, nearBottom: boolean): string {
  if (nearBottom) return "footer";
  const line = vh * 0.4;
  let id = "hero";
  for (const t of tops) if (t.top <= line && t.bottom > line) id = t.id;
  return id;
}
