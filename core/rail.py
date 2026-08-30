"""
The settlement rail interface. Five methods, one per thing a rail must do.

**Nothing in `core/` imports anything from `rails/`.** The gate decides on
authority; a rail moves money. If a check ever needs to know which rail it is
on, the design is wrong. `tests/test_layering.py` greps the imports and fails
the build if this is violated, because at hour 20 someone will import
`rails.razorpay` into a check "just to read the payment id" and nobody will
notice in review.

This file lives in `core/` and declares only the shape. The implementations live
in `rails/` and depend on `core`, never the other way round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from contracts.money import Paise


@dataclass(frozen=True, slots=True)
class RailIntent:
    """A created but unsettled payment. `order_id` on Razorpay, an equivalent
    elsewhere."""

    intent_id: str
    amount_paise: Paise
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RailResult:
    ok: bool
    #: The rail's id for what just happened: pay_, rfnd_, or the rail's analogue.
    ref: str | None
    status: str
    #: True when this call was served from the idempotency table rather than
    #: sent to the rail. The saga records it, and the duplicate-webhook demo
    #: leans on it.
    replayed: bool = False
    error_code: str | None = None
    error_detail: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RailStatus:
    intent_id: str
    status: str
    amount_paise: Paise
    amount_refunded_paise: Paise = 0
    payment_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RailAdapter(Protocol):
    """
    Frozen with everything else at hour 4. Adding a rail is implementing this;
    the saga, the ledger and the audit trail do not change.
    """

    name: str

    def create_intent(self, amount_paise: Paise, ref: str, idem_key: str) -> RailIntent: ...

    def capture(self, intent_id: str, amount_paise: Paise, idem_key: str) -> RailResult: ...

    def refund(self, intent_id: str, amount_paise: Paise, idem_key: str) -> RailResult: ...

    def status(self, intent_id: str) -> RailStatus: ...

    def verify_callback(self, body: bytes, signature: str) -> bool: ...
