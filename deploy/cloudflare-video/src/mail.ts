// The video link's emails as plain text: one per human visitor per UTC day (capped) and a daily summary. No imports, so the tests
// run it under Node. The emails carry the city and country, the network, a coarse device (for example "Windows desktop, Chrome"),
// the source, the time and counts of unique humans; never an IP address, a user agent or a cookie.

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
  city: string;
  country: string;
  /** "Reliance Jio (AS55836)". */
  network: string;
  /** "Windows desktop, Chrome". */
  device: string;
  at: number;
  /** Different human visitors counted so far on this UTC day, this one included. */
  unique: number;
  /** The UTC day, "2026-10-26". */
  day: string;
  /** This email's number on the day, and the most there will be. */
  slot: number;
  cap: number;
}

export function visitEmail(v: Visit): Mail {
  const place = [v.city, v.country].filter(Boolean).join(", ");
  const lines = [
    "Someone opened the Ratchet video link.",
    `Where: ${place}`,
    `Network: ${v.network}`,
    `Device: ${v.device}`,
    `Source: ${v.source}`,
    `Time (IST): ${istTime(v.at)}`,
    `Unique humans today: ${v.unique} (UTC day ${v.day}).`,
  ];
  if (v.slot >= v.cap) {
    lines.push(
      "",
      `This is visit email ${v.slot} of ${v.cap} for this UTC day, the daily cap. No more visit emails today; the 09:00 IST summary counts every click.`,
    );
  }
  lines.push("", "Kept 30 days: country, city, network, coarse device and a daily-changing visitor hash. Never the IP address, user agent or a cookie.");
  return { subject: `Ratchet video opened (${place}, ${v.source})`, text: lines.join("\n") + "\n" };
}

export interface Summary {
  day: string;
  /** Clicks per source on that day. */
  sources: { source: string; human: number; bot: number }[];
  /** Bot visits on that day by reason ("" is a row from before reasons were kept). */
  reasons: { reason: string; n: number }[];
  /** Different human visitors on that day. */
  unique: number;
  /** Networks of human and of bot visits on that day, most first. */
  humanNetworks: { network: string; n: number }[];
  botNetworks: { network: string; n: number }[];
  /** Human clicks per country on that day, from the events table. */
  countries: { country: string; n: number }[];
  /** Visit emails sent on that day. */
  emails: number;
  cap: number;
}

const top5 = (rows: { network: string; n: number }[]): string[] =>
  rows.length ? rows.slice(0, 5).map((r, i) => `  ${i + 1}. ${r.network}: ${r.n}`) : ["  none"];

export function summaryEmail(s: Summary): Mail {
  const human = s.sources.reduce((a, r) => a + r.human, 0);
  const bot = s.sources.reduce((a, r) => a + r.bot, 0);
  const rows = [...s.sources].sort((a, b) => b.human - a.human || b.bot - a.bot || a.source.localeCompare(b.source));
  const reasons = [...s.reasons].sort((a, b) => b.n - a.n || a.reason.localeCompare(b.reason));
  const lines = [
    `The Ratchet video link on ${s.day} (UTC day).`,
    `Humans: ${human} ${human === 1 ? "click" : "clicks"}, ${s.unique} unique`,
    `Bots: ${bot}`,
    "",
    "Bots by reason:",
    ...(reasons.length ? reasons.map((r) => `  ${r.reason || "not recorded"}: ${r.n}`) : ["  none"]),
    "",
    "Top 5 networks, humans:",
    ...top5(s.humanNetworks),
    "",
    "Top 5 networks, bots:",
    ...top5(s.botNetworks),
    "",
    "By source (human / bots):",
    ...rows.map((r) => `  ${r.source}: ${r.human} / ${r.bot}`),
    "",
    `Countries (human clicks): ${s.countries.length ? s.countries.map((c) => `${c.country} ${c.n}`).join(", ") : "none"}`,
    `Visit emails sent: ${s.emails} of ${s.cap}.`,
  ];
  return {
    subject: `Ratchet video link, ${s.day} (UTC): ${s.unique} unique ${s.unique === 1 ? "human" : "humans"}, ${bot} bot ${bot === 1 ? "visit" : "visits"}`,
    text: lines.join("\n") + "\n",
  };
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
