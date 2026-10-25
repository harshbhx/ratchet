// The video link's emails as plain text: one per human visit (capped per UTC day) and a daily summary. No imports, so the tests
// run it under Node. The emails carry the source, the two-letter country and the time, never an IP address or a user agent.

const IST_OFFSET_MS = 330 * 60_000; // India Standard Time is UTC+5:30 all year (no daylight saving)

/** A time as India Standard Time, "2026-10-26 15:42", computed without the runtime's time-zone data. */
export function istTime(ms: number): string {
  return new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 16).replace("T", " ");
}

export interface Mail {
  subject: string;
  text: string;
}

export interface Visit {
  source: string;
  country: string;
  at: number;
  /** Human clicks counted so far on this UTC day, this one included. */
  today: number;
  /** The UTC day, "2026-10-26". */
  day: string;
  /** This email's number on the day, and the most there will be. */
  slot: number;
  cap: number;
}

export function visitEmail(v: Visit): Mail {
  const lines = [
    "Someone opened the Ratchet demo video link.",
    `Source: ${v.source}`,
    `Country: ${v.country}`,
    `Time (IST): ${istTime(v.at)}`,
    `Today so far: ${v.today} human ${v.today === 1 ? "click" : "clicks"} (UTC day ${v.day}).`,
  ];
  if (v.slot >= v.cap) {
    lines.push(
      "",
      `This is visit email ${v.slot} of ${v.cap} for this UTC day, the daily cap. No more visit emails today; the 09:00 IST summary counts every click.`,
    );
  }
  lines.push("", "No IP address, user agent or cookie is stored.");
  return { subject: `Ratchet video opened (${v.source}, ${v.country})`, text: lines.join("\n") + "\n" };
}

export interface Summary {
  day: string;
  /** Clicks per source on that day. */
  sources: { source: string; human: number; bot: number }[];
  /** Human clicks per country on that day, from the events table. */
  countries: { country: string; n: number }[];
  /** Visit emails sent on that day. */
  emails: number;
  cap: number;
}

export function summaryEmail(s: Summary): Mail {
  const human = s.sources.reduce((a, r) => a + r.human, 0);
  const bot = s.sources.reduce((a, r) => a + r.bot, 0);
  const rows = [...s.sources].sort((a, b) => b.human - a.human || b.bot - a.bot || a.source.localeCompare(b.source));
  const lines = [
    `The Ratchet demo video link on ${s.day} (UTC day).`,
    `Human clicks: ${human}`,
    `Link previews and bots: ${bot}`,
    "",
    "By source (human / previews and bots):",
    ...rows.map((r) => `  ${r.source}: ${r.human} / ${r.bot}`),
    "",
    `Countries (human clicks): ${s.countries.length ? s.countries.map((c) => `${c.country} ${c.n}`).join(", ") : "none"}`,
    `Visit emails sent: ${s.emails} of ${s.cap}.`,
  ];
  return { subject: `Ratchet video link, ${s.day} (UTC): ${human} human ${human === 1 ? "click" : "clicks"}`, text: lines.join("\n") + "\n" };
}

/** A header value on one line: a stray line break would start a new header. */
const oneLine = (v: string): string => v.replace(/[\r\n]+/g, " ").trim();

/** A plain address: something@domain, no spaces, angle brackets or line breaks. */
export function plainAddress(v: unknown): v is string {
  return typeof v === "string" && /^[^\s<>@,;"]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(v);
}

/** The raw RFC 5322 message the send_email binding takes: plain text, UTF-8, CRLF line endings. */
export function mime(m: Mail & { from: string; to: string; at: number; messageId: string }): string {
  const date = new Date(m.at).toUTCString().replace("GMT", "+0000");
  const headers = [
    `From: Ratchet video link <${oneLine(m.from)}>`,
    `To: ${oneLine(m.to)}`,
    `Subject: ${oneLine(m.subject)}`,
    `Date: ${date}`,
    `Message-ID: <${oneLine(m.messageId)}>`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
    "Content-Transfer-Encoding: 8bit",
  ];
  const body = m.text.replace(/\r?\n/g, "\r\n");
  return headers.join("\r\n") + "\r\n\r\n" + body;
}
