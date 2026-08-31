/**
 * Console-side mirror of the frozen contracts.
 *
 * Lane A owns `contracts/` and generates `reason_codes.ts` there. Until that
 * file exists this module is the console's copy of the shapes in
 * 00-SHARED-CONTRACTS. When Lane A publishes, re-export from their file and
 * delete the duplicated literals below; the plain-language strings stay here
 * because wording on screen is Lane C's call.
 *
 * Lane B asserts on `reason_code`, never on the human string. So does this UI:
 * every colour and icon decision keys off the code, and the string is display
 * only.
 */

/* ------------------------------------------------------------- money ------ */

/** Money is integer paise. Never floats, never rupees in a payload. */
export type Paise = number;

/* -------------------------------------------------------- reason codes ---- */

/**
 * The codes, the chain order and the saga states come from
 * `contracts/generated.ts`, which `scripts/gen_ts_contracts.py` produces from
 * the Python enum. They are not duplicated here, because a drift between the
 * two is not a compile error — it is a console rendering a code it does not
 * recognise, at the worst possible moment.
 */
export {
  CHECK_ORDER,
  REASON_CODES,
  SAGA_STATES,
  STEP_UP_CODES,
  type CheckName,
  type ReasonCode,
  type SagaState,
} from "../../../contracts/generated";

import {
  REASON_TEXT as GENERATED_TEXT,
  type CheckName,
  type ReasonCode,
  type ReasonCode as Code,
} from "../../../contracts/generated";

/**
 * Wording on screen is Lane C's call, so the generated strings are the default
 * and anything in this override wins. Keep overrides few: a message that says
 * something different from the engine's is a support problem.
 */
const TEXT_OVERRIDES: Partial<Record<Code, string>> = {
  AUDITOR_UNAVAILABLE: "We could not check this one automatically, so it needs you",
};

export const REASON_TEXT: Record<Code, string> = { ...GENERATED_TEXT, ...TEXT_OVERRIDES };

export function reasonText(code: string | null | undefined): string {
  if (!code) return "";
  return REASON_TEXT[code as Code] ?? code;
}

/* -------------------------------------------------------------- gate ------ */

export type Verdict = "ALLOW" | "BLOCK" | "STEP_UP";

export const CHECK_LABEL: Record<string, string> = {
  request_signature: "request signature",
  mandate_signature: "mandate signature",
  mandate_state: "mandate state",
  validity_window: "validity window",
  freshness: "freshness",
  replay: "replay",
  scope: "scope",
  ceiling: "ceiling",
  quote_binding: "quote binding",
  intent: "intent",
};

export type CheckResult = {
  name: CheckName | string;
  status: "PASS" | "FAIL" | "SKIPPED" | "STEP_UP";
  ms: number;
  detail?: string;
  /** Set by the intent auditor when it finds instruction-shaped text. */
  injected_span?: { text: string; start: number; end: number } | null;
};

export type Decision = {
  decision_id: string;
  mandate_id: string;
  verdict: Verdict;
  reason_code: ReasonCode | string;
  reason_detail?: string;
  payee_vpa: string;
  amount_paise: Paise;
  quote_id?: string | null;
  order_id?: string | null;
  elapsed_ms: number;
  checks: CheckResult[];
  /** Populated on an injection block so the console can highlight the span. */
  page_excerpt?: string | null;
  /**
   * Single use, present only on an ALLOW and only in the direct response. The
   * gate strips it from the broadcast stream — it is a bearer credential and
   * everyone watching the console is on that stream.
   */
  settlement_token?: string | null;
  at: string;
};

/* ---------------------------------------------------------- headroom ------ */

/**
 * Section 7. The growth feature.
 *
 * Note what is absent and keep it absent: the delegator's identity, the intent
 * text, the total budget, and the spend history. If a field ever shows up here
 * that the merchant should not see, that is a contract bug, not a UI decision.
 */
export type Headroom = {
  mandate_id: string;
  headroom_paise: Paise;
  max_per_txn_paise: Paise;
  payments_remaining: number;
  categories_allowed: string[];
  valid_until: string;
  merchant_in_scope: boolean;
  as_of: string;
  signature: string;
};

/* ------------------------------------------------------------- quote ------ */

export type LineItem = {
  sku: string;
  name: string;
  qty: number;
  unit_paise: Paise;
  line_total_paise: Paise;
  category: string;
};

export type Quote = {
  quote_id: string;
  items: LineItem[];
  subtotal_paise: Paise;
  tax_paise: Paise;
  shipping_paise: Paise;
  total_paise: Paise;
  expires_at: string;
  headroom_fit?: {
    fits: boolean;
    headroom_paise: Paise;
    headroom_after_paise: Paise;
    categories_ok: boolean;
  } | null;
};

export type Addon = {
  sku: string;
  name: string;
  category: string;
  price_paise: Paise;
  reason?: string;
};

/* -------------------------------------------------------------- saga ------ */

export type SagaStep = {
  order_id: string;
  seq: number;
  state: string;
  action: string;
  outcome: "OK" | "FAIL" | "PENDING" | string;
  detail: string;
  /**
   * Why a step failed, in the contract's vocabulary. `detail` beside it is
   * prose and may be reworded freely; this is the half that is safe to branch
   * on. Absent on steps that succeeded.
   */
  reason_code?: ReasonCode | null;
  /** The rail's id where one exists: pay_, rfnd_, order_. */
  ref?: string | null;
  at: string;
};

export type Order = {
  order_id: string;
  quote_id: string;
  mandate_id: string;
  state: string;
  /** Which settlement rail moved the money. The console only displays it. */
  rail: string;
  amount_paise: Paise;
  items_summary: string;
  rail_order_id?: string | null;
  rail_payment_id?: string | null;
  /** True when this order exists because an earlier one rolled back. */
  recovered_from?: string | null;
  /**
   * Set between ALTERNATIVE_OFFERED and the buyer's answer. A recovery the
   * merchant grants itself is not a recovery, so the saga stops here until
   * someone accepts.
   */
  alternative?: Addon | null;
  at: string;
};

/* ------------------------------------------------------------- stats ------ */

export type MerchantStats = {
  gmv_paise: Paise;
  orders: number;
  avg_order_value_paise: Paise;
  upsell_offers_made: number;
  upsell_offers_accepted: number;
  upsell_offers_filtered_by_headroom: number;
  upsell_attach_rate: number;
  recovered_paise: Paise;
  recovered_orders: number;
  needs_attention: number;
};

/* ------------------------------------------------------------ mandate ----- */

export type MandateConstraints = {
  max_per_txn_paise: Paise;
  max_total_paise: Paise;
  max_count: number;
  merchant_allowlist: string[];
  category_allowlist: string[];
  valid_from: string;
  valid_until: string;
};

export type Mandate = {
  v: 1;
  mandate_id: string;
  delegator: { vpa: string; pubkey: string };
  delegate: { agent_id: string; pubkey: string };
  intent: string;
  constraints: MandateConstraints;
  issued_at: string;
  signature?: string;
};

/* ---------------------------------------------------------- manifest ------ */

export type AgentCommerceManifest = {
  merchant: string;
  merchant_vpa: string;
  mcp_endpoint: string;
  categories: string[];
  currency: string;
  accepts_mandates: string[];
  headroom_endpoint: string;
  quote_ttl_seconds: number;
  return_window_days: number;
  rate_limit_per_minute: number;
};
