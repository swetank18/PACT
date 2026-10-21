"""
PayPal behind the five-method RailAdapter.

The saga, the ledger and the audit trail talk to this shape and never to
`PayPalClient`. That is what makes PayPal a third directory under `rails/`
rather than a change to the gate.

The mapping, which is the only interesting thing here:

    create_intent  ->  create an order against the vaulted wallet, AUTHORIZE,
                       in one call. The authorization id is the `payment_id`
                       the saga captures against.
    capture        ->  capture that authorization, for exactly the amount, as
                       the final capture. A capture that fails voids the hold,
                       so the payer's money is not tied up for 29 days.
    refund         ->  refund the capture, always with an amount.
    status         ->  read the order back for the reconciler, reporting
                       `captured` only for a COMPLETED capture.
    verify_callback -> PayPal's own verification, by postback.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from contracts.money import MinorUnits
from core.rail import RailAdapter, RailIntent, RailResult, RailStatus
from rails.paypal.client import PayPalClient, PayPalError, from_value

log = logging.getLogger("pact.rail.paypal")

#: PayPal's capture statuses, in the vocabulary the saga and the reconciler
#: already speak. The reconciler acts on `captured` and nothing else.
CAPTURE_STATUS = {
    "COMPLETED": "captured",
    "PENDING": "capture_pending",
    "DECLINED": "declined",
    "FAILED": "failed",
    "REFUNDED": "refunded",
    "PARTIALLY_REFUNDED": "partially_refunded",
}

#: Money has left the payer's control. PENDING is included, and API_NOTES says
#: why and what it costs: for the principal's ceiling, counting it as spent is
#: the safe direction.
CAPTURED = frozenset({"COMPLETED", "PENDING"})

#: A refund that is under way or done. PENDING is not money returned, and the
#: status says so; it is still not a failure to retry.
REFUNDING = frozenset({"COMPLETED", "PENDING"})


class PayPalAdapter(RailAdapter):
    name = "paypal"

    def __init__(
        self,
        client: PayPalClient,
        *,
        vault_id_for: Callable[[str], str] | None = None,
    ) -> None:
        self.client = client
        #: Which vaulted wallet pays for a given order. One wallet from the
        #: environment for now. A second payer is a lookup from the order's
        #: mandate to the wallet its holder vaulted when they granted it — the
        #: interface does not change, only this function.
        self.vault_id_for = vault_id_for or (lambda _ref: client.credentials.vault_id)

    def create_intent(self, amount_minor: MinorUnits, ref: str, idem_key: str) -> RailIntent:
        vault_id = self.vault_id_for(ref)
        if not vault_id:
            raise PayPalError(
                0, "NO_VAULT_ID",
                "no vaulted PayPal wallet to charge; set PAYPAL_VAULT_ID", {},
            )

        body = self.client.create_authorized_order(amount_minor, ref, vault_id, idem_key)
        authorization = _first(body, "authorizations")
        status = str((authorization or {}).get("status", ""))

        # COMPLETED on the order says nothing about whether it was paid. The
        # authorization says that, and a denied one ends the purchase here.
        if authorization is None or status not in ("CREATED", "CAPTURED", "PENDING"):
            raise PayPalError(
                402, "AUTHORIZATION_DENIED",
                f"PayPal did not authorise the payment (order {body.get('status')}, "
                f"authorization {status or 'absent'})",
                body,
            )

        amount = authorization.get("amount") or {}
        return RailIntent(
            intent_id=str(body["id"]),
            amount_minor=from_value(amount["value"], amount["currency_code"])
            if amount else amount_minor,
            status="authorized",
            raw={"payment_id": authorization["id"], "order_status": body.get("status"),
                 "replayed": bool(body.get("_replayed"))},
        )

    def capture(self, intent_id: str, amount_minor: MinorUnits, idem_key: str) -> RailResult:
        """`intent_id` here is the authorization id, which `create_intent`
        returned as `payment_id` — the saga captures what that names."""
        try:
            body = self.client.capture(intent_id, amount_minor, idem_key)
        except PayPalError as exc:
            self._void(intent_id, idem_key)
            return _failed(exc)

        status = str(body.get("status", ""))
        ok = status in CAPTURED
        if not ok:
            self._void(intent_id, idem_key)
        return RailResult(
            ok=ok,
            ref=body.get("id"),
            status=CAPTURE_STATUS.get(status, status.lower()),
            replayed=bool(body.get("_replayed")),
            error_code=None if ok else f"CAPTURE_{status or 'UNKNOWN'}",
            error_detail=None if ok else _reason(body),
            raw=body,
        )

    def _void(self, authorization_id: str, idem_key: str) -> None:
        """
        Release the hold after a capture that did not happen. Best effort: a
        void that fails leaves an authorization PayPal expires on its own, and
        the saga has already rolled the order back either way.
        """
        try:
            self.client.void(authorization_id, idem_key)
        except PayPalError as exc:
            log.warning("could not void %s after a failed capture: %s", authorization_id, exc)

    def refund(self, intent_id: str, amount_minor: MinorUnits, idem_key: str) -> RailResult:
        """`intent_id` is the capture id, which the saga stored as the order's
        `rail_payment_id`."""
        try:
            body = self.client.refund(intent_id, amount_minor, idem_key)
        except PayPalError as exc:
            return _failed(exc)

        status = str(body.get("status", ""))
        return RailResult(
            ok=status in REFUNDING,
            ref=body.get("id"),
            status=status.lower(),
            replayed=bool(body.get("_replayed")),
            error_code=None if status in REFUNDING else f"REFUND_{status or 'UNKNOWN'}",
            error_detail=None if status in REFUNDING else _reason(body),
            raw=body,
        )

    def status(self, intent_id: str) -> RailStatus:
        """
        `intent_id` is the PayPal order id. Used by the reconciliation poller,
        which never trusts a webhook as the only path.
        """
        body = self.client.get_order(intent_id)
        payments = _payments(body)
        captures = payments.get("captures", [])
        completed = next((c for c in captures if c.get("status") == "COMPLETED"), None)
        chosen = completed or (captures[0] if captures else None)
        currency = self.client.currency

        if chosen is not None:
            status = CAPTURE_STATUS.get(str(chosen.get("status")), str(chosen.get("status")).lower())
            amount = _minor(chosen.get("amount"), currency)
            payment_id = chosen.get("id")
        else:
            auth = (payments.get("authorizations") or [None])[0]
            status = {"CREATED": "authorized", "DENIED": "declined", "VOIDED": "voided",
                      "PENDING": "authorization_pending"}.get(
                str((auth or {}).get("status")), str(body.get("status", "created")).lower())
            amount = _minor((auth or {}).get("amount"), currency)
            payment_id = None

        refunded = sum(
            _minor(r.get("amount"), currency)
            for r in payments.get("refunds", []) if r.get("status") == "COMPLETED"
        )
        return RailStatus(
            intent_id=intent_id,
            status=status,
            amount_minor=amount,
            amount_refunded_minor=refunded,
            payment_id=payment_id,
            raw=body,
        )

    def verify_callback(self, body: bytes, signature: str) -> bool:
        """
        PayPal signs with five headers, and this protocol carries one string,
        so `signature` is those headers as a JSON object. Anything that is not
        one is a rejection.
        """
        try:
            headers = json.loads(signature or "")
        except ValueError:
            return False
        if not isinstance(headers, dict):
            return False
        return self.client.verify_webhook(body, {str(k): str(v) for k, v in headers.items()})


# ----------------------------------------------------------------- helpers --


def _payments(order: dict) -> dict:
    units = order.get("purchase_units") or [{}]
    return units[0].get("payments") or {}


def _first(order: dict, kind: str) -> dict | None:
    found = _payments(order).get(kind) or []
    return found[0] if found else None


def _minor(money: dict | None, currency: str) -> MinorUnits:
    if not money:
        return 0
    return from_value(money["value"], money.get("currency_code", currency))


def _reason(body: dict) -> str:
    details = body.get("status_details") or {}
    return str(details.get("reason") or body.get("status") or "unknown")


def _failed(exc: PayPalError) -> RailResult:
    detail = exc.description or str(exc)
    if exc.debug_id:
        detail = f"{detail} (debug_id {exc.debug_id})"
    return RailResult(
        ok=False,
        ref=None,
        status="failed",
        error_code=exc.code or str(exc.status),
        error_detail=detail,
        raw=exc.body if isinstance(exc.body, dict) else {},
    )
