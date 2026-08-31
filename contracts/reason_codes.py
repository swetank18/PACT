"""
The frozen reason code enum.

Lane B asserts on `reason_code`, never on the human string. Lane C colours on
the code, never on the string. So the code is the contract and the string is
presentation — changing a string is free, adding or renaming a code is not.

`scripts/gen_ts_contracts.py` generates `contracts/reason_codes.ts` from this
file so the two languages cannot drift.
"""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    STEP_UP = "STEP_UP"


class ReasonCode(StrEnum):
    OK = "OK"

    # signature and identity
    REQUEST_SIG_INVALID = "REQUEST_SIG_INVALID"
    MANDATE_SIG_INVALID = "MANDATE_SIG_INVALID"

    # mandate state
    MANDATE_NOT_FOUND = "MANDATE_NOT_FOUND"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID"

    # freshness and replay
    REQUEST_STALE = "REQUEST_STALE"
    NONCE_REPLAY = "NONCE_REPLAY"

    # ceilings
    CEILING_PER_TXN = "CEILING_PER_TXN"
    CEILING_TOTAL = "CEILING_TOTAL"
    CEILING_COUNT = "CEILING_COUNT"

    # scope
    SCOPE_MERCHANT_NOT_ALLOWED = "SCOPE_MERCHANT_NOT_ALLOWED"
    SCOPE_CATEGORY_MISMATCH = "SCOPE_CATEGORY_MISMATCH"

    # intent
    INTENT_MISMATCH = "INTENT_MISMATCH"
    INTENT_INJECTION_SUSPECTED = "INTENT_INJECTION_SUSPECTED"
    AUDITOR_UNAVAILABLE = "AUDITOR_UNAVAILABLE"

    # quote binding
    QUOTE_EXPIRED = "QUOTE_EXPIRED"
    QUOTE_AMOUNT_MISMATCH = "QUOTE_AMOUNT_MISMATCH"

    # settlement and fulfilment
    STOCK_UNAVAILABLE = "STOCK_UNAVAILABLE"
    RAIL_CAPTURE_FAILED = "RAIL_CAPTURE_FAILED"
    SAGA_ROLLED_BACK = "SAGA_ROLLED_BACK"

    # settlement token
    #: The gate did not answer. Distinct from TOKEN_INVALID on purpose: a
    #: forged token and an unreachable gate both refuse the order, and they are
    #: completely different incidents. Reporting the first when the second
    #: happened sends whoever reads the audit trail hunting an attacker.
    GATE_UNAVAILABLE = "GATE_UNAVAILABLE"
    TOKEN_INVALID = "TOKEN_INVALID"
    TOKEN_ALREADY_USED = "TOKEN_ALREADY_USED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"


#: The nine checks, in the order they run: cheapest and most certain first,
#: short circuiting. 8b (quote binding) sits between ceiling and intent.
#: Never reorder. The order is the design, and Lane C renders it verbatim.
CHECK_ORDER: tuple[str, ...] = (
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
)


#: Which verdict a code implies when it is the reason a check failed.
#: Anything not listed blocks. Fail closed is the default, not a special case.
STEP_UP_CODES: frozenset[ReasonCode] = frozenset(
    {
        # The auditor is probabilistic. A probabilistic signal must never block
        # a legitimate sale outright — it asks the human instead. This is the
        # difference between arm C losing the sale and arm D recovering it.
        ReasonCode.INTENT_MISMATCH,
        ReasonCode.AUDITOR_UNAVAILABLE,
    }
)


def verdict_for(code: ReasonCode) -> Verdict:
    if code is ReasonCode.OK:
        return Verdict.ALLOW
    if code in STEP_UP_CODES:
        return Verdict.STEP_UP
    return Verdict.BLOCK


#: Human strings live here so there is exactly one place to reword them, but
#: nothing in the engine branches on them.
REASON_TEXT: dict[ReasonCode, str] = {
    ReasonCode.OK: "Approved",
    ReasonCode.REQUEST_SIG_INVALID: "The request was not signed by the agent this mandate names",
    ReasonCode.MANDATE_SIG_INVALID: "The mandate signature does not match the delegator's key",
    ReasonCode.MANDATE_NOT_FOUND: "No mandate with that id",
    ReasonCode.MANDATE_REVOKED: "This mandate was revoked",
    ReasonCode.MANDATE_EXPIRED: "This mandate has expired",
    ReasonCode.MANDATE_NOT_YET_VALID: "This mandate is not valid yet",
    ReasonCode.REQUEST_STALE: "The request is older than the 60 second freshness window",
    ReasonCode.NONCE_REPLAY: "This exact request was already used once",
    ReasonCode.CEILING_PER_TXN: "Over the per transaction limit",
    ReasonCode.CEILING_TOTAL: "Over the total budget for this mandate",
    ReasonCode.CEILING_COUNT: "No purchases left on this mandate",
    ReasonCode.SCOPE_MERCHANT_NOT_ALLOWED: "This merchant is not on the allowlist",
    ReasonCode.SCOPE_CATEGORY_MISMATCH: "This category is not on the allowlist",
    ReasonCode.INTENT_MISMATCH: "The purchase does not match the stated goal",
    ReasonCode.INTENT_INJECTION_SUSPECTED: "The page text tried to instruct the agent",
    ReasonCode.AUDITOR_UNAVAILABLE: "The intent auditor did not answer, so this needs you",
    ReasonCode.QUOTE_EXPIRED: "That quote has expired",
    ReasonCode.QUOTE_AMOUNT_MISMATCH: "The payment amount does not match the quote",
    ReasonCode.STOCK_UNAVAILABLE: "Out of stock",
    ReasonCode.RAIL_CAPTURE_FAILED: "The payment could not be captured",
    ReasonCode.SAGA_ROLLED_BACK: "The order was rolled back and the money returned",
    ReasonCode.GATE_UNAVAILABLE: "The gate did not answer, so the order was not placed",
    ReasonCode.TOKEN_INVALID: "That settlement token is not valid",
    ReasonCode.TOKEN_ALREADY_USED: "That settlement token was already spent",
    ReasonCode.TOKEN_EXPIRED: "That settlement token has expired",
}
