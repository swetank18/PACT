"""
A simulated UPI Circle rail.

This is not a stub for when Razorpay is down. It is the second rail, and its
whole job is to prove the adapter boundary is real: the gate, the ledger, the
saga and the audit trail run unchanged against it, because none of them knows
which rail they are on.

It also makes the demo runnable with no keys at all, and it makes Lane B's
simulation — hundreds of sessions per arm — possible without hammering a
sandbox that has an UNVERIFIED rate limit.

It settles in memory, deterministically, with injectable failures so the chaos
suite can force a refund to fail without unplugging anything.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from dataclasses import dataclass, field

from contracts.ids import new_id
from contracts.money import Paise
from core.rail import RailAdapter, RailIntent, RailResult, RailStatus

log = logging.getLogger("pact.rail.mock_upi")


@dataclass
class _Intent:
    intent_id: str
    amount_paise: Paise
    status: str = "created"
    payment_id: str | None = None
    refunded_paise: Paise = 0


@dataclass
class FailureSwitches:
    """
    Deterministic failure injection for the chaos suite.

    `refund_fails` is the one that matters. Anyone can handle the failure they
    planned for; handling the failure of your own compensation is what payments
    engineering actually looks like, and it is the scenario worth volunteering
    unprompted.
    """

    capture_fails: bool = False
    refund_fails: bool = False
    #: Refunds report `pending` instead of `processed`, so the saga has to cope
    #: with a compensation that has not finished.
    refund_pending: bool = False


class MockUpiAdapter(RailAdapter):
    name = "mock_upi"

    def __init__(self, *, webhook_secret: str = "mock_upi_secret") -> None:
        self._intents: dict[str, _Intent] = {}
        self._by_payment: dict[str, _Intent] = {}
        self._idem: dict[str, RailResult] = {}
        self._lock = threading.Lock()
        self.webhook_secret = webhook_secret
        self.failures = FailureSwitches()

    def create_intent(self, amount_paise: Paise, ref: str, idem_key: str) -> RailIntent:
        with self._lock:
            intent = _Intent(intent_id=new_id("ord").replace("ord_", "mupi_"), amount_paise=amount_paise)
            self._intents[intent.intent_id] = intent
            # A UPI Circle payment is authorised the moment the delegate
            # presents the mandate, so it lands here already authorised.
            intent.payment_id = intent.intent_id.replace("mupi_", "mpay_")
            intent.status = "authorized"
            self._by_payment[intent.payment_id] = intent
        return RailIntent(
            intent_id=intent.intent_id,
            amount_paise=amount_paise,
            status=intent.status,
            raw={"ref": ref, "payment_id": intent.payment_id},
        )

    def capture(self, intent_id: str, amount_paise: Paise, idem_key: str) -> RailResult:
        with self._lock:
            if idem_key in self._idem:
                cached = self._idem[idem_key]
                return RailResult(**{**cached.__dict__, "replayed": True})

            intent = self._by_payment.get(intent_id) or self._intents.get(intent_id)
            if intent is None:
                return RailResult(ok=False, ref=None, status="not_found", error_code="NOT_FOUND")
            if self.failures.capture_fails:
                return RailResult(
                    ok=False, ref=None, status="failed", error_code="CAPTURE_DECLINED"
                )
            if intent.status == "captured":
                # Same shape as Razorpay's behaviour: a second capture is not a
                # new charge, and the caller sees that it was a replay.
                result = RailResult(ok=True, ref=intent.payment_id, status="captured", replayed=True)
                self._idem[idem_key] = result
                return result

            intent.status = "captured"
            result = RailResult(ok=True, ref=intent.payment_id, status="captured")
            self._idem[idem_key] = result
            return result

    def refund(self, intent_id: str, amount_paise: Paise, idem_key: str) -> RailResult:
        with self._lock:
            if idem_key in self._idem:
                cached = self._idem[idem_key]
                return RailResult(**{**cached.__dict__, "replayed": True})

            intent = self._by_payment.get(intent_id) or self._intents.get(intent_id)
            if intent is None:
                return RailResult(ok=False, ref=None, status="not_found", error_code="NOT_FOUND")
            if self.failures.refund_fails:
                # chs_05. The compensation itself fails. The saga must retry,
                # then park in NEEDS_ATTENTION, and never silently swallow it.
                return RailResult(
                    ok=False,
                    ref=None,
                    status="failed",
                    error_code="REFUND_DECLINED",
                    error_detail="the refund could not be processed",
                )

            intent.refunded_paise += amount_paise
            status = "pending" if self.failures.refund_pending else "processed"
            result = RailResult(
                ok=True, ref=new_id("ord").replace("ord_", "mrfnd_"), status=status
            )
            self._idem[idem_key] = result
            return result

    def status(self, intent_id: str) -> RailStatus:
        with self._lock:
            intent = self._intents.get(intent_id) or self._by_payment.get(intent_id)
            if intent is None:
                return RailStatus(intent_id=intent_id, status="not_found", amount_paise=0)
            return RailStatus(
                intent_id=intent.intent_id,
                status=intent.status,
                amount_paise=intent.amount_paise,
                amount_refunded_paise=intent.refunded_paise,
                payment_id=intent.payment_id,
            )

    def verify_callback(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    # ------------------------------------------------------------- testing --

    def sign_callback(self, body: bytes) -> str:
        """So the chaos suite can post a webhook that actually verifies."""
        return hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()

    def reset(self) -> None:
        with self._lock:
            self._intents.clear()
            self._by_payment.clear()
            self._idem.clear()
            self.failures = FailureSwitches()
