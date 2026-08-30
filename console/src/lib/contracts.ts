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

export const REASON_CODES = [
  "OK",
  "REQUEST_SIG_INVALID",
  "MANDATE_SIG_INVALID",
  "MANDATE_NOT_FOUND",
  "MANDATE_REVOKED",
  "MANDATE_EXPIRED",
  "MANDATE_NOT_YET_VALID",
  "REQUEST_STALE",
  "NONCE_REPLAY",
  "CEILING_PER_TXN",
  "CEILING_TOTAL",
  "CEILING_COUNT",
  "SCOPE_MERCHANT_NOT_ALLOWED",
  "SCOPE_CATEGORY_MISMATCH",
  "INTENT_MISMATCH",
  "INTENT_INJECTION_SUSPECTED",
  "AUDITOR_UNAVAILABLE",
  "QUOTE_EXPIRED",
  "QUOTE_AMOUNT_MISMATCH",
  "STOCK_UNAVAILABLE",
  "RAZORPAY_CAPTURE_FAILED",
  "SAGA_ROLLED_BACK",
] as const;

export type ReasonCode = (typeof REASON_CODES)[number];

/**
 * Plain language, because "explainable" in the brief means a human reads it and
 * understands what happened. Written to be legible at ten metres, so no jargon
 * and no code names.
 */
export const REASON_TEXT: Record<ReasonCode, string> = {
  OK: "Approved",
  REQUEST_SIG_INVALID: "The request was not signed by the agent this mandate names",
  MANDATE_SIG_INVALID: "The mandate signature does not match the delegator's key",
  MANDATE_NOT_FOUND: "No mandate with that id",
  MANDATE_REVOKED: "This mandate was revoked",
  MANDATE_EXPIRED: "This mandate has expired",
  MANDATE_NOT_YET_VALID: "This mandate is not valid yet",
  REQUEST_STALE: "The request is older than the 60 second freshness window",
  NONCE_REPLAY: "This exact request was already used once",
  CEILING_PER_TXN: "Over the per transaction limit",
  CEILING_TOTAL: "Over the total budget for this mandate",
  CEILING_COUNT: "No purchases left on this mandate",
  SCOPE_MERCHANT_NOT_ALLOWED: "This merchant is not on the allowlist",
  SCOPE_CATEGORY_MISMATCH: "This category is not on the allowlist",
  INTENT_MISMATCH: "The purchase does not match the stated goal",
  INTENT_INJECTION_SUSPECTED: "The page text tried to instruct the agent",
  AUDITOR_UNAVAILABLE: "The intent auditor did not answer, so this needs you",
  QUOTE_EXPIRED: "That quote has expired",
  QUOTE_AMOUNT_MISMATCH: "The payment amount does not match the quote",
  STOCK_UNAVAILABLE: "Out of stock",
  RAZORPAY_CAPTURE_FAILED: "The payment could not be captured",
  SAGA_ROLLED_BACK: "The order was rolled back and the money returned",
};

export function reasonText(code: string | null | undefined): string {
  if (!code) return "";
  return REASON_TEXT[code as ReasonCode] ?? code;
}

/* -------------------------------------------------------------- gate ------ */

export type Verdict = "ALLOW" | "BLOCK" | "STEP_UP";

/** Fixed order. Section 9 of the shared contract. Never reorder on screen. */
export const CHECK_ORDER = [
  "request_signature",
  "mandate_signature",
  "mandate_state",
  "validity_window",
  "freshness",
  "replay",
  "scope",
  "ceiling",
  "quote_binding",
  "intent",
] as const;

export type CheckName = (typeof CHECK_ORDER)[number];

export const CHECK_LABEL: Record<CheckName, string> = {
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

export const SAGA_STATES = [
  "QUOTED",
  "RESERVED_STOCK",
  "GATE_ALLOWED",
  "PAYMENT_CAPTURED",
  "FULFILLED",
  "ROLLING_BACK",
  "REFUND_ISSUED",
  "BUDGET_RELEASED",
  "ROLLED_BACK",
  "ALTERNATIVE_OFFERED",
  "RECOVERED",
  "NEEDS_ATTENTION",
] as const;

export type SagaState = (typeof SAGA_STATES)[number];

export type SagaStep = {
  order_id: string;
  seq: number;
  state: SagaState | string;
  action: string;
  outcome: "OK" | "FAIL" | "PENDING" | string;
  detail: string;
  /** Razorpay id where one exists: pay_, rfnd_, order_. */
  ref?: string | null;
  at: string;
};

export type Order = {
  order_id: string;
  quote_id: string;
  mandate_id: string;
  state: SagaState | string;
  amount_paise: Paise;
  items_summary: string;
  razorpay_order_id?: string | null;
  razorpay_payment_id?: string | null;
  /** True when this order exists because an earlier one rolled back. */
  recovered_from?: string | null;
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
