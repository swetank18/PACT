/**
 * Money formatting. Integer paise in, Indian-grouped rupees out.
 *
 * The console never does arithmetic that reaches a payload — that is the quote
 * engine's job and saying so is part of the pitch. What is here is division by
 * 100 for display and nothing else.
 */
import type { Paise } from "./contracts";

const GROUPER = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

const GROUPER_2DP = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** `₹ 1,24,500`. Paise are dropped when the amount is whole rupees. */
export function inr(paise: Paise | null | undefined): string {
  if (paise == null || Number.isNaN(paise)) return "—";
  const rupees = paise / 100;
  const body = paise % 100 === 0 ? GROUPER.format(rupees) : GROUPER_2DP.format(rupees);
  return `₹${paise < 0 ? "" : ""}${body}`;
}

/** Without the symbol, for tiles that carry their own ₹ at a different size. */
export function inrPlain(paise: Paise | null | undefined): string {
  if (paise == null || Number.isNaN(paise)) return "—";
  const rupees = paise / 100;
  return paise % 100 === 0 ? GROUPER.format(rupees) : GROUPER_2DP.format(rupees);
}

/** Rupees typed by a human into the grant form, back to integer paise. */
export function rupeesToPaise(rupees: string | number): Paise {
  const n = typeof rupees === "number" ? rupees : Number.parseFloat(rupees.replace(/[^0-9.]/g, ""));
  if (!Number.isFinite(n)) return 0;
  // Round rather than truncate so 1234.565 does not silently lose a paisa.
  return Math.round(n * 100);
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Latency, always with a unit, always monospace at the call site. */
export function ms(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v < 10 ? `${v.toFixed(1)} ms` : `${Math.round(v)} ms`;
}

/** `14:22:05` from an RFC 3339 UTC timestamp, in the viewer's local zone. */
export function clock(rfc3339: string | null | undefined): string {
  if (!rfc3339) return "—";
  const d = new Date(rfc3339);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export function nowRfc3339(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** `in 4m 12s` / `expired`, for quote TTLs. */
export function until(rfc3339: string | null | undefined, from = Date.now()): string {
  if (!rfc3339) return "—";
  const delta = new Date(rfc3339).getTime() - from;
  if (Number.isNaN(delta)) return "—";
  if (delta <= 0) return "expired";
  const s = Math.floor(delta / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d`;
}

/** Long ids are unreadable on a projector. Keep the prefix and the tail. */
export function shortId(id: string | null | undefined, tail = 6): string {
  if (!id) return "—";
  const us = id.indexOf("_");
  if (us > 0 && id.length > us + 1 + tail + 2) {
    return `${id.slice(0, us + 1)}…${id.slice(-tail)}`;
  }
  return id;
}

/**
 * `2 min ago`. The feed and every table read at a glance, so relative time is
 * the default and the exact stamp lives in a title attribute beside it.
 */
export function ago(rfc3339: string | null | undefined, from = Date.now()): string {
  if (!rfc3339) return "—";
  const t = new Date(rfc3339).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.round((from - t) / 1000);
  if (s < 0) return until(rfc3339, from);
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d < 30 ? `${d}d ago` : new Date(t).toLocaleDateString("en-IN");
}

/** The full local timestamp. Goes behind every relative one as a tooltip. */
export function stamp(rfc3339: string | null | undefined): string {
  if (!rfc3339) return "";
  const d = new Date(rfc3339);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

/** `1:42`, for a countdown that has to be read in peripheral vision. */
export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
