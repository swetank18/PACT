/**
 * Everything the firewall screens compute rather than fetch.
 *
 * All of it is a pure function of data the gate already returns — the mandate
 * the device signed, the headroom envelope, and the decision list. Nothing in
 * here invents a number the engine did not produce, which is the rule that
 * makes the health dot and the threat card safe to put in front of someone.
 */
import type { Decision, Headroom, Paise } from "../../lib/contracts";
import type { StoredAgent, StoredMandate } from "./state";

export type Band = "green" | "amber" | "red";

/* -------------------------------------------------------------- health ---- */

export type HealthSignal = {
  label: string;
  detail: string;
  band: Band;
  weight: number;
};

export type Health = {
  score: number;
  band: Band;
  word: "HEALTHY" | "WATCH" | "CRITICAL";
  signals: HealthSignal[];
};

/**
 * Four signals, weighted, each banded before it is scored.
 *
 * The bands are the contract (a mandate 86% spent is red however little time
 * has passed); the score exists so a table of twenty mandates can be sorted.
 * Green scores 100, amber 40, red 0.
 */
const POINTS: Record<Band, number> = { green: 100, amber: 40, red: 0 };

function band(value: number, amberAt: number, redAt: number): Band {
  return value > redAt ? "red" : value > amberAt ? "amber" : "green";
}

export function healthFor(
  sm: StoredMandate,
  hr: Headroom | undefined,
  blocks: number,
  now = Date.now(),
): Health {
  const c = sm.mandate.constraints;

  // Spend and count come from the envelope, which is the ledger's own answer.
  // Without it we say so rather than guessing at zero.
  const spent = hr ? Math.max(0, c.max_total_paise - hr.headroom_paise) : 0;
  const usedFrac = c.max_total_paise > 0 ? spent / c.max_total_paise : 0;
  const countUsed = hr ? Math.max(0, c.max_count - hr.payments_remaining) : 0;
  const countFrac = c.max_count > 0 ? countUsed / c.max_count : 0;

  const from = Date.parse(c.valid_from);
  const to = Date.parse(c.valid_until);
  const span = to - from;
  const leftFrac = span > 0 ? Math.min(1, Math.max(0, (to - now) / span)) : 0;

  const signals: HealthSignal[] = [
    {
      label: "Budget",
      detail: hr
        ? `${Math.round(usedFrac * 100)}% used`
        : "not reported — the gate did not answer",
      band: band(usedFrac, 0.5, 0.85),
      weight: 0.4,
    },
    {
      label: "Time",
      detail: `${Math.round(leftFrac * 100)}% of the window left`,
      band: band(1 - leftFrac, 0.5, 0.9),
      weight: 0.25,
    },
    {
      label: "Payments",
      detail: hr ? `${countUsed} of ${c.max_count} used` : "not reported",
      band: band(countFrac, 0.5, 0.85),
      weight: 0.2,
    },
    {
      label: "Blocked attempts",
      detail: `${blocks}`,
      band: blocks >= 3 ? "red" : blocks >= 1 ? "amber" : "green",
      weight: 0.15,
    },
  ];

  const score = Math.round(signals.reduce((n, s) => n + s.weight * POINTS[s.band], 0));
  const b: Band = score >= 80 ? "green" : score >= 45 ? "amber" : "red";
  return {
    score,
    band: b,
    word: b === "green" ? "HEALTHY" : b === "amber" ? "WATCH" : "CRITICAL",
    signals,
  };
}

/* -------------------------------------------------------------- status ---- */

export type MandateStatus = "Active" | "Expired" | "Revoked" | "Paused" | "Spent";

export function statusOf(sm: StoredMandate, hr: Headroom | undefined, now = Date.now()): MandateStatus {
  if (sm.revoked_at) return sm.revoked_reason === "kill_switch" ? "Paused" : "Revoked";
  if (Date.parse(sm.mandate.constraints.valid_until) <= now) return "Expired";
  if (hr && hr.headroom_paise === 0) return "Spent";
  if (hr && hr.payments_remaining === 0) return "Spent";
  return "Active";
}

export const isLive = (s: MandateStatus) => s === "Active";

/**
 * The envelope to read a revoked mandate's numbers from.
 *
 * A revoked mandate reports zero headroom, deliberately — the merchant's
 * question is "what may I offer" and the truthful answer is "nothing". Read
 * literally that makes a paused mandate look fully spent, which is a different
 * and false claim. So for one the kill switch paused, the remainder captured
 * before the revoke is the honest source; for one revoked outright there is no
 * honest number and the caller is given nothing rather than a zero.
 */
export function effectiveHeadroom(
  sm: StoredMandate,
  hr: Headroom | undefined,
): Headroom | undefined {
  if (!sm.revoked_at) return hr;
  if (!sm.remainder || !hr) return undefined;
  return {
    ...hr,
    headroom_paise: sm.remainder.headroom_paise,
    payments_remaining: sm.remainder.payments_remaining,
    max_per_txn_paise: sm.remainder.max_per_txn_paise,
  };
}

/* ------------------------------------------------------------- threats ---- */

export type ThreatSummary = {
  prevented_paise: Paise;
  injections: number;
  replays: number;
  blocks: number;
  step_ups: number;
  top_payee: { vpa: string; n: number } | null;
  /** The two hour bucket with the most blocks in it, local time. */
  window: { from: number; to: number; n: number } | null;
};

const INJECTION = "INTENT_INJECTION_SUSPECTED";
const REPLAY = "NONCE_REPLAY";

export function threatSummary(decisions: Decision[], days = 7, now = Date.now()): ThreatSummary {
  const since = now - days * 86400_000;
  const recent = decisions.filter((d) => {
    const t = Date.parse(d.at);
    return Number.isNaN(t) ? true : t >= since;
  });

  const blocked = recent.filter((d) => d.verdict === "BLOCK");
  const payees = new Map<string, number>();
  const hours = new Array<number>(12).fill(0);

  for (const d of blocked) {
    payees.set(d.payee_vpa, (payees.get(d.payee_vpa) ?? 0) + 1);
    const t = Date.parse(d.at);
    if (!Number.isNaN(t)) hours[Math.floor(new Date(t).getHours() / 2)] += 1;
  }

  let top: { vpa: string; n: number } | null = null;
  for (const [vpa, n] of payees) if (!top || n > top.n) top = { vpa, n };

  let bucket = -1;
  for (let i = 0; i < hours.length; i++) if (bucket === -1 || hours[i] > hours[bucket]) bucket = i;

  return {
    prevented_paise: blocked.reduce((n, d) => n + d.amount_paise, 0),
    injections: blocked.filter((d) => d.reason_code === INJECTION).length,
    replays: blocked.filter((d) => d.reason_code === REPLAY).length,
    blocks: blocked.length,
    step_ups: recent.filter((d) => d.verdict === "STEP_UP").length,
    top_payee: top,
    window: bucket >= 0 && hours[bucket] > 0 ? { from: bucket * 2, to: bucket * 2 + 2, n: hours[bucket] } : null,
  };
}

/* -------------------------------------------------------------- agents ---- */

/**
 * Which agent a decision belongs to.
 *
 * A decision names a mandate, not an agent, so the answer only exists for
 * mandates this device signed. For anything else the mandate id is shown
 * instead of a guessed name — the alternative is attributing a payment to an
 * agent that may not have made it.
 */
export function agentForMandate(
  mandateId: string,
  mandates: StoredMandate[],
  agents: StoredAgent[],
): StoredAgent | null {
  const sm = mandates.find((m) => m.mandate.mandate_id === mandateId);
  if (!sm) return null;
  return agents.find((a) => a.agent_id === sm.mandate.delegate.agent_id) ?? null;
}

/** Stable colour per agent id, so the same agent is the same square everywhere. */
export function agentColour(agentId: string): string {
  let h = 0;
  for (let i = 0; i < agentId.length; i++) h = (h * 31 + agentId.charCodeAt(i)) >>> 0;
  const palette = ["#6366f1", "#3b82f6", "#0ea5e9", "#8b5cf6", "#ec4899", "#14b8a6"];
  return palette[h % palette.length];
}

export function initialsOf(name: string): string {
  const parts = name.split(/[\s_-]+/).filter(Boolean);
  if (parts.length === 0) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/* ------------------------------------------------------------- buckets ---- */

/** Decisions per day, split by verdict, oldest first. For the activity chart. */
export function dailyCounts(
  decisions: Decision[],
  days: number,
  now = Date.now(),
): Array<{ day: string; allowed: number; blocked: number }> {
  const out: Array<{ day: string; allowed: number; blocked: number }> = [];
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);

  const index = new Map<string, { allowed: number; blocked: number }>();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(start.getTime() - i * 86400_000);
    const key = d.toISOString().slice(0, 10);
    const cell = { allowed: 0, blocked: 0 };
    index.set(key, cell);
    out.push({ day: key, ...cell });
  }

  for (const d of decisions) {
    const key = new Date(Date.parse(d.at)).toISOString().slice(0, 10);
    const row = out.find((r) => r.day === key);
    if (!row) continue;
    if (d.verdict === "ALLOW") row.allowed += 1;
    else if (d.verdict === "BLOCK") row.blocked += 1;
  }
  return out;
}

/** p50 / p95 over the gate's own elapsed_ms, which is the number it reports. */
export function latencyPercentiles(decisions: Decision[]): { p50: number; p95: number; n: number } {
  const xs = decisions
    .map((d) => d.elapsed_ms)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
    .sort((a, b) => a - b);
  if (xs.length === 0) return { p50: NaN, p95: NaN, n: 0 };
  const at = (q: number) => xs[Math.min(xs.length - 1, Math.floor(q * xs.length))];
  return { p50: at(0.5), p95: at(0.95), n: xs.length };
}
