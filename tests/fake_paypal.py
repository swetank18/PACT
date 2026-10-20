"""
A PayPal stand-in that enforces the documented rules.

Same purpose as `tests/fake_razorpay.py`, and the same limit. There are no
sandbox credentials here, so this is the only thing the client has run against.
It is built from `rails/paypal/API_NOTES.md` and refuses what the spec says
PayPal refuses:

  - Basic auth on the token endpoint, Bearer everywhere else, and a token can
    be revoked so the client's refresh path runs.
  - `value` is a string, and has no more decimals than the currency allows.
    A currency PayPal does not list — INR among them — is refused.
  - `Prefer` defaults to `return=minimal`: without asking for the
    representation, a write returns id, status and links and nothing else.
  - A `PayPal-Request-Id` seen before on the same call type returns the
    resource **as it is now**, with 200 rather than 201. Not as it was.
  - `invoice_id` is unique per merchant.
  - An authorization captures once, for no more than was authorised, in the
    authorised currency. A refund is for no more than is left.
  - Webhook verification answers SUCCESS only for a delivery this fake issued,
    with every header, the right webhook id, and the event as the exact bytes
    that were delivered.

What it proves and what it cannot: that the client sends what the notes say and
copes with what they say comes back. Not that the notes are right. A field the
real API requires that the notes failed to record is still invisible here, and
the issue code PayPal uses for capturing a captured authorization is this
fake's invention — which is why the client does not match on it.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx

CLIENT_ID = "sb-fake-client-0000"
CLIENT_SECRET = "sb-fake-secret-0000"
WEBHOOK_ID = "WH-FAKE-0000"
VAULT_ID = "8kk8451t"

#: The currencies the fake will settle, with PayPal's decimals. INR is absent
#: because PayPal's list does not have it.
DECIMALS = {"USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "AUD": 2, "JPY": 0, "HUF": 0, "TWD": 0}
VALUE_PATTERN = re.compile(r"^((-?[0-9]+)|(-?([0-9]+)?[.][0-9]+))$")


def _error(status: int, name: str, issue: str, description: str) -> httpx.Response:
    """PayPal's error body: name, message, debug_id, details[].issue."""
    return httpx.Response(
        status,
        json={
            "name": name,
            "message": "The requested action could not be performed.",
            "debug_id": f"dbg{abs(hash((issue, description))) % 10**10:010d}",
            "details": [{"issue": issue, "description": description}],
        },
    )


def _minimal(resource: dict) -> dict:
    return {k: resource[k] for k in ("id", "status", "links") if k in resource}


@dataclass
class FakePayPal:
    orders: dict[str, dict] = field(default_factory=dict)
    authorizations: dict[str, dict] = field(default_factory=dict)
    captures: dict[str, dict] = field(default_factory=dict)
    refunds: dict[str, dict] = field(default_factory=dict)
    setup_tokens: dict[str, dict] = field(default_factory=dict)

    #: Wallets that have been vaulted, and whether each one pays.
    vaulted: dict[str, bool] = field(default_factory=lambda: {VAULT_ID: True})

    tokens: set[str] = field(default_factory=set)
    invoice_ids: set[str] = field(default_factory=set)
    #: (call type, request id) -> (resource kind, resource id)
    request_ids: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    #: Every request, in order: (method, path, body, headers, raw bytes).
    seen: list[tuple[str, str, Any, httpx.Headers, bytes]] = field(default_factory=list)

    #: Status codes to return before behaving normally again. Popped in order.
    fail_with: list[int] = field(default_factory=list)
    #: Apply the next write, then drop the connection before answering — the
    #: call that may or may not have landed.
    drop_after_apply: bool = False

    capture_status: str = "COMPLETED"
    refund_status: str = "COMPLETED"

    #: Webhook deliveries this fake signed: transmission id -> (sig, raw body).
    issued: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    _n: int = 0

    # ------------------------------------------------------------ helpers ---

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:014d}"

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def revoke_tokens(self) -> None:
        """Every token stops working, as one does when PayPal expires it early."""
        self.tokens.clear()

    def deliver(self, event: dict, *, pretty: bool = False) -> tuple[bytes, dict[str, str]]:
        """
        A webhook delivery, signed: the raw bytes and the five headers. `pretty`
        sends whitespace a re-serialising client would destroy.
        """
        body = json.dumps(event, indent=2 if pretty else None).encode()
        tid = self._id("tx-")
        sig = base64.b64encode(f"sig:{tid}".encode()).decode()
        self.issued[tid] = (sig, body)
        return body, {
            "PAYPAL-AUTH-ALGO": "SHA256withRSA",
            "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-fake",
            "PAYPAL-TRANSMISSION-ID": tid,
            "PAYPAL-TRANSMISSION-SIG": sig,
            "PAYPAL-TRANSMISSION-TIME": "2026-10-20T12:00:00Z",
        }

    # ------------------------------------------------------------ routing ---

    def _handle(self, request: httpx.Request) -> httpx.Response:
        raw = request.content or b""
        body: Any = {}
        if raw and request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(raw)
        path = request.url.path
        self.seen.append((request.method, path, body, request.headers, raw))

        if self.fail_with:
            status = self.fail_with.pop(0)
            return _error(status, "INTERNAL_SERVER_ERROR", "INTERNAL_SERVICE_ERROR", "injected")

        if path == "/v1/oauth2/token":
            return self._token(request, raw)

        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.tokens:
            return httpx.Response(401, json={"error": "invalid_token",
                                             "error_description": "Token signature verification failed"})

        response = self._route(request.method, path, body, request.headers, raw)
        if self.drop_after_apply and request.method == "POST":
            self.drop_after_apply = False
            raise httpx.ReadTimeout("connection dropped after the write applied", request=request)
        return response

    def _route(self, method: str, path: str, body: Any, headers: httpx.Headers,
               raw: bytes) -> httpx.Response:
        prefer = headers.get("prefer", "return=minimal")
        rid = headers.get("paypal-request-id")

        if method == "POST" and path == "/v2/checkout/orders":
            return self._create_order(body, rid, prefer)
        if m := re.fullmatch(r"/v2/checkout/orders/([^/]+)", path):
            return self._show(self.orders, m.group(1))
        if m := re.fullmatch(r"/v2/payments/authorizations/([^/]+)/capture", path):
            return self._capture(m.group(1), body, rid, prefer)
        if m := re.fullmatch(r"/v2/payments/authorizations/([^/]+)/void", path):
            return self._void(m.group(1), rid, prefer)
        if m := re.fullmatch(r"/v2/payments/authorizations/([^/]+)", path):
            return self._show(self.authorizations, m.group(1))
        if m := re.fullmatch(r"/v2/payments/captures/([^/]+)/refund", path):
            return self._refund(m.group(1), body, rid, prefer)
        if m := re.fullmatch(r"/v2/payments/captures/([^/]+)", path):
            return self._show(self.captures, m.group(1))
        if method == "POST" and path == "/v1/notifications/verify-webhook-signature":
            return self._verify(raw)
        if method == "POST" and path == "/v3/vault/setup-tokens":
            return self._setup_token(body)
        if m := re.fullmatch(r"/v3/vault/setup-tokens/([^/]+)", path):
            return self._show(self.setup_tokens, m.group(1))
        if method == "POST" and path == "/v3/vault/payment-tokens":
            return self._payment_token(body)
        return _error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID", f"no route {method} {path}")

    # --------------------------------------------------------------- auth ---

    def _token(self, request: httpx.Request, raw: bytes) -> httpx.Response:
        header = request.headers.get("authorization", "")
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode() \
            if header.startswith("Basic ") else ""
        if decoded != f"{CLIENT_ID}:{CLIENT_SECRET}":
            return httpx.Response(401, json={"error": "invalid_client",
                                             "error_description": "Client Authentication failed"})
        if parse_qs(raw.decode()).get("grant_type") != ["client_credentials"]:
            return httpx.Response(400, json={"error": "unsupported_grant_type",
                                             "error_description": "Grant Type is NULL"})
        token = self._id("A21AA")
        self.tokens.add(token)
        return httpx.Response(200, json={"access_token": token, "token_type": "Bearer",
                                         "expires_in": 32400, "app_id": "APP-FAKE"})

    # ------------------------------------------------------------- money ----

    def _money_error(self, money: Any) -> httpx.Response | None:
        if not isinstance(money, dict) or "value" not in money or "currency_code" not in money:
            return _error(400, "INVALID_REQUEST", "MISSING_REQUIRED_PARAMETER",
                          "amount needs currency_code and value")
        currency, value = money["currency_code"], money["value"]
        if currency not in DECIMALS:
            return _error(422, "UNPROCESSABLE_ENTITY", "INVALID_CURRENCY_CODE",
                          f"Currency code {currency} is not supported")
        if not isinstance(value, str) or not VALUE_PATTERN.match(value):
            return _error(400, "INVALID_REQUEST", "INVALID_PARAMETER_SYNTAX",
                          "value must be a string number")
        places = -Decimal(value).as_tuple().exponent if "." in value else 0
        if places > DECIMALS[currency]:
            return _error(422, "UNPROCESSABLE_ENTITY", "DECIMAL_PRECISION",
                          f"{currency} does not support {places} decimal places")
        if Decimal(value) <= 0:
            return _error(422, "UNPROCESSABLE_ENTITY", "CANNOT_BE_ZERO_OR_NEGATIVE",
                          "amount must be positive")
        return None

    # --------------------------------------------------------- idempotency --

    def _replay(self, call: str, rid: str | None, prefer: str) -> httpx.Response | None:
        """A known request id returns the resource as it is *now*, with 200."""
        if rid is None or (call, rid) not in self.request_ids:
            return None
        kind, rid_target = self.request_ids[(call, rid)]
        store = {"order": self.orders, "authorization": self.authorizations,
                 "capture": self.captures, "refund": self.refunds}[kind]
        resource = store[rid_target]
        return httpx.Response(200, json=resource if "representation" in prefer
                              else _minimal(resource))

    # ------------------------------------------------------------ orders ----

    def _create_order(self, body: dict, rid: str | None, prefer: str) -> httpx.Response:
        if (replay := self._replay("orders.create", rid, prefer)) is not None:
            return replay
        if body.get("intent") not in ("CAPTURE", "AUTHORIZE"):
            return _error(400, "INVALID_REQUEST", "MISSING_REQUIRED_PARAMETER", "intent")
        units = body.get("purchase_units") or []
        if not 1 <= len(units) <= 10:
            return _error(400, "INVALID_REQUEST", "INVALID_ARRAY_MIN_ITEMS", "purchase_units")
        unit = units[0]
        if (err := self._money_error(unit.get("amount"))) is not None:
            return err
        for key, limit in (("reference_id", 256), ("custom_id", 255), ("invoice_id", 127)):
            if key in unit and not 1 <= len(unit[key]) <= limit:
                return _error(400, "INVALID_REQUEST", "INVALID_STRING_LENGTH", key)
        invoice = unit.get("invoice_id")
        if invoice and invoice in self.invoice_ids:
            return _error(422, "UNPROCESSABLE_ENTITY", "DUPLICATE_INVOICE_ID",
                          "Duplicate Invoice ID detected.")

        oid = self._id("ORD")
        order: dict[str, Any] = {
            "id": oid, "intent": body["intent"], "status": "CREATED",
            "purchase_units": [{**unit, "payments": {}}],
            "links": [{"rel": "self", "href": f"/v2/checkout/orders/{oid}", "method": "GET"}],
        }

        wallet = ((body.get("payment_source") or {}).get("paypal") or {})
        vault_id = wallet.get("vault_id")
        if vault_id is not None:
            if vault_id not in self.vaulted:
                return _error(422, "UNPROCESSABLE_ENTITY", "INVALID_VAULT_ID",
                              "The vault_id is invalid.")
            if body["intent"] == "AUTHORIZE":
                # Single shot: created and authorised in one call. The order is
                # COMPLETED either way; only the authorization says if it paid.
                aid = self._id("AUTH")
                self.authorizations[aid] = {
                    "id": aid,
                    "status": "CREATED" if self.vaulted[vault_id] else "DENIED",
                    "amount": dict(unit["amount"]),
                    "supplementary_data": {"related_ids": {"order_id": oid}},
                }
                order["status"] = "COMPLETED"
                order["purchase_units"][0]["payments"] = {
                    "authorizations": [self.authorizations[aid]]}
        else:
            order["links"].append({"rel": "approve", "method": "GET",
                                   "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"})

        self.orders[oid] = order
        if invoice:
            self.invoice_ids.add(invoice)
        if rid:
            self.request_ids[("orders.create", rid)] = ("order", oid)
        return httpx.Response(201, json=order if "representation" in prefer else _minimal(order))

    def _show(self, store: dict, rid: str) -> httpx.Response:
        if rid not in store:
            return _error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID",
                          "Specified resource ID does not exist.")
        return httpx.Response(200, json=store[rid])

    # ---------------------------------------------------- authorizations ----

    def _capture(self, aid: str, body: dict, rid: str | None, prefer: str) -> httpx.Response:
        if (replay := self._replay("authorizations.capture", rid, prefer)) is not None:
            return replay
        auth = self.authorizations.get(aid)
        if auth is None:
            return _error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID",
                          "Specified resource ID does not exist.")
        amount = body.get("amount") or auth["amount"]
        if (err := self._money_error(amount)) is not None:
            return err
        if amount["currency_code"] != auth["amount"]["currency_code"]:
            return _error(422, "UNPROCESSABLE_ENTITY", "CURRENCY_MISMATCH",
                          "Currency of capture must match the authorization.")
        if Decimal(amount["value"]) > Decimal(auth["amount"]["value"]):
            return _error(422, "UNPROCESSABLE_ENTITY", "MAX_CAPTURE_AMOUNT_EXCEEDED",
                          "Capture amount exceeds the authorized amount.")
        if auth["status"] != "CREATED":
            # The fake's own name for this. The spec does not give one, which
            # is why the client reads the authorization back instead.
            return _error(422, "UNPROCESSABLE_ENTITY", f"AUTHORIZATION_ALREADY_{auth['status']}",
                          f"Authorization is {auth['status']}.")

        cid = self._id("CAP")
        capture = {
            "id": cid, "status": self.capture_status, "amount": dict(amount),
            "final_capture": bool(body.get("final_capture", False)),
            "supplementary_data": {"related_ids": {
                "order_id": auth["supplementary_data"]["related_ids"]["order_id"],
                "authorization_id": aid}},
        }
        if self.capture_status == "PENDING":
            capture["status_details"] = {"reason": "PENDING_REVIEW"}
        if self.capture_status == "DECLINED":
            capture["status_details"] = {"reason": "DECLINED_BY_RISK_FRAUD_FILTERS"}
        self.captures[cid] = capture
        if self.capture_status in ("COMPLETED", "PENDING"):
            auth["status"] = "CAPTURED"
        order = self.orders[auth["supplementary_data"]["related_ids"]["order_id"]]
        order["purchase_units"][0]["payments"].setdefault("captures", []).append(capture)
        if rid:
            self.request_ids[("authorizations.capture", rid)] = ("capture", cid)
        return httpx.Response(201, json=capture if "representation" in prefer
                              else _minimal(capture))

    def _void(self, aid: str, rid: str | None, prefer: str) -> httpx.Response:
        auth = self.authorizations.get(aid)
        if auth is None:
            return _error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID",
                          "Specified resource ID does not exist.")
        if auth["status"] != "CREATED":
            return _error(422, "UNPROCESSABLE_ENTITY", "CANNOT_BE_VOIDED",
                          f"Authorization is {auth['status']}.")
        auth["status"] = "VOIDED"
        return httpx.Response(200, json=auth) if "representation" in prefer \
            else httpx.Response(204)

    # ----------------------------------------------------------- refunds ----

    def _refund(self, cid: str, body: dict, rid: str | None, prefer: str) -> httpx.Response:
        if (replay := self._replay("captures.refund", rid, prefer)) is not None:
            return replay
        capture = self.captures.get(cid)
        if capture is None:
            return _error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID",
                          "Specified resource ID does not exist.")
        if capture["status"] not in ("COMPLETED", "PARTIALLY_REFUNDED"):
            return _error(422, "UNPROCESSABLE_ENTITY", "CAPTURE_FULLY_REFUNDED"
                          if capture["status"] == "REFUNDED" else "INVALID_RESOURCE_STATE",
                          f"Capture is {capture['status']}.")
        refunded = sum(Decimal(r["amount"]["value"]) for r in self.refunds.values()
                       if r["capture_id"] == cid and r["status"] != "FAILED")
        remaining = Decimal(capture["amount"]["value"]) - refunded
        amount = body.get("amount") or {"currency_code": capture["amount"]["currency_code"],
                                         "value": str(remaining)}
        if (err := self._money_error(amount)) is not None:
            return err
        if Decimal(amount["value"]) > remaining:
            return _error(422, "UNPROCESSABLE_ENTITY", "REFUND_AMOUNT_EXCEEDED",
                          "The refund amount must be less than or equal to the capture amount "
                          "that has not yet been refunded.")
        fid = self._id("REF")
        refund = {"id": fid, "status": self.refund_status, "amount": dict(amount),
                  "capture_id": cid}
        self.refunds[fid] = refund
        capture["status"] = "REFUNDED" if Decimal(amount["value"]) == remaining \
            else "PARTIALLY_REFUNDED"
        order = self.orders[capture["supplementary_data"]["related_ids"]["order_id"]]
        order["purchase_units"][0]["payments"].setdefault("refunds", []).append(refund)
        if rid:
            self.request_ids[("captures.refund", rid)] = ("refund", fid)
        return httpx.Response(201, json=refund if "representation" in prefer
                              else _minimal(refund))

    # -------------------------------------------------------------- vault ---

    def approve(self, setup_token_id: str) -> None:
        """What the account holder does at PayPal's approve link."""
        self.setup_tokens[setup_token_id]["status"] = "APPROVED"

    def _setup_token(self, body: dict) -> httpx.Response:
        wallet = (body.get("payment_source") or {}).get("paypal")
        if wallet is None:
            return _error(400, "INVALID_REQUEST", "MISSING_REQUIRED_PARAMETER", "payment_source")
        context = wallet.get("experience_context") or {}
        if not context.get("return_url") or not context.get("cancel_url"):
            return _error(422, "UNPROCESSABLE_ENTITY", "RETURN_URL_REQUIRED",
                          "return_url and cancel_url are required")
        sid = self._id("ST")
        self.setup_tokens[sid] = {
            "id": sid, "status": "PAYER_ACTION_REQUIRED",
            "payment_source": {"paypal": {"usage_type": wallet.get("usage_type")}},
            "links": [{"rel": "approve", "method": "GET",
                       "href": f"https://www.sandbox.paypal.com/agreements/approve?approval_session_id={sid}"}],
        }
        return httpx.Response(200, json=self.setup_tokens[sid])

    def _payment_token(self, body: dict) -> httpx.Response:
        token = (body.get("payment_source") or {}).get("token") or {}
        setup = self.setup_tokens.get(token.get("id", ""))
        if token.get("type") != "SETUP_TOKEN" or setup is None:
            return _error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID",
                          "Specified resource ID does not exist.")
        if setup["status"] != "APPROVED":
            return _error(422, "UNPROCESSABLE_ENTITY", "SETUP_TOKEN_NOT_APPROVED",
                          "The payer has not approved the setup token.")
        vault_id = self._id("vt")
        self.vaulted[vault_id] = True
        return httpx.Response(201, json={"id": vault_id, "payment_source": {"paypal": {}}})

    # ----------------------------------------------------------- webhooks ---

    def _verify(self, raw: bytes) -> httpx.Response:
        body = json.loads(raw)
        required = ("auth_algo", "cert_url", "transmission_id", "transmission_sig",
                    "transmission_time", "webhook_id", "webhook_event")
        if any(not body.get(k) for k in required):
            return _error(400, "VALIDATION_ERROR", "MISSING_REQUIRED_PARAMETER",
                          "every verification field is required")
        issued = self.issued.get(body["transmission_id"])
        genuine = (
            issued is not None
            and body["webhook_id"] == WEBHOOK_ID
            and body["transmission_sig"] == issued[0]
            # The event exactly as delivered: its bytes, whitespace and all,
            # must appear in the verification request.
            and b'"webhook_event":' + issued[1] in raw
        )
        return httpx.Response(200, json={"verification_status": "SUCCESS" if genuine else "FAILURE"})
