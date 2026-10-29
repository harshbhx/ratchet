// "Judge mode" on /judges: what the panel shows with a judges' key, the real requests its buttons send and how it reads their answers.
// Pure: no DOM, no network. The key itself is handled only in judge-key-model.ts (browser storage, an Authorization header, never a URL).
import type { ServedBy } from "@/lib/api";

export const MODE_BADGE = "Judge mode · key active";
export const NO_KEY_LINE = "Judges: paste the key from your dossier to switch on Judge mode.";
export const DOSSIER_LINE = "Your key and the Nebius account details are in your judges' dossier.";

/** The built-in judges' key limits (deploy/cloudflare-live: JUDGES_API_KEY), shown only when the gateway reports no per-key count. */
export const LIMITS_TEXT = "Limits for a judges' key: 300 requests a day, $1 a day, 30 a minute.";

/** The panel is shown only on a live gateway with a key set; without one the page shows the one-line invitation. */
export function panelState(mode: string | null, key: string): "panel" | "invite" | "none" {
  if (mode !== "live") return "none";
  return key.trim() ? "panel" : "invite";
}

/** Requests left today when the gateway said so (its `X-RateLimit-Remaining-Requests` header); otherwise the key's limits as text. */
export function budgetLine(left: number | null): string {
  return left == null ? LIMITS_TEXT : `${left} left today`;
}

export interface JudgeTest {
  id: "sql" | "tools";
  label: string;
  body: Record<string, unknown>;
}

/**
 * The one-click tests. The live gateway registers no text-to-SQL task by default, so the SQL question goes as a plain request on the default
 * task. A request that offers `tools` is answered by the teacher alone, with its tools, and is never stored (docs/API.md). Both are short and
 * both carry `store: false`.
 */
export const TESTS: JudgeTest[] = [
  {
    id: "sql",
    label: "Ask a SQL question",
    body: {
      model: "ratchet",
      max_tokens: 160,
      store: false,
      messages: [
        {
          role: "user",
          content: "Write one SQL query: the five customers with the highest total order value. Tables: customers(id, name), orders(id, customer_id, total). Reply with the query only.",
        },
      ],
    },
  },
  {
    id: "tools",
    label: "Ask a tool-calling question",
    body: {
      model: "ratchet",
      max_tokens: 120,
      store: false,
      messages: [{ role: "user", content: "What is the weather in Pune right now?" }],
      tools: [
        {
          type: "function",
          function: {
            name: "get_weather",
            description: "Current weather for a city.",
            parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] },
          },
        },
      ],
    },
  },
];

export interface JudgeResult {
  /** The answer's text, or for a tool call the call itself as `name({"arg": ...})`. */
  text: string;
  servedBy: ServedBy | null;
  model: string | null;
  route: string | null;
  /** What serving it cost in US dollars (`ratchet.cost_usd`), null when the reply did not say. */
  costUsd: number | null;
}

/** Read a chat completion defensively: text or a tool call, who answered and the cost. null when it holds neither. */
export function parseJudgeResult(body: unknown): JudgeResult | null {
  const b = body as {
    model?: unknown;
    choices?: { message?: { content?: unknown; tool_calls?: { function?: { name?: unknown; arguments?: unknown } }[] } }[];
    ratchet?: { served_by?: unknown; model?: unknown; route?: unknown; cost_usd?: unknown };
  } | null;
  const msg = b?.choices?.[0]?.message;
  let text = typeof msg?.content === "string" ? msg.content.trim() : "";
  const call = msg?.tool_calls?.[0]?.function;
  if (!text && call && typeof call.name === "string") text = `${call.name}(${typeof call.arguments === "string" ? call.arguments : ""})`;
  if (!text) return null;
  const by = b?.ratchet?.served_by;
  const cost = b?.ratchet?.cost_usd;
  return {
    text,
    servedBy: by === "student" || by === "teacher" ? by : null,
    model: typeof b?.model === "string" && b.model ? b.model : null,
    route: typeof b?.ratchet?.route === "string" && b.ratchet.route ? b.ratchet.route : null,
    costUsd: typeof cost === "number" && Number.isFinite(cost) && cost >= 0 ? cost : null,
  };
}

/** The cost as the page words it: "cost $0.00031", or "cost not reported" when the reply carried none. */
export function costLine(costUsd: number | null): string {
  if (costUsd == null) return "cost not reported";
  const d = costUsd >= 0.01 ? 3 : 5;
  return `cost $${costUsd.toFixed(d)}`;
}
