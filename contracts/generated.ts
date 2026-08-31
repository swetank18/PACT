/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Produced by `scripts/gen_ts_contracts.py` from `contracts/reason_codes.py`.
 * Run that script after changing a reason code, the check order, or a saga
 * state. `tests/test_invariants.py` asserts this file is current, so a stale
 * copy fails the Python suite rather than shipping a console that renders a
 * code it does not recognise.
 *
 * Human-facing strings are generated too, but the console is free to override
 * them — wording on screen is a presentation decision. What must not drift is
 * the set of codes and the order of the chain.
 */


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
  "RAIL_CAPTURE_FAILED",
  "SAGA_ROLLED_BACK",
  "TOKEN_INVALID",
  "TOKEN_ALREADY_USED",
  "TOKEN_EXPIRED",
] as const;

export type ReasonCode = (typeof REASON_CODES)[number];

/** Plain language. The console may override any of these. */
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
  RAIL_CAPTURE_FAILED: "The payment could not be captured",
  SAGA_ROLLED_BACK: "The order was rolled back and the money returned",
  TOKEN_INVALID: "That settlement token is not valid",
  TOKEN_ALREADY_USED: "That settlement token was already spent",
  TOKEN_EXPIRED: "That settlement token has expired",
};

/** Codes that ask a human rather than refusing outright. */
export const STEP_UP_CODES: readonly ReasonCode[] = [
  "AUDITOR_UNAVAILABLE",
  "INTENT_MISMATCH",
];

/** The frozen order. Never reorder, never truncate on screen. */
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

export const SAGA_STATES = [
  "QUOTED",
  "RESERVED_STOCK",
  "GATE_ALLOWED",
  "PAYMENT_CAPTURED",
  "FULFILLED",
  "FULFILMENT",
  "ROLLING_BACK",
  "REFUND_ISSUED",
  "BUDGET_RELEASED",
  "ROLLED_BACK",
  "ALTERNATIVE_OFFERED",
  "RECOVERED",
  "NEEDS_ATTENTION",
] as const;

export type SagaState = (typeof SAGA_STATES)[number];
