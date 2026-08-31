"""
The frozen wire shapes. Section 6, 7 and 8 of the shared contract.

Pydantic models rather than dataclasses so that every boundary — the gate's HTTP
API, the merchant's MCP tools, the webhook receiver — validates the same way,
and so `openapi.json` falls out of FastAPI for free.

Two rules this file enforces structurally rather than by convention:

  * Money is `int` paise. There is no float in any model here.
  * The headroom envelope cannot carry the delegator's identity, the intent
    text, the total budget or the spend history, because those fields do not
    exist on it. Privacy by construction means the schema physically cannot
    leak, not that we remembered to strip it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from contracts.money import Paise, parse_paise
from contracts.reason_codes import ReasonCode, Verdict


def utcnow() -> str:
    """RFC 3339 UTC with a Z, seconds precision. The only clock in the system."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Strict(BaseModel):
    """
    Rejects unknown fields.

    An authorize request with an extra key is either a client on the wrong
    version or someone probing, and both deserve a 422 rather than a silent
    ignore. It also means a field renamed on one side cannot quietly become a
    default on the other.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- mandate ---


class Party(Strict):
    pubkey: str


class Delegator(Party):
    vpa: str


class Delegate(Party):
    agent_id: str


class Constraints(Strict):
    max_per_txn_paise: Paise
    max_total_paise: Paise
    max_count: int = Field(ge=1)
    merchant_allowlist: list[str]
    category_allowlist: list[str]
    valid_from: str
    valid_until: str

    @field_validator("max_per_txn_paise", "max_total_paise", mode="before")
    @classmethod
    def _paise(cls, v: Any) -> int:
        return parse_paise(v)


class Mandate(Strict):
    """A UPI Circle shaped delegation, signed on the buyer's device."""

    v: Literal[1] = 1
    mandate_id: str
    delegator: Delegator
    delegate: Delegate
    intent: str
    constraints: Constraints
    issued_at: str
    signature: str | None = None


# --------------------------------------------------------------- headroom ---


class Headroom(Strict):
    """
    The minimal signed envelope the merchant is allowed to read.

    What is deliberately absent, and must stay absent: the delegator's identity,
    the intent text, the total budget, and the spend history. When a judge asks
    whether this leaks buyer data, the answer is that the schema physically
    cannot carry it. `test_headroom.py` asserts exactly that.
    """

    mandate_id: str
    headroom_paise: Paise
    max_per_txn_paise: Paise
    payments_remaining: int
    categories_allowed: list[str]
    valid_until: str
    merchant_in_scope: bool
    as_of: str
    signature: str | None = None


# ------------------------------------------------------------------ quote ---


class LineItem(Strict):
    sku: str
    name: str
    qty: int = Field(ge=1)
    unit_paise: Paise
    line_total_paise: Paise
    category: str


class HeadroomFit(Strict):
    fits: bool
    headroom_paise: Paise
    headroom_after_paise: Paise
    categories_ok: bool


class Quote(Strict):
    quote_id: str
    items: list[LineItem]
    subtotal_paise: Paise
    tax_paise: Paise
    shipping_paise: Paise
    total_paise: Paise
    expires_at: str
    headroom_fit: HeadroomFit | None = None


class QuoteItemRequest(Strict):
    sku: str
    qty: int = Field(default=1, ge=1)


class QuoteRequest(Strict):
    items: list[QuoteItemRequest]
    mandate_id: str | None = None


class Addon(Strict):
    sku: str
    name: str
    category: str
    price_paise: Paise
    reason: str | None = None


class SuggestAddonsRequest(Strict):
    quote_id: str
    mandate_id: str


class SuggestAddonsResponse(Strict):
    addons: list[Addon]
    #: Candidates withheld because they would not have passed the gate. Lane B
    #: needs this to compute the naive baseline's rejection rate.
    filtered_out: int


# ------------------------------------------------------------- gate wire ---


class AuthorizeContext(Strict):
    """
    Populated honestly by the buyer agent on every call.

    If this is stubbed the intent auditor has nothing to work with and the
    injection attack cannot fire, so `atk_01` would pass for the wrong reason.
    """

    page_excerpt: str = ""
    agent_reasoning: str = ""


class AuthorizeRequest(Strict):
    mandate_id: str
    quote_id: str
    amount_paise: Paise
    payee_vpa: str
    nonce: str
    issued_at: str
    context: AuthorizeContext = Field(default_factory=AuthorizeContext)
    signature: str | None = None

    @field_validator("amount_paise", mode="before")
    @classmethod
    def _paise(cls, v: Any) -> int:
        return parse_paise(v)


class InjectedSpan(Strict):
    text: str
    start: int
    end: int


class CheckResult(Strict):
    name: str
    status: Literal["PASS", "FAIL", "SKIPPED", "STEP_UP"]
    ms: float
    detail: str = ""
    injected_span: InjectedSpan | None = None


class Decision(Strict):
    decision_id: str
    mandate_id: str
    verdict: Verdict
    reason_code: ReasonCode
    reason_detail: str = ""
    payee_vpa: str
    amount_paise: Paise
    quote_id: str | None = None
    order_id: str | None = None
    elapsed_ms: float
    checks: list[CheckResult]
    page_excerpt: str | None = None
    #: Single use, issued only on ALLOW. The merchant redeems it to place an
    #: order; the gate refuses a second redemption.
    settlement_token: str | None = None
    at: str


# ------------------------------------------------------------------- saga ---

SagaState = Literal[
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
]


class SagaStep(Strict):
    order_id: str
    seq: int
    state: SagaState
    action: str
    outcome: Literal["OK", "FAIL", "PENDING"]
    detail: str = ""
    #: The contract's name for why a step failed, where one applies. `detail` is
    #: prose and may be reworded freely; this is the thing Lane B asserts on and
    #: Lane C colours by. Optional because most steps succeed, and a successful
    #: step has no reason to give.
    reason_code: ReasonCode | None = None
    #: The rail's id where one exists: pay_, rfnd_, order_.
    ref: str | None = None
    at: str


class Order(Strict):
    order_id: str
    quote_id: str
    mandate_id: str
    state: SagaState
    amount_paise: Paise
    items_summary: str
    rail: str
    rail_order_id: str | None = None
    rail_payment_id: str | None = None
    #: Set when this order exists because an earlier one rolled back.
    recovered_from: str | None = None
    #: Present between ALTERNATIVE_OFFERED and the buyer's answer.
    alternative: Addon | None = None
    at: str


class CreateOrderRequest(Strict):
    quote_id: str
    decision_id: str
    settlement_token: str
    #: Set when this order is the replacement for one that rolled back, so the
    #: revenue is attributed to the recovery rather than counted as fresh GMV.
    recovered_from: str | None = None


# ------------------------------------------------------------------ stats ---


class MerchantStats(Strict):
    gmv_paise: Paise = 0
    orders: int = 0
    avg_order_value_paise: Paise = 0
    upsell_offers_made: int = 0
    upsell_offers_accepted: int = 0
    upsell_offers_filtered_by_headroom: int = 0
    upsell_attach_rate: float = 0.0
    recovered_paise: Paise = 0
    recovered_orders: int = 0
    needs_attention: int = 0


# --------------------------------------------------------------- manifest ---


class AgentCommerceManifest(Strict):
    """Served at /.well-known/agent-commerce.json so an unknown agent can find
    us cold, without a partnership and without being told our endpoints."""

    merchant: str
    merchant_vpa: str
    mcp_endpoint: str
    categories: list[str]
    currency: str
    accepts_mandates: list[str]
    headroom_endpoint: str
    quote_ttl_seconds: int
    return_window_days: int
    rate_limit_per_minute: int
