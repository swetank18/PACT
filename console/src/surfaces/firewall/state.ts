/**
 * What the principal's device knows.
 *
 * The gate is the authority on every number that matters — what is spent, what
 * is left, what is revoked. This module holds the half the gate cannot know:
 * which mandates *this device* signed, what the human named their agents, and
 * their preferences. It lives in localStorage for the same reason the device
 * key does, and with the same caveat: localStorage is not a secure enclave.
 *
 * Nothing here is a security control. Anything the UI enforces from this file
 * is a convenience; anything that has to hold against a compromised agent is
 * enforced by the gate. The kill switch is the clearest case — it does not set
 * a flag here and call the agents paused, it revokes the mandates server side.
 */
import type { Mandate, Paise } from "../../lib/contracts";

/* ------------------------------------------------------------ storage ----- */

const NS = "pact.firewall.";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(NS + key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    // Private window, blocked site data, or a value written by an older
    // version that no longer parses. An empty firewall is recoverable; a
    // throw at module load is not.
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(NS + key, JSON.stringify(value));
  } catch {
    /* ephemeral is fine */
  }
}

/* -------------------------------------------------------------- types ----- */

/**
 * The authority that was left on a mandate at the instant the kill switch
 * revoked it. Captured *before* the revoke, because a revoked mandate reports
 * zero headroom — indistinguishable from a fully spent one — so this is the
 * only moment the remainder can be read.
 */
export type Remainder = {
  headroom_paise: Paise;
  payments_remaining: number;
  max_per_txn_paise: Paise;
};

export type StoredMandate = {
  mandate: Mandate;
  signature: string;
  created_at: string;
  /** Set when this device asked the gate to revoke it, and why. */
  revoked_at?: string | null;
  revoked_reason?: "user" | "kill_switch" | "agent_revoked" | null;
  /** Only on a kill-switch revoke, so resume can re-issue what was left. */
  remainder?: Remainder | null;
  /** The mandate this one was re-issued from after a pause. */
  replaces?: string | null;
  template_id?: string | null;
};

export type StoredAgent = {
  agent_id: string;
  name: string;
  /** The device public key the mandates delegate to. */
  pubkey: string;
  status: "active" | "paused" | "revoked";
  connected_at: string;
};

export type Prefs = {
  display_name: string;
  vpa: string;
  notify: {
    blocked: boolean;
    step_up: boolean;
    expiry: boolean;
    weekly: boolean;
    every_allow: boolean;
  };
  /**
   * Below this, a step-up is answered without asking. It is applied by this
   * screen only — the gate does not know about it — so it is described that
   * way on the settings page rather than implied to be a server-side rule.
   */
  auto_approve_paise: Paise;
  sound_on_block: boolean;
};

export type KillState = {
  engaged: boolean;
  at: string | null;
  /** Mandate ids the switch revoked, so resume knows what to re-issue. */
  paused: string[];
};

export const DEFAULT_PREFS: Prefs = {
  display_name: "Swetank",
  vpa: "swetank@okaxis",
  notify: { blocked: true, step_up: true, expiry: true, weekly: true, every_allow: false },
  auto_approve_paise: 10000,
  sound_on_block: false,
};

export const NO_KILL: KillState = { engaged: false, at: null, paused: [] };

/* ---------------------------------------------------------- templates ----- */

export type Template = {
  id: string;
  icon: string;
  name: string;
  intent: string;
  categories: string[];
  merchants: string[];
  per_txn_paise: Paise;
  total_paise: Paise;
  count: number;
  hours: number;
};

/**
 * Categories and merchant VPAs are the ones this merchant actually publishes
 * in its manifest. A template offering a category the catalog does not carry
 * produces a mandate that can never be used.
 */
export const TEMPLATES: Template[] = [
  {
    id: "restock",
    icon: "📦",
    name: "Monthly restock",
    intent: "restock office supplies for the month",
    categories: ["stationery", "office_furniture", "cables"],
    merchants: ["deskkit@razorpay"],
    per_txn_paise: 200000,
    total_paise: 800000,
    count: 10,
    hours: 720,
  },
  {
    id: "one_off",
    icon: "🛍️",
    name: "One-time purchase",
    intent: "buy a desk lamp",
    categories: ["office_furniture"],
    merchants: ["deskkit@razorpay"],
    per_txn_paise: 500000,
    total_paise: 500000,
    count: 1,
    hours: 24,
  },
  {
    id: "consumables",
    icon: "🔄",
    name: "Recurring consumables",
    intent: "replace printer consumables when they run low",
    categories: ["printers"],
    merchants: ["deskkit@razorpay", "officebasket@okhdfc"],
    per_txn_paise: 100000,
    total_paise: 300000,
    count: 3,
    hours: 720,
  },
  {
    id: "kit_out",
    icon: "🖥️",
    name: "Kit out a desk",
    intent: "kit out a new desk for a joiner",
    categories: ["office_furniture", "cables", "storage"],
    merchants: ["deskkit@razorpay"],
    per_txn_paise: 1500000,
    total_paise: 2500000,
    count: 3,
    hours: 168,
  },
];

/* ------------------------------------------------------------- slices ----- */

export const store = {
  mandates: {
    load: () => read<StoredMandate[]>("mandates.v1", []),
    save: (v: StoredMandate[]) => write("mandates.v1", v),
  },
  agents: {
    load: () => read<StoredAgent[]>("agents.v1", []),
    save: (v: StoredAgent[]) => write("agents.v1", v),
  },
  prefs: {
    load: (): Prefs => {
      const v = read<Partial<Prefs>>("prefs.v1", {});
      return { ...DEFAULT_PREFS, ...v, notify: { ...DEFAULT_PREFS.notify, ...v.notify } };
    },
    save: (v: Prefs) => write("prefs.v1", v),
  },
  kill: {
    load: (): KillState => ({ ...NO_KILL, ...read<Partial<KillState>>("kill.v1", {}) }),
    save: (v: KillState) => write("kill.v1", v),
  },
  templateUse: {
    load: () => read<Record<string, number>>("template_use.v1", {}),
    save: (v: Record<string, number>) => write("template_use.v1", v),
  },
  dismissed: {
    load: () => read<string[]>("dismissed.v1", []),
    save: (v: string[]) => write("dismissed.v1", v),
  },
};

/** The agent every part of this system delegates to unless told otherwise. */
export const DEFAULT_AGENT_ID = "buyer_agent_v1";

export function seedAgent(pubkey: string): StoredAgent {
  return {
    agent_id: DEFAULT_AGENT_ID,
    name: "BuyerBot v1",
    pubkey,
    status: "active",
    connected_at: new Date().toISOString(),
  };
}
