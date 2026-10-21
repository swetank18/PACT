"""
Every PayPal call in the system goes through this file. Nothing else talks to
PayPal.

Written against `API_NOTES.md`, which was written against PayPal's published
specs on 2026-10-20. The findings that shape this file:

**Sandbox only, enforced on the host.** A PayPal client id does not say which
environment it belongs to, so there is no prefix to check the way the Razorpay
client checks `rzp_test_`. The client talks to the sandbox host and refuses to
be pointed anywhere else.

**Money is a decimal string whose precision belongs to the currency.** PACT
counts integer minor units. `to_value` and `from_value` are the only crossing,
and both refuse rather than round.

**PayPal has an idempotency header, and it is not enough on its own.** A
`PayPal-Request-Id` must be unique per call type, and a replay returns the
resource as it is *now* — a capture retried after its refund comes back
`REFUNDED`. So each operation gets its own request id, and the local table
still answers a replay with what actually happened.

**`Prefer: return=minimal` is the default.** Without asking for the
representation, a create returns no authorization id at all.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from contracts.money import MinorUnits
from contracts.schemas import utcnow
from core.db import Database

log = logging.getLogger("pact.paypal")

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
SANDBOX_HOSTS = frozenset({"api-m.sandbox.paypal.com", "api.sandbox.paypal.com"})
DEFAULT_TIMEOUT_S = 10.0
RETRIES = 3
#: Refresh a token this long before PayPal says it expires, so a request is
#: never sent with one that dies in flight.
TOKEN_MARGIN_S = 60

#: Decimal places PayPal takes per currency. A currency not listed is refused at
#: construction rather than guessed at: "12.30" sent for JPY is an error, and
#: 1230 minor units sent as "12.30" in a zero-decimal currency would charge a
#: hundredth of the price. INR is absent because PayPal does not list it — see
#: API_NOTES — and a rupee amount must not travel labelled as anything else.
DECIMALS: Mapping[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "AUD": 2, "JPY": 0}

#: The five headers PayPal signs a webhook delivery with, as the verify call
#: names its fields. Lower case, because that is how they arrive through ASGI.
WEBHOOK_HEADERS: Mapping[str, str] = {
    "paypal-auth-algo": "auth_algo",
    "paypal-cert-url": "cert_url",
    "paypal-transmission-id": "transmission_id",
    "paypal-transmission-sig": "transmission_sig",
    "paypal-transmission-time": "transmission_time",
}


class PayPalError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str | None,
        description: str | None,
        body: Any,
        debug_id: str | None = None,
    ):
        super().__init__(f"paypal {status}: {code} {description}")
        self.status = status
        self.code = code
        self.description = description
        self.body = body
        #: What PayPal support asks for. Kept on every error, never discarded.
        self.debug_id = debug_id


# --------------------------------------------------------------- money ----


def to_value(amount_minor: MinorUnits, currency: str) -> str:
    """Integer minor units to PayPal's decimal string. Never via a float."""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise ValueError(f"amount must be integer minor units, got {amount_minor!r}")
    if amount_minor < 0:
        raise ValueError(f"amount must not be negative, got {amount_minor}")
    places = DECIMALS[currency]
    if places == 0:
        return str(amount_minor)
    unit = 10**places
    return f"{amount_minor // unit}.{amount_minor % unit:0{places}d}"


def from_value(value: str, currency: str) -> MinorUnits:
    """
    PayPal's decimal string back to integer minor units. Refuses a value with
    more decimals than the currency has, because rounding it would invent or
    lose a fraction of a cent in a ledger that agrees to the unit.
    """
    try:
        dec = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a money value: {value!r}") from exc
    scaled = dec.scaleb(DECIMALS[currency])
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value!r} has more decimals than {currency} allows")
    return int(scaled)


# --------------------------------------------------------- credentials ----


@dataclass(frozen=True, slots=True)
class Credentials:
    client_id: str
    client_secret: str
    #: From the app's webhook settings. Not a secret, but verification is
    #: impossible without it: PayPal signs over it.
    webhook_id: str = ""
    #: The account holder's vaulted wallet. One payer for the demo; see
    #: `PayPalAdapter.vault_id_for` for where a second one would come from.
    vault_id: str = ""
    base_url: str = SANDBOX_BASE

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def credentials_from_env() -> Credentials:
    return Credentials(
        client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID", ""),
        vault_id=os.environ.get("PAYPAL_VAULT_ID", ""),
        base_url=os.environ.get("PAYPAL_API_BASE", SANDBOX_BASE),
    )


def request_id(operation: str, idem_key: str) -> str:
    """
    One `PayPal-Request-Id` per call type. The saga hands the same key to
    `create_intent` and to `capture`, and PayPal requires the id to be unique
    per call type, so the operation goes into it.
    """
    return f"pact-{operation}-{idem_key}"


class PayPalClient:
    def __init__(
        self,
        db: Database,
        credentials: Credentials | None = None,
        *,
        currency: str = "USD",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.db = db
        self.credentials = credentials or credentials_from_env()

        host = urlsplit(self.credentials.base_url).hostname or ""
        if host not in SANDBOX_HOSTS:
            # A live credential in a hackathon repo is a real incident. There is
            # no key prefix to check, so the host is the guard.
            raise RuntimeError(
                f"PayPal base URL {self.credentials.base_url!r} is not the sandbox. "
                "This project is sandbox only. Refusing to start."
            )
        if currency not in DECIMALS:
            raise RuntimeError(
                f"PayPal cannot settle {currency}: it is not one of "
                f"{', '.join(sorted(DECIMALS))}. PayPal does not list INR at all, so "
                "a rupee-priced catalog needs a currency profile, not this rail."
            )
        self.currency = currency

        self._client = httpx.Client(
            base_url=self.credentials.base_url, timeout=DEFAULT_TIMEOUT_S, transport=transport
        )
        self._token: str | None = None
        self._token_expires_at = 0.0

        if not self.credentials.configured:
            log.warning(
                "No PayPal credentials. The rail will refuse calls rather than "
                "pretend to settle. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET, "
                "or run the mock_upi rail."
            )

    # ---------------------------------------------------------- idempotency --

    def _cached(self, operation: str, idem_key: str) -> dict | None:
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT result_json FROM idempotency WHERE idem_key = ?",
                (f"paypal.{operation}:{idem_key}",),
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def _remember(self, operation: str, idem_key: str, result: dict) -> None:
        with self.db.immediate_tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency "
                "(idem_key, operation, result_json, created_at) VALUES (?, ?, ?, ?)",
                (f"paypal.{operation}:{idem_key}", f"paypal.{operation}",
                 json.dumps(result, separators=(",", ":")), utcnow()),
            )

    # ---------------------------------------------------------- transport ----

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        response = self._client.post(
            "/v1/oauth2/token",
            auth=(self.credentials.client_id, self.credentials.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            body = response.json() if response.content else {}
            raise PayPalError(
                response.status_code,
                body.get("error") if isinstance(body, dict) else None,
                body.get("error_description") if isinstance(body, dict) else response.text,
                body,
            )
        body = response.json()
        self._token = str(body["access_token"])
        lifetime = float(body.get("expires_in", 0))
        self._token_expires_at = time.monotonic() + max(0.0, lifetime - TOKEN_MARGIN_S)
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        request_id: str | None = None,
        raw_content: bytes | None = None,
    ) -> dict:
        if not self.credentials.configured:
            raise PayPalError(0, "NO_CREDENTIALS", "PayPal is not configured", {})

        last: Exception | None = None
        refreshed = False
        attempt = 0
        while attempt < RETRIES:
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            try:
                response = self._client.request(
                    method, path, headers=headers,
                    content=raw_content if raw_content is not None
                    else (json.dumps(payload).encode() if payload is not None else None),
                )
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(0.25 * (2**attempt))
                attempt += 1
                continue

            if response.status_code == 401 and not refreshed:
                # A token PayPal no longer honours. Drop it and try once more
                # with a fresh one; a second 401 is the credentials, not the
                # token, and looping on it would hide that.
                self._token = None
                refreshed = True
                continue

            # 409 is "a previous call for the given resource is in progress":
            # the same request id still running. Waiting is the answer.
            if response.status_code in (409, 429) or response.status_code >= 500:
                last = PayPalError(response.status_code, None, response.text, {})
                time.sleep(0.25 * (2**attempt))
                attempt += 1
                continue

            body = response.json() if response.content else {}
            if response.status_code >= 400:
                details = body.get("details") if isinstance(body, dict) else None
                first = details[0] if details else {}
                raise PayPalError(
                    response.status_code,
                    first.get("issue") or (body.get("name") if isinstance(body, dict) else None),
                    first.get("description")
                    or (body.get("message") if isinstance(body, dict) else response.text),
                    body,
                    debug_id=body.get("debug_id") if isinstance(body, dict) else None,
                )
            return body

        raise PayPalError(0, "TRANSPORT", str(last), {})

    # -------------------------------------------------------------- vault ----
    #
    # The one moment a human is in the loop. The account holder approves the
    # wallet once, at PayPal, and what comes back is the `vault_id` every
    # later order names. It is the PayPal half of granting a mandate: the
    # mandate bounds what the agent may spend, the vault is what it spends
    # from.

    def create_setup_token(self, return_url: str, cancel_url: str, idem_key: str) -> dict:
        """Returns the setup token; its `approve` link is where the human goes."""
        return self._request(
            "POST",
            "/v3/vault/setup-tokens",
            {"payment_source": {"paypal": {
                "usage_type": "MERCHANT",
                "usage_pattern": "UNSCHEDULED_POSTPAID",
                "description": "Purchases your agent makes within the limits you sign",
                "experience_context": {"return_url": return_url, "cancel_url": cancel_url},
            }}},
            request_id=request_id("setup_token", idem_key),
        )

    def get_setup_token(self, setup_token_id: str) -> dict:
        return self._request("GET", f"/v3/vault/setup-tokens/{setup_token_id}")

    def create_payment_token(self, setup_token_id: str, idem_key: str) -> dict:
        """An approved setup token becomes a payment token. Its `id` is the
        `vault_id`."""
        return self._request(
            "POST",
            "/v3/vault/payment-tokens",
            {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}},
            request_id=request_id("payment_token", idem_key),
        )

    def delete_payment_token(self, vault_id: str) -> None:
        """
        Forget the wallet at PayPal, so nothing — not even a bug on our side —
        can charge it again. A 404 is success: the token is already gone.
        """
        try:
            self._request("DELETE", f"/v3/vault/payment-tokens/{vault_id}")
        except PayPalError as exc:
            if exc.status != 404:
                raise

    # ------------------------------------------------------------- orders ----

    def create_authorized_order(
        self, amount_minor: MinorUnits, ref: str, vault_id: str, idem_key: str
    ) -> dict:
        """
        Create and authorise in one call against a vaulted wallet: the shape
        PayPal publishes as "single shot". No buyer redirect, because the buyer
        approved the vault once, when they granted the mandate.
        """
        cached = self._cached("create_order", idem_key)
        if cached is not None:
            log.info("create_order short circuited on idempotency key")
            return {**cached, "_replayed": True}

        body = self._request(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "AUTHORIZE",
                "purchase_units": [{
                    "reference_id": ref[:256],
                    "custom_id": ref[:255],
                    # Unique per merchant account by default, so a second order
                    # for the same PACT order is refused by PayPal as well as by
                    # the table above.
                    "invoice_id": ref[:127],
                    "amount": {"currency_code": self.currency,
                               "value": to_value(amount_minor, self.currency)},
                }],
                "payment_source": {"paypal": {
                    "vault_id": vault_id,
                    "stored_credential": {
                        "payment_initiator": "MERCHANT",
                        "usage": "SUBSEQUENT",
                        "usage_pattern": "UNSCHEDULED_POSTPAID",
                    },
                }},
            },
            request_id=request_id("create_order", idem_key),
        )
        self._remember("create_order", idem_key, body)
        return body

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/v2/checkout/orders/{order_id}")

    # ------------------------------------------------------- authorizations --

    def get_authorization(self, authorization_id: str) -> dict:
        return self._request("GET", f"/v2/payments/authorizations/{authorization_id}")

    def capture(self, authorization_id: str, amount_minor: MinorUnits, idem_key: str) -> dict:
        """
        Capture exactly the amount the gate allowed, and say it is the last
        capture, so the authorization cannot be drawn on again.
        """
        cached = self._cached("capture", idem_key)
        if cached is not None:
            log.info("capture short circuited on idempotency key")
            return {**cached, "_replayed": True}

        try:
            body = self._request(
                "POST",
                f"/v2/payments/authorizations/{authorization_id}/capture",
                {"amount": {"currency_code": self.currency,
                            "value": to_value(amount_minor, self.currency)},
                 "final_capture": True},
                request_id=request_id("capture", idem_key),
            )
        except PayPalError as exc:
            # Two cases where the capture may exist although this call did not
            # say so. A 422 on an authorization that is already captured — the
            # issue code for that is not in the spec, so this does not match on
            # one. And a call that never got an answer, which may have landed:
            # rolling that back would release the budget while the money is
            # gone. Either way, read the authorization back rather than guess.
            if exc.status == 422 or exc.status == 0 or exc.status >= 500:
                try:
                    existing = self._existing_capture(authorization_id)
                except PayPalError:
                    raise exc from None
                if existing is not None:
                    self._remember("capture", idem_key, existing)
                    return {**existing, "_replayed": True}
            raise

        self._remember("capture", idem_key, body)
        return body

    def _existing_capture(self, authorization_id: str) -> dict | None:
        auth = self.get_authorization(authorization_id)
        if auth.get("status") not in ("CAPTURED", "PARTIALLY_CAPTURED"):
            return None
        order_id = (auth.get("supplementary_data") or {}).get("related_ids", {}).get("order_id")
        if not order_id:
            return None
        for unit in self.get_order(order_id).get("purchase_units", []):
            for capture in (unit.get("payments") or {}).get("captures", []):
                related = (capture.get("supplementary_data") or {}).get("related_ids", {})
                if related.get("authorization_id") in (None, authorization_id):
                    return capture
        return None

    def void(self, authorization_id: str, idem_key: str) -> dict:
        """Release a hold that will not be captured."""
        return self._request(
            "POST", f"/v2/payments/authorizations/{authorization_id}/void",
            request_id=request_id("void", idem_key),
        )

    # ------------------------------------------------------------- refunds ---

    def refund(self, capture_id: str, amount_minor: MinorUnits, idem_key: str) -> dict:
        """
        Always with an amount. An empty body refunds "whatever is left", and a
        retry of that after a partial refund would be a different refund.
        """
        cached = self._cached("refund", idem_key)
        if cached is not None:
            log.info("refund short circuited on idempotency key")
            return {**cached, "_replayed": True}

        body = self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            {"amount": {"currency_code": self.currency,
                        "value": to_value(amount_minor, self.currency)}},
            request_id=request_id("refund", idem_key),
        )
        self._remember("refund", idem_key, body)
        return body

    # ------------------------------------------------------------ webhooks ---

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """
        Ask PayPal whether a delivery is genuine.

        The event goes back **as received**: the raw bytes are spliced into the
        request, never parsed and re-serialised, because the signature covers
        those bytes and re-serialising is the standard way this check silently
        stops working. Every failure is a rejection — a missing header, no
        webhook id, PayPal unreachable — never an acceptance.
        """
        if not self.credentials.webhook_id:
            log.error("webhook received but PAYPAL_WEBHOOK_ID is not set; rejecting")
            return False
        lowered = {k.lower(): v for k, v in headers.items()}
        fields = {}
        for header, field in WEBHOOK_HEADERS.items():
            if not lowered.get(header):
                log.warning("webhook rejected: no %s header", header)
                return False
            fields[field] = lowered[header]
        try:
            json.loads(body)
        except ValueError:
            return False

        envelope = json.dumps({**fields, "webhook_id": self.credentials.webhook_id})
        raw = envelope[:-1].encode() + b',"webhook_event":' + body + b"}"
        try:
            result = self._request(
                "POST", "/v1/notifications/verify-webhook-signature", raw_content=raw
            )
        except PayPalError:
            log.exception("webhook verification call failed; rejecting")
            return False
        return result.get("verification_status") == "SUCCESS"

    def close(self) -> None:
        self._client.close()
