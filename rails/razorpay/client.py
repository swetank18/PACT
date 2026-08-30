"""
Every Razorpay call in the system goes through this file. Nothing else talks to
Razorpay.

Written against `API_NOTES.md`, which was written against the live docs on
2026-08-30. The two findings that shape this file:

**There is no idempotency header.** `receipt` exists on orders and refunds only,
is capped at 40 ASCII characters, and *rejects* a duplicate rather than
replaying the original response. Capture has no idempotency at all. So we keep
our own table: the key is hashed, we check it before the call, and we store the
result after. Calling twice does not charge twice for any of the three
operations, and we can demo that.

**A refund can return `pending`.** A 200 from the refund endpoint does not mean
the money is back. The saga records the refund id and its status, and only
`processed` closes the compensation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from contracts.money import Paise
from contracts.schemas import utcnow
from core.db import Database

log = logging.getLogger("pact.razorpay")

API_BASE = "https://api.razorpay.com/v1"
RECEIPT_MAX = 40  # API_NOTES: hard limit, ASCII
DEFAULT_TIMEOUT_S = 10.0
RETRIES = 3


class RazorpayError(RuntimeError):
    def __init__(self, status: int, code: str | None, description: str | None, body: Any):
        super().__init__(f"razorpay {status}: {code} {description}")
        self.status = status
        self.code = code
        self.description = description
        self.body = body


@dataclass(frozen=True, slots=True)
class Credentials:
    key_id: str
    key_secret: str
    webhook_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    @property
    def is_test_mode(self) -> bool:
        return self.key_id.startswith("rzp_test_")


def credentials_from_env() -> Credentials:
    return Credentials(
        key_id=os.environ.get("RAZORPAY_KEY_ID", ""),
        key_secret=os.environ.get("RAZORPAY_KEY_SECRET", ""),
        webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
    )


def idempotency_key(order_ref: str, amount_paise: Paise, attempt: int) -> str:
    """`sha256(order_ref | amount_paise | attempt)`, per the shared contract."""
    return hashlib.sha256(
        f"{order_ref}|{amount_paise}|{attempt}".encode()
    ).hexdigest()


def receipt_for(idem_key: str) -> str:
    """
    Razorpay's `receipt` caps at 40 ASCII characters, so the full sha256 hex
    (64) does not fit. Send a prefix; the full key stays in our table, which is
    where the real de-duplication happens anyway.
    """
    return idem_key[:RECEIPT_MAX]


class RazorpayClient:
    def __init__(
        self,
        db: Database,
        credentials: Credentials | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.db = db
        self.credentials = credentials or credentials_from_env()
        self._client = httpx.Client(
            base_url=API_BASE,
            timeout=DEFAULT_TIMEOUT_S,
            transport=transport,
            auth=(self.credentials.key_id, self.credentials.key_secret)
            if self.credentials.configured
            else None,
        )
        if not self.credentials.configured:
            log.warning(
                "No Razorpay credentials. The rail will refuse calls rather than "
                "pretend to settle. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET, "
                "or run the mock_upi rail."
            )
        elif not self.credentials.is_test_mode:
            # A live key in a hackathon repo is a real incident, not a
            # hypothetical. Refuse loudly rather than move real money.
            raise RuntimeError(
                "RAZORPAY_KEY_ID is not a test key. This project is test mode only. "
                "Refusing to start."
            )

    # ---------------------------------------------------------- idempotency --

    def _cached(self, idem_key: str) -> dict | None:
        with self.db.read_tx() as conn:
            row = conn.execute(
                "SELECT result_json FROM idempotency WHERE idem_key = ?", (idem_key,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def _remember(self, idem_key: str, operation: str, result: dict) -> None:
        with self.db.immediate_tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency "
                "(idem_key, operation, result_json, created_at) VALUES (?, ?, ?, ?)",
                (idem_key, operation, json.dumps(result, separators=(",", ":")), utcnow()),
            )

    # ---------------------------------------------------------- transport ----

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.credentials.configured:
            raise RazorpayError(0, "NO_CREDENTIALS", "Razorpay is not configured", {})

        last: Exception | None = None
        for attempt in range(RETRIES):
            try:
                response = self._client.request(method, path, json=payload)
            except httpx.HTTPError as exc:
                # Transport failure. Retry with backoff — the rate limit is
                # UNVERIFIED (see API_NOTES), so an undocumented limit has to
                # degrade into slowness rather than a crash.
                last = exc
                time.sleep(0.25 * (2**attempt))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last = RazorpayError(response.status_code, None, response.text, {})
                time.sleep(0.25 * (2**attempt))
                continue

            body = response.json() if response.content else {}
            if response.status_code >= 400:
                err = body.get("error", {}) if isinstance(body, dict) else {}
                raise RazorpayError(
                    response.status_code,
                    err.get("code"),
                    err.get("description"),
                    body,
                )
            return body

        raise RazorpayError(0, "TRANSPORT", str(last), {})

    # ------------------------------------------------------------- orders ----

    def create_order(
        self, amount_paise: Paise, receipt: str, notes: dict, idem_key: str
    ) -> dict:
        cached = self._cached(idem_key)
        if cached is not None:
            log.info("create_order short circuited on idempotency key")
            return {**cached, "_replayed": True}

        body = self._request(
            "POST",
            "/orders",
            {
                "amount": amount_paise,  # smallest sub-unit; INR paise
                "currency": "INR",
                "receipt": receipt[:RECEIPT_MAX],
                "notes": notes,
            },
        )
        self._remember(idem_key, "create_order", body)
        return body

    # ------------------------------------------------------------ payments ---

    def fetch_payment(self, payment_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}")

    def fetch_order_payments(self, order_id: str) -> list[dict]:
        body = self._request("GET", f"/orders/{order_id}/payments")
        return list(body.get("items", []))

    def capture(self, payment_id: str, amount_paise: Paise, idem_key: str) -> dict:
        """
        Both `amount` and `currency` are mandatory — see API_NOTES. This is the
        call people get wrong from memory.

        Capture has no server-side idempotency, so the table is the only thing
        standing between a retry and a double charge.
        """
        cached = self._cached(idem_key)
        if cached is not None:
            log.info("capture short circuited on idempotency key")
            return {**cached, "_replayed": True}

        try:
            body = self._request(
                "POST",
                f"/payments/{payment_id}/capture",
                {"amount": amount_paise, "currency": "INR"},
            )
        except RazorpayError as exc:
            # Razorpay 400s a second capture rather than returning the original
            # payment. If the payment is in fact already captured, that is a
            # success from our side, not a failure — treat it as one and record
            # it so the retry path converges.
            if exc.status == 400:
                current = self.fetch_payment(payment_id)
                if current.get("status") == "captured":
                    self._remember(idem_key, "capture", current)
                    return {**current, "_replayed": True}
            raise

        self._remember(idem_key, "capture", body)
        return body

    # ------------------------------------------------------------- refunds ---

    def refund(
        self, payment_id: str, amount_paise: Paise, idem_key: str, speed: str = "normal"
    ) -> dict:
        cached = self._cached(idem_key)
        if cached is not None:
            log.info("refund short circuited on idempotency key")
            return {**cached, "_replayed": True}

        body = self._request(
            "POST",
            f"/payments/{payment_id}/refund",
            {
                "amount": amount_paise,  # required for partial refunds
                "speed": speed,
                "receipt": receipt_for(idem_key),
            },
        )
        self._remember(idem_key, "refund", body)
        return body

    def fetch_refund(self, payment_id: str, refund_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}/refunds/{refund_id}")

    # ------------------------------------------------------------ webhooks ---

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        """
        HMAC-SHA256 over the RAW body with the webhook secret.

        The body must not be parsed and re-serialised before this runs — the
        docs say so explicitly, and re-serialising is the standard way this
        check silently stops working. Compared with `compare_digest`, because a
        naive `==` on a signature leaks timing.
        """
        if not self.credentials.webhook_secret:
            log.error("webhook received but RAZORPAY_WEBHOOK_SECRET is not set; rejecting")
            return False
        expected = hmac.new(
            self.credentials.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def close(self) -> None:
        self._client.close()
