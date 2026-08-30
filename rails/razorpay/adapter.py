"""
Razorpay behind the five-method RailAdapter.

The saga, the ledger and the audit trail talk to this shape and never to
`RazorpayClient`. That is what makes adding `mock_upi` or a cross-border rail a
new directory rather than a refactor.
"""

from __future__ import annotations

import logging

from contracts.money import Paise
from core.rail import RailAdapter, RailIntent, RailResult, RailStatus
from rails.razorpay.client import RazorpayClient, RazorpayError

log = logging.getLogger("pact.rail.razorpay")


class RazorpayAdapter(RailAdapter):
    name = "razorpay"

    def __init__(self, client: RazorpayClient) -> None:
        self.client = client

    def create_intent(self, amount_paise: Paise, ref: str, idem_key: str) -> RailIntent:
        body = self.client.create_order(
            amount_paise, receipt=ref, notes={"pact_ref": ref}, idem_key=idem_key
        )
        return RailIntent(
            intent_id=body["id"],
            amount_paise=int(body.get("amount", amount_paise)),
            status=str(body.get("status", "created")),
            raw=body,
        )

    def capture(self, intent_id: str, amount_paise: Paise, idem_key: str) -> RailResult:
        """
        `intent_id` here is the payment id, not the order id. Razorpay captures
        payments; an order is only the thing a payment is made against.
        """
        try:
            body = self.client.capture(intent_id, amount_paise, idem_key)
        except RazorpayError as exc:
            return RailResult(
                ok=False,
                ref=None,
                status="failed",
                error_code=exc.code or str(exc.status),
                error_detail=exc.description,
                raw=exc.body if isinstance(exc.body, dict) else {},
            )
        return RailResult(
            ok=body.get("status") == "captured",
            ref=body.get("id"),
            status=str(body.get("status", "")),
            replayed=bool(body.get("_replayed")),
            raw=body,
        )

    def refund(self, intent_id: str, amount_paise: Paise, idem_key: str) -> RailResult:
        try:
            body = self.client.refund(intent_id, amount_paise, idem_key)
        except RazorpayError as exc:
            return RailResult(
                ok=False,
                ref=None,
                status="failed",
                error_code=exc.code or str(exc.status),
                error_detail=exc.description,
                raw=exc.body if isinstance(exc.body, dict) else {},
            )

        status = str(body.get("status", ""))
        # `pending` is a real state. A 200 here does not mean the money is back,
        # and the saga must not close the compensation on it.
        return RailResult(
            ok=status in ("processed", "pending"),
            ref=body.get("id"),
            status=status,
            replayed=bool(body.get("_replayed")),
            raw=body,
        )

    def status(self, intent_id: str) -> RailStatus:
        """
        `intent_id` is the Razorpay order id. Used by the reconciliation poller,
        which never trusts a webhook as the only path.
        """
        payments = self.client.fetch_order_payments(intent_id)
        captured = next((p for p in payments if p.get("status") == "captured"), None)
        chosen = captured or (payments[0] if payments else None)
        return RailStatus(
            intent_id=intent_id,
            status=str(chosen.get("status")) if chosen else "created",
            amount_paise=int(chosen.get("amount", 0)) if chosen else 0,
            amount_refunded_paise=int(chosen.get("amount_refunded", 0)) if chosen else 0,
            payment_id=chosen.get("id") if chosen else None,
            raw={"payments": payments},
        )

    def verify_callback(self, body: bytes, signature: str) -> bool:
        return self.client.verify_webhook(body, signature)
