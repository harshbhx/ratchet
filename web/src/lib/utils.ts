import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** $ with enough decimals to show small per-request costs (e.g. $0.00003). */
export function fmtUsd(v: number | null | undefined, digits?: number): string {
  if (v == null || Number.isNaN(v)) return "–";
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 1 ? 2 : Math.abs(v) >= 0.01 ? 3 : 5);
  return "$" + v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "–";
  return (v * 100).toFixed(digits) + "%";
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "–";
  return Math.round(v).toLocaleString("en-US");
}

export function fmtMs(v: number | null | undefined): string {
  if (v == null) return "–";
  return v >= 1000 ? (v / 1000).toFixed(1) + " s" : Math.round(v) + " ms";
}

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Only http(s) URLs are ever rendered as links (results come from the open web). */
export function safeHref(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : undefined;
  } catch {
    return undefined;
  }
}

export const REPO_URL = "https://github.com/harshbhx/ratchet";
export const DOCS_URL = "https://docs.ratchet.dualmindlab.tech";
