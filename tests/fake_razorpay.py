"""
A Razorpay stand-in that enforces the documented rules.

The client has never run against the real API — there are no test keys in this
environment — so until now every line below `RazorpayAdapter` was unexercised.
That is the largest unverified surface in the system, and "we could not test it"
is not the same as "we cannot test any of it".

This is not a mock that returns whatever the caller hopes for. It is a small
server built from `rails/razorpay/API_NOTES.md`, and it **refuses** requests the
real API refuses:

  - Basic auth is required; a missing or wrong key is 401.
  - `amount` and `currency` are both mandatory on capture. This is the field
    people get wrong from memory, so getting it wrong here is a 400.
  - Only an `authorized` payment can be captured. A second capture is HTTP 400
    with the documented "order is already paid" description, not a success.
  - `receipt` is 40 ASCII characters, and a duplicate is rejected rather than
    replayed — on orders and refunds, the only two endpoints that have one.
  - A refund may come back `pending`. That is a real state, not an error.

What this proves and what it does not:

  It proves the client sends what the docs require, parses what the docs
  return, and behaves correctly on the documented error paths.

  It does not prove the docs are right, and it cannot catch a field the real
  API requires that `API_NOTES.md` failed to record. Only a test key does that.
  The boundary is exactly the accuracy of API_NOTES.md, and that is a much
  smaller and better-labelled gap than "unexercised".
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

KEY_ID = "rzp_test_fake0000000000"
KEY_SECRET = "secret_fake_0000000000"
WEBHOOK_SECRET = "whsec_fake_000"


def _error(status: int, code: str, description: str) -> httpx.Response:
    """Razorpay's error envelope, which the client reads `error.code` and
    `error.description` out of."""
    return httpx.Response(
        status,
        json={"error": {"code": code, "description": description, "source": "business",
                        "step": "payment_initiation", "reason": "input_validation_failed"}},
    )


@dataclass
class FakeRazorpay:
    """
    The state a real account would hold, and a record of every request, so a
    test can assert on what was actually sent rather than only on what came
    back.
    """

    orders: dict[str, dict] = field(default_factory=dict)
    payments: dict[str, dict] = field(default_factory=dict)
    refunds: dict[str, dict] = field(default_factory=dict)

    #: receipt -> id, for the duplicate rejection the docs describe.
    order_receipts: dict[str, str] = field(default_factory=dict)
    refund_receipts: dict[str, str] = field(default_factory=dict)

    #: Every request, in order: (method, path, parsed body, headers).
    seen: list[tuple[str, str, dict, httpx.Headers]] = field(default_factory=list)

    #: Status codes to return before behaving normally again, for the retry
    #: path. Popped from the front.
    fail_with: list[int] = field(default_factory=list)

    #: Refunds come back in this state. `pending` is documented and real.
    refund_status: str = "processed"

    _n: int = 0

    # ------------------------------------------------------------ helpers ---

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:014d}"

    def authorized_payment(self, order_id: str, amount_paise: int) -> str:
        """
        A payment sitting in `authorized`, as one is after a customer pays but
        before the merchant captures. Nothing in our code creates this — the
        customer does — so a test has to put one here.
        """
        pid = self._id("pay")
        self.payments[pid] = {
            "id": pid, "entity": "payment", "amount": amount_paise, "currency": "INR",
            "status": "authorized", "order_id": order_id, "captured": False,
            "amount_refunded": 0, "created_at": int(time.time()),
        }
        return pid

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ------------------------------------------------------------ routing ---

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {}
        if request.content:
            body = json.loads(request.content)
        path = request.url.path.removeprefix("/v1")
        self.seen.append((request.method, path, body, request.headers))

        if self.fail_with:
            status = self.fail_with.pop(0)
            return httpx.Response(status, json={"error": {"code": "SERVER_ERROR"}})

        unauthorized = self._check_auth(request)
        if unauthorized is not None:
            return unauthorized

        if request.method == "POST" and path == "/orders":
            return self._create_order(body)
        if m := re.fullmatch(r"/payments/([^/]+)/capture", path):
            return self._capture(m.group(1), body)
        if m := re.fullmatch(r"/payments/([^/]+)/refund", path):
            return self._refund(m.group(1), body)
        if m := re.fullmatch(r"/payments/([^/]+)/refunds/([^/]+)", path):
            return self._get_refund(m.group(2))
        if m := re.fullmatch(r"/orders/([^/]+)/payments", path):
            return self._order_payments(m.group(1))
        if m := re.fullmatch(r"/payments/([^/]+)", path):
            return self._get_payment(m.group(1))

        return _error(404, "NOT_FOUND", f"no route for {request.method} {path}")

    def _check_auth(self, request: httpx.Request) -> httpx.Response | None:
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return _error(401, "BAD_REQUEST_ERROR", "Authentication failed")
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
        if decoded != f"{KEY_ID}:{KEY_SECRET}":
            return _error(401, "BAD_REQUEST_ERROR", "Authentication failed")
        return None

    # ------------------------------------------------------------- orders ---

    def _create_order(self, body: dict) -> httpx.Response:
        if "amount" not in body:
            return _error(400, "BAD_REQUEST_ERROR", "The amount field is required.")
        if body.get("currency") != "INR":
            return _error(400, "BAD_REQUEST_ERROR", "The currency field is required.")

        receipt = body.get("receipt")
        if receipt is not None:
            if len(receipt) > 40:
                return _error(400, "BAD_REQUEST_ERROR",
                              "The receipt may not be greater than 40 characters.")
            if not receipt.isascii():
                return _error(400, "BAD_REQUEST_ERROR", "The receipt must be ASCII.")
            if receipt in self.order_receipts:
                # The documented duplicate rejection. Note it is an *error*,
                # not a replay of the original order — which is precisely why
                # the client keeps its own idempotency table.
                return _error(400, "BAD_REQUEST_ERROR",
                              "Duplicate request. Order already exists for receipt.")
            self.order_receipts[receipt] = "pending"

        oid = self._id("order")
        order = {
            "id": oid, "entity": "order", "amount": body["amount"], "amount_paid": 0,
            "amount_due": body["amount"], "currency": "INR", "receipt": receipt,
            "status": "created", "attempts": 0, "notes": body.get("notes", {}),
            "created_at": int(time.time()), "offer_id": None,
        }
        self.orders[oid] = order
        if receipt is not None:
            self.order_receipts[receipt] = oid
        return httpx.Response(200, json=order)

    # ----------------------------------------------------------- payments ---

    def _get_payment(self, pid: str) -> httpx.Response:
        payment = self.payments.get(pid)
        if payment is None:
            return _error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")
        return httpx.Response(200, json=payment)

    def _order_payments(self, oid: str) -> httpx.Response:
        items = [p for p in self.payments.values() if p.get("order_id") == oid]
        return httpx.Response(200, json={"entity": "collection", "count": len(items),
                                         "items": items})

    def _capture(self, pid: str, body: dict) -> httpx.Response:
        payment = self.payments.get(pid)
        if payment is None:
            return _error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")

        # Both mandatory. Capture is not a bare POST, whatever memory says.
        if "amount" not in body:
            return _error(400, "BAD_REQUEST_ERROR", "The amount field is required.")
        if "currency" not in body:
            return _error(400, "BAD_REQUEST_ERROR", "The currency field is required.")
        if body["amount"] != payment["amount"]:
            return _error(400, "BAD_REQUEST_ERROR",
                          "The amount must be equal to the authorised amount.")
        if body["currency"] != payment["currency"]:
            return _error(400, "BAD_REQUEST_ERROR",
                          "The currency must match the payment currency.")

        if payment["status"] != "authorized":
            return _error(400, "BAD_REQUEST_ERROR",
                          "Your payment has been declined as the order is already paid.")

        payment["status"] = "captured"
        payment["captured"] = True
        if order := self.orders.get(payment.get("order_id", "")):
            order["status"] = "paid"
            order["amount_paid"] = payment["amount"]
            order["amount_due"] = 0
        return httpx.Response(200, json=payment)

    # ------------------------------------------------------------ refunds ---

    def _refund(self, pid: str, body: dict) -> httpx.Response:
        payment = self.payments.get(pid)
        if payment is None:
            return _error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")
        if payment["status"] != "captured":
            return _error(400, "BAD_REQUEST_ERROR",
                          "This payment has not been captured yet.")

        receipt = body.get("receipt")
        if receipt is not None:
            if len(receipt) > 40:
                return _error(400, "BAD_REQUEST_ERROR",
                              "The receipt may not be greater than 40 characters.")
            if receipt in self.refund_receipts:
                return _error(400, "BAD_REQUEST_ERROR",
                              "Duplicate receipt found for this refund request.")
            self.refund_receipts[receipt] = "pending"

        amount = body.get("amount", payment["amount"] - payment["amount_refunded"])
        if amount > payment["amount"] - payment["amount_refunded"]:
            return _error(400, "BAD_REQUEST_ERROR",
                          "The refund amount is greater than the amount refundable.")

        rid = self._id("rfnd")
        refund = {
            "id": rid, "entity": "refund", "amount": amount, "currency": "INR",
            "payment_id": pid, "status": self.refund_status,
            "speed_processed": body.get("speed", "normal"),
            "receipt": receipt, "created_at": int(time.time()),
        }
        self.refunds[rid] = refund
        payment["amount_refunded"] += amount
        if receipt is not None:
            self.refund_receipts[receipt] = rid
        return httpx.Response(200, json=refund)

    def _get_refund(self, rid: str) -> httpx.Response:
        refund = self.refunds.get(rid)
        if refund is None:
            return _error(400, "BAD_REQUEST_ERROR", "The id provided does not exist")
        return httpx.Response(200, json=refund)
